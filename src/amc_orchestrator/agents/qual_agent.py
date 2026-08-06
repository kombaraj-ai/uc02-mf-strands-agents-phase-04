"""Qualitative Strategy Agent - retrieves fund manager narrative/commentary."""

from __future__ import annotations

from typing import Any

from strands import Agent

from amc_orchestrator.config.model_factory import get_model
from amc_orchestrator.config.settings import Settings
from amc_orchestrator.observability.hooks import LoggingHookProvider, QualGroundingHookProvider
from amc_orchestrator.tools.qual_tools import search_fund_commentary

NODE_NAME = "qual_narrative_pull"

SYSTEM_PROMPT = """\
You are the Qualitative Strategy Agent for a Mutual Fund AMC.

Your job is to search historical fund manager commentary and macroeconomic
outlook using the `search_fund_commentary` tool, then synthesize what it
returns into a clear narrative.

Rules:
- Search for commentary on every fund mentioned or implied in the request.
- Synthesize the retrieved text professionally, but never invent strategic
  positions, opinions, or outlooks that were not returned by the tool.
- If no relevant commentary is found for a fund, state that plainly.
- Do not report numerical performance metrics or comment on compliance -
  that is not your job.
"""


def get_qual_agent(settings: Settings, tools: list[Any] | None = None) -> Agent:
    """Build the Qualitative Strategy Agent for the given environment settings.

    `tools` lets the caller (workflows.graph_build) supply the Gateway-routed
    `search_fund_commentary` MCP tool instead of the in-process default, when
    `settings.effective_tool_backend == "gateway"`. Both backends are
    permanent, not a migration - in-process is what makes local dev without
    any AWS Gateway dependency possible at all, and is what the fast, no-AWS
    unit tests exercise. Defaults to the in-process tool when not given.
    """
    model = get_model(settings, temperature=settings.model_temperature_worker)
    return Agent(
        model=model,
        tools=tools if tools is not None else [search_fund_commentary],
        system_prompt=SYSTEM_PROMPT,
        name=NODE_NAME,
        hooks=[LoggingHookProvider(NODE_NAME), QualGroundingHookProvider()],
    )
