from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUDIT_LOG_PATH = Path(
    os.getenv(
        "RBI_QUERY_AUDIT_LOG",
        str(ROOT / "data" / "query_analyzer_audit.jsonl"),
    )
)
_write_lock = threading.Lock()
logger = logging.getLogger(__name__)


def record_audit_event(
    request_id: str,
    event: str,
    **fields: Any,
) -> None:
    record = {
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            with AUDIT_LOG_PATH.open("a", encoding="utf-8") as audit_file:
                audit_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("Could not append retrieval audit event.")


def _request_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    request_event = next(
        (event for event in events if event["event"] == "request_received"),
        {},
    )
    plan_event = next(
        (
            event
            for event in events
            if event["event"] in {
                "search_plan_generated",
                "retrieval_plan_generated",
            }
        ),
        {},
    )
    validation_event = next(
        (
            event
            for event in reversed(events)
            if isinstance(event.get("plan_validation"), dict)
        ),
        {},
    )
    mode_event = next(
        (event for event in events if event["event"] == "retrieval_mode_selected"),
        plan_event,
    )
    analysis_event = next(
        (
            event
            for event in reversed(events)
            if event["event"] in {
                "query_analysis_completed",
                "query_analysis_fallback_plan_generated",
            }
        ),
        {},
    )
    analysis_failure = next(
        (
            event
            for event in events
            if event["event"] == "query_analysis_failed"
        ),
        {},
    )
    fallback_event = next(
        (
            event
            for event in events
            if event["event"] == "query_analysis_fallback_plan_generated"
        ),
        {},
    )
    fallback_disabled_event = next(
        (
            event
            for event in events
            if event["event"] == "query_analysis_fallback_disabled"
        ),
        {},
    )
    answer_event = next(
        (
            event
            for event in reversed(events)
            if event["event"] in {
                "answer_generation_completed",
                "answer_generation_failed",
            }
        ),
        {},
    )
    response_event = next(
        (
            event
            for event in reversed(events)
            if event["event"] == "chat_response_ready"
        ),
        {},
    )
    planned_searches = plan_event.get("searches", [])
    searches: dict[int, dict[str, Any]] = {}
    executed_operations: set[int] = set()
    for index, planned in enumerate(planned_searches, start=1):
        searches[index] = {
            "operation": index,
            "query": planned.get("query", ""),
            "purpose": planned.get("purpose", ""),
            "evidence_requirement_ids": planned.get(
                "evidence_requirement_ids", []
            ),
            "query_source": plan_event.get("query_source", "original_question"),
            "status": "skipped",
            "passages": [],
        }

    for event in events:
        operation = event.get("search_operation")
        if not isinstance(operation, int):
            continue
        search = searches.setdefault(
            operation,
            {
                "operation": operation,
                "query": event.get("query", ""),
                "purpose": event.get("purpose", ""),
                "evidence_requirement_ids": event.get(
                    "evidence_requirement_ids", []
                ),
                "query_source": event.get("query_source", "original_question"),
                "status": "skipped",
                "passages": [],
            },
        )
        if event["event"] == "retrieval_search_started":
            executed_operations.add(operation)
            search.update(
                status="running",
                started_at=event.get("timestamp"),
                query=event.get("query", search["query"]),
                purpose=event.get("purpose", search["purpose"]),
                query_source=event.get("query_source", search["query_source"]),
                collection=event.get("collection"),
            )
        elif event["event"] == "retrieval_search_completed":
            search.update(
                status="succeeded",
                ended_at=event.get("timestamp"),
                duration_ms=event.get("duration_ms"),
                passage_count=event.get("passage_count", 0),
                passages=event.get("passages", []),
                collection=event.get("collection"),
            )
        elif event["event"] == "retrieval_search_failed":
            search.update(
                status="failed",
                ended_at=event.get("timestamp"),
                duration_ms=event.get("duration_ms"),
                error=event.get("error"),
                collection=event.get("collection"),
            )
        elif event["event"] == "retrieval_search_skipped":
            search.update(
                status="skipped",
                error=event.get("reason"),
                collection=event.get("collection"),
            )

    successful_searches = sum(
        search["status"] == "succeeded" for search in searches.values()
    )
    failed_searches = sum(
        search["status"] == "failed" for search in searches.values()
    )
    attempted_searches = sum(
        search["status"] in {"succeeded", "failed", "running"}
        for search in searches.values()
    )
    total_passages = sum(
        search.get("passage_count", 0) for search in searches.values()
    )
    direct_retrieval = (
        plan_event.get("event") == "retrieval_plan_generated"
        and plan_event.get("query_source") == "original_question"
    )
    return {
        "request_id": events[0]["request_id"],
        "timestamp": request_event.get("timestamp", events[0]["timestamp"]),
        "original_query": request_event.get("question", ""),
        "status": response_event.get("status")
        or (
            "failed"
            if answer_event.get("status") == "failed"
            or any(event["event"] == "chat_request_failed" for event in events)
            else "processing"
        ),
        "retrieval_mode": mode_event.get("retrieval_mode", "pending"),
        "analyzer_decision": analysis_event.get(
            "analyzer_decision",
            analysis_event.get(
                "retrieval_mode",
                "unavailable"
                if analysis_failure
                else "not_applicable"
                if direct_retrieval
                else "pending",
            ),
        ),
        "analyzer_status": analysis_event.get(
            "analyzer_status",
            "failed"
            if analysis_failure
            else "not_used"
            if direct_retrieval
            else "pending",
        ),
        "analyzer_error_type": analysis_failure.get("error_type"),
        "analyzer_error": analysis_failure.get("error"),
        "query_source": analysis_event.get(
            "query_source",
            "fallback"
            if fallback_event
            else "not_executed"
            if fallback_disabled_event
            else plan_event.get("query_source", "pending"),
        ),
        "fallback_strategy": fallback_event.get(
            "fallback_strategy",
            fallback_disabled_event.get("fallback_strategy"),
        ),
        "reason": plan_event.get("reason", ""),
        "evidence_requirements": plan_event.get(
            "evidence_requirements",
            analysis_event.get("evidence_requirements", []),
        ),
        "plan_validation": plan_event.get(
            "plan_validation",
            validation_event.get("plan_validation"),
        ),
        "planned_search_count": plan_event.get(
            "planned_search_count",
            len(planned_searches),
        ),
        "executed_search_count": len(executed_operations),
        "attempted_search_count": attempted_searches,
        "successful_search_count": successful_searches,
        "failed_search_count": failed_searches,
        "total_passages": total_passages,
        "retrieval_skipped": any(
            event["event"] == "retrieval_skipped" for event in events
        ),
        "context_supplied": next(
            (
                event.get("context_supplied")
                for event in events
                if event["event"] == "context_assembly_completed"
            ),
            None,
        ),
        "answer_generation_started": any(
            event["event"] == "answer_generation_started" for event in events
        ),
        "answer_generation_status": answer_event.get("status", "pending"),
        "generation_method": answer_event.get("generation_method"),
        "response_status": response_event.get("status", "pending"),
        "searches": [searches[index] for index in sorted(searches)],
        "events": events,
    }


def read_audit_requests(
    limit: int = 50,
    request_id: str | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    if not AUDIT_LOG_PATH.is_file():
        return []

    latest_lines: deque[str] = deque(maxlen=20000)
    try:
        with AUDIT_LOG_PATH.open("r", encoding="utf-8") as audit_file:
            latest_lines.extend(audit_file)
    except OSError:
        logger.exception("Could not read retrieval audit log.")
        raise

    requests: dict[str, list[dict[str, Any]]] = {}
    for line in latest_lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed retrieval audit log line.")
            continue
        trace_id = event.get("request_id")
        if not isinstance(trace_id, str) or (
            request_id is not None and trace_id != request_id
        ):
            continue
        requests.setdefault(trace_id, []).append(event)

    ordered = sorted(
        (
            events
            for events in requests.values()
            if user_id is None
            or any(
                event.get("event") == "request_received"
                and event.get("user_id") == user_id
                for event in events
            )
        ),
        key=lambda events: events[0].get("timestamp", ""),
        reverse=True,
    )
    return [_request_summary(events) for events in ordered[:limit]]
