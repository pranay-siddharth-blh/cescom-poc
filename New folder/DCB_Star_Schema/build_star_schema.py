import sqlite3
import os
import re
from collections import defaultdict

STAGING_DB = r"C:\Users\Dell\OneDrive\Desktop\New folder\DCB_Staging_DB\dcb_staging.sqlite"
OUT_DIR = r"C:\Users\Dell\OneDrive\Desktop\New folder\DCB_Star_Schema"
OUT_DB = os.path.join(OUT_DIR, "dcb_star_schema.sqlite")
os.makedirs(OUT_DIR, exist_ok=True)

SUBDIVISION_TYPE = {
    "Division_Total": "division",
    "Holenarasipura": "sub_division",
    "Arkalgud": "sub_division",
    "Hangarahalli": "sub_division",
    "Ramanathapura": "sub_division",
    "Saligrama": "sub_division",
    "Konanur": "section",
    "Division_Office": "office",
}

# Ratios/percentages/per-unit figures are NOT additive -- summing them across
# multiple source rows (e.g. several usage-slab rows under one tariff code)
# would produce a meaningless number. Kept only when a fact_dcb row is built
# from a single staging row; set to NULL when multiple rows were combined.
NON_ADDITIVE_PREFIXES = ("pct_", "ratio_", "collection_per_unit_", "demand_per_unit", "consumption_per_installation")


def is_additive(col):
    return not any(col.startswith(p) or col == p.rstrip("_") for p in NON_ADDITIVE_PREFIXES)


def to_num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


src = sqlite3.connect(STAGING_DB)
src.row_factory = sqlite3.Row
scur = src.cursor()

scur.execute("PRAGMA table_info(staging_dcb)")
all_staging_cols = [r[1] for r in scur.fetchall()]
FIXED = {"staging_row_id", "fiscal_year", "subdivision", "row_type", "sl_no",
         "tariff_category", "tariff_category_forward_filled", "description", "extra_metrics"}
numeric_cols = [c for c in all_staging_cols if c not in FIXED]

# ---------- dim_fiscal_year ----------
scur.execute("SELECT DISTINCT fiscal_year FROM staging_dcb ORDER BY fiscal_year")
years = [r[0] for r in scur.fetchall()]


def year_bounds(fy):
    m = re.match(r"(\d{4})-(\d{2})", fy)
    start = int(m.group(1))
    end = start + 1
    return start, end


# ---------- dim_subdivision ----------
scur.execute("SELECT DISTINCT subdivision FROM staging_dcb")
subdivisions = sorted(r[0] for r in scur.fetchall())

# ---------- dim_tariff_category ----------
scur.execute("""
    SELECT DISTINCT tariff_category, description FROM staging_dcb
    WHERE row_type='line_item' AND tariff_category IS NOT NULL
    ORDER BY tariff_category, description
""")
tariff_pairs = [(r[0], r[1]) for r in scur.fetchall()]

# ---------- output DB ----------
if os.path.exists(OUT_DB):
    os.remove(OUT_DB)
out = sqlite3.connect(OUT_DB)
ocur = out.cursor()

ocur.execute("""
CREATE TABLE dim_fiscal_year (
    fiscal_year TEXT PRIMARY KEY,
    start_year INTEGER,
    end_year INTEGER
)
""")
ocur.executemany(
    "INSERT INTO dim_fiscal_year (fiscal_year, start_year, end_year) VALUES (?, ?, ?)",
    [(fy, *year_bounds(fy)) for fy in years],
)

ocur.execute("""
CREATE TABLE dim_subdivision (
    subdivision_id INTEGER PRIMARY KEY,
    subdivision_name TEXT UNIQUE,
    subdivision_type TEXT
)
""")
subdivision_id = {}
for i, name in enumerate(subdivisions, start=1):
    ocur.execute(
        "INSERT INTO dim_subdivision (subdivision_id, subdivision_name, subdivision_type) VALUES (?, ?, ?)",
        (i, name, SUBDIVISION_TYPE.get(name, "unknown")),
    )
    subdivision_id[name] = i

ocur.execute("""
CREATE TABLE dim_tariff_category (
    tariff_category_id INTEGER PRIMARY KEY,
    tariff_code TEXT,
    description TEXT,
    UNIQUE(tariff_code, description)
)
""")
tariff_id = {}
for i, (code, desc) in enumerate(tariff_pairs, start=1):
    ocur.execute(
        "INSERT INTO dim_tariff_category (tariff_category_id, tariff_code, description) VALUES (?, ?, ?)",
        (i, code, desc),
    )
    tariff_id[(code, desc)] = i

# ---------- fact_dcb ----------
fact_cols_sql = ",\n    ".join(f'"{c}" REAL' for c in numeric_cols)
ocur.execute(f"""
CREATE TABLE fact_dcb (
    fact_id INTEGER PRIMARY KEY,
    fiscal_year TEXT REFERENCES dim_fiscal_year(fiscal_year),
    subdivision_id INTEGER REFERENCES dim_subdivision(subdivision_id),
    tariff_category_id INTEGER REFERENCES dim_tariff_category(tariff_category_id),
    source_row_count INTEGER,
    tariff_category_forward_filled TEXT,
    {fact_cols_sql}
)
""")

scur.execute("""
    SELECT fiscal_year, subdivision, tariff_category, description, tariff_category_forward_filled, *
    FROM staging_dcb WHERE row_type='line_item'
""")
groups = defaultdict(list)
for row in scur.fetchall():
    key = (row["fiscal_year"], row["subdivision"], row["tariff_category"], row["description"])
    groups[key].append(row)

