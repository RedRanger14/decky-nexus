import type { CSSProperties } from "react";

// Brand accents for the plugin's full-screen pages. The QAM panel stays
// native Steam styling on purpose - these are only for our own surfaces.
//
// Official Nexus Mods orange (confirmed by Michael, 2026-07-16).
export const NEXUS_ORANGE = "#da8e35";
export const NEXUS_ORANGE_HOVER = "#e6a45a";
export const NEXUS_ORANGE_PRESSED = "#b9792d";

export const ACCENT_SUCCESS = "#8fd48f";
export const ACCENT_DANGER = "#ff6b6b";

// Injected once per page that uses the primary button. Hover covers desktop
// pointers; gpfocus is Steam's gamepad-focus class - the Gaming Mode "hover".
export const PRIMARY_BUTTON_CLASS = "nexus-mods-primary-btn";
// Secondary action buttons need explicit focus states too: inline styles
// override Steam's focus background, leaving text unreadable on focus.
const WHITE_BUTTON_CLASS_NAME = "nexus-mods-white-btn";
const BLUE_BUTTON_CLASS_NAME = "nexus-mods-blue-btn";
export const WHITE_BUTTON_CLASS = WHITE_BUTTON_CLASS_NAME;
export const BLUE_BUTTON_CLASS = BLUE_BUTTON_CLASS_NAME;
export const PRIMARY_BUTTON_CSS = `
.${PRIMARY_BUTTON_CLASS} {
  background: ${NEXUS_ORANGE} !important;
  color: #fff !important;
  font-weight: 600;
}
.${PRIMARY_BUTTON_CLASS}:hover,
.${PRIMARY_BUTTON_CLASS}.gpfocus,
.${PRIMARY_BUTTON_CLASS}.gpfocuswithin {
  background: ${NEXUS_ORANGE_HOVER} !important;
  color: #fff !important;
}
.${PRIMARY_BUTTON_CLASS}:active {
  background: ${NEXUS_ORANGE_PRESSED} !important;
}
.${WHITE_BUTTON_CLASS_NAME} {
  background: rgba(255, 255, 255, 0.6) !important;
  color: #111 !important;
  transition: background 0.12s ease, transform 0.12s ease,
    box-shadow 0.12s ease;
}
.${WHITE_BUTTON_CLASS_NAME}:hover,
.${WHITE_BUTTON_CLASS_NAME}.gpfocus,
.${WHITE_BUTTON_CLASS_NAME}.gpfocuswithin {
  /* Steam's own white-button focus: flip to full white, glow, and a
     slight grow - the idle state stays dimmer so the pop is obvious. */
  background: #ffffff !important;
  color: #000 !important;
  font-weight: 600;
  transform: scale(1.02);
  box-shadow: inset 0 0 0 2px ${NEXUS_ORANGE},
    0 0 14px rgba(255, 255, 255, 0.55);
}
.${BLUE_BUTTON_CLASS_NAME} {
  background: rgba(74, 169, 255, 0.22) !important;
  color: #cfe9ff !important;
}
.${BLUE_BUTTON_CLASS_NAME}:hover,
.${BLUE_BUTTON_CLASS_NAME}.gpfocus,
.${BLUE_BUTTON_CLASS_NAME}.gpfocuswithin {
  background: #4aa9ff !important;
  color: #08243a !important;
  font-weight: 600;
}
`;

// ---- Action rows -----------------------------------------------------------
// The row of buttons under a mod's or collection's header. A page with two
// actions and a page with five should look like the same app: buttons take
// a comfortable width and WRAP onto a second line rather than stretching to
// whatever space is left. Capping each button is what stops a two-button
// page from rendering two enormous slabs.

export const ACTION_ROW: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "10px",
};

export const ACTION_BUTTON: CSSProperties = {
  flexGrow: 1,
  flexBasis: "170px",
  minWidth: "150px",
  maxWidth: "230px",
};

/** The one action the page exists for - wider, but still capped. */
export const ACTION_BUTTON_PRIMARY: CSSProperties = {
  flexGrow: 2,
  flexBasis: "280px",
  minWidth: "240px",
  maxWidth: "360px",
};

/** Icon-only button (Downloads). Sized so it reads as a different kind of
 * control than the labelled actions beside it. */
export const ACTION_BUTTON_ICON: CSSProperties = {
  flexGrow: 0,
  flexShrink: 0,
  // DialogButton fills its container by default; both are needed to make
  // it size to its content instead.
  width: "auto",
  minWidth: "58px",
  padding: "0 14px",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: "6px",
};
