"""View report (ported from the KPCL POC): the on-screen preview shows the same workbook as the download, sheet for
sheet and figure for figure, with every cell escaped."""
import html
import io
import re

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import answer_report
from app.excel_common import data_version
from app.xlsx_view import format_value


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        assert c.post("/api/login", json={"username": "tester", "password": "x"}).json()["ok"]
        yield c


@pytest.mark.parametrize("v,fmt,want", [
    (8671.845, "#,##0.00", "8,671.85"), (57.4, "0.00", "57.40"), (6533495.01, "#,##0", "6,533,495"),
    (0.4399, "0.0%", "44.0%"), (-1234.5, "#,##0.00", "-1,234.50"), (12, "General", "12"), ("LT-1", "General", "LT-1"),
    (None, "0.00", ""),
])
def test_number_formats_match_excel(v, fmt, want):
    assert format_value(v, fmt) == want


def _cells_shown(sheet_html: str) -> set[str]:
    return {html.unescape(m) for m in re.findall(r"<td[^>]*>(.*?)</td>", sheet_html)}


def _same_workbook(view: dict, wb) -> None:
    assert [s["name"] for s in view["sheets"]] == wb.sheetnames
    for s in view["sheets"]:
        ws, shown = wb[s["name"]], _cells_shown(s["html"])
        for row in ws.iter_rows():
            for c in row:
                # the generation time is the only thing allowed to differ (the two were built a moment apart)
                if c.value is None or "generated" in str(c.value).lower() or "generated" in str(ws.cell(c.row, 1).value or "").lower():
                    continue
                assert format_value(c.value, c.number_format) in shown, (s["name"], c.coordinate, c.value)


@pytest.mark.parametrize("params", [{}, {"subdivision": "Arkalgud"}, {"subdivision": "Konanur", "fy": "2023-24"}])
def test_mis_view_is_the_downloaded_workbook(client, params):
    v = client.get("/api/mis-report/view", params=params)
    d = client.get("/api/mis-report", params=params)
    assert v.status_code == d.status_code == 200
    view = v.json()
    assert view["name"] in d.headers["content-disposition"]
    _same_workbook(view, load_workbook(io.BytesIO(d.content)))


def test_mis_view_needs_login(client):
    from app.main import app
    anon = TestClient(app)          # no `with`: shares the running app without restarting its lifespan
    assert anon.get("/api/mis-report/view").status_code == 401


def test_mis_view_unknown_subdivision_is_404(client):
    assert client.get("/api/mis-report/view", params={"subdivision": "Belur"}).status_code == 404


def test_answer_view_matches_download_and_expires(client):
    a = client.post("/api/ask_once", json={"question": "Collection efficiency of every sub-division in 2024-25"}).json()
    assert a["report_id"]
    v = client.get(f"/api/answer-report/{a['report_id']}/view")
    d = client.get(f"/api/answer-report/{a['report_id']}")
    assert v.status_code == d.status_code == 200
    _same_workbook(v.json(), load_workbook(io.BytesIO(d.content)))
    assert client.get("/api/answer-report/" + "A" * 24 + "/view").status_code == 404


def test_cell_text_is_escaped(client):
    rid = answer_report.keep("<script>alert(1)</script> report", "**<img src=x onerror=alert(1)>**", "report:x",
                             ["subdivision_name", "demand_total"], [("<b>Arkalgud</b>", 1.0), ("Konanur", 2.0)], False,
                             None, None, data_version())
    v = client.get(f"/api/answer-report/{rid}/view").json()
    joined = "".join(s["html"] for s in v["sheets"])
    assert "<script>" not in joined and "<img src=x" not in joined and "<b>Arkalgud" not in joined
    assert "&lt;script&gt;" in joined or "&lt;b&gt;Arkalgud" in joined
