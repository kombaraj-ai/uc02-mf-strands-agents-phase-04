"""Unit tests for the real WS8 AgentCore Gateway target Lambda handler.

`infra/terraform/modules/lambda-tools/src/` lives outside the normal
`src/amc_orchestrator` package tree (it's Lambda-deployment source, packaged
by Terraform's archive_file, not part of the app's own package) - its
`sys.path` entry is added here rather than via a shared conftest fixture,
since this is the only test module that needs it.

Mocks `fetch_fund_performance`/`search_commentary` the same way test_tools.py
mocks `get_settings` - by patching the names `handler.py` imported directly
into its own namespace (`from X import Y`), not the source modules.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_LAMBDA_SRC = (
    Path(__file__).resolve().parents[2] / "infra" / "terraform" / "modules" / "lambda-tools" / "src"
)
if str(_LAMBDA_SRC) not in sys.path:
    sys.path.insert(0, str(_LAMBDA_SRC))

import handler  # noqa: E402


@pytest.fixture(autouse=True)
def _lambda_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test-table")
    monkeypatch.setenv("BEDROCK_KNOWLEDGE_BASE_ID", "test-kb-id")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


def test_quant_tool_returns_metrics_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_NAME", "quant-tools")
    monkeypatch.setattr(
        handler,
        "fetch_fund_performance",
        lambda table_name, ticker: {"ticker": ticker, "nav": 52.1},
    )

    response = handler.handler({"ticker": "INC2"}, None)

    assert json.loads(response["result"]) == {"ticker": "INC2", "nav": 52.1}


def test_quant_tool_unknown_ticker_returns_error_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_NAME", "quant-tools")
    monkeypatch.setattr(handler, "fetch_fund_performance", lambda table_name, ticker: None)

    response = handler.handler({"ticker": "ZZZZ"}, None)

    assert json.loads(response["result"]) == {
        "error": "No performance data found for ticker 'ZZZZ'."
    }


def test_quant_tool_accepts_nested_input_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_NAME", "quant-tools")
    monkeypatch.setattr(
        handler, "fetch_fund_performance", lambda table_name, ticker: {"ticker": ticker}
    )

    response = handler.handler({"input": {"ticker": "SMC3"}}, None)

    assert json.loads(response["result"]) == {"ticker": "SMC3"}


def test_qual_tool_returns_joined_passages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_NAME", "qual-tools")
    monkeypatch.setattr(
        handler,
        "search_commentary",
        lambda kb_id, region, query, n_results=2: ["passage one", "passage two"],
    )

    response = handler.handler({"query": "smallcap volatility"}, None)

    assert response["result"] == "passage one\n\npassage two"


def test_qual_tool_no_results_returns_exact_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Must match the sentinel character-for-character - QualGroundingHookProvider
    (observability/hooks.py) pattern-matches on this exact string."""
    monkeypatch.setenv("TOOL_NAME", "qual-tools")
    monkeypatch.setattr(handler, "search_commentary", lambda kb_id, region, query, n_results=2: [])

    response = handler.handler({"query": "anything"}, None)

    assert response["result"] == "No relevant fund manager commentary found for this query."


def test_unrecognized_tool_name_returns_error_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_NAME", "some-other-tool")

    response = handler.handler({}, None)

    assert json.loads(response["result"]) == {"error": "Unrecognized TOOL_NAME 'some-other-tool'"}
