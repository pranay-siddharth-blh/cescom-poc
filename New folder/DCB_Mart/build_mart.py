"""
DCB mart (SQLite) -- query-ready, trap-free tables for the chatbot, built from the LOCKED star schema.

The star schema (../DCB_Star_Schema/dcb_star_schema.sqlite) is opened read-only (mode=ro) and never written;
none of the 4 locked folders is touched. Everything this script writes lands in this folder:

  dcb_mart.sqlite            mart_*      the only new tables the chatbot's SQL may read (plus the star tables)
                             semantic_*  table / column docs, metric registry, capabilities, known issues
                             ledger_*    build run, build checks, family-vs-printed-subtotal matches
  mart_semantic_layer.json   the same docs + entity lists, exported for the app (the app only reads it)
  ../dcb-ai-app/integrity_manifest.json   SHA-256 of every file the app only reads (tests prove it never writes)

What the mart fixes by construction (each was a real trap for model-written SQL on the star schema):
  * Division_Total vs sub-divisions: division-wide and per-sub-division figures are separate tables, so
    nothing can add the division's own rows to its sub-divisions (and KI-02: they don't add up anyway).
  * Tariff families: every slab carries family_code, and family totals are summed from line items. They
    reconcile to the printed grand total and equal the printed family subtotal wherever that one is right
    (the printed 'Sub Total of LT-5' is 0 up to 2023-24 -- KI-05).
  * DC/MNR installations: one comparable column across the 2018-19 layout change.
  * Ratios: pre-computed per row, with the sums beside them so any re-aggregation stays correct.

Usage (from anywhere):  python build_mart.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
PROJECT = HERE.parent
STAR = PROJECT / "DCB_Star_Schema" / "dcb_star_schema.sqlite"
SEMANTIC_STAR = PROJECT / "DCB_Semantic_Layer" / "semantic_layer.json"
EXAMPLES_STAR = PROJECT / "DCB_Semantic_Layer" / "example_queries.sql"
META = HERE / "metadata"
OUT_DB = HERE / "dcb_mart.sqlite"
OUT_JSON = HERE / "mart_semantic_layer.json"
MART_EXAMPLES = HERE / "example_queries.sql"
MANIFEST = PROJECT / "dcb-ai-app" / "integrity_manifest.json"

# Additive / stock metric columns carried from the star schema (same names there).
BASE_METRICS = [
    "total_installations", "live_installations", "billed_installations",
    "connected_load_kw", "connected_load_hp", "connected_load_kva",
    "consumption_metered", "consumption_assessed", "consumption_total",
    "opening_balance_total",
    "demand_fixed_charges", "demand_energy_charges", "demand_others", "demand_revenue_subtotal", "demand_fac",
    "demand_tax", "demand_interest", "demand_pg_surcharge", "demand_total",
    "collection_revenue_total", "collection_fac_total", "collection_tax_total", "collection_interest_total",
    "collection_pg_surcharge_total", "collection_grand_total",
    "closing_balance_total", "security_deposit_held",
    # 2026-10-01: DC and MNR as printed in their own columns (2018-19 on), and FPPCA (FAC) opening / closing balance
    "dc_installations", "mnr_installations", "ob_fac", "closing_balance_fac",
]
DC_MNR = ("CASE WHEN {p}dc_installations IS NULL AND {p}mnr_installations IS NULL AND {p}dc_mnr_installations IS NULL "
          "THEN NULL ELSE COALESCE({p}dc_installations, 0) + COALESCE({p}mnr_installations, 0) "
          "+ COALESCE({p}dc_mnr_installations, 0) END")
# dc_column_installations: the sheet's own "DC" column -- the combined DC/MNR column up to 2017-18, the DC column alone
# from 2018-19 -- which is what its printed "% of DC/MNR" divides (reproduced on every grand-total row).
DC_COLUMN = "COALESCE({p}dc_installations, {p}dc_mnr_installations)"
# Collections split as the sheet prints them: 'Chq/cash' (money received) and 'Adjust' (book adjustments -- for LT-1
# domestic and LT-4(a) IP sets that is the Government subsidy (confirmed by CESC): Gruha Jyothi, and the IP-set subsidy the
# tariff booklet says GoK pays), summed over the five heads (revenue, FPPCA, tax, interest, P&G surcharge).
COLLECTION_HEADS = ("revenue", "fac", "tax", "interest", "pg_surcharge")
CASH = " + ".join(f"COALESCE({{p}}collection_{h}_chq_cash, 0)" for h in COLLECTION_HEADS)
ADJUST = " + ".join(f"COALESCE({{p}}collection_{h}_adjust, 0)" for h in COLLECTION_HEADS)
ALL_METRICS = BASE_METRICS + ["dc_mnr_installations", "dc_column_installations", "uncollected_demand",
                              "collection_cash_total", "collection_adjust_total"]
MISC_FLAG = "m.family_code = 'MISC'"   # line-item tables join tmap AS m
DERIVED = {
    "collection_efficiency_pct": "collection_grand_total * 100.0 / NULLIF(demand_total, 0)",
    "live_installation_pct": "live_installations * 100.0 / NULLIF(total_installations, 0)",
    "arrears_to_demand_ratio": "closing_balance_total * 1.0 / NULLIF(demand_total, 0)",
    "demand_per_unit_rs": "demand_total * 1.0 / NULLIF(consumption_total, 0)",
    "demand_per_installation_rs": "demand_total * 1.0 / NULLIF(total_installations, 0)",
    # The sheet's own DCB percentages -- each reproduces the printed figure on every grand-total row (build check below).
    "billed_installation_pct": "billed_installations * 100.0 / NULLIF(total_installations, 0)",
    "dc_mnr_installation_pct": "dc_column_installations * 100.0 / NULLIF(total_installations, 0)",
    "mnr_installation_pct": "mnr_installations * 100.0 / NULLIF(total_installations, 0)",
    "metered_consumption_pct": "consumption_metered * 100.0 / NULLIF(consumption_total, 0)",
    "assessed_consumption_pct": "consumption_assessed * 100.0 / NULLIF(consumption_total, 0)",
    # FPPCA (printed as FAC) and per-consumer figures
    "fac_share_of_demand_pct": "demand_fac * 100.0 / NULLIF(demand_total, 0)",
    "fac_collection_efficiency_pct": "collection_fac_total * 100.0 / NULLIF(demand_fac, 0)",
    "fac_per_unit_rs": "demand_fac * 1.0 / NULLIF(consumption_total, 0)",
    "collection_per_installation_rs": "collection_grand_total * 1.0 / NULLIF(total_installations, 0)",
    "arrears_per_installation_rs": "closing_balance_total * 1.0 / NULLIF(total_installations, 0)",
    # 2026-10-01 (second pass): the sheet's own formulas printed in its headers / consolidation block
    #   '% of Collection Efficiency without Adjustment (34-Adj./30)'  -> cash collection / demand
    #   consolidation 'Billing Efficiency (4/3)' = installations actually billed / live installations
    #   AT&C loss = 100 - billing efficiency x collection efficiency (the standard formula, with the sheet's billing efficiency)
    "collection_efficiency_without_adj_pct": "collection_cash_total * 100.0 / NULLIF(demand_total, 0)",
    "adjustment_share_pct": "collection_adjust_total * 100.0 / NULLIF(collection_grand_total, 0)",
    "billing_efficiency_pct": "billed_installations * 100.0 / NULLIF(live_installations, 0)",
    "atc_loss_pct": ("100.0 - (billed_installations * 1.0 / NULLIF(live_installations, 0)) "
                     "* (collection_grand_total * 100.0 / NULLIF(demand_total, 0))"),
    "energy_charge_per_unit_paise": "demand_energy_charges * 100.0 / NULLIF(consumption_total, 0)",
}
# mart percentage column -> the percentage the sheet prints on its own rows (star fact_dcb_rollups)
PRINTED_PCT = {
    "collection_efficiency_pct": "pct_collection_efficiency_with_adj",
    "live_installation_pct": "pct_live_installations",
    "billed_installation_pct": "pct_billed_installations",
    "dc_mnr_installation_pct": "pct_dc_mnr",
    "metered_consumption_pct": "pct_metered_consumption",
    "assessed_consumption_pct": "pct_assessed_consumption",
    "arrears_to_demand_ratio": "ratio_arrears_to_demand",
}
FAMILY_ORDER = ["LT-1", "LT-2", "LT-3", "LT-4", "LT-5", "LT-6", "LT-7", "LT IPP", "AUXILIARY CONSUMPTION",
                "HT-1", "HT-2", "HT-3", "HT-4", "HT-5", "HT-6", "HT-7", "HT IPP", "MISC"]
# Labels follow the CESC tariff booklet FY2025-26 to FY2027-28 (metadata/tariff_booklet.json); the family is the code
# as printed in each year's sheet, so the notes say where the 2024-25 restructuring moved slabs between families.
FAMILY_LABELS = {
    "LT-1": "Domestic: homes incl. Bhagyajyothi / Kutirajyothi (urban domestic printed as LT-2(a) up to 2023-24)",
    "LT-2": "Private educational institutions & hospitals (up to 2023-24 also domestic lighting & heating, LT-2(a))",
    "LT-3": "Commercial LT-3(a) and hoardings LT-3(b)",
    "LT-4": "Irrigation pump sets: LT-4(a) up to 10 HP (Govt subsidy), LT-4(b) above 10 HP, LT-4(c) nurseries & plantations",
    "LT-5": "Industrial (LT), incl. MSMEs",
    "LT-6": "Water supply LT-6(a), public lighting LT-6(b), EV charging LT-6(c)",
    "LT-7": "Temporary supply (hoardings printed as LT-7(b) up to 2023-24)",
    "LT IPP": "LT independent power producers (special account)",
    "AUXILIARY CONSUMPTION": "Auxiliary consumption (special account)",
    "HT-1": "HT water supply & sewerage",
    "HT-2": "HT industrial 2(a), commercial 2(b), hospitals & educational institutions 2(c)",
    "HT-3": "HT private lift irrigation (Govt lift irrigation printed as HT-3(a)(i) up to 2023-24)",
    "HT-4": "HT residential apartments & colonies",
    "HT-5": "HT temporary supply (67 HP and above)",
    "HT-6": "HT irrigation & agricultural farms, plantations (printed as HT-3b up to 2023-24)",
    "HT-7": "HT Government lift irrigation schemes",
    "HT IPP": "HT independent power producers (special account)",
    "MISC": "Miscellaneous revenue (fees, penalties, rentals, receipts)",
}
TOL_REL, TOL_ABS = 0.01, 2.0   # same tolerance as DCB_Star_Schema/test_grand_total_reconciliation.py


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def family_of(code: str) -> tuple[str, str]:
    """(family_code, voltage_class) for a printed tariff code."""
    c = (code or "").strip()
    m = re.match(r"^(LT|HT)\s*-\s*(\d)", c, re.I)
    if m:
        return f"{m[1].upper()}-{m[2]}", m[1].upper()
    if re.match(r"^6\d\.", c):
        return "MISC", "MISC"
    up = c.upper()
    if up in ("HT IPP",):
        return "HT IPP", "HT"
    if up in ("LT IPP",):
        return "LT IPP", "LT"
    if up.startswith("AUXILIARY"):
        return "AUXILIARY CONSUMPTION", "LT"   # sits inside the printed LT total
    raise ValueError(f"tariff code {code!r} has no family rule -- add one before building")


def source_note(code: str, description: str) -> str | None:
    """KI-01: the 2024-25+ sheets print the BMA / urban-local-body lighting slabs (LT-2(a) before) under LT-1."""
    if (code or "").strip().upper() == "LT-1" and (description or "").lower().startswith("ltng"):
        return "LT-1 A"
    return None


def metric_select(prefix: str, agg: bool, printed_total: bool = False, misc_flag: str | None = None) -> str:
    """printed_total: the row is a printed GRAND TOTAL; KI-11 -- one of them (Holenarasipura 2023-24) leaves
    consumption_total blank while its metered and assessed parts are printed, so the total is taken from them.
    misc_flag: SQL condition marking a miscellaneous-revenue line (61.xxxx / 62.xxxx). Those lines' 'Adjust' columns are
    not part of their printed collection total (KI-17), so their whole collection counts as cash and no adjustment;
    on a printed GRAND TOTAL the same is done with the printed misc_total row."""
    cols = []
    for m in BASE_METRICS:
        if printed_total and m == "consumption_total":
            cols.append(f"COALESCE({prefix}consumption_total, {prefix}consumption_metered + {prefix}consumption_assessed) AS {m}")
            continue
        cols.append(f"SUM({prefix}{m}) AS {m}" if agg else f"{prefix}{m} AS {m}")
    dc = DC_MNR.format(p=prefix)
    cols.append(f"SUM({dc}) AS dc_mnr_installations" if agg else f"{dc} AS dc_mnr_installations")
    dcc = DC_COLUMN.format(p=prefix)
    cols.append(f"SUM({dcc}) AS dc_column_installations" if agg else f"{dcc} AS dc_column_installations")
    unc = f"COALESCE({prefix}demand_total, 0) - COALESCE({prefix}collection_grand_total, 0)"
    cols.append(f"SUM({unc}) AS uncollected_demand" if agg else f"{unc} AS uncollected_demand")
    cash, adj, grand = CASH.format(p=prefix), ADJUST.format(p=prefix), f"COALESCE({prefix}collection_grand_total, 0)"
    if misc_flag:
        cash = f"(CASE WHEN {misc_flag} THEN {grand} ELSE {cash} END)"
        adj = f"(CASE WHEN {misc_flag} THEN 0 ELSE {adj} END)"
    elif printed_total:
        misc = lambda e: (f"COALESCE((SELECT {e} FROM star.fact_dcb_rollups x WHERE x.subdivision_id = {prefix}subdivision_id "
                          f"AND x.fiscal_year = {prefix}fiscal_year AND x.rollup_type = 'misc_total'), 0)")
        cash = f"({cash} - {misc(CASH.format(p='x.'))} + {misc('COALESCE(x.collection_grand_total, 0)')})"
        adj = f"({adj} - {misc(ADJUST.format(p='x.'))})"
    for name, e in (("collection_cash_total", cash), ("collection_adjust_total", adj)):
        cols.append(f"SUM({e}) AS {name}" if agg else f"{e} AS {name}")
    return ",\n       ".join(cols)


def load_booklet() -> dict:
    return json.loads((META / "tariff_booklet.json").read_text(encoding="utf-8"))


def booklet_of(code: str, description: str, rules: list[dict]) -> str | None:
    """The tariff booklet category (current structure) a printed slab belongs to; None for IPP / auxiliary / misc."""
    c, d = (code or "").strip(), (description or "")
    for r in rules:
        if re.search(r["match"], c, re.I) and (not r.get("desc") or re.search(r["desc"], d, re.I)):
            return r["booklet"]
    return None


def booklet_rate_rows(booklet: dict) -> list[tuple]:
    """(booklet_code, fiscal_year, component, value, unit) for every printed rate."""
    out = []
    comps = (("fixed", "fixed_charge", "fixed_unit"), ("demand", "demand_charge", "demand_unit"),
             ("ht_demand", "ht_demand_charge", "ht_demand_unit"), ("energy_paise", "energy_charge", None),
             ("energy_led_paise", "energy_charge_led", None), ("cdt_paise", "cdt", None),
             ("cdt_fixed_component_paise", "cdt_fixed_component", None),
             ("cdt_variable_component_paise", "cdt_variable_component", None))
    for cat in booklet["categories"]:
        for fy, r in cat["rates"].items():
            for key, comp, unit_key in comps:
                if key in r:
                    unit = ("Rs " + r[unit_key]) if unit_key else "paise per kWh"
                    out.append((cat["code"], fy, comp, float(r[key]), unit))
            db = r.get("demand_based")
            if db:
                out.append((cat["code"], fy, "demand_based_fixed_charge", float(db["fixed"]), "Rs " + db["fixed_unit"] + " (" + db["applies"] + ")"))
                out.append((cat["code"], fy, "demand_based_energy_charge", float(db["energy_paise"]), "paise per kWh (" + db["applies"] + ")"))
        for v in cat.get("variants", []):
            for fy, r in v["rates"].items():
                out.append((cat["code"], fy, f"variant: {v['name']} demand_charge", float(r["demand"]), "Rs per kVA per month of billing demand"))
                out.append((cat["code"], fy, f"variant: {v['name']} energy_charge", float(r["energy_paise"]), "paise per kWh"))
    return out


def booklet_energy_rate(booklet: dict, code: str, fy: str) -> float | None:
    """The booklet's energy rate (paise/kWh) used for the data-vs-booklet check: the CDT for LT-4(a), else the energy charge."""
    for cat in booklet["categories"]:
        if cat["code"] == code:
            r = cat["rates"].get(fy) or {}
            return r.get("cdt_paise", r.get("energy_paise"))
    return None


