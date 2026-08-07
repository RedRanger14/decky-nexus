import {
  ConfirmModal,
  DialogButton,
  Focusable,
  Navigation,
  QuickAccessTab,
  ScrollPanelGroup,
  showModal,
} from "@decky/ui";
import { addEventListener, removeEventListener, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FaArrowDown, FaThumbsUp } from "react-icons/fa";

import {
  FilesResult,
  InstallProgress,
  InstalledMod,
  ModFile,
  ModRequirement,
  NexusMod,
  getEndorsement,
  getGameStatus,
  getInstalledMods,
  getModDetails,
  getModFiles,
  getModRequirements,
  installMod,
  setEndorsement,
  uninstallMod,
} from "./api";
import { PayloadChoiceModal } from "./ChoiceModal";
import { getCompatHint } from "./compat";
import { modeParams } from "./games";
import { finishFomod, installLatest } from "./install";
import { FomodWizardData, FomodWizardModal } from "./FomodWizard";

// Steam's scroll panel: right-stick scrolling (untyped props upstream).
const Scroller: any = ScrollPanelGroup;
import {
  SelectedMod,
  getDetailOrigin,
  getSelectedMod,
  nameDownload,
  setSelectedMod,
} from "./state";
import { isGameRunning, restartGame } from "./steam";
import {
  ACCENT_DANGER,
  ACCENT_SUCCESS,
  ACTION_BUTTON,
  ACTION_ROW,
  NEXUS_ORANGE,
  PRIMARY_BUTTON_CLASS,
  PRIMARY_BUTTON_CSS,
} from "./theme";
import { DownloadsButton } from "./DownloadsButton";

