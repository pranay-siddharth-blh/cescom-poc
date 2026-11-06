"""
The one SQLite connection this whole app uses.

Design decisions (see CLAUDE.md "Architecture" and section 12 for the full rationale):
  - Opened ONCE, in FastAPI's lifespan, and reused for every request (design point #3).
  - The locked star schema is the main database and the mart (DCB_Mart/dcb_mart.sqlite) is ATTACHed as
    `mart`, both with `mode=ro` in their URI, and `PRAGMA query_only = ON` on top: SQLite itself refuses
    every write, whatever the SQL says.
  - Every statement runs under an authorizer (restricted=True, the default) that is checked while SQLite
    compiles it, so it holds however the SQL is phrased (aliases, sub-queries, CTEs, views): only SELECT /
    READ / FUNCTION actions are allowed (no PRAGMA, ATTACH, writes, transactions or recursive CTEs), only
    the reviewed star tables and the mart_* tables may be read (never reconciliation_report, the mart's
    semantic_ / ledger_ tables or SQLite's own catalogue), and file / extension functions are denied.
  - Values from requests are passed as bound parameters (`params`), never pasted into the SQL text.
  - A single connection is not thread-safe for concurrent statements, so a threading.Lock serializes
    execution in one worker thread; a wall-clock timeout interrupts a runaway statement and a row cap
    bounds the result (fetchmany(cap+1), not text-level LIMIT injection).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

# Tables model-written (and all other app) SQL may read. Views count as reads of the tables under them.
STAR_TABLES = frozenset({
    "dim_fiscal_year", "dim_subdivision", "dim_tariff_category", "fact_dcb", "fact_dcb_rollups",
    "fact_dcb_subdivisions", "fact_dcb_grand_totals",
})
MART_PREFIX = "mart_"
_ALLOWED_VIRTUAL_TABLES = frozenset({"json_each", "json_tree"})
_DENIED_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer", "randomblob",
                               "zeroblob", "sqlite_offset", "sqlite_compileoption_get", "sqlite_source_id"})
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}


def table_allowed(table: str, db_name: str | None = None) -> bool:
    t = (table or "").lower()
    if t in _ALLOWED_VIRTUAL_TABLES:
        return True
    if db_name == "mart" or db_name is None:
        if t.startswith(MART_PREFIX):
            return True
    if db_name in ("main", None) and t in STAR_TABLES:
        return True
    return False


def _restricted_authorizer(action, arg1, arg2, db_name, _trigger_or_view):
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY   # writes, PRAGMA, ATTACH, recursive CTEs, transactions ...
    if action == sqlite3.SQLITE_READ and not table_allowed(arg1, db_name):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _DENIED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class RestrictedQueryError(Exception):
    """The SQL tried to read or do something the authorizer forbids."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_seconds: float


class ReadOnlyDB:
    def __init__(self, db_path: Path, mart_path: Path | None = None):
        if not db_path.exists():
            raise FileNotFoundError(
                f"Locked star-schema DB not found at {db_path}. "
                "This app only reads it — it never creates or regenerates it."
            )
        mart_path = mart_path or settings.mart_db_path
        if not mart_path.exists():
            raise FileNotFoundError(
                f"Mart DB not found at {mart_path}. Build it with: python DCB_Mart/build_mart.py "
                "(it only reads the locked star schema)."
            )
        self.path, self.mart_path = db_path, mart_path
        self._conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        self._conn.execute("ATTACH DATABASE ? AS mart", (f"file:{mart_path.as_posix()}?mode=ro",))
        self._conn.execute("PRAGMA query_only = ON")
        self._lock = threading.Lock()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="dcb-sqlite")

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._conn.close()

    def run_sync(self, sql: str, max_rows: int, restricted: bool = True, params: tuple = ()) -> QueryResult:
        start = time.monotonic()
        with self._lock:
            if restricted:
                self._conn.set_authorizer(_restricted_authorizer)
            try:
                cur = self._conn.execute(sql, params)
                columns = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchmany(max_rows + 1)
            except sqlite3.DatabaseError as exc:
                msg = str(exc).lower()
                if restricted and ("not authorized" in msg or "prohibited" in msg):
                    raise RestrictedQueryError(str(exc)) from exc
                raise
            finally:
                if restricted:
                    self._conn.set_authorizer(None)
        truncated = len(rows) > max_rows
        return QueryResult(columns, rows[:max_rows], truncated, time.monotonic() - start)

    async def run(self, sql: str, max_rows: int, timeout_seconds: float, restricted: bool = True,
                  params: tuple | list = ()) -> QueryResult:
        """Run one read-only statement with a row cap and a wall-clock timeout, without blocking the event
        loop (the caller awaits the worker thread). restricted=False is for the app's own internal reads of
        the mart's semantic_ / ledger_ tables only -- never for model-written SQL."""
        future = self._executor.submit(self.run_sync, sql, max_rows, restricted, tuple(params))
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            self._conn.interrupt()
            raise TimeoutError(f"Query exceeded the {timeout_seconds}s safety timeout and was interrupted.")


_db: ReadOnlyDB | None = None


def get_db() -> ReadOnlyDB:
    if _db is None:
        raise RuntimeError("DB not initialized — did FastAPI's lifespan run?")
    return _db


def init_db() -> ReadOnlyDB:
    global _db
    _db = ReadOnlyDB(settings.db_path, settings.mart_db_path)
    return _db


def shutdown_db() -> None:
    global _db
    if _db is not None:
        _db.close()
        _db = None
