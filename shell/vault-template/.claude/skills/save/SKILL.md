---
name: save
description: Save the current Claude session into this private vault with zone-safe PARA routing, L0-L3 trust, and a verified retrieval rebuild.
---

# Save this session

Use this vault-local workflow instead of the generic plugin save command.

1. Determine the active zone from the conversation. Default to work. Never read or write another zone.
2. Acquire `bash scripts/wiki-lock.sh acquire .vault-meta/write/ZONE --ttl 3600`. Every batch writer for that zone uses this same lock path. Record the returned token. Use a `finally` path that releases this exact token after success or failure. After every long phase and before later writes, build and checkpoint, run `renew` then `check`. Never reacquire over an expired record and continue partial work.
3. Preserve raw session material only when useful. It is L0 and goes to the active inbox. Work inbox is `wiki/workspace/projects/inbox/`.
4. Materialize explicit user decisions and verified reusable outcomes as separate project or resource pages. Use explicit L1 with `memory_provenance`, or explicit L2 for canonical state and workflows. Automatically inferred or uncertain claims go through `memory-candidates.py` and remain L0 until reviewed. Evidence paths remain L0.
5. Update the active zone index, project index, append-only log, then overwrite its hot cache. Do not call the plugin repository's `wiki-mode.py`.
6. Run `memory-model.py scan`, then `build --zone ZONE --lock-token TOKEN`, then `validate --zone ZONE --lock-token TOKEN`. The build is the final wiki write.
7. Check the token again, then create a local Git checkpoint containing only this save's durable files. Never push. Release in the `finally` path, then run one project-scoped retrieval acceptance query with the matching named role loadout and inspect its snippet and context budget.

If validation or checkpoint fails, release the token, report the exact state, then reacquire before repair. Do not claim that context was saved while retrieval is stale or the acceptance query fails.
