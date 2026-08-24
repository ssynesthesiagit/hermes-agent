"""Small, deterministic policy seam for the protected Yatima boards.

The Kanban database remains the source of task state.  This module owns the
optional Fleet policy database and all of the policy decisions that sit in
front of a native claim.  Ordinary boards deliberately take the fast, legacy
path and do not require a policy file or database.

Fleet policy and pause settings are read from ``config.yaml`` without writes.
Tests may opt into explicit temporary path overrides with
``HERMES_FLEET_POLICY_TEST_OVERRIDES=1``; those overrides are never a
production configuration source.  The policy database is created lazily for
protected-board decisions and contains exactly the three application tables
documented by the Fleet contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


PROTECTED_BOARDS = frozenset(("yatima-portfolio", "yatima-canary"))
_AUTHORITY_ORDER = {f"R{i}": i for i in range(5)}
_PENDING_RECEIPTS: ContextVar[dict[int, str]] = ContextVar(
    "hermes_fleet_pending_receipts", default={}
)


def is_protected_board(board: Optional[str]) -> bool:
    return str(board or "").strip().lower() in PROTECTED_BOARDS


def _connection_protected_board(conn: sqlite3.Connection) -> Optional[str]:
    """Return the protected board owning a connection's main DB, if any."""
    try:
        main = conn.execute(
            "SELECT file FROM pragma_database_list WHERE name = 'main'"
        ).fetchone()
        raw_path = str(main[0] if main else "").strip()
        if not raw_path:
            return None
        actual_path = Path(raw_path).expanduser().resolve(strict=False)
        from hermes_cli import kanban_db

        matches = []
        for candidate in sorted(PROTECTED_BOARDS):
            expected = Path(kanban_db.kanban_db_path(board=candidate)).expanduser().resolve(strict=False)
            if actual_path == expected:
                matches.append(candidate)
        return matches[0] if len(matches) == 1 else None
    except Exception:
        return None


