# DCB Star Schema (local SQLite)

Final step in the pipeline: `DCB_Segregated_By_SubDivision/` →
`DCB_Staging_DB/` → **this**. Transforms the flat, unfiltered
`staging_dcb` table into the typed star schema `HANDOFF.md` originally
called for — clean, numeric, ready for direct queries or an NL2SQL
chatbot to sit on top of.

## Files

| File | What it is |
|---|---|
| `dcb_star_schema.sqlite` | The database — 3 dimension tables, 2 fact tables, 1 QA table, 2 convenience views |
| `build_star_schema.py` | The transform script. Re-run any time `dcb_staging.sqlite` changes |

## Tables

| Table | Rows | What it is |
|---|---|---|
| `dim_fiscal_year` | 11 | One per year, 2015-16 .. 2025-26, with `start_year`/`end_year` as integers |
| `dim_subdivision` | 8 | `Division_Total` (the whole-division rollup), 5 real sub-divisions, `Konanur` (a section, not a sub-division), `Saligrama` (closed after 2017-18), `Division_Office` (2020-21 to 2023-24 only) — see `subdivision_type` |
| `dim_tariff_category` | 141 | Every distinct (tariff_code, description) pair actually billed somewhere in the 11 years |
| `fact_dcb` | 5,488 | One row per (year, sub-division, tariff_category, description) — every real numeric column, `REAL` typed |
| `fact_dcb_rollups` | 2,232 | The report's own printed Sub Total / LT-TOTAL / HT-TOTAL / LT+HT-TOTAL / MISCELLANEOUS-Total / GRAND TOTAL rows, kept separate so `SUM()` over `fact_dcb` is always safe and never double-counts |
| `reconciliation_report` | 292 | QA: for every (year, sub-division) block × 4 headline metrics, `fact_dcb` summed vs. the block's own `grand_total` row |

**Views:** `fact_dcb_subdivisions` (fact_dcb filtered to real sub-divisions/sections only, excluding the `Division_Total` rollup and `Division_Office`) and `fact_dcb_grand_totals` (just the `grand_total` rows from `fact_dcb_rollups`).

**Joins:** `fact_dcb.fiscal_year → dim_fiscal_year.fiscal_year`,
`fact_dcb.subdivision_id → dim_subdivision.subdivision_id`,
`fact_dcb.tariff_category_id → dim_tariff_category.tariff_category_id`.
`fact_dcb_rollups` joins to `dim_fiscal_year`/`dim_subdivision` the same
way but has no `tariff_category_id` (a rollup spans multiple categories).
For a `sub_total` row, `tariff_code_summarized` gives the tariff code it
subtotals (extracted from its printed label, e.g. `"Sub Total of LT-1"` → `"LT-1"`), as free text (not an FK).

## Reconciliation result

**0.0% average and 0.0% max deviation** across all 292 checks — `fact_dcb`,
summed per block, matches the report's own printed GRAND TOTAL exactly
(to floating-point precision) for every year, every sub-division, on all
4 headline metrics (installations, demand, collection, closing balance).
This is a much tighter result than `HANDOFF.md`'s original caveat about a
structural gap between line items and the printed total — see below for
why.

## Design decisions

- **Aggregation grain:** the source sometimes prints the same
  (tariff_code, description) pair as 2-3 separate physical rows (different
  consumer sub-groups under one usage slab). `fact_dcb` sums these into
  one row per (year, sub-division, tariff_code, description) — matching
  how the report's own "Sub Total of X" arithmetic works. `source_row_count`
  records how many staging rows were combined.
- **Non-additive columns aren't summed.** Percentages, ratios, and
  per-unit figures (`pct_*`, `ratio_*`, `*_per_unit*`, `*_per_installation`)
  are meaningless to add across rows, so they're kept only when a
  `fact_dcb` row came from a single staging row (`source_row_count=1`) and
  set to `NULL` otherwise, rather than silently producing a wrong number.
- **Why reconciliation is now exact, not ~structural-gap-like `HANDOFF.md` describes:**
  building this surfaced (and fixed, at the staging layer) two real bugs
  that were previously masking the true numbers — a forward-fill gap that
  dropped 1,046 real rows from their tariff code, and a row-type
  misclassification that conflated "LT+HT-TOTAL" with "GRAND TOTAL
  (LT+HT+Misc.)" as if they were the same duplicate row. With both fixed,
  the line items really do add up to the printed total for every block.

## Refinements made after the first build

`dim_tariff_category` started at 152 rows; a pass looking for avoidable
duplicates brought it to 141 by fixing two real issues at the staging
layer (both propagate through automatically on a rebuild):

- **4 more instances of the 2025-26 text-corruption bug** were found
  beyond what was already fixed (`Bhagyajyothi`, `Sewerage`,
  `Agriculture`, `damages` were each corrupted in at least one row) —
  each had silently created a duplicate `dim_tariff_category` row instead
  of matching the correctly-spelled version from other years.
- **Whitespace normalization**: the source pads descriptions with extra
  spaces for column alignment (e.g. `"...  , <= 5 HP"` vs `"...  ,   <= 5
  HP"`), which also created spurious duplicate rows. Now collapsed
  everywhere.

`LT-1` went from 13 description variants down to 11 genuinely distinct
usage slabs; `HT-1` from 6 down to 3. Reconciliation was re-checked after
both fixes and is still exact (0.0% avg/max deviation).

**Remaining, deliberately not touched:** ~30 tariff codes still have
multiple description variants (e.g. `LT-1` still has 11, `LT-6` has 7) —
these look like genuinely different usage slabs (different HP ranges,
Free Lighting variants, etc.), not further duplication. Distinguishing
"real slab" from "further typo" beyond what's been checked here would
need row-by-row judgment calls `HANDOFF.md` §5-6 flags as unresolved from
the original ETL too; left as-is rather than guessed at.

## Not done here

- No load-to-Neon step — this is a local SQLite build. The schema/data
  shape is close enough to Postgres that porting later is mostly `CREATE
  TABLE` syntax + `SERIAL`/`JSONB` type tweaks, not a rewrite.
