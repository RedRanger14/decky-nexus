// Full-screen Updates: per-game rows with one-click update, update-all,
// and per-version dismissal ("skip this version") - collection-pinned
// mods never appear (their versions are curated).
import {
  DialogButton,
  Focusable,
  ScrollPanelGroup,
} from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useState } from "react";

import { showModal } from "@decky/ui";

import { dismissUpdate } from "./api";
import { PayloadChoiceModal } from "./ChoiceModal";
import { installLatest } from "./install";
import { PendingUpdate, scanUpdates } from "./updates";
import { PRIMARY_BUTTON_CLASS, PRIMARY_BUTTON_CSS } from "./theme";
import { TabBar, exitTabsToQam, handleTabButtons } from "./Tabs";

const Scroller: any = ScrollPanelGroup;

export function UpdatesPage() {
  const [pending, setPending] = useState<PendingUpdate[] | undefined>();
  const [busy, setBusy] = useState(false);

  const rescan = () => scanUpdates().then(setPending);
  useEffect(() => {
    rescan();
  }, []);

  const updateOne = async (
    u: PendingUpdate,
    payloadChoice = ""
  ): Promise<boolean> => {
    const result = await installLatest(
      u.game,
      u.modId,
      u.name,
      u.current,
      payloadChoice
    );
    if (result.ok) {
      setPending((prev) => prev?.filter((p) => p !== u));
      return true;
    }
    if (result.needs_choice && result.options?.length) {
      // Act, don't instruct. This used to toast "open the mod's page to
      // pick one" - Michael: "the toasts are tiny and fast, relying on me
      // to read them is ridiculous". The choice opens right here, and the
      // pick resumes the same update.
      return await new Promise<boolean>((resolve) => {
        const modal = showModal(
          <PayloadChoiceModal
            modName={u.name}
            options={result.options!}
            labels={result.option_labels}
            allowMerge={result.merge_allowed !== false}
            onPick={(opt) => resolve(updateOne(u, opt))}
            closeModal={() => {
              modal.Close();
              setTimeout(() => resolve(false), 0);
            }}
          />
        );
      });
    }
    toaster.toast({
      title: `${u.name} update failed`,
      body: result.error ?? "Unknown error - check the mod's page",
    });
    return false;
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
      // Count what actually happened. The old toast said "Updates applied"
      // unconditionally - Michael watched it claim success over an update
      // that was still sitting in the list.
      let ok = 0;
      let failed = 0;
      for (const u of [...pending]) {
        (await updateOne(u)) ? ok++ : failed++;
      }
      toaster.toast(
        failed === 0 && ok > 0
          ? {
              title: `${ok} update${ok === 1 ? "" : "s"} applied`,
              body: "Restart affected games to load them",
            }
          : {
              title:
                ok === 0
                  ? "No updates applied"
                  : `${ok} applied, ${failed} failed`,
              body:
                failed > 0
                  ? "The failed ones say why in their own toasts"
                  : "Nothing was pending",
            }
      );
      // Re-scan rather than trusting local bookkeeping: if a row comes
      // back, the update genuinely did not take, and the list says so.
      rescan();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Focusable
      // No autoFocus/onActivate here: the TabBar guarantees focusable
      // children, and a focusable root traps the gamepad focus.
      onButtonDown={handleTabButtons("updates")}
      onCancel={exitTabsToQam}
      style={{ marginTop: "40px", height: "calc(100% - 40px)" }}
    >
      <Scroller
        focusable={false}
        onButtonDown={handleTabButtons("updates")}
        style={{ height: "100%", overflowY: "auto", padding: "0 24px 110px", scrollPaddingBottom: "110px" }}
      >
        <style>{PRIMARY_BUTTON_CSS}</style>
        <TabBar currentId="updates" />
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
