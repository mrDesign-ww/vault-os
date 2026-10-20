<h1 align="center">🗄️ Vault OS</h1>

<p align="center">
  <em>A private, persistent Obsidian memory system for Claude Code and Codex.</em>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-1f1f1f.svg"></a>
  <img alt="Platform" src="https://img.shields.io/badge/full%20runtime-macOS%20%7C%20Linux-4b7bec.svg">
  <img alt="For Claude Code, Codex, and Obsidian" src="https://img.shields.io/badge/for-Claude%20Code%20%2B%20Codex%20%2B%20Obsidian-6f56d9.svg">
  <a href="https://mrdesign-ww.github.io/vault-os/"><img alt="Live walkthrough" src="https://img.shields.io/badge/walkthrough-live-2ecc71.svg"></a>
</p>

Vault OS turns a local Obsidian vault into durable, trust-aware memory for AI work. PARA controls where knowledge lives. L0-L3 controls how evidence, facts, project state, and owner policy are trusted. Work, personal, and client zones remain isolated.

> [!IMPORTANT]
> This repository contains an empty, parameterized template. It includes no projects, notes, session traces, accounts, tokens, private paths, or client material.

## What changed in the current release

- A global Claude layer, installed by `installer/install-claude-layer.py` on macOS, Linux and Windows: 10 agent types in three model tiers, global rules, an orchestration doc for main sessions, spec and design-round templates, hooks, and a status line.
- Hooks that guard unattended work: no process kills by pattern and no `rm` with globs, a pause at 98% of the weekly usage limit, a compaction snapshot, and big-image downscaling for build agents (macOS).
- A design layer for product UI/UX and frontend: `skill-router` (at most 3 skills per job), `figma-craft` with read-only Figma gate scripts, a `product-designer` agent, and design-spec and design-round templates.
- 74 bundled skills with a source, licence and copyright table and a licence file in each folder; gstack and Remotion are installed from their upstream at a pinned commit (`--upstream-skills`). With Codex, `~/.codex/skills` and `~/.agents/skills` get exactly the owner's live sets: 97 and 152 bundled skill folders (the Claude skills his folders hold, `vault-os-update` and the host-only skills), plus gstack's own Codex setup and the Remotion copy from upstream.
- A lighter vault runtime: `retrieve --brief`, light Russian stemming in retrieval, semantics-first retrieval evals, a faster health check, and reflection off by default (evolution runs only on the owner's word).
- The installer never overwrites user files: it skips them, or backs them up with `--backup`, and only adds missing settings.
- Clean install and full upgrade: `installer/vault-os.py` and the `vault-os-update` skill make a machine match this release exactly. An older or foreign setup is replaced (its extra skills, hooks, agents and settings keys go to a timestamped backup archive), personal files are kept, vault notes are moved into this structure with their links rewritten, never deleted, and the whole upgrade rolls back from the archive: `rollback` first shows a preview of what it restores, keeps and moves aside, and changes something only with `--confirm` and the preview's id after the person's yes; notes and commits made after the upgrade stay. A SessionStart check reports a newer release once a day. Details: [`installer/UPGRADE.md`](installer/UPGRADE.md).

Earlier releases added the fail-closed L0-L3 resolver with role budgets, Git-checkpointed index authority, zone write locks, candidate memory, exact owner approval of L3 doctrine, and `reseal-policy.py`.

## Install

Open Claude Code and say:

```text
Install https://github.com/mrDesign-ww/vault-os
```

Claude follows [`INSTALL.md`](INSTALL.md), asks a short trust and setup quiz, then creates a new private vault. On a machine that already has a Claude setup or an older Vault OS, say `Update Vault OS from https://github.com/mrDesign-ww/vault-os`: Claude shows the full upgrade plan first and changes nothing without a yes. The full hardened runtime currently supports macOS and Linux. Windows users can run it through WSL.

For a direct test render:

```bash
python3 installer/render-vault.py \
  --target "/absolute/path/to/new-vault" \
  --owner "Owner Name" \
  --title "Vault Title" \
  --client-label "Client" \
  --approve-memory-policy
```

The approval flag must only be used after the owner explicitly approves the bundled L3 doctrine during installation.

## System layers

| Layer | Included |
| --- | --- |
| Vault | Empty PARA scaffold with work, personal, and client zones |
| Memory | L0 evidence, L1 verified facts, L2 canonical scenarios, owner-approved L3 policy |
| Retrieval | Separate local index per zone, role loadouts, strict provenance and freshness checks |
| Integrity | Shared locks, local Git checkpoints, content hashes, authority receipts, fail-closed validation |
| Evolution | Owner-triggered curator, wiki hygiene, retrieval refresh, adversarial verification; reflection off by default |
| Agents | 10 global agent types (Haiku, Sonnet, Opus tiers), vault-local Claude and Codex contracts, evolution skills |
| Guards | Global hooks: dangerous-command guard, weekly-limit pause, compaction snapshot, orchestration doc, image-size gate (macOS) |
| Tooling | 74 bundled skills (97 for Codex, 152 for `~/.agents`) plus pinned upstream skills, 33 Claude plugin entries (9 on, 24 off), optional MCP servers, Codex and ECC setup |

## Privacy boundary

- Obsidian files remain the source of truth on the user's machine.
- Generated retrieval indexes and session traces stay local and are ignored by Git.
- Small index authority receipts are committed locally so retrieval can detect stale or unreviewed state.
- Personal and client zones are never eligible for work reflection.
- Authentication files, local MCP configuration, hook trust hashes, and existing user settings are never shipped.
- Optional third-party plugins and connected services have their own network and privacy policies. Vault OS itself does not upload vault contents.
- Bundled hyperframes/media-use skills send anonymous usage telemetry to HeyGen; opt out with `HYPERFRAMES_NO_TELEMETRY=1` or `DO_NOT_TRACK=1`.

## Repository layout

```text
vault-os/
├── INSTALL.md
├── installer/
│   ├── render-vault.py
│   ├── install-claude-layer.py
│   ├── vault-os.py         # detect, upgrade, parity, rollback, recover (UPGRADE.md)
│   ├── vaultos/
│   ├── install-mac.sh
│   ├── install-windows.ps1
│   ├── quiz.md
│   └── steps.md
├── shell/
│   ├── claude/             # agents, hooks, docs, templates, status line, settings template, 74 skills
│   ├── codex/              # public plugin and hook templates, 92 Codex and ~/.agents skills
│   └── vault-template/     # empty vault, memory engine, hooks, and evolution runtime
├── tests/
│   ├── test_render_vault.py
│   ├── test_install_claude_layer.py
│   ├── test_install_parity_steps.py
│   ├── test_upgrade.py
│   ├── test_vault_migration.py
│   ├── test_vault_os_check.py
│   └── test_claude_hooks.py
└── site/
```

## Verify the release

```bash
python3 -m unittest discover -s tests -v
```

The tests run every hook self-test, install the Claude layer into temporary homes for each platform, and render a clean vault that is checked end to end: policy chain, local Git, all three indexes under locks, authority receipts, and a live retrieval query. The upgrade tests install the previous public release with a foreign vault into a temporary home, upgrade it to parity 0, roll it back to the exact previous tree, kill the upgrade at every phase and recover it.

## License

[MIT](LICENSE) © 2026 mrDesign-ww. Bundled third-party skills keep their own licences, see [`shell/claude/skills/THIRD-PARTY-SKILLS.md`](shell/claude/skills/THIRD-PARTY-SKILLS.md).
