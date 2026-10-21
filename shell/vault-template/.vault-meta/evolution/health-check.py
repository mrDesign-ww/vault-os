#!/usr/bin/env python3
"""health-check.py — SessionStart-нудж для Vault Evolution.

Запускается хуком при старте сессии (cwd = vault root), цель меньше 3 с. Печатает
нудж только когда нужно действие: испорчено или не завершено состояние last-run,
сломана Claude memory, индекс памяти не проходит validate. Иначе молчит.
Давность evolution и длина log.md не нудж (это уборка, не поломка). Оценка поиска
(`memory-model.py evaluate`) идёт в прогоне evolution, не здесь. Reflection
отключена по умолчанию, её состояние здесь не проверяется.
"""
import datetime
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.abspath(os.path.join(HERE, "..", ".."))
LAST_RUN = os.path.join(HERE, "last-run.json")
AUDIT = os.path.join(HERE, "memory-audit.py")
MEMORY_MODEL = os.path.join(VAULT, "scripts", "memory-model.py")
REFLECTION_POLICY = os.path.join(HERE, "reflection_policy.py")
REFLECTION_DISPOSITION = os.path.join(HERE, "reflection-disposition.py")
PLATFORM = os.environ.get("EVOLUTION_PLATFORM", "claude").strip().lower()
if PLATFORM not in ("claude", "codex"):
    PLATFORM = "claude"

REQUIRED_PHASES = ("curator_memory", "wiki_hygiene", "retrieval_refresh")
GATED_PHASES = ("adversarial_gate",)
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
PLACEHOLDER_RE = re.compile(r"\b(?:undefined|null)\b", re.I)
# A writer (save, build, evolution) holds the shared work lock, and a public
# validate refuses while it is held or its generation moves.
LOCK_RACE_MARKERS = ("zone write in progress", "zone changed during")
REFLECTION_NO_EVIDENCE = {
    "ready-autonomous-awaiting-completion",
    "blocked-delegation-invalid",
}


def phase_complete(value, run_date=None):
    if value == "complete":
        return run_date is None
    text = str(value)
    try:
        parsed = datetime.date.fromisoformat(text)
        return (
            parsed.isoformat() == text
            and parsed <= datetime.date.today()
            and (run_date is None or text == run_date)
        )
    except ValueError:
        return False


def incomplete_required_phases(phases, run_date=None):
    names = list(REQUIRED_PHASES) + [name for name in GATED_PHASES if name in phases]
    return [
        name for name in names
        if not phase_complete(phases.get(name), run_date)
    ]


def days_since_last():
    try:
        with open(LAST_RUN, encoding="utf-8") as f:
            data = json.load(f)
        platform_state = data.get("platforms", {}).get(PLATFORM)
        state = platform_state if isinstance(platform_state, dict) else data
        last = state.get("last_run")
        phases = state.get("phases", {})
        evidence = state.get("retrieval_evidence")
        reflection_evidence = state.get("reflection_evidence")
        reflection_noop_evidence = state.get("reflection_noop_evidence")
        notes = state.get("notes")
        schema = state.get("state_schema_version", 1)
        if not last:
            return (
                None, None, phases, evidence, reflection_evidence,
                reflection_noop_evidence, notes, schema,
            )
        d = datetime.date.fromisoformat(last)
        return (
            (datetime.date.today() - d).days, last, phases, evidence,
            reflection_evidence, reflection_noop_evidence, notes, schema,
        )
    except Exception:
        return None, None, None, None, None, None, None, None


def retrieval_evidence_issue(evidence, completed, schema):
    if type(schema) is not int or schema not in (1, 2, 3, 4):
        return "run-state schema is unsupported"
    if schema not in (2, 3, 4):
        return None
    if not completed:
        return "retrieval evidence exists while retrieval_refresh is pending" if evidence else None
    if not isinstance(evidence, dict):
        return "completed retrieval_refresh has no typed recovery evidence"
    pages, chunks = evidence.get("pages"), evidence.get("chunks")
    digest = evidence.get("snapshot_sha256")
    loadouts_digest = evidence.get("loadouts_sha256")
    if (
        isinstance(pages, bool) or not isinstance(pages, int) or pages < 0
        or isinstance(chunks, bool) or not isinstance(chunks, int) or chunks < 0
        or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest)
        or not isinstance(loadouts_digest, str) or not SHA256_RE.fullmatch(loadouts_digest)
    ):
        return "typed recovery evidence is invalid"
    return None


