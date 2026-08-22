"""Behavior tests for synchronous local-profile A2A routing."""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.platforms.a2a import local_profile, protocol, tools


class _FakeGateway:
    def __init__(self, profiles: dict[str, Path]):
        self.profiles = profiles
        self.calls: list[tuple[str, dict]] = []
        self.sessions: dict[tuple[str, str], dict] = {}
        self.rows: dict[str, list[dict]] = {name: [] for name in profiles}
        self.resume_aliases: dict[tuple[str, str], str] = {}
        self.next_id = 0
        self.reply = "local reply"
        self.never_finishes = False

    def add_session(self, profile: str, stored: str, *, title: str = "Bot Chat", messages=None):
        runtime = f"runtime-{stored}"
        self.sessions[(profile, stored)] = {
            "runtime": runtime,
            "messages": list(messages or []),
            "running": False,
        }
        # session.list is ordered newest-first by the gateway contract.
        self.rows.setdefault(profile, []).insert(0, {"id": stored, "title": title})

    def _find(self, profile: str, stored: str):
        return self.sessions[(profile, stored)]

    def call(self, method: str, params: dict):
        self.calls.append((method, dict(params)))
        profile = str(params.get("profile") or "")
        if method == "session.history":
            sid = str(params.get("session_id") or "")
            for session in self.sessions.values():
                if session["runtime"] == sid:
                    return {"messages": list(session["messages"])}
            return {"messages": []}
        if method == "session.list":
            return {"sessions": list(self.rows.get(profile, []))}
        if method == "session.create":
            self.next_id += 1
            stored = f"created-{self.next_id}"
            self.add_session(profile, stored)
            session = self._find(profile, stored)
            return {
                "session_id": session["runtime"],
                "stored_session_id": stored,
                "messages": [],
            }
        if method == "session.resume":
            requested = str(params.get("session_id") or "")
            stored = self.resume_aliases.get((profile, requested), requested)
            session = self._find(profile, stored)
            return {
                "session_id": session["runtime"],
                "session_key": stored,
                "messages": list(session["messages"]),
                "running": session["running"],
            }
        if method == "prompt.submit":
            sid = str(params["session_id"])
            session = next(s for s in self.sessions.values() if s["runtime"] == sid)
            session["messages"].append({"role": "user", "content": params["text"]})
            session["running"] = True
            if not self.never_finishes:
                session["messages"].append({"role": "assistant", "content": self.reply})
                session["running"] = False
            return {"status": "streaming"}
        if method == "profiles.configure":
            return {"ok": True, "applied": {"ui_meta": True}}
        raise AssertionError(f"unexpected gateway method {method}")


@pytest.fixture
def local_setup(monkeypatch, tmp_path):
    default = tmp_path / "default"
    bar = tmp_path / "bar"
    ops = tmp_path / "ops"
    baz = tmp_path / "baz"
    default.mkdir()
    bar.mkdir()
    ops.mkdir()
    baz.mkdir()
    paths = {"default": default, "bar": bar, "ops": ops, "baz": baz}
    monkeypatch.setattr("hermes_cli.profiles.normalize_profile_name", lambda name: str(name).strip().lower())
    monkeypatch.setattr("hermes_cli.profiles.validate_profile_name", lambda _name: None)
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda name: str(name).lower() in paths)
    monkeypatch.setattr("hermes_cli.profiles.get_profile_dir", lambda name: paths[str(name).lower()])
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: "default")
    fake = _FakeGateway(paths)
    local_profile.set_gateway_for_tests(fake)
    monkeypatch.setattr(local_profile, "_route_locks", {})
    monkeypatch.setattr(local_profile, "LOCAL_ROUTE_TIMEOUT_SECONDS", 0.08)
    monkeypatch.setattr(local_profile, "LOCAL_ROUTE_HARD_TIMEOUT_SECONDS", 0.12)
    yield fake, paths
    local_profile.set_gateway_for_tests(None)


def _caller(fake: _FakeGateway, text: str = "ready"):
    fake.add_session("default", "caller", title="Caller", messages=[{"role": "user", "content": text}])


