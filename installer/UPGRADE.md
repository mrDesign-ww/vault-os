# Clean install and full upgrade

How a machine with an older Vault OS, or with a different Claude setup altogether, is brought to exactly the
system in this repository without losing anything the person wrote.

The engine is `installer/vault-os.py` (package `installer/vaultos/`, Python standard library only, Python 3.9 or
newer). Claude drives it through the `vault-os-update` skill. A SessionStart hook (`hooks/vault-os-check.py`) tells
a main session when the installed system is outdated, unstamped or half-upgraded.

## 1. Goal and the two rules

1. After an upgrade every system location matches the repository exactly. Anything in a system location that the
   repository does not ship is removed: the person's own skills, hooks, agents, commands, output styles, settings
   keys, and leftovers of an older release. One exception (2.1): in `~/.codex/skills` and `~/.agents/skills` a
   folder an earlier release mirrored there that this release no longer ships, and that the person changed or
   added a file to, stays in place and is no longer compared. An edited copy of a skill this release does ship in
   that folder is replaced like any system path, its old copy archived first.
2. Nothing personal is deleted or changed. Personal notes may be moved into the system's folder structure, never
   deleted. Everything the upgrade removes or overwrites is first copied into a backup archive that is never
   deleted automatically.

The upgrade is planned first, shown to the person, applied only after one explicit confirmation, and verified by
a parity check that must report 0 differences. A fresh install is the same command on an empty machine.

## 2. The boundary: system versus personal

In two lines: everything the repository ships into `~/.claude`, `~/.codex`, `~/.agents` and the vault's system
folders is system and is replaced exactly; notes, attachments, memory, sessions, credentials, caches and every
key or table the repository does not set in a mixed file are personal and stay byte for byte.

### 2.1 System (owned by Vault OS, replaced on upgrade)

Claude home (`~/.claude`):

| Path | Rule |
|---|---|
| `CLAUDE.md` | rendered from `shell/claude/CLAUDE.template.md` |
| `agents/`, `hooks/`, `docs/`, `templates/`, `statusline/`, `scripts/`, `skills/`, `commands/`, `output-styles/` | owned folders: exactly the repository's children (filtered per platform), nothing else; a folder the repository does not ship must be absent |
| `settings.json` | split (2.3): system keys replaced, personal keys kept |
| `vault-os/installed.json` | the stamp (section 3) |
| `skills/<upstream>` and its `<upstream>-*` children | pinned upstream skills when selected (2.5) |

Codex, only with the Codex integration (`--codex`):

| Path | Rule |
|---|---|
| `~/.codex/hooks.json` | rendered from `shell/codex/hooks.template.json` (merge result on an empty file) |
| `~/.codex/config.toml` | split (2.4) |
| `~/.codex/skills/`, `~/.agents/skills/` | owned folders: exactly the owner's live sets, not every bundled skill: the Claude skill folders `claude_mirror` lists for that folder in `shell/codex/skill-targets.json` and `vault-os-update`, the root licence files, the `shell/codex/skills` folders it lists for `codex` or `agents`, plus the upstream host copies (2.5); Codex's own `.system` is not touched. A folder an earlier release mirrored there and this one does not ship is removed (archived first) only when it is byte-identical to what 28777ca (`installer/vaultos/legacy-mirror.json`) or the Claude set installed; one the person changed or added a file to stays in place and is no longer compared |
| `~/.codex/prompts/` | owned folder; absent, because the repository ships none |
| `~/.codex/AGENTS.md` | absent, because the repository ships none |

Vault:

