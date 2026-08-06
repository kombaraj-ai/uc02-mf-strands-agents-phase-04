"""Unit tests for `QualGroundingHookProvider`.

Constructs real Strands hook-event dataclasses directly (not through a live
Agent invocation) so this exercises the grounding-enforcement logic without
any model or Ollama call - same isolation-first approach as
`test_compliance_agent_retry.py`.
"""

from __future__ import annotations

from strands.agent.agent_result import AgentResult
from strands.hooks.events import AfterInvocationEvent, AfterToolCallEvent, BeforeInvocationEvent

from amc_orchestrator.observability.hooks import QualGroundingHookProvider


def _tool_result(text: str) -> dict:
    return {"content": [{"text": text}], "status": "success", "toolUseId": "t1"}


def _agent_result(text: str) -> AgentResult:
    return AgentResult(
        stop_reason="end_turn",
        message={"role": "assistant", "content": [{"text": text}]},
        metrics=None,
        state=None,
    )


def test_forces_honest_response_when_every_search_returns_the_sentinel() -> None:
    provider = QualGroundingHookProvider()
    agent = object()

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent))
    provider._on_after_tool_call(
        AfterToolCallEvent(
            agent=agent,
            selected_tool=None,
            tool_use={"name": "search_fund_commentary"},
            invocation_state={},
            result=_tool_result(QualGroundingHookProvider.NOT_FOUND_SENTINEL),
        )
    )
    result = _agent_result("A fabricated but fluent narrative about strategy.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent, result=result))

    assert result.message["content"] == [{"text": QualGroundingHookProvider.NO_COMMENTARY_RESPONSE}]


def test_leaves_response_untouched_when_real_commentary_was_found() -> None:
    provider = QualGroundingHookProvider()
    agent = object()

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent))
    provider._on_after_tool_call(
        AfterToolCallEvent(
            agent=agent,
            selected_tool=None,
            tool_use={"name": "search_fund_commentary"},
            invocation_state={},
            result=_tool_result("Real retrieved commentary about the fund."),
        )
    )
    result = _agent_result("A narrative grounded in the retrieved commentary.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent, result=result))

    assert result.message["content"] == [{"text": "A narrative grounded in the retrieved commentary."}]


def test_leaves_response_untouched_when_tool_never_called() -> None:
    provider = QualGroundingHookProvider()
    agent = object()

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent))
    result = _agent_result("Some response with no tool call at all.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent, result=result))

    assert result.message["content"] == [{"text": "Some response with no tool call at all."}]


def test_ignores_unrelated_tool_calls() -> None:
    provider = QualGroundingHookProvider()
    agent = object()

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent))
    provider._on_after_tool_call(
        AfterToolCallEvent(
            agent=agent,
            selected_tool=None,
            tool_use={"name": "some_other_tool"},
            invocation_state={},
            result=_tool_result("irrelevant"),
        )
    )
    result = _agent_result("Untouched response.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent, result=result))

    assert result.message["content"] == [{"text": "Untouched response."}]


def test_multiple_calls_forces_honest_response_only_if_none_found() -> None:
    provider = QualGroundingHookProvider()
    agent = object()

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent))
    for _ in range(2):
        provider._on_after_tool_call(
            AfterToolCallEvent(
                agent=agent,
                selected_tool=None,
                tool_use={"name": "search_fund_commentary"},
                invocation_state={},
                result=_tool_result(QualGroundingHookProvider.NOT_FOUND_SENTINEL),
            )
        )
    provider._on_after_tool_call(
        AfterToolCallEvent(
            agent=agent,
            selected_tool=None,
            tool_use={"name": "search_fund_commentary"},
            invocation_state={},
            result=_tool_result("Real commentary for the third fund."),
        )
    )
    result = _agent_result("Narrative covering all three funds.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent, result=result))

    # At least one call found real commentary, so the response is left to
    # the model's own prompt adherence (see the hook's docstring).
    assert result.message["content"] == [{"text": "Narrative covering all three funds."}]


def test_state_does_not_leak_between_separate_agents() -> None:
    provider = QualGroundingHookProvider()
    agent_a = object()
    agent_b = object()

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent_a))
    provider._on_after_tool_call(
        AfterToolCallEvent(
            agent=agent_a,
            selected_tool=None,
            tool_use={"name": "search_fund_commentary"},
            invocation_state={},
            result=_tool_result("Real commentary for agent A."),
        )
    )

    provider._on_before_invocation(BeforeInvocationEvent(agent=agent_b))
    provider._on_after_tool_call(
        AfterToolCallEvent(
            agent=agent_b,
            selected_tool=None,
            tool_use={"name": "search_fund_commentary"},
            invocation_state={},
            result=_tool_result(QualGroundingHookProvider.NOT_FOUND_SENTINEL),
        )
    )

    result_a = _agent_result("Response for A.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent_a, result=result_a))
    result_b = _agent_result("Fabricated response for B.")
    provider._on_after_invocation(AfterInvocationEvent(agent=agent_b, result=result_b))

    assert result_a.message["content"] == [{"text": "Response for A."}]
    assert result_b.message["content"] == [{"text": QualGroundingHookProvider.NO_COMMENTARY_RESPONSE}]
