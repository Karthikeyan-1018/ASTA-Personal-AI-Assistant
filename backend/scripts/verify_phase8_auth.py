"""Real-PostgreSQL verification for Phase 8 authentication.

Guarded to ``asta_db`` only. It creates throwaway rows, verifies persistence,
hash storage, singleton and uniqueness constraints, expiry, and revocation,
then deletes only the rows it created. Existing users, data, embeddings, and
pending actions are never modified.

If a real owner already exists, the script leaves it untouched and verifies
the session lifecycle with a throwaway user instead.
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DEBUG", "False")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.database.connection import AsyncSessionLocal
from app.exceptions.auth_exceptions import (
    InvalidOwnerPasswordError,
    OwnerAlreadyExistsError,
)
from app.models.auth import AuthSession, OwnerAccount
from app.models.user import User
from app.services.auth_service import auth_service, hash_token


PASSWORD = "phase8-verification-password"
WRONG_PASSWORD = "phase8-wrong-password"


def check(condition, message):
    if not condition:
        raise AssertionError(message)

    print(f"OK  {message}")


async def _get_session_by_raw(session, raw_token):
    result = await session.execute(
        select(AuthSession).where(
            AuthSession.token_hash == hash_token(raw_token)
        )
    )
    return result.scalar_one_or_none()


async def _verify_owner(session, suffix):
    """Exercise owner creation and password verification on real PostgreSQL."""

    username = f"phase8_verify_owner_{suffix}"

    owner, user = await auth_service.create_owner(
        session=session,
        username=username,
        password=PASSWORD,
    )

    check(owner.id == 1, "owner account uses singleton id 1")
    check(
        owner.password_hash.startswith("$argon2id$"),
        "owner password is stored as an Argon2id hash",
    )
    check(
        PASSWORD not in owner.password_hash,
        "plaintext password is not stored",
    )

    authenticated = await auth_service.authenticate(
        session=session,
        username=username,
        password=PASSWORD,
    )
    check(authenticated is not None, "correct password authenticates owner")

    rejected = await auth_service.authenticate(
        session=session,
        username=username,
        password=WRONG_PASSWORD,
    )
    check(rejected is None, "incorrect password is rejected")

    duplicate = False
    try:
        await auth_service.create_owner(
            session=session,
            username=f"phase8_verify_duplicate_{suffix}",
            password=PASSWORD,
        )
    except OwnerAlreadyExistsError:
        duplicate = True

    check(duplicate, "a second owner account cannot be created")

    weak_rejected = False
    try:
        await auth_service.hash_password("short")
    except InvalidOwnerPasswordError:
        weak_rejected = True

    check(weak_rejected, "weak passwords are rejected")

    return owner, user


async def _verify_singleton_constraint(session, user_id):
    """The CHECK (id = 1) constraint blocks any second owner row."""

    session.add(
        OwnerAccount(
            id=2,
            user_id=user_id,
            password_hash=auth_service.hash_password(PASSWORD),
        )
    )

    failed = False
    try:
        await session.flush()
    except IntegrityError:
        failed = True
        await session.rollback()

    check(failed, "PostgreSQL enforces the owner singleton constraint")


async def _verify_sessions(session, user_id):
    """Exercise session persistence, hashing, expiry, and revocation."""

    owner_ref = SimpleNamespace(user_id=user_id)

    raw_token, csrf_token, auth_session = await auth_service.create_session(
        session=session,
        owner=owner_ref,
    )

    session_id = auth_session.id

    stored = await _get_session_by_raw(session, raw_token)

    check(stored is not None, "session row persists in PostgreSQL")
    check(stored.token_hash != raw_token, "raw session token is never stored")
    check(
        stored.token_hash == hash_token(raw_token),
        "session token is stored as its SHA-256 hash",
    )
    check(
        stored.csrf_token_hash == hash_token(csrf_token),
        "CSRF token is stored only as its SHA-256 hash",
    )

    resolved = await auth_service.resolve_user_id(
        session=session,
        raw_token=raw_token,
        touch=False,
    )
    check(resolved == user_id, "valid session resolves the owner user id")

    check(
        auth_service.verify_csrf(stored, csrf_token),
        "valid CSRF token is accepted",
    )
    check(
        not auth_service.verify_csrf(stored, "wrong-csrf"),
        "invalid CSRF token is rejected",
    )

    # Expired sessions must not authenticate.
    now = datetime.now(timezone.utc)
    stored.created_at = now - timedelta(days=2)
    stored.expires_at = now - timedelta(minutes=1)
    await session.commit()

    expired = await auth_service.get_session_by_token(session, raw_token)
    check(expired is None, "expired session is rejected")

    # Revoked sessions must not authenticate.
    raw_token_2, _csrf_2, _session_2 = await auth_service.create_session(
        session=session,
        owner=owner_ref,
    )
    revoked = await auth_service.revoke_session(session, raw_token_2)
    check(revoked, "logout revokes the session row")

    after_revoke = await auth_service.get_session_by_token(
        session,
        raw_token_2,
    )
    check(after_revoke is None, "revoked session is rejected")

    # token_hash must be unique at the database level.
    stored_2 = await _get_session_by_raw(session, raw_token_2)
    duplicate = AuthSession(
        user_id=user_id,
        token_hash=stored_2.token_hash,
        csrf_token_hash=hash_token("duplicate-csrf"),
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session.add(duplicate)

    duplicate_failed = False
    try:
        await session.flush()
    except IntegrityError:
        duplicate_failed = True
        await session.rollback()

    check(
        duplicate_failed,
        "PostgreSQL enforces unique session token hashes",
    )

    # Re-fetch after rollback for cleanup-safe state.
    return session_id


async def main():

    if settings.DB_NAME != "asta_db":
        raise SystemExit(
            "Refusing to run: DB_NAME must be 'asta_db' "
            f"(currently '{settings.DB_NAME}')."
        )

    suffix = uuid.uuid4().hex[:10]
    created_owner = False
    session_user_ids = []

    async with AsyncSessionLocal() as session:

        try:
            existing_owner = await auth_service.get_owner(session)

            if existing_owner is None:
                owner, owner_user = await _verify_owner(session, suffix)
                created_owner = True
                owner_user_id = owner_user.id
                session_user_ids.append(owner_user_id)

                # A throwaway user for the singleton constraint check.
                constraint_user = User(
                    username=f"phase8_verify_constraint_{suffix}"
                )
                session.add(constraint_user)
                await session.commit()
                await session.refresh(constraint_user)
                session_user_ids.append(constraint_user.id)

                await _verify_singleton_constraint(
                    session,
                    constraint_user.id,
                )
                await _verify_sessions(session, owner_user_id)

                print(
                    "OK  real owner flow verified on PostgreSQL "
                    f"for throwaway user {owner_user_id}"
                )

            else:
                print(
                    "SKIP real owner creation: an owner already exists "
                    f"(user_id={existing_owner.user_id}); it is untouched."
                )

                constraint_user = User(
                    username=f"phase8_verify_constraint_{suffix}"
                )
                session.add(constraint_user)
                await session.commit()
                await session.refresh(constraint_user)
                session_user_ids.append(constraint_user.id)

                await _verify_singleton_constraint(
                    session,
                    constraint_user.id,
                )

                throwaway = User(
                    username=f"phase8_verify_session_{suffix}"
                )
                session.add(throwaway)
                await session.commit()
                await session.refresh(throwaway)
                session_user_ids.append(throwaway.id)

                await _verify_sessions(session, throwaway.id)

                print(
                    "OK  session lifecycle verified with a throwaway user "
                    f"{throwaway.id}"
                )

        finally:
            # Delete only rows created by this script.
            await session.rollback()

            for user_id in session_user_ids:
                await session.execute(
                    delete(AuthSession).where(AuthSession.user_id == user_id)
                )

            if created_owner:
                await session.execute(
                    delete(OwnerAccount).where(OwnerAccount.id == 1)
                )

            for user_id in session_user_ids:
                await session.execute(
                    delete(User).where(User.id == user_id)
                )

            await session.commit()
            print("OK  throwaway verification rows removed")

    print("Phase 8 real-PostgreSQL verification passed.")


if __name__ == "__main__":
    asyncio.run(main())
