# Installer reference

## What the renderer creates

`installer/render-vault.py` copies only the public empty template. It creates work, personal, and client PARA zones, renders `CLAUDE.md`, `AGENTS.md`, and `mode.json`, then binds the current L0-L3 memory and autonomous-reflection trust chain to the new owner and absolute vault path.

The renderer uses only the Python standard library. It refuses non-empty targets and requires the explicit `--approve-memory-policy` flag.

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

## Verification

The supported repository check is:

```bash
python3 -m unittest discover -s tests -v
```

It renders a fresh vault, validates the reflection policy, initializes local Git, builds and validates all three memory indexes under zone locks, checkpoints the authority receipts, and performs a live retrieval acceptance query.
