"""
Follow-up questions (pattern ported from the RDPR POC's app/followup.py, read-only reference; rewritten for the DCB
vocabulary). After "What was the collection efficiency in Arkalgud in 2024-25?" an officer asks "and Konanur?", "what
about 2023-24?", "for the whole division?", "and the demand?" or "and HT-2?". The page sends the last question it had
answered with the new one; resolve() rewrites a follow-up into a whole question by putting its place, year, tariff
category or measure in place of the previous question's. That whole question then goes through the unchanged pipeline
(every screen and guard applies to it), and the page shows "Taking this as: ...". Deterministic, no model call.

A follow-up is short and made only of places, years, tariff codes and measures plus linking words ("and", "what about",
"same for", "the", "in" ...). Without a cue word ("and", "what about" ...) it must also name no measure or tariff code
("Konanur?", "2023-24?"), so a short complete question ("Konanur demand 2024-25") is never bent onto the previous one.
When the new part cannot be placed cleanly, nothing is rewritten: the question is answered as asked.
"""
from __future__ import annotations

import re

from app import mart_layer
from app.router import _DIVISION_RE, _SUB_ALIASES

MAX_WORDS = 10
_CUE_RE = re.compile(r"^(?:(?:ok|okay|so|now|then|also)[\s,]+)*(?:and|what about|how about|what of|same|the same|now|also|"
                     r"then|for|in)\b")
_FILLER = {"and", "what", "about", "how", "of", "for", "in", "the", "same", "now", "then", "also", "ok", "okay", "so",
           "please", "instead", "there", "too", "as", "well", "with", "on", "at", "by", "to", "is", "it", "its", "was",
           "were", "that", "'s", "s", "sub", "division", "subdivision", "sub-division", "section", "fy", "financial",
           "fiscal", "year", "thanks", "great", "nice", "number", "count", "amount", "value", "figure", "figures"}
MAX_PREVIOUS = 600                  # a longer `previous` is not a question the page sent: ignored
# "FY 2024-25", "financial year 2024-2025", "fy 24-25", "2024-25": one span each (never "fy 24" with "-25" left over)
_PERIOD_RES = [re.compile(r"\b(?:(?:fy|financial year|fiscal year)\s*'?)?(?:20\d\d|\d\d)\s*[-/]\s*(?:20)?\d\d\b"),
               re.compile(r"\bfy\s*'?\d{2,4}\b"),
               re.compile(r"\b(?:this|last|previous|prior|current|latest) (?:fiscal |financial )?year\b")]
_TARIFF_RE = re.compile(r"\b(?:l\.?\s?t|h\.?\s?t)\s*[-.]?\s*\d+(?:[a-z](?![a-z]))?(?:\s?\([a-z]{1,4}\))*", re.I)
_BARE_FY = re.compile(r"^20\d\d\s*[-/]\s*(?:20)?\d\d$")
# a question that ranks or lists places: a follow-up naming one place asks for that place's own figure
_GROUPED = re.compile(r"\b(?:which (?:sub-?division|area|section|one)|sub-?divisions|each sub-?division|by sub-?division|"
                      r"sub-?division[- ]wise|area[- ]wise|rank\w*|best|worst|highest|lowest|most|least)\b")


def _canon(text: str) -> str:
    s = (text or "").replace("’", "'").replace("–", "-").replace("—", "-")
    return " ".join(s.split())


def _free(span, taken) -> bool:
    return not any(span[0] < b and a < span[1] for a, b in taken)


def _places(low: str) -> list[tuple[str, int, int]]:
    """(canonical place, start, end): sub-divisions by any alias, and the whole division."""
    out, taken = [], []
    for m in _DIVISION_RE.finditer(low):
        if re.search(r"sub[- ]?$", low[:m.start()]):
            continue                    # the "division" of "sub-division"
        out.append(("the whole division", *m.span()))
        taken.append(m.span())
    pairs = sorted(((a.lower().replace("_", " "), name) for name, al in _SUB_ALIASES.items() for a in [name, *al]),
                   key=lambda x: -len(x[0]))
    for alias, name in pairs:
        for m in re.finditer(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", low):
            if _free(m.span(), taken):
                b = m.end()
                tail = re.match(r"\s+(?:sub-?division|sub division|section)\b", low[b:])
                out.append((name.replace("_", " "), m.start(), b + (tail.end() if tail else 0)))
                taken.append((m.start(), b))
    return sorted(out, key=lambda p: p[1])


def _spans(rxs, low: str, taken) -> list[tuple[int, int]]:
    taken = list(taken)
    out = []
    for rx in rxs:
        for m in rx.finditer(low):
            if _free(m.span(), taken):
                out.append(m.span())
                taken.append(m.span())
    return sorted(out)


def _metrics(low: str, taken) -> list[tuple[int, int]]:
    taken = list(taken)
    out = []
    for syn, _key in mart_layer.metric_synonyms():
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(syn) + r"(?![a-z0-9])", low):
            if _free(m.span(), taken):
                out.append(m.span())
                taken.append(m.span())
    return sorted(out)


def _parts(text: str):
    low = text.lower()
    places = _places(low)
    taken = [(a, b) for _, a, b in places]
    periods = _spans(_PERIOD_RES, low, taken)
    tariffs = _spans([_TARIFF_RE], low, taken + periods)
    metrics = _metrics(low, taken + periods + tariffs)
    return low, places, periods, tariffs, metrics


