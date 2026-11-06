"""
Follow-up stress test (ported from the RDPR POC's tests/run_followup_stress.py, read-only reference): plays every
conversation in tests/followup_bank.py, keeping the context exactly as the page does (the last question answered from
the data -- `asked_as` or the question itself -- is sent as `previous` with the next one; a refusal or a definition keeps
the earlier context; voice turns use mode "voice"; stream turns use the SSE endpoint the page uses). Every turn is
checked for: taken / not taken as a follow-up, the figures recomputed from the locked star schema, and (voice) the
spoken answer naming the place.

In-process by default (no server needed); --url plays against a running server instead.
Writes tests/followup_stress_report.json and ../reports/FOLLOWUP_STRESS_TEST.md.

usage (from dcb-ai-app):  python tests/run_followup_stress.py [--url http://127.0.0.1:8010] [--only 3,17] [--area Random]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

os.environ["DCB_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["VOICE_RATE_LIMIT_PER_MINUTE"] = "100000"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import httpx  # noqa: E402

from tests.followup_bank import CONVERSATIONS  # noqa: E402
from tests.run_stress import check  # noqa: E402

REPORT = HERE / "followup_stress_report.json"
MD = HERE.parent.parent / "reports" / "FOLLOWUP_STRESS_TEST.md"


async def post(client, body: dict) -> dict:
    try:
        r = await client.post("/api/ask_once", json=body)
    except Exception as exc:  # noqa: BLE001
        return {"answer": f"!!! request failed: {exc}", "answerable": None}
    if r.status_code >= 400:
        return {"answer": f"!!! HTTP {r.status_code}: {r.text[:300]!r}", "answerable": None, "http_status": r.status_code}
    return r.json()


async def post_sse(client, body: dict) -> dict:
    """The page's endpoint: events in order; the result is rebuilt from them like the page does."""
    events, answer, out = [], "", {}
    try:
        r = await client.post("/api/ask", json=body)
        raw = r.text
    except Exception as exc:  # noqa: BLE001
        return {"answer": f"!!! stream failed: {exc}", "answerable": None, "events": []}
    for frame in raw.split("\n\n"):
        ev = data = ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if not ev:
            continue
        d = json.loads(data or "{}")
        events.append(ev)
        if ev == "followup":
            out["followup_event"] = d.get("asked_as", "")
        elif ev == "answer_chunk":
            answer += d.get("text", "")
        elif ev == "done":
            out.update(answerable=d.get("answerable"), asked_as=d.get("asked_as", ""), context=d.get("context", False),
                       spoken_answer=d.get("spoken_answer", ""))
    out.update(answer=answer, events=events)
    return out


def place_word(tn: dict) -> str:
    return next((alts[0] for k, *rest in tn["checks"] if k == "text" for alts in rest), "")


async def play(client, c: dict, sem: asyncio.Semaphore) -> list[dict]:
    async with sem:
        previous, rows = "", []
        for i, tn in enumerate(c["turns"], 1):
            sent = tn.get("previous", previous)
            body = {"question": tn["q"], **({"previous": sent} if sent else {}), **({"mode": "voice"} if tn.get("voice") else {})}
            t0 = time.time()
            ans = await (post_sse(client, body) if tn.get("sse") else post(client, body))
            asked = ans.get("asked_as") or ""
            problems = check(tn, ans)
            if ans.get("http_status") or str(ans.get("answer", "")).startswith("!!!"):
                problems.append("request failed")
            if tn["follow"] is True and not asked:
                problems.append("not taken as a follow-up")
            if tn["follow"] is False and asked:
                problems.append(f"wrongly taken as a follow-up: {asked!r}")
            if tn.get("voice") and ans.get("answerable"):
                said, name = (ans.get("spoken_answer") or "").lower(), place_word(tn)
                if not said:
                    problems.append("no spoken answer")
                elif name and name not in said:
                    problems.append(f"spoken answer does not name {name!r}")
            if tn.get("sse"):
                ev = ans.get("events", [])
                if asked and (ans.get("followup_event") != asked or "followup" not in ev
                              or "answer_chunk" in ev[:ev.index("followup")]):
                    problems.append("no followup event before the answer")
                if not asked and "followup" in ev:
                    problems.append("followup event for a question asked as typed")
            rows.append({"turn": i, "q": tn["q"], "previous": sent, "asked_as": asked, "follow": tn["follow"],
                         "status": "PASS" if not problems else "FAIL", "problems": problems,
                         "answer": ans.get("answer", ""), "spoken": ans.get("spoken_answer", ""),
                         "secs": round(time.time() - t0, 2)})
            if ans.get("context"):
                previous = asked or tn["q"]
        return rows


