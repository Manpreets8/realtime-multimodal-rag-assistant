"""Administrative commands, run on the server (they need database access, not an API login).

    python -m app.cli promote you@example.com   # make an existing account an administrator
    python -m app.cli demote you@example.com    # back to a regular user

With Docker:  docker compose exec api python -m app.cli promote you@example.com

Why a command and not a setting such as ADMIN_EMAILS: email addresses are not verified, so
anyone who signed up with a listed address first would become an administrator. Running a
command on the server proves you control the deployment.
"""

import argparse
import asyncio
import sys

from app.core.errors import NotFoundError
from app.db.session import SessionLocal, engine
from app.models import UserRole
from app.services.admin_service import set_role_by_email


async def _set_role(email: str, role: UserRole) -> int:
    try:
        async with SessionLocal() as db:
            user = await set_role_by_email(db, email, role)
    except NotFoundError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    print(f"{user.email} is now {'an administrator' if role is UserRole.ADMIN else 'a regular user'}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Mindora AI administration")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("promote", "make an account an administrator"),
        ("demote", "make it a regular user"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("email")
    args = parser.parse_args(argv)
    role = UserRole.ADMIN if args.command == "promote" else UserRole.USER
    return asyncio.run(_set_role(args.email, role))


if __name__ == "__main__":
    raise SystemExit(main())
