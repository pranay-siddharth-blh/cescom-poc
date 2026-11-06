"""
Read-only SQL enforcement (design point #6).

Two independent layers, deliberately redundant:
  1. This module — a text-level allowlist check before a query ever
     reaches the DB connection.
  2. app.db.ReadOnlyDB — opens SQLite itself with `mode=ro` + query_only,
     and compiles every statement under an authorizer (reads of the
     reviewed star tables and mart_* only), so even a query that slipped
     past this layer physically cannot write or read anything else.

Neither layer alone is trusted as sufficient; both must hold.

Checks here (all fail closed; the message is written for the model's one
repair round in app.pipeline):
  - one SELECT / WITH statement, no write / admin keyword, no RECURSIVE,
    no unresolved {placeholder};
  - only the reviewed star tables and mart_* tables (or the query's own CTEs);
  - the Division_Total double-counting guard (below);
  - grain guards ported from the KPCL POC: no AVG/SUM of a ratio column
    (collection efficiency, per-unit, pct_*, ratio_*, ARR ...), a column
    named after a KPI must use that KPI's formula, no aggregate over a join
    of two different mart fact tables, no summing of a year-end position
    (arrears, installations) across years;
  - then _nulls_last() makes ascending sorts put missing values last
    (SQLite sorts NULL first, so "lowest" could otherwise name a row with
    no figure at all).
"""
from __future__ import annotations

import re

# Statement-level keywords that must never appear, anywhere, in generated
# SQL. Checked as whole words (so e.g. a column literally named
# "updated_at" isn't falsely rejected for containing "update").
_FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX",
    "GRANT", "REVOKE", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE",
    "RECURSIVE",
]
# REPLACE is refused only as a statement (REPLACE INTO / INSERT OR REPLACE);
# the string function replace() is harmless and sometimes needed.
_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b|\bREPLACE\s+INTO\b", re.IGNORECASE
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

# A comparison written as two (or more) side-by-side columns -- e.g.
# "compare X and Y" -- naturally produces SUM(CASE WHEN subdivision_name =
# 'X' THEN ... ELSE 0 END), SUM(CASE WHEN subdivision_name = 'Y' THEN ...
# ELSE 0 END) rather than a single subdivision_name = '...' filter on the
# whole query, which is exactly the shape the "exactly one occurrence"
# check above (deliberately) refuses to trust. Each such aggregate is
# still independently safe: subdivision_name is single-valued per row (see
# module docstring), so a CASE WHEN condition on it can only ever decide
# THAT branch's own contribution to THAT one aggregate -- it can't blend
# two subdivisions the way an unrestricted SUM over the raw table can, no
# matter how many such branches a query has or whether their literals
# repeat. Requiring the CASE WHEN to be the direct argument of the
# aggregate call (not just "somewhere in the SQL") is deliberate: it rules
# out an equality merely sitting inside some unrelated boolean
# sub-expression (e.g. buried in a WHERE clause's OR) that doesn't actually
# scope any aggregate -- that shape isn't matched here and still falls
# through to the fail-closed default below, same as before this was added.
_AGGREGATE_CASE_WHEN_RE = re.compile(
    r"\b(?:SUM|COUNT|AVG|TOTAL)\s*\(\s*CASE\s+WHEN\b.*?\bEND\s*\)",
    re.IGNORECASE | re.DOTALL,
)


def _all_subdivision_name_equals_inside_aggregated_case_when(sql: str) -> bool:
    """True iff every subdivision_name = '<literal>' equality in `sql`
    appears inside a CASE WHEN ... END expression that is itself the
    direct argument of a SUM/COUNT/AVG/TOTAL(...) call. See
    _AGGREGATE_CASE_WHEN_RE's comment for why that's provably safe."""
    all_positions = [m.start() for m in _SUBDIVISION_NAME_EQUALS_RE.finditer(sql)]
    if not all_positions:
        return False
    agg_spans = [(m.start(), m.end()) for m in _AGGREGATE_CASE_WHEN_RE.finditer(sql)]
    if not agg_spans:
        return False
    return all(any(start <= pos < end for start, end in agg_spans) for pos in all_positions)


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

    if _all_subdivision_name_equals_inside_aggregated_case_when(sql):
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


# ---------------------------------------------------------------------------------------------- table allowlist
_TABLE_REF_RE = re.compile(r"\b(?:FROM|JOIN)\s+(?:(main|mart)\.)?([A-Za-z_][\w]*)\s*(\()?", re.IGNORECASE)
_CTE_RE = re.compile(r"(?:\bWITH\b|,)\s*([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s+AS\s*(?:NOT\s+)?(?:MATERIALIZED\s+)?\(",
                     re.IGNORECASE)
