"""
Slab-level questions answered deterministically from the mart's slab facts (no model). Before this route, any question
naming something narrower than a tariff family ("BMA lighting", "unmetered IP sets", "EV charging", "industries",
"LT-4(a)", a quoted slab description) went to the model, and its answer varied with the gateway model of the day.
Following the KPCL POC's ladder (deterministic first, the model only for what is left, ask instead of guessing):

  1. resolve the wording to an exact set of (tariff_code, description) slabs from mart_dim_tariff:
       * consumer segments ("domestic", "industries", "EV charging", "IP sets", "BMA", "water supply" ...), matched by
         the slab DESCRIPTION, so a series stays comparable across the 2024-25 re-coding (LT-2(a) -> LT-1, LT-6 ->
         LT-6 A/B/C, LT-7 b -> LT-3 b);
       * qualifiers that narrow a segment or a family ("free lighting", "unmetered", "rural feeder", "<= 10 HP",
         "33 kV", "prepaid", "village panchayat") -- they intersect, never widen;
       * slab codes ("LT-4(a)", "HT-2(c)(i)"), a quoted description, or one clear fuzzy match;
       * a family code plus a qualifier ("LT-4 unmetered");
  2. several equally good fuzzy matches -> a clarifying question listing them (never a guess, never the model);
  3. answer from mart_fact_slab_division_year / _subdivision_year with registry metric expressions and bound
     parameters only: one year (with the previous year for context), a series, sub-divisions side by side,
     segments compared, or slabs ranked; every answer lists exactly which slabs were counted.
Nothing resolvable -> None, and the question moves on to the model as before.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app import mart_layer
from app.answer import build_chart, narrate
from app.db import ReadOnlyDB
from app.metric_lookup import default_period_note, fmt_metric

T_DIV = "mart_fact_slab_division_year"
T_SUB = "mart_fact_slab_subdivision_year"
SUMMARY_KEYS = ["installations", "demand", "collection", "collection_efficiency", "closing_balance"]
MAX_LISTED = 8


@dataclass(frozen=True)
class Segment:
    key: str
    label: str
    alias: str                 # regex over the (lower-cased) question
    desc: str                  # regex over the slab description (case-insensitive)
    qualifier: bool = False    # narrows the set instead of adding to it
    standalone: bool = False   # a qualifier that may also stand alone ("free lighting demand")


# Most specific first: a phrase is consumed by the first segment that matches it ("lift irrigation" never also
# counts as "irrigation"). Description patterns were written against every description in mart_dim_tariff.
SEGMENTS: list[Segment] = [
    Segment("bjkj", "Bhagyajyothi / Kutirajyothi (BJ/KJ)",
            r"bhagya\s*-?\s*jy?othi|kutira\s*-?\s*jy?othi|\bbj\s*/?\s*kj\b|\bbv\s*/\s*kj\b", r"Bhagyajyothi|Kutirajyothi"),
    Segment("bma", "lighting, heating & motive power in BMA / municipal-corporation areas",
            r"(?<!than )\bbma\b|municipal\s+corp\w*", r"BMA\s*&\s*Municipal"),
    Segment("ulb_domestic", "lighting, heating & motive power in urban-local-body areas",
            r"urban\s+local\s+bod(y|ies)", r"Urban Local\s+Bodies"),
    Segment("pvt_inst", "private institutions", r"(pvt\.?|private)\s+(educational\s+)?institu\w*", r"Pvt\.\s*Institu"),
    Segment("hoardings", "hoardings & advertisement boards", r"hoardings?|advertis\w*", r"Hoardings"),
    Segment("railway", "railway traction", r"railways?|traction", r"Railway"),
    Segment("govt_hosp", "government, charitable & university hospitals and hostels",
            r"(govt\.?|government|charitable|esi)\s+(hospitals?|institu\w*|hostels?)|universit(y|ies)|\besi\b",
            r"^Govt\.,\s*Chartable"),
    Segment("ev", "EV charging stations",
            r"\bev\b|\be\.v\.|electric\s+vehicles?|vehicle\s+charging|charging\s+stations?", r"Charging Station"),
    Segment("public_lighting", "public / street lighting",
            r"street\s*-?\s*lights?|street\s*-?\s*lighting|public\s+lighting|led\s+lighting", r"Public Lighting"),
    Segment("water_supply", "water supply & sewerage pumping",
            r"water\s*-?\s*supply|water\s*works|drinking\s+water|sewerage|sewage", r"Water Supply"),
    Segment("apartments", "residential apartments",
            r"apartments?|residential\s+(complex\w*|colon\w*)|housing\s+colon\w*", r"Apartments"),
    Segment("lift_irrigation", "lift irrigation schemes", r"lift\s+irrigation|\bli\s+schemes?", r"Lift Irrigation|LI schemes"),
    Segment("horticulture", "horticulture, nurseries & plantations",
            r"horticult\w*|nurser(y|ies)|coffee|\btea\b|plantations?|rubber|areca\w*|coconut", r"Horticu|Plantation"),
    Segment("ip_sets", "irrigation pump (IP) sets",
            r"\bip\s*-?\s*sets?\b|irrigation\s+pump\s*-?\s*sets?|pump\s*-?\s*sets?|agricultural\s+pumps?|farm\s+pumps?",
            r"^IP Sets"),
    Segment("agri_farms", "irrigation & agricultural farms (HT)",
            r"agricultur\w*\s+farms?|irrigation\s+(and|&)\s+agricultur\w*",
            r"Irrigation\s*(&|and)\s*agricultur|Agriculture\s+forms"),
    Segment("temporary", "temporary supply", r"temporary|temp\.?\s+supply|short[- ]term\s+supply", r"Temporary Supply"),
    Segment("commercial", "commercial", r"\bcommercial\b|\bshops?\b|\bbusiness(es)?\b", r"^Comme\.|^Commercial|^HT Commercial"),
    Segment("industrial", "industrial (LT heating & motive power and HT industrial)",
            r"industr\w*|factor(y|ies)|manufactur\w*", r"^Heating\s*&\s*Motive Power|^Industrial"),
    Segment("domestic", "domestic (BJ/KJ, and lighting & heating in BMA / municipal and urban-local-body areas)",
            r"\bdomestic\b|\bhouseholds?\b|\bhomes?\b|\bhouses\b|\bresidences?\b",
            r"Bhagyajyothi|Kutirajyothi|BMA\s*&\s*Municipal|Urban Local\s+Bodies"),
    Segment("agriculture", "agriculture (IP sets, lift irrigation and agricultural farms)",
            r"farmers?|agricultur\w*|\birrigation\b",
            r"^IP Sets|Lift Irrigation|LI schemes|Irrigation\s*(&|and)\s*agricultur|Agriculture\s+forms"),
    # qualifiers
    Segment("free_lighting", "free lighting", r"free\s+lighting|\(fl\)", r"Free Lighting", True, True),
    Segment("office_lighting", "office lighting", r"office\s+lighting|\(ol\)", r"Office Lighting", True, True),
    Segment("prepaid", "prepaid meter", r"pre-?\s?paid", r"Prepaid", True, True),
    Segment("unmetered", "un-metered", r"un-?\s?metered|without\s+meters?|not\s+metered", r"Un-?\s*Metered", True),
    Segment("metered", "metered", r"(?<![a-z-])metered", r"(?<![A-Za-z-])Metered", True),
    Segment("rural_feeder", "rural feeders", r"rural\s+feeders?", r"Rural Feeder", True),
    Segment("urban_feeder", "urban feeders", r"urban\s+feeders?", r"Urban feeders", True),
    Segment("village", "village panchayat areas",
            r"village\s+pan\w*|gram\s*-?\s*panchayath?\w*|grampanchayath?\w*", r"Village Pan|Grampanchayath", True),
    Segment("urban_area", "urban local body / city areas",
            r"\bulbs?'?s?\b|\bcmc\b|\btmc\b|city\s+corporations?", r"ULB|CMC/TMC|Urban Local", True),
    Segment("le10hp", "10 HP and below",
            r"(up\s*to|upto|<=|≤|below|less\s+than|under|within)\s*10\s*hp|10\s*hp\s*(&|and)?\s*(below|less)",
            r"<=\s*10\s*HP|10 HP & below", True),
    Segment("gt10hp", "above 10 HP", r"(above|more\s+than|over|>)\s*10\s*hp", r"Above 10 HP", True),
    Segment("ge100hp", "100 HP and above",
            r"100\s*hp\s*(&|and)?\s*(above|more)|(above|over|more\s+than)\s*100\s*hp", r"100 HP & Above", True),
    Segment("lt100hp", "below 100 HP", r"(below|under|less\s+than)\s*100\s*hp", r"below 100 HP", True),
    Segment("le5hp", "5 HP and below", r"(up\s*to|upto|<=|≤|below|less\s+than|under)\s*5\s*hp", r"<=\s*5 HP", True),
    Segment("5to40hp", "5 to 40 HP", r"\b5\s*(to|-|–)\s*40\s*hp", r"5<x<40", True),
    Segment("40to67hp", "40 to 67 HP", r"\b40\s*(to|-|–)\s*67\s*hp", r"40<x<67", True),
    Segment("ge67hp", "67 HP and above", r"67\s*hp\s*(&|and)?\s*(above|more)|(>=|above|over)\s*67\s*hp",
            r">=\s*67 HP|67 HP and above", True),
    Segment("11kv", "11 kV", r"\b11\s*kv\b", r"\b11 KV", True),
    Segment("33kv", "33 kV", r"\b33\s*kv\b", r"\b33 KV", True),
    Segment("66kv", "66 kV", r"\b66\s*kv\b", r"\b66 KV", True),
]
_BY_KEY = {s.key: s for s in SEGMENTS}


@dataclass(frozen=True)
class Slab:
    code: str
    desc: str
    family: str
    voltage: str
    first: str
    last: str


@dataclass
class SlabAnswer:
    key: str
    answer: str
    sql: str = ""
    params: list = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    chart: dict | None = None
    total: tuple | None = None
    answerable: bool = True
    notes: list[str] = field(default_factory=list)
    slabs: list[Slab] = field(default_factory=list)


_CATALOG: list[Slab] | None = None


async def catalog(db: ReadOnlyDB, timeout: float) -> list[Slab]:
    """Every non-miscellaneous slab with a description (read once from the mart, cached for the process)."""
    global _CATALOG
    if _CATALOG is None:
        res = await db.run("SELECT tariff_code, description, family_code, voltage_class, first_fiscal_year, "
                           "last_fiscal_year FROM mart_dim_tariff WHERE is_misc = 0 AND description IS NOT NULL "
                           "ORDER BY family_code, tariff_code, description", max_rows=1000, timeout_seconds=timeout)
        _CATALOG = [Slab(*r) for r in res.rows]
    return _CATALOG


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _code_key(code: str) -> str:
    return re.sub(r"\s+", "", (code or "").lower())


def match_segments(question: str) -> tuple[list[Segment], list[Segment]]:
    """(base segments, qualifiers) named in the question; each phrase is consumed by its first (most specific) match."""
    text = " " + (question or "").lower().replace("’", "'") + " "
    base, quals = [], []
    for seg in SEGMENTS:
        m = re.search(seg.alias, text)
        if not m:
            continue
        text = re.sub(seg.alias, lambda x: " " * len(x.group(0)), text)
        (quals if seg.qualifier else base).append(seg)
    return base, quals


def _matches(slab: Slab, seg: Segment) -> bool:
    return bool(re.search(seg.desc, slab.desc, re.I))


@dataclass
class Resolution:
    slabs: list[Slab]
    label: str
    groups: list[tuple[str, list[Slab]]] = field(default_factory=list)   # segments to compare side by side
    clarify: list[Slab] = field(default_factory=list)
    concept: str = ""              # a consumer group the data has no single category for ("government")
    empty_reason: str = ""
    by_description: bool = True    # False when the set was defined by tariff codes (family / slab code)


def resolve(question: str, e: dict, llm_entities: dict, cat: list[Slab]) -> Resolution | None:
    q = (question or "").lower()
    base, quals = match_segments(q)
    families = [f for f in e.get("families", []) if f != "MISC"]
    pool = [s for s in cat if s.family in families] if families else list(cat)
    if e.get("voltage") in ("LT", "HT"):
        pool = [s for s in pool if s.voltage == e["voltage"]]
    label = ""
    groups: list[tuple[str, list[Slab]]] = []

    codes = e.get("slab_codes", [])
    exact = llm_entities.get("tariff_category_matches") or []
    if codes:
        want = {_code_key(c) for c in codes}
        chosen = [s for s in pool if _code_key(s.code) in want]
        if not chosen:   # 'LT-2(a)(i)' also names its '(FL)' variant only when nothing matches exactly
            chosen = [s for s in pool if any(_code_key(s.code).startswith(w) for w in want)]
        label = " + ".join(codes)
    elif exact:
        pairs = {(_code_key(x["tariff_code"]), _norm(x["description"])) for x in exact}
        descs = {d for _, d in pairs}
        # the same description under another code in another year (2024-25 re-coding) is the same slab
        chosen = [s for s in pool if _norm(s.desc) in descs]
        label = exact[0]["description"] if len(descs) == 1 else f"{len(descs)} named slabs"
    elif base:
        if len(base) >= 2 and re.search(r"\b(vs\.?|versus|compare\w*|comparison|against)\b", q):
            for seg in base:
                g = [s for s in pool if _matches(s, seg)]
                for qual in quals:
                    g = [s for s in g if _matches(s, qual)]
                groups.append((seg.label, g))
        chosen = [s for s in pool if any(_matches(s, seg) for seg in base)]
        label = " + ".join(seg.label for seg in base)
    elif quals and (families or any(x.standalone for x in quals)):
        chosen = list(pool)
        label = " + ".join(families) if families else ""
    elif families and re.search(r"\b(slabs?|sub-?categor(y|ies)|description)\b", q):
        chosen = list(pool)
        label = " + ".join(families) + " slabs"
    elif re.search(r"\bslabs?\b", q) and set(e.get("intents", [])) & {"ranking", "breakdown", "share"}:
        chosen = list(pool)
        label = "all tariff slabs" + (f" ({e['voltage']})" if e.get("voltage") in ("LT", "HT") else "")
    else:
        cands = llm_entities.get("tariff_category_candidates") or []
        if not cands:
            return _concept(q, pool, cat)
        top = cands[0]["match_score"]
        best = {_norm(c["description"]) for c in cands if c["match_score"] == top}
        if len(best) == 1 and top >= 0.75:
            chosen = [s for s in pool if _norm(s.desc) in best]
            label = cands[0]["description"]
        else:
            seen, options = set(), []
            for c in cands:
                for s in pool:
                    if _norm(s.desc) == _norm(c["description"]) and (s.code, s.desc) not in seen:
                        seen.add((s.code, s.desc))
                        options.append(s)
            return Resolution([], "", clarify=options[:8]) if options else None

    for qual in quals:
        chosen = [s for s in chosen if _matches(s, qual)]
    if quals:
        ql = ", ".join(x.label for x in quals)
        label = f"{label} — {ql}" if label else f"{ql} slabs"
    if not chosen:
        what = ", ".join([x.label for x in base] + [x.label for x in quals] + families + list(codes))
        return Resolution([], label, empty_reason=f"No tariff slab in this data matches {what or 'that description'}.")
    return Resolution(chosen, label, groups=[g for g in groups if g[1]],
                      by_description=not (codes or (families and not base)))


# Consumer groups the DCB statement has NO single category for. Left to the model, these were guessed (found live:
# "share of LT demand from government connections" -> the model summed LT-1 over all demand, and the reviewer passed
# it). Now the answer lists the slabs whose description mentions the group and asks which one is meant, or says plainly
# that there is no such category.
_CONCEPTS = [
    ("government", r"\b(government|govt\.?)\b", r"Govt|Government"),
    ("private", r"\b(private|pvt\.?)\b", r"Pvt|Private"),
    ("hospitals", r"\bhospitals?\b", r"Hospital"),
    ("educational institutions", r"\b(schools?|colleges?|education\w*|universit(y|ies))\b", r"Institu|Univercity|University"),
    ("religious places", r"\b(temples?|mosques?|churches|church|religious)\b", r"Temple|Religious|Church|Mosque"),
    ("rural areas", r"\b(rural|villages?)\b", r"Rural|Village|Grampanchayath|\bGP\b"),
    ("urban areas", r"\b(urban|city|cities|town)\b", r"Urban|ULB|CMC|TMC|BMA"),
    ("public services", r"\bpublic\b", r"Public"),
]


def _concept(q: str, pool: list[Slab], cat: list[Slab]) -> Resolution | None:
    for name, rx, desc in _CONCEPTS:
        if re.search(rx, q):
            hits = [s for s in pool if re.search(desc, s.desc, re.I)]
            if hits:
                return Resolution([], name, clarify=hits[:10], concept=name)
            elsewhere = sorted({s.code for s in cat if re.search(desc, s.desc, re.I)})
            reason = f"The DCB statement has no tariff category or slab for {name}."
            if elsewhere and len(pool) < len(cat):
                reason = (f"None of the tariff slabs asked about is for {name}; the slabs that mention it are "
                          f"{', '.join(elsewhere)}.")
            return Resolution([], name, concept=name, empty_reason=reason)
    return None


# ---------------------------------------------------------------------------------------------- answering
# ---------------------------------------------------------------------------------------------- readable wording
# The sheets print slab descriptions with stray spaces and clipped words ("Comme. Ltng ,heating & Motive Power , in
# ULB's & CC"); answers show them tidied. Segment names carry their definition in brackets ("domestic (BJ/KJ, and
# lighting & heating in BMA / ...)"): headlines and charts use the short name, the slabs counted say the rest.
_DESC_WORDS = [(r"\bComme\.?\s*", "Commercial "), (r"\bLtng\.?\s*", "Lighting "), (r"\bInstitu\.?\s*", "Institutions "),
               (r"\bPvt\.?\s*", "Private "), (r"\bVillAFe\b", "Village"), (r"\bPan\.\s*", "Panchayat ")]


def clean_desc(desc: str) -> str:
    t = desc or ""
    for pat, rep in _DESC_WORDS:
        t = re.sub(pat, rep, t)
    t = re.sub(r"\s+([,.)])", r"\1", t)
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r",(?=\S)", ", ", t)
    return " ".join(t.split())


def short_name(name: str) -> str:
    n = (name or "").split(" (")[0].strip()
    return n[:1].upper() + n[1:] if n else name


def _counted_line(name: str, slabs: list) -> str:
    codes = sorted({s.code for s in slabs})
    shown = ", ".join(codes[:6]) + (f" and {len(codes) - 6} more" if len(codes) > 6 else "")
    return f"•  {short_name(name)}: {len(slabs)} slab{'s' if len(slabs) != 1 else ''} ({shown})"


def _slab_where(slabs: list[Slab]) -> tuple[str, list]:
    conds, params = [], []
    for s in slabs:
        conds.append("(tariff_code = ? AND description = ?)")
        params += [s.code, s.desc]
    return "(" + " OR ".join(conds) + ")", params


def _exprs(keys: list[str]) -> str:
    return ", ".join(mart_layer.metric_sql(k, k) for k in keys)


def _years_span(slabs: list[Slab]) -> tuple[str, str]:
    return min(s.first for s in slabs), max(s.last for s in slabs)


def _included(slabs: list[Slab]) -> str:
    """Which slabs were counted, grouped by description (one slab may carry different codes in different years)."""
    by_desc: dict[str, list[Slab]] = {}
    for s in sorted(slabs, key=lambda x: (x.family, x.desc, x.first)):
        by_desc.setdefault(s.desc, []).append(s)
    lines = []
    for desc, ss in list(by_desc.items())[:MAX_LISTED]:
        codes = "; ".join(f"{s.code}, {s.first}" + (f" to {s.last}" if s.last != s.first else "") for s in ss)
        lines.append(f"•  {clean_desc(desc)} ({codes})")
    more = f"\n•  … and {len(by_desc) - MAX_LISTED} more (see the table)" if len(by_desc) > MAX_LISTED else ""
    return f"Slabs counted ({len(by_desc)}):\n" + "\n".join(lines) + more


def _notes(slabs: list[Slab], fys: list[str], subs: list[str], by_description: bool = True) -> list[str]:
    fams = sorted({s.family for s in slabs})
    cav = mart_layer.caveats(fams, fys, subs)
    if not by_description:
        return cav
    # Matched by description, so a slab keeps its identity across the 2024-25 re-coding: the family-level
    # "LT-1 / LT-2 not comparable" caveat does not apply to these figures.
    cav = [c for c in cav if "not comparable" not in c]
    recoded = {s.desc for s in slabs if s.family == "LT-1" and s.first >= "2024-25"} & \
              {s.desc for s in slabs if s.family == "LT-2"}
    if recoded and (not fys or min(fys) < "2024-25" <= max(fys)):
        cav.append("These slabs are matched by their description, so the figures stay comparable across the 2024-25 "
                   "re-coding (they were printed under LT-2(a) up to 2023-24 and under LT-1 from 2024-25).")
    return cav


def _head(key: str, what: str, place: str, fy: str, v, prev: str | None, p) -> tuple[str, str]:
    meta = mart_layer.metric(key)
    when = f"at the end of {fy}" if meta["kind"] == "stock" else f"in {fy}"
    lab = meta["label"]
    verb = "were" if lab.endswith(("installations", "charges")) else "was"
    text = f"{lab[:1].upper()}{lab[1:]} for {what}{place} {when} {verb} {fmt_metric(key, v)}"
    ch = ""
    if p is not None and v is not None and prev:
        if meta["unit"] == "%":
            ch = f"{'up' if v >= p else 'down'} {abs(v - p):.2f} points from {fmt_metric(key, p)} in {prev}"
        elif p:
            ch = f"{'up' if v >= p else 'down'} {abs(v / p - 1) * 100:.1f}% from {fmt_metric(key, p, exact=False)} in {prev}"
    return text, ch


async def answer(question: str, e: dict, llm_entities: dict, db: ReadOnlyDB, timeout: float) -> SlabAnswer | None:
    if e.get("unknown_tariffs") or e.get("unknown_subdivisions") or e.get("out_of_range"):
        return None
    cat = await catalog(db, timeout)
    r = resolve(question, e, llm_entities, cat)
    if r is None:
        return None
    if r.clarify:
        opts = "\n".join(f"•  {s.code} — {s.desc} ({s.first}" + (f" to {s.last})" if s.last != s.first else ")")
                         for s in r.clarify)
        lead = (f"**The DCB statement has no single \"{r.concept}\" category.** These tariff slabs mention it — which "
                f"one do you mean?" if r.concept else "**That could mean more than one tariff slab.** Which one do you mean?")
        return SlabAnswer("clarify", lead + "\n\n" + opts +
                          "\n\nAsk again with the slab's name or code, for example \"" + r.clarify[0].code +
                          " demand in " + mart_layer.latest_fy() + "\".", answerable=False, slabs=r.clarify)
    if r.empty_reason:
        hint = ""
        if r.concept and "slabs that mention it" not in r.empty_reason:
            fams = [f for f in mart_layer.families() if f != "MISC"]
            hint = (" Its categories are " + ", ".join(fams) + " and miscellaneous revenue; ask for one of them, or for a "
                    "consumer group such as domestic, commercial, industries, IP sets, water supply or street lights.")
        return SlabAnswer("none", f"**{r.empty_reason}**{hint}", answerable=False)

    intents = set(e.get("intents", []))
    subs = [s for s in e.get("subdivisions", []) if s != "Division_Office"]
    fys = e.get("fiscal_years", [])
    keys = [k for k in e.get("metrics", []) if k in mart_layer.metrics()][:3]
    share_q = "share" in intents and not intents & {"ranking", "breakdown"} and not re.search(
        r"\b(each|every|slab-?wise|by slab|which)\b", question, re.I)
    ranking = bool(intents & {"ranking", "breakdown", "share"}) and not r.groups and not share_q
    series = ("trend" in intents or len(fys) >= 2) and not ranking
    if not keys:
        keys = ["demand"] if (ranking or series or r.groups or len(subs) > 1) else SUMMARY_KEYS
    table = T_SUB if subs else T_DIV
    scope_w, scope_p = (["subdivision_name IN (" + ", ".join("?" * len(subs)) + ")"], list(subs)) if subs else ([], [])
    place = (f" in {subs[0]}" if len(subs) == 1 else "") if subs else " (whole division)"
    notes: list[str] = []

    if r.groups:
        return await _compare_groups(r, keys[0], fys, series, table, scope_w, scope_p, place, subs, db, timeout)

    sw, sp = _slab_where(r.slabs)
    first, last = _years_span(r.slabs)

    if series:
        cond = "fiscal_year IN (" + ", ".join("?" * len(fys)) + ")" if len(fys) >= 2 else "1 = 1"
        fy_p = list(fys) if len(fys) >= 2 else []
        sql = (f"SELECT fiscal_year, {_exprs(keys)} FROM {table} WHERE {' AND '.join(scope_w + [sw, cond])} "
               f"GROUP BY fiscal_year ORDER BY fiscal_year")
        params = scope_p + sp + fy_p
        res = await db.run(sql, max_rows=50, timeout_seconds=timeout, params=params)
        if not res.rows:
            return SlabAnswer("none", f"**There are no figures for {r.label}{place} in those years.** These slabs have "
                                      f"figures for {first} to {last}.", sql, params, answerable=False)
        body = narrate(res.columns, res.rows, keys[0])
        lab = mart_layer.metric(keys[0])["label"]
        head = f"**{lab[:1].upper()}{lab[1:]} for {r.label}{place}, {res.rows[0][0]} to {res.rows[-1][0]}.**"
        notes += _notes(r.slabs, [x[0] for x in res.rows], subs, r.by_description)
        text = "\n\n".join([head, body, _included(r.slabs)] + notes)
        extra = [(k, mart_layer.metric(k)["label"]) for k in keys[1:] if mart_layer.metric(k)["unit"] == mart_layer.metric(keys[0])["unit"]]
        chart = build_chart("line", "fiscal_year", keys[0], lab[:1].upper() + lab[1:], res.columns, res.rows, extra)
        total = ("Total", sum(x[1] or 0 for x in res.rows)) if mart_layer.metric(keys[0])["kind"] == "flow" and len(keys) == 1 else None
        return SlabAnswer("series", text, sql, params, res.columns, res.rows, chart, total, notes=notes, slabs=r.slabs)

    fy = fys[0] if fys else mart_layer.latest_fy()
    descs = {s.desc for s in r.slabs}
    renamed = sorted({s.code for s in cat if s.desc in descs and s.first > last})
    if not fys and last < fy:
        fy = last
        notes.append(f"No year was named and these slabs appear up to {last} only, so {last} is used.")
    elif not fys:
        notes.append(default_period_note(fy))
    if renamed and fy >= last:
        notes.append(f"After {last} the same slab description is printed under {', '.join(renamed)}; ask for that code "
                     f"(or the slab by name) for later years.")
    prev = mart_layer.previous_fy(fy)

    if len(subs) > 1:
        sql = (f"SELECT subdivision_name, {_exprs(keys)} FROM {table} WHERE {' AND '.join(scope_w + [sw, 'fiscal_year = ?'])} "
               f"GROUP BY subdivision_name ORDER BY {keys[0]} DESC NULLS LAST")
        params = scope_p + sp + [fy]
        res = await db.run(sql, max_rows=20, timeout_seconds=timeout, params=params)
        if not res.rows:
            return SlabAnswer("none", f"**There are no figures for {r.label} in {', '.join(subs)} in {fy}.**", sql, params,
                              answerable=False)
        lab = mart_layer.metric(keys[0])["label"]
        lines = "\n".join(f"•  {x[0]} — {fmt_metric(keys[0], x[1], False)}" for x in res.rows)
        head = f"**{lab[:1].upper()}{lab[1:]} for {r.label} in {fy}: {res.rows[0][0]} leads with {fmt_metric(keys[0], res.rows[0][1])}.**"
        notes += _notes(r.slabs, [fy], subs, r.by_description)
        text = "\n\n".join([head, lines, _included([s for s in r.slabs if s.first <= fy <= s.last])] + notes)
        chart = build_chart("bar", "subdivision_name", keys[0], lab[:1].upper() + lab[1:], res.columns, res.rows)
        return SlabAnswer("subdivisions", text, sql, params, res.columns, res.rows, chart, notes=notes, slabs=r.slabs)

    # one scope, one year: the total (with the previous year), then the slab-by-slab table
    tot_sql = (f"SELECT fiscal_year, {_exprs(keys)} FROM {table} WHERE {' AND '.join(scope_w + [sw, 'fiscal_year IN (?, ?)'])} "
               f"GROUP BY fiscal_year ORDER BY fiscal_year")
    tot_p = scope_p + sp + [fy, prev or fy]
    tot = await db.run(tot_sql, max_rows=5, timeout_seconds=timeout, params=tot_p)
    by = {x[0]: dict(zip(tot.columns, x)) for x in tot.rows}
    cur, old = by.get(fy), by.get(prev) if prev else None
    if not cur or all(cur.get(k) is None for k in keys):
        why = f" These slabs have figures for {first} to {last} only." if not (first <= fy <= last) else ""
        hint = (f" From {min(s.first for s in cat if s.code in renamed)} the same slab is printed under "
                f"{', '.join(renamed)}.") if renamed else ""
        return SlabAnswer("none", f"**There are no figures for {r.label}{place} in {fy}.**{why}{hint}", tot_sql, tot_p,
                          answerable=False, notes=notes)
    det_sql = (f"SELECT tariff_code, description, {_exprs(keys)} FROM {table} WHERE "
               f"{' AND '.join(scope_w + [sw, 'fiscal_year = ?'])} GROUP BY tariff_code, description "
               f"ORDER BY {keys[0]} {'ASC' if e.get('direction') == 'low' and ranking else 'DESC'} NULLS LAST")
    det_p = scope_p + sp + [fy]
    det = await db.run(det_sql, max_rows=200, timeout_seconds=timeout, params=det_p)
    rows = det.rows[:e["top_n"]] if ranking and e.get("top_n") else det.rows

    parts = []
    if ranking and rows:
        lead = rows[0]
        lab = mart_layer.metric(keys[0])["label"]
        word = "lowest" if e.get("direction") == "low" else "highest"
        parts.append(f"**In {fy}, {lead[0]} ({lead[1]}) had the {word} {lab}{place} among {r.label}: "
                     f"{fmt_metric(keys[0], lead[2])}.**")
        parts.append("\n".join(f"•  {x[0]} — {clean_desc(x[1])[:70]}: {fmt_metric(keys[0], x[2], False)}" for x in rows[:10]))
    else:
        share = ""
        if share_q and cur.get(keys[0]) and mart_layer.metric(keys[0])["kind"] != "ratio":
            all_sql = f"SELECT {_exprs(keys[:1])} FROM {table} WHERE {' AND '.join(scope_w + ['fiscal_year = ?'])}"
            whole = await db.run(all_sql, max_rows=1, timeout_seconds=timeout, params=scope_p + [fy])
            if whole.rows and whole.rows[0][0]:
                lab0 = mart_layer.metric(keys[0])["label"]
                share = f", {cur[keys[0]] / whole.rows[0][0] * 100:.2f}% of all {lab0}{place}"
        for i, k in enumerate(keys):
            head, ch = _head(k, r.label, place, fy, cur.get(k), prev, (old or {}).get(k))
            if i == 0:
                parts.append(f"**{head}{share}.**" + (f" {ch[:1].upper()}{ch[1:]}." if ch else ""))
            else:
                parts.append(f"{head}" + (f"; {ch}." if ch else "."))
        listed = det.rows[:MAX_LISTED]
        parts.append(("Slabs counted:\n" if len(det.rows) > 1 else "Slab counted:\n") + "\n".join(
            f"•  {x[0]} — {clean_desc(x[1])[:80]}" + (f": {fmt_metric(keys[0], x[2], False)}" if len(det.rows) > 1 else "")
            for x in listed) + (f"\n•  … and {len(det.rows) - len(listed)} more (see the table)"
                                if len(det.rows) > len(listed) else ""))
    notes += _notes(r.slabs, [fy], subs, r.by_description)
    text = "\n\n".join([p for p in parts if p] + notes)
    lab = mart_layer.metric(keys[0])["label"]
    chart = build_chart("bar", "description", keys[0], lab[:1].upper() + lab[1:], det.columns, rows[:15]) if len(rows) > 1 else None
    kind = mart_layer.metric(keys[0])["kind"]
    total = ("Total", "", sum(x[2] or 0 for x in det.rows)) if kind != "ratio" and len(keys) == 1 else None
    return SlabAnswer("rank" if ranking else "lookup", text, det_sql, det_p, det.columns, rows, chart, total,
                      notes=notes, slabs=r.slabs)


async def _compare_groups(r: Resolution, key: str, fys: list[str], series: bool, table: str, scope_w: list[str],
                          scope_p: list, place: str, subs: list[str], db: ReadOnlyDB, timeout: float) -> SlabAnswer:
    """Two or more consumer segments side by side ("domestic vs commercial demand")."""
    lab = mart_layer.metric(key)["label"]
    notes: list[str] = []
    all_slabs = [s for _, g in r.groups for s in g]
    if series:
        cond = "fiscal_year IN (" + ", ".join("?" * len(fys)) + ")" if len(fys) >= 2 else "1 = 1"
        years: dict[str, dict] = {}
        sqls, params_all = [], []
        for name, g in r.groups:
            sw, sp = _slab_where(g)
            sql = (f"SELECT fiscal_year, {mart_layer.metric_sql(key, key)} FROM {table} WHERE "
                   f"{' AND '.join(scope_w + [sw, cond])} GROUP BY fiscal_year ORDER BY fiscal_year")
            p = scope_p + sp + (list(fys) if len(fys) >= 2 else [])
            res = await db.run(sql, max_rows=50, timeout_seconds=timeout, params=p)
            for fy, v in res.rows:
                years.setdefault(fy, {})[name] = v
            sqls.append(sql)
            params_all += p
        names = [n for n, _ in r.groups]
        cols = ["fiscal_year"] + [f"{key}__{i}" for i in range(len(names))]
        rows = [tuple([fy] + [years[fy].get(n) for n in names]) for fy in sorted(years)]
        if not rows:
            return SlabAnswer("none", f"**There are no figures for {r.label}{place}.**", answerable=False)
        last = rows[-1]
        short = [short_name(n) for n in names]
        head = (f"**{lab[:1].upper()}{lab[1:]}{place}, {rows[0][0]} to {last[0]}: in {last[0]} "
                + "; ".join(f"{n.lower()} {fmt_metric(key, v, False)}" for n, v in zip(short, last[1:])) + ".**")
        lines = "\n".join(f"•  {x[0]}: " + "; ".join(f"{n.lower()} {fmt_metric(key, v, False)}" for n, v in zip(short, x[1:]))
                          for x in rows)
        counted = "What was counted:\n" + "\n".join(_counted_line(n, g) for n, g in r.groups)
        notes += _notes(all_slabs, [x[0] for x in rows], subs)
        chart = build_chart("line", "fiscal_year", cols[1], short[0], cols, rows,
                            [(c, n) for c, n in zip(cols[2:], short[1:])])
        return SlabAnswer("compare", "\n\n".join([head, lines, counted] + notes), ";\n".join(sqls), params_all,
                          ["fiscal_year"] + names, rows, chart, notes=notes, slabs=all_slabs)
    fy = fys[0] if fys else mart_layer.latest_fy()
    if not fys:
        notes.append(default_period_note(fy))
    rows, sqls, params_all = [], [], []
    for name, g in r.groups:
        sw, sp = _slab_where(g)
        sql = (f"SELECT {mart_layer.metric_sql(key, key)} FROM {table} WHERE "
               f"{' AND '.join(scope_w + [sw, 'fiscal_year = ?'])}")
        p = scope_p + sp + [fy]
        res = await db.run(sql, max_rows=1, timeout_seconds=timeout, params=p)
        rows.append((name, res.rows[0][0] if res.rows else None))
        sqls.append(sql)
        params_all += p
    rows.sort(key=lambda x: -(x[1] or 0))
    head = (f"**{lab[:1].upper()}{lab[1:]}{place} in {fy}: "
            + "; ".join(f"{short_name(n).lower()} {fmt_metric(key, v, False)}" for n, v in rows) + ".**")
    extra = ""
    if len(rows) == 2 and rows[0][1] and rows[1][1]:
        a, b = rows
        if mart_layer.metric(key)["unit"] == "%":
            extra = f"{short_name(a[0])} is {a[1] - b[1]:.2f} points above {short_name(b[0]).lower()}."
        else:
            extra = f"{short_name(a[0])} is {a[1] / b[1]:.2f} times {short_name(b[0]).lower()}."
    counted = "What was counted:\n" + "\n".join(_counted_line(n, [s for s in g if s.first <= fy <= s.last]) for n, g in r.groups)
    notes += _notes(all_slabs, [fy], subs)
    rows_short = [(short_name(n), v) for n, v in rows]
    chart = build_chart("bar", "segment", key, lab[:1].upper() + lab[1:], ["segment", key], rows_short)
    text = "\n\n".join([head] + ([extra] if extra else []) + [counted] + notes)
    return SlabAnswer("compare", text, ";\n".join(sqls), params_all, ["segment", key], rows, chart, notes=notes,
                      slabs=all_slabs)
