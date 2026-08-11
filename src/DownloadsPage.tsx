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
  CollectionRun,
  clearCompletedDownloads,
  getAggregateBps,
  getCollectionRun,
  getCompletedDownloads,
  getDownloads,
  getRunSkippedCount,
  getSpeedHistory,
  recordSpeedSample,
  setDetailOrigin,
  setSelectedCollection,
  setSelectedMod,
  subscribeCollectionRun,
  subscribeDownloads,
} from "./state";
import {
  cancelDownload,
  getDiskUsage,
  getDownloadControl,
  getModDetails,
  setDownloadsPaused,
} from "./api";
import { cancellableDownload, pauseAllControl } from "./panelRules";
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

/** Live download-speed sparkline. Samples are recorded in the global
 * store on every progress event (so sub-second downloads register) and
 * bucketed into 500ms columns over a 60s window here. */
function SpeedGraph({ samples, current }: { samples: number[]; current: number }) {
  const W = 180;
  const H = 44;
  const peak = Math.max(...samples, 1);
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
  pulse,
  onActivate,
}: {
  name: string;
  status: string;
  dim?: boolean;
  /** In-flight rows fill orange left-to-right - the row IS the bar. */
  pct?: number;
  /** Actively installing: breathe so it reads as "working", not stuck. */
  pulse?: boolean;
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
        animation: pulse ? "nexusInstallPulse 1.4s ease-in-out infinite" : undefined,
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

/** Disk gauge: used-space bar with the free-space floor marked. Pairs
 * with the speed graph top-right; refreshed while the page is open. */
function DiskGauge({
  totalGb,
  freeGb,
  minFreeGb,
}: {
  totalGb: number;
  freeGb: number;
  minFreeGb: number;
}) {
  const W = 180;
  const H = 44;
  const usedFrac = totalGb > 0 ? (totalGb - freeGb) / totalGb : 0;
  const floorFrac = totalGb > 0 ? 1 - minFreeGb / totalGb : 1;
  const nearFloor = freeGb <= minFreeGb * 2;
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
        style={{ background: "rgba(255,255,255,0.05)", borderRadius: "4px" }}
      >
        <rect
          x={0}
          y={H / 2 - 7}
          width={W}
          height={14}
          rx={4}
          fill="rgba(255,255,255,0.10)"
        />
        <rect
          x={0}
          y={H / 2 - 7}
          width={Math.max(2, usedFrac * W)}
          height={14}
          rx={4}
          fill={nearFloor ? "#e05c5c" : "rgba(218,142,53,0.85)"}
        />
        {/* the min-free floor: downloads stop past this line */}
        <line
          x1={floorFrac * W}
          x2={floorFrac * W}
          y1={H / 2 - 11}
          y2={H / 2 + 11}
          stroke="#e05c5c"
          strokeWidth={1.5}
          strokeDasharray="3,2"
        />
      </svg>
      <span
        style={{
          fontSize: "12px",
          opacity: 0.85,
          color: nearFloor ? "#e05c5c" : undefined,
        }}
      >
        {freeGb >= 100 ? Math.round(freeGb) : freeGb.toFixed(1)} GB free
      </span>
    </div>
  );
}

/** The collection in flight IS the page's centerpiece: blurred cover art
 * as a backdrop, the cover proper as a card, live counts, an honest
 * pace-based ETA, and a glowing brand-orange progress bar. */
