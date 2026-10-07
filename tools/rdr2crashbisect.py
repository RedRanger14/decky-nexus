"""Bisect by switching suspects OFF against a full background.

Everything enabled stays on except `always_off`; each step switches off
half the suspects. A lit picture means the culprit is in the half that was
off. Judged by screenshot; crashes and no-starts are booted again.
Restores the starting set at the end.

  python3 rdr2_offbisect.py
"""
import json
import sys

sys.path.insert(0, "/home/deck")
import rdr2boothunt as h  # noqa: E402

# Collyrium crashes alone; without it the other four overhauls crash
# together. All five off boots every time and is still black.
ALWAYS_OFF = {
    "Collyrium - Visual and Weather Overhaul", "Sekis Visuals - Graphics Changes",
    "Visuals", "VESTIGIA 2.0 - A Visual Mod", "VAXISs PBW - Physics Based Water v.2.0",
    "Disable Out Of Bounds Snipers",  # the black screen
}
FAULT = "exit"   # with the black screen gone, the game crashes in the world
SUSPECTS = [
    "Bounty Hunting - Expanded and Enhanced", "Brave Horses", "CAR MOD EVOLVED",
    "Camp Anywhere", "Catalogue Improvements", "Center Mass - Bleed Out Mod",
    "Colt 1911 Mod Expanded Version", "Colt Model 1911",
    "Community ScriptHookRDR2 .NET", "Complete Horse Overhaul",
    "Complete Temperature OVERHAUL", "Core Regen", "Corn Sack Fix",
    "Cozy Mountain Campsite", "Crime Tweaks", "Custom First Person FOV",
    "Cut Content Dogs Unlocked for SP", "Cut Dialogue Enhanced", "DeadEyeInfinite",
    "Disable Out Of Bounds Snipers", "Dog Companion", "Duels",
    "Dump That Cargo 1.1", "Early Game Fishing 1.0",
]

h.LOOK = True
m = h.load_plugin()
if not h.ensure_pad():
    raise SystemExit("pad daemon will not start")
original, keys = h.read_state(m)
SUSPECTS = sorted(set(original) - ALWAYS_OFF)
missing = (set(SUSPECTS) | ALWAYS_OFF) - set(keys)
if missing:
    raise SystemExit(f"no such records: {sorted(missing)}")
base = set(original) - ALWAYS_OFF


def judge(label, on):
    h.apply_state(m, on, keys)
    for attempt in range(3):
        v = h.boot_once(label if not attempt else f"{label}-again{attempt}")
        if v in ("ok", FAULT):
            return v
        h.say(f"  {label}: {v}; booting again")
    return v


try:
    h.say(f"base: all on except {sorted(ALWAYS_OFF)}")
    v = judge("base", base)
    h.say(f"BASE {v.upper()}")
    if v != FAULT:
        h.say(f"ABORT: the base is not {FAULT}, nothing to bisect")
    else:
        suspects = list(SUSPECTS)
        while len(suspects) > 1:
            half = suspects[: len(suspects) // 2]
            h.say(f"switching off {len(half)} of {len(suspects)}: {half}")
            v = judge(f"off-{len(half)}", base - set(half))
            if v == "ok":
                suspects = half
            elif v == FAULT:
                suspects = suspects[len(half):]
            else:
                h.say(f"  cannot judge ({v}); stopping")
                break
        h.say(f"CULPRIT: {suspects}" if len(suspects) == 1
              else f"NARROWED TO {len(suspects)}: {suspects}")
        if len(suspects) == 1:
            v = judge("confirm", base - set(suspects))
            h.say(f"CONFIRM without {suspects[0]}: {v.upper()}")
finally:
    h.say("restoring the original mod set")
    h.apply_state(m, set(original), keys)
    h.kill_game()
    h.say("restored")
