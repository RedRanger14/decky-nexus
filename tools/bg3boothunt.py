"""Find which BG3 mod stops the game booting, by bisection, unattended.

Why this exists
---------------
Baldur's Gate 3 says nothing when a mod's data is bad. It shows a loading
bar that stops moving, and the process sits there burning CPU. On device
(2026-09-02) a 97-mod collection hung at 79%, then at 94%, and the only way
to know which mod did it was to try subsets. Doing that by hand costs an
evening per collection; this does it in about half an hour with nobody
watching.

How a boot is judged
--------------------
Measured, not guessed. The fault has a signature that a healthy boot does
not share:

    spinning: 1.5+ cores pinned, RSS flat to within a few MB, and ZERO
              bytes read or written. (Zero I/O also rules out shader
              compilation, which writes its cache.)
    healthy:  the game has SETTLED at the press-any-key screen: the memory
              of a loaded game, its profile files written, and CPU quiet
              for three samples in a row. Memory growth on its own is
              loading, not success.

Anything that fits neither is reported as INCONCLUSIVE rather than
guessed at. An earlier version of this called "I ran out of time" a spin,
which would have blamed an innocent mod. Another called any boot that had
grown by 150MB a success, which every boot does in its first 20 seconds,
so a hang at 94% could never have been caught.

Hard-won details, all of them from a failure
--------------------------------------------
* Launch with `steam -applaunch <appid>`. The `steam://rungameid/<id>` URL
  opened the STORE PAGE on this device and the game never started, which
  the watcher then read as a failure ("bg3 just seems to be sat on the
  store page").
* Never name a script after a stdlib module. The first version was
  `bisect.py`, which shadowed `bisect`, so it died during import with no
  output at all and looked like a hang.
* The plugin REFUSES to change mod state while bg3 is alive, and it is
  right to (moving paks under a load hung the game). Every toggle in the
  first run was refused for that reason, leaving a control run that tested
  nothing. So: kill, verify dead, then toggle.
* Put a timeout on every subprocess call. One `pkill` that never returned
  wedged the whole run reading its pipe.
* Restore the caller's mod set on the way out - including on Ctrl+C, on a
  crash, and on a machine that reboots mid-run (the wanted state is
  written to disk first, so --restore-only can finish the job).

Usage
-----
    python3 tools/bg3boothunt.py --check          # environment only
    python3 tools/bg3boothunt.py --hunt           # bisect enabled mods
    python3 tools/bg3boothunt.py --collection SLUG
    python3 tools/bg3boothunt.py --restore-only   # after an aborted run

Runs ON the device (it needs the game and the plugin), so copy it over and
run it there. SteamOS sets logind KillUserProcesses=True, so anything
started from an ssh session dies the moment that session closes - nohup
and & do not help. Start it as a transient user service instead:

    systemd-run --user --unit bg3hunt --collect \\
        sh -c "exec python3 /tmp/bg3boothunt.py --hunt > /tmp/hunt.log 2>&1"
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

APPID = "1086940"
DOMAIN = "baldursgate3"
GAME_DIR = "Baldurs Gate 3"
PLUGIN_DIR = "/home/deck/homebrew/plugins/Nexus Mods"
LOG_PATH = "/tmp/bg3-boot-hunt.log"
WANTED_PATH = "/tmp/bg3-boot-hunt-wanted.json"

SAMPLE_SECS = 10
# A boot that has neither settled nor spun by here is reported as
# inconclusive. A clean boot to the "press any key" screen took ~40s on
# device; a 118-mod one needs longer, and a spin declares itself inside a
# minute either way.
BOOT_BUDGET_SECS = 360
# One core is 100 ticks/sec. A spin pinned 2-3 cores; the menu, being
# vsync-limited, sits well under this.
SPIN_TICKS_PER_SAMPLE = int(SAMPLE_SECS * 100 * 1.5)
SPIN_RSS_TOLERANCE_MB = 6
SPIN_SAMPLES_NEEDED = 6          # 60s of unambiguous spinning
# Measured 2026-09-02 with 118 mods: the press-any-key screen sits at
# 640-680 ticks per 10s sample (0.65 of a core) with memory flat at 2.1GB
# and no I/O. The spins sat at 2050 and 3180. One core splits them.
IDLE_TICKS_PER_SAMPLE = int(SAMPLE_SECS * 100 * 1.0)
MENU_RSS_MB = 1200


# ---------------------------------------------------------------------------
# The pure part: given samples, what happened? Unit-tested in
# tests/test_backend.py (TestBg3BootHunt) so the judgement can be trusted
# without a game to hand.
# ---------------------------------------------------------------------------
def classify(samples, profile_touched, budget_used):
    """samples: list of dicts with cpu_ticks, rss_mb, io_mb (cumulative).

    Returns "spin", "ok", or "watching"/"inconclusive".
    """
    if len(samples) < 2:
        return "watching"
    deltas = []
    for a, b in zip(samples, samples[1:]):
        deltas.append({
            "cpu": b["cpu_ticks"] - a["cpu_ticks"],
            "rss": b["rss_mb"] - a["rss_mb"],
            "io": b["io_mb"] - a["io_mb"],
        })

    # Healthy means SETTLED at the press-any-key screen: the memory of a
    # loaded game, profile files written, and three quiet samples in a
    # row. Growth alone is never success. The boot that hung at 94% grew
    # by a gigabyte first, and the version of this that called growth "ok"
    # would have cleared every late hang there has ever been.
    recent = deltas[-3:]
    if (
        len(recent) >= 3
        and profile_touched
        and samples[-1]["rss_mb"] > MENU_RSS_MB
        and all(d["cpu"] < IDLE_TICKS_PER_SAMPLE for d in recent)
    ):
        return "ok"

    # Spinning: pinned CPU, flat memory, no I/O, for long enough that it
    # cannot be a stutter.
    run = 0
    for d in deltas:
        if (
            d["cpu"] >= SPIN_TICKS_PER_SAMPLE
            and abs(d["rss"]) <= SPIN_RSS_TOLERANCE_MB
            and d["io"] == 0
        ):
            run += 1
            if run >= SPIN_SAMPLES_NEEDED:
                return "spin"
        else:
            run = 0

    if budget_used:
        # Deliberately NOT "spin". Blaming a mod on a timeout is how an
        # innocent one gets condemned.
        return "inconclusive"
    return "watching"


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
    """subprocess with a timeout ALWAYS. A pkill that never returned once
    wedged an entire run."""
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        say("  (timeout) " + " ".join(args))
        return None


def load_plugin():
    sys.path.insert(0, PLUGIN_DIR)
    import importlib.util
    import types

    quiet = types.SimpleNamespace(
        info=lambda *a: None, warning=lambda *a: None,
        error=lambda *a: None, debug=lambda *a: None,
    )
    d = types.ModuleType("decky")
    d.logger = quiet
    d.DECKY_USER_HOME = "/home/deck"
    d.DECKY_PLUGIN_SETTINGS_DIR = "/home/deck/homebrew/settings/Nexus Mods"
    d.DECKY_PLUGIN_RUNTIME_DIR = "/home/deck/homebrew/data/Nexus-Mods"
    d.DECKY_PLUGIN_LOG_DIR = "/home/deck/homebrew/logs/Nexus-Mods"

    async def _emit(*a, **k):
        pass

    d.emit = _emit
    sys.modules["decky"] = d
    spec = importlib.util.spec_from_file_location(
        "bg3hunt_main", os.path.join(PLUGIN_DIR, "main.py")
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def bg3_pids():
    out = run_cmd(["pgrep", "-x", "bg3"], timeout=10)
    if out is None or out.returncode != 0:
        return []
    return [int(x) for x in out.stdout.split() if x.isdigit()]


def game_pid():
    """The real game, not the shim. The shim sits in do_wait on its child;
    sampling the shim showed 93MB and 0% CPU and nearly sent me chasing a
    phantom."""
    pids = bg3_pids()
    if not pids:
        return None
    shim = pids[0]
    try:
        with open(f"/proc/{shim}/task/{shim}/children") as f:
            kids = [int(x) for x in f.read().split()]
        return kids[0] if kids else shim
    except (OSError, IndexError, ValueError):
        return shim


def kill_game(m=None):
    if not bg3_pids():
        return True
    run_cmd(["pkill", "-x", "bg3"], timeout=15)
    for _ in range(10):
        time.sleep(2)
        if not bg3_pids():
            return True
    run_cmd(["pkill", "-9", "-x", "bg3"], timeout=15)
    for _ in range(6):
        time.sleep(2)
        if not bg3_pids():
            return True
    return False


def sample(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            p = f.read().split()
        io_total = 0
        with open(f"/proc/{pid}/io") as f:
            for line in f:
                if line.startswith(("read_bytes", "write_bytes")):
                    io_total += int(line.split()[1])
        return {
            "cpu_ticks": int(p[13]) + int(p[14]),
            "rss_mb": int(p[23]) * 4096 // (1024 * 1024),
            "io_mb": io_total // (1024 * 1024),
        }
    except (OSError, IndexError, ValueError):
        return None


def profile_files(m):
    root = m.BG3_PROFILE_ROOT
    return [
        os.path.join(root, "analytics.lsx"),
        os.path.join(root, "PlayerProfiles", "playerprofiles8.lsf"),
        os.path.join(root, "graphicSettings.lsx"),
    ]


def boot_once(m, label):
    """Launch, watch, classify. Always leaves the game closed."""
    if not kill_game(m):
        say(f"  {label}: cannot close a previous bg3; aborting this boot")
        return "error"
    launch_at = time.time()
    before = {}
    for p in profile_files(m):
        try:
            before[p] = os.path.getmtime(p)
        except OSError:
            before[p] = 0
    subprocess.Popen(
        ["steam", "-applaunch", APPID],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    pid = None
    while time.time() - launch_at < 120:
        time.sleep(3)
        pid = game_pid()
        if pid:
            break
    if not pid:
        say(f"  {label}: the game never started (Steam side, not a mod)")
        return "nostart"
    say(f"  {label}: pid {pid}, watching")
    samples = []
    first = sample(pid)
    if not first:
        return "nostart"
    samples.append(first)
    while True:
        time.sleep(SAMPLE_SECS)
        cur = sample(pid)
        if not cur:
            say(f"  {label}: the process exited (crash, not a hang)")
            return "exit"
        samples.append(cur)
        touched = any(
            os.path.exists(p) and os.path.getmtime(p) > before.get(p, 0) + 1
            for p in profile_files(m)
        )
        budget_used = time.time() - launch_at > BOOT_BUDGET_SECS
        verdict = classify(samples, touched, budget_used)
        if verdict != "watching":
            d_rss = samples[-1]["rss_mb"] - samples[0]["rss_mb"]
            d_io = samples[-1]["io_mb"] - samples[0]["io_mb"]
            say(
                f"  {label}: {verdict.upper()} "
                f"(rss{d_rss:+d}MB io{d_io:+d}MB profile_written={touched})"
            )
            kill_game(m)
            return verdict


def read_state(m):
    s = m._load_settings()
    recs = m._bg3_records(s, DOMAIN)
    return (
        sorted(k for k, r in recs if r.get("enabled", True)),
        sorted(k for k, _r in recs),
    )


def apply_state(m, keys_on, all_keys):
    """Enable exactly keys_on. Kills the game first: the plugin refuses to
    touch mod state while it runs, and rightly so."""
    import asyncio

    if not kill_game(m):
        raise RuntimeError("bg3 will not close; refusing to guess at state")
    plugin = m.Plugin()

    async def go():
        for k in all_keys:
            want = k in keys_on
            s = m._load_settings()
            rec = s.get("installed", {}).get(DOMAIN, {}).get(k) or {}
            if bool(rec.get("enabled", True)) == want:
                continue
            r = await plugin.set_mod_enabled(
                GAME_DIR, "Mods", k, want, "bg3", DOMAIN
            )
            if not r.get("ok"):
                say(f"  toggle failed {k} -> {want}: {r.get('error')}")

    asyncio.run(go())


def save_wanted(keys):
    try:
        with open(WANTED_PATH, "w") as f:
            json.dump(sorted(keys), f)
    except OSError:
        pass


def check(m):
    say("environment check")
    ok = True
    if not os.path.isdir(m._bg3_mods_dir()):
        say("  FAIL: no BG3 Mods dir; launch the game once first")
        ok = False
    if not os.path.isfile(m._bg3_modsettings_path()):
        say("  FAIL: no modsettings.lsx; launch the game once first")
        ok = False
    if run_cmd(["which", "steam"], timeout=10) is None:
        say("  FAIL: steam not on PATH")
        ok = False
    enabled, all_keys = read_state(m)
    say(f"  {len(enabled)} enabled of {len(all_keys)} bg3 records")
    if bg3_pids():
        say("  NOTE: bg3 is running; the hunt would close it")
    say("  ready" if ok else "  NOT ready")
    return ok


def hunt(m, slug=None):
    enabled, all_keys = read_state(m)
    if slug:
        s = m._load_settings()
        recs = s.get("installed", {}).get(DOMAIN, {})
        enabled = sorted(
            k for k in enabled
            if (recs.get(k) or {}).get("collection_slug") == slug
        )
        say(f"scoped to collection {slug}: {len(enabled)} mods")
    if not enabled:
        say("nothing enabled to hunt")
        return
    save_wanted(enabled)
    restore = enabled

    def bail(*_a):
        say("interrupted: restoring the original mod set")
        try:
            apply_state(m, set(restore), all_keys)
        finally:
            say("restored")
        sys.exit(1)

    signal.signal(signal.SIGINT, bail)
    signal.signal(signal.SIGTERM, bail)

    try:
        say(f"control run: all {len(enabled)} mods OFF")
        apply_state(m, set(), all_keys)
        v = boot_once(m, "control")
        if v != "ok":
            say(f"ABORT: the game does not boot cleanly even with no mods "
                f"({v}). That is not a mod problem, so bisecting would "
                f"blame an innocent one.")
            return
        suspects = list(enabled)
        while len(suspects) > 1:
            half = len(suspects) // 2
            first, second = suspects[:half], suspects[half:]
            say(f"trying {len(first)} of {len(suspects)}")
            apply_state(m, set(first), all_keys)
            v = boot_once(m, f"first-{len(first)}")
            if v == "spin":
                suspects = first
                continue
            if v in ("inconclusive", "error", "nostart"):
                say(f"  cannot judge that half ({v}); stopping with "
                    f"{len(suspects)} candidates rather than guessing")
                break
            say(f"trying the other {len(second)}")
            apply_state(m, set(second), all_keys)
            v2 = boot_once(m, f"second-{len(second)}")
            if v2 == "spin":
                suspects = second
                continue
            if v2 in ("inconclusive", "error", "nostart"):
                say(f"  cannot judge that half ({v2}); stopping with "
                    f"{len(suspects)} candidates")
                break
            say("  NEITHER half spins alone, so this is an INTERACTION "
                "between mods, not one bad mod. Stopping.")
            break
        if len(suspects) == 1:
            say(f"CULPRIT: {suspects[0]}")
        else:
            say(f"NARROWED TO {len(suspects)}: {suspects}")
    finally:
        say("restoring the original mod set")
        apply_state(m, set(restore), all_keys)
        say("restored; done")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--hunt", action="store_true")
    ap.add_argument("--collection", metavar="SLUG")
    ap.add_argument("--restore-only", action="store_true")
    args = ap.parse_args()
    m = load_plugin()
    if args.restore_only:
        try:
            with open(WANTED_PATH) as f:
                keys = json.load(f)
        except (OSError, ValueError):
            say("no saved state to restore")
            return 1
        _enabled, all_keys = read_state(m)
        say(f"restoring {len(keys)} mods from {WANTED_PATH}")
        apply_state(m, set(keys), all_keys)
        say("restored")
        return 0
    if args.check or not (args.hunt or args.collection):
        return 0 if check(m) else 1
    if not check(m):
        return 1
    hunt(m, args.collection)
    return 0


if __name__ == "__main__":
    sys.exit(main())
