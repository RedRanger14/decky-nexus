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

/** One line under the heading, before the user expands anything. */
export function mergeStepSummary(installed: boolean, version: string): string {
  if (!installed) return "Not installed. Needed for the community patches.";
  return version
    ? `ME3Tweaks Mod Manager ${version} installed`
    : "ME3Tweaks Mod Manager installed";
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

/** Progress text while the step runs. It is a long job (a 200 MB download,
 * then a runtime install inside the prefix) and silence reads as a hang. */
export const MERGE_STEP_STAGES: Record<string, string> = {
  lookup: "Finding Mod Manager on Nexus Mods…",
  download: "Downloading Mod Manager (about 200 MB)…",
  extract: "Unpacking…",
  stage: "Putting it in the game's environment…",
  configure: "Setting it up…",
  runtime: "Installing the Windows runtime it needs…",
};

/** Whether the step should be offered at all. Only Mass Effect uses merge
 * mods, so only Mass Effect gets the step: a game that cannot use it must
 * not grow a step that does nothing. */
export function offersMergeSupport(installMode: string | undefined): boolean {
  return installMode === "masseffect";
}

/** Shown on the button while the step runs. Measured on a Legion Go 2:
 * 610 seconds from a clean prefix, nearly all of it the download. Ten
 * minutes of "Setting up" with nothing else on screen is indistinguishable
 * from a hang, and this plugin's audience is people on a couch who cannot
 * go and read a log. */
export const MERGE_STEP_BUSY = "Setting up, this takes about ten minutes…";

/** What the panel says after a failed attempt. Kept because a toast is
 * gone before it can be read, and this step is long enough that the user
 * may have looked away. */
export function mergeStepFailure(error: string): string {
  const trimmed = (error || "").trim();
  if (!trimmed) return "";
  return `Last attempt failed: ${trimmed}`;
}
