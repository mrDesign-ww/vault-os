"""Shared fixtures for the upgrade tests: temporary homes, the previous public install, personal extras, a vault
with a foreign structure. Everything runs below a temporary HOME; nothing touches the real machine."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "installer" / "vault-os.py"
LEGACY = "28777ca"  # the previous public install procedure (manual merge, no installer scripts)
sys.path.insert(0, str(ROOT / "installer"))

from vaultos import fsx, migrate  # noqa: E402


# No automatic Git housekeeping in fixture commands, the same flags the installer passes (UPGRADE.md 5.3): started by
# a commit it runs detached and writes .git/info/refs after the command returned, inside a later snapshot window.
GIT_QUIET = ("-c", "gc.auto=0", "-c", "maintenance.auto=false")


def git(*args, cwd, check=True):
    return subprocess.run(["git", *GIT_QUIET, "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          cwd=str(cwd), check=check, capture_output=True, text=True)


class Machine:
    """A temporary machine: HOME with .claude, .codex, .agents, a vault and the backup root."""

    def __init__(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="vault-os-upgrade-test-")))
        self.home = self.tmp / "home"
        self.claude = self.home / ".claude"
        self.codex = self.home / ".codex"
        self.agents = self.home / ".agents"
        self.vault = self.home / "Vault"
        self.backups = self.home / "vault-os-backups"
        self.home.mkdir()
        (self.tmp / "tmp").mkdir()  # the engine's staging folders, so a killed run leaves nothing outside

    def env(self, **extra):
        env = dict(os.environ, HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_NOSYSTEM="1",
                   TMPDIR=str(self.tmp / "tmp"))
        env.pop("VAULT_OS_FAULT_AFTER", None)
        env.update(extra)
        return env

    def engine(self, *args, env=None, timeout=900):
        return subprocess.run([sys.executable, str(ENGINE), *args, "--home", str(self.home)], capture_output=True,
                              text=True, timeout=timeout, env=env or self.env())

    def plan(self, *answers):
        out = self.engine("upgrade", *answers, "--json")
        if out.returncode not in (0, 3):
            raise AssertionError(out.stdout + out.stderr)
        return json.loads(out.stdout)["plan"], out.returncode

    def upgrade(self, *answers, approve=True, env=None):
        plan, _ = self.plan(*answers)
        extra = ["--approve-memory-policy"] if approve and plan["approval_needed"] else []
        out = self.engine("upgrade", *answers, "--confirm", plan["id"], *extra, env=env)
        return plan, out

    def rollback(self, archive, env=None):
        """Roll back as the update skill does: the preview (it must write nothing), then --confirm with its id."""
        quiet = fsx.snapshot(str(self.home), exclude=("Vault/.git/objects",))
        out = self.engine("rollback", str(archive), "--json", env=env)
        if out.returncode != 0:
            return out
        if fsx.snapshot(str(self.home), exclude=("Vault/.git/objects",)) != quiet:
            raise AssertionError("the rollback preview wrote")
        preview = json.loads(out.stdout)["rollback"]
        return self.engine("rollback", str(archive), "--confirm", preview["id"], env=env)

    def snapshot(self):
        """Exact tree of the home, without the backup archives and the vault's Git object store."""
        snap = fsx.snapshot(str(self.home), exclude=("vault-os-backups", "Vault/.git/objects"))
        return snap

    def cleanup(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)


def legacy_available():
    try:
        out = subprocess.run(["git", "cat-file", "-e", LEGACY + "^{commit}"], cwd=str(ROOT), capture_output=True)
    except OSError:
        return False
    return out.returncode == 0 and os.path.exists("/usr/bin/python3")


