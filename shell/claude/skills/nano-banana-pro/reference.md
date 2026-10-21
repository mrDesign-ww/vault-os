# Nano Banana Pro: provider reference and worked prompts

Disclosed reference for [`SKILL.md`](SKILL.md). Read the provider section before a call to that provider; read the worked prompts when building a new prompt from scratch. Facts dated where they were measured; check the provider's own schema when a value matters.

## Route order

Set by the project or brief. A sensible default: the cheapest provider that meets the quality bar for the asset type (see the NezhaGate quality note), an official route as fallback with a daily cap, and paid premium routes only when the brief allows them.

- Check the price before a paid call and the balance before a batch; log the balance after spending.

## Magnific (`images_generate`)

| Field | Value |
| --- | --- |
| model | `mode: "imagen-nano-banana-2"` (this is Pro; premium; expected time about 56 s) |
| aspect | `aspectRatio`; default `1:1` ("edits may snap"); `1:1, 21:9, 16:9, 9:16, 2:3, 3:4, 1:2, 2:1, 5:4, 4:5, 3:2, 4:3` |
| resolution | `1k`, `2k`, `4k` (lowercase) |
| references | `references` up to 12, `{type, identifier}`, types `image`, `style`, `character`, `product`, `locations`; how the type reaches Gemini is unknown, so write roles in the prompt anyway |
| seed | `seed` field exists; reproducibility for Pro not verified |
| count | 1..8 |
| negative, system prompt, temperature | none; `images_models_settings` reports no extra settings |
| transparency | none for this model; chain `images_remove_background` (3 credits) |
| price (3:4, 2026-09-23) | 75 credits at 1k and 2k, 150 at 4k; references do not change the price |
| file size | metadata wrong: a 16:9 2k file measured 2752x1536 while `creations_get` said 1344x768 |
| edits | `images_generate` with references is not pixel-preserving; spot tools exist (relight, retouch, expand, upscale) |

Magnific's own guidance: use Pro over Nano Banana 2 when maximum fidelity or brand accuracy on a final asset is needed.

## Higgsfield (`generate_image`)

| Field | Value |
| --- | --- |
| model | `nano_banana_pro` |
| aspect | `1:1, 3:2, 2:3, 4:3, 3:4, 4:5, 5:4, 9:16, 16:9, 21:9` |
| resolution | `1k`, `2k`, `4k`; the schema says default `2k`, but a call without it returned 1k (2026-07-28): always pass it |
| references | `medias` with role `image_references`; maximum not declared |
| seed, negative prompt | none |
| count | 1..4; use 1 by default (duplicate pairs waste credits) |
| price | 2 credits at 1k and 2k, 4 at 4k |
| statuses | jobs can end as `nsfw` or `ip_detected`: avoid real public people, trademarks and brand characters |

Higgsfield facts:

- Elements (saved references) work only through MCP (`show_reference_elements` list/get/create; create needs `media_upload`, PUT bytes, `media_confirm`), inserted as `<<<element_id>>>` in the prompt. The CLI rejects them. Models listed as supporting Elements (2026-07-22): `nano_banana_2`, `gpt_image_2`, `seedream_v4_5`, `seedance_2_0`, `kling3_0`, Cinema Studio; check before relying on Elements with `nano_banana_pro`.
- An Element is an image reference: it carries its source photo's composition, and text bans do not override it.
- Media ids, Elements and job ids belong to one account. After an account switch they fail with `Media input not found`: re-upload and recreate.
- Plans with unlimited web use do not extend it to MCP (`unlim.available: false`); MCP spends credits. Unlimited does not cover 4k.
- The concurrency limit can be counted separately for unlimited jobs (one job including waiting ones) and for credit jobs (paid jobs go through in small batches). A job whose status call fails with `Something went wrong` while `balance` answers is dead: resubmit.
- "Not eligible" on an image means it cannot be a video source, not a moderation refusal; the image remains a usable asset. It correlated with near-white, low-contrast frames.

## NezhaGate (OpenAI-compatible gateway)

