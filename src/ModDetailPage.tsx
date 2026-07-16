import {
  ButtonItem,
  ConfirmModal,
  Focusable,
  Navigation,
  showModal,
} from "@decky/ui";
import { addEventListener, removeEventListener, toaster } from "@decky/api";
import { useEffect, useState } from "react";

import {
  FilesResult,
  InstallProgress,
  InstalledMod,
  ModFile,
  ModRequirement,
  getInstalledMods,
  getModFiles,
  getModRequirements,
  installMod,
  uninstallMod,
} from "./api";
import { getCompatHint } from "./compat";
import { getSelectedMod } from "./state";
import { isGameRunning, restartGame } from "./steam";

function fmtSize(sizeKb: number): string {
  if (sizeKb >= 1024) return `${(sizeKb / 1024).toFixed(1)} MB`;
  return `${sizeKb} KB`;
}

export function ModDetailPage() {
  const sel = getSelectedMod();
  const [files, setFiles] = useState<FilesResult | undefined>();
  const [progress, setProgress] = useState<InstallProgress | undefined>();
  const [installingFileId, setInstallingFileId] = useState<number | undefined>();
  const [installedFileIds, setInstalledFileIds] = useState<Set<number>>(new Set());
  const [installedCopy, setInstalledCopy] = useState<InstalledMod | undefined>();
  const [requirements, setRequirements] = useState<ModRequirement[] | undefined>();

  const refreshInstalled = () => {
    if (sel) {
      getInstalledMods(
        sel.game.nexusDomain,
        sel.game.installDirName,
        sel.game.modsSubdir
      ).then((r) =>
        setInstalledCopy(r.mods?.find((m) => m.mod_id === sel.mod.modId))
      );
    }
  };

  useEffect(() => {
    if (sel) {
      getModFiles(sel.game.nexusDomain, sel.mod.modId).then(setFiles);
      getModRequirements(sel.game.nexusDomain, sel.mod.modId).then((r) =>
        setRequirements(r.ok ? r.requirements ?? [] : [])
      );
      refreshInstalled();
    }
    const listener = addEventListener<[p: InstallProgress]>(
      "install_progress",
      (p) => setProgress(p)
    );
    return () => removeEventListener("install_progress", listener);
  }, []);

  if (!sel) {
    return (
      <div style={{ marginTop: "40px", padding: "24px" }}>
        No mod selected.
      </div>
    );
  }
  const { game, mod } = sel;

  const onInstall = async (file: ModFile) => {
    setInstallingFileId(file.file_id);
    setProgress(undefined);
    try {
      const result = await installMod(
        game.nexusDomain,
        mod.modId,
        file.file_id,
        file.file_name,
        mod.name,
        file.version || mod.version,
        game.installDirName,
        game.modsSubdir
      );
      if (result.ok) {
        setInstalledFileIds((prev) => new Set(prev).add(file.file_id));
        refreshInstalled();
        toaster.toast({
          title: `${mod.name} installed`,
          body: isGameRunning(game.appId)
            ? `Tap here to restart ${game.displayName} and load it.`
            : `It will load next time ${game.displayName} starts.`,
          onClick: () => restartGame(game.appId),
        });
      } else {
        toaster.toast({ title: "Install failed", body: result.error ?? "Unknown error" });
      }
    } catch (e) {
      toaster.toast({ title: "Install failed", body: String(e) });
    } finally {
      setInstallingFileId(undefined);
      setProgress(undefined);
    }
  };

  const progressText =
    progress?.mod_id === mod.modId
      ? progress.phase === "downloading"
        ? `Downloading… ${progress.percent}%`
        : progress.phase === "extracting"
        ? "Extracting…"
        : "Installing…"
      : "Installing…";

  const heroUrl = mod.pictureUrl ?? mod.thumbnailUrl;

  return (
    <div
      style={{
        marginTop: "40px",
        height: "calc(100% - 40px)",
        overflowY: "auto",
        padding: "0 24px 24px",
      }}
    >
      <Focusable style={{ display: "flex", gap: "20px", padding: "12px 0" }}>
        {heroUrl && (
          <img
            src={heroUrl}
            alt={mod.name}
            style={{
              width: "38%",
              maxHeight: "230px",
              objectFit: "cover",
              borderRadius: "8px",
              flexShrink: 0,
            }}
          />
        )}
        <div style={{ minWidth: 0 }}>
          <h2 style={{ margin: "0 0 4px 0" }}>{mod.name}</h2>
          <div style={{ opacity: 0.75, marginBottom: "8px" }}>
            by {mod.author} · v{mod.version} · 👍{" "}
            {mod.endorsements.toLocaleString()} · ⬇{" "}
            {mod.downloads.toLocaleString()}
          </div>
          {mod.summary && (
            <div style={{ fontSize: "14px", opacity: 0.9 }}>{mod.summary}</div>
          )}
          {(() => {
            const hint = getCompatHint(game.nexusDomain, mod.modId);
            return hint ? (
              <div
                style={{
                  marginTop: "10px",
                  padding: "8px 10px",
                  background: "rgba(255, 200, 60, 0.12)",
                  borderLeft: "3px solid #ffc83c",
                  borderRadius: "4px",
                  fontSize: "13px",
                }}
              >
                🐧 <b>Linux note:</b> {hint}
              </div>
            ) : null;
          })()}
          {requirements && requirements.length > 0 && (
            <div
              style={{
                marginTop: "10px",
                padding: "8px 10px",
                background: "rgba(120, 170, 255, 0.10)",
                borderLeft: "3px solid #78aaff",
                borderRadius: "4px",
                fontSize: "13px",
              }}
            >
              <b>Requires:</b>{" "}
              {requirements
                .map((r) => r.modName + (r.notes ? ` (${r.notes})` : ""))
                .join(", ")}
            </div>
          )}
          {installedCopy && (
            <div style={{ marginTop: "8px", fontSize: "13px", color: "#8fd48f" }}>
              ✓ Installed{installedCopy.version ? ` (v${installedCopy.version})` : ""}
              {installedCopy.enabled ? "" : " · currently disabled"}
            </div>
          )}
          {game.moddedSaveWarning && (
            <div
              style={{
                marginTop: "12px",
                padding: "8px 10px",
                background: "rgba(255, 200, 60, 0.12)",
                borderLeft: "3px solid #ffc83c",
                borderRadius: "4px",
                fontSize: "13px",
              }}
            >
              ⚠ {game.displayName} keeps separate save files for modded and
              unmodded play.
            </div>
          )}
        </div>
      </Focusable>

      <h3 style={{ margin: "16px 0 4px" }}>Files</h3>
      {files === undefined && <div style={{ opacity: 0.8 }}>Loading files…</div>}
      {files && !files.ok && (
        <div style={{ opacity: 0.8 }}>Could not load files: {files.error}</div>
      )}
      <Focusable style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
        {files?.files?.map((file) => (
          <ButtonItem
            key={file.file_id}
            layout="below"
            disabled={installingFileId !== undefined}
            description={`${file.category_name}${file.is_primary ? " · primary" : ""} · v${file.version} · ${fmtSize(file.size_kb)}`}
            onClick={() => onInstall(file)}
          >
            {installingFileId === file.file_id
              ? progressText
              : installedFileIds.has(file.file_id)
              ? `Install ${file.name} ✓`
              : `Install ${file.name}`}
          </ButtonItem>
        ))}
      </Focusable>
      {installedFileIds.size > 0 && (
        <>
          <div style={{ marginTop: "8px", fontSize: "13px", opacity: 0.8 }}>
            Installed mods load when the game starts
            {" "}(it may relaunch itself once more to compile mods).
          </div>
          {isGameRunning(game.appId) && (
            <Focusable style={{ marginTop: "8px", maxWidth: "300px" }}>
              <ButtonItem
                layout="below"
                onClick={() => restartGame(game.appId)}
              >
                Restart {game.displayName} now
              </ButtonItem>
            </Focusable>
          )}
        </>
      )}

      <Focusable
        style={{ marginTop: "16px", display: "flex", gap: "12px", maxWidth: "540px" }}
      >
        {installedCopy && (
          <div style={{ flexGrow: 1 }}>
            <ButtonItem
              layout="below"
              disabled={installingFileId !== undefined}
              onClick={() =>
                showModal(
                  <ConfirmModal
                    strTitle={`Uninstall ${mod.name}?`}
                    strDescription={`This deletes the "${installedCopy.folder}" folder from the game. You can reinstall it at any time.`}
                    strOKButtonText="Uninstall"
                    bDestructiveWarning={true}
                    onOK={async () => {
                      const result = await uninstallMod(
                        game.nexusDomain,
                        game.installDirName,
                        game.modsSubdir,
                        installedCopy.folder
                      );
                      toaster.toast(
                        result.ok
                          ? { title: "Mod uninstalled", body: mod.name }
                          : { title: "Uninstall failed", body: result.error ?? "" }
                      );
                      setInstalledFileIds(new Set());
                      refreshInstalled();
                    }}
                  />
                )
              }
            >
              Uninstall
            </ButtonItem>
          </div>
        )}
        <div style={{ flexGrow: 1 }}>
          <ButtonItem layout="below" onClick={() => Navigation.NavigateBack()}>
            Back to browse
          </ButtonItem>
        </div>
      </Focusable>
    </div>
  );
}
