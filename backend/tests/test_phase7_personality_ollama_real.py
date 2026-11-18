r"""Opt-in real Ollama check for the shared Phase 7 personality prompt.

Run only when a local Ollama instance is available:

    $env:ASTA_REAL_OLLAMA_TESTS="1"
    .\venv\Scripts\python.exe -m unittest \
        tests.test_phase7_personality_ollama_real -v

No secrets are read or printed. The test forces the local model directly and
uses marker-based assertions because model wording is nondeterministic.
"""

from __future__ import annotations

import os
import unittest

from app.llm.ollama_provider import ollama_provider
from app.memory.context_builder import build_chat_prompt
from app.personality import build_persona_instructions, load_personality_config


@unittest.skipUnless(
    os.getenv("ASTA_REAL_OLLAMA_TESTS") == "1",
    "Set ASTA_REAL_OLLAMA_TESTS=1 to run the live local Ollama probe.",
)
class RealOllamaPersonalityTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the real prompt path against the configured local provider."""

    async def test_shared_persona_reaches_real_ollama(self):
        config = load_personality_config()
        user_message = "In one sentence, who are you?"
        persona = build_persona_instructions(config, user_message)
        prompt = build_chat_prompt(
            user_message,
            [],
            persona_instructions=persona,
        )

        self.assertIn("You are ASTA", prompt)
        self.assertIn(config.assistant_name, prompt)

        response = await ollama_provider.generate_response(
            prompt,
            model="qwen3:8b",
        )

        self.assertIsInstance(response, str)
        self.assertTrue(response.strip())
        self.assertIn("ASTA", response.upper())
