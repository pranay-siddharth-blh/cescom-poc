# DCB Staging Layer (local SQLite)

Next step after `DCB_Segregated_By_SubDivision/`. This takes the raw
`HNPURA DIVISION DCB FY 2015 TO 2026.xlsx` report and lands it into a
**staging** table — flat, mostly-TEXT, one row per line item/sub-total/
grand-total as printed in the source — matching the staging concept
described in `HANDOFF.md` (a dumb landing zone, not the final typed star
schema; that transform step is still to come).

## Files

| File | What it is |
|---|---|
| `dcb_staging.sqlite` | The database. One table `staging_dcb` (11,712 rows) + `staging_column_reference` (documents every raw source column → field mapping, per year/sub-division) |
| `staging_schema.sql` | The DDL for `staging_dcb` (TEXT-only, no constraints — the staging philosophy) |
| `staging_dcb.csv` | Same data as `staging_dcb`, as a portable CSV |
| `build_staging.py` | The ETL script that produced all of the above. Re-run any time the source workbook changes |

## How to open it

Any SQLite browser (e.g. "DB Browser for SQLite"), or from a terminal:

```bash
python -c "import sqlite3; c=sqlite3.connect('dcb_staging.sqlite'); print(c.execute('SELECT COUNT(*) FROM staging_dcb').fetchone())"
```

or `sqlite3 dcb_staging.sqlite` if you have the sqlite3 CLI installed.

## What's in `staging_dcb`

11,712 rows, one per (fiscal year × sub-division × tariff-category line, or
sub-total/grand-total row) exactly as printed in the source — nothing is
filtered out at this stage.

Fixed columns: `fiscal_year`, `subdivision`, `row_type`, `sl_no`,
`tariff_category`, `tariff_category_forward_filled`, `description`.

`row_type` tells you what kind of row it is:

| row_type | count | meaning |
|---|---|---|
| `line_item` | 5,525 | a real tariff-category line — safe to `SUM()` |
| `spillover_fragment` | 3,681 | near-empty row carrying one stray value spilled from a merged cell in the row above; not a real line item |
| `sub_total` | 2,013 | the report's own "Sub Total of X" rows |
| `other_total` | 152 | LT-TOTAL / HT-TOTAL rows |
| `annotation` | 122 | free-text footnote rows (e.g. "Applicable to areas under municipal corporations"), not data |
| `misc_total` | 73 | MISCELLANEOUS-Total rows — one per (year × sub-division) block |
| `lt_ht_total` | 73 | "LT+HT - TOTAL" rows — LT+HT tariffs only, **excluding** Miscellaneous revenue |
| `grand_total` | 73 | "GRAND TOTAL (LT+HT+Misc.)" rows — the true grand total, `lt_ht_total + misc_total` |

**Never `SUM()` across mixed row_types** — filter to `row_type='line_item'`
for a computed total, or use the `*_total`/`grand_total` rows directly when
you want the report's own printed figure (same principle as
`fact_dcb` vs `fact_dcb_rollups` in HANDOFF.md's target design).

The source doesn't consistently re-print a tariff code on every physical
row of a multi-row entry (e.g. a wrapped sub-slab description). Where a
`line_item` row's own `tariff_category` cell was blank in the source, it's
been forward-filled from the nearest preceding row that had one (1,046
rows) — `tariff_category_forward_filled='yes'` marks exactly which ones,
so you can always tell what was printed vs. inferred. Rows where the blank
turned out to be a footnote or a merged-cell artifact instead of a real
continuation were reclassified to `annotation`/`spillover_fragment` and
left unfilled.

~70 named numeric/text columns (all still stored as TEXT — staging does no
casting), e.g. `total_installations`, `connected_load_kw`,
`consumption_metered`, `ob_revenue`/`ob_interest`/`ob_tax`/`ob_fac`,
`demand_fixed_charges`, `collection_revenue_chq_cash`,
`closing_balance_total`, `pct_collection_efficiency_with_adj`, etc.

Every column name was derived from the source's own 3-level header (a
top header row, a mid sub-header row, and a Chq./Cash-vs-Adjust. row —
their exact position shifts between the 2015-16..2017-18 layout and the
2018-19+ layout, which `build_staging.py` accounts for). Check
`staging_column_reference` any time you want to see exactly which raw
Excel column, in which year, produced a given field.

`extra_metrics` (JSON): a handful of columns per block that didn't map to
a named field — mostly a couple of stray/orphan fragments the source
itself contains (e.g. an unlabeled "Approved | Tariff" fragment) and one
% column with a source-side typo variant. Nothing load-bearing is hidden
there; check it if a number seems to be missing from the named columns.

## Known caveats (carried over / newly found)

1. **Two header layouts exist and were reconciled by field name, not
   position** — 2015-16/2016-17/2017-18 put the Revenue/Interest/Tax/FAC
   sub-label one row lower than 2018-19 onward. Handled, but flagging it
   since it's the main source of complexity in `build_staging.py`.
2. **The 2025-26 text corruption bug is more widespread than documented.**
   `HANDOFF.md` only mentions `Village`→`VillAFe`. Building this surfaced
   the actual pattern: lowercase `ag`→`AF` (also hits `Average`, `Sewerage`,
   `Agriculture`, `damages`, `Bhagyajyothi`) and, separately, a `C`→`B` swap
   (`Actually`→`ABtually`, `FAC`→`FAB`). All known instances are fixed via
   an explicit typo-fix list in `build_staging.py` — not a blanket
   letter-substitution, since that risks false positives on legitimate
   text. A source-wide `Collec tion` (stray space) typo in one `%` column
   header is fixed the same way. Whitespace is also normalized (the report
   pads with spaces for column alignment), which matters because these
   typos and stray spacing were silently duplicating real-world categories
   into separate `dim_tariff_category` rows in the star schema — see
   `DCB_Star_Schema/README.md`.
3. **Some sheets embed an unrelated orphan mini-table** further right in
   the same rows (columns ~72-134 depending on year), repeating the
   block's own title and a compressed monthly/Lakh-denominated summary.
   This is excluded entirely (detected by the title text reappearing, or a
   run of blank columns) — it's not part of HANDOFF.md's documented ~65
   column fact table and its exact meaning wasn't established.
4. `Saligrama` is only present 2015-16/2016-17/2017-18 (closed as a
   sub-division after) and `Division_Office` only appears 2020-21 through
   2023-24 — both are real, not extraction gaps (same as noted in
   `HANDOFF.md` for the segregated files).

## Not done yet

This is **staging only** — TEXT, unfiltered, positionally faithful. Per
`HANDOFF.md`'s own pipeline, the next step would be a transform into the
typed star schema (`dim_fiscal_year`, `dim_subdivision`,
`dim_tariff_category`, `fact_dcb`, `fact_dcb_rollups`) with proper numeric
types, tariff-category normalization, and the sub-division name/typo
fixes already catalogued in `HANDOFF.md` §5-6. Say the word and that's the
next script to write.
