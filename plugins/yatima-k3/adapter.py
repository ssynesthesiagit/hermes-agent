"""Opt-in Hermes ``pre_llm_call`` bridge for the source-locked K3 core.

The plugin owns no compiler logic and no persistence.  It imports the K3 core
from an explicitly configured source path, asks its read-only artifact seam
for a precompiled capsule, and returns rendered context to Hermes.  Hermes'
existing hook delivery keeps that context ephemeral and beside pruning in the
API-bound user message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import subprocess
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
_MAX_PROJECT_STATE_BYTES = 256 * 1024
_MAX_COMPILER_OUTPUT_BYTES = 512 * 1024


def _read_project_state(path: Path) -> tuple[str, dict[str, str | int]]:
    """Read one coherent regular-file snapshot without following a leaf symlink."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_PROJECT_STATE_BYTES:
            raise ValueError("invalid project state file")
        with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as handle:
            raw = handle.read(_MAX_PROJECT_STATE_BYTES + 1)
        after = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("project state changed during read")
        if len(raw.encode("utf-8")) > _MAX_PROJECT_STATE_BYTES:
            raise ValueError("project state exceeded its bound")
        return raw, {
            "device": str(after.st_dev),
            "inode": str(after.st_ino),
            "size": after.st_size,
            "mtimeNs": str(after.st_mtime_ns),
        }
    finally:
        os.close(descriptor)


def _history_retains_marker(history: Any, marker: str) -> bool:
    """Check only host-owned API sidecars, never user-controlled clean text."""

    if not isinstance(history, list):
        return False
    for message in history:
        if not isinstance(message, Mapping):
            continue
        sidecar = message.get("api_content")
        if isinstance(sidecar, str) and f"<!-- {marker} -->" in sidecar:
            return True
    return False


@dataclass(frozen=True)
class PluginSettings:
    enabled: bool
    core_path: str
    capsule_path: str
    receipt_path: str
    cache_path: str | None
    worker_input_path: str | None
    now: str
    scope: Mapping[str, Any]
    runtime_identity: Mapping[str, Any] | None = None
    compiler_version: str | None = None
    schema_generation: str = "yatima.k3.v1"
    executable_hash: str | None = None
    context_required_fields: tuple[str, ...] = _REQUIRED_SCOPE_FIELDS

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
            return cls(
                enabled=True,
                core_path=core_path,
                capsule_path=capsule_path,
                receipt_path=receipt_path,
                cache_path=setting("cache_path"),
                worker_input_path=setting("worker_input_path"),
                now=now,
                scope=dict(scope),
                runtime_identity=dict(runtime) if runtime is not None else None,
                compiler_version=setting("compiler_version"),
                schema_generation=str(setting("schema_generation", "yatima.k3.v1")),
                executable_hash=setting("executable_hash"),
                context_required_fields=required_fields,
            )
        except Exception:
            # Plugin discovery must not make Hermes startup fail.  The absence
            # of a valid explicit opt-in is a safe no-op.
            return None


@dataclass(frozen=True)
class InteractiveSettings:
    core_path: str
    project_state_path: str
    compiler_script: str
    python_executable: str
    project_scope: str
    profile_or_agent: str
    host_id: str
    role_scope: str
    source_repository: str
    source_commit: str
    timeout_seconds: float = 10.0

    @classmethod
    def from_context(cls, ctx: Any) -> "InteractiveSettings | None":
        try:
            if ctx.get_config("interactive_continuity_enabled", False) is not True:
                return None
            values = {
                "core_path": ctx.get_config("core_path"),
                "project_state_path": ctx.get_config("project_state_path"),
                "compiler_script": ctx.get_config("compiler_script"),
                "python_executable": ctx.get_config("python_executable"),
                "project_scope": ctx.get_config("project_scope"),
                "profile_or_agent": ctx.get_config("profile_or_agent"),
                "host_id": ctx.get_config("host_id"),
                "role_scope": ctx.get_config("role_scope", "direct-hermes"),
                "source_repository": ctx.get_config("source_repository"),
                "source_commit": ctx.get_config("source_commit"),
            }
            if not all(isinstance(value, str) and value.strip() for value in values.values()):
                return None
            for name in ("core_path", "project_state_path", "compiler_script", "python_executable"):
                if not Path(values[name]).expanduser().is_absolute():
                    return None
            if len(values["source_commit"]) != 40 or any(
                character not in "0123456789abcdef" for character in values["source_commit"]
            ):
                return None
            timeout = ctx.get_config("interactive_timeout_seconds", 10.0)
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 30:
                return None
            return cls(**values, timeout_seconds=float(timeout))
        except Exception:
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
            if self.settings.worker_input_path is not None:
                worker_path = Path(self.settings.worker_input_path)
                if not worker_path.is_absolute():
                    return None
                raw_worker, _ = _read_project_state(worker_path)
                worker = json.loads(raw_worker)
                if not isinstance(worker, Mapping):
                    return None
                supplied_hash = worker.get("input_pack_hash")
                hash_material = {key: value for key, value in worker.items() if key != "input_pack_hash"}
                if (
                    worker.get("schema_version") != "yatima.k3.worker-input.v1"
                    or worker.get("task_id") != scope.get("task_id")
                    or worker.get("authority_expanded") is not False
                    or supplied_hash != self.core.sha256_json(hash_material)
                ):
                    return None
                rendered = (
                    "# Yatima K3 narrow worker input pack\n\n"
                    "This pack is evidence context for an already-authorized worker; "
                    "it does not dispatch work or expand authority.\n\n"
                    f"- worker_input: {self.core.canonical_json(worker)}\n"
                )
            if not isinstance(rendered, str) or not rendered.strip():
                return None
            # Hermes invokes this hook before composing the API user message;
            # it does not persist returned context in the session DB/history.
            return {"context": rendered}
        except Exception:
            return None


