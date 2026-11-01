from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

from embedding_service import VECTOR_DIMENSION, embed_documents


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / "backend" / ".env", override=False)

SOURCE_COLLECTION = "rbi_regulations"
TARGET_COLLECTION = "rbi_regulations_bge"


def text_for_payload(payload: dict) -> str:
    for key in ("text", "content", "page_content", "chunk_text", "document"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def connect() -> QdrantClient:
    url = os.getenv("QDRANT_URL")
    api_key = os.getenv("QDRANT_API_KEY")
    if not url or not api_key:
        raise RuntimeError("QDRANT_URL and QDRANT_API_KEY are required.")
    return QdrantClient(url=url, api_key=api_key, timeout=120)


def main(batch_size: int = 64) -> None:
    client = connect()
    collections = {item.name for item in client.get_collections().collections}
    if SOURCE_COLLECTION not in collections:
        raise RuntimeError(f"Source collection '{SOURCE_COLLECTION}' does not exist.")
    if TARGET_COLLECTION not in collections:
        client.create_collection(
            collection_name=TARGET_COLLECTION,
            vectors_config=models.VectorParams(
                size=VECTOR_DIMENSION,
                distance=models.Distance.COSINE,
            ),
        )
        print(f"Created collection: {TARGET_COLLECTION}")

    target_info = client.get_collection(TARGET_COLLECTION)
    if target_info.config.params.vectors.size != VECTOR_DIMENSION:
        raise RuntimeError(
            f"Target collection has size {target_info.config.params.vectors.size}; "
            f"expected {VECTOR_DIMENSION}."
        )

    offset = None
    migrated = 0
    while True:
        points, next_offset = client.scroll(
            collection_name=SOURCE_COLLECTION,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            break

        texts = [text_for_payload(point.payload or {}) for point in points]
        valid = [(point, text) for point, text in zip(points, texts) if text]
        if valid:
            vectors = embed_documents([text for _, text in valid])
            client.upsert(
                collection_name=TARGET_COLLECTION,
                points=[
                    models.PointStruct(id=point.id, vector=vector, payload=point.payload)
                    for vector, (point, _) in zip(vectors, valid)
                ],
                wait=True,
            )
            migrated += len(valid)
        print(f"Migrated {migrated} points.")
        if next_offset is None:
            break
        offset = next_offset

    info = client.get_collection(TARGET_COLLECTION)
    print(
        f"Migration complete: {migrated} points copied to "
        f"{TARGET_COLLECTION} ({info.points_count} total points)."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    main(args.batch_size)
