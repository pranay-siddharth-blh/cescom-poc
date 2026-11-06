"""The full pipeline with the model mocked (repair rounds, reviewer, offline mode), the HTTP API (security headers,
limits, streaming, login gate), the Excel reports and their guardrails, and input integrity."""
import hashlib
import io
import json
from unittest.mock import AsyncMock, patch

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import answer_report, pipeline
from app.config import INTEGRITY_MANIFEST_PATH, PROJECT_ROOT, settings
from app.excel_common import ReportUnderReview, finalize
from app.llm import LLMUnavailable, Verdict
from app.models import ChartSpec
from tests.conftest import plan, run

SLAB_Q = "LT-2(b)(ii) demand across all years"
GOOD_SQL = ("SELECT f.fiscal_year, SUM(f.demand_total) AS demand FROM fact_dcb f JOIN dim_tariff_category t "
            "USING (tariff_category_id) JOIN dim_subdivision s USING (subdivision_id) WHERE t.tariff_code = 'LT-2(b)(ii)' "
            "AND s.subdivision_name = 'Division_Total' GROUP BY f.fiscal_year ORDER BY f.fiscal_year")


@pytest.fixture
def model_path():
    """These tests exercise the model path's mechanics with a slab question; the slab route (app/slab_lookup.py)
    would answer it deterministically, so it is switched off here. The slab route has its own tests below."""
    with patch("app.pipeline.slab_lookup.answer", AsyncMock(return_value=None)):
        yield


def slab_years(db):
    return len(db.run_sync(GOOD_SQL, 50).rows)


def ask(db, router_index, q):
    return run(pipeline.answer_question(q, db, router_index, 200, 10))


@pytest.mark.parametrize("q,route", [
    ("hello", "small_talk:greeting"), ("who are you", "identity"), ("what is LT-4", "tariff:explain:lt4"), ("monthly demand for Arkalgud", "capability"),
    ("collection in Mysuru sub-division", "capability"), ("demand for LT-9", "unknown_name"), ("data for 2010-11", "out_of_range"),
    ("tell me a joke", "refused:off_topic"), ("DROP TABLE dim_subdivision", "refused:code"),
    ("What was the collection efficiency in Arkalgud in 2024-25?", "metric:collection_efficiency"),
    ("LT-1 vs HT-2 demand", "report:family_comparison"),
    ("Collection efficiency of every sub-division in 2024-25", "verified:mart_example_01"),
])
def test_deterministic_routes_need_no_model(db, router_index, q, route):
    with patch("app.pipeline.generate_query_plan", AsyncMock(side_effect=AssertionError("model called"))):
        r = ask(db, router_index, q)
    assert r.route == route, (q, r.route)


def test_identity_answer_is_about_dcb(db, router_index):
    r = ask(db, router_index, "who are you")
    assert "Holenarasipura" in r.answer and "billing, and account data" not in r.answer


def test_offline_model_path_says_so(db, router_index, model_path):
    r = ask(db, router_index, SLAB_Q)          # conftest switched the gateway off
    assert r.route == "llm_unavailable" and not r.answerable


def test_model_path_happy(db, router_index, model_path):
    with patch("app.pipeline.generate_query_plan", AsyncMock(return_value=plan(GOOD_SQL, "x", ChartSpec(
            type="line", label_column="fiscal_year", value_column="demand")))), \
         patch("app.pipeline.verify_answer", AsyncMock(return_value=Verdict(True))):
        r = ask(db, router_index, SLAB_Q)
    assert r.route == "llm" and r.verification == "ok" and len(r.rows) == slab_years(db) >= 5
    assert r.chart_data["labels"][0] == "2015-16" and r.table and "years from FY 2015-16" in r.answer


def test_repair_round_after_guard_rejection(db, router_index, model_path):
    bad = "SELECT SUM(demand_total) AS d FROM fact_dcb"          # Division_Total double count
    gen = AsyncMock(side_effect=[plan(bad, "{d}"), plan(GOOD_SQL, "x")])
    with patch("app.pipeline.generate_query_plan", gen), patch("app.pipeline.verify_answer", AsyncMock(return_value=Verdict(True))):
        r = ask(db, router_index, SLAB_Q)
    assert gen.await_count == 2 and "Division_Total" in gen.await_args_list[1].args[0] and r.route == "llm"


def test_empty_result_gets_one_repair(db, router_index, model_path):
    empty = GOOD_SQL.replace("LT-2(b)(ii)", "LT-2(b)(zz)")
    gen = AsyncMock(side_effect=[plan(empty, "x"), plan(GOOD_SQL, "x")])
    with patch("app.pipeline.generate_query_plan", gen), patch("app.pipeline.verify_answer", AsyncMock(return_value=Verdict(True))):
        r = ask(db, router_index, SLAB_Q)
    assert gen.await_count == 2 and "returned no rows" in gen.await_args_list[1].args[0] and len(r.rows) == slab_years(db)


def test_reviewer_rejects_twice_nothing_shown(db, router_index, model_path):
    gen = AsyncMock(side_effect=[plan(GOOD_SQL, "x"), plan(GOOD_SQL, "x")])
    no = Verdict(False, "the SQL is for the wrong year", "wrong_period")
    with patch("app.pipeline.generate_query_plan", gen), patch("app.pipeline.verify_answer", AsyncMock(return_value=no)):
        r = ask(db, router_index, SLAB_Q + " in 2016-17")
    assert r.route == "llm_unverified" and not r.rows and r.chart_data is None


