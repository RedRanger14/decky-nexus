// "My Mods": everything installed across every supported game - the
// full-screen mod manager. Split view: loose mods on the left,
// collections (expandable, with whole-collection toggles) on the right.
// (Load-order editing and a health-check section are future additions.)
import {
  ConfirmModal,
  DialogButton,
  Focusable,
  Router,
  ScrollPanelGroup,
  showModal,
} from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { FaEye } from "react-icons/fa";

import {
  AttentionItem,
  InstalledCollectionInfo,
  InstalledMod,
  getInstalledMods,
  getModDetails,
  getModsByIds,
  setModEnabled,
  uninstallCollection,
  uninstallMod,
  getShowAdult,
} from "./api";
import { ALL_GAMES, SupportedGame, getActiveGame, modeParams } from "./games";
import {
  getBrowseGame,
  setDetailOrigin,
  setSelectedCollection,
  setSelectedMod,
  clearManagerReturn,
  managerReturnsToMod,
} from "./state";
import {
  BLUE_BUTTON_CLASS,
  NEXUS_ORANGE,
  PRIMARY_BUTTON_CSS,
  WHITE_BUTTON_CLASS,
} from "./theme";
import { OrangeToggle } from "./Toggle";
import {
  TabBar,
  exitTabsToQam,
  handleTabButtons,
  popOurPage,
  pushOurPage,
} from "./Tabs";

const Scroller: any = ScrollPanelGroup;

