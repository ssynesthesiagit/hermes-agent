# Incident and fix record

## Summary

- Date/time and timezone: 2026-08-24, America/New_York.
- Affected platform/gateway: Windows Hermes dashboard/mobile endpoint.
- User-visible symptom: the saved Windows computer gateway appeared
  disconnected.
- Severity and current status: service interruption; recovered.

## Baseline

- Repository/branch/commit: `codex/yatima-custom` at the recorded checkpoint
  `e3f287e9af3b59a9d71cf80b0f4ea1f272aceb7e`, with existing owner changes
  preserved.
- Installed version or package: existing Windows Hermes installation.
- Dirty owner files present: yes; no repository files were reset or cleaned.
- Service/task and listener state: `Hermes_Dashboard` was `Ready` rather than
  running, nothing listened on dashboard port 9119, and its last task result was
  `0xC000013A`. The profile messaging gateways were separate processes.
- Last known verified checkpoint:
  `e3f287e9af3b59a9d71cf80b0f4ea1f272aceb7e`.

## Reproduction and evidence

1. Minimal reproduction: select the saved Windows computer endpoint from the
   mobile client.
2. Expected result: the dashboard/mobile endpoint responds and redirects an
   unauthenticated `/mobile` request to login.
3. Actual result: no process listened on port 9119.
4. Bounded redacted logs/errors: the dashboard Scheduled Task's last result was
   the Windows control-event exit status `0xC000013A`; no credential or private
   configuration value was inspected.
5. Retry/restart comparison: starting the existing task restored the listener
   and `/mobile` returned HTTP 302.

## Root cause

The saved computer endpoint was unavailable because the separate Windows
dashboard process was no longer running. The task result proves a control-event
termination, but the available evidence does not identify which actor issued
it. The Telegram gateways were not the cause: `brain`, `dungeon-master`, and
`yatima-training` all reported Telegram `connected`.

## Change

- Files changed: this incident record only.
- Runtime/configuration changed: started the existing `Hermes_Dashboard`
  Scheduled Task. No task definition or profile configuration was edited.
- Security or compatibility implications: the listener remains restricted to
  its existing Tailscale address; authentication remains enabled.
- Owner changes preserved: yes. No gateway, session, profile, or credential
  state was removed.

## Verification

- Focused tests: not applicable; no source change.
- Integration/build: not required; the existing `--skip-build` dashboard task
  was used.
- Rendered or device smoke test: unauthenticated `/mobile` returned the expected
  HTTP 302 login redirect.
- Cross-platform check: not required; Ubuntu was not changed.
- Result and remaining limitations: Windows dashboard task is running and port
  9119 is listening. All pre-existing Telegram gateway PIDs remained unchanged.
  `dungeon-master` Telegram is connected, but its optional API server still
  reports `api_server_port_in_use` because `brain` already owns port 8642; that
  separate profile API setting was not changed.

## Deployment

- Staging artifact/source: none.
- First machine result: Windows dashboard recovered successfully.
- Second machine result: Ubuntu untouched.
- Android APK required: no; no application or DOM-contract change.

## Recovery

- Backup/rollback reference: not required; no configuration was modified.
- Exact rollback steps: stop only the `Hermes_Dashboard` Scheduled Task if this
  operational start must be reversed.
- Post-rollback health check: confirm port 9119 is intentionally absent; the
  Telegram gateway processes must remain unaffected.

## Repository action

- Commit: none.
- Push/remote branch: none.
- Documentation updated: this incident record.

## Follow-up: `dungeon-master` API port conflict

- Proven cause: `brain` and `dungeon-master` were both explicitly configured
  to bind their profile API server to port 8642. `brain` acquired the listener
  first, so `dungeon-master` reported `api_server_port_in_use` while its
  independent Telegram adapter remained connected.
- Change: backed up the private `dungeon-master` environment file and changed
  only `API_SERVER_PORT` from 8642 to the unused Tailscale-only port 8644.
- Restart scope: restarted only the `dungeon-master` gateway. The `brain` and
  `yatima-training` gateway PIDs and the Windows dashboard PID remained
  unchanged.
- Verification: `dungeon-master` Telegram and API server both report
  `connected`; its API listener owns port 8644 and `/health` returns HTTP 200.
  `brain` remains on 8642, `yatima-training` remains on 8643, and dashboard
  `/mobile` remains healthy.
- Recovery: restore the dated `.env.before-api-port-8644-20260824` backup from
  the profile's private `gateway-service` directory, then restart only the
  `dungeon-master` gateway. Restoring 8642 will intentionally recreate the
  collision while `brain` is using that port.
