"""Real AgentCore Gateway target handler for the quant/qual tool Lambdas.

Dispatches on the TOOL_NAME env var (set per-function in main.tf) to either
the quant (DynamoDB) or qual (Bedrock Knowledge Base) lookup. Mirrors the
in-process tools' exact output shape - amc_orchestrator/tools/quant_tools.py's
JSON error sentinel and amc_orchestrator/tools/qual_tools.py's "not found"
text - so QualGroundingHookProvider (observability/hooks.py) keeps working
identically regardless of which tool-routing backend an agent is using.

The Gateway/MCP invocation's exact `event` shape (top-level keys vs. nested
under an "input"/"arguments" key) was not confirmed by static research - this
is the one genuinely uncertain piece of this workstream. Parses defensively
(checks a flat shape first, then common nested shapes) and logs the raw event
so the real shape can be read from CloudWatch on the first live invocation and
this can be tightened afterward.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from knowledge_base_lookup import search_commentary

from amc_orchestrator.data.dynamodb_store import fetch_fund_performance

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Must match tools/qual_tools.py's exact sentinel - QualGroundingHookProvider
# pattern-matches on this literal string.
QUAL_NOT_FOUND_TEXT = "No relevant fund manager commentary found for this query."


def _extract_arg(event: dict[str, Any], name: str) -> Any:
    """Best-effort extraction of a declared input property from the Gateway's
    invocation event, checking a flat shape first, then common nested shapes."""
    if name in event:
        return event[name]
    for key in ("input", "arguments", "parameters"):
        nested = event.get(key)
        if isinstance(nested, dict) and name in nested:
            return nested[name]
    return None


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    logger.info("gateway_tool_invocation tool_name=%s event=%s", os.environ.get("TOOL_NAME"), event)

    tool_name = os.environ.get("TOOL_NAME", "unknown-tool")

    if tool_name == "quant-tools":
        ticker = _extract_arg(event, "ticker")
        row = fetch_fund_performance(os.environ["DYNAMODB_TABLE_NAME"], ticker)
        if row is None:
            result = json.dumps({"error": f"No performance data found for ticker '{ticker}'."})
        else:
            result = json.dumps(row)
        return {"result": result}

    if tool_name == "qual-tools":
        query = _extract_arg(event, "query")
        passages = search_commentary(
            os.environ["BEDROCK_KNOWLEDGE_BASE_ID"], os.environ["AWS_REGION"], query, n_results=2
        )
        result = "\n\n".join(passages) if passages else QUAL_NOT_FOUND_TEXT
        return {"result": result}

    return {"result": json.dumps({"error": f"Unrecognized TOOL_NAME '{tool_name}'"})}
