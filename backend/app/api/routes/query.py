from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.schemas.query import QueryRequest, QueryResponse
from app.schemas.response import ApiResponse, success_response
from app.services.agent_harness import AgentHarness
from app.dependencies import get_agent_harness


router = APIRouter()


@router.post("", response_model=ApiResponse[QueryResponse])
async def run_query(
    request: Request,
    payload: QueryRequest,
    harness: AgentHarness = Depends(get_agent_harness),
) -> ApiResponse[QueryResponse]:
    # AgentHarness 负责具体任务流程；这里仅做 HTTP 请求到任务流程的适配。
    result = await harness.answer(
        payload,
        request_id=request.state.trace_id,
        metadata={"http_path": str(request.url.path)},
    )
    return success_response(data=result, trace_id=request.state.trace_id)


@router.post("/stream")
async def run_query_stream(
    request: Request,
    payload: QueryRequest,
    harness: AgentHarness = Depends(get_agent_harness),
) -> StreamingResponse:
    """SSE streaming endpoint: yields intermediate pipeline events.

    Each event is a JSON line: {"type": "...", "data": {...}, "timestamp": "..."}
    """
    async def event_generator():
        try:
            async for event in harness.answer_stream(
                payload,
                request_id=request.state.trace_id,
                metadata={"http_path": str(request.url.path), "stream": True},
            ):
                import json
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            import json
            error_event = {
                "type": "error",
                "data": {"message": str(exc)},
                "timestamp": "",
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
