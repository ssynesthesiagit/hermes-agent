# Safe upstream updates and recovery

Treat source integration, build/package, deployment and runtime migration as
four separate checkpoints. Never merge a new Nous release directly inside a
running Hermes installation.

## Branch policy

- `main` follows `NousResearch/hermes-agent` and should remain easy to update.
- `codex/yatima-custom` is the last owner-verified customization checkpoint.
- `codex/yatima-upstream-integration-<date>` is disposable integration work.
- `backup/yatima-custom-before-<date>` preserves the pre-integration source.
- No force pushes, history rewrites, blind conflict selection, or automatic
  deployment from an unverified merge.

At the 2026-08-23 checkpoint, the custom branch was 4 commits ahead and 201
commits behind the fetched `upstream/main`. That number will change; always
measure it again rather than assuming the handoff is current.

## Stage 0: authority and inventory

Before mutation:

1. Confirm whether the owner requested investigation, integration, deployment,
   commit and/or push. One does not imply all the others.
2. Record repository remotes, branch, commit and dirty state.
3. Record the installed Windows and Ubuntu versions/commits and preserve dirty
   owner files.
4. Record the Android standalone/fork checkpoint if native code is involved.
5. Check both gateways and perform a pre-update smoke test.
6. Create supported configuration/state backups outside the repository and
   verify that the backup exists and is non-empty.

If backup creation fails, do not deploy. Source investigation and integration
may continue in a separate clean checkout if it does not touch the live install.

## Stage 1: isolated source integration

```bash
git fetch origin --prune
git fetch upstream --prune
git switch codex/yatima-custom
git status --short
git branch backup/yatima-custom-before-<date>
git switch -c codex/yatima-upstream-integration-<date>
git merge --no-commit upstream/main
```

Review every conflict semantically. High-risk areas include canonical Bot Chat,
profile routing, session resume/projection, secondary windows, provider catalogs,
gateway boot, hosted Bots/mobile UI, A2A tools and Android-hosted DOM contracts.

Do not resolve by choosing all `ours` or all `theirs`. New upstream code may be
required for correctness while the custom behavior must still be re-expressed
against its new interfaces.

## Stage 2: source verification

Run focused tests while resolving each concrete failure, then one owning pass:

- Python local and existing A2A suites;
- canonical Bot Chat and Bots roster plugin tests;
- profile-routing and session-action UI tests;
- Electron secondary-session window tests;
- model/provider catalog tests;
- desktop/electron/e2e TypeScript checks;
- desktop production build;
- Android unit/build checks only if native Android or its hosted DOM contract
  changed.

Use commands from `OPERATIONS_RUNBOOK.md`. Record exact pass/fail counts and
explain skipped or unrelated failures. Do not call a failing broad suite green
merely because focused tests pass.

## Stage 3: staged package and first-node deployment

1. Build/package outside the live installation.
2. Preserve the exact artifact/source commit and checksum where practical.
3. Stop the first test node cleanly.
4. Deploy without overwriting profiles, credentials, sessions, memory or logs.
5. Start it and run the full minimum acceptance in `HANDOFF.md`.
6. Keep the second node on the old verified version until the first passes.

Choose the first node based on recoverability and owner availability, not
convenience. A hosted UI deployment is per gateway; Android does not propagate
it to the other gateway.

## Stage 4: second node and Android

Deploy the identical verified desktop/gateway source or package to the second
node and repeat the smoke test. Rebuild/reinstall Android only when native code,
resources, Capacitor configuration or the native-to-hosted DOM contract changed.

Verify gateway switching after both full nodes are healthy. Each should retain
its own authentication and canonical Bot Chats.

## Abort before deployment

If the integration is not viable:

```bash
git merge --abort
git switch codex/yatima-custom
```

If conflicts were already committed only on the integration branch, keep or
delete that branch deliberately after recording useful findings. The running
installations should be unchanged.

## Roll back after deployment

1. Stop the affected app/service cleanly and preserve failure evidence.
2. Restore the pre-update package/source and supported configuration/state
   backup. Do not mix a new binary with partially migrated old source.
3. Start the gateway and verify login, canonical Bot Chat, one complete streamed
   response and reopen.
4. Restore the other node only if it was actually changed.
5. Retain the failed integration branch and public-safe incident report until
   the cause is understood.

Never use destructive Git reset/clean operations on a dirty installation tree.
Never delete sessions or profile databases to make a UI symptom disappear.

## Promoting a successful integration

Only after both full nodes and any required APK pass acceptance:

1. update `CURRENT_STATUS.md`, `HANDOFF.md` and feature-specific notes;
2. record validation and rollback references;
3. request owner approval for commit/push if not already granted;
4. push the verified custom branch normally, without force;
5. verify the remote branch SHA matches the local SHA and the working tree is
   clean.

