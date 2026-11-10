"""Preference CRUD, ownership, suggestion, and extraction tests."""

import json
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all model tables)
from app.api.dependencies import get_current_user_id
from app.database.base import Base
from app.database.connection import get_db
from app.llm.llm_router import llm_router
from app.main import app
from app.memory.preference_extractor import preference_extractor
from app.models.preference import (
    PreferenceSource,
    PreferenceStatus,
    UserPreference,
)
from app.models.user import User
from app.schemas.preference import ExtractedPreferenceCandidate
from app.services.preference_service import preference_service


class PreferenceAPITests(unittest.IsolatedAsyncioTestCase):
    """Exercise /api/preferences with an isolated database."""

    async def asyncSetUp(self):

        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.SessionLocal = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False
        )

        async with self.SessionLocal() as session:
            alice = User(username="preference_alice")
            bob = User(username="preference_bob")

            session.add_all([alice, bob])
            await session.commit()
            await session.refresh(alice)
            await session.refresh(bob)

            self.alice_id = alice.id
            self.bob_id = bob.id

        async def override_get_db():
            async with self.SessionLocal() as session:
                yield session

        async def override_alice():
            return self.alice_id

        self.override_alice = override_alice

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user_id] = override_alice

        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        )

    async def asyncTearDown(self):

        await self.client.aclose()
        app.dependency_overrides.clear()
        await self.engine.dispose()

    def _as_user(self, user_id: int):

        async def override():
            return user_id

        app.dependency_overrides[get_current_user_id] = override

    def _restore_alice(self):
        app.dependency_overrides[get_current_user_id] = self.override_alice

    async def _create_preference(
        self,
        key: str = "preferred_language",
        value: str = "I prefer Python for AI projects.",
        category: str = "programming"
    ):
        response = await self.client.post(
            "/api/preferences/",
            json={"key": key, "value": value, "category": category}
        )

        self.assertEqual(response.status_code, 201, response.text)

        return response.json()

    async def _count_preferences(self, user_id: int) -> int:

        async with self.SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(UserPreference)
                    .where(UserPreference.user_id == user_id)
                ) or 0
            )

    def _candidate(
        self,
        key: str = "explanation_style",
        value: str = "Prefer beginner-friendly explanations.",
        category: str = "communication"
    ) -> ExtractedPreferenceCandidate:

        return ExtractedPreferenceCandidate(
            key=key,
            value=value,
            category=category,
            explanation="The user explicitly requested this style."
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def test_create_preference(self):

        body = await self._create_preference()

        self.assertEqual(body["key"], "preferred_language")
        self.assertEqual(body["category"], "programming")
        self.assertEqual(body["status"], "active")
        self.assertEqual(body["source"], "explicit")
        self.assertEqual(body["user_id"], self.alice_id)

    async def test_create_preference_normalises_key(self):

        body = await self._create_preference(key="Preferred Language")

        self.assertEqual(body["key"], "preferred_language")

    async def test_get_preference(self):

        created = await self._create_preference()

        response = await self.client.get(
            f"/api/preferences/{created['id']}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], created["id"])

    async def test_list_preferences_and_filter_by_category(self):

        await self._create_preference(
            key="preferred_language",
            category="programming"
        )
        await self._create_preference(
            key="explanation_style",
            value="Explain concepts like I am a beginner.",
            category="communication"
        )

        everything = await self.client.get("/api/preferences/")
        self.assertEqual(everything.status_code, 200)
        self.assertEqual(len(everything.json()), 2)

        filtered = await self.client.get(
            "/api/preferences/?category=programming"
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(len(filtered.json()), 1)
        self.assertEqual(
            filtered.json()[0]["key"],
            "preferred_language"
        )

    async def test_list_preferences_filter_by_status(self):

        first = await self._create_preference()
        await self._create_preference(
            key="career_focus",
            value="I want to focus on AI/ML engineering.",
            category="career"
        )

        await self.client.post(
            f"/api/preferences/{first['id']}/deactivate"
        )

        active = await self.client.get("/api/preferences/?status=active")
        inactive = await self.client.get(
            "/api/preferences/?status=inactive"
        )

        self.assertEqual(
            [item["key"] for item in active.json()],
            ["career_focus"]
        )
        self.assertEqual(
            [item["key"] for item in inactive.json()],
            ["preferred_language"]
        )

    async def test_update_preference(self):

        created = await self._create_preference()

        response = await self.client.patch(
            f"/api/preferences/{created['id']}",
            json={
                "value": "I prefer Python and PyTorch.",
                "category": "programming"
            }
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["value"],
            "I prefer Python and PyTorch."
        )
        self.assertEqual(response.json()["status"], "active")

    async def test_update_preference_conflicting_key(self):

        first = await self._create_preference()
        await self._create_preference(
            key="career_focus",
            category="career"
        )

        response = await self.client.patch(
            f"/api/preferences/{first['id']}",
            json={"key": "career_focus"}
        )

        self.assertEqual(response.status_code, 409)

    async def test_delete_preference(self):

        created = await self._create_preference()

        deleted = await self.client.delete(
            f"/api/preferences/{created['id']}"
        )

        self.assertEqual(deleted.status_code, 204)

        missing = await self.client.get(
            f"/api/preferences/{created['id']}"
        )

        self.assertEqual(missing.status_code, 404)

    async def test_deactivate_preference(self):

        created = await self._create_preference()

        response = await self.client.post(
            f"/api/preferences/{created['id']}/deactivate"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "inactive")

    async def test_duplicate_preference_is_rejected(self):

        await self._create_preference(key="Preferred Language")

        duplicate = await self.client.post(
            "/api/preferences/",
            json={
                "key": "preferred-language",
                "value": "I prefer Rust.",
                "category": "programming"
            }
        )

        self.assertEqual(duplicate.status_code, 409)

    async def test_secret_value_is_rejected(self):

        response = await self.client.post(
            "/api/preferences/",
            json={
                "key": "api_key",
                "value": "api_key: gsk_abcdefghijklmnop",
                "category": "general"
            }
        )

        self.assertEqual(response.status_code, 400)

    async def test_secret_key_is_rejected(self):

        response = await self.client.post(
            "/api/preferences/",
            json={
                "key": "gsk_abcdefghijklmnop",
                "value": "Some preference",
                "category": "general"
            }
        )

        self.assertEqual(response.status_code, 400)

    async def test_invalid_category_is_rejected_by_schema(self):

        response = await self.client.post(
            "/api/preferences/",
            json={
                "key": "something",
                "value": "Some value",
                "category": "not_a_category"
            }
        )

        self.assertEqual(response.status_code, 422)

    # ------------------------------------------------------------------
    # Ownership isolation
    # ------------------------------------------------------------------

    async def test_user_ownership_isolation(self):

        self._as_user(self.bob_id)

        bob_preference = await self._create_preference(
            key="bob_only",
            value="Bob's preference.",
            category="general"
        )

        self._restore_alice()

        get_response = await self.client.get(
            f"/api/preferences/{bob_preference['id']}"
        )
        patch_response = await self.client.patch(
            f"/api/preferences/{bob_preference['id']}",
            json={"value": "Hijacked"}
        )
        delete_response = await self.client.delete(
            f"/api/preferences/{bob_preference['id']}"
        )
        approve_response = await self.client.post(
            f"/api/preferences/suggestions/{bob_preference['id']}/approve"
        )

        self.assertEqual(get_response.status_code, 404)
        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)
        self.assertEqual(approve_response.status_code, 404)

        listing = await self.client.get("/api/preferences/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json(), [])

    # ------------------------------------------------------------------
    # Suggestions and extraction
    # ------------------------------------------------------------------

    async def test_extract_returns_candidates_without_persisting(self):

        candidate = self._candidate()
        extract_mock = AsyncMock(return_value=[candidate])

        with patch.object(
            preference_extractor,
            "extract",
            new=extract_mock
        ):
            response = await self.client.post(
                "/api/preferences/extract",
                json={
                    "conversation": "Please explain concepts like a beginner."
                }
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["candidates"][0]["key"],
            "explanation_style"
        )
        self.assertEqual(await self._count_preferences(self.alice_id), 0)

    async def test_suggestion_approval(self):

        candidate = self._candidate()
        extract_mock = AsyncMock(return_value=[candidate])

        with patch.object(
            preference_extractor,
            "extract",
            new=extract_mock
        ):
            created = await self.client.post(
                "/api/preferences/suggestions/",
                json={
                    "conversation": "Please explain concepts like a beginner."
                }
            )

        self.assertEqual(created.status_code, 201, created.text)

        suggestions = created.json()["suggestions"]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["status"], "pending")
        self.assertEqual(suggestions[0]["source"], "inferred")

        # Pending suggestions must not be part of active context.
        active = await self.client.get("/api/preferences/?status=active")
        self.assertEqual(active.json(), [])

        approved = await self.client.post(
            f"/api/preferences/suggestions/{suggestions[0]['id']}/approve"
        )

        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["status"], "active")
        self.assertEqual(approved.json()["source"], "inferred")

        duplicate = await self.client.post(
            f"/api/preferences/suggestions/{suggestions[0]['id']}/approve"
        )
        self.assertEqual(duplicate.status_code, 400)

    async def test_suggestion_rejection(self):

        candidate = self._candidate()
        extract_mock = AsyncMock(return_value=[candidate])

        with patch.object(
            preference_extractor,
            "extract",
            new=extract_mock
        ):
            created = await self.client.post(
                "/api/preferences/suggestions/",
                json={"conversation": "Give me beginner explanations."}
            )

        suggestion_id = created.json()["suggestions"][0]["id"]

        rejected = await self.client.post(
            f"/api/preferences/suggestions/{suggestion_id}/reject"
        )

        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()["status"], "rejected")

        active = await self.client.get("/api/preferences/?status=active")
        self.assertEqual(active.json(), [])

        listed = await self.client.get("/api/preferences/suggestions/")
        self.assertEqual(listed.json()["suggestions"], [])

    async def test_suggestions_do_not_duplicate_existing_preference(self):

        await self._create_preference(
            key="explanation_style",
            value="Beginner-friendly.",
            category="communication"
        )

        candidate = self._candidate()
        extract_mock = AsyncMock(return_value=[candidate])

        with patch.object(
            preference_extractor,
            "extract",
            new=extract_mock
        ):
            created = await self.client.post(
                "/api/preferences/suggestions/",
                json={"conversation": "Explain like a beginner."}
            )

        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["suggestions"], [])
        self.assertEqual(await self._count_preferences(self.alice_id), 1)

    async def test_suggestion_list_excludes_explicit_preferences(self):

        await self._create_preference()

        listed = await self.client.get("/api/preferences/suggestions/")

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["suggestions"], [])


