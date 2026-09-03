---
name: evolution
description: "Прогон самосовершенствования vault в Codex: диагностика памяти, zone-aware wiki lint/fold, retrieval refresh и reflection по Codex-сессиям. Используй при запросах: прогони evolution, run evolution, гигиена vault, прибраться в vault, vault evolution, обнови индексы вики."
---

# Vault Evolution for Codex

Единый прогон гигиены и обучения vault. Codex запускает health-check по правилу `AGENTS.md`, а сам прогон выполняет вручную через `$evolution`. Деструктивные операции требуют явного одобрения владельца.

## Как исполнять

1. Прочитай `.vault-meta/evolution/EVOLUTION.md`. Он задает фазы и общие инварианты. Для Codex используй адаптации из этого файла, если формулировки плейбука относятся только к Claude.
2. Соблюдай инварианты: зоны work, personal и client изолированы; L0-L3 loadout строится только для активной зоны; `log.md` append-only; `hot.md` является перезаписываемым кешем; перед пакетными правками wiki используй lock; не отправляй клиентские данные наружу. Standing reflection delegation разрешает verified L1/L2 без повторного owner prompt, но не L3, skills, approval machinery или запрещённые policy-классы.
3. Выполни фазы по порядку: память, wiki hygiene, retrieval refresh, reflection.
4. Перед любым durable outcome из reflection примени `$evolution-verifier`. Сам verifier ничего не изменяет. Прошедший L1/L2 outcome материализуется автономно; ambiguous outcome остаётся provisional L2 и не поддерживает promotion. Reflection checkpoint содержит только exact provenance-bound outcome pages; approved L3 и любой parent-L3 path запрещены. Shared `index`, `log`, `hot`, `overview` и project `_index.md` обновляются отдельным обычным owner-scoped циклом после finalization.
5. Обновляй `.vault-meta/evolution/last-run.json` только через `.vault-meta/evolution/update-last-run.py`, чтобы JSON всегда оставался валидным.
6. Заверши кратким отчетом: что исправлено, что проверено и что было безопасно пропущено.

## Codex-адаптации

- Долговременная проектная память Codex в этом vault находится в обычных PARA-страницах wiki.
- `~/.codex/memories_1.sqlite` является внутренним генерируемым состоянием. Evolution проверяет его только на чтение и не редактирует.
- Reflection анализирует `~/.codex/sessions` в формате Codex. Claude-сессии и Claude-memory не изменяются.
- Gate для Codex: code-anchored standing policy, completed-turn receipt и `$evolution-verifier`. Повторное одобрение владельца для L1/L2 не требуется.

## Команды

- Codex memory audit: `python3 .vault-meta/evolution/memory-audit.py --platform codex`
- L0-L3 scan: `/usr/bin/python3 scripts/memory-model.py scan --zone ZONE`
- L0-L3 index under the shared writer lock: `/usr/bin/python3 scripts/memory-model.py build --zone ZONE --lock-token TOKEN`
- L0-L3 validation: under a writer lock use `/usr/bin/python3 scripts/memory-model.py validate --zone ZONE --lock-token TOKEN`; read-only checks after release omit the token
- L0-L3 query: `/usr/bin/python3 scripts/memory-model.py retrieve "QUERY" --zone ZONE --loadout ROLE`
- Context inspector: `/usr/bin/python3 scripts/memory-model.py inspect "QUERY" --zone ZONE --loadout ROLE`
- Memory candidates: `/usr/bin/python3 scripts/memory-candidates.py list --zone ZONE`; proposals require the shared zone owner token through catalog/log/hot update, build, validate and scoped checkpoint, followed by release and a post-release check
- Wiki lint: `python3 .vault-meta/evolution/lint-scan.py --zone ZONE`
- Codex lifecycle registration: at every turn start, under a brief shared work lock run `python3 .vault-meta/evolution/trace-session.py register --zone ACTIVE_ZONE --lock-token TOKEN`; a full route uses `trace-session.py switch --scope session`, a one-off route uses `--scope turn`. Interrupted registration is recovered, interrupted session switch rolls back before an unrelated command, and operational close errors remain retryable. Exact duplicates across policy generations are canonicalized before closure; conflicts fail before publication. A newly prepared receipt binds registration and closing authority. Once prepared, retry or rollover reuses its exact original bytes and completion generation. A session switch also promotes an effective one-off route into the durable ledger.
- Autonomous selection under the same work lock: `python3 .vault-meta/evolution/trace-manifest.py select --platform codex --zone work --publish-noop --lock-token TOKEN`; zero eligible publishes a durable content-free selector receipt
- Content-free verification: `python3 .vault-meta/evolution/trace-scan.py --platform codex --zone work --manifest MANIFEST --verify-only --json`
- Durable reflection scan under the same work lock: `python3 .vault-meta/evolution/trace-scan.py --platform codex --zone work --manifest MANIFEST --publish-receipt --lock-token TOKEN --json`
- Completed outcome receipt after checkpoint and live acceptance: `python3 .vault-meta/evolution/reflection-disposition.py publish --platform codex --manifest-sha256 MANIFEST_SHA --scan-receipt-sha256 SCAN_SHA --plan PLAN_JSON --acceptance-result RETRIEVAL_JSON --checkpoint COMMIT --lock-token TOKEN`. The verifier plan is schema 2 and binds policy, platform, manifest, scan, exact current-HEAD outcome-only path+hash diff, verifier skill hash and every signal. Every materialized page also carries exact `reflection_manifest_sha256`, `reflection_scan_receipt_sha256` and a JSON-string `reflection_signal_ids` provenance list. A signal-bearing scan is processed only after the strict immutable disposition finalization marker is durable; verify, run-state and health reject a body without it.
- Codex repeated procedures: add `--repeats` to the scan
- Wiki lock: use shared `.vault-meta/write/ZONE`; acquire returns `TOKEN`; renew and check after long phases and before later writes, build and checkpoint; release requires that exact token. Expired records remain fail-closed until explicit recovery.
- Retrieval refresh: `/usr/bin/python3 scripts/memory-model.py build --zone ZONE --lock-token TOKEN`
- Codex nudge: `EVOLUTION_PLATFORM=codex /usr/bin/python3 .vault-meta/evolution/health-check.py`
- State update after successful validate: `python3 .vault-meta/evolution/update-last-run.py --platform codex --lock-token TOKEN --phase curator_memory=DATE --phase wiki_hygiene=DATE --phase retrieval_refresh=DATE --phase reflection=DATE --retrieval-pages PAGES --retrieval-chunks CHUNKS --snapshot-sha256 SHA256 --loadouts-sha256 SHA256 --reflection-manifest-sha256 MANIFEST_SHA --reflection-receipt-sha256 SCAN_SHA --reflection-disposition-sha256 DISPOSITION_SHA --reflection-processed-sha256 PROCESSED_SHA --reflection-signal-count COUNT --notes "SUMMARY"`. For `reflection=ready-autonomous-no-eligible-traces`, omit disposition fields and pass `--reflection-noop-sha256 SELECTOR_RECEIPT_SHA`.

`ZONE` means the active session zone (`work`, `personal` or `client`). Never substitute `all`.

Claude использует тот же standing policy и отдельные official lifecycle hooks; его команды описаны в `.claude/skills/evolution/SKILL.md`.