_STRING_RE = re.compile(r"'(?:[^']|'')*'")


def _strip_strings(sql: str) -> str:
    return _STRING_RE.sub("''", sql)


def check_tables(code: str) -> None:
    from app.db import STAR_TABLES
    ctes = {c.lower() for c in _CTE_RE.findall(code)}
    for _schema, name, paren in _TABLE_REF_RE.findall(code):
        low = name.lower()
        if low in STAR_TABLES or low.startswith("mart_") or low in ctes or low == "select" or (
                paren and low in ("json_each", "json_tree")):
            continue
        raise UnsafeSQLError(
            f"Table {name!r} is not available. Use only the tables in the schema above "
            "(dim_*, fact_dcb, fact_dcb_rollups and their views, or the mart_* tables)."
        )


# ---------------------------------------------------------------------------------------------- grain guards
_AGG_COL_RE = re.compile(r"\b(AVG|SUM|TOTAL)\s*\(\s*(?:DISTINCT\s+)?(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)\s*\)", re.IGNORECASE)
_STOCK_COLS = ("closing_balance_total", "opening_balance_total", "total_installations", "live_installations",
               "billed_installations", "dc_mnr_installations", "security_deposit_held")
_MART_FACTS = ("mart_fact_division_year", "mart_fact_subdivision_year", "mart_fact_family_division_year",
               "mart_fact_family_subdivision_year", "mart_fact_slab_division_year", "mart_fact_slab_subdivision_year")
# A column the model names after a KPI must be computed with that KPI's formula (or read as a stored column).
_KPI_ALIASES = {
    "collection_efficiency_pct": ("collection_grand_total", "demand_total"),
    "collection_efficiency": ("collection_grand_total", "demand_total"),
    "efficiency_pct": ("collection_grand_total", "demand_total"),
    "collection_efficiency_percent": ("collection_grand_total", "demand_total"),
    "live_installation_pct": ("live_installations", "total_installations"),
    "arrears_to_demand_ratio": ("closing_balance_total", "demand_total"),
    "demand_per_unit_rs": ("demand_total", "consumption_total"),
}


def _ratio_columns() -> tuple[set[str], set[str]]:
    try:
        from app import mart_layer
        layer = mart_layer.load()
        return set(layer["non_averageable_columns"]), set(layer["non_summable_columns"])
    except Exception:  # noqa: BLE001 -- the mart layer is optional for this text-level check
        return set(), set()


