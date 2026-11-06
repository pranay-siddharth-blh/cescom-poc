"""
Loads DCB_Semantic_Layer/semantic_layer.json (read-only, never written to)
and prunes it down to the subset relevant to one question (design point
#2) before it goes into the LLM prompt.

Why prune at all when semantic_layer.json is only ~5KB today: the pruning
step is what keeps the prompt lean as the semantic layer grows (more
tariff codes, more worked examples, more tables) — and it's what keeps
this a deterministic, sub-millisecond dict-filtering pass rather than a
second model call, so it can never become its own latency source. The
router below is plain keyword/substring matching against entity lists
loaded once at startup — no LLM, no embeddings.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config import SEMANTIC_LAYER_PATH

_FISCAL_YEAR_RE = re.compile(r"\b(20\d{2})\s*-\s*(\d{2})\b")
_FISCAL_YEAR_SHORT_RE = re.compile(r"\b(20\d{2})\b")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# Self-referential/meta questions ("who are you") are not a schema gap --
# routing them through the LLM produced the out-of-scope refusal wording
# ("...I cannot answer this from the provided schema"), which is correct
# for genuinely unrelated questions (weather, sports) but misleading here.
# Plain keyword/regex, matching this module's own no-LLM/no-embeddings
# router design -- checked before extract_entities/prune_semantic_layer
# even run, so an identity question never reaches the LLM or DB at all.
_IDENTITY_INTENT_RE = re.compile(
    r"\b("
    r"who\s+are\s+you"
    r"|what\s+are\s+you"
    r"|what\s*(?:'s|s|\s+is)\s+your\s+name"
    r"|what\s+can\s+you\s+do"
    r"|what\s+(?:do|can)\s+you\s+(?:know|help\s+with|help\s+me\s+with)"
    r"|who\s+(?:made|created|built|developed)\s+you"
    r"|are\s+you\s+(?:a\s+|an\s+)?(?:bot|robot|ai|chatbot|human)\b"
    r"|tell\s+me\s+about\s+yourself"
    r")\b",
    re.IGNORECASE,
)


def is_identity_question(question: str) -> bool:
    """Deterministic (no LLM, no embeddings) check for self-referential/
    meta questions -- "who are you", "what can you do", "who made you",
    etc. -- so app.pipeline can short-circuit them before the LLM/schema
    pipeline ever runs."""
    return bool(_IDENTITY_INTENT_RE.search(question))


# A bare "what is LT-1" / "define HT-2" / "what does DCB mean" is asking
# for a glossary lookup, not a data query -- the correct answer already
# exists, unused, in DCB_Semantic_Layer/semantic_layer.json's `glossary`/
# `tariff_code_glossary` dicts. Routing this through the LLM produced
# SQL like `SELECT DISTINCT description FROM dim_tariff_category WHERE
# tariff_code LIKE 'LT-1%'`, dumping all of a family's raw sub-slab rows
# instead of the one-line definition a human would actually want. Same
# short-circuit pattern as is_identity_question above -- anchored to the
# WHOLE question (start to end) so this only matches a bare definitional
# ask, never a real data question that happens to start with "what is"
# but goes on to name a metric/year/comparison/sub-division (those fall
# through to the normal pipeline unchanged, since their captured
# "subject" text won't equal any real glossary key -- see below).
_DEFINITIONAL_LEAD_RE = re.compile(
    r"^\s*(?:what\s*(?:'s|s|\s+is)|define|explain|tell\s+me\s+about)\s+"
    r"(?:the\s+|an?\s+)?(?P<subject>.+?)\s*\??\s*$",
    re.IGNORECASE,
)
# "what does DCB mean" / "what does DCB stand for" -- the latter added with the KPCL-port stress bank, where it
# fell through to the model and was declined.
_DEFINITIONAL_MEAN_RE = re.compile(
    r"^\s*what\s+does\s+(?:the\s+|an?\s+)?(?P<subject>.+?)\s+(?:mean|stand\s+for)\s*\??\s*$",
    re.IGNORECASE,
)
# Stripped off the END of a captured subject only -- "LT-1 category" /
# "HT-2 tariff code" / "DCB code" all still mean the bare term "LT-1" /
# "HT-2" / "DCB" for lookup purposes.
_DEFINITIONAL_TRAILING_QUALIFIER_RE = re.compile(
    r"\s+(?:tariff\s+)?(?:category|code)\s*$", re.IGNORECASE
)


def _definitional_subject_key(subject: str, table: dict[str, str]) -> str | None:
    """Match `subject` (already extracted + qualifier-stripped) against a
    glossary dict's keys, case/whitespace-normalized. Some keys are
    compound ("Sub Total / LT-TOTAL / HT-TOTAL / GRAND TOTAL") -- split on
    "/" the same way prune_semantic_layer's own glossary matching already
    does, so "what does Sub Total mean" resolves to that entry without
    requiring the whole compound key verbatim. Returns the real dict key
    (for the caller to look the definition text up with), never a guess:
    exact match only, after normalization -- a subject that doesn't
    exactly equal a real key (or one of its "/"-parts) returns None."""
    target = re.sub(r"\s+", "", subject.strip().lower())
    if not target:
        return None
    for key in table:
        for part in (key, *key.split("/")):
            if re.sub(r"\s+", "", part.strip().lower()) == target:
                return key
    return None


def is_definitional_question(question: str) -> str | None:
    """Deterministic (no LLM, no embeddings) check for a bare "what is
    <code>" / "define <code>" / "what does <term> mean" glossary lookup.
    Returns the matched glossary key (a tariff_code_glossary key like
    "LT-1", or a plain glossary key like "DCB") if the question is purely
    asking what something means, else None -- including when the question
    matches this definitional SHAPE but names something not in either
    glossary (e.g. a specific sub-slab code like "LT-2(b)(ii)"), so
    app.pipeline falls through to the normal LLM pipeline rather than this
    function guessing an answer for a term it doesn't actually have."""
    m = _DEFINITIONAL_LEAD_RE.match(question) or _DEFINITIONAL_MEAN_RE.match(question)
    if not m:
        return None
    subject = _DEFINITIONAL_TRAILING_QUALIFIER_RE.sub("", m.group("subject").strip()).strip()
    if not subject:
        return None
    full = load_semantic_layer()
    return (
        _definitional_subject_key(subject, full["tariff_code_glossary"])
        or _definitional_subject_key(subject, full["glossary"])
    )


