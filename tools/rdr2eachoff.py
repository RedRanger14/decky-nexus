"""Switch each suspect off on its own against a stable base, judged by
screenshot. Prints one RESULT line per suspect; restores at the end.

  python3 rdr2_eachoff.py
"""
import sys

sys.path.insert(0, "/home/deck")
import rdr2boothunt as h  # noqa: E402

BASE_OFF = {
    "Collyrium - Visual and Weather Overhaul", "Sekis Visuals - Graphics Changes",
    "Visuals", "VESTIGIA 2.0 - A Visual Mod", "VAXISs PBW - Physics Based Water v.2.0",
}
SUSPECTS = [
    "DeadEyeInfinite", "Disable Out Of Bounds Snipers", "Dog Companion",
    "Duels", "Dump That Cargo 1.1", "Early Game Fishing 1.0",
]

h.LOOK = True
m = h.load_plugin()
if not h.ensure_pad():
    raise SystemExit("pad daemon will not start")
original, keys = h.read_state(m)
missing = (set(SUSPECTS) | BASE_OFF) - set(keys)
if missing:
    raise SystemExit(f"no such records: {sorted(missing)}")
base = set(original) - BASE_OFF
try:
    for s in SUSPECTS:
        h.apply_state(m, base - {s}, keys)
        v = h.boot_once(f"off {s}")
        if v not in ("ok", "black", "exit"):
            v = h.boot_once(f"off {s} again")
        h.say(f"RESULT without {s}: {v.upper()}")
finally:
    h.say("restoring the original mod set")
    h.apply_state(m, set(original), keys)
    h.kill_game()
    h.say("restored")
