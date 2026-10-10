"""Boot Kingdom Come: Deliverance on the device and drive it into the game,
unattended, saving screenshots as evidence.

Runs ON the device (beside bg3pad.py, the uinput pad daemon):

  python3 kcdcrucible.py boot            launch, wait for the menu, print the
                                         game's own mod counts from kcd.log
  python3 kcdcrucible.py newgame         New Game > Normal, skip the cinematic
                                         (hold B), into Henry's first dialogue
  python3 kcdcrucible.py press A [ms]    one pad press (HOLD_X 2000 etc.)
  python3 kcdcrucible.py shot NAME       screenshot to /tmp/kcd-NAME.png
  python3 kcdcrucible.py quit            close the game and the launcher

Learned on the Legion, 2026-10-08:
* The game process's comm is "Main" (its main thread), not KingdomCome.exe.
* kcd.log in the game folder says "[Mod] N mods loaded from mods/" and
  "Pak 'mods\\<mod>\\data\\x.pak' is opened" per mod pak.
* First launch shows Steam's "Magnifier Tool" tip and the game's EULA.
* The opening cinematic: B skips a part, B HELD (6 s) skips it all.
* Memory: ~3.1 GB at the main menu.
"""
import os
import re
import subprocess
import sys
import time

APPID = "379430"
GAME = "/home/deck/.local/share/Steam/steamapps/common/KingdomComeDeliverance"
PAD = "/home/deck/bg3pad.py"
MENU_RSS_KB = 3_000_000


def sh(args, timeout=30):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def pids(names=("Main",)):
    out = sh(["ps", "-eo", "pid=,comm="])
    found = []
    for line in (out.stdout if out else "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip() in names:
            found.append(int(parts[0]))
    return found


def rss_kb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def pad(*cmds):
    sh(["python3", PAD, "send", *cmds], timeout=30)


def ensure_pad():
    st = sh(["systemctl", "--user", "is-active", "bg3pad"])
    if st and st.stdout.strip() == "active":
        return
    open("/tmp/bg3pad.cmd", "w").close()   # it replays the file on start
    sh(["systemd-run", "--user", "--unit=bg3pad", "--collect", "python3", PAD, "daemon"])
    time.sleep(2)


def shot(name):
    path = f"/tmp/kcd-{name}.png"
    try:
        os.remove(path)
    except OSError:
        pass
    sh(["gamescopectl", "screenshot", path])
    for _ in range(10):
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            break
        time.sleep(1)
    print(f"shot {path}", flush=True)
    return path


def log_summary():
    try:
        text = open(os.path.join(GAME, "kcd.log"), encoding="utf-8", errors="replace").read()
    except OSError:
        return "no kcd.log"
    loaded = re.findall(r"\[Mod\] (\d+) mods loaded from mods/", text)
    paks = len(re.findall(r"Pak 'mods\\", text, re.I))
    engine = len(re.findall(r"Pak 'engine\\[^']*_mod\.pak' is opened", text, re.I))
    errors = [l for l in text.splitlines()
              if re.search(r"\[Mod\].*(error|fail|cannot)", l, re.I)]
    return (f"mods loaded {loaded[-1] if loaded else '?'}, mod paks opened {paks}, "
            f"engine mod paks {engine}, mod errors {len(errors)}"
            + ("".join("\n  " + e[:160] for e in errors[:5])))


def boot():
    if pids():
        print("already running")
    else:
        ensure_pad()
        sh(["systemd-run", "--user", "--collect", "steam", "-applaunch", APPID])
    t0 = time.time()
    while time.time() - t0 < 300:
        p = pids()
        r = rss_kb(p[0]) if p else None
        if r and r > MENU_RSS_KB:
            break
        time.sleep(5)
    else:
        print("game never reached menu memory")
        return
    time.sleep(15)
    print(f"menu after {time.time() - t0:.0f}s, rss {r // 1024} MB")
    print(log_summary())
    shot("menu")


def newgame():
    ensure_pad()
    pad("A")                 # New Game
    time.sleep(4)
    pad("A")                 # Normal Mode
    time.sleep(35)
    pad("HOLD_B", "6000")    # skip the whole cinematic
    time.sleep(25)
    shot("dialogue")


def quit_game():
    for _ in range(3):
        live = pids(("Main", "steam.exe", "reaper"))
        if not live:
            break
        for p in live:
            try:
                os.kill(p, 9)
            except OSError:
                pass
        time.sleep(4)
    print("closed" if not pids() else "STILL RUNNING")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "boot"
    if cmd == "boot":
        boot()
    elif cmd == "newgame":
        newgame()
    elif cmd == "press":
        ensure_pad()
        pad(*sys.argv[2:])
    elif cmd == "shot":
        shot(sys.argv[2])
    elif cmd == "quit":
        quit_game()
    elif cmd == "log":
        print(log_summary())
