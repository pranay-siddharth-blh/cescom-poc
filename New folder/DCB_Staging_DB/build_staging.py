import openpyxl
import sqlite3
import json
import os
import re
import csv

# Resolved relative to this script's own location (not a machine-specific
# absolute path) so the pipeline is re-runnable after the project folder is
# moved/zipped/handed off to a different machine -- see CLAUDE.md "How to
# run this from scratch". Both are still override-able via env var so the
# standing reconciliation regression test (test_grand_total_reconciliation.py)
# can redirect output to a scratch directory without touching the real,
# checked-in staging DB.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.environ.get(
    "DCB_SRC_WORKBOOK",
    os.path.join(_PROJECT_ROOT, "HNPURA DIVISION DCB FY 2015 TO 2026.xlsx"),
)
OUT_DIR = os.environ.get(
    "DCB_STAGING_OUT_DIR",
    os.path.join(_PROJECT_ROOT, "DCB_Staging_DB"),
)
os.makedirs(OUT_DIR, exist_ok=True)

SHEET_ORDER = ["Division_Total", "Holenarasipura", "Arkalgud", "Hangarahalli",
               "Saligrama", "Ramanathapura", "Konanur", "Division_Office"]

TYPO_FIXES = [("ABtually", "Actually"), ("AverAFe", "Average"), ("VillAFe", "Village"), ("FAB", "FAC"),
              ("Collec tion", "Collection"), ("BhAFyajyothi", "Bhagyajyothi"), ("AFriculture", "Agriculture"),
              ("damAFes", "damages"), ("SewerAFe", "Sewerage"), ("ABcess", "Access"), ("TrABtion", "Traction")]


def fix_typos(s):
    if not isinstance(s, str):
        return s
    for bad, good in TYPO_FIXES:
        s = s.replace(bad, good)
    # Collapse incidental whitespace runs (the report pads with spaces for column
    # alignment) so the same real-world category doesn't end up as two distinct
    # dim_tariff_category rows purely over spacing.
    s = re.sub(r"\s+", " ", s.strip())
    return s


def classify_block(title):
    t = (title or "").lower()
    if "arkalgud" in t:
        return "Arkalgud"
    if "hangara" in t:
        return "Hangarahalli"
    if "ramanath" in t:
        return "Ramanathapura"
    if "konanur" in t:
        return "Konanur"
    if "saligrama" in t:
        return "Saligrama"
    if "division office" in t:
        return "Division_Office"
    if "holenarasip" in t:
        return "Holenarasipura" if "sub" in t else "Division_Total"
    return None


def ffill(row):
    out = []
    last = None
    for v in row:
        if v not in (None, ""):
            last = fix_typos(str(v).strip())
        out.append(last)
    return out


def ffill_h2(r2, r3, r4, width):
    """Forward-fill the top-level header row, but reset whenever a column is
    completely blank across r2/r3/r4 (a true spacer column in the source,
    not part of any merged group) -- otherwise the last real h2 value would
    wrongly bleed across the gap and duplicate onto the next real column."""
    out = []
    last = None
    for i in range(width):
        v2 = r2[i] if i < len(r2) else None
        v3 = r3[i] if i < len(r3) else None
        v4 = r4[i] if i < len(r4) else None
        if v2 in (None, "") and v3 in (None, "") and v4 in (None, ""):
            last = None
        elif v2 not in (None, ""):
            last = fix_typos(str(v2).strip())
        out.append(last)
    return out


def norm(s):
    if not s:
        return ""
    s = fix_typos(str(s))
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def group_aware_ffill(values, group_keys):
    """Forward-fill `values`, but reset whenever `group_keys` changes.
    Prevents a merged sub-header (e.g. 'KVA') from bleeding into unrelated
    columns once its parent header group (e.g. 'CONNECTED LOAD') has ended."""
    out = []
    last_val = None
    last_group = object()
    for v, g in zip(values, group_keys):
        if g != last_group:
            last_val = None
            last_group = g
        if v not in (None, ""):
            last_val = fix_typos(str(v).strip())
        out.append(last_val)
    return out


def find_real_width(r2, r3, r4, width, min_gap=4, min_real_col=15):
    """Some sheets embed an unrelated orphan mini-table far to the right --
    either separated by a run of fully-blank columns, or (2016-17/2017-18)
    immediately abutting the real table with only a single blank column
    between. The orphan table always repeats the block's own title text
    ('Division:'/'Sub-Division:'), which never legitimately appears in a
    real data column, so that text is an unambiguous second cutoff signal."""
    blank_run = 0
    for i in range(width):
        v2 = r2[i] if i < len(r2) else None
        v3 = r3[i] if i < len(r3) else None
        v4 = r4[i] if i < len(r4) else None
        if i >= min_real_col and isinstance(v2, str) and (
            "division" in v2.lower() or "consoldation" in v2.lower() or "section" in v2.lower()
        ):
            return i
        if v2 in (None, "") and v3 in (None, "") and v4 in (None, ""):
            blank_run += 1
            if blank_run >= min_gap:
                return i - min_gap + 1
        else:
            blank_run = 0
    return width


