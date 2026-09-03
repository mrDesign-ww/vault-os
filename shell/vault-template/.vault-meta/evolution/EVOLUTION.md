# Vault Evolution: общий плейбук самосовершенствования

Единый прогон гигиены и обучения общего vault. Его может выполнять Claude через `/evolution` или Codex через `$evolution`. Это не автономный робот: Влад участвует во всех деструктивных решениях.

Идея заимствована из Hermes Agent (Nous Research): closed learning loop, Curator и trace-driven improvement. Она адаптирована под общий стек Codex + Obsidian + Claude. Ключевое отличие: человек остается в петле на всех деструктивных операциях.

## Межагентный контракт

1. Общая долговременная память находится в обычных PARA-страницах vault, индексах, log и hot активной зоны.
2. Внутренние хранилища не синхронизируются напрямую. Claude использует свой Markdown-memory и свои session traces. Codex использует свою SQLite-memory только для read-only диагностики и свои session traces для reflection.
3. Каждый агент анализирует собственное внутреннее состояние, а одобренные факты, решения, handoff и устойчивые уроки материализует в общей wiki. Так второй агент получает их при следующем чтении активной зоны.
4. Platform-specific команды задаются в `.claude/skills/evolution/SKILL.md` и `.agents/skills/evolution/SKILL.md`. Этот файл задает общую политику.

## Модель памяти L0-L3

PARA отвечает за место хранения. L0-L3 отвечает за доверие, продвижение и порядок загрузки контекста.

- **L0:** сырые sources, imports, traces, inbox, logs, `analysis/raw`, asset и QA evidence, prototype internals. Это evidence, а не подтверждённая истина. Evidence path всегда остаётся L0. Проверка создаёт отдельную L1 или L2 synthesis page.
- **L1:** проверенные факты, решения, ограничения, findings и lessons с provenance.
- **L2:** состояние проекта, indexes, architecture, specifications, scenarios, playbooks и handoff.
- **L3:** стабильные правила владельца, privacy policy и operating doctrine. Только после явного approval владельца.

Новые и существенно обновляемые страницы получают `memory_level`. Старые страницы не переписываются массово: `scripts/memory-model.py` определяет уровень по path, filename и `type`. `hot.md` имеет роль cache и не является канонической памятью.

Порядок loadout: approved L3, active L2, релевантные L1, затем L0 только для проверки evidence. L0 не может переопределить L1-L3. Страницы со статусом `superseded`, `retired`, `historical` или `do-not-implement` по умолчанию не входят в retrieval.

Продвижение:

1. L0 в L1: факт проверен и имеет provenance.
2. L1 в L2: факт синтезирован в повторяемый сценарий или canonical project state.
3. L2 в L3: только явное одобрение владельца, hardcoded exact owner, ISO date, matching page hash в `.vault-meta/memory-approvals.json` и matching code anchor самого manifest. Это human-reviewed cooperative boundary, не криптографическое доказательство личности против coordinated code plus manifest edit.

Evolution автоматически диагностирует и предлагает promotion, но не повышает L2 до L3 молча.

Автоматически inferred или uncertain memory сначала создаётся как L0 candidate в
`memory-candidates/`. Она не участвует в default retrieval. После проверки и
одобрения создаётся отдельная canonical L1 или L2 page. Named loadouts `general`,
`designer`, `builder`, `researcher` и `reviewer` ограничивают project scope,
уровни, item count, snippet characters и pinned skills. Budget нельзя повысить
через CLI. `memory-model.py inspect` показывает точный selection trace.
Proposal остаётся незавершённым до обновления candidate catalog, zone log и hot,
build + validate под тем же owner token, scoped local checkpoint, release и
post-release list или project-scoped `--include-l0` query. Candidate сохраняет
source path + SHA-256; `type: memory-candidate` всегда L0 и допустим только в
canonical candidate inbox активной зоны.

## Инварианты (нарушать нельзя)

