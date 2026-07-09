#!/usr/bin/env bash
# Vault OS — macOS platform helpers. Claude calls these functions during install.
# Usage: bash install-mac.sh <function> [args...]
set -euo pipefail

ensure_prereqs() {
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew is required. Install it from https://brew.sh then re-run." >&2; return 1
  fi
  for p in git node python3; do command -v "$p" >/dev/null 2>&1 || brew install "$p"; done
  command -v claude >/dev/null 2>&1 || npm install -g @anthropic-ai/claude-code
  echo "prereqs ok"
}

ensure_obsidian() {
  if [ -d "/Applications/Obsidian.app" ]; then echo "Obsidian present"; else brew install --cask obsidian; fi
}

ensure_codex() {
  if [ -d "/Applications/Codex.app" ]; then echo "Codex present"; else
    brew install --cask codex 2>/dev/null || echo "Install the Codex desktop app from OpenAI, then sign in."
  fi
}

make_shortcut() {  # $1 = vault path, $2 = name
  local vault="$1" name="${2:-Vault}" f
  f="$HOME/Desktop/${name}.command"
  printf '#!/bin/bash\ncd %q && claude\n' "$vault" > "$f"
  chmod +x "$f"
  echo "Shortcut created: $f"
}

backup() {  # $1 = file to back up if it exists
  [ -f "$1" ] && cp "$1" "$1.backup-$(date '+%Y%m%d-%H%M%S')" && echo "backed up $1" || true
}

"$@"