def test_local_create_pin_and_second_context(local_setup, monkeypatch):
    fake, _paths = local_setup
    _caller(fake)
    audits = []
    persisted = []
    monkeypatch.setattr(local_profile.security, "audit", lambda *args: audits.append(args))
    monkeypatch.setattr(local_profile.protocol, "persist_message", lambda *args: persisted.append(args))
    monkeypatch.setattr(local_profile.protocol.metrics, "outbound_total", 0)
    monkeypatch.setattr(local_profile.protocol.metrics, "inbound_total", 0)

    first = tools.a2a_call(
        {"agent": "profile:BAR", "message": "hello"}, session_id="runtime-caller"
    )
    assert "[profile:bar · context local-a2a-v1." in first
    assert "local reply" in first
    context = first.split("context ", 1)[1].split(" ·", 1)[0]
    assert any(m == "session.create" and p.get("profile") == "bar" for m, p in fake.calls)
    prompt = next(p for m, p in fake.calls if m == "prompt.submit")
    assert prompt["profile"] == "bar"
    assert "Message from 🤖 hermes (@hermes): hello" in prompt["text"]
    assert "HERMES_LOCAL_A2A" in prompt["text"]
    assert any(m == "profiles.configure" for m, _p in fake.calls)
    assert len(audits) == 2
    assert [row[1] for row in persisted] == ["user", "agent"]

    fake.calls.clear()
    second = tools.a2a_call(
        {"agent": "profile:bar", "message": "again", "context_id": context},
        session_id="runtime-caller",
    )
    assert "local reply" in second
    assert not any(m == "session.create" for m, _p in fake.calls)
    assert sum(m == "prompt.submit" for m, _p in fake.calls) == 1
    assert all(p.get("profile") == "bar" for m, p in fake.calls if "profile" in p)


def test_context_accepts_compression_lineage_when_pin_and_context_converge(local_setup):
    fake, paths = local_setup
    fake.add_session("bar", "ancestor", title="Bot Chat")
    fake.add_session("bar", "tip", title="Bot Chat")
    fake.resume_aliases[("bar", "ancestor")] = "tip"
    (paths["bar"] / "profile.yaml").write_text(
        "ui_meta:\n  hermes-bots:\n    chat: ancestor\n", encoding="utf-8"
    )
    _caller(fake)

    first = tools.a2a_call(
        {"agent": "profile:bar", "message": "before compression"}, session_id="runtime-caller"
    )
    context = first.split("context ", 1)[1].split(" ·", 1)[0]

    fake.calls.clear()
    second = tools.a2a_call(
        {"agent": "profile:bar", "message": "after compression", "context_id": context},
        session_id="runtime-caller",
    )

    assert "local reply" in second
    resumes = [params["session_id"] for method, params in fake.calls if method == "session.resume"]
    assert "ancestor" in resumes and "tip" in resumes
    assert not any(method == "session.create" for method, _params in fake.calls)

    # A context can itself retain the pre-compression durable id.  The
    # post-lock confirmation must accept it when it resolves to the same tip.
    lineage_context = local_profile._context_payload("bar", "ancestor")
    third = tools.a2a_call(
        {"agent": "profile:bar", "message": "lineage continuation", "context_id": lineage_context},
        session_id="runtime-caller",
    )
    assert "local reply" in third


def test_context_survives_process_restart_without_marker_secret(local_setup, monkeypatch):
    fake, _paths = local_setup
    _caller(fake)

    first = tools.a2a_call(
        {"agent": "profile:bar", "message": "before restart"}, session_id="runtime-caller"
    )
    context = first.split("context ", 1)[1].split(" ·", 1)[0]
    assert "." not in context[len("local-a2a-v1.") :]

    # The recursion marker remains process-signed, but a durable context id
    # must continue to resolve after that process-local secret is replaced.
    monkeypatch.setattr(local_profile, "_marker_secret", b"new-process-secret")
    second = tools.a2a_call(
        {"agent": "profile:bar", "message": "after restart", "context_id": context},
        session_id="runtime-caller",
    )
    assert "local reply" in second