@dataclass
class InteractiveK3Runtime:
    settings: InteractiveSettings
    semantic_view_by_request: dict[str, str] = field(default_factory=dict, repr=False)

    def on_pre_llm_call(self, **kwargs: Any) -> dict[str, str] | None:
        """Compile fresh project continuity for ordinary Hermes turns."""

        session_id = kwargs.get("session_id")
        goal = kwargs.get("user_message")
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        if not isinstance(goal, str) or not goal.strip():
            return None
        goal = goal.strip()[:2000]
        try:
            state_path = Path(self.settings.project_state_path)
            raw, source_snapshot = _read_project_state(state_path)
            state = json.loads(raw)
            if not isinstance(state, dict) or state.get("schema_version") != "yatima.k3.project-state.v1":
                return None
            if state.get("project_scope") != self.settings.project_scope:
                return None
            state_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            normalized_session = session_id.strip()
            request_key = hashlib.sha256(
                f"{state_hash}\0{goal}".encode("utf-8")
            ).hexdigest()
            cached_view = self.semantic_view_by_request.get(request_key)
            if cached_view and _history_retains_marker(
                kwargs.get("conversation_history"), f"yatima-k3-view:{cached_view}"
            ):
                return None
            source_generation = hashlib.sha256(
                f"{self.settings.project_scope}\0{goal}\0{state_hash}".encode("utf-8")
            ).hexdigest()
            payload = {
                "clientKind": "Hermes",
                "clientInstanceId": self.settings.profile_or_agent,
                "roleId": self.settings.role_scope,
                "projectScope": self.settings.project_scope,
                "hostId": self.settings.host_id,
                "sessionId": normalized_session,
                "goal": goal,
                "sourceGeneration": source_generation,
                "provider": "hermes-configured-route",
                "model": str(kwargs.get("model") or "unreported"),
                "endpoint": str(kwargs.get("platform") or "hermes-interactive"),
                "sourceRepository": self.settings.source_repository,
                "sourceCommit": self.settings.source_commit,
                "pointers": [],
                "projectStateRaw": raw,
                "projectStatePath": str(state_path),
                "projectStateHash": state_hash,
                "projectStateSnapshot": source_snapshot,
                "deliveryEpoch": str(
                    kwargs.get("turn_id")
                    or f"history-{len(kwargs.get('conversation_history', [])) if isinstance(kwargs.get('conversation_history'), list) else 0}"
                ),
            }
            completed = subprocess.run(
                [
                    self.settings.python_executable,
                    self.settings.compiler_script,
                    "--core",
                    self.settings.core_path,
                ],
                input=json.dumps(payload, ensure_ascii=False) + "\n",
                text=True,
                capture_output=True,
                shell=False,
                timeout=self.settings.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0 or len(completed.stdout.encode("utf-8")) > _MAX_COMPILER_OUTPUT_BYTES:
                return None
            result = json.loads(completed.stdout)
            capsule = result.get("task_session_capsule")
            if result.get("status") != "PASS" or result.get("project_state_loaded") is not True:
                return None
            if not isinstance(capsule, dict):
                return None
            if (
                capsule.get("project_scope") != self.settings.project_scope
                or capsule.get("profile_or_agent") != self.settings.profile_or_agent
                or capsule.get("session_id") != normalized_session
                or capsule.get("source_generation") != source_generation
            ):
                return None
            context_pack = result.get("context_pack")
            semantic_view_id = result.get("semantic_view_id")
            delivery = result.get("delivery_receipt")
            rendered = result.get("context_markdown")
            if (
                not isinstance(context_pack, Mapping)
                or context_pack.get("admitted") is not True
                or not isinstance(semantic_view_id, str)
                or len(semantic_view_id) != 64
                or context_pack.get("semantic_view_id") != semantic_view_id
                or not isinstance(delivery, Mapping)
                or delivery.get("semantic_view_id") != semantic_view_id
                or delivery.get("retention_marker") != f"yatima-k3-view:{semantic_view_id}"
                or not isinstance(rendered, str)
                or f"<!-- yatima-k3-view:{semantic_view_id} -->" not in rendered
            ):
                return None
            if request_key not in self.semantic_view_by_request and len(self.semantic_view_by_request) >= 256:
                self.semantic_view_by_request.pop(next(iter(self.semantic_view_by_request)))
            # Cache only after a complete validated compile. A timeout, parse
            # failure, or rejected view never consumes the recovery chance.
            self.semantic_view_by_request[request_key] = semantic_view_id
            return {"context": rendered}
        except Exception:
            return None


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
    return K3HookRuntime(settings=settings, core=core, adapter=adapter)


def register_plugin(ctx: Any) -> None:
    worker_override_present = bool(os.environ.get("YATIMA_K3_WORKER_SETTINGS_JSON"))
    settings = PluginSettings.from_context(ctx)
    if settings is not None:
        try:
            runtime = build_runtime(settings)
        except Exception:
            runtime = None
        if runtime is not None:
            ctx.register_hook("pre_llm_call", runtime.on_pre_llm_call)
    elif not worker_override_present:
        interactive = InteractiveSettings.from_context(ctx)
        if interactive is not None:
            runtime = InteractiveK3Runtime(interactive)
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
