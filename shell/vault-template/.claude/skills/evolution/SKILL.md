---
name: evolution
description: "Прогон самосовершенствования общего vault: Claude memory, zone-aware wiki lint/fold, retrieval refresh и reflection по Claude-сессиям. Триггеры: прогони evolution, run evolution, гигиена vault, прибраться в vault, vault evolution, обнови индексы вики."
---

# Vault Evolution for Claude

Единый прогон гигиены и обучения общего Claude + Codex vault. Запускается по SessionStart-нуджу или вручную через `/evolution`. Деструктивные операции требуют явного одобрения владельца.

## Как исполнять

1. Прочитай `.vault-meta/evolution/EVOLUTION.md` и следуй его фазам и инвариантам.
2. Соблюдай zone isolation, L0-L3 loadout только для активной зоны, append-only `log.md`, wiki-lock перед пакетными правками и запрет на внешний egress. Standing reflection delegation разрешает verified L1/L2 без повторного owner prompt, но не L3, skills, approval machinery или запрещённые policy-классы.
3. Claude `UserPromptSubmit` и официальный `Stop` hook автоматически создают work-turn lifecycle receipts. Policy-independent session ledger сохраняет зону и prompt sequence между turns и policy generations. State и ledger меняются через recoverable write-ahead transaction: interrupted registration завершается, а interrupted switch откатывается перед несвязанным prompt. Перед новым prompt или Stop exact duplicate states across generations сводятся к одному; conflicting variants или prepared receipts отклоняются до публикации. Новый receipt хранит registration и closing authority. После durable prepare retry или policy rollover публикует exact original bytes в исходной completion generation. Session switch, совпавший с effective one-off route, повышает его до session scope и обновляет ledger. Zone switch находит единственный открытый turn во всех policy generations до изменения общего ledger, поэтому mid-turn policy update не обходит zone exclusion. Prompt hook связывает exact prompt hash, записывает точные команды смены зоны и one-off префиксы `личное:` / `client:`. Такой turn исключается из work reflection; при невозможности durable записи hook сохраняет nonzero, а operational I/O/publication error остаётся retryable и никогда не превращается в retirement. Tool-result user envelopes являются продолжением одного хода: они входят в exact segment hash, но не считаются новым prompt. Пропущенный Stop восстанавливается на следующей prompt boundary; только deterministic mixed segment получает content-free retire.
4. Одобренные межагентные факты, решения и уроки сохраняй в обычные PARA-страницы, индекс, log и hot. Не копируй внутренние Claude-memory или трейсы в Codex.
5. Перед durable outcome reflection используй `evolution-verifier`. Прошедший L1/L2 outcome материализуется автономно. Ambiguous outcome остаётся provisional L2 и не поддерживает promotion. Reflection checkpoint содержит только exact provenance-bound outcome pages; approved L3 и любой parent-L3 path запрещены. Shared `index`, `log`, `hot`, `overview` и project `_index.md` обновляются отдельным обычным owner-scoped циклом после finalization.
6. Обновляй `.vault-meta/evolution/last-run.json` только через `.vault-meta/evolution/update-last-run.py`.

## Команды

- Claude memory audit: `python3 .vault-meta/evolution/memory-audit.py`
- L0-L3 scan: `/usr/bin/python3 scripts/memory-model.py scan --zone ZONE`
- L0-L3 index under the shared writer lock: `/usr/bin/python3 scripts/memory-model.py build --zone ZONE --lock-token TOKEN`
- L0-L3 validation: under a writer lock use `/usr/bin/python3 scripts/memory-model.py validate --zone ZONE --lock-token TOKEN`; read-only checks after release omit the token
- L0-L3 query: `/usr/bin/python3 scripts/memory-model.py retrieve "QUERY" --zone ZONE --loadout ROLE`
- Context inspector: `/usr/bin/python3 scripts/memory-model.py inspect "QUERY" --zone ZONE --loadout ROLE`
- Memory candidates: `/usr/bin/python3 scripts/memory-candidates.py list --zone ZONE`; proposals require the shared zone owner token through catalog/log/hot update, build, validate and scoped checkpoint, followed by release and a post-release check
- Wiki lint: `python3 .vault-meta/evolution/lint-scan.py --zone ZONE`
- Claude hooks: `.claude/settings.json` registers `UserPromptSubmit` and `Stop`; a lifecycle failure is nonzero and visible, so the turn is retried instead of being silently lost
- Autonomous selection under the shared work lock: `python3 .vault-meta/evolution/trace-manifest.py select --platform claude --zone work --publish-noop --lock-token TOKEN`; zero eligible publishes a durable content-free selector receipt
- Content-free verification: `python3 .vault-meta/evolution/trace-scan.py --platform claude --zone work --manifest MANIFEST --verify-only --json`
- Durable reflection scan under the shared work lock: `python3 .vault-meta/evolution/trace-scan.py --platform claude --zone work --manifest MANIFEST --publish-receipt --lock-token TOKEN --json`
- Completed outcome receipt after checkpoint and live acceptance: `python3 .vault-meta/evolution/reflection-disposition.py publish --platform claude --manifest-sha256 MANIFEST_SHA --scan-receipt-sha256 SCAN_SHA --plan PLAN_JSON --acceptance-result RETRIEVAL_JSON --checkpoint COMMIT --lock-token TOKEN`. The verifier plan is schema 2 and binds policy, platform, manifest, scan, exact current-HEAD outcome-only path+hash diff, verifier skill hash and every signal. Every materialized page also carries exact `reflection_manifest_sha256`, `reflection_scan_receipt_sha256` and a JSON-string `reflection_signal_ids` provenance list. A signal-bearing scan is processed only after the strict immutable disposition finalization marker is durable; verify, run-state and health reject a body without it.
- Claude repeated procedures: add `--repeats` to the scan
- Wiki lock: use shared `.vault-meta/write/ZONE`; acquire returns `TOKEN`; renew and check after long phases and before later writes, build and checkpoint; release requires that exact token. Expired records remain fail-closed until explicit recovery.
- Retrieval refresh: `/usr/bin/python3 scripts/memory-model.py build --zone ZONE --lock-token TOKEN`
- Claude nudge: `python3 .vault-meta/evolution/health-check.py`
- State update after successful validate: `python3 .vault-meta/evolution/update-last-run.py --platform claude --lock-token TOKEN --phase curator_memory=DATE --phase wiki_hygiene=DATE --phase retrieval_refresh=DATE --phase reflection=DATE --retrieval-pages PAGES --retrieval-chunks CHUNKS --snapshot-sha256 SHA256 --loadouts-sha256 SHA256 --reflection-manifest-sha256 MANIFEST_SHA --reflection-receipt-sha256 SCAN_SHA --reflection-disposition-sha256 DISPOSITION_SHA --reflection-processed-sha256 PROCESSED_SHA --reflection-signal-count COUNT --notes "SUMMARY"`. For `reflection=ready-autonomous-no-eligible-traces`, omit disposition fields and pass `--reflection-noop-sha256 SELECTOR_RECEIPT_SHA`.

`ZONE` means the active session zone (`work`, `personal` or `client`). Never substitute `all`.

Codex использует тот же playbook и state, но запускает platform-specific команды из `.agents/skills/evolution/SKILL.md`.
