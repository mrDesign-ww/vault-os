# Installing Vault OS

These are the instructions for Claude Code when a user asks to install Vault OS from this repository.

## 0. Detect what is installed

Run `python3 installer/vault-os.py detect` first.

- `fresh`: no Claude setup yet. Follow steps 1 to 9 below, or let the engine do the whole install: show the plan of `python3 installer/vault-os.py upgrade --owner "Owner Name" --vault "/absolute/vault/path" --title "Vault Title" --language English [--codex] [--upstream-skills] [--bypass-permissions]`, then run the same command with `--confirm PLAN_ID --approve-memory-policy` after the user's yes and the L3 approval (quiz). The engine route installs no MCP servers: add them afterwards, from the repo root, with `python3 installer/install-claude-layer.py --owner "Owner Name" --language English --mcp default` (step 5).
- `foreign` (another Claude setup), `pre-stamp` (an older Vault OS without a stamp) or `installed` (with its version, `outdated` when this release is newer): follow the `vault-os-update` skill (`shell/claude/skills/vault-os-update/SKILL.md`). It replaces the old system completely, keeps every personal file, and moves vault notes into this structure. Do not use the add-only steps below over an existing system: they skip or back up differing files and leave the old system's extra skills, hooks and settings keys in place.
- `interrupted`: run `python3 installer/vault-os.py recover` first, then detect again.

Design, the system and personal boundary, the archive and the rollback: `installer/UPGRADE.md`.

## Safety rules

- The steps below create the vault only in a new or empty directory. An existing vault is upgraded in place only by `installer/vault-os.py` (step 0), which moves notes and never deletes them.
- Never copy a real vault into this repository.
- Back up existing global Claude and Codex configuration before merging anything.
- Never copy authentication files, API keys, local MCP configuration, trust hashes, or machine-specific paths.
- Initialize Git locally inside the new vault. Do not add a remote.
- The hardened memory and reflection runtime currently requires macOS or Linux. On Windows, use WSL for the full system. The global Claude layer (step 5) also installs natively on Windows.

## 1. Check prerequisites

The full system needs Git, Python 3, Node.js, Claude Code, and Obsidian. On macOS, the helper can check or install the command-line prerequisites:

```bash
bash installer/install-mac.sh ensure_prereqs
bash installer/install-mac.sh ensure_obsidian
```

## 2. Ask the install quiz

Use `installer/quiz.md`. Collect the owner name, preferred language, vault title and path, client-zone label, the skip-or-backup choice for existing Claude files, optional Codex integration, optional ECC integration, and explicit approval for the two bundled L3 doctrine pages.

Do not infer L3 approval. If the user declines it, stop before rendering and explain that this release packages the current hardened system as one trust-bound installation.

## 3. Render a clean vault

Run the deterministic renderer. It refuses a non-empty target and binds all owner, path, policy, code, evaluation, and doctrine hashes locally.

```bash
python3 installer/render-vault.py \
  --target "/absolute/path/to/new-vault" \
  --owner "Owner Name" \
  --title "Vault Title" \
  --language "English" \
  --client-label "Client" \
  --approve-memory-policy
```

The result contains three isolated zones: work, personal, and client. The client label is configurable, but its stable internal slug remains `client` so the audited runtime does not need generated code.

## 4. Create the local trust baseline

Run these commands from the new vault. Use the user's normal Git identity if already configured.

```bash
git init
git add .
git commit -m "Initialize Vault OS"

for zone in work personal client; do
  token=$(bash scripts/wiki-lock.sh acquire ".vault-meta/write/$zone" --ttl 3600) || exit 1
  python3 scripts/memory-model.py build --zone "$zone" --lock-token "$token" &&
  python3 scripts/memory-model.py validate --zone "$zone" --lock-token "$token"
  rc=$?
  bash scripts/wiki-lock.sh release ".vault-meta/write/$zone" "$token"
  [ "$rc" -eq 0 ] || exit "$rc"
done

git add .vault-meta/memory-index/*.authority.json
git commit -m "Bind memory index authority"
python3 scripts/memory-model.py retrieve "memory source of truth" --zone work --loadout general
```

Inspect the final result. It must include both bundled L3 pages with `memory_trust: approved` and a snippet stating that Obsidian is the source of truth.

## 5. Install the global Claude layer

Ask whether existing files that differ should be skipped (default) or backed up and replaced (quiz question 5), whether to use the owner's bypass permission mode (question 6), and which MCP servers to add (question 7). Then run the layer installer. It works on macOS, Linux and Windows and uses only the Python standard library.

