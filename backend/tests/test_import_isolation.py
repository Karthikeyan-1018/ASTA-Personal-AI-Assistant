"""Regression tests for standalone module imports.

Phase 5.3 exposed an import cycle: importing ``app.services.memory_service``
as the first application module failed because the ``app.memory`` package
eagerly imported modules that depend back on the service. These tests run each
import in a fresh interpreter so module caching cannot hide a regression.
"""

import subprocess
import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]

STANDALONE_MODULES = (
    "app.services.memory_service",
    "app.services.memory_embedding_service",
    "app.services.chat_service",
    "app.memory.memory_retriever",
    "app.memory.context_builder",
    "app.memory.memory_extractor",
    "app.memory.embedding_service",
    "app.memory.semantic_support",
    "app.api.memory",
    "app.memory",
)


class StandaloneImportTests(unittest.TestCase):

    def _run(self, code: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=300
        )

    def test_modules_import_standalone(self):
        for module_name in STANDALONE_MODULES:
            with self.subTest(module=module_name):
                result = self._run(f"import {module_name}")

                self.assertEqual(
                    result.returncode,
                    0,
                    msg=(
                        f"Standalone import failed for {module_name}.\n"
                        f"stdout:\n{result.stdout}\n"
                        f"stderr:\n{result.stderr}"
                    )
                )

    def test_memory_service_does_not_initialize_app_main(self):
        result = self._run(
            "import sys\n"
            "import app.services.memory_service\n"
            "print('app.main' in sys.modules)"
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=result.stderr
        )
        self.assertEqual(result.stdout.strip(), "False")

    def test_secret_patterns_are_owned_by_core(self):
        result = self._run(
            "from app.core.secret_patterns import SECRET_PATTERNS, contains_secret\n"
            "assert contains_secret('api_key = abc123456')\n"
            "assert not contains_secret('I am a computer science student')\n"
            "from app.services.memory_service import SECRET_PATTERNS as reexported\n"
            "assert reexported is SECRET_PATTERNS\n"
            "print('ok')"
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=result.stderr
        )
        self.assertEqual(result.stdout.strip(), "ok")

    def test_lazy_package_exports_remain_available(self):
        result = self._run(
            "from app.memory import (\n"
            "    build_chat_prompt, memory_retriever, embedding_service,\n"
            "    RetrievedMemory, RetrievalMode,\n"
            ")\n"
            "print('ok')"
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=result.stderr
        )
        self.assertEqual(result.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
