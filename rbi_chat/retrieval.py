from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from retrieve import (
    COLLECTION_NAME,
    MODEL_NAME,
    connect_qdrant,
    ensure_source_filter_index,
    load_embedding_model,
    retrieve_chunks,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("RBI_DATA_DIR", ROOT / "data"))
TEXT_DIR = Path(os.getenv("RBI_TEXT_DIR", DATA_DIR / "rbi_text"))
PAGE_MARKER = re.compile(
    r"(?m)^[ \t]*---[ \t]*Page[ \t]+(\d+)[ \t]*---[ \t]*(?:\r?\n|$)",
    re.IGNORECASE,
)
MIN_RELEVANCE = 0.55


def _display_page_reference(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(page) for page in value)
    return str(value) if value is not None else ""


def _source_reference(
    payload: dict[str, Any],
    page_numbers: list[int] | None = None,
) -> str:
    references: list[str] = []
    for key, label in (
        ("chapter", "Chapter"),
        ("section_number", "Section"),
        ("clause_labels", "Clauses"),
    ):
        value = payload.get(key)
        if not value:
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        if key == "chapter" and str(value).casefold().startswith("chapter "):
            references.append(str(value))
        else:
            references.append(f"{label} {value}")
    pages = page_numbers if page_numbers is not None else payload.get("page_numbers")
    if pages:
        references.append(f"Pages {_display_page_reference(pages)}")
    return " · ".join(references)


