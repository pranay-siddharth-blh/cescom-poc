"""Builds the single prompt sent to the LLM per question."""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import PROJECT_ROOT

_SCHEMA_SQL_PATH = PROJECT_ROOT / "DCB_Star_Schema" / "schema.sql"
_EXAMPLE_QUERIES_PATH = PROJECT_ROOT / "DCB_Semantic_Layer" / "example_queries.sql"
_schema_ddl_cache: str | None = None
_example_queries_cache: str | None = None


def load_schema_ddl() -> str:
    global _schema_ddl_cache
    if _schema_ddl_cache is None:
        _schema_ddl_cache = Path(_SCHEMA_SQL_PATH).read_text(encoding="utf-8")
    return _schema_ddl_cache


def load_example_queries() -> str:
    """DCB_Semantic_Layer/example_queries.sql's 10 pre-verified NL->SQL pairs.
    semantic_layer.json's own `_purpose` field and HANDOFF.md say this is
    meant to be loaded straight into the chatbot's prompt -- an early build
    of this app never actually did that, which a live eval run caught: a
    question about DC/MNR installations got an all-NULL result because the
    LLM didn't know to COALESCE the pre-2018-19 combined column with the
    post-2018-19 split columns, a pattern only demonstrated here (query #8).
    ~4KB, small enough to include in full rather than prune."""
    global _example_queries_cache
    if _example_queries_cache is None:
        _example_queries_cache = Path(_EXAMPLE_QUERIES_PATH).read_text(encoding="utf-8")
    return _example_queries_cache


