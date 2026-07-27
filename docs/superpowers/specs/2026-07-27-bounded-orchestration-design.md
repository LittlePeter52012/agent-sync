# Bounded Orchestration Design

**Date:** 2026-07-27

## Goal

Keep Codex and Multica orchestration usable for quality-sensitive work while
preventing recursive fan-out, expensive default helper settings, rapid
model-mediated polling, and stale cross-product hook state.

## Evidence

The C31 Codex task used 27 spawned agents, 229 agent waits, 948 tool calls, and
nine context compactions. Its session consumed two rate-limit windows. The
session did not contain an OMO or Multica activation directive, so the primary
failure was the workflow shape rather than a billing-counter defect. Codex
still retained trusted OMO hook-state entries after the OpenCode-only plugin
was removed, and the existing doctor did not report them.

## Considered Approaches

1. Disable multi-agent support. This prevents fan-out but removes a useful
   quality tool.
2. Add prose guidance only. This preserves flexibility but cannot detect
   configuration drift.
3. Use bounded orchestration. Keep the capability, add hard Codex defaults,
   synchronized operating rules, Multica delegation limits, and doctor/fix
   enforcement.

Use approach 3.

## Design

The private Agent Hub owns desired orchestration policy. The public
`agent-sync` tool reads that policy without embedding personal values.

For Codex:

- enable agents, but cap concurrent spawned threads at three;
- default helpers to `gpt-5.6-terra` with `low` reasoning;
- reserve an explicit stronger model or reasoning override for one
  manuscript-critical reviewer;
- remove configured forbidden plugins and their stale trusted hook-state
  sections;
- report drift without revealing unrelated configuration or secrets.

For behavior across agents:

- single-main-agent is the default;
- delegate only bounded independent work;
- no recursive delegation;
- no short model-mediated wait loops;
- stop new delegation when surfaced rate-limit use jumps abnormally.

For Multica:

- use one delegate by default and at most two active delegates without a fresh
  explicit user request;
- only the squad leader may delegate;
- retry quota, authentication, or provider failures at most once;
- keep one final writer and use deterministic checks for quality.

## Verification

- focused unit tests fail before doctor/reconciler support exists;
- reconciler dry-run is non-mutating;
- apply removes only policy-forbidden plugin and hook sections, preserves
  unrelated hook state and secrets, and converges idempotently;
- `agent-sync test`, `agent-sync rules`, `agent-sync multica --apply`,
  `agent-sync verify --strict`, and a final doctor run pass.
