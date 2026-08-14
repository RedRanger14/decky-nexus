import {
  ButtonItem,
  ConfirmModal,
  DialogButton,
  Focusable,
  ModalRoot,
  PanelSection,
  PanelSectionRow,
  Field,
  Navigation,
  Router,
  TextField,
  ToggleField,
  showModal,
  staticClasses,
} from "@decky/ui";
import {
  addEventListener,
  removeEventListener,
  callable,
  definePlugin,
  routerHook,
  toaster,
} from "@decky/api";
import { Fragment, useEffect, useRef, useState } from "react";

/** The hunt outlives the panel: closing the QAM unmounts the component
 * but not the async loop driving it. Keeping the stop flag and the
 * "is one running" fact outside React means reopening the panel shows the
 * hunt again with a working Stop, instead of an inviting Start button and
 * no way to halt what is already going - which is exactly what happened
 * on the first overnight run. */
let huntStopFlag = false;
let huntActive = false;
import { FaEye, FaEyeSlash, FaPuzzlePiece } from "react-icons/fa";

import {
  AuthStatus,
  GameStatus,
  InstalledMod,
  ModLoadState,
  SaveStatus,
  UpdateInfo,
  InstallProgress,
  Me3State,
  CrashReport,
  ScriptExtenderPlugin,
  applyDisplayFix,
  checkDocsFile,
  checkGameFile,
  checkUpdates,
  copySavesToModded,
  getAuthStatus,
  refreshContentGate,
  getDebugInfo,
  getFrameworkSetup,
  getGameStatus,
  getInstalledMods,
  getModDetails,
  disableFailingMods,
  getBlamedFolders,
  getModLoadStatus,
  repairFailingMods,
  getSaveStatus,
  getSmapiLoadStatus,
  checkPluginMasters,
  disableBlockedPlugins,
  removeGhostPlugins,
  disablePlugins,
  fixPrefixRuntime,
  crashBisectApply,
  crashBisectFinish,
  crashBisectRecord,
  crashBisectStart,
  crashSince,
  enforceSkips,
  fixLoadOrder,
  getInstalledCount,
  getLoadOrderState,
  getPrefixRuntimeState,
  getScriptExtenderState,
  setScriptExtenderPlugins,
  getPrefixToolsState,
  getMe3State,
  getMe3LaunchCommand,
  getMe3CoopPassword,
  setMe3CoopPassword,
  installMe3,
  installFramework,
  skipPrefixTools,
  runPrefixTool,
  seedGameIni,
  markLaunchOptionsSet,
  resetGameModding,
  setFrameworkLaunchOptions,
  clearFrameworkLaunchOptions,
  setAllModsEnabled,
  setFrameworkEnabled,
  setApiKey,
  setModEnabled,
} from "./api";
import {
  crashHuntVerdict,
  crashSuspect,
  disableFailingOutcome,
  failingProblem,
  huntProgressNote,
  launchWaitNotice,
  loadOrderProblem,
  ghostPluginProblem,
  missingMasterProblem,
  blockedPluginsAction,
  lastRunSummary,
  maskCoopPassword,
  repairedNote,
  updatedNote,
  showInstalledModsSection,
  showResetRow,
  slotPressure,
  troubleshootingCount,

  frameworkStepNumbers,
  installedDepsNote,} from "./panelRules";
import {
  ALL_GAMES,
  DEFAULT_GAME,
  getLastActiveGame,
  getSupportedGame,
  modeParams,
  noteActiveGame,
  SupportedGame,
} from "./games";
import {
  getAppDisplayName,
  getAvailableCompatTools,
  getMainWindowPath,
  getRunningAppIds,
  getViewedLibraryAppId,
  isGameRunning,
  pickProton,
  restartGame,
  setCompatTool,
  setLaunchOptions,
} from "./steam";
import {
  getAggregateDownloadPercent,
  getCollectionRun,
  getCompletedDownloads,
  getDownloads,
  notifyGameStateChanged,
  subscribeGameState,
  setBrowseGame,
  setDetailOrigin,
  setSelectedMod,
  subscribeCollectionRun,
  subscribeDownloads,
  updateDownload,
} from "./state";
import {
  PRIMARY_BUTTON_CLASS,
  PRIMARY_BUTTON_CSS,
} from "./theme";

interface GameContext {
  /** The game being managed; undefined outside a supported game's context. */
  game?: SupportedGame;
  /** Display name of the unsupported context game, when running/viewing one. */
  unsupportedName?: string;
  /** Names of supported games running simultaneously - ambiguous context. */
  multipleNames?: string[];
  /** True on neutral ground (home screen etc.) - no game context at all. */
  neutral?: boolean;
}

/** Resolve which game the plugin is managing. The panel strictly follows
 * what the user is doing: a supported game's full sections appear only when
 * that game is running or its library page is on screen. Several supported
 * games running at once is an explicit (unsupported) state, not a guess. */
function resolveGameContext(): GameContext {
  const runningIds = getRunningAppIds();
  const runningSupported = runningIds
    .map((id) => getSupportedGame(id))
    .filter((g): g is SupportedGame => Boolean(g));

  if (runningSupported.length > 1) {
    return { multipleNames: runningSupported.map((g) => g.displayName) };
  }
  if (runningSupported.length === 1) {
    noteActiveGame(runningSupported[0].appId);
    return { game: runningSupported[0] };
  }

  const viewedId = getViewedLibraryAppId();
  const viewed = getSupportedGame(viewedId);
  if (viewed) {
    noteActiveGame(viewed.appId);
    return { game: viewed };
  }

  if (runningIds.length > 0) {
    return {
      unsupportedName:
        Router.MainRunningApp?.display_name ??
        getAppDisplayName(runningIds[0]) ??
        "This game",
    };
  }
  if (viewedId !== undefined) {
    return { unsupportedName: getAppDisplayName(viewedId) ?? "This game" };
  }
  // Route glitch guard: some overlay states report no /library/app path
  // even though the user is sitting on a game's page - the scope used to
  // vanish until the QAM was reopened. Stick with the last resolved game
  // until a genuinely different context (another game, a running app)
  // takes over.
  const last = getLastActiveGame();
  if (last) return { game: last };
  return { neutral: true };
}
import { pushOurPage, resetTabStack } from "./Tabs";
import { BrowsePage } from "./BrowsePage";
import { EndorsableFrameworkRow } from "./EndorseButton";
import { CollectionPage } from "./CollectionPage";
import { DownloadsPage } from "./DownloadsPage";
import { ModDetailPage } from "./ModDetailPage";
import { ManagerPage } from "./ManagerPage";
import { SettingsPage } from "./SettingsPage";
import { UpdatesPage } from "./UpdatesPage";
import { installLatest } from "./install";
import HealthCheckPage, { setHealthGame } from "./HealthCheckPage";
import { scanUpdates } from "./updates";

/** QAM row shortcut: jump from an installed mod straight to its detail page
 * (to re-check requirements, files, or updates). */
async function openInstalledModDetail(game: SupportedGame, mod: InstalledMod) {
  if (!mod.mod_id) return;
  const result = await getModDetails(game.nexusDomain, mod.mod_id);
  if (result.ok && result.mod) {
    setSelectedMod({ game, mod: result.mod });
    setDetailOrigin("qam");
    Router.CloseSideMenus();
    pushOurPage(DETAIL_ROUTE);
  } else {
    toaster.toast({
      title: "Could not open mod",
      body: result.error ?? mod.name ?? mod.folder,
    });
  }
}

const BROWSE_ROUTE = "/nexus-mods";
const DETAIL_ROUTE = "/nexus-mods/mod";
const COLLECTION_ROUTE = "/nexus-mods/collection";
const DOWNLOADS_ROUTE = "/nexus-mods/downloads";
const HEALTH_ROUTE = "/nexus-mods/health";
const UPDATES_ROUTE = "/nexus-mods/updates";
const MANAGER_ROUTE = "/nexus-mods/manager";
const SETTINGS_ROUTE = "/nexus-mods/settings";

interface BackendInfo {
  user: string;
  home: string;
  plugin_name: string;
  plugin_version: string;
  decky_version: string;
}

const ping = callable<[emit_event?: boolean], BackendInfo>("ping");

/** Brand-orange call-to-action for the QAM (hover/focus states included). */
function OrangeActionButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <>
      <style>{PRIMARY_BUTTON_CSS}</style>
      <DialogButton
        className={PRIMARY_BUTTON_CLASS}
        style={{ width: "100%" }}
        onClick={onClick}
      >
        {children}
      </DialogButton>
    </>
  );
}

function LaunchOptionsModal({
  frameworkName,
  gameName,
  appId,
  gameDomain,
  options,
  onDone,
  closeModal,
}: {
  frameworkName: string;
  gameName: string;
  appId: number;
  gameDomain: string;
  options: string;
  onDone?: () => void;
  closeModal?: () => void;
}) {
  return (
    <ModalRoot closeModal={closeModal}>
      <h3 style={{ marginTop: 0 }}>
        Launch {gameName} through {frameworkName}
      </h3>
      <div style={{ fontSize: "13px", opacity: 0.9, lineHeight: "1.5" }}>
        Mods only load when Steam starts the game via {frameworkName}. That
        needs these launch options on {gameName}:
      </div>
      <pre
        style={{
          fontSize: "12px",
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
          background: "rgba(0,0,0,0.35)",
          padding: "8px",
          borderRadius: "4px",
          margin: "10px 0",
        }}
      >
        {options}
      </pre>
      <ButtonItem
        layout="below"
        description="Replaces any existing launch options for this game"
        onClick={async () => {
          // On devices running decky-launch-options, Steam's field only
          // holds dlo's wrapper - the real command must go into dlo's
          // profile (the backend detects this); otherwise set Steam's
          // field directly via SteamClient.
          const result = await setFrameworkLaunchOptions(
            appId,
            gameDomain,
            options
          );
          const ok =
            result.ok ||
            (Boolean(result.use_steam_client) &&
              setLaunchOptions(appId, options));
          toaster.toast(
            ok
              ? { title: "Launch options set", body: `${gameName} will start through ${frameworkName}` }
              : { title: "Could not set launch options", body: result.error ?? "Use Copy instead and set them manually" }
          );
          if (ok) {
            onDone?.();
            closeModal?.();
          }
        }}
      >
        Set automatically
      </ButtonItem>
      <ButtonItem
        layout="below"
        description={`Then: ${gameName} page → gear icon → Properties → Launch Options → paste`}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(options);
            toaster.toast({
              title: "Copied to clipboard",
              body: "Paste it in the game's Properties → Launch Options",
            });
          } catch {
            toaster.toast({
              title: "Clipboard unavailable",
              body: options,
            });
          }
        }}
      >
        Copy to clipboard
      </ButtonItem>
      <ButtonItem
        layout="below"
        onClick={() => {
          onDone?.();
          closeModal?.();
        }}
      >
        I've set them manually — mark done
      </ButtonItem>
    </ModalRoot>
  );
}

/** "Modding went wrong - start over": uninstall every tracked mod, remove
 * the framework loader, clear the launch command and the plugin's state
 * for this game. Deliberately behind a destructive confirm modal. */
function ResetGameRow({
  game,
  onDone,
}: {
  game: SupportedGame;
  onDone?: () => void;
}) {
  const [busy, setBusy] = useState(false);

  const doReset = async () => {
    setBusy(true);
    try {
      const result = await resetGameModding(
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        game.installMode ?? "folder",
        game.appId,
        game.pluginsTxtSubpath ?? "",
        game.pluginsTxtStyle ?? "starred",
        // Every loader's leftovers, not just the primary one's: CP77
        // installs four frameworks, and each keeps its own copyRoot
        // files with no manifest to remove them by.
        [
          ...(game.framework?.cleanupPrefixes ?? []),
          ...(game.extraFrameworks ?? []).flatMap(
            (fw) => fw.cleanupPrefixes ?? []
          ),
        ],
        game.witcherLayout ?? false,
        [
          ...(game.framework?.frameworkModFolders ?? []),
          ...(game.extraFrameworks ?? []).flatMap(
            (fw) => fw.frameworkModFolders ?? []
          ),
        ],
        // Put back anything a modding tool rewrote, from the backup the
        // tool made. Without this a reset left the game exe patched and
        // Step 3 ticked, so the setup could not honestly be redone.
        (game.prefixTools ?? [])
          .map((t) => t.restoreOnReset)
          .filter((p): p is [string, string] => Boolean(p))
      );
      if (result.ok && result.use_steam_client) {
        setLaunchOptions(game.appId, "");
      }
      // Reset used to report success it had not checked: on device it
      // said "1543 mods removed, 0 errors" while 20GB of mod files stayed
      // in Data, and the only clue was the main menu looking wrong. If
      // files survive that no record covered, say so.
      const left = result.leftovers ?? 0;
      toaster.toast(
        result.ok
          ? left > 0
            ? {
                title: `${game.displayName} is not fully vanilla`,
                body:
                  `${result.removed ?? 0} mods removed, but ${left} file` +
                  `${left === 1 ? "" : "s"} could not be traced to a mod and ` +
                  "are still there. Verify the game files in Steam to be sure.",
                duration: 20000,
              }
            : {
                title: `${game.displayName} reset to vanilla`,
                body:
                  `${result.removed ?? 0} mods removed` +
                  ((result.swept ?? 0) > 0
                    ? `, ${result.swept} leftover file${
                        result.swept === 1 ? "" : "s"
                      } swept`
                    : "") +
                  ((result.errors?.length ?? 0) > 0
                    ? ` · ${result.errors!.length} items need a look`
                    : result.verified
                    ? " · verified clean"
                    : " · ready for a clean start"),
              }
          : { title: "Reset failed", body: result.error ?? "" }
      );
    } catch (e) {
      toaster.toast({ title: "Reset failed", body: String(e) });
    } finally {
      setBusy(false);
      onDone?.();
      // Every other section is a different component: without this they
      // keep showing the loader as installed and the mods as present, and
      // the reset looks like it did nothing.
      notifyGameStateChanged();
    }
  };

  return (
    <PanelSectionRow>
      <ButtonItem
        layout="below"
        disabled={busy}
        description="Removes every mod this plugin installed, the mod loader, and the launch command"
        onClick={() =>
          showModal(
            <ConfirmModal
              strTitle={`Reset ${game.displayName} to vanilla?`}
              strDescription={
                `Every mod installed by this plugin is uninstalled, ` +
                `${game.framework?.name ?? "the mod loader"} is removed, ` +
                `and the launch command is cleared. Saves are not touched. ` +
                `Files added outside this plugin stay - use Steam's ` +
                `"Verify integrity" afterwards if the game still misbehaves.`
              }
              strOKButtonText="Reset to vanilla"
              bDestructiveWarning={true}
              onOK={doReset}
            />
          )
        }
      >
        {busy ? "Resetting…" : "⟲ Reset game modding"}
      </ButtonItem>
    </PanelSectionRow>
  );
}

