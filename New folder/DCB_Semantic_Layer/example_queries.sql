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
SELECT f.fiscal_year,
       SUM(CASE WHEN t.tariff_code LIKE 'LT-1%' THEN f.demand_total ELSE 0 END) AS domestic_lt1_demand,
       SUM(CASE WHEN t.tariff_code LIKE 'HT-2%' THEN f.demand_total ELSE 0 END) AS industrial_ht2_demand
FROM fact_dcb f
JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id
JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id
WHERE s.subdivision_name = 'Division_Total'
GROUP BY f.fiscal_year
ORDER BY f.fiscal_year;


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
