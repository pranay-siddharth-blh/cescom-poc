"""
Chat answer -> Excel report (GET /api/answer-report/{report_id}; pattern ported from the KPCL POC).

Every answer computed from the data gets a random report_id. The server keeps the answer in memory (3 hours, at most
500 kept, forgotten when the data version changes) and builds the file from what it kept -- never from anything the
page sends back. The workbook has a Report sheet (question, the answer as given, checks), a Data sheet (every row, with
a total row where the measure adds up), a Chart sheet (a native Excel chart when the answer had one) and Notes.

Checks: the Data sheet has every row of the answer, the total row equals the sum of the rows, and the headline figure
stated in the answer is traceable to the data (a value, a column total, or its crore / lakh form). A failed check
withholds the workbook (ReportUnderReview -> HTTP 409), and the chat says it is under review.
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from app.answer import adds_up_over_places, adds_up_over_years, header, to_float
from app.excel_common import (NAVY, ReportUnderReview, checks_block, close, data_rows, finalize, header_row, letterhead,
                              to_bytes, widths)

KEEP_SECONDS = 3 * 60 * 60
KEEP_MAX = 500
_NO_REPORT_ROUTES = ("greeting", "identity", "glossary", "capability", "unknown_name", "out_of_range")
_NUM_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


@dataclass
class Kept:
    question: str
    answer: str
    route: str
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    chart: dict | None
    total: tuple | None
    version: str
    created: float = field(default_factory=time.time)


_KEPT: dict[str, Kept] = {}


def wants_report(route: str, answerable: bool, columns: list[str], rows: list) -> bool:
    return bool(answerable and columns and rows and not route.startswith(("refused", "llm_unverified"))
                and route not in _NO_REPORT_ROUTES)


def _purge() -> None:
    now = time.time()
    for k in [k for k, v in _KEPT.items() if now - v.created > KEEP_SECONDS]:
        del _KEPT[k]
    while len(_KEPT) >= KEEP_MAX:
        del _KEPT[min(_KEPT, key=lambda k: _KEPT[k].created)]


def keep(question: str, answer: str, route: str, columns: list[str], rows: list, truncated: bool, chart: dict | None,
         total: tuple | None, version: str) -> str:
    _purge()
    rid = secrets.token_urlsafe(16)
    _KEPT[rid] = Kept(question, answer, route, list(columns), [tuple(r) for r in rows], truncated, chart, total, version)
    return rid


def get(report_id: str, version: str) -> Kept | None:
    _purge()
    k = _KEPT.get(report_id or "")
    if k is None or k.version != version:
        return None
    return k


def _plain(text: str) -> list[str]:
    return [re.sub(r"\*\*", "", line).strip() for line in (text or "").split("\n") if line.strip()]


def _headline_supported(answer: str, question: str, columns: list[str], rows: list) -> tuple[bool, str]:
    """The first figure of the answer's first sentence must come from the data (value, total, crore / lakh form)."""
    first = _plain(answer)[0] if _plain(answer) else ""
    first = re.sub(r"\b(19|20)\d{2}\s*-\s*\d{2}\b|\b(19|20)\d{2}\b", " ", first)
    known = []
    for i in range(len(columns)):
        vals = [to_float(r[i]) for r in rows if not isinstance(r[i], str)]
        vals = [v for v in vals if v is not None]
        known += vals + ([sum(vals)] if vals else [])
    known += [k / 1e7 for k in known] + [k / 1e5 for k in known]
    for tok in _NUM_RE.findall(first):
        x = to_float(tok)
        if x is None or (abs(x) < 10 and float(x).is_integer()):
            continue
        ok = any(abs(x - k) <= max(abs(k) * 0.005, 0.006) for k in known)
        return ok, f"headline figure {tok}" + ("" if ok else " not found in the data")
    return True, "no figure in the headline"


def _summable(col: str, rows: list, ci: int, time_series: bool) -> bool:
    if not all(isinstance(r[ci], (int, float)) or r[ci] is None for r in rows):
        return False
    return adds_up_over_years(col) if time_series else adds_up_over_places(col)


