# Changelog

Release notes for Vault OS, newest first. The version is in [`VERSION`](VERSION).

## 2026.10.20

- A global Claude layer, installed by `installer/install-claude-layer.py` on macOS, Linux and Windows: 10 agent types in three model tiers, global rules, an orchestration doc for main sessions, spec and design-round templates, hooks, and a status line.
- Hooks that guard unattended work: no process kills by pattern and no `rm` with globs, a pause at 98% of the weekly usage limit, a compaction snapshot, and big-image downscaling for build agents (macOS).
- A design layer for product UI/UX and frontend: `skill-router` (at most 3 skills per job), `figma-craft` with read-only Figma gate scripts, a `product-designer` agent, and design-spec and design-round templates.
- 74 bundled skills with a source, licence and copyright table and a licence file in each folder; gstack and Remotion are installed from their upstream at a pinned commit (`--upstream-skills`). With Codex, `~/.codex/skills` and `~/.agents/skills` get exactly the owner's live sets: 97 and 152 bundled skill folders (the Claude skills his folders hold, `vault-os-update` and the host-only skills), plus gstack's own Codex setup and the Remotion copy from upstream.
- A lighter vault runtime: `retrieve --brief`, light Russian stemming in retrieval, semantics-first retrieval evals, a faster health check, and reflection off by default (evolution runs only on the owner's word).
- The installer never overwrites user files: it skips them, or backs them up with `--backup`, and only adds missing settings.
- Clean install and full upgrade: `installer/vault-os.py` and the `vault-os-update` skill make a machine match this release exactly. An older or foreign setup is replaced (its extra skills, hooks, agents and settings keys go to a timestamped backup archive), personal files are kept, vault notes are moved into this structure with their links rewritten, never deleted, and the whole upgrade rolls back from the archive: `rollback` first shows a preview of what it restores, keeps and moves aside, and changes something only with `--confirm` and the preview's id after the person's yes; notes and commits made after the upgrade stay. A SessionStart check reports a newer release once a day. Details: [`installer/UPGRADE.md`](installer/UPGRADE.md).

## Earlier releases

Earlier releases added the fail-closed L0-L3 resolver with role budgets, Git-checkpointed index authority, zone write locks, candidate memory, exact owner approval of L3 doctrine, and `reseal-policy.py`.
