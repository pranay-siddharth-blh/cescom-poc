"""
What the voice says, and how a spoken question is cleaned up. No model call anywhere in this module. Ported from the
RDPR POC (which tuned the KPCL voice instance's port of the Meghalaya voice agent) and re-tuned for the CESC DCB data:
six sub-divisions instead of 31 zillas / 238 taluks / 6,099 GPs, spoken tariff codes ("L T four A" -> LT-4(a)), DCB
terms (FPPCA, MNR, AT&C, Gruha Jyothi) and fiscal years 2015-16 to 2025-26.

  spoken_answer(question, answer, route)  -> 1-2 sentences for the ear (also the caption in the voice panel), built from
                                             the answer the pipeline already wrote and verified
  pronounce(text)                         -> the same text made speakable for TTS: fiscal years read naturally, tariff
                                             codes and acronyms spelled out (LT-4(a) -> "L T four A", DCB -> "D C B"),
                                             rupees and percent in words
  clean_transcript(text)                  -> speech-to-text output -> the question the pipeline expects

Why deterministic (as in RDPR and KPCL, not a model rewrite as in the Meghalaya agent): every DCB answer is written by
reviewed code from the query result, with a bold headline that already states the answer. Re-wording it with a model
would add a second round trip and a chance of a wrong spoken figure. Here every spoken figure is the screen figure,
rounded the way an officer says it, and app/voice_guard.py checks that before anything is said.

Rules: the answer first (the headline); at most one supporting sentence that adds a new figure; lists cut to three
items; screen-only notes ("Note: ...", "No year was named ...", "What was counted", "in the table") are not read; a long
answer ends with "The full details are on your screen."
"""
from __future__ import annotations

import re
from contextvars import ContextVar
from functools import lru_cache

MONTHS = {"jan": "January", "feb": "February", "mar": "March", "apr": "April", "may": "May", "jun": "June",
          "jul": "July", "aug": "August", "sep": "September", "oct": "October", "nov": "November", "dec": "December"}
_MONTH_LIST = list(MONTHS.values())

IDENTITY = ("I'm the CESC DCB assistant for Holenarasipura Division. Ask me about demand, collection, arrears or "
            "installations for the division, a sub-division or a tariff category, from 2015-16 to 2025-26.")
SCREEN_NOTE = "The full details are on your screen."
REPORT_NOTE = "The full details, and the report, are on your screen."
FILLERS = ["Let me check that.", "One moment, I'm pulling up the figures.", "Checking the records now."]

# Sentences that only make sense on screen, or that are caveats the officer can read there.
_SCREEN_ONLY = re.compile(
    r"\b(table below|in the table|below\b|chart|scroll|click|download|CSV|View report|"
    r"No (?:financial |fiscal )?year was named|is used\.|What was counted|Changes are against|booklet page|"
    r"Taking this as|The other \d+ are|not comparable|tariff restructuring|synthetic|not official|Please check the spelling|"
    r"Proof of concept)", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9'\"(₹•])")
_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}|\d+)(\.\d+)?(?![\w])")
# a tariff code (LT-4(a), HT-2(b)(ii), LT-3 a (i), LT IPP): never a figure
TARIFF_CODE = re.compile(r"\b[LH]T\s?-\s?\d{1,2}(?:\s?\([A-Za-z]{1,4}\)|\s[a-c](?=\s?\())*(?:\s?\([A-Za-z]{1,4}\))*|\b[LH]T IPP\b")


def _plain(text: str) -> str:
    """Markdown and list marks out; whitespace collapsed."""
    t = re.sub(r"\*\*|__|`", "", text or "")
    t = re.sub(r"^\s*[•\-*]\s+", "", t, flags=re.M)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s.strip()]


def _dates(text: str) -> str:
    """30-Sep-2026 -> 30 September 2026;  2026-09-30 -> 30 September 2026;  MAR-2025 / Mar 2025 -> March 2025."""
    text = re.sub(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b",
                  lambda m: f"{int(m.group(3))} {_MONTH_LIST[int(m.group(2)) - 1]} {m.group(1)}"
                  if 1 <= int(m.group(2)) <= 12 else m.group(0), text)
    text = re.sub(r"\b(\d{1,2})-([A-Za-z]{3})-(\d{4})\b",
                  lambda m: f"{int(m.group(1))} {MONTHS.get(m.group(2).lower(), m.group(2))} {m.group(3)}", text)
    return re.sub(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?[- ](\d{4})\b",
                  lambda m: f"{MONTHS[m.group(1).lower()[:3]]} {m.group(2)}", text, flags=re.I)


def _trim(v: float, places: int) -> str:
    s = f"{v:,.{places}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _count_words(v: float) -> str:
    """A big count the way an officer says it: 56,194,893 -> about 5.62 crore; 6,41,900 -> about 6.42 lakh."""
    if v >= 1e7:
        return f"about {_trim(v / 1e7, 2)} crore"
    if v >= 1e5:
        return f"about {_trim(v / 1e5, 2)} lakh"
    return _trim(v, 2)


# ₹124.30 crore / ₹11.85 lakh / ₹30,993 / ₹1,905.41 cr
_RUPEE = re.compile(r"₹\s?(\d[\d,]*(?:\.\d+)?)(?:\s*(crore|cr\b|lakh))?", re.I)
# a bare big number (not part of a rupee figure or a date): 16,301,364 / 6,41,900
_BIG = re.compile(r"(?<![\w.,₹])(\d{1,3}(?:,\d{3}){2,}|\d{1,2}(?:,\d{2}){2,},\d{3})(\.\d+)?(?![\w,]|\.\d)")


def _speak_figures(text: str) -> str:
    """Rupees said as rupees, big counts said in lakh / crore, percent as a word. Only the last decimals change."""
    def rupee(m):
        v = float(m.group(1).replace(",", ""))
        scale = (m.group(2) or "").lower()
        if scale in ("crore", "cr"):
            return f"{_trim(v, 2)} crore rupees"
        if scale == "lakh":
            return f"{_trim(v, 2)} lakh rupees"
        if v >= 1e5:
            return _count_words(v) + " rupees"
        return f"{_trim(v, 2)} rupees"
    text = _RUPEE.sub(rupee, text)
    text = _BIG.sub(lambda m: _count_words(float((m.group(1) + (m.group(2) or "")).replace(",", ""))), text)
    text = re.sub(r"(\d)\s*%", r"\1 percent", text)
    return text


