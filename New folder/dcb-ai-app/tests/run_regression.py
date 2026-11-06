"""
Live regression through the real pipeline (real gateway -- VPN needed for the model routes), in-process:

  * the 49 questions of ../test_questions.json, compared with the last recorded browser run
    (../test_run_results_fix3/results.json, status OK / FAIL by its error-marker rule);
  * the 82 questions of tests/eval_queries_categorized.py, with the category each belongs to.

For every question it records the route taken (deterministic route or model), the SQL, the answer, whether the
answer carries refusal / no-data wording, and whether it differs in status from the previous run. Writes
../reports/regression_kpcl_port.json and .md.

Run from dcb-ai-app/:  python -m tests.run_regression [--workers 4]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pipeline  # noqa: E402
from app.config import PROJECT_ROOT, settings  # noqa: E402
from app.db import ReadOnlyDB  # noqa: E402
from app.llm import init_llm_client  # noqa: E402
from app.semantic_layer import build_router_index  # noqa: E402

ERROR_MARKERS = ["unable to answer", "i don't know", "i couldn't", "no data found", "error", "something went wrong",
                 "not able to", "no results", "could not find", "no matching"]
OUT = PROJECT_ROOT / "reports"


def categorized() -> list[tuple[str, str]]:
    src = (Path(__file__).parent / "eval_queries_categorized.py").read_text(encoding="utf-8")
    start = src.index("QUESTION_CATEGORIES")
    end = src.index("]\n\n", start) + 2
    ns: dict = {}
    exec("from __future__ import annotations\n" + src[start:end], ns)  # noqa: S102 -- our own test file
    return [(f"{letter}{n}", label, q) for letter, label, qs in ns["QUESTION_CATEGORIES"] for n, q in qs]


def status(answer: str) -> str:
    low = (answer or "").lower()
    return "FAIL" if any(m in low for m in ERROR_MARKERS) else "OK"


async def main(workers: int) -> None:
    db = ReadOnlyDB(settings.db_path)
    init_llm_client()
    idx = await build_router_index(db)
    prev = {r["id"]: r for r in json.loads((PROJECT_ROOT / "test_run_results_fix3" / "results.json").read_text(encoding="utf-8"))}
    items = [("suite", str(q["id"]), q["category"], q["question"]) for q in
             json.loads((PROJECT_ROOT / "test_questions.json").read_text(encoding="utf-8"))]
    items += [("categorized", i, label, q) for i, label, q in categorized()]
    sem = asyncio.Semaphore(workers)
    out = []

    async def one(kind, qid, cat, q):
        async with sem:
            t = time.monotonic()
            try:
                r = await pipeline.answer_question(q, db, idx, settings.dcb_max_rows, settings.dcb_query_timeout_seconds)
                rec = {"kind": kind, "id": qid, "category": cat, "question": q, "route": r.route, "answerable": r.answerable,
                       "answer": r.answer, "sql": r.sql, "rows": len(r.rows), "verification": r.verification,
                       "review_notes": r.review_notes, "error": r.error}
            except Exception as exc:  # noqa: BLE001
                rec = {"kind": kind, "id": qid, "category": cat, "question": q, "route": "EXCEPTION", "answerable": False,
                       "answer": f"{type(exc).__name__}: {exc}", "sql": "", "rows": 0}
            rec["seconds"] = round(time.monotonic() - t, 2)
            rec["status"] = status(rec["answer"])
            if kind == "suite":
                p = prev.get(int(qid))
                rec["previous_status"] = p["status"] if p else None
                rec["previous_answer"] = (p or {}).get("answer", "")[:400]
            out.append(rec)
            print(f"{kind[:5]} {qid:>4} {rec['status']:4} {rec['route']:28} {rec['seconds']:5}s  {q[:70]}", flush=True)

    await asyncio.gather(*(one(*it) for it in items))
    order = {("suite", str(i)): n for n, i in enumerate(range(1, 100))}
    out.sort(key=lambda r: (r["kind"] != "suite", order.get((r["kind"], r["id"]), 0), r["id"]))
    OUT.mkdir(exist_ok=True)
    (OUT / "regression_kpcl_port.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    suite = [r for r in out if r["kind"] == "suite"]
    ok = sum(r["status"] == "OK" for r in suite)
    prev_ok = sum(r["previous_status"] == "OK" for r in suite)
    worse = [r for r in suite if r["previous_status"] == "OK" and r["status"] != "OK"]
    better = [r for r in suite if r["previous_status"] != "OK" and r["status"] == "OK"]
    routes: dict[str, int] = {}
    for r in out:
        k = r["route"].split(":")[0]
        routes[k] = routes.get(k, 0) + 1
    lines = ["# Regression after the KPCL port (live, real gateway)", "",
             f"Suite (test_questions.json, 49): **{ok}/{len(suite)} OK** by the error-marker rule; previous recorded run "
             f"(test_run_results_fix3): {prev_ok}/{len(suite)}.", "",
             f"Routes over all {len(out)} questions: " + ", ".join(f"{k} {v}" for k, v in sorted(routes.items())), "",
             "## Previously OK, now flagged", ""] + [f"- #{r['id']} {r['question']} -> `{r['route']}`: {r['answer'][:200]}" for r in worse] + \
            ["", "## Previously flagged, now OK", ""] + [f"- #{r['id']} {r['question']} -> `{r['route']}`" for r in better] + \
            ["", "## Every answer", ""]
    for r in out:
        lines += [f"### {r['kind']} {r['id']} — {r['question']}", f"route `{r['route']}` · {r['status']} · {r['seconds']}s"
                  + (f" · previous {r.get('previous_status')}" if r["kind"] == "suite" else ""), "",
                  "```", r["answer"][:1200], "```", ""]
    (OUT / "regression_kpcl_port.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nsuite {ok}/{len(suite)} OK (previous {prev_ok}); {len(worse)} worse, {len(better)} better; routes {routes}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    asyncio.run(main(ap.parse_args().workers))
