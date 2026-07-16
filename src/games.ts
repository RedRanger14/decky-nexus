// Registry of games the plugin supports. v1: Slay the Spire 2 only.
// appid is the Steam app ID; nexusDomain is the game's slug on nexusmods.com.

export interface SupportedGame {
  appId: number;
  displayName: string;
  nexusDomain: string;
  /** Directory name under steamapps/common/ */
  installDirName: string;
  /** Where drop-in mods live, relative to the install dir */
  modsSubdir: string;
  /** Game keeps separate save files for modded and unmodded play */
  moddedSaveWarning: boolean;
  /** Process name (comm) used to detect the game is running */
  processName: string;
  /** Godot user dir under ~/.local/share/ (mod-loader logs live here) */
  godotUserDirName: string;
}

export const SUPPORTED_GAMES: Record<number, SupportedGame> = {
  2868840: {
    appId: 2868840,
    displayName: "Slay the Spire 2",
    nexusDomain: "slaythespire2",
    installDirName: "Slay the Spire 2",
    modsSubdir: "mods",
    moddedSaveWarning: true,
    processName: "SlayTheSpire2",
    godotUserDirName: "SlayTheSpire2",
  },
};

export function getSupportedGame(appId: number | undefined): SupportedGame | undefined {
  return appId === undefined ? undefined : SUPPORTED_GAMES[appId];
}

// v1 is single-game: the full-screen browser falls back to StS2 when no
// supported game is running.
export const DEFAULT_GAME = SUPPORTED_GAMES[2868840];

export const ALL_GAMES: SupportedGame[] = Object.values(SUPPORTED_GAMES);

// ---- Active-game context ----------------------------------------------------
// Which game the plugin is managing right now: the running supported game
// wins; otherwise the user's explicit selection; otherwise the default.
// A selector UI appears in the QAM automatically once the registry has more
// than one game.

let selectedAppId: number | undefined;
const listeners = new Set<() => void>();

export function setSelectedGameAppId(appId: number): void {
  selectedAppId = appId;
  listeners.forEach((fn) => fn());
}

export function subscribeActiveGame(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function getActiveGame(runningAppId: number | undefined): SupportedGame {
  return (
    getSupportedGame(runningAppId) ??
    getSupportedGame(selectedAppId) ??
    DEFAULT_GAME
  );
}
