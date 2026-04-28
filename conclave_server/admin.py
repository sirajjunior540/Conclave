"""Admin CLI for the Conclave server.

Usage:
    python -m conclave_server.admin issue-code --slack-user U02ABC --name "Siraj"

Prints a one-time claim code. The user pastes it into Claude Code:
    /grill claim <code>

This is the bootstrap path for the very first user (the initiator). Subsequent
stakeholders are issued claim codes automatically by create_session and
delivered via the notifications adapter.
"""
import argparse
import secrets
from datetime import datetime, timedelta, timezone

from .db import get_conn, init_db


def issue_code(slack_user_id: str, display_name: str = "", days: int = 7) -> str:
    init_db()
    code = secrets.token_urlsafe(8)
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO claim_codes (code, slack_user_id, display_name, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (code, slack_user_id, display_name, expires),
        )
    return code


def main() -> None:
    p = argparse.ArgumentParser(prog="conclave-admin")
    sub = p.add_subparsers(dest="cmd", required=True)

    ic = sub.add_parser("issue-code", help="issue a one-time claim code for a user")
    ic.add_argument("--slack-user", required=True, help="Slack user ID (e.g. U02ABC)")
    ic.add_argument("--name", default="", help="display name (optional)")
    ic.add_argument("--days", type=int, default=7, help="code TTL in days")

    args = p.parse_args()
    if args.cmd == "issue-code":
        code = issue_code(args.slack_user, args.name, args.days)
        print(f"Claim code: {code}")
        print(f"User: {args.slack_user}" + (f" ({args.name})" if args.name else ""))
        print(f"Expires in: {args.days} days")
        print()
        print("The user runs in Claude Code:")
        print(f"  /grill claim {code}")


if __name__ == "__main__":
    main()
