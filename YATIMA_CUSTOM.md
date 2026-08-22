# Yatima Hermes customization

This branch is the cross-machine source checkpoint for the Yatima Hermes desktop,
gateway, and mobile-web customizations. It intentionally contains source and tests
only. Runtime configuration, credentials, sessions, logs, packaged applications,
and machine-specific files must never be committed here.

## Branch layout

- `main` follows `NousResearch/hermes-agent` and should remain easy to fast-forward.
- `codex/yatima-custom` contains the Yatima customization set.
- The customization branch currently starts from upstream commit
  `8286c46502e1f59eafdfabcf5af998024f64dfb8`.

This first synchronized commit is a development checkpoint, not a release tag.
Some desktop pop-out chat behavior is still undergoing final verification.

## Documentation

- [Current implementation status](docs/yatima/CURRENT_STATUS.md)
- [Local-profile A2A routing](docs/yatima/A2A_LOCAL_PROFILE_ROUTING.md)
- [Safe upstream updates and recovery](docs/yatima/UPDATE_AND_RECOVERY.md)
- [Fresh Codex task handoff](docs/yatima/FRESH_CHAT_PROMPT.md)
- [Documentation index](docs/yatima/README.md)

## Checkpoint validation

The synchronized source was byte-compared with the 35 intended working-tree
source/test files before commit. The following focused checks passed on
2026-08-22:

- Local-profile plus existing A2A tests: 115 passed, 10 deselected.
- Canonical bot-chat and roster tests: 40 passed.
- Electron secondary-session window tests: 24 passed.
- Mobile-web owning tests: 45 passed across serial reruns.
- Mixed Codex CLI/model-picker tests: 4 passed.
- Desktop and mobile-web TypeScript typechecks.

The broad desktop plugin sweep passed 396 of 398 tests. Its two failures were in
untouched delegated-routine shell tests where the Windows subprocess returned a
null exit status. They are retained as a known non-owning environment issue.

## Included work

- Mobile WebView-oriented Hermes chat and bot-management UI.
- Canonical per-bot chat routing shared with desktop session history.
- Desktop bot roster, session navigation, and secondary chat-window support.
- Gateway reconnect/boot hardening used by desktop and mobile clients.
- Model-picker compatibility work for mixed provider choices.
- Local-profile A2A routing work and its regression tests.

## Clone on another computer

```bash
git clone --branch codex/yatima-custom \
  https://github.com/ssynesthesiagit/hermes-agent.git
cd hermes-agent
git remote add upstream https://github.com/NousResearch/hermes-agent.git
git fetch upstream
```

Do not copy a live Hermes configuration directory into this checkout. Configure
credentials and gateway settings through Hermes on each computer.

## Safe upstream integration

Do not merge a new upstream release directly into a running installation. Use an
integration branch and keep a rollback reference first:

```bash
git fetch upstream
git switch codex/yatima-custom
git branch backup/yatima-custom-before-update
git switch -c codex/yatima-upstream-integration
git merge upstream/main
```

Resolve conflicts on the integration branch, then run the relevant Python, web,
and desktop tests plus production builds. Smoke-test bot selection, canonical bot
chat, session reopening, streamed responses, gateway reconnection, and secondary
chat windows before deploying the integration branch.

If integration fails before a deployment, run `git merge --abort` while a merge is
active, then switch back to `codex/yatima-custom`. Keep the backup branch until the
updated application has passed real Windows and Ubuntu smoke tests.

## Recording fixes

For any cross-machine failure, record the public-safe error, root cause, changed
files, validation commands, deployment result, and rollback instructions under
`docs/yatima/`. Never paste access tokens, passwords, cookies, private
configuration, session contents, memories, or machine-specific recovery paths
into this public repository.
