"""
Insights page (GET /insights): a handful of fixed, hand-written aggregate
queries against the same locked star-schema DB the chat pipeline and
dashboard already use, via app.db.ReadOnlyDB.run -- the one persistent
connection (design point #3), never a second connection of our own. Not
LLM-generated SQL, so app.sql_safety's guard doesn't apply here -- these
are fixed, hand-reviewed queries written once, same as
app.dashboard_data (see that module's own docstring for the rules this
still follows: fact_dcb vs fact_dcb_rollups never summed together,
division-wide means subdivision_type='division', efficiency recomputed
from summed totals, never from the stored pct_ column).

Every "highlight" below is computed in Python from a small, fully-fetched
result set (at most a few hundred rows -- one row per tariff family per
fiscal_year, or per sub-division per fiscal_year), not in SQL itself:
SQLite has no clean pivot/window-over-groups for "biggest % change across
a handful of columns", and the row counts here are small enough that
fetch-then-compute in Python is simpler and just as fast as a more
elaborate single query would be.

Tariff-category totals (2026-10-01, second pass) read the mart's
booklet-category table (mart_fact_booklet_division_year): the 22
categories of the CESC tariff booklet FY2025-26 to FY2027-28, with the
sheets' old codes mapped to today's categories, so a category is
comparable across all 11 years -- the printed family subtotals read
before moved with the 2024-25 restructuring (domestic LT-2(a) became
LT-1, KI-01). Never a naive fact_dcb GROUP BY on tariff text (CLAUDE.md
11.2). The page also shows the latest year's subsidy (collection by
adjustment) and AT&C loss.
"""
from __future__ import annotations

from app.db import ReadOnlyDB

# Tariff categories as the CESC tariff booklet FY2025-26 to FY2027-28 names them (LT-1, LT-4(a), HT-2(b) ...), from the
# mart's booklet-category table (DCB_Mart/build_mart.py: line items mapped to today's categories, checked to add up to
# the LT + HT families every year). The printed family subtotals this used to read move with the 2024-25 tariff
# restructuring (domestic LT-2(a) became LT-1, KI-01), which made LT-1 look like the biggest demand "jump" when it was
# a renumbering; on the booklet basis every category is comparable across all 11 years.
_ALL_YEARS_BY_TARIFF_FAMILY_SQL = """
    SELECT b.booklet_code AS tariff_code, b.booklet_name AS tariff_name, b.fiscal_year AS fiscal_year,
           d.start_year AS start_year, 'combined_total' AS rollup_type, b.demand_total AS demand
    FROM mart_fact_booklet_division_year b
    JOIN mart_dim_fiscal_year d ON d.fiscal_year = b.fiscal_year
"""

# Latest year, per booklet category: the share of collection booked as adjustment (the Government subsidy for LT-1
# Gruha Jyothi and LT-4(a) IP sets) and the division-wide subsidy total.
_LATEST_SUBSIDY_BY_CATEGORY_SQL = """
    SELECT booklet_code, booklet_name, collection_adjust_total, collection_grand_total, adjustment_share_pct
    FROM mart_fact_booklet_division_year
    WHERE fiscal_year = (SELECT MAX(fiscal_year) FROM mart_dim_fiscal_year) AND collection_grand_total > 0
"""
_LATEST_DIVISION_SQL = """
    SELECT fiscal_year, collection_adjust_total, collection_grand_total, adjustment_share_pct, atc_loss_pct,
           billing_efficiency_pct, collection_efficiency_pct
    FROM mart_fact_division_year WHERE fiscal_year = (SELECT MAX(fiscal_year) FROM mart_dim_fiscal_year)
"""
_LATEST_ATC_BY_SUBDIVISION_SQL = """
    SELECT subdivision_name, atc_loss_pct, billing_efficiency_pct, collection_efficiency_pct
    FROM mart_fact_subdivision_year
    WHERE fiscal_year = (SELECT MAX(fiscal_year) FROM mart_dim_fiscal_year) AND atc_loss_pct IS NOT NULL
"""

# Real sub-divisions/sections only (never Division_Total or the
# administrative Division_Office), latest fiscal year only, straight from
# each one's own printed grand_total rollup row -- rule 1 (never SUM
# fact_dcb and fact_dcb_rollups together) and rule 5 (recompute
# efficiency from summed totals) both apply here exactly as in
# app.dashboard_data.
_LATEST_EFFICIENCY_BY_SUBDIVISION_SQL = """
    SELECT s.subdivision_name AS subdivision_name, r.demand_total AS demand_total,
           r.collection_grand_total AS collection_grand_total
    FROM fact_dcb_rollups r
    JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
    WHERE r.rollup_type = 'grand_total' AND s.subdivision_type IN ('sub_division', 'section')
      AND r.fiscal_year = (SELECT MAX(fiscal_year) FROM fact_dcb)
      AND r.demand_total IS NOT NULL AND r.demand_total > 0
"""

