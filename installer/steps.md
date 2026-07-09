# Installer steps — generation detail

## Zones → mode.json + folders

Start from `shell/vault-template/.vault-meta/mode.template.json`. Replace `{{DATE}}` (`configured_at`) with today, and drop the `_installer_note` / `_example_extra_zone` helper keys in the final file. For EACH extra zone the user named:

- `slug` = lowercase, spaces → `-`.
- Add a zone block under `zones`:
  ```json
  "<slug>": {
    "label": "<Name>",
    "root": "wiki/<slug>/",
    "index": "wiki/<slug>/_index.md",
    "log": "wiki/<slug>/log.md",
    "hot": "wiki/<slug>/hot.md",
    "projects_folder": "wiki/<slug>/projects/",
    "inbox_folder": "wiki/<slug>/inbox/"
  }
  ```
- Under `zones.switch`, add `"to_<slug>": [<phrases in the chosen language>]` and optionally `"oneoff_<slug>_prefix": "<word>:"`.
- Create folders `wiki/<slug>/{projects,inbox}` and starter `_index.md`, `log.md`, `hot.md` (minimal frontmatter, empty body).

The evolution scripts read zones from `mode.json` at runtime — no code edits needed per zone.

## Rules → CLAUDE.md / AGENTS.md

Fill `shell/vault-template/CLAUDE.template.md`:
- `{{VAULT_TITLE}}` → e.g. `"<Name>'s Vault"`.
- `{{OWNER_NAME}}` → quiz name. `{{DATE}}` → today (YYYY-MM-DD).
- `{{ZONE_FOLDERS}}` → tree lines for each extra zone, e.g. `├── <slug>/           # <Name> zone (isolated)`.
- `{{ZONE_LIST}}` → `work` + extra names joined.
- `{{ZONE_DEFINITIONS}}` → one bullet per extra zone: name, `root: wiki/<slug>/`, its switch phrase.
- `{{ZONE_SWITCH_PHRASES}}` → a short block listing each zone's switch phrase in the chosen language.

Then write `AGENTS.md` = the same rendered file with `Claude` → `Codex` (leave `claude-obsidian` plugin names and file names intact). This is the Codex mirror.

## Backups (before any overwrite)

- `~/.claude/settings.json` → `~/.claude/settings.json.backup-<YYYYMMDD-HHMMSS>` before merging the template.
- `~/.codex/config.toml` → same pattern, before merging the Codex blocks.
- If the target vault path already exists and is non-empty, STOP and ask the user.
