# HNPURA DCB → NL2SQL — Project Handoff (v2)

**This supersedes the previous HANDOFF.md in this folder.** That version
described an ETL/CSV pipeline targeting Neon Postgres — those files never
actually existed in this folder (only this handoff doc and the raw
source workbook did). This version reflects what was actually built,
verified, and is present on disk right now, as a **local SQLite**
pipeline instead.

Read this top to bottom once; after that it's a reference doc. If you're
picking this project back up in a new session (with Claude or otherwise,
in this folder or a different one), paste this whole file in as context.

---

## 1. What this project is

Source file: `HNPURA DIVISION DCB FY 2015 TO 2026.xlsx` — a CESCOM
(Karnataka electricity board) "Provision DCB STATEMENT" for Holenarasipura
Division, one sheet per fiscal year, 2015-16 through 2025-26 (11 sheets).
DCB = Demand-Collection-Balance: for every tariff category in every
sub-division, how many installations there are, what was billed (demand),
what was collected, and what's still outstanding (balance).

**Goal:** turn this into a database an NL2SQL chatbot can query (FastAPI +
Gemini + Neon Postgres, matching the **bescom-ai** project's architecture
— this one is **dcb-ai-app**). The chatbot itself is **not built yet** —
everything below is the data pipeline leading up to it.

## 2. Current state — 4 folders, each a pipeline stage

```
New folder/
├── HANDOFF.md                              (this file)
├── HNPURA DIVISION DCB FY 2015 TO 2026.xlsx   (raw source, untouched)
├── DCB_Segregated_By_SubDivision/          (stage 1: raw split, human-readable)
├── DCB_Staging_DB/                         (stage 2: flat staging table, SQLite)
├── DCB_Star_Schema/                        (stage 3: typed star schema, SQLite)
└── DCB_Semantic_Layer/                     (stage 4: business-meaning layer for the chatbot's prompt)
```

Each folder has its own `README.md` with full details, caveats, and the
script that built it. Summary of each:

### Stage 1 — `DCB_Segregated_By_SubDivision/`
11 `.xlsx` files (one per fiscal year), each split into separate sheets
per sub-division/section, exactly as printed in the source — no data
reshaping. Useful for a human to eyeball the original report contents
without wading through one giant 1000+-row sheet.

### Stage 2 — `DCB_Staging_DB/dcb_staging.sqlite`
The raw report parsed into one flat table, `staging_dcb` (11,712 rows,
86 columns, all TEXT — a deliberately "dumb" landing zone, no casting).
Every row is tagged with a `row_type` (`line_item`, `sub_total`,
`lt_ht_total`, `misc_total`, `grand_total`, `annotation`,
`spillover_fragment`) so real data is never accidentally mixed with the
report's own printed subtotals. A second table, `staging_column_reference`,
documents which raw Excel column (per year/sub-division) produced every
named field.

### Stage 3 — `DCB_Star_Schema/dcb_star_schema.sqlite`
The typed star schema, matching the design v1 of this handoff originally
called for:

| Table | Rows | What it is |
|---|---|---|
| `dim_fiscal_year` | 11 | One per year, with integer `start_year`/`end_year` |
| `dim_subdivision` | 8 | `Division_Total` (division-wide rollup), 5 real sub-divisions, `Konanur` (a section), `Saligrama` (closed after 2017-18), `Division_Office` (2020-21 to 2023-24 only) |
| `dim_tariff_category` | 139 | Every distinct (tariff_code, description) pair billed anywhere in the 11 years |
| `fact_dcb` | 5,488 | One row per (year, sub-division, tariff_category, description), `REAL`-typed. `SUM()` over this table is always safe |
| `fact_dcb_rollups` | 2,232 | The report's own printed Sub Total / LT-TOTAL / HT-TOTAL / LT+HT-TOTAL / MISC-Total / GRAND TOTAL rows, kept separate |
| `reconciliation_report` | 292 | QA table: `fact_dcb` summed vs. the report's own printed grand total, per block per metric |

Plus 2 views: `fact_dcb_subdivisions` (real sub-divisions/sections only,
excludes the division-wide rollup) and `fact_dcb_grand_totals`.

**Reconciliation result: 0.0% average and 0.0% max deviation** across all
292 checks — `fact_dcb` summed per block matches the report's own printed
GRAND TOTAL exactly, everywhere. (Two of the bugs found and fixed while
building this — see §4 — are precisely why this is tighter than the
original v1 handoff's caveat about a structural gap between line items
and the printed total.)

