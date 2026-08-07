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
// The row of buttons under a mod's or collection's header.
//
// Every button in a row is the SAME width - no exceptions, not even the
// primary one. They shrink together as more actions appear and grow
// together (to a cap) as fewer do, so the row always reads as one set of
// controls rather than a ransom note. Four buttons of four different
// widths is what this replaced.
//
// The mechanism: identical flex-basis with flexGrow/flexShrink of 1 makes
// flex distribute space equally, so widths stay locked to each other; the
// max-width stops a two-button row becoming two slabs.

export const ACTION_ROW: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "10px",
  alignItems: "stretch",
};

export const ACTION_BUTTON: CSSProperties = {
  flexGrow: 1,
  flexShrink: 1,
  flexBasis: "0",
  minWidth: "120px",
  maxWidth: "240px",
};
