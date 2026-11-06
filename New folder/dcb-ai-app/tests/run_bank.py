"""
The expanded bank (tests/voice_bank.py) asked as TYPED text, in-process, in voice mode: the same checks as the live
stress test (figures recomputed from the locked star schema), plus the spoken-answer rules for every answer (present for
every answerable question, no screen marks, a single figure said aloud). Shows which questions the pipeline answers
before any speech is involved, so the spoken run can tell hearing misses from pipeline misses.

usage (from dcb-ai-app):  python tests/run_bank.py [--area client] [--workers 6]
Writes tests/bank_report.json and ../reports/STRESS_TEST_QUESTIONS_VOICE.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

os.environ["DCB_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["VOICE_RATE_LIMIT_PER_MINUTE"] = "100000"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import httpx  # noqa: E402

from tests.run_stress import check, star  # noqa: E402
from tests.run_voice_stress import spoken_has  # noqa: E402
from tests.voice_bank import BANK  # noqa: E402

MD = {"voice": HERE.parent.parent / "reports" / "STRESS_TEST_QUESTIONS_VOICE.md",
      "text": HERE.parent.parent / "reports" / "STRESS_TEST_QUESTIONS.md"}


async def one(client, item, sem, mode="voice"):
    async with sem:
        r = await client.post("/api/ask_once", json={"question": item["q"], "mode": mode})
        body = r.json() if r.status_code < 400 else {"answer": r.text, "answerable": False}
        probs = check(item, body)
        spoken = body.get("spoken_answer") or ""
        nums = [c for c in item["checks"] if c[0] == "num"]
        if mode == "voice" and not probs and body.get("answerable") is not False and not spoken.strip():
            probs.append("no spoken answer")
        if spoken and re.search(r"\*\*|₹|\|", spoken):
            probs.append("spoken answer has screen marks")
        if not probs and len(nums) == 1 and spoken and "on your screen" not in spoken.split(".")[0]:
            v = star(nums[0][1], nums[0][2])
            if v is not None and not spoken_has(spoken, float(v)):
                probs.append(f"spoken answer lacks {v:,.2f}")
        if "figures are on your screen" in spoken.lower() and body.get("answerable"):
            probs.append("spoken figures withheld")
        rec = {"id": item["id"], "area": item["area"], "q": item["q"], "ok": not probs, "problems": probs,
               "answer": body.get("answer", "")[:500], "spoken": spoken, "answerable": body.get("answerable")}
        print(f"{rec['id']:4} {'PASS' if rec['ok'] else 'FAIL'} {rec['area']:11} {rec['q'][:64]:64} | {spoken[:150]}"
              + ("" if rec["ok"] else f"\n      <- {'; '.join(probs)} | {rec['answer'][:200]!r}"), flush=True)
        return rec


async def main_async(a):
    items = [b for b in BANK if not a.area or b["area"].lower().startswith(a.area.lower())]
    if a.only:
        keep = {int(x) for x in a.only.split(",")}
        items = [b for b in items if b["id"] in keep]
    from app.main import app
    sem = asyncio.Semaphore(a.workers)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bank", timeout=300) as client:
            res = await asyncio.gather(*(one(client, b, sem, a.mode) for b in items))
    passed = sum(r["ok"] for r in res)
    (HERE / f"bank_report_{a.mode}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    if not a.area and not a.only:
        areas: dict[str, list[int]] = {}
        for r in res:
            areas.setdefault(r["area"], [0, 0])
            areas[r["area"]][0] += r["ok"]
            areas[r["area"]][1] += 1
        cell = lambda s: str(s).replace("|", "/").replace("\n", " ")  # noqa: E731
        voice = a.mode == "voice"
        lines = [f"# Stress-test questions{', voice mode (typed)' if voice else ''} — {passed}/{len(res)} passed", "",
                 f"Run {time.strftime('%d %B %Y')}, in-process, every question asked in {a.mode} mode. Expected figures are "
                 "recomputed from the locked star schema (`DCB_Star_Schema/dcb_star_schema.sqlite`) by queries written "
                 "independently of the chatbot and must appear in the answer"
                 + ("; every answerable question must also get a spoken answer with no screen marks, carrying the single "
                    "figure it asks for" if voice else "") + f". Re-run: `python tests/run_bank.py --mode {a.mode}` in `dcb-ai-app`.", "",
                 "| Area | Passed |", "|---|---|"] + [f"| {k} | {v[0]}/{v[1]} |" for k, v in areas.items()]
        lines += ["", f"| # | Area | Question | {'Spoken answer' if voice else 'Answer (headline)'} | Result |", "|---|---|---|---|---|"]
        lines += [f"| {r['id']} | {r['area']} | {cell(r['q'])} | {cell((r['spoken'] if voice else r['answer'].split(chr(10))[0].replace('**', ''))[:180])} | "
                  f"{'PASS' if r['ok'] else 'FAIL: ' + cell('; '.join(r['problems']))} |" for r in res]
        MD[a.mode].write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(res)} passed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--mode", choices=["voice", "text"], default="voice")
    asyncio.run(main_async(ap.parse_args()))