### Stage 4 — `DCB_Semantic_Layer/` ← **the deliverable**
The business-meaning layer on top of the star schema — domain vocabulary
(DCB, LT/HT, DC/MNR, FAC, ARR, the OB→Demand→Collection→CB chain), a
tariff-code glossary (what LT-1 vs HT-2 vs the decimal misc-revenue codes
actually mean), the rules that prevent a wrong/double-counted query
(§6 of this handoff, expanded), common derived-metric formulas, and 10
worked NL question → SQL pairs, each actually run and spot-checked
against the database. `semantic_layer.json` is the structured version
meant to be loaded straight into the chatbot's prompt; `semantic_layer.md`
is the same content for a person to read. This is what §6 below
originally asked for.

## 3. How to open/query it

No server needed — SQLite is a single file. Easiest: **DB Browser for
SQLite** (free GUI, already installed on this machine via winget —
`winget install DBBrowserForSQLite.DBBrowserForSQLite` if picking this up
elsewhere). Open `DCB_Star_Schema/dcb_star_schema.sqlite`, use the
"Execute SQL" tab. Example:

```sql
SELECT f.fiscal_year, t.tariff_code, t.description,
       f.total_installations, f.demand_total, f.collection_grand_total
FROM fact_dcb f
JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id
WHERE s.subdivision_name = 'Arkalgud' AND f.fiscal_year = '2024-25'
ORDER BY f.demand_total DESC;
```

Or from Python: `sqlite3.connect(r"...\DCB_Star_Schema\dcb_star_schema.sqlite")`.

To regenerate any stage from scratch (e.g. after the source workbook gets
a new year appended): re-run `DCB_Staging_DB/build_staging.py` first,
then `DCB_Star_Schema/build_star_schema.py` (it reads from the staging
DB). Both are plain Python + `openpyxl`/`sqlite3`, no extra setup.

## 4. Bugs found and fixed along the way (so you don't re-litigate them)

The source report's raw structure is genuinely messy — multi-level merged
headers, inconsistent field repetition, embedded orphan sub-tables, and
real character-corruption bugs. All of the below were found by building
and cross-checking this pipeline, not assumed:

1. **3-level header structure differs across two eras.** 2015-16/16-17/17-18
   put the Revenue/Interest/Tax/FAC sub-label one header row lower than
   2018-19 onward. Handled by matching on combined header text, not fixed
   column position.
2. **Forward-fill bled sub-headers across unrelated columns** wherever a
   merged header cell was blank (e.g. "KVA" leaking into DC Installations,
   Security Deposit, etc.). Fixed with a group-aware forward-fill that
   resets at the parent header boundary.
3. **An unrelated orphan mini-table is embedded far to the right** in some
   sheets (columns ~72-134 depending on year) — a compressed
   Lakh-denominated monthly summary, repeating the block's own title.
   Excluded entirely (detected via the title text reappearing, or a run
   of blank columns).
4. **A genuinely blank spacer column** was mistaken for a duplicate "FAC"
   column in 2015-16-era sheets — fixed by resetting forward-fill on
   columns blank across all 3 header rows, not just the immediate one.
5. **Two previously-undocumented character-corruption bugs**, beyond the
   one v1 of this handoff knew about (`Village`→`VillAFe`): the actual
   pattern is lowercase `ag`→`AF` (also hits `Average`, `Sewerage`,
   `Agriculture`, `damages`, `Bhagyajyothi`) and a separate `C`→`B` swap
   (`Actually`→`ABtually`, `FAC`→`FAB`), plus a source-wide `Collec tion`
   (stray space) typo. All fixed via an explicit typo list, not a blanket
   substitution (too risky for false positives).
6. **Tariff code wasn't consistently re-printed** on every physical row of
   a multi-row entry (e.g. a wrapped sub-slab description). ~1,046 real
   data rows had a blank `tariff_category` as a result — recovered via
   forward-fill, with a `tariff_category_forward_filled` flag marking
   exactly which rows were inferred vs. printed.
7. **Row-type misclassification conflated two different rollup rows**:
   "LT+HT - TOTAL" (LT+HT only) and "GRAND TOTAL (LT+HT+Misc.)" (the true
   grand total) were both being bucketed as `grand_total`, making it look
   like every block had a duplicate/inconsistent grand total. They're
   distinct, legitimate rows — `misc_total` is the difference between
   them, exactly.
