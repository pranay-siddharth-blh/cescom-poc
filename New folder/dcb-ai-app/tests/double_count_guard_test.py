"""
Deterministic, code-level tests for app.sql_safety's Division_Total
double-counting guard (no LLM involved -- the whole point is that this
holds regardless of what the LLM generates). See sql_safety.py's own
module docstring for the full design rationale.

Run from the dcb-ai-app/ directory:  python -m tests.double_count_guard_test
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sql_safety import UnsafeSQLError, validate_readonly_sql

# --- Dangerous: must be BLOCKED --------------------------------------------

DANGEROUS_CASES = [
    (
        "no subdivision reference at all",
        "SELECT SUM(f.demand_total) AS total FROM fact_dcb f WHERE f.fiscal_year = '2024-25'",
    ),
    (
        "the real shape from the intermediate eval run: dim_subdivision "
        "joined but never filtered, GROUP BY tariff_code only",
        "SELECT t.tariff_code AS tariff_code, SUM(f.demand_total) AS total_demand "
        "FROM fact_dcb f "
        "JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id "
        "JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id "
        "WHERE f.fiscal_year = '2023-24' GROUP BY t.tariff_code",
    ),
    (
        "adversarial question's literal ask: sum every sub-division's "
        "demand plus Division_Total, joined but unfiltered",
        "SELECT SUM(f.demand_total) AS combined_total FROM fact_dcb f "
        "JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id "
        "WHERE f.fiscal_year = '2024-25'",
    ),
    (
        "COUNT (not SUM) is equally unsafe -- same root cause",
        "SELECT COUNT(f.total_installations) AS cnt FROM fact_dcb f "
        "JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id "
        "WHERE f.fiscal_year = '2024-25'",
    ),
    (
        "subdivision_type IN(...) that DOES include 'division' -- looks "
        "like a safety filter but isn't one",
        "SELECT SUM(f.demand_total) AS total FROM fact_dcb f "
        "JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id "
        "WHERE s.subdivision_type IN ('division', 'sub_division') AND f.fiscal_year = '2024-25'",
    ),
    (
        "two subdivision_name equalities (OR-shaped) -- ambiguous, must "
        "fail closed rather than trust the first match",
        "SELECT SUM(f.demand_total) AS total FROM fact_dcb f "
        "JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id "
        "WHERE f.fiscal_year = '2024-25' AND "
        "(s.subdivision_name = 'Division_Total' OR s.subdivision_name = 'Arkalgud')",
    ),
]

# --- Safe: must be ACCEPTED (these are the real shapes from tonight's
# final clean eval run -- regression-critical, must keep working) ---------

SAFE_CASES = [
    (
        "single real subdivision by name (Q1/Q4/Q10 shape)",
        "SELECT ROUND(SUM(f.collection_grand_total) * 100.0 / SUM(f.demand_total), 2) "
        "AS collection_efficiency_pct FROM fact_dcb f "
        "JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id "
        "WHERE s.subdivision_name = 'Arkalgud' AND f.fiscal_year = '2024-25'",
    ),
    (
        "Division_Total only, explicitly (Q3/Q5 shape -- the official total, single row)",
        "SELECT t.tariff_code AS tariff_code, SUM(f.demand_total) AS total_demand "
        "FROM fact_dcb f "
        "JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id "
        "JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id "
        "WHERE f.fiscal_year = '2023-24' AND s.subdivision_name = 'Division_Total' "
        "GROUP BY t.tariff_code",
    ),
    (
        "subdivision_type IN(...) excluding 'division' (Q7/Q8 shape)",
        "SELECT s.subdivision_name AS subdivision_name, "
        "SUM(f.collection_grand_total) AS total FROM fact_dcb f "
        "JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id "
        "WHERE f.fiscal_year = '2024-25' AND s.subdivision_type IN ('sub_division', 'section') "
        "GROUP BY s.subdivision_name",
    ),
    (
        "GROUP BY subdivision_name with no WHERE filter at all -- each "
        "row (including one for Division_Total) is still individually correct",
        "SELECT s.subdivision_name AS subdivision_name, SUM(f.demand_total) AS total "
        "FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id "
        "WHERE f.fiscal_year = '2024-25' GROUP BY s.subdivision_name",
    ),
    (
        "a fact_dcb query with no aggregate at all -- nothing to double-count",
        "SELECT f.demand_total AS demand FROM fact_dcb f "
        "JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id "
        "WHERE s.subdivision_name = 'Konanur' AND f.fiscal_year = '2024-25' LIMIT 1",
    ),
    (
        "fact_dcb_subdivisions view (structurally safe, excludes Division_Total by construction)",
        "SELECT SUM(demand_total) AS total FROM fact_dcb_subdivisions WHERE fiscal_year = '2024-25'",
    ),
    (
        "fact_dcb_rollups / fact_dcb_grand_totals -- different table, different grain, out of this guard's scope",
        "SELECT demand_total AS demand_total FROM fact_dcb_grand_totals "
        "WHERE fiscal_year = '2025-26' AND subdivision_id = 1",
    ),
]


def main() -> None:
    failures = []

    print("--- Dangerous cases (must be BLOCKED) ---")
    for label, sql in DANGEROUS_CASES:
        try:
            validate_readonly_sql(sql)
            print(f"FAIL (approved a double-counting query!): {label}")
            failures.append(label)
        except UnsafeSQLError as exc:
            print(f"OK, blocked: {label}\n    -> {exc}")

    print("\n--- Safe cases (must be ACCEPTED, regression-critical) ---")
    for label, sql in SAFE_CASES:
        try:
            validate_readonly_sql(sql)
            print(f"OK, accepted: {label}")
        except UnsafeSQLError as exc:
            print(f"FAIL (wrongly blocked a safe query!): {label}\n    -> {exc}")
            failures.append(label)

    print(f"\n{len(DANGEROUS_CASES) + len(SAFE_CASES) - len(failures)}/"
          f"{len(DANGEROUS_CASES) + len(SAFE_CASES)} cases behaved as expected.")
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL CASES PASSED.")


if __name__ == "__main__":
    main()
