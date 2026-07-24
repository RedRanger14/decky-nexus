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
  background: rgba(255, 255, 255, 0.85) !important;
  color: #111 !important;
}
.${WHITE_BUTTON_CLASS_NAME}:hover,
.${WHITE_BUTTON_CLASS_NAME}.gpfocus,
.${WHITE_BUTTON_CLASS_NAME}.gpfocuswithin {
  background: #ffffff !important;
  color: #000 !important;
  box-shadow: inset 0 0 0 2px ${NEXUS_ORANGE};
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
