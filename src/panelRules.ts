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
