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
  getModSupport,
  setEndorsement,

  getKnownModVerdict,
  getShowAdult,
  getInstallBlock,
} from "./api";
import { knownBrokenNote, requirementSetupNotes } from "./panelRules";
import { PayloadChoiceModal } from "./ChoiceModal";
import { EndorsePill } from "./EndorseButton";
import { popOurPage, pushOurPage } from "./Tabs";
import { getCompatHint } from "./compat";
import { frameworkModIds, modeParams } from "./games";
import {
  finishFomod,
  installLatest,
  installModWith,
  removeMod,
} from "./install";
import { FomodWizardData, FomodWizardModal } from "./FomodWizard";

// Steam's scroll panel: right-stick scrolling (untyped props upstream).
const Scroller: any = ScrollPanelGroup;
import {
  SelectedMod,
  getDetailOrigin,
  getSelectedMod,
  nameDownload,
  setSelectedMod,
  markManagerReturn,
} from "./state";
import { isGameRunning, restartGame, setLaunchOptions } from "./steam";
import {
  ACCENT_DANGER,
  ACCENT_SUCCESS,
  ACTION_BUTTON,
  ACTION_COLUMN,
  ACTION_HERO,
  ACTION_ROW,
  actionColumnWidth,
  NEXUS_ORANGE,
  PRIMARY_BUTTON_CLASS,
  PRIMARY_BUTTON_CSS,
  BUSY_BUTTON_CLASS,
} from "./theme";
import { PageBackdrop, SectionHeading, StatChip, WarningBox } from "./chrome";
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
  /** DLC this mod needs: the structured field, or the author's own sentence. */
  const [dlcNeed, setDlcNeed] = useState("");
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
  // Needs something Nexus does not host. Shown on the mod's own page,
  // because a user can arrive here from a search and never see the
  // collection that warned about it.
  // The account's "blur adult images" preference. Browse tiles honoured it
  // and this page did not, so opening a blurred tile showed it unblurred -
  // which defeats the setting entirely, since the mod page is where the art
  // is biggest.
  const [blurAdult, setBlurAdult] = useState(false);
  // A refusal that needs explaining, kept on the page rather than in a
  // toast that truncates it and then leaves.
  const [blocked, setBlocked] = useState<string | undefined>();
  // Installable, but built for an older build of the game. A warning, not
  // a refusal: a byte signature often survives a patch, and it is the
  // author's mod, not ours to veto.
  const [stale, setStale] = useState<string | undefined>();
  useEffect(() => {
    getShowAdult()
      .then((r) => setBlurAdult(Boolean(r.ok && r.show_adult && r.blur_adult)))
      .catch(() => {});
  }, []);
  // Non-empty = the version we watched fail on this game build.
  const [knownBroken, setKnownBroken] = useState("");
  const [needsExternal, setNeedsExternal] = useState<
    { reason: string; url: string } | undefined
  >();
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
    setBlocked(undefined);
    setStale(undefined);
    getModFiles(s.game.nexusDomain, s.mod.modId).then((r) => {
      setFiles(r);
      // Ask up front whether this would be refused. Michael: "lets just put
      // the box there before the user clicks install, why show it after?"
      const list = r.ok ? r.files ?? [] : [];
      const first = list.find((f) => f.is_primary) ?? list[0];
      if (!first) return;
      getInstallBlock(
        s.game.nexusDomain,
        s.mod.modId,
        first.file_id,
        s.mod.name,
        String(modeParams(s.game)[0] ?? ""),
        s.game.appId
      ).then((b) => {
        if (b.blocked && b.reason) setBlocked(b.reason);
        else if (b.warning) setStale(b.warning);
      });
    });
    getModRequirements(s.game.nexusDomain, s.mod.modId).then((r) => {
      setRequirements(r.ok ? r.requirements ?? [] : []);
      // Structured DLC first (the real field), the author's sentence as the
      // fallback for everything published before that field existed.
      const dlcNames = (r.dlc ?? []).map((d) => d.name).filter(Boolean);
      setDlcNeed(
        dlcNames.length > 0
          ? `Needs the ${dlcNames.join(" and ")} DLC.`
          : r.dlc_quote ?? ""
      );
    });
    getModDetails(s.game.nexusDomain, s.mod.modId).then((r) => {
      setDescription(r.ok ? stripMarkup(r.mod?.description ?? "") : "");
      setUploader(r.ok ? (r.mod as NexusMod | undefined)?.uploader : undefined);
    });
    setEndorseStatus(undefined);
    setDlcNeed("");
    setNeedsExternal(undefined);
    setKnownBroken("");
    // What we have watched this mod actually do on the game build installed
    // right now. Michael: "I dont want users to run into these problems
    // indicually as well as on collections" - and unlike the hand-written
    // table this one fills itself in.
    getKnownModVerdict(s.game.nexusDomain, s.mod.modId, s.game.appId)
      .then((r) => setKnownBroken(r.ok && r.known ? r.version ?? "" : ""))
      .catch(() => {});
    getModSupport(s.game.nexusDomain, s.mod.modId)
      .then((r) => {
        if (r.ok && r.supported === false) {
          setNeedsExternal({ reason: r.reason ?? "", url: r.url ?? "" });
        }
      })
      .catch(() => {});
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
    // Every framework, not just the primary one. Cyberpunk needs five and
    // Step 1 installs them together; counting only CET left the other four
    // showing orange on every mod page that required them.
    const fwIds = frameworkModIds(game);
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
        // Desktop managers (Arsenal-class) are never "missing" - the
        // plugin does their job, and installing one would drop a Windows
        // exe into the game folder.
        if ((game.heroExcludeModIds ?? []).includes(r.modId)) return false;
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
      setBlocked(undefined);
      const result = await installModWith(
        game,
        mod.modId,
        file.file_id,
        file.file_name,
        mod.name,
        file.version || mod.version,
        "",
        mod.version,
        "",
        payloadChoice
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
            allowMerge={result.merge_allowed !== false}
            onPick={(opt) => onInstall(file, opt)}
          />
        );
        return;
      }
      if (result.ok && result.reshade && game.reshade) {
        // Injector installed beside the exe. Proton loads its builtin
        // dxgi unless told otherwise, so the override is applied for the
        // user rather than described to them - and the warning (anti-cheat
        // risk, author's own words) stays on the page, not in a toast.
        setLaunchOptions(game.appId, game.reshade.launchOptionsTemplate);
        setInstalledFileIds((prev) => new Set(prev).add(file.file_id));
        refreshInstalled(sel);
        setStale(result.warning);
        toaster.toast({
          title: `${mod.name} installed`,
          body: "ReShade launch options set - see the note on this page",
        });
        return;
      }
      if (result.ok && result.installed_disabled) {
        // Installed and deliberately off. The box stays on the page with
        // the reason, so nothing depends on catching a toast.
        setInstalledFileIds((prev) => new Set(prev).add(file.file_id));
        refreshInstalled(sel);
        setStale(result.warning);
        toaster.toast({
          title: `${mod.name} installed, switched off`,
          body: "Built for an older patch - see the mod page",
        });
      } else if (result.ok) {
        setInstalledFileIds((prev) => new Set(prev).add(file.file_id));
        refreshInstalled(sel);
        // onClick ONLY while the game is running. It used to be attached
        // unconditionally, so brushing a toast launched the game - and
        // during a 183-mod collection there are a lot of toasts to brush.
        // Michael, on the Witcher 3 menu: "I kept getting 'game is already
        // running'". A toast that says "it will load next time" must not
        // start anything when tapped.
        const running = isGameRunning(game.appId);
        toaster.toast({
          title: `${mod.name} installed`,
          body: running
            ? `Tap here to restart ${game.displayName} and load it.`
            : `It will load next time ${game.displayName} starts.`,
          ...(running ? { onClick: () => restartGame(game.appId) } : {}),
        });
      } else if (result.mod_conflict || result.script_conflict) {
        // Not a broken mod: something already installed is in the way, and
        // the way out is a sentence long. The box says it in full; the
        // toast only points at the box.
        setBlocked(result.error ?? "Another mod is in the way.");
        toaster.toast({
          title: "Install blocked",
          body: "Details on the mod page.",
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
        : progress.phase === "compiling"
        ? `Compiling… ${progress.percent}%`
        : "Installing…"
      : "Installing…";

  // What the install is actually doing, in words. Only shown while it runs.
  // A compile is minutes of silence otherwise, and silence reads as broken.
  const progressNote =
    installingFileId !== undefined &&
    progress?.mod_id === mod.modId &&
    progress.message
      ? progress.message
      : "";

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
  // While the main file installs, the button IS the progress bar - the
  // same fill language as the collection rows and the QAM tool button.
  // All files + (Uninstall) + Go to downloads. The hero above them takes
  // its width from this count.
  const secondaryActions = 2 + (installedCopy ? 1 : 0);
  const primaryBusy =
    primaryFile !== undefined && installingFileId === primaryFile.file_id;
  const primaryPct =
    primaryBusy && progress?.mod_id === mod.modId
      ? progress.phase === "extracting"
        ? 100
        : progress.percent
      : 0;

  // Blur only when BOTH are true: the account asked for it and this

  // mod is flagged adult. Blurring everything would be a bug in the

  // other direction.

  const blurThisMod = blurAdult && Boolean(mod.adultContent);

  const heroUrl = mod.pictureUrl ?? mod.thumbnailUrl;
  const compatHint = getCompatHint(game.nexusDomain, mod.modId);
  const updatedDate = mod.updatedAt ? new Date(mod.updatedAt).toLocaleDateString() : "";
  const createdDate = mod.createdAt ? new Date(mod.createdAt).toLocaleDateString() : "";
  const descLong = (description?.length ?? 0) > DESC_COLLAPSE_LENGTH;

  const goBack = () => {
    if (getDetailOrigin() === "qam") {
      // QAM first so focus lands in it, then pop the page behind.
      Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);
      setTimeout(() => Navigation.NavigateBack(), 50);
    } else {
      popOurPage();
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
      {/* Mod art as blurred atmosphere behind the header - depth without
          competing with the real artwork card in front of it. */}
      <PageBackdrop src={heroUrl} height={250} />
      {/* One flag for every image on the page: the header art, the backdrop
          behind it and the gallery all have to agree, or the setting only
          half-works. */}
      <div style={{ position: "relative", zIndex: 1 }}>
      {/* ---- Header: hero image + facts ---- */}
      <Focusable style={{ display: "flex", gap: "20px", padding: "12px 0 4px" }}>
        {heroUrl && (
          <Focusable
            onActivate={() => setImageFull(true)}
            style={{ width: "40%", flexShrink: 0, alignSelf: "flex-start" }}
          >
            <img
              src={heroUrl}
              alt={mod.name}
              style={{
                filter: blurThisMod ? "blur(18px)" : undefined,
                width: "100%",
                height: "250px",
                // Never crop the artwork - letterbox odd aspect ratios.
                objectFit: "contain",
                background: "rgba(11,14,19,0.85)",
                borderRadius: "8px",
                border: "1px solid rgba(255,255,255,0.08)",
                display: "block",
              }}
            />
          </Focusable>
        )}
        <div style={{ minWidth: 0, flexGrow: 1 }}>
          <h2
            style={{
              margin: "0 0 3px 0",
              fontSize: "24px",
              lineHeight: 1.2,
              fontWeight: 700,
            }}
          >
            {mod.name}
          </h2>
          <div style={{ opacity: 0.75, fontSize: "13.5px", marginBottom: "8px" }}>
            by {mod.author}
          </div>
          {/* Warned on the mod's OWN page, not only on the collection that
              ships it - a user can arrive here from a search and would
              otherwise install something that stops the game starting with
              nothing anywhere to explain why. */}
          {knownBroken && (
            <div
              style={{
                marginBottom: "10px",
                padding: "8px 10px",
                borderRadius: "4px",
                fontSize: "12.5px",
                lineHeight: 1.45,
                background: "rgba(220, 80, 80, 0.14)",
                border: "1px solid rgba(220, 80, 80, 0.5)",
              }}
            >
              ⚠ {knownBrokenNote(knownBroken)}
            </div>
          )}
          {needsExternal && (
            <div
              style={{
                marginBottom: "10px",
                padding: "8px 10px",
                borderRadius: "4px",
                fontSize: "12.5px",
                lineHeight: 1.45,
                background: "rgba(220, 80, 80, 0.14)",
                border: "1px solid rgba(220, 80, 80, 0.5)",
              }}
            >
              ⚠ {needsExternal.reason}
              {needsExternal.url ? ` Get it from ${needsExternal.url}` : ""}
            </div>
          )}
          <Focusable
            style={{
              display: "flex",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "8px",
              marginBottom: "10px",
            }}
          >
            <StatChip icon={<FaThumbsUp size={11} />}>
              {mod.endorsements.toLocaleString()}
            </StatChip>
            <StatChip icon={<FaArrowDown size={11} />}>
              {mod.downloads.toLocaleString()}
            </StatChip>
            <StatChip>v{mod.version}</StatChip>
            {mod.preGameUpdate && (
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  padding: "1px 8px",
                  borderRadius: "3px",
                  fontSize: "10px",
                  fontWeight: 700,
                  letterSpacing: "0.4px",
                  background: "rgba(200, 130, 50, 0.92)",
                  color: "#fff",
                }}
              >
                PRE-UPDATE
              </span>
            )}
            {/* Both dates, plainly: upload age and update recency answer
                different questions (is it established? is it maintained?),
                and on live-service games the updated date is the whole
                ballgame. */}
            {createdDate && <StatChip>uploaded {createdDate}</StatChip>}
            {updatedDate && updatedDate !== createdDate && (
              <StatChip>updated {updatedDate}</StatChip>
            )}
            {endorseStatus !== undefined && endorseStatus !== "unknown" && (
              <EndorsePill
                endorsed={endorseStatus === "Endorsed"}
                busy={endorseBusy}
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
              />
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
          {dlcNeed !== "" && (
            <div
              style={{
                marginTop: "10px",
                padding: "8px 12px 10px",
                background: "rgba(218, 142, 53, 0.10)",
                borderLeft: `3px solid ${NEXUS_ORANGE}`,
                borderRadius: "4px",
                fontSize: "12px",
                lineHeight: 1.45,
              }}
            >
              <div style={{ fontWeight: 600, marginBottom: "3px" }}>
                Needs paid DLC
              </div>
              {/* The author's words, not ours: working out which DLC a Steam
                  install includes is game-specific and fragile, and a wrong
                  "you do not own this" is worse than the sentence itself.
                  This is stated ABOVE the required-mods list because no
                  amount of mod installing fixes a missing DLC - Michael's
                  Eagle Rising crash was exactly this, after its own DLLs
                  had loaded fine. */}
              <div style={{ opacity: 0.9 }}>{dlcNeed}</div>
              <div style={{ opacity: 0.6, marginTop: "3px" }}>
                Check you own it before installing. Without it, the mod can
                install cleanly and still crash the game.
              </div>
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
                  if ((game.heroExcludeModIds ?? []).includes(r.modId))
                    return false;
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
                  // Desktop mod managers listed as "requirements" out of
                  // community habit (Arsenal, HD2MM). The plugin does that
                  // job here, and offering to install a Windows manager on
                  // a Deck is worse than confusing. Named per game by the
                  // same ids the hero band excludes.
                  const managed =
                    !external &&
                    (game.heroExcludeModIds ?? []).includes(req.modId);
                  // The note is NOT crammed in here any more: the pill is
                  // nowrap with an ellipsis, so a real instruction ("Disable
                  // troop overhaul") was clipped into invisibility while the
                  // row looked complete. Instructions get their own block
                  // below; short category notes ("Required for scripts") add
                  // nothing to a name that already says it.
                  const label = external
                    ? req.modName || req.notes || req.url || "external"
                    : managed
                    ? `${req.modName} · not needed (this plugin does its job)`
                    : req.modName;
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
              {requirementSetupNotes(requirements).length > 0 && (
                <div
                  style={{
                    marginTop: "8px",
                    paddingTop: "7px",
                    borderTop: "1px solid rgba(255,255,255,0.10)",
                    fontSize: "12px",
                    lineHeight: 1.45,
                  }}
                >
                  <div style={{ fontWeight: 600, marginBottom: "3px" }}>
                    Setup notes from the author
                  </div>
                  {requirementSetupNotes(requirements).map((n) => (
                    <div key={n.modName} style={{ opacity: 0.9 }}>
                      <span style={{ color: NEXUS_ORANGE }}>{n.modName}</span>
                      {": "}
                      {n.notes}
                    </div>
                  ))}
                </div>
              )}
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
      {/* Install spans exactly the buttons beneath it - the column sets
          one width and both rows fill it. It also doubles as its own
          progress bar while installing. */}
      <Focusable
        autoFocus={true}
        style={{
          ...ACTION_COLUMN,
          margin: "14px 0 0",
          maxWidth: actionColumnWidth(secondaryActions),
        }}
      >
        <style>{PRIMARY_BUTTON_CSS}</style>
        <DialogButton
          disabled={primaryDisabled}
          onClick={() => primaryFile && onInstall(primaryFile)}
          className={primaryBusy ? BUSY_BUTTON_CLASS : PRIMARY_BUTTON_CLASS}
          style={{
            ...ACTION_HERO,
            ...(primaryBusy
              ? {
                  background: `linear-gradient(90deg, rgba(218,142,53,0.55) ${primaryPct}%, rgba(255,255,255,0.10) ${primaryPct}%)`,
                  color: "#fff",
                  transition: "background 0.4s linear",
                }
              : {}),
            opacity: primaryDisabled && !upToDate ? 0.55 : upToDate ? 0.75 : 1,
          }}
        >
          {primaryLabel}
        </DialogButton>
        {/* What the install is doing, in words, directly under the button
            that is doing it. Michael went to the Downloads page to find out
            whether anything was happening at all - the answer belongs here. */}
        {progressNote !== "" && (
          <div
            style={{
              margin: "6px 2px 0",
              fontSize: "12px",
              opacity: 0.75,
              lineHeight: 1.4,
            }}
          >
            {progressNote}
          </div>
        )}
      <Focusable style={ACTION_ROW}>
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
                    // removeMod picks the mechanism: Frostbite games have
                    // to recompile their pack rather than delete a folder.
                    const result = await removeMod(game, installedCopy.folder);
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
      </Focusable>
      {blocked && (
        <WarningBox
          title="This mod cannot install yet"
          body={blocked}
          action={{
            label: "Manage my mods",
            onClick: () => {
              // So B on My Mods comes back HERE, ready to install again.
              markManagerReturn();
              pushOurPage("/nexus-mods/manager");
            },
          }}
        />
      )}
      {!blocked && stale && (
        <WarningBox title="Built for an older version of the game" body={stale} />
      )}
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
          <SectionHeading title="About" />
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
      {!isFrameworkMod && showAllFiles && (
        <SectionHeading title={`All files (${fileList.length})`} />
      )}
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
      </div>
      </Scroller>
    </Focusable>
  );
}
