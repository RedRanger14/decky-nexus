import {
  ButtonItem,
  ConfirmModal,
  DialogButton,
  Focusable,
  ModalRoot,
  Navigation,
  ScrollPanelGroup,
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
  NexusMod,
  getEndorsement,
  getInstalledMods,
  getModDetails,
  getModFiles,
  getModRequirements,
  installMod,
  setEndorsement,
  uninstallMod,
} from "./api";
import { getCompatHint } from "./compat";
import { modeParams } from "./games";

// Steam's scroll panel: right-stick scrolling (untyped props upstream).
const Scroller: any = ScrollPanelGroup;
import { SelectedMod, getSelectedMod, setSelectedMod } from "./state";
import { isGameRunning, restartGame } from "./steam";
import {
  ACCENT_DANGER,
  ACCENT_SUCCESS,
  NEXUS_ORANGE,
  PRIMARY_BUTTON_CLASS,
  PRIMARY_BUTTON_CSS,
} from "./theme";

function fmtSize(sizeKb: number): string {
  if (sizeKb >= 1024) return `${(sizeKb / 1024).toFixed(1)} MB`;
  return `${sizeKb} KB`;
}

/** Mod descriptions arrive as bbcode/html soup - reduce to readable text. */
function stripMarkup(text: string): string {
  return text
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/\[img[^\]]*\][^[]*\[\/img\]/gi, "")
    .replace(/\[youtube[^\]]*\][^[]*\[\/youtube\]/gi, "")
    .replace(/\[[^\]]*\]/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

const DESC_COLLAPSE_LENGTH = 500;

/** Option-style archives ship several alternative folders (a manual-choice
 * mini-FOMOD). The backend lists them; the user picks one to install. */
function PayloadChoiceModal({
  modName,
  options,
  onPick,
  closeModal,
}: {
  modName: string;
  options: string[];
  onPick: (option: string) => void;
  closeModal?: () => void;
}) {
  return (
    <ModalRoot closeModal={closeModal}>
      <h3 style={{ marginTop: 0 }}>{modName}: choose a version</h3>
      <div style={{ fontSize: "13px", opacity: 0.9, marginBottom: "8px" }}>
        This mod's archive offers alternative folders — pick the one to
        install. (Check the mod's description if you're unsure.)
      </div>
      {options.map((opt) => (
        <ButtonItem
          key={opt}
          layout="below"
          onClick={() => {
            closeModal?.();
            onPick(opt);
          }}
        >
          {opt.split("/").pop()}
        </ButtonItem>
      ))}
    </ModalRoot>
  );
}

