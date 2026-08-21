// Registry of games the plugin supports. v1: Slay the Spire 2 only.
// appid is the Steam app ID; nexusDomain is the game's slug on nexusmods.com.

import type { InstallMode } from "./api";

/** Every Nexus mod id that IS a framework for this game: the primary one,
 * the extras, and every alias of both.
 *
 * Frameworks arrive through Step 1, not the mod list, so anything asking
 * "is this installed?" has to count them or it reports the game's own
 * foundations as missing. The health check learned this when it called all
 * 77 of a Stardew setup's mods broken for want of SMAPI; the mod page had
 * the same bug for longer and more quietly, because it only counted the
 * PRIMARY framework - so Cyberpunk's CET read as present while RED4ext,
 * ArchiveXL, TweakXL and redscript all showed as "needs installing" on
 * every mod page that required them. Michael found it: "some required mods
 * that are installed are being marked as orange".
 *
 * One function, every caller, so the two can never disagree again.
 */
/** Mods the built-for-an-older-patch rule must never skip: the game's
 * framework, plus any dll loader other mods load through. */
export function stalenessExemptModIds(game: SupportedGame): number[] {
  return [...frameworkModIds(game), ...(game.me3?.loaderModIds ?? [])];
}

export function frameworkModIds(game: SupportedGame): number[] {
  return [
    game.framework?.nexusModId,
    ...(game.framework?.aliasModIds ?? []),
    ...(game.extraFrameworks ?? []).flatMap((fw) => [
      fw.nexusModId,
      ...(fw.aliasModIds ?? []),
    ]),
  ].filter((id): id is number => typeof id === "number");
}

