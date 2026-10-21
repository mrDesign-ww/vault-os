# Global rules for {{OWNER_NAME}}

Installed by Vault OS. Edit freely: this file is yours after install.

# graphify
- **graphify** (`~/.claude/skills/graphify/SKILL.md`): any input to a knowledge graph. Trigger: `/graphify`. When the owner types `/graphify`, use the installed graphify skill before doing anything else.

# Language
- Everything the owner reads is in {{LANGUAGE}}: replies, final reports, decision questions and pages, wiki pages.
- Memory notes and every doc only agents read are in English to save tokens; keep the owner's quotes verbatim.
- Everything only agents read is in English to save tokens: agent briefs, agent reports to the orchestrator, handoff files, specs written for agents, agent type prompts. The orchestrator relays results to the owner in {{LANGUAGE}}.
- Keep the owner's own trigger phrases and quotes verbatim.

# Subagents and models
- The agent type sets the model and effort: pick a type, not a model. Fable is excluded everywhere. Routing, review, brief and long-work rules for the orchestrator: `~/.claude/docs/orchestration.md` (injected into main sessions at start by a hook).

| Tier | Work | Types |
|---|---|---|
| Haiku | mechanical one-shot work by exact instruction: file conversion, status checks, code location, running a ready script or test and reporting, appending to state files; intermediate visual checks of parts (missing parts, artifacts, obvious concept mismatch), escalating to Sonnet when unsure | `runner`, `checker`, `locator` |
| Sonnet | implementation from a spec or a fix list, applying review fixes, part builds and fix rounds by an existing method, captures, renders, deploys, audits, inventories, doc edits; look and likeness review of single objects and parts | `builder`, `worker`, `look-reviewer` |
| Opus | architecture; money and security code; art direction: one review per assembled composition plus the final check before the owner; batch review of Sonnet code; research synthesis; hard debugging and new methods (`architect` only for architecture, money and security, a new method, or after `builder` failed the same fix twice); concepts and design | `architect`, `reviewer`, `product-designer`, `general-purpose` |

