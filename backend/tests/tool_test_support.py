"""Shared fixtures for the Phase 11 tool-engine tests.

The base class mirrors the existing SQLite test setup used elsewhere in the
project: in-memory SQLite with ``StaticPool`` so every request sees the same
database. Tests that need real PostgreSQL live in a separate opt-in module.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers every ORM table)
from app.core.config import settings
from app.database.base import Base
from app.models.user import User
from app.tools import os_security
from app.tools.base import BaseTool, ToolContext
from app.tools.enums import ToolCategory
from app.tools.executor import tool_executor
from app.tools.result import success_result
from app.tools.schemas import StrictModel, ToolResult


class EmptyInput(StrictModel):
    pass


class EchoInput(StrictModel):
    value: int = 1


class EchoTool(BaseTool):
    name = "test_echo"
    description = "Return a fixed successful payload."
    category = ToolCategory.READ_ONLY
    input_model = EchoInput

    async def execute(self, context, arguments) -> ToolResult:
        return success_result(
            tool_name=self.name,
            data={"ok": True, "value": arguments.value},
        )


class SlowTool(BaseTool):
    name = "test_slow"
    description = "Sleep longer than the configured timeout."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments) -> ToolResult:
        await asyncio.sleep(5)
        return success_result(tool_name=self.name, data={})


class FailingTool(BaseTool):
    name = "test_failing"
    description = "Raise an unexpected error."
    category = ToolCategory.READ_ONLY
    input_model = EmptyInput

    async def execute(self, context, arguments) -> ToolResult:
        raise RuntimeError("boom")


class ToolEngineTestBase(unittest.IsolatedAsyncioTestCase):
    """Two owners, an isolated database, and an isolated workspace root."""

    async def asyncSetUp(self):
        # The OS automation rate limiter and emergency stop are process-local
        # by design, so reset them for deterministic, isolated tests.
        os_security.reset_state_for_tests()

        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.SessionLocal = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        async with self.SessionLocal() as session:
            alice = User(username=f"tool_alice_{id(self)}")
            bob = User(username=f"tool_bob_{id(self)}")
            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)
            self.alice_id = alice.id
            self.bob_id = bob.id

        self.workspace = tempfile.TemporaryDirectory(
            prefix="asta_tool_workspace_"
        )

    async def asyncTearDown(self):
        os_security.reset_state_for_tests()
        self.workspace.cleanup()
        await self.engine.dispose()

    def settings(self, **values):
        """Patch settings for the duration of the test."""

        for name, value in values.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def context(self, session, user_id=None):
        return ToolContext(
            session=session,
            user_id=user_id or self.alice_id,
            conversation_id=None,
            private=False,
        )

    async def execute(
        self,
        tool_name,
        arguments=None,
        *,
        user_id=None,
        confirmation_id=None,
        create_confirmation=True,
        idempotency_key=None,
    ):
        async with self.SessionLocal() as session:
            context = await self.context(session, user_id)
            return await tool_executor.execute(
                tool_name=tool_name,
                arguments=arguments or {},
                context=context,
                confirmation_id=confirmation_id,
                create_confirmation=create_confirmation,
                idempotency_key=idempotency_key,
            )