def build_column_map(r2, r3, r4, width):
    """Return list of (canonical_field_or_None, raw_label) per column index (0-based)."""
    width = find_real_width(r2, r3, r4, width)
    r2x = list(r2) + [None] * (width - len(r2))
    r3x = list(r3) + [None] * (width - len(r3))
    r4x = list(r4) + [None] * (width - len(r4))
    f2 = ffill_h2(r2x, r3x, r4x, width)
    f3 = group_aware_ffill(r3x, f2)

    result = []
    seen = {}
    for i in range(width):
        h2 = f2[i]
        h3 = f3[i] if i < len(f3) else None
        h4 = fix_typos(str(r4x[i]).strip()) if r4x[i] not in (None, "") else None
        # Two source layouts exist: some years put the Revenue/Interest/Tax/FAC
        # sub-label in row3 (h3), others put it in row4 (h4) instead -- `sub`
        # picks up whichever one actually carries it.
        sub = h3 or h4 or ""
        parts = [p for p in (h2, h3, h4) if p]
        raw_label = " | ".join(parts)
        field = canonical_field(norm(h2), norm(h3), norm(h4), norm(raw_label), norm(sub))
        if field:
            if field in seen:
                seen[field] += 1
                field = f"{field}_{seen[field]}"
            else:
                seen[field] = 1
        result.append((field, raw_label))
    return result, width


