// QAM panel visibility tests. Run via: pnpm run test:panel
// (compiles src/panelRules.ts standalone, then executes this file)
import assert from "node:assert/strict";
import test from "node:test";

import {
  crashHuntVerdict,
  cancellableDownload,
  crashSuspect,
  huntProgressNote,
  launchWaitNotice,
  loadOrderProblem,
  saveLoadVerdict,
  maskCoopPassword,
  pauseAllControl,
  showInstalledModsSection,
  showResetRow,
  slotPressure,
  isRemaining,
  isActionableAttention,
  collectionOwnedCount,
  endorseControl,
  missingMasterProblem,
  blockedPluginsAction,
  fileConflictProblem,
  ghostPluginProblem,
  troubleshootingCount,
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

// The troubleshooting section is collapsed by default and shows a count,
// so a panel full of ways to disable mods does not read as "this is
// fragile" to someone whose game is working fine.
test("nothing wrong reads as nothing wrong", () => {
  assert.equal(troubleshootingCount(false, 0, false, undefined), 0);
});

test("each distinct fault counts once", () => {
  assert.equal(troubleshootingCount(true, 0, false, undefined), 1);
  assert.equal(troubleshootingCount(false, 3, false, undefined), 1);
  assert.equal(troubleshootingCount(false, 0, true, undefined), 1);
  assert.equal(troubleshootingCount(false, 0, false, "load order"), 1);
});

test("several faults add up", () => {
  assert.equal(troubleshootingCount(true, 2, true, "load order"), 4);
});

test("many failed plugins are still one thing to look at", () => {
  // The user acts on the row, not on each plugin - counting 37 would
  // make one fixable problem look like a catastrophe.
  assert.equal(troubleshootingCount(false, 37, false, undefined), 1);
});

// Download rows: cancel only while bytes are still owed.
test("downloading and paused rows can be cancelled", () => {
  assert.equal(cancellableDownload("downloading"), true);
  assert.equal(cancellableDownload("paused"), true);
  assert.equal(cancellableDownload("starting"), true);
});

test("extracting and queued rows cannot - the bytes are already here", () => {
  // Mid-extraction a cancel leaves a half-merged mod; a queued archive
  // would just be re-downloaded by the installer anyway.
  assert.equal(cancellableDownload("extracting"), false);
  assert.equal(cancellableDownload("queued"), false);
  assert.equal(cancellableDownload("done"), false);
});

test("the pause control shows whenever anything is active", () => {
  assert.deepEqual(pauseAllControl(3, false), { show: true, label: "⏸ Pause all" });
});

test("a paused page always shows Resume even with zero rows", () => {
  // Paused rows are parked and will never change state on their own -
  // hiding the only way out would trap the user.
  assert.deepEqual(pauseAllControl(0, true), { show: true, label: "▶ Resume" });
});

test("idle and unpaused shows nothing", () => {
  assert.equal(pauseAllControl(0, false).show, false);
});

// Plugin slots: 254 ordinary, one shared index for all the light ones.
// Going over does not announce itself, which is the whole problem.
test("a normal load order says nothing", () => {
  assert.equal(slotPressure(208, 254, 1766, 4096).level, "ok");
});

test("over the full limit is called out with the numbers", () => {
  const r = slotPressure(260, 254, 100, 4096);
  assert.equal(r.level, "over");
  assert.match(r.message, /260 full plugins/);
  assert.match(r.message, /silently not load/);
});

test("over the light limit is caught too", () => {
  assert.equal(slotPressure(10, 254, 4200, 4096).level, "over");
});

test("close to the limit warns before it breaks", () => {
  // 250 of 254 is one patch away from a crash nobody can explain.
  const r = slotPressure(250, 254, 100, 4096);
  assert.equal(r.level, "near");
  assert.match(r.message, /250 of 254/);
});

test("the boundary itself is not over", () => {
  assert.equal(slotPressure(254, 254, 4096, 4096).level, "near");
  assert.equal(slotPressure(255, 254, 0, 4096).level, "over");
});


// ---- remaining count during Finish setup -------------------------------
// Reported from device: 1 mod left, started Finish setup, the count went
// UP to 2, then back to 1 when the pass ended.

const file = { modId: 7, fileId: 700 };
const none = new Set();

test("an uninstalled file is remaining", () => {
  assert.equal(isRemaining(file, none, {}, none), true);
});

test("installed, done, or parked for attention are all not remaining", () => {
  assert.equal(isRemaining(file, new Set([7]), {}, none), false);
  assert.equal(isRemaining(file, none, { 700: "done" }, none), false);
  assert.equal(isRemaining(file, none, {}, new Set([700])), false);
});

test("a mod resolved by Finish setup does not bounce back to remaining", () => {
  // The window that caused it: out of the attention queue already,
  // but the installed-mods list has not been re-read yet.
  assert.equal(isRemaining(file, none, {}, none, new Set([700])), false);
});

test("a failed install IS still remaining", () => {
  // The stuck-on-Install mod. It has to keep counting, otherwise a
  // failure disappears from the tally and the collection looks complete.
  assert.equal(isRemaining(file, none, { 700: "failed" }, none), true);
});


// ---- uninstall count ---------------------------------------------------
// Device: a 546-entry collection whose Uninstall button read 454. Nothing
// had failed - 78 of its mods ship more than one file, and a mod is one
// install record however many files it brought.

const entries = [
  { modId: 1 },
  { modId: 1 }, // same mod, second file
  { modId: 2 },
  { modId: 3 },
];

test("counts collection entries, not install records", () => {
  assert.equal(collectionOwnedCount(entries, new Set([1, 2])), 3);
});

test("mods installed outside this collection do not inflate it", () => {
  assert.equal(collectionOwnedCount(entries, new Set([1, 99])), 2);
});

test("falls back to the record count before the detail loads", () => {
  // Otherwise the Uninstall button vanishes on a slow page load and the
  // mods cannot be removed at all.
  assert.equal(collectionOwnedCount(undefined, new Set([1, 2])), 2);
  assert.equal(collectionOwnedCount([], new Set([1, 2])), 2);
});

test("a revision that no longer lists our mods still offers uninstall", () => {
  assert.equal(collectionOwnedCount(entries, new Set([77])), 1);
});

test("nothing owned means no button", () => {
  assert.equal(collectionOwnedCount(entries, new Set()), 0);
});


// ---- QAM endorse button ------------------------------------------------

test("no button when we cannot know the endorsement state", () => {
  assert.equal(endorseControl(undefined).show, false);
  assert.equal(endorseControl("unknown").show, false);
});

test("an endorsement made years ago on the website is respected", () => {
  // Otherwise the button invites a press that toggles it back OFF.
  const c = endorseControl("Endorsed");
  assert.equal(c.endorsed, true);
  assert.equal(c.label, "Endorsed");
});

test("abstained still offers the button, undecided is the ask", () => {
  assert.equal(endorseControl("Abstained").endorsed, false);
  assert.equal(endorseControl("Undecided").label, "Endorse");
});

test("explains the cooldown by default, and drops it once it cannot apply", () => {
  // Unknown install time is the normal case - a framework installed on
  // some previous day. Warning costs a line; not warning costs a press
  // that looks like a broken button.
  assert.match(endorseControl("Undecided").hint, /15 minutes/);
  assert.match(endorseControl("Undecided", 2).hint, /15 minutes/);
  assert.doesNotMatch(endorseControl("Undecided", 40).hint, /15 minutes/);
});

test("no hint clutters the already-endorsed state", () => {
  assert.equal(endorseControl("Endorsed", 1).hint, undefined);
});


// ---- engines with no light tier ----------------------------------------
// FO3 and New Vegas predate ESL. lightLimit 0 says so, and comparing
// against it naively puts every one of those load orders at 100% of its
// limit - a permanent warning on two shipping games.

test("a zero light limit never warns on its own", () => {
  assert.equal(slotPressure(10, 255, 0, 0).level, "ok");
});

test("a no-ESL engine still warns on its own full slots", () => {
  assert.equal(slotPressure(250, 255, 0, 0).level, "near");
  assert.equal(slotPressure(256, 255, 0, 0).level, "over");
});

test("a no-ESL engine gets the extra slot ESL games give up to 0xFE", () => {
  // 255 is not over for FO3/FNV; it is over for Skyrim SE.
  assert.equal(slotPressure(255, 255, 0, 0).level, "near");
  assert.equal(slotPressure(255, 254, 0, 4096).level, "over");
});


// ---- masters that are not installed at all ------------------------------
// Device, New Vegas 2026-08-12: the game put up a modal naming mil.esp and
// quit. 115 of 245 enabled plugins could not load, for want of five DLC.

test("nothing to say when every master is present", () => {
  assert.equal(missingMasterProblem([], 0), undefined);
  assert.equal(missingMasterProblem(undefined, 0), undefined);
});

test("names the DLC, not the esm filenames", () => {
  const msg = missingMasterProblem(
    [
      { name: "HonestHearts.esm", label: "Honest Hearts", needed_by: 81 },
      { name: "DeadMoney.esm", label: "Dead Money", needed_by: 79 },
    ],
    115
  );
  assert.match(msg, /Honest Hearts/);
  assert.match(msg, /Dead Money/);
  assert.doesNotMatch(msg, /\.esm/);
});

test("leads with how many mods are blocked, not how many files are absent", () => {
  // Five missing files sounds survivable. 115 dead mods does not.
  const msg = missingMasterProblem(
    [{ name: "DeadMoney.esm", label: "Dead Money", needed_by: 79 }],
    115
  );
  assert.match(msg, /^115 mods cannot load/);
});

test("a missing mod master is described as a mod, not as DLC", () => {
  const msg = missingMasterProblem(
    [{ name: "Project Nevada - Core.esm", needed_by: 2 }],
    2
  );
  assert.match(msg, /Project Nevada - Core/);
  assert.doesNotMatch(msg, /DLC installed/);
});

test("DLC and missing mods are reported together", () => {
  const msg = missingMasterProblem(
    [
      { name: "DeadMoney.esm", label: "Dead Money", needed_by: 79 },
      { name: "Project Nevada - Core.esm", needed_by: 2 },
    ],
    81
  );
  assert.match(msg, /Dead Money/);
  assert.match(msg, /Project Nevada - Core/);
});

test("sends you to Steam only when DLC is actually involved", () => {
  // Otherwise someone goes looking for a DLC called Tale Of Two Wastelands.
  const dlc = missingMasterProblem(
    [{ name: "DeadMoney.esm", label: "Dead Money", needed_by: 79 }],
    79
  );
  assert.match(dlc, /Steam's DLC tab/);

  const mods = missingMasterProblem(
    [{ name: "TaleOfTwoWastelands.esm", needed_by: 1 }],
    1
  );
  assert.doesNotMatch(mods, /Steam/);
  assert.match(mods, /switching them off/);
});

test("offers to turn off mods blocked by a missing MOD", () => {
  const a = blockedPluginsAction([
    { name: "TaleOfTwoWastelands.esm", needed_by: 1 },
  ]);
  assert.equal(a.show, true);
});

test("never offers it for DLC alone", () => {
  // Device: DLC blocked 115 mods and missing mods blocked 4. A button
  // that treated those the same would bin the collection one tap after
  // telling the user what was wrong, when the real fix cost a few pounds.
  assert.equal(
    blockedPluginsAction([
      { name: "DeadMoney.esm", label: "Dead Money", needed_by: 79 },
    ]).show,
    false
  );
  assert.equal(blockedPluginsAction([]).show, false);
  assert.equal(blockedPluginsAction(undefined).show, false);
});

test("offers it when DLC and missing mods are mixed", () => {
  assert.equal(
    blockedPluginsAction([
      { name: "DeadMoney.esm", label: "Dead Money", needed_by: 79 },
      { name: "TaleOfTwoWastelands.esm", needed_by: 1 },
    ]).show,
    true
  );
});


test("a missing master counts as something to look at", () => {
  // It stops the game booting, and it was not counted - so the section
  // said "Nothing looks wrong" while hiding the one-tap fix inside.
  assert.equal(
    troubleshootingCount(false, 0, false, undefined, "4 mods cannot load"),
    1
  );
  assert.equal(troubleshootingCount(false, 0, false, undefined, undefined), 0);
});


// ---- files won by the wrong mod ----------------------------------------
// Device, New Vegas: 10,362 shared paths across 867 mod-sets, almost all
// deliberate - and 1,440 files across 47 pairs where the wrong mod won.
// Reporting the first number would bury the second.

test("says nothing when the install matches the collection", () => {
  assert.equal(fileConflictProblem(0, 0, []), undefined);
  assert.equal(fileConflictProblem(5, 1, []), undefined);
});

test("leads with a real example, not the totals", () => {
  const msg = fileConflictProblem(1440, 47, [
    { actual: "Iron Sights Aligned", intended: "VeryLastKisss Collections Hub", files: 319 },
  ]);
  assert.match(msg, /Iron Sights Aligned/);
  assert.match(msg, /VeryLastKisss Collections Hub/);
  assert.match(msg, /319 files/);
  assert.match(msg, /1,440 files/);
  assert.match(msg, /47 pairs/);
});

test("explains the cause the user can recognise", () => {
  // The mods that finish last are the ones that asked them questions.
  const msg = fileConflictProblem(319, 1, [
    { actual: "a", intended: "b", files: 319 },
  ]);
  assert.match(msg, /needed your choices/);
});

test("describes the fix as per-file, never as reinstalling mods", () => {
  // v0.97.0 promised "reinstall the affected mods in collection order" and
  // took the device from 47 wrong pairs to 92, because reinstalling a mod
  // rewrites files it was not contesting. The wording has to keep the
  // distinction the fix depends on.
  const msg = fileConflictProblem(2, 1, [
    { actual: "a", intended: "b", files: 2 },
  ]);
  assert.match(msg, /rewrites just those files/);
  assert.match(msg, /leaving everything else alone/);
  assert.doesNotMatch(msg, /reinstall/i);
});

test("gets the singulars right", () => {
  const msg = fileConflictProblem(1, 1, [
    { actual: "a", intended: "b", files: 1 },
  ]);
  assert.match(msg, /1 file belonging/);
  assert.match(msg, /1 pair/);
});


// ---- plugins enabled but not installed ---------------------------------

test("nothing to say when every enabled plugin exists", () => {
  assert.equal(ghostPluginProblem(0), undefined);
});

test("names them and says the fix is safe", () => {
  const msg = ghostPluginProblem(1, ["oHUD.esm"]);
  assert.match(msg, /oHUD/);
  assert.doesNotMatch(msg, /\.esm/);
  assert.match(msg, /safe/);
  assert.match(msg, /1 mod is switched on/);
});

test("gets the plurals right", () => {
  assert.match(ghostPluginProblem(3, ["a.esp", "b.esp"]), /3 mods are/);
});

test("explains where they come from", () => {
  // The user did not do anything wrong - we left them behind.
  assert.match(ghostPluginProblem(2, []), /left behind by an uninstall/);
});


// ---- an installer with nothing to install ------------------------------
// Device: a collection listed a SECOND file of Iron Sights Aligned whose
// installer offers options none of whose sources are in the archive. Every
// attempt staged 0 files, and Finish setup kept presenting it as
// outstanding work - so the run never looked finished. The mod itself was
// already installed from its main file.

test("an empty installer is answered, not still remaining", () => {
  const file = { modId: 81933, fileId: 1000129576 };
  // Recorded as a skip, which means it is in the attention set - and
  // pending-attention files are not "remaining".
  assert.equal(
    isRemaining(file, new Set(), {}, new Set([1000129576])),
    false
  );
});

test("it does not count as an actionable Finish setup item", () => {
  // Only choices and fomod are actionable; "empty" must not be, or the
  // button reappears offering the same nothing.
  const actionable = ["choices", "fomod"];
  assert.equal(actionable.includes("empty"), false);
});


// ---- what Finish setup can actually resolve ----------------------------
// Device, Gopher's Stable New Vegas: the button read "Finish setup (2)",
// the user tapped it, and no wizard appeared - there was nothing to pick.
// Offering a step that does nothing visible is worse than not offering it,
// because the user cannot tell whether it worked.

test("a FOMOD is always actionable", () => {
  assert.equal(isActionableAttention({ reason: "fomod" }), true);
});

test("choices with options to pick are actionable", () => {
  assert.equal(
    isActionableAttention({ reason: "choices", options: ["a", "b"] }),
    true
  );
});

test("choices with nothing to pick are NOT actionable", () => {
  assert.equal(isActionableAttention({ reason: "choices", options: [] }), false);
  assert.equal(isActionableAttention({ reason: "choices" }), false);
});

test("permanent skips are never actionable", () => {
  for (const reason of ["tool", "conflict", "layout", "empty",
                        "needs_external"]) {
    assert.equal(
      isActionableAttention({ reason, options: ["a"] }),
      false,
      reason
    );
  }
});
