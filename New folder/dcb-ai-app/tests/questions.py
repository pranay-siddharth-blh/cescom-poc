"""
The eval set. Questions 1-10 are the NL phrasing of each of the 10 pairs
in DCB_Semantic_Layer/example_queries.sql, in the same order, so results
can be sanity-checked against that file's pre-verified SQL/output. 11-12
are deliberately adversarial (per the task's verification requirement).
"""

EXAMPLE_QUESTIONS = [
    "What was the collection efficiency in Arkalgud in 2024-25?",
    "What's the year-over-year growth in total installations for the whole division?",
    "Which tariff category generated the most demand in 2023-24?",
    "How much outstanding balance (arrears) does Konanur currently carry?",
    "Compare domestic (LT-1) vs industrial (HT-2) demand across all years.",
    "What's the official printed GRAND TOTAL demand for 2025-26?",
    "Which sub-division has the best collection efficiency in the most recent year?",
    "How many installations are dead or non-recoverable (DC/MNR) by sub-division, for 2024-25?",
    "What's the trend in Miscellaneous revenue (fees and penalties) over the years?",
    "Show the full DCB chain -- opening balance, demand, collection, and closing balance -- for Holenarasipura in 2024-25.",
]

ADVERSARIAL_QUESTIONS = [
    # No such tariff code exists anywhere in dim_tariff_category -- should
    # come back answerable=false, not a hallucinated number.
    "What was the total demand for tariff code ZZ-99 in 2024-25?",
    # Tempts exactly the double-counting bug the semantic layer's rules
    # warn against: summing every sub-division AND the Division_Total
    # rollup together would double the real figure.
    "What is the combined total demand if I add up every sub-division's demand plus the Division_Total for 2024-25?",
]