def _parentheses(text: str) -> str:
    """'(₹3,541,649,882.79)' (the exact amount, already said in crore) dropped; '(%)' / '(LT)' dropped; tariff
    sub-parts kept ('LT-4(a)'); '(₹58.65 crore against ₹62.65 crore)' and '(+13.6%)' become comma clauses."""
    text = re.sub(r"\s*\(₹[\d,]+\.\d{2}\)", "", text)
    text = re.sub(r"\s*\((?:kWh|kVA|kW|HP)\)", "", text)
    text = re.sub(r"\s*\(whole division\)", "", text)
    text = re.sub(r"\s+\([a-z]{2,}\)", "", text)                   # "energy (technical) losses" -> "energy losses"
    text = re.sub(r"(\b[LH]T\s?-\s?\d{1,2}(?:\([A-Za-z]{1,4}\))*)\s+\((?:[^()]|\([^()]*\))*\)", r"\1", text)
    text = re.sub(r"(?<![\w)])\s*\((?:[A-Z]{1,5}|%)\)", "", text)
    return re.sub(r"\s+\((?![A-Za-z]{1,4}\))([^()]*)\)|(?<=[.,;:])\s*\(([^()]*)\)",
                  lambda m: f", {m.group(1) or m.group(2)},", text)


def _clean(sentence: str) -> str:
    s = _dates(_plain(sentence))
    s = re.sub(r"\s*→\s*", " to ", s)
    s = re.sub(r"\s+[—–]\s+", ", ", s)
    s = _parentheses(s)
    s = re.sub(r"'([A-Z][\w-]*)'", r"\1", s)
    s = re.sub(r"(?<=[a-z]) %", " percentage", s)                # "collection %" -> "collection percentage"
    s = re.sub(r"\(\s*-(?=\d)", "(down ", s)                     # "(-0.5%)" -> "(down 0.5 percent)"
    s = re.sub(r"(?<![\w-])\+(?=\d)", "up ", s)                  # "+13.6%" -> "up 13.6 percent"
    s = _speak_figures(s)
    s = re.sub(r"\s+([,.;:])", r"\1", s)
    s = re.sub(r",\s*(?=[,.;:!?])", "", s)                       # ", ." / ", ," left by the bracket rules
    s = re.sub(r"\s{2,}", " ", s).strip().rstrip(",;:")
    if s and s[-1] not in ".!?":
        s += "."
    return s


def _paragraphs(answer: str) -> list[str]:
    """Paragraphs that can be read out: no Markdown table, no "Note:" caveat."""
    return [p.strip() for p in re.split(r"\n\s*\n", answer or "")
            if p.strip() and not p.lstrip().startswith(("|", "Note:", "**Note"))]