| Path | Rule |
|---|---|
| `CLAUDE.md`, `AGENTS.md`, `Reference/*`, `scripts/*` and `.vault-meta/*` shipped files | exact rendered template, trust chain bound (section 6); a person's own file at a `Reference/*` path moves first (path classes below), a symbolic link there, or a link or file above it, is renamed beside first (2.2) |
| `.gitignore` | merged: no file, or a file of template lines only, gives the template; otherwise the person's lines first (order and comments kept, exact template lines dropped), then the system block between `# >>> vault-os managed` and `# <<< vault-os managed` (Git lets the last matching rule win, so system rules keep working); moved files the old rules ignored get an anchored rule (section 7) |
| `.obsidian/core-plugins.json` | template plugins set `true` are written `true`; template plugins set `false` keep the person's value (the template's when missing); other keys (`sync`, `publish`, the person's own) are personal; the older list format counts as `true` for each listed id |
| `.obsidian/community-plugins.json` | the person's ids in their order, missing template ids appended; ids only an earlier release's template listed (the 28777ca list, `installer/vaultos/shipped-versions.json`) are removed only in a vault that had Vault OS before (a stamp or a Vault OS `mode.json`), never in a foreign vault; plugin folders are never touched |
| `.obsidian/app.json`, `.obsidian/plugins/*/data.json` | template keys system, other keys personal |
| `.claude/settings.json` | exact |
| `.claude/settings.local.json` | split by the Claude settings rules (2.3) |
| `.claude/skills/`, `.claude/agents/`, `.claude/commands/`, `.claude/output-styles/`, `.claude/hooks/` | owned folders; absent when the template ships none |
| `.agents/skills/` | owned folder |
| `.vault-meta/memory-approvals.json`, `memory-evals.json`, `evolution/reflection-policy.json` | produced by the trust binding; the person's own L3 approvals and eval cases are carried over |
| the two doctrine pages in `wiki/resources/concepts/` | system L3 doctrine |
| every other template page under `wiki/` (indexes, logs, hot caches, dashboards) | seeds: created when missing, never overwritten |

Inside owned folders, names that start with a dot (for example Codex's own `skills/.system`) and OS junk
(`.DS_Store`, `Thumbs.db`, `desktop.ini`, `__pycache__`, `*.pyc`) are neither compared nor removed. Paths that an
older stamped release shipped and this release does not are removed as leftovers; in the vault only when the live
file still equals the stamped one, otherwise it is personal and stays.

Vault path classes (`manifest.vault_path_class`, every vault system path has exactly one):

- note paths: `Reference/*`, the two doctrine pages and any `.md`, `.canvas` or `.base` outside dot folders and
  `scripts/`, except the root `CLAUDE.md` and `AGENTS.md`. A person may own a file there. The live file is the
  system's only when it equals the desired bytes, or the SHA-256 the stamp recorded, or a shipped version of that
  template (both sides normalised: dates, `{{DATE}}`, the owner name and the policy hash fold into tokens; the
  versions of 28777ca and later are in `installer/vaultos/shipped-versions.json`), or, for a doctrine page, when
  the live `memory-approvals.json` lists it under `l3`. Anything else is personal: the migration moves it below
  the inbox, keeping its path (`<inbox>/Reference/_index.md`, ` (n)` on a collision, links rewritten, reason
  "personal file at a system path") before the template is written there. It is never replaced in place.
- config paths: the root `CLAUDE.md` and `AGENTS.md` (assistant instructions are the person's old system,
  archived and replaced like `~/.claude/CLAUDE.md`), `scripts/*`, `.vault-meta/*`, `.claude/*`, `.agents/*`.
- mixed files: `.gitignore` and `.obsidian/*.json`, merged by the rows above.

### 2.2 Personal (never deleted, never changed)

- Vault notes, attachments, canvases, sources and every vault file outside the rows above, including the
  person's own files in `scripts/`, `.vault-meta/` runtime state (indexes are rebuilt, trace receipts stay) and
  other files in the vault's `.claude/` folder.
- Claude memory (`~/.claude/projects/*/memory`), transcripts and every session file (`projects/`, `todos/`,
  `plans/`, `file-history/`, `shell-snapshots/`, `history.jsonl`, logs).
- Credentials and auth: `~/.claude.json`, `.credentials*`, Codex `auth.json`, MCP servers and tokens.
- Plugin caches (`~/.claude/plugins/`), `keybindings.json`, Codex `sessions/`, and any other file in a home that
  is not in a system row.
- The person's Git history in the vault. The upgrade commits only system paths and index receipts, spelled as the
  disk stores them (on a case-insensitive volume the person's `Scripts/` holds the system `scripts/` files). A
  failed add or commit leaves nothing staged and fails the upgrade, which rolls back (exit 7); it never reports
  "upgrade complete".
- Symbolic links at the vault's personal paths and folders that hold their own Git repository: kept in place as
  they are (section 7).

A wrong shape at a vault system path is the person's: a symbolic link of any kind (to a file or a folder, broken, a
link to a link) at the path or at a folder above it (for example `Reference/` itself, `wiki/` or `.claude/skills/`),
a regular file where the system needs a folder (`wiki`, `.claude`, `.obsidian`), or a folder where the system keeps
a file (`CLAUDE.md/`). The path-shape preflight (5.1 step 0) finds each one before planning, and phase 0 renames it
beside its path, in the same folder, with ` (2)`, ` (3)` on a taken name (`Reference (2)`, `wiki (2)`,
`Reference/_index (2).md`; reasons "symbolic link at a system path", "file where the system needs a folder",
"folder where the system keeps a file"). Its bytes are not changed (a folder moves as one unit), a link's target is
never read, written or deleted, and a relative target keeps resolving because the folder stays the same. The stamp
keeps the renamed paths, so later plans keep them in place ("renamed beside a system path by an earlier upgrade")
instead of sorting them into the inbox. A rename never lands in a zone: inside one (below `wiki/`, no dot name on
the way, for example `wiki/personal/inbox`) a wrong shape is a plan question with a way out instead, because the
memory index refuses links there and the person decides what a file in the zone's place is. Then the system file
or folder is written there as a real one. Inside an owned folder (`.claude/skills/<name>`, `.agents/skills/<name>`)
every child is the system's (2.1): a link, file or folder there is archived and replaced, as in `~/.claude`,
`~/.codex` and `~/.agents`, where a link or a file at a system path (or at a folder above one, such as
`~/.claude/vault-os/`) is archived as it is and replaced. Nothing is written or removed below a linked folder
anywhere. A plan that lists one path as both kept and changed (written, removed or moved, or below it) is an engine
error: it stops before any write (exit 8).

### 2.3 Claude settings split (`settings.json`, vault `.claude/settings.local.json`)

The desired file is the template (the settings a fresh install writes on an empty home), plus the personal keys
of the current file in their order:

- Keys the template sets are system. `hooks`, `enabledPlugins`, `extraKnownMarketplaces`, `skillOverrides` and
  `modelSettings` are replaced as a whole: the person's own hooks and plugins do not survive.
- `env` is merged entry by entry: the template's entries are set, entries the previous stamp recorded as system
  are removed, every other entry is personal.
- `permissions`: `defaultMode` and the template's `allow` rules are system (bypass mode only with
  `--bypass-permissions`); allow rules the previous stamp recorded as system are removed; every other rule and
  key (`deny`, `ask`, `additionalDirectories`) is personal.
- Any other key is personal when it is one of `apiKeyHelper`, `awsAuthRefresh`, `awsCredentialExport`,
  `otelHeadersHelper`, `forceLoginMethod`, `forceLoginOrgUUID`, `model`, `availableModels`, `theme`, `editorMode`,
  `preferredNotifChannel`, `autoUpdates`, `autoUpdatesChannel`, `cleanupPeriodDays`, `feedbackSurveyState`,
  `additionalDirectories`, `sandbox`, `enableAllProjectMcpServers`, `enabledMcpjsonServers`,
  `disabledMcpjsonServers`, `defaultShell`, `localPlugins`, `skipDangerousModePermissionPrompt`, `autoMode`, or its
  name matches `auth|token|secret|key|credential|password|login|helper|account|org|proxy|cert|mcp|aws|gcp|vertex|bedrock|otel|dir|path|env`
  (case-insensitive). Every other key is an old system key and is removed.
- Hook groups that run a kept upstream skill (`/skills/<name>/` or `/skills/<name>-` in the command) survive
  after the system hooks.

Every set or removed key is listed in the plan; the old file is in the archive.

### 2.4 Codex config split (`config.toml`)

System: the top-level keys the template sets (`model`, `model_reasoning_effort`, `service_tier`), the tables it
sets (`[features]`), and every `marketplaces.*` and `plugins.*` table. They are written inside marked blocks
(`# >>> vault-os managed` ... `# <<< vault-os managed`). Everything else (other keys, profiles, MCP servers,
project trust, `[hooks.state]`) is personal and stays byte for byte. Parity compares the system part as values,
so comments and layout of an add-only install do not count as differences.

### 2.5 Upstream skills (`--upstream-skills`)

`shell/claude/upstream-skills.json` pins gstack (setup kind) and Remotion (folder kind) to a commit.

- A setup-kind install whose Git `HEAD` equals the pin, or a folder-kind install whose stamp records the pin and
  the same tree hash, is adopted: it stays exactly as it is, with its children (`<name>-*` folders, links into it,
  recorded children) and its hooks.
- Anything else is reinstalled: its children and hooks are removed, the pinned source is fetched before any live
  change (a failed fetch stops with exit 6 and nothing changed), then the setup runs with `HOME` set to the
  person's home.
- A missing required tool (`bun` for gstack) skips the item with a warning; an existing install is then left as
  it is.
- Writes of an upstream setup outside the Claude home (gstack: `~/.gstack`, `~/.bun`, browser caches) are not
  part of the archive and are not rolled back.
- With `--codex`, an item's `hosts` entry also installs it for Codex and `~/.agents`, at the same pin.
  gstack (`hosts.codex`) runs `./setup --host codex` with the Claude flags in the Claude checkout, with `HOME` and
  `CODEX_HOME` set; that writes `~/.codex/skills/gstack` (a runtime folder) and the `~/.codex/skills/gstack-*`
  links into the checkout's generated `.agents/skills`, and needs no network beyond the pinned clone. Remotion
  (`hosts.agents`) is copied from the same fetched folder to `~/.agents/skills/remotion-best-practices`.
- A host copy is adopted when its stamp record has the pin (and, for a folder, the same tree hash) and it
  exists; the Codex children are then kept exactly. A Claude reinstall, a new pin or a missing copy reinstalls the
  host: its folder and its recorded children are archived and removed first, then the host setup or copy runs.
  Rollback restores the host folders and the checkout's `.agents` render from the journal; `~/.gstack` is not
  rolled back.

## 3. System identity: version, manifest, stamp

- `VERSION` at the repository root holds the release version `YYYY.MM.DD[.N]`. A GitHub release is tagged
  `v<VERSION>`.
- The manifest is the exact desired state of every system path. It is not a checked-in file: the engine builds it
  in a temporary staging folder from the repository and the person's answers, with the installers' own code
  (`install-claude-layer.py`, `install-codex-layer.py`, `render-vault.py`), so a fresh install, an add-only install
  and an upgrade agree by construction.
- The stamp `~/.claude/vault-os/installed.json` records the result:

```json
{
  "schema_version": 1, "system": "vault-os", "state": "installed",
  "version": "2026.10.20", "repo": "mrDesign-ww/vault-os",
  "source": {"commit": "<git sha or null>", "tree_sha256": "<hash of installer/, shell/ and VERSION>"},
  "installed_at": "2026-10-20T12:00:00Z", "archive": "<backup archive of this upgrade>",
  "answers": {"owner": "...", "language": "...", "vault": "/abs/vault", "title": "...", "client_label": "...",
              "date": "YYYY-MM-DD", "python": "/abs/python3", "platform": "darwin", "codex": true,
              "upstream_skills": false, "bypass_permissions": false},
  "homes": {"claude": "/abs/.claude", "codex": "/abs/.codex", "agents": "/abs/.agents"},
  "manifest": {"claude": {"entries": {"agents/builder.md": {"type": "file", "sha256": "...", "exec": false}},
                          "owned_dirs": {"skills": ["..."]}, "absent": [], "protected": [],
                          "splits": ["settings.json"], "seeds": []},
               "codex": {}, "agents": {}, "vault": {}},
  "upstream": {"gstack": {"kind": "setup", "commit": "...", "children": ["gstack-review"], "status": "installed"}},
  "settings": {"system_env": ["..."], "system_allow": ["..."]}
}
```

A `tree` entry hashes a whole folder: SHA-256 over sorted lines `<path>\0<exec bit>\0<sha256>` (links as
`link:<target>`), junk excluded. During an upgrade `state` is `upgrading`; only the last step writes `installed`.
A stamped machine whose release and source tree are unchanged plans nothing and writes nothing.

## 4. Detection and the SessionStart check

`vault-os.py detect` classifies the machine:

| Result | Meaning |
|---|---|
| `fresh` | no `CLAUDE.md`, `settings.json` or non-empty `skills/`, `agents/`, `hooks/`, `commands/`, `output-styles/` in the Claude home |
| `foreign` | a Claude setup without a stamp |
| `pre-stamp` | a setup without a stamp that has at least 5 of this release's skill names, or a Vault OS `CLAUDE.md` |
| `installed` | stamped; reports the version and `outdated` when this release is newer |
| `interrupted` | the stamp says `upgrading`, or an archive journal has no commit record |

`INSTALL.md` step 0 routes every result except `fresh` to the upgrade, never to the add-only steps.

`hooks/vault-os-check.py` runs at session start (matcher `startup`, 5 second limit) in main sessions only and
prints at most one line of context:

- no stamp: this setup may differ from the published system; offer the `vault-os-update` skill;
- stamp `upgrading` and no live process holds the upgrade lock: an upgrade was interrupted; recover it before other
  work (while the lock's process is alive the upgrade is still running and the hook says nothing);
- stamp older than the latest release: name both versions and offer the update;
- current, offline, any error, a subagent, or `VAULT_OS_CHECK=off`: nothing.

The latest version comes from the GitHub `releases/latest` API (`tag_name`), else from `VERSION` on `main`. The
answer is cached in `~/.claude/vault-os/check-cache.json` for 24 hours (a failure for 6 hours); all requests
together get about 2 seconds and send nothing but the GET. `VAULT_OS_CHECK_URL` points it at another URL (tests). A foreign
setup does not run the hook; it is found by `detect` when Claude runs the installer or the skill.

## 5. Modes

```
python3 installer/vault-os.py detect   [--json]
python3 installer/vault-os.py upgrade  [answers] [--map SRC=DST ...] [--json]   dry run: the plan and its id
python3 installer/vault-os.py upgrade  [answers] --confirm PLAN_ID [--approve-memory-policy]
python3 installer/vault-os.py parity   [answers] [--json]                       0 differences expected
python3 installer/vault-os.py rollback ARCHIVE [--json]                         preview: restores, stays, moves aside
python3 installer/vault-os.py rollback ARCHIVE --confirm ROLLBACK_ID            apply exactly that preview
python3 installer/vault-os.py recover                                           roll back an interrupted upgrade
```

The same through the installers: `install-claude-layer.py --upgrade [answers]` and `--rollback ARCHIVE [--confirm
ID]` hand over
to the engine (`--mcp`, `--extra-skills-dir` and `--claude-bin` are separate install steps and are refused there);
`install-mac.sh` has `detect_system`, `upgrade_system`, `rollback_system`, `recover_system`; `install-windows.ps1`
has `Detect-VaultOS`, `Upgrade-VaultOS`, `Rollback-VaultOS`.

Answers: `--owner --language --vault --title --client-label --python`, `--codex/--no-codex`,
`--upstream-skills/--no-upstream-skills`, `--bypass-permissions/--no-bypass-permissions`. Precedence: flag, then
the stamp, then what the old system says (vault `mode.json` owner and client label, vault `CLAUDE.md` title,
`settings.json` language, the Python of its hook commands, `permissions.defaultMode`, an upstream skill folder,
Codex markers), then defaults (English, the running Python, the vault folder name, `Client`, all options off). A
missing owner or vault stops with the questions to ask. `--home`, `--claude-home`, `--codex-home`, `--agents-home`
and `--backup-root` exist for tests and unusual layouts.

Exit codes: 0 done, 1 parity differences, 2 usage, 3 questions or approval needed (also a plan or rollback id that no
longer matches), 4 locked (a live upgrade, rollback or recover holds the lock, or a stale lock) or interrupted, 5 not
on Windows, 6 an upstream fetch failed (nothing changed), 7 the upgrade
failed and was rolled back, 8 engine error (the plan contradicts itself; nothing changed).

### 5.1 The upgrade, step by step

0. Path-shape preflight (read-only, part of the plan): every vault path the plan writes, splits, seeds or moves
   into, every folder above one, the owned folders and the folders the vault tools write into
   (`.vault-meta/memory-index`, `write`, `locks-v2`) is classified with lstat: absent, folder, file, link to a file,
   link to a folder, dangling link, link chain. The plan gives the expected shape: a file (or nothing) at a written
   file, a real folder (or nothing) everywhere above. Each mismatch becomes exactly one of two things (2.2): a
   rename beside its path in phase 0, or, inside a zone, a plan question with a way out. The move destinations add
   the folders above them, so the preflight runs again until nothing new is needed. A move never lands on an
   existing path or on a folder another destination needs: it gets ` (n)`.
1. Plan (read-only): stage the desired state, compare it with the machine, plan upstream skills and the vault
   migration. The plan lists every replace, add and remove per layer, every settings key set or removed, every
   note move, rename and link rewrite, folders that need a `--map`, warnings, and whether the L3 doctrine needs
   approval. Before it is shown, the plan's phases are replayed over the lstat shapes: no move or write may go
   through a link or a file (a path phase 0 renamed included), no move may land on an existing path, no write may
   replace a folder or a link outside an owned folder. A contradiction is an engine error (exit 8, nothing changed),
   so a dry run that exits 0 never fails on a path's shape at `--confirm`. Its id is the SHA-256 of the canonical
   plan.
2. Confirmation: the person reads the plan and says yes once. `--confirm` must carry the same plan id: if the
   machine changed since the dry run the id differs and nothing runs. Open questions refuse the plan; a doctrine
   that needs approval also needs `--approve-memory-policy` (the same yes, explained by the skill).
3. Preflight: refuse on Windows, while the lock `<backup root>/.vault-os.lock` is held, or while an interrupted
   upgrade is unrecovered. Fetch upstream sources.
4. Apply under a write-ahead journal, each path copied into the archive and its record fsynced just before its
   change: a link or file where `~/.claude/vault-os/` goes (archived), then the interim stamp `upgrading`; Claude
   layer (removals, files and folders, settings split, seeds); upstream installs; Codex; `~/.agents`; then the vault
   in phases: phase 0 every rename beside, phase 1 the migration moves and link rewrites with their own invariant
   check, phase 2 the system files and folders, splits and seeds, phase 3 removals; vault Git checkpoint (system
   paths committed, the three zone indexes rebuilt under their locks, receipts committed; a failed commit rolls back
   like a failed index build); the final stamp. The homes have no renames or moves: their removals (a link or file
   in the way included) come first. `mapping.json` lists the moves in this apply order and `undo.py` reverses it.
5. Verify: a full re-plan must have 0 actions, 0 moves and 0 rewrites, and the trust chain must pass
   (`reseal-policy.py --check` and `load_policy()`). Then the journal gets its commit record.

Any failure in steps 4 and 5 rolls the whole upgrade back and exits 7. A second upgrade with nothing new plans 0
actions and writes nothing, not even an archive.

### 5.2 Backup archive

```
~/vault-os-backups/20261020-120000-ab12/
  README.txt              how to roll back
  plan.json               the confirmed plan
  stamp-before.json
  journal.jsonl           one fsynced record before each live change
  files/claude/... files/codex/... files/agents/... files/vault/...   originals, modes and links kept
  migration/mapping.json  every move, rename and link rewrite, with hashes before and after
  migration/undo.py       stand-alone undo of the note migration only
  displaced/              anything a rollback had to move out of the way
```

The archive lives outside every system location and nothing deletes it automatically.

### 5.3 Rollback and recovery

`rollback ARCHIVE` first prints a preview and changes nothing: how many paths it restores, what stays in place, and
what moves to the holding folder `<archive>/displaced/`, each with its reason (`--json` gives the same as data). It
ends with the exact command `rollback ARCHIVE --confirm <id>`. The id hashes the preview with the live states it
covers: when they change, the id changes and an old id is refused (exit 3). The person reads the preview and says yes
once; only then `--confirm` runs. It refuses an archive that is not the latest one, one already rolled back, and an
interrupted one (that is `recover`).

The rollback replays the journal backwards: restores overwritten and removed items from the archive copies, moves
notes back, removes the empty folders it created and recreates the folders it removed. Just before its commit record a
successful upgrade journals what it left (`settle` records) at every path a tool changed (vault Git metadata, index
and lock paths, `settings.json` after an upstream setup) and the children of every tool folder. That settled state is
the upgrade's own; anything newer is the person's:

- What still equals the upgrade's output is replaced by the backup. A file the upgrade wrote that changed since (an
  edited system file, an edited note whose links were rewritten) goes to `displaced/` first; the preview lists each.
- A folder the upgrade made that holds files created after the upgrade stays in place with them (only junk is
  cleared). Children that appeared below a tool folder after the upgrade stay. A moved note that is no longer at its
  new place, or whose old place is taken again, stays where it is.
- Vault Git metadata (`HEAD`, refs, logs, `packed-refs`, index, `ORIG_HEAD`, `COMMIT_EDITMSG`) is restored only
  while `HEAD`, refs, `packed-refs` and logs still equal the settled state, so the branch points where it pointed. An
  index that only `git status` refreshed is restored and its live copy goes to `displaced/`, labelled regenerable.
  When the person committed after the upgrade, all Git metadata stays as it is: the preview says the history is kept
  and the restored files will show as changes.
- Index and lock state that changed (`.vault-meta/memory-index`, the write locks) is restored, its live copy in
  `displaced/`, labelled regenerable.

A rollback right after an upgrade returns the exact previous tree, and a rollback never deletes data it did not
write. The upgrade's Git commits stay in the object store as unreferenced objects until `git gc` prunes them later;
the rollback never deletes them itself.

`recover` rolls back, unattended, every upgrade whose journal has no commit record (a killed process, a crash, a power
cut) to the exact previous tree, then clears the lock. It checks the lock's pid first: while that process lives (an
upgrade still running) it refuses with exit 4 and touches nothing; a missing or bad pid counts as dead. An upgrade
removes only a lock it took. The automatic rollback of a failed upgrade is the same unattended replay. Records are
written before their actions, rollback steps are idempotent, a torn last journal line is ignored, and live writes go
through a temporary file and an atomic rename, so recovery returns the exact previous tree whether or not the last
recorded action happened.

