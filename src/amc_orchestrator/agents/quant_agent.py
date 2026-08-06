"""Quantitative Analyst Agent - pulls exact fund performance metrics."""

from __future__ import annotations

from typing import Any

from strands import Agent

from amc_orchestrator.config.model_factory import get_model
from amc_orchestrator.config.settings import Settings
from amc_orchestrator.observability.hooks import (
    GatewayPolicyDenialHookProvider,
    LoggingHookProvider,
)
from amc_orchestrator.tools.quant_tools import get_fund_performance

NODE_NAME = "quant_data_pull"

SYSTEM_PROMPT = """\
You are the Quantitative Analyst Agent for a Mutual Fund AMC.

Your job is to pull exact numerical performance metrics using the
`get_fund_performance` tool: NAV, Alpha, Beta, Sharpe Ratio, Standard
Deviation, Sortino Ratio, R-Squared, and 1-year/3-year trailing returns.

Rules:
- Identify every fund ticker mentioned or implied in the request and call the
  tool for each one.
- Report the values exactly as returned by the tool. Never estimate, round
  beyond what the tool returned, or recall figures from memory.
- If the tool reports no data for a ticker, state that plainly instead of
  guessing.
- If a tool call is denied or blocked by an authorization/compliance policy
  rather than returning data, state that plainly too - do not guess at
  figures and do not repeat the raw error text back verbatim.
- Do not comment on compliance, strategy, or narrative - that is not your job.
"""

_POLICY_DENIAL_RESPONSE = (
    "Quantitative data for this request could not be retrieved - the tool call was "
    "denied by this platform's authorization policy, not because no data exists. "
    "No figures are being reported."
)


def get_quant_agent(settings: Settings, tools: list[Any] | None = None) -> Agent:
    """Build the Quantitative Analyst Agent for the given environment settings.

    `tools` lets the caller (workflows.graph_build) supply the Gateway-routed
    `get_fund_performance` MCP tool instead of the in-process default, when
    `settings.effective_tool_backend == "gateway"`. Both backends are
    permanent, not a migration - in-process is what makes local dev without
    any AWS Gateway dependency possible at all, and is what the fast, no-AWS
    unit tests exercise. Defaults to the in-process tool when not given.
    """
    model = get_model(settings, temperature=settings.model_temperature_worker)
    hooks: list[Any] = [LoggingHookProvider(NODE_NAME)]
    if settings.effective_tool_backend == "gateway":
        # A harmless no-op unless a Policy engine is actually attached to the
        # Gateway in ENFORCE mode - see GatewayPolicyDenialHookProvider's own
        # docstring. Only relevant on the gateway path; the in-process tool
        # never returns a policy-denial result at all.
        hooks.append(GatewayPolicyDenialHookProvider(NODE_NAME, _POLICY_DENIAL_RESPONSE))
    return Agent(
        model=model,
        tools=tools if tools is not None else [get_fund_performance],
        system_prompt=SYSTEM_PROMPT,
        name=NODE_NAME,
        hooks=hooks,
    )
