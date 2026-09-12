"""
Dashboard summary: read-only aggregate queries against the same locked
star-schema DB the chatbot itself uses, via app.db.ReadOnlyDB.run -- the
one persistent connection (design point #3), never a second connection
of our own.

These are NOT LLM-generated SQL, so app.sql_safety's guard doesn't apply
here -- they're fixed, hand-reviewed queries written once. They still
follow the same rules DCB_Semantic_Layer/semantic_layer.md lays down for
anyone querying this schema, LLM or human:
  - fact_dcb and fact_dcb_rollups are never summed together (rule 1).
  - The division-wide total is read via subdivision_type = 'division',
    never by summing every sub-division AND Division_Total (rule 2) --
    exactly the double-counting trap CLAUDE.md's adversarial eval
    question targets.
  - Per-sub-division figures come from fact_dcb_rollups' own grand_total
    row for that sub-division (its already-printed total), not a fresh
    SUM() over fact_dcb, keeping this consistent with rule 1.
  - Collection efficiency is recomputed from summed totals after
    aggregation, never averaged from the per-row pct_ column (rule 5).
  - Saligrama drops out of the data after 2017-18 and Division_Office
    only exists 2020-21..2023-24 (rule 4) -- neither needs special-casing
    here since both are simply absent from the relevant year's rows.
"""
from __future__ import annotations

from app.db import ReadOnlyDB

_YEARLY_DIVISION_TOTALS_SQL = """
    SELECT r.fiscal_year, r.demand_total, r.collection_grand_total,
           r.closing_balance_total, r.total_installations
    FROM fact_dcb_rollups r
    JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
    WHERE r.rollup_type = 'grand_total' AND s.subdivision_type = 'division'
    ORDER BY r.fiscal_year
"""

_SUBDIVISION_LATEST_YEAR_SQL_TEMPLATE = """
    SELECT s.subdivision_name, r.demand_total, r.collection_grand_total,
           r.closing_balance_total, r.total_installations, r.dc_installations
    FROM fact_dcb_rollups r
    JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
    WHERE r.rollup_type = 'grand_total' AND s.subdivision_type IN ('sub_division', 'section')
      AND r.fiscal_year = '{fy}'
    ORDER BY r.demand_total DESC
"""

_TOP_TARIFF_CATEGORIES_SQL_TEMPLATE = """
    SELECT t.tariff_code, SUM(f.demand_total) AS demand
    FROM fact_dcb f
    JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id
    JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
    WHERE f.fiscal_year = '{fy}' AND s.subdivision_type IN ('sub_division', 'section')
    GROUP BY t.tariff_code
    ORDER BY demand DESC
    LIMIT 5
"""


def _rows_as_dicts(query_result) -> list[dict]:
    return [dict(zip(query_result.columns, row)) for row in query_result.rows]


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def _efficiency_pct(demand_total: float | None, collection_total: float | None) -> float | None:
    if not demand_total:
        return None
    return round((collection_total or 0) / demand_total * 100, 1)


def _sql_quote(value: str) -> str:
    """Defensive only -- `value` always comes from our own prior query
    result (a fiscal_year string already in the DB), never from a
    request, but this keeps that assumption from becoming a silent
    requirement if a caller ever changes."""
    return value.replace("'", "''")


def build_dashboard_summary(db: ReadOnlyDB, max_rows: int, timeout_seconds: float) -> dict:
    yearly_result = db.run(_YEARLY_DIVISION_TOTALS_SQL, max_rows=max(max_rows, 50), timeout_seconds=timeout_seconds)
    years = _rows_as_dicts(yearly_result)
    if not years:
        raise RuntimeError("No division-wide fiscal-year rollup rows found in the star schema.")

    latest = years[-1]
    previous = years[-2] if len(years) > 1 else None
    latest_fy = latest["fiscal_year"]
    safe_fy = _sql_quote(latest_fy)

    subdivisions_result = db.run(
        _SUBDIVISION_LATEST_YEAR_SQL_TEMPLATE.format(fy=safe_fy),
        max_rows=max(max_rows, 20),
        timeout_seconds=timeout_seconds,
    )
    subdivisions = []
    for row in _rows_as_dicts(subdivisions_result):
        row["efficiency_pct"] = _efficiency_pct(row["demand_total"], row["collection_grand_total"])
        subdivisions.append(row)

    tariff_result = db.run(
        _TOP_TARIFF_CATEGORIES_SQL_TEMPLATE.format(fy=safe_fy),
        max_rows=max(max_rows, 10),
        timeout_seconds=timeout_seconds,
    )
    top_tariff_categories = []
    for row in _rows_as_dicts(tariff_result):
        row["share_pct"] = round(row["demand"] / latest["demand_total"] * 100, 1) if latest["demand_total"] else None
        top_tariff_categories.append(row)

    return {
        "fiscal_year": latest_fy,
        "kpis": {
            "total_demand": latest["demand_total"],
            "total_demand_yoy_pct": _pct_change(latest["demand_total"], previous["demand_total"] if previous else None),
            "total_collection": latest["collection_grand_total"],
            "total_collection_yoy_pct": _pct_change(
                latest["collection_grand_total"], previous["collection_grand_total"] if previous else None
            ),
            "outstanding_balance": latest["closing_balance_total"],
            "outstanding_balance_yoy_pct": _pct_change(
                latest["closing_balance_total"], previous["closing_balance_total"] if previous else None
            ),
            "total_installations": latest["total_installations"],
            "collection_efficiency_pct": _efficiency_pct(latest["demand_total"], latest["collection_grand_total"]),
            "collection_efficiency_prev_pct": (
                _efficiency_pct(previous["demand_total"], previous["collection_grand_total"]) if previous else None
            ),
        },
        "yearly_demand": [{"fiscal_year": y["fiscal_year"], "demand_total": y["demand_total"]} for y in years],
        "subdivisions": subdivisions,
        "top_tariff_categories": top_tariff_categories,
    }
