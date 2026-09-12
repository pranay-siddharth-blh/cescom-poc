"""Builds the single prompt sent to the LLM per question."""
from __future__ import annotations

import json
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
in clarification_message instead of guessing — do not invent data.

Hard rules (violating any of these makes the answer wrong, not just \
imperfect):
- Read-only. SELECT / WITH...SELECT only. Never INSERT/UPDATE/DELETE/DROP/etc.
- fiscal_year is a TEXT column like '2015-16' — never compare it as a number.
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


def build_prompt(question: str, schema_ddl: str, pruned_semantic_layer: dict, entities: dict) -> str:
    parts = [
        SYSTEM_INSTRUCTIONS,
        "\n## Database schema (authoritative column names — use exactly these)\n",
        "```sql\n" + schema_ddl + "\n```\n",
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
