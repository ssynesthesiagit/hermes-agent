"""Opt-in Hermes ``pre_llm_call`` bridge for the source-locked K3 core.

The plugin owns no compiler logic and no persistence.  It imports the K3 core
from an explicitly configured source path, asks its read-only artifact seam
for a precompiled capsule, and returns rendered context to Hermes.  Hermes'
existing hook delivery keeps that context ephemeral and beside pruning in the
API-bound user message.  An independently namespaced, default-off Crystal
observer may inspect the already validated capsule out of band; its return is
never exposed to Hermes' hook result.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


_REQUIRED_SCOPE_FIELDS = (
    "project_scope",
    "role_scope",
    "profile_or_agent",
    "host_id",
    "session_id",
    "task_id",
    "attempt_id",
    "task_fingerprint",
    "policy_generation",
    "source_generation",
    "privacy_class",
    "authorization_state",
    "task_current",
    "attempt_current",
    "cancelled",
    "superseded",
    "stale_reason",
)
_BOOLEAN_SCOPE_FIELDS = frozenset(
    {"task_current", "attempt_current", "cancelled", "superseded"}
)
_SHADOW_KEYS = frozenset(
    {
        "mode",
        "core_path",
        "core_digest",
        "catalog_path",
        "catalog_root",
        "sink_path",
        "sink_root",
        "observed_at",
        "policy",
        "task_family",
        "trusted_facts",
        "max_sink_events",
        "max_sink_bytes",
    }
)


@dataclass(frozen=True)
class ShadowSettings:
    core_path: str
    core_digest: str
    catalog_path: str
    catalog_root: str
    sink_path: str
    sink_root: str
    observed_at: str
    policy: Mapping[str, Any]
    task_family: str
    trusted_facts: Mapping[str, Any]
    max_sink_events: int
    max_sink_bytes: int

    @classmethod
    def from_value(cls, value: Any) -> tuple["ShadowSettings | None", str]:
        """Return a valid shadow config or an inert categorical rejection."""

        if value is None:
            return None, "OFF"
        if not isinstance(value, Mapping):
            return None, "CONFIG_REJECTED"
        mode = value.get("mode", "off")
        if mode == "off" and set(value) == {"mode"}:
            return None, "OFF"
        if mode != "shadow" or set(value) != _SHADOW_KEYS:
            return None, "CONFIG_REJECTED"
        paths = (
            "core_path",
            "core_digest",
            "catalog_path",
            "catalog_root",
            "sink_path",
            "sink_root",
            "observed_at",
            "task_family",
        )
        if any(not isinstance(value.get(key), str) or not value[key].strip() for key in paths):
            return None, "CONFIG_REJECTED"
        if not isinstance(value.get("policy"), Mapping) or not isinstance(
            value.get("trusted_facts"), Mapping
        ):
            return None, "CONFIG_REJECTED"
        max_events = value.get("max_sink_events")
        max_bytes = value.get("max_sink_bytes")
        if type(max_events) is not int or type(max_bytes) is not int:
            return None, "CONFIG_REJECTED"
        return (
            cls(
                core_path=value["core_path"],
                core_digest=value["core_digest"],
                catalog_path=value["catalog_path"],
                catalog_root=value["catalog_root"],
                sink_path=value["sink_path"],
                sink_root=value["sink_root"],
                observed_at=value["observed_at"],
                policy=dict(value["policy"]),
                task_family=value["task_family"],
                trusted_facts=dict(value["trusted_facts"]),
                max_sink_events=max_events,
                max_sink_bytes=max_bytes,
            ),
            "CONFIGURED",
        )


@dataclass(frozen=True)
class PluginSettings:
    enabled: bool
    core_path: str
    capsule_path: str
    receipt_path: str
    cache_path: str | None
    now: str
    scope: Mapping[str, Any]
    runtime_identity: Mapping[str, Any] | None = None
    compiler_version: str | None = None
    schema_generation: str = "yatima.k3.v1"
    executable_hash: str | None = None
    context_required_fields: tuple[str, ...] = _REQUIRED_SCOPE_FIELDS
    shadow: ShadowSettings | None = None
    shadow_config_state: str = "OFF"

    @classmethod
    def from_context(cls, ctx: Any) -> "PluginSettings | None":
        """Read namespaced plugin settings; missing/invalid settings disable."""

        try:
            override = os.environ.pop("YATIMA_K3_WORKER_SETTINGS_JSON", "")
            override_values = json.loads(override) if override else None
            if override_values is not None and not isinstance(override_values, Mapping):
                return None

            def setting(name: str, default: Any = None) -> Any:
                if isinstance(override_values, Mapping) and name in override_values:
                    return override_values[name]
                return ctx.get_config(name, default)

            enabled = setting("enabled", False)
            if enabled is not True:
                return None
            core_path = setting("core_path")
            capsule_path = setting("capsule_path")
            receipt_path = setting("receipt_path")
            now = setting("now")
            scope = setting("scope")
            if not all(isinstance(value, str) and value.strip() for value in (core_path, capsule_path, receipt_path, now)):
                return None
            if not isinstance(scope, Mapping):
                return None
            for key in _REQUIRED_SCOPE_FIELDS:
                if key not in scope:
                    return None
                value = scope[key]
                if key in _BOOLEAN_SCOPE_FIELDS:
                    if type(value) is not bool:
                        return None
                elif key == "stale_reason":
                    if value is not None and (
                        not isinstance(value, str) or not value.strip()
                    ):
                        return None
                elif not isinstance(value, str) or not value.strip():
                    return None
            runtime = setting("runtime_identity")
            if runtime is not None and not isinstance(runtime, Mapping):
                return None
            required = setting("context_required_fields", _REQUIRED_SCOPE_FIELDS)
            if not isinstance(required, (list, tuple)) or not required:
                return None
            required_fields = tuple(str(value) for value in required)
            if any(value not in _REQUIRED_SCOPE_FIELDS for value in required_fields):
                return None
            shadow, shadow_config_state = ShadowSettings.from_value(setting("shadow"))
            return cls(
                enabled=True,
                core_path=core_path,
                capsule_path=capsule_path,
                receipt_path=receipt_path,
                cache_path=setting("cache_path"),
                now=now,
                scope=dict(scope),
                runtime_identity=dict(runtime) if runtime is not None else None,
                compiler_version=setting("compiler_version"),
                schema_generation=str(setting("schema_generation", "yatima.k3.v1")),
                executable_hash=setting("executable_hash"),
                context_required_fields=required_fields,
                shadow=shadow,
                shadow_config_state=shadow_config_state,
            )
        except Exception:
            # Plugin discovery must not make Hermes startup fail.  The absence
            # of a valid explicit opt-in is a safe no-op.
            return None


def _load_core(core_path: str) -> Any:
    """Import exactly the K3 package rooted at the configured source path."""

    root = Path(core_path).expanduser()
    if not root.is_absolute() or not (root / "yatima_k3" / "__init__.py").is_file():
        raise ImportError("configured core_path is not a yatima_k3 source root")
    package_path = (root / "yatima_k3" / "__init__.py").resolve()
    existing = sys.modules.get("yatima_k3")
    if existing is not None:
        loaded_path = getattr(existing, "__file__", None)
        if loaded_path is None or Path(loaded_path).resolve() != package_path:
            raise ImportError("yatima_k3 is already loaded from a different source path")
        return existing
    path_text = str(root.resolve())
    if path_text not in sys.path:
        sys.path.insert(0, path_text)
    importlib.invalidate_caches()
    module = importlib.import_module("yatima_k3")
    loaded_path = getattr(module, "__file__", None)
    if loaded_path is None or Path(loaded_path).resolve() != package_path:
        raise ImportError("loaded yatima_k3 path does not match configured source")
    return module


def _shadow_source_digest(root: Path) -> str:
    package = root / "yatima_k3_shadow"
    if root.is_symlink() or package.is_symlink():
        raise ImportError("configured shadow core path contains a symlink")
    digest = hashlib.sha256()
    files = sorted(path for path in package.rglob("*.py") if "__pycache__" not in path.parts)
    if not files:
        raise ImportError("configured shadow core contains no Python source")
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ImportError("configured shadow core contains a non-regular source path")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _load_shadow_core(core_path: str, expected_digest: str) -> Any:
    """Import the Crystal observer only after explicit shadow selection."""

    root = Path(core_path).expanduser()
    if not root.is_absolute() or not (root / "yatima_k3_shadow" / "__init__.py").is_file():
        raise ImportError("configured shadow core_path is not a yatima_k3_shadow source root")
    if (
        not isinstance(expected_digest, str)
        or len(expected_digest) != 64
        or _shadow_source_digest(root) != expected_digest
    ):
        raise ImportError("configured shadow core digest does not match source")
    package_path = (root / "yatima_k3_shadow" / "__init__.py").resolve()
    existing = sys.modules.get("yatima_k3_shadow")
    if existing is not None:
        loaded_path = getattr(existing, "__file__", None)
        if loaded_path is None or Path(loaded_path).resolve() != package_path:
            raise ImportError("yatima_k3_shadow is already loaded from a different source path")
        return existing
    path_text = str(root.resolve())
    if path_text not in sys.path:
        sys.path.insert(0, path_text)
    importlib.invalidate_caches()
    module = importlib.import_module("yatima_k3_shadow")
    loaded_path = getattr(module, "__file__", None)
    if loaded_path is None or Path(loaded_path).resolve() != package_path:
        raise ImportError("loaded yatima_k3_shadow path does not match configured source")
    return module


def _scope_from_core(core: Any, values: Mapping[str, Any]) -> Any:
    scope_values = {key: values[key] for key in _REQUIRED_SCOPE_FIELDS}
    for key in ("task_current", "attempt_current", "cancelled", "superseded", "stale_reason"):
        if key in values:
            scope_values[key] = values[key]
    return core.ScopeBindings(**scope_values)


@dataclass
class K3HookRuntime:
    settings: PluginSettings
    core: Any
    adapter: Any
    shadow_observer: Any | None = None
    shadow_health: str = "OFF"

    def on_pre_llm_call(self, **kwargs: Any) -> dict[str, str] | None:
        """Return only ephemeral user-message context for an exact turn."""

        # Every ScopeBindings field is a live binding. Missing or mismatched
        # values fail closed before reading artifacts; accepting a partial
        # callback would allow a valid capsule from another turn to leak in.
        scope = self.settings.scope
        if any(
            key not in kwargs or kwargs[key] != scope.get(key)
            for key in self.settings.context_required_fields
        ):
            return None
        try:
            static_scope = _scope_from_core(self.core, scope)
        except Exception:
            return None
        try:
            payload = self.adapter.call_memory_seed()
            capsule = payload["task_session_capsule"]
            if not isinstance(capsule, Mapping):
                return None
            cancellation = capsule.get("cancellation_and_staleness")
            if not isinstance(cancellation, Mapping):
                return None
            capsule_values: dict[str, Any] = {}
            for key in _REQUIRED_SCOPE_FIELDS:
                if key in _BOOLEAN_SCOPE_FIELDS or key in {"authorization_state", "stale_reason"}:
                    if key not in cancellation:
                        return None
                    capsule_values[key] = cancellation[key]
                else:
                    if key not in capsule:
                        return None
                    capsule_values[key] = capsule[key]
            # Re-check the exact host seam bindings after the transport-level
            # validator. This prevents a stale callback payload from injecting
            # a valid capsule belonging to another turn.
            if _scope_from_core(self.core, capsule_values).to_dict() != static_scope.to_dict():
                return None
            compiler = capsule.get("compiler", {})
            if self.settings.compiler_version is not None and compiler.get("compiler_version") != self.settings.compiler_version:
                return None
            if compiler.get("schema_generation") != self.settings.schema_generation:
                return None
            if self.settings.executable_hash is not None and compiler.get("executable_hash") != self.settings.executable_hash:
                return None
            rendered = self.core.render_markdown(capsule)
            if not isinstance(rendered, str) or not rendered.strip():
                return None
            # The shadow observer runs only after existing K3 validation and
            # rendering have produced the normal return. Its output and
            # failures remain on a caller-owned diagnostic channel.
            if self.shadow_observer is not None:
                try:
                    result = self.shadow_observer.observe_validated_k3(
                        capsule,
                        static_scope.to_dict(),
                    )
                    self.shadow_health = result.sink.state
                except Exception:
                    self.shadow_health = "DEGRADED"
            # Hermes invokes this hook before composing the API user message;
            # it does not persist returned context in the session DB/history.
            return {"context": rendered}
        except Exception:
            return None


def _build_shadow_observer(settings: ShadowSettings) -> Any:
    shadow_core = _load_shadow_core(settings.core_path, settings.core_digest)
    policy = shadow_core.ShadowPolicy.from_mapping(settings.policy)
    if policy.mode != "shadow":
        raise ValueError("configured shadow policy is not in shadow mode")
    catalog = shadow_core.load_catalog(
        settings.catalog_path,
        settings.catalog_root,
        policy,
    )
    sink = shadow_core.FileDiagnosticSink(
        settings.sink_path,
        settings.sink_root,
        max_events=settings.max_sink_events,
        max_bytes=settings.max_sink_bytes,
    )
    return shadow_core.ShadowObserver(
        policy=policy,
        catalog=catalog,
        sink=sink,
        observed_at=settings.observed_at,
        task_family=settings.task_family,
        facts=settings.trusted_facts,
    )


def build_runtime(settings: PluginSettings) -> K3HookRuntime:
    core = _load_core(settings.core_path)
    transport = importlib.import_module("yatima_k3.mcp")
    scope = _scope_from_core(core, settings.scope)
    runtime = None
    if settings.runtime_identity is not None:
        runtime = core.RuntimeIdentity(**dict(settings.runtime_identity))
    adapter = transport.K3MCPAdapter(
        transport.AdapterConfig(
            capsule_path=settings.capsule_path,
            receipt_path=settings.receipt_path,
            cache_path=settings.cache_path,
            expected_scope=scope,
            expected_runtime=runtime,
            now=settings.now,
        )
    )
    runtime = K3HookRuntime(
        settings=settings,
        core=core,
        adapter=adapter,
        shadow_health=settings.shadow_config_state,
    )
    if settings.shadow is not None:
        try:
            runtime.shadow_observer = _build_shadow_observer(settings.shadow)
            runtime.shadow_health = "READY"
        except Exception:
            # Shadow setup cannot disable the accepted K3 hook or leak an
            # exception into model-visible context.
            runtime.shadow_health = "DEGRADED"
    return runtime


def register_plugin(ctx: Any) -> None:
    settings = PluginSettings.from_context(ctx)
    if settings is not None:
        try:
            runtime = build_runtime(settings)
        except Exception:
            runtime = None
        if runtime is not None:
            ctx.register_hook("pre_llm_call", runtime.on_pre_llm_call)

    # Runtime integrity is a separate, explicitly namespaced opt-in.  Keep it
    # out of the accepted K3 read-only hook path so disabled/default profiles
    # register no behavior-changing middleware.
    try:
        from .integrity_adapter import register_integrity_middleware
    except (ImportError, ValueError):
        register_integrity_middleware = None
    if register_integrity_middleware is not None:
        register_integrity_middleware(ctx)
