"""
Dashboard selection -> Excel MIS workbook (GET /api/mis-report?subdivision=&fy=; pattern ported from the KPCL POC).

Sheets:
  Summary       letterhead, key figures against the previous year, observations written from the data, and the
                integrity checks the workbook was released under
  Year trend    every fiscal year for the scope
  Sub-divisions (whole division only) each sub-division for the year, their total, and the division's printed total
  Tariff families / Tariff slabs   the year's figures by family and by slab for the scope
  Notes         definitions and the known-issue notes that apply

Every figure comes from the mart; the Summary's headline figures are ALSO taken from app.dashboard_data (the exact
object the dashboard renders) and checked equal, so the workbook cannot disagree with the screen. Integrity checks:
the tariff families add up to the year's printed grand total, the year trend's selected year equals the Summary,
the sub-divisions agree with the division on installations and LT/HT demand. A failed check withholds the workbook
(ReportUnderReview -> HTTP 409).
"""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app import dashboard_data, mart_layer, scope as scopes
from app.db import ReadOnlyDB
from app.excel_common import (NAVY, ReportUnderReview, checks_block, close, data_rows, finalize, header_row, letterhead,
                              to_bytes, widths)

KPI_COLS = [("demand_total", "Demand (₹)"), ("collection_grand_total", "Collection (₹)"),
            ("collection_efficiency_pct", "Collection efficiency %"), ("uncollected_demand", "Uncollected demand (₹)"),
            ("opening_balance_total", "Opening balance (₹)"), ("closing_balance_total", "Closing balance / arrears (₹)"),
            ("total_installations", "Installations"), ("live_installations", "Live installations"),
            ("dc_mnr_installations", "DC/MNR installations"), ("consumption_total", "Consumption (units)"),
            ("demand_per_unit_rs", "Demand per unit (₹)"),
            # 2026-10-01: the sheet's own percentages (each reproduces the printed one), FPPCA, DC / MNR, per consumer
            ("live_installation_pct", "Live installations %"), ("billed_installation_pct", "Installations billed %"),
            ("dc_installations", "DC installations (from 2018-19)"), ("mnr_installations", "MNR installations (from 2018-19)"),
            ("dc_mnr_installation_pct", "DC/MNR % (as printed)"), ("mnr_installation_pct", "MNR %"),
            ("metered_consumption_pct", "Metered consumption %"), ("assessed_consumption_pct", "Assessed consumption %"),
            ("arrears_to_demand_ratio", "Arrears ÷ demand"),
            ("demand_fac", "FPPCA billed (₹)"), ("collection_fac_total", "FPPCA collected (₹)"),
            ("closing_balance_fac", "FPPCA outstanding (₹)"), ("fac_share_of_demand_pct", "FPPCA % of demand"),
            ("fac_per_unit_rs", "FPPCA per unit (₹)"),
            ("demand_per_installation_rs", "Demand per consumer (₹)"), ("collection_per_installation_rs", "Collection per consumer (₹)"),
            ("arrears_per_installation_rs", "Arrears per consumer (₹)"),
            # 2026-10-01 (second pass): cash / adjustment (the Government subsidy, confirmed by CESC), the sheet's
            # header formula for efficiency without adjustment, billing efficiency (billed / live) and AT&C loss, and the
            # energy rate billed (comparable with the CESC tariff booklet)
            ("collection_cash_total", "Collection in cash / cheque (₹)"),
            ("collection_adjust_total", "Government subsidy, collected by adjustment (₹)"),
            ("adjustment_share_pct", "Subsidy % of collection"),
            ("collection_efficiency_without_adj_pct", "Collection efficiency without adjustment %"),
            ("billing_efficiency_pct", "Billing efficiency % (billed ÷ live)"), ("atc_loss_pct", "AT&C loss %"),
            ("energy_charge_per_unit_paise", "Energy charge billed per unit (paise)")]
ADDITIVE = {"demand_total", "collection_grand_total", "uncollected_demand", "opening_balance_total", "closing_balance_total",
            "total_installations", "live_installations", "dc_mnr_installations", "consumption_total",
            "dc_installations", "mnr_installations", "demand_fac", "collection_fac_total", "closing_balance_fac",
            "collection_cash_total", "collection_adjust_total"}
