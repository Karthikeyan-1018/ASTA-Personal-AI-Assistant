"""Phase 8.1 dependency-management consistency tests.

These tests verify that the requirements files are valid, pinned, free of
duplicates, and consistent with the interpreter running the suite. The actual
fresh-venv installation is performed by
``scripts/verify_phase81_dependencies.py`` because it downloads the full ML
stack and is intentionally not part of the fast unit-test run.
"""

import importlib
import re
import unittest
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
REQUIREMENTS = BACKEND_DIR / "requirements.txt"
REQUIREMENTS_DEV = BACKEND_DIR / "requirements-dev.txt"

PIN_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)$")

REQUIRED_PRODUCTION = {
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "python-dotenv",
    "sqlalchemy",
    "greenlet",
    "asyncpg",
    "alembic",
    "pgvector",
    "argon2-cffi",
    "groq",
    "ollama",
    "httpx",
    "sentence-transformers",
    "torch",
    "transformers",
    "numpy",
    "scipy",
    "scikit-learn",
    "tzdata",
}

REQUIRED_TEST = {
    "aiosqlite",
}

IMPORT_MODULES = {
    "fastapi": "fastapi",
    "starlette": "starlette",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
    "python-dotenv": "dotenv",
    "sqlalchemy": "sqlalchemy",
    "greenlet": "greenlet",
    "asyncpg": "asyncpg",
    "alembic": "alembic",
    "pgvector": "pgvector",
    "argon2-cffi": "argon2",
    "groq": "groq",
    "ollama": "ollama",
    "httpx": "httpx",
    "sentence-transformers": "sentence_transformers",
    "torch": "torch",
    "transformers": "transformers",
    "numpy": "numpy",
    "scipy": "scipy",
    "scikit-learn": "sklearn",
    "tzdata": "tzdata",
    "aiosqlite": "aiosqlite",
}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(path: Path) -> tuple[dict[str, str], list[str]]:
    pins: dict[str, str] = {}
    includes: list[str] = []

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("-r "):
            includes.append(line[3:].strip())
            continue

        match = PIN_PATTERN.fullmatch(line)

        if match is None:
            raise AssertionError(
                f"{path.name}:{line_number} is not an exact pin: {line!r}"
            )

        name, pinned_version = match.groups()
        canonical_name = canonical(name)

        if canonical_name in pins:
            raise AssertionError(
                f"{path.name}:{line_number} duplicates {canonical_name}"
            )

        pins[canonical_name] = pinned_version

    return pins, includes


class DependencyFilesTestCase(unittest.TestCase):

    def test_requirements_files_exist(self):

        self.assertTrue(REQUIREMENTS.is_file())
        self.assertTrue(REQUIREMENTS_DEV.is_file())

    def test_every_requirement_is_exactly_pinned(self):

        production, production_includes = parse_requirements(REQUIREMENTS)
        development, development_includes = parse_requirements(
            REQUIREMENTS_DEV
        )

        self.assertEqual(production_includes, [])
        self.assertEqual(development_includes, ["requirements.txt"])
        self.assertTrue(production)
        self.assertIn("aiosqlite", development)

    def test_required_runtime_and_test_dependencies_are_present(self):

        production, _ = parse_requirements(REQUIREMENTS)
        development, _ = parse_requirements(REQUIREMENTS_DEV)

        self.assertEqual(
            REQUIRED_PRODUCTION - set(production),
            set(),
            "missing runtime dependencies",
        )
        self.assertEqual(
            REQUIRED_TEST - set(development),
            set(),
            "missing test dependencies",
        )

    def test_test_only_dependencies_are_not_in_runtime_file(self):

        production, _ = parse_requirements(REQUIREMENTS)
        development, _ = parse_requirements(REQUIREMENTS_DEV)

        self.assertNotIn("aiosqlite", production)
        self.assertNotIn("aiosqlite", set(production) & set(development))

        # With the include in place, only the declared test additions should
        # remain outside the runtime file.
        extras = set(development) - set(production)

        self.assertEqual(
            extras,
            REQUIRED_TEST,
            "unexpected packages in requirements-dev.txt",
        )

    def test_no_duplicate_or_conflicting_pins(self):

        production, _ = parse_requirements(REQUIREMENTS)
        development, _ = parse_requirements(REQUIREMENTS_DEV)

        overlap = set(production) & set(development)

        for name in overlap:
            self.assertEqual(
                production[name],
                development[name],
                f"{name} is pinned differently in the two files",
            )

    def test_pins_match_the_tested_environment(self):

        production, _ = parse_requirements(REQUIREMENTS)
        development, _ = parse_requirements(REQUIREMENTS_DEV)

        for name, expected_version in {
            **production,
            **development,
        }.items():
            try:
                installed = version(name)

            except PackageNotFoundError as error:
                self.fail(f"{name} is pinned but not installed: {error}")

            self.assertEqual(
                installed,
                expected_version,
                f"{name} is {installed}, requirements pin {expected_version}",
            )

    def test_declared_dependencies_import_successfully(self):

        for distribution, module_name in IMPORT_MODULES.items():
            with self.subTest(distribution=distribution):
                module = importlib.import_module(module_name)
                self.assertIsNotNone(module)

    def test_application_imports_successfully(self):

        from app.main import app

        paths = set(app.openapi()["paths"])

        self.assertIn("/api/auth/login", paths)
        self.assertIn("/api/auth/setup", paths)
        self.assertIn("/api/chat/", paths)

    def test_requirements_do_not_contain_secrets(self):

        text = (
            REQUIREMENTS.read_text(encoding="utf-8")
            + REQUIREMENTS_DEV.read_text(encoding="utf-8")
        )

        for marker in (
            "DB_PASSWORD=",
            "GROQ_API_KEY=",
            "AUTH_OWNER_SETUP_TOKEN=",
            "-----BEGIN",
        ):
            self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
