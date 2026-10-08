"""Boot the CURRENT mod set into Story and check it is playable, the way a
person would see it: a lit frame, input reaching the game, and the world
not frozen. Saves the frames for a human look.

  python3 rdr2_playcheck.py N
"""
import shutil
import subprocess
import sys
import time

sys.path.insert(0, "/home/deck")
import rdr2boothunt as h  # noqa: E402

h.LOOK = False   # this script does its own looking


def shot(path):
    grey = h.screen_grey()
    shutil.copy("/tmp/rdr2hunt-shot.png", path)
    return grey


def lit(grey):
    return sum(1 for b in grey if b > h.BRIGHT_LEVEL)


def diff(a, b):
    if not a or not b or len(a) != len(b):
        return 0
    return sum(1 for x, y in zip(a, b) if abs(x - y) > 12)


def one(i):
    label = f"play-{i}"
    if not h.kill_game():
        return "error"
    subprocess.Popen(["steam", "-applaunch", h.APPID],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    pid = None
    while time.time() - t0 < h.START_BUDGET_SECS and not pid:
        time.sleep(5)
        pid = h.game_pid()
    if not pid:
        h.say(f"  {label}: never started")
        return "nostart"
    seen = []
    t = time.time()
    while time.time() - t < h.TITLE_WAIT_SECS:
        time.sleep(5)
        cur = h.rss_kb(pid)
        if cur is None:
            break
        seen.append(cur)
        if h.title_settled(seen):
            break
    title = shot(f"/tmp/{label}-0-title.png")
    h.say(f"  {label}: title screen {lit(title)} of {len(title)} lit")
    base = h.rss_kb(pid) or 0
    for _ in range(h.PRESS_TRIES):
        h.pad("A")
        time.sleep(h.PRESS_SETTLE_SECS)
        now = h.rss_kb(pid)
        if now is None or h.press_landed(base, now):
            break
    samples = []
    t = time.time()
    while True:
        alive = h.game_pid() == pid
        cur = h.rss_kb(pid) if alive else None
        if cur is not None:
            samples.append(cur)
        v = h.classify(samples, alive, time.time() - t > h.BOOT_BUDGET_SECS)
        if v != "watching":
            break
        time.sleep(h.SAMPLE_SECS)
    if v != "ok":
        h.say(f"  {label}: {v.upper()} before the world")
        h.kill_game()
        return v
    time.sleep(h.LOOK_AFTER_SECS)
    a = shot(f"/tmp/{label}-1-world.png")
    h.pad("A")                      # dismiss the trainer's welcome box
    time.sleep(6)
    b = shot(f"/tmp/{label}-2-after-A.png")
    time.sleep(20)
    alive = h.game_pid() == pid
    c = shot(f"/tmp/{label}-3-later.png") if alive else b""
    h.say(f"  {label}: world {lit(a)} lit; after A {lit(b)} lit, "
          f"{diff(a, b)} pixels changed; 20s later {'alive' if alive else 'GONE'}, "
          f"{diff(b, c)} changed")
    verdict = ("black" if h.frame_is_black(a) or h.frame_is_black(b)
               else "exit" if not alive
               else "frozen" if diff(b, c) == 0 and diff(a, b) == 0
               else "playable")
    h.say(f"RESULT {label}: {verdict.upper()}")
    h.kill_game()
    return verdict


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    if not h.ensure_pad():
        raise SystemExit("pad daemon will not start")
    results = [one(i) for i in range(1, n + 1)]
    h.say(f"PLAYCHECK {results}")
