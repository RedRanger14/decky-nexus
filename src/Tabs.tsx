// The full-screen app's tab strip: Store / Downloads / Manager / Updates.
// LB/RB cycle tabs from anywhere on a page (wire handleTabButtons into
// each page root's onButtonDown); the strip itself is clickable too.
import { Focusable, Navigation } from "@decky/ui";
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

export function switchTab(currentId: string, direction: 1 | -1): void {
  const idx = TABS.findIndex((t) => t.id === currentId);
  if (idx < 0) return;
  const next = TABS[(idx + direction + TABS.length) % TABS.length];
  Navigation.Navigate(next.route);
}

// TEMP (v0.28.2 debug): shows whether the handler actually ran on a
// bumper press - remove once the first-press investigation closes.
let debugToast: ((label: string) => void) | undefined;
export function setTabDebugToast(fn: (label: string) => void): void {
  debugToast = fn;
}

/** Attach to a page root's (and its scroller's) onButtonDown: LB/RB
 * cycle the tabs. No preventDefault/stopPropagation - claiming the
 * event made BOTH bumpers need two presses (v0.28.1 regression). */
export function handleTabButtons(currentId: string) {
  return (evt: CustomEvent) => {
    const button = (evt as any)?.detail?.button;
    if (button === GamepadButton.BUMPER_LEFT) {
      debugToast?.("LB handled → switching");
      switchTab(currentId, -1);
    } else if (button === GamepadButton.BUMPER_RIGHT) {
      debugToast?.("RB handled → switching");
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
            onActivate={() => {
              if (!active) Navigation.Navigate(tab.route);
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
