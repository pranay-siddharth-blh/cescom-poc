"""
Runs the full pipeline (real Gemini call + real DB execution) over the 10
example questions + 2 adversarial questions, printing verbatim results
and per-stage latency, then writing eval_report.md next to this file.

Requires a real GEMINI_API_KEY in dcb-ai-app/.env (see .env.example).
Run from the dcb-ai-app/ directory:  python -m tests.eval_queries
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.db import init_db, shutdown_db, get_db
from app.llm import init_llm_client
from app.pipeline import answer_question
from app.semantic_layer import build_router_index
from tests.questions import EXAMPLE_QUESTIONS, ADVERSARIAL_QUESTIONS


async def main() -> None:
    if not settings.gemini_api_key:
        print(
            "GEMINI_API_KEY is not set (dcb-ai-app/.env). "
            "Copy .env.example to .env and fill it in before running this eval."
        )
        sys.exit(1)

    db = init_db()
    init_llm_client()
    router_index = build_router_index(db)

    report_lines = ["# DCB Chatbot — eval run\n"]
    all_results = []
    wall_start = time.monotonic()

    numbered = list(enumerate(EXAMPLE_QUESTIONS, start=1)) + [
        (f"A{i + 1}", q) for i, q in enumerate(ADVERSARIAL_QUESTIONS)
    ]

    for label, question in numbered:
        print(f"\n=== Q{label}: {question}")
        t0 = time.monotonic()
        result = await answer_question(
            question=question,
            db=db,
            router_index=router_index,
            max_rows=settings.dcb_max_rows,
            timeout_seconds=settings.dcb_query_timeout_seconds,
        )
        total_ms = round((time.monotonic() - t0) * 1000, 1)
        all_results.append((label, question, result, total_ms))

        print(f"  answerable: {result.answerable}")
        print(f"  sql: {result.sql}")
        print(f"  columns: {result.columns}")
        print(f"  rows: {result.rows}")
        print(f"  answer: {result.answer}")
        if result.error:
            print(f"  ERROR: {result.error}")
        print(f"  timing_ms: {result.timing_ms}  total_ms: {total_ms}")

        report_lines.append(f"## Q{label}: {question}\n")
        report_lines.append(f"- **answerable**: {result.answerable}")
        report_lines.append(f"- **sql**: `{result.sql}`")
        report_lines.append(f"- **columns**: {result.columns}")
        report_lines.append(f"- **rows**: {result.rows}")
        report_lines.append(f"- **answer**: {result.answer}")
        if result.error:
            report_lines.append(f"- **error**: {result.error}")
        report_lines.append(f"- **timing_ms**: {result.timing_ms}  (total: {total_ms} ms)\n")

    wall_total = round((time.monotonic() - wall_start) * 1000, 1)
    llm_times = [r.timing_ms.get("llm_ms", 0) for _, _, r, _ in all_results if r.timing_ms]
    total_times = [t for _, _, _, t in all_results]

    summary = (
        f"\n## Latency summary\n"
        f"- Questions run: {len(all_results)}\n"
        f"- Wall time for full run: {wall_total} ms\n"
        f"- Mean total latency per question: {round(sum(total_times) / len(total_times), 1)} ms\n"
        f"- Min / max total latency: {round(min(total_times), 1)} / {round(max(total_times), 1)} ms\n"
        f"- Mean LLM-call latency: {round(sum(llm_times) / len(llm_times), 1) if llm_times else 'N/A'} ms\n"
    )
    print(summary)
    report_lines.append(summary)

    (Path(__file__).parent / "eval_report.md").write_text("\n".join(report_lines), encoding="utf-8")
    print(f"\nFull report written to {Path(__file__).parent / 'eval_report.md'}")

    shutdown_db()


if __name__ == "__main__":
    asyncio.run(main())
