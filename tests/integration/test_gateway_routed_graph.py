"""End-to-end proof that WS8's Gateway-routed tool backend actually reaches
the real AgentCore Gateway + Lambda, not just that the graph runs.

Needs a genuinely deployed dev Gateway (GATEWAY_URL + real AWS credentials) -
skip-guarded via `isolated_graph_settings_gateway_backend`, since there is no
local stand-in for AWS_IAM/SigV4-authenticated MCP the way Ollama has a local
server. A successful graph run alone isn't sufficient proof: a bug in the
`effective_tool_backend` branch could silently fall back to the in-process
tools and this test would still pass - so this also confirms via CloudWatch
Logs that the quant/qual Lambdas were actually invoked during the run.
"""

from __future__ import annotations

import time

import boto3
import pytest

from amc_orchestrator.config.messages import ESCALATION_HOLDING_MESSAGE
from amc_orchestrator.config.settings import Settings
from amc_orchestrator.workflows.graph_build import build_rfp_graph
from amc_orchestrator.workflows.result_extraction import summarize_exception, summarize_result

pytestmark = pytest.mark.integration

# Matches infra/terraform/environments/*/locals.tf's
# name_prefix = "${var.project}-${var.environment}" and
# modules/lambda-tools/main.tf's per-function log group naming.
_PROJECT = "amc-orchestrator"


def _lambda_log_group(settings: Settings, tool_name: str) -> str:
    return f"/aws/lambda/{_PROJECT}-{settings.environment}-{tool_name}"


def _lambda_invoked_recently(log_group: str, since_epoch_ms: int) -> bool:
    logs = boto3.client("logs", region_name="us-east-1")
    response = logs.filter_log_events(
        logGroupName=log_group,
        startTime=since_epoch_ms,
        filterPattern='"gateway_tool_invocation"',
    )
    return len(response.get("events", [])) > 0


def test_gateway_routed_query_actually_invokes_the_real_lambdas(
    isolated_graph_settings_gateway_backend: Settings,
) -> None:
    settings = isolated_graph_settings_gateway_backend
    question = (
        "Please provide the current risk metrics for the Fixed Income Core "
        "Bond Fund (INC2) and its current macroeconomic strategy."
    )
    start_ms = int(time.time() * 1000)

    try:
        with build_rfp_graph(settings) as graph:
            result = graph(question)
        outcome = summarize_result(result)
    except Exception as exc:  # graph node execution is fail-fast, see result_extraction.py
        outcome = summarize_exception(exc)

    # Same resilience contract as every other end-to-end test - never a raw
    # crash, always either a real compliant completion or the safe escalation.
    if outcome.succeeded:
        assert outcome.graph_status == "completed"
    else:
        assert outcome.escalated is True
        assert outcome.response_text == ESCALATION_HOLDING_MESSAGE

    # The real proof this is Gateway-routed, not a silently-fallen-back
    # in-process run: both Lambdas' CloudWatch logs show a real invocation
    # (handler.py logs "gateway_tool_invocation" on every call) during this run.
    assert _lambda_invoked_recently(_lambda_log_group(settings, "quant-tools"), start_ms), (
        "quant-tools Lambda was not invoked - the graph may have silently used "
        "the in-process tool instead of routing through the Gateway."
    )
    assert _lambda_invoked_recently(_lambda_log_group(settings, "qual-tools"), start_ms), (
        "qual-tools Lambda was not invoked - the graph may have silently used "
        "the in-process tool instead of routing through the Gateway."
    )
