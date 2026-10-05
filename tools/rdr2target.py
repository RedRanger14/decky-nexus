"""Targeted RDR2 boots on the Legion, judged by screenshot.

  python3 rdr2_target.py without "Collyrium - Visual and Weather Overhaul"
  python3 rdr2_target.py only "Collyrium - Visual and Weather Overhaul"

`without`: every enabled mod on except the named ones.
`only`: the named ones on, everything else off.
Restores the starting mod set at the end, whatever happens.
"""
import sys

sys.path.insert(0, "/home/deck")
import rdr2boothunt as h  # noqa: E402  (copy rdr2boothunt.py beside it)

h.LOOK = True
mode, names = sys.argv[1], set(sys.argv[2:])
m = h.load_plugin()
if not h.ensure_pad():
    raise SystemExit("pad daemon will not start")
original, keys = h.read_state(m)
missing = names - set(keys)
if missing:
    raise SystemExit(f"no such records: {sorted(missing)}")
want = set(original) - names if mode == "without" else set(names)
try:
    h.say(f"target {mode} {sorted(names)}: {len(want)} mods on")
    h.apply_state(m, want, keys)
    v = h.boot_once(f"{mode}")
    if v not in ("ok", "black"):
        h.say(f"  {v}; booting again")
        v = h.boot_once(f"{mode}-again")
    h.say(f"RESULT {mode} {sorted(names)}: {v.upper()}")
finally:
    h.say("restoring the original mod set")
    h.apply_state(m, set(original), keys)
    h.kill_game()
    h.say("restored")
