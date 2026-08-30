# Hyprland computer-use pixel capture incident

## Summary

- Date/time and timezone: 2026-08-29, America/Detroit
- Affected platform/gateway: Omarchy 3.8.5 / Arch Linux, Hyprland, packaged Hermes Desktop
- User-visible symptom: the Bot could identify the window below Hermes but could not read its pixels or contents.
- Severity and current status: computer vision unavailable; fixed and live-verified.

## Baseline

- Repository/branch/commit: `ssynesthesiagit/hermes-agent`, `codex/yatima-custom`, baseline `4e526b0954e97623d845ac563294fcf89a970dcc`
- Installed version or package: Hermes Desktop v0.17.0 with Hermes Agent v0.20.6
- Dirty owner files present: the separate session-tile render-loop repair was already in progress and was preserved.
- Service/task and listener state: packaged Electron desktop with local default and profile-scoped `hermes serve` backends; dashboard and messaging gateway remained separate.
- Last known verified checkpoint: session-tile render-loop repair passed focused tests, typecheck, package, and real-window acceptance.

## Reproduction and evidence

1. Minimal reproduction: ask the Bot whether it can see the application beside Hermes.
2. Expected result: Hermes selects the pixel-capable `computer_use` tool and describes visible screen contents.
3. Actual result: only `read_window_below` ran, correctly returning app/title/bounds metadata under Hyprland but no pixels by design.
4. Bounded redacted logs/errors: `hermes computer-use status` reported that cua-driver was missing. After installation, the default X11 desktop capture failed at `GetImage`, and `list_windows` returned no windows despite the basic doctor probe reporting X11 capture readiness.
5. Retry/restart comparison: with cua-driver's native Wayland backend enabled, native windows enumerated and a 2560x1440 composited PNG was returned. The restarted live Bot then completed two consecutive pixel-vision turns.

## Root cause

Two independent prerequisites were absent. The configured `computer_use` toolset had no installed cua-driver backend, so the model fell back to the metadata-only desktop bridge. Once installed, cua-driver selected X11 because Hyprland exposes `DISPLAY` through XWayland; that backend cannot see native Wayland windows and its real full-screen capture failed. cua-driver's native Wayland lane worked when explicitly enabled.

## Change

- Files changed: `tools/computer_use/cua_backend.py`, `tests/computer_use/test_cua_telemetry.py`, and this incident record.
- Runtime/configuration changed: installed cua-driver-rs 0.22.2 in its user-scoped canonical location. Hermes now sets `CUA_DRIVER_RS_ENABLE_WAYLAND=1` for cua-driver children only when both Hyprland and Wayland are detected, unless the owner explicitly overrides the variable.
- Security or compatibility implications: cua-driver remains local; Hermes's existing default telemetry-off policy remains in force. The automatic opt-in is narrowly scoped to Linux Hyprland sessions and respects an explicit owner override.
- Owner changes preserved: no sessions, profiles, memories, themes, browser data, credentials, or recovery material were modified.

## Verification

- Focused tests: environment-policy and sanitized CLI fallback tests pass, including native-Wayland enablement and explicit-override coverage.
- Integration/build: Hermes's real `CuaDriverBackend` started against the installed binary and returned a non-empty 2560x1440 full-screen pixel capture.
- Rendered or device smoke test: after restarting the packaged desktop, the live Bot used `computer_use` twice. One explicit tool-selection prompt and one natural-language screen question both produced accurate descriptions of the two visible applications and visible text. Tool execution completed without retained turn errors.
- Cross-platform check: behavior is unchanged outside Linux Hyprland and when the owner sets `CUA_DRIVER_RS_ENABLE_WAYLAND` explicitly.
- Result and remaining limitations: pixel vision works. Native Wayland window enumeration in cua-driver 0.22.2 reports incomplete per-window geometry, so this acceptance proves composited full-screen vision rather than every background input/action operation.

## Deployment

- Staging artifact/source: live checkout at `/home/ssynesthesia/Projects/hermes-agent`; the desktop launches the Python backend from this checkout.
- First machine result: Swarmlord passed live pixel-vision acceptance.
- Second machine result: not run; the independent Ubuntu node was not modified.
- Android APK required: no; no native, Capacitor, resource, or mobile DOM contract changed.

## Recovery

- Backup/rollback reference: Git diff against baseline `4e526b0954e97623d845ac563294fcf89a970dcc`; cua-driver is isolated under the user's `.cua-driver` directory with a user-bin symlink.
- Exact rollback steps: revert only the two source/test hunks and uninstall cua-driver through its supported installer workflow if removal is desired. Do not delete Hermes state.
- Post-rollback health check: `hermes computer-use status`, `hermes computer-use doctor --json`, and a metadata-only `read_window_below` turn.

## Repository action

- Commit: none.
- Push/remote branch: none.
- Documentation updated: this incident record.
