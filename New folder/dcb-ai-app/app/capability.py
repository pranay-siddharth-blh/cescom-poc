"""
Capability check (no LLM, no SQL): a question that needs data this DCB statement does not hold -- monthly figures,
individual consumers, other divisions, tariff rates, losses and outages, payment channels, staff -- gets the
reviewed note from DCB_Mart/metadata/capability_manifest.json instead of a guessed or empty answer.
"""
from __future__ import annotations

import re

from app import mart_layer

_COVERED = ("demand, collection (cash and adjustment / subsidy), collection efficiency with and without adjustment, "
            "opening and closing balance (arrears), installations (total, live, billed, DC, MNR), billing efficiency and "
            "AT&C loss, metered and assessed consumption, FPPCA billed / collected / outstanding, connected load, for the "
            "whole division and each sub-division, by tariff category (as the CESC tariff booklet names them), family and "
            "slab, for every fiscal year from 2015-16 to 2025-26; and the CESC tariff booklet's rates and rules for "
            "FY2025-26 to FY2027-28")
# FPPCA is in the data: its full name contains "power purchase cost", which on its own (the cost of buying power) is not
# wording the manifest's consumer_level keywords miss
_CONSUMER_RE = re.compile(r"\b(each|every|individual|particular)\s+(consumer|customer)s?\b|"
                          r"\b(consumer|customer)[- ]?(wise|level|by consumer)\b")
_FPPCA_RE = re.compile(r"fuel\s*(and|&)?\s*power\s+purchase\s+cost\s+adjust\w*|power\s+purchase\s+cost\s+adjust\w*|\bfppca\b")


def check(question: str) -> str | None:
    q = " " + re.sub(r"\s+", " ", (question or "").lower()) + " "
    q = _FPPCA_RE.sub(" fppca ", q)
    if _CONSUMER_RE.search(q):
        q += " individual consumer "
    if re.search(r"\b(paid|pay|paying|payments?)\s+(online|digitally|by upi|through upi|by card)\b|\bonline\s+(paid|payers?)\b", q):
        q += " online payment "
    for cap in mart_layer.capabilities():
        if cap["status"] != "not_loaded":
            continue
        for kw in cap.get("keywords", []):
            if re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])", q):
                return f"**{cap['note']}**\n\nWhat this data does cover: {_COVERED}."
    return None
