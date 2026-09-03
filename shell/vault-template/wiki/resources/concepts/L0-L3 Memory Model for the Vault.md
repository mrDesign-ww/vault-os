---
type: doctrine
title: "L0-L3 Memory Model for the Vault"
status: evergreen
created: {{DATE}}
updated: {{DATE}}
memory_level: l3
memory_approved_by: "{{OWNER_NAME}}"
memory_approved: {{DATE}}
retrieval_validation_mode: "checkpointed-index-authority-plus-logical-file-state-v1"
tags:
  - memory
  - l0-l3
  - retrieval
  - knowledge-management
  - operating-model
related:
  - "[[Autonomous Reflection Delegation Policy]]"
  - "[[resources/_index|Resources]]"
---

# L0-L3 Memory Model for the Vault

## Owner decision

Obsidian remains the only source of truth, project workspace and evidence archive.
L0-L3 is the trust, promotion and retrieval model inside the existing PARA tree.
It does not create a second database or four parallel folder structures.

This policy was explicitly approved by the owner on {{DATE}}.

## Levels

### L0: evidence

Raw sources, imported documents, media, transcripts, session captures, inbox items,
operation logs, `analysis/raw`, asset and QA evidence, and prototype internals.

- L0 preserves what was received or observed.
- L0 is not accepted as true only because it exists.
- Imported project sources remain immutable.
- Retrieval excludes L0 by default. `--include-l0` searches textual Markdown
  evidence. Non-Markdown JSON, PDF and media remain discoverable through project
  source links and provenance, then are inspected directly rather than silently
  parsed by the canonical retriever.

### L1: verified facts

Verified facts, owner decisions, constraints, audit findings and durable lessons.

- L1 must retain a source, evidence link or clear provenance in the page.
- New explicit L1 pages require `memory_provenance` in frontmatter.
- An ordinary durable project or resource page defaults to L1.
- A conflicting raw claim in L0 cannot override verified L1.
- Incorrect L1 is corrected explicitly or marked superseded, never silently hidden.

### L2: scenarios and project state

Project indexes, canonical state, architecture, specifications, scenarios,
playbooks, plans, handoff and reusable operating procedures.

- L2 synthesizes multiple L1 facts into a usable model or workflow.
- New explicit L2 pages use `memory_provenance`. Only an explicit L2 with this
  trail is labeled canonical by the resolver; older explicit L2 remains an
  `explicit-unverified` discovery candidate until verified.
- Current L2 is loaded before individual L1 pages.
- A newer L2 may supersede an older L2, but the old page remains auditable.
- Status determines whether a page belongs in the default loadout.

### L3: persona and policy

Stable owner-approved principles, privacy rules and cross-project operating doctrine.

- L3 is never inferred from a filename, path or page type.
- L3 requires `memory_level: l3`, the exact owner and ISO approval date.
- Approval also lives in `.vault-meta/memory-approvals.json` and binds the
  canonical page path to its SHA-256. Editing the page invalidates approval.
- Any new L3 rule or material L3 change requires explicit owner approval.
- L3 stays small. Temporary project preferences belong in L1 or L2.

## Cache and loadout

`hot.md` is a compiled working cache. It is not canonical memory and is not a
fifth level.

Every substantive task loads context in this order:

1. Approved L3 rules.
2. Active project or domain L2.
3. Query-relevant L1 facts and decisions.
4. L0 only when original evidence is required.

This order prevents a stale transcript, old implementation or raw source claim
from overriding the current project model.

## Promotion rules

### L0 to L1

Promote only after the claim is verified and provenance is preserved. Evidence
paths are permanently L0. Promotion creates a separate synthesis page outside
`sources/`, `raw/`, `inbox/`, evidence, asset, QA and prototype folders. A raw
page cannot promote itself by adding frontmatter.

### L1 to L2

Promote when verified facts form a canonical project state, repeatable scenario,
specification or workflow. Promotion requires synthesis, not copying.

### L2 to L3

Promote only when the rule is stable across projects and the owner explicitly approves
it. Evolution may prepare a proposal but cannot perform this promotion silently.

## Legacy pages

Old pages do not need a mass frontmatter rewrite. Inferred L1 and L2 are marked
`legacy-unverified` and ranked below explicit pages. Inference helps discovery,
but it is not verification or approval. The resolver uses this fallback:

- `sources/`, `incoming/`, `inbox/`, `analysis/raw/`, asset and QA evidence,
  prototype internals, raw types and `log.md` become L0;
- indexes, overview, architecture, specifications, playbooks, handoff, state and
  similar synthesis types become L2;
- other durable pages become L1;
- `hot.md` becomes cache;
- L3 remains explicit only.

Every new or substantively updated durable page must add `memory_level`.

## Retrieval behavior

Each zone owns a separate generated index under `.vault-meta/memory-index/`.
Work, personal and Client are never queried through one shared memory index.
The generated index is disposable and untrusted: every query verifies its full
payload against a small authority receipt committed in local Git. The receipt
binds the exact index bytes, resolver version and hash, approval manifest,
loadouts, page-hash snapshot and logical file-state manifest. A locked build is
the only operation that reads every canonical Markdown page and composes a fresh
full snapshot.
Build holds the shared active-zone write lock. Public retrieval refuses to answer
while a writer holds that lock and rechecks index identity before returning. One
internal reflection-acceptance path accepts the exact owner token for that same
lock. It validates the local authority without requiring it in HEAD, replays the
same result under the token and rechecks ownership before returning. This exists
only between an outcome-only checkpoint and durable finalization. Public Claude
Code and Codex retrieval still require checkpointed authority. Every lock acquire
and release advances a persistent zone generation; public retrieval also requires
that generation to remain unchanged for the whole query.

Ordinary retrieval compares path, device, inode, creation identity, size and
modification time without materializing cloud placeholders. This detects normal
edits, additions, deletions and replacements. It is a cooperative local-workflow
guard, not proof against an actor who rewrites the same inode with equal-length
bytes and restores its timestamp. Such an actor can also edit both code and
manifests and is outside the declared trust boundary. Full locked rebuilds remain
the content-hash authority.

Default retrieval excludes:

- L0, unless `--include-l0` is requested;
- pages and whole heading sections marked superseded, retired, archived,
  historical or do-not-implement;
- `hot.md`, because it is a cache.

Commands for the work zone:

```bash
/usr/bin/python3 scripts/memory-model.py scan --zone work
/usr/bin/python3 scripts/memory-model.py build --zone work
/usr/bin/python3 scripts/memory-model.py validate --zone work
/usr/bin/python3 scripts/memory-model.py retrieve "query" --zone work
/usr/bin/python3 scripts/memory-model.py retrieve "query" --zone work --project project-slug
/usr/bin/python3 scripts/memory-model.py retrieve "verify source" --zone work --include-l0
```

## Invariants

- Obsidian remains the only source of truth.
- No client data leaves the machine during indexing or retrieval.
- No automatic L3 promotion.
- No L0 self-promotion. Verified synthesis is a separate page.
- No trusted L3 without exact owner, ISO date and content-hash manifest approval.
- No cross-zone retrieval.
- No mass migration only to add metadata.
- Superseded knowledge remains auditable.
- Agent internal memories remain separate and are never synchronized directly.
