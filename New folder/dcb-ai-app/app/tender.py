"""
Tender generation and analysis (no LLM, every rule cited):

  plan(value, kind, method)   how a purchase of this value goes: the GeM procurement mode (GFR 2017 Rule 149 as amended
                              by OM F.1/3/2024-PPD dated 10.07.2024), and who in CESC may invite and accept the tender
                              (CESC Manual of Delegation of Powers, First Edition 2023, Section B 'Delegation of powers in
                              respect of purchases', and Section A for procurement / award above the MD's power).
  draft(form)                 a bid document in the GeM bid-document format (Bid Details, EMD / ePBG, MII / MSE, the
                              standard eligibility clauses, technical specifications, consignees, buyer-added terms,
                              GeM's disclaimer list), plus CESC's internal approval note -- as HTML for the screen and
                              as a Word .docx, with a compliance check of the buyer-added terms against GeM's list of
                              clauses that make a bid null and void.
  analyse(text)               Bid Assist-style reading of a tender document (a GeM bid PDF or any tender text): the key
                              fields (bid number, dates, estimated value, EMD, ePBG, turnover, experience, MSE / startup
                              exemption, RA, evaluation method, documents required), a short summary, the same
                              compliance check, and -- given a bidder profile -- an eligibility verdict per criterion.

Sources (read in full for this module): gem.gov.in and a published GeM bid document (GEM/2025/B/5970949, 36 pages) for
the format and its standard clauses; nexizo.ai / BidAssist's published feature descriptions for the analyser (their site
needs a login); the CESC Delegation of Powers manual (2023) and the revenue-section circular (12.10.2023).
"""
from __future__ import annotations

import datetime as dt
import html
import io
import re
import zipfile
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------------------------- rules
LAKH, CRORE = 1e5, 1e7

GEM_MODES = [  # GFR 2017 Rule 149, revised by Ministry of Finance OM F.1/3/2024-PPD dated 10.07.2024
    (50_000, "Direct purchase", "Up to Rs 50,000: direct purchase from any available GeM seller meeting the required "
                                "quality, specification and delivery period."),
    (10 * LAKH, "L1 purchase", "Above Rs 50,000 and up to Rs 10 lakh: from the GeM seller with the lowest price among "
                               "sellers of at least three different manufacturers meeting the requirement (L1)."),
    (float("inf"), "Bid / reverse auction", "Above Rs 10 lakh: online bidding or reverse auction on GeM (e-bid; Bid to "
                                            "RA can be enabled)."),
]

# CESC Manual of Delegation of Powers (2023), Section B 'Delegation of Authority in Respect of Purchase' (MD / CEE /
# SEE / EEE / AEEE); None = no power; inf = full powers.
OFFICERS = ["MD", "CEE", "SEE", "EEE", "AEEE"]
OFFICER_NAMES = {"MD": "Managing Director", "CEE": "Chief Engineer (Electricity)", "SEE": "Superintending Engineer (Electricity)",
                 "EEE": "Executive Engineer (Electricity)", "AEEE": "Assistant Executive Engineer (Electricity)",
                 "CPC": "Central Purchase Committee", "Board": "Board of Directors"}
INF = float("inf")
DOP = {
    "invite": {"MD": INF, "CEE": 3 * LAKH, "SEE": 1 * LAKH, "EEE": 25_000, "AEEE": None},
    "accept_open": {"MD": 3 * CRORE, "CEE": 10 * LAKH, "SEE": 5 * LAKH, "EEE": 1 * LAKH, "AEEE": None},
    "accept_limited": {"MD": 5 * LAKH, "CEE": 1 * LAKH, "SEE": 50_000, "EEE": 25_000, "AEEE": None},
    "proprietary": {"MD": 15 * LAKH, "CEE": 2.5 * LAKH, "SEE": 1 * LAKH, "EEE": 50_000, "AEEE": None},
    "urgent_local": {"MD": 5 * LAKH, "CEE": 20_000, "SEE": 5_000, "EEE": 2_000, "AEEE": 500},
}
DOP_ABOVE_MD = [(10 * CRORE, "CPC", "more than Rs 3 crore and up to Rs 10 crore"), (INF, "Board", "more than Rs 10 crore")]
LIMITED_TENDER_CAP = 5 * LAKH       # 'Limited Tenders can not be called for purchases more than Rs 5.00 lakh as per KTPP Act.'
MIN_PO_VALUE = 1_000                # 'No purchase order need be placed where the total value of the purchase is less than Rs 1,000'

DOP_NOTES = [
    "GM (Purchase), Corporate Office, is the authority for all purchase orders placed in the Corporate Office; GM (Tech) "
    "may approve purchases not exceeding Rs 20,000 at a time needed for the smooth functioning of the Corporate Office.",
    "In case of a lone bidder, the guidelines in Appendix-3 (accepting a single tender) are followed.",
    "Limited tenders cannot be called for purchases of more than Rs 5.00 lakh (KTPP Act).",
    "Extension orders: within 6 months, not exceeding once, up to 25% of the original ordered quantity, if prices have "
    "not fallen and the total is within the officer's purchase powers (para 99, Accounts Manual Vol-II part-A).",
    "Price variation in raw materials: MD and CEE have full powers subject to the IEEMA formulae or the approved price "
    "variation clause in the purchase order; statutory variations (duties / taxes / freight) within the original "
    "delivery schedule are within MD / CEE / SEE / EEE powers.",
    "Condonation of delay / levy of penalty follows B.O. No. KEB/B5/3410/77-78 dated 19.05.1981 (Annexure-1) and the "
    "penalty table of Annexure-2.",
]
SINGLE_TENDER_GUIDELINES = [
    "On the date of closure of sale of tender, if only one tender is sold, the date of sale and opening should be "
    "extended and intimated to other firms who can participate, indicating that only one tender form is sold.",
    "If more than one firm purchased the tender forms and only one tender is received on the opening date, the dates "
    "are extended again, informing the other firms who took the forms and other similar firms (also on the Internet) "
    "about the receipt of a single tender.",
    "If, in spite of extending the date twice, only one tender is received, it is opened duly intimating the Corporate "
    "Office. This applies to both procurement and turnkey tenders and is followed strictly.",
]
# Annexure-2: penalty as a % of the maximum liveable penalty, by delay. Columns: <3 m, 3-6 m, 6-9 m, 9 m-1 y, 1-2 y, >2 y
PENALTY_FULL_SUPPLY = [
    ("Full supply made but more than 75% delivered after the delay", ["Condonation / levy decided by SEEs / CEEs", "35%", "40%", "45%", "50%", "Referred to the Company"]),
    ("Full supply made but 50% to 75% delivered after the delay", ["Condonation / levy decided by SEEs / CEEs", "30%", "35%", "40%", "45%", "—"]),
    ("Full supply made but 25% to 50% delivered after the delay", ["Condonation / levy decided by SEEs / CEEs", "25%", "30%", "35%", "40%", "—"]),
    ("Full supply made but 0% to 25% delivered after the delay", ["Condonation / levy decided by SEEs / CEEs", "20%", "25%", "30%", "35%", "—"]),
]
PENALTY_PART = [
    ("Up to 50% delivered with delay and the rest not supplied", ["50%", "55%", "60%", "Referred to the Board"]),
    ("More than 50% delivered with delay and the rest not supplied", ["40%", "45%", "50%", "—"]),
]
PENALTY_PART_IN_TIME = [("0 to 25% delivered in time, rest not supplied", "50% of the maximum liveable penalty"),
                        ("25% to 50% delivered in time, rest not supplied", "40% of the maximum liveable penalty"),
                        ("50% to 75% delivered in time, rest not supplied", "25% of the maximum liveable penalty"),
                        ("75% to 99% delivered in time, rest not supplied", "Token penalty")]

