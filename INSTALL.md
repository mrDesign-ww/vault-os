# Installing Vault OS

These are the instructions for Claude Code when a user asks to install Vault OS from this repository.

## Safety rules

- Create the vault only in a new or empty directory.
- Never copy a real vault into this repository.
- Back up existing global Claude and Codex configuration before merging anything.
- Never copy authentication files, API keys, local MCP configuration, trust hashes, or machine-specific paths.
- Initialize Git locally inside the new vault. Do not add a remote.
- The hardened memory and reflection runtime currently requires macOS or Linux. On Windows, use WSL for the full system.

## 1. Check prerequisites

The full system needs Git, Python 3, Node.js, Claude Code, and Obsidian. On macOS, the helper can check or install the command-line prerequisites:

```bash
bash installer/install-mac.sh ensure_prereqs
bash installer/install-mac.sh ensure_obsidian
```

## 2. Ask the install quiz

Use `installer/quiz.md`. Collect the owner name, preferred language, vault title and path, client-zone label, optional Codex integration, optional ECC integration, and explicit approval for the two bundled L3 doctrine pages.

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
  status=$?
  bash scripts/wiki-lock.sh release ".vault-meta/write/$zone" "$token"
  [ "$status" -eq 0 ] || exit "$status"
done

git add .vault-meta/memory-index/*.authority.json
git commit -m "Bind memory index authority"
python3 scripts/memory-model.py retrieve "memory source of truth" --zone work --loadout general
```

Inspect the final result. It must include both bundled L3 pages with `memory_trust: approved` and a snippet stating that Obsidian is the source of truth.

## 5. Merge Claude settings and install skills

Back up `~/.claude/settings.json`, then merge `shell/claude/settings.template.json` into it. Replace `{{LANGUAGE}}`, preserve unrelated user keys, and never enable bypass permissions.

Copy the folders under `shell/claude/skills/` into `~/.claude/skills/`. Existing user-modified skills require confirmation before replacement.

The new vault already contains its local Claude lifecycle hooks in `.claude/settings.json`. Do not replace that file with the global settings template.

## 6. Optional Codex integration

If selected in the quiz:

1. Install the Codex desktop app and let the user sign in with their own OpenAI account.
2. Back up `~/.codex/config.toml`, then merge only the public marketplace and plugin blocks from `shell/codex/config.template.toml`.
3. Back up `~/.codex/hooks.json`, then render `shell/codex/hooks.template.json` into it with the absolute vault path substituted for `{{VAULT_PATH}}`.
4. Preserve the user's model, account, MCP servers, desktop settings, and existing hooks.
5. Never copy `[hooks.state]`. Codex creates its machine-specific trust hashes after the user approves the hooks.

## 7. Optional ECC integration

ECC is project-scoped and does not belong inside the vault. If selected, follow the current project install command in `installer/steps.md` using the user's chosen code-project path.

## 8. Open the vault

Open the new directory in Obsidian. The bundled `.obsidian` configuration lists the recommended community plugins, but Obsidian may require the user to install or approve them in its Community Plugins screen.

Optionally create a launch shortcut with `installer/install-mac.sh make_shortcut`.

Report the vault path, the two local Git commits, the retrieval acceptance result, and any optional integrations that were skipped.
