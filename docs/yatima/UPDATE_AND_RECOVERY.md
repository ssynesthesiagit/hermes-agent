# Safe upstream updates and recovery

The customized branch must not be updated in place inside a running Hermes
installation. Treat upstream integration and deployment as separate stages.

## Branch policy

- `main` tracks `NousResearch/hermes-agent` as closely as possible.
- `codex/yatima-custom` is the last verified customization checkpoint.
- Use a temporary `codex/yatima-upstream-integration` branch for each upstream
  merge.
- Create a dated backup branch/tag before integration and retain it until both
  Windows and Ubuntu pass smoke tests.

## Integration checkpoint

```bash
git fetch upstream
git switch codex/yatima-custom
git branch backup/yatima-custom-before-update
git switch -c codex/yatima-upstream-integration
git merge upstream/main
```

Resolve conflicts on the integration branch. Never solve a conflict by blindly
choosing all upstream or all customized files; confirm the behavior represented
by each side.

## Owning verification

Run checks proportional to the touched code, including the focused A2A,
canonical chat, mobile-web, model-picker, secondary-window, and TypeScript suites
when those surfaces are affected. Then build/package in a staging location.

Before deployment, prove at minimum:

1. install or launch succeeds;
2. authenticated gateway connection succeeds;
3. normal and canonical Bot Chat messages stream fully;
4. the conversation reopens after application restart;
5. gateway sleep/reconnect recovers;
6. Bots, Sessions, mixed model choices, and secondary windows still work;
7. one bounded two-turn local-profile A2A call preserves context and appears in
   the target profile's Bot Chat.

Verify one machine first. Deploy the same verified source/package to the second
machine only after the first passes.

## Abort before deployment

If a merge is active and integration is not viable:

```bash
git merge --abort
git switch codex/yatima-custom
```

The running installation should be unchanged because integration happened in a
separate checkout/branch.

## Roll back after deployment

1. Stop the affected Hermes application/gateway cleanly.
2. Restore the pre-update application/source package and configuration backup.
3. Start the gateway and verify login, sessions, canonical Bot Chat, and one
   streamed response before restoring normal use.
4. Preserve the failed integration branch and public-safe logs until the root
   cause is documented.

Never commit a backup archive, `.env`, token, cookie, session database, memory,
profile state, model weight, installer, APK, or code-signing material.

