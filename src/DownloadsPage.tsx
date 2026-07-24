// Full-screen Downloads: active transfers (mods and collection batches)
// plus a completed section - the QAM only carries a shortcut here.
import {
  DialogButton,
  Focusable,
  Navigation,
  QuickAccessTab,
  ScrollPanelGroup,
} from "@decky/ui";
import { useEffect, useState } from "react";

import {
  clearCompletedDownloads,
  getCollectionRun,
  getCompletedDownloads,
  getDownloads,
  getRunSkippedCount,
  setDetailOrigin,
  setSelectedCollection,
  setSelectedMod,
  subscribeCollectionRun,
  subscribeDownloads,
} from "./state";
import { getModDetails } from "./api";
import { getSupportedGame } from "./games";
import { TabBar, handleTabButtons } from "./Tabs";

const Scroller: any = ScrollPanelGroup;

function Row({
  name,
  status,
  dim,
  pct,
  onActivate,
}: {
  name: string;
  status: string;
  dim?: boolean;
  /** In-flight rows fill orange left-to-right - the row IS the bar. */
  pct?: number;
  onActivate?: () => void;
}) {
  const Tag: any = onActivate ? Focusable : "div";
  return (
    <Tag
      onActivate={onActivate}
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "8px 12px",
        background:
          pct !== undefined
            ? `linear-gradient(90deg, rgba(218,142,53,0.45) ${pct}%, rgba(255,255,255,0.05) ${pct}%)`
            : "rgba(255,255,255,0.05)",
        color: pct !== undefined ? "#fff" : undefined,
        transition: "background 0.3s linear",
        borderRadius: "4px",
        fontSize: "13.5px",
        opacity: dim ? 0.65 : 1,
      }}
    >
      <span
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {name}
      </span>
      <span style={{ flexShrink: 0, marginLeft: "12px" }}>{status}</span>
    </Tag>
  );
}

/** Row click-through: open the mod's detail page in its game context.
 * Collection summary entries open the collection page instead. */
async function openDownloadTarget(
  modId: number,
  gameAppId?: number,
  collectionSlug?: string,
  name?: string
) {
  const game = getSupportedGame(gameAppId);
  if (!game) return;
  if (collectionSlug) {
    // Synthesized summary is enough - the page fetches the detail.
    setSelectedCollection({
      game,
      collection: {
        name: (name ?? collectionSlug).split(" · ")[0],
        slug: collectionSlug,
        summary: "",
        endorsements: 0,
        author: "",
        modCount: 0,
        totalSize: 0,
      },
    });
    Navigation.Navigate("/nexus-mods/collection");
    return;
  }
  if (modId <= 0) return;
  const result = await getModDetails(game.nexusDomain, modId);
  if (result.ok && result.mod) {
    setSelectedMod({ game, mod: result.mod });
    setDetailOrigin("browse"); // B returns here, not to the QAM
    Navigation.Navigate("/nexus-mods/mod");
  }
}

export function DownloadsPage() {
  const [, force] = useState(0);
  useEffect(() => {
    const un1 = subscribeDownloads(() => force((n) => n + 1));
    const un2 = subscribeCollectionRun(() => force((n) => n + 1));
    return () => {
      un1();
      un2();
    };
  }, []);

  const active = getDownloads();
  const completed = getCompletedDownloads();
  const run = getCollectionRun();

  return (
    <Focusable
      // The TabBar always provides focusable children, so B (onCancel)
      // is always catchable. The old autoFocus + onActivate guard made
      // the ROOT itself the focus leaf - the stick couldn't move down
      // into the rows at all.
      onButtonDown={handleTabButtons("downloads")}
      onCancel={() => {
        // QAM first so gamepad focus lands INSIDE it - then pop the page.
        // The old order left focus on the page behind the menu, so B in
        // the QAM re-triggered page handlers instead of closing it.
        Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);
        setTimeout(() => Navigation.NavigateBack(), 50);
      }}
      style={{ marginTop: "40px", height: "calc(100% - 40px)" }}
    >
      <Scroller
        focusable={false}
        style={{ height: "100%", overflowY: "auto", padding: "0 24px 110px", scrollPaddingBottom: "110px" }}
      >
        <TabBar currentId="downloads" />
        <h2 style={{ margin: "12px 0 10px" }}>Downloads</h2>

        {run && (
          <Focusable
            onActivate={() => Navigation.Navigate("/nexus-mods/collection")}
            style={{ marginBottom: "14px" }}
          >
            <div style={{ fontSize: "14px", fontWeight: 600, marginBottom: "4px" }}>
              {run.name ?? "Collection"}: {run.finished}/{run.total}{" "}
              {run.running ? "installing…" : "finished"}
              {getRunSkippedCount(run) > 0 && (
                <span style={{ color: "#4aa9ff" }}>
                  {" "}
                  · {getRunSkippedCount(run)} need choices → open to finish
                </span>
              )}
            </div>
            <div
              style={{
                height: "6px",
                background: "rgba(255,255,255,0.1)",
                borderRadius: "3px",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${
                    run.total ? Math.round((run.finished / run.total) * 100) : 0
                  }%`,
                  height: "100%",
                  background: "#da8e35",
                }}
              />
            </div>
          </Focusable>
        )}

        <div style={{ fontSize: "13px", fontWeight: 600, margin: "8px 0 6px" }}>
          Active ({active.length})
        </div>
        <Focusable style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          {active.length === 0 && (
            <div style={{ fontSize: "13px", opacity: 0.6 }}>
              Nothing downloading right now.
            </div>
          )}
          {active.map((d) => (
            <Row
              key={d.modId}
              onActivate={() =>
                openDownloadTarget(d.modId, d.gameAppId, d.collectionSlug, d.name)
              }
              name={d.name}
              pct={d.phase === "extracting" ? 100 : d.percent}
              status={
                d.phase === "downloading"
                  ? `${d.percent}%`
                  : d.phase === "extracting"
                  ? "Installing…"
                  : "Starting…"
              }
            />
          ))}
        </Focusable>

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            margin: "16px 0 6px",
          }}
        >
          <span style={{ fontSize: "13px", fontWeight: 600 }}>
            Completed ({completed.length})
          </span>
          {completed.length > 0 && (
            <DialogButton
              onClick={clearCompletedDownloads}
              style={{
                minWidth: "0",
                width: "auto",
                padding: "4px 12px",
                fontSize: "12px",
              }}
            >
              Clear
            </DialogButton>
          )}
        </div>
        <Focusable style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          {completed.map((d, i) => (
            <Row
              key={`${d.modId}-${i}`}
              onActivate={() =>
                openDownloadTarget(d.modId, d.gameAppId, d.collectionSlug, d.name)
              }
              name={d.name}
              status={d.phase === "done" ? "Done ✓" : "Failed ⚠"}
              dim
            />
          ))}
        </Focusable>
      </Scroller>
    </Focusable>
  );
}