def _normalize(text: str) -> str:
    """Collapse case/punctuation/whitespace differences so a tariff
    description like 'Ltng.,heating & Motive Power , BMA & Municipal Corp
    ( Free Lighting)' still matches a question that types the same words
    with slightly different spacing around the commas/parens -- the source
    sheet itself isn't consistent about that spacing row-to-row."""
    return _NON_ALNUM_RE.sub(" ", text.lower()).strip()


# Small, closed set of abbreviations this specific billing-sheet vocabulary
# uses -- 'Ltng.'/'Comme.'/'Pvt.'/'Institu.' etc. -- plus a couple of the
# source sheet's OWN typos in description text ('Univercity', 'Chartable',
# 'Less then one week', 'permanent connection Basses'), mapped to a shared
# canonical token so a fully-spelled-out question still overlaps with the
# abbreviated/misspelled DB text. Not test-specific: every entry here is a
# real spelling that appears in dim_tariff_category.description itself.
_SYNONYMS = {
    "ltng": "lighting",
    "comme": "commercial", "commerce": "commercial",
    "pvt": "private",
    "govt": "government",
    "institu": "institution", "institutions": "institution", "institutn": "institution",
    "univercity": "university",
    "chartable": "charitable",
    "then": "than",
    "basses": "basis",
    "pan": "panchayat", "panchayats": "panchayat",
}
_STOPWORDS = {"in", "the", "of", "and", "to", "for", "a", "an", "on", "at", "or", "with"}
# 106 distinct descriptions in dim_tariff_category; "heating"/"motive"/
# "power" each appear in 17-19 of them (mostly the whole LT-1/LT-2/LT-3/
# LT-5 "Ltng ,heating & Motive Power , <customer type>" family) and
# "lighting"/"ltng" in another 8-11 -- boilerplate shared across exactly
# the confusable sibling rows this matcher most needs to tell apart (BMA
# vs ULB vs Pvt-institution vs Village-Panchayat vs Commercial). Counting
# them padded every sibling's score roughly equally, diluting the ratio
# just enough that a question naturally omitting them (nobody says
# "lighting heating motive power" when they mean "private institutions in
# village panchayats") fell under threshold -- found live: entity
# extraction returned no candidates at all for that exact question, and
# the LLM hallucinated a plausible-but-wrong description from a sibling
# row's pattern instead of admitting it didn't know. Excluded here from
# scoring only (never from the exact-match check in extract_entities,
# which still requires the real, complete description text).
_LOW_SIGNAL_WORDS = {"lighting", "heating", "motive", "power"}


