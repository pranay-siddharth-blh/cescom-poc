"""
Reports / comparison page (GET /reports): side-by-side charting of two
sub-divisions OR two tariff categories across all 11 fiscal years.
Same rules as app.dashboard_data (read-only, one persistent connection,
fixed hand-written SQL -- app.sql_safety's guard doesn't apply since
none of this is LLM-generated): fact_dcb vs fact_dcb_rollups never
summed together, division-wide tariff totals are scoped to the single
Division_Total row explicitly (never left unfiltered), efficiency is
always recomputed from summed totals, never averaged from the stored
pct_ column.

`name`/`tariff_code` reaching get_entity_series come from a request path
(app.main's /api/reports_series route) -- untrusted. They are validated
against the real, known option list fetched fresh from the DB
(get_reports_options) before any query runs, and then passed as bound SQL
parameters, never pasted into the SQL text -- a name that doesn't exactly
match one of those raises ValueError, never "run some other query with it."
"""
from __future__ import annotations

from app.db import ReadOnlyDB

_SUBDIVISION_OPTIONS_SQL = """
    SELECT subdivision_name FROM dim_subdivision
    WHERE subdivision_type IN ('sub_division', 'section')
    ORDER BY subdivision_name
"""

# Tariff categories as the CESC tariff booklet FY2025-26 to FY2027-28 names them (LT-1, LT-2, LT-3(a), LT-3(b), LT-4(a),
# LT-4(b), LT-4(c), LT-5, LT-6(a), LT-6(b), LT-6(c), LT-7, HT-1, HT-2(a), HT-2(b), HT-2(c)(i), HT-2(c)(ii), HT-3 ... HT-7),
# read from the mart's booklet-category table: the sheets' old codes are mapped to today's categories (domestic LT-2(a)
# -> LT-1, Govt lift irrigation HT-3(a)(i) -> HT-7, HT-3b -> HT-6, hoardings LT-7(b) -> LT-3(b)), so a category compares
# across all 11 years -- the printed families jump at the 2024-25 restructuring (KI-01). Only categories with data are
# offered.
_TARIFF_OPTIONS_SQL = """
    SELECT c.booklet_code, c.booklet_name
    FROM mart_tariff_booklet_category c
    WHERE EXISTS (SELECT 1 FROM mart_fact_booklet_division_year b WHERE b.booklet_code = c.booklet_code AND b.demand_total <> 0)
    ORDER BY c.booklet_order
"""

# One sub-division's own printed grand-total row per year (the mart's mart_fact_subdivision_year holds exactly those
# printed rows, checked against them at build time) -- with the consumer figures (#1), the cash / adjustment split
# (subsidy, #5), billing efficiency and AT&C loss (#6), all from the mart.
_SERIES_COLS = """
           demand_total AS demand, collection_grand_total AS collection, closing_balance_total AS arrears,
           total_installations AS installations, live_installations AS live_installations,
           billed_installations AS billed_installations, collection_cash_total AS cash_collection,
           collection_adjust_total AS adjustment_collection, consumption_total AS consumption"""
_SUBDIVISION_SERIES_SQL_TEMPLATE = f"""
    SELECT f.fiscal_year AS fiscal_year, d.start_year AS start_year, {_SERIES_COLS}
    FROM mart_fact_subdivision_year f
    JOIN mart_dim_fiscal_year d ON d.fiscal_year = f.fiscal_year
    WHERE f.subdivision_name = ?
    ORDER BY d.start_year
"""
_TARIFF_SERIES_SQL_TEMPLATE = f"""
    SELECT f.fiscal_year AS fiscal_year, d.start_year AS start_year, {_SERIES_COLS}
    FROM mart_fact_booklet_division_year f
    JOIN mart_dim_fiscal_year d ON d.fiscal_year = f.fiscal_year
    WHERE f.booklet_code = ?
    ORDER BY d.start_year
"""
# the division's own consumer total per year: each side's share of the consumer population
_DIVISION_CONSUMERS_SQL = "SELECT fiscal_year, total_installations, demand_total FROM mart_fact_division_year"


def _rows_as_dicts(query_result) -> list[dict]:
    return [dict(zip(query_result.columns, row)) for row in query_result.rows]


