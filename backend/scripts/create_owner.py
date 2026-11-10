"""Create the single ASTA owner account from the local console.

This is the alternative to ``POST /api/auth/setup``. It never transmits the
password over the network and never echoes it to the terminal.

Usage (from ``C:\\Asta\\backend``)::

    ..\\venv\\Scripts\\python.exe scripts\\create_owner.py

The owner username may also be supplied as the first argument.
"""

import asyncio
import getpass
import os
import sys
from pathlib import Path

os.environ.setdefault("DEBUG", "False")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.database.connection import AsyncSessionLocal
from app.exceptions.auth_exceptions import (
    InvalidOwnerPasswordError,
    InvalidOwnerUsernameError,
    OwnerAlreadyExistsError,
)
from app.services.auth_service import auth_service


def _guard_database() -> None:
    if settings.DB_NAME != "asta_db":
        raise SystemExit(
            "Refusing to run: DB_NAME must be 'asta_db' "
            f"(currently '{settings.DB_NAME}')."
        )


def _read_password() -> str:
    password = getpass.getpass("Owner password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        raise SystemExit("Passwords do not match.")

    return password


async def _create(username: str | None) -> None:
    if username is None:
        username = input("Owner username: ").strip()

    password = _read_password()

    async with AsyncSessionLocal() as session:
        try:
            owner, user = await auth_service.create_owner(
                session=session,
                username=username,
                password=password,
            )

        except OwnerAlreadyExistsError as error:
            raise SystemExit(str(error))

        except (InvalidOwnerUsernameError, InvalidOwnerPasswordError) as error:
            raise SystemExit(str(error))

    print(f"Owner account created for username '{user.username}'.")
    print("Remove AUTH_OWNER_SETUP_TOKEN from .env and restart the backend.")


def main() -> None:
    _guard_database()
    username = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(_create(username))


if __name__ == "__main__":
    main()
