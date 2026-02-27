#!/usr/bin/env python3
"""
CLI for managing users and API keys.

Usage:
    python3 manage.py user add "adriano" --email adriano@example.com
    python3 manage.py user list
    python3 manage.py user disable "adriano"
    python3 manage.py user enable "adriano"

    python3 manage.py key create --user adriano --name "production"
    python3 manage.py key create --user adriano --name "testing" --rate-limit 10 --models "auto-free,deepseek-v3"
    python3 manage.py key list
    python3 manage.py key list --user adriano
    python3 manage.py key revoke mss_a1b2c3d4...

    python3 manage.py stats
    python3 manage.py stats --user adriano
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

import auth
import db as logdb


# ─── User Commands ────────────────────────────────────────────────────────────

def cmd_user_add(args: argparse.Namespace) -> None:
    try:
        user = auth.create_user(args.name, email=args.email or "")
        print(f"✅ User created: {user['name']} (id={user['id']})")
        if user.get("email"):
            print(f"   Email: {user['email']}")
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)


def cmd_user_list(args: argparse.Namespace) -> None:
    users = auth.list_users(include_inactive=args.all)
    if not users:
        print("No users found.")
        return

    print(f"\n{'ID':>4}  {'Name':<20} {'Email':<30} {'Active':>6}  {'Created'}")
    print(f"{'─'*4}  {'─'*20} {'─'*30} {'─'*6}  {'─'*22}")
    for u in users:
        status = "✅" if u["active"] else "❌"
        email = u.get("email") or ""
        created = (u.get("created_at") or "")[:19]
        print(f"{u['id']:>4}  {u['name']:<20} {email:<30} {status:>6}  {created}")
    print()


def cmd_user_disable(args: argparse.Namespace) -> None:
    if auth.set_user_active(args.name, False):
        print(f"✅ User '{args.name}' disabled")
    else:
        print(f"❌ User '{args.name}' not found", file=sys.stderr)
        sys.exit(1)


def cmd_user_enable(args: argparse.Namespace) -> None:
    if auth.set_user_active(args.name, True):
        print(f"✅ User '{args.name}' enabled")
    else:
        print(f"❌ User '{args.name}' not found", file=sys.stderr)
        sys.exit(1)


# ─── Key Commands ─────────────────────────────────────────────────────────────

def cmd_key_create(args: argparse.Namespace) -> None:
    models = [m.strip() for m in args.models.split(",")] if args.models else None
    try:
        key_data = auth.create_api_key(
            user_name=args.user,
            key_name=args.name,
            rate_limit=args.rate_limit,
            allowed_models=models,
        )
        print(f"\n✅ API key created for user '{args.user}'")
        print(f"   Name:       {key_data['name']}")
        print(f"   Key:        {key_data['key']}")
        print(f"   Rate limit: {key_data['rate_limit'] or 'unlimited'} req/min")
        if key_data.get("allowed_models"):
            print(f"   Models:     {', '.join(key_data['allowed_models'])}")
        else:
            print(f"   Models:     all")
        print(f"\n⚠️  Save this key — it won't be shown again in full.\n")
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)


def cmd_key_list(args: argparse.Namespace) -> None:
    keys = auth.list_api_keys(user_name=args.user, include_inactive=args.all)
    if not keys:
        print("No API keys found.")
        return

    print(f"\n{'ID':>4}  {'User':<15} {'Name':<15} {'Key':<20} {'Rate':>6}  {'Models':<20} {'Active':>6}  {'Last Used'}")
    print(f"{'─'*4}  {'─'*15} {'─'*15} {'─'*20} {'─'*6}  {'─'*20} {'─'*6}  {'─'*22}")
    for k in keys:
        status = "✅" if k["active"] else "❌"
        # Mask the key: show prefix + first 4 + last 4
        key_str = k["key"]
        masked = f"{key_str[:8]}...{key_str[-4:]}" if len(key_str) > 12 else key_str
        rate = str(k["rate_limit"]) if k["rate_limit"] else "∞"
        models = ", ".join(k["allowed_models"]) if k.get("allowed_models") else "all"
        if len(models) > 20:
            models = models[:17] + "..."
        last_used = (k.get("last_used_at") or "never")[:19]
        user_name = k.get("user_name", "?")
        print(f"{k['id']:>4}  {user_name:<15} {k['name']:<15} {masked:<20} {rate:>6}  {models:<20} {status:>6}  {last_used}")
    print()


def cmd_key_revoke(args: argparse.Namespace) -> None:
    if auth.revoke_api_key(args.key):
        print(f"✅ Key revoked: {args.key[:12]}...")
    else:
        print(f"❌ Key not found: {args.key[:12]}...", file=sys.stderr)
        sys.exit(1)


# ─── Stats Command ────────────────────────────────────────────────────────────

def cmd_stats(args: argparse.Namespace) -> None:
    if args.user:
        user = auth.get_user(args.user)
        if not user:
            print(f"❌ User '{args.user}' not found", file=sys.stderr)
            sys.exit(1)

        auth.init_auth_db()
        from db import _get_connection
        with _get_connection() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*)            AS total_extractions,
                    SUM(tokens_in)      AS total_tokens_in,
                    SUM(tokens_out)     AS total_tokens_out,
                    SUM(cost)           AS total_cost,
                    CAST(AVG(latency_ms) AS INTEGER) AS avg_latency_ms,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
                    SUM(CASE WHEN status = 'error'   THEN 1 ELSE 0 END) AS errors
                FROM extractions WHERE user_id = ?
                """,
                (user["id"],),
            ).fetchone()
            totals = dict(row) if row else {}

            search_row = conn.execute(
                """
                SELECT COUNT(*) AS total_searches, SUM(total_cost) AS search_cost
                FROM searches WHERE user_id = ?
                """,
                (user["id"],),
            ).fetchone()
            search_totals = dict(search_row) if search_row else {}

        total_ext = totals.get("total_extractions") or 0
        print(f"\n📊 Stats for user: {user['name']}")
        print(f"   Status: {'active' if user['active'] else 'disabled'}")

        if total_ext == 0:
            print("   No extraction data yet.\n")
            return

        total_cost = totals.get("total_cost") or 0
        cost_str = "FREE" if total_cost == 0 else f"${total_cost:.4f}"
        print(f"   Extractions: {total_ext}")
        print(f"   Successes:   {totals.get('successes') or 0}")
        print(f"   Errors:      {totals.get('errors') or 0}")
        print(f"   Tokens:      {(totals.get('total_tokens_in') or 0):,} in / {(totals.get('total_tokens_out') or 0):,} out")
        print(f"   Cost:        {cost_str}")
        total_searches = search_totals.get("total_searches") or 0
        if total_searches:
            s_cost = search_totals.get("search_cost") or 0
            print(f"   Searches:    {total_searches} (${s_cost:.4f})")
        print()
    else:
        # Global stats — delegate to existing extract.py stats
        stats = logdb.get_stats()
        totals = stats["totals"]
        total_ext = totals.get("total_extractions") or 0

        if total_ext == 0:
            print("📊 No usage data yet.")
            return

        total_cost = totals.get("total_cost") or 0
        cost_str = "FREE" if total_cost == 0 else f"${total_cost:.4f}"
        print(f"\n📊 Global Usage Statistics")
        print(f"   Extractions: {total_ext}")
        print(f"   Tokens:      {(totals.get('total_tokens_in') or 0):,} in / {(totals.get('total_tokens_out') or 0):,} out")
        print(f"   Total cost:  {cost_str}")

        users = auth.list_users(include_inactive=True)
        if users:
            print(f"   Users:       {len(users)}")
            keys = auth.list_api_keys(include_inactive=True)
            active_keys = sum(1 for k in keys if k["active"])
            print(f"   API keys:    {len(keys)} ({active_keys} active)")
        print()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Manage users and API keys for Monsoft Scrapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command group")

    # ── user ──
    user_parser = subparsers.add_parser("user", help="Manage users")
    user_sub = user_parser.add_subparsers(dest="action")

    add_p = user_sub.add_parser("add", help="Create a new user")
    add_p.add_argument("name", help="Username")
    add_p.add_argument("--email", default="", help="Email address")
    add_p.set_defaults(func=cmd_user_add)

    list_p = user_sub.add_parser("list", help="List users")
    list_p.add_argument("--all", action="store_true", help="Include inactive users")
    list_p.set_defaults(func=cmd_user_list)

    dis_p = user_sub.add_parser("disable", help="Disable a user")
    dis_p.add_argument("name", help="Username")
    dis_p.set_defaults(func=cmd_user_disable)

    en_p = user_sub.add_parser("enable", help="Enable a user")
    en_p.add_argument("name", help="Username")
    en_p.set_defaults(func=cmd_user_enable)

    # ── key ──
    key_parser = subparsers.add_parser("key", help="Manage API keys")
    key_sub = key_parser.add_subparsers(dest="action")

    cr_p = key_sub.add_parser("create", help="Create a new API key")
    cr_p.add_argument("--user", required=True, help="Username")
    cr_p.add_argument("--name", default="default", help="Key label")
    cr_p.add_argument("--rate-limit", type=int, default=0, help="Max req/min (0=unlimited)")
    cr_p.add_argument("--models", default=None, help="Comma-separated allowed model aliases")
    cr_p.set_defaults(func=cmd_key_create)

    kl_p = key_sub.add_parser("list", help="List API keys")
    kl_p.add_argument("--user", default=None, help="Filter by user")
    kl_p.add_argument("--all", action="store_true", help="Include revoked keys")
    kl_p.set_defaults(func=cmd_key_list)

    rev_p = key_sub.add_parser("revoke", help="Revoke an API key")
    rev_p.add_argument("key", help="The full API key to revoke")
    rev_p.set_defaults(func=cmd_key_revoke)

    # ── stats ──
    stats_p = subparsers.add_parser("stats", help="Show usage statistics")
    stats_p.add_argument("--user", default=None, help="Filter by user")
    stats_p.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