async def get_reports_options(db: ReadOnlyDB, max_rows: int, timeout_seconds: float) -> dict:
    subdivisions = _rows_as_dicts(
        await db.run(_SUBDIVISION_OPTIONS_SQL, max_rows=max(max_rows, 20), timeout_seconds=timeout_seconds)
    )
    tariffs = _rows_as_dicts(await db.run(_TARIFF_OPTIONS_SQL, max_rows=60, timeout_seconds=timeout_seconds))
    return {
        "subdivisions": [r["subdivision_name"] for r in subdivisions],
        "tariff_categories": [r["booklet_code"] for r in tariffs],
        "tariff_labels": {r["booklet_code"]: f"{r['booklet_code']} - {r['booklet_name']}" for r in tariffs},
    }


def _efficiency_pct(demand: float | None, collection: float | None) -> float | None:
    if not demand:
        return None
    return round((collection or 0) / demand * 100, 1)


async def get_entity_series(
    db: ReadOnlyDB, entity_type: str, value: str, max_rows: int, timeout_seconds: float
) -> dict:
    """entity_type is 'subdivision' or 'tariff' -- validated by the one
    caller (app.main's route), which also validates `value` against
    get_reports_options's real list before this ever runs a query."""
    if entity_type == "subdivision":
        rows = _rows_as_dicts(await db.run(_SUBDIVISION_SERIES_SQL_TEMPLATE, max_rows=max(max_rows, 20),
                                           timeout_seconds=timeout_seconds, params=(value,)))
    else:
        rows = _rows_as_dicts(await db.run(_TARIFF_SERIES_SQL_TEMPLATE, max_rows=max(max_rows, 30),
                                           timeout_seconds=timeout_seconds, params=(value,)))

    if not rows:
        raise ValueError(f"No data found for {entity_type} {value!r}.")
    division = {r["fiscal_year"]: r for r in _rows_as_dicts(
        await db.run(_DIVISION_CONSUMERS_SQL, max_rows=30, timeout_seconds=timeout_seconds))}

    per = lambda num, r: round(r[num] / r["installations"], 2) if r[num] is not None and r["installations"] else None
    pct = lambda a, b: round(a * 100.0 / b, 2) if a is not None and b else None
    share = lambda key, dkey: [pct(r[key], (division.get(r["fiscal_year"]) or {}).get(dkey)) for r in rows]
    atc = lambda r: (round(100 - (r["billed_installations"] / r["live_installations"]) * (r["collection"] / r["demand"]) * 100, 2)
                     if r["live_installations"] and r["demand"] and r["billed_installations"] is not None and r["collection"] is not None
                     else None)
    return {
        "label": value,
        "fiscal_years": [r["fiscal_year"] for r in rows],
        "demand": [r["demand"] for r in rows],
        "collection": [r["collection"] for r in rows],
        "collection_efficiency_pct": [_efficiency_pct(r["demand"], r["collection"]) for r in rows],
        "installations": [r["installations"] for r in rows],
        # consumers (#1): live consumers, and demand / collection / arrears per consumer (installation)
        "live_installations": [r["live_installations"] for r in rows],
        "demand_per_installation": [per("demand", r) for r in rows],
        "collection_per_installation": [per("collection", r) for r in rows],
        "arrears_per_installation": [per("arrears", r) for r in rows],
        # every amount as a percentage too (#2): consumer population and demand as a share of the division, live and
        # billed consumers as a share of the side's own consumers, the subsidy (adjustment) share, cash-only
        # efficiency, billing efficiency and AT&C loss
        "consumer_share_pct": share("installations", "total_installations"),
        "demand_share_pct": share("demand", "demand_total"),
        "live_pct": [pct(r["live_installations"], r["installations"]) for r in rows],
        "billed_installations": [r["billed_installations"] for r in rows],
        "billed_pct": [pct(r["billed_installations"], r["installations"]) for r in rows],
        "consumer_growth_pct": [None] + [pct(b["installations"] - a["installations"], a["installations"])
                                         if a["installations"] and b["installations"] is not None else None
                                         for a, b in zip(rows, rows[1:])],
        "cash_collection": [r["cash_collection"] for r in rows],
        "adjustment_collection": [r["adjustment_collection"] for r in rows],
        "adjustment_share_pct": [pct(r["adjustment_collection"], r["collection"]) for r in rows],
        "cash_efficiency_pct": [pct(r["cash_collection"], r["demand"]) for r in rows],
        "billing_efficiency_pct": [pct(r["billed_installations"], r["live_installations"]) for r in rows],
        "atc_loss_pct": [atc(r) for r in rows],
        "consumption": [r["consumption"] for r in rows],
    }
