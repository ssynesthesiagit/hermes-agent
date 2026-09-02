"""Prepare a task-scoped K3 capsule and live integrity state for one worker.

This is the thin Hermes-to-portable-core seam.  It runs in the trusted
dispatcher before the model worker starts, emits only public K3/issuer data,
and never writes a private signing key.  The worker plugin creates its
component key inside the constrained plugin process and exposes only typed
observations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")


class YatimaWorkerIntegrityError(RuntimeError):
    """The protected worker bundle could not be prepared."""


def _safe_id(value: Any, label: str) -> str:
    text = str(value or "")
    if not _SAFE_ID.fullmatch(text):
        raise YatimaWorkerIntegrityError(f"{label} is not a safe identifier")
    return text


def _load_package(root: Path, package: str) -> Any:
    package_file = root / package / "__init__.py"
    if not root.is_absolute() or not package_file.is_file():
        raise YatimaWorkerIntegrityError(f"configured {package} core is missing")
    loaded = sys.modules.get(package)
    if loaded is not None:
        loaded_file = Path(str(getattr(loaded, "__file__", ""))).resolve(strict=False)
        if loaded_file != package_file.resolve():
            raise YatimaWorkerIntegrityError(f"{package} is already loaded from another source")
        return loaded
    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    importlib.invalidate_caches()
    return importlib.import_module(package)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.parent.is_symlink():
        raise YatimaWorkerIntegrityError("worker state path may not be a symlink")
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def prepare_worker_security(
    *,
    task: Any,
    profile: str,
    env: dict[str, str],
    settings: Mapping[str, Any],
    board: str,
) -> Mapping[str, Any]:
    """Create exact per-run public artifacts and inject one-shot child config."""

    if settings.get("worker_bundle_enabled") is not True:
        return {"activated": False, "reason": "worker bundle disabled"}
    task_id = _safe_id(task.id, "task id")
    run_id = _safe_id(task.current_run_id, "run id")
    profile_id = _safe_id(profile, "profile")
    attempt_id = _safe_id(f"{task_id}:run:{run_id}", "attempt id")
    body = task.body or ""
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    try:
        envelope = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise YatimaWorkerIntegrityError("worker task body is not valid JSON") from exc
    if not isinstance(envelope, Mapping):
        raise YatimaWorkerIntegrityError("worker task body must be an object")

    goal = envelope.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        goal = task.title
    project_scope = envelope.get("project") or task.project_id or "yatima"
    project_scope = _safe_id(str(project_scope).replace("/", ":"), "project scope")
    host_id = _safe_id(os.uname().nodename, "host id")
    policy_generation = _safe_id(
        settings.get("policy_generation", "yatima-runtime-integrity-v1.1"),
        "policy generation",
    )
    now = _utc_now()
    lifetime = max(3600, int(task.max_runtime_seconds or 3600) + 3600)
    expires = now + timedelta(seconds=lifetime)
    scope_values = {
        "project_scope": project_scope,
        "role_scope": profile_id,
        "profile_or_agent": profile_id,
        "host_id": host_id,
        "session_id": f"kanban:{task_id}:run:{run_id}",
        "task_id": task_id,
        "attempt_id": attempt_id,
        "task_fingerprint": body_hash,
        "policy_generation": policy_generation,
        "source_generation": body_hash,
        "privacy_class": "owner-private",
        "authorization_state": "AUTHORIZED",
        "task_current": True,
        "attempt_current": True,
        "cancelled": False,
        "superseded": False,
        "stale_reason": None,
    }

    k3_root = Path(str(settings.get("core_path", ""))).expanduser().resolve(strict=False)
    k3 = _load_package(k3_root, "yatima_k3")
    scope = k3.ScopeBindings(**scope_values)
    recorded_at = _iso(now)
    source_locator = f"kanban://{board}/{task_id}"
    constraints = envelope.get("constraints")
    if not isinstance(constraints, list):
        constraints = []
    records = [
        k3.ClassifiedInput(
            kind="OWNER_REQUEST",
            record_id=f"owner-{task_id}",
            payload={
                "objective": goal,
                "exact_source_locator": source_locator,
                "exact_source_revision": run_id,
                "exact_source_hash": body_hash,
            },
            recorded_at=recorded_at,
            source_locator=source_locator,
            source_revision=run_id,
            source_hash=body_hash,
        ),
        k3.ClassifiedInput(
            kind="CURRENT_TASK_RECORD",
            record_id=f"task-{task_id}",
            payload={
                "status": "running",
                "phase": "owner-dispatched",
                "active_constraints": [str(item) for item in constraints if isinstance(item, str)],
                "prohibitions": [
                    "autonomous follow-on jobs",
                    "scope expansion without owner review",
                    "automatic main-branch writes",
                ],
            },
            recorded_at=recorded_at,
        ),
        k3.ClassifiedInput(
            kind="CURRENT_ATTEMPT_RECORD",
            record_id=f"attempt-{task_id}-{run_id}",
            payload={"status": "running", "attempt_current": True},
            recorded_at=recorded_at,
        ),
        k3.ClassifiedInput(
            kind="AUTHORIZATION_RECORD",
            record_id=f"authorization-{task_id}-{run_id}",
            payload={
                "authorization": {
                    "state": "OWNER_INITIATED_ONLY",
                    "write_authority": envelope.get("writeAuthority", "NONE"),
                    "max_active_jobs": 1,
                },
                "prohibited_actions": [
                    "AUTONOMOUS_JOB_DISPATCH",
                    "SELF_INITIATED_JOBS",
                    "SCHEDULED_JOB_CREATION",
                    "RECURSIVE_JOB_SPAWNING",
                    "UNBOUNDED_EXECUTION",
                ],
            },
            recorded_at=recorded_at,
        ),
    ]
    done_when = envelope.get("doneWhen")
    if isinstance(done_when, str) and done_when.strip():
        records.append(
            k3.ClassifiedInput(
                kind="EXPLICIT_NEXT_ACTION",
                record_id=f"done-when-{task_id}",
                payload={"next_action": done_when},
                recorded_at=recorded_at,
            )
        )
    compiled = k3.compile_capsule(
        k3.CompilationInputs(scope=scope, records=records),
        k3.CompilerConfig(
            generated_at=recorded_at,
            expires_at=_iso(expires),
            compiler_version="yatima-k3-hermes-worker-v1.1",
            repository="ssynesthesiagit/YatimaHarness",
            commit=str(settings.get("source_commit", "runtime-integrity-v1.1")),
            config_hash=hashlib.sha256(
                json.dumps(scope_values, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        ),
    )

    profile_home = Path(env["HERMES_HOME"]).expanduser().resolve(strict=False)
    bundle_root = profile_home / "k3" / "worker" / task_id / run_id
    capsule_path = bundle_root / "capsule.json"
    receipts_path = bundle_root / "receipts.json"
    cache_path = bundle_root / "cache.json"
    _atomic_json(capsule_path, compiled.capsule.as_dict())
    _atomic_json(receipts_path, compiled.receipts.to_dict())
    _atomic_json(cache_path, compiled.cache_binding.to_dict())
    k3_settings = {
        "enabled": True,
        "core_path": str(k3_root),
        "capsule_path": str(capsule_path),
        "receipt_path": str(receipts_path),
        "cache_path": str(cache_path),
        "now": recorded_at,
        "scope": scope_values,
        "runtime_identity": compiled.capsule.runtime_identity,
        "compiler_version": "yatima-k3-hermes-worker-v1.1",
        "schema_generation": "yatima.k3.v1",
        "context_required_fields": ["task_id"],
    }
    env["YATIMA_K3_WORKER_SETTINGS_JSON"] = json.dumps(k3_settings, separators=(",", ":"))

    integrity_core_root = Path(str(settings.get("integrity_core_path", ""))).expanduser().resolve(strict=False)
    integrity = _load_package(integrity_core_root, "yatima_runtime_integrity")
    state_root = Path(
        str(settings.get("integrity_state_root", profile_home / "runtime-integrity-v1.1"))
    ).expanduser().resolve(strict=False)
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    ledger_path = state_root / "integrity-ledger.sqlite3"
    if not ledger_path.exists():
        integrity.IntegrityLedger.initialize(ledger_path).close()
    registry_path = state_root / "issuers-public.json"
    if not registry_path.exists():
        _atomic_json(registry_path, {"issuers": []})
    quarantine_root = state_root / "quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    integrity_config = {
        "enabled": True,
        "trusted": True,
        "receipt_mode": "host_observed",
        "worker_mode": True,
        "core_path": str(integrity_core_root),
        "issuer_registry_path": str(registry_path),
        "ledger_path": str(ledger_path),
        "quarantine_root": str(quarantine_root),
        "issuer_id": f"hermes-{profile_id}",
        "policy_generation": policy_generation,
        "scope": scope_values,
        "runtime_identity": {
            "capability_certificate": f"owner-envelope-{body_hash[:16]}",
        },
    }
    env["YATIMA_INTEGRITY_WORKER_CONFIG_JSON"] = json.dumps(
        integrity_config, separators=(",", ":")
    )
    return {
        "activated": True,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "capsule_path": str(capsule_path),
        "ledger_path": str(ledger_path),
        "registry_path": str(registry_path),
        "quarantine_root": str(quarantine_root),
        "private_key_material_persisted": False,
    }


__all__ = ["YatimaWorkerIntegrityError", "prepare_worker_security"]
