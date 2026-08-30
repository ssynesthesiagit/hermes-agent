"""In-process A2A routing to another local Hermes profile.

This module deliberately keeps the local route separate from the HTTP peer
transport in :mod:`plugins.platforms.a2a.tools`.  The gateway handlers are
looked up lazily so a normal A2A client import does not start or reconfigure a
gateway.  Tests can replace the handler seam with :func:`set_gateway_for_tests`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from . import protocol, security


MAX_LOCAL_ROUTE_DEPTH = 3
LOCAL_ROUTE_TIMEOUT_SECONDS = 180.0
LOCAL_ROUTE_HARD_TIMEOUT_SECONDS = 1200.0
_BOT_CHAT_TITLE = "Bot Chat"
_CONTEXT_PREFIX = "local-a2a-v1."
_MAX_CONTEXT_LENGTH = 384
_MARKER_PREFIX = "[HERMES_LOCAL_A2A v1 "
_PROFILE_RE_TEXT = r"[a-z0-9][a-z0-9_-]{0,63}"
_MARKER_RE = re.compile(
    rf"^\[HERMES_LOCAL_A2A v1 profile=({_PROFILE_RE_TEXT}) "
    rf"depth=([1-9]) chain=({_PROFILE_RE_TEXT}(?:>{_PROFILE_RE_TEXT}){{0,3}}) "
    rf"sig=([0-9a-f]{{32}})\]$"
)

_marker_secret = secrets.token_bytes(32)
_gateway_override: Any = None
_route_locks: dict[tuple[str, str], threading.Lock] = {}
_route_locks_guard = threading.Lock()


class LocalRouteError(RuntimeError):
    """A safe, user-facing local-route failure."""


class GatewayLike(Protocol):
    def call(self, method: str, params: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class _Target:
    profile: str
    stored_id: str
    runtime_id: str
    baseline_messages: tuple[dict[str, Any], ...]
    needs_pin: bool = False


@dataclass(frozen=True)
class _RouteMarker:
    profile: str
    depth: int
    chain: tuple[str, ...]


def set_gateway_for_tests(gateway: Any) -> None:
    """Install a fake gateway for behavior tests; never used by production."""
    global _gateway_override
    _gateway_override = gateway


def _gateway_call(method: str, params: dict[str, Any]) -> Any:
    gateway = _gateway_override
    if gateway is not None:
        if callable(gateway):
            response = gateway(method, params)
        else:
            response = gateway.call(method, params)
    else:
        # Importing the server is intentionally deferred until a local route is
        # actually requested.  No process, socket, token, or HERMES_HOME
        # mutation is involved: these are the existing in-process RPC handlers.
        from tui_gateway import server

        response = server.handle_request(
            {"jsonrpc": "2.0", "id": secrets.token_hex(8), "method": method, "params": params}
        )
    if isinstance(response, dict) and response.get("error"):
        error = response.get("error") or {}
        raise LocalRouteError(str(error.get("message") or "gateway request failed"))
    if isinstance(response, dict) and "result" in response:
        return response["result"]
    return response


def _profile_parts(agent: str) -> str | None:
    """Return a canonical profile name only for the explicit profile: form."""
    if not agent.startswith("profile:"):
        return None
    raw = agent[len("profile:") :].strip()
    if not raw:
        raise LocalRouteError("profile target is empty")
    try:
        from hermes_cli import profiles

        canonical = profiles.normalize_profile_name(raw)
        profiles.validate_profile_name(canonical)
        if not profiles.profile_exists(canonical):
            raise LocalRouteError(f"unknown local profile '{canonical}'")
        return canonical
    except LocalRouteError:
        raise
    except (ValueError, TypeError) as exc:
        raise LocalRouteError(f"invalid local profile '{raw}'") from exc
    except Exception as exc:
        raise LocalRouteError("could not validate local profile") from exc


def is_local_agent(agent: str) -> bool:
    return str(agent or "").startswith("profile:")


def _active_profile() -> str:
    try:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
        return home.name if home.parent.name == "profiles" else "default"
    except Exception as exc:
        raise LocalRouteError("could not determine the caller profile") from exc


def _handle(profile: str) -> str:
    return "hermes" if profile == "default" else profile


def _message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return str(message or "")
    content = message.get("content")
    if isinstance(content, str):
        return content
    text = message.get("text")
    if isinstance(text, str):
        return text
    parts = message.get("parts")
    if isinstance(parts, list):
        chunks = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        return "\n".join(chunks)
    return ""


def _messages_from(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    messages = value.get("messages")
    return [m for m in messages if isinstance(m, dict)] if isinstance(messages, list) else []


def _role(message: dict[str, Any]) -> str:
    return str(message.get("role") or "").strip().lower()


def _marker_payload(profile: str, depth: int, chain: tuple[str, ...]) -> str:
    return f"v1|{profile}|{depth}|{'>'.join(chain)}"


def _make_marker(profile: str, depth: int, chain: tuple[str, ...]) -> str:
    payload = _marker_payload(profile, depth, chain)
    sig = hmac.new(_marker_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{_MARKER_PREFIX}profile={profile} depth={depth} chain={'>'.join(chain)} sig={sig}]"


def _parse_marker(text: str) -> _RouteMarker | None:
    candidates = [line.strip() for line in str(text or "").splitlines() if _MARKER_PREFIX in line]
    if not candidates:
        return None
    # A marker-looking line that is not exactly ours is an explicit deny.  It
    # can never be used to increase routing authority or reset a chain.
    candidate = next((line for line in candidates if line.startswith(_MARKER_PREFIX)), "")
    match = _MARKER_RE.fullmatch(candidate)
    if not match:
        raise LocalRouteError("invalid local routing marker")
    profile, depth_text, chain_text, signature = match.groups()
    depth = int(depth_text)
    chain = tuple(chain_text.split(">"))
    if len(chain) != depth + 1 or chain[-1] != profile or len(set(chain)) != len(chain):
        raise LocalRouteError("invalid local routing marker")
    expected = hmac.new(
        _marker_secret,
        _marker_payload(profile, depth, chain).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    if not hmac.compare_digest(signature, expected):
        raise LocalRouteError("invalid local routing marker")
    return _RouteMarker(profile=profile, depth=depth, chain=chain)


def _caller_messages_from_store(profile: str, session_id: str) -> list[dict[str, Any]]:
    """Read a durable caller when ``session_id`` is not a live gateway id."""
    if not session_id:
        return []
    db = None
    try:
        from hermes_cli.profiles import get_profile_dir
        from hermes_state import SessionDB

        db = SessionDB(db_path=get_profile_dir(profile) / "state.db")
        messages = db.get_messages_as_conversation(
            session_id, include_ancestors=True
        )
        return [message for message in messages if isinstance(message, dict)]
    except Exception:
        return []
    finally:
        if db is not None and hasattr(db, "close"):
            try:
                db.close()
            except Exception:
                pass


def _caller_marker(session_id: str, profile: str) -> _RouteMarker | None:
    if not session_id:
        return None
    messages: list[dict[str, Any]] = []
    try:
        result = _gateway_call("session.history", {"session_id": session_id})
        messages = _messages_from(result)
    except LocalRouteError:
        pass
    if not messages:
        messages = _caller_messages_from_store(profile, session_id)
    for message in reversed(messages):
        if _role(message) in {"user", "human"}:
            return _parse_marker(_message_text(message))
    return None


def _read_ui_meta(profile: str) -> dict[str, Any]:
    try:
        import yaml
        from hermes_cli.profiles import get_profile_dir

        path = get_profile_dir(profile) / "profile.yaml"
        if not path.is_file():
            return {}
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        meta = raw.get("ui_meta") if isinstance(raw, dict) else None
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def _resume(profile: str, stored_id: str) -> _Target:
    result = _gateway_call(
        "session.resume",
        {"profile": profile, "session_id": stored_id, "defer_history": False},
    )
    if not isinstance(result, dict):
        raise LocalRouteError("local profile returned an invalid session")
    if bool(result.get("running")):
        raise LocalRouteError(f"local profile '{profile}' is busy")
    runtime_id = str(result.get("session_id") or "").strip()
    resolved_id = str(result.get("session_key") or result.get("resumed") or stored_id).strip()
    if not runtime_id or not resolved_id:
        raise LocalRouteError("local profile returned no session id")
    return _Target(profile, resolved_id, runtime_id, tuple(_messages_from(result)))


def _resolve_target(profile: str, forced_id: str = "") -> _Target:
    meta = _read_ui_meta(profile)
    block = meta.get("hermes-bots") if isinstance(meta.get("hermes-bots"), dict) else {}
    pinned = str(block.get("chat") or "").strip()
    if forced_id:
        try:
            target = _resume(profile, forced_id)
            if pinned:
                # Compression can rotate the context's stored id while the
                # canonical pin still names its ancestor.  Resolve both
                # durable ids through the gateway and accept the context only
                # when they converge on the same live tip.  The runtime ids
                # are deliberately not compared: each resume gets its own
                # in-process handle for one durable conversation.
                pinned_target = _resume(profile, pinned)
                if pinned_target.stored_id != target.stored_id:
                    raise LocalRouteError("stale or mismatched local context")
            else:
                # A context is not a capability to address an arbitrary
                # session.  If the profile has not persisted its canonical
                # pin yet, discover an existing hidden Bot Chat and require
                # the supplied id to resolve to that same durable tip.  Do
                # not create a session on this validation-only path.
                listed = _gateway_call(
                    "session.list",
                    {"profile": profile, "include_hidden": True, "limit": 200},
                )
                sessions = listed.get("sessions", []) if isinstance(listed, dict) else []
                canonical = None
                for row in sessions:
                    if not isinstance(row, dict) or str(row.get("title") or "").strip() != _BOT_CHAT_TITLE:
                        continue
                    sid = str(row.get("id") or "").strip()
                    if not sid:
                        continue
                    try:
                        canonical = _resume(profile, sid)
                        break
                    except LocalRouteError:
                        continue
                if canonical is None or canonical.stored_id != target.stored_id:
                    raise LocalRouteError("stale or mismatched local context")
        except LocalRouteError as exc:
            raise LocalRouteError("stale or mismatched local context") from exc
        return target

    if pinned:
        try:
            return _resume(profile, pinned)
        except LocalRouteError:
            # A deleted/rotated pin is recoverable through the title search.
            pass

    try:
        listed = _gateway_call(
            "session.list",
            {"profile": profile, "include_hidden": True, "limit": 200},
        )
    except LocalRouteError:
        listed = {}
    sessions = listed.get("sessions", []) if isinstance(listed, dict) else []
    for row in sessions:
        if not isinstance(row, dict) or str(row.get("title") or "").strip() != _BOT_CHAT_TITLE:
            continue
        sid = str(row.get("id") or "").strip()
        if sid:
            try:
                target = _resume(profile, sid)
                return _Target(
                    target.profile,
                    target.stored_id,
                    target.runtime_id,
                    target.baseline_messages,
                    needs_pin=True,
                )
            except LocalRouteError:
                continue

    created = _gateway_call(
        "session.create",
        {"profile": profile, "title": _BOT_CHAT_TITLE, "hidden": True, "source": "a2a"},
    )
    if not isinstance(created, dict):
        raise LocalRouteError("local profile failed to create Bot Chat")
    runtime_id = str(created.get("session_id") or "").strip()
    stored_id = str(created.get("stored_session_id") or created.get("session_key") or "").strip()
    if not runtime_id or not stored_id:
        raise LocalRouteError("local profile returned no created session id")
    return _Target(profile, stored_id, runtime_id, tuple(_messages_from(created)), needs_pin=True)


def _acquire_lock(profile: str, stored_id: str) -> tuple[threading.Lock, bool]:
    key = (profile, stored_id)
    with _route_locks_guard:
        lock = _route_locks.setdefault(key, threading.Lock())
    return lock, lock.acquire(blocking=False)


def _release_lock(profile: str, stored_id: str, lock: threading.Lock) -> None:
    try:
        lock.release()
    finally:
        key = (profile, stored_id)
        with _route_locks_guard:
            if _route_locks.get(key) is lock:
                _route_locks.pop(key, None)


def _timeout_seconds() -> float:
    try:
        configured = float(LOCAL_ROUTE_TIMEOUT_SECONDS)
        hard_cap = float(LOCAL_ROUTE_HARD_TIMEOUT_SECONDS)
    except (TypeError, ValueError, OverflowError):
        configured = LOCAL_ROUTE_TIMEOUT_SECONDS
        hard_cap = 1200.0
    if not math.isfinite(configured):
        configured = LOCAL_ROUTE_TIMEOUT_SECONDS
    if not math.isfinite(hard_cap):
        hard_cap = 1200.0
    hard_cap = max(0.05, hard_cap)
    return max(0.05, min(configured, hard_cap))


def _status_is_running(status: Any) -> bool:
    if not isinstance(status, dict):
        return False
    if bool(status.get("running")):
        return True
    return str(status.get("status") or "").strip().lower() in {
        "busy",
        "processing",
        "running",
        "streaming",
    }


def _extract_reply(messages: list[dict[str, Any]], baseline: int) -> str:
    # Only assistant text is returned. Tool calls, reasoning, and arbitrary
    # metadata never leave the local route.
    candidates = messages[max(0, baseline) :]
    for message in reversed(candidates):
        if _role(message) in {"assistant", "agent"}:
            text = _message_text(message).strip()
            if text:
                return text
    return ""


def _wait_for_reply(target: _Target) -> str:
    started = time.monotonic()
    base_timeout = _timeout_seconds()
    hard_deadline = started + max(0.05, float(LOCAL_ROUTE_HARD_TIMEOUT_SECONDS))
    deadline = min(started + base_timeout, hard_deadline)
    while time.monotonic() < hard_deadline:
        try:
            history = _gateway_call("session.history", {"profile": target.profile, "session_id": target.runtime_id})
            messages = _messages_from(history)
        except LocalRouteError:
            if not target.needs_pin:
                raise
            messages = []
        try:
            status = _gateway_call(
                "session.resume",
                {"profile": target.profile, "session_id": target.stored_id, "defer_history": False},
            )
        except LocalRouteError:
            # A newly created session is not durable until the first turn has
            # flushed. Keep polling that bounded window; stale existing chats
            # fail immediately through the non-created branch above.
            if not target.needs_pin:
                raise
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
            continue
        if isinstance(status, dict):
            running = _status_is_running(status)
            if not running:
                status_messages = _messages_from(status)
                reply = _extract_reply(messages or status_messages, len(target.baseline_messages))
                if reply:
                    return reply
                if time.monotonic() >= deadline:
                    break
            else:
                # A live turn is allowed to outlast the base polling window,
                # but every extension remains bounded by the hard cap.
                deadline = min(hard_deadline, time.monotonic() + base_timeout)
        time.sleep(0.01)
    raise LocalRouteError("local profile timed out waiting for Bot Chat")


def _persist_pin(profile: str, stored_id: str) -> None:
    meta = _read_ui_meta(profile)
    block = dict(meta.get("hermes-bots") or {}) if isinstance(meta.get("hermes-bots"), dict) else {}
    block["chat"] = stored_id
    result = _gateway_call(
        "profiles.configure",
        {"name": profile, "ui_meta": {"hermes-bots": block}},
    )
    if isinstance(result, dict) and result.get("ok") is False:
        raise LocalRouteError("local profile canonical pin could not be persisted")


def _context_payload(profile: str, stored_id: str) -> str:
    raw = json.dumps({"v": 1, "profile": profile, "session": stored_id}, separators=(",", ":"), ensure_ascii=True).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    # Context ids are durable references, not bearer secrets.  The process
    # random marker key must remain limited to recursion authorization; using
    # it here made an otherwise-valid context die whenever the process
    # restarted.  Route validation resolves this id against the profile's
    # canonical Bot Chat and compression lineage before it can be used.
    context = f"{_CONTEXT_PREFIX}{encoded}"
    if len(context) > _MAX_CONTEXT_LENGTH:
        raise LocalRouteError("local context is too large")
    return context


def _decode_context(context_id: str, profile: str) -> str:
    value = str(context_id or "").strip()
    if not value.startswith(_CONTEXT_PREFIX) or len(value) > _MAX_CONTEXT_LENGTH:
        raise LocalRouteError("invalid local context")
    try:
        encoded = value[len(_CONTEXT_PREFIX) :]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
            raise ValueError("invalid context encoding")
        padding = "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode((encoded + padding).encode())
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise LocalRouteError("invalid local context") from exc
    if not isinstance(data, dict) or data.get("v") != 1:
        raise LocalRouteError("invalid local context")
    try:
        from hermes_cli import profiles

        encoded_profile = profiles.normalize_profile_name(str(data.get("profile") or ""))
        profiles.validate_profile_name(encoded_profile)
    except Exception as exc:
        raise LocalRouteError("invalid local context") from exc
    if encoded_profile != profile or not isinstance(data.get("session"), str) or not data["session"].strip():
        raise LocalRouteError("stale or mismatched local context")
    return data["session"].strip()


def route(agent: str, message: str, context_id: str = "", *, caller_session_id: str = "") -> str:
    """Synchronously deliver one local-profile A2A turn and format the reply."""
    target_profile = _profile_parts(agent)
    if target_profile is None:
        raise LocalRouteError("not a local profile target")
    caller_profile = _active_profile()
    if target_profile == caller_profile:
        raise LocalRouteError("cannot route a local A2A message to the caller profile")

    marker = _caller_marker(str(caller_session_id or "").strip(), caller_profile)
    if marker is not None:
        if marker.profile != caller_profile or marker.chain[-1] != caller_profile:
            raise LocalRouteError("local routing marker does not match caller profile")
        if marker.depth >= MAX_LOCAL_ROUTE_DEPTH:
            raise LocalRouteError("local A2A route depth exceeded")
        if target_profile in marker.chain:
            raise LocalRouteError("local A2A route cycle rejected")
        depth = marker.depth + 1
        chain = marker.chain + (target_profile,)
    else:
        depth = 1
        chain = (caller_profile, target_profile)
    if depth > MAX_LOCAL_ROUTE_DEPTH:
        raise LocalRouteError("local A2A route depth exceeded")

    forced_id = _decode_context(context_id, target_profile) if context_id else ""
    target = _resolve_target(target_profile, forced_id)
    lock, acquired = _acquire_lock(target.profile, target.stored_id)
    if not acquired:
        raise LocalRouteError(f"local profile '{target.profile}' Bot Chat is busy")
    try:
        # A second resolver pass after acquiring the lock prevents a stale
        # context from being sent to a chat whose canonical pin changed while
        # another local call was completing.
        if forced_id and target.stored_id != forced_id:
            try:
                confirmed = _resolve_target(target_profile, forced_id)
            except LocalRouteError as exc:
                raise LocalRouteError("stale or mismatched local context") from exc
            if confirmed.stored_id != target.stored_id:
                raise LocalRouteError("stale or mismatched local context")
        safe_message = security.redact_outbound(str(message or "").strip())
        if not safe_message:
            raise LocalRouteError("message is required")
        delivered = (
            f"Message from 🤖 {_handle(caller_profile)} (@{_handle(caller_profile)}): "
            f"{safe_message}\n{_make_marker(target_profile, depth, chain)}"
        )
        _gateway_call(
            "prompt.submit",
            {
                "profile": target_profile,
                "session_id": target.runtime_id,
                "text": delivered,
            },
        )
        reply = _wait_for_reply(target)
        safe_reply = security.redact_outbound(reply) if reply else ""
        context = _context_payload(target_profile, target.stored_id)
        task_id = context
        security.audit("outbound", f"profile:{target_profile}", task_id, safe_message)
        protocol.persist_message(context, "user", safe_message, task_id)
        protocol.metrics.outbound_total += 1
        security.audit("inbound", f"profile:{target_profile}", task_id, safe_reply)
        protocol.persist_message(context, "agent", safe_reply, task_id)
        protocol.metrics.inbound_total += 1
        if target.needs_pin:
            # This is intentionally after _wait_for_reply: a created/adopted
            # Bot Chat is not canonical until a durable assistant turn exists.
            _persist_pin(target_profile, target.stored_id)
        return f"[profile:{target_profile} · context {context} · session {target.stored_id}]\n{safe_reply or '(no text reply)'}"
    finally:
        _release_lock(target.profile, target.stored_id, lock)


def available_profiles() -> list[str]:
    try:
        from hermes_cli.profiles import list_profiles

        return [str(p.name) for p in list_profiles() if str(getattr(p, "name", "")).strip()]
    except Exception:
        return []


__all__ = [
    "GatewayLike",
    "LocalRouteError",
    "MAX_LOCAL_ROUTE_DEPTH",
    "LOCAL_ROUTE_TIMEOUT_SECONDS",
    "available_profiles",
    "is_local_agent",
    "route",
    "set_gateway_for_tests",
]