def _content_tokens(text: str) -> set[str]:
    """Tokenize for fuzzy matching: normalize, map known abbreviations/
    typos to a shared canonical spelling, drop stopwords and low-signal
    boilerplate that would otherwise pad the overlap score without
    actually helping tell two descriptions apart."""
    words = _normalize(text).split()
    mapped = (_SYNONYMS.get(w, w) for w in words)
    return {w for w in mapped if w and w not in _STOPWORDS and w not in _LOW_SIGNAL_WORDS}


@dataclass
class RouterIndex:
    """Entity lists pulled from the live DB once at startup, used to spot
    which parts of the semantic layer a question actually touches."""

    subdivision_names: list[str] = field(default_factory=list)
    fiscal_years: list[str] = field(default_factory=list)
    # (tariff_code, description) pairs from dim_tariff_category -- grounds
    # slab-level questions (e.g. "Ltng.,heating & Motive Power , BMA &
    # Municipal Corp ( Free Lighting)") the same way subdivision_names/
    # fiscal_years ground subdivision/year questions. Without this the LLM
    # has no way to know a free-text tariff description is real (only
    # schema DDL, not row values, is in its prompt) and was observed
    # wrongly refusing ("does not match any recognized tariff code") for
    # descriptions that exist verbatim in the table.
    tariff_categories: list[tuple[str, str]] = field(default_factory=list)
    # Distinct tariff_code_summarized values from fact_dcb_rollups
    # (rollup_type IN ('sub_total','combined_total')) -- these are the
    # FAMILY-level codes (LT-2, LT-3, LT-4, LT-5, LT-6, HT-2, HT-3, plus
    # one-off source labels like '2025-26's 'LT-1 A') that NEVER appear as
    # their own row in dim_tariff_category/fact_dcb -- only their lettered
    # sub-splits do. Grounds a question naming the bare family so the LLM
    # is told to read fact_dcb_rollups directly instead of (wrongly)
    # looking for/summing a fact_dcb row that structurally can't exist.
    rollup_category_codes: list[str] = field(default_factory=list)


_full_layer: dict | None = None


def load_semantic_layer() -> dict:
    """The locked semantic layer, read-only. Its tariff-code glossary predates the 2024-25 tariff restructuring (LT-1
    "usage-slab tiered", LT-2 "municipal premises"), so in memory -- never on disk -- it is replaced by the CESC tariff
    booklet FY2025-26 to FY2027-28 (app/tariff_booklet.py), and the booklet's terms join the glossary: the prompt, the
    glossary route and the About page all read this one copy."""
    global _full_layer
    if _full_layer is None:
        from app import tariff_booklet   # local import: tariff_booklet -> db -> config only
        layer = json.loads(Path(SEMANTIC_LAYER_PATH).read_text(encoding="utf-8"))
        layer["tariff_code_glossary"] = tariff_booklet.code_glossary(layer.get("tariff_code_glossary"))
        layer["glossary"] = {**layer.get("glossary", {}), **tariff_booklet.GLOSSARY_TERMS}
        _full_layer = layer
    return _full_layer


