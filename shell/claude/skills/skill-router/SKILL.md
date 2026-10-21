---
name: skill-router
description: Design skill router. Load first for any design, art, motion, deck or video task, before other design skills: UI/UX screens and blocks, Figma, HTML/CSS layout, banners and image generation, design system work, decks, promo video. Names the 1-3 skills to load and the ones to skip.
---

# skill-router

Picks the skills for one design-family job. The look comes from the brief, the design system and the project's art or style guide; skills add method on top. Done = the job's `Skills:` line is written (orchestrator) or the named skills are loaded in order (agent), then the work starts.

When the brief already has a `Skills:` line, load exactly those and close this skill.

## 1. Name the context

| Context | What sets the look |
|---|---|
| A product with a design system: site screens, page blocks, banners, cards, modals, emails | the product's Figma components and tokens, plus its art guide and motion notes (the project's design context page names them) |
| Design system work: components, variants, variables, themes | the component library file and its audit; new tokens only on the owner's command |
| Internal tool UI: back offices, dashboards, boards | the tool's existing UI (impeccable Operate mode) |
| Greenfield page with no brand canon: pitch site, research page, new tool landing. A page of a product that has a design system is never greenfield, even in a Figma file with no library linked | one taste voice for the page |
| Deck | one idea per slide, clean structure, no card overload |
| Promo video or film | the video project's own rules |

A project with its own rules for a different medium keeps its own routing; this router covers web and product UI.

## 2. Pick the skills

Load in the order shown, at most 3 per job (`figma:figma-use` plus its companions count as one Figma set).

| Job | Load, in order | Notes |
|---|---|---|
| UX spec, screen or block design | `ui-ux-pro-max`, `impeccable` | ui-ux-pro-max as a checklist: Quick Reference §1, §2, §5, §8, §9. On a product with its own DS its style and palette generator stays unused. impeccable: `shape` while planning, `critique` on the sketch |
| Open direction, N structures | `design-council:design-council` | `superpowers:brainstorming` only while the direction is open |
| Figma build, fix or audit | `figma:figma-use`, `figma-craft`, `figma:figma-generate-design` | generate-design for a page or multi-section screen (it runs `search_design_system` first). Components, variables, modes: `figma:figma-generate-library` in its place |
| Figma: new file, diagram, FigJam, Slides | `figma:figma-create-new-file` / `figma:figma-generate-diagram` / `figma:figma-use-figjam` / `figma:figma-use-slides` | each next to `figma:figma-use` |
| HTML mockup into Figma | `figma:figma-code-workflows` | when editable layers or a Code Layer is asked |
| Layout code from Figma | `figma:figma-design-to-code`, `impeccable` | design-to-code before `get_design_context`; one impeccable `audit` then `polish` pass at the end; the code matches Figma 1:1 |
| Internal tool UI code | `impeccable` | Operate mode; `dataviz` for charts and dashboards |
| Greenfield page | `impeccable` | new-work. An Artifact page: Artifact quickstart (or `artifact-design`) with `frontend-design:frontend-design` |
| UI motion in code | `emil-design-eng` | GSAP code: `gsap-core`, plus `gsap-timeline`, `gsap-scrolltrigger` or `gsap-react` as used. Motion from a Figma node: `figma:figma-implement-motion`. Drag, sheets, springs, gestures: `apple-design`. Product motion values: the project's motion notes. hyperframes is for rendered video only |
| Motion in Figma | `figma:figma-use`, `figma:figma-use-motion` | |
| Motion review or audit | `review-animations` | whole-app motion audit: `improve-animations`; where motion is missing: `find-animation-opportunities`; naming a described effect: `animation-vocabulary` |
| Colour, palette, contrast, token maths | `color-expert` | |
| Style profile from references, theme from brand assets | `design-dna` | |
| Image generation, any model | `nano-banana-pro` | before the first prompt; product art follows the project's art guide sections the brief names. `higgsfield-generate` (vault sessions only) when the brief names Higgsfield |
| Promo video | `hyperframes` | the entry names its sub-skills. Remotion project: `remotion`. Generated clips: `seedance-20` or `higgsfield-generate`, per brief |
| Deck | Artifact quickstart, intent `slides` | a .pptx file: `anthropic-skills:pptx`; Figma Slides: `figma:figma-use-slides` |
| Design review, assembled screens | `figma:figma-use`, `figma-craft` | gate scripts for Figma; layout code: `impeccable` `critique` and `audit`; motion: `review-animations` |
| Accessibility of a built page | `chrome-devtools-mcp:a11y-debugging` | |

## 3. One taste voice

Taste skills carry their own fonts, palettes and bans: `design-taste-frontend`, `high-end-visual-design`, `minimalist-ui`, `industrial-brutalist-ui`, `gpt-taste`, `stitch-design-taste`, `redesign-existing-projects`, `frontend-design:frontend-design`. They fit a greenfield page only, one at a time, when the brief names it. On a product with a design system, on Figma work and on internal tools the DS and the canon set the look: a taste skill's bans (fonts, glow, gradients) would fight the brand.

Off-domain for this work, reached only by exact name: `image-to-code`, `imagegen-frontend-web`, `imagegen-frontend-mobile`, `brandkit`, `higgsfield-product-photoshoot`, `higgsfield-marketplace-cards`, `higgsfield-websites`, `motion-design` (Lottie only).

## 4. Rules

- Heavy skills by section: `ui-ux-pro-max` Quick Reference only; `design-taste-frontend` only on a greenfield page that names it.
- Name-only skills (no description in the listing) stay callable by exact name.
- Agent types without the Skill tool (look-reviewer, checker, worker, runner) Read `~/.claude/skills/<name>/SKILL.md` when the brief names a user skill.
- Vault project skills (`higgsfield-*`, `save`, `wiki-*`, `evolution`) exist only in sessions started in the vault.

## 5. The brief line (orchestrator)

Every design, art, motion, deck or video brief carries one line per agent job:

`Skills: load <a>, then <b>. The look comes from <DS file, canon page or style kit>.`

Name skills per job from section 2, never a pile for the whole project.
