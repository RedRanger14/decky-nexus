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

/** Mods that cannot load because a master is not installed at all, said
 * in terms of the thing the user has to go and buy or download.
 *
 * The third load-order fault and the only one no button here can fix.
 * The other two are repairs: switch a master back on, or move it earlier.
 * This one is "you do not have Dead Money", and pretending otherwise
 * would offer a Fix button that cannot work.
 *
 * Leads with DLC names rather than filenames because `DeadMoney.esm`
 * means nothing to someone who has never opened a mod manager, and the
 * action - tick it in Steam's DLC tab - is invisible from the filename.
 *
 * Counted in blocked mods, not missing masters: five absent files sounds
 * survivable, and on the device it meant 115 of 245 mods would not load.
 * The game says none of this. It names one plugin in a modal and quits.
 */
export function missingMasterProblem(
  missing: { name: string; label?: string; needed_by: number }[] | undefined,
  blockedPlugins: number
): string | undefined {
  if (!missing?.length || blockedPlugins <= 0) return undefined;
  const dlc = missing.filter((m) => m.label);
  const mods = missing.filter((m) => !m.label);
  const parts: string[] = [];
  if (dlc.length) {
    const names = dlc.map((m) => m.label).join(", ");
    parts.push(
      `you don't have ${dlc.length === 1 ? "this DLC" : "these DLC"} ` +
        `installed: ${names}`
    );
  }
  if (mods.length) {
    const names = mods
      .slice(0, 3)
      .map((m) => m.name.replace(/\.es[lmp]$/i, ""))
      .join(", ");
    parts.push(
      `${mods.length} mod${mods.length > 1 ? "s are" : " is"} missing that ` +
        `others were built on (${names}${mods.length > 3 ? ", …" : ""})`
    );
  }
  // The advice differs by cause, and giving both would send someone to
  // Steam looking for a DLC called Tale Of Two Wastelands.
  const advice = dlc.length
    ? `DLC is installed from Steam's DLC tab, not from here.`
    : `These are usually patches the collection ships for a setup you don't ` +
      `have, and switching them off is the normal fix.`;
  return (
    `${blockedPlugins.toLocaleString()} mod${blockedPlugins > 1 ? "s" : ""} ` +
    `cannot load because ${parts.join("; and ")}. The game shows one name ` +
    `and closes, so this is the full list. ${advice}`
  );
}

/** Whether to offer "turn these off", and what to call it.
 *
 * Only for masters that are missing MODS. A missing DLC master has a
 * better answer than switching mods off - buy it - and on the device that
 * distinction was 115 mods against four. A button that treated them the
 * same would have thrown away a collection somebody had every intention
 * of running, one tap after they were told what was wrong. */
export function blockedPluginsAction(
  missing: { name: string; label?: string; needed_by: number }[] | undefined
): { show: boolean; label: string } {
  return {
    show: (missing ?? []).some((m) => !m.label),
    label: "Turn off the mods that can't load",
  };
}

/** Files where the wrong mod won, or undefined when the install matches
 * what the collection asked for.
 *
 * Mods overwriting each other is not a fault - it is the entire mechanism
 * a collection uses, and the device's New Vegas install shares 10,362
 * paths across 867 mod-sets almost all deliberately. So this never
 * mentions conflicts in general; only the ones where the result disagrees
 * with the curator's order.
 *
 * Phrased around the consequence rather than the mechanism. "1,440 files
 * were overwritten by the wrong mod" means nothing to someone whose
 * actual experience is that the interface looks wrong and the game will
 * not start, so the numbers come second and one real example comes first.
 */
export function fileConflictProblem(
  files: number,
  pairs: number,
  conflicts: { actual: string; intended: string; files: number }[] = []
): string | undefined {
  if (files <= 0 || !conflicts.length) return undefined;
  const worst = conflicts[0];
  return (
    `Some mods overwrote files they were meant to lose to — for example ` +
    `"${worst.actual}" replaced ${worst.files.toLocaleString()} file` +
    `${worst.files === 1 ? "" : "s"} belonging to "${worst.intended}". ` +
    `${files.toLocaleString()} file${files === 1 ? "" : "s"} across ` +
    `${pairs} pair${pairs === 1 ? "" : "s"} of mods ended up in the wrong ` +
    `order. This usually happens to mods that needed your choices during ` +
    `install, because they finish last. Fixing it rewrites just those ` +
    `files from the mod that should own them, leaving everything else ` +
    `alone.`
  );
}