8. **A GRAND TOTAL row's own merged-cell continuation row** (same
   phenomenon as the source's normal multi-row line items) landed a stray
   number in a `description` field for one row in 2025-26, which fooled
   the "is this a real continuation" check into forward-filling a fake
   line item — inflating that year's installation count by ~90%. Fixed by
   requiring `description` be actual text, not a number, before treating
   a row as a real continuation.
9. **Whitespace-padding and residual corruption both created spurious
   duplicate tariff categories** (e.g. `LT-1` looked like it had 13
   description variants; really 11). Fixed by collapsing whitespace and
   sweeping for every remaining instance of bug #5's pattern, not just
   the first one found.
10. **2 more instances of bug #5's pattern surfaced while compiling the
    tariff-code glossary** for `DCB_Semantic_Layer/` (`ABcess`→`Access`,
    `TrABtion`→`Traction`, the same `ac`→`AB` substitution as
    `Actually`→`ABtually`). Fixed the same way.
11. **Decimal misc-revenue tariff codes could pick up binary
    floating-point noise** (`61.9047` printed as `61.904700000000005` in
    one year's cell), fracturing one real code into two
    `dim_tariff_category` rows. Fixed by rounding decimal tariff codes to
    4 places before storing.

## 5. Known caveats (real, left as-is — not bugs)

1. **`Saligrama`** genuinely closed as a sub-division after 2017-18 — real
   fact, not a data gap.
2. **`Division_Office`** only appears 2020-21 through 2023-24, with
   all-zero figures (an administrative unit with no consumer
   installations) — real, not a bug.
3. **Decimal-coded Misc. revenue categories** (61.9027, 61.9047, etc.) are
   kept as their own `dim_tariff_category` rows, not filtered out.
4. **~30 tariff codes still have multiple description variants** (e.g.
   `LT-1` has 11, `LT-6` has 7) after the dedup in §4.9 — these look like
   genuinely different usage slabs (HP ranges, Free Lighting variants),
   not further duplication, but weren't individually verified row-by-row.
5. **`approved_avg_cost_of_supply`** is almost always empty at line-item
   grain in the source itself — not an extraction bug.
6. A handful of columns per block didn't map to a named field (mostly
   stray orphan fragments the source itself contains) — preserved
   losslessly in `staging_dcb.extra_metrics` (JSON), not silently dropped.
7. **`demand_revenue_subtotal` doesn't always equal
   `demand_fixed_charges + demand_others + demand_energy_charges`**, even
   though the source sheet's own header says it should. Found by the
   project owner, verified: holds on ~99% of rows; the exceptions are the
   Misc. revenue tariff codes (`61.xxxx`/`62.xxxx`) — flat fees where the
   Fixed/Others/Energy split doesn't apply, so the source puts the whole
   amount directly in Revenue with the other three at zero. Correct
   source data, not a bug — don't "fix" it. Full detail in
   `DCB_Semantic_Layer/semantic_layer.md` §3.

## 6. Next steps (not started)

1. **Port to Neon Postgres**, if/when cloud hosting is wanted — the
   schema is close enough to Postgres that this is mostly `CREATE TABLE`
   syntax + `SERIAL`/`JSONB` type tweaks, not a rewrite. (This session
   deliberately stayed local/SQLite — no cloud DB was set up.)
2. **The NL2SQL chatbot itself** — FastAPI + Gemini + Neon Postgres,
   matching `bescom-ai`'s architecture. The schema/prompt module this
   needs is already built: load `DCB_Semantic_Layer/semantic_layer.json`
   into the system prompt alongside the schema DDL, and use
   `DCB_Semantic_Layer/example_queries.sql`'s 10 pairs as the starting
   eval set. The double-counting/grain rules that module encodes (never
   mix `fact_dcb` with `fact_dcb_rollups`, don't double-count the
   Division total, `fiscal_year` is a string, etc.) are the same ones
   this handoff has been carrying since v1 — now written once, in a form
   a prompt-builder can load directly instead of re-typing into a prompt
   by hand.

---

*Pipeline built and verified across this session: raw `.xlsx` →
`DCB_Segregated_By_SubDivision/` → `DCB_Staging_DB/` →
`DCB_Star_Schema/` → `DCB_Semantic_Layer/`. Every stage's own README has
more detail than this summary; this file exists to be portable on its
own.*