fact_rows = []
for (fy, subdiv, code, desc), rows in groups.items():
    agg = {}
    for c in numeric_cols:
        vals = [to_num(r[c]) for r in rows]
        vals = [v for v in vals if v is not None]
        if not vals:
            agg[c] = None
        elif is_additive(c):
            agg[c] = sum(vals)
        else:
            agg[c] = vals[0] if len(rows) == 1 else None
    ff = "yes" if any(r["tariff_category_forward_filled"] == "yes" for r in rows) else "no"
    fact_rows.append((
        fy, subdivision_id[subdiv], tariff_id[(code, desc)], len(rows), ff,
        *[agg[c] for c in numeric_cols],
    ))

placeholders = ", ".join(["?"] * (5 + len(numeric_cols)))
col_list = ", ".join(['fiscal_year', 'subdivision_id', 'tariff_category_id', 'source_row_count',
                       'tariff_category_forward_filled'] + [f'"{c}"' for c in numeric_cols])
ocur.executemany(f"INSERT INTO fact_dcb ({col_list}) VALUES ({placeholders})", fact_rows)

# ---------- fact_dcb_rollups ----------
rollup_cols_sql = ",\n    ".join(f'"{c}" REAL' for c in numeric_cols)
ocur.execute(f"""
CREATE TABLE fact_dcb_rollups (
    rollup_id INTEGER PRIMARY KEY,
    fiscal_year TEXT REFERENCES dim_fiscal_year(fiscal_year),
    subdivision_id INTEGER REFERENCES dim_subdivision(subdivision_id),
    rollup_type TEXT,
    tariff_code_summarized TEXT,
    sl_no_text TEXT,
    {rollup_cols_sql}
)
""")


def extract_tariff_code(sl_no):
    if not sl_no:
        return None
    m = re.search(r"(?:sub\s*)?total\s+of\s+(.+)$", sl_no, re.IGNORECASE)
    return m.group(1).strip() if m else None


ROLLUP_TYPES = ("sub_total", "lt_total", "ht_total", "lt_ht_total", "misc_total", "grand_total")
scur.execute(f"""
    SELECT * FROM staging_dcb WHERE row_type IN ({",".join("?" * len(ROLLUP_TYPES))})
""", ROLLUP_TYPES)

rollup_rows = []
for row in scur.fetchall():
    vals = [to_num(row[c]) for c in numeric_cols]
    rollup_rows.append((
        row["fiscal_year"], subdivision_id[row["subdivision"]], row["row_type"],
        extract_tariff_code(row["sl_no"]), row["sl_no"], *vals,
    ))

col_list_r = ", ".join(["fiscal_year", "subdivision_id", "rollup_type", "tariff_code_summarized", "sl_no_text"]
                        + [f'"{c}"' for c in numeric_cols])
placeholders_r = ", ".join(["?"] * (5 + len(numeric_cols)))
ocur.executemany(f"INSERT INTO fact_dcb_rollups ({col_list_r}) VALUES ({placeholders_r})", rollup_rows)

# ---------- reconciliation_report ----------
ocur.execute("""
CREATE TABLE reconciliation_report (
    fiscal_year TEXT,
    subdivision_id INTEGER,
    metric TEXT,
    summed_from_fact_dcb REAL,
    printed_grand_total REAL,
    diff_pct REAL
)
""")

RECON_METRICS = ["total_installations", "demand_total", "collection_grand_total", "closing_balance_total"]
recon_rows = []
for fy in years:
    for subdiv, sid in subdivision_id.items():
        ocur.execute(
            "SELECT sl_no_text, " + ", ".join(RECON_METRICS) +
            " FROM fact_dcb_rollups WHERE fiscal_year=? AND subdivision_id=? AND rollup_type='grand_total'",
            (fy, sid),
        )
        gt = ocur.fetchone()
        if not gt:
            continue
        for i, metric in enumerate(RECON_METRICS, start=1):
            printed = gt[i]
            ocur.execute(
                f'SELECT SUM("{metric}") FROM fact_dcb WHERE fiscal_year=? AND subdivision_id=?',
                (fy, sid),
            )
            summed = ocur.fetchone()[0]
            if printed is None or summed is None:
                diff_pct = None
            elif printed == 0:
                diff_pct = None
            else:
                diff_pct = round((summed - printed) / printed * 100, 3)
            recon_rows.append((fy, sid, metric, summed, printed, diff_pct))

ocur.executemany(
    "INSERT INTO reconciliation_report (fiscal_year, subdivision_id, metric, summed_from_fact_dcb, printed_grand_total, diff_pct) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    recon_rows,
)

# ---------- convenience views ----------
ocur.execute("""
CREATE VIEW fact_dcb_subdivisions AS
SELECT f.* FROM fact_dcb f
JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE s.subdivision_type IN ('sub_division', 'section')
""")

ocur.execute("""
CREATE VIEW fact_dcb_grand_totals AS
SELECT r.* FROM fact_dcb_rollups r
WHERE r.rollup_type = 'grand_total'
""")

out.commit()

# ---------- summary ----------
print("dim_fiscal_year:", len(years))
print("dim_subdivision:", len(subdivisions))
print("dim_tariff_category:", len(tariff_pairs))
print("fact_dcb:", len(fact_rows))
print("fact_dcb_rollups:", len(rollup_rows))
print("reconciliation_report:", len(recon_rows))

ocur.execute("SELECT AVG(ABS(diff_pct)), MAX(ABS(diff_pct)) FROM reconciliation_report WHERE diff_pct IS NOT NULL")
print("reconciliation avg/max abs diff_pct:", ocur.fetchone())

src.close()
out.close()
print("\nDone:", OUT_DB)
