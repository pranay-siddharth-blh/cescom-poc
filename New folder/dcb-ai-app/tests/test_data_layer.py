"""The mart (DCB_Mart/dcb_mart.sqlite) and the read-only guard around both databases."""
import sqlite3

import pytest

from app import mart_layer
from app.config import settings
from app.db import RestrictedQueryError
from tests.conftest import star_one


def _ledger():
    con = sqlite3.connect(f"file:{settings.mart_db_path.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT check_name, status, detail FROM ledger_build_check").fetchall()
    finally:
        con.close()


def test_every_build_check_passed():
    checks = _ledger()
    assert len(checks) >= 15
    assert all(s == "PASS" for _, s, _ in checks), [c for c in checks if c[1] != "PASS"]


@pytest.mark.parametrize("sql,expected", [
    ("SELECT SUM(collection_grand_total) * 100.0 / SUM(demand_total) FROM mart_fact_family_subdivision_year "
     "WHERE subdivision_name = 'Arkalgud' AND fiscal_year = '2024-25'", 93.62),
    ("SELECT collection_efficiency_pct FROM mart_fact_subdivision_year WHERE subdivision_name = 'Konanur' "
     "AND fiscal_year = '2025-26'", 97.46),
    ("SELECT demand_total FROM mart_fact_family_division_year WHERE family_code = 'HT-2' AND fiscal_year = '2015-16'",
     23399034.75),
    ("SELECT demand_total FROM mart_fact_family_division_year WHERE family_code = 'LT-1' AND fiscal_year = '2025-26'",
     542708386.67),
])
def test_golden_figures(db, sql, expected):
    assert round(db.run_sync(sql, 5).rows[0][0], 2) == expected


def test_lt5_family_is_real_where_printed_subtotal_is_zero(db):
    """KI-05: the sheet's 'Sub Total of LT-5' is 0 in 2015-16; the mart's family total equals 'LT-5 (a+b)'."""
    printed_zero = star_one(db, "SELECT r.demand_total FROM fact_dcb_rollups r JOIN dim_subdivision s USING (subdivision_id) "
                                "WHERE s.subdivision_type = 'division' AND r.fiscal_year = '2015-16' AND r.tariff_code_summarized = 'LT-5'")
    ab = star_one(db, "SELECT r.demand_total FROM fact_dcb_rollups r JOIN dim_subdivision s USING (subdivision_id) "
                      "WHERE s.subdivision_type = 'division' AND r.fiscal_year = '2015-16' AND r.tariff_code_summarized = 'LT-5 (a+b)'")
    mart = star_one(db, "SELECT demand_total FROM mart_fact_family_division_year WHERE family_code = 'LT-5' AND fiscal_year = '2015-16'")
    assert printed_zero == 0 and round(mart, 2) == round(ab, 2) > 0


def test_families_add_up_to_printed_grand_total_every_year(db):
    rows = db.run_sync("SELECT d.fiscal_year, d.demand_total, SUM(f.demand_total) FROM mart_fact_division_year d "
                       "JOIN mart_fact_family_division_year f ON f.fiscal_year = d.fiscal_year GROUP BY d.fiscal_year", 20).rows
    assert len(rows) == 11
    for fy, printed, fams in rows:
        assert abs(printed - fams) < 2, fy


def test_division_is_not_the_sum_of_subdivisions_from_2019_20(db):
    """KI-02: miscellaneous revenue booked only at division level; LT and HT agree exactly."""
    d, s, dlt, slt = db.run_sync("SELECT d.demand_total, SUM(s.demand_total), d.lt_demand_total, SUM(s.lt_demand_total) "
                                 "FROM mart_fact_division_year d JOIN mart_fact_subdivision_year s USING (fiscal_year) "
                                 "WHERE d.fiscal_year = '2024-25'", 1).rows[0]
    assert d - s > 1e8 and abs(dlt - slt) < 2


def test_semantic_layer_documents_every_mart_column():
    for t in mart_layer.load()["tables"].values():
        assert all(c["doc"] for c in t["columns"].values())


# ---------------------------------------------------------------------------------------------- read-only guard
@pytest.mark.parametrize("sql", [
    "SELECT * FROM reconciliation_report", "SELECT * FROM mart.ledger_build_check", "SELECT * FROM semantic_table_doc",
    "SELECT name FROM sqlite_master", "PRAGMA table_info(fact_dcb)",
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c LIMIT 3) SELECT * FROM c",
    "SELECT load_extension('evil')",
])
def test_authorizer_denies(db, sql):
    with pytest.raises(RestrictedQueryError):
        db.run_sync(sql, 5)


@pytest.mark.parametrize("sql", ["CREATE TABLE x (a)", "DELETE FROM fact_dcb", "INSERT INTO mart_dim_tariff VALUES (1)",
                                 "UPDATE fact_dcb SET demand_total = 0"])
def test_writes_refused_even_without_the_authorizer(db, sql):
    with pytest.raises(sqlite3.DatabaseError):
        db.run_sync(sql, 5, restricted=False)


def test_bound_parameters(db):
    rows = db.run_sync("SELECT subdivision_name FROM mart_dim_subdivision WHERE subdivision_name = ?", 5,
                       params=("Arkalgud' OR '1'='1",)).rows
    assert rows == []
    assert db.run_sync("SELECT subdivision_name FROM mart_dim_subdivision WHERE subdivision_name = ?", 5,
                       params=("Konanur",)).rows == [("Konanur",)]


def test_timeout_interrupts(db):
    import asyncio
    slow = ("SELECT COUNT(*) FROM fact_dcb a, fact_dcb b, fact_dcb c WHERE a.demand_total + b.demand_total + c.demand_total > -1")
    with pytest.raises(TimeoutError):
        asyncio.run(db.run(slow, 5, timeout_seconds=0.3))
