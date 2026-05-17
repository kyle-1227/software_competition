from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import get_trace_store
from app.schemas.response import ApiResponse, success_response
from app.services.trace_store import TraceStore
from app.services.tracing.analytics import build_trace_analytics
from app.services.tracing.reader import (
    find_trace_by_id,
    sanitize_span_for_export,
    sanitize_trace_for_export,
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
