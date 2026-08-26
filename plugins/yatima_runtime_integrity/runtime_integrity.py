"""Thin opt-in hook adapter over Hermes' canonical runtime-integrity policy.

All capability, envelope, provenance, evidence, contradiction, receipt, and
Windows identity decisions live in :mod:`agent.runtime_integrity` and
:mod:`agent.windows_runtime_identity`.  This module only adapts those decisions
to the general-plugin middleware and lifecycle hook surfaces.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import yaml

from agent.runtime_integrity import (
    ArtifactReadback,
    CapabilityAdmission,
    CapabilityTier,
    ClaimContradiction,
    DebateReceiptValidation,
    ExperimentReceiptValidation,
    FAIL_TERMINAL,
    OccupantTransition,
    PASS_TERMINAL,
    POLICY_GENERATION,
    ProvenanceRecord,
    RuntimeEnvelope,
    RuntimeIntegrityLedger,
    RuntimeIntegrityViolation,
    ToolReceipt,
    VerifiedHandoffState,
    admit_capability,
    bind_runtime_envelope,
    create_tool_receipt,
    detect_claim_contradiction,
    detect_occupant_change,
    filter_outgoing_request,
    readback_artifact,
    validate_debate_receipt,
    validate_experiment_receipt,
    validate_policy,
)
from agent.windows_runtime_identity import (
    RuntimeReadbackError,
    WindowsRuntimeIdentityAdapter,
    WindowsRuntimeReadback,
)


POLICY_FILENAME = "YATIMA_FLEET_RUNTIME_INTEGRITY_POLICY_V1.yaml"
AUTHORITATIVE_YATIMA_COMMIT = "8d78fa67d0aaf57e7f2d7d763ee15cb98d47369f"
AUTHORITATIVE_POLICY_SHA256 = (
    "1a1889d291cf1fb836dca935c518dfdc18c0f549344ed9fb672b8e769d188f10"
)


class RuntimeIntegrityAdapter:
    """Translate canonical runtime-integrity decisions onto plugin hooks."""

    _DEFAULT_CEILING = "C1_ROUTINE_ASSISTANT"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        identity_adapter: Any = None,
    ) -> None:
        self._config = copy.deepcopy(dict(config or {}))
        self._identity_adapter = identity_adapter
        self._ledger = RuntimeIntegrityLedger()

    @property
    def config(self) -> dict[str, Any]:
        return copy.deepcopy(self._config)

    def _settings(self) -> dict[str, Any]:
        nested = self._config.get("runtime_integrity")
        source = nested if isinstance(nested, Mapping) else self._config
        return copy.deepcopy(dict(source))

    @property
    def enabled(self) -> bool:
        return self._settings().get("enabled") is True

    @staticmethod
    def _required(value: Any, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise RuntimeIntegrityViolation(f"{field_name} is required")
        return text

    def _identity(self) -> Any:
        if self._identity_adapter is not None:
            return self._identity_adapter
        settings = self._settings()
        return WindowsRuntimeIdentityAdapter(
            config=settings,
            host=str(settings.get("host") or "").strip() or None,
        )

    def _make_envelope(
        self,
        readback: WindowsRuntimeReadback,
        *,
        session_id: str,
        attempt_id: str,
    ) -> RuntimeEnvelope:
        if not isinstance(readback, WindowsRuntimeReadback):
            raise RuntimeIntegrityViolation("runtime identity readback has an invalid type")
        if not readback.attested:
            raise RuntimeIntegrityViolation(
                "RUNTIME_IDENTITY_ATTESTATION_FAILED provider/model/route readback mismatch"
            )
        settings = self._settings()
        ceilings = settings.get("ceilings")
        ceilings = ceilings if isinstance(ceilings, Mapping) else settings
        return RuntimeEnvelope.create(
            role_id=readback.role_id,
            host=readback.host,
            provider=readback.provider,
            exact_model_id=readback.exact_model_id,
            model_route=readback.model_route,
            policy_generation=readback.policy_generation,
            session_id=session_id,
            attempt_id=attempt_id,
            capability_tier=readback.capability_tier,
            role_ceiling=str(
                ceilings.get("role_ceiling") or self._DEFAULT_CEILING
            ),
            task_ceiling=str(
                ceilings.get("task_ceiling") or self._DEFAULT_CEILING
            ),
            evidence_ceiling=str(
                ceilings.get("evidence_ceiling") or self._DEFAULT_CEILING
            ),
            policy_ceiling=str(
                ceilings.get("policy_ceiling") or self._DEFAULT_CEILING
            ),
        )

    def bind_readback(
        self,
        readback: WindowsRuntimeReadback,
        *,
        session_id: str,
        attempt_id: str,
        verified_state: VerifiedHandoffState | None = None,
    ) -> RuntimeEnvelope:
        if not self.enabled:
            raise RuntimeIntegrityViolation("runtime-integrity adapter is disabled")
        session_id = self._required(session_id, "session_id")
        attempt_id = self._required(attempt_id, "attempt_id")
        current = self._make_envelope(
            readback,
            session_id=session_id,
            attempt_id=attempt_id,
        )
        previous = self._ledger.current_occupant(current.role_id)
        if previous is not None and (
            previous.provider != current.provider
            or previous.exact_model_id != current.exact_model_id
            or previous.model_route != current.model_route
        ):
            raise RuntimeIntegrityViolation(
                "MODEL_OCCUPANT_CHANGE_DETECTED requires a canonical request boundary"
            )
        self._ledger.record_envelope(current)
        self._ledger.record_event(
            session_id=current.session_id,
            role_id=current.role_id,
            event="RUNTIME_IDENTITY_BOUND",
            payload={"envelope_sha256": current.envelope_sha256},
        )
        return current

    def bind_agent(
        self,
        agent: Any,
        *,
        session_id: str | None = None,
        attempt_id: str,
        verified_state: VerifiedHandoffState | None = None,
    ) -> RuntimeEnvelope | None:
        if not self.enabled:
            return None
        resolved_session = session_id or getattr(agent, "session_id", "")
        readback = self._identity().read_agent(agent)
        return self.bind_readback(
            readback,
            session_id=str(resolved_session or ""),
            attempt_id=attempt_id,
            verified_state=verified_state,
        )

    def current_envelope(self, session_id: str) -> RuntimeEnvelope | None:
        return self._ledger.envelope_for_session(str(session_id))

    def events_for_session(self, session_id: str) -> tuple[str, ...]:
        return self._ledger.events_for_session(str(session_id))

    def receipts_for_session(self, session_id: str) -> tuple[ToolReceipt, ...]:
        return self._ledger.receipts_for_session(str(session_id))

    def _active_envelope(
        self, session_id: str, attempt_id: str | None = None
    ) -> RuntimeEnvelope:
        envelope = self.current_envelope(session_id)
        if envelope is None or not envelope.verify():
            raise RuntimeIntegrityViolation(
                "RUNTIME_IDENTITY_ATTESTATION_FAILED no active envelope"
            )
        if attempt_id and envelope.attempt_id != str(attempt_id):
            raise RuntimeIntegrityViolation(
                "RUNTIME_IDENTITY_ATTESTATION_FAILED attempt changed"
            )
        return envelope

    @staticmethod
    def _receipt_dict(receipt: ToolReceipt, receipt_kind: str) -> dict[str, Any]:
        return {
            **asdict(receipt),
            "receipt_kind": receipt_kind,
            "envelope_hash": receipt.runtime_envelope_sha256,
        }

    def pre_tool_call(
        self,
        *,
        tool_name: str,
        args: Any = None,
        session_id: str,
        tool_call_id: str = "",
        attempt_id: str | None = None,
        api_request_id: str = "",
        turn_id: str = "",
        model_claimed_capable: bool | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"allowed": True, "action": "allow", "event": "INTEGRITY_DISABLED"}
        tool_name = self._required(tool_name, "tool_name")
        session_id = self._required(session_id, "session_id")
        try:
            envelope = self._active_envelope(
                session_id, attempt_id or api_request_id or turn_id
            )
        except RuntimeIntegrityViolation as exc:
            return {
                "allowed": False,
                "action": "block",
                "event": "RUNTIME_IDENTITY_ATTESTATION_FAILED",
                "message": str(exc),
            }

        settings = self._settings()
        requirements = settings.get("tool_requirements")
        requirements = requirements if isinstance(requirements, Mapping) else {}
        required = requirements.get(tool_name)
        if required:
            admission = admit_capability(
                required_tier=str(required),
                role_ceiling=envelope.role_ceiling,
                task_ceiling=envelope.task_ceiling,
                model_capability=envelope.capability_tier,
                evidence_ceiling=envelope.evidence_ceiling,
                policy_ceiling=envelope.policy_ceiling,
                tool_name=tool_name,
                model_claimed_capable=model_claimed_capable,
                mismatch_action=str(settings.get("mismatch_action") or "ESCALATE"),
            )
            if not admission.allowed:
                return {
                    "allowed": False,
                    "action": "block",
                    "event": admission.event,
                    "message": (
                        f"CAPABILITY_MISMATCH tool={tool_name} "
                        f"required={admission.required_tier} "
                        f"effective={admission.effective_authority} "
                        f"action={admission.action}"
                    ),
                    "admission": asdict(admission),
                    "envelope_hash": envelope.envelope_sha256,
                }

        evidence_requirements = settings.get("evidence_requirements")
        evidence_requirements = (
            evidence_requirements
            if isinstance(evidence_requirements, Mapping)
            else {}
        )
        evidence_kind = str(evidence_requirements.get(tool_name) or "").strip().lower()
        if evidence_kind:
            evidence_refs = self._evidence_refs(args)
            if not self._trusted_admission(evidence_kind, evidence_refs, args):
                event = f"{evidence_kind.upper()}_ADMISSION_DENIED"
                self._ledger.record_event(
                    session_id=session_id,
                    role_id=envelope.role_id,
                    event=event,
                    payload={"tool_name": tool_name, "evidence_refs": evidence_refs},
                )
                return {
                    "allowed": False,
                    "action": "block",
                    "event": event,
                    "message": (
                        f"{event} tool={tool_name} trusted_receipt_required=true"
                    ),
                    "envelope_hash": envelope.envelope_sha256,
                }

        call_id = str(tool_call_id or f"{tool_name}-{len(self.receipts_for_session(session_id)) + 1}")
        receipt = self._ledger.record_tool_receipt(
            envelope=envelope,
            tool_name=tool_name,
            tool_call_id=call_id,
            args=args,
            result=None,
            status="pre_tool_call",
        )
        self._ledger.record_event(
            session_id=session_id,
            role_id=envelope.role_id,
            event="TOOL_CALL_BOUND",
            payload={"receipt_sha256": receipt.receipt_sha256},
        )
        return {
            "allowed": True,
            "action": "allow",
            "event": "TOOL_CALL_BOUND",
            "receipt": self._receipt_dict(receipt, "tool_call"),
            "envelope": envelope.as_dict(),
            "tool_call_id": call_id,
        }

    def post_tool_call(
        self,
        *,
        tool_name: str,
        args: Any = None,
        result: Any = None,
        session_id: str,
        tool_call_id: str = "",
        attempt_id: str | None = None,
        api_request_id: str = "",
        turn_id: str = "",
        status: str = "unknown",
        **_: Any,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"allowed": True, "event": "INTEGRITY_DISABLED"}
        tool_name = self._required(tool_name, "tool_name")
        session_id = self._required(session_id, "session_id")
        try:
            envelope = self._active_envelope(
                session_id, attempt_id or api_request_id or turn_id
            )
        except RuntimeIntegrityViolation as exc:
            return {
                "allowed": False,
                "event": "ACTION_NOT_PROVEN",
                "publication_allowed": False,
                "message": str(exc),
            }
        call_id = str(tool_call_id or f"{tool_name}-{len(self.receipts_for_session(session_id)) + 1}")
        try:
            receipt = self._ledger.record_tool_receipt(
                envelope=envelope,
                tool_name=tool_name,
                tool_call_id=call_id,
                args=args,
                result=result,
                status=str(status),
                bind_to_pre_call=True,
            )
        except RuntimeIntegrityViolation as exc:
            self._ledger.record_event(
                session_id=session_id,
                role_id=envelope.role_id,
                event="ACTION_NOT_PROVEN",
                payload={"tool_name": tool_name, "tool_call_id": call_id},
            )
            return {
                "allowed": False,
                "event": "ACTION_NOT_PROVEN",
                "publication_allowed": False,
                "message": str(exc),
            }
        self._ledger.record_event(
            session_id=session_id,
            role_id=envelope.role_id,
            event="TOOL_RESULT_BOUND",
            payload={"receipt_sha256": receipt.receipt_sha256},
        )
        response = {
            "allowed": True,
            "event": "TOOL_RESULT_BOUND",
            "receipt": self._receipt_dict(receipt, "tool_result"),
            "envelope_hash": envelope.envelope_sha256,
        }
        artifact = self._readback_for_tool(
            tool_name=tool_name,
            args=args,
            result=result,
            result_receipt=receipt,
        )
        if artifact is not None:
            response["artifact_readback"] = asdict(artifact)
            if not artifact.passed:
                response["allowed"] = False
                response["publication_allowed"] = False
        return response

    def prepare_request(
        self,
        request: Mapping[str, Any],
        *,
        session_id: str,
        attempt_id: str,
        model: str,
        provider: str,
        base_url: str,
        api_mode: str = "",
        model_route: str = "",
        runtime_agent: Any = None,
        runtime_envelope: RuntimeEnvelope | None = None,
        current_user_message: str | None = None,
        verified_state: VerifiedHandoffState | None = None,
    ) -> dict[str, Any]:
        del model, provider, base_url, api_mode, model_route
        if not isinstance(request, Mapping):
            raise RuntimeIntegrityViolation("outgoing request must be a mapping")
        if not self.enabled:
            return {
                "request": copy.deepcopy(dict(request)),
                "changed": False,
                "events": (),
            }
        try:
            session_id = self._required(session_id, "session_id")
            attempt_id = self._required(attempt_id, "attempt_id")
            if runtime_agent is not None:
                readback = self._identity().read_agent(runtime_agent)
                envelope = self._make_envelope(
                    readback, session_id=session_id, attempt_id=attempt_id
                )
            elif isinstance(runtime_envelope, RuntimeEnvelope):
                if (
                    not runtime_envelope.verify()
                    or runtime_envelope.session_id != session_id
                    or runtime_envelope.attempt_id != attempt_id
                ):
                    raise RuntimeIntegrityViolation(
                        "RUNTIME_IDENTITY_ATTESTATION_FAILED inconsistent runtime envelope"
                    )
                envelope = runtime_envelope
            else:
                raise RuntimeIntegrityViolation(
                    "RUNTIME_IDENTITY_ATTESTATION_FAILED trusted live runtime is absent"
                )
            result = bind_runtime_envelope(
                envelope,
                request=request,
                current_user_message=current_user_message,
                verified_state=verified_state,
                ledger=self._ledger,
            )
        except (RuntimeIntegrityViolation, RuntimeReadbackError, ValueError) as exc:
            blocked_request = copy.deepcopy(dict(request))
            if "tools" in blocked_request:
                blocked_request["tools"] = []
            if "tool_choice" in blocked_request:
                blocked_request["tool_choice"] = "none"
            return {
                "request": blocked_request,
                "changed": True,
                "events": ("RUNTIME_IDENTITY_ATTESTATION_FAILED",),
                "event": "RUNTIME_IDENTITY_ATTESTATION_FAILED",
                "owner_notice": str(exc),
                "blocked": True,
            }
        result["events"] = self.events_for_session(session_id)
        return result

    def llm_request_middleware(
        self,
        *,
        request: Mapping[str, Any],
        session_id: str = "",
        api_request_id: str = "",
        turn_id: str = "",
        model: str = "",
        provider: str = "",
        requested_provider: str = "",
        base_url: str = "",
        api_mode: str = "",
        model_route: str = "",
        current_user_message: str | None = None,
        verified_state: VerifiedHandoffState | None = None,
        runtime_agent: Any = None,
        runtime_envelope: RuntimeEnvelope | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        return self.prepare_request(
            request,
            session_id=session_id,
            attempt_id=api_request_id or turn_id,
            model=model,
            provider=provider or requested_provider,
            base_url=base_url,
            api_mode=api_mode,
            model_route=model_route,
            current_user_message=current_user_message,
            verified_state=verified_state,
            runtime_agent=runtime_agent,
            runtime_envelope=runtime_envelope,
        )

    def llm_request_callback(self, **context: Any) -> dict[str, Any] | None:
        result = self.llm_request_middleware(**context)
        notice_callback = context.get("runtime_notice_callback")
        owner_notice = result.get("owner_notice")
        if owner_notice and callable(notice_callback):
            notice_callback(str(owner_notice))
        return result if self.enabled else None

    def llm_execution_middleware(
        self,
        *,
        request: Mapping[str, Any],
        next_call: Any,
        session_id: str = "",
        api_request_id: str = "",
        runtime_agent: Any = None,
        **_: Any,
    ) -> Any:
        from hermes_cli.middleware import MiddlewareExecutionBlocked

        if not self.enabled:
            return next_call(request)
        try:
            session_id = self._required(session_id, "session_id")
            api_request_id = self._required(api_request_id, "api_request_id")
            if runtime_agent is None:
                raise RuntimeIntegrityViolation(
                    "RUNTIME_IDENTITY_ATTESTATION_FAILED trusted live runtime is absent"
                )
            readback = self._identity().read_agent(runtime_agent)
            current = self._make_envelope(
                readback,
                session_id=session_id,
                attempt_id=api_request_id,
            )
            active = self._active_envelope(session_id, api_request_id)
            if (
                active.provider != current.provider
                or active.exact_model_id != current.exact_model_id
                or active.model_route != current.model_route
                or active.policy_generation != current.policy_generation
            ):
                raise RuntimeIntegrityViolation(
                    "RUNTIME_IDENTITY_ATTESTATION_FAILED live transport changed after request admission"
                )
            request_model = str(request.get("model") or "").strip()
            if request_model and request_model != current.exact_model_id:
                raise RuntimeIntegrityViolation(
                    "RUNTIME_IDENTITY_ATTESTATION_FAILED request model does not match live runtime"
                )
        except (RuntimeIntegrityViolation, RuntimeReadbackError, ValueError) as exc:
            raise MiddlewareExecutionBlocked(str(exc)) from exc
        return next_call(request)

    @staticmethod
    def _evidence_refs(args: Any) -> tuple[str, ...]:
        if not isinstance(args, Mapping):
            return ()
        refs: list[str] = []
        for key in (
            "evidence_refs",
            "receipt_refs",
            "provenance_refs",
            "runtime_integrity_receipts",
        ):
            value = args.get(key)
            if isinstance(value, str):
                refs.append(value)
            elif isinstance(value, (list, tuple)):
                refs.extend(str(item) for item in value if item)
        return tuple(dict.fromkeys(refs))

    def _trusted_admission(
        self,
        kind: str,
        refs: tuple[str, ...],
        args: Any,
    ) -> bool:
        if not refs or not isinstance(args, Mapping):
            return False
        if kind == "experiment":
            receipt = args.get("experiment_receipt")
            if not isinstance(receipt, Mapping):
                return False
            validation = validate_experiment_receipt(dict(receipt), ledger=self._ledger)
            return validation.action_proven and validation.publication_allowed
        if kind == "debate":
            receipt = args.get("debate_receipt")
            if not isinstance(receipt, Mapping):
                return False
            validation = validate_debate_receipt(dict(receipt), ledger=self._ledger)
            return validation.experimental_multi_model_evidence and validation.publication_allowed
        if kind == "publication":
            acceptance_ref = str(args.get("controlling_acceptance_ref") or "")
            subject_ref = str(args.get("subject_ref") or "")
            return bool(
                acceptance_ref
                and subject_ref
                and acceptance_ref in refs
                and self._ledger.controlling_acceptance_covers(
                    acceptance_ref, subject_ref
                )
            )
        if kind == "memory":
            statement = str(args.get("statement") or args.get("content") or "")
            if not statement:
                return False
            statement_sha256 = hashlib.sha256(statement.encode("utf-8")).hexdigest()
            return any(
                (record := self._ledger.artifact_readback_record(ref)) is not None
                and record["artifact_sha256"] == statement_sha256
                for ref in refs
            )
        return False

    def _artifact_spec(self, tool_name: str) -> dict[str, Any] | None:
        specs = self._settings().get("artifact_contracts")
        if not isinstance(specs, Mapping):
            return None
        value = specs.get(tool_name)
        return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else None

    @staticmethod
    def _artifact_path(spec: Mapping[str, Any], args: Any, result: Any) -> str:
        if spec.get("path"):
            return str(spec["path"])
        path_arg = str(spec.get("path_arg") or "path")
        if isinstance(args, Mapping) and args.get(path_arg):
            return str(args[path_arg])
        result_key = str(spec.get("result_path_key") or "path")
        if isinstance(result, Mapping) and result.get(result_key):
            return str(result[result_key])
        return ""

    def _readback_for_tool(
        self,
        *,
        tool_name: str,
        args: Any,
        result: Any,
        result_receipt: ToolReceipt,
    ) -> ArtifactReadback | None:
        spec = self._artifact_spec(tool_name)
        if spec is None:
            return None
        path = self._artifact_path(spec, args, result)
        readback = readback_artifact(
            path,
            required=bool(spec.get("required", True)),
            required_json_keys=tuple(spec.get("required_json_keys") or ()),
            expected_sha256=str(spec.get("expected_sha256") or "") or None,
            acceptance_contract=(
                spec.get("acceptance_contract")
                if isinstance(spec.get("acceptance_contract"), Mapping)
                else None
            ),
        )
        self._ledger.record_artifact_readback(
            readback,
            result_receipt_ref=result_receipt.receipt_sha256,
            path=path,
            acceptance_contract=(
                spec.get("acceptance_contract")
                if isinstance(spec.get("acceptance_contract"), Mapping)
                else None
            ),
        )
        return readback

    def on_session_finalize(self, *, session_id: str = "", **_: Any) -> dict[str, Any]:
        session_id = str(session_id or "")
        failures: list[dict[str, Any]] = []
        specs = self._settings().get("required_artifacts")
        iterable = specs.values() if isinstance(specs, Mapping) else (specs or ())
        for raw_spec in iterable:
            if not isinstance(raw_spec, Mapping):
                continue
            spec = dict(raw_spec)
            tool_call_id = str(spec.get("tool_call_id") or "")
            receipt = self._ledger.tool_result_for_call(session_id, tool_call_id)
            if receipt is None:
                failures.append(
                    {
                        "event": "ACTION_NOT_PROVEN",
                        "reason": "required artifact has no trusted result receipt",
                    }
                )
                continue
            path = str(spec.get("path") or "")
            readback = readback_artifact(
                path,
                required=bool(spec.get("required", True)),
                required_json_keys=tuple(spec.get("required_json_keys") or ()),
                expected_sha256=str(spec.get("expected_sha256") or "") or None,
                acceptance_contract=(
                    spec.get("acceptance_contract")
                    if isinstance(spec.get("acceptance_contract"), Mapping)
                    else None
                ),
            )
            self._ledger.record_artifact_readback(
                readback,
                result_receipt_ref=receipt.receipt_sha256,
                path=path,
                acceptance_contract=(
                    spec.get("acceptance_contract")
                    if isinstance(spec.get("acceptance_contract"), Mapping)
                    else None
                ),
            )
            if not readback.passed:
                failures.append(asdict(readback))
        event = "SESSION_ARTIFACTS_VERIFIED" if not failures else "SILENT_FAILURE_DETECTED"
        envelope = self.current_envelope(session_id)
        self._ledger.record_event(
            session_id=session_id,
            role_id=envelope.role_id if envelope else "",
            event=event,
            payload={"failures": failures},
        )
        return {
            "allowed": not failures,
            "publication_allowed": not failures,
            "event": event,
            "failures": failures,
        }

    def on_session_end(self, *, session_id: str = "", **_: Any) -> None:
        if session_id:
            envelope = self.current_envelope(str(session_id))
            self._ledger.record_event(
                session_id=str(session_id),
                role_id=envelope.role_id if envelope else "",
                event="SESSION_ENDED",
            )


_PLUGIN_SETTING_KEYS = (
    "enabled",
    "role_id",
    "host",
    "policy_generation",
    "role_ceiling",
    "task_ceiling",
    "evidence_ceiling",
    "policy_ceiling",
    "ceilings",
    "model_capabilities",
    "capabilities",
    "default_capability",
    "tool_requirements",
    "evidence_requirements",
    "artifact_contracts",
    "required_artifacts",
    "mismatch_action",
)


def register_plugin(ctx: Any) -> RuntimeIntegrityAdapter | None:
    """Register canonical policy adapters only after an explicit opt-in."""

    if ctx is None or not callable(getattr(ctx, "get_config", None)):
        raise ValueError("runtime-integrity registration requires a plugin context")
    config: dict[str, Any] = {}
    for key in _PLUGIN_SETTING_KEYS:
        value = ctx.get_config(key, None)
        if value is not None:
            config[key] = copy.deepcopy(value)
    adapter = RuntimeIntegrityAdapter(config=config)
    if not adapter.enabled:
        return None
    ctx.register_middleware("llm_request", adapter.llm_request_callback)
    ctx.register_middleware("llm_execution", adapter.llm_execution_middleware)
    ctx.register_hook("pre_tool_call", adapter.pre_tool_call)
    ctx.register_hook("post_tool_call", adapter.post_tool_call)
    ctx.register_hook("on_session_finalize", adapter.on_session_finalize)
    ctx.register_hook("on_session_end", adapter.on_session_end)
    return adapter


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load the plugin's bundled owner-gated configuration artifact."""

    policy_path = (
        Path(path)
        if path is not None
        else Path(__file__).with_name(POLICY_FILENAME)
    )
    try:
        raw_policy = policy_path.read_bytes()
        if path is None and hashlib.sha256(raw_policy).hexdigest() != (
            AUTHORITATIVE_POLICY_SHA256
        ):
            raise ValueError(
                "vendored runtime-integrity policy does not match authoritative source"
            )
        policy = yaml.safe_load(raw_policy.decode("utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(
            f"unable to read runtime-integrity policy: {policy_path}"
        ) from exc
    return validate_policy(policy)


__all__ = [
    "AUTHORITATIVE_POLICY_SHA256",
    "AUTHORITATIVE_YATIMA_COMMIT",
    "ArtifactReadback",
    "CapabilityAdmission",
    "CapabilityTier",
    "ClaimContradiction",
    "DebateReceiptValidation",
    "ExperimentReceiptValidation",
    "FAIL_TERMINAL",
    "OccupantTransition",
    "PASS_TERMINAL",
    "POLICY_FILENAME",
    "POLICY_GENERATION",
    "ProvenanceRecord",
    "RuntimeEnvelope",
    "RuntimeIntegrityAdapter",
    "RuntimeIntegrityViolation",
    "RuntimeReadbackError",
    "ToolReceipt",
    "VerifiedHandoffState",
    "WindowsRuntimeIdentityAdapter",
    "WindowsRuntimeReadback",
    "admit_capability",
    "create_tool_receipt",
    "detect_claim_contradiction",
    "detect_occupant_change",
    "filter_outgoing_request",
    "load_policy",
    "readback_artifact",
    "register_plugin",
    "validate_debate_receipt",
    "validate_experiment_receipt",
    "validate_policy",
]
