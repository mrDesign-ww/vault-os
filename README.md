<h1 align="center">🗄️ Vault OS</h1>

<p align="center">
  <em>Persistent memory, curated skills, and a self-improving vault<br>for <b>Claude Code + Obsidian</b> — the setup its author uses daily, packaged for a teammate.</em>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-1f1f1f.svg"></a>
  <img alt="Platform" src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows-4b7bec.svg">
  <img alt="For Claude Code + Obsidian" src="https://img.shields.io/badge/for-Claude%20Code%20%2B%20Obsidian-6f56d9.svg">
  <a href="https://mrdesign-ww.github.io/vault-os/"><img alt="Live walkthrough" src="https://img.shields.io/badge/walkthrough-live-2ecc71.svg"></a>
</p>

---

Claude Code is brilliant in the moment but forgetful between sessions. **Vault OS** gives it a memory that compounds: a PARA-structured Obsidian wiki, a curated toolbelt of skills and agents, isolated work zones that never bleed into each other, and a self-improvement loop that keeps the whole thing tidy. Drop a source, ask a question — the knowledge sticks and grows.

> [!NOTE]
> **The shell ships zero personal data.** No projects, memory, tokens, or client work — only configuration templates and an empty scaffold. Your teammate installs it and fills it with *their* work.

## ✨ Why Vault OS

- **It remembers.** A persistent wiki becomes Claude's long-term memory across every session.
- **It comes loaded.** ~23 curated plugins and 35 skills for design, dev, review, PM, and motion — wired in on install.
- **It stays clean.** Work / personal / client zones are fully isolated and never cross-link.
- **It improves itself.** An evolution loop tidies the vault (Curator) and learns from friction (reflection) — with a human in the loop for every destructive step.
- **Two brains, one vault.** Optional Codex add-on shares the same memory, skills, and hooks as the Claude side.

## 🚀 Install

1. Open **Claude Code**.
2. Say: **“install `https://github.com/mrDesign-ww/vault-os`”**.
3. Answer a short quiz — your name, language, work zones, and optional Codex / ECC.

Everything auto-installs: Obsidian, plugins, skills, agents, rules, hooks, the PARA vault, and a launch shortcut. Prefer a friendly tour first? **[See the walkthrough →](https://mrdesign-ww.github.io/vault-os/)**

Supported: **macOS** and **Windows**.

## 📦 What's inside

| Layer | What you get |
| --- | --- |
| **Plugins** | ~23 curated Claude Code plugins + the public [`claude-obsidian`](https://github.com/AgriciDaniel/claude-obsidian) plugin (wiki, save, canvas, lint, and more) |
| **Skills** | 35 curated skills — design, dev, review, PM, motion, GSAP, Atlassian — plus agents |
| **Zones** | Isolated work / personal / client spaces that never mix |
| **Evolution** | A self-tidying vault that learns from friction — Claude `/evolution`, Codex `$evolution` |
| **Codex** *(optional)* | A design/ideas engine wired to the same vault: config, hooks, and skill parity |
| **ECC** *(optional)* | A dev framework for code projects — project-scoped, never global |

## 🧠 How it works

- **Persistent wiki-memory.** A PARA vault (Projects / Areas / Resources / Archives) with an index, an append-only log, and a hot-context cache that reloads on session start.
- **Isolated zones.** Each zone owns its own index, log, and hot cache. Content in one never links to or lists another.
- **Self-improvement loop.** `.vault-meta/evolution/` runs a Curator over memory, a zone-aware wiki lint/fold, a retrieval refresh, and a reflection pass over session traces — surfacing suggestions, never silently rewriting.
- **Claude + Codex parity.** Codex reads the same vault as durable memory. Its hooks live in a standalone `~/.codex/hooks.json` (distinct from Claude's `settings.json` hooks), and the evolution toolkit runs on both with `--platform` flags.

## 🗂️ Repository layout

```
vault-os/
├── site/                 # 1-page landing (GitHub Pages)
├── INSTALL.md            # instructions Claude follows on "install"
├── installer/            # quiz + platform install scripts
└── shell/
    ├── claude/           # settings template + 35 curated skills
    ├── codex/            # Codex config + hooks templates
    └── vault-template/   # CLAUDE/AGENTS templates, PARA scaffold,
                          # evolution toolkit (Claude + Codex), .obsidian config
```

## 🔒 Privacy by design

Templates carry placeholders, not secrets. The installer **backs up** every file before it merges, and never touches `auth.json`, `settings.local.json`, API keys, or an existing vault without asking. What you clone here is a scaffold — your data stays yours, on your machine.

## 📄 License

[MIT](LICENSE) © 2026 Vladyslav Chumak
