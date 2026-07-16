// Registry of games the plugin supports. v1: Slay the Spire 2 only.
// appid is the Steam app ID; nexusDomain is the game's slug on nexusmods.com.

export interface GameFramework {
  /** Community mod loader most mods require (e.g. SMAPI) */
  name: string;
  /** Filename prefix inside the install dir that proves it's installed */
  detectFile: string;
  /** Where to learn about installing it */
  url: string;
  /** The framework's own Nexus mod id - downloads route through Nexus so
   * the author gets credit and download counts */
  nexusModId?: number;
  /** Steam launch options needed after install; {install_path} is replaced */
  launchOptionsTemplate?: string;
}

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
  /** Godot user dir under ~/.local/share/ (mod-loader logs live here).
   * Absent for non-Godot games - load-status/log features hide. */
  godotUserDirName?: string;
  /** Required community mod loader, if the game has one */
  framework?: GameFramework;
  /** Mod folders bulk operations must never remove (framework components) */
  protectedModFolders?: string[];
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
  413150: {
    appId: 413150,
    displayName: "Stardew Valley",
    nexusDomain: "stardewvalley", // verified: game id 1303, ~32k mods
    installDirName: "Stardew Valley",
    modsSubdir: "Mods", // SMAPI convention
    moddedSaveWarning: false, // saves are shared between modded/vanilla
    processName: "StardewValley", // TODO verify comm name on device
    framework: {
      name: "SMAPI",
      detectFile: "StardewModdingAPI",
      url: "smapi.io",
      nexusModId: 2400, // verified: "SMAPI - Stardew Modding API" by Pathoschild
      launchOptionsTemplate: '"{install_path}/StardewModdingAPI" %command%',
    },
    // SMAPI's own bundled components - "uninstall all" keeps these
    protectedModFolders: ["SaveBackup", "ConsoleCommands"],
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
// The plugin always follows what the user is doing: the running supported
// game, else the supported game page they're viewing, else the last
// supported game this session touched, else the default. There is
// deliberately NO manual game selection - unsupported contexts just say so.

let lastActiveAppId: number | undefined;

export function noteActiveGame(appId: number): void {
  lastActiveAppId = appId;
}

export function getLastActiveGame(): SupportedGame | undefined {
  return getSupportedGame(lastActiveAppId);
}

export function getActiveGame(runningAppId: number | undefined): SupportedGame {
  return (
    getSupportedGame(runningAppId) ?? getLastActiveGame() ?? DEFAULT_GAME
  );
}