- Base `https://nezhagate.com/v1`, Bearer key from your OS keychain or a secrets manager; never print the key.
- Async: `POST /v1/images/generations` with `model: "nano-banana-pro"`, `prompt`, `n`, `size` returns 202 and a job id; poll `GET /v1/images/jobs/{id}` until `succeeded`; result at `data[0].url`. One Pro job at 2K or 4K takes 30-54 s. Submit in parallel (up to 50 concurrent), then poll together.
- References: JSON `image` or `images` (up to 8; URL, data URI or base64) on `/v1/images/generations`. Multipart and chat routes do not work.
- Price (2026-10-11): about $0.02 / $0.03 / $0.05 at 1K / 2K / 4K. The gateway does not refuse at zero balance (it went negative): check that the balance covers the whole batch first (`GET /v1/usage`).
- Quality (14-case evaluation 2026-10-11): equal to Higgsfield on square assets and lettering, and equal to official Replicate Pro on 6 probes. Weak on non-square: 27-44% fewer pixels and vertical strip seams on smooth gradients (around 1/3 and 2/3 width), coming and going with the supplier route. Reject seamed outputs and re-run; never patch seams.
- Good for chroma-key sheets (icons, UI, VFX, sprites, character sheets), 4K backgrounds and plates, lettering drafts, bulk variations. Prefer Higgsfield for hero art that must keep one exact object from 3+ references, outputs over 4096 px, references over 16 MB.
- Output metadata is stripped, including content credentials; SynthID is the only origin mark.

## Replicate

- Official `google/nano-banana-pro` through the Replicate MCP with `allow_fallback_model: false`. About $0.15 per 1K or 2K image, $0.30 at 4K (2026-10-11).

## Raw Gemini API

| Field | Value |
| --- | --- |
| model | `gemini-3-pro-image` (earlier `gemini-3-pro-image-preview`) |
| canvas | generateContent: `generationConfig.imageConfig.{aspectRatio, imageSize}`; Interactions API: `response_format: {type: "image", aspect_ratio, image_size}` |
| size | `1K`, `2K`, `4K` uppercase; lowercase `1k` is rejected |
| references | 14 total (see SKILL.md); Vertex: 7 MB per inline file, png, jpeg, webp, heic, heif |
| thinking | always on, not configurable for Pro (`thinking_level` exists only on 3.1 Flash models) |
| system instructions | supported (no other route exposes them) |
| sampling | temperature default 1.0, topP 0.95; a generic `seed` exists but is not documented for images |
| editing | multi-turn is the recommended way to iterate; pass thought signatures |
| text first | Google: Gemini works best if you first generate the text, then ask for an image with that text |
| price | $0.134 per 1K or 2K, $0.24 per 4K; Batch and Flex $0.067 and $0.12; no free tier |
| safety | refusals come back as text with `FinishReason` STOP, `BlockedReason` on the prompt, `IMAGE_SAFETY` and similar on the output; filters include Celebrity, Child, People/Face. Every image carries SynthID and C2PA |
| knowledge | world knowledge up to January 2025 |

Best-supported prompt languages include EN, RU and UA.

## Not Nano Banana: Codex CLI image_gen

Codex CLI (0.155.1+, model `gpt-6-astra`) has a native `image_gen` (gpt-image): 9:16 comes out 941x1672 PNG, it accepts reference images (`referenced_image_paths`). Consumer plans hit a rate limit (`429 usage_limit_reached`) after a few images per hour. A useful concept-sketch alternative, not a Nano Banana route; these rules are not verified on it.

## Worked prompt: isolated object for cutout

```text
Create a single mascot illustration to be used as the central element of an app onboarding card. It will be cut out and placed on a dark card, so it has to read as a bold, clear silhouette even at thumbnail size.

The attached image is the product's own artwork. Keep this character recognisably the same: <identity key 1>; <identity key 2>; <identity key 3>. Keep the product's own art style: <style words>. Redraw it as a new, clean, high-detail illustration and take nothing from the reference's background, frame, logo or lettering.

Subject: <description, pose, expression>.

Composition: only this one character, in the middle of the picture, facing the viewer. <framing sentence: bust | waist up | whole object | group>. There is a little empty space above the top of the head, and nothing touches the sides of the picture.

Light: soft key light from the front and above, crisp highlights, and a bright rim light along the whole silhouette.

Background: a plain, flat, even light grey backdrop with no gradient, floor, shadow, texture or particles.

There is no writing anywhere in the picture.
```

