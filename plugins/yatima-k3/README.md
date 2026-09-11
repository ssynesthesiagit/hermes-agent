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
          task_current: true
          attempt_current: true
          cancelled: false
          superseded: false
          stale_reason: null
```

Rollback is reversible: omit the `enabled` setting (or set it to `false`) to
remove the hook, and remove only the disposable precompiled artifact/cache
files when their owner no longer needs them. No normal Codex config or live
Hermes profile is modified by this plugin package.

## Crystal passive shadow v1 (separate opt-in)

The K3 hook can call the source-locked Crystal matcher only after the existing
capsule transport, scope, currentness, compiler and rendering checks succeed.
The default is off: omit `shadow` or set only `shadow: {mode: off}`. In that
state Hermes does not import the observer, read a catalog, open or create a
sink, or add work to the callback.

There is no `inject` mode. Unknown modes, extra keys, weak types and incomplete
shadow settings are rejected to an inert `CONFIG_REJECTED` state while the
accepted K3 hook continues unchanged. A shadow setup/runtime/sink failure is
also non-model-visible and cannot replace or alter K3's normal hook result.

A future separately reviewed shadow-only activation must supply every field:

```yaml
shadow:
  mode: shadow
  core_path: /absolute/source-locked/Yatima/shared/crystals/k3_shadow_v1/python
  core_digest: exact-64-character-package-source-digest
  catalog_path: /absolute/private-diagnostic/catalog.json
  catalog_root: /absolute/private-diagnostic
  sink_path: /absolute/private-diagnostic/observations.jsonl
  sink_root: /absolute/private-diagnostic
  policy:
    mode: shadow
    generation: approved-shadow-policy-generation
    catalog_generation: approved-shadow-catalog-generation
    expires_at: "owner-approved-expiry-with-timezone"
    allowed_task_families: [owner_preflight]
    max_candidates: 32
    max_content_bytes: 8192
  task_family: owner_preflight
  trusted_facts:
    work_kind: implementation
    repository_state: isolated-worktree
    evidence_state: open
  fact_snapshot_generation: approved-fact-snapshot-generation
  fact_snapshot_valid_from: "owner-approved-start-with-timezone"
  fact_snapshot_expires_at: "owner-approved-expiry-with-timezone"
  fact_snapshot_binding:
    project_scope: synthetic-project
    role_scope: owner-coder
    profile_or_agent: yatima
    host_id: synthetic-host
    session_id: session-001
    task_id: task-001
    attempt_id: attempt-001
    task_fingerprint: task-fingerprint-001
    source_generation: source-generation-001
    privacy_class: synthetic-public
    k3_digest: exact-validated-capsule-hash
    capsule_generation: exact-validated-capsule-id
  max_sink_events: 256
  max_sink_bytes: 1048576
```

`task_family` and `trusted_facts` are an immutable, validity-bounded host fact
snapshot bound to the exact K3 task, attempt and capsule. The observer uses the
current host clock on every callback; there is no live-facing fixed
`observed_at` setting. A policy, catalog, admission or fact change requires a
new generation and observer rebuild. The observer never examines
`user_message` or `conversation_history`, never calls `memory_seed` again, and
never returns its decision to the hook. Its request-path sink only makes a
bounded non-blocking in-memory handoff. A caller may explicitly invoke
`runtime.drain_shadow_diagnostics()` outside the request path to persist and
deduplicate JSONL receipts. Buffered, persisted, duplicate, dropped,
contended, saturated and degraded outcomes remain distinct. Candidate
procedure text is excluded. Disable by returning to
`mode: off`; disabling does not delete evidence. Installation and activation
remain a separate owner gate and were not performed with this source change.

The exact R1 source and package digest are pinned in the companion Yatima
publication evidence. Those values identify source only; they do not activate
or approve a catalog.

## Runtime Integrity V1.1 (opt-in)

The same package can register the portable Runtime Integrity adapter when the
separate namespaced `integrity_enabled` setting is explicitly `true` and its
config is both `enabled: true` and `trusted: true`. The adapter resolves
relative state paths through Hermes' `get_hermes_home()` profile, reads only a
public issuer registry, and opens an existing ledger; missing, corrupt, or
untrusted state installs a synchronous deny callback rather than creating a
new trust anchor. Private keys and arbitrary signing are never exposed by the
plugin.

The execution callbacks require host-issued authorization, tool start/end,
and model-call receipts bound to the final request/result and request ID.
They do not modify the system prompt or prior history and do not disable
prompt-cache decoration. Enable it only after provisioning one profile-owned
config, issuer registry, ledger, and scope. Set `integrity_enabled: false` or
remove the setting to roll back; restore the profile's installer backup before
deleting state. Codex hosted and specialized tool paths remain explicitly
uncovered and must be quarantined by the repo-local Codex adapter.

### Dispatcher worker mode

For bounded Kanban workers, set `worker_bundle_enabled: true` together with
an explicit `integrity_core_path` and `integrity_state_root`. The existing
Hermes dispatcher then compiles a fresh K3 capsule from the immutable task
body and current run identity before spawning the worker. It passes the
public artifact/config bundle through one-shot environment values that the
plugin consumes and removes during startup.

In this mode `receipt_mode: host_observed` is selected automatically. The
constrained plugin process generates an ephemeral component-specific
Ed25519 key in memory and exposes no signer to the model or its tools. Public
issuer identity is appended to the profile registry; authenticated attempt,
runtime, authorization, model-call, and tool receipts are appended to the
existing SQLite WAL ledger. No private key is written to disk. Missing K3
source, ledger, runtime identity, request ID, or current task/run binding
fails before provider/tool execution.

The worker bundle does not dispatch anything. The existing Kanban claim,
one-slot guard, assignee profile, and owner envelope remain authoritative.
