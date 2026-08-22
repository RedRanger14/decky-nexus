// Full-screen collection view: the curated mod list with one-button
// sequential install through the per-game pipeline (order preserved -
// collections are ordered, and so is our plugin activation).
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
import { useEffect, useRef, useState } from "react";
import { FaArrowDown, FaEye, FaPuzzlePiece } from "react-icons/fa";
import { EndorsePill } from "./EndorseButton";
import { popOurPage, pushOurPage } from "./Tabs";
import {
  collectionOwnedCount,
  isActionableAttention,
  fileConflictProblem,
  isRemaining,

  preDisabledNote,
  directNote,
  isGoneFromNexus,
  isNetworkError,
  collectionRetryDelayMs,
  unavailableNote,} from "./panelRules";

import {
  endorseCollection,
  applyKnownPrerequisites,
  applyKnownVerdicts,
  installCollectionDirect,
  getCollectionExtras,
  getCollectionSupport,
  disableBlockedPlugins,
  getFileConflicts,
  installCollectionBundles,
  applyCollectionPlugins,
  recordCollectionInstalled,
  resolveFileConflicts,
  AttentionItem,
  CollectionDetail,
  CollectionFile,
  NexusMod,
  getCollection,
  getCollectionAttention,
  getUserPrefs,
  getCollectionManifest,
  getInstalledMods,
  getModDetails,
  installFomodAuto,
  registerCollection,
  setCollectionAttention,
  uninstallCollection,
  enforceSkips,
} from "./api";
import { PayloadChoiceModal } from "./ChoiceModal";
import { FomodWizardData, FomodWizardModal } from "./FomodWizard";
import { modeParams } from "./games";
import {
  finishFomod,
  installPinned,
  prefetchPinned,
  preparePinned,
} from "./install";
import { backAction } from "./navRules";
import {
  CollectionRowState,
  beginCollectionRun,
  dropDownload,
  endCollectionRun,
  getAggregateDownloadPercent,
  getCollectionRun,
  getDownloadPercent,
  getDownloads,
  getSelectedCollection,
  noteCollectionInstalled,
  setCollectionNote,
  setCollectionRow,
  setDetailOrigin,
  setSelectedMod,
  subscribeCollectionRun,
  subscribeDownloads,
  updateDownload,
} from "./state";
import {
  ACTION_BUTTON,
  ACTION_COLUMN,
  ACTION_HERO,
  ACTION_ROW,
  actionColumnWidth,
  BLUE_BUTTON_CLASS,
  PRIMARY_BUTTON_CLASS,
  PRIMARY_BUTTON_CSS,
  WHITE_BUTTON_CLASS,
} from "./theme";
import { PageBackdrop, SectionHeading, StackedThumb, StatChip } from "./chrome";
import { DownloadsButton } from "./DownloadsButton";

const Scroller: any = ScrollPanelGroup;