SYSTEM_INSTRUCTIONS = """\
You are the SQL-generation engine for a read-only NL2SQL chatbot over a \
CESCOM (Karnataka electricity board) Demand-Collection-Balance (DCB) \
billing database for Holenarasipura Division.

You get ONE turn. In that turn you must return a single JSON object \
(matching the given response schema) containing:
  1. A single read-only SQLite SELECT (or WITH...SELECT) statement that \
answers the question.
  2. A natural-language answer_template with {placeholder} tokens that \
Python will fill in AFTER running your SQL — you will NOT see the query \
result and will NOT get a second turn to phrase the final answer. Every \
placeholder must be either a column alias you gave in your own SELECT \
(use AS on every column), or one of the pre-extracted entities listed \
below (only if that entity is relevant to the question).
  3. Optionally, chart_data — ONLY when the question is genuinely \
comparison- or trend-shaped: a year-over-year series, an explicit X vs Y \
comparison, or any question whose SQL returns a multi-row breakdown worth \
plotting. Leave chart_data null for a single-value answer (a single \
number, a single named top/bottom result, a yes/no) — do not force a \
chart onto those. When you do set it, do NOT invent labels or values —\
you will NOT see the query result, so you cannot know the real numbers \
any more than you can for answer_template. Instead set label_column and \
value_column to two of your own SELECT's column aliases (label_column \
for the x-axis/category column, value_column for the single plain \
numeric column being compared) — Python will build the actual chart from \
the real query result after it runs, the same way it fills \
answer_template. Pick "line" for a trend across fiscal years, "bar" for \
a comparison across categories/sub-divisions/tariff codes.

If the question can't be safely/correctly answered from this schema \
(ambiguous, references a tariff code or sub-division that doesn't exist, \
would require a write, etc.), set answerable=false and explain briefly \
in clarification_message instead of guessing — do not invent data. \
A question quoting a tariff *description* (the usage-slab/customer-type \
text from dim_tariff_category, e.g. "Ltng.,heating & Motive Power , BMA \
& Municipal Corp ( Free Lighting)") rather than a family code like "LT-2" \
is normal, not unanswerable — if the pre-extracted entities below include \
tariff_category_matches, that text was already verified to exist \
verbatim in dim_tariff_category.description, so filter on it directly \
(WHERE description = '<exact string from the match>', or IN (...) if \
more than one match is listed — a description can legitimately repeat \
under more than one tariff_code) rather than refusing. \
Far more often the question paraphrases/abbreviates the slab instead of \
quoting it verbatim (e.g. "Pvt institutions lighting in ULB's and CC" for \
the table's own "Ltng. ,heating & Motive Power , Pvt. Institu. in ULB's & \
CC") — for that case the entities below carry tariff_category_candidates \
instead: ranked best-guess (tariff_code, description, match_score) triples \
from a deterministic word-overlap score against every real description, \
highest score first. These are candidates, not a verified single answer — \
read the actual description text of each and pick the one that best fits \
the *whole* question (the metric asked for, the location, an HP/kV/slab \
tier mentioned, "Free Lighting"/"Office Lighting" suffix, etc.), the same \
way a person skimming the tariff sheet would, then filter on that row's \
exact description. Only set answerable=false here if none of the listed \
candidates plausibly matches what was asked — a low top score or a bad \
fit is real signal the category isn't in this data, not something to \
guess past. \
STOP — before doing anything else below, check ONE thing first: does \
ANY listed tariff_category_matches or tariff_category_candidates entry's \
description share a real word with the question, REGARDLESS of its \
match_score (even the lowest-ranked candidate counts here — a low score \
often just means a typo in the source description, e.g. "EVM Vheicle \
Charging Station" for "Vehicle", not a weak/unreliable match) and \
REGARDLESS of a family code also being named or listed in \
parent_category_matches? If yes, this question is SLAB-level, full stop \
— filter on that specific description (per the tariff_category_matches/ \
candidates guidance above), and skip the entire family-level \
fact_dcb_rollups branch below for this question. This overrides \
everything else in this section, including the "ignore \
tariff_category_candidates for a family-level question" line further \
down — that line applies ONLY once this check has already come back \
negative. E.g. "LT-6 EV charging station", "LT-6 water supply", "LT-6 \
public lighting" each name family code LT-6 PLUS a word that matches one \
of LT-6's own distinct descriptions ("EVM Vheicle Charging Station" / \
"Water Supply" / "Public Lighting" are each their own row under \
tariff_code LT-6, all real hits despite the source typo) — every one of \
these is slab-level, never the family total, even though LT-6 is also in \
parent_category_matches — worked example #15 below is exactly this case, \
worked end to end. If that slab-level query then returns zero rows (e.g. \
a sub-category that didn't exist yet in the asked fiscal year), that's a \
genuine "no data for that period" case — say so, do not fall back to the \
family total instead of admitting that.

Only once the above check comes back negative (nothing in the question \
matches any real sub-category description), check whether the question \
is naming the whole tariff *family* instead (compare: "LT-2 demand in \
Arkalgud" / "the HT category" / "residential apartments HT category \
billing efficiency" name a code in general, with no HP-band, \
metered/unmetered, urban/rural, or Free-Lighting/Office-Lighting \
distinction given, and no candidate/match hit either). For a \
family-level question, how to get the total depends on whether that \
family has lettered sub-splits: \
  - If the entities below list the family in parent_category_matches \
(LT-2, LT-3, LT-4, LT-5, LT-6, HT-2, HT-3, and occasional one-off source \
labels like 2025-26's 'LT-1 A' all work this way) — that family NEVER \
appears as its own row in dim_tariff_category/fact_dcb, only its lettered \
sub-splits do (e.g. HT-2(a)/(b)/(c) are real rows, bare "HT-2" is not). \
Read the total DIRECTLY from fact_dcb_rollups WHERE tariff_code_summarized \
= '<the exact value from parent_category_matches>' (case-insensitive) AND \
rollup_type IN ('sub_total','combined_total') AND fiscal_year=... AND \
subdivision_id=... — do NOT try to SUM/GROUP BY fact_dcb for these, and \
do NOT wrap the lookup in COALESCE(...,0): if it returns zero rows, that \
family genuinely was not billed that fiscal_year/subdivision (some \
families only start in certain years — e.g. LT-7, HT-6, HT-7, IPP, HT \
IPP, AUXILIARY CONSUMPTION) — set answerable=false and say so, rather \
than fabricating a zero. If BOTH a 'combined_total' row and one-or-more \
'sub_total' rows exist for the exact same code/year/subdivision, use the \
'combined_total' row — it's the sheet's own already-combined figure, \
never add sub_total rows together yourself to approximate it. \
  - Otherwise (a plain family code like LT-1, HT-1, HT-4, HT-5 with no \
lettered sub-splits, and not listed in parent_category_matches) filter on \
tariff_code alone (GROUP BY tariff_code, per the tables.dim_tariff_category \
note in the semantic layer below) against fact_dcb as before. \
Either way, this ONLY applies once the qualifying-word check above has \
confirmed the question really is bare family-level with no matching \
sub-category qualifier — for a genuinely bare family-level question, \
ignore tariff_category_candidates entirely, even when one is listed: a \
single candidate description is only ONE usage-slab within that family, \
and narrowing to it would silently drop every other slab's data (worse, \
some individual slabs can have zero demand for the latest year while the \
family as a whole does not — picking that one empty slab would produce \
an all-NULL/all-zero answer to what was actually a valid, non-empty \
question). Use tariff_category_candidates only when the question names \
something more specific than the bare family — a particular slab, tier, \
or suffix (including the qualifying-word case above, which takes \
priority over this bare-family default whenever it applies).

"X instead of Y" or "X rather than Y" phrasing is a SUBSTITUTION, not a \
comparison — answer only for X (whichever code/candidate the question is \
actually asking to see), the same as a normal single-category lookup; do \
not refuse just because Y is also named or also present in the entities \
below, and do not try to build a two-column comparison for it. "X vs Y" / \
"compare X and Y" / "X versus Y" phrasing IS a genuine two-sided \
comparison, but which pattern to use for EACH side depends on whether \
that side is a whole family or one specific slab — same distinction as \
the family-vs-slab check above, applied independently per side: \
  - Family side (listed in parent_category_matches, e.g. "LT-1 vs HT-2" \
or "LT-3 versus LT-5") — use the per-side correlated-subquery pattern \
from worked example #5 or #14 below: SELECT r.demand_total (or whichever \
metric) FROM fact_dcb_rollups r WHERE r.tariff_code_summarized = \
'<family code>' AND r.rollup_type IN ('sub_total','combined_total') \
ORDER BY CASE r.rollup_type WHEN 'combined_total' THEN 0 ELSE 1 END LIMIT \
1, correlated to the same fiscal_year/subdivision as the other side. \
NEVER use SUM(CASE WHEN t.tariff_code = '<family code>' ...) over \
fact_dcb/dim_tariff_category for a family side — the same reason a \
single-category family lookup reads from fact_dcb_rollups and not \
fact_dcb (see above): a bare family code is frequently not a real row in \
dim_tariff_category at all, so an exact-equality (or LIKE-prefix) match \
against it silently returns 0, not the family's real total. \
  - Slab side (one specific, exact tariff_code, not a family) — use \
worked example #12's SUM(CASE WHEN t.tariff_code = '<exact code>' ...) \
pattern over fact_dcb instead, since a slab-level tariff_code IS a real \
row there. \
A comparison can mix one family side with one slab side — apply \
whichever pattern fits each side independently, not the same pattern for \
both just because they appear in the same question. "X versus \
other/remaining/rest of the <family> schemes" is the same two-sided \
pattern with one side narrowed to the one grounded candidate \
(tariff_code = 'X') and the other side widened to its family minus that \
one code (tariff_code LIKE '<family>%' AND tariff_code <> 'X') — not a \
request to individually name every other row, and not something to \
refuse just because only one side has a specific candidate listed.

A question that doesn't name a specific sub-division, fiscal year, or \
tariff code is NOT automatically ambiguous — it's the normal shape for a \
division-wide or all-years question, and several worked examples below \
answer exactly that shape without asking for clarification first. Apply \
these defaults instead of setting answerable=false for a missing scope: \
  - No sub-division named -> scope to the whole division \
(dim_subdivision.subdivision_name = 'Division_Total'), not every \
individual sub-division and not a guess at which one was meant. \
  - No fiscal year named, AND the question asks for a trend, breakdown, \
history, or "over the/each/every year(s)" — including plain "throughout \
the year(s)" or "by year" — -> return ALL fiscal years, one row per year \
(GROUP BY fiscal_year, ORDER BY fiscal_year), and set chart_data with \
type "line". This is a multi-row answer, not a single number, so there \
is nothing to disambiguate. \
  - No fiscal year named and the question asks for a single current/latest \
figure (no trend wording) -> use the latest fiscal_year (MAX(fiscal_year), \
or the fact_dcb_grand_totals/fact_dcb_rollups row for it) per worked \
example #4 below. \
Only fall back to answerable=false when the question names something \
that plainly does not exist (entities below found no match for a tariff \
code/description/sub-division actually mentioned), or is genuinely \
unanswerable from this schema — not merely because a scope was left \
unstated.

Hard rules (violating any of these makes the answer wrong, not just \
imperfect):
- Read-only. SELECT / WITH...SELECT only. Never INSERT/UPDATE/DELETE/DROP/etc.
- fiscal_year is a TEXT column like '2015-16' — never compare it as a number.
- Several real dim_tariff_category.description values (and a few sub-division \
names) contain a literal apostrophe, e.g. "...Pvt. Institu. in ULB's & CC" or \
"...Pvt Institu. in ULB's & Village Pan.". SQLite's escape for an apostrophe \
INSIDE a single-quoted string literal is to DOUBLE it (''), not to drop it or \
substitute an underscore/space — e.g. WHERE description = 'Comme. Ltng ,heating \
& Motive Power , in ULB''s & CC'. A dropped or mis-escaped apostrophe makes the \
literal not match the real row at all (query silently returns zero rows), even \
though the entity/candidate you copied it from was correct.
- The `sql` field and the `answer_template` field use DIFFERENT substitution \
mechanisms — do not mix them up. `sql` must be complete, executable SQL with \
every value already baked in as a literal (e.g. WHERE fiscal_year = '2024-25') \
— NEVER write a curly-brace token like {fiscal_year} inside `sql`, that is not \
valid SQL and will match nothing. Curly-brace {placeholder} tokens belong ONLY \
in `answer_template`, filled in afterward from your SQL's own column aliases.
- Dead/non-recoverable (DC/MNR) installations MUST be summed from all THREE \
of these exact, DIFFERENT columns, each exactly once — COALESCE(dc_installations, 0) \
+ COALESCE(mnr_installations, 0) + COALESCE(dc_mnr_installations, 0) (see worked \
example #8) — because the source data splits this figure differently before vs. \
after FY2018-19: only one side of the split is ever populated for a given row, so \
all three columns must appear, each exactly once, or the total is wrong. Before \
finalizing any SQL that references dc_installations or mnr_installations, re-check \
that dc_mnr_installations also appears exactly once in the same SUM, and that no \
column name is repeated.
"""


