from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import get_agent_harness, get_approval_store
from app.schemas.query import QueryResponse
from app.schemas.response import ApiResponse, success_response
from app.services.agent_harness import AgentHarness
from app.services.approval_store import ApprovalRecord, ApprovalStore, ApprovalStatus

router = APIRouter()


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    reviewer: str | None = None
    note: str | None = None


class ApprovalDecisionResponse(BaseModel):
    approval: ApprovalRecord
    response: QueryResponse | None = None


@router.get("", response_model=ApiResponse[list[ApprovalRecord]])
async def list_approvals(
    status: ApprovalStatus | None = "pending",
    store: ApprovalStore = Depends(get_approval_store),
) -> ApiResponse[list[ApprovalRecord]]:
    records = store.list(status=status)
    trace_id = records[0].trace_id if records else "approvals"
    return success_response(data=records, trace_id=trace_id or "approvals")


@router.get("/{approval_id}", response_model=ApiResponse[ApprovalRecord])
async def get_approval(
    approval_id: str,
    store: ApprovalStore = Depends(get_approval_store),
) -> ApiResponse[ApprovalRecord]:
    record = store.get(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return success_response(data=record, trace_id=record.trace_id or approval_id)


@router.post("/{approval_id}/decision", response_model=ApiResponse[ApprovalDecisionResponse])
async def decide_approval(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    store: ApprovalStore = Depends(get_approval_store),
    harness: AgentHarness = Depends(get_agent_harness),
) -> ApiResponse[ApprovalDecisionResponse]:
    try:
        record = store.decide(
            approval_id,
            decision=payload.decision,
            reviewer=payload.reviewer,
            note=payload.note,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    response = await harness.resume_approval(record)
    return success_response(
        data=ApprovalDecisionResponse(approval=record, response=response),
        trace_id=record.trace_id or approval_id,
    )
