"""Tender generation and analysis (app/tender.py, /api/tender/*): GeM procurement modes (GFR Rule 149, 2024), CESC's
delegation of powers for purchases, the GeM-format bid document (HTML and .docx), the compliance check against GeM's
null-and-void clause list, and the Bid Assist-style analyser on a real published GeM bid
(tests/fixtures/gem_bid_GEM_2025_B_5970949.pdf, Delhi Milk Scheme)."""
import base64
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import tender
from app.main import app

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gem_bid_GEM_2025_B_5970949.pdf"


# ---------------------------------------------------------------------------------------------- plan
@pytest.mark.parametrize("value,mode", [(40_000, "Direct purchase"), (50_000, "Direct purchase"), (50_001, "L1 purchase"),
                                        (10_00_000, "L1 purchase"), (10_00_001, "Bid / reverse auction")])
def test_gem_modes_follow_gfr_rule_149_2024(value, mode):
    assert tender.plan(value)["gem_mode"] == mode


@pytest.mark.parametrize("value,method,who", [
    (80_000, "open", "(EEE)"), (1_00_000, "open", "(EEE)"), (4_00_000, "open", "(SEE)"), (9_00_000, "open", "(CEE)"),
    (2_00_00_000, "open", "(MD)"), (5_00_00_000, "open", "(CPC)"), (12_00_00_000, "open", "(Board)"),
    (20_000, "limited", "(EEE)"), (40_000, "limited", "(SEE)"), (3_00_000, "limited", "(MD)"),
])
def test_acceptance_authority_follows_the_delegation_of_powers(value, method, who):
    assert who in tender.plan(value, "goods", method)["accept_authority"]


def test_limited_tender_above_5_lakh_is_refused():
    p = tender.plan(6_00_000, "goods", "limited")
    assert p["method"] == "open" and any("KTPP" in w for w in p["warnings"])


def test_inviting_authority():
    assert "(EEE)" in tender.plan(20_000)["invite_authority"]
    assert "(CEE)" in tender.plan(2_50_000)["invite_authority"]
    assert "(MD)" in tender.plan(50_00_000)["invite_authority"]


# ---------------------------------------------------------------------------------------------- draft
FORM = {"title": "Supply of 25 kVA distribution transformers", "estimated_value": "2500000", "kind": "goods",
        "item_category": "Distribution Transformer", "office": "O&M Division, Holenarasipura", "contract_period": "1 Year(s)",
        "turnover_lakh": "7.5", "experience_years": "3",
        "items": [{"name": "Distribution transformer 25 kVA", "specification": "11 kV / 433 V, IS 1180, BEE star 2",
                   "quantity": "40", "unit": "Nos", "delivery_days": "60"}]}


def test_draft_has_the_gem_sections_and_cesc_note():
    f = tender.form_from(FORM)
    html, s = tender.render_html(f)
    for needle in ("Bid Details", "EMD Detail", "ePBG Detail", "MII Compliance", "MSE Purchase Preference",
                   "Technical Specifications", "Consignees / Reporting Officer", "Buyer Added Bid Specific Terms",
                   "OPTION CLAUSE", "Disclaimer", "CESC internal approval note", "Bid / reverse auction"):
        assert needle in html, needle
    assert s["suggested"]["emd_amount"] == 50000          # 2% of Rs 25 lakh
    assert s["suggested"]["epbg_months"] == 14             # 1 year + 2 months
    assert "(CEE)" not in s["plan"]["accept_authority"] and "(MD)" in s["plan"]["accept_authority"]


def test_draft_escapes_user_text():
    f = tender.form_from({**FORM, "title": "<script>alert(1)</script>"})
    html, _ = tender.render_html(f)
    assert "<script>" not in html and "&lt;script&gt;" in html


@pytest.mark.parametrize("term,clause", [
    ("Make: Siemens only", "GeM disclaimer clause 5"),
    ("Bidders shall submit hard copy of all documents before the bid end date.", "GeM disclaimer clause 6"),
    ("Samples to be submitted along with the bid.", "GeM disclaimer clause 8"),
    ("A tender fee of Rs 5,000 is payable.", "GeM disclaimer clause 14"),
    ("Terms as per the website www.example.com apply.", "GeM disclaimer clause 13"),
    ("MSEs are not eligible for exemption.", "GeM disclaimer clause 12"),
])
def test_compliance_flags_gem_null_and_void_clauses(term, clause):
    f = tender.form_from({**FORM, "additional_terms": term})
    rules = [c["rule"] for c in tender.checks(f, tender.plan(f.estimated_value))]
    assert clause in rules, (term, rules)


