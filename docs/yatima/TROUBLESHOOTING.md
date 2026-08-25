# Troubleshooting guide

Start with the narrowest section matching the symptom. Preserve logs and state
before restarting; a retry that works is evidence of a race, not proof that the
problem is fixed.

## Connectivity and startup

### Android shows `ERR_CONNECTION_REFUSED` or connection timed out

Likely causes include a sleeping/offline peer, no listener on its Tailscale
address and port 9119, a messaging gateway running without the dashboard, an
exited service/task, or a saved entry pointing at the other computer or an old
address. Follow the platform health checks in `OPERATIONS_RUNBOOK.md` and confirm
the active Android gateway before editing services.

### Browser shows `Headless backend (hermes serve): web UI disabled`

The address reaches a headless backend, not the dashboard UI. Start/configure
the Hermes dashboard on the Tailscale interface and port 9119. Do not disable
authentication or expose a public interface to make the error disappear.

### `/mobile` returns HTTP 302

This is normally the expected redirect to `/login`. Sign in using the existing
Hermes authentication. It proves the dashboard answered; it is not a failed
health check.

### Windows Scheduled Task is `Ready`, not `Running`

Inspect `LastTaskResult`, the dashboard listener and process list. Result
`0xC000013A` means the process was interrupted, often by Ctrl+C or a forced stop.
Start the task, verify port 9119 and confirm restart settings. Do not launch a
permanent service inside an automation-owned temporary terminal.

## Hosted UI and stale versions

### The phone says `Profiles` instead of `Bots`

The label and Bots UI are served by the selected Hermes gateway, not baked into
the APK. If one gateway shows Bots and the other shows Profiles, the second is
serving older/stale desktop assets or source. Verify the active gateway, safely
deploy that full node, restart its dashboard, then reload. Reinstalling an
unchanged APK will not fix hosted text.

### Bot icons are generic or have the wrong art

Determine whether the hosted roster returned an avatar and whether the cached
asset belongs to the active gateway. Profile art is owned by Hermes/profile
data, not the Android shell. Preserve a fallback for profiles without artwork.

### Desktop Bot Chat flickers, stays blank or a pop-out never paints history

Capture whether the canonical session resolves, REST history arrives, and
runtime resume binds. The fork allows a secondary window to paint correctly
addressed prefetched history before slow resume, while the live runtime later
reconciles it. Do not add polling loops, duplicate sessions or multiple renderer
builds as a visual workaround. Run profile-routing, session-actions and Electron
session-window tests, then verify a packaged pop-out with an existing multi-turn
Bot Chat.

### Opening a session fails, but Retry immediately works

Treat this as a gateway/runtime startup or reconnect race. Record timestamps and
the affected profile/session, then inspect boot, session-resume and connection
logs. A single recovery after sleep is tolerable; repeated first-open failure is
not a completed fix.

## Conversation identity

### Phone messages appear on phone but not in the desktop Bots pane

The clients may be viewing different session IDs. Confirm both resolve the
profile's canonical hidden `Bot Chat`. Do not create a new phone session by
default. Inspect the canonical pin, newest `Bot Chat` lineage and desktop source
routing before modifying or deleting sessions. History can be intact while a
pane points at the wrong conversation.

### A pop-out opens a different or empty conversation

Verify the window receives the source gateway, profile and canonical session
identity together. Window reuse must be keyed to that identity. A compressed
session ID may rotate, so use durable lineage/canonical resolution rather than
freezing the first raw ID forever.

## Models and providers

### Model selection shows Codex OAuth or Nous/local models, but not both

Check provider credential/availability reporting and catalog normalization
before the visual picker. Intended behavior merges all usable configured
providers while preserving canonical custom model keys. Do not hardcode one
provider, treat an empty provider as global failure, or discard custom models
because another provider is authenticated. Verify one Codex OAuth and one
Nous/local choice in the same running app after repair.

## Speech and formatting

### TTS reads only the first sentence or speaks in abrupt fragments

The Android controller must wait for a completed assistant reply, extract the
entire final message, split only for Android TTS engine limits, and queue every
chunk in order. Do not feed each streaming DOM mutation directly to TTS. Check
`CompletedReplyTracker`, `SpeechPageScript`, `SpeechChunker` and controller queue
callbacks, then test a long multi-paragraph reply. The installed TTS engine can
affect voice quality but should not discard the queued remainder.

### Dictation works but does not send

That is intentional. `MIC` inserts the final transcript into the active composer
for review; it does not auto-submit. Recognition may be local or network-backed
depending on the phone's installed service.

### Bold, lists or code appear as plain text

Formatting is hosted mobile UI behavior. Preserve safe Markdown rendering and
copy the underlying response text. Do not replace the stable chat projection
with a second Android-native transcript renderer.

## Local-profile A2A

### Bots cannot see or call another local profile

Enabling the `a2a` toolset only permits outbound calls. Use the explicit target
`profile:<canonical-profile-name>` through the customized local route. The
target does not need a separate port or inbound Agent Card. Confirm the source
runs the customized fork and the caller has the outbound A2A toolset.

### Context is rejected or a second turn forgets the first

Reuse the exact returned `context_id` with the same target. Inspect whether the
canonical session was deleted/replaced or compressed and whether its lineage
still resolves. Never edit signed route state or bypass profile/context checks.

### Automated bots loop or recursively call one another

Stop the caller/scheduler and preserve audit evidence. Keep self/cycle/depth
safeguards. Local A2A supports bounded exchanges, not an unattended infinite
coworker loop. Add orchestration only with explicit limits and owner approval.

## Safe escalation bundle

Provide the next task with symptom/time/platform; gateway and profile names
without credentials; repository commit/branch/dirty state; service/task,
listener and HTTP result; the smallest relevant redacted log excerpt;
reproduction and Retry/restart comparison; tests already run; and every code,
runtime or deployment change made.
