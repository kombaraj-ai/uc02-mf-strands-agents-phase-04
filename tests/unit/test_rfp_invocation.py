"""Unit tests for the WS9 shared `invoke_rfp` helper.

Mocks `build_rfp_graph` (same `nullcontext`-wrapped fake-callable approach as
`test_api_rfp.py`/`test_runtime_entrypoint.py`) and the memory module's
`read_prior_turns`/`write_turn` (already unit-tested for their own internal
behavior in `test_memory_client.py` - here we only need to confirm
`invoke_rfp` calls them with the right arguments and uses their return value
correctly), so no real Ollama/AWS call is needed.
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from amc_orchestrator.config.messages import ESCALATION_HOLDING_MESSAGE
from amc_orchestrator.config.settings import Settings
from amc_orchestrator.workflows.rfp_invocation import invoke_rfp

_SETTINGS = Settings()
_MOD = "amc_orchestrator.workflows.rfp_invocation"


def _fake_graph_result(*, synthesis_text: str, compliance_status: str) -> SimpleNamespace:
    compliance_result = SimpleNamespace(
        result=SimpleNamespace(structured_output=SimpleNamespace(status=compliance_status)),
    )
    synthesis_result = SimpleNamespace(result=synthesis_text)
    execution_order = [SimpleNamespace(node_id="compliance_check")]
    return SimpleNamespace(
        status=SimpleNamespace(value="completed"),
        execution_order=execution_order,
        results={"compliance_check": compliance_result, "final_synthesis": synthesis_result},
    )


def test_invoke_rfp_sends_question_unchanged_when_no_prior_context() -> None:
    fake_result = _fake_graph_result(
        synthesis_text="Compliant answer.", compliance_status="APPROVED"
    )
    captured_questions: list[str] = []

    def fake_graph(question: str) -> SimpleNamespace:
        captured_questions.append(question)
        return fake_result

    with (
        patch(f"{_MOD}.build_rfp_graph", return_value=nullcontext(fake_graph)),
        patch(f"{_MOD}.read_prior_turns", return_value=None) as mock_read,
        patch(f"{_MOD}.write_turn") as mock_write,
    ):
        outcome = invoke_rfp(_SETTINGS, "What is INC2's Beta?", session_id="session-1")

    assert captured_questions == ["What is INC2's Beta?"]
    assert outcome.succeeded is True
    mock_read.assert_called_once_with(_SETTINGS, "session-1")
    mock_write.assert_called_once_with(
        _SETTINGS, "session-1", "What is INC2's Beta?", "Compliant answer."
    )


def test_invoke_rfp_prepends_prior_context_to_graph_question() -> None:
    fake_result = _fake_graph_result(
        synthesis_text="Compliant answer.", compliance_status="APPROVED"
    )
    captured_questions: list[str] = []

    def fake_graph(question: str) -> SimpleNamespace:
        captured_questions.append(question)
        return fake_result

    prior_context = "Context from earlier in this session:\nClient asked: Tell me about INC2"
    with (
        patch(f"{_MOD}.build_rfp_graph", return_value=nullcontext(fake_graph)),
        patch(f"{_MOD}.read_prior_turns", return_value=prior_context),
        patch(f"{_MOD}.write_turn") as mock_write,
    ):
        invoke_rfp(_SETTINGS, "What about its Beta?", session_id="session-1")

    assert captured_questions == [f"{prior_context}\n\nWhat about its Beta?"]
    # The write-back uses the ORIGINAL question, not the context-prepended one.
    mock_write.assert_called_once_with(
        _SETTINGS, "session-1", "What about its Beta?", "Compliant answer."
    )


def test_invoke_rfp_writes_escalation_message_on_exception() -> None:
    def raising_graph(question: str) -> None:
        raise RuntimeError("boom")

    with (
        patch(f"{_MOD}.build_rfp_graph", return_value=nullcontext(raising_graph)),
        patch(f"{_MOD}.read_prior_turns", return_value=None),
        patch(f"{_MOD}.write_turn") as mock_write,
    ):
        outcome = invoke_rfp(_SETTINGS, "What is INC2's Beta?", session_id="session-1")

    assert outcome.succeeded is False
    assert outcome.response_text == ESCALATION_HOLDING_MESSAGE
    mock_write.assert_called_once_with(
        _SETTINGS, "session-1", "What is INC2's Beta?", ESCALATION_HOLDING_MESSAGE
    )


def test_invoke_rfp_without_session_id_still_calls_memory_helpers() -> None:
    """`session_id=None` (the CLI's default, no second argv) - `invoke_rfp`
    doesn't special-case this itself, it relies on read_prior_turns/write_turn's
    own internal falsy-session_id no-op (see test_memory_client.py)."""
    fake_result = _fake_graph_result(
        synthesis_text="Compliant answer.", compliance_status="APPROVED"
    )
    fake_graph = lambda question: fake_result  # noqa: E731

    with (
        patch(f"{_MOD}.build_rfp_graph", return_value=nullcontext(fake_graph)),
        patch(f"{_MOD}.read_prior_turns", return_value=None) as mock_read,
        patch(f"{_MOD}.write_turn") as mock_write,
    ):
        invoke_rfp(_SETTINGS, "What is INC2's Beta?")

    mock_read.assert_called_once_with(_SETTINGS, None)
    mock_write.assert_called_once_with(_SETTINGS, None, "What is INC2's Beta?", "Compliant answer.")
