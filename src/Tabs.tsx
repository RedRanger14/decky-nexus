// The full-screen app's tab strip: Store / Downloads / Manager / Updates.
// LB/RB cycle tabs from anywhere on a page (wire handleTabButtons into
// each page root's onButtonDown); the strip itself is clickable too.
import { Focusable, Navigation, QuickAccessTab } from "@decky/ui";
import { GamepadButton } from "@decky/ui";

import { popsToExitToQam } from "./navRules";
import { NEXUS_ORANGE } from "./theme";

export interface TabDef {
  id: string;
  label: string;
  route: string;
}

export const DOWNLOADS_ROUTE = "/nexus-mods/downloads";

export const TABS: TabDef[] = [
  { id: "store", label: "Store", route: "/nexus-mods" },
  { id: "downloads", label: "Downloads", route: DOWNLOADS_ROUTE },
  { id: "manager", label: "My Mods", route: "/nexus-mods/manager" },
  { id: "updates", label: "Updates", route: "/nexus-mods/updates" },
  { id: "health", label: "Health", route: "/nexus-mods/health" },
  { id: "settings", label: "Settings", route: "/nexus-mods/settings" },
];

// EVERY push of one of our full-screen pages onto Steam's nav stack has to
// be counted, because exiting to the QAM must pop all of them. Miss one and
// B in the QAM closes it to reveal a stale Nexus page still mounted
// underneath instead of the game - the recurring B-in-QAM bug.
//
// It kept recurring because the count was kept in three places in this file
// while TWENTY call sites pushed pages (browse -> collection -> mod, the
// Downloads shortcut, five QAM entry points), and the reset was called from
// exactly one of them. Under-counting leaves pages behind; over-counting
// pops Steam's own screens. So the counter lives here and pushes go through
// pushOurPage - there is no second place to forget.
let ourDepth = 0;

export function resetTabStack(): void {
  ourDepth = 0;
}

/** Push one of our full-screen pages, counting it. */
export function pushOurPage(route: string): void {
  ourDepth += 1;
  Navigation.Navigate(route);
}

/** Pop one of our pages (B on a page that layers over another). */
export function popOurPage(): void {
  if (ourDepth > 0) ourDepth -= 1;
  Navigation.NavigateBack();
}

/** How deep we currently are. Exported for the exit and for tests. */
export function ourPageDepth(): number {
  return ourDepth;
}

/** Kept for call sites that navigate without going through pushOurPage. */
export function noteTabPush(): void {
  ourDepth += 1;
}

export function switchTab(currentId: string, direction: 1 | -1): void {
  const idx = TABS.findIndex((t) => t.id === currentId);
  if (idx < 0) return;
  const next = TABS[(idx + direction + TABS.length) % TABS.length];
  pushOurPage(next.route);
}

/** The tabbed pages' exit: open the QAM (so gamepad focus lands inside
 * it), then unwind the ENTIRE tab stack - the original page plus one
 * push per tab switch. */
/** Put the QAM back at the top of our panel.
 *
 * Michael: "when I press back to go back to the QAM, it puts me at the
 * bottom of the nexus mods menu". Two causes, and the scroll is the lesser
 * one - Steam restores gamepad focus to whatever was last focused, which is
 * the button near the bottom that opened the page, and focusing it scrolls
 * it back into view. So scrolling alone does not hold; focus has to move
 * too.
 *
 * Deliberately best-effort and silent. This reaches into Steam's own DOM,
 * which we do not own and which changes between client builds, so every
 * step is optional and a failure just leaves the panel where it was.
 */
export function scrollQamPanelToTop(): void {
  try {
    const top = document.querySelector(`.${PANEL_TOP_CLASS}`);
    if (!top) return;
    let el: HTMLElement | null = top.parentElement;
    let scroller: HTMLElement | null = null;
    while (el) {
      if (el.scrollHeight > el.clientHeight) scroller = el;
      el.scrollTop = 0;
      el = el.parentElement;
    }
    // Focus the first thing the D-pad can land on, so Steam does not
    // scroll straight back down to where we were.
    const first = (scroller ?? document).querySelector<HTMLElement>(
      '[tabindex]:not([tabindex="-1"])'
    );
    first?.focus();
    if (scroller) scroller.scrollTop = 0;
  } catch {
    /* never let a cosmetic nicety break going back */
  }
}

/** Marks the top of our QAM panel so the scroll reset can find it. */
export const PANEL_TOP_CLASS = "nexus-panel-top";

export function exitTabsToQam(): void {
  // Exactly as deep as we actually are - not depth+1, which over-popped
  // whenever the page had been reached without a counted push. Rule and
  // reasoning in navRules.popsToExitToQam, with tests.
  const pops = popsToExitToQam(ourDepth);
  ourDepth = 0;
  Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);
  setTimeout(() => {
    for (let i = 0; i < pops; i++) Navigation.NavigateBack();
    // After the pops, not before: each NavigateBack can move focus itself.
    setTimeout(scrollQamPanelToTop, 60);
  }, 50);
}

/** Attach to a page root's (and its scroller's) onButtonDown: LB/RB
 * cycle the tabs. No preventDefault/stopPropagation - claiming the
 * event made BOTH bumpers need two presses (v0.28.1 regression). */
export function handleTabButtons(currentId: string) {
  return (evt: CustomEvent) => {
    const button = (evt as any)?.detail?.button;
    if (button === GamepadButton.BUMPER_LEFT) {
      switchTab(currentId, -1);
    } else if (button === GamepadButton.BUMPER_RIGHT) {
      switchTab(currentId, 1);
    }
  };
}

export function TabBar({ currentId }: { currentId: string }) {
  return (
    <Focusable
      style={{
        display: "flex",
        gap: "4px",
        padding: "8px 0 4px",
        alignItems: "center",
      }}
    >
      <span style={{ fontSize: "11px", opacity: 0.5, marginRight: "4px" }}>
        LB
      </span>
      {TABS.map((tab) => {
        const active = tab.id === currentId;
        return (
          <Focusable
            key={tab.id}
            // Freshly-mounted pages have NO established gamepad focus,
            // so the first bumper press was spent establishing it (the
            // "highlights the tab then works" double-press). Landing
            // focus on the active tab at mount makes press one dispatch.
            autoFocus={active}
            onActivate={() => {
              if (!active) pushOurPage(tab.route);
            }}
            style={{
              padding: "5px 16px",
              borderRadius: "4px",
              fontSize: "13px",
              fontWeight: 600,
              background: active ? NEXUS_ORANGE : "rgba(255,255,255,0.07)",
              color: active ? "#1a1d24" : undefined,
            }}
          >
            {tab.label}
          </Focusable>
        );
      })}
      <span style={{ fontSize: "11px", opacity: 0.5, marginLeft: "4px" }}>
        RB
      </span>
    </Focusable>
  );
}
