// "My Mods": everything installed across every supported game - the
// full-screen mod manager. Two views: loose Mods (default) and
// Collections (expandable, showing the mods each one installed).
// (Load-order editing and a health-check section are future additions.)
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
import { useEffect, useState } from "react";

import {
  InstalledCollectionInfo,
  InstalledMod,
  getInstalledMods,
  getModsByIds,
  setModEnabled,
  uninstallMod,
} from "./api";
import { ALL_GAMES, SupportedGame, modeParams } from "./games";
import { NEXUS_ORANGE } from "./theme";
import { TabBar, handleTabButtons } from "./Tabs";

const Scroller: any = ScrollPanelGroup;

interface GameMods {
  game: SupportedGame;
  mods: InstalledMod[];
  collections: Record<string, InstalledCollectionInfo>;
}

/** Mods installed as part of a collection group under it; the rest are
 * loose. Pre-slug records (before v0.17) sort under a legacy bucket. */
const LEGACY_SLUG = "__earlier__";

function collectionSlugOf(mod: InstalledMod): string | undefined {
  if (mod.collection_slug) return mod.collection_slug;
  if (mod.source === "collection") return LEGACY_SLUG;
  return undefined;
}

function Thumb({
  url,
  size,
  fallback,
}: {
  url?: string;
  size: { w: number; h: number };
  fallback: string;
}) {
  if (url) {
    return (
      <img
        src={url}
        alt=""
        loading="lazy"
        style={{
          width: `${size.w}px`,
          height: `${size.h}px`,
          objectFit: "cover",
          borderRadius: "3px",
          flexShrink: 0,
          background: "#0b0e13",
        }}
      />
    );
  }
  return (
    <div
      style={{
        width: `${size.w}px`,
        height: `${size.h}px`,
        borderRadius: "3px",
        flexShrink: 0,
        background: "rgba(255,255,255,0.08)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: `${Math.round(size.h * 0.5)}px`,
        opacity: 0.7,
      }}
    >
      {fallback}
    </div>
  );
}

function ModRow({
  game,
  mod,
  thumb,
  busy,
  onToggle,
  onRemove,
}: {
  game: SupportedGame;
  mod: InstalledMod;
  thumb?: string;
  busy: boolean;
  onToggle: (game: SupportedGame, mod: InstalledMod) => void;
  onRemove: (game: SupportedGame, mod: InstalledMod) => void;
}) {
  return (
    <Focusable
      style={{
        display: "flex",
        alignItems: "center",
        gap: "10px",
        padding: "6px 10px",
        background: "rgba(255,255,255,0.05)",
        borderRadius: "4px",
        opacity: mod.enabled ? 1 : 0.6,
      }}
    >
      <Thumb
        url={thumb}
        size={{ w: 46, h: 30 }}
        fallback={(mod.name ?? mod.folder).charAt(0).toUpperCase()}
      />
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
          {mod.name ?? mod.folder}
        </div>
        <div style={{ fontSize: "11.5px", opacity: 0.6 }}>
          {mod.version ? `v${mod.version}` : "untracked"}
          {mod.enabled ? "" : " · disabled"}
          {mod.togglable === false ? " · always active" : ""}
        </div>
      </div>
      {mod.togglable !== false && (
        <DialogButton
          disabled={busy}
          onClick={() => onToggle(game, mod)}
          style={{
            minWidth: "0",
            width: "auto",
            padding: "6px 14px",
            fontSize: "12.5px",
            flexShrink: 0,
          }}
        >
          {busy ? "…" : mod.enabled ? "Disable" : "Enable"}
        </DialogButton>
      )}
      <DialogButton
        disabled={busy}
        onClick={() => onRemove(game, mod)}
        style={{
          minWidth: "0",
          width: "auto",
          padding: "6px 14px",
          fontSize: "12.5px",
          flexShrink: 0,
          color: "#ff8a8a",
        }}
      >
        Uninstall
      </DialogButton>
    </Focusable>
  );
}