export type LogAdapter =
  /** Godot games: ~/.local/share/<userDirName>/logs/godot.log */
  | { kind: "godot"; userDirName: string }
  /** SMAPI games: ~/.config/<configDirName>/ErrorLogs/SMAPI-latest.txt */
  | { kind: "smapi"; configDirName: string }
  /** redscript games (Cyberpunk): <install>/r6/logs/redscript_rCURRENT.log.
   * No field needed - the log lives inside the game folder, which the
   * backend already knows. Read by the health check rather than by the
   * load-status badges: redscript reports on FILES, not on mods, so it
   * answers "did your script stack compile" rather than "did this mod
   * load". */
  | { kind: "redscript" };

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
  /** Other Nexus mod ids that ARE this framework (mirrors/repacks) - mod
   * requirements point at any of them, and all should read as installed
   * once the framework is. */
  aliasModIds?: number[];
  /** How the framework archive installs: SMAPI's install.dat method, or
   * flatten-and-copy into the game dir (SKSE-style) */
  installKind?: "smapi" | "copyRoot";
  /** Skip files whose name contains any of these (case-insensitive) when
   * auto-picking the download - filters out other stores' builds (e.g.
   * SKSE publishes Steam and GOG variants on the same mod page) */
  avoidFileKeywords?: string[];
  /** copyRoot target relative to the game root when the loader lives
   * deeper (UE4SS: "Pal/Binaries/Win64") */
  installSubdir?: string;
  /** Steam launch options needed after install; {install_path} is replaced */
  launchOptionsTemplate?: string;
  /** The framework IS an ordinary Nexus mod that installs into the mods
   * folder like any other - Slay the Spire 2's BaseLib. Step 1 then routes
   * through the normal mod installer instead of the framework one, which
   * flattens archives into the game root and would scatter BaseLib's files
   * across mods/ instead of mods/BaseLib/. */
  installAsMod?: boolean;
  /** Reset-to-vanilla: game-root files AND directories starting with any
   * of these prefixes belong to the framework (copyRoot installs keep no
   * manifest) and are removed on reset (e.g. ["skse64"]). */
  cleanupPrefixes?: string[];
  /** Reset-to-vanilla: mods the framework bundles with itself (SMAPI
   * ships ConsoleCommands and SaveBackup). No install record exists for
   * them, so reset has to be told they belong to the loader. */
  frameworkModFolders?: string[];
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
  /** The game shows its own launcher before starting, so the long grey
   * screen begins when the user presses Play THERE, not when they press
   * Launch here. The wait notice has to warn about it in advance. */
  ownLauncher?: boolean;
  /** Mod count above which the startup wait is minutes rather than a
   * moment. Defaults to 400, which was measured on Bethesda games; a game
   * paying a heavy per-mod startup cost sets its own. */
  longWaitAtMods?: number;
  /** Required community mod loader, if the game has one */
  framework?: GameFramework;
  /** Additional frameworks beyond the primary (CP77 script mods need
   * 3-4 at once) - installed together by the one-button Step 1. */
  extraFrameworks?: GameFramework[];
  /** Upgrade the Proton prefix's VC++ runtime, during Step 1 and via a
   * repair row whenever it falls behind. Games' own Steam install
   * scripts leave an ancient CRT in the prefix - CP77's is 2019
   * (14.28), Skyrim's is 2017 (14.0) - and any mod binary that links
   * the runtime dynamically then fails to load: CET and RED4ext with
   * error 998, and 37 of Skyrim's SKSE plugins with nothing but "fatal
   * error occurred while loading plugin" in the SKSE log. */
  prefixRuntimeFix?: boolean;
  /** Script-extender log under the prefix's Documents/My Games, e.g.
   * "Skyrim Special Edition/SKSE/skse64.log". Reading it tells us which
   * DLL plugins the extender refused to load, so a stale mod can be set
   * aside instead of stopping the game with a modal the user has to
   * guess at. */
  scriptExtenderLog?: string;
  /** The exception address of a known, reproducible boot crash for this
   * game, as it appears in the crash log (e.g. "01D74A0"). Enables the
   * automated hunt: it has to know WHICH crash it is chasing, because
   * mods also crash on forms their own disabled plugins used to provide
   * and those say nothing about the fault. */
  crashSignature?: string;
  /** DLLs the automated hunt must NOT park. Everything else is set aside
   * for the duration: with half the load order off, any SKSE plugin that
   * looks up a form from its own plugin crashes, and the hunt cannot tell
   * that apart from progress. These are the ones the game needs anyway. */
  huntKeepDlls?: string[];
  /** Written only once the game is in the world, not at the menu - the
   * save-load hunt's pass condition. Papyrus for Bethesda games; needs
   * bEnableLogging=1, which enablePapyrusLogging sets. */
  inGameLog?: string;
  /** The ini holding [Papyrus], so the log above can be switched on. */
  inGameLogIni?: string;
  /** RE Engine pak-patch chain (RE4 remake): every .pak in an archive
   * takes the next re_chunk_000.pak.patch_XXX.pak number in the game
   * root; uninstalls renumber survivors to close the gap. */
  pakPatchLayout?: boolean;
  /** Cyberpunk's layout: game-root-relative payloads across known roots
   * (bin/red4ext/r6/engine/archive) with exact-file records; bare
   * .archive files go flat into archive/pc/mod. */
  cp77Layout?: boolean;
  /** Mod folders bulk operations must never remove (framework components) */
  protectedModFolders?: string[];
  /** How mods install: per-mod folders (default), merged into a shared
   * data dir with per-file manifests and plugins.txt activation (Skyrim),
   * or the me3 tier (FromSoft) where mods live outside the game folder
   * entirely and a generated .me3 profile activates them. */
  installMode?: InstallMode;
  /** FromSoft games loaded by me3. The game folder is never written to:
   * me3 boots the real exe instead of the anti-cheat launcher, so mods
   * live in the plugin's own profile dir. Matchmaking stays blocked and
   * modded saves stay separate - both enforced in the backend, not
   * offered as settings. */
  me3?: {
    /** Real exe me3 runs, relative to the install dir - shown to the
     * user so it's clear the anti-cheat launcher is bypassed, not
     * patched or replaced. */
    gameExe: string;
    /** Mods everyone installs first, named in the setup copy */
    headlineMod?: string;
    /** Nexus mod ids of dll loaders that other mods load THROUGH.
     *
     * Exempt from the built-for-an-older-patch rule. A proxy loader does
     * not search the game's code for a signature, so a game patch cannot
     * age it out - Elden Mod Loader was last updated in 2022 and boots
     * clean on a 2026 build. These are not the game's framework (me3 is,
     * and we ship it), so frameworkModIds does not cover them: without
     * this the date rule skipped every mod in a collection including the
     * one that works. Michael: "it skipped every mod again!"
     */
    loaderModIds?: number[];
  };
  /** dataDir mode: plugins.txt path relative to the Proton prefix's
   * AppData/Local (e.g. "Skyrim Special Edition/plugins.txt") */
  pluginsTxtSubpath?: string;
  /** dataDir mode: how plugins.txt activates a plugin. "starred"
   * (SSE/FO4): '*Name.esp'; "listed" (FNV/FO3/2011 Skyrim): presence in
   * the file IS activation. Default starred. */
  pluginsTxtStyle?: "starred" | "listed";
  /** Curated "start here" mods featured as the browse page heroes */
  recommendedModIds?: number[];
  /** Frameworkless games whose stock launcher hangs under Proton (FO3's
   * 2008 launcher freezes at the Play screen and never finishes first-run
   * setup): a one-tap Step swaps the launcher for the game exe and seeds
   * the Documents ini the launcher would have created. Carries the
   * setupInis application too - there's no framework step to do it. */
  launcherBypass?: {
    launchOptionsTemplate: string;
    /** Game-dir default ini to copy into Documents when missing. */
    seedIni?: { sourceRel: string; prefsSubpath: string };
  };
  /** Games that ship a native Linux build that mod loaders can't hook:
   * when nativeMarker exists in the install dir, mods need the Windows
   * build - offer a one-tap switch to the given Proton tool. */
  protonRequired?: {
    /** File that only exists in the native Linux build */
    nativeMarker: string;
    /** Steam Play tool name to force (e.g. "proton_experimental") */
    tool: string;
  };
  /** The Witcher 3's layered layout: "mod" and "dlc" prefixed folders,
   * menu-XML filelist registration, and a script-conflict gate. */
  witcherLayout?: boolean;
  /** Flat-file games (Cyberpunk archive/pc/mod): the game loads FILES
   * from the mods dir, not folders - installs move matching files flat
   * with per-file records. Lists the loadable extensions. */
  flatModExtensions?: string[];
  /** Helldivers 2: mods are <hash>.patch_N file swaps dropped flat into
   * data/, renumbered per archive hash so two mods patching the same
   * archive coexist instead of the second silently overwriting the first. */
  hd2Layout?: boolean;
  /** Mods that must never take a hero slot: desktop tools with big
   * endorsement counts (mod managers) that a Gaming Mode plugin cannot run
   * and should not showcase. The install-time tool refusal still catches
   * them if someone finds them by search. */
  heroExcludeModIds?: number[];
  /** Frostbite games (Battlefront II): mods are .fbmod archives that must be
   * COMPILED into a ModData tree, and the game is redirected at it. There is
   * no per-mod install - any change recompiles the whole enabled set - so
   * these games route through the frosty* backend calls rather than the
   * normal installer. See docs/frosty-swbf2/WORKING.md. */
  frostbite?: boolean;
  /** Shown as a banner at the top of the QAM panel: support for this game
   * is real but rough. Honest signposting beats silent rough edges. */
  underConstruction?: string;
  /** ReShade support: where the game's exe lives (injector files land
   * there), and the launch options Proton needs to load a native dxgi. */
  reshade?: { subdir: string; launchOptionsTemplate: string };
  /** Shown at the top of the game panel until the given Documents-file
   * exists - for games that must run once before modding works (their
   * launcher creates the activation config on first run). For
   * launcherBypass games this renders as a "launch once" checklist Step
   * instead of a banner. */
  firstRunNotice?: {
    message: string;
    /** Documents-relative file whose existence clears the notice */
    goneWhenDocsFile: string;
  };
  /** Windows exe patchers this game's modding scene depends on (FO3's
   * ESM Patcher, Anniversary Patcher): downloaded from Nexus Mods and
   * run inside the game's Proton prefix by a one-tap checklist Step.
   * Success is judged by the files each tool exists to modify. */
  prefixTools?: Array<{
    name: string;
    nexusModId: number;
    description: string;
    /** This tool cannot run headless, so it is never launched
     * automatically - it is listed as an optional manual job instead.
     *
     * Used by the Fallout 3 ESM Patcher, which its own page describes as
     * "run the exe installer ... both destination locations in the
     * installer are towards your Fallout 3 Data folder": a GUI with two
     * path fields. It is a bespoke Delphi program with no command line at
     * all - no Inno or NSIS silent switch to reach for - so feeding it
     * Enter presses does nothing and it sat there for the full five-minute
     * timeout, producing no output and changing nothing, while Step 3
     * reported "(1)" forever.
     *
     * The Anniversary Patcher next to it DOES work headless (verified: exe
     * downgraded to 1.7.0.3 on device), so this is per-tool rather than
     * per-game. */
    needsDesktopMode?: boolean;
    /** Substring picking the tool exe inside the archive */
    exeHint?: string;
    /** Skip download files whose name contains any of these */
    avoidFileKeywords?: string[];
    /** Game-root-relative files whose change proves the tool worked */
    verifyChangedFiles: string[];
    /** What this tool backed up, and where it goes, so reset can undo it:
     * [backup, original]. Reset removes mods; a tool that rewrote the game
     * exe is not a mod, and leaving it in place made "reset game modding"
     * come back with Step 3 still ticked and nothing a user could do about
     * it. Only set this where the tool makes the backup itself. */
    restoreOnReset?: [string, string];
    timeoutSec?: number;
  }>;
  /** Every directory this game's mods write into, relative to the game
   * root and BESIDES modsSubdir.
   *
   * Reset removes mod files by install record, so an ordinary uninstall is
   * fine - but a record lost to an interrupted install leaves a file no
   * reset can find. Two orphaned .reds files in Cyberpunk's r6/scripts had
   * been failing redscript compilation for weeks, and one bad .reds
   * disables EVERY script mod. Declared here so the baseline covers them
   * and reset can tell an orphan from a game file. */
  modWriteDirs?: string[];
  /** Games whose built-in gamepad support is broken/removed (FO3 lost
   * its when GFWL was excised): a persistent note telling the user how
   * to play on controller. */
  controllerNotice?: string;
  /** Launcher-selected modules (Bannerlord): activation lives in an XML
   * under Documents in the Proton prefix; module Ids come from each
   * module's SubModule.xml. */
  launcherXmlSubpath?: string;
  /** UE4SS games: where script/Blueprint mods route. Lua and native mods
   * become folders (with enabled.txt) under modsSubdir; Blueprint paks go
   * flat into logicModsSubdir. Absent = UE4SS mods are refused. */
  ue4ss?: {
    modsSubdir: string;
    logicModsSubdir: string;
  };
  /** Ini blocks required for mods to load at all (e.g. Fallout 4's
   * loose-files invalidation). Applied automatically after the framework
   * installs; files are created if missing. */
  setupInis?: Array<{
    prefsSubpath: string;
    section: string;
    settings: Record<string, string>;
  }>;
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
    // BaseLib is this game's SMAPI - Michael's observation, and the mods
    // agree: five of one collection's mods declare it, and Enchanted
    // Offerings installed on its own did not load at all without it
    // ("Tried to load mod EnchantedOfferings, but it depends on mods which
    // have not been loaded: BaseLib!"). Nobody should have to know that.
    framework: {
      name: "BaseLib",
      detectFile: "mods/BaseLib/BaseLib.dll",
      url: "nexusmods.com/slaythespire2/mods/103",
      nexusModId: 103,
      installAsMod: true,
      frameworkModFolders: ["BaseLib"],
    },
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
      // Verified on device: SMAPI leaves StardewModdingAPI{,.dll,.deps.json,
      // .runtimeconfig.json,.xml} plus the smapi-internal/ directory. Its
      // save-backups/ folder is deliberately NOT listed - that's user data.
      cleanupPrefixes: ["StardewModdingAPI", "smapi-internal"],
      frameworkModFolders: ["SaveBackup", "ConsoleCommands", "ErrorHandler"],
    },
    // SMAPI's own bundled components - "uninstall all" keeps these
    protectedModFolders: ["SaveBackup", "ConsoleCommands", "ErrorHandler"],
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
    // Skyrim's Steam install script leaves VC++ 14.0 (2017) in the
    // prefix. Every SKSE plugin that links the runtime dynamically then
    // fails to load - 37 of them on a Gate To Sovngarde install, and
    // vcruntime140_1.dll isn't in that redist at all.
    prefixRuntimeFix: true,
    scriptExtenderLog: "Skyrim Special Edition/SKSE/skse64.log",
    // Left empty on purpose: the hunt reads the target address out of the
    // newest crash log. Hardcoding one meant it could only ever chase the
    // crash we already knew about - the moment 19 bad plugins fixed
    // SkyrimSE.exe+01D74A0 on device, a different crash surfaced at
    // +01D8845 and a pinned signature was useless against it.
    crashSignature: "",
    // Engine Fixes patches the memory manager - vanilla Skyrim will not
    // load 1,900 plugins without it. CrashLogger is how the hunt reads
    // its own results, so parking it blinds the search.
    huntKeepDlls: ["EngineFixes.dll", "CrashLogger.dll"],
    inGameLog: "Skyrim Special Edition/Logs/Script/Papyrus.0.log",
    inGameLogIni: "Skyrim Special Edition/Skyrim.ini",
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
      cleanupPrefixes: ["skse64"],
    },
    recommendedModIds: [12604, 266], // SkyUI, USSEP - the canon starters
  },
  377160: {
    appId: 377160,
    displayName: "Fallout 4",
    nexusDomain: "fallout4", // verified: game id 1151, ~75k mods
    installDirName: "Fallout 4", // TODO verify on device
    modsSubdir: "Data",
    installMode: "dataDir",
    // Verified: folder is "Fallout4" (no space); starred format like SSE.
    // The game auto-loads Fallout4.esm + DLC - never write them here.
    pluginsTxtSubpath: "Fallout4/Plugins.txt",
    pluginsTxtStyle: "starred",
    moddedSaveWarning: false,
    processName: "Fallout4.exe",
    // Same class as Skyrim: F4SE plugins link the runtime dynamically.
    prefixRuntimeFix: true,
    scriptExtenderLog: "Fallout4/F4SE/f4se.log",
    framework: {
      name: "F4SE",
      detectFile: "f4se_loader.exe",
      url: "f4se.silverlock.org",
      nexusModId: 42147, // verified: "Fallout 4 Script Extender (F4SE)"
      installKind: "copyRoot",
      // Same recipe as SKSE: swap the launcher for the loader.
      // TODO verify on device (extrapolated from the SSE recipe).
      launchOptionsTemplate:
        "bash -c 'exec \"$" +
        "{@/Fallout4Launcher.exe/f4se_loader.exe}\"' -- %command%",
      cleanupPrefixes: ["f4se"],
    },
    recommendedModIds: [4598, 21497], // verified: UFO4P, Mod Configuration Menu
    // Loose files don't load until archive invalidation is enabled.
    setupInis: [
      {
        prefsSubpath: "Fallout4/Fallout4Custom.ini",
        section: "Archive",
        settings: {
          bInvalidateOlderFiles: "1",
          sResourceDataDirsFinal: "",
        },
      },
    ],
  },
  22380: {
    appId: 22380,
    displayName: "Fallout: New Vegas",
    nexusDomain: "newvegas", // verified: game id 130, ~41k mods
    installDirName: "Fallout New Vegas", // verified on device
    modsSubdir: "Data",
    installMode: "dataDir",
    // FNV predates the starred format: a plugin listed in the file IS
    // active (no '*' prefix).
    pluginsTxtSubpath: "FalloutNV/Plugins.txt",
    pluginsTxtStyle: "listed",
    moddedSaveWarning: false,
    processName: "FalloutNV.exe",
    framework: {
      name: "xNVSE",
      detectFile: "nvse_loader.exe",
      url: "github.com/xNVSE/NVSE",
      nexusModId: 67883, // verified live: "New Vegas Script Extender (NVSE xNVSE)"
      installKind: "copyRoot",
      // Same recipe as SKSE/F4SE: swap the launcher for the loader.
      launchOptionsTemplate:
        "bash -c 'exec \"$" +
        "{@/FalloutNVLauncher.exe/nvse_loader.exe}\"' -- %command%",
      // "nvse" matches top-level names only, so xNVSE's own Data/NVSE
      // folder (nvse_config.ini, and Plugins/ where NVSE-plugin mods land)
      // survived every reset - and then got recorded as vanilla by the
      // next baseline, on a game we had just reinstalled from scratch to
      // get a clean one. A prefix containing a slash is treated as an
      // EXACT relative path, never a prefix.
      //
      // Only New Vegas: Skyrim's Data/SKSE and Fallout 4's Data/F4SE do
      // not exist on the test device, so adding them would be a guess
      // about games that currently work.
      cleanupPrefixes: ["nvse", "Data/NVSE"],
    },
    // verified live: NVAC (the domain's top mod) + YUP (51664, 5.3M dl)
    recommendedModIds: [53635, 51664],
    // Loose files don't load until archive invalidation is enabled.
    setupInis: [
      {
        prefsSubpath: "FalloutNV/Fallout.ini",
        section: "Archive",
        settings: {
          bInvalidateOlderFiles: "1",
          SInvalidationFile: "",
        },
      },
    ],
    firstRunNotice: {
      message:
        "Launch the game once first - it creates the config files mods need.",
      goneWhenDocsFile: "My Games/FalloutNV/Fallout.ini",
    },
  },
  2050650: {
    appId: 2050650,
    displayName: "Resident Evil 4",
    nexusDomain: "residentevil42023", // verified: game id 5195
    // Capcom's dir name really has two spaces (verified on device).
    installDirName: "RESIDENT EVIL 4  BIOHAZARD RE4",
    // Pak-patch mods live in the game ROOT with engine-dictated names -
    // there is no mods dir. This path never exists, so the folder
    // scanner stays quiet; rows come from the per-file records instead.
    modsSubdir: "._nexus_mods_unused",
    pakPatchLayout: true,
    moddedSaveWarning: false,
    processName: "re4.exe",
    framework: {
      name: "REFramework",
      detectFile: "dinput8.dll",
      url: "github.com/praydog/REFramework",
      nexusModId: 12, // verified live: 24k endorsements
      installKind: "copyRoot",
      launchOptionsTemplate: 'WINEDLLOVERRIDES="dinput8=n,b" %command%',
      cleanupPrefixes: ["dinput8"],
    },
    // verified live 2026-08-05: top non-tool, non-adult content mods
    recommendedModIds: [117, 1479],
  },
  22370: {
    appId: 22370,
    displayName: "Fallout 3",
    nexusDomain: "fallout3", // verified: game id 120, ~17k mods
    installDirName: "Fallout 3 goty", // verified on device (GOTY SKU)
    modsSubdir: "Data",
    installMode: "dataDir",
    // FO3 matches FNV: a plugin listed in the file IS active (no '*').
    pluginsTxtSubpath: "Fallout3/Plugins.txt",
    pluginsTxtStyle: "listed",
    moddedSaveWarning: false,
    processName: "Fallout3.exe",
    // NO framework yet: FOSE can't hook the current Steam exe (1.7.0.4)
    // - the community fix (Anniversary Patcher) patches the exe inside
    // the prefix, a future tier. Plain data mods work without it.
    // verified live 2026-08-05: UF3P (85k endorsements) + Fellout (71k),
    // both FOSE-free data mods.
    recommendedModIds: [19122, 2672],
    // The stock launcher froze at the Play screen on device (2026-08-05)
    // BEFORE creating FALLOUT.INI - boot the game exe directly and seed
    // the ini from the game's own defaults.
    launcherBypass: {
      // Runs whichever exe is actually there, decided at launch rather
      // than when this was written.
      //
      // Fallout 3's stock launcher freezes on this device, so the original
      // form of this swapped in Fallout3.exe. Then Fallout Rebirth+ turned
      // out to need FOSE - which only loads when the game is STARTED
      // through fose_loader.exe - and a static substitution would have
      // meant a setting that is right for one collection and wrong for
      // the next. Deciding in the shell means installing or removing FOSE
      // later just works, with no step to re-run and no stored state to
      // go stale.
      // Fallout3.exe, deliberately - NOT fose_loader.exe.
      //
      // Fallout 3's stock launcher freezes on this device, so this swaps in
      // the game exe. When FOSE turned out to be the missing piece of
      // Fallout Rebirth+ I briefly routed this through fose_loader.exe,
      // which was wrong and cost a morning: the Fallout Anniversary Patcher
      // that Step 3 applies "automatically loads FOSE when using
      // Fallout3.exe to start the game", and it also rewrites the binary to
      // downgrade it to 1.7.0.3. FOSE's loader then refuses the exe it does
      // not recognise - "You have an unknown version of Fallout ...
      // CRC = 6E3D50D1" - and closes the game.
      //
      // So the patcher is what loads FOSE here, and this only has to start
      // the game. FOSE still has to BE installed for the patcher to find,
      // which is the part that was actually missing.
      launchOptionsTemplate:
        "bash -c 'exec \"$" +
        "{@/Fallout3Launcher.exe/Fallout3.exe}\"' -- %command%",
      seedIni: {
        sourceRel: "Fallout_default.ini",
        prefsSubpath: "Fallout3/FALLOUT.INI",
      },
    },
    setupInis: [
      {
        // Loose files don't load until archive invalidation is enabled.
        prefsSubpath: "Fallout3/FALLOUT.INI",
        section: "Archive",
        settings: {
          bInvalidateOlderFiles: "1",
          SInvalidationFile: "",
        },
      },
      {
        // FO3 on modern many-core CPUs freezes without the threading
        // caps - the standard community fix, safe on all hardware.
        prefsSubpath: "Fallout3/FALLOUT.INI",
        section: "General",
        settings: {
          bUseThreadedAI: "1",
          iNumHWThreads: "2",
        },
      },
      {
        // Steam Input drives FO3 via synthetic keyboard/mouse (the game
        // lost native gamepad support with GFWL); Gamebryo drops that
        // input when it thinks its window is backgrounded - gamescope
        // focus wobbles make that constant without these.
        prefsSubpath: "Fallout3/FALLOUT.INI",
        section: "Controls",
        settings: {
          "bBackground Mouse": "1",
          "bBackground Keyboard": "1",
        },
      },
      {
        // Intro movies hang under Proton's DirectShow (wine quartz
        // graph stalls decoding them - caught live in the boot log,
        // worse with modded movie replacers). Skipping them boots
        // straight to the menu.
        prefsSubpath: "Fallout3/FALLOUT.INI",
        section: "General",
        settings: {
          SIntroSequence: "",
          SMainMenuMovieIntro: "",
          SCreditsMenuMovie: "",
        },
      },
      {
        // Exclusive fullscreen never PRESENTS under gamescope: the game
        // runs (menu music audible) behind a stuck Steam loading logo.
        // Windowed mode displays - gamescope fullscreens it anyway.
        // Same crash class as Skyrim SE/FO4's display doctor.
        prefsSubpath: "Fallout3/FalloutPrefs.ini",
        section: "Display",
        settings: {
          "bFull Screen": "0",
        },
      },
    ],
    firstRunNotice: {
      message:
        "Boot the game to the main menu once before installing mods - it "
        + "finishes first-run setup and proves the launch fix works. Note: "
        + "mods requiring FOSE aren't supported yet.",
      // The game writes this on every real boot (FALLOUT.INI can't be
      // the marker anymore - Step 1 seeds it).
      goneWhenDocsFile: "My Games/Fallout3/RendererInfo.txt",
    },
    // Exe patchers the FO3 scene depends on, runnable in the prefix.
    // Order matters: the Anniversary Patcher rewrites Fallout3.exe;
    // the ESM Patcher then repairs the master files UF3P requires.
    prefixTools: [
      {
        name: "Fallout Anniversary Patcher",
        nexusModId: 24913, // verified live: MAIN v1.1
        // The patcher writes Fallout3_backup.exe before replacing the exe,
        // which is what lets a reset genuinely undo it.
        restoreOnReset: ["Fallout3_backup.exe", "Fallout3.exe"],
        description:
          "Patches the game exe: 4GB memory, crash fixes, and FOSE "
          + "support - the community's standard stability fix.",
        exeHint: "patcher",
        avoidFileKeywords: ["ttw", "downgrader"],
        verifyChangedFiles: ["Fallout3.exe"],
        timeoutSec: 120,
      },
      {
        name: "Unofficial Fallout 3 ESM Patcher",
        nexusModId: 25717,
        // A GUI installer asking for two destination paths. Nothing here
        // can drive it, and it is an optimisation patch rather than
        // something the game needs to start - so it is offered as an
        // optional manual job instead of a step that can never complete.
        needsDesktopMode: true, // verified live: paired EN/FR mains - avoid FR
        description:
          "Repairs errors inside the game's own master files - required "
          + "by the Updated Unofficial Fallout 3 Patch.",
        exeHint: "patcher",
        avoidFileKeywords: ["non officiel", "guide"],
        verifyChangedFiles: [
          "Data/Fallout3.esm",
          "Data/Anchorage.esm",
          "Data/ThePitt.esm",
          "Data/BrokenSteel.esm",
          "Data/PointLookout.esm",
          "Data/Zeta.esm",
        ],
        timeoutSec: 300,
      },
    ],
    // Bethesda's GFWL-removal update (2021) took the native 360-pad
    // support with it - the game can ONLY be driven by keyboard/mouse,
    // so Steam Input must be enabled to translate the controller.
    controllerNotice:
      "Fallout 3's controller support is broken since GFWL was removed. "
      + "In the layout screen: 1) set 'Steam Input' to ENABLED for your "
      + "controller, 2) use TEMPLATES → 'Gamepad' (verified working on "
      + "device). Avoid community layouts - this game's popular ones are "
      + "legacy configs that show BLANK in today's Steam. Quirk: the "
      + "layout sometimes drops after a game restart - re-apply it.",
  },
  1030300: {
    appId: 1030300,
    displayName: "Hollow Knight: Silksong",
    nexusDomain: "hollowknightsilksong", // verified: game id 8136
    installDirName: "Hollow Knight Silksong",
    // BepInEx convention: plugin dlls in per-mod folders
    modsSubdir: "BepInEx/plugins",
    moddedSaveWarning: false,
    processName: "Hollow Knight Silksong", // TODO verify comm under Proton
    framework: {
      name: "BepInEx",
      detectFile: "winhttp.dll",
      url: "docs.bepinex.dev",
      // verified: "BepInEx 5 with Configuration Manager" on Nexus
      nexusModId: 26,
      aliasModIds: [26, 986], // the newer re-upload counts too
      installKind: "copyRoot",
      // Proton needs the loader dll preferred over the builtin
      launchOptionsTemplate: 'WINEDLLOVERRIDES="winhttp=n,b" %command%',
    },
    // Steam installs the native Linux build by default, which BepInEx's
    // winhttp injection can't hook (verified on device) - mods need the
    // Windows build under Proton.
    protonRequired: {
      nativeMarker: "UnityPlayer.so",
      tool: "proton_experimental",
    },
  },
  1623730: {
    appId: 1623730,
    displayName: "Palworld",
    nexusDomain: "palworld", // verified: game id 6063
    installDirName: "Palworld",
    // UE5 pak drop-ins auto-load from ~mods; the folder doesn't ship with
    // the game (our installer creates it).
    modsSubdir: "Pal/Content/Paks/~mods", // TODO verify subfolder pak mounting
    moddedSaveWarning: false,
    processName: "Palworld-Win64-Shipping.exe",
    // Script mods (the biggest ones) need UE4SS. Palworld uses a fork;
    // mod 3405 is the Linux/Proton-fixes build ("UE4SS Palworld").
    // Archive verified: dwmapi.dll + ue4ss/ at root -> Pal/Binaries/Win64.
    framework: {
      name: "UE4SS",
      detectFile: "Pal/Binaries/Win64/dwmapi.dll",
      url: "docs.ue4ss.com",
      nexusModId: 3405,
      // Mods requirement-link any of the UE4SS uploads interchangeably.
      aliasModIds: [3405, 3035, 1121],
      installKind: "copyRoot",
      installSubdir: "Pal/Binaries/Win64",
      launchOptionsTemplate: 'WINEDLLOVERRIDES="dwmapi=n,b" %command%',
    },
    ue4ss: {
      modsSubdir: "Pal/Binaries/Win64/ue4ss/Mods",
      logicModsSubdir: "Pal/Content/Paks/LogicMods",
    },
  },
  261550: {
    appId: 261550,
    displayName: "Mount & Blade II: Bannerlord",
    nexusDomain: "mountandblade2bannerlord", // verified: game id 3174
    installDirName: "Mount & Blade II Bannerlord", // verified on device
    modsSubdir: "Modules",
    moddedSaveWarning: false,
    processName: "Bannerlord.exe",
    // BLSE is Bannerlord's script extender: current ButterLib/MCM declare
    // a dependency on its assembly resolver, and its LauncherEx replaces
    // the TaleWorlds launcher (and quiets its version-metadata alarms).
    // Archive verified: bin/Win64_Shipping_Client + Gaming.Desktop variant
    // at root -> copyRoot merges into the game root (no-flatten rule keys
    // off the detect file's "bin/" component).
    // BLSE, made to work under Proton on 2026-08-19 after a month of looking
    // impossible. The whole story is in main.py next to BLSE_LAUNCH_SCRIPT;
    // the short version:
    //
    //   - All three BLSE entry points die under wine-mono with a
    //     TypeLoadException for 0Harmony, because Mono resolves the field
    //     type eagerly, before BLSE's own assembly resolver exists.
    //   - MONO_PATH pointed at the Harmony MODULE's bin dir fixes it, and
    //     because the assembly then loads from the path the Harmony module
    //     expects, the "loaded from another location" warning never fires.
    //   - Delivered via a backend-written script at a no-space path, because
    //     decky-launch-options mangles quoted env assignments and the game
    //     path has spaces. The script swaps the exe AND sets MONO_PATH, and
    //     falls back to a vanilla boot if Harmony or BLSE is missing - a
    //     missing dependency must never produce an unbootable game.
    //
    // {blse_script} is substituted from game_status, which also (re)writes
    // the script, so the options never point at a file that does not exist.
    framework: {
      name: "BLSE",
      detectFile: "bin/Win64_Shipping_Client/Bannerlord.BLSE.Launcher.exe",
      url: "nexusmods.com/mountandblade2bannerlord/mods/1",
      nexusModId: 1, // verified: "Bannerlord Software Extender (BLSE)"
      installKind: "copyRoot",
      // Eight files, no manifest: the * globs that one folder for that stem,
      // so reset removes them all (Step 1 miscounted after reset without it).
      cleanupPrefixes: ["bin/Win64_Shipping_Client/Bannerlord.BLSE.*"],
      launchOptionsTemplate: "{blse_script} %command%",
    },
    extraFrameworks: [
      {
        // BLSE cannot run without Harmony - it is a declared requirement on
        // Nexus, and BLSE's own exception handler is built on it, so without
        // 0Harmony.dll it dies inside its error handler with no log at all.
        name: "Harmony",
        detectFile: "Modules/Bannerlord.Harmony/SubModule.xml",
        url: "nexusmods.com/mountandblade2bannerlord/mods/2006",
        nexusModId: 2006, // verified: "Harmony", v2.4.2.248 (2026-07-21)
        installKind: "copyRoot",
        cleanupPrefixes: ["Modules/Bannerlord.Harmony"],
      },
    ],
    // Modules activate via the launcher's XML (Vortex manages the same
    // file). Created by the launcher on first run - activation is
    // best-effort until then; the launcher also auto-detects modules.
    launcherXmlSubpath: "Mount and Blade II Bannerlord/Configs/LauncherData.xml",
    firstRunNotice: {
      message:
        "First time? Launch the game once before installing mods - its launcher creates the file that activates them.",
      goneWhenDocsFile:
        "Mount and Blade II Bannerlord/Configs/LauncherData.xml",
    },
    // The game's own modules live in Modules/ too - never list or touch.
    protectedModFolders: [
      // verified on device - the full official set for 1.2
      "Native",
      "SandBoxCore",
      "CustomBattle",
      "SandBox",
      "StoryMode",
      "Multiplayer",
      "BirthAndDeath",
      "FastMode",
    ],
    recommendedModIds: [612, 2018], // verified: Mod Configuration Menu (61k endo), ButterLib
  },
  292030: {
    appId: 292030,
    displayName: "The Witcher 3",
    nexusDomain: "witcher3", // verified: game id 952, ~8.8k mods
    installDirName: "The Witcher 3", // TODO verify on device
    modsSubdir: "mods",
    witcherLayout: true,
    moddedSaveWarning: false,
    processName: "witcher3.exe",
    // Next-gen loads everything in mods/ automatically - no framework,
    // no launch options. Script-conflicting mods are refused at install
    // (Script Merger is Windows-only); menu XMLs are registered in both
    // dx11/dx12 filelists per the next-gen requirement.
  },
  1245620: {
    appId: 1245620,
    displayName: "Elden Ring",
    nexusDomain: "eldenring", // verified: game id 4333, ~7.3k mods
    installDirName: "ELDEN RING",
    // me3 mods never touch the game folder - they live under the
    // plugin's runtime dir and are activated by a generated .me3
    // profile. This path is never created; the mod list comes from the
    // install records, like RE4's pak tier.
    modsSubdir: "._nexus_mods_unused",
    installMode: "me3",
    me3: {
      gameExe: "Game/eldenring.exe",
      headlineMod: "Seamless Co-op",
      // Elden Mod Loader: other mods are loaded BY it, and it has booted
      // on every build tested.
      loaderModIds: [117],
    },
    // me3's profile redirects the modded session to its own save file,
    // so vanilla characters are safe without our save-profile machinery.
    moddedSaveWarning: false,
    processName: "eldenring.exe",
    recommendedModIds: [510], // verified: Seamless Co-op (LukeYui)
  },
  1091500: {
    appId: 1091500,
    displayName: "Cyberpunk 2077",
    nexusDomain: "cyberpunk2077", // verified: game id 3333
    installDirName: "Cyberpunk 2077",
    // Verified on device: mods land in five places, and reset only ever
    // looked at archive/pc/mod.
    modWriteDirs: [
      "r6/scripts",
      "r6/tweaks",
      "red4ext/plugins",
      "bin/x64/plugins",
    ],
    modsSubdir: "archive/pc/mod",
    // Framework + archive tiers: payloads route by their game-root
    // prefix (bin/red4ext/r6/engine/archive); bare .archive files still
    // go flat into archive/pc/mod. All framework shapes verified by
    // downloading each archive (2026-08-04).
    cp77Layout: true,
    prefixRuntimeFix: true,
    moddedSaveWarning: false,
    processName: "Cyberpunk2077.exe",
    // The game's own compiler, and the only thing here that is evidence
    // rather than inference. Welcome to Night City omits seven mods their
    // pages call required, boots, and compiles clean - so the health check
    // asks this before calling any of them a fault.
    logAdapter: { kind: "redscript" },
    // REDlauncher stands between "Launch" here and the game starting.
    ownLauncher: true,
    // Measured on device, 2026-08-14, across six collections: at roughly
    // 270 tracked mods the grey screen after Play ran about two minutes,
    // while earlier, smaller collections barely paused. Cyberpunk pays a
    // per-archive startup cost (ArchiveXL and TweakXL both process at
    // load) that Bethesda games do not - New Vegas at 1,954 mods starts
    // faster than this at 270 - so the default 400 never fired here on a
    // setup that plainly needed it. 150 is an estimate from two points on
    // the curve, not a measurement of the knee.
    longWaitAtMods: 150,
    framework: {
      name: "Cyber Engine Tweaks",
      // Verified on device 2026-08-14. Exact paths, never prefixes:
      // "bin" as a prefix would delete the game.
      cleanupPrefixes: ["bin/x64/plugins", "bin/x64/version.dll"],
      // CET hooks via its own version.dll; RED4ext via winmm.dll -
      // Proton needs both preferred over Wine's builtins. redscript
      // needs no override (the game invokes its compiler natively).
      detectFile: "bin/x64/plugins/cyber_engine_tweaks.asi",
      url: "wiki.redmodding.org/cyber-engine-tweaks",
      nexusModId: 107, // verified live: 17.4M downloads
      installKind: "copyRoot",
      launchOptionsTemplate:
        'WINEDLLOVERRIDES="version=n,b;winmm=n,b" %command%',
    },
    extraFrameworks: [
      {
        name: "RED4ext",
        cleanupPrefixes: ["red4ext", "bin/x64/winmm.dll"],
        detectFile: "red4ext/RED4ext.dll",
        url: "docs.red4ext.com",
        nexusModId: 2380, // verified live
        installKind: "copyRoot",
      },
      {
        name: "ArchiveXL",
        // Inside RED4ext's tree, declared anyway so reset does not
        // depend on which loader is removed first.
        cleanupPrefixes: ["red4ext/plugins/ArchiveXL"],
        detectFile: "red4ext/plugins/ArchiveXL/ArchiveXL.dll",
        url: "github.com/psiberx/cp2077-archive-xl",
        nexusModId: 4198, // verified live
        installKind: "copyRoot",
      },
      {
        name: "TweakXL",
        cleanupPrefixes: ["red4ext/plugins/TweakXL"],
        detectFile: "red4ext/plugins/TweakXL/TweakXL.dll",
        url: "github.com/psiberx/cp2077-tweak-xl",
        nexusModId: 4197, // verified live
        installKind: "copyRoot",
      },
      {
        name: "redscript",
        // engine/tools only. r6/ and engine/ are vanilla and
        // r6/scripts holds other mods' scripts, so neither is claimed.
        cleanupPrefixes: ["engine/tools"],
        detectFile: "engine/tools/scc.exe",
        url: "github.com/jac3km4/redscript",
        nexusModId: 1511, // verified live
        installKind: "copyRoot",
      },
    ],
  },
  553850: {
    appId: 553850,
    displayName: "Helldivers 2",
    nexusDomain: "helldivers2", // verified: ~16k mods, HD2ModManager top
    installDirName: "Helldivers 2", // verified on device
    // Mods are numbered patch files overlaid on hash-named archives, flat
    // in data/. No folders, no loader, nothing executable.
    modsSubdir: "data",
    hd2Layout: true,
    // The two most-endorsed "mods" for this game are Windows mod managers
    // (HD2ModManager and Arsenal). They would headline the hero band on a
    // device that cannot run either. Michael: "it appears in the hero mods
    // and obviously wont work well on steamos in big picture mode."
    heroExcludeModIds: [109, 4664],
    // ReShade packages install beside the exe. Proton's builtin dxgi wins
    // unless overridden - same mechanism as the winhttp/dinput8 loaders.
    // The install carries an anti-cheat warning: injection is a different
    // category from the asset swaps GameGuard is known to tolerate.
    reshade: {
      subdir: "bin",
      launchOptionsTemplate: 'WINEDLLOVERRIDES="dxgi=n,b" %command%',
    },
    // The under-construction banner lived here for one build. Michael:
    // "Remove the under construction bit please as it takes up way too
    // much space and I am hopeful this will work as more updated mods get
    // added." The field stays available for future rough edges.
    moddedSaveWarning: false, // progression is server-side
    processName: "helldivers2.exe", // verified on device
    // The anti-cheat question, asked BEFORE this config existed. HD2 ships
    // GameGuard (bin/GameGuard on device) and is online-only. Per mod
    // authors in the beta channel: it does not act on cosmetic file swaps,
    // and the developers' stated concern is currency cheating. Everything
    // this tier installs is an asset swap - no code, no injection. The
    // FromSoft never-online rule cannot apply to a game with no offline
    // mode, so the honest posture is: cosmetic swaps only, said plainly.
    // ROADMAP until installed, booted and played on hardware.
  },
  1237950: {
    appId: 1237950,
    displayName: "STAR WARS Battlefront II",
    nexusDomain: "starwarsbattlefront22017", // verified: ~9.7k mods
    installDirName: "STAR WARS Battlefront II", // verified on device
    // Nothing is installed INTO the game's data folder: mods are compiled
    // into ModData and the game is pointed at that. modsSubdir is only here
    // because the shared status call wants a path that exists.
    modsSubdir: "Data",
    frostbite: true,
    installMode: "frosty",
    moddedSaveWarning: false, // progression is server-side
    processName: "starwarsbattlefrontii.exe", // verified on device
    // Not a Nexus mod: our own build of FrostyCli, because the upstream tool
    // could not do this at all until we implemented the bundle format and
    // fixed four data-corruption bugs in it.
    framework: {
      name: "Mod compiler",
      detectFile: "",
      url: "github.com/RedRanger14/decky-nexus",
      nexusModId: 0,
      installKind: "copyRoot",
    },
  },
};

/** Positional params several backend calls need for install-mode dispatch. */
export function modeParams(
  g: SupportedGame
): [InstallMode, number, string, "starred" | "listed"] {
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
