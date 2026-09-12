# DCB Chatbot — eval run

## Q1: What was the collection efficiency in Arkalgud in 2024-25?

- **answerable**: True
- **sql**: `SELECT ROUND(SUM(f.collection_grand_total) * 100.0 / SUM(f.demand_total), 2) AS collection_efficiency_pct FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_name = 'Arkalgud' AND f.fiscal_year = '2024-25'`
- **columns**: ['collection_efficiency_pct']
- **rows**: [(93.62,)]
- **answer**: The collection efficiency in Arkalgud in 2024-25 was 93.62%.
- **chart_data**: None
- **timing_ms**: {'router_ms': 0.9, 'llm_ms': 1976.4, 'safety_ms': 0.4, 'db_ms': 3.3, 'fill_ms': 0.1}  (total: 1981.2 ms)

## Q2: What's the year-over-year growth in total installations for the whole division?

- **answerable**: True
- **sql**: `SELECT r.fiscal_year AS fiscal_year, r.total_installations AS installations, r.total_installations - LAG(r.total_installations) OVER (ORDER BY d.start_year) AS yoy_change FROM fact_dcb_grand_totals r JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id JOIN dim_fiscal_year d ON r.fiscal_year = d.fiscal_year WHERE s.subdivision_name = 'Division_Total' ORDER BY d.start_year`
- **columns**: ['fiscal_year', 'installations', 'yoy_change']
- **rows**: [('2015-16', 159149.0, None), ('2016-17', 166060.0, 6911.0), ('2017-18', 137634.0, -28426.0), ('2018-19', 141262.0, 3628.0), ('2019-20', 148159.0, 6897.0), ('2020-21', 153614.0, 5455.0), ('2021-22', 166896.0, 13282.0), ('2022-23', 172182.0, 5286.0), ('2023-24', 176049.0, 3867.0), ('2024-25', 181893.0, 5844.0), ('2025-26', 183794.0, 1901.0)]
- **answer**: Results: (showing 10 of 11 rows)
1. fiscal_year=2015-16, installations=159,149, yoy_change=N/A
2. fiscal_year=2016-17, installations=166,060, yoy_change=6,911
3. fiscal_year=2017-18, installations=137,634, yoy_change=-28,426
4. fiscal_year=2018-19, installations=141,262, yoy_change=3,628
5. fiscal_year=2019-20, installations=148,159, yoy_change=6,897
6. fiscal_year=2020-21, installations=153,614, yoy_change=5,455
7. fiscal_year=2021-22, installations=166,896, yoy_change=13,282
8. fiscal_year=2022-23, installations=172,182, yoy_change=5,286
9. fiscal_year=2023-24, installations=176,049, yoy_change=3,867
10. fiscal_year=2024-25, installations=181,893, yoy_change=5,844
- **chart_data**: type='line' labels=['2015-16', '2016-17', '2017-18', '2018-19', '2019-20', '2020-21', '2021-22', '2022-23', '2023-24', '2024-25', '2025-26'] values=[159149.0, 166060.0, 137634.0, 141262.0, 148159.0, 153614.0, 166896.0, 172182.0, 176049.0, 181893.0, 183794.0] series_label='Total Installations'
- **timing_ms**: {'router_ms': 0.3, 'llm_ms': 1514.9, 'safety_ms': 0.2, 'db_ms': 6.3, 'fill_ms': 0.1}  (total: 1522.0 ms)

## Q3: Which tariff category generated the most demand in 2023-24?

- **answerable**: True
- **sql**: `SELECT t.tariff_code AS tariff_code, t.description AS description, SUM(f.demand_total) AS total_demand FROM fact_dcb f JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE f.fiscal_year = '2023-24' AND s.subdivision_name = 'Division_Total' GROUP BY t.tariff_code, t.description ORDER BY total_demand DESC LIMIT 1`
- **columns**: ['tariff_code', 'description', 'total_demand']
- **rows**: [('LT-4(a)', 'IP Sets - <=10Hp, Rural Feeder (Un-Metered)', 1687837231.91)]
- **answer**: In 2023-24, the tariff category that generated the most demand was LT-4(a) (IP Sets - <=10Hp, Rural Feeder (Un-Metered)) with a total demand of 1,687,837,231.91.
- **chart_data**: None
- **timing_ms**: {'router_ms': 0.3, 'llm_ms': 1454.3, 'safety_ms': 0.3, 'db_ms': 4.1, 'fill_ms': 0.1}  (total: 1459.1 ms)

