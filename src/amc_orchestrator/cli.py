"""Direct graph invocation from a terminal - the pre-API smoke-testing path.

Usage:
    uv run python -m amc_orchestrator.cli "<client question>" [session_id]

The optional `session_id` lets a local run exercise WS9 AgentCore Memory
continuity (`MEMORY_BACKEND=agentcore`) - pass the same value across two
invocations to prepend the first turn's context into the second. Omit it for
the ordinary one-shot smoke-testing path; memory is a no-op without one.
"""

from __future__ import annotations

import sys
import uuid

import structlog

from amc_orchestrator.config.settings import get_settings
from amc_orchestrator.data import qual_store, quant_store
from amc_orchestrator.observability.logging_setup import bind_trace_context, configure_logging
from amc_orchestrator.workflows.rfp_invocation import invoke_rfp

logger = structlog.get_logger(__name__)


def bootstrap_dev_data() -> None:
    """Idempotently seed the active data backend (SQLite+Chroma, or DynamoDB+KB)."""
    settings = get_settings()
    quant_store.ensure_seeded(settings)
    qual_store.ensure_seeded(settings)


def run_rfp_query(question: str, session_id: str | None = None) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt="console" if settings.environment == "dev" else "json")
    bind_trace_context(trace_id=str(uuid.uuid4()))

    bootstrap_dev_data()

    print(f"--- Processing Client Query: '{question}' ---\n")
    outcome = invoke_rfp(settings, question, session_id=session_id)

    print("\n--- FINAL RFP RESPONSE ---")
    print(outcome.response_text)
    print("\n--- METADATA ---")
    print(f"graph_status={outcome.graph_status}")
    print(f"compliance_attempts={outcome.compliance_attempts}")
    print(f"escalated={outcome.escalated}")


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: uv run python -m amc_orchestrator.cli "<client question>" [session_id]')
        raise SystemExit(1)
    session_id = sys.argv[2] if len(sys.argv) > 2 else None
    run_rfp_query(sys.argv[1], session_id=session_id)


if __name__ == "__main__":
    main()
