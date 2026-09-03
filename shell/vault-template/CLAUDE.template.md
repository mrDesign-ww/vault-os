# {{VAULT_TITLE}}: LLM Wiki (PARA)

Mode: PARA (Projects / Areas / Resources / Archives)
Purpose: Central knowledge hub for {{OWNER_NAME}}. Drop a source, ask a question, and the knowledge compounds.
Owner: {{OWNER_NAME}}
Preferred language: {{LANGUAGE}}
Client zone label: {{CLIENT_LABEL}}
Created: {{DATE}}

> **Private vault.** This is client and personal work. Local git only, no remote, never push to a public host. Do NOT copy its contents into the `claude-obsidian` plugin repo (that one is public).

## What this vault is

A persistent, compounding knowledge base built with the `claude-obsidian` plugin (the plugin lives at the installed `claude-obsidian` plugin; this vault is a separate, private consumer of it). The wiki is the product: drop a source, ask a question, and the knowledge compounds.

## Cross-agent memory

Claude and Codex share the same Obsidian vault as durable project memory.

At the start of substantive work, treat `hot.md` as a cache, then load approved L3, the active zone index, current project L2, relevant L1 and L0 only for evidence. Use the vault-local `scripts/memory-model.py retrieve` command with a named role loadout: `designer`, `builder`, `researcher`, `reviewer` or `general`. Designer, builder and reviewer require `--project PROJECT-SLUG`. Hard budgets in `.vault-meta/memory-loadouts.json` cannot be raised from the CLI. Use `inspect` when the exact selection and exclusions matter. This is how Claude receives durable context saved by Codex. Write reusable outcomes back to normal PARA pages so Codex receives them in the same way.

Internal engine state stays separate: Claude memory and session traces belong to Claude, Codex SQLite memory and session traces belong to Codex. Do not copy or synchronize those stores directly. Exchange approved facts, decisions, handoffs, and lessons through the vault only.

Codex often handles design/product thinking, hypotheses, UX critique, task shaping, and handoff documentation. When Codex saves useful work into normal active-zone project pages, resources, indexes, logs, or hot cache, treat those pages as regular vault knowledge.

Do not read `wiki/agent-memory/` as project context unless owner explicitly asks. That folder is legacy Codex process memory only. Project decisions that matter for implementation, Figma, review, or future planning should live in normal project or resource pages.

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
├── Claude Commands.md    # cheatsheet: plugin commands/skills
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

The always-loaded `UserPromptSubmit` hook records these exact route directives in a policy-independent session ledger before the request is processed. Lifecycle state and ledger use a recoverable write-ahead transaction. An interrupted registration is recovered because the human turn exists; an interrupted switch is rolled back before an unrelated prompt. The ledger supplies the initial zone of every later turn, including after a reflection-policy update. Before a new prompt or Stop, exact duplicate lifecycle records across policy generations are reduced to one; conflicting variants fail before publication. A new completion receipt preserves original registration authority and current closing authority. Once its exact bytes are durably prepared, retry or policy rollover publishes those same bytes in their original completion generation. A session route matching a one-off target still promotes the transition and updates the durable ledger. A zone switch resolves the uniquely open turn across policy generations before changing the shared ledger, so a mid-turn policy update cannot bypass zone exclusion. If any lifecycle event cannot be recorded, the configured hook preserves the nonzero status so the turn is retried instead of silently lost. Operational I/O or publication errors remain retryable; only deterministic segment ambiguity may retire a state. The `Stop` hook excludes every switched turn from work reflection. If `Stop` misses its lock, the next prompt closes the prior exact one-prompt segment. Tool-result user envelopes remain inside the exact segment hash but are not counted as a second human prompt.

**One-off override (optional).** A message starting with `личное:` or `client:` is automatically marked by the same prompt hook as non-work before it is processed. It routes only that request and does not change the session's active zone.

**Isolation rule.** Do not cross-link or co-list zones. When a zone is active, ignore the other zones' folders. The only allowed cross-reference is a single navigation link between zone indexes.

## Conventions

