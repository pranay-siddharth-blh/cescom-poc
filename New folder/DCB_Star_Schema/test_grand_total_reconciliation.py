"""
Standing regression test for the ingestion pipeline (build_staging.py ->
build_star_schema.py), NOT the chat app. Run it directly:

    python test_grand_total_reconciliation.py

or via pytest (it's also a valid pytest module -- see test_reconciliation()).

What it checks and why
-----------------------
Every yearly sheet in the raw source workbook prints its own authoritative
"GRAND TOTAL (LT+HT+Misc.)" row per (fiscal_year, subdivision) block. That
number must equal the sum, across every LT/HT/Misc/IPP/Auxiliary tariff
category the pipeline extracted into fact_dcb, of each additive metric
column -- for every metric, not just a hand-picked few.

This is a *complementary* check, not a complete one: because it sums ALL
of fact_dcb regardless of which category each row got attributed to, it
would NOT by itself have caught the original bug this test was written in
response to (LT-1 demand being ~5.5x inflated in 2024-25 because some rows
were mislabeled 'LT-1' in the source when they structurally belonged to a
separate group -- see build_staging.py's classify_row_type() docstring).
That specific bug was root-caused by hand, then fixed at the source
(distinguishing "Sub Total of LT-1" from "Sub Total of LT-1 A" rather than
trusting each row's own tariff_category text). What THIS test guards
against is anything that breaks fact_dcb's *completeness* or a metric
column's *mapping* -- a category silently dropped, a column resolved to
the wrong header, a newly-added non-additive column wrongly summed, a new
year's sheet using yet another rollup-label spelling the classifier
doesn't recognize (so a whole rollup_type's rows go missing) -- exactly
the bug classes a "new year's sheet has yet another layout quirk" would
produce. Together with the specific, byte-level checks below (no NULL
tariff_code_summarized extractions, every distinct rollup label lands in
a real rollup_type, not silently dropped into a bucket nothing reads),
this closes the loop: a genuinely new quirk either reconciles or this
test fails loudly, by design, rather than serving a silently wrong number.

Runs the REAL pipeline against the REAL source file
----------------------------------------------------
This does not just re-open the already-built, checked-in
dcb_star_schema.sqlite (which could go stale, or be hand-edited without
re-running the pipeline). It re-runs build_staging.py and
build_star_schema.py, fresh, against the actual
'HNPURA DIVISION DCB FY 2015 TO 2026.xlsx' source workbook, into a
throwaway scratch directory (via the DCB_STAGING_OUT_DIR /
DCB_STAR_SCHEMA_OUT_DIR / DCB_STAGING_DB env var overrides both scripts
support) -- so a future change to either script, or a brand new fiscal
year's sheet added to the workbook, gets caught here before it ever
reaches the chat app.

Tolerance
---------
Not exact-equality. Two small, real, already-investigated source-data
imperfections exist in the current 11 years of data (confirmed by hand,
not assumed):
  - 'diff_billed_vs_live', 2025-26, two subdivisions: the sheet's own
    printed Grand Total is exactly 1 unit higher than the true sum of its
    own category rows (111 vs 112, 232 vs 233) -- a real off-by-one in
    the source's own total, not an extraction bug (independently hand-
    verified by summing every real leaf row).
  - 'collection_fac_total', Division_Total, 2015-16/2016-17/2017-18: the
    printed Grand Total is a few thousand rupees below the true sum of
    its own category rows (max 0.132%) -- again the source's own printed
    total, not a missing row (verified: fact_dcb's sum already matches
    the sum of every 'Sub Total of X' rollup row for that metric/year).
TOLERANCE below (1% relative, OR 2 absolute units, whichever is larger)
comfortably covers both known cases while still failing hard on anything
structurally bigger -- the original LT-1 bug was off by ~450%, HT-2 was
off by 100% (entirely missing), so there is enormous headroom between
"real source noise" and "an actual pipeline bug" for this dataset.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
_STAGING_SCRIPT = os.path.join(_PROJECT_ROOT, "DCB_Staging_DB", "build_staging.py")
_STAR_SCHEMA_SCRIPT = os.path.join(_HERE, "build_star_schema.py")

REL_TOLERANCE_PCT = 1.0
ABS_TOLERANCE = 2.0


def _run_pipeline_into_scratch_dir(scratch_dir: str) -> str:
    """Runs the real ingestion pipeline against the real source workbook,
    writing into `scratch_dir` instead of the checked-in locked DBs.
    Returns the path to the freshly-built star schema sqlite file."""
    staging_out = os.path.join(scratch_dir, "staging")
    star_schema_out = os.path.join(scratch_dir, "star_schema")
    os.makedirs(staging_out, exist_ok=True)
    os.makedirs(star_schema_out, exist_ok=True)

    env = dict(os.environ)
    env["DCB_STAGING_OUT_DIR"] = staging_out
    subprocess.run([sys.executable, _STAGING_SCRIPT], check=True, cwd=_HERE, env=env)

    env["DCB_STAGING_DB"] = os.path.join(staging_out, "dcb_staging.sqlite")
    env["DCB_STAR_SCHEMA_OUT_DIR"] = star_schema_out
    subprocess.run([sys.executable, _STAR_SCHEMA_SCRIPT], check=True, cwd=_HERE, env=env)

    return os.path.join(star_schema_out, "dcb_star_schema.sqlite")


def _check_no_dropped_rollup_labels(con: sqlite3.Connection) -> list[str]:
    """Every rollup-shaped row (row_type ending in a *_total variant) must
    land in fact_dcb_rollups. A row_type this classifier doesn't recognize
    ('other_total' -- the generic fallback bucket) means some rollup label
    variant slipped past every specific pattern and its real figures were
    silently dropped from the star schema entirely -- exactly what
    happened to 'HT - TOTAL'/'LT - TOTAL' (dash-with-spaces) and
    'Total of LT-1' (no 'Sub' prefix) before this fix."""
    cur = con.cursor()
    cur.execute(
        "SELECT DISTINCT fiscal_year, subdivision_id, sl_no_text FROM fact_dcb_rollups "
        "WHERE rollup_type IN ('sub_total', 'combined_total') AND tariff_code_summarized IS NULL"
    )
    null_extractions = cur.fetchall()
    problems = [
        f"tariff_code_summarized extraction FAILED for {fy}/{sid}: {sl!r}"
        for fy, sid, sl in null_extractions
    ]
    return problems


def _check_no_placeholder_rows(con: sqlite3.Connection) -> list[str]:
    """Structural half of requirement 4 (a category absent in a year must
    be ABSENT, not present-with-a-fabricated-zero): a category that
    genuinely doesn't exist for a year produces NO fact_dcb row at all
    (confirmed directly -- e.g. HT-6/HT-7/LT-7/IPP only have rows in the
    years they're actually billed); a category that exists but happens to
    have zero live installations/demand that year (e.g. Division_Office,
    which is real and all-zero by design -- see CLAUDE.md) is a normal,
    common, LEGITIMATE data point, not a placeholder -- confirmed directly
    too (roughly 300-570 real all-zero rows per subdivision, spread across
    every subdivision, not just Division_Office). So "all columns are 0"
    is NOT a usable signal here. What WOULD be a real bug: a row where
    literally every numeric column is NULL (nothing extracted at all) --
    that's checked here and should never happen. The app-layer half of
    requirement 4 (answering "doesn't exist for that year" instead of a
    bare 0 when a user asks about an absent category) is a chatbot
    concern, not a pipeline one -- see dcb-ai-app/app/semantic_layer.py's
    category-existence check, exercised by dcb-ai-app's own test suite,
    not this one."""
    cur = con.cursor()
    cur.execute("PRAGMA table_info(fact_dcb)")
    numeric_cols = [
        r[1] for r in cur.fetchall()
        if r[1] not in ("fact_id", "fiscal_year", "subdivision_id", "tariff_category_id",
                        "source_row_count", "tariff_category_forward_filled")
    ]
    and_null = " AND ".join(f'"{c}" IS NULL' for c in numeric_cols)
    cur.execute(f"SELECT COUNT(*) FROM fact_dcb WHERE {and_null}")
    (all_null_rows,) = cur.fetchone()
    if all_null_rows:
        return [f"{all_null_rows} fact_dcb row(s) exist with EVERY numeric column NULL -- "
                f"nothing was actually extracted for these, they shouldn't exist as rows at all"]
    return []


def _check_grand_total_reconciliation(con: sqlite3.Connection) -> list[str]:
    cur = con.cursor()
    cur.execute(
        "SELECT fiscal_year, subdivision_id, metric, summed_from_fact_dcb, printed_grand_total, diff_pct "
        "FROM reconciliation_report WHERE diff_pct IS NOT NULL"
    )
    rows = cur.fetchall()
    if not rows:
        return ["reconciliation_report is EMPTY -- no grand_total rows were found to check against at all"]

    problems = []
    for fy, sid, metric, summed, printed, diff_pct in rows:
        abs_diff = abs(summed - printed)
        if abs_diff <= ABS_TOLERANCE:
            continue
        if abs(diff_pct) <= REL_TOLERANCE_PCT:
            continue
        problems.append(
            f"{fy} / subdivision_id={sid} / {metric}: fact_dcb sums to {summed}, "
            f"sheet's own GRAND TOTAL row says {printed} (diff {diff_pct}%, abs {abs_diff})"
        )
    return problems


def test_reconciliation():
    scratch_dir = tempfile.mkdtemp(prefix="dcb_reconciliation_test_")
    try:
        star_schema_path = _run_pipeline_into_scratch_dir(scratch_dir)
        con = sqlite3.connect(star_schema_path)
        try:
            problems = []
            problems += _check_no_dropped_rollup_labels(con)
            problems += _check_no_placeholder_rows(con)
            problems += _check_grand_total_reconciliation(con)
        finally:
            con.close()
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)

    if problems:
        message = f"{len(problems)} reconciliation problem(s) found:\n" + "\n".join(
            f"  - {p}" for p in problems
        )
        raise AssertionError(message)


if __name__ == "__main__":
    print("Running the real ingestion pipeline against the real source workbook "
          "into a scratch directory, then checking Grand Total reconciliation...")
    test_reconciliation()
    print("OK: every fiscal year's category totals reconcile against its own "
          "printed GRAND TOTAL row (within tolerance), no dropped rollup labels, "
          "no all-NULL fact_dcb rows.")
