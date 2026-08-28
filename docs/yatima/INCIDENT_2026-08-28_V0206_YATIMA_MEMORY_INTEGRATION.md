# Incident record — Hermes v0.20.6 and Yatima memory integration

## Summary

- **Date:** 2026-08-28
- **Affected scope:** Hermes v0.20.6 integration with the Ubuntu Yatima local
  profile and its bounded evidence workflows.
- **User-visible symptom:** The upstream integration changed the effective
  history-pruning default and the candidate-memory merge required explicit
  ledger-integrity hardening before acceptance.
- **Current severity/status:** Resolved for the bounded gate; Stage 1 is
  enabled for Yatima only and Stage 2 remains inert accepted shadow
  infrastructure.

## Baseline

- **Source head:** `f48f8ecae24bfba8d91a844dd2f67dea4821e06a`.
- **Reference behavior:** deterministic tool-result pruning from the accepted
  integration baseline.
- **Runtime boundary:** Windows and Ubuntu remain separate full nodes; Android
  remains a thin Tailscale-only client.
- **Last verified checkpoint:** Stage 1 live canary PASS; Stage 2 mechanical
  canary PASS with zero model/provider calls and zero canonical writes.

## Reproduction and evidence

The bounded Stage 1 canary builds three HC2 integrated requests plus one short
regression request. It requires the exact source head, Yatima profile values,
strict eight-field JSON, stable listener correlation, and exactly four POST
attempts with no retry or repair path. The accepted run preserved `21/21`
protected fields and `3/3` required negative abstentions.

The Stage 2 package runs the candidate-memory focused suite and a disposable
stdlib-only synthetic ledger canary. It covers append-only prefixes, duplicate
choice B, crash/tail recovery, project/privacy isolation, source hash/revision
binding, derived view/index rebuild, four test-only shadow types, and a
dry-run proposal bridge. Its receipts report `model_calls: 0` and
`canonical_write_count: 0`.

## Root cause

The upstream history-pruning default differed from the accepted Yatima
behavior. The candidate-memory merge also needed explicit validation around
duplicate transaction ordering, truncated tails, receipt binding, and
read-only proposal behavior so a partial or tampered ledger could not be
accepted.

## Change

- Yatima pins the accepted pruning mode and thresholds in its profile; no
  Hermes source patch was required for Stage 1.
- `hermes_cli/candidate_memory.py` now enforces append-only, deterministic,
  source-bound, project/privacy-isolated shadow-ledger behavior and a
  fail-closed dry-run proposal boundary.
- The focused test suite and public-safe Yatima result packages record the
  source identity, decisions, accounting, and state-preservation evidence.
- **Android APK:** not rebuilt. No native, Capacitor, resource, or DOM
  contract changed.

## Verification

- Stage 1 sidecar tests: `11/11` PASS; live request accounting: exactly `4`.
- Stage 2 Hermes owning tests: `38/38` PASS; sidecar tests: `4/4` PASS; final
  canary checks: `10/10` PASS.
- No hosted provider, model, Librarian, canonical-memory, or live-session
  write was used by Stage 2.
- `git diff --check` and public-safe package checksum validation are required
  before publication.

## Deployment

No deploy, service restart, remote mutation, or branch operation was performed
for this record. The worktree remains intentionally uncommitted for owner
review.

## Recovery

The Yatima profile rollback receipt is ready and verified. Revert only the
Yatima-scoped profile change using the sealed rollback bytes; do not broaden
the change to other profiles or nodes. Stage 2 is disposable and can be
discarded without touching canonical memory.

## Repository action

This incident record is a public-safe repository artifact. It contains no
profile bytes, SOUL text, session content, machine-private paths, credentials,
or raw model responses.
