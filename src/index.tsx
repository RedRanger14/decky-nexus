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
import { Fragment, useEffect, useState } from "react";
import { FaEye, FaPuzzlePiece } from "react-icons/fa";

import {
  AuthStatus,
  GameStatus,
  InstalledMod,
  ModLoadState,
  SaveStatus,
  UpdateInfo,
  applyDisplayFix,
  checkGameFile,
  checkUpdates,
  copySavesToModded,
  getAuthStatus,
  getDebugInfo,
  getFrameworkSetup,
  getGameStatus,
  getInstalledMods,
  getModDetails,
  getModLoadStatus,
  getNxmQueue,
  getSaveStatus,
  getSmapiLoadStatus,
  installFramework,
  registerNxmHandler,
  markLaunchOptionsSet,
  setAllModsEnabled,
  setFrameworkEnabled,
  setApiKey,
  setModEnabled,
  uninstallAllMods,
  uninstallMod,
} from "./api";
import {
  ALL_GAMES,
  DEFAULT_GAME,
  getSupportedGame,
  modeParams,
  noteActiveGame,
  SupportedGame,
} from "./games";
import {
  getAppDisplayName,
  getMainWindowPath,
  getRunningAppIds,
  getViewedLibraryAppId,
  isGameRunning,
  restartGame,
  setCompatTool,
  setLaunchOptions,
} from "./steam";
import { setSelectedMod } from "./state";
import { PRIMARY_BUTTON_CLASS, PRIMARY_BUTTON_CSS } from "./theme";

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
  return { neutral: true };
}
import { BrowsePage } from "./BrowsePage";
import { ModDetailPage } from "./ModDetailPage";

/** QAM row shortcut: jump from an installed mod straight to its detail page
 * (to re-check requirements, files, or updates). */
async function openInstalledModDetail(game: SupportedGame, mod: InstalledMod) {
  if (!mod.mod_id) return;
  const result = await getModDetails(game.nexusDomain, mod.mod_id);
  if (result.ok && result.mod) {
    setSelectedMod({ game, mod: result.mod });
    Router.CloseSideMenus();
    Navigation.Navigate(DETAIL_ROUTE);
  } else {
    toaster.toast({
      title: "Could not open mod",
      body: result.error ?? mod.name ?? mod.folder,
    });
  }
}

const BROWSE_ROUTE = "/nexus-mods";
const DETAIL_ROUTE = "/nexus-mods/mod";

interface BackendInfo {
  user: string;
  home: string;
  plugin_name: string;
  plugin_version: string;
  decky_version: string;
}

