# Yatima K3 Hermes plugin

This plugin is inactive unless the namespaced plugin setting
`plugins.entries.yatima-k3.settings.enabled` is explicitly `true`. When
enabled, it imports the K3 core from the explicitly configured `core_path`,
reads the precompiled capsule/receipt/cache paths, validates the configured
project/role/profile/host/session/task/attempt/policy/source bindings and
expiry/cancellation state, then returns the core-rendered capsule from the
existing `pre_llm_call` seam. Hermes appends that return value to the
API-bound user message beside normal pruning; the plugin never changes the
system prompt or writes history, canonical memory, candidate memory, a DB, or
configuration.

The source root must be explicit and source-locked:

```yaml
plugins:
  entries:
    yatima-k3:
      settings:
        enabled: true
        core_path: /absolute/path/to/Yatima/shared/k3/python
        capsule_path: /absolute/path/to/capsule.json
        receipt_path: /absolute/path/to/receipts.json
        cache_path: /absolute/path/to/cache.json
        now: "2026-08-29T13:00:00Z"
        scope:
          project_scope: synthetic-project
          role_scope: owner-coder
          profile_or_agent: yatima
          host_id: synthetic-host
          session_id: session-001
          task_id: task-001
          attempt_id: attempt-001
          task_fingerprint: task-fingerprint-001
          policy_generation: policy-generation-001
          source_generation: source-generation-001
          privacy_class: synthetic-public
          authorization_state: AUTHORIZED
```

Rollback is reversible: omit the `enabled` setting (or set it to `false`) to
remove the hook, and remove only the disposable precompiled artifact/cache
files when their owner no longer needs them. No normal Codex config or live
Hermes profile is modified by this plugin package.