def add_derived(con: sqlite3.Connection, table: str) -> None:
    for col, expr in DERIVED.items():
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} REAL")
        con.execute(f"UPDATE {table} SET {col} = {expr}")


# ============================================================================= mart
def build_mart(con: sqlite3.Connection, booklet: dict) -> None:
    con.executescript("""
    CREATE TABLE mart_dim_fiscal_year AS
    SELECT fiscal_year, start_year, end_year,
           ROW_NUMBER() OVER (ORDER BY fiscal_year) AS fy_order,
           CASE WHEN fiscal_year = (SELECT MAX(fiscal_year) FROM star.dim_fiscal_year) THEN 1 ELSE 0 END AS is_latest
    FROM star.dim_fiscal_year ORDER BY fiscal_year;
    CREATE UNIQUE INDEX ux_mart_dim_fiscal_year ON mart_dim_fiscal_year (fiscal_year);

    CREATE TABLE mart_dim_subdivision AS
    SELECT s.subdivision_name, s.subdivision_type,
           CASE WHEN s.subdivision_type IN ('sub_division', 'section') THEN 1 ELSE 0 END AS is_geographic,
           MIN(r.fiscal_year) AS first_fiscal_year, MAX(r.fiscal_year) AS last_fiscal_year,
           COUNT(DISTINCT r.fiscal_year) AS years_with_data,
           CASE WHEN MAX(r.fiscal_year) = (SELECT MAX(fiscal_year) FROM star.dim_fiscal_year) THEN 1 ELSE 0 END AS is_active
    FROM star.dim_subdivision s
    JOIN star.fact_dcb_rollups r ON r.subdivision_id = s.subdivision_id AND r.rollup_type = 'grand_total'
    WHERE s.subdivision_type <> 'division'
    GROUP BY s.subdivision_name, s.subdivision_type
    ORDER BY is_geographic DESC, s.subdivision_name;
    CREATE UNIQUE INDEX ux_mart_dim_subdivision ON mart_dim_subdivision (subdivision_name);
    """)

    # tariff map (computed in Python: the family rules are regexes)
    con.execute("CREATE TEMP TABLE tmap (tariff_category_id INTEGER PRIMARY KEY, family_code TEXT, voltage_class TEXT, "
                "source_note TEXT, booklet_code TEXT)")
    rules = booklet["data_code_map"]["rules"]
    rows = con.execute("SELECT tariff_category_id, tariff_code, description FROM star.dim_tariff_category").fetchall()
    con.executemany("INSERT INTO tmap VALUES (?, ?, ?, ?, ?)",
                    [(i, *family_of(code), source_note(code, desc), booklet_of(code, desc, rules)) for i, code, desc in rows])
    con.execute("CREATE TEMP TABLE fam (family_code TEXT PRIMARY KEY, family_order INTEGER, family_label TEXT)")
    con.executemany("INSERT INTO fam VALUES (?, ?, ?)", [(f, i + 1, FAMILY_LABELS[f]) for i, f in enumerate(FAMILY_ORDER)])

    # the tariff booklet itself (metadata/tariff_booklet.json): categories, every printed rate, the general rules
    con.executescript("""
    CREATE TABLE mart_tariff_booklet_category (booklet_code TEXT PRIMARY KEY, booklet_order INTEGER, family_code TEXT,
        voltage_class TEXT, booklet_name TEXT, booklet_short TEXT, applies_to TEXT, booklet_page INTEGER);
    CREATE TABLE mart_tariff_booklet_rate (booklet_code TEXT, fiscal_year TEXT, component TEXT, value REAL, unit TEXT);
    CREATE TABLE mart_tariff_booklet_rule (rule_key TEXT PRIMARY KEY, title TEXT, text TEXT, booklet_page INTEGER);
    """)
    con.executemany("INSERT INTO mart_tariff_booklet_category VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(c["code"], i + 1, c["family"], c["voltage"], c["name"], c["short"], c["applies_to"], c["page"])
                     for i, c in enumerate(booklet["categories"])])
    con.executemany("INSERT INTO mart_tariff_booklet_rate VALUES (?, ?, ?, ?, ?)", booklet_rate_rows(booklet))
    con.executemany("INSERT INTO mart_tariff_booklet_rule VALUES (?, ?, ?, ?)",
                    [(r["key"], r["title"], r["text"], r["page"]) for r in booklet["rules"]])

    con.executescript("""
    CREATE TABLE mart_dim_tariff AS
    SELECT t.tariff_code, t.description, m.family_code, m.voltage_class, fam.family_label,
           CASE WHEN m.family_code = 'MISC' THEN 1 ELSE 0 END AS is_misc,
           m.booklet_code, bc.booklet_name,
           MIN(f.fiscal_year) AS first_fiscal_year, MAX(f.fiscal_year) AS last_fiscal_year
    FROM star.dim_tariff_category t
    JOIN tmap m ON m.tariff_category_id = t.tariff_category_id
    JOIN fam ON fam.family_code = m.family_code
    LEFT JOIN mart_tariff_booklet_category bc ON bc.booklet_code = m.booklet_code
    LEFT JOIN star.fact_dcb f ON f.tariff_category_id = t.tariff_category_id
    GROUP BY t.tariff_category_id
    ORDER BY fam.family_order, t.tariff_code, t.description;

    CREATE TABLE mart_dim_tariff_family AS
    SELECT fam.family_code, m.voltage_class, fam.family_label, fam.family_order,
           MIN(f.fiscal_year) AS first_fiscal_year, MAX(f.fiscal_year) AS last_fiscal_year
    FROM fam
    JOIN tmap m ON m.family_code = fam.family_code
    JOIN star.fact_dcb f ON f.tariff_category_id = m.tariff_category_id
    GROUP BY fam.family_code ORDER BY fam.family_order;
    CREATE UNIQUE INDEX ux_mart_dim_tariff_family ON mart_dim_tariff_family (family_code);
    """)

    printed = lambda rt, col: (f"(SELECT x.{col} FROM star.fact_dcb_rollups x WHERE x.subdivision_id = r.subdivision_id "
                               f"AND x.fiscal_year = r.fiscal_year AND x.rollup_type = '{rt}')")
    splits = ",\n       ".join([
        f"{printed('lt_total', 'demand_total')} AS lt_demand_total",
        f"{printed('ht_total', 'demand_total')} AS ht_demand_total",
        f"{printed('misc_total', 'demand_total')} AS misc_demand_total",
        f"{printed('lt_total', 'collection_grand_total')} AS lt_collection_total",
        f"{printed('ht_total', 'collection_grand_total')} AS ht_collection_total",
        f"{printed('misc_total', 'collection_grand_total')} AS misc_collection_total",
    ])
    con.executescript(f"""
    CREATE TABLE mart_fact_division_year AS
    SELECT r.fiscal_year,
       {metric_select('r.', False, printed_total=True)},
       {splits}
    FROM star.fact_dcb_rollups r JOIN star.dim_subdivision s ON s.subdivision_id = r.subdivision_id
    WHERE r.rollup_type = 'grand_total' AND s.subdivision_type = 'division'
    ORDER BY r.fiscal_year;

    CREATE TABLE mart_fact_subdivision_year AS
    SELECT r.fiscal_year, s.subdivision_name, s.subdivision_type,
       {metric_select('r.', False, printed_total=True)},
       {splits}
    FROM star.fact_dcb_rollups r JOIN star.dim_subdivision s ON s.subdivision_id = r.subdivision_id
    WHERE r.rollup_type = 'grand_total' AND s.subdivision_type IN ('sub_division', 'section')
    ORDER BY r.fiscal_year, s.subdivision_name;

    CREATE TABLE mart_fact_family_division_year AS
    SELECT f.fiscal_year, m.family_code, m.voltage_class, fam.family_label, fam.family_order,
       COUNT(*) AS slab_count,
       {metric_select('f.', True, misc_flag=MISC_FLAG)}
    FROM star.fact_dcb f
    JOIN star.dim_subdivision s ON s.subdivision_id = f.subdivision_id
    JOIN tmap m ON m.tariff_category_id = f.tariff_category_id
    JOIN fam ON fam.family_code = m.family_code
    WHERE s.subdivision_type = 'division'
    GROUP BY f.fiscal_year, m.family_code
    ORDER BY f.fiscal_year, fam.family_order;

    CREATE TABLE mart_fact_family_subdivision_year AS
    SELECT f.fiscal_year, s.subdivision_name, s.subdivision_type, m.family_code, m.voltage_class, fam.family_label,
       fam.family_order, COUNT(*) AS slab_count,
       {metric_select('f.', True, misc_flag=MISC_FLAG)}
    FROM star.fact_dcb f
    JOIN star.dim_subdivision s ON s.subdivision_id = f.subdivision_id
    JOIN tmap m ON m.tariff_category_id = f.tariff_category_id
    JOIN fam ON fam.family_code = m.family_code
    WHERE s.subdivision_type IN ('sub_division', 'section')
    GROUP BY f.fiscal_year, s.subdivision_name, m.family_code
    ORDER BY f.fiscal_year, s.subdivision_name, fam.family_order;

    CREATE TABLE mart_fact_booklet_division_year AS
    SELECT f.fiscal_year, m.booklet_code, bc.booklet_name, bc.booklet_order, m.voltage_class,
       COUNT(*) AS slab_count,
       {metric_select('f.', True, misc_flag=MISC_FLAG)}
    FROM star.fact_dcb f
    JOIN star.dim_subdivision s ON s.subdivision_id = f.subdivision_id
    JOIN tmap m ON m.tariff_category_id = f.tariff_category_id
    JOIN mart_tariff_booklet_category bc ON bc.booklet_code = m.booklet_code
    WHERE s.subdivision_type = 'division'
    GROUP BY f.fiscal_year, m.booklet_code
    ORDER BY f.fiscal_year, bc.booklet_order;

    CREATE TABLE mart_fact_booklet_subdivision_year AS
    SELECT f.fiscal_year, s.subdivision_name, s.subdivision_type, m.booklet_code, bc.booklet_name, bc.booklet_order,
       m.voltage_class, COUNT(*) AS slab_count,
       {metric_select('f.', True, misc_flag=MISC_FLAG)}
    FROM star.fact_dcb f
    JOIN star.dim_subdivision s ON s.subdivision_id = f.subdivision_id
    JOIN tmap m ON m.tariff_category_id = f.tariff_category_id
    JOIN mart_tariff_booklet_category bc ON bc.booklet_code = m.booklet_code
    WHERE s.subdivision_type IN ('sub_division', 'section')
    GROUP BY f.fiscal_year, s.subdivision_name, m.booklet_code
    ORDER BY f.fiscal_year, s.subdivision_name, bc.booklet_order;

    CREATE TABLE mart_fact_slab_division_year AS
    SELECT f.fiscal_year, t.tariff_code, t.description, m.family_code, m.voltage_class, m.source_note, m.booklet_code,
       {metric_select('f.', False, misc_flag=MISC_FLAG)}
    FROM star.fact_dcb f
    JOIN star.dim_subdivision s ON s.subdivision_id = f.subdivision_id
    JOIN star.dim_tariff_category t ON t.tariff_category_id = f.tariff_category_id
    JOIN tmap m ON m.tariff_category_id = f.tariff_category_id
    WHERE s.subdivision_type = 'division'
    ORDER BY f.fiscal_year, t.tariff_code, t.description;

    CREATE TABLE mart_fact_slab_subdivision_year AS
    SELECT f.fiscal_year, s.subdivision_name, s.subdivision_type, t.tariff_code, t.description, m.family_code,
       m.voltage_class, m.source_note, m.booklet_code,
       {metric_select('f.', False, misc_flag=MISC_FLAG)}
    FROM star.fact_dcb f
    JOIN star.dim_subdivision s ON s.subdivision_id = f.subdivision_id
    JOIN star.dim_tariff_category t ON t.tariff_category_id = f.tariff_category_id
    JOIN tmap m ON m.tariff_category_id = f.tariff_category_id
    WHERE s.subdivision_type IN ('sub_division', 'section')
    ORDER BY f.fiscal_year, s.subdivision_name, t.tariff_code, t.description;
    """)
    for t in ("mart_fact_division_year", "mart_fact_subdivision_year", "mart_fact_family_division_year",
              "mart_fact_family_subdivision_year", "mart_fact_slab_division_year", "mart_fact_slab_subdivision_year",
              "mart_fact_booklet_division_year", "mart_fact_booklet_subdivision_year"):
        add_derived(con, t)

    # data vs booklet: the energy rate actually billed (energy charges / units) next to the booklet's printed rate,
    # for every year the data and the booklet both cover
    con.execute("CREATE TABLE mart_tariff_rate_check (fiscal_year TEXT, booklet_code TEXT, booklet_name TEXT, "
                "consumption_total REAL, demand_energy_charges REAL, data_energy_paise REAL, booklet_energy_paise REAL, "
                "difference_paise REAL, difference_pct REAL)")
    years = set(booklet["source"]["fiscal_years"])
    for fy, code, name, units, ec, paise in con.execute(
            "SELECT fiscal_year, booklet_code, booklet_name, consumption_total, demand_energy_charges, energy_charge_per_unit_paise "
            "FROM mart_fact_booklet_division_year ORDER BY fiscal_year, booklet_order").fetchall():
        if fy not in years:
            continue
        book = booklet_energy_rate(booklet, code, fy)
        diff = None if (paise is None or book is None) else paise - book
        con.execute("INSERT INTO mart_tariff_rate_check VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (fy, code, name, units, ec, paise, book, diff, None if diff is None or not book else diff * 100.0 / book))
    con.executescript("""
    CREATE UNIQUE INDEX ux_mart_book_div ON mart_fact_booklet_division_year (fiscal_year, booklet_code);
    CREATE UNIQUE INDEX ux_mart_book_sub ON mart_fact_booklet_subdivision_year (fiscal_year, subdivision_name, booklet_code);
    CREATE UNIQUE INDEX ux_mart_div_year ON mart_fact_division_year (fiscal_year);
    CREATE UNIQUE INDEX ux_mart_sub_year ON mart_fact_subdivision_year (fiscal_year, subdivision_name);
    CREATE UNIQUE INDEX ux_mart_fam_div ON mart_fact_family_division_year (fiscal_year, family_code);
    CREATE UNIQUE INDEX ux_mart_fam_sub ON mart_fact_family_subdivision_year (fiscal_year, subdivision_name, family_code);
    CREATE INDEX ix_mart_slab_div ON mart_fact_slab_division_year (fiscal_year, tariff_code);
    CREATE INDEX ix_mart_slab_sub ON mart_fact_slab_subdivision_year (fiscal_year, subdivision_name, tariff_code);
    """)


# ============================================================================= semantic
def build_semantic(con: sqlite3.Connection, docs: dict, registry: dict, caps: dict, issues: dict) -> list[str]:
    """Docs, metric registry, capabilities and issues as tables. Returns undocumented columns (build check)."""
    con.executescript("""
    CREATE TABLE semantic_table_doc (table_name TEXT PRIMARY KEY, grain TEXT, doc TEXT, row_count INTEGER);
    CREATE TABLE semantic_column_doc (table_name TEXT, column_name TEXT, data_type TEXT, unit TEXT, kind TEXT, doc TEXT,
        PRIMARY KEY (table_name, column_name));
    CREATE TABLE semantic_metric (metric_key TEXT PRIMARY KEY, label TEXT, unit TEXT, kind TEXT, precision INTEGER,
        synonyms TEXT, expr TEXT);
    CREATE TABLE semantic_capability (topic TEXT PRIMARY KEY, status TEXT, mart_tables TEXT, keywords TEXT, note TEXT);
    CREATE TABLE semantic_known_issue (issue_id TEXT PRIMARY KEY, severity TEXT, status TEXT, title TEXT, detail TEXT,
        caveat TEXT, affects TEXT);
    """)
    missing = []
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'mart_%' ORDER BY name")]
    for t in tables:
        td = docs["tables"].get(t)
        if not td:
            missing.append(f"{t} (table)")
            td = {"grain": "", "doc": ""}
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        con.execute("INSERT INTO semantic_table_doc VALUES (?, ?, ?, ?)", (t, td["grain"], td["doc"], n))
        for _, col, typ, *_ in con.execute(f"PRAGMA table_info({t})").fetchall():
            cd = docs["columns"].get(col)
            if not cd:
                missing.append(f"{t}.{col}")
                cd = {"unit": None, "kind": None, "doc": ""}
            con.execute("INSERT INTO semantic_column_doc VALUES (?, ?, ?, ?, ?, ?)",
                        (t, col, typ or "", cd.get("unit"), cd.get("kind"), cd.get("doc")))
    for m in registry["metrics"]:
        con.execute("INSERT INTO semantic_metric VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (m["key"], m["label"], m["unit"], m["kind"], m["precision"], json.dumps(m["synonyms"]), json.dumps(m["expr"])))
    for c in caps["topics"]:
        con.execute("INSERT INTO semantic_capability VALUES (?, ?, ?, ?, ?)",
                    (c["topic"], c["status"], ",".join(c.get("mart_tables", [])), json.dumps(c.get("keywords", [])), c["note"]))
    for i in issues["issues"]:
        con.execute("INSERT INTO semantic_known_issue VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (i["id"], i["severity"], i["status"], i["title"], i["detail"], i["caveat"], json.dumps(i["affects"])))
    return missing


# ============================================================================= checks
def _close(a, b) -> bool:
    a, b = a or 0.0, b or 0.0
    return abs(a - b) <= max(TOL_ABS, TOL_REL * max(abs(a), abs(b)))


def run_checks(con: sqlite3.Connection, missing_docs: list[str], caps: dict) -> list[tuple[str, str, str]]:
    checks: list[tuple[str, str, str]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append((name, "PASS" if ok else "FAIL", detail))

    flows = [m for m in ALL_METRICS if m not in ("security_deposit_held",)]
    # 1-2. family rows add up to the printed grand total (division; every sub-division)
    for fam_t, year_t, keys in (("mart_fact_family_division_year", "mart_fact_division_year", ["fiscal_year"]),
                                ("mart_fact_family_subdivision_year", "mart_fact_subdivision_year", ["fiscal_year", "subdivision_name"])):
        k = ", ".join(keys)
        bad = []
        sums = {tuple(r[:len(keys)]): r[len(keys):] for r in con.execute(
            f"SELECT {k}, " + ", ".join(f"SUM({m})" for m in flows) + f" FROM {fam_t} GROUP BY {k}")}
        for r in con.execute(f"SELECT {k}, " + ", ".join(flows) + f" FROM {year_t}"):
            key, vals = tuple(r[:len(keys)]), r[len(keys):]
            got = sums.get(key)
            if got is None:
                if any(v for v in vals):
                    bad.append(f"{key}: no family rows")
                continue
            for m, a, b in zip(flows, got, vals):
                if not _close(a, b):
                    bad.append(f"{key} {m}: families {a} vs printed {b}")
        add(f"families add up to printed grand total ({year_t})", not bad, "; ".join(bad[:5]) or f"{len(sums)} groups x {len(flows)} metrics")

    # 3. sub-divisions vs division: installations and LT+HT agree exactly; the gap is misc revenue only (KI-02)
    bad = []
    for fy, d_inst, s_inst, d_lt, s_lt, d_ht, s_ht in con.execute("""
        SELECT d.fiscal_year, d.total_installations, SUM(s.total_installations), d.lt_demand_total, SUM(s.lt_demand_total),
               d.ht_demand_total, SUM(s.ht_demand_total)
        FROM mart_fact_division_year d JOIN mart_fact_subdivision_year s ON s.fiscal_year = d.fiscal_year
        GROUP BY d.fiscal_year"""):
        if fy >= "2016-17" and not _close(d_inst, s_inst):
            bad.append(f"{fy} installations {d_inst} vs {s_inst}")
        if not _close(d_lt, s_lt) or not _close(d_ht, s_ht):
            bad.append(f"{fy} LT/HT {d_lt}/{d_ht} vs {s_lt}/{s_ht}")
    add("sub-divisions add up to the division for installations and LT/HT demand", not bad, "; ".join(bad) or "11 years")

    # 4. golden figures (hand-verified in CLAUDE.md sections 5 and 11, and against the printed rows here)
    one = lambda sql, p=(): con.execute(sql, p).fetchone()[0]
    golden = [
        ("Arkalgud 2024-25 collection efficiency 93.62%",
         round(one("SELECT SUM(collection_grand_total) * 100.0 / SUM(demand_total) FROM mart_fact_family_subdivision_year "
                   "WHERE subdivision_name = 'Arkalgud' AND fiscal_year = '2024-25'"), 2), 93.62),
        ("Konanur 2025-26 collection efficiency 97.46%",
         round(one("SELECT collection_efficiency_pct FROM mart_fact_subdivision_year WHERE subdivision_name = 'Konanur' AND fiscal_year = '2025-26'"), 2), 97.46),
        ("HT-2 division demand 2015-16 = 23,399,034.75",
         round(one("SELECT demand_total FROM mart_fact_family_division_year WHERE family_code = 'HT-2' AND fiscal_year = '2015-16'"), 2), 23399034.75),
        ("LT-1 division demand 2025-26 = 'Total of LT-1' 542,708,386.67",
         round(one("SELECT demand_total FROM mart_fact_family_division_year WHERE family_code = 'LT-1' AND fiscal_year = '2025-26'"), 2),
         round(one("SELECT demand_total FROM star.fact_dcb_rollups r JOIN star.dim_subdivision s USING (subdivision_id) "
                   "WHERE s.subdivision_type = 'division' AND r.fiscal_year = '2025-26' AND r.rollup_type = 'combined_total' "
                   "AND r.tariff_code_summarized = 'LT-1'"), 2)),
        ("LT-5 division demand 2015-16 = 'Sub Total of LT-5 (a+b)'",
         round(one("SELECT demand_total FROM mart_fact_family_division_year WHERE family_code = 'LT-5' AND fiscal_year = '2015-16'"), 2),
         round(one("SELECT demand_total FROM star.fact_dcb_rollups r JOIN star.dim_subdivision s USING (subdivision_id) "
                   "WHERE s.subdivision_type = 'division' AND r.fiscal_year = '2015-16' AND r.tariff_code_summarized = 'LT-5 (a+b)'"), 2)),
        ("division 2024-25 demand = printed GRAND TOTAL",
         round(one("SELECT demand_total FROM mart_fact_division_year WHERE fiscal_year = '2024-25'"), 2),
         round(one("SELECT demand_total FROM star.fact_dcb_grand_totals g JOIN star.dim_subdivision s USING (subdivision_id) "
                   "WHERE s.subdivision_type = 'division' AND g.fiscal_year = '2024-25'"), 2)),
    ]
    for name, got, want in golden:
        add(f"golden: {name}", got == want, f"got {got}, expected {want}")

    # 5. every family total matches a printed family subtotal wherever the sheet prints one (known exceptions listed)
    known = {("2024-25", "LT-1")}   # KI-01: the sheet prints LT-1 and LT-1 A separately that year, no combined row
    bad, matched = [], 0
    printed = {}
    for fy, sname, stype, rt, code, v in con.execute("""
        SELECT r.fiscal_year, s.subdivision_name, s.subdivision_type, r.rollup_type, r.tariff_code_summarized, r.demand_total
        FROM star.fact_dcb_rollups r JOIN star.dim_subdivision s USING (subdivision_id)
        WHERE r.rollup_type IN ('sub_total', 'combined_total') AND r.tariff_code_summarized IS NOT NULL"""):
        scope = "Division" if stype == "division" else sname
        printed.setdefault((fy, scope), []).append((code, v))
    fams = [(fy, "Division", f, v) for fy, f, v in con.execute("SELECT fiscal_year, family_code, demand_total FROM mart_fact_family_division_year")]
    fams += list(con.execute("SELECT fiscal_year, subdivision_name, family_code, demand_total FROM mart_fact_family_subdivision_year"))
    con.execute("CREATE TABLE ledger_family_printed_match (fiscal_year TEXT, scope TEXT, family_code TEXT, demand_total REAL, "
                "printed_label TEXT, printed_demand REAL, status TEXT)")
    for fy, scope, f, v in fams:
        norm = re.sub(r"[^a-z0-9]", "", f.lower())
        cands = [(c, pv) for c, pv in printed.get((fy, scope), [])
                 if re.sub(r"[^a-z0-9]", "", c.lower()) in (norm, norm + "ab")]
        if not cands:
            con.execute("INSERT INTO ledger_family_printed_match VALUES (?, ?, ?, ?, NULL, NULL, 'no printed subtotal')", (fy, scope, f, v))
            continue
        hit = next(((c, pv) for c, pv in cands if _close(v, pv)), None)
        status = "match" if hit else ("known exception" if (fy, f) in known else "MISMATCH")
        c, pv = hit or cands[0]
        con.execute("INSERT INTO ledger_family_printed_match VALUES (?, ?, ?, ?, ?, ?, ?)", (fy, scope, f, v, c, pv, status))
        if status == "match":
            matched += 1
        elif status == "MISMATCH":
            bad.append(f"{fy} {scope} {f}: {v} vs {cands}")
    add("family totals equal the printed family subtotal wherever printed", not bad,
        "; ".join(bad[:5]) or f"{matched} matches, exceptions only KI-01 (LT-1 2024-25)")

    add("sub-division facts hold no Division_Total / Division_Office rows",
        one("SELECT COUNT(*) FROM mart_fact_subdivision_year WHERE subdivision_type NOT IN ('sub_division', 'section')") == 0
        and one("SELECT COUNT(*) FROM mart_fact_family_subdivision_year WHERE subdivision_name IN ('Division_Total', 'Division_Office')") == 0,
        "checked mart_fact_subdivision_year and mart_fact_family_subdivision_year")
    # 6. the DCB percentages shown by the app reproduce the sheet's own printed ones (division + every unit, every year)
    bad, n = [], 0
    for t, extra in (("mart_fact_division_year", "s.subdivision_type = 'division'"),
                     ("mart_fact_subdivision_year", "s.subdivision_name = m.subdivision_name")):
        sel = ", ".join(f"m.{a}, r.{b}" for a, b in PRINTED_PCT.items())
        sql = (f"SELECT m.fiscal_year, {sel} FROM {t} m JOIN star.fact_dcb_rollups r ON r.fiscal_year = m.fiscal_year "
               f"AND r.rollup_type = 'grand_total' JOIN star.dim_subdivision s ON s.subdivision_id = r.subdivision_id AND {extra}")
        for row in con.execute(sql):
            fy, vals = row[0], row[1:]
            for i, (a, b) in enumerate(PRINTED_PCT.items()):
                mine, printed_v = vals[2 * i], vals[2 * i + 1]
                if printed_v is None or mine is None:
                    continue
                n += 1
                if abs(mine - printed_v) > max(0.02, abs(printed_v) * 0.001):
                    bad.append(f"{t} {fy} {a}: {mine:.3f} vs printed {printed_v:.3f}")
    add("DCB percentages reproduce the sheet's printed percentages", not bad, "; ".join(bad[:5]) or f"{n} printed values matched")
    add("DC and MNR printed separately from 2018-19",
        one("SELECT COUNT(*) FROM mart_fact_division_year WHERE fiscal_year >= '2018-19' AND (dc_installations IS NULL OR mnr_installations IS NULL)") == 0,
        "dc_installations and mnr_installations present 2018-19 to the latest year")
    add("DC/MNR installations present in every year",
        one("SELECT COUNT(*) FROM mart_fact_division_year WHERE dc_mnr_installations IS NULL") == 0, "11 years")
    # 7. cash + adjustments = the printed collection grand total (the subsidy view depends on it)
    bad = []
    for t in ("mart_fact_division_year", "mart_fact_subdivision_year", "mart_fact_family_division_year"):
        for row in con.execute(f"SELECT fiscal_year, collection_cash_total, collection_adjust_total, collection_grand_total FROM {t}"):
            if not _close((row[1] or 0) + (row[2] or 0), row[3]):
                bad.append(f"{t} {row[0]}: {row[1]} + {row[2]} vs {row[3]}")
    add("cash + adjustment collections = printed collection total", not bad, "; ".join(bad[:5]) or "division, sub-division and family rows")
    # 8. tariff booklet: every billed LT/HT slab maps to a booklet category, and the booklet rows add up
    unmapped = [f"{c} / {d}" for c, d in con.execute(
        "SELECT tariff_code, description FROM mart_dim_tariff WHERE booklet_code IS NULL "
        "AND family_code NOT IN ('MISC', 'LT IPP', 'HT IPP', 'AUXILIARY CONSUMPTION')")]
    add("every LT/HT slab maps to a tariff booklet category", not unmapped, "; ".join(unmapped[:5]) or
        f"{one('SELECT COUNT(*) FROM mart_dim_tariff WHERE booklet_code IS NOT NULL')} slabs mapped")
    bad = []
    for fy, fam_sum, book_sum in con.execute("""
        SELECT f.fiscal_year, SUM(f.demand_total),
               (SELECT SUM(b.demand_total) FROM mart_fact_booklet_division_year b WHERE b.fiscal_year = f.fiscal_year)
        FROM mart_fact_family_division_year f
        WHERE f.family_code NOT IN ('MISC', 'LT IPP', 'HT IPP', 'AUXILIARY CONSUMPTION') GROUP BY f.fiscal_year"""):
        if not _close(fam_sum, book_sum):
            bad.append(f"{fy}: families {fam_sum} vs booklet categories {book_sum}")
    add("booklet categories add up to the LT + HT families", not bad, "; ".join(bad[:5]) or "11 years")
    # 9. the transcribed booklet rates agree with what was actually billed (energy charges / units) where volumes are material
    bad, n = [], 0
    for fy, code, diff_pct, units in con.execute(
            "SELECT fiscal_year, booklet_code, difference_pct, consumption_total FROM mart_tariff_rate_check"):
        if diff_pct is None or (units or 0) < 100000:
            continue
        n += 1
        if abs(diff_pct) > 6.0:
            bad.append(f"{fy} {code}: {diff_pct:+.1f}%")
    add("booklet energy rates agree with the billed rate (within 6%)", not bad, "; ".join(bad) or f"{n} categories checked")
    add("no negative installation counts",
        one("SELECT COUNT(*) FROM mart_fact_slab_subdivision_year WHERE total_installations < 0 OR live_installations < 0") == 0, "")
    add("every mart table and column is documented", not missing_docs, ", ".join(missing_docs[:10]) or "all documented")
    bad = [c["topic"] for c in caps["topics"] if c["status"] == "loaded"
           and any(one(f"SELECT COUNT(*) FROM {t}") == 0 for t in c["mart_tables"])]
    add("every 'loaded' capability has rows", not bad, ", ".join(bad) or "")
    return checks


# ============================================================================= export
def export_json(con: sqlite3.Connection, docs: dict, registry: dict, caps: dict, issues: dict, checks) -> dict:
    tables = {}
    for t, grain, doc, n in con.execute("SELECT table_name, grain, doc, row_count FROM semantic_table_doc ORDER BY table_name"):
        cols = {}
        for c, typ, unit, kind, cdoc in con.execute(
                "SELECT column_name, data_type, unit, kind, doc FROM semantic_column_doc WHERE table_name = ? ORDER BY rowid", (t,)):
            cols[c] = {"type": typ, "unit": unit, "kind": kind, "doc": cdoc}
        tables[t] = {"grain": grain, "doc": doc, "row_count": n, "columns": cols}
    fys = [r[0] for r in con.execute("SELECT fiscal_year FROM mart_dim_fiscal_year ORDER BY fiscal_year")]
    subs = [dict(zip(("name", "type", "is_geographic", "first_fy", "last_fy", "is_active"), r)) for r in con.execute(
        "SELECT subdivision_name, subdivision_type, is_geographic, first_fiscal_year, last_fiscal_year, is_active FROM mart_dim_subdivision")]
    fams = [dict(zip(("code", "voltage", "label", "order", "first_fy", "last_fy"), r)) for r in con.execute(
        "SELECT family_code, voltage_class, family_label, family_order, first_fiscal_year, last_fiscal_year FROM mart_dim_tariff_family ORDER BY family_order")]
    codes = sorted({r[0] for r in con.execute("SELECT tariff_code FROM mart_dim_tariff")})
    return {
        "_purpose": "Generated by DCB_Mart/build_mart.py from dcb_mart.sqlite and DCB_Mart/metadata/*.json. Never edit by hand.",
        "built_at": dt.datetime.now().isoformat(timespec="seconds"),
        "data_period": {"first_fiscal_year": fys[0], "latest_fiscal_year": fys[-1], "fiscal_years": fys},
        "tables": tables,
        "metrics": registry["metrics"],
        "non_averageable_columns": registry["non_averageable_columns"],
        "non_summable_columns": registry["non_summable_columns"],
        "capabilities": caps["topics"],
        "known_issues": issues["issues"],
        "rules": docs["rules"],
        "entities": {"fiscal_years": fys, "subdivisions": subs, "families": fams, "tariff_codes": codes},
        "build_checks": [{"check": n, "status": s, "detail": d} for n, s, d in checks],
    }


def write_manifest() -> None:
    kb = META / "knowledge_base.yaml"            # app/knowledge.py reads it (general and Kannada answers)
    tb = META / "tariff_booklet.json"            # app/tariff_booklet.py reads it (tariff rates, categories, rules)
    files = [STAR, SEMANTIC_STAR, EXAMPLES_STAR, OUT_DB, OUT_JSON] + [f for f in (MART_EXAMPLES, kb, tb) if f.exists()]
    manifest = {"_purpose": "SHA-256 of the files the chatbot only reads; written by DCB_Mart/build_mart.py. "
                            "tests/test_integrity.py fails if any of them changes.",
                "files": {f.relative_to(PROJECT).as_posix(): sha256(f) for f in files}}
    MANIFEST.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")


def main() -> int:
    t0 = time.time()
    if not STAR.exists():
        print(f"star schema not found at {STAR}", file=sys.stderr)
        return 2
    star_hash_before = sha256(STAR)
    docs = json.loads((META / "semantic_docs.json").read_text(encoding="utf-8"))
    registry = json.loads((META / "metric_registry.json").read_text(encoding="utf-8"))
    caps = json.loads((META / "capability_manifest.json").read_text(encoding="utf-8"))
    issues = json.loads((META / "known_issues.json").read_text(encoding="utf-8"))
    booklet = load_booklet()

    tmp = OUT_DB.with_suffix(".building.sqlite")
    if tmp.exists():
        tmp.unlink()
    # ATTACH with a read-only URI needs a URI-enabled connection
    con = sqlite3.connect(f"file:{tmp.as_posix()}", uri=True)
    con.execute(f"ATTACH DATABASE 'file:{STAR.as_posix()}?mode=ro' AS star")
    log("mart")
    build_mart(con, booklet)
    log("semantic")
    missing = build_semantic(con, docs, registry, caps, issues)
    log("checks")
    checks = run_checks(con, missing, caps)
    con.executescript("""
    CREATE TABLE ledger_build_check (check_name TEXT, status TEXT, detail TEXT);
    CREATE TABLE ledger_build_run (built_at TEXT, star_sha256 TEXT, seconds REAL, checks_passed INTEGER, checks_failed INTEGER);
    """)
    con.executemany("INSERT INTO ledger_build_check VALUES (?, ?, ?)", checks)
    failed = [c for c in checks if c[1] != "PASS"]
    con.execute("INSERT INTO ledger_build_run VALUES (?, ?, ?, ?, ?)",
                (dt.datetime.now().isoformat(timespec="seconds"), star_hash_before, round(time.time() - t0, 2),
                 len(checks) - len(failed), len(failed)))
    data = export_json(con, docs, registry, caps, issues, checks)
    con.commit()
    con.execute("DETACH DATABASE star")
    con.close()

    for name, status, detail in checks:
        print(f"  {status}  {name}" + (f"  -- {detail}" if detail else ""))
    if sha256(STAR) != star_hash_before:
        print("the star schema changed during the build -- this must never happen", file=sys.stderr)
        return 3
    if failed:
        tmp.unlink()
        print(f"{len(failed)} check(s) failed; dcb_mart.sqlite left unchanged", file=sys.stderr)
        return 1
    try:
        os.replace(tmp, OUT_DB)
    except PermissionError:
        tmp.unlink()
        print("dcb_mart.sqlite is open in another process (the app server has it open read-only): stop the server, "
              "then run this build again. Nothing was changed.", file=sys.stderr)
        return 4
    OUT_JSON.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    write_manifest()
    log(f"done: {len(checks)} checks passed, {OUT_DB.name} + {OUT_JSON.name} + integrity manifest written "
        f"({time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