function fmtBytes(bytes: number): string {
  if (bytes >= 1 << 30) return `${(bytes / (1 << 30)).toFixed(1)} GB`;
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function CollectionPage() {
  const sel = getSelectedCollection();
  const [detail, setDetail] = useState<CollectionDetail | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [installedIds, setInstalledIds] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [modInfo, setModInfo] = useState<Record<number, NexusMod | null>>({});
  // Mods a previous run left needing manual choices - persisted so any
  // later visit can show and resolve them. The ref mirrors the state for
  // long-running async flows: installAll's final bookkeeping once used
  // its own stale copy and RESURRECTED wizards the user had already
  // resolved mid-run (the "kept having to install it" loop on video).
  const [attention, setAttention] = useState<AttentionItem[]>([]);
  const attentionRef = useRef<AttentionItem[]>([]);
  const [finishingFileId, setFinishingFileId] = useState<number | undefined>();
  // Finish-setup progress: which item, how far - the button narrates it.
  const [finishProgress, setFinishProgress] = useState<
    { done: number; total: number; name: string } | undefined
  >();
  // Mods installed via Finish setup this session. They leave the
  // attention queue as soon as they install, but installedIds is only
  // re-read at the end of the pass - without this they read as "still
  // to do" in between, and the remaining count climbs while the user is
  // actively clearing it. See isRemaining in panelRules.
  const [justResolved, setJustResolved] = useState<Set<number>>(new Set());
  // Endorsing a collection needs the download to be over 12 HOURS old,
  // not the 15 minutes mods use - so the most likely first press is one
  // Nexus refuses. The refusal is explained in words by the backend
  // rather than left as an error code.
  const [endorsing, setEndorsing] = useState(false);
  const [endorsed, setEndorsed] = useState(false);
  // Records installed BY this collection (its slug) - drives Uninstall.
  const [ownedModIds, setOwnedModIds] = useState<Set<number>>(new Set());
  // Uninstalling unmounts the focused button; without a stand-in the
  // gamepad focus dies and the next press falls through to Steam's
  // back-chain (reported as "closed the page and went back to the game").
  const [justUninstalled, setJustUninstalled] = useState(false);
  const [repairing, setRepairing] = useState(false);
  // Files where the wrong mod won. Not "conflicts" - this install shares
  // 10,362 paths deliberately; only disagreements with the collection's
  // order are worth a word. See fileConflictProblem.
  const [conflicts, setConflicts] = useState<
    | {
        files: number;
        pairs: number;
        list: { actual: string; intended: string; files: number }[];
        resolve: number[];
      }
    | undefined
  >();
  const [fixingFiles, setFixingFiles] = useState(false);
  // What the post-install pass is doing. It runs several backend steps -
  // one of which re-downloads the collection manifest - so the page sat
  // silent for a long time after Finish setup with no hint that anything
  // was happening, or that waiting was the right thing to do.
  const [finalising, setFinalising] = useState("");
  // Mods switched off before the first launch because this game build has
  // already been seen to fail on them.
  const [preDisabled, setPreDisabled] = useState<string[]>([]);
  // Mods this collection lists that Nexus will not serve any more.
  const [unavailable, setUnavailable] = useState<string[]>([]);
  // Files fetched from a URL the collection supplied rather than Nexus.
  const [directInstalled, setDirectInstalled] = useState<string[]>([]);
  // Known not to work here. Said before the download, not after - the TTW
  // collection is 42 GB and needs a conversion Gaming Mode cannot build.
  const [unsupported, setUnsupported] = useState("");
  // Mods the collection needs that are hosted off Nexus - no API can
  // fetch them, so the only honest thing is to name them.
  const [manualMods, setManualMods] = useState<
    { name: string; url: string; instructions: string; optional: boolean }[]
  >([]);
  // Batch state lives in a module store so navigating away and back
  // shows live progress instead of a stale page.
  const [, force] = useState(0);
  useEffect(() => subscribeCollectionRun(() => force((n) => n + 1)), []);
  // Live per-mod download percent drives the row fill while installing.
  useEffect(() => subscribeDownloads(() => force((n) => n + 1)), []);
  const run = getCollectionRun();
  const runIsOurs = run?.slug === sel?.collection.slug;
  const rowState: Record<number, CollectionRowState> = runIsOurs
    ? run!.rows
    : {};
  const installing = Boolean(runIsOurs && run!.running);

  // Six call sites fire this, several of them during a run, so two reads
  // are routinely in flight at once - and the responses can land in any
  // order. An older one landing last put the pre-install picture back:
  // Michael, after a clean 4-mod run, "after the button reverted back to
  // install required (5)". Leaving and reopening the page fixed it, which
  // is the tell - the records were right all along, the newest read just
  // lost a race. Only the latest read may write state.
  const refreshSeq = useRef(0);

  const refreshInstalled = () => {
    if (!sel) return;
    const seq = ++refreshSeq.current;
    const stale = () => seq !== refreshSeq.current;
    getInstalledMods(
      sel.game.nexusDomain,
      sel.game.installDirName,
      sel.game.modsSubdir,
      ...modeParams(sel.game),
      sel.game.protectedModFolders ?? []
    ).then((r) => {
      if (stale()) return;
      // Framework pins (REFramework, CET...) count as installed: Step 1
      // owns them, and their archives don't fit the mod pipeline anyway.
      const fwIds = [
        sel.game.framework?.nexusModId,
        ...(sel.game.framework?.aliasModIds ?? []),
        ...(sel.game.extraFrameworks ?? []).flatMap((fw) => [
          fw.nexusModId,
          ...(fw.aliasModIds ?? []),
        ]),
      ].filter((x): x is number => typeof x === "number");
      setInstalledIds(
        new Set([
          ...(r.mods ?? [])
            .map((m) => m.mod_id)
            .filter((id): id is number => id !== undefined),
          ...fwIds,
        ])
      );
      // Only records CARRYING this slug can be uninstalled by this
      // collection - shared/individual installs stay, so the button
      // must hide when none are left (it looked broken otherwise).
      setOwnedModIds(
        new Set(
          (r.mods ?? [])
            .filter((m) => m.collection_slug === sel.collection.slug)
            .map((m) => m.mod_id)
            .filter((id): id is number => id !== undefined)
        )
      );
    });
  };

  const refreshConflicts = () => {
    if (!sel || !detail) return;
    getFileConflicts(
      sel.game.nexusDomain,
      detail.files.map((f) => f.modId)
    )
      .then((r) => {
        if (!r.ok) return;
        setConflicts({
          files: r.files ?? 0,
          pairs: r.pairs ?? 0,
          list: r.conflicts ?? [],
          resolve: r.resolve ?? [],
        });
      })
      .catch(() => {});
  };

  useEffect(refreshConflicts, [detail, sel?.collection.slug]);

  // Loaded whenever the page opens, not only after an install. A mod that
  // cannot be downloaded here is a permanent fact about the collection -
  // it has to be visible when someone comes back to ask "why is this one
  // missing", which is exactly when they will look.
  useEffect(() => {
    if (!sel) return;
    let live = true;
    getCollectionSupport(
      sel.game.nexusDomain,
      sel.collection.slug,
      sel.game.appId,
      sel.game.installDirName
    )
      .then((r) => {
        if (live && r.ok && r.supported === false) {
          setUnsupported(r.reason ?? "");
        }
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [sel?.collection.slug, sel?.game.nexusDomain]);

  useEffect(() => {
    if (!sel) return;
    let live = true;
    getCollectionExtras(sel.collection.slug, sel.game.nexusDomain)
      .then((r) => {
        if (live && r.ok) setManualMods(r.browse ?? []);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [sel?.collection.slug, sel?.game.nexusDomain]);

  /** Rewrite each contested file from the mod the collection wanted to
   * own it. Per PATH: nothing uncontested is touched, so this cannot
   * create the new conflicts that reinstalling whole mods did. */
  const fixFileOwners = async () => {
    if (!detail || !conflicts?.files || fixingFiles) return;
    setFixingFiles(true);
    try {
      const r = await resolveFileConflicts(
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        detail.files.map((f) => f.modId),
        []
      );
      if (r.ok) {
        toaster.toast({
          title: `Fixed ${r.rewritten ?? 0} file${
            (r.rewritten ?? 0) === 1 ? "" : "s"
          }`,
          body: r.errors?.length
            ? `${r.errors.length} could not be fixed`
            : `From ${r.mods ?? 0} mod${(r.mods ?? 0) === 1 ? "" : "s"}`,
        });
      } else {
        toaster.toast({ title: "Could not fix", body: r.error ?? "" });
      }
    } finally {
      setFixingFiles(false);
      refreshConflicts();
    }
  };

  /** Apply what the collection's own manifest says, once its mods are in.
   *
   * Every one of these was being thrown away with the manifest we already
   * download for its FOMOD choices:
   *  - mods it ships itself, which need no download at all
   *  - the plugin list, because we switch on every plugin in every
   *    archive and a curator picks which should be ON (21 too many on
   *    device, past the engine's 254 limit, so the game refused to start)
   *  - mods hosted off Nexus that no API can fetch, which were skipped in
   *    silence and left the collection quietly broken
   */
  const runCollectionExtras = async () => {
    if (!sel) return;
    const { game, collection } = sel;
    setFinalising("Finishing off — this takes a minute, don't close the page");
    // Before the bundles: FOSE and its kind are the layer everything else
    // loads through, and Fallout Rebirth+ installed 168 mods without it and
    // then crashed on launch with nothing to look at.
    try {
      setFinalising("Downloading the files this collection links to…");
      const direct = await installCollectionDirect(
        collection.slug,
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        game.appId,
        game.pluginsTxtSubpath ?? "",
        game.pluginsTxtStyle ?? "starred"
      );
      if (direct.ok && (direct.names?.length ?? 0) > 0) {
        setDirectInstalled(direct.names ?? []);
      }
      for (const e of direct.errors ?? []) {
        toaster.toast({ title: "Could not install a linked file", body: e });
      }
    } catch {
      /* the extras note still names what is missing */
    }
    try {
      setFinalising("Installing the mods this collection ships itself…");
      const bundles = await installCollectionBundles(
        collection.slug,
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        game.appId,
        game.pluginsTxtSubpath ?? "",
        game.pluginsTxtStyle ?? "starred"
      );
      if (bundles.ok && (bundles.installed ?? 0) > 0) {
        toaster.toast({
          title: `${bundles.installed} bundled mod${
            bundles.installed === 1 ? "" : "s"
          } installed`,
          body: (bundles.mods ?? []).join(", "),
        });
      }
    } catch {
      /* a bundle failing must not fail the whole install */
    }
    if (game.pluginsTxtSubpath) {
      try {
        setFinalising("Setting the load order the collection asks for…");
        const pl = await applyCollectionPlugins(
          collection.slug,
          game.nexusDomain,
          game.installDirName,
          game.modsSubdir,
          game.appId,
          game.pluginsTxtSubpath,
          game.pluginsTxtStyle ?? "starred"
        );
        if (pl.ok && (pl.disabled ?? 0) > 0) {
          toaster.toast({
            title: `Load order set to the collection's ${pl.total ?? 0}`,
            body: `${pl.disabled} plugin${
              pl.disabled === 1 ? "" : "s"
            } the collection doesn't use were switched off`,
          });
        }
      } catch {
        /* leave the load order as installed rather than half-applied */
      }
    }
    // Mods that need a file Nexus does not host: off, not broken.
    try {
      setFinalising("Checking for mods that need a manual download…");
      await applyKnownPrerequisites(
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        game.appId,
        game.pluginsTxtSubpath ?? "",
        game.pluginsTxtStyle ?? "starred",
        collection.slug
      );
    } catch {
      /* nothing parked is the same as before this existed */
    }
    // LAST of the load-order steps, deliberately. A plugin whose master
    // never arrived stops the game starting with a modal naming one
    // file - and parking a mod above REMOVES its plugins, so orphans are
    // created by that step. Running this first swept a load order that
    // was about to change, which is how 'Immersion Mods Merged - FPGE
    // Patch.esp is missing required files' reached the user.
    if (game.pluginsTxtSubpath) {
      try {
        setFinalising("Switching off mods that can't load…");
        await disableBlockedPlugins(
          game.appId,
          game.installDirName,
          game.pluginsTxtSubpath,
          game.pluginsTxtStyle ?? "starred",
          game.nexusDomain
        );
      } catch {
        /* the load-order row still offers it manually */
      }
    }
    // Anything we have already watched fail on this exact game build goes
    // off now, before the first launch. Every earlier fix only worked AFTER
    // a crash had produced a log to read, so a reset and reinstall put the
    // same mod back and the game died on it again.
    try {
      setFinalising("Switching off mods this game version can't run…");
      const known = await applyKnownVerdicts(
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        game.installMode ?? "folder",
        game.appId,
        game.pluginsTxtSubpath ?? "",
        game.pluginsTxtStyle ?? "starred",
        game.recommendedModIds ?? []
      );
      if (known.ok && (known.disabled ?? 0) > 0) {
        setPreDisabled(known.names ?? []);
      }
    } catch {
      /* the panel still repairs it after the first launch */
    }
    try {
      const extras = await getCollectionExtras(
        collection.slug,
        game.nexusDomain
      );
      if (extras.ok) setManualMods(extras.browse ?? []);
    } catch {
      /* the note is a nicety; never block on it */
    }
    setFinalising("");
    toaster.toast({
      title: "Collection ready",
      body: "Launch the game from the panel or your library",
    });
  };

  /** Endorse the collection. One-way: see the button for why. */
  const endorseCollectionNow = async () => {
    if (!detail || endorsing || endorsed) return;
    setEndorsing(true);
    try {
      const r = await endorseCollection(detail.id, true);
      if (r.ok) {
        setEndorsed(true);
        toaster.toast({
          title: "Collection endorsed!",
          body: `Thanks for supporting ${detail.author}`,
        });
      } else {
        toaster.toast({ title: "Could not endorse", body: r.error ?? "" });
      }
    } finally {
      setEndorsing(false);
    }
  };

  const persistAttention = (items: AttentionItem[]) => {
    attentionRef.current = items;
    setAttention(items);
    if (sel) {
      setCollectionAttention(
        sel.game.nexusDomain,
        sel.collection.slug,
        items
      ).catch(() => {});
    }
  };

  // Re-check what's installed whenever a run STOPS (including runs that
  // finished while this page was away): the mount-time snapshot went
  // stale when the run banner cleared, and "Install remaining"
  // re-queued already-installed mods - including a 10 GB re-download.
  useEffect(() => {
    if (!installing) refreshInstalled();
  }, [installing]);

  useEffect(() => {
    if (!sel) return;
    getCollection(sel.collection.slug, sel.game.nexusDomain).then((r) => {
      if (r.ok && r.collection) {
        setDetail(r.collection);
        // Refresh an ALREADY-registered collection's stored info (title,
        // banner, member ids) - entries registered before mod_ids
        // existed undercount in My Mods until this runs.
        registerCollection(
          sel.game.nexusDomain,
          sel.collection.slug,
          sel.collection.name || r.collection.name,
          sel.collection.thumbnailUrl ?? "",
          r.collection.files.length,
          r.collection.files.map((f) => f.modId),
          true
        ).catch(() => {});
      } else setError(r.error ?? "Could not load collection");
    });
    getCollectionAttention(sel.game.nexusDomain, sel.collection.slug).then(
      (r) => {
        attentionRef.current = r.items ?? [];
        setAttention(r.items ?? []);
      }
    );
    refreshInstalled();
  }, []);

  if (!sel) {
    return (
      <div style={{ marginTop: "40px", padding: "24px" }}>
        No collection selected.
      </div>
    );
  }
  const { game, collection } = sel;

  const attentionIds = new Set(attention.map((a) => a.file_id));
  // Actionable = Finish setup can do something: choices/wizards get
  // their modals. Script conflicts are NOT retryable by default now -
  // the second mod is skipped to keep the game bootable (auto-merge
  // proved able to break boot), so they're a note, not an action.
  const actionable = attention.filter(isActionableAttention);
  const actionableIds = new Set(actionable.map((a) => a.file_id));
  const toolSkips = attention.filter((a) => a.reason === "tool");
  const emptySkips = attention.filter((a) => a.reason === "empty");
  // Only claim we switched things off when we actually did - this
  // collection's note said so while nothing had been parked.
  const parkedForExternal = attention.filter(
    (a) => a.reason === "needs_external"
  ).length;
  // Mods proven to stop THIS game booting on SteamOS. Not a failure to
  // retry and not something Finish setup can resolve - the collection is
  // usable without them and the page has to say which and why, or the
  // user is left with mods that are simply, silently absent.
  const brokenSkips = attention.filter((a) => a.reason === "incompatible");
  const conflictSkips = attention.filter((a) => a.reason === "conflict");
  const layoutSkips = attention.filter((a) => a.reason === "layout");

  // Entries, to match every other number on this page - see
  // collectionOwnedCount for why the record count read as 92 missing.
  const ownedCount = collectionOwnedCount(detail?.files, ownedModIds);
  const conflictIssue = conflicts
    ? fileConflictProblem(conflicts.files, conflicts.pairs, conflicts.list)
    : undefined;
  const required = detail?.files.filter((f) => !f.optional) ?? [];
  const optional = detail?.files.filter((f) => f.optional) ?? [];
  // Pending-attention mods are NOT "remaining": re-queueing them just
  // re-parks (or re-skips) them - they resolve via Finish setup instead.
  const remaining = required.filter((f) =>
    isRemaining(f, installedIds, rowState, attentionIds, justResolved)
  );
  const optionalRemaining = optional.filter((f) =>
    isRemaining(f, installedIds, rowState, attentionIds, justResolved)
  );
  // "Resume" only makes sense for a run THIS page started - already
  // owning some of a collection's mods individually is not a resume.
  const partialFromRun = runIsOurs && !run!.running && run!.finished > 0;
  // Actually-installed count (skipped tools are NOT installed - the old
  // required-minus-remaining math counted them and overstated).
  const installedRequiredCount = required.filter(
    (f) =>
      installedIds.has(f.modId) ||
      rowState[f.fileId] === "done" ||
      justResolved.has(f.fileId)
  ).length;

  // Secondary buttons actually rendered below the hero: the hero takes
  // its width from this, so the two rows always share an edge.
  const secondaryActions =
    2 + // Go to downloads, Back
    (actionable.length > 0 ? 1 : 0) +
    (optionalRemaining.length > 0 ? 1 : 0) +
    (conflictIssue && !installing ? 1 : 0) + // Fix contested files
    (ownedCount > 0 && !installing ? 1 : 0) + // Repair
    ((ownedCount > 0 && !installing) || (justUninstalled && !installing)
      ? 1
      : 0);

  /** Re-run the mods that install through a FOMOD, restoring any files
   * that never made it. A destination of "." in a FOMOD used to have
   * every one of its files dropped by the traversal guard, so mods
   * reported success having installed only part of themselves - one of
   * them took 15 of its 68 files and left 79 other mods without the
   * master they needed. Nothing recorded that, so the only way to find
   * it is to stage each installer again and see what's absent.
   *
   * Files already on disk are left ALONE: a file that is present is
   * either this mod's or a later mod's deliberate override, and
   * re-asserting it would undo the collection's conflict order. */
  const repairInstallers = async () => {
    if (!detail || installing || repairing) return;
    // Mods parked for a script conflict get another go. They were skipped
    // because two mods edited the same script and merging was off; merging
    // is on now, and the same 30 mods installed on the next attempt. Until
    // this, nothing re-offered them - the only way back was clearing the
    // parked list by hand over SSH, which is not a thing a user can do.
    // Repair is the right place: it is the button for "try again".
    const parkedConflicts = attentionRef.current.filter(
      (a) => a.reason === "conflict"
    );
    if (parkedConflicts.length) {
      persistAttention(
        attentionRef.current.filter((a) => a.reason !== "conflict")
      );
    }
    setRepairing(true);
    let checked = 0;
    let repaired = 0;
    let restored = 0;
    let failed = 0;
    try {
      const manifest = await getCollectionManifest(
        collection.slug,
        game.nexusDomain
      );
      const choices = (manifest.ok ? manifest.choices : {}) ?? {};
      // Only mods with recorded installer choices can have hit this -
      // everything else took the plain payload path.
      const queue = detail.files.filter(
        (f) => choices[String(f.fileId)] !== undefined
      );
      beginCollectionRun(collection.slug, queue.length, {
        gameAppId: game.appId,
        name: `Repairing ${collection.name}`,
        thumbnailUrl: collection.thumbnailUrl,
      });
      for (const f of queue) {
        if (f.domain && f.domain !== game.nexusDomain) continue;
        checked += 1;
        setCollectionRow(f.fileId, "installing");
        try {
          let result = await installPinned(
            game,
            f.modId,
            f.fileId,
            f.fileName,
            f.modName,
            f.version,
            collection.slug,
            "",
            true
          );
          if (result.needs_fomod && result.fomod_token) {
            const picked = choices[String(f.fileId)];
            if (picked !== undefined) {
              result = await installFomodAuto(result.fomod_token, picked);
            }
          }
          if (result.ok) {
            const added = result.added ?? 0;
            if (added > 0) {
              repaired += 1;
              restored += added;
            }
            setCollectionRow(f.fileId, "done");
          } else {
            failed += 1;
            setCollectionRow(f.fileId, "failed");
          }
        } catch {
          failed += 1;
          setCollectionRow(f.fileId, "failed");
        }
        dropDownload(f.modId);
      }
    } finally {
      // The finishing pass an install does: bundled mods, the ini patches,
      // and - the reason this is here - the load order the collection
      // asks for. A collection installed before that existed never got
      // it, and there is otherwise no way to apply it without
      // reinstalling: Michael had no Finish setup to press, because
      // nothing was pending, while the load order was still whatever
      // install order produced. Repair is the recovery path for an
      // already-installed collection, so it belongs here.
      try {
        await runCollectionExtras();
      } catch {
        /* a repair that fixed files must not report failure over this */
      }
      endCollectionRun();
      setRepairing(false);
      refreshInstalled();
      toaster.toast({
        title:
          repaired > 0
            ? `Repaired ${repaired} mod${repaired > 1 ? "s" : ""}`
            : "Nothing needed repairing",
        body:
          repaired > 0
            ? `${restored} missing file${
                restored > 1 ? "s" : ""
              } restored across ${checked} checked${
                failed > 0 ? ` · ${failed} couldn't be checked` : ""
              }`
            : `All ${checked} installer-based mods were already complete`,
        duration: 12000,
      });
    }
  };

  const installAll = async (includeOptional = false) => {
    if (!detail || installing) return;
    const queue = includeOptional
      ? [...remaining, ...optionalRemaining]
      : remaining;
    beginCollectionRun(collection.slug, queue.length, {
      gameAppId: game.appId,
      name: collection.name,
      thumbnailUrl: collection.thumbnailUrl,
    });
    // My Mods groups these installs under the collection - remember its
    // display info (records only carry the slug).
    registerCollection(
      game.nexusDomain,
      collection.slug,
      collection.name,
      collection.thumbnailUrl ?? "",
      detail.files.length,
      detail.files.map((f) => f.modId),
      false
    ).catch(() => {});
    const freshAttention: AttentionItem[] = [];
    try {
      // The curator's FOMOD selections travel in the collection manifest -
      // fetch once so wizard mods install hands-off with their choices.
      let curatorChoices: Record<string, unknown> = {};
      setCollectionNote("Reading the collection…");
      try {
        const manifest = await getCollectionManifest(
          collection.slug,
          game.nexusDomain
        );
        if (manifest.ok) curatorChoices = manifest.choices ?? {};
      } catch {
        // Manifest is an enhancement - never let it stall the batch.
      }
      let failures = 0;
      // Mods the collection lists that Nexus no longer serves. Tracked
      // apart from failures because nobody can act on them.
      const unavailable: string[] = [];
      // ---- download-ahead pipeline -------------------------------------
      // Installs are serial (they mutate game dirs and share plugins.txt),
      // but the network needn't idle while each mod extracts: prefetch up
      // to 4 files in parallel, never more than 8 ahead of the installer
      // (bounds disk usage to a handful of archives). The installer's own
      // download step then hits the backend's archive cache instantly.
      setCollectionNote("Starting downloads…");
      const prefs = await getUserPrefs().catch(() => undefined);
      const PREFETCH_PARALLEL = prefs?.prefs?.parallel_downloads ?? 4;
      const PREFETCH_WINDOW = prefs?.prefs?.prefetch_window ?? 8;
      // The nearest few mods are EXTRACTED as well as downloaded, so the
      // serial installer only has to commit them. Extraction shares
      // nothing, but each prepared mod is an unpacked tree on disk, so
      // the window is small. 0 turns it off and restores the old
      // download-only behaviour exactly.
      // How many times a mod is re-attempted when the NETWORK is what failed.
// Three tries at 5s, 10s and 20s covers a wifi reconnect; past that the
// link is genuinely down and the honest move is to stop the run rather
// than spend the rest of the queue discovering the same thing.
const NETWORK_RETRIES = 3;

const EXTRACT_AHEAD = prefs?.prefs?.extract_ahead ?? 2;
      let installIndex = 0;
      let nextPrefetch = 0;
      const inflight = new Map<number, Promise<void>>();
      const pump = () => {
        while (
          nextPrefetch < queue.length &&
          nextPrefetch < installIndex + PREFETCH_WINDOW &&
          inflight.size < PREFETCH_PARALLEL
        ) {
          const idx = nextPrefetch++;
          const p = queue[idx];
          // Cross-domain pins get skipped by the installer - don't waste
          // bandwidth on them.
          if (p.domain && p.domain !== game.nexusDomain) continue;
          // Only the mods about to be installed are worth unpacking;
          // the rest just get their bytes fetched.
          const fetcher =
            idx < installIndex + EXTRACT_AHEAD ? preparePinned : prefetchPinned;
          const promise = fetcher(
            game,
            p.modId,
            p.fileId,
            p.fileName,
            p.modName
          ).finally(() => {
            inflight.delete(idx);
            pump();
          });
          inflight.set(idx, promise);
        }
      };
      pump();

      // Set when the run gives up because the network went away, so the
      // summary can say so instead of reporting phantom failures.
      let networkStopped = false;
      for (let qi = 0; qi < queue.length; qi++) {
        const f = queue[qi];
        installIndex = qi;
        pump();
        // One mod must never kill the batch: a thrown transport error
        // used to abandon the whole remaining queue (87 of 99 left).
        try {
          if (f.domain && f.domain !== game.nexusDomain) {
            // Cross-domain pin (Bethini Pie lives under "site"): a
            // desktop utility this game can never load - skip for good.
            setCollectionRow(f.fileId, "skipped");
            freshAttention.push({
              file_id: f.fileId,
              mod_id: f.modId,
              mod_name: f.modName,
              file_name: f.fileName,
              version: f.version,
              reason: "tool",
              options: [],
            });
            continue;
          }
          // Never race the prefetcher on this file's archive - let an
          // in-flight download finish before installing it.
          const pending = inflight.get(qi);
          if (pending) await pending;
          setCollectionRow(f.fileId, "installing");
          let result = await installPinned(
            game,
            f.modId,
            f.fileId,
            f.fileName,
            f.modName,
            f.version,
            collection.slug
          );
          // A dropped connection says nothing about the mod, so it must
          // not cost the mod its place in the queue. Michael's wifi went
          // down mid-run and 47 mods failed on DNS in five minutes, then
          // sat on the button as "still to install" with no reason
          // attached - exactly the diagnosis work a console player should
          // never be handed.
          for (
            let attempt = 1;
            attempt <= NETWORK_RETRIES && !result.ok &&
              isNetworkError(result.error);
            attempt++
          ) {
            const waitMs = collectionRetryDelayMs(attempt);
            updateDownload(
              f.modId,
              "downloading",
              0,
              undefined,
              undefined,
              undefined,
              `connection lost - retrying in ${Math.round(waitMs / 1000)}s`
            );
            await new Promise((r) => setTimeout(r, waitMs));
            result = await installPinned(
              game,
              f.modId,
              f.fileId,
              f.fileName,
              f.modName,
              f.version,
              collection.slug
            );
          }
          if (!result.ok && isNetworkError(result.error)) {
            // Still down after the retries. STOP, rather than spending
            // the rest of the queue fifteen seconds at a time: every one
            // of these would fail the same way, and a run that ends
            // "finished" with 47 unexplained leftovers is worse than one
            // that says the connection went and it is waiting.
            dropDownload(f.modId);
            // Back to pending, NOT failed: nothing is wrong with this mod
            // and "Install remaining" must pick it up untouched once the
            // connection returns.
            setCollectionRow(f.fileId, "pending");
            networkStopped = true;
            break;
          }
          if (result.needs_fomod && result.fomod_token) {
            const choices = curatorChoices[String(f.fileId)];
            if (choices !== undefined) {
              // The curator recorded selections - install hands-off.
              result = await installFomodAuto(result.fomod_token, choices);
            }
            // No curator choices: fall through with needs_fomod set -
            // the mod parks under "needs choices" for Finish setup
            // instead of silently taking wizard defaults.
          }
          if (result.ok) {
            noteCollectionInstalled(f.modId);
            setCollectionRow(f.fileId, "done");
          } else if (result.needs_choice || result.needs_fomod) {
            // Manual decisions pending - remembered (persisted) so the
            // "Finish setup" button can resolve them all in one pass.
            dropDownload(f.modId);
            setCollectionRow(f.fileId, "skipped");
            freshAttention.push({
              file_id: f.fileId,
              mod_id: f.modId,
              mod_name: f.modName,
              file_name: f.fileName,
              version: f.version,
              reason: result.needs_fomod ? "fomod" : "choices",
              options: result.options ?? [],
            });
          } else if (result.nothing_staged) {
            dropDownload(f.modId);
            setCollectionRow(f.fileId, "skipped");
            freshAttention.push({
              file_id: f.fileId,
              mod_id: f.modId,
              mod_name: f.modName,
              file_name: f.fileName,
              version: f.version,
              reason: "empty",
              options: [],
            });
          } else if (result.unsupported_layout) {
            // Retrying can't change an unrecognized archive layout -
            // park it (the refusal log carries the shape for us to fix).
            dropDownload(f.modId);
            setCollectionRow(f.fileId, "skipped");
            freshAttention.push({
              file_id: f.fileId,
              mod_id: f.modId,
              mod_name: f.modName,
              file_name: f.fileName,
              version: f.version,
              reason: "layout",
              options: [],
            });
            toaster.toast({
              title: `${f.modName}: not installable - skipped`,
              body: result.error ?? "",
            });
          } else if (result.unsupported_tool) {
            // Desktop tools (xEdit, patchers) aren't failures - the
            // game never loads them; they just can't live here. Persist
            // the skip so they stop counting as "remaining" forever.
            dropDownload(f.modId);
            setCollectionRow(f.fileId, "skipped");
            freshAttention.push({
              file_id: f.fileId,
              mod_id: f.modId,
              mod_name: f.modName,
              file_name: f.fileName,
              version: f.version,
              reason: "tool",
              options: [],
            });
            // (no per-mod toast: the summary counts skips and the row shows why)
          } else if (result.stale_skip) {
            // Built before the game's current patch: it would install and
            // then hang the game on a "Could not find signature!" box. Not
            // a failure, and not the user's problem to diagnose - so it is
            // skipped, named, and left visible in the attention list.
            dropDownload(f.modId);
            setCollectionRow(f.fileId, "skipped");
            freshAttention.push({
              file_id: f.fileId,
              mod_id: f.modId,
              mod_name: f.modName,
              file_name: f.fileName,
              version: f.version,
              reason: "older-game",
              options: [],
            });
            // (no per-mod toast: the summary counts skips and the row shows why)
          } else if (result.script_conflict || result.mod_conflict) {
            // Conflicts with something already installed: parking it
            // keeps the button honest ("everything installed" when only
            // these remain) instead of offering an install that can only
            // re-refuse until the user resolves the clash.
            dropDownload(f.modId);
            setCollectionRow(f.fileId, "skipped");
            freshAttention.push({
              file_id: f.fileId,
              mod_id: f.modId,
              mod_name: f.modName,
              file_name: f.fileName,
              version: f.version,
              reason: "conflict",
              options: [],
            });
            toaster.toast({
              title: `${f.modName}: conflict - skipped`,
              body: result.error ?? "",
            });
          } else if (isGoneFromNexus(result.error)) {
            // Not a failure of ours or theirs. A collection outlives the
            // mods in it: authors delete things and Nexus takes things
            // down for review, and the curator has not caught up yet.
            // Counting it as "failed" made Michael go looking for a
            // Premium problem on a Premium account.
            unavailable.push(f.modName);
            setCollectionRow(f.fileId, "skipped");
            updateDownload(f.modId, "done", 100);
            // PERSISTED, or it is only skipped until the page reloads.
            // Vault Boy 101 finished with "4 remaining" that were all
            // deleted mods: the skip lived in React state, so every fresh
            // look at the collection offered them again, and the count
            // could never reach zero. Nothing the user can do about a mod
            // that no longer exists, so it is a permanent skip like a PC
            // tool rather than something for Finish setup.
            freshAttention.push({
              file_id: f.fileId,
              mod_id: f.modId,
              mod_name: f.modName,
              file_name: f.fileName,
              version: f.version,
              reason: "unavailable",
              options: [],
            });
          } else {
            failures += 1;
            setCollectionRow(f.fileId, "failed");
            updateDownload(f.modId, "error", 0);
            toaster.toast({
              title: `${f.modName} failed`,
              body: result.error ?? "",
            });
          }
        } catch (e) {
          failures += 1;
          setCollectionRow(f.fileId, "failed");
          updateDownload(f.modId, "error", 0);
          toaster.toast({
            title: `${f.modName} failed`,
            body: String(e),
          });
        }
      }
      // Carry forward older pending choices that this run didn't touch;
      // everything re-attempted is superseded by freshAttention. MUST
      // read the live ref: the user may have resolved items via Finish
      // setup while this batch was still running.
      persistAttention([
        ...attentionRef.current.filter(
          (a) => !queue.some((f) => f.fileId === a.file_id)
        ),
        ...freshAttention,
      ]);
      // The three things the collection tells us that a plain per-mod
      // install cannot know. Run at the END of the batch because they
      // work on the finished set, not on one mod at a time.
      await runCollectionExtras();
      refreshInstalled();
      // Note WHEN this landed and what the playtime was, so "played since"
      // can be measured later. Recorded even when the run stopped short -
      // the entry is a starting point for evidence, not a claim that
      // anything works. Only playing earns the badge.
      //
      // But NOT when the run installed nothing. EldenBoobs skipped all 16
      // of its mods, was recorded anyway, and then an Elden Ring session
      // promoted an empty install to VERIFIED ON DECK. Michael: "Why has
      // Elden boobs been given a verifed badge when we havent done a
      // successful install confirmation?" Playtime cannot be evidence for
      // mods that were never put on the device.
      if (installedRequiredCount > 0)
      recordCollectionInstalled(
        game.nexusDomain,
        collection.slug,
        game.appId,
        collection.name,
        installedRequiredCount
      ).catch(() => undefined);
      // Only actionable items belong in "waiting on your choices" -
      // tools/conflicts/unrecognized archives are permanent skips and
      // used to make this toast promise a Finish setup that never came.
      const needsChoices = freshAttention.filter(isActionableAttention).length;
      const skipped = freshAttention.length - needsChoices;
      const bits = [];
      if (unavailable.length > 0) setUnavailable(unavailable);
      if (failures > 0) bits.push(`${failures} failure(s)`);
      if (networkStopped) bits.push("stopped - connection lost");
      if (unavailable.length > 0)
        bits.push(`${unavailable.length} no longer on Nexus`);
      if (needsChoices > 0)
        bits.push(`${needsChoices} waiting on your choices (Finish setup)`);
      if (skipped > 0) bits.push(`${skipped} skipped (see notes)`);
      toaster.toast({
        title: `${collection.name}`,
        body:
          bits.length === 0
            ? "Collection installed - restart the game to load it"
            : `Done: ${bits.join(", ")}`,
      });
    } finally {
      // The per-install check only sees a mod's own masters at the moment
      // it installs, so a mod installed BEFORE its master was skipped is
      // never reconsidered. One slipped through exactly that way on a
      // clean 1,972-mod run, and the user had to be told to fix it.
      if (game.pluginsTxtSubpath) {
        await enforceSkips(
          game.appId,
          game.installDirName,
          game.pluginsTxtSubpath,
          game.pluginsTxtStyle ?? "starred",
          game.nexusDomain
        );
      }
      endCollectionRun();
    }
  };

  /** Modal helpers that resolve as promises so Finish setup can walk
   * every pending mod sequentially. closeModal resolves undefined a tick
   * later than onPick - the pick wins when both fire. */
  const pickChoice = (name: string, options: string[], labels?: string[]) =>
    new Promise<string | undefined>((resolve) => {
      const modal = showModal(
        <PayloadChoiceModal
          modName={name}
          options={options}
          labels={labels}
          // Merging is real everywhere: HD2 folders renumber into their
          // own patch slots (a weapons pack is a SET, not alternatives).
          allowMerge={true}
          onPick={(o) => resolve(o)}
          closeModal={() => {
            modal.Close();
            setTimeout(() => resolve(undefined), 0);
          }}
        />
      );
    });

  const runWizard = (wizard: FomodWizardData) =>
    new Promise<string[] | undefined>((resolve) => {
      const modal = showModal(
        <FomodWizardModal
          wizard={wizard}
          onInstall={(ids) => resolve(ids)}
          closeModal={() => {
            modal.Close();
            setTimeout(() => resolve(undefined), 0);
          }}
        />
      );
    });

  /** Resolve ONE pending manual decision: re-install to the decision
   * point, show its modal, finish. "backout" = the user closed the
   * modal - the item stays pending AND the caller must stop prompting. */
  const resolveAttentionItem = async (
    item: AttentionItem
  ): Promise<"installed" | "backout" | "failed" | "empty"> => {
    setFinishingFileId(item.file_id);
    try {
      let choice = "";
      if (item.reason === "choices" && item.options.length > 0) {
        const picked = await pickChoice(
          item.mod_name, item.options, item.option_labels
        );
        if (picked === undefined) return "backout";
        choice = picked;
      }
      let result = await installPinned(
        game,
        item.mod_id,
        item.file_id,
        item.file_name,
        item.mod_name,
        item.version,
        collection.slug,
        choice
      );
      if (result.needs_fomod && result.fomod_token && result.wizard) {
        const ids = await runWizard(result.wizard as FomodWizardData);
        if (ids === undefined) {
          dropDownload(item.mod_id);
          return "backout";
        }
        result = await finishFomod(result.fomod_token, ids);
      } else if (result.needs_choice && result.options?.length) {
        const picked = await pickChoice(
          item.mod_name, result.options, result.option_labels
        );
        if (picked === undefined) {
          dropDownload(item.mod_id);
          return "backout";
        }
        result = await installPinned(
          game,
          item.mod_id,
          item.file_id,
          item.file_name,
          item.mod_name,
          item.version,
          collection.slug,
          picked
        );
      }
      if (result.ok) return "installed";
      // An installer with nothing to install is answered, not failed.
      // Asking again can only produce the same nothing, and leaving it in
      // the queue makes Finish setup look permanently unfinished.
      if (result.nothing_staged) {
        updateDownload(item.mod_id, "done", 100);
        toaster.toast({
          title: `${item.mod_name}: nothing to install`,
          body: "Skipped - see the note on the collection",
        });
        return "empty";
      }
      updateDownload(item.mod_id, "error", 0);
      toaster.toast({
        title: `${item.mod_name} failed`,
        body: result.error ?? "",
      });
      return "failed";
    } catch (e) {
      updateDownload(item.mod_id, "error", 0);
      toaster.toast({ title: `${item.mod_name} failed`, body: String(e) });
      return "failed";
    } finally {
      setFinishingFileId(undefined);
    }
  };

  /** Resolve every pending manual decision in one guided pass. Backing
   * out of any modal ends the WHOLE pass - B means "not now", not
   * "next question please". */
  const finishSetup = async () => {
    if (!detail || finishingFileId !== undefined) return;
    const queue = [...actionable];
    let done = 0;
    try {
      for (const item of queue) {
        setFinishProgress({ done, total: queue.length, name: item.mod_name });
        const outcome = await resolveAttentionItem(item);
        if (outcome === "backout") break;
        done++;
        if (outcome === "installed" || outcome === "empty") {
          setJustResolved((prev) => new Set(prev).add(item.file_id));
          persistAttention([
            ...attentionRef.current.filter(
              (a) => a.file_id !== item.file_id
            ),
            // Kept on the collection as a named skip so the mod is not
            // silently absent - it just stops being a question.
            ...(outcome === "empty"
              ? [{ ...item, reason: "empty", options: [] }]
              : []),
          ]);
        }
      }
    } finally {
      setFinishProgress(undefined);
    }
    // The collection is only complete once the wizards are answered, so
    // the manifest steps belong here too - a mod resolved by Finish setup
    // brings its own plugins, and the plugin list has to account for them.
    await runCollectionExtras();
    refreshInstalled();
  };

  /** One row's "Make choices & install" - usable even while the batch
   * is still working through other mods. */
  const resolveSingle = async (item: AttentionItem) => {
    if (finishingFileId !== undefined) return;
    const outcome = await resolveAttentionItem(item);
    if (outcome === "installed" || outcome === "empty") {
      setJustResolved((prev) => new Set(prev).add(item.file_id));
      persistAttention([
        ...attentionRef.current.filter((a) => a.file_id !== item.file_id),
        ...(outcome === "empty"
          ? [{ ...item, reason: "empty", options: [] }]
          : []),
      ]);
      refreshInstalled();
    }
  };

  /** Eye button: open the mod's full detail page (fetching details if
   * the accordion hasn't loaded them yet). */
  const openModPage = async (f: CollectionFile) => {
    let info = modInfo[f.modId];
    if (!info) {
      const r = await getModDetails(game.nexusDomain, f.modId);
      info = r.ok ? r.mod ?? null : null;
    }
    if (!info) {
      toaster.toast({ title: "Could not open mod", body: f.modName });
      return;
    }
    setSelectedMod({ game, mod: info });
    setDetailOrigin("browse");
    pushOurPage("/nexus-mods/mod");
  };

  const onUninstallCollection = () => {
    showModal(
      <ConfirmModal
        strTitle={`Uninstall ${collection.name}?`}
        strDescription="Removes the mods this collection installed. Mods you installed yourself (or via another collection) stay."
        strOKButtonText="Uninstall collection"
        bDestructiveWarning={true}
        onOK={async () => {
          const result = await uninstallCollection(
            game.nexusDomain,
            game.installDirName,
            game.modsSubdir,
            ...modeParams(game),
            collection.slug
          );
          toaster.toast(
            result.ok
              ? {
                  title: `${collection.name} uninstalled`,
                  body: `${result.removed ?? 0} mods removed`,
                }
              : { title: "Uninstall failed", body: result.error ?? "" }
          );
          if (result.ok) setJustUninstalled(true);
          persistAttention([]);
          refreshInstalled();
        }}
      />
    );
  };

  const toggleExpand = (f: CollectionFile) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(f.fileId)) {
        next.delete(f.fileId);
      } else {
        next.add(f.fileId);
        if (!(f.modId in modInfo)) {
          getModDetails(game.nexusDomain, f.modId).then((r) =>
            setModInfo((m) => ({ ...m, [f.modId]: r.ok ? r.mod ?? null : null }))
          );
        }
      }
      return next;
    });
  };

  const stateBadge = (f: CollectionFile): string => {
    if (
      installedIds.has(f.modId) ||
      rowState[f.fileId] === "done" ||
      justResolved.has(f.fileId)
    )
      return "✓ ";
    if (actionableIds.has(f.fileId)) return "⚙ ";
    if (attentionIds.has(f.fileId)) {
      const reason = attention.find((a) => a.file_id === f.fileId)?.reason;
      if (reason === "conflict") return "🔒 ";
      // Left out on purpose, and it must not read as done. A tick on a
      // mod we chose not to install is the least honest mark available.
      // Michael: "why not have some icon for skip instead of the ticks in
      // the mod list... The toast is too fast."
      if (reason === "older-game") return "⚠ ";
      return reason === "incompatible" ? "⚠ " : "⏭ ";
    }
    const st = rowState[f.fileId];
    if (st === "installing") return "";
    if (st === "failed") return "⚠ ";
    if (st === "skipped") return "⏭ ";
    return "";
  };

  return (
    <Focusable
      onCancel={() => {
        // This page is always PUSHED on top of another (store home,
        // downloads) - B pops back there. Opening the QAM here trapped
        // users in a B-loop (see navRules + tests/nav.test.mjs).
        if (backAction("collection") === "pop") {
          popOurPage();
        } else {
          Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);
          setTimeout(() => Navigation.NavigateBack(), 50);
        }
      }}
      style={{ marginTop: "40px", height: "calc(100% - 40px)" }}
    >
      <Scroller
        focusable={false}
        style={{ height: "100%", overflowY: "auto", padding: "0 24px 110px", scrollPaddingBottom: "110px" }}
      >
        <style>{PRIMARY_BUTTON_CSS + `
        @keyframes nexusFinishPulse {
          0%, 100% { filter: brightness(1); }
          50% { filter: brightness(1.45); }
        }
      `}</style>
        {/* Collection art as blurred atmosphere behind the header. */}
        <PageBackdrop src={collection.thumbnailUrl} height={250} />
        <div style={{ position: "relative", zIndex: 1 }}>
        <Focusable style={{ display: "flex", gap: "20px", padding: "12px 0" }}>
          {/* The same stacked-card language as the store tiles, at header
              scale: this page is a deck of mods, and looks like one. */}
          <StackedThumb
            src={collection.thumbnailUrl}
            width={180}
            height={200}
            peek={9}
            radius={8}
            fit="contain"
          />
          <div style={{ minWidth: 0, alignSelf: "center" }}>
            <h2
              style={{
                margin: "0 0 3px 0",
                fontSize: "24px",
                lineHeight: 1.2,
                fontWeight: 700,
              }}
            >
              {collection.name}
            </h2>
            <div style={{ opacity: 0.75, fontSize: "13.5px", marginBottom: "10px" }}>
              a collection by {detail?.author ?? collection.author} ·{" "}
              {game.displayName}
            </div>
            <Focusable
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: "8px",
                marginBottom: "10px",
              }}
            >
              <StatChip icon={<FaPuzzlePiece size={11} />}>
                {detail ? detail.files.length : collection.modCount} mods
              </StatChip>
              <StatChip icon={<FaArrowDown size={11} />}>
                {fmtBytes(detail ? detail.totalSize : collection.totalSize)}
              </StatChip>
              {installedRequiredCount > 0 && (
                <StatChip>✓ {installedRequiredCount} installed</StatChip>
              )}
            </Focusable>
            {unsupported && (
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
                {/* whiteSpace preserves the blank lines around the
                    curator's own quoted instruction - run together as one
                    paragraph it reads as our words rather than theirs. */}
                <span style={{ whiteSpace: "pre-wrap" }}>
                  ⚠ This collection isn't supported on SteamOS. {unsupported}
                </span>
              </div>
            )}
            {conflictIssue && (
              <div
                style={{
                  marginBottom: "10px",
                  padding: "8px 10px",
                  borderRadius: "4px",
                  fontSize: "12px",
                  lineHeight: 1.45,
                  background: "rgba(218, 142, 53, 0.12)",
                  border: "1px solid rgba(218, 142, 53, 0.4)",
                }}
              >
                {conflictIssue}
              </div>
            )}
            {ownedCount > 0 && detail && (
              <div style={{ marginBottom: "10px", display: "flex" }}>
                <EndorsePill
                  endorsed={endorsed}
                  busy={endorsing}
                  onActivate={endorseCollectionNow}
                  label="Endorse collection"
                  endorsedLabel="Collection endorsed"
                />
              </div>
            )}
            {(detail?.summary ?? collection.summary) && (
              <div style={{ fontSize: "13px", opacity: 0.9, lineHeight: 1.5 }}>
                {detail?.summary ?? collection.summary}
              </div>
            )}
          </div>
        </Focusable>

        {/* Install spans exactly the buttons beneath it - the column sets
            one width and both rows fill it. While a run is live it is
            also the progress bar, same fill language as the mod rows. */}
        <Focusable
          autoFocus={true}
          style={{
            ...ACTION_COLUMN,
            margin: "6px 0 14px",
            maxWidth: actionColumnWidth(secondaryActions),
          }}
        >
          <DialogButton
            className={installing ? undefined : PRIMARY_BUTTON_CLASS}
            disabled={!detail || installing || remaining.length === 0}
            onClick={() => installAll(false)}
            style={{
              ...ACTION_HERO,
              ...(installing
                ? {
                    background: `linear-gradient(90deg, rgba(218,142,53,0.55) ${
                      getAggregateDownloadPercent(run) ?? 0
                    }%, rgba(255,255,255,0.10) ${
                      getAggregateDownloadPercent(run) ?? 0
                    }%)`,
                    color: "#fff",
                    transition: "background 0.4s linear",
                  }
                : {}),
            }}
          >
            {finalising
              ? finalising
              : installing
              ? // The count is COMPLETED INSTALLS, which sits at 0 for a
                // long time on a big collection while several gigabytes
                // download - so it read as stuck. The percentage includes
                // download progress, so something always moves.
                runIsOurs && run!.finished === 0 && run!.note
                  ? run!.note
                  : `Installing… ${runIsOurs ? run!.finished : 0}/${
                      runIsOurs ? run!.total : remaining.length
                    } · ${getAggregateDownloadPercent(run) ?? 0}%`
              : remaining.length === 0 && detail
              ? "Everything installed ✓"
              : partialFromRun
              ? `Resume collection (${remaining.length} left)`
              : detail && remaining.length < required.length
              ? `Install remaining (${remaining.length} of ${required.length})`
              : `Install required (${remaining.length})`}
          </DialogButton>
          <Focusable style={ACTION_ROW}>
          {actionable.length > 0 && (
            <DialogButton
              className={BLUE_BUTTON_CLASS}
              disabled={finishingFileId !== undefined}
              onClick={finishSetup}
              style={{
                ...ACTION_BUTTON,
                animation:
                  finishingFileId !== undefined
                    ? "nexusFinishPulse 1.4s ease-in-out infinite"
                    : undefined,
              }}
            >
              {finishProgress
                ? `⚙ Finishing ${Math.min(
                    finishProgress.done + 1,
                    finishProgress.total
                  )}/${finishProgress.total}…`
                : finishingFileId !== undefined
                ? "⚙ Finishing…"
                : `⚙ Finish setup (${actionable.length})`}
            </DialogButton>
          )}
          {optionalRemaining.length > 0 && (
            <DialogButton
              className={WHITE_BUTTON_CLASS}
              disabled={!detail || installing}
              onClick={() => installAll(true)}
              style={ACTION_BUTTON}
            >
              {remaining.length === 0
                ? `Install optional (${optionalRemaining.length})`
                : `+ optional (${optionalRemaining.length})`}
            </DialogButton>
          )}
          {conflictIssue && !installing && (
            <DialogButton
              className={BLUE_BUTTON_CLASS}
              disabled={fixingFiles || finishingFileId !== undefined}
              onClick={fixFileOwners}
              style={ACTION_BUTTON}
            >
              {fixingFiles
                ? "Fixing…"
                : `Fix ${conflicts!.files.toLocaleString()} file${
                    conflicts!.files === 1 ? "" : "s"
                  }`}
            </DialogButton>
          )}
          {/* One-way on purpose: the API has no viewer-endorsement field
              for collections (PR open against nexus-api), so a toggle
              would be guesswork and guessing wrong records an abstention
              over somebody's existing endorsement.
              Shown whenever the collection is installed, not hidden in the
              stat chips where it read as a label rather than a button. */}
          {ownedCount > 0 && !installing && (
            <DialogButton
              disabled={repairing || finishingFileId !== undefined}
              onClick={repairInstallers}
              style={ACTION_BUTTON}
            >
              {repairing ? "Checking…" : "Repair"}
            </DialogButton>
          )}
          {ownedCount > 0 && !installing ? (
            <DialogButton
              className={WHITE_BUTTON_CLASS}
              disabled={repairing || finishingFileId !== undefined}
              onClick={onUninstallCollection}
              style={ACTION_BUTTON}
            >
              Uninstall ({ownedCount})
            </DialogButton>
          ) : justUninstalled && !installing ? (
            <DialogButton
              onClick={() => {}}
              style={{ ...ACTION_BUTTON, opacity: 0.7 }}
            >
              Uninstalled ✓
            </DialogButton>
          ) : null}
          <DownloadsButton />
          <DialogButton
            style={ACTION_BUTTON}
            onClick={() => {
              popOurPage();
            }}
          >
            Back
          </DialogButton>
          </Focusable>
        </Focusable>

        {/* Partial without a run of ours = the user already owns some of
            these mods (individual installs, another collection) - say so,
            or the shrunken count reads as stale cache. */}
        {detail &&
          !installing &&
          !partialFromRun &&
          remaining.length > 0 &&
          installedRequiredCount > 0 && (
            <div
              style={{
                fontSize: "12.5px",
                opacity: 0.7,
                margin: "-6px 0 12px",
              }}
            >
              {installedRequiredCount} of this collection's mods are already
              installed - only the missing ones will download.
            </div>
          )}
        {directInstalled.length > 0 && !installing && (
          <div
            style={{
              fontSize: "12.5px",
              margin: "-6px 0 12px",
              padding: "8px 10px",
              borderRadius: "4px",
              background: "rgba(143, 212, 143, 0.10)",
              border: "1px solid rgba(143, 212, 143, 0.35)",
              lineHeight: 1.45,
            }}
          >
            {directNote(directInstalled)}
          </div>
        )}
        {unavailable.length > 0 && !installing && (
          <div
            style={{
              fontSize: "12.5px",
              margin: "-6px 0 12px",
              padding: "8px 10px",
              borderRadius: "4px",
              background: "rgba(255, 255, 255, 0.06)",
              border: "1px solid rgba(255, 255, 255, 0.18)",
              lineHeight: 1.45,
            }}
          >
            {unavailableNote(unavailable)}
          </div>
        )}
        {preDisabled.length > 0 && !installing && (
          <div
            style={{
              fontSize: "12.5px",
              margin: "-6px 0 12px",
              padding: "8px 10px",
              borderRadius: "4px",
              background: "rgba(143, 212, 143, 0.10)",
              border: "1px solid rgba(143, 212, 143, 0.35)",
              lineHeight: 1.45,
            }}
          >
            {preDisabledNote(preDisabled)}
          </div>
        )}
        {(manualMods.length > 0 ||
          (detail?.externals.length ?? 0) > 0) &&
          !installing && (
            <div
              style={{
                fontSize: "12.5px",
                margin: "-6px 0 12px",
                padding: "8px 10px",
                borderRadius: "4px",
                lineHeight: 1.45,
                background: "rgba(218, 142, 53, 0.12)",
                border: "1px solid rgba(218, 142, 53, 0.4)",
              }}
            >
              {(() => {
                // One list, deduplicated. A manifest browse-mod and a
                // collection external resource are the same fact to a
                // user - "we cannot download this for you" - and two
                // notes with different counts read as contradicting
                // each other.
                const seen = new Set<string>();
                const items: { label: string; url?: string }[] = [];
                for (const m of manualMods) {
                  const k = m.name.toLowerCase();
                  if (seen.has(k)) continue;
                  seen.add(k);
                  items.push({
                    label: m.name + (m.optional ? " (optional)" : ""),
                    url: m.url,
                  });
                }
                for (const e of detail?.externals ?? []) {
                  const k = e.name.toLowerCase();
                  if (seen.has(k)) continue;
                  seen.add(k);
                  const isFramework =
                    game.framework &&
                    e.name
                      .toLowerCase()
                      .includes(
                        game.framework.name.toLowerCase().slice(0, 4)
                      );
                  items.push({
                    label: isFramework
                      ? `${e.name} — this is ${game.framework!.name}, ` +
                        `Step 1 on the game panel installs it`
                      : e.name + (e.optional ? " (optional)" : ""),
                  });
                }
                const withUrl = items.find((i) => i.url);
                return (
                  <>
                    ⬇ {items.length} thing{items.length === 1 ? "" : "s"} in
                    this collection {items.length === 1 ? "is" : "are"} not
                    hosted on Nexus Mods, so{" "}
                    {items.length === 1 ? "it cannot" : "they cannot"} be
                    downloaded here: {items.map((i) => i.label).join(", ")}.
                    {withUrl ? ` Get it from ${withUrl.url}.` : ""}
                    {parkedForExternal > 0
                      ? ` ${parkedForExternal} mod${
                          parkedForExternal === 1 ? "" : "s"
                        } that need ${
                          items.length === 1 ? "it" : "them"
                        } been switched off so the game still starts — turn ${
                          parkedForExternal === 1 ? "it" : "them"
                        } back on in My Mods once you have added ${
                          items.length === 1 ? "it" : "them"
                        }.`
                      : " Nothing else needed switching off, so the rest of" +
                        " the collection is installed and active."}
                  </>
                );
              })()}
            </div>
          )}
        {brokenSkips.length > 0 && !installing && (
          <div
            style={{
              fontSize: "12.5px",
              margin: "-6px 0 12px",
              padding: "8px 10px",
              borderRadius: "4px",
              lineHeight: 1.45,
              background: "rgba(218, 142, 53, 0.12)",
              border: "1px solid rgba(218, 142, 53, 0.4)",
            }}
          >
            ⏭ {brokenSkips.length} mod
            {brokenSkips.length === 1 ? "" : "s"} left out because the game
            will not start with {brokenSkips.length === 1 ? "it" : "them"}:{" "}
            {brokenSkips.map((b) => b.mod_name).join(", ")}.
            {brokenSkips[0]?.detail ? ` ${brokenSkips[0].detail}` : ""}{" "}
            Everything else in the collection is installed and working.
          </div>
        )}
        {emptySkips.length > 0 && !installing && (
          <div
            style={{
              fontSize: "12.5px",
              opacity: 0.7,
              margin: "-6px 0 12px",
            }}
          >
            ⏭ {emptySkips.length} installer
            {emptySkips.length === 1 ? "" : "s"} had nothing to install (
            {emptySkips.map((t) => t.mod_name).join(", ")}) - the options
            offered are not in the archive, so there is nothing to add and
            nothing to fix.
          </div>
        )}
        {toolSkips.length > 0 && !installing && (
          <div
            style={{
              fontSize: "12.5px",
              opacity: 0.7,
              margin: "-6px 0 12px",
            }}
          >
            ⏭ {toolSkips.length} PC modding tool
            {toolSkips.length === 1 ? "" : "s"} skipped (
            {toolSkips.map((t) => t.mod_name).join(", ")}) - they run on a
            desktop, not in-game, and don't count as missing.
          </div>
        )}
        {layoutSkips.length > 0 && !installing && (
          <div
            style={{
              fontSize: "12.5px",
              opacity: 0.7,
              margin: "-6px 0 12px",
            }}
          >
            ⏭ {layoutSkips.length} archive
            {layoutSkips.length === 1 ? "" : "s"} skipped (
            {layoutSkips.map((t) => t.mod_name).join(", ")}) - no
            installable payload for this device (utilities, updater
            scripts, or layouts we don't support yet).
          </div>
        )}
        {conflictSkips.length > 0 && !installing && (
          <div
            style={{
              fontSize: "12.5px",
              opacity: 0.75,
              color: "#ffc83c",
              margin: "-6px 0 12px",
            }}
          >
            🔒 {conflictSkips.length} mod
            {conflictSkips.length === 1 ? "" : "s"} skipped for script
            conflicts ({conflictSkips.map((c) => c.mod_name).join(", ")}).
            Each edits a game script another installed mod already changed;
            the installed one was kept so the game still boots. Resolving
            these needs Script Merger on PC.
          </div>
        )}

        {error && (
          <div style={{ color: "#ff8a8a", padding: "8px 0" }}>{error}</div>
        )}

        {detail && (
          <SectionHeading
            title={`Mods (${required.length}${
              optional.length > 0 ? ` + ${optional.length} optional` : ""
            })`}
          />
        )}
        {detail && (
          <Focusable
            style={{ display: "flex", flexDirection: "column", gap: "4px" }}
          >
            {required.map((f) => {
              const open = expanded.has(f.fileId);
              const info = modInfo[f.modId];
              // Fill from EITHER the install turn or a live prefetch -
              // the pipeline downloads rows long before they install,
              // and those rows went dark without this (v0.40 regression).
              const liveDl = getDownloads().find(
                (d) => d.modId === f.modId && d.phase === "downloading"
              );
              const pct =
                rowState[f.fileId] === "installing" ||
                finishingFileId === f.fileId
                  ? getDownloadPercent(f.modId) ?? 0
                  : liveDl && installing
                  ? liveDl.percent
                  : undefined;
              const needsChoices =
                actionableIds.has(f.fileId) && !installedIds.has(f.modId);
              const parkedReason = attentionIds.has(f.fileId)
                ? attention.find((a) => a.file_id === f.fileId)?.reason
                : undefined;
              const isToolSkip =
                parkedReason === "tool" && !installedIds.has(f.modId);
              const isConflict =
                parkedReason === "conflict" && !installedIds.has(f.modId);
              const attentionItem = needsChoices
                ? attention.find((a) => a.file_id === f.fileId)
                : undefined;
              return (
                <Focusable
                  key={f.fileId}
                  // A Focusable WITH onActivate is a leaf: the controller can
                  // never reach anything inside it, so the eye button in an
                  // expanded row existed but was untappable. Michael: "the
                  // eye icon when you expand the mod is not focusable with a
                  // controller". Closed rows activate to expand; open rows
                  // hand focus to their children (eye, Finish setup) and the
                  // title collapses them.
                  onActivate={open ? undefined : () => toggleExpand(f)}
                  style={{
                    padding: "6px 10px",
                    // Downloading rows fill orange left-to-right with the
                    // live percent - the row IS the progress bar.
                    background:
                      pct !== undefined
                        ? `linear-gradient(90deg, rgba(218,142,53,0.45) ${pct}%, rgba(255,255,255,0.05) ${pct}%)`
                        : needsChoices
                        ? "rgba(74,169,255,0.10)"
                        : "rgba(255,255,255,0.05)",
                    color: pct !== undefined ? "#fff" : undefined,
                    borderLeft: needsChoices
                      ? "3px solid #4aa9ff"
                      : "3px solid transparent",
                    transition: "background 0.3s linear",
                    borderRadius: "4px",
                    fontSize: "13px",
                  }}
                >
                  <Focusable
                    // When the row is open, the row itself no longer
                    // activates (so its buttons become reachable) and the
                    // title line is the collapse control instead.
                    onActivate={open ? () => toggleExpand(f) : undefined}
                    style={{ display: "flex", justifyContent: "space-between" }}
                  >
                    <span
                      style={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {open ? "▾ " : "▸ "}
                      {stateBadge(f)}
                      {f.modName}
                      {f.version ? ` · v${f.version}` : ""}
                      {needsChoices && (
                        <span style={{ color: "#4aa9ff" }}>
                          {" "}
                          · needs choices
                        </span>
                      )}
                      {isToolSkip && (
                        <span style={{ opacity: 0.55 }}> · PC tool</span>
                      )}
                      {parkedReason === "layout" &&
                        !installedIds.has(f.modId) && (
                          <span style={{ opacity: 0.55 }}>
                            {" "}
                            · not installable
                          </span>
                        )}
                      {isConflict && (
                        <span style={{ color: "#ffc83c" }}>
                          {" "}
                          · script conflict
                        </span>
                      )}
                      {/* Amber and worded, because a toast has gone by the
                          time the user wonders why this one has no tick. */}
                      {parkedReason === "older-game" &&
                        !installedIds.has(f.modId) && (
                          <span style={{ color: "#ffc83c" }}>
                            {" "}
                            · skipped · built for an older patch
                          </span>
                        )}
                    </span>
                    <span
                      style={{ opacity: 0.6, flexShrink: 0, marginLeft: "10px" }}
                    >
                      {/* sizeInBytes comes back NULL for some files
                          (small and huge alike) - unknown, not big */}
                      {f.sizeKb > 0 ? fmtBytes(f.sizeKb * 1024) : "—"}
                    </span>
                  </Focusable>
                  {open && (
                    <div
                      style={{
                        display: "flex",
                        gap: "10px",
                        marginTop: "6px",
                        paddingTop: "6px",
                        borderTop: "1px solid rgba(255,255,255,0.08)",
                      }}
                    >
                      {info === undefined && (
                        <span style={{ opacity: 0.6, fontSize: "12px" }}>
                          Loading…
                        </span>
                      )}
                      {info === null && (
                        <span style={{ opacity: 0.6, fontSize: "12px" }}>
                          Details unavailable.
                        </span>
                      )}
                      {info && (
                        <>
                          {info.thumbnailUrl && (
                            <img
                              src={info.thumbnailUrl}
                              alt=""
                              loading="lazy"
                              decoding="async"
                              style={{
                                width: "96px",
                                height: "54px",
                                objectFit: "cover",
                                borderRadius: "4px",
                                flexShrink: 0,
                              }}
                            />
                          )}
                          <div
                            style={{
                              fontSize: "12px",
                              opacity: 0.85,
                              flexGrow: 1,
                              minWidth: 0,
                            }}
                          >
                            <div style={{ opacity: 0.7 }}>
                              by {info.author} · {f.fileName}
                            </div>
                            {info.summary}
                          </div>
                          <Focusable
                            style={{
                              display: "flex",
                              flexDirection: "column",
                              gap: "6px",
                              flexShrink: 0,
                              alignSelf: "center",
                            }}
                          >
                            <DialogButton
                              onClick={() => openModPage(f)}
                              style={{
                                minWidth: "0",
                                width: "44px",
                                padding: "8px 0",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                              }}
                            >
                              <FaEye />
                            </DialogButton>
                            {attentionItem && (
                              <DialogButton
                                className={BLUE_BUTTON_CLASS}
                                disabled={finishingFileId !== undefined}
                                onClick={() => resolveSingle(attentionItem)}
                                style={{
                                  minWidth: "0",
                                  width: "auto",
                                  padding: "8px 12px",
                                  fontSize: "12px",
                                }}
                              >
                                Make choices
                              </DialogButton>
                            )}
                          </Focusable>
                        </>
                      )}
                    </div>
                  )}
                </Focusable>
              );
            })}
            {optional.length > 0 && (
              <div
                style={{ fontSize: "12px", opacity: 0.65, margin: "8px 0 2px" }}
              >
                Optional ({optional.length}) — not installed automatically:
              </div>
            )}
            {optional.map((f) => {
              const open = expanded.has(f.fileId);
              const info = modInfo[f.modId];
              // Same fill treatment as required rows - optionals download
              // through the identical pipeline.
              const liveDl = getDownloads().find(
                (d) => d.modId === f.modId && d.phase === "downloading"
              );
              const pct =
                rowState[f.fileId] === "installing" ||
                finishingFileId === f.fileId
                  ? getDownloadPercent(f.modId) ?? 0
                  : liveDl && installing
                  ? liveDl.percent
                  : undefined;
              return (
                <Focusable
                  key={f.fileId}
                  // Same leaf problem as the row above: see that comment.
                  onActivate={open ? undefined : () => toggleExpand(f)}
                  style={{
                    padding: "5px 10px",
                    background:
                      pct !== undefined
                        ? `linear-gradient(90deg, rgba(218,142,53,0.45) ${pct}%, rgba(255,255,255,0.03) ${pct}%)`
                        : "rgba(255,255,255,0.03)",
                    color: pct !== undefined ? "#fff" : undefined,
                    transition: "background 0.3s linear",
                    borderRadius: "4px",
                    fontSize: "12.5px",
                    opacity: pct !== undefined ? 1 : 0.8,
                  }}
                >
                  <Focusable
                    // When the row is open, the row itself no longer
                    // activates (so its buttons become reachable) and the
                    // title line is the collapse control instead.
                    onActivate={open ? () => toggleExpand(f) : undefined}
                    style={{ display: "flex", justifyContent: "space-between" }}
                  >
                    <span
                      style={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {open ? "▾ " : "▸ "}
                      {stateBadge(f)}
                      {f.modName}
                    </span>
                    <span style={{ flexShrink: 0, marginLeft: "10px" }}>
                      {f.sizeKb > 0 ? fmtBytes(f.sizeKb * 1024) : "—"}
                    </span>
                  </Focusable>
                  {open && (
                    <div
                      style={{
                        display: "flex",
                        gap: "10px",
                        marginTop: "6px",
                        paddingTop: "6px",
                        borderTop: "1px solid rgba(255,255,255,0.08)",
                      }}
                    >
                      {info === undefined && (
                        <span style={{ opacity: 0.6, fontSize: "12px" }}>
                          Loading…
                        </span>
                      )}
                      {info === null && (
                        <span style={{ opacity: 0.6, fontSize: "12px" }}>
                          Details unavailable.
                        </span>
                      )}
                      {info && (
                        <>
                          {info.thumbnailUrl && (
                            <img
                              src={info.thumbnailUrl}
                              alt=""
                              loading="lazy"
                              decoding="async"
                              style={{
                                width: "96px",
                                height: "54px",
                                objectFit: "cover",
                                borderRadius: "4px",
                                flexShrink: 0,
                              }}
                            />
                          )}
                          <div
                            style={{
                              fontSize: "12px",
                              opacity: 0.85,
                              flexGrow: 1,
                              minWidth: 0,
                            }}
                          >
                            <div style={{ opacity: 0.7 }}>
                              by {info.author} · {f.fileName}
                            </div>
                            {info.summary}
                          </div>
                          <Focusable
                            style={{ flexShrink: 0, alignSelf: "center" }}
                          >
                            <DialogButton
                              onClick={() => openModPage(f)}
                              style={{
                                minWidth: "0",
                                width: "44px",
                                padding: "8px 0",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                              }}
                            >
                              <FaEye />
                            </DialogButton>
                          </Focusable>
                        </>
                      )}
                    </div>
                  )}
                </Focusable>
              );
            })}
          </Focusable>
        )}
        {!detail && !error && (
          <div style={{ opacity: 0.8, padding: "12px 0" }}>
            Loading collection…
          </div>
        )}
        </div>
      </Scroller>
    </Focusable>
  );
}
