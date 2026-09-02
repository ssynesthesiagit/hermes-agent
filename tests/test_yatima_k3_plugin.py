import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
