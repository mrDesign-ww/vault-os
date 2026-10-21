# Design round R<n>: <block>

One file per owner round of edits (`design/R<n>-BRIEF.md`). The builder reports per E-id, the reviewer checks per E-id. Short form for a small polish round: Owner words, the E-list, Keep as is.

## Where
- Figma file, section id, rows or frames to duplicate (or edit in place, if the owner said so).
- Protected ids (owner rows, references, annotations): snapshot with `figma-craft/scripts/untouched.js` before the build.
- Round number on this part (round 3+ needs a method change written here).

## Owner words
Verbatim, as written or spoken, with the date. No paraphrase.

## Reference read at 3x
Only the traits the owner named ("take X from the ref" = only X). One line per trait: what it is, measured where possible.

## Edits
- E1. «<the owner's words>». Reading: <literal reading>. Target: <node id or layer>. Change: <measurable>. Must not change: <neighbours>.
- E2. ...

## Keep as is
Everything the owner did not name, listed where a builder might be tempted (titles, art, spacing, other variants).

## Done when
- Every E-id reported done or blocked, with a render of the target at 1x.
- `untouched.js` diff: 0 changed, 0 missing. `hygiene.js` counts outside decor: 0.
- No unrequested change (the reviewer counts any as MAJOR).
