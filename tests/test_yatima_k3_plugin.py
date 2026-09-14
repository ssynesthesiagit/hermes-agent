import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

CORE_ROOT = Path("/home/ssynesthesia/Projects/Yatima-k3-universal-v1/shared/k3/python").resolve()
PLUGIN_ROOT = Path(__file__).parents[1] / "plugins" / "yatima-k3"


def _load_plugin_adapter():
    import sys

    spec = importlib.util.spec_from_file_location("yatima_k3_hermes_test_adapter", PLUGIN_ROOT / "adapter.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_plugin_package():
    import sys

    name = "hermes_plugin_yatima_k3_test_package"
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

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))


class YatimaK3PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys

        sys.path.insert(0, str(CORE_ROOT))
        from yatima_k3.fixtures import PUBLIC_FIXTURES

        cls.fixture = next(item for item in PUBLIC_FIXTURES if item.name == "ordinary-active-task")
        cls.result = cls.fixture.compile()
        cls.adapter_module = _load_plugin_adapter()
        cls.plugin_package = _load_plugin_package()

    def _settings(self, directory: Path, **overrides):
        capsule = directory / "capsule.json"
        receipt = directory / "receipts.json"
        cache = directory / "cache.json"
        capsule.write_text(json.dumps(self.result.capsule.as_dict()), encoding="utf-8")
        receipt.write_text(json.dumps(self.result.receipts.to_dict()), encoding="utf-8")
        cache.write_text(json.dumps(self.result.cache_binding.to_dict()), encoding="utf-8")
        settings = {
            "enabled": True,
            "core_path": str(CORE_ROOT),
            "capsule_path": str(capsule),
            "receipt_path": str(receipt),
            "cache_path": str(cache),
            "now": "2026-08-29T13:00:00Z",
            "scope": self.fixture.inputs.scope.to_dict(),
            "compiler_version": "yatima-k3-core-fixture-1",
            "schema_generation": "yatima.k3.v1",
        }
        settings.update(overrides)
        return settings

    def _live_scope(self, **overrides):
        values = dict(self.fixture.inputs.scope.to_dict())
        values.update(overrides)
        return values

    def test_plugin_is_inactive_without_explicit_opt_in(self):
        context = _Context({})
        self.adapter_module.register_plugin(context)
        self.assertEqual(context.hooks, [])
        context = _Context({"enabled": False})
        self.adapter_module.register_plugin(context)
        self.assertEqual(context.hooks, [])

    def test_source_locked_opt_in_registers_only_pre_llm_hook_and_injects_ephemeral_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = _Context(self._settings(Path(temporary)))
            self.plugin_package.register(context)
            self.assertEqual([name for name, _ in context.hooks], ["pre_llm_call"])
            history = [{"role": "user", "content": "owner text"}]
            name, callback = context.hooks[0]
            result = callback(**self._live_scope(), conversation_history=history)
            self.assertIsInstance(result, dict)
            self.assertIn("# Yatima K3 Task/Session Capsule", result["context"])
            self.assertEqual(history, [{"role": "user", "content": "owner text"}])

    def test_every_scope_binding_is_required_and_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            context = _Context(self._settings(directory))
            self.adapter_module.register_plugin(context)
            callback = context.hooks[0][1]
            valid = self._live_scope()
            self.assertIsNotNone(callback(**valid))
            for key in self.adapter_module._REQUIRED_SCOPE_FIELDS:
                missing = dict(valid)
                missing.pop(key)
                self.assertIsNone(callback(**missing), f"missing {key} must fail closed")
                mismatched = dict(valid)
                value = mismatched[key]
                if isinstance(value, bool):
                    mismatched[key] = not value
                elif value is None:
                    mismatched[key] = "different-stale-reason"
                else:
                    mismatched[key] = "different-binding"
                self.assertIsNone(callback(**mismatched), f"mismatched {key} must fail closed")

    def test_missing_or_wrong_turn_scope_and_stale_source_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            context = _Context(self._settings(directory))
            self.adapter_module.register_plugin(context)
            callback = context.hooks[0][1]
            self.assertIsNone(callback(**self._live_scope(session_id="wrong-session")))
            context = _Context(
                self._settings(directory, scope={**self.fixture.inputs.scope.to_dict(), "source_generation": "source-generation-002"})
            )
            self.adapter_module.register_plugin(context)
            self.assertIsNone(context.hooks[0][1](**self._live_scope(source_generation="source-generation-002")))
            context = _Context(self._settings(directory, now="2026-08-31T00:00:00Z"))
            self.adapter_module.register_plugin(context)
            self.assertIsNone(context.hooks[0][1](**self._live_scope()))

    def test_serialized_scope_preserves_stale_reason_null_and_discovery_registers_one_hook(self):
        """Regression for the installer null-dropping/no-hook failure."""

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            settings = self._settings(directory)
            installed = directory / "installed-k3.json"
            installed.write_text(json.dumps(settings), encoding="utf-8")
            loaded = json.loads(installed.read_text(encoding="utf-8"))
            self.assertIn("stale_reason", loaded["scope"])
            self.assertIsNone(loaded["scope"]["stale_reason"])
            context = _Context(loaded)
            self.plugin_package.register(context)
            self.assertEqual([name for name, _ in context.hooks], ["pre_llm_call"])
            callback = context.hooks[0][1]
            self.assertIsNotNone(callback(**self._live_scope()))
            for key, value in {
                "project_scope": "other-project",
                "role_scope": "other-role",
                "attempt_id": "other-attempt",
                "source_generation": "other-source",
                "cancelled": True,
            }.items():
                wrong = self._live_scope(**{key: value})
                self.assertIsNone(callback(**wrong), f"wrong {key} must fail closed")

    def test_bad_source_path_does_not_activate_and_manifest_is_opt_in(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = _Context(self._settings(Path(temporary), core_path="/explicitly/missing/k3/core"))
            self.adapter_module.register_plugin(context)
            self.assertEqual(context.hooks, [])
        manifest = (PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8")
        self.assertIn("pre_llm_call", manifest)
        self.assertIn("kind: standalone", manifest)

    def test_interactive_continuity_compiles_fresh_project_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            state_path = directory / "yatima.json"
            state_path.write_text(json.dumps({
                "schema_version": "yatima.k3.project-state.v1",
                "project_scope": "yatima",
                "privacy_class": "owner-private",
                "task_id": "continuity-audit",
                "source_revision": "fixture-v1",
                "observed_at": "2026-09-14T12:00:00Z",
                "records": [],
                "claim_events": [],
            }), encoding="utf-8")
            context = _Context({
                "enabled": True,
                "interactive_continuity_enabled": True,
                "core_path": str(CORE_ROOT),
                "project_state_path": str(state_path),
                "compiler_script": "/tmp/compile-k3-session.py",
                "python_executable": "/usr/bin/python3",
                "project_scope": "yatima",
                "profile_or_agent": "brain_omarchy",
                "host_id": "Swarmlord",
                "role_scope": "direct-hermes",
                "source_repository": "ssynesthesiagit/Yatima",
                "source_commit": "a" * 40,
            })

            def compile_result(*args, **kwargs):
                payload = json.loads(kwargs["input"])
                semantic_id = hashlib.sha256(payload["sourceGeneration"].encode()).hexdigest()
                marker = f"yatima-k3-view:{semantic_id}"
                return SimpleNamespace(
                    returncode=0,
                    stderr="",
                    stdout=json.dumps({
                        "status": "PASS",
                        "project_state_loaded": True,
                        "semantic_view_id": semantic_id,
                        "context_pack": {"admitted": True, "semantic_view_id": semantic_id},
                        "delivery_receipt": {
                            "semantic_view_id": semantic_id,
                            "retention_marker": marker,
                        },
                        "context_markdown": f"<!-- {marker} -->\n# Yatima K3 request-scoped continuity projection\n\ncurrent work",
                        "task_session_capsule": {
                            "project_scope": "yatima",
                            "profile_or_agent": "brain_omarchy",
                            "session_id": "fresh-session",
                            "source_generation": payload["sourceGeneration"],
                        },
                    }),
                )

            with patch.object(self.adapter_module.subprocess, "run", side_effect=compile_result) as run:
                self.adapter_module.register_plugin(context)
                self.assertEqual([name for name, _ in context.hooks], ["pre_llm_call"])
                callback = context.hooks[0][1]
                result = callback(
                    session_id="fresh-session",
                    task_id="task",
                    user_message="What work is underway?",
                    model="configured-model",
                    platform="cli",
                )
                self.assertIsNone(callback(
                    session_id="fresh-session",
                    user_message="What work is underway?",
                    conversation_history=[{"role": "user", "api_content": result["context"]}],
                ))
                recovered = callback(
                    session_id="fresh-session",
                    user_message="What work is underway?",
                    conversation_history=[{"role": "assistant", "content": "compacted summary"}],
                )
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["source_revision"] = "fixture-v2"
                state_path.write_text(json.dumps(state), encoding="utf-8")
                refreshed = callback(
                    session_id="fresh-session",
                    user_message="What changed?",
                )
            self.assertIn("current work", result["context"])
            self.assertIn("current work", recovered["context"])
            self.assertIn("current work", refreshed["context"])
            self.assertEqual(run.call_count, 3)
            payload = json.loads(run.call_args.kwargs["input"])
            self.assertEqual(payload["projectStatePath"], str(state_path))
            self.assertEqual(payload["clientKind"], "Hermes")

    def test_failed_compile_does_not_consume_recovery_opportunity(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            state_path = directory / "yatima.json"
            state_path.write_text(json.dumps({
                "schema_version": "yatima.k3.project-state.v1",
                "project_scope": "yatima",
            }), encoding="utf-8")
            settings = self._interactive_settings(state_path)
            context = _Context(settings)
            self.adapter_module.register_plugin(context)
            callback = context.hooks[0][1]
            semantic_id = "c" * 64
            failed = SimpleNamespace(returncode=1, stdout="", stderr="failed")
            passed = SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
                "status": "PASS",
                "project_state_loaded": True,
                "semantic_view_id": semantic_id,
                "context_pack": {"admitted": True, "semantic_view_id": semantic_id},
                "delivery_receipt": {
                    "semantic_view_id": semantic_id,
                    "retention_marker": f"yatima-k3-view:{semantic_id}",
                },
                "context_markdown": f"<!-- yatima-k3-view:{semantic_id} -->\nrecovered",
                "task_session_capsule": {
                    "project_scope": "yatima",
                    "profile_or_agent": "brain_omarchy",
                    "session_id": "session-failure",
                    "source_generation": hashlib.sha256(
                        f"yatima\0recover\0{hashlib.sha256(state_path.read_bytes()).hexdigest()}".encode()
                    ).hexdigest(),
                },
            }))
            with patch.object(self.adapter_module.subprocess, "run", side_effect=[failed, passed]) as run:
                self.assertIsNone(callback(session_id="session-failure", user_message="recover"))
                result = callback(session_id="session-failure", user_message="recover")
            self.assertIn("recovered", result["context"])
            self.assertEqual(run.call_count, 2)

    def _interactive_settings(self, state_path: Path, *, profile: str = "brain_omarchy"):
        return {
            "enabled": True,
            "interactive_continuity_enabled": True,
            "core_path": str(CORE_ROOT),
            "project_state_path": str(state_path),
            "compiler_script": "/tmp/compile-k3-session.py",
            "python_executable": "/usr/bin/python3",
            "project_scope": "yatima",
            "profile_or_agent": profile,
            "host_id": "Swarmlord",
            "role_scope": "direct-hermes",
            "source_repository": "ssynesthesiagit/Yatima",
            "source_commit": "a" * 40,
        }

    def test_interactive_continuity_wrong_project_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            state_path = directory / "other.json"
            state_path.write_text(json.dumps({
                "schema_version": "yatima.k3.project-state.v1",
                "project_scope": "other-project",
            }), encoding="utf-8")
            context = _Context({
                "enabled": True,
                "interactive_continuity_enabled": True,
                "core_path": str(CORE_ROOT),
                "project_state_path": str(state_path),
                "compiler_script": "/tmp/compile-k3-session.py",
                "python_executable": "/usr/bin/python3",
                "project_scope": "yatima",
                "profile_or_agent": "brain_omarchy",
                "host_id": "Swarmlord",
                "role_scope": "direct-hermes",
                "source_repository": "ssynesthesiagit/Yatima",
                "source_commit": "a" * 40,
            })
            with patch.object(self.adapter_module.subprocess, "run") as run:
                self.adapter_module.register_plugin(context)
                result = context.hooks[0][1](
                    session_id="fresh-session",
                    user_message="What work is underway?",
                )
            self.assertIsNone(result)
            run.assert_not_called()

    def test_interactive_continuity_rejects_symlinked_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / "target.json"
            target.write_text(json.dumps({
                "schema_version": "yatima.k3.project-state.v1",
                "project_scope": "yatima",
            }), encoding="utf-8")
            state_path = directory / "state.json"
            state_path.symlink_to(target)
            context = _Context(self._interactive_settings(state_path))
            with patch.object(self.adapter_module.subprocess, "run") as run:
                self.adapter_module.register_plugin(context)
                result = context.hooks[0][1](
                    session_id="symlink-session", user_message="recover"
                )
            self.assertIsNone(result)
            run.assert_not_called()

    def test_profile_runtimes_do_not_share_retention_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "yatima.json"
            state_path.write_text(json.dumps({
                "schema_version": "yatima.k3.project-state.v1",
                "project_scope": "yatima",
            }), encoding="utf-8")
            brain = _Context(self._interactive_settings(state_path, profile="brain_omarchy"))
            yatima = _Context(self._interactive_settings(state_path, profile="yatima-training"))
            self.adapter_module.register_plugin(brain)
            self.adapter_module.register_plugin(yatima)

            def compiled(*args, **kwargs):
                payload = json.loads(kwargs["input"])
                semantic_id = hashlib.sha256(payload["sourceGeneration"].encode()).hexdigest()
                marker = f"yatima-k3-view:{semantic_id}"
                return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
                    "status": "PASS",
                    "project_state_loaded": True,
                    "semantic_view_id": semantic_id,
                    "context_pack": {"admitted": True, "semantic_view_id": semantic_id},
                    "delivery_receipt": {"semantic_view_id": semantic_id, "retention_marker": marker},
                    "context_markdown": f"<!-- {marker} -->\nprofile={payload['clientInstanceId']}",
                    "task_session_capsule": {
                        "project_scope": "yatima",
                        "profile_or_agent": payload["clientInstanceId"],
                        "session_id": payload["sessionId"],
                        "source_generation": payload["sourceGeneration"],
                    },
                }))

            with patch.object(self.adapter_module.subprocess, "run", side_effect=compiled) as run:
                first = brain.hooks[0][1](session_id="shared", user_message="recover")
                second = yatima.hooks[0][1](session_id="shared", user_message="recover")
                self.assertIsNone(brain.hooks[0][1](
                    session_id="shared",
                    user_message="recover",
                    conversation_history=[{"api_content": first["context"]}],
                ))
                # Brain's retained marker must not suppress Yatima's runtime.
                third = yatima.hooks[0][1](session_id="shared", user_message="recover")
            self.assertIn("brain_omarchy", first["context"])
            self.assertIn("yatima-training", second["context"])
            self.assertIn("yatima-training", third["context"])
            self.assertEqual(run.call_count, 3)

    def test_worker_input_pack_is_hash_bound_and_replaces_full_capsule_render(self):
        import yatima_k3

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            worker = {
                "schema_version": "yatima.k3.worker-input.v1",
                "delegation_id": "delegate-1",
                "worker_id": "worker-a",
                "task_id": self.fixture.inputs.scope.task_id,
                "semantic_view_id": "d" * 64,
                "protected_core": {"owner_request": {"objective": "narrow task"}},
                "evidence": [],
                "authority_expanded": False,
            }
            worker["input_pack_hash"] = yatima_k3.sha256_json(worker)
            worker_path = directory / "worker-input.json"
            worker_path.write_text(json.dumps(worker), encoding="utf-8")
            context = _Context(self._settings(directory, worker_input_path=str(worker_path)))
            self.adapter_module.register_plugin(context)
            rendered = context.hooks[0][1](**self._live_scope())["context"]
            self.assertIn("narrow worker input pack", rendered)
            self.assertIn("narrow task", rendered)
            self.assertNotIn("Task/Session Capsule", rendered)
            worker["authority_expanded"] = True
            worker_path.write_text(json.dumps(worker), encoding="utf-8")
            self.assertIsNone(context.hooks[0][1](**self._live_scope()))


if __name__ == "__main__":
    unittest.main()
