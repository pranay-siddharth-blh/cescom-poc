"""The deterministic routes: router, metric lookup, reports, verified queries -- every figure checked against an
independent query on the locked star schema, never against the mart the routes read."""
import pytest

from app import metric_lookup, reports, router, verified_queries
from tests.conftest import run, star_one

DIV = "SELECT {c} FROM fact_dcb_grand_totals g JOIN dim_subdivision s USING (subdivision_id) WHERE s.subdivision_type = 'division' AND g.fiscal_year = ?"
SUB = "SELECT {c} FROM fact_dcb_grand_totals g JOIN dim_subdivision s USING (subdivision_id) WHERE s.subdivision_name = ? AND g.fiscal_year = ?"


@pytest.mark.parametrize("q,key,val", [
    ("What was the collection efficiency in Arkalgud in 2024-25?", "subdivisions", ["Arkalgud"]),
    ("dmd for lt1 this year", "families", ["LT-1"]),
    ("dmd for lt1 this year", "fiscal_years", ["2025-26"]),
    ("l.t.-1", "families", ["LT-1"]),
    ("HT - 2 demand", "families", ["HT-2"]),
    ("give total collection FY24", "fiscal_years", ["2023-24"]),
    ("since 2020", "fiscal_years", ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]),
    ("Belur sub-division demand", "unknown_subdivisions", ["Belur"]),
    ("demand for LT-9", "unknown_tariffs", ["LT-9"]),
    ("data for 2010-11", "out_of_range", ["2010-11"]),
    ("LT-4(a) demand in Arkalgud", "slab_codes", ["LT-4(a)"]),
    ("show demand, collection, and arrears for Division_Total this year", "metrics",
     ["demand", "collection", "closing_balance"]),
])
def test_router(q, key, val):
    assert router.extract(q)[key] == val


def test_holenarasipura_division_vs_subdivision():
    assert router.extract("Holenarasipura division demand").get("scope") == "division"
    assert router.extract("Holenarasipura division demand")["subdivisions"] == []
    assert router.extract("Holenarasipura collection efficiency")["subdivisions"] == ["Holenarasipura"]


def ask_metric(db, q):
    return run(metric_lookup.answer(router.extract(q), q, db, 10))


def test_metric_collection_efficiency_arkalgud(db):
    a = ask_metric(db, "What was the collection efficiency in Arkalgud in 2024-25?")
    assert "93.62%" in a.answer


def test_metric_ht2_demand_matches_printed_subtotal(db):
    printed = star_one(db, "SELECT r.demand_total FROM fact_dcb_rollups r JOIN dim_subdivision s USING (subdivision_id) "
                           "WHERE s.subdivision_type = 'division' AND r.fiscal_year = '2015-16' AND r.tariff_code_summarized = 'HT-2'")
    assert f"₹{printed:,.2f}" in ask_metric(db, "HT-2 demand in 2015-16").answer


def test_metric_division_demand_default_year(db):
    want = star_one(db, DIV.format(c="demand_total"), ("2025-26",))
    a = ask_metric(db, "total demand")
    assert f"₹{want:,.2f}" in a.answer and "latest year" in a.answer


def test_metric_installations_and_dues(db):
    inst = star_one(db, DIV.format(c="total_installations"), ("2025-26",))
    assert f"{inst:,.0f}" in ask_metric(db, "how many connections do we have").answer
    cb = star_one(db, SUB.format(c="closing_balance_total"), ("Konanur", "2025-26"))
    assert f"₹{cb:,.2f}" in ask_metric(db, "total dues in konanur").answer


def test_metric_no_figures_for_closed_subdivision(db):
    a = ask_metric(db, "Saligrama collection efficiency in 2020-21")
    assert "no figures" in a.answer and "2017-18" in a.answer


def test_metric_declines_what_it_cannot_honour(db):
    for q in ("average demand per year in Arkalgud", "which sub-division collects the best", "LT-1 vs HT-2 demand",
              "collection efficiency trend"):
        assert ask_metric(db, q) is None, q


def ask_report(db, q):
    return run(reports.match_and_run(q, router.extract(q), db, 10))


@pytest.mark.parametrize("q,key", [
    ("LT-1 vs HT-2 demand", "family_comparison"), ("Arkalgud or Konanur, which collects better", "subdivision_rank"),
    ("top 3 tariff categories by demand", "tariff_rank"), ("which area has the most pending dues", "subdivision_rank"),
    ("worst performing area", "subdivision_rank"), ("show me arkalgud numbers", "summary"),
    ("compare curent year and last year collection", "year_comparison"), ("past 3 years collection efficiency trend", "trend"),
    ("why is ht2 demand so high suddenly", "trend"), ("LT vs HT demand in 2024-25", "voltage_split"),
    ("collection efficiency trend for Arkalgud and Konanur", "subdivision_series"), ("latest figures", "summary"),
])
def test_report_routes(db, q, key):
    r = ask_report(db, q)
    assert r is not None and r.key == key, (q, r and r.key)


def test_report_ranking_matches_star(db):
    r = ask_report(db, "which sub-division collects the best")
    rows = db.run_sync("SELECT s.subdivision_name, g.collection_grand_total * 100.0 / g.demand_total AS ce FROM fact_dcb_grand_totals g "
                       "JOIN dim_subdivision s USING (subdivision_id) WHERE s.subdivision_type IN ('sub_division', 'section') "
                       "AND g.fiscal_year = '2025-26' ORDER BY ce DESC", 10).rows
    assert r.rows[0][0] == rows[0][0] == "Konanur"
    assert f"{rows[-1][1]:.2f}%" in r.answer


def test_report_top_tariff_share(db):
    r = ask_report(db, "top 3 tariff categories by demand")
    assert len(r.rows) == 3 and r.rows[0][0] == "LT-4" and "59.7%" in r.answer


def test_group_words_go_to_the_model(db):
    for q in ("where do we have the most industries", "LT-6 EV charging station demand in 2016-17"):
        assert ask_report(db, q) is None


def test_verified_queries_are_conservative():
    assert verified_queries.match("Collection efficiency of every sub-division in 2024-25").example_no == 1
    assert verified_queries.match("Collection efficiency of every sub-division in 2023-24") is None
    assert verified_queries.match("LT-4 demand in Konanur for every year") is None
    assert verified_queries.match("LT-4 demand in Arkalgud for every year").example_no == 4


def test_every_verified_query_runs(db):
    for vq in verified_queries.library():
        res = db.run_sync(vq.sql, 200)
        assert res.rows, vq.key
        text, _ = verified_queries.render(vq, res.columns, res.rows)
        assert text
