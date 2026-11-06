-- Reviewed question -> SQL pairs over the mart_* tables (DCB_Mart/dcb_mart.sqlite, attached as `mart`).
-- Used two ways by dcb-ai-app: (1) as worked examples in the model's prompt, ahead of the older star-schema
-- examples in DCB_Semantic_Layer/example_queries.sql; (2) as the verified-query library (app/verified_queries.py):
-- a question phrased very close to one of these, naming exactly the same sub-divisions / families / years, gets
-- this SQL and a deterministic answer with no model call. Every block is executed by tests/test_verified.py.
-- Format: "-- <n>. <question>" then the SQL, ending with a semicolon.

-- 1. Collection efficiency of every sub-division in 2024-25
SELECT subdivision_name, collection_efficiency_pct, demand_total, collection_grand_total, closing_balance_total
FROM mart_fact_subdivision_year
WHERE fiscal_year = '2024-25'
ORDER BY collection_efficiency_pct DESC;

-- 2. Division demand, collection and collection efficiency for every year
SELECT fiscal_year, demand_total, collection_grand_total, collection_efficiency_pct, closing_balance_total
FROM mart_fact_division_year
ORDER BY fiscal_year;

-- 3. Demand of every tariff family in the whole division in 2023-24
SELECT family_code, family_label, demand_total, collection_grand_total, collection_efficiency_pct
FROM mart_fact_family_division_year
WHERE fiscal_year = '2023-24'
ORDER BY demand_total DESC;

-- 4. LT-4 demand in Arkalgud for every year
SELECT fiscal_year, demand_total, collection_grand_total, collection_efficiency_pct
FROM mart_fact_family_subdivision_year
WHERE subdivision_name = 'Arkalgud' AND family_code = 'LT-4'
ORDER BY fiscal_year;

-- 5. Compare LT-1 and HT-2 demand across all years
SELECT fiscal_year,
       SUM(CASE WHEN family_code = 'LT-1' THEN demand_total END) AS lt1_demand,
       SUM(CASE WHEN family_code = 'HT-2' THEN demand_total END) AS ht2_demand
FROM mart_fact_family_division_year
WHERE family_code IN ('LT-1', 'HT-2')
GROUP BY fiscal_year
ORDER BY fiscal_year;

-- 6. LT-4(a) demand and collection efficiency in Hangarahalli in 2024-25
SELECT tariff_code, description, demand_total, collection_grand_total, collection_efficiency_pct
FROM mart_fact_slab_subdivision_year
WHERE subdivision_name = 'Hangarahalli' AND fiscal_year = '2024-25' AND tariff_code = 'LT-4(a)';

-- 7. Public lighting demand in the whole division for every year
SELECT fiscal_year, tariff_code, description, demand_total
FROM mart_fact_slab_division_year
WHERE family_code = 'LT-6' AND description LIKE '%Public Lighting%'
ORDER BY fiscal_year;

-- 8. DC/MNR installations for LT-1 for every year
SELECT fiscal_year, dc_mnr_installations, total_installations
FROM mart_fact_family_division_year
WHERE family_code = 'LT-1'
ORDER BY fiscal_year;

-- 9. Arrears (closing balance) of every sub-division in 2025-26
SELECT subdivision_name, closing_balance_total, opening_balance_total, uncollected_demand
FROM mart_fact_subdivision_year
WHERE fiscal_year = '2025-26'
ORDER BY closing_balance_total DESC;

-- 10. LT and HT demand and collection in 2024-25
SELECT voltage_class, SUM(demand_total) AS demand_total, SUM(collection_grand_total) AS collection_grand_total,
       SUM(collection_grand_total) * 100.0 / NULLIF(SUM(demand_total), 0) AS collection_efficiency_pct
FROM mart_fact_family_division_year
WHERE fiscal_year = '2024-25' AND voltage_class IN ('LT', 'HT')
GROUP BY voltage_class
ORDER BY voltage_class;

-- 11. Share of LT-4 in the division's demand in 2025-26
SELECT f.family_code, f.demand_total, d.demand_total AS division_demand_total,
       f.demand_total * 100.0 / NULLIF(d.demand_total, 0) AS share_of_demand_pct
FROM mart_fact_family_division_year f
JOIN mart_fact_division_year d ON d.fiscal_year = f.fiscal_year
WHERE f.fiscal_year = '2025-26' AND f.family_code = 'LT-4';

-- 12. Change in collection of every sub-division from 2024-25 to 2025-26
SELECT c.subdivision_name, p.collection_grand_total AS collection_2024_25, c.collection_grand_total AS collection_2025_26,
       (c.collection_grand_total - p.collection_grand_total) * 100.0 / NULLIF(p.collection_grand_total, 0) AS change_pct
FROM mart_fact_subdivision_year c
JOIN mart_fact_subdivision_year p ON p.subdivision_name = c.subdivision_name AND p.fiscal_year = '2024-25'
WHERE c.fiscal_year = '2025-26'
ORDER BY change_pct DESC;

-- 13. Collection efficiency of HT categories in each sub-division in 2025-26
SELECT subdivision_name, SUM(demand_total) AS demand_total, SUM(collection_grand_total) AS collection_grand_total,
       SUM(collection_grand_total) * 100.0 / NULLIF(SUM(demand_total), 0) AS collection_efficiency_pct
FROM mart_fact_family_subdivision_year
WHERE fiscal_year = '2025-26' AND voltage_class = 'HT'
GROUP BY subdivision_name
ORDER BY collection_efficiency_pct;

-- 14. Consumption and average billed rate of the whole division for every year
SELECT fiscal_year, consumption_total, demand_total, demand_per_unit_rs
FROM mart_fact_division_year
ORDER BY fiscal_year;
