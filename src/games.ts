// Registry of games the plugin supports. v1: Slay the Spire 2 only.
// appid is the Steam app ID; nexusDomain is the game's slug on nexusmods.com.

export type LogAdapter =
  /** Godot games: ~/.local/share/<userDirName>/logs/godot.log */
  | { kind: "godot"; userDirName: string }
  /** SMAPI games: ~/.config/<configDirName>/ErrorLogs/SMAPI-latest.txt */
  | { kind: "smapi"; configDirName: string };

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
  /** How the framework archive installs: SMAPI's install.dat method, or
   * flatten-and-copy into the game dir (SKSE-style) */
  installKind?: "smapi" | "copyRoot";
  /** Skip files whose name contains any of these (case-insensitive) when
   * auto-picking the download - filters out other stores' builds (e.g.
   * SKSE publishes Steam and GOG variants on the same mod page) */
  avoidFileKeywords?: string[];
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
  /** How to read the game's mod-loader diagnostics. Absent = no
   * load-status badges or game-log viewer for this game. */
  logAdapter?: LogAdapter;
  /** Required community mod loader, if the game has one */
  framework?: GameFramework;
  /** Mod folders bulk operations must never remove (framework components) */
  protectedModFolders?: string[];
  /** How mods install: per-mod folders (default) or merged into a shared
   * data dir with per-file manifests and plugins.txt activation (Skyrim) */
  installMode?: "folder" | "dataDir";
  /** dataDir mode: plugins.txt path relative to the Proton prefix's
   * AppData/Local (e.g. "Skyrim Special Edition/plugins.txt") */
  pluginsTxtSubpath?: string;
  /** dataDir mode: how plugins.txt activates a plugin. "starred"
   * (SSE/FO4): '*Name.esp'; "listed" (FNV/FO3/2011 Skyrim): presence in
   * the file IS activation. Default starred. */
  pluginsTxtStyle?: "starred" | "listed";
  /** Curated "start here" mods featured as the browse page heroes */
  recommendedModIds?: number[];
  /** Prefs-ini settings this game needs to survive the Steam UI taking
   * over the screen (e.g. exclusive fullscreen crashes Proton games when
   * the mod browser opens). Checked and offered as a one-tap fix. */
  displayFix?: {
    /** Path under Documents/My Games/ in the Proton prefix */
    prefsSubpath: string;
    section: string;
    settings: Record<string, string>;
  };
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
    logAdapter: { kind: "godot", userDirName: "SlayTheSpire2" },
    recommendedModIds: [103, 137], // BaseLib, RitsuLib - the ecosystem libraries
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
    recommendedModIds: [2400, 1915], // SMAPI, Content Patcher
    // verified on device: SMAPI logs land in ~/.config/StardewValley/
    logAdapter: { kind: "smapi", configDirName: "StardewValley" },
  },
  489830: {
    appId: 489830,
    displayName: "Skyrim Special Edition",
    nexusDomain: "skyrimspecialedition", // verified: game id 1704, ~135k mods
    installDirName: "Skyrim Special Edition",
    modsSubdir: "Data",
    installMode: "dataDir",
    // Proton game: Plugins.txt lives inside the compat prefix. The game
    // writes it with a capital P (verified on device) - casing matters on
    // the deck's filesystem even though Wine's lookups are insensitive.
    pluginsTxtSubpath: "Skyrim Special Edition/Plugins.txt",
    moddedSaveWarning: false,
    processName: "SkyrimSE.exe",
    framework: {
      name: "SKSE64",
      detectFile: "skse64_loader.exe",
      url: "skse.silverlock.org",
      nexusModId: 30379, // verified: "Skyrim Script Extender (SKSE64)" by SKSE Team
      installKind: "copyRoot",
      // The mod page hosts Steam AND GOG builds as MAIN files; the GOG one
      // (higher file_id) refuses to run against the Steam game.
      avoidFileKeywords: ["GOG"],
      // Standard Deck recipe: swap the launcher for the SKSE loader
      launchOptionsTemplate:
        "bash -c 'exec \"$" +
        "{@/SkyrimSELauncher.exe/skse64_loader.exe}\"' -- %command%",
    },
    recommendedModIds: [12604, 266], // SkyUI, USSEP - the canon starters
    // Exclusive fullscreen dies when gamescope switches to the Steam UI
    // (the classic alt-tab crash) - borderless survives it.
    displayFix: {
      prefsSubpath: "Skyrim Special Edition/SkyrimPrefs.ini",
      section: "Display",
      settings: { "bFull Screen": "0", "bBorderless": "1" },
    },
  },
};

/** Positional params several backend calls need for install-mode dispatch. */
export function modeParams(
  g: SupportedGame
): ["folder" | "dataDir", number, string, "starred" | "listed"] {
  return [
    g.installMode ?? "folder",
    g.appId,
    g.pluginsTxtSubpath ?? "",
    g.pluginsTxtStyle ?? "starred",
  ];
}

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
