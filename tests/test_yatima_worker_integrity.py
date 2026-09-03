"""Integrated worker-bundle and host-observed receipt tests."""

from __future__ import annotations

import json
import importlib
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import tempfile
import unittest

from hermes_cli.yatima_worker_integrity import prepare_worker_security


YATIMA_ROOT = Path("/home/ssynesthesia/Projects/Yatima-k3-fleet-runtime-integrity-v1-1-20260901")
K3_CORE = YATIMA_ROOT / "shared" / "k3" / "python"
INTEGRITY_CORE = YATIMA_ROOT / "shared" / "runtime-integrity" / "python"
PLUGIN_ROOT = Path(__file__).parents[1] / "plugins" / "yatima-k3"


def _load_integrity_module():
    name = "hermes_plugin_yatima_worker_integrity_test_package"
    spec = importlib.util.spec_from_file_location(
        name,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    package = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = package
    spec.loader.exec_module(package)
    return importlib.import_module(f"{name}.integrity_adapter")


class _Context:
    def get_config(self, key, default=None):
        return True if key == "integrity_enabled" else default


class YatimaWorkerIntegrityTests(unittest.TestCase):
    def _task(self):
        model_id = "current-local-test"
        provider = "current-local-provider"
        return SimpleNamespace(
            id="t_owner_canary",
            title="Owner integrity canary",
            body=json.dumps(
                {
                    "goal": "Read the integrated status and make no changes.",
                    "doneWhen": "Return the active project, blocker, and next action.",
                    "constraints": ["read only", "no follow-on jobs"],
                    "writeAuthority": "READ_ONLY",
                    "project": "yatima",
                    "ownerEnvelopeVersion": 1,
                    "runtimeBinding": {
                        "schemaVersion": "CURRENT_LOCAL_RUNTIME_BINDING_V1",
                        "selected": {
                            "routeId": model_id,
                            "modelId": model_id,
                            "hermesProvider": provider,
                            "endpoint": "http://127.0.0.1:11999/v1",
                            "parallelSlots": 1,
                            "processing": False,
                        },
                        "activeRoutes": [],
                    },
                }
            ),
            project_id="yatima",
            current_run_id=1,
            max_runtime_seconds=900,
            model_override=model_id,
            provider_override=provider,
            created_by="owner-command-center",
        )

    def test_bundle_is_task_scoped_and_host_observed_receipts_are_real(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_home = root / "profile"
            profile_home.mkdir()
            env = {"HERMES_HOME": str(profile_home)}
            prepared = prepare_worker_security(
                task=self._task(),
                profile="brain_omarchy",
                env=env,
                board="yatima-owner-dispatch",
                settings={
                    "worker_bundle_enabled": True,
                    "core_path": str(K3_CORE),
                    "integrity_core_path": str(INTEGRITY_CORE),
                    "integrity_state_root": str(root / "integrity-state"),
                    "policy_generation": "yatima-runtime-integrity-v1.1",
                    "source_commit": "test-source",
                },
            )
            self.assertTrue(prepared["activated"])
            self.assertFalse(prepared["private_key_material_persisted"])
            k3_settings = json.loads(env["YATIMA_K3_WORKER_SETTINGS_JSON"])
            self.assertEqual(k3_settings["scope"]["task_id"], "t_owner_canary")
            self.assertEqual(k3_settings["scope"]["attempt_id"], "t_owner_canary:run:1")
            self.assertEqual(k3_settings["context_required_fields"], ["task_id"])
            integrity_config = json.loads(env["YATIMA_INTEGRITY_WORKER_CONFIG_JSON"])
            self.assertEqual(integrity_config["task_ceiling"], "C1")
            self.assertEqual(
                integrity_config["capability_certificate"]["exact_model_id"],
                "current-local-test",
            )
            self.assertIn("read_file", integrity_config["capability_certificate"]["allowed_tools"])
            self.assertNotIn("terminal", integrity_config["capability_certificate"]["allowed_tools"])
            capability = json.loads(Path(prepared["capability_path"]).read_text(encoding="utf-8"))
            self.assertEqual(capability, prepared["capability_certificate"])
            self.assertEqual(capability["tier"], "C1")
            self.assertNotIn("private_key", json.dumps(capability))
            capsule = json.loads(Path(k3_settings["capsule_path"]).read_text(encoding="utf-8"))
            self.assertEqual(capsule["owner_request"]["objective"], "Read the integrated status and make no changes.")

            os.environ["YATIMA_INTEGRITY_WORKER_CONFIG_JSON"] = env[
                "YATIMA_INTEGRITY_WORKER_CONFIG_JSON"
            ]
            integrity_adapter = _load_integrity_module()

            runtime = integrity_adapter.build_integrity_runtime(_Context())
            self.assertIsNotNone(runtime)
            self.assertTrue(runtime.active)
            self.assertIsNotNone(runtime.broker)
            self.assertNotIn("YATIMA_INTEGRITY_WORKER_CONFIG_JSON", os.environ)

            request = {"model": "current-local-test", "input": "hello"}
            response = SimpleNamespace(
                id="response-1",
                model="current-local-test",
                choices=[SimpleNamespace(index=0, message=SimpleNamespace(content="ok"))],
            )
            value = runtime.on_llm_execution(
                request=request,
                next_call=lambda final: response,
                task_id="t_owner_canary",
                session_id="hermes-session-1",
                api_request_id="api-request-1",
                provider="provider-a",
                model="current-local-test",
                base_url="http://model.invalid/v1",
            )
            self.assertIs(value, response)
            retry_value = runtime.on_llm_execution(
                request=request,
                next_call=lambda final: {"id": "response-2", "output": "retry-ok"},
                task_id="t_owner_canary",
                session_id="hermes-session-1",
                api_request_id="api-request-1",
                provider="provider-a",
                model="current-local-test",
                base_url="http://model.invalid/v1",
            )
            self.assertEqual(retry_value["output"], "retry-ok")
            with self.assertRaises(integrity_adapter.IntegrityMiddlewareDenied):
                runtime.on_tool_execution(
                    tool_name="terminal",
                    args={"command": "touch forbidden"},
                    next_call=lambda args: self.fail("read-only terminal must not run"),
                    task_id="t_owner_canary",
                    session_id="hermes-session-1",
                    api_request_id="api-request-1",
                )
            tool_value = runtime.on_tool_execution(
                tool_name="file_read",
                args={"path": "README.md"},
                next_call=lambda args: {"ok": True},
                task_id="t_owner_canary",
                session_id="hermes-session-1",
                api_request_id="api-request-1",
            )
            self.assertEqual(tool_value, {"ok": True})
            event_types = [event.receipt.event_type for event in runtime.ledger.events()]
            self.assertEqual(
                event_types,
                [
                    "BEGIN_ATTEMPT",
                    "RUNTIME_IDENTITY",
                    "AUTHORIZATION_DECISION",
                    "MODEL_CALL",
                    "MODEL_CALL",
                    "TOOL_START",
                    "TOOL_END",
                ],
            )
            registry = json.loads(Path(prepared["registry_path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(registry["issuers"]), 1)
            self.assertNotIn("private_key", json.dumps(registry))

    def test_owner_runtime_binding_mismatch_fails_before_worker_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_home = root / "profile"
            profile_home.mkdir()
            task = self._task()
            task.model_override = "different-model"
            with self.assertRaisesRegex(Exception, "runtime binding does not match"):
                prepare_worker_security(
                    task=task,
                    profile="brain_omarchy",
                    env={"HERMES_HOME": str(profile_home)},
                    board="yatima-owner-dispatch",
                    settings={
                        "worker_bundle_enabled": True,
                        "core_path": str(K3_CORE),
                        "integrity_core_path": str(INTEGRITY_CORE),
                        "integrity_state_root": str(root / "integrity-state"),
                    },
                )
            self.assertFalse((profile_home / "k3").exists())

    def test_worker_mode_fails_closed_without_request_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_home = root / "profile"
            profile_home.mkdir()
            env = {"HERMES_HOME": str(profile_home)}
            prepare_worker_security(
                task=self._task(), profile="brain_omarchy", env=env,
                board="yatima-owner-dispatch",
                settings={
                    "worker_bundle_enabled": True,
                    "core_path": str(K3_CORE),
                    "integrity_core_path": str(INTEGRITY_CORE),
                    "integrity_state_root": str(root / "integrity-state"),
                },
            )
            os.environ["YATIMA_INTEGRITY_WORKER_CONFIG_JSON"] = env[
                "YATIMA_INTEGRITY_WORKER_CONFIG_JSON"
            ]
            integrity_adapter = _load_integrity_module()

            runtime = integrity_adapter.build_integrity_runtime(_Context())
            with self.assertRaises(integrity_adapter.IntegrityMiddlewareDenied):
                runtime.on_llm_execution(
                    request={"model": "model-a"},
                    next_call=lambda request: self.fail("provider must not run"),
                    task_id="t_owner_canary",
                    session_id="hermes-session-1",
                    provider="provider-a",
                    model="model-a",
                )

    def test_isolated_write_envelope_reaches_c1_only_after_verified_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_home = root / "profile"
            profile_home.mkdir()
            task = self._task()
            body = json.loads(task.body)
            body["writeAuthority"] = "REPOSITORY_WRITES_ISOLATED"
            task.body = json.dumps(body)
            env = {"HERMES_HOME": str(profile_home)}
            prepare_worker_security(
                task=task,
                profile="brain_omarchy",
                env=env,
                board="yatima-owner-dispatch",
                settings={
                    "worker_bundle_enabled": True,
                    "core_path": str(K3_CORE),
                    "integrity_core_path": str(INTEGRITY_CORE),
                    "integrity_state_root": str(root / "integrity-state"),
                },
            )
            config = json.loads(env["YATIMA_INTEGRITY_WORKER_CONFIG_JSON"])
            self.assertEqual(config["task_ceiling"], "C1")
            self.assertIn("terminal", config["capability_certificate"]["allowed_tools"])
            os.environ["YATIMA_INTEGRITY_WORKER_CONFIG_JSON"] = env[
                "YATIMA_INTEGRITY_WORKER_CONFIG_JSON"
            ]
            runtime = _load_integrity_module().build_integrity_runtime(_Context())
            with self.assertRaises(Exception):
                runtime.on_tool_execution(
                    tool_name="terminal", args={"command": "true"},
                    next_call=lambda args: self.fail("C1 requires a verified model call first"),
                    task_id="t_owner_canary", session_id="hermes-session-1",
                    api_request_id="api-request-write",
                )
            runtime.on_llm_execution(
                request={"model": "current-local-test", "input": "write task"},
                next_call=lambda request: {"id": "response-write", "output": "ok"},
                task_id="t_owner_canary", session_id="hermes-session-1",
                api_request_id="api-request-write", provider="custom",
                model="current-local-test", base_url="http://127.0.0.1:11999/v1",
            )
            value = runtime.on_tool_execution(
                tool_name="terminal", args={"command": "true"},
                next_call=lambda args: {"exit_code": 0},
                task_id="t_owner_canary", session_id="hermes-session-1",
                api_request_id="api-request-write",
            )
            self.assertEqual(value, {"exit_code": 0})


if __name__ == "__main__":
    unittest.main()