# GeM's own disclaimer (printed on every GeM bid): buyer-added clauses that make the bid null and void
GEM_PROHIBITED = [
    "Definition of Class I and Class II suppliers in the bid not in line with the extant Order / Office Memorandum issued by DPIIT.",
    "Seeking EMD submission from bidder(s), including via Additional Terms & Conditions, in contravention to exemption provided to such sellers under GeM GTC.",
    "Publishing Custom / BOQ bids for items for which regular GeM categories are available without any Category item bunched with it.",
    "Creating BoQ bid for single item.",
    "Mentioning specific Brand or Make or Model or Manufacturer or Dealer name.",
    "Mandating submission of documents in physical form as a pre-requisite to qualify bidders.",
    "Floating / creation of work contracts as Custom Bids in Services.",
    "Seeking sample with bid or approval of samples during bid evaluation process (trials allowed for attached categories as per the buyer nodal Ministry's procurement policy).",
    "Mandating foreign / international certifications even in case of existence of Indian Standards without specifying equivalent Indian Certification / standards.",
    "Seeking experience from specific organization / department / institute only or from foreign / export experience.",
    "Creating bid for items from irrelevant categories.",
    "Incorporating any clause against the MSME policy and Preference to Make in India Policy.",
    "Reference of conditions published on any external site or reference to external documents / clauses.",
    "Asking for any Tender fee / Bid Participation fee / Auction fee in case of Bids / Forward Auction.",
    "Any ATC clause in contravention with GeM GTC Clause 4 (xiii)(h).",
]
# what can be checked in text, and how (index into GEM_PROHIBITED)
_COMPLIANCE_RES = [
    (4, re.compile(r"\b(make|brand|model\s*(no|number)?|manufacturer|dealer)\s*[:\-]\s*\S|\b(siemens|abb|schneider|"
                   r"havells|crompton|l\s*&\s*t|larsen|bajaj|philips|tata|kirloskar|polycab|finolex|hp|dell|lenovo|"
                   r"samsung|lg|cisco|microsoft|oracle)\b(?!\s*or\s*equivalent)", re.I), "names a brand / make / model"),
    (5, re.compile(r"\b(hard\s*copy|physical\s*(copy|form|submission)|submit\w*\s+in\s+person|by\s+post|sealed\s+cover|"
                   r"original\s+documents?\s+(must|shall|should)\s+be\s+submitted)\b", re.I), "asks for documents in physical form"),
    (7, re.compile(r"\bsamples?\b.{0,40}\b(submit|with\s+the\s+bid|along\s+with|approval|required|furnish)", re.I | re.S),
     "asks for samples with the bid / sample approval"),
    (8, re.compile(r"\b(ce\s+(mark|certif\w*)|ul\s+(listed|certif\w*)|fcc|rohs|iec\s+certif\w*|ansi|astm|din\s+\d|"
                   r"european\s+standard|international\s+certif\w*)\b", re.I), "asks for a foreign / international certification"),
    (9, re.compile(r"\bexperience\b.{0,60}\b(only|exclusively)\b.{0,40}\b(cesc|kptcl|bescom|psu|department|organi[sz]ation|"
                   r"railways?|central\s+govt)|\b(foreign|export|international|overseas)\s+experience\b", re.I | re.S),
     "asks for experience from a specific organisation only / foreign experience"),
    (11, re.compile(r"\b(mse|msme|micro\s+and\s+small)\w*\b.{0,40}\b(not\s+eligible|excluded|not\s+allowed|no\s+exemption|"
                    r"not\s+exempted)|\b(make\s+in\s+india|mii)\b.{0,40}\b(not\s+applicable|not\s+considered|excluded)", re.I | re.S),
     "goes against the MSE / Make in India policy"),
    (12, re.compile(r"\b(www\.|https?://|as\s+per\s+(the\s+)?(website|portal|link)|available\s+(on|at)\s+(the\s+)?(website|site|portal))",
                    re.I), "refers to conditions on an external site / document"),
    (13, re.compile(r"\b(tender|bid|participation|auction|document)\s+(fee|cost)\b", re.I), "asks for a tender / bid participation fee"),
    (1, re.compile(r"\b(emd|earnest\s+money)\b.{0,60}\b(no\s+exemption|not\s+exempt\w*|mandatory\s+for\s+all|all\s+bidders\s+must)",
                   re.I | re.S), "seeks EMD against the GeM GTC exemption"),
]


# ---------------------------------------------------------------------------------------------- plan
def _fmt_rs(v: float) -> str:
    if v == INF:
        return "full powers"
    if v >= CRORE:
        return f"Rs {v / CRORE:,.2f} crore".replace(".00 crore", " crore")
    if v >= LAKH:
        return f"Rs {v / LAKH:,.2f} lakh".replace(".00 lakh", " lakh")
    return f"Rs {v:,.0f}"


def _lowest_officer(table: dict, value: float) -> str | None:
    """The lowest-ranking officer whose power covers `value` (AEEE upward)."""
    for off in reversed(OFFICERS):
        cap = table.get(off)
        if cap is not None and value <= cap:
            return off
    return None


def gem_mode(value: float) -> tuple[str, str]:
    for cap, name, text in GEM_MODES:
        if value <= cap:
            return name, text
    return GEM_MODES[-1][1], GEM_MODES[-1][2]


