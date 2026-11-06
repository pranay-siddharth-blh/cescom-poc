"""
Deterministic router for the no-LLM routes (metric lookup, reports, verified queries): regex + dictionaries over
the mart's entity lists, no model, no database call. app.semantic_layer.extract_entities (the older extractor
whose output goes into the LLM prompt) is unchanged; this one adds what the deterministic routes need:

  subdivisions [names]      aliases and typos included ("arakalagud", "hn pura"); "Holenarasipura division"
                            means the whole division, plain "Holenarasipura" the sub-division
  scope                     'division' when the question says division / overall / total / Division_Total
  fiscal_years [fy]         '2024-25', '2024-2025', 'FY24', 'FY 2025', 'this year', 'last year', 'since 2020',
                            'past 3 years' -- resolved against the data's latest year, never the wall clock
  out_of_range [labels]     years named that are outside 2015-16 .. 2025-26
  relative_year             True when a year came from 'this / last year' wording
  families [codes]          bare tariff families: 'LT-1', 'lt1', 'l.t.-1', 'HT - 2', 'miscellaneous'
  slab_codes [codes]        specific slabs such as 'LT-4(a)', 'HT-2(a)(ii)' (left to the model / slab routes)
  unknown_tariffs [text]    'LT-9', 'HT-8'
  unknown_subdivisions      'Belur sub-division'
  voltage                   'LT' / 'HT' when the question names the class, not a family
  metrics [keys]            from the registry synonyms, longest match first, non-overlapping
  intents                   ranking, comparison, trend, breakdown, change, summary, share
  direction                 'high' / 'low' for rankings
  top_n                     'top 3'
"""
from __future__ import annotations

import re

from app import mart_layer

_SUB_ALIASES = {
    "Arkalgud": ["arkalgud", "arakalagud", "arkalagud", "arkalgood", "arakalgud"],
    "Hangarahalli": ["hangarahalli", "hangarhalli", "hangara halli"],
    "Holenarasipura": ["holenarasipura", "holenarsipura", "holenarasipur", "hole narasipura", "hn pura", "hnpura", "h n pura"],
    "Ramanathapura": ["ramanathapura", "ramanathpura", "ramanathapur", "ramnathpura"],
    "Saligrama": ["saligrama", "saligram"],
    "Konanur": ["konanur", "konnanur", "konanuru"],
    "Division_Office": ["division_office", "division office"],
}
_DIVISION_RE = re.compile(r"\b(division[_ ]total|whole division|entire division|division[- ]wide|division as a whole|"
                          r"overall|all sub-?divisions combined|total division|holenarasipura division|hn ?pura division|"
                          r"the division|division level|for the division|of the division|division)\b(?![- ]?office)", re.I)
_SUBDIVISION_WORD_RE = re.compile(r"\b([a-z][a-z]{2,})\s+(?:sub-?division|sub division|section)\b", re.I)
_NOT_A_PLACE = {"which", "each", "every", "all", "the", "a", "per", "any", "best", "worst", "top", "our", "this", "that",
                "wise", "one", "same", "other", "by", "and", "or", "of", "in", "for", "every", "different", "various",
                "holenarasipura", "division"}

_NAME_AFTER_RE = re.compile(r"\b(?:in|of|for|at|from)\s+([A-Z][a-z]{5,})\b(?!\s*(?:sub-?division|section)\b)")
_KNOWN_WORDS = {
    "holenarasipura", "division", "karnataka", "india", "chamundeshwari", "cescom", "government", "jyothi", "gruha",
    "bhagya", "kutira", "bhagyajyothi", "kutirajyothi", "gruhajyothi", "streetlights", "january", "february", "march", "april", "august", "september", "october", "november",
    "december", "demand", "collection", "arrears", "balance", "installations", "consumption", "domestic", "commercial",
    "industries", "industrial", "irrigation", "agriculture", "hospitals", "schools", "temples", "water", "street",
    "total", "overall", "average", "subsidy", "adjustment", "efficiency", "opening", "closing", "outstanding", "billing",
    "tariff", "category", "categories", "rupees", "crore", "crores", "lakhs", "percent", "english", "kannada", "hindi",
    "fiscal", "financial", "previous", "current", "latest", "earlier", "recent", "public", "lighting", "heating",
    "motive", "power", "energy", "electricity", "units", "kerc", "amount", "figures", "numbers", "summary", "detail",
    "details", "terms", "words", "lakh", "today", "yesterday", "tomorrow", "general", "metered", "unmetered", "rural",
    "urban", "private", "educational", "institutions", "charging", "station", "stations", "vehicles", "temporary",
    "apartments", "residential", "advertisement", "hoardings", "horticulture", "nurseries", "coffee", "plantation",
    "plantations", "sericulture", "municipal", "panchayat", "panchayats", "village", "villages", "corporation",
}

