# Yatima Hermes engineering handoff

This is the canonical cold-start guide for the customized Hermes fork. It is
written so a new coding task can diagnose, update, validate, deploy, or recover
the project without relying on chat history.

## Source of truth

- Repository: `https://github.com/ssynesthesiagit/hermes-agent`
- Custom branch: `codex/yatima-custom`
- Verified v0.21.0 integration branch: `codex/yatima-v0.21-integration`
- Verified integration code checkpoint before documentation:
  `8d2f55841`
- Checkpoint when this handoff was written:
  `e3f287e9af3b59a9d71cf80b0f4ea1f272aceb7e`
- Upstream remote: `https://github.com/NousResearch/hermes-agent.git`
- Upstream integration must happen on a temporary branch, never directly in a
  live installation.

The integration branch contains the official v0.21.0 release plus bounded
post-release security, auth, messaging, updater, cache-observability, and
compression fixes. It is source-verified but not deployed. Read
`INTEGRATION_2026-09-02_HERMES_V0210.md` before merging or installing it.

The checkpoint is the source of truth for the customized Windows and Linux
desktop/gateway code, the hosted mobile UI, local-profile A2A routing, and the
native Android client under `apps/android/`. Runtime profiles, credentials,
sessions, memory, logs, installed packages, and model weights remain local to
each machine and are not repository content.

## System shape

| Component | Responsibility | Important boundary |
| --- | --- | --- |
| Windows Hermes | Full desktop, gateway, hosted mobile UI, profiles and bots | A full node; preserve local state and authentication |
| Ubuntu Hermes | Separate full desktop/gateway with its own profiles and bots | A separate node; updating Windows does not update Ubuntu |
| Android client | Thin Capacitor/WebView shell with secure gateway picker and native speech controls | Not a Hermes node; it connects to one saved Tailscale gateway at a time |
| Hosted mobile UI | Responsive Bots, sessions and chat experience served by each Hermes gateway | UI fixes usually deploy with Hermes, not with the APK |
| Canonical Bot Chat | One durable hidden `Bot Chat` per profile shared by desktop, phone and local A2A | Never create an unrelated mobile-only chat by default |
| Local-profile A2A | Bounded `a2a_call(agent="profile:<name>")` routing on one machine | Same-machine only; no automatic infinite bot loops |

The Android app may save both computers, but it is active on only one gateway at
a time. Each origin retains its own cookies and storage, so switching gateways
does not erase the other login or its sessions.

## Product invariants

Preserve these unless the owner explicitly changes scope:

1. Hermes remains the backend and authentication authority. Do not port the
   runtime into Android or introduce a second backend.
2. Network access is Tailscale-only. The Android URL policy accepts the
   Tailscale IPv4 range, Tailscale IPv6 range, and valid `.ts.net` names only.
3. A phone message to a bot uses that profile's canonical `Bot Chat`, so the
   same conversation is visible from desktop history.
4. Streaming, reconnection, existing sessions, files/photos, Markdown, copy,
   dictation, full-response TTS and multi-gateway switching must continue to
   work together.
5. Model catalogs may contain Codex OAuth, Nous and local/custom providers at
   the same time. Do not solve one-provider bugs by filtering out another.
6. Profiles retain separate configuration, sessions, memory, tools and audit
   records. Local A2A addresses a target profile without flattening isolation.
7. Bot-to-bot routing stays bounded: keep self/cycle/depth rejection, signed
   route state, timeouts and per-conversation single-flight behavior.
8. Desktop pop-outs are views of the same canonical chat, not new sessions.
9. The app does not promise offline mode, push infrastructure, a local phone
   model, or default-assistant integration.
10. Never commit secrets, private configuration, sessions, memory, logs, model
    weights, recovery archives, signing material, APKs or installed build output.

## Important code areas

| Surface | Primary locations |
| --- | --- |
| Native Android shell | `apps/android/` |
| Android gateway policy/storage | `apps/android/android/app/src/main/java/com/hermes/gatewayclient/` |
| Android speech | `HermesSpeechController.java`, `SpeechPageScript.java`, `SpeechChunker.java` |
| Android launcher assets | `apps/android/tools/` and Android `mipmap-*` resources |
| Hosted Bots/mobile UI | `apps/desktop/src/plugins/hermes-bots/` |
| Canonical profile routing | `apps/desktop/src/sdk/index.ts`, `profile-routing.test.ts` |
| Desktop session actions/transcript projection | `apps/desktop/src/app/session/` |
| Secondary native windows | `apps/desktop/electron/session-windows.ts` and tests |
| Gateway boot/reconnect | `apps/desktop/src/app/gateway/hooks/use-gateway-boot.ts` |
| Provider/model catalog | desktop composer/model catalog and gateway model option code |
| Local-profile A2A | `plugins/platforms/a2a/local_profile.py`, `tools.py` |
| A2A regression coverage | `tests/plugins/test_a2a_local_profile.py`, `test_a2a_plugin.py` |

Search for owning symbols and tests before editing; do not assume this table is
exhaustive after an upstream merge.

## Current verified behavior

- The Android unit suite and debug APK assembly pass from a clean dependency
  install after Capacitor sync.
- The no-face Athena adaptive launcher icon is included.
- Focused local-profile A2A, canonical Bot Chat, roster, session-window,
  mobile-web and mixed-model tests passed at the recorded checkpoint.
- Desktop and mobile-web TypeScript checks passed at the recorded checkpoint.
- The secondary window source includes eager REST transcript paint before a
  slow runtime resume; the real packaged Windows acceptance gate must still be
  rechecked after future desktop packaging or upstream integration.

See [CURRENT_STATUS.md](CURRENT_STATUS.md) for exact evidence and known limits.

## How to approach any new task

1. Read this file, `CURRENT_STATUS.md`, `OPERATIONS_RUNBOOK.md`,
   `TROUBLESHOOTING.md`, `UPDATE_AND_RECOVERY.md`, and the feature-specific A2A
   handoff before changing code.
2. Inspect the actual branch, remotes, dirty state, runtime health and deployed
   version. Documentation is a checkpoint, not proof that a machine still
   matches it.
3. Classify the request as diagnosis, source implementation, packaging,
   deployment, or recovery. Do not expand one into another without authority.
4. Preserve owner changes in dirty installation trees. Prefer a clean staging
   checkout for integration and builds.
5. Make the narrowest source change and run the owning tests first.
6. Run one proportional integration/build pass, then a real rendered smoke test
   for owner-facing UI changes.
7. Deploy one full node first, verify it, then deploy the same artifact/source
   to the other node. Rebuild the APK only when native Android code/assets
   changed.
8. Record a public-safe incident or milestone using `INCIDENT_TEMPLATE.md`.
9. Do not commit or push unless the owner explicitly requests it. Never force
   push or rewrite the verified branch.

## Minimum cross-platform acceptance

For a change that can affect normal operation, verify in order:

1. launch or service start;
2. Tailscale gateway reachability and authenticated login;
3. select a bot and open its canonical Bot Chat;
4. send a message and observe the entire streamed response;
5. reopen the conversation after sleep/restart;
6. switch Android to the other saved gateway and back;
7. open Sessions and a secondary Bot Chat window on desktop;
8. attach a harmless file/photo if upload code changed;
9. exercise dictation, copy and full-response TTS if mobile presentation changed;
10. run a two-turn local-profile A2A call with the same `context_id` if A2A,
    profiles, sessions or canonical routing changed.

Do not report a platform as deployed merely because its source exists in the
fork. Repository state, built artifacts and live installations are three
separate facts.
