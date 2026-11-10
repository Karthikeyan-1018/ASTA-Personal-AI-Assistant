"""Real-PostgreSQL verification for Phase 7D pending actions.

Creates a throwaway user/conversation/task, exercises persistence, atomic
claiming, duplicate protection, expiry, ownership scoping, and execution, then
deletes only the throwaway user (cascades remove its rows).
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["DEBUG"] = "False"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, text

from app.database.connection import AsyncSessionLocal
from app.exceptions.task_intent_exceptions import (
    PendingActionAlreadyResolvedError,
    PendingActionExpiredError,
    PendingActionNotFoundError,
)
from app.models.task_action import (
    PendingActionStatus,
    PendingActionType,
    TaskPendingAction,
)
from app.models.user import User
from app.services.conversation_service import conversation_service
from app.services.task_service import task_service
from app.task_intent.executor import task_action_executor
from app.task_intent.pending_service import pending_action_service


def check(condition, message):
    if not condition:
        raise AssertionError(message)

    print(f"OK  {message}")


async def main():

    suffix = uuid.uuid4().hex[:10]

    async with AsyncSessionLocal() as session:
        alice = User(username=f"nl_pg_alice_{suffix}")
        bob = User(username=f"nl_pg_bob_{suffix}")
        session.add_all([alice, bob])
        await session.commit()
        await session.refresh(alice)
        await session.refresh(bob)

        alice_id, bob_id = alice.id, bob.id

        try:
            conversation = await conversation_service.create_conversation(
                session=session,
                user_id=alice_id,
                title="Phase 7D verification",
            )
            conversation_id = conversation.id

            action = await pending_action_service.create_pending(
                session=session,
                user_id=alice_id,
                conversation_id=conversation_id,
                action_type=PendingActionType.CREATE,
                payload={
                    "title": "Phase 7D verification task",
                    "description": "Created by the PostgreSQL check.",
                    "category": "project",
                    "priority": "high",
                    "due_date": None,
                    "due_time": None,
                    "timezone": None,
                },
                summary="Verification proposal",
            )

            check(action.id > 0, "pending action persisted with an id")
            check(
                action.status == PendingActionStatus.PENDING,
                "pending action starts pending",
            )
            check(
                action.expires_at > action.created_at,
                "expiry is after creation",
            )

            listed = await pending_action_service.list_pending(
                session=session,
                user_id=alice_id,
                conversation_id=conversation_id,
            )
            check(
                [item.id for item in listed] == [action.id],
                "pending action is listed for its owner and conversation",
            )

            try:
                await pending_action_service.get_action(
                    session=session,
                    user_id=bob_id,
                    action_id=action.id,
                )
                raise AssertionError("another user read the pending action")

            except PendingActionNotFoundError:
                print("OK  another user cannot read the pending action")

            claimed = await pending_action_service.claim(
                session=session,
                user_id=alice_id,
                action_id=action.id,
            )
            check(
                claimed.status == PendingActionStatus.EXECUTED,
                "claim atomically moves the action to executed",
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

            task = await task_action_executor.execute(
                session=session,
                user_id=alice_id,
                action=claimed,
            )
            check(task is not None and task.id > 0, "confirmed task created")

            await pending_action_service.mark_result(
                session=session,
                user_id=alice_id,
                action_id=action.id,
                result_task_id=task.id,
            )

            stored = await pending_action_service.get_action(
                session=session,
                user_id=alice_id,
                action_id=action.id,
            )
            check(
                stored.result_task_id == task.id,
                "result task id is persisted",
            )

            # Expiry: move a fresh pending action into the past.
            expiring = await pending_action_service.create_pending(
                session=session,
                user_id=alice_id,
                conversation_id=conversation_id,
                action_type=PendingActionType.COMPLETE,
                payload={},
                summary="Expiry verification",
                target_task_id=task.id,
            )

            now = datetime.now(timezone.utc)
            await session.execute(
                text(
                    "UPDATE task_pending_actions "
                    "SET created_at = :created, expires_at = :expires "
                    "WHERE id = :id"
                ),
                {
                    "created": now - timedelta(seconds=20),
                    "expires": now - timedelta(seconds=10),
                    "id": expiring.id,
                },
            )
            await session.commit()
            await session.refresh(expiring)

            remaining = await pending_action_service.list_pending(
                session=session,
                user_id=alice_id,
                conversation_id=conversation_id,
            )
            check(
                all(item.id != expiring.id for item in remaining),
                "expired action is excluded from the pending list",
            )

            try:
                await pending_action_service.claim(
                    session=session,
                    user_id=alice_id,
                    action_id=expiring.id,
                )
                raise AssertionError("expired action was claimed")

            except PendingActionExpiredError:
                print("OK  expired action cannot be claimed")

            count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(TaskPendingAction)
                    .where(TaskPendingAction.user_id == alice_id)
                )
                or 0
            )
            check(count >= 2, "pending rows persist in PostgreSQL")

        finally:
            await session.rollback()
            await session.delete(
                await session.get(User, alice_id)
            )
            await session.delete(
                await session.get(User, bob_id)
            )
            await session.commit()

    print("Phase 7D PostgreSQL verification passed.")


if __name__ == "__main__":
    asyncio.run(main())
