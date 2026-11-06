"""The CESC tariff booklet FY2025-26 to FY2027-28 (DCB_Mart/metadata/tariff_booklet.json, app/tariff_booklet.py) and
what was built on it: tariff answers with no model, the booklet-category mart tables, the data-vs-booklet rate check,
the cash / adjustment (subsidy) split, billing efficiency and AT&C loss, and the booklet-based glossary."""
from unittest.mock import AsyncMock, patch

import pytest

from app import pipeline, tariff_booklet
from app.semantic_layer import load_semantic_layer
from tests.conftest import run, star_one


def ask(db, router_index, q):
    with patch("app.pipeline.generate_query_plan", AsyncMock(side_effect=AssertionError("model called"))):
        return run(pipeline.answer_question(q, db, router_index, 200, 10))


def one(db, sql, params=()):
    return db.run_sync(sql, 5, params=params).rows[0][0]


# ---------------------------------------------------------------------------------------------- the booklet itself
def test_booklet_has_every_category_and_three_years_of_rates():
    cats = tariff_booklet.categories()
    codes = [c["code"] for c in cats]
    assert codes == ["LT-1", "LT-2", "LT-3(a)", "LT-3(b)", "LT-4(a)", "LT-4(b)", "LT-4(c)", "LT-5", "LT-6(a)", "LT-6(b)",
                     "LT-6(c)", "LT-7", "HT-1", "HT-2(a)", "HT-2(b)", "HT-2(c)(i)", "HT-2(c)(ii)", "HT-3", "HT-4", "HT-5",
                     "HT-6", "HT-7"]
    for c in cats:
        assert set(c["rates"]) == {"2025-26", "2026-27", "2027-28"}, c["code"]
        assert c["applies_to"] and c["page"]


@pytest.mark.parametrize("code,fy,key,value", [
    ("LT-1", "2025-26", "energy_paise", 580), ("LT-1", "2027-28", "fixed", 160), ("LT-4(a)", "2025-26", "cdt_paise", 830),
    ("LT-4(a)", "2027-28", "cdt_paise", 899), ("HT-2(b)", "2026-27", "energy_paise", 570), ("HT-2(c)(i)", "2025-26", "demand", 295),
    ("LT-6(b)", "2025-26", "energy_led_paise", 600), ("HT-5", "2025-26", "energy_paise", 1050), ("HT-7", "2025-26", "energy_paise", 475),
])
def test_transcribed_rates(code, fy, key, value):
    assert tariff_booklet.category(code)["rates"][fy][key] == value


# ---------------------------------------------------------------------------------------------- tariff answers
@pytest.mark.parametrize("q,route,needle", [
    ("What is the LT-3 tariff rate?", "tariff:rate:LT-3(a),LT-3(b)", "700 paise"),
    ("energy charge rate for HT-2(b) in 2026-27", "tariff:rate:HT-2(b)", "570 paise"),
    ("Which tariff category is a homestay?", "tariff:category:LT-1", "LT-3(a)"),
    ("Under which category does an EV charging station fall?", "tariff:category:LT-6(c)", "450 paise"),
    ("which category is a data centre on HT", "tariff:category:HT-2(a)", "Industrial (HT)"),
    ("What is LT-4(a)?", "tariff:explain:LT-4(a)", "Commission Determined Tariff"),
    ("sub-categories of LT-4", "tariff:list:lt4", "LT-4(c)"),
    ("What are the tariff categories?", "tariff:list", "22 categories"),
    ("interest on late payment", "tariff:rule:late_payment_interest", "1% per month"),
    ("reconnection charges", "tariff:rule:reconnection_charges", "Rs 500"),
    ("electricity tax", "tariff:rule:electricity_tax", "9%"),
    ("what is ToD", "tariff:rule:tod", "500 kVA"),
    ("slab rates for domestic", "tariff:rate:LT-1", "Single slab"),
])
def test_tariff_questions_come_from_the_booklet(db, router_index, q, route, needle):
    r = ask(db, router_index, q)
    assert r.route == route and needle in r.answer, (q, r.route, r.answer[:300])