export function ModDetailPage() {
  const [sel, setSel] = useState<SelectedMod | undefined>(getSelectedMod());
  const [files, setFiles] = useState<FilesResult | undefined>();
  const [requirements, setRequirements] = useState<ModRequirement[] | undefined>();
  const [description, setDescription] = useState<string | undefined>();
  const [descExpanded, setDescExpanded] = useState(false);
  const [showAllFiles, setShowAllFiles] = useState(false);
  const [progress, setProgress] = useState<InstallProgress | undefined>();
  const [installingFileId, setInstallingFileId] = useState<number | undefined>();
  const [installedFileIds, setInstalledFileIds] = useState<Set<number>>(new Set());
  const [installedCopy, setInstalledCopy] = useState<InstalledMod | undefined>();
  const [installedMods, setInstalledMods] = useState<InstalledMod[]>([]);
  const [endorseStatus, setEndorseStatus] = useState<string | undefined>();
  const [endorseBusy, setEndorseBusy] = useState(false);

  const refreshInstalled = (s: SelectedMod) => {
    getInstalledMods(
      s.game.nexusDomain,
      s.game.installDirName,
      s.game.modsSubdir,
      ...modeParams(s.game)
    ).then((r) => {
      setInstalledMods(r.mods ?? []);
      setInstalledCopy(r.mods?.find((m) => m.mod_id === s.mod.modId));
    });
  };

  const loadAll = (s: SelectedMod) => {
    setFiles(undefined);
    setRequirements(undefined);
    setDescription(undefined);
    setDescExpanded(false);
    setShowAllFiles(false);
    setInstalledFileIds(new Set());
    setInstalledCopy(undefined);
    getModFiles(s.game.nexusDomain, s.mod.modId).then(setFiles);
    getModRequirements(s.game.nexusDomain, s.mod.modId).then((r) =>
      setRequirements(r.ok ? r.requirements ?? [] : [])
    );
    getModDetails(s.game.nexusDomain, s.mod.modId).then((r) =>
      setDescription(r.ok ? stripMarkup(r.mod?.description ?? "") : "")
    );
    setEndorseStatus(undefined);
    getEndorsement(s.game.nexusDomain, s.mod.modId).then((r) =>
      setEndorseStatus(r.ok ? r.status : undefined)
    );
    refreshInstalled(s);
  };

  useEffect(() => {
    if (sel) loadAll(sel);
    const listener = addEventListener<[p: InstallProgress]>(
      "install_progress",
      (p) => setProgress(p)
    );
    return () => removeEventListener("install_progress", listener);
  }, []);

  if (!sel) {
    return <div style={{ marginTop: "40px", padding: "24px" }}>No mod selected.</div>;
  }
  const { game, mod } = sel;

  // Framework mods (SMAPI) must go through the guided game-panel setup -
  // installing the raw zip as a drop-in mod just parks the installer in Mods/.
  const isFrameworkMod = game.framework?.nexusModId === mod.modId;

  const openRequirement = async (req: ModRequirement) => {
    if (!req.modId) return;
    const result = await getModDetails(game.nexusDomain, req.modId);
    if (result.ok && result.mod) {
      const next = { game, mod: result.mod as NexusMod };
      setSelectedMod(next);
      setSel(next);
      loadAll(next);
    } else {
      toaster.toast({
        title: "Could not open mod",
        body: result.error ?? req.modName,
      });
    }
  };

  const onInstall = async (file: ModFile, payloadChoice = "") => {
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
        game.modsSubdir,
        "",
        "",
        ...modeParams(game),
        payloadChoice
      );
      if (result.needs_choice && result.options?.length) {
        // Option-style archive: ask which folder to install, then retry.
        showModal(
          <PayloadChoiceModal
            modName={mod.name}
            options={result.options}
            onPick={(opt) => onInstall(file, opt)}
          />
        );
        return;
      }
      if (result.ok) {
        setInstalledFileIds((prev) => new Set(prev).add(file.file_id));
        refreshInstalled(sel);
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

  const fileList = files?.files ?? [];
  // The site's single download button maps to the latest MAIN file; our sort
  // puts the primary file first and OLD_VERSION files last.
  const primaryFile =
    fileList.find((f) => f.category_name !== "OLD_VERSION") ?? fileList[0];

  // The primary button tells the truth about installed state: up-to-date
  // installs get a disabled "Installed" state, outdated ones an Update.
  const normVersion = (v?: string) => (v ?? "").trim().replace(/^[vV]/, "");
  const upToDate = Boolean(
    installedCopy?.version &&
      primaryFile &&
      normVersion(installedCopy.version) === normVersion(primaryFile.version)
  );
  const primaryLabel = !primaryFile
    ? files === undefined
      ? "Loading…"
      : "No files available"
    : installingFileId === primaryFile.file_id
    ? progressText
    : installedFileIds.has(primaryFile.file_id)
    ? "Installed ✓"
    : upToDate
    ? "Installed ✓ (up to date)"
    : installedCopy
    ? installedCopy.version
      ? `⬆ Update to v${primaryFile.version} (${fmtSize(primaryFile.size_kb)})`
      : `⟳ Reinstall v${primaryFile.version} (${fmtSize(primaryFile.size_kb)})`
    : `⬇ Install v${primaryFile.version} (${fmtSize(primaryFile.size_kb)})`;
  const primaryDisabled =
    installingFileId !== undefined || !primaryFile || upToDate;

  const heroUrl = mod.pictureUrl ?? mod.thumbnailUrl;
  const compatHint = getCompatHint(game.nexusDomain, mod.modId);
  const updatedDate = mod.updatedAt ? new Date(mod.updatedAt).toLocaleDateString() : "";
  const descLong = (description?.length ?? 0) > DESC_COLLAPSE_LENGTH;

  return (
    <div
      style={{
        marginTop: "40px",
        height: "calc(100% - 40px)",
      }}
    >
      <Scroller
        focusable={false}
        style={{
          height: "100%",
          overflowY: "auto",
          padding: "0 24px 24px",
        }}
      >
      {/* ---- Header: hero image + facts ---- */}
      <Focusable style={{ display: "flex", gap: "20px", padding: "12px 0 4px" }}>
        {heroUrl && (
          <img
            src={heroUrl}
            alt={mod.name}
            style={{
              width: "44%",
              maxHeight: "280px",
              objectFit: "cover",
              borderRadius: "8px",
              flexShrink: 0,
              alignSelf: "flex-start",
            }}
          />
        )}
        <div style={{ minWidth: 0, flexGrow: 1 }}>
          <h2 style={{ margin: "0 0 2px 0" }}>{mod.name}</h2>
          <div style={{ opacity: 0.75, fontSize: "14px" }}>
            by {mod.author} · v{mod.version}
            {updatedDate ? ` · updated ${updatedDate}` : ""}
          </div>
          <Focusable
            style={{
              display: "flex",
              alignItems: "center",
              gap: "10px",
              marginBottom: "8px",
            }}
          >
            <span style={{ opacity: 0.75, fontSize: "14px" }}>
              👍 {mod.endorsements.toLocaleString()} · ⬇{" "}
              {mod.downloads.toLocaleString()}
            </span>
            {endorseStatus !== undefined && endorseStatus !== "unknown" && (
              <Focusable
                onActivate={async () => {
                  if (endorseBusy) return;
                  setEndorseBusy(true);
                  try {
                    const target = endorseStatus !== "Endorsed";
                    const result = await setEndorsement(
                      game.nexusDomain,
                      mod.modId,
                      mod.version,
                      target
                    );
                    if (result.ok) {
                      setEndorseStatus(result.status);
                      toaster.toast({
                        title: target ? "Endorsed!" : "Endorsement removed",
                        body: target
                          ? `Thanks for supporting ${mod.author}`
                          : mod.name,
                      });
                    } else {
                      toaster.toast({
                        title: "Could not endorse",
                        body: result.error ?? "",
                      });
                    }
                  } finally {
                    setEndorseBusy(false);
                  }
                }}
                style={{
                  padding: "3px 12px",
                  borderRadius: "999px",
                  fontSize: "12px",
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                  opacity: endorseBusy ? 0.5 : 1,
                  ...(endorseStatus === "Endorsed"
                    ? {
                        background: "rgba(143, 212, 143, 0.15)",
                        border: "1px solid rgba(143, 212, 143, 0.5)",
                      }
                    : {
                        background: "rgba(218, 142, 53, 0.15)",
                        border: `1px solid ${NEXUS_ORANGE}88`,
                      }),
                }}
              >
                {endorseStatus === "Endorsed" ? "👍 Endorsed ✓" : "👍 Endorse"}
              </Focusable>
            )}
          </Focusable>
          {mod.summary && (
            <div style={{ fontSize: "13px", opacity: 0.9 }}>{mod.summary}</div>
          )}
          {installedCopy && (
            <div
              style={{ marginTop: "8px", fontSize: "13px", color: ACCENT_SUCCESS }}
            >
              ✓ Installed{installedCopy.version ? ` (v${installedCopy.version})` : ""}
              {installedCopy.enabled ? "" : " · currently disabled"}
            </div>
          )}
          {compatHint && (
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
              🐧 <b>Linux note:</b> {compatHint}
            </div>
          )}
          {requirements && requirements.length > 0 && (
            <div
              style={{
                marginTop: "10px",
                padding: "8px 12px 10px",
                background: "rgba(120, 170, 255, 0.08)",
                borderLeft: "3px solid rgba(120, 170, 255, 0.6)",
                borderRadius: "4px",
              }}
            >
              <div
                style={{ fontSize: "13px", fontWeight: 600, marginBottom: "6px" }}
              >
                Required mods
              </div>
              <Focusable
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                {requirements.map((req) => {
                  const have = installedMods.some(
                    (m) => m.mod_id === req.modId
                  );
                  return (
                    <Focusable
                      key={`${req.modId}-${req.modName}`}
                      onActivate={() => openRequirement(req)}
                      style={{
                        padding: "3px 12px",
                        borderRadius: "999px",
                        fontSize: "12px",
                        whiteSpace: "nowrap",
                        ...(have
                          ? {
                              background: "rgba(143, 212, 143, 0.15)",
                              border: "1px solid rgba(143, 212, 143, 0.5)",
                            }
                          : {
                              background: "rgba(218, 142, 53, 0.15)",
                              border: `1px solid ${NEXUS_ORANGE}88`,
                            }),
                      }}
                    >
                      {have ? "✓ " : "⬇ "}
                      {req.modName}
                      {req.notes ? ` · ${req.notes}` : ""}
                    </Focusable>
                  );
                })}
              </Focusable>
              <div style={{ fontSize: "11px", opacity: 0.65, marginTop: "5px" }}>
                ✓ = already installed · tap an orange one to view and install
                it
              </div>
            </div>
          )}
        </div>
      </Focusable>

      {isFrameworkMod ? (
        <div
          style={{
            marginTop: "12px",
            padding: "10px 12px",
            background: "rgba(255, 200, 60, 0.12)",
            borderLeft: "3px solid #ffc83c",
            borderRadius: "4px",
            fontSize: "13px",
            lineHeight: "1.5",
          }}
        >
          🛠 <b>{mod.name}</b> is the mod loader for {game.displayName}.
          Install it from the game's panel (Step 1) for guided setup with
          launch options — installing it here as a regular mod won't work.
        </div>
      ) : (
        <>
      {/* ---- Primary actions: one big install (latest main file), all-files
           toggle, uninstall - mirroring the site's single download button ---- */}
      {/* Focus starts on the page's main action row, not the endorse chip
          above it (first-in-DOM otherwise wins). */}
      <Focusable
        autoFocus={true}
        style={{
          display: "flex",
          gap: "10px",
          margin: "12px 0 0",
          maxWidth: "760px",
        }}
      >
        <style>{PRIMARY_BUTTON_CSS}</style>
        <DialogButton
          disabled={primaryDisabled}
          onClick={() => primaryFile && onInstall(primaryFile)}
          className={PRIMARY_BUTTON_CLASS}
          style={{
            flexGrow: 2,
            minWidth: "240px",
            opacity: primaryDisabled && !upToDate ? 0.55 : upToDate ? 0.75 : 1,
          }}
        >
          {primaryLabel}
        </DialogButton>
        <DialogButton
          disabled={fileList.length === 0}
          onClick={() => setShowAllFiles(!showAllFiles)}
          style={{ flexGrow: 1, minWidth: "150px" }}
        >
          {showAllFiles ? "Hide files ▴" : `All files (${fileList.length}) ▾`}
        </DialogButton>
        {installedCopy && (
          <DialogButton
            disabled={installingFileId !== undefined}
            style={{ flexGrow: 1, minWidth: "140px", color: ACCENT_DANGER }}
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
                      installedCopy.folder,
                      ...modeParams(game)
                    );
                    toaster.toast(
                      result.ok
                        ? { title: "Mod uninstalled", body: mod.name }
                        : { title: "Uninstall failed", body: result.error ?? "" }
                    );
                    setInstalledFileIds(new Set());
                    refreshInstalled(sel);
                  }}
                />
              )
            }
          >
            Uninstall
          </DialogButton>
        )}
      </Focusable>
      {files && !files.ok && (
        <div style={{ opacity: 0.8, fontSize: "13px" }}>
          Could not load files: {files.error}
        </div>
      )}
      {installedFileIds.size > 0 && (
        <div style={{ marginTop: "4px", fontSize: "13px", opacity: 0.8 }}>
          Installed mods load when the game starts
          {game.logAdapter?.kind === "godot"
            ? " (it may relaunch itself once more to compile mods)."
            : "."}
        </div>
      )}
        </>
      )}

      {/* ---- Description ---- */}
      {description === undefined ? (
        <div style={{ opacity: 0.7, padding: "8px 0" }}>Loading description…</div>
      ) : description ? (
        <>
          <h3 style={{ margin: "14px 0 4px" }}>About</h3>
          <div
            style={{
              fontSize: "13px",
              opacity: 0.9,
              whiteSpace: "pre-wrap",
              lineHeight: "1.5",
              ...(descExpanded || !descLong
                ? {}
                : { maxHeight: "108px", overflow: "hidden" }),
            }}
          >
            {description}
          </div>
          {descLong && (
            <Focusable
              onActivate={() => setDescExpanded(!descExpanded)}
              style={{
                display: "inline-block",
                marginTop: "4px",
                padding: "3px 12px",
                background: "rgba(255, 255, 255, 0.08)",
                borderRadius: "999px",
                fontSize: "12px",
              }}
            >
              {descExpanded ? "Show less ▴" : "Show more ▾"}
            </Focusable>
          )}
        </>
      ) : null}

      {/* ---- All files (collapsed by default) ---- */}
      {!isFrameworkMod && showAllFiles && <h3 style={{ margin: "16px 0 6px" }}>All Files</h3>}
      {!isFrameworkMod && showAllFiles && (
      <Focusable
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(290px, 1fr))",
          gap: "8px",
        }}
      >
        {files?.files?.map((file) => {
          const busy = installingFileId === file.file_id;
          const done = installedFileIds.has(file.file_id);
          return (
            <Focusable
              key={file.file_id}
              onActivate={() => {
                if (installingFileId === undefined) onInstall(file);
              }}
              style={{
                background: "rgba(255, 255, 255, 0.06)",
                borderRadius: "6px",
                padding: "8px 12px",
                opacity:
                  installingFileId !== undefined && !busy ? 0.45 : 1,
              }}
            >
              <div
                style={{
                  fontWeight: 600,
                  fontSize: "13px",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {file.name}
              </div>
              <div style={{ fontSize: "11px", opacity: 0.65 }}>
                {file.category_name}
                {file.is_primary ? " · primary" : ""} · v{file.version} ·{" "}
                {fmtSize(file.size_kb)}
              </div>
              <div
                style={{
                  fontSize: "12px",
                  marginTop: "2px",
                  color: done ? ACCENT_SUCCESS : NEXUS_ORANGE,
                }}
              >
                {busy ? progressText : done ? "Installed ✓" : "⬇ Install"}
              </div>
            </Focusable>
          );
        })}
      </Focusable>
      )}

      {/* ---- Footer actions ---- */}
      <Focusable
        style={{ marginTop: "16px", display: "flex", gap: "12px", maxWidth: "640px" }}
      >
        {isGameRunning(game.appId) && installedFileIds.size > 0 && (
          <DialogButton
            style={{ flexGrow: 1 }}
            onClick={() => restartGame(game.appId)}
          >
            Restart {game.displayName} now
          </DialogButton>
        )}
        <DialogButton
          style={{ flexGrow: 1 }}
          onClick={() => Navigation.NavigateBack()}
        >
          Back to browse
        </DialogButton>
      </Focusable>
      </Scroller>
    </div>
  );
}
