---
name: design-dna
description: "Extract and apply evidence-tagged Design DNA from screenshots, images, URLs, Figma context, or existing UI code across tokens, qualitative style, and visual effects. Use when the user explicitly asks for Design DNA, a structured design profile, tokens/style/effects from references, reference-to-JSON analysis, or implementation from an existing DNA JSON. Do not trigger for ordinary UI critique or routine component implementation."
---

# Design DNA

Build a machine-readable design profile without presenting guesses as measured facts. Treat existing project tokens, Figma variables, approved specs, and vault decisions as sources of truth.

## Source priority

Use the strongest available evidence in this order:

1. Existing approved project tokens, Figma variables, and implementation constants.
2. Inspected source code and computed styles.
3. Pixel measurements or color sampling from supplied assets.
4. Visual inference from screenshots or video.
5. Qualitative interpretation.

Never overwrite a stronger source with a weaker inference. When sources conflict, report the conflict and ask which source governs.

## Evidence contract

Read [references/schema.md](references/schema.md) before extraction. Preserve its three dimensions: `design_system`, `design_style`, and `visual_effects`.

For every populated leaf, add an entry to a top-level `evidence` map keyed by JSON path:

```json
{
  "evidence": {
    "design_system.color.primary.hex": {
      "status": "measured",
      "confidence": 0.96,
      "source": "Figma variable Brand/Primary"
    }
  }
}
```

Allowed status values:

- `authoritative`: approved token, Figma variable, spec, or implementation constant.
- `measured`: directly sampled or inspected.
- `observed`: clearly visible behavior or property without an exact value.
- `inferred`: reasoned estimate from incomplete evidence.
- `unknown`: not supported by the available references.

Use `null` for unknown values. Do not fabricate a complete-looking profile. Keep confidence between 0 and 1 and explain low-confidence fields briefly.

## Workflows

### Structure

When asked for the schema, read `references/schema.md`, present only the relevant sections unless the user requests the complete schema, and explain the evidence contract.

### Analyze references

1. Identify reference type and available evidence.
2. Inspect the strongest sources first.
3. Extract only supported fields.
4. Record evidence status, confidence, and source for every populated field.
5. Separate shared patterns from reference-specific variants.
6. Report unknowns and contradictions instead of silently resolving them.
7. Return the JSON plus a concise findings summary.

Do not claim exact typography, spacing, easing, shader parameters, or responsive behavior from a static screenshot unless they were directly measured elsewhere.

### Apply existing DNA

Only generate or modify an implementation when the user explicitly requests it. Read [references/generation-guide.md](references/generation-guide.md), then:

1. Validate the DNA against current project tokens and constraints.
2. Preserve existing framework, animation library, component system, and source-of-truth files.
3. Ask before installing dependencies, downloading assets, or introducing network-loaded resources.
4. Use original assets only when provenance and reuse permission are clear.
5. Respect reduced motion, accessibility, responsive behavior, and performance budgets.
6. Verify the result against the supplied references and list any intentional deviations.

Never create a competing `MASTER.md` or replace existing Figma/vault token architecture unless the user explicitly asks for a migration.

## Vault integration

Inside the shared Claude + Codex vault, route an approved durable Design DNA profile to the active project's normal PARA pages. Keep raw references in the project's immutable `sources/` folder. Follow zone isolation and update the active index, log, and hot cache when the operation is significant.
