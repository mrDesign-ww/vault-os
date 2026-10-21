# Feature spec: <name>

Filled in before the first review. Reviewers check the feature against this spec, not against taste.

## Goal
What the user gets. One or two sentences.

## Done criteria
- Functional: what must work, testable, as a list.
- Visual: a measurable criterion for every debatable spot. Example: "hero glow: opacity ≤ 0.6, duration 0.8–1.2 s, never covers the price". A reference frame or Figma node link also works.

## Devices and viewports
Required: 320, iPhone 390×844, desktop 1440. Add if it matters for the feature: tablet, landscape, slow network.

## Error paths
What the user sees on network loss, timeout, API refusal, empty data and a repeated tap.

## Money and state
Invariants that must never break. Example: "the balance changes exactly once per order", "a repeated request never charges twice". Each invariant gets a test that fails when it is broken.

## OWNER-DECISIONS
The owner's decisions with dates. Reviewers do not challenge them or report them as problems.
- YYYY-MM-DD: <decision>

## Out of scope
What this feature deliberately does not do.