## 6. Vault runtime and the trust chain

The vault runtime is sealed: `reflection-policy.json` hashes the guarded code, `reflection_policy.py` anchors the
policy digest, the doctrine page carries it, `memory-approvals.json` hashes the doctrine pages and
`memory-model.py` anchors the approvals and evals manifests. The upgrade never patches hashes by hand and never
reseals the live vault in place:

1. The vault system files are rendered into the staging folder by `render-vault.py`'s own `render_files` and
   `bind_trust`, bound to the real vault path, the owner and the approval date.
2. The person's own L3 approvals (with the paths their pages move to) and eval cases are merged in, the evals
   anchor is set, and the staged `reseal-policy.py` runs until the chain is a fixed point.
3. The staged files are then ordinary system files of the plan. After the upgrade the live chain must pass
   `reseal-policy.py --check` and `load_policy()`.

Approval date: when the live doctrine pages equal the release's pages (apart from the policy hash line), the
existing `approved_on` is kept and no new approval is needed. Otherwise the date is today and the plan needs
`--approve-memory-policy`. An own approved L3 page that gets link rewrites is named in a warning: its approval no
longer matches and must be given again. Trace generations sealed under an older policy stay on disk; the new seal
does not trust them.

## 7. Vault migration

When a note or attachment sits outside the system's structure, the plan maps it into the structure. Nothing is
deleted; files are renamed inside the vault.

