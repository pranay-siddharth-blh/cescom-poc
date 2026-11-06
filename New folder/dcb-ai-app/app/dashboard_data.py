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
    here since both are simply absent from the relevant year's rows. The
    one exception is the per-sub-division drill-down below, which has to
    look Saligrama's absence squarely in the face instead of just not
    querying past it -- see build_subdivision_dashboard_summary's
    docstring.

subdivision_name values that reach this module's per-sub-division
functions may originate from a request path (app.main's drill-down
route), not just our own prior query results. They are passed as bound
SQL parameters (app.db.ReadOnlyDB.run's `params`), never pasted into the
SQL text, and every statement runs under app.db's read-only authorizer.
The WHERE clause is also a whitelist check: a name that doesn't exactly
match a real sub_division/section row simply returns zero rows, which
build_subdivision_dashboard_summary treats as "not found," never as
"run some other query." No sub-division name is ever treated as trusted
just because it appeared in a URL.
"""
from __future__ import annotations

from app.db import ReadOnlyDB

_YEARLY_DIVISION_TOTALS_SQL = """
    SELECT r.fiscal_year, r.demand_total, r.collection_grand_total,
           r.closing_balance_total, r.total_installations, r.live_installations
    FROM fact_dcb_rollups r
    JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
    WHERE r.rollup_type = 'grand_total' AND s.subdivision_type = 'division'
    ORDER BY r.fiscal_year
"""

# Every sub-division/section that has EVER had a grand_total rollup row --
# not just the ones present in the division's latest fiscal year -- paired
# with each one's own first/last fiscal year and the real figures from
# its own last active year (not necessarily the division's latest year).
# A currently-active sub-division's "last active year" naturally equals
# the division's latest year, so this one query replaces what used to be
# a latest-year-only lookup without changing a single figure for the 5
# active sub-divisions -- it only adds Saligrama (closed after 2017-18),
# which the old latest-year-filtered query silently dropped entirely.
_ALL_SUBDIVISIONS_EVER_SQL = """
    SELECT s.subdivision_name, s.subdivision_type,
           span.first_fiscal_year, span.last_fiscal_year,
           r.demand_total, r.collection_grand_total,
           r.closing_balance_total, r.total_installations, r.dc_installations
    FROM (
        SELECT subdivision_id, MIN(fiscal_year) AS first_fiscal_year, MAX(fiscal_year) AS last_fiscal_year
        FROM fact_dcb_rollups
        WHERE rollup_type = 'grand_total'
        GROUP BY subdivision_id
    ) span
    JOIN dim_subdivision s ON s.subdivision_id = span.subdivision_id
    JOIN fact_dcb_rollups r ON r.subdivision_id = span.subdivision_id
        AND r.fiscal_year = span.last_fiscal_year AND r.rollup_type = 'grand_total'
    WHERE s.subdivision_type IN ('sub_division', 'section')
    ORDER BY r.demand_total DESC
"""

# Top tariff categories as the CESC tariff booklet FY2025-26 to FY2027-28 names them (LT-1 Domestic, LT-4(a) IP sets up to
# 10 HP, HT-2(a) ...), from the mart's booklet-category table: the sheets' printed codes mapped to today's categories, so
# the list reads the same in every year (the printed codes moved at the 2024-25 restructuring, KI-01). Real
# sub-divisions / sections only, as before.
_TOP_TARIFF_CATEGORIES_SQL_TEMPLATE = """
    SELECT booklet_code AS tariff_code, booklet_name AS tariff_name, SUM(demand_total) AS demand,
           SUM(total_installations) AS installations
    FROM mart_fact_booklet_subdivision_year
    WHERE fiscal_year = ?
    GROUP BY booklet_code
    ORDER BY demand DESC
    LIMIT 5
"""

# Same shape as the division-wide yearly totals query, scoped to one
# sub-division by exact name -- only the fiscal years THAT sub-division
# actually has a grand_total row for (see module docstring: no padding,
# no fabricated series for years it never existed).
_SUBDIVISION_YEARLY_SQL_TEMPLATE = """
    SELECT r.fiscal_year, r.demand_total, r.collection_grand_total,
           r.closing_balance_total, r.total_installations, r.live_installations
    FROM fact_dcb_rollups r
    JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
    WHERE r.rollup_type = 'grand_total' AND s.subdivision_type IN ('sub_division', 'section')
      AND s.subdivision_name = ?
    ORDER BY r.fiscal_year
"""

_SUBDIVISION_TOP_TARIFF_SQL_TEMPLATE = """
    SELECT booklet_code AS tariff_code, booklet_name AS tariff_name, SUM(demand_total) AS demand,
           SUM(total_installations) AS installations
    FROM mart_fact_booklet_subdivision_year
    WHERE fiscal_year = ? AND subdivision_name = ?
    GROUP BY booklet_code
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




# The DCB indicators panel (2026-10-01): the sheet's own percentages, FPPCA and DC / MNR, read from the mart for the
# year shown and the year before. Every percentage here reproduces the sheet's printed one (DCB_Mart build check).
_INDICATOR_COLS = [
    "collection_efficiency_pct", "live_installation_pct", "billed_installation_pct", "dc_mnr_installation_pct",
    "mnr_installation_pct", "metered_consumption_pct", "assessed_consumption_pct", "arrears_to_demand_ratio",
    "demand_fac", "collection_fac_total", "closing_balance_fac", "fac_share_of_demand_pct", "fac_collection_efficiency_pct",
    "fac_per_unit_rs", "dc_installations", "mnr_installations", "dc_mnr_installations", "total_installations",
    "live_installations", "billed_installations", "consumption_metered", "consumption_assessed", "consumption_total",
    "demand_per_installation_rs", "collection_per_installation_rs", "arrears_per_installation_rs",
    # 2026-10-01 (second pass): cash / adjustment (subsidy) split, efficiency without adjustment, billing efficiency, AT&C
    "demand_total", "collection_grand_total", "collection_cash_total", "collection_adjust_total", "adjustment_share_pct",
    "collection_efficiency_without_adj_pct", "billing_efficiency_pct", "atc_loss_pct",
]


async def _indicators(db: ReadOnlyDB, fiscal_year: str, previous_fy: str | None, subdivision: str | None,
                      timeout_seconds: float) -> dict:
    cols = ", ".join(_INDICATOR_COLS)
    if subdivision:
        sql = f"SELECT fiscal_year, {cols} FROM mart_fact_subdivision_year WHERE subdivision_name = ? AND fiscal_year IN (?, ?)"
        params = (subdivision, fiscal_year, previous_fy or fiscal_year)
    else:
        sql = f"SELECT fiscal_year, {cols} FROM mart_fact_division_year WHERE fiscal_year IN (?, ?)"
        params = (fiscal_year, previous_fy or fiscal_year)
    res = await db.run(sql, max_rows=5, timeout_seconds=timeout_seconds, params=params)
    by = {r["fiscal_year"]: r for r in _rows_as_dicts(res)}
    return {"fiscal_year": fiscal_year, "previous_fiscal_year": previous_fy,
            "current": by.get(fiscal_year), "previous": by.get(previous_fy) if previous_fy else None}


def _kpis(latest: dict, previous: dict | None) -> dict:
    """Shared by the division-wide and per-sub-division summaries -- same
    KPI shape, same formulas, just handed a different `latest`/`previous`
    pair of fact_dcb_rollups grand_total rows."""
    return {
        "total_demand": latest["demand_total"],
        "total_demand_yoy_pct": _pct_change(latest["demand_total"], previous["demand_total"] if previous else None),
        "total_collection": latest["collection_grand_total"],
        "total_collection_yoy_pct": _pct_change(
            latest["collection_grand_total"], previous["collection_grand_total"] if previous else None
        ),
        # closing_balance_total is OB + Demand - Collection, and OB is
        # itself the prior year's closing balance carried forward -- so
        # it's arrears accumulated since inception, not this year's own
        # shortfall (by request: show one fiscal year, not all years).
        # demand_total - collection_grand_total isolates just the
        # latest fiscal year's own billed-but-uncollected amount. For a
        # sub-division's final active year (e.g. Saligrama's 2017-18,
        # demand_total=0 with real collections still posted against
        # carried-over arrears) this correctly comes out negative --
        # collected more than was billed that year -- not clamped away,
        # since that's what the underlying figures actually say.
        "outstanding_balance": latest["demand_total"] - (latest["collection_grand_total"] or 0),
        "outstanding_balance_yoy_pct": _pct_change(
            latest["demand_total"] - (latest["collection_grand_total"] or 0),
            (previous["demand_total"] - (previous["collection_grand_total"] or 0)) if previous else None,
        ),
        "total_installations": latest["total_installations"],
        "collection_efficiency_pct": _efficiency_pct(latest["demand_total"], latest["collection_grand_total"]),
        "collection_efficiency_prev_pct": (
            _efficiency_pct(previous["demand_total"], previous["collection_grand_total"]) if previous else None
        ),
    }


async def build_dashboard_summary(db: ReadOnlyDB, max_rows: int, timeout_seconds: float) -> dict:
    yearly_result = await db.run(_YEARLY_DIVISION_TOTALS_SQL, max_rows=max(max_rows, 50), timeout_seconds=timeout_seconds)
    years = _rows_as_dicts(yearly_result)
    if not years:
        raise RuntimeError("No division-wide fiscal-year rollup rows found in the star schema.")

    latest = years[-1]
    previous = years[-2] if len(years) > 1 else None
    latest_fy = latest["fiscal_year"]
    subdivisions_result = await db.run(
        _ALL_SUBDIVISIONS_EVER_SQL,
        max_rows=max(max_rows, 20),
        timeout_seconds=timeout_seconds,
    )
    subdivisions = []
    for row in _rows_as_dicts(subdivisions_result):
        row["efficiency_pct"] = _efficiency_pct(row["demand_total"], row["collection_grand_total"])
        # Honest, not inferred by the frontend from date math: this
        # sub-division's own last active year is literally the division's
        # current year, or it isn't. Saligrama's is '2017-18' != latest_fy.
        row["is_active"] = row["last_fiscal_year"] == latest_fy
        # The Sub-divisions panel only lists currently-active sub-divisions
        # / sections, by request -- a closed one (Saligrama, closed after
        # 2017-18) is dropped from what's shown here even though
        # _ALL_SUBDIVISIONS_EVER_SQL above still finds it, so `is_active`
        # is computed for every row before this filter runs, not lost.
        # build_subdivision_dashboard_summary can still be queried
        # directly by name for any sub-division that ever existed
        # (including Saligrama) -- only this list, what actually renders
        # as a clickable row, is restricted to active ones.
        if row["is_active"]:
            subdivisions.append(row)

    tariff_result = await db.run(
        _TOP_TARIFF_CATEGORIES_SQL_TEMPLATE,
        max_rows=max(max_rows, 10),
        timeout_seconds=timeout_seconds,
        params=(latest_fy,),
    )
    top_tariff_categories = []
    for row in _rows_as_dicts(tariff_result):
        row["share_pct"] = round(row["demand"] / latest["demand_total"] * 100, 1) if latest["demand_total"] else None
        row["installation_share_pct"] = (round(row["installations"] / latest["total_installations"] * 100, 1)
                                         if row["installations"] is not None and latest.get("total_installations") else None)
        top_tariff_categories.append(row)

    return {
        "fiscal_year": latest_fy,
        "kpis": _kpis(latest, previous),
        "yearly_demand": [{"fiscal_year": y["fiscal_year"], "demand_total": y["demand_total"],
                           "collection_grand_total": y["collection_grand_total"],
                           "closing_balance_total": y["closing_balance_total"]} for y in years],
        "yearly_live_installations": [
            {"fiscal_year": y["fiscal_year"], "live_installations": y["live_installations"]} for y in years
        ],
        "subdivisions": subdivisions,
        "top_tariff_categories": top_tariff_categories,
        "indicators": await _indicators(db, latest_fy, previous["fiscal_year"] if previous else None, None, timeout_seconds),
    }


async def build_subdivision_dashboard_summary(
    db: ReadOnlyDB, subdivision_name: str, max_rows: int, timeout_seconds: float
) -> dict:
    """Same JSON shape as build_dashboard_summary's kpis/yearly_demand/
    yearly_live_installations/top_tariff_categories, but scoped to one
    sub-division (or section -- Konanur) by exact name, using ITS OWN
    latest fiscal year as "current," not the division's. Raises
    ValueError if `subdivision_name` doesn't exactly match a real
    sub_division/section row with at least one grand_total rollup --
    that covers both a genuine typo and someone hand-crafting a request
    for 'Division_Total'/'Division_Office' (out of scope for this
    per-sub-division view, which is deliberately restricted to the same
    subdivision_type IN ('sub_division', 'section') set used everywhere
    else in this file), or anything else nonexistent.

    yearly_demand / yearly_live_installations only ever contain the
    fiscal years this sub-division has a real row for -- no zero-padding
    to a full 11-year series. For Saligrama that's 3 years, ending
    2017-18; every other sub-division/section currently gets all 11.

    Saligrama's own last active year (2017-18) is a real, unsmoothed
    surprise worth calling out rather than hiding: demand_total is 0.0
    that year (fact_dcb agrees -- every tariff category's demand is 0.0
    too) while collection_grand_total is still ~₹7.96 Cr, real money
    collected against carried-over arrears after billing had already
    stopped. That makes outstanding_balance negative for that year
    (collected more than was billed) -- left as-is, not clamped, per
    this module's own _kpis docstring note.
    """
    yearly_result = await db.run(
        _SUBDIVISION_YEARLY_SQL_TEMPLATE,
        max_rows=max(max_rows, 20),
        timeout_seconds=timeout_seconds,
        params=(subdivision_name,),
    )
    years = _rows_as_dicts(yearly_result)
    if not years:
        raise ValueError(
            f"No sub-division/section named {subdivision_name!r} with any fact_dcb_rollups "
            "grand_total rows was found. Either a typo, or it's Division_Total, "
            "Division_Office, or some other subdivision_type this per-sub-division view "
            "deliberately excludes (same subdivision_type IN ('sub_division', 'section') "
            "filter as the rest of this file)."
        )

    latest = years[-1]
    previous = years[-2] if len(years) > 1 else None
    latest_fy = latest["fiscal_year"]
    tariff_result = await db.run(
        _SUBDIVISION_TOP_TARIFF_SQL_TEMPLATE,
        max_rows=max(max_rows, 10),
        timeout_seconds=timeout_seconds,
        params=(latest_fy, subdivision_name),
    )
    top_tariff_categories = []
    for row in _rows_as_dicts(tariff_result):
        row["share_pct"] = round(row["demand"] / latest["demand_total"] * 100, 1) if latest["demand_total"] else None
        row["installation_share_pct"] = (round(row["installations"] / latest["total_installations"] * 100, 1)
                                         if row["installations"] is not None and latest.get("total_installations") else None)
        top_tariff_categories.append(row)

    return {
        "subdivision_name": subdivision_name,
        "fiscal_year": latest_fy,
        "first_fiscal_year": years[0]["fiscal_year"],
        "last_fiscal_year": latest_fy,
        "kpis": _kpis(latest, previous),
        "yearly_demand": [{"fiscal_year": y["fiscal_year"], "demand_total": y["demand_total"],
                           "collection_grand_total": y["collection_grand_total"],
                           "closing_balance_total": y["closing_balance_total"]} for y in years],
        "yearly_live_installations": [
            {"fiscal_year": y["fiscal_year"], "live_installations": y["live_installations"]} for y in years
        ],
        "top_tariff_categories": top_tariff_categories,
        "indicators": await _indicators(db, latest_fy, previous["fiscal_year"] if previous else None, subdivision_name,
                                        timeout_seconds),
    }
