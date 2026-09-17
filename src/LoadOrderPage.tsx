// Load Order: arrange the plugins of a game that reads them in sequence.
//
// Skyrim, Fallout 4 and Starfield load plugins in the order plugins.txt
// lists them; Fallout 3 and New Vegas load them by file date. The rule the
// user cares about is the same either way, later wins, and this page is
// where they set it.
//
// Controller first. A row is one focus stop. A picks it up and puts it
// down, the D-pad carries it, and the plugins it depends on act as walls
// it cannot be pushed through, so no order that would crash the game can
// be made here (the rules live in loadOrderRules.ts, with tests). While a
// row is carried the DOM is never reordered: the rows slide with CSS
// transforms and the real reorder happens once, on drop, so Steam's focus
// stays exactly where the thumb left it.
import {
  DialogButton,
  Focusable,
  GamepadButton,
  Router,
  ScrollPanelGroup,
  TextField,
} from "@decky/ui";
import { toaster } from "@decky/api";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import {
  FaArrowsAltV,
  FaExternalLinkAlt,
  FaLock,
  FaSortAmountDown,
  FaUndo,
} from "react-icons/fa";

import {
  LoadOrderState,
  fixLoadOrder,
  getLoadOrder,
  getLoadOrderGames,
  getModDetails,
  setLoadOrder,
  setPluginEnabled,
} from "./api";
import { SectionHeading, WarningBox } from "./chrome";
import { ALL_GAMES, SupportedGame, getActiveGame } from "./games";
import {
  CARRY_HINT,
  IDLE_HINT,
  LoadOrderEntry,
  Wall,
  dependentsOf,
  kindLabel,
  kindTag,
  lockReasons,
  matchesFilter,
  moveBounds,
  moveEntry,
  orderProblems,
  orderSummary,
  problemNote,
  pluginTitle,
  positionLabel,
  shiftFor,
  sortToast,
  stepTarget,
  toggleToast,
  unsupportedNote,
} from "./loadOrderRules";
import { getBrowseGame, setDetailOrigin, setSelectedMod } from "./state";
import {
  TabBar,
  exitTabsToQam,
  handleTabButtons,
  pushOurPage,
} from "./Tabs";
import { NEXUS_ORANGE, PAGE_SCROLLER, PRIMARY_BUTTON_CSS } from "./theme";

const Scroller: any = ScrollPanelGroup;

/** Rows are one fixed height so a carry is arithmetic: the rows between
 * the pick-up and the landing slide by exactly one pitch. */
const ROW_H = 58;
const GAP = 6;
const PITCH = ROW_H + GAP;

const LO_CSS = `
.nexus-lo-row { transition: transform 0.14s ease, box-shadow 0.14s ease, background 0.14s ease; }
.nexus-lo-row.gpfocus, .nexus-lo-row.gpfocuswithin {
  background: rgba(255,255,255,0.13) !important;
  box-shadow: inset 0 0 0 2px rgba(255,255,255,0.92);
}
.nexus-lo-carried, .nexus-lo-carried.gpfocus, .nexus-lo-carried.gpfocuswithin {
  background: rgba(218,142,53,0.24) !important;
  box-shadow: 0 14px 30px rgba(0,0,0,0.6), inset 0 0 0 2px ${NEXUS_ORANGE} !important;
}
.nexus-lo-blocked { box-shadow: inset 0 0 0 2px rgba(255,107,107,0.95) !important; }
.nexus-lo-num {
  width: 50px; height: 100%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-variant-numeric: tabular-nums; font-size: 13px; font-weight: 600;
  opacity: 0.7; border-left: 3px solid rgba(218,142,53,0.32);
  border-radius: 6px 0 0 6px;
}
.nexus-lo-master .nexus-lo-num { border-left-color: rgba(218,142,53,0.8); }
.nexus-lo-num.locked { opacity: 0.45; border-left-color: rgba(255,255,255,0.22); }
.nexus-lo-carried .nexus-lo-num { opacity: 1; color: ${NEXUS_ORANGE}; font-size: 15px; }
.nexus-lo-tag {
  font-size: 10.5px; font-weight: 700; letter-spacing: 0.6px; padding: 3px 7px;
  border-radius: 4px; background: rgba(255,255,255,0.08); opacity: 0.8; flex-shrink: 0;
}
.nexus-lo-pill {
  min-width: 54px; box-sizing: border-box; text-align: center; font-size: 12px;
  font-weight: 700; padding: 5px 10px; border-radius: 999px; flex-shrink: 0;
  background: rgba(255,255,255,0.12); display: inline-flex; gap: 5px;
  align-items: center; justify-content: center; cursor: pointer;
}
.nexus-lo-pill.on { background: ${NEXUS_ORANGE}; color: #1a1d24; }
@keyframes nexus-lo-shake {
  0% { transform: translateX(0); } 25% { transform: translateX(-4px); }
  50% { transform: translateX(4px); } 75% { transform: translateX(-2px); }
  100% { transform: translateX(0); }
}
.nexus-lo-bump { animation: nexus-lo-shake 0.22s ease; }
.nexus-lo-chip { padding: 6px 14px; border-radius: 4px; font-size: 13px; font-weight: 600; background: rgba(255,255,255,0.07); }
.nexus-lo-chip.active { background: ${NEXUS_ORANGE}; color: #1a1d24; }
.nexus-lo-chip.gpfocus, .nexus-lo-chip.gpfocuswithin { box-shadow: inset 0 0 0 2px rgba(255,255,255,0.9); }
`;

