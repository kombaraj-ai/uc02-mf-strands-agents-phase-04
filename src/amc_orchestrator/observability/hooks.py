"""Structured logging hooks attached to every agent.

Gives per-agent, per-tool-call structured logs (with whatever trace/request
IDs are bound via `logging_setup.bind_trace_context`) without any business
logic in the agents themselves having to log anything.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from strands.hooks import (
    AfterInvocationEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

logger = structlog.get_logger(__name__)


class LoggingHookProvider(HookProvider):
    """Logs agent invocation and tool-call lifecycle events as structured JSON."""

    def __init__(self, node_name: str) -> None:
        self._node_name = node_name
        self._invocation_started_at: dict[int, float] = {}

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self._on_before_invocation)
        registry.add_callback(AfterInvocationEvent, self._on_after_invocation)
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool_call)
        registry.add_callback(AfterToolCallEvent, self._on_after_tool_call)

    def _on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        self._invocation_started_at[id(event.agent)] = time.monotonic()
        logger.debug("agent_invocation_started", node=self._node_name)

    def _on_after_invocation(self, event: AfterInvocationEvent) -> None:
        started_at = self._invocation_started_at.pop(id(event.agent), None)
        duration_ms = round((time.monotonic() - started_at) * 1000) if started_at else None
        stop_reason = getattr(event.result, "stop_reason", None)
        logger.info(
            "agent_invocation_completed",
            node=self._node_name,
            duration_ms=duration_ms,
            stop_reason=stop_reason,
        )

    def _on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        logger.debug(
            "tool_call_started",
            node=self._node_name,
            tool_name=event.tool_use.get("name"),
        )

    def _on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        logger.info(
            "tool_call_completed",
            node=self._node_name,
            tool_name=event.tool_use.get("name"),
            status=event.result.get("status") if isinstance(event.result, dict) else None,
            had_exception=event.exception is not None,
        )


class QualGroundingHookProvider(HookProvider):
    """Forces `qual_narrative_pull`'s answer to stay honest when the Knowledge
    Base has nothing, instead of trusting the LLM to honor its own prompt.

    `search_fund_commentary` (`tools/qual_tools.py`) already returns an
    unambiguous sentinel string on an empty retrieval, and the qual agent's
    system prompt already says never to invent commentary in that case - but
    that's LLM instruction-following only, and the agent has been observed
    fabricating narrative anyway (see CLAUDE.md's Phase 03 "qual agent
    fabricates fund commentary" finding). This closes the gap at the code
    layer: if every `search_fund_commentary` call this turn came back with
    only the sentinel, the agent's final message is overwritten with a fixed,
    honest response rather than whatever prose the model generated - removing
    the opportunity to fabricate for the "nothing was retrieved at all" case
    this bug was actually observed in. A mix of found/not-found across
    multiple funds in one request is left to the model's own prompt
    adherence, same as before - only the all-empty case is enforced in code.
    """

    NOT_FOUND_SENTINEL = "No relevant fund manager commentary found for this query."
    NO_COMMENTARY_RESPONSE = "No relevant fund manager commentary was found for any fund in this request."
    _TOOL_NAME = "search_fund_commentary"

    def __init__(self) -> None:
        self._commentary_found: dict[int, bool] = {}
        self._search_performed: dict[int, bool] = {}

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self._on_before_invocation)
        registry.add_callback(AfterToolCallEvent, self._on_after_tool_call)
        registry.add_callback(AfterInvocationEvent, self._on_after_invocation)

    def _on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        key = id(event.agent)
        self._commentary_found[key] = False
        self._search_performed[key] = False

    def _on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        if event.tool_use.get("name") != self._TOOL_NAME:
            return
        key = id(event.agent)
        self._search_performed[key] = True
        result = event.result
        if not isinstance(result, dict):
            return
        text = "".join(
            block.get("text", "") for block in result.get("content", []) if isinstance(block, dict)
        )
        if text.strip() != self.NOT_FOUND_SENTINEL:
            self._commentary_found[key] = True

    def _on_after_invocation(self, event: AfterInvocationEvent) -> None:
        key = id(event.agent)
        searched = self._search_performed.pop(key, False)
        found = self._commentary_found.pop(key, False)
        if searched and not found and event.result is not None:
            event.result.message["content"] = [{"text": self.NO_COMMENTARY_RESPONSE}]
            logger.info("qual_grounding_enforced", node="qual_narrative_pull")
