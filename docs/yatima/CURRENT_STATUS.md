# Current status

Recorded 2026-08-22 for branch `codex/yatima-custom`.

## Architecture

- Windows and Ubuntu run full Hermes gateways and desktop applications.
- Android is a thin Tailscale-only client. It stores multiple gateway entries
  securely and connects to one selected gateway at a time.
- Bots use profile-scoped canonical conversations so phone and desktop can open
  the same bot chat instead of creating unrelated mobile sessions.
- Local-profile A2A routing provides bounded same-machine bot-to-bot calls while
  preserving each profile's configuration, memory, tools, and session history.

## Included milestone

- Responsive mobile chat and Bots UI served by Hermes.
- Secure multi-gateway selection in the native Android shell.
- Streaming responses, reconnect/resume, existing sessions, files/photos, and
  Android speech dictation.
- Manual and automatic full-response text-to-speech, Markdown rendering, and
  copy-response controls.
- Combined provider/model selection, including Codex OAuth and Nous/local
  models, without replacing Hermes authentication or backend behavior.
- Canonical bot-chat routing shared across phone and desktop.
- Desktop Bots roster, Sessions action, and secondary bot-chat window support.
- Bounded local-profile A2A routing through `a2a_call(agent="profile:<name>")`.

## Remaining acceptance gate

The secondary bot-chat window already opened with the correct profile/session
identity, but its cold transcript waited for runtime resume and could remain
blank during a slow profile start. The source repair now paints a correctly
addressed REST-prefetched transcript immediately in secondary windows only,
while the main window retains its single-build behavior and the live projection
still reconciles when resume binds.

The complete session-actions suite passed 55 of 55 tests and the Desktop,
Electron, and e2e TypeScript projects passed typecheck after this repair. A real
packaged Windows pop-out smoke test is still required before calling the UI gate
complete or deploying the same package/source change to Ubuntu.

## Checkpoint evidence

The initial fork checkpoint was byte-compared with all 35 intended changed/new
source and test files in the verified Windows tree with zero mismatches.

- Local-profile and existing A2A suites: 115 passed, 10 deselected.
- Canonical bot-chat and roster suites: 40 passed.
- Electron secondary-session window suite: 24 passed.
- Mobile-web owning tests: all 45 passed across bounded serial reruns. An earlier
  parallel run hit Windows worker-startup timeouts, not assertion failures.
- Mixed Codex CLI/model-picker suite: 4 passed.
- Desktop and mobile-web TypeScript typechecks: passed.
- Secondary-window eager transcript paint: complete session-actions suite 55
  passed; Desktop/Electron/e2e TypeScript typecheck passed.
- Broad desktop plugin sweep: 396 of 398 passed. The two failures were in
  untouched delegated-routine shell tests where a Windows subprocess returned a
  null exit status; they remain a recorded non-owning environment issue.

This is a development checkpoint, not a claim that every owner-facing flow is a
finished release. Update this file whenever an acceptance gate changes.
