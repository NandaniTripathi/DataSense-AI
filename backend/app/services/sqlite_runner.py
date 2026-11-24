"""Sandboxed SQL execution against a dataset's SQLite file.

Layers of protection:
 1. read-only connection (URI mode=ro) + PRAGMA query_only
 2. authorizer callback that only permits SELECT / READ / safe functions
 3. progress handler that aborts queries after a time limit
 4. hard row cap on what is fetched
"""
from __future__ import annotations

import math
import sqlite3
import statistics
import time
from dataclasses import dataclass

import pandas as pd

from app.core.config import get_settings
from app.services.sql_guard import validate_sql
from app.services.storage import dataset_db_path

_BLOCKED_FUNCTIONS = {
    "load_extension", "readfile", "writefile", "edit", "fts3_tokenizer",
    "sqlite_compileoption_get", "zipfile", "sqlar_compress",
}


class QueryExecutionError(RuntimeError):
    """The query was valid SQL but failed to run (bad column, timeout, ...)."""


@dataclass
class QueryResult:
    df: pd.DataFrame
    sql: str
    truncated: bool
    elapsed_ms: int


class _StdDev:
    def __init__(self):
        self.values: list[float] = []

    def step(self, v):
        if v is not None:
            self.values.append(float(v))

    def finalize(self):
        return statistics.stdev(self.values) if len(self.values) > 1 else None


class _Variance(_StdDev):
    def finalize(self):
        return statistics.variance(self.values) if len(self.values) > 1 else None


class _Median(_StdDev):
    def finalize(self):
        return statistics.median(self.values) if self.values else None


def _register_helpers(conn: sqlite3.Connection) -> None:
    """SQLite lacks STDDEV / MEDIAN / math functions on many builds: add them."""
    conn.create_aggregate("STDDEV", 1, _StdDev)
    conn.create_aggregate("STDEV", 1, _StdDev)
    conn.create_aggregate("VARIANCE", 1, _Variance)
    conn.create_aggregate("MEDIAN", 1, _Median)

    def _safe(fn):
        def wrapped(*a):
            try:
                if any(x is None for x in a):
                    return None
                return fn(*a)
            except (ValueError, OverflowError, ZeroDivisionError):
                return None
        return wrapped

    for name, nargs, fn in [
        ("SQRT", 1, math.sqrt), ("LN", 1, math.log), ("LOG10", 1, math.log10),
        ("EXP", 1, math.exp), ("POWER", 2, math.pow), ("POW", 2, math.pow),
        ("FLOOR", 1, lambda x: float(math.floor(x))), ("CEIL", 1, lambda x: float(math.ceil(x))),
    ]:
        conn.create_function(name, nargs, _safe(fn), deterministic=True)


def _authorizer(action, arg1, arg2, dbname, source):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        if (arg2 or "").lower() in _BLOCKED_FUNCTIONS:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def run_query(dataset_id: str, sql: str, max_rows: int | None = None) -> QueryResult:
    settings = get_settings()
    max_rows = max_rows or settings.query_max_rows
    sql = validate_sql(sql)
    path = dataset_db_path(dataset_id)
    if not path.exists():
        raise QueryExecutionError("Dataset file is missing on disk.")

    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
    try:
        conn.execute("PRAGMA query_only = ON")
        _register_helpers(conn)
        conn.set_authorizer(_authorizer)
        deadline = time.monotonic() + settings.query_timeout_seconds
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        start = time.monotonic()
        try:
            cur = conn.execute(sql)
            columns = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(max_rows + 1)
        except sqlite3.OperationalError as exc:
            msg = str(exc)
            if "interrupted" in msg:
                raise QueryExecutionError(
                    f"The query took longer than {settings.query_timeout_seconds:.0f}s and was stopped."
                ) from exc
            raise QueryExecutionError(msg) from exc
        except sqlite3.DatabaseError as exc:
            raise QueryExecutionError(str(exc)) from exc
        truncated = len(rows) > max_rows
        df = pd.DataFrame(rows[:max_rows], columns=_dedupe(columns))
        return QueryResult(df=df, sql=sql, truncated=truncated,
                           elapsed_ms=int((time.monotonic() - start) * 1000))
    finally:
        conn.close()


def _dedupe(cols: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for c in cols:
        if c in seen:
            seen[c] += 1
            out.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            out.append(c)
    return out