# ratios: shown as they are, never summed, and no "change %" (the change column already gives points)
RATIOS = {c for c, _ in KPI_COLS if c.endswith(("_pct", "_ratio", "_rs", "_paise"))}


class NotFound(ValueError):
    pass


async def _rows(db: ReadOnlyDB, sql: str, params: list, timeout: float) -> tuple[list[str], list[tuple]]:
    r = await db.run(sql, max_rows=2000, timeout_seconds=timeout, params=params)
    return r.columns, r.rows


def _cols(extra: list[str] | None = None) -> str:
    return ", ".join((extra or []) + [c for c, _ in KPI_COLS])


async def build_mis_report(db: ReadOnlyDB, subdivision: str | None, fy: str | None, timeout: float,
                           as_workbook: bool = False):
    fys = mart_layer.fiscal_years()
    if subdivision and subdivision not in mart_layer.geographic_subdivisions():
        raise NotFound(f"Unknown sub-division {subdivision!r}.")
    if fy and fy not in fys:
        raise NotFound(f"Unknown fiscal year {fy!r}.")
    sc = scopes.build(subdivision)
    sub_row = next((s for s in mart_layer.subdivisions() if s["name"] == subdivision), None)
    fy = fy or (sub_row["last_fy"] if sub_row else mart_layer.latest_fy())
    if sub_row and not (sub_row["first_fy"] <= fy <= sub_row["last_fy"]):
        raise NotFound(f"{subdivision} has figures for {sub_row['first_fy']} to {sub_row['last_fy']} only.")
    prev = mart_layer.previous_fy(fy)
    checks: list[tuple[str, bool, str]] = []

    tc, trend = await _rows(db, f"SELECT {_cols(['fiscal_year'])} FROM {sc.table} WHERE {sc.where_sql()} ORDER BY fiscal_year",
                            sc.params, timeout)
    by_fy = {r[0]: dict(zip(tc, r)) for r in trend}
    cur, old = by_fy.get(fy), by_fy.get(prev) if prev else None
    if not cur:
        raise NotFound(f"No figures for {sc.label} in {fy}.")

    # the dashboard's own object (its latest year only): the workbook must agree with the screen
    dash = await (dashboard_data.build_subdivision_dashboard_summary(db, subdivision, 200, timeout) if subdivision
                  else dashboard_data.build_dashboard_summary(db, 200, timeout))
    if dash["fiscal_year"] == fy:
        k = dash["kpis"]
        ok = close(k["total_demand"], cur["demand_total"]) and close(k["total_collection"], cur["collection_grand_total"]) \
            and close(k["total_installations"], cur["total_installations"])
        checks.append(("Summary equals the dashboard (demand, collection, installations)", ok,
                       f"dashboard {k['total_demand']:,.2f} / {k['total_collection']:,.2f} / {k['total_installations']:,.0f}"))

    fam_t = scopes.T_FAM_SUB if subdivision else scopes.T_FAM_DIV
    slab_t = scopes.T_SLAB_SUB if subdivision else scopes.T_SLAB_DIV
    where = "fiscal_year = ?" + (" AND subdivision_name = ?" if subdivision else "")
    wparams = [fy] + ([subdivision] if subdivision else [])
    fc, fams = await _rows(db, f"SELECT {_cols(['family_code', 'family_label', 'voltage_class'])} FROM {fam_t} WHERE {where} "
                               f"ORDER BY family_order", wparams, timeout)
    scol, slabs = await _rows(db, f"SELECT {_cols(['family_code', 'tariff_code', 'description'])} FROM {slab_t} WHERE {where} "
                                  f"ORDER BY family_code, tariff_code, description", wparams, timeout)
    book_t = "mart_fact_booklet_subdivision_year" if subdivision else "mart_fact_booklet_division_year"
    bcol, books = await _rows(db, f"SELECT {_cols(['booklet_code', 'booklet_name'])} FROM {book_t} WHERE {where} "
                                  f"ORDER BY booklet_order", wparams, timeout)
    rates = {r[0]: r[1] for r in (await _rows(db, "SELECT booklet_code, value FROM mart_tariff_booklet_rate WHERE fiscal_year = ? "
                                                  "AND component IN ('energy_charge', 'cdt')", [fy], timeout))[1]}
    fam_sum = sum((dict(zip(fc, r))["demand_total"] or 0) for r in fams)
    checks.append(("Tariff families add up to the printed grand total (demand)", close(fam_sum, cur["demand_total"]),
                   f"families {fam_sum:,.2f} vs grand total {cur['demand_total']:,.2f}"))
    coll_sum = sum((dict(zip(fc, r))["collection_grand_total"] or 0) for r in fams)
    checks.append(("Tariff families add up to the printed grand total (collection)", close(coll_sum, cur["collection_grand_total"]),
                   f"families {coll_sum:,.2f} vs grand total {cur['collection_grand_total']:,.2f}"))
    slab_sum = sum((dict(zip(scol, r))["demand_total"] or 0) for r in slabs)
    book_sum = sum((dict(zip(bcol, r))["demand_total"] or 0) for r in books)
    lt_ht_sum = sum((dict(zip(fc, r))["demand_total"] or 0) for r in fams
                    if dict(zip(fc, r))["family_code"] not in ("MISC", "LT IPP", "HT IPP", "AUXILIARY CONSUMPTION"))
    checks.append(("Tariff-booklet categories add up to the LT + HT families (demand)", close(book_sum, lt_ht_sum),
                   f"booklet categories {book_sum:,.2f} vs LT + HT families {lt_ht_sum:,.2f}"))
    checks.append(("Tariff slabs add up to the families (demand)", close(slab_sum, fam_sum), f"slabs {slab_sum:,.2f}"))

    subs = None
    if not subdivision:
        sc2, subs = await _rows(db, f"SELECT {_cols(['subdivision_name'])}, lt_demand_total, ht_demand_total FROM "
                                    f"{scopes.T_SUB} WHERE fiscal_year = ? ORDER BY subdivision_name", [fy], timeout)
        d = {c: sum((dict(zip(sc2, r))[c] or 0) for r in subs) for c in ("total_installations", "lt_demand_total", "ht_demand_total")}
        dl = await _rows(db, "SELECT lt_demand_total, ht_demand_total FROM mart_fact_division_year WHERE fiscal_year = ?", [fy], timeout)
        lt, ht = dl[1][0]
        inst_ok = fy == fys[0] or close(d["total_installations"], cur["total_installations"])
        checks.append(("Sub-divisions agree with the division (installations, LT and HT demand)",
                       inst_ok and close(d["lt_demand_total"], lt) and close(d["ht_demand_total"], ht),
                       f"installations {d['total_installations']:,.0f} vs {cur['total_installations']:,.0f}"))

    checks.append(("Year trend has every year the scope reports", len(trend) == len([f for f in fys if (not sub_row) or sub_row['first_fy'] <= f <= sub_row['last_fy']]),
                   f"{len(trend)} years"))

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    title = f"DCB MIS report — {sc.label}, {fy}"
    r = letterhead(ws, "DCB MIS report", f"{sc.label} · fiscal year {fy}" + (f" (compared with {prev})" if old else ""), 6)
    header_row(ws, r, ["Measure", fy, prev or "", "Change", "Change %"])
    r += 1
    for col, label in KPI_COLS:
        v, p = cur.get(col), (old or {}).get(col)
        ch = (v - p) if (v is not None and p is not None) else None
        pct = (ch / p * 100) if (ch is not None and p and col not in RATIOS) else None
        r = data_rows(ws, r, ["measure", col, col, col, "change_pct"], [(label, v, p, ch, pct)])
    r += 1
    ws.cell(r, 1, "Observations").font = Font(bold=True, color=NAVY, size=12)
    r += 1
    for line in _observations(cur, old, fy, prev, fams, fc, subs):
        ws.cell(r, 1, "•  " + line)
        r += 1
    r += 1
    r = checks_block(ws, r, checks)
    widths(ws, {"A": 44, "B": 22, "C": 22, "D": 20, "E": 12, "F": 14})

    ws2 = wb.create_sheet("Year trend")
    rr = letterhead(ws2, "Year trend", sc.label, len(tc))
    header_row(ws2, rr, ["Fiscal year"] + [lab for _, lab in KPI_COLS])
    end = data_rows(ws2, rr + 1, tc, trend)
    widths(ws2, {get_column_letter(i + 1): 18 for i in range(len(tc))})
    ch = LineChart()
    ch.title, ch.y_axis.title, ch.height, ch.width = "Demand and collection (₹)", "₹", 8, 22
    ch.add_data(Reference(ws2, min_col=2, max_col=3, min_row=rr, max_row=end - 1), titles_from_data=True)
    ch.set_categories(Reference(ws2, min_col=1, min_row=rr + 1, max_row=end - 1))
    ws2.add_chart(ch, f"A{end + 2}")

    if subs is not None:
        ws3 = wb.create_sheet("Sub-divisions")
        rr = letterhead(ws3, "Sub-divisions", f"fiscal year {fy}", len(sc2))
        header_row(ws3, rr, ["Sub-division"] + [lab for _, lab in KPI_COLS] + ["LT demand (₹)", "HT demand (₹)"])
        end = data_rows(ws3, rr + 1, sc2, subs)
        tot = ["Sum of sub-divisions"] + [sum((x[i] or 0) for x in subs) if sc2[i] in ADDITIVE | {"lt_demand_total", "ht_demand_total"}
                                          else None for i in range(1, len(sc2))]
        ie = sc2.index("collection_efficiency_pct")
        tot[ie] = tot[sc2.index("collection_grand_total")] * 100 / tot[sc2.index("demand_total")] if tot[sc2.index("demand_total")] else None
        end = data_rows(ws3, end, sc2, [tuple(tot)], total=True)
        div_line = ["Division (printed grand total)"] + [cur.get(c) for c in sc2[1:]]
        end = data_rows(ws3, end, sc2, [tuple(div_line)], total=True)
        ws3.cell(end + 1, 1, "The division's own total includes miscellaneous revenue booked only at division level, so it "
                            "can exceed the sum of its sub-divisions (known issue KI-02).").font = Font(italic=True)
        widths(ws3, {get_column_letter(i + 1): 18 for i in range(len(sc2))})
        bc = BarChart()
        bc.title, bc.height, bc.width = "Collection efficiency %", 8, 18
        bc.add_data(Reference(ws3, min_col=ie + 1, min_row=rr, max_row=rr + len(subs)), titles_from_data=True)
        bc.set_categories(Reference(ws3, min_col=1, min_row=rr + 1, max_row=rr + len(subs)))
        ws3.add_chart(bc, f"A{end + 3}")

    ws4 = wb.create_sheet("Tariff families")
    rr = letterhead(ws4, "Tariff families", f"{sc.label} · fiscal year {fy}", len(fc))
    header_row(ws4, rr, ["Family", "Category", "LT / HT"] + [lab for _, lab in KPI_COLS])
    end = data_rows(ws4, rr + 1, fc, fams)
    tot = ["Total", "", ""] + [sum((x[i] or 0) for x in fams) if fc[i] in ADDITIVE else None for i in range(3, len(fc))]
    tot[fc.index("collection_efficiency_pct")] = (tot[fc.index("collection_grand_total")] * 100 / tot[fc.index("demand_total")]
                                                  if tot[fc.index("demand_total")] else None)
    data_rows(ws4, end, fc, [tuple(tot)], total=True)
    widths(ws4, {"A": 14, "B": 44, "C": 9, **{get_column_letter(i + 1): 18 for i in range(3, len(fc))}})

    ws45 = wb.create_sheet("Tariff categories (booklet)")
    rr = letterhead(ws45, "Tariff categories as the CESC tariff booklet names them",
                    f"{sc.label} · fiscal year {fy} · CESC tariff booklet FY2025-26 to FY2027-28 (KERC order 27.03.2025)", len(bcol) + 3)
    header_row(ws45, rr, ["Category", "Name", "% of demand", "Booklet energy rate (paise)"] + [lab for _, lab in KPI_COLS])
    keys45 = ["booklet_code", "booklet_name", "x_pct", "booklet_energy_paise"] + bcol[2:]
    out45 = []
    for row in books:
        d = dict(zip(bcol, row))
        share = d["demand_total"] * 100 / cur["demand_total"] if cur.get("demand_total") and d["demand_total"] is not None else None
        out45.append((d["booklet_code"], d["booklet_name"], share, rates.get(d["booklet_code"])) + tuple(row[2:]))
    end = data_rows(ws45, rr + 1, keys45, out45)
    ws45.cell(end + 1, 1, "Old codes the sheets printed before the 2024-25 restructuring are counted in today's category "
                          "(domestic LT-2(a) in LT-1, Government lift irrigation HT-3(a)(i) in HT-7, HT-3b in HT-6, hoardings "
                          "LT-7(b) in LT-3(b)). The booklet's rates cover FY2025-26 to FY2027-28 only.").font = Font(italic=True)
    widths(ws45, {"A": 12, "B": 44, "C": 12, "D": 14, **{get_column_letter(i + 1): 18 for i in range(4, len(keys45))}})

    ws5 = wb.create_sheet("Tariff slabs")
    rr = letterhead(ws5, "Tariff slabs", f"{sc.label} · fiscal year {fy}", len(scol))
    header_row(ws5, rr, ["Family", "Tariff code", "Slab"] + [lab for _, lab in KPI_COLS])
    data_rows(ws5, rr + 1, scol, slabs)
    widths(ws5, {"A": 12, "B": 16, "C": 60, **{get_column_letter(i + 1): 18 for i in range(3, len(scol))}})

    ws6 = wb.create_sheet("Notes")
    rr = letterhead(ws6, "Notes", "Definitions and data notes", 2)
    notes = [
        ("DCB", "Demand-Collection-Balance statement: what was billed (demand), what was paid (collection) and what is owed (balance)."),
        ("Collection efficiency", "Collection / demand × 100 for the scope and year. It can exceed 100% when old arrears are collected."),
        ("Closing balance (arrears)", "Opening balance + demand − collection at year end. A position: never added over years."),
        ("Uncollected demand", "Demand − collection for the year (negative when more was collected than billed)."),
        ("Installations", "Connections (consumers) on the books at year end. MNR = meter not recording. DC = direct connection "
                          "(supply without a meter while a faulty meter awaits replacement)."),
        ("DCB percentages", "Live %, billed %, DC/MNR %, metered / assessed consumption % and arrears ÷ demand are computed "
                            "exactly as the statement prints them (checked against every printed grand total)."),
        ("FPPCA", "Fuel and power purchase cost adjustment, printed as FAC in the statement: billed, collected and outstanding. "
                  "Negative in 2021-22 (a refund)."),
        ("Per consumer", "Demand, collection or arrears divided by the number of installations (consumers) at year end."),
        ("Tariff categories", "As the CESC tariff booklet FY2025-26 to FY2027-28 names them (KERC Tariff Order 2025 dated "
                              "27.03.2025): LT-1 Domestic ... HT-7 Government lift irrigation, with every sub-category "
                              "(LT-4(a), HT-2(c)(i) ...). The 'Tariff families' sheet is the code as each year's sheet printed it."),
        ("Collection by adjustment", "Collections the statement books as 'Adjust' rather than cheque / cash. For LT-1 domestic "
                                     "(Gruha Jyothi) and LT-4(a) IP sets: the Government subsidy (confirmed by CESC)."),
        ("Collection efficiency without adjustment", "Cash / cheque collection ÷ demand × 100, the statement's own header "
                                                     "formula (34 − Adj.) / 30."),
        ("Billing efficiency / AT&C loss", "Billing efficiency = installations billed ÷ live installations (the statement's "
                                           "'Billing Efficiency (4/3)'); AT&C loss = 100 − billing efficiency × collection "
                                           "efficiency. The data has no energy-input figures, so energy (technical) loss is not included."),
        ("Source", "The division's DCB statement workbook, via the locked star schema and the checked DCB mart."),
    ] + [(i["id"], i["caveat"] or i["detail"]) for i in mart_layer.known_issues() if i["caveat"]]
    header_row(ws6, rr, ["Term / note", "Meaning"])
    data_rows(ws6, rr + 1, ["t", "m"], notes)
    widths(ws6, {"A": 28, "B": 120})

    finalize(wb, checks, title)
    name = f"DCB_MIS_{(subdivision or 'Holenarasipura_Division').replace(' ', '_')}_{fy}.xlsx"
    return (wb, name) if as_workbook else (to_bytes(wb), name)