def _bullets(answer: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^\s*[•\-]\s+(.+)$", answer or "", re.M)]


def _table_rows(answer: str) -> list[list[str]]:
    """The body rows of the first Markdown table (header and rule skipped)."""
    rows = [ln for ln in (answer or "").splitlines() if ln.strip().startswith("|")]
    out = []
    for ln in rows[2:]:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if cells and not set("".join(cells)) <= set("-: "):
            out.append(cells)
    return out


def _headline(answer: str) -> str:
    m = re.match(r"\s*\*\*(.+?)\*\*", answer or "", re.S)
    return m.group(1).strip() if m else ""


def _years_out(text: str) -> str:
    text = TARIFF_CODE.sub(" ", text)
    return re.sub(r"\b(?:FY ?)?(?:19|20)\d{2}(?:-\d{2})?\b|\b\d{1,2}[- ](?:[A-Za-z]{3,9})[- ]\d{4}\b|"
                  r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}\b", " ", text)


def _numbers(text: str) -> set[str]:
    return {a + b for a, b in _NUM.findall(_years_out(text))}


def _list_sentence(items: list[str], head: str) -> str:
    """Up to three list items; if the headline already named the first one, continue with 'followed by'."""
    items = [_plain(i).rstrip(".") for i in items]
    first_name = re.split(r"\s*[—–:]\s*|,", items[0])[0].strip()
    lead = ""
    if first_name and first_name.lower() in head.lower() and len(items) > 1:
        items, lead = items[1:3], "Followed by "
    else:
        items = items[:3]
    # one clause per item: "Konanur — 97.46% (₹... of ...), ..." -> "Konanur, 97.46%"
    items = [re.split(r"\s*\((?![a-z]{1,4}\))|,\s(?=\d)", re.sub(r"\s*[—–]\s*", ", ", i))[0] for i in items]
    body = items[0] if len(items) == 1 else ", ".join(items[:-1]) + (" and " if lead else ", and ") + items[-1]
    return lead + body


def _short(sentence: str, words: int = 30) -> str:
    """A long description cut at a comma or semicolon after about `words` words ("..., and more.")."""
    w = sentence.split()
    if len(w) <= words + 8:
        return sentence
    cut = " ".join(w[:words])
    i = max(cut.rfind(","), cut.rfind(";"))
    if i > len(cut) // 3:
        cut = cut[:i]
    return cut.rstrip(" ,;:") + ", and more."


_STORY = re.compile(r"\b(highest|lowest|ranged|times|grew|higher|lower|together|share|led|top)\b", re.I)
_HEADLINE_ONLY = ("capability", "unknown_name", "out_of_range", "llm_unavailable", "llm_error", "llm_declined",
                  "llm_sql_failed", "llm_mismatch", "llm_unverified")


def spoken_answer(question: str, answer: str, route: str, *, rows: int = 0, has_chart: bool = False,
                  has_report: bool = False) -> str:
    """1-2 sentences to say for `answer` (already verified and shown on screen)."""
    if route == "identity":
        return IDENTITY
    if route.startswith("small_talk:") or route == "greeting":
        from app.small_talk import SPOKEN
        return SPOKEN.get(route.split(":", 1)[-1], SPOKEN["greeting"])
    if not answer:
        return "Sorry, I couldn't answer that just now. Please ask again."
    if route in ("llm_unavailable", "llm_error"):
        return ("That question needs the language model, which isn't reachable right now. Single figures, "
                "definitions and the standard reports still work, so please try one of those, or ask again shortly.")
    head = _headline(answer)
    rest = re.sub(r"^\*\*\s*", "", answer[answer.find(head) + len(head):]) if head else answer
    if route == "glossary":                                       # "**DCB**: Demand-Collection-Balance: ..."
        term, _, defn = _plain(answer).partition(":")
        first = re.split(r"(?<=[a-z\)]):\s|(?<=[.!?])\s", defn.strip())[0] if defn.strip() else term
        return _clean(f"{term.strip()} stands for {first}")
    if route.startswith("tariff:"):
        return _tariff_spoken(answer, head, rest)
    if route == "out_of_range":
        return _clean(head) + " If I misheard the year, please say it again."
    is_sentence = bool(head) and head[-1] in ".!?"
    spoken: list[str] = []
    if head and head.endswith(":"):                              # a headline introducing bullets
        spoken.append(_clean(head.rstrip(":") + "."))
        spoken += [_clean(b) for b in _bullets(answer)[:2]]
    elif is_sentence:
        spoken.append(_clean(head))
    else:                                                        # a title (knowledge) or no bold headline
        paras = [p for p in _paragraphs(rest if head else answer) if not p.lstrip().startswith(("•", "-"))]
        if head and paras and rest.lstrip().startswith(":"):
            paras[0] = head + paras[0].lstrip()
        # An explanation is said without its bracketed asides ("(tariff LT-1 Domestic in the current booklet)"),
        # which keeps the first sentence whole instead of cutting it mid-thought.
        sents = [re.sub(r"\s*\((?:[^()]|\([^()]*\))*\)", "", s) for p in paras for s in _sentences(_plain(p))
                 if not _SCREEN_ONLY.search(s)]
        spoken += [_clean(_short(s, 36)) for s in sents[:1 if sents and len(sents[0].split()) > 22 else 2]]
        if not spoken and head:
            spoken.append(_clean(head))
    premise = head.lower().startswith("the question says")
    if route.startswith(_HEADLINE_ONLY) or route.startswith("refused"):
        spoken = spoken[:1] if is_sentence or route == "capability" else spoken[:2]
        if route == "capability":
            # the note's first sentence (what is missing); the formula after it is for the screen
            spoken = [(_sentences(spoken[0]) or [""])[0]] if spoken else []
            spoken.append("I can tell you about demand, collection, arrears, installations and tariff categories.")
    elif premise:
        # "The question says ₹500 crore, but the data shows ₹354.16 crore." then the figure that was asked for: the
        # last paragraph of the answer proper (a premise answer is the correction, the figure stated, the figure asked).
        paras = [p for p in _paragraphs(rest) if not p.lstrip().startswith(("•", "-"))]
        head_nums = _numbers(head)
        for p in reversed(paras):
            s = _sentences(_plain(p))[0] if _sentences(_plain(p)) else ""
            if s and not _SCREEN_ONLY.search(s) and _numbers(s) - head_nums:
                spoken.append(_clean(re.sub(r"\s*\(₹[\d,]+\.\d{2}\)", "", s)))
                break
    elif len(spoken) == 1 and is_sentence and not _numbers(head):
        # A title-like headline with no figure ("Demand for Holenarasipura Division, 2015-16 to 2025-26."): the story
        # sentence under it (range, highest / lowest, growth), never the first few rows of the list.
        sents = [s for p in _paragraphs(rest) if not p.lstrip().startswith(("•", "-"))
                 for s in _sentences(_plain(p)) if not _SCREEN_ONLY.search(s) and _numbers(s)]
        if sents:
            story = next((s for s in sents[1:] if _STORY.search(s)), sents[1] if len(sents) > 1 else "")
            spoken.append(_clean(sents[0]))
            if story and len(" ".join(spoken).split()) + len(story.split()) <= 60:
                spoken.append(_clean(story))
        elif _bullets(answer):
            spoken.append(_clean(_list_sentence(_bullets(answer), head)))
    elif len(spoken) == 1 and is_sentence and len(_numbers(head)) < 3:
        # One supporting sentence (or up to three list items) that adds a figure the headline doesn't have.
        bl = _bullets(answer)
        head_nums = _numbers(head)
        names = [re.split(r"\s*[—–:·]\s*|,", _plain(b))[0].strip().lower() for b in bl]
        already = sum(1 for n in names[:3] if n and n in head.lower())
        busy_items = any(len(_numbers(re.split(r"\s*\((?![a-z]{1,4}\))", _plain(b))[0])) > 2 for b in bl[:3])
        if bl and 2 <= len(bl) <= 12 and already < 2 and not busy_items and not _SCREEN_ONLY.search(bl[0]):
            spoken.append(_clean(_list_sentence(bl, head)))
        else:
            for p in _paragraphs(rest):
                if p.lstrip().startswith(("•", "-")):
                    continue
                for s in _sentences(_plain(p)):
                    nums = _numbers(s)
                    if _SCREEN_ONLY.search(s) or not nums or nums & head_nums or len(nums) > 4:
                        continue
                    spoken.append(_clean(s))
                    break
                if len(spoken) > 1:
                    break
    text = re.sub(r"\s*\n\s*", ". ", " ".join(spoken))
    text = re.sub(r"\.\s*\.", ".", text)
    if len(text.split()) > 70:                                   # keep it to what the ear can hold
        text = spoken[0]
    more_on_screen = rows > 2 or has_chart or (route in ("knowledge", "knowledge_fallback")
                                               and len(_plain(answer).split()) > len(text.split()) + 25)
    if more_on_screen:
        text += " " + (REPORT_NOTE if has_report else SCREEN_NOTE)
    return text.strip()


def _tariff_spoken(answer: str, head: str, rest: str) -> str:
    """Tariff-booklet answers: the title, then either the first table rows ("LT-4(a), irrigation pump sets up to 10
    HP; ...") or the first sentence of the description, cut short. Rates and rules stay on the screen in full."""
    title = re.sub(r"\s*\(booklet page \d+\)", "", head).strip()
    out = [_clean(title)] if title else []
    rows = _table_rows(answer)
    if rows:
        cell = lambda c: re.split(r"\s+\(|;", c)[0].strip()  # noqa: E731 -- "₹215 per kW per month (...)" -> the rate
        n = 3 if len(rows[0]) > 2 and not _RUPEE.search(rows[0][1]) else 2
        items = [f"{cell(r[0])}, {cell(r[1])}" if len(r) > 1 and r[1] and r[1] != r[0] else cell(r[0]) for r in rows[:n]]
        more = len(rows) - len(items)
        out.append(_clean("; ".join(items) + (f"; and {more} more" if more > 0 else "")))
    else:
        paras = [p for p in _paragraphs(rest) if not p.lstrip().startswith(("•", "-"))]
        sents = [s for p in paras for s in _sentences(_plain(p)) if not _SCREEN_ONLY.search(s)]
        if sents:
            out.append(_clean(_short(sents[0], 24)))
    text = " ".join(out)
    if len(text.split()) > 70:
        text = out[0] if out else SCREEN_NOTE
    return (text + " " + SCREEN_NOTE).strip()


# ================================================================================================ transcripts
# What the clean-up was unsure about, for the "Did you mean ...?" confirmation (app/voice_guard.confirmation).
# Each note: {"kind": "name" | "year" | "tariff", "span": text in the cleaned question, "options": [most likely, other]}.
_NOTES: ContextVar[list | None] = ContextVar("voice_clean_notes", default=None)


def _note(kind: str, span: str, options: list[str]) -> None:
    notes = _NOTES.get()
    if notes is not None and len({o.lower() for o in options}) > 1:
        notes.append({"kind": kind, "span": span, "options": options})


_SPELLED = re.compile(r"\b(?:[A-Za-z][.\s]\s*){1,5}[A-Za-z]\b\.?")
_W1 = {w: i for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                                    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
                                    "eighteen", "nineteen"])}
_W10 = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_TWO = r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[\s-]+(?:one|two|three|four|five|six|seven|eight|nine))?|" \
       r"ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)"


