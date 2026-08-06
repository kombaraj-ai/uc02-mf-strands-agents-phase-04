"""RFP submission endpoint - the HTTP equivalent of `cli.py`.

Delegates to `workflows.rfp_invocation.invoke_rfp`, which applies the
identical try/except-around-`graph(...)` safety pattern as `cli.py` (see
CLAUDE.md "Bug #2"): Strands node execution is fail-fast, so a
`StructuredOutputException` from `compliance_check` propagates as a raw
Python exception, not a `FAILED` GraphResult. Without that try/except, it
would surface to callers as an unhandled 500 instead of the intended
graceful escalation response.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from amc_orchestrator.config.settings import Settings, get_settings
from amc_orchestrator.observability.logging_setup import bind_trace_context, clear_trace_context
from amc_orchestrator.workflows.result_extraction import RfpOutcome
from amc_orchestrator.workflows.rfp_invocation import invoke_rfp

router = APIRouter(tags=["rfp"])


class RfpRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        description="The client's institutional RFP / portfolio query.",
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "Optional client-supplied identifier for cross-turn continuity via "
            "AgentCore Memory (WS9, only takes effect when MEMORY_BACKEND=agentcore). "
            "Pass the same value across calls to give later turns context from "
            "earlier ones; omit for a stateless one-shot query."
        ),
    )


@router.post("/rfp", response_model=RfpOutcome)
def submit_rfp(request: RfpRequest, settings: Settings = Depends(get_settings)) -> RfpOutcome:  # noqa: B008
    request_id = str(uuid.uuid4())
    bind_trace_context(trace_id=request_id)
    try:
        return invoke_rfp(settings, request.question, session_id=request.session_id)
    finally:
        clear_trace_context()
