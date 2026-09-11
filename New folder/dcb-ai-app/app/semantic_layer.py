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


@dataclass
class RouterIndex:
    """Entity lists pulled from the live DB once at startup, used to spot
    which parts of the semantic layer a question actually touches."""

    subdivision_names: list[str] = field(default_factory=list)
    fiscal_years: list[str] = field(default_factory=list)


_full_layer: dict | None = None


def load_semantic_layer() -> dict:
    global _full_layer
    if _full_layer is None:
        _full_layer = json.loads(Path(SEMANTIC_LAYER_PATH).read_text(encoding="utf-8"))
    return _full_layer


def build_router_index(db) -> RouterIndex:
    result = db.run(
        "SELECT subdivision_name FROM dim_subdivision", max_rows=100, timeout_seconds=5
    )
    subdivisions = [row[0] for row in result.rows]
    result = db.run(
        "SELECT fiscal_year FROM dim_fiscal_year", max_rows=100, timeout_seconds=5
    )
    years = [row[0] for row in result.rows]
    return RouterIndex(subdivision_names=subdivisions, fiscal_years=years)


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

    tariff_codes_found = [
        code
        for code in (
            "LT-1", "LT-2", "LT-3", "LT-4", "LT-5", "LT-6", "LT-7",
            "HT-1", "HT-2", "HT-3", "HT-4", "HT-5", "HT-6", "HT-7",
        )
        if code.lower() in q_lower
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
    # If the question mentions a tariff *family* generically ("LT" or
    # "HT" or "domestic"/"industrial") but no specific code, keep the
    # whole tariff glossary — safer than guessing wrong and it's still
    # small (16 entries).
    generic_tariff_terms = ["tariff", "lt-", "ht-", "domestic", "industrial", "irrigation", "misc"]
    if not pruned_tariff_glossary and any(t in q_lower for t in generic_tariff_terms):
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
