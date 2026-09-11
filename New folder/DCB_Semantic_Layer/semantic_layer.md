# DCB Semantic Layer

The business-meaning layer on top of `DCB_Star_Schema/dcb_star_schema.sqlite`.
Where the star schema's README explains *what the tables are*, this
explains *what the numbers mean* — domain vocabulary, tariff-code
glossary, derived metrics, and the rules that keep a query from silently
producing a wrong (usually double-counted or non-additive-summed) answer.

This is the module `HANDOFF.md` §6 calls for: the thing an NL2SQL
chatbot's prompt needs to be given verbatim (or a human needs to read)
before it can safely turn a question like *"what was the collection
efficiency in Arkalgud last year"* into correct SQL. `semantic_layer.json`
is the same content in a structure a prompt-builder can load directly;
this file is the same content for a person to read.

---

## 1. Domain vocabulary

**DCB** = Demand-Collection-Balance. For every tariff category, in every
sub-division, every year: how many customers there are, what they were
billed (**Demand**), what was actually paid (**Collection**), and what's
still owed (**Balance**). It's the standard periodic financial-position
report an Indian electricity distribution utility runs.

**LT vs HT** = Low Tension vs High Tension. LT tariff codes are
low-voltage connections — households, small shops, small pump sets. HT
codes are high-voltage bulk connections — factories, large irrigation
schemes, apartment complexes, HT-metered agriculture. This is the single
biggest split in the tariff structure; most "what kind of customer" or
"industrial vs domestic" questions map onto LT vs HT.

**DC / MNR** = Dead Connections / connections under some form of
Non-Recovery status (confirmed in `HANDOFF.md` §5 from an earlier review
of this same source with the project owner) — installations that exist on
paper but aren't actively billed/collectable. Tracked separately from
live installations (`dc_installations`, `mnr_installations`, or the
combined `dc_mnr_installations` in years where the source didn't split
them).

**FAC** = Fuel Adjustment Charge — a pass-through surcharge tied to fuel
cost fluctuations, billed and collected alongside the base tariff.

**ARR** = Average Revenue Realized (`revised_arr` column) — revenue per
unit of energy actually realized, a standard regulatory metric.

**OB / Demand / Collection / CB** = the DCB arithmetic chain per period:
`Closing Balance = Opening Balance + Demand − Collection`. Each of these
four is itself broken into Revenue / Interest / Tax / FAC (and, from
2025-26 onward, a P&G Surcharge) sub-components — see §3.

**Sub Total / LT-TOTAL / HT-TOTAL / GRAND TOTAL** — the report's own
running subtotals, printed inline with the data in the original Excel.
These live in `fact_dcb_rollups`, never in `fact_dcb` — see §4.

## 2. Tariff category glossary

`dim_tariff_category.tariff_code` groups into these bands. Full
descriptions are in the table itself (`SELECT * FROM dim_tariff_category`);
this is the map for going from a code prefix to what kind of customer it
represents.

### LT (Low Tension) — domestic, small commercial, agricultural
| Code | What it covers |
|---|---|
| `LT-1` | Domestic lighting — the subsidized **Bhagyajyothi/Kutirajyothi** schemes, billed in usage-slab tiers (metered, tiered by units consumed) |
| `LT-2` | Lighting & motive power for institutions and municipal-area premises (BMA/Municipal Corp, Urban Local Bodies, Village Panchayat, private institutions) — several sub-codes by area type, some with a Free Lighting variant |
| `LT-3` | Commercial lighting & motive power (shops, businesses) in urban local bodies / village panchayats, plus permanent hoarding/advertisement-board connections (`LT-3 b`) |
| `LT-4` | Irrigation Pump (IP) Sets — agricultural pumps, split by HP size (`<=10HP` vs `Above 10HP`) and rural/metered vs unmetered feeder |
| `LT-5` | Heating & Motive Power for larger LT loads, tiered by HP band (5-40, 40-67, 100+ HP) |
| `LT-6` | Public utilities — Public Lighting, municipal Water Supply pumping, EV charging stations |
| `LT-7` | Temporary Supply — short-term/prepaid-meter connections (event power, construction, renewable renewals) |
| `61.xxxx` / `62.xxxx` | **Miscellaneous revenue codes** — not consumption billing at all: reconnection fees, meter testing charges, delayed-payment penalties, rental income, cross-subsidy surcharges, solar rooftop registration fees, etc. Kept in the schema per `HANDOFF.md` §5, not filtered out |

