# {{VAULT_TITLE}}: LLM Wiki (PARA)

Mode: PARA (Projects / Areas / Resources / Archives)
Purpose: Central knowledge hub for {{OWNER_NAME}}. Drop a source, ask a question, and the knowledge compounds.
Owner: {{OWNER_NAME}}
Preferred language: {{LANGUAGE}}
Client zone label: {{CLIENT_LABEL}}
Created: {{DATE}}

> **Private vault.** This is client and personal work. Local git only, no remote, never push to a public host. Do NOT copy its contents into the public plugin repository.

## What this vault is

A persistent, compounding knowledge base using the `claude-obsidian` plugin at the installed `claude-obsidian` plugin plus vault-local Codex and Claude adapters. This vault is a separate private consumer. The wiki is the product: drop a source, ask a question, and the knowledge compounds.

## Codex vault operating model

Codex is a vault-native work partner, not a single-note memory bot. Treat the active Obsidian zone as Codex's durable memory in the same spirit as Claude.

Codex's usual role is design/product thinking: hypotheses, UX decisions, information architecture, Figma reasoning, critique, planning, and handoff-quality documentation. Claude often handles heavier manual implementation. This role split changes the kind of notes Codex creates, not the storage rules: useful Codex work belongs in the active zone's normal PARA pages.

When working in this vault, Codex must:

1. Start from the active zone. New sessions default to work. A zone switch changes the whole session until switched back.
2. At session start, run `EVOLUTION_PLATFORM=codex /usr/bin/python3 .vault-meta/evolution/health-check.py` once. Treat any output as a repair nudge. At the start of every turn in every zone, briefly acquire the shared work lock, run `/usr/bin/python3 .vault-meta/evolution/trace-session.py register --zone ACTIVE_ZONE --lock-token TOKEN`, and release the same token. Registration failure makes only that turn ineligible for future work reflection and never blocks its assigned work. Non-work registration is content-free lifecycle metadata and never makes personal or Client content eligible. Then read the active zone context before substantive answers: `hot.md` as a cache, approved L3 rules, the zone index and relevant L2 project state, relevant L1 facts, and L0 evidence only when verification is needed.
3. Route every durable output into the active zone's PARA tree. Do not keep project decisions only in `wiki/agent-memory/`.
4. If the user starts an ongoing effort and no project exists yet, create a project folder in the active zone:
   - work: `wiki/workspace/projects/<slug>/`
   - personal: `wiki/personal/projects/<slug>/`
   - Client: `wiki/client/projects/<slug>/`
   Add a project `_index.md` with required YAML frontmatter, then update the active zone's index and project catalog.
5. Save design discussions, hypotheses, decisions, UX models, task breakdowns, and handoff notes as normal project pages, or as resources if they are reusable across projects.
6. Put raw imported sources in the relevant project's `sources/` folder and treat them as immutable.
7. Use the active zone inbox only as a temporary landing place for unsorted session notes, then reroute them into the correct project or resource area.
8. After significant work, update the active zone's log at the top and overwrite the active zone's hot cache with the current useful context.
9. If a decision needs Claude, Figma, implementation, or future review to use it, materialize it as a real project note. Do not leave it only in Codex process memory.
10. If the active zone or project is ambiguous and the answer cannot be safely inferred, ask the owner before writing.

`wiki/agent-memory/` is now a legacy Codex process-memory layer. Codex may read it for historical process agreements in the work zone, but it is not the primary memory and it is not project documentation. New project knowledge should go into the normal zone folders above.

If plugin commands or skills are unavailable in Codex, perform the same vault operations through direct file reads and edits while preserving the routing, index, log, hot, and zone-isolation rules.

## Structure (PARA)