def plan(value: float, kind: str = "goods", method: str = "open") -> dict:
    """How a purchase of `value` rupees goes: GeM mode, CESC tender type, inviting and accepting authority, warnings."""
    value = float(value or 0)
    mode, mode_text = gem_mode(value)
    warnings: list[str] = []
    if value < MIN_PO_VALUE:
        warnings.append("No purchase order is placed where the total value is less than Rs 1,000 (DoP manual, purchases note).")
    if method == "limited" and value > LIMITED_TENDER_CAP:
        warnings.append("Limited tenders cannot be called for purchases of more than Rs 5.00 lakh (KTPP Act; DoP manual) "
                        "-- use an open (advertised) tender.")
        method = "open"
    inviter = _lowest_officer(DOP["invite"], value)
    acc_table = DOP["accept_limited"] if method == "limited" else DOP["accept_open"]
    acceptor = _lowest_officer(acc_table, value)
    above = None
    if acceptor is None:
        above = next((who for cap, who, _ in DOP_ABOVE_MD if value <= cap), "Board")
    authority_line = (f"{OFFICER_NAMES[acceptor]} ({acceptor}) -- powers up to {_fmt_rs(acc_table[acceptor])}" if acceptor else
                      f"{OFFICER_NAMES[above]} ({above}) -- procurement / award of works "
                      f"{next(t for c, w, t in DOP_ABOVE_MD if w == above)} (DoP Section A; the MD's power is up to Rs 3 crore)")
    ladder = [{"officer": o, "name": OFFICER_NAMES[o], "invite": _fmt_rs(DOP["invite"][o]) if DOP["invite"][o] is not None else "—",
               "accept_open": _fmt_rs(DOP["accept_open"][o]) if DOP["accept_open"][o] is not None else "—",
               "accept_limited": _fmt_rs(DOP["accept_limited"][o]) if DOP["accept_limited"][o] is not None else "—"}
              for o in OFFICERS]
    return {
        "estimated_value": value, "estimated_value_text": _fmt_rs(value), "kind": kind, "method": method,
        "gem_mode": mode, "gem_mode_text": mode_text,
        "tender_type": "Limited tender" if method == "limited" else "Open (advertised) tender",
        "invite_authority": f"{OFFICER_NAMES[inviter]} ({inviter})" if inviter else "Managing Director (MD)",
        "accept_authority": authority_line,
        "ladder": ladder, "warnings": warnings, "notes": DOP_NOTES,
        "single_tender_guidelines": SINGLE_TENDER_GUIDELINES,
        "state_note": ("CESC procures through GeM (gem.gov.in), as confirmed by CESC. The GeM thresholds above are those of "
                       "GFR 2017 Rule 149 (Government of India); the approving authority and limited-tender caps follow the "
                       "CESC Manual of Delegation of Powers (2023)."),
        "sources": ["CESC Manual of Delegation of Powers, First Edition 2023 (Sections A and B, Annexures 1-2, single-tender guidelines)",
                    "GFR 2017 Rule 149, as amended by Ministry of Finance OM F.1/3/2024-PPD dated 10.07.2024",
                    "GeM bid document format (gem.gov.in / bidplus)"],
    }


# ---------------------------------------------------------------------------------------------- draft
TEXT_FIELDS = ["title", "department", "organisation", "office", "buyer_email", "item_category", "contract_period",
               "bid_end", "bid_opening", "offer_validity_days", "turnover_lakh", "experience_years", "documents_required",
               "evaluation_method", "bid_type", "technical_clarification_days", "emd_amount", "epbg_percent", "epbg_months",
               "beneficiary", "consignee_name", "consignee_address", "delivery_days", "additional_terms", "kind", "method",
               "ra_enabled", "mse_exemption", "startup_exemption", "mii", "mse_preference", "past_experience_similar",
               "arbitration", "mediation", "inspection", "estimated_value", "subdivision"]


@dataclass
class TenderForm:
    title: str = ""
    estimated_value: float = 0.0
    kind: str = "goods"               # goods / services / works
    method: str = "open"              # open / limited
    department: str = "Chamundeshwari Electricity Supply Corporation Limited (CESC), Mysuru"
    organisation: str = "Chamundeshwari Electricity Supply Corporation Limited"
    office: str = ""
    buyer_email: str = ""
    item_category: str = ""
    contract_period: str = ""
    bid_end: str = ""
    bid_opening: str = ""
    offer_validity_days: int = 180
    turnover_lakh: float | None = None
    experience_years: int | None = None
    past_experience_similar: bool = True
    mse_exemption: bool = True
    startup_exemption: bool = True
    documents_required: str = "Experience Criteria, Past Performance, Bidder Turnover, Certificate (Requested in ATC), OEM Authorization Certificate"
    ra_enabled: bool = True
    bid_type: str = "Two Packet Bid"
    technical_clarification_days: int = 2
    evaluation_method: str = "Total value wise evaluation"
    emd_amount: float | None = None
    epbg_percent: float = 5.0
    epbg_months: int | None = None
    beneficiary: str = ""
    mii: bool = True
    mse_preference: bool = True
    arbitration: bool = False
    mediation: bool = False
    inspection: bool = True
    items: list[dict] = field(default_factory=list)        # [{name, specification, quantity, unit, delivery_days}]
    consignee_name: str = ""
    consignee_address: str = ""
    delivery_days: int | None = None
    additional_terms: str = ""
    subdivision: str = ""


def form_from(data: dict) -> TenderForm:
    """A validated form: known keys only, strings capped, numbers parsed, at most 60 items."""
    f = TenderForm()
    for k, v in (data or {}).items():
        if not hasattr(f, k) or k == "items":
            continue
        cur = getattr(f, k)
        try:
            if isinstance(cur, bool):
                v = bool(v) if not isinstance(v, str) else v.strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(cur, int) and not isinstance(cur, bool) or k in ("epbg_months", "delivery_days", "experience_years"):
                v = None if v in (None, "") else int(float(v))
            elif isinstance(cur, float) or k in ("turnover_lakh", "emd_amount", "estimated_value"):
                v = None if v in (None, "") else float(str(v).replace(",", ""))
            else:
                v = str(v or "")[:4000]
        except (TypeError, ValueError):
            continue
        setattr(f, k, v)
    f.kind = f.kind if f.kind in ("goods", "services", "works") else "goods"
    f.method = f.method if f.method in ("open", "limited") else "open"
    items = []
    for it in (data or {}).get("items") or []:
        if not isinstance(it, dict):
            continue
        items.append({"name": str(it.get("name") or "")[:300], "specification": str(it.get("specification") or "")[:3000],
                      "quantity": str(it.get("quantity") or "")[:40], "unit": str(it.get("unit") or "")[:40],
                      "delivery_days": str(it.get("delivery_days") or "")[:20]})
        if len(items) >= 60:
            break
    f.items = [i for i in items if i["name"] or i["specification"]]
    f.estimated_value = max(0.0, f.estimated_value or 0.0)
    return f


def suggested(f: TenderForm) -> dict:
    """Defaults the form leaves open: EMD 2% of the estimated value (GFR Rule 170 range 2-5%), ePBG duration = contract
    period + 2 months where the period is in months / years."""
    emd = f.emd_amount if f.emd_amount is not None else round(f.estimated_value * 0.02, -1)
    months = f.epbg_months
    if months is None:
        m = re.search(r"(\d+)\s*(year|month)", f.contract_period or "", re.I)
        months = (int(m[1]) * (12 if m[2].lower().startswith("year") else 1) + 2) if m else 14
    return {"emd_amount": emd, "epbg_months": months}


# A physical EMD / performance-security instrument (DD, banker's cheque) handed in after a bid is allowed on GeM; only
# physical documents as a condition to qualify are not.
_EMD_INSTRUMENT_RE = re.compile(r"\b(emd|pbg|performance\s+security|demand\s+draft|\bdd\b|banker'?s?\s+cheque|bank\s+guarantee)\b", re.I)