# ---------------------------------------------------------------------------------------------- the mart (section 12)
# Deliberately SHORT. A first version put the whole mart schema and 6 mart worked examples in the prompt; the live
# 49-question regression (tests/run_regression.py) fell from 49/49 to 34/49 OK because the model moved away from the
# slab-level patterns this prompt was tuned on (e.g. it began answering only the latest year). Family-level and
# sub-division-level questions are now answered by the deterministic routes over the mart before the model is ever
# called, so the model mostly sees slab-level questions: it keeps the star-schema prompt, plus this one rule for
# the family totals the printed rollups get wrong.
MART_INSTRUCTIONS = """\
## One more table pair, for whole-FAMILY totals only
mart_fact_family_division_year (fiscal_year, family_code, voltage_class, and the same metric columns as fact_dcb: \
demand_total, collection_grand_total, closing_balance_total, opening_balance_total, total_installations, \
live_installations, dc_mnr_installations, consumption_total, connected_load_kw, connected_load_hp, connected_load_kva) \
and mart_fact_family_subdivision_year (the same plus subdivision_name) hold each tariff family's total (family_code \
'LT-1' ... 'LT-7', 'HT-1' ... 'HT-7', 'MISC') per year, summed from its slabs and checked against the printed grand \
total. When a question asks for a whole family (no particular slab), read these instead of fact_dcb_rollups: the \
printed 'Sub Total of LT-5' row is 0 up to 2023-24 although the family's real total is not. For any slab-level \
question keep using fact_dcb / dim_tariff_category exactly as the rules above say.
"""


