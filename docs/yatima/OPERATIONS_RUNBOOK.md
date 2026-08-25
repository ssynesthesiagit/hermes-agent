# Operations runbook

This runbook covers routine health checks, local builds, deployment staging and
recovery without exposing machine-specific addresses or credentials. Replace
values in angle brackets with values from the owner's private machine handoff.

## Fast health model

Check the stack from the bottom upward:

1. the computer is awake and Tailscale sees it;
2. the correct process owns the dashboard port;
3. `/mobile` returns a page or an authentication redirect;
4. login succeeds;
5. the gateway API/WebSocket connects;
6. the target profile runtime resumes;
7. the canonical Bot Chat streams and reopens.

This order prevents a UI symptom from being misdiagnosed as lost session data.

## Dashboard versus messaging gateway

These commands are not interchangeable:

- `hermes dashboard --host <tailscale-ip> --port 9119 --skip-build --no-open`
  serves the browser/mobile UI.
- `hermes gateway run` starts configured messaging-platform integrations such
  as Telegram. It does not by itself serve the phone UI on port 9119.
- `hermes serve` is a headless backend. Browsing its root can return a JSON
  message explaining that the web UI is disabled.

When Android says `ERR_CONNECTION_REFUSED` or times out, verify that the
dashboard command—not merely a messaging gateway—is listening.

## Windows dashboard checks

```powershell
tailscale status
Get-NetTCPConnection -LocalPort 9119 -State Listen -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskName 'Hermes_Dashboard' -ErrorAction SilentlyContinue
Get-ScheduledTaskInfo -TaskName 'Hermes_Dashboard' -ErrorAction SilentlyContinue

try {
  Invoke-WebRequest -UseBasicParsing -MaximumRedirection 0 `
    -Uri 'http://<tailscale-ip>:9119/mobile?hermes_client=android-4' `
    -TimeoutSec 8
} catch {
  if ($_.Exception.Response) {
    [int]$_.Exception.Response.StatusCode
  } else {
    $_.Exception.Message
  }
}
```

An HTTP `302` to `/login` is healthy when the client is not authenticated. No
listener or a TCP timeout is not healthy.

To start an existing persistent task:

```powershell
Start-ScheduledTask -TaskName 'Hermes_Dashboard'
```

If the task must be recreated, resolve the exact installed Hermes executable
and working directory first. Use a logon trigger, `StartWhenAvailable`, no
battery stop, no execution time limit, `IgnoreNew` multiple-instance behavior,
and bounded restart-on-failure. Do not create duplicate dashboard tasks or run
two processes on port 9119.

## Ubuntu gateway checks

```bash
tailscale status
systemctl --user status hermes-gateway.service --no-pager
ss -ltnp | grep ':9119'
curl -I --max-time 8 \
  'http://<tailscale-ip>:9119/mobile?hermes_client=android-4'
journalctl --user -u hermes-gateway.service -n 100 --no-pager
```

Restart only after collecting the useful status/log evidence:

```bash
systemctl --user restart hermes-gateway.service
systemctl --user status hermes-gateway.service --no-pager
```

If the installed service definition starts only a headless or messaging
gateway, correct the service using Hermes's supported dashboard configuration;
do not weaken authentication or bind a second public interface as a shortcut.

## Android gateway switching

1. From the top-level hosted page, press Android Back to open `Hermes gateways`.
2. Select a saved Windows or Ubuntu entry, or edit the address.
3. Use a complete URL such as `http://<tailscale-ip>:9119`, not a label or
   placeholder.
4. Sign in once per gateway origin. Switching does not delete the other origin's
   cookies or sessions.

If Back navigates web history, return to the hosted root first and press Back
again. Never clear app data as the first troubleshooting step because that
removes encrypted gateway entries and hosted-origin login state.

## Android build and install

From `apps/android/` on the recorded Windows toolchain:

```powershell
$env:JAVA_HOME = '<path-to-a-JDK-17-installation>'
$env:ANDROID_HOME = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
npm ci --workspaces=false
npm run cap:sync --workspaces=false
npm test --workspaces=false
npm run assembleDebug --workspaces=false
```

The debug APK is generated under
`android/app/build/outputs/apk/debug/app-debug.apk` and is intentionally ignored
by Git. Install on an authorized connected device with:

```powershell
adb devices
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

Run Capacitor sync before Gradle. A clean clone does not already contain the
generated `capacitor-cordova-android-plugins` directory.

## Desktop development verification

Install dependencies from the repository root using the supported Node/npm
versions, then select checks based on the files touched:

```bash
npm install
npm --workspace apps/desktop run typecheck
npm --workspace apps/desktop run test:ui
npm --workspace apps/desktop run test:desktop:platforms
npm --workspace apps/desktop run check:test:plugins
npm --workspace apps/desktop run build
```

Use focused tests during repair and one owning integration pass afterward. A UI
change also requires rendered verification in the real desktop shell or browser
viewport; typecheck alone is not acceptance.

For local-profile A2A changes:

```bash
python -m pytest \
  tests/plugins/test_a2a_local_profile.py \
  tests/plugins/test_a2a_plugin.py -q
```

## Deployment discipline

1. Record the running commit/package/version and dirty state.
2. Back up local configuration/state using Hermes's supported backup mechanism.
3. Build in a clean staging checkout, not inside the live install.
4. Stop the affected application/service cleanly.
5. Deploy only the verified source/package; do not overwrite configuration,
   profiles, sessions or memory.
6. Start and perform the minimum acceptance flow from `HANDOFF.md`.
7. Keep the old package/source and backup reference until both full nodes pass.

Hosted UI changes require deployment to each full Hermes gateway. Native Android
changes require a new APK. A backend-only or hosted-UI change normally does not
require reinstalling the Android shell.

## Evidence to collect before repair

- exact user-visible error and time;
- current Git commit, branch and dirty state;
- process/service state and listening address;
- relevant bounded logs;
- HTTP status for `/mobile`;
- affected gateway, profile and session identity without message contents;
- whether Retry, restart or switching gateways changes the symptom.

Use [INCIDENT_TEMPLATE.md](INCIDENT_TEMPLATE.md) to record the result without
publishing secrets.
