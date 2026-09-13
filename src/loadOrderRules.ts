// Rules for the Load Order page, kept free of @decky/ui so they can be
// tested outside the Steam client (tests/loadOrder.test.mjs).
//
// The page never writes an order the game cannot use. That is what makes
// a hand-set order honest: the automatic sorter is a stable dependency
// sort, so it leaves any valid order exactly as it found it. Every rule
// here exists to keep the user inside "valid" while they move things,
// with a reason on screen the moment they push against the edge.

export interface LoadOrderEntry {
  name: string;
  enabled: boolean;
  /** Loads in the masters group, which the engine puts before everything
   * else whatever the file says. */
  master: boolean;
  light: boolean;
  /** Masters it names that are in this list, spelled as the list has them. */
  needs: string[];
  /** Masters it names that are not installed at all. */
  missing: string[];
  on_disk: boolean;
  /** The mod it came with, when this plugin installed it. */
  mod: string;
  mod_key: string;
  collection: string;
  mod_id?: number | null;
  /** Why it is off, when the plugin decided rather than the user. */
  skipped: string;
  /** Has a place in the order. False for a New Vegas plugin that is off:
   * those games have no "listed but off", so it has no position yet. */
  positioned: boolean;
}

/** What stopped a move: the plugin standing in the way, and why. An empty
 * name is the edge of the list. */
export interface Wall {
  name: string;
  why: string;
}

export interface MoveBounds {
  /** Lowest index the plugin may land at. */
  min: number;
  /** Highest index the plugin may land at. */
  max: number;
  up?: Wall;
  down?: Wall;
}

const EXT = /\.es[lmp]$/i;

/** "Some Mod - Patch.esp" reads as "Some Mod - Patch" everywhere on the page. */
export function pluginTitle(name: string): string {
  return (name ?? "").replace(EXT, "");
}

export function kindTag(e: LoadOrderEntry): "ESM" | "ESL" | "ESP" {
  if (e.light) return "ESL";
  if (e.master) return "ESM";
  return "ESP";
}

export function kindLabel(e: LoadOrderEntry): string {
  if (e.light && e.master) return "Light master file";
  if (e.light) return "Light plugin";
  if (e.master) return "Master file";
  return "Plugin";
}

const low = (s: string) => s.toLowerCase();

/** Plugins in `list` that name `name` as a master. */
export function dependentsOf(
  list: LoadOrderEntry[],
  name: string
): LoadOrderEntry[] {
  const target = low(name);
  return list.filter((e) => e.needs.some((n) => low(n) === target));
}

/** Where the plugin at `from` may land, and what stands either side.
 *
 * `list` is the positioned plugins in load order, masters first. Indices
 * are landing positions in the sense of moveEntry: remove the plugin,
 * then insert it so that it ends up at that index.
 *
 * Three walls, tightest wins in each direction:
 * - the masters boundary, which nothing crosses;
 * - each master it needs, which must stay above it;
 * - each plugin that needs it, which must stay below it.
 *
 * An order that already breaks a rule (a master listed below its
 * dependent, before "Sort for me" has run) gives contradictory bounds.
 * Then the plugin simply cannot move in the direction that would make it
 * worse, and the wall says why.
 */
export function moveBounds(list: LoadOrderEntry[], from: number): MoveBounds {
  const e = list[from];
  if (!e || !e.positioned) return { min: from, max: from };
  const n = list.length;
  const masters = list.filter((x) => x.master).length;
  const title = pluginTitle(e.name);
  let min = 0;
  let max = n - 1;
  let up: Wall | undefined;
  let down: Wall | undefined;
  if (e.master) {
    max = masters - 1;
    down = {
      name: list[masters]?.name ?? "",
      why: "Master files load before the other plugins, whatever the order",
    };
  } else {
    min = masters;
    up = {
      name: list[masters - 1]?.name ?? "",
      why: "Master files always load first",
    };
  }
  const index = new Map<string, number>();
  list.forEach((x, i) => index.set(low(x.name), i));
  for (const need of e.needs) {
    const j = index.get(low(need));
    if (j === undefined) continue;
    // Above it now: landing right after it is j + 1 (it does not move).
    // Below it now (already wrong): landing at j puts this one after it.
    const floor = j < from ? j + 1 : j;
    if (floor > min) {
      min = floor;
      up = { name: need, why: `${title} needs ${pluginTitle(need)} loaded before it` };
    }
  }
  for (const dep of dependentsOf(list, e.name)) {
    const d = index.get(low(dep.name));
    if (d === undefined) continue;
    const ceiling = d > from ? d - 1 : d;
    if (ceiling < max) {
      max = ceiling;
      down = {
        name: dep.name,
        why: `${pluginTitle(dep.name)} needs ${title} loaded before it`,
      };
    }
  }
  // Already out of place: it can stay, and only move in the direction
  // that does not make things worse.
  if (min > from) min = from;
  if (max < from) max = from;
  return { min, max, up, down };
}

