---
name: evolution
description: "Прогон самосовершенствования vault в Codex: диагностика памяти, zone-aware wiki lint/fold, retrieval refresh и reflection по Codex-сессиям. Используй при запросах: прогони evolution, run evolution, гигиена vault, прибраться в vault, vault evolution, обнови индексы вики."
---

# Vault Evolution for Codex

Единый прогон гигиены и обучения vault. Запускается по SessionStart-нуджу или вручную через `$evolution`. Деструктивные операции требуют явного одобрения владельца.

## Как исполнять

1. Прочитай `.vault-meta/evolution/EVOLUTION.md`. Он задает фазы и общие инварианты. Для Codex используй адаптации из этого файла, если формулировки плейбука относятся только к Claude.
2. Соблюдай инварианты: зоны из `mode.json` изолированы; `log.md` append-only; `hot.md` является перезаписываемым кешем; перед пакетными правками wiki используй lock; не меняй инструкции, память или skills без предложения и одобрения; не отправляй клиентские данные наружу; retrieval запускай с `--no-llm`.
3. Выполни фазы по порядку: память, wiki hygiene, retrieval refresh, reflection.
4. Перед любыми предложениями из reflection примени `$evolution-verifier`. Сам verifier ничего не изменяет.
5. Обновляй `.vault-meta/evolution/last-run.json` только через `.vault-meta/evolution/update-last-run.py`, чтобы JSON всегда оставался валидным.
6. Заверши кратким отчетом: что исправлено, что проверено, что ожидает одобрения.

## Codex-адаптации

- Долговременная проектная память Codex в этом vault находится в обычных PARA-страницах wiki.
- `~/.codex/memories_1.sqlite` является внутренним генерируемым состоянием. Evolution проверяет его только на чтение и не редактирует.
- Reflection анализирует `~/.codex/sessions` в формате Codex. Claude-сессии и Claude-memory не изменяются.
- Gate для Codex: `$evolution-verifier` и затем одобрение владельца.

## Команды

- Codex memory audit: `python3 .vault-meta/evolution/memory-audit.py --platform codex`
- Wiki lint: `python3 .vault-meta/evolution/lint-scan.py`
- Codex friction scan: `python3 .vault-meta/evolution/trace-scan.py --platform codex`
- Codex repeated procedures: `python3 .vault-meta/evolution/trace-scan.py --platform codex --repeats`
- Wiki lock: `bash scripts/wiki-lock.sh acquire .vault-meta/evolution/run`, затем `bash scripts/wiki-lock.sh release .vault-meta/evolution/run`
- Retrieval refresh: `VAULT_ROOT="$PWD" bash <path-to-claude-obsidian-plugin>/bin/setup-retrieve.sh --no-llm`
- Codex nudge: `EVOLUTION_PLATFORM=codex python3 .vault-meta/evolution/health-check.py`
- State update: `python3 .vault-meta/evolution/update-last-run.py --platform codex --phase curator_memory=DATE --phase wiki_hygiene=DATE --phase retrieval_refresh=DATE --phase reflection=DATE --notes "SUMMARY"`

Запуск без `--platform codex` у memory-audit и trace-scan сохраняет прежнее Claude-поведение для обратной совместимости.
