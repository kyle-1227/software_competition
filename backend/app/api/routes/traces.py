from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import get_trace_store
from app.schemas.response import ApiResponse, success_response
from app.services.trace_store import TraceStore
from app.services.tracing.reader import find_trace_by_id, sanitize_trace_for_export
from app.services.tracing.summary import build_trace_summary
from app.services.tracing.timeline import build_trace_timeline

router = APIRouter()


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


def _get_trace_or_404(trace_store: TraceStore, trace_id: str):
    trace = find_trace_by_id(trace_store, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace not found: {trace_id}")
    return trace
