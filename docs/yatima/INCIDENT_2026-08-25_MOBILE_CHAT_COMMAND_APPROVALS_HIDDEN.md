# Incident and fix record: mobile Chat command approvals hidden

## Summary

- Date/time and timezone: 2026-08-25 05:34 EDT (UTC-04:00)
- Affected platform/gateway: Shared hosted Hermes `/mobile` client used by the thin Android Capacitor/WebView client on either Windows or Ubuntu gateway.
- User-visible symptom: A command that required approval did not present approval controls in the phone Chat screen. The user had to open the Advanced screen to approve it.
- Severity and current status: Command execution was blocked from the normal phone workflow. The narrow source fix is implemented and verified locally; deployment remains pending explicit owner authorization.

## Baseline

- Repository/branch/commit: `hermes-agent-fork`, `codex/yatima-custom`, HEAD `e3f287e9af3b59a9d71cf80b0f4ea1f272aceb7e` plus preserved uncommitted owner work.
- Installed version or package: Windows and Ubuntu installed source matched the pre-fix mobile source during diagnosis. Neither installed tree was overwritten during this source change.
- Dirty owner files present: Yes. Existing documentation, CLI, test, and incident/runbook changes were preserved.
- Service/task and listener state: No live dashboard, gateway, backend, listener, or Android installation was restarted or changed for this source-only fix. Acceptance used a temporary local synthetic gateway that was stopped afterward.
- Last known verified checkpoint: `e3f287e9af3b59a9d71cf80b0f4ea1f272aceb7e`.

## Reproduction and evidence

1. Minimal reproduction: Open the hosted `/mobile` Chat view with an active profile/session, then let a command emit `approval.request`.
2. Expected result: Chat displays a phone-sized approval card with only the choices offered by the backend and sends the response for the exact active runtime session and profile.
3. Actual result: The baseline mobile event filter omitted `approval.request`; its catch-all gateway listener returned before presenting anything in Chat. Advanced could still handle the request.
4. Bounded redacted logs/errors: Source tracing proved the mobile event-set omission and early return. Synthetic acceptance verified `approval.received`, `approval.pending`, and exact `approval.respond` parameters without using owner data or credentials.
5. Retry/restart comparison: Reopening or retrying Chat could not surface the omitted event. Advanced worked because it had a separate approval handler. The fixed Chat view replays a pending approval after reconnect.

No tokens, passwords, cookies, private configuration, owner messages, sessions,
memories, recovery archives, or signing material are included here.

## Root cause

`web/src/pages/MobilePage.tsx` did not include `approval.request` in the mobile activity events handled by `gateway.onAny`. The handler returned early for events outside that set, so a valid backend approval request never reached the phone Chat renderer. This was a shared source defect, not an Android runtime requirement or a stale-node-only symptom.

## Change

- Files changed: `web/src/pages/MobilePage.tsx`, `web/src/pages/MobilePage.css`, and `web/src/pages/MobilePage.test.tsx`.
- Runtime/configuration changed: None. The change adds the Chat approval presentation, reconnect replay, response/error state, phone layout, and focused regression coverage to the existing hosted renderer.
- Security or compatibility implications: The UI renders command text as React text, accepts only choices offered by the backend, binds responses to the exact active runtime session/profile, invalidates stale delayed work, keeps the composer locked while approval is pending, and requires a second focused confirmation for `always`. It adds no phone runtime, backend, public listener, offline mode, or push path.
- Owner changes preserved: Yes. No unrelated dirty file was edited, reverted, deleted, committed, or deployed.

## Verification

- Focused tests: `npm exec -- vitest run src/pages/MobilePage.test.tsx --reporter=dot` passed: 1 file, 12 tests.
- Integration/build: Fresh production `npx vite build` passed with 2,221 modules transformed. `git diff --check` passed.
- Rendered or device smoke test: The real production bundle was exercised in the Codex in-app browser at a configured 390 x 844 phone viewport against a temporary synthetic gateway. The approval card appeared in Chat above the disabled composer; Stop remained available; `Run once` sent the exact active session/profile payload; a forced response failure stayed visible inside the card and retried successfully; and `Always allow` sent nothing until the separate focused confirmation was clicked. Browser warnings/errors: 0.
- Cross-platform check: The implementation is in the shared hosted `/mobile` page used by both full nodes. No installed Windows or Ubuntu bundle was changed in this stage.
- Result and remaining limitations: Independent read-only review passed with no blocking findings. A real signed-in phone smoke test remains appropriate after an authorized staged dashboard deployment.

## Deployment

- Staging artifact/source: Locally built `hermes_cli/web_dist`; temporary acceptance server stopped after testing.
- First machine result: Not deployed; explicit owner authority is still required.
- Second machine result: Not deployed; explicit owner authority is still required.
- Android APK required: No. Only hosted React/CSS behavior changed; native Capacitor resources and the Android DOM contract were preserved.

## Recovery

- Backup/rollback reference: No live rollback is currently needed because nothing was deployed. Pre-deployment backups must be created before replacing either node's hosted bundle.
- Exact rollback steps: If later required, stop only the affected dashboard, restore its backed-up hosted bundle/source, restart that dashboard, and leave `hermes gateway run` and `hermes serve` separate and untouched.
- Post-rollback health check: Verify the dashboard listener and confirm `/mobile` returns the expected healthy response (including `302` to `/login` when unauthenticated).

## Repository action

- Commit: None; not authorized.
- Push/remote branch: None; not authorized. Remotes were not altered.
- Documentation updated: This incident record only.
