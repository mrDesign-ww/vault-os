---
name: nano-banana-pro
description: Use before writing a prompt that generates or edits images or assets with Nano Banana Pro (Gemini 3 Pro Image) via Magnific imagen-nano-banana-2, Higgsfield nano_banana_pro, NezhaGate, Replicate or the Gemini API, in any project. Prompt rules, references, text, canvas, cutout, provider parameters, failure fixes. Not for tasks without image generation.
---

# Nano Banana Pro: prompting and asset rules

Model-level rules for Nano Banana Pro (Gemini 3 Pro Image). They say how the model reads a prompt and how to get a usable asset; they carry no style. Style comes from the project canon (last section).

Read [`reference.md`](reference.md) when you call a provider (exact parameters, prices, limits, quirks) or want a full worked prompt.

## Model names

- Nano Banana Pro = Gemini 3 Pro Image, `gemini-3-pro-image` (GA 2026-05-28). Magnific slug `imagen-nano-banana-2` is **Pro**; Magnific `imagen-nano-banana-2-flash` is Nano Banana 2 (Gemini 3.1 Flash Image). Higgsfield `nano_banana_pro`, NezhaGate `nano-banana-pro`, Replicate `google/nano-banana-pro`.
- Guides from late 2025 sometimes call Pro "Nano Banana 2". Tricks proven on Nano Banana 2.5 Flash (CAPS, markdown lists) are not re-checked on Pro.

## The fence

The prompt text is the only channel to the model. A requirement written in a parameter table, a metadata field, a brief or an acceptance list does not reach it. Before submitting, check that every requirement (backdrop colour, layout, no labels on a sheet, the aspect ratio in words) sits inside the prompt text itself.

## How the model reads a prompt

Pro thinks before it renders (always on, up to two unbilled interim frames), so context and purpose change its choices.

1. **Open with a verb and the purpose.** "Create a ... illustration to be used as ..." Name what the image is for and how it is seen (a small UI tile, a full-bleed hero). Without an explicit "create an image" an ambiguous prompt can come back as text only.
2. **Narrative, not tags.** Full sentences. Without references: subject, action, location, composition, style. With references: the references and their roles, the relationship instruction, the new scenario.
3. **Step order for complex scenes**: "First ... Then ... Finally ..." (setting, hero, then any lettering).
4. **Concrete materials and actions** ("navy blue tweed", "polished warm gold"). Evaluative adjectives (beautiful, premium) do not render.
5. **Camera and light in film and photo language**: low-angle, wide-angle, macro, shallow depth of field, rim light, golden hour backlight, colour grading.
6. **Place key elements with top, middle, bottom, center.** The model card reports left/right confusion.
7. **Name the style explicitly.** Pro defaults to realism, earthy texture and a slightly aged, paper-textured look. A cartoon asset needs the style spelled out ("2D cartoon with thick black outlines, flat cel shading"), or the model upgrades it toward 3D.
8. **Everything you name can be painted.** Any line that names visual material without saying how it is used reads as content:
   - layout words (safe area, caption, strip, band, panel, border, frame) become a drawn panel with a hard seam line;
   - a hex palette line becomes a row of colour swatches with codes;
   - digits, percentages and coordinates in framing rules have been printed onto the art as an overlay;
   - "transparent", "PNG", "sticker", "cutout" bring a checkerboard or a white sticker outline;
   - a title mentioned twice comes out twice.
   Describe the empty area as a continuation of the same scene (what lies there, same light, same focus and detail). Name colours in words tied to objects, or give the palette as a reference image; if a list is unavoidable, write "paint the scene with these colours and never depict them as samples".
