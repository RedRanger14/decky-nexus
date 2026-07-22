// Full-screen Updates: per-game rows with one-click update, update-all,
// and per-version dismissal ("skip this version") - collection-pinned
// mods never appear (their versions are curated).
import {
  DialogButton,
  Focusable,
  Navigation,
  QuickAccessTab,
  ScrollPanelGroup,
} from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useState } from "react";

import { dismissUpdate } from "./api";
import { installLatest } from "./install";
import { PendingUpdate, scanUpdates } from "./updates";
import { PRIMARY_BUTTON_CLASS, PRIMARY_BUTTON_CSS } from "./theme";

const Scroller: any = ScrollPanelGroup;

export function UpdatesPage() {
  const [pending, setPending] = useState<PendingUpdate[] | undefined>();
  const [busy, setBusy] = useState(false);

  const rescan = () => scanUpdates().then(setPending);
  useEffect(() => {
    rescan();
  }, []);

  const updateOne = async (u: PendingUpdate) => {
    const result = await installLatest(u.game, u.modId, u.name, u.current);
    if (result.ok) {
      setPending((prev) => prev?.filter((p) => p !== u));
    } else {
      toaster.toast({ title: `${u.name} update failed`, body: result.error ?? "" });
    }
  };

  const skipOne = async (u: PendingUpdate) => {
    const result = await dismissUpdate(
      u.game.nexusDomain,
      u.folder,
      u.current
    );
    if (result.ok) {
      setPending((prev) => prev?.filter((p) => p !== u));
    }
  };

  const updateAll = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      for (const u of [...pending]) {
        await updateOne(u);
      }
      toaster.toast({
        title: "Updates applied",
        body: "Restart affected games to load them",
      });
    } finally {
      setBusy(false);
    }
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
        <h2 style={{ margin: "12px 0 4px" }}>Updates</h2>
        <div style={{ fontSize: "12.5px", opacity: 0.65, marginBottom: "10px" }}>
          Mods installed as part of a collection aren't shown - collections
          pin their versions on purpose.
        </div>

        {pending === undefined && (
          <div style={{ opacity: 0.8 }}>Checking your mods…</div>
        )}
        {pending !== undefined && pending.length === 0 && (
          <div style={{ opacity: 0.8 }}>Everything is up to date ✓</div>
        )}

        {pending && pending.length > 0 && (
          <Focusable
            autoFocus={true}
            style={{ margin: "0 0 12px", maxWidth: "420px" }}
          >
            <DialogButton
              className={PRIMARY_BUTTON_CLASS}
              disabled={busy}
              onClick={updateAll}
            >
              {busy ? "Updating…" : `⬆ Update all (${pending.length})`}
            </DialogButton>
          </Focusable>
        )}

        <Focusable style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          {(pending ?? []).map((u) => (
            <Focusable
              key={`${u.game.appId}:${u.folder}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                padding: "8px 12px",
                background: "rgba(255,255,255,0.05)",
                borderRadius: "4px",
              }}
            >
              <div style={{ flexGrow: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: "13.5px",
                    fontWeight: 600,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {u.name}
                </div>
                <div style={{ fontSize: "12px", opacity: 0.65 }}>
                  {u.game.displayName} · new version {u.current}
                </div>
              </div>
              <DialogButton
                disabled={busy}
                onClick={() => updateOne(u)}
                style={{
                  minWidth: "0",
                  width: "auto",
                  padding: "6px 14px",
                  fontSize: "12.5px",
                  flexShrink: 0,
                }}
              >
                ⬆ Update
              </DialogButton>
              <DialogButton
                disabled={busy}
                onClick={() => skipOne(u)}
                style={{
                  minWidth: "0",
                  width: "auto",
                  padding: "6px 14px",
                  fontSize: "12.5px",
                  flexShrink: 0,
                }}
              >
                Skip
              </DialogButton>
            </Focusable>
          ))}
        </Focusable>
      </Scroller>
    </Focusable>
  );
}
