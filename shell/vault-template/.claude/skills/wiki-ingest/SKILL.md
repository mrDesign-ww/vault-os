---
name: wiki-ingest
description: Ingest a source into this private vault while preserving immutable L0 evidence and separate verified synthesis.
---

# Ingest into canonical memory

1. Determine the active zone and project. Do not inspect another zone.
2. Acquire `bash scripts/wiki-lock.sh acquire .vault-meta/write/ZONE --ttl 3600`. Save, ingest and evolution share this exact zone lock. Retain the token. After every long phase and before later writes, build and checkpoint, run `renew` then `check`. Release in a `finally` path on success or failure; never reacquire over expired partial work.
3. Copy the original source without rewriting it. Project evidence goes in the project's `sources/`. Non-project work evidence goes to `wiki/resources/incoming/`; personal and Client non-project evidence goes to that zone's `inbox`. Evidence is always L0, regardless of its frontmatter.
4. Verify the copied bytes or hash. Create a separate L1 page with `memory_provenance`, or L2 synthesis when the source clearly changes canonical project state. If the claim is inferred, conflicting or uncertain, create an L0 memory candidate instead and wait for review.
5. Update project and zone indexes, append-only log, then hot cache.
6. Run zone `scan`, check the token, `build --zone ZONE --lock-token TOKEN`, and `validate --zone ZONE --lock-token TOKEN`. Check again, create a scoped local Git checkpoint and never push.
7. Release in the `finally` path, then run a relevant retrieval query and inspect its current-state snippet. Do not call the plugin repository's `wiki-mode.py`.

Stop, release the token, and report the exact failure if provenance, zone, source immutability, validation, checkpoint, or retrieval acceptance cannot be established.