def _observations(cur: dict, old: dict | None, fy: str, prev: str | None, fams, fc, subs) -> list[str]:
    from app.answer import fmt_rupees
    out = []
    ce = cur.get("collection_efficiency_pct")
    if ce is not None:
        line = f"Collection efficiency was {ce:.2f}% in {fy}"
        if old and old.get("collection_efficiency_pct") is not None:
            d = ce - old["collection_efficiency_pct"]
            line += f", {'up' if d >= 0 else 'down'} {abs(d):.2f} points on {prev}"
        out.append(line + ".")
    if old and old.get("closing_balance_total"):
        d = (cur["closing_balance_total"] - old["closing_balance_total"]) / old["closing_balance_total"] * 100
        out.append(f"Arrears (closing balance) {'rose' if d >= 0 else 'fell'} {abs(d):.1f}% to {fmt_rupees(cur['closing_balance_total'])}.")
    if fams:
        top = max(fams, key=lambda r: dict(zip(fc, r))["demand_total"] or 0)
        t = dict(zip(fc, top))
        if cur.get("demand_total"):
            out.append(f"{t['family_code']} ({t['family_label'].split(' (')[0]}) was the largest tariff family: "
                       f"{t['demand_total'] / cur['demand_total'] * 100:.1f}% of demand.")
    if subs:
        low = min(subs, key=lambda r: r[3] if r[3] is not None else 1e9)
        out.append(f"Lowest collection efficiency among sub-divisions: {low[0]} ({low[3]:.2f}%).")
    if cur.get("demand_fac") is not None and cur.get("fac_share_of_demand_pct") is not None:
        word = "refunded" if cur["demand_fac"] < 0 else "billed"
        line = f"FPPCA {word}: {fmt_rupees(abs(cur['demand_fac']))} ({abs(cur['fac_share_of_demand_pct']):.2f}% of demand)"
        if cur.get("fac_collection_efficiency_pct") is not None and cur["demand_fac"] > 0:
            line += f"; {cur['fac_collection_efficiency_pct']:.1f}% of it collected"
        out.append(line + ".")
    if cur.get("atc_loss_pct") is not None:
        out.append(f"AT&C loss was {cur['atc_loss_pct']:.2f}% (billing efficiency {cur['billing_efficiency_pct']:.2f}% × "
                   f"collection efficiency {cur['collection_efficiency_pct']:.2f}%).")
    if cur.get("adjustment_share_pct") is not None and cur.get("collection_adjust_total") is not None:
        out.append(f"{cur['adjustment_share_pct']:.1f}% of the collection ({fmt_rupees(cur['collection_adjust_total'])}) was booked "
                   f"as adjustment, i.e. the Government subsidy; collection efficiency without adjustment was "
                   f"{cur['collection_efficiency_without_adj_pct']:.2f}%.")
    if cur.get("assessed_consumption_pct") is not None:
        line = f"Assessed (unmetered) consumption was {cur['assessed_consumption_pct']:.2f}% of all consumption"
        if cur.get("mnr_installations") is not None:
            line += f"; {cur['mnr_installations']:,.0f} installations were MNR (meter not recording)"
        out.append(line + ".")
    return out


__all__ = ["build_mis_report", "NotFound", "ReportUnderReview"]
