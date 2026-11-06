"""
Shared pieces of the Excel reports (app/mis_report.py, app/answer_report.py), with the report guardrails ported from
the KPCL POC:

  * finalize(wb, checks) runs on every workbook before it is handed out:
      - no formulas: any text starting with = + - @ is stored as text with Excel's quote prefix (openpyxl would
        otherwise write "=..." as a live formula);
      - a "Proof of concept – not for official use" footer on every printed sheet, and DRAFT file properties;
      - the integrity checks travel with the workbook (a Checks block on the first sheet);
      - a workbook that fails ANY check is withheld: ReportUnderReview -> HTTP 409 'under_review'.
  * data_version(): a short hash of the mart file; kept answers expire when the data changes.
  * CESC palette (sampled from dashboard.html) and number formats per unit.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
from functools import lru_cache

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app.answer import col_unit
from app.config import settings

NAVY, NAVY_DEEP, NAVY_SOFT, RED, BORDER, GOOD, WARN_BG = "1A2F5C", "10203F", "E8ECF3", "C0202C", "D7DCE4", "2F9E58", "FFF6E6"
ORG = "CESC — Chamundeshwari Electricity Supply Corporation Limited, Mysore"
DIVISION = "Holenarasipura Division · Demand-Collection-Balance (DCB)"
FOOTER = "Proof of concept – not for official use"
_THIN = Side(style="thin", color=BORDER)
BOX = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
HEAD_FONT = Font(bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor=NAVY)
SOFT_FILL = PatternFill("solid", fgColor=NAVY_SOFT)
TOTAL_FILL = PatternFill("solid", fgColor=WARN_BG)
NUMBER_FORMATS = {"Rs": "#,##0.00", "%": "0.00", "count": "#,##0", "units": "#,##0", "kW": "#,##0.00", "HP": "#,##0.00",
                  "kVA": "#,##0.00", "ratio": "0.00", "Rs/unit": "0.00", "paise/unit": "0.0"}


class ReportUnderReview(Exception):
    """A workbook failed one of its own integrity checks and is withheld."""


@lru_cache(maxsize=4)
def _hash_file(path: str, mtime: float) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def data_version() -> str:
    p = settings.mart_db_path
    return _hash_file(str(p), p.stat().st_mtime)


def number_format(col: str) -> str | None:
    return NUMBER_FORMATS.get(col_unit(col) or "")


def letterhead(ws, title: str, subtitle: str, width: int = 8) -> int:
    """Rows 1-5: organisation, division, report title, scope line, draft line. Returns the next free row."""
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=width)
    ws.cell(1, 1, ORG).font = Font(bold=True, size=13, color="FFFFFF")
    for c in range(1, width + 1):
        ws.cell(1, c).fill = PatternFill("solid", fgColor=NAVY_DEEP)
    ws.cell(2, 1, DIVISION).font = Font(bold=True, size=11, color=NAVY)
    ws.cell(3, 1, title).font = Font(bold=True, size=15, color=RED)
    ws.cell(4, 1, subtitle).font = Font(italic=True, color="4B5563")
    ws.cell(5, 1, f"DRAFT · {FOOTER} · generated {dt.datetime.now():%d-%b-%Y %H:%M} · data version {data_version()}").font = \
        Font(size=9, color="8A5A00")
    return 7


def header_row(ws, row: int, headers: list[str], col: int = 1) -> None:
    for i, h in enumerate(headers):
        c = ws.cell(row, col + i, h)
        c.font, c.fill, c.border = HEAD_FONT, HEAD_FILL, BOX
        c.alignment = Alignment(wrap_text=True, vertical="center")


def data_rows(ws, start: int, keys: list[str], rows: list, col: int = 1, total: bool = False) -> int:
    r = start
    for row in rows:
        for i, (k, v) in enumerate(zip(keys, row)):
            c = ws.cell(r, col + i, v)
            c.border = BOX
            fmt = number_format(k) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
            if fmt:
                c.number_format = fmt
        r += 1
    if total:
        for i in range(len(keys)):
            ws.cell(r - 1, col + i).font = Font(bold=True)
            ws.cell(r - 1, col + i).fill = TOTAL_FILL
    return r


def widths(ws, values: dict[str, float]) -> None:
    for letter, w in values.items():
        ws.column_dimensions[letter].width = w


def checks_block(ws, row: int, checks: list[tuple[str, bool, str]], col: int = 1) -> int:
    ws.cell(row, col, "Integrity checks").font = Font(bold=True, color=NAVY, size=12)
    header_row(ws, row + 1, ["Check", "Result", "Detail"], col)
    r = row + 2
    for name, ok, detail in checks:
        ws.cell(r, col, name).border = BOX
        c = ws.cell(r, col + 1, "PASS" if ok else "FAIL")
        c.border = BOX
        c.font = Font(bold=True, color=GOOD if ok else RED)
        ws.cell(r, col + 2, detail).border = BOX
        r += 1
    return r + 1


def finalize(wb: Workbook, checks: list[tuple[str, bool, str]], title: str) -> Workbook:
    """The safety pass every workbook gets, downloaded or not. Raises ReportUnderReview if a check failed."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value[:1] in ("=", "+", "-", "@"):
                    c.value = c.value
                    c.data_type = "s"
                    c.quotePrefix = True
        ws.oddFooter.center.text = FOOTER
        ws.oddFooter.right.text = "Page &P of &N"
        ws.evenFooter.center.text = FOOTER
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToHeight = 0
    wb.properties.title = f"DRAFT — {title}"
    wb.properties.subject = FOOTER
    wb.properties.keywords = "DRAFT; proof of concept; CESC DCB"
    wb.properties.creator = "CESC DCB assistant (proof of concept)"
    failed = [f"{n}: {d}" for n, ok, d in checks if not ok]
    if failed:
        raise ReportUnderReview("; ".join(failed))
    return wb


def to_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def close(a, b, rel: float = 0.01, abs_: float = 2.0) -> bool:
    a, b = a or 0.0, b or 0.0
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))