/** Plugins the load order switches on that are not installed.
 *
 * The one repair here that is unconditionally safe, and worth saying so:
 * a plugin with no file cannot load whatever the list says, so clearing
 * the line changes nothing about what the game does. Every other fix in
 * this section changes what loads; this one only stops us counting
 * something that is not there.
 *
 * Found on device after an uninstall left `oHUD.esm` switched on. On
 * Skyrim and FO4 that is a stray line; on FO3 and New Vegas, where being
 * in the file IS being enabled, it is a phantom enabled mod - and it
 * inflates the mod count the panel reports back at the user. */
export function ghostPluginProblem(
  count: number,
  examples: string[] = []
): string | undefined {
  if (count <= 0) return undefined;
  const shown = examples
    .slice(0, 3)
    .map((n) => n.replace(/\.es[lmp]$/i, ""))
    .join(", ");
  return (
    `${count} mod${count === 1 ? " is" : "s are"} switched on but ` +
    `${count === 1 ? "is" : "are"} not installed` +
    (shown ? ` (${shown})` : "") +
    `. Usually left behind by an uninstall. Clearing them is safe — they ` +
    `cannot load either way, so nothing about the game changes.`
  );
}

/** What to say about mods the game's own log blamed for errors last run.
 *
 * Slay the Spire 2, 2026-08-13: a collection threw 1,078 exceptions and
 * killed the game five seconds into the menu. Every one of them named the
 * mod it came from, so "which ones are out of date" is answerable - and
 * the answer is worth spending a sentence on, because from the outside a
 * collection that closes the game looks like the plugin's fault.
 *
 * Names the first two mods. A list of nine reads as a wall and gets
 * skipped; two plus a count is enough to recognise what is going.
 */
export function failingProblem(
  details: { name: string; why: string }[],
  held: string[] = []
): string | undefined {
  const names = details.map((d) => d.name).filter(Boolean);
  if (!names.length) return undefined;
  const one = names.length === 1;
  const shown = names.slice(0, 2).join(", ");
  const rest = names.length - 2;
  const who = `${shown}${rest > 0 ? ` and ${rest} more` : ""}`;
  const kept = held.filter(Boolean);
  return (
    `The game reported errors from ${who}. That normally means ` +
    `${one ? "it has" : "they have"} not been updated for this version of ` +
    `the game. Switching ${one ? "it" : "them"} off leaves the rest of ` +
    `your mods alone, and you can switch ${one ? "it" : "them"} back on ` +
    `once ${one ? "an update arrives" : "updates arrive"}.` +
    (kept.length
      ? ` ${kept.slice(0, 2).join(" and ")} also reported errors but ` +
        `${kept.length === 1 ? "is" : "are"} left on — your other mods ` +
        `need ${kept.length === 1 ? "it" : "them"}.`
      : "")
  );
}

/** The warning on a mod's own page when we have watched that exact version
 * fail on the game build installed now.
 *
 * Says what was seen rather than passing judgement on the mod. It failed
 * against this build - which is usually the game moving, not the mod being
 * bad - and the author may already have shipped the fix.
 */
export function knownBrokenNote(version: string): string {
  const v = (version || "").trim();
  return (
    `This ${v ? `version (${v})` : "mod"} stopped the game running the ` +
    `last time it was installed — it needs an update for the version of the ` +
    `game you have. You can still install it; it will be switched off until ` +
    `you turn it on.`
  );
}

/** Whether an install error means Nexus will not serve the mod at all.
 *
 * Matches on what _download_forbidden_reason writes, which is derived from
 * the API's own 403 body - an author deleting a mod, or Nexus taking one
 * down for review. Neither is a failure anybody can act on, so neither
 * belongs in a failure count.
 */
