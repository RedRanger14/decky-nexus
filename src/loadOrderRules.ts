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
  /** Which rule put it there. "masters" is the boundary between the two
   * groups, which is a place rather than a plugin, so a message about it
   * should not name whichever plugin happens to sit on the line. */
  kind?: "masters" | "needs" | "dependent";
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

type Translate = (text: string, vars?: Record<string, string | number>) => string;

let tr: Translate = (text, vars) => {
  if (!vars) return text;
  let out = text;
  for (const [key, value] of Object.entries(vars)) {
    out = out.split(`{${key}}`).join(String(value));
  }
  return out;
};
let ordinalFn: (n: number) => string = (n) => ordinal(n);

/** Install the UI translator (and its ordinal formatter). Called once by
 * the panel entry point; the node tests keep the English default. */
export function setLoadOrderTranslator(
  fn: Translate,
  ord: (n: number) => string = (n) => ordinal(n)
): void {
  tr = fn;
  ordinalFn = ord;
}

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
  if (e.light && e.master) return tr("Light master file");
  if (e.light) return tr("Light plugin");
  if (e.master) return tr("Master file");
  return tr("Plugin");
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
      why: tr("Master files load before the other plugins, whatever the order"),
      kind: "masters",
    };
  } else {
    min = masters;
    up = {
      name: list[masters - 1]?.name ?? "",
      why: tr("Master files always load first"),
      kind: "masters",
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
      up = {
        name: need,
        why: tr("{title} needs {name} loaded before it", {
          title,
          name: pluginTitle(need),
        }),
        kind: "needs",
      };
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
        why: tr("{name} needs {title} loaded before it", {
          name: pluginTitle(dep.name),
          title,
        }),
        kind: "dependent",
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
      wall: current === b.min ? b.up ?? { name: "", why: tr("Already first") } : undefined,
    };
  }
  if (wanted > b.max) {
    return {
      to: b.max,
      wall: current === b.max ? b.down ?? { name: "", why: tr("Already last") } : undefined,
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

/** Why this plugin cannot be moved at all, or "" when it can be.
 *
 * Michael, on his first run: "I assume there are mods which you cant
 * change the load order of? Its confusing initially why I cant move
 * those so maybe we could add a locked icon". A row that refuses to move
 * has to say so BEFORE it is picked up, not in a toast afterwards.
 */
export function lockReason(list: LoadOrderEntry[], index: number): string {
  const e = list[index];
  if (!e) return "";
  if (!e.positioned) return tr("Switch it on to give it a place in the order");
  const b = moveBounds(list, index);
  if (b.min !== b.max) return "";
  const masters = list.filter((x) => x.master).length;
  if (e.master && masters === 1) {
    return tr("The only master file, so it always loads first");
  }
  const after = !b.up
    ? ""
    : b.up.kind === "masters"
    ? tr("the master files")
    : pluginTitle(b.up.name);
  const before = !b.down
    ? ""
    : b.down.kind === "masters"
    ? tr("the plugins")
    : pluginTitle(b.down.name);
  if (after && before)
    return tr("Has to stay after {after} and before {before}", { after, before });
  if (after) return tr("Has to stay after {after}", { after });
  if (before) return tr("Has to stay before {before}", { before });
  return tr("Nothing to swap it with");
}

/** Every lock reason at once, by plugin name. Computed per load rather
 * than per render: it asks moveBounds about every row, which reads the
 * whole list each time. */
export function lockReasons(
  list: LoadOrderEntry[],
  off: LoadOrderEntry[] = []
): Record<string, string> {
  const out: Record<string, string> = {};
  list.forEach((e, i) => {
    const why = lockReason(list, i);
    if (why) out[e.name] = why;
  });
  for (const e of off) {
    out[e.name] = tr("Switch it on to give it a place in the order");
  }
  return out;
}

/** Plugins that load before a master they need, in the order as shown.
 *
 * An order can arrive with one of these: a collection's own sequence, or
 * a restamp that did not finish. The page says so rather than letting
 * the user discover it when a move is refused. */
export function orderProblems(
  list: LoadOrderEntry[]
): { name: string; needs: string }[] {
  const pos = new Map<string, number>();
  list.forEach((e, i) => pos.set(e.name.toLowerCase(), i));
  const out: { name: string; needs: string }[] = [];
  list.forEach((e, i) => {
    for (const n of e.needs) {
      const j = pos.get(n.toLowerCase());
      if (j !== undefined && j > i) out.push({ name: e.name, needs: n });
    }
  });
  return out;
}

export function problemNote(
  problems: { name: string; needs: string }[]
): string {
  if (problems.length === 0) return "";
  const first = tr("{name} loads before {needs}, which it needs", {
    name: pluginTitle(problems[0].name),
    needs: pluginTitle(problems[0].needs),
  });
  return problems.length === 1
    ? tr("{first}. Sort for me puts it right.", { first })
    : tr("{first}, and {n} more like it. Sort for me puts them right.", {
        first,
        n: problems.length - 1,
      });
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
  return tr("Loads {ord} of {total}", {
    ord: ordinalFn(index + 1),
    total,
  });
}

/** The one line under the title, for someone who has never heard the
 * phrase "load order". */
export function orderSummary(list: LoadOrderEntry[]): string {
  const total = list.length;
  const on = list.filter((e) => e.enabled).length;
  const plural = total === 1 ? "plugin" : "plugins";
  return tr(
    "{total} {plural}, {on} on. Loads top to bottom: when two mods change the same thing, the lower one wins.",
    { total, plural, on }
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
      ? mastersSwitchedOn === 1
        ? tr(" {n} master file that other mods need was switched on.", {
            n: mastersSwitchedOn,
          })
        : tr(" {n} master files that other mods need were switched on.", {
            n: mastersSwitchedOn,
          })
      : "";
  if (moved === 0) {
    return {
      title: tr("Already in a good order"),
      body: tr("Every plugin loads after the ones it needs.") + on,
    };
  }
  return {
    title:
      moved === 1
        ? tr("Moved 1 plugin")
        : tr("Moved {n} plugins", { n: moved }),
    body:
      tr(
        "Each one now loads after the plugins it needs. Nothing else was changed."
      ) + on,
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
  const suffix =
    also.length > 3 ? tr(" and {n} more", { n: also.length - 3 }) : "";
  return enabled
    ? {
        title: tr("Also switched on {names}{more}", { names, more: suffix }),
        body:
          also.length === 1
            ? tr("{name} needs it loaded first.", {
                name: pluginTitle(e.name),
              })
            : tr("{name} needs them loaded first.", {
                name: pluginTitle(e.name),
              }),
      }
    : {
        title: tr("Also switched off {names}{more}", { names, more: suffix }),
        body:
          also.length === 1
            ? tr("It needs {name}, so it can't load without it.", {
                name: pluginTitle(e.name),
              })
            : tr("They need {name}, so they can't load without it.", {
                name: pluginTitle(e.name),
              }),
      };
}

export const CARRY_HINT =
  "Up and down move it. LB and RB jump ten at a time. A puts it down, B puts it back.";

export const IDLE_HINT =
  "A picks a plugin up. Y switches it on or off. X opens the mod's page.";

/** Games with nothing to arrange get told so in one sentence. */
export function unsupportedNote(gameName: string): string {
  return tr(
    "{game} loads its mods in a fixed way, so there is no order to arrange there.",
    { game: gameName }
  );
}