def canonical_field(h2, h3, h4, label, sub=""):
    if label == "sl.no":
        return None  # dropped; staging generates its own surrogate id
    # '%'/ratio columns are checked first: their label text often contains words
    # (e.g. "collection", "live install") that would otherwise false-match the
    # base field rules further below.
    if label.startswith("%") or "ratio of" in label or "per unit" in label or "per installation" in label:
        if "% of live installations" in label:
            return "pct_live_installations"
        if "% of billed installations" in label:
            return "pct_billed_installations"
        if "% of dc/mnr" in label:
            return "pct_dc_mnr"
        if "% of metered consump" in label:
            return "pct_metered_consumption"
        if "% of assessed consump" in label:
            return "pct_assessed_consumption"
        if "collection efficiency with adjustment" in label:
            return "pct_collection_efficiency_with_adj"
        if "collection efficiency with out adjustment" in label or "collection efficiency without adjustment" in label:
            return "pct_collection_efficiency_without_adj"
        if "ratio of arrears" in label:
            return "ratio_arrears_to_demand"
        if "only on demand per unit" in label:
            return "demand_per_unit_only"
        if "demand per unit" in label:
            return "demand_per_unit"
        if "collection per unit with adj" in label and "with out" not in label and "without" not in label:
            return "collection_per_unit_with_adj"
        if "collection per unit with out adj" in label or "collection per unit without adj" in label:
            return "collection_per_unit_without_adj"
        if "consump" in label and "per installation" in label:
            return "consumption_per_installation"
        if "% of recovery of average cost" in label:
            return "pct_recovery_of_avg_cost"
        return None
    if "tariff" in label and "category" in label:
        return "tariff_category"
    if label == "description":
        return "description"
    if "total no" in label and "install" in label:
        return "total_installations"
    if "live install" in label:
        return "live_installations"
    if "ctually billed" in label:
        return "billed_installations"
    if "connected load" in label:
        if "kw" in label:
            return "connected_load_kw"
        if "hp" in label:
            return "connected_load_hp"
        if "kva" in label:
            return "connected_load_kva"
        return "connected_load_other"
    if "dc/mnr" in label and "install" in label:
        return "dc_mnr_installations"
    if re.search(r"\bdc\b", label) and "install" in label and "mnr" not in label:
        return "dc_installations"
    if "mnr" in label and "install" in label:
        return "mnr_installations"
    if "security deposit" in label:
        return "security_deposit_held"
    if "approved average cost" in label:
        return "approved_avg_cost_of_supply"
    if "metered units" in label:
        return "consumption_metered"
    if "assessed consumption" in label or "assessed consump" in label:
        return "consumption_assessed"
    if "wheeled energy" in label:
        return "consumption_wheeled"
    if "consump" in label and "total" in label and "installation" not in label:
        return "consumption_total"
    if "opening balance total" in label:
        return "opening_balance_total"
    if h2 == "opening balance":
        if "p&g" in sub:
            return "ob_pg_surcharge"
        if "revenue" in sub:
            return "ob_revenue"
        if "interest" in sub:
            return "ob_interest"
        if "tax" in sub:
            return "ob_tax"
        if "fac" in sub:
            return "ob_fac"
        return "ob_other"
    if label == "fixed charges":
        return "demand_fixed_charges"
    if label == "others":
        return "demand_others"
    if "energy charges" in label and "demand" not in label:
        return "demand_energy_charges"
    if label == "fac":
        return "demand_fac_standalone"
    if label == "p&g surcharges for gok portion":
        return "demand_pg_surcharge_standalone"
    if "demand total" in label:
        return "demand_total"
    if h2 == "demand":
        if "p&g" in sub:
            return "demand_pg_surcharge"
        if "revenue" in sub:
            return "demand_revenue_subtotal"
        if "interest" in sub:
            return "demand_interest"
        if "tax" in sub:
            return "demand_tax"
        if "fac" in sub:
            return "demand_fac"
        return "demand_other"
    if h2 == "collection":
        chq = "chq" in h4 if h4 else False
        adj = "adjust" in h4 if h4 else False
        if "revenue total" in sub:
            return "collection_revenue_total"
        if "interest total" in sub:
            return "collection_interest_total"
        if "tax total" in sub:
            return "collection_tax_total"
        if "fac total" in sub:
            return "collection_fac_total"
        if sub and sub.startswith("total ("):
            return "collection_grand_total"
        if "revenue" in sub:
            return "collection_revenue_chq_cash" if chq else "collection_revenue_adjust" if adj else "collection_revenue_other"
        if "interest" in sub:
            return "collection_interest_chq_cash" if chq else "collection_interest_adjust" if adj else "collection_interest_other"
        if "tax" in sub:
            return "collection_tax_chq_cash" if chq else "collection_tax_adjust" if adj else "collection_tax_other"
        if "p&g" in sub:
            return "collection_pg_surcharge_chq_cash" if chq else "collection_pg_surcharge_adjust" if adj else "collection_pg_surcharge_total"
        if "fac" in sub:
            return "collection_fac_chq_cash" if chq else "collection_fac_adjust" if adj else "collection_fac_other"
        if not sub:
            return "collection_total"
    if "opening balance + demand" in label or "opening balance +demand" in label:
        if "revenue" in h3:
            return "ob_plus_demand_revenue"
        if "interest" in h3:
            return "ob_plus_demand_interest"
        if "tax" in h3:
            return "ob_plus_demand_tax"
        if "p&g" in h3:
            return "ob_plus_demand_pg_surcharge"
        if "fac" in h3:
            return "ob_plus_demand_fac"
        if h3 and "total" in h3:
            return "ob_plus_demand_total"
        return "ob_plus_demand_other"
    if h2 == "closing balance":
        if "revenue" in h3:
            return "closing_balance_revenue"
        if "interest" in h3:
            return "closing_balance_interest"
        if "tax" in h3:
            return "closing_balance_tax"
        if "p&g" in h3:
            return "closing_balance_pg_surcharge"
        if "fac" in h3:
            return "closing_balance_fac"
        if h3 and "total" in h3:
            return "closing_balance_total"
        return "closing_balance_other"
    if "revised arr" in label:
        return "revised_arr"
    if "diff" in label and "bild" in label and "live" in label:
        return "diff_billed_vs_live"
    if "diff" in label and "live" in label:
        return "diff_live_vs_total"
    return None


_COMPACT_WS_HYPHEN_RE = re.compile(r"[\s\-]+")
# "Total of <category>" (bare, no "Sub" prefix) is a DIFFERENT, distinct
# label from "Sub Total of <category>" -- confirmed live in the 2025-26
# sheet, which prints both "Sub Total of LT-1" (35,357,859.15) AND
# "Sub Total of LT-1 A" (168,063,582.60) as two separate per-group
# subtotals, THEN a bare "Total of LT-1" (203,421,441.74) that is exactly
# their sum -- the source's own explicit combined figure, not a duplicate.
# 2024-25 has the first two groups but never prints a combined "Total of
# LT-1" at all. Classified separately (combined_total, not sub_total) so a
# downstream category-total lookup can prefer the sheet's own explicit
# combined total when one exists, without ever silently manufacturing one
# by adding sibling subtotals together on its own.
# '\s*' (not '\s+') between "of" and the category text -- a real source
# variant is 'Sub Total ofIPP' with NO space at all before the category
# name (confirmed in the 2025-26 sheet).
_TOTAL_OF_RE = re.compile(r"\btotal\s+of\s*(.+)$", re.IGNORECASE)


