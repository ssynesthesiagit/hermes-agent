"""Contract tests for the plugin's canonical boundary re-exports."""

from __future__ import annotations

from agent import runtime_integrity as canonical
from plugins.yatima_runtime_integrity import runtime_integrity as adapter


def _envelope(*, provider: str, model: str, route: str, attempt: str, tier: str):
    return adapter.RuntimeEnvelope.create(
        role_id="brain",
        host="windows-host",
        provider=provider,
        exact_model_id=model,
        model_route=route,
        policy_generation="policy-v1",
        session_id="session-1",
        attempt_id=attempt,
        capability_tier=tier,
        role_ceiling="R4_FRONTIER_JUDGMENT",
        task_ceiling="R4_FRONTIER_JUDGMENT",
        evidence_ceiling="R4_FRONTIER_JUDGMENT",
        policy_ceiling="R4_FRONTIER_JUDGMENT",
    )


def test_boundary_policy_is_canonical_and_filters_only_the_outgoing_copy():
    assert adapter.RuntimeEnvelope is canonical.RuntimeEnvelope
    assert adapter.VerifiedHandoffState is canonical.VerifiedHandoffState
    assert adapter.detect_occupant_change is canonical.detect_occupant_change
    assert adapter.filter_outgoing_request is canonical.filter_outgoing_request

    previous = _envelope(
        provider="openai-codex",
        model="gpt-5.6-sol",
        route="https://chatgpt.com/backend-api/codex",
        attempt="attempt-1",
        tier="C2_BOUNDED_SYNTHESIS",
    )
    current = _envelope(
        provider="ollama",
        model="gemma3:4b",
        route="http://127.0.0.1:11434",
        attempt="attempt-2",
        tier="C1_ROUTINE_ASSISTANT",
    )
    transition = adapter.detect_occupant_change(
        previous,
        current,
        adapter.VerifiedHandoffState(
            predecessor_conclusions=("verified predecessor conclusion",),
            receipt_refs=("sha256:tool-receipt",),
        ),
    )
    request = {
        "messages": [
            {"role": "system", "content": "stable system prompt"},
            {"role": "user", "content": "old user"},
            {"role": "assistant", "content": "private predecessor chat"},
            {"role": "user", "content": "current user"},
        ]
    }

    filtered = adapter.filter_outgoing_request(request, transition)

    assert request["messages"][-1]["content"] == "current user"
    assert [message["role"] for message in filtered["messages"]] == [
        "system",
        "user",
    ]
    assert "private predecessor chat" not in str(filtered)
    assert "verified predecessor conclusion" in filtered["messages"][-1]["content"]
    assert transition.capability_clamp_active is True
    assert transition.handoff_capsule["raw_predecessor_chat_included"] is False
    assert transition.handoff_capsule["attributed_predecessor_conclusions"][0][
        "origin_envelope_sha256"
    ] == previous.envelope_sha256