```bash
python3 installer/install-claude-layer.py --owner "Owner Name" --language "English"          # skip differing files
python3 installer/install-claude-layer.py --owner "Owner Name" --language "English" --backup # move them aside first
# the full system, as on the owner's machine: upstream skills, bypass mode (only on a yes), MCP servers
python3 installer/install-claude-layer.py --owner "Owner Name" --language "English" \
  --upstream-skills --bypass-permissions --mcp default
```

- `--upstream-skills` installs gstack (pinned commit, its own `./setup --prefix`, needs `bun`; it registers its AskUserQuestion hooks itself) and the Remotion skill from `shell/claude/upstream-skills.json`. It takes about 1.8 GB of disk with its built tools and browser cache. A missing tool is reported and skipped.
- `--bypass-permissions` writes `permissions.defaultMode: bypassPermissions` and the allow list `Bash(*)`, `Read(*)`, `Write(*)`, `Edit(*)`, `WebFetch(*)`, `WebSearch(*)`, `Agent(*)`, `Glob(*)`, `Grep(*)`, `LS(*)`. Without it the mode stays `default`.
- `--mcp default` adds the user-scope servers magnific and replicate (`--mcp all` also higgsfield) with `claude mcp add --scope user`, URL only; each signs in through OAuth on first use. The plugin servers (Figma, Playwright, Chrome DevTools) come with their plugins. List: `shell/claude/mcp-servers.json`.

On macOS the same call is `bash installer/install-mac.sh install_claude_layer "Owner Name" English [--backup]`; on Windows, `Install-ClaudeLayer -Owner "Owner Name" -Language English [-Backup]` from `installer/install-windows.ps1`. Add `--dry-run` to preview the report without writing anything.

To replace an existing system instead of adding to it, use `--upgrade` (step 0): `python3 installer/install-claude-layer.py --upgrade --vault "/absolute/vault/path" [answers] [--confirm PLAN_ID]` hands over to `installer/vault-os.py`, and `--rollback ARCHIVE` undoes an upgrade. The helpers are `detect_system`, `upgrade_system`, `rollback_system` and `recover_system` in `install-mac.sh`, and `Detect-VaultOS`, `Upgrade-VaultOS` and `Rollback-VaultOS` in `install-windows.ps1`. On Windows the upgrade plans and checks parity but refuses to write: run it inside WSL.

What it installs into `~/.claude/`:

| Layer | Source | Notes |
| --- | --- | --- |
| Agent types | `shell/claude/agents/` (10) | Haiku `runner`, `checker`, `locator`; Sonnet `builder`, `worker`, `look-reviewer`; Opus `architect`, `reviewer`, `product-designer`, `general-purpose` |
| Global rules | `shell/claude/CLAUDE.template.md` | rendered to `CLAUDE.md` with the owner name and language |
| Orchestration doc | `shell/claude/docs/orchestration.md` | injected into main sessions only by a hook |
| Templates | `shell/claude/templates/` | feature spec, design spec, design round |
| Hooks | `shell/claude/hooks/` | wired from `hooks/hooks.json`, filtered per platform |
| Status line | `shell/claude/statusline/statusline.py` | three lines: model, project, branch, effort; context, 5-hour and weekly usage, prompt cache; agents at work by model, background jobs, session time and cost. Russian labels when the language is Russian. Also feeds the weekly-limit guard |
| Scripts | `shell/claude/scripts/` | `keep-awake.sh` (macOS), `wait-no-chromium.sh` (macOS, Linux); not copied on Windows |
| Skills | `shell/claude/skills/` (74) | sources and licences in `THIRD-PARTY-SKILLS.md` |

Hooks per platform:

| Hook | Event | Platforms |
| --- | --- | --- |
| `guard-dangerous-bash.py` | PreToolUse Bash | all |
| `weekly-limit-guard.py` | PreToolUse, every tool | all |
| `compact-snapshot.py` | PreCompact, SessionStart compact | all |
| `orchestration-doc.py` | SessionStart | all |
| `image-size-gate.py` | PreToolUse Read | macOS only (uses `sips`) |
| `keep-awake.sh` | UserPromptSubmit | macOS only (uses `caffeinate`) |
| end-of-turn sound (Hero, twice) | Stop | macOS only (uses `afplay`) |
| gstack question hooks | PreToolUse and PostToolUse AskUserQuestion | registered by the gstack setup with `--upstream-skills` |

Rules the installer keeps:

