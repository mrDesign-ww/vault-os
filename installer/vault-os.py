#!/usr/bin/env python3
"""Vault OS clean install and full upgrade: detect, plan, upgrade, parity, rollback, recover.

  python3 installer/vault-os.py detect   [--json]
  python3 installer/vault-os.py upgrade  [answers] [--map SRC=DST ...]                 dry run: the plan and its id
  python3 installer/vault-os.py upgrade  [answers] --confirm PLAN_ID [--approve-memory-policy]
  python3 installer/vault-os.py parity   [--json]                                      0 differences expected
  python3 installer/vault-os.py rollback ARCHIVE [--json]                               preview: restores, stays, moves aside
  python3 installer/vault-os.py rollback ARCHIVE --confirm ROLLBACK_ID                 apply exactly that preview
  python3 installer/vault-os.py recover                                              roll back an interrupted upgrade

Answers: --owner --language --vault --title --client-label --python, --codex/--no-codex,
--upstream-skills/--no-upstream-skills, --bypass-permissions/--no-bypass-permissions. They default to the stamp
of the installed system, then to what the old system says. Design and boundary: installer/UPGRADE.md.
Exit codes: 0 done, 1 parity differences, 2 usage, 3 questions or approval needed, 4 locked or interrupted,
5 not on this platform, 6 fetch failed (nothing changed), 7 failed and rolled back, 8 engine error (the plan
contradicts itself; nothing changed).
"""
import argparse
import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vaultos import engine  # noqa: E402

REPO = os.path.dirname(HERE)


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("detect", "upgrade", "parity", "rollback", "recover"))
    ap.add_argument("archive", nargs="?", help="rollback: the archive folder")
    ap.add_argument("--owner")
    ap.add_argument("--language")
    ap.add_argument("--vault")
    ap.add_argument("--title")
    ap.add_argument("--client-label")
    ap.add_argument("--python")
    for name in ("codex", "upstream-skills", "bypass-permissions"):
        dest = name.replace("-", "_")
        ap.add_argument("--" + name, dest=dest, action="store_const", const=True, default=None)
        ap.add_argument("--no-" + name, dest=dest, action="store_const", const=False)
    ap.add_argument("--map", action="append", default=[], metavar="SRC=DST",
                    help="put the vault folder SRC at DST (vault-relative paths)")
    ap.add_argument("--confirm", metavar="ID", help="apply the plan (upgrade) or the rollback preview with this id")
    ap.add_argument("--approve-memory-policy", action="store_true",
                    help="the person approved the two bundled L3 doctrine pages again")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--home", help="use this folder as HOME (tests, unusual layouts)")
    ap.add_argument("--claude-home")
    ap.add_argument("--codex-home")
    ap.add_argument("--agents-home")
    ap.add_argument("--backup-root")
    ap.add_argument("--platform", choices=("darwin", "linux", "win32"), help=argparse.SUPPRESS)
    ap.add_argument("--upstream-manifest", help=argparse.SUPPRESS)
    return ap.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    if a.home:
        os.environ["HOME"] = os.path.abspath(a.home)
    maps = []
    for item in a.map:
        if "=" not in item:
            print("--map needs SRC=DST", file=sys.stderr)
            return 2
        src, dst = item.split("=", 1)
        maps.append((src.strip().strip("/"), dst.strip().strip("/")))
    opts = {"owner": a.owner, "language": a.language, "vault": a.vault, "title": a.title,
            "client_label": a.client_label, "python": a.python, "codex": a.codex,
            "upstream_skills": a.upstream_skills, "bypass_permissions": a.bypass_permissions,
            "claude_home": a.claude_home, "codex_home": a.codex_home, "agents_home": a.agents_home,
            "backup_root": a.backup_root, "platform": a.platform, "upstream_manifest": a.upstream_manifest,
            "maps": maps, "approve_memory_policy": a.approve_memory_policy}
    homes = engine.homes_from(opts)
    try:
        return run(a, opts, homes)
    except engine.Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code


