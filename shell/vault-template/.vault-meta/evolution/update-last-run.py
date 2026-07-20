#!/usr/bin/env python3
"""Atomically update evolution state using a JSON serializer."""

import argparse
import datetime
import json
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT = os.path.join(HERE, "last-run.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--platform", choices=("claude", "codex"), required=True)
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    parser.add_argument("--phase", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--notes", required=True)
    args = parser.parse_args()

    phases = {}
    for item in args.phase:
        if "=" not in item:
            parser.error("--phase must use NAME=VALUE")
        name, value = item.split("=", 1)
        phases[name.strip()] = value.strip()

    destination = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    try:
        with open(destination, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        payload = {}

    platforms = payload.setdefault("platforms", {})
    platform_state = platforms.setdefault(args.platform, {})
    prior_phases = platform_state.get("phases", {})
    if not isinstance(prior_phases, dict):
        prior_phases = {}
    prior_phases.update(phases)
    platform_state.update({
        "last_run": args.date,
        "phases": prior_phases,
        "notes": args.notes,
    })

    # Keep legacy top-level fields for older Claude tooling.
    payload.update({"last_run": args.date, "phases": prior_phases, "notes": args.notes})
    fd, temporary = tempfile.mkstemp(
        prefix=".last-run-", suffix=".json.tmp", dir=os.path.dirname(destination)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
