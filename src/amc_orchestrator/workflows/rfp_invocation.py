"""Shared single-turn RFP invocation, now with optional AgentCore Memory (WS9).

Consolidates what `cli.py`, `api/routes/rfp.py`, and `runtime_entrypoint.py`
each duplicated identically since M8 (the `build_rfp_graph(settings)` context
manager, the try/except-around-`graph(...)` safety net, and
`summarize_result`/`summarize_exception`) into one place, and layers memory
read/write around it:

  1. `read_prior_turns` (best-effort, None when memory is disabled/no
     session_id/nothing found) is prepended to the question actually sent
     into the graph - `graph(question)` takes a single string, and quant/qual
     are the entry-point nodes that receive it as their task, so this is the
     one point that reaches every downstream node.
  2. The graph runs exactly as before, using the ORIGINAL question for the
     memory write-back (not the context-prepended one) so stored turns stay
     a clean, minimal record - re-reading them later shouldn't re-prepend
     ever-growing prior context into itself.
  3. `write_turn` is best-effort and never raises, so it runs after both the
     success and exception paths - even an escalation holding message is a
     legitimate turn to remember (the client did ask something, and the
     system did respond with something).

`session_id` is `None` by default for callers with no session concept (e.g.
a one-shot CLI invocation with no second argument) - `read_prior_turns`/
`write_turn` already treat a falsy `session_id` as "memory not applicable"
and no-op cleanly, so this file itself never needs an `if session_id` guard.
"""

from __future__ import annotations

import structlog

from amc_orchestrator.config.settings import Settings
from amc_orchestrator.memory.agentcore_memory_client import read_prior_turns, write_turn
from amc_orchestrator.workflows.graph_build import build_rfp_graph
from amc_orchestrator.workflows.result_extraction import (
    RfpOutcome,
    summarize_exception,
    summarize_result,
)

logger = structlog.get_logger(__name__)


def invoke_rfp(settings: Settings, question: str, session_id: str | None = None) -> RfpOutcome:
    """Run one RFP question through the graph, with optional memory context."""
    prior_context = read_prior_turns(settings, session_id)
    augmented_question = f"{prior_context}\n\n{question}" if prior_context else question

    try:
        # build_rfp_graph is a context manager (opens the Gateway MCP client
        # for the whole invocation when settings.effective_tool_backend ==
        # "gateway") - construction itself can fail (e.g. an unreachable
        # Gateway), so it's inside this try alongside graph execution to
        # keep the never-crash resilience contract for both.
        with build_rfp_graph(settings) as graph:
            result = graph(augmented_question)
        outcome = summarize_result(result)
    except Exception as exc:  # graph node execution is fail-fast; never crash the caller
        logger.error("graph_invocation_failed", error=str(exc), error_type=type(exc).__name__)
        outcome = summarize_exception(exc)

    write_turn(settings, session_id, question, outcome.response_text)
    return outcome