Already in place (never moved): `wiki/workspace/projects/**`, `wiki/areas/**`, `wiki/resources/**`,
`wiki/archives/**`, `wiki/personal/**`, `wiki/client/**`, the scaffold pages at the top of `wiki/`,
`Reference/**`, `_attachments/**`. Never scanned: dot folders, `scripts/` (any case of the name on a
case-insensitive volume), system paths.

Kept in place, listed in the plan, never scanned or moved:

- Symbolic links (to files or folders, broken ones included): "symbolic link". They stay usable as link targets
  by path; a relative link whose target moves, or whose target folder holds files that move, is named in a
  warning. A link at a system path is not kept: it is renamed beside that path (2.2), with the same warning.
- Paths an earlier upgrade renamed beside a system path (the stamp lists them): "renamed beside a system path by
  an earlier upgrade", one unit each.
- A folder (not the vault root) that holds `.git` (a folder or a file): "Git repository", as one unit. Its files
  stay link targets but are never rewritten; links in it that the moves would break are named in a warning.

Rules, first match wins (a personal file at a system note path, 2.1, always goes below the inbox, keeping its
path):

1. `--map SRC=DST` given by the person (longest source prefix).
2. A folder that looks like a zone of the old system (a zone root in the old `mode.json`, or a folder holding
   `_index.md` with `hot.md` or `log.md`) whose name is not `personal`, `client` or `work`: blocked. The plan asks
   for a `--map` and cannot be confirmed until it has one, because merging it into another zone would widen its
   privacy boundary.
