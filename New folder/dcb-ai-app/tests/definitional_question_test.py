"""
Deterministic, code-level tests for app.semantic_layer.is_definitional_question
-- the short-circuit that answers a bare "what is LT-1" / "define HT-2" /
"what does DCB mean" straight from semantic_layer.json's own glossary/
tariff_code_glossary dicts, never touching the LLM or DB. No LLM involved --
the whole point is that this holds regardless of what an LLM might have done
with the same question (see app.pipeline.answer_question's own short-circuit,
modeled on the pre-existing is_identity_question one).

Run from the dcb-ai-app/ directory:  python -m tests.definitional_question_test
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.semantic_layer import is_definitional_question, load_semantic_layer

_LAYER = load_semantic_layer()
_TARIFF_CODES = [
    "LT-1", "LT-2", "LT-3", "LT-4", "LT-5", "LT-6", "LT-7",
    "HT-1", "HT-2", "HT-3", "HT-4", "HT-5", "HT-6", "HT-7",
]
assert all(code in _LAYER["tariff_code_glossary"] for code in _TARIFF_CODES), (
    "This test's own code list must stay in sync with semantic_layer.json's "
    "real tariff_code_glossary keys -- if this fires, one of them was renamed/removed."
)

# --- Positive cases: MUST short-circuit, returning the exact expected key -

POSITIVE_CASES: list[tuple[str, str]] = []


def _add_positive(question: str, expected_key: str) -> None:
    POSITIVE_CASES.append((question, expected_key))


# Every LT-1..LT-7 / HT-1..HT-7 code, in every phrasing the pipeline is
# supposed to recognize, each with and without a trailing "?" -- generated
# rather than hand-typed 140 times so every code gets identical coverage.
for _code in _TARIFF_CODES:
    _phrasings = [
        f"what is {_code}",
        f"what's {_code}",
        f"define {_code}",
        f"explain {_code} category",
        f"tell me about {_code}",
    ]
    for _p in _phrasings:
        _add_positive(_p, _code)
        _add_positive(_p + "?", _code)

# Case-insensitivity and extra whitespace -- same "case-insensitive,
# whitespace-normalized" matching extract_entities' own code-matching uses.
_add_positive("what is lt-1", "LT-1")
_add_positive("WHAT IS HT-2", "HT-2")
_add_positive("what is   LT-3", "LT-3")
_add_positive("  what is LT-4  ", "LT-4")
_add_positive("what is the LT-5", "LT-5")
_add_positive("what is the LT-6 tariff code", "LT-6")
_add_positive("what is HT-7 code", "HT-7")

# General glossary (semantic_layer.json's plain `glossary` dict), same
# phrasings, plus the "what does X mean" shape that's specific to it.
for _term, _key in [
    ("DCB", "DCB"), ("LT", "LT"), ("HT", "HT"), ("FAC", "FAC"),
    ("ARR", "ARR"), ("OB", "OB"), ("CB", "CB"),
]:
    for _q in (
        f"what is {_term}",
        f"what's {_term}",
        f"define {_term}",
        f"explain {_term}",
        f"tell me about {_term}",
        f"what does {_term} mean",
    ):
        _add_positive(_q, _key)
        _add_positive(_q + "?", _key)

# Compound glossary keys ("A / B / C") -- matched via any one of their
# "/"-separated parts, same convention prune_semantic_layer's own glossary
# pruning already uses.
_add_positive("what does Sub Total mean", "Sub Total / LT-TOTAL / HT-TOTAL / GRAND TOTAL")
_add_positive("what is LT-TOTAL", "Sub Total / LT-TOTAL / HT-TOTAL / GRAND TOTAL")
_add_positive("what is GRAND TOTAL", "Sub Total / LT-TOTAL / HT-TOTAL / GRAND TOTAL")
_add_positive("what is DC/MNR", "DC/MNR")
_add_positive("what does DC/MNR mean", "DC/MNR")


# --- Negative cases: MUST NOT match -- must fall through (None) to the ----
# normal LLM/SQL pipeline, either because they're real data questions that
# merely start with "what is", or because they match the bare shape but
# name something that isn't actually in either glossary.

NEGATIVE_CASES: list[str] = [
    # Metric attached -- a real data question, not a definition lookup.
    "what is the demand for LT-1",
    "what is the collection for HT-2",
    "what is LT-1's demand in 2024-25",
    "what is LT-1's collection efficiency",
    # Year attached.
    "what is HT-2 in 2024-25",
    "what is the LT-1 demand in 2015-16",
    # Comparison.
    "what is LT-1 vs HT-2",
    "what is LT-1 vs HT-2 demand",
    "LT-1 or HT-2, what is bigger",
    "what is bigger, LT-1 or HT-2",
    # Sub-division attached.
    "what is LT-1 in Arkalgud",
    "what is HT-2 demand in Konanur",
    # Matches the bare definitional SHAPE, but names a real sub-slab code
    # that is NOT a key in tariff_code_glossary (only the bare family
    # codes LT-1..LT-7/HT-1..HT-7 are) -- must fall through, not guess.
    "what is LT-2(b)(ii)",
    "define LT-3(i)",
    "what is HT-2(a)(ii)",
    # Matches the shape but names something in neither glossary at all.
    "what is ZZ-99",
    "define Holenarasipura",
    "what does Arkalgud mean",
    # Not the definitional shape at all -- ordinary questions this
    # short-circuit must never intercept.
    "collection efficiency in Arkalgud 2024-25",
    "compare LT-1 vs HT-2 demand across all years",
    "top tariff category by demand 2023-24",
    "who are you",
]


def main() -> None:
    failures = []

    print(f"--- Positive cases (must short-circuit, {len(POSITIVE_CASES)} total) ---")
    pos_failures = []
    for question, expected in POSITIVE_CASES:
        got = is_definitional_question(question)
        if got != expected:
            print(f"FAIL: {question!r} -> {got!r} (expected {expected!r})")
            pos_failures.append(question)
    failures.extend(pos_failures)
    print(f"{len(POSITIVE_CASES) - len(pos_failures)}/{len(POSITIVE_CASES)} positive cases correct.")

    print(f"\n--- Negative cases (must fall through, {len(NEGATIVE_CASES)} total) ---")
    neg_failures = []
    for question in NEGATIVE_CASES:
        got = is_definitional_question(question)
        if got is not None:
            print(f"FAIL (wrongly short-circuited!): {question!r} -> {got!r}")
            neg_failures.append(question)
        else:
            print(f"OK, fell through: {question!r}")
    failures.extend(neg_failures)

    total = len(POSITIVE_CASES) + len(NEGATIVE_CASES)
    print(f"\n{total - len(failures)}/{total} cases behaved as expected.")
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL CASES PASSED.")


if __name__ == "__main__":
    main()
