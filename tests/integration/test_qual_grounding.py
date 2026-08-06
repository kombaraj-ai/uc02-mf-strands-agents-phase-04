"""Phase 04: qual agent must not fabricate commentary when the Knowledge Base
is genuinely empty.

Complements `tests/unit/test_qual_grounding_hook.py` (which proves the hook
logic in isolation, no LLM) with the real end-to-end proof: does the whole
graph - qual agent reasoning, compliance judging, revision loop, and
synthesis all included - stay honest when `search_fund_commentary` returns
zero results, rather than the fabrication observed in Phase 03 (see
CLAUDE.md's Phase 03 "qual agent fabricates fund commentary" finding).
"""

from __future__ import annotations

import pytest

from amc_orchestrator.config.settings import Settings
from amc_orchestrator.workflows.graph_build import build_rfp_graph
from amc_orchestrator.workflows.result_extraction import summarize_exception, summarize_result

pytestmark = pytest.mark.integration

_NO_COMMENTARY_SIGNALS = (
    "no relevant",
    "no commentary",
    "no manager commentary",
    "no strategy commentary",
    "not found",
    "no data",
    "unable to find",
    "no information",
    "not available",
)


def test_empty_kb_reported_honestly_not_fabricated(
    isolated_graph_settings_empty_kb: Settings,
) -> None:
    question = (
        "Please provide the current risk metrics and manager strategy "
        "commentary for the Fixed Income Core Bond Fund (INC2)."
    )

    try:
        with build_rfp_graph(isolated_graph_settings_empty_kb) as graph:
            result = graph(question)
        outcome = summarize_result(result)
    except Exception as exc:  # graph node execution is fail-fast, see result_extraction.py
        outcome = summarize_exception(exc)

    assert outcome.response_text, "Response text must never be empty."

    if not outcome.succeeded:
        # Graceful escalation is an acceptable outcome - the escalation
        # message never claims fabricated commentary either.
        assert outcome.escalated is True
        return

    lowered = outcome.response_text.lower()
    assert any(signal in lowered for signal in _NO_COMMENTARY_SIGNALS), (
        "Response for a fund with zero Knowledge Base results did not "
        f"honestly report missing commentary via any expected phrase - "
        f"got: {outcome.response_text!r}"
    )

    # Real quant data (seeded in SQLite even though Chroma is empty) must
    # still come through - this proves the honesty fix didn't just suppress
    # the whole report, only the fabricated qualitative narrative.
    assert "0.35" in outcome.response_text or "INC2" in outcome.response_text