- A project may keep its own agent types in its `.claude/agents/`; they load only in sessions started there. System improvements (hooks, tiers, review rules) apply to every project; agents on one project take no content (files, memory, specs, patterns) from another.
- Effort: xhigh for architecture, economics, global reviews (compositions, code batches) and design agents (`product-designer`); high for other Opus work; medium for Sonnet; low for Haiku.
- Fix rounds: at most two per part; every fix brief states the round on that part; after round 2 the reviewer writes why two fixes did not close it, and the next round uses a different method.
- Rules for every agent:
  - never wait on a render or long job: submit, exit; a fresh agent picks up the result;
  - past about 300k tokens of context, write a handoff note; a fresh agent continues;
  - never change test fixtures or thresholds to make a test pass; report instead;
  - an action repeated 3+ times with fixed steps becomes a script (the project's tools folder, else `~/.claude/scripts/`; Figma gates: the `figma-craft` skill);
  - read guides by section, not whole.
- An agent writes to the result file its brief names, else replies; a reply to the orchestrator is at most 15 lines.
- Waiting: `run_in_background` plus the notification, or Monitor. No `sleep` loops; one `sleep` at most 270 s. Agents never wait for each other's files; the orchestrator sequences work.

# superpowers plugin
- Its SessionStart text says "check skills first". Where it differs from this file, this file wins (the plugin itself puts user instructions first).
- Use its skills silently: go straight to the work, no "Using X" line; a todo list only for a task with several real steps.
- Scope: systematic-debugging for every bug or failing test; verification-before-completion before saying code, config or a fix works; test-driven-development for code that has a test suite (prototypes, config, visuals and generated assets go without it); brainstorming and writing-plans while a feature or direction is still open. Once the owner has set the direction, do the work; a question that needs the owner's decision goes to the owner's decision channel (see Transparency).
- Plans live in the project's STATE.md or docs folder. Execute them inline or through the agent types by the tier table; the final review follows the code review rule in `~/.claude/docs/orchestration.md`.
- Visual and creative quality is judged by the reviewers and the owner, not by verification scripts.
- Not used, because the tier table and batch review replace them: subagent-driven-development, requesting-code-review, using-git-worktrees, finishing-a-development-branch (skillOverrides turn them off; if a harness still lists them, do not use them).

# Economical reading (all agents)
- Send independent reads, searches and commands in one turn, in parallel.
- Grep or Glob first, then Read the needed parts with offset and limit. Do not read large files whole.
- Downscale screenshots for general review before reading (`sips -Z 1280` on macOS). Full size only for comparing with Figma and fine details.
- Research reports are archive, not working instructions. Every research ends with a distil step: its conclusions go into the discipline guide (the project's `docs/<DISCIPLINE>.md`, for example `docs/VFX.md`) as rules, recipes and numbers, and the report gets a header "distilled into <guide>, <date>". Agent definitions and briefs point to the guide and the kit SKILL files, never to whole reports; a report section is linked only when an agent needs the reasoning behind a rule.
- Research on a general tool or model that any project may use (an image model such as Nano Banana Pro, a video tool, the Figma API) is distilled into an on-demand user skill `~/.claude/skills/<topic>/SKILL.md`, not into CLAUDE.md, memory or a project guide: only its one-line description stays in context, the body loads only when a task uses that tool. The vault page stays the human-readable version and names the skill; project style stays in the project's own guide. Image generation with Nano Banana Pro: skill `nano-banana-pro`.

# Transparency for the owner
- Every question and every review for the owner goes to the owner's decision channel, one item per question: options, a recommendation, room for the owner's answer. When the project has a decision channel configured, use it and say in chat only one line that a question waits; without one, ask in chat in one line and wait for the answer.
- Every new question for the owner also gets a phone push: call `PushNotification` with "New question: <title>".
- After implementing an owner answer, mark it applied with the time and a one-line note where the answer lives.
- Results the owner can look at or listen to now (finished or intermediate: images, sounds, animations, effects) are shown as review items, not questions. Mark one "ready" only after the owner-gate check (`reviewer` in design review mode), else "intermediate" with one line on what is raw. Never stand-ins or clips with a known bug.
- Report status at milestones without being asked: when agents start, one line with the phases and when the next result lands; one line at each phase end (research, build, review, ready); in a live session never more than 20 minutes of silence.
- Write reports in plain language, no internal jargon, with a "where to look" link.
- Say "done" for visuals only after checking on a deployed preview and in iPhone (390) and 320 viewports.

# Design and Figma
- When a project names a design-context page (or a trigger phrase for it, such as "design context"), re-read that page first before design work; if the owner forgot to name it before a design task, remind them.
- Product art (banners, page blocks, cards, modals, emails) follows the project's art style guide, by section; every art brief links it, nobody researches the art style anew, and a new rule the owner accepts is written back into the guide as part of done. Maker: `builder` with those guide sections; check per art family: `look-reviewer` against the guide's acceptance checklist.
- Every design, art, motion, deck or video brief carries a `Skills:` line picked with the `skill-router` skill; agents load the named skills, at most 3.
- Make design system edits only in the main component or component set, never in instances.
- Create new tokens, variable collections and styles only on an explicit command. Binding existing variables is allowed.

# Other
- Watch videos for real: via claude-video-vision if enabled, otherwise yt-dlp + ffmpeg (frames) + whisper-cli (audio). Never guess from the title.
- Where things live: code projects in `~/dev`, knowledge in the Obsidian vault, system scripts in `~/.claude/scripts/`, temporary files in the scratchpad.

# Compact instructions
- Auto-compact runs at 300k tokens (`autoCompactWindow`); a PreCompact hook saves a small snapshot (the owner's last messages, running agents, state files) and SessionStart(compact) injects it.
- Summary keeps: the owner's open requests and latest quotes verbatim, decisions taken, running agent ids, exact file paths of state and results, next steps. Drop tool output, file dumps and finished detail (they stay on disk and in the transcript).
- After a compact never re-read state files or the transcript whole: continue the task, read only the section or grep hit the next step needs.
