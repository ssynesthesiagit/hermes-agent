# Incident: restored session tile render loop on Linux

## Summary

- Date/time and timezone: 2026-08-29, America/Detroit
- Affected platform/gateway: packaged Electron desktop on Omarchy Linux
- User-visible symptom: a restored session tile fell into its contribution
  error boundary with `Maximum update depth exceeded`; Retry reproduced it.
- Severity and current status: every newly opened Bot session tile could become
  unusable; fixed and verified against three preserved tiles in a local package
  without deleting or rewriting session data.

## Baseline

- Repository/branch/commit: `codex/yatima-custom` at
  `4e526b0954e97623d845ac563294fcf89a970dcc`
- Installed version or package: Hermes desktop v0.17.0, locally packaged Linux
  Electron build
- Dirty owner files present: none before the repair
- Service/task and listener state: desktop plus its loopback `hermes serve`
  backend were active
- Last known verified checkpoint: clean Linux package from the baseline commit

## Reproduction and evidence

1. Open a preserved session as a pane tile in the packaged desktop.
2. Expected result: the tile renders its existing transcript and remains
   responsive.
3. Actual result: React repeatedly committed the assistant runtime boundary
   until its maximum-update-depth guard fired and the contribution error
   boundary replaced the tile.
4. Bounded redacted error:

   ```text
   [error-boundary:contrib:session-tile:<redacted>]
   Maximum update depth exceeded. The result of getSnapshot should be cached
   to avoid an infinite loop.
   ```

5. A first repair that memoized the external-store adapter was insufficient: a
   new tile (`20260829_052917_4156cc`) reproduced the failure, proving that the
   problem was not corrupt transcript data or a stale package.
6. A bounded live diagnostic compared consecutive snapshots at each
   assistant-ui subscription boundary. `useAuiState` and per-thread runtime
   selectors were stable; the thread-list resource returned a different object
   for two reads with identical observable fields.
7. After the final repair, the packaged app switched across three preserved Bot
   tiles (including both reported failures) with zero error-boundary hits, zero
   update-depth errors, and one stable skin state per tile.

## Root cause

The first trigger was adapter identity churn in `ChatRuntimeBoundary`; memoizing
that adapter removed needless adapter synchronization but did not remove the
failing snapshot.

The decisive fault was assistant-ui's public `ThreadListRuntime` snapshot. Its
lazy subject intentionally rebuilds a wrapper while it has no subscriber. When
stacked Bot tiles mount during the Bots workspace/profile-skin handoff, React can
read that snapshot twice before subscription. Both wrappers describe the same
thread-list state but have different identities, violating
`useSyncExternalStore`'s cached-snapshot contract. `UseTapEffects` then commits,
notifies the resource, and closes the render/notify loop. The visible white ↔
green skin flashing was the surrounding workspace handoff repeatedly repainting
while that loop was active; it was not corrupt theme data.

## Change

- Files changed: `apps/desktop/src/app/chat/index.tsx`,
  `apps/desktop/src/lib/incremental-external-store-runtime.ts`,
  `apps/desktop/src/lib/incremental-runtime-adapter-notify.test.ts`, and this
  incident record
- Runtime/configuration changed: none
- Security or compatibility implications: none; submission, cancellation,
  editing, reload, branch visibility, and message-repository behavior are
  unchanged
- Owner changes preserved: session/profile databases and desktop state were
  not deleted, reset, or rewritten

The chat runtime adapter is memoized from the values the runtime consumes. The
custom runtime also wraps the public thread-list getter with shallow snapshot
memoization: it still reads the source every time (so disconnected updates are
observed), but reuses the previous object when every observable field is
unchanged.

## Verification

- Focused tests: incremental adapter notification/snapshot, runtime repository,
  and transcript window suites — 29 passed
- Integration/build: desktop/electron/e2e TypeScript checks passed; Linux
  Electron directory package completed successfully
- Rendered smoke test: the rebuilt real Electron application launched with the
  restored Bots layout and affected profile backend. Automated real-window
  interaction opened `api_1787068979_4eef9392`, `20260829_050513_9587b1`, and
  `20260829_052917_4156cc`; all three rendered, each retained one skin state
  during the observation window, and no matching renderer error was emitted.
- Cross-platform check: not required for this Linux-only package acceptance;
  source remains shared and TypeScript checks cover all desktop targets
- Result and remaining limitations: the render loop and white/green flashing no
  longer reproduce in the packaged Linux app; owner interaction remains the
  final acceptance gate

## Deployment

- Staging artifact/source: local dirty build from the baseline commit plus the
  three source/test-file repair and this incident record
- First machine result: Omarchy Linux package launched successfully
- Second machine result: not deployed
- Android APK required: no; neither native Android nor the hosted DOM contract
  changed

## Recovery

- Backup/rollback reference: the clean baseline commit and the previous local
  package remain the rollback references
- Exact rollback steps: stop the desktop, restore the clean baseline source or
  previous package, then relaunch; do not alter session/profile databases
- Post-rollback health check: confirm desktop startup and the loopback backend,
  then reopen a normal non-tiled session

## Repository action

- Commit: not created
- Push/remote branch: not performed
- Documentation updated: this incident record