```
wiki/
├── index.md          # master catalog of all pages
├── log.md            # append-only operation log (newest at TOP)
├── hot.md            # ~500-word recent-context cache
├── overview.md       # executive summary of the whole vault
├── workspace/        # work-zone wrapper for the project folder
│   └── projects/     # active, deadline-bound efforts (one folder per project)
│       ├── _index.md
│       ├── inbox/    # unsorted session notes land here, then get rerouted
│       └── <project>/  # e.g. demo-project/
├── areas/            # ongoing responsibilities with no end date
│   └── _index.md
├── resources/        # reference material reusable across projects
│   ├── _index.md
│   ├── concepts/     # ideas, patterns, frameworks
│   ├── people/       # entities (people, orgs, vendors)
│   └── incoming/     # new sources awaiting sorting
├── archives/         # completed or inactive projects/areas
│   └── _index.md
├── personal/         # PERSONAL zone (see "Zones" below), fully isolated
│   ├── _index.md     # personal catalog (own index, separate from wiki/index.md)
│   ├── log.md        # personal operation log (separate from wiki/log.md)
│   ├── hot.md        # personal recent-context cache (loaded only in personal mode)
│   ├── inbox/        # unsorted personal session notes
│   └── projects/     # personal projects, one folder each
└── client/           # CLIENT zone (see "Zones" below), fully isolated
    ├── _index.md     # Client catalog (own index, separate from wiki/index.md)
    ├── log.md        # Client operation log (separate from wiki/log.md)
    ├── hot.md        # Client recent-context cache (loaded only in Client mode)
    ├── inbox/        # unsorted Client session notes
    └── projects/     # Client projects, one folder each

Reference/            # top-level (sibling of wiki/), NOT project knowledge
├── _index.md
├── Codex Commands.md    # cheatsheet: plugin commands/skills
└── Obsidian Plugins.md   # cheatsheet: installed Obsidian community plugins
```

`.vault-meta/mode.json` declares `mode: para` plus the `zones` block below. The plugin's `wiki-mode.py` router is hardcoded to the plugin repo and does NOT run against this vault, so this file is the authoritative routing spec: file new content per the active zone and the PARA folders above.

## Zones: work (default), personal, and Client

The vault has three isolated zones. They never mix: pages in one zone do not link to pages in another, and each zone's index/log/hot lists only its own content. Each zone owns its own index, log, and hot cache.

- **Work zone (default).** Everything under `wiki/` except `wiki/personal/` and `wiki/client/`. The existing PARA tree. Catalog `wiki/index.md`, log `wiki/log.md`, cache `wiki/hot.md`.
- **Personal zone.** Self-contained under `wiki/personal/`: its own `_index.md`, `log.md`, `hot.md`, `projects/`, `inbox/`.
- **Client zone.** Self-contained under `wiki/client/`: its own `_index.md`, `log.md`, `hot.md`, `projects/`, `inbox/`.

**Routing rule.** Every new session starts in the WORK zone. All ingest / save / query / lint operations target the active zone's folders and meta files (its index, log, hot), never another zone's.

**Switching is session-scoped.** When the user names a zone, the WHOLE session switches to it: read order becomes that zone's `hot.md` -> `_index.md` -> its pages, and all new content is written under that zone's root.
- To personal: "перейди в личную область" / "switch to personal" (or "личный режим"). Root `wiki/personal/`.
- To Client: "перейди в область Client" / "switch to Client" (or "режим Client"). Root `wiki/client/`.
- Back to work: "вернись в рабочую" / "switch to work". A new session also resets to work.

Before a Codex zone switch, briefly acquire the work lock and record the transition with `/usr/bin/python3 .vault-meta/evolution/trace-session.py switch --scope session --zone TARGET --lock-token TOKEN`, then release the same token. The durable session ledger becomes the initial zone of later turns. If the current turn already reached TARGET through a one-off transition, the session switch promotes that transition and still updates the durable ledger. A recorded transition excludes the whole switch turn from work reflection. If recording fails, do not enter the target zone. Never infer a reflection zone from message text.

**One-off override (optional).** Before processing a message starting with `личное:` or `client:`, record a `personal` or `client` transition for the current Codex turn through the same lock and `trace-session.py switch --scope turn` lifecycle. Only then route that one request; the durable session zone does not change. If the marker cannot be written, do not process the non-work override.

**Isolation rule.** Do not cross-link or co-list zones. When a zone is active, ignore the other zones' folders. The only allowed cross-reference is a single navigation link between zone indexes.

## Conventions

