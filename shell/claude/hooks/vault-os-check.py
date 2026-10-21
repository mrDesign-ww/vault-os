#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SessionStart hook: tell a main session when Vault OS is outdated, unstamped or half-upgraded.

Reads the stamp <claude home>/vault-os/installed.json. Asks GitHub for the latest release at most once a day (cached
in vault-os/check-cache.json, about 2 seconds in total, a plain GET and nothing else). Prints at most one line of
context. A stamp that says `upgrading` while the upgrade lock's process is alive is a running upgrade: nothing is said.
Subagents skip it. Offline or any error: prints nothing. VAULT_OS_CHECK=off turns it off; VAULT_OS_CHECK_URL points it
at another release URL (JSON with tag_name, or plain text with the version).
Selftest: vault-os-check.py --selftest
"""
import json, os, shutil, sys, tempfile, threading, time
from urllib.request import Request, urlopen

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "mrDesign-ww/vault-os"
URLS = ("https://api.github.com/repos/%s/releases/latest" % REPO,
        "https://raw.githubusercontent.com/%s/main/VERSION" % REPO)
DAY, RETRY = 86400, 6 * 3600
BUDGET = 2.0  # seconds for every release URL together
LOCK_NAME = ".vault-os.lock"
UPDATE = "run the vault-os-update skill"


def is_subagent(data):
    return isinstance(data, dict) and bool(
        data.get("agent_id") or data.get("agent_type") or "/subagents/" in str(data.get("transcript_path", "")))


def version_key(value):
    return tuple(int(p) if p.isdigit() else 0 for p in str(value or "0").strip().lstrip("v").split("."))


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def fetch_latest(urls, budget=BUDGET):
    """The latest version from the first URL that answers; None when nothing answers within the budget in total."""
    found = []

    def worker():
        deadline = time.time() + budget
        for url in urls:
            left = deadline - time.time()
            if left <= 0.05:
                return
            try:
                req = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "vault-os-check"})
                with urlopen(req, timeout=left) as resp:
                    body = resp.read(65536).decode("utf-8", "replace").strip()
            except Exception:
                continue
            try:
                tag = json.loads(body).get("tag_name")
            except (ValueError, AttributeError):
                tag = body.splitlines()[0] if body else None
            if tag and version_key(tag) > (0,):
                found.append(tag.lstrip("v"))
                return

    thread = threading.Thread(target=worker, daemon=True)  # also bounds a slow name lookup
    thread.start()
    thread.join(budget)
    return found[0] if found else None


def pid_alive(pid):
    """True while the process exists. A missing or bad pid counts as dead. Never sends a real signal."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def upgrade_running(home, stamp):
    """The upgrade lock next to the stamp's archive (else in ~/vault-os-backups) is held by a live process."""
    archive = stamp.get("archive")
    root = os.path.dirname(str(archive).rstrip("/\\")) if archive else os.path.join(os.path.dirname(home),
                                                                                      "vault-os-backups")
    lock = read_json(os.path.join(root, LOCK_NAME))
    return bool(lock) and pid_alive(lock.get("pid"))


def latest_version(home, now):
    cache_path = os.path.join(home, "vault-os", "check-cache.json")
    cache = read_json(cache_path) or {}
    age = now - float(cache.get("checked_at", 0) or 0)
    if "checked_at" in cache and 0 <= age < (DAY if cache.get("latest") else RETRY):
        return cache.get("latest")
    custom = os.environ.get("VAULT_OS_CHECK_URL")
    latest = fetch_latest((custom,) if custom else URLS)
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"checked_at": now, "latest": latest}, f)
    except OSError:
        pass
    return latest


def message(home, data, now=None):
    if is_subagent(data) or os.environ.get("VAULT_OS_CHECK", "").lower() in ("off", "0", "false", "no"):
        return ""
    stamp_path = os.path.join(home, "vault-os", "installed.json")
    if not os.path.exists(stamp_path):
        return ("Vault OS: this Claude setup has no install stamp, so it may differ from the published system. "
                "If the person wants the current Vault OS, %s (it plans first and changes nothing without a yes)."
                % UPDATE)
    stamp = read_json(stamp_path) or {}
    if stamp.get("state") == "upgrading":
        if upgrade_running(home, stamp):
            return ""
        return "Vault OS: an upgrade was interrupted and is not finished; %s to recover it before other work." % UPDATE
    if stamp.get("state") != "installed":
        return ""
    latest = latest_version(home, time.time() if now is None else now)
    if latest and version_key(latest) > version_key(stamp.get("version")):
        return "Vault OS %s is installed and %s is out; tell the person and offer to %s." % (
            stamp.get("version"), latest, UPDATE)
    return ""


def output(home, data, now=None):
    text = message(home, data, now)
    if not text:
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}},
                      ensure_ascii=False)


def selftest():
    home = tempfile.mkdtemp(prefix="vault-os-check-")
    os.environ.pop("VAULT_OS_CHECK", None)
    release = os.path.join(home, "release.json")
    os.environ["VAULT_OS_CHECK_URL"] = "file://" + release
    assert "no install stamp" in output(home, {})
    assert output(home, {"agent_id": "a1"}) == ""
    os.makedirs(os.path.join(home, "vault-os"))
    stamp = os.path.join(home, "vault-os", "installed.json")
    with open(stamp, "w") as f:
        json.dump({"state": "upgrading", "version": "2026.10.20"}, f)
    assert "interrupted" in output(home, {})
    with open(stamp, "w") as f:
        json.dump({"state": "installed", "version": "2026.10.20"}, f)
    cache = os.path.join(home, "vault-os", "check-cache.json")
    assert output(home, {}, now=1000.0) == ""  # offline (no release file yet): silent
    assert read_json(cache)["latest"] is None
    with open(release, "w") as f:
        json.dump({"tag_name": "v2026.11.02"}, f)
    assert output(home, {}, now=1000.0 + 60) == ""  # failure cached: no retry within RETRY
    out = output(home, {}, now=1000.0 + RETRY + 1)
    assert "2026.11.02 is out" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    with open(release, "w") as f:
        json.dump({"tag_name": "v2026.10.20"}, f)
    assert "is out" in output(home, {}, now=1000.0 + RETRY + 2)  # cached for a day
    assert output(home, {}, now=1000.0 + RETRY + DAY + 3) == ""  # current
    os.environ["VAULT_OS_CHECK"] = "off"
    os.remove(stamp)
    assert output(home, {}) == ""
    shutil.rmtree(home, ignore_errors=True)
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        sys.exit(0)
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    try:
        out = output(HOME, payload)
        if out:
            print(out)
    except Exception:
        pass
