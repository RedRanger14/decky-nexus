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

// ---- Collection hand-off -------------------------------------------------------

import { CollectionSummary } from "./api";

export interface SelectedCollection {
  game: SupportedGame;
  collection: CollectionSummary;
}

let currentCollection: SelectedCollection | undefined;

export function setSelectedCollection(sel: SelectedCollection | undefined): void {
  currentCollection = sel;
}

export function getSelectedCollection(): SelectedCollection | undefined {
  return currentCollection;
}

// ---- Download tracker ---------------------------------------------------------
// Installs emit install_progress events; a module-level store lets the QAM
// show active downloads even when the user navigates away mid-download.

export interface ActiveDownload {
  modId: number;
  name: string;
  phase: string;
  percent: number;
  /** Which game this install belongs to (for row click-through). */
  gameAppId?: number;
  /** Set on collection-run summary entries: clicking opens the
   * collection page instead of a mod page. */
  collectionSlug?: string;
}

const downloads = new Map<number, ActiveDownload>();
const downloadListeners = new Set<() => void>();

function notifyDownloads(): void {
  downloadListeners.forEach((l) => l());
}

export function nameDownload(
  modId: number,
  name: string,
  gameAppId?: number
): void {
  downloads.set(modId, {
    modId,
    name,
    phase: "starting",
    percent: 0,
    gameAppId,
  });
  notifyDownloads();
}

/** Live percent for a mod's active download (collection row fills). */
export function getDownloadPercent(modId: number): number | undefined {
  const d = downloads.get(modId);
  if (!d) return undefined;
  return d.phase === "extracting" ? 100 : d.percent;
}

const completed: ActiveDownload[] = [];

export function updateDownload(
  modId: number,
  phase: string,
  percent: number
): void {
  const existing = downloads.get(modId);
  if (phase === "done" || phase === "error") {
    // Move terminal states to the completed list (Downloads page shows
    // them until cleared).
    if (existing) {
      downloads.delete(modId);
      completed.unshift({ ...existing, phase, percent });
      if (completed.length > 30) completed.pop();
      notifyDownloads();
    }
    return;
  }
  downloads.set(modId, {
    modId,
    name: existing ? existing.name : "Mod " + modId,
    phase,
    percent,
    gameAppId: existing?.gameAppId,
  });
  notifyDownloads();
}

/** Remove an entry without recording an outcome - parked installs
 * (needs_choice/wizard) re-register when the user picks options. */
export function dropDownload(modId: number): void {
  if (downloads.delete(modId)) notifyDownloads();
}

/** Aggregate percent across everything in flight, for the QAM button's
 * fill: mid-collection this blends finished mods with the live one. */
export function getAggregateDownloadPercent(
  run?: CollectionRun
): number | undefined {
  const active = Array.from(downloads.values());
  const avg =
    active.length > 0
      ? active.reduce(
          (sum, d) => sum + (d.phase === "extracting" ? 100 : d.percent),
          0
        ) / active.length
      : undefined;
  if (run?.running && run.total > 0) {
    return Math.round(
      ((run.finished + (avg ?? 0) / 100) / run.total) * 100
    );
  }
  if (avg === undefined) return undefined;
  return Math.round(avg);
}

export function getCompletedDownloads(): ActiveDownload[] {
  return [...completed];
}

export function clearCompletedDownloads(): void {
  completed.length = 0;
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

// ---- Collection batch run ------------------------------------------------------
// The install loop lives OUTSIDE the page component: navigating away must
// not orphan the batch's UI state (the loop itself always survived - the
// page just forgot about it).

export type CollectionRowState =
  | "pending"
  | "installing"
  | "done"
  | "skipped"
  | "failed";

export interface CollectionRun {
  slug: string;
  running: boolean;
  total: number;
  finished: number;
  rows: Record<number, CollectionRowState>;
  /** Display metadata so Downloads entries can open the collection. */
  gameAppId?: number;
  name?: string;
  thumbnailUrl?: string;
}

let collectionRun: CollectionRun | undefined;
const runListeners = new Set<() => void>();

function notifyRun(): void {
  runListeners.forEach((l) => l());
}

export function getCollectionRun(): CollectionRun | undefined {
  return collectionRun;
}

export function subscribeCollectionRun(listener: () => void): () => void {
  runListeners.add(listener);
  return () => {
    runListeners.delete(listener);
  };
}

export function beginCollectionRun(
  slug: string,
  total: number,
  meta?: { gameAppId?: number; name?: string; thumbnailUrl?: string }
): void {
  collectionRun = {
    slug,
    running: true,
    total,
    finished: 0,
    rows: {},
    ...meta,
  };
  notifyRun();
}

/** Rows the finished run left needing manual choices. */
export function getRunSkippedCount(run?: CollectionRun): number {
  if (!run) return 0;
  return Object.values(run.rows).filter((s) => s === "skipped").length;
}

export function setCollectionRow(
  fileId: number,
  state: CollectionRowState
): void {
  if (!collectionRun) return;
  collectionRun.rows[fileId] = state;
  if (state === "done" || state === "skipped" || state === "failed") {
    collectionRun.finished += 1;
  }
  notifyRun();
}

export function endCollectionRun(): void {
  if (!collectionRun) return;
  collectionRun.running = false;
  // Surface the finished run in Completed and clear the banner shortly -
  // a 32/32 banner that never leaves reads as "stuck".
  const skipped = getRunSkippedCount(collectionRun);
  completed.unshift({
    modId: -Math.abs(collectionRun.total * 1000 + collectionRun.finished),
    name:
      `${collectionRun.name ?? "Collection"} · ` +
      `${collectionRun.finished}/${collectionRun.total} processed` +
      (skipped ? ` · ${skipped} need choices` : ""),
    phase: "done",
    percent: 100,
    gameAppId: collectionRun.gameAppId,
    collectionSlug: collectionRun.slug,
  });
  if (completed.length > 30) completed.pop();
  notifyRun();
  notifyDownloads();
  setTimeout(() => {
    collectionRun = undefined;
    notifyRun();
  }, 8000);
}
