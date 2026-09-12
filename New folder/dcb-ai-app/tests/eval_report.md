# DCB Chatbot — eval run

## Q1: What was the collection efficiency in Arkalgud in 2024-25?

- **answerable**: True
- **sql**: `SELECT ROUND(SUM(f.collection_grand_total) * 100.0 / SUM(f.demand_total), 2) AS collection_efficiency_pct FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_name = 'Arkalgud' AND f.fiscal_year = '2024-25'`
- **columns**: ['collection_efficiency_pct']
- **rows**: [(93.62,)]
- **answer**: The collection efficiency in Arkalgud in 2024-25 was 93.62%.
- **timing_ms**: {'router_ms': 0.0, 'llm_ms': 1421.0, 'safety_ms': 0.0, 'db_ms': 0.0, 'fill_ms': 0.0}  (total: 1421.0 ms)

## Q2: What's the year-over-year growth in total installations for the whole division?

- **answerable**: True
- **sql**: `SELECT r.fiscal_year AS fiscal_year, r.total_installations AS total_installations, r.total_installations - LAG(r.total_installations) OVER (ORDER BY d.start_year) AS yoy_change FROM fact_dcb_grand_totals r JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id JOIN dim_fiscal_year d ON r.fiscal_year = d.fiscal_year WHERE s.subdivision_name = 'Division_Total' ORDER BY d.start_year`
- **columns**: ['fiscal_year', 'total_installations', 'yoy_change']
- **rows**: [('2015-16', 159149.0, None), ('2016-17', 166060.0, 6911.0), ('2017-18', 137634.0, -28426.0), ('2018-19', 141262.0, 3628.0), ('2019-20', 148159.0, 6897.0), ('2020-21', 153614.0, 5455.0), ('2021-22', 166896.0, 13282.0), ('2022-23', 172182.0, 5286.0), ('2023-24', 176049.0, 3867.0), ('2024-25', 181893.0, 5844.0), ('2025-26', 183794.0, 1901.0)]
- **answer**: Results: (showing 10 of 11 rows)
1. fiscal_year=2015-16, total_installations=159,149, yoy_change=N/A
2. fiscal_year=2016-17, total_installations=166,060, yoy_change=6,911
3. fiscal_year=2017-18, total_installations=137,634, yoy_change=-28,426
4. fiscal_year=2018-19, total_installations=141,262, yoy_change=3,628
5. fiscal_year=2019-20, total_installations=148,159, yoy_change=6,897
6. fiscal_year=2020-21, total_installations=153,614, yoy_change=5,455
7. fiscal_year=2021-22, total_installations=166,896, yoy_change=13,282
8. fiscal_year=2022-23, total_installations=172,182, yoy_change=5,286
9. fiscal_year=2023-24, total_installations=176,049, yoy_change=3,867
10. fiscal_year=2024-25, total_installations=181,893, yoy_change=5,844
- **timing_ms**: {'router_ms': 0.0, 'llm_ms': 1266.0, 'safety_ms': 0.0, 'db_ms': 16.0, 'fill_ms': 0.0}  (total: 1282.0 ms)

## Q3: Which tariff category generated the most demand in 2023-24?

- **answerable**: True
- **sql**: `SELECT t.tariff_code AS tariff_code, t.description AS description, SUM(f.demand_total) AS total_demand FROM fact_dcb f JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE f.fiscal_year = '2023-24' AND s.subdivision_name = 'Division_Total' GROUP BY t.tariff_code, t.description ORDER BY total_demand DESC LIMIT 1`
- **columns**: ['tariff_code', 'description', 'total_demand']
- **rows**: [('LT-4(a)', 'IP Sets - <=10Hp, Rural Feeder (Un-Metered)', 1687837231.91)]
- **answer**: In fiscal year 2023-24, the tariff category that generated the most demand was LT-4(a) (IP Sets - <=10Hp, Rural Feeder (Un-Metered)) with a total demand of 1,687,837,231.91.
- **timing_ms**: {'router_ms': 0.0, 'llm_ms': 1484.0, 'safety_ms': 0.0, 'db_ms': 0.0, 'fill_ms': 0.0}  (total: 1484.0 ms)

