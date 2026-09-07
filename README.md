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

- A fail-closed, zone-scoped L0-L3 memory resolver with role-based context budgets.
- Git-checkpointed index authority, page-hash snapshots, and shared zone write locks.
- Candidate memory that keeps unverified inferences out of canonical retrieval.
- Crash-safe Claude and Codex turn lifecycle tracking.
- Work-only autonomous reflection with immutable evidence, selection, disposition, and processed receipts.
- Exact owner approval and SHA-256 binding for L3 doctrine. Reflection can never approve L3.
- 157 bundled skills and 30 optional Claude plugin entries.
- A deterministic, standard-library installer that refuses non-empty targets and renders all owner and machine-specific identities locally.
- A lifecycle hook that degrades loudly instead of discarding the turn, plus `reseal-policy.py` to repair the trust-hash chain after an approved edit.
- Harness-written transcript envelopes are no longer counted as human prompts, so a failed `Stop` can no longer reproduce itself in the next segment.
- Codex parity for the bundled design hook and a documented path for mirroring skills into `~/.codex/skills` and `~/.agents/skills`.

## Install

Open Claude Code and say:

```text
Install https://github.com/mrDesign-ww/vault-os
```

Claude follows [`INSTALL.md`](INSTALL.md), asks a short trust and setup quiz, then creates a new private vault. The full hardened runtime currently supports macOS and Linux. Windows users can run it through WSL.

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
| Evolution | Curator, wiki hygiene, retrieval refresh, adversarial verification, guarded reflection |
| Agents | Vault-local Claude and Codex contracts, lifecycle hooks, evolution skills |
| Tooling | 157 bundled skills, 30 optional Claude plugin entries, optional Codex and ECC setup |

## Privacy boundary

- Obsidian files remain the source of truth on the user's machine.
- Generated retrieval indexes and session traces stay local and are ignored by Git.
- Small index authority receipts are committed locally so retrieval can detect stale or unreviewed state.
- Personal and client zones are never eligible for work reflection.
- Authentication files, local MCP configuration, hook trust hashes, and existing user settings are never shipped.
- Optional third-party plugins and connected services have their own network and privacy policies. Vault OS itself does not upload vault contents.

## Repository layout

```text
vault-os/
├── INSTALL.md
├── installer/
│   ├── render-vault.py
│   ├── quiz.md
│   └── steps.md
├── shell/
│   ├── claude/             # global settings template and 157 skills
│   ├── codex/              # public plugin and hook templates
│   └── vault-template/     # empty vault, memory engine, hooks, and evolution runtime
├── tests/
│   └── test_render_vault.py
└── site/
```

## Verify the release

```bash
python3 -m unittest discover -s tests -v
```

The test renders a clean vault, validates the policy chain, initializes local Git, builds and validates all three indexes under locks, commits authority receipts, and performs a live retrieval query.

## License

[MIT](LICENSE) © 2026 Vladyslav Chumak