def compliance(text: str) -> list[dict]:
    """GeM's null-and-void clause list, checked against free text (buyer-added terms, specifications)."""
    out = []
    for idx, rx, what in _COMPLIANCE_RES:
        m = None
        for cand in rx.finditer(text or ""):
            if idx == 5 and _EMD_INSTRUMENT_RE.search((text or "")[max(0, cand.start() - 250):cand.end() + 60]):
                continue
            m = cand
            break
        if m:
            s = max(0, m.start() - 40)
            out.append({"severity": "error", "rule": f"GeM disclaimer clause {idx + 1}", "rule_text": GEM_PROHIBITED[idx],
                        "finding": what, "excerpt": (text[s:m.end() + 40]).strip().replace("\n", " ")})
    return out


def checks(f: TenderForm, p: dict) -> list[dict]:
    out = [{"severity": "error", "rule": "DoP / KTPP", "finding": w} for w in p["warnings"]]
    if not f.title:
        out.append({"severity": "error", "rule": "Bid details", "finding": "The tender needs a title / item description."})
    if not f.items:
        out.append({"severity": "warning", "rule": "Technical specifications", "finding": "No item / specification rows were given."})
    if len(f.items) == 1 and "boq" in (f.item_category or "").lower():
        out.append({"severity": "error", "rule": "GeM disclaimer clause 4", "finding": "A BoQ bid for a single item is not allowed on GeM."})
    if f.kind == "works" and "custom" in (f.item_category or "").lower():
        out.append({"severity": "error", "rule": "GeM disclaimer clause 7", "finding": "Works contracts are not floated as custom bids in services."})
    if f.estimated_value > 10 * LAKH and not f.ra_enabled:
        out.append({"severity": "info", "rule": "GFR Rule 149", "finding": "Above Rs 10 lakh GeM allows online bidding or reverse auction; Bid to RA is off."})
    if f.estimated_value and f.emd_amount is not None and not (0.02 * f.estimated_value - 1 <= f.emd_amount <= 0.05 * f.estimated_value + 1):
        out.append({"severity": "warning", "rule": "GFR Rule 170", "finding": f"EMD {_fmt_rs(f.emd_amount)} is outside 2-5% of the estimated value."})
    if f.turnover_lakh and f.estimated_value and f.turnover_lakh * LAKH > f.estimated_value:
        out.append({"severity": "warning", "rule": "Eligibility", "finding": "The turnover asked for is more than the estimated value of the bid; it may restrict competition."})
    text = f"{f.additional_terms}\n" + "\n".join(f"{i['name']} {i['specification']}" for i in f.items)
    out += compliance(text)
    if not [c for c in out if c["severity"] == "error"]:
        out.append({"severity": "ok", "rule": "GeM disclaimer", "finding": "No buyer-added term matched GeM's list of clauses that make a bid null and void."})
    return out


def _yes(b: bool) -> str:
    return "Yes" if b else "No"


def sections(f: TenderForm) -> dict:
    """The bid document as structured sections (shared by the HTML and the .docx)."""
    p = plan(f.estimated_value, f.kind, f.method)
    s = suggested(f)
    bid_details = [
        ("Bid End Date/Time", f.bid_end or "(to be set on GeM)"), ("Bid Opening Date/Time", f.bid_opening or "(30 minutes after bid end, set on GeM)"),
        ("Bid Offer Validity (From End Date)", f"{f.offer_validity_days} (Days)"),
        ("Ministry/State Name", "Karnataka"), ("Department Name", f.department), ("Organisation Name", f.organisation),
        ("Office Name", f.office or "(office)"), ("Buyer Email", f.buyer_email or "(buyer email)"),
        ("Item Category", f.item_category or f.title), ("Contract Period", f.contract_period or "—"),
        ("Minimum Average Annual Turnover of the bidder (For 3 Years)", f"{f.turnover_lakh:g} Lakh (s)" if f.turnover_lakh else "—"),
        ("Years of Past Experience Required for same/similar service" if f.kind == "services" else "Years of Past Experience Required",
         f"{f.experience_years} Year (s)" if f.experience_years else "—"),
        ("Past Experience of Similar Services required" if f.kind == "services" else "Past experience of similar supplies required", _yes(f.past_experience_similar)),
        ("MSE Exemption for Years of Experience and Turnover", _yes(f.mse_exemption)),
        ("Startup Exemption for Years of Experience and Turnover", _yes(f.startup_exemption)),
        ("Document required from seller", f.documents_required),
        ("Bid to RA enabled", _yes(f.ra_enabled)), ("Type of Bid", f.bid_type),
        ("Time allowed for Technical Clarifications during technical evaluation", f"{f.technical_clarification_days} Days"),
        ("Inspection Required (By Empanelled Inspection Authority / Agencies pre-registered with GeM)", _yes(f.inspection)),
        ("Estimated Bid Value", f"{f.estimated_value:,.0f}"), ("Evaluation Method", f.evaluation_method),
        ("Financial Document Indicating Price Breakup Required", "Yes"),
        ("Arbitration Clause", _yes(f.arbitration)), ("Mediation Clause", _yes(f.mediation)),
    ]
    emd = [("Advisory Bank", "State Bank of India"), ("EMD Amount", f"{s['emd_amount']:,.0f}")]
    epbg = [("Advisory Bank", "State Bank of India"), ("ePBG Percentage(%)", f"{f.epbg_percent:.2f}"),
            ("Duration of ePBG required (Months)", str(s["epbg_months"]))]
    std = [
        "(a). EMD EXEMPTION: The bidder seeking EMD exemption must submit the valid supporting document for the relevant "
        "category as per GeM GTC with the bid. Under MSE category, only manufacturers for goods and service providers for "
        "services are eligible for exemption from EMD. Traders are excluded from the purview of this policy.",
        "(b). EMD & Performance security should be in favour of the Beneficiary, wherever it is applicable.",
    ]
    clauses = [
        "The minimum average annual financial turnover of the bidder during the last three years, ending on 31st March of "
        "the previous financial year, should be as indicated above in the bid document. Documentary evidence in the form "
        "of certified Audited Balance Sheets of relevant periods or a certificate from the Chartered Accountant / Cost "
        "Accountant indicating the turnover details for the relevant period shall be uploaded with the bid. In case the "
        "date of constitution / incorporation of the bidder is less than 3 years old, the average turnover in respect of "
        "the completed financial years after the date of constitution shall be taken into account for this criteria.",
        "Years of Past Experience required: the bidder must have experience for the number of years indicated above in the "
        "bid document (ending month of March prior to the bid opening) of providing similar type of " +
        ("services" if f.kind == "services" else "goods") + " to any Central / State Govt Organization / PSU. Copies of "
        "relevant contracts / orders to be uploaded along with the bid in support of having provided them during each of the "
        "Financial year.",
        "Purchase preference to Micro and Small Enterprises (MSEs): purchase preference will be given to MSEs as defined in "
        "the Public Procurement Policy for Micro and Small Enterprises (MSEs) Order, 2012 dated 23.03.2012 and its "
        "subsequent Orders / Notifications. If L-1 is not an MSE and MSE bidder(s) have quoted a price within L-1 + 15% of "
        "margin of purchase preference / price band, the order will be awarded as per the policy subject to acceptance of "
        "the L1 bid price (concurrent application with the Public Procurement (Preference to Make in India) Order, 2017 per "
        "OM No.1_4_2021_PPD dated 18.05.2023).",
        "The Estimated Bid Value indicated above is declared solely for guidance on the EMD amount and for determining the "
        "eligibility criteria related to turnover, past performance and past experience. It has no bearing on the price "
        "to be quoted and is not a criterion for the reasonableness of quoted prices.",
    ]
    if f.past_experience_similar:
        clauses.append("Past experience of similar " + ("services" if f.kind == "services" else "supplies") + ": the bidder must "
                       "have successfully executed / completed similar work over the last three years, i.e. the current "
                       "financial year and the last three financial years (ending month of March prior to the bid opening): "
                       "three similar completed orders each costing not less than 40% of the estimated cost; or two each not "
                       "less than 50%; or one not less than 80%.")
    items = [(str(i + 1), it["name"], it["specification"], f"{it['quantity']} {it['unit']}".strip(),
              it["delivery_days"] or (str(f.delivery_days) if f.delivery_days else "—")) for i, it in enumerate(f.items)]
    consignees = [("1", f.consignee_name or "(reporting officer)", f.consignee_address or "(address)",
                   f.items[0]["quantity"] if len(f.items) == 1 else "As per item list", str(f.delivery_days or "—"))]
    atc = ["OPTION CLAUSE: The buyer can increase or decrease the contract quantity or contract duration up to 25 percent "
           "at the time of issue of the contract. However, once the contract is issued, contract quantity or contract "
           "duration can only be increased up to 25 percent. Bidders are bound to accept the revised quantity or duration."]
    atc += [t.strip() for t in re.split(r"\n\s*\n|\n(?=\d+[.)]\s)", f.additional_terms or "") if t.strip()]
    disclaimer_tail = [
        "If any seller has any objection / grievance against these additional clauses or otherwise on any aspect of this "
        "bid, they can raise their representation using the Representation window in the bid details field of the Seller "
        "dashboard within 4 days of bid publication on GeM. The buyer is duty bound to reply to all such representations.",
        "In terms of GeM GTC clause 26 regarding restrictions on procurement from a bidder of a country which shares a land "
        "border with India, any bidder from such a country will be eligible to bid only if registered with the Competent "
        "Authority.",
    ]
    return {"plan": p, "suggested": s, "bid_details": bid_details, "emd": emd, "epbg": epbg, "std": std,
            "beneficiary": f.beneficiary or "(designation, office, CESC)", "mii": _yes(f.mii), "mse": _yes(f.mse_preference),
            "clauses": clauses, "items": items, "consignees": consignees, "atc": atc, "disclaimer": GEM_PROHIBITED,
            "disclaimer_tail": disclaimer_tail, "checks": checks(f, p)}


