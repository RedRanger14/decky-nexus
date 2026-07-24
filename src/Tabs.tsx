// The full-screen app's tab strip: Store / Downloads / Manager / Updates.
// LB/RB cycle tabs from anywhere on a page (wire handleTabButtons into
// each page root's onButtonDown); the strip itself is clickable too.
import { Focusable, Navigation, QuickAccessTab } from "@decky/ui";
import { GamepadButton } from "@decky/ui";

import { NEXUS_ORANGE } from "./theme";

export interface TabDef {
  id: string;
  label: string;
  route: string;
}

export const TABS: TabDef[] = [
  { id: "store", label: "Store", route: "/nexus-mods" },
  { id: "downloads", label: "Downloads", route: "/nexus-mods/downloads" },
  { id: "manager", label: "My Mods", route: "/nexus-mods/manager" },
  { id: "updates", label: "Updates", route: "/nexus-mods/updates" },
];

// Every tab switch PUSHES a page onto Steam's nav stack - exiting to the
// QAM must pop them ALL, or B in the QAM "returns" to the stale pages
// left underneath (the recurring B-in-QAM bug). Entry points from the
// QAM reset the counter; switches increment it.
let tabPushes = 0;

export function resetTabStack(): void {
  tabPushes = 0;
}

export function switchTab(currentId: string, direction: 1 | -1): void {
  const idx = TABS.findIndex((t) => t.id === currentId);
  if (idx < 0) return;
  const next = TABS[(idx + direction + TABS.length) % TABS.length];
  tabPushes += 1;
  Navigation.Navigate(next.route);
}

/** The tabbed pages' exit: open the QAM (so gamepad focus lands inside
 * it), then unwind the ENTIRE tab stack - the original page plus one
 * push per tab switch. */
export function exitTabsToQam(): void {
  const pops = tabPushes + 1;
  tabPushes = 0;
  Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);
  setTimeout(() => {
    for (let i = 0; i < pops; i++) Navigation.NavigateBack();
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
              if (!active) {
                tabPushes += 1;
                Navigation.Navigate(tab.route);
              }
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
