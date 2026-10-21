---
name: evolution
description: "Прогон гигиены общего vault в Codex: диагностика Codex memory, zone-aware wiki lint/fold и retrieval refresh. Используй при запросах: прогони evolution, run evolution, гигиена vault, прибраться в vault, vault evolution, обнови индексы вики."
---

# Vault Evolution for Codex

Прогон гигиены общего Claude + Codex vault из трёх фаз: `curator_memory`, `wiki_hygiene`, `retrieval_refresh`. Запускается только по слову владельца (`$evolution` или «прогони evolution»); нудж health-check сам по себе не задание. Деструктивные операции требуют явного одобрения владельца.

Reflection (фаза 4 в `EVOLUTION.md`) и регистрация ходов Codex (`trace-session.py`) отключены по умолчанию, так же как для Claude. Не запускай `trace-session.py`, `trace-manifest.py`, `trace-scan.py`, `reflection-disposition.py` и `$evolution-verifier` в этом прогоне. Код сохранён, включение только по прямому слову владельца.

## Как исполнять

1. Прочитай фазы 1-3 и «Финализацию» в `.vault-meta/evolution/EVOLUTION.md` и следуй им и инвариантам.
2. Соблюдай zone isolation, L0-L3 loadout только для активной зоны, append-only `log.md`, wiki-lock перед пакетными правками и запрет на внешний egress.
3. Одобренные межагентные факты, решения и уроки сохраняй в обычные PARA-страницы, индекс, log и hot. Не копируй внутренние Codex-memory или трейсы в Claude.
4. Обновляй `.vault-meta/evolution/last-run.json` только через `.vault-meta/evolution/update-last-run.py`.

## Codex-адаптации

- Долговременная проектная память Codex в этом vault находится в обычных PARA-страницах wiki.
- `~/.codex/memories_1.sqlite` является внутренним генерируемым состоянием. Evolution проверяет его только на чтение и не редактирует.

## Команды

- Codex memory audit: `python3 .vault-meta/evolution/memory-audit.py --platform codex`
- L0-L3 scan: `/usr/bin/python3 scripts/memory-model.py scan --zone ZONE`
- L0-L3 index under the shared writer lock: `/usr/bin/python3 scripts/memory-model.py build --zone ZONE --lock-token TOKEN`
- L0-L3 validation: under a writer lock use `/usr/bin/python3 scripts/memory-model.py validate --zone ZONE --lock-token TOKEN`; read-only checks after release omit the token
- Retrieval quality: `/usr/bin/python3 scripts/memory-model.py evaluate --zone ZONE` after release. A red result goes into the report to the owner; do not re-record the goldens without the owner's word.
- L0-L3 query: `/usr/bin/python3 scripts/memory-model.py retrieve "QUERY" --zone ZONE --loadout ROLE`
- Context inspector: `/usr/bin/python3 scripts/memory-model.py inspect "QUERY" --zone ZONE --loadout ROLE`
- Memory candidates: `/usr/bin/python3 scripts/memory-candidates.py list --zone ZONE`
- Wiki lint: `python3 .vault-meta/evolution/lint-scan.py --zone ZONE`
- Wiki lock: use shared `.vault-meta/write/ZONE`; acquire returns `TOKEN`; renew and check after long phases and before later writes, build and checkpoint; release requires that exact token. Expired writer records remain fail-closed until explicit recovery.
- Retrieval refresh: `/usr/bin/python3 scripts/memory-model.py build --zone ZONE --lock-token TOKEN`
- Codex nudge: `EVOLUTION_PLATFORM=codex /usr/bin/python3 .vault-meta/evolution/health-check.py`
- State update after successful validate: `python3 .vault-meta/evolution/update-last-run.py --platform codex --lock-token TOKEN --phase curator_memory=DATE --phase wiki_hygiene=DATE --phase retrieval_refresh=DATE --retrieval-pages PAGES --retrieval-chunks CHUNKS --snapshot-sha256 SHA256 --loadouts-sha256 SHA256 --notes "SUMMARY"`. The writer records exactly the phases passed in this run.

`ZONE` means the active session zone (`work`, `personal` or `client`). Never substitute `all`.

Claude использует тот же playbook и state, его команды описаны в `.claude/skills/evolution/SKILL.md`.
