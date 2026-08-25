# Yatima Hermes fork

This directory is the durable engineering handoff for the customized Hermes
fork. The source and the documentation live together on
`codex/yatima-custom`; the fork's `main` branch stays suitable for tracking
upstream NousResearch Hermes.

## Read in this order

1. [Canonical engineering handoff](HANDOFF.md)
2. [Current status](CURRENT_STATUS.md)
3. [Operations runbook](OPERATIONS_RUNBOOK.md)
4. [Troubleshooting guide](TROUBLESHOOTING.md)
5. [Safe upstream updates and recovery](UPDATE_AND_RECOVERY.md)
6. [Local-profile A2A routing](A2A_LOCAL_PROFILE_ROUTING.md)
7. [Fresh-task prompt](FRESH_CHAT_PROMPT.md)
8. [Incident/fix template](INCIDENT_TEMPLATE.md)
9. [`YATIMA_CUSTOM.md`](../../YATIMA_CUSTOM.md) at the repository root

## Repository boundaries

This public fork contains Hermes gateway, desktop, mobile-web, native Android,
A2A source, tests, and public-safe operational documentation. The Android
Capacitor project lives at [`apps/android/`](../../apps/android/). It
intentionally excludes:

- credentials, tokens, cookies, and private configuration;
- sessions, memories, profile databases, logs, and audit data;
- model weights, environments, dependencies, installers, and build output;
- recovery archives and machine-specific filesystem paths.

The Android application remains a thin native/WebView client with its own build,
signing, and release lifecycle, but its public-safe source is checkpointed in
this fork alongside the hosted Hermes mobile UI and gateway contract. Signing
keys and generated APKs are deliberately excluded.

Machine-specific addresses, account names, installed paths and live service
details belong in the owner's private local handoff, not this public directory.
