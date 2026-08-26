"""Behavioral tests for the plugin's real middleware and tool-hook adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugins.yatima_runtime_integrity import runtime_integrity as integrity


def _agent(
    *,
    model: str = "gpt-5.6-sol",
    route: str = "https://chatgpt.com/backend-api/codex",
    provider: str = "openai-codex",
):
    return SimpleNamespace(
        provider=provider,
        requested_provider=provider,
        model=model,
        model_route=route,
        base_url=route,
        api_mode=(
            "codex_responses"
            if provider == "openai-codex"
            else "chat_completions"
        ),
        session_id="session-hooks",
        client=SimpleNamespace(base_url=route),
    )


def _config() -> dict[str, object]:
    return {
        "enabled": True,
        "role_id": "brain",
        "policy_generation": "policy-v1",
        "role_ceiling": "R4_FRONTIER_JUDGMENT",
        "task_ceiling": "R4_FRONTIER_JUDGMENT",
        "evidence_ceiling": "R4_FRONTIER_JUDGMENT",
        "policy_ceiling": "R4_FRONTIER_JUDGMENT",
        "capabilities": {
            "gpt-5.6-sol": "C2_BOUNDED_SYNTHESIS",
            "gemma3:4b": "C1_ROUTINE_ASSISTANT",
        },
        "tool_requirements": {"write_file": "C2_BOUNDED_SYNTHESIS"},
        "mismatch_action": "PAUSE",
    }


def test_tool_hooks_bind_pre_and_post_receipts_to_the_active_envelope():
    adapter = integrity.RuntimeIntegrityAdapter(config=_config())
    envelope = adapter.bind_agent(_agent(), attempt_id="attempt-1")

    pre = adapter.pre_tool_call(
        tool_name="write_file",
        args={"path": "out.txt", "content": "ok"},
        session_id="session-hooks",
        tool_call_id="tool-1",
        attempt_id="attempt-1",
    )
    post = adapter.post_tool_call(
        tool_name="write_file",
        args={"path": "out.txt", "content": "ok"},
        result={"written": True},
        session_id="session-hooks",
        tool_call_id="tool-1",
        attempt_id="attempt-1",
        status="ok",
    )

    assert envelope.verify()
    assert pre["action"] == "allow"
    assert pre["receipt"]["receipt_kind"] == "tool_call"
    assert post["receipt"]["receipt_kind"] == "tool_result"
    assert pre["receipt"]["envelope_hash"] == envelope.envelope_hash
    assert post["receipt"]["envelope_hash"] == envelope.envelope_hash
    assert adapter.receipts_for_session("session-hooks")


def test_tool_hook_wall_blocks_a_capability_mismatch_and_middleware_filters_swap():
    adapter = integrity.RuntimeIntegrityAdapter(config=_config())
    first_request = {
        "messages": [
            {"role": "system", "content": "stable"},
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "private"},
            {"role": "user", "content": "current"},
        ]
    }
    first = adapter.llm_request_middleware(
        request=first_request,
        session_id="session-hooks",
        api_request_id="attempt-1",
        model="gpt-5.6-sol",
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        api_mode="codex_responses",
        model_route="route-primary",
        runtime_agent=_agent(),
    )
    second = adapter.llm_request_middleware(
        request=first_request,
        session_id="session-hooks",
        api_request_id="attempt-2",
        model="gemma3:4b",
        provider="openai-codex",
        base_url="http://127.0.0.1:11434",
        api_mode="chat_completions",
        model_route="route-fallback",
        runtime_agent=_agent(
            model="gemma3:4b",
            route="http://127.0.0.1:11434",
            provider="ollama",
        ),
    )
    denied = adapter.pre_tool_call(
        tool_name="write_file",
        args={"path": "out.txt"},
        session_id="session-hooks",
        tool_call_id="tool-2",
        attempt_id="attempt-2",
    )

    assert first["changed"] is False
    assert second["changed"] is True
    assert [item["role"] for item in second["request"]["messages"]] == [
        "system",
        "user",
    ]
    assert "private" not in str(second["request"])
    assert "MODEL_OCCUPANT_CHANGE_DETECTED" in second["events"]
    assert denied["action"] == "block"
    assert denied["event"] == "CAPABILITY_MISMATCH"
    assert "PAUSE" in denied["message"]


def test_request_attestation_failure_applies_the_capability_wall():
    config = _config()
    config.pop("policy_generation")
    adapter = integrity.RuntimeIntegrityAdapter(config=config)

    result = adapter.llm_request_callback(
        request={
            "messages": [{"role": "user", "content": "run it"}],
            "tools": [{"type": "function", "function": {"name": "write_file"}}],
            "tool_choice": "auto",
        },
        session_id="session-hooks",
        api_request_id="attempt-1",
        model="gpt-5.6-sol",
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        api_mode="codex_responses",
    )

    assert result is not None
    assert result["blocked"] is True
    assert result["event"] == "RUNTIME_IDENTITY_ATTESTATION_FAILED"
    assert result["request"]["tools"] == []
    assert result["request"]["tool_choice"] == "none"


class _RegistrationContext:
    def __init__(self, settings):
        self.settings = settings
        self.middleware = []
        self.hooks = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_middleware(self, kind, callback):
        self.middleware.append((kind, callback))

    def register_hook(self, hook_name, callback):
        self.hooks.append((hook_name, callback))


def test_registration_is_explicitly_enabled_and_uses_existing_surfaces():
    disabled = _RegistrationContext({"enabled": False})
    assert integrity.register_plugin(disabled) is None
    assert disabled.middleware == []
    assert disabled.hooks == []

    enabled = _RegistrationContext(_config())
    adapter = integrity.register_plugin(enabled)

    assert isinstance(adapter, integrity.RuntimeIntegrityAdapter)
    assert [kind for kind, _ in enabled.middleware] == [
        "llm_request",
        "llm_execution",
    ]
    assert [name for name, _ in enabled.hooks] == [
        "pre_tool_call",
        "post_tool_call",
        "on_session_finalize",
        "on_session_end",
    ]


def test_execution_middleware_rechecks_live_transport_before_provider_call():
    from hermes_cli.middleware import MiddlewareExecutionBlocked

    context = _RegistrationContext(_config())
    adapter = integrity.register_plugin(context)
    assert adapter is not None
    middleware = dict(context.middleware)
    agent = _agent()
    prepared = middleware["llm_request"](
        request={
            "model": agent.model,
            "messages": [{"role": "user", "content": "run"}],
        },
        session_id=agent.session_id,
        api_request_id="attempt-live",
        runtime_agent=agent,
    )
    provider_calls = []

    agent.client.base_url = "https://other.example/v1"
    with pytest.raises(MiddlewareExecutionBlocked):
        middleware["llm_execution"](
            request=prepared["request"],
            next_call=lambda request: provider_calls.append(request),
            session_id=agent.session_id,
            api_request_id="attempt-live",
            runtime_agent=agent,
        )
    assert provider_calls == []

    agent.client.base_url = agent.base_url
    with pytest.raises(MiddlewareExecutionBlocked):
        middleware["llm_execution"](
            request={**prepared["request"], "model": "forged-model"},
            next_call=lambda request: provider_calls.append(request),
            session_id=agent.session_id,
            api_request_id="attempt-live",
            runtime_agent=agent,
        )
    assert provider_calls == []

    result = middleware["llm_execution"](
        request=prepared["request"],
        next_call=lambda request: {"executed": request["model"]},
        session_id=agent.session_id,
        api_request_id="attempt-live",
        runtime_agent=agent,
    )
    assert result == {"executed": "gpt-5.6-sol"}