export function isGoneFromNexus(error?: string): boolean {
  const e = (error ?? "").toLowerCase();
  return (
    e.includes("author has removed this mod") ||
    e.includes("taken this mod down while it is reviewed")
  );
}

/** What the collection page says about mods Nexus no longer serves.
 *
 * A collection outlives the mods in it. Michael hit two on Slay the Spire
 * 2's most popular collection - one deleted by its author, one under
 * moderation - and was told he needed a Premium account he already had.
 */
export function unavailableNote(names: string[]): string {
  const gone = names.filter(Boolean);
  if (!gone.length) return "";
  const one = gone.length === 1;
  const shown = gone.slice(0, 3).join(", ");
  const rest = gone.length - 3;
  return (
    `${shown}${rest > 0 ? ` and ${rest} more` : ""} ` +
    `${one ? "is" : "are"} no longer available on Nexus — ` +
    `${one ? "its author has" : "their authors have"} removed ` +
    `${one ? "it" : "them"}, or Nexus is reviewing ` +
    `${one ? "it" : "them"}. Nothing to fix: the collection lists ` +
    `${one ? "it" : "them"} but cannot supply ` +
    `${one ? "it" : "them"}, and the rest installed normally.`
  );
}

/** Numbers for the framework setup steps, given which of them exist.
 *
 * The step labels were hardcoded 1, 2, 3, 4 with step 2 rendered only when
 * the framework needs a launch command. Slay the Spire 2's BaseLib does not
 * - the game loads mods by itself - so the panel read "Step 1" then
 * "Step 3", which looks like a missing step rather than a step that was
 * never needed.
 *
 * Returned rather than hardcoded so adding another optional step cannot
 * reintroduce the gap.
 */
export function frameworkStepNumbers(hasLaunchCommand: boolean): {
  install: number;
  launch: number;
  browse: number;
  play: number;
} {
  let n = 1;
  const install = n++;
  const launch = hasLaunchCommand ? n++ : 0;
  const browse = n++;
  const play = n++;
  return { install, launch, browse, play };
}

/** What the panel says about libraries it installed on a mod's behalf.
 *
 * Twice on device a mod simply did not load: Enchanted Offerings wanted
 * BaseLib, LustTravel2 wanted RitsuLib, and in both cases the game printed
 * a red line, the mod count silently came up one short, and nothing said
 * what was needed. Nobody browsing mods should have to know which library
 * a mod is built on.
 */
export function installedDepsNote(
  deps: { name: string; for: string }[]
): string {
  const kept = deps.filter((d) => d.name);
  if (!kept.length) return "";
  const one = kept.length === 1;
  const shown = kept
    .slice(0, 2)
    .map((d) => (d.for ? `${d.name} for ${d.for}` : d.name))
    .join(", ");
  const rest = kept.length - 2;
  return (
    `Installed ${shown}${rest > 0 ? ` and ${rest} more` : ""}. ` +
    `${one ? "That mod needs it" : "Those mods need them"} and ` +
    `${one ? "it was" : "they were"} missing, which is why ` +
    `${one ? "it" : "they"} did not load. Restart the game and ` +
    `${one ? "it" : "they"} should work.`
  );
}

/** A plain readout of what the game itself reported last run.
 *
 * Michael, 2026-08-13: "it doesnt say there are 23 errors, just 23 mods
 * loaded with errors so its hard to tell if its different or not". The
 * game's banner is binary - it looks identical whether one mod erred or
 * five - so going from 5 erroring mods to 1 is invisible in the place the
 * user is actually looking. This is the number, in words, on our side.
 *
 * `blamed` counts mods still reporting errors; `handled` is what the plugin
 * has already dealt with this run.
 */