- YAML frontmatter on every note: `type`, `status`, `created`, `updated`, `tags` (minimum). Add `related:` wikilinks where useful.
- Add `memory_level: l0|l1|l2|l3` to every new or substantively updated durable note. Explicit L1 requires `memory_provenance`; explicit L2 gets canonical trust only with `memory_provenance`. Ambiguous reflection L2 must also use `memory_approval_mode: provisional` and `promotion_eligible: false`; retrieval marks it non-canonical. Legacy notes are inferred by `scripts/memory-model.py`; do not mass-edit them only to add the field.
- Wikilinks use `[[Note Name]]`; filenames are unique, no paths needed.
- `wiki/index.md` is the master catalog: update on every new page.
- Narrow reflection exception: the autonomous reflection checkpoint contains only exact provenance-bound outcome pages and must remain exact HEAD until immutable disposition finalization. A path that is owner-approved L3 or was L3 in the checkpoint parent can never be an autonomous outcome. Shared `index`, `log`, `hot`, `overview` and project `_index.md` are excluded from that checkpoint and are refreshed only in a later normal owner-scoped cycle. The generated memory index may be rebuilt for retrieval acceptance but is not checkpointed. Its small exact authority receipt is checkpointed during the later normal owner-scoped cycle.
- `wiki/log.md` is append-only: newest entry at the TOP, never edit past entries.
- `wiki/hot.md` is overwritten completely after every significant operation; keep it under ~500 words.
- Source documents copied into a project's `sources/` folder are immutable reference: synthesize on top, never rewrite them.
- Style: no em dashes (U+2014) or `--` as punctuation; use periods, commas, colons, parentheses. Short and direct.

## L0-L3 memory model

PARA determines where knowledge lives. L0-L3 determines how it is trusted and loaded.

- **L0, evidence.** Raw sources, imports, session traces, inbox captures, operation logs, `analysis/raw`, asset and QA evidence, and prototype internals. Immutable or operational evidence, never assumed true merely because it exists. Evidence paths are always L0. Promotion creates a separate synthesis page outside the evidence folder.
- **L1, facts.** Verified facts, decisions, constraints, findings and lessons with a source or evidence trail. This is the default for an ordinary durable page.
- **L2, scenarios.** Project state, indexes, architectures, specifications, playbooks, handoff and reusable workflows. L2 synthesizes L1 and may supersede older L2 pages.
- **L3, persona and policy.** Stable owner-approved principles, privacy rules and cross-project operating doctrine. L3 is never inferred. It requires exact hardcoded owner, ISO approval date frontmatter, a matching page SHA-256 record in `.vault-meta/memory-approvals.json`, and the code-anchored hash of that manifest. This detects one-sided drift. It is a human-reviewed cooperative trust boundary, not cryptographic identity against an actor authorized to edit both code and manifests.

`hot.md` is a compiled working cache, not L0-L3 truth. Load context in this order: approved L3, active L2, query-relevant L1, then L0 only to inspect evidence. Do not let a raw L0 claim override verified L1-L3.

Promotion is approval-aware: L0 to L1 requires verification, L1 to L2 requires synthesis or repeated use, L2 to L3 requires the owner's explicit approval. A live explicit owner directive may be materialized once through the exact direct-owner L3 page-hash chain without asking the owner to repeat it. Historical trace text, delegated judgment and inferred rules never authorize L3. Preserve superseded pages and mark their status instead of rewriting history.

The resolver and local retrieval entry point is:

`/usr/bin/python3 scripts/memory-model.py retrieve "QUERY" --zone work`

Choose a named role loadout: `designer` for product, UX and visual decisions; `builder` for implementation; `researcher` for broad synthesis; `reviewer` for audits; `general` otherwise. Designer, builder and reviewer require `--project PROJECT-SLUG`. Every loadout has hard item, character, per-level and L3 limits from `.vault-meta/memory-loadouts.json`; `--top` may reduce but never exceed them. Use `inspect` instead of `retrieve` when the loaded context, exclusions or remaining budget must be audited. Pinned role skills are hints and do not override skill trigger rules.

Use `--include-l0` only when the task needs original evidence. Automatically inferred or uncertain facts normally go to the active zone's `memory-candidates/` inbox as L0 through `scripts/memory-candidates.py`; they never enter default retrieval. The narrow standing-reflection exception may create a useful ambiguous scenario as explicit provisional L2 after deterministic evidence selection and `evolution-verifier`; it can never support L3. A proposal is a normal durable write: keep the same owner token while updating the candidate catalog, zone log and hot cache, rebuild and validate the index, create a scoped local checkpoint, release the token, then run a post-release list or retrieval check. Promotion creates a separate verified L1 or L2 page after evidence review. Explicit user decisions may be materialized directly with provenance. Personal and Client queries must use their own zone value and separate generated index. Legacy inferred L1 and L2 are discovery candidates, not verified facts, and rank below explicit pages.