def _two_digits(words: str) -> int:
    parts = re.split(r"[\s-]+", words.lower().strip())
    return sum(_W10.get(p, 0) + _W1.get(p, 0) for p in parts)


def _spoken_years(t: str) -> str:
    """'two thousand and twenty three twenty four' / 'twenty twenty-three, twenty-four' / '2023 24' -> '2023-24'."""
    # "FY twenty two to twenty three" (the short form "fy 22-23" said aloud): FY 2022-23
    short = r"(fifteen|sixteen|seventeen|eighteen|nineteen|twenty[\s-]+(?:one|two|three|four|five|six|seven|eight|nine)|twenty)"
    t = re.sub(rf"\b(fy|f\.y\.|financial year|fiscal year)\s+{short}\s+(?:to|-|and)\s+{short}\b",
               lambda m: f"{m.group(1)} 20{_two_digits(m.group(2)):02d}-{_two_digits(m.group(3)):02d}"
               if _two_digits(m.group(3)) == _two_digits(m.group(2)) + 1 else m.group(0), t, flags=re.I)
    t = re.sub(rf"\btwo thousand(?: and)?[\s-]+({_TWO})\b", lambda m: str(2000 + _two_digits(m.group(1))), t, flags=re.I)
    t = re.sub(rf"\btwenty[\s-]+({_TWO})\b", lambda m: str(2000 + _two_digits(m.group(1))), t, flags=re.I)
    t = re.sub(rf"\b(20\d\d)[\s,]*(?:to|-|and)?[\s,]*({_TWO})\b", lambda m: f"{m.group(1)}-{_two_digits(m.group(2)):02d}", t,
               flags=re.I)
    t = re.sub(r"\b(20\d\d)\s*(?:to|-|/)?\s*(20)?(\d\d)\b(?!-)",
               lambda m: f"{m.group(1)}-{m.group(3)}" if int(m.group(3)) == (int(m.group(1)) + 1) % 100 else m.group(0), t)
    return t


def _repair_merged_years(t: str) -> str:
    """Speech-to-text merges "twenty twenty-three twenty-four" into "2020 to 24" / "2020-2024" / "2020 even 23". The
    second year survives, so the FY is (second - 1)-(second). Applied only right after FY / financial year / fiscal
    year, or with the tell-tale "even". Unlike RDPR's data, FY 2020-21 IS in this data (2015-16 to 2025-26), so the
    repaired year is always confirmed ("Did you mean FY 2023-24, or FY 2020-21?"). A real range ("from 2020 to 2024")
    is never touched."""
    def fy(prefix: str, b: int) -> str:
        _note("year", f"20{b - 1:02d}-{b:02d}", [f"20{b - 1:02d}-{b:02d}", "2020-21"])
        return f"{prefix}20{b - 1:02d}-{b:02d}"
    t = re.sub(r"\b((?:FY|financial year|fiscal year)\s*)2020\s*(?:to|-|–|even|and)\s*(?:20)?(2[2-6])\b(?!-)",
               lambda m: fy(m.group(1), int(m.group(2))), t, flags=re.I)
    t = re.sub(r"\b2020\s+even\s+(?:20)?(2[2-6])\b", lambda m: fy("", int(m.group(1))), t, flags=re.I)

    # Without "FY" (as heard in the spoken stress test): "in twenty twenty-two twenty-three" comes back as "in 2020 to
    # 23" -- the "two" became "to". Left alone after "from" / "between" / "since" (a real range), unless a second year
    # follows. Not confirmed: the place and year are read back with the answer.
    def bare(m):
        before = t[:m.start()].rstrip().lower()
        if re.search(r"\b(?:from|between|since)$", before) and not re.match(r"\s+(?:to|and)\s+20\d\d", t[m.end():]):
            return m.group(0)
        b = int(m.group(1))
        return f"20{b - 1:02d}-{b:02d}"
    return re.sub(r"\b2020\s*(?:to|-|–)\s*(?:20)?(2[2-6])\b(?![-\d])", bare, t, flags=re.I)


def _phon(word: str) -> str:
    """A rough sound key for Karnataka place names as speech-to-text spells them: Arkalgud / Arakalagodu, Konanur /
    Konanooru, Holenarasipura / Holey Narsipur collapse to near-identical keys."""
    s = word.lower()
    s = "i" + s[1:] if s.startswith("e") else s
    for a, b in (("th", "t"), ("dh", "d"), ("bh", "b"), ("gh", "g"), ("kh", "k"), ("sh", "s"), ("ph", "f"),
                 ("ck", "k"), ("ch", "\0"), ("c", "k"), ("\0", "ch"), ("w", "v"), ("z", "j"), ("ee", "i"), ("oo", "u"),
                 ("y", "i"), ("q", "k")):
        s = s.replace(a, b)
    s = s.replace("h", "")
    s = re.sub(r"(.)\1+", r"\1", s)
    return re.sub(r"(?<=[^aeiou])u$", "", s)                     # Konanuru ~ Konanur (Kannada final -u)


def _score(heard: str, name: str) -> float:
    """How alike a heard word and a real name are: the mean of the sound-key and the spelling similarity."""
    import difflib
    a, b = heard.lower(), name.lower()
    base = (difflib.SequenceMatcher(None, _phon(a), _phon(b)).ratio() + difflib.SequenceMatcher(None, a, b).ratio()) / 2
    # The speech model keeps a place name's consonants and slips on its vowels and last sound ("Erkalgu" for Arkalgud):
    # the consonant skeletons, when four or more consonants line up, count a little below an exact sound match.
    ka, kb = re.sub(r"[aeiou]", "", _phon(a)), re.sub(r"[aeiou]", "", _phon(b))
    m = difflib.SequenceMatcher(None, ka, kb)
    if sum(x.size for x in m.get_matching_blocks()) >= 4:
        base = max(base, 0.95 * m.ratio())
    return base


# Words that are never sub-division names, so they are never "corrected" (the questions' own vocabulary, plus real
# names that are not sub-divisions: the CESC districts, ESCOMs, schemes).
_ORDINARY = set("""what was the how much many did does do in of for and to from by at on with which who when where is are
were was what's whats show give tell list all every each about me please compare between versus vs best worst highest
lowest top bottom trend monthly yearly annual average total overall report summary overview performance situation status
position picture scorecard rank ranking need attention lagging improve improved collection collections collected collect
demand demanded billed billing arrears arrear balance balances outstanding dues adjustment adjustments revenue opening
closing installation installations connections consumers consumer live dead meter meters metered unmetered recording
consumption units unit energy load connected sanctioned efficiency percentage percent rate rates tariff tariffs category
categories slab slabs family families domestic commercial industrial industries agriculture agricultural irrigation pump
pumps sets lighting heating motive power water supply public street temporary hospital hospitals railway railways
government subsidy subsidies cash share number count month year years financial fiscal quarter division divisions
subdivision subdivisions section sections office state karnataka india electricity supply company corporation limited
chamundeshwari cesc cescom bescom mescom hescom gescom kptcl kerc escom escoms mysuru mysore mandya hassan chamarajanagar
chamarajanagara kodagu coorg bengaluru bangalore gruha jyothi bhagya kutira jyoti nirantara surya raitha horticulture
nurseries plantation plantations hoardings hoarding advertisement apartments lift schemes scheme charging station stations
vehicle vehicles electric january february march april may june july august september october november december now
today latest last current currently this previous next first second third fourth crore lakh lakhs thousand rupees
paise miscellaneous misc other others loss losses technical commercial aggregate realised realized assessed fuel
purchase cost fppca mnr dcb high tension low voltage complete completed full whole entire hello thanks thank okay yes
""".split())


