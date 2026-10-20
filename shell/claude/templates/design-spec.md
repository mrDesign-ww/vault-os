# Design spec: <screen or block>

For UI, web and DS work (code features use `feature-spec.md`). Filled in before the first build. Builders execute it; reviewers check against it, not against taste.

## Goal and user task
Who uses it, the task, the next action. One or two sentences.

## States x breakpoints
| State | 1440 | 768 | 390 | 320 |
|---|---|---|---|---|
| default, hover, pressed, active, disabled, empty, loading, error (those that apply) | node or sketch | | | |

## Components
Every control and repeated piece: library component name and key (or "new local component" with the reason). Tokens bound by name; new tokens only on an owner command.

## Layer tree
The target Figma tree of one card or row: auto layout direction, gap, padding, sizing (hug, fill, fixed) per frame; decor layers named `art /`, `decor /`, `fx /`, `bg /`.

## Content
Real products, items and images with their source. No invented content.

## UX bar
One first look; one accent per card; 3-4 cards visible in a row; equal paddings; visible hover; the block's own styling.

## Art slots
Size, position and role of each art slot; the art follows the project's own art style guide.

## Motion
Durations, easings, what moves on hover and reveal, reduced-motion behaviour (follow the project's motion notes, if it has them).

## OWNER-DECISIONS
- YYYY-MM-DD: <decision>. Reviewers never report these as problems.

## Open questions
Each with the designer's default.

## Out of scope
