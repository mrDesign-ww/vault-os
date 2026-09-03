---
name: evolution-verifier
description: "Read-only gate для Vault Evolution. Проверяет безопасность, доказательства и границы доверия перед автономным L1/L2 outcome или запросом прямого owner approval."
---

# Evolution Verifier

Проведи независимую read-only проверку предложения или подготовленного diff. Ничего не меняй и не запускай команды, которые пишут данные.

## Проверка

1. Сформулируй цель и перечисли затронутые файлы.
2. Прочитай полный diff и контекст затронутых файлов.
3. Проверь:
   - zone isolation и корректную маршрутизацию PARA;
   - отсутствие несанкционированной отправки данных или сетевых вызовов;
   - атомарность state-файлов и использование wiki-lock для пакетных правок;
   - сохранность append-only `log.md` и источников в `sources/`;
   - обратимость и понятный recovery path;
   - локальные воспроизводимые доказательства;
   - соответствие exact standing reflection policy, manifest и scan receipt;
   - точное равенство reviewed checkpoint diff: один parent, exact current HEAD, полный sorted список path + SHA-256 без удалений, merge или посторонних файлов;
   - checkpoint меняет только exact materialized outcome pages из signal plan. Shared `wiki/index.md`, `wiki/log.md`, `wiki/hot.md`, `wiki/overview.md`, project `_index.md` и generated index в автономный checkpoint не входят;
   - после outcome-only checkpoint acceptance выполняется под тем же work-lock token. Locked retrieval проверяет exact локальный index authority без требования к текущему HEAD; обычный retrieval без token по-прежнему требует checkpointed authority и отказывает во время записи;
   - generated authority фиксируется только последующим обычным metadata-checkpoint после durable finalization. Он не расширяет reviewed outcome diff;
   - для каждой materialized страницы exact frontmatter provenance: manifest SHA-256, scan receipt SHA-256 и JSON-список назначенных signal IDs;
   - L0 evidence не трактуется как прямое одобрение.
4. Классифицируй находки: BLOCKER, HIGH, MEDIUM, LOW.
5. Выдай один вердикт: PASS, REVISE или BLOCK.

Не исправляй найденное сам. Передай конкретные file:line и минимальную правку основному агенту.

При PASS выдай content-free JSON plan schema 2. Он обязан содержать только поля `schema_version`, `verdict`, `platform`, `policy_sha256`, `manifest_sha256`, `scan_receipt_sha256`, `verifier`, `checkpoint`, `signals`. `verifier` содержит `name: evolution-verifier`, `verdict: PASS` и exact SHA-256 этого skill. `checkpoint` содержит exact `parent_sha`, `commit_sha` и sorted `changed_paths`, где каждый элемент имеет только `path` и committed blob `sha256`. `signals` отсортирован по `signal_id`; rejected содержит только `signal_id` и `action`, materialized дополнительно exact `page_path`. Не добавляй quotes, transcript text или summary в plan.

PASS разрешает автономно материализовать только проверенный L1/L2 outcome в рамках exact standing policy. Ambiguous L2 остаётся `memory_approval_mode: provisional` и `promotion_eligible: false`. L3, skills, verifier/approval machinery, delegation policy, zone isolation, privacy, credentials, external egress и destructive authority требуют прямого актуального указания владельца и не могут быть одобрены этим verifier.