_INTENTS = {
    "ranking": r"\b(highest|lowest|top|bottom|best|worst|most|least|maximum|minimum|max|min|largest|smallest|biggest|"
               r"rank\w*|leading|lagging|which (sub-?division|area|section|tariff|category|family|slab|year|one)s?|"
               r"which \w+ (has|had|collects?|pays?|is|was)|who (collects?|pays?))\b",
    "comparison": r"\b(compare[ds]?|comparison|comparing|versus|vs\.?|against|or)\b",
    "trend": r"\b(trend|trends|over the years|over years|year[- ]?wise|year[- ]on[- ]year|yoy|each year|every year|all years|"
             r"across (all )?(the )?years|by year|per year|yearly|annual(ly)?|history|historical|growth|since|past \d+ years?|"
             r"last \d+ years?|through the years|throughout the years?|11 years|ten years|10 years)\b",
    "breakdown": r"\b(break ?down|split|by (tariff|category|categories|family|families|slab|sub-?division|section|area)|"
                 r"(tariff|category|categor(y|ies)|family|slab|sub-?division|area)[- ]wise|each (tariff|category|sub-?division|family)|"
                 r"all (tariffs|categories|sub-?divisions|families|slabs)|per (sub-?division|tariff|category)|distribution|mix|"
                 r"composition|contribut\w*)\b",
    "change": r"\b(change|changed|increase[ds]?|decrease[ds]?|grew|grow|growth|rise|rose|fall|fell|drop(ped)?|jump(ed)?|"
              r"up or down|difference|compared (to|with) (last|previous)|vs last year|than last year)\b",
    "summary": r"\b(summary|summari[sz]e|overview|snapshot|numbers|figures|kpis?|status|position|performance|report card|"
               r"scorecard|how (did|is|was|are|were) .{0,30}(doing|do|perform\w*|going))\b",
    "share": r"\b(share|percentage of total|% of total|proportion|portion|contribution)\b",
}
_HIGH_RE = re.compile(r"\b(highest|top|best|most|maximum|max|largest|biggest|leading|better|more)\b", re.I)
_LOW_RE = re.compile(r"\b(lowest|bottom|worst|least|minimum|min|smallest|lagging|poor\w*|not paying|weak\w*|low)\b", re.I)


def _measure_words() -> set[str]:
    return {w for syn, _ in mart_layer.metric_synonyms() for w in re.findall(r"[a-z]{5,}", syn)}


def _fix_measure_typos(text: str) -> str:
    """'colection efficency' -> 'collection efficiency': a word of 5+ letters that is not a measure word but is one
    letter-slip from one (same first letter, close spelling) is read as that word; measure words spelt right are
    never touched, so the longest measure name still wins."""
    import difflib
    import os
    vocab = _measure_words()

    def fix(m):
        w = m.group(0)
        if w in vocab or len(w) < 5:
            return w
        # a real form of a measure word ("collects", "collecting", "installed") shares its stem: never a slip
        if any(len(os.path.commonprefix([v, w])) >= 7 for v in vocab):
            return w
        best = difflib.get_close_matches(w, [v for v in vocab if v[0] == w[0] and abs(len(v) - len(w)) <= 2], 1, 0.8)
        return best[0] if best else w
    return re.sub(r"[a-z]+", fix, text)


def _norm(q: str) -> str:
    s = (q or "").lower().replace("’", "'").replace("–", "-").replace("—", "-")
    return " ".join(s.split())


def _fy(start: int) -> str:
    return f"{start}-{(start + 1) % 100:02d}"


