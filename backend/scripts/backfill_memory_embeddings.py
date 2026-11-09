"""Backfill embeddings for approved ASTA memories.

This command is intentionally manual. It is never run during application
startup and it never changes memory approval statuses. Failed records remain
without an embedding and are retried by the next invocation.

Usage (from ``backend``):

    ..\\venv\\Scripts\\python.exe -m scripts.backfill_memory_embeddings
    ..\\venv\\Scripts\\python.exe -m scripts.backfill_memory_embeddings --batch-size 20
    ..\\venv\\Scripts\\python.exe -m scripts.backfill_memory_embeddings --force
"""

import argparse
import asyncio
import logging
import sys

from app.database.connection import AsyncSessionLocal, engine
from app.memory.semantic_support import is_semantic_storage_available
from app.services.memory_embedding_service import memory_embedding_service


logger = logging.getLogger(__name__)


async def run_backfill(args: argparse.Namespace) -> int:

    try:
        async with AsyncSessionLocal() as session:

            if not await is_semantic_storage_available(session):
                print(
                    "pgvector is not enabled in the configured database. "
                    "Install the vector extension and apply the Alembic "
                    "migration before running this backfill.",
                    file=sys.stderr
                )
                return 2

            result = await memory_embedding_service.backfill_embeddings(
                session=session,
                batch_size=args.batch_size,
                limit=args.limit,
                force=args.force
            )

    finally:
        await engine.dispose()

    print(
        "Embedding backfill complete: "
        f"indexed={result.indexed} "
        f"skipped={result.skipped} "
        f"failed={result.failed} "
        f"batches={result.batches}"
    )

    return 0 if result.failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        description=(
            "Generate embeddings for approved memories that do not have "
            "a current embedding."
        )
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="Number of memories fetched per batch (default: 25)."
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of memories to process (default: no limit)."
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Regenerate embeddings even when a current embedding already "
            "exists. Use sparingly."
        )
    )

    return parser


def main() -> int:

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s"
    )

    args = build_parser().parse_args()

    try:
        return asyncio.run(run_backfill(args))
    except KeyboardInterrupt:
        print("Backfill interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