def legacy_tree(dest):
    """The repository at the previous public release, unpacked into dest."""
    dest.mkdir(parents=True)
    archive = subprocess.run(["git", "archive", "--format=tar", LEGACY], cwd=str(ROOT), capture_output=True,
                             check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
    return dest


def legacy_install(m, codex=True):
    """Install the previous release by its own procedure (INSTALL.md at that commit) into machine m."""
    old = legacy_tree(m.tmp / "old-release")
    env = m.env()
    subprocess.run([sys.executable, str(old / "installer" / "render-vault.py"), "--target", str(m.vault),
                    "--owner", "Test Owner", "--title", "Old Vault", "--language", "English",
                    "--client-label", "Client", "--date", "2026-09-07", "--approve-memory-policy"],
                   check=True, capture_output=True, env=env)
    git("init", "-q", cwd=m.vault)
    git("add", ".", cwd=m.vault)
    git("commit", "-q", "-m", "Initialize Vault OS", cwd=m.vault)
    for zone in ("work", "personal", "client"):
        token = subprocess.run(["bash", "scripts/wiki-lock.sh", "acquire", ".vault-meta/write/" + zone, "--ttl",
                                "3600"], cwd=str(m.vault), capture_output=True, text=True, env=env,
                               check=True).stdout.strip()
        for step in ("build", "validate"):
            subprocess.run([sys.executable, "scripts/memory-model.py", step, "--zone", zone, "--lock-token", token],
                           cwd=str(m.vault), capture_output=True, env=env, check=True)
        subprocess.run(["bash", "scripts/wiki-lock.sh", "release", ".vault-meta/write/" + zone, token],
                       cwd=str(m.vault), capture_output=True, env=env, check=True)
    git("add", "-f", *[str(p.relative_to(m.vault)) for p in (m.vault / ".vault-meta" / "memory-index").glob(
        "*.authority.json")], cwd=m.vault)
    git("commit", "-q", "-m", "Bind memory index authority", cwd=m.vault)
    # Step 5 of that release: merge the settings template by hand, copy the skills.
    template = json.loads((old / "shell" / "claude" / "settings.template.json").read_text(encoding="utf-8")
                          .replace("{{LANGUAGE}}", "English"))
    template.pop("_comment", None)
    m.claude.mkdir(parents=True, exist_ok=True)
    (m.claude / "settings.json").write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    shutil.copytree(str(old / "shell" / "claude" / "skills"), str(m.claude / "skills"), symlinks=True)
    if codex:
        m.codex.mkdir(parents=True, exist_ok=True)
        hooks = (old / "shell" / "codex" / "hooks.template.json").read_text(encoding="utf-8")
        (m.codex / "hooks.json").write_text(hooks.replace("{{VAULT_PATH}}", str(m.vault)), encoding="utf-8")
        config = (old / "shell" / "codex" / "config.template.toml").read_text(encoding="utf-8")
        blocks = config[config.index("[marketplaces."):]
        (m.codex / "config.toml").write_text(PERSONAL_CODEX + "\n" + blocks, encoding="utf-8")
        for target in (m.codex / "skills", m.agents / "skills"):
            shutil.copytree(str(old / "shell" / "claude" / "skills"), str(target), symlinks=True)
    return old


PERSONAL_CODEX = '''model = "my-own-model"
approval_policy = "never"

[profiles.fast]
model = "my-fast-model"

[mcp_servers.private]
url = "https://mcp.example.invalid/private"

[hooks.state]
"hook-a" = "0123abcd"
'''

PERSONAL_FILES = {  # Claude home and Codex files that are personal and must survive byte for byte
    ".claude/projects/-tmp-x/memory/MEMORY.md": "# my memory\n",
    ".claude/projects/-tmp-x/session.jsonl": '{"type":"user"}\n',
    ".claude/history.jsonl": '{"display":"hi"}\n',
    ".claude/keybindings.json": '{"bindings":[]}\n',
    ".claude/.credentials.json": '{"token":"secret"}\n',
    ".claude/plugins/cache/x/plugin.json": "{}\n",
    ".claude/todos/t.json": "[]\n",
    ".claude.json": '{"oauthAccount":{"emailAddress":"person@example.invalid"},"mcpServers":{"a":{}}}\n',
    ".codex/auth.json": '{"tokens":"secret"}\n',
    ".codex/sessions/2026/s.jsonl": "{}\n",
}

SYSTEM_EXTRAS = {  # the person's own system items: removed by the upgrade, kept in the archive
    ".claude/skills/my-own-skill/SKILL.md": "---\nname: my-own-skill\n---\nmine\n",
    ".claude/hooks/my-hook.py": "print('mine')\n",
    ".claude/agents/my-agent.md": "---\nname: my-agent\n---\n",
    ".claude/commands/my-command.md": "do it\n",
    ".claude/output-styles/terse.md": "terse\n",
    ".codex/AGENTS.md": "# my global codex rules\n",
    ".codex/prompts/p.md": "prompt\n",
    ".agents/skills/my-agent-skill/SKILL.md": "mine\n",
}


def add_personal_extras(m):
    for rel, text in list(PERSONAL_FILES.items()) + list(SYSTEM_EXTRAS.items()):
        path = m.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    settings = json.loads((m.claude / "settings.json").read_text(encoding="utf-8"))
    settings["theme"] = "dark"
    settings["apiKeyHelper"] = "/opt/me/key-helper.sh"
    settings["myOwnSystemThing"] = {"x": 1}
    settings["env"] = {"MY_PRIVATE_DIR": "/opt/me"}
    settings.setdefault("permissions", {})["allow"] = ["Bash(ls:*)"]
    settings.setdefault("hooks", {}).setdefault("PreToolUse", []).append(
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/my-hook.py"}]})
    settings.setdefault("enabledPlugins", {})["my-plugin@my-market"] = True
    (m.claude / "settings.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


FOREIGN_NOTES = {
    "Projects/Alpha/Plan.md": "# Alpha plan\nSee [[Spec]] and ![[diagram.png]] and [[Plan#Goals|the goals]].\n",
    "Projects/Alpha/Spec.md": "# Spec\nBack to [[Projects/Alpha/Plan|plan]].\n```\n[[Plan]] stays as code\n```\n",
    "Projects/Alpha/img/diagram.png": b"\x89PNG\r\n\x1a\nfake",
    "1. Areas/Health.md": "# Health\nLinks: [[Plan]] and [md](../Notes/Plan.md) and `[[Spec]]` inline.\n",
    "Notes/Plan.md": "---\ntags: [note]\n---\n# Another plan\n[[Idea]] [[Notes/deep/nested/Idea#Part]]\n",
    "Notes/deep/nested/Idea.md": "# Idea\nUp: [[Notes/Plan]] and [[Health]] and ![[photo.jpg]]\n",
    "Notes/board.canvas": json.dumps({"nodes": [{"id": "1", "type": "file", "file": "Notes/Plan.md",
                                                "x": 0, "y": 0, "width": 10, "height": 10}], "edges": []}),
    "Archive/Old.md": "# Old\n[[Plan]]\n",
    "Inbox/todo.md": "- [ ] something\n",
    "people/Bob.md": "# Bob\n",
    "attachments/photo.jpg": b"\xff\xd8\xff fake jpeg",
    "README.md": "# My vault\n[[Health]]\n",
    "Journal/2026-10-01.md": "---\nzone: personal\n---\n# Private day\n![[mood.png]]\n",
    "Journal/mood.png": b"\x89PNG fake mood",
    "Done/Thing.md": "---\nstatus: archived\n---\n# Thing\n",
    "Secret/_index.md": "# Secret zone\n",
    "Secret/hot.md": "# hot\n",
    "Secret/project/Deal.md": "# Deal\n[[Plan]]\n",
    "wiki/workspace/projects/Kept.md": "# Kept in place\n[[Plan]]\n",
}


def add_foreign_vault(vault):
    for rel, content in FOREIGN_NOTES.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


def personal_vault_files(vault, system_paths=()):
    return migrate.scan(str(vault), set(system_paths))


def archives(m):
    return sorted(p for p in m.backups.iterdir() if (p / "journal.jsonl").is_file()) if m.backups.exists() else []