## Q4: How much outstanding balance (arrears) does Konanur currently carry?

- **answerable**: True
- **sql**: `SELECT f.fiscal_year AS fiscal_year, SUM(f.closing_balance_total) AS outstanding_balance FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_name = 'Konanur' GROUP BY f.fiscal_year ORDER BY f.fiscal_year DESC LIMIT 1`
- **columns**: ['fiscal_year', 'outstanding_balance']
- **rows**: [('2025-26', 30717819.028217457)]
- **answer**: For the fiscal year 2025-26, Konanur currently carries an outstanding balance (arrears) of 30,717,819.03.
- **timing_ms**: {'router_ms': 0.0, 'llm_ms': 1281.0, 'safety_ms': 0.0, 'db_ms': 0.0, 'fill_ms': 0.0}  (total: 1281.0 ms)

## Q5: Compare domestic (LT-1) vs industrial (HT-2) demand across all years.

- **answerable**: True
- **sql**: `SELECT f.fiscal_year, SUM(CASE WHEN t.tariff_code LIKE 'LT-1%' THEN f.demand_total ELSE 0 END) AS domestic_lt1_demand, SUM(CASE WHEN t.tariff_code LIKE 'HT-2%' THEN f.demand_total ELSE 0 END) AS industrial_ht2_demand FROM fact_dcb f JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_name = 'Division_Total' GROUP BY f.fiscal_year ORDER BY f.fiscal_year`
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
- **timing_ms**: {'router_ms': 0.0, 'llm_ms': 1516.0, 'safety_ms': 0.0, 'db_ms': 0.0, 'fill_ms': 0.0}  (total: 1516.0 ms)

## Q6: What's the official printed GRAND TOTAL demand for 2025-26?

- **answerable**: True
- **sql**: `SELECT r.fiscal_year AS fiscal_year, s.subdivision_name AS subdivision_name, r.demand_total AS demand_total, r.collection_grand_total AS collection_grand_total FROM fact_dcb_grand_totals r JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id WHERE r.fiscal_year = '2025-26' AND s.subdivision_name = 'Division_Total'`
- **columns**: ['fiscal_year', 'subdivision_name', 'demand_total', 'collection_grand_total']
- **rows**: [('2025-26', 'Division_Total', 3541649882.7938004, 3300093200.7644)]
- **answer**: For the fiscal year 2025-26, the official printed GRAND TOTAL demand for Division_Total was 3,541,649,882.79 with a collection grand total of 3,300,093,200.76.
- **timing_ms**: {'router_ms': 0.0, 'llm_ms': 1328.0, 'safety_ms': 0.0, 'db_ms': 0.0, 'fill_ms': 0.0}  (total: 1328.0 ms)

## Q7: Which sub-division has the best collection efficiency in the most recent year?

- **answerable**: True
- **sql**: `SELECT s.subdivision_name AS subdivision_name, ROUND(SUM(f.collection_grand_total) * 100.0 / SUM(f.demand_total), 2) AS collection_efficiency_pct FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE f.fiscal_year = (SELECT MAX(fiscal_year) FROM fact_dcb) AND s.subdivision_type IN ('sub_division', 'section') GROUP BY s.subdivision_name ORDER BY collection_efficiency_pct DESC LIMIT 1`
- **columns**: ['subdivision_name', 'collection_efficiency_pct']
- **rows**: [('Konanur', 97.46)]
- **answer**: The sub-division with the best collection efficiency in the most recent year is Konanur with an efficiency of 97.46%.
- **timing_ms**: {'router_ms': 0.0, 'llm_ms': 1453.0, 'safety_ms': 0.0, 'db_ms': 0.0, 'fill_ms': 0.0}  (total: 1453.0 ms)

## Q8: How many installations are dead or non-recoverable (DC/MNR) by sub-division, for 2024-25?