### HT (High Tension) — industrial, bulk, large agricultural
| Code | What it covers |
|---|---|
| `HT-1` | Bulk Water Supply & Sewerage Pumping |
| `HT-2` | Industrial and commercial HT consumers (factories, railway traction, hospitals/institutions, general commercial) |
| `HT-3` | Lift Irrigation schemes — government and private, by feeder type |
| `HT-4` | Residential apartment complexes on HT connections |
| `HT-5` | Temporary Supply, HT-scale (67 HP and above) |
| `HT-6` | Large-scale irrigation/agriculture/horticulture (govt & private farms, plantations) |
| `HT-7` | Lift Irrigation, government department schemes |
| `HT IPP`, `LT IPP`, `AUXILIARY CONSUMPTION` | Special accounting categories with little or no installation count in the data (near-zero across all years/sub-divisions) — not regular billed consumer categories in the usual sense; treat totals from these with more caution than a normal tariff row |

**On querying by category:** `tariff_code` alone is the right grain for
"how much industrial demand" type questions (`GROUP BY tariff_code`);
`tariff_category_id`/`(tariff_code, description)` is the right grain for
"how many domestic customers use more than 40 units" type questions where
the usage slab itself matters.

## 3. The DCB financial chain (why so many `*_revenue`/`*_interest`/`*_tax`/`*_fac` columns)

Every one of Opening Balance, Demand, Collection, Opening-Balance-Plus-
Demand, and Closing Balance is broken into up to 5 components in the
source report, and `fact_dcb`/`fact_dcb_rollups` preserve that breakdown
column-for-column:

- **Revenue** — the base energy charge
- **Interest** — interest on overdue amounts
- **Tax** — electricity duty / cess
- **FAC** — fuel adjustment charge (see §1)
- **P&G Surcharge** — a component that only appears from **2025-26
  onward** (`*_pg_surcharge` columns are `NULL` in all earlier years —
  this is a real new tariff component, not missing data)

