#!/usr/bin/env python3
"""
API key authentication, validation, and rate limiting.

Provides middleware-friendly functions for the HTTP API and MCP server.
CLI requests bypass authentication (localhost only).
"""

import secrets
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from db import _get_connection, init_db

# ─── Constants ────────────────────────────────────────────────────────────────

KEY_PREFIX = "mss_"
KEY_HEX_LENGTH = 32  # 32 hex chars = 16 bytes of entropy


# ─── Rate Limiter (in-memory, per-process) ────────────────────────────────────

class RateLimiter:
    """Sliding-window rate limiter using in-memory token buckets."""

    def __init__(self):
        self._windows: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, limit: int) -> tuple[bool, int]:
        """
        Check if a request is allowed under the rate limit.

        Args:
            key: The API key string.
            limit: Max requests per minute. 0 = unlimited.

        Returns:
            (allowed, remaining) — whether the request is allowed and how many remain.
        """
        if limit <= 0:
            return True, -1  # Unlimited

        now = time.monotonic()
        window_start = now - 60.0
        timestamps = self._windows[key]

        # Purge expired entries
        self._windows[key] = [t for t in timestamps if t > window_start]
        timestamps = self._windows[key]

        remaining = limit - len(timestamps)
        if remaining <= 0:
            return False, 0

        timestamps.append(now)
        return True, remaining - 1


# Global rate limiter instance
_rate_limiter = RateLimiter()


# ─── Key Generation ───────────────────────────────────────────────────────────

def generate_key() -> str:
    """Generate a new API key with the mss_ prefix."""
    return f"{KEY_PREFIX}{secrets.token_hex(KEY_HEX_LENGTH // 2)}"


# ─── Schema Extension ────────────────────────────────────────────────────────

