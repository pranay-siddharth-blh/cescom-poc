#!/usr/bin/env python3
"""
Automated frontend regression runner for the Holenarasipura DCB NL2SQL chatbot.
Loads test_questions.json, drives the chat UI in a real browser (Playwright),
submits each question, captures the answer + a screenshot, flags likely
failures, and writes results.json / results.md.

BEFORE RUNNING: edit CONFIG below with your frontend's real selectors.
SETUP: pip install playwright && playwright install chromium
USAGE: python run_frontend_tests.py --url http://localhost:3000 --headless
"""
import argparse, json
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

CONFIG = {
    # app/static/chat.html: <input id="box"> (no type attribute -- the
    # original input[type='text'] fallback in this file never matched it).
    "chat_input_selector": "#box",
    # <button id="send">Send</button> -- submits via fetch('/api/ask') SSE.
    "send_button_selector": "#send",
    # box.addEventListener('keydown', e => { if (e.key==='Enter') send(); })
    # exists too, but the button click is more explicit/robust for a script.
    "send_with_enter": False,
    # addBubble(text, who) sets outer wrapper className = 'msg ' + who
    # ('user' or 'bot') and always creates an inner '.bubble' div -- this
    # selector matches only assistant replies, never the echoed question.
    "response_bubble_selector": ".msg.bot .bubble",
    "new_response_timeout_ms": 30000,
    "settle_delay_ms": 800,
    # send() in chat.html sets sendBtn.disabled = true synchronously at the
    # start and only sets it back to false after the SSE stream fully ends
    # and renderMeta() has run (success or error alike) -- the one DOM
    # signal that actually means "this answer is complete", used below
    # instead of a response-bubble-count change (see note in run()).
    "send_button_selector_enabled_state": "#send:not([disabled])",
}

ERROR_MARKERS = [
    "unable to answer", "i don't know", "i couldn't", "no data found",
    "error", "something went wrong", "not able to", "no results",
    "could not find", "no matching",
]

def load_questions(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def wait_for_response_complete(page, timeout_ms):
    """This app's send() (app/static/chat.html) creates the empty bot
    bubble SYNCHRONOUSLY, before the network request even starts, so a
    response-bubble-count check (the original approach here) resolves
    almost instantly -- while the SSE stream is still filling the answer
    in, sometimes for several seconds. Waiting on the count would capture
    a half-streamed or still-empty answer and mislabel it FAIL. The
    button's own disabled state is the reliable signal: chat.html sets
    sendBtn.disabled = true at the very start of send() and only clears
    it after the SSE stream ends and renderMeta() has appended the final
    meta/technical-details block -- true completion, success or error."""
    page.wait_for_selector(CONFIG["send_button_selector"] + "[disabled]", timeout=2000)
    page.wait_for_selector(CONFIG["send_button_selector_enabled_state"], timeout=timeout_ms)

def get_last_response_text(page):
    bubbles = page.locator(CONFIG["response_bubble_selector"])
    last = bubbles.nth(bubbles.count() - 1)
    return last.inner_text()

def looks_like_failure(answer_text: str) -> bool:
    low = answer_text.lower()
    return any(marker in low for marker in ERROR_MARKERS) or len(answer_text.strip()) == 0

def run(url, questions, headless, out_dir: Path):
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        for q in questions:
            record = {
                "id": q["id"], "category": q.get("category"), "question": q["question"],
                "target_tariff_code": q.get("target_tariff_code"),
                "target_location": q.get("target_location"), "notes": q.get("notes"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            try:
                box = page.locator(CONFIG["chat_input_selector"]).first
                box.click()
                box.fill(q["question"])
                if CONFIG["send_with_enter"]:
                    box.press("Enter")
                else:
                    page.locator(CONFIG["send_button_selector"]).first.click()
                wait_for_response_complete(page, CONFIG["new_response_timeout_ms"])
                page.wait_for_timeout(CONFIG["settle_delay_ms"])
                answer = get_last_response_text(page)
                record["answer"] = answer
                record["status"] = "FAIL" if looks_like_failure(answer) else "OK"
                shot_path = out_dir / f"q{q['id']:02d}.png"
                page.screenshot(path=str(shot_path))
                record["screenshot"] = shot_path.name
            except PWTimeout:
                record["answer"] = ""
                record["status"] = "TIMEOUT"
            except Exception as e:
                record["answer"] = ""
                record["status"] = "ERROR"
                record["error"] = str(e)
            print(f"[{record['status']:7}] #{record['id']:02d} {record['question'][:70]}")
            results.append(record)
        browser.close()
    return results

def write_report(results, out_dir: Path):
    (out_dir / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    n_ok = sum(1 for r in results if r["status"] == "OK")
    n_total = len(results)
    lines = ["# Frontend regression run — Holenarasipura DCB chatbot", "",
              f"**{n_ok}/{n_total} passed, {n_total - n_ok} flagged for review**", ""]
    for r in results:
        lines.append(f"## #{r['id']:02d} [{r['status']}] — {r['category']}")
        lines.append(f"**Q:** {r['question']}")
        if r.get("target_tariff_code"):
            lines.append(f"*Expected category:* `{r['target_tariff_code']}`  *Location:* {r.get('target_location', '')}")
        if r.get("notes"):
            lines.append(f"*Why this is a good edge case:* {r['notes']}")
        lines.append(f"**A:** {r.get('answer', '(no answer captured)')[:1200]}")
        if r.get("error"):
            lines.append(f"*Error:* {r['error']}")
        lines.append("")
    (out_dir / "results.md").write_text("\n".join(lines), encoding="utf-8")

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--questions", default="test_questions.json")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--out", default="test_run_results")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)
    questions = load_questions(Path(args.questions))
    results = run(args.url, questions, args.headless, out_dir)
    write_report(results, out_dir)
    n_ok = sum(1 for r in results if r["status"] == "OK")
    print(f"\nDone. {n_ok}/{len(results)} OK.")
    print(f"Report: {out_dir / 'results.md'}  |  Raw: {out_dir / 'results.json'}")

if __name__ == "__main__":
    main()