Parameters: aspect 3:4, resolution 2K, count 1, the product's own artwork as the reference. Use this as a structure; a project keeps its own versioned template.

## Worked prompt: full scene with a title (research example)

```text
Create a vertical 3:4 illustration to be used as the poster of a lantern festival. It is shown as a small tile in an events app, so it needs one clear hero, a bold silhouette and a title that reads at thumbnail size.

First, paint the setting: a riverside old town at dusk under a deep violet sky. Rows of round paper lanterns in warm amber and soft red hang across a narrow street of wooden houses, a stone bridge arches over the dark river behind, and dozens of floating lanterns glow softly out of focus on the water in the distance.

Then, in the middle of the picture, place the hero: a cheerful red panda standing upright, seen from a slightly low angle from the knees up, smiling with its eyes half closed. It wears a short indigo festival jacket with a white wave pattern and holds a round paper lantern up in both paws. Its fur is thick and soft, rust red with a white face mask and dark brown legs.

Below it, the same street continues all the way down to the bottom edge of the picture: old cobblestones scattered with a few fallen paper petals, lit by the same lanterns and painted with the same style, focus and detail as the rest of the scene.

Finally, at the top center, in the open sky above the hero's head, place the title "LANTERN NIGHTS" exactly once, correctly spelled, on two lines with the last word on the second line. The letters are chunky rounded serif capitals of warm cream with a thick deep red outline and a soft warm glow, slightly arched, with clear sky around them on every side. This title is the only writing anywhere in the picture; the lanterns and the houses are plain.

Style: a polished stylized 3D cartoon render in the look of premium animated film key art, clean and freshly painted, crisp edges, rich saturated color. Warm amber light from the lanterns against the cool violet dusk, a thin warm rim light along the red panda's fur, shallow depth of field that turns the distant lanterns into soft bokeh. The whole picture is one continuous scene in one light.
```

What it deliberately avoids: the title anywhere outside the TITLE sentence; layout words, digits, percentages, hex; glossy floors, puddles or mirrors under the title (the cobblestones are "old", not "wet"); left and right; a "DO NOT DRAW" list; sign-like objects at the bottom.

With references, insert after the first paragraph:

```text
Two reference images are attached. The first image is the house style reference: take only its rendering style, lighting and finish from it, and none of its objects or lettering. The second image is the red panda character sheet: keep its face, fur markings, jacket and body proportions exactly as shown.
```

## Worked fixes

- New camera view of an approved location (blocks from SKILL.md):

```text
STYLE AND SET REFERENCE: the attached image sets the style, materials and inventory of the place, not the frame.
CAMERA POSITION: <height in metres>, <distance to the subject>, <tilt>.
IN FRAME, LARGE: <what fills the foreground>.
OUT OF FRAME: <sky, forest, lawn: every object that would recreate the old composition>.
```

- Banned state that keeps appearing: honey kept pouring through four "no drip" variants until the dipper was made dry and empty; a broken edge kept looking bitten until it became a straight-edged rectangular chip. A sandwich cut across into thirds kept reading as two sandwiches until it became a square with two parallel cuts.
- Size that will not land: "small crunchy edge" failed five times; "an edge two cat paws long and smaller than his head" landed first try.
- Hand action without a target: a judge's gavel with table and stand banned grew into a sledgehammer; keeping one plain stand and stating "the gavel head is about the size of his own paw" fixes it.

## Open questions (unverified, do not rely on)

- Magnific `seed` reproducibility on Pro; real 3:4 pixels on Magnific and Higgsfield.
- Whether a long "DO NOT DRAW" list beats a positive prompt; "No other text." versus "the only writing anywhere".
- Spelling a word letter by letter (G-O-L-D-E-N) to fix a lost letter.
- Prompt length limits (a 200-250 word ceiling claim comes from a weak source; long structured prompts worked for others).
- Whether GA `gemini-3-pro-image` behaves like the preview most practice was written on.
