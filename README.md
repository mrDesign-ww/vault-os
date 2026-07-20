# Vault OS

An installable companion system for **Claude Code + Obsidian**. It turns Claude into a partner with persistent memory (a wiki), a curated set of skills and agents, isolated work zones, and a self-improvement loop — the same setup its author uses daily, packaged so a teammate can get it in minutes.

> Working name. The shell contains **no personal data** — no projects, memory, tokens, or client work. Just the configuration and scaffold.

## Install

1. Open **Claude Code**.
2. Say: **“install `https://github.com/mrDesign-ww/vault-os`”**.
3. Answer a short quiz (your name, language, work zones, optional Codex / ECC).

Everything auto-installs: Obsidian, plugins, skills, agents, rules, hooks, the PARA vault, and a launch shortcut. See the [site](site/index.html) for the friendly walkthrough.

Supported: **macOS + Windows**.

## What's inside

- **Curated Claude Code plugins** (~23) + the public [`claude-obsidian`](https://github.com/AgriciDaniel/claude-obsidian) plugin (wiki, save, canvas, lint, and more).
- **Curated skills** (design, dev, review, PM, motion, GSAP, Atlassian) + agents.
- **Isolated zones** — work / personal / clients never mix.
- **Evolution system** — the vault tidies itself (Curator) and learns from friction (reflection).
- **Optional add-ons:** Codex (design/ideas engine, wired to the same vault) and ECC (dev framework for code projects).

## Repository layout

```
vault-os/
├── site/                 # 1-page landing (GitHub Pages)
├── INSTALL.md            # instructions Claude follows on "install"
├── installer/            # quiz + platform install scripts
└── shell/
    ├── claude/           # settings template + 35 curated skills (design, dev, review, PM, motion, GSAP)
    ├── codex/            # Codex config + hooks templates
    └── vault-template/   # CLAUDE/AGENTS templates, PARA scaffold, evolution toolkit (Claude + Codex), .obsidian config
```

## License

MIT — see [LICENSE](LICENSE).