def _select_expression(code: str, alias_pos: int) -> str:
    """The expression that ends right before 'AS <alias>' (walk back to the comma / SELECT at depth 0)."""
    depth, i = 0, alias_pos - 1
    while i >= 0:
        ch = code[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        elif depth == 0 and re.match(r"(?i)select\b", code[i:i + 7]) and (i == 0 or not code[i - 1].isalnum()):
            i += 6
            break
        i -= 1
    return code[i + 1:alias_pos]


_EQ_RE = re.compile(r"\b(?:([A-Za-z_]\w*)\.)?(description|tariff_code|subdivision_name|fiscal_year|family_code)\s*=\s*"
                    r"('(?:[^']|'')*')", re.IGNORECASE)


def check_contradictory_filters(cleaned: str) -> None:
    """`description = 'A' AND description = 'B'` can never match a row (seen live: the model meant IN ('A', 'B')).
    Only checked for a query with no OR / CASE / sub-queries, where every equality belongs to the same WHERE."""
    code = _strip_strings(cleaned).upper()
    if " OR " in code or "CASE" in code or code.count("SELECT") > 1 or " UNION " in code:
        return
    seen: dict[str, set[str]] = {}
    for alias, col, lit in _EQ_RE.findall(cleaned):   # c.fiscal_year and p.fiscal_year are different columns
        seen.setdefault(f"{alias.lower()}.{col.lower()}" if alias else col.lower(), set()).add(lit)
    for col, lits in seen.items():
        if len(lits) > 1:
            raise UnsafeSQLError(
                f"The WHERE clause requires {col} to equal {len(lits)} different values at once ({', '.join(sorted(lits))}), "
                f"so it can never match a row. Use {col} IN (...) for several values."
            )


def check_grain(code: str) -> None:
    no_avg, no_sum = _ratio_columns()
    for fn, col in _AGG_COL_RE.findall(code):
        fn_u, col_l = fn.upper(), col.lower()
        if (fn_u == "AVG" and col_l in no_avg) or (fn_u in ("SUM", "TOTAL") and col_l in no_sum):
            raise UnsafeSQLError(
                f"{fn_u}({col}) is wrong on this data: {col} is a ratio, so averaging or adding it across rows gives "
                "a wrong figure. Recompute it from sums over the rows in scope, e.g. collection efficiency = "
                "SUM(collection_grand_total) * 100.0 / SUM(demand_total), or read the stored column of a single row."
            )
    for m in re.finditer(r"\bAS\s+([A-Za-z_]\w*)", code, re.IGNORECASE):
        need = _KPI_ALIASES.get(m.group(1).lower())
        if not need:
            continue
        expr = _select_expression(code, m.start())
        if "(" not in expr:          # a bare column read (f.collection_efficiency_pct AS collection_efficiency_pct)
            continue
        if re.search(r"\b" + m.group(1) + r"\b", expr):
            continue
        missing = [c for c in need if not re.search(r"\b" + c + r"\b", expr)]
        if missing:
            raise UnsafeSQLError(
                f"The column {m.group(1)} is not computed with its formula (it must use {' and '.join(need)}), "
                "e.g. collection efficiency = SUM(collection_grand_total) * 100.0 / SUM(demand_total)."
            )
    low = code.lower()
    facts = {f for f in _MART_FACTS if re.search(r"\b" + f + r"\b", low)}
    if len(facts) > 1 and _AGGREGATE_FUNC_RE.search(code) and not re.search(r"\bunion\b", low):
        raise UnsafeSQLError(
            "The query aggregates over a join of different mart fact tables (" + ", ".join(sorted(facts)) + "); "
            "a join repeats rows and double counts. Use one table at the grain of the question, or compute each "
            "figure in its own SELECT (combine them with UNION ALL or a join of already-aggregated sub-queries)."
        )
    stock_summed = [c for fn, c in _AGG_COL_RE.findall(code) if fn.upper() in ("SUM", "TOTAL") and c.lower() in _STOCK_COLS]
    if stock_summed and not re.search(r"\bfiscal_year\s*(=|in\b)", low) and not re.search(
            r"group\s+by[^;]*\bfiscal_year\b", low):
        raise UnsafeSQLError(
            f"{stock_summed[0]} is a position at year end (arrears / installations), so adding it up across years "
            "is wrong. Filter one fiscal_year, or GROUP BY fiscal_year."
        )


# ---------------------------------------------------------------------------------------------- NULLS LAST
_ORDER_BY_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
_ORDER_END_RE = re.compile(r"\b(LIMIT|OFFSET|UNION|EXCEPT|INTERSECT|WINDOW)\b", re.IGNORECASE)


def _nulls_last(sql: str) -> str:
    """Every ascending ORDER BY term gets NULLS LAST, so 'lowest' never names a row that has no figure (port of
    the KPCL / WB POC rewrite). Terms that already say NULLS, or DESC (NULLs already last), are left alone."""
    out, pos = [], 0
    for m in _ORDER_BY_RE.finditer(sql):
        if m.start() < pos:
            continue
        start = i = m.end()
        depth = 0
        in_str = False
        while i < len(sql):
            ch = sql[i]
            if ch == "'":
                in_str = not in_str
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    if depth == 0:
                        break
                    depth -= 1
                elif ch == ";" and depth == 0:
                    break
                elif depth == 0 and _ORDER_END_RE.match(sql, i) and (i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")):
                    break
            i += 1
        terms, depth, cur = [], 0, ""
        for ch in sql[start:i]:
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                terms.append(cur)
                cur = ""
            else:
                cur += ch
        terms.append(cur)
        fixed = []
        for t in terms:
            body = t.rstrip()
            if body and not re.search(r"\bNULLS\b", body, re.I) and not re.search(r"\bDESC$", body, re.I):
                body += " NULLS LAST"
            fixed.append(body + t[len(t.rstrip()):])
        out.append(sql[pos:start] + ",".join(fixed))
        pos = i
    return "".join(out) + sql[pos:]


def validate_readonly_sql(sql: str) -> str:
    """Raise UnsafeSQLError if `sql` is anything but a single read-only
    SELECT/WITH statement. Returns the (comment-stripped, trimmed) SQL on
    success — callers should execute the returned string, not the raw
    input, so a comment can't be used to hide a second statement."""
    if not sql or not sql.strip():
        raise UnsafeSQLError("Empty SQL.")

    cleaned = _strip_sql_comments(sql).strip()
    cleaned = cleaned.rstrip(";").strip()

    if ";" in _strip_strings(cleaned):
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

    code = _strip_strings(cleaned)
    match = _FORBIDDEN_RE.search(code)
    if match:
        raise UnsafeSQLError(f"Forbidden keyword in generated SQL: {match.group(0)!r}")

    check_tables(code)
    check_division_total_double_counting(cleaned)
    check_grain(code)
    check_contradictory_filters(cleaned)

    return _nulls_last(cleaned)
