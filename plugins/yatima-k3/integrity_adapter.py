"""Narrow opt-in Hermes adapter for the portable Yatima trust anchor.

This module is deliberately an adapter only.  It imports the platform-neutral
core from the explicit configured source path, reads public issuers and an
existing ledger, and requires host-provided authenticated receipts.  It never
loads private keys and exposes no signing tool or arbitrary signing method.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from hermes_constants import get_hermes_home


class IntegrityMiddlewareDenied(RuntimeError):
    """A protected Hermes chain must not continue after this denial."""

    _integrity_fail_closed = True


def _load_core(core_path: str) -> Any:
    root = Path(core_path).expanduser()
    if not root.is_absolute() or not (root / "yatima_runtime_integrity" / "__init__.py").is_file():
        raise IntegrityMiddlewareDenied("configured integrity core is missing")
    package_file = (root / "yatima_runtime_integrity" / "__init__.py").resolve()
    existing = sys.modules.get("yatima_runtime_integrity")
    if existing is not None:
        loaded = getattr(existing, "__file__", None)
        if loaded is None or Path(loaded).resolve() != package_file:
            raise IntegrityMiddlewareDenied("integrity core is already loaded from another source")
        return existing
    text = str(root.resolve())
    if text not in sys.path:
        sys.path.insert(0, text)
    importlib.invalidate_caches()
    module = importlib.import_module("yatima_runtime_integrity")
    loaded = getattr(module, "__file__", None)
    if loaded is None or Path(loaded).resolve() != package_file:
        raise IntegrityMiddlewareDenied("loaded integrity core path does not match configuration")
    return module


def _load_public_registry(core: Any, path: Path) -> Any:
    if not path.is_file():
        raise IntegrityMiddlewareDenied("integrity issuer registry is missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityMiddlewareDenied("integrity issuer registry is malformed") from exc
    values = raw.get("issuers", raw) if isinstance(raw, Mapping) else raw
    if isinstance(values, Mapping):
        values = list(values.values())
    if not isinstance(values, list) or not values:
        raise IntegrityMiddlewareDenied("integrity issuer registry has no public issuers")
    try:
        issuers = [core.Issuer.from_dict(item) for item in values]
        if not any(not item.revoked for item in issuers):
            raise IntegrityMiddlewareDenied("integrity issuer registry has no active issuer")
        return core.IssuerRegistry.from_dict({"issuers": values})
    except IntegrityMiddlewareDenied:
        raise
    except Exception as exc:
        raise IntegrityMiddlewareDenied("integrity issuer registry is invalid") from exc


class HermesIntegrityRuntime:
    """Authenticated fail-closed middleware around final tool/provider calls."""

    _integrity_fail_closed = True

    def __init__(self, *, core: Any, config: Mapping[str, Any], reason: str = "active") -> None:
        self.core = core
        self.config = dict(config)
        self.active = reason == "active"
        self.reason = reason
        self.verifier = None
        self.ledger = None
        self.broker = None
        self.envelopes = core.AttemptEnvelopeStore()
        self._evidence_state = "CANDIDATE"
        if not self.active:
            return
        registry_path = Path(str(self.config["issuer_registry_path"])).expanduser()
        if self.config.get("receipt_mode") == "host_observed":
            self.broker = core.Ed25519ReceiptBroker.generate(
                issuer_id=str(self.config.get("issuer_id", "hermes-executor")),
                issuer_class="EXECUTOR",
                key_id=f"worker-{os.getpid()}",
                policy_generation=str(self.config["policy_generation"]),
            )
            registry = self._activate_host_issuer(registry_path)
        else:
            registry = _load_public_registry(core, registry_path)
        self.verifier = core.ReceiptVerifier(registry)
        ledger_path = Path(str(self.config["ledger_path"])).expanduser()
        # Opening, never creating, is intentional: missing integrity state
        # must deny rather than bootstrap an empty trust anchor in-process.
        self.ledger = core.IntegrityLedger(ledger_path, verifier=self.verifier)

    def _activate_host_issuer(self, registry_path: Path) -> Any:
        """Add only this process' public key; private material never leaves memory."""

        values: list[Mapping[str, Any]] = []
        if registry_path.is_file():
            raw = json.loads(registry_path.read_text(encoding="utf-8"))
            existing = raw.get("issuers", raw) if isinstance(raw, Mapping) else raw
            if isinstance(existing, Mapping):
                existing = list(existing.values())
            if not isinstance(existing, list):
                raise IntegrityMiddlewareDenied("integrity issuer registry is malformed")
            for value in existing:
                values.append(self.core.Issuer.from_dict(value).to_dict())
        assert self.broker is not None
        current = self.broker.public_issuer().to_dict()
        values = [
            value for value in values
            if not (
                value.get("issuer_id") == current["issuer_id"]
                and value.get("key_id") == current["key_id"]
            )
        ]
        values.append(current)
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = registry_path.with_suffix(registry_path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps({"issuers": values}, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(registry_path)
        return self.core.IssuerRegistry.from_dict({"issuers": values})

    @classmethod
    def inactive(cls, reason: str, *, core: Any | None = None, config: Mapping[str, Any] | None = None) -> "HermesIntegrityRuntime":
        if core is None:
            # A tiny stand-in still exposes the callbacks so an explicitly
            # enabled but malformed configuration fails closed at execution.
            class _NoCore:
                class AttemptEnvelopeStore:
                    def __init__(self) -> None:
                        pass
            core = _NoCore()
        return cls(core=core, config=dict(config or {}), reason=reason)

    def _deny(self, reason: str) -> None:
        raise IntegrityMiddlewareDenied(reason)

    def _attempt_id(self, context: Mapping[str, Any]) -> str:
        scope = self.config.get("scope", {})
        expected = scope.get("attempt_id") if isinstance(scope, Mapping) else None
        if self.config.get("worker_mode") is True:
            request_id = context.get("api_request_id") or context.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                self._deny("worker runtime request identity is missing")
            digest = self.core.sha256_text(request_id)[:16]
            return f"{expected}:call:{digest}"
        supplied = context.get("attempt_id") if "attempt_id" in context else expected
        if supplied != expected:
            self._deny("runtime attempt identity does not match the configured scope")
        value = supplied
        if not isinstance(value, str) or not value:
            self._deny("integrity attempt identity is missing")
        return value

    def _role_id(self, context: Mapping[str, Any]) -> str:
        scope = self.config.get("scope", {})
        expected = scope.get("role_scope") if isinstance(scope, Mapping) else None
        supplied = context.get("role_id") if "role_id" in context else expected
        if supplied != expected:
            self._deny("runtime role identity does not match the configured scope")
        value = supplied
        if not isinstance(value, str) or not value:
            self._deny("integrity role identity is missing")
        return value

    def _assert_scope_context(self, context: Mapping[str, Any]) -> None:
        scope = self.config.get("scope", {})
        if not isinstance(scope, Mapping):
            self._deny("integrity scope is missing")
        checks = (
            ("host", scope.get("host_id")),
            ("host_id", scope.get("host_id")),
            ("project_scope", scope.get("project_scope")),
            ("role_scope", scope.get("role_scope")),
            ("session_id", scope.get("session_id")),
            ("task_id", scope.get("task_id")),
            ("attempt_id", scope.get("attempt_id")),
            ("source_generation", scope.get("source_generation")),
            ("policy_generation", scope.get("policy_generation") or self.config.get("policy_generation")),
        )
        if self.config.get("worker_mode") is True:
            checks = tuple(item for item in checks if item[0] in {"task_id", "policy_generation"})
        for name, expected in checks:
            supplied = context.get(name)
            if supplied is not None and supplied != expected:
                self._deny(f"runtime {name} does not match the configured scope")
        for name in ("task_current", "attempt_current", "cancelled", "superseded"):
            expected = scope.get(name)
            supplied = context.get(name)
            if supplied is not None and supplied != expected:
                self._deny(f"runtime {name} does not match the configured scope")
        if scope.get("task_current") is not True or scope.get("attempt_current") is not True:
            self._deny("integrity scope is stale")
        if scope.get("cancelled") is True or scope.get("superseded") is True or scope.get("stale_reason") is not None:
            self._deny("integrity scope is cancelled, superseded, or stale")

    def _identity(self, request: Mapping[str, Any], context: Mapping[str, Any]) -> Any:
        scope = self.config.get("scope", {})
        configured = self.config.get("runtime_identity", {})
        configured = configured if isinstance(configured, Mapping) else {}
        if not isinstance(scope, Mapping):
            self._deny("integrity scope is missing")
        self._assert_scope_context(context)
        # A host cannot repair a configured identity mismatch merely by
        # reporting a different provider/model in callback kwargs.  The
        # effective identity is late-bound, but all configured expectations
        # remain immutable constraints for this attempt.
        for name, aliases in (
            ("host", ("host", "host_id")),
            ("provider", ("provider",)),
            ("model_route", ("model_route",)),
            ("exact_model_id", ("exact_model_id", "model", "model_id")),
            ("session_id", ("session_id",)),
            ("attempt_id", ("attempt_id",)),
            ("capability_certificate", ("capability_certificate",)),
            ("endpoint_identity", ("endpoint_identity", "base_url")),
        ):
            expected = configured.get(name)
            if expected in (None, "") and name == "host":
                expected = configured.get("host_id")
            if expected in (None, "") and name == "exact_model_id":
                expected = configured.get("model") or configured.get("model_id")
            if expected in (None, ""):
                continue
            supplied = next((context[key] for key in aliases if context.get(key) is not None), None)
            if supplied is not None and supplied != expected:
                self._deny(f"runtime {name} does not match the configured identity")
        configured_cert = configured.get("capability_certificate")
        supplied_cert = context.get("capability_certificate")
        if configured_cert is not None and supplied_cert is not None and supplied_cert != configured_cert:
            self._deny("capability certificate changed after identity binding")
        request_model = request.get("model") or request.get("model_id")
        supplied_model = context.get("exact_model_id") or context.get("model")
        configured_model = configured.get("exact_model_id")
        exact_model = supplied_model or configured_model or request_model
        if request_model is not None and exact_model is not None and request_model != exact_model:
            self._deny("final request model does not match runtime identity")
        values = {
            "role_id": self._role_id(context),
            "host": context.get("host") or context.get("host_id") or scope.get("host_id"),
            "provider": context.get("provider") or configured.get("provider"),
            "model_route": context.get("model_route") or configured.get("model_route") or context.get("provider"),
            "exact_model_id": exact_model,
            "policy_generation": scope.get("policy_generation") or self.config.get("policy_generation"),
            "session_id": context.get("session_id") or scope.get("session_id"),
            "attempt_id": self._attempt_id(context),
            "capability_certificate": supplied_cert or configured_cert,
            "endpoint_identity": context.get("base_url") or configured.get("endpoint_identity", ""),
        }
        try:
            identity = self.core.RuntimeIdentity.from_mapping(values)
            certificate_raw = self.config.get("capability_certificate")
            if certificate_raw is not None:
                certificate = self.core.CapabilityCertificate.from_mapping(certificate_raw)
                if certificate.role_id != identity.role_id:
                    self._deny("capability certificate role does not match runtime identity")
                if certificate.exact_model_id and certificate.exact_model_id != identity.exact_model_id:
                    self._deny("capability certificate model does not match runtime identity")
                if self.broker is not None and certificate.issuer_id:
                    if certificate.issuer_id != self.broker.public_issuer().issuer_id:
                        self._deny("capability certificate issuer does not match runtime issuer")
            expected_hash = context.get("final_request_hash")
            if expected_hash is not None and expected_hash != self.core.request_hash(request):
                self._deny("STALE_RUNTIME_IDENTITY: final request bytes changed after route resolution")
            return identity
        except IntegrityMiddlewareDenied:
            raise
        except Exception as exc:
            self._deny(f"runtime identity is incomplete or invalid: {exc}")
        raise AssertionError("unreachable")

    def _verify(
        self,
        value: Any,
        *,
        event_type: str,
        attempt_id: str,
        subject: str | None = None,
        role_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> Any:
        if value is None or self.verifier is None:
            self._deny(f"{event_type} receipt is missing or verifier unavailable")
        try:
            receipt = self.verifier.verify(
                value,
                expected_event_type=event_type,
                expected_attempt_id=attempt_id,
                expected_policy_generation=self.config.get("policy_generation"),
            )
            if subject is not None and subject not in receipt.subject_hashes.values():
                self._deny(f"{event_type} receipt is not bound to the exact request/result")
            if role_id is not None and receipt.role_id != role_id:
                self._deny(f"{event_type} receipt role binding mismatch")
            for key, expected in (context or {}).items():
                if receipt.context.get(key) != expected:
                    self._deny(f"{event_type} receipt {key} binding mismatch")
            return receipt
        except IntegrityMiddlewareDenied:
            raise
        except Exception as exc:
            self._deny(f"authenticated {event_type} receipt rejected: {exc}")
        raise AssertionError("unreachable")

    @staticmethod
    def _result_hash(core: Any, result: Any) -> str:
        def canonical(value: Any, seen: set[int]) -> Any:
            if isinstance(value, (str, int, float, bool, type(None))):
                return value
            if isinstance(value, Mapping):
                return {str(key): canonical(item, seen) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [canonical(item, seen) for item in value]
            identity = id(value)
            if identity in seen:
                raise IntegrityMiddlewareDenied("provider/tool result contains a cycle")
            seen.add(identity)
            try:
                for method in ("model_dump", "to_dict", "dict"):
                    converter = getattr(value, method, None)
                    if callable(converter):
                        return canonical(converter(), seen)
                attributes = getattr(value, "__dict__", None)
                if isinstance(attributes, Mapping):
                    public = {
                        str(key): item
                        for key, item in attributes.items()
                        if isinstance(key, str) and not key.startswith("_") and not callable(item)
                    }
                    if public:
                        return canonical(public, seen)
            finally:
                seen.discard(identity)
            raise IntegrityMiddlewareDenied("provider/tool result cannot be canonically read back")

        return core.sha256_json(canonical(result, set()))

    def on_llm_execution(self, *, request: Any, next_call: Any, **context: Any) -> Any:
        if not self.active:
            self._deny(f"integrity adapter unavailable: {self.reason}")
        if not isinstance(request, Mapping):
            self._deny("final provider request is not a mapping")
        finalizer = context.get("final_request_resolver")
        if finalizer is not None:
            if not callable(finalizer):
                self._deny("final provider request resolver is invalid")
            try:
                request = finalizer(request)
            except Exception as exc:  # noqa: BLE001 - resolver failure denies
                self._deny(f"final provider request could not be resolved: {exc}")
            if not isinstance(request, Mapping):
                self._deny("final provider request resolver returned a non-mapping")
        identity = self._identity(request, context)
        attempt_id = identity.attempt_id
        request_digest = self.core.request_hash(request)
        try:
            _envelope, new_attempt = self.envelopes.seal_or_assert_exact(identity, request)
        except Exception as exc:
            self._deny(f"late-bound provider request was not sealed: {exc}")
        if self.broker is not None:
            authorization = self.broker.authorization_decision(
                attempt_id=attempt_id,
                role_id=identity.role_id,
                subject_hashes={"request": request_digest},
                decision="ALLOW",
            )
            if new_attempt:
                begin = self.broker.begin_attempt(
                    attempt_id=attempt_id,
                    role_id=identity.role_id,
                    subject_hashes={"request": request_digest},
                )
                runtime_receipt = self.broker.runtime_identity(
                    attempt_id=attempt_id,
                    role_id=identity.role_id,
                    subject_hashes={"identity": self.core.sha256_json(identity.to_dict())},
                    exact_model_id=identity.exact_model_id,
                    provider=identity.provider,
                    endpoint_identity=identity.endpoint_identity,
                )
                assert self.ledger is not None
                self.ledger.append(begin, state="CANDIDATE", subject_id=f"attempt:{attempt_id}", expected_attempt_id=attempt_id)
                self.ledger.append(runtime_receipt, state="P1_TOOL_BACKED", subject_id=f"runtime:{attempt_id}", expected_attempt_id=attempt_id)
                self.ledger.append(authorization, state="P1_TOOL_BACKED", subject_id=f"authorization:{attempt_id}", expected_attempt_id=attempt_id)
        else:
            authorization = context.get("authorization_receipt") or context.get("integrity_authorization_receipt")
        authorization_receipt = self._verify(
            authorization,
            event_type="AUTHORIZATION_DECISION",
            attempt_id=attempt_id,
            subject=request_digest,
            role_id=identity.role_id,
        )
        # The provider is reached only after the synchronous authorization
        # check.  Downstream exceptions are not swallowed by this adapter.
        result = next_call(request)
        response_digest = self._result_hash(self.core, context.get("response_payload", result))
        request_id = context.get("api_request_id") or context.get("request_id")
        if self.broker is not None:
            call_receipt = self.broker.model_call(
                attempt_id=attempt_id,
                role_id=identity.role_id,
                subject_hashes={"request": request_digest, "response": response_digest},
                request_id=request_id,
            )
        else:
            call_receipt = context.get("model_call_receipt") or context.get("post_call_receipt")
        if call_receipt is None:
            self._deny("MODEL_CALL receipt is missing; provider result is not accepted")
        parsed = self._verify(
            call_receipt,
            event_type="MODEL_CALL",
            attempt_id=attempt_id,
            role_id=identity.role_id,
        )
        if parsed.subject_hashes.get("request") != request_digest:
            self._deny("MODEL_CALL receipt request binding mismatch")
        if parsed.subject_hashes.get("response") != response_digest:
            self._deny("MODEL_CALL receipt response binding mismatch")
        expected_request_id = parsed.context.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            self._deny("provider request id is missing")
        if expected_request_id != request_id:
            self._deny("MODEL_CALL receipt request-id binding mismatch")
        assert self.ledger is not None
        self.ledger.append(parsed, state="P1_TOOL_BACKED", subject_id=f"model:{attempt_id}", expected_attempt_id=attempt_id)
        self._evidence_state = "P1_TOOL_BACKED"
        return result

    def on_tool_execution(self, *, tool_name: str, args: Mapping[str, Any], next_call: Any, **context: Any) -> Any:
        if not self.active:
            self._deny(f"integrity adapter unavailable: {self.reason}")
        if not isinstance(tool_name, str) or not isinstance(args, Mapping):
            self._deny("tool identity or arguments are malformed")
        self._assert_scope_context(context)
        attempt_id = self._attempt_id(context)
        role_id = self._role_id(context)
        args_digest = self.core.sha256_json(args)
        if self.broker is not None:
            authorization = self.broker.authorization_decision(
                attempt_id=attempt_id,
                role_id=role_id,
                subject_hashes={"args": args_digest},
                tool_name=tool_name,
                decision="ALLOW",
            )
        else:
            authorization = context.get("authorization_receipt") or context.get("integrity_authorization_receipt")
        authorization_receipt = self._verify(
            authorization,
            event_type="AUTHORIZATION_DECISION",
            attempt_id=attempt_id,
            subject=args_digest,
            role_id=role_id,
            context={"tool_name": tool_name},
        )
        certificate_raw = self.config.get("capability_certificate")
        if certificate_raw is not None:
            try:
                certificate = self.core.CapabilityCertificate.from_mapping(certificate_raw)
                gate = self.core.CapabilityGate(
                    verifier=self.verifier,
                    tool_tiers=self.config.get("tool_tiers"),
                    policy_generation=self.config.get("policy_generation"),
                )
                decision = gate.admit(
                    tool_name=tool_name,
                    role_ceiling=self.config.get("role_ceiling", "C0"),
                    task_ceiling=self.config.get("task_ceiling", "C0"),
                    certificate=certificate,
                    evidence_state=self._evidence_state,
                    policy_generation=self.config.get("policy_generation"),
                    authorization_receipt=authorization_receipt,
                    attempt_id=attempt_id,
                )
                if not decision.allowed:
                    self._deny(f"capability/tool admission denied: {decision.reason}")
            except IntegrityMiddlewareDenied:
                raise
            except Exception as exc:  # noqa: BLE001 - capability is a protected boundary
                self._deny(f"capability/tool admission failed: {exc}")
        start_value = (
            self.broker.tool_start(
                attempt_id=attempt_id,
                role_id=role_id,
                subject_hashes={"args": args_digest},
                tool_name=tool_name,
            )
            if self.broker is not None else context.get("tool_start_receipt")
        )
        start = self._verify(
            start_value,
            event_type="TOOL_START",
            attempt_id=attempt_id,
            subject=args_digest,
            role_id=role_id,
            context={"tool_name": tool_name},
        )
        assert self.ledger is not None
        self.ledger.append(start, state="P1_TOOL_BACKED", subject_id=f"tool:{tool_name}:{attempt_id}", expected_attempt_id=attempt_id)
        result = next_call(args)
        result_digest = self._result_hash(self.core, context.get("result_payload", result))
        end_value = (
            self.broker.tool_end(
                attempt_id=attempt_id,
                role_id=role_id,
                subject_hashes={"args": args_digest, "result": result_digest},
                tool_name=tool_name,
            )
            if self.broker is not None else context.get("tool_end_receipt")
        )
        end = self._verify(
            end_value,
            event_type="TOOL_END",
            attempt_id=attempt_id,
            role_id=role_id,
            context={"tool_name": tool_name},
        )
        if end.subject_hashes.get("args") != args_digest:
            self._deny("TOOL_END receipt arguments binding mismatch")
        if end.subject_hashes.get("result") != result_digest:
            self._deny("TOOL_END receipt result binding mismatch")
        self.ledger.append(end, state="P1_TOOL_BACKED", subject_id=f"tool:{tool_name}:{attempt_id}", expected_attempt_id=attempt_id)
        return result


def _resolve_path(raw: Any, *, base: Path) -> Path:
    if not isinstance(raw, str) or not raw:
        raise IntegrityMiddlewareDenied("integrity path is missing")
    path = Path(raw).expanduser()
    return (base / path).resolve(strict=False) if not path.is_absolute() else path.resolve(strict=False)


def _profile_home() -> Path:
    """Resolve profile-owned paths through Hermes' canonical home helper."""

    return Path(get_hermes_home()).expanduser().resolve(strict=False)


def build_integrity_runtime(ctx: Any) -> HermesIntegrityRuntime | None:
    """Return a runtime only for explicit namespaced opt-in.

    Disabled-by-default contexts return ``None`` and do not register any
    behavior-changing middleware.  Enabled-but-broken contexts return an
    inactive fail-closed runtime so protected calls cannot accidentally pass.
    """

    try:
        enabled = ctx.get_config("integrity_enabled", False)
    except Exception:
        return None
    if enabled is not True:
        return None
    override = os.environ.pop("YATIMA_INTEGRITY_WORKER_CONFIG_JSON", "")
    if not override:
        try:
            if ctx.get_config("worker_bundle_enabled", False) is True:
                return None
        except Exception:
            return None
    try:
        override_config = json.loads(override) if override else None
    except json.JSONDecodeError:
        override_config = None
    config_raw = override_config if isinstance(override_config, Mapping) else ctx.get_config("integrity_config", None)
    config_path_raw = ctx.get_config("integrity_config_path", None)
    try:
        if isinstance(config_raw, Mapping):
            config = dict(config_raw)
            base = _profile_home()
        else:
            if not isinstance(config_path_raw, str) or not config_path_raw:
                return HermesIntegrityRuntime.inactive("integrity config path is missing")
            config_path = _resolve_path(config_path_raw, base=_profile_home())
            base = config_path.parent
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(config, Mapping):
                return HermesIntegrityRuntime.inactive("integrity config is not an object")
            config = dict(config)
        if config.get("enabled") is not True or config.get("trusted") is not True:
            return HermesIntegrityRuntime.inactive("integrity config is disabled or untrusted", config=config)
        core_path = _resolve_path(config.get("core_path"), base=base)
        issuer = _resolve_path(config.get("issuer_registry_path"), base=base)
        ledger = _resolve_path(config.get("ledger_path"), base=base)
        config["core_path"] = str(core_path)
        config["issuer_registry_path"] = str(issuer)
        config["ledger_path"] = str(ledger)
        config.setdefault("policy_generation", config.get("scope", {}).get("policy_generation"))
        core = _load_core(str(core_path))
        if not isinstance(config.get("scope"), Mapping):
            return HermesIntegrityRuntime.inactive("integrity scope is missing", core=core, config=config)
        # Use the same strict null-preserving scope compiler as the K3 repair.
        config["scope"] = core.serialize_scope(config["scope"])
        return HermesIntegrityRuntime(core=core, config=config)
    except IntegrityMiddlewareDenied as exc:
        return HermesIntegrityRuntime.inactive(str(exc), config=locals().get("config", {}))
    except Exception as exc:
        return HermesIntegrityRuntime.inactive(f"integrity adapter unavailable: {exc}", config=locals().get("config", {}))


def register_integrity_middleware(ctx: Any) -> HermesIntegrityRuntime | None:
    runtime = build_integrity_runtime(ctx)
    if runtime is None or not hasattr(ctx, "register_middleware"):
        return runtime

    def llm_callback(**kwargs: Any) -> Any:
        return runtime.on_llm_execution(**kwargs)

    def tool_callback(**kwargs: Any) -> Any:
        return runtime.on_tool_execution(**kwargs)

    llm_callback._integrity_fail_closed = True  # type: ignore[attr-defined]
    tool_callback._integrity_fail_closed = True  # type: ignore[attr-defined]
    ctx.register_middleware("llm_execution", llm_callback)
    ctx.register_middleware("tool_execution", tool_callback)
    return runtime


__all__ = [
    "IntegrityMiddlewareDenied",
    "HermesIntegrityRuntime",
    "build_integrity_runtime",
    "register_integrity_middleware",
]