def _only_fillers(low: str, places, periods, tariffs, metrics) -> bool:
    if len(low.split()) > MAX_WORDS:
        return False
    rest = list(low)
    for a, b in [(a, b) for _, a, b in places] + periods + tariffs + metrics:
        rest[a:b] = " " * (b - a)
    return all(w in _FILLER for w in re.findall(r"[a-z0-9'-]+", "".join(rest)))


def is_fragment(question: str) -> bool:
    """A short follow-up made only of places, periods, tariff codes, measures and linking words, opened by a cue word
    ("and Konanur?", "what about the balance?"). Voice mode uses it so such a turn is not dropped as a fragment."""
    text = _canon(question).rstrip(" ?.!")
    low, places, periods, tariffs, metrics = _parts(text)
    return bool(_CUE_RE.match(low)) and _only_fillers(low, places, periods, tariffs, metrics) and         bool(places or periods or tariffs or metrics)


def about_data(question: str) -> bool:
    """The question names a place, year, tariff category or measure, so a follow-up can build on it."""
    _, places, periods, tariffs, metrics = _parts(_canon(question).rstrip(" ?.!"))
    return bool(places or periods or tariffs or metrics)


def _code(text: str) -> str:
    """'lt 4(a)' / 'ht-2' -> 'LT-4(a)' / 'HT-2': the prefix in capitals, the slab letters as written."""
    m = re.match(r"(?i)(l\.?\s?t|h\.?\s?t)\s*[-.]?\s*(.*)", text.strip())
    return (m.group(1).replace(".", "").replace(" ", "").upper() + "-" + m.group(2).replace(" ", "")) if m else text


def _fy(s: str) -> str:
    return "FY " + s if _BARE_FY.match(s) else s


def _plain(new, body, places, periods, tariffs, metrics, pplaces, pperiods, ptariffs, pmetrics) -> str | None:
    """"What was the <measure> of <place> in <year> for <tariff>?" from the new parts, else the previous ones."""
    if metrics:
        measure = new[metrics[0][0]:metrics[-1][1]]
    elif pmetrics:
        measure = body[pmetrics[0][0]:pmetrics[-1][1]]
    elif re.search(r"\bcollect", body.lower()):
        measure = "collection efficiency"           # "which sub-division collects the best" ranks by it
    elif re.search(r"\b(dues|arrears|pending)\b", body.lower()):
        measure = "closing balance"
    else:
        return None
    place = places[0][0] if places else (pplaces[0][0] if pplaces else "")
    when = [_fy(new[a:b]) for a, b in periods] or [_fy(body[a:b]) for a, b in pperiods]
    tar = [new[a:b] for a, b in tariffs] or [body[a:b] for a, b in ptariffs]
    out = (f"What was the {measure}" + (f" of {tar[0]}" if tar else "") + (f" in {place}" if place else "")
           + (f" in {' and '.join(when)}" if when else "") + "?")
    return out[0].upper() + out[1:]


def resolve(question: str, previous: str) -> str | None:
    """The whole question a follow-up stands for, or None when `question` is not a follow-up of `previous`."""
    prev, new = _canon(previous), _canon(question)
    if not prev or not new or prev.lower() == new.lower() or len(prev) > MAX_PREVIOUS:
        return None
    new = new.rstrip(" ?.!")
    low, places, periods, tariffs, metrics = _parts(new)
    if not (places or periods or tariffs or metrics) or not _only_fillers(low, places, periods, tariffs, metrics):
        return None
    if (metrics or tariffs) and not _CUE_RE.match(low):
        return None
    body = prev.rstrip(" ?.!")
    end_mark = prev[len(body):].strip() or "?"
    plow, pplaces, pperiods, ptariffs, pmetrics = _parts(body)
    if not (pplaces or pperiods or ptariffs or pmetrics):
        return None                     # the previous question was not about the data ("What is DCB?")
    edits: list[tuple[int, int, str]] = []
    tail = ""

    if places:
        if len(places) > 1:
            return None
        phrase = places[0][0]
        if _GROUPED.search(plow) and len(pplaces) != 1:
            # "Which sub-division collects the best?" -> "and Konanur?": Konanur's own figure, not a ranking
            return _plain(new, body, places, periods, tariffs, metrics, pplaces, pperiods, ptariffs, pmetrics)
        if not pplaces:
            tail += " in " + phrase
        elif len(pplaces) > 1:
            edits.append((pplaces[-1][2], pplaces[-1][2], " and " + phrase))   # a comparison list: the new one joins it
        else:
            _, a0, b0 = pplaces[0]
            edits.append((a0, b0, phrase))

    if periods:
        said = [_fy(new[a:b]) for a, b in periods]
        if not pperiods:
            tail += " in " + " and ".join(said)
        elif len(pperiods) == len(said):
            edits += [(a, b, s) for (a, b), s in zip(pperiods, said)]
        elif len(pperiods) == 1:
            edits.append((*pperiods[0], " and ".join(said)))
        else:
            return None

    if tariffs:
        said = [_code(new[a:b]) for a, b in tariffs]
        if not ptariffs:
            tail += " for " + " and ".join(said)
        elif len(ptariffs) == len(said):
            edits += [(a, b, s) for (a, b), s in zip(ptariffs, said)]
        elif len(ptariffs) == 1:
            edits.append((*ptariffs[0], " and ".join(said)))
        else:
            return None

    if metrics:
        if len(pmetrics) != 1:
            return _plain(new, body, places, periods, tariffs, metrics, pplaces, pperiods, ptariffs, pmetrics)
        edits.append((*pmetrics[0], new[metrics[0][0]:metrics[-1][1]]))

    out = body
    for a, b, text in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        out = out[:a] + text + out[b:]
    out = " ".join((out + tail).split()) + end_mark
    return out[0].upper() + out[1:]
