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
  subscribeCollectionRun,
  subscribeDownloads,
} from "./state";
import { TabBar, handleTabButtons } from "./Tabs";

const Scroller: any = ScrollPanelGroup;

function Row({
  name,
  status,
  dim,
}: {
  name: string;
  status: string;
  dim?: boolean;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "8px 12px",
        background: "rgba(255,255,255,0.05)",
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
    </div>
  );
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
      // Even when the page has no focusable rows, B must land here (not
      // fall through to Steam's default close) - hence the focusable
      // fallback + autofocus.
      autoFocus={true}
      noFocusRing={true}
      // onActivate makes this a real focus target even with no focusable
      // children, so B lands on onCancel instead of Steam's default.
      onActivate={() => {}}
      onButtonDown={handleTabButtons("downloads")}
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
        <TabBar currentId="downloads" />
        <h2 style={{ margin: "12px 0 10px" }}>Downloads</h2>

        {run && (
          <div style={{ marginBottom: "14px" }}>
            <div style={{ fontSize: "14px", fontWeight: 600, marginBottom: "4px" }}>
              Collection: {run.finished}/{run.total}{" "}
              {run.running ? "installing…" : "finished"}
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
          </div>
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
              name={d.name}
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
