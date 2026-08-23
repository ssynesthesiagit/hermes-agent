# Hermes Android gateway client

This is a small Capacitor 6.2.1 Android shell around the existing responsive Hermes web gateway. It does not port Hermes or add a second chat UI: the same WebView top-level navigates to one selected hosted gateway at a time. The hosted page remains responsible for login, cookies, sessions, WebSocket streaming, uploads, and reconnect behavior.

## Build and test

Use the supplied JDK and Android SDK from PowerShell:

```powershell
$env:JAVA_HOME = 'C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot'
$env:ANDROID_HOME = 'C:\Users\ssyne\AppData\Local\Android\Sdk'
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
npm ci --workspaces=false
npm run cap:sync --workspaces=false
npm test --workspaces=false
npm run assembleDebug --workspaces=false
```

The debug APK is written to:

`android/app/build/outputs/apk/debug/app-debug.apk`

Install it on a Tailscale-connected device with `adb install -r android/app/build/outputs/apk/debug/app-debug.apk`. No emulator is included in this repository.

## Architecture and security boundaries

- `MainActivity` shows a native `Hermes gateways` picker from root-level Android Back. It lists each saved name and URL, marks the current entry, supports adding/editing/selecting gateways, reloads the encrypted active selection on reopen, and preserves normal WebView history.
- `TailscaleUrlPolicy` accepts only HTTP(S) hosts in `100.64.0.0/10`, `fd7a:115c:a1e0::/48`, or a proper `.ts.net` hostname. It rejects userinfo, ambiguous/malformed authorities, unsafe schemes, and invalid ports.
- `SecureGatewayStore` stores a bounded, versioned list of named gateways plus the active URL as one AES/GCM-encrypted payload. Only ciphertext and IV are kept in private SharedPreferences; the Android Keystore key is non-exportable and there is no plaintext fallback. Existing encrypted single-URL data is migrated on first read, while unreadable data is cleared fail-closed without touching unrelated preferences. Hermes passwords/tokens remain in the hosted WebView cookie/localStorage mechanism.
- The WebView keeps DOM storage/cookies and Capacitor's `BridgeWebChromeClient`, so each gateway origin retains its own cookies and DOM storage when switching. Hosted file/photo inputs remain available.
- A small native speech rail overlays the hosted page. `MIC` uses Android speech recognition and inserts the final transcript into the active Hermes Chat composer without sending it. `READ` speaks the latest completed assistant message with Android text-to-speech. `AUTO` is opt-in per app launch, skips the currently visible reply, waits for streaming to finish, reads each new reply once, and keeps the screen awake while enabled. Speech controls are scoped to Hermes's chat DOM and expose no JavaScript interface to hosted content.
- Microphone permission is requested only when `MIC` is first used. Recognition may be local or network-backed depending on the Android speech service installed on the device; the app stores no transcript itself.
- Cleartext is enabled only for the current HTTP Tailscale deployment; the custom WebView client blocks non-Tailscale HTTP(S) main-frame and resource requests, and cancels every TLS error. Android backup and device-transfer extraction are disabled.

## Explicit non-goals

No default-assistant roles, local models, guaranteed offline speech, background/push infrastructure, QR setup, bespoke speech service, or cosmetic redesign are included.
