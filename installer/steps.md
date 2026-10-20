# Installer reference

## What the renderer creates

`installer/render-vault.py` copies only the public empty template. It creates work, personal, and client PARA zones, renders `CLAUDE.md`, `AGENTS.md`, and `mode.json`, then binds the current L0-L3 memory and autonomous-reflection trust chain to the new owner and absolute vault path.

The renderer uses only the Python standard library. It refuses non-empty targets and requires the explicit `--approve-memory-policy` flag.

## Global Claude layer

`installer/install-claude-layer.py` copies agents, hooks, docs, templates, the status line, scripts and skills into the Claude home, renders `CLAUDE.md` from `shell/claude/CLAUDE.template.md`, and merges `shell/claude/settings.template.json` plus the hooks from `shell/claude/hooks/hooks.json`. Hook entries carry a `platforms` list; macOS-only entries (`image-size-gate.py`, `keep-awake.sh`, the `afplay` sound) are never written on Linux or Windows, and POSIX shell scripts are not copied on Windows. Differing user files are skipped, or moved aside with `--backup`. It prints a JSON report.

## Codex layer

`installer/install-codex-layer.py --vault PATH` merges `shell/codex/config.template.toml` (missing top-level keys and tables only) and `shell/codex/hooks.template.json` (missing hook commands only, macOS-only ones on macOS) into `~/.codex`, after timestamped backups. `--mcp NAME` adds a URL-only MCP block from `shell/claude/mcp-servers.json`.

## Clean install and full upgrade

`installer/vault-os.py` replaces an existing system instead of adding to it: `detect`, a dry-run `upgrade` that prints the plan and its id, `upgrade --confirm PLAN_ID`, `parity`, `rollback ARCHIVE` and `recover`. It builds the desired state with the three installers above, archives everything it replaces or removes, moves vault notes into the structure, and must end with parity 0. The boundary between system and personal files, the archive and the migration rules: `installer/UPGRADE.md`.

## Global configuration merge

Before changing either global file:

- copy `~/.claude/settings.json` to a timestamped backup;
- copy `~/.codex/config.toml` and `~/.codex/hooks.json` to timestamped backups;
- merge only documented public marketplace, plugin, language, and hook fields;
- preserve all unrelated user settings;
- never copy auth files, secrets, MCP servers, local marketplace paths, notification executables, project trust entries, or hook trust hashes.

## ECC, optional

Install ECC only inside the selected code project:

```bash
cd "/absolute/path/to/code-project"
git clone https://github.com/affaan-m/ECC .ecc-src
cd .ecc-src
npm install --omit=dev --ignore-scripts
node scripts/install-apply.js --profile core \
  --with lang:typescript --with framework:react --with framework:nextjs \
  --with capability:database --with capability:security --with capability:research \
  --target claude-project
cd ..
rm -rf .ecc-src
```

Confirm the exact target before removing `.ecc-src`. ECC is never installed globally and never copied into the vault.

## Repairing the trust chain

The reflection policy, its loader, the doctrine page, the approvals manifest, and
the memory resolver hash each other in a cycle. After an owner-approved edit to any
of them, re-seal the cycle from inside the vault instead of patching hashes by hand:

```bash
python3 .vault-meta/evolution/reseal-policy.py --check
python3 .vault-meta/evolution/reseal-policy.py --apply
```

`--check` never writes. Rebuild the affected zone index afterwards, because rewriting
a guarded file moves its logical state.

## Verification

The supported repository check is:

```bash
python3 -m unittest discover -s tests -v
```

It also runs every hook self-test and the Claude layer installer against temporary homes for macOS, Linux and Windows. The vault test renders a fresh vault, validates the reflection policy, initializes local Git, builds and validates all three memory indexes under zone locks, checkpoints the authority receipts, and performs a live retrieval acceptance query.
