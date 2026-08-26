"""Windows identity compatibility contract for the plugin import path."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import windows_runtime_identity as canonical
from plugins.yatima_runtime_integrity import windows_runtime_identity as adapter


def _agent():
    return SimpleNamespace(
        provider="openai-codex",
        model="gpt-5.6-sol",
        base_url="https://chatgpt.com/backend-api/codex",
        api_mode="codex_responses",
        model_self_report="ollama/gemma3:4b",
        client=SimpleNamespace(
            base_url="https://chatgpt.com/backend-api/codex"
        ),
    )


def test_windows_readback_is_canonical_fail_closed_and_model_report_neutral():
    assert adapter.RuntimeReadbackError is canonical.RuntimeReadbackError
    assert adapter.WindowsRuntimeReadback is canonical.WindowsRuntimeReadback
    assert adapter.WindowsRuntimeIdentityAdapter is canonical.WindowsRuntimeIdentityAdapter

    with pytest.raises(adapter.RuntimeReadbackError):
        adapter.WindowsRuntimeIdentityAdapter(
            config={"runtime_integrity": {"role_id": "brain"}},
            host="windows-test-host",
        ).read_agent(_agent())

    readback = adapter.WindowsRuntimeIdentityAdapter(
        config={
            "runtime_integrity": {
                "role_id": "brain",
                "policy_generation": "policy-v1",
                "model_capabilities": {
                    "gpt-5.6-sol": "R4_FRONTIER_JUDGMENT"
                },
            }
        },
        host="windows-test-host",
    ).read_agent(_agent())

    assert readback.attested is True
    assert readback.role_id == "brain"
    assert readback.provider == "openai-codex"
    assert readback.exact_model_id == "gpt-5.6-sol"
    assert "gemma" not in readback.canonical_json().lower()
