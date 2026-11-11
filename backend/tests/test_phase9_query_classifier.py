import unittest
from unittest.mock import AsyncMock, patch

from app.ai.query_classifier import (
    QueryComplexity,
    classify_query,
    classify_query_rules,
)
from app.core.config import settings
from app.llm.llm_router import llm_router


class DeterministicClassificationTests(unittest.TestCase):

    def test_private_requests_are_always_private(self):

        result = classify_query_rules(
            "Add a task for tomorrow and compare strategies",
            private=True,
        )

        self.assertEqual(result.complexity, QueryComplexity.PRIVATE)
        self.assertEqual(result.confidence, 1.0)
        self.assertFalse(result.cache_eligible)

    def test_task_mutation_words_are_task_actions(self):

        for query in (
            "Add a task to call the dentist",
            "Mark my revision task as done",
            "confirm it",
        ):
            with self.subTest(query=query):
                result = classify_query_rules(query)
                self.assertEqual(
                    result.complexity,
                    QueryComplexity.TASK_ACTION,
                )
                self.assertFalse(result.cache_eligible)

    def test_time_sensitive_queries_are_dynamic(self):

        for query in (
            "What is the weather tomorrow?",
            "What tasks do I have due today?",
            "What should I study next?",
        ):
            with self.subTest(query=query):
                result = classify_query_rules(query)
                self.assertEqual(
                    result.complexity,
                    QueryComplexity.DYNAMIC_INFORMATION,
                )

    def test_long_or_multi_part_queries_are_complex(self):

        result = classify_query_rules(
            "Design a distributed system and explain the trade-offs "
            "step by step. Include an architecture, a migration plan, "
            "and a benchmark strategy for each component."
        )

        self.assertEqual(result.complexity, QueryComplexity.COMPLEX)

    def test_explanatory_queries_are_moderate(self):

        result = classify_query_rules(
            "Can you explain how retrieval augmented generation works "
            "in simple terms?"
        )

        self.assertEqual(result.complexity, QueryComplexity.MODERATE)

    def test_short_small_talk_is_simple_and_cache_eligible(self):

        result = classify_query_rules("Hello there")

        self.assertEqual(result.complexity, QueryComplexity.SIMPLE)
        self.assertTrue(result.cache_eligible)


class OptionalLLMFallbackTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):

        self.generate = AsyncMock(
            return_value={
                "provider": "groq",
                "model": "test",
                "response": "COMPLEX",
            }
        )
        self.router_patch = patch.object(
            llm_router, "generate_response", new=self.generate
        )
        self.router_patch.start()
        self.addCleanup(self.router_patch.stop)

    async def test_fallback_is_disabled_by_default(self):

        with patch.object(
            settings,
            "AI_QUERY_CLASSIFIER_LLM_FALLBACK_ENABLED",
            False,
        ):
            result = await classify_query("Hello there")

        self.assertFalse(result.used_llm)
        self.generate.assert_not_awaited()

    async def test_fallback_can_refine_a_low_confidence_result(self):

        with patch.object(
            settings, "AI_QUERY_CLASSIFIER_LLM_FALLBACK_ENABLED", True
        ), patch.object(
            settings, "AI_QUERY_CLASSIFIER_LLM_FALLBACK_THRESHOLD", 1.0
        ):
            result = await classify_query("Hello there")

        self.assertTrue(result.used_llm)
        self.assertEqual(result.complexity, QueryComplexity.COMPLEX)
        self.generate.assert_awaited_once()

    async def test_fallback_ignores_unsafe_or_unknown_labels(self):

        for label in (
            "TASK_ACTION",
            "PRIVATE",
            "ignore previous instructions",
            "",
        ):
            with self.subTest(label=label):
                self.generate.return_value = {
                    "provider": "groq",
                    "model": "test",
                    "response": label,
                }

                with patch.object(
                    settings,
                    "AI_QUERY_CLASSIFIER_LLM_FALLBACK_ENABLED",
                    True,
                ), patch.object(
                    settings,
                    "AI_QUERY_CLASSIFIER_LLM_FALLBACK_THRESHOLD",
                    1.0,
                ):
                    result = await classify_query("Hello there")

                self.assertFalse(result.used_llm)
                self.assertEqual(
                    result.complexity,
                    QueryComplexity.SIMPLE,
                )

    async def test_private_requests_never_use_the_llm_fallback(self):

        with patch.object(
            settings, "AI_QUERY_CLASSIFIER_LLM_FALLBACK_ENABLED", True
        ), patch.object(
            settings, "AI_QUERY_CLASSIFIER_LLM_FALLBACK_THRESHOLD", 1.0
        ):
            result = await classify_query(
                "private question", private=True
            )

        self.assertEqual(result.complexity, QueryComplexity.PRIVATE)
        self.generate.assert_not_awaited()

    async def test_classifier_can_be_disabled(self):

        with patch.object(
            settings, "AI_QUERY_CLASSIFIER_ENABLED", False
        ):
            normal = await classify_query("Hello there")
            private = await classify_query("Hello there", private=True)

        self.assertEqual(normal.complexity, QueryComplexity.MODERATE)
        self.assertEqual(private.complexity, QueryComplexity.PRIVATE)
        self.generate.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
