# Install quiz

Ask these via **AskUserQuestion**. Keep it short and friendly; explain jargon in one line.

## Q1 — Name
"What should I call you?" — free text. Fills `{{OWNER_NAME}}` in CLAUDE.md / AGENTS.md.

## Q2 — Language
"What language are you most comfortable working in?" (English, Русский, etc.). Sets `language` in `~/.claude/settings.json` and the wording of zone switch-phrases.

## Q3 — Zones
Explain first: "Zones are **isolated work contexts** — their notes never mix and never cross-link. Everyone gets a **Work** zone. You can add more (e.g. **Personal**, or a **client's name**) to keep things cleanly separated."
Ask: how many extra zones and what to name them (free-form). Default: just Work.

## Q4 — Codex
"Install the **Codex** integration? It's a design/ideas engine wired to the same vault. Needs the Codex desktop app + your own OpenAI login." — Yes / No.

## Q5 — ECC
"Install **ECC**? It's a development framework (27 agents + skills for code projects: planning, TDD, review, security). It installs into a **code project**, not the vault." — Yes / No.
If Yes: "Path to the code project to install it into?" (if they have none yet, skip and print the install command for later).

After the quiz, proceed through `INSTALL.md` steps 3–11 using these answers. Zone + rules generation detail is in `steps.md`.
