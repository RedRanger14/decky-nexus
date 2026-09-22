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

import { dismissUpdate, getPluginUpdate, installFramework } from "./api";
import { PayloadChoiceModal } from "./ChoiceModal";
import { installLatest } from "./install";
import { PendingUpdate, scanUpdates } from "./updates";
import {
  PluginUpdate,
  UPDATE_WAIT_MS,
  formatReleaseNotes,
  pluginUpdateBlocked,
  pluginUpdateBody,
  pluginUpdateLabel,
  requestPluginUpdate,
  updateLanded,
} from "./pluginUpdate";
import {
  PAGE_SCROLLER,
  PRIMARY_BUTTON_CLASS,
  PRIMARY_BUTTON_CSS,
} from "./theme";
import { TabBar, exitTabsToQam, handleTabButtons } from "./Tabs";

const Scroller: any = ScrollPanelGroup;

export function UpdatesPage() {
  const [pending, setPending] = useState<PendingUpdate[] | undefined>();
  const [busy, setBusy] = useState(false);
  // The plugin's own update. It sits above the mods because it is the one
  // update that used to require Desktop Mode and a terminal, which for
  // this audience meant it never happened.
  const [self, setSelf] = useState<PluginUpdate | undefined>();
  // When the request went to Decky, not whether it did. Decky's prompt
  // can be cancelled and nothing tells us, so a one-way "sent" flag left
  // the button disabled for good: Michael hit that after a successful
  // update, and a cancel would have been the same dead end.
  const [selfSentAt, setSelfSentAt] = useState(0);
  const [selfDone, setSelfDone] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  const selfWaiting =
    selfSentAt > 0 && now - selfSentAt < UPDATE_WAIT_MS && !selfDone;
  const notes = formatReleaseNotes(self?.notes);
  useEffect(() => {
    getPluginUpdate()
      .then((u) => setSelf(u?.update_available ? u : undefined))
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (!selfSentAt || selfDone) return;
    // Decky restarts the plugin once it has installed the update, and
    // this page belongs to the instance being replaced, so it is never
    // told. Asking is the only way to know it landed, and the call
    // failing mid-swap is expected rather than an error.
    const id = setInterval(() => {
      setNow(Date.now());
      getPluginUpdate()
        .then((u) => {
          if (u?.current && self?.version && updateLanded(self.version, u.current)) {
            setSelfDone(true);
          }
        })
        .catch(() => {});
    }, 2000);
    return () => clearInterval(id);
  }, [selfSentAt, selfDone, self?.version]);

  const rescan = () => scanUpdates().then(setPending);
  useEffect(() => {
    rescan();
  }, []);

  const updateOne = async (
    u: PendingUpdate,
    payloadChoice = ""
  ): Promise<boolean> => {
    // A script extender is not a mod: it installs beside the game exe and
    // its right build is the one matching that exe. Re-running the framework
    // install picks by game version, so this needs no target passed in.
    // A blocked row is information, not an action: there is no build to
    // install. Guarded here too so "Update all" cannot try.
    if (u.blocked) {
      toaster.toast({ title: u.name, body: u.blocked });
      return false;
    }
    if (u.framework && u.game.framework) {
      const fw = u.game.framework;
      const result = await installFramework(
        u.game.nexusDomain,
        fw.nexusModId!,
        u.game.installDirName,
        fw.installKind ?? "copyRoot",
        fw.detectFile,
        fw.avoidFileKeywords ?? [],
        fw.installSubdir ?? "",
        u.game.modsSubdir,
        u.game.appId,
        u.game.launcherXmlSubpath ?? "",
        u.game.processName ?? ""
      );
      if (result.ok) {
        setPending((prev) => prev?.filter((p) => p !== u));
        toaster.toast({
          title: `${u.name} updated`,
          body:
            "matched_game_version" in result && result.matched_game_version
              ? `Now the build for game ${result.matched_game_version}`
              : "",
        });
        return true;
      }
      toaster.toast({
        title: `${u.name} update failed`,
        body: result.error ?? "Unknown error",
      });
      return false;
    }
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
        style={PAGE_SCROLLER}
      >
        <style>{PRIMARY_BUTTON_CSS}</style>
        <TabBar currentId="updates" />
        <h2 style={{ margin: "12px 0 4px" }}>Updates</h2>
        <div style={{ fontSize: "12.5px", opacity: 0.65, marginBottom: "10px" }}>
          Mods installed as part of a collection aren't shown - collections
          pin their versions on purpose.
        </div>

        {self && (
          <Focusable
            style={{
              margin: "0 0 14px",
              padding: "10px 12px",
              borderRadius: "6px",
              background: "rgba(218, 142, 53, 0.12)",
              border: "1px solid rgba(218, 142, 53, 0.35)",
              maxWidth: "560px",
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: "2px" }}>
              {selfDone ? `Updated to v${self.version} ✓` : pluginUpdateLabel(self)}
            </div>
            <div style={{ fontSize: "12.5px", opacity: 0.8, marginBottom: "8px" }}>
              {selfDone
                ? "Decky has installed it. Close this page and reopen the panel to see the new version."
                : pluginUpdateBlocked(self) || pluginUpdateBody(self)}
            </div>

            {!selfDone && notes.length > 0 && (
              <div
                style={{
                  fontSize: "12px",
                  opacity: 0.85,
                  margin: "0 0 10px",
                  padding: "8px 10px",
                  borderRadius: "4px",
                  background: "rgba(0, 0, 0, 0.22)",
                  maxHeight: notesOpen ? "none" : "92px",
                  overflow: "hidden",
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: "4px" }}>
                  What is in this update
                </div>
                {notes.map((line, i) =>
                  line === "" ? (
                    <div key={i} style={{ height: "6px" }} />
                  ) : (
                    <div
                      key={i}
                      style={{
                        marginBottom: "2px",
                        fontWeight: /^[A-Z0-9 ,'()-]+$/.test(line) ? 600 : 400,
                        opacity: /^[A-Z0-9 ,'()-]+$/.test(line) ? 0.9 : 0.8,
                      }}
                    >
                      {line}
                    </div>
                  )
                )}
              </div>
            )}

            <Focusable style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
              {!selfDone && !pluginUpdateBlocked(self) && (
                <DialogButton
                  className={PRIMARY_BUTTON_CLASS}
                  disabled={selfWaiting}
                  onClick={async () => {
                    setSelfSentAt(Date.now());
                    const err = await requestPluginUpdate(self);
                    if (err) {
                      setSelfSentAt(0);
                      toaster.toast({ title: "Nexus Mods", body: err });
                      return;
                    }
                    toaster.toast({
                      title: "Nexus Mods",
                      body: "Decky will ask you to confirm the update.",
                    });
                  }}
                >
                  {selfWaiting ? "Waiting for Decky…" : "⬆ Update the plugin"}
                </DialogButton>
              )}
              {!selfDone && notes.length > 0 && (
                <DialogButton onClick={() => setNotesOpen((v) => !v)}>
                  {notesOpen ? "Show less" : "Show more"}
                </DialogButton>
              )}
            </Focusable>
          </Focusable>
        )}

        {pending === undefined && (
          <div style={{ opacity: 0.8 }}>Checking your mods…</div>
        )}
        {pending !== undefined && pending.length === 0 && !self && (
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
                  {u.blocked
                    ? u.blocked
                    : u.framework
                    ? // Not "new version": a script extender's right build
                      // is the one matching the game, which after a
                      // downgrade is older than what is installed. Saying
                      // "new" there would look like a mistake.
                      `${u.game.displayName} · script extender, ` +
                      (u.installedVersion
                        ? `${u.installedVersion} installed, `
                        : "") +
                      `your game needs ${u.current}`
                      : `${u.game.displayName} · new version ${u.current}`}
                </div>
              </div>
              {/* No Update button on a blocked row: there is no build to
                  install, and offering one would be a lie. */}
              {!u.blocked && (
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
              )}
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
