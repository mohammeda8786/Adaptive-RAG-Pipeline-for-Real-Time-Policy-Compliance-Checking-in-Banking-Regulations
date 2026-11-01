from __future__ import annotations

from functools import lru_cache
from typing import Iterable

import numpy as np
from fastembed import TextEmbedding


MODEL_NAME = "BAAI/bge-small-en-v1.5"
VECTOR_DIMENSION = 384


def _validate_texts(texts: Iterable[str]) -> list[str]:
    validated = [text.strip() for text in texts if isinstance(text, str) and text.strip()]
    if not validated:
        raise ValueError("At least one non-empty text is required.")
    return validated


@lru_cache(maxsize=1)
def get_embedding_model() -> TextEmbedding:
    return TextEmbedding(model_name=MODEL_NAME)


def embed_documents(texts: Iterable[str]) -> list[list[float]]:
    documents = _validate_texts(texts)
    vectors = [np.asarray(vector, dtype=np.float32) for vector in get_embedding_model().embed(documents)]
    return _validate_vectors(vectors)


def embed_query(text: str) -> list[float]:
    query = _validate_texts([text])[0]
    model = get_embedding_model()
    if hasattr(model, "query_embed"):
        vectors = list(model.query_embed([query]))
    else:
        vectors = list(model.embed([query]))
    return _validate_vectors([np.asarray(vector, dtype=np.float32) for vector in vectors])[0]


def embed_text(text: str) -> list[float]:
    return embed_documents([text])[0]


def _validate_vectors(vectors: list[np.ndarray]) -> list[list[float]]:
    normalized: list[list[float]] = []
    for vector in vectors:
        if vector.shape != (VECTOR_DIMENSION,):
            raise RuntimeError(
                f"Embedding dimension mismatch: expected {VECTOR_DIMENSION}, got {vector.shape}"
            )
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            raise RuntimeError("Embedding model produced a zero vector.")
        normalized.append((vector / norm).tolist())
    return normalized