1. **Zone isolation.** work / personal / client обрабатываются РАЗДЕЛЬНО. Пути берутся из `.vault-meta/mode.json` (у каждой зоны свои index/log/hot). Никогда не кросс-линковать и не ко-листить зоны. Активная зона по умолчанию work.
2. **Log append-only.** `log.md` любой зоны НИКОГДА не редактируется задним числом. Уменьшение объёма только через `wiki-fold` (сворачивает старые записи в fold-страницу, не удаляя смысл).
3. **hot.md является кэшем.** Не используй его как журнал. После значимой операции перезапиши его кратким актуальным контекстом активной зоны, не более 500 слов.
4. **wiki-lock перед пакетными правками вики.** Все batch writers используют один path `.vault-meta/write/ZONE`. `acquire` возвращает owner token. Проверяй или продлевай его при долгом прогоне. `release` принимает тот же token и не может снять чужой lock. Default TTL 6 часов.
5. **Никогда молчаливой записи в load-bearing.** Правки `CLAUDE.md`, `AGENTS.md`, внутренних memory и skills идут только как предложения на одобрение владельца. Исключение: явно заказанные Владом настройки и чисто механические безопасные фиксы.
6. **Клиентские данные не покидают машину.** L0-L3 build всегда synthetic и on-machine. Legacy plugin retrieval запускается только с `--no-llm`. Egress-tier (claude-cli / Anthropic API) разрешён только по явному решению владельца для конкретного прогона.
7. **Внутренняя memory вне vault-git.** Claude-memory и Codex SQLite не входят в репозиторий vault. Не копируй их содержимое между клиентами.

## Фаза 1 — Curator памяти

Цель: внутреннее состояние выбранной платформы не повреждено, а общая проектная память остается в wiki.

1. Claude запускает `python3 .vault-meta/evolution/memory-audit.py`. Codex запускает `python3 .vault-meta/evolution/memory-audit.py --platform codex`.
2. Для активной зоны запустить `/usr/bin/python3 scripts/memory-model.py scan --zone ZONE`. Invalid level, L0 self-promotion, explicit L1 без provenance и L3 без manifest approval являются ошибками. Legacy inference разрешён только как `legacy-unverified` discovery и не равен проверке.
3. **Безопасные фиксы (применяю сразу, whitelist):**
   - orphan-файл (есть на диске, нет в `MEMORY.md`) → добавить строку-указатель в индекс;
   - dead link (индекс ссылается на несуществующий файл) → убрать строку из индекса;
   - битый/отсутствующий frontmatter → починить поля.
4. **Предложения на апрув (не применяю молча):**
   - консолидация кластера (например 11 файлов `project-alpha-*`) в 2-3 связных;
   - устаревшие статусы (файл старый + содержит статус-маркеры) → «подтверди/обнови»;
   - удаление того, что стало неверным.
   - promotion L0 в L1 или L1 в L2, если evidence достаточно;
   - любое promotion или изменение L3.
5. Обновить `MEMORY.md`, чтобы индекс = факты на диске.

## Фаза 2 — Wiki гигиена (zone-aware)

Только для активной session zone, по её путям из `mode.json`. Один evolution-run никогда не переходит в другую зону:

1. Быстрый детерминированный проход: `python3 .vault-meta/evolution/lint-scan.py --zone ZONE` (dead wikilinks, orphans, frontmatter gaps активной зоны). Команды `--zone all` нет. Scanner не перечисляет другие зоны и отдельно отмечает только explicit path cross-links. `Reference/` классифицируется отдельно. Внутренняя agent memory не считается Obsidian target.
2. Если нужен глубже (stale claims, missing xrefs) — `wiki-lint` скилл. Всё это ОТЧЁТ. Автофиксы вики-контента (dead, orphans, frontmatter) НЕ применять без апрува владельца.
3. Взять token-owned wiki-lock перед любыми пакетными правками вики; снять тем же token после проверки.
4. Fold: `wiki-fold` создаёт навигируемую rollup-страницу в `folds/` и НЕ усекает лог (лог append-only, растёт by design). Запускать когда лог реально большой и нужен обзор; dry-run сначала, commit по апруву.

Для другой зоны нужен отдельный session switch и отдельный evolution-run. В текущем прогоне её пути не перечислять и содержимое не читать.

## Фаза 3 — Retrieval refresh

Цель: zone-scoped L0-L3 retrieval не отстаёт от vault и не смешивает зоны.

