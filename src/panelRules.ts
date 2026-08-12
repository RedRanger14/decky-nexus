// Which QAM controls EXIST. Pure and import-free so it can be tested
// (tests/panelRules.test.mjs) - "the button isn't rendered" is a class of
// bug no backend test can see, and it has bitten twice now.

/** The installed-mods list has nothing to show without mods of its own or
 * a framework row. */
export function showInstalledModsSection(
  modCount: number,
  hasFrameworkRow: boolean
): boolean {
  return modCount > 0 || hasFrameworkRow;
}

/** Reset to vanilla is the recovery tool, so it is reachable whenever the
 * game is installed and NEVER depends on there being mods listed.
 *
 * It used to live inside the installed-mods section, which returns null on
 * an empty list: a successful reset removed the last mod and took the
 * reset button away with it. That left no way to run it again while the
 * mod loader and launch command were still in place - the exact state
 * someone wanting to "start from scratch" is in. */
export function showResetRow(gameInstalled: boolean): boolean {
  return gameInstalled;
}

/** Whether the co-op password renders as dots. Hidden by default, since
 * the panel gets opened on a TV as often as a handheld.
 *
 * Keyed off the SAVED password rather than the draft: keying off the
 * draft would turn the field to dots on the first keystroke of a new one,
 * and an empty saved password has nothing to hide, so the field stays
 * editable and you can just type. */
export function maskCoopPassword(saved: string, revealed: boolean): boolean {
  return !revealed && saved.length > 0;
}

/** What to warn about the black screen after pressing Launch, or
 * undefined when the wait is short enough not to be worth a word.
 *
 * A heavily modded game shows nothing at all for minutes while it loads,
 * which is indistinguishable from a hang unless you already know. On a
 * handheld in Gaming Mode there is no window title, no spinner and no
 * console to reassure you, so people quit a game that was working.
 *
 * The bands are coarse on purpose - the one measurement we have is the
 * device's Gate To Sovngarde install (1,954 mods / 2,367 plugins) taking
 * a bit over three minutes to reach the main menu. Below ~50 mods the
 * wait is unremarkable and a notice would just be noise. */
export function launchWaitNotice(modCount: number): string | undefined {
  if (modCount < 50) return undefined;
  // Steam's toast truncates hard, and twice now the half that mattered
  // was the half cut off. Instruction first, comma not em dash (the dash
  // ate width for nothing), and short enough to survive.
  const wait = modCount >= 400 ? "a few minutes" : "a moment";
  return `Don't quit, ${modCount.toLocaleString()} mods take ${wait}.`;
}

/** What is wrong with the load order, phrased for someone who has never
 * heard the word "master", or undefined when there is nothing to say.
 *
 * Two faults, one button. Plugins listed before something they depend on,
 * and dependencies that are installed but switched off. Both crash the
 * game as it loads and neither is the user's doing, so the row leads with
 * the consequence rather than the vocabulary. */
export function loadOrderProblem(
  violations: number,
  disabledMasters: number,
  examples: string[] = []
): string | undefined {
  const parts: string[] = [];
  if (disabledMasters > 0) {
    const shown = examples
      .slice(0, 2)
      .map((n) => n.replace(/\.es[lmp]$/i, ""))
      .join(", ");
    parts.push(
      `${disabledMasters} mod${disabledMasters > 1 ? "s are" : " is"} ` +
        `installed but switched off while other mods depend on ` +
        `${disabledMasters > 1 ? "them" : "it"}` +
        (shown ? ` (${shown})` : "")
    );
  }
  if (violations > 0) {
    parts.push(
      `${violations.toLocaleString()} mod${violations > 1 ? "s" : ""} ` +
        `load before something they need`
    );
  }
  if (!parts.length) return undefined;
  return `${parts.join(", and ")}. Either one crashes the game while it starts. Fixing this only turns mods on and reorders them — nothing is installed, removed or downloaded.`;
}

/** Whether the load order has outgrown what the engine can address, and
 * what to tell someone who has never heard of a plugin slot.
 *
 * Skyrim and FO4 address plugins with one byte: 254 ordinary slots plus
 * one shared index that every ESL-flagged plugin lives behind. Going
 * over does not announce itself - the game stops loading plugins past
 * the limit or dies on the way in, and nothing says which of two
 * thousand mods was the straw.
 *
 * Warned at 95% rather than only when broken: a collection sitting at
 * 250 of 254 is one patch away from a crash nobody will be able to
 * explain, and that is worth knowing before it happens. */
export function slotPressure(
  full: number,
  fullLimit: number,
  light: number,
  lightLimit: number
): { level: "ok" | "near" | "over"; message?: string } {
  if (full > fullLimit || light > lightLimit) {
    const which = full > fullLimit ? "full" : "light";
    return {
      level: "over",
      message:
        `Too many mods for the game to load: ${
          which === "full" ? full : light
        } ${which} plugins against a hard limit of ${
          which === "full" ? fullLimit : lightLimit
        }. Some will silently not load. Turning off a few mods is the only fix.`,
    };
  }
  if (full >= fullLimit * 0.95 || light >= lightLimit * 0.95) {
    return {
      level: "near",
      message:
        `Close to the game's limit: ${full} of ${fullLimit} full plugin ` +
        `slots used. A few more mods and the game stops loading them.`,
    };
  }
  return { level: "ok" };
}