def build_answer_report(kept: Kept, as_workbook: bool = False):
    cols, rows = kept.columns, kept.rows
    checks = []
    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    r = letterhead(ws, "DCB assistant — answer report", "An answer from the chat, with the data it was computed from", 4)
    ws.cell(r, 1, "Question").font = Font(bold=True, color=NAVY)
    ws.cell(r, 2, kept.question).alignment = Alignment(wrap_text=True)
    r += 2
    ws.cell(r, 1, "Answer (as given)").font = Font(bold=True, color=NAVY)
    r += 1
    for line in _plain(kept.answer):
        ws.cell(r, 1, line).alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        ws.row_dimensions[r].height = 15 * max(1, len(line) // 110 + 1)
        r += 1
    if kept.truncated:
        ws.cell(r, 1, "INCOMPLETE RESULT: only the first rows were returned.").font = Font(bold=True, color="C0202C")
        r += 1

    ds = wb.create_sheet("Data")
    rr = letterhead(ds, "Data", kept.question, max(4, len(cols)))
    header_row(ds, rr, [header(c) for c in cols])
    end = data_rows(ds, rr + 1, cols, rows)
    checks.append(("The Data sheet has every row of the answer", end - rr - 1 == len(rows), f"{len(rows)} rows"))
    time_series = bool(cols) and cols[0] == "fiscal_year"
    total_cells = []
    for ci, c in enumerate(cols):
        if ci and _summable(c, rows, ci, time_series) and len(rows) > 1:
            total_cells.append((ci, sum(to_float(x[ci]) or 0 for x in rows)))
    if total_cells:
        tot = ["Total"] + [None] * (len(cols) - 1)
        for ci, v in total_cells:
            tot[ci] = v
        data_rows(ds, end, cols, [tuple(tot)], total=True)
        if kept.total and len(kept.total) >= 2 and isinstance(kept.total[-1], (int, float)):
            want = kept.total[-1]
            got = next((v for ci, v in total_cells if close(v, want)), None)
            checks.append(("The total row equals the report's own total", got is not None, f"{want:,.2f}"))
    widths(ds, {get_column_letter(i + 1): (40 if i < 2 and any(isinstance(x[i], str) for x in rows) else 20) for i in range(len(cols))})

    ok, detail = _headline_supported(kept.answer, kept.question, cols, rows)
    checks.append(("The figure stated in the answer comes from the data", ok, detail))

    ch = kept.chart
    if ch and ch.get("labels") and len(rows) > 1:
        cs = wb.create_sheet("Chart")
        cs.cell(1, 1, "Label").font = Font(bold=True)
        series = ch.get("series") or [{"label": ch.get("series_label") or "Value", "values": ch["values"]}]
        for j, s in enumerate(series):
            cs.cell(1, 2 + j, s["label"]).font = Font(bold=True)
        for i, lab in enumerate(ch["labels"]):
            cs.cell(2 + i, 1, lab)
            for j, s in enumerate(series):
                cs.cell(2 + i, 2 + j, s["values"][i] if i < len(s["values"]) else None)
        chart = LineChart() if ch.get("type") == "line" else BarChart()
        chart.title, chart.height, chart.width = ch.get("series_label") or "", 9, 22
        chart.add_data(Reference(cs, min_col=2, max_col=1 + len(series), min_row=1, max_row=1 + len(ch["labels"])),
                       titles_from_data=True)
        chart.set_categories(Reference(cs, min_col=1, min_row=2, max_row=1 + len(ch["labels"])))
        cs.add_chart(chart, "E2")
        widths(cs, {"A": 30, "B": 20, "C": 20})

    ns = wb.create_sheet("Notes")
    rn = letterhead(ns, "Notes", "How this answer was produced", 2)
    route_note = {"report": "a reviewed, fixed report over the checked DCB mart",
                  "metric": "the metric registry's reviewed formula over the checked DCB mart",
                  "verified": "a reviewed worked example over the checked DCB mart",
                  "slab": "the slab resolver (exact tariff slabs, listed in the answer) and the registry's reviewed "
                          "formulas over the checked DCB mart",
                  "llm": "SQL written by the language model, checked by the SQL guards and a second reviewer pass"}
    how = next((v for k, v in route_note.items() if kept.route.startswith(k)), kept.route)
    for i, (a, b) in enumerate([("Computed with", how), ("Amounts", "Rupees; the chat shows crore / lakh, the Data sheet the exact amounts."),
                                ("Status", "Proof of concept – not for official use.")]):
        ns.cell(rn + i, 1, a).font = Font(bold=True)
        ns.cell(rn + i, 2, b)
    widths(ns, {"A": 18, "B": 110})

    r += 1
    checks_block(ws, r, checks)
    widths(ws, {"A": 60, "B": 60, "C": 50, "D": 20})
    finalize(wb, checks, f"DCB answer report — {kept.question[:60]}")
    name = "DCB_answer_" + re.sub(r"[^A-Za-z0-9]+", "_", kept.question)[:50].strip("_") + ".xlsx"
    return (wb, name) if as_workbook else (to_bytes(wb), name)


__all__ = ["keep", "get", "wants_report", "build_answer_report", "ReportUnderReview"]
