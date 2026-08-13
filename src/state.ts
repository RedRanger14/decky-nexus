// Tiny hand-off store: the browse page selects a mod, the detail page reads it.
import { collectionProgressPercent } from "./panelRules";
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

// ---- Browse scope hand-off -----------------------------------------------------
// The QAM knows exactly which game it's scoped to - hand it to the store
// explicitly. Ambient resolution (last-active fallback) could go stale
// and show another game's mods/collections under the wrong scope.
let browseGame: SupportedGame | undefined;

export function setBrowseGame(game: SupportedGame | undefined): void {
  browseGame = game;
}

export function getBrowseGame(): SupportedGame | undefined {
  return browseGame;
}

// ---- Collections-list hand-back ----------------------------------------------
// Opening a collection FROM the all-collections list must return there on
// B, not to the store home (the browse page unmounts and forgets its mode).
let returnToCollections = false;

export function markCollectionsReturn(): void {
  returnToCollections = true;
}

/** One-shot: whether the next browse mount should reopen collections mode. */
export function takeCollectionsReturn(): boolean {
  const take = returnToCollections;
  returnToCollections = false;
  return take;
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
  /** Exact transfer accounting from the backend (downloading only). */
  bytesDone?: number;
  bytesTotal?: number;
  /** Smoothed download speed, bytes/second. */
  bps?: number;
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
  // A real row exists now, so the run no longer needs a stand-in phase -
  // otherwise the note would linger and hide the actual percentage.
  if (collectionRun?.note) {
    collectionRun.note = undefined;
    notifyRun();
  }
  const prior = downloads.get(modId);
  downloads.set(modId, {
    modId,
    name,
    // Keep whatever the row already knew: naming a download must not
    // blank its progress back to 0%.
    phase: prior?.phase ?? "starting",
    percent: prior?.percent ?? 0,
    gameAppId,
    bytesDone: prior?.bytesDone,
    bytesTotal: prior?.bytesTotal,
    bps: prior?.bps,
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
  percent: number,
  bytesDone?: number,
  bytesTotal?: number,
  bps?: number
): void {
  const existing = downloads.get(modId);
  if (phase === "done" || phase === "error" || phase === "cancelled") {
    // Move terminal states to the completed list (Downloads page shows
    // them until cleared).
    if (existing) {
      downloads.delete(modId);
      completed.unshift({ ...existing, phase, percent, bps: undefined });
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
    bytesDone: bytesDone ?? existing?.bytesDone,
    bytesTotal: bytesTotal ?? existing?.bytesTotal,
    // Speed only means something mid-download.
    bps: phase === "downloading" ? bps ?? existing?.bps : undefined,
  });
  recordSpeedSample();
  notifyDownloads();
}

/** Sum of live download speeds (bytes/sec) - the graph's data source. */
export function getAggregateBps(): number {
  let sum = 0;
  for (const d of downloads.values()) {
    if (d.phase === "downloading" && d.bps) sum += d.bps;
  }
  return sum;
}

// Speed history lives HERE, recorded on every progress event (throttled),
// so even a mod that downloads in half a second lands a sample - a page
// polling once a second showed tiny mods as "idle".
const speedHistory: { t: number; bps: number }[] = [];
let lastSpeedRecord = 0;

export function recordSpeedSample(): void {
  const now = Date.now();
  if (now - lastSpeedRecord < 200) return;
  lastSpeedRecord = now;
  speedHistory.push({ t: now, bps: getAggregateBps() });
  if (speedHistory.length > 600) {
    speedHistory.splice(0, speedHistory.length - 600);
  }
}

export function getSpeedHistory(): { t: number; bps: number }[] {
  return speedHistory;
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
  const active = Array.from(downloads.values()).map((d) =>
    d.phase === "extracting" ? 100 : d.percent
  );
  return collectionProgressPercent(
    run?.finished ?? 0,
    run?.running ? run.total : 0,
    active
  );
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

// ---- Game state changes -------------------------------------------------------
// Bulk actions (reset to vanilla, uninstall all) live in the installed-mods
// section, but they invalidate what the setup-steps section shows - a
// different component, with its own state. Without this nudge a successful
// reset leaves "Mod loader installed ✓" on screen, which reads as the reset
// having done nothing at all.

const gameStateListeners = new Set<() => void>();

export function subscribeGameState(listener: () => void): () => void {
  gameStateListeners.add(listener);
  return () => {
    gameStateListeners.delete(listener);
  };
}

export function notifyGameStateChanged(): void {
  for (const listener of gameStateListeners) listener();
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
  /** What the run is doing when there are no download rows to show yet.
   * A collection starts by fetching its own manifest - megabytes, with no
   * progress events - so the Downloads page said "nothing downloading"
   * while the run was very much underway. */
  note?: string;
  rows: Record<number, CollectionRowState>;
  /** Display metadata so Downloads entries can open the collection. */
  gameAppId?: number;
  name?: string;
  thumbnailUrl?: string;
  /** Epoch ms when the run began - drives the Downloads page ETA. */
  startedAt?: number;
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
    note: "Reading the collection…",
    rows: {},
    startedAt: Date.now(),
    ...meta,
  };
  notifyRun();
}

/** Rows the finished run left needing manual choices. */
export function getRunSkippedCount(run?: CollectionRun): number {
  if (!run) return 0;
  return Object.values(run.rows).filter((s) => s === "skipped").length;
}

/** Name the current phase, for when there is nothing else to show. */
export function setCollectionNote(note: string): void {
  if (!collectionRun) return;
  collectionRun.note = note;
  notifyRun();
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
