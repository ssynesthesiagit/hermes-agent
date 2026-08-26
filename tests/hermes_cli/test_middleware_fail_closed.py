"""Behavioral contracts for additive, fail-closed plugin middleware seams."""

from __future__ import annotations

import pytest

from hermes_cli import middleware
from hermes_cli.plugins import PluginManager


def _manager(monkeypatch) -> PluginManager:
    manager = PluginManager()
    manager._discovered = True
    monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", lambda: manager)
    return manager


def test_additive_middleware_context_preserves_narrow_callback_signatures(monkeypatch):
    manager = _manager(monkeypatch)
    observed: list[str] = []

    def request_callback(request):
        observed.append("request")
        return {"request": {**request, "filtered": True}}

    def execution_callback(request, next_call):
        observed.append("execution")
        return next_call({**request, "checked": True})

    manager._middleware["llm_request"] = [request_callback]
    manager._middleware["llm_execution"] = [execution_callback]

    prepared = middleware.apply_llm_request_middleware(
        {"model": "m"}, runtime_agent=object(), future_context="additive"
    )
    executed = middleware.run_llm_execution_middleware(
        prepared.payload,
        lambda request: request,
        runtime_agent=object(),
        future_context="additive",
    )

    assert observed == ["request", "execution"]
    assert executed == {"model": "m", "filtered": True, "checked": True}


def test_explicit_middleware_block_is_rethrown_before_provider_execution(monkeypatch):
    manager = _manager(monkeypatch)
    blocked = middleware.MiddlewareExecutionBlocked("attestation failed")
    provider_calls: list[dict] = []

    def guard(request, next_call):
        raise blocked

    manager._middleware["llm_execution"] = [guard]

    with pytest.raises(middleware.MiddlewareExecutionBlocked, match="attestation failed"):
        middleware.run_llm_execution_middleware(
            {"model": "m"},
            lambda request: provider_calls.append(request),
        )
    assert provider_calls == []
