// Full-screen collection view: the curated mod list with one-button
// sequential install through the per-game pipeline (order preserved -
// collections are ordered, and so is our plugin activation).
import {
  DialogButton,
  Focusable,
  Navigation,
  QuickAccessTab,
  ScrollPanelGroup,
} from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useState } from "react";

import {
  CollectionDetail,
  CollectionFile,
  NexusMod,
  getCollection,
  getInstalledMods,
  getModDetails,
} from "./api";
import { modeParams } from "./games";
import { installPinned } from "./install";
import {
  CollectionRowState,
  beginCollectionRun,
  endCollectionRun,
  getCollectionRun,
  getSelectedCollection,
  setCollectionRow,
  subscribeCollectionRun,
} from "./state";
import { PRIMARY_BUTTON_CLASS, PRIMARY_BUTTON_CSS } from "./theme";

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
  // Batch state lives in a module store so navigating away and back
  // shows live progress instead of a stale page.
  const [, force] = useState(0);
  useEffect(() => subscribeCollectionRun(() => force((n) => n + 1)), []);
  const run = getCollectionRun();
  const runIsOurs = run?.slug === sel?.collection.slug;
  const rowState: Record<number, CollectionRowState> = runIsOurs
    ? run!.rows
    : {};
  const installing = Boolean(runIsOurs && run!.running);

  useEffect(() => {
    if (!sel) return;
    getCollection(sel.collection.slug, sel.game.nexusDomain).then((r) => {
      if (r.ok && r.collection) setDetail(r.collection);
      else setError(r.error ?? "Could not load collection");
    });
    getInstalledMods(
      sel.game.nexusDomain,
      sel.game.installDirName,
      sel.game.modsSubdir,
      ...modeParams(sel.game),
      sel.game.protectedModFolders ?? []
    ).then((r) =>
      setInstalledIds(
        new Set(
          (r.mods ?? [])
            .map((m) => m.mod_id)
            .filter((id): id is number => id !== undefined)
        )
      )
    );
  }, []);

  if (!sel) {
    return (
      <div style={{ marginTop: "40px", padding: "24px" }}>
        No collection selected.
      </div>
    );
  }
  const { game, collection } = sel;

  const required = detail?.files.filter((f) => !f.optional) ?? [];
  const optional = detail?.files.filter((f) => f.optional) ?? [];
  const remaining = required.filter(
    (f) => !installedIds.has(f.modId) && rowState[f.fileId] !== "done"
  );

  const installAll = async () => {
    if (!detail || installing) return;
    beginCollectionRun(collection.slug, remaining.length);
    try {
      let failures = 0;
      for (const f of remaining) {
        setCollectionRow(f.fileId, "installing");
        const result = await installPinned(
          game,
          f.modId,
          f.fileId,
          f.fileName,
          f.modName,
          f.version
        );
        if (result.ok) {
          setCollectionRow(f.fileId, "done");
        } else if (result.needs_choice || result.needs_fomod) {
          setCollectionRow(f.fileId, "skipped");
          toaster.toast({
            title: `${f.modName}: needs manual choices`,
            body: "Open its mod page to pick options",
          });
        } else {
          failures += 1;
          setCollectionRow(f.fileId, "failed");
          toaster.toast({
            title: `${f.modName} failed`,
            body: result.error ?? "",
          });
        }
      }
      toaster.toast({
        title: `${collection.name}`,
        body:
          failures === 0
            ? "Collection installed - restart the game to load it"
            : `Finished with ${failures} failure(s) - see the list`,
      });
    } finally {
      endCollectionRun();
    }
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
    const st = rowState[f.fileId];
    if (st === "installing") return "⏳ ";
    if (st === "failed") return "⚠ ";
    if (st === "skipped") return "⏭ ";
    return "";
  };

  return (
    <Focusable
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
            onClick={installAll}
            style={{ flexGrow: 2, minWidth: "260px" }}
          >
            {installing
              ? `Installing… ${runIsOurs ? run!.finished : 0}/${
                  runIsOurs ? run!.total : remaining.length
                }`
              : remaining.length === 0 && detail
              ? "Everything installed ✓"
              : detail && remaining.length < required.length
              ? `⬇ Resume collection (${remaining.length} left)`
              : `⬇ Install collection (${remaining.length} mods)`}
          </DialogButton>
          <DialogButton
            style={{ flexGrow: 1, minWidth: "140px" }}
            onClick={() => {
              Navigation.NavigateBack();
            }}
          >
            Back
          </DialogButton>
        </Focusable>

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
            {detail.externals.map((e) => e.name).join(", ")}
          </div>
        )}

        {detail && (
          <Focusable
            style={{ display: "flex", flexDirection: "column", gap: "4px" }}
          >
            {required.map((f) => {
              const open = expanded.has(f.fileId);
              const info = modInfo[f.modId];
              return (
                <Focusable
                  key={f.fileId}
                  onActivate={() => toggleExpand(f)}
                  style={{
                    padding: "6px 10px",
                    background: "rgba(255,255,255,0.05)",
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
                    </span>
                    <span
                      style={{ opacity: 0.6, flexShrink: 0, marginLeft: "10px" }}
                    >
                      {fmtBytes(f.sizeKb * 1024)}
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
                          <div style={{ fontSize: "12px", opacity: 0.85 }}>
                            <div style={{ opacity: 0.7 }}>
                              by {info.author} · {f.fileName}
                            </div>
                            {info.summary}
                          </div>
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
            {optional.map((f) => (
              <div
                key={f.fileId}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  padding: "5px 10px",
                  background: "rgba(255,255,255,0.03)",
                  borderRadius: "4px",
                  fontSize: "12.5px",
                  opacity: 0.75,
                }}
              >
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {stateBadge(f)}
                  {f.modName}
                </span>
                <span style={{ flexShrink: 0, marginLeft: "10px" }}>
                  {fmtBytes(f.sizeKb * 1024)}
                </span>
              </div>
            ))}
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