1. Определить активную зону по session mode. Каждая зона имеет отдельный index в `.vault-meta/memory-index/`.
2. Запустить `/usr/bin/python3 scripts/memory-model.py validate --zone ZONE`. Проверка сверяет exact local-Git authority индекса, resolver, L3 approvals, loadouts, payload digest и logical file-state. Полный content snapshot пересобирается только locked build.
3. Если index отсутствует, устарел или отличается от Markdown: взять shared zone lock, запустить `/usr/bin/python3 scripts/memory-model.py build --zone ZONE --lock-token TOKEN`, затем `/usr/bin/python3 scripts/memory-model.py validate --zone ZONE --lock-token TOKEN`.
4. Сохранить token до финализации. После checkpoint и release выполнить project-scoped запрос с подходящим named loadout: `/usr/bin/python3 scripts/memory-model.py retrieve "active project state" --zone ZONE --project PROJECT --loadout ROLE`. Проверить snippet и context budget.
5. L0 не включается по умолчанию. Для source verification добавить `--include-l0`.

Legacy `.vault-meta/chunks/` и `.vault-meta/bm25/` остаются regenerable artifacts старого hybrid retrieval. Не использовать их напрямую для межзонных запросов, потому что прежний `--all` индекс не соблюдает zone isolation.

## Фаза 4 — Reflection (Tier 2, обучение на трении)

Цель: уроки, которые нигде не записались, оседают в рабочей памяти без
повторяющихся пауз на owner approval.

**Standing autonomous gate:** direct-owner L3 policy
`reflection-policy.json` один раз задаёт точный vault, work zone, платформы,
roots, limits, selector, scanner, lifecycle hooks, evolution workflows и verifier hashes. Per-manifest ручного
approval нет. Policy drift, code drift или revocation отключает только reflection;
обычная работа и фазы 1-3 продолжаются.

Система не угадывает область и завершение по тексту:

- Codex регистрирует каждый turn с durable active-zone ledger и публикует receipt только после
  platform marker `task_complete`. Receipt связывает exact byte range, поэтому
  последующее resume того же task не меняет уже завершённый segment. Перед новой
  регистрацией lifecycle states группируются по всем policy generations. Exact
  duplicates сводятся к одному, conflicts отклоняются до публикации. Новый receipt
  связывает registration policy/hook и current closing authority. После durable
  prepare его exact bytes и completion generation не меняются при retry или
  policy rollover. Zone switch сначала находит единственный открытый turn во всех
  policy generations. Если effective zone уже задана one-off transition, session
  switch повышает её до session scope и обновляет ledger.
- Claude `UserPromptSubmit` hook связывает prompt hash, монотонный session
  sequence и policy-independent zone ledger через recoverable write-ahead
  transaction. Официальный `Stop` hook подтверждает exact transcript range;
  tool-result envelope остаётся внутри byte range, но не считается новым prompt.
  Пропущенный Stop восстанавливается на следующей prompt boundary. Interrupted
  registration завершается, interrupted switch откатывается перед несвязанным
  prompt. Operational read, publication и state-write failures остаются retryable;
  только deterministic mixed segment получает retirement. Exact duplicates across
  policy generations сводятся к одному, а conflicting states или prepared receipts
  отклоняются до публикации. Durable prepared receipt повторно публикуется exact в
  своей original completion generation. Switch находит единственный открытый turn
  во всех policy generations и повышает совпавший one-off route до session scope.
  Exact session-switch и one-off route становятся structured metadata до обработки запроса. Configured
  hook сохраняет nonzero durable lifecycle failure, а не теряет его молча.
- Rollover читает только current policy и exact pinned predecessors с их hook,
  delegation и not-before. Пока predecessor lifecycle или global processed marker
  существует, его authority сохраняется в следующей policy. Legacy predecessor
  без processor hash допустим только с exact proof-empty для generated и flat
  scan catalogs.
- До новой selection проверяются scan catalogs всех pinned predecessors. Любой
  scan без exact global processed coverage останавливает только reflection batch
  с policy и scan identity. Тот же completion не может войти в новый outcome
  cycle. Policy rollout выполняется после завершения исходного cycle.
- Любой записанный переход в personal или Client делает turn неeligible для work.
- Legacy traces без lifecycle receipt не читаются ретроактивно.

Команды автономного batch:

1. Выбрать oldest-first bounded batch:
   `python3 .vault-meta/evolution/trace-manifest.py select --platform claude|codex --zone work --publish-noop --lock-token TOKEN`.
   Zero eligible является здоровым `no-eligible-traces` и публикует content-free
   selector receipt с cutoff и exact catalog identity.
