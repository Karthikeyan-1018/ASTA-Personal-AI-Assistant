import unittest
from unittest.mock import patch

from app.ai.tokenization import (
    TokenBudget,
    estimate_tokens,
    parse_model_context_windows,
    provider_default_context_window,
    resolve_context_window,
    resolve_token_budget,
    safe_estimate_tokens,
)
from app.core.config import settings


class TokenEstimationTests(unittest.TestCase):

    def test_empty_text_has_no_tokens(self):

        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens(None), 0)

    def test_non_empty_text_never_estimates_zero(self):

        for value in ("a", "hi", "hello world", "1234567890"):
            with self.subTest(value=value):
                self.assertGreaterEqual(estimate_tokens(value), 1)

    def test_estimate_grows_with_text(self):

        short = estimate_tokens("hello")
        long = estimate_tokens("hello " * 100)

        self.assertGreater(long, short)

    def test_cjk_text_is_not_underestimated(self):

        with patch.object(settings, "AI_CHARS_PER_TOKEN", 4.0):
            latin = estimate_tokens("a" * 80)
            cjk = estimate_tokens("漢" * 80)

        self.assertGreaterEqual(cjk, latin)

    def test_safe_estimate_returns_none_on_failure(self):

        with patch(
            "app.ai.tokenization.estimate_tokens",
            side_effect=RuntimeError("boom"),
        ):
            self.assertIsNone(safe_estimate_tokens("hello"))


class ContextWindowTests(unittest.TestCase):

    def test_parse_model_windows_ignores_malformed_entries(self):

        parsed = parse_model_context_windows(
            "model-a:4096,broken,model-b:0,model-c:-1,model-d:8192"
        )

        self.assertEqual(parsed, {"model-a": 4096, "model-d": 8192})

    def test_model_override_wins_over_provider_default(self):

        with patch.object(
            settings,
            "AI_MODEL_CONTEXT_WINDOWS",
            "custom-model:2048",
        ), patch.object(
            settings,
            "AI_GROQ_CONTEXT_WINDOW_TOKENS",
            131072,
        ):
            self.assertEqual(
                resolve_context_window(model="custom-model", provider="groq"),
                2048,
            )

    def test_provider_defaults_are_used_without_override(self):

        with patch.object(
            settings, "AI_GROQ_CONTEXT_WINDOW_TOKENS", 100000
        ), patch.object(
            settings, "AI_OLLAMA_CONTEXT_WINDOW_TOKENS", 30000
        ), patch.object(
            settings, "AI_DEFAULT_CONTEXT_WINDOW_TOKENS", 7000
        ):
            self.assertEqual(
                provider_default_context_window("groq"), 100000
            )
            self.assertEqual(
                provider_default_context_window("ollama"), 30000
            )
            self.assertEqual(
                provider_default_context_window("other"), 7000
            )

    def test_token_budget_reserves_response_and_safety_tokens(self):

        with patch.object(
            settings, "AI_RESPONSE_RESERVE_TOKENS", 1024
        ), patch.object(
            settings, "AI_CONTEXT_SAFETY_TOKENS", 256
        ):
            budget = resolve_token_budget(context_window_tokens=8192)

        self.assertIsInstance(budget, TokenBudget)
        self.assertEqual(budget.context_window, 8192)
        self.assertEqual(budget.available, 8192 - 1024 - 256)
        self.assertEqual(budget.method, "heuristic")

    def test_token_budget_never_drops_below_safe_minimum(self):

        budget = resolve_token_budget(
            context_window_tokens=100,
            response_reserve_tokens=1000,
        )

        self.assertGreaterEqual(budget.available, 128)


if __name__ == "__main__":
    unittest.main()