def _compact(text):
    """Collapse whitespace/hyphen runs and lowercase, so row-type
    classification is robust to real spacing variants seen across years
    ('HT - TOTAL' vs 'HT-TOTAL' vs 'HT TOTAL', 'Sub  Total of...') instead
    of whack-a-mole-matching each variant as a separate literal string."""
    return _COMPACT_WS_HYPHEN_RE.sub("", text.lower())


def classify_row_type(sl_no, tariff_category, description):
    text = " ".join(str(x) for x in (sl_no, tariff_category, description) if x is not None)
    compact = _compact(text)
    if "grandtotal" in compact:
        return "grand_total"
    if "lt+httotal" in compact or "lt+ht" in compact:
        # "LT+HT - TOTAL" is LT+HT only, excluding Miscellaneous revenue codes --
        # a distinct, genuinely different figure from "GRAND TOTAL (LT+HT+Misc.)"
        # above, not a duplicate of it.
        return "lt_ht_total"
    if "httotal" in compact:
        return "ht_total"
    if "lttotal" in compact:
        return "lt_total"
    if "miscellaneous" in compact and "total" in compact:
        return "misc_total"
    m = _TOTAL_OF_RE.search(text)
    if m:
        prefix = text[: m.start()].lower()
        return "sub_total" if "sub" in prefix else "combined_total"
    if "total" in compact:
        return "other_total"
    return "line_item"


wb_src = openpyxl.load_workbook(SRC, read_only=True, data_only=True)

all_records = []
all_fields = set()
label_reference = []  # (fiscal_year, subdivision, col_position, raw_label, canonical_field)