3. Frontmatter: `zone: work|personal|client`, then `para:` or `category:` or a `para/<bucket>` tag, then
   `status: archived`. A frontmatter zone never widens a folder's zone: a note in a personal or client folder
   stays in that zone even with `zone: work`.
4. The first folder of the path (case, numbering and emoji prefixes ignored, English and Russian names): projects,
   areas, resources, archives, inbox, people, concepts, templates, attachments, personal, client.
5. Everything else goes to the inbox, keeping its folder path below it: the work inbox
   `wiki/workspace/projects/inbox/` in a vault that had Vault OS before (a stamp or a Vault OS `mode.json`), the
   personal inbox `wiki/personal/inbox/` in a foreign vault, whose notes are private until the person sorts them.
   The stamp keeps this origin, so later plans of the same install agree.

The rest of the path below the matched folder is kept. Attachments (any file that is not a note or canvas) move
by their folder's rule; one used by exactly one note that moves for its frontmatter, and stored in that note's
folder, follows the note. Old folders left empty are removed (the undo recreates them), except a folder that holds
a system path, so it is never removed and created again.

Collisions: a taken destination (case-insensitive, system and seed paths included) gets ` (2)`, ` (3)` before the
extension. A moved note whose name another note already uses gets the same suffix, because the memory index
expects unique note names; the names of the system's own notes (`wiki/index.md`, `hot.md`, `overview.md`,
`dashboard.md` and the other seeds) count, so a moved `index.md` or `hot.md` becomes `index (2).md`. Only `_index`,
`log` and `readme` may repeat. Notes in place keep their names.

