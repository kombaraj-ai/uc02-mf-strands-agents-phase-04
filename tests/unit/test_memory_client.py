"""Unit tests for the WS9 AgentCore Memory client wrapper.

Mocks the AWS-facing boundary (`MemoryClient` itself, patched at the
`agentcore_memory_client` module's own namespace - it does
`from bedrock_agentcore.memory.client import MemoryClient`) the same way
`test_gateway_client.py` mocks SigV4 signing - no real network call, no
real Memory resource needed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from amc_orchestrator.config.settings import Settings
from amc_orchestrator.memory.agentcore_memory_client import (
    DEFAULT_ACTOR_ID,
    read_prior_turns,
    write_turn,
)

_ENABLED = Settings(memory_backend="agentcore", memory_id="test-memory-id", aws_region="us-east-1")
_DISABLED = Settings(memory_backend="disabled", memory_id="test-memory-id", aws_region="us-east-1")


@pytest.fixture
def mock_memory_client():
    with patch("amc_orchestrator.memory.agentcore_memory_client.MemoryClient") as mock_cls:
        yield mock_cls, mock_cls.return_value


def test_read_prior_turns_none_when_memory_disabled(mock_memory_client) -> None:
    mock_cls, _ = mock_memory_client
    assert read_prior_turns(_DISABLED, "session-1") is None
    mock_cls.assert_not_called()


def test_read_prior_turns_none_when_no_session_id(mock_memory_client) -> None:
    mock_cls, _ = mock_memory_client
    assert read_prior_turns(_ENABLED, "") is None
    mock_cls.assert_not_called()


def test_read_prior_turns_none_when_no_memory_id(mock_memory_client) -> None:
    mock_cls, _ = mock_memory_client
    settings = Settings(memory_backend="agentcore", memory_id="", aws_region="us-east-1")
    assert read_prior_turns(settings, "session-1") is None
    mock_cls.assert_not_called()


def test_read_prior_turns_formats_turns_into_context_block(mock_memory_client) -> None:
    _, instance = mock_memory_client
    instance.get_last_k_turns.return_value = [
        [
            {"content": {"text": "What is the risk profile of INC2?"}, "role": "USER"},
            {"content": {"text": "INC2 has a Beta of 0.35."}, "role": "ASSISTANT"},
        ]
    ]
    instance.retrieve_memories.return_value = []

    result = read_prior_turns(_ENABLED, "session-1")

    assert result is not None
    assert "Context from earlier in this session:" in result
    assert "Client asked: What is the risk profile of INC2?" in result
    assert "We answered: INC2 has a Beta of 0.35." in result
    instance.get_last_k_turns.assert_called_once_with(
        memory_id="test-memory-id", actor_id=DEFAULT_ACTOR_ID, session_id="session-1", k=5
    )


def test_read_prior_turns_appends_semantic_records(mock_memory_client) -> None:
    _, instance = mock_memory_client
    instance.get_last_k_turns.return_value = [
        [{"content": {"text": "Tell me about INC2"}, "role": "USER"}],
    ]
    instance.retrieve_memories.return_value = [
        {"content": {"text": "INC2 is a conservative bond fund."}}
    ]

    result = read_prior_turns(_ENABLED, "session-1")

    assert result is not None
    assert "Recalled: INC2 is a conservative bond fund." in result


def test_read_prior_turns_swallows_get_last_k_turns_exception(mock_memory_client) -> None:
    _, instance = mock_memory_client
    instance.get_last_k_turns.side_effect = RuntimeError("boom")

    result = read_prior_turns(_ENABLED, "session-1")

    assert result is None
    instance.retrieve_memories.assert_not_called()


def test_read_prior_turns_swallows_retrieve_memories_exception(mock_memory_client) -> None:
    _, instance = mock_memory_client
    instance.get_last_k_turns.return_value = [
        [{"content": {"text": "Tell me about INC2"}, "role": "USER"}],
    ]
    instance.retrieve_memories.side_effect = RuntimeError("boom")

    result = read_prior_turns(_ENABLED, "session-1")

    # Turn-based context still comes through even though the semantic layer failed.
    assert result is not None
    assert "Client asked: Tell me about INC2" in result


def test_read_prior_turns_none_when_nothing_found(mock_memory_client) -> None:
    _, instance = mock_memory_client
    instance.get_last_k_turns.return_value = []
    instance.retrieve_memories.return_value = []

    assert read_prior_turns(_ENABLED, "session-1") is None


def test_write_turn_calls_create_event_with_expected_shape(mock_memory_client) -> None:
    _, instance = mock_memory_client

    write_turn(_ENABLED, "session-1", "What is INC2's Beta?", "INC2's Beta is 0.35.")

    instance.create_event.assert_called_once_with(
        memory_id="test-memory-id",
        actor_id=DEFAULT_ACTOR_ID,
        session_id="session-1",
        messages=[("What is INC2's Beta?", "USER"), ("INC2's Beta is 0.35.", "ASSISTANT")],
    )


def test_write_turn_noop_when_memory_disabled(mock_memory_client) -> None:
    mock_cls, _ = mock_memory_client
    write_turn(_DISABLED, "session-1", "question", "response")
    mock_cls.assert_not_called()


def test_write_turn_noop_when_no_session_id(mock_memory_client) -> None:
    mock_cls, _ = mock_memory_client
    write_turn(_ENABLED, "", "question", "response")
    mock_cls.assert_not_called()


def test_write_turn_swallows_create_event_exception(mock_memory_client) -> None:
    _, instance = mock_memory_client
    instance.create_event.side_effect = RuntimeError("boom")

    write_turn(_ENABLED, "session-1", "question", "response")  # must not raise