_AUTH_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    email       TEXT,
    created_at  TEXT    NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS api_keys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    key             TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL DEFAULT 'default',
    created_at      TEXT    NOT NULL,
    last_used_at    TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    rate_limit      INTEGER NOT NULL DEFAULT 0,
    allowed_models  TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_keys_key ON api_keys(key);
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
"""


def init_auth_db() -> None:
    """Initialize auth tables. Safe to call multiple times."""
    init_db()
    with _get_connection() as conn:
        conn.executescript(_AUTH_SCHEMA_SQL)
        # Add user_id columns to existing tables if missing
        for table in ("extractions", "searches"):
            cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if "user_id" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ─── User Management ─────────────────────────────────────────────────────────

def create_user(name: str, email: str = "") -> dict[str, Any]:
    """Create a new user. Returns the user dict."""
    init_auth_db()
    with _get_connection() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO users (name, email, created_at) VALUES (?, ?, ?)",
                (name, email or None, _now_iso()),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"User '{name}' already exists")
        return _user_row_to_dict(conn.execute(
            "SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)
        ).fetchone())


def get_user(name: str) -> Optional[dict[str, Any]]:
    """Get a user by name."""
    init_auth_db()
    with _get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
        return _user_row_to_dict(row) if row else None


def list_users(include_inactive: bool = False) -> list[dict[str, Any]]:
    """List all users."""
    init_auth_db()
    with _get_connection() as conn:
        query = "SELECT * FROM users" if include_inactive else "SELECT * FROM users WHERE active = 1"
        rows = conn.execute(f"{query} ORDER BY id").fetchall()
        return [_user_row_to_dict(r) for r in rows]


def set_user_active(name: str, active: bool) -> bool:
    """Enable or disable a user. Returns True if user was found."""
    init_auth_db()
    with _get_connection() as conn:
        result = conn.execute(
            "UPDATE users SET active = ? WHERE name = ?", (1 if active else 0, name)
        )
        return result.rowcount > 0


def _user_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["active"] = bool(d["active"])
    return d


# ─── API Key Management ──────────────────────────────────────────────────────

def create_api_key(
    user_name: str,
    key_name: str = "default",
    rate_limit: int = 0,
    allowed_models: list[str] | None = None,
) -> dict[str, Any]:
    """
    Create an API key for a user.

    Args:
        user_name: The user's name.
        key_name: Label for the key.
        rate_limit: Max requests/min (0 = unlimited).
        allowed_models: List of allowed model aliases (None = all).

    Returns:
        The key dict including the plaintext key (only shown once).
    """
    init_auth_db()
    user = get_user(user_name)
    if not user:
        raise ValueError(f"User '{user_name}' not found")

    import json
    models_json = json.dumps(allowed_models) if allowed_models else None
    key = generate_key()

    with _get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO api_keys (user_id, key, name, created_at, rate_limit, allowed_models)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user["id"], key, key_name, _now_iso(), rate_limit, models_json),
        )
        row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _key_row_to_dict(row)


def list_api_keys(user_name: str = None, include_inactive: bool = False) -> list[dict[str, Any]]:
    """List API keys, optionally filtered by user."""
    init_auth_db()
    with _get_connection() as conn:
        conditions = []
        params: list[Any] = []

        if not include_inactive:
            conditions.append("k.active = 1")
        if user_name:
            conditions.append("u.name = ?")
            params.append(user_name)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"""
            SELECT k.*, u.name AS user_name
            FROM api_keys k JOIN users u ON k.user_id = u.id
            {where}
            ORDER BY k.id
            """,
            params,
        ).fetchall()
        return [_key_row_to_dict(r) for r in rows]


def revoke_api_key(key: str) -> bool:
    """Revoke an API key. Returns True if key was found."""
    init_auth_db()
    with _get_connection() as conn:
        result = conn.execute("UPDATE api_keys SET active = 0 WHERE key = ?", (key,))
        return result.rowcount > 0


def _key_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    import json
    d = dict(row)
    d["active"] = bool(d["active"])
    if d.get("allowed_models"):
        try:
            d["allowed_models"] = json.loads(d["allowed_models"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


# ─── Authentication & Authorization ──────────────────────────────────────────

class AuthError(Exception):
    """Raised when authentication or authorization fails."""

    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


def authenticate(api_key: str) -> dict[str, Any]:
    """
    Validate an API key and return the key + user info.

    Raises AuthError if the key is invalid, revoked, rate-limited, etc.
    Returns a dict with user_id, user_name, key_name, rate_limit, allowed_models.
    """
    if not api_key or not api_key.startswith(KEY_PREFIX):
        raise AuthError("Invalid API key format")

    init_auth_db()
    with _get_connection() as conn:
        row = conn.execute(
            """
            SELECT k.*, u.name AS user_name, u.active AS user_active
            FROM api_keys k JOIN users u ON k.user_id = u.id
            WHERE k.key = ?
            """,
            (api_key,),
        ).fetchone()

    if not row:
        raise AuthError("Invalid API key")

    key_data = dict(row)

    if not key_data["user_active"]:
        raise AuthError("User account is disabled", 403)

    if not key_data["active"]:
        raise AuthError("API key has been revoked", 403)

    # Rate limiting
    rate_limit = key_data["rate_limit"]
    if rate_limit > 0:
        allowed, remaining = _rate_limiter.check(api_key, rate_limit)
        if not allowed:
            raise AuthError(
                f"Rate limit exceeded ({rate_limit} req/min). Try again shortly.",
                429,
            )

    # Update last_used_at
    with _get_connection() as conn:
        conn.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE key = ?",
            (_now_iso(), api_key),
        )

    import json
    allowed_models = None
    if key_data.get("allowed_models"):
        try:
            allowed_models = json.loads(key_data["allowed_models"])
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "user_id": key_data["user_id"],
        "user_name": key_data["user_name"],
        "key_name": key_data["name"],
        "rate_limit": rate_limit,
        "allowed_models": allowed_models,
    }


def check_model_access(auth_info: dict[str, Any], model_alias: str) -> None:
    """
    Check if the authenticated key is allowed to use the given model.

    Raises AuthError if the model is not in the allowed list.
    """
    allowed = auth_info.get("allowed_models")
    if allowed is None:
        return  # No restrictions

    if model_alias not in allowed:
        raise AuthError(
            f"Model '{model_alias}' not allowed for this key. "
            f"Allowed: {', '.join(allowed)}",
            403,
        )