# Reflection is disabled by default: the two checks below are kept for a
# possible re-enable but main() no longer calls them.
def reflection_evidence_issue(evidence, noop_evidence, phase, run_date, schema):
    completed = phase_complete(phase, run_date)
    if phase == "ready-autonomous-no-eligible-traces":
        if evidence:
            return "no-eligible reflection has disposition evidence"
        if schema not in (3, 4) or not isinstance(noop_evidence, dict) or set(noop_evidence) != {
            "status", "selection_receipt_sha256",
        }:
            return "no-eligible reflection has no exact selector evidence"
        if (
            noop_evidence.get("status") != "no-eligible-traces"
            or not SHA256_RE.fullmatch(
                str(noop_evidence.get("selection_receipt_sha256", ""))
            )
        ):
            return "typed no-eligible selector evidence is invalid"
        return None
    if not completed:
        if phase is not None and phase not in REFLECTION_NO_EVIDENCE:
            return "reflection phase has an unsupported state"
        if evidence or noop_evidence:
            return "reflection evidence exists without a completed reflection"
        if phase == "blocked-delegation-invalid":
            return "autonomous reflection delegation is invalid"
        return None
    if schema not in (2, 3, 4):
        return "completed reflection requires a typed state schema"
    if noop_evidence:
        return "completed reflection has no-op selector evidence"
    if not isinstance(evidence, dict) or set(evidence) != {
        "manifest_sha256", "scan_receipt_sha256", "disposition_sha256",
        "processed_marker_sha256", "signal_count", "approval_scope",
    }:
        return "completed reflection has no exact typed evidence"
    if (
        any(
            not isinstance(evidence.get(key), str)
            or not SHA256_RE.fullmatch(evidence[key])
            for key in (
                "manifest_sha256", "scan_receipt_sha256", "disposition_sha256",
                "processed_marker_sha256",
            )
        )
        or isinstance(evidence.get("signal_count"), bool)
        or not isinstance(evidence.get("signal_count"), int)
        or evidence["signal_count"] < 0
        or evidence.get("approval_scope") != "standing-autonomous-l0-l2"
    ):
        return "typed reflection evidence is invalid"
    return None


def reflection_delegation_issue(evidence, noop_evidence, phase, run_date):
    try:
        policy_spec = importlib.util.spec_from_file_location(
            "reflection_policy_health", REFLECTION_POLICY,
        )
        policy_module = importlib.util.module_from_spec(policy_spec)
        policy_spec.loader.exec_module(policy_module)
        policy, policy_sha = policy_module.load_policy()
        disposition_spec = importlib.util.spec_from_file_location(
            "reflection_disposition_health", REFLECTION_DISPOSITION,
        )
        disposition_module = importlib.util.module_from_spec(disposition_spec)
        disposition_spec.loader.exec_module(disposition_module)
        generation_paths = {
            key: os.path.join(
                VAULT, policy["roots"][key], policy_sha, PLATFORM,
            )
            for key in (
                "completion_work", "manifest_work", "scan_receipt_work",
                "selection_receipt_work", "disposition_work",
            )
        }
        for path in generation_paths.values():
            if not os.path.exists(path):
                continue
            metadata = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or os.path.realpath(path) != os.path.abspath(path)
            ):
                raise ValueError("reflection catalog generation is unsafe")
        if os.path.exists(generation_paths["scan_receipt_work"]):
            # A fresh policy generation legitimately holds scan receipts before its own
            # completion catalog exists (it drains pinned predecessors first).
            if not os.path.exists(generation_paths["manifest_work"]):
                raise ValueError("reflection scan catalog is incomplete")
            disposition_module.SELECTOR.read_processed_manifests(
                generation_paths["scan_receipt_work"],
                generation_paths["manifest_work"],
                generation_paths["completion_work"],
                policy, policy_sha, PLATFORM,
            )
            disposition_module.SELECTOR.validate_processed_coverage(
                generation_paths["scan_receipt_work"],
                generation_paths["manifest_work"],
                generation_paths["completion_work"],
                policy, policy_sha, PLATFORM,
            )
        if phase_complete(phase, run_date):
            value = disposition_module.load_published(
                evidence["disposition_sha256"], PLATFORM, policy, policy_sha,
            )
            if any(
                value[field] != evidence[field]
                for field in (
                    "manifest_sha256", "scan_receipt_sha256", "signal_count",
                )
            ):
                raise ValueError("run-state differs from disposition receipt")
            manifest, _ = disposition_module.published_artifacts(
                policy, policy_sha, PLATFORM,
                value["manifest_sha256"], value["scan_receipt_sha256"],
            )
            processed = disposition_module.SELECTOR.PROCESSED.read_processed(
                policy, policy_sha, PLATFORM,
            )
            marker_sha = disposition_module.SELECTOR.PROCESSED.marker_for_receipts(
                processed,
                [
                    item["receipt_sha256"]
                    for item in manifest["completion_receipts"]
                ],
            )
            if marker_sha != evidence["processed_marker_sha256"]:
                raise ValueError("run-state differs from processed marker")
        elif phase == "ready-autonomous-no-eligible-traces":
            selector = disposition_module.SELECTOR
            selector.load_noop_receipt(
                generation_paths["selection_receipt_work"],
                noop_evidence["selection_receipt_sha256"],
                policy, policy_sha, PLATFORM,
                generation_paths["completion_work"],
                generation_paths["scan_receipt_work"],
                generation_paths["manifest_work"], replay=True,
            )
            manifest, _ = selector.compute_selection(
                policy, policy_sha,
                datetime.datetime.now(datetime.timezone.utc),
                generation_paths["completion_work"],
                generation_paths["scan_receipt_work"],
                generation_paths["manifest_work"], PLATFORM,
            )
            if manifest is not None:
                raise ValueError("new eligible reflection traces are pending")
        return None
    except Exception as error:
        return "autonomous reflection delegation/catalog is invalid: %s" % str(error)[:120]