## Q4: How much outstanding balance (arrears) does Konanur currently carry?

- **answerable**: True
- **sql**: `SELECT f.fiscal_year AS fiscal_year, SUM(f.closing_balance_total) AS outstanding_balance FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_name = 'Konanur' GROUP BY f.fiscal_year ORDER BY f.fiscal_year DESC LIMIT 1`
- **columns**: ['fiscal_year', 'outstanding_balance']
- **rows**: [('2025-26', 30717819.028217457)]
- **answer**: Konanur currently carries an outstanding balance (arrears) of 30,717,819.03 for fiscal year 2025-26.
- **chart_data**: None
- **timing_ms**: {'router_ms': 0.3, 'llm_ms': 1449.4, 'safety_ms': 0.1, 'db_ms': 2.8, 'fill_ms': 0.0}  (total: 1452.7 ms)

## Q5: Compare domestic (LT-1) vs industrial (HT-2) demand across all years.

- **answerable**: True
- **sql**: `SELECT f.fiscal_year AS fiscal_year, SUM(CASE WHEN t.tariff_code LIKE 'LT-1%' THEN f.demand_total ELSE 0 END) AS domestic_lt1_demand, SUM(CASE WHEN t.tariff_code LIKE 'HT-2%' THEN f.demand_total ELSE 0 END) AS industrial_ht2_demand FROM fact_dcb f JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_name = 'Division_Total' GROUP BY f.fiscal_year ORDER BY f.fiscal_year`
- **columns**: ['fiscal_year', 'domestic_lt1_demand', 'industrial_ht2_demand']
- **rows**: [('2015-16', 37954648.5142, 23399034.7482), ('2016-17', 42590327.3818, 31865165.7148), ('2017-18', 48666798.38160001, 47893872.1232), ('2018-19', 53733170.4134, 53509240.1942), ('2019-20', 57459579.95680001, 68665481.3168), ('2020-21', 60218920.8345, 70025907.1177), ('2021-22', 57472384.26432222, 88207595.16417779), ('2022-23', 58088498.6936, 124145839.1884), ('2023-24', 72662730.56030001, 154074536.92249998), ('2024-25', 473913919.16, 144256241.7349), ('2025-26', 542708386.6741, 141349133.1922)]
- **answer**: Results: (showing 10 of 11 rows)
1. fiscal_year=2015-16, domestic_lt1_demand=37,954,648.51, industrial_ht2_demand=23,399,034.75
2. fiscal_year=2016-17, domestic_lt1_demand=42,590,327.38, industrial_ht2_demand=31,865,165.71
3. fiscal_year=2017-18, domestic_lt1_demand=48,666,798.38, industrial_ht2_demand=47,893,872.12
4. fiscal_year=2018-19, domestic_lt1_demand=53,733,170.41, industrial_ht2_demand=53,509,240.19
5. fiscal_year=2019-20, domestic_lt1_demand=57,459,579.96, industrial_ht2_demand=68,665,481.32
6. fiscal_year=2020-21, domestic_lt1_demand=60,218,920.83, industrial_ht2_demand=70,025,907.12
7. fiscal_year=2021-22, domestic_lt1_demand=57,472,384.26, industrial_ht2_demand=88,207,595.16
8. fiscal_year=2022-23, domestic_lt1_demand=58,088,498.69, industrial_ht2_demand=124,145,839.19
9. fiscal_year=2023-24, domestic_lt1_demand=72,662,730.56, industrial_ht2_demand=154,074,536.92
10. fiscal_year=2024-25, domestic_lt1_demand=473,913,919.16, industrial_ht2_demand=144,256,241.73
- **chart_data**: type='line' labels=['2015-16', '2016-17', '2017-18', '2018-19', '2019-20', '2020-21', '2021-22', '2022-23', '2023-24', '2024-25', '2025-26'] values=[37954648.5142, 42590327.3818, 48666798.38160001, 53733170.4134, 57459579.95680001, 60218920.8345, 57472384.26432222, 58088498.6936, 72662730.56030001, 473913919.16, 542708386.6741] series_label='Domestic LT-1 Demand'
- **timing_ms**: {'router_ms': 0.3, 'llm_ms': 1657.1, 'safety_ms': 0.3, 'db_ms': 4.7, 'fill_ms': 0.1}  (total: 1662.6 ms)

