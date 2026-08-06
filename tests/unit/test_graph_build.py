"""Unit tests for WS8's tool-routing branch in workflows.graph_build and
agents/quant_agent.py, agents/qual_agent.py.

`build_rfp_graph`'s tests mock `gateway_tools` and `_build_graph` (rather than
letting a full Strands Graph/Agent get built with fake tool objects) so this
exercises exactly the branch/filtering logic WS8 added, without needing to
satisfy Strands' own Agent/tool validation with placeholder objects - that
validation is already covered by the full integration suite and the existing
unit tests that build real agents.
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from amc_orchestrator.agents.qual_agent import get_qual_agent
from amc_orchestrator.agents.quant_agent import get_quant_agent
from amc_orchestrator.config.settings import Settings
from amc_orchestrator.tools.qual_tools import search_fund_commentary
from amc_orchestrator.tools.quant_tools import get_fund_performance
from amc_orchestrator.workflows.graph_build import _tools_named, build_rfp_graph


def _fake_mcp_tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(tool_name=name)


def test_tools_named_filters_by_exact_tool_name() -> None:
    tools = [_fake_mcp_tool("get_fund_performance"), _fake_mcp_tool("search_fund_commentary")]

    assert _tools_named(tools, "get_fund_performance") == [tools[0]]
    assert _tools_named(tools, "search_fund_commentary") == [tools[1]]
    assert _tools_named(tools, "unrelated_tool") == []


def test_get_quant_agent_defaults_to_in_process_tool() -> None:
    agent = get_quant_agent(Settings())
    assert agent.tool_names == ["get_fund_performance"]


def test_get_quant_agent_uses_supplied_tools_when_given() -> None:
    agent = get_quant_agent(Settings(), tools=[get_fund_performance])
    assert agent.tool_names == ["get_fund_performance"]


def test_get_qual_agent_defaults_to_in_process_tool() -> None:
    agent = get_qual_agent(Settings())
    assert agent.tool_names == ["search_fund_commentary"]


def test_get_qual_agent_uses_supplied_tools_when_given() -> None:
    agent = get_qual_agent(Settings(), tools=[search_fund_commentary])
    assert agent.tool_names == ["search_fund_commentary"]


def test_build_rfp_graph_skips_gateway_client_for_in_process_backend() -> None:
    settings = Settings(tool_backend="in_process")
    assert settings.effective_tool_backend == "in_process"

    with (
        patch("amc_orchestrator.workflows.graph_build.gateway_tools") as mock_gateway_tools,
        patch(
            "amc_orchestrator.workflows.graph_build._build_graph", return_value="the-graph"
        ) as mock_build,
    ):
        with build_rfp_graph(settings) as graph:
            assert graph == "the-graph"

    mock_gateway_tools.assert_not_called()
    mock_build.assert_called_once_with(settings, quant_tools=None, qual_tools=None)


def test_build_rfp_graph_opens_gateway_client_and_filters_tools_for_gateway_backend() -> None:
    settings = Settings(tool_backend="gateway", gateway_url="https://example.gateway.test/mcp")
    assert settings.effective_tool_backend == "gateway"

    fake_tools = [
        _fake_mcp_tool("get_fund_performance"),
        _fake_mcp_tool("search_fund_commentary"),
        _fake_mcp_tool("some_unrelated_tool"),
    ]

    with (
        patch(
            "amc_orchestrator.workflows.graph_build.gateway_tools",
            return_value=nullcontext(fake_tools),
        ) as mock_gateway_tools,
        patch(
            "amc_orchestrator.workflows.graph_build._build_graph", return_value="the-graph"
        ) as mock_build,
    ):
        with build_rfp_graph(settings) as graph:
            assert graph == "the-graph"

    mock_gateway_tools.assert_called_once_with(settings)
    mock_build.assert_called_once_with(
        settings, quant_tools=[fake_tools[0]], qual_tools=[fake_tools[1]]
    )