export function ManagerPage() {
  const [groups, setGroups] = useState<GameMods[] | undefined>();
  const [busyKey, setBusyKey] = useState<string | undefined>();
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const [view, setView] = useState<"mods" | "collections">("mods");
  const [openCollections, setOpenCollections] = useState<Set<string>>(
    new Set()
  );

  const refresh = async () => {
    const found: GameMods[] = [];
    for (const game of ALL_GAMES) {
      const r = await getInstalledMods(
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        ...modeParams(game),
        game.protectedModFolders ?? []
      );
      const mods = r.mods ?? [];
      if (mods.length > 0) {
        found.push({ game, mods, collections: r.collections ?? {} });
      }
    }
    setGroups(found);
    // Thumbnails arrive lazily: one batch lookup per game, merged in as
    // they land - rows render immediately with placeholders.
    for (const { game, mods } of found) {
      const ids = Array.from(
        new Set(
          mods
            .map((m) => m.mod_id)
            .filter((id): id is number => typeof id === "number" && id > 0)
        )
      );
      if (ids.length === 0) continue;
      getModsByIds(game.nexusDomain, ids)
        .then((res) => {
          if (!res.ok || !res.mods) return;
          setThumbs((prev) => {
            const next = { ...prev };
            for (const m of res.mods!) {
              if (m.thumbnailUrl ?? m.pictureUrl) {
                next[`${game.appId}:${m.modId}`] =
                  m.thumbnailUrl ?? m.pictureUrl!;
              }
            }
            return next;
          });
        })
        .catch(() => {});
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const toggle = async (game: SupportedGame, mod: InstalledMod) => {
    const key = `${game.appId}:${mod.folder}`;
    setBusyKey(key);
    try {
      const result = await setModEnabled(
        game.installDirName,
        game.modsSubdir,
        mod.folder,
        !mod.enabled,
        game.installMode ?? "folder",
        game.nexusDomain,
        game.appId,
        game.pluginsTxtSubpath ?? "",
        game.pluginsTxtStyle ?? "starred"
      );
      if (!result.ok) {
        toaster.toast({ title: "Could not toggle", body: result.error ?? "" });
      }
    } finally {
      setBusyKey(undefined);
      refresh();
    }
  };

  const remove = (game: SupportedGame, mod: InstalledMod) => {
    showModal(
      <ConfirmModal
        strTitle={`Uninstall ${mod.name ?? mod.folder}?`}
        strDescription="You can reinstall it from the store at any time."
        strOKButtonText="Uninstall"
        bDestructiveWarning={true}
        onOK={async () => {
          const result = await uninstallMod(
            game.nexusDomain,
            game.installDirName,
            game.modsSubdir,
            mod.folder,
            ...modeParams(game)
          );
          toaster.toast(
            result.ok
              ? { title: "Uninstalled", body: mod.name ?? mod.folder }
              : { title: "Uninstall failed", body: result.error ?? "" }
          );
          refresh();
        }}
      />
    );
  };

  // ---- split each game's mods into loose vs per-collection ----
  const looseByGame = (groups ?? []).map(({ game, mods }) => ({
    game,
    mods: mods.filter((m) => collectionSlugOf(m) === undefined),
  }));
  const collectionsByGame = (groups ?? []).map(
    ({ game, mods, collections }) => {
      const bySlug = new Map<string, InstalledMod[]>();
      for (const m of mods) {
        const slug = collectionSlugOf(m);
        if (!slug) continue;
        if (!bySlug.has(slug)) bySlug.set(slug, []);
        bySlug.get(slug)!.push(m);
      }
      return { game, bySlug, collections };
    }
  );
  const looseTotal = looseByGame.reduce((n, g) => n + g.mods.length, 0);
  const collectionsTotal = collectionsByGame.reduce(
    (n, g) => n + g.bySlug.size,
    0
  );

  const subTab = (id: "mods" | "collections", label: string) => (
    <DialogButton
      onClick={() => setView(id)}
      style={{
        minWidth: "0",
        width: "auto",
        padding: "6px 18px",
        fontSize: "13px",
        fontWeight: view === id ? 600 : 400,
        borderBottom:
          view === id
            ? `2px solid ${NEXUS_ORANGE}`
            : "2px solid transparent",
        borderRadius: "4px 4px 0 0",
        background: view === id ? "rgba(218,142,53,0.12)" : "transparent",
      }}
    >
      {label}
    </DialogButton>
  );

  return (
    <Focusable
      autoFocus={true}
      noFocusRing={true}
      onActivate={() => {}}
      onButtonDown={handleTabButtons("manager")}
      onCancel={() => {
        // QAM first so gamepad focus lands INSIDE it - then pop the page.
        Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);
        setTimeout(() => Navigation.NavigateBack(), 50);
      }}
      style={{ marginTop: "40px", height: "calc(100% - 40px)" }}
    >
      <Scroller
        focusable={false}
        style={{ height: "100%", overflowY: "auto", padding: "0 24px 110px", scrollPaddingBottom: "110px" }}
      >
        <TabBar currentId="manager" />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            margin: "6px 0 10px",
          }}
        >
          <h2 style={{ margin: 0 }}>My Mods</h2>
          <Focusable style={{ display: "flex", gap: "6px" }}>
            {subTab("mods", `Mods (${looseTotal})`)}
            {subTab("collections", `Collections (${collectionsTotal})`)}
          </Focusable>
        </div>

        {groups === undefined && (
          <div style={{ opacity: 0.8 }}>Reading your games…</div>
        )}
        {groups !== undefined && groups.length === 0 && (
          <div style={{ opacity: 0.8 }}>
            Nothing installed yet - the Store tab is where it starts.
          </div>
        )}

        {view === "mods" &&
          looseByGame
            .filter((g) => g.mods.length > 0)
            .map(({ game, mods }) => (
              <div key={game.appId} style={{ marginBottom: "16px" }}>
                <div
                  style={{
                    fontSize: "14px",
                    fontWeight: 700,
                    margin: "6px 0",
                    opacity: 0.9,
                  }}
                >
                  {game.displayName}{" "}
                  <span style={{ opacity: 0.55, fontWeight: 400 }}>
                    · {mods.length} mod{mods.length === 1 ? "" : "s"}
                  </span>
                </div>
                <Focusable
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "4px",
                  }}
                >
                  {mods.map((mod) => (
                    <ModRow
                      key={`${game.appId}:${mod.folder}`}
                      game={game}
                      mod={mod}
                      thumb={thumbs[`${game.appId}:${mod.mod_id}`]}
                      busy={busyKey === `${game.appId}:${mod.folder}`}
                      onToggle={toggle}
                      onRemove={remove}
                    />
                  ))}
                </Focusable>
              </div>
            ))}
        {view === "mods" && groups !== undefined && looseTotal === 0 && (
          <div style={{ opacity: 0.7, fontSize: "13px" }}>
            No individually installed mods - check the Collections view.
          </div>
        )}

        {view === "collections" &&
          collectionsByGame
            .filter((g) => g.bySlug.size > 0)
            .map(({ game, bySlug, collections }) => (
              <div key={game.appId} style={{ marginBottom: "16px" }}>
                <div
                  style={{
                    fontSize: "14px",
                    fontWeight: 700,
                    margin: "6px 0",
                    opacity: 0.9,
                  }}
                >
                  {game.displayName}
                </div>
                <Focusable
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "6px",
                  }}
                >
                  {Array.from(bySlug.entries()).map(([slug, mods]) => {
                    const key = `${game.appId}:${slug}`;
                    const info = collections[slug];
                    const title =
                      slug === LEGACY_SLUG
                        ? "Collection (installed before v0.17)"
                        : info?.title ?? slug;
                    const open = openCollections.has(key);
                    return (
                      <div key={key}>
                        <Focusable
                          onActivate={() =>
                            setOpenCollections((prev) => {
                              const next = new Set(prev);
                              if (next.has(key)) next.delete(key);
                              else next.add(key);
                              return next;
                            })
                          }
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "12px",
                            padding: "8px 10px",
                            background: "rgba(218,142,53,0.10)",
                            border: `1px solid ${
                              open ? NEXUS_ORANGE + "88" : "transparent"
                            }`,
                            borderRadius: "6px",
                          }}
                        >
                          <Thumb
                            url={info?.thumb_url}
                            size={{ w: 72, h: 44 }}
                            fallback="📦"
                          />
                          <div style={{ flexGrow: 1, minWidth: 0 }}>
                            <div
                              style={{
                                fontSize: "14px",
                                fontWeight: 600,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {title}
                            </div>
                            <div style={{ fontSize: "12px", opacity: 0.65 }}>
                              {mods.length} mod{mods.length === 1 ? "" : "s"}{" "}
                              installed
                              {info?.mod_count
                                ? ` · ${info.mod_count} in the collection`
                                : ""}
                            </div>
                          </div>
                          <div style={{ fontSize: "16px", opacity: 0.7 }}>
                            {open ? "▾" : "▸"}
                          </div>
                        </Focusable>
                        {open && (
                          <Focusable
                            style={{
                              display: "flex",
                              flexDirection: "column",
                              gap: "4px",
                              margin: "4px 0 4px 16px",
                            }}
                          >
                            {mods.map((mod) => (
                              <ModRow
                                key={`${game.appId}:${mod.folder}`}
                                game={game}
                                mod={mod}
                                thumb={thumbs[`${game.appId}:${mod.mod_id}`]}
                                busy={
                                  busyKey === `${game.appId}:${mod.folder}`
                                }
                                onToggle={toggle}
                                onRemove={remove}
                              />
                            ))}
                          </Focusable>
                        )}
                      </div>
                    );
                  })}
                </Focusable>
              </div>
            ))}
        {view === "collections" &&
          groups !== undefined &&
          collectionsTotal === 0 && (
            <div style={{ opacity: 0.7, fontSize: "13px" }}>
              No collections installed yet - find them on the Store tab.
            </div>
          )}
      </Scroller>
    </Focusable>
  );
}
