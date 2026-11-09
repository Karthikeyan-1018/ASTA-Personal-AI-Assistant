"""Lazy, reusable embedding generation for ASTA long-term memories.

The preferred model is ``intfloat/multilingual-e5-base`` (768 dimensions).
E5 models are trained with explicit ``query:`` and ``passage:`` prefixes, so
the service adds them before encoding and always normalises the result.
"""

import asyncio
import logging
import math
import threading
from collections.abc import Sequence

from app.core.config import settings
from app.exceptions.memory_exceptions import (
    EmbeddingModelError,
    EmbeddingServiceError,
    EmbeddingValidationError,
)


logger = logging.getLogger(__name__)


EMBEDDING_MODEL_NAME = settings.EMBEDDING_MODEL_NAME
EMBEDDING_DIMENSION = settings.EMBEDDING_DIMENSION
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "


def _normalise_vector(values: Sequence[float]) -> list[float]:
    """Return a finite, L2-normalised copy of an embedding vector."""

    vector = [float(value) for value in values]

    if not vector:
        raise EmbeddingValidationError("Embedding vector cannot be empty.")

    if any(not math.isfinite(value) for value in vector):
        raise EmbeddingValidationError(
            "Embedding vector contains a non-finite value."
        )

    norm = math.sqrt(sum(value * value for value in vector))

    if norm == 0.0:
        raise EmbeddingValidationError(
            "Embedding vector cannot have zero magnitude."
        )

    return [value / norm for value in vector]


class EmbeddingService:
    """Generate query and passage embeddings with a lazily loaded model."""

    def __init__(
        self,
        model_name: str = EMBEDDING_MODEL_NAME,
        dimension: int = EMBEDDING_DIMENSION,
        device: str = settings.EMBEDDING_DEVICE,
        batch_size: int = settings.EMBEDDING_BATCH_SIZE
    ) -> None:

        self._model_name = model_name
        self._dimension = dimension
        self._device = device
        self._batch_size = max(1, batch_size)
        self._model = None
        self._model_lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def reset(self) -> None:
        """Forget the loaded model. Intended for tests and reconfiguration."""

        with self._model_lock:
            self._model = None

    def _ensure_model(self):
        """Load and validate the model once, even under concurrency."""

        if self._model is not None:
            return self._model

        with self._model_lock:
            if self._model is not None:
                return self._model

            try:
                from sentence_transformers import SentenceTransformer

                model = SentenceTransformer(
                    self._model_name,
                    device=self._device
                )

                model_dimension = model.get_embedding_dimension()

                if (
                    model_dimension is not None
                    and model_dimension != self._dimension
                ):
                    raise EmbeddingModelError(
                        "Embedding model dimension does not match the "
                        "configured embedding dimension."
                    )

            except EmbeddingModelError:
                raise

            except Exception as error:
                logger.exception("Failed to load the embedding model.")
                raise EmbeddingModelError(
                    "The embedding model could not be loaded."
                ) from error

            self._model = model

            return self._model

    def _encode_sync(self, texts: Sequence[str]) -> list[list[float]]:

        model = self._ensure_model()

        try:
            encoded = model.encode(
                list(texts),
                batch_size=self._batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True
            )
        except Exception as error:
            logger.exception("Embedding generation failed.")
            raise EmbeddingServiceError(
                "Embedding generation failed."
            ) from error

        if hasattr(encoded, "tolist"):
            encoded = encoded.tolist()

        vectors: list[list[float]] = []

        for raw_vector in encoded:
            vector = _normalise_vector(raw_vector)

            if len(vector) != self._dimension:
                raise EmbeddingServiceError(
                    "Embedding model returned an unexpected dimension."
                )

            vectors.append(vector)

        if len(vectors) != len(texts):
            raise EmbeddingServiceError(
                "Embedding model returned an unexpected number of vectors."
            )

        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query using the E5 ``query:`` prefix."""

        cleaned = (text or "").strip()

        if not cleaned:
            raise EmbeddingValidationError("Query text cannot be empty.")

        vectors = await asyncio.to_thread(
            self._encode_sync,
            [QUERY_PREFIX + cleaned]
        )

        return vectors[0]

    async def embed_passage(self, text: str) -> list[float]:
        """Embed a memory document using the E5 ``passage:`` prefix."""

        vectors = await self.embed_passages([text])

        return vectors[0]

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed memory documents using the E5 ``passage:`` prefix."""

        cleaned = [(text or "").strip() for text in texts]

        if not cleaned:
            return []

        if any(not text for text in cleaned):
            raise EmbeddingValidationError("Passage text cannot be empty.")

        prefixed = [PASSAGE_PREFIX + text for text in cleaned]

        return await asyncio.to_thread(self._encode_sync, prefixed)


# Create a reusable service instance. The model is loaded only on first use.
embedding_service = EmbeddingService()