def render_html(f: TenderForm) -> tuple[str, dict]:
    s = sections(f)
    e = html.escape
    kv = lambda rows: "<table class='kv'>" + "".join(f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>" for k, v in rows) + "</table>"
    grid = lambda head, rows: ("<table class='grid'><thead><tr>" + "".join(f"<th>{e(h)}</th>" for h in head) + "</tr></thead><tbody>"
                               + "".join("<tr>" + "".join(f"<td>{e(str(c))}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table>")
    p = s["plan"]
    parts = [
        f"<div class='doc-head'><div class='doc-kicker'>Bid Document (GeM format) — draft</div><h2>{e(f.title or 'Untitled tender')}</h2>"
        f"<div class='doc-sub'>{e(f.organisation)}{(' · ' + e(f.office)) if f.office else ''} · prepared {dt.date.today():%d-%m-%Y}</div></div>",
        "<h3>Bid Details</h3>", kv(s["bid_details"]),
        "<h3>EMD Detail</h3>", kv(s["emd"]), "<h3>ePBG Detail</h3>", kv(s["epbg"]),
        "".join(f"<p class='small'>{e(t)}</p>" for t in s["std"]),
        "<h3>Beneficiary</h3>", f"<p>{e(s['beneficiary'])}</p>",
        "<h3>MII Compliance</h3>", kv([("MII Compliance", s["mii"])]),
        "<h3>MSE Purchase Preference</h3>", kv([("MSE Purchase Preference", s["mse"])]),
        "<ol class='clauses'>" + "".join(f"<li>{e(c)}</li>" for c in s["clauses"]) + "</ol>",
        "<h3>Technical Specifications</h3>",
        grid(["S.No.", "Item", "Specification", "Quantity", "Delivery (days)"], s["items"] or [("—", "—", "—", "—", "—")]),
        "<h3>Consignees / Reporting Officer</h3>",
        grid(["S.No.", "Consignee / Reporting Officer", "Address", "Quantity", "Delivery days"], s["consignees"]),
        "<h3>Buyer Added Bid Specific Terms and Conditions</h3>",
        "<ol class='clauses'>" + "".join(f"<li>{e(t)}</li>" for t in s["atc"]) + "</ol>",
        "<h3>Disclaimer</h3>",
        "<p class='small'>The additional terms and conditions have been incorporated by the Buyer after approval of the "
        "Competent Authority in the Buyer Organization, whereby the Buyer organization is solely responsible for the impact "
        "of these clauses on the bidding process. If any clause(s) is / are incorporated by the Buyer regarding the "
        "following, the bid and resultant contracts shall be treated as null and void and such bids may be cancelled by "
        "GeM at any stage of the bidding process without notice:</p>",
        "<ol class='clauses small'>" + "".join(f"<li>{e(t)}</li>" for t in s["disclaimer"]) + "</ol>",
        "".join(f"<p class='small'>{e(t)}</p>" for t in s["disclaimer_tail"]),
        "<p class='small center'>---Thank You---</p>",
        "<div class='internal'><h3>CESC internal approval note (not part of the GeM bid)</h3>",
        kv([("Estimated value", p["estimated_value_text"]), ("GeM procurement mode", f"{p['gem_mode']} — {p['gem_mode_text']}"),
            ("Tender type", p["tender_type"]), ("Tender to be invited by", p["invite_authority"]),
            ("Tender to be accepted by", p["accept_authority"])]),
        "<p class='small'>" + e(p["state_note"]) + "</p>",
        "<ul class='small'>" + "".join(f"<li>{e(n)}</li>" for n in p["notes"]) + "</ul>",
        "<p class='small'><b>Sources:</b> " + e("; ".join(p["sources"])) + ".</p></div>",
    ]
    return "".join(parts), s


# ---------------------------------------------------------------------------------------------- .docx (no dependency)
def _x(t: str) -> str:
    return html.escape(str(t), quote=False)


def _run(text: str, bold: bool = False, size: int = 20) -> str:
    rpr = f"<w:rPr>{'<w:b/>' if bold else ''}<w:sz w:val=\"{size}\"/></w:rPr>"
    return f"<w:r>{rpr}<w:t xml:space=\"preserve\">{_x(text)}</w:t></w:r>"


_CENTER = '<w:jc w:val="center"/>'


def _p(text: str = "", bold: bool = False, size: int = 20, center: bool = False, space_after: int = 80) -> str:
    jc = _CENTER if center else ""
    ppr = f'<w:pPr><w:spacing w:after="{space_after}"/>{jc}</w:pPr>'
    return f"<w:p>{ppr}{_run(text, bold, size) if text else ''}</w:p>"


def _tbl(rows: list[list[str]], header: bool = False, widths: list[int] | None = None) -> str:
    border = "".join(f"<w:{s} w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"9AA4B5\"/>" for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
    out = [f"<w:tbl><w:tblPr><w:tblW w:w=\"5000\" w:type=\"pct\"/><w:tblBorders>{border}</w:tblBorders></w:tblPr>"]
    for i, r in enumerate(rows):
        out.append("<w:tr>")
        for j, c in enumerate(r):
            w = f"<w:tcW w:w=\"{widths[j]}\" w:type=\"dxa\"/>" if widths and j < len(widths) else ""
            shade = "<w:shd w:val=\"clear\" w:color=\"auto\" w:fill=\"E8ECF3\"/>" if (header and i == 0) else ""
            out.append(f"<w:tc><w:tcPr>{w}{shade}</w:tcPr>{_p(str(c), bold=(header and i == 0) or (not header and j == 0 and len(r) == 2), size=18, space_after=20)}</w:tc>")
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def render_docx(f: TenderForm) -> bytes:
    s = sections(f)
    p = s["plan"]
    body = [_p("Bid Document (GeM format) — draft", bold=True, size=18, center=True),
            _p(f.title or "Untitled tender", bold=True, size=30, center=True),
            _p(f"{f.organisation}{(' · ' + f.office) if f.office else ''} · prepared {dt.date.today():%d-%m-%Y}", size=18, center=True),
            _p("Bid Details", bold=True, size=24), _tbl([[k, v] for k, v in s["bid_details"]], widths=[4200, 5200]),
            _p("EMD Detail", bold=True, size=24), _tbl([[k, v] for k, v in s["emd"]], widths=[4200, 5200]),
            _p("ePBG Detail", bold=True, size=24), _tbl([[k, v] for k, v in s["epbg"]], widths=[4200, 5200])]
    body += [_p(t, size=18) for t in s["std"]]
    body += [_p("Beneficiary", bold=True, size=24), _p(s["beneficiary"]),
             _p("MII Compliance", bold=True, size=24), _tbl([["MII Compliance", s["mii"]]], widths=[4200, 5200]),
             _p("MSE Purchase Preference", bold=True, size=24), _tbl([["MSE Purchase Preference", s["mse"]]], widths=[4200, 5200])]
    body += [_p(f"{i + 1}. {c}", size=18) for i, c in enumerate(s["clauses"])]
    body += [_p("Technical Specifications", bold=True, size=24),
             _tbl([["S.No.", "Item", "Specification", "Quantity", "Delivery (days)"]] + [list(r) for r in (s["items"] or [("—",) * 5])], header=True),
             _p("Consignees / Reporting Officer", bold=True, size=24),
             _tbl([["S.No.", "Consignee / Reporting Officer", "Address", "Quantity", "Delivery days"]] + [list(r) for r in s["consignees"]], header=True),
             _p("Buyer Added Bid Specific Terms and Conditions", bold=True, size=24)]
    body += [_p(f"{i + 1}. {t}", size=18) for i, t in enumerate(s["atc"])]
    body += [_p("Disclaimer", bold=True, size=24),
             _p("The additional terms and conditions have been incorporated by the Buyer after approval of the Competent "
                "Authority in the Buyer Organization. If any clause(s) is / are incorporated by the Buyer regarding the "
                "following, the bid and resultant contracts shall be treated as null and void:", size=18)]
    body += [_p(f"{i + 1}. {t}", size=18) for i, t in enumerate(s["disclaimer"])]
    body += [_p(t, size=18) for t in s["disclaimer_tail"]] + [_p("---Thank You---", size=18, center=True)]
    body += [_p("CESC internal approval note (not part of the GeM bid)", bold=True, size=24),
             _tbl([["Estimated value", p["estimated_value_text"]], ["GeM procurement mode", f"{p['gem_mode']} — {p['gem_mode_text']}"],
                   ["Tender type", p["tender_type"]], ["Tender to be invited by", p["invite_authority"]],
                   ["Tender to be accepted by", p["accept_authority"]]], widths=[3200, 6200]),
             _p(p["state_note"], size=18)] + [_p("• " + n, size=18) for n in p["notes"]] + \
            [_p("Sources: " + "; ".join(p["sources"]) + ".", size=16)]
    doc = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
           "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body>" + "".join(body) +
           "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/><w:pgMar w:top=\"1134\" w:right=\"1134\" w:bottom=\"1134\" "
           "w:left=\"1134\" w:header=\"567\" w:footer=\"567\" w:gutter=\"0\"/></w:sectPr></w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
                   "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
                   "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
                   "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
                   "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
                   "</Types>")
        z.writestr("_rels/.rels",
                   "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
                   "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
                   "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>"
                   "</Relationships>")
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def file_name(f: TenderForm, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "_", f.title or "tender").strip("_")[:60] or "tender"
    return f"GeM_bid_draft_{base}.{ext}"


# ---------------------------------------------------------------------------------------------- analyse (Bid Assist)
_GEM_LABELS = [
    ("bid_number", r"Bid Number"), ("dated", r"Dated"), ("bid_end", r"Bid End Date/Time"), ("bid_opening", r"Bid Opening Date/Time"),
    ("offer_validity", r"Bid Offer Validity"), ("ministry", r"Ministry/State Name"), ("department", r"Department Name"),
    ("organisation", r"Organisation Name"), ("office", r"Office Name"), ("item_category", r"Item Category"),
    ("contract_period", r"Contract Period"), ("turnover", r"Minimum Average Annual Turnover"),
    ("oem_turnover", r"OEM Average Turnover"), ("experience_years", r"Years of Past Experience"),
    ("past_experience", r"Past Experience of Similar"), ("mse_exemption", r"MSE Exemption"),
    ("startup_exemption", r"Startup Exemption"), ("documents_required", r"Document required from seller"),
    ("ra_enabled", r"Bid to RA enabled"), ("bid_type", r"Type of Bid"), ("total_quantity", r"Total Quantity"),
    ("estimated_value", r"Estimated Bid Value"), ("evaluation_method", r"Evaluation Method"),
    ("emd_amount", r"EMD Amount"), ("epbg_percent", r"ePBG Percentage"), ("epbg_months", r"Duration of ePBG"),
    ("mii", r"MII Compliance"), ("mse_preference", r"MSE Purchase Preference"), ("arbitration", r"Arbitration Clause"),
    ("mediation", r"Mediation Clause"), ("inspection", r"Inspection Required"),
]
FIELD_LABELS = {"bid_number": "Bid number", "dated": "Dated", "bid_end": "Bid end date / time", "bid_opening": "Bid opening date / time",
                "offer_validity": "Offer validity", "ministry": "Ministry / State", "department": "Department",
                "organisation": "Organisation", "office": "Office", "item_category": "Item category",
                "contract_period": "Contract period", "turnover": "Minimum average annual turnover (3 years)",
                "oem_turnover": "OEM average turnover", "experience_years": "Years of past experience",
                "past_experience": "Past experience of similar work", "mse_exemption": "MSE exemption (experience / turnover)",
                "startup_exemption": "Startup exemption (experience / turnover)", "documents_required": "Documents required from seller",
                "ra_enabled": "Bid to RA enabled", "bid_type": "Type of bid", "total_quantity": "Total quantity",
                "estimated_value": "Estimated bid value", "evaluation_method": "Evaluation method", "emd_amount": "EMD amount",
                "epbg_percent": "ePBG %", "epbg_months": "ePBG duration (months)", "mii": "MII compliance",
                "mse_preference": "MSE purchase preference", "arbitration": "Arbitration clause", "mediation": "Mediation clause",
                "inspection": "Inspection required"}


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((pg.extract_text() or "") for pg in reader.pages[:80])


def _clean_lines(text: str) -> list[str]:
    """ASCII parts of each line (GeM bids are bilingual), without the label-only debris."""
    out = []
    for ln in (text or "").splitlines():
        a = re.sub(r"\s+", " ", "".join(ch if 32 <= ord(ch) < 127 else " " for ch in ln)).strip(" /")
        if re.search(r"[A-Za-z0-9]", a) and not re.fullmatch(r"[\W_]{0,3}\d{0,1}[\W_]{0,3}", a):
            out.append(a)
    return out


# The shape each GeM field's value has: bilingual GeM PDFs leave fragments of the Hindi labels ("D %", "45 ()") between
# a label and its value, and labels wrap over lines ("Minimum Average Annual Turnover of the" / "bidder (For 3 Years)"),
# so the value is the first following line that has the right shape.
_DATE = r"^\d{2}-\d{2}-\d{4}"
_YESNO = r"^(Yes|No)\b"
_VALUE_SHAPE = {
    "bid_number": r"^GEM/\d{4}/[BR]/\d+", "dated": _DATE, "bid_end": _DATE, "bid_opening": _DATE,
    "offer_validity": r"^\d+\s*\(?\s*Days?", "contract_period": r"^\d+\s*(Year|Month|Day|Week)",
    "turnover": r"^[\d,.]+\s*(Lakh|Crore|Cr\b|Thousand)", "oem_turnover": r"^[\d,.]+\s*(Lakh|Crore|Cr\b|Thousand)",
    "experience_years": r"^\d+\s*Year", "total_quantity": r"^[\d,]+$", "estimated_value": r"^[\d,]+(\.\d+)?$",
    "emd_amount": r"^[\d,]+(\.\d+)?$", "epbg_percent": r"^\d+(\.\d+)?$", "epbg_months": r"^\d+$",
    "bid_type": r"(Packet|Single|Two)\b.*Bid|Bid$", "evaluation_method": r"(wise|evaluation)",
    **{k: _YESNO for k in ("past_experience", "mse_exemption", "startup_exemption", "ra_enabled", "mii", "mse_preference",
                           "arbitration", "mediation", "inspection")},
}
_FREE_TEXT = {"ministry", "department", "organisation", "office", "item_category", "documents_required"}


def _gem_fields(lines: list[str]) -> dict:
    labels = [(k, re.compile(r"^" + lab, re.I)) for k, lab in _GEM_LABELS]
    is_label = lambda s: any(rx.match(s) for _, rx in labels) or re.match(r"^(Bid Details|EMD Detail|ePBG Detail|Beneficiary|"
                                                                           r"Technical Specifications|Consignees|Bid Document)", s, re.I)
    debris = lambda s: len(re.sub(r"[^A-Za-z]", "", s)) < 3 or bool(re.search(r"\(\s*\)", s))
    out = {}
    for i, ln in enumerate(lines):
        for k, rx in labels:
            if k in out or not rx.match(ln):
                continue
            after = re.sub(r"^[^A-Za-z0-9(]+", "", ln[rx.match(ln).end():]).strip(" :/")
            shape = _VALUE_SHAPE.get(k)
            if shape and re.search(shape, after or "", re.I) and not after.startswith("("):
                out[k] = after[:400]
                continue
            vals = []
            numeric = shape is not None and shape.startswith(r"^[\d") or shape in (r"^\d+$", r"^\d+(\.\d+)?$")
            for nxt in lines[i + 1:i + 14]:
                nxt = re.sub(r"^[^A-Za-z0-9(*]+", "", nxt)
                if is_label(nxt) and not (vals and k in _FREE_TEXT):
                    if vals or (shape is None and k not in _FREE_TEXT):
                        break
                    continue
                if shape:
                    if re.search(shape, nxt, re.I):
                        vals = [nxt]
                        if not numeric:
                            break
                        continue          # a number: the Hindi label can leave a stray number first -- keep the last
                    if vals:
                        break
                    continue
                if k == "documents_required" and vals:
                    if nxt.startswith("*") or is_label(nxt):
                        break
                    vals.append(nxt)
                    if len(vals) >= 4:
                        break
                    continue
                # free text: skip the label's own wrapped words and the Hindi debris, then take the value
                if debris(nxt) or re.fullmatch(r"(bidder|of the|same/similar service|services?|and turnover|turnover)", nxt, re.I):
                    continue
                if is_label(nxt):
                    break
                vals.append(nxt.split(" *")[0].split("*In case")[0].strip())
                if k == "documents_required":
                    continue
                if k != "item_category" or len(vals) >= 3 or nxt.endswith(")"):
                    break
            if vals:
                out[k] = " ".join(v for v in vals if v)[:400]
    return out


_MONEY = r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|lakh|lakhs|lac|l)?"


def _money(s: str) -> float | None:
    m = re.search(_MONEY, s or "", re.I)
    if not m:
        return None
    v = float(m[1].replace(",", ""))
    unit = (m[2] or "").lower()
    return v * (CRORE if unit.startswith("cr") else LAKH if unit.startswith(("lakh", "lac", "l")) else 1)


def _generic_fields(text: str) -> dict:
    """Any tender (not GeM): the usual fields by their common wording."""
    t = re.sub(r"\s+", " ", text or "")
    out = {}
    pats = {
        "bid_number": r"(?:tender|bid|nit|rfp|enquiry)\s*(?:no\.?|number|ref\.?)\s*[:\-]?\s*([A-Z0-9][A-Za-z0-9/\-.()]{3,60}[A-Za-z0-9)])",
        "estimated_value": r"(?:estimated\s+(?:cost|value)|tender\s+value|ecv|approximate\s+value)\s*(?:of\s+the\s+work)?\s*[:\-]?\s*((?:rs\.?|inr|₹)?\s*[\d,]+(?:\.\d+)?\s*(?:crore|cr|lakh|lakhs|lac)?)",
        "emd_amount": r"(?:emd|earnest\s+money(?:\s+deposit)?)\s*(?:amount)?\s*(?:of)?\s*[:\-]?\s*((?:rs\.?|inr|₹)?\s*[\d,]+(?:\.\d+)?\s*(?:crore|cr|lakh|lakhs|lac)?)",
        "bid_end": r"(?:last\s+date|bid\s+submission\s+end|closing\s+date|due\s+date)[^0-9]{0,40}(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm|hrs)?)?)",
        "bid_opening": r"(?:opening\s+(?:date|of\s+(?:technical\s+)?bids?))[^0-9]{0,40}(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm|hrs)?)?)",
        "turnover": r"(?:average\s+annual\s+turnover|annual\s+turnover|turnover)[^.]{0,80}?((?:rs\.?|inr|₹)\s*[\d,]+(?:\.\d+)?\s*(?:crore|cr|lakh|lakhs|lac)?|[\d,]+(?:\.\d+)?\s*(?:crore|cr|lakh|lakhs|lac))",
        "experience_years": r"(\d{1,2})\s*(?:\(\w+\)\s*)?years?\s+(?:of\s+)?(?:past\s+)?experience",
        "contract_period": r"(?:contract\s+period|period\s+of\s+(?:completion|contract)|completion\s+period)\s*[:\-]?\s*([\w ]{2,30}?(?:days|months|years?))",
        "epbg_percent": r"(?:performance\s+(?:bank\s+guarantee|security)|pbg|epbg)[^%]{0,60}?(\d{1,2}(?:\.\d+)?)\s*%",
    }
    for k, rx in pats.items():
        m = re.search(rx, t, re.I)
        if m:
            out[k] = m[1].strip(" .,;")
    if re.search(r"reverse\s+auction", t, re.I):
        out.setdefault("ra_enabled", "Yes (reverse auction mentioned)")
    if re.search(r"\b(mse|msme)\b.{0,60}\bexempt", t, re.I):
        out.setdefault("mse_exemption", "Mentioned")
    return out


def _num(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"[\d,]+(?:\.\d+)?", s)
    return float(m[0].replace(",", "")) if m else None


def analyse(text: str, profile: dict | None = None) -> dict:
    lines = _clean_lines(text)
    is_gem = bool(re.search(r"\bGEM/\d{4}/[BR]/\d+", text or "")) or sum(1 for l in lines if re.match(r"^Bid (End|Opening) Date", l)) >= 1
    fields = _gem_fields(lines) if is_gem else {}
    for k, v in _generic_fields(text).items():
        fields.setdefault(k, v)
    if is_gem and "bid_number" not in fields:
        m = re.search(r"GEM/\d{4}/[BR]/\d+", text or "")
        if m:
            fields["bid_number"] = m[0]
    # numbers
    est = _money(fields.get("estimated_value", "")) if fields.get("estimated_value") else None
    emd = _money(fields.get("emd_amount", "")) if fields.get("emd_amount") else None
    turnover = None
    if fields.get("turnover"):
        tv = fields["turnover"]
        turnover = (_num(tv) or 0) * (LAKH if re.search(r"lakh|lac", tv, re.I) else CRORE if re.search(r"crore|cr\b", tv, re.I) else 1)
    exp_years = _num(fields.get("experience_years"))
    # summary
    s = []
    if fields.get("item_category") or fields.get("bid_number"):
        s.append(f"{'GeM bid' if is_gem else 'Tender'} {fields.get('bid_number', '')} for {fields.get('item_category', 'the items listed')}"
                 + (f" by {fields['organisation']}" if fields.get("organisation") else "") + ".")
    if est:
        mode, _ = gem_mode(est)
        s.append(f"Estimated value {_fmt_rs(est)}" + (f"; EMD {_fmt_rs(emd)} ({emd / est * 100:.2f}% of it)" if emd and est else "")
                 + f". On GeM a purchase of this size is: {mode}.")
    if fields.get("bid_end"):
        s.append(f"Bids close {fields['bid_end']}" + (f" and open {fields['bid_opening']}" if fields.get("bid_opening") else "") + ".")
    if turnover or exp_years:
        s.append("Eligibility: " + ", ".join(x for x in [f"average turnover {_fmt_rs(turnover)}" if turnover else "",
                                                          f"{exp_years:g} years' experience" if exp_years else ""] if x)
                 + (f" (MSE exemption: {fields['mse_exemption']}; startup exemption: {fields.get('startup_exemption', '—')})" if fields.get("mse_exemption") else "") + ".")
    if fields.get("ra_enabled"):
        s.append(f"Reverse auction: {fields['ra_enabled']}.")
    # ATC part (GeM) or the whole text for the compliance check
    atc_text = text or ""
    m = re.search(r"Buyer Added Bid Specific Terms and Conditions(.*?)(Disclaimer|$)", text or "", re.S | re.I)
    if m:
        atc_text = m[1]
    comp = compliance(atc_text)
    # eligibility against a bidder profile
    elig = []
    if profile:
        is_mse = bool(profile.get("is_mse"))
        is_startup = bool(profile.get("is_startup"))
        mse_ok = str(fields.get("mse_exemption", "")).lower().startswith("yes")
        st_ok = str(fields.get("startup_exemption", "")).lower().startswith("yes")
        exempt = (is_mse and mse_ok) or (is_startup and st_ok)
        pt = profile.get("turnover_lakh")
        if turnover:
            if pt not in (None, ""):
                ok = float(pt) * LAKH >= turnover
                elig.append({"criterion": f"Average annual turnover ≥ {_fmt_rs(turnover)}",
                             "result": "pass" if ok else ("exempt" if exempt else "fail"),
                             "detail": f"bidder {_fmt_rs(float(pt) * LAKH)}"})
        pe = profile.get("experience_years")
        if exp_years:
            if pe not in (None, ""):
                ok = float(pe) >= exp_years
                elig.append({"criterion": f"{exp_years:g} years of past experience", "result": "pass" if ok else ("exempt" if exempt else "fail"),
                             "detail": f"bidder {float(pe):g} years"})
        if emd:
            elig.append({"criterion": f"EMD {_fmt_rs(emd)}", "result": "exempt" if is_mse else "pay",
                         "detail": "MSE manufacturers / service providers are exempt under GeM GTC (traders are not)" if is_mse else "to be paid"})
        if str(fields.get("mse_preference", "")).lower().startswith("yes") and is_mse:
            elig.append({"criterion": "MSE purchase preference", "result": "benefit", "detail": "L1 + 15% price band as per the MSE Order 2012"})
        if str(fields.get("mii", "")).lower().startswith("yes"):
            elig.append({"criterion": "Make in India (MII) compliance", "result": "check",
                         "detail": "needs the local-content declaration (Class I / Class II supplier)"})
    verdict = None
    if elig:
        verdict = "Not eligible" if any(e["result"] == "fail" for e in elig) else "Eligible"
    ordered = [(FIELD_LABELS.get(k, k), fields[k]) for k in FIELD_LABELS if k in fields]
    return {"is_gem": is_gem, "fields": dict(ordered), "summary": " ".join(s) or "Few standard fields were found in this text.",
            "compliance": comp, "eligibility": elig, "verdict": verdict,
            "numbers": {"estimated_value": est, "emd": emd, "turnover": turnover, "experience_years": exp_years},
            "characters": len(text or "")}