/** One press while carrying: where the plugin goes, and the wall it hit
 * if it could not go that far. The wall is only reported when the press
 * moved nothing, so a long jump that stops short is still a move. */
export function stepTarget(
  list: LoadOrderEntry[],
  from: number,
  current: number,
  delta: number
): { to: number; wall?: Wall } {
  const b = moveBounds(list, from);
  const wanted = current + delta;
  if (wanted < b.min) {
    return {
      to: b.min,
      wall: current === b.min ? b.up ?? { name: "", why: "Already first" } : undefined,
    };
  }
  if (wanted > b.max) {
    return {
      to: b.max,
      wall: current === b.max ? b.down ?? { name: "", why: "Already last" } : undefined,
    };
  }
  return { to: wanted };
}

/** Take the item at `from` out and put it back so it sits at `to`. */
export function moveEntry<T>(list: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) {
    return list.slice();
  }
  const next = list.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

/** How far row `i` slides, in rows, while the plugin at `from` is being
 * held over position `to`. The rows between them make room; nothing else
 * moves. This is what lets the page animate a carry without touching the
 * DOM order until the drop. */
export function shiftFor(i: number, from: number, to: number): -1 | 0 | 1 {
  if (to > from && i > from && i <= to) return -1;
  if (to < from && i >= to && i < from) return 1;
  return 0;
}

export function matchesFilter(e: LoadOrderEntry, query: string): boolean {
  const q = (query ?? "").trim().toLowerCase();
  if (!q) return true;
  return e.name.toLowerCase().includes(q) || (e.mod ?? "").toLowerCase().includes(q);
}

export function ordinal(n: number): string {
  const v = n % 100;
  if (v >= 11 && v <= 13) return `${n}th`;
  switch (n % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
}

export function positionLabel(index: number, total: number): string {
  return `Loads ${ordinal(index + 1)} of ${total}`;
}

/** The one line under the title, for someone who has never heard the
 * phrase "load order". */
export function orderSummary(list: LoadOrderEntry[]): string {
  const total = list.length;
  const on = list.filter((e) => e.enabled).length;
  const plural = total === 1 ? "plugin" : "plugins";
  return (
    `${total} ${plural}, ${on} on. Loads top to bottom: when two mods ` +
    "change the same thing, the lower one wins."
  );
}

/** What "Sort for me" did, from the order before and after. */
export function sortToast(
  before: string[],
  after: string[],
  mastersSwitchedOn = 0
): { title: string; body: string } {
  const pos = new Map<string, number>();
  before.forEach((n, i) => pos.set(n.toLowerCase(), i));
  let moved = 0;
  after.forEach((n, i) => {
    const was = pos.get(n.toLowerCase());
    if (was !== undefined && was !== i) moved += 1;
  });
  const on =
    mastersSwitchedOn > 0
      ? ` ${mastersSwitchedOn} master file${mastersSwitchedOn === 1 ? "" : "s"} that other mods need ${mastersSwitchedOn === 1 ? "was" : "were"} switched on.`
      : "";
  if (moved === 0) {
    return {
      title: "Already in a good order",
      body: "Every plugin loads after the ones it needs." + on,
    };
  }
  return {
    title: `Moved ${moved} plugin${moved === 1 ? "" : "s"}`,
    body:
      "Each one now loads after the plugins it needs. Nothing else was changed." +
      on,
  };
}

/** What a switch did beyond the one plugin, if anything. */
export function toggleToast(
  e: LoadOrderEntry,
  enabled: boolean,
  also: string[]
): { title: string; body: string } | undefined {
  if (!also || also.length === 0) return undefined;
  const names = also.slice(0, 3).map(pluginTitle).join(", ");
  const more = also.length > 3 ? ` and ${also.length - 3} more` : "";
  return enabled
    ? {
        title: `Also switched on ${names}${more}`,
        body: `${pluginTitle(e.name)} needs ${also.length === 1 ? "it" : "them"} loaded first.`,
      }
    : {
        title: `Also switched off ${names}${more}`,
        body: `${also.length === 1 ? "It needs" : "They need"} ${pluginTitle(e.name)}, so ${also.length === 1 ? "it" : "they"} can't load without it.`,
      };
}

export const CARRY_HINT =
  "Up and down move it. LB and RB jump ten at a time. A puts it down, B puts it back.";

export const IDLE_HINT =
  "A picks a plugin up. Y switches it on or off. X opens the mod's page.";

/** Games with nothing to arrange get told so in one sentence. */
export function unsupportedNote(gameName: string): string {
  return (
    `${gameName} loads its mods in a fixed way, so there is no order to ` +
    "arrange there."
  );
}
