-- DCB Semantic Layer -- worked example questions
-- Every query below was run against dcb_star_schema.sqlite and verified to
-- return sensible, spot-checked results. Use as documentation and as a
-- starting eval set for an NL2SQL chatbot (see HANDOFF.md and
-- semantic_layer.md for the rules behind why each is written this way).

-- 1. What was the collection efficiency in Arkalgud in 2024-25?
-- (Recomputed from totals, not read from the stored pct_ column, because
--  this aggregates across every tariff category -- see semantic_layer.md #4.5)
SELECT ROUND(SUM(f.collection_grand_total) * 100.0 / SUM(f.demand_total), 2) AS collection_efficiency_pct
FROM fact_dcb f
JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE s.subdivision_name = 'Arkalgud' AND f.fiscal_year = '2024-25';
-- => 93.62%


-- 2. What's the year-over-year growth in total installations for the whole division?
-- (Reads from the division-wide grand_total rollup, not a SUM over fact_dcb
--  sub-divisions -- see semantic_layer.md #4.2 on not double-counting the division)
SELECT r.fiscal_year,
       r.total_installations AS installations,
       r.total_installations - LAG(r.total_installations) OVER (ORDER BY d.start_year) AS yoy_change
FROM fact_dcb_grand_totals r
JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
JOIN dim_fiscal_year d ON r.fiscal_year = d.fiscal_year
WHERE s.subdivision_name = 'Division_Total'
ORDER BY d.start_year;


-- 3. Which tariff category generated the most demand in 2023-24?
SELECT t.tariff_code, t.description, SUM(f.demand_total) AS total_demand
FROM fact_dcb f
JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id
JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE f.fiscal_year = '2023-24' AND s.subdivision_name = 'Division_Total'
GROUP BY t.tariff_code, t.description
ORDER BY total_demand DESC
LIMIT 5;
-- => LT-4(a) (agricultural IP Sets, un-metered rural feeder) tops the list


-- 4. How much outstanding balance (arrears) does Konanur currently carry?
SELECT f.fiscal_year, SUM(f.closing_balance_total) AS outstanding_balance
FROM fact_dcb f
JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE s.subdivision_name = 'Konanur'
GROUP BY f.fiscal_year
ORDER BY f.fiscal_year DESC
LIMIT 1;


-- 5. Compare domestic (LT-1) vs industrial (HT-2) demand across all years.
-- CORRECTED (was a `t.tariff_code LIKE 'LT-1%'`/`LIKE 'HT-2%'` SUM over
-- fact_dcb): a LIKE-prefix match against the tariff-category column looks
-- reasonable for HT-2 (whose real leaf rows are HT-2(a)/(b)/(c), so the
-- prefix genuinely captures the right rows) but is NOT safe for LT-1 --
-- the 2024-25 and 2025-26 sheets both also tag a second, DIFFERENT group
-- ("Ltng., heating & Motive Power...", which is LT-2's family in every
-- other year) with a raw tariff_category cell of 'LT-1'/'LT-1 A' -- a
-- real source mislabeling, not an extraction bug (see
-- DCB_Staging_DB/build_staging.py's classify_row_type() docstring). A
-- LIKE 'LT-1%' prefix match silently swept that second group in too,
-- inflating LT-1 demand by ~4-5x for those two years. Reading the
-- category's own printed "Sub Total of X" / "Total of X" rollup row from
-- fact_dcb_rollups instead (never re-deriving from fact_dcb by matching
-- the tariff-category column) sidesteps this entirely -- it's also the
-- only way to get HT-2 at all if HT-2 had no lettered sub-splits, so this
-- is the one general pattern to use for ANY family-level total, not a
-- workaround specific to LT-1. The ORDER BY/LIMIT 1 per side picks the
-- sheet's own explicit combined figure ('Total of LT-1', rollup_type
-- 'combined_total') over the two separate per-group subtotals when both
-- exist for the same year (2025-26 only, so far) -- see
-- DCB_Semantic_Layer/semantic_layer.json's fact_dcb_rollups.tariff_code_summarized
-- note for why that's the correct one to prefer, not a value to
-- re-derive by adding the sub_total rows together yourself.
SELECT d.fiscal_year,
       (SELECT r.demand_total FROM fact_dcb_rollups r
          JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
         WHERE s.subdivision_name = 'Division_Total' AND r.fiscal_year = d.fiscal_year
           AND r.tariff_code_summarized = 'LT-1' AND r.rollup_type IN ('sub_total', 'combined_total')
         ORDER BY CASE r.rollup_type WHEN 'combined_total' THEN 0 ELSE 1 END
         LIMIT 1) AS domestic_lt1_demand,
       (SELECT r.demand_total FROM fact_dcb_rollups r
          JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
         WHERE s.subdivision_name = 'Division_Total' AND r.fiscal_year = d.fiscal_year
           AND r.tariff_code_summarized = 'HT-2' AND r.rollup_type IN ('sub_total', 'combined_total')
         ORDER BY CASE r.rollup_type WHEN 'combined_total' THEN 0 ELSE 1 END
         LIMIT 1) AS industrial_ht2_demand
FROM dim_fiscal_year d
ORDER BY d.fiscal_year;
-- => e.g. 2024-25: LT-1 98,589,118.03 / HT-2 144,256,241.73;
--         2025-26: LT-1 542,708,386.67 (the 'Total of LT-1' combined
--         figure) / HT-2 141,349,133.19


-- 6. What's the official (printed) GRAND TOTAL demand for 2025-26?
-- (Reads the report's own printed figure from fact_dcb_rollups, does not
--  recompute it -- see semantic_layer.md #4.1)
SELECT r.fiscal_year, s.subdivision_name, r.demand_total, r.collection_grand_total
FROM fact_dcb_grand_totals r
JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
WHERE r.fiscal_year = '2025-26' AND s.subdivision_name = 'Division_Total';


-- 7. Which sub-division has the best collection efficiency, most recent year?
SELECT s.subdivision_name,
       ROUND(SUM(f.collection_grand_total) * 100.0 / SUM(f.demand_total), 2) AS collection_efficiency_pct
FROM fact_dcb f
JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE f.fiscal_year = (SELECT MAX(fiscal_year) FROM fact_dcb)
  AND s.subdivision_type IN ('sub_division', 'section')
GROUP BY s.subdivision_name
ORDER BY collection_efficiency_pct DESC;
-- => Konanur leads at 97.46% (2025-26)


-- 8. How many installations are dead/non-recoverable (DC/MNR) by sub-division?
-- (COALESCE handles both source layouts -- pre-2018-19 combined dc_mnr_installations,
--  2018-19+ split dc_installations/mnr_installations; only one side is ever non-zero
--  per row, so summing all three is safe, not double-counting)
SELECT s.subdivision_name, f.fiscal_year,
       SUM(COALESCE(f.dc_installations, 0) + COALESCE(f.mnr_installations, 0) + COALESCE(f.dc_mnr_installations, 0)) AS dead_or_nonrecoverable
FROM fact_dcb f
JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE s.subdivision_type IN ('sub_division', 'section') AND f.fiscal_year = '2024-25'
GROUP BY s.subdivision_name, f.fiscal_year
ORDER BY dead_or_nonrecoverable DESC;


-- 9. What's the trend in Miscellaneous revenue (fees/penalties) over the years?
-- (misc_total is a distinct rollup type -- LT+HT tariffs excluded, see
--  semantic_layer.md #1 on Sub Total/GRAND TOTAL and the star schema README)
SELECT r.fiscal_year, r.demand_total AS misc_revenue_demand
FROM fact_dcb_rollups r
JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
WHERE r.rollup_type = 'misc_total' AND s.subdivision_name = 'Division_Total'
ORDER BY r.fiscal_year;


-- 10. Show the full DCB chain (opening, demand, collection, closing) for
--     Holenarasipura, 2024-25.
SELECT SUM(f.opening_balance_total) AS opening_balance,
       SUM(f.demand_total) AS demand,
       SUM(f.collection_grand_total) AS collection,
       SUM(f.closing_balance_total) AS closing_balance
FROM fact_dcb f
JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE s.subdivision_name = 'Holenarasipura' AND f.fiscal_year = '2024-25';
-- => closing_balance = opening_balance + demand - collection, holds exactly


-- 11. Outstanding balance throughout the years / year-wise breakdown
--     (no sub-division or fiscal year named -- that's not ambiguous here,
--     it means "the whole division, every year": default subdivision_name
--     to 'Division_Total' and return every fiscal_year as its own row,
--     not a single figure and not a request for clarification. Trend
--     phrasing like this ("throughout the year(s)", "over the years",
--     "breakdown", "by year") is chart-worthy -- set chart_data with
--     type "line", label_column=fiscal_year, value_column=outstanding_balance.)
SELECT r.fiscal_year, r.closing_balance_total AS outstanding_balance
FROM fact_dcb_grand_totals r
JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
WHERE s.subdivision_name = 'Division_Total'
ORDER BY r.fiscal_year;


-- 12. Compare demand between two specific tariff-category SLABS (not whole
--     families like example #5's LT-1%/HT-2% LIKE-prefix comparison) --
--     e.g. "BMA & Municipal Corp" lighting/motive-power demand vs "Urban
--     Local Bodies" lighting/motive-power demand, division-wide. Same
--     SUM(CASE WHEN...) shape as example #5, but each side is keyed on an
--     exact tariff_code equality (not a family prefix), since a slab-level
--     comparison needs to isolate one specific row per side, not a whole
--     family of codes.
SELECT f.fiscal_year,
       SUM(CASE WHEN t.tariff_code = 'LT-2(a)(i)' THEN f.demand_total ELSE 0 END) AS bma_municipal_corp_demand,
       SUM(CASE WHEN t.tariff_code = 'LT-2(a)(ii)' THEN f.demand_total ELSE 0 END) AS urban_local_bodies_demand
FROM fact_dcb f
JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id
JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE s.subdivision_name = 'Division_Total'
GROUP BY f.fiscal_year
ORDER BY f.fiscal_year;


-- 13. What was the HT-2 demand in 2015-16? -- a single-category, single-year
--     family-level lookup. HT-2 never appears as its own row in
--     dim_tariff_category/fact_dcb -- only its lettered sub-splits do
--     (HT-2(a)(ii), HT-2(b)(ii), HT-2(c)(i), HT-2(c)(ii) in this data) --
--     so `WHERE tariff_code = 'HT-2'` against fact_dcb matches zero rows
--     every time, in every year (this is what silently returned 0 for
--     "HT-2 demand" before this fix, when wrapped in COALESCE(SUM(...),0)).
--     Read the category's own printed subtotal from fact_dcb_rollups
--     instead -- same pattern as example #5's corrected version, just for
--     one category/year instead of a two-way comparison across all years.
--     If this returns zero rows for some other category/year, that
--     category genuinely wasn't billed that year -- answerable=false,
--     not a fabricated 0 (see rules above on LT-7/HT-6/HT-7/IPP/HT IPP/
--     AUXILIARY CONSUMPTION only starting in certain years).
SELECT r.demand_total AS ht2_demand
FROM fact_dcb_rollups r
JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
WHERE s.subdivision_name = 'Division_Total' AND r.fiscal_year = '2015-16'
  AND r.tariff_code_summarized = 'HT-2' AND r.rollup_type IN ('sub_total', 'combined_total')
ORDER BY CASE r.rollup_type WHEN 'combined_total' THEN 0 ELSE 1 END
LIMIT 1;
-- => 23,399,034.75


-- 14. Compare LT-3 vs LT-5 collection across all years -- a second
--     family-vs-family comparison (see example #5), deliberately a
--     DIFFERENT pair, so this pattern reads as the general rule for any
--     family-vs-family comparison rather than something LT-1/HT-2-
--     specific. LT-3 and LT-5 are both plain family codes with no
--     lettered sub-splits, so neither has its own row in
--     dim_tariff_category/fact_dcb either -- a `SUM(CASE WHEN
--     t.tariff_code = 'LT-3' ...)` over fact_dcb (the pattern that IS
--     correct for a slab-level comparison, example #12) would silently
--     return 0 for both sides here, the same failure this fix already
--     corrected once for LT-1/HT-2 in example #5. The ORDER BY/LIMIT 1
--     combined_total-preference tie-break is included on both sides
--     regardless of whether either family happens to have one this time
--     (LT-3/LT-5 mostly don't -- only example #5's LT-1 does, in
--     2025-26), since the pattern must hold generically for ANY family.
SELECT d.fiscal_year,
       (SELECT r.collection_grand_total FROM fact_dcb_rollups r
          JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
         WHERE s.subdivision_name = 'Division_Total' AND r.fiscal_year = d.fiscal_year
           AND r.tariff_code_summarized = 'LT-3' AND r.rollup_type IN ('sub_total', 'combined_total')
         ORDER BY CASE r.rollup_type WHEN 'combined_total' THEN 0 ELSE 1 END
         LIMIT 1) AS lt3_collection,
       (SELECT r.collection_grand_total FROM fact_dcb_rollups r
          JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id
         WHERE s.subdivision_name = 'Division_Total' AND r.fiscal_year = d.fiscal_year
           AND r.tariff_code_summarized = 'LT-5' AND r.rollup_type IN ('sub_total', 'combined_total')
         ORDER BY CASE r.rollup_type WHEN 'combined_total' THEN 0 ELSE 1 END
         LIMIT 1) AS lt5_collection
FROM dim_fiscal_year d
ORDER BY d.fiscal_year;


-- 15. What was the LT-6 EV charging station demand in 2024-25? -- a family
--     code (LT-6) named ALONGSIDE a qualifying word ("EV charging
--     station") that matches one of LT-6's own real, distinct
--     descriptions -- NOT a bare family-level question, even though LT-6
--     is also a real fact_dcb_rollups family (see example #13/rule
--     above). dim_tariff_category has THREE separate rows under LT-6/
--     'LT-6 C'/'LT-6 c' for "Public Lighting", "Water Supply", and "EVM
--     Vheicle Charging Station" (sic -- a real source typo for "Vehicle",
--     not an extraction bug) respectively -- reading the bare LT-6
--     family total from fact_dcb_rollups here would silently blend all
--     three of those together and badly overstate what was actually
--     asked (verified: the family total for 2024-25 is 210,077,463.48,
--     over 6,700x the real EV-charging-only figure below). Filter on the
--     one matching tariff_category_candidates description instead, the
--     same as any other slab-level lookup (example #12), even though a
--     family code was also named. If this returns zero rows for an
--     earlier year (e.g. 2016-17, before this row even existed in the
--     source), that's a genuine "this category wasn't billed yet"
--     answer -- do not fall back to the family total instead.
SELECT t.tariff_code, t.description, SUM(f.demand_total) AS ev_charging_demand
FROM fact_dcb f
JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id
JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE t.description = 'EVM Vheicle Charging Station LT' AND s.subdivision_name = 'Division_Total'
  AND f.fiscal_year = '2024-25'
GROUP BY t.tariff_code, t.description;
-- => tariff_code='LT-6 C', ev_charging_demand=31,200.57
