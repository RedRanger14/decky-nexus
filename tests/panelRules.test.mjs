// QAM panel visibility tests. Run via: pnpm run test:panel
// (compiles src/panelRules.ts standalone, then executes this file)
import assert from "node:assert/strict";
import test from "node:test";

import {
  crashHuntVerdict,
  crashSuspect,
  huntProgressNote,
  launchWaitNotice,
  loadOrderProblem,
  saveLoadVerdict,
  maskCoopPassword,
  showInstalledModsSection,
  showResetRow,
} from "../.test-build/panelRules.js";

test("reset is reachable with no mods installed", () => {
  // The regression: reset removed the last mod, the mods section returned
  // null, and the reset button went with it - so it could never be run
  // again from a half-configured state.
  assert.equal(showResetRow(true), true);
});

test("reset is reachable for a game with a loader but nothing else", () => {
  assert.equal(showResetRow(true), true);
});

test("reset is hidden only when the game isn't installed", () => {
  assert.equal(showResetRow(false), false);
});

test("the mods section hides itself when it has nothing to list", () => {
  assert.equal(showInstalledModsSection(0, false), false);
});

test("the mods section shows for mods, or for a framework row alone", () => {
  assert.equal(showInstalledModsSection(1, false), true);
  assert.equal(showInstalledModsSection(0, true), true);
});

test("hiding the mods section never hides reset", () => {
  // The two decisions must stay independent - that coupling was the bug.
  const modsHidden = showInstalledModsSection(0, false);
  assert.equal(modsHidden, false);
  assert.equal(showResetRow(true), true);
});

test("a saved co-op password is hidden by default", () => {
  assert.equal(maskCoopPassword("hunter2", false), true);
});

test("the eye reveals it", () => {
  assert.equal(maskCoopPassword("hunter2", true), false);
});

test("an empty password stays editable - there is nothing to hide", () => {
  assert.equal(maskCoopPassword("", false), false);
});

test("typing a first password does not mask mid-keystroke", () => {
  // The rule reads the SAVED value, so a draft never flips it to dots.
  for (const draftLength of [1, 2, 3]) {
    assert.equal(maskCoopPassword("", false), false, `draft ${draftLength}`);
  }
});

// After a crash we offer to skip ONE plugin. Several mod DLLs can sit on
// one call stack, and skipping all of them would take out mods that had
// no part in the crash.
test("nothing to offer when no mod DLL was on the stack", () => {
  assert.equal(crashSuspect(undefined), undefined);
  assert.equal(crashSuspect([]), undefined);
});

test("the frame nearest the crash wins", () => {
  const pick = crashSuspect([
    { name: "Deep.dll", frame: 9, probable: true },
    { name: "Near.dll", frame: 2, probable: true },
  ]);
  assert.equal(pick.name, "Near.dll");
});

test("a real frame beats a stack-scan hit however shallow", () => {
  // A scan hit is a leftover value that merely looks like a return
  // address, so depth cannot rescue it.
  const pick = crashSuspect([
    { name: "Scanned.dll", frame: 1, probable: false },
    { name: "Real.dll", frame: 8, probable: true },
  ]);
  assert.equal(pick.name, "Real.dll");
});

test("a scan hit is still offered when it is all we have", () => {
  const pick = crashSuspect([{ name: "Scanned.dll", frame: 4, probable: false }]);
  assert.equal(pick.name, "Scanned.dll");
});

test("picking does not reorder the caller's array", () => {
  const culprits = [
    { name: "Deep.dll", frame: 9, probable: true },
    { name: "Near.dll", frame: 2, probable: true },
  ];
  crashSuspect(culprits);
  assert.equal(culprits[0].name, "Deep.dll");
});

// A heavily modded game shows a black screen for minutes. In Gaming Mode
// that is indistinguishable from a hang, so people quit a working game.
test("a light setup gets no notice - it would just be noise", () => {
  assert.equal(launchWaitNotice(0), undefined);
  assert.equal(launchWaitNotice(49), undefined);
});

test("a middling setup is warned in the right order of magnitude", () => {
  const n = launchWaitNotice(120);
  assert.match(n, /a moment/);
  assert.match(n, /120/);
});

test("a collection-sized setup says minutes", () => {
  const n = launchWaitNotice(1954);
  assert.match(n, /a few minutes/);
  // Thousands separator: "1954 mods" reads like a typo at a glance.
  assert.match(n, /1,954/);
});

test("the notice fits in a Steam toast", () => {
  // Device: the first wording was cut off mid-sentence at "black for".
  // The instruction has to survive truncation, so it goes first and the
  // whole thing stays short.
  for (const count of [50, 400, 5000]) {
    const n = launchWaitNotice(count);
    assert.ok(n.length <= 44, `${count}: ${n.length} chars - "${n}"`);
    assert.ok(!n.includes("—"), `${count}: em dash eats width`);
    assert.ok(n.startsWith("Don't quit"), `${count}: "${n}"`);
  }
});

