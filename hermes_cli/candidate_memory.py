"""Append-only, host-owned candidate-memory storage.

This module is intentionally inert: importing it performs no discovery, model
request, provider import, or canonical-memory write.  A caller must explicitly
construct :class:`CandidateMemoryStore` and invoke an append method.

``events.jsonl`` and ``relations_reviews.jsonl`` are the authoritative history.
The records are canonical JSON, written as one UTF-8 line with ``O_APPEND`` and
``fsync`` while holding one store-level lock.  Duplicate observations use the
frozen choice-B rule: the second event is retained and a ``DUPLICATE_OF``
relation is appended.  A duplicate transaction intent is first recorded in a
separate append-only recovery journal, allowing an interrupted event/relation
pair to be completed idempotently without rewriting either authoritative file.
A truncated final line is never rewritten; recovery first appends an immutable
receipt and then appends only the missing newline.
Malformed fragments are quarantined by that receipt and are never treated as
candidate records.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import threading
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


SCHEMA_VERSION = 1

EVENT_TYPES = frozenset(
    {
        "OWNER_CORRECTION",
        "DECISION_ACCEPTED",
        "BLOCKER_CHANGED",
        "SOURCE_CHANGED",
        "TASK_COMPLETED",
        "AUTHORIZATION_CHANGED",
        "MODEL_ROUTE_CHANGED",
        "MILESTONE_REACHED",
        "EXPLICIT_MEMORY_CAPTURE",
    }
)
RELATION_TYPES = frozenset(
    {
        "SUPPORTS",
        "CONTRADICTS",
        "SUPERSEDES",
        "DUPLICATE_OF",
        "REJECTED_BECAUSE",
        "ACCEPTED_FOR_UPDATE_REQUEST",
        "MARKED_STALE",
        "CANCELLED_BY",
    }
)
PROJECT_SCOPES = frozenset({"Yatima", "Factory", "BDH/research", "owner-private", "hidden-evaluation"})
PRIVACY_CLASSES = frozenset({"public-synthetic", "internal", "owner-private", "hidden-evaluation"})
VIEW_STATES = frozenset(
    {
        "PENDING",
        "ACCEPTED_FOR_UPDATE_REQUEST",
        "REJECTED",
        "SUPERSEDED",
        "CONFLICTING",
        "STALE",
        "CANCELLED",
    }
)
TEST_SHADOW_EVENT_TYPES = frozenset(
    {"TASK_COMPLETED", "MODEL_ROUTE_CHANGED", "SOURCE_CHANGED", "EXPLICIT_MEMORY_CAPTURE"}
)

EVENT_FIELDS = (
    "schema_version",
    "event_id",
    "event_type",
    "event_fingerprint",
    "project_scope",
    "session_id",
    "task_id",
    "attempt_id",
    "claim_or_event",
    "payload",
    "source_locators",
    "source_revisions",
    "source_hashes",
    "origin_actor",
    "configured_route",
    "actual_model",
    "actual_harness",
    "authority_class",
    "privacy_class",
    "test_only",
    "cancelled",
    "stale",
    "created_at",
)
RELATION_FIELDS = (
    "schema_version",
    "record_id",
    "record_type",
    "from_candidate_ids",
    "to_candidate_ids",
    "reviewer_identity",
    "source_locators",
    "source_hashes",
    "reason",
    "created_at",
)
INDEX_FIELDS = (
    "schema_version",
    "index_type",
    "project_scope",
    "include_private",
    "include_hidden_evaluation",
    "event_ledger_sha256",
    "relation_ledger_sha256",
    "candidates",
)
TRANSACTION_INTENT_FIELDS = (
    "schema_version",
    "transaction_id",
    "record_type",
    "event",
    "relation",
    "created_at",
)
TRANSACTION_COMMIT_FIELDS = (
    "schema_version",
    "transaction_id",
    "record_type",
    "event_id",
    "relation_id",
    "commit_fingerprint",
    "created_at",
)


class CandidateMemoryError(RuntimeError):
    """Base error for fail-closed candidate-memory operations."""


class SchemaError(CandidateMemoryError):
    """A caller or persisted line violates the frozen schema."""


class ScopeError(CandidateMemoryError):
    """An operation omitted or crossed an explicit project/privacy scope."""


class LedgerCorruptionError(CandidateMemoryError):
    """A persisted line is malformed or a relation references unknown data."""


class TruncatedTailError(LedgerCorruptionError):
    """The final authoritative line has no newline terminator."""

    def __init__(self, path: Path, offset: int, fragment_sha256: str) -> None:
        self.path = path
        self.offset = offset
        self.fragment_sha256 = fragment_sha256
        super().__init__(f"truncated JSONL tail at {path.name}:{offset} ({fragment_sha256})")


class AppendError(CandidateMemoryError):
    """A single append did not reach the filesystem durably."""


class SourceVerificationError(CandidateMemoryError):
    """A cited local source could not be reopened and verified exactly."""


class ProposalError(CandidateMemoryError):
    """A proposal could not be built without crossing an explicit boundary."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes or fail closed."""
    try:
        return json.dumps(
            _normalize(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise SchemaError("value is not canonical JSON") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def bytes_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _normalize(value: Any) -> Any:
    """Normalize JSON-compatible input for stable identity and persistence."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SchemaError("non-finite numeric value is forbidden")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise SchemaError("JSON object keys must be strings")
            key = unicodedata.normalize("NFC", raw_key).strip()
            if key in normalized:
                raise SchemaError("duplicate normalized JSON object key")
            normalized[key] = _normalize(raw_value)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise SchemaError(f"unsupported JSON value type: {type(value).__name__}")


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{field} must be a non-empty string")
    return _normalize(value)


def _optional_string(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SchemaError(f"{field} must be a string")
    return _normalize(value)


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise SchemaError(f"{field} must be boolean")
    return value


def _list_field(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise SchemaError(f"{field} must be a JSON list")
    return _normalize(list(value))


def validate_project_scope(project_scope: Any) -> str:
    if project_scope is None:
        raise ScopeError("project_scope is required")
    scope = _required_string(project_scope, "project_scope")
    if scope not in PROJECT_SCOPES:
        raise ScopeError(f"unknown project scope: {scope}")
    return scope


def _validate_privacy(project_scope: str, privacy_class: str) -> None:
    if privacy_class not in PRIVACY_CLASSES:
        raise ScopeError(f"unknown privacy class: {privacy_class}")
    if project_scope == "owner-private" and privacy_class != "owner-private":
        raise ScopeError("owner-private scope requires owner-private privacy")
    if project_scope == "hidden-evaluation" and privacy_class != "hidden-evaluation":
        raise ScopeError("hidden-evaluation scope requires hidden-evaluation privacy")


def _identity_for_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Frozen identity for every semantic, route, and state field.

    Only the generated event ID, its fingerprint, and wall-clock creation time
    are excluded.  This keeps a persisted event tamper-evident even when a
    route, actor, harness, cancellation, or stale-state field is modified.
    """
    return {
        field: event[field]
        for field in EVENT_FIELDS
        if field not in {"event_id", "event_fingerprint", "created_at"}
    }


def event_fingerprint(event: Mapping[str, Any]) -> str:
    return canonical_sha256(_identity_for_event(event))


def _identity_for_relation(relation: Mapping[str, Any]) -> dict[str, Any]:
    """Frozen relation identity; generated ID and timestamp are excluded."""
    return {
        field: relation[field]
        for field in RELATION_FIELDS
        if field not in {"record_id", "created_at"}
    }


def relation_fingerprint(relation: Mapping[str, Any]) -> str:
    return canonical_sha256(_identity_for_relation(relation))


def _expected_event_id(event: Mapping[str, Any], occurrence: int) -> str:
    return f"candidate-{event['event_fingerprint'][:32]}-{occurrence:04d}"


def _expected_relation_id(relation: Mapping[str, Any], occurrence: int) -> str:
    return f"relation-{relation_fingerprint(relation)[:32]}-{occurrence:04d}"


def record_sha256(record: Mapping[str, Any]) -> str:
    """Hash an immutable record exactly as persisted, excluding its newline."""
    return bytes_sha256(canonical_json_bytes(record))


_LOCAL_LOCKS: dict[str, threading.RLock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


def _local_lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(key, threading.RLock())


class _StoreLock:
    """One mandatory OS lock with a small same-process serialization guard."""

    def __init__(self, path: Path, *, existing_only: bool = False) -> None:
        self.path = path
        self.existing_only = existing_only
        self._local = _local_lock_for(path)
        self._handle: Any = None

    def __enter__(self) -> "_StoreLock":
        self._local.acquire()
        locked = False
        try:
            if self.existing_only:
                # Read-only callers must never create the root/lock or repair
                # its marker.  A missing or invalid lock is an unsafe store
                # state, even when the caller only intends to read it.
                if not self.path.parent.is_dir() or not self.path.is_file():
                    raise LedgerCorruptionError(f"read-only store lock is absent: {self.path}")
                try:
                    if self.path.stat().st_size != 1:
                        raise LedgerCorruptionError(f"read-only store lock is invalid: {self.path}")
                    self._handle = self.path.open("r+b")
                except OSError as exc:
                    raise LedgerCorruptionError(f"read-only store lock is unavailable: {self.path}") from exc
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._handle = self.path.open("a+b")
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            else:
                try:
                    import fcntl
                except ImportError as exc:  # pragma: no cover - platform guard
                    raise CandidateMemoryError("mandatory POSIX lock support is unavailable") from exc
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
                locked = True
            if not self.existing_only:
                # Initialize only after the OS lock is held, so two first
                # writers cannot race into a two-byte marker.
                self._handle.seek(0, os.SEEK_END)
                size = self._handle.tell()
                if size == 0:
                    self._handle.write(b"\0")
                    self._handle.flush()
                    os.fsync(self._handle.fileno())
                elif size != 1:
                    raise LedgerCorruptionError(f"store lock is invalid: {self.path}")
            # Validate the marker only after the OS lock is held.  On Windows
            # a competing byte-range lock can deny even a read of the marker;
            # acquiring first makes independent processes wait correctly.
            self._handle.seek(0)
            if self._handle.read(1) != b"\0":
                raise LedgerCorruptionError(f"store lock marker is invalid: {self.path}")
            self._handle.seek(0)
            return self
        except Exception:
            if self._handle is not None:
                if locked:
                    try:
                        if os.name == "nt":
                            import msvcrt

                            self._handle.seek(0)
                            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                self._handle.close()
                self._handle = None
            self._local.release()
            raise

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self._handle is not None:
                if os.name == "nt":
                    import msvcrt

                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                self._handle.close()
                self._handle = None
        finally:
            self._local.release()


def _append_bytes(path: Path, data: bytes) -> None:
    if not data:
        raise AppendError("empty append is forbidden")
    # Windows' CRT defaults descriptor writes to text mode, translating the
    # required LF delimiter to CRLF.  O_BINARY keeps the authoritative ledger
    # byte-for-byte identical across platforms.
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        written = os.write(fd, data)
        if written != len(data):
            raise AppendError(f"short append to {path.name}: {written}/{len(data)} bytes")
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_record(path: Path, record: Mapping[str, Any]) -> None:
    _append_bytes(path, canonical_json_bytes(record) + b"\n")


def _recovery_path(path: Path) -> Path:
    return path.with_name(path.name + ".recovery.jsonl")


def _transaction_path(root: Path) -> Path:
    # This journal is recovery metadata only.  It is never exposed by a
    # candidate query and is deliberately separate from both authoritative
    # ledgers.
    return root / ".candidate-memory.transactions.jsonl"


def _decode_canonical_object(body: bytes, description: str) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerCorruptionError(f"malformed JSONL record: {description}") from exc
    if not isinstance(value, dict):
        raise LedgerCorruptionError(f"JSONL record is not an object: {description}")
    try:
        canonical = canonical_json_bytes(value)
    except SchemaError as exc:
        raise LedgerCorruptionError(f"JSONL record is not canonical: {description}") from exc
    if canonical != body:
        raise LedgerCorruptionError(f"JSONL record is not canonical: {description}")
    return value


def _parse_jsonl_bytes(
    path: Path,
    raw: bytes,
    recovery: Mapping[tuple[int, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Parse canonical JSONL bytes without reading or changing the path."""
    if not raw:
        return []
    rows: list[dict[str, Any]] = []
    offset = 0
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            raise TruncatedTailError(path, offset, bytes_sha256(line))
        body = line[:-1]
        key = (offset, bytes_sha256(body))
        receipt = recovery.get(key)
        if receipt and receipt["action"] == "quarantined_truncated_tail":
            offset += len(line)
            continue
        try:
            value = _decode_canonical_object(body, f"{path.name}:{offset}")
        except LedgerCorruptionError as exc:
            raise LedgerCorruptionError(f"malformed or noncanonical JSONL record at {path.name}:{offset}") from exc
        rows.append(value)
        offset += len(line)
    if not raw.endswith(b"\n"):  # defensive; splitlines normally catches this
        raise TruncatedTailError(path, offset, bytes_sha256(raw[offset:]))
    return rows


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = path.read_bytes()
    if not raw:
        return []
    recovery = _read_recovery_receipts(_recovery_path(path), expected_ledger=path.name)
    return _parse_jsonl_bytes(path, raw, recovery)


def _read_recovery_receipts(
    path: Path, *, expected_ledger: str | None = None
) -> dict[tuple[int, str], dict[str, Any]]:
    if not path.exists():
        return {}
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise LedgerCorruptionError(f"recovery ledger has a truncated tail: {path.name}")
    receipts: dict[tuple[int, str], dict[str, Any]] = {}
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            raise LedgerCorruptionError(f"recovery ledger has a truncated tail: {path.name}")
        value = _decode_canonical_object(line[:-1], path.name)
        if set(value) != {
            "schema_version",
            "recovery_id",
            "ledger",
            "offset",
            "fragment_sha256",
            "action",
            "created_at",
        }:
            raise LedgerCorruptionError(f"malformed recovery receipt fields: {path.name}")
        try:
            if value["schema_version"] != SCHEMA_VERSION or not isinstance(value["schema_version"], int):
                raise ValueError("schema_version")
            if not isinstance(value["ledger"], str) or not value["ledger"]:
                raise ValueError("ledger")
            if expected_ledger is not None and value["ledger"] != expected_ledger:
                raise ValueError("ledger binding")
            if isinstance(value["offset"], bool) or not isinstance(value["offset"], int) or value["offset"] < 0:
                raise ValueError("offset")
            fragment_sha = str(value["fragment_sha256"]).lower()
            if len(fragment_sha) != 64 or any(character not in "0123456789abcdef" for character in fragment_sha):
                raise ValueError("fragment_sha256")
            action = str(value["action"])
            if not isinstance(value["recovery_id"], str) or not isinstance(value["created_at"], str):
                raise ValueError("receipt strings")
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerCorruptionError(f"malformed recovery receipt fields: {path.name}") from exc
        if action not in {"sealed_missing_newline", "quarantined_truncated_tail"}:
            raise LedgerCorruptionError(f"unknown recovery action: {action}")
        expected_id = canonical_sha256(
            {
                "ledger": value["ledger"],
                "offset": value["offset"],
                "fragment_sha256": fragment_sha,
                "action": action,
            }
        )
        if value["recovery_id"] != expected_id:
            raise LedgerCorruptionError(f"recovery receipt ID mismatch: {path.name}")
        key = (value["offset"], fragment_sha)
        prior = receipts.get(key)
        if prior is not None and canonical_json_bytes(prior) != canonical_json_bytes(value):
            raise LedgerCorruptionError(f"conflicting recovery receipts: {path.name}")
        receipts[key] = {"action": action, **value}
    return receipts


def _transaction_id(event: Mapping[str, Any], relation: Mapping[str, Any]) -> str:
    return f"duplicate-txn-{canonical_sha256({'event': event, 'relation': relation})[:32]}"


def _commit_fingerprint(transaction_id: str, event_id: str, relation_id: str) -> str:
    return canonical_sha256(
        {
            "schema_version": SCHEMA_VERSION,
            "transaction_id": transaction_id,
            "event_id": event_id,
            "relation_id": relation_id,
        }
    )


def _transaction_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise LedgerCorruptionError("transaction journal record is not an object")
    record_type = record.get("record_type")
    if record_type == "DUPLICATE_APPEND_INTENT":
        if set(record) != set(TRANSACTION_INTENT_FIELDS):
            raise LedgerCorruptionError("duplicate transaction intent fields are invalid")
        if record["schema_version"] != SCHEMA_VERSION or not isinstance(record["schema_version"], int):
            raise LedgerCorruptionError("duplicate transaction intent version is invalid")
        if not isinstance(record["transaction_id"], str) or not record["transaction_id"]:
            raise LedgerCorruptionError("duplicate transaction intent ID is invalid")
        if not isinstance(record["created_at"], str) or not record["created_at"]:
            raise LedgerCorruptionError("duplicate transaction intent timestamp is invalid")
        event = _event_from_record(record["event"])
        relation = _relation_from_record(record["relation"])
        expected_id = _transaction_id(event, relation)
        if record["transaction_id"] != expected_id:
            raise LedgerCorruptionError("duplicate transaction intent ID mismatch")
        if relation["record_type"] != "DUPLICATE_OF":
            raise LedgerCorruptionError("duplicate transaction relation type is invalid")
        return dict(record)
    if record_type == "DUPLICATE_APPEND_COMMIT":
        if set(record) != set(TRANSACTION_COMMIT_FIELDS):
            raise LedgerCorruptionError("duplicate transaction commit fields are invalid")
        if record["schema_version"] != SCHEMA_VERSION or not isinstance(record["schema_version"], int):
            raise LedgerCorruptionError("duplicate transaction commit version is invalid")
        for field in ("transaction_id", "event_id", "relation_id", "commit_fingerprint", "created_at"):
            if not isinstance(record[field], str) or not record[field]:
                raise LedgerCorruptionError(f"duplicate transaction commit {field} is invalid")
        return dict(record)
    raise LedgerCorruptionError("unknown duplicate transaction record type")


def _read_transaction_journal(path: Path) -> list[dict[str, Any]]:
    # Tail repair is performed by the store while holding its lock.  Reuse
    # the canonical JSONL reader here so receipt-bound malformed fragments
    # are skipped and valid missing-newline records are parsed normally after
    # the append-only newline repair.
    return [_transaction_from_record(value) for value in _read_jsonl(path)]


def _event_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise SchemaError("event record must be an object")
    if set(record) != set(EVENT_FIELDS):
        raise SchemaError("event fields do not exactly match the V1 contract")
    if record["schema_version"] != SCHEMA_VERSION or not isinstance(record["schema_version"], int):
        raise SchemaError("event schema_version is invalid")
    for field in ("event_id", "event_fingerprint", "session_id", "task_id", "attempt_id", "created_at"):
        if not isinstance(record[field], str):
            raise SchemaError(f"event {field} must be a string")
    if record["event_type"] not in EVENT_TYPES:
        raise SchemaError(f"unsupported event type: {record['event_type']}")
    scope = validate_project_scope(record["project_scope"])
    privacy = _required_string(record["privacy_class"], "privacy_class")
    _validate_privacy(scope, privacy)
    if not isinstance(record["claim_or_event"], str):
        raise SchemaError("claim_or_event must be a string")
    if not isinstance(record["payload"], (dict, list, str, int, float, bool, type(None))):
        raise SchemaError("payload must be JSON-compatible")
    for field in ("source_locators", "source_revisions", "source_hashes"):
        if not isinstance(record[field], list):
            raise SchemaError(f"event {field} must be a list")
    for field in ("origin_actor", "configured_route", "actual_model", "actual_harness", "authority_class"):
        if not isinstance(record[field], str):
            raise SchemaError(f"event {field} must be a string")
    for field in ("test_only", "cancelled", "stale"):
        _bool(record[field], f"event {field}")
    expected = event_fingerprint(record)
    if record["event_fingerprint"] != expected:
        raise LedgerCorruptionError(f"event fingerprint mismatch: {record['event_id']}")
    return dict(record)


def _relation_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise SchemaError("relation record must be an object")
    if set(record) != set(RELATION_FIELDS):
        raise SchemaError("relation fields do not exactly match the V1 contract")
    if record["schema_version"] != SCHEMA_VERSION or not isinstance(record["schema_version"], int):
        raise SchemaError("relation schema_version is invalid")
    if record["record_type"] not in RELATION_TYPES:
        raise SchemaError(f"unsupported relation type: {record['record_type']}")
    if not isinstance(record["record_id"], str) or not record["record_id"]:
        raise SchemaError("relation record_id must be a string")
    for field in ("from_candidate_ids", "to_candidate_ids", "source_locators", "source_hashes"):
        if not isinstance(record[field], list):
            raise SchemaError(f"relation {field} must be a list")
    if not record["from_candidate_ids"] or not record["to_candidate_ids"]:
        raise SchemaError("relation endpoints cannot be empty")
    if any(not isinstance(value, str) or not value for value in record["from_candidate_ids"] + record["to_candidate_ids"]):
        raise SchemaError("relation candidate IDs must be non-empty strings")
    for field in ("reviewer_identity", "reason", "created_at"):
        if not isinstance(record[field], str):
            raise SchemaError(f"relation {field} must be a string")
    return dict(record)


class CandidateMemoryStore:
    """Explicit append/query seam for the non-canonical candidate plane."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        try:
            raw_root = os.fspath(root)
        except TypeError as exc:
            raise SchemaError("store root must be an explicit absolute path") from exc
        if isinstance(raw_root, bytes):
            try:
                raw_root = os.fsdecode(raw_root)
            except UnicodeDecodeError as exc:
                raise SchemaError("store root must be an explicit absolute path") from exc
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise SchemaError("store root must be an explicit absolute path")
        self.root = Path(raw_root)
        if not self.root.is_absolute():
            raise SchemaError("store root must be an explicit absolute path")
        self.events_path = self.root / "events.jsonl"
        self.relations_path = self.root / "relations_reviews.jsonl"
        self.index_path = self.root / "candidate_view.index.json"
        self.lock_path = self.root / ".candidate-memory.lock"
        self.transactions_path = _transaction_path(self.root)

    @contextlib.contextmanager
    def _locked(self, *, reconcile: bool = True) -> Iterator[None]:
        with _StoreLock(self.lock_path):
            # The recovery journal is itself append-only and can be torn at
            # the final write.  Repair/quarantine it before reconciliation so
            # an interrupted duplicate cannot prevent all public operations
            # from making progress.  _recover_tail_unlocked does not acquire
            # the store lock, avoiding recursive lock acquisition.
            self._recover_tail_unlocked(self.transactions_path)
            if reconcile:
                self._reconcile_duplicate_transactions_unlocked()
            yield

    @contextlib.contextmanager
    def _locked_read_only(self) -> Iterator[None]:
        """Lock and validate recovery state without repairing or reconciling it."""
        with _StoreLock(self.lock_path, existing_only=True):
            # Proposal construction and evidence snapshots must not repair a
            # torn journal, append a receipt/newline, or complete a pending
            # duplicate transaction.  Read the journal exactly as persisted
            # and fail closed on any incomplete or inconsistent transaction.
            self._validate_transactions_read_only_unlocked()
            yield

    def _events_unlocked(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        occurrences: dict[str, int] = {}
        seen_ids: set[str] = set()
        for row in _read_jsonl(self.events_path):
            event = _event_from_record(row)
            if event["event_id"] in seen_ids:
                raise LedgerCorruptionError(f"duplicate event ID: {event['event_id']}")
            fingerprint = event["event_fingerprint"]
            occurrence = occurrences.get(fingerprint, 0) + 1
            expected_id = _expected_event_id(event, occurrence)
            if event["event_id"] != expected_id:
                raise LedgerCorruptionError(f"deterministic event ID mismatch: {event['event_id']}")
            occurrences[fingerprint] = occurrence
            seen_ids.add(event["event_id"])
            events.append(event)
        return events

    def _relations_unlocked(self) -> list[dict[str, Any]]:
        relations = self._relations_unlocked_raw()
        events = {event["event_id"]: event for event in self._events_unlocked()}
        self._validate_duplicate_relations_unlocked(relations, events)
        return relations

    @staticmethod
    def _validate_relation_scope(event_map: Mapping[str, Mapping[str, Any]], endpoints: Sequence[str]) -> None:
        scopes = {event_map[candidate_id]["project_scope"] for candidate_id in endpoints}
        if len(scopes) != 1:
            raise ScopeError("relations cannot cross project scopes")
        privacy = {event_map[candidate_id]["privacy_class"] for candidate_id in endpoints}
        if len(privacy) != 1:
            raise ScopeError("relations cannot cross privacy classes")

    @staticmethod
    def _split_duplicate_transactions(
        records: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        intents: dict[str, dict[str, Any]] = {}
        commits: dict[str, dict[str, Any]] = {}
        intent_positions: dict[str, int] = {}
        for position, record in enumerate(records):
            transaction_id = record["transaction_id"]
            if record["record_type"] == "DUPLICATE_APPEND_INTENT":
                if transaction_id in intents or transaction_id in commits:
                    raise LedgerCorruptionError(f"duplicate transaction intent: {transaction_id}")
                intents[transaction_id] = dict(record)
                intent_positions[transaction_id] = position
            else:
                if transaction_id in commits:
                    raise LedgerCorruptionError(f"duplicate transaction commit: {transaction_id}")
                commits[transaction_id] = dict(record)
                if transaction_id not in intents:
                    raise LedgerCorruptionError(f"transaction commit precedes intent: {transaction_id}")
                if position <= intent_positions[transaction_id]:
                    raise LedgerCorruptionError(f"transaction ordering is invalid: {transaction_id}")
        return intents, commits

    @staticmethod
    def _duplicate_transaction_commit(
        transaction_id: str, event: Mapping[str, Any], relation: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "transaction_id": transaction_id,
            "record_type": "DUPLICATE_APPEND_COMMIT",
            "event_id": event["event_id"],
            "relation_id": relation["record_id"],
            "commit_fingerprint": _commit_fingerprint(transaction_id, event["event_id"], relation["record_id"]),
        }

    @staticmethod
    def _validate_duplicate_transaction_shape(
        transaction_id: str, event: Mapping[str, Any], relation: Mapping[str, Any]
    ) -> None:
        if relation["from_candidate_ids"] != [event["event_id"]]:
            raise LedgerCorruptionError(f"duplicate transaction event endpoint mismatch: {transaction_id}")
        if len(relation["to_candidate_ids"]) != 1:
            raise LedgerCorruptionError(f"duplicate transaction target cardinality is invalid: {transaction_id}")
        if relation["to_candidate_ids"][0] == event["event_id"]:
            raise LedgerCorruptionError(f"duplicate transaction self-reference: {transaction_id}")

    def _preflight_duplicate_transactions_unlocked(
        self,
        records: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
        relations: Sequence[Mapping[str, Any]],
    ) -> list[tuple[dict[str, Any], dict[str, Any], Mapping[str, Any] | None]]:
        """Validate every duplicate intent before any authoritative append.

        The transaction journal may describe a crash after either authoritative
        append.  The in-memory projection below accounts for those missing
        records while checking the persisted prefixes first.  No filesystem
        mutation is permitted until every intent has an exact event/relation
        occurrence, immediate-predecessor target, scope, and collision check.
        """
        intents, commits = self._split_duplicate_transactions(records)
        event_by_id: dict[str, dict[str, Any]] = {event["event_id"]: dict(event) for event in events}
        relation_by_id: dict[str, dict[str, Any]] = {
            relation["record_id"]: dict(relation) for relation in relations
        }
        events_by_fingerprint: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            events_by_fingerprint.setdefault(event["event_fingerprint"], []).append(dict(event))
        relations_by_fingerprint: dict[str, list[dict[str, Any]]] = {}
        for relation in relations:
            relations_by_fingerprint.setdefault(relation_fingerprint(relation), []).append(dict(relation))

        plans: list[tuple[dict[str, Any], dict[str, Any], Mapping[str, Any] | None]] = []
        for transaction_id, intent in intents.items():
            event = _event_from_record(intent["event"])
            relation = _relation_from_record(intent["relation"])
            if relation["record_type"] != "DUPLICATE_OF":
                raise LedgerCorruptionError(f"duplicate transaction relation type is invalid: {transaction_id}")
            self._validate_duplicate_transaction_shape(transaction_id, event, relation)

            occurrences = events_by_fingerprint.setdefault(event["event_fingerprint"], [])
            existing_event = event_by_id.get(event["event_id"])
            if existing_event is None:
                expected_event_id = _expected_event_id(event, len(occurrences) + 1)
                if event["event_id"] != expected_event_id:
                    raise LedgerCorruptionError(f"duplicate transaction event ID mismatch: {transaction_id}")
                source_event = event
                source_occurrence = len(occurrences) + 1
            else:
                if canonical_json_bytes(existing_event) != canonical_json_bytes(event):
                    raise LedgerCorruptionError(f"duplicate transaction event conflicts: {transaction_id}")
                source_event = existing_event
                source_occurrence = next(
                    (
                        occurrence
                        for occurrence, candidate in enumerate(occurrences, start=1)
                        if candidate["event_id"] == event["event_id"]
                    ),
                    0,
                )
                if source_occurrence == 0 or event["event_id"] != _expected_event_id(event, source_occurrence):
                    raise LedgerCorruptionError(f"duplicate transaction event occurrence mismatch: {transaction_id}")
            if source_occurrence <= 1 or not occurrences:
                raise LedgerCorruptionError(f"duplicate transaction has no predecessor: {transaction_id}")
            expected_target = occurrences[source_occurrence - 2]
            target_id = relation["to_candidate_ids"][0]
            if target_id != expected_target["event_id"]:
                raise LedgerCorruptionError(f"duplicate transaction target is not the immediate predecessor: {transaction_id}")
            if source_event["event_fingerprint"] != expected_target["event_fingerprint"]:
                raise LedgerCorruptionError(f"duplicate transaction fingerprint mismatch: {transaction_id}")

            projected_events = dict(event_by_id)
            projected_events[event["event_id"]] = source_event
            self._validate_relation_scope(
                projected_events,
                [source_event["event_id"], expected_target["event_id"]],
            )

            relation_occurrences = relations_by_fingerprint.setdefault(relation_fingerprint(relation), [])
            existing_relation = relation_by_id.get(relation["record_id"])
            if existing_relation is None:
                expected_relation_id = _expected_relation_id(relation, len(relation_occurrences) + 1)
                if relation["record_id"] != expected_relation_id:
                    raise LedgerCorruptionError(f"duplicate transaction relation ID mismatch: {transaction_id}")
            else:
                if canonical_json_bytes(existing_relation) != canonical_json_bytes(relation):
                    raise LedgerCorruptionError(f"duplicate transaction relation conflicts: {transaction_id}")
                relation_occurrence = next(
                    (
                        occurrence
                        for occurrence, candidate in enumerate(relation_occurrences, start=1)
                        if candidate["record_id"] == relation["record_id"]
                    ),
                    0,
                )
                if relation_occurrence == 0 or relation["record_id"] != _expected_relation_id(
                    relation, relation_occurrence
                ):
                    raise LedgerCorruptionError(f"duplicate transaction relation occurrence mismatch: {transaction_id}")

            commit = commits.get(transaction_id)
            if commit is not None:
                expected_commit = self._duplicate_transaction_commit(transaction_id, event, relation)
                if any(commit[field] != expected_commit[field] for field in expected_commit):
                    raise LedgerCorruptionError(f"duplicate transaction commit conflicts: {transaction_id}")

            if existing_event is None:
                event_by_id[event["event_id"]] = event
                occurrences.append(event)
            if existing_relation is None:
                relation_by_id[relation["record_id"]] = relation
                relation_occurrences.append(relation)
            plans.append((event, relation, commit))

        intent_relation_ids = {relation["record_id"] for _, relation, _ in plans}
        for relation in relations:
            if relation["record_type"] == "DUPLICATE_OF" and relation["record_id"] not in intent_relation_ids:
                raise LedgerCorruptionError(
                    f"DUPLICATE_OF relation lacks a transaction intent: {relation['record_id']}"
                )
        for relation in relation_by_id.values():
            endpoints = relation["from_candidate_ids"] + relation["to_candidate_ids"]
            if any(candidate_id not in event_by_id for candidate_id in endpoints):
                raise LedgerCorruptionError(f"relation references an unknown candidate: {relation['record_id']}")
            self._validate_relation_scope(event_by_id, endpoints)
        expected_duplicate_sources = {
            event["event_id"]
            for occurrences in events_by_fingerprint.values()
            for event in occurrences[1:]
        }
        actual_duplicate_sources = {
            relation["from_candidate_ids"][0]
            for relation in relation_by_id.values()
            if relation["record_type"] == "DUPLICATE_OF"
        }
        if actual_duplicate_sources != expected_duplicate_sources:
            raise LedgerCorruptionError("duplicate event/relation preflight accounting mismatch")
        return plans

    @staticmethod
    def _has_quarantined_transaction_tail(receipts: Mapping[Any, Mapping[str, Any]]) -> bool:
        return any(receipt.get("action") == "quarantined_truncated_tail" for receipt in receipts.values())

    def _validate_duplicate_relations_unlocked(
        self,
        relations: Sequence[Mapping[str, Any]],
        events: Mapping[str, Mapping[str, Any]],
        transaction_records: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        """Require exact one-to-one choice-B accounting for repeated events.

        A duplicate relation is not sufficient evidence by itself: every event
        after the first occurrence of an event fingerprint must be linked to
        its immediately preceding occurrence by one persisted relation and one
        committed transaction.  The reverse direction is checked as well so a
        relation or transaction cannot be orphaned from the event history.
        """
        duplicate_relations = [relation for relation in relations if relation["record_type"] == "DUPLICATE_OF"]
        events_by_fingerprint: dict[str, list[Mapping[str, Any]]] = {}
        for event in events.values():
            events_by_fingerprint.setdefault(event["event_fingerprint"], []).append(event)
        expected_duplicate_targets: dict[str, Mapping[str, Any]] = {}
        for occurrences in events_by_fingerprint.values():
            for event, target in zip(occurrences[1:], occurrences):
                expected_duplicate_targets[event["event_id"]] = target
        if not duplicate_relations and not expected_duplicate_targets:
            return
        records = (
            list(transaction_records)
            if transaction_records is not None
            else _read_transaction_journal(self.transactions_path)
        )
        intents, commits = self._split_duplicate_transactions(records)
        committed_by_relation_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for transaction_id, intent in intents.items():
            event = _event_from_record(intent["event"])
            relation = _relation_from_record(intent["relation"])
            if relation["record_type"] != "DUPLICATE_OF":
                raise LedgerCorruptionError(f"duplicate transaction relation type is invalid: {transaction_id}")
            self._validate_duplicate_transaction_shape(transaction_id, event, relation)
            commit = commits.get(transaction_id)
            if commit is None:
                raise LedgerCorruptionError(f"uncommitted duplicate transaction: {transaction_id}")
            expected_commit = self._duplicate_transaction_commit(transaction_id, event, relation)
            if any(commit[field] != expected_commit[field] for field in expected_commit):
                raise LedgerCorruptionError(f"duplicate transaction commit conflicts: {transaction_id}")
            if relation["record_id"] in committed_by_relation_id:
                raise LedgerCorruptionError(f"duplicate transaction relation is repeated: {transaction_id}")
            committed_by_relation_id[relation["record_id"]] = (event, relation)

        persisted_relation_ids = {relation["record_id"] for relation in duplicate_relations}
        committed_relation_ids = set(committed_by_relation_id)
        if persisted_relation_ids != committed_relation_ids:
            raise LedgerCorruptionError("duplicate transaction/relation accounting mismatch")

        relations_by_source: dict[str, Mapping[str, Any]] = {}
        for relation in duplicate_relations:
            from_ids = relation["from_candidate_ids"]
            to_ids = relation["to_candidate_ids"]
            if len(from_ids) != 1 or len(to_ids) != 1 or from_ids[0] == to_ids[0]:
                raise LedgerCorruptionError(f"DUPLICATE_OF endpoints are invalid: {relation['record_id']}")
            source = events.get(from_ids[0])
            target = events.get(to_ids[0])
            if source is None or target is None:
                raise LedgerCorruptionError(f"DUPLICATE_OF references an unknown candidate: {relation['record_id']}")
            if source["event_fingerprint"] != target["event_fingerprint"]:
                raise LedgerCorruptionError(f"DUPLICATE_OF fingerprint mismatch: {relation['record_id']}")
            if source["event_id"] in relations_by_source:
                raise LedgerCorruptionError(f"DUPLICATE_OF source is repeated: {relation['record_id']}")
            relations_by_source[source["event_id"]] = relation
            committed = committed_by_relation_id.get(relation["record_id"])
            if committed is None:
                raise LedgerCorruptionError(f"DUPLICATE_OF lacks a committed transaction: {relation['record_id']}")
            transaction_event, transaction_relation = committed
            if canonical_json_bytes(transaction_relation) != canonical_json_bytes(relation):
                raise LedgerCorruptionError(f"DUPLICATE_OF relation bytes do not match its transaction: {relation['record_id']}")
            if canonical_json_bytes(transaction_event) != canonical_json_bytes(source):
                raise LedgerCorruptionError(f"DUPLICATE_OF event bytes do not match its transaction: {relation['record_id']}")
            if transaction_event["event_fingerprint"] != target["event_fingerprint"]:
                raise LedgerCorruptionError(f"DUPLICATE_OF transaction fingerprint mismatch: {relation['record_id']}")

        if set(relations_by_source) != set(expected_duplicate_targets):
            raise LedgerCorruptionError("duplicate event/relation accounting mismatch")
        for source_id, expected_target in expected_duplicate_targets.items():
            relation = relations_by_source[source_id]
            if relation["to_candidate_ids"] != [expected_target["event_id"]]:
                raise LedgerCorruptionError(
                    f"DUPLICATE_OF target does not match event occurrence: {relation['record_id']}"
                )

    def _relations_unlocked_raw(self, *, validate_endpoints: bool = True) -> list[dict[str, Any]]:
        """Read relation schema/IDs without requiring duplicate transactions."""
        relations: list[dict[str, Any]] = []
        occurrences: dict[str, int] = {}
        seen_ids: set[str] = set()
        for row in _read_jsonl(self.relations_path):
            relation = _relation_from_record(row)
            if relation["record_id"] in seen_ids:
                raise LedgerCorruptionError(f"duplicate relation ID: {relation['record_id']}")
            fingerprint = relation_fingerprint(relation)
            occurrence = occurrences.get(fingerprint, 0) + 1
            expected_id = _expected_relation_id(relation, occurrence)
            if relation["record_id"] != expected_id:
                raise LedgerCorruptionError(f"deterministic relation ID mismatch: {relation['record_id']}")
            occurrences[fingerprint] = occurrence
            seen_ids.add(relation["record_id"])
            relations.append(relation)
        if validate_endpoints:
            events = {event["event_id"]: event for event in self._events_unlocked()}
            for relation in relations:
                endpoints = relation["from_candidate_ids"] + relation["to_candidate_ids"]
                if any(candidate_id not in events for candidate_id in endpoints):
                    raise LedgerCorruptionError(f"relation references an unknown candidate: {relation['record_id']}")
                self._validate_relation_scope(events, endpoints)
        return relations

    def _validate_transactions_read_only_unlocked(self) -> None:
        """Validate the transaction journal without repairing or appending."""
        receipts = _read_recovery_receipts(
            _recovery_path(self.transactions_path),
            expected_ledger=self.transactions_path.name,
        )
        if self._has_quarantined_transaction_tail(receipts):
            raise LedgerCorruptionError("transaction journal contains a quarantined tail")
        records = _read_transaction_journal(self.transactions_path)
        if not records:
            return
        intents, commits = self._split_duplicate_transactions(records)
        events = self._events_unlocked()
        relations = self._relations_unlocked_raw()
        event_by_id = {event["event_id"]: event for event in events}
        relation_by_id = {relation["record_id"]: relation for relation in relations}
        for transaction_id, intent in intents.items():
            event = _event_from_record(intent["event"])
            relation = _relation_from_record(intent["relation"])
            self._validate_duplicate_transaction_shape(transaction_id, event, relation)
            commit = commits.get(transaction_id)
            if commit is None:
                raise LedgerCorruptionError(f"uncommitted duplicate transaction: {transaction_id}")
            expected_commit = self._duplicate_transaction_commit(transaction_id, event, relation)
            if any(commit[field] != expected_commit[field] for field in expected_commit):
                raise LedgerCorruptionError(f"duplicate transaction commit conflicts: {transaction_id}")
            existing_event = event_by_id.get(event["event_id"])
            if existing_event is None or canonical_json_bytes(existing_event) != canonical_json_bytes(event):
                raise LedgerCorruptionError(f"duplicate transaction event is not durably committed: {transaction_id}")
            existing_relation = relation_by_id.get(relation["record_id"])
            if existing_relation is None or canonical_json_bytes(existing_relation) != canonical_json_bytes(relation):
                raise LedgerCorruptionError(f"duplicate transaction relation is not durably committed: {transaction_id}")
            self._validate_relation_scope(
                event_by_id,
                relation["from_candidate_ids"] + relation["to_candidate_ids"],
            )
        self._validate_duplicate_relations_unlocked(relations, event_by_id, records)

    def _reconcile_duplicate_transactions_unlocked(self) -> None:
        """Finish durable duplicate intents without rewriting either ledger."""
        records = _read_transaction_journal(self.transactions_path)
        if not records:
            return
        events = self._events_unlocked()
        # Reconciliation must inspect the relation ledger without requiring a
        # committed transaction for the intent it is about to finish.
        relations = self._relations_unlocked_raw(validate_endpoints=False)
        plans = self._preflight_duplicate_transactions_unlocked(records, events, relations)
        event_by_id = {event["event_id"]: event for event in events}
        relation_by_id = {relation["record_id"]: relation for relation in relations}
        event_occurrences: dict[str, int] = {}
        for event in events:
            fingerprint = event["event_fingerprint"]
            event_occurrences[fingerprint] = event_occurrences.get(fingerprint, 0) + 1
        relation_occurrences: dict[str, int] = {}
        for relation in relations:
            fingerprint = relation_fingerprint(relation)
            relation_occurrences[fingerprint] = relation_occurrences.get(fingerprint, 0) + 1

        for event, relation, commit in plans:
            existing_event = event_by_id.get(event["event_id"])
            if existing_event is None:
                _append_record(self.events_path, event)
                event_by_id[event["event_id"]] = event
                event_occurrences[event["event_fingerprint"]] = event_occurrences.get(event["event_fingerprint"], 0) + 1

            event_map = dict(event_by_id)
            endpoints = relation["from_candidate_ids"] + relation["to_candidate_ids"]
            if any(candidate_id not in event_map for candidate_id in endpoints):
                raise LedgerCorruptionError("duplicate transaction relation references unknown candidate")
            self._validate_relation_scope(event_map, endpoints)
            existing_relation = relation_by_id.get(relation["record_id"])
            if existing_relation is None:
                _append_record(self.relations_path, relation)
                relation_by_id[relation["record_id"]] = relation
                relation_fingerprint_value = relation_fingerprint(relation)
                relation_occurrences[relation_fingerprint_value] = relation_occurrences.get(relation_fingerprint_value, 0) + 1

            transaction_id = _transaction_id(event, relation)
            expected_commit = {
                "schema_version": SCHEMA_VERSION,
                "transaction_id": transaction_id,
                "record_type": "DUPLICATE_APPEND_COMMIT",
                "event_id": event["event_id"],
                "relation_id": relation["record_id"],
                "commit_fingerprint": _commit_fingerprint(transaction_id, event["event_id"], relation["record_id"]),
            }
            if commit is not None:
                if any(commit[field] != expected_commit[field] for field in expected_commit):
                    raise LedgerCorruptionError(f"duplicate transaction commit conflicts: {transaction_id}")
            else:
                _append_record(
                    self.transactions_path,
                    {**expected_commit, "created_at": utc_now()},
                )

    def read_events(self, *, recover: bool = False) -> list[dict[str, Any]]:
        with self._locked(reconcile=not recover):
            if recover:
                self._recover_tail_unlocked(self.events_path)
                self._reconcile_duplicate_transactions_unlocked()
            events = self._events_unlocked()
            # A normal event read also validates the relation ledger.  This
            # keeps orphaned or tampered DUPLICATE_OF records from being
            # treated as a valid view merely because the caller did not ask
            # for relations explicitly.
            self._relations_unlocked()
            return events

    def read_relations(self, *, recover: bool = False) -> list[dict[str, Any]]:
        with self._locked(reconcile=not recover):
            if recover:
                self._recover_tail_unlocked(self.relations_path)
                self._reconcile_duplicate_transactions_unlocked()
            return self._relations_unlocked()

    def _valid_duplicate_tail_context_unlocked(
        self,
        *,
        candidate_event: Mapping[str, Any] | None,
        candidate_relation: Mapping[str, Any] | None,
        event_rows: Sequence[Mapping[str, Any]],
        relation_rows: Sequence[Mapping[str, Any]],
    ) -> bool:
        """Require a duplicate tail to be backed by its exact transaction intent."""
        try:
            transaction_records = _read_transaction_journal(self.transactions_path)
            intents, commits = self._split_duplicate_transactions(transaction_records)
            matching: tuple[str, dict[str, Any], dict[str, Any]] | None = None
            for transaction_id, intent in intents.items():
                event = _event_from_record(intent["event"])
                relation = _relation_from_record(intent["relation"])
                if relation["record_type"] != "DUPLICATE_OF":
                    continue
                if candidate_event is not None and (
                    event["event_id"] != candidate_event["event_id"]
                    or canonical_json_bytes(event) != canonical_json_bytes(candidate_event)
                ):
                    continue
                if candidate_relation is not None and (
                    relation["record_id"] != candidate_relation["record_id"]
                    or canonical_json_bytes(relation) != canonical_json_bytes(candidate_relation)
                ):
                    continue
                matching = (transaction_id, event, relation)
                break
            if matching is None:
                return False

            transaction_id, intended_event, intended_relation = matching
            self._validate_duplicate_transaction_shape(transaction_id, intended_event, intended_relation)
            if transaction_id != _transaction_id(intended_event, intended_relation):
                return False
            commit = commits.get(transaction_id)
            if commit is not None:
                expected_commit = self._duplicate_transaction_commit(
                    transaction_id, intended_event, intended_relation
                )
                if any(commit[field] != expected_commit[field] for field in expected_commit):
                    return False

            projected_events = [dict(event) for event in event_rows]
            existing_event = next(
                (event for event in projected_events if event["event_id"] == intended_event["event_id"]),
                None,
            )
            if existing_event is not None:
                if canonical_json_bytes(existing_event) != canonical_json_bytes(intended_event):
                    return False
            else:
                occurrences = [
                    event
                    for event in projected_events
                    if event["event_fingerprint"] == intended_event["event_fingerprint"]
                ]
                if intended_event["event_id"] != _expected_event_id(intended_event, len(occurrences) + 1):
                    return False
                projected_events.append(dict(intended_event))

            event_occurrences: dict[str, list[Mapping[str, Any]]] = {}
            event_by_id: dict[str, Mapping[str, Any]] = {}
            for event in projected_events:
                event_id = event["event_id"]
                if event_id in event_by_id:
                    return False
                occurrence = len(event_occurrences.setdefault(event["event_fingerprint"], [])) + 1
                if event_id != _expected_event_id(event, occurrence):
                    return False
                event_by_id[event_id] = event
                event_occurrences[event["event_fingerprint"]].append(event)
            occurrences = event_occurrences.get(intended_event["event_fingerprint"], [])
            source_occurrence = next(
                (
                    occurrence
                    for occurrence, event in enumerate(occurrences, start=1)
                    if event["event_id"] == intended_event["event_id"]
                ),
                0,
            )
            if source_occurrence <= 1:
                return False
            expected_target = occurrences[source_occurrence - 2]
            if intended_relation["from_candidate_ids"] != [intended_event["event_id"]]:
                return False
            if intended_relation["to_candidate_ids"] != [expected_target["event_id"]]:
                return False
            if intended_event["event_fingerprint"] != expected_target["event_fingerprint"]:
                return False
            self._validate_relation_scope(
                event_by_id,
                [intended_event["event_id"], expected_target["event_id"]],
            )

            projected_relations = [dict(relation) for relation in relation_rows]
            existing_relation = next(
                (
                    relation
                    for relation in projected_relations
                    if relation["record_id"] == intended_relation["record_id"]
                ),
                None,
            )
            if existing_relation is not None:
                if canonical_json_bytes(existing_relation) != canonical_json_bytes(intended_relation):
                    return False
            else:
                relation_occurrences = [
                    relation
                    for relation in projected_relations
                    if relation_fingerprint(relation) == relation_fingerprint(intended_relation)
                ]
                if intended_relation["record_id"] != _expected_relation_id(
                    intended_relation, len(relation_occurrences) + 1
                ):
                    return False

            # Validate the complete projected relation prefix before allowing
            # a missing-newline duplicate tail to be sealed.  This keeps a
            # candidate event/relation from being accepted merely because its
            # own local schema and ID look plausible: every duplicate relation
            # must have its exact transaction intent and immediate predecessor
            # context, while ordinary relations still need valid endpoints.
            if existing_relation is None:
                projected_relations.append(dict(intended_relation))
            relation_by_id: dict[str, Mapping[str, Any]] = {}
            relation_occurrences: dict[str, list[Mapping[str, Any]]] = {}
            intent_by_relation_id: dict[str, tuple[str, Mapping[str, Any], Mapping[str, Any]]] = {}
            for transaction_id, intent in intents.items():
                intent_event = _event_from_record(intent["event"])
                intent_relation = _relation_from_record(intent["relation"])
                if intent_relation["record_type"] != "DUPLICATE_OF":
                    return False
                self._validate_duplicate_transaction_shape(transaction_id, intent_event, intent_relation)
                if transaction_id != _transaction_id(intent_event, intent_relation):
                    return False
                intent_by_relation_id[intent_relation["record_id"]] = (
                    transaction_id,
                    intent_event,
                    intent_relation,
                )
            for relation in projected_relations:
                record_id = relation["record_id"]
                if record_id in relation_by_id:
                    return False
                fingerprint = relation_fingerprint(relation)
                occurrence = len(relation_occurrences.setdefault(fingerprint, [])) + 1
                if record_id != _expected_relation_id(relation, occurrence):
                    return False
                endpoints = relation["from_candidate_ids"] + relation["to_candidate_ids"]
                if any(candidate_id not in event_by_id for candidate_id in endpoints):
                    return False
                self._validate_relation_scope(event_by_id, endpoints)
                if relation["record_type"] == "DUPLICATE_OF":
                    from_ids = relation["from_candidate_ids"]
                    to_ids = relation["to_candidate_ids"]
                    if len(from_ids) != 1 or len(to_ids) != 1 or from_ids[0] == to_ids[0]:
                        return False
                    source = event_by_id[from_ids[0]]
                    target = event_by_id[to_ids[0]]
                    if source["event_fingerprint"] != target["event_fingerprint"]:
                        return False
                    duplicate_occurrences = event_occurrences[source["event_fingerprint"]]
                    source_occurrence = next(
                        (
                            index
                            for index, event in enumerate(duplicate_occurrences, start=1)
                            if event["event_id"] == source["event_id"]
                        ),
                        0,
                    )
                    if source_occurrence <= 1 or duplicate_occurrences[source_occurrence - 2]["event_id"] != target["event_id"]:
                        return False
                    intent_match = intent_by_relation_id.get(record_id)
                    if intent_match is None:
                        return False
                    transaction_id, intent_event, intent_relation = intent_match
                    if canonical_json_bytes(intent_event) != canonical_json_bytes(source):
                        return False
                    if canonical_json_bytes(intent_relation) != canonical_json_bytes(relation):
                        return False
                    commit = commits.get(transaction_id)
                    if commit is not None:
                        expected_commit = self._duplicate_transaction_commit(
                            transaction_id, intent_event, intent_relation
                        )
                        if any(commit[field] != expected_commit[field] for field in expected_commit):
                            return False
                relation_by_id[record_id] = relation
                relation_occurrences[fingerprint].append(relation)
            return True
        except (CandidateMemoryError, KeyError, TypeError, ValueError):
            return False

    def _valid_missing_newline_tail_unlocked(
        self,
        path: Path,
        fragment: bytes,
        prefix: bytes,
        receipts: Mapping[tuple[int, str], Mapping[str, Any]],
    ) -> bool:
        """Check a tail as a record of this exact ledger without writing.

        Duplicate recovery validates the record shape, deterministic IDs, and
        the exact cross-ledger transaction context needed for a duplicate.
        Completion is deferred to reconciliation, so a valid in-flight intent
        is not quarantined merely because its event or relation has not reached
        its authoritative ledger yet.
        """
        try:
            candidate = _decode_canonical_object(fragment, f"{path.name}:tail")
            prefix_rows = _parse_jsonl_bytes(path, prefix, receipts)
            if path == self.events_path:
                rows = [_event_from_record(row) for row in prefix_rows]
                candidate_event = _event_from_record(candidate)
                rows.append(candidate_event)
                occurrences: dict[str, int] = {}
                seen_ids: set[str] = set()
                for event in rows:
                    event_id = event["event_id"]
                    if event_id in seen_ids:
                        return False
                    fingerprint = event["event_fingerprint"]
                    occurrence = occurrences.get(fingerprint, 0) + 1
                    if event_id != _expected_event_id(event, occurrence):
                        return False
                    occurrences[fingerprint] = occurrence
                    seen_ids.add(event_id)
                candidate_occurrence = occurrences[candidate_event["event_fingerprint"]]
                if candidate_occurrence > 1:
                    relation_rows = [
                        _relation_from_record(row)
                        for row in _read_jsonl(self.relations_path)
                    ]
                    if not self._valid_duplicate_tail_context_unlocked(
                        candidate_event=candidate_event,
                        candidate_relation=None,
                        event_rows=rows,
                        relation_rows=relation_rows,
                    ):
                        return False
                return True

            if path == self.relations_path:
                event_rows = [_event_from_record(row) for row in _read_jsonl(self.events_path)]
                events = {event["event_id"]: event for event in event_rows}
                rows = [_relation_from_record(row) for row in prefix_rows]
                candidate_relation = _relation_from_record(candidate)
                rows.append(candidate_relation)
                occurrences: dict[str, int] = {}
                seen_ids: set[str] = set()
                for relation in rows:
                    record_id = relation["record_id"]
                    if record_id in seen_ids:
                        return False
                    fingerprint = relation_fingerprint(relation)
                    occurrence = occurrences.get(fingerprint, 0) + 1
                    if record_id != _expected_relation_id(relation, occurrence):
                        return False
                    endpoints = relation["from_candidate_ids"] + relation["to_candidate_ids"]
                    missing_endpoint = any(candidate_id not in events for candidate_id in endpoints)
                    if missing_endpoint:
                        if relation is not candidate_relation or relation["record_type"] != "DUPLICATE_OF":
                            return False
                    if not missing_endpoint:
                        self._validate_relation_scope(events, endpoints)
                    if relation["record_type"] == "DUPLICATE_OF":
                        from_ids = relation["from_candidate_ids"]
                        to_ids = relation["to_candidate_ids"]
                        if len(from_ids) != 1 or len(to_ids) != 1 or from_ids[0] == to_ids[0]:
                            return False
                        if (
                            not missing_endpoint
                            and events[from_ids[0]]["event_fingerprint"]
                            != events[to_ids[0]]["event_fingerprint"]
                        ):
                            return False
                    occurrences[fingerprint] = occurrence
                    seen_ids.add(record_id)
                if candidate_relation["record_type"] == "DUPLICATE_OF" and not self._valid_duplicate_tail_context_unlocked(
                    candidate_event=None,
                    candidate_relation=candidate_relation,
                    event_rows=event_rows,
                    relation_rows=rows,
                ):
                    return False
                return True

            if path == self.transactions_path:
                prefix_transactions = [_transaction_from_record(row) for row in prefix_rows]
                candidate_transaction = _transaction_from_record(candidate)
                intents, commits = self._split_duplicate_transactions(prefix_transactions)
                for transaction_id, intent in intents.items():
                    event = _event_from_record(intent["event"])
                    relation = _relation_from_record(intent["relation"])
                    self._validate_duplicate_transaction_shape(transaction_id, event, relation)
                    commit = commits.get(transaction_id)
                    if commit is not None:
                        expected_commit = self._duplicate_transaction_commit(transaction_id, event, relation)
                        if any(commit[field] != expected_commit[field] for field in expected_commit):
                            return False
                transaction_id = candidate_transaction["transaction_id"]
                if candidate_transaction["record_type"] == "DUPLICATE_APPEND_INTENT":
                    if transaction_id in intents or transaction_id in commits:
                        return False
                    event = _event_from_record(candidate_transaction["event"])
                    relation = _relation_from_record(candidate_transaction["relation"])
                    self._validate_duplicate_transaction_shape(transaction_id, event, relation)
                    return True
                if transaction_id in commits:
                    return False
                intent = intents.get(transaction_id)
                if intent is None:
                    return False
                event = _event_from_record(intent["event"])
                relation = _relation_from_record(intent["relation"])
                expected_commit = self._duplicate_transaction_commit(transaction_id, event, relation)
                return all(candidate_transaction[field] == expected_commit[field] for field in expected_commit)
        except (CandidateMemoryError, KeyError, TypeError, ValueError):
            return False
        return False

    def _recover_tail_unlocked(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"status": "NO_TAIL", "path": path.name}
        raw = path.read_bytes()
        if not raw or raw.endswith(b"\n"):
            return {"status": "NO_TAIL", "path": path.name}
        offset = raw.rfind(b"\n") + 1
        fragment = raw[offset:]
        fragment_hash = bytes_sha256(fragment)
        receipts = _read_recovery_receipts(_recovery_path(path), expected_ledger=path.name)
        tail_is_valid = self._valid_missing_newline_tail_unlocked(path, fragment, raw[:offset], receipts)
        existing = receipts.get((offset, fragment_hash))
        if existing is not None:
            if existing["action"] == "sealed_missing_newline" and not tail_is_valid:
                raise LedgerCorruptionError(f"sealed recovery receipt is invalid: {path.name}")
            _append_bytes(path, b"\n")
            return {"status": "RECOVERED", "path": path.name, "offset": offset, **existing}
        action = "sealed_missing_newline" if tail_is_valid else "quarantined_truncated_tail"
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "recovery_id": canonical_sha256(
                {"ledger": path.name, "offset": offset, "fragment_sha256": fragment_hash, "action": action}
            ),
            "ledger": path.name,
            "offset": offset,
            "fragment_sha256": fragment_hash,
            "action": action,
            "created_at": utc_now(),
        }
        _append_record(_recovery_path(path), receipt)
        _append_bytes(path, b"\n")
        return {"status": "RECOVERED", **receipt}

    def recover_truncated_tail(self, ledger: str = "events") -> dict[str, Any]:
        if ledger not in {"events", "relations"}:
            raise SchemaError("ledger must be 'events' or 'relations'")
        path = self.events_path if ledger == "events" else self.relations_path
        # Repair the selected authoritative ledger before reconciliation can
        # parse it.  A duplicate event/relation may already be complete except
        # for its final delimiter when the process crashed; reconciling first
        # would fail closed on that truncated line and prevent its own repair.
        with self._locked(reconcile=False):
            receipt = self._recover_tail_unlocked(path)
            self._reconcile_duplicate_transactions_unlocked()
            return receipt

    def ledger_snapshot(self) -> dict[str, Any]:
        """Return hashes/bytes for state-preservation tests without exposing data."""
        with self._locked_read_only():
            # A snapshot is a normal read of the candidate plane.  Parse both
            # ledgers before returning hashes so an injected/orphaned
            # DUPLICATE_OF cannot hide behind a metadata-only snapshot.
            self._events_unlocked()
            self._relations_unlocked()
            result: dict[str, Any] = {}
            for name, path in (("events", self.events_path), ("relations", self.relations_path)):
                raw = path.read_bytes() if path.exists() else b""
                result[name] = {"sha256": bytes_sha256(raw), "bytes": len(raw)}
            return result

    def _new_event(
        self,
        event_type: str,
        *,
        project_scope: str,
        claim_or_event: str,
        payload: Any = None,
        session_id: str = "",
        task_id: str = "",
        attempt_id: str = "",
        source_locators: Sequence[Any] | None = None,
        source_revisions: Sequence[Any] | None = None,
        source_hashes: Sequence[Any] | None = None,
        origin_actor: str = "host-controller",
        configured_route: str = "test-only",
        actual_model: str = "none",
        actual_harness: str = "candidate-memory-v1",
        authority_class: str = "host-observed",
        privacy_class: str = "public-synthetic",
        test_only: bool = False,
        cancelled: bool = False,
        stale: bool = False,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        scope = validate_project_scope(project_scope)
        event_name = _required_string(event_type, "event_type")
        if event_name not in EVENT_TYPES:
            raise SchemaError(f"unsupported event type: {event_name}")
        privacy = _required_string(privacy_class, "privacy_class")
        _validate_privacy(scope, privacy)
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "event_id": "",  # assigned under the store lock
            "event_type": event_name,
            "event_fingerprint": "",  # assigned after identity normalization
            "project_scope": scope,
            "session_id": _optional_string(session_id, "session_id"),
            "task_id": _optional_string(task_id, "task_id"),
            "attempt_id": _optional_string(attempt_id, "attempt_id"),
            "claim_or_event": _required_string(claim_or_event, "claim_or_event"),
            "payload": _normalize({} if payload is None else payload),
            "source_locators": _list_field(source_locators, "source_locators"),
            "source_revisions": _list_field(source_revisions, "source_revisions"),
            "source_hashes": _list_field(source_hashes, "source_hashes"),
            "origin_actor": _required_string(origin_actor, "origin_actor"),
            "configured_route": _required_string(configured_route, "configured_route"),
            "actual_model": _required_string(actual_model, "actual_model"),
            "actual_harness": _required_string(actual_harness, "actual_harness"),
            "authority_class": _required_string(authority_class, "authority_class"),
            "privacy_class": privacy,
            "test_only": _bool(test_only, "test_only"),
            "cancelled": _bool(cancelled, "cancelled"),
            "stale": _bool(stale, "stale"),
            "created_at": _required_string(created_at or utc_now(), "created_at"),
        }
        record["event_fingerprint"] = event_fingerprint(record)
        return record

    def append_event(self, event_type: str, **kwargs: Any) -> dict[str, Any]:
        """Append one explicitly typed event, applying frozen duplicate-B."""
        record = self._new_event(event_type, **kwargs)
        with self._locked():
            events = self._events_unlocked()
            relations = self._relations_unlocked()
            duplicates = [event for event in events if event["event_fingerprint"] == record["event_fingerprint"]]
            occurrence = len(duplicates) + 1
            record["event_id"] = _expected_event_id(record, occurrence)
            # A deterministic ID collision means the persisted history is not
            # what this store expects; never silently overwrite or reuse it.
            if any(event["event_id"] == record["event_id"] for event in events):
                raise LedgerCorruptionError(f"deterministic event ID collision: {record['event_id']}")
            if duplicates:
                relation = self._make_relation_unlocked(
                    "DUPLICATE_OF",
                    [record["event_id"]],
                    [duplicates[-1]["event_id"]],
                    reviewer_identity="deterministic-dedupe",
                    source_locators=record["source_locators"],
                    source_hashes=record["source_hashes"],
                    reason="exact event_fingerprint match; duplicate retained",
                    existing_relations=relations,
                )
                intent = {
                    "schema_version": SCHEMA_VERSION,
                    "transaction_id": _transaction_id(record, relation),
                    "record_type": "DUPLICATE_APPEND_INTENT",
                    "event": record,
                    "relation": relation,
                    "created_at": utc_now(),
                }
                _append_record(self.transactions_path, intent)
                _append_record(self.events_path, record)
                _append_record(self.relations_path, relation)
                _append_record(
                    self.transactions_path,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "transaction_id": intent["transaction_id"],
                        "record_type": "DUPLICATE_APPEND_COMMIT",
                        "event_id": record["event_id"],
                        "relation_id": relation["record_id"],
                        "commit_fingerprint": _commit_fingerprint(
                            intent["transaction_id"], record["event_id"], relation["record_id"]
                        ),
                        "created_at": utc_now(),
                    },
                )
            else:
                _append_record(self.events_path, record)
        return dict(record)

    def _make_relation_unlocked(
        self,
        record_type: str,
        from_candidate_ids: Sequence[str],
        to_candidate_ids: Sequence[str],
        *,
        reviewer_identity: str,
        source_locators: Sequence[Any] | None = None,
        source_hashes: Sequence[Any] | None = None,
        reason: str = "",
        created_at: str | None = None,
        existing_relations: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        relation_name = _required_string(record_type, "record_type")
        if relation_name not in RELATION_TYPES:
            raise SchemaError(f"unsupported relation type: {relation_name}")
        from_ids = [_required_string(value, "from_candidate_ids") for value in from_candidate_ids]
        to_ids = [_required_string(value, "to_candidate_ids") for value in to_candidate_ids]
        if not from_ids or not to_ids:
            raise SchemaError("relation endpoints cannot be empty")
        identity = {
            "schema_version": SCHEMA_VERSION,
            "record_type": relation_name,
            "from_candidate_ids": from_ids,
            "to_candidate_ids": to_ids,
            "reviewer_identity": _required_string(reviewer_identity, "reviewer_identity"),
            "source_locators": _list_field(source_locators, "source_locators"),
            "source_hashes": _list_field(source_hashes, "source_hashes"),
            "reason": _optional_string(reason, "reason"),
        }
        digest = canonical_sha256(identity)
        occurrence = sum(
            1
            for relation in existing_relations
            if relation.get("record_id", "").startswith(f"relation-{digest[:32]}-")
        ) + 1
        return {
            **identity,
            "record_id": f"relation-{digest[:32]}-{occurrence:04d}",
            "created_at": _required_string(created_at or utc_now(), "created_at"),
        }

    def append_relation(
        self,
        record_type: str,
        from_candidate_ids: Sequence[str],
        to_candidate_ids: Sequence[str],
        *,
        reviewer_identity: str = "host-controller",
        source_locators: Sequence[Any] | None = None,
        source_hashes: Sequence[Any] | None = None,
        reason: str = "",
        created_at: str | None = None,
    ) -> dict[str, Any]:
        relation_name = _required_string(record_type, "record_type")
        if relation_name == "DUPLICATE_OF":
            raise SchemaError("DUPLICATE_OF is reserved for duplicate-B append transactions")
        with self._locked():
            events = self._events_unlocked()
            relations = self._relations_unlocked()
            event_map = {event["event_id"]: event for event in events}
            endpoints = list(from_candidate_ids) + list(to_candidate_ids)
            if not endpoints or any(candidate_id not in event_map for candidate_id in endpoints):
                raise LedgerCorruptionError("relation endpoints must reference existing candidates")
            scopes = {event_map[candidate_id]["project_scope"] for candidate_id in endpoints}
            if len(scopes) != 1:
                raise ScopeError("relations cannot cross project scopes")
            privacy = {event_map[candidate_id]["privacy_class"] for candidate_id in endpoints}
            if len(privacy) != 1:
                raise ScopeError("relations cannot cross privacy classes")
            relation = self._make_relation_unlocked(
                record_type,
                from_candidate_ids,
                to_candidate_ids,
                reviewer_identity=reviewer_identity,
                source_locators=source_locators,
                source_hashes=source_hashes,
                reason=reason,
                created_at=created_at,
                existing_relations=relations,
            )
            if any(existing["record_id"] == relation["record_id"] for existing in relations):
                raise LedgerCorruptionError(f"deterministic relation ID collision: {relation['record_id']}")
            _append_record(self.relations_path, relation)
            return dict(relation)

    def capture_test_only(self, event_type: str, **kwargs: Any) -> dict[str, Any]:
        """Explicitly capture only the four harmless real-path shadow types."""
        if event_type not in TEST_SHADOW_EVENT_TYPES:
            raise SchemaError("test-only capture seam permits only lifecycle/source events")
        if kwargs.get("test_only") is not True:
            raise SchemaError("test-only capture requires test_only=True")
        return self.append_event(event_type, **kwargs)

    def _view_unlocked(self) -> list[dict[str, Any]]:
        events = self._events_unlocked()
        relations = self._relations_unlocked()
        state: dict[str, str] = {
            event["event_id"]: ("CANCELLED" if event["cancelled"] else "STALE" if event["stale"] else "PENDING")
            for event in events
        }
        duplicate_of: dict[str, list[str]] = {event["event_id"]: [] for event in events}
        relation_ids: dict[str, list[str]] = {event["event_id"]: [] for event in events}
        for relation in relations:
            touched = set(relation["from_candidate_ids"] + relation["to_candidate_ids"])
            for candidate_id in touched:
                relation_ids[candidate_id].append(relation["record_id"])
            relation_type = relation["record_type"]
            if relation_type == "DUPLICATE_OF":
                for duplicate_id in relation["from_candidate_ids"]:
                    duplicate_of[duplicate_id].extend(relation["to_candidate_ids"])
            elif relation_type == "CONTRADICTS":
                for candidate_id in touched:
                    if state[candidate_id] == "PENDING":
                        state[candidate_id] = "CONFLICTING"
            elif relation_type == "SUPERSEDES":
                for candidate_id in relation["to_candidate_ids"]:
                    if state[candidate_id] not in {"CANCELLED", "STALE"}:
                        state[candidate_id] = "SUPERSEDED"
            elif relation_type == "REJECTED_BECAUSE":
                for candidate_id in relation["from_candidate_ids"]:
                    if state[candidate_id] not in {"CANCELLED", "STALE"}:
                        state[candidate_id] = "REJECTED"
            elif relation_type == "ACCEPTED_FOR_UPDATE_REQUEST":
                for candidate_id in relation["from_candidate_ids"]:
                    if state[candidate_id] == "PENDING":
                        state[candidate_id] = "ACCEPTED_FOR_UPDATE_REQUEST"
            elif relation_type == "MARKED_STALE":
                for candidate_id in relation["from_candidate_ids"]:
                    state[candidate_id] = "STALE"
            elif relation_type == "CANCELLED_BY":
                for candidate_id in relation["from_candidate_ids"]:
                    state[candidate_id] = "CANCELLED"
        return [
            {
                **dict(event),
                "candidate_id": event["event_id"],
                "state": state[event["event_id"]],
                "duplicate_of": sorted(set(duplicate_of[event["event_id"]])),
                "relation_ids": relation_ids[event["event_id"]],
            }
            for event in events
        ]

    @staticmethod
    def _visible_rows(
        rows: Sequence[Mapping[str, Any]],
        scope: str,
        *,
        include_private: bool,
        include_hidden_evaluation: bool,
    ) -> list[dict[str, Any]]:
        visible: list[dict[str, Any]] = []
        for row in rows:
            if row["project_scope"] != scope:
                continue
            privacy = row["privacy_class"]
            if privacy == "hidden-evaluation" and not include_hidden_evaluation:
                continue
            if privacy == "owner-private" and not include_private:
                continue
            visible.append(dict(row))
        return visible

    def query(
        self,
        *,
        project_scope: str | None = None,
        include_private: bool = False,
        include_hidden_evaluation: bool = False,
    ) -> list[dict[str, Any]]:
        """Return only explicitly scoped candidates; missing scope fails closed."""
        scope = validate_project_scope(project_scope)
        if not isinstance(include_private, bool) or not isinstance(include_hidden_evaluation, bool):
            raise ScopeError("privacy query flags must be boolean")
        with self._locked():
            rows = self._view_unlocked()
        return self._visible_rows(
            rows,
            scope,
            include_private=include_private,
            include_hidden_evaluation=include_hidden_evaluation,
        )

    def current_view(self, *, project_scope: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.query(project_scope=project_scope, **kwargs)

    def rebuild_current_view(
        self,
        *,
        project_scope: str | None = None,
        include_private: bool = False,
        include_hidden_evaluation: bool = False,
    ) -> list[dict[str, Any]]:
        """Recompute the disposable view from the authoritative ledgers."""
        return self.query(
            project_scope=project_scope,
            include_private=include_private,
            include_hidden_evaluation=include_hidden_evaluation,
        )

    def _atomic_write_derived_unlocked(self, path: Path, data: bytes) -> None:
        """Replace a disposable derived file without touching either ledger."""
        if not data:
            raise AppendError("empty derived write is forbidden")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        fd: int | None = None
        try:
            fd = os.open(str(temporary), flags, 0o600)
            written = os.write(fd, data)
            if written != len(data):
                raise AppendError(f"short derived write: {written}/{len(data)} bytes")
            os.fsync(fd)
            os.close(fd)
            fd = None
            os.replace(str(temporary), str(path))
            directory_flag = getattr(os, "O_DIRECTORY", 0)
            if directory_flag:
                directory_fd = os.open(str(path.parent), os.O_RDONLY | directory_flag)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if fd is not None:
                os.close(fd)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _validate_index_payload(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != set(INDEX_FIELDS):
            raise LedgerCorruptionError("derived candidate index schema is invalid")
        if payload["schema_version"] != SCHEMA_VERSION or not isinstance(payload["schema_version"], int):
            raise LedgerCorruptionError("derived candidate index version is invalid")
        if payload["index_type"] != "candidate-memory-current-view":
            raise LedgerCorruptionError("derived candidate index type is invalid")
        validate_project_scope(payload["project_scope"])
        if not isinstance(payload["include_private"], bool) or not isinstance(
            payload["include_hidden_evaluation"], bool
        ):
            raise LedgerCorruptionError("derived candidate index privacy flags are invalid")
        for field in ("event_ledger_sha256", "relation_ledger_sha256"):
            if not isinstance(payload[field], str) or len(payload[field]) != 64:
                raise LedgerCorruptionError(f"derived candidate index {field} is invalid")
        if not isinstance(payload["candidates"], list):
            raise LedgerCorruptionError("derived candidate index candidates are invalid")
        return payload

    def rebuild_index(
        self,
        *,
        project_scope: str | None = None,
        include_private: bool = False,
        include_hidden_evaluation: bool = False,
    ) -> list[dict[str, Any]]:
        """Atomically rebuild a disposable index from both JSONL ledgers."""
        scope = validate_project_scope(project_scope)
        if not isinstance(include_private, bool) or not isinstance(include_hidden_evaluation, bool):
            raise ScopeError("privacy query flags must be boolean")
        with self._locked():
            rows = self._visible_rows(
                self._view_unlocked(),
                scope,
                include_private=include_private,
                include_hidden_evaluation=include_hidden_evaluation,
            )
            payload = {
                "schema_version": SCHEMA_VERSION,
                "index_type": "candidate-memory-current-view",
                "project_scope": scope,
                "include_private": include_private,
                "include_hidden_evaluation": include_hidden_evaluation,
                "event_ledger_sha256": bytes_sha256(
                    self.events_path.read_bytes() if self.events_path.exists() else b""
                ),
                "relation_ledger_sha256": bytes_sha256(
                    self.relations_path.read_bytes() if self.relations_path.exists() else b""
                ),
                "candidates": rows,
            }
            self._validate_index_payload(payload)
            self._atomic_write_derived_unlocked(self.index_path, canonical_json_bytes(payload))
        return rows

    # A descriptive alias keeps callers from confusing this disposable index
    # with either authoritative JSONL ledger.
    rebuild_view_index = rebuild_index

    def read_index(
        self,
        *,
        project_scope: str | None = None,
        include_private: bool = False,
        include_hidden_evaluation: bool = False,
    ) -> list[dict[str, Any]]:
        """Read an index only when it still exactly matches a fresh rebuild."""
        scope = validate_project_scope(project_scope)
        if not isinstance(include_private, bool) or not isinstance(include_hidden_evaluation, bool):
            raise ScopeError("privacy query flags must be boolean")
        with self._locked():
            if not self.index_path.exists():
                raise LedgerCorruptionError("derived candidate index is absent")
            raw = self.index_path.read_bytes()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise LedgerCorruptionError("derived candidate index is malformed") from exc
            self._validate_index_payload(payload)
            if canonical_json_bytes(payload) != raw:
                raise LedgerCorruptionError("derived candidate index is not canonical")
            if payload["project_scope"] != scope:
                raise ScopeError("derived candidate index scope does not match query scope")
            if payload["include_private"] != include_private or payload["include_hidden_evaluation"] != include_hidden_evaluation:
                raise ScopeError("derived candidate index privacy flags do not match query")
            current_event_hash = bytes_sha256(
                self.events_path.read_bytes() if self.events_path.exists() else b""
            )
            current_relation_hash = bytes_sha256(
                self.relations_path.read_bytes() if self.relations_path.exists() else b""
            )
            if payload["event_ledger_sha256"] != current_event_hash or payload["relation_ledger_sha256"] != current_relation_hash:
                raise LedgerCorruptionError("derived candidate index is stale")
            expected = self._visible_rows(
                self._view_unlocked(),
                scope,
                include_private=include_private,
                include_hidden_evaluation=include_hidden_evaluation,
            )
            if canonical_json_bytes(payload["candidates"]) != canonical_json_bytes(expected):
                raise LedgerCorruptionError("derived candidate index does not match authoritative view")
            return [dict(row) for row in payload["candidates"]]

    def _resolve_source_locator(self, locator: Any) -> tuple[str, Path]:
        value = _required_string(locator, "source_locators")
        drive_path = len(value) >= 3 and value[1] == ":" and value[2] in "/\\"
        parsed = urlsplit(value)
        if parsed.scheme and not drive_path:
            if parsed.scheme.lower() != "file":
                raise SourceVerificationError("only local file source locators are allowed")
            if parsed.netloc not in {"", "localhost"}:
                raise SourceVerificationError("remote file hosts are not allowed")
            path_text = unquote(parsed.path)
            if os.name == "nt" and len(path_text) >= 3 and path_text[0] == "/" and path_text[2] == ":":
                path_text = path_text[1:]
        else:
            # urlsplit treats a Windows drive letter as a URI scheme; retain
            # the original drive path while discarding an optional anchor.
            path_text = value.split("#", 1)[0].split("?", 1)[0]
            path_text = unquote(path_text)
        if not path_text:
            raise SourceVerificationError("source locator has no local path")
        path = Path(path_text)
        if not path.is_absolute():
            path = self.root / path
        if str(path).startswith("\\\\"):
            raise SourceVerificationError("UNC source locators are not allowed")
        try:
            resolved = path.resolve(strict=False)
        except OSError as exc:
            raise SourceVerificationError(f"cannot resolve source locator: {value}") from exc
        return value, resolved

    @staticmethod
    def _expected_digest(value: Any, field: str) -> str:
        expected = _required_string(value, field).lower()
        if expected.startswith("sha256:"):
            expected = expected[7:]
        if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
            raise SourceVerificationError(f"{field} must be a SHA-256 digest")
        return expected

    def reopen_sources(self, candidate_or_event: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Reopen every cited local source and verify its declared hash/revision."""
        if not isinstance(candidate_or_event, Mapping):
            raise SchemaError("source verification requires an event mapping")
        locators = candidate_or_event.get("source_locators", [])
        revisions = candidate_or_event.get("source_revisions", [])
        hashes = candidate_or_event.get("source_hashes", [])
        if not isinstance(locators, list) or not isinstance(revisions, list) or not isinstance(hashes, list):
            raise SchemaError("source provenance fields must be lists")
        if not locators:
            if revisions or hashes:
                raise SourceVerificationError("source metadata exists without a locator")
            return []
        if len(hashes) != len(locators):
            raise SourceVerificationError("every cited source requires one expected hash")
        if revisions and len(revisions) != len(locators):
            raise SourceVerificationError("source revisions must align with source locators")
        reopened: list[dict[str, Any]] = []
        for index, locator in enumerate(locators):
            original_locator, path = self._resolve_source_locator(locator)
            if not path.is_file():
                raise SourceVerificationError(f"cited source is not a readable local file: {original_locator}")
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise SourceVerificationError(f"cannot reopen cited source: {original_locator}") from exc
            digest = hashlib.sha256(content).hexdigest()
            expected_hash = self._expected_digest(hashes[index], "source_hashes")
            if digest != expected_hash:
                raise SourceVerificationError(f"source hash mismatch: {original_locator}")
            if revisions:
                expected_revision = self._expected_digest(revisions[index], "source_revisions")
                if digest != expected_revision:
                    raise SourceVerificationError(f"source revision mismatch: {original_locator}")
            reopened.append(
                {
                    "locator": original_locator,
                    "path": str(path),
                    "source_revision": f"sha256:{digest}",
                    "source_hash": digest,
                    "bytes": len(content),
                }
            )
        return reopened

    verify_source_binding = reopen_sources

    def build_memory_update_request(
        self,
        candidate_ids: Sequence[str],
        *,
        project_scope: str | None = None,
        include_private: bool = False,
        include_hidden_evaluation: bool = False,
    ) -> dict[str, Any]:
        """Build an in-memory, candidate-bound proposal without any writes."""
        scope = validate_project_scope(project_scope)
        if not isinstance(candidate_ids, Sequence) or isinstance(candidate_ids, (str, bytes, bytearray)):
            raise SchemaError("candidate_ids must be a sequence of IDs")
        selected_ids = [_required_string(candidate_id, "candidate_ids") for candidate_id in candidate_ids]
        if not selected_ids:
            raise ProposalError("at least one candidate ID is required")
        if len(set(selected_ids)) != len(selected_ids):
            raise ProposalError("candidate_ids must not repeat")
        if not isinstance(include_private, bool) or not isinstance(include_hidden_evaluation, bool):
            raise ScopeError("privacy query flags must be boolean")
        with self._locked_read_only():
            all_rows = self._view_unlocked()
            visible_rows = self._visible_rows(
                all_rows,
                scope,
                include_private=include_private,
                include_hidden_evaluation=include_hidden_evaluation,
            )
            row_map = {row["candidate_id"]: row for row in visible_rows}
            missing = [candidate_id for candidate_id in selected_ids if candidate_id not in row_map]
            if missing:
                raise ScopeError("candidate is absent from the explicitly scoped view")
            selected_rows = [row_map[candidate_id] for candidate_id in selected_ids]
            blocked = [
                (row["candidate_id"], row["state"])
                for row in selected_rows
                if row["state"] not in {"PENDING", "ACCEPTED_FOR_UPDATE_REQUEST"}
            ]
            if blocked:
                raise ProposalError(f"explicit relation state blocks proposal: {blocked}")
            relations = self._relations_unlocked()
            visible_set = {row["candidate_id"] for row in visible_rows}
            selected_set = set(selected_ids)
            selected_relations = [
                relation
                for relation in relations
                if (
                    set(relation["from_candidate_ids"] + relation["to_candidate_ids"]).issubset(visible_set)
                    and bool(
                        set(relation["from_candidate_ids"] + relation["to_candidate_ids"]) & selected_set
                    )
                )
            ]
            evidence_lineage: list[dict[str, Any]] = []
            for row in selected_rows:
                locators = row["source_locators"]
                revisions = row["source_revisions"]
                hashes = row["source_hashes"]
                if not isinstance(locators, list) or not locators:
                    raise ProposalError("every selected candidate requires source locators")
                if not isinstance(revisions, list) or not isinstance(hashes, list):
                    raise ProposalError("every selected candidate requires source revisions and hashes")
                if len(locators) != len(revisions) or len(locators) != len(hashes):
                    raise ProposalError("candidate source metadata must align one-to-one")
                for locator, revision, source_hash in zip(locators, revisions, hashes):
                    if not isinstance(locator, str) or not locator.strip():
                        raise ProposalError("candidate source locators must be non-empty")
                    try:
                        revision_digest = self._expected_digest(revision, "source_revisions")
                        hash_digest = self._expected_digest(source_hash, "source_hashes")
                    except SourceVerificationError as exc:
                        raise ProposalError("candidate source revisions and hashes must be SHA-256 digests") from exc
                    if revision_digest != hash_digest:
                        raise ProposalError("candidate source revision and hash must match exactly")
                reopened = self.reopen_sources(row)
                if len(reopened) != len(locators) or any(
                    item["source_hash"] != self._expected_digest(source_hash, "source_hashes")
                    or item["source_revision"] != f"sha256:{self._expected_digest(revision, 'source_revisions')}"
                    for item, revision, source_hash in zip(reopened, revisions, hashes)
                ):
                    raise ProposalError("candidate source reopen did not match declared evidence")
                evidence_lineage.append(
                    {
                        "candidate_id": row["candidate_id"],
                        "event_id": row["event_id"],
                        "event_fingerprint": row["event_fingerprint"],
                        "source_locators": list(row["source_locators"]),
                        "source_revisions": list(row["source_revisions"]),
                        "source_hashes": list(row["source_hashes"]),
                        "reopened_sources": reopened,
                    }
                )
            identity = {
                "schema_version": SCHEMA_VERSION,
                "project_scope": scope,
                "candidate_ids": selected_ids,
                "candidate_fingerprints": [row["event_fingerprint"] for row in selected_rows],
                "evidence_hashes": [
                    source["source_hash"]
                    for lineage in evidence_lineage
                    for source in lineage["reopened_sources"]
                ],
                "relation_ids": [relation["record_id"] for relation in selected_relations],
            }
            return {
                "schema_version": SCHEMA_VERSION,
                "proposal_type": "memory_update_request",
                "proposal_id": f"memory-update-{canonical_sha256(identity)}",
                "project_scope": scope,
                "candidate_ids": selected_ids,
                "candidates": [dict(row) for row in selected_rows],
                "evidence_lineage": evidence_lineage,
                "explicit_relations": [dict(relation) for relation in selected_relations],
                "dry_run": True,
                "canonical_write_count": 0,
                "model_calls": 0,
                "created_at": utc_now(),
            }

    build_proposal = build_memory_update_request
    propose_memory_update = build_memory_update_request


__all__ = [
    "AppendError",
    "CandidateMemoryError",
    "CandidateMemoryStore",
    "EVENT_FIELDS",
    "EVENT_TYPES",
    "INDEX_FIELDS",
    "LedgerCorruptionError",
    "PRIVACY_CLASSES",
    "ProposalError",
    "PROJECT_SCOPES",
    "RELATION_FIELDS",
    "RELATION_TYPES",
    "SchemaError",
    "ScopeError",
    "SourceVerificationError",
    "TEST_SHADOW_EVENT_TYPES",
    "TruncatedTailError",
    "VIEW_STATES",
    "bytes_sha256",
    "canonical_json_bytes",
    "canonical_sha256",
    "event_fingerprint",
    "record_sha256",
    "validate_project_scope",
]