@lru_cache(maxsize=1)
def _names() -> dict[str, str]:
    """Every spelling of every sub-division (its aliases, multi-word ones joined: "hnpura") -> the name the data knows."""
    from app.router import _SUB_ALIASES
    out: dict[str, str] = {}
    for name, aliases in _SUB_ALIASES.items():
        if name == "Division_Office":
            continue
        for a in [name.lower(), *aliases]:
            out.setdefault(a.replace(" ", ""), name)
    return out


@lru_cache(maxsize=1)
def _name_vocab() -> dict[str, str]:
    """Sound key -> the sub-division name, for every spelling of 5+ letters."""
    return {_phon(s): n for s, n in _names().items() if len(s) >= 5}


def place_kind(name: str) -> str:
    """How the data names the place: Konanur is a section, the others are sub-divisions."""
    return "section" if name == "Konanur" else "sub-division"


def _ranked(heard: str) -> list[tuple[float, str]]:
    """The sub-divisions, best match first (distinct names)."""
    best: dict[str, float] = {}
    for s, n in _names().items():
        sc = _score(heard, s)
        if sc > best.get(n, 0):
            best[n] = sc
    return sorted(((v, k) for k, v in best.items()), reverse=True)


_KNOWN_ACRONYMS = {"FY", "DCB", "LT", "HT", "MNR", "DC", "FPPCA", "FAC", "ARR", "KERC", "CESC", "KPTCL", "ESCOM", "BMA",
                   "ULB", "IP", "EV", "HP", "KVA", "KW", "KWH", "TOD", "MIS", "CDT", "BJ", "KJ", "OB", "CB", "IPP", "GST"}


def _spell_join(t: str) -> str:
    """'D C B' / 'f.p.p.c.a' / 'M N R' / 'L T' -> 'DCB' / 'FPPCA' / 'MNR' / 'LT' (known acronyms only)."""
    def join(m):
        letters = re.sub(r"[^A-Za-z]", "", m.group(0)).upper()
        if letters in _KNOWN_ACRONYMS and len(letters) >= 2:
            return letters + (" " if m.group(0).endswith(" ") else "")
        return m.group(0)
    return _SPELLED.sub(join, t)


# Mis-hearings of ordinary question words (Indian-English speakers, qwen3-asr), and the spoken forms of DCB terms.
_PHRASES = [
    # heard in the spoken stress tests, 2026-10-07 (qwen3-asr; Windows Ravi / Heera voices)
    (r"\b(?:80|eighty|a tea|8)\s+and\s+c\b(?=\s*(?:loss|losses))", "AT&C"),
    (r"\bassist(?:ed)?\s+consumption\b", "assessed consumption"),
    (r"\bbuild\s+rate\b", "billed rate"),
    (r"^\s*nh\s?-?\s?(\d)\b", r"and HT-\1"),
    # "and" heard as "an" at the start of a follow-up ("An in Ramanathapura.", "An FY 2025-26.", "Okay, an opening
    # balance."): never before a word that takes "an" ("an IP set", "an installation")
    (r"^(\s*(?:ok(?:ay)?,?\s+)?)an\s+(?=(?:in|fy|for|the|same|now|then|what|how|opening|closing|demand|collection|"
     r"arrears|consumption|live|number|last|this|arkalgud|arakalagud|hangarahalli|holenarasipura|ramanathapura|"
     r"ramanathpura|konanur|saligrama|hn)\b)", r"\1and "),
    # "HT to a" / "LT too b": the speech model's homophone of two before a slab letter
    (r"\b([LH]T)\s+(?:to|too)\s+\(?([abc])\)?(?![a-z'])", lambda m: f"{m.group(1).upper()}-2({m.group(2).lower()})"),
    # "L4 demand", "H2 demand", "what is L4": the speech model's shorthand for a tariff family before a measure
    (r"\b([LlHh])\s?-?\s?([1-9])\b(?=\s*(?:\(|demand|collection|collections|arrears|tariff|rate|category|installations|"
     r"consumption|slab|share|in\b|for\b|\?|\.|$))", lambda m: f"{m.group(1).upper()}T-{m.group(2)}"),
    (r"\b(?:chenpura|hn\s?purak|h\s?n\s?purak|hen\s?pura|aitch\s?n\s?pura)\b", "HN Pura"),
    (r"\bsub[\s-]*divi?s(?:i|io|ia)ons\b", "sub-divisions"),
    (r"\b(?:sub[\s-]*divi?s(?:i|io|ia)on|sab[\s-]*division|subdivison|sub divison)\b", "sub-division"),
    (r"\bper\s*cent\b", "percent"),
    (r"\b(?:lakhs|lacs|lac|laks)\b", "lakh"),
    (r"\bcrores\b", "crore"),
    (r"\b(?:a\s?t\s?(?:and|&|n|en)\s?c|atnc|a\.t\.\s?&\s?c\.?|at\s?&\s?c)\b(?=\s*(?:loss|losses|percentage|percent)?)", "AT&C"),
    (r"\b(?:f\.?\s?p\.?\s?p\.?\s?c\.?\s?a|fppc|fpca|f p p c a)\b", "FPPCA"),
    (r"\bfuel (?:and )?power purchase cost(?: adjustment)?\b", "FPPCA"),
    (r"\b(?:meter|meters) not recording\b", "MNR"),
    (r"\bd\s?c\s?(?:/|and|or|slash)\s?m\s?n\s?r\b", "DC/MNR"),
    (r"\b(?:i\s?p|eye pee)\s+sets\b", "IP sets"),
    (r"\b(?:i\s?p|eye pee)\s+set\b", "IP set"),
    (r"\bgr[iu]h?a\s*jyo(?:thi|ti|thy)\b", "Gruha Jyothi"),
    (r"\bbhagya\s*jyo(?:thi|ti|thy)\b", "Bhagya Jyothi"),
    (r"\bkut(?:i|ee)ra?\s*jyo(?:thi|ti|thy)\b", "Kutira Jyothi"),
    (r"\bcollection\s+efficienc(?:y|ies|e)\b|\bcollection\s+efficency\b|\bcollection\s+effeciency\b", "collection efficiency"),
    (r"\b(?:correction|collections)\s+efficiency\b", "collection efficiency"),
    (r"\b(?:installation|instalations?|instillations?)\b", lambda m: "installations" if m.group(0).lower().endswith("s") else "installation"),
    (r"\b(the|and|total|of|in)\s+(?:areas|arears|errors|arrear's)\b", r"\1 arrears"),
    (r"^(?:areas|arears)\b", "arrears"),
    (r"\bout\s+standing\b", "outstanding"),
    (r"\bde\s?mand\b", "demand"),
    (r"\b(financial|fiscal)\s+year\s+(\d{2})\b(?![-\d])", r"FY\2"),
    (r"\b(?:financial|fiscal)\s+year\s+(20\d{2})\b(?![-\d])", r"FY \1"),
    (r"\bden(?=\s*[.?!]*\s*$)", "then"),                                     # "And Konanur den."
    (r"\bhigh\s+tension\b", "HT"),
    (r"\blow\s+tension\b", "LT"),
]