Each of these has its own `_total` column too (e.g. `demand_total`,
`opening_balance_total`, `closing_balance_total`) — **use the `_total`
column for "how much did X owe/bill/collect" questions**; only reach for
the Revenue/Interest/Tax/FAC breakdown when the question specifically
asks about one of those components (e.g. "how much fuel surcharge was
billed").

**Collection** additionally splits each component by payment channel:
`_chq_cash` (cheque/cash payments) vs `_adjust` (adjustments, e.g.
against a security deposit or prior credit) — both roll up into the same
`_total`.

**`demand_revenue_subtotal` = `demand_fixed_charges` + `demand_others` +
`demand_energy_charges`, except for flat-fee tariff codes.** The source
sheet's own header states this formula (`Revenue (24=20+21+22)`), and it
holds on ~99% of rows. The exceptions are entirely explainable, not data
errors: the Miscellaneous revenue codes (`61.9027`, `61.9047`, `61.9067`,
`62.9177`, etc. — reconnection fees, service-connection charges, other
receipts) aren't consumption-based tariffs, so the Fixed/Others/Energy
split doesn't semantically apply to them — the source correctly enters
the full flat fee directly under Revenue instead, with Fixed/Others/
Energy all zero. **Do not "fix" these rows to satisfy the formula** —
forcing zeros or redistributing the amount would misrepresent a flat fee
as consumption-based billing. A stray ~₹1-on-₹2M mismatch can also show
up on an otherwise normal consumption-tariff row (seen once, on `HT-6`,
2025-26) — that's ordinary source-side rounding noise, not a pattern, and
immaterial at that scale. If you build an automated check for this
formula, exclude the Misc. revenue (`6X.XXXX`) tariff codes and allow a
small rounding tolerance, rather than treating every mismatch as an
extraction bug.

## 4. Rules that prevent a wrong answer

These are not stylistic preferences — getting any of them wrong produces
a plausible-looking but incorrect number.

1. **Never mix `fact_dcb` and `fact_dcb_rollups` in the same `SUM()`.**
   `fact_dcb` is real line items only, at the correct grain to sum
   directly. `fact_dcb_rollups` holds the report's *own* printed
   subtotals (`rollup_type` = `sub_total`, `lt_total`, `ht_total`,
   `lt_ht_total`, `misc_total`, `grand_total`) — summing both together
   double-counts every category twice over.
   - Want a *computed* total? `SUM(...)` over `fact_dcb`.
   - Want the report's own *official/printed* headline figure? Read the
     matching row from `fact_dcb_rollups` (or the `fact_dcb_grand_totals`
     view for the grand total specifically) directly — don't recompute it.
   - As of this build, both agree exactly (0.0% deviation, see the star
     schema README) — but the distinction still matters if the source
     data ever changes.

2. **Never double-count the Division total.** `dim_subdivision` has a
   `subdivision_type`: `division` (the `Division_Total` row — a rollup of
   the whole division, not a place), `sub_division`, `section` (Konanur —
   organizationally a section, not a sub-division, but functionally
   equivalent for totals), and `office` (`Division_Office` — an
   administrative unit with no consumer installations, not a geographic
   area). To total "the whole division," either:
   - read `subdivision_type = 'division'` directly, **or**
   - `SUM()` over the `fact_dcb_subdivisions` view (already filtered to
     `sub_division`/`section` only)
   
   — never both, and never include `Division_Office` in a geographic
   rollup (it would silently add a second, redundant "division total"
   layer, or pollute a sub-division comparison with an unrelated
   administrative bucket).

3. **`fiscal_year` is a string** (`'2015-16'`), not a number — sort/filter
   as text, or join to `dim_fiscal_year.start_year` if you need it as an
   integer for arithmetic (e.g. "years since 2020").

4. **`Saligrama` genuinely stops appearing after 2017-18** (closed as a
   sub-division, not a data gap) and **`Division_Office` only exists
   2020-21 through 2023-24** — a query returning no rows for either
   outside those ranges is correct, not broken.

5. **Percentage/ratio/per-unit columns are not additive.** Columns
   prefixed `pct_`, `ratio_`, or matching `*_per_unit*`/
   `*_per_installation` (e.g. `pct_collection_efficiency_with_adj`,
   `demand_per_unit`) **cannot be summed or averaged across rows and stay
   meaningful** — a "45%" and a "60%" don't average to a real 52.5%
   unless weighted by the right denominator. In `fact_dcb`, these are
   `NULL` on any row that combines more than one staging line
   (`source_row_count > 1`) specifically to prevent silently producing a
   wrong ratio; recompute from the underlying totals instead (e.g.
   `collection_grand_total / demand_total`) rather than reading/summing
   these columns across multiple rows.

## 5. Common derived metrics (formulas, not stored columns)

| Question shape | Formula |
|---|---|
| Collection efficiency (with adjustments) | `collection_grand_total / demand_total` (a version of this is stored per-row as `pct_collection_efficiency_with_adj`, but only trust the stored value on a single un-aggregated row — recompute after any `GROUP BY`/`SUM`) |
| Outstanding arrears | `closing_balance_total` (already = opening + demand − collection for that period) |
| Year-over-year change | Self-join `fact_dcb`/an aggregate on `fiscal_year` to `dim_fiscal_year.start_year = start_year - 1` |
| "Which tariff category contributes most to demand" | `GROUP BY tariff_category_id` (or `tariff_code` for a coarser cut) on `fact_dcb`, `ORDER BY SUM(demand_total) DESC` |
| Domestic vs Industrial split | `WHERE tariff_code LIKE 'LT-1%'` (domestic) vs `WHERE tariff_code LIKE 'HT-2%'` (industrial) — or `LIKE 'LT%'` vs `LIKE 'HT%'` for the broad low-tension/high-tension split |
| Per-sub-division comparison | `JOIN fact_dcb_subdivisions` (not raw `fact_dcb`, to exclude `Division_Total`/`Division_Office`) |

## 6. Worked example questions

See `example_queries.sql` in this folder for the runnable SQL behind each
of these — useful both as documentation and as a starting eval set for
an NL2SQL chatbot, matching what the original `HANDOFF.md`/README called
for.

1. What was the collection efficiency in Arkalgud in 2024-25?
2. What's the year-over-year growth in total installations for the whole division?
3. Which tariff category generated the most demand in 2023-24?
4. How much outstanding balance (arrears) does Konanur currently carry?
5. Compare domestic (LT-1) vs industrial (HT-2) demand across all years.
6. What's the official (printed) GRAND TOTAL demand for 2025-26?
7. Which sub-division has the best collection efficiency, most recent year?
8. How many installations are dead/non-recoverable (DC/MNR) by sub-division?
9. What's the trend in Miscellaneous revenue (fees/penalties) over the years?
10. Show the full DCB chain (opening, demand, collection, closing) for Holenarasipura, 2024-25.
