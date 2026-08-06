"""Unit tests for `GatewayPolicyDenialHookProvider`.

Constructs real Strands hook-event dataclasses directly (not through a live
Agent invocation), same isolation-first approach as
`test_qual_grounding_hook.py` - no model, no Ollama, no AWS call needed.
"""

from __future__ import annotations

from strands.agent.agent_result import AgentResult
from strands.hooks.events import AfterInvocationEvent, AfterToolCallEvent, BeforeInvocationEvent

from amc_orchestrator.observability.hooks import GatewayPolicyDenialHookProvider

_HONEST_RESPONSE = "No data was retrieved - the tool call was denied by policy."
_DENIAL_TEXT = (
    "AuthorizeActionException - Tool Execution Denied: Tool call not allowed due to "
    "policy enforcement [No policy applies to the request (denied by default).]"
)


def _tool_result(text: str, *, status: str) -> dict:
    return {"content": [{"text": text}], "status": status, "toolUseId": "t1"}


def _agent_result(text: str) -> AgentResult:
    return AgentResult(
        stop_reason="end_turn",
        message={"role": "assistant", "content": [{"text": text}]},
        metrics=None,
        state=None,
    )


def test_forces_honest_response_when_a_tool_call_is_policy_denied() -> None:
    provider = GatewayPolicyDenialHookProvider("quant_data_pull", _HONEST_RESPONSE)
    agent = object()

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent))
    provider._on_after_tool_call(
        AfterToolCallEvent(
            agent=agent,
            selected_tool=None,
            tool_use={"name": "get_fund_performance"},
            invocation_state={},
            result=_tool_result(_DENIAL_TEXT, status="error"),
        )
    )
    result = _agent_result("A fabricated but fluent answer despite the denial.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent, result=result))

    assert result.message["content"] == [{"text": _HONEST_RESPONSE}]


def test_leaves_response_untouched_on_a_successful_tool_call() -> None:
    provider = GatewayPolicyDenialHookProvider("quant_data_pull", _HONEST_RESPONSE)
    agent = object()

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent))
    provider._on_after_tool_call(
        AfterToolCallEvent(
            agent=agent,
            selected_tool=None,
            tool_use={"name": "get_fund_performance"},
            invocation_state={},
            result=_tool_result('{"nav": 52.1}', status="success"),
        )
    )
    result = _agent_result("Real answer using the retrieved data.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent, result=result))

    assert result.message["content"] == [{"text": "Real answer using the retrieved data."}]


def test_leaves_response_untouched_on_a_non_policy_tool_error() -> None:
    """An ordinary tool failure (e.g. a Lambda exception) should not trip
    this hook - only the specific policy-denial signature should."""
    provider = GatewayPolicyDenialHookProvider("quant_data_pull", _HONEST_RESPONSE)
    agent = object()

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent))
    provider._on_after_tool_call(
        AfterToolCallEvent(
            agent=agent,
            selected_tool=None,
            tool_use={"name": "get_fund_performance"},
            invocation_state={},
            result=_tool_result("Internal server error", status="error"),
        )
    )
    result = _agent_result("Response after an unrelated tool error.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent, result=result))

    assert result.message["content"] == [{"text": "Response after an unrelated tool error."}]


def test_leaves_response_untouched_when_no_tool_called() -> None:
    provider = GatewayPolicyDenialHookProvider("quant_data_pull", _HONEST_RESPONSE)
    agent = object()

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent))
    result = _agent_result("Some response with no tool call at all.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent, result=result))

    assert result.message["content"] == [{"text": "Some response with no tool call at all."}]


def test_state_does_not_leak_between_separate_agents() -> None:
    provider = GatewayPolicyDenialHookProvider("quant_data_pull", _HONEST_RESPONSE)
    agent_a = object()
    agent_b = object()

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent_a))
    provider._on_after_tool_call(
        AfterToolCallEvent(
            agent=agent_a,
            selected_tool=None,
            tool_use={"name": "get_fund_performance"},
            invocation_state={},
            result=_tool_result('{"nav": 52.1}', status="success"),
        )
    )

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent_b))
    provider._on_after_tool_call(
        AfterToolCallEvent(
            agent=agent_b,
            selected_tool=None,
            tool_use={"name": "get_fund_performance"},
            invocation_state={},
            result=_tool_result(_DENIAL_TEXT, status="error"),
        )
    )

    result_a = _agent_result("Response for A.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent_a, result=result_a))
    result_b = _agent_result("Fabricated response for B despite the denial.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent_b, result=result_b))

    assert result_a.message["content"] == [{"text": "Response for A."}]
    assert result_b.message["content"] == [{"text": _HONEST_RESPONSE}]
