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

    match = _FORBIDDEN_RE.search(cleaned)
    if match:
        raise UnsafeSQLError(f"Forbidden keyword in generated SQL: {match.group(0)!r}")

    return cleaned
