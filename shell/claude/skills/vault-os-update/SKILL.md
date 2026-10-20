---
name: vault-os-update
description: Install or update Vault OS so this machine matches the published release exactly - replaces the old system (settings, hooks, skills, agents), keeps every personal file, re-sorts vault notes by moving them. Use when the Vault OS check says outdated, unstamped or interrupted, or the person asks to install, update or reinstall Vault OS over an existing Claude setup.
---

# Vault OS update

The engine is `installer/vault-os.py` in the Vault OS repository. It plans, backs up, applies, verifies (parity
must be 0) and rolls back by itself on failure. Design and the system/personal boundary: `installer/UPGRADE.md`.

## Rules

- Never pass `--confirm` without the person's explicit yes to the plan or rollback preview you showed, in this
  session. A subagent never runs `--confirm`. No yes, no upgrade and no rollback.
- `--approve-memory-policy` only when the person also approved the two bundled L3 doctrine pages again.
- Nothing personal is deleted: notes are moved, never removed; every replaced or removed system item goes to the
  backup archive first. Say this plainly when you show the plan.

## Steps

1. Get the release (a fresh clone, never the person's vault):
   `git clone --depth 1 https://github.com/mrDesign-ww/vault-os.git "$TMPDIR/vault-os-release"`
   (if the folder exists: `git -C "$TMPDIR/vault-os-release" pull --ff-only`). Run every command below from it.
2. `python3 installer/vault-os.py detect`. Results: `fresh`, `foreign` (another Claude setup), `pre-stamp`
   (an older Vault OS), `installed` (with the version), `interrupted`.
3. `interrupted`: tell the person an earlier upgrade stopped half way, then run `python3 installer/vault-os.py
   recover`. It restores the state before that upgrade. Then continue with step 4.
4. Dry run: `python3 installer/vault-os.py upgrade --vault "/absolute/vault/path"`. Add `--owner`, `--title`,
   `--language`, `--client-label`, `--codex`, `--upstream-skills` or `--bypass-permissions` only when the output
   asks (exit code 3 lists the questions) or the person chose them. The answers line shows where each value came
   from.
5. Show the plan in the person's language, short: what is replaced, what is removed (their own skills, hooks,
   agents, commands, settings keys) and that all of it stays in the archive; personal notes that move and where;
   folders that need a `--map SRC=DST` answer; whether the L3 doctrine needs approval. Ask one question: apply
   this plan?
6. On yes: `python3 installer/vault-os.py upgrade --vault "..." [same answers] --confirm <plan id>
   [--approve-memory-policy]`. If it says the machine changed since the dry run, show the new plan and ask again.
7. Report: the archive path, `parity 0`, any warnings. Undo is the rollback below; undoing only the note moves is
   `python3 "<archive>/migration/undo.py"`.

## Rollback

1. `python3 installer/vault-os.py rollback "<archive>"` prints a preview and changes nothing: how many paths it
   restores, what stays in place (notes and folders created after the upgrade, Git history with commits made since)
   and what moves to the holding folder `<archive>/displaced/`, with the reason for each.
2. Show the preview in the person's language, short, and ask one question: roll back?
3. On yes: `python3 installer/vault-os.py rollback "<archive>" --confirm <id>` with the id from the preview. If it
   says the machine changed since the preview, show the new preview and ask again.

## Exit codes

0 done, 1 parity differences, 2 usage, 3 questions, approval needed or an id that no longer matches, 4 locked (a
live run holds the lock: wait) or interrupted (run `recover`),
5 Windows (run inside WSL), 6 an upstream fetch failed (nothing changed), 7 the upgrade failed and was rolled back,
8 engine error: the plan contradicts itself and nothing changed (report it, do not retry).
