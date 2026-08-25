# Incident and fix record

## Summary

- Date/time and timezone: 2026-08-23, America/New_York.
- Affected platform/gateway: Ubuntu Hermes Desktop Prime/default backend and
  the Ubuntu default messaging/API gateway. Windows and Android application
  code were not affected.
- User-visible symptom: Prime did not load its own sessions and could not open
  a usable new session. A fresh Ubuntu gateway launch could also adopt the
  sticky desktop profile instead of the canonical default profile.
- Severity and current status: High usability and routing impact; resolved and
  deployed on Ubuntu. Both full-node gateways are connected and healthy.

## Baseline

- Repository/branch/commit: `codex/yatima-custom` at
  `e3f287e9af3b59a9d71cf80b0f4ea1f272aceb7e` with preserved local owner
  documentation changes.
- Installed version or package: Ubuntu live source and packaged desktop under
  the normal Hermes installation tree; Windows remained on its existing Hermes
  installation.
- Dirty owner files present: yes. They were inventoried before work and were
  not reset, cleaned, committed, or pushed.
- Service/task and listener state: dashboard processes were already separate
  from messaging gateways. Ubuntu's default gateway was initially running with
  stale identity behavior; the intended Windows `yatima-training` task was not
  running.
- Last known verified checkpoint:
  `e3f287e9af3b59a9d71cf80b0f4ea1f272aceb7e`.

## Reproduction and evidence

1. Minimal reproduction: open Ubuntu Hermes Desktop, right-click Prime, choose
   **Sessions**, then try **New Session**. Separately, restart the generated
   Ubuntu default gateway unit while a named desktop profile is sticky.
2. Expected result: Prime lists default-profile sessions and opens a blank,
   enabled composer; the canonical Ubuntu gateway always starts as `default`.
3. Actual result: Prime's backend adopted the sticky named profile, so the
   sessions list belonged to that profile and new-session routing was unusable.
   A regenerated default gateway unit omitted an explicit profile and could do
   the same on its next restart.
4. Bounded redacted logs/errors: Python module tracing showed
   `hermes_cli.main` executing once as `__main__` and again by package import.
   The first pass consumed `--profile default`; the second pass then followed
   the sticky profile. The old desktop entry also launched an unrelated UV
   interpreter and failed to import `hermes_cli`.
5. Retry/restart comparison: restarting without the source fix reproduced the
   wrong backend identity. After the module-binding fix, Prime stayed default.
   After adding the systemd drop-in, a gateway restart showed an effective
   command containing `--profile default` and returned HTTP 200 from `/health`.

No tokens, cookies, private configuration values, session contents, memories,
or signing material were recorded.

## Root cause

`python -m hermes_cli.main --profile default serve ...` initially executed the
CLI as `__main__`. A later import of `hermes_cli.main` created a second module
instance. Because the first instance had already stripped the profile flag from
`sys.argv`, the second instance applied the sticky active profile and changed
the backend's effective `HERMES_HOME`.

Two deployment details amplified the fault:

- the Ubuntu desktop entry bypassed the installed Hermes launcher and selected
  a Python interpreter that could not import Hermes; and
- the generated default systemd gateway unit intentionally used a bare
  `gateway run`, which is unsafe for this two-profile desktop architecture
  because bare runs follow the sticky active profile.

The apparent "two installs" were one active installation plus inactive staging
and rollback artifacts, not two simultaneously active Ubuntu applications.

## Change

- Files changed: `hermes_cli/main.py` binds the running `__main__` module as
  `hermes_cli.main`; `tests/hermes_cli/test_apply_profile_override.py` covers
  that invariant.
- Runtime/configuration changed: the Ubuntu desktop entry now invokes the
  installed `hermes desktop` launcher. A systemd service drop-in clears and
  replaces the default gateway `ExecStart` with an explicit
  `--profile default` command, so future unit regeneration cannot remove the
  pin. Ubuntu live source received the two Python files.
- Security or compatibility implications: listeners remain on their existing
  Tailscale-only addresses. Authentication, profile storage, sessions, and
  platform credentials were not changed. The drop-in narrows gateway identity;
  it does not create an additional gateway.
- Owner changes preserved: yes. No session/profile state was deleted, and no
  unrelated source or documentation was reverted.

## Verification

- Focused tests: five profile-override tests passed against both staging and
  the Ubuntu live tree. `py_compile` and `git diff --check` passed.
- Integration/build: the owning Ubuntu desktop packaging pass completed from
  the live source. The packaged application was then launched normally without
  a remote-debugging flag; the temporary debug listener was closed.
- Rendered or device smoke test: in the real Ubuntu renderer, Prime's Sessions
  view showed default-profile rows, **New Session** opened an enabled blank
  composer with the default profile selected, and a named bot's Sessions view
  showed that bot's own rows. No message was sent and no state was deleted.
- Cross-platform check: Ubuntu's default gateway restarted with an explicit
  default identity and `/health` returned 200. The intended Windows
  `yatima-training` scheduled gateway connected, its Telegram polling reported
  healthy, and `/health` returned 200. Both dashboards returned the expected
  302 redirect from `/mobile`.
- Result and remaining limitations: resolved. The inactive generic Windows
  root startup launcher was deliberately not started; it remains a documented
  duplicate-port risk if manually enabled alongside `yatima-training`.

## Deployment

- Staging artifact/source: isolated Ubuntu staging build followed by the live
  Python source update and packaged desktop build.
- First machine result: Ubuntu Prime/default and named-bot Sessions flows passed
  rendered acceptance; desktop, dashboard, and default gateway are healthy.
- Second machine result: Windows application code was unchanged; only the
  intended existing `yatima-training` gateway task was started and verified.
- Android APK required: no. No Android, Capacitor, native resource, or mobile
  DOM-contract change was made, and Android session management was already
  healthy.

## Recovery

- Backup/rollback reference: the local Ubuntu rollback bundle is
  `~/hermes-rollbacks/prime-profile-repair-20260823/`; the earlier packaged
  desktop rollback directories were retained.
- Exact rollback steps: stop the Ubuntu desktop and gateway; restore
  `main.py.before`, `test_apply_profile_override.py.before`, and
  `hermes.desktop.before` from the rollback bundle; remove only the added
  `hermes-gateway.service.d/10-yatima-default-profile.conf` drop-in; run
  `systemctl --user daemon-reload`; restore the prior packaged desktop directory;
  then restart the desktop, dashboard if needed, and gateway.
- Post-rollback health check: confirm exactly one desktop main process, verify
  gateway and dashboard listeners, expect HTTP 200 from gateway `/health` and
  HTTP 302 from dashboard `/mobile`, and verify the effective gateway profile
  before sending any platform message.

## Repository action

- Commit: none.
- Push/remote branch: none; remotes were not altered.
- Documentation updated: this incident record. Existing owner handoff and
  runbook edits were preserved.
