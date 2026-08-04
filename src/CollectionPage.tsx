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
import { FaEye } from "react-icons/fa";

import {
  AttentionItem,
  CollectionDetail,
  CollectionFile,
  NexusMod,
  getCollection,
  getCollectionAttention,
  getCollectionManifest,
  getInstalledMods,
  getModDetails,
  installFomodAuto,
  registerCollection,
  setCollectionAttention,
  uninstallCollection,
} from "./api";
import { PayloadChoiceModal } from "./ChoiceModal";
import { FomodWizardData, FomodWizardModal } from "./FomodWizard";
import { modeParams } from "./games";
import { finishFomod, installPinned } from "./install";
import { backAction } from "./navRules";
import {
  CollectionRowState,
  beginCollectionRun,
  dropDownload,
  endCollectionRun,
  getCollectionRun,
  getDownloadPercent,
  getSelectedCollection,
  setCollectionRow,
  setDetailOrigin,
  setSelectedMod,
  subscribeCollectionRun,
  subscribeDownloads,
  updateDownload,
} from "./state";
import {
  BLUE_BUTTON_CLASS,
  PRIMARY_BUTTON_CLASS,
  PRIMARY_BUTTON_CSS,
  WHITE_BUTTON_CLASS,
} from "./theme";

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
  // Records installed BY this collection (its slug) - drives Uninstall.
  const [ownedCount, setOwnedCount] = useState(0);
  // Uninstalling unmounts the focused button; without a stand-in the
  // gamepad focus dies and the next press falls through to Steam's
  // back-chain (reported as "closed the page and went back to the game").
  const [justUninstalled, setJustUninstalled] = useState(false);
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

  const refreshInstalled = () => {
    if (!sel) return;
    getInstalledMods(
      sel.game.nexusDomain,
      sel.game.installDirName,
      sel.game.modsSubdir,
      ...modeParams(sel.game),
      sel.game.protectedModFolders ?? []
    ).then((r) => {
      setInstalledIds(
        new Set(
          (r.mods ?? [])
            .map((m) => m.mod_id)
            .filter((id): id is number => id !== undefined)
        )
      );
      // Only records CARRYING this slug can be uninstalled by this
      // collection - shared/individual installs stay, so the button
      // must hide when none are left (it looked broken otherwise).
      setOwnedCount(
        (r.mods ?? []).filter(
          (m) => m.collection_slug === sel.collection.slug
        ).length
      );
    });
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
  const actionable = attention.filter(
    (a) => a.reason === "choices" || a.reason === "fomod"
  );
  const actionableIds = new Set(actionable.map((a) => a.file_id));
  const toolSkips = attention.filter((a) => a.reason === "tool");
  const conflictSkips = attention.filter((a) => a.reason === "conflict");
  const layoutSkips = attention.filter((a) => a.reason === "layout");

  const required = detail?.files.filter((f) => !f.optional) ?? [];
  const optional = detail?.files.filter((f) => f.optional) ?? [];
  // Pending-attention mods are NOT "remaining": re-queueing them just
  // re-parks (or re-skips) them - they resolve via Finish setup instead.
  const remaining = required.filter(
    (f) =>
      !installedIds.has(f.modId) &&
      rowState[f.fileId] !== "done" &&
      !attentionIds.has(f.fileId)
  );
  const optionalRemaining = optional.filter(
    (f) =>
      !installedIds.has(f.modId) &&
      rowState[f.fileId] !== "done" &&
      !attentionIds.has(f.fileId)
  );
  // "Resume" only makes sense for a run THIS page started - already
  // owning some of a collection's mods individually is not a resume.
  const partialFromRun = runIsOurs && !run!.running && run!.finished > 0;
  // Actually-installed count (skipped tools are NOT installed - the old
  // required-minus-remaining math counted them and overstated).
  const installedRequiredCount = required.filter(
    (f) => installedIds.has(f.modId) || rowState[f.fileId] === "done"
  ).length;

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
      for (const f of queue) {
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
            toaster.toast({
              title: `${f.modName}: PC tool - skipped`,
              body: "Utilities like this run on a desktop, not in-game",
            });
          } else if (result.script_conflict) {
            // Unmergeable script conflict: parking it keeps the button
            // honest ("everything installed" when only these remain)
            // instead of offering an install that can only re-refuse.
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
              title: `${f.modName}: script conflict - skipped`,
              body: result.error ?? "",
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
      refreshInstalled();
      // Only actionable items belong in "waiting on your choices" -
      // tools/conflicts/unrecognized archives are permanent skips and
      // used to make this toast promise a Finish setup that never came.
      const needsChoices = freshAttention.filter(
        (a) => a.reason === "choices" || a.reason === "fomod"
      ).length;
      const skipped = freshAttention.length - needsChoices;
      const bits = [];
      if (failures > 0) bits.push(`${failures} failure(s)`);
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
      endCollectionRun();
    }
  };

  /** Modal helpers that resolve as promises so Finish setup can walk
   * every pending mod sequentially. closeModal resolves undefined a tick
   * later than onPick - the pick wins when both fire. */
  const pickChoice = (name: string, options: string[]) =>
    new Promise<string | undefined>((resolve) => {
      const modal = showModal(
        <PayloadChoiceModal
          modName={name}
          options={options}
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
  ): Promise<"installed" | "backout" | "failed"> => {
    setFinishingFileId(item.file_id);
    try {
      let choice = "";
      if (item.reason === "choices" && item.options.length > 0) {
        const picked = await pickChoice(item.mod_name, item.options);
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
        const picked = await pickChoice(item.mod_name, result.options);
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
    for (const item of [...actionable]) {
      const outcome = await resolveAttentionItem(item);
      if (outcome === "backout") break;
      if (outcome === "installed") {
        persistAttention(
          attentionRef.current.filter((a) => a.file_id !== item.file_id)
        );
      }
    }
    refreshInstalled();
  };

  /** One row's "Make choices & install" - usable even while the batch
   * is still working through other mods. */
  const resolveSingle = async (item: AttentionItem) => {
    if (finishingFileId !== undefined) return;
    const outcome = await resolveAttentionItem(item);
    if (outcome === "installed") {
      persistAttention(
        attentionRef.current.filter((a) => a.file_id !== item.file_id)
      );
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
    Navigation.Navigate("/nexus-mods/mod");
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
    if (installedIds.has(f.modId) || rowState[f.fileId] === "done")
      return "✓ ";
    if (actionableIds.has(f.fileId)) return "⚙ ";
    if (attentionIds.has(f.fileId)) {
      const reason = attention.find((a) => a.file_id === f.fileId)?.reason;
      return reason === "conflict" ? "🔒 " : "⏭ ";
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
          Navigation.NavigateBack();
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
        <style>{PRIMARY_BUTTON_CSS}</style>
        <Focusable style={{ display: "flex", gap: "18px", padding: "12px 0" }}>
          {collection.thumbnailUrl && (
            <img
              src={collection.thumbnailUrl}
              alt=""
              loading="lazy"
              style={{
                width: "180px",
                borderRadius: "8px",
                objectFit: "contain",
                background: "#0b0e13",
                alignSelf: "flex-start",
              }}
            />
          )}
          <div style={{ minWidth: 0 }}>
            <h2 style={{ margin: "0 0 2px 0" }}>{collection.name}</h2>
            <div style={{ opacity: 0.75, fontSize: "14px" }}>
              a collection by {detail?.author ?? collection.author} ·{" "}
              {game.displayName}
            </div>
            <div style={{ opacity: 0.75, fontSize: "13px", marginTop: "4px" }}>
              {detail
                ? `${detail.files.length} mods · ${fmtBytes(detail.totalSize)}`
                : `${collection.modCount} mods · ${fmtBytes(
                    collection.totalSize
                  )}`}
            </div>
            {(detail?.summary ?? collection.summary) && (
              <div style={{ fontSize: "13px", opacity: 0.9, marginTop: "8px" }}>
                {detail?.summary ?? collection.summary}
              </div>
            )}
          </div>
        </Focusable>

        <Focusable
          autoFocus={true}
          style={{ display: "flex", gap: "10px", margin: "6px 0 14px" }}
        >
          <DialogButton
            className={PRIMARY_BUTTON_CLASS}
            disabled={!detail || installing || remaining.length === 0}
            onClick={() => installAll(false)}
            style={{ flexGrow: 2, minWidth: "260px" }}
          >
            {installing
              ? `Installing… ${runIsOurs ? run!.finished : 0}/${
                  runIsOurs ? run!.total : remaining.length
                }`
              : remaining.length === 0 && detail
              ? "Everything installed ✓"
              : partialFromRun
              ? `⬇ Resume collection (${remaining.length} left)`
              : detail && remaining.length < required.length
              ? `⬇ Install remaining (${remaining.length} of ${required.length})`
              : `⬇ Install required (${remaining.length})`}
          </DialogButton>
          {actionable.length > 0 && (
            <DialogButton
              className={BLUE_BUTTON_CLASS}
              disabled={finishingFileId !== undefined}
              onClick={finishSetup}
              style={{ flexGrow: 1, minWidth: "200px" }}
            >
              {finishingFileId !== undefined
                ? "Finishing…"
                : `⚙ Finish setup (${actionable.length})`}
            </DialogButton>
          )}
          {optionalRemaining.length > 0 && (
            <DialogButton
              className={WHITE_BUTTON_CLASS}
              disabled={!detail || installing}
              onClick={() => installAll(true)}
              style={{ flexGrow: 1, minWidth: "190px" }}
            >
              {remaining.length === 0
                ? `Install optional (${optionalRemaining.length})`
                : `Install All (inc ${optionalRemaining.length} optional)`}
            </DialogButton>
          )}
          <DialogButton
            style={{ flexGrow: 1, minWidth: "120px" }}
            onClick={() => {
              Navigation.NavigateBack();
            }}
          >
            Back
          </DialogButton>
          {ownedCount > 0 && !installing ? (
            <DialogButton
              className={WHITE_BUTTON_CLASS}
              disabled={finishingFileId !== undefined}
              onClick={onUninstallCollection}
              style={{ flexGrow: 1, minWidth: "150px" }}
            >
              Uninstall ({ownedCount})
            </DialogButton>
          ) : justUninstalled && !installing ? (
            <DialogButton
              onClick={() => {}}
              style={{ flexGrow: 1, minWidth: "150px", opacity: 0.7 }}
            >
              Uninstalled ✓
            </DialogButton>
          ) : null}
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

        {detail && detail.externals.length > 0 && (
          <div
            style={{
              margin: "0 0 12px",
              padding: "8px 12px",
              background: "rgba(255, 200, 60, 0.12)",
              borderLeft: "3px solid #ffc83c",
              borderRadius: "4px",
              fontSize: "13px",
            }}
          >
            This collection references {detail.externals.length} external
            file(s) we can't fetch automatically:{" "}
            {detail.externals
              .map((e) =>
                game.framework &&
                e.name
                  .toLowerCase()
                  .includes(game.framework.name.toLowerCase().slice(0, 4))
                  ? `${e.name} (this is ${game.framework.name} - Step 1 on the game's panel installs it)`
                  : e.name
              )
              .join(", ")}
          </div>
        )}

        {detail && (
          <Focusable
            style={{ display: "flex", flexDirection: "column", gap: "4px" }}
          >
            {required.map((f) => {
              const open = expanded.has(f.fileId);
              const info = modInfo[f.modId];
              const pct =
                rowState[f.fileId] === "installing" ||
                finishingFileId === f.fileId
                  ? getDownloadPercent(f.modId) ?? 0
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
                  onActivate={() => toggleExpand(f)}
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
                  <div
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
                    </span>
                    <span
                      style={{ opacity: 0.6, flexShrink: 0, marginLeft: "10px" }}
                    >
                      {/* sizeInBytes comes back NULL for some files
                          (small and huge alike) - unknown, not big */}
                      {f.sizeKb > 0 ? fmtBytes(f.sizeKb * 1024) : "—"}
                    </span>
                  </div>
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
              const pct =
                rowState[f.fileId] === "installing" ||
                finishingFileId === f.fileId
                  ? getDownloadPercent(f.modId) ?? 0
                  : undefined;
              return (
                <Focusable
                  key={f.fileId}
                  onActivate={() => toggleExpand(f)}
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
                  <div
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
                  </div>
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
      </Scroller>
    </Focusable>
  );
}