- YAML frontmatter on every note: `type`, `status`, `created`, `updated`, `tags` (minimum). Add `related:` wikilinks where useful.
- Add `memory_level: l0|l1|l2|l3` to every new or substantively updated durable note. Explicit L1 requires `memory_provenance`; explicit L2 gets canonical trust only with `memory_provenance`.
- Wikilinks use `[[Note Name]]`; filenames are unique, no paths needed.
- `wiki/index.md` is the master catalog: update on every new page.
- Narrow reflection exception: the autonomous reflection checkpoint contains only exact provenance-bound outcome pages and must remain exact HEAD until immutable disposition finalization. A path that is approved L3 or was L3 in the parent commit is forbidden even if the proposed post-state says L1 or L2. Shared `index`, `log`, `hot`, `overview` and project `_index.md` are excluded from that checkpoint and are refreshed only in a later normal owner-scoped cycle. The generated memory index may be rebuilt for retrieval acceptance but is not checkpointed. Its small exact authority receipt is checkpointed during the later normal owner-scoped cycle.
- `wiki/log.md` is append-only: newest entry at the TOP, never edit past entries.
- `wiki/hot.md` is overwritten completely after every significant operation; keep it under ~500 words.
- Source documents copied into a project's `sources/` folder are immutable reference: synthesize on top, never rewrite them.
- Style: no em dashes (U+2014) or `--` as punctuation; use periods, commas, colons, parentheses. Short and direct.

## Operations (via the claude-obsidian plugin)

Vault-local skills under `.claude/skills/` override generic plugin workflows for save, query and ingest. They must not call the plugin repository's `wiki-mode.py` because that router is not configured for this vault.

PARA determines location. L0-L3 determines trust: evidence folders are always L0; verified facts are L1; canonical project state and workflows are L2; stable policy is L3 only with hardcoded exact owner, ISO approval date, matching page hash in `.vault-meta/memory-approvals.json` and matching code anchor for that manifest. This is a cooperative human-reviewed boundary. Legacy inferred L1/L2 is discovery material, not verified truth.

A signal-bearing reflection scan remains pending until a strictly validated immutable disposition finalization marker is published last under the same work lock. A later selector run in the same policy generation resumes that exact unfinished manifest instead of creating a second outcome cycle. Before any current-generation selection, pinned predecessor scan catalogs are checked against the global processed ledger. An unfinished predecessor cycle fails closed with its exact policy and scan identity, so policy rollout must drain it and can never reselect the same completion. Selector, `verify`, last-run and health use the same strict structural disposition contract and reject a body without that marker. A zero-signal scan may close its manifest directly. After an outcome-only checkpoint, reflection acceptance may use the exact active work-lock token to validate and replay the local uncheckpointed index authority. Public retrieval still refuses active writers and requires the authority committed at HEAD. The authority is committed only in the later ordinary metadata checkpoint after finalization.

All operations act on the ACTIVE zone (work by default; personal or Client after a session switch). The paths below are the work-zone defaults; in personal mode substitute the `wiki/personal/` equivalents, in Client mode the `wiki/client/` equivalents.

- **Ingest**: preserve the raw source as immutable L0. Use project `sources/`, work `wiki/resources/incoming/`, or the personal/Client zone `inbox`. Create verified synthesis as a separate L1 or L2 page.
- **Query**: use `/usr/bin/python3 scripts/memory-model.py retrieve "QUERY" --zone ZONE --loadout ROLE`, with `--project` for project-scoped roles. A stale index or changed loadout policy fails closed and must be rebuilt before answering from it.
- **Candidate memory**: automatically inferred or uncertain claims go to the active zone's `memory-candidates/` inbox as L0 through `scripts/memory-candidates.py`. Proposal is not complete when the file is created: under the same owner token update the candidate catalog, zone log and hot cache, rebuild and validate, create a scoped local checkpoint, release, then run a post-release list or retrieval check. Candidates remain excluded until evidence review and a separate approved L1 or L2 page. Explicit user decisions may be materialized directly with provenance.
- **Save**: `/save` uses `.claude/skills/save/SKILL.md`. Work inbox is `wiki/workspace/projects/inbox/`. The skill owns the shared zone lock through the scoped checkpoint, releases it on every path, then runs the live retrieval acceptance query.
- **Lint**: "lint the wiki" runs a health check (orphans, dead links, stale claims) on the active zone.

## External accounts

No accounts, tokens, email addresses, team names, or machine-specific paths ship with this template. Authenticate optional services with the vault owner's own accounts. An external account never changes the active vault zone.

## Active projects

None yet. Create a project folder and project `_index.md` when an ongoing effort begins.
