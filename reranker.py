
import os
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from fastembed.rerank.cross_encoder import TextCrossEncoder
from embedding_service import embed_query


# --------------------------------------------------
# Configuration
# --------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / "backend" / ".env")

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "rbi_regulations_bge")
if COLLECTION_NAME == "rbi_regulations":
    COLLECTION_NAME = "rbi_regulations_bge"

# Keep the embedding model used by the existing RAG project.
EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"

# The standalone cross-encoder to evaluate.
RERANKER_MODEL_NAME = "BAAI/bge-reranker-v2-m3"

TOP_K = 5

# Optional: set this to your exact Qdrant payload text key.
# Example: TEXT_PAYLOAD_KEY= text
TEXT_PAYLOAD_KEY = os.getenv("TEXT_PAYLOAD_KEY", "").strip()

# Common passage text keys, tried in this order.
TEXT_KEYS = [
    "text",
    "content",
    "page_content",
    "chunk_text",
    "document",
]


def get_passage_text(payload):
    """Find the passage text in a Qdrant point's payload."""
    if not payload:
        return ""

    if TEXT_PAYLOAD_KEY:
        value = payload.get(TEXT_PAYLOAD_KEY)
        if value is not None:
            return str(value)

        raise KeyError(
            f"Payload key '{TEXT_PAYLOAD_KEY}' was not found. "
            f"Available keys: {list(payload.keys())}"
        )

    for key in TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    # Do not silently rerank a metadata dictionary as document text.
    return ""


def print_results(title, results):
    print("\n" + "=" * 90)
    print(title)
    print("=" * 90)

    if not results:
        print("No results to display.")
        return

    for item in results:
        print(f"\nRank: {item['rank']}")
        print(f"Qdrant similarity score: {item['qdrant_score']:.6f}")

        if item.get("reranker_score") is not None:
            print(f"Cross-encoder score:     {item['reranker_score']:.6f}")

        print(f"Point ID: {item['id']}")

        payload = item["payload"]
        for key in (
            "title",
            "filename",
            "source",
            "source_url",
            "page",
            "section",
        ):
            if payload.get(key) is not None:
                print(f"{key}: {payload[key]}")

        print("\nPassage:")
        print(item["text"])
        print("-" * 90)


def main():
    if not QDRANT_URL or not QDRANT_API_KEY:
        raise RuntimeError(
            "QDRANT_URL or QDRANT_API_KEY is missing from your .env file."
        )

    question = input("\nEnter your RBI question: ").strip()
    if not question:
        raise ValueError("The question cannot be empty.")

    print("\nConnecting to Qdrant Cloud...")
    client = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY,
    )

    # Confirm the collection exists and inspect its configuration.
    collection_info = client.get_collection(COLLECTION_NAME)
    print(f"Collection: {COLLECTION_NAME}")
    print(f"Collection points: {collection_info.points_count}")

    print(f"\nLoading embedding model: {EMBEDDING_MODEL_NAME}")
    print(EMBEDDING_MODEL_NAME)

    print(f"Embedding the question: {question}")
    query_vector = embed_query(question)

    # Stage 1: existing vector retrieval. No collection changes.
    print(f"\nRetrieving top {TOP_K} candidates from Qdrant...")
    response = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=TOP_K,
        with_payload=True,
        with_vectors=False,
    )

    points = response.points

    if not points:
        print("Qdrant returned no points for this question.")
        return

    candidates = []

    for point in points:
        payload = point.payload or {}
        text = get_passage_text(payload)

        if not text:
            print(
                f"Warning: skipping point {point.id}; "
                "no passage text field was identified. "
                f"Payload keys: {list(payload.keys())}"
            )
            continue

        candidates.append({
            "id": point.id,
            "qdrant_score": float(point.score),
            "payload": payload,
            "text": text,
        })

    if not candidates:
        raise RuntimeError(
            "Retrieved points, but no passage text was found. "
            "Inspect your Qdrant payload keys and set TEXT_PAYLOAD_KEY "
            "to the correct field in .env."
        )

    # Preserve the order returned by Qdrant for the baseline.
    original_results = [
        {**item, "rank": index + 1, "reranker_score": None}
        for index, item in enumerate(candidates)
    ]

    print_results("BEFORE RERANKING — QDRANT ORDER", original_results)

    # Stage 2: load the cross-encoder.
    print(f"\nLoading reranker: {RERANKER_MODEL_NAME}")
    reranker = TextCrossEncoder(model_name=RERANKER_MODEL_NAME)

    pairs = [
        (question, item["text"])
        for item in candidates
    ]

    print(f"Scoring {len(pairs)} question-passage pairs...")
    scores = reranker.rerank(question, [item["text"] for item in candidates])

    # Stage 3: sort the same candidates by cross-encoder score.
    reranked = []

    for item, score in zip(candidates, scores):
        reranked.append({
            **item,
            "reranker_score": float(score),
        })

    reranked.sort(
        key=lambda item: item["reranker_score"],
        reverse=True,
    )

    reranked_results = [
        {**item, "rank": index + 1}
        for index, item in enumerate(reranked)
    ]

    print_results(
        "AFTER RERANKING — BGE CROSS-ENCODER ORDER",
        reranked_results,
    )

    # Show how each point moved.
    old_ranks = {
        item["id"]: item["rank"]
        for item in original_results
    }

    print("\n" + "=" * 90)
    print("RANK MOVEMENT")
    print("=" * 90)

    for item in reranked_results:
        old_rank = old_ranks[item["id"]]
        new_rank = item["rank"]
        movement = old_rank - new_rank

        if movement > 0:
            change = f"moved UP {movement} position(s)"
        elif movement < 0:
            change = f"moved DOWN {abs(movement)} position(s)"
        else:
            change = "unchanged"

        print(
            f"Point {item['id']}: "
            f"rank {old_rank} -> {new_rank} ({change}); "
            f"reranker score = {item['reranker_score']:.6f}"
        )

    print("\nExperiment complete.")
    print("No points were inserted, updated, or deleted in Qdrant.")


if __name__ == "__main__":
    main()
    