async def main_async(a) -> None:
    convs = CONVERSATIONS
    if a.only:
        ids = {int(x) for x in a.only.split(",")}
        convs = [c for c in convs if c["id"] in ids]
    if a.area:
        convs = [c for c in convs if c["area"].lower().startswith(a.area.lower())]
    sem = asyncio.Semaphore(a.workers)
    t0 = time.time()
    if a.url:
        async with httpx.AsyncClient(base_url=a.url, timeout=300) as client:
            played = await asyncio.gather(*(play(client, c, sem) for c in convs))
    else:
        from app.main import app
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://followup", timeout=300) as client:
                played = await asyncio.gather(*(play(client, c, sem) for c in convs))
    results = []
    for c, rows in zip(convs, played):
        results.append({"id": c["id"], "area": c["area"], "turns": rows})
        bad = [r for r in rows if r["status"] != "PASS"]
        print(f"{'PASS' if not bad else 'FAIL'} {c['id']:3d} [{c['area']}] {len(rows)} turns", flush=True)
        for r in bad:
            print(f"      turn {r['turn']}: {r['q']!r}  previous={r['previous'][:90]!r}\n        asked_as={r['asked_as']!r}\n"
                  f"        {r['problems']}\n        -> {r['answer'][:240]!r}", flush=True)
    turns = [r for c in results for r in c["turns"]]
    passed = sum(r["status"] == "PASS" for r in turns)
    follow = [r for r in turns if r["follow"] is True]
    summary = {"conversations": len(results),
               "conversations_passed": sum(all(r["status"] == "PASS" for r in c["turns"]) for c in results),
               "turns": len(turns), "turns_passed": passed, "follow_ups": len(follow),
               "follow_ups_passed": sum(r["status"] == "PASS" for r in follow), "seconds": round(time.time() - t0, 1)}
    print(f"\n{summary['turns_passed']}/{summary['turns']} turns passed ({summary['follow_ups_passed']}/{summary['follow_ups']} "
          f"follow-ups), {summary['conversations_passed']}/{summary['conversations']} conversations fully correct, "
          f"{summary['seconds']} s")
    if a.only or a.area:
        return
    REPORT.write_text(json.dumps({"summary": summary, "results": results}, indent=1, ensure_ascii=False), encoding="utf-8")
    write_md(summary, results)


def write_md(summary: dict, results: list[dict]) -> None:
    out = ["# Follow-up stress test\n",
           f"**{summary['turns_passed']} of {summary['turns']} turns correct** ({summary['follow_ups_passed']} of "
           f"{summary['follow_ups']} follow-ups; {summary['conversations_passed']} of {summary['conversations']} conversations "
           f"fully correct), {time.strftime('%d %B %Y')}.\n",
           "Each conversation is played as the page plays it: the last question answered from the data is sent as "
           "`previous`, a refusal or a definition keeps the earlier context. A follow-up must be taken as one (\"Taken as\" "
           "below), a whole question must not, and every figure is recomputed from the locked star schema "
           "(`DCB_Star_Schema/dcb_star_schema.sqlite`) by SQL written independently of the chatbot. Bank: "
           "`dcb-ai-app/tests/followup_bank.py`; re-run: `python tests/run_followup_stress.py` in `dcb-ai-app`.\n"]
    area = None
    for c in results:
        if c["area"] != area:
            area = c["area"]
            out.append(f"\n## {area}\n")
        out.append(f"\n**Conversation {c['id']}**\n\n| # | Asked | Taken as | Result |\n|---|---|---|---|")
        for r in c["turns"]:
            res = r["status"] if r["status"] == "PASS" else "FAIL: " + "; ".join(r["problems"]).replace("|", "/")
            out.append(f"| {r['turn']} | {r['q'].replace('|', '/')} | {(r['asked_as'] or '—').replace('|', '/')} | {res} |")
    MD.write_text("\n".join(out) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--area", default="")
    ap.add_argument("--workers", type=int, default=6)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