/** How the automated crash hunt reads one launch.
 *
 * "No crash log yet" is not the same as "it booted" - the crash we chased
 * on device landed at 2:54-4:18, so a verdict before then is a guess. And
 * a crash log at a DIFFERENT address is not our crash: mods die on forms
 * their own plugin no longer provides once the hunt disables it, which
 * looks identical from outside and wasted two steps when I read it by eye.
 */
export function crashHuntVerdict(
  elapsedMs: number,
  crashAddress: string | undefined,
  signature: string,
  patienceMs = 330_000
): "crash" | "boot" | "other-crash" | "waiting" {
  if (crashAddress) {
    return crashAddress.includes(signature) ? "crash" : "other-crash";
  }
  return elapsedMs >= patienceMs ? "boot" : "waiting";
}

/** How the hunt reads a launch when the test is "does a save load?".
 *
 * Reaching the menu proves nothing here - the whole point of this mode is
 * faults that only appear once the world loads. And unlike the boot hunt,
 * silence is NOT success: the user has to press Continue, and if they
 * walked away nothing happened at all. Calling that a pass would poison
 * the search with a result nobody produced, so it is reported as
 * "no-input" and the launch is repeated rather than counted.
 *
 * `inGame` comes from the Papyrus log being written after launch - scripts
 * only run in the world, so it is a genuine "we are playing" signal rather
 * than "a window appeared".
 */
export function saveLoadVerdict(
  elapsedMs: number,
  crashAddress: string | undefined,
  signature: string,
  inGame: boolean,
  patienceMs = 600_000
): "crash" | "loaded" | "other-crash" | "waiting" | "no-input" {
  if (crashAddress) {
    return crashAddress.includes(signature) ? "crash" : "other-crash";
  }
  if (inGame) return "loaded";
  return elapsedMs >= patienceMs ? "no-input" : "waiting";
}

/** The toast shown as each hunt launch begins.
 *
 * The hunt starts and closes the game over and over for hours. Without a
 * running count that is indistinguishable from a boot loop, and the
 * rational thing for the user to do is pull the plug on something that
 * was working. So every launch is numbered, and the number goes first.
 */
export function huntProgressNote(
  attempt: number,
  modsUnderTest: number,
  remaining: number,
  found: number
): { title: string; body: string } {
  // Halving: each launch removes half of what's left to rule out, plus a
  // launch to confirm the crash and one to confirm the end.
  const left = Math.max(0, Math.ceil(Math.log2(Math.max(2, remaining)))) + 1;
  return {
    title: `Attempt ${attempt} — testing ${modsUnderTest.toLocaleString()} mods`,
    body:
      `About ${left} more launch${left === 1 ? "" : "es"} to go` +
      (found > 0
        ? `. ${found} broken mod${found === 1 ? "" : "s"} found so far`
        : ". Leave the game alone — it closes itself"),
  };
}

/** One crash-log call stack frame that names a mod DLL we could skip. */
export interface CrashSuspect {
  name: string;
  /** Stack depth: 0 is where it died, so lower is stronger evidence. */
  frame: number;
  /** A real stack frame, as opposed to a stack-scan guess. */
  probable: boolean;
}

/** Which single plugin to offer to skip after a crash, or undefined for
 * none.
 *
 * ONE, deliberately. Several mod DLLs can sit on one call stack, and only
 * the frame nearest the crash is meaningful evidence - skipping the rest
 * would take out mods that were working fine to fix a crash they had no
 * part in. If the pick is wrong the next launch writes a new crash log
 * naming the next candidate, so a wrong guess costs one launch and never
 * compounds.
 *
 * Stack-scan frames rank below every real frame no matter how shallow:
 * a scan hit is a leftover value that merely looks like a return address,
 * so a genuine frame 9 is better evidence than a scanned frame 1. */
export function crashSuspect(
  culprits: CrashSuspect[] | undefined
): CrashSuspect | undefined {
  if (!culprits?.length) return undefined;
  return [...culprits].sort(
    (a, b) => Number(a.probable ? 0 : 1) - Number(b.probable ? 0 : 1) ||
      a.frame - b.frame
  )[0];
}

/** How many distinct things are wrong, for the Troubleshooting header.
 *
 * Counted per FAULT, not per affected mod: 37 script-extender plugins
 * failing is one thing the user can act on, and showing "37" would make a
 * single fixable problem look like a catastrophe. */
export function troubleshootingCount(
  runtimeOutdated: boolean,
  failedPlugins: number,
  hasCrashSuspect: boolean,
  loadOrderIssue: string | undefined
): number {
  return (
    (runtimeOutdated ? 1 : 0) +
    (failedPlugins > 0 ? 1 : 0) +
    (hasCrashSuspect ? 1 : 0) +
    (loadOrderIssue ? 1 : 0)
  );
}

