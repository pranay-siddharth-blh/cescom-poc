"""
The one SQLite connection this whole app uses.

Design decisions (see CLAUDE.md "Architecture" section for the full
rationale):
  - Opened ONCE, in FastAPI's lifespan, and reused for every request
    (design point #3) — never re-opened per-request.
  - Opened with `mode=ro` in the connection URI, so the OS/SQLite layer
    itself refuses any write regardless of what SQL reaches it — this is
    a second, independent safety net under app.sql_safety's text-level
    checks, not a replacement for them.
  - A single connection is not thread-safe for concurrent statements, and
    we deliberately keep exactly one connection open (per the "persistent
    connection" requirement) rather than a pool — so a threading.Lock
    serializes query execution. For this app's traffic (a POC chatbot,
    not a high-QPS service) that's the right trade-off; it's a known
    scaling limit, documented rather than hidden (see CLAUDE.md).
  - Query timeout is enforced by running the query in a worker thread and
    calling `sqlite3.Connection.interrupt()` from the caller if it
    overruns — the documented, supported way to abort a long-running
    SQLite statement from another thread.
"""
from __future__ import annotations

import concurrent.futures
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import settings


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_seconds: float


class ReadOnlyDB:
    def __init__(self, db_path: Path):
        if not db_path.exists():
            raise FileNotFoundError(
                f"Locked star-schema DB not found at {db_path}. "
                "This app only reads it — it never creates or regenerates it."
            )
        uri = f"file:{db_path.as_posix()}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._lock = threading.Lock()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="dcb-sqlite"
        )

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._conn.close()

    def run(self, sql: str, max_rows: int, timeout_seconds: float) -> QueryResult:
        """Run one read-only SQL statement with a row cap and a wall-clock timeout."""
        start = time.monotonic()

        def _execute() -> QueryResult:
            with self._lock:
                cur = self._conn.execute(sql)
                columns = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchmany(max_rows + 1)
                truncated = len(rows) > max_rows
                if truncated:
                    rows = rows[:max_rows]
                return QueryResult(
                    columns=columns,
                    rows=rows,
                    truncated=truncated,
                    elapsed_seconds=time.monotonic() - start,
                )

        future = self._executor.submit(_execute)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            self._conn.interrupt()
            raise TimeoutError(
                f"Query exceeded the {timeout_seconds}s safety timeout and was interrupted."
            )


_db: ReadOnlyDB | None = None


def get_db() -> ReadOnlyDB:
    if _db is None:
        raise RuntimeError("DB not initialized — did FastAPI's lifespan run?")
    return _db


def init_db() -> ReadOnlyDB:
    global _db
    _db = ReadOnlyDB(settings.db_path)
    return _db


def shutdown_db() -> None:
    global _db
    if _db is not None:
        _db.close()
        _db = None
