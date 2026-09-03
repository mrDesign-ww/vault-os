---
name: wiki-query
description: Query this vault's canonical, zone-isolated L0-L3 memory for project context or evidence.
---

# Query canonical memory

Determine the active zone first. Read only its hot cache and index. Run:

`/usr/bin/python3 scripts/memory-model.py retrieve "QUERY" --zone ZONE --loadout ROLE`

Use `designer`, `builder`, `researcher`, `reviewer` or `general` according to the task. Designer, builder and reviewer require `--project PROJECT-SLUG`. Role budgets are hard caps; `--top` can only reduce them. Use `memory-model.py inspect` to see exclusions, selected snippets, candidate inbox state and budget use. Read approved L3, explicit current L2, then relevant L1. Treat `legacy-unverified` results as discovery candidates that require confirmation. Add `--include-l0` only to inspect original evidence.

Automatically inferred or uncertain claims are not answers or canonical memory. If they are worth preserving, propose them through `scripts/memory-candidates.py` under the shared zone lock. Do not release after file creation: with the same owner token update the candidate catalog, zone log and hot cache, rebuild and validate, create a scoped local Git checkpoint, release in the final path, then run a post-release candidate list or `--include-l0` project query. They remain L0 until evidence review and a separate L1 or L2 page is approved.

If retrieval reports a stale or missing index, acquire the shared zone lock, retain its token, run `build --zone ZONE --lock-token TOKEN` and `validate --zone ZONE --lock-token TOKEN`, then release in a `finally` path and retry. An expired lock stays fail-closed until explicit recovery. Never query multiple zones or use another zone's index.