interface Carry {
  name: string;
  from: number;
  to: number;
}

interface RowHandlers {
  primary(name: string): void;
  toggle(name: string): void;
  modPage(name: string): void;
  focus(name: string): void;
  button(evt: CustomEvent, name: string): void;
  direction(evt: CustomEvent, name: string): void;
  cancel(evt: CustomEvent): void;
  ref(name: string, el: HTMLDivElement | null): void;
}

interface RowProps {
  entry: LoadOrderEntry;
  /** Position in the load order, 0-based; -1 for a plugin with no place yet. */
  index: number;
  carried: boolean;
  carrying: boolean;
  /** Pixels to slide while another row is carried past this one. */
  shift: number;
  blocked: boolean;
  /** Why it cannot be moved, or "" when it can. */
  lock: string;
  h: RowHandlers;
}

/** One plugin. Memoised so a carry re-renders the two rows that changed,
 * not two thousand. */
const Row = memo(function Row({
  entry,
  index,
  carried,
  carrying,
  shift,
  blocked,
  lock,
  h,
}: RowProps) {
  const off = !entry.enabled;
  const held = Boolean(entry.skipped) || entry.missing.length > 0 || !entry.on_disk;
  const note = entry.skipped
    ? `Off: ${entry.skipped}`
    : entry.missing.length > 0
    ? `Needs ${entry.missing.map(pluginTitle).join(", ")}, which is not installed`
    : !entry.on_disk
    ? "Not installed any more"
    : lock
    ? lock
    : "";
  const cls =
    "nexus-lo-row" +
    (carried ? " nexus-lo-carried" : "") +
    (blocked ? " nexus-lo-blocked" : "") +
    (entry.master ? " nexus-lo-master" : "");
  return (
    <Focusable
      ref={(el: HTMLDivElement | null) => h.ref(entry.name, el)}
      className={cls}
      onActivate={() => h.primary(entry.name)}
      onClick={() => h.primary(entry.name)}
      onSecondaryButton={carrying ? undefined : () => h.modPage(entry.name)}
      onOptionsButton={carrying ? undefined : () => h.toggle(entry.name)}
      onGamepadFocus={() => h.focus(entry.name)}
      onButtonDown={carried ? (e: CustomEvent) => h.button(e, entry.name) : undefined}
      onGamepadDirection={
        carried ? (e: CustomEvent) => h.direction(e, entry.name) : undefined
      }
      onCancelButton={carrying ? (e: CustomEvent) => h.cancel(e) : undefined}
      onOKActionDescription={
        carrying ? (carried ? "Put down" : "Put down here") : lock ? "Can't move" : "Move"
      }
      onSecondaryActionDescription={
        carrying || !entry.mod_id ? undefined : "Mod page"
      }
      onOptionsActionDescription={
        carrying ? undefined : off ? "Switch on" : "Switch off"
      }
      onCancelActionDescription={carrying ? "Put back" : undefined}
      style={{
        height: `${ROW_H}px`,
        boxSizing: "border-box",
        display: "flex",
        alignItems: "center",
        gap: "12px",
        padding: "0 12px 0 0",
        borderRadius: "6px",
        background: entry.master
          ? "rgba(218,142,53,0.07)"
          : "rgba(255,255,255,0.05)",
        opacity: off && !carried ? 0.55 : 1,
        transform: shift ? `translateY(${shift}px)` : undefined,
        position: "relative",
        zIndex: carried ? 3 : undefined,
      }}
    >
      <div className={"nexus-lo-num" + (lock ? " locked" : "")}>
        {lock ? <FaLock size={11} /> : index < 0 ? "·" : index + 1}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: "15px",
            fontWeight: 600,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {pluginTitle(entry.name)}
        </div>
        <div
          style={{
            fontSize: "12px",
            opacity: note ? 0.95 : 0.65,
            color: note ? NEXUS_ORANGE : undefined,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {note || entry.mod || "Not installed by this plugin"}
        </div>
      </div>
      <span className="nexus-lo-tag">{kindTag(entry)}</span>
      <div
        className={"nexus-lo-pill" + (entry.enabled ? " on" : "")}
        onClick={(ev) => {
          ev.stopPropagation();
          if (!carrying) h.toggle(entry.name);
        }}
      >
        {held && off ? <FaLock size={10} /> : null}
        {entry.enabled ? "On" : "Off"}
      </div>
    </Focusable>
  );
});

interface AvailableGame {
  app_id: number;
  total: number;
  enabled: number;
}

const EMPTY: LoadOrderState = {
  ok: true,
  supported: false,
  style: "starred",
  entries: [],
  implicit: [],
};

export default function LoadOrderPage() {
  const pluginGames = ALL_GAMES.filter((g) => g.pluginsTxtSubpath);
  // Same scope as My Mods: the game being browsed or run.
  const scoped =
    getBrowseGame() ??
    getActiveGame(
      Router.MainRunningApp ? Number(Router.MainRunningApp.appid) : undefined
    );
  const [available, setAvailable] = useState<AvailableGame[] | undefined>();
  const [game, setGame] = useState<SupportedGame | undefined>(
    scoped?.pluginsTxtSubpath ? scoped : undefined
  );
  const [state, setState] = useState<LoadOrderState | undefined>();
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [carry, setCarry] = useState<Carry | undefined>();
  const [wall, setWall] = useState<Wall | undefined>();
  const [focused, setFocused] = useState<string | undefined>();
  const [undo, setUndo] = useState<string[][]>([]);
  const [busy, setBusy] = useState(false);

  // Handlers are stable (created once) and read the live values through
  // refs, which is what lets the rows be memoised.
  const rows = useRef(new Map<string, HTMLDivElement>());
  const tops = useRef(new Map<string, number>());
  const scroller = useRef<HTMLElement | null>(null);
  const scrollAt = useRef(0);
  const carryRef = useRef<Carry | undefined>(undefined);
  const stateRef = useRef<LoadOrderState | undefined>(undefined);
  const gameRef = useRef<SupportedGame | undefined>(game);
  const filterRef = useRef("");
  const busyRef = useRef(false);
  const lastPrimary = useRef({ name: "", at: 0 });
  const lastStep = useRef({ button: -1, at: 0 });
  const wallTimer = useRef<number | undefined>(undefined);
  useEffect(() => {
    carryRef.current = carry;
  }, [carry]);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);
  useEffect(() => {
    gameRef.current = game;
  }, [game]);
  useEffect(() => {
    filterRef.current = filter;
  }, [filter]);
  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

  const args = (g: SupportedGame) =>
    [
      g.appId,
      g.installDirName,
      g.pluginsTxtSubpath ?? "",
      g.pluginsTxtStyle ?? "starred",
      g.nexusDomain,
    ] as const;

  const load = async (g: SupportedGame) => {
    setLoading(true);
    try {
      const r = await getLoadOrder(...args(g));
      setState(r.ok && r.supported ? r : EMPTY);
    } catch {
      setState(EMPTY);
    }
    setLoading(false);
  };

  useEffect(() => {
    getLoadOrderGames(
      pluginGames.map((g) => ({
        app_id: g.appId,
        plugins_subpath: g.pluginsTxtSubpath ?? "",
        plugins_style: g.pluginsTxtStyle ?? "starred",
        game_domain: g.nexusDomain,
      }))
    )
      .then((r) => {
        const found = r.ok ? r.games ?? [] : [];
        setAvailable(found);
        // Stay on the scoped game even when it has nothing to arrange
        // yet: Michael opened this with Skyrim selected and got Fallout
        // 4's list, which reads as the wrong page. Only a game with no
        // load order at all (Cyberpunk, Stardew) falls through to the
        // first game that has one.
        if (!game) {
          const first = found.length
            ? pluginGames.find((g) => g.appId === found[0].app_id)
            : undefined;
          if (first) setGame(first);
          else setLoading(false);
        }
      })
      .catch(() => {
        setAvailable([]);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (game) load(game);
  }, [game?.appId]);

  const positioned = (s?: LoadOrderState) =>
    (s?.entries ?? []).filter((e) => e.positioned);

  const entries = state?.entries ?? [];
  const ordered = positioned(state);
  const offTail = entries.filter((e) => !e.positioned);
  // Asked once per load, not per render: every answer reads the list.
  const locks = useMemo(() => lockReasons(ordered, offTail), [state]);
  const masters = ordered.filter((e) => e.master);
  const plugins = ordered.filter((e) => !e.master);

  // ---- focus and scrolling ------------------------------------------------

  const refocus = (name: string) => {
    window.requestAnimationFrame(() => {
      const el = rows.current.get(name);
      el?.focus();
      el?.scrollIntoView({ block: "nearest" });
    });
  };

  const findScroller = (el: HTMLElement | null): HTMLElement | null => {
    let node = el?.parentElement ?? null;
    while (node) {
      if (node.scrollHeight > node.clientHeight + 4) {
        const ov = window.getComputedStyle(node).overflowY;
        if (ov === "auto" || ov === "scroll") return node;
      }
      node = node.parentElement;
    }
    return null;
  };

  /** The carried row's DOM never moves, so the browser will not scroll
   * to follow it. Keep its visual position on screen ourselves. */
  const keepVisible = (targetName: string) => {
    const sc = scroller.current;
    const top0 = tops.current.get(targetName);
    if (!sc || top0 === undefined) return;
    const visualTop = top0 - (sc.scrollTop - scrollAt.current);
    const view = sc.getBoundingClientRect();
    const margin = 150;
    if (visualTop < view.top + margin) {
      sc.scrollTop -= view.top + margin - visualTop;
    } else if (visualTop + ROW_H > view.bottom - margin) {
      sc.scrollTop += visualTop + ROW_H - (view.bottom - margin);
    }
  };

  const showWall = (w?: Wall) => {
    if (!w) return;
    setWall(w);
    if (wallTimer.current) window.clearTimeout(wallTimer.current);
    wallTimer.current = window.setTimeout(() => setWall(undefined), 1600);
  };

  const bump = (name: string) => {
    const num = rows.current.get(name)?.querySelector(".nexus-lo-num");
    if (!num) return;
    num.classList.remove("nexus-lo-bump");
    void (num as HTMLElement).offsetWidth;
    num.classList.add("nexus-lo-bump");
  };

  // ---- carrying -------------------------------------------------------------

  const beginCarry = (name: string) => {
    const list = positioned(stateRef.current);
    const from = list.findIndex((e) => e.name === name);
    if (from < 0) {
      toaster.toast({
        title: "Switch it on first",
        body: `${pluginTitle(name)} is off, so it has no place in the order yet.`,
      });
      return;
    }
    // Measured once, while nothing is moving: every slide is arithmetic
    // on these until the drop.
    const measured = new Map<string, number>();
    for (const e of list) {
      const el = rows.current.get(e.name);
      if (el) measured.set(e.name, el.getBoundingClientRect().top);
    }
    tops.current = measured;
    scroller.current = findScroller(rows.current.get(name) ?? null);
    scrollAt.current = scroller.current?.scrollTop ?? 0;
    setWall(undefined);
    const c = { name, from, to: from };
    carryRef.current = c;
    setCarry(c);
  };

  const drop = async (toOverride?: number) => {
    const c = carryRef.current;
    const s = stateRef.current;
    const g = gameRef.current;
    if (!c || !s || !g) return;
    const to = toOverride ?? c.to;
    carryRef.current = undefined;
    setCarry(undefined);
    setWall(undefined);
    if (to === c.from) {
      refocus(c.name);
      return;
    }
    const list = positioned(s);
    const before = list.map((e) => e.name);
    const next = moveEntry(list, c.from, to).map((e) => e.name);
    setBusy(true);
    try {
      const r = await setLoadOrder(...args(g), next);
      if (!r.ok) {
        toaster.toast({ title: "Could not move it", body: r.error ?? "" });
      } else {
        setUndo((u) => [...u.slice(-19), before]);
        setState(r);
      }
    } finally {
      setBusy(false);
      refocus(c.name);
    }
  };

  const cancelCarry = () => {
    const c = carryRef.current;
    carryRef.current = undefined;
    setCarry(undefined);
    setWall(undefined);
    if (c) refocus(c.name);
  };

  const step = (delta: number) => {
    const c = carryRef.current;
    const list = positioned(stateRef.current);
    if (!c) return;
    const { to, wall: w } = stepTarget(list, c.from, c.to, delta);
    if (to === c.to) {
      showWall(w);
      bump(c.name);
      return;
    }
    setWall(undefined);
    const next = { ...c, to };
    carryRef.current = next;
    setCarry(next);
    keepVisible(list[to].name);
  };

  /** A D-pad press can arrive as a button and as a direction; whichever
   * lands first moves the row, the other is swallowed. */
  const carryPress = (evt: CustomEvent) => {
    const b = Number((evt as any)?.detail?.button);
    let delta = 0;
    if (b === GamepadButton.DIR_UP) delta = -1;
    else if (b === GamepadButton.DIR_DOWN) delta = 1;
    else if (b === GamepadButton.BUMPER_LEFT) delta = -10;
    else if (b === GamepadButton.BUMPER_RIGHT) delta = 10;
    else if (b === GamepadButton.DIR_LEFT || b === GamepadButton.DIR_RIGHT) {
      evt.preventDefault();
      evt.stopPropagation();
      return;
    } else return;
    evt.preventDefault();
    evt.stopPropagation();
    const now = Date.now();
    const repeat = Boolean((evt as any)?.detail?.is_repeat);
    if (
      !repeat &&
      lastStep.current.button === b &&
      now - lastStep.current.at < 40
    ) {
      return; // the same press, seen twice
    }
    lastStep.current = { button: b, at: now };
    step(delta);
  };

  // ---- the row handlers, created once -------------------------------------

  const handlers = useRef<RowHandlers>({
    primary(name) {
      const now = Date.now();
      if (
        lastPrimary.current.name === name &&
        now - lastPrimary.current.at < 250
      ) {
        return; // A and click both fired for one press
      }
      lastPrimary.current = { name, at: now };
      if (busyRef.current) return;
      const c = carryRef.current;
      if (!c) {
        if (filterRef.current) {
          // Searching is for finding. The move happens in the full list,
          // so clear the search, land on the row, then pick it up.
          setFilter("");
          window.setTimeout(() => {
            const el = rows.current.get(name);
            el?.scrollIntoView({ block: "center" });
            el?.focus();
            beginCarry(name);
          }, 80);
          return;
        }
        beginCarry(name);
        return;
      }
      if (c.name === name) {
        void drop();
        return;
      }
      // Pointer: put the carried plugin where this row is.
      const list = positioned(stateRef.current);
      const idx = list.findIndex((e) => e.name === name);
      if (idx < 0) return;
      const b = moveBounds(list, c.from);
      const to = Math.max(b.min, Math.min(b.max, idx));
      if (to !== idx) showWall(idx < c.from ? b.up : b.down);
      void drop(to);
    },
    async toggle(name) {
      const s = stateRef.current;
      const g = gameRef.current;
      if (!s || !g || carryRef.current || busyRef.current) return;
      const e = (s.entries ?? []).find((x) => x.name === name);
      if (!e) return;
      setBusy(true);
      try {
        const r = await setPluginEnabled(...args(g), name, !e.enabled);
        if (!r.ok) {
          toaster.toast({
            title: e.enabled ? "Could not switch it off" : "Could not switch it on",
            body: r.error ?? "",
          });
          return;
        }
        setState(r);
        const t = toggleToast(e, !e.enabled, r.also ?? []);
        if (t) toaster.toast(t);
      } finally {
        setBusy(false);
        refocus(name);
      }
    },
    async modPage(name) {
      const s = stateRef.current;
      const g = gameRef.current;
      if (!s || !g || carryRef.current) return;
      const e = (s.entries ?? []).find((x) => x.name === name);
      if (!e?.mod_id) {
        toaster.toast({
          title: "No mod page for this one",
          body: `${pluginTitle(name)} was not installed by this plugin, so there is nothing to open.`,
        });
        return;
      }
      const result = await getModDetails(g.nexusDomain, e.mod_id);
      if (result.ok && result.mod) {
        setSelectedMod({ game: g, mod: result.mod });
        setDetailOrigin("browse");
        pushOurPage("/nexus-mods/mod");
      } else {
        toaster.toast({ title: "Could not open mod", body: result.error ?? "" });
      }
    },
    focus(name) {
      setFocused(name);
    },
    button(evt) {
      carryPress(evt);
    },
    direction(evt) {
      carryPress(evt);
    },
    cancel(evt) {
      if (!carryRef.current) return;
      evt.preventDefault();
      evt.stopPropagation();
      cancelCarry();
    },
    ref(name, el) {
      if (el) rows.current.set(name, el);
      else rows.current.delete(name);
    },
  });

  // ---- page-level actions -------------------------------------------------

  const sortForMe = async () => {
    const g = gameRef.current;
    const s = stateRef.current;
    if (!g || !s || carryRef.current) return;
    setBusy(true);
    try {
      const before = positioned(s).map((e) => e.name);
      const r = await fixLoadOrder(...args(g));
      if (!r.ok) {
        toaster.toast({ title: "Could not sort", body: r.error ?? "" });
        return;
      }
      const fresh = await getLoadOrder(...args(g));
      if (fresh.ok && fresh.supported) {
        setState(fresh);
        setUndo((u) => [...u.slice(-19), before]);
        toaster.toast(
          sortToast(
            before,
            positioned(fresh).map((e) => e.name),
            r.enabled_masters ?? 0
          )
        );
      }
    } finally {
      setBusy(false);
    }
  };

  const undoLast = async () => {
    const g = gameRef.current;
    const prev = undo[undo.length - 1];
    if (!g || !prev || carryRef.current) return;
    setBusy(true);
    try {
      const r = await setLoadOrder(...args(g), prev);
      if (r.ok) {
        setUndo((u) => u.slice(0, -1));
        setState(r);
        // Said out loud, because on a 220-row list the row that moved
        // back is usually off screen and nothing else changes.
        toaster.toast({
          title: "Put back",
          body: "The load order is as it was before that change.",
        });
      } else {
        toaster.toast({ title: "Could not undo", body: r.error ?? "" });
      }
    } finally {
      setBusy(false);
    }
  };

  const onRootCancel = () => {
    if (carryRef.current) {
      cancelCarry();
      return;
    }
    exitTabsToQam();
  };

  const tabButtons = (evt: CustomEvent) => {
    // LB/RB jump the carried row instead of switching tabs.
    if (carryRef.current) return;
    handleTabButtons("loadorder")(evt);
  };

  // ---- render helpers -----------------------------------------------------

  const indexOf = new Map<string, number>();
  ordered.forEach((e, i) => indexOf.set(e.name, i));

  const carriedShift = (): number => {
    if (!carry) return 0;
    const target = ordered[carry.to];
    const a = tops.current.get(carry.name);
    const b = target ? tops.current.get(target.name) : undefined;
    if (a === undefined || b === undefined) return (carry.to - carry.from) * PITCH;
    return b - a;
  };

  /** How far a row that is NOT the carried one slides, in pixels.
   *
   * Measured, not PITCH times the direction: the section headings sit
   * between rows, so the gap either side of one is bigger than a row
   * pitch and a fixed step would leave the rows visibly misaligned
   * exactly where the list changes section. */
  const slideShift = (i: number): number => {
    if (!carry) return 0;
    const dir = shiftFor(i, carry.from, carry.to);
    if (dir === 0) return 0;
    const from = tops.current.get(ordered[i]?.name ?? "");
    const to = tops.current.get(ordered[i + dir]?.name ?? "");
    if (from === undefined || to === undefined) return dir * PITCH;
    return to - from;
  };

  const renderRow = (e: LoadOrderEntry) => {
    const i = indexOf.get(e.name) ?? -1;
    const carried = carry?.name === e.name;
    let shift = 0;
    if (carry && i >= 0) {
      shift = carried ? carriedShift() : slideShift(i);
    }
    return (
      <Row
        key={e.name}
        entry={e}
        index={i}
        carried={carried}
        carrying={Boolean(carry)}
        shift={shift}
        blocked={wall?.name === e.name}
        lock={locks[e.name] ?? ""}
        h={handlers.current}
      />
    );
  };

  const focusedEntry = entries.find((e) => e.name === focused);
  const problems = useMemo(() => orderProblems(ordered), [state]);
  const carriedEntry = carry ? entries.find((e) => e.name === carry.name) : undefined;
  const inspected = carriedEntry ?? focusedEntry;
  const filtering = filter.trim().length > 0;
  const showGameChips =
    (available ?? []).filter((a) => a.app_id !== game?.appId).length > 0;

  return (
    <Focusable
      onButtonDown={tabButtons}
      onCancel={onRootCancel}
      style={{ marginTop: "40px", height: "calc(100% - 40px)" }}
    >
      <Scroller
        focusable={false}
        onButtonDown={tabButtons}
        style={PAGE_SCROLLER}
      >
        <style>{PRIMARY_BUTTON_CSS + LO_CSS}</style>
        <TabBar currentId="loadorder" />

        <Focusable
          style={{
            display: "flex",
            alignItems: "center",
            gap: "14px",
            margin: "6px 0 2px",
          }}
        >
          <h2 style={{ margin: 0 }}>
            Load Order
            {game && (
              <span style={{ opacity: 0.6, fontWeight: 400 }}>
                {"  ·  "}
                {game.displayName}
              </span>
            )}
          </h2>
          {game && state?.supported && (
            // In the header row, not above the list: Steam's text field
            // keeps a D-pad press that lands on it, so a field alone
            // between the header and the rows was a trap (found on
            // device, first build). Beside the buttons, LEFT and RIGHT
            // walk out of it and the vertical path never enters it.
            <div style={{ flex: "1 1 auto", minWidth: "200px", maxWidth: "420px" }}>
              <TextField
                label="Find a plugin or mod"
                value={filter}
                bShowClearAction={true}
                disabled={Boolean(carry)}
                onChange={(e) => setFilter(e?.target?.value ?? "")}
                onKeyDown={(e) => {
                  if (e.key === "Enter") (e.target as HTMLElement).blur();
                }}
              />
            </div>
          )}
          <div style={{ marginLeft: "auto", display: "flex", gap: "8px" }}>
            <DialogButton
              onClick={sortForMe}
              disabled={busy || !state?.supported || Boolean(carry)}
              style={{
                minWidth: "0",
                width: "auto",
                padding: "8px 16px",
                fontSize: "13px",
                display: "flex",
                alignItems: "center",
                gap: "8px",
              }}
            >
              <FaSortAmountDown size={12} /> Sort for me
            </DialogButton>
            <DialogButton
              onClick={undoLast}
              disabled={busy || undo.length === 0 || Boolean(carry)}
              style={{
                minWidth: "0",
                width: "auto",
                padding: "8px 16px",
                fontSize: "13px",
                display: "flex",
                alignItems: "center",
                gap: "8px",
              }}
            >
              <FaUndo size={12} /> Undo
            </DialogButton>
          </div>
        </Focusable>

        {showGameChips && (
          <Focusable
            style={{ display: "flex", gap: "6px", flexWrap: "wrap", margin: "8px 0 4px" }}
          >
            {pluginGames
              .filter(
                (g) =>
                  g.appId === game?.appId ||
                  (available ?? []).some((a) => a.app_id === g.appId)
              )
              .map((g) => {
                const a = (available ?? []).find((x) => x.app_id === g.appId);
                const active = game?.appId === g.appId;
                return (
                  <Focusable
                    key={g.appId}
                    className={"nexus-lo-chip" + (active ? " active" : "")}
                    onActivate={() => {
                      if (!active && !carry) setGame(g);
                    }}
                    onClick={() => {
                      if (!active && !carry) setGame(g);
                    }}
                  >
                    {g.displayName}
                    <span style={{ opacity: 0.7, fontWeight: 400 }}>
                      {" "}
                      {a?.total ?? 0}
                    </span>
                  </Focusable>
                );
              })}
          </Focusable>
        )}

        {loading && <div style={{ opacity: 0.8, marginTop: "12px" }}>Reading the load order…</div>}

        {!loading && (!game || !state?.supported) && (
          <div style={{ opacity: 0.85, marginTop: "12px", maxWidth: "720px", lineHeight: 1.5 }}>
            {scoped && !scoped.pluginsTxtSubpath && (
              <div style={{ marginBottom: "8px" }}>{unsupportedNote(scoped.displayName)}</div>
            )}
            {game ? (
              <div>
                {game.displayName} has no plugins yet. Install a mod with an .esp or
                .esm file and it appears here.
              </div>
            ) : (available?.length ?? 0) === 0 ? (
              <div>
                Nothing to arrange yet. Install a mod for one of these games and it
                appears here:{" "}
                {pluginGames.map((g) => g.displayName).join(", ")}.
              </div>
            ) : (
              <div>Pick a game above.</div>
            )}
          </div>
        )}

        {!loading && game && state?.supported && (
          <>
            <div style={{ fontSize: "13px", opacity: 0.7, margin: "4px 0 10px" }}>
              {orderSummary(ordered)}
            </div>
            {/* A fault the order arrived with. Said here, once, rather
                than discovered when a move is refused: the backend
                allows moves that do not make things worse, so this can
                sit unfixed for as long as the user likes. */}
            {problems.length > 0 && !carry && (
              <WarningBox
                title={
                  problems.length === 1
                    ? "One plugin loads too early"
                    : `${problems.length} plugins load too early`
                }
                body={problemNote(problems)}
                action={{ label: "Sort for me", onClick: sortForMe }}
              />
            )}

            <Focusable style={{ display: "flex", gap: "18px", alignItems: "flex-start" }}>
              {/* ---- the list ----
                  ONE Focusable holding every row, headings included as
                  plain children. Three sibling Focusables inside a plain
                  div is what shipped first, and Steam's gamepad focus
                  never left the first of them: Michael could reach the
                  master rows and nothing below. My Mods carries the same
                  note for the same reason. */}
              <Focusable
                style={{
                  flex: "1 1 auto",
                  minWidth: 0,
                  display: "flex",
                  flexDirection: "column",
                  gap: `${GAP}px`,
                }}
              >
                {(state.implicit?.length ?? 0) > 0 && (
                  <div style={{ fontSize: "12px", opacity: 0.55, margin: "6px 0 2px 4px" }}>
                    The game's own files load first: {state.implicit!.join(", ")}.
                  </div>
                )}

                {masters.length > 0 && (
                  <SectionHeading title={`Master files · ${masters.length}`} />
                )}
                {masters.filter((e) => matchesFilter(e, filter)).map(renderRow)}

                <SectionHeading title={`Plugins · ${plugins.length}`} />
                {plugins.length === 0 && (
                  <div style={{ opacity: 0.65, fontSize: "12.5px" }}>
                    No plugins yet. Mods with an .esp or .esm file land here.
                  </div>
                )}
                {plugins.filter((e) => matchesFilter(e, filter)).map(renderRow)}

                {filtering &&
                  ordered.filter((e) => matchesFilter(e, filter)).length === 0 && (
                    <div style={{ opacity: 0.65, fontSize: "12.5px", marginTop: "6px" }}>
                      Nothing matches "{filter.trim()}".
                    </div>
                  )}

                {offTail.length > 0 && (
                  <>
                    <SectionHeading title={`Off · ${offTail.length}`} />
                    <div style={{ fontSize: "12px", opacity: 0.6, margin: "-4px 0 4px 4px" }}>
                      {game.displayName} has no place for a plugin that is off.
                      Switch one on and it joins the order.
                    </div>
                  </>
                )}
                {offTail.filter((e) => matchesFilter(e, filter)).map(renderRow)}
              </Focusable>

              {/* ---- the inspector ---- */}
              <Focusable
                style={{
                  flex: "0 0 300px",
                  position: "sticky",
                  top: "8px",
                  alignSelf: "flex-start",
                  padding: "16px 18px",
                  borderRadius: "8px",
                  background: carry
                    ? "linear-gradient(160deg, rgba(218,142,53,0.22), rgba(255,255,255,0.03))"
                    : "rgba(255,255,255,0.05)",
                  border: `1px solid ${carry ? NEXUS_ORANGE + "88" : "rgba(255,255,255,0.08)"}`,
                  minHeight: "200px",
                  transition: "background 0.2s ease, border-color 0.2s ease",
                }}
              >
                {carry && carriedEntry ? (
                  <>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "8px",
                        color: NEXUS_ORANGE,
                        fontSize: "12px",
                        fontWeight: 700,
                        letterSpacing: "0.6px",
                        textTransform: "uppercase",
                      }}
                    >
                      <FaArrowsAltV /> Moving
                    </div>
                    <div style={{ fontSize: "19px", fontWeight: 700, margin: "6px 0 2px" }}>
                      {pluginTitle(carriedEntry.name)}
                    </div>
                    <div style={{ fontSize: "13px", opacity: 0.8 }}>
                      {carry.to === carry.from
                        ? positionLabel(carry.from, ordered.length)
                        : `${positionLabel(carry.from, ordered.length).replace("Loads ", "Was ")}, now ${
                            positionLabel(carry.to, ordered.length).split(" ")[1]
                          }`}
                    </div>
                    <div style={{ fontSize: "13px", opacity: 0.85, marginTop: "14px", lineHeight: 1.5 }}>
                      {CARRY_HINT}
                    </div>
                    {wall && (
                      <div
                        style={{
                          marginTop: "14px",
                          padding: "10px 12px",
                          borderRadius: "6px",
                          background: "rgba(255,107,107,0.16)",
                          borderLeft: "3px solid rgba(255,107,107,0.9)",
                          fontSize: "13px",
                          lineHeight: 1.45,
                        }}
                      >
                        {wall.why}
                      </div>
                    )}
                  </>
                ) : inspected ? (
                  <>
                    <div
                      style={{
                        fontSize: "12px",
                        fontWeight: 700,
                        letterSpacing: "0.6px",
                        textTransform: "uppercase",
                        opacity: 0.6,
                      }}
                    >
                      {kindLabel(inspected)}
                    </div>
                    <div
                      style={{
                        fontSize: "19px",
                        fontWeight: 700,
                        margin: "6px 0 2px",
                        lineHeight: 1.2,
                        wordBreak: "break-word",
                      }}
                    >
                      {pluginTitle(inspected.name)}
                    </div>
                    <div style={{ fontSize: "13px", opacity: 0.8 }}>
                      {inspected.positioned && indexOf.has(inspected.name)
                        ? positionLabel(indexOf.get(inspected.name)!, ordered.length)
                        : "Not in the order yet"}
                      {" · "}
                      {inspected.enabled ? "On" : "Off"}
                    </div>
                    <div style={{ fontSize: "13px", marginTop: "12px", lineHeight: 1.5 }}>
                      <div style={{ opacity: 0.6, fontSize: "11.5px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                        Part of
                      </div>
                      <div>{inspected.mod || "Not installed by this plugin"}</div>
                    </div>
                    {(inspected.needs.length > 0 || inspected.missing.length > 0) && (
                      <div style={{ fontSize: "13px", marginTop: "10px", lineHeight: 1.5 }}>
                        <div style={{ opacity: 0.6, fontSize: "11.5px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                          Needs above it
                        </div>
                        <div>
                          {inspected.needs.map(pluginTitle).join(", ")}
                          {inspected.missing.length > 0 && (
                            <span style={{ color: NEXUS_ORANGE }}>
                              {inspected.needs.length > 0 ? ", " : ""}
                              {inspected.missing.map(pluginTitle).join(", ")} (not installed)
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                    {dependentsOf(ordered, inspected.name).length > 0 && (
                      <div style={{ fontSize: "13px", marginTop: "10px", lineHeight: 1.5 }}>
                        <div style={{ opacity: 0.6, fontSize: "11.5px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                          Needed by
                        </div>
                        <div>
                          {dependentsOf(ordered, inspected.name)
                            .slice(0, 6)
                            .map((d) => pluginTitle(d.name))
                            .join(", ")}
                          {dependentsOf(ordered, inspected.name).length > 6
                            ? ` and ${dependentsOf(ordered, inspected.name).length - 6} more`
                            : ""}
                        </div>
                      </div>
                    )}
                    {inspected.skipped && (
                      <div style={{ fontSize: "13px", marginTop: "10px", color: NEXUS_ORANGE, lineHeight: 1.45 }}>
                        Switched off by the plugin: {inspected.skipped}.
                      </div>
                    )}
                    {Boolean(inspected.mod_id) && (
                      <DialogButton
                        onClick={() => handlers.current.modPage(inspected.name)}
                        style={{
                          marginTop: "14px",
                          minWidth: "0",
                          width: "100%",
                          padding: "9px 12px",
                          fontSize: "13px",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          gap: "8px",
                        }}
                      >
                        <FaExternalLinkAlt size={11} /> Mod page
                      </DialogButton>
                    )}
                    <div style={{ fontSize: "12px", opacity: 0.6, marginTop: "14px", lineHeight: 1.5 }}>
                      {IDLE_HINT}
                    </div>
                  </>
                ) : (
                  <>
                    <div style={{ fontSize: "17px", fontWeight: 700 }}>How this works</div>
                    <div style={{ fontSize: "13px", opacity: 0.85, marginTop: "8px", lineHeight: 1.55 }}>
                      Mods load from the top down. If two of them change the same
                      thing, the one lower in the list wins.
                    </div>
                    <div style={{ fontSize: "13px", opacity: 0.85, marginTop: "10px", lineHeight: 1.55 }}>
                      {IDLE_HINT} A plugin can't be moved above something it needs,
                      and the page tells you when you bump into that.
                    </div>
                  </>
                )}
              </Focusable>
            </Focusable>
          </>
        )}
      </Scroller>
    </Focusable>
  );
}