def test_emd_demand_draft_hard_copy_is_allowed():
    f = tender.form_from({**FORM, "additional_terms": "Bidders can submit the EMD as a demand draft; the hardcopy of the DD "
                                                      "must reach the buyer within 5 days of bid end."})
    assert all(c["severity"] != "error" for c in tender.checks(f, tender.plan(f.estimated_value)))


def test_docx_is_a_valid_word_document():
    blob = tender.render_docx(tender.form_from(FORM))
    z = zipfile.ZipFile(io.BytesIO(blob))
    assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(z.namelist())
    doc = z.read("word/document.xml").decode()
    assert "Bid Details" in doc and "Distribution transformer 25 kVA" in doc and "<w:tbl>" in doc


# ---------------------------------------------------------------------------------------------- analyse
def test_analyser_reads_a_real_gem_bid():
    a = tender.analyse(tender.pdf_text(FIXTURE.read_bytes()), {"turnover_lakh": 10, "experience_years": 2, "is_mse": True})
    f = a["fields"]
    assert a["is_gem"]
    assert f["Bid number"] == "GEM/2025/B/5970949" and f["Dated"] == "22-02-2025"
    assert f["Bid end date / time"] == "15-03-2025 16:00:00" and f["Bid opening date / time"] == "15-03-2025 16:30:00"
    assert f["Organisation"] == "Delhi Milk Scheme" and f["Contract period"] == "2 Year(s)"
    assert f["Minimum average annual turnover (3 years)"] == "13 Lakh (s)" and f["Years of past experience"] == "1 Year (s)"
    assert f["Estimated bid value"] == "1680000" and f["EMD amount"] == "16800"
    assert f["ePBG %"] == "5.00" and f["ePBG duration (months)"] == "38"
    assert f["Type of bid"] == "Two Packet Bid" and f["Bid to RA enabled"] == "No"
    assert f["MSE exemption (experience / turnover)"] == "No" and f["MII compliance"] == "Yes"
    assert "Bidder Turnover" in f["Documents required from seller"] and "Additional Doc 2" in f["Documents required from seller"]
    assert a["compliance"] == []                           # its DD hard-copy clause is an allowed EMD form
    # 10 lakh turnover < 13 lakh asked, and this bid gives MSEs no turnover exemption
    assert a["verdict"] == "Not eligible"
    assert any(e["result"] == "fail" and "turnover" in e["criterion"].lower() for e in a["eligibility"])


def test_analyser_reads_plain_tender_text():
    text = ("NIT No. CESC/HNP/2025-26/14. Estimated cost Rs 12.5 lakh. EMD Rs 25,000. Last date for submission "
            "20-11-2025 17:00. Bidders must have 3 years of experience and an average annual turnover of Rs 5 lakh.")
    a = tender.analyse(text)
    assert a["numbers"]["estimated_value"] == 12.5e5 and a["numbers"]["emd"] == 25000
    assert a["fields"]["Bid end date / time"].startswith("20-11-2025")
    assert a["numbers"]["experience_years"] == 3


# ---------------------------------------------------------------------------------------------- API
def test_tender_api():
    with TestClient(app) as c:
        assert c.get("/tenders").status_code == 200
        assert len(c.get("/api/tender/rules").json()["prohibited"]) == 15
        p = c.post("/api/tender/plan", json={"estimated_value": 2500000}).json()
        assert p["gem_mode"] == "Bid / reverse auction"
        d = c.post("/api/tender/draft", json=FORM).json()
        assert "Bid Details" in d["html"] and d["checks"]
        r = c.post("/api/tender/docx", json=FORM)
        assert r.status_code == 200 and r.content[:2] == b"PK"
        a = c.post("/api/tender/analyse", json={"pdf_base64": base64.b64encode(FIXTURE.read_bytes()).decode()}).json()
        assert a["fields"]["Bid number"] == "GEM/2025/B/5970949"
        assert c.post("/api/tender/analyse", json={"pdf_base64": base64.b64encode(b"not a pdf").decode()}).status_code == 400
