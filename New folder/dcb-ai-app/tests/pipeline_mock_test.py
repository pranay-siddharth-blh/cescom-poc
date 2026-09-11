"""
Exercises the FULL request/response pipeline -- FastAPI routing, lifespan,
the DB layer, sql_safety, template-filling, and SSE streaming -- with
app.llm.generate_query_plan monkeypatched to a fixed LLMQueryPlan instead
of a real Gemini call. This is NOT a substitute for tests/eval_queries.py
(it proves the plumbing works, not that the LLM generates correct SQL),
but it's real, verbatim evidence the app wiring itself is correct.

Run from the dcb-ai-app/ directory:  python -m tests.pipeline_mock_test
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.models import LLMQueryPlan


FIXED_PLAN = LLMQueryPlan(
    answerable=True,
    sql=(
        "SELECT ROUND(SUM(f.collection_grand_total) * 100.0 / SUM(f.demand_total), 2) "
        "AS collection_efficiency_pct FROM fact_dcb f "
        "JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id "
        "WHERE s.subdivision_name = 'Arkalgud' AND f.fiscal_year = '2024-25'"
    ),
    answer_template="The collection efficiency in {subdivision_name} for {fiscal_year} was {collection_efficiency_pct}%.",
)

UNSAFE_PLAN = LLMQueryPlan(
    answerable=True,
    sql="DELETE FROM fact_dcb",
    answer_template="deleted",
)


def main() -> None:
    # NOTE: app.pipeline does `from app.llm import generate_query_plan` (a direct
    # name import), so the patch target for both scenarios below must be
    # "app.pipeline.generate_query_plan", not "app.llm.generate_query_plan" --
    # patching the latter after app.pipeline has already imported the name
    # would silently not take effect. Real Python import-binding gotcha,
    # caught by writing this test, not a bug in the app's actual runtime
    # behavior (the app never swaps app.llm.generate_query_plan at runtime).
    with patch("app.pipeline.generate_query_plan", new=AsyncMock(return_value=FIXED_PLAN)):
        from app.main import app

        with TestClient(app) as client:
            print("--- /api/ask_once (mocked LLM, real DB) ---")
            r = client.post("/api/ask_once", json={"question": "What was the collection efficiency in Arkalgud in 2024-25?"})
            print("status:", r.status_code)
            print("body:", r.json())
            assert r.status_code == 200
            body = r.json()
            assert body["answerable"] is True
            assert "93.62" in body["answer"], body["answer"]
            print("ASSERTIONS PASSED: answerable=True, template correctly filled with real DB value 93.62%\n")

            print("--- /api/ask (SSE, mocked LLM, real DB) ---")
            with client.stream("POST", "/api/ask", json={"question": "What was the collection efficiency in Arkalgud in 2024-25?"}) as resp:
                print("status:", resp.status_code)
                events = list(resp.iter_lines())
                for line in events:
                    print(" ", line)
            print("SSE stream completed\n")

        # Now prove the safety layer holds even if the LLM returns an unsafe query.
        with patch("app.pipeline.generate_query_plan", new=AsyncMock(return_value=UNSAFE_PLAN)):
            with TestClient(app) as client:
                print("--- /api/ask_once with a (mocked) unsafe LLM response ---")
                r = client.post("/api/ask_once", json={"question": "irrelevant"})
                print("status:", r.status_code)
                print("body:", r.json())
                assert r.json()["sql"] == "DELETE FROM fact_dcb"  # echoed back for transparency
                assert "safety check" in r.json()["answer"]
                print("ASSERTIONS PASSED: unsafe SQL was rejected before reaching the DB, error surfaced honestly")


if __name__ == "__main__":
    main()