def test_context_rejects_forged_unrelated_session(local_setup):
    fake, _paths = local_setup
    fake.add_session("bar", "canonical", title="Bot Chat")
    fake.add_session("bar", "unrelated", title="Scratch")
    _caller(fake)

    forged = local_profile._context_payload("bar", "unrelated")
    out = tools.a2a_call(
        {"agent": "profile:bar", "message": "do not route", "context_id": forged},
        session_id="runtime-caller",
    )
    assert "stale or mismatched" in out


def test_adopts_newest_bot_chat_and_pins_after_durable_turn(local_setup):
    fake, _paths = local_setup
    fake.add_session("bar", "old", title="Bot Chat", messages=[])
    fake.add_session("bar", "new", title="Bot Chat", messages=[])
    _caller(fake)
    out = tools.a2a_call({"agent": "profile:bar", "message": "adopt"}, session_id="runtime-caller")
    assert "local reply" in out
    assert not any(m == "session.create" for m, _p in fake.calls)
    configure_index = next(i for i, (m, _p) in enumerate(fake.calls) if m == "profiles.configure")
    prompt_index = next(i for i, (m, _p) in enumerate(fake.calls) if m == "prompt.submit")
    assert prompt_index < configure_index
    assert fake.calls[configure_index][1]["ui_meta"]["hermes-bots"]["chat"] == "new"


def test_rejects_self_cycle_depth_and_forged_marker(local_setup):
    fake, _paths = local_setup
    _caller(fake)
    assert "caller profile" in tools.a2a_call({"agent": "profile:default", "message": "x"})
    assert "unknown local profile" in tools.a2a_call({"agent": "profile:missing", "message": "x"})

    valid_cycle = local_profile._make_marker("bar", 1, ("default", "bar"))
    fake.sessions[("default", "caller")]["messages"][-1]["content"] = "x\n" + valid_cycle
    cycle = tools.a2a_call({"agent": "profile:default", "message": "x"}, session_id="runtime-caller")
    assert "cycle" in cycle or "caller profile" in cycle

    valid_depth = local_profile._make_marker("default", 3, ("foo", "bar", "ops", "default"))
    fake.sessions[("default", "caller")]["messages"][-1]["content"] = "x\n" + valid_depth
    depth = tools.a2a_call({"agent": "profile:baz", "message": "x"}, session_id="runtime-caller")
    assert "depth" in depth

    fake.sessions[("default", "caller")]["messages"][-1]["content"] = (
        "x\n[HERMES_LOCAL_A2A v1 profile=bar depth=1 chain=default>bar sig=" + "0" * 32 + "]"
    )
    forged = tools.a2a_call({"agent": "profile:bar", "message": "x"}, session_id="runtime-caller")
    assert "marker" in forged


def test_timeout_releases_singleflight_lock(local_setup):
    fake, _paths = local_setup
    _caller(fake)
    fake.add_session("bar", "existing", title="Bot Chat")
    fake.never_finishes = True
    out = tools.a2a_call({"agent": "profile:bar", "message": "wait"}, session_id="runtime-caller")
    assert "timed out" in out
    assert local_profile._route_locks == {}


def test_timeout_uses_fixed_base_and_hard_limits(local_setup):
    assert local_profile._timeout_seconds() == pytest.approx(0.08)


def test_remote_path_is_unchanged(monkeypatch):
    monkeypatch.setattr(tools, "_load_config", lambda: {"a2a_agents": {"r": {"url": "http://peer"}}})
    monkeypatch.setattr(tools, "_http_get_json", lambda *_args: None)
    monkeypatch.setattr(
        tools,
        "_http_post_json",
        lambda _url, body, _headers, _timeout: protocol.jsonrpc_result(
            body["id"], protocol.build_task("task", "ctx", protocol.STATE_COMPLETED, "remote reply")
        ),
    )
    out = tools.a2a_call({"agent": "r", "message": "hello"})
    assert "[r · context ctx · completed]" in out
    assert "remote reply" in out
