"""Qualitative fund-manager commentary lookup for the qual-tools Lambda.

A trimmed, Lambda-runtime-safe copy of
amc_orchestrator/data/knowledge_base_store.py::search_commentary - the real
file also imports structlog (for one log line inside ensure_seeded, which
this Lambda never calls) and structlog isn't present in the default Lambda
Python runtime, so vendoring a small copy avoids adding a pip-install build
step just to drop an unused import. Keep this in sync with the real
search_commentary if its retrieval logic ever changes.
"""

from __future__ import annotations

import boto3


def _client(region: str):
    return boto3.client("bedrock-agent-runtime", region_name=region)


def search_commentary(
    knowledge_base_id: str, region: str, query: str, n_results: int = 2
) -> list[str]:
    """Return up to `n_results` best-first passage texts, or `[]` if none found."""
    response = _client(region).retrieve(
        knowledgeBaseId=knowledge_base_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": n_results}},
    )
    results = response.get("retrievalResults", [])
    return [r["content"]["text"] for r in results if r.get("content", {}).get("text")]