# The two most recent fiscal years, real sub-divisions/sections only --
# a sub-division only appearing in one of the two (e.g. Saligrama, closed
# long before either of these years) naturally can't have a "growth"
# figure computed and is excluded, without needing a separate is_active
# check.
_LAST_TWO_YEARS_OUTSTANDING_BY_SUBDIVISION_SQL = """
    SELECT s.subdivision_name AS subdivision_name, r.fiscal_year AS fiscal_year, d.start_year AS start_year,
           (r.demand_total - COALESCE(r.collection_grand_total, 0)) AS outstanding
    FROM fact_dcb_rollups r
    JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
    JOIN dim_fiscal_year d ON r.fiscal_year = d.fiscal_year
    WHERE r.rollup_type = 'grand_total' AND s.subdivision_type IN ('sub_division', 'section')
      AND r.fiscal_year IN (SELECT fiscal_year FROM dim_fiscal_year ORDER BY start_year DESC LIMIT 2)
"""


def _rows_as_dicts(query_result) -> list[dict]:
    return [dict(zip(query_result.columns, row)) for row in query_result.rows]


_NAMES: dict[str, str] = {}


def _pivot_by_code_and_year(rows: list[dict]) -> dict[str, dict[int, float]]:
    """Groups rollup rows by (tariff_code, start_year), preferring a
    'combined_total' row over a 'sub_total' one when a family has both
    for the same year (2025-26 only, so far) -- never adds them together,
    see this module's own top-of-file comment for why."""
    by_code: dict[str, dict[int, tuple[str, float]]] = {}
    for r in rows:
        year_map = by_code.setdefault(r["tariff_code"], {})
        existing = year_map.get(r["start_year"])
        if existing is None or r["rollup_type"] == "combined_total":
            year_map[r["start_year"]] = (r["rollup_type"], r["demand"] or 0.0)
        if r.get("tariff_name"):
            _NAMES[r["tariff_code"]] = r["tariff_name"]
    return {code: {year: demand for year, (_, demand) in year_map.items()} for code, year_map in by_code.items()}


def _fastest_growing_tariff_3yr(by_code: dict[str, dict[int, float]], recent_start_years: set[int]) -> dict | None:
    best = None
    for code, by_year in by_code.items():
        years = sorted(y for y in by_year if y in recent_start_years)
        if len(years) < 2:
            continue
        first, last = by_year[years[0]], by_year[years[-1]]
        if first < 10 * 100_000:
            continue  # a % change from a near-zero base (under Rs 10 lakh, e.g. a handful of EV chargers) is not a real "fastest growing"
        pct = (last - first) / first * 100
        if best is None or pct > best["pct_change"]:
            best = {"tariff_code": code, "tariff_name": _NAMES.get(code, ""), "pct_change": round(pct, 1),
                    "from_demand": first, "to_demand": last}
    return best


def _largest_yoy_jump(by_code: dict[str, dict[int, float]]) -> dict | None:
    best = None
    for code, by_year in by_code.items():
        years = sorted(by_year)
        for prev_year, this_year in zip(years, years[1:]):
            if prev_year + 1 != this_year:
                continue  # not actually consecutive (a code with a gap year)
            jump = by_year[this_year] - by_year[prev_year]
            if best is None or jump > best["jump"]:
                best = {
                    "tariff_code": code, "tariff_name": _NAMES.get(code, ""), "jump": round(jump, 2),
                    "pct_change": round(jump / by_year[prev_year] * 100, 1) if by_year[prev_year] else None,
                    "from_fiscal_year_start": prev_year, "to_fiscal_year_start": this_year,
                    "from_demand": by_year[prev_year], "to_demand": by_year[this_year],
                }
    return best


def _best_worst_efficiency(rows: list[dict]) -> tuple[dict | None, dict | None]:
    scored = []
    for r in rows:
        demand = r["demand_total"] or 0.0
        if demand <= 0:
            continue
        pct = round((r["collection_grand_total"] or 0) / demand * 100, 1)
        scored.append({"subdivision_name": r["subdivision_name"], "efficiency_pct": pct})
    if not scored:
        return None, None
    scored.sort(key=lambda x: x["efficiency_pct"])
    return scored[-1], scored[0]  # best (highest), worst (lowest)


def _highest_outstanding_growth(rows: list[dict]) -> dict | None:
    by_name: dict[str, dict[int, float]] = {}
    for r in rows:
        by_name.setdefault(r["subdivision_name"], {})[r["start_year"]] = r["outstanding"] or 0.0
    best = None
    for name, by_year in by_name.items():
        years = sorted(by_year)
        if len(years) < 2:
            continue
        prev, curr = by_year[years[0]], by_year[years[1]]
        if prev == 0:
            continue  # % growth from a zero base is meaningless (and rare/real, e.g. a just-closed sub-division)
        pct = (curr - prev) / abs(prev) * 100
        if best is None or pct > best["pct_change"]:
            best = {
                "subdivision_name": name, "pct_change": round(pct, 1),
                "from_outstanding": prev, "to_outstanding": curr,
            }
    return best