function CollectionHero({
  run,
  activeNames,
}: {
  run: CollectionRun;
  activeNames: string[];
}) {
  const pct = run.total ? Math.round((run.finished / run.total) * 100) : 0;
  const skipped = getRunSkippedCount(run);
  // ETA from observed pace (mods/min). Only shown once a few mods are in
  // - earlier than that it's noise, not information.
  let eta: string | undefined;
  if (run.running && run.startedAt && run.finished >= 3) {
    const perMod = (Date.now() - run.startedAt) / run.finished;
    const mins = Math.round((perMod * (run.total - run.finished)) / 60_000);
    eta =
      mins < 1
        ? "under a minute left"
        : mins === 1
        ? "about 1 minute left"
        : mins < 90
        ? `about ${mins} minutes left`
        : `about ${(mins / 60).toFixed(1)} hours left`;
  }
  return (
    <Focusable
      onActivate={() => Navigation.Navigate("/nexus-mods/collection")}
      style={{
        position: "relative",
        borderRadius: "12px",
        overflow: "hidden",
        marginBottom: "18px",
        border: "1px solid rgba(218,142,53,0.35)",
        background: "#14161c",
      }}
    >
      {/* Backdrop: the collection's own art, blurred into atmosphere. */}
      {run.thumbnailUrl && (
        <img
          src={run.thumbnailUrl}
          aria-hidden
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
            filter: "blur(28px) saturate(1.2) brightness(0.5)",
            transform: "scale(1.25)",
          }}
        />
      )}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "linear-gradient(105deg, rgba(10,11,15,0.92) 0%, rgba(10,11,15,0.6) 55%, rgba(218,142,53,0.16) 100%)",
        }}
      />
      <div style={{ position: "relative", display: "flex", gap: "16px", padding: "16px" }}>
        {run.thumbnailUrl && (
          <img
            src={run.thumbnailUrl}
            alt=""
            style={{
              width: "116px",
              height: "116px",
              objectFit: "cover",
              borderRadius: "8px",
              boxShadow: "0 4px 18px rgba(0,0,0,0.55)",
              flexShrink: 0,
            }}
          />
        )}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            gap: "5px",
          }}
        >
          <div
            style={{
              fontSize: "11px",
              letterSpacing: "1.5px",
              textTransform: "uppercase",
              opacity: 0.7,
            }}
          >
            {run.running ? "Installing collection" : "Collection finished"}
          </div>
          <div
            style={{
              fontSize: "20px",
              fontWeight: 700,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {run.name ?? "Collection"}
          </div>
          <div style={{ fontSize: "13px", opacity: 0.85 }}>
            {run.finished} of {run.total} mods
            {eta ? ` · ${eta}` : ""}
            {skipped > 0 && (
              <span style={{ color: "#4aa9ff" }}> · {skipped} need choices</span>
            )}
          </div>
          {run.running && activeNames.length > 0 && (
            <div
              style={{
                fontSize: "12px",
                opacity: 0.65,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {activeNames.join(" · ")}
            </div>
          )}
          <div style={{ marginTop: "auto", paddingTop: "6px" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: "11.5px",
                opacity: 0.75,
                marginBottom: "3px",
              }}
            >
              <span>{pct}%</span>
              <span>{run.running ? "tap to open" : "tap to finish setup"}</span>
            </div>
            <div
              style={{
                height: "8px",
                background: "rgba(255,255,255,0.12)",
                borderRadius: "4px",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${pct}%`,
                  height: "100%",
                  background: "linear-gradient(90deg, #da8e35, #f0b160)",
                  borderRadius: "4px",
                  transition: "width 0.4s ease",
                  boxShadow: "0 0 10px rgba(218,142,53,0.6)",
                }}
              />
            </div>
          </div>
        </div>
      </div>
    </Focusable>
  );
}

export function DownloadsPage() {
  const [, force] = useState(0);
  const [disk, setDisk] = useState<
    { totalGb: number; freeGb: number; minFreeGb: number } | undefined
  >();
  // Backend truth, not page memory: a reopened page must show a pause
  // that was set an hour ago.
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    const un1 = subscribeDownloads(() => force((n) => n + 1));
    const un2 = subscribeCollectionRun(() => force((n) => n + 1));
    const pollDisk = () =>
      getDiskUsage().then((r) => {
        if (r.ok)
          setDisk({
            totalGb: r.total_gb ?? 0,
            freeGb: r.free_gb ?? 0,
            minFreeGb: r.min_free_gb ?? 5,
          });
      });
    pollDisk();
    getDownloadControl().then((r) => setPaused(Boolean(r.paused)));
    const diskTimer = setInterval(pollDisk, 3000);
    // 250ms tick: records idle samples (event-driven sampling only fires
    // while bytes flow) and keeps the graph/ETA visibly live.
    const timer = setInterval(() => {
      recordSpeedSample();
      force((n) => n + 1);
    }, 250);
    return () => {
      un1();
      un2();
      clearInterval(timer);
      clearInterval(diskTimer);
    };
  }, []);

  const active = getDownloads();
  const completed = getCompletedDownloads();
  const run = getCollectionRun();

  // Bucket the event-driven history into 500ms columns over 60s: a mod
  // that downloaded in 400ms still owns a visible column.
  const WINDOW_MS = 60_000;
  const BUCKET_MS = 500;
  const now = Date.now();
  const speedSamples = new Array(WINDOW_MS / BUCKET_MS).fill(0);
  for (const s of getSpeedHistory()) {
    const age = now - s.t;
    if (age < 0 || age >= WINDOW_MS) continue;
    const i = speedSamples.length - 1 - Math.floor(age / BUCKET_MS);
    speedSamples[i] = Math.max(speedSamples[i], s.bps);
  }

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
        <style>{`
          @keyframes nexusInstallPulse {
            0%, 100% { filter: brightness(1); }
            50% { filter: brightness(1.45); }
          }
        `}</style>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            margin: "12px 0 10px",
          }}
        >
          <h2 style={{ margin: 0 }}>Downloads</h2>
          <div style={{ display: "flex", gap: "14px" }}>
            {disk && (
              <DiskGauge
                totalGb={disk.totalGb}
                freeGb={disk.freeGb}
                minFreeGb={disk.minFreeGb}
              />
            )}
            <SpeedGraph samples={speedSamples} current={getAggregateBps()} />
          </div>
        </div>

        {run && (
          <CollectionHero
            run={run}
            activeNames={active
              .filter((d) => d.phase === "downloading")
              .map((d) => d.name)}
          />
        )}

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            margin: "8px 0 6px",
          }}
        >
          <span style={{ fontSize: "13px", fontWeight: 600 }}>
            Active ({active.length})
          </span>
          {pauseAllControl(active.length, paused).show && (
            <DialogButton
              onClick={async () => {
                const next = !paused;
                setPaused(next);
                await setDownloadsPaused(next);
              }}
              style={{
                minWidth: "0",
                width: "auto",
                padding: "4px 12px",
                fontSize: "12px",
              }}
            >
              {pauseAllControl(active.length, paused).label}
            </DialogButton>
          )}
        </div>
        <Focusable style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          {active.length === 0 && (
            <div style={{ fontSize: "13px", opacity: 0.6 }}>
              Nothing downloading right now.
            </div>
          )}
          {active.map((d) => (
            <Focusable
              key={d.modId}
              style={{ display: "flex", gap: "4px", alignItems: "stretch" }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <Row
                  onActivate={() =>
                    openDownloadTarget(
                      d.modId,
                      d.gameAppId,
                      d.collectionSlug,
                      d.name
                    )
                  }
                  name={d.name}
                  pct={d.phase === "extracting" ? 100 : d.percent}
                  pulse={d.phase === "extracting"}
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
                      ? "⚙ Installing…"
                      : d.phase === "paused"
                      ? "⏸ Paused"
                      : d.phase === "queued"
                      ? "✓ Downloaded · waiting to install"
                      : "Starting…"
                  }
                />
              </div>
              {cancellableDownload(d.phase) && (
                <DialogButton
                  onClick={() => cancelDownload(d.modId)}
                  style={{
                    minWidth: "0",
                    width: "36px",
                    padding: "0",
                    fontSize: "14px",
                    flexShrink: 0,
                  }}
                >
                  ✕
                </DialogButton>
              )}
            </Focusable>
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
              status={
                d.phase === "done"
                  ? "Done ✓"
                  : d.phase === "cancelled"
                  ? "Cancelled"
                  : "Failed ⚠"
              }
              dim
            />
          ))}
        </Focusable>
      </Scroller>
    </Focusable>
  );
}