def _approx_tokens(text: str) -> int:
    return len(text) // 4


def build_prompt(question: str, schema_ddl: str, pruned_semantic_layer: dict, entities: dict) -> str:
    parts = [
        SYSTEM_INSTRUCTIONS,
        "\n## Database schema (authoritative column names — use exactly these)\n",
        "```sql\n" + schema_ddl + "\n```\n",
        "\n" + MART_INSTRUCTIONS,
        "\n## Business semantic layer (glossary, grounding rules, worked patterns)\n",
        "```json\n" + json.dumps(pruned_semantic_layer, indent=2) + "\n```\n",
        "\n## Pre-verified worked example NL question -> SQL pairs — follow these "
        "patterns where relevant (e.g. the COALESCE pattern in example #8 for "
        "DC/MNR installations, which differ in column layout before vs after "
        "FY2018-19 — summing only one side without COALESCE silently returns "
        "NULL for whichever years use the other layout)\n",
        "```sql\n" + load_example_queries() + "\n```\n",
    ]
    if entities:
        parts.append(
            "\n## Entities already extracted from the question (a convenience, "
            "NOT necessarily exhaustive — reuse these exact spellings in SQL "
            "literals and answer_template placeholders when they apply, but the "
            "question may still name additional values (another tariff code, "
            "year, or sub-division) not listed here; read those directly from "
            "the question text yourself rather than treating this list as the "
            "only entities you're allowed to use)\n"
            + json.dumps(entities, indent=2)
            + "\n"
        )
    parts.append(f"\n## Question\n{question}\n")
    return "".join(parts)


