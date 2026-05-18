from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import get_trace_store
from app.schemas.response import ApiResponse, success_response
from app.services.trace_store import TraceStore
from app.services.tracing.analytics import build_trace_analytics
from app.services.tracing.eval_adapter import (
    should_export_trace_to_eval,
    trace_to_eval_case,
)
from app.services.tracing.eval_dataset import TraceEvalDatasetWriter
from app.services.tracing.reader import (
    find_trace_by_id,
    sanitize_span_for_export,
    sanitize_trace_for_export,
)
from app.services.tracing.metrics import (
    build_trace_failure_metrics,
    build_trace_latency_metrics,
    build_trace_metrics_overview,
    build_trace_operational_metrics,
    build_trace_repository_metrics,
    build_trace_eval_readiness_metrics,
    load_recent_traces_for_metrics,
)
from app.services.tracing.summary import build_trace_summary
from app.services.tracing.timeline import build_trace_timeline

router = APIRouter()


@router.get("", response_model=ApiResponse[list[dict[str, Any]]])
async def list_traces(
    request: Request,
    limit: int = 50,
    session_id: str | None = None,
    status: str | None = None,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[list[dict[str, Any]]]:
    data = trace_store.list_trace_summaries(limit=limit, session_id=session_id, status=status)
    return success_response(data=data, trace_id=request.state.trace_id)


@router.get("/health", response_model=ApiResponse[dict[str, Any]])
async def get_trace_health(
    request: Request,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    return success_response(
        data=trace_store.health_status(),
        trace_id=request.state.trace_id,
    )


@router.get("/metrics", response_model=ApiResponse[dict[str, Any]])
async def get_trace_metrics(
    request: Request,
    window_hours: int = 24,
    limit: int = 1000,
    session_id: str | None = None,
    status: str | None = None,
    top_n: int = 10,
    slow_threshold_ms: int = 5000,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    result = load_recent_traces_for_metrics(
        trace_store, window_hours=window_hours, limit=limit, session_id=session_id, status=status
    )
    metrics = build_trace_operational_metrics(
        result.traces,
        repository_health=trace_store.health_status(),
        window_hours=window_hours,
        top_n=top_n,
        slow_threshold_ms=slow_threshold_ms,
    )
    metrics["metadata"]["trace_count"] = len(result.traces)
    metrics["metadata"]["skipped_trace_count"] = result.skipped_trace_count
    metrics["metadata"]["limit"] = limit
    metrics["metadata"]["session_id"] = session_id
    metrics["metadata"]["status"] = status
    return success_response(data=metrics, trace_id=request.state.trace_id)


@router.get("/metrics/overview", response_model=ApiResponse[dict[str, Any]])
async def get_trace_metrics_overview(
    request: Request,
    window_hours: int = 24,
    limit: int = 1000,
    session_id: str | None = None,
    status: str | None = None,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    result = load_recent_traces_for_metrics(
        trace_store, window_hours=window_hours, limit=limit, session_id=session_id, status=status
    )
    data = build_trace_metrics_overview(result.traces, window_hours=window_hours)
    return success_response(data=data, trace_id=request.state.trace_id)


@router.get("/metrics/failures", response_model=ApiResponse[dict[str, Any]])
async def get_trace_metrics_failures(
    request: Request,
    window_hours: int = 24,
    limit: int = 1000,
    session_id: str | None = None,
    status: str | None = None,
    top_n: int = 10,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    result = load_recent_traces_for_metrics(
        trace_store, window_hours=window_hours, limit=limit, session_id=session_id, status=status
    )
    data = build_trace_failure_metrics(result.traces, top_n=top_n)
    return success_response(data=data, trace_id=request.state.trace_id)


@router.get("/metrics/latency", response_model=ApiResponse[dict[str, Any]])
async def get_trace_metrics_latency(
    request: Request,
    window_hours: int = 24,
    limit: int = 1000,
    session_id: str | None = None,
    status: str | None = None,
    slow_threshold_ms: int = 5000,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    result = load_recent_traces_for_metrics(
        trace_store, window_hours=window_hours, limit=limit, session_id=session_id, status=status
    )
    data = build_trace_latency_metrics(result.traces, slow_threshold_ms=slow_threshold_ms)
    return success_response(data=data, trace_id=request.state.trace_id)


@router.get("/metrics/repository", response_model=ApiResponse[dict[str, Any]])
async def get_trace_metrics_repository(
    request: Request,
    window_hours: int = 24,
    limit: int = 1000,
    session_id: str | None = None,
    status: str | None = None,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    result = load_recent_traces_for_metrics(
        trace_store, window_hours=window_hours, limit=limit, session_id=session_id, status=status
    )
    data = build_trace_repository_metrics(result.traces, repository_health=trace_store.health_status())
    return success_response(data=data, trace_id=request.state.trace_id)


@router.get("/metrics/eval-readiness", response_model=ApiResponse[dict[str, Any]])
async def get_trace_metrics_eval_readiness(
    request: Request,
    window_hours: int = 24,
    limit: int = 1000,
    session_id: str | None = None,
    status: str | None = None,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    result = load_recent_traces_for_metrics(
        trace_store, window_hours=window_hours, limit=limit, session_id=session_id, status=status
    )
    data = build_trace_eval_readiness_metrics(result.traces)
    return success_response(data=data, trace_id=request.state.trace_id)


@router.get("/{trace_id}", response_model=ApiResponse[dict[str, Any]])
async def get_trace_raw(
    request: Request,
    trace_id: str,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    trace = _get_trace_or_404(trace_store, trace_id)
    return success_response(
        data=sanitize_trace_for_export(trace),
        trace_id=request.state.trace_id,
    )


@router.get("/{trace_id}/summary", response_model=ApiResponse[dict[str, Any]])
async def get_trace_summary(
    request: Request,
    trace_id: str,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    trace = _get_trace_or_404(trace_store, trace_id)
    return success_response(
        data=build_trace_summary(trace),
        trace_id=request.state.trace_id,
    )


@router.get("/{trace_id}/timeline", response_model=ApiResponse[dict[str, Any]])
async def get_trace_timeline(
    request: Request,
    trace_id: str,
    include_summary: bool = True,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    trace = _get_trace_or_404(trace_store, trace_id)
    return success_response(
        data={
            "format": "markdown",
            "timeline": build_trace_timeline(trace, include_summary=include_summary),
        },
        trace_id=request.state.trace_id,
    )


@router.get("/{trace_id}/spans", response_model=ApiResponse[list[dict[str, Any]]])
async def get_trace_spans(
    request: Request,
    trace_id: str,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[list[dict[str, Any]]]:
    _get_trace_or_404(trace_store, trace_id)
    spans = trace_store.list_spans(trace_id)
    return success_response(
        data=[sanitize_span_for_export(span) for span in spans],
        trace_id=request.state.trace_id,
    )


@router.get("/{trace_id}/tree", response_model=ApiResponse[dict[str, Any]])
async def get_trace_tree(
    request: Request,
    trace_id: str,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    trace = _get_trace_or_404(trace_store, trace_id)
    return success_response(
        data=sanitize_trace_for_export(trace),
        trace_id=request.state.trace_id,
    )


@router.get("/{trace_id}/analytics", response_model=ApiResponse[dict[str, Any]])
async def get_trace_analytics(
    request: Request,
    trace_id: str,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    trace = _get_trace_or_404(trace_store, trace_id)
    return success_response(
        data=build_trace_analytics(trace),
        trace_id=request.state.trace_id,
    )


@router.post("/{trace_id}/export-eval-case", response_model=ApiResponse[dict[str, Any]])
async def export_trace_eval_case(
    request: Request,
    trace_id: str,
    trace_store: TraceStore = Depends(get_trace_store),
) -> ApiResponse[dict[str, Any]]:
    trace = _get_trace_or_404(trace_store, trace_id)
    analytics = build_trace_analytics(trace)
    failure_type = str(analytics.get("failure_type") or "unknown_failure")
    dataset_path = TraceEvalDatasetWriter().path
    if not should_export_trace_to_eval(trace, analytics):
        return success_response(
            data={
                "exported": False,
                "deduplicated": False,
                "case_id": None,
                "dataset_path": str(dataset_path),
                "failure_type": failure_type,
                "reason": "trace_not_eligible",
            },
            trace_id=request.state.trace_id,
        )

    case = trace_to_eval_case(trace, source="api_export", analytics=analytics)
    writer = TraceEvalDatasetWriter(dataset_path)
    appended = writer.append_case(case)
    return success_response(
        data={
            "exported": appended,
            "deduplicated": not appended,
            "case_id": case["case_id"],
            "dataset_path": str(dataset_path),
            "failure_type": case["failure_type"],
            "reason": "exported" if appended else "duplicate_case_id",
        },
        trace_id=request.state.trace_id,
    )


def _get_trace_or_404(trace_store: TraceStore, trace_id: str):
    trace = find_trace_by_id(trace_store, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace not found: {trace_id}")
    return trace


def _trace_list_item(trace) -> dict[str, Any]:
    summary = build_trace_summary(trace)
    return {
        "trace_id": trace.trace_id,
        "session_id": trace.session_id,
        "status": trace.status,
        "created_at": trace.created_at,
        "closed_at": trace.closed_at,
        "total_duration_ms": trace.total_duration_ms,
        "question_preview": summary.get("question_preview"),
        "span_count": summary.get("span_count", 0),
        "error_count": summary.get("error_count", 0),
        "degraded_tool_names": summary.get("degraded_tool_names", []),
    }
