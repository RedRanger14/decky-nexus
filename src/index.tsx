import {
  ButtonItem,
  ConfirmModal,
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
import { FaPuzzlePiece } from "react-icons/fa";

import {
  AuthStatus,
  GameStatus,
  InstalledMod,
  ModLoadState,
  SaveStatus,
  UpdateInfo,
  checkUpdates,
  copySavesToModded,
  getAuthStatus,
  getDebugInfo,
  getGameStatus,
  getInstalledMods,
  getModLoadStatus,
  getSaveStatus,
  installFramework,
  setAllModsEnabled,
  setApiKey,
  setModEnabled,
  uninstallMod,
} from "./api";
import {
  ALL_GAMES,
  DEFAULT_GAME,
  getSupportedGame,
  noteActiveGame,
  SupportedGame,
} from "./games";
import {
  getAppDisplayName,
  getMainWindowPath,
  getViewedLibraryAppId,
  isGameRunning,
  restartGame,
  setLaunchOptions,
} from "./steam";

interface GameContext {
  /** The game being managed; undefined outside a supported game's context. */
  game?: SupportedGame;
  /** Display name of the unsupported context game, when running/viewing one. */
  unsupportedName?: string;
  /** True on neutral ground (home screen etc.) - no game context at all. */
  neutral?: boolean;
}

/** Resolve which game the plugin is managing. The panel strictly follows
 * what the user is doing: a supported game's full sections appear only when
 * that game is running or its library page is on screen. Everything else is
 * either an "unsupported" message or the neutral all-games view. */
function resolveGameContext(): GameContext {
  const app = Router.MainRunningApp;
  const runningId = app ? Number(app.appid) : undefined;
  const running = getSupportedGame(runningId);
  if (running) {
    noteActiveGame(running.appId);
    return { game: running };
  }

  const viewedId = getViewedLibraryAppId();
  const viewed = getSupportedGame(viewedId);
  if (viewed) {
    noteActiveGame(viewed.appId);
    return { game: viewed };
  }

  if (app) return { unsupportedName: app.display_name };
  if (viewedId !== undefined) {
    return { unsupportedName: getAppDisplayName(viewedId) ?? "This game" };
  }
  return { neutral: true };
}
import { BrowsePage } from "./BrowsePage";
import { ModDetailPage } from "./ModDetailPage";

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

function CurrentGameSection() {
  const app = Router.MainRunningApp;
  const runningSupported = getSupportedGame(app ? Number(app.appid) : undefined);
  const { game, unsupportedName } = resolveGameContext();

  const [status, setStatus] = useState<GameStatus | undefined>();
  const [frameworkBusy, setFrameworkBusy] = useState(false);

  const refreshStatus = () => {
    if (game) {
      getGameStatus(
        game.installDirName,
        game.modsSubdir,
        game.framework?.detectFile ?? ""
      ).then(setStatus);
    }
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
        game.installDirName
      );
      if (result.ok && result.install_path) {
        let body = `${game.framework.name} installed.`;
        if (game.framework.launchOptionsTemplate) {
          const options = game.framework.launchOptionsTemplate.replace(
            "{install_path}",
            result.install_path
          );
          body += setLaunchOptions(game.appId, options)
            ? " Steam launch options set automatically."
            : ` Set the game's launch options to: ${options}`;
        }
        toaster.toast({ title: `${game.framework.name} ready`, body });
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
          <ButtonItem
            layout="below"
            onClick={() => {
              Navigation.Navigate(BROWSE_ROUTE);
              Navigation.CloseSideMenus();
            }}
          >
            Open Mod Browser
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  return (
    <PanelSection title="Current Game">
      <PanelSectionRow>
        <Field label="Game">
          {runningSupported ? game.displayName : `${game.displayName} · not running`}
        </Field>
      </PanelSectionRow>
      {status && !status.installed && (
        <PanelSectionRow>
          <Field label="Installed">Not found in main Steam library</Field>
        </PanelSectionRow>
      )}
      {game.framework &&
        status?.installed &&
        status.framework_installed === false && (
          <>
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
                ⚠ {game.framework.name} isn't installed — most{" "}
                {game.displayName} mods require it.
              </div>
            </PanelSectionRow>
            {game.framework.nexusModId && (
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  disabled={frameworkBusy}
                  description="Downloads from Nexus (author gets the credit) and sets up the game"
                  onClick={onInstallFramework}
                >
                  {frameworkBusy
                    ? `Installing ${game.framework.name}…`
                    : `Download ${game.framework.name}`}
                </ButtonItem>
              </PanelSectionRow>
            )}
          </>
        )}
      {game.framework && status?.framework_installed === true && (
        <PanelSectionRow>
          <Field label={game.framework.name}>Installed ✓</Field>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={() => {
            Navigation.Navigate(BROWSE_ROUTE);
            Navigation.CloseSideMenus();
          }}
        >
          Open Mod Browser
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

function UninstallPickerModal({
  mods,
  gameDomain,
  installDir,
  modsSubdir,
  closeModal,
  onDone,
}: {
  mods: InstalledMod[];
  gameDomain: string;
  installDir: string;
  modsSubdir: string;
  closeModal?: () => void;
  onDone: () => void;
}) {
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
        strDescription={`This deletes the "${mod.folder}" folder from the game. You can reinstall it from Nexus at any time.`}
        strOKButtonText="Uninstall"
        bDestructiveWarning={true}
        onOK={async () => {
          const result = await uninstallMod(
            gameDomain,
            installDir,
            modsSubdir,
            mod.folder
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
          (await getInstalledMods(g.nexusDomain, g.installDirName, g.modsSubdir))
            .mods ?? [],
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
        enabled
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
        RitsuLib) on the Linux build even when Nexus lists no requirements.
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

  const refresh = () => {
    if (game) {
      getInstalledMods(game.nexusDomain, game.installDirName, game.modsSubdir).then(
        (r) => setMods(r.ok ? r.mods : [])
      );
      if (game.godotUserDirName) {
        getModLoadStatus(game.godotUserDirName).then((r) =>
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
    }
  };

  useEffect(refresh, [game?.appId]);

  if (!game) return null;

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

  if (!game || mods === undefined || mods.length === 0) return null;

  const anyEnabled = mods.some((m) => m.enabled);

  const onToggle = async (mod: InstalledMod, enabled: boolean) => {
    setBusyFolder(mod.folder);
    try {
      const result = await setModEnabled(
        game.installDirName,
        game.modsSubdir,
        mod.folder,
        enabled
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
        enabled
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
      {mods.map((mod) => {
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
            <ToggleField
              label={mod.name ?? mod.folder}
              description={base + badge}
              checked={mod.enabled}
              disabled={busyFolder === mod.folder || busyAll}
              onChange={(checked: boolean) => {
                if (checked !== mod.enabled) onToggle(mod, checked);
              }}
            />
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
                mods={mods}
                gameDomain={game.nexusDomain}
                installDir={game.installDirName}
                modsSubdir={game.modsSubdir}
                onDone={refresh}
              />
            )
          }
        >
          Uninstall a mod…
        </ButtonItem>
      </PanelSectionRow>
      {isGameRunning(game.appId) && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="Mod changes apply on next game start"
            onClick={() => restartGame(game.appId)}
          >
            Restart {game.displayName}
          </ButtonItem>
        </PanelSectionRow>
      )}
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
      <PanelSection title="Nexus Account">
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
    <PanelSection title="Nexus Account">
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
    const debug = await getDebugInfo(game.godotUserDirName ?? "");
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
      {game.godotUserDirName && (
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

function Content() {
  const ctx = resolveGameContext();
  return (
    <>
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
