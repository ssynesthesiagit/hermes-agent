# Desktop bot Sessions action did nothing

## Summary

- Date/time and timezone: 2026-08-23, America/New_York
- Affected platform/gateway: Windows and Ubuntu Hermes Desktop packages;
  Android/mobile was unaffected
- User-visible symptom: Right-clicking a bot and selecting **Sessions** closed the
  menu without opening that bot profile's session list or showing an error.
- Severity and current status: Medium usability failure; fixed and rendered-
  verified on both full desktop nodes.

## Baseline

- Repository/branch/commit: `codex/yatima-custom` at
  `e3f287e9af3b59a9d71cf80b0f4ea1f272aceb7e`
- Installed version or package: Windows and Ubuntu unpacked Electron packages
  whose renderer archives predated the current source implementation.
- Dirty owner files present: Yes. Documentation changes in the source checkout
  and custom/runtime changes in the installed tree were preserved.
- Service/task and listener state: Hermes Desktop was running on both nodes. The
  independent Windows dashboard task was later found stopped, restarted without
  changing its definition, and verified with the expected HTTP 302 authentication
  redirect. Ubuntu's separate gateway and dashboard services remained active on
  their original process IDs throughout its successful package deployment.
- Last known verified checkpoint:
  `e3f287e9af3b59a9d71cf80b0f4ea1f272aceb7e`

## Reproduction and evidence

1. Minimal reproduction: Open the Bots roster, right-click a bot, and select
   **Sessions**.
2. Expected result: Activate the bot's owning connection/profile and reveal the
   Sessions pane for that profile.
3. Actual result: The menu closed and the visible UI did not change.
4. Bounded redacted logs/errors: Static inspection of each deployed renderer
   showed the menu calling `openBotSessionsWorkspace(...)`, with no definition
   for that identifier anywhere in either package. The current source instead
   calls the host's `openProfileSessions(...)` method and reports handler
   failures.
5. Retry/restart comparison: Repeating the action in the old Windows package
   reproduced the no-op. Both corrected packages made the action work without
   changing sessions or profile state.

## Root cause

The deployed Windows and Ubuntu `app.asar` files were stale, broken intermediate
builds. Their bot context-menu handler referenced the undefined
`openBotSessionsWorkspace` identifier, causing a synchronous renderer
`ReferenceError`. Because the failure occurred before the current handler's
error reporting, the menu simply closed. Public Yatima repository source already
contained the correct `host.openProfileSessions(...)` routing; repository state,
installed-source state, build state, and deployed-package state had diverged.

## Change

- Repository files changed: No application source files in the public-source
  checkout. This incident record was added.
- Installed-source files changed: Ubuntu's owner-dirty installed tree received
  only the durable routing hunks in `plugin.js`, `sdk/index.ts`, and the focused
  `profile-routing.test.ts` fixture. Unrelated content in those files and the
  rest of the 31-path dirty set was preserved.
- Runtime/configuration changed: Replaced only the Windows and Ubuntu unpacked
  Electron packages with verified builds of the current public Yatima source.
  Existing profile, authentication, session, memory, and gateway data were not
  replaced.
- Security or compatibility implications: Authentication and network exposure
  were unchanged. Android was not rebuilt because no native, Capacitor, resource,
  or hosted mobile DOM-contract change was required.
- Owner changes preserved: Yes. Neither live source tree was cleaned or broadly
  overwritten, and both previous desktop packages remain available for
  rollback.

## Verification

- Focused tests: `profile-routing.test.ts` passed 30 of 30 tests in the clean
  checkpoint staging tree and again in the narrowly patched Ubuntu installed
  tree.
- Integration/build: `npm run pack --workspace apps/desktop` produced verified
  Windows and Linux unpacked packages. A subsequent owning build of Ubuntu's
  patched installed tree also passed. Both verified renderers contained
  `openProfileSessions` and its error fallback, with zero occurrences of
  `openBotSessionsWorkspace`.
- Rendered or device smoke test: In the relaunched Windows app, right-clicked the
  Yatima bot, selected **Sessions**, and observed the Sessions pane populated with
  that profile's saved sessions and profile roster. The owner independently
  confirmed the result looked correct. In the deployed Ubuntu Electron window,
  the real bot context menu exposed **Sessions**; selecting it closed the menu,
  selected the Sessions tab, rendered the searchable populated workspace, and
  produced no renderer error or fallback notification.
- Cross-platform check: Ubuntu's final package hash matched staging, the desktop-
  owned backends restarted, the independent gateway/dashboard services stayed
  active, the temporary localhost-only acceptance port was closed, and `/mobile`
  retained the expected HTTP 302 login redirect. Android was not changed.
- Result and remaining limitations: Both desktop nodes pass. The committed root
  npm lockfile does not yet include the Android workspace dependency graph; the
  staging-only lock synchronization used for these packages was not promoted to
  source.

## Deployment

- Staging artifact/source: Clean detached staging worktree at the recorded
  checkpoint. Dependency lock synchronization occurred only in staging.
- First machine result: Windows passed package-hash, process, profile-backend,
  dashboard-health, and rendered Sessions acceptance checks.
- Second machine result: Ubuntu passed package-hash, focused-test, owning-build,
  process, service-health, and rendered Sessions acceptance checks. Its normal
  `hermes desktop` entry rebuilds before launch, so the installed source received
  the same narrow routing fix to prevent a future launch from recreating the
  broken package. The verified package was launched directly for acceptance;
  the launcher definition itself was not changed.
- Android APK required: No. The working phone session-management flow and native
  Android contract were unchanged.

## Recovery

- Backup/rollback reference: Supported full Hermes state backups were verified
  outside the repository. The previous packages are retained as
  `win-unpacked-pre-sessions-20260823` and
  `linux-unpacked-pre-sessions-20260823`. Ubuntu also retains the package created
  by the intercepted live-tree rebuild and exact pre-patch source/test archives.
- Exact rollback steps: Cleanly quit only Hermes Desktop, move the current
  platform package aside, restore the corresponding `*-pre-sessions-20260823`
  directory to its normal unpacked name, and relaunch Desktop without restarting
  the independent gateway/dashboard service. On Ubuntu, restore the exact source
  archive too before using the rebuild-on-launch command. State restore is not
  required for a package-only rollback.
- Post-rollback health check: Confirm Desktop launch, expected profile backends,
  independent gateway/dashboard health and the 302 login redirect, then reopen
  an existing chat without deleting or migrating session data.

## Repository action

- Commit: None.
- Push/remote branch: None; remotes were not altered.
- Documentation updated: This public-safe incident record only; pre-existing
  documentation edits remain owner-controlled.