def _year4(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 else n


def extract_years(q: str) -> tuple[list[str], list[str], bool, str]:
    """(fiscal years in range, out-of-range labels, relative wording used, text with matches blanked)."""
    fys, bad, relative = [], [], False
    valid = set(mart_layer.fiscal_years())
    latest = mart_layer.latest_fy()
    text = q

    def take(m):
        nonlocal text
        text = text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]

    def add(fy: str, label: str | None = None):
        (fys if fy in valid else bad).append(fy if fy in valid else (label or fy))

    all_fys = sorted(valid)
    li = all_fys.index(latest)
    for m in list(re.finditer(r"\b(?:past|last|previous|recent)\s+(\d{1,2}|two|three|four|five)\s+(?:fiscal\s+|financial\s+)?years?\b", text)):
        n = {"two": 2, "three": 3, "four": 4, "five": 5}.get(m[1], None) or int(m[1])
        fys.extend(all_fys[max(0, li - n + 1):li + 1])
        relative = True
        take(m)
    for m in list(re.finditer(r"\b(?:since|from)\s+(?:fy\s*)?(20\d{2})(?:\s*-\s*(\d{2}))?(?:\s*(?:to|till|until|-)\s*(?:fy\s*)?(20\d{2})(?:\s*-\s*\d{2})?)?\b", text)):
        start = int(m[1])
        end_start = int(m[3]) if m[3] else int(latest[:4])
        fys.extend([f for f in all_fys if start <= int(f[:4]) <= end_start])
        take(m)
    rel = [(r"\b(this|current|present|latest|curent|currnt|recent)\s+(fy|financial year|fiscal year|year)\b", latest),
           (r"\b(last|previous|prior|preceding)\s+(fy|financial year|fiscal year|year)('s)?\b",
            all_fys[li - 1] if li > 0 else latest)]
    for pat, fy in rel:
        for m in list(re.finditer(pat, text)):
            fys.append(fy)
            relative = True
            take(m)
    # FY 2024-25, 2024-25, 2024-2025, FY24-25
    for m in list(re.finditer(r"\b(?:fy|financial year|fiscal year)?\s*'?(20\d{2}|\d{2})\s*[-/]\s*(20\d{2}|\d{2})\b", text)):
        a, b = _year4(m[1]), _year4(m[2])
        if b == a + 1:
            add(_fy(a), f"{a}-{b % 100:02d}")
            take(m)
    # FY24 / FY 2025 = the year ending in March of that year
    for m in list(re.finditer(r"\bfy\s*'?(20\d{2}|\d{2})\b", text)):
        end = _year4(m[1])
        add(_fy(end - 1), f"FY{m[1]}")
        take(m)
    seen, out = set(), []
    for f in fys:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out, bad, relative, text


