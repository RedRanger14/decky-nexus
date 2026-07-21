// Tiny hand-off store: the browse page selects a mod, the detail page reads it.
// (Decky routes can't easily carry complex objects as params.)
import { NexusMod } from "./api";
import { SupportedGame } from "./games";

export interface SelectedMod {
  game: SupportedGame;
  mod: NexusMod;
}

let current: SelectedMod | undefined;

export function setSelectedMod(sel: SelectedMod | undefined): void {
  current = sel;
}

export function getSelectedMod(): SelectedMod | undefined {
  return current;
}

// ---- Browse-state hand-back --------------------------------------------------
// Returning from the detail page must not reset the browse page's search and
// results. The browse page continuously saves its list state here; opening a
// detail sets a flag so the next browse mount restores instead of reloading.
export interface BrowseCache {
  appId: number;
  sort: string;
  search: string;
  mods: NexusMod[];
  total?: number;
  nextOffset: number;
}

let browseCache: BrowseCache | undefined;
let returnToBrowse = false;

export function saveBrowseState(cache: BrowseCache): void {
  browseCache = cache;
}

export function markBrowseReturn(): void {
  returnToBrowse = true;
}

/** One-shot: the saved state, only when returning from a detail page for
 * the same game; always clears the return flag. */
export function takeBrowseRestore(appId: number): BrowseCache | undefined {
  const take = returnToBrowse;
  returnToBrowse = false;
  return take && browseCache?.appId === appId ? browseCache : undefined;
}
