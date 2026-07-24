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

/** Attach to a page root's onButtonDown: LB/RB cycle the tabs. */
export function handleTabButtons(currentId: string) {
  return (evt: CustomEvent) => {
    const button = (evt as any)?.detail?.button;
    if (
      button === GamepadButton.BUMPER_LEFT ||
      button === GamepadButton.BUMPER_RIGHT
    ) {
      // Claim the event: Steam's default bumper behavior jumps focus up
      // a section (to the tab strip), which ate the FIRST LB press and
      // made tab-switching need two clicks.
      (evt as any).preventDefault?.();
      (evt as any).stopPropagation?.();
      switchTab(currentId, button === GamepadButton.BUMPER_LEFT ? -1 : 1);
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
