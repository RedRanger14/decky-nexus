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

// ---- Detail-page origin ------------------------------------------------------
// B on the detail page should return WHERE THE USER CAME FROM: the browse
// page (default back-nav) or the QAM panel (the installed-mods eye button).

let detailOrigin: "browse" | "qam" = "browse";

export function setDetailOrigin(origin: "browse" | "qam"): void {
  detailOrigin = origin;
}

export function getDetailOrigin(): "browse" | "qam" {
  return detailOrigin;
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

// ---- Download tracker ---------------------------------------------------------
// Installs emit install_progress events; a module-level store lets the QAM
// show active downloads even when the user navigates away mid-download.

export interface ActiveDownload {
  modId: number;
  name: string;
  phase: string;
  percent: number;
}

const downloads = new Map<number, ActiveDownload>();
const downloadListeners = new Set<() => void>();

function notifyDownloads(): void {
  downloadListeners.forEach((l) => l());
}

export function nameDownload(modId: number, name: string): void {
  downloads.set(modId, { modId, name, phase: "starting", percent: 0 });
  notifyDownloads();
}

export function updateDownload(
  modId: number,
  phase: string,
  percent: number
): void {
  const existing = downloads.get(modId);
  if (phase === "done" || phase === "error") {
    // Keep terminal states visible briefly, then clear.
    if (existing) {
      downloads.set(modId, { ...existing, phase, percent });
      notifyDownloads();
      setTimeout(() => {
        downloads.delete(modId);
        notifyDownloads();
      }, 8000);
    }
    return;
  }
  downloads.set(modId, {
    modId,
    name: existing ? existing.name : "Mod " + modId,
    phase,
    percent,
  });
  notifyDownloads();
}

export function getDownloads(): ActiveDownload[] {
  return Array.from(downloads.values());
}

export function subscribeDownloads(listener: () => void): () => void {
  downloadListeners.add(listener);
  return () => {
    downloadListeners.delete(listener);
  };
}