def extract(question: str) -> dict:
    q = _norm(question)
    e: dict = {"subdivisions": [], "fiscal_years": [], "out_of_range": [], "families": [], "slab_codes": [],
               "unknown_tariffs": [], "unknown_subdivisions": [], "metrics": [], "intents": []}
    fys, bad, relative, rest = extract_years(q)
    e["fiscal_years"], e["out_of_range"], e["relative_year"] = fys, bad, relative

    # sub-divisions ("holenarasipura division" is the division; "holenarasipura sub-division" is the sub-division)
    div_named = bool(re.search(r"\b(holenarasipura|hn ?pura)\s+division\b", rest))
    for name, aliases in _SUB_ALIASES.items():
        for a in aliases:
            for m in re.finditer(r"(?<![a-z])" + re.escape(a) + r"(?![a-z])", rest):
                after = rest[m.end():m.end() + 12]
                if name == "Holenarasipura" and re.match(r"\s+division\b", after):
                    continue
                if name not in e["subdivisions"]:
                    e["subdivisions"].append(name)
    if div_named or _DIVISION_RE.search(re.sub(r"sub-?\s?divisions?", " ", rest)):
        e["scope"] = "division"
    for m in re.finditer(r"\b(?:sub-?division|section)\s+(?:number|no\.?|#)?\s*(\d+)\b", rest):
        e["unknown_subdivisions"].append(f"section {m[1]}")
    for m in _SUBDIVISION_WORD_RE.finditer(rest):
        w = m.group(1).lower()
        known = any(w in x.split() for al in _SUB_ALIASES.values() for x in al)   # "hn pura sub-division"
        if not known and w not in _NOT_A_PLACE:
            e["unknown_subdivisions"].append(m.group(1).title())
    # A capitalised name after "in / of / for / at" that is no place, term or month of this data ("What was the
    # collection of Channarayapatna in 2024-25?"): never answered with the division's figures.
    for m in _NAME_AFTER_RE.finditer(question or ""):
        w = m.group(1)
        lw = w.lower()
        if (lw in _NOT_A_PLACE or lw in _KNOWN_WORDS or any(lw in al for al in _SUB_ALIASES.values())
                or any(lw.startswith(a[:6]) for al in _SUB_ALIASES.values() for a in al if len(a) >= 6)):
            continue
        if w.title() not in e["unknown_subdivisions"]:
            e["unknown_subdivisions"].append(w.title())

    # tariff codes: specific slabs first, then bare families, then unknown families
    codes = mart_layer.load()["entities"]["tariff_codes"]
    compact = re.sub(r"\s+", "", rest)
    slab_hits = [c for c in codes if "(" in c and re.sub(r"\s+", "", c.lower()) in compact]
    e["slab_codes"] = [c for c in slab_hits if not any(o != c and o.lower().startswith(c.lower()) for o in slab_hits)]
    # "HT-2(a)" when the data prints only its sub-slabs ("HT-2(a)(ii)"): every slab under that prefix
    for m in re.finditer(r"(?<![a-z0-9])([lh])\s*\.?\s*t\s*-?\s*(\d{1,2})\s*\(\s*([a-z])\s*\)(?!\s*\()", rest):
        pre = f"{m[1]}t-{m[2]}({m[3]})"
        if not any(re.sub(r"\s+", "", c.lower()) == pre for c in codes):
            for c in codes:
                if re.sub(r"\s+", "", c.lower()).startswith(pre) and c not in e["slab_codes"]:
                    e["slab_codes"].append(c)
    fam_codes = set(mart_layer.families())
    for m in re.finditer(r"(?<![a-z0-9])([lh])\s*\.?\s*t\s*\.?\s*-?\s*(\d{1,2})(?!\d)(\s*\(\s*[a-z0-9]+\s*\))?", rest):
        code = f"{m[1].upper()}T-{m[2]}"
        if m[3]:
            continue   # LT-4(a): a slab, handled above
        if code in fam_codes:
            if code not in e["families"]:
                e["families"].append(code)
        else:
            e["unknown_tariffs"].append(code)
    # A code-shaped token that is no tariff this data has ("ZZ-99", "XT-4"): never answered with a total.
    for m in re.finditer(r"(?<![a-z0-9])([a-z]{2,3})\s*-\s*(\d{1,3})(?![0-9])", rest):
        if m[1] not in ("lt", "ht", "fy", "wy", "cy", "no", "sl"):
            e["unknown_tariffs"].append(f"{m[1].upper()}-{m[2]}")
    if re.search(r"\bmisc(ellaneous)?\b", rest) and "MISC" not in e["families"]:
        e["families"].append("MISC")
    if not e["families"] and not e["slab_codes"] and not e["unknown_tariffs"]:
        if re.search(r"\blt\b|\blow[- ]tension\b", rest):
            e["voltage"] = "LT"
        if re.search(r"\bht\b|\bhigh[- ]tension\b", rest):
            e["voltage"] = "HT" if "voltage" not in e else "both"

    # metrics (longest synonym first, non-overlapping)
    taken, found = _fix_measure_typos(rest), []     # "colection efficency" -> "collection efficiency"
    for syn, key in mart_layer.metric_synonyms():
        for m in list(re.finditer(r"(?<![a-z])" + re.escape(syn) + r"(?![a-z])", taken)):
            found.append((m.start(), key))
            taken = taken[:m.start()] + " " * (m.end() - m.start()) + taken[m.end():]
    for _, key in sorted(found):          # in the order the question names them
        if key not in e["metrics"]:
            e["metrics"].append(key)
    # "average billed rate per unit": the rate, not the demand its word "billed" also names
    if "demand_per_unit" in e["metrics"] and "demand" in e["metrics"] and not re.search(r"\bdemand\b", rest):
        e["metrics"].remove("demand")
    # "subsidy share", "Gruha Jyothi share of collection": the adjusted share, not the adjusted amount
    if "adjustment_collection" in e["metrics"] and re.search(r"\b(share|percentage|proportion)\b|%", rest):
        e["metrics"][e["metrics"].index("adjustment_collection")] = "adjustment_share"
        taken = re.sub(r"\b(share|percentage|proportion)\b|%", lambda m: " " * len(m.group(0)), taken)
    # a per-unit / per-installation measure is an average by definition: "average" is part of its name, not a request
    if any(k.endswith(("_per_unit", "_per_installation")) for k in e["metrics"]):
        taken = re.sub(r"\b(average|avg|mean)\b", lambda m: " " * len(m.group(0)), taken)

    for intent, pat in _INTENTS.items():
        if re.search(pat, rest if intent != "trend" else q):
            e["intents"].append(intent)
    # "FPPCA share of demand", "MNR %": a share that is part of a metric's own name is not a request for a breakdown
    if "share" in e["intents"] and e["metrics"] and not re.search(_INTENTS["share"], taken):
        e["intents"].remove("share")
    e["rest_text"] = taken
    if "comparison" in e["intents"] and re.search(r"\bor\b", rest) and not re.search(
            r"\b(compare|vs|versus|which|better|more|less|higher|lower)\b", rest):
        e["intents"].remove("comparison")   # "demand or collection" is not a comparison of two places
    if _HIGH_RE.search(rest):
        e["direction"] = "high"
    if _LOW_RE.search(rest):
        e["direction"] = "low" if "direction" not in e or re.search(r"\b(lowest|worst|least|bottom)\b", rest) else e["direction"]
    m = re.search(r"\b(?:top|bottom|best|worst|highest|lowest)\s+(\d{1,2})\b", rest)
    if m:
        e["top_n"] = int(m[1])
    return e