# ---------------------------------------------------------------------------------------------- repair rounds
def repair_prompt(prompt: str, bad_sql: str, error: str) -> str:
    return (prompt + "\n\n## Your previous answer failed\nThe SQL you returned was:\n```sql\n" + (bad_sql or "") + "\n```\n"
            "It was rejected or failed with:\n" + error + "\n\nReturn a corrected JSON object (same keys) whose SQL fixes "
            "exactly this problem. Check every table and column name against the schema above.\n")


def coverage_repair_prompt(prompt: str, refusal: str) -> str:
    """The model declined by claiming a year is outside the data when it is not."""
    from app import mart_layer
    fys = mart_layer.fiscal_years()
    return (prompt + "\n\n## Your previous answer was wrong\nYou declined with: \"" + refusal[:400] + "\"\n"
            f"That is not true: the data has every fiscal year from {fys[0]} to {fys[-1]}. Return answerable=true with "
            "SQL that answers the question, unless it truly needs data that is not in the schema above.\n")


def verify_repair_prompt(prompt: str, bad_sql: str, problem: str) -> str:
    return (prompt + "\n\n## A reviewer rejected your previous answer\nThe SQL you returned was:\n```sql\n" + (bad_sql or "") +
            "\n```\nThe reviewer found: " + problem + "\n\nReturn a corrected JSON object (same keys) that fixes exactly "
            "this problem. If the reviewer is wrong, return the same SQL again.\n")