function fmtSize(sizeKb: number): string {
  if (sizeKb >= 1024 * 1024) return `${(sizeKb / 1024 / 1024).toFixed(1)} GB`;
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
  const [imageFull, setImageFull] = useState(false);
  const [fwInstalled, setFwInstalled] = useState(false);
  const [uploader, setUploader] = useState<NexusMod["uploader"]>();

  const refreshInstalled = (s: SelectedMod) => {
    getInstalledMods(
      s.game.nexusDomain,
      s.game.installDirName,
      s.game.modsSubdir,
      ...modeParams(s.game),
      s.game.protectedModFolders ?? []
    ).then((r) => {
      setInstalledMods(r.mods ?? []);
      setInstalledCopy(r.mods?.find((m) => m.mod_id === s.mod.modId));
    });
    // Frameworks (SMAPI/SKSE/BepInEx) don't create mod records - a
    // requirement pointing at one must still show green when installed.
    if (s.game.framework) {
      getGameStatus(
        s.game.installDirName,
        s.game.modsSubdir,
        s.game.framework.detectFile
      ).then((r) => setFwInstalled(Boolean(r.framework_installed)));
    }
  };

  const loadAll = (s: SelectedMod) => {
    setFiles(undefined);
    setRequirements(undefined);
    setDescription(undefined);
    setDescExpanded(false);
    setShowAllFiles(false);
    setInstalledFileIds(new Set());
    setInstalledCopy(undefined);
    setImageFull(false);
    getModFiles(s.game.nexusDomain, s.mod.modId).then(setFiles);
    getModRequirements(s.game.nexusDomain, s.mod.modId).then((r) =>
      setRequirements(r.ok ? r.requirements ?? [] : [])
    );
    getModDetails(s.game.nexusDomain, s.mod.modId).then((r) => {
      setDescription(r.ok ? stripMarkup(r.mod?.description ?? "") : "");
      setUploader(r.ok ? (r.mod as NexusMod | undefined)?.uploader : undefined);
    });
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
      // Only THIS mod's events - background pipeline events for other
      // mods made the install button flicker downloading<->installing.
      (p) =>
        setProgress((prev) => (p.mod_id === sel?.mod.modId ? p : prev))
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

  // One classification, shared by the chips and the install-all button.
  const classifyRequirement = (req: ModRequirement) => {
    const external = !req.modId || req.modId <= 0;
    const fwIds = [
      game.framework?.nexusModId,
      ...(game.framework?.aliasModIds ?? []),
    ].filter(Boolean);
    const norm = (t: string) => t.toLowerCase().replace(/[^a-z0-9]/g, "");
    const have =
      !external &&
      (installedMods.some(
        (m) =>
          m.mod_id === req.modId ||
          Boolean(
            m.name && req.modName && norm(m.name) === norm(req.modName)
          )
      ) ||
        (fwInstalled && fwIds.includes(req.modId)));
    const optional = !have && /optional/i.test(req.notes ?? "");
    return { external, have, optional };
  };

  const [reqBatchBusy, setReqBatchBusy] = useState(false);

  /** Install every missing required (non-optional) Nexus mod, in the
   * order the mod page lists them. The Downloads panel tracks each. */
  const installMissingRequirements = async () => {
    if (!requirements || reqBatchBusy) return;
    setReqBatchBusy(true);
    try {
      const missing = requirements.filter((r) => {
        const c = classifyRequirement(r);
        return !c.external && !c.have && !c.optional;
      });
      for (const req of missing) {
        const result = await installLatest(
          game,
          req.modId,
          req.modName || `Mod ${req.modId}`
        );
        if (result.needs_choice) {
          toaster.toast({
            title: `${req.modName}: choose manually`,
            body: "This one offers versions - open its page to pick",
          });
        } else if (!result.ok) {
          toaster.toast({
            title: `${req.modName} failed`,
            body: result.error ?? "",
          });
        }
      }
      refreshInstalled(sel);
      toaster.toast({
        title: "Required mods done",
        body: `${missing.length} processed - check the chips`,
      });
    } finally {
      setReqBatchBusy(false);
    }
  };

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
    nameDownload(mod.modId, mod.name, game.appId);
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
        payloadChoice,
        game.ue4ss?.modsSubdir ?? "",
        game.ue4ss?.logicModsSubdir ?? "",
        game.launcherXmlSubpath ?? "",
        game.flatModExtensions ?? [],
        mod.version,
        "",
        game.witcherLayout ?? false,
        "",
        game.cp77Layout ?? false,
        game.pakPatchLayout ?? false
      );
      if (result.needs_fomod && result.fomod_token && result.wizard) {
        // FOMOD archive: run the wizard, then finish with the choices.
        showModal(
          <FomodWizardModal
            wizard={result.wizard as FomodWizardData}
            onInstall={async (ids) => {
              nameDownload(mod.modId, mod.name, game.appId);
              const done = await finishFomod(result.fomod_token!, ids);
              if (done.ok) {
                setInstalledFileIds((prev) => new Set(prev).add(file.file_id));
                refreshInstalled(sel);
                toaster.toast({ title: `${mod.name} installed`, body: "" });
              } else {
                toaster.toast({
                  title: "Install failed",
                  body: done.error ?? "",
                });
              }
            }}
          />
        );
        return;
      }
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
    : `Install v${primaryFile.version} (${fmtSize(primaryFile.size_kb)})`;
  const primaryDisabled =
    installingFileId !== undefined || !primaryFile || upToDate;

  const heroUrl = mod.pictureUrl ?? mod.thumbnailUrl;
  const compatHint = getCompatHint(game.nexusDomain, mod.modId);
  const updatedDate = mod.updatedAt ? new Date(mod.updatedAt).toLocaleDateString() : "";
  const descLong = (description?.length ?? 0) > DESC_COLLAPSE_LENGTH;

  const goBack = () => {
    if (getDetailOrigin() === "qam") {
      // QAM first so focus lands in it, then pop the page behind.
      Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);
      setTimeout(() => Navigation.NavigateBack(), 50);
    } else {
      Navigation.NavigateBack();
    }
  };

  return (
    <Focusable
      onCancel={goBack}
      style={{
        marginTop: "40px",
        height: "calc(100% - 40px)",
      }}
    >
      {imageFull && heroUrl && (
        <Focusable
          autoFocus={true}
          onActivate={() => setImageFull(false)}
          onCancel={() => setImageFull(false)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 100,
            background: "rgba(0, 0, 0, 0.96)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <img
            src={heroUrl}
            alt={mod.name}
            style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }}
          />
          <div
            style={{
              position: "absolute",
              bottom: "14px",
              right: "20px",
              fontSize: "13px",
              opacity: 0.7,
            }}
          >
            B — close
          </div>
        </Focusable>
      )}
      <Scroller
        focusable={false}
        style={{
          height: "100%",
          overflowY: "auto",
          // Clears the SteamOS footer bar AND makes focus-driven scrolling
          // stop short of it (scroll-padding), so the last row is usable.
          padding: "0 24px 110px",
          scrollPaddingBottom: "110px",
        }}
      >
      {/* ---- Header: hero image + facts ---- */}
      <Focusable style={{ display: "flex", gap: "20px", padding: "12px 0 4px" }}>
        {heroUrl && (
          <Focusable
            onActivate={() => setImageFull(true)}
            style={{ width: "44%", flexShrink: 0, alignSelf: "flex-start" }}
          >
            <img
              src={heroUrl}
              alt={mod.name}
              style={{
                width: "100%",
                height: "280px",
                // Never crop the artwork - letterbox odd aspect ratios.
                objectFit: "contain",
                background: "#0b0e13",
                borderRadius: "8px",
                display: "block",
              }}
            />
          </Focusable>
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
              <FaThumbsUp size={11} style={{ opacity: 0.75, marginRight: "4px" }} />{mod.endorsements.toLocaleString()} ·{" "}
              <FaArrowDown size={11} style={{ opacity: 0.75, margin: "0 4px 0 4px" }} />{" "}
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
                <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                  <FaThumbsUp size={12} />
                  {endorseStatus === "Endorsed" ? "Endorsed" : "Endorse"}
                </span>
              </Focusable>
            )}
            {uploader?.donationsEnabled && uploader.memberId && (
              <Focusable
                onActivate={() =>
                  Navigation.NavigateToExternalWeb(
                    `https://www.nexusmods.com/users/${uploader.memberId}`
                  )
                }
                style={{
                  padding: "3px 12px",
                  borderRadius: "999px",
                  fontSize: "12px",
                  whiteSpace: "nowrap",
                  background: "rgba(255, 120, 150, 0.12)",
                  border: "1px solid rgba(255, 120, 150, 0.45)",
                }}
              >
                ❤ Support {uploader.name ?? mod.author}
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
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  marginBottom: "6px",
                }}
              >
                <span style={{ fontSize: "13px", fontWeight: 600 }}>
                  Required mods
                </span>
                {requirements.some((r) => {
                  const c = classifyRequirement(r);
                  return !c.external && !c.have && !c.optional;
                }) && (
                  <DialogButton
                    disabled={reqBatchBusy}
                    onClick={installMissingRequirements}
                    style={{
                      minWidth: "0",
                      width: "auto",
                      padding: "4px 12px",
                      fontSize: "12px",
                    }}
                  >
                    {reqBatchBusy
                      ? "Installing…"
                      : "Install all missing"}
                  </DialogButton>
                )}
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
                  const { external, have, optional } =
                    classifyRequirement(req);
                  const label = external
                    ? req.modName || req.notes || req.url || "external"
                    : `${req.modName}${req.notes ? ` · ${req.notes}` : ""}`;
                  return (
                    <Focusable
                      key={`${req.modId}-${req.modName}-${req.url}`}
                      onActivate={() => {
                        if (!external) {
                          openRequirement(req);
                        } else if (req.url) {
                          Navigation.NavigateToExternalWeb(req.url);
                        }
                      }}
                      style={{
                        padding: "3px 12px",
                        borderRadius: "999px",
                        fontSize: "12px",
                        whiteSpace: "nowrap",
                        maxWidth: "100%",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        ...(external
                          ? {
                              background: "rgba(120, 170, 255, 0.15)",
                              border: "1px solid rgba(120, 170, 255, 0.5)",
                            }
                          : have
                          ? {
                              background: "rgba(143, 212, 143, 0.15)",
                              border: "1px solid rgba(143, 212, 143, 0.5)",
                            }
                          : optional
                          ? {
                              background: "rgba(255, 255, 255, 0.06)",
                              border: "1px dashed rgba(255, 255, 255, 0.35)",
                              opacity: 0.8,
                            }
                          : {
                              background: "rgba(218, 142, 53, 0.15)",
                              border: `1px solid ${NEXUS_ORANGE}88`,
                            }),
                      }}
                    >
                      {external ? "🌐 " : have ? "✓ " : optional ? "○ " : ""}
                      {label}
                    </Focusable>
                  );
                })}
              </Focusable>
              <div style={{ fontSize: "11px", marginTop: "6px" }}>
                <span style={{ color: "rgb(143, 212, 143)" }}>● Installed</span>
                <span style={{ opacity: 0.5 }}> · </span>
                <span style={{ color: NEXUS_ORANGE }}>● Needs installing</span>
                <span style={{ opacity: 0.5 }}> · </span>
                <span style={{ color: "rgb(120, 170, 255)" }}>
                  ● External link
                </span>
                <span style={{ opacity: 0.5 }}> · </span>
                <span style={{ opacity: 0.7 }}>○ Optional</span>
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
        style={{ ...ACTION_ROW, margin: "12px 0 0" }}
      >
        <style>{PRIMARY_BUTTON_CSS}</style>
        <DialogButton
          disabled={primaryDisabled}
          onClick={() => primaryFile && onInstall(primaryFile)}
          className={PRIMARY_BUTTON_CLASS}
          style={{
            ...ACTION_BUTTON,
            opacity: primaryDisabled && !upToDate ? 0.55 : upToDate ? 0.75 : 1,
          }}
        >
          {primaryLabel}
        </DialogButton>
        <DialogButton
          disabled={fileList.length === 0}
          onClick={() => setShowAllFiles(!showAllFiles)}
          style={ACTION_BUTTON}
        >
          {showAllFiles ? "Hide files ▴" : `All files (${fileList.length}) ▾`}
        </DialogButton>
        {installedCopy && (
          <DialogButton
            disabled={installingFileId !== undefined}
            style={{ ...ACTION_BUTTON, color: ACCENT_DANGER }}
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
        <DownloadsButton />
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
                {busy ? progressText : done ? "Installed ✓" : "Install"}
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
          onClick={goBack}
        >
          {getDetailOrigin() === "qam" ? "Back" : "Back to browse"}
        </DialogButton>
      </Focusable>
      </Scroller>
    </Focusable>
  );
}