Ignored files: every moved file that the vault's old ignore rules matched (asked read-only with
`git check-ignore --no-index` against a temporary empty repository; tracked files are not ignored by Git and get
no rule) gets an anchored, escaped rule for its new path in the person's part of `.gitignore`; a whole ignored
folder that moves as one into a folder holding nothing else gets one folder rule.

The memory index after the upgrade is predicted in the dry run: the staged `scripts/memory-model.py` checks the
final layout of every zone (final paths, the bytes they will hold) for what stops its build (levels, hot cache
paths, duplicate note names, L3 approvals, provenance), plus symbolic links and hard links inside a zone. Each
predicted issue is a plan question naming the note, so the plan cannot be confirmed until the person fixes it.
The person's own approved L3 pages that get link rewrites are approved again with their new text, which needs
`--approve-memory-policy`. If a zone index still fails to build or validate during the upgrade, the upgrade rolls
back (exit 7) and the message names the zone and the issue; the verify step also validates every zone.

Links: every wikilink, embed, Markdown link and canvas file reference is resolved in the old layout (exact path,
relative path, unique path suffix, then name with same-folder and shortest-path preference). After the moves a
link that would no longer reach the same file is rewritten to the shortest form that does (the name when unique,
else the vault path), keeping heading, block and alias; when the target name changed and the link had no alias,
the old text becomes the alias so the note reads the same. Links in code blocks and inline code are not touched;
links that were already broken stay as they are. Bytes that are not valid UTF-8 survive a rewrite.

