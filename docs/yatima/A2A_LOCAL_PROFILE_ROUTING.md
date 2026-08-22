# Local-profile A2A routing

Recorded 2026-08-22. This is the implementation, safety, validation, and
recovery handoff for same-machine bot-to-bot routing in the Yatima Hermes fork.

## Original failure

Several Hermes features resembled local A2A support but did not create a usable
same-machine route:

1. Enabling the `a2a` toolset only enabled outbound A2A tools for a profile.
2. A target profile did not automatically run an inbound A2A server or publish
   an Agent Card.
3. Profiles isolate configuration, sessions, memory, and runtime state. Existing
   peer lookup knew configured HTTP endpoints, not local Hermes profiles.
4. A session could discover another profile but could not address that profile's
   canonical Bot Chat, preserve a context, or expose the exchange in the target
   profile's history without a second process and TCP port.

The key distinction is that an enabled outbound A2A toolset does not make every
local profile an inbound A2A service.

## Interface

The existing `a2a_call` tool recognizes an explicit local target:

```text
a2a_call(
  agent="profile:yatima-training",
  message="Reply exactly: YATIMA_TRAINING_REACHABLE"
)
```

The response identifies the target profile, reusable `context_id`, durable
target session, and assistant reply. Reuse the returned context for another turn:

```text
a2a_call(
  agent="profile:yatima-training",
  message="What exact phrase did you send in the previous turn?",
  context_id="local-a2a-v1.…"
)
```

## Implementation

### `plugins/platforms/a2a/local_profile.py`

- Only `profile:<name>` selects the local route.
- Profile names are normalized, validated, and resolved through
  `hermes_cli.profiles`.
- The active caller profile is detected and cannot route to itself.
- Calls reuse Hermes's `tui_gateway.server.handle_request` RPC handlers. No
  additional gateway, Agent Card, socket, port, or global `HERMES_HOME`
  mutation is required.
- The router resumes the target profile's `hermes-bots.chat` pin. If missing or
  stale, it adopts the newest `Bot Chat`; if none exists, it creates one.
- A new/adopted session is pinned only after a durable assistant turn succeeds.
- `local-a2a-v1` context binds target profile and stored session identity. It is
  validated against the requested profile and canonical conversation.
- Context survives process restart and accepts session-ID rotation caused by
  compression only when both IDs resolve to the same durable lineage.
- The message identifies the caller and carries a process-signed recursion
  marker.
- The router waits for one target response, records audit entries, persists both
  sides of the A2A exchange, and increments existing A2A metrics.

### `plugins/platforms/a2a/tools.py`

`a2a_call` checks `profile:` before using the configured-peer HTTP path. Local
calls delegate to `local_profile.route`; remote HTTP peers are unchanged.

### Tests

`tests/plugins/test_a2a_local_profile.py` covers creation, pinning, context
reuse, compression lineage, process restart, forged/stale context rejection,
profile isolation, self/cycle/depth rejection, timeouts, lock release, and
preservation of remote HTTP routing. `tests/plugins/test_a2a_plugin.py` retains
the wider A2A behavior checks.

## Safety boundaries

- Maximum local route depth: 3.
- A signed marker rejects forged route state, cycles, and revisiting a profile.
- A per-profile/session single-flight lock prevents interleaved turns in one Bot
  Chat and releases on success or failure.
- The normal response timeout begins at 180 seconds and has a 1200-second hard
  ceiling.
- Empty messages, unknown profiles, self-routes, stale/mismatched contexts,
  invalid sessions, and busy conversations fail safely.
- Existing redaction, audit records, persistence, and metrics remain active.
- Profile configuration, memory, tool permissions, and databases remain
  isolated; only the addressed profile's canonical Bot Chat receives the turn.
- Existing remote A2A peers continue to use the original HTTP transport.

These constraints deliberately prevent unbounded autonomous bot loops. A
multi-bot experiment or scheduler remains an explicit caller responsibility.

## Acceptance evidence

The owning suite includes coverage for:

- local creation, pinning, and second-turn context reuse;
- compression lineage and process restart;
- forged and unrelated session rejection;
- adoption of the newest canonical Bot Chat;
- self, cycle, depth, and forged-marker rejection;
- timeout limits and single-flight release;
- unchanged configured-peer HTTP routing.

Focused validation at the synchronized checkpoint:

```text
python -m pytest tests/plugins/test_a2a_local_profile.py tests/plugins/test_a2a_plugin.py -q
115 passed, 10 deselected in 63.36s
```

The owner also observed a successful live two-profile exchange on Ubuntu on
2026-08-22. Future incident records must include the exact public-safe error,
root cause, changed files, commands/tests, live result, and rollback reference;
do not reconstruct missing evidence from memory.

## Known limits

- Local-profile routing is same-machine only. Cross-machine bots still require
  approved remote A2A transport.
- Each call synchronously waits for one target assistant response.
- Only the canonical Bot Chat is reused; this is not arbitrary-session remote
  control.
- Deleting or replacing the canonical conversation can invalidate an old
  context unless Hermes can prove durable lineage.
- This enables bounded calls, not an infinite coworker loop or meeting scheduler.

## Recovery

1. Stop issuing new local calls and preserve the exact tool error plus
   caller/target profile names. Do not publish credentials or session contents.
2. Restore the last verified customization checkpoint or the deployment's
   pre-update backup.
3. Reverting the local-profile routing change removes `local_profile.py` and
   restores the prior `tools.py`; remote HTTP A2A remains independent.
4. Restart only the affected Hermes gateway after restoring source/package
   files.
5. Rerun the focused A2A tests, then perform the exact two-turn live acceptance
   with one disposable target Bot Chat message.

