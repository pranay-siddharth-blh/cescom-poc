"""
Tariff answers from the CESC tariff booklet FY2025-26 to FY2027-28 (DCB_Mart/metadata/tariff_booklet.json, transcribed
from 'CESCOM Tariff Categories.pdf'; KERC Tariff Order 2025 dated 27.03.2025). Deterministic, no LLM.

  rate        "What is the LT-3 tariff rate?", "energy charge rate for HT-2(b) in 2026-27", "LT-4(a) CDT"
  category    "Which tariff category is a homestay?", "Under which category does an EV charging station fall?"
  explain     "What is LT-4(a)?", "Explain HT-2(c)(i)", "Who comes under LT-6(b)?"
  list        "What are the tariff categories?", "sub-categories of LT-4"
  rule        "Interest on late payment", "reconnection charges", "electricity tax", "power factor surcharge", "ToD"
  check       "Does the billed rate match the tariff booklet?" (mart_tariff_rate_check)

Data questions ("LT-1 demand in 2024-25", "energy charges billed in Arkalgud") never come here: rate wording needs
'rate' / 'tariff for' / 'paise' / 'price', and anything asking for billed figures is left to the data routes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.config import MART_DIR
from app.db import ReadOnlyDB

BOOKLET_PATH = MART_DIR / "metadata" / "tariff_booklet.json"
SOURCE_LINE = "CESC tariff booklet FY2025-26 to FY2027-28 (KERC Tariff Order 2025 dated 27.03.2025)"


@dataclass
class TariffAnswer:
    answer: str
    key: str
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    sql: str = ""


@lru_cache(maxsize=1)
def booklet() -> dict:
    return json.loads(BOOKLET_PATH.read_text(encoding="utf-8"))


def categories() -> list[dict]:
    return booklet()["categories"]


def years() -> list[str]:
    return booklet()["source"]["fiscal_years"]


# ---------------------------------------------------------------------------------------------- codes
def norm_code(code: str) -> str:
    """'LT-4(a)' / 'lt 4 a' / 'LT4A' -> 'lt4a'; 'HT-2(c)(ii)' -> 'ht2cii'."""
    return re.sub(r"[^a-z0-9]", "", (code or "").lower())


_CODE_RE = re.compile(r"\b(lt|ht)\s*[-–]?\s*(\d)\s*(?:\(\s*([a-c])\s*\)|\s?([a-c])(?![a-z]))?"
                      r"\s*(?:\(\s*(i{1,3})\s*\)|\s(i{1,3})(?![a-z]))?", re.I)


def codes_in(question: str) -> list[str]:
    """Normalised codes named in the question, most specific form ('lt4a', 'ht2ci', 'lt4' for a whole family)."""
    out = []
    for m in _CODE_RE.finditer(question or ""):
        sub = (m.group(3) or m.group(4) or "").lower()
        roman = (m.group(5) or m.group(6) or "").lower() if sub else ""
        c = f"{m.group(1).lower()}{m.group(2)}{sub}{roman}"
        if c not in out:
            out.append(c)
    return out


def category(code: str) -> dict | None:
    n = norm_code(code)
    return next((c for c in categories() if norm_code(c["code"]) == n), None)


def family_members(code: str) -> list[dict]:
    """'lt4' -> LT-4(a), LT-4(b), LT-4(c); 'ht2' -> HT-2(a) ... HT-2(c)(ii); 'lt1' -> [LT-1]."""
    n = norm_code(code)
    exact = category(n)
    if exact:
        return [exact]
    return [c for c in categories() if norm_code(c["code"]).startswith(n)]


# ---------------------------------------------------------------------------------------------- formatting
def _rs(v) -> str:
    return f"₹{v:,.0f}" if float(v).is_integer() else f"₹{v:,.2f}"


def rate_lines(cat: dict, fy: str) -> list[tuple[str, str]]:
    """(component, value) pairs for one year, in the booklet's own words."""
    r = cat["rates"].get(fy) or {}
    out = []
    if "fixed" in r:
        out.append(("Fixed charge", f"{_rs(r['fixed'])} {r['fixed_unit']}"))
    if "demand" in r:
        out.append(("Demand charge", f"{_rs(r['demand'])} {r['demand_unit']}"))
    if "ht_demand" in r:
        out.append(("Demand charge (HT)", f"{_rs(r['ht_demand'])} {r['ht_demand_unit']}"))
    if "energy_paise" in r:
        out.append(("Energy charge", f"{r['energy_paise']:g} paise per unit (kWh)"))
    if "energy_led_paise" in r:
        out.append(("Energy charge, LED / induction lighting", f"{r['energy_led_paise']:g} paise per unit"))
    if "cdt_paise" in r:
        out.append(("Commission Determined Tariff (CDT)", f"{r['cdt_paise']:g} paise per unit"))
        out.append(("  of which fixed-charge component", f"{r['cdt_fixed_component_paise']:g} paise per unit"))
        out.append(("  of which variable-charge component", f"{r['cdt_variable_component_paise']:g} paise per unit"))
    db = r.get("demand_based")
    if db:
        out.append((f"Demand-based option ({db['applies']}): fixed", f"{_rs(db['fixed'])} {db['fixed_unit']}"))
        out.append(("Demand-based option: energy", f"{db['energy_paise']:g} paise per unit"))
    for v in cat.get("variants", []):
        vr = v["rates"].get(fy)
        if vr:
            out.append((f"{v['name']}", f"{_rs(vr['demand'])} per kVA per month; {vr['energy_paise']:g} paise per unit"))
    return out


