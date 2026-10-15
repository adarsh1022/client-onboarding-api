"""Manage API keys from the command line.

    python -m app.cli create-key --name demo
    python -m app.cli list-keys
    python -m app.cli revoke-key 3

It uses the database in DATABASE_URL, like the app itself.
"""

import argparse
import sys

from sqlalchemy import select

from app import db
from app.auth import create_api_key
from app.models import ApiKey, utcnow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-key", help="create an API key and print it once")
    create.add_argument("--name", required=True, help="a label, e.g. who the key is for")
    commands.add_parser("list-keys", help="list keys (never the keys themselves)")
    revoke = commands.add_parser("revoke-key", help="stop a key from working")
    revoke.add_argument("key_id", type=int)

    args = parser.parse_args(argv)
    with db.SessionLocal() as session:
        if args.command == "create-key":
            record, key = create_api_key(session, args.name.strip())
            print(f"Created API key {record.id} ({record.name!r}).")
            print("Copy it now: it is shown only once and can't be recovered.\n")
            print(key)
            print("\nSend it in the X-API-Key header.")
            return 0

        if args.command == "list-keys":
            keys = session.scalars(select(ApiKey).order_by(ApiKey.id)).all()
            for k in keys:
                state = f"revoked {k.revoked_at:%Y-%m-%d}" if k.revoked_at else "active"
                print(f"{k.id:>4}  {k.prefix}...  {k.name:<20} {state}")
            if not keys:
                print("No API keys yet. Create one with: python -m app.cli create-key --name demo")
            return 0

        record = session.get(ApiKey, args.key_id)
        if record is None:
            print(f"No API key with id {args.key_id}.", file=sys.stderr)
            return 1
        if record.revoked_at is None:
            record.revoked_at = utcnow()
            session.commit()
        print(f"Revoked API key {record.id} ({record.name!r}).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