async def build_insights_summary(db: ReadOnlyDB, max_rows: int, timeout_seconds: float) -> dict:
    tariff_rows = _rows_as_dicts(
        await db.run(_ALL_YEARS_BY_TARIFF_FAMILY_SQL, max_rows=max(max_rows, 500), timeout_seconds=timeout_seconds)
    )
    by_code = _pivot_by_code_and_year(tariff_rows)
    recent_start_years = {r["start_year"] for r in tariff_rows}
    recent_start_years = set(sorted(recent_start_years)[-3:]) if len(recent_start_years) >= 3 else recent_start_years

    efficiency_rows = _rows_as_dicts(
        await db.run(_LATEST_EFFICIENCY_BY_SUBDIVISION_SQL, max_rows=max(max_rows, 20), timeout_seconds=timeout_seconds)
    )
    outstanding_rows = _rows_as_dicts(
        await db.run(
            _LAST_TWO_YEARS_OUTSTANDING_BY_SUBDIVISION_SQL, max_rows=max(max_rows, 20), timeout_seconds=timeout_seconds
        )
    )

    best_efficiency, worst_efficiency = _best_worst_efficiency(efficiency_rows)
    subsidy_rows = _rows_as_dicts(await db.run(_LATEST_SUBSIDY_BY_CATEGORY_SQL, max_rows=60, timeout_seconds=timeout_seconds))
    division = _rows_as_dicts(await db.run(_LATEST_DIVISION_SQL, max_rows=2, timeout_seconds=timeout_seconds))
    atc_rows = _rows_as_dicts(await db.run(_LATEST_ATC_BY_SUBDIVISION_SQL, max_rows=20, timeout_seconds=timeout_seconds))

    return {
        "fastest_growing_tariff_3yr": _fastest_growing_tariff_3yr(by_code, recent_start_years),
        "largest_yoy_jump": _largest_yoy_jump(by_code),
        "best_efficiency_subdivision": best_efficiency,
        "worst_efficiency_subdivision": worst_efficiency,
        "highest_outstanding_growth": _highest_outstanding_growth(outstanding_rows),
        "subsidy": _subsidy(subsidy_rows, division[0] if division else None),
        "atc": _atc(atc_rows, division[0] if division else None),
    }


def _subsidy(rows: list[dict], division: dict | None) -> dict | None:
    """Latest year: the division's Government subsidy (collection by adjustment) and the categories that carry it."""
    if not division or not division.get("collection_grand_total"):
        return None
    top = sorted((r for r in rows if (r["collection_adjust_total"] or 0) > 0),
                 key=lambda r: -(r["collection_adjust_total"] or 0))[:3]
    return {
        "fiscal_year": division["fiscal_year"],
        "adjustment_total": division["collection_adjust_total"],
        "collection_total": division["collection_grand_total"],
        "share_pct": round(division["adjustment_share_pct"], 1) if division["adjustment_share_pct"] is not None else None,
        "top": [{"code": r["booklet_code"], "name": r["booklet_name"], "amount": r["collection_adjust_total"],
                 "share_of_category_pct": round(r["adjustment_share_pct"], 1) if r["adjustment_share_pct"] is not None else None,
                 "share_of_subsidy_pct": round(r["collection_adjust_total"] * 100 / division["collection_adjust_total"], 1)
                 if division["collection_adjust_total"] else None} for r in top],
    }


def _atc(rows: list[dict], division: dict | None) -> dict | None:
    """Latest year: the division's AT&C loss and the sub-divisions with the highest / lowest."""
    if not division or division.get("atc_loss_pct") is None:
        return None
    ranked = sorted(rows, key=lambda r: r["atc_loss_pct"])
    pack = lambda r: {"subdivision_name": r["subdivision_name"], "atc_loss_pct": round(r["atc_loss_pct"], 2),
                      "billing_efficiency_pct": round(r["billing_efficiency_pct"], 2),
                      "collection_efficiency_pct": round(r["collection_efficiency_pct"], 2)}
    return {
        "fiscal_year": division["fiscal_year"],
        "division_atc_loss_pct": round(division["atc_loss_pct"], 2),
        "division_billing_efficiency_pct": round(division["billing_efficiency_pct"], 2),
        "division_collection_efficiency_pct": round(division["collection_efficiency_pct"], 2),
        "highest": pack(ranked[-1]) if ranked else None,
        "lowest": pack(ranked[0]) if ranked else None,
    }