_NUMWORD = {"one": 1, "two": 2, "to": 2, "too": 2, "three": 3, "four": 4, "for": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10}
_ROMAN = {"one": "i", "two": "ii", "three": "iii", "1": "i", "2": "ii", "3": "iii", "i": "i", "ii": "ii"}


def _tariff_codes(t: str) -> str:
    """Spoken tariff codes -> the data's codes: 'L T four A' / 'LT four a' / 'el tee 4' / 'LT-4 A' -> 'LT-4(a)';
    'HT two' -> 'HT-2'; 'H T two B two' -> 'HT-2(b)(ii)'. 'LT for <letter>' and 'HT to <measure>' are the speech
    model's homophones of four / two: rewritten, and confirmed ("Did you mean LT-4, or LT?") when no letter follows."""
    t = re.sub(r"\b(?:el\s?tee|elty|eltee|l\.?\s?t\.?)(?=[\s-]*(?:\d|one|two|three|four|five|six|seven|eight|nine)\b)", "LT", t, flags=re.I)
    t = re.sub(r"\b(?:aitch\s?tee|edge\s?tee|h\.?\s?t\.?)(?=[\s-]*(?:\d|one|two|three|four|five|six|seven|eight|nine)\b)", "HT", t, flags=re.I)
    num = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
    letter = r"(?:[\s-]*\(?\s*([abc])\s*\)?(?![a-z']))?"
    sub2 = r"(?:[\s-]*\(?\s*(one|two|three|i{1,3}|[123])\s*\)?(?![a-z']))?"

    def code(m):
        fam = m.group(1).upper()
        n = m.group(2).lower()
        n = str(_NUMWORD.get(n, n))
        out = f"{fam}-{n}"
        if m.group(3):
            out += f"({m.group(3).lower()})"
            if m.group(4):
                out += f"({_ROMAN.get(m.group(4).lower(), m.group(4))})"
        return out
    t = re.sub(rf"\b(LT|HT)[\s-]*{num}{letter}{sub2}", code, t, flags=re.I)
    # homophones: "LT for a" -> LT-4(a); "LT for demand" -> LT-4 demand (confirmed); "HT to collection" -> HT-2 (confirmed)
    t = re.sub(r"\b(LT|HT)\s+(?:for|four)\s+\(?([abc])\)?(?![a-z'])", lambda m: f"{m.group(1).upper()}-4({m.group(2).lower()})", t, flags=re.I)

    def homophone(m):
        fam, n = m.group(1).upper(), _NUMWORD[m.group(2).lower()]
        _note("tariff", f"{fam}-{n}", [f"{fam}-{n}", fam])
        return f"{fam}-{n} "
    measure = (r"(?=(?:demand|collection|collections|arrears|installations|consumption|tariff|rate|category|slab|balance|share)\b"
               r"|in\s+(?:fy|f\.y\.|financial|fiscal|the|this|last|20\d\d)\b|[?.!]?\s*$)")
    return re.sub(rf"\b(LT|HT)\s+(for|to|too)\s+{measure}", homophone, t, flags=re.I)


_GLUED_KIND = re.compile(r"\b([A-Za-z]{4,}?)(sub-?division|section)\b", re.I)


def _unglue(t: str) -> str:
    """'Konanursection' / 'Arkalgudsubdivision' -> 'Konanur section' / 'Arkalgud sub-division', only when the front part
    sounds like a real sub-division (score >= 0.7)."""
    def sub(m):
        stem = m.group(1)
        if stem.lower() in _ORDINARY or _ranked(stem)[0][0] < 0.7:
            return m.group(0)
        return f"{stem} {m.group(2)}"
    return _GLUED_KIND.sub(sub, t)


_SPAN_LEAD = {"of", "the", "in", "for", "at", "and", "an", "to", "from", "by", "on", "with", "is", "was", "what", "how", "much",
              "many", "did", "does", "do", "which", "vs", "versus", "compare", "show", "me", "give", "all", "total",
              "collection", "demand", "balance", "percentage", "outstanding", "arrears", "about", "tell"}


def _match_name(words: list[str], need: float, loose: float) -> tuple[int, str, float, str | None, float] | None:
    """The best sub-division for the last one to three words (joined), as (start, name, score, runner-up, its score).
    None below `loose`."""
    best = None
    for start in range(len(words) - 1, max(-1, len(words) - 4), -1):
        part = words[start:]
        if part[0].lower() in _SPAN_LEAD or (start < len(words) - 1 and part[0].lower() in _ORDINARY):
            break
        heard = "".join(part)
        if len(heard) < 4 or (len(part) == 1 and heard.lower() in _ORDINARY):
            continue
        if heard.lower() in _names():
            return start, _names()[heard.lower()], 1.0, None, 0.0
        if len(part) == 1 and _phon(heard) in _name_vocab():
            return start, _name_vocab()[_phon(heard)], 1.0, None, 0.0
        top = _ranked(heard)
        bonus = 0.0 if len(part) == 1 else 0.005                 # on a tie the longer (joined) reading wins
        cand = (start, top[0][1], top[0][0] + bonus, top[1][1] if len(top) > 1 else None, top[1][0] if len(top) > 1 else 0.0)
        if best is None or cand[2] > best[2]:
            best = cand
    if best is None or best[2] < loose:
        return None
    return best


