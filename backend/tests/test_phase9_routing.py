import unittest
from unittest.mock import patch

from app.ai.query_classifier import QueryComplexity
from app.ai.routing_policy import (
    COST_AWARE,
    GROQ_FIRST,
    LOCAL_FIRST,
    OLLAMA,
    provider_profile,
    resolve_model,
    select_route,
)
from app.core.config import settings


class RoutingPolicyTests(unittest.TestCase):

    def test_groq_first_is_the_default(self):

        with patch.object(settings, "AI_ROUTING_POLICY", GROQ_FIRST):
            route = select_route(private=False, complexity="moderate")

        self.assertEqual(route.primary, "groq")
        self.assertEqual(route.fallback, "ollama")
        self.assertEqual(route.provider_order, ("groq", "ollama"))
        self.assertEqual(route.policy, GROQ_FIRST)

    def test_local_first_prefers_ollama(self):

        with patch.object(settings, "AI_ROUTING_POLICY", LOCAL_FIRST):
            route = select_route(private=False, complexity="complex")

        self.assertEqual(route.primary, "ollama")
        self.assertEqual(route.fallback, "groq")
        self.assertEqual(route.provider_order, ("ollama", "groq"))

    def test_cost_aware_uses_configured_provider_per_complexity(self):

        with patch.object(
            settings, "AI_ROUTING_POLICY", COST_AWARE
        ), patch.object(
            settings, "AI_ROUTING_SIMPLE_PROVIDER", "ollama"
        ), patch.object(
            settings, "AI_ROUTING_MODERATE_PROVIDER", "groq"
        ), patch.object(
            settings, "AI_ROUTING_COMPLEX_PROVIDER", "groq"
        ):
            simple = select_route(
                private=False, complexity=QueryComplexity.SIMPLE
            )
            moderate = select_route(
                private=False, complexity=QueryComplexity.MODERATE
            )

        self.assertEqual(simple.primary, "ollama")
        self.assertEqual(simple.fallback, "groq")
        self.assertEqual(moderate.primary, "groq")
        self.assertEqual(moderate.fallback, "ollama")

    def test_private_requests_are_ollama_only_for_every_policy(self):

        for policy in (GROQ_FIRST, LOCAL_FIRST, COST_AWARE):
            with self.subTest(policy=policy), patch.object(
                settings, "AI_ROUTING_POLICY", policy
            ):
                route = select_route(
                    private=True,
                    complexity=QueryComplexity.PRIVATE,
                )

                self.assertEqual(route.primary, OLLAMA)
                self.assertIsNone(route.fallback)
                self.assertEqual(route.provider_order, (OLLAMA,))
                self.assertEqual(route.policy, "private")
                self.assertTrue(route.private)

    def test_unknown_policy_falls_back_to_groq_first(self):

        with patch.object(settings, "AI_ROUTING_POLICY", "not-a-policy"):
            route = select_route(private=False, complexity="moderate")

        self.assertEqual(route.policy, GROQ_FIRST)
        self.assertEqual(route.primary, "groq")

    def test_model_override_uses_complexity_provider_pair(self):

        with patch.object(
            settings,
            "AI_ROUTING_MODEL_OVERRIDES",
            "simple:ollama:tiny-model,default:groq:fallback-model",
        ):
            self.assertEqual(
                resolve_model("ollama", QueryComplexity.SIMPLE),
                "tiny-model",
            )
            self.assertEqual(
                resolve_model("groq", QueryComplexity.SIMPLE),
                "fallback-model",
            )

    def test_unknown_provider_is_rejected(self):

        with self.assertRaises(ValueError):
            provider_profile("unknown-provider")


if __name__ == "__main__":
    unittest.main()
