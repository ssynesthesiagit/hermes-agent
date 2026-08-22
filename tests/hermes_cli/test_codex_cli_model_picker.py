"""Regression tests for the /model picker's credential-discovery paths.

Covers:
 - Normal path (tokens already in Hermes auth store)
 - Claude Code fallback (tokens only in ~/.claude/.credentials.json)
 - Negative case (no credentials anywhere)

Note: auto-import from ~/.codex/auth.json was removed in #12360 — Hermes
now owns its own openai-codex auth state, and users explicitly adopt
existing Codex CLI tokens via `hermes auth openai-codex`. The old
"Codex CLI shared file" discovery tests were removed with that change.
"""

import base64
import json
import time
from pathlib import Path

import pytest


def _make_fake_jwt(expiry_offset: int = 3600) -> str:
    """Build a fake JWT with a future expiry."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    exp = int(time.time()) + expiry_offset
    payload_bytes = json.dumps({"exp": exp, "sub": "test"}).encode()
    payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesig"


@pytest.fixture()
def hermes_auth_only_env(tmp_path, monkeypatch):
    """Tokens already in Hermes auth store (no Codex CLI needed)."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    # Point CODEX_HOME to nonexistent dir to prove it's not needed
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no_codex"))

    (hermes_home / "auth.json").write_text(json.dumps({
        "version": 2,
        "providers": {
            "openai-codex": {
                "tokens": {
                    "access_token": _make_fake_jwt(),
                    "refresh_token": "fake-refresh",
                },
                "last_refresh": "2026-04-12T00:00:00Z",
            }
        },
    }))

    for var in [
        "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "NOUS_API_KEY", "DEEPSEEK_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)

    return hermes_home


def test_normal_path_still_works(hermes_auth_only_env):
    """openai-codex appears when tokens are already in Hermes auth store."""
    from hermes_cli.model_switch import list_authenticated_providers

    providers = list_authenticated_providers(
        current_provider="openai-codex",
        max_models=10,
    )
    slugs = [p["slug"] for p in providers]
    assert "openai-codex" in slugs


def test_profile_picker_reads_global_codex_without_migrating_auth(tmp_path, monkeypatch):
    """A profile-local Nous login must not hide or copy the global Codex login."""
    import agent.models_dev as models_dev
    import hermes_cli.models as models_mod
    import hermes_cli.model_switch as model_switch
    import hermes_cli.providers as providers_mod

    global_root = tmp_path / ".hermes"
    profile_home = global_root / "profiles" / "coder"
    profile_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))

    global_store = {
        "version": 2,
        "active_provider": "openai-codex",
        "providers": {
            "openai-codex": {
                "tokens": {
                    "access_token": "global-codex-access",
                    "refresh_token": "global-codex-refresh",
                },
            },
        },
    }
    profile_store = {
        "version": 2,
        "active_provider": "nous",
        "providers": {
            "nous": {
                "access_token": "profile-nous-access",
                "refresh_token": "profile-nous-refresh",
            },
        },
    }
    (global_root / "auth.json").write_text(json.dumps(global_store, indent=2))
    (profile_home / "auth.json").write_text(json.dumps(profile_store, indent=2))
    global_before = (global_root / "auth.json").read_text()
    profile_before = (profile_home / "auth.json").read_text()

    monkeypatch.setattr(models_dev, "PROVIDER_TO_MODELS_DEV", {})
    monkeypatch.setattr(models_dev, "fetch_models_dev", lambda: {})
    monkeypatch.setattr(models_mod, "CANONICAL_PROVIDERS", [])
    monkeypatch.setattr(models_mod, "clear_provider_models_cache", lambda: None)
    monkeypatch.setattr(models_mod, "get_curated_nous_model_ids", lambda: ["nous-model"])
    monkeypatch.setattr(models_mod, "cached_provider_model_ids", lambda _provider: ["codex-model"])
    monkeypatch.setattr(models_mod, "get_pricing_for_provider", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(models_mod, "check_nous_free_tier", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        models_mod,
        "union_with_portal_paid_recommendations",
        lambda model_ids, *_args, **_kwargs: (model_ids, {}),
    )
    monkeypatch.setattr(
        providers_mod,
        "HERMES_OVERLAYS",
        {
            "nous": providers_mod.HERMES_OVERLAYS["nous"],
            "openai-codex": providers_mod.HERMES_OVERLAYS["openai-codex"],
        },
    )
    # Isolate the regression to the singleton-state fallback under test.
    monkeypatch.setattr(model_switch, "_credential_pool_is_usable", lambda *_args, **_kwargs: False)

    providers = model_switch.list_authenticated_providers(
        current_provider="nous",
        current_model="nous-model",
        max_models=10,
        refresh=True,
    )
    by_slug = {provider["slug"]: provider for provider in providers}

    assert set(by_slug) == {"nous", "openai-codex"}
    assert by_slug["nous"]["is_current"] is True
    assert by_slug["openai-codex"]["is_current"] is False
    assert (global_root / "auth.json").read_text() == global_before
    assert (profile_home / "auth.json").read_text() == profile_before
    assert "credential_pool" not in (profile_home / "auth.json").read_text()




@pytest.fixture()
def claude_code_only_env(tmp_path, monkeypatch):
    """Set up an environment where Anthropic credentials only exist in
    ~/.claude/.credentials.json (Claude Code) — not in env vars or Hermes
    auth store."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    # No Codex CLI
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no_codex"))

    (hermes_home / "auth.json").write_text(
        json.dumps({"version": 2, "providers": {}})
    )

    # Claude Code credentials in the correct format
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": _make_fake_jwt(),
            "refreshToken": "fake-refresh",
            "expiresAt": int(time.time() * 1000) + 3_600_000,
        }
    }))

    # Patch Path.home() so the adapter finds the file
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    for var in [
        "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
        "NOUS_API_KEY", "DEEPSEEK_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)

    return hermes_home


def test_claude_code_file_detected_by_model_picker(claude_code_only_env):
    """anthropic should appear when credentials only exist in ~/.claude/.credentials.json."""
    from hermes_cli.model_switch import list_authenticated_providers

    providers = list_authenticated_providers(
        current_provider="anthropic",
        max_models=10,
    )
    slugs = [p["slug"] for p in providers]
    assert "anthropic" in slugs, (
        f"anthropic not found in /model picker providers: {slugs}"
    )

    anthropic = next(p for p in providers if p["slug"] == "anthropic")
    assert anthropic["is_current"] is True
    assert anthropic["total_models"] > 0


def test_no_codex_when_no_credentials(tmp_path, monkeypatch):
    """openai-codex should NOT appear when no credentials exist anywhere."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no_codex"))

    (hermes_home / "auth.json").write_text(
        json.dumps({"version": 2, "providers": {}})
    )

    for var in [
        "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "NOUS_API_KEY", "DEEPSEEK_API_KEY", "COPILOT_GITHUB_TOKEN",
        "GH_TOKEN", "GEMINI_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)

    from hermes_cli.model_switch import list_authenticated_providers

    providers = list_authenticated_providers(
        current_provider="openrouter",
        max_models=10,
    )
    slugs = [p["slug"] for p in providers]
    assert "openai-codex" not in slugs, (
        "openai-codex should not appear without any credentials"
    )