class PreferenceExtractorTests(unittest.IsolatedAsyncioTestCase):
    """Validate extraction parsing, filtering, and privacy routing."""

    def _patch_llm(self, payload: object):
        stack = ExitStack()
        mock = AsyncMock(return_value={"response": payload})

        stack.enter_context(
            patch.object(llm_router, "generate_response", new=mock)
        )

        return stack, mock

    async def test_extractor_filters_secrets_temporary_and_duplicates(self):

        raw = json.dumps({
            "preferences": [
                {
                    "key": "explanation_style",
                    "value": "Prefer beginner-friendly explanations.",
                    "category": "communication",
                    "explanation": "The user asked for this."
                },
                {
                    "key": "explanation_style",
                    "value": "Prefer beginner-friendly explanations.",
                    "category": "communication",
                    "explanation": "Duplicate candidate."
                },
                {
                    "key": "temporary_detail",
                    "value": "Give a short answer today.",
                    "category": "communication",
                    "explanation": "Temporary request."
                },
                {
                    "key": "api_key",
                    "value": "api_key: gsk_abcdefghijklmnop",
                    "category": "general",
                    "explanation": "Looks like a secret."
                }
            ]
        })

        stack, mock = self._patch_llm(raw)

        with stack:
            candidates = await preference_extractor.extract(
                "Please explain like a beginner."
            )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].key, "explanation_style")
        mock.assert_awaited_once()
        self.assertFalse(mock.await_args.kwargs["private"])

    async def test_private_extraction_uses_private_routing(self):

        raw = json.dumps({
            "preferences": [
                {
                    "key": "explanation_style",
                    "value": "Prefer beginner-friendly explanations.",
                    "category": "communication",
                    "explanation": "Explicit request."
                }
            ]
        })

        stack, mock = self._patch_llm(raw)

        with stack:
            await preference_extractor.extract(
                "Private conversation",
                private=True
            )

        self.assertTrue(mock.await_args.kwargs["private"])

    async def test_invalid_json_raises_extraction_error(self):

        stack, _mock = self._patch_llm("not json at all")

        with stack:
            with self.assertRaises(Exception):
                await preference_extractor.extract("Some text")

    async def test_empty_conversation_is_rejected(self):

        with self.assertRaises(Exception):
            await preference_extractor.extract("   ")