/** Whether one collection file still counts as "remaining to install".
 *
 * Four ways a file stops being remaining, and the fourth is the one that
 * bit: a mod resolved through Finish setup leaves the attention queue the
 * moment it installs, but the installed-mods list is only re-read when
 * the whole pass ends. In that window it belongs to neither set, so it
 * reappears as work still to do - the remaining count goes UP as the user
 * works through the queue, which reads as "the tool is going backwards".
 * `justResolved` covers the gap with what the page already knows.
 */
export function isRemaining(
  file: { modId: number; fileId: number },
  installedModIds: Set<number>,
  rowState: Record<number, string>,
  pendingAttentionFileIds: Set<number>,
  justResolvedFileIds: Set<number> = new Set()
): boolean {
  return (
    !installedModIds.has(file.modId) &&
    rowState[file.fileId] !== "done" &&
    !pendingAttentionFileIds.has(file.fileId) &&
    !justResolvedFileIds.has(file.fileId)
  );
}

/** How many of a collection's entries the Uninstall button will remove.
 *
 * Counted in ENTRIES, not install records, because the rest of the page
 * counts entries and two numbers that should agree must not disagree.
 * A collection lists one entry per file, but a mod shipping a main file
 * plus two patches is three entries and ONE install record - so on a
 * 546-entry collection the record count is 454 and reads like 92 mods
 * quietly failed. Nothing had failed: 78 mods supplied more than one
 * file each.
 *
 * Falls back to the record count while the collection detail is still
 * loading, or if a revision changed under us and no entry matches -
 * losing the button would strand mods the user cannot then remove. */
export function collectionOwnedCount(
  entries: { modId: number }[] | undefined,
  ownedModIds: Set<number>
): number {
  if (!entries?.length) return ownedModIds.size;
  return (
    entries.filter((e) => ownedModIds.has(e.modId)).length || ownedModIds.size
  );
}

/** The QAM's thumbs-up on a framework row: is it shown, is it on, and
 * what does the line under it say.
 *
 * Framework mods (SMAPI, SKSE, REFramework) are installed by a Step
 * button, so nobody ever opens their mod page - which is the only place
 * the plugin could endorse from. These are the mods every single user of
 * a game depends on and the ones least likely to get thanked.
 *
 * Four states matter and only one is a plain button:
 *  - `unknown`: no API key, or the lookup failed. Show nothing rather
 *    than a control that cannot work.
 *  - `Endorsed`: possibly from years ago on the website. Reflect that
 *    instead of inviting a second endorsement that would toggle the
 *    first one OFF.
 *  - `Abstained`: they said no once. Still offer it, without nagging.
 *  - `Undecided`: the ask.
 *
 * The cooldown gets its own line because Nexus rejects an endorsement in
 * the first 15 minutes after download, and a Step 1 install is followed
 * by pressing things immediately - so the most likely first attempt is
 * the one that fails, and "TOO_SOON_AFTER_DOWNLOAD" explains nothing.
 * Shown whenever the install time is unknown, which is the usual case:
 * warning someone who does not need it costs a line of small grey text,
 * while omitting it costs a press that looks like a broken button. */
export function endorseControl(
  status: string | undefined,
  installedMinutesAgo?: number
): { show: boolean; endorsed: boolean; label: string; hint?: string } {
  if (!status || status === "unknown") {
    return { show: false, endorsed: false, label: "" };
  }
  if (status === "Endorsed") {
    return { show: true, endorsed: true, label: "Endorsed" };
  }
  const knownSettled =
    installedMinutesAgo !== undefined && installedMinutesAgo >= 15;
  return {
    show: true,
    endorsed: false,
    label: "Endorse",
    hint: knownSettled
      ? "Endorsing tells the author their work is being used."
      : "Nexus Mods only accepts an endorsement 15 minutes after the download, so this may not work straight away.",
  };
}

/** Whether a download row offers a Cancel control, by phase.
 *
 * Only while bytes are still owed: cancelling mid-extraction would leave
 * a half-merged mod, and cancelling a "queued" row (downloaded, waiting
 * on the serial installer) deletes nothing the installer would not just
 * fetch again. Paused rows stay cancellable - "stop this one for good"
 * is a natural thing to decide while everything is stopped.  */
export function cancellableDownload(phase: string): boolean {
  return phase === "starting" || phase === "downloading" || phase === "paused";
}

/** The pause-all control: label and whether it is worth showing.
 *
 * Shown while anything is active OR while paused - a paused page with no
 * visible resume control is a trap, because the rows themselves are
 * parked and will never change state on their own. */
export function pauseAllControl(
  activeCount: number,
  paused: boolean
): { show: boolean; label: string } {
  return {
    show: paused || activeCount > 0,
    label: paused ? "▶ Resume" : "⏸ Pause all",
  };
}
