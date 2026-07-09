# Vault OS — image prompts

The landing page (`index.html`) is fully self-contained and renders with **zero** image
files: the hero graph, architecture diagram, icons, wordmark, and favicon are all
hand-authored inline SVG. Nothing below is required for the page to work.

These are **optional raster assets** the owner can generate later (e.g. via Codex) and
drop in. Only the first one is referenced by the page today.

---

## 1. Social share image (Open Graph)  —  RECOMMENDED

**File / where it goes:** `site/assets/og.png`
Already wired up in `index.html` via `<meta property="og:image" content="assets/og.png">`.
Until this file exists, links shared to Slack/X/etc. simply show no preview image; the
page itself is unaffected.

**Target dimensions:** 1200 × 630 px, PNG (or JPG), < 300 KB.

**Prompt:**
> A calm, premium social-share card for a developer tool called "Vault OS". Deep obsidian
> near-black background (#0A0B10) with a soft warm ember glow (amber #F4A659) bleeding in
> from the lower-right and a faint cool periwinkle glow (#93A0DC) from the upper-left.
> Centered: a minimal knowledge-graph constellation — one glowing amber core node linked by
> thin lines to ~10 smaller amber and periwinkle nodes, like an Obsidian graph view, subtle
> and uncluttered. Overlaid headline in a clean monospaced typeface, off-white (#EAE8E1):
> "Give Claude a memory that compounds." Small uppercase tracked eyebrow above it in ember:
> "CLAUDE CODE × OBSIDIAN". Generous negative space, no UI chrome, no logos, no photographic
> texture. Flat, modern, editorial, high contrast. Not busy.

---

## 2. Hero ambient backdrop  —  OPTIONAL UPGRADE

Only if the owner wants a richer hero than the current inline-SVG graph card. The page looks
complete without it; this would sit *behind* the hero as low-opacity atmosphere.

**File / where it goes:** `site/assets/hero-bg.png` (or `.webp`). If added, place it behind
`.hero` as a very-low-opacity `background-image` (≈ 0.15) with the existing content on top,
and mirror it for light mode or gate it to dark mode only.

**Target dimensions:** 2400 × 1400 px, transparent PNG or WebP, < 500 KB.

**Prompt:**
> Abstract ambient background for a dark developer landing page. Obsidian volcanic-glass
> texture: near-black (#0A0B10) with faint layered facets and a subtle golden sheen, like
> polished obsidian stone. Drifting particle field suggesting a knowledge graph — tiny amber
> (#F4A659) and periwinkle (#93A0DC) points connected by whisper-thin lines, densest toward
> the right, fading to empty black on the left for text legibility. Very dark, very quiet,
> low contrast, no focal subject, no text. Designed to sit at 15% opacity behind a headline.

---

## Palette reference (for consistency with the page)

| Token       | Dark        | Light       | Use                          |
|-------------|-------------|-------------|------------------------------|
| bg          | `#0A0B10`   | `#F0F1F4`   | page background              |
| text        | `#EAE8E1`   | `#16181F`   | body / headlines             |
| ember       | `#F4A659`   | `#C2632C`   | primary accent (warm)        |
| ember-text  | `#F6B06A`   | `#A94E1C`   | accent used on text/links    |
| mineral     | `#93A0DC`   | `#4F5EA8`   | secondary accent (cool)      |