Invariants checked before the upgrade continues: the number of personal files is unchanged; every file that was
not rewritten has its SHA-256; every rewritten note, with its logged replacements reversed, has its original
SHA-256; every link that resolved before reaches the same file after. A personal file changed between the plan
and the apply stops the upgrade. Obsidian settings that name a moved folder are listed as warnings and not edited.

## 8. Safety rules

- A plan that the dry run shows without a question completes, or rolls back on a failure that is not about a
  path's shape (5.1 steps 0 and 1); a shape it cannot handle is a question before anything runs.
- Dry run first, always. No `--confirm` without the person's explicit yes in this session; agents and subagents
  never pass it on their own.
- Tests never touch a real home: every test runs on a temporary `HOME`.
- Never write or remove below a symbolic link; never delete: removal means copied into the archive, then removed.
  Rollback displaces instead of deleting.
- Writes are atomic (temporary file and rename). A move that fails (for example across a mount point inside the
  vault) fails the upgrade, which then rolls back.
- One upgrade at a time (the lock in the backup root).
- No network access except the version check, the `git clone` the skill runs, and the pinned upstream fetches when
  selected.

## 9. Windows

The vault runtime is POSIX-only. On Windows `upgrade --confirm`, `rollback` and `recover` stop before touching
anything, with "not yet on Windows: run the upgrade inside WSL" and exit 5; `detect`, the dry run and `parity`
work because they only read. The PowerShell helpers pass through to the engine and inherit the refusal.