2. Проверить manifest без quotes. Scanner детерминированно пересобирает selection
   на exact cutoff; любой skipped segment даёт incomplete и nonzero:
   `python3 .vault-meta/evolution/trace-scan.py --platform PLATFORM --zone work --manifest MANIFEST --verify-only --json`.
3. Под shared work lock выполнить scan и durable content-free receipt:
   `python3 .vault-meta/evolution/trace-scan.py --platform PLATFORM --zone work --manifest MANIFEST --publish-receipt --lock-token TOKEN --json`.
4. Разобрать сигналы трения:
   - я переспросил то, что уже есть в CLAUDE.md/памяти → инструкция плохо находится/сформулирована;
   - Влад меня поправил («нет», «не так», «я же говорил») → есть невыраженное правило;
   - я пошёл не туда и переделывал → не хватает guardrail.
5. Claude применяет `evolution-verifier`, Codex применяет `$evolution-verifier`.
   Verified L1/L2 можно материализовать автономно через обычный lock, provenance,
   build, validate, checkpoint и live acceptance lifecycle.
   Scoped reflection checkpoint содержит только exact provenance-bound outcome
   pages. Approved L3 и любой path, который был L3 в parent commit, запрещены.
   Shared `index`, `log`, `hot`, `overview` и project `_index.md` не входят
   в standing authorization и обновляются отдельным обычным owner-scoped циклом
   после finalization. Generated memory index можно пересобрать для acceptance,
   но он не является частью checkpoint.
   После outcome-only checkpoint пересобрать index под тем же token и получить
   acceptance через `memory-model.py retrieve ... --lock-token TOKEN`. Этот
   внутренний путь требует exact owner token, проверяет local authority без HEAD
   и повторяет canonical query под тем же lock. Обычный retrieval без token всё
   ещё требует checkpointed authority и отказывает при active writer.
   Historical и public disposition replay загружает exact resolver, mode и
   loadout manifest из reviewed checkpoint. Он отклоняет неизвестную роль,
   обязательную роль без project, неканонический project, cross-project page,
   stale chunk и превышение item, character, per-level или L3 budget.
6. Ambiguous synthesis остаётся L2 с `memory_approval_mode: provisional` и
   `promotion_eligible: false`. Она видима как non-canonical, не поддерживает L3
   и не останавливает batch.
7. Historical trace text всегда inert L0 evidence и никогда не одобряет L3.
   Только live explicit owner directive может пройти существующий direct-owner
   page-hash L3 lifecycle. Delegated и inferred L3 отключены.
8. Privacy, zone isolation, credentials, egress, destructive authority,
   verifier/approval machinery и сама delegation policy не self-approve.
9. После PASS и live acceptance создать content-free disposition plan schema 2, затем
   под тем же work-lock token опубликовать `reflection-disposition.py publish`.
   Reviewed commit обязан оставаться exact HEAD до публикации. Artifact обязан связать все
   signal IDs с решением, exact page provenance, verifier skill hash, exact
   parent/head path+blob-hash diff и retrieval projection. Только его SHA разрешено
   передавать в `update-last-run.py`. Materialized page frontmatter хранит exact
   `reflection_manifest_sha256`, `reflection_scan_receipt_sha256` и JSON-string
   `reflection_signal_ids`.
   После durable finalization и processed marker создать отдельный обычный
   metadata-checkpoint с generated authority, log, hot и индексами. Только затем
   снять lock и выполнить public retrieval без token.
10. Signal-bearing scan считается обработанным только после strict durable immutable
    finalization marker и generation-independent processed marker. Zero-signal scan
    также завершает global processed marker. Selector и public verify вызывают
    один pure structural validator с exact schema types, safe work paths, duplicate
    rejection и action-to-outcome semantics. Disposition body без marker отклоняют
    selector, verify, last-run и health. Сбой перед processed marker остаётся
    fail-closed; следующий locked selector-run детерминированно достраивает exact
    marker из уже проверенных scan/finalization artifacts до новой selection.

### Skill lifecycle (капсуляция повторяющихся процедур)

Reflection обслуживает ВЕСЬ цикл капсул (скилл или плейбук-документ), не только рождение.