class RegulatoryRetriever:
    """Qdrant-backed retrieval shared by chat and report jobs."""

    def __init__(self) -> None:
        self._client = None
        self._model = None
        self._ready = False
        self._state = "not_ready"
        self._error: str | None = None
        self._sources: list[dict[str, str]] = []
        self._point_count = 0
        self._page_markers: dict[str, list[tuple[int, int]]] = {}
        self._source_text: dict[str, str] = {}

    @property
    def status(self) -> dict[str, Any]:
        return {
            "ready": self._ready,
            "state": self._state,
            "error": self._error,
            "source_count": len(self._sources),
            "point_count": self._point_count,
            "index": "qdrant_cloud",
            "embedding_model": MODEL_NAME,
            "collection": COLLECTION_NAME,
        }

    def initialize(self) -> None:
        if self._ready:
            return

        self._state = "connecting_to_qdrant"
        self._client = connect_qdrant()
        collection = self._client.get_collection(COLLECTION_NAME)
        self._point_count = collection.points_count or 0
        ensure_source_filter_index(self._client)

        self._state = "loading_embedding_model"
        self._model = load_embedding_model()

        self._state = "loading_sources"
        self._sources = self._load_sources()
        if not self._sources:
            raise RuntimeError(
                f"No source documents found in Qdrant collection {COLLECTION_NAME!r}."
            )

        self._ready = True
        self._state = "ready"
        self._error = None

    def _load_sources(self) -> list[dict[str, str]]:
        if self._client is None:
            raise RuntimeError("The Qdrant client is unavailable.")

        sources: dict[str, dict[str, str]] = {}
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=COLLECTION_NAME,
                limit=512,
                offset=offset,
                with_payload=["source_file", "document_title", "title"],
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                filename = payload.get("source_file")
                if not filename:
                    continue
                filename = str(filename)
                title = payload.get("document_title") or payload.get("title")
                sources.setdefault(
                    filename,
                    {
                        "filename": filename,
                        "title": str(title) if title else Path(filename).stem,
                    },
                )
            if offset is None:
                break

        return sorted(sources.values(), key=lambda source: source["filename"].casefold())

    def sources(self) -> list[dict[str, str]]:
        if not self._ready:
            self.initialize()
        return list(self._sources)

    def _load_page_markers(self, document: str) -> list[tuple[int, int]]:
        filename = Path(document).name
        if filename not in self._page_markers:
            source_path = TEXT_DIR / filename
            if not source_path.is_file():
                self._page_markers[filename] = []
                return []
            with source_path.open("r", encoding="utf-8", newline="") as source_file:
                text = source_file.read()
            self._source_text[filename] = text
            self._page_markers[filename] = [
                (match.start(), int(match.group(1)))
                for match in PAGE_MARKER.finditer(text)
            ]
        return self._page_markers[filename]

    def resolve_pages(self, source: dict[str, Any]) -> list[int]:
        document = str(source.get("document") or "")
        if not document or Path(document).name != document:
            return []
        markers = self._load_page_markers(document)
        if not markers:
            return []

        text = self._source_text[document]
        start = source.get("start_char")
        end = source.get("end_char")
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start < end <= len(text)
        ):
            return self._pages_in_range(markers, start, end)

        excerpt = str(source.get("excerpt") or "")
        if not excerpt:
            return []
        first_match = text.find(excerpt)
        if first_match < 0 or text.find(excerpt, first_match + 1) >= 0:
            return []
        return self._pages_in_range(markers, first_match, first_match + len(excerpt))

    @staticmethod
    def _pages_in_range(
        markers: list[tuple[int, int]],
        start: int,
        end: int,
    ) -> list[int]:
        pages = []
        for index, (marker_start, page) in enumerate(markers):
            segment_end = markers[index + 1][0] if index + 1 < len(markers) else end
            if segment_end > start and marker_start < end:
                pages.append(page)
        return pages

    def retrieve(
        self,
        query: str,
        limit: int = 6,
        source_files: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self._ready:
            self.initialize()
        if self._client is None or self._model is None:
            raise RuntimeError("The Qdrant retrieval index is unavailable.")

        passages = retrieve_chunks(
            client=self._client,
            model=self._model,
            question=query,
            top_k=max(limit, 1),
            source_files=source_files,
        )
        sources: list[dict[str, Any]] = []
        for rank, point in enumerate(passages, start=1):
            payload = point.payload or {}
            document = str(payload.get("source_file") or "Unknown source")
            title = (
                payload.get("section_title")
                or payload.get("heading_or_clause")
                or payload.get("document_title")
                or Path(document).stem
            )
            score = float(point.score or 0.0)
            source = {
                "id": f"S{rank}",
                "title": str(title),
                "document": document,
                "chapter": payload.get("chapter"),
                "section": payload.get("section_number"),
                "clauses": payload.get("clause_labels"),
                "excerpt": str(payload.get("text") or "").strip(),
                "relevance": max(0.0, min(score, 1.0)),
            }
            page_source = {
                **source,
                "start_char": payload.get("start_char"),
                "end_char": payload.get("end_char"),
            }
            page_numbers = self.resolve_pages(page_source) or payload.get("page_numbers") or []
            source["page"] = _display_page_reference(page_numbers)
            source["reference"] = _source_reference(source, page_numbers)
            sources.append(source)

        return {
            "sources": sources,
            "sufficient": bool(
                sources and sources[0]["relevance"] >= MIN_RELEVANCE
            ),
            "retrieval_mode": "qdrant_vector_search",
        }

    def retrieve_planned_query(self, query: str) -> dict[str, Any]:
        """Execute one analyzer-planned query using the interactive retrieval strategy."""
        if not self._ready:
            self.initialize()
        if self._client is None or self._model is None:
            raise RuntimeError("The Qdrant retrieval index is unavailable.")
        if not query or not query.strip():
            raise ValueError("Please enter a complete retrieval query.")

        query_vector = self._model.encode(
            query.strip(),
            normalize_embeddings=False,
        ).tolist()
        response = self._client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=5,
            with_payload=True,
        )

        sources: list[dict[str, Any]] = []
        for rank, point in enumerate(response.points, start=1):
            payload = point.payload or {}
            document = str(payload.get("source_file") or "Unknown source")
            title = (
                payload.get("section_title")
                or payload.get("heading_or_clause")
                or payload.get("document_title")
                or Path(document).stem
            )
            score = float(point.score or 0.0)
            source = {
                "id": f"S{rank}",
                "title": str(title),
                "document": document,
                "chapter": payload.get("chapter"),
                "section": payload.get("section_number"),
                "clauses": payload.get("clause_labels"),
                "excerpt": str(payload.get("text") or "").strip(),
                "relevance": max(0.0, min(score, 1.0)),
            }
            page_source = {
                **source,
                "start_char": payload.get("start_char"),
                "end_char": payload.get("end_char"),
            }
            page_numbers = (
                self.resolve_pages(page_source) or payload.get("page_numbers") or []
            )
            source["page"] = _display_page_reference(page_numbers)
            source["reference"] = _source_reference(source, page_numbers)
            sources.append(source)

        return {
            "sources": sources,
            "sufficient": bool(
                sources and sources[0]["relevance"] >= MIN_RELEVANCE
            ),
            "retrieval_mode": "qdrant_vector_search",
        }


retriever = RegulatoryRetriever()
