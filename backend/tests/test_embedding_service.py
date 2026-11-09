import math
import unittest
from unittest.mock import patch

from app.core.config import settings
from app.exceptions.memory_exceptions import (
    EmbeddingModelError,
    EmbeddingServiceError,
    EmbeddingValidationError,
)
from app.memory.embedding_service import (
    EMBEDDING_DIMENSION,
    EmbeddingService,
)
from app.models.memory import Memory


class FakeSentenceTransformer:
    """Small deterministic stand-in for sentence-transformers models."""

    def __init__(self, dimension=4, vectors=None):
        self.dimension = dimension
        self.vectors = vectors
        self.calls = []

    def get_embedding_dimension(self):
        return self.dimension

    def encode(self, texts, **kwargs):
        self.calls.append(list(texts))

        if self.vectors is not None:
            return self.vectors

        return [
            [1.0] + [0.0] * (self.dimension - 1)
            for _ in texts
        ]


class DeprecatedOnlyDimensionTransformer(FakeSentenceTransformer):
    """Fails if the service calls the removed deprecated dimension API."""

    def get_sentence_embedding_dimension(self):
        raise AssertionError(
            "The deprecated get_sentence_embedding_dimension() API was called."
        )


class EmbeddingServiceTests(unittest.IsolatedAsyncioTestCase):

    def _service(self, dimension=4):
        service = EmbeddingService(
            model_name="test-embedding-model",
            dimension=dimension,
            device="cpu",
            batch_size=2
        )
        service._model = FakeSentenceTransformer(dimension=dimension)
        return service

    def test_configured_embedding_dimension_is_768(self):
        self.assertEqual(settings.EMBEDDING_DIMENSION, 768)
        self.assertEqual(EMBEDDING_DIMENSION, 768)
        self.assertEqual(Memory.__table__.c.embedding.type.dim, 768)

    async def test_768_dimensional_vector_is_accepted(self):
        service = EmbeddingService(
            model_name="test-embedding-model",
            dimension=768
        )
        service._model = FakeSentenceTransformer(dimension=768)

        vector = await service.embed_query("768 dimension check")

        self.assertEqual(len(vector), 768)
        self.assertAlmostEqual(
            math.sqrt(sum(value * value for value in vector)),
            1.0
        )

    async def test_embed_query_uses_query_prefix(self):
        service = self._service()

        await service.embed_query("  career goal  ")

        self.assertEqual(
            service._model.calls,
            [["query: career goal"]]
        )

    async def test_embed_passages_use_passage_prefix(self):
        service = self._service()

        await service.embed_passages(["career goal", "education"])

        self.assertEqual(
            service._model.calls,
            [["passage: career goal", "passage: education"]]
        )

    async def test_embedding_is_l2_normalised(self):
        service = EmbeddingService(
            model_name="test-embedding-model",
            dimension=2
        )
        service._model = FakeSentenceTransformer(
            dimension=2,
            vectors=[[3.0, 4.0]]
        )

        vector = await service.embed_query("normalise me")

        self.assertAlmostEqual(vector[0], 0.6)
        self.assertAlmostEqual(vector[1], 0.8)
        self.assertAlmostEqual(
            math.sqrt(sum(value * value for value in vector)),
            1.0
        )

    async def test_empty_query_is_rejected(self):
        service = self._service()

        for query in ("", "   ", "\n\t"):
            with self.subTest(query=query):
                with self.assertRaises(EmbeddingValidationError):
                    await service.embed_query(query)

    async def test_empty_passage_is_rejected(self):
        service = self._service()

        with self.assertRaises(EmbeddingValidationError):
            await service.embed_passage("   ")

        with self.assertRaises(EmbeddingValidationError):
            await service.embed_passages(["valid", "   "])

    async def test_empty_passage_batch_returns_empty_list(self):
        service = self._service()

        self.assertEqual(await service.embed_passages([]), [])
        self.assertEqual(service._model.calls, [])

    async def test_wrong_embedding_dimension_is_rejected(self):
        service = EmbeddingService(
            model_name="test-embedding-model",
            dimension=4
        )
        service._model = FakeSentenceTransformer(
            dimension=3,
            vectors=[[1.0, 0.0, 0.0]]
        )

        with self.assertRaises(EmbeddingServiceError):
            await service.embed_query("dimension check")

    async def test_non_finite_embedding_is_rejected(self):
        service = EmbeddingService(
            model_name="test-embedding-model",
            dimension=2
        )
        service._model = FakeSentenceTransformer(
            dimension=2,
            vectors=[[float("nan"), 1.0]]
        )

        with self.assertRaises(EmbeddingValidationError):
            await service.embed_query("bad vector")

    async def test_zero_magnitude_embedding_is_rejected(self):
        service = EmbeddingService(
            model_name="test-embedding-model",
            dimension=2
        )
        service._model = FakeSentenceTransformer(
            dimension=2,
            vectors=[[0.0, 0.0]]
        )

        with self.assertRaises(EmbeddingValidationError):
            await service.embed_query("zero vector")

    async def test_model_is_loaded_only_once(self):
        service = EmbeddingService(
            model_name="test-embedding-model",
            dimension=4
        )

        fake_model = FakeSentenceTransformer(dimension=4)

        with patch(
            "sentence_transformers.SentenceTransformer",
            return_value=fake_model
        ) as constructor:
            await service.embed_query("first")
            await service.embed_query("second")

        constructor.assert_called_once()
        self.assertEqual(len(fake_model.calls), 2)

    async def test_model_load_failure_raises_model_error(self):
        service = EmbeddingService(
            model_name="missing-model",
            dimension=4
        )

        with patch(
            "sentence_transformers.SentenceTransformer",
            side_effect=RuntimeError("cannot load")
        ):
            with self.assertRaises(EmbeddingModelError):
                await service.embed_query("hello")

    async def test_model_dimension_mismatch_raises_model_error(self):
        service = EmbeddingService(
            model_name="wrong-dimension-model",
            dimension=4
        )

        with patch(
            "sentence_transformers.SentenceTransformer",
            return_value=FakeSentenceTransformer(dimension=3)
        ):
            with self.assertRaises(EmbeddingModelError):
                await service.embed_query("hello")

    async def test_uses_supported_embedding_dimension_api(self):
        """The deprecated dimension accessor must not be called."""

        service = EmbeddingService(
            model_name="modern-dimension-model",
            dimension=4
        )

        with patch(
            "sentence_transformers.SentenceTransformer",
            return_value=DeprecatedOnlyDimensionTransformer(dimension=4)
        ):
            vector = await service.embed_query("modern api")

        self.assertEqual(len(vector), 4)


if __name__ == "__main__":
    unittest.main()
