// Tiny hand-off store: the browse page selects a mod, the detail page reads it.
// (Decky routes can't easily carry complex objects as params.)
import { NexusMod } from "./api";
import { SupportedGame } from "./games";

export interface SelectedMod {
  game: SupportedGame;
  mod: NexusMod;
}

let current: SelectedMod | undefined;

export function setSelectedMod(sel: SelectedMod | undefined): void {
  current = sel;
}

export function getSelectedMod(): SelectedMod | undefined {
  return current;
}