## Q6: What's the official printed GRAND TOTAL demand for 2025-26?

- **answerable**: True
- **sql**: `SELECT r.fiscal_year AS fiscal_year, s.subdivision_name AS subdivision_name, r.demand_total AS demand_total, r.collection_grand_total AS collection_grand_total FROM fact_dcb_grand_totals r JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id WHERE r.fiscal_year = '2025-26' AND s.subdivision_name = 'Division_Total'`
- **columns**: ['fiscal_year', 'subdivision_name', 'demand_total', 'collection_grand_total']
- **rows**: [('2025-26', 'Division_Total', 3541649882.7938004, 3300093200.7644)]
- **answer**: The official printed GRAND TOTAL demand for 2025-26 in Division_Total was 3,541,649,882.79 (with collection grand total of 3,300,093,200.76).
- **chart_data**: None
- **timing_ms**: {'router_ms': 0.3, 'llm_ms': 1397.1, 'safety_ms': 0.1, 'db_ms': 4.4, 'fill_ms': 0.1}  (total: 1402.1 ms)

## Q7: Which sub-division has the best collection efficiency in the most recent year?

- **answerable**: True
- **sql**: `SELECT s.subdivision_name AS subdivision_name, ROUND(SUM(f.collection_grand_total) * 100.0 / SUM(f.demand_total), 2) AS collection_efficiency_pct FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE f.fiscal_year = (SELECT MAX(fiscal_year) FROM fact_dcb) AND s.subdivision_type IN ('sub_division', 'section') GROUP BY s.subdivision_name ORDER BY collection_efficiency_pct DESC LIMIT 1`
- **columns**: ['subdivision_name', 'collection_efficiency_pct']
- **rows**: [('Konanur', 97.46)]
- **answer**: In the most recent fiscal year, the sub-division with the best collection efficiency is Konanur with 97.46%.
- **chart_data**: None
- **timing_ms**: {'router_ms': 0.6, 'llm_ms': 1440.2, 'safety_ms': 0.3, 'db_ms': 9.0, 'fill_ms': 0.2}  (total: 1450.3 ms)

## Q8: How many installations are dead or non-recoverable (DC/MNR) by sub-division, for 2024-25?

- **answerable**: True
- **sql**: `SELECT s.subdivision_name AS subdivision_name, SUM(COALESCE(f.dc_installations, 0) + COALESCE(f.mnr_installations, 0) + COALESCE(f.dc_mnr_installations, 0)) AS dead_or_nonrecoverable FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_type IN ('sub_division', 'section') AND f.fiscal_year = '2024-25' GROUP BY s.subdivision_name ORDER BY dead_or_nonrecoverable DESC`
- **columns**: ['subdivision_name', 'dead_or_nonrecoverable']
- **rows**: [('Holenarasipura', 11806.0), ('Arkalgud', 7743.0), ('Ramanathapura', 6917.0), ('Hangarahalli', 5401.0), ('Konanur', 4222.0)]
- **answer**: Results: (showing 5 of 5 rows)
1. subdivision_name=Holenarasipura, dead_or_nonrecoverable=11,806
2. subdivision_name=Arkalgud, dead_or_nonrecoverable=7,743
3. subdivision_name=Ramanathapura, dead_or_nonrecoverable=6,917
4. subdivision_name=Hangarahalli, dead_or_nonrecoverable=5,401
5. subdivision_name=Konanur, dead_or_nonrecoverable=4,222
- **chart_data**: type='bar' labels=['Holenarasipura', 'Arkalgud', 'Ramanathapura', 'Hangarahalli', 'Konanur'] values=[11806.0, 7743.0, 6917.0, 5401.0, 4222.0] series_label='DC/MNR Installations'
- **timing_ms**: {'router_ms': 0.5, 'llm_ms': 1658.4, 'safety_ms': 0.1, 'db_ms': 3.3, 'fill_ms': 0.1}  (total: 1662.6 ms)

## Q9: What's the trend in Miscellaneous revenue (fees and penalties) over the years?

