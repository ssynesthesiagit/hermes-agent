# Hermes v0.21.0 Yatima integration record

## Status and scope

- **Date:** 2026-09-02 (America/Detroit).
- **Branch:** `codex/yatima-v0.21-integration`.
- **Yatima input:** `7bad5810a65b91c89304731fe08d7ed2acbe6daa`.
- **Official release input:**
  `29112bef099274229cadff79cdff7bf7b99c4b77` (Hermes v0.21.0).
- **Release merge checkpoint:** `0218697ad`.
- **Verified code checkpoint before this record:** `8d2f55841`.
- **Deployment state:** source-only. No live gateway, desktop installation,
  profile, credential, session database, or Android installation was changed or
  restarted.

The integration was performed in a separate Git worktree. The original Yatima
worktree and its owner-created untracked files were not modified.

## Retained Yatima capabilities

- Native Android client and Tailscale-only gateway policy.
- Append-only candidate-memory shadow plane.
- Bounded same-machine `a2a_call(agent="profile:<name>")` routing.
- Direct `@bot@machine` peer addresses, exact per-agent endpoints and
  credentials, private key-file input, and all-profile route propagation.
- Hyprland native-Wayland selection for computer-use capture.
- Session render-loop stabilization and compression-ID rotation handling.
- Secondary native Bot Chat windows with early cold-transcript paint.

Upstream replaced the legacy monolithic Bot Mode plugin with modular
TypeScript/TSX. The obsolete plugin and slice-based tests were removed. The
Yatima native-window action and its source/profile identity invariants were
ported into `bot-row.tsx` and `canonical-chat.ts` with native modular tests.

## Adopted upstream behavior

- Official v0.21.0 Bot Mode, cross-gateway Group Chat, canonical-chat registry,
  gateway/profile isolation, session recovery, and compression improvements.
- Redirect-safe authenticated peer requests plus asynchronous peer
  `run`, `status`, and `stop` operations.
- GitSpawn RCE defense, including the prerequisite hardened internal Git
  environment.
- Single-use OAuth grant isolation and healing across profiles, concurrent Nous
  401 recovery, credential-lifetime keepalive, and quieter background refresh.
- Delivery into Desktop-owned live Bot Chats and accurate Telegram degraded
  connection state.
- Stage-and-swap desktop rebuilding and noninteractive update checks.
- Optional cache-hit-rate and tokens-per-second status-bar metrics.
- Post-release compression fixes for timeout termination, retry/cooldown
  recovery, failed-commit rollback, thinking-summary adoption, latest-intent
  anchoring, overflow recovery, tool-output deduplication, and stale screenshot
  eviction.

The merged root `package-lock.json` initially disagreed with the combined
manifests. It was regenerated with npm 11.17 and then accepted by a clean
`npm ci`.

## Verification evidence

### Python

- Yatima peer/A2A/profile/Bot DM/CUA/candidate-memory gate:
  `258 passed, 1 skipped, 10 deselected, 27 subtests passed`.
- GitSpawn and noninteractive Git security gate: `23 passed`.
- OAuth/Nous isolation, healing, concurrency and keepalive gate: `30 passed`.
- Bot delivery, Telegram state and reconnect gate: `92 passed, 1 skipped`.
- Desktop updater and passive-update gate: `65 passed, 9 skipped`.
  The owning shell exports `ELECTRON_OZONE_PLATFORM_HINT=wayland`; this suite
  was run with that inherited variable removed so its explicit x11/wayland
  configuration cases remained hermetic.
- Compression/context integrity gate: `294 passed`.
- Prompt-cache boundary, scope, TTL, provider-policy and metadata gate:
  `220 passed`.
- Ruff passed for the manually reconciled Python files.

### Desktop

- Focused Bot/session/cache UI gate: `216 passed`.
- Broad UI gate: `6,830 passed` across `688` test files.
- Renderer, Electron and e2e TypeScript typecheck: passed.
- Targeted ESLint for the ported modular Bot files: passed.

The broad jsdom run emitted its expected warning that
`HTMLCanvasElement.getContext()` is unavailable without the optional native
canvas package; it produced no test failure.

### Android

- Capacitor sync: passed.
- `:app:testDebugUnitTest`: passed.
- `assembleDebug`: passed.

Gradle reported the existing Android Gradle Plugin/compileSdk compatibility
warning and deprecated-API notices. They did not fail the build and were not
changed by this integration.

## Deliberately not adopted

- Current upstream `main` as a whole; it already contained hundreds of
  post-release commits beyond v0.21.0.
- The post-release default that automatically prunes `state.db` after 90 days.
  Yatima retains historical sessions and evidence under owner-controlled
  retention policy.
- The experimental core-tool rename/deferral tranche claiming roughly 49%
  smaller Desktop tool schemas. It changes public tool names and discovery
  behavior and requires an isolated before/after compatibility and token study.
- Unrelated post-release platform and cosmetic churn.

## Token-evidence boundary

This integration proves cache and compression mechanics through tests and adds
live cache-hit/tokens-per-second visibility. It does **not** claim a new live
token-savings percentage: no provider-backed workload was run during source
integration. Preserve the existing pre-button measurements, then collect an
equivalent post-integration workload before attributing any savings or
regression to v0.21.0.

## Remaining acceptance and rollout

Before deployment, package and smoke-test one non-authoritative node first:

1. Start the desktop and gateway without changing stored profiles.
2. Confirm Nous sign-in/refresh and profile isolation.
3. Open an existing canonical Bot Chat in the main window and a native pop-out.
4. Exercise a same-machine two-turn A2A context and one disposable
   `@bot@machine` Tailnet exchange.
5. Confirm Telegram reports connected only when polling is healthy.
6. Run a long disposable conversation through compression and record cache-hit,
   input/output, cooldown, and rotation metrics without reading message text.
7. Switch the Android client between both saved gateways and back.

Only after those checks should the integration branch be merged into the
customization branch, packaged, installed, or pushed as the deployment source.
