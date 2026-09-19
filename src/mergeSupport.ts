/** Copy and rules for the Mass Effect merge mod step.
 *
 * Merge mods rewrite the game's own package files: script recompiles, class
 * replacements, 2DA and Coalesced merges. Six of the ten most endorsed Mass
 * Effect mods are merge mods, all three community patches among them, and
 * several mods that DO install then require one. Refusing them refuses most
 * of the game's modding scene.
 *
 * The plugin does not reimplement that. ME3Tweaks Mod Manager already does
 * it and runs unattended in the game's own Proton prefix, verified on a
 * Legion Go 2 on 2026-09-18.
 *
 * What it does NOT do is install somebody else's program quietly. Michael:
 * "maybe there is a middle ground where we can warn the user and ask that
 * they install as part of the steps in the QAM, that way the user is sort
 * of more aware as its a deliberate step". So it is a step, with the text
 * below, and until it is pressed merge mods are refused with a message
 * pointing at it. Everywhere else the plugin does the work rather than
 * asking; here the user should know what is landing on their machine.
 *
 * Pure strings and predicates, tested in tests/mergeSupport.test.mjs.
 */

/** The step's heading. */
export const MERGE_STEP_TITLE = "Merge mod support";

/** The step's one line of body text.
 *
 * It leads with the step's name because the Field label is now its NUMBER
 * ("Step 2"), the same shape as every other step in the panel. Michael:
 * "can we make isntalling the mod manager step 2 because its an action in
 * its own right".
 */
export function mergeStepSummary(installed: boolean, version: string): string {
  if (!installed) {
    return `${MERGE_STEP_TITLE}: not installed. Needed for the community patches.`;
  }
  return version
    ? `ME3Tweaks Mod Manager ${version} installed ✓`
    : "ME3Tweaks Mod Manager installed ✓";
}

/** What pressing the button will actually do, in full. Shown before the
 * press, not after: this is the paragraph the decision rests on. */
export const MERGE_STEP_EXPLAINER =
  "Some Mass Effect mods work by editing the game's own files rather than " +
  "adding new ones. The LE1, LE2 and LE3 community patches all do, and so " +
  "do many mods that depend on them. Installing those needs ME3Tweaks Mod " +
  "Manager, the tool the Mass Effect modding community builds around.\n\n" +
  "This downloads it from Nexus Mods (about 200 MB, by Mgamerz) and sets it " +
  "up inside this game's own Windows environment, where the plugin runs it " +
  "for you in the background. You will never have to open it.\n\n" +
  "It takes about ten minutes, most of it downloading. You can leave this " +
  "menu while it works.\n\n" +
  "It is somebody else's program, which is why this is a button rather than " +
  "something that happens on its own. You can remove it again below, and " +
  "removing it leaves any mods it installed in place.";

/** Said when the step has not been taken and a merge mod is refused. The
 * backend sends its own copy of this; the frontend repeats it where it
 * explains the step. */
export const MERGE_NEEDED_NOTE =
  "Mods that edit the game's own files need this. Without it they are " +
  "skipped with a note rather than installed.";

/** Shown on the button while the step runs. Measured on a Legion Go 2:
 * 610 seconds from a clean prefix, nearly all of it the download. Ten
 * minutes of "Setting up" with nothing else on screen is indistinguishable
 * from a hang, and this plugin's audience is people on a couch who cannot
 * go and read a log. */
export const MERGE_STEP_BUSY = "Setting up, this takes about ten minutes…";

/** Progress text while the step runs, keyed by the phase the backend
 * emits. It is a long job (a 200 MB download, then a runtime install
 * inside the prefix) and silence reads as a hang. Michael: "Up to 10
 * minutes with no visual feedback is not accetpable". */
export const MERGE_STEP_STAGES: Record<string, string> = {
  queued: "Finding Mod Manager on Nexus Mods…",
  downloading: "Downloading Mod Manager",
  extracting: "Unpacking Mod Manager…",
  installing: "Setting up Mod Manager…",
  done: "Ready",
};

/** What the button says right now, given the last progress event.
 *
 * The download is the long part and the only one with real numbers, so it
 * is the only one that shows a percentage: a made-up percentage on the
 * other phases would be worse than none. Everything else names what it is
 * doing, which is enough to show it is alive.
 */
export function mergeStepProgress(phase?: string, percent?: number): string {
  if (!phase) return MERGE_STEP_BUSY;
  const text = MERGE_STEP_STAGES[phase];
  if (!text) return MERGE_STEP_BUSY;
  if (phase === "downloading") {
    const pct = Math.max(0, Math.min(100, Math.round(percent ?? 0)));
    return `${text} ${pct}%`;
  }
  return text;
}

/** How full the button's progress bar should be, 0 to 100.
 *
 * The download is most of the wall clock, so it owns most of the bar:
 * finishing the download at 100% and then sitting on "setting up" for
 * four more minutes would read as a hang at the worst moment. Measured on
 * a Legion Go 2: 610 seconds total.
 */
export function mergeStepPercent(phase?: string, percent?: number): number {
  const pct = Math.max(0, Math.min(100, percent ?? 0));
  const bar = (() => {
    switch (phase) {
      case "queued":
        return 2;
      case "downloading":
        return 5 + pct * 0.55;
      case "extracting":
        return 62;
      case "installing":
        // The backend walks 40 to 80 through staging, config and runtime.
        return 65 + Math.max(0, pct - 40) * 0.85;
      case "done":
        return 100;
      default:
        return 0;
    }
  })();
  // Clamped at the end rather than trusted: a phase whose percentage runs
  // past what this expected would otherwise overflow the track. The
  // installing arm did exactly that at 100, returning 116.
  return Math.max(0, Math.min(100, bar));
}

/** Whether the step should be offered at all. Only Mass Effect uses merge
 * mods, so only Mass Effect gets the step: a game that cannot use it must
 * not grow a step that does nothing. */
export function offersMergeSupport(installMode: string | undefined): boolean {
  return installMode === "masseffect";
}

/** What the panel says after a failed attempt. Kept because a toast is
 * gone before it can be read, and this step is long enough that the user
 * may have looked away. */
export function mergeStepFailure(error: string): string {
  const trimmed = (error || "").trim();
  if (!trimmed) return "";
  return `Last attempt failed: ${trimmed}`;
}

/** What a requirement pill says for a tool the plugin handles itself.
 *
 * The default is for desktop mod managers the plugin replaces. Mass
 * Effect overrides it, because there the plugin does not replace
 * ME3Tweaks Mod Manager, it installs and drives it. Telling someone a
 * community patch's requirement is "not needed" when the patch genuinely
 * cannot install without it would be the wrong kind of reassuring.
 */
export function managedRequirementNote(override?: string): string {
  return override || "not needed (this plugin does its job)";
}

/** ME3Tweaks Mod Manager's own Nexus mod id. Progress for the setup is
 * reported under it, so the Downloads panel lists it like any other
 * download instead of the step being the only place anything moves. */
export const ME3TWEAKS_MOD_ID = 2;
