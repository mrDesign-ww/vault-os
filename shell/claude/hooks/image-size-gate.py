#!/usr/bin/env python3
# PreToolUse(Read): build and service agents read big images downscaled or cropped, which saves context.
# Visual roles (look-reviewer, checker, designers, reviewer), unknown types and the main session are never
# gated, so look judgement keeps full detail. macOS only: it measures with `sips`; elsewhere it fails open.
# Any error fails open. Selftest: image-size-gate.py --selftest
import json, os, re, subprocess, sys

GATED = {'builder', 'architect', 'worker', 'runner', 'locator'}
LIMIT = 1600


def size(p):
    o = subprocess.run(['sips', '-g', 'pixelWidth', '-g', 'pixelHeight', p],
                       capture_output=True, text=True, timeout=4).stdout.split()
    return int(o[o.index('pixelWidth:') + 1]), int(o[o.index('pixelHeight:') + 1])


def decide(d):
    p = (d.get('tool_input') or {}).get('file_path') or ''
    if d.get('agent_type') not in GATED or not re.search(r'\.(png|jpe?g|webp)$', p, re.I):
        return None
    if '@full' in os.path.basename(p) or not os.path.isfile(p):
        return None
    w, h = size(p)
    if max(w, h) <= LIMIT:
        return None
    return (f'Image is {w}x{h}. Build and service agents read images up to {LIMIT} px. '
            f"Overall look: sips -Z 1280 '{p}' --out <your scratchpad>/<name>.png and read that. "
            f"Fine detail: crop the region at full size: sips -c <h> <w> --cropOffset <y> <x> '{p}' --out <scratchpad file>. "
            'Only if the whole image at full size is truly needed (pixel compare with Figma), copy it to a name '
            'containing @full and say why in your report.')


def selftest():
    assert decide({'agent_type': 'look-reviewer', 'tool_input': {'file_path': '/x/a.png'}}) is None
    assert decide({'agent_type': 'builder', 'tool_input': {'file_path': '/x/a.txt'}}) is None
    assert decide({'agent_type': 'builder', 'tool_input': {'file_path': '/no/such/a.png'}}) is None
    assert decide({'tool_input': {'file_path': '/x/a.png'}}) is None
    print('selftest ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--selftest']:
        selftest()
        sys.exit(0)
    try:
        why = decide(json.load(sys.stdin))
    except Exception:
        sys.exit(0)
    if why:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                                                 'permissionDecision': 'deny',
                                                 'permissionDecisionReason': why}}))
