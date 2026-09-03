---
type: reference
title: "Obsidian Plugins"
status: evergreen
created: {{DATE}}
updated: {{DATE}}
memory_level: l1
memory_provenance: "bundled Obsidian plugin reference"
tags:
  - reference
  - cheatsheet
  - obsidian
  - plugins
related:
  - "[[Reference/_index|Reference]]"
  - "[[Claude Commands]]"
  - "[[index]]"
---

# Obsidian Plugins (шпаргалка)

Community-плагины этого vault. Работают **внутри Obsidian** (не в Claude, это другое — см. [[Claude Commands]]).

- **Вызвать команду:** `Cmd + P` → имя команды.
- **Все команды плагина:** `Cmd + P` → имя плагина (увидишь полный список).
- **Настройки:** Settings → Community plugins → шестерёнка у плагина. Горячие клавиши: Settings → Hotkeys → фильтр по имени.

Команды ниже извлечены из кода плагинов (актуально на 2026-06-01).

---

## Advanced Canvas (v6.2.0)

Прокачка Obsidian Canvas: фигуры флоучартов, презентации, навигация по графу заметок прямо на холсте.

**Сценарии:**
- **Клиентское ревью**: холст с экранами проекта → режим презентации → листаешь по узлам как слайды на созвоне.
- **Архитектурная схема**: флоучарт системы (модули → связи) фигурами и стрелками.
- **Карта user-flow**: экраны/шаги узлами + рёбрами; `Pull backlinks/outgoing links` подтягивает связанные заметки на холст.
- **Декомпозиция**: `Encapsulate selection` сворачивает кусок холста в отдельный canvas-файл.

**Команды (28):**
- *Презентация:* Create new slide · Set start node · Start presentation · Continue presentation · End presentation · Previous node · Next node
- *Узлы и группы:* Group selected nodes · Create text node · Create file node · Encapsulate selection · Swap nodes · Toggle collapse group · Toggle Focus Mode · Toggle readonly
- *Рёбра и выделение:* Select all edges · Select connected edges · Select incoming edges · Select outgoing edges · Flip selection horizontally · Flip selection vertically
- *Граф заметок:* Copy wikilink to node · Pull outgoing links to canvas · Pull backlinks to canvas
- *Зум и экспорт:* Zoom to selection · Zoom to fit · Export canvas as image · Export selected nodes as image

## Banners (v1.3.3)

Картинка-баннер в шапке заметки + иконка.

**Сценарии:**
- Обложка для каждого проектного MOC (баннер бренда на странице проекта).
- Визуальное различение разделов (своя картинка на projects / resources).
- Иконка-эмодзи в шапке для быстрого узнавания типа заметки.

**Как (frontmatter):**
```yaml
banner: "путь/или/url.jpg"
banner_x: 0.5      # горизонтальный фокус 0..1
banner_y: 0.3      # вертикальный фокус 0..1
banner_icon: "📁"
```

**Команды (6):** Add/Change banner with local image · Paste banner from clipboard · Add/Change emoji icon · Lock/Unlock banner position · Remove banner · Remove icon

> Позицию баннера можно тянуть мышью (если не Lock) — сохранится в `banner_y`.

## Excalidraw (v2.23.8)

Рисованные схемы и вайтборды. «4D PKM»: внутри рисунка живут текст и ссылки на заметки.

**Сценарии:**
- Быстрый **вайрфрейм** экрана до Figma (накидать блоки, показать идею команде).
- **Аннотация скриншота**: вставить картинку экрана и обвести/подписать проблемы (связка с UX-аудитом).
- **Whiteboard токенов/потоков** от руки, когда canvas-сетка мешает.
- Диаграммы со **ссылками на заметки** (клик по элементу открывает заметку проекта).

**Как:** `Cmd+P` → "Create new drawing" → файл `*.excalidraw.md`. Встроить в заметку: `![[имя.excalidraw]]`. Переключение рисунок ↔ markdown — команда Toggle. Иконка на левой ленте.

**Команды:** плагин регистрирует **~70+ команд** (имена локализованы, поэтому смотри полный список через `Cmd+P` → "Excalidraw"). Практическое ядро:
- Create new drawing (+ в новой вкладке / панели / активной панели)
- Insert drawing / Transclude drawing (встроить в заметку)
- Toggle between Excalidraw and Markdown mode
- Convert *.excalidraw to Excalidraw-markdown
- Insert image / Insert markdown file / Insert PDF page / Insert any file
- Export image (PNG / SVG)
- Search for text elements
- Toggle fullscreen
- Run Excalidraw Automate script

## Homepage (v4.4.2)

Стартовая страница: открывает выбранную заметку/canvas/base/workspace при запуске или по команде.

**Сценарии:**
- При старте Obsidian сразу открывается [[overview]] или [[workspace/projects/_index|Projects]] — не ищешь, с чего начать.
- Быстрый «домой» по команде из любого места.

**Как:** Settings → Homepage → выбрать заметку + опции (открывать при запуске, в новой вкладке, на мобиле).
**Команда (1):** Open homepage

## Mind Map (v1.1.0)

Превью заметки как интерактивная mind-map (Markmap): заголовки → ветки.

**Сценарии:**
- Структурный док (`00-PROJECT-BRIEF`, `10-SPEC`) одним взглядом как дерево.
- Быстрый обзор большого MOC перед погружением.
- Экспорт ветки в SVG/PNG для презентации.

**Как:** открыть заметку → `Cmd+P` → команда ниже (откроется сбоку). `#`/`##`/`###` → ветки, списки → листья.
**Команда (1):** Preview the current note as a Mind Map

## Tasks (v8.0.0)

Задачи по всему vault: дедлайны, повторение, приоритеты, фильтры, запрос-блоки. Самый мощный из набора.

**Сценарии:**
- Трекинг многоэтапной работы: под-задачи с приоритетом и сроком.
- Личный дашборд: блок ```tasks``` на [[overview]] собирает все незакрытые задачи по проектам.
- Повторяющиеся ритуалы: «еженедельный обзор» через `🔁 every week`.
- Привязка к проекту: задачи прямо в заметке проекта, а сводный список — отдельным запросом.

**Синтаксис задачи** (в любой заметке):
```
- [ ] Пример повторяющейся задачи 📅 2026-06-15 🔼 🔁 every week
```
| Эмодзи | Значение |
|---|---|
| 📅 | due (срок) |
| ⏳ | scheduled (запланировано) |
| 🛫 | start |
| 🔁 | recurrence (`🔁 every week`, `🔁 every month`) |
| ✅ | done date (ставится сам при закрытии) |
| 🔺 ⏫ 🔼 🔽 ⏬ | приоритет: highest → high → medium → low → lowest |

**Запрос-блок** (динамический список):
````
```tasks
not done
due before 2026-07-01
sort by due
group by filename
```
````
Фильтры: `done` / `not done`, `due before/after/on`, `path includes projects`, `priority is high`, `tags include #ds`, `happens this week` и др.

**Команды (3):** Create or edit task (модалка-конструктор строки) · Toggle task done · Add all Query File Defaults properties

> Клик по чекбоксу закрывает задачу: ставит ✅ done date и при `🔁` создаёт следующий повтор.

---

> Версии и команды актуальны на 2026-06-01. Поставишь/обновишь плагин — попроси Claude "обнови шпаргалку Obsidian-плагинов" (он перечитает команды из кода). Полный список команд любого плагина: `Cmd+P` + имя плагина.
