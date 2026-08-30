from __future__ import annotations

from types import SimpleNamespace

from hermes_cli.subcommands import peer


def test_parse_direct_bot_at_machine_address():
    assert peer._parse_target("@brain_omarchy@swarmlord") == (
        "swarmlord",
        "brain_omarchy",
    )
    assert peer._parse_target("brain_omarchy@swarmlord") == (
        "swarmlord",
        "brain_omarchy",
    )


def test_parse_legacy_peer_profile_address_stays_compatible():
    assert peer._parse_target("swarmlord/brain_omarchy") == (
        "swarmlord",
        "brain_omarchy",
    )


def test_add_all_profiles_propagates_route_directory_and_credential(monkeypatch, capsys):
    saved = []
    monkeypatch.setattr(peer, "_all_profile_names", lambda: ["default", "brain", "yatima"])
    monkeypatch.setattr(
        peer,
        "_save_peer_for_profile",
        lambda profile, name, entry, key: saved.append((profile, name, entry, key)),
    )
    args = SimpleNamespace(
        peer_action="add",
        name="swarmlord",
        url="http://100.74.27.25:8642",
        key="secret-for-test",
        note="Ubuntu Hermes",
        agents="brain_omarchy,yatima-training",
        all_profiles=True,
    )

    assert peer.cmd_peer(args) == 0
    assert [row[0] for row in saved] == ["default", "brain", "yatima"]
    assert all(row[2]["agents"] == ["brain_omarchy", "yatima-training"] for row in saved)
    assert all(row[3] == "secret-for-test" for row in saved)
    assert "3 profiles" in capsys.readouterr().out


def test_add_all_profiles_reuses_existing_default_key(monkeypatch):
    saved = []
    monkeypatch.setattr(peer, "_all_profile_names", lambda: ["default", "brain"])
    monkeypatch.setattr(peer, "_default_profile_peer_secret", lambda _name: "existing-key")
    monkeypatch.setattr(
        peer,
        "_save_peer_for_profile",
        lambda profile, name, entry, key: saved.append((profile, key)),
    )
    args = SimpleNamespace(
        peer_action="add",
        name="linux",
        url="http://100.97.133.41:8642",
        key="",
        note="",
        agents="",
        all_profiles=True,
    )

    assert peer.cmd_peer(args) == 0
    assert saved == [("default", "existing-key"), ("brain", "existing-key")]


def test_private_key_file_accepts_exact_peer_assignment(tmp_path):
    key_file = tmp_path / "peer.env"
    key_file.write_text("HERMES_PEER_SWARMLORD_KEY=file-key\n", encoding="utf-8")
    key_file.chmod(0o600)
    assert peer._peer_secret_from_file(str(key_file), "swarmlord") == "file-key"


def test_key_file_rejects_group_readable_permissions(tmp_path):
    key_file = tmp_path / "peer.env"
    key_file.write_text("API_SERVER_KEY=file-key\n", encoding="utf-8")
    key_file.chmod(0o640)
    try:
        peer._peer_secret_from_file(str(key_file), "swarmlord")
    except ValueError as exc:
        assert "chmod 600" in str(exc)
    else:
        raise AssertionError("group-readable credential file was accepted")


def test_agent_route_uses_exact_endpoint_and_agent_scoped_credential(monkeypatch, capsys):
    configured = {
        "url": "http://linux.tailnet:8642",
        "agent_routes": {
            "brain": {"url": "http://linux.tailnet:8750"},
        },
    }
    assert peer._base_url(configured, "brain") == "http://linux.tailnet:8750"

    selected = []
    requests = []
    monkeypatch.setattr(peer, "_load_peers", lambda: {"linux": configured})
    monkeypatch.setattr(
        peer,
        "_peer_secret",
        lambda name, agent=None: selected.append((name, agent)) or "brain-key",
    )
    monkeypatch.setattr(peer, "_ensure_bot_chat", lambda base, key: "bot-chat-id")
    monkeypatch.setattr(
        peer,
        "_request",
        lambda url, key, **kwargs: requests.append((url, key, kwargs))
        or {"message": {"content": "ok"}},
    )

    args = SimpleNamespace(
        peer_action="dm",
        target="@brain@linux",
        message="ping",
        json=False,
    )
    assert peer.cmd_peer(args) == 0
    assert selected == [("linux", "brain")]
    assert requests[0][0] == "http://linux.tailnet:8750/api/sessions/bot-chat-id/chat"
    assert requests[0][1] == "brain-key"
    assert capsys.readouterr().out.strip() == "ok"


def test_agent_secret_name_is_distinct_from_machine_default():
    assert peer._peer_key_env("swarmlord") == "HERMES_PEER_SWARMLORD_KEY"
    assert (
        peer._peer_key_env("swarmlord", "yatima-training")
        == "HERMES_PEER_SWARMLORD_YATIMA_TRAINING_KEY"
    )


def test_route_all_profiles_preserves_peer_and_propagates_scoped_key(monkeypatch, capsys):
    saved = []
    monkeypatch.setattr(peer, "_load_peers", lambda: {"swarmlord": {"url": "http://old"}})
    monkeypatch.setattr(peer, "_all_profile_names", lambda: ["default", "brain"])
    monkeypatch.setattr(
        peer,
        "_save_agent_route_for_profile",
        lambda profile, machine, agent, url, key: saved.append(
            (profile, machine, agent, url, key)
        ),
    )
    args = SimpleNamespace(
        peer_action="route",
        name="swarmlord",
        agent="yatima-training",
        url="http://100.74.27.25:8643",
        key="yatima-key",
        key_file="",
        all_profiles=True,
    )

    assert peer.cmd_peer(args) == 0
    assert saved == [
        (
            "default",
            "swarmlord",
            "yatima-training",
            "http://100.74.27.25:8643",
            "yatima-key",
        ),
        (
            "brain",
            "swarmlord",
            "yatima-training",
            "http://100.74.27.25:8643",
            "yatima-key",
        ),
    ]
    assert "2 profiles" in capsys.readouterr().out


def test_agent_key_file_accepts_scoped_or_machine_assignment(tmp_path):
    scoped = tmp_path / "scoped.env"
    scoped.write_text(
        "HERMES_PEER_SWARMLORD_YATIMA_TRAINING_KEY=scoped-key\n",
        encoding="utf-8",
    )
    scoped.chmod(0o600)
    assert (
        peer._peer_secret_from_file(
            str(scoped), "swarmlord", agent="yatima-training"
        )
        == "scoped-key"
    )

    machine = tmp_path / "machine.env"
    machine.write_text("HERMES_PEER_SWARMLORD_KEY=machine-key\n", encoding="utf-8")
    machine.chmod(0o600)
    assert (
        peer._peer_secret_from_file(
            str(machine), "swarmlord", agent="yatima-training"
        )
        == "machine-key"
    )


def test_machine_update_preserves_existing_agent_routes_and_directory():
    existing = {
        "url": "http://old",
        "agents": ["brain"],
        "agent_routes": {"brain": {"url": "http://brain:8750"}},
    }
    updated = peer._merge_peer_entry(
        existing,
        {"url": "http://new", "note": "renamed machine"},
    )
    assert updated == {
        "url": "http://new",
        "note": "renamed machine",
        "agents": ["brain"],
        "agent_routes": {"brain": {"url": "http://brain:8750"}},
    }
