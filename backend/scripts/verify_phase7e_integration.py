"""Real-PostgreSQL verification for Phase 7E integration and reliability.

This script verifies the Phase 7E changes against the live ``asta_db`` database:

* assistant ``provider`` and ``model`` metadata is persisted;
* persisted history is readable from a brand-new session (process restart);
* private messages are stored and remain scoped to their owner;
* ownership isolation rejects foreign conversations;
* a failed write rolls back without leaving partial rows;
* pending actions are conversation-scoped and single-use.

It refuses to run against any database other than ``asta_db`` and removes only
the throwaway users it creates (cascades remove their rows). External LLM
providers are never contacted.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

os.environ["DEBUG"] = "False"
os.environ["DB_ECHO"] = "False"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.core.config import settings
from app.database.connection import AsyncSessionLocal
from app.exceptions.conversation_exceptions import (
    ConversationNotFoundError,
    ConversationValidationError,
)
from app.exceptions.task_intent_exceptions import (
    PendingActionAlreadyResolvedError,
)
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.task_action import (
    PendingActionStatus,
    PendingActionType,
    TaskPendingAction,
)
from app.models.user import User
from app.services.conversation_service import conversation_service
from app.task_intent.pending_service import pending_action_service


def check(condition, message):
    if not condition:
        raise AssertionError(message)

    print(f"OK  {message}")


async def _count(session, model, *conditions):
    return int(
        await session.scalar(
            select(func.count()).select_from(model).where(*conditions)
        )
        or 0
    )


async def main():

    if settings.DB_NAME != "asta_db":
        raise SystemExit(
            f"Refusing to run: DB_NAME is {settings.DB_NAME!r}, expected 'asta_db'."
        )

    print(f"OK  connected to the expected database: {settings.DB_NAME}")

    suffix = uuid.uuid4().hex[:10]

    async with AsyncSessionLocal() as session:
        alice = User(username=f"phase7e_pg_alice_{suffix}")
        bob = User(username=f"phase7e_pg_bob_{suffix}")
        session.add_all([alice, bob])
        await session.commit()
        await session.refresh(alice)
        await session.refresh(bob)

        alice_id, bob_id = alice.id, bob.id

        try:
            conversation = await conversation_service.create_conversation(
                session=session,
                user_id=alice_id,
                title="Phase 7E verification",
            )
            conversation_id = conversation.id

            # --- message metadata persistence ------------------------------
            user_message = await conversation_service.add_message(
                session=session,
                conversation=conversation,
                role=MessageRole.USER,
                content="Remember that I prefer concise answers.",
            )
            assistant_message = await conversation_service.add_message(
                session=session,
                conversation=conversation,
                role=MessageRole.ASSISTANT,
                content="Understood.",
                provider="groq",
                model="openai/gpt-oss-20b",
            )
            private_message = await conversation_service.add_message(
                session=session,
                conversation=conversation,
                role=MessageRole.USER,
                content="This one is private.",
                is_private=True,
            )

            check(user_message.id > 0, "user message persisted with an id")
            check(
                assistant_message.provider == "groq"
                and assistant_message.model == "openai/gpt-oss-20b",
                "assistant provider and model metadata persisted",
            )
            check(
                private_message.is_private is True,
                "private flag persisted on the message",
            )
            check(
                user_message.provider is None and user_message.model is None,
                "user messages carry no assistant metadata",
            )

            # --- restart: read through a brand-new session ----------------
            async with AsyncSessionLocal() as restarted_session:
                messages, total = await conversation_service.get_messages(
                    session=restarted_session,
                    user_id=alice_id,
                    conversation_id=conversation_id,
                )

                check(total == 3, "all messages readable after a new session")
                check(
                    [message.role for message in messages]
                    == [
                        MessageRole.USER,
                        MessageRole.ASSISTANT,
                        MessageRole.USER,
                    ],
                    "messages are returned in chronological order",
                )

                stored = [
                    message
                    for message in messages
                    if message.role == MessageRole.ASSISTANT
                ][0]
                check(
                    stored.provider == "groq"
                    and stored.model == "openai/gpt-oss-20b",
                    "provider and model survive a session restart",
                )

                recent = await conversation_service.get_recent_messages(
                    session=restarted_session,
                    user_id=alice_id,
                    conversation_id=conversation_id,
                    limit=1,
                )
                check(
                    len(recent) == 1
                    and recent[0].content == "This one is private.",
                    "recent-history limit returns the latest message",
                )

            # --- ownership isolation --------------------------------------
            try:
                await conversation_service.get_conversation(
                    session=session,
                    user_id=bob_id,
                    conversation_id=conversation_id,
                )
                raise AssertionError("another user read a foreign conversation")

            except ConversationNotFoundError:
                print("OK  foreign conversation access is rejected")

            foreign_messages = await _count(
                session,
                Message,
                Message.conversation_id == conversation_id,
                Message.provider == "groq",
            )
            check(
                foreign_messages == 1,
                "only the owning conversation holds the assistant metadata row",
            )

            # --- failed write rolls back ----------------------------------
            before = await _count(
                session,
                Message,
                Message.conversation_id == conversation_id,
            )

            try:
                await conversation_service.add_message(
                    session=session,
                    conversation=conversation,
                    role=MessageRole.USER,
                    content="   ",
                )
                raise AssertionError("blank message was accepted")

            except ConversationValidationError:
                print("OK  blank message is rejected")

            after = await _count(
                session,
                Message,
                Message.conversation_id == conversation_id,
            )
            check(
                before == after,
                "rejected write leaves no partial message row",
            )

            # --- conversation-scoped, single-use pending actions ----------
            action = await pending_action_service.create_pending(
                session=session,
                user_id=alice_id,
                conversation_id=conversation_id,
                action_type=PendingActionType.CREATE,
                payload={
                    "title": "Phase 7E verification task",
                    "description": "Created by the Phase 7E check.",
                    "category": "project",
                    "priority": "high",
                    "due_date": None,
                    "due_time": None,
                    "timezone": None,
                },
                summary="Verification proposal",
            )

            other_conversation = await conversation_service.create_conversation(
                session=session,
                user_id=alice_id,
                title="Phase 7E other conversation",
            )

            other_pending = await pending_action_service.list_pending(
                session=session,
                user_id=alice_id,
                conversation_id=other_conversation.id,
            )
            check(
                other_pending == [],
                "pending actions are scoped to their conversation",
            )

            claimed = await pending_action_service.claim(
                session=session,
                user_id=alice_id,
                action_id=action.id,
            )
            check(
                claimed.status == PendingActionStatus.EXECUTED,
                "claim atomically marks the action executed",
            )

            try:
                await pending_action_service.claim(
                    session=session,
                    user_id=alice_id,
                    action_id=action.id,
                )
                raise AssertionError("duplicate claim succeeded")

            except PendingActionAlreadyResolvedError:
                print("OK  duplicate claim is rejected")

            action_count = await _count(
                session,
                TaskPendingAction,
                TaskPendingAction.user_id == alice_id,
            )
            check(action_count >= 1, "pending action rows persist in PostgreSQL")

            conversation_count = await _count(
                session,
                Conversation,
                Conversation.user_id == alice_id,
            )
            check(
                conversation_count == 2,
                "both throwaway conversations persisted",
            )

        finally:
            await session.rollback()
            await session.delete(await session.get(User, alice_id))
            await session.delete(await session.get(User, bob_id))
            await session.commit()

    print("Phase 7E PostgreSQL verification passed.")


if __name__ == "__main__":
    asyncio.run(main())