- **answerable**: True
- **sql**: `SELECT r.fiscal_year AS fiscal_year, r.demand_total AS misc_revenue_demand FROM fact_dcb_rollups r JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id WHERE r.rollup_type = 'misc_total' AND s.subdivision_name = 'Division_Total' ORDER BY r.fiscal_year`
- **columns**: ['fiscal_year', 'misc_revenue_demand']
- **rows**: [('2015-16', 2552571.0), ('2016-17', 3363710.0), ('2017-18', 2978270.0), ('2018-19', 5082945.72), ('2019-20', 17251625.42), ('2020-21', 6102523.0), ('2021-22', 6317401.0), ('2022-23', 17274587.68), ('2023-24', 40813745.9), ('2024-25', 112362444.88000001), ('2025-26', 12715969.370000001)]
- **answer**: Results: (showing 10 of 11 rows)
1. fiscal_year=2015-16, misc_revenue_demand=2,552,571
2. fiscal_year=2016-17, misc_revenue_demand=3,363,710
3. fiscal_year=2017-18, misc_revenue_demand=2,978,270
4. fiscal_year=2018-19, misc_revenue_demand=5,082,945.72
5. fiscal_year=2019-20, misc_revenue_demand=17,251,625.42
6. fiscal_year=2020-21, misc_revenue_demand=6,102,523
7. fiscal_year=2021-22, misc_revenue_demand=6,317,401
8. fiscal_year=2022-23, misc_revenue_demand=17,274,587.68
9. fiscal_year=2023-24, misc_revenue_demand=40,813,745.90
10. fiscal_year=2024-25, misc_revenue_demand=112,362,444.88
- **chart_data**: type='line' labels=['2015-16', '2016-17', '2017-18', '2018-19', '2019-20', '2020-21', '2021-22', '2022-23', '2023-24', '2024-25', '2025-26'] values=[2552571.0, 3363710.0, 2978270.0, 5082945.72, 17251625.42, 6102523.0, 6317401.0, 17274587.68, 40813745.9, 112362444.88000001, 12715969.370000001] series_label='Miscellaneous Revenue Demand'
- **timing_ms**: {'router_ms': 0.5, 'llm_ms': 1417.3, 'safety_ms': 0.1, 'db_ms': 2.3, 'fill_ms': 0.1}  (total: 1420.3 ms)

## Q10: Show the full DCB chain -- opening balance, demand, collection, and closing balance -- for Holenarasipura in 2024-25.

- **answerable**: True
- **sql**: `SELECT SUM(f.opening_balance_total) AS opening_balance, SUM(f.demand_total) AS demand, SUM(f.collection_grand_total) AS collection, SUM(f.closing_balance_total) AS closing_balance FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_name = 'Holenarasipura' AND f.fiscal_year = '2024-25'`
- **columns**: ['opening_balance', 'demand', 'collection', 'closing_balance']
- **rows**: [(162003262.46606404, 1034457103.7718816, 942195118.9479599, 254265247.28998575)]
- **answer**: For Holenarasipura in 2024-25, the full DCB chain was: Opening Balance of 162,003,262.47, Demand of 1,034,457,103.77, Collection of 942,195,118.95, and Closing Balance of 254,265,247.29.
- **chart_data**: None
- **timing_ms**: {'router_ms': 0.2, 'llm_ms': 1444.2, 'safety_ms': 0.1, 'db_ms': 6.0, 'fill_ms': 0.1}  (total: 1450.7 ms)

## QA1: What was the total demand for tariff code ZZ-99 in 2024-25?

- **answerable**: False
- **sql**: ``
- **columns**: []
- **rows**: []
- **answer**: I cannot answer this question because the tariff code ZZ-99 does not exist in the database.
- **chart_data**: None
- **timing_ms**: {'router_ms': 0.4, 'llm_ms': 1140.4}  (total: 1140.8 ms)

## QA2: What is the combined total demand if I add up every sub-division's demand plus the Division_Total for 2024-25?

- **answerable**: False
- **sql**: ``
- **columns**: []
- **rows**: []
- **answer**: Adding every sub-division's demand together and then adding Division_Total would double-count the figures, as Division_Total is already the sum of all sub-divisions. Please ask for either the breakdown by sub-division or the Division Total separately.
- **chart_data**: None
- **timing_ms**: {'router_ms': 0.4, 'llm_ms': 1340.4}  (total: 1340.8 ms)


## Latency summary
- Questions run: 12
- Wall time for full run: 17947.8 ms
- Mean total latency per question: 1495.4 ms
- Min / max total latency: 1140.8 / 1981.2 ms
- Mean LLM-call latency: 1490.8 ms
