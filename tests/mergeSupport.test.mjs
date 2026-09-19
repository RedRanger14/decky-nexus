// Copy and rules for the Mass Effect merge mod step. Run via:
// pnpm run test:merge
import assert from "node:assert/strict";
import test from "node:test";

import {
  MERGE_NEEDED_NOTE,
  MERGE_STEP_BUSY,
  MERGE_STEP_EXPLAINER,
  MERGE_STEP_STAGES,
  MERGE_STEP_TITLE,
  managedRequirementNote,
  mergeStepFailure,
  mergeStepSummary,
  offersMergeSupport,
} from "../.test-build/mergeSupport.js";

test("the step is only offered to the game that needs it", () => {
  assert.equal(offersMergeSupport("masseffect"), true);
  for (const mode of ["folder", "dataDir", "me3", "cp77", undefined, ""]) {
    assert.equal(offersMergeSupport(mode), false, String(mode));
  }
});

test("the summary says what is missing before it is installed", () => {
  const before = mergeStepSummary(false, "");
  assert.match(before, /Not installed/);
  // It must say WHY someone would want it, not just that it is absent.
  assert.match(before, /community patches/i);
});

test("the summary names the version once it is installed", () => {
  assert.match(mergeStepSummary(true, "9.2.1.137"), /9\.2\.1\.137/);
  assert.match(mergeStepSummary(true, "9.2.1.137"), /Mod Manager/);
  // A build that reports no version still reads as installed.
  assert.match(mergeStepSummary(true, ""), /installed/);
  assert.doesNotMatch(mergeStepSummary(true, ""), /undefined|null/);
});

test("the explainer answers what, whose, how big, and why a button", () => {
  const t = MERGE_STEP_EXPLAINER;
  // What it is for.
  assert.match(t, /community patches/i);
  assert.match(t, /editing the game's own files/i);
  // Whose it is. Naming the author is the point of informed consent.
  assert.match(t, /ME3Tweaks Mod Manager/);
  assert.match(t, /Mgamerz/);
  // How big, since it lands on the user's device.
  assert.match(t, /200 MB/);
  // That it is somebody else's program, said plainly.
  assert.match(t, /somebody else's program/i);
  // And that it is reversible, which is what makes the decision cheap.
  assert.match(t, /remove it/i);
});

test("the explainer never pretends the plugin wrote it", () => {
  assert.doesNotMatch(MERGE_STEP_EXPLAINER, /our tool|we built|plugin's own tool/i);
});

test("every stage has text, because silence in a long job reads as a hang", () => {
  for (const stage of ["lookup", "download", "extract", "stage", "configure", "runtime"]) {
    assert.ok(MERGE_STEP_STAGES[stage], stage);
    assert.ok(MERGE_STEP_STAGES[stage].length > 5, stage);
  }
  // The download is the long one, so it says how long to expect.
  assert.match(MERGE_STEP_STAGES.download, /200 MB/);
});

test("a failure is kept and shown rather than left to a toast", () => {
  assert.equal(mergeStepFailure(""), "");
  assert.equal(mergeStepFailure("   "), "");
  const msg = mergeStepFailure("Network error: ClientError");
  assert.match(msg, /Last attempt failed/);
  assert.match(msg, /Network error/);
});

test("the wait is stated up front, because ten minutes of silence reads as a hang", () => {
  // Measured on a Legion Go 2: 610 seconds from a clean prefix, nearly
  // all of it the 200 MB download.
  assert.match(MERGE_STEP_EXPLAINER, /ten minutes/i);
  assert.match(MERGE_STEP_BUSY, /ten minutes/i);
  // And that they need not sit and watch it.
  assert.match(MERGE_STEP_EXPLAINER, /leave this\s+menu/i);
});

test("no copy here uses an em dash", () => {
  const all = [
    MERGE_STEP_TITLE,
    MERGE_STEP_BUSY,
    MERGE_STEP_EXPLAINER,
    MERGE_NEEDED_NOTE,
    mergeStepSummary(false, ""),
    mergeStepSummary(true, "9.2.1"),
    mergeStepFailure("x"),
    ...Object.values(MERGE_STEP_STAGES),
  ];
  // Written as an escape so this guard never trips over itself.
  for (const s of all) assert.doesNotMatch(s, /\u2014/, s);
});

test("the needed note says what happens without the step, not just that it is missing", () => {
  assert.match(MERGE_NEEDED_NOTE, /skipped/i);
  assert.match(MERGE_NEEDED_NOTE, /note/i);
});

test("a tool the plugin handles says the right thing, per game", () => {
  // The generic line is for desktop managers the plugin REPLACES.
  assert.match(managedRequirementNote(), /not needed/);
  // Mass Effect overrides it, because there the plugin does not replace
  // ME3Tweaks Mod Manager, it installs and drives it. Telling someone a
  // community patch's requirement is "not needed" would be a lie: the
  // patch genuinely cannot install without it.
  const override = "installed by the Merge mod support step";
  assert.equal(managedRequirementNote(override), override);
  assert.doesNotMatch(managedRequirementNote(override), /not needed/);
  assert.equal(managedRequirementNote(""), managedRequirementNote());
});
