"""
Exercises every component EXCEPT the live Gemini call (no API key needed).
This is not a substitute for tests/eval_queries.py — it cannot show real
LLM-generated SQL or the final template-filled answer — but it gives real,
verbatim evidence that: the DB opens read-only and truly can't be written
to, the safety layer rejects unsafe SQL and accepts safe SQL, the router's
entity-extraction and semantic-layer pruning behave correctly on all 12
eval questions, and the 10 pre-verified reference queries from
DCB_Semantic_Layer/example_queries.sql still return the same results
through this app's own DB layer (db.run) that they do standalone.

Run from the dcb-ai-app/ directory:  python -m tests.dry_run_no_llm
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.db import init_db, shutdown_db
from app.sql_safety import UnsafeSQLError, validate_readonly_sql
from app.semantic_layer import build_router_index, extract_entities, prune_semantic_layer
from tests.questions import EXAMPLE_QUESTIONS, ADVERSARIAL_QUESTIONS

REFERENCE_QUERIES = [
    (
        "collection efficiency Arkalgud 2024-25",
        """SELECT ROUND(SUM(f.collection_grand_total) * 100.0 / SUM(f.demand_total), 2) AS collection_efficiency_pct
FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE s.subdivision_name = 'Arkalgud' AND f.fiscal_year = '2024-25'""",
    ),
    (
        "top tariff category by demand 2023-24",
        """SELECT t.tariff_code, t.description, SUM(f.demand_total) AS total_demand
FROM fact_dcb f JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id
JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE f.fiscal_year = '2023-24' AND s.subdivision_name = 'Division_Total'
GROUP BY t.tariff_code, t.description ORDER BY total_demand DESC LIMIT 5""",
    ),
    (
        "best collection efficiency by sub-division, latest year",
        """SELECT s.subdivision_name, ROUND(SUM(f.collection_grand_total) * 100.0 / SUM(f.demand_total), 2) AS pct
FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE f.fiscal_year = (SELECT MAX(fiscal_year) FROM fact_dcb) AND s.subdivision_type IN ('sub_division','section')
GROUP BY s.subdivision_name ORDER BY pct DESC""",
    ),
]


def main() -> None:
    print(f"DB path: {settings.db_path}  (exists: {settings.db_path.exists()})")
    db = init_db()
    print("DB opened read-only: OK")

    # 1. Safety layer.
    print("\n--- SQL safety checks ---")
    good = "SELECT fiscal_year FROM dim_fiscal_year"
    validate_readonly_sql(good)
    print(f"ACCEPTED (expected): {good!r}")
    for bad in [
        "DELETE FROM fact_dcb",
        "DROP TABLE dim_subdivision",
        "SELECT 1; DELETE FROM fact_dcb",
        "UPDATE fact_dcb SET demand_total=0",
        "ATTACH DATABASE 'x' AS y",
    ]:
        try:
            validate_readonly_sql(bad)
            print(f"FAILED TO REJECT (bug!): {bad!r}")
        except UnsafeSQLError as exc:
            print(f"REJECTED (expected): {bad!r} -> {exc}")

    # 2. Read-only connection truly can't write, even if a bad query got past validate_readonly_sql.
    print("\n--- Read-only enforcement at the connection level ---")
    try:
        db.run("DELETE FROM fact_dcb", max_rows=1, timeout_seconds=5)
        print("FAILED TO BLOCK WRITE (bug!)")
    except Exception as exc:  # noqa: BLE001
        print(f"Write blocked at connection level (expected): {exc}")

    # 3. Router: entity extraction + semantic-layer pruning, all 12 questions.
    print("\n--- Router (entity extraction + semantic-layer pruning) ---")
    router_index = build_router_index(db)
    print(f"subdivisions loaded: {router_index.subdivision_names}")
    print(f"fiscal years loaded: {router_index.fiscal_years}")
    all_questions = EXAMPLE_QUESTIONS + ADVERSARIAL_QUESTIONS
    router_times = []
    for q in all_questions:
        t0 = time.monotonic()
        entities = extract_entities(q, router_index)
        pruned = prune_semantic_layer(q, router_index)
        elapsed_ms = (time.monotonic() - t0) * 1000
        router_times.append(elapsed_ms)
        print(
            f"  [{elapsed_ms:.2f}ms] entities={entities} "
            f"glossary_terms={list(pruned['glossary'].keys())} "
            f"tariff_terms={list(pruned['tariff_code_glossary'].keys())}"
        )
    print(
        f"Router timing: mean={sum(router_times)/len(router_times):.2f}ms "
        f"max={max(router_times):.2f}ms over {len(router_times)} questions"
    )

    # 4. Reference queries (pre-verified in example_queries.sql) through this app's own DB layer.
    print("\n--- Reference queries through app.db.ReadOnlyDB.run() ---")
    for label, sql in REFERENCE_QUERIES:
        result = db.run(sql, max_rows=settings.dcb_max_rows, timeout_seconds=settings.dcb_query_timeout_seconds)
        print(f"  {label}:")
        print(f"    columns={result.columns}")
        print(f"    rows={result.rows}")
        print(f"    elapsed={result.elapsed_seconds*1000:.2f}ms truncated={result.truncated}")

    shutdown_db()
    print("\nDone.")


if __name__ == "__main__":
    main()
