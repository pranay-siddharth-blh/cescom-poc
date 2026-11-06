"""Slab-level questions (app/slab_lookup.py): answered without the model, every figure checked against an independent
query on the locked star schema (fact_dcb line items), never against the mart the route reads."""
from unittest.mock import AsyncMock, patch

import pytest

from app import pipeline, slab_lookup
from tests.conftest import run, star_one

STAR = ("SELECT SUM(f.demand_total) FROM fact_dcb f JOIN dim_tariff_category t USING (tariff_category_id) "
        "JOIN dim_subdivision s USING (subdivision_id) WHERE f.fiscal_year = ? AND {where}")


def ask(db, router_index, q):
    with patch("app.pipeline.generate_query_plan", AsyncMock(side_effect=AssertionError("model called"))):
        return run(pipeline.answer_question(q, db, router_index, 200, 10))


def rupees(v):
    return f"₹{v:,.2f}"


@pytest.mark.parametrize("q", [
    "What was the demand for BMA lighting in 2024-25?", "unmetered IP sets demand in Arkalgud in 2023-24",
    "EV charging demand trend", "domestic demand over the years", "industries collection efficiency in 2024-25",
    "LT-4(a) demand in 2023-24", "free lighting installations", "Compare domestic vs commercial demand in 2024-25",
    "water supply demand in Konanur and Arkalgud in 2024-25", "which slab had the highest demand in 2024-25",
    "LT-4 unmetered demand", "street lights arrears", "private institutions in village panchayats demand in 2022-23",
])
def test_slab_questions_need_no_model(db, router_index, q):
    r = ask(db, router_index, q)
    assert r.route.startswith("slab:") and r.answerable, (q, r.route, r.answer[:200])
    assert "slab" in r.answer.lower() or r.route == "slab:compare"


def test_slab_code_matches_star(db, router_index):
    want = star_one(db, STAR.format(where="s.subdivision_type = 'division' AND t.tariff_code = 'LT-2(a)(i)'"), ("2023-24",))
    assert rupees(want) in ask(db, router_index, "LT-2(a)(i) demand in 2023-24").answer


def test_bma_segment_follows_the_recoding(db, router_index):
    """BMA lighting is LT-2(a)(i) up to 2023-24 and LT-1 from 2024-25: matched by description, both years work."""
    for fy in ("2023-24", "2024-25"):
        want = star_one(db, STAR.format(where="s.subdivision_type = 'division' AND t.description LIKE '%BMA & Municipal%'"), (fy,))
        r = ask(db, router_index, f"demand for BMA lighting in {fy}")
        assert rupees(want) in r.answer and "not comparable" not in r.answer


def test_domestic_2024_25_equals_printed_lt1(db, router_index):
    # From 2024-25 the sheet prints every domestic slab under LT-1 ("Sub Total of LT-1" + "Sub Total of LT-1 A").
    want = star_one(db, STAR.format(where="s.subdivision_type = 'division' AND t.tariff_code = 'LT-1'"), ("2024-25",))
    assert round(want, 2) == 473913919.16
    assert rupees(want) in ask(db, router_index, "domestic demand in 2024-25").answer


def test_qualifier_narrows_in_a_subdivision(db, router_index):
    want = star_one(db, STAR.format(where="s.subdivision_name = 'Arkalgud' AND t.description LIKE 'IP Sets%Un-Metered%'"),
                    ("2023-24",))
    assert rupees(want) in ask(db, router_index, "unmetered IP sets demand in Arkalgud in 2023-24").answer


def test_code_after_it_was_renamed_says_so(db, router_index):
    r = ask(db, router_index, "LT-2(b)(i) demand in 2024-25")
    assert not r.answerable and "LT-2(i)" in r.answer


def test_impossible_combination_is_refused(db, router_index):
    r = ask(db, router_index, "free lighting IP sets")
    assert r.route == "slab:none" and not r.answerable


def test_ambiguous_fuzzy_match_asks_instead_of_guessing(db):
    cat = run(slab_lookup.catalog(db, 10))
    cands = [{"tariff_code": "LT-3(i)", "description": "Comme. Ltng ,heating & Motive Power , in ULB's & CC", "match_score": 0.6},
             {"tariff_code": "LT-3(ii)", "description": "Comme. Ltng ,heating & Motive Power , in Village Pan.", "match_score": 0.6}]
    r = slab_lookup.resolve("comme ltng demand", {"families": [], "intents": []}, {"tariff_category_candidates": cands}, cat)
    assert r.clarify and not r.slabs


def test_unresolvable_wording_goes_to_the_model(db):
    cat = run(slab_lookup.catalog(db, 10))
    assert slab_lookup.resolve("lighting demand", {"families": [], "intents": []}, {}, cat) is None


def test_consumer_group_without_a_category_is_not_guessed(db, router_index):
    """Found live: the model answered "share of LT demand from government connections" with LT-1 over ALL demand
    (15.49%), and the reviewer passed it. Now: no LT slab is for government, said plainly, no model call."""
    r = ask(db, router_index, "What share of LT demand came from government connections in 2024-25?")
    assert r.route == "slab:none" and not r.answerable and "HT-2(c)(i)" in r.answer
    r = ask(db, router_index, "government demand")
    assert r.route == "slab:clarify" and "no single \"government\" category" in r.answer
    assert ask(db, router_index, "temples demand").route == "slab:none"