- A file or skill folder that already exists and differs is skipped, or with `--backup` moved to `<name>.backup-<timestamp>`. Identical files are left alone.
- `~/.claude/settings.json` is backed up before any change. Only missing keys are added, and the maps (plugins, marketplaces, `skillOverrides`, `modelSettings`, `env`) entry by entry, plus the hooks; existing user values win. The template carries the owner's working defaults: `model: opus[1m]`, Opus effort `xhigh`, `autoCompactWindow: 300000`, the skill listing caps, `tui: fullscreen`, push notifications for agents, `ENABLE_CLAUDEAI_MCP_SERVERS: false`, and 106 `skillOverrides` that decide which skills are off, listed by name only, or reachable only when typed. Bypass permissions only with `--bypass-permissions`.
- Plugins are marketplace dependencies, never vendored: the template turns on nine (chrome-devtools-mcp, design-council, figma, frontend-design, obsidian, playwright, security-guidance, superpowers, vercel) and lists 23 more as optional (`false`); `optional-plugins.json` adds one more, also off.
- A second run changes nothing.

The new vault already contains its local Claude lifecycle hooks in `.claude/settings.json`. Do not replace that file with the global settings template.

## 6. Optional Codex integration

If selected in the quiz:

1. Install the Codex desktop app and let the user sign in with their own OpenAI account.
2. Run `python3 installer/install-codex-layer.py --vault "/absolute/vault/path"` (add `--mcp higgsfield` if wanted). It backs up `~/.codex/config.toml` and `~/.codex/hooks.json`, adds the missing model defaults (`model_reasoning_effort = "xhigh"` and the owner's model, only when the user has none), `[features]`, the public marketplaces and plugin blocks from `shell/codex/config.template.toml`, and the hooks from `shell/codex/hooks.template.json` with the vault path substituted for `{{VAULT_PATH}}` (macOS-only sound and keep-awake hooks only on macOS).
3. Mirror the skills with `python3 installer/install-claude-layer.py ... --extra-skills-dir ~/.codex/skills --extra-skills-dir ~/.agents/skills` (add `--upstream-skills` when step 5 used it). Codex does not read `~/.claude/skills`, and the bundled design hook looks for `~/.agents/skills/impeccable/scripts/hook.mjs`. Each folder gets exactly the owner's live set, not a mirror of every bundled skill: the Claude skills his folder holds (`claude_mirror` in `shell/codex/skill-targets.json`: 28 for Codex, 59 for `~/.agents`), `vault-os-update` and the licence files, plus its own set from `shell/codex/skills`: 97 skill folders in `~/.codex/skills`, 152 in `~/.agents/skills`. The other bundled skills (for example `figma-craft`, `skill-router`) stay in `~/.claude/skills` only. Any other `--extra-skills-dir` gets all 74. With `--upstream-skills`, gstack's own `./setup --host codex` also runs in the Claude checkout (it writes `~/.codex/skills/gstack` and the `gstack-*` links) and Remotion is copied to `~/.agents/skills/remotion-best-practices`, both at the pin.
4. Preserve the user's model, account, MCP servers, desktop settings, and existing hooks.
5. Never copy `[hooks.state]`. Codex creates its machine-specific trust hashes after the user approves the hooks.

## 7. Optional ECC integration

ECC is project-scoped and does not belong inside the vault. If selected, follow the current project install command in `installer/steps.md` using the user's chosen code-project path.

## 8. Open the vault

Open the new directory in Obsidian. The bundled `.obsidian` configuration lists the recommended community plugins, but Obsidian may require the user to install or approve them in its Community Plugins screen.

Optionally create a launch shortcut with `installer/install-mac.sh make_shortcut`.

## 9. Stamp the install

Run `python3 installer/vault-os.py upgrade --vault "/absolute/vault/path" [--codex] [--upstream-skills] [--bypass-permissions]` with the answers used above. After steps 3 to 6 it prints `nothing to change; the stamp will be written`; run it again with `--confirm PLAN_ID`. With `--upstream-skills`, an install whose upstream skills already sit at the pinned commit is adopted as it is, so the plan stays empty; skills at another commit or missing ones show up in the plan. The stamp (`~/.claude/vault-os/installed.json`) lets the SessionStart check report newer releases and lets a later upgrade remove what a release no longer ships. Any other plan means a step differs from this release: show it to the user before confirming. `python3 installer/vault-os.py parity` must then print `parity: 0 differences`.

Report the vault path, the two local Git commits, the retrieval acceptance result, the Claude layer report (installed, skipped, backed up), the stamp and parity result, and any optional integrations that were skipped.
