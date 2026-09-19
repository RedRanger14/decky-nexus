// Copy and rules for the Mass Effect merge mod step. Run via:
// pnpm run test:merge
import assert from "node:assert/strict";
import test from "node:test";

import {
  MERGE_NEEDED_NOTE,
  MERGE_RUNTIME_BUDGET_MS,
  MERGE_STEP_BUSY,
  MERGE_STEP_EXPLAINER,
  MERGE_STEP_STAGES,
  MERGE_STEP_TITLE,
  managedRequirementNote,
  mergeStepFailure,
  mergeStepPercent,
  mergeStepProgress,
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
  // It leads with the step's NAME now, because the Field label is the
  // step number, the same shape as every other step in the panel.
  assert.match(before, /^Merge mod support:/);
  assert.match(before, /not installed/i);
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
  assert.match(t, /70 MB/);
  // That it is somebody else's program, said plainly.
  assert.match(t, /somebody else's program/i);
  // And that it is reversible, which is what makes the decision cheap.
  assert.match(t, /remove it/i);
});

test("the explainer never pretends the plugin wrote it", () => {
  assert.doesNotMatch(MERGE_STEP_EXPLAINER, /our tool|we built|plugin's own tool/i);
});

test("every phase the backend emits has text", () => {
  // Keyed by the phase name the backend actually sends, so a rename on
  // one side cannot leave the button blank on the other.
  for (const phase of ["queued", "downloading", "extracting", "installing", "done"]) {
    assert.ok(MERGE_STEP_STAGES[phase], phase);
    assert.ok(MERGE_STEP_STAGES[phase].length > 3, phase);
  }
});

test("a failure is kept and shown rather than left to a toast", () => {
  assert.equal(mergeStepFailure(""), "");
  assert.equal(mergeStepFailure("   "), "");
  const msg = mergeStepFailure("Network error: ClientError");
  assert.match(msg, /Last attempt failed/);
  assert.match(msg, /Network error/);
});

test("the wait is stated up front, because ten minutes of silence reads as a hang", () => {
  // Measured on a Legion Go 2: 605 seconds from a clean prefix, and the
  // 68 MB download was about ten of them. The rest is the silent Windows
  // runtime installer.
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
    mergeStepProgress("installing", 80),
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

// --- progress feedback ---------------------------------------------------
// Michael: "Up to 10 minutes with no visual feedback is not accetpable".
// The backend reports every phase under Mod Manager's own Nexus id, so the
// button can show real progress instead of a spinner.

test("the download is the only phase that claims a percentage", () => {
  // It is the only one with real numbers. A made-up percentage on the
  // others would be worse than none.
  assert.match(mergeStepProgress("downloading", 42), /42%/);
  assert.doesNotMatch(mergeStepProgress("extracting", 42), /%/);
  assert.doesNotMatch(mergeStepProgress("installing", 60), /%/);
  assert.doesNotMatch(mergeStepProgress("queued", 0), /%/);
});

test("every phase says what it is doing", () => {
  for (const phase of ["queued", "downloading", "extracting", "installing"]) {
    const t = mergeStepProgress(phase, 10);
    assert.ok(t.length > 6, phase);
    assert.match(t, /Mod Manager|Finding|Unpacking|Setting up/i, phase);
  }
});

test("an unknown or missing phase still says something", () => {
  // Never a blank button: silence is the thing being fixed.
  assert.ok(mergeStepProgress(undefined, 0).length > 6);
  assert.ok(mergeStepProgress("something-new", 0).length > 6);
});

test("a download percentage is clamped and rounded", () => {
  assert.match(mergeStepProgress("downloading", 42.6), /43%/);
  assert.match(mergeStepProgress("downloading", -5), /0%/);
  assert.match(mergeStepProgress("downloading", 140), /100%/);
  assert.match(mergeStepProgress("downloading", undefined), /0%/);
});

test("the bar only ever moves forwards through the phases", () => {
  const steps = [
    mergeStepPercent("queued", 0),
    mergeStepPercent("downloading", 0),
    mergeStepPercent("downloading", 100),
    mergeStepPercent("extracting", 100),
    mergeStepPercent("installing", 40),
    mergeStepPercent("installing", 80),
    mergeStepPercent("done", 100),
  ];
  for (let i = 1; i < steps.length; i++) {
    assert.ok(
      steps[i] >= steps[i - 1],
      `bar went backwards at ${i}: ${steps[i - 1]} then ${steps[i]}`
    );
  }
  assert.equal(steps.at(-1), 100);
});

test("the download barely moves the bar, because it is a tenth of the job", () => {
  // This first shipped weighted as though the download were the long
  // part. It is not: on a Legion Go 2 the 68 MB download was ten seconds
  // of a 605 second setup. The bar hit 99% in fifteen seconds and then
  // sat there for eight minutes, which is precisely the hang it exists
  // to stop looking like.
  const full = mergeStepPercent("downloading", 100);
  assert.ok(full > 10, `download finished at ${full}%, which reads as stalled`);
  assert.ok(full < 30, `download alone reached ${full}%, over-claiming the job`);
});

test("the runtime install owns most of the bar, because it owns most of the wait", () => {
  const entering = mergeStepPercent("installing", 80, 0);
  const halfway = mergeStepPercent("installing", 80, MERGE_RUNTIME_BUDGET_MS / 2);
  const overdue = mergeStepPercent("installing", 80, MERGE_RUNTIME_BUDGET_MS * 3);
  assert.ok(entering < 40, `entered the long phase at ${entering}%`);
  assert.ok(halfway > entering + 20, "the bar must visibly move during the wait");
  // Running over budget parks it short of the end rather than lying.
  assert.ok(overdue <= 95, `ran past its budget to ${overdue}%`);
  assert.ok(overdue > halfway);
  assert.equal(mergeStepPercent("done", 100), 100);
});

test("only the backend can finish the bar, never the clock", () => {
  // A bar that reaches 100% while the work continues is a worse lie than
  // one that stops, so the last stretch is reserved for the done event.
  for (const ms of [0, 60_000, MERGE_RUNTIME_BUDGET_MS, 60 * 60_000]) {
    assert.ok(mergeStepPercent("installing", 80, ms) < 100, `at ${ms}ms`);
  }
});

test("the clock never drags the bar backwards mid-phase", () => {
  let last = 0;
  for (let ms = 0; ms <= MERGE_RUNTIME_BUDGET_MS * 2; ms += 15_000) {
    const v = mergeStepPercent("installing", 80, ms);
    assert.ok(v >= last, `went backwards at ${ms}ms: ${last} then ${v}`);
    last = v;
  }
});

test("the long phase says what it is waiting on, not just 'setting up'", () => {
  // "Setting up Mod Manager" was already on screen for the quick staging
  // steps, so leaving it there for the eight minute runtime install told
  // the user nothing had changed when in fact the slow part had started.
  const staging = mergeStepProgress("installing", 40);
  const runtime = mergeStepProgress("installing", 80);
  assert.notEqual(runtime, staging);
  assert.match(runtime, /runtime/i);
  // And it warns about the length, so a still-looking bar is expected.
  assert.match(runtime, /minutes/i);
});

test("the bar stays within its track", () => {
  for (const phase of ["queued", "downloading", "extracting", "installing", "done", "x"]) {
    for (const pct of [-50, 0, 50, 100, 500, undefined]) {
      const v = mergeStepPercent(phase, pct);
      assert.ok(v >= 0 && v <= 100, `${phase} ${pct} gave ${v}`);
    }
  }
});

test("the placeholder phase is mapped, not left to the generic busy text", () => {
  // nameDownload seeds the Downloads row in this phase before the backend
  // has said anything. Leaving it unmapped is what made the button sit on
  // "Setting up, this takes about ten minutes…" for a whole setup while
  // the work was actually progressing.
  assert.equal(mergeStepProgress("starting", 0), MERGE_STEP_STAGES.starting);
  assert.notEqual(mergeStepProgress("starting", 0), MERGE_STEP_BUSY);
  assert.ok(mergeStepPercent("starting", 0) > 0);
});