async def build_router_index(db) -> RouterIndex:
    result = await db.run(
        "SELECT subdivision_name FROM dim_subdivision", max_rows=100, timeout_seconds=5
    )
    subdivisions = [row[0] for row in result.rows]
    result = await db.run(
        "SELECT fiscal_year FROM dim_fiscal_year", max_rows=100, timeout_seconds=5
    )
    years = [row[0] for row in result.rows]
    result = await db.run(
        "SELECT DISTINCT tariff_code, description FROM dim_tariff_category",
        max_rows=500,
        timeout_seconds=5,
    )
    tariff_categories = [(row[0], row[1]) for row in result.rows if row[1]]
    result = await db.run(
        "SELECT DISTINCT tariff_code_summarized FROM fact_dcb_rollups "
        "WHERE rollup_type IN ('sub_total', 'combined_total') AND tariff_code_summarized IS NOT NULL",
        max_rows=200,
        timeout_seconds=5,
    )
    rollup_category_codes = [row[0] for row in result.rows]
    return RouterIndex(
        subdivision_names=subdivisions,
        fiscal_years=years,
        tariff_categories=tariff_categories,
        rollup_category_codes=rollup_category_codes,
    )


def extract_entities(question: str, index: RouterIndex) -> dict:
    """Deterministic extraction of the entities the prompt-filler needs
    later to fill answer-template placeholders like {fiscal_year} or
    {subdivision_name} without a second LLM call."""
    q_lower = question.lower()
    entities: dict[str, str] = {}

    for name in index.subdivision_names:
        if name.lower().replace("_", " ") in q_lower or name.lower() in q_lower:
            entities["subdivision_name"] = name
            break

    m = _FISCAL_YEAR_RE.search(question)
    if m:
        candidate = f"{m.group(1)}-{m.group(2)}"
        if candidate in index.fiscal_years:
            entities["fiscal_year"] = candidate
    if "fiscal_year" not in entities:
        for year in index.fiscal_years:
            if year in question:
                entities["fiscal_year"] = year
                break

    # Match against every REAL tariff_code in the table, not just the 14
    # generic family codes (LT-1..LT-7/HT-1..HT-7) -- a plain substring
    # check against only those 14 silently truncates a question that names
    # a full sub-code, since e.g. "LT-3" IS a substring of "LT-3(i)". Found
    # live: "LT-3(i) collection efficiency in Arkalgud" produced
    # entities={"tariff_code": "LT-3"} and the LLM trusted it -- but there
    # is no bare "LT-3" row, only "LT-3(i)"/"LT-3(ii)"/etc., so the query
    # matched zero rows. bare family codes like "LT-1"/"HT-3" are
    # themselves real, separate rows in this table (not every family has
    # sub-codes), so they're included in the same full-code scan, not
    # special-cased.
    all_codes = {code for code, _ in index.tariff_categories}
    q_compact = re.sub(r"\s+", "", q_lower)

    def _code_in_question(code: str) -> bool:
        code_lower = code.lower()
        # The compact (whitespace-stripped) check catches DB spacing
        # quirks like the row literally coded 'HT - 3b' matching a
        # question that reasonably types 'HT-3b'.
        return code_lower in q_lower or re.sub(r"\s+", "", code_lower) in q_compact

    raw_code_matches = [code for code in all_codes if _code_in_question(code)]
    # Keep only the most specific match per family: drop a matched code
    # that is a plain prefix of another matched code also found in this
    # question (e.g. "LT-3" dropped in favor of "LT-3(i)" when the
    # question names the full sub-code) -- mirrors the subsumption rule
    # used for tariff_category_matches below.
    tariff_codes_found = [
        code
        for code in raw_code_matches
        if not any(other != code and other.lower().startswith(code.lower()) for other in raw_code_matches)
    ]
    # A question can legitimately name more than one tariff code (e.g. "LT-1
    # vs HT-2") -- found via a real crash/misfire during the first live-LLM
    # eval run (see CLAUDE.md): extracting only the first match and telling
    # the LLM to treat it as authoritative caused the model to wrongly
    # refuse to answer a valid comparison question. Single match keeps the
    # simple `tariff_code` key other prompts/tests expect; 2+ matches go in
    # `tariff_codes` instead.
    if len(tariff_codes_found) == 1:
        entities["tariff_code"] = tariff_codes_found[0]
    elif len(tariff_codes_found) > 1:
        entities["tariff_codes"] = tariff_codes_found

    # Family-level "parent" categories (LT-2, LT-3, LT-4, LT-5, LT-6, HT-2,
    # HT-3, and one-off source labels like 2025-26's 'LT-1 A') never appear
    # as their own row in dim_tariff_category/fact_dcb -- only their
    # lettered sub-splits do (e.g. HT-2(a)/(b)/(c)) -- so the tariff_code
    # matching above (which only scans dim_tariff_category) can never find
    # them; "HT-2 demand" used to silently return 0 for exactly this
    # reason (no fact_dcb row ever matches tariff_code='HT-2', so a naive
    # COALESCE(SUM(...),0) produced a fabricated zero instead of an
    # error). Match separately against fact_dcb_rollups' own distinct
    # tariff_code_summarized values instead, normalized for whitespace/
    # case via the same _normalize() used for tariff descriptions below --
    # never by exact-matching the tariff-category column, which structurally
    # cannot contain these codes at all.
    q_norm_compact = _normalize(question)
    raw_parent_matches = [
        code for code in index.rollup_category_codes
        if _normalize(code) and _normalize(code) in q_norm_compact
    ]
    # Same "most specific wins" subsumption as tariff_codes_found above --
    # e.g. don't also report "HT-2" when the question named "HT-2(a)"
    # specifically (both would match via plain substring otherwise).
    normalized_parent_codes = {_normalize(c) for c in raw_parent_matches}
    parent_category_matches = [
        code for code in raw_parent_matches
        if not any(
            _normalize(code) != other and _normalize(code) in other
            for other in normalized_parent_codes
        )
    ]
    if parent_category_matches:
        entities["parent_category_matches"] = sorted(set(parent_category_matches))

    # Slab-level questions quote a dim_tariff_category.description verbatim
    # (e.g. off a spreadsheet row) rather than a tariff_code family like
    # "LT-2" above. Match those against the live table so the LLM gets the
    # real, exact-spelling string to filter on instead of either inventing
    # its own LIKE pattern or refusing because the text isn't one of the
    # 14 family codes it otherwise knows about. A description can legally
    # repeat under more than one tariff_code (e.g. the same "( Free
    # Lighting)" slab text appears under both LT-1 and LT-2(a)(i)(FL) in
    # this data) -- that's a real, separate row each time, not a dedup bug,
    # so every matching (code, description) pair is kept.
    q_norm = _normalize(question)
    raw_matches = [
        (code, description, _normalize(description))
        for code, description in index.tariff_categories
        if _normalize(description) and _normalize(description) in q_norm
    ]
    # Descriptions in this table often share a common prefix across slabs
    # (e.g. the plain and "( Free Lighting)" variants of the same tariff
    # family) -- normalizing one and finding it "in" the question can also
    # trivially match the shorter prefix of a longer description the
    # question actually names. Drop a match whose normalized text is a
    # strict substring of another match's, so a question naming the FL
    # variant doesn't also drag in the non-FL variant as noise.
    normalized_texts = {norm for _, _, norm in raw_matches}
    tariff_category_matches = [
        {"tariff_code": code, "description": description}
        for code, description, norm in raw_matches
        if not any(norm != other and norm in other for other in normalized_texts)
    ]
    if tariff_category_matches:
        entities["tariff_category_matches"] = tariff_category_matches
    else:
        # No description appears verbatim -- most questions describe a
        # slab in paraphrased/abbreviated business language instead (e.g.
        # "Pvt institutions lighting in ULB's and CC" for the DB's own
        # "Ltng. ,heating & Motive Power , Pvt. Institu. in ULB's & CC"),
        # which the exact-substring check above can never catch. Score
        # every description by what fraction of ITS OWN words appear in
        # the question (after the synonym/typo normalization above) --
        # deterministic bag-of-words overlap, still no LLM/embeddings, in
        # keeping with this module's router design. Unlike
        # tariff_category_matches these are ranked best-guess candidates,
        # not confirmed hits -- the prompt says so and asks the LLM to
        # pick among them using the rest of the question (metric,
        # location, HP/kV tier, etc.), the same way a human skimming the
        # tariff sheet would.
        q_tokens = _content_tokens(question)
        scored = []
        for code, description in index.tariff_categories:
            d_tokens = _content_tokens(description)
            overlap = d_tokens & q_tokens
            if len(overlap) < 2 or not d_tokens:
                continue
            score = len(overlap) / len(d_tokens)
            if score >= 0.5:
                scored.append((score, code, description))
        scored.sort(key=lambda item: -item[0])
        if scored:
            entities["tariff_category_candidates"] = [
                {"tariff_code": code, "description": description, "match_score": round(score, 2)}
                for score, code, description in scored[:6]
            ]

    return entities


