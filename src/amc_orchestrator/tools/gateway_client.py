"""SigV4-authenticated MCP client for the real AgentCore Gateway (WS8).

Parallel in spirit to `config.model_factory.get_model` (constructs the right
client for the environment) but for tools: `gateway_tools()` is the only
place that knows how to reach the Gateway, so agents/quant_agent.py and
agents/qual_agent.py never touch SigV4/MCP details themselves.

The Gateway's `authorizer_type = "AWS_IAM"` (see
infra/terraform/modules/agentcore-gateway/main.tf) means every request must
be SigV4-signed - there is no bearer token. Strands' `MCPClient` has no
built-in AWS-auth transport, and no SigV4-httpx integration library is a
project dependency, so `_SigV4HttpxAuth` hand-rolls the signing using
botocore (already a transitive dependency via boto3, used throughout
data/dynamodb_store.py etc.) - the same category of hand-rolled SigV4 call
this project already relied on once before (the Phase 02 dev-teardown
incident's direct SigV4-signed HTTP DELETE against the AOSS collection
endpoint, when no higher-level AWS API existed either).

Callers MUST keep the `with gateway_tools(settings) as tools:` block open for
the entire duration any of the yielded tools might be invoked, not just while
listing them - `MCPAgentTool.stream()` calls back into the *same* MCPClient
session for every tool invocation (confirmed by reading
strands/tools/mcp/mcp_agent_tool.py directly), so closing the client right
after listing would break every subsequent tool call.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import asynccontextmanager, contextmanager

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from mcp.client.streamable_http import streamable_http_client
from strands.tools.mcp import MCPClient
from strands.tools.mcp.mcp_agent_tool import MCPAgentTool

from amc_orchestrator.config.settings import Settings

# The real SigV4 signing name for calls to a Bedrock AgentCore Gateway -
# confirmed via AWS's own docs, not guessed (see this workstream's plan notes).
_SIGV4_SERVICE_NAME = "bedrock-agentcore"


class _SigV4HttpxAuth(httpx.Auth):
    """Signs every outgoing request with the caller's current AWS credentials.

    Re-fetches frozen credentials per request (via a cached `boto3.Session`,
    not a fresh `Session()` each time) so credential rotation - e.g. the
    deployed Runtime's assumed-role temporary credentials - is handled
    correctly across a long-lived connection, not just at construction time.
    """

    def __init__(self, region: str) -> None:
        self._region = region
        self._session = boto3.Session()

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        credentials = self._session.get_credentials()
        if credentials is None:
            raise RuntimeError(
                "No AWS credentials available to sign the AgentCore Gateway request. "
                "Configure credentials (e.g. `aws configure`, an SSO profile, or the "
                "deployed Runtime's assumed role) before using TOOL_BACKEND=gateway."
            )
        frozen = credentials.get_frozen_credentials()

        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=dict(request.headers),
        )
        SigV4Auth(frozen, _SIGV4_SERVICE_NAME, self._region).add_auth(aws_request)
        for key, value in aws_request.headers.items():
            request.headers[key] = value

        yield request


def _sigv4_transport(gateway_url: str, region: str):
    """Zero-arg callable returning an async context manager, matching
    `strands.tools.mcp.mcp_client.MCPTransport`'s expected shape."""

    @asynccontextmanager
    async def _transport():
        # httpx.AsyncClient is created fresh per connection and closed here -
        # streamable_http_client() only manages a client's lifecycle when it
        # creates one itself, never one passed in via http_client= (confirmed
        # by reading mcp/client/streamable_http.py directly), so this context
        # manager is what actually closes it.
        async with httpx.AsyncClient(auth=_SigV4HttpxAuth(region)) as client:
            async with streamable_http_client(gateway_url, http_client=client) as streams:
                yield streams

    return _transport


@contextmanager
def gateway_tools(settings: Settings) -> Generator[list[MCPAgentTool], None, None]:
    """Yield the live Gateway's MCP tools for the duration of the context.

    Must stay open for the whole graph run - see this module's docstring.
    """
    client = MCPClient(_sigv4_transport(settings.gateway_url, settings.aws_region))
    with client:
        yield client.list_tools_sync()
