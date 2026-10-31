
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer
from retrieve import ensure_source_filter_index


# --------------------------------------------------
# Configuration
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
CHUNKS_FILE = PROJECT_ROOT / "data" / "rbi_text" / "rbi_024_chunks.json"

COLLECTION_NAME = os.getenv(
    "QDRANT_COLLECTION", "rbi_regulations"
)
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
BATCH_SIZE = 32

# Stable namespace for deterministic chunk IDs
ID_NAMESPACE = uuid.UUID("f72b7e7d-88bd-4a7b-b62c-a01e8fbb52a1")


# --------------------------------------------------
# Load and validate chunks
# --------------------------------------------------

def load_chunks(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"Chunks file not found: {path}\n"
            "Run semantic_splitter.py first."
        )

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    # Support either a JSON list or a dictionary containing a list.
    if isinstance(data, dict):
        for key in ("chunks", "documents", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break

    if not isinstance(data, list):
        raise ValueError(
            "Expected a list of chunks, or a dictionary containing "
            "'chunks', 'documents', or 'results'."
        )

    valid_chunks = []

    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Chunk {index} is not a JSON object.")

        # Accept common text field names.
        chunk_text = (
            item.get("text")
            or item.get("content")
            or item.get("chunk_text")
        )

        if not isinstance(chunk_text, str) or not chunk_text.strip():
            raise ValueError(
                f"Chunk {index} has no usable text field. "
                f"Available fields: {list(item.keys())}"
            )

        # Metadata can be nested or stored alongside the text.
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        # Preserve additional fields from the splitter.
        for key, value in item.items():
            if key not in {
                "text", "content", "chunk_text",
                "metadata", "embedding", "vector"
            }:
                metadata.setdefault(key, value)

        valid_chunks.append({
            "text": chunk_text,
            "metadata": metadata,
            "index": index,
        })

    if not valid_chunks:
        raise ValueError("No valid chunks found in the JSON file.")

    return valid_chunks


# --------------------------------------------------
# Prepare Qdrant-compatible metadata
# --------------------------------------------------

def clean_payload_value(value):
    """Keep payload values compatible with Qdrant JSON payloads."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (list, tuple)):
        cleaned = [clean_payload_value(v) for v in value]
        return [v for v in cleaned if v is not None]

    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)

    return str(value)


def make_point_id(chunk_text: str, metadata: dict) -> str:
    """Generate the same UUID when indexing the same chunk again."""
    source = str(
        metadata.get("source_file")
        or metadata.get("source")
        or CHUNKS_FILE.name
    )
    start = metadata.get("start_char", metadata.get("start_offset", ""))
    end = metadata.get("end_char", metadata.get("end_offset", ""))

    fingerprint = hashlib.sha256(
        f"{source}|{start}|{end}|{chunk_text}".encode("utf-8")
    ).hexdigest()

    return str(uuid.uuid5(ID_NAMESPACE, fingerprint))


def prepare_payload(chunk: dict) -> tuple[str, dict]:
    text = chunk["text"]
    metadata = chunk["metadata"]

    payload = {
        "text": text,
        "source_file": str(
            metadata.get("source_file")
            or metadata.get("source")
            or CHUNKS_FILE.name
        ),
        "chunk_index": chunk["index"],
        "text_sha256": hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest(),
    }

    for key, value in metadata.items():
        if key in {"text", "content", "embedding", "vector"}:
            continue

        cleaned = clean_payload_value(value)
        if cleaned is not None:
            payload[str(key)] = cleaned

    point_id = make_point_id(text, metadata)
    return point_id, payload


# --------------------------------------------------
# Connect to Qdrant Cloud
# --------------------------------------------------

def connect_qdrant() -> QdrantClient:
    url = os.getenv("QDRANT_URL")
    api_key = os.getenv("QDRANT_API_KEY")

    if not url or not api_key:
        raise EnvironmentError(
            "QDRANT_URL or QDRANT_API_KEY is missing.\n"
            "Add both values to your .env file."
        )

    client = QdrantClient(
        url=url,
        api_key=api_key,
        timeout=60,
    )

    # Verify the cloud connection.
    client.get_collections()
    print("Connected to Qdrant Cloud.")

    return client


def ensure_collection(client: QdrantClient, vector_size: int):
    existing = client.get_collections().collections
    collection_exists = any(
        collection.name == COLLECTION_NAME
        for collection in existing
    )

    if not collection_exists:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            ),
        )
        print(f"Created collection: {COLLECTION_NAME}")
        return

    info = client.get_collection(COLLECTION_NAME)
    config = info.config.params.vectors

    # This script uses a single unnamed dense vector.
    if not isinstance(config, models.VectorParams):
        raise ValueError(
            f"Collection '{COLLECTION_NAME}' does not use a single "
            "unnamed dense vector. Use a new collection name or "
            "configure the script for your collection's vector schema."
        )

    if config.size != vector_size:
        raise ValueError(
            f"Collection '{COLLECTION_NAME}' has vector size "
            f"{config.size}, but model '{MODEL_NAME}' produces "
            f"{vector_size}-dimensional vectors. Use a new collection "
            "name or recreate the collection after backing up its data."
        )

    if config.distance != models.Distance.COSINE:
        raise ValueError(
            f"Collection '{COLLECTION_NAME}' uses {config.distance}, "
            "but this script expects COSINE distance."
        )

    print(f"Using existing collection: {COLLECTION_NAME}")


# --------------------------------------------------
# Embed and upsert chunks
# --------------------------------------------------

def main(chunks_files: list[Path] | None = None):
    load_dotenv(PROJECT_ROOT / "backend" / ".env", override=False)

    input_files = [CHUNKS_FILE] if chunks_files is None else chunks_files
    if not input_files:
        raise ValueError("At least one chunks file must be provided.")
    chunks = []
    for path in input_files:
        print(f"Loading chunks from: {path}")
        chunks.extend(load_chunks(path))
    print(f"Loaded {len(chunks)} chunks.")

    print(f"Loading embedding model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    vector_size = model.get_sentence_embedding_dimension()
    if vector_size is None:
        raise RuntimeError("Could not determine embedding dimension.")

    print(f"Embedding dimension: {vector_size}")

    client = connect_qdrant()
    ensure_collection(client, vector_size)
    ensure_source_filter_index(client)

    total = len(chunks)

    for start in range(0, total, BATCH_SIZE):
        batch = chunks[start:start + BATCH_SIZE]
        texts = [chunk["text"] for chunk in batch]

        vectors = model.encode(
            texts,
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

        points = []

        for chunk, vector in zip(batch, vectors):
            point_id, payload = prepare_payload(chunk)

            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=vector.tolist(),
                    payload=payload,
                )
            )

        client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
            wait=True,
        )

        processed = min(start + len(batch), total)
        print(f"Indexed {processed}/{total} chunks.")

    collection_info = client.get_collection(COLLECTION_NAME)

    print("\nIndexing completed.")
    print(f"Collection: {COLLECTION_NAME}")
    print(f"Points reported by Qdrant: {collection_info.points_count}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)