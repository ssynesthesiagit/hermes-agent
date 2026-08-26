"""Deterministic runtime identity, capability, provenance, and evidence gate.

This is a narrow edge layer around Hermes' existing lifecycle/tool hooks and
verification evidence ledger.  It does not schedule work, dispatch models, or
store conversational memory.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any, Iterable, Mapping


POLICY_GENERATION = "YATIMA_FLEET_RUNTIME_INTEGRITY_POLICY_V1"
PASS_TERMINAL = (
    "YATIMA_FLEET_V4_RUNTIME_INTEGRITY_CANARY_PASSED_"
    "ACTIVATION_STILL_OWNER_GATED"
)
FAIL_TERMINAL = (
    "YATIMA_FLEET_V4_RUNTIME_INTEGRITY_CANARY_FAILED_ACTIVATION_PROHIBITED"
)


class CapabilityTier(IntEnum):
    C0_DETERMINISTIC_ONLY = 0
    C1_ROUTINE_ASSISTANT = 1
    C2_BOUNDED_SYNTHESIS = 2
    C3_RESEARCH_EXECUTION = 3
    R4_FRONTIER_JUDGMENT = 4


def _tier(value: str | CapabilityTier) -> CapabilityTier:
    if isinstance(value, CapabilityTier):
        return value
    try:
        return CapabilityTier[str(value).strip().upper()]
    except (KeyError, AttributeError):
        return CapabilityTier.C0_DETERMINISTIC_ONLY


def validate_policy(policy: Any) -> dict[str, Any]:
    """Validate the owner-gated runtime-integrity policy contract."""

    if not isinstance(policy, dict):
        raise ValueError("runtime-integrity policy must be a mapping")
    required = {
        "schema_version",
        "status",
        "architecture_generation",
        "execution_enabled",
        "bounded_activation_allowed",
        "activation",
        "capability_model",
        "pre_activation_integrity_canary",
    }
    missing = sorted(required - set(policy))
    if missing:
        raise ValueError(
            f"runtime-integrity policy missing fields: {', '.join(missing)}"
        )
    if policy["schema_version"] != 1:
        raise ValueError("unsupported runtime-integrity policy schema")
    if policy["architecture_generation"] != "V4":
        raise ValueError("runtime-integrity policy must target V4")
    if policy["status"] != "OWNER_DIRECTED_PRE_ACTIVATION_POLICY":
        raise ValueError("runtime-integrity policy is not owner-directed pre-activation")
    if (
        policy["execution_enabled"] is not False
        or policy["bounded_activation_allowed"] is not False
    ):
        raise ValueError(
            "runtime-integrity policy must remain disabled before owner activation"
        )

    activation = policy["activation"]
    required_gates = (
        "button3_final_reconciliation_required",
        "runtime_integrity_canary_required",
    )
    if (
        not isinstance(activation, dict)
        or any(activation.get(key) is not True for key in required_gates)
        or activation.get("automatic_activation") is not False
    ):
        raise ValueError("invalid owner activation gates")

    capability_model = policy["capability_model"]
    tiers = (
        capability_model.get("tiers")
        if isinstance(capability_model, dict)
        else None
    )
    tier_names = list(tiers) if isinstance(tiers, (list, dict)) else None
    if not isinstance(tiers, dict) or list(tiers) != [
        tier.name for tier in CapabilityTier
    ]:
        raise ValueError("runtime-integrity policy capability tiers are invalid")

    canary = policy["pre_activation_integrity_canary"]
    if (
        not isinstance(canary, dict)
        or canary.get("pass_terminal") != PASS_TERMINAL
        or canary.get("fail_terminal") != FAIL_TERMINAL
    ):
        raise ValueError("runtime-integrity policy canary terminals are invalid")
    return policy


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExperimentReceiptValidation:
    action_proven: bool
    publication_allowed: bool
    event: str
    terminals: tuple[str, ...]
    receipt_sha256: str
    missing_evidence: tuple[str, ...] = ()


def _hash_mapping_valid(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and bool(value)
        and all(str(name) and _is_sha256(digest) for name, digest in value.items())
    )


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def validate_experiment_receipt(
    receipt: dict[str, Any],
    *,
    ledger: "RuntimeIntegrityLedger | None" = None,
) -> ExperimentReceiptValidation:
    """Require external runtime/tool evidence for completed experiment claims."""
    receipt = receipt if isinstance(receipt, dict) else {}
    invocation = receipt.get("invocation")
    started = _parse_timestamp(receipt.get("started_at"))
    ended = _parse_timestamp(receipt.get("ended_at"))
    evidence_ledger = ledger or RuntimeIntegrityLedger()
    runtime_ref = str(receipt.get("runtime_envelope_sha256") or "")
    tool_ref = str(receipt.get("tool_receipt_sha256") or "")
    review_ref = str(receipt.get("independent_review_receipt") or "")
    artifact_hashes = tuple(
        str(digest)
        for mapping in (receipt.get("input_sha256"), receipt.get("output_sha256"))
        if isinstance(mapping, dict)
        for digest in mapping.values()
    )
    trusted_runtime = evidence_ledger.envelope_by_hash(runtime_ref) is not None
    trusted_tool = evidence_ledger.tool_result_receipt(tool_ref)
    trusted_artifacts = bool(artifact_hashes) and all(
        evidence_ledger.artifact_digest_exists(digest) for digest in artifact_hashes
    )
    trusted_review = evidence_ledger.independent_verification_covers(
        review_ref, (tool_ref,)
    )
    trusted_refs = bool(
        trusted_runtime
        and trusted_tool is not None
        and trusted_tool.runtime_envelope_sha256 == runtime_ref
        and trusted_artifacts
    )
    source_revision = str(receipt.get("source_revision") or "")
    source_revision_shape = len(source_revision) == 40 and all(
        char in "0123456789abcdefABCDEF" for char in source_revision
    )
    checks = {
        "RUN_ID": bool(receipt.get("run_id")),
        "ACTUAL_INVOCATION": isinstance(invocation, list)
        and bool(invocation)
        and all(isinstance(part, str) and part for part in invocation),
        "START_AND_END_TIME": started is not None and ended is not None,
        "TIMESTAMP_ORDER": started is not None and ended is not None and started <= ended,
        "EXIT_STATUS": isinstance(receipt.get("exit_status"), int)
        and receipt.get("exit_status") == 0,
        "SOURCE_REVISION": source_revision_shape
        and evidence_ledger.source_revision_exists(source_revision, tool_ref),
        "INPUT_HASHES": _hash_mapping_valid(receipt.get("input_sha256")),
        "OUTPUT_HASHES": _hash_mapping_valid(receipt.get("output_sha256")),
        "RUNTIME_IDENTITY": trusted_runtime,
        "TRUSTED_LEDGER_REFERENCES": trusted_refs,
    }
    missing = tuple(name for name, passed in checks.items() if not passed)
    proven = not missing
    reviewed = trusted_review
    publishable = proven and reviewed
    if proven:
        terminals = ("EXPERIMENT_RECEIPT_VERIFIED",)
        if not reviewed:
            terminals += ("PUBLICATION_DENIED_UNVERIFIED_EVIDENCE",)
        event = "EXPERIMENT_RECEIPT_VERIFIED"
    else:
        terminals = (
            "NO_EXECUTION_RECEIPT",
            "ACTION_NOT_PROVEN",
            "PUBLICATION_DENIED_UNVERIFIED_EVIDENCE",
        )
        event = "ACTION_NOT_PROVEN"
    safe_receipt = {
        "checks": checks,
        "independently_reviewed": reviewed,
        "run_id": str(receipt.get("run_id") or ""),
    }
    return ExperimentReceiptValidation(
        action_proven=proven,
        publication_allowed=publishable,
        event=event,
        terminals=terminals,
        receipt_sha256=_sha256(safe_receipt),
        missing_evidence=missing,
    )


@dataclass(frozen=True)
class DebateReceiptValidation:
    classification: str
    event: str
    experimental_multi_model_evidence: bool
    publication_allowed: bool
    terminals: tuple[str, ...]
    receipt_sha256: str
    missing_evidence: tuple[str, ...] = ()


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdefABCDEF" for char in text)


def validate_debate_receipt(
    receipt: dict[str, Any],
    *,
    ledger: "RuntimeIntegrityLedger | None" = None,
) -> DebateReceiptValidation:
    """Distinguish trusted multi-model execution from one-model role-play."""
    receipt = receipt if isinstance(receipt, dict) else {}
    participants = receipt.get("participants")
    participants = participants if isinstance(participants, list) else []
    external_calls = int(receipt.get("external_participant_call_count") or 0)
    sessions = {
        str(item.get("session_id") or "")
        for item in participants
        if isinstance(item, dict) and item.get("session_id")
    }
    identities = {
        (
            str(item.get("provider") or ""),
            str(item.get("model") or ""),
            str(item.get("session_id") or ""),
        )
        for item in participants
        if isinstance(item, dict)
    }
    transcripts = receipt.get("round_transcript_sha256")
    transcripts = transcripts if isinstance(transcripts, list) else []
    aggregator = receipt.get("aggregator")
    evidence_ledger = ledger or RuntimeIntegrityLedger()
    participant_envelopes = []
    for item in participants:
        if not isinstance(item, dict):
            continue
        envelope = evidence_ledger.envelope_by_hash(
            str(item.get("runtime_envelope_sha256") or "")
        )
        if (
            envelope is not None
            and envelope.provider == str(item.get("provider") or "")
            and envelope.exact_model_id == str(item.get("model") or "")
            and envelope.session_id == str(item.get("session_id") or "")
        ):
            participant_envelopes.append(envelope)
    harness_ref = str(receipt.get("harness_tool_receipt_sha256") or "")
    harness_receipt = evidence_ledger.tool_result_receipt(harness_ref)
    artifact_digests = (
        str(receipt.get("stack_manifest_sha256") or ""),
        *(str(value) for value in transcripts),
        str(receipt.get("final_output_sha256") or ""),
    )
    trusted_artifacts = bool(artifact_digests) and all(
        evidence_ledger.artifact_digest_exists(digest) for digest in artifact_digests
    )
    independent_ref = str(receipt.get("independent_validation_receipt") or "")
    independent = evidence_ledger.independent_verification_covers(
        independent_ref, (harness_ref,)
    )

    checks = {
        "HARNESS_RECEIPT": bool(receipt.get("harness_run_id"))
        and harness_receipt is not None,
        "STACK_MANIFEST": evidence_ledger.artifact_digest_exists(
            str(receipt.get("stack_manifest_sha256") or "")
        ),
        "PARTICIPANT_RUNTIME_IDENTITIES": (
            len(identities) >= 2
            and all(all(value for value in identity) for identity in identities)
            and len(participant_envelopes) == len(participants)
        ),
        "DISTINCT_SESSION_PROOF": len(sessions) >= 2,
        "EXECUTION_EVIDENCE": external_calls >= 2,
        "TRANSCRIPT_HASHES": bool(transcripts)
        and all(evidence_ledger.artifact_digest_exists(value) for value in transcripts),
        "AGGREGATOR_IDENTITY": isinstance(aggregator, dict)
        and all(aggregator.get(key) for key in ("provider", "model", "session_id")),
        "FINAL_OUTPUT_HASH": evidence_ledger.artifact_digest_exists(
            str(receipt.get("final_output_sha256") or "")
        ),
        "INDEPENDENT_VALIDATION": independent,
        "TRUSTED_LEDGER_REFERENCES": bool(
            harness_receipt is not None and trusted_artifacts and independent
        ),
    }
    missing = tuple(name for name, passed in checks.items() if not passed)
    verified = not missing
    if verified:
        terminals = ("DEBATE_RECEIPT_VERIFIED",)
        classification = "MULTI_MODEL_DEBATE"
        event = "DEBATE_RECEIPT_VERIFIED"
    else:
        terminals = (
            "SIMULATION",
            "NOT_MULTI_MODEL_DEBATE",
            "NOT_EXPERIMENTAL_MULTI_MODEL_EVIDENCE",
            "PUBLICATION_DENIED_UNVERIFIED_EVIDENCE",
        )
        classification = "SIMULATION"
        event = "NOT_MULTI_MODEL_DEBATE"
    safe_receipt = {
        "checks": checks,
        "participant_identities": sorted(identities),
        "external_participant_call_count": external_calls,
        "classification": classification,
    }
    return DebateReceiptValidation(
        classification=classification,
        event=event,
        experimental_multi_model_evidence=verified,
        publication_allowed=verified,
        terminals=terminals,
        receipt_sha256=_sha256(safe_receipt),
        missing_evidence=missing,
    )


@dataclass(frozen=True)
class ArtifactReadback:
    passed: bool
    event: str
    material_status: str
    artifact_sha256: str
    readback_receipt_sha256: str
    reason: str = ""


def _acceptance_contract_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _acceptance_contract_matches(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _acceptance_contract_matches(got, wanted)
            for got, wanted in zip(actual, expected)
        )
    return actual == expected


def readback_artifact(
    path: str | Path,
    *,
    required: bool = True,
    required_json_keys: tuple[str, ...] = (),
    expected_sha256: str | None = None,
    acceptance_contract: Mapping[str, Any] | None = None,
) -> ArtifactReadback:
    """Read and validate a required artifact before a material PASS."""
    artifact = Path(path)
    digest = ""
    reason = ""
    try:
        data = artifact.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if required and not data:
            reason = "required_artifact_empty"
        if not reason and expected_sha256 and digest.lower() != expected_sha256.lower():
            reason = "artifact_sha256_mismatch"
        if not reason and (required_json_keys or acceptance_contract is not None):
            parsed = json.loads(data.decode("utf-8"))
            if not isinstance(parsed, dict) or any(
                key not in parsed for key in required_json_keys
            ):
                reason = "artifact_acceptance_contract_mismatch"
            elif acceptance_contract is not None and not _acceptance_contract_matches(
                parsed, acceptance_contract
            ):
                reason = "artifact_acceptance_contract_mismatch"
    except FileNotFoundError:
        reason = "required_artifact_missing" if required else "artifact_missing"
    except (OSError, UnicodeError, json.JSONDecodeError):
        reason = "artifact_unreadable_or_unparseable"

    passed = not reason
    event = "ARTIFACT_READBACK_VERIFIED" if passed else "SILENT_FAILURE_DETECTED"
    receipt_payload = {
        "event": event,
        "artifact_name": artifact.name,
        "artifact_sha256": digest,
        "required_json_keys": list(required_json_keys),
        "acceptance_contract_sha256": (
            _sha256(dict(acceptance_contract)) if acceptance_contract is not None else ""
        ),
        "reason": reason,
    }
    return ArtifactReadback(
        passed=passed,
        event=event,
        material_status="PASS" if passed else "NOT_PASS",
        artifact_sha256=digest,
        readback_receipt_sha256=_sha256(receipt_payload),
        reason=reason,
    )


@dataclass(frozen=True)
class ProvenanceRecord:
    record_id: str
    subject_role: str
    origin_model: str
    origin_provider: str
    origin_attempt_id: str
    claim_type: str
    statement: str
    provenance_level: str
    evidence_refs: tuple[str, ...]
    canonicality: str
    quarantine_state: str
    quarantine_reason: str
    supersedes_record_id: str | None = None
    independent_receipt: str | None = None
    controlling_acceptance: str | None = None

    @classmethod
    def create(
        cls,
        *,
        subject_role: str,
        origin_model: str,
        origin_provider: str,
        origin_attempt_id: str,
        claim_type: str,
        statement: str,
        evidence_refs: tuple[str, ...] = (),
        provenance_level: str = "P0_MODEL_CLAIM",
    ) -> "ProvenanceRecord":
        level = str(provenance_level).upper()
        if level not in {
            "P0_MODEL_CLAIM",
            "P1_TOOL_BACKED",
            "P2_INDEPENDENTLY_VERIFIED",
            "P3_CANONICAL",
        }:
            level = "P0_MODEL_CLAIM"
        # Creating a P3 object directly would bypass controlling acceptance.
        if level == "P3_CANONICAL":
            level = "P2_INDEPENDENTLY_VERIFIED"
        payload = {
            "subject_role": subject_role,
            "origin_model": origin_model,
            "origin_provider": origin_provider,
            "origin_attempt_id": origin_attempt_id,
            "claim_type": claim_type,
            "statement": statement,
        }
        return cls(
            record_id=f"prov-{_sha256(payload)[:24]}",
            subject_role=subject_role,
            origin_model=origin_model,
            origin_provider=origin_provider,
            origin_attempt_id=origin_attempt_id,
            claim_type=claim_type,
            statement=statement,
            provenance_level=level,
            evidence_refs=tuple(evidence_refs),
            canonicality="CANDIDATE",
            quarantine_state="ACTIVE",
            quarantine_reason="",
        )

    def with_tool_evidence(
        self,
        evidence_refs: tuple[str, ...],
        *,
        ledger: "RuntimeIntegrityLedger | None" = None,
    ) -> "ProvenanceRecord":
        evidence_ledger = ledger or RuntimeIntegrityLedger()
        trusted = tuple(
            ref
            for ref in evidence_refs
            if evidence_ledger.is_p1_evidence(ref)
        )
        refs = tuple(dict.fromkeys((*self.evidence_refs, *trusted)))
        if not trusted:
            return self
        return replace(
            self,
            provenance_level="P1_TOOL_BACKED",
            evidence_refs=refs,
            canonicality="WORKING_STATE",
        )

    def promote(
        self,
        target_level: str,
        *,
        independent_receipt: str | None = None,
        controlling_acceptance: str | None = None,
        ledger: "RuntimeIntegrityLedger | None" = None,
    ) -> "ProvenanceRecord | None":
        if self.quarantine_state == "QUARANTINED":
            return None
        target = str(target_level).upper()
        evidence_ledger = ledger or RuntimeIntegrityLedger()
        if target == "P2_INDEPENDENTLY_VERIFIED":
            if (
                self.provenance_level != "P1_TOOL_BACKED"
                or not independent_receipt
                or not evidence_ledger.independent_verification_covers(
                    independent_receipt, self.evidence_refs
                )
            ):
                return None
            return replace(
                self,
                provenance_level=target,
                canonicality="CANONICAL_CANDIDATE",
                independent_receipt=independent_receipt,
            )
        if target == "P3_CANONICAL":
            if (
                self.provenance_level != "P2_INDEPENDENTLY_VERIFIED"
                or not controlling_acceptance
                or not evidence_ledger.controlling_acceptance_covers(
                    controlling_acceptance, self.independent_receipt or ""
                )
            ):
                return None
            return replace(
                self,
                provenance_level=target,
                canonicality="CANONICAL",
                controlling_acceptance=controlling_acceptance,
            )
        return None

    def quarantine(self, reason: str) -> "ProvenanceRecord":
        return replace(
            self,
            canonicality="PROMOTION_BLOCKED",
            quarantine_state="QUARANTINED",
            quarantine_reason=str(reason),
        )

    def supersede(self, previous: "ProvenanceRecord") -> "ProvenanceRecord":
        return replace(self, supersedes_record_id=previous.record_id)


@dataclass(frozen=True)
class ClaimContradiction:
    event: str
    contradicted: bool
    quarantined_record: ProvenanceRecord
    promotion_allowed: bool
    raw_evidence_preserved: bool
    attempt_state: str
    contradiction_receipt_sha256: str
    mismatches: tuple[str, ...]


def detect_claim_contradiction(
    record: ProvenanceRecord,
    *,
    claimed_minimums: dict[str, int],
    telemetry: dict[str, Any],
) -> ClaimContradiction:
    """Compare consequential count claims with deterministic telemetry."""
    mismatches: list[str] = []
    for key, minimum in sorted(claimed_minimums.items()):
        try:
            actual = int(telemetry.get(key, 0))
            required = int(minimum)
        except (TypeError, ValueError):
            actual, required = 0, 1
        if actual < required:
            mismatches.append(f"{key}:{actual}<{required}")
    contradicted = bool(mismatches)
    quarantined = (
        record.quarantine("CLAIM_EVIDENCE_CONTRADICTION")
        if contradicted
        else record
    )
    receipt = {
        "record_id": record.record_id,
        "claimed_minimums": claimed_minimums,
        "telemetry_sha256": _sha256(telemetry),
        "mismatches": mismatches,
    }
    return ClaimContradiction(
        event=(
            "CLAIM_EVIDENCE_CONTRADICTION"
            if contradicted
            else "CLAIM_EVIDENCE_CONSISTENT"
        ),
        contradicted=contradicted,
        quarantined_record=quarantined,
        promotion_allowed=not contradicted,
        raw_evidence_preserved=True,
        attempt_state="SUSPECT" if contradicted else "ACTIVE",
        contradiction_receipt_sha256=_sha256(receipt),
        mismatches=tuple(mismatches),
    )


@dataclass(frozen=True)
class CapabilityAdmission:
    allowed: bool
    event: str
    required_tier: str
    effective_authority: str
    action: str
    tool_name: str
    tool_wall_enforced: bool
    model_self_assessment_considered: bool = False


def admit_capability(
    *,
    required_tier: str,
    role_ceiling: str,
    task_ceiling: str,
    model_capability: str,
    evidence_ceiling: str,
    policy_ceiling: str,
    tool_name: str,
    model_claimed_capable: bool | None = None,
    mismatch_action: str = "ESCALATE",
) -> CapabilityAdmission:
    """Apply the minimum-effective-ceiling rule without model self-assessment."""
    operands = (
        _tier(role_ceiling),
        _tier(task_ceiling),
        _tier(model_capability),
        _tier(evidence_ceiling),
        _tier(policy_ceiling),
    )
    effective = min(operands)
    required = _tier(required_tier)
    allowed = effective >= required
    action = "ALLOW" if allowed else str(mismatch_action or "ESCALATE").upper()
    if action not in {"ALLOW", "NARROW", "QUEUE", "ESCALATE", "PAUSE"}:
        action = "ESCALATE"
    return CapabilityAdmission(
        allowed=allowed,
        event="CAPABILITY_ADMITTED" if allowed else "CAPABILITY_MISMATCH",
        required_tier=required.name,
        effective_authority=effective.name,
        action=action,
        tool_name=tool_name,
        tool_wall_enforced=not allowed,
        model_self_assessment_considered=False,
    )


@dataclass(frozen=True)
class RuntimeEnvelope:
    role_id: str
    host: str
    provider: str
    exact_model_id: str
    model_route: str
    policy_generation: str
    session_id: str
    attempt_id: str
    capability_tier: str
    role_ceiling: str
    task_ceiling: str
    evidence_ceiling: str
    policy_ceiling: str
    effective_authority: str
    capability_certificate_id: str
    context_id: str
    envelope_sha256: str

    @classmethod
    def create(
        cls,
        *,
        role_id: str,
        host: str,
        provider: str,
        exact_model_id: str,
        model_route: str,
        policy_generation: str,
        session_id: str,
        attempt_id: str,
        capability_tier: str,
        role_ceiling: str,
        task_ceiling: str,
        evidence_ceiling: str,
        policy_ceiling: str,
    ) -> "RuntimeEnvelope":
        normalized_capability = _tier(capability_tier).name
        normalized_role = _tier(role_ceiling).name
        normalized_task = _tier(task_ceiling).name
        normalized_evidence = _tier(evidence_ceiling).name
        normalized_policy = _tier(policy_ceiling).name
        tiers = tuple(
            _tier(value)
            for value in (
                normalized_role,
                normalized_task,
                normalized_capability,
                normalized_evidence,
                normalized_policy,
            )
        )
        effective = min(tiers).name
        certificate_payload = {
            "model": exact_model_id,
            "provider": provider,
            "tier": normalized_capability,
            "policy_generation": policy_generation,
        }
        certificate_id = f"cap-{_sha256(certificate_payload)[:24]}"
        context_payload = {
            "role_id": role_id,
            "provider": provider,
            "model": exact_model_id,
            "route": model_route,
            "session_id": session_id,
            "attempt_id": attempt_id,
            "certificate": certificate_id,
        }
        context_id = f"ctx-{_sha256(context_payload)[:24]}"
        envelope = cls(
            role_id=role_id,
            host=host,
            provider=provider,
            exact_model_id=exact_model_id,
            model_route=model_route,
            policy_generation=policy_generation,
            session_id=session_id,
            attempt_id=attempt_id,
            capability_tier=normalized_capability,
            role_ceiling=normalized_role,
            task_ceiling=normalized_task,
            evidence_ceiling=normalized_evidence,
            policy_ceiling=normalized_policy,
            effective_authority=effective,
            capability_certificate_id=certificate_id,
            context_id=context_id,
            envelope_sha256="",
        )
        return replace(envelope, envelope_sha256=envelope.hash())

    @property
    def envelope_hash(self) -> str:
        """Compatibility name for the canonical envelope digest."""

        return self.envelope_sha256

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def _hash_payload(self) -> dict[str, Any]:
        payload = self.as_dict()
        payload.pop("envelope_sha256", None)
        return payload

    def hash(self) -> str:
        return _sha256(self._hash_payload())

    def verify(self) -> bool:
        return bool(self.envelope_sha256) and self.envelope_sha256 == self.hash()

    def canonical_json(self) -> str:
        return _canonical(asdict(self))


@dataclass(frozen=True)
class ToolReceipt:
    receipt_sha256: str
    previous_receipt_sha256: str
    runtime_envelope_sha256: str
    session_id: str
    attempt_id: str
    tool_name: str
    tool_call_id: str
    args_sha256: str
    result_sha256: str
    status: str


def create_tool_receipt(
    *,
    envelope: RuntimeEnvelope,
    tool_name: str,
    tool_call_id: str,
    args: Any,
    result: Any,
    status: str,
    previous_receipt_sha256: str = "",
) -> ToolReceipt:
    """Create one deterministic receipt bound to the canonical envelope."""

    if not envelope.verify():
        raise ValueError("runtime envelope digest is invalid")
    args_digest = _sha256(args)
    result_digest = _sha256(result)
    payload = {
        "previous_receipt_sha256": str(previous_receipt_sha256 or ""),
        "runtime_envelope_sha256": envelope.envelope_sha256,
        "session_id": envelope.session_id,
        "attempt_id": envelope.attempt_id,
        "tool_name": str(tool_name),
        "tool_call_id": str(tool_call_id),
        "args_sha256": args_digest,
        "result_sha256": result_digest,
        "status": str(status),
    }
    return ToolReceipt(
        receipt_sha256=_sha256(payload),
        previous_receipt_sha256=payload["previous_receipt_sha256"],
        runtime_envelope_sha256=envelope.envelope_sha256,
        session_id=envelope.session_id,
        attempt_id=envelope.attempt_id,
        tool_name=payload["tool_name"],
        tool_call_id=payload["tool_call_id"],
        args_sha256=args_digest,
        result_sha256=result_digest,
        status=payload["status"],
    )


class RuntimeIntegrityLedger:
    """Integrity tables inside Hermes' existing verification evidence ledger."""

    @staticmethod
    def _ensure_schema(conn: Any) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_integrity_envelopes (
                envelope_sha256 TEXT PRIMARY KEY,
                role_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                envelope_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_integrity_occupants (
                role_id TEXT PRIMARY KEY,
                envelope_sha256 TEXT NOT NULL,
                envelope_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_integrity_tool_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_sha256 TEXT NOT NULL UNIQUE,
                previous_receipt_sha256 TEXT NOT NULL,
                runtime_envelope_sha256 TEXT NOT NULL,
                session_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                args_sha256 TEXT NOT NULL,
                result_sha256 TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_runtime_integrity_receipts_session
            ON runtime_integrity_tool_receipts(session_id, id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_integrity_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transition_sha256 TEXT NOT NULL UNIQUE,
                role_id TEXT NOT NULL,
                previous_envelope_sha256 TEXT NOT NULL,
                current_envelope_sha256 TEXT NOT NULL,
                previous_session_id TEXT NOT NULL,
                current_session_id TEXT NOT NULL,
                predecessor_segment_sha256 TEXT NOT NULL,
                predecessor_message_count INTEGER NOT NULL,
                boundary_anchor_sha256 TEXT NOT NULL,
                boundary_anchor_json TEXT NOT NULL,
                handoff_capsule_json TEXT NOT NULL,
                new_context_json TEXT NOT NULL,
                attribution_json TEXT NOT NULL,
                owner_notice TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_runtime_integrity_transitions_role
            ON runtime_integrity_transitions(role_id, id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_integrity_active_contexts (
                role_id TEXT PRIMARY KEY,
                transition_sha256 TEXT NOT NULL,
                current_envelope_sha256 TEXT NOT NULL,
                boundary_anchor_sha256 TEXT NOT NULL,
                boundary_anchor_json TEXT NOT NULL,
                transition_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_integrity_artifact_readbacks (
                readback_receipt_sha256 TEXT PRIMARY KEY,
                artifact_sha256 TEXT NOT NULL,
                result_receipt_sha256 TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                acceptance_contract_json TEXT NOT NULL,
                passed INTEGER NOT NULL,
                event TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_runtime_integrity_artifact_digest
            ON runtime_integrity_artifact_readbacks(artifact_sha256, passed)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_integrity_evidence_records (
                record_sha256 TEXT PRIMARY KEY,
                evidence_kind TEXT NOT NULL,
                subject_refs_json TEXT NOT NULL,
                envelope_sha256 TEXT NOT NULL,
                authority TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_integrity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role_id TEXT NOT NULL,
                event TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    def record_envelope(self, envelope: RuntimeEnvelope) -> None:
        from agent.verification_evidence import _transaction

        encoded = envelope.canonical_json()
        with _transaction() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO runtime_integrity_envelopes(
                    envelope_sha256, role_id, session_id, attempt_id, envelope_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    envelope.envelope_sha256,
                    envelope.role_id,
                    envelope.session_id,
                    envelope.attempt_id,
                    encoded,
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO runtime_integrity_occupants(
                    role_id, envelope_sha256, envelope_json
                ) VALUES (?, ?, ?)
                """,
                (envelope.role_id, envelope.envelope_sha256, encoded),
            )

    def current_occupant(self, role_id: str) -> RuntimeEnvelope | None:
        from agent.verification_evidence import _transaction

        with _transaction() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                "SELECT envelope_json FROM runtime_integrity_occupants WHERE role_id = ?",
                (role_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return RuntimeEnvelope(**json.loads(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def envelope_for_session(self, session_id: str) -> RuntimeEnvelope | None:
        from agent.verification_evidence import _transaction

        with _transaction() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                """
                SELECT envelope_json FROM runtime_integrity_envelopes
                WHERE session_id = ? ORDER BY rowid DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return RuntimeEnvelope(**json.loads(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def envelope_by_hash(self, envelope_sha256: str) -> RuntimeEnvelope | None:
        from agent.verification_evidence import _transaction

        digest = str(envelope_sha256 or "").removeprefix("sha256:")
        with _transaction() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                """
                SELECT envelope_json FROM runtime_integrity_envelopes
                WHERE envelope_sha256 = ? LIMIT 1
                """,
                (digest,),
            ).fetchone()
        if row is None:
            return None
        try:
            envelope = RuntimeEnvelope(**json.loads(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return envelope if envelope.verify() else None

    def record_tool_receipt(
        self,
        *,
        envelope: RuntimeEnvelope,
        tool_name: str,
        tool_call_id: str,
        args: Any,
        result: Any,
        status: str,
        bind_to_pre_call: bool = False,
    ) -> ToolReceipt:
        from agent.verification_evidence import _transaction

        with _transaction() as conn:
            self._ensure_schema(conn)
            if bind_to_pre_call:
                row = conn.execute(
                    """
                    SELECT receipt_sha256 FROM runtime_integrity_tool_receipts
                    WHERE session_id = ? AND runtime_envelope_sha256 = ?
                      AND tool_name = ? AND tool_call_id = ?
                      AND status = 'pre_tool_call'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (
                        envelope.session_id,
                        envelope.envelope_sha256,
                        str(tool_name),
                        str(tool_call_id),
                    ),
                ).fetchone()
                if row is None:
                    raise RuntimeIntegrityViolation(
                        "ACTION_NOT_PROVEN result has no trusted pre-tool receipt"
                    )
            else:
                row = conn.execute(
                    """
                    SELECT receipt_sha256 FROM runtime_integrity_tool_receipts
                    WHERE session_id = ? ORDER BY id DESC LIMIT 1
                    """,
                    (envelope.session_id,),
                ).fetchone()
            previous = str(row[0]) if row else ""
            receipt = create_tool_receipt(
                envelope=envelope,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                args=args,
                result=result,
                status=status,
                previous_receipt_sha256=previous,
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO runtime_integrity_tool_receipts(
                    receipt_sha256, previous_receipt_sha256,
                    runtime_envelope_sha256, session_id, attempt_id, tool_name,
                    tool_call_id, args_sha256, result_sha256, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.receipt_sha256,
                    receipt.previous_receipt_sha256,
                    receipt.runtime_envelope_sha256,
                    receipt.session_id,
                    receipt.attempt_id,
                    receipt.tool_name,
                    receipt.tool_call_id,
                    receipt.args_sha256,
                    receipt.result_sha256,
                    receipt.status,
                ),
            )
        return receipt

    @staticmethod
    def _receipt_from_row(row: Any) -> ToolReceipt | None:
        if row is None:
            return None
        try:
            return ToolReceipt(
                receipt_sha256=str(row["receipt_sha256"]),
                previous_receipt_sha256=str(row["previous_receipt_sha256"]),
                runtime_envelope_sha256=str(row["runtime_envelope_sha256"]),
                session_id=str(row["session_id"]),
                attempt_id=str(row["attempt_id"]),
                tool_name=str(row["tool_name"]),
                tool_call_id=str(row["tool_call_id"]),
                args_sha256=str(row["args_sha256"]),
                result_sha256=str(row["result_sha256"]),
                status=str(row["status"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def receipts_for_session(self, session_id: str) -> tuple[ToolReceipt, ...]:
        from agent.verification_evidence import _transaction

        with _transaction() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT * FROM runtime_integrity_tool_receipts
                WHERE session_id = ? ORDER BY id
                """,
                (str(session_id),),
            ).fetchall()
        return tuple(
            receipt
            for row in rows
            if (receipt := self._receipt_from_row(row)) is not None
        )

    def tool_receipt(self, receipt_sha256: str) -> ToolReceipt | None:
        from agent.verification_evidence import _transaction

        digest = str(receipt_sha256 or "").removeprefix("sha256:")
        with _transaction() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                """
                SELECT * FROM runtime_integrity_tool_receipts
                WHERE receipt_sha256 = ? LIMIT 1
                """,
                (digest,),
            ).fetchone()
        return self._receipt_from_row(row)

    def tool_result_receipt(self, receipt_sha256: str) -> ToolReceipt | None:
        receipt = self.tool_receipt(receipt_sha256)
        if receipt is None or receipt.status == "pre_tool_call":
            return None
        if receipt.status.strip().lower() not in {
            "ok",
            "success",
            "passed",
            "pass",
            "completed",
        }:
            return None
        previous = self.tool_receipt(receipt.previous_receipt_sha256)
        if (
            previous is None
            or previous.status != "pre_tool_call"
            or previous.runtime_envelope_sha256 != receipt.runtime_envelope_sha256
            or previous.session_id != receipt.session_id
            or previous.tool_name != receipt.tool_name
            or previous.tool_call_id != receipt.tool_call_id
        ):
            return None
        return receipt

    def tool_result_for_call(
        self, session_id: str, tool_call_id: str
    ) -> ToolReceipt | None:
        from agent.verification_evidence import _transaction

        if not session_id or not tool_call_id:
            return None
        with _transaction() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT * FROM runtime_integrity_tool_receipts
                WHERE session_id = ? AND tool_call_id = ?
                  AND status != 'pre_tool_call'
                ORDER BY id DESC
                """,
                (str(session_id), str(tool_call_id)),
            ).fetchall()
        for row in rows:
            candidate = self._receipt_from_row(row)
            if candidate is not None:
                trusted = self.tool_result_receipt(candidate.receipt_sha256)
                if trusted is not None:
                    return trusted
        return None

    def is_p1_evidence(self, evidence_ref: str) -> bool:
        digest = str(evidence_ref or "").removeprefix("sha256:")
        if self.tool_result_receipt(digest) is not None:
            return True
        from agent.verification_evidence import _transaction

        with _transaction() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                """
                SELECT 1 FROM runtime_integrity_artifact_readbacks
                WHERE readback_receipt_sha256 = ? AND passed = 1 LIMIT 1
                """,
                (digest,),
            ).fetchone()
        return row is not None

    def record_event(
        self,
        *,
        session_id: str,
        role_id: str,
        event: str,
        payload: Any = None,
    ) -> str:
        from agent.verification_evidence import _transaction

        payload_digest = _sha256(payload if payload is not None else {})
        with _transaction() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO runtime_integrity_events(
                    session_id, role_id, event, payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(session_id),
                    str(role_id),
                    str(event),
                    payload_digest,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return payload_digest

    def events_for_session(self, session_id: str) -> tuple[str, ...]:
        from agent.verification_evidence import _transaction

        with _transaction() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT event FROM runtime_integrity_events
                WHERE session_id = ? ORDER BY id
                """,
                (str(session_id),),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def record_artifact_readback(
        self,
        readback: ArtifactReadback,
        *,
        result_receipt_ref: str,
        path: str | Path,
        acceptance_contract: Mapping[str, Any] | None = None,
    ) -> str:
        from agent.verification_evidence import _transaction

        result_digest = str(result_receipt_ref or "").removeprefix("sha256:")
        if self.tool_result_receipt(result_digest) is None:
            raise RuntimeIntegrityViolation(
                "ACTION_NOT_PROVEN artifact readback has no trusted tool result"
            )
        contract = dict(acceptance_contract or {})
        with _transaction() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO runtime_integrity_artifact_readbacks(
                    readback_receipt_sha256, artifact_sha256,
                    result_receipt_sha256, artifact_path,
                    acceptance_contract_json, passed, event, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    readback.readback_receipt_sha256,
                    readback.artifact_sha256,
                    result_digest,
                    str(Path(path)),
                    _canonical(contract),
                    int(readback.passed),
                    readback.event,
                    readback.reason,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return readback.readback_receipt_sha256

    def artifact_digest_exists(self, artifact_sha256: str) -> bool:
        from agent.verification_evidence import _transaction

        digest = str(artifact_sha256 or "").removeprefix("sha256:")
        if not _is_sha256(digest):
            return False
        with _transaction() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                """
                SELECT 1 FROM runtime_integrity_artifact_readbacks
                WHERE artifact_sha256 = ? AND passed = 1 LIMIT 1
                """,
                (digest,),
            ).fetchone()
        return row is not None

    def artifact_readback_record(self, record_ref: str) -> dict[str, Any] | None:
        from agent.verification_evidence import _transaction

        digest = str(record_ref or "").removeprefix("sha256:")
        if not _is_sha256(digest):
            return None
        with _transaction() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                """
                SELECT * FROM runtime_integrity_artifact_readbacks
                WHERE readback_receipt_sha256 = ? AND passed = 1 LIMIT 1
                """,
                (digest,),
            ).fetchone()
        if row is None:
            return None
        return {
            "readback_receipt_sha256": str(row["readback_receipt_sha256"]),
            "artifact_sha256": str(row["artifact_sha256"]),
            "result_receipt_sha256": str(row["result_receipt_sha256"]),
            "artifact_path": str(row["artifact_path"]),
            "acceptance_contract": json.loads(row["acceptance_contract_json"]),
        }

    def _evidence_record(self, record_sha256: str) -> dict[str, Any] | None:
        from agent.verification_evidence import _transaction

        digest = str(record_sha256 or "").removeprefix("sha256:")
        with _transaction() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                """
                SELECT * FROM runtime_integrity_evidence_records
                WHERE record_sha256 = ? LIMIT 1
                """,
                (digest,),
            ).fetchone()
        if row is None:
            return None
        try:
            return {
                "record_sha256": str(row["record_sha256"]),
                "evidence_kind": str(row["evidence_kind"]),
                "subject_refs": tuple(json.loads(row["subject_refs_json"])),
                "envelope_sha256": str(row["envelope_sha256"]),
                "authority": str(row["authority"]),
                "accepted": bool(row["accepted"]),
                "payload": json.loads(row["payload_json"]),
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def record_source_revision(
        self,
        *,
        source_revision: str,
        tool_receipt_ref: str,
    ) -> str:
        """Bind a source revision to an existing trusted tool-result receipt."""
        from agent.verification_evidence import _transaction

        revision = str(source_revision or "").lower()
        tool_ref = str(tool_receipt_ref or "").removeprefix("sha256:")
        receipt = self.tool_result_receipt(tool_ref)
        if (
            len(revision) != 40
            or any(char not in "0123456789abcdef" for char in revision)
            or receipt is None
        ):
            raise RuntimeIntegrityViolation(
                "source revision requires a trusted tool-result receipt"
            )
        payload = {
            "kind": "source_revision",
            "source_revision": revision,
            "tool_receipt_sha256": tool_ref,
        }
        digest = _sha256(payload)
        with _transaction() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO runtime_integrity_evidence_records(
                    record_sha256, evidence_kind, subject_refs_json,
                    envelope_sha256, authority, accepted, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest,
                    "source_revision",
                    _canonical([tool_ref]),
                    receipt.runtime_envelope_sha256,
                    "tool_readback",
                    1,
                    _canonical(payload),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return digest

    def source_revision_exists(
        self,
        source_revision: str,
        tool_receipt_ref: str,
    ) -> bool:
        from agent.verification_evidence import _transaction

        revision = str(source_revision or "").lower()
        tool_ref = str(tool_receipt_ref or "").removeprefix("sha256:")
        if len(revision) != 40 or not _is_sha256(tool_ref):
            return False
        with _transaction() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT payload_json FROM runtime_integrity_evidence_records
                WHERE evidence_kind = 'source_revision' AND accepted = 1
                """
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row[0])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                payload.get("source_revision") == revision
                and payload.get("tool_receipt_sha256") == tool_ref
            ):
                return True
        return False

    def record_independent_verification(
        self,
        *,
        subject_refs: Iterable[str],
        verifier_envelope: RuntimeEnvelope,
        accepted: bool,
    ) -> str:
        from agent.verification_evidence import _transaction

        subjects = tuple(
            dict.fromkeys(str(ref or "").removeprefix("sha256:") for ref in subject_refs)
        )
        if not subjects or not all(self.is_p1_evidence(ref) for ref in subjects):
            raise RuntimeIntegrityViolation(
                "independent verification subjects must resolve to trusted P1 evidence"
            )
        if self.envelope_by_hash(verifier_envelope.envelope_sha256) != verifier_envelope:
            raise RuntimeIntegrityViolation("verifier envelope is not durably attested")
        subject_envelopes = {
            receipt.runtime_envelope_sha256
            for ref in subjects
            if (receipt := self.tool_result_receipt(ref)) is not None
        }
        if verifier_envelope.envelope_sha256 in subject_envelopes:
            raise RuntimeIntegrityViolation("verification is not independent")
        payload = {
            "kind": "independent_verification",
            "subject_refs": list(subjects),
            "verifier_envelope_sha256": verifier_envelope.envelope_sha256,
            "accepted": bool(accepted),
        }
        digest = _sha256(payload)
        with _transaction() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO runtime_integrity_evidence_records(
                    record_sha256, evidence_kind, subject_refs_json,
                    envelope_sha256, authority, accepted, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest,
                    "independent_verification",
                    _canonical(list(subjects)),
                    verifier_envelope.envelope_sha256,
                    "",
                    int(bool(accepted)),
                    _canonical(payload),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return digest

    def independent_verification_covers(
        self, record_ref: str, subject_refs: Iterable[str]
    ) -> bool:
        record = self._evidence_record(record_ref)
        subjects = {
            str(ref or "").removeprefix("sha256:") for ref in subject_refs
        }
        return bool(
            record
            and record["evidence_kind"] == "independent_verification"
            and record["accepted"]
            and subjects
            and subjects.issubset(set(record["subject_refs"]))
            and self.envelope_by_hash(record["envelope_sha256"]) is not None
        )

    def record_controlling_acceptance(
        self, *, subject_ref: str, authority: str, accepted: bool
    ) -> str:
        from agent.verification_evidence import _transaction

        subject = str(subject_ref or "").removeprefix("sha256:")
        record = self._evidence_record(subject)
        if (
            record is None
            or record["evidence_kind"] != "independent_verification"
            or not record["accepted"]
        ):
            raise RuntimeIntegrityViolation(
                "controlling acceptance subject must be an accepted P2 record"
            )
        normalized_authority = str(authority or "").strip().lower()
        if normalized_authority != "owner":
            raise RuntimeIntegrityViolation("controlling acceptance requires owner authority")
        payload = {
            "kind": "controlling_acceptance",
            "subject_ref": subject,
            "authority": normalized_authority,
            "accepted": bool(accepted),
        }
        digest = _sha256(payload)
        with _transaction() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO runtime_integrity_evidence_records(
                    record_sha256, evidence_kind, subject_refs_json,
                    envelope_sha256, authority, accepted, payload_json, created_at
                ) VALUES (?, ?, ?, '', ?, ?, ?, ?)
                """,
                (
                    digest,
                    "controlling_acceptance",
                    _canonical([subject]),
                    normalized_authority,
                    int(bool(accepted)),
                    _canonical(payload),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return digest

    def controlling_acceptance_covers(
        self, record_ref: str, subject_ref: str
    ) -> bool:
        record = self._evidence_record(record_ref)
        subject = str(subject_ref or "").removeprefix("sha256:")
        return bool(
            record
            and record["evidence_kind"] == "controlling_acceptance"
            and record["accepted"]
            and record["authority"] == "owner"
            and record["subject_refs"] == (subject,)
        )

    def is_controlling_acceptance(self, record_ref: str) -> bool:
        record = self._evidence_record(record_ref)
        return bool(
            record
            and record["evidence_kind"] == "controlling_acceptance"
            and record["accepted"]
            and record["authority"] == "owner"
            and len(record["subject_refs"]) == 1
        )

    def record_transition(
        self,
        transition: "OccupantTransition",
        *,
        predecessor_segment: Iterable[Mapping[str, Any]],
        boundary_anchor: Mapping[str, Any] | None,
    ) -> str:
        from agent.verification_evidence import _transaction

        predecessor = [copy.deepcopy(dict(item)) for item in predecessor_segment]
        anchor = copy.deepcopy(dict(boundary_anchor or {}))
        attribution = {
            "attributed_predecessor_conclusions": copy.deepcopy(
                transition.handoff_capsule.get(
                    "attributed_predecessor_conclusions", []
                )
            ),
            "previous_envelope_sha256": transition.previous.envelope_sha256,
        }
        new_context = {
            "context_id": transition.current.context_id,
            "role_id": transition.current.role_id,
            "provider": transition.current.provider,
            "exact_model_id": transition.current.exact_model_id,
            "model_route": transition.current.model_route,
            "session_id": transition.current.session_id,
            "attempt_id": transition.current.attempt_id,
            "runtime_envelope_sha256": transition.current.envelope_sha256,
        }
        payload = {
            "event": transition.event,
            "role_id": transition.current.role_id,
            "previous_envelope_sha256": transition.previous.envelope_sha256,
            "current_envelope_sha256": transition.current.envelope_sha256,
            "predecessor_segment_sha256": _sha256(predecessor),
            "predecessor_message_count": len(predecessor),
            "boundary_anchor_sha256": _sha256(anchor) if anchor else "",
            "handoff_capsule": transition.handoff_capsule,
            "new_context": new_context,
            "attribution": attribution,
            "owner_notice": transition.owner_notice,
        }
        digest = _sha256(payload)
        transition_json = _canonical(asdict(transition))
        with _transaction() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO runtime_integrity_transitions(
                    transition_sha256, role_id, previous_envelope_sha256,
                    current_envelope_sha256, previous_session_id,
                    current_session_id, predecessor_segment_sha256,
                    predecessor_message_count, boundary_anchor_sha256,
                    boundary_anchor_json, handoff_capsule_json, new_context_json,
                    attribution_json, owner_notice, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest,
                    transition.current.role_id,
                    transition.previous.envelope_sha256,
                    transition.current.envelope_sha256,
                    transition.previous.session_id,
                    transition.current.session_id,
                    payload["predecessor_segment_sha256"],
                    payload["predecessor_message_count"],
                    payload["boundary_anchor_sha256"],
                    _canonical(anchor),
                    _canonical(transition.handoff_capsule),
                    _canonical(new_context),
                    _canonical(attribution),
                    transition.owner_notice,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO runtime_integrity_active_contexts(
                    role_id, transition_sha256, current_envelope_sha256,
                    boundary_anchor_sha256, boundary_anchor_json, transition_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    transition.current.role_id,
                    digest,
                    transition.current.envelope_sha256,
                    payload["boundary_anchor_sha256"],
                    _canonical(anchor),
                    transition_json,
                ),
            )
        return digest

    @staticmethod
    def _transition_from_json(encoded: str) -> "OccupantTransition | None":
        try:
            payload = json.loads(encoded)
            payload["previous"] = RuntimeEnvelope(**payload["previous"])
            payload["current"] = RuntimeEnvelope(**payload["current"])
            return OccupantTransition(**payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def active_context(self, role_id: str) -> dict[str, Any] | None:
        from agent.verification_evidence import _transaction

        with _transaction() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                """
                SELECT * FROM runtime_integrity_active_contexts
                WHERE role_id = ? LIMIT 1
                """,
                (str(role_id),),
            ).fetchone()
        if row is None:
            return None
        transition = self._transition_from_json(str(row["transition_json"]))
        if transition is None:
            return None
        try:
            anchor = json.loads(row["boundary_anchor_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return {
            "transition_sha256": str(row["transition_sha256"]),
            "current_envelope_sha256": str(row["current_envelope_sha256"]),
            "boundary_anchor_sha256": str(row["boundary_anchor_sha256"]),
            "boundary_anchor": anchor,
            "transition": transition,
        }

    def latest_transition(self, role_id: str) -> dict[str, Any] | None:
        from agent.verification_evidence import _transaction

        with _transaction() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                """
                SELECT * FROM runtime_integrity_transitions
                WHERE role_id = ? ORDER BY id DESC LIMIT 1
                """,
                (str(role_id),),
            ).fetchone()
        if row is None:
            return None
        try:
            return {
                "transition_sha256": str(row["transition_sha256"]),
                "role_id": str(row["role_id"]),
                "previous_envelope_sha256": str(row["previous_envelope_sha256"]),
                "current_envelope_sha256": str(row["current_envelope_sha256"]),
                "previous_session_id": str(row["previous_session_id"]),
                "current_session_id": str(row["current_session_id"]),
                "predecessor_segment_sha256": str(
                    row["predecessor_segment_sha256"]
                ),
                "predecessor_message_count": int(row["predecessor_message_count"]),
                "boundary_anchor_sha256": str(row["boundary_anchor_sha256"]),
                "handoff_capsule": json.loads(row["handoff_capsule_json"]),
                "new_context": json.loads(row["new_context_json"]),
                "attribution": json.loads(row["attribution_json"]),
                "owner_notice": str(row["owner_notice"]),
                "created_at": str(row["created_at"]),
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            return None


@dataclass(frozen=True)
class VerifiedHandoffState:
    verified_role_state: tuple[str, ...] = ()
    owner_decisions: tuple[str, ...] = ()
    canonical_sources: tuple[str, ...] = ()
    receipt_refs: tuple[str, ...] = ()
    predecessor_conclusions: tuple[str, ...] = ()
    pending_tasks: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class OccupantTransition:
    event: str
    previous: RuntimeEnvelope
    current: RuntimeEnvelope
    fresh_context: bool
    capability_clamp_active: bool
    handoff_capsule: dict[str, Any]
    owner_notice: str


def _attributed_conclusions(
    previous: RuntimeEnvelope, statements: Iterable[str]
) -> list[dict[str, str]]:
    return [
        {
            "statement": statement,
            "origin_envelope_sha256": previous.envelope_sha256,
            "origin_provider": previous.provider,
            "origin_model": previous.exact_model_id,
            "origin_attempt_id": previous.attempt_id,
        }
        for statement in statements
    ]


def detect_occupant_change(
    previous: RuntimeEnvelope,
    current: RuntimeEnvelope,
    verified: VerifiedHandoffState | None = None,
) -> OccupantTransition:
    """Create a deterministic continuity boundary for any provider/model swap."""
    if (
        previous.provider == current.provider
        and previous.exact_model_id == current.exact_model_id
    ):
        raise ValueError("occupant identity did not change")
    verified = verified or VerifiedHandoffState()
    clamp = _tier(current.effective_authority) < _tier(previous.effective_authority)
    capsule = {
        "schema_version": 1,
        "event": "MODEL_OCCUPANT_CHANGE_DETECTED",
        "previous_context_closed": True,
        "fresh_context_id": current.context_id,
        "raw_predecessor_chat_included": False,
        "predecessor_authorship_transferred": False,
        "verified_role_state": list(verified.verified_role_state),
        "owner_decisions": list(verified.owner_decisions),
        "canonical_sources": list(verified.canonical_sources),
        "tool_execution_receipt_refs": list(verified.receipt_refs),
        "attributed_predecessor_conclusions": _attributed_conclusions(
            previous, verified.predecessor_conclusions
        ),
        "pending_tasks": list(verified.pending_tasks),
        "unresolved_questions": list(verified.unresolved_questions),
        "current_capability_tool_envelope": {
            "runtime_envelope_sha256": current.envelope_sha256,
            "capability_certificate_id": current.capability_certificate_id,
            "effective_authority": current.effective_authority,
        },
    }
    notice = (
        "MODEL_OCCUPANT_CHANGE_DETECTED "
        f"role={current.role_id} previous={previous.provider}/{previous.exact_model_id} "
        f"current={current.provider}/{current.exact_model_id} reason=runtime_readback_change "
        f"certificate={current.capability_certificate_id} "
        f"capability_clamp_active={str(clamp).lower()}"
    )
    return OccupantTransition(
        event="MODEL_OCCUPANT_CHANGE_DETECTED",
        previous=previous,
        current=current,
        fresh_context=True,
        capability_clamp_active=clamp,
        handoff_capsule=capsule,
        owner_notice=notice,
    )


def _handoff_content(content: Any, capsule: Mapping[str, Any]) -> Any:
    marker = "[RUNTIME INTEGRITY HANDOFF]\n" + _canonical(capsule)
    if isinstance(content, str):
        if marker in content:
            return content
        return (content.rstrip() + "\n\n" + marker).strip()
    if isinstance(content, list):
        if any(
            isinstance(item, Mapping)
            and marker in str(item.get("text") or "")
            for item in content
        ):
            return copy.deepcopy(content)
        return list(content) + [{"type": "text", "text": marker}]
    return marker


_PROVIDER_CONTEXT_KEYS = (
    "previous_response_id",
    "conversation_id",
    "thread_id",
    "conversation",
    "thread",
)


def _clear_provider_context_ids(request: dict[str, Any]) -> None:
    for key in _PROVIDER_CONTEXT_KEYS:
        request.pop(key, None)


def filter_outgoing_request(
    request: Mapping[str, Any],
    transition: OccupantTransition,
    *,
    current_user_message: str | None = None,
) -> dict[str, Any]:
    """Apply an occupant boundary to one outbound payload without mutating history."""

    if not isinstance(request, Mapping):
        raise ValueError("outgoing request must be a mapping")
    if not isinstance(transition, OccupantTransition) or not transition.fresh_context:
        return copy.deepcopy(dict(request))

    filtered = copy.deepcopy(dict(request))
    _clear_provider_context_ids(filtered)
    key = "messages" if isinstance(filtered.get("messages"), list) else "input"
    items = filtered.get(key)
    if isinstance(items, list):
        preserved = [
            copy.deepcopy(item)
            for item in items
            if isinstance(item, dict) and item.get("role") in {"system", "developer"}
        ]
        current = next(
            (
                copy.deepcopy(item)
                for item in reversed(items)
                if isinstance(item, dict) and item.get("role") == "user"
            ),
            None,
        )
        if current is None and current_user_message is not None:
            current = {"role": "user", "content": current_user_message}
        if current is not None:
            current["content"] = _handoff_content(
                current.get("content"), transition.handoff_capsule
            )
            preserved.append(current)
        filtered[key] = preserved
    elif isinstance(items, str):
        filtered[key] = _handoff_content(items, transition.handoff_capsule)
    elif current_user_message is not None:
        filtered["messages"] = [
            {
                "role": "user",
                "content": _handoff_content(
                    current_user_message, transition.handoff_capsule
                ),
            }
        ]
    return filtered


def _boundary_parts(
    request: Mapping[str, Any], current_user_message: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    items = request.get("messages")
    if not isinstance(items, list):
        items = request.get("input")
    if not isinstance(items, list):
        anchor = (
            {"role": "user", "content": current_user_message}
            if current_user_message is not None
            else None
        )
        return [], anchor
    anchor_index = next(
        (
            index
            for index in range(len(items) - 1, -1, -1)
            if isinstance(items[index], Mapping) and items[index].get("role") == "user"
        ),
        None,
    )
    if anchor_index is None:
        anchor = (
            {"role": "user", "content": current_user_message}
            if current_user_message is not None
            else None
        )
        return [copy.deepcopy(dict(item)) for item in items if isinstance(item, Mapping)], anchor
    predecessor = [
        copy.deepcopy(dict(item))
        for item in items[:anchor_index]
        if isinstance(item, Mapping) and item.get("role") not in {"system", "developer"}
    ]
    return predecessor, copy.deepcopy(dict(items[anchor_index]))


def _filter_active_context_request(
    request: Mapping[str, Any],
    *,
    transition: OccupantTransition,
    boundary_anchor: Mapping[str, Any],
    boundary_anchor_sha256: str,
    current_user_message: str | None = None,
) -> dict[str, Any]:
    filtered = copy.deepcopy(dict(request))
    _clear_provider_context_ids(filtered)
    key = "messages" if isinstance(filtered.get("messages"), list) else "input"
    items = filtered.get(key)
    if not isinstance(items, list):
        return filter_outgoing_request(
            filtered, transition, current_user_message=current_user_message
        )

    preserved = [
        copy.deepcopy(item)
        for item in items
        if isinstance(item, dict) and item.get("role") in {"system", "developer"}
    ]
    anchor_index = next(
        (
            index
            for index, item in enumerate(items)
            if isinstance(item, Mapping)
            and (
                _sha256(dict(item)) == boundary_anchor_sha256
                or dict(item) == dict(boundary_anchor)
            )
        ),
        None,
    )
    if anchor_index is None:
        anchor_index = next(
            (
                index
                for index in range(len(items) - 1, -1, -1)
                if isinstance(items[index], Mapping)
                and items[index].get("role") == "user"
            ),
            None,
        )
    suffix: list[dict[str, Any]] = []
    if anchor_index is not None:
        suffix = [
            copy.deepcopy(dict(item))
            for item in items[anchor_index:]
            if isinstance(item, Mapping)
            and item.get("role") not in {"system", "developer"}
        ]
    elif current_user_message is not None:
        suffix = [{"role": "user", "content": current_user_message}]
    if suffix:
        suffix[0]["content"] = _handoff_content(
            suffix[0].get("content"), transition.handoff_capsule
        )
    filtered[key] = [*preserved, *suffix]
    return filtered


def _same_runtime_occupant(left: RuntimeEnvelope, right: RuntimeEnvelope) -> bool:
    return (
        left.role_id,
        left.host,
        left.provider,
        left.exact_model_id,
        left.model_route,
        left.policy_generation,
        left.capability_certificate_id,
    ) == (
        right.role_id,
        right.host,
        right.provider,
        right.exact_model_id,
        right.model_route,
        right.policy_generation,
        right.capability_certificate_id,
    )


def bind_runtime_envelope(
    envelope: RuntimeEnvelope,
    *,
    request: Mapping[str, Any],
    current_user_message: str | None = None,
    verified_state: VerifiedHandoffState | None = None,
    ledger: RuntimeIntegrityLedger | None = None,
) -> dict[str, Any]:
    """Durably bind one trusted envelope and enforce any active context boundary."""

    if not envelope.verify():
        raise RuntimeIntegrityViolation("runtime envelope digest is invalid")
    if not isinstance(request, Mapping):
        raise RuntimeIntegrityViolation("outgoing request must be a mapping")
    evidence_ledger = ledger or RuntimeIntegrityLedger()
    previous = evidence_ledger.current_occupant(envelope.role_id)
    transition: OccupantTransition | None = None
    transition_sha256 = ""
    changed = previous is not None and not _same_runtime_occupant(previous, envelope)
    if changed and previous is not None:
        transition = detect_occupant_change(
            previous, envelope, verified_state or VerifiedHandoffState()
        )
        predecessor, anchor = _boundary_parts(request, current_user_message)
        if anchor is None:
            raise RuntimeIntegrityViolation(
                "RUNTIME_IDENTITY_ATTESTATION_FAILED no current request anchor"
            )
        evidence_ledger.record_envelope(envelope)
        transition_sha256 = evidence_ledger.record_transition(
            transition,
            predecessor_segment=predecessor,
            boundary_anchor=anchor,
        )
        filtered = filter_outgoing_request(
            request,
            transition,
            current_user_message=current_user_message,
        )
        evidence_ledger.record_event(
            session_id=envelope.session_id,
            role_id=envelope.role_id,
            event=transition.event,
            payload={"transition_sha256": transition_sha256},
        )
    else:
        evidence_ledger.record_envelope(envelope)
        active = evidence_ledger.active_context(envelope.role_id)
        if (
            active is not None
            and _same_runtime_occupant(active["transition"].current, envelope)
        ):
            transition = active["transition"]
            transition_sha256 = str(active["transition_sha256"])
            filtered = _filter_active_context_request(
                request,
                transition=transition,
                boundary_anchor=active["boundary_anchor"],
                boundary_anchor_sha256=str(active["boundary_anchor_sha256"]),
                current_user_message=current_user_message,
            )
        else:
            filtered = copy.deepcopy(dict(request))
    evidence_ledger.record_event(
        session_id=envelope.session_id,
        role_id=envelope.role_id,
        event="RUNTIME_IDENTITY_BOUND",
        payload={"envelope_sha256": envelope.envelope_sha256},
    )
    return {
        "request": filtered,
        "changed": changed,
        "event": transition.event if changed and transition else "RUNTIME_IDENTITY_BOUND",
        "owner_notice": transition.owner_notice if changed and transition else "",
        "handoff_capsule": (
            copy.deepcopy(transition.handoff_capsule) if changed and transition else {}
        ),
        "predecessor_segment_frozen": bool(changed),
        "capability_clamp_active": (
            transition.capability_clamp_active if changed and transition else False
        ),
        "transition_sha256": transition_sha256,
        "envelope": envelope.as_dict(),
        "envelope_hash": envelope.envelope_sha256,
        "blocked": False,
    }


class RuntimeIntegrityViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class TurnIntegrityBoundary:
    enabled: bool
    occupant_changed: bool
    conversation_history: list[dict[str, Any]] | None
    injected_context: str
    envelope: RuntimeEnvelope | None
    transition: OccupantTransition | None = None


def _integrity_config() -> dict[str, Any]:
    from agent.windows_runtime_identity import _read_profile_config

    config = _read_profile_config()
    value = config.get("runtime_integrity") if isinstance(config, dict) else None
    return dict(value) if isinstance(value, dict) else {}


def runtime_integrity_enabled() -> bool:
    return bool(_integrity_config().get("enabled", False))


def _envelope_from_agent(agent: Any, attempt_id: str) -> RuntimeEnvelope:
    from agent.windows_runtime_identity import WindowsRuntimeIdentityAdapter

    readback = WindowsRuntimeIdentityAdapter().read_agent(agent)
    if not readback.attested:
        raise RuntimeIntegrityViolation(
            "RUNTIME_IDENTITY_ATTESTATION_FAILED provider/model/route readback mismatch"
        )
    config = _integrity_config()
    return RuntimeEnvelope.create(
        role_id=readback.role_id,
        host=readback.host,
        provider=readback.provider,
        exact_model_id=readback.exact_model_id,
        model_route=readback.model_route,
        policy_generation=readback.policy_generation,
        session_id=str(getattr(agent, "session_id", "") or ""),
        attempt_id=str(attempt_id),
        capability_tier=readback.capability_tier,
        role_ceiling=str(config.get("role_ceiling") or "C1_ROUTINE_ASSISTANT"),
        task_ceiling=str(config.get("task_ceiling") or "C1_ROUTINE_ASSISTANT"),
        evidence_ceiling=str(config.get("evidence_ceiling") or "C1_ROUTINE_ASSISTANT"),
        policy_ceiling=str(config.get("policy_ceiling") or "C1_ROUTINE_ASSISTANT"),
    )


def prepare_agent_turn(
    agent: Any,
    *,
    conversation_history: list[dict[str, Any]] | None,
    attempt_id: str,
    verified_state: VerifiedHandoffState | None = None,
) -> TurnIntegrityBoundary:
    """Establish the trusted envelope and filter history on occupant change."""
    if not runtime_integrity_enabled():
        return TurnIntegrityBoundary(
            enabled=False,
            occupant_changed=False,
            conversation_history=conversation_history,
            injected_context="",
            envelope=None,
        )
    current = _envelope_from_agent(agent, attempt_id)
    ledger = RuntimeIntegrityLedger()
    previous = getattr(agent, "_runtime_integrity_envelope", None)
    if not isinstance(previous, RuntimeEnvelope):
        previous = ledger.current_occupant(current.role_id)
    changed = bool(
        previous
        and (
            previous.provider != current.provider
            or previous.exact_model_id != current.exact_model_id
        )
    )
    transition = None
    injected = ""
    effective_history = conversation_history
    if changed and previous is not None:
        transition = detect_occupant_change(
            previous, current, verified_state or VerifiedHandoffState()
        )
        effective_history = None
        injected = _canonical(transition.handoff_capsule)
        emitter = getattr(agent, "_emit_status", None)
        if callable(emitter):
            emitter(transition.owner_notice)
    ledger.record_envelope(current)
    agent._runtime_integrity_envelope = current
    agent._runtime_integrity_injected_context = injected
    return TurnIntegrityBoundary(
        enabled=True,
        occupant_changed=changed,
        conversation_history=effective_history,
        injected_context=injected,
        envelope=current,
        transition=transition,
    )


def consume_integrity_turn_context(agent: Any) -> str:
    context = str(getattr(agent, "_runtime_integrity_injected_context", "") or "")
    agent._runtime_integrity_injected_context = ""
    return context


def apply_midturn_occupant_boundary(
    agent: Any,
    api_messages: list[dict[str, Any]],
    *,
    attempt_id: str,
    verified_state: VerifiedHandoffState | None = None,
) -> OccupantTransition | None:
    """Replace in-flight predecessor history after a fallback changes occupant."""
    if not runtime_integrity_enabled():
        return None
    boundary = prepare_agent_turn(
        agent,
        conversation_history=list(api_messages),
        attempt_id=attempt_id,
        verified_state=verified_state,
    )
    if not boundary.occupant_changed or boundary.transition is None:
        return None
    filtered = filter_outgoing_request(
        {"messages": api_messages}, boundary.transition
    )
    api_messages[:] = filtered["messages"]
    return boundary.transition


def enforce_pre_api_runtime(agent: Any, *, api_request_id: str) -> RuntimeEnvelope | None:
    """Fail closed if live provider/model/endpoint drifted before transport."""
    if not runtime_integrity_enabled():
        return None
    current = _envelope_from_agent(agent, api_request_id)
    bound = getattr(agent, "_runtime_integrity_envelope", None)
    if not isinstance(bound, RuntimeEnvelope):
        bound = RuntimeIntegrityLedger().envelope_for_session(current.session_id)
    if bound is None:
        raise RuntimeIntegrityViolation("RUNTIME_IDENTITY_ATTESTATION_FAILED no bound envelope")
    if (
        bound.role_id != current.role_id
        or bound.provider != current.provider
        or bound.exact_model_id != current.exact_model_id
        or bound.model_route != current.model_route
        or bound.policy_generation != current.policy_generation
        or bound.capability_certificate_id != current.capability_certificate_id
    ):
        raise RuntimeIntegrityViolation(
            "RUNTIME_IDENTITY_ATTESTATION_FAILED live runtime differs from bound occupant"
        )
    RuntimeIntegrityLedger().record_envelope(current)
    agent._runtime_integrity_envelope = current
    return current


def pre_tool_call_directive(
    *, tool_name: str, session_id: str, tool_call_id: str = ""
) -> dict[str, str] | None:
    """Return a fail-closed tool-wall directive for the current envelope."""
    config = _integrity_config()
    if not bool(config.get("enabled", False)):
        return None
    requirements = config.get("tool_requirements")
    requirements = requirements if isinstance(requirements, dict) else {}
    required = requirements.get(tool_name)
    if not required:
        return None
    envelope = RuntimeIntegrityLedger().envelope_for_session(session_id)
    if envelope is None:
        return {
            "action": "block",
            "message": (
                f"CAPABILITY_MISMATCH tool={tool_name} required={required} "
                "effective=C0_DETERMINISTIC_ONLY action=PAUSE"
            ),
        }
    admission = admit_capability(
        required_tier=str(required),
        role_ceiling=envelope.role_ceiling,
        task_ceiling=envelope.task_ceiling,
        model_capability=envelope.capability_tier,
        evidence_ceiling=envelope.evidence_ceiling,
        policy_ceiling=envelope.policy_ceiling,
        tool_name=tool_name,
        mismatch_action=str(config.get("mismatch_action") or "ESCALATE"),
    )
    if admission.allowed:
        return None
    return {
        "action": "block",
        "message": (
            f"CAPABILITY_MISMATCH tool={tool_name} required={admission.required_tier} "
            f"effective={admission.effective_authority} action={admission.action}"
        ),
    }


def handles_hook(hook_name: str) -> bool:
    return runtime_integrity_enabled() and hook_name in {"post_tool_call"}


def observe_lifecycle(hook_name: str, **kwargs: Any) -> None:
    """Capture hash-only tool receipts through Hermes' existing post-tool seam."""
    if hook_name != "post_tool_call" or not runtime_integrity_enabled():
        return
    session_id = str(kwargs.get("session_id") or "")
    envelope = RuntimeIntegrityLedger().envelope_for_session(session_id)
    if envelope is None:
        return
    RuntimeIntegrityLedger().record_tool_receipt(
        envelope=envelope,
        tool_name=str(kwargs.get("tool_name") or ""),
        tool_call_id=str(kwargs.get("tool_call_id") or ""),
        args=kwargs.get("args") if isinstance(kwargs.get("args"), dict) else {},
        result=kwargs.get("result"),
        status=str(kwargs.get("status") or "unknown"),
    )
