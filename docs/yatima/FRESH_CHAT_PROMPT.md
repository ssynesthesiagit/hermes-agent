# Fresh Codex task prompt

Copy the block below into a new coding task. Add the specific symptom or feature
request after the final paragraph.

```text
Continue the Yatima customized Hermes project from its durable handoff. The
source of truth is https://github.com/ssynesthesiagit/hermes-agent on branch
codex/yatima-custom. The recorded checkpoint when this prompt was written is
e3f287e9af3b59a9d71cf80b0f4ea1f272aceb7e, but do not assume the checkout or
deployed machines still match it: inspect actual state first.

Before acting, read YATIMA_CUSTOM.md and these files completely:
- docs/yatima/HANDOFF.md
- docs/yatima/CURRENT_STATUS.md
- docs/yatima/OPERATIONS_RUNBOOK.md
- docs/yatima/TROUBLESHOOTING.md
- docs/yatima/UPDATE_AND_RECOVERY.md
- docs/yatima/A2A_LOCAL_PROFILE_ROUTING.md
- docs/yatima/INCIDENT_TEMPLATE.md
- apps/android/README.md when Android or the hosted mobile contract is involved

If a private local-machine handoff is provided in the workspace, read it too,
but never copy its addresses, account names, paths, private state or credentials
into the public repository. Never request or expose passwords, tokens, cookies,
session contents, memories, model weights or signing keys.

Start with read-only inventory: Git branch/remotes/commit/status and upstream
divergence; installed Windows and Ubuntu source/package/version/dirty state;
Tailscale peer health; dashboard service/task; listeners and /mobile HTTP status;
and the active Android gateway when relevant. A 302 redirect to /login is a
healthy unauthenticated dashboard response. Distinguish `hermes dashboard` on
port 9119 from `hermes gateway run` messaging integrations and the headless
`hermes serve` backend.

Preserve the architecture and invariants: Windows and Ubuntu are separate full
Hermes nodes; Android is a thin Tailscale-only Capacitor/WebView client that can
save both gateways but selects one at a time. Hermes remains the backend and
authentication authority. Preserve secure encrypted gateway storage, existing
sessions, streaming/reconnect, files/photos, Markdown/copy, dictation,
full-response TTS, combined Codex OAuth plus Nous/local model catalogs, canonical
cross-device Bot Chats, Desktop Bots/Sessions/pop-outs, profile isolation, audit
records, and bounded same-machine `a2a_call(agent="profile:<name>")` routing.
Do not add phone-local models, offline mode, push infrastructure, default-
assistant integration, a second chat renderer/backend, public non-Tailscale
network exposure, or unbounded agent-to-agent loops unless explicitly requested.

Do not edit a live installation as the upstream integration workspace. Preserve
owner changes in dirty trees. For updates, create a backup reference and a
temporary integration branch, merge upstream semantically, run focused owning
tests and one proportional build, then verify rendered behavior. Deploy one
recoverable full node first, perform install/connect/message/full stream/reopen,
Bots/Sessions/pop-out, model selection, reconnect and bounded two-turn A2A
acceptance as applicable, then deploy the same verified source/package to the
second node. Rebuild Android only for native/Capacitor/resource/DOM-contract
changes. Keep a rollback artifact until both nodes pass.

For diagnosis, prove the cause and report it without implementing a change unless
the request includes a fix. For implementation, make the narrowest change and
verify it. Do not commit, push, alter remotes, force-update history, delete state,
or deploy to another machine without the owner's explicit authority. Record a
public-safe incident/milestone using docs/yatima/INCIDENT_TEMPLATE.md.

Your concrete task for this first turn is handoff ingestion. Use the available
filesystem tools now to read every named file completely; do not merely promise
to read them, and do not ask me to restate the project or provide a feature
request. Perform a read-only inventory of the checkout with `git status
--short`, current branch/HEAD, remotes, and local upstream divergence. Do not
fetch, edit, build, restart services, connect to another computer, commit, push,
or deploy during this ingestion turn.

Then return a compact readiness report containing: the source-of-truth branch
and recorded checkpoint; the Windows/Ubuntu/Android architecture; critical
behavior and security invariants; the difference between repository, build and
deployment state; known unfinished/risky areas; and the safe first steps for a
future task. Explicitly confirm which files you actually read and that you made
no changes. After that, wait for my next request.
```