export function lastRunSummary(
  blamed: string[],
  handled: number,
  noUpdate: string[] = []
): string | undefined {
  const names = blamed.filter(Boolean);
  if (!names.length) {
    return handled > 0
      ? `No mods are reporting errors any more. The game may still show a ` +
          `red "WITH ERRORS" line from the run before this fix — launch it ` +
          `once more and that will clear.`
      : "No mods reported errors the last time you played.";
  }
  const one = names.length === 1;
  const shown = names.slice(0, 3).join(", ");
  const rest = names.length - 3;
  const stuck = noUpdate.filter((n) => names.includes(n));
  // The dead-end case, which is worth naming as a dead end. Otherwise the
  // reader keeps looking for the fix that does not exist, and the red line
  // reads as an unsolved problem rather than a finished one.
  const allStuck = stuck.length === names.length;
  return (
    `${one ? "1 mod is" : `${names.length} mods are`} still reporting ` +
    `errors: ${shown}${rest > 0 ? ` and ${rest} more` : ""}. ` +
    (allStuck
      ? `${one ? "It is" : "They are"} running — only part of ` +
        `${one ? "it" : "them"} failed — and there is no newer version to ` +
        `move to, so only the mod ${one ? "author" : "authors"} can clear ` +
        `this. Nothing else to do: the game plays, and the red line the ` +
        `game shows will stay until ${one ? "it is" : "they are"} updated.`
      : `The game shows one red line however many there are, so it looks ` +
        `the same whether that is one or twenty.`)
  );
}

/** What the panel says about blamed mods it updated rather than switched
 * off.
 *
 * On device this was the whole fix: the collection pinned BaseLib 3.1.2 and
 * RitsuLib 0.2.30 against a game build wanting 3.3.8 and 0.5.11, and
 * updating those two took the erroring mods from 5 to 1 - it repaired the
 * two mods that depend on RitsuLib as well. Switching a library off would
 * have taken them down instead.
 */
export function updatedNote(
  updated: { name: string; from: string; to: string }[]
): string {
  const kept = updated.filter((u) => u.name);
  if (!kept.length) return "";
  const one = kept.length === 1;
  const shown = kept
    .slice(0, 2)
    .map((u) => `${u.name} to ${u.to || "the newest version"}`)
    .join(", ");
  const rest = kept.length - 2;
  return (
    `Updated ${shown}${rest > 0 ? ` and ${rest} more` : ""}. ` +
    `${one ? "It was" : "They were"} out of date for the version of the ` +
    `game you have, and other mods need ${one ? "it" : "them"}, so ` +
    `updating was the fix rather than switching ${one ? "it" : "them"} off.`
  );
}

/** What the collection page says about mods it switched off before the
 * first launch, because this game build has already been seen to fail on
 * them.
 *
 * Framed as the collection being ready rather than as damage. Somebody who
 * just installed 27 mods and is told 4 are off wants to know the game will
 * start, not to read an incident report.
 */
export function preDisabledNote(names: string[]): string {
  const off = names.filter(Boolean);
  if (!off.length) return "";
  const one = off.length === 1;
  const shown = off.slice(0, 3).join(", ");
  const rest = off.length - 3;
  return (
    `Ready to play. ${shown}${rest > 0 ? ` and ${rest} more` : ""} ` +
    `${one ? "was" : "were"} left switched off — ${one ? "it does" : "they do"} ` +
    `not work with the version of the game you have, and the game will not ` +
    `start with ${one ? "it" : "them"} on. ${one ? "It is" : "They are"} ` +
    `still installed, so ${one ? "it" : "they"} can be switched on in ` +
    `Installed mods when ${one ? "an update arrives" : "updates arrive"}.`
  );
}

/** What the panel says about mods it switched off without being asked.
 *
 * Silence would be worse than a button. Somebody who installed a
 * 27-mod collection and finds 4 of them off is owed the reason and the
 * knowledge that they can put them back - the plugin acting on its own is
 * only acceptable if it says what it did.
 */
export function repairedNote(names: string[]): string {
  const off = names.filter(Boolean);
  if (!off.length) return "";
  const one = off.length === 1;
  const shown = off.slice(0, 3).join(", ");
  const rest = off.length - 3;
  return (
    `${shown}${rest > 0 ? ` and ${rest} more` : ""} ` +
    `${one ? "was" : "were"} switched off — the game could not run ` +
    `${one ? "it" : "them"} and kept crashing. ${one ? "It is" : "They are"} ` +
    `still installed, so you can switch ${one ? "it" : "them"} back on in ` +
    `Installed mods once ${one ? "it has" : "they have"} been updated.`
  );
}

