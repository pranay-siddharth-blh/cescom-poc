"""
Independent re-verification of the mart's three data facts straight from the RAW workbook -- no pipeline code, no
star schema: the sheets are re-read with openpyxl (read-only), blocks are found by their own 'Sl.No' header rows,
metric columns by their own header text, and line items carry their tariff code forward exactly as printed.

    python DCB_Mart/verify_against_raw.py            (~15 s; writes ../reports/raw_reverification.md)

Checks (every one must PASS; exit code 1 otherwise):
  1. LT-1 from 2024-25: the sheet prints the BMA / urban-local-body lighting slabs under LT-1 with their own subtotal
     'LT-1 A'; the mart's LT-1 equals ALL LT-1 line items (= 'Sub Total of LT-1' + 'Sub Total of LT-1 A', and =
     the 2025-26 sheet's own 'Total of LT-1'), in every block; those slabs were printed as LT-2(a) up to 2023-24.
  2. LT-5 up to 2023-24: the row printed 'Sub Total of LT-5' is the subtotal of the LT-5(a) slabs (all zero); the real
     family total is 'Sub Total of LT-5 (a+b)' = every LT-5 line item = the mart, in every block and year.
  3. Division vs sub-divisions: the division's printed GRAND TOTAL minus the sum of its sub-divisions' and section's
     GRAND TOTALs, per year; the whole gap is miscellaneous revenue (non-misc categories agree to the rupee).
The workbook and the locked folders are only read.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "HNPURA DIVISION DCB FY 2015 TO 2026.xlsx"
MART = HERE / "dcb_mart.sqlite"
REPORT = ROOT / "reports" / "raw_reverification.md"
GEO = ["Holenarasipura", "Arkalgud", "Hangarahalli", "Saligrama", "Ramanathapura", "Konanur"]


def s(v) -> str:
    return "" if v is None else str(v).strip()


def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", ""))
    except ValueError:
        return None


def block_name(title: str) -> str | None:
    t = re.sub(r"\s+", " ", title).strip()
    tl = t.lower()
    if "division office" in tl:
        return "Division_Office"
    m = re.search(r"(?:sub-?\s*divi?s?i?on|section)\s*:\s*([A-Za-z]+)", t, re.I)
    if m and "or section" not in tl:
        n = m.group(1).title()
        return "Ramanathapura" if n.startswith("Ramanath") else n
    if (re.search(r"\bdivision\s*:\s*holenarasipura", t, re.I) and "sub" not in tl) or tl.startswith("holenarasipura division"):
        return "Division_Total"
    return None


def family_of(code: str) -> str | None:
    m = re.match(r"^\s*(LT|HT)\s*-?\s*(\d+)", code, re.I)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    return "MISC" if re.match(r"^\d+(\.\d+)?$", code) else None


class Sheet:
    def __init__(self, fy: str, rows: list[tuple]):
        self.fy, self.rows = fy, rows
        starts = [(block_name(" ".join(s(x) for x in rows[i - 1][:6])), i - 1) for i in range(1, len(rows))
                  if rows[i] and s(rows[i][0]).lower().replace(" ", "") == "sl.no"]
        self.blocks = {}
        for k, (name, i) in enumerate(starts):
            if name:
                self.blocks[name] = (i, starts[k + 1][1] if k + 1 < len(starts) else len(rows))
        self.cols = self._columns(self.blocks["Division_Total"][0])

    def _columns(self, i0: int) -> dict:
        hdr = self.rows[i0 + 1:i0 + 5]
        width = max(len(r) for r in hdr)
        txt = [" | ".join(re.sub(r"\s+", " ", s(r[c])) for r in hdr if c < len(r) and s(r[c])).lower() for c in range(width)]
        cols = {"installations": 3}
        for c, t in enumerate(txt):
            if "demand total" in t and "demand" not in cols:
                cols["demand"] = c
            if re.search(r"fac total \(\s*\d+\s*\+\s*\d+\s*\)", t) and "collection" not in cols:
                cols["collection"] = c + 1          # the collection grand total follows 'FAC Total' in its group
        return cols

    def block(self, name: str):
        if name not in self.blocks:
            return None, None
        i, j = self.blocks[name]
        items, labels, last, body = [], [], "", False
        for k in range(i, j):
            r = self.rows[k]
            c0, c1 = s(r[0]), s(r[1]) if len(r) > 1 else ""
            if c0 == "1" and c1 == "2":
                body = True
                continue
            if not body:
                continue
            val = {key: (num(r[c]) if c < len(r) else None) for key, c in self.cols.items()}
            if c0 and not c0.isdigit() and re.search(r"(?i)total|misc", c0):
                labels.append({"row": k + 1, "label": re.sub(r"\s+", " ", c0), **val})
                continue
            if c1:
                last = c1
            desc = s(r[2]) if len(r) > 2 else ""
            if (c1 or desc) and (val["demand"] is not None or val["installations"] is not None) and family_of(last):
                items.append({"row": k + 1, "code": last, "family": family_of(last), "desc": desc, **val})
        return items, labels


def lab(labels, rx):
    return [x for x in labels if re.search(rx, x["label"], re.I)]


def money(v) -> str:
    return "—" if v is None else f"{v:,.2f}"


def main() -> int:
    t0 = datetime.now()
    wb = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
    sheets = {ws.title: Sheet(ws.title, [tuple(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets}
    fys = sorted(sheets)
    mart = sqlite3.connect(f"file:{MART}?mode=ro", uri=True)
    fails: list[str] = []
    out: list[str] = []

    def mart_family(fy, fam, sub):
        if sub == "Division_Total":
            q = mart.execute("SELECT demand_total FROM mart_fact_family_division_year WHERE fiscal_year=? AND family_code=?", (fy, fam))
        else:
            q = mart.execute("SELECT demand_total FROM mart_fact_family_subdivision_year WHERE fiscal_year=? AND family_code=? "
                             "AND subdivision_name=?", (fy, fam, sub))
        r = q.fetchone()
        return r[0] if r else None

    # ---- 1. LT-1 -----------------------------------------------------------------------------------------------
    out += ["## 1. LT-1 from 2024-25", "",
            "| FY | Block | Sub Total of LT-1 | Sub Total of LT-1 A | Total of LT-1 (printed) | All LT-1 line items | Mart LT-1 |",
            "|---|---|---:|---:|---:|---:|---:|"]
    for fy in ["2023-24", "2024-25", "2025-26"]:
        for name in ["Division_Total"] + GEO:
            items, labels = sheets[fy].block(name)
            if items is None:
                continue
            tot = sum(x["demand"] or 0 for x in items if x["family"] == "LT-1")
            st = lab(labels, r"^sub total of lt\s*-?\s*1\s*$")
            sta = lab(labels, r"^sub total of lt\s*-?\s*1\s*a\s*$")
            tl = lab(labels, r"^total of lt\s*-?\s*1\s*$")
            m = mart_family(fy, "LT-1", name)
            out.append(f"| {fy} | {name} | {money(st[0]['demand'] if st else None)} | {money(sta[0]['demand'] if sta else None)} | "
                       f"{money(tl[0]['demand'] if tl else None)} | {money(tot)} | {money(m)} |")
            parts = sum(x["demand"] or 0 for x in st + sta)
            if abs(parts - tot) > 1:
                fails.append(f"LT-1 {fy} {name}: printed subtotals {parts:,.2f} != line items {tot:,.2f}")
            if tl and abs(tl[0]["demand"] - tot) > 1:
                fails.append(f"LT-1 {fy} {name}: printed 'Total of LT-1' {tl[0]['demand']:,.2f} != line items {tot:,.2f}")
            if m is None or abs(m - tot) > 1:
                fails.append(f"LT-1 {fy} {name}: mart {m} != line items {tot:,.2f}")
    items24, _ = sheets["2024-25"].block("Division_Total")
    a_slabs = [x for x in items24 if x["family"] == "LT-1" and re.search(r"(?i)ltng|heating|motive", x["desc"])]
    key = lambda d: re.sub(r"[^a-z]", "", d.lower())[:30]
    items23, _ = sheets["2023-24"].block("Division_Total")
    out += ["", "The 2024-25 'LT-1 A' slabs and the code the same description carried in 2023-24 (division block):", ""]
    for a in a_slabs:
        prev = sorted({x["code"] for x in items23 if key(x["desc"]) == key(a["desc"])})
        out.append(f"- {a['desc']} — 2024-25 demand ₹{money(a['demand'])}; 2023-24 code(s): {', '.join(prev) or 'none'}")
        if not prev or not all(c.upper().startswith("LT-2(A)") for c in prev):
            fails.append(f"LT-1 A slab '{a['desc']}' was not LT-2(a) in 2023-24: {prev}")

    # ---- 2. LT-5 -----------------------------------------------------------------------------------------------
    out += ["", "## 2. LT-5 printed subtotals", "",
            "| FY | Block | LT-5(a) items: installations / demand | 'Sub Total of LT-5' | 'Sub Total of LT-5 (a+b)' | All LT-5 line items | Mart LT-5 |",
            "|---|---|---:|---:|---:|---:|---:|"]
    for fy in fys:
        for name in ["Division_Total"] + GEO:
            items, labels = sheets[fy].block(name)
            if items is None:
                continue
            lt5 = [x for x in items if x["family"] == "LT-5"]
            tot = sum(x["demand"] or 0 for x in lt5)
            st, stab = lab(labels, r"^sub total of lt\s*-?\s*5\s*$"), lab(labels, r"lt\s*-?\s*5\s*\(a\s*\+\s*b\)")
            # the LT-5(a) block = the LT-5 rows printed BEFORE the 'Sub Total of LT-5' row
            a_rows = [x for x in lt5 if st and x["row"] < st[0]["row"]] if stab else []
            a_inst, a_dem = sum(x["installations"] or 0 for x in a_rows), sum(x["demand"] or 0 for x in a_rows)
            m = mart_family(fy, "LT-5", name)
            out.append(f"| {fy} | {name} | {a_inst:,.0f} / {money(a_dem)} | {money(st[0]['demand'] if st else None)} | "
                       f"{money(stab[0]['demand'] if stab else None)} | {money(tot)} | {money(m)} |")
            if stab:
                if abs(stab[0]["demand"] - tot) > 1:
                    fails.append(f"LT-5 {fy} {name}: (a+b) {stab[0]['demand']:,.2f} != line items {tot:,.2f}")
                if st and abs((st[0]["demand"] or 0) - a_dem) > 1:
                    fails.append(f"LT-5 {fy} {name}: 'Sub Total of LT-5' {st[0]['demand']} is not the LT-5(a) subtotal {a_dem}")
            if m is None or abs(m - tot) > 1:
                fails.append(f"LT-5 {fy} {name}: mart {m} != line items {tot:,.2f}")

    # ---- 3. Division vs sub-divisions --------------------------------------------------------------------------
    out += ["", "## 3. Division total vs the sum of its sub-divisions and section (printed GRAND TOTAL rows)", "",
            "| FY | Division demand | Sum of units | Gap | Gap % | Collection gap | Installations gap | Non-misc gap | Largest misc gaps |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    office = []
    for fy in fys:
        di, dl = sheets[fy].block("Division_Total")
        g = lab(dl, r"grand total")[0]
        tot = {"demand": 0.0, "collection": 0.0, "installations": 0.0}
        nonmisc_sub, misc_sub = 0.0, defaultdict(float)
        for name in GEO:
            it, lb = sheets[fy].block(name)
            if it is None or not lab(lb, r"grand total"):
                continue
            gg = lab(lb, r"grand total")[0]
            for k in tot:
                tot[k] += gg[k] or 0
            nonmisc_sub += sum(x["demand"] or 0 for x in it if x["family"] != "MISC")
            for x in it:
                if x["family"] == "MISC":
                    misc_sub[x["code"]] += x["demand"] or 0
        misc_div = defaultdict(float)
        for x in di:
            if x["family"] == "MISC":
                misc_div[x["code"]] += x["demand"] or 0
        nonmisc_gap = sum(x["demand"] or 0 for x in di if x["family"] != "MISC") - nonmisc_sub
        gap = (g["demand"] or 0) - tot["demand"]
        top = sorted(((misc_div[c] - misc_sub[c], c) for c in set(misc_div) | set(misc_sub)), reverse=True)[:3]
        tops = "; ".join(f"{c} ₹{v:,.0f}" for v, c in top if v > 0.5) or "—"
        out.append(f"| {fy} | {money(g['demand'])} | {money(tot['demand'])} | {money(gap)} | {100 * gap / tot['demand']:+.3f}% | "
                   f"{money((g['collection'] or 0) - tot['collection'])} | {(g['installations'] or 0) - tot['installations']:+,.0f} | "
                   f"{money(nonmisc_gap)} | {tops} |")
        if abs(nonmisc_gap) > 1:
            fails.append(f"division vs sub-divisions {fy}: non-misc gap {nonmisc_gap:,.2f}")
        oi, ol = sheets[fy].block("Division_Office")
        if oi is not None:
            nz = [x for x in oi if any((x[k] or 0) != 0 for k in ("demand", "collection", "installations"))]
            office.append(f"- {fy}: Division_Office non-zero line items: " +
                          (", ".join(f"{x['code']} demand ₹{money(x['demand'])}" for x in nz) if nz else "none"))
    out += ["", "Division_Office blocks (administrative unit):", ""] + office

    status = "PASS" if not fails else f"FAIL ({len(fails)})"
    head = [f"# Raw-workbook re-verification — {status}", "",
            f"Generated by `DCB_Mart/verify_against_raw.py` on {t0:%Y-%m-%d %H:%M}, straight from "
            f"`{SRC.name}` (read-only) and `DCB_Mart/dcb_mart.sqlite` (read-only). Amounts are demand in rupees.", ""]
    if fails:
        head += ["**Failures:**", ""] + [f"- {f}" for f in fails] + [""]
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(head + out) + "\n", encoding="utf-8")
    print(f"{status}: {len(fails)} failure(s); report {REPORT} ({(datetime.now() - t0).total_seconds():.1f} s)")
    for f in fails:
        print("  FAIL", f)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
