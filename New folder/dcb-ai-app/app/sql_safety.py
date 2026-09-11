"""
Read-only SQL enforcement (design point #6).

Two independent layers, deliberately redundant:
  1. This module — a text-level allowlist check before a query ever
     reaches the DB connection.
  2. app.db.ReadOnlyDB — opens SQLite itself with `mode=ro`, so even a
     query that slipped past this layer physically cannot write.

Neither layer alone is trusted as sufficient; both must hold.
"""
from __future__ import annotations

import re

# Statement-level keywords that must never appear, anywhere, in generated
# SQL. Checked as whole words (so e.g. a column literally named
# "updated_at" isn't falsely rejected for containing "update").
_FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "TRUNCATE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX",
    "GRANT", "REVOKE",
]
_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE
)

# A leading SELECT or WITH (CTE) is the only shape allowed.
_ALLOWED_START_RE = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)

# SQLite has no {name} syntax, so a curly-brace token in generated SQL can
# only be a leftover, unresolved answer_template-style placeholder (the LLM
# confusing the `sql` field's literal-value contract with `answer_template`'s
# separate {placeholder} substitution mechanism — a real failure caught in
# this project's first live-LLM eval run: it produced SQL literally
# containing '{fiscal_year}', which matched nothing and returned an all-NULL
# row instead of an error). Catch it here rather than silently running a
# query that looks successful but is actually answering nothing.
_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")

