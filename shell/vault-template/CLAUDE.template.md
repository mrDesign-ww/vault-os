# {{VAULT_TITLE}}: LLM Wiki (PARA)

Mode: PARA (Projects / Areas / Resources / Archives)
Purpose: Central knowledge hub for {{OWNER_NAME}}'s work. Drop a source, ask a question, the knowledge compounds.
Owner: {{OWNER_NAME}}
Created: {{DATE}}

> **Private vault.** Local git only, no remote by default. If this holds client or personal work, never push it to a public host.

## What this vault is

A persistent, compounding knowledge base built with the `claude-obsidian` plugin (a separate consumer of it). The wiki is the product: drop a source, ask a question, and the knowledge compounds.

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
│       └── <project>/
├── areas/            # ongoing responsibilities with no end date
├── resources/        # reference material reusable across projects
│   ├── concepts/     # ideas, patterns, frameworks
│   ├── people/       # entities (people, orgs, vendors)
│   └── incoming/     # new sources awaiting sorting
├── archives/         # completed or inactive projects/areas
{{ZONE_FOLDERS}}
```

`.vault-meta/mode.json` declares `mode: para` plus the zones block below. File new content per the active zone and the PARA folders above.

## Zones: {{ZONE_LIST}}

The vault has one or more **isolated zones**. They never mix: pages in one zone do not link to pages in another, and each zone's index/log/hot lists only its own content. Each zone owns its own index, log, and hot cache.

- **Work zone (default).** Everything under `wiki/` except the other zones' roots. Catalog `wiki/index.md`, log `wiki/log.md`, cache `wiki/hot.md`.
{{ZONE_DEFINITIONS}}

**Routing rule.** Every new session starts in the WORK zone. All ingest / save / query / lint operations target the active zone's folders and meta files (its index, log, hot), never another zone's.

**Switching is session-scoped.** When the user names a zone, the WHOLE session switches to it: read order becomes that zone's `hot.md` → `_index.md` → its pages, and all new content is written under that zone's root. A new session resets to work.

{{ZONE_SWITCH_PHRASES}}

**Isolation rule.** Do not cross-link or co-list zones. When a zone is active, ignore the other zones' folders. The only allowed cross-reference is a single navigation link between zone indexes.

## Conventions

- YAML frontmatter on every note: `type`, `status`, `created`, `updated`, `tags` (minimum). Add `related:` wikilinks where useful.
- Wikilinks use `[[Note Name]]`; filenames are unique, no paths needed.
- `wiki/index.md` is the master catalog: update on every new page.
- `wiki/log.md` is append-only: newest entry at the TOP, never edit past entries.
- `wiki/hot.md` is overwritten completely after every significant operation; keep it under ~500 words.
- Source documents copied into a project's `sources/` folder are immutable reference: synthesize on top, never rewrite them.
- Style: no em dashes or `--` as punctuation; use periods, commas, colons, parentheses. Short and direct.

## Operations (via the claude-obsidian plugin)

All operations act on the ACTIVE zone. The paths below are the work-zone defaults; in another zone substitute that zone's equivalents.

- **Ingest**: drop a source, say "ingest [path]". New pages route per PARA (resources/incoming for raw sources, workspace/projects/<name> for project-scoped notes).
- **Query**: ask any question. Read order (work): `wiki/hot.md` → `wiki/index.md` → the relevant `wiki/<para-folder>/_index.md` → individual pages.
- **Save**: "/save" files the current conversation; session notes land in the active zone's inbox, then get rerouted.
- **Lint**: "lint the wiki" runs a health check (orphans, dead links, stale claims) on the active zone.

## Self-improvement (evolution)

`.vault-meta/evolution/` holds a human-in-the-loop self-improvement loop: a Curator that keeps memory and the wiki tidy, retrieval refresh, and reflection that learns from friction. Run it by saying "run evolution" (or `/evolution`); a SessionStart nudge suggests it when due. Destructive operations always require your approval. See `.vault-meta/evolution/EVOLUTION.md`.

## MCP accounts (optional)

If you connect design/data services (e.g. Figma, Supabase) via MCP or plugins, they authenticate under YOUR own accounts — set them up per that plugin's instructions. This template ships no accounts or tokens.

## Active projects

None yet. This is a fresh vault — your first ingested source or `/save` starts the catalog.