## 10. Tests

`tests/test_upgrade.py`, `tests/test_vault_migration.py` and `tests/test_vault_os_check.py`, all on temporary
homes:

- fresh install: dry run writes nothing, approval required, parity 0, a second run writes nothing, rollback
  returns the empty home;
- the add-only step installers give parity 0, and the upgrade then writes only the stamp;
- the previous public release (28777ca) installed by its own procedure, plus the person's own skills, hooks,
  agents, commands, output styles, settings keys, Codex keys and tables and personal files, plus a vault with a
  foreign structure (numbered and nested folders, duplicate names, attachments, embeds, Markdown links, code
  blocks, a canvas, a frontmatter zone, a zone-like folder): blocked until mapped, then parity 0, personal files
  byte for byte, notes with their hashes, links resolving, the archive complete; rollback returns the exact tree;
  a second upgrade changes nothing;
- the same upgrade killed in the Claude, Codex and vault phases (migration included) and after the final stamp:
  detected as interrupted, refused until recovered, recovered to the exact tree, then completed;
- pinned upstream skills from local fixture repositories: installed, adopted with their links and hooks, rolled
  back, reinstalled at a new pin;
- `tests/test_codex_skills.py`: `--codex` gives exactly the planned `~/.codex/skills` and `~/.agents/skills`
  sets (the owner's live sets, with an old mirror trimmed and an edited mirrored skill kept) in the engine and the step installers, every bundled host skill has its licence, and the Codex and
  `~/.agents` host copies of upstream skills are installed, adopted, rolled back and reinstalled at a new pin;
- Windows refusals leave nothing; the split rules are idempotent;
- migration rules, collisions, reserved paths, code, canvas, blocked folders, the journal rollback, a torn journal
  line, and `undo.py`;
- `tests/test_path_shapes.py`: the path-shape matrix. The paths come from a baseline foreign vault's own plan (every
  vault path it writes, splits, seeds or moves to, every folder above one, the tool folders; one file path per
  folder and operation), each with each shape of step 0, in a fresh foreign vault with Git. Each case: the dry run
  exits 0 or 3 and writes nothing; a question names the path, refuses `--confirm` and runs nothing, and comes only
  inside a zone; anything at a move destination makes the move take a free name; exit 0 implies `--confirm` exit
  0; every original file and link is byte-identical somewhere in the vault (a config path or owned folder: in the
  archive; a mixed file: the person's part kept); link targets untouched; parity 0; a no-op second plan; an exact
  forced rollback. Plus the phase order and the warning for a link whose folder target moves;
- the SessionStart check: no stamp, interrupted, current, outdated and cached, offline, subagent, off, bad input,
  and its wiring in `hooks.json` and the installer.