Claude and Codex lifecycle state plus the policy-independent session-zone ledger use recoverable write-ahead transactions. Interrupted registration is recovered because the human turn exists; an interrupted session switch is rolled back before an unrelated operation. Operational read, publication or state-write failures remain retryable and are never converted into semantic retirement. Before a new turn, lifecycle records are grouped across current and prior policy generations. Exact duplicates are reduced to one before closure; conflicting variants or conflicting prepared receipts fail before publication. A not-yet-prepared receipt binds original registration authority and the current closing authority. Once its exact bytes are durably prepared, retry and policy rollover publish those same bytes in their original completion generation instead of reauthorizing them. A zone switch resolves the uniquely open turn across policy generations before changing the shared ledger, so a mid-turn policy update cannot bypass zone exclusion. A signal-bearing scan remains pending until a strictly validated immutable disposition finalization marker is published last under the same work lock. A later selector run in the same policy generation resumes that exact unfinished manifest instead of creating a second outcome cycle. Before any current-generation selection, pinned predecessor scan catalogs are checked against the global processed ledger. An unfinished predecessor cycle fails closed with its exact policy and scan identity, so policy rollout must drain it and can never reselect the same completion. Selector and public consumers use the same structural disposition validator; verify, run-state and health require the marker. A zero-signal scan may close its manifest directly. After an outcome-only checkpoint, reflection acceptance may use the exact active work-lock token to validate and replay the local uncheckpointed index authority. Public retrieval still refuses active writers and requires the authority committed at HEAD. The authority is committed only in the later ordinary metadata checkpoint after finalization.

## Operations (via the Codex-obsidian plugin)

All operations act on the ACTIVE zone (work by default; personal or Client after a session switch). The paths below are the work-zone defaults; in personal mode substitute the `wiki/personal/` equivalents, in Client mode the `wiki/client/` equivalents.

Every Codex ingest or save uses one durable write cycle. Acquire `bash scripts/wiki-lock.sh acquire .vault-meta/write/ZONE --ttl 3600`, retain its token, and release that exact token on every success or failure path. Renew and check ownership after every potentially long phase and immediately before each later canonical mutation, build, and checkpoint. An expired record remains fail-closed and cannot be stolen: release it with the original token, or run `bash scripts/wiki-lock.sh clear-stale .vault-meta/write/ZONE` only after confirming the writer died. While the lock is held: write source and synthesis pages, update project and zone indexes, append log, overwrite hot, run `scan`, run `build --lock-token TOKEN`, run `validate --lock-token TOKEN`, and create a scoped local Git checkpoint. Release in a `finally` path, then run a live project-scoped retrieval acceptance query and inspect the returned snippet, not only its page path. Do not claim completion if that query fails or if the index is stale.

- **Ingest**: drop a source, say "ingest [path]". Raw imported pages are L0. Project evidence goes to `workspace/projects/<name>/sources`; non-project work evidence goes to `resources/incoming`; personal and Client non-project evidence goes to that zone's `inbox`. Synthesis becomes a separate L1 or L2 page.
- **Query**: ask any question. Read the active hot cache, approved L3, zone and project L2, then retrieve L1 with the matching named role loadout. Read L0 only for verification. Use `memory-model.py inspect` for the exact budget and selection trace.
- **Save**: "/save" files the current conversation. Raw session capture is L0; verified decisions and reusable outcomes must be promoted into separate L1 or L2 pages in the active zone. Inbox remains temporary. Follow the durable write cycle above.
- **Lint**: "lint the wiki" runs a health check (orphans, dead links, stale claims) on the active zone.

## External accounts

No accounts, tokens, email addresses, team names, or machine-specific paths ship with this template. Authenticate optional services with the vault owner's own accounts. An external account never changes the active vault zone.

## Active projects

None yet. Create a project folder and project `_index.md` when an ongoing effort begins.