/** What the toast says after switching off the mods the log blamed.
 *
 * `held` is the ecosystem libraries. BaseLib genuinely threw in that
 * session, but 21 mods sit on it - so it is named as still-erroring rather
 * than switched off, and saying so beats the user finding out later that
 * one blamed mod is still there.
 */
export function disableFailingOutcome(
  names: string[],
  held: string[] = [],
  fallback?: string
): string {
  const off = names.filter(Boolean);
  const kept = held.filter(Boolean);
  if (!off.length && !kept.length) {
    return fallback || "Nothing matched an installed mod";
  }
  const parts: string[] = [];
  if (off.length) parts.push(off.slice(0, 3).join(", "));
  if (kept.length) {
    parts.push(
      `Left ${kept.slice(0, 2).join(", ")} on — your other mods need ` +
        `${kept.length === 1 ? "it" : "them"}.`
    );
  }
  return parts.join(" ");
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
 * explain, and that is worth knowing before it happens.
 *
 * A lightLimit of 0 means the engine has no light tier at all (FO3, New
 * Vegas) and light must be ignored entirely - not merely "compared
 * against zero", which would put every one of those load orders at 100%
 * of its limit and warn on all of them forever. */
export function slotPressure(
  full: number,
  fullLimit: number,
  light: number,
  lightLimit: number
): { level: "ok" | "near" | "over"; message?: string } {
  const hasLight = lightLimit > 0;
  if (full > fullLimit || (hasLight && light > lightLimit)) {
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
  if (full >= fullLimit * 0.95 || (hasLight && light >= lightLimit * 0.95)) {
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
 * single fixable problem look like a catastrophe.
 *
 * Every fault that can stop the game booting has to be in here. Missing
 * masters were not, so the section offered "Nothing looks wrong. Open if
 * the game won't start." to a user whose game had just refused to start
 * over exactly that - and the fix was one tap inside, behind a button
 * saying there was nothing to fix. */
export function troubleshootingCount(
  runtimeOutdated: boolean,
  failedPlugins: number,
  hasCrashSuspect: boolean,
  loadOrderIssue: string | undefined,
  missingMasters?: string
): number {
  return (
    (runtimeOutdated ? 1 : 0) +
    (failedPlugins > 0 ? 1 : 0) +
    (hasCrashSuspect ? 1 : 0) +
    (loadOrderIssue ? 1 : 0) +
    (missingMasters ? 1 : 0)
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

/** Whether a parked mod is something Finish setup can actually resolve.
 *
 * "choices" means the installer offered alternatives and the user must
 * pick one - but an entry with an EMPTY option list has nothing to pick.
 * Counting it made the button read "Finish setup (2)" and then do
 * nothing visible, which is worse than not offering it: the user taps,
 * sees no wizard, and cannot tell whether it worked.
 *
 * A FOMOD always has a wizard, so it needs no such check. */
export function isActionableAttention(item: {
  reason: string;
  options?: unknown[];
}): boolean {
  if (item.reason === "fomod") return true;
  return item.reason === "choices" && (item.options?.length ?? 0) > 0;
}

/** Overall progress through a collection, as a percentage.
 *
 * Each in-flight download counts as its OWN fraction of a mod, which is
 * the fix: this used to take the AVERAGE of the active downloads and add
 * it as a single mod's worth. With six mods downloading at 50% that is
 * 0.5 of one mod out of hundreds - so the bar sat at 0% while the device
 * was visibly pulling four to six files at once, and nothing moved until
 * the first install finished.
 *
 * Six at 50% is three mods of real progress, and that is what it now
 * reports. Capped at 100 because a download can briefly still be listed
 * while its install has already been counted as finished.
 */
export function collectionProgressPercent(
  finished: number,
  total: number,
  inFlightPercents: number[]
): number | undefined {
  const inFlight = inFlightPercents.reduce((sum, p) => sum + p / 100, 0);
  if (total > 0) {
    return Math.min(100, Math.round(((finished + inFlight) / total) * 100));
  }
  if (!inFlightPercents.length) return undefined;
  return Math.round(
    inFlightPercents.reduce((a, b) => a + b, 0) / inFlightPercents.length
  );
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
