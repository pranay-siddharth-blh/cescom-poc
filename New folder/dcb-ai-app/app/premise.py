"""
False premises in a data question (ported from the RDPR POC's app/premise.py, read-only reference; DCB measures). A
question can state a figure as already true and ask about something else: "Of the ₹500 crore demanded in 2025-26, how
much was collected?", "Arkalgud's collection efficiency is 99%, so what are its arrears?", "Is the division's demand
₹300 crore?". Answering the rest as if the stated figure held would read as the assistant endorsing a wrong number,
so when the data contradicts it the answer opens with the correction:

    **The question says ₹500 crore, but the data shows ₹354.16 crore.** Demand for Holenarasipura Division in 2025-26
    was ₹354.16 crore. The answer below is from the data.

How (deterministic, no model call; precision over recall -- when unsure it stays silent):
  1. extract: a number stated with a unit -- rupees (₹ / Rs / rupees / crore / lakh), percent, or a count of
     installations / connections / consumers. Not a premise: thresholds and conditions ("more than 90%", "below 10%",
     "top 3"), years, tariff codes, bare numbers;
  2. measure: the registry measure named next to the number whose unit matches; none -> no check;
  3. recompute that measure on its own through the reviewed single-figure lookup (app.metric_lookup), for the place
     and year of the question -- not from the answer, which may be about something else;
  4. compare at the precision stated ("about ₹350 crore" matches ₹354.16 crore; "99%" does not match 95.75%).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app import mart_layer, metric_lookup, router
from app.db import ReadOnlyDB

DATA_ROUTES = ("metric:", "report:", "verified:", "slab:", "llm")

_NUM = re.compile(r"(?<![\w.])(₹\s*|rs\.?\s*|inr\s*)?(\d[\d,]*(?:\.\d+)?)(\s*(?:crores?|cr|lakhs?|lacs?|lac|thousand)\b)?"
                  r"(\s*(?:%|percent\b|per cent\b|rupees\b|rs\b))?", re.I)
_SCALE = {"crore": (1e7, "crore"), "crores": (1e7, "crore"), "cr": (1e7, "crore"), "lakh": (1e5, "lakh"),
          "lakhs": (1e5, "lakh"), "lacs": (1e5, "lakh"), "lac": (1e5, "lakh"), "thousand": (1e3, "thousand")}
_COUNT_RE = re.compile(r"^\s*(?:live\s+)?(installations?|connections?|consumers?)\b", re.I)
_BEFORE_NOT = re.compile(r"(?:\b(?:more|less|fewer|greater|higher|lower|bigger|smaller)\s+than|\b(?:above|below|over|under|"
                         r"exceeding|exceeds?|beyond|within|upto|up to|at least|at most|between|and|or|top|bottom|first|"
                         r"last|best|worst|minimum|maximum|than|from|to|by|per|which|show|list|threshold|target|"
                         r"lt|ht|lt-|ht-)|[<>=≤≥-])\s*$", re.I)
_AFTER_NOT = re.compile(r"^\s*(?:(?:or|and)\s+(?:less|more|below|above|fewer|lower|higher|under|over)\b|\+|plus\b|"
                        r"(?:st|nd|rd|th)\b|\s*[-/]\s*\d)", re.I)
_HEDGE = re.compile(r"\b(?:about|around|approximately|approx|roughly|nearly|almost|close to|some|~)\s*$", re.I)
_YEARISH = re.compile(r"^(?:19|20)\d\d$")


@dataclass
class Premise:
    said: str
    value: float
    kind: str          # "Rs" | "%" | "count"
    tol: float
    start: int
    end: int
    metric: str = ""


def _precision(num: str, kind: str) -> float:
    digits = num.replace(",", "")
    if "." in digits:
        return 10 ** -len(digits.split(".")[1])
    if kind == "%":
        return 1.0
    stripped = digits.rstrip("0")
    return 10 ** (len(digits) - len(stripped)) if stripped else 1.0


def extract(question: str) -> list[Premise]:
    text = " ".join((question or "").replace("’", "'").split())
    low = text.lower()
    out: list[Premise] = []
    for m in _NUM.finditer(text):
        a, b = m.span()
        num = m.group(2)
        if _YEARISH.match(num.replace(",", "")) and not (m.group(1) or m.group(3)):
            continue                                    # a year
        before, after = low[:a], low[b:]
        if _BEFORE_NOT.search(before) or _AFTER_NOT.match(after):
            continue
        n = float(num.replace(",", ""))
        scale_word = (m.group(3) or "").strip().lower()
        unit = (m.group(4) or "").strip().lower()
        mult, scale = _SCALE.get(scale_word, (1.0, ""))
        cnt = _COUNT_RE.match(after)
        if unit in ("%", "percent", "per cent"):
            kind, value, said = "%", n, f"{num}%"
        elif cnt and not m.group(1):
            if mult == 1.0 and n < 100:
                continue
            kind, value, said = "count", n * mult, f"{num}{(' ' + scale) if scale else ''} {cnt.group(1)}"
            b = m.end() + cnt.end()
        elif m.group(1) or scale or unit in ("rupees", "rs"):
            kind, value, said = "Rs", n * mult, f"₹{num}{(' ' + scale) if scale else ''}"
        else:
            continue
        if n == 0:
            continue
        hedged = bool(_HEDGE.search(before))
        tol = 0.5 * _precision(num, kind) * (mult if kind != "%" else 1.0)
        if kind != "%":
            tol = max(min(tol, abs(value) * (0.10 if hedged else 0.05)), abs(value) * 0.005)
        elif hedged:
            tol = max(tol, 2.0)
        out.append(Premise(said, value, kind, tol, a, b))
    return out


def _measure(question: str, p: Premise) -> str:
    """The registry measure named next to the figure, with the same kind of unit; "" when none."""
    low = " ".join((question or "").replace("’", "'").split()).lower()
    lo, hi = max(0, p.start - 60), min(len(low), p.end + 60)
    best, dist = "", 1e9
    for syn, key in mart_layer.metric_synonyms():
        unit = mart_layer.metric(key)["unit"]
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(syn) + r"(?![a-z0-9])", low[lo:hi]):
            s, e = lo + m.start(), lo + m.end()
            if p.start <= s < p.end:
                continue
            k = key
            if p.kind == "%" and unit == "Rs" and key == "collection":
                k, unit = "collection_efficiency", "%"          # "collection is 95%" is the collection efficiency
            if unit != p.kind:
                continue
            d = min(abs(s - p.end), abs(p.start - e))
            if d < dist:
                best, dist = k, d
    if p.kind == "count" and not best:
        best = "installations"
    return best


_FIGURE = re.compile(r"-?₹[\d,]+(?:\.\d+)?(?: crore| lakh)?|[\d,]+(?:\.\d+)?%|\d[\d,]*(?:\.\d+)?")


async def check(question: str, db: ReadOnlyDB, timeout_seconds: float, answer: str = "") -> str:
    """The correction to lead the answer with ("" when every stated figure agrees with the data, or none is stated)."""
    premises = extract(question)
    if not premises:
        return ""
    base = router.extract(question)
    lines = []
    for p in premises[:2]:
        p.metric = _measure(question, p)
        if not p.metric:
            continue
        e = {**base, "metrics": [p.metric], "intents": [], "rest_text": ""}
        try:
            ma = await metric_lookup.answer(e, "", db, timeout_seconds)
        except Exception:  # noqa: BLE001 -- a check that can't run stays silent
            continue
        if not ma or ma.metric != p.metric or not ma.rows:
            continue
        fy = (base.get("fiscal_years") or [mart_layer.latest_fy()])[0]
        row = next((dict(zip(ma.columns, r)) for r in ma.rows if r and r[0] == fy), None)
        if not row or row.get(p.metric) is None:
            continue
        value = float(row[p.metric])
        if abs(value - p.value) <= p.tol:
            continue
        head = re.match(r"\s*\*\*(.+?)\*\*", ma.answer or "", re.S)
        if not head:
            continue
        sentence = head.group(1).strip()
        tail = sentence.split(" was ")[-1] if " was " in sentence else sentence.split(" were ")[-1]
        figs = _FIGURE.findall(re.sub(r"\s*\(₹[\d,.]+\)", "", tail))
        if not figs:
            continue
        lines.append((p.said, figs[0], re.sub(r"\s*\(₹[\d,.]+\)", "", sentence)))
    if not lines:
        return ""
    said = " and ".join(s for s, _, _ in lines)
    shows = " and ".join(f for _, f, _ in lines)
    head = f"**The question says {said}, but the data shows {shows}.**"
    shown = re.match(r"\s*\*\*(.+?)\*\*", answer or "", re.S)
    shown_text = re.sub(r"\s*\(₹[\d,.]+\)", "", shown.group(1)).strip().rstrip(".") if shown else ""
    detail = [s if s.endswith(".") else s + "." for _, _, s in lines if s.rstrip(".") != shown_text]
    if not detail:
        return head                     # the answer itself opens with that figure: lead with the correction only
    return f"{head} {' '.join(detail)} The answer below is from the data."