def rate_table(cat: dict, fys: list[str]) -> tuple[list[str], list[tuple]]:
    comps: list[str] = []
    by: dict[str, dict[str, str]] = {}
    for fy in fys:
        for comp, val in rate_lines(cat, fy):
            if comp not in comps:
                comps.append(comp)
            by.setdefault(comp, {})[fy] = val
    cols = ["Component"] + [f"FY {fy}" for fy in fys]
    rows = [tuple([comp] + [by.get(comp, {}).get(fy, "—") for fy in fys]) for comp in comps]
    return cols, rows


def _md_table(cols: list[str], rows: list[tuple]) -> str:
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols)
    return head + "\n" + "\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in rows)


def category_card(cat: dict, fys: list[str] | None = None, with_notes: bool = True) -> str:
    fys = fys or years()
    parts = [f"**{cat['code']} — {cat['name']}** (booklet page {cat['page']})", cat["applies_to"]]
    cols, rows = rate_table(cat, fys)
    if rows:
        parts.append(_md_table(cols, rows))
    if with_notes and cat.get("notes"):
        parts.append("\n".join(f"- {n}" for n in cat["notes"]))
    parts.append(f"Source: {SOURCE_LINE}. {booklet()['source']['caveat']}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------------------------- matching
_FY_RE = re.compile(r"\b(20\d\d)\s*[-–/]\s*(\d\d)\b|\bfy\s*(\d\d)\b|\bfy\s*20(\d\d)\b", re.I)
_RATE_RE = re.compile(r"\b(rates?|paise|price|pricing|cdt|commission determined|tariff\s+(for|of|rate|rates|applicable|"
                      r"schedule|booklet|order|card)|what\s+is\s+the\s+tariff|current\s+tariff|new\s+tariff|"
                      r"how\s+much\s+(is|does)\s+(it\s+)?(cost|charged?)|charged?\s+per\s+(unit|kwh|kw|hp|kva)|"
                      r"per\s+(kw|hp|kva)\s+per\s+month|fixed\s+charges?\s+(rate|per)|energy\s+charges?\s+(rate|per\s+kwh)|"
                      r"demand\s+charges?\s+(rate|per)|(what\s+is|what\s+are|what's)\s+the\s+([lh]t\s*-?\s*\d\w*(\s*\(\w+\))*\s+)?"
                      r"(demand|fixed|energy)\s+charges?(\s+(for|of)\s+[lh]t\s*-?\s*\d\w*(\s*\(\w+\))*)?\s*\??\s*$)", re.I)
# rate words that actually ask for billed data
_DATA_RATE_RE = re.compile(r"\b(collection|recovery|realisation|realization|growth|billed|effective|average|avg|actual)\s+"
                           r"(energy\s+)?rates?\b|\bbilled\b|\bcollected\b", re.I)
_MEMBERSHIP_RE = re.compile(r"\b(which|what)\s+(tariff\s+)?(category|categories|tariff|schedule|slab)\b|"
                            r"\bunder\s+which\b|\b(falls?|comes?)\s+under\b|\bcategor(y|ies|ised|ized)\s+(for|of)\b|"
                            r"\btariff\s+(applies|applicable)\s+(to|for)\b|\bapplicable\s+tariff\b|\bbilled\s+under\b", re.I)
_EXPLAIN_RE = re.compile(r"\b(what\s+is|what's|whats|what\s+are|explain|describe|meaning|define|who\s+(is|are|comes?)|"
                         r"covers?|covered|includes?|eligib\w*|applicable\s+to|applies\s+to|details?|about|tell\s+me)\b", re.I)
_LIST_RE = re.compile(r"\b(list|all|what\s+are\s+the|show)\b.*\b(tariff\s+categor\w*|categories|sub-?\s?categor\w*|"
                      r"tariff\s+schedules?)\b|\bsub-?\s?categor\w*\b|\btariff\s+categories\b", re.I)
_CHECK_RE = re.compile(r"\b(match|matches|agree|compare[ds]?|comparison|versus|vs\.?|against|check|verify|differ\w*)\b.*"
                       r"\b(booklet|tariff|approved|kerc|rates?)\b|\b(booklet|tariff)\b.*\b(match|agree|vs\.?|versus|"
                       r"against|compare)\b", re.I)
# data intent: these belong to the data routes even when a category is named
_DATA_RE = re.compile(r"\b(demand|collection|collected|arrears|balance|dues|installations?|"
                      r"consumption|units\s+(sold|billed)|efficiency|trend|growth|highest|lowest|top|bottom|rank\w*|"
                      r"most|least|compare|share|how\s+many|total|sum|subsidy|adjustment|at&c|loss)\b", re.I)


def _fys_asked(question: str) -> list[str]:
    out = []
    for m in _FY_RE.finditer(question or ""):
        if m.group(1):
            fy = f"{m.group(1)}-{m.group(2)}"
        else:
            yy = int(m.group(3) or m.group(4))
            fy = f"20{yy - 1:02d}-{yy:02d}"
        if fy not in out:
            out.append(fy)
    return out


def _norm_text(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9&]+", " ", (s or "").lower()).strip() + " "


def match_keyword(question: str) -> tuple[dict | None, str]:
    """The category whose keyword matches the longest part of the question (an 'ht' in the question prefers HT)."""
    q = _norm_text(question)
    want_ht = bool(re.search(r"\bht\b|high\s+tension|11\s*kv|33\s*kv|66\s*kv", question or "", re.I))
    best, best_len, best_kw = None, 0, ""
    for c in categories():
        for kw in c.get("keywords", []):
            n = _norm_text(kw)
            if n in q:
                score = len(n) + (3 if want_ht == c["voltage"].startswith("HT") else 0)
                if score > best_len:
                    best, best_len, best_kw = c, score, kw
    return best, best_kw


def match_rule(question: str) -> dict | None:
    q = _norm_text(question)
    best, best_len = None, 0
    for r in booklet()["rules"]:
        for a in [r["title"]] + r.get("aliases", []):
            n = _norm_text(a)
            if n.strip() and n in q and len(n) > best_len:
                best, best_len = r, len(n)
    return best


def rule_card(rule: dict) -> str:
    return f"**{rule['title']}**\n\n{rule['text']}\n\nSource: {SOURCE_LINE}, page {rule['page']}."


def list_card(prefix: str | None = None) -> tuple[str, list[str], list[tuple]]:
    cats = [c for c in categories() if not prefix or norm_code(c["code"]).startswith(prefix)]
    cols = ["Category", "Name", "Who it is for", "Energy FY 2025-26", "Fixed / demand FY 2025-26"]
    rows = []
    for c in cats:
        r = c["rates"].get(years()[0], {})
        energy = (f"{r['cdt_paise']:g} paise (CDT)" if "cdt_paise" in r else f"{r.get('energy_paise', 0):g} paise")
        fixed = (f"{_rs(r['fixed'])} {r['fixed_unit']}" if "fixed" in r else
                 f"{_rs(r['demand'])} {r['demand_unit']}" if "demand" in r else "— (in the CDT)")
        rows.append((c["code"], c["name"], c["short"], energy, fixed))
    title = (f"**{len(cats)} sub-categories of {cats[0]['family']} in the CESC tariff booklet**" if prefix and cats else
             f"**The CESC tariff booklet has {len(cats)} categories: {sum(1 for c in cats if c['voltage'].startswith('LT'))} "
             f"LT (Part I) and {sum(1 for c in cats if c['voltage'] == 'HT')} HT (Part II).**")
    body = "\n\n".join([title, _md_table(cols, rows),
                        booklet()["parts"]["LT"], booklet()["parts"]["HT"], f"Source: {SOURCE_LINE}."])
    return body, cols, rows


# ---------------------------------------------------------------------------------------------- glossary
_FAMILY_TEXT = {
    "LT-1": "Domestic: all residential houses incl. Bhagyajyothi / Kutirajyothi, Government hospitals and schools, "
            "religious and charitable institutions. Single slab. (In the DCB sheets LT-1 held only BJ/KJ up to 2023-24; "
            "urban domestic lighting & heating was printed as LT-2(a) and is printed under LT-1 from 2024-25.)",
    "LT-2": "Private educational institutions, nursing homes and private hospitals. (Up to 2023-24 the DCB sheets' LT-2 "
            "also held domestic lighting & heating, LT-2(a), now LT-1.)",
    "LT-3": "Commercial: LT-3(a) shops, hotels, offices, banks, cinemas, clinics and other commercial premises; LT-3(b) "
            "hoardings and advertisement boards (printed as LT-7(b) up to 2023-24).",
    "LT-4": "Irrigation pump sets: LT-4(a) up to 10 HP (Commission Determined Tariff, paid by the Government as subsidy), "
            "LT-4(b) above 10 HP, LT-4(c) nurseries, gardens, plantations, saffron and mushroom.",
    "LT-5": "Industrial (LT): heating and motive power of industries including MSMEs, workshops, mills, IT units, data "
            "centres (sanctioned load below 150 kW).",
    "LT-6": "LT-6(a) water supply and sewerage pumping of Government / local bodies, LT-6(b) public street and traffic "
            "lighting, LT-6(c) EV charging and battery swapping stations.",
    "LT-7": "Temporary supply below 67 HP (prepaid meter), touring cinemas, outdoor shooting.",
    "HT-1": "HT water supply, drainage / sewerage treatment and pumping of KUWS&DB, local bodies and Government.",
    "HT-2": "HT-2(a) industrial (incl. railway traction, data centres), HT-2(b) commercial, HT-2(c)(i) Government / "
            "charitable / ESI hospitals, universities and Government / aided educational institutions, HT-2(c)(ii) other "
            "hospitals and educational institutions.",
    "HT-3": "Private lift irrigation schemes (Government schemes are HT-7; up to 2023-24 the DCB sheets printed Government "
            "lift irrigation as HT-3(a)(i)).",
    "HT-4": "Residential apartments and colonies on HT, religious institutions, jails.",
    "HT-5": "Temporary supply of 67 HP and above, hoardings, construction power.",
    "HT-6": "Irrigation and agricultural farms, horticultural farms and nurseries, coffee / tea / rubber / coconut / "
            "arecanut plantations, mushroom raising (printed as HT-3b up to 2023-24).",
    "HT-7": "Government lift irrigation schemes / societies (printed as HT-3(a)(i) up to 2023-24).",
}
_LEGACY = {
    "LT-2(a)": "Domestic lighting & heating as printed in the DCB sheets up to 2023-24 (BMA / municipal corporations, urban "
               "local bodies, free lighting) -- today LT-1 Domestic in the CESC tariff booklet.",
    "LT-2(b)": "Private institutions as printed up to 2023-24 -- today LT-2.",
    "LT-7(b)": "Hoardings and advertisement boards as printed up to 2023-24 -- today LT-3(b).",
    "HT-3(a)(i)": "Government lift irrigation as printed up to 2023-24 -- today HT-7.",
    "HT-3(a)(ii) / HT-3(a)(iii)": "Private lift irrigation as printed up to 2023-24 -- today HT-3.",
    "HT-3b": "Irrigation & agricultural farms as printed up to 2023-24 -- today HT-6.",
    "LT-5(a) / LT-5(b)": "How the DCB sheets print industrial lines ('Heating & Motive Power - other than BMA', by HP band). "
                         "The CESC tariff booklet has only LT-5, with no sub-categories: all of them are LT-5 Industrial. "
                         "LT-5(a) has no installations in this division.",
    "(FL) Free Lighting": "Lines the DCB sheets print under LT-2(a) up to 2023-24 and LT-1 from 2024-25: LT-1 Domestic in the "
                          "booklet. A handful of installations billed at the domestic rate and paid 85-95% by the Government subsidy.",
    "(OL) Office Lighting": "Lines the DCB sheets print under LT-3: LT-3(a) Commercial in the booklet, which covers 'all "
                            "offices'. 13-15 installations at the LT-3 rate, paid 99-100% by adjustment.",
    "(i) / (ii)": "DCB lines split by area within one booklet category: (i) urban local bodies and city corporations, (ii) "
                  "village panchayats (the areas the booklet's 20 paise per unit village-panchayat rebate applies to).",
}


def _rate_summary(cat: dict) -> str:
    fy = years()[0]
    bits = [f"{c}: {v}" for c, v in rate_lines(cat, fy) if not c.startswith("  ")]
    return f"FY{fy}: " + "; ".join(bits) if bits else ""


def code_glossary(old: dict | None = None) -> dict[str, str]:
    """The tariff-code glossary from the booklet: each family, each booklet sub-category with its FY2025-26 rates, and the
    old codes the DCB sheets printed before the 2024-25 restructuring. Non-tariff keys of the old glossary (misc codes,
    IPP / auxiliary) are kept."""
    out: dict[str, str] = {}
    for fam, text in _FAMILY_TEXT.items():
        out[fam] = text + " (CESC tariff booklet FY2025-26 to FY2027-28.)"
        for c in categories():
            if c["family"] == fam and c["code"] != fam:
                out[c["code"]] = f"{c['name']}: {c['short']}. {_rate_summary(c)} (booklet page {c['page']})."
        own = category(fam)
        if own:
            out[fam] += f" {_rate_summary(own)} (booklet page {own['page']})."
    out.update(_LEGACY)
    for k, v in (old or {}).items():
        if not re.match(r"^(LT|HT)-\d$", k):
            out.setdefault(k, v)
    return out


GLOSSARY_TERMS = {
    "Tariff booklet": f"{SOURCE_LINE}: 22 categories (12 LT, 10 HT), their rates for FY2025-26, FY2026-27 and FY2027-28, "
                      "and the general terms. Every tariff category, rate and rule in this app follows it.",
    "Single slab": "LT energy charges are billed at one rate per unit for all consumption (no usage slabs); the DCB sheets "
                   "still print their own lines ('slabs') by area, load or metering.",
    "CDT": "Commission Determined Tariff: the per-unit tariff KERC fixes for LT-4(a) irrigation pump sets up to 10 HP "
           "(830 paise in FY2025-26), paid by the Government of Karnataka as subsidy.",
    "ToD": "Time-of-Day tariff: December-June +100 paise/unit 06-09 h and 18-22 h, -100 paise/unit 22-06 h; compulsory for "
           "HT-2 with contract demand of 500 kVA and above.",
    "Adjustment (collection)": "Collection booked by adjustment rather than cash / cheque: the Government subsidy "
                               "(confirmed by CESC), almost all of it Gruha Jyothi for LT-1 and the IP-set subsidy "
                               "for LT-4(a).",
    "Collection efficiency without adjustment": "Cash / cheque collection / demand x 100 (the sheet's header formula "
                                                "(34 - Adj.) / 30).",
    "Billing efficiency": "Installations actually billed / live installations x 100 (the sheet's 'Billing Efficiency (4/3)').",
    "AT&C loss": "100 - billing efficiency x collection efficiency, with the sheet's billing efficiency; the data has no "
                 "energy-input figures, so energy (technical) loss is not included.",
    "Electricity tax": "9% ad valorem on energy charges, for all consumers except IP sets up to 10 HP.",
}


def page_data() -> dict:
    """Everything the About page's tariff section shows."""
    cats = []
    for c in categories():
        cols, rows = rate_table(c, years())
        cats.append({"code": c["code"], "name": c["name"], "short": c["short"], "voltage": c["voltage"], "page": c["page"],
                     "applies_to": c["applies_to"], "notes": c.get("notes", []), "rate_columns": cols,
                     "rate_rows": [list(r) for r in rows]})
    return {"source": booklet()["source"], "parts": booklet()["parts"], "categories": cats,
            "rules": [{"title": r["title"], "text": r["text"], "page": r["page"]} for r in booklet()["rules"]]}


# ---------------------------------------------------------------------------------------------- entry point
async def answer(question: str, db: ReadOnlyDB | None = None, timeout: float = 10.0) -> TariffAnswer | None:
    q = (question or "").strip()
    if not q:
        return None
    codes = codes_in(q)
    data_like = bool(_DATA_RE.search(q))
    fys = _fys_asked(q)

    # data vs booklet rate check
    if _CHECK_RE.search(q) and re.search(r"\b(billed|actual|data|dcb)\b", q, re.I) and db is not None:
        return await _rate_check(codes, fys, db, timeout)

    rate_q = bool(_RATE_RE.search(q)) and not _DATA_RATE_RE.search(q)
    rate_cats = [m for c in codes for m in family_members(c)] if rate_q else []
    kw = ""
    if rate_q and not rate_cats:
        hit, kw = match_keyword(q)
        rate_cats = [hit] if hit else ([category("LT-1")] if re.search(r"\b(domestic|household|home)\b", q, re.I) else [])
    # 1. a rule ("interest on late payment", "reconnection charges", "electricity tax") -- never a billed-figure
    #    question; a rate question that names a category ("slab rates for domestic") gets that category's rates instead
    rule = match_rule(q)
    if (rule and not rate_cats and not fys and not data_like
            and not re.search(r"\b(collected|billed|how much was|total|trend|in\s+\w+\s+sub-?division)\b", q, re.I)):
        return TariffAnswer(rule_card(rule), f"rule:{rule['key']}")

    # 2. a list of categories / sub-categories
    if _LIST_RE.search(q) and not data_like:
        prefix = next((c for c in codes if not category(c)), None) or (codes[0] if codes and len(family_members(codes[0])) > 1 else None)
        text, cols, rows = list_card(prefix)
        return TariffAnswer(text, "list" + (f":{prefix}" if prefix else ""), cols, rows)

    # 3. rates for a named category (or a described consumer)
    if rate_q:
        cats = rate_cats
        if not cats:
            text, cols, rows = list_card()
            return TariffAnswer("Which category's rates? Here are all of them for FY2025-26:\n\n" + text, "rate:list", cols, rows)
        wanted = [fy for fy in fys if fy in years()]
        older = [fy for fy in fys if fy not in years()]
        parts = []
        if kw:
            parts.append(f"A **{kw}** is billed under **{cats[0]['code']} ({cats[0]['name']})**.")
        if older:
            parts.append(f"The tariff booklet covers FY2025-26 to FY2027-28 only, so it has no rates for {', '.join(older)}."
                         + (" The billed energy rate in the DCB data for those years can be asked as \"energy charge per "
                            "unit for <category> in <year>\"." if not wanted else ""))
        cols, rows = (["Component"], [])
        for c in cats[:6]:
            parts.append(category_card(c, wanted or None, with_notes=len(cats) == 1))
            if len(cats) == 1:
                cols, rows = rate_table(c, wanted or years())
        return TariffAnswer("\n\n".join(parts), "rate:" + ",".join(c["code"] for c in cats[:6]), cols, rows)

    # 4. which category is X for
    if _MEMBERSHIP_RE.search(q) and not (data_like and not codes):
        hit, kw = match_keyword(q)
        if hit:
            text = (f"**A {kw} is billed under {hit['code']} — {hit['name']}.**\n\n" + category_card(hit))
            cols, rows = rate_table(hit, years())
            return TariffAnswer(text, f"category:{hit['code']}", cols, rows)
        if not codes and not data_like:
            rule = next(r for r in booklet()["rules"] if r["key"] == "unclassified")
            text, cols, rows = list_card()
            return TariffAnswer("**The tariff booklet does not name that use explicitly.** " + rule["text"] +
                                "\n\nThese are the categories it defines:\n\n" + text, "category:unmatched", cols, rows)

    # 5. explain a named category ("what is LT-4(a)", "who comes under HT-2(c)(i)")
    if codes and _EXPLAIN_RE.search(q) and not data_like and not fys:
        cats = [m for c in codes for m in family_members(c)]
        bare = re.fullmatch(r"\s*(what\s+is|what's|whats|explain|define|meaning\s+of|what\s+does)\s+(the\s+)?"
                            r"(lt|ht)\s*-?\s*\d\s*\(?\s*[a-c]?\s*\)?\s*(\(?\s*i{1,3}\s*\)?)?\s*(mean|stand\s+for)?\s*\??\s*", q, re.I)
        if not cats and bare:
            # a bare "what is LT-5(b)" -- a code the booklet does not have (the DCB sheets' own line naming): answer with
            # its category. Anything more ("LT-5(b) ... 5 to 40 HP slab in Saligrama") stays with the slab route.
            base = re.match(r"(lt|ht)\d", codes[0])
            fam = family_members(base[0]) if base else []
            line = next((d for d in booklet().get("data_lines", {}).get("lines", [])
                         if norm_code(d["line"]) == codes[0]), None)
            if len(fam) == 1:
                cols, rows = rate_table(fam[0], years())
                pretty = line["line"] if line else codes[0].upper()
                head = (f"**The CESC tariff booklet has no {pretty}: it has only {fam[0]['code']}, with no sub-categories.** "
                        + (line["meaning"] if line else f"{pretty} is how the DCB sheets print some {fam[0]['code']} lines."))
                return TariffAnswer(head + "\n\n" + category_card(fam[0]), f"explain:{fam[0]['code']}", cols, rows)
        if len(cats) == 1:
            cols, rows = rate_table(cats[0], years())
            return TariffAnswer(category_card(cats[0]), f"explain:{cats[0]['code']}", cols, rows)
        if cats:
            text, cols, rows = list_card(norm_code(codes[0]))
            return TariffAnswer(text, f"explain:{codes[0]}", cols, rows)
    return None


async def _rate_check(codes: list[str], fys: list[str], db: ReadOnlyDB, timeout: float) -> TariffAnswer:
    where, params = [], []
    if fys:
        where.append(f"fiscal_year IN ({', '.join('?' for _ in fys)})")
        params += fys
    cats = [m["code"] for c in codes for m in family_members(c)]
    if cats:
        where.append(f"booklet_code IN ({', '.join('?' for _ in cats)})")
        params += cats
    sql = ("SELECT fiscal_year, booklet_code, booklet_name, data_energy_paise, booklet_energy_paise, difference_paise, "
           "difference_pct FROM mart_tariff_rate_check WHERE data_energy_paise IS NOT NULL"
           + "".join(f" AND {w}" for w in where) + " ORDER BY fiscal_year, booklet_code")
    res = await db.run(sql, max_rows=200, timeout_seconds=timeout, params=params)
    rows = res.rows
    if not rows:
        return TariffAnswer("**The data and the tariff booklet overlap only in FY2025-26; there is no billed rate to "
                            "compare for that selection.**", "check:none", [], [], sql)
    close = [r for r in rows if abs(r[6] or 0) <= 1.0]
    far = sorted((r for r in rows if abs(r[6] or 0) > 1.0), key=lambda r: -abs(r[6] or 0))
    lines = [f"**The energy rate actually billed (energy charges / units) matches the booklet within 1% for {len(close)} of "
             f"{len(rows)} categories** in FY{rows[0][0]}."]
    if far:
        lines.append("Larger differences (ToD peak surcharges / off-peak rebates, power-factor surcharges, village-panchayat "
                     "rebates and part-year billing explain them): " +
                     "; ".join(f"{r[1]} {r[3]:.0f} vs {r[4]:g} paise ({r[6]:+.1f}%)" for r in far) + ".")
    cols = ["fiscal_year", "booklet_code", "booklet_name", "data_energy_paise", "booklet_energy_paise", "difference_paise",
            "difference_pct"]
    return TariffAnswer("\n\n".join(lines + [f"Booklet: {SOURCE_LINE}."]), "check", cols, rows, sql)
