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
  const many = modCount >= 400;
  return (
    `With ${modCount.toLocaleString()} mods the screen can stay black for ` +
    `${many ? "several minutes" : "a minute or so"}. That's normal — ` +
    `don't quit, it's still loading.`
  );
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
