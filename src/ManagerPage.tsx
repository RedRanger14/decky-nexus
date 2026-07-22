// "My Mods": every installed mod across every supported game, grouped by
// game with enable/disable and uninstall - the full-screen mod manager.
// (Load-order editing is a future addition.)
import {
  ConfirmModal,
  DialogButton,
  Focusable,
  Navigation,
  QuickAccessTab,
  ScrollPanelGroup,
  showModal,
} from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useState } from "react";

import {
  InstalledMod,
  getInstalledMods,
  setModEnabled,
  uninstallMod,
} from "./api";
import { ALL_GAMES, SupportedGame, modeParams } from "./games";
import { TabBar, handleTabButtons } from "./Tabs";

const Scroller: any = ScrollPanelGroup;

interface GameMods {
  game: SupportedGame;
  mods: InstalledMod[];
}

export function ManagerPage() {
  const [groups, setGroups] = useState<GameMods[] | undefined>();
  const [busyKey, setBusyKey] = useState<string | undefined>();

  const refresh = async () => {
    const found: GameMods[] = [];
    for (const game of ALL_GAMES) {
      const r = await getInstalledMods(
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        ...modeParams(game),
        game.protectedModFolders ?? []
      );
      const mods = r.mods ?? [];
      if (mods.length > 0) found.push({ game, mods });
    }
    setGroups(found);
  };

  useEffect(() => {
    refresh();
  }, []);

  const toggle = async (game: SupportedGame, mod: InstalledMod) => {
    const key = `${game.appId}:${mod.folder}`;
    setBusyKey(key);
    try {
      const result = await setModEnabled(
        game.installDirName,
        game.modsSubdir,
        mod.folder,
        !mod.enabled,
        game.installMode ?? "folder",
        game.nexusDomain,
        game.appId,
        game.pluginsTxtSubpath ?? "",
        game.pluginsTxtStyle ?? "starred"
      );
      if (!result.ok) {
        toaster.toast({ title: "Could not toggle", body: result.error ?? "" });
      }
    } finally {
      setBusyKey(undefined);
      refresh();
    }
  };

  const remove = (game: SupportedGame, mod: InstalledMod) => {
    showModal(
      <ConfirmModal
        strTitle={`Uninstall ${mod.name ?? mod.folder}?`}
        strDescription="You can reinstall it from the store at any time."
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
              ? { title: "Uninstalled", body: mod.name ?? mod.folder }
              : { title: "Uninstall failed", body: result.error ?? "" }
          );
          refresh();
        }}
      />
    );
  };

  return (
    <Focusable
      autoFocus={true}
      noFocusRing={true}
      onActivate={() => {}}
      onButtonDown={handleTabButtons("manager")}
      onCancel={() => {
        Navigation.NavigateBack();
        Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);
      }}
      style={{ marginTop: "40px", height: "calc(100% - 40px)" }}
    >
      <Scroller
        focusable={false}
        style={{ height: "100%", overflowY: "auto", padding: "0 24px 80px" }}
      >
        <TabBar currentId="manager" />
        <h2 style={{ margin: "6px 0 10px" }}>My Mods</h2>

        {groups === undefined && (
          <div style={{ opacity: 0.8 }}>Reading your games…</div>
        )}
        {groups !== undefined && groups.length === 0 && (
          <div style={{ opacity: 0.8 }}>
            Nothing installed yet - the Store tab is where it starts.
          </div>
        )}

        {(groups ?? []).map(({ game, mods }) => (
          <div key={game.appId} style={{ marginBottom: "16px" }}>
            <div
              style={{
                fontSize: "14px",
                fontWeight: 700,
                margin: "6px 0",
                opacity: 0.9,
              }}
            >
              {game.displayName}{" "}
              <span style={{ opacity: 0.55, fontWeight: 400 }}>
                · {mods.length} mod{mods.length === 1 ? "" : "s"}
              </span>
            </div>
            <Focusable
              style={{ display: "flex", flexDirection: "column", gap: "4px" }}
            >
              {mods.map((mod) => {
                const key = `${game.appId}:${mod.folder}`;
                const busy = busyKey === key;
                return (
                  <Focusable
                    key={key}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      padding: "7px 12px",
                      background: "rgba(255,255,255,0.05)",
                      borderRadius: "4px",
                      opacity: mod.enabled ? 1 : 0.6,
                    }}
                  >
                    <div style={{ flexGrow: 1, minWidth: 0 }}>
                      <div
                        style={{
                          fontSize: "13.5px",
                          fontWeight: 600,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {mod.name ?? mod.folder}
                      </div>
                      <div style={{ fontSize: "11.5px", opacity: 0.6 }}>
                        {mod.version ? `v${mod.version}` : "untracked"}
                        {mod.enabled ? "" : " · disabled"}
                        {mod.togglable === false ? " · always active" : ""}
                      </div>
                    </div>
                    {mod.togglable !== false && (
                      <DialogButton
                        disabled={busy}
                        onClick={() => toggle(game, mod)}
                        style={{
                          minWidth: "0",
                          width: "auto",
                          padding: "6px 14px",
                          fontSize: "12.5px",
                          flexShrink: 0,
                        }}
                      >
                        {busy ? "…" : mod.enabled ? "Disable" : "Enable"}
                      </DialogButton>
                    )}
                    <DialogButton
                      disabled={busy}
                      onClick={() => remove(game, mod)}
                      style={{
                        minWidth: "0",
                        width: "auto",
                        padding: "6px 14px",
                        fontSize: "12.5px",
                        flexShrink: 0,
                        color: "#ff8a8a",
                      }}
                    >
                      Uninstall
                    </DialogButton>
                  </Focusable>
                );
              })}
            </Focusable>
          </div>
        ))}
      </Scroller>
    </Focusable>
  );
}
