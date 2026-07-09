# Installing Vault OS — instructions for Claude Code

You (Claude Code) are installing the **Vault OS** starter for a new user on THIS machine. The user has cloned this repo and said "install". Follow these steps in order.

## Golden rules (safety)

- **Back up before you overwrite.** Copy any existing `~/.claude/settings.json` and `~/.codex/config.toml` to `*.backup-<timestamp>` before merging.
- **Never overwrite an existing vault** or existing personal data without asking.
- **Idempotent:** if something is already installed, skip or update — don't duplicate.
- Detect the OS and use `installer/install-mac.sh` (macOS) or `installer/install-windows.ps1` (Windows) for platform steps.

## 1. Prerequisites

Check `git`, `node`, `python3`, and the `claude` CLI are present. Install any missing:
- macOS: Homebrew (`brew install …`).
- Windows: winget (`winget install …`).
The platform script handles this — run it first.

## 2. Quiz — ask the user

Run the questions in `installer/quiz.md` via **AskUserQuestion**: their **name**, preferred **language**, **zones** (free-form, with the isolation explanation), whether to install **Codex**, and whether to install **ECC** (+ the code-project path if yes). Hold the answers for the steps below.

## 3. Obsidian app

Install Obsidian if absent: macOS `brew install --cask obsidian`; Windows `winget install Obsidian.Obsidian`.

## 4. Vault

1. Ask for the vault location (default: `~/Documents/<name-or-"Vault">`). If a vault already exists there, ask before proceeding.
2. Copy `shell/vault-template/*` (including dotfiles: `.obsidian/`, `.vault-meta/`, `.claude/`, `.gitignore`) into the vault path.
3. Install the `claude-obsidian` plugin via marketplace **`AgriciDaniel/claude-obsidian`** → plugin `claude-obsidian@agricidaniel-claude-obsidian` (this is already enabled in the settings template).
4. Run the plugin's own scaffolders on the vault: `bash <plugin>/bin/setup-vault.sh <vault>` then `bash <plugin>/bin/setup-mode.sh --mode para` (with cwd = vault). These create `.obsidian` config, PARA folders, and `wiki/{index,hot,log,overview}.md`.
5. Generate `.vault-meta/mode.json` from `mode.template.json` + the quiz zones — see `installer/steps.md` §Zones. Create each extra zone's folders + starter `_index.md`/`log.md`/`hot.md`.
6. `git init` the vault (local only, no remote).

## 5. Claude global settings

Back up `~/.claude/settings.json`, then merge `shell/claude/settings.template.json` into it: substitute `{{LANGUAGE}}` with the quiz language, keep the user's existing unrelated keys, and DO NOT enable `bypassPermissions`.

## 6. Custom skills

Copy every folder in `shell/claude/skills/*` into `~/.claude/skills/` (skip any that already exist unless updating).

## 7. Rules (CLAUDE.md + AGENTS.md)

Generate the vault's `CLAUDE.md` from `shell/vault-template/CLAUDE.template.md`, filling `{{VAULT_TITLE}}`, `{{OWNER_NAME}}`, `{{DATE}}`, and the `{{ZONE_*}}` blocks from the quiz (see `installer/steps.md` §Rules). Then generate `AGENTS.md` as the **Codex mirror**: same content with "Claude" → "Codex" (leave `claude-obsidian` plugin names intact).

## 8. Codex (only if chosen in the quiz)

1. Install the **Codex desktop app** (macOS: OpenAI download or `brew install --cask codex` if available; Windows: the app installer, else `npm install -g @openai/codex` with a note that the CLI's integration differs).
2. The user signs in with their own OpenAI account (writes `~/.codex/auth.json` — never copy this).
3. Back up `~/.codex/config.toml`, then merge the blocks from `shell/codex/config.template.toml` (the git-source marketplace + enable). `AGENTS.md` (step 7) provides the rules.
4. Tell the user Codex will prompt to trust the vault + hooks on first open — that's expected.

## 9. ECC (only if chosen in the quiz)

Install the SAME stack the author uses, into the user's code-project path:
```
cd <code-project> && git clone https://github.com/affaan-m/ECC .ecc-src && cd .ecc-src
npm install --omit=dev --ignore-scripts
node scripts/install-apply.js --profile core \
  --with lang:typescript --with framework:react --with framework:nextjs \
  --with capability:database --with capability:security --with capability:research \
  --target claude-project
cd .. && rm -rf .ecc-src
```
The two env paths it writes self-heal to the user's root. If the user gave no code-project path, print this command for them to run later (ECC is project-scoped — never global).

## 10. Launch shortcut

Create a shortcut that opens Claude Code in the vault: macOS a `~/Desktop/<name>.command` running `cd "<vault>" && claude`; Windows a `.lnk`. The platform script has a helper.

## 11. Done

Print a short "You're ready" message: the vault path, how to open it in Obsidian, and that they can just start talking to Claude in the vault (try `/wiki` or "ingest <file>"). Mention "run evolution" for self-maintenance.
