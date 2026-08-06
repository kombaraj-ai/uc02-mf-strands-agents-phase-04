"""Unit tests for the WS8 SigV4 MCP-Gateway client.

Tests the hand-rolled SigV4 signing adapter in isolation - a fake request,
mocked credentials, no real network call or MCP server - since that's the
one genuinely new piece of auth logic this workstream adds (Strands' MCPClient
has no built-in AWS-auth transport).
"""

from __future__ import annotations

import httpx
import pytest
from botocore.credentials import Credentials

from amc_orchestrator.tools.gateway_client import _SigV4HttpxAuth


@pytest.fixture
def signed_request(monkeypatch: pytest.MonkeyPatch) -> httpx.Request:
    auth = _SigV4HttpxAuth(region="us-east-1")
    monkeypatch.setattr(
        auth._session,
        "get_credentials",
        lambda: Credentials("AKIA_TEST", "secret", "session-token"),
    )

    request = httpx.Request(
        "POST",
        "https://abcdefgh.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
        content=b'{"jsonrpc": "2.0"}',
    )
    flow = auth.auth_flow(request)
    return next(flow)


def test_signs_request_with_sigv4_authorization_header(signed_request: httpx.Request) -> None:
    authorization = signed_request.headers["authorization"]
    assert authorization.startswith("AWS4-HMAC-SHA256")
    # The signing scope must reference the real AgentCore Gateway service name,
    # not e.g. "execute-api" - confirmed via AWS's own docs, see the module docstring.
    assert "/bedrock-agentcore/aws4_request" in authorization


def test_includes_amz_date_and_session_token_headers(signed_request: httpx.Request) -> None:
    assert "x-amz-date" in signed_request.headers
    assert signed_request.headers["x-amz-security-token"] == "session-token"


def test_raises_clear_error_when_no_credentials_available(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _SigV4HttpxAuth(region="us-east-1")
    monkeypatch.setattr(auth._session, "get_credentials", lambda: None)
    request = httpx.Request("POST", "https://example.com/mcp", content=b"{}")

    with pytest.raises(RuntimeError, match="No AWS credentials"):
        next(auth.auth_flow(request))
