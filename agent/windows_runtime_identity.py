"""Windows runtime identity readback outside model-authored content.

The adapter reads the live agent transport plus profile configuration.  It does
not inspect assistant messages or accept a model-supplied identity field.
"""

from __future__ import annotations

import json
import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from hermes_cli.route_identity import normalize_route_base_url
from hermes_constants import get_hermes_home


class RuntimeReadbackError(ValueError):
    """Raised when an explicitly configured runtime cannot be attested."""


@dataclass(frozen=True)
class WindowsRuntimeReadback:
    role_id: str
    host: str
    provider: str
    exact_model_id: str
    model_route: str
    api_mode: str
    policy_generation: str
    capability_tier: str
    attested: bool
    sources: tuple[str, ...]

    def canonical_json(self) -> str:
        return json.dumps(
            asdict(self),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _read_profile_config(path: Path | None = None) -> Mapping[str, Any]:
    config_path = path or (get_hermes_home() / "config.yaml")
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, yaml.YAMLError):
        return {}
    return _mapping(loaded)


def _client_route(agent: Any) -> str:
    clients = (
        getattr(agent, "client", None),
        getattr(agent, "_anthropic_client", None),
    )
    for client in clients:
        if client is None:
            continue
        for name in ("base_url", "_base_url"):
            value = getattr(client, name, None)
            if value:
                return normalize_route_base_url(str(value))
    return ""


class WindowsRuntimeIdentityAdapter:
    """Attest an agent's effective provider/model/endpoint on Windows."""

    def __init__(
        self,
        *,
        config_path: Path | None = None,
        config: Mapping[str, Any] | None = None,
        host: str | None = None,
    ) -> None:
        self._config_path = config_path
        self._config = dict(config) if isinstance(config, Mapping) else None
        self._host = str(host or "").strip()

    def read_agent(self, agent: Any) -> WindowsRuntimeReadback:
        config = (
            self._config
            if self._config is not None
            else _read_profile_config(self._config_path)
        )
        model_config = _mapping(config.get("model"))
        nested_integrity = config.get("runtime_integrity")
        integrity = (
            _mapping(nested_integrity)
            if isinstance(nested_integrity, Mapping)
            else _mapping(config)
        )

        provider = str(getattr(agent, "provider", "") or "").strip().lower()
        model = str(getattr(agent, "model", "") or "").strip()
        route = normalize_route_base_url(getattr(agent, "base_url", "") or "")
        client_route = _client_route(agent)

        configured_provider = str(model_config.get("provider") or "").strip().lower()
        configured_model = str(
            model_config.get("default") or model_config.get("model") or ""
        ).strip()
        configured_route = normalize_route_base_url(model_config.get("base_url") or "")

        config_matches = (
            (not configured_provider or configured_provider == provider)
            and (not configured_model or configured_model == model)
            and (not configured_route or configured_route == route)
        )
        client_matches = not client_route or client_route == route
        sources = ["agent_effective_runtime"]
        if client_route:
            sources.append("client_endpoint_readback")
        if model_config or integrity:
            sources.append("profile_config_readback")

        model_capabilities = _mapping(
            integrity.get("model_capabilities") or integrity.get("capabilities")
        )
        capability = str(
            model_capabilities.get(model)
            or integrity.get("default_capability")
            or "C1_ROUTINE_ASSISTANT"
        ).strip()
        policy_generation = str(integrity.get("policy_generation") or "").strip()
        if self._config is not None and not policy_generation:
            raise RuntimeReadbackError(
                "runtime-integrity policy_generation is required for injected config"
            )

        require_profile_match = bool(integrity.get("require_profile_match", False))
        return WindowsRuntimeReadback(
            role_id=str(integrity.get("role_id") or "default").strip() or "default",
            host=self._host or platform.node() or "windows",
            provider=provider,
            exact_model_id=model,
            model_route=route,
            api_mode=str(getattr(agent, "api_mode", "") or "").strip(),
            policy_generation=policy_generation or "UNCONFIGURED",
            capability_tier=capability,
            attested=bool(
                provider
                and model
                and route
                and client_matches
                and (config_matches or not require_profile_match)
            ),
            sources=tuple(sources),
        )