for sheet_name in wb_src.sheetnames:
    ws = wb_src[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    n = len(rows)

    marker_idx = []
    for i, row in enumerate(rows):
        for cell in row[:4]:
            if isinstance(cell, str) and cell.strip().lower() == "sl.no":
                marker_idx.append(i)
                break

    blocks = []
    for k, m in enumerate(marker_idx):
        title_idx = m - 1
        title_row = rows[title_idx] if title_idx >= 0 else ()
        title_text = " ".join(str(c) for c in title_row[:4] if c is not None)
        name = classify_block(title_text)
        start = title_idx
        end = (marker_idx[k + 1] - 2) if k + 1 < len(marker_idx) else (n - 1)
        while end > start and all(c is None for c in rows[end]):
            end -= 1
        blocks.append((name, start, m, end))

    for name, start, slno_idx, end in blocks:
        if name is None:
            continue
        r2 = rows[slno_idx]
        r3 = rows[slno_idx + 1]
        r4 = rows[slno_idx + 2]
        data_start = slno_idx + 4  # skip index-number row + blank row

        raw_width = max(len(r2), len(r3), len(r4))
        colmap, width = build_column_map(r2, r3, r4, raw_width)

        for pos, (field, raw_label) in enumerate(colmap):
            label_reference.append((sheet_name, name, pos + 1, raw_label, field))

        last_tariff = None  # forward-fill state, reset per block
        for r_idx in range(data_start, end + 1):
            row = rows[r_idx]
            if row is None or all(c is None for c in row):
                continue
            row = list(row) + [None] * (width - len(row))
            sl_no = fix_typos(row[0]) if isinstance(row[0], str) else row[0]
            tariff_category = fix_typos(row[1]) if isinstance(row[1], str) else row[1]
            if isinstance(tariff_category, float):
                # Decimal-coded Misc. revenue categories (61.9047, etc.) can pick up
                # binary floating-point noise (61.904700000000005) depending on how
                # the source cell was entered/computed -- round so the same nominal
                # code doesn't fracture into multiple dim_tariff_category rows.
                tariff_category = round(tariff_category, 4)
            description = fix_typos(row[2]) if isinstance(row[2], str) else row[2]
            if sl_no is None and tariff_category is None and description is None and all(
                c is None for c in row[3:]
            ):
                continue

            row_type = classify_row_type(sl_no, tariff_category, description)
            tariff_category_filled = tariff_category
            if tariff_category is not None:
                last_tariff = tariff_category
            elif row_type == "line_item":
                # The source doesn't consistently re-print the tariff code on every
                # physical row of a multi-row entry. Distinguish three cases:
                if isinstance(description, str):
                    # a real continuation row (e.g. a wrapped sub-slab description)
                    # -- recover its tariff code from the row above.
                    tariff_category_filled = last_tariff
                elif isinstance(sl_no, str):
                    # free-text footnote/annotation row (e.g. "Applicable to areas
                    # under municipal corporations"), not a data row.
                    row_type = "annotation"
                else:
                    # near-empty row carrying one or two stray values spilled over
                    # from a merged cell in the row above (a numeric value can land
                    # in the description slot too) -- not a real line item.
                    row_type = "spillover_fragment"

            rec = {
                "fiscal_year": sheet_name,
                "subdivision": name,
                "row_type": row_type,
                "sl_no": sl_no,
                "tariff_category": tariff_category_filled,
                "tariff_category_forward_filled": "yes" if tariff_category_filled != tariff_category else "no",
                "description": description,
            }
            extras = {}
            for pos in range(3, width):
                field, raw_label = colmap[pos]
                val = row[pos]
                if val is None:
                    continue
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                if field:
                    rec[field] = val
                    all_fields.add(field)
                else:
                    extras[raw_label or f"col_{pos+1}"] = val
            rec["extra_metrics"] = json.dumps(extras, ensure_ascii=False) if extras else None
            all_records.append(rec)

    print(f"{sheet_name}: parsed {sum(1 for r in all_records if r['fiscal_year']==sheet_name)} rows "
          f"from {sum(1 for b in blocks if b[0])} blocks")

fixed_cols = ["fiscal_year", "subdivision", "row_type", "sl_no", "tariff_category",
              "tariff_category_forward_filled", "description"]
numeric_cols = sorted(all_fields)
all_cols = fixed_cols + numeric_cols + ["extra_metrics"]

print(f"\nTotal rows parsed: {len(all_records)}")
print(f"Total canonical numeric/text fields discovered: {len(numeric_cols)}")

# ---------- write staging_schema.sql (TEXT-only, per HANDOFF's staging philosophy) ----------
def sql_ident(name):
    return '"' + name.replace('"', '""') + '"'

ddl_lines = ["CREATE TABLE staging_dcb (", "  staging_row_id INTEGER PRIMARY KEY AUTOINCREMENT,"]
for c in all_cols:
    ddl_lines.append(f"  {sql_ident(c)} TEXT,")
ddl_lines[-1] = ddl_lines[-1].rstrip(",")
ddl_lines.append(");")
staging_schema_sql = "\n".join(ddl_lines)

with open(os.path.join(OUT_DIR, "staging_schema.sql"), "w", encoding="utf-8") as f:
    f.write(staging_schema_sql + "\n")

# ---------- build SQLite DB ----------
db_path = os.path.join(OUT_DIR, "dcb_staging.sqlite")
if os.path.exists(db_path):
    os.remove(db_path)
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute(staging_schema_sql)

placeholders = ", ".join("?" for _ in all_cols)
col_list = ", ".join(sql_ident(c) for c in all_cols)
insert_sql = f"INSERT INTO staging_dcb ({col_list}) VALUES ({placeholders})"

rows_to_insert = []
for rec in all_records:
    rows_to_insert.append(tuple(
        (str(rec[c]) if not isinstance(rec.get(c), str) and rec.get(c) is not None else rec.get(c))
        for c in all_cols
    ))
cur.executemany(insert_sql, rows_to_insert)

cur.execute("CREATE TABLE staging_column_reference (fiscal_year TEXT, subdivision TEXT, "
            "col_position INTEGER, raw_label TEXT, canonical_field TEXT)")
cur.executemany(
    "INSERT INTO staging_column_reference (fiscal_year, subdivision, col_position, raw_label, canonical_field) "
    "VALUES (?, ?, ?, ?, ?)",
    label_reference,
)

conn.commit()

cur.execute("SELECT COUNT(*) FROM staging_dcb")
total = cur.fetchone()[0]
cur.execute("SELECT row_type, COUNT(*) FROM staging_dcb GROUP BY row_type ORDER BY 2 DESC")
by_type = cur.fetchall()
cur.execute("SELECT fiscal_year, COUNT(*) FROM staging_dcb GROUP BY fiscal_year ORDER BY fiscal_year")
by_year = cur.fetchall()

print(f"\nLoaded into SQLite: {total} rows")
print("By row_type:", by_type)
print("By year:", by_year)

# ---------- also export as CSV for portability ----------
csv_path = os.path.join(OUT_DIR, "staging_dcb.csv")
with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(all_cols)
    for rec in all_records:
        writer.writerow([rec.get(c) for c in all_cols])

conn.close()
print(f"\nDone.\nDB: {db_path}\nSchema: {os.path.join(OUT_DIR, 'staging_schema.sql')}\nCSV: {csv_path}")
