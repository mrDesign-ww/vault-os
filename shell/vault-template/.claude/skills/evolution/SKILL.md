---
name: evolution
description: "Прогон самосовершенствования vault: Curator памяти, zone-aware wiki lint/fold, retrieval refresh, reflection на трейсах. Триггеры: прогони evolution, run evolution, гигиена vault, прибраться в vault, vault evolution, обнови индексы вики."
---

# Vault Evolution

Единый прогон гигиены и обучения vault. Запускается по SessionStart-нуджу (`health-check.py`) или вручную. НЕ автономный робот: деструктивные операции идут через апрув владельца.

## Как исполнять

1. Прочитай авторитетный плейбук `.vault-meta/evolution/EVOLUTION.md` и следуй его фазам и ИНВАРИАНТАМ.
2. Инварианты (жёстко): zone isolation (каждая зона раздельно, имена и пути зон из `mode.json`); `log.md` append-only (только fold, не редактировать); `hot.md` не трогать вживую; wiki-lock перед пакетными правками вики; никогда молчаливой записи в CLAUDE.md/память (только предложения на апрув); клиентские данные не слать наружу (retrieval `--no-llm`).
3. Фазы по порядку: (1) Curator памяти → (2) zone-aware wiki гигиена → (3) retrieval refresh → (4) reflection (Tier 2, с гейтом `verifier` + апрув).
4. По завершении обнови `.vault-meta/evolution/last-run.json` и дай владельцу краткий отчёт: что починено сразу, что ждёт апрува.

## Роли

- Codex = дизайн/идеи (эскалация структурных проблем). Claude = реализация. Гейт = `claude-obsidian:verifier` + владелец. Codex НЕ критик.

## Быстрые команды

- Диагностика памяти: `python3 .vault-meta/evolution/memory-audit.py`
- Вики-lint по зонам: `python3 .vault-meta/evolution/lint-scan.py` (dead классифицируй: memory-refs / Reference / реально-unresolved)
- Сигналы трения (reflection): `python3 .vault-meta/evolution/trace-scan.py`
- Повторяющиеся процедуры (кандидаты капсуляции): `python3 .vault-meta/evolution/trace-scan.py --repeats`
- Retrieval refresh: `VAULT_ROOT="$PWD" bash <path-to-claude-obsidian-plugin>/bin/setup-retrieve.sh --no-llm`
- Нудж-состояние: `python3 .vault-meta/evolution/health-check.py`
