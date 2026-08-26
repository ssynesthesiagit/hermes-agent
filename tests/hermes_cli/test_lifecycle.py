from types import SimpleNamespace

import pytest

from agent import relay_runtime
from hermes_cli import lifecycle, observability, plugins


def test_invoke_hook_notifies_builtin_observers_before_plugins(monkeypatch):
    calls = []
    manager = SimpleNamespace(
        invoke_hook=lambda name, **kwargs: calls.append(("plugin", name, kwargs)) or ["ok"]
    )
    monkeypatch.setattr(
        observability,
        "observe_lifecycle",
        lambda name, **kwargs: calls.append(("builtin", name, kwargs)),
    )
    monkeypatch.setattr(plugins, "invoke_hook", manager.invoke_hook)

    result = lifecycle.invoke_hook("on_session_start", session_id="session-1")

    assert result == ["ok"]
    assert [call[0] for call in calls] == ["builtin", "plugin"]


def test_finalize_session_runs_plugin_gate_before_core_close(monkeypatch):
    calls = []
    manager = SimpleNamespace(
        invoke_hook=lambda name, **kwargs: calls.append(("plugin", name, kwargs)) or []
    )
    coordinator = SimpleNamespace(
        finalize_conversation=lambda **kwargs: calls.append(("core", kwargs))
    )
    monkeypatch.setattr(
        observability,
        "observe_lifecycle",
        lambda name, **kwargs: calls.append(("builtin", name, kwargs)),
    )
    monkeypatch.setattr(plugins, "invoke_hook", manager.invoke_hook)
    monkeypatch.setattr(relay_runtime, "SESSION_COORDINATOR", coordinator)
    monkeypatch.setattr(relay_runtime, "current_profile_key", lambda: "profile-1")

    lifecycle.finalize_session(session_id="session-1", platform="cli")

    assert [call[0] for call in calls] == ["builtin", "plugin", "core"]
    assert calls[2][1] == {
        "profile_key": "profile-1",
        "session_id": "session-1",
    }


def test_finalize_session_denial_blocks_core_close(monkeypatch):
    calls = []
    monkeypatch.setattr(
        observability,
        "observe_lifecycle",
        lambda name, **kwargs: calls.append(("builtin", name)),
    )
    monkeypatch.setattr(
        plugins,
        "invoke_hook",
        lambda name, **kwargs: [
            {"allowed": False, "event": "SILENT_FAILURE_DETECTED"}
        ],
    )
    monkeypatch.setattr(
        relay_runtime.SESSION_COORDINATOR,
        "finalize_conversation",
        lambda **kwargs: calls.append(("core", kwargs)),
    )

    with pytest.raises(lifecycle.SessionFinalizationBlocked):
        lifecycle.finalize_session(session_id="blocked-session", platform="cli")
    assert all(call[0] != "core" for call in calls)


def test_plugin_only_dispatch_does_not_reenter_builtin_observers(monkeypatch):
    manager = SimpleNamespace(invoke_hook=lambda name, **kwargs: [name, kwargs])
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(
        observability,
        "observe_lifecycle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
    )

    assert plugins.invoke_hook("custom", value=1) == ["custom", {"value": 1}]
