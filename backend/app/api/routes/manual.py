from fastapi import APIRouter, HTTPException, Request

from app.schemas.manual import ManualRegisterRequest, ManualRegisterResponse
from app.schemas.response import ApiResponse, success_response
from app.services.manual_indexer import ManualIndexer


router = APIRouter()


@router.post("/register", response_model=ApiResponse[ManualRegisterResponse])
async def register_manual(
    request: Request, payload: ManualRegisterRequest
) -> ApiResponse[ManualRegisterResponse]:
    indexer = ManualIndexer()
    try:
        result = await indexer.register_manual(payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success_response(data=result, trace_id=request.state.trace_id)