@pytest.mark.parametrize("q", [
    "LT-1 demand in 2024-25", "energy charges billed in Arkalgud", "collection rate in 2024-25",
    "which tariff category has the highest demand", "electricity tax collected in 2024-25", "LT-4 collection efficiency",
    # a slab description that contains a rule word ("charitable") is still a data question (regression suite 28)
    "Govt charitable university and ESI hospital hostels — demand in Saligrama",
    # regression suite 15 / K65: a slab question naming LT-5(b), and a follow-up formatting request
    "What is LT-5(b) heating and motive power, 5 to 40 HP slab, in Saligrama", "round to the nearest rupee",
])
def test_data_questions_are_not_taken_by_the_tariff_route(db, router_index, q):
    assert run(tariff_booklet.answer(q, db)) is None, q


def test_lt5b_is_lt5_the_booklet_has_no_sub_categories(db, router_index):
    r = ask(db, router_index, "What is LT-5(b)?")
    assert r.route == "tariff:explain:LT-5" and "has only LT-5, with no sub-categories" in r.answer
    g = load_semantic_layer()["tariff_code_glossary"]
    assert "only LT-5" in g["LT-5(a) / LT-5(b)"] and "LT-1 Domestic" in g["(FL) Free Lighting"]
    assert "LT-3(a)" in g["(OL) Office Lighting"]


def test_adjustment_is_the_subsidy(db, router_index):
    r = ask(db, router_index, "subsidy in 2025-26")
    assert r.route == "metric:adjustment_collection" and r.answer.startswith("**Government subsidy")


def test_rate_for_a_year_the_booklet_does_not_cover_says_so(db, router_index):
    r = ask(db, router_index, "what is the tariff rate for LT-1 in 2023-24")
    assert r.route == "tariff:rate:LT-1" and "covers FY2025-26 to FY2027-28 only" in r.answer


def test_data_vs_booklet_check(db, router_index):
    r = ask(db, router_index, "does the billed rate match the tariff booklet?")
    assert r.route == "tariff:check" and "matches the booklet within 1%" in r.answer
    assert r.table and r.rows


# ---------------------------------------------------------------------------------------------- the mart
def test_every_slab_maps_to_a_booklet_category(db):
    assert one(db, "SELECT COUNT(*) FROM mart_dim_tariff WHERE booklet_code IS NULL AND family_code NOT IN "
                   "('MISC', 'LT IPP', 'HT IPP', 'AUXILIARY CONSUMPTION')") == 0
    assert one(db, "SELECT booklet_code FROM mart_dim_tariff WHERE tariff_code = 'HT-3(a)(i)' LIMIT 1") == "HT-7"
    assert one(db, "SELECT booklet_code FROM mart_dim_tariff WHERE tariff_code = 'LT-2(a)(i)' LIMIT 1") == "LT-1"


def test_booklet_domestic_is_comparable_across_the_restructuring(db):
    # LT-1 on the booklet basis 2023-24 = printed LT-1 (BJ/KJ) + LT-2(a) domestic that year
    book = one(db, "SELECT demand_total FROM mart_fact_booklet_division_year WHERE booklet_code = 'LT-1' AND fiscal_year = '2023-24'")
    printed = one(db, "SELECT SUM(demand_total) FROM mart_fact_slab_division_year WHERE fiscal_year = '2023-24' "
                      "AND (tariff_code = 'LT-1' OR tariff_code LIKE 'LT-2(a)%')")
    assert abs(book - printed) < 1


def test_cash_plus_adjustment_is_the_collection(db):
    for t in ("mart_fact_division_year", "mart_fact_subdivision_year", "mart_fact_booklet_division_year"):
        assert one(db, f"SELECT MAX(ABS(collection_cash_total + collection_adjust_total - collection_grand_total)) FROM {t}") < 2