def _resolve_claim_board(conn: sqlite3.Connection, requested: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Resolve board identity and return ``(board, failure_reason)``."""
    requested_name = str(requested or os.environ.get("HERMES_KANBAN_BOARD") or "").strip().lower()
    actual_protected = _connection_protected_board(conn)
    if actual_protected:
        if requested is not None and not is_protected_board(requested_name):
            return None, "board_connection_mismatch"
        if is_protected_board(requested_name) and requested_name != actual_protected:
            return None, "board_connection_mismatch"
        # A default/ordinary current-board value cannot downgrade a protected
        # connection; infer the real board from its canonical DB path.
        return actual_protected, None
    if is_protected_board(requested_name):
        # A protected label must never be used against an ordinary/ambiguous DB.
        return None, "board_connection_mismatch"
    return requested_name or "default", None


def _kanban_home() -> Path:
    # Keep this import lazy: policy import must never perturb ordinary boards.
    from hermes_cli import kanban_db

    return kanban_db.kanban_home()


def policy_db_path() -> Path:
    value = os.environ.get("HERMES_FLEET_POLICY_DB", "").strip()
    if value and os.environ.get("HERMES_FLEET_POLICY_TEST_OVERRIDES") == "1":
        return Path(value).expanduser()
    settings = _config_policy_settings()
    configured = settings.get("database", settings.get("policy_db"))
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else _kanban_home() / path
    return _kanban_home() / "kanban" / "fleet-policy.db"


def _config_policy_settings() -> dict[str, Any]:
    """Read Fleet policy settings from config.yaml without writing it."""
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly()
    except Exception:
        return {}
    value = cfg.get("fleet_policy", cfg.get("fleet", {}).get("policy", {}))
    return dict(value) if isinstance(value, Mapping) else {}


def _load_policy_config() -> dict[str, Any]:
    override = os.environ.get("HERMES_FLEET_POLICY_CONFIG", "").strip()
    if override and os.environ.get("HERMES_FLEET_POLICY_TEST_OVERRIDES") == "1":
        path = Path(override).expanduser()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return {}
        return raw if isinstance(raw, dict) else {}
    configured = _config_policy_settings()
    return configured if configured else {}


def _policy_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS task_policy_receipts (
            receipt_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            board TEXT NOT NULL,
            run_id INTEGER,
            fingerprint TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            route TEXT,
            authority TEXT,
            comment_boundary TEXT,
            execution_envelope TEXT,
            route_identity TEXT,
            created_at INTEGER NOT NULL,
            expires_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS resource_leases (
            resource TEXT PRIMARY KEY,
            lease_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            receipt_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            acquired_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY,
            task_id TEXT,
            fingerprint TEXT NOT NULL,
            authority TEXT,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        """
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(task_policy_receipts)")}
    if "route_identity" not in columns:
        db.execute("ALTER TABLE task_policy_receipts ADD COLUMN route_identity TEXT")


def _open_policy_db() -> sqlite3.Connection:
    path = policy_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise PermissionError(f"unsafe Fleet policy database path: {path}")
    if path.exists():
        owner = getattr(os, "geteuid", lambda: None)()
        if owner is not None and path.stat().st_uid != owner:
            raise PermissionError(f"Fleet policy database is not owned by this user: {path}")
    db = sqlite3.connect(str(path), timeout=2.0)
    db.row_factory = sqlite3.Row
    try:
        os.chmod(path, 0o600)
    except OSError:
        db.close()
        raise
    _policy_schema(db)
    return db


def _normalise(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _normalise(value[k]) for k in sorted(value, key=lambda x: str(x))}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    if isinstance(value, str):
        return value.strip()
    return value


def _canonical(value: Any) -> str:
    return json.dumps(_normalise(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _task_values(task: Any) -> dict[str, Any]:
    names = (
        "id", "title", "body", "assignee", "priority", "created_by",
        "created_at", "workspace_kind", "project_id",
        "tenant", "result", "idempotency_key", "max_runtime_seconds", "workflow_template_id",
        "current_step_key", "skills", "model_override", "provider_override",
        "reasoning_effort", "max_retries", "goal_mode", "goal_max_turns", "session_id",
    )
    if isinstance(task, Mapping):
        return {name: task.get(name) for name in names}
    return {name: getattr(task, name, None) for name in names}


def task_fingerprint(task: Any, comments: list[Any], execution_envelope: Mapping[str, Any]) -> str:
    """Return the stable SHA-256 identity of an execution-relevant task."""
    ordered_comments = []
    for comment in comments:
        if isinstance(comment, Mapping):
            get = comment.get
        else:
            get = lambda key, default=None: getattr(comment, key, default)
        ordered_comments.append({
            "id": get("id"), "author": get("author"), "body": get("body"),
            "created_at": get("created_at"),
        })
    # Workspace path/branch are resolved by the trusted dispatcher after the
    # native claim.  They are therefore carried as an explicit, post-resolution
    # identity and validated at worker start, rather than hashed before the
    # dispatcher has materialized the scratch/worktree workspace.
    envelope = dict(execution_envelope) if isinstance(execution_envelope, Mapping) else {}
    envelope.pop("workspace_identity", None)
    material = {
        "task": _task_values(task),
        "comments": ordered_comments,
        "execution_envelope": _normalise(envelope),
    }
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    receipt_id: Optional[str] = None
    fingerprint: Optional[str] = None
    reason: str = ""
    route: Optional[str] = None
    authority: Optional[str] = None
    comment_boundary: Optional[dict[str, Any]] = None
    execution_envelope: Optional[dict[str, Any]] = None
    mutating: bool = False
    route_identity: Optional[dict[str, str]] = None

    def event_payload(self) -> dict[str, Any]:
        if not self.allowed or not self.receipt_id:
            return {}
        return {
            "policy_receipt_id": self.receipt_id,
            "policy_fingerprint": self.fingerprint,
            "comment_boundary": self.comment_boundary,
        }

    def run_metadata(
        self, run_id: Optional[int] = None, source_status: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        if not self.allowed or not self.receipt_id:
            return None
        fleet = {
            "receipt_id": self.receipt_id,
            "fingerprint": self.fingerprint,
            "route": self.route,
            "authority": self.authority,
            "comment_boundary": self.comment_boundary,
            "execution_envelope": self.execution_envelope,
            "route_identity": self.route_identity,
            "mutating": self.mutating,
            "run_id": run_id,
        }
        if source_status:
            fleet["source_status"] = source_status
        return {
            "fleet_policy": fleet
        }


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, default)


def _ceil(value: Any) -> Optional[int]:
    key = str(value or "").strip().upper()
    return _AUTHORITY_ORDER.get(key)


def _route_capabilities(route: Mapping[str, Any]) -> set[str]:
    values = route.get("capabilities", route.get("capability", []))
    if isinstance(values, str):
        values = [values]
    return {str(v).strip() for v in (values or []) if str(v).strip()}


def _task_policy(config: Mapping[str, Any], task_id: str) -> dict[str, Any]:
    policies = config.get("tasks", config.get("task_policies", {}))
    if not isinstance(policies, Mapping):
        return {}
    value = policies.get(task_id, {})
    return dict(value) if isinstance(value, Mapping) else {}


def _routes(config: Mapping[str, Any]) -> Mapping[str, Any]:
    routes = config.get("routes", config.get("model_routes", {}))
    return routes if isinstance(routes, Mapping) else {}


def _roles(config: Mapping[str, Any]) -> Mapping[str, Any]:
    roles = config.get("roles", {})
    return roles if isinstance(roles, Mapping) else {}


def _allowed_identity_values(route: Mapping[str, Any], singular: str) -> list[str]:
    values = route.get(singular)
    if values is None:
        values = route.get(f"allowed_{singular}s", route.get(f"{singular}s"))
    if isinstance(values, str):
        values = [values]
    return [str(value).strip() for value in (values or []) if str(value).strip()]


def _effective_profile_identity(profile: str) -> tuple[Optional[str], Optional[str]]:
    """Read the effective profile model/provider without mutating config."""
    try:
        from hermes_cli.profiles import get_profile_dir, _read_config_model

        model, provider = _read_config_model(get_profile_dir(profile))
        if isinstance(model, Mapping):
            provider = model.get("provider") or provider
            model = model.get("model") or model.get("default")
        return (
            str(model).strip() if model else None,
            str(provider).strip() if provider else None,
        )
    except Exception:
        return None, None


def _resolved_route_identity(
    route: Mapping[str, Any], task: Any, task_policy: Mapping[str, Any],
) -> Optional[dict[str, str]]:
    """Bind an admitted route to the worker identity the dispatcher starts."""
    nested_identity = route.get("identity")
    if isinstance(nested_identity, Mapping):
        route = {**route, **nested_identity}
    assignee = str(_row_value(task, "assignee", "") or "").strip()
    policy_role = str(task_policy.get("role", assignee) or "").strip()
    profiles = _allowed_identity_values(route, "profile")
    models = _allowed_identity_values(route, "model")
    providers = _allowed_identity_values(route, "provider")
    if not assignee or not policy_role or policy_role != assignee:
        return None
    if not profiles or not models or not providers:
        return None
    if assignee not in profiles:
        return None
    model = str(_row_value(task, "model_override", "") or "").strip()
    provider = str(_row_value(task, "provider_override", "") or "").strip()
    if provider and not model:
        # _default_spawn only passes --provider inside its model-override
        # branch; a provider-only task would execute the profile default.
        return None
    default_model, default_provider = _effective_profile_identity(assignee)
    effective_model = model or default_model
    effective_provider = provider or default_provider
    if not (
        effective_model
        and effective_provider
        and effective_model in models
        and effective_provider in providers
    ):
        return None
    return {
        "profile": assignee,
        "model": effective_model,
        "provider": effective_provider,
    }


def _route_identity_matches(
    route: Mapping[str, Any], task: Any, task_policy: Mapping[str, Any],
) -> bool:
    return _resolved_route_identity(route, task, task_policy) is not None


def _approval_current(db: sqlite3.Connection, task_id: str, fingerprint: str, now: int) -> bool:
    row = db.execute(
        "SELECT 1 FROM approvals WHERE (task_id = ? OR task_id IS NULL) "
        "AND fingerprint = ? AND status IN ('approved', 'current') "
        "AND expires_at >= ? LIMIT 1",
        (task_id, fingerprint, now),
    ).fetchone()
    return row is not None


def _reconcile_stale_receipts(
    native_conn: sqlite3.Connection, policy_db: sqlite3.Connection, now: int,
    board: str,
) -> None:
    """Cancel expired/orphaned bindings left by a process crash.

    Native SQLite and the policy SQLite file cannot commit atomically.  A
    subsequent protected claim therefore reconciles bound receipts on the
    exact same board whose run is absent/non-running (or whose TTL expired),
    cancels them, and releases only their receipt-owned leases.  This bounded
    TTL cleanup is the recovery path for a crash between the two commits.
    """
    rows = policy_db.execute(
        "SELECT receipt_id, task_id, run_id, expires_at FROM task_policy_receipts "
        "WHERE decision = 'allowed' AND board = ?", (board,)
    ).fetchall()
    for row in rows:
        stale = row["expires_at"] is not None and int(row["expires_at"]) < now
        if not stale and row["run_id"] is not None:
            native = native_conn.execute(
                "SELECT r.task_id AS run_task_id, t.status, t.current_run_id, r.status AS run_status "
                "FROM task_runs r LEFT JOIN tasks t ON t.id = r.task_id WHERE r.id = ?",
                (int(row["run_id"]),),
            ).fetchone()
            stale = (
                native is None
                or native["run_task_id"] != row["task_id"]
                or native["run_status"] != "running"
                or native["status"] != "running"
                or int(native["current_run_id"] or 0) != int(row["run_id"])
            )
        if stale:
            policy_db.execute(
                "UPDATE task_policy_receipts SET decision = 'cancelled', reason = ? "
                "WHERE receipt_id = ? AND decision = 'allowed'",
                ("stale_native_binding", row["receipt_id"]),
            )
            policy_db.execute(
                "DELETE FROM resource_leases WHERE receipt_id = ?", (row["receipt_id"],)
            )


def _write_receipt(
    db: sqlite3.Connection,
    *, task_id: str, board: str, fingerprint: str, decision: str, reason: str,
    route: Optional[str], authority: Optional[str], boundary: Mapping[str, Any],
    envelope: Mapping[str, Any], route_identity: Optional[Mapping[str, Any]],
    now: int, expires_at: Optional[int], receipt_id: str,
) -> None:
    db.execute(
        "INSERT INTO task_policy_receipts "
        "(receipt_id, task_id, board, fingerprint, decision, reason, route, authority, "
        "comment_boundary, execution_envelope, route_identity, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (receipt_id, task_id, board, fingerprint, decision, reason, route, authority,
         _canonical(boundary), _canonical(envelope),
         _canonical(route_identity) if route_identity else None, now, expires_at),
    )


def authorize_claim(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    board: Optional[str] = None,
    source_status: str = "ready",
    now: Optional[int] = None,
) -> PolicyDecision:
    """Authorize one native claim; protected-board failures always deny."""
    board_name, board_error = _resolve_claim_board(conn, board)
    if board_error:
        return PolicyDecision(False, reason=board_error)
    board_name = str(board_name or "").strip().lower()
    if not is_protected_board(board_name):
        return PolicyDecision(True, reason="ordinary_board")
    now = int(time.time() if now is None else now)
    receipt_id = uuid.uuid4().hex
    # Use Kanban's normalized Task view so JSON-backed fields (skills, etc.)
    # hash identically at claim and at registration/worker-start time.
    from hermes_cli import kanban_db
    task = kanban_db.get_task(conn, task_id)
    config = _load_policy_config()
    task_policy = _task_policy(config, task_id)
    envelope = task_policy.get("execution_envelope", task_policy.get("envelope", {}))
    envelope = dict(envelope) if isinstance(envelope, Mapping) else {}
    comments = conn.execute(
        "SELECT id, author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY id",
        (task_id,),
    ).fetchall()
    fingerprint = task_fingerprint(task, comments, envelope) if task else ""
    boundary = {
        "through_comment_id": int(comments[-1]["id"]) if comments else 0,
        "comment_count": len(comments),
    }
    route_name = str(task_policy.get("route", envelope.get("route", ""))).strip() or None
    routes = _routes(config)
    route = routes.get(route_name, {}) if route_name else {}
    route = route if isinstance(route, Mapping) else {}
    route_identity = _resolved_route_identity(route, task, task_policy) if task else None
    authority = str(task_policy.get("authority", envelope.get("authority", ""))).strip().upper() or None
    role = str(task_policy.get("role", getattr(task, "assignee", "") or _row_value(task, "assignee", ""))).strip()
    role_cfg = _roles(config).get(role, {})
    role_cfg = role_cfg if isinstance(role_cfg, Mapping) else {}
    route_cap = _ceil(route.get("authority_ceiling", route.get("authority", "")))
    task_cap = _ceil(envelope.get("authority_ceiling", envelope.get("task_authority_ceiling", "")))
    role_cap = _ceil(role_cfg.get("authority_ceiling", role_cfg.get("authority", "")))
    granted = _ceil(authority)
    required_caps = envelope.get("required_capabilities", envelope.get("capabilities", []))
    if isinstance(required_caps, str):
        required_caps = [required_caps]
    required_caps = {str(v).strip() for v in (required_caps or []) if str(v).strip()}
    mutating = bool(envelope.get("mutating", task_policy.get("mutating", False)))
    bounded = bool(envelope.get("bounded", task_policy.get("bounded", False)))
    required_approval = bool(envelope.get("approval_required", task_policy.get("approval_required", False)))
    resources = envelope.get("exclusive_resources", envelope.get("resources", task_policy.get("resources", [])))
    if isinstance(resources, str):
        resources = [resources]
    resources = sorted({str(v.get("name") if isinstance(v, Mapping) else v).strip() for v in (resources or []) if str(v.get("name") if isinstance(v, Mapping) else v).strip()})
    reason = "allowed"
    if not task or not task_policy or not envelope:
        reason = "missing_policy_envelope"
    elif mutating and not bounded:
        reason = "mutating_envelope_unbounded"
    elif config.get("paused") or config.get("pause"):
        reason = "fleet_paused"
    else:
        pause_settings = _config_policy_settings()
        configured_pause = pause_settings.get("pause_file", pause_settings.get("pause_flag"))
        test_pause = ""
        if os.environ.get("HERMES_FLEET_POLICY_TEST_OVERRIDES") == "1":
            test_pause = os.environ.get("HERMES_FLEET_POLICY_TEST_PAUSE_FILE", "").strip()
        pause_value = test_pause or configured_pause
        pause_path = Path(pause_value).expanduser() if pause_value else _kanban_home() / "kanban" / "fleet.pause"
        if not pause_path.is_absolute():
            pause_path = _kanban_home() / pause_path
        if pause_path.exists():
            reason = "fleet_paused"
        elif not route_name or not route or route.get("admitted") is not True:
            reason = "route_not_admitted"
        elif route.get("manual_only") or route.get("candidate_only"):
            reason = "manual_or_candidate_route"
        elif route_identity is None:
            reason = "route_identity_mismatch"
        elif not required_caps.issubset(_route_capabilities(route)):
            reason = "route_capability_missing"
        elif granted is None or task_cap is None or role_cap is None or route_cap is None:
            reason = "authority_ceiling_missing"
        elif granted > task_cap or granted > role_cap or granted > route_cap:
            reason = "authority_exceeded"
        elif task_policy.get("fingerprint") and str(task_policy["fingerprint"]) != fingerprint:
            reason = "fingerprint_mismatch"
        elif task_policy.get("task_fingerprint") and str(task_policy["task_fingerprint"]) != fingerprint:
            reason = "fingerprint_mismatch"
        elif required_approval:
            # Approval lookup is performed below, after opening the policy DB.
            reason = "approval_required"

    expires_at = now + int(task_policy.get("ttl_seconds", 3600) or 3600)
    try:
        db = _open_policy_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            _reconcile_stale_receipts(conn, db, now, board_name)
            if reason == "approval_required":
                reason = "allowed" if _approval_current(db, task_id, fingerprint, now) else "approval_missing_or_expired"
            if reason == "allowed":
                for resource in resources:
                    existing = db.execute(
                        "SELECT task_id, fingerprint, expires_at FROM resource_leases WHERE resource = ?",
                        (resource,),
                    ).fetchone()
                    if existing and int(existing["expires_at"]) >= now and not (
                        existing["task_id"] == task_id and existing["fingerprint"] == fingerprint
                    ):
                        reason = "resource_lease_unavailable"
                        break
            _write_receipt(
                db, task_id=task_id, board=board_name, fingerprint=fingerprint,
                decision="allowed" if reason == "allowed" else "denied", reason=reason,
                route=route_name, authority=authority, boundary=boundary, envelope=envelope,
                route_identity=route_identity,
                now=now, expires_at=expires_at, receipt_id=receipt_id,
            )
            if reason == "allowed":
                for resource in resources:
                    existing = db.execute(
                        "SELECT task_id, fingerprint, expires_at FROM resource_leases WHERE resource = ?",
                        (resource,),
                    ).fetchone()
                    if existing and int(existing["expires_at"]) >= now:
                        # An idempotent retry must not replace another
                        # receipt's lease; cancellation of this receipt must
                        # never release the original owner's lease.
                        continue
                    db.execute(
                        "INSERT OR REPLACE INTO resource_leases "
                        "(resource, lease_id, task_id, receipt_id, fingerprint, acquired_at, expires_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (resource, uuid.uuid4().hex, task_id, receipt_id, fingerprint, now, expires_at),
                    )
            db.commit()
        finally:
            db.close()
    except Exception:
        # Protected policy is fail closed, including policy DB outage or a
        # malformed local policy document that reaches an unexpected branch.
        return PolicyDecision(False, fingerprint=fingerprint, reason="policy_unavailable")
    decision = PolicyDecision(
        reason == "allowed", receipt_id if reason == "allowed" else None,
        fingerprint, reason, route_name, authority, boundary, envelope, mutating,
        route_identity,
    )
    if decision.allowed and getattr(conn, "in_transaction", False):
        register_pending_receipt(conn, decision.receipt_id)
    return decision


def register_pending_receipt(conn: sqlite3.Connection, receipt_id: Optional[str]) -> None:
    """Remember a prepared receipt until the native claim transaction commits."""
    if not receipt_id:
        return
    pending = dict(_PENDING_RECEIPTS.get())
    pending[id(conn)] = str(receipt_id)
    _PENDING_RECEIPTS.set(pending)


def clear_pending_receipt(conn: sqlite3.Connection, receipt_id: Optional[str] = None) -> None:
    pending = dict(_PENDING_RECEIPTS.get())
    current = pending.get(id(conn))
    if receipt_id is None or current == str(receipt_id):
        pending.pop(id(conn), None)
        _PENDING_RECEIPTS.set(pending)


def compensate_pending_receipt(conn: sqlite3.Connection, reason: str) -> None:
    """Best-effort compensation for any native claim exit before commit."""
    pending = dict(_PENDING_RECEIPTS.get())
    receipt_id = pending.pop(id(conn), None)
    _PENDING_RECEIPTS.set(pending)
    if receipt_id:
        try:
            cancel_receipt(receipt_id, reason)
        except Exception:
            # A later TTL/reconciliation pass can recover a policy row when
            # the policy database itself is unavailable during native rollback.
            pass


def bind_receipt_run(receipt_id: str, run_id: int) -> bool:
    """Bind an allowed receipt to its native run; failure is never swallowed."""
    db = _open_policy_db()
    try:
        cur = db.execute(
            "UPDATE task_policy_receipts SET run_id = ? "
            "WHERE receipt_id = ? AND decision = 'allowed'",
            (run_id, receipt_id),
        )
        if cur.rowcount != 1:
            db.rollback()
            return False
        db.commit()
        return True
    finally:
        db.close()


def cancel_receipt(receipt_id: Optional[str], reason: str) -> bool:
    """Cancel a prepared/allowed receipt and release only its leases."""
    if not receipt_id:
        return True
    db = _open_policy_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        cur = db.execute(
            "UPDATE task_policy_receipts SET decision = 'cancelled', reason = ? "
            "WHERE receipt_id = ? AND decision = 'allowed'",
            (reason, receipt_id),
        )
        db.execute("DELETE FROM resource_leases WHERE receipt_id = ?", (receipt_id,))
        db.commit()
        return cur.rowcount == 1
    finally:
        db.close()


def refresh_workspace_identity(
    conn: sqlite3.Connection,
    task_id: str,
    run_id: Optional[int],
    board: Optional[str],
    workspace: str,
    branch: Optional[str],
) -> bool:
    """Bind the dispatcher-resolved workspace after native setters commit.

    Workspace path/branch are mutable while resolving a claim, so they are
    excluded from the stable fingerprint and then recorded explicitly in both
    the native run metadata and the policy receipt.  Every other fingerprint
    input is recomputed before the refresh; a mismatch compensates the receipt.
    """
    board_name = str(board or "").strip().lower()
    if not is_protected_board(board_name) or run_id is None:
        return not is_protected_board(board_name)
    from hermes_cli import kanban_db

    receipt_id: Optional[str] = None
    try:
        task = kanban_db.get_task(conn, task_id)
        run = conn.execute(
            "SELECT metadata FROM task_runs WHERE id = ? AND task_id = ?",
            (int(run_id), task_id),
        ).fetchone()
        metadata = json.loads(run["metadata"]) if run and run["metadata"] else {}
        fleet = metadata.get("fleet_policy") if isinstance(metadata, Mapping) else None
        if not task or not isinstance(fleet, Mapping):
            raise RuntimeError("protected run metadata missing")
        receipt_id = str(fleet.get("receipt_id") or "") or None
        envelope = fleet.get("execution_envelope")
        if not isinstance(envelope, Mapping):
            raise RuntimeError("protected execution envelope missing")
        comments = conn.execute(
            "SELECT id, author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        if task_fingerprint(task, comments, envelope) != str(fleet.get("fingerprint") or ""):
            raise RuntimeError("protected fingerprint changed before workspace binding")
        identity = {
            "path": str(Path(workspace).expanduser().resolve(strict=False)),
            "branch": str(branch or "").strip(),
        }
        bound_envelope = dict(envelope)
        bound_envelope["workspace_identity"] = identity
        bound_metadata = json.loads(json.dumps(metadata, ensure_ascii=False))
        bound_fleet = bound_metadata["fleet_policy"]
        bound_fleet["execution_envelope"] = bound_envelope
        bound_fleet["workspace_identity"] = identity

        policy_db = _open_policy_db()
        try:
            policy_db.execute("BEGIN IMMEDIATE")
            row = policy_db.execute(
                "SELECT decision, task_id, board, run_id, fingerprint FROM task_policy_receipts WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
            if not row or row["decision"] != "allowed" or row["task_id"] != task_id \
                    or row["board"] != board_name or int(row["run_id"] or 0) != int(run_id) \
                    or row["fingerprint"] != str(fleet.get("fingerprint") or ""):
                policy_db.rollback()
                raise RuntimeError("protected receipt changed before workspace binding")
            cur = policy_db.execute(
                "UPDATE task_policy_receipts SET execution_envelope = ? WHERE receipt_id = ?",
                (_canonical(bound_envelope), receipt_id),
            )
            if cur.rowcount != 1:
                policy_db.rollback()
                raise RuntimeError("protected receipt workspace binding failed")
            policy_db.commit()
        finally:
            policy_db.close()

        with kanban_db.write_txn(conn):
            current = conn.execute(
                "SELECT status, current_run_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not current or current["status"] != "running" or int(current["current_run_id"] or 0) != int(run_id):
                raise RuntimeError("native run changed during workspace binding")
            conn.execute(
                "UPDATE task_runs SET metadata = ? WHERE id = ? AND task_id = ?",
                (json.dumps(bound_metadata, ensure_ascii=False), int(run_id), task_id),
            )
        return True
    except Exception:
        if receipt_id:
            try:
                cancel_receipt(receipt_id, "workspace_identity_failed")
            except Exception:
                pass
        return False


def worker_start_recheck(
    conn: sqlite3.Connection, task_id: str, run_id: Optional[int], metadata: Optional[Mapping[str, Any]],
    *, board: Optional[str] = None,
) -> bool:
    """Recompute trusted identity before an agent/tool snapshot is built."""
    fleet = metadata.get("fleet_policy") if isinstance(metadata, Mapping) else None
    if not isinstance(fleet, Mapping):
        fleet = {}
    board_name = str(board or os.environ.get("HERMES_KANBAN_BOARD") or "").strip().lower()
    protected = is_protected_board(board_name)
    if not protected and not fleet:
        return True
    from hermes_cli import kanban_db
    task = kanban_db.get_task(conn, task_id)
    if run_id is None and task:
        active = conn.execute(
            "SELECT current_run_id FROM tasks WHERE id = ? AND status = 'running'",
            (task_id,),
        ).fetchone()
        if active and active["current_run_id"] is not None:
            run_id = int(active["current_run_id"])
    native_run = None
    native_binding_ok = False
    if run_id is not None:
        native_run = conn.execute(
            "SELECT task_id, status, ended_at, claim_lock, claim_expires FROM task_runs WHERE id = ?",
            (int(run_id),),
        ).fetchone()
        native_binding_ok = bool(
            task
            and task.status == "running"
            and int(task.current_run_id or 0) == int(run_id)
            and native_run
            and native_run["task_id"] == task_id
            and native_run["status"] == "running"
            and native_run["ended_at"] is None
            and str(native_run["claim_lock"] or "") == str(task.claim_lock or "")
            and int(native_run["claim_expires"] or 0) == int(task.claim_expires or 0)
        )
    candidate_source = str(fleet.get("source_status") or "").strip().lower()
    claimed = None
    event_source = "ready"
    if run_id is not None:
        claimed = conn.execute(
            "SELECT payload FROM task_events WHERE task_id = ? AND run_id = ? "
            "AND kind = 'claimed' ORDER BY id DESC LIMIT 1",
            (task_id, int(run_id)),
        ).fetchone()
        try:
            payload = json.loads(claimed["payload"]) if claimed and claimed["payload"] else {}
            if payload.get("source_status") in {"ready", "review"}:
                event_source = payload["source_status"]
        except (TypeError, ValueError):
            pass
    # The durable claimed event is authoritative for the lane.  Metadata must
    # repeat it exactly; a tampered/missing value fails closed but releases to
    # the event-derived source lane rather than trusting the tampered value.
    source_status = event_source
    lane_matches = candidate_source in {"ready", "review"} and candidate_source == event_source
    receipt_id = str(fleet.get("receipt_id") or "") or None
    if run_id is not None:
        claimed_row = conn.execute(
            "SELECT payload FROM task_events WHERE task_id = ? AND run_id = ? "
            "AND kind = 'claimed' ORDER BY id DESC LIMIT 1",
            (task_id, int(run_id)),
        ).fetchone()
        try:
            claimed_payload = json.loads(claimed_row["payload"]) if claimed_row and claimed_row["payload"] else {}
            event_receipt = str(claimed_payload.get("policy_receipt_id") or "") or None
            if event_receipt:
                # The native claimed event is the durable binding for this
                # run; prefer it when metadata is missing or tampered.
                receipt_id = event_receipt
        except (TypeError, ValueError):
            pass
    comments = conn.execute(
        "SELECT id, author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY id", (task_id,)
    ).fetchall()
    envelope = fleet.get("execution_envelope", {})
    actual = task_fingerprint(task, comments, envelope if isinstance(envelope, Mapping) else {})
    expected = str(fleet.get("fingerprint") or "")
    board = board_name
    identity = fleet.get("workspace_identity")
    workspace_ok = False
    if isinstance(identity, Mapping) and task:
        try:
            actual_path = str(Path(task.workspace_path or "").expanduser().resolve(strict=False))
            workspace_ok = (
                bool(identity.get("path"))
                and actual_path == str(identity.get("path"))
                and str(identity.get("branch") or "").strip() == str(task.branch_name or "").strip()
            )
        except (OSError, RuntimeError, TypeError):
            workspace_ok = False
    route_identity = fleet.get("route_identity")
    route_ok = False
    if task and isinstance(route_identity, Mapping):
        try:
            config = _load_policy_config()
            task_policy = _task_policy(config, task_id)
            routes = _routes(config)
            route_cfg = routes.get(str(fleet.get("route") or ""), {})
            fresh_route_identity = (
                _resolved_route_identity(route_cfg, task, task_policy)
                if isinstance(route_cfg, Mapping) else None
            )
            route_ok = (
                fresh_route_identity is not None
                and _canonical(fresh_route_identity) == _canonical(route_identity)
            )
        except Exception:
            route_ok = False
    good = bool(
        task
        and run_id is not None
        and actual == expected
        and int(fleet.get("run_id") or 0) == int(run_id)
        and is_protected_board(board)
        and workspace_ok
        and lane_matches
        and route_ok
        and native_binding_ok
    )
    if good:
        try:
            db = _open_policy_db()
            try:
                row = db.execute(
                    "SELECT decision, task_id, board, run_id, fingerprint, expires_at, execution_envelope, route_identity "
                    "FROM task_policy_receipts WHERE receipt_id = ?",
                    (fleet.get("receipt_id"),),
                ).fetchone()
            finally:
                db.close()
            try:
                receipt_envelope = json.loads(row["execution_envelope"]) if row else None
            except (TypeError, ValueError):
                receipt_envelope = None
            try:
                receipt_route_identity = json.loads(row["route_identity"]) if row and row["route_identity"] else None
            except (TypeError, ValueError):
                receipt_route_identity = None
            good = bool(
                row
                and row["decision"] == "allowed"
                and row["task_id"] == task_id
                and row["board"] == board
                and int(row["run_id"] or 0) == int(run_id)
                and row["fingerprint"] == actual
                and isinstance(receipt_envelope, Mapping)
                and _canonical(receipt_envelope) == _canonical(envelope if isinstance(envelope, Mapping) else {})
                and isinstance(receipt_route_identity, Mapping)
                and _canonical(receipt_route_identity) == _canonical(route_identity)
                and (row["expires_at"] is None or int(row["expires_at"]) >= int(time.time()))
            )
        except Exception:
            good = False
    if good:
        return True
    # Safe release: this worker has not created an agent, so return the task to
    # its source lane and close only the run that it was handed.
    try:
        with conn:
            row = conn.execute("SELECT current_run_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row and int(row["current_run_id"] or 0) == int(run_id or 0):
                conn.execute(
                    "UPDATE tasks SET status = ?, claim_lock = NULL, claim_expires = NULL, "
                    "worker_pid = NULL, current_run_id = NULL WHERE id = ? AND status = 'running'",
                    (source_status, task_id),
                )
                conn.execute(
                    "UPDATE task_runs SET status = 'released', outcome = 'NEEDS_REVALIDATION', "
                    "error = ?, ended_at = ? WHERE id = ? AND ended_at IS NULL",
                    ("trusted task metadata mismatch", int(time.time()), int(run_id)),
                )
                conn.execute(
                    "INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                    (task_id, int(run_id), "needs_revalidation", json.dumps({"reason": "fingerprint_mismatch"}), int(time.time())),
                )
    except Exception:
        pass
    finally:
        try:
            cancel_receipt(receipt_id, "worker_revalidation_failed")
        except Exception:
            pass
    return False


def worker_start_guard(
    task_id: str, run_id: Optional[int], *, board: Optional[str] = None,
) -> bool:
    """Load the durable run envelope and gate CLI construction."""
    from hermes_cli import kanban_db

    conn = kanban_db.connect(board=board)
    try:
        metadata = None
        if run_id is not None:
            row = conn.execute(
                "SELECT metadata FROM task_runs WHERE id = ?", (int(run_id),)
            ).fetchone()
            if row and row["metadata"]:
                try:
                    metadata = json.loads(row["metadata"])
                except (TypeError, ValueError):
                    metadata = None
        return worker_start_recheck(conn, task_id, run_id, metadata, board=board)
    finally:
        conn.close()


def live_comment_steering_allowed() -> bool:
    if os.environ.get("HERMES_KANBAN_MUTATING_RUN", "").strip() == "1":
        return False
    raw = os.environ.get("HERMES_KANBAN_POLICY_METADATA", "").strip()
    if not raw:
        return True
    try:
        fleet = json.loads(raw).get("fleet_policy", {})
    except (TypeError, ValueError):
        return False
    return not (isinstance(fleet, Mapping) and bool(fleet.get("mutating")))
