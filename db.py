#!/usr/bin/env python3
"""
SQLite logging database for extractions, searches, and model usage.

All logging is non-blocking — failures never break extraction.
Database auto-creates on first use at data/logs.db.
"""

import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).parent / "data"
DB_PATH = DB_DIR / "logs.db"

# ─── Schema ───────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS extractions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    url             TEXT    NOT NULL,
    final_url       TEXT,
    query           TEXT,
    format          TEXT,
    model           TEXT,
    model_id        TEXT,
    fetcher         TEXT,
    status          TEXT    NOT NULL DEFAULT 'success',
    text_length     INTEGER DEFAULT 0,
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    cost            REAL    DEFAULT 0.0,
    latency_ms      INTEGER DEFAULT 0,
    error           TEXT,
    result_hash     TEXT
);

CREATE TABLE IF NOT EXISTS searches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    query           TEXT    NOT NULL,
    count           INTEGER DEFAULT 0,
    freshness       TEXT,
    deep            INTEGER DEFAULT 0,
    parallel        INTEGER DEFAULT 0,
    model           TEXT,
    results_found   INTEGER DEFAULT 0,
    extracted_count INTEGER DEFAULT 0,
    total_tokens_in INTEGER DEFAULT 0,
    total_tokens_out INTEGER DEFAULT 0,
    total_cost      REAL    DEFAULT 0.0,
    total_time_ms   INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_extractions_timestamp ON extractions(timestamp);
CREATE INDEX IF NOT EXISTS idx_extractions_model ON extractions(model);
CREATE INDEX IF NOT EXISTS idx_extractions_url ON extractions(url);
CREATE INDEX IF NOT EXISTS idx_searches_timestamp ON searches(timestamp);

CREATE VIEW IF NOT EXISTS model_usage AS
SELECT
    model,
    model_id,
    COUNT(*)                        AS total_calls,
    SUM(tokens_in)                  AS total_tokens_in,
    SUM(tokens_out)                 AS total_tokens_out,
    SUM(cost)                       AS total_cost,
    CAST(AVG(latency_ms) AS INTEGER) AS avg_latency_ms,
    MAX(timestamp)                  AS last_used,
    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
    SUM(CASE WHEN status = 'error'   THEN 1 ELSE 0 END) AS errors
FROM extractions
GROUP BY model, model_id;
"""


# ─── Connection Management ────────────────────────────────────────────────────

def _ensure_db() -> Path:
    """Create the data directory and database file if needed."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    return DB_PATH


@contextmanager
def _get_connection():
    """Yield a SQLite connection with WAL mode and foreign keys."""
    db_path = _ensure_db()
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize the database schema. Safe to call multiple times."""
    with _get_connection() as conn:
        conn.executescript(_SCHEMA_SQL)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _result_hash(content: str) -> str:
    """SHA-256 hash of extraction result for duplicate detection."""
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _safe(func):
    """Decorator: swallow exceptions so logging never breaks extraction."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            logger.warning("DB logging failed: %s", exc)
            return None
    return wrapper


# ─── Logging Functions ────────────────────────────────────────────────────────

@_safe
def log_extraction(
    *,
    url: str,
    final_url: str = "",
    query: str = "",
    format: str = "markdown",
    model: str = "",
    model_id: str = "",
    fetcher: str = "",
    status: str = "success",
    text_length: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: float = 0.0,
    latency_ms: int = 0,
    error: str = "",
    result: str = "",
) -> Optional[int]:
    """Log a single extraction. Returns the row ID."""
    init_db()
    with _get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO extractions
                (timestamp, url, final_url, query, format, model, model_id,
                 fetcher, status, text_length, tokens_in, tokens_out, cost,
                 latency_ms, error, result_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now_iso(), url, final_url, query, format, model, model_id,
                fetcher, status, text_length, tokens_in, tokens_out, cost,
                latency_ms, error, _result_hash(result) if result else None,
            ),
        )
        return cursor.lastrowid


@_safe
def log_search(
    *,
    query: str,
    count: int = 0,
    freshness: str = "",
    deep: int = 0,
    parallel: bool = False,
    model: str = "",
    results_found: int = 0,
    extracted_count: int = 0,
    total_tokens_in: int = 0,
    total_tokens_out: int = 0,
    total_cost: float = 0.0,
    total_time_ms: int = 0,
) -> Optional[int]:
    """Log a search operation. Returns the row ID."""
    init_db()
    with _get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO searches
                (timestamp, query, count, freshness, deep, parallel, model,
                 results_found, extracted_count, total_tokens_in, total_tokens_out,
                 total_cost, total_time_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now_iso(), query, count, freshness or None, deep,
                1 if parallel else 0, model or None, results_found,
                extracted_count, total_tokens_in, total_tokens_out,
                total_cost, total_time_ms,
            ),
        )
        return cursor.lastrowid


# ─── Query Functions ──────────────────────────────────────────────────────────

def get_history(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent extractions."""
    init_db()
    with _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, timestamp, url, model, status, tokens_in, tokens_out,
                   cost, latency_ms, text_length, format, fetcher, error
            FROM extractions
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_stats(model_filter: str = None) -> dict[str, Any]:
    """
    Return usage statistics.

    If model_filter is given, stats are scoped to that model alias.
    Returns dict with keys: extractions, searches, models, totals.
    """
    init_db()
    with _get_connection() as conn:
        # Model usage view
        if model_filter:
            model_rows = conn.execute(
                "SELECT * FROM model_usage WHERE model = ?", (model_filter,)
            ).fetchall()
        else:
            model_rows = conn.execute(
                "SELECT * FROM model_usage ORDER BY total_cost DESC"
            ).fetchall()
        models = [dict(r) for r in model_rows]

        # Extraction totals (optionally filtered)
        where = ""
        params: list[Any] = []
        if model_filter:
            where = "WHERE model = ?"
            params = [model_filter]

        totals_row = conn.execute(
            f"""
            SELECT
                COUNT(*)            AS total_extractions,
                SUM(tokens_in)      AS total_tokens_in,
                SUM(tokens_out)     AS total_tokens_out,
                SUM(cost)           AS total_cost,
                CAST(AVG(latency_ms) AS INTEGER) AS avg_latency_ms,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
                SUM(CASE WHEN status = 'error'   THEN 1 ELSE 0 END) AS errors,
                MIN(timestamp)      AS first_extraction,
                MAX(timestamp)      AS last_extraction
            FROM extractions {where}
            """,
            params,
        ).fetchone()
        totals = dict(totals_row) if totals_row else {}

        # Search totals
        search_row = conn.execute(
            """
            SELECT
                COUNT(*)                AS total_searches,
                SUM(total_tokens_in)    AS search_tokens_in,
                SUM(total_tokens_out)   AS search_tokens_out,
                SUM(total_cost)         AS search_cost,
                SUM(extracted_count)    AS search_extractions
            FROM searches
            """
        ).fetchone()
        search_totals = dict(search_row) if search_row else {}

        return {
            "models": models,
            "totals": totals,
            "searches": search_totals,
        }


def get_search_history(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent searches."""
    init_db()
    with _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, timestamp, query, count, deep, model, results_found,
                   extracted_count, total_cost, total_time_ms
            FROM searches
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
