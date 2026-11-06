"""
Live stress test against the RUNNING server (python -m uvicorn app.main:app --port 8000). Asks every question of
tests/stress_bank.py through POST /api/ask_once, recomputes each expected figure from the locked star schema with an
independent read-only query, and checks the answer. Paced to stay under the server's per-IP rate limit.
Writes tests/stress_report.json and ../reports/stress_test.md.

Run from dcb-ai-app/:  python tests/run_stress.py [--base http://127.0.0.1:8000] [--workers 2]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from tests.stress_bank import BANK  # noqa: E402

STAR = HERE.parent.parent / "DCB_Star_Schema" / "dcb_star_schema.sqlite"
_con = sqlite3.connect(f"file:{STAR.as_posix()}?mode=ro", uri=True, check_same_thread=False)
_lock = threading.Lock()
_pace = threading.Lock()
_last = [0.0]


def star(sql: str, params: tuple):
    with _lock:
        row = _con.execute(sql, params).fetchone()
    return row[0] if row else None


def fmt(v, kind: str) -> list[str]:
    if v is None:
        return ["no figures"]
    if kind == "rs":
        return [f"₹{v:,.2f}", f"{v:,.2f}"]
    if kind == "cr":                       # a summary card prints crore only
        return [f"₹{v / 1e7:,.2f} crore"]
    if kind == "pct":
        return [f"{v:.2f}%"]
    if kind == "count":
        return [f"{v:,.0f}"]
    return [str(v)]


def ask(base: str, q: str, min_gap: float) -> dict:
    with _pace:
        wait = _last[0] + min_gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
    req = urllib.request.Request(base + "/api/ask_once", data=json.dumps({"question": q}).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.monotonic()
    body = json.loads(urllib.request.urlopen(req, timeout=240).read())
    body["_seconds"] = round(time.monotonic() - t, 2)
    return body


def check(item: dict, body: dict) -> list[str]:
    text = (body.get("answer") or "")
    low = text.lower()
    problems = []
    for c in item["checks"]:
        kind = c[0]
        if kind == "refuse" and body.get("answerable"):
            problems.append("expected a refusal")
        elif kind == "answer" and not body.get("answerable"):
            problems.append("expected an answer")
        elif kind == "text" and not any(a.lower() in low for a in c[1]):
            problems.append(f"missing one of {c[1]}")
        elif kind == "num":
            want = fmt(star(c[1], c[2]), c[3])
            if not any(w in text for w in want):
                problems.append(f"missing figure {want[0]}")
        elif kind == "name":
            name = star(c[1], c[2])
            if name not in text:
                problems.append(f"missing {name}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--per-minute", type=int, default=55)
    a = ap.parse_args()
    gap = 60.0 / a.per_minute
    results = []

    def one(item):
        try:
            body = ask(a.base, item["q"], gap)
            probs = check(item, body)
        except Exception as exc:  # noqa: BLE001
            body, probs = {"answer": f"{type(exc).__name__}: {exc}", "answerable": False, "_seconds": 0}, ["request failed"]
        rec = {"id": item["id"], "area": item["area"], "q": item["q"], "ok": not probs, "problems": probs,
               "answer": body.get("answer", ""), "answerable": body.get("answerable"), "seconds": body.get("_seconds")}
        print(f"{rec['id']:4} {'PASS' if rec['ok'] else 'FAIL'} {rec['area']:12} {rec['q'][:70]}" +
              ("" if rec["ok"] else f"  <- {'; '.join(probs)}"), flush=True)
        return rec

    with ThreadPoolExecutor(a.workers) as ex:
        results = list(ex.map(one, BANK))
    passed = sum(r["ok"] for r in results)
    (HERE / "stress_report.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    areas: dict[str, list[int]] = {}
    for r in results:
        areas.setdefault(r["area"], [0, 0])
        areas[r["area"]][0] += r["ok"]
        areas[r["area"]][1] += 1
    lines = [f"# Live stress test — {passed}/{len(results)} passed", "",
             "Every expected figure was recomputed from the locked star schema by an independent query.", "",
             "| Area | Passed |", "|---|---|"] + [f"| {k} | {v[0]}/{v[1]} |" for k, v in areas.items()] + ["", "## Failures", ""]
    lines += [f"- #{r['id']} {r['q']} — {'; '.join(r['problems'])}\n  > {r['answer'][:300]}" for r in results if not r["ok"]] or ["None."]
    out = HERE.parent.parent / "reports" / "stress_test.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(results)} passed -> {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