def prune_semantic_layer(question: str, index: RouterIndex) -> dict:
    """Return a subset of the full semantic layer relevant to `question`.

    Kept unconditionally (cheap + load-bearing for correctness, not worth
    the risk of pruning away): the grounding `rules`, `common_metrics`,
    `joins`, and every table's schema (`tables`). Pruned by keyword match:
    `glossary` and `tariff_code_glossary` entries, so an unrelated
    question doesn't carry the whole 16-entry tariff glossary along.
    """
    full = load_semantic_layer()
    q_lower = question.lower()

    pruned_glossary = {
        term: definition
        for term, definition in full["glossary"].items()
        if term.lower() in q_lower or any(w in q_lower for w in term.lower().split("/"))
    }
    # Always keep the double-counting-relevant terms — cheap, and the
    # rules below reference them by name.
    for must_keep in ("Sub Total / LT-TOTAL / HT-TOTAL / GRAND TOTAL",):
        if must_keep in full["glossary"]:
            pruned_glossary[must_keep] = full["glossary"][must_keep]

    pruned_tariff_glossary = {
        code: definition
        for code, definition in full["tariff_code_glossary"].items()
        if code.lower() in q_lower
    }
    # If the question doesn't literally name a code, it may still describe
    # a family generically ("commercial", "residential", "irrigation
    # pumps") -- a FIXED keyword list here has exactly the same failure
    # shape the identity-check/entity-extraction work elsewhere in this
    # module exists to avoid: it was previously missing "commercial" and
    # "residential" even though HT-2's/HT-4's own definitions use those
    # exact words. Score each entry's own definition text against the
    # question the same deterministic, no-LLM/no-embeddings way
    # tariff_category_candidates already scores dim_tariff_category
    # descriptions above (same _content_tokens overlap, same >=2-token/
    # >=0.5-score bar) instead of matching a hand-maintained word list.
    if not pruned_tariff_glossary:
        q_tokens = _content_tokens(question)
        scored_codes = []
        for code, definition in full["tariff_code_glossary"].items():
            d_tokens = _content_tokens(definition)
            overlap = d_tokens & q_tokens
            if len(overlap) < 2 or not d_tokens:
                continue
            score = len(overlap) / len(d_tokens)
            if score >= 0.5:
                scored_codes.append(code)
        if scored_codes:
            pruned_tariff_glossary = {code: full["tariff_code_glossary"][code] for code in scored_codes}
        else:
            # No single entry scored confidently enough to know which
            # one(s) are relevant -- keep the whole (still small, 16-entry)
            # glossary rather than guess wrong or drop it entirely, same
            # "safer to over-include" fallback philosophy as before, just
            # no longer gated on a fixed keyword list that inevitably goes
            # stale as new phrasings come up.
            pruned_tariff_glossary = full["tariff_code_glossary"]

    return {
        "_purpose": full["_purpose"],
        "database": full["database"],
        "glossary": pruned_glossary,
        "tariff_code_glossary": pruned_tariff_glossary,
        "tables": full["tables"],
        "views": full["views"],
        "joins": full["joins"],
        "rules": full["rules"],
        "common_metrics": full["common_metrics"],
    }