def run(a, opts, homes):
    platform = engine.manifest.load_installers(REPO)[0].platform_name(a.platform)
    if platform == "win32" and (a.mode in ("rollback", "recover") or (a.mode == "upgrade" and a.confirm)):
        raise engine.Stop(engine.WINDOWS_REFUSAL, 5)
    if a.mode == "detect":
        out = engine.detect(REPO, homes)
        if a.json:
            print(json.dumps(out, indent=2, ensure_ascii=False))
        else:
            line = out["result"]
            if out.get("version"):
                line += " %s (this release %s%s)" % (out["version"], out["version_available"],
                                                     ", outdated" if out.get("outdated") else "")
            if out.get("incomplete_archives"):
                line += ": interrupted upgrade in %s, run recover" % out["incomplete_archives"][-1]
            print(line)
        return 0
    if a.mode == "rollback":
        if not a.archive:
            print("rollback needs the archive folder", file=sys.stderr)
            return 2
        preview = engine.rollback_archive(a.archive, homes, platform, confirm=a.confirm)
        if preview is not None:
            print(json.dumps({"rollback": preview}, indent=2, ensure_ascii=False) if a.json
                  else engine.rollback_summary(preview))
        return 0
    if a.mode == "recover":
        engine.recover(homes, platform)
        return 0
    stamp = engine.read_stamp(homes["claude"])
    if a.mode == "parity":
        if not stamp or stamp.get("state") != "installed":
            answers, _, questions = engine.resolve_answers(REPO, opts, homes, stamp)
            if questions:
                raise engine.Stop("no installed stamp; parity needs: " + ", ".join(questions), 3)
            plan, ctx = engine.build(REPO, answers, homes, stamp, opts)
            ctx.cleanup()
            problems = engine.differences(plan)
        else:
            problems = engine.verify(REPO, homes, opts)
        if a.json:
            print(json.dumps({"differences": len(problems), "items": problems}, indent=2, ensure_ascii=False))
        else:
            for p in problems:
                print(p)
            print("parity: %d differences" % len(problems))
        return 0 if not problems else 1
    # upgrade
    answers, sources, questions = engine.resolve_answers(REPO, opts, homes, stamp)
    if questions:
        raise engine.Stop("answers needed before a plan: " + "; ".join(questions), 3)
    state = engine.detect(REPO, homes)
    if state["result"] == "interrupted":
        raise engine.Stop("an earlier upgrade was interrupted: run recover first", 4)
    plan, ctx = engine.build(REPO, answers, homes, stamp, opts)
    try:
        if not a.confirm:
            if a.json:
                print(json.dumps({"plan": plan, "answer_sources": sources, "detect": state["result"]}, indent=2,
                                 ensure_ascii=False))
            else:
                print("machine: %s" % state["result"])
                print("answers: " + ", ".join("%s=%s (%s)" % (k, plan["answers"][k], sources.get(k, "derived"))
                                              for k in sorted(plan["answers"])))
                print(engine.summary(plan))
                m = plan["migration"]
                if not plan["questions"] and (plan["actions"] or m["moves"] or m["rewrites"] or plan["stamp"]):
                    print("\nTo apply exactly this plan: python3 installer/vault-os.py upgrade ... --confirm %s%s"
                          % (plan["id"], " --approve-memory-policy" if plan["approval_needed"] else ""))
            return 3 if plan["questions"] else 0
        if a.confirm != plan["id"]:
            raise engine.Stop("the machine changed since the dry run (plan %s, confirmed %s)%s: show the new plan and "
                              "ask again" % (plan["id"], a.confirm,
                                             ", or the date changed (a plan that needs memory "
                                             "approval carries today's date)" if plan["approval_needed"] else ""), 3)
        archive, warnings = engine.upgrade(REPO, answers, homes, stamp, opts, plan, ctx)
        if archive:
            print("archive: " + archive)
        return 0
    finally:
        ctx.cleanup()


if __name__ == "__main__":
    sys.exit(main())
