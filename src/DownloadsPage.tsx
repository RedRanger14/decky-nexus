// Full-screen Downloads: active transfers (mods and collection batches)
// plus a completed section - the QAM only carries a shortcut here.
import {
  DialogButton,
  Focusable,
  Navigation,
  ScrollPanelGroup,
} from "@decky/ui";
import { useEffect, useState } from "react";

import {
  clearCompletedDownloads,
  getAggregateBps,
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
import { TabBar, exitTabsToQam, handleTabButtons } from "./Tabs";

const Scroller: any = ScrollPanelGroup;

export function formatBytes(n: number): string {
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(1)} GB`;
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(n >= 100 << 20 ? 0 : 1)} MB`;
  if (n >= 1 << 10) return `${Math.round(n / (1 << 10))} KB`;
  return `${n} B`;
}

function formatSpeed(bps: number): string {
  return `${formatBytes(bps)}/s`;
}

/** Live download-speed sparkline: one sample per second, ~90s of history,
 * scaled to the window's peak. Sits top-right of the page header. */
function SpeedGraph({ samples }: { samples: number[] }) {
  const W = 180;
  const H = 44;
  const peak = Math.max(...samples, 1);
  const current = samples[samples.length - 1] ?? 0;
  const points = samples
    .map((v, i) => {
      const x = (i / Math.max(samples.length - 1, 1)) * W;
      const y = H - (v / peak) * (H - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-end",
        gap: "2px",
        flexShrink: 0,
      }}
    >
      <svg
        width={W}
        height={H}
        style={{
          background: "rgba(255,255,255,0.05)",
          borderRadius: "4px",
        }}
      >
        {samples.length > 1 && (
          <>
            <polyline
              points={`0,${H} ${points} ${W},${H}`}
              fill="rgba(218,142,53,0.25)"
              stroke="none"
            />
            <polyline
              points={points}
              fill="none"
              stroke="#da8e35"
              strokeWidth="1.5"
            />
          </>
        )}
      </svg>
      <span style={{ fontSize: "12px", opacity: 0.85 }}>
        {current > 0 ? formatSpeed(current) : "idle"}
      </span>
    </div>
  );
}

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
  // Speed history: sampled here (not in the global store) so the graph
  // costs nothing while the page is closed. Zeroes record idle gaps.
  const [speedSamples, setSpeedSamples] = useState<number[]>([]);
  useEffect(() => {
    const un1 = subscribeDownloads(() => force((n) => n + 1));
    const un2 = subscribeCollectionRun(() => force((n) => n + 1));
    const timer = setInterval(() => {
      setSpeedSamples((prev) => [...prev, getAggregateBps()].slice(-90));
    }, 1000);
    return () => {
      un1();
      un2();
      clearInterval(timer);
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
      onCancel={exitTabsToQam}
      style={{ marginTop: "40px", height: "calc(100% - 40px)" }}
    >
      <Scroller
        focusable={false}
        // The scroll panel sits between the rows and the page root and
        // consumes bumper presses (section-jump) - handle tabs here too.
        onButtonDown={handleTabButtons("downloads")}
        style={{ height: "100%", overflowY: "auto", padding: "0 24px 110px", scrollPaddingBottom: "110px" }}
      >
        <TabBar currentId="downloads" />
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            margin: "12px 0 10px",
          }}
        >
          <h2 style={{ margin: 0 }}>Downloads</h2>
          <SpeedGraph samples={speedSamples} />
        </div>

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
                  ? [
                      d.bytesDone !== undefined && d.bytesTotal
                        ? `${formatBytes(d.bytesDone)} / ${formatBytes(d.bytesTotal)}`
                        : `${d.percent}%`,
                      d.bps ? formatSpeed(d.bps) : undefined,
                    ]
                      .filter(Boolean)
                      .join(" · ")
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
