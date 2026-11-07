r"""
Verify that ASTA can connect to its PostgreSQL database.

Run from the backend directory:

    ..\venv\Scripts\python.exe test_database.py

This script only reads server metadata. It does not create any tables.
"""

import asyncio

from sqlalchemy import text

from app.database.connection import engine


EXPECTED_DATABASE = "asta_db"


async def verify_database() -> bool:
    """Connect to PostgreSQL and verify the configured database."""

    print("=== ASTA PostgreSQL connection test ===")

    try:

        async with engine.connect() as connection:

            version_result = await connection.execute(
                text("SELECT version()")
            )
            version = version_result.scalar_one()

            database_result = await connection.execute(
                text("SELECT current_database()")
            )
            database = database_result.scalar_one()

        print("Connection: SUCCESS")
        print("PostgreSQL version:", version)
        print("Current database:", database)

        if database == EXPECTED_DATABASE:
            print(
                "Database name check: PASSED "
                f"(expected and connected to {EXPECTED_DATABASE})"
            )
            return True

        print(
            "Database name check: FAILED "
            f"(expected {EXPECTED_DATABASE}, connected to {database})"
        )
        return False

    except Exception as error:

        print("Connection: FAILED")
        print("Error:", error)
        return False

    finally:

        await engine.dispose()
        print("Database engine disposed.")


if __name__ == "__main__":

    success = asyncio.run(verify_database())

    raise SystemExit(0 if success else 1)
