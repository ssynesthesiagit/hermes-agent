"""Real-path behavior tests for the opt-in Hermes integrity adapter."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


YATIMA_ROOT = Path("/home/ssynesthesia/Projects/Yatima-k3-fleet-runtime-integrity-v1-1-20260901")
CORE_ROOT = YATIMA_ROOT / "shared" / "runtime-integrity" / "python"
PLUGIN_ROOT = Path(__file__).parents[1] / "plugins" / "yatima-k3"


def _load_package():
    import sys

    name = "hermes_plugin_yatima_integrity_test_package"
    spec = importlib.util.spec_from_file_location(
        name,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Context:
    def __init__(self, settings):
        self.settings = settings
        self.hooks = []
        self.middleware = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def register_middleware(self, kind, callback):
        self.middleware.append((kind, callback))


def _scope():
    return {
        "project_scope": "yatima",
        "role_scope": "role",
        "profile_or_agent": "profile",
        "host_id": "hermes-test-host",
        "session_id": "session-1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "task_fingerprint": "f" * 64,
        "policy_generation": "policy-v1",
        "source_generation": "source-v1",
        "privacy_class": "private",
        "authorization_state": "authorized",
        "task_current": True,
        "attempt_current": True,
        "cancelled": False,
        "superseded": False,
        "stale_reason": None,
    }


class YatimaIntegrityAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = _load_package()
        import sys

        sys.path.insert(0, str(CORE_ROOT))
        import yatima_runtime_integrity as core

        cls.core = core

    def _environment(self, directory: Path):
        executor = self.core.Ed25519ReceiptBroker.generate(
            issuer_id="executor", issuer_class="EXECUTOR", policy_generation="policy-v1"
        )
        registry = directory / "issuers.json"
        registry.write_text(json.dumps({"issuers": [executor.public_issuer().to_dict()]}), encoding="utf-8")
        ledger = self.core.IntegrityLedger.initialize(directory / "ledger.sqlite3")
        ledger.close()
        config = directory / "runtime-integrity.json"
        config.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "trusted": True,
                    "core_path": str(CORE_ROOT),
                    "issuer_registry_path": str(registry),
                    "ledger_path": str(directory / "ledger.sqlite3"),
                    "policy_generation": "policy-v1",
                    "runtime_identity": {"capability_certificate": "cert-1"},
                    "scope": _scope(),
                }
            ),
            encoding="utf-8",
        )
        settings = {"integrity_enabled": True, "integrity_config_path": str(config)}
        return settings, executor

    def _context(self, directory: Path):
        settings, executor = self._environment(directory)
        context = _Context(settings)
        self.package.register(context)
        return context, executor

    def test_integrity_disabled_by_default_does_not_register_middleware(self):
        context = _Context({})
        self.package.register(context)
        self.assertEqual(context.middleware, [])
        self.assertEqual(context.hooks, [])

    def test_missing_config_is_not_activated_but_explicit_enable_registers_fail_closed_callbacks(self):
        context = _Context({"integrity_enabled": True, "integrity_config_path": "/missing/runtime-integrity.json"})
        self.package.register(context)
        self.assertEqual([kind for kind, _ in context.middleware], ["llm_execution", "tool_execution"])
        with self.assertRaises(Exception):
            context.middleware[0][1](request={"model": "m"}, next_call=lambda _: self.fail("provider must not run"))

    def test_final_llm_request_is_bound_and_authenticated_before_provider_and_after_result(self):
        with tempfile.TemporaryDirectory() as directory:
            context, executor = self._context(Path(directory))
            llm = next(callback for kind, callback in context.middleware if kind == "llm_execution")
            request = {"model": "model-a", "input": "hello"}
            request_hash = self.core.request_hash(request)
            response = {"id": "response-1", "output": "ok"}
            response_hash = self.core.sha256_json(response)
            auth = executor.authorization_decision(attempt_id="attempt-1", role_id="role", subject_hashes={"request": request_hash})
            model_receipt = executor.model_call(
                attempt_id="attempt-1", role_id="role",
                subject_hashes={"request": request_hash, "response": response_hash},
                request_id="api-request-1",
            )
            calls = []
            result = llm(
                request=request,
                next_call=lambda value: calls.append(value) or response,
                authorization_receipt=auth.to_dict(),
                model_call_receipt=model_receipt.to_dict(),
                api_request_id="api-request-1",
                provider="provider",
                model="model-a",
                host_id="hermes-test-host",
                capability_certificate="cert-1",
                session_id="session-1",
                attempt_id="attempt-1",
            )
            self.assertEqual(result, response)
            self.assertEqual(calls, [request])

    def test_missing_or_rewritten_identity_receipt_denies_before_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            context, executor = self._context(Path(directory))
            llm = next(callback for kind, callback in context.middleware if kind == "llm_execution")
            request = {"model": "model-a", "input": "hello"}
            auth = executor.authorization_decision(attempt_id="attempt-1", role_id="role", subject_hashes={"request": self.core.request_hash(request)})
            called = []
            with self.assertRaises(Exception):
                llm(
                    request=request,
                    next_call=lambda value: called.append(value),
                    authorization_receipt=auth.to_dict(),
                    final_request_hash=self.core.request_hash({"model": "model-b", "input": "hello"}),
                    provider="provider",
                    model="model-a",
                    host_id="hermes-test-host",
                    capability_certificate="cert-1",
                    session_id="session-1",
                    attempt_id="attempt-1",
                )
            self.assertEqual(called, [])

    def test_tool_start_end_receipts_are_required_and_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            context, executor = self._context(Path(directory))
            tool = next(callback for kind, callback in context.middleware if kind == "tool_execution")
            args = {"path": "safe.txt"}
            args_hash = self.core.sha256_json(args)
            auth = executor.authorization_decision(attempt_id="attempt-1", role_id="role", subject_hashes={"args": args_hash}, tool_name="file_read")
            start = executor.tool_start(attempt_id="attempt-1", role_id="role", subject_hashes={"args": args_hash}, tool_name="file_read")
            result = {"ok": True}
            end = executor.tool_end(attempt_id="attempt-1", role_id="role", subject_hashes={"args": args_hash, "result": self.core.sha256_json(result)}, tool_name="file_read")
            value = tool(
                tool_name="file_read",
                args=args,
                next_call=lambda _: result,
                authorization_receipt=auth.to_dict(),
                tool_start_receipt=start.to_dict(),
                tool_end_receipt=end.to_dict(),
                session_id="session-1",
                attempt_id="attempt-1",
                role_id="role",
            )
            self.assertEqual(value, result)
            with self.assertRaises(Exception):
                tool(tool_name="file_read", args=args, next_call=lambda _: self.fail("tool must not run"), authorization_receipt=auth.to_dict(), tool_start_receipt=None, tool_end_receipt=end.to_dict(), attempt_id="attempt-1", role_id="role")

    def test_tool_capability_certificate_clamps_execution_before_the_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            settings, executor = self._environment(directory_path)
            config_path = Path(settings["integrity_config_path"])
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({
                "role_ceiling": "C0",
                "task_ceiling": "C0",
                "capability_certificate": {
                    "certificate_id": "cert-1",
                    "role_id": "role",
                    "tier": "C0",
                    "policy_generation": "policy-v1",
                    "allowed_tools": ["file_read"],
                    "revoked": False,
                },
                "tool_tiers": {"file_read": "C1"},
            })
            config_path.write_text(json.dumps(config), encoding="utf-8")
            context = _Context(settings)
            self.package.register(context)
            tool = next(callback for kind, callback in context.middleware if kind == "tool_execution")
            args = {"path": "safe.txt"}
            args_hash = self.core.sha256_json(args)
            auth = executor.authorization_decision(
                attempt_id="attempt-1", role_id="role", subject_hashes={"args": args_hash}, tool_name="file_read"
            )
            with self.assertRaises(Exception):
                tool(
                    tool_name="file_read", args=args,
                    next_call=lambda _: self.fail("clamped tool must not run"),
                    authorization_receipt=auth.to_dict(),
                    tool_start_receipt=executor.tool_start(
                        attempt_id="attempt-1", role_id="role", subject_hashes={"args": args_hash}, tool_name="file_read"
                    ).to_dict(),
                    tool_end_receipt=None,
                    attempt_id="attempt-1", role_id="role", session_id="session-1",
                )

    def test_fail_closed_chain_does_not_swallow_integrity_callback_or_unrelated_middleware_exception(self):
        from hermes_cli.middleware import _run_execution_chain

        def integrity_callback(**kwargs):
            raise RuntimeError("integrity unavailable")

        integrity_callback._integrity_fail_closed = True
        with self.assertRaises(RuntimeError):
            _run_execution_chain("llm_execution", [integrity_callback], lambda _: self.fail("terminal reached"), request={"x": 1})


if __name__ == "__main__":
    unittest.main()
