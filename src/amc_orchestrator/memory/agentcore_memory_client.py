"""Real AgentCore Memory read/write (WS9).

`bedrock_agentcore.memory.integrations.strands.session_manager.AgentCoreMemorySessionManager`
is the official Strands integration, but it cannot be used here: this
project's orchestrator is a `strands.multiagent.graph.Graph`, and attaching
any `SessionManager` to a Graph or its node Agents hard-fails against the
installed strands-agents 1.47.0 - confirmed by reproducing both failures
directly:
  - `Graph(session_manager=...)` raises `NotImplementedError: MultiAgent is
    not implemented for this repository` (the AgentCore class never
    overrides `create_multi_agent`/`read_multi_agent`/`update_multi_agent`).
  - `Agent(session_manager=...)` added to a `GraphBuilder` raises
    `ValueError: Session persistence is not supported for Graph agents yet.`
    (an explicit, unconditional check in `strands/multiagent/graph.py`).

So memory is wired as orchestration glue around the graph invocation
instead (see `workflows/rfp_invocation.py`), using
`bedrock_agentcore.memory.client.MemoryClient` directly - a ready-made
high-level client (confirmed via `inspect.signature`/`inspect.getsource`
against the real installed class), not raw boto3/SigV4 hand-rolling like
`tools/gateway_client.py` needed for the Gateway (Strands has no built-in
AWS-auth transport for MCP; it does have a memory client, just not one
usable with Graph).

`get_last_k_turns` (synchronous, no extraction lag) is the primary
mechanism for immediate cross-turn continuity - this is what a live
multi-turn test needs. `retrieve_memories` (semantic search over the
asynchronously-extracted long-term records the Terraform-provisioned
SEMANTIC strategy populates in the background) is layered in as a
secondary, best-effort enhancement - it may show nothing within a short
test window due to real extraction latency, so its absence is never
treated as an error.
"""

from __future__ import annotations

import structlog
from bedrock_agentcore.memory.client import MemoryClient

from amc_orchestrator.config.settings import Settings

logger = structlog.get_logger(__name__)

# No auth/user-identity concept exists anywhere in this project (confirmed -
# RequestContext has no actor field, no user auth at all). Sessions are the
# real isolation boundary here anyway: the Terraform-provisioned SEMANTIC
# strategy's namespace is "{sessionId}" only (not "{actorId}"), so actor_id
# is a required-by-API field that doesn't actually factor into this
# project's retrieval isolation. A single fixed constant, passed explicitly
# rather than buried, is a deliberate simplification - not silently picked.
DEFAULT_ACTOR_ID = "amc-rfp-orchestrator"

# Prior turns/records are prepended to every subsequent prompt (see
# workflows/rfp_invocation.py) - truncate so one long prior answer doesn't
# balloon every later turn's prompt indefinitely. Tunable, not a hard
# protocol requirement.
_MAX_LINE_CHARS = 600


def _client(settings: Settings) -> MemoryClient:
    # Builds its own boto3 clients internally (confirmed via
    # inspect.signature) - no connection to keep open across calls, unlike
    # tools/gateway_client.py's MCPClient, so a fresh instance per call is fine.
    return MemoryClient(region_name=settings.aws_region)


def _truncate(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _MAX_LINE_CHARS else text[:_MAX_LINE_CHARS] + "..."


def _format_turns(turns: list[list[dict]]) -> list[str]:
    """`get_last_k_turns` returns turns oldest-first, each turn a list of
    `{"content": {"text": ...}, "role": "USER"|"ASSISTANT"}` dicts (confirmed
    via reading the method's real source) - render as readable lines."""
    lines: list[str] = []
    for turn in turns:
        for message in turn:
            text = message.get("content", {}).get("text", "")
            if not text:
                continue
            role = message.get("role", "")
            speaker = "Client asked" if role == "USER" else "We answered"
            lines.append(f"{speaker}: {_truncate(text)}")
    return lines


def _format_records(records: list[dict]) -> list[str]:
    """`retrieve_memories`'s `memoryRecordSummaries` item shape wasn't
    confirmable without a live call against real extracted records (none
    exist yet on a fresh Memory resource) - verified defensively here so an
    unexpected shape degrades to "no semantic context" rather than raising."""
    lines: list[str] = []
    for record in records:
        content = record.get("content")
        if isinstance(content, dict):
            text = content.get("text", "")
        elif isinstance(content, str):
            text = content
        else:
            text = ""
        if text:
            lines.append(f"Recalled: {_truncate(text)}")
    return lines


def read_prior_turns(
    settings: Settings, session_id: str | None, actor_id: str = DEFAULT_ACTOR_ID
) -> str | None:
    """Best-effort context block from this session's prior turns.

    Returns None when memory is disabled, `session_id` is falsy,
    `settings.memory_id` is unset, the read fails, or nothing is found.
    Never raises - a memory read failure must not break the RFP response,
    the same never-crash contract the graph invocation itself already has.
    """
    if settings.effective_memory_backend != "agentcore" or not session_id or not settings.memory_id:
        return None

    client = _client(settings)
    lines: list[str] = []

    try:
        turns = client.get_last_k_turns(
            memory_id=settings.memory_id, actor_id=actor_id, session_id=session_id, k=5
        )
        lines.extend(_format_turns(turns))
    except Exception as exc:  # get_last_k_turns re-raises ClientError, unlike retrieve_memories
        logger.warning("memory_read_failed", error=str(exc), error_type=type(exc).__name__)

    # Secondary, best-effort semantic layer - see module docstring for why
    # this may legitimately find nothing within a short test window.
    if lines:
        try:
            records = client.retrieve_memories(
                memory_id=settings.memory_id,
                namespace=session_id,
                query=lines[-1],
                actor_id=actor_id,
                top_k=3,
            )
            lines.extend(_format_records(records))
        except Exception as exc:
            logger.warning(
                "memory_semantic_retrieve_failed", error=str(exc), error_type=type(exc).__name__
            )

    if not lines:
        return None
    return "Context from earlier in this session:\n" + "\n".join(lines)


def write_turn(
    settings: Settings,
    session_id: str | None,
    question: str,
    response_text: str,
    actor_id: str = DEFAULT_ACTOR_ID,
) -> None:
    """Best-effort write-back of one completed turn. Never raises - same
    never-crash reasoning as `read_prior_turns`."""
    if settings.effective_memory_backend != "agentcore" or not session_id or not settings.memory_id:
        return

    try:
        _client(settings).create_event(
            memory_id=settings.memory_id,
            actor_id=actor_id,
            session_id=session_id,
            messages=[(question, "USER"), (response_text, "ASSISTANT")],
        )
    except Exception as exc:
        logger.warning("memory_write_failed", error=str(exc), error_type=type(exc).__name__)
