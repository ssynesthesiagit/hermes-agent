# Hermes Android client verification report

Date: 2026-08-19

## Outcome

The narrow Android client is implemented as a Capacitor 6.2.1 / Android WebView shell around the existing responsive Hermes frontend. It preserves the live Hermes backend, login, cookies, sessions, streaming transport, and upload controls rather than porting the runtime. After connection it opens the gateway's dedicated `/mobile` route, where the existing Hermes profile and session APIs power the phone-oriented bot UI.

Implementation status: **PASS**

Authenticated end-to-end proof: **BLOCKED pending owner sign-in**

## Deliverable

- APK: `android/app/build/outputs/apk/debug/app-debug.apk`
- Package: `com.hermes.gatewayclient`
- Size: 3,774,761 bytes
- SHA-256: `E470BFFDA0A05D132E8087D6F50964FA0786A0DE69AE017E02D9535860E8B5C5`

## Implemented scope

- First-run native dialog accepts only HTTP(S) Tailscale destinations in `100.64.0.0/10`, `fd7a:115c:a1e0::/48`, or a valid `.ts.net` hostname.
- WebView navigation, HTTP(S) resources, WebSocket/EventSource/WebTransport, fetch/XHR/beacon, workers, WebRTC, and service-worker paths are guarded to prevent non-tailnet egress. Unsupported WebViews fail closed.
- Gateway configuration is encrypted with AES-GCM using a non-exportable Android Keystore key. Private preferences contain only ciphertext and IV; there is no plaintext fallback.
- Hermes cookies and hosted application storage remain under the existing WebView/authentication mechanism.
- Capacitor's normal file chooser is preserved for the hosted Hermes file/photo controls.
- Android system keyboard dictation is used; the APK requests no microphone or camera permission.
- Cookies are flushed on pause. Resume retries a previously failed main-frame load and dispatches online/visibility events so the hosted frontend can reconnect.
- Android backup/data transfer is disabled. The only declared runtime permission is `INTERNET`.
- The shell derives `/mobile` from the validated gateway URL without changing the encrypted stored base URL; root, reverse-proxy path, query, fragment, and already-mobile cases are covered by unit tests.
- The mobile frontend labels Hermes profiles as **Bots**, shows compact bot badges in the picker, opens a profile-scoped direct chat, and surfaces only gateway-emitted reasoning/status/tool/background activity. Tool metadata is reduced to allowlisted generic labels and opaque lifecycle keys before entering UI state.

Explicitly excluded: default-assistant roles, local models, offline mode, push/notification infrastructure, a bespoke speech service, and cosmetic redesign.

## Verification evidence

- Android unit tests: 14 passed, 0 failures, 0 errors.
- Focused mobile chat tests: 20 passed. Frontend typecheck and production build passed; targeted lint reported 0 errors and 2 pre-existing hook warnings.
- Debug APK assembled and installed with `adb install -r` on an API 36 emulator.
- The installed app connected through the tailnet to `http://100.112.52.74:9119` and rendered the existing Hermes authentication page.
- Force-stop/reopen and background/resume returned to the live Hermes authentication page.
- Final installed configuration preferences were inspected and contained ciphertext plus IV only, with no plaintext gateway URL.
- Final device screenshots:
  - `evidence/hermes-hardened2.png` — hardened build connected to the live Hermes login.
  - `evidence/hermes-hardened-resume.png` — live Hermes login restored after background/resume.
- Post-review browser verification at a 390×844 viewport confirmed the Bots picker, compact per-bot badges, selected-bot state, direct bot chat header, bot-specific composer, and no console warning or error. Source hashes were unchanged by browser QA. No real message was sent.

## Independent review

The initial native shell source, tests, APK, and evidence were reviewed by a separate `gpt-5.6-terra` HIGH process under an explicit read-only sandbox with multi-agent work disabled. The final mobile activity adapter received an additional focused review under the same read-only constraints.

- Result: no outstanding implementation defects found.
- The initial reviewed APK size and SHA-256 matched that milestone's deliverable; the current APK hash is recorded above after the verified `/mobile` route update.
- The designated `__readonly_probe__.tmp` shell write was denied and the file was not created.
- The 61-file source/config composite SHA-256 was identical before and after review: `B10171AA6E8DDE58E6C4A35277586670CDAF3AAB195312C20A1ABDB8BF72396A`.
- A separate post-review browser debugger hash covered 74 app source/config files and also remained identical: `7657255630427A74EB7683C85D0575448BC2502C22B95EBD31FC539A0BB5E0BE`.
- The focused activity-feed review confirmed that raw tool IDs, command/context previews, results, errors, and secret-like names do not enter rendered activity state. Its write probe was denied and the reviewed source hashes remained unchanged.

## Coding-team configuration

The reusable Codex coding team is configured with `gpt-5.6-sol` MAX as the root coordinator, Luna researcher/coder/browser-debugger roles, and a Terra reviewer role. The actual implementation used the Coder, independent Reviewer, and pre/post browser verification workflow while preserving owner files and avoiding Git mutations.

## Remaining acceptance gate

The device/browser reached the real Hermes sign-in page, but no authorized Hermes credentials were supplied. Consequently, the following authenticated runtime claims have not yet been observed and must not be reported as proven:

- existing-session restoration after login;
- message send and incrementally streamed response;
- file/photo sharing through the hosted controls;
- Android IME speech dictation into the hosted composer.

To complete the requested install → connect → message → streamed response → reopen chain, the owner should sign in to the existing Hermes gateway in the already-open in-app browser or installed APK, keep the password private, and reply `ready`. The authenticated checks can then continue without changing Hermes authentication or backend behavior.

SOL_LUNA_TERRA_CODEX_TEAM_BLOCKED