def _anchored(t: str) -> str:
    """The words right before "sub-division" / "section" are matched only against the sub-divisions, so a looser match
    is safe: "Arakalagodu sub-division" -> Arkalgud, "Konanooru section" -> Konanur, "Hangara Halli sub-division" ->
    Hangarahalli. Close (>= 0.7): replaced, and below 0.86 the officer confirms between the two best. Loose (0.55 up to
    that): not replaced, but the two best are offered ("Did you mean ...?"). An unknown name stays as heard, so the
    router answers "I don't recognise that sub-division" (asked again by voice), never the whole division."""
    pat = re.compile(r"\b((?:[A-Za-z]+\s+){0,2}[A-Za-z]+)\s+(?=(?:sub-division|sub division|subdivision|section)\b)", re.I)

    def sub(m):
        words = m.group(1).split()
        if len(words) == 1 and words[0].lower() in _ORDINARY:
            return m.group(0)
        hit = _match_name(words, 0.7, 0.55)
        if hit is None:
            return m.group(0)
        start, name, score, other, other_score = hit
        span = " ".join(words[start:])
        if score >= 0.7:
            if score < 0.86 and other and other_score >= max(0.55, score - 0.1):
                _note("name", name, [name, other])
            return " ".join(words[:start] + [name]) + " "
        if other:
            _note("name", span, [name, other])
        return m.group(0)
    return pat.sub(sub, t)


def _fix_names(t: str) -> str:
    """Respell sub-division names the way the data knows them anywhere in the question ("Arakalagud" -> Arkalgud):
    an exact sound-key match for any word; a close match (score >= 0.8) for words of 5+ letters that are not ordinary
    question words, and below 0.9 the officer is asked "Did you mean ...?" with the runner-up. A name the speech model
    split in two ("Hangara halli", "Rama natha pura") is joined first."""
    words = re.findall(r"[A-Za-z]+|[^A-Za-z]+", t)
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if not w.isalpha() or len(w) < 4 or w.lower() in _ORDINARY or w.lower() in _names() or w.isupper():
            out.append(w)
            i += 1
            continue
        joined = False
        for span in (5, 3):                                      # three words, then two ("Rama natha pura", "Hangara halli")
            if i + span - 1 < len(words) and all(words[i + k] == " " for k in range(1, span, 2)) and \
                    all(words[i + k].isalpha() and words[i + k].lower() not in _ORDINARY for k in range(0, span, 2)):
                pair = "".join(words[i:i + span:2])
                if pair.lower() in _names() or _phon(pair) in _name_vocab():
                    out.append(_names().get(pair.lower()) or _name_vocab()[_phon(pair)])
                    i += span
                    joined = True
                    break
                top = _ranked(pair)
                if len(pair) >= 8 and top[0][0] >= 0.84 and _score(w, top[0][1]) < top[0][0]:
                    out.append(top[0][1])
                    if top[0][0] < 0.9 and len(top) > 1 and top[1][0] >= 0.72:
                        _note("name", top[0][1], [top[0][1], top[1][1]])
                    i += span
                    joined = True
                    break
        if joined:
            continue
        key = _phon(w)
        if key in _name_vocab():
            out.append(_name_vocab()[key])
        elif len(w) >= 5:
            top = _ranked(w)
            score, best = top[0]
            if score >= 0.8:
                out.append(best)
                if score < 0.9 and len(top) > 1 and top[1][0] >= max(0.62, score - 0.08):
                    _note("name", best, [best, top[1][1]])
            else:
                out.append(w)
        else:
            out.append(w)
        i += 1
    return "".join(out)


_FOLLOW_CUE = re.compile(r"^\s*(?:(?:ok|okay|so|now|then|also)[\s,]+)*(?:and|an|what about|how about|same for|now)\b", re.I)
_NOT_A_NAME = {"then", "also", "okay", "about", "same", "instead", "there", "well", "please", "that", "this", "those"}


def _follow_up_name(t: str) -> str:
    """A spoken follow-up names a place without "sub-division" after it ("And Konanuru.", "What about Arakalgodu?"), so
    nothing anchors the match. In a short follow-up each run of one or two words that are neither question words nor
    known names is matched against the sub-divisions: close (>= 0.72) replaced (confirmed below 0.84); looser (>= 0.58)
    the two best offered."""
    if not _FOLLOW_CUE.match(t) or len(t.split()) > 10:
        return t

    def candidate(w: str) -> bool:
        lw = w.lower()
        return len(lw) >= 4 and lw not in _ORDINARY and lw not in _NOT_A_NAME and lw not in _names() and not w.isupper()

    toks = list(re.finditer(r"[A-Za-z]+", t))
    edits = []
    i = 0
    while i < len(toks):
        if i == 0 or not candidate(toks[i].group(0)):
            i += 1
            continue
        j = i + 1 if i + 1 < len(toks) and candidate(toks[i + 1].group(0)) and t[toks[i].end():toks[i + 1].start()] == " " else i
        heard = "".join(x.group(0) for x in toks[i:j + 1])
        top = _ranked(heard)
        if top[0][0] >= 0.72:
            edits.append((toks[i].start(), toks[j].end(), top[0][1]))
            if top[0][0] < 0.84 and len(top) > 1 and top[1][0] >= max(0.55, top[0][0] - 0.1):
                _note("name", top[0][1], [top[0][1], top[1][1]])
        elif top[0][0] >= 0.58 and len(top) > 1:
            _note("name", t[toks[i].start():toks[j].end()], [top[0][1], top[1][1]])
        i = j + 1
    for a, b, name in reversed(edits):
        t = t[:a] + name + t[b:]
    return t


def _lexicon() -> dict[str, str]:
    """Names as speech-to-text spells them -> the name the data knows: app/voice_lexicon.json, validated at load by
    app/voice_guard.lexicon() (an entry can never turn one real place into another)."""
    from app.voice_guard import lexicon
    return lexicon()["mishearings"]


def _confusable_and_years(t: str) -> None:
    """Notes for a known confusable name heard on its own, and a single ambiguous year ("FY 2024", "in 2024")."""
    from app.voice_guard import lexicon
    for c in lexicon()["confusable"]:
        unless = "|".join(map(re.escape, c.get("unless_followed_by", [])))
        for h in c["heard"]:
            m = re.search(rf"\b{re.escape(h)}\b(?!\s+(?:{unless})\b)" if unless else rf"\b{re.escape(h)}\b", t, re.I)
            if m:
                _note("name", m.group(0), list(c["options"]))
                return
    m = re.search(r"\bFY\s?(?:20)?(\d{2})\b(?![-\d])", t)
    if m and 16 <= int(m.group(1)) <= 26:
        yy = int(m.group(1))
        _note("year", m.group(0), [f"FY 20{yy - 1:02d}-{yy:02d}", f"FY 20{yy:02d}-{yy + 1:02d}"])
        return
    # a bare calendar year ("demand in 2024"): the router would ignore it and use the latest year, so ask which FY
    for m in re.finditer(r"\b(20(1[5-9]|2[0-6]))\b(?![-\d])", t):
        before = t[:m.start()].rstrip().lower()
        after = t[m.end():m.end() + 12].lower()
        # "since 2020" is a range; "March 2025" is a month, which the capability check answers (yearly data only)
        if re.search(r"\b(?:since|from|between|after|before|till|until|to|and|through|january|february|march|april|"
                     r"may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|"
                     r"oct|nov|dec)$", before) or \
                re.match(r"\s*(?:to|and|till|until|-)\s*(?:fy\s*)?20\d\d", after):
            continue
        y = int(m.group(1))
        if 2016 <= y <= 2026:
            _note("year", m.group(1), [f"{y - 1}-{y % 100:02d}", f"{y}-{(y + 1) % 100:02d}"] if y < 2026 else
                  [f"{y - 1}-{y % 100:02d}", f"{y - 2}-{(y - 1) % 100:02d}"])
        return