interface GameMods {
  game: SupportedGame;
  mods: InstalledMod[];
  collections: Record<string, InstalledCollectionInfo>;
  attention: Record<string, AttentionItem[]>;
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
  blur,
}: {
  url?: string;
  size: { w: number; h: number };
  fallback: string;
  /** The account's own adult-image preference, honoured here too. The
   * browse rows and the mod page have always blurred; these thumbnails
   * did not, so a setting the user set once was being kept in two places
   * out of three. Michael: "the small mod thumbnails on the my mods
   * section are not respecting the adult content settings for blur". */
  blur?: boolean;
}) {
  if (url) {
    return (
      <img
        src={url}
        alt=""
        loading="lazy"
        decoding="async"
        style={{
          width: `${size.w}px`,
          height: `${size.h}px`,
          objectFit: "cover",
          filter: blur ? "blur(9px)" : undefined,
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
        fontSize: `${Math.round(size.h * 0.45)}px`,
        opacity: 0.7,
      }}
    >
      {fallback}
    </div>
  );
}

/** Eye button: jump to the mod's full detail page. */
async function openModDetail(game: SupportedGame, mod: InstalledMod) {
  if (!mod.mod_id) return;
  const result = await getModDetails(game.nexusDomain, mod.mod_id);
  if (result.ok && result.mod) {
    setSelectedMod({ game, mod: result.mod });
    setDetailOrigin("browse"); // B pops back here, not to the QAM
    pushOurPage("/nexus-mods/mod");
  } else {
    toaster.toast({
      title: "Could not open mod",
      body: result.error ?? mod.name ?? mod.folder,
    });
  }
}

function ModRow({
  game,
  mod,
  thumb,
  blur,
  busy,
  onToggle,
  onRemove,
}: {
  game: SupportedGame;
  mod: InstalledMod;
  thumb?: string;
  blur?: boolean;
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
        padding: "8px 10px",
        background: "rgba(255,255,255,0.05)",
        borderRadius: "4px",
        opacity: mod.enabled ? 1 : 0.6,
      }}
    >
      <Thumb
        url={thumb}
        blur={blur}
        size={{ w: 64, h: 40 }}
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
        {/* The reason lives on the row, not in a log. A mod switched off
            because it cannot work looks exactly like one the user turned
            off, so without this the obvious response is to turn it back
            on - which is what stopped a device booting. */}
        {!mod.enabled && mod.disabled_reason && (
          <div
            style={{
              fontSize: "11px",
              marginTop: "2px",
              lineHeight: 1.3,
              color: NEXUS_ORANGE,
              opacity: 0.85,
            }}
          >
            {mod.disabled_reason}
          </div>
        )}
      </div>
      {mod.mod_id !== undefined && mod.mod_id > 0 && (
        <DialogButton
          disabled={busy}
          onClick={() => openModDetail(game, mod)}
          style={{
            minWidth: "0",
            width: "40px",
            padding: "8px 0",
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <FaEye size={13} />
        </DialogButton>
      )}
      {mod.togglable !== false && (
        <OrangeToggle
          checked={mod.enabled}
          disabled={busy}
          onChange={() => onToggle(game, mod)}
        />
      )}
      <DialogButton
        disabled={busy}
        onClick={() => onRemove(game, mod)}
        style={{
          minWidth: "0",
          width: "auto",
          padding: "6px 12px",
          fontSize: "12px",
          flexShrink: 0,
          opacity: 0.85,
        }}
      >
        Uninstall
      </DialogButton>
    </Focusable>
  );
}

export function ManagerPage() {
  // Same scope resolution as the Store tab: the game the user is browsing
  // (or running) is the one whose mods they expect to see. Everything
  // else hides behind an explicit "All games" toggle - seeing Skyrim
  // collections while managing Fallout 4 reads as a bug.
  const scopeGame =
    getBrowseGame() ??
    getActiveGame(
      Router.MainRunningApp ? Number(Router.MainRunningApp.appid) : undefined
    );
  const [showAllGames, setShowAllGames] = useState(false);
  const [groups, setGroups] = useState<GameMods[] | undefined>();
  const [busyKey, setBusyKey] = useState<string | undefined>();
  // Which installed mods are adult, and whether this account blurs them.
  // A ref because it is filled by the same fetch that fills thumbs and
  // read in the same render pass.
  const adultRef = useRef<Set<number>>(new Set());
  const [blurAdult, setBlurAdult] = useState(false);
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
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
        found.push({
          game,
          mods,
          collections: r.collections ?? {},
          attention: r.attention ?? {},
        });
      }
    }
    setGroups(found);
    // Thumbnails arrive lazily: one batched lookup per game, merged in
    // as they land - rows render immediately with placeholders.
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
              if (m.adultContent) {
                adultRef.current.add(m.modId);
              }
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
    // The account decides this, not the plugin - same source the browse
    // rows and the mod page read.
    getShowAdult().then((r) => {
      if (r.ok) setBlurAdult(Boolean(r.blur_adult));
    });
  }, []);

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

  /** Whole-collection switch: flips every toggleable member. */
  const toggleCollection = async (
    game: SupportedGame,
    slug: string,
    members: InstalledMod[],
    enable: boolean
  ) => {
    const key = `${game.appId}:coll:${slug}`;
    setBusyKey(key);
    try {
      for (const mod of members) {
        if (mod.togglable === false || mod.enabled === enable) continue;
        await setModEnabled(
          game.installDirName,
          game.modsSubdir,
          mod.folder,
          enable,
          game.installMode ?? "folder",
          game.nexusDomain,
          game.appId,
          game.pluginsTxtSubpath ?? "",
          game.pluginsTxtStyle ?? "starred"
        );
      }
      toaster.toast({
        title: enable ? "Collection enabled" : "Collection disabled",
        body: `${members.filter((m) => m.togglable !== false).length} mods ${
          enable ? "activated" : "deactivated"
        }`,
      });
    } finally {
      setBusyKey(undefined);
      refresh();
    }
  };

  /** "Finish installing" jumps to the collection page, where the
   * Finish-setup flow walks the pending wizards/choices. */
  const openCollectionPage = (
    game: SupportedGame,
    slug: string,
    info?: InstalledCollectionInfo
  ) => {
    setSelectedCollection({
      game,
      collection: {
        name: info?.title ?? slug,
        slug,
        summary: "",
        endorsements: 0,
        author: "",
        modCount: info?.mod_count ?? 0,
        totalSize: 0,
        thumbnailUrl: info?.thumb_url,
      },
    });
    pushOurPage("/nexus-mods/collection");
  };

  const removeCollection = (
    game: SupportedGame,
    slug: string,
    title: string,
    memberCount: number
  ) => {
    showModal(
      <ConfirmModal
        strTitle={`Uninstall ${title}?`}
        strDescription={
          `Removes the ${memberCount} mods this collection installed. ` +
          `Mods you installed yourself (or via another collection) stay.`
        }
        strOKButtonText="Uninstall collection"
        bDestructiveWarning={true}
        onOK={async () => {
          const result = await uninstallCollection(
            game.nexusDomain,
            game.installDirName,
            game.modsSubdir,
            ...modeParams(game),
            slug
          );
          toaster.toast(
            result.ok
              ? {
                  title: `${title} uninstalled`,
                  body: `${result.removed ?? 0} mods removed`,
                }
              : { title: "Uninstall failed", body: result.error ?? "" }
          );
          refresh();
        }}
      />
    );
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
  // Membership comes from the collection's registered mod-id list when
  // available: a mod installed individually (or by another collection)
  // still counts toward every collection that pins it. Record slugs are
  // the fallback for entries registered before mod_ids existed.
  interface CollectionEntry {
    slug: string;
    info?: InstalledCollectionInfo;
    members: InstalledMod[];
    /** Wizard/option decisions still waiting - "Finish installing". */
    pendingChoices?: number;
  }
  const allGrouped = (groups ?? []).map(
    ({ game, mods, collections, attention }) => {
    const claimed = new Set<string>();
    const entries: CollectionEntry[] = [];
    for (const [slug, info] of Object.entries(collections)) {
      const idSet = new Set(info.mod_ids ?? []);
      const members = mods.filter(
        (m) =>
          (m.mod_id !== undefined && idSet.has(m.mod_id)) ||
          m.collection_slug === slug
      );
      if (members.length === 0) continue;
      const pendingChoices = (attention[slug] ?? []).filter(
        (a) => a.reason === "choices" || a.reason === "fomod"
      ).length;
      entries.push({ slug, info, members, pendingChoices });
      members.forEach((m) => claimed.add(m.folder));
    }
    const legacy = mods.filter(
      (m) => collectionSlugOf(m) !== undefined && !claimed.has(m.folder)
    );
    if (legacy.length > 0) {
      entries.push({ slug: LEGACY_SLUG, members: legacy });
      legacy.forEach((m) => claimed.add(m.folder));
    }
    const loose = mods.filter(
      (m) => !claimed.has(m.folder) && collectionSlugOf(m) === undefined
    );
    return { game, loose, entries };
    }
  );
  const scoped = scopeGame !== undefined && !showAllGames;
  const grouped = scoped
    ? allGrouped.filter((g) => g.game.appId === scopeGame!.appId)
    : allGrouped;
  const hiddenGames = allGrouped.length - grouped.length;
  const looseTotal = grouped.reduce((n, g) => n + g.loose.length, 0);
  const collectionsTotal = grouped.reduce(
    (n, g) => n + g.entries.length,
    0
  );

  const columnHeader = (label: string) => (
    <div
      style={{
        fontSize: "15px",
        fontWeight: 700,
        padding: "0 0 6px",
        borderBottom: `2px solid ${NEXUS_ORANGE}55`,
        marginBottom: "10px",
      }}
    >
      {label}
    </div>
  );

  return (
    <Focusable
      // No autoFocus/onActivate here: the TabBar guarantees focusable
      // children, and a focusable root traps the gamepad focus.
      onButtonDown={handleTabButtons("manager")}
      onCancel={() => {
        // Opened from a mod page that could not install: B returns to it,
        // one press from trying again now the conflict is resolved.
        if (managerReturnsToMod()) {
          clearManagerReturn();
          popOurPage();
          return;
        }
        exitTabsToQam();
      }}
      style={{ marginTop: "40px", height: "calc(100% - 40px)" }}
    >
      <Scroller
        focusable={false}
        onButtonDown={handleTabButtons("manager")}
        style={{ height: "100%", overflowY: "auto", padding: "0 24px 110px", scrollPaddingBottom: "110px" }}
      >
        <TabBar currentId="manager" />
        <style>{PRIMARY_BUTTON_CSS}</style>
        <Focusable
          style={{
            display: "flex",
            alignItems: "center",
            gap: "14px",
            margin: "6px 0 12px",
          }}
        >
          <h2 style={{ margin: 0 }}>
            My Mods
            {scopeGame ? ` — ${scopeGame.displayName}` : ""}
          </h2>
          {scopeGame && (hiddenGames > 0 || showAllGames) && (
            <DialogButton
              onClick={() => setShowAllGames((v) => !v)}
              style={{
                minWidth: "0",
                width: "auto",
                padding: "6px 14px",
                fontSize: "12.5px",
                flexShrink: 0,
              }}
            >
              {showAllGames
                ? `Only ${scopeGame.displayName}`
                : `All games (${hiddenGames} more)`}
            </DialogButton>
          )}
        </Focusable>

        {groups === undefined && (
          <div style={{ opacity: 0.8 }}>Reading your games…</div>
        )}
        {groups !== undefined && groups.length === 0 && (
          <div style={{ opacity: 0.8 }}>
            Nothing installed yet - the Store tab is where it starts.
          </div>
        )}
        {groups !== undefined &&
          groups.length > 0 &&
          grouped.length === 0 &&
          scoped && (
            <div style={{ opacity: 0.8 }}>
              Nothing installed for {scopeGame!.displayName} yet - mods for
              other games are behind "All games" above.
            </div>
          )}

        {groups !== undefined && grouped.length > 0 && (
          // Focusable columns: plain divs broke gamepad traversal - the
          // stick couldn't move down from the tab strip into the rows.
          <Focusable
            style={{ display: "flex", gap: "20px", alignItems: "flex-start" }}
          >
            {/* ---- left: loose mods ---- */}
            <Focusable style={{ flex: 1, minWidth: 0 }}>
              {columnHeader(`Mods (${looseTotal})`)}
              {looseTotal === 0 && (
                <div style={{ opacity: 0.65, fontSize: "12.5px" }}>
                  No individually installed mods.
                </div>
              )}
              {grouped
                .filter((g) => g.loose.length > 0)
                .map(({ game, loose: mods }) => (
                  <div key={game.appId} style={{ marginBottom: "14px" }}>
                    <div
                      style={{
                        fontSize: "13px",
                        fontWeight: 700,
                        margin: "4px 0 6px",
                        opacity: 0.85,
                      }}
                    >
                      {game.displayName}{" "}
                      <span style={{ opacity: 0.55, fontWeight: 400 }}>
                        · {mods.length}
                      </span>
                    </div>
                    <Focusable
                      style={{
                        display: "flex",
                        flexDirection: "column",
                        gap: "5px",
                      }}
                    >
                      {mods.map((mod) => (
                        <ModRow
                          key={`${game.appId}:${mod.folder}`}
                          game={game}
                          mod={mod}
                          thumb={thumbs[`${game.appId}:${mod.mod_id}`]}
                          blur={
                            blurAdult &&
                            adultRef.current.has(mod.mod_id ?? -1)
                          }
                          busy={busyKey === `${game.appId}:${mod.folder}`}
                          onToggle={toggle}
                          onRemove={remove}
                        />
                      ))}
                    </Focusable>
                  </div>
                ))}
            </Focusable>

            {/* ---- right: collections ---- */}
            <Focusable style={{ flex: 1, minWidth: 0 }}>
              {columnHeader(`Collections (${collectionsTotal})`)}
              {collectionsTotal === 0 && (
                <div style={{ opacity: 0.65, fontSize: "12.5px" }}>
                  No collections installed yet - find them on the Store tab.
                </div>
              )}
              {grouped
                .filter((g) => g.entries.length > 0)
                .map(({ game, entries }) => (
                  <div key={game.appId} style={{ marginBottom: "14px" }}>
                    <div
                      style={{
                        fontSize: "13px",
                        fontWeight: 700,
                        margin: "4px 0 6px",
                        opacity: 0.85,
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
                      {entries.map(({ slug, info, members, pendingChoices }) => {
                        const key = `${game.appId}:${slug}`;
                        const title =
                          slug === LEGACY_SLUG
                            ? "Collection (installed before v0.17)"
                            : info?.title ?? slug;
                        const open = openCollections.has(key);
                        const toggleable = members.filter(
                          (m) => m.togglable !== false
                        );
                        const allOn =
                          toggleable.length > 0 &&
                          toggleable.every((m) => m.enabled);
                        const collBusy =
                          busyKey === `${game.appId}:coll:${slug}`;
                        return (
                          <div key={key}>
                            <Focusable
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
                                  flexGrow: 1,
                                  minWidth: 0,
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
                                  <div
                                    style={{
                                      fontSize: "12px",
                                      opacity: 0.65,
                                    }}
                                  >
                                    {members.length} mod
                                    {members.length === 1 ? "" : "s"} installed
                                    {info?.mod_count
                                      ? ` · ${info.mod_count} in the collection`
                                      : ""}
                                    {collBusy ? " · switching…" : ""}
                                  </div>
                                </div>
                                <div
                                  style={{ fontSize: "16px", opacity: 0.7 }}
                                >
                                  {open ? "▾" : "▸"}
                                </div>
                              </Focusable>
                              {slug !== LEGACY_SLUG && (
                                <DialogButton
                                  disabled={collBusy}
                                  onClick={() =>
                                    openCollectionPage(game, slug, info)
                                  }
                                  style={{
                                    minWidth: "0",
                                    width: "40px",
                                    padding: "8px 0",
                                    flexShrink: 0,
                                    display: "flex",
                                    alignItems: "center",
                                    justifyContent: "center",
                                  }}
                                >
                                  <FaEye size={13} />
                                </DialogButton>
                              )}
                              {(pendingChoices ?? 0) > 0 &&
                                slug !== LEGACY_SLUG && (
                                  <DialogButton
                                    className={BLUE_BUTTON_CLASS}
                                    onClick={() =>
                                      openCollectionPage(game, slug, info)
                                    }
                                    style={{
                                      minWidth: "0",
                                      width: "auto",
                                      padding: "6px 12px",
                                      fontSize: "12px",
                                      flexShrink: 0,
                                    }}
                                  >
                                    ⚙ Finish installing ({pendingChoices})
                                  </DialogButton>
                                )}
                              {toggleable.length > 0 && (
                                <OrangeToggle
                                  checked={allOn}
                                  disabled={collBusy}
                                  onChange={(next) =>
                                    toggleCollection(game, slug, members, next)
                                  }
                                />
                              )}
                              <DialogButton
                                className={WHITE_BUTTON_CLASS}
                                disabled={collBusy}
                                onClick={() =>
                                  removeCollection(
                                    game,
                                    slug,
                                    title,
                                    members.length
                                  )
                                }
                                style={{
                                  minWidth: "0",
                                  width: "auto",
                                  padding: "6px 12px",
                                  fontSize: "12px",
                                  flexShrink: 0,
                                }}
                              >
                                Uninstall
                              </DialogButton>
                            </Focusable>
                            {open && (
                              <Focusable
                                style={{
                                  display: "flex",
                                  flexDirection: "column",
                                  gap: "5px",
                                  margin: "5px 0 5px 16px",
                                }}
                              >
                                {members.map((mod) => (
                                  <ModRow
                                    key={`${game.appId}:${mod.folder}`}
                                    game={game}
                                    mod={mod}
                                    thumb={
                                      thumbs[`${game.appId}:${mod.mod_id}`]
                                    }
                                    blur={
                                      blurAdult &&
                                      adultRef.current.has(mod.mod_id ?? -1)
                                    }
                                    busy={
                                      busyKey ===
                                        `${game.appId}:${mod.folder}` ||
                                      collBusy
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
            </Focusable>
          </Focusable>
        )}
      </Scroller>
    </Focusable>
  );
}