def test_reviewer_false_objection_is_vetoed(db, router_index, model_path):
    sql = GOOD_SQL.replace("GROUP BY", "AND f.fiscal_year = '2016-17' GROUP BY")
    no = Verdict(False, "wrong year", "wrong_period")
    with patch("app.pipeline.generate_query_plan", AsyncMock(return_value=plan(sql, "Demand was {demand}."))), \
         patch("app.pipeline.verify_answer", AsyncMock(return_value=no)):
        r = ask(db, router_index, "LT-2(b)(ii) demand in 2016-17")
    assert r.route == "llm" and "overruled" in r.review_notes[0]


def test_reviewer_unreachable_fails_open(db, router_index, model_path):
    with patch("app.pipeline.generate_query_plan", AsyncMock(return_value=plan(GOOD_SQL, "x"))), \
         patch("app.pipeline.verify_answer", AsyncMock(side_effect=LLMUnavailable("down"))):
        r = ask(db, router_index, SLAB_Q)
    assert r.route == "llm" and r.verification == "skipped"


def test_declined_plan_uses_safe_wording(db, router_index, model_path):
    with patch("app.pipeline.generate_query_plan", AsyncMock(return_value=plan("", answerable=False,
                                                                                message="The fact_dcb table lacks it."))):
        r = ask(db, router_index, SLAB_Q)
    assert not r.answerable and "fact_dcb" not in r.answer


# ---------------------------------------------------------------------------------------------- API
@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_health_and_headers(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["data_to"] == "2025-26"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    assert client.get("/docs").status_code == 404


def test_bad_bodies(client):
    assert client.post("/api/ask_once", json={"question": "x" * 5000}).status_code == 400
    big = json.dumps({"question": "a" * 20000})
    assert client.post("/api/ask_once", content=big, headers={"content-type": "application/json"}).status_code == 413


def test_sse_stream_matches_json_answer(client):
    q = "What was the collection efficiency in Arkalgud in 2024-25?"
    once = client.post("/api/ask_once", json={"question": q}).json()
    assert once["sql"] == "" and once["timing_ms"] == {} and once["report_id"]
    text, stages, done = "", [], None
    with client.stream("POST", "/api/ask", json={"question": q}) as resp:
        event = None
        for line in resp.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if event == "answer_chunk":
                    text += data["text"]
                elif event == "status":
                    stages.append(data["stage"])
                elif event == "done":
                    done = data
    assert text == once["answer"] and "understanding" in stages and stages[-1] == "answering" and done["report_id"]


def test_answer_report_download(client):
    rid = client.post("/api/ask_once", json={"question": "which sub-division collects the best"}).json()["report_id"]
    r = client.get(f"/api/answer-report/{rid}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert {"Report", "Data", "Chart", "Notes"} <= set(wb.sheetnames)
    assert all(row[1] == "PASS" for row in wb["Report"].iter_rows(values_only=True) if row[1] in ("PASS", "FAIL"))
    assert client.get("/api/answer-report/not-a-real-id-123").status_code == 404


def test_mis_report_login_gated(client):
    assert client.get("/api/mis-report").status_code == 401
    client.post("/api/login", json={"username": "tester", "password": ""})
    r = client.get("/api/mis-report?subdivision=Arkalgud&fy=2024-25")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames[:2] == ["Summary", "Year trend"] and "Tariff slabs" in wb.sheetnames
    assert wb.properties.title.startswith("DRAFT")
    assert client.get("/api/mis-report?subdivision=Belur").status_code == 404
    assert client.get("/api/mis-report?fy=2030-31").status_code == 404
    whole = openpyxl.load_workbook(io.BytesIO(client.get("/api/mis-report").content))
    results = [row[1] for row in whole["Summary"].iter_rows(values_only=True) if row[1] in ("PASS", "FAIL")]
    assert results and set(results) == {"PASS"} and "Sub-divisions" in whole.sheetnames


def test_rate_limit():
    from app.protection import Protection

    async def ok_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    c = TestClient(Protection(ok_app, per_minute=3))          # no lifespan for this stub app
    codes = [c.post("/api/ask_once", json={}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


# ---------------------------------------------------------------------------------------------- report guardrails
def test_finalize_stores_formula_text_as_text_and_withholds_on_failure():
    wb = openpyxl.Workbook()
    wb.active["A1"] = "=HYPERLINK(\"http://x\")"
    finalize(wb, [("ok", True, "")], "t")
    assert wb.active["A1"].data_type == "s" and wb.active.oddFooter.center.text.startswith("Proof of concept")
    with pytest.raises(ReportUnderReview):
        finalize(openpyxl.Workbook(), [("adds up", False, "off by 5")], "t")


def test_answer_report_withheld_when_headline_not_in_data():
    rid = answer_report.keep("q", "**Demand was ₹999.99 crore.**", "llm", ["demand_total"], [(1.0,), (2.0,)], False,
                             None, None, "v1")
    with pytest.raises(ReportUnderReview):
        answer_report.build_answer_report(answer_report.get(rid, "v1"))
    assert answer_report.get(rid, "v2") is None          # a new data version expires kept answers


# ---------------------------------------------------------------------------------------------- integrity
def test_inputs_unchanged():
    """The app only reads its inputs: the locked star schema, its semantic layer and examples, the mart and its
    layer must hash exactly as recorded when DCB_Mart/build_mart.py last ran. Run after the other tests."""
    manifest = json.loads(INTEGRITY_MANIFEST_PATH.read_text(encoding="utf-8"))
    for rel, digest in manifest["files"].items():
        h = hashlib.sha256((PROJECT_ROOT / rel).read_bytes()).hexdigest()
        assert h == digest, f"{rel} changed since the manifest was written"


def test_db_opened_read_only(db):
    assert db._conn.execute("PRAGMA query_only").fetchone()[0] == 1
    assert settings.db_path.name == "dcb_star_schema.sqlite"