def clean_transcript_ex(text: str) -> tuple[str, list[dict]]:
    """clean_transcript plus what it was unsure about (see _note)."""
    token = _NOTES.set([])
    try:
        cleaned = clean_transcript(text)
        _confusable_and_years(cleaned)
        notes = _NOTES.get() or []
    finally:
        _NOTES.reset(token)
    return cleaned, notes


def clean_transcript(text: str) -> str:
    """Speech-to-text output -> the question the pipeline expects. 'D C B' -> 'DCB'; 'L T four A' -> 'LT-4(a)'; spoken
    fiscal years -> '2023-24'; misheard sub-division names -> the data's names. Anything else is left as heard."""
    t = (text or "").strip()
    if not t:
        return ""
    t = _spoken_years(t)
    t = _repair_merged_years(t)
    t = _spell_join(t)
    for pat, rep in _PHRASES:
        t = re.sub(pat, rep, t, flags=re.I)
    t = _tariff_codes(t)
    lex = _lexicon()
    t = re.sub(r"\b[A-Za-z]+\b", lambda m: lex.get(m.group(0).lower(), m.group(0)), t)
    t = _unglue(t)
    t = _follow_up_name(t)
    t = _anchored(t)
    # "FY23-24", "FY 23 24", "financial year 23-24" -> "FY 2023-24" (only consecutive years)
    t = re.sub(r"\b(F\.?\s?Y\.?|financial year|fiscal year)\s*'?(\d{2})\s*(?:-|to|/|\s)\s*(\d{2})\b(?!-)",
               lambda m: (f"FY 20{m.group(2)}-{m.group(3)}" if int(m.group(3)) == (int(m.group(2)) + 1) % 100
                          else m.group(0)), t, flags=re.I)
    t = re.sub(r"\bF\.\s?Y\.?(?=\s|$)", "FY", t)
    t = _fix_names(t)
    t = re.sub(r"\s+([?.!,])", r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip()


# ================================================================================================ pronunciation
# Acronyms spelled out for TTS ("D C B", "M N R"); plural forms keep a spoken "s".
_SPELL_OUT = ["FPPCA", "KPTCL", "CESC", "KERC", "DCB", "MNR", "FAC", "ARR", "BMA", "ULB", "CDT", "MIS", "CSV", "IPP", "GST",
              "HP", "kVA", "kWh", "kW", "EV", "IP", "AI", "LT", "HT", "DC", "BJ", "KJ"]
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ROMAN_SAY = {"i": "one", "ii": "two", "iii": "three", "iv": "four", "v": "five"}


def _two(n: int) -> str:
    return _ONES[n] if n < 20 else _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")


def _year(y: int) -> str:
    """2024 -> twenty twenty-four; 2005 -> two thousand five."""
    if 2000 <= y <= 2009:
        return "two thousand" + (f" {_ONES[y - 2000]}" if y > 2000 else "")
    return f"{_two(y // 100)} {_two(y % 100)}"


def _say_code(m: re.Match) -> str:
    """LT-4(a) -> 'L T four A'; HT-2(b)(ii) -> 'H T two B two'; LT-3 a (i)(OL) -> 'L T three A one O L'."""
    code = m.group(0)
    fam = code[0].upper()
    if code.upper().endswith("IPP"):
        return f"{fam} T I P P"
    n = int(re.search(r"\d{1,2}", code).group(0))
    parts = []
    for p in re.findall(r"\(([A-Za-z]{1,4})\)|\s([a-c])(?=\s?\()", code[re.search(r"\d{1,2}", code).end():]):
        p = (p[0] or p[1])
        if p.lower() in _ROMAN_SAY and not (len(p) == 1 and p.lower() in "abc"):
            parts.append(_ROMAN_SAY[p.lower()])
        else:
            parts.append(" ".join(p.upper()))
    return " ".join([f"{fam} T", _ONES[n] if n < 20 else str(n)] + parts)


def pronounce(text: str) -> str:
    """Make spoken text TTS-safe. Applied by /api/voice/speak* just before synthesis."""
    t = _dates(text)
    t = TARIFF_CODE.sub(_say_code, t)
    t = re.sub(r"\bFY\s*(\d{4})-(\d{2})\b",
               lambda m: f"financial year {_year(int(m.group(1)))}, {_two(int(m.group(2)))}", t)
    t = re.sub(r"(?<![\d,])\b(\d{4})-(\d{2})\b(?![\d-])", lambda m: f"{_year(int(m.group(1)))}, {_two(int(m.group(2)))}", t)
    t = re.sub(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
               lambda m: f"{m.group(1)} {_year(int(m.group(2)))}", t)
    t = re.sub(r"\bQ([1-4])\b", lambda m: "quarter " + _ONES[int(m.group(1))], t)
    t = re.sub(r"\b(year|in|since|of|from|until|to|before|after)\s+((?:19|20)\d{2})\b(?![-\d]|[,.]\d)",
               lambda m: f"{m.group(1)} {_year(int(m.group(2)))}", t)
    t = re.sub(r"(?<![\w-])\+(?=\d)", "up ", t)
    t = _speak_figures(t) if "₹" in t or "%" in t else t
    t = re.sub(r"\bAT&C\b", "A T and C", t)
    t = re.sub(r"\bDC\s*/\s*MNR\b", "D C and M N R", t)
    t = re.sub(r"\bBJ\s*/\s*KJ\b", "B J and K J", t)
    t = re.sub(r"\bToD\b", "time of day", t)
    t = re.sub(r"\bGoK\b", "the Government of Karnataka", t)
    t = re.sub(r"\bESCOMs\b", "ESCOMS", t)
    t = re.sub(r"\bkWh\b", "kilowatt hour", t)
    t = re.sub(r"\bkVA\b", "kilovolt-ampere", t)
    t = re.sub(r"\bkW\b", "kilowatt", t)
    t = re.sub(r"\bkV\b", "kilovolt", t)
    t = re.sub(r"(?<=\d )HP\b|\bper HP\b", lambda m: m.group(0).replace("HP", "horsepower"), t)
    for a in _SPELL_OUT:
        t = re.sub(rf"\b{a}s\b", " ".join(a.upper()) + "s", t)
        t = re.sub(rf"\b{a}\b", " ".join(a.upper()), t)
    t = t.replace("&", " and ")
    t = re.sub(r"\s/\s", " or ", t)
    t = re.sub(r"\s*→\s*", " to ", t)
    return re.sub(r"\s{2,}", " ", t).strip()