9. **Size and framing as measurable relations, not qualities.** "Medium close-up", "small", "tight" are ignored. What works: edge contact ("the ears touch the top edge of the picture"), fractions in words ("the head is wider than half the picture"), and sizes relative to a body part ("the lantern is about the size of its head"). Counts by inference: "two cuts, so exactly three pieces" beats "exactly three pieces". Keep digits and coordinates out (rule 8).
10. **An action needs its object in frame.** If the hand strikes, places or draws, the surface or target stays in the picture as one minimal plain object, with its scale tied to the character. An invisible target makes the model invent trajectory and size. Gestures that end in the air (pointing, looking) are safe.
11. **Negation.** Google advises positive framing ("an empty, deserted street" instead of "no cars"); no provider has a negative prompt field. A short "No text." is fine (Google's own Pro examples use it). A negative that contradicts the scene description loses to the description. A negative list is a guard added after a seen defect, never the main tool.
12. **When a ban fails twice, change the frame, not the wording.** The model completes the most probable state of every object present. Remove the object or change the shape that makes the defect likely (a dry empty honey dipper instead of a loaded one; a straight-edged chip instead of a broken edge).

## Canvas and resolution

- With references, output follows the input size or falls back to 1:1. Always set the aspect ratio parameter and repeat it in words ("A vertical 3:4 illustration").
- Ratios: 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 on every route. 3:4 gives 896x1200 (1K), 1792x2400 (2K), 3584x4800 (4K).
- Generate at **2K** by default: small text is blurry at 1K, and 1K and 2K cost the same on Gemini API, Magnific and Higgsfield. Pass the resolution explicitly every call.
- Measure the downloaded file. Provider metadata lies (Magnific reported 1344x768 for a 2752x1536 file) and NezhaGate resizes non-square output down.
- Crop to the target with a small trim and a downscale (3:4 to 418x564 trims about 0.7% of the width).

## References

Limits (Gemini API): 14 images in total, up to 6 objects in high fidelity (5 in another section of the same page), up to 5 characters, up to 3 style references. Provider limits differ (reference.md).

- **The model learns each reference's role only from the text.** Name roles in attachment order and say what to take and what to leave: "The first image is the style reference: take only its rendering, light and finish, none of its objects or lettering. The second image is the character sheet: keep his face, markings and proportions exactly as shown."
- **A reference pulls more than you ask for**: its composition, camera, pose, size, angle and proportions. Text does not override the pull.
  - Changing proportions, size or angle: drop the reference and describe the object in words.
  - A new pose: give a pose reference already in that pose (a locally rotated copy of the old render worked first try where "tilted 20 degrees" failed).
  - A new camera view of a referenced place: four blocks. STYLE AND SET REFERENCE (it carries style, materials and inventory, not the frame), CAMERA POSITION (height in metres, distance, tilt), IN FRAME, LARGE (the foreground), OUT OF FRAME (objects that are absent; the strongest block, since it makes the old composition impossible). A view orthogonal to the reference (true top-down, plan) works only with no reference at all.
  - When the composition carries a fact (distances, relative positions), use no style reference; describe the look in words and pin the scale by what cannot be resolved from that distance.
- **Words that cancel reference traits break the likeness.** Each trait you reverse in text, the model redraws from text, and text yields a generic character. Pick an input already in the needed state, size and place, so nothing needs cancelling. Keep trait checklists in your acceptance criteria, not in the prompt.
- An unusual character drifts toward its canonical or average look; give a 2-3 view character sheet. More than 5 character references raise hallucinations.
- Series consistency: 1-3 style anchor images plus one fixed STYLE text block reused verbatim.
- A layout sketch as reference can leave its lines in the output ("persistent ink"). Search grounding gives the model text only, never images.

## Text on images

Default for UI and product assets: generate with no text and set type in Figma or code ("There is no writing anywhere in the picture"). When the model must draw text:

- The exact string in quotes, once; the letterform described in words or by font name; the place named ("at the top center"); line breaks in words ("on two lines with the last word on the second line"); "exactly once, correctly spelled"; "This is the only writing anywhere in the picture; <text-prone objects> are plain."
- Give long text verbatim. Left to fill a space, the model fills it with nonsense.
- Keep text-prone surfaces (signs, tickets, labels, banners) plain or out of frame; Pro likes to add a date stamp in a corner. Keep glossy or wet floors away from lettering (a mirrored copy of the title was seen).
- Spelling is never guaranteed: short proper names distort, words duplicate, edges cut letters. Check every text output by OCR for duplicates, cut-offs and spelling.
- Lettering as a separate pass on a flat chroma backdrop, keyed in code, gives full control of placement and letterform but loses light integration with the scene (community practice, not measured on our assets).

## Generate or edit

- Magnific, Higgsfield, NezhaGate and Replicate keep no session: every call is a fresh generation, and reference-based "edits" are not pixel-preserving. True multi-turn editing exists only on the raw Gemini API (pass thought signatures).
- Edit prompt: "Using the provided image, change only <element> to <new element>. Keep everything else exactly the same, preserving the original style, lighting and composition." Describe only the changed object; add no words about the character's look.
- Output-as-input chains are reported to accumulate artifacts after 2-3 passes; restart from a clean prompt instead.
- Use a provider's dedicated tools for spot work (background removal, relight, upscale, outpaint) rather than a regeneration.
- When Higgsfield marks an image "Not eligible" (it cannot be a video source; correlates with near-white, low-contrast frames), re-editing resubmits the same problem: rebuild from a clean prompt without the input image, asking for dark masses and contrast in a block of its own.

## Cutout and matte for UI use

Pro has no transparent background.

1. Generate the object alone on a flat, even, single-colour backdrop with no gradient, floor, shadow, texture or particles, described positively. The backdrop colour contrasts with the object: light grey by default, saturated green for grey, white or silver objects, magenta for green objects. Put the colour in the prompt text (the fence).
2. Remove the background (Magnific `images_remove_background`, 3 credits; Higgsfield `remove_background`).
3. Clean the matte: fill interior holes only where the colour differs from the backdrop (else see-through gaps turn solid), then colour-to-alpha against the flat backdrop with colours unblended from it. This leaves no fringe on any card colour and keeps glow and smoke. When the container has its own glow, skip the colour-to-alpha haze and keep a tight matte.
4. An object meant to be cut by the frame edge: generate it whole and crop or fade it in post. "Cut by the bottom edge" in a prompt is unreliable.
5. For a fixed card layout, generate composed for the card ratio rather than cropping an isolated object arbitrarily.

## Acceptance checklist

Check every output before using it; a failed launch (wrong parameter) is a launch error, not a candidate.

- [ ] File measured: real pixels and aspect ratio match the target.
- [ ] No unrequested writing: letters, date stamp, labels on sheets, swatches, measurement overlay.
- [ ] No drawn panel, hard seam line or blurred second copy where an empty area was asked.
- [ ] Non-square NezhaGate output has no vertical strip seams (around 1/3 and 2/3 width); a seamed output is rejected and re-run, never patched.
- [ ] Backdrop is the requested flat key colour, even, without floor or shadow.
- [ ] Identity and pose match the reference where likeness was required; proportions match where they were changed.
- [ ] Object count and size relations as specified.
- [ ] Requested text spelled right, once, uncut (OCR).
- [ ] Project canon checklist passed.

## Prompt template

```text
Create a <aspect in words, e.g. vertical 3:4> <style noun> of <subject> to be used as <purpose and viewing size>.
[References] The first image is <role>: take <what>, and none of <what to leave>. The second image is <role>: keep <identity keys> exactly as shown.
First, <setting or backdrop, described positively; for a cutout: a flat, even <colour> backdrop with no floor, shadow or texture>.
Then, in the middle of the picture, <hero: who or what, materials, action with its object in frame, pose, camera angle>. <Size by edge contact or body-part relation.>
[Finally, at the top center, the title "<TEXT>" exactly once, correctly spelled, <letterform>. This is the only writing anywhere in the picture; <text-prone objects> are plain.]
Light: <key, rim, depth of field in film language>.
Style: <project STYLE block, verbatim>.
<No text. | There is no writing anywhere in the picture.>
```

Defaults: count 1 (two only when a choice is planned and recorded), resolution 2K explicit, aspect ratio parameter set.

## Project canons

Style comes from the project, not from this skill. Read the project's own art or style guide (the brief names it) before writing the style, subject and acceptance lines. With no guide, the brief sets the style.

Route order (which provider to call) is also set by the project or brief; reference.md lists the providers and their parameters.