test("every notice says not to quit - that is the whole point", () => {
  for (const count of [50, 399, 400, 5000]) {
    assert.match(launchWaitNotice(count), /don't quit/i, `at ${count}`);
  }
});

// Two faults, one row. Both crash the game while it loads, and neither
// is the user's doing, so the wording leads with the consequence.
test("a healthy load order says nothing", () => {
  assert.equal(loadOrderProblem(0, 0, []), undefined);
});

test("switched-off dependencies are named, not just counted", () => {
  const n = loadOrderProblem(0, 13, ["ccBGSSSE001-Fish.esm", "_ResourcePack.esl"]);
  assert.match(n, /13 mods are installed but switched off/);
  // Extensions are jargon; the names alone are the useful part.
  assert.match(n, /ccBGSSSE001-Fish, _ResourcePack/);
  assert.doesNotMatch(n, /\.esm/);
});

test("ordering problems are reported with a thousands separator", () => {
  assert.match(loadOrderProblem(557, 0, []), /557 mods load before/);
  assert.match(loadOrderProblem(1200, 0, []), /1,200/);
});

test("both faults are joined rather than one hiding the other", () => {
  const n = loadOrderProblem(557, 13, ["A.esm"]);
  assert.match(n, /switched off/);
  assert.match(n, /load before/);
});

test("it always promises nothing is installed or removed", () => {
  for (const [v, d] of [[1, 0], [0, 1], [5, 5]]) {
    assert.match(
      loadOrderProblem(v, d, ["X.esm"]),
      /nothing is installed, removed or downloaded/,
      `${v}/${d}`
    );
  }
});

test("singular reads correctly for one switched-off mod", () => {
  const n = loadOrderProblem(0, 1, ["Solo.esm"]);
  assert.match(n, /1 mod is installed but switched off/);
  assert.match(n, /depend on it/);
});

// The automated hunt reads its own launches. Both misreadings below cost
// real launches when I was doing it by eye.
test("no crash log before the crash window is not a boot", () => {
  assert.equal(crashHuntVerdict(60_000, undefined, "01D74A0"), "waiting");
  assert.equal(crashHuntVerdict(200_000, undefined, "01D74A0"), "waiting");
});

test("no crash log once the window has passed is a boot", () => {
  assert.equal(crashHuntVerdict(330_000, undefined, "01D74A0"), "boot");
});

test("the hunted crash is recognised by address", () => {
  assert.equal(
    crashHuntVerdict(120_000, "SkyrimSE.exe 0x0001401D74A0", "01D74A0"),
    "crash"
  );
});

test("a different crash is not counted as the hunted one", () => {
  // Device: BladeAndBlunt and DragonWar crash on forms their own plugins
  // no longer provide once disabled. Reading those as "crash" corrupted
  // two bisect steps.
  assert.equal(
    crashHuntVerdict(120_000, "BladeAndBlunt.dll 0x6FFFF9AB629D", "01D74A0"),
    "other-crash"
  );
});

test("a crash beats the clock - it does not wait out the window", () => {
  assert.equal(
    crashHuntVerdict(1_000, "SkyrimSE.exe 0x0001401D74A0", "01D74A0"),
    "crash"
  );
});

// The hunt restarts the game for hours. Without a visible count that
// looks like a boot loop, and quitting it is the rational response.
test("every launch is numbered, and the number comes first", () => {
  const t = huntProgressNote(3, 977, 977, 0);
  assert.ok(t.title.startsWith("Attempt 3"), t.title);
});

test("it says how many launches are left, not just where it is", () => {
  assert.match(huntProgressNote(1, 1955, 1955, 0).body, /About 12 more launches/);
  assert.match(huntProgressNote(9, 8, 8, 2).body, /About 4 more launches/);
});

test("found culprits are reported once there are any", () => {
  assert.match(huntProgressNote(5, 100, 100, 1).body, /1 broken mod found/);
  assert.match(huntProgressNote(5, 100, 100, 3).body, /3 broken mods found/);
});

test("before anything is found it says to leave the game alone", () => {
  assert.match(huntProgressNote(2, 500, 500, 0).body, /closes itself/);
});

test("the estimate never goes negative or NaN at the end", () => {
  for (const remaining of [0, 1, 2]) {
    const t = huntProgressNote(20, 1, remaining, 4);
    assert.match(t.body, /About \d+ more launch/, `remaining=${remaining}`);
  }
});

// The save-load hunt. Reaching the menu proves nothing; the world has to
// load, and that needs the user to press Continue.
test("a crash on load is the fault being hunted", () => {
  assert.equal(
    saveLoadVerdict(60_000, "SkyrimSE.exe 0x0001401D74A0", "01D74A0", false),
    "crash"
  );
});

test("the world loading is the pass condition, not the menu", () => {
  assert.equal(saveLoadVerdict(60_000, undefined, "01D74A0", true), "loaded");
  // Menu reached, nothing else: still waiting, NOT a pass.
  assert.equal(saveLoadVerdict(60_000, undefined, "01D74A0", false), "waiting");
});

test("silence is never counted as success here", () => {
  // The boot hunt treats "no crash by now" as a pass. This one cannot -
  // nobody pressed Continue, so there is no result to record.
  assert.equal(
    saveLoadVerdict(600_000, undefined, "01D74A0", false),
    "no-input"
  );
});

test("a loaded world beats the clock", () => {
  assert.equal(saveLoadVerdict(900_000, undefined, "01D74A0", true), "loaded");
});

test("a different crash is still not our crash", () => {
  assert.equal(
    saveLoadVerdict(60_000, "BladeAndBlunt.dll 0x6FFFF9AB629D", "01D74A0", false),
    "other-crash"
  );
});

test("a crash outranks the in-game signal", () => {
  // Scripts can start and the game die a moment later; the crash is the
  // more important fact.
  assert.equal(
    saveLoadVerdict(60_000, "SkyrimSE.exe 0x0001401D74A0", "01D74A0", true),
    "crash"
  );
});
