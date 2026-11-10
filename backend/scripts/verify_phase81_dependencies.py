"""Validate ASTA requirements in a fresh, temporary Python 3.12 venv.

This script never touches ``C:\\Asta\\venv``. It creates a throwaway venv under
the system temporary directory, installs the selected requirements file, runs
``pip check``, imports every direct dependency, and imports the FastAPI
application. The venv is removed afterwards.

Usage:
    cd C:\\Asta\\backend
    ..\\venv\\Scripts\\python.exe scripts\\verify_phase81_dependencies.py

Use ``--requirements requirements.txt`` to validate runtime dependencies only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
PRODUCTION_MODULES = [
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "dotenv",
    "sqlalchemy",
    "greenlet",
    "asyncpg",
    "alembic",
    "pgvector",
    "argon2",
    "groq",
    "ollama",
    "httpx",
    "sentence_transformers",
    "torch",
    "transformers",
    "numpy",
    "scipy",
    "sklearn",
    "tzdata",
]


def run(command: list[str], *, cwd: Path | None = None) -> None:
    rendered = " ".join(str(part) for part in command)
    print(f"\n>>> {rendered}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--requirements",
        default="requirements-dev.txt",
        choices=["requirements.txt", "requirements-dev.txt"],
    )
    args = parser.parse_args()

    if sys.version_info[:2] != (3, 12):
        print("This project targets Python 3.12.")
        return 2

    requirements_path = BACKEND_DIR / args.requirements

    if not requirements_path.is_file():
        print(f"Missing requirements file: {requirements_path}")
        return 2

    started = time.monotonic()

    with tempfile.TemporaryDirectory(
        prefix="asta-phase81-venv-",
    ) as temporary_directory:
        environment_dir = Path(temporary_directory) / "venv"
        print(f"Creating temporary virtual environment: {environment_dir}")

        venv.EnvBuilder(with_pip=True, clear=True).create(environment_dir)

        if sys.platform == "win32":
            python = environment_dir / "Scripts" / "python.exe"
        else:
            python = environment_dir / "bin" / "python"

        run([str(python), "--version"])
        run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--requirement",
                str(requirements_path),
            ],
            cwd=BACKEND_DIR,
        )
        run([str(python), "-m", "pip", "check"])

        modules = list(PRODUCTION_MODULES)

        if args.requirements == "requirements-dev.txt":
            modules.append("aiosqlite")

        module_imports = "\n".join(
            f"import {module}" for module in modules
        )

        check_script = (
            "from app.main import app\n"
            f"{module_imports}\n"
            "print('application import: OK')\n"
            "print('dependency imports: OK')\n"
        )

        run([str(python), "-c", check_script], cwd=BACKEND_DIR)

        versions_script = (
            "from importlib.metadata import version\n"
            "names = [\n"
            f"    {', '.join(repr(name) for name in modules)},\n"
            "]\n"
            "for name in names:\n"
            "    distribution = {\n"
            "        'dotenv': 'python-dotenv',\n"
            "        'argon2': 'argon2-cffi',\n"
            "        'sklearn': 'scikit-learn',\n"
            "    }.get(name, name)\n"
            "    try:\n"
            "        print(f'{distribution}=={version(distribution)}')\n"
            "    except Exception as error:\n"
            "        print(f'{distribution}: {error}')\n"
        )

        run([str(python), "-c", versions_script], cwd=BACKEND_DIR)

    elapsed = time.monotonic() - started
    print(f"\nFresh-environment validation passed in {elapsed:.1f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