def memory_health():
    try:
        result = subprocess.run(
            [sys.executable, AUDIT, "--platform", PLATFORM, "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip() or "audit failed")
        data = json.loads(result.stdout)
        return (
            len(data.get("orphans", [])),
            len(data.get("dead_links", [])),
            int(data.get("issue_count", 0)),
        )
    except Exception:
        return None, None, None


def run_memory_model(arguments, timeout, run=subprocess.run, pause=time.sleep):
    """Run memory-model.py, once more after a short pause if a lifecycle hook raced it."""
    for attempt in (1, 2):
        result = run(
            [sys.executable, MEMORY_MODEL] + arguments,
            capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stderr or "") + (result.stdout or "")
        if result.returncode == 0 or attempt == 2 or not any(
            marker in output for marker in LOCK_RACE_MARKERS
        ):
            return result
        pause(2)


def retrieval_health():
    """One validate: the index matches Markdown. A rebuild since the last evolution is fine."""
    try:
        result = run_memory_model(["validate", "--zone", "work"], 30)
        if result.returncode == 0:
            if json.loads(result.stdout).get("valid") is not True:
                return "memory index did not return a valid receipt"
            return None
        detail = (result.stderr or result.stdout).strip().splitlines()
        return detail[-1][:180] if detail else "index validation failed"
    except Exception as error:
        return "index validation unavailable: %s" % str(error)[:120]


def main():
    reasons = []

    days, last, phases, evidence, _, _, notes, state_schema = days_since_last()
    if days is None:
        reasons.append("гигиена ещё не запускалась")
    elif days < 0:
        reasons.append("дата последней гигиены находится в будущем: %s" % last)
    if phases is not None and not isinstance(phases, dict):
        reasons.append("состояние обязательных фаз повреждено")
    elif isinstance(phases, dict):
        incomplete = incomplete_required_phases(phases, last)
        if incomplete:
            reasons.append("незавершённые обязательные фазы: %s" % ", ".join(incomplete))
    completed_refresh = (
        isinstance(phases, dict)
        and phase_complete(phases.get("retrieval_refresh"), last)
    )
    evidence_issue = retrieval_evidence_issue(evidence, completed_refresh, state_schema)
    if evidence_issue:
        reasons.append(evidence_issue)
    if isinstance(notes, str) and PLACEHOLDER_RE.search(notes):
        reasons.append("run-state notes содержат unresolved placeholder")

    orphans, dead, memory_issues = memory_health()
    if orphans is None:
        reasons.append("%s memory audit недоступен" % PLATFORM)
    elif PLATFORM == "claude":
        if orphans:
            reasons.append("memory orphans: %d" % orphans)
        if dead:
            reasons.append("memory dead-links: %d" % dead)
        frontmatter_issues = max(0, memory_issues - orphans - dead)
        if frontmatter_issues:
            reasons.append("memory frontmatter issues: %d" % frontmatter_issues)
    elif memory_issues:
        reasons.append("Codex memory issues: %d" % memory_issues)

    retrieval_issue = retrieval_health()
    if retrieval_issue:
        reasons.append("индекс памяти не проходит validate: %s" % retrieval_issue)

    if not reasons:
        return 0  # тихо

    print("🧹 VAULT EVOLUTION: нужно действие")
    for r in reasons:
        print("  - %s" % r)
    print(
        "Это сигнал, не задание: скажи владельцу одной строкой и продолжай его задачу. "
        "Починка по его слову: «прогони evolution»."
    )
    return 0


if __name__ == "__main__":
    try:
        if "--self-test" in sys.argv:
            assert phase_complete("complete")
            assert not phase_complete("complete", "2026-08-20")
            assert phase_complete("2026-08-20")
            assert not phase_complete("2099-01-01")
            assert not phase_complete("20260820")
            assert not phase_complete("2026-08-19", "2026-08-20")
            assert not phase_complete("pending-final-adversarial-gate")
            assert incomplete_required_phases({
                "curator_memory": "2026-08-20", "wiki_hygiene": "2026-08-20",
                "retrieval_refresh": "pending",
                "adversarial_gate": "pending",
            }, "2026-08-20") == ["retrieval_refresh", "adversarial_gate"]
            good_evidence = {
                "pages": 254, "chunks": 7833, "snapshot_sha256": "a" * 64,
                "loadouts_sha256": "b" * 64,
            }
            assert retrieval_evidence_issue(good_evidence, True, 2) is None
            assert retrieval_evidence_issue(None, True, 2)
            assert retrieval_evidence_issue(good_evidence, False, 2)
            assert retrieval_evidence_issue(None, True, 1) is None
            assert retrieval_evidence_issue(None, True, 3)
            assert retrieval_evidence_issue(good_evidence, True, True)
            assert retrieval_evidence_issue(good_evidence, True, 2.0)
            good_reflection = {
                "manifest_sha256": "c" * 64,
                "scan_receipt_sha256": "d" * 64,
                "disposition_sha256": "e" * 64,
                "processed_marker_sha256": "1" * 64,
                "signal_count": 2,
                "approval_scope": "standing-autonomous-l0-l2",
            }
            assert reflection_evidence_issue(
                good_reflection, None, "2026-08-20", "2026-08-20", 2,
            ) is None
            good_noop = {
                "status": "no-eligible-traces",
                "selection_receipt_sha256": "f" * 64,
            }
            assert reflection_evidence_issue(
                None, good_noop, "ready-autonomous-no-eligible-traces",
                "2026-08-20", 3,
            ) is None
            assert reflection_evidence_issue(
                None, None, "ready-autonomous-awaiting-completion", "2026-08-20", 3,
            ) is None
            assert reflection_evidence_issue(
                None, None, "blocked-delegation-invalid", "2026-08-20", 3,
            ) == "autonomous reflection delegation is invalid"
            assert reflection_evidence_issue(
                None, None, "pending-owner-review", "2026-08-20", 3,
            ) == "reflection phase has an unsupported state"
            calls = []

            def scripted(*outcomes):
                queue = list(outcomes)

                def run(arguments, **_):
                    calls.append(arguments)
                    code, error = queue.pop(0)
                    return type("Result", (), {"returncode": code, "stderr": error, "stdout": ""})()
                return run

            race = "ERR: zone write in progress; retry retrieval after the writer releases its lock"
            moved = "ERR: zone changed during validation; retry"
            for outcomes, code, attempts in (
                (((1, race), (0, "")), 0, 2),
                (((1, moved), (1, moved)), 1, 2),
                (((1, "ERR: evaluations failed: x hash=drift"),), 1, 1),
                (((0, ""),), 0, 1),
            ):
                calls.clear()
                result = run_memory_model(
                    ["evaluate"], 1, run=scripted(*outcomes), pause=lambda seconds: None,
                )
                assert result.returncode == code and len(calls) == attempts, outcomes
            import tempfile
            # SessionStart: one validate, no evaluate, silent unless action is needed.
            # Old run date, leftover reflection state and an index rebuilt since the
            # last evolution are not actions.
            import contextlib
            import io
            with tempfile.TemporaryDirectory() as scratch:
                state = os.path.join(scratch, "last-run.json")
                phases = dict.fromkeys(REQUIRED_PHASES, "2026-01-01")
                phases["reflection"] = "ready-autonomous-no-eligible-traces"
                with open(state, "w") as handle:
                    json.dump({"platforms": {PLATFORM: {
                        "last_run": "2026-01-01", "state_schema_version": 4,
                        "phases": phases, "retrieval_evidence": good_evidence,
                    }}}, handle)
                saved = {
                    name: globals()[name]
                    for name in ("LAST_RUN", "memory_health", "run_memory_model")
                }
                globals().update(LAST_RUN=state, memory_health=lambda: (0, 0, 0))
                try:
                    for code, expect_nudge in ((0, False), (1, True)):
                        rebuilt = json.dumps({"valid": True, **good_evidence, "chunks": 1})
                        globals()["run_memory_model"] = lambda arguments, *_a, **_k: (
                            calls.append(arguments) or type("Result", (), {
                                "returncode": code, "stderr": "ERR: stale", "stdout": rebuilt,
                            })()
                        )
                        calls.clear()
                        out = io.StringIO()
                        with contextlib.redirect_stdout(out):
                            main()
                        assert bool(out.getvalue()) == expect_nudge, out.getvalue()
                        assert [arguments[0] for arguments in calls] == ["validate"], calls
                finally:
                    globals().update(saved)
            print("health-check self-test: PASS")
            sys.exit(0)
        sys.exit(main())
    except Exception as error:
        print("🧹 VAULT EVOLUTION — health-check недоступен")
        print("  - %s" % str(error)[:180])
        sys.exit(0)
