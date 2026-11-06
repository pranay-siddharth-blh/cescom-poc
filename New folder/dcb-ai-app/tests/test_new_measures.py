"""FPPCA, DC / MNR, the sheet's DCB percentages and per-consumer figures (2026-10-01): every figure checked against an
independent query on the locked star schema's printed rows, never against the mart the app reads."""
import io
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import pipeline
from tests.conftest import run, star_one

DIV = ("SELECT {c} FROM fact_dcb_rollups r JOIN dim_subdivision s USING (subdivision_id) "
       "WHERE r.rollup_type = 'grand_total' AND s.subdivision_type = 'division' AND r.fiscal_year = ?")
SUB = ("SELECT {c} FROM fact_dcb_rollups r JOIN dim_subdivision s USING (subdivision_id) "
       "WHERE r.rollup_type = 'grand_total' AND s.subdivision_name = ? AND r.fiscal_year = ?")


def ask(db, router_index, q):
    with patch("app.pipeline.generate_query_plan", AsyncMock(side_effect=AssertionError("model called"))):
        return run(pipeline.answer_question(q, db, router_index, 200, 10))


def rupees(v):
    return f"₹{v:,.2f}"


@pytest.mark.parametrize("q,col,fy", [
    ("FPPCA billed in 2024-25", "demand_fac", "2024-25"),
    ("fuel and power purchase cost adjustment in 2023-24", "demand_fac", "2023-24"),
    ("FPPCA outstanding in 2025-26", "closing_balance_fac", "2025-26"),
    ("FPPCA collected in 2022-23", "collection_fac_total", "2022-23"),
])
def test_fppca_amounts_match_the_printed_grand_total(db, router_index, q, col, fy):
    want = star_one(db, DIV.format(c=col), (fy,))
    r = ask(db, router_index, q)
    assert r.route.startswith("metric:") and rupees(want) in r.answer, (q, r.answer[:200])


def test_fppca_refund_year_says_so(db, router_index):
    r = ask(db, router_index, "FPPCA share of demand in 2021-22")
    assert r.route == "metric:fppca_share" and "-" in r.answer.split("**")[1] and "refund" in r.answer


def test_mnr_and_dc_counts(db, router_index):
    mnr = star_one(db, DIV.format(c="mnr_installations"), ("2024-25",))
    assert f"{mnr:,.0f}" in ask(db, router_index, "MNR installations in 2024-25").answer
    dc = star_one(db, SUB.format(c="dc_installations"), ("Konanur", "2025-26"))
    r = ask(db, router_index, "DC installations in Konanur in 2025-26")
    assert f"{dc:,.0f}" in r.answer and "to be confirmed" not in r.answer      # DC = direct connection, confirmed (KI-15)


def test_mnr_before_2018_names_the_measure(db, router_index):
    r = ask(db, router_index, "MNR installations in 2016-17")
    assert not r.answerable and "no MNR installations figures" in r.answer and "2018-19" in r.answer


@pytest.mark.parametrize("q,col", [
    ("billed installations % in Arkalgud in 2024-25", "pct_billed_installations"),
    ("assessed consumption % in Arkalgud in 2024-25", "pct_assessed_consumption"),
    ("dc/mnr % in Arkalgud in 2024-25", "pct_dc_mnr"),
])
def test_percentages_equal_the_printed_ones(db, router_index, q, col):
    want = star_one(db, SUB.format(c=col), ("Arkalgud", "2024-25"))
    assert f"{want:.2f}%" in ask(db, router_index, q).answer


def test_per_consumer(db, router_index):
    arrears = star_one(db, SUB.format(c="closing_balance_total"), ("Hangarahalli", "2025-26"))
    inst = star_one(db, SUB.format(c="total_installations"), ("Hangarahalli", "2025-26"))
    r = ask(db, router_index, "arrears per consumer by sub-division")
    assert f"{arrears / inst:,.2f}" in r.answer


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        assert c.post("/api/login", json={"username": "tester", "password": "x"}).json()["ok"]
        yield c


def test_dashboard_indicators(client, db):
    d = client.get("/api/dashboard_summary").json()
    ind = d["indicators"]["current"]
    fy = d["fiscal_year"]
    assert abs(ind["live_installation_pct"] - star_one(db, DIV.format(c="pct_live_installations"), (fy,))) < 0.01
    assert abs(ind["dc_mnr_installation_pct"] - star_one(db, DIV.format(c="pct_dc_mnr"), (fy,))) < 0.01
    assert abs(ind["demand_fac"] - star_one(db, DIV.format(c="demand_fac"), (fy,))) < 1
    assert d["indicators"]["previous"] is not None
    s = client.get("/api/dashboard_summary/Arkalgud").json()["indicators"]["current"]
    assert abs(s["mnr_installations"] - star_one(db, SUB.format(c="mnr_installations"), ("Arkalgud", fy))) < 0.5


def test_reports_series_has_consumers(client):
    s = client.get("/api/reports_series", params={"entity_type": "subdivision", "value": "Konanur"}).json()
    i = s["fiscal_years"].index("2025-26")
    assert s["installations"][i] and abs(s["demand_per_installation"][i] - s["demand"][i] / s["installations"][i]) < 0.01
    assert len(s["live_installations"]) == len(s["fiscal_years"])
    sal = client.get("/api/reports_series", params={"entity_type": "subdivision", "value": "Saligrama"}).json()
    assert sal["fiscal_years"] == ["2015-16", "2016-17", "2017-18"]


def test_mis_workbook_carries_the_new_rows(client):
    r = client.get("/api/mis-report")
    assert r.status_code == 200
    ws = load_workbook(io.BytesIO(r.content))["Summary"]
    labels = [ws.cell(i, 1).value for i in range(1, ws.max_row + 1)]
    for want in ("FPPCA billed (₹)", "FPPCA outstanding (₹)", "MNR installations (from 2018-19)", "Assessed consumption %",
                 "Arrears per consumer (₹)"):
        assert want in labels, want
    row = labels.index("Live installations %") + 1
    assert ws.cell(row, 5).value is None              # a ratio gets no "change %"