- **answerable**: True
- **sql**: `SELECT s.subdivision_name, SUM(COALESCE(f.dc_installations, 0) + COALESCE(f.mnr_installations, 0) + COALESCE(f.dc_mnr_installations, 0)) AS dead_or_nonrecoverable FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_type IN ('sub_division', 'section') AND f.fiscal_year = '2024-25' GROUP BY s.subdivision_name ORDER BY dead_or_nonrecoverable DESC`
- **columns**: ['subdivision_name', 'dead_or_nonrecoverable']
- **rows**: [('Holenarasipura', 11806.0), ('Arkalgud', 7743.0), ('Ramanathapura', 6917.0), ('Hangarahalli', 5401.0), ('Konanur', 4222.0)]
- **answer**: Results: (showing 5 of 5 rows)
1. subdivision_name=Holenarasipura, dead_or_nonrecoverable=11,806
2. subdivision_name=Arkalgud, dead_or_nonrecoverable=7,743
3. subdivision_name=Ramanathapura, dead_or_nonrecoverable=6,917
4. subdivision_name=Hangarahalli, dead_or_nonrecoverable=5,401
5. subdivision_name=Konanur, dead_or_nonrecoverable=4,222
- **timing_ms**: {'router_ms': 0.0, 'llm_ms': 1360.0, 'safety_ms': 0.0, 'db_ms': 0.0, 'fill_ms': 0.0}  (total: 1360.0 ms)

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
- **timing_ms**: {'router_ms': 0.0, 'llm_ms': 1375.0, 'safety_ms': 0.0, 'db_ms': 0.0, 'fill_ms': 0.0}  (total: 1375.0 ms)

## Q10: Show the full DCB chain -- opening balance, demand, collection, and closing balance -- for Holenarasipura in 2024-25.

- **answerable**: True
- **sql**: `SELECT SUM(f.opening_balance_total) AS opening_balance, SUM(f.demand_total) AS demand, SUM(f.collection_grand_total) AS collection, SUM(f.closing_balance_total) AS closing_balance FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_name = 'Holenarasipura' AND f.fiscal_year = '2024-25'`
- **columns**: ['opening_balance', 'demand', 'collection', 'closing_balance']
- **rows**: [(162003262.46606404, 1034457103.7718816, 942195118.9479599, 254265247.28998575)]
- **answer**: For Holenarasipura in 2024-25, the full DCB chain is as follows: Opening Balance = 162,003,262.47, Demand = 1,034,457,103.77, Collection = 942,195,118.95, and Closing Balance = 254,265,247.29.
- **timing_ms**: {'router_ms': 0.0, 'llm_ms': 1437.0, 'safety_ms': 0.0, 'db_ms': 16.0, 'fill_ms': 0.0}  (total: 1453.0 ms)

## QA1: What was the total demand for tariff code ZZ-99 in 2024-25?

- **answerable**: False
- **sql**: ``
- **columns**: []
- **rows**: []
- **answer**: I don't see a tariff code matching 'ZZ-99' in the database. Please check the tariff code and try again.
- **timing_ms**: {'router_ms': 0.0, 'llm_ms': 1078.0}  (total: 1078.0 ms)

## QA2: What is the combined total demand if I add up every sub-division's demand plus the Division_Total for 2024-25?

- **answerable**: False
- **sql**: ``
- **columns**: []
- **rows**: []
- **answer**: The question asks to add up every sub-division's demand plus the Division_Total, which would double-count the data since Division_Total already represents the sum of all sub-divisions. To get the division-wide demand, please use the Division_Total row or sum the sub-divisions separately.
- **timing_ms**: {'router_ms': 0.0, 'llm_ms': 1203.0}  (total: 1203.0 ms)


## Latency summary
- Questions run: 12
- Wall time for full run: 16234.0 ms
- Mean total latency per question: 1352.8 ms
- Min / max total latency: 1078.0 / 1516.0 ms
- Mean LLM-call latency: 1350.2 ms
