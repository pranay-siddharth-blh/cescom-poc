"""
Voice stress test (ported from the RDPR POC's tests/run_voice_stress.py, 2026-10-05): every speakable question of the
expanded bank (tests/voice_bank.py: the typed stress bank plus the RDPR / KPCL question kinds adapted to DCB) is SPOKEN
by the Windows Indian-English voices Ravi and Heera, sent through the real speech-to-text (/api/voice/transcribe ->
qwen3-asr on the gateway, VPN needed) and then through this app's voice pipeline (/api/ask_once with mode "voice"),
in-process, so the per-IP rate limits don't throttle the run.

Each spoken question is checked exactly like the typed stress test (expected figures recomputed from the locked star
schema by independent SQL) and, for single-figure answers, the spoken answer must carry that figure (as said: "354.16
crore rupees", "about 2.39 lakh", "93.62 percent"). Every answerable question must get a spoken answer. A failure is
re-asked as typed text: if the typed question passes, the problem is the voice layer (hearing / clean-up); if it fails
too, it is the pipeline.

Two speaking styles per question:
  A  Ravi (Indian English, male):    letters ("L T four A", "D C B", "M N R"), "F Y twenty twenty four twenty five"
  B  Heera (Indian English, female): words ("financial year twenty twenty four to twenty twenty five", "sub division"),
                                     "low tension" never (officers say the codes), tariff letters as words

usage (from dcb-ai-app):  python tests/run_voice_stress.py [--only 4,136] [--failed] [--limit N] [--workers 4]
Writes tests/voice_stress_report.json and ../reports/VOICE_STRESS_TEST_QUESTIONS.md.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

os.environ["DCB_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["VOICE_RATE_LIMIT_PER_MINUTE"] = "100000"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import httpx  # noqa: E402

from tests.run_stress import check, star  # noqa: E402  (same checks as the typed stress test)
from tests.voice_bank import BANK  # noqa: E402

WAVS = Path(os.environ.get("VOICE_WAV_DIR", str(Path(os.environ.get("TEMP", HERE)) / "cescom_voice_wavs")))
REPORT = HERE / "voice_stress_report.json"
MD = HERE.parent.parent / "reports" / "VOICE_STRESS_TEST_QUESTIONS.md"


def unspeakable(q: str) -> bool:
    """Typed-only cases: SQL, symbols, keyboard mashing and Kannada script (the test voices speak English only)."""
    return bool(re.search(r"\*|;|DROP TABLE|DELETE FROM|asdfghjkl|^\W+$|@|[ಀ-೿]", q))


# ------------------------------------------------------------------ how a person says a written question
ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
ROMAN = {"i": "one", "ii": "two", "iii": "three"}


def two(n: int) -> str:
    return ONES[n] if n < 20 else TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else "")


def year(y: int) -> str:
    return ("two thousand" + (f" {ONES[y - 2000]}" if y > 2000 else "")) if 2000 <= y <= 2009 else f"{two(y // 100)} {two(y % 100)}"


def fy_words(y1: int, y2: int, style: str) -> str:
    return f"{year(y1)} {two(y2 % 100)}" if style == "A" else f"{year(y1)} to {year(2000 + y2 % 100)}"


def code_words(m: re.Match, style: str) -> str:
    """LT-4(a) -> 'L T four A' (A) / 'L T four a' (B); HT-2(b)(ii) -> 'H T two B two'."""
    fam, n, rest = m.group(1).upper() + "T", int(m.group(2)), m.group(3) or ""     # "L T four", never "L four"
    parts = [ROMAN.get(p.lower(), p.upper() if style == "A" else p.lower()) for p in re.findall(r"([a-zA-Z]+)", rest)]
    return " ".join([" ".join(fam), ONES[n]] + parts)


LETTERS = {"DCB": "D C B", "MNR": "M N R", "FPPCA": "F P P C A", "AT&C": "A T and C", "DC/MNR": "D C and M N R",
           "CESC": "C E S C", "IP": "I P", "EV": "E V", "LT": "L T", "HT": "H T", "DC": "D C"}


def speakify(q: str, style: str) -> str:
    s = q
    s = re.sub(r"\b(?:FY|fy)\s*(\d{4})-(\d{2,4})\b",
               lambda m: ("F Y " if style == "A" else "financial year ") + fy_words(int(m.group(1)), int(m.group(2)), style), s)
    s = re.sub(r"\b(?:FY|fy)\s*(\d{2})\b(?!-)", lambda m: ("F Y " if style == "A" else "financial year ") + two(int(m.group(1))), s)
    s = re.sub(r"\b(?:FY|fy)\s*(\d{4})\b(?!-)", lambda m: ("F Y " if style == "A" else "financial year ") + year(int(m.group(1))), s)
    s = re.sub(r"\b(\d{4})-(\d{2,4})\b", lambda m: fy_words(int(m.group(1)), int(m.group(2)), style), s)
    s = re.sub(r"\b(in|since|from|by|the|of) (\d{4})\b", lambda m: f"{m.group(1)} {year(int(m.group(2)))}", s)
    # tariff codes as typed: LT-4(a), l.t.-4, ht2, lt1, HT - 2
    s = re.sub(r"\b([lLhH])\.?\s?[tT]\.?\s?-?\s?(\d{1,2})((?:\s?\([a-zA-Z]{1,3}\))*)", lambda m: code_words(m, style), s)
    s = s.replace("hn pura", "H N pura").replace("dmd", "demand").replace("colection efficiancy", "collection efficiency")
    # typed typos are said as the words they stand for (a speaker never mis-spells aloud)
    for a, b in (("colection", "collection"), ("efficency", "efficiency"), ("effciency", "efficiency"), ("demnad", "demand"),
                 ("arrers", "arrears"), ("instalations", "installations")):
        s = re.sub(rf"\b{a}\b", b, s, flags=re.I)
    s = s.replace("sub-division", "sub division" if style == "B" else "sub-division")
    for a, b in LETTERS.items():
        s = re.sub(rf"(?<![\w&/]){re.escape(a)}(?![\w&/])", b, s)
    for a, b in (("%", " percent"), ("&", " and "), (" vs ", " versus "), ("year-wise", "year wise"), ("category-wise", "category wise")):
        s = s.replace(a, b)
    return re.sub(r"\s{2,}", " ", s).strip()


# ------------------------------------------------------------------ running
NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(\d+))?\s*(crore|lakh)?")


def spoken_has(spoken: str, expected: float) -> bool:
    """The figure is in what the voice says, at the precision it is said ("about 2.39 lakh" for 239,310)."""
    for w, f, scale in NUM.findall(spoken or ""):
        unit = {"crore": 1e7, "lakh": 1e5}.get(scale, 1)
        v = float(w.replace(",", "") + ("." + f if f else "")) * unit
        step = 10 ** -len(f) * unit if f else unit
        if abs(v - expected) <= max(abs(expected) * 0.006, step * 0.51):
            return True
    return False


def make_wavs(trials: list[dict]) -> None:
    WAVS.mkdir(parents=True, exist_ok=True)
    todo = [{"path": tr["wav"], "voice": tr["voice"], "text": tr["said"]} for tr in trials if not Path(tr["wav"]).exists()]
    if not todo:
        return
    lst = WAVS / "phrases.json"
    lst.write_text(json.dumps(todo), encoding="utf-8")
    print(f"speaking {len(todo)} questions to WAV ...", flush=True)
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "make_wavs.ps1"),
                    "-List", str(lst)], check=True)


_GENERIC = {"sub-division", "division", "section", "tariff", "category", "all"}


def pick_option(options: list[dict], meant: str) -> int:
    """Which 'Did you mean ...?' option the speaker meant (the same word match as the panel's spoken reply)."""
    tx = " " + re.sub(r"[^a-z0-9-()]+", " ", meant.lower()) + " "
    meant_years = set(re.findall(r"(?:19|20)(\d{2})-(\d{2})", meant))
    meant_fy_short = set(re.findall(r"\bfy\s?(\d{2})\b", meant.lower()))

    def score(o: dict) -> int:
        label = o["label"].lower()
        words = [w for w in re.findall(r"[a-z]{4,}", label) if w not in _GENERIC]
        years = set(re.findall(r"(?:19|20)(\d{2})-(\d{2})", o["label"]))
        codes = re.findall(r"[lh]t-\d+(?:\([a-z]+\))*", label)
        sc = sum(f" {w} " in tx for w in words) + 2 * len(years & meant_years)
        sc += 3 * sum(c in tx for c in codes) - (2 if label.startswith("all of") and re.search(r"[lh]t-\d", tx) else 0)
        # "FY24" means the year ending March 2024 (2023-24), as the router reads it
        sc += 2 * sum(1 for y in years if y[1] in meant_fy_short)
        return sc
    scores = [score(o) for o in options]
    return scores.index(max(scores))


async def run_trial(client, tr: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        audio = Path(tr["wav"]).read_bytes()
        r = await client.post(f"/api/voice/transcribe?session_id=t{tr['item']['id']}{tr['style']}", content=audio,
                              headers={"content-type": "audio/wav"})
        body = r.json() if r.status_code == 200 else {}
        heard = body.get("text", "") if r.status_code == 200 else f"!!! transcribe {r.status_code}"
        if body.get("rejected"):
            heard = f"!!! rejected {body['rejected']}: {body.get('heard', '')}"
        conf = body.get("confirm") or {}
        confirm, question = conf.get("prompt", ""), body.get("text", "")
        if conf.get("options"):                       # the officer taps the option they meant, as on the voice panel
            i = pick_option(conf["options"], tr["item"]["q"])
            question = conf["options"][i]["question"]
            confirm += f" -> {conf['options'][i]['label']}"
        a = await client.post("/api/ask_once", json={"question": question or "(silence)", "mode": "voice"})
        ans = a.json() if a.headers.get("content-type", "").startswith("application/json") else {"answer": a.text}
        if a.status_code >= 400:
            ans = {"answer": str(ans.get("detail", "")), "answerable": False}
        problems = check(tr["item"], ans)
        nums = [c for c in tr["item"]["checks"] if c[0] == "num"]
        spoken = ans.get("spoken_answer") or ""
        if not problems and len(nums) == 1 and spoken and "on your screen" not in spoken.split(".")[0]:
            v = star(nums[0][1], nums[0][2])
            if v is not None and not spoken_has(spoken, float(v)):
                problems.append(f"spoken answer lacks {v:,.2f}")
        if not problems and ans.get("answerable") is not False and not spoken.strip():
            problems.append("no spoken answer")
        if not problems and re.search(r"\*\*|₹|\|", spoken):
            problems.append("spoken answer has screen marks")
        out = {"id": tr["item"]["id"], "style": tr["style"], "voice": tr["voice"], "area": tr["item"]["area"], "q": tr["item"]["q"],
               "said": tr["said"], "asr": body.get("heard", ""), "heard": heard, "asked": question, "problems": problems,
               "answer": (ans.get("answer") or "")[:400], "spoken": spoken, "confirm": confirm,
               "status": "PASS" if not problems else "FAIL", "secs": round(time.time() - t0, 2)}
        if problems:                                  # is it the voice layer or the pipeline?
            typed = await client.post("/api/ask_once", json={"question": tr["item"]["q"]})
            typed = typed.json() if typed.status_code < 400 else {"answer": "", "answerable": False}
            out["typed_status"] = "PASS" if not check(tr["item"], typed) else "FAIL"
        print(f"{out['status']} {out['id']}{tr['style']} [{out['area']}] heard: {heard[:110]}" + (f"  [confirmed: {confirm}]" if confirm else "")
              + ("" if not problems else f"  <-- {problems} (typed: {out['typed_status']})\n      spoken: {spoken[:200]}"), flush=True)
        return out


def trials_for(items, prev, failed_only) -> list[dict]:
    out = []
    for b in items:
        for style, voice in (("A", "Ravi"), ("B", "Heera")):
            key = f"{b['id']}{style}"
            if failed_only and prev.get(key, {}).get("status") == "PASS" and prev[key].get("q") == b["q"]:
                continue
            said = speakify(b["q"], style)
            out.append({"item": b, "style": style, "voice": voice, "said": said,
                        "wav": str(WAVS / f"{key}_{voice}_{hashlib.md5(said.encode()).hexdigest()[:10]}.wav")})
    return out


async def main_async(a) -> None:
    items = [b for b in BANK if not unspeakable(b["q"])]
    if a.only:
        keep = {int(x) for x in a.only.split(",")}
        items = [b for b in items if b["id"] in keep]
    if a.area:
        items = [b for b in items if b["area"].lower().startswith(a.area.lower())]
    prev = json.loads(REPORT.read_text(encoding="utf-8"))["results"] if REPORT.exists() else {}
    trials = trials_for(items, prev, a.failed)
    if a.limit:
        trials = trials[: a.limit]
    make_wavs(trials)
    if a.wavs_only:
        return
    from app.main import app
    sem = asyncio.Semaphore(a.workers)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://voice", timeout=300) as client:
            results = await asyncio.gather(*(run_trial(client, tr, sem) for tr in trials))
    merged = prev if (a.failed or a.only or a.area) else {}
    merged.update({f"{r['id']}{r['style']}": r for r in results})
    valid = {f"{b['id']}{s}" for b in BANK if not unspeakable(b["q"]) for s in "AB"}
    merged = {k: v for k, v in merged.items() if k in valid}
    passed = sum(r["status"] == "PASS" for r in merged.values())
    REPORT.write_text(json.dumps({"passed": passed, "total": len(merged), "results": merged}, indent=1, ensure_ascii=False),
                      encoding="utf-8")
    write_md(merged)
    print(f"\n{passed}/{len(merged)} spoken questions passed (this run: {sum(r['status'] == 'PASS' for r in results)}/{len(results)})")


def write_md(res: dict) -> None:
    rows = sorted(res.values(), key=lambda r: (r["id"], r["style"]))
    passed = sum(r["status"] == "PASS" for r in rows)
    voice_miss = sum(r["status"] == "FAIL" and r.get("typed_status") == "PASS" for r in rows)
    pipe_miss = sum(r["status"] == "FAIL" and r.get("typed_status") != "PASS" for r in rows)
    cell = lambda s: str(s).replace("|", "/").replace("\n", " ")  # noqa: E731
    lines = ["# Voice stress test (spoken questions)", "",
             f"**{passed} of {len(rows)} spoken questions answered correctly** ({time.strftime('%d %B %Y')}). "
             f"Of the misses, {voice_miss} pass when typed (a hearing / clean-up miss) and {pipe_miss} fail typed too.", "",
             "Each question was spoken by a Windows Indian-English voice (A: Ravi, letters and short year forms; B: Heera, "
             "words and long-form years), heard by the real speech-to-text (qwen3-asr), cleaned up and answered by the voice "
             "pipeline. Where the assistant asked \"Did you mean ...?\", the option the speaker meant was tapped, as on the "
             "voice panel. Expected figures are recomputed from the locked star schema; a single figure must also be in what "
             "the voice says. Re-run: `python tests/run_voice_stress.py` in `dcb-ai-app` (VPN needed).", "",
             "| # | Area | Spoken | Heard (cleaned) | Spoken answer | Result |", "|---|---|---|---|---|---|"]
    for r in rows:
        res_txt = r["status"] if r["status"] == "PASS" else f"FAIL: {'; '.join(r['problems'])} (typed: {r.get('typed_status', '?')})"
        if r.get("confirm"):
            res_txt += f" (confirmed: {r['confirm']})"
        lines.append(f"| {r['id']}{r['style']} | {cell(r['area'])} | {cell(r['said'])} | {cell(r['heard'])} | "
                     f"{cell(r['spoken'][:160])} | {cell(res_txt)} |")
    MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--area", default="")
    ap.add_argument("--failed", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--wavs-only", action="store_true")
    asyncio.run(main_async(ap.parse_args()))
