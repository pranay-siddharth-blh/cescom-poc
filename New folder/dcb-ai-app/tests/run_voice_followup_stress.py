"""
Spoken follow-up stress test (ported from the RDPR POC's tests/run_voice_followup_stress.py, read-only reference): the
follow-up conversations of tests/followup_bank.py SPOKEN by the Windows Indian-English voices Ravi (style A: "F Y twenty
twenty four twenty five", "L T four") and Heera (style B: "financial year twenty twenty four to twenty twenty five",
"sub division"), sent through the real speech-to-text (/api/voice/transcribe -> qwen3-asr, VPN needed) and then asked in
voice mode with the previous question the page holds at that point, in-process.

Each turn is played on its own: the `previous` it is asked with is the one the typed run sent at that turn
(tests/followup_stress_report.json -- run tests/run_followup_stress.py first), so one mishearing does not throw the rest
of a conversation off. Checks: the spoken follow-up is not dropped as a fragment, it is taken as a follow-up when it
should be, the figures (recomputed from the locked star schema), the spoken answer naming the place and saying the
figure. A failure is re-asked as typed text: a typed pass means the miss is in hearing, not in following up.

usage (from dcb-ai-app):  python tests/run_voice_followup_stress.py [--random 30] [--workers 4] [--only 3,17] [--wavs-only]
Writes tests/voice_followup_stress_report.json and ../reports/VOICE_FOLLOWUP_STRESS_TEST.md.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
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
from tests.run_stress import check, star  # noqa: E402
from tests.run_voice_stress import WAVS, make_wavs, pick_option, speakify, spoken_has, unspeakable  # noqa: E402

TYPED = HERE / "followup_stress_report.json"
REPORT = HERE / "voice_followup_stress_report.json"
MD = HERE.parent.parent / "reports" / "VOICE_FOLLOWUP_STRESS_TEST.md"


def place_word(tn: dict) -> str:
    return next((alts[0] for k, *rest in tn["checks"] if k == "text" for alts in rest), "")


async def run_trial(client, tr: dict, sem: asyncio.Semaphore) -> dict:
    tn = tr["turn"]
    async with sem:
        t0 = time.time()
        r = await client.post(f"/api/voice/transcribe?session_id=f{tr['key']}", content=Path(tr["wav"]).read_bytes(),
                              headers={"content-type": "audio/wav"})
        body = r.json() if r.status_code == 200 else {}
        question, conf, problems = body.get("text", ""), body.get("confirm") or {}, []
        if r.status_code != 200:
            problems.append(f"transcribe failed ({r.status_code})")
        if body.get("rejected"):
            problems.append(f"dropped by the voice guard ({body['rejected']})")
        if conf.get("options"):                       # the officer taps the option they meant
            i = pick_option(conf["options"], tn["q"])
            question = conf["options"][i]["question"]
        payload = {"question": question or "(silence)", "mode": "voice",
                   **({"previous": tr["previous"]} if tr["previous"] else {})}
        a = await client.post("/api/ask_once", json=payload)
        ans = a.json() if a.status_code < 400 else {"answer": f"!!! HTTP {a.status_code}", "answerable": None}
        problems += check(tn, ans)
        asked = ans.get("asked_as") or ""
        if tn["follow"] is True and not asked:
            problems.append("not taken as a follow-up")
        if tn["follow"] is False and asked:
            problems.append(f"wrongly taken as a follow-up: {asked!r}")
        said = (ans.get("spoken_answer") or "").lower()
        if ans.get("answerable") and not problems:
            name = place_word(tn)
            if not said:
                problems.append("no spoken answer")
            elif name and name not in said:
                problems.append(f"spoken answer does not name {name!r}")
            nums = [c for c in tn["checks"] if c[0] == "num"]
            if len(nums) == 1 and said and "on your screen" not in said.split(".")[0]:
                v = star(nums[0][1], nums[0][2])
                if v is not None and not spoken_has(said, float(v)):
                    problems.append(f"spoken answer lacks {v:,.2f}")
        out = {"key": tr["key"], "conv": tr["conv"], "turn": tr["n"], "voice": tr["voice"], "q": tn["q"], "said": tr["said"],
               "asr": body.get("heard", ""), "heard": question, "previous": tr["previous"], "asked_as": asked,
               "problems": problems, "answer": (ans.get("answer") or "")[:300], "spoken": ans.get("spoken_answer", ""),
               "status": "PASS" if not problems else "FAIL", "secs": round(time.time() - t0, 2)}
        if problems:                                  # hearing, or following up?
            typed = await client.post("/api/ask_once", json={"question": tn["q"],
                                                             **({"previous": tr["previous"]} if tr["previous"] else {})})
            tp = check(tn, typed.json() if typed.status_code < 400 else {"answer": "", "answerable": None})
            out["typed_status"] = "PASS" if not tp else "FAIL"
        print(f"{out['status']} {tr['key']} heard: {question[:100]!r}" + (f" -> {asked[:90]!r}" if asked else "")
              + ("" if not problems else f"\n      <-- {problems} (typed: {out['typed_status']})  said: {tr['said']!r}"),
              flush=True)
        return out


def build_trials(a) -> list[dict]:
    typed = {c["id"]: c for c in json.loads(TYPED.read_text(encoding="utf-8"))["results"]}
    walks = [c for c in CONVERSATIONS if c["area"] == "Random walk"][: a.random]
    convs = walks + [c for c in CONVERSATIONS if c["area"] != "Random walk"]
    if a.only:
        keep = {int(x) for x in a.only.split(",")}
        convs = [c for c in convs if c["id"] in keep]
    trials = []
    for c in convs:
        rows = typed[c["id"]]["turns"]
        for n, (tn, row) in enumerate(zip(c["turns"], rows), 1):
            if "previous" in tn or unspeakable(tn["q"]) or row["q"] != tn["q"] or len(tn["q"].split()) == 1:
                continue                              # typed-only: a client's own `previous`, symbols, one word ("demand?",
                                                      # which the voice drops as a fragment by design)
            for style, voice in (("A", "Ravi"), ("B", "Heera")):
                said = speakify(tn["q"], style)
                key = f"{c['id']}.{n}{style}"
                trials.append({"key": key, "conv": c["id"], "n": n, "turn": tn, "voice": voice, "said": said,
                               "previous": row["previous"],
                               "wav": str(WAVS / f"fu{key}_{voice}_{hashlib.md5(said.encode()).hexdigest()[:10]}.wav")})
    return trials


async def main_async(a) -> None:
    trials = build_trials(a)
    make_wavs(trials)
    if a.wavs_only:
        print(f"{len(trials)} spoken turns ready in {WAVS}")
        return
    from app.main import app
    sem = asyncio.Semaphore(a.workers)
    t0 = time.time()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://voice", timeout=300) as client:
            results = await asyncio.gather(*(run_trial(client, tr, sem) for tr in trials))
    passed = sum(r["status"] == "PASS" for r in results)
    fails = [r for r in results if r["status"] != "PASS"]
    hearing = sum(r.get("typed_status") == "PASS" for r in fails)
    summary = {"turns": len(results), "passed": passed, "failed": len(fails), "failed_but_typed_passes": hearing,
               "dropped_as_fragment": sum(any("dropped" in p for p in r["problems"]) for r in results),
               "seconds": round(time.time() - t0, 1)}
    print(f"\n{passed}/{len(results)} spoken follow-up turns passed; of the {len(fails)} misses, {hearing} pass when typed "
          f"(hearing errors); {summary['dropped_as_fragment']} dropped as fragments; {summary['seconds']} s")
    if a.only:
        return
    REPORT.write_text(json.dumps({"summary": summary, "results": results}, indent=1, ensure_ascii=False), encoding="utf-8")
    cell = lambda s: str(s).replace("|", "/").replace("\n", " ")  # noqa: E731
    out = ["# Spoken follow-up stress test\n",
           f"**{passed} of {len(results)} spoken turns correct** ({time.strftime('%d %B %Y')}); of the {len(fails)} misses, "
           f"{hearing} pass when the same turn is typed (the speech was misheard, not the follow-up mishandled). "
           f"{summary['dropped_as_fragment']} spoken follow-ups were dropped as fragments.\n",
           "Each turn of the follow-up conversations (`dcb-ai-app/tests/followup_bank.py`) is spoken by the Windows voices "
           "Ravi (A) and Heera (B), transcribed by the real speech-to-text and asked in voice mode with the previous "
           "question the page holds at that turn. Expected figures are recomputed from the locked star schema. Re-run: "
           "`python tests/run_voice_followup_stress.py` in `dcb-ai-app` (VPN needed).\n",
           "| Turn | Said | Heard | Taken as | Result |", "|---|---|---|---|---|"]
    for r in results:
        res = r["status"] if r["status"] == "PASS" else "FAIL: " + "; ".join(r["problems"]) + \
            (f" (typed: {r.get('typed_status')})" if r.get("typed_status") else "")
        out.append(f"| {r['key']} | {cell(r['said'])} | {cell(r['heard'])} | {cell(r['asked_as'] or '—')} | {cell(res)} |")
    MD.write_text("\n".join(out) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--random", type=int, default=30, help="how many random-walk conversations to speak")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--wavs-only", action="store_true", help="only speak the turns to WAV files (no VPN needed)")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