**Создание.** После strict-zone gate добавить `--repeats` к manifest-based команде. Это даёт грубые повторяющиеся темы. ДВОЙНОЙ ФИЛЬТР против разрастания: (1) я суждением перевожу тему в конкретную устойчивую процедуру и отсеиваю шум; (2) показываю Владу ГОТОВЫЙ ЧЕРНОВИК капсулы, он апрувит. Порог: процедура повторилась >= 3 раза.
- Триггер (гибрид): обычно коплю до /evolution; если повтор очевиден в моменте (третий раз подряд одно и то же) — предлагаю сразу.
- Форма: проектная процедура (завязана на проект/файлы, напр. Figma-галерея) -> плейбук-документ В ПРОЕКТЕ (как этот файл), НЕ трогает глобальный набор скиллов. Кросс-проектная (доказанно нужна в >= 2 проектах) -> формальный скилл через `skill-creator`.
- Пишется КАЧЕСТВЕННО, с суждением, не автоболванкой.

**Обновление (дрейф).** Устаревшая капсула опаснее её отсутствия (молча ведёт по мёртвому пути).
- Явная правка флоу (Влад сказал «теперь иначе») -> обновляю капсулу СРАЗУ (capture-on-spot, как память).
- Неявный дрейф -> reflection ловит трение при исполнении капсулы (Влад поправил / я отклонился / результат не тот) -> предлагает обновить.

**Retire.** Капсула долго не используется или устарела совсем -> Curator предлагает архивировать.

Инвариант: заметить и предложить — автоматически; создать/переписать/удалить капсулу — только через апрув владельца. Никогда молча.

## Финализация

1. После каждого потенциально долгого этапа и перед следующей canonical записью выполнить `renew`, затем `check`. Перед build и scoped checkpoint повторить `check`. После всех Markdown, index, log и hot правок собрать и проверить memory index под token-owned lock: `build --zone ZONE --lock-token TOKEN`, затем `validate --zone ZONE --lock-token TOKEN`. Создать scoped local checkpoint, снять lock в `finally`, после чего выполнить живой project-scoped query и проверить содержание snippet. Retrieval build должен быть последней wiki-записью. Expired record остаётся блокирующим до token release или явного scoped stale-clear после подтверждения смерти writer.
2. После успешного live query обновить platform-specific состояние в `.vault-meta/evolution/last-run.json` через `update-last-run.py --platform claude|codex --lock-token TOKEN`, явно передав все обязательные phases и typed evidence из успешного validate: `--retrieval-pages PAGES --retrieval-chunks CHUNKS --snapshot-sha256 SHA256 --loadouts-sha256 SHA256`. Writer проверяет work-lock до валидации и перед replace, отклоняет rollback даты, placeholders и retrieval complete без всех четырёх значений. Датированный completed reflection дополнительно требует exact `--reflection-manifest-sha256`, `--reflection-receipt-sha256`, `--reflection-disposition-sha256`, `--reflection-processed-sha256` и `--reflection-signal-count`. Если selector не нашёл завершённых turns, использовать `ready-autonomous-no-eligible-traces` вместе с exact `--reflection-noop-sha256`; пока lifecycle ждёт completion, использовать `ready-autonomous-awaiting-completion`; invalid policy или code anchor получает `blocked-delegation-invalid`. Затем создать scoped metadata checkpoint truthful post-query state. Состояние второго агента сохраняется. Без этого checkpoint evolution run не считается durable.
3. Краткий отчёт Владу: что починено сразу, что ждёт апрува, что отложено.

## Роли в стеке

- **Codex** обычно ведет проектирование, дизайн-решения, гипотезы, critique и handoff.
- **Claude** обычно ведет тяжелую реализацию, исполнение и гигиену.
- Это специализация, а не запрет. Общие результаты обоих агентов живут в vault. Один и тот же code-anchored verifier contract действует для обеих платформ. PASS автономен только для L1/L2 в standing scope; запрещённые классы требуют прямого указания владельца.

## Как это связано с остальным

- Health-нудж (`health-check.py`) читает `last-run.json` и подсказывает запуск, когда пора. Claude вызывает его SessionStart hook; Codex запускает его по load-bearing правилу в `AGENTS.md`.
- Плейбук исполняет активный агент. Детерминированная часть вынесена в скрипты, суждение остается за агентом и Владом.
- Tier-разбивка: Фазы 1-3 = Tier 1/3 (гигиена, низкий риск). Фаза 4 = Tier 2 (обучение, апрув-гейт).