const ping = callable<[], BackendInfo>("ping");

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
  options,
  onDone,
  closeModal,
}: {
  frameworkName: string;
  gameName: string;
  appId: number;
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
        onClick={() => {
          const ok = setLaunchOptions(appId, options);
          toaster.toast(
            ok
              ? { title: "Launch options set", body: `${gameName} will start through ${frameworkName}` }
              : { title: "Could not set launch options", body: "Use Copy instead and set them manually" }
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

function CurrentGameSection() {
  const { game, unsupportedName, multipleNames } = resolveGameContext();
  const gameIsRunning = game ? isGameRunning(game.appId) : false;

  const [status, setStatus] = useState<GameStatus | undefined>();
  const [frameworkBusy, setFrameworkBusy] = useState(false);
  const [launchOptionsSet, setLaunchOptionsSet] = useState(false);
  const [nativeBuild, setNativeBuild] = useState(false);

  const refreshStatus = () => {
    if (game) {
      getGameStatus(
        game.installDirName,
        game.modsSubdir,
        game.framework?.detectFile ?? ""
      ).then(setStatus);
      if (game.protonRequired) {
        checkGameFile(
          game.installDirName,
          game.protonRequired.nativeMarker
        ).then((r) => setNativeBuild(Boolean(r.ok && r.exists)));
      }
      if (game.framework) {
        getFrameworkSetup(game.nexusDomain).then((r) =>
          setLaunchOptionsSet(Boolean(r.launch_options_set))
        );
      }
    }
  };

  const markDone = () => {
    if (game) {
      markLaunchOptionsSet(game.nexusDomain).then(() => setLaunchOptionsSet(true));
    }
  };

  const openLaunchOptionsModal = () => {
    if (!game?.framework?.launchOptionsTemplate || !status) return;
    showModal(
      <LaunchOptionsModal
        frameworkName={game.framework.name}
        gameName={game.displayName}
        appId={game.appId}
        options={game.framework.launchOptionsTemplate.replace(
          "{install_path}",
          status.install_path
        )}
        onDone={markDone}
      />
    );
  };

  useEffect(() => {
    setStatus(undefined);
    refreshStatus();
  }, [game?.appId]);

  const onInstallFramework = async () => {
    if (!game?.framework?.nexusModId) return;
    setFrameworkBusy(true);
    try {
      const result = await installFramework(
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
      if (result.ok && result.install_path) {
        toaster.toast({
          title: `${game.framework.name} installed`,
          body: "Step 2: set the launch command",
        });
        if (game.framework.launchOptionsTemplate) {
          showModal(
            <LaunchOptionsModal
              frameworkName={game.framework.name}
              gameName={game.displayName}
              appId={game.appId}
              options={game.framework.launchOptionsTemplate.replace(
                "{install_path}",
                result.install_path
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
              Navigation.Navigate(BROWSE_ROUTE);
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
            <PanelSectionRow>
              <Field label="⚠ Game won't start">
                {game.displayName} is set to launch through{" "}
                {game.framework.name}, which isn't installed. Install{" "}
                {game.framework.name} below (or clear the game's launch
                options to play without mods).
              </Field>
            </PanelSectionRow>
          )}

          <PanelSectionRow>
            {status.framework_installed ? (
              <Field label="Step 1">{game.framework.name} installed ✓</Field>
            ) : (
              <ButtonItem
                label="Step 1"
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
          {game.framework.launchOptionsTemplate && (
            <PanelSectionRow>
              {launchOptionsSet ? (
                <Field label="Step 2">Launch command set ✓</Field>
              ) : (
                <ButtonItem
                  label="Step 2"
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
            <Field label="Step 3" childrenLayout="below">
              <OrangeActionButton
                onClick={() => {
                  Navigation.Navigate(BROWSE_ROUTE);
                  Navigation.CloseSideMenus();
                }}
              >
                Open Mod Browser
              </OrangeActionButton>
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              label="Step 4"
              layout="below"
              description="Restarts are required for mods to take effect"
              onClick={() => restartGame(game.appId)}
            >
              {gameIsRunning
                ? `Restart ${game.displayName}`
                : `Launch ${game.displayName}`}
            </ButtonItem>
          </PanelSectionRow>
        </>
      ) : (
        <>
          <PanelSectionRow>
            <OrangeActionButton
              onClick={() => {
                Navigation.Navigate(BROWSE_ROUTE);
                Navigation.CloseSideMenus();
              }}
            >
              Open Mod Browser
            </OrangeActionButton>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              description="Restarts are required for mods to take effect"
              onClick={() => restartGame(game.appId)}
            >
              {gameIsRunning
                ? `Restart ${game.displayName}`
                : `Launch ${game.displayName}`}
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
}

function UninstallPickerModal({
  mods,
  game,
  closeModal,
  onDone,
}: {
  mods: InstalledMod[];
  game: SupportedGame;
  closeModal?: () => void;
  onDone: () => void;
}) {
  const protectedFolders = game.protectedModFolders ?? [];
  // The picker stays open across uninstalls so several mods can be removed
  // in one visit; it closes itself when nothing is left.
  const [list, setList] = useState(mods);

  useEffect(() => {
    if (list.length === 0) closeModal?.();
  }, [list]);

  const confirmUninstall = (mod: InstalledMod) => {
    showModal(
      <ConfirmModal
        strTitle={`Uninstall ${mod.name ?? mod.folder}?`}
        strDescription={`This deletes the "${mod.folder}" folder from the game. You can reinstall it from Nexus Mods at any time.`}
        strOKButtonText="Uninstall"
        bDestructiveWarning={true}
        onOK={async () => {
          const result = await uninstallMod(
            game.nexusDomain,
            game.installDirName,
            game.modsSubdir,
            mod.folder,
            ...modeParams(game)
          );
          toaster.toast(
            result.ok
              ? { title: "Mod uninstalled", body: mod.name ?? mod.folder }
              : { title: "Uninstall failed", body: result.error ?? "" }
          );
          onDone();
          if (result.ok) {
            setList((prev) => prev.filter((m) => m.folder !== mod.folder));
          }
        }}
      />
    );
  };

  const confirmUninstallAll = () => {
    showModal(
      <ConfirmModal
        strTitle="Uninstall ALL mods?"
        strDescription={
          `Removes every mod folder for this game` +
          (protectedFolders.length
            ? `, except framework components (${protectedFolders.join(", ")})`
            : "") +
          `. Mods can be reinstalled from Nexus Mods at any time.`
        }
        strOKButtonText="Uninstall all"
        bDestructiveWarning={true}
        onOK={async () => {
          const result = await uninstallAllMods(
            game.nexusDomain,
            game.installDirName,
            game.modsSubdir,
            protectedFolders,
            ...modeParams(game)
          );
          toaster.toast(
            result.ok
              ? {
                  title: "All mods uninstalled",
                  body: `${result.removed} removed${
                    result.kept?.length ? `, kept: ${result.kept.join(", ")}` : ""
                  }`,
                }
              : { title: "Uninstall all failed", body: result.error ?? "" }
          );
          onDone();
          closeModal?.();
        }}
      />
    );
  };

  return (
    <ModalRoot closeModal={closeModal}>
      <h3 style={{ marginTop: 0 }}>Uninstall a mod</h3>
      {list.map((mod) => (
        <ButtonItem
          key={mod.folder}
          layout="below"
          description={mod.tracked ? `v${mod.version}` : "not installed by this plugin"}
          onClick={() => confirmUninstall(mod)}
        >
          {mod.name ?? mod.folder}
        </ButtonItem>
      ))}
      <ButtonItem
        layout="below"
        description={
          protectedFolders.length
            ? `Keeps framework components: ${protectedFolders.join(", ")}`
            : undefined
        }
        onClick={confirmUninstallAll}
      >
        Uninstall all mods…
      </ButtonItem>
    </ModalRoot>
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

  const refresh = () => {
    if (game) {
      getInstalledMods(
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        ...modeParams(game),
        game.protectedModFolders ?? []
      ).then((r) => setMods(r.ok ? r.mods : []));
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
      checkUpdates(game.nexusDomain).then((r) =>
        setUpdates(r.ok ? r.updates : undefined)
      );
      if (game.framework) {
        getGameStatus(
          game.installDirName,
          game.modsSubdir,
          game.framework.detectFile
        ).then(setFwStatus);
        getFrameworkSetup(game.nexusDomain).then((r) =>
          setFwEnabled(r.enabled !== false)
        );
      }
    }
  };

  useEffect(refresh, [game?.appId]);

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
      await markLaunchOptionsSet(game.nexusDomain);
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

  const failures = (mods ?? [])
    .filter((m) => m.enabled && loadStateFor(m.folder)?.state === "error")
    .map((m) => ({
      name: m.name ?? m.folder,
      detail: loadStateFor(m.folder)?.detail ?? "",
    }));

  if ((mods === undefined || mods.length === 0) && !showFrameworkRow) return null;

  const anyEnabled = (mods ?? []).some((m) => m.enabled);

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
      {(mods ?? []).map((mod) => {
        const load = mod.enabled ? loadStateFor(mod.folder) : undefined;
        const update = updates?.[mod.folder];
        const badge =
          (load === undefined
            ? ""
            : load.state === "loaded"
            ? " · loaded ✓"
            : " · failed to load ⚠") +
          (update?.update_available ? ` · ⬆ ${update.current} available` : "");
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
      {failures.length > 0 && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="Why, and what to try"
            onClick={() => showModal(<FailedModsModal failures={failures} />)}
          >
            ⚠ {failures.length} mod{failures.length > 1 ? "s" : ""} failed to
            load
          </ButtonItem>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={busyAll}
          onClick={() => onToggleAll(!anyEnabled)}
        >
          {anyEnabled ? "Disable all (play vanilla)" : "Enable all"}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={busyAll}
          onClick={() =>
            showModal(
              <UninstallPickerModal
                mods={mods ?? []}
                game={game}
                onDone={refresh}
              />
            )
          }
        >
          Uninstall a mod…
        </ButtonItem>
      </PanelSectionRow>
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

  useEffect(() => {
    getAuthStatus().then(setAuth);
  }, []);

  const onSave = async () => {
    setBusy(true);
    try {
      const result = await setApiKey(draft);
      setAuth(result);
      if (result.ok) setDraft("");
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
          description="nexusmods.com → account settings → API keys"
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
      setInfo(await ping());
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
      {/* Phase 1 spike for the free-user flow (docs/free-user-design.md):
          register the relay, click 'Mod Manager Download' on any mod page in
          the Gaming Mode browser, then check whether the queue caught it. */}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          description="Free-user spike: register the nxm:// relay handler"
          onClick={async () => {
            const result = await registerNxmHandler();
            toaster.toast(
              result.ok
                ? {
                    title: "NXM relay registered",
                    body: `tools: ${Object.entries(result.tools ?? {})
                      .map(([k, v]) => `${k}=${v ? "ok" : "missing"}`)
                      .join(", ")}`,
                  }
                : { title: "Relay registration failed", body: result.error ?? "" }
            );
          }}
        >
          NXM relay: register
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          description="Shows nxm:// links the relay has caught"
          onClick={async () => {
            const result = await getNxmQueue(false);
            if (!result.ok) {
              toaster.toast({ title: "Queue read failed", body: result.error ?? "" });
              return;
            }
            const text =
              (result.raw?.length ?? 0) === 0
                ? "(queue is empty - nothing dispatched yet)"
                : `Raw:\n${(result.raw ?? []).join("\n")}\n\nParsed:\n` +
                  JSON.stringify(result.entries, null, 2);
            showModal(<LogModal title="NXM relay queue" text={text} />);
          }}
        >
          NXM relay: check queue
        </ButtonItem>
      </PanelSectionRow>
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

function Content() {
  const ctx = resolveGameContext();
  return (
    <>
      <VersionBadge />
      <CurrentGameSection />
      {ctx.game ? (
        <>
          <InstalledModsSection />
          <SavesSection />
        </>
      ) : (
        <AllInstalledModsSection />
      )}
      <AccountSection />
      <DevSection />
    </>
  );
}

export default definePlugin(() => {
  console.log("Nexus Mods plugin initializing");

  routerHook.addRoute(BROWSE_ROUTE, BrowsePage, { exact: true });
  routerHook.addRoute(DETAIL_ROUTE, ModDetailPage, { exact: true });

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
      removeEventListener("backend_event", listener);
    },
  };
});
