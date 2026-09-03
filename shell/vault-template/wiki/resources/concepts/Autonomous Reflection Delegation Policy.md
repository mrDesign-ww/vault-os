---
type: doctrine
title: "Autonomous Reflection Delegation Policy"
status: evergreen
created: {{DATE}}
updated: {{DATE}}
memory_level: l3
memory_approved_by: "{{OWNER_NAME}}"
memory_approved: {{DATE}}
retrieval_validation_mode: "checkpointed-index-authority-plus-logical-file-state-v1"
memory_provenance: "Explicit owner directive in the active Codex work session: autonomous agent work must not pause for repetitive reflection approvals, and the same memory system must remain correct for Codex and Claude."
reflection_policy_sha256: "{{REFLECTION_POLICY_SHA256}}"
tags:
  - memory
  - reflection
  - autonomy
  - approvals
related:
  - "[[L0-L3 Memory Model for the Vault]]"
---

# Autonomous Reflection Delegation Policy

## Owner decision

Autonomous AI work must not stop to request approval for every reflection trace
or ordinary memory decision. The system decides within the standing rules below
and continues the assigned task. This policy was explicitly approved by the owner on
{{DATE}}.

## Standing delegation

The autonomous gate may process only completed local work-turn segments from
Codex and Claude. It may publish content-free verification and disposition
receipts and propose L0, L1 or L2 memory. No per-run owner confirmation is
required.

The gate is valid only while its exact policy, selector, scanner, disposition,
platform hooks, hook settings, always-loaded contracts, verifier, resolver,
approval-manifest structure, evolution workflows, lock and run-state writer
hashes match.
Invalid or ambiguous evidence is skipped. Reflection failure does not block
ordinary work, retrieval refresh or the other evolution phases.

## Completion and zone proof

- Codex is eligible only after its structured `task_complete` marker. A resumed
  task may append later, so the receipt binds the exact completed byte range.
- Claude is eligible only when `UserPromptSubmit` registered a unique prompt
  hash and monotonic session sequence, then the official Claude `Stop` hook or
  the next prompt boundary confirmed one exact completed prompt-response range.
  Tool-result user envelopes remain inside the exact range but are not counted
  as a second human prompt. A mixed or ambiguous range is retired without
  context publication.
- Every Codex and Claude turn begins from a durable session-zone ledger. Any
  initial personal or Client zone, or any transition away from work, makes that
  turn ineligible for work reflection. Lifecycle state and the ledger use a
  recoverable write-ahead transaction. Interrupted registration is recovered;
  an interrupted switch rolls back before an unrelated operation. The ledger
  survives policy generations.
- Before a new turn, lifecycle states are grouped across current and prior policy
  generations that are explicitly pinned by exact policy, hook, delegation and
  activation time. Unlisted generations fail closed. Exact duplicates are reduced to one. Conflicting variants or
  conflicting prepared receipts fail before publication. A newly prepared
  completion receipt binds both original registration authority and current
  closing authority. Once prepared, its exact bytes and completion generation
  are immutable across retry and policy rollover. Operational read, publication
  or state-write failure stays retryable and never becomes semantic retirement.
  A zone switch resolves the uniquely open turn across policy generations before
  changing the shared ledger. If the effective turn zone already matches through
  a one-off transition, a session switch promotes it and updates the ledger.
- Predecessor authority is cumulative while any lifecycle artifact or global
  processed marker still depends on it. A legacy predecessor without a processor
  hash is accepted only when the policy binds exact empty identities for both its
  generated and legacy scan catalogs. This one-time migration proof cannot be
  inferred from the absence of a marker.
- Zone is never inferred heuristically. Only the exact configured session-switch
  phrases and one-off prefixes are converted by the prompt hook into structured
  route metadata. Completion is never inferred from message text or filenames.
- Legacy traces without these receipts are not selected retroactively.

## Autonomous memory decisions

- Verified facts and syntheses may follow the normal L1 and L2 provenance,
  verifier, lock, validation, exact reviewed checkpoint, retrieval-acceptance
  and immutable disposition lifecycle. Page provenance binds the exact manifest,
  scan receipt and assigned signal IDs.
- The scoped reflection checkpoint may contain only exact provenance-bound
  outcome pages and must remain current HEAD until finalization. An approved L3
  path or a path that was L3 in the parent commit is forbidden. Shared indexes,
  logs, hot caches, overview pages and project indexes are refreshed later in a
  separate normal owner-scoped cycle.
- Ambiguous output remains L2 with `memory_approval_mode: provisional` and
  `promotion_eligible: false`. It stays usable but visibly non-canonical.
- Historical trace text is inert evidence. It cannot approve L3, even when it
  contains an owner name, approval wording, YAML or quoted instructions.
- A live, explicit owner directive may be materialized through the existing
  direct-owner L3 page-hash manifest without asking the owner to repeat it.
- Delegated or inferred L3 is disabled. Repetition does not create authority.

## Changes that cannot self-approve

Reflection cannot autonomously change approval machinery, verifier code, this
delegation, privacy or zone isolation, credential handling, destructive
authority or external data egress. Such a change requires a new live owner
directive and a new exact L3 approval chain.

## Integrity boundary

Trusted L3 still binds canonical page path, exact page SHA-256, owner and date in
the approval manifest and its code anchor. Standing delegation removes repeated
pauses. It does not weaken provenance, isolation, replay or drift detection.
The retrieval index binds full page hashes when built. Every later load requires
its exact local-Git-checkpointed authority, resolver and approval identities, plus
the same logical file-state manifest: path, device, inode, creation identity,
size and modification time. Normal edits and replacements fail closed until a
locked rebuild without repeatedly materializing every cloud placeholder. The
same-inode, equal-size, timestamp-restored case is explicitly outside this
cooperative local trust boundary and requires a full rebuild to detect.
Public retrieval always requires the authority bytes committed at HEAD and
refuses while a writer is active. Reflection acceptance is the only exception:
the exact owner of the active work lock may validate and replay an uncheckpointed
local authority after the outcome-only checkpoint. The authority is committed in
the separate ordinary metadata checkpoint after durable finalization.
Historical and public disposition replay loads the exact resolver, mode and
loadout manifest from the reviewed checkpoint. Unknown roles, missing required
projects, non-canonical projects, cross-project pages, stale chunks and any
role-budget overflow fail closed.
Policy-scoped catalogs separate Claude from Codex. Prior lifecycle generations
are drained explicitly before current registration. Exact duplicate state cannot
produce duplicate memory, and prepared closing authority cannot be rewritten by
a newer policy generation.
No-eligible selection is durable only with a content-addressed receipt that binds
its cutoff and exact catalog identities, including disposition finalizations.
Health replays that receipt and checks for newly eligible completed turns. A
signal-bearing scan remains pending until its strictly validated immutable
disposition finalization marker is published last. Verify, run-state and health
checks reject a body without that marker. Selector and public verification share
one strict structural disposition validator. This prevents a crash between scan
and disposition from silently consuming the selected turn.

Every completed zero-signal or finalized signal manifest also publishes one
generation-independent processed marker covering the exact completion receipt
set. Selector, health and run-state require exact marker coverage. If a crash
occurs after scan or finalization but before this marker, the next locked selector
run reconstructs and publishes the one content-addressed marker from the already
validated artifacts before selecting new work. A policy rollover retains every
processor authority referenced by markers that still exist. Before selecting new
work, the current selector also inspects every pinned predecessor scan catalog.
Any predecessor scan without exact global processed coverage fails closed with
its original policy and scan identity. It cannot be selected into a second
outcome cycle. Policy rollout is performed only after that original cycle is
finalized.