# --- Division_Total double-counting guard ---------------------------------
# fact_dcb has one row per (fiscal_year, subdivision_id, tariff_category_id)
# -- and dim_subdivision's single subdivision_type='division' row
# ("Division_Total") gets its own fact_dcb rows too, holding the
# division-wide total for each tariff category. SUM/COUNT/AVG/TOTAL-ing
# fact_dcb across every subdivision_id with no restriction therefore
# silently adds Division_Total's own already-aggregated figures on top of
# the real sub-divisions it sums -- roughly doubling the true number. This
# is exactly what semantic_layer.json's own rules warn against (never
# double-count Division_Total against the sub-divisions), and it happened
# for real during this project's first live-LLM eval: an intermediate
# re-run generated `SELECT t.tariff_code, SUM(f.demand_total) ...
# GROUP BY t.tariff_code` with dim_subdivision joined but never filtered --
# caught only by manual review at the time, not by any code check.
#
# This is a second, code-enforced line of defense underneath the prompt's
# own grounding rules (which stay in place, unchanged) -- the same
# principle already used for read-only enforcement above: the prompt asks
# nicely, the code refuses regardless of what the LLM actually generated.
#
# Design choice -- REFUSE, don't auto-fix: unlike some checks, this one
# never tries to inject a "corrected" filter and run the query anyway.
# Guessing the "right" fix (subdivision_name = 'Division_Total'? IN a
# specific list? subdivision_type NOT IN (...)?) risks silently answering
# a *different* question than the one asked, which would look just as
# clean and be just as wrong as the bug it's fixing. Refusing with a clear
# reason (same pattern as the unresolved-placeholder check above) is safer.
#
# Scope, stated honestly -- this is a text-level heuristic over the SQL
# string, not a real SQL parser or a DB-aware check:
#   - It recognizes every subdivision-filter shape this project's own
#     semantic layer and prompt actually produce (see
#     DCB_Semantic_Layer/example_queries.sql / semantic_layer.json):
#     a single `subdivision_name = '...'` equality (dim_subdivision.
#     subdivision_name is UNIQUE, so ANY single such filter -- including
#     to 'Division_Total' itself -- already restricts the query to one
#     subdivision, which can never blend two together); `subdivision_name
#     != 'Division_Total'`; `subdivision_name IN (...)` not containing
#     'Division_Total'; the equivalent `subdivision_type` forms excluding
#     'division'; a single `subdivision_id = <int>`; or GROUP BY on any
#     subdivision-identifying column (each output row is then already
#     scoped to one subdivision, so no single row can blend figures).
#   - It fails CLOSED: a query it can't positively prove safe is blocked,
#     not approved by default -- the same fail-safe direction as the
#     read-only checks above.
#   - Two known, deliberately unhandled gaps, documented rather than
#     silently mishandled: (1) it does not parse boolean AND/OR structure,
#     so a WHERE clause that OR-combines a safe-looking filter on one
#     column with something unsafe on a *different* column (e.g.
#     `subdivision_type = 'sub_division' OR subdivision_name =
#     'Division_Total'`) could in principle slip through, since each
#     column's filter is checked independently; every query this app's
#     own prompt has ever produced combines filters with AND, never OR,
#     so this is a theoretical gap against a hypothetical future LLM
#     output shape, not an observed one. (2) `subdivision_id IN (...)`
#     with raw integers can't be judged safe or unsafe from SQL text alone
#     without a DB lookup (which this text-only, DB-free layer deliberately
#     avoids, to stay a fast deterministic check with no extra round
#     trip) -- that shape is blocked outright rather than risk approving
#     one that happens to include Division_Total's numeric id.
_FACT_DCB_RAW_TABLE_RE = re.compile(r"\bfact_dcb\b", re.IGNORECASE)
_AGGREGATE_FUNC_RE = re.compile(r"\b(SUM|COUNT|AVG|TOTAL)\s*\(", re.IGNORECASE)
_GROUP_BY_CLAUSE_RE = re.compile(
    r"\bGROUP\s+BY\b(.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|\bHAVING\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_SUBDIVISION_GROUPING_COLUMN_RE = re.compile(r"\bsubdivision_(?:name|id|type)\b", re.IGNORECASE)
_QUOTED_LITERAL_RE = re.compile(r"'([^']*)'")

_SUBDIVISION_NAME_EQUALS_RE = re.compile(r"subdivision_name\s*=\s*'[^']*'", re.IGNORECASE)
_SUBDIVISION_NAME_NOT_EQUALS_DIVISION_RE = re.compile(
    r"subdivision_name\s*(?:!=|<>)\s*'Division_Total'", re.IGNORECASE
)
_SUBDIVISION_NAME_IN_RE = re.compile(r"subdivision_name\s+IN\s*\(([^)]*)\)", re.IGNORECASE)
_SUBDIVISION_TYPE_EQUALS_RE = re.compile(r"subdivision_type\s*=\s*'([^']*)'", re.IGNORECASE)
_SUBDIVISION_TYPE_NOT_EQUALS_DIVISION_RE = re.compile(
    r"subdivision_type\s*(?:!=|<>)\s*'division'", re.IGNORECASE
)
_SUBDIVISION_TYPE_IN_RE = re.compile(r"subdivision_type\s+IN\s*\(([^)]*)\)", re.IGNORECASE)
_SUBDIVISION_ID_EQUALS_INT_RE = re.compile(r"subdivision_id\s*=\s*\d+", re.IGNORECASE)


def _is_division_total_provably_excluded(sql: str) -> bool:
    """True iff `sql` matches one of the whitelisted patterns that
    guarantee a fact_dcb aggregate can't blend Division_Total's rows with
    other subdivisions' rows. See the guard's module-level docstring above."""
    group_by_match = _GROUP_BY_CLAUSE_RE.search(sql)
    if group_by_match and _SUBDIVISION_GROUPING_COLUMN_RE.search(group_by_match.group(1)):
        return True

    # "Exactly one" (not "at least one") for every equality-style check
    # below: two or more occurrences of the same filter usually means an
    # OR or some other combination this text-level heuristic can't safely
    # reason about -- fall through to the fail-closed default instead of
    # trusting just the first match found.
    if len(_SUBDIVISION_NAME_EQUALS_RE.findall(sql)) == 1:
        return True

    if _SUBDIVISION_NAME_NOT_EQUALS_DIVISION_RE.search(sql):
        return True

    name_in_match = _SUBDIVISION_NAME_IN_RE.search(sql)
    if name_in_match:
        listed = {v.strip().lower() for v in _QUOTED_LITERAL_RE.findall(name_in_match.group(1))}
        if listed and "division_total" not in listed:
            return True

    type_eq_matches = _SUBDIVISION_TYPE_EQUALS_RE.findall(sql)
    if len(type_eq_matches) == 1 and type_eq_matches[0].strip().lower() != "division":
        return True

    if _SUBDIVISION_TYPE_NOT_EQUALS_DIVISION_RE.search(sql):
        return True

    type_in_match = _SUBDIVISION_TYPE_IN_RE.search(sql)
    if type_in_match:
        listed = {v.strip().lower() for v in _QUOTED_LITERAL_RE.findall(type_in_match.group(1))}
        if listed and "division" not in listed:
            return True

    if len(_SUBDIVISION_ID_EQUALS_INT_RE.findall(sql)) == 1:
        return True

    return False


def check_division_total_double_counting(sql: str) -> None:
    """Raise UnsafeSQLError if `sql` aggregates the raw fact_dcb table in a
    way that could double-count the Division_Total rollup row. No-op for
    anything that doesn't touch fact_dcb or doesn't aggregate it (views
    like fact_dcb_subdivisions are structurally safe already and are never
    matched by _FACT_DCB_RAW_TABLE_RE -- see its definition)."""
    if not _FACT_DCB_RAW_TABLE_RE.search(sql):
        return
    if not _AGGREGATE_FUNC_RE.search(sql):
        return
    if _is_division_total_provably_excluded(sql):
        return
    raise UnsafeSQLError(
        "This query aggregates fact_dcb (SUM/COUNT/AVG/TOTAL) without a filter "
        "that provably excludes the Division_Total rollup row -- executing it "
        "as-is would silently double-count Division_Total alongside the real "
        "sub-divisions it already sums. Add a WHERE filter on "
        "subdivision_name/subdivision_type to a specific value (or list "
        "excluding Division_Total), or GROUP BY a subdivision column, "
        "before this can run."
    )


class UnsafeSQLError(ValueError):
    pass


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return sql


def validate_readonly_sql(sql: str) -> str:
    """Raise UnsafeSQLError if `sql` is anything but a single read-only
    SELECT/WITH statement. Returns the (comment-stripped, trimmed) SQL on
    success — callers should execute the returned string, not the raw
    input, so a comment can't be used to hide a second statement."""
    if not sql or not sql.strip():
        raise UnsafeSQLError("Empty SQL.")

    cleaned = _strip_sql_comments(sql).strip()
    cleaned = cleaned.rstrip(";").strip()

    if ";" in cleaned:
        raise UnsafeSQLError("Multiple statements are not allowed.")

    if not _ALLOWED_START_RE.match(cleaned):
        raise UnsafeSQLError("Only SELECT / WITH ... SELECT statements are allowed.")

    placeholder_match = _UNRESOLVED_PLACEHOLDER_RE.search(cleaned)
    if placeholder_match:
        raise UnsafeSQLError(
            f"SQL contains an unresolved template placeholder {placeholder_match.group(0)!r} "
            "instead of a literal value — refusing to execute a query that would "
            "silently match nothing."
        )

    match = _FORBIDDEN_RE.search(cleaned)
    if match:
        raise UnsafeSQLError(f"Forbidden keyword in generated SQL: {match.group(0)!r}")

    check_division_total_double_counting(cleaned)

    return cleaned
