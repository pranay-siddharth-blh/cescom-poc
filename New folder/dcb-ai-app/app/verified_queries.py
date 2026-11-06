"""
Verified query library: the reviewed SQL of DCB_Mart/example_queries.sql plus a deterministic writer (pattern
ported from the KPCL POC). Even at temperature 0 a model can return different SQL for the same question; a
verified answer is identical every time.

Matching is deliberately conservative: the normalised question must be very close to the registered phrasing
(difflib ratio >= 0.9) AND carry exactly the same entity tokens (sub-divisions, tariff families and slabs, fiscal
years) AND ask in the same direction (highest vs lowest). "Konanur" never matches the Arkalgud entry; "2023-24"
never matches "2024-25".
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from functools import lru_cache

from app import router
from app.answer import build_chart, narrate
from app.config import MART_EXAMPLES_PATH

_CHARTS = {  # example no -> (type, label column, value column, series label)
    1: ("bar", "subdivision_name", "collection_efficiency_pct", "Collection efficiency %"),
    2: ("line", "fiscal_year", "demand_total", "Demand (₹)"),
    3: ("bar", "family_code", "demand_total", "Demand (₹)"),
    4: ("line", "fiscal_year", "demand_total", "Demand (₹)"),
    5: ("line", "fiscal_year", "lt1_demand", "LT-1 demand (₹)"),
    8: ("line", "fiscal_year", "dc_mnr_installations", "DC/MNR installations"),
    9: ("bar", "subdivision_name", "closing_balance_total", "Closing balance (₹)"),
    12: ("bar", "subdivision_name", "change_pct", "Change in collection %"),
    13: ("bar", "subdivision_name", "collection_efficiency_pct", "Collection efficiency %"),
    14: ("line", "fiscal_year", "demand_per_unit_rs", "Demand per unit (₹)"),
}
_PREFERRED = {1: "collection_efficiency_pct", 2: "demand_total", 3: "demand_total", 4: "demand_total", 5: "lt1_demand",
              9: "closing_balance_total", 12: "change_pct", 13: "collection_efficiency_pct", 14: "demand_per_unit_rs"}


@dataclass(frozen=True)
class VerifiedQuery:
    key: str
    example_no: int
    question: str
    sql: str

    @property
    def chart(self):
        return _CHARTS.get(self.example_no)


@lru_cache(maxsize=1)
def library() -> list[VerifiedQuery]:
    text = MART_EXAMPLES_PATH.read_text(encoding="utf-8")
    out = []
    for block in re.split(r"\n(?=-- \d+\. )", text):
        m = re.match(r"-- (\d+)\. (.*)\n", block)
        if m:
            sql = block[m.end():].strip().rstrip(";").strip()
            out.append(VerifiedQuery(f"mart_example_{int(m[1]):02d}", int(m[1]), m[2].strip(), sql))
    return out


def examples_text() -> str:
    return MART_EXAMPLES_PATH.read_text(encoding="utf-8")


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9()\- ]+", " ", (s or "").lower()).split())


def _tokens(e: dict) -> tuple:
    return (tuple(sorted(e.get("subdivisions", []))), tuple(sorted(e.get("families", []))),
            tuple(sorted(e.get("slab_codes", []))), tuple(sorted(e.get("fiscal_years", []))), e.get("direction"),
            e.get("voltage"), bool(e.get("scope")))


def match(question: str) -> VerifiedQuery | None:
    q = _norm(question)
    qe = router.extract(question)
    best, best_ratio = None, 0.0
    for vq in library():
        ratio = difflib.SequenceMatcher(None, q, _norm(vq.question)).ratio()
        if ratio > best_ratio:
            best, best_ratio = vq, ratio
    if best is None or best_ratio < 0.9:
        return None
    if _tokens(qe) != _tokens(router.extract(best.question)):
        return None
    return best


def render(vq: VerifiedQuery, columns: list[str], rows: list[tuple]) -> tuple[str, dict | None]:
    if not rows:
        return "**No matching figures were found for that question.**", None
    head = f"**{vq.question}.**"
    body = narrate(columns, rows, _PREFERRED.get(vq.example_no)) if len(rows) > 1 else ""
    if len(rows) == 1:
        from app.answer import fmt_value, header
        body = "\n".join(f"•  {header(c)}: {fmt_value(c, v, exact=True)}" for c, v in zip(columns, rows[0]))
    chart = None
    if vq.chart:
        t, lab, val, series = vq.chart
        extra = [("ht2_demand", "HT-2 demand (₹)")] if vq.example_no == 5 else None
        chart = build_chart(t, lab, val, series, columns, rows, extra)
    return "\n\n".join(p for p in (head, body) if p), chart
