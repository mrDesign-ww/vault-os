#!/usr/bin/env bash
set -euo pipefail

vault_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WIKI_LOCK_VAULT="$vault_root"
exec /usr/bin/python3 "$vault_root/scripts/wiki-lock.py" "$@"
