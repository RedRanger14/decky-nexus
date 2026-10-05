"""Find which RDR2 mod crashes Story Mode, by bisection, unattended.

Why this exists
---------------
Red Dead Redemption 2 boots to its title screen with almost any mod set.
The crash comes AFTER pressing Story: a short load, then "Unknown error
FFFFFFFF", and the game closes when OK is pressed. On 2026-10-05 the Legion
had two collections installed (Ultimate RDR 2 - Essentials and RDR 2: Fixed
and Enhanced, 111 mods) and the culprit was WhyEm's DLC. Finding it by hand
cost Michael a boot per step; this does the same steps with nobody there.

It is bg3boothunt.py's shape (kill, toggle through the plugin, launch,
judge, restore) with RDR2's two differences: someone has to press Story,
and the title screen looks exactly like a failed boot in the process stats.

How a boot is judged
--------------------
Measured on device, RDR2.exe's resident memory:

    title screen, idle ....... 3.0-3.1 GB, flat
    loading into Story ....... climbs from there
    in the game (camp) ....... 4.5-4.6 GB, still creeping up

The trap, found the hard way: the first Story press after launch does not
always land. When it does not, the game sits on the title screen at 3 GB,
flat, which is indistinguishable from "loaded and crashed back". Two of the
first four automated boots were misread that way before a screenshot
showed the title screen. So a press only counts once memory has LEFT the
title baseline; until then the harness presses again, and a boot that
never leaves it is NOSTART, never a verdict about the mods.

    ok     memory passed GAME_RSS_KB and held there for SETTLE_SAMPLES
    exit   loading began, then RDR2.exe vanished (the FFFFFFFF dialog
           closes the game), or memory fell back to the title baseline
    nostart  the press never took; says nothing about the mods

Hard-won details
----------------
* Match the process name EXACTLY. "RDR2.exe" is a suffix of
  "PlayRDR2.exe", the launcher shim, and a substring match reports the game
  running before it has started.
* Wait for the launcher to let go between boots. Relaunching a few seconds
  after a kill got the Rockstar Launcher's "Unable to launch Red Dead
  Redemption 2 ... Code:7002.1" dialog (network was fine). Killing every
  Rockstar process and waiting RELAUNCH_GAP_SECS cleared it.
* Input is a uinput Xbox pad (bg3pad.py, running as a user service). XTEST
  clicks do not reach the game; the title screen takes A. The prompts may
  show keyboard glyphs after a keyboard event elsewhere; the pad still
  works, the glyphs are cosmetic.
* Never launch while someone may be playing: this signs Michael's Rockstar
  and Steam accounts in. Ask first (memory: ask-before-launching-games).

Usage (runs ON the device; start it as a user service so ssh can drop)
-----
    python3 rdr2boothunt.py --check
    python3 rdr2boothunt.py --hunt [--collection SLUG]
    python3 rdr2boothunt.py --verify 2
    python3 rdr2boothunt.py --restore-only

    systemd-run --user --unit rdr2hunt --collect \\
        sh -c "exec python3 /tmp/rdr2boothunt.py --hunt > /tmp/rdr2hunt.out 2>&1"
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

APPID = "1174180"
DOMAIN = "reddeadredemption2"
GAME_DIR = "Red Dead Redemption 2"
MODS_SUBDIR = "._nexus_mods_unused"
PLUGIN_DIR = "/home/deck/homebrew/plugins/Nexus Mods"
PAD = "/home/deck/bg3pad.py"
LOG_PATH = "/tmp/rdr2-boot-hunt.log"
WANTED_PATH = "/home/deck/.rdr2-boot-hunt-wanted.json"  # not /tmp: survives a reboot

GAME_EXE = "RDR2.exe"
ROCKSTAR_PROCS = ("RDR2.exe", "PlayRDR2.exe", "Launcher.exe",
                  "RockstarErrorHa", "RockstarService")

SAMPLE_SECS = 10
TITLE_RSS_KB = 3_300_000      # above the 3.0-3.1 GB idle title screen
GAME_RSS_KB = 4_200_000       # under the 4.5 GB measured in camp
SETTLE_SAMPLES = 3
PRESS_TRIES = 4
PRESS_SETTLE_SECS = 12        # first press 8s after the exe appears missed once
START_BUDGET_SECS = 120
BOOT_BUDGET_SECS = 300
RELAUNCH_GAP_SECS = 15


# ---------------------------------------------------------------------------
# The pure part: given RSS samples (KB) taken after Story was pressed and
# whether the process is still alive, what happened? Unit-tested.
# ---------------------------------------------------------------------------
def classify(samples, alive, budget_used):
    """samples: RSS in KB, oldest first. Returns ok, exit, nostart,
    inconclusive or watching."""
    loading = any(s > TITLE_RSS_KB for s in samples)
    if not alive:
        return "exit" if loading else "nostart"
    if not samples:
        return "inconclusive" if budget_used else "watching"
    tail = samples[-SETTLE_SAMPLES:]
    if len(tail) == SETTLE_SAMPLES and all(s >= GAME_RSS_KB for s in tail):
        return "ok"
    # Reached the game once and fell back to title-screen memory: the load
    # failed and the game recovered to its menu instead of exiting.
    if any(s >= GAME_RSS_KB for s in samples) and samples[-1] < TITLE_RSS_KB:
        return "exit"
    if budget_used:
        return "inconclusive" if loading else "nostart"
    return "watching"


def press_landed(before_kb, after_kb):
    """Did a Story press take? Only memory leaving the title baseline says
    so; an acknowledged pad event does not."""
    return after_kb > TITLE_RSS_KB and after_kb > before_kb


# ---------------------------------------------------------------------------
# Everything below touches the machine.
# ---------------------------------------------------------------------------
def say(msg):
    line = time.strftime("[%H:%M:%S] ") + msg
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def run_cmd(args, timeout=20):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        say("  (timeout) " + " ".join(args))
        return None


def procs():
    """{comm: [pid]} for the Rockstar processes, exact names only."""
    out = run_cmd(["ps", "-eo", "pid=,comm="], timeout=10)
    found = {}
    if out is None:
        return found
    for line in out.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip() in ROCKSTAR_PROCS:
            found.setdefault(parts[1].strip(), []).append(int(parts[0]))
    return found


def game_pid():
    pids = procs().get(GAME_EXE)
    return pids[0] if pids else None


def rss_kb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def kill_game():
    """Close the game, the launcher and its services, then give the
    launcher time to let go (or the next launch fails with Code:7002.1)."""
    for _ in range(3):
        live = procs()
        if not live:
            break
        for pids in live.values():
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        time.sleep(3)
    if procs():
        return False
    time.sleep(RELAUNCH_GAP_SECS)
    return True


def pad(*cmds):
    run_cmd(["python3", PAD, "send", *cmds], timeout=20)


def ensure_pad():
    st = run_cmd(["systemctl", "--user", "is-active", "bg3pad"], timeout=10)
    if st is not None and st.stdout.strip() == "active":
        return True
    run_cmd(["systemd-run", "--user", "--unit=bg3pad", "--collect",
             "python3", PAD, "daemon"], timeout=20)
    time.sleep(2)
    st = run_cmd(["systemctl", "--user", "is-active", "bg3pad"], timeout=10)
    return st is not None and st.stdout.strip() == "active"


def load_plugin():
    sys.path.insert(0, PLUGIN_DIR)
    import importlib.util
    import types

    quiet = types.SimpleNamespace(
        info=lambda *a: None, warning=lambda *a: None,
        error=lambda *a: None, debug=lambda *a: None,
        exception=lambda *a: None,
    )
    d = types.ModuleType("decky")
    d.logger = quiet
    d.DECKY_USER_HOME = "/home/deck"
    d.DECKY_PLUGIN_SETTINGS_DIR = "/home/deck/homebrew/settings/Nexus Mods"
    d.DECKY_PLUGIN_RUNTIME_DIR = "/home/deck/homebrew/data/Nexus Mods"
    d.DECKY_PLUGIN_LOG_DIR = "/home/deck/homebrew/logs/Nexus Mods"
    d.DECKY_PLUGIN_VERSION = "boothunt"

    async def _emit(*a, **k):
        pass

    d.emit = _emit
    sys.modules["decky"] = d
    spec = importlib.util.spec_from_file_location(
        "rdr2hunt_main", os.path.join(PLUGIN_DIR, "main.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def read_state(m, slug=None):
    """(enabled keys, all keys) of RDR2 records, optionally one collection's.
    Records with a stored reason other than ours were switched off on
    purpose; they are not suspects and are left alone."""
    recs = m._load_settings().get("installed", {}).get(DOMAIN, {}) or {}
    keys = sorted(
        k for k, r in recs.items()
        if not slug or r.get("collection_slug") == slug
    )
    return (sorted(k for k in keys if recs[k].get("enabled", True)), keys)


def apply_state(m, keys_on, keys):
    import asyncio

    if not kill_game():
        raise RuntimeError("RDR2 will not close; refusing to guess at state")
    plugin = m.Plugin()

    async def go():
        recs = m._load_settings().get("installed", {}).get(DOMAIN, {}) or {}
        for k in keys:
            want = k in keys_on
            if bool((recs.get(k) or {}).get("enabled", True)) == want:
                continue
            r = await plugin.set_mod_enabled(
                GAME_DIR, MODS_SUBDIR, k, want, "folder", DOMAIN, int(APPID),
                "", "starred", "" if want else "boot hunt", [])
            if not r.get("ok"):
                say(f"  toggle failed {k} -> {want}: {r.get('error')}")

    asyncio.run(go())


def boot_once(label):
    """Launch, press Story until it takes, judge. Leaves the game closed."""
    if not kill_game():
        say(f"  {label}: cannot close a previous RDR2; not booting")
        return "error"
    subprocess.Popen(["steam", "-applaunch", APPID],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = time.time()
    pid = None
    while time.time() - started < START_BUDGET_SECS:
        time.sleep(5)
        pid = game_pid()
        if pid:
            break
    if not pid:
        say(f"  {label}: RDR2.exe never started (launcher side, not a mod)")
        kill_game()
        return "nostart"
    say(f"  {label}: pid {pid}")
    time.sleep(PRESS_SETTLE_SECS)
    base = rss_kb(pid) or 0
    landed = False
    for attempt in range(1, PRESS_TRIES + 1):
        pad("A")
        time.sleep(PRESS_SETTLE_SECS)
        now = rss_kb(pid)
        if now is None:
            break
        say(f"    press {attempt}: rss {base // 1024}MB -> {now // 1024}MB")
        if press_landed(base, now):
            landed = True
            break
    if not landed and game_pid():
        say(f"  {label}: Story never took after {PRESS_TRIES} presses")
        kill_game()
        return "nostart"
    samples = []
    t0 = time.time()
    while True:
        alive = game_pid() == pid
        cur = rss_kb(pid) if alive else None
        if cur is not None:
            samples.append(cur)
            say(f"    {label} t={time.time() - t0:3.0f} rss={cur // 1024}MB")
        verdict = classify(samples, alive, time.time() - t0 > BOOT_BUDGET_SECS)
        if verdict != "watching":
            say(f"  {label}: {verdict.upper()}")
            kill_game()
            return verdict
        time.sleep(SAMPLE_SECS)


def save_wanted(keys):
    try:
        with open(WANTED_PATH, "w") as f:
            json.dump(sorted(keys), f)
    except OSError:
        pass


def check(m):
    ok = True
    if not os.path.isfile(PAD):
        say(f"  FAIL: no pad daemon at {PAD}")
        ok = False
    elif not ensure_pad():
        say("  FAIL: the pad daemon would not start")
        ok = False
    enabled, keys = read_state(m)
    say(f"  {len(enabled)} enabled of {len(keys)} RDR2 records")
    if game_pid():
        say("  NOTE: RDR2 is running; a hunt would close it")
    say("  ready" if ok else "  NOT ready")
    return ok


def hunt(m, slug=None):
    """Bisect the enabled mods (optionally one collection's) to the one
    whose presence crashes Story Mode. Mods outside the scope stay on."""
    enabled, keys = read_state(m, slug)
    _all_on, all_keys = read_state(m)
    if not enabled:
        say("nothing enabled to hunt")
        return
    original = read_state(m)[0]
    save_wanted(original)
    others_on = set(original) - set(enabled)

    def bail(*_a):
        say("interrupted: restoring the original mod set")
        apply_state(m, set(original), all_keys)
        sys.exit(1)

    signal.signal(signal.SIGINT, bail)
    signal.signal(signal.SIGTERM, bail)
    try:
        say(f"confirm the fault: all {len(enabled)} suspects ON")
        apply_state(m, others_on | set(enabled), all_keys)
        if boot_once("all-on") != "exit":
            say("ABORT: the full set did not crash, so there is nothing to bisect")
            return
        say("control: suspects OFF")
        apply_state(m, others_on, all_keys)
        v = boot_once("control")
        if v != "ok":
            say(f"ABORT: the game does not reach Story Mode with every suspect "
                f"off ({v}); the cause is outside this set")
            return
        suspects = list(enabled)
        while len(suspects) > 1:
            half = suspects[: len(suspects) // 2]
            say(f"trying {len(half)} of {len(suspects)} on")
            apply_state(m, others_on | set(half), all_keys)
            v = boot_once(f"half-{len(half)}")
            if v == "exit":
                suspects = half
                continue
            if v != "ok":
                say(f"  cannot judge that half ({v}); stopping with "
                    f"{len(suspects)} candidates rather than guessing")
                break
            rest = suspects[len(half):]
            apply_state(m, others_on | set(rest), all_keys)
            v2 = boot_once(f"rest-{len(rest)}")
            if v2 == "exit":
                suspects = rest
                continue
            if v2 != "ok":
                say(f"  cannot judge that half ({v2}); stopping")
                break
            say("  NEITHER half crashes alone: an INTERACTION, not one mod. Stopping.")
            break
        say(f"CULPRIT: {suspects[0]}" if len(suspects) == 1
            else f"NARROWED TO {len(suspects)}: {suspects}")
    finally:
        say("restoring the original mod set")
        apply_state(m, set(original), all_keys)
        kill_game()


def verify(times=2):
    """Boot the current set `times` over. Every hunt ends here: the last
    thing verified must be the thing left behind."""
    verdicts = [boot_once(f"verify-{i}") for i in range(1, times + 1)]
    say("VERIFIED" if all(v == "ok" for v in verdicts)
        else f"NOT VERIFIED: {verdicts}")
    return verdicts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--hunt", action="store_true")
    ap.add_argument("--collection", metavar="SLUG")
    ap.add_argument("--restore-only", action="store_true")
    ap.add_argument("--verify", type=int, nargs="?", const=2, metavar="N")
    args = ap.parse_args()
    m = load_plugin()
    if args.restore_only:
        try:
            with open(WANTED_PATH) as f:
                keys = json.load(f)
        except (OSError, ValueError):
            say("no saved state to restore")
            return 1
        apply_state(m, set(keys), read_state(m)[1])
        say("restored")
        return 0
    if not check(m):
        return 1
    if args.verify is not None:
        return 0 if all(v == "ok" for v in verify(args.verify)) else 1
    if args.hunt or args.collection:
        hunt(m, args.collection)
        verify(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
