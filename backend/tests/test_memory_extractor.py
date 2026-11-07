import json
import unittest
from unittest.mock import ANY, AsyncMock, patch

from app.exceptions.llm_exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.exceptions.memory_exceptions import (
    MemoryExtractionError,
    MemoryValidationError,
)
from app.llm.llm_router import llm_router
from app.memory.memory_extractor import memory_extractor
from app.schemas.memory_extraction import MemoryCategory


class MemoryExtractorTests(unittest.IsolatedAsyncioTestCase):
    """Exercise MemoryExtractor with a mocked LLM router."""

    async def asyncSetUp(self):
        self.generate_response = AsyncMock()
        self.router_patcher = patch.object(
            llm_router,
            "generate_response",
            new=self.generate_response
        )
        self.router_patcher.start()

    async def asyncTearDown(self):
        self.router_patcher.stop()

    def _respond_with(self, payload, provider: str = "test"):
        raw = payload if isinstance(payload, str) else json.dumps(payload)

        self.generate_response.return_value = {
            "provider": provider,
            "model": "test-model",
            "response": raw
        }

    async def _extract(self, conversation: str, private: bool = False):
        return await memory_extractor.extract(
            conversation=conversation,
            private=private
        )

    # ------------------------------------------------------------------
    # Happy-path categories
    # ------------------------------------------------------------------

    async def test_extracts_career_information(self):

        self._respond_with({
            "memories": [
                {
                    "content": "Works as a backend engineer at Acme.",
                    "category": "career",
                    "explanation": "Useful for future career advice."
                }
            ]
        })

        candidates = await self._extract(
            "I work as a backend engineer at Acme."
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].content,
            "Works as a backend engineer at Acme."
        )
        self.assertEqual(candidates[0].category, MemoryCategory.CAREER)
        self.assertIn("career", candidates[0].explanation)

    async def test_extracts_education_information(self):

        self._respond_with({
            "memories": [
                {
                    "content": "Is studying a Bachelor of Computer Science.",
                    "category": "education",
                    "explanation": "Track the user's studies over time."
                }
            ]
        })

        candidates = await self._extract(
            "I am studying a Bachelor of Computer Science."
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].category, MemoryCategory.EDUCATION)

    async def test_extracts_project_information(self):

        self._respond_with({
            "memories": [
                {
                    "content": "Is building ASTA, a personal AI assistant.",
                    "category": "projects",
                    "explanation": "Remember the user's ongoing project."
                }
            ]
        })

        candidates = await self._extract(
            "I am building an AI assistant called ASTA."
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].category, MemoryCategory.PROJECTS)

    async def test_extracts_multiple_candidates(self):

        self._respond_with({
            "memories": [
                {
                    "content": "Lives in London.",
                    "category": "personal",
                    "explanation": "Useful personal context."
                },
                {
                    "content": "Wants to learn Portuguese.",
                    "category": "goals",
                    "explanation": "Long-term learning goal."
                }
            ]
        })

        candidates = await self._extract(
            "I live in London and want to learn Portuguese."
        )

        self.assertEqual(len(candidates), 2)
        self.assertEqual(
            [candidate.category for candidate in candidates],
            [MemoryCategory.PERSONAL, MemoryCategory.GOALS]
        )

    async def test_normalises_category_case_and_aliases(self):

        self._respond_with({
            "memories": [
                {
                    "content": "Works remotely.",
                    "category": "  Work  ",
                    "explanation": "Working preference."
                }
            ]
        })

        candidates = await self._extract("I work remotely.")

        self.assertEqual(candidates[0].category, MemoryCategory.CAREER)

    # ------------------------------------------------------------------
    # Empty and malformed responses
    # ------------------------------------------------------------------

    async def test_no_useful_information_returns_empty_list(self):

        self._respond_with({"memories": []})

        candidates = await self._extract(
            "Today is Tuesday and I had a sandwich."
        )

        self.assertEqual(candidates, [])

    async def test_empty_conversation_is_rejected_without_calling_llm(self):

        for conversation in ("", "   ", "\n\t "):

            with self.subTest(conversation=conversation):

                with self.assertRaises(MemoryValidationError):
                    await self._extract(conversation)

        self.generate_response.assert_not_awaited()

    async def test_invalid_json_raises_extraction_error(self):

        self._respond_with("this is not json")

        with self.assertRaises(MemoryExtractionError):
            await self._extract("I live in London.")

    async def test_missing_required_field_raises_extraction_error(self):

        self._respond_with({
            "memories": [
                {
                    "content": "Lives in London.",
                    "category": "personal"
                }
            ]
        })

        with self.assertRaises(MemoryExtractionError):
            await self._extract("I live in London.")

    async def test_unknown_category_raises_extraction_error(self):

        self._respond_with({
            "memories": [
                {
                    "content": "Lives in London.",
                    "category": "favourite-colour",
                    "explanation": "Not a supported category."
                }
            ]
        })

        with self.assertRaises(MemoryExtractionError):
            await self._extract("I live in London.")

    async def test_empty_llm_response_raises_extraction_error(self):

        self._respond_with("")

        with self.assertRaises(MemoryExtractionError):
            await self._extract("I live in London.")

    async def test_malformed_router_result_raises_extraction_error(self):

        self.generate_response.return_value = {
            "provider": "test",
            "model": "test-model"
        }

        with self.assertRaises(MemoryExtractionError):
            await self._extract("I live in London.")

    async def test_markdown_fenced_json_is_accepted(self):

        payload = {
            "memories": [
                {
                    "content": "Likes Python.",
                    "category": "preferences",
                    "explanation": "Coding preference."
                }
            ]
        }

        self._respond_with(
            "```json\n" + json.dumps(payload) + "\n```"
        )

        candidates = await self._extract("I like Python.")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].category, MemoryCategory.PREFERENCES)

    async def test_bare_json_array_is_accepted(self):

        self._respond_with([
            {
                "content": "Likes tea.",
                "category": "preferences",
                "explanation": "Stable preference."
            }
        ])

        candidates = await self._extract("I like tea.")

        self.assertEqual(len(candidates), 1)

    # ------------------------------------------------------------------
    # Secret filtering and de-duplication
    # ------------------------------------------------------------------

    async def test_secret_candidates_are_dropped(self):

        self._respond_with({
            "memories": [
                {
                    "content": "api_key=sk-1234567890abcdef",
                    "category": "personal",
                    "explanation": "Looks like a secret."
                },
                {
                    "content": "Lives in London.",
                    "category": "personal",
                    "explanation": "Useful personal context."
                },
                {
                    "content": "Uses token=abcdef123456.",
                    "category": "personal",
                    "explanation": "Contains a token."
                }
            ]
        })

        candidates = await self._extract(
            "My key is api_key=sk-1234567890abcdef and I live in London."
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].content, "Lives in London.")

    async def test_secret_in_explanation_drops_candidate(self):

        self._respond_with({
            "memories": [
                {
                    "content": "Lives in London.",
                    "category": "personal",
                    "explanation": "password=hunter2"
                }
            ]
        })

        candidates = await self._extract("I live in London.")

        self.assertEqual(candidates, [])

    async def test_duplicate_candidates_are_removed(self):

        self._respond_with({
            "memories": [
                {
                    "content": "Lives in London.",
                    "category": "personal",
                    "explanation": "First mention."
                },
                {
                    "content": "  lives   in london.  ",
                    "category": "personal",
                    "explanation": "Duplicate mention."
                }
            ]
        })

        candidates = await self._extract(
            "I live in London. I really do live in London."
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].explanation, "First mention.")

    # ------------------------------------------------------------------
    # Router interaction and structured LLM errors
    # ------------------------------------------------------------------

    async def test_uses_router_with_private_flag(self):

        self._respond_with({"memories": []})

        await self._extract("I live in London.", private=True)

        self.generate_response.assert_awaited_once()
        _, kwargs = self.generate_response.await_args
        self.assertTrue(kwargs["private"])
        self.assertIn("I live in London.", kwargs["prompt"])

    async def test_timeout_propagates(self):

        self.generate_response.side_effect = LLMTimeoutError("timeout")

        with self.assertRaises(LLMTimeoutError):
            await self._extract("I live in London.")

    async def test_provider_failure_propagates(self):

        self.generate_response.side_effect = LLMProviderError("failed")

        with self.assertRaises(LLMProviderError):
            await self._extract("I live in London.")

    async def test_unavailable_provider_propagates(self):

        self.generate_response.side_effect = LLMUnavailableError(
            "unavailable"
        )

        with self.assertRaises(LLMUnavailableError):
            await self._extract("I live in London.")

    async def test_oversized_conversation_is_rejected(self):

        with self.assertRaises(MemoryValidationError):
            await self._extract("a" * 50001)

        self.generate_response.assert_not_awaited()

    async def test_router_receives_only_expected_arguments(self):

        self._respond_with({"memories": []})

        await self._extract("I live in London.")

        self.generate_response.assert_awaited_once_with(
            prompt=ANY,
            private=False
        )


if __name__ == "__main__":
    unittest.main()