function CurrentGameSection() {
  const { game, unsupportedName, multipleNames } = resolveGameContext();
  const gameIsRunning = game ? isGameRunning(game.appId) : false;

  const [status, setStatus] = useState<GameStatus | undefined>();
  const [frameworkBusy, setFrameworkBusy] = useState(false);
  const [launchOptionsSet, setLaunchOptionsSet] = useState(false);
  const [nativeBuild, setNativeBuild] = useState(false);
  const [firstRunNeeded, setFirstRunNeeded] = useState(false);
  const [extraFwInstalled, setExtraFwInstalled] = useState<
    Record<string, boolean>
  >({});
  // Prefix tools (FO3's exe patchers): applied-state + run progress.
  const [toolsDone, setToolsDone] = useState<Record<number, boolean>>({});
  const [toolsLast, setToolsLast] = useState<
    Record<number, { stage: string; message: string }>
  >({});
  const [toolsSkipped, setToolsSkipped] = useState<Record<number, boolean>>({});
  const [toolsInfoOpen, setToolsInfoOpen] = useState(false);
  const [toolsBusy, setToolsBusy] = useState<string | undefined>();
  // me3 (FromSoft games): loader state + Seamless Co-op's session password.
  const [me3, setMe3] = useState<Me3State | undefined>();
  const [me3Busy, setMe3Busy] = useState(false);
  const [coopPassword, setCoopPassword] = useState("");
  const [coopSaved, setCoopSaved] = useState("");
  const [coopShown, setCoopShown] = useState(false);
  // Steam batches its config writes, so the backend can't confirm a
  // just-set compat tool for a second or two - remember what we set.
  const [protonChosen, setProtonChosen] = useState<string | undefined>();
  // Only used to size the launch notice, so undefined just means silent.
  const [modCount, setModCount] = useState<number | undefined>();

  // Same visual language as the download rows: the button FILLS orange.
  // No percentage exists for an exe patcher, so the fill tracks elapsed
  // time against the tool's own budget - honest, and it always moves.
  const [toolPct, setToolPct] = useState(0);
  const toolClock = useRef<{ start: number; budgetMs: number } | undefined>(
    undefined
  );
  useEffect(() => {
    if (!toolsBusy) {
      setToolPct(0);
      return;
    }
    const timer = setInterval(() => {
      const c = toolClock.current;
      if (!c) return;
      const frac = (Date.now() - c.start) / c.budgetMs;
      setToolPct(Math.min(97, Math.round(frac * 100)));
    }, 400);
    return () => clearInterval(timer);
  }, [toolsBusy]);

  const refreshStatus = () => {
    if (game) {
      getGameStatus(
        game.installDirName,
        game.modsSubdir,
        game.framework?.detectFile ?? ""
      ).then(setStatus);
      // Multi-framework games (CP77): each extra gets its own row +
      // installed check via its detect file.
      for (const fw of game.extraFrameworks ?? []) {
        checkGameFile(game.installDirName, fw.detectFile).then((r) =>
          setExtraFwInstalled((prev) => ({
            ...prev,
            [fw.name]: Boolean(r.ok && r.exists),
          }))
        );
      }
      if (game.protonRequired) {
        checkGameFile(
          game.installDirName,
          game.protonRequired.nativeMarker
        ).then((r) => setNativeBuild(Boolean(r.ok && r.exists)));
      }
      if (game.firstRunNotice) {
        checkDocsFile(game.appId, game.firstRunNotice.goneWhenDocsFile).then(
          (r) => setFirstRunNeeded(Boolean(r.ok && !r.exists))
        );
      }
      if (game.framework || game.launcherBypass) {
        // "Applied ✓" has to mean "applied, and still what we would apply".
        // Fallout 3's launch command grew a FOSE branch and the step showed
        // a tick with no button, so there was no way to take the new one.
        const expected =
          game.launcherBypass?.launchOptionsTemplate ??
          game.framework?.launchOptionsTemplate ??
          "";
        getFrameworkSetup(game.nexusDomain, expected).then((r) =>
          setLaunchOptionsSet(
            Boolean(r.launch_options_set) &&
              r.launch_options_current !== false
          )
        );
      }
      if (game.prefixTools) {
        getPrefixToolsState(game.nexusDomain).then((r) => {
          setToolsDone(r.done ?? {});
          setToolsLast((r.last ?? {}) as Record<
            number,
            { stage: string; message: string }
          >);
          setToolsSkipped(r.skipped ?? {});
        });
      }
      getInstalledCount(game.nexusDomain).then((r) =>
        setModCount(r.ok ? r.mods : undefined)
      );
      if (game.me3) {
        getMe3State(game.nexusDomain, game.installDirName, game.appId).then(
          setMe3
        );
        getFrameworkSetup(game.nexusDomain, "").then((r) =>
          setLaunchOptionsSet(Boolean(r.launch_options_set))
        );
        getMe3CoopPassword(game.nexusDomain).then((r) => {
          setCoopPassword(r.password ?? "");
          setCoopSaved(r.password ?? "");
        });
      }
    }
  };

  const markDone = () => {
    if (game) {
      // Record the value, not just the fact. Without it "applied ✓" cannot
      // be checked against a template that changes later.
      const applied =
        game.launcherBypass?.launchOptionsTemplate ??
        game.framework?.launchOptionsTemplate ??
        "";
      markLaunchOptionsSet(game.nexusDomain, applied).then(() =>
        setLaunchOptionsSet(true)
      );
    }
  };

  /** Launching is the end of what the panel is for - leaving it open just
   * puts something over the game that has to be dismissed. */
  const launchGame = () => {
    if (!game) return;
    restartGame(game.appId);
    // Said on the way out, because the panel is about to close and the
    // black screen that follows looks exactly like a hang.
    const wait = launchWaitNotice(modCount ?? 0);
    if (wait) {
      toaster.toast({
        title: `Starting ${game.displayName}`,
        body: wait,
        duration: 15000,
      });
    }
    Navigation.CloseSideMenus();
  };

  const openLaunchOptionsModal = () => {
    if (!game?.framework?.launchOptionsTemplate || !status) return;
    showModal(
      <LaunchOptionsModal
        frameworkName={game.framework.name}
        gameName={game.displayName}
        appId={game.appId}
        gameDomain={game.nexusDomain}
        options={game.framework.launchOptionsTemplate.replace(
          "{install_path}",
          status.install_path
        )}
        onDone={markDone}
      />
    );
  };

  const onClearLaunchOptions = async () => {
    if (!game) return;
    const result = await clearFrameworkLaunchOptions(
      game.appId,
      game.nexusDomain
    );
    // Non-dlo devices: the backend can't touch Steam's field safely while
    // Steam runs - clear it from here via SteamClient instead.
    const ok =
      result.ok &&
      (!result.use_steam_client || setLaunchOptions(game.appId, ""));
    toaster.toast(
      ok
        ? {
            title: "Launch command removed",
            body: `${game.displayName} will start without ${
              game.framework?.name ?? "the mod loader"
            }`,
          }
        : {
            title: "Could not clear launch command",
            body:
              result.error ??
              `${game.displayName} page → Properties → Launch Options → clear`,
          }
    );
    if (ok) {
      setLaunchOptionsSet(false);
      refreshStatus();
    }
  };

  useEffect(() => {
    setStatus(undefined);
    // Stale me3 state from the previous game would drive this game's
    // steps (and its co-op field) until the fetch lands.
    setMe3(undefined);
    setCoopPassword("");
    setCoopSaved("");
    setCoopShown(false);
    setProtonChosen(undefined);
    refreshStatus();
  }, [game?.appId]);

  // A reset (or any bulk action) invalidates every step on this panel.
  useEffect(() => subscribeGameState(refreshStatus), [game?.appId]);

  // Installing a mod happens on another page: the loader panel has to
  // notice, or Seamless Co-op's password field never appears.
  useEffect(() => {
    if (!game?.me3) return;
    // Finished installs land in the completed list, so its length is the
    // signal that something new is on disk.
    let last = getCompletedDownloads().length;
    return subscribeDownloads(() => {
      const now = getCompletedDownloads().length;
      if (now !== last) {
        last = now;
        refreshStatus();
      }
    });
  }, [game?.appId]);

  const onInstallFramework = async () => {
    if (!game?.framework?.nexusModId) return;
    setFrameworkBusy(true);
    try {
      // Same reason as the multi-framework path: a loader installs fine
      // against an old prefix CRT and then its plugins refuse to load.
      if (game.prefixRuntimeFix) {
        await fixPrefixRuntime(game.appId).catch(() => undefined);
      }
      // BaseLib is an ordinary mod that lives in mods/BaseLib/. The
      // framework installer flattens archives into the game root, which
      // would scatter it across mods/ - so route it through the installer
      // that already handles folder mods correctly.
      const result = game.framework.installAsMod
        ? await installLatest(
            game,
            game.framework.nexusModId,
            game.framework.name
          )
        : await installFramework(
            game.nexusDomain,
            game.framework.nexusModId,
            game.installDirName,
            game.framework.installKind ?? "smapi",
            game.framework.detectFile,
            game.framework.avoidFileKeywords ?? [],
            game.framework.installSubdir ?? ""
          );
      // Some games need ini blocks before mods load at all (e.g. FO4's
      // archive invalidation) - apply them as part of framework setup.
      if (result.ok && game.setupInis) {
        for (const ini of game.setupInis) {
          await applyDisplayFix(
            game.appId,
            ini.prefsSubpath,
            ini.section,
            ini.settings,
            true
          );
        }
      }
      // installAsMod frameworks report no install_path and need no launch
      // command: Slay the Spire 2 loads whatever is in mods/ by itself, so
      // Step 1 is the whole setup.
      const installPath =
        "install_path" in result ? result.install_path : undefined;
      if (result.ok) {
        toaster.toast({
          title: `${game.framework.name} installed`,
          body: game.framework.launchOptionsTemplate
            ? "Step 2: set the launch command"
            : "That's the setup done — mods will load next time you play",
        });
        if (game.framework.launchOptionsTemplate && installPath) {
          showModal(
            <LaunchOptionsModal
              frameworkName={game.framework.name}
              gameName={game.displayName}
              appId={game.appId}
              gameDomain={game.nexusDomain}
              options={game.framework.launchOptionsTemplate.replace(
                "{install_path}",
                installPath
              )}
              onDone={markDone}
            />
          );
        }
      } else {
        toaster.toast({
          title: `${game.framework?.name} install failed`,
          body: result.error ?? "Unknown error",
        });
      }
    } finally {
      setFrameworkBusy(false);
      refreshStatus();
    }
  };

  // Multi-framework games (CP77): Step 1 is ONE button that installs the
  // whole stack. Behind the scenes each framework downloads individually
  // from Nexus Mods so every author still gets the download credit.
  const allFrameworks = game?.framework
    ? [game.framework, ...(game.extraFrameworks ?? [])]
    : [];
  const isMultiFw = (game?.extraFrameworks?.length ?? 0) > 0;
  // Tools the plugin can actually run, and tools that need a person.
  // Fallout 3 has one of each: the Anniversary Patcher works headless, the
  // ESM Patcher is a GUI installer asking for two paths. Treating them as
  // one group meant either blocking the working one or forever reporting
  // "(1)" for the one that can never finish.
  const autoTools = (game?.prefixTools ?? []).filter(
    (t) => !t.needsDesktopMode
  );
  const manualTools = (game?.prefixTools ?? []).filter(
    (t) => t.needsDesktopMode
  );
  // Step numbers follow what renders. BaseLib needs no launch command, so
  // hardcoded labels produced "Step 1" followed by "Step 3".
  const fwSteps = frameworkStepNumbers(
    Boolean(game?.framework?.launchOptionsTemplate)
  );
  const coopMasked = maskCoopPassword(coopSaved, coopShown);
  const missingFrameworks = allFrameworks.filter((fw, i) =>
    i === 0 ? !status?.framework_installed : !extraFwInstalled[fw.name]
  );
  const [fwProgress, setFwProgress] = useState<string | undefined>();

  const onInstallAllFrameworks = async () => {
    if (!game?.framework) return;
    const queue = missingFrameworks;
    setFrameworkBusy(true);
    let failed = 0;
    let mainInstallPath: string | undefined;
    try {
      // The runtime fix goes first: without a 14.40+ CRT in the prefix,
      // CET and RED4ext install fine but fail to LOAD (error 998).
      if (game.prefixRuntimeFix) {
        setFwProgress("Updating VC++ runtime…");
        const rt = await fixPrefixRuntime(game.appId);
        if (rt.ok && rt.updated) {
          toaster.toast({
            title: "VC++ runtime updated",
            body: `${rt.previous ?? "old"} → ${rt.version} (needed by CET and RED4ext)`,
          });
        } else if (!rt.ok) {
          toaster.toast({
            title: "VC++ runtime check failed",
            body: rt.error ?? "Frameworks may not load in-game",
          });
        }
      }
      for (let i = 0; i < queue.length; i++) {
        const fw = queue[i];
        const isMain = fw.name === game.framework.name;
        setFwProgress(`Installing ${fw.name} (${i + 1}/${queue.length})…`);
        const result = await installFramework(
          game.nexusDomain,
          fw.nexusModId!,
          game.installDirName,
          fw.installKind ?? (isMain ? "smapi" : "copyRoot"),
          fw.detectFile,
          fw.avoidFileKeywords ?? [],
          fw.installSubdir ?? ""
        );
        if (!result.ok) {
          failed++;
          toaster.toast({
            title: `${fw.name} install failed`,
            body: result.error ?? "Unknown error",
          });
        } else if (isMain && result.install_path) {
          mainInstallPath = result.install_path;
        }
      }
      if (mainInstallPath && game.setupInis) {
        for (const ini of game.setupInis) {
          await applyDisplayFix(
            game.appId,
            ini.prefsSubpath,
            ini.section,
            ini.settings,
            true
          );
        }
      }
      if (failed === 0) {
        toaster.toast({
          title: `Frameworks installed (${queue.length})`,
          body: "Step 2: set the launch command",
        });
      }
      if (
        mainInstallPath &&
        game.framework.launchOptionsTemplate &&
        !launchOptionsSet
      ) {
        showModal(
          <LaunchOptionsModal
            frameworkName={game.framework.name}
            gameName={game.displayName}
            appId={game.appId}
            gameDomain={game.nexusDomain}
            options={game.framework.launchOptionsTemplate.replace(
              "{install_path}",
              mainInstallPath
            )}
            onDone={markDone}
          />
        );
      }
    } finally {
      setFrameworkBusy(false);
      setFwProgress(undefined);
      refreshStatus();
    }
  };

  if (!game) {
    if (multipleNames) {
      // Several supported games running at once: say so instead of guessing.
      return (
        <PanelSection title="Current Game">
          <PanelSectionRow>
            <Field label="Running">{multipleNames.join(" · ")}</Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <div
              style={{
                padding: "8px 10px",
                margin: "4px 0",
                background: "rgba(255, 200, 60, 0.12)",
                borderLeft: "3px solid #ffc83c",
                borderRadius: "4px",
                fontSize: "12px",
                lineHeight: "1.45",
              }}
            >
              ⚠ Multiple supported games are running. Mod management works
              with one game at a time — close one to continue. Installed mods
              are still listed below.
            </div>
          </PanelSectionRow>
        </PanelSection>
      );
    }
    if (unsupportedName) {
      // Running or viewing a game the plugin doesn't support: just say so.
      return (
        <PanelSection title="Current Game">
          <PanelSectionRow>
            <Field label="Game">{unsupportedName}</Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Support">
              Not supported yet — currently:{" "}
              {ALL_GAMES.map((g) => g.displayName).join(", ")}
            </Field>
          </PanelSectionRow>
        </PanelSection>
      );
    }
    // Neutral ground (home screen etc.): just the browser entry point.
    return (
      <PanelSection title="Nexus Mods">
        <PanelSectionRow>
          <OrangeActionButton
            onClick={() => {
              setBrowseGame(undefined);
              resetTabStack();
              pushOurPage(BROWSE_ROUTE);
              Navigation.CloseSideMenus();
            }}
          >
            Open Mod Browser
          </OrangeActionButton>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  return (
    <PanelSection title="Current Game">
      <PanelSectionRow>
        <Field label="Game">
          {gameIsRunning ? game.displayName : `${game.displayName} · not running`}
        </Field>
      </PanelSectionRow>
      {status && !status.installed && (
        <PanelSectionRow>
          <Field label="Installed">Not found in main Steam library</Field>
        </PanelSectionRow>
      )}
      {/* Framework games render a uniform numbered checklist: every step has
          a "Step N" heading; the content is a button while actionable and a
          plain ✓ line once done (one-time buttons disappear after use). */}
      {/* launcherBypass games carry the first-run message as a checklist
          Step instead of this banner. */}
      {firstRunNeeded &&
        status?.installed &&
        game.firstRunNotice &&
        !game.launcherBypass && (
          <PanelSectionRow>
            <Field label="ℹ Before you mod">
              {game.firstRunNotice.message}
            </Field>
          </PanelSectionRow>
        )}
      {game.protonRequired && status?.installed && nativeBuild && (
        <PanelSectionRow>
          <ButtonItem
            label="⚠ Wrong game version for mods"
            layout="below"
            description={`Steam installed the native Linux version, which mod loaders can't hook. This switches ${game.displayName} to the Windows version via Proton - Steam will download it (your save syncs via Steam Cloud).`}
            onClick={() => {
              const ok = setCompatTool(
                game.appId,
                game.protonRequired!.tool
              );
              toaster.toast(
                ok
                  ? {
                      title: "Switched to Proton",
                      body: "Steam will update the game - launch it once the download finishes",
                    }
                  : {
                      title: "Could not switch automatically",
                      body: "Game page → Properties → Compatibility → force Proton Experimental",
                    }
              );
              setTimeout(refreshStatus, 2000);
            }}
          >
            Switch to Proton (required)
          </ButtonItem>
        </PanelSectionRow>
      )}
      {game.framework && status?.installed ? (
        <>
          {/* Steam is pointed at the framework's loader but the loader is
              gone (uninstalled/removed): the game silently won't start.
              Say so instead of letting the user discover it. */}
          {launchOptionsSet && !status.framework_installed && (
            <>
              <PanelSectionRow>
                <Field label="⚠ Game won't start">
                  {game.displayName} is set to launch through{" "}
                  {game.framework.name}, which isn't installed. Install{" "}
                  {game.framework.name} below, or:
                </Field>
              </PanelSectionRow>
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  description="Removes the launch command so the game starts without mods"
                  onClick={onClearLaunchOptions}
                >
                  Clear launch command
                </ButtonItem>
              </PanelSectionRow>
            </>
          )}

          {/* Numbered from what actually renders: BaseLib needs no launch
              command, so the panel used to read "Step 1" then "Step 3". */}
          {isMultiFw ? (
            /* Multi-framework games (CP77): one button installs the whole
               stack; each framework still downloads individually from
               Nexus Mods so every author gets the download credit. */
            missingFrameworks.length === 0 ? (
              /* One PanelSectionRow PER framework, not one holding all of
                 them. Steam treats a row as a single focus target, so five
                 endorse buttons in one row could not be reached
                 individually with a controller - the D-pad highlighted the
                 whole block and A always endorsed the first author.
                 Cyberpunk has five, so four of them were unreachable. */
              <>
                <PanelSectionRow>
                  <Field label={`Step ${fwSteps.install}`}>
                    All {allFrameworks.length} installed ✓
                  </Field>
                </PanelSectionRow>
                {allFrameworks.map((f, i) => (
                  <PanelSectionRow key={f.nexusModId ?? f.name}>
                    {/* Breathing room, including above the first: five
                        focus rings stacked flush against each other read as
                        one block, which is the thing that made these look
                        unselectable in the first place. */}
                    <div style={{ margin: i === 0 ? "10px 0 5px" : "5px 0" }}>
                    <EndorsableFrameworkRow
                      text={`${f.name} ✓`}
                      gameDomain={game.nexusDomain}
                      modId={f.nexusModId}
                      modName={f.name}
                      /* The cooldown note is one fact about Nexus, not one
                         per author - five copies of it was just noise. */
                      showHint={i === allFrameworks.length - 1}
                    />
                    </div>
                  </PanelSectionRow>
                ))}
              </>
            ) : (
              <PanelSectionRow>
                <ButtonItem
                  label={`Step ${fwSteps.install}`}
                  layout="below"
                  disabled={frameworkBusy}
                  description={`Installs everything ${game.displayName} mods need: ${missingFrameworks
                    .map((f) => f.name)
                    .join(", ")}. Each is downloaded from Nexus Mods so its author gets the download credit.${
                    game.prefixRuntimeFix
                      ? " Also updates the game's VC++ runtime (required on SteamOS)."
                      : ""
                  }`}
                  onClick={onInstallAllFrameworks}
                >
                  {frameworkBusy
                    ? fwProgress ?? "Installing…"
                    : missingFrameworks.length === allFrameworks.length
                      ? `Install all frameworks (${allFrameworks.length})`
                      : `Install remaining frameworks (${missingFrameworks.length})`}
                </ButtonItem>
              </PanelSectionRow>
            )
          ) : (
            <PanelSectionRow>
              {status.framework_installed ? (
                <Field label={`Step ${fwSteps.install}`} childrenLayout="below">
                  <EndorsableFrameworkRow
                    text={`${game.framework.name} installed ✓`}
                    gameDomain={game.nexusDomain}
                    modId={game.framework.nexusModId}
                    modName={game.framework.name}
                  />
                </Field>
              ) : (
                <ButtonItem
                  label={`Step ${fwSteps.install}`}
                  layout="below"
                  disabled={frameworkBusy || !game.framework.nexusModId}
                  description={`Most ${game.displayName} mods require ${game.framework.name}. Downloads from Nexus Mods (author gets the credit).`}
                  onClick={onInstallFramework}
                >
                  {frameworkBusy
                    ? `Installing ${game.framework.name}…`
                    : `Install ${game.framework.name}`}
                </ButtonItem>
              )}
            </PanelSectionRow>
          )}
          {game.framework.launchOptionsTemplate && (
            <PanelSectionRow>
              {launchOptionsSet ? (
                <Field label={`Step ${fwSteps.launch}`}>
                  Launch command set ✓
                </Field>
              ) : (
                <ButtonItem
                  label={`Step ${fwSteps.launch}`}
                  layout="below"
                  disabled={!status.framework_installed}
                  description={`Needed for ${game.framework.name} to load mods`}
                  onClick={openLaunchOptionsModal}
                >
                  Set launch command
                </ButtonItem>
              )}
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <Field label={`Step ${fwSteps.browse}`} childrenLayout="below">
              <OrangeActionButton
                onClick={() => {
                  setBrowseGame(game);
                  resetTabStack();
              pushOurPage(BROWSE_ROUTE);
                  Navigation.CloseSideMenus();
                }}
              >
                Open Mod Browser
              </OrangeActionButton>
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              label={`Step ${fwSteps.play}`}
              layout="below"
              description="Restarts are required for mods to take effect"
              onClick={launchGame}
            >
              {gameIsRunning
                ? `Restart ${game.displayName}`
                : `Launch ${game.displayName}`}
            </ButtonItem>
          </PanelSectionRow>
        </>
      ) : (
        <>
          {/* Frameworkless games with a broken stock launcher (FO3): the
              fix boots the game exe directly, seeds the Documents ini the
              launcher never created, and applies the setup ini blocks
              (no framework step exists to carry them). */}
          {game.launcherBypass && status?.installed && (
            <PanelSectionRow>
              {launchOptionsSet ? (
                <Field label="Step 1">Launch fix applied ✓</Field>
              ) : (
                <ButtonItem
                  label="Step 1"
                  layout="below"
                  description={`${game.displayName}'s own launcher freezes on this device - this starts the game directly instead (and applies the config fixes mods need).`}
                  onClick={async () => {
                    if (game.launcherBypass!.seedIni) {
                      await seedGameIni(
                        game.installDirName,
                        game.appId,
                        game.launcherBypass!.seedIni.sourceRel,
                        game.launcherBypass!.seedIni.prefsSubpath
                      );
                    }
                    for (const ini of game.setupInis ?? []) {
                      await applyDisplayFix(
                        game.appId,
                        ini.prefsSubpath,
                        ini.section,
                        ini.settings,
                        true
                      );
                    }
                    showModal(
                      <LaunchOptionsModal
                        frameworkName="Direct launch"
                        gameName={game.displayName}
                        appId={game.appId}
                        gameDomain={game.nexusDomain}
                        options={game.launcherBypass!.launchOptionsTemplate}
                        onDone={markDone}
                      />
                    );
                  }}
                >
                  Fix game launch
                </ButtonItem>
              )}
            </PanelSectionRow>
          )}
          {/* FromSoft games: me3 is a Linux binary the plugin keeps its
              own copy of. It boots the game's real exe instead of the
              anti-cheat launcher, so the game folder stays vanilla and
              the modded session never reaches FromSoft's servers. */}
          {game.me3 && status?.installed && (
            <>
              {/* Step 1 is Proton because the mod loader asks Steam which
                  one to use, and Steam only answers if it's been told.
                  A Verified game runs on an implicit default that's
                  written down nowhere, so the loader falls back to a
                  build that may not be installed and the game just
                  doesn't start. One tap writes it down. */}
              <PanelSectionRow>
                {protonChosen || me3?.compat_tool ? (
                  <Field label={`Step ${fwSteps.install}`}>
                    Compatibility set ✓ ({protonChosen ?? me3?.compat_tool})
                  </Field>
                ) : (
                  <ButtonItem
                    label={`Step ${fwSteps.install}`}
                    layout="below"
                    disabled={me3?.protons?.length === 0}
                    description={
                      me3?.protons?.length === 0
                        ? "No Proton version is installed on this device - install one from Steam's Library → Tools first."
                        : `Tells Steam which Proton to run ${game.displayName} with. The mod loader needs a named version; unmodded play is unaffected.`
                    }
                    onClick={async () => {
                      const tools = await getAvailableCompatTools(game.appId);
                      const pick = pickProton(tools);
                      if (!pick || !setCompatTool(game.appId, pick.name)) {
                        toaster.toast({
                          title: "Could not set compatibility",
                          body: `${game.displayName} → Properties → Compatibility → tick "Force the use of a specific Steam Play tool"`,
                        });
                        return;
                      }
                      setProtonChosen(pick.displayName || pick.name);
                      toaster.toast({
                        title: `Compatibility set to ${pick.displayName}`,
                        body: `${game.displayName} is ready for the mod loader`,
                      });
                      // Steam writes config.vdf on its own schedule.
                      setTimeout(refreshStatus, 2000);
                    }}
                  >
                    Set compatibility (Proton)
                  </ButtonItem>
                )}
              </PanelSectionRow>
              <PanelSectionRow>
                {me3?.installed && !me3.error ? (
                  <Field label="Step 2">
                    Mod loader installed ✓{me3.version ? ` (me3 ${me3.version})` : ""}
                  </Field>
                ) : (
                  <ButtonItem
                    label="Step 2"
                    layout="below"
                    disabled={me3Busy}
                    description={
                      me3?.error
                        ? // Unpacked but won't execute: a green tick here
                          // would resurface as "the game won't start".
                          `The mod loader is installed but won't run (${me3.error}). Reinstall it.`
                        : `${game.displayName} mods load through me3, a mod loader that starts ${game.me3.gameExe} directly${game.me3.headlineMod ? ` - it's what ${game.me3.headlineMod} needs` : ""}. Your game files aren't modified.`
                    }
                    onClick={async () => {
                      setMe3Busy(true);
                      try {
                        const r = await installMe3();
                        toaster.toast(
                          r.ok
                            ? {
                                title: "Mod loader installed",
                                body: `me3 ${r.version ?? ""}`.trim(),
                              }
                            : {
                                title: "Could not install the mod loader",
                                body: r.error ?? "Check your connection",
                              }
                        );
                        refreshStatus();
                      } finally {
                        setMe3Busy(false);
                      }
                    }}
                  >
                    {me3Busy
                      ? "Installing…"
                      : me3?.error
                        ? "Reinstall mod loader (me3)"
                        : "Install mod loader (me3)"}
                  </ButtonItem>
                )}
              </PanelSectionRow>
              <PanelSectionRow>
                {launchOptionsSet ? (
                  <Field label="Step 3">Launch command set ✓</Field>
                ) : (
                  <ButtonItem
                    label="Step 3"
                    layout="below"
                    disabled={!me3?.installed || Boolean(me3?.error)}
                    description="Steam needs to start the game through me3. Mods stay inactive until this is set."
                    onClick={async () => {
                      const r = await getMe3LaunchCommand(game.nexusDomain);
                      if (!r.ok || !r.command) {
                        toaster.toast({
                          title: "Could not build the launch command",
                          body: r.error ?? "Try reinstalling the mod loader",
                        });
                        return;
                      }
                      showModal(
                        <LaunchOptionsModal
                          frameworkName="me3"
                          gameName={game.displayName}
                          appId={game.appId}
                          gameDomain={game.nexusDomain}
                          options={r.command}
                          onDone={markDone}
                        />
                      );
                    }}
                  >
                    Set launch command
                  </ButtonItem>
                )}
              </PanelSectionRow>
              {/* me3 runs the game through whichever Proton Steam has
                  mapped for it, falling back to the game's verified-Deck
                  runtime (Proton 8.0 for Elden Ring) - which only exists
                  if that build is actually installed. */}
              {me3?.installed && me3.protons?.length === 0 && (
                <PanelSectionRow>
                  <Field label="⚠ Proton">
                    No Proton build is installed. Set one in{" "}
                    {game.displayName} → Properties → Compatibility, or the
                    mod loader has nothing to run the game with.
                  </Field>
                </PanelSectionRow>
              )}
            </>
          )}
          {/* Step 2: prove the launch fix by booting to the main menu
              once BEFORE mods go in - a clean baseline beats debugging
              boot and mods at the same time. */}
          {game.launcherBypass && status?.installed && game.firstRunNotice && (
            <PanelSectionRow>
              {firstRunNeeded ? (
                <ButtonItem
                  label="Step 2"
                  layout="below"
                  disabled={!launchOptionsSet}
                  description={game.firstRunNotice.message}
                  onClick={() => {
                    launchGame();
                    // The marker file appears once the game reaches the
                    // menu - re-check when the user comes back.
                    setTimeout(refreshStatus, 15000);
                  }}
                >
                  {gameIsRunning
                    ? `Restart ${game.displayName} (vanilla)`
                    : `Launch ${game.displayName} once (vanilla)`}
                </ButtonItem>
              ) : (
                <Field label="Step 2">First vanilla boot done ✓</Field>
              )}
            </PanelSectionRow>
          )}
          {/* Step 3: prefix tools - exe patchers the scene depends on,
              run inside the game's Proton prefix, one tap for all. */}
          {game.prefixTools && status?.installed && (
            <PanelSectionRow>
              <style>{`
                .nexus-tool-fill button,
                .nexus-tool-fill .DialogButton {
                  background: linear-gradient(90deg, rgba(218,142,53,0.55) var(--tool-pct), rgba(255,255,255,0.08) var(--tool-pct)) !important;
                  color: #fff !important;
                  transition: background 0.5s linear;
                }
              `}</style>
              {autoTools.every(
                (t) => toolsDone[t.nexusModId] || toolsSkipped[t.nexusModId]
              ) &&
              autoTools.some((t) => toolsSkipped[t.nexusModId]) ? (
                /* Skipped: say plainly what it costs, and offer the way back. */
                <div>
                  <Field label="Step 3">
                    Modding tools skipped — mods that need{" "}
                    {autoTools
                      .filter((t) => toolsSkipped[t.nexusModId])
                      .map((t) => t.name)
                      .join(" or ")}{" "}
                    won't work until it's applied. Everything else installs
                    normally.
                  </Field>
                  <ButtonItem
                    layout="below"
                    description="Puts the step back so you can try again"
                    onClick={async () => {
                      await skipPrefixTools(
                        game.nexusDomain,
                        autoTools.map((t) => t.nexusModId),
                        false
                      );
                      refreshStatus();
                    }}
                  >
                    Un-skip modding tools
                  </ButtonItem>
                </div>
              ) : autoTools.length === 0 ? (
                <Field label="Step 3">
                  ⚠ {game.displayName}'s tools have their own windows and
                  cannot be run from here.
                </Field>
              ) : autoTools.every((t) => toolsDone[t.nexusModId]) ? (
                <Field label="Step 3">
                  Modding tools applied ✓ (
                  {autoTools.map((t) => t.name).join(", ")})
                </Field>
              ) : (
                <div
                  className={toolsBusy ? "nexus-tool-fill" : undefined}
                  style={
                    toolsBusy
                      ? ({ "--tool-pct": `${toolPct}%` } as React.CSSProperties)
                      : undefined
                  }
                >
                <Field label="Step 3" childrenLayout="below">
                  <Focusable style={{ display: "flex", gap: "8px" }}>
                    <DialogButton
                      disabled={toolsBusy !== undefined || firstRunNeeded}
                      style={{
                        flex: "1 1 auto",
                        minWidth: 0,
                        padding: "8px 10px",
                        fontSize: "13px",
                        // Off-white, not brand orange. Orange is reserved
                        // for the one primary action or for something
                        // happening right now, and three orange buttons in
                        // a row of setup steps makes none of them read as
                        // the one to press.
                        background: "rgba(240, 240, 238, 0.92)",
                        color: "#1c1c1c",
                      }}
                      onClick={async () => {
                    for (const tool of autoTools) {
                      if (toolsDone[tool.nexusModId]) continue;
                      toolClock.current = {
                        start: Date.now(),
                        budgetMs: (tool.timeoutSec ?? 180) * 1000,
                      };
                      setToolPct(0);
                      setToolsBusy(
                        `⚙ Running ${tool.name}… (up to ${Math.ceil(
                          (tool.timeoutSec ?? 180) / 60
                        )} min)`
                      );
                      const r = await runPrefixTool(
                        game.nexusDomain,
                        tool.nexusModId,
                        game.installDirName,
                        game.appId,
                        tool.exeHint ?? "",
                        tool.avoidFileKeywords ?? [],
                        tool.verifyChangedFiles,
                        tool.timeoutSec ?? 180
                      );
                      toaster.toast(
                        r.ok
                          ? {
                              title: `${tool.name} applied`,
                              body: `Verified: ${(r.changed ?? []).join(", ")}`,
                            }
                          : {
                              title: `${tool.name} failed${
                                r.stage ? ` (${r.stage})` : ""
                              }`,
                              body: r.error ?? r.output?.slice(-120) ?? "",
                              duration: 12000,
                            }
                      );
                      if (r.ok) {
                        setToolsDone((prev) => ({
                          ...prev,
                          [tool.nexusModId]: true,
                        }));
                      }
                    }
                    setToolsBusy(undefined);
                    // Re-read from the backend rather than trusting the
                    // optimistic local flags. A tool that ran but failed
                    // verification left the count unchanged with no
                    // explanation, which reads as "the button did nothing".
                    refreshStatus();
                  }}
                    >
                      {toolsBusy ??
                        `Apply modding tools (${
                          autoTools.filter(
                            (t) => !toolsDone[t.nexusModId]
                          ).length
                        })`}
                    </DialogButton>
                    {!toolsBusy && (
                      <DialogButton
                        style={{
                          flex: "0 0 auto",
                          width: "auto",
                          minWidth: "0",
                          padding: "8px 14px",
                          fontSize: "13px",
                        }}
                        onClick={async () => {
                          await skipPrefixTools(
                            game.nexusDomain,
                            autoTools
                              .filter((t) => !toolsDone[t.nexusModId])
                              .map((t) => t.nexusModId),
                            true
                          );
                          refreshStatus();
                        }}
                      >
                        Skip
                      </DialogButton>
                    )}
                  </Focusable>
                </Field>
                {/* The detail lives in an accordion - the QAM has no room
                    for two paragraphs, but the trade-off must be readable. */}
                <ButtonItem
                  layout="below"
                  onClick={() => setToolsInfoOpen(!toolsInfoOpen)}
                >
                  {toolsInfoOpen ? "▾" : "▸"} What are these? / Why skip?
                </ButtonItem>
                {toolsInfoOpen && (
                  <div
                    style={{
                      padding: "8px 10px",
                      margin: "0 0 8px",
                      background: "rgba(255,255,255,0.05)",
                      borderRadius: "4px",
                      fontSize: "12px",
                      lineHeight: 1.5,
                    }}
                  >
                    <b>Apply</b> downloads{" "}
                    {game.prefixTools
                      .filter((t) => !toolsDone[t.nexusModId])
                      .map((t) => t.name)
                      .join(" and ")}{" "}
                    from Nexus Mods and runs it inside the game's own
                    environment. {game.prefixTools[0].description} Takes a few
                    minutes; Steam may close this menu while the tool's window
                    flashes up - reopen it for the result.
                    <br />
                    <br />
                    <b>Skip</b> if it keeps failing: these are old Windows
                    tools and some refuse to run under Proton. Skipping hides
                    this step and lets you mod normally - only mods that
                    specifically require it become unavailable. You can apply
                    it later from Desktop Mode, or un-skip to retry.
                  </div>
                )}
                {!toolsBusy &&
                  game.prefixTools
                    .filter((t) => toolsLast[t.nexusModId])
                    .map((t) => (
                      <div
                        key={t.nexusModId}
                        style={{
                          padding: "7px 10px",
                          margin: "0 0 8px",
                          background: "rgba(224, 92, 92, 0.12)",
                          borderLeft: "3px solid #e05c5c",
                          borderRadius: "4px",
                          fontSize: "12px",
                          lineHeight: 1.45,
                        }}
                      >
                        ⚠ {t.name} last failed at{" "}
                        <b>{toolsLast[t.nexusModId].stage}</b>:{" "}
                        {toolsLast[t.nexusModId].message}
                      </div>
                    ))}
                </div>
              )}
            </PanelSectionRow>
          )}
          {/* Tools nothing here can drive. Said once, plainly, as optional -
              not as a step that can never be completed. */}
          {manualTools.length > 0 && status?.installed && (
            <PanelSectionRow>
              <Field label="Optional, and only from Desktop Mode" childrenLayout="below">
                {manualTools.map((t) => t.name).join(" and ")}{" "}
                {manualTools.length === 1 ? "is a Windows installer" : "are Windows installers"}{" "}
                with {manualTools.length === 1 ? "its" : "their"} own window,
                so {manualTools.length === 1 ? "it" : "they"} cannot be run
                from Gaming Mode. Nothing needs{" "}
                {manualTools.length === 1 ? "it" : "them"} to play — mods
                install and the game starts without{" "}
                {manualTools.length === 1 ? "it" : "them"}. If you want{" "}
                {manualTools.length === 1 ? "it" : "them"}, switch to Desktop
                Mode, download from Nexus Mods and run{" "}
                {manualTools.length === 1 ? "it" : "them"} against your Data
                folder.
              </Field>
            </PanelSectionRow>
          )}
          {game.controllerNotice && status?.installed && (
            <PanelSectionRow>
              <ButtonItem
                label="🎮 Controller"
                layout="below"
                description={game.controllerNotice}
                onClick={() => {
                  // Straight to this game's controller-layout screen -
                  // Community Layouts is one tab away from here.
                  const steamClient = (window as any).SteamClient;
                  if (steamClient?.URL?.ExecuteSteamURL) {
                    steamClient.URL.ExecuteSteamURL(
                      `steam://controllerconfig/${game.appId}`
                    );
                    Navigation.CloseSideMenus();
                  } else {
                    toaster.toast({
                      title: "Open it manually",
                      body: "Steam button → Controller Settings → Community Layouts",
                    });
                  }
                }}
              >
                Open controller layouts
              </ButtonItem>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            {(game.launcherBypass || game.me3) && status?.installed ? (
              <Field
                label={game.me3 ? "Step 4" : game.prefixTools ? "Step 4" : "Step 3"}
                childrenLayout="below"
              >
                <OrangeActionButton
                  onClick={() => {
                    setBrowseGame(game);
                    resetTabStack();
                    pushOurPage(BROWSE_ROUTE);
                    Navigation.CloseSideMenus();
                  }}
                >
                  Open Mod Browser
                </OrangeActionButton>
              </Field>
            ) : (
              <OrangeActionButton
                onClick={() => {
                  setBrowseGame(game);
                  resetTabStack();
                  pushOurPage(BROWSE_ROUTE);
                  Navigation.CloseSideMenus();
                }}
              >
                Open Mod Browser
              </OrangeActionButton>
            )}
          </PanelSectionRow>
          {/* Seamless Co-op matches players on a shared password. It sits
              between "get mods" and "play" because that's when it's set:
              after the mod is in, before the session starts. Masked by
              default - it's shown on a TV as often as a handheld. */}
          {game.me3 && me3?.coop_installed && (
            <>
              <PanelSectionRow>
                {/* Label and description belong to the Field, not the
                    TextField: that leaves the flex row holding just the
                    input and the button, so centring them against each
                    other needs no guessed offsets. Sized to match the
                    reveal buttons on the installed-mod rows. */}
                <Field
                  label="Co-op password"
                  description="Everyone playing together needs the same one."
                  childrenLayout="below"
                >
                  <Focusable
                    style={{
                      display: "flex",
                      gap: "8px",
                      alignItems: "center",
                    }}
                  >
                    <div style={{ flexGrow: 1, minWidth: 0 }}>
                      {/* A saved password is rendered as dots by us rather
                          than left to the field's own bIsPassword, which
                          doesn't mask on this Steam build. An empty one
                          has nothing to hide, so it stays editable and
                          you can just type. */}
                      {coopMasked ? (
                        <div
                          style={{
                            background: "rgba(0, 0, 0, 0.2)",
                            borderRadius: "2px",
                            padding: "10px 12px",
                            fontSize: "14px",
                            letterSpacing: "2px",
                            opacity: 0.8,
                            overflow: "hidden",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {"•".repeat(Math.min(coopSaved.length, 20))}
                        </div>
                      ) : (
                        <TextField
                          bIsPassword={false}
                          value={coopPassword}
                          onChange={(e) =>
                            setCoopPassword(e?.target?.value ?? "")
                          }
                        />
                      )}
                    </div>
                    {/* Nothing saved yet means nothing to reveal. */}
                    {coopSaved.length > 0 && (
                      <DialogButton
                        onClick={() => setCoopShown((s) => !s)}
                        style={{
                          minWidth: "40px",
                          width: "40px",
                          height: "32px",
                          padding: "0",
                          flexShrink: 0,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        {coopShown ? (
                          <FaEyeSlash size={14} />
                        ) : (
                          <FaEye size={14} />
                        )}
                      </DialogButton>
                    )}
                  </Focusable>
                </Field>
              </PanelSectionRow>
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  disabled={coopPassword === coopSaved}
                  onClick={async () => {
                    const r = await setMe3CoopPassword(
                      game.nexusDomain,
                      coopPassword
                    );
                    toaster.toast(
                      r.ok
                        ? {
                            title: "Co-op password saved",
                            body: "Takes effect next time the game starts",
                          }
                        : {
                            title: "Password not saved",
                            body: r.error ?? "Try a shorter one",
                          }
                    );
                    if (r.ok) setCoopSaved(coopPassword);
                  }}
                >
                  Save co-op password
                </ButtonItem>
              </PanelSectionRow>
            </>
          )}
          <PanelSectionRow>
            <ButtonItem
              label={
                status?.installed
                  ? game.me3
                    ? "Step 5"
                    : game.launcherBypass
                      ? game.prefixTools
                        ? "Step 5"
                        : "Step 4"
                      : undefined
                  : undefined
              }
              layout="below"
              description="Restarts are required for mods to take effect"
              onClick={launchGame}
            >
              {gameIsRunning
                ? `Restart ${game.displayName}`
                : `Launch ${game.displayName}`}
            </ButtonItem>
          </PanelSectionRow>
          {/* Reassurance, not a step - so it sits after the steps rather
              than interrupting them. */}
          {game.me3 && status?.installed && (
            <PanelSectionRow>
              <Field description="Modded sessions run offline with their own save file, so your online character is untouched. Playing modded on FromSoft's servers gets accounts banned — the plugin never enables it.">
                Offline &amp; separate saves: always on
              </Field>
            </PanelSectionRow>
          )}
        </>
      )}
    </PanelSection>
  );
}

function AllInstalledModsSection() {
  // Neutral/unsupported contexts: a collapsed accordion of every installed
  // mod, grouped by game. Full per-game tooling lives in the game's context.
  const [expanded, setExpanded] = useState(false);
  const [byGame, setByGame] = useState<
    { game: SupportedGame; mods: InstalledMod[] }[] | undefined
  >();
  const [busyFolder, setBusyFolder] = useState<string | undefined>();

  const refresh = () => {
    Promise.all(
      ALL_GAMES.map(async (g) => ({
        game: g,
        mods:
          (
            await getInstalledMods(
              g.nexusDomain,
              g.installDirName,
              g.modsSubdir,
              ...modeParams(g),
              g.protectedModFolders ?? []
            )
          ).mods ?? [],
      }))
    ).then((results) => setByGame(results.filter((r) => r.mods.length > 0)));
  };
  useEffect(refresh, []);

  if (!byGame || byGame.length === 0) return null;
  const total = byGame.reduce((n, r) => n + r.mods.length, 0);

  const onToggle = async (
    game: SupportedGame,
    mod: InstalledMod,
    enabled: boolean
  ) => {
    setBusyFolder(mod.folder);
    try {
      const result = await setModEnabled(
        game.installDirName,
        game.modsSubdir,
        mod.folder,
        enabled,
        game.installMode ?? "folder",
        game.nexusDomain,
        game.appId,
        game.pluginsTxtSubpath ?? "",
        game.pluginsTxtStyle ?? "starred"
      );
      if (!result.ok) {
        toaster.toast({ title: "Could not toggle mod", body: result.error ?? "" });
      }
    } finally {
      setBusyFolder(undefined);
      refresh();
    }
  };

  return (
    <PanelSection title="Installed Mods">
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => setExpanded(!expanded)}>
          {expanded ? "▾" : "▸"} {total} mod{total === 1 ? "" : "s"} ·{" "}
          {byGame.length} game{byGame.length === 1 ? "" : "s"}
        </ButtonItem>
      </PanelSectionRow>
      {expanded &&
        byGame.map(({ game, mods }) => (
          <Fragment key={game.appId}>
            <PanelSectionRow>
              <div
                style={{
                  fontWeight: 600,
                  fontSize: "13px",
                  opacity: 0.75,
                  marginTop: "8px",
                }}
              >
                {game.displayName}
              </div>
            </PanelSectionRow>
            {mods.map((mod) => (
              <PanelSectionRow key={`${mod.folder}:${mod.enabled}`}>
                <ToggleField
                  label={mod.name ?? mod.folder}
                  description={
                    mod.tracked
                      ? `v${mod.version}${mod.enabled ? "" : " · disabled"}`
                      : "not installed by this plugin"
                  }
                  checked={mod.enabled}
                  disabled={busyFolder === mod.folder}
                  onChange={(checked: boolean) => {
                    if (checked !== mod.enabled) onToggle(game, mod, checked);
                  }}
                />
              </PanelSectionRow>
            ))}
          </Fragment>
        ))}
    </PanelSection>
  );
}

function FailedModsModal({
  failures,
  closeModal,
}: {
  failures: { name: string; detail: string }[];
  closeModal?: () => void;
}) {
  return (
    <ModalRoot closeModal={closeModal}>
      <h3 style={{ marginTop: 0 }}>Mods that failed to load</h3>
      {failures.map((f) => (
        <div key={f.name} style={{ marginBottom: "10px" }}>
          <b>{f.name}</b>
          <div
            style={{
              fontSize: "12px",
              opacity: 0.8,
              fontFamily: "monospace",
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {f.detail || "(no error detail captured)"}
          </div>
        </div>
      ))}
      <div
        style={{
          marginTop: "12px",
          padding: "8px 10px",
          background: "rgba(120, 170, 255, 0.10)",
          borderLeft: "3px solid #78aaff",
          borderRadius: "4px",
          fontSize: "13px",
          lineHeight: "1.45",
        }}
      >
        A "patching exception" usually means the mod's code doesn't match this
        version of the game — and some mods need library mods (BaseLib,
        RitsuLib) on the Linux build even when Nexus Mods lists no requirements.
        Try installing the libraries from the browser, check the mod's
        description and posts, or look for an updated version.
      </div>
    </ModalRoot>
  );
}

function InstalledModsSection() {
  // Active-game context: toggling mods makes the most sense while the game
  // is NOT running, so this never requires a running game.
  const { game } = resolveGameContext();

  const [mods, setMods] = useState<InstalledMod[] | undefined>();
  const [busyFolder, setBusyFolder] = useState<string | undefined>();
  const [busyAll, setBusyAll] = useState(false);
  const [loadStates, setLoadStates] = useState<
    Record<string, ModLoadState> | undefined
  >();
  const [updates, setUpdates] = useState<Record<string, UpdateInfo> | undefined>();
  const [fwStatus, setFwStatus] = useState<GameStatus | undefined>();
  const [fwEnabled, setFwEnabled] = useState(true);
  // Enabled plugins whose masters are absent (missing DLC, external
  // prerequisites like TTW): the game refuses to boot until they're off.
  const [brokenPlugins, setBrokenPlugins] = useState<
    { plugin: string; missing: string[] }[]
  >([]);

  const refresh = () => {
    if (game) {
      getInstalledMods(
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        ...modeParams(game),
        game.protectedModFolders ?? []
      ).then((r) => setMods(r.ok ? r.mods : []));
      if (game.installMode === "dataDir" && game.pluginsTxtSubpath) {
        checkPluginMasters(
          game.installDirName,
          game.modsSubdir,
          game.appId,
          game.pluginsTxtSubpath,
          game.pluginsTxtStyle ?? "starred"
        ).then((r) => setBrokenPlugins(r.ok ? r.broken ?? [] : []));
      } else {
        setBrokenPlugins([]);
      }
      if (game.logAdapter?.kind === "godot") {
        getModLoadStatus(game.logAdapter.userDirName).then((r) =>
          setLoadStates(
            r.ok && r.available && r.modded_session ? r.status : undefined
          )
        );
      } else if (game.logAdapter?.kind === "smapi") {
        getSmapiLoadStatus(game.logAdapter.configDirName).then((r) =>
          setLoadStates(
            r.ok && r.available && r.modded_session ? r.status : undefined
          )
        );
      } else {
        setLoadStates(undefined);
      }
      // Ask which mods the game blamed first, so a collection-pinned mod
      // the game cannot run still gets its update offered. Without this the
      // panel reported "no updates" at a game printing "Loaded 21 mods WITH
      // ERRORS" over its own main menu.
      (game.logAdapter?.kind === "godot"
        ? getBlamedFolders(
            game.nexusDomain,
            game.installDirName,
            game.modsSubdir,
            game.logAdapter.userDirName
          ).then((b) => (b.ok ? b.folders ?? [] : []))
        : Promise.resolve<string[]>([])
      ).then((blamed) =>
        checkUpdates(game.nexusDomain, blamed).then((r) =>
          setUpdates(r.ok ? r.updates : undefined)
        )
      );
      if (game.framework) {
        getGameStatus(
          game.installDirName,
          game.modsSubdir,
          game.framework.detectFile
        ).then(setFwStatus);
        getFrameworkSetup(game.nexusDomain, "").then((r) =>
          setFwEnabled(r.enabled !== false)
        );
      }
    }
  };

  useEffect(refresh, [game?.appId]);
  // Reset now lives in its own section, so this list has to be told when
  // one happens or it keeps listing mods that are already gone.
  useEffect(() => subscribeGameState(refresh), [game?.appId]);

  if (!game) return null;

  // The framework (SMAPI) isn't a Mods/-folder mod, but it deserves a row:
  // its toggle applies/clears the launch options - a real enable/disable.
  const showFrameworkRow = Boolean(
    game.framework && fwStatus?.framework_installed
  );

  const onToggleFramework = async (enabled: boolean) => {
    if (!game.framework?.launchOptionsTemplate || !fwStatus) return;
    const ok = enabled
      ? setLaunchOptions(
          game.appId,
          game.framework.launchOptionsTemplate.replace(
            "{install_path}",
            fwStatus.install_path
          )
        )
      : setLaunchOptions(game.appId, "");
    if (!ok) {
      toaster.toast({
        title: "Could not change launch options",
        body: "Steam client API unavailable",
      });
      return;
    }
    if (enabled) {
      await markLaunchOptionsSet(
        game.nexusDomain,
        game.framework?.launchOptionsTemplate ?? ""
      );
    } else {
      await setFrameworkEnabled(game.nexusDomain, false);
    }
    setFwEnabled(enabled);
    toaster.toast({
      title: enabled
        ? `${game.framework.name} enabled`
        : `${game.framework.name} disabled`,
      body: enabled
        ? "Mods will load next launch"
        : `${game.displayName} will launch without mods`,
    });
  };

  // Same normalization as the backend: log tags vs manifest ids can differ
  // in dashes/underscores.
  const loadStateFor = (folder: string): ModLoadState | undefined =>
    loadStates?.[folder.toLowerCase().replace(/[^a-z0-9]/g, "")];

  // Creation Club masters (cc*.esl/.esm) are paid Anniversary Edition
  // content, not a missing mod - no amount of installing fixes them, so
  // the copy says so rather than implying the user can act on it.
  const creationClubCount = brokenPlugins.filter((b) =>
    b.missing.some((m) => /^cc[a-z]/i.test(m))
  ).length;


  // Reset deliberately does NOT live in here - see ResetSection.
  if (!showInstalledModsSection((mods ?? []).length, showFrameworkRow)) {
    return null;
  }

  // Only mods with a toggle count: the framework renders its own row and
  // always-active mods can't be flipped - with none toggleable, a lone
  // enabled SKSE made this read "Enable all" out of nowhere.
  const toggleableMods = (mods ?? []).filter((m) => m.togglable !== false);
  const anyEnabled = toggleableMods.some((m) => m.enabled);

  const onToggle = async (mod: InstalledMod, enabled: boolean) => {
    setBusyFolder(mod.folder);
    try {
      const result = await setModEnabled(
        game.installDirName,
        game.modsSubdir,
        mod.folder,
        enabled,
        game.installMode ?? "folder",
        game.nexusDomain,
        game.appId,
        game.pluginsTxtSubpath ?? "",
        game.pluginsTxtStyle ?? "starred"
      );
      if (!result.ok) {
        toaster.toast({ title: "Could not toggle mod", body: result.error ?? "" });
      }
    } finally {
      setBusyFolder(undefined);
      refresh();
    }
  };

  const onToggleAll = async (enabled: boolean) => {
    setBusyAll(true);
    try {
      const result = await setAllModsEnabled(
        game.installDirName,
        game.modsSubdir,
        enabled,
        game.installMode ?? "folder",
        game.nexusDomain,
        game.appId,
        game.pluginsTxtSubpath ?? "",
        game.pluginsTxtStyle ?? "starred"
      );
      if (result.ok && result.errors && result.errors.length > 0) {
        toaster.toast({
          title: "Some mods could not be moved",
          body: result.errors.join("; "),
        });
      }
    } finally {
      setBusyAll(false);
      refresh();
    }
  };

  return (
    <PanelSection title="Installed Mods">
      {brokenPlugins.length > 0 && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            // "Broken" reads as "you broke something", and on a big
            // collection the number is alarming. Nothing is damaged:
            // these mods are waiting on files that aren't there, and
            // most of the time that is paid Creation Club content the
            // user simply doesn't own. Say that, and call the fix what
            // it is - skipping them.
            label={`${brokenPlugins.length} mod${
              brokenPlugins.length > 1 ? "s" : ""
            } can't load yet`}
            description={
              `${
                brokenPlugins.length > 1 ? "They need" : "It needs"
              } files that aren't installed (e.g. ${
                brokenPlugins[0].plugin
              } needs ${brokenPlugins[0].missing[0]}). ` +
              (creationClubCount > 0
                ? `${creationClubCount} of them want Creation Club content, which comes with the paid Anniversary Edition upgrade - installing more mods won't help those. `
                : "") +
              `Skipping turns them off so ${game.displayName} starts. Nothing is deleted and you can turn them back on any time.`
            }
            onClick={async () => {
              const result = await disablePlugins(
                game.appId,
                game.pluginsTxtSubpath ?? "",
                game.pluginsTxtStyle ?? "starred",
                brokenPlugins.map((b) => b.plugin)
              );
              toaster.toast(
                result.ok
                  ? {
                      title: "Mods skipped",
                      body: `${result.disabled ?? 0} turned off — ${game.displayName} should start now`,
                    }
                  : { title: "Could not skip them", body: result.error ?? "" }
              );
              refresh();
            }}
          >
            Skip {brokenPlugins.length} mod
            {brokenPlugins.length > 1 ? "s" : ""}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {showFrameworkRow && game.framework && (
        <PanelSectionRow key={`framework:${fwEnabled}`}>
          <ToggleField
            label={`${game.framework.name} (mod loader)`}
            description={
              fwEnabled
                ? "framework — mods need it"
                : "disabled · game launches without mods"
            }
            checked={fwEnabled}
            onChange={(checked: boolean) => {
              if (checked !== fwEnabled) onToggleFramework(checked);
            }}
          />
        </PanelSectionRow>
      )}
      {/* Collections make this list enormous - cap the QAM at 5 rows and
          hand the rest to the full-screen manager. */}
      {(mods ?? []).slice(0, 5).map((mod) => {
        const load = mod.enabled ? loadStateFor(mod.folder) : undefined;
        const update = updates?.[mod.folder];
        const badge =
          (load === undefined
            ? ""
            : load.state === "loaded"
            ? " · loaded ✓"
            : " · failed to load ⚠") +
          (update?.update_available
            ? update.blamed
              ? // Not a nag. The game named this mod as erroring and the
                // newer version is the likely reason it will stop.
                ` · ⬆ ${update.current} may fix its errors`
              : ` · ⬆ ${update.current} available`
            : "");
        const base = mod.tracked
          ? `v${mod.version}${mod.enabled ? "" : " · disabled"}`
          : "not installed by this plugin";
        return (
          // key includes enabled-state: Steam's toggle only reads `checked` on
          // mount, so a remount is required for programmatic state changes
          // (e.g. "Disable all") to actually show.
          <PanelSectionRow key={`${mod.folder}:${mod.enabled}`}>
            <Focusable
              style={{ display: "flex", alignItems: "flex-start", gap: "4px" }}
            >
              <div style={{ flexGrow: 1, minWidth: 0 }}>
                <ToggleField
                  label={mod.name ?? mod.folder}
                  description={
                    mod.togglable === false
                      ? base + badge + " · assets only, always active"
                      : base + badge
                  }
                  checked={mod.enabled}
                  disabled={
                    busyFolder === mod.folder ||
                    busyAll ||
                    mod.togglable === false
                  }
                  onChange={(checked: boolean) => {
                    if (checked !== mod.enabled) onToggle(mod, checked);
                  }}
                />
              </div>
              {mod.mod_id !== undefined && (
                <DialogButton
                  style={{
                    minWidth: "40px",
                    width: "40px",
                    height: "32px",
                    padding: "0",
                    flexShrink: 0,
                    // The toggle knob sits in the field's FIRST line (the
                    // description renders below), so center against that
                    // line instead of the whole field.
                    alignSelf: "flex-start",
                    marginTop: "5px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                  onClick={() => openInstalledModDetail(game, mod)}
                >
                  <FaEye size={14} />
                </DialogButton>
              )}
            </Focusable>
          </PanelSectionRow>
        );
      })}
      {(mods?.length ?? 0) > 5 && (
        <PanelSectionRow>
          <Field
            description={`…and ${
              (mods?.length ?? 0) - 5
            } more — Manage my mods below has the full list`}
          />
        </PanelSectionRow>
      )}
      {toggleableMods.length > 0 && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busyAll}
            onClick={() => onToggleAll(!anyEnabled)}
          >
            {anyEnabled ? "Disable all (play vanilla)" : "Enable all"}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {/* Uninstalls moved to the full-screen My Mods page (v0.43.0):
          the QAM picker outgrew collection-scale libraries. */}
      {(mods?.length ?? 0) > 0 && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="Toggle, inspect and uninstall - the full manager"
            onClick={() => {
              Router.CloseSideMenus();
              resetTabStack();
              pushOurPage(MANAGER_ROUTE);
            }}
          >
            Manage my mods →
          </ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

/** Reset lives in its own section so it survives an empty mod list. When
 * it sat with the bulk actions, running it removed the last mod, the
 * section returned null, and the button disappeared - unreachable for
 * anyone wanting to start over from a half-configured state. */
/** Everything for when the game is misbehaving, kept out of the way.
 *
 * These rows are not setup: they only matter when something has already
 * gone wrong, and a panel that leads with five ways to disable mods reads
 * as "this is fragile". Collapsed by default, and sitting next to Reset
 * because that is where someone looks when the game will not start.
 *
 * It lives in its own section rather than in the game panel so it can be
 * placed at the bottom - the game panel renders first, above the mod
 * list. */
function TroubleshootingSection() {
  const { game } = resolveGameContext();
  const [open, setOpen] = useState(false);
  const [installed, setInstalled] = useState(false);
  // Prefix VC++ runtime: mod binaries that link it dynamically won't
  // load against the ancient one a game's install script leaves behind.
  const [runtime, setRuntime] = useState<
    { have?: string; newest?: string; outdated?: boolean } | undefined
  >();
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  // DLL plugins the script extender refused to load last launch, plus
  // any implicated in a crash since.
  const [sePlugins, setSePlugins] = useState<
    | {
        failed: ScriptExtenderPlugin[];
        parked: string[];
        dir: string;
        crash?: CrashReport;
      }
    | undefined
  >();
  const [seBusy, setSeBusy] = useState(false);
  const [blockedBusy, setBlockedBusy] = useState(false);
  const [ghostBusy, setGhostBusy] = useState(false);
  // Mods the game's own log blamed last session. Named by the log, so
  // "which ones are outdated" stops being a hunt.
  const [failing, setFailing] = useState<{
    names: string[];
    details: { name: string; why: string }[];
    held: string[];
    repaired: string[];
    updated: { name: string; from: string; to: string }[];
    noUpdate: string[];
    installedDeps: { name: string; for: string }[];
  }>({
    names: [],
    details: [],
    held: [],
    repaired: [],
    updated: [],
    noUpdate: [],
    installedDeps: [],
  });
  const [failingBusy, setFailingBusy] = useState(false);
  // Enabled plugins listed before a master they need - a boot crash.
  const [loadOrder, setLoadOrder] = useState<
    {
      total: number;
      violations: number;
      disabledMasters: number;
      examples: string[];
      fullSlots: number;
      fullSlotLimit: number;
      lightSlots: number;
      lightSlotLimit: number;
      missingMasters: { name: string; label?: string; needed_by: number }[];
      blockedPlugins: number;
      ghostPlugins: number;
      ghostExamples: string[];
    }
    | undefined
  >();
  const [loadOrderBusy, setLoadOrderBusy] = useState(false);
  // The automated crash hunt: apply a load order, launch, read the crash
  // log, repeat. Running it by hand took two days and five culprits.
  const [hunt, setHunt] = useState<
    | {
        running: boolean;
        launches: number;
        remaining: number;
        skipped: string[];
        note: string;
      }
    | undefined
  >();

  const refresh = () => {
    if (!game) return;
    getGameStatus(game.installDirName, game.modsSubdir, "").then((s) =>
      setInstalled(Boolean(s.installed))
    );
    if (game.prefixRuntimeFix) {
      getPrefixRuntimeState(game.appId).then((r) =>
        setRuntime(r.ok && r.prefix_exists ? r : undefined)
      );
    }
    if (game.scriptExtenderLog) {
      getScriptExtenderState(
        game.appId,
        game.installDirName,
        game.scriptExtenderLog
      ).then((r) =>
        setSePlugins(
          r.ok
            ? {
                failed: r.failed ?? [],
                parked: r.parked ?? [],
                dir: r.plugins_dir ?? "",
                crash: r.crash,
              }
            : undefined
        )
      );
    }
    // Switch off what the game could not run, then report what is left.
    // The mod that threw 1,041 exceptions was knowable from the log and the
    // plugin still waited to be asked - which is a button where there
    // should have been an action.
    if (game.logAdapter?.kind === "godot") {
      repairFailingMods(
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        game.logAdapter.userDirName,
        game.installMode ?? "folder",
        game.appId,
        game.pluginsTxtSubpath ?? "",
        game.pluginsTxtStyle ?? "starred",
        game.recommendedModIds ?? []
      ).then((r) =>
        setFailing({
          names: r.ok ? (r.remaining ?? []).map((d) => d.name) : [],
          details: r.ok ? r.remaining ?? [] : [],
          held: r.ok ? r.held ?? [] : [],
          repaired: r.ok ? r.names ?? [] : [],
          updated: r.ok ? r.updated ?? [] : [],
          noUpdate: r.ok ? r.no_update ?? [] : [],
          installedDeps: r.ok ? r.installed_deps ?? [] : [],
        })
      );
    } else {
      setFailing({
        names: [],
        details: [],
        held: [],
        repaired: [],
        updated: [],
        noUpdate: [],
        installedDeps: [],
      });
    }
    if (game.pluginsTxtSubpath) {
      getLoadOrderState(
        game.appId,
        game.installDirName,
        game.pluginsTxtSubpath,
        game.pluginsTxtStyle ?? "starred",
        game.nexusDomain
      ).then((r) =>
        setLoadOrder(
          r.ok && r.supported
            ? {
                total: r.total ?? 0,
                violations: r.violations ?? 0,
                disabledMasters: r.disabled_masters ?? 0,
                examples: r.examples ?? [],
                fullSlots: r.full_slots ?? 0,
                fullSlotLimit: r.full_slot_limit ?? 254,
                lightSlots: r.light_slots ?? 0,
                lightSlotLimit: r.light_slot_limit ?? 4096,
                missingMasters: r.missing_masters ?? [],
                blockedPlugins: r.blocked_plugins ?? 0,
                ghostPlugins: r.ghost_plugins ?? 0,
                ghostExamples: r.ghost_examples ?? [],
              }
            : undefined
        )
      );
    }
  };

  useEffect(refresh, [game?.appId]);
  // The one plugin worth offering to skip after a crash, if any.
  const suspect = crashSuspect(sePlugins?.crash?.culprits);
  // Two faults, one row: nothing loads if either is true.
  const loadOrderIssue = loadOrder
    ? loadOrderProblem(
        loadOrder.violations,
        loadOrder.disabledMasters,
        loadOrder.examples
      )
    : undefined;
  // Kept apart from loadOrderIssue: that one has a Fix button behind it,
  // and no button here can install DLC the account does not own.
  const missingMasters = loadOrder
    ? missingMasterProblem(loadOrder.missingMasters, loadOrder.blockedPlugins)
    : undefined;
  const blockedAction = blockedPluginsAction(loadOrder?.missingMasters);
  const ghostIssue = loadOrder
    ? ghostPluginProblem(loadOrder.ghostPlugins, loadOrder.ghostExamples)
    : undefined;

  /** Drive the hunt: set a load order, launch, watch, record, repeat.
   *
   * Deliberately reads the crash log itself rather than asking what the
   * user saw. Doing this by hand, two steps were corrupted by a mod dying
   * on a form its own (now-disabled) plugin used to provide - a crash that
   * looks identical from the outside and means nothing here. */
  const runCrashHunt = async () => {
    if (!game?.pluginsTxtSubpath || !game.scriptExtenderLog) return;
    if (game.crashSignature === undefined) return;
    huntStopFlag = false;
    huntActive = true;
    const style = game.pluginsTxtStyle ?? "starred";
    const started = await crashBisectStart(
      game.appId,
      game.installDirName,
      game.pluginsTxtSubpath,
      style,
      game.nexusDomain,
      game.crashSignature,
      game.scriptExtenderLog,
      game.huntKeepDlls ?? []
    );
    if (!started.ok) {
      toaster.toast({ title: "Couldn't start", body: started.error ?? "" });
      return;
    }
    const target = started.signature ?? game.crashSignature;
    setHunt({
      running: true,
      launches: 0,
      remaining: started.total ?? 0,
      skipped: [],
      note: `Chasing ${target}`,
    });
    let strayCrashes = 0;
    for (;;) {
      if (huntStopFlag) break;
      const step = await crashBisectApply();
      if (!step.ok || step.done) break;
      setHunt((h) => ({
        running: true,
        launches: step.launches ?? h?.launches ?? 0,
        remaining: step.remaining ?? 0,
        skipped: step.skipped ?? h?.skipped ?? [],
        note: `Launch ${(step.launches ?? 0) + 1}: trying ${(
          step.enabled ?? 0
        ).toLocaleString()} mods`,
      }));
      // Numbered every time: hours of the game opening and closing is
      // indistinguishable from a boot loop without a running count.
      const note = huntProgressNote(
        (step.launches ?? 0) + 1,
        step.enabled ?? 0,
        step.remaining ?? 0,
        (step.skipped ?? []).length
      );
      toaster.toast({ ...note, duration: 12000 });
      const launchedAt = Date.now() / 1000;
      restartGame(game.appId);
      let verdict: ReturnType<typeof crashHuntVerdict> = "waiting";
      while (!huntStopFlag) {
        await new Promise((r) => setTimeout(r, 10_000));
        const seen = await crashSince(
          game.appId,
          game.scriptExtenderLog,
          launchedAt
        );
        verdict = crashHuntVerdict(
          Date.now() - launchedAt * 1000,
          seen.crash?.address,
          game.crashSignature
        );
        if (verdict !== "waiting") break;
      }
      if (huntStopFlag) break;
      // A different crash tells us nothing about the fault being hunted,
      // so it is not folded in - the run is simply repeated.
      if (verdict === "other-crash") {
        // Bounded, because "retry" only helps if the outcome can change.
        // A mod dying on a form its own disabled plugin used to provide
        // does it every single time - that looped 20 times overnight.
        strayCrashes += 1;
        if (strayCrashes > 3) {
          toaster.toast({
            title: "Hunt stopped — something else keeps crashing",
            body: "A different crash repeated, so the search can't tell mods apart. Nothing was changed.",
            duration: 20000,
          });
          break;
        }
        setHunt((prev) => ({
          ...prev!,
          note: `A different crash (${strayCrashes}/3) - retrying`,
        }));
        toaster.toast({
          title: `Different crash — retry ${strayCrashes} of 3`,
          body: "That one wasn't the fault being hunted, so it doesn't count",
          duration: 8000,
        });
        continue;
      }
      strayCrashes = 0;
      (window as any).SteamClient?.Apps?.TerminateApp?.(
        String(game.appId),
        false
      );
      const rec = await crashBisectRecord(verdict === "crash");
      setHunt({
        running: true,
        launches: rec.launches ?? 0,
        remaining: rec.remaining ?? 0,
        skipped: rec.skipped ?? [],
        note: rec.found ? `Found ${rec.found}` : "Narrowing…",
      });
      if (rec.found) {
        toaster.toast({
          title: "Found a broken mod",
          body: rec.found.replace(/\.es[lmp]$/i, ""),
          duration: 12000,
        });
      }
      if (rec.done) break;
    }
    huntActive = false;
    const end = await crashBisectFinish(true);
    setHunt(undefined);
    toaster.toast({
      title: huntStopFlag ? "Hunt stopped" : "Hunt finished",
      body: (end.skipped?.length ?? 0) > 0
        ? `Skipped ${end.skipped!.length} broken mod${
            end.skipped!.length === 1 ? "" : "s"
          } - everything else is back on`
        : "Nothing found - all mods are back on",
      duration: 15000,
    });
    refresh();
  };

  // A hunt started before the panel was closed is still running: show it
  // again with a working Stop, rather than an inviting Start button and
  // no way to halt what is already going.
  useEffect(() => {
    if (huntActive && !hunt) {
      setHunt({
        running: true,
        launches: 0,
        remaining: 0,
        skipped: [],
        note: "Still running — reopened",
      });
    }
  }, [game?.appId]);

  if (!game || !installed) return null;

  // How many things are actually wrong. Shown on the header so the row is
  // worth opening - or worth ignoring.
  // Separate from loadOrderIssue on purpose: nothing the load-order
  // repair does can free a plugin slot. The only fix is fewer mods.
  const slots = loadOrder
    ? slotPressure(
        loadOrder.fullSlots,
        loadOrder.fullSlotLimit,
        loadOrder.lightSlots,
        loadOrder.lightSlotLimit
      )
    : ({ level: "ok" } as ReturnType<typeof slotPressure>);

  const problems = troubleshootingCount(
    Boolean(runtime?.outdated),
    (sePlugins?.failed.length ?? 0) + failing.names.length,
    Boolean(suspect),
    loadOrderIssue,
    missingMasters || ghostIssue
  );

  return (
    <PanelSection title="Troubleshooting">
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={() => setOpen((v) => !v)}
          description={
            open
              ? undefined
              : problems > 0
              ? `${problems} thing${problems === 1 ? "" : "s"} to look at`
              : "Nothing looks wrong. Open if the game won't start."
          }
        >
          {open ? "Hide" : problems > 0 ? `Fixes (${problems})` : "Fixes"}
        </ButtonItem>
      </PanelSectionRow>
      {open && (
        <>
          {game && (
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                description="Checks every installed mod has the other mods and the game DLC it says it needs — the things the game won't mention until it refuses to start."
                onClick={() => {
                  setHealthGame(game);
                  resetTabStack();
                  pushOurPage(HEALTH_ROUTE);
                  Navigation.CloseSideMenus();
                }}
              >
                Run a health check
              </ButtonItem>
            </PanelSectionRow>
          )}
        {/* The prefix's VC++ runtime falls behind on its own: a game's own
            Steam install script writes an old one, and any mod binary that
            links it dynamically then fails to load with nothing said
            in-game. Sits outside the framework branches because it applies
            to any game with a prefix, and long after setup is "done". */}
        {runtime?.outdated && installed && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              label="Mods not loading?"
              disabled={runtimeBusy}
              description={`${game.displayName}'s Windows runtime is ${
                runtime.have || "very old"
              } — mods built against ${
                runtime.newest || "a newer one"
              } can't load against it, and they fail silently. This copies the newer runtime out of Proton. Safe to run any time.`}
              onClick={async () => {
                setRuntimeBusy(true);
                try {
                  const r = await fixPrefixRuntime(game.appId);
                  toaster.toast(
                    r.ok
                      ? {
                          title: r.updated
                            ? "Runtime updated"
                            : "Runtime already current",
                          body: r.updated
                            ? `${r.previous ?? "old"} → ${r.version} — restart ${game.displayName} to load the mods that were failing`
                            : `Already on ${r.version}`,
                          duration: 10000,
                        }
                      : {
                          title: "Could not update the runtime",
                          body: r.error ?? "",
                        }
                  );
                  refresh();
                } finally {
                  setRuntimeBusy(false);
                }
              }}
            >
              {runtimeBusy ? "Updating…" : "Update the Windows runtime"}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {/* Mods built for an older game than Steam ships will never load,
            and the script extender stops the whole game with a modal
            asking whether to continue - an impossible question, and a
            rotten trade when it's one stale mod out of two thousand.
            Setting it aside (renamed, not deleted) makes the game boot. */}
        {(sePlugins?.failed.length ?? 0) > 0 && installed && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              label={`${sePlugins!.failed.length} mod plugin${
                sePlugins!.failed.length > 1 ? "s" : ""
              } can't run`}
              disabled={seBusy}
              description={
                `${sePlugins!.failed
                  .slice(0, 3)
                  .map((p) => p.name.replace(/\.dll$/i, ""))
                  .join(", ")}${
                  sePlugins!.failed.length > 3
                    ? ` and ${sePlugins!.failed.length - 3} more`
                    : ""
                } failed to load last time` +
                (sePlugins!.failed.some((p) => p.outdated)
                  ? " — they're built for an older version of the game, so only their authors can fix them. "
                  : ". ") +
                "Skipping sets them aside so the game starts. They're renamed, not deleted."
              }
              onClick={async () => {
                setSeBusy(true);
                try {
                  const r = await setScriptExtenderPlugins(
                    game.installDirName,
                    sePlugins!.dir,
                    sePlugins!.failed.map((p) => p.name),
                    false
                  );
                  toaster.toast(
                    r.ok
                      ? {
                          title: `Skipped ${r.changed ?? 0} mod plugin${
                            (r.changed ?? 0) === 1 ? "" : "s"
                          }`,
                          body: `${game.displayName} should start now — the mods that needed them just won't do their thing`,
                          duration: 10000,
                        }
                      : { title: "Could not skip them", body: r.error ?? "" }
                  );
                  refresh();
                } finally {
                  setSeBusy(false);
                }
              }}
            >
              {seBusy
                ? "Skipping…"
                : `Skip ${sePlugins!.failed.length} mod plugin${
                    sePlugins!.failed.length > 1 ? "s" : ""
                  }`}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {/* Skyrim and FO4 read plugins.txt AS the load order, and we only
            ever appended to it - so it was whatever order things happened
            to install in. A plugin listed before a master it depends on
            crashes the game on the way into the world. */}
        {/* Two days of hand-driving this on a 1,960-mod Skyrim found five
            broken plugins at ~12 four-minute launches each, and every
            wasted launch came from me varying something between steps. The
            machine sets the load order, launches, reads the crash log and
            repeats - unattended. */}
        {game.crashSignature !== undefined && installed && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              label={
                hunt?.running
                  ? `Hunting - launch ${hunt.launches + 1}`
                  : "Game crashes on startup?"
              }
              description={
                hunt?.running
                  ? `${hunt.note}. ${hunt.remaining.toLocaleString()} mods left to rule out` +
                    (hunt.skipped.length
                      ? `. Found so far: ${hunt.skipped
                          .map((n) => n.replace(/\.es[lmp]$/i, ""))
                          .join(", ")}`
                      : "")
                  : "Finds the mods responsible by launching repeatedly and " +
                    "reading the crash log itself. Takes a few hours and the " +
                    "game will start and close on its own - leave it alone. " +
                    "Nothing is deleted and every mod comes back except the " +
                    "ones it proves are broken."
              }
              onClick={() => {
                if (hunt?.running) {
                  huntStopFlag = true;
                  setHunt((prev) => ({ ...prev!, note: "Stopping after this launch…" }));
                } else {
                  runCrashHunt();
                }
              }}
            >
              {hunt?.running ? "Stop the hunt" : "Find what's breaking it"}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {failing.details.length > 0 && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              description="Why, and what to try"
              onClick={() =>
                showModal(
                  <FailedModsModal
                    failures={failing.details.map((d) => ({
                      name: d.name,
                      detail: d.why,
                    }))}
                  />
                )
              }
            >
              ⚠ {failing.details.length} mod
              {failing.details.length > 1 ? "s" : ""} failed to load
            </ButtonItem>
          </PanelSectionRow>
        )}
        {game?.logAdapter?.kind === "godot" && (
          <PanelSectionRow>
            <Field label="What the game reported last run" childrenLayout="below">
              {lastRunSummary(
                [
                  ...failing.held,
                  ...failing.details.map((d) => d.name),
                ],
                failing.repaired.length + failing.updated.length,
                failing.noUpdate
              )}
            </Field>
          </PanelSectionRow>
        )}
        {failing.installedDeps.length > 0 && (
          <PanelSectionRow>
            <Field label="✓ Libraries a mod needed" childrenLayout="below">
              {installedDepsNote(failing.installedDeps)}
            </Field>
          </PanelSectionRow>
        )}
        {failing.updated.length > 0 && (
          <PanelSectionRow>
            <Field label="✓ Mods updated to match your game" childrenLayout="below">
              {updatedNote(failing.updated)}
            </Field>
          </PanelSectionRow>
        )}
        {failing.repaired.length > 0 && (
          <PanelSectionRow>
            <Field label="✓ Mods the game could not run" childrenLayout="below">
              {repairedNote(failing.repaired)}
            </Field>
          </PanelSectionRow>
        )}
        {failing.names.length > 0 && game && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              label={
                failing.names.length === 1
                  ? "1 mod broke last time you played"
                  : `${failing.names.length} mods broke last time you played`
              }
              description={failingProblem(failing.details, failing.held)}
              disabled={failingBusy}
              onClick={async () => {
                if (game.logAdapter?.kind !== "godot") return;
                setFailingBusy(true);
                try {
                  const r = await disableFailingMods(
                    game.nexusDomain,
                    game.installDirName,
                    game.modsSubdir,
                    game.logAdapter.userDirName,
                    game.installMode ?? "folder",
                    game.appId,
                    game.pluginsTxtSubpath ?? "",
                    game.pluginsTxtStyle ?? "starred",
                    game.recommendedModIds ?? [],
                    false
                  );
                  toaster.toast({
                    title: r.ok
                      ? `Switched off ${r.disabled ?? 0}`
                      : "Could not switch them off",
                    body: r.ok
                      ? disableFailingOutcome(
                          r.names ?? [],
                          r.held ?? [],
                          r.error
                        )
                      : r.error ?? "",
                  });
                  if (r.ok && (r.disabled ?? 0) > 0) {
                    setFailing((f) => ({ ...f, names: [], details: [] }));
                    notifyGameStateChanged();
                  }
                } finally {
                  setFailingBusy(false);
                }
              }}
            >
              {failingBusy ? "Switching off…" : "Switch them off"}
            </ButtonItem>
          </PanelSectionRow>
        )}
          {ghostIssue && game && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              label="Mods switched on but not installed"
              description={ghostIssue}
              disabled={ghostBusy}
              onClick={async () => {
                setGhostBusy(true);
                try {
                  const r = await removeGhostPlugins(
                    game.appId,
                    game.installDirName,
                    game.pluginsTxtSubpath ?? "",
                    game.pluginsTxtStyle ?? "starred",
                    game.nexusDomain
                  );
                  toaster.toast({
                    title: r.ok
                      ? `Cleared ${r.removed ?? 0}`
                      : "Could not clear them",
                    body: r.ok ? (r.names ?? []).slice(0, 3).join(", ") : r.error ?? "",
                  });
                  if (r.ok) refresh();
                } finally {
                  setGhostBusy(false);
                }
              }}
            >
              {ghostBusy ? "Clearing…" : "Clear them"}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {missingMasters && (
          <PanelSectionRow>
            <Field label="⚠ Missing game content" childrenLayout="below">
              {missingMasters}
            </Field>
          </PanelSectionRow>
        )}
        {missingMasters && blockedAction.show && game && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={blockedBusy}
              onClick={async () => {
                setBlockedBusy(true);
                try {
                  const r = await disableBlockedPlugins(
                    game.appId,
                    game.installDirName,
                    game.pluginsTxtSubpath ?? "",
                    game.pluginsTxtStyle ?? "starred",
                    game.nexusDomain
                  );
                  if (r.ok) {
                    toaster.toast({
                      title: `${r.disabled ?? 0} mod${
                        (r.disabled ?? 0) === 1 ? "" : "s"
                      } switched off`,
                      body: (r.names ?? []).slice(0, 3).join(", "),
                    });
                    refresh();
                  } else {
                    toaster.toast({
                      title: "Could not switch them off",
                      body: r.error ?? "",
                    });
                  }
                } finally {
                  setBlockedBusy(false);
                }
              }}
            >
              {blockedBusy ? "Switching off…" : blockedAction.label}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {slots.level !== "ok" && (
          <PanelSectionRow>
            <Field
              label={
                slots.level === "over"
                  ? "⚠ Too many mods for the game"
                  : "Close to the game's mod limit"
              }
              childrenLayout="below"
            >
              {slots.message}
            </Field>
          </PanelSectionRow>
        )}
      {loadOrderIssue && installed && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              label={`${game.displayName} won't start?`}
              disabled={loadOrderBusy}
              description={loadOrderIssue}
              onClick={async () => {
                setLoadOrderBusy(true);
                try {
                  const r = await fixLoadOrder(
                    game.appId,
                    game.installDirName,
                    game.pluginsTxtSubpath!,
                    game.pluginsTxtStyle ?? "starred",
                    game.nexusDomain
                  );
                  toaster.toast(
                    r.ok
                      ? {
                          title: "Load order fixed",
                          body: [
                            (r.enabled_masters ?? 0) > 0 &&
                              `${r.enabled_masters} mod${
                                r.enabled_masters === 1 ? "" : "s"
                              } switched back on`,
                            (r.violations_before ?? 0) > 0 &&
                              `${(r.violations_before ?? 0).toLocaleString()} reordered`,
                          ]
                            .filter(Boolean)
                            .join(", ") || "Nothing needed changing",
                          duration: 10000,
                        }
                      : { title: "Could not fix it", body: r.error ?? "" }
                  );
                  const s = await getLoadOrderState(
                    game.appId,
                    game.installDirName,
                    game.pluginsTxtSubpath!,
                    game.pluginsTxtStyle ?? "starred",
                    game.nexusDomain
                  );
                  setLoadOrder(
                    s.ok && s.supported
                      ? {
                          total: s.total ?? 0,
                          violations: s.violations ?? 0,
                          disabledMasters: s.disabled_masters ?? 0,
                          examples: s.examples ?? [],
                          fullSlots: s.full_slots ?? 0,
                          fullSlotLimit: s.full_slot_limit ?? 254,
                          lightSlots: s.light_slots ?? 0,
                          lightSlotLimit: s.light_slot_limit ?? 4096,
                          missingMasters: s.missing_masters ?? [],
                          blockedPlugins: s.blocked_plugins ?? 0,
                          ghostPlugins: s.ghost_plugins ?? 0,
                          ghostExamples: s.ghost_examples ?? [],
                        }
                      : undefined
                  );
                } finally {
                  setLoadOrderBusy(false);
                }
              }}
            >
              {loadOrderBusy ? "Fixing…" : "Fix load order"}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {/* A plugin the extender loaded happily can still crash the game
            later, which leaves nothing in the extender's log - so this
            reads the crash log instead. Only the frame nearest the crash
            is offered: several mod DLLs can sit on one stack, and skipping
            all of them would take out mods that were working fine. */}
        {suspect && installed && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              label={`${game.displayName} crashed last time`}
              disabled={seBusy}
              description={
                `${suspect.name.replace(/\.dll$/i, "")} was ${
                  suspect.frame === 0
                    ? "the code running when it crashed"
                    : "on the stack when it crashed"
                }, so it's the most likely cause${
                  suspect.probable ? "" : " (a weaker match)"
                }. Skipping sets it aside so you can get back in — it's ` +
                "renamed, not deleted, and the crash may still turn out to be something else."
              }
              onClick={async () => {
                setSeBusy(true);
                try {
                  const r = await setScriptExtenderPlugins(
                    game.installDirName,
                    sePlugins!.dir,
                    [suspect.name],
                    false
                  );
                  toaster.toast(
                    r.ok
                      ? {
                          title: `Skipped ${suspect.name.replace(/\.dll$/i, "")}`,
                          body: "Launch again — if it still crashes, the next suspect will show up here",
                          duration: 10000,
                        }
                      : { title: "Could not skip it", body: r.error ?? "" }
                  );
                  refresh();
                } finally {
                  setSeBusy(false);
                }
              }}
            >
              {seBusy
                ? "Skipping…"
                : `Skip ${suspect.name.replace(/\.dll$/i, "")}`}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {(sePlugins?.parked.length ?? 0) > 0 && installed && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={seBusy}
              description={`${sePlugins!.parked
                .slice(0, 3)
                .map((n) => n.replace(/\.dll$/i, ""))
                .join(", ")}${
                sePlugins!.parked.length > 3
                  ? ` and ${sePlugins!.parked.length - 3} more`
                  : ""
              } are set aside. Bring them back if their authors have since updated them.`}
              onClick={async () => {
                setSeBusy(true);
                try {
                  const r = await setScriptExtenderPlugins(
                    game.installDirName,
                    sePlugins!.dir,
                    sePlugins!.parked.map((n) => `${n}`),
                    true
                  );
                  toaster.toast({
                    title: r.ok
                      ? `Restored ${r.changed ?? 0} mod plugin${
                          (r.changed ?? 0) === 1 ? "" : "s"
                        }`
                      : "Could not restore them",
                    body: r.ok ? "They'll be tried again next launch" : r.error ?? "",
                  });
                  refresh();
                } finally {
                  setSeBusy(false);
                }
              }}
            >
              Restore {sePlugins!.parked.length} skipped plugin
              {sePlugins!.parked.length > 1 ? "s" : ""}
            </ButtonItem>
          </PanelSectionRow>
        )}
        </>
      )}
    </PanelSection>
  );
}

function ResetSection() {
  const { game } = resolveGameContext();
  const [installed, setInstalled] = useState(false);

  useEffect(() => {
    setInstalled(false);
    if (!game) return;
    getGameStatus(game.installDirName, game.modsSubdir, "").then((s) =>
      setInstalled(Boolean(s.installed))
    );
  }, [game?.appId]);

  if (!game || !showResetRow(installed)) return null;
  return (
    <PanelSection>
      <ResetGameRow game={game} />
    </PanelSection>
  );
}

function SavesSection() {
  const app = Router.MainRunningApp;
  const { game } = resolveGameContext();

  const [status, setStatus] = useState<SaveStatus | undefined>();
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const refresh = () => {
    if (game?.moddedSaveWarning) {
      getSaveStatus(game.appId, game.processName).then(setStatus);
    }
  };
  useEffect(refresh, [game?.appId]);

  if (!game || !game.moddedSaveWarning || !status?.ok || !status.active_account)
    return null;

  const account = status.accounts?.find(
    (a) => a.account_id === status.active_account
  );
  const gameRunning =
    Boolean(status.game_running) || (app !== undefined && Number(app.appid) === game.appId);

  const onCopy = () =>
    showModal(
      <ConfirmModal
        strTitle={`Copy vanilla save to modded — ${game.displayName}`}
        strDescription={
          `Copies your unmodded ${game.displayName} progress (unlocks, stats, ` +
          `run history) into the modded save for Steam account ` +
          `${status.active_account}. The current modded save is backed up ` +
          `first. One-way only: modded progress can't safely go back to ` +
          `vanilla. If Steam shows a cloud sync conflict afterwards, choose ` +
          `"Upload local".`
        }
        strOKButtonText="Copy save"
        bDestructiveWarning={true}
        onOK={async () => {
          setBusy(true);
          try {
            const result = await copySavesToModded(
              game.appId,
              status.active_account!,
              game.processName
            );
            toaster.toast(
              result.ok
                ? {
                    title: "Save copied",
                    body: `Vanilla progress is now available in modded play${
                      result.backup ? " (previous modded save backed up)" : ""
                    }`,
                  }
                : { title: "Copy failed", body: result.error ?? "" }
            );
          } finally {
            setBusy(false);
            refresh();
          }
        }}
      />
    );

  return (
    <PanelSection title="Saves">
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          description={expanded ? undefined : "Modded-save info & tools"}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "▾ Hide save options" : "▸ Save options"}
        </ButtonItem>
      </PanelSectionRow>
      {expanded && (
        <>
          {game.moddedSaveWarning && (
            <PanelSectionRow>
              <div
                style={{
                  padding: "8px 10px",
                  margin: "12px 0 4px",
                  background: "rgba(255, 200, 60, 0.12)",
                  borderLeft: "3px solid #ffc83c",
                  borderRadius: "4px",
                  fontSize: "12px",
                  lineHeight: "1.45",
                }}
              >
                ⚠ {game.displayName} keeps separate save files for modded and
                unmodded play
              </div>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <Field label="Modded save">
              {account?.has_modded ? "Present" : "Not created yet"}
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={busy || gameRunning}
              description={
                gameRunning
                  ? "Close the game first"
                  : `Steam account ${status.active_account}`
              }
              onClick={onCopy}
            >
              Copy vanilla save → modded
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
}

function AccountSection() {
  const [auth, setAuth] = useState<AuthStatus | undefined>();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  // Adult content follows the account (site preference + platform age
  // verification) — no local toggle. undefined = not yet checked.
  const [gate, setGate] = useState<
    { show: boolean; adultPref: boolean; ageVerified: boolean } | undefined
  >();

  useEffect(() => {
    getAuthStatus().then(setAuth);
    refreshContentGate().then((r) => {
      if (r.ok)
        setGate({
          show: !!r.show_adult,
          adultPref: !!r.adult_pref,
          ageVerified: !!r.age_verified,
        });
    });
  }, []);

  const onSave = async () => {
    setBusy(true);
    try {
      const result = await setApiKey(draft);
      setAuth(result);
      if (result.ok) {
        setDraft("");
        const r = await refreshContentGate();
        if (r.ok)
          setGate({
            show: !!r.show_adult,
            adultPref: !!r.adult_pref,
            ageVerified: !!r.age_verified,
          });
      }
    } catch (e) {
      setAuth({ ok: false, error: String(e) });
    } finally {
      setBusy(false);
    }
  };

  const onSignOut = async () => {
    setBusy(true);
    try {
      setAuth(await setApiKey(""));
    } finally {
      setBusy(false);
    }
  };

  if (auth?.ok) {
    return (
      <PanelSection title="Nexus Mods Account">
        <PanelSectionRow>
          <Field label="Signed in">
            {auth.name} ({auth.is_premium ? "Premium" : "Free"})
          </Field>
        </PanelSectionRow>
        {/* Free accounts are not supported (decision 2026-07-24): mod
            downloads use the Premium download API. Browsing still works,
            so stay signed in but say why installs will fail. */}
        {!auth.is_premium && (
          <PanelSectionRow>
            <Field label="⚠ Premium required">
              Downloads need a Nexus Mods Premium account — free accounts
              can browse, but installs won't work on this device.
            </Field>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <Field label="Adult content">
            {gate === undefined
              ? "checking…"
              : gate.show
                ? "On — follows your Nexus Mods account (age verified ✓)"
                : gate.adultPref && !gate.ageVerified
                  ? "Off — age verification needed on nexusmods.com"
                  : "Off — enable it in your Nexus Mods account content settings"}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={onSignOut}>
            Sign out
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  return (
    <PanelSection title="Nexus Mods Account">
      <PanelSectionRow>
        <Field label="Status">
          {auth === undefined ? "checking…" : auth.error ?? "Not signed in"}
        </Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label="Personal API key"
          description="nexusmods.com → account settings → API keys. A Nexus Mods Premium account is required for downloads."
          bIsPassword={true}
          value={draft}
          onChange={(e) => setDraft(e?.target?.value ?? "")}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={busy || draft.trim().length === 0}
          onClick={onSave}
        >
          {busy ? "Validating…" : "Validate & save"}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

function LogModal({
  title,
  text,
  closeModal,
}: {
  title: string;
  text: string;
  closeModal?: () => void;
}) {
  return (
    <ModalRoot closeModal={closeModal} bAllowFullSize={true}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <pre
        style={{
          fontSize: "11px",
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
          maxHeight: "60vh",
          overflowY: "auto",
          background: "rgba(0,0,0,0.35)",
          padding: "8px",
          borderRadius: "4px",
        }}
      >
        {text}
      </pre>
    </ModalRoot>
  );
}

function DevSection() {
  // Dev tools work regardless of context; fall back to the default game's
  // log location when the context is unsupported.
  const game = resolveGameContext().game ?? DEFAULT_GAME;

  const [info, setInfo] = useState<BackendInfo | undefined>();
  const [error, setError] = useState<string | undefined>();

  const onPing = async () => {
    try {
      setError(undefined);
      setInfo(await ping(true));
    } catch (e) {
      setError(String(e));
    }
  };

  const showLog = async (which: "game" | "plugin") => {
    const debug = await getDebugInfo(
      game.logAdapter?.kind === "godot" ? game.logAdapter.userDirName : "",
      game.logAdapter?.kind === "smapi" ? game.logAdapter.configDirName : ""
    );
    if (!debug.ok) {
      toaster.toast({ title: "Debug info failed", body: debug.error ?? "" });
      return;
    }
    if (which === "game") {
      showModal(
        <LogModal
          title={`${game.displayName} — mod loader log`}
          text={debug.game_log_mod_lines ?? "(empty)"}
        />
      );
    } else {
      showModal(
        <LogModal title="Plugin backend log" text={debug.plugin_log ?? "(empty)"} />
      );
    }
  };

  return (
    <PanelSection title="Developer">
      {game.logAdapter && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="What the game's mod loader reported last run"
            onClick={() => showLog("game")}
          >
            Game mod log
          </ButtonItem>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => showLog("plugin")}>
          Plugin log
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onPing}>
          Ping backend
        </ButtonItem>
      </PanelSectionRow>
      {/* NXM relay spike rows removed 2026-07-24: free-user support is
          off the table (business-model decision - docs/free-user-design.md).
          Backend callables remain if that ever gets revisited. */}
      {error && (
        <PanelSectionRow>
          <Field label="Error">{error}</Field>
        </PanelSectionRow>
      )}
      {info && (
        <PanelSectionRow>
          <Field label="Backend">
            {info.plugin_name} v{info.plugin_version} on Decky {info.decky_version}
          </Field>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        {/* Diagnostic for viewed-game detection - shows what route Steam
            reports. Remove once detection is confirmed on real hardware. */}
        <Field label="Route">{getMainWindowPath() ?? "(unavailable)"}</Field>
      </PanelSectionRow>
    </PanelSection>
  );
}

/** QAM shortcut to the full-screen Downloads page, with a live activity
 * indicator. The heavy list lives on the page - the QAM stays lean. */
function DownloadsButton() {
  const [, force] = useState(0);
  useEffect(() => {
    const un1 = subscribeDownloads(() => force((n) => n + 1));
    const un2 = subscribeCollectionRun(() => force((n) => n + 1));
    return () => {
      un1();
      un2();
    };
  }, []);
  const active = getDownloads().length;
  const run = getCollectionRun();
  // The button itself is the progress bar: orange fills left-to-right
  // with the aggregate percent (all active downloads averaged; during a
  // collection run, finished mods blend with the live one).
  const pct = getAggregateDownloadPercent(run);
  const label = run?.running
    ? `Downloads · collection ${run.finished}/${run.total}`
    : active > 0
    ? `Downloads · ${active} active`
    : "Downloads";
  return (
    <PanelSectionRow>
      {/* The inline fill gradient overrode Steam's hover/focus style,
          making the button read as unclickable mid-download - the class
          keeps the fill AND brightens with an inset ring on focus. */}
      <style>{`
        .nexus-dl-fill {
          background: linear-gradient(90deg, rgba(218,142,53,0.55) var(--dl-pct), rgba(255,255,255,0.08) var(--dl-pct)) !important;
          color: #fff !important;
          transition: background 0.3s linear;
        }
        .nexus-dl-fill:hover,
        .nexus-dl-fill.gpfocus,
        .nexus-dl-fill.gpfocuswithin {
          background: linear-gradient(90deg, rgba(230,164,90,0.8) var(--dl-pct), rgba(255,255,255,0.16) var(--dl-pct)) !important;
          box-shadow: inset 0 0 0 2px #fff;
        }
      `}</style>
      <DialogButton
        className={pct !== undefined ? "nexus-dl-fill" : undefined}
        style={{
          width: "100%",
          ...(pct !== undefined
            ? ({ "--dl-pct": `${pct}%` } as React.CSSProperties)
            : {}),
        }}
        onClick={() => {
          Router.CloseSideMenus();
          resetTabStack();
          pushOurPage(DOWNLOADS_ROUTE);
        }}
      >
        {label}
      </DialogButton>
    </PanelSectionRow>
  );
}

/** QAM shortcut to the full-screen Updates page, with a pending count. */
function UpdatesButton({ scopedGame }: { scopedGame?: SupportedGame }) {
  const [count, setCount] = useState<number | undefined>();
  useEffect(() => {
    let stale = false;
    setCount(undefined);
    scanUpdates(scopedGame).then((found) => {
      if (!stale) setCount(found.length);
    });
    return () => {
      stale = true;
    };
  }, [scopedGame?.appId]);
  return (
    <PanelSectionRow>
      <DialogButton
        style={{ width: "100%", marginBottom: "8px" }}
        onClick={() => {
          Router.CloseSideMenus();
          resetTabStack();
              pushOurPage(UPDATES_ROUTE);
        }}
      >
        {count ? `⬆ Updates · ${count} available` : "Updates"}
      </DialogButton>
    </PanelSectionRow>
  );
}

/** Build identifier so QA always knows which version is on the device. */
function VersionBadge() {
  const [version, setVersion] = useState<string | undefined>();
  useEffect(() => {
    ping().then((r) => setVersion(r.plugin_version)).catch(() => {});
  }, []);
  if (!version) return null;
  return (
    <div
      style={{
        textAlign: "right",
        fontSize: "11px",
        opacity: 0.5,
        padding: "0 16px",
      }}
    >
      v{version}
    </div>
  );
}

/** The QAM restores its last scroll/focus position, so the panel could
 * open scrolled to the bottom - walk the scroll ancestors back to the
 * top on every mount (same trick as the FOMOD wizard's step reset). */
function ScrollToTopOnMount() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let el: HTMLElement | null = ref.current;
    while (el) {
      if (el.scrollTop) el.scrollTop = 0;
      el = el.parentElement;
    }
  }, []);
  return <div ref={ref} />;
}

function Content() {
  const ctx = resolveGameContext();
  return (
    <>
      <ScrollToTopOnMount />
      <VersionBadge />
      <PanelSection>
        <UpdatesButton scopedGame={ctx.game} />
        <DownloadsButton />
      </PanelSection>
      <CurrentGameSection />
      {ctx.game ? (
        <>
          <InstalledModsSection />
          <SavesSection />
          <TroubleshootingSection />
          <ResetSection />
        </>
      ) : (
        <AllInstalledModsSection />
      )}
      <PanelSection>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="Download pipeline, bandwidth and disk safety"
            onClick={() => {
              resetTabStack();
              pushOurPage(SETTINGS_ROUTE);
              Navigation.CloseSideMenus();
            }}
          >
            ⚙ Plugin Settings
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      <AccountSection />
      <DevSection />
    </>
  );
}

export default definePlugin(() => {
  console.log("Nexus Mods plugin initializing");

  routerHook.addRoute(BROWSE_ROUTE, BrowsePage, { exact: true });
  routerHook.addRoute(DETAIL_ROUTE, ModDetailPage, { exact: true });
  routerHook.addRoute(COLLECTION_ROUTE, CollectionPage, { exact: true });
  routerHook.addRoute(DOWNLOADS_ROUTE, DownloadsPage, { exact: true });
  routerHook.addRoute(HEALTH_ROUTE, HealthCheckPage, { exact: true });
  routerHook.addRoute(UPDATES_ROUTE, UpdatesPage, { exact: true });
  routerHook.addRoute(MANAGER_ROUTE, ManagerPage, { exact: true });
  routerHook.addRoute(SETTINGS_ROUTE, SettingsPage, { exact: true });

  // Feed the QAM Downloads section from anywhere in the UI.
  const progressListener = addEventListener<[p: InstallProgress]>(
    "install_progress",
    (p) =>
      updateDownload(
        p.mod_id,
        p.phase,
        p.percent,
        p.bytes_done,
        p.bytes_total,
        p.bps
      )
  );

  // The "this takes minutes, don't quit" notice has to hang off the game
  // actually STARTING, not off our Launch button - almost nobody launches
  // from the panel. Steam's own library button is the normal way in, and
  // that path never touched our code.
  const lifetime = (
    (window as any).SteamClient as {
      GameSessions?: {
        RegisterForAppLifetimeNotifications?: (
          cb: (e: { unAppID: number; bRunning: boolean }) => void
        ) => { unregister: () => void };
      };
    }
  ).GameSessions?.RegisterForAppLifetimeNotifications?.((e) => {
    const game = getSupportedGame(e.unAppID);
    if (!game) return;
    if (!e.bRunning) {
      // Bethesda games rewrite Plugins.txt themselves: Skyrim switched
      // two deliberately-skipped mods back on mid-run and crashed on the
      // next launch. Re-asserting on exit means whatever the game did to
      // the file, the next launch starts from our state rather than its.
      if (game.pluginsTxtSubpath) {
        enforceSkips(
          game.appId,
          game.installDirName,
          game.pluginsTxtSubpath,
          game.pluginsTxtStyle ?? "starred",
          game.nexusDomain
        );
      }
      return;
    }
    getInstalledCount(game.nexusDomain).then((r) => {
      const wait = launchWaitNotice(r.ok ? r.mods ?? 0 : 0);
      if (wait) {
        toaster.toast({
          title: `Starting ${game.displayName}`,
          body: wait,
          duration: 15000,
        });
      }
    });
  });

  // Verifies the backend -> frontend event channel via the Ping button.
  const listener = addEventListener<[message: string]>(
    "backend_event",
    (message) => {
      toaster.toast({ title: "Nexus Mods", body: `Backend says: ${message}` });
    }
  );

  return {
    name: "Nexus Mods",
    titleView: <div className={staticClasses.Title}>Nexus Mods</div>,
    content: <Content />,
    icon: <FaPuzzlePiece />,
    onDismount() {
      routerHook.removeRoute(DETAIL_ROUTE);
      routerHook.removeRoute(BROWSE_ROUTE);
      routerHook.removeRoute(COLLECTION_ROUTE);
      routerHook.removeRoute(DOWNLOADS_ROUTE);
      routerHook.removeRoute(UPDATES_ROUTE);
      routerHook.removeRoute(MANAGER_ROUTE);
      routerHook.removeRoute(SETTINGS_ROUTE);
      removeEventListener("backend_event", listener);
      removeEventListener("install_progress", progressListener);
      lifetime?.unregister();
    },
  };
});
