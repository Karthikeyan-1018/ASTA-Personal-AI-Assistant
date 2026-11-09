"""Long-term memory extraction, retrieval, and chat context for ASTA.

Submodules are imported lazily rather than eagerly. Several memory modules
depend on ``app.services``, while services also import memory submodules, so
eager imports here made ``app.services.memory_service`` impossible to import
as the first application module. Lazy re-exports keep the public
``from app.memory import ...`` API without creating that cycle.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.memory.context_builder import (
        build_chat_prompt,
        build_memory_context,
    )
    from app.memory.embedding_service import EmbeddingService, embedding_service
    from app.memory.memory_extractor import MemoryExtractor, memory_extractor
    from app.memory.memory_retriever import (
        MemoryRetriever,
        RetrievalMode,
        RetrievedMemory,
        cosine_similarity,
        memory_retriever,
        rank_memories,
    )


__all__ = [
    "MemoryExtractor",
    "memory_extractor",
    "MemoryRetriever",
    "RetrievalMode",
    "RetrievedMemory",
    "rank_memories",
    "cosine_similarity",
    "memory_retriever",
    "build_chat_prompt",
    "build_memory_context",
    "EmbeddingService",
    "embedding_service",
]


# Export name -> defining module. Resolved on first access.
_EXPORTS = {
    "MemoryExtractor": "app.memory.memory_extractor",
    "memory_extractor": "app.memory.memory_extractor",
    "MemoryRetriever": "app.memory.memory_retriever",
    "RetrievalMode": "app.memory.memory_retriever",
    "RetrievedMemory": "app.memory.memory_retriever",
    "rank_memories": "app.memory.memory_retriever",
    "cosine_similarity": "app.memory.memory_retriever",
    "memory_retriever": "app.memory.memory_retriever",
    "build_chat_prompt": "app.memory.context_builder",
    "build_memory_context": "app.memory.context_builder",
    "EmbeddingService": "app.memory.embedding_service",
    "embedding_service": "app.memory.embedding_service",
}


def __getattr__(name: str) -> Any:
    """Resolve a public memory export on first access."""

    module_name = _EXPORTS.get(name)

    if module_name is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )

    value = getattr(import_module(module_name), name)

    # Cache the resolved value so subsequent access is a normal lookup.
    globals()[name] = value

    return value