def test_cash_and_adjustment_match_the_star_schema(db):
    adj = star_one(db, "SELECT SUM(collection_revenue_adjust) FROM fact_dcb f JOIN dim_tariff_category t USING (tariff_category_id) "
                       "JOIN dim_subdivision s USING (subdivision_id) WHERE s.subdivision_type = 'division' "
                       "AND f.fiscal_year = '2025-26' AND t.tariff_code LIKE 'LT-4(a)%'")
    mine = one(db, "SELECT collection_adjust_total FROM mart_fact_booklet_division_year WHERE booklet_code = 'LT-4(a)' AND fiscal_year = '2025-26'")
    assert mine >= adj - 1   # all heads >= the revenue head alone


def test_gruha_jyothi_shows_in_the_domestic_adjustment_share(db):
    share = {fy: v for fy, v in db.run_sync("SELECT fiscal_year, adjustment_share_pct FROM mart_fact_booklet_division_year "
                                             "WHERE booklet_code = 'LT-1'", 20).rows}
    assert share["2022-23"] < 20 and share["2023-24"] > 55 and share["2024-25"] > 80
    assert one(db, "SELECT ROUND(adjustment_share_pct) FROM mart_fact_booklet_division_year WHERE booklet_code = 'LT-4(a)' "
                   "AND fiscal_year = '2025-26'") == 100


def test_atc_loss_formula(db):
    be, ce, atc = db.run_sync("SELECT billing_efficiency_pct, collection_efficiency_pct, atc_loss_pct FROM mart_fact_division_year "
                              "WHERE fiscal_year = '2025-26'", 2).rows[0]
    assert abs(atc - (100 - be * ce / 100)) < 1e-6
    billed, live = db.run_sync("SELECT billed_installations, live_installations FROM mart_fact_division_year "
                               "WHERE fiscal_year = '2025-26'", 2).rows[0]
    assert abs(be - billed * 100 / live) < 1e-9


def test_rate_check_rows_agree(db):
    rows = db.run_sync("SELECT booklet_code, difference_pct, consumption_total FROM mart_tariff_rate_check "
                       "WHERE data_energy_paise IS NOT NULL", 50).rows
    assert len(rows) >= 14
    assert all(abs(d) <= 6 for _, d, u in rows if u and u >= 100000)
    assert one(db, "SELECT ROUND(data_energy_paise) FROM mart_tariff_rate_check WHERE booklet_code = 'LT-1' AND fiscal_year = '2025-26'") == 581


# ---------------------------------------------------------------------------------------------- measures in chat
@pytest.mark.parametrize("q,route,needle", [
    ("AT&C loss in 2025-26", "metric:atc_loss", "6.95%"),
    ("billing efficiency in 2025-26", "metric:billing_efficiency", "99.86%"),
    ("collection efficiency without adjustment in 2025-26", "metric:collection_efficiency_without_adj", "20.77%"),
    ("subsidy share in 2025-26", "metric:adjustment_share", "77.71%"),
])
def test_new_measures_answer_without_the_model(db, router_index, q, route, needle):
    r = ask(db, router_index, q)
    assert r.route == route and needle in r.answer, (q, r.route, r.answer[:300])


def test_amounts_for_a_part_carry_their_share_of_the_whole(db, router_index):
    r = ask(db, router_index, "demand in Arkalgud in 2024-25")
    assert r.route == "metric:demand" and "% of Holenarasipura Division" in r.answer


# ---------------------------------------------------------------------------------------------- glossary
def test_glossary_follows_the_booklet():
    g = load_semantic_layer()["tariff_code_glossary"]
    assert "usage-slab" not in g["LT-1"] and "Single slab" in g["LT-1"]
    assert "LT-4(a)" in g and "830 paise" in g["LT-4(a)"]
    assert "HT-7" in g["HT-3(a)(i)"]
    assert "AT&C loss" in load_semantic_layer()["glossary"]


def test_glossary_api_serves_the_booklet():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        d = c.get("/api/glossary").json()
    assert len(d["booklet"]["categories"]) == 22 and d["booklet"]["rules"]
    assert d["tariff_code_glossary"]["LT-2"].startswith("Private educational institutions")