class PreferenceServiceTests(unittest.IsolatedAsyncioTestCase):
    """Direct service checks for lifecycle behavior."""

    async def asyncSetUp(self):

        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.SessionLocal = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False
        )

        async with self.SessionLocal() as session:
            user = User(username="preference_service_user")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            self.user_id = user.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_suggestion_lifecycle(self):

        async with self.SessionLocal() as session:
            suggestion = await preference_service.create_suggestion(
                session=session,
                user_id=self.user_id,
                key="Explanation Style",
                value="Prefer beginner-friendly explanations.",
                category="communication"
            )

            self.assertIsNotNone(suggestion)
            self.assertEqual(suggestion.key, "explanation_style")
            self.assertEqual(suggestion.status, PreferenceStatus.PENDING)
            self.assertEqual(suggestion.source, PreferenceSource.INFERRED)

            duplicate = await preference_service.create_suggestion(
                session=session,
                user_id=self.user_id,
                key="explanation-style",
                value="Different value.",
                category="communication"
            )

            self.assertIsNone(duplicate)

            active = await preference_service.get_active_preferences(
                session=session,
                user_id=self.user_id
            )

            self.assertEqual(active, [])

            approved = await preference_service.approve_suggestion(
                session=session,
                user_id=self.user_id,
                suggestion_id=suggestion.id
            )

            self.assertEqual(approved.status, PreferenceStatus.ACTIVE)

            active = await preference_service.get_active_preferences(
                session=session,
                user_id=self.user_id
            )

            self.assertEqual([item.id for item in active], [suggestion.id])


if __name__ == "__main__":
    unittest.main()
