"""Amazon Bedrock AgentCore Runtime entrypoint.

Packaged into the container built from the repo-root `Dockerfile` and run via
`uv run uvicorn amc_orchestrator.runtime_entrypoint:app --host 0.0.0.0 --port 8080`
(the exact CMD in that Dockerfile) - this is what
`infra/terraform/modules/agentcore-runtime` points `agent_runtime_artifact` at
once an image built from it is pushed to ECR.

Deliberately thin: reuses `workflows.graph_build.build_rfp_graph` and
`workflows.result_extraction.{summarize_result,summarize_exception}` exactly as
`cli.py` and `api/routes/rfp.py` already do - no new translation logic, same
resilience contract (never crash, always a well-formed outcome).

`BedrockAgentCoreApp` implements the HTTP protocol contract AgentCore Runtime
expects: `POST /invocations` (routed to the `@app.entrypoint`-decorated
function below) and `GET /ping` (built in, no code needed here). See
`docs/architecture.md`'s "Phase 02" section and
`infra/terraform/README.md` for the container-image bootstrap flow this
unblocks.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from amc_orchestrator.config.settings import get_settings
from amc_orchestrator.data import qual_store, quant_store
from amc_orchestrator.observability.logging_setup import configure_logging
from amc_orchestrator.workflows.rfp_invocation import invoke_rfp

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: BedrockAgentCoreApp) -> AsyncIterator[None]:
    """Same idempotent seeding as `api/main.py`'s lifespan - safe to run on every
    cold start regardless of which data backend is active."""
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    quant_store.ensure_seeded(settings)
    qual_store.ensure_seeded(settings)
    yield


app = BedrockAgentCoreApp(lifespan=lifespan)


@app.entrypoint
def invoke(payload: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Handle one `POST /invocations` call.

    `payload` is expected to carry the client's question under `"prompt"` -
    the key used throughout AWS's own AgentCore Runtime examples/tooling
    (including the console's test invocation UI). `context.session_id`, when
    AgentCore Runtime supplies one, is the real cross-turn identity used for
    AgentCore Memory read/write (WS9, `settings.effective_memory_backend ==
    "agentcore"`) - the Runtime assigns a stable session_id per client
    session, so two `/invocations` calls sharing one give this entrypoint
    real multi-turn continuity for free. A no-op when memory is disabled.
    """
    question = payload.get("prompt", "")
    if not question:
        return {"error": "payload must include a non-empty 'prompt' string."}

    session_id = getattr(context, "session_id", None)
    logger.info("runtime_invocation_received", session_id=session_id)

    settings = get_settings()
    outcome = invoke_rfp(settings, question, session_id=session_id)

    return dataclasses.asdict(outcome)
