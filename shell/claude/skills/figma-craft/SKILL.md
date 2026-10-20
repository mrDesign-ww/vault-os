---
name: figma-craft
description: Use before building, editing or auditing frames in Figma (use_figma writes, rebuilds, adaptives, design system components), any project. Holds the owner's craft bar plus two read-only gate scripts, hygiene and untouched, to run before reporting.
---

# figma-craft

Rejections this prevents: elements made as bare rectangles and frames instead of auto layout; hand-drawn buttons when the design system has them; collection cards assembled from frames and rectangles.

The bar every frame meets, whether you build it or specify it:
- Work on a duplicate unless the brief says otherwise; the owner's frames, notes and annotations stay as they are. After creating a top-level frame, read back `parent.id` against the target section.
- Auto layout for every structure that flows: rows, columns, cards, chips, buttons, headers, lists, grids. Absolute positioning (`layoutPositioning = "ABSOLUTE"` inside an auto layout parent) only for decor, background art, glows, overlays, badges straddling an edge and motion ghosts, each named for its role.
- Sizing: hug for content, fill for stretching children, fixed only for a deliberate size; constraints set so the frame survives a resize. Adaptives reuse the desktop structure.
- Components: controls and repeated pieces are library instances (record your project's button and icon component keys in its design context page), kept attached; edits go to main components only. Local components only for pieces repeated across frames, kept in a Components frame.
- Tokens: bind existing variables; new variables, collections and styles only on an explicit owner command.
- Geometry: 4 px grid; nested radii outer = inner + padding; one text style per role.
- Hygiene: role names for every layer (generic `Frame 123`, `Group`, `Rectangle 666` and export filenames get renamed), hidden leftovers deleted, layer order background, content, decor.
- Load the `figma:figma-use` skill before the first `use_figma`; confirm the active Figma account with `whoami`; the Figma MCP allows about 10 calls a minute shared by all agents, so batch.

## Before drawing anything
Before any control or repeated piece: run `search_design_system` and inspect one sibling block of the same product; reuse its instances. Never hand-draw what the system has.

## Visible rows are drafts
Every new row the owner can see gets a row note «draft · in review» (text node next to the row label). Only the orchestrator removes it, after an owner-ready verdict. If the board allows, build in a `WIP` section beside the target and move the row in after review.

## Before reporting (gates)
1. Before the build: run `scripts/untouched.js` in `snapshot` mode on the owner's rows, refs and annotations (`PROTECTED_IDS`); keep the JSON.
2. After the build: run it in `diff` mode with that JSON as `BASELINE`. Anything changed or missing: restore it or report it.
3. Run `scripts/hygiene.js` on every frame you created (`ROOT_IDS`, plus `TARGET_SECTION_ID` for top-level frames).
4. Paste both result counts under Verified. Nonzero hygiene counts outside decor = not done.

Hygiene buckets: layoutMode NONE frames with more than one child, GROUPs, bare RECTANGLEs, non-decor ABSOLUTE children, generic names (`Frame 12`), button/arrow/chip/tab-like frames that are not instances, frames named like a component in the file (detached copies), hidden nodes, decor nodes that hold content, roots outside the target section. Decor is exempt by exact name only (`icon button` is not decor): prefixes `art /`, `decor /`, `fx /`, `bg /`; names `backdrop`, `scrim`, `fade`, `glow`, `ring`, `border`, `plate`, `icon`, `art`, `band`. A decor node skips only the structural checks (and so does a pure-decor subtree); one that holds TEXT or an INSTANCE counts in `decorWithContent` and its children get every check. Hidden, generic names, button-like names and detached copies are checked everywhere. Name decor layers accordingly instead of arguing with the gate.

## Running a script
Read the file, set the CONFIG block at its top, pass the whole code to `use_figma` (top-level await and `return` are fine; load `figma:figma-use` first). Both scripts are read-only: no property writes, no create, no remove.
