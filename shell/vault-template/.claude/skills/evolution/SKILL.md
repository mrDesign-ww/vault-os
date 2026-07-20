---
name: evolution
description: "Прогон самосовершенствования общего vault: Claude memory, zone-aware wiki lint/fold, retrieval refresh и reflection по Claude-сессиям. Триггеры: прогони evolution, run evolution, гигиена vault, прибраться в vault, vault evolution, обнови индексы вики."
---

# Vault Evolution for Claude

Единый прогон гигиены и обучения общего Claude + Codex vault. Запускается по SessionStart-нуджу или вручную через `/evolution`. Деструктивные операции требуют явного одобрения владельца.

## Как исполнять

1. Прочитай `.vault-meta/evolution/EVOLUTION.md` и следуй его фазам и инвариантам.
2. Соблюдай zone isolation, append-only `log.md`, wiki-lock перед пакетными правками, запрет на внешний egress без разрешения и approval gate для изменений инструкций, памяти и skills.
3. Для Claude используй команды без `--platform`: они сохраняют прежний Claude-режим.
4. Одобренные межагентные факты, решения и уроки сохраняй в обычные PARA-страницы, индекс, log и hot. Не копируй внутренние Claude-memory или трейсы в Codex.
5. Перед предложениями reflection используй `claude-obsidian:verifier`, затем запроси одобрение владельца.
6. Обновляй `.vault-meta/evolution/last-run.json` только через `.vault-meta/evolution/update-last-run.py`.

## Команды

- Claude memory audit: `python3 .vault-meta/evolution/memory-audit.py`
- Wiki lint: `python3 .vault-meta/evolution/lint-scan.py`
- Claude friction scan: `python3 .vault-meta/evolution/trace-scan.py`
- Claude repeated procedures: `python3 .vault-meta/evolution/trace-scan.py --repeats`
- Wiki lock: `bash scripts/wiki-lock.sh acquire .vault-meta/evolution/run`, затем `bash scripts/wiki-lock.sh release .vault-meta/evolution/run`
- Retrieval refresh: `VAULT_ROOT="$PWD" bash <path-to-claude-obsidian-plugin>/bin/setup-retrieve.sh --no-llm`
- Claude nudge: `python3 .vault-meta/evolution/health-check.py`
- State update: `python3 .vault-meta/evolution/update-last-run.py --platform claude --phase curator_memory=DATE --phase wiki_hygiene=DATE --phase retrieval_refresh=DATE --phase reflection=DATE --notes "SUMMARY"`

Codex использует тот же playbook и state, но запускает platform-specific команды из `.agents/skills/evolution/SKILL.md`.
