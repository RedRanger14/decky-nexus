import {
  ButtonItem,
  Dropdown,
  Focusable,
  Router,
  ScrollPanelGroup,
  TextField,
} from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { FaArrowDown, FaCheck, FaThumbsUp } from "react-icons/fa";

import {
  CollectionSummary,
  ModsResult,
  NexusMod,
  getCollections,
  getInstalledMods,
  getMods,
  getModsByIds,
  getShowAdult,
  getTrendingMods,
  CollectionVerdictState,
  getCollectionVerdicts,
} from "./api";
import { SupportedGame, getActiveGame, modeParams } from "./games";
import {
  getBrowseGame,
  markBrowseReturn,
  saveBrowseState,
  markCollectionsReturn,
  setDetailOrigin,
  setSelectedCollection,
  setSelectedMod,
  takeBrowseRestore,
  takeCollectionsReturn,
} from "./state";

// Steam's scroll panel: right-stick scrolling for free. The published types
// only declare children, but the underlying component takes Focusable-ish
// props.
const Scroller: any = ScrollPanelGroup;
import { PageBackdrop, SectionHeading, StackedThumb } from "./chrome";
import { NEXUS_ORANGE } from "./theme";
import { TabBar, exitTabsToQam, handleTabButtons, pushOurPage } from "./Tabs";

const SORT_OPTIONS = [
  { data: "featured", label: "Featured" },
  { data: "trending", label: "Trending" },
  { data: "endorsements", label: "Most endorsed" },
  { data: "downloads", label: "Most downloaded" },
  { data: "updatedAt", label: "Recently updated" },
  { data: "createdAt", label: "Newest" },
];

const PAGE_SIZE = 24;
// Rail length + tile width are a pair: 5 tiles and the View-all card
// must all fit across a 1280px screen (minus the 24px page gutters)
// WITHOUT scrolling - a rail that hides its own exit invites nobody in.
// 6 × 195px + 5 × 10px gaps = 1220px ≤ 1232px available.
const ROW_SIZE = 5;
const TILE_WIDTH = 195;

/** The badge, and the rule behind it.
 *
 * Michael asked for a "Verified on Deck" mark like the site's EASY INSTALL
 * one. The bar is deliberately high: Fallout 4's Vault Boy 101 installed
 * 451 of 454 mods, applied its load order, booted and started a new game
 * while rendering every surface magenta - so "it installed" is worth
 * showing and is NOT verification. Only real playtime after the install
 * earns the green mark, and that evidence comes from Steam, not from us.
 */
function VerifiedBadge({ state }: { state: CollectionVerdictState }) {
  const look =
    state === "played"
      ? { text: "VERIFIED ON DECK", bg: "rgba(70, 170, 90, 0.92)" }
      : state === "booted"
      ? { text: "BOOTED HERE", bg: "rgba(90, 130, 200, 0.9)" }
      : { text: "INSTALLED HERE", bg: "rgba(255, 255, 255, 0.16)" };
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "4px",
        alignSelf: "flex-start",
        marginTop: "3px",
        padding: "1px 7px",
        borderRadius: "3px",
        fontSize: "9.5px",
        fontWeight: 700,
        letterSpacing: "0.4px",
        background: look.bg,
        color: "#fff",
      }}
    >
      {state === "played" && <FaCheck size={8} />}
      {look.text}
    </div>
  );
}

/** Undo the scroll that autoFocus causes, so the page opens at its top. */
function ScrollHeaderIntoView() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // After the focus pass, not with it: zeroing scrollTop in the same
    // frame is simply overwritten by Steam scrolling its focus into view.
    const timer = setTimeout(() => {
      let el: HTMLElement | null = ref.current;
      while (el) {
        if (el.scrollTop) el.scrollTop = 0;
        el = el.parentElement;
      }
    }, 80);
    return () => clearTimeout(timer);
  }, []);
  return <div ref={ref} style={{ height: 0 }} />;
}

function CollectionCard({
  game,
  c,
  fromList,
  verdict,
}: {
  game: SupportedGame;
  c: CollectionSummary;
  /** Opened from the all-collections list: B on the collection page
   * must return THERE, not to the store home. */
  fromList?: boolean;
  /** What this device has done with it, if anything. */
  verdict?: CollectionVerdictState;
}) {
  return (
    <Focusable
      onActivate={() => {
        if (fromList) markCollectionsReturn();
        setSelectedCollection({ game, collection: c });
        setDetailOrigin("browse");
        pushOurPage("/nexus-mods/collection");
      }}
      style={{
        display: "flex",
        gap: "10px",
        background: "rgba(255,255,255,0.06)",
        borderRadius: "6px",
        overflow: "hidden",
        padding: "8px",
      }}
    >
      {/* Stacked-card thumb: the at-a-glance cue that a collection is a
          DECK of mods, not one mod. "contain" like the detail header -
          cropping collection art to fill a tile cut the sides off it. */}
      <StackedThumb
        src={c.thumbnailUrl}
        width={72}
        height={64}
        peek={5}
        fit="contain"
      />
      <div style={{ minWidth: 0, alignSelf: "center" }}>
        <div
          style={{
            fontWeight: 600,
            fontSize: "13.5px",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {c.name}
        </div>
        <div style={{ fontSize: "11.5px", opacity: 0.65 }}>by {c.author}</div>
        <div style={{ fontSize: "11.5px", opacity: 0.65 }}>
          {c.modCount} mods ·{" "}
          {c.totalSize >= 1 << 30
            ? `${(c.totalSize / (1 << 30)).toFixed(1)} GB`
            : `${Math.round(c.totalSize / (1 << 20))} MB`}
        </div>
        {c.needs_older_game && (
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
              alignSelf: "flex-start",
              marginTop: "3px",
              padding: "1px 7px",
              borderRadius: "3px",
              fontSize: "9.5px",
              fontWeight: 700,
              letterSpacing: "0.4px",
              background: "rgba(200, 90, 70, 0.92)",
              color: "#fff",
            }}
          >
            NEEDS AN OLDER GAME
          </div>
        )}
        {verdict && <VerifiedBadge state={verdict} />}
      </div>
    </Focusable>
  );
}

function openMod(game: SupportedGame, mod: NexusMod) {
  setSelectedMod({ game, mod });
  setDetailOrigin("browse");
  markBrowseReturn();
  pushOurPage("/nexus-mods/mod");
}

/** Endorsements + downloads with real icons - emoji looked cheap next
 * to Steam's own UI and rendered inconsistently. */
function StatsLine({ mod, author }: { mod: NexusMod; author?: boolean }) {
  const icon = { opacity: 0.75, marginRight: "3px", flexShrink: 0 } as const;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "8px",
        color: "rgba(255,255,255,0.82)",
      }}
    >
      {author && <span>{mod.author}</span>}
      <span style={{ display: "inline-flex", alignItems: "center" }}>
        <FaThumbsUp size={10} style={icon} />
        {mod.endorsements.toLocaleString()}
      </span>
      <span style={{ display: "inline-flex", alignItems: "center" }}>
        <FaArrowDown size={10} style={icon} />
        {mod.downloads.toLocaleString()}
      </span>
    </span>
  );
}

/** "18+" chip for adult mods whose account preference blurs images. */
function AdultBadge() {
  return (
    <div
      style={{
        position: "absolute",
        top: "6px",
        right: "6px",
        padding: "2px 7px",
        borderRadius: "4px",
        background: "rgba(0,0,0,0.72)",
        fontSize: "11px",
        fontWeight: 700,
        letterSpacing: "0.5px",
      }}
    >
      18+
    </div>
  );
}

/** Big-and-bold hero card: full-bleed image, title on a gradient. */
function HeroCard({
  mod,
  game,
  blur,
}: {
  mod: NexusMod;
  game: SupportedGame;
  blur?: boolean;
}) {
  const blurred = !!blur && mod.adultContent;
  // Prefer the server-blurred variant (proper obscuring, no client cost);
  // CSS blur is the fallback for v1-sourced mods without one.
  const heroSrc = blurred
    ? mod.thumbnailBlurredUrl ?? mod.thumbnailUrl ?? mod.pictureUrl
    : mod.thumbnailUrl ?? mod.pictureUrl;
  const cssBlur = blurred && !mod.thumbnailBlurredUrl;
  return (
    <Focusable
      onActivate={() => openMod(game, mod)}
      style={{
        position: "relative",
        borderRadius: "8px",
        overflow: "hidden",
        aspectRatio: "16 / 8",
        background: "#1a1d24",
      }}
    >
      {heroSrc && (
        <img
          src={heroSrc}
          alt={mod.name}
          loading="lazy"
          decoding="async"
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
            ...(cssBlur ? { filter: "blur(22px)", transform: "scale(1.1)" } : {}),
          }}
        />
      )}
      {blurred && <AdultBadge />}
      <div
        style={{
          position: "absolute",
          insetInline: 0,
          bottom: 0,
          padding: "26px 14px 10px",
          background:
            "linear-gradient(180deg, rgba(0,0,0,0) 0%, rgba(0,0,0,0.88) 85%)",
        }}
      >
        <div
          style={{
            fontWeight: 700,
            fontSize: "18px",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {mod.name}
        </div>
        <div style={{ fontSize: "12px", opacity: 0.9 }}>
          <StatsLine mod={mod} author />
        </div>
      </div>
    </Focusable>
  );
}

function ModTile({
  mod,
  game,
  blur,
}: {
  mod: NexusMod;
  game: SupportedGame;
  blur?: boolean;
}) {
  const blurred = !!blur && mod.adultContent;
  return (
    <Focusable
      onActivate={() => openMod(game, mod)}
      style={{
        background: "rgba(255, 255, 255, 0.06)",
        borderRadius: "6px",
        overflow: "hidden",
      }}
    >
      {mod.thumbnailUrl ? (
        <div style={{ position: "relative", overflow: "hidden" }}>
          <img
            src={
              blurred ? mod.thumbnailBlurredUrl ?? mod.thumbnailUrl : mod.thumbnailUrl
            }
            alt={mod.name}
            loading="lazy"
            decoding="async"
            style={{
              width: "100%",
              aspectRatio: "16 / 9",
              objectFit: "cover",
              display: "block",
              ...(blurred && !mod.thumbnailBlurredUrl
                ? { filter: "blur(16px)", transform: "scale(1.1)" }
                : {}),
            }}
          />
          {blurred && <AdultBadge />}
        </div>
      ) : (
        <div style={{ width: "100%", aspectRatio: "16 / 9", background: "#23262e" }} />
      )}
      <div style={{ padding: "8px 10px" }}>
        <div
          style={{
            fontWeight: 600,
            fontSize: "14px",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {mod.name}
        </div>
        <div style={{ fontSize: "12px", opacity: 0.7 }}>
          {mod.author} · v{mod.version}
        </div>
        <div style={{ fontSize: "12px", opacity: 0.7 }}>
          <StatsLine mod={mod} />
        </div>
      </div>
    </Focusable>
  );
}

/** End-of-rail card that jumps into the sorted list view - an organic way
 * into the same place the sort dropdown goes. */
function ViewAllCard({
  onActivate,
  label = "View all →",
}: {
  onActivate: () => void;
  label?: string;
}) {
  // No fixed height: the flex row's default stretch matches it to the
  // tiles beside it, whatever their text rows add up to.
  return (
    <Focusable
      onActivate={onActivate}
      style={{
        width: `${TILE_WIDTH}px`,
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: "6px",
        background: "rgba(218, 142, 53, 0.12)",
        border: `1px solid ${NEXUS_ORANGE}55`,
        fontWeight: 600,
        fontSize: "15px",
      }}
    >
      {label}
    </Focusable>
  );
}

/** Horizontally scrolling, controller-focusable carousel row. */
function ModCarousel({
  title,
  mods,
  game,
  onViewAll,
  blur,
}: {
  title: string;
  mods: NexusMod[];
  game: SupportedGame;
  onViewAll?: () => void;
  blur?: boolean;
}) {
  if (mods.length === 0) return null;
  return (
    <>
      <SectionHeading title={title} />
      <Focusable
        style={{
          display: "flex",
          gap: "10px",
          overflowX: "auto",
          paddingBottom: "6px",
        }}
      >
        {mods.slice(0, ROW_SIZE).map((mod) => (
          <div key={mod.modId} style={{ width: `${TILE_WIDTH}px`, flexShrink: 0 }}>
            <ModTile mod={mod} game={game} blur={blur} />
          </div>
        ))}
        {onViewAll && <ViewAllCard onActivate={onViewAll} />}
      </Focusable>
    </>
  );
}

export function BrowsePage() {
  // Explicit scope from the QAM beats ambient resolution (which could
  // go stale and surface another game's store).
  const explicitScope = getBrowseGame() !== undefined;
  const game =
    getBrowseGame() ??
    getActiveGame(
      Router.MainRunningApp ? Number(Router.MainRunningApp.appid) : undefined
    );

  // Coming back from a mod detail restores the previous search/results
  // instead of resetting to the home rails. (Lazy init: runs once.)
  const [restored] = useState(() => takeBrowseRestore(game.appId));

  const [sort, setSort] = useState(restored?.sort ?? "featured");
  const [search, setSearch] = useState(restored?.search ?? "");

  // list mode
  const [mods, setMods] = useState<NexusMod[]>(restored?.mods ?? []);
  const [total, setTotal] = useState<number | undefined>(restored?.total);
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  // What this device has actually done with each collection. Read on
  // every visit rather than cached: the answer changes when the user
  // plays, which has nothing to do with this page.
  const [verdicts, setVerdicts] = useState<
    Record<string, CollectionVerdictState>
  >({});
  const nextOffset = useRef(restored?.nextOffset ?? 0);
  // Skip the mount fetch ONLY when the restored state is list-mode; a
  // home-mode restore never fetches, so the pending skip used to eat the
  // NEXT list fetch instead (view-all landed on an empty page).
  const skipNextFetch = useRef(
    Boolean(
      restored &&
        (restored.search.trim() !== "" || restored.sort !== "featured")
    )
  );

  // home mode
  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [recommended, setRecommended] = useState<NexusMod[]>([]);
  const [trending, setTrending] = useState<NexusMod[]>([]);
  const [newest, setNewest] = useState<NexusMod[]>([]);
  const [popular, setPopular] = useState<NexusMod[]>([]);
  // Account blur preference: adult thumbnails get blurred + 18+ badge.
  const [blurAdult, setBlurAdult] = useState(false);
  // Already-installed mods never take a hero slot - the user has them.
  const [installedIds, setInstalledIds] = useState<Set<number>>(new Set());
  useEffect(() => {
    getShowAdult().then((r) => {
      if (r.ok) setBlurAdult(!!r.show_adult && !!r.blur_adult);
    });
  }, []);
  useEffect(() => {
    setInstalledIds(new Set());
    getInstalledMods(
      game.nexusDomain,
      game.installDirName,
      game.modsSubdir,
      ...modeParams(game),
      game.protectedModFolders ?? []
    ).then((r) => {
      setInstalledIds(
        new Set(
          (r.mods ?? [])
            .map((m) => m.mod_id)
            .filter((id): id is number => typeof id === "number")
        )
      );
    });
  }, [game.appId]);

  // The API rejects 1-char wildcard searches - treat them as no search.
  const effectiveSearch = search.trim().length >= 2 ? search.trim() : "";
  const isHome = sort === "featured" && effectiveSearch === "";
  const effectiveSort = sort === "featured" ? "endorsements" : sort;

  // Focus restore: switching home<->list unmounts the focused element and
  // Steam's navigator strands focus on the system header ("down" goes dead
  // until "up"). When the mode flips, focus the first tile of the new
  // content once it exists.
  const contentRef = useRef<HTMLDivElement>(null);
  const pendingFocus = useRef(true);
  // Gaming Mode's gamepad focus is not DOM focus - checking activeElement
  // can't detect "the user is typing". Track keystroke recency instead and
  // refuse to move focus (which dismisses the on-screen keyboard) near one.
  const lastSearchEdit = useRef(0);
  const typedRecently = () => Date.now() - lastSearchEdit.current < 1500;
  useEffect(() => {
    pendingFocus.current = true;
  }, [isHome]);
  useEffect(() => {
    if (!pendingFocus.current) return;
    // Never yank focus away from the search box mid-typing - that blurs
    // the field and dismisses the on-screen keyboard.
    if (typedRecently()) {
      pendingFocus.current = false;
      return;
    }
    const ready = isHome
      ? recommended.length + trending.length > 0
      : mods.length > 0;
    if (!ready) return;
    pendingFocus.current = false;
    const timer = setTimeout(() => {
      (
        contentRef.current?.querySelector("[tabindex]") as HTMLElement | null
      )?.focus();
    }, 120);
    return () => clearTimeout(timer);
  });

  const [lastPageFull, setLastPageFull] = useState(true);
  const [searchScope, setSearchScope] = useState<"mods" | "collections">(
    "mods"
  );
  const [searchCollections, setSearchCollections] = useState<
    CollectionSummary[]
  >([]);
  // "All collections" browse mode: its own sort, mirrors the mods list.
  // Returning from a collection opened out of this list re-enters it
  // (the page unmounts while the collection is open).
  const [collectionsMode, setCollectionsMode] = useState(() =>
    takeCollectionsReturn()
  );
  const [collectionsSort, setCollectionsSort] = useState("endorsements");
  const [allCollections, setAllCollections] = useState<CollectionSummary[]>(
    []
  );
  const [collectionsHasMore, setCollectionsHasMore] = useState(true);
  const fetchCollectionsPage = (offset: number, append: boolean) => {
    getCollections(game.nexusDomain, 30, "", collectionsSort, offset).then(
      (r) => {
        if (!r.ok) return;
        const page = r.collections ?? [];
        setCollectionsHasMore(page.length >= 30);
        setAllCollections((prev) => (append ? [...prev, ...page] : page));
      }
    );
  };
  useEffect(() => {
    if (!collectionsMode) return;
    setAllCollections([]);
    setCollectionsHasMore(true);
    fetchCollectionsPage(0, false);
  }, [collectionsMode, collectionsSort, game.appId]);

  const fetchPage = async (offset: number, append: boolean) => {
    setLoading(true);
    try {
      const result = await getMods(
        game.nexusDomain,
        effectiveSort,
        PAGE_SIZE,
        offset,
        effectiveSearch,
        game.appId
      );
      if (result.ok) {
        setError(undefined);
        setTotal(result.total);
        // Advance by what the SOURCE consumed, not by page size: the
        // backend backfills over filtered entries, so those two differ
        // whenever anything was hidden and the difference is rows shown
        // twice.
        nextOffset.current = result.next_offset ?? offset + PAGE_SIZE;
        // A short page means the well is dry regardless of what the
        // total claims (search totals can be stale or absent).
        // A short page now means the source ran dry, not that the
        // filter took a few - the backend keeps reading until the row is
        // full or there is nothing left.
        setLastPageFull((result.mods ?? []).length >= PAGE_SIZE);
        setMods((prev) => (append ? [...prev, ...(result.mods ?? [])] : result.mods ?? []));
      } else {
        setError(result.error);
        if (!append) setMods([]);
      }
    } finally {
      setLoading(false);
    }
  };

  // Home rails: trending (v1 signal), newest, all-time popular.
  useEffect(() => {
    let cancelled = false;
    const apply =
      (setter: (m: NexusMod[]) => void) => (result: ModsResult) => {
        if (!cancelled && result.ok) setter(result.mods ?? []);
        if (!cancelled && result.ok && result.total !== undefined)
          setTotal((t) => t ?? result.total);
      };
    setCollections([]);
    setRecommended([]);
    setTrending([]);
    setNewest([]);
    setPopular([]);
    setTotal(undefined);
    getCollections(game.nexusDomain, 5, "", "endorsements", 0).then((r) => {
      if (!cancelled && r.ok) setCollections(r.collections ?? []);
    });
    // Badges for every list on this page, fetched once per game.
    getCollectionVerdicts(game.nexusDomain, game.appId)
      .then((r) => {
        if (cancelled || !r.ok) return;
        const map: Record<string, CollectionVerdictState> = {};
        for (const [slug, v] of Object.entries(r.verdicts ?? {})) {
          map[slug] = v.state;
        }
        setVerdicts(map);
      })
      .catch(() => undefined);
    if (game.recommendedModIds?.length) {
      getModsByIds(game.nexusDomain, game.recommendedModIds).then(
        apply(setRecommended)
      );
    }
    getTrendingMods(game.nexusDomain, 10, game.appId).then(apply(setTrending));
    getMods(game.nexusDomain, "createdAt", ROW_SIZE, 0, "", game.appId).then(
      apply(setNewest)
    );
    getMods(game.nexusDomain, "endorsements", ROW_SIZE, 0, "", game.appId).then((r) => {
      if (!cancelled && r.ok) {
        setPopular(r.mods ?? []);
        setTotal(r.total);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [game.appId]);

  // Collection search runs alongside the mod search (cheap: one query).
  useEffect(() => {
    if (isHome || !effectiveSearch) {
      setSearchCollections([]);
      return;
    }
    const timer = setTimeout(() => {
      getCollections(game.nexusDomain, 20, effectiveSearch, "endorsements", 0).then((r) => {
        if (r.ok) setSearchCollections(r.collections ?? []);
      });
    }, 500);
    return () => clearTimeout(timer);
  }, [game.appId, search]);

  useEffect(() => {
    if (isHome) return;
    if (skipNextFetch.current) {
      // Restored results are already on screen - don't reload page one.
      skipNextFetch.current = false;
      return;
    }
    // Debounce while typing; instant for sort changes.
    const timer = setTimeout(() => fetchPage(0, false), search ? 500 : 0);
    return () => clearTimeout(timer);
  }, [game.appId, sort, search]);

  // Keep the hand-back cache current so opening a mod detail can restore.
  useEffect(() => {
    saveBrowseState({
      appId: game.appId,
      sort,
      search,
      mods,
      total,
      nextOffset: nextOffset.current,
    });
  }, [game.appId, sort, search, mods, total]);

  const hasMore =
    lastPageFull && total !== undefined && nextOffset.current < total;
  // Curated recommendations take the hero slots (the "start here" mods -
  // libraries and loaders); games without curation fall back to trending.
  // Always TWO heroes: a single curated pick stretched across the whole
  // hero band looks broken - blend trending in to fill the pair. Mods the
  // user already has installed never take a slot (they'd be dead weight
  // right after Step 1); if literally everything is installed, fall back
  // to the unfiltered blend rather than showing an empty band.
  const heroBlend = [
    ...recommended,
    ...trending.filter((t) => !recommended.some((r) => r.modId === t.modId)),
  ];
  const heroMods = [
    ...heroBlend.filter((m) => !installedIds.has(m.modId)),
    ...heroBlend.filter((m) => installedIds.has(m.modId)),
  ].slice(0, 2);
  const heroIsCurated = heroMods.some((h) =>
    recommended.some((r) => r.modId === h.modId)
  );
  const heroTitle = heroIsCurated ? "Recommended" : "Trending now";
  const railTrending = trending.filter(
    (t) => !heroMods.some((h) => h.modId === t.modId)
  );
  const railTitle = heroIsCurated ? "Trending now" : "Also trending";

  return (
    // onCancel: B returns to the plugin's QAM panel instead of dumping the
    // user on the home screen with everything closed.
    <Focusable
      onButtonDown={handleTabButtons("store")}
      onCancel={() => {
        if (collectionsMode) {
          setCollectionsMode(false);
          return;
        }
        if (!isHome) {
          // Entered a results view (view-all / search): B steps back to
          // the home rails, not out of the page.
          setSort("featured");
          setSearch("");
          return;
        }
        exitTabsToQam();
      }}
      style={{
        marginTop: "40px",
        height: "calc(100% - 40px)",
        position: "relative",
      }}
    >
      {/* The hero grid takes autoFocus so the D-pad has somewhere to land -
          without it you had to press RB twice to leave the Store. But
          Steam scrolls whatever it focuses into view, and the hero sits
          below the tab bar and search, so opening the page shoved both off
          the top. Michael: "it is auto scrolling down a bit and hiding the
          nav and search".
          Focus stays where it was; only the scroll is put back, once, after
          the focus has settled. */}
      <ScrollHeaderIntoView />
      <Scroller
        focusable={false}
        onButtonDown={handleTabButtons("store")}
        style={{
          height: "100%",
          overflowY: "auto",
          // Clears the SteamOS footer bar AND makes focus-driven scrolling
          // stop short of it (scroll-padding), so the last row is usable.
          padding: "0 24px 110px",
          scrollPaddingBottom: "110px",
          position: "relative",
        }}
      >
      {/* Game hero art as a faded backdrop banner behind the header. */}
      <PageBackdrop
        src={`https://cdn.cloudflare.steamstatic.com/steam/apps/${game.appId}/library_hero.jpg`}
        height={220}
        blur={false}
      />

      <div style={{ position: "relative", zIndex: 1 }}>
        {/* STICKY, because restoring the scroll was not enough. The hero
            grid must keep autoFocus (without it the D-pad has nowhere to
            land and you press RB twice to leave the Store), Steam scrolls
            whatever it focuses into view, and it does so after anything we
            set - so the header kept going off the top however it was put
            back. Michael: "the top menu is still being cut off by default.
            Maybe we should make it sticky anyway?"
            Sticky stops fighting the focus scroll and simply keeps the nav
            on screen wherever the page happens to be. It needs its own
            opaque background, or the rails scroll through underneath it. */}
        <div
          style={{
            position: "sticky",
            top: 0,
            zIndex: 3,
            // The page's own dark ground, so content passing behind is
            // hidden rather than ghosting through the tabs.
            background: "#0e141b",
            margin: "0 -24px",
            padding: "0 24px",
            boxShadow: "0 6px 12px -8px rgba(0,0,0,0.9)",
          }}
        >
          <TabBar currentId="store" />
        </div>
        {/* ---- Header: [game art] [title/count] ..... [search] [sort] ---- */}
        <Focusable
          style={{
            display: "flex",
            alignItems: "center",
            gap: "14px",
            padding: "12px 0",
          }}
        >
          <img
            src={`https://cdn.cloudflare.steamstatic.com/steam/apps/${game.appId}/header.jpg`}
            alt=""
            onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
            style={{ height: "52px", borderRadius: "6px", flexShrink: 0 }}
          />
          <div style={{ flexShrink: 0, minWidth: 0 }}>
            <h2 style={{ margin: 0, whiteSpace: "nowrap", lineHeight: 1.15 }}>
              {game.displayName}
            </h2>
            <div style={{ fontSize: "13px", fontWeight: 400, opacity: 0.6 }}>
              {total !== undefined ? `${total.toLocaleString()} mods` : "loading…"}
            </div>
          </div>
          <div style={{ flexGrow: 1 }} />
          {!collectionsMode && (
          <>
          <div style={{ width: "300px", flexShrink: 0 }}>
            <TextField
              label="Search"
              value={search}
              bShowClearAction={true}
              onChange={(e) => {
                lastSearchEdit.current = Date.now();
                setSearch(e?.target?.value ?? "");
              }}
              onKeyDown={(e) => {
                // Search is live per keystroke; Enter just puts the
                // on-screen keyboard away.
                if (e.key === "Enter") (e.target as HTMLElement).blur();
              }}
            />
          </div>
          <div style={{ width: "200px", flexShrink: 0 }}>
            <Dropdown
              rgOptions={SORT_OPTIONS}
              selectedOption={sort}
              onChange={(opt) => setSort(opt.data)}
              strDefaultLabel="Sort"
            />
          </div>
          </>
          )}
        </Focusable>

        {collectionsMode ? (
          <div>
            <Focusable
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                margin: "4px 0 10px",
              }}
            >
              <h3 style={{ margin: 0 }}>
                Collections{" "}
                <span style={{ opacity: 0.6, fontWeight: 400, fontSize: "13px" }}>
                  · {allCollections.length} shown
                </span>
              </h3>
              <div style={{ width: "220px" }}>
                <Dropdown
                  rgOptions={[
                    { data: "endorsements", label: "Most endorsed" },
                    { data: "downloads", label: "Most downloaded" },
                    { data: "updatedAt", label: "Recently updated" },
                    { data: "createdAt", label: "Newest" },
                  ]}
                  selectedOption={collectionsSort}
                  onChange={(opt) => setCollectionsSort(opt.data)}
                  strDefaultLabel="Sort"
                />
              </div>
            </Focusable>
            <Focusable
              autoFocus={true}
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3, 1fr)",
                gap: "12px",
              }}
            >
              {allCollections.map((c) => (
                <CollectionCard
                  key={c.slug}
                  game={game}
                  c={c}
                  fromList
                  verdict={verdicts[c.slug]}
                />
              ))}
            </Focusable>
            {allCollections.length === 0 && (
              <div style={{ opacity: 0.8, padding: "12px 0" }}>
                Loading collections…
              </div>
            )}
            {collectionsHasMore && allCollections.length > 0 && (
              <Focusable style={{ margin: "14px auto 0", maxWidth: "320px" }}>
                <ButtonItem
                  layout="below"
                  onClick={() =>
                    fetchCollectionsPage(allCollections.length, true)
                  }
                >
                  Load more ({allCollections.length} shown)
                </ButtonItem>
              </Focusable>
            )}
          </div>
        ) : isHome ? (
          <div ref={contentRef}>
            {/* Focus anchor. The element that claims focus on this page is
                the hero rail, and it only renders once the API answers -
                so on landing there is a window with NO established gamepad
                focus, and the first bumper press is spent establishing it
                instead of switching tab ("press RB twice from the Store").
                A zero-height focusable that exists from the first frame
                closes that window; it removes itself once the hero is
                there and can take focus properly. */}
            {heroMods.length === 0 && (
              <Focusable autoFocus style={{ height: 0, overflow: "hidden" }}>
                <span />
              </Focusable>
            )}
            {/* ---- Hero: curated recommendations, big and bold ---- */}
            {heroMods.length > 0 && (
              <>
                <SectionHeading title={heroTitle} />
                <Focusable
                  autoFocus={!typedRecently()}
                  style={{
                    display: "grid",
                    gridTemplateColumns:
                      heroMods.length > 1 ? "1fr 1fr" : "1fr",
                    gap: "14px",
                  }}
                >
                  {heroMods.map((mod) => (
                    <HeroCard key={mod.modId} mod={mod} game={game} blur={blurAdult} />
                  ))}
                </Focusable>
              </>
            )}
            {!explicitScope && (
          <div
            style={{
              padding: "8px 11px",
              margin: "4px 0 12px",
              background: "rgba(218,142,53,0.10)",
              borderLeft: `3px solid ${NEXUS_ORANGE}`,
              borderRadius: "4px",
              fontSize: "12.5px",
              lineHeight: 1.45,
            }}
          >
            Showing mods for <b>{game.displayName}</b> - the game this store
            defaults to. To browse another game's mods, open that game in your
            Steam library, then use the Nexus Mods panel in the Quick Access
            Menu (the ... button).
          </div>
        )}
        {collections.length > 0 && (
              <>
                <SectionHeading title="Collections" />
                <Focusable
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(3, 1fr)",
                    gap: "12px",
                    marginBottom: "6px",
                  }}
                >
                  {collections.map((c) => (
                    <CollectionCard
                      key={c.slug}
                      game={game}
                      c={c}
                      verdict={verdicts[c.slug]}
                    />
                  ))}
                  <Focusable
                    onActivate={() => setCollectionsMode(true)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      borderRadius: "6px",
                      background: "rgba(218, 142, 53, 0.12)",
                      border: `1px solid ${NEXUS_ORANGE}55`,
                      fontWeight: 600,
                      fontSize: "13.5px",
                      minHeight: "80px",
                    }}
                  >
                    All collections →
                  </Focusable>
                </Focusable>
              </>
            )}
            {trending.length === 0 && recommended.length === 0 && (
              <div style={{ padding: "20px 0", opacity: 0.8 }}>
                Loading mods…
              </div>
            )}
            <ModCarousel
              title={railTitle}
              mods={railTrending}
              game={game}
              blur={blurAdult}
              onViewAll={() => {
                setSearch("");
                setSort("trending");
              }}
            />
            <ModCarousel
              title="New mods"
              mods={newest}
              game={game}
              blur={blurAdult}
              onViewAll={() => {
                setSearch("");
                setSort("createdAt");
              }}
            />
            <ModCarousel
              title="All-time favourites"
              mods={popular}
              game={game}
              blur={blurAdult}
              onViewAll={() => {
                setSearch("");
                setSort("endorsements");
              }}
            />
          </div>
        ) : (
          <div ref={contentRef}>
            {error && (
              <div style={{ padding: "24px 0", opacity: 0.8 }}>
                Could not load mods: {error}
              </div>
            )}
            {loading && mods.length === 0 && (
              <div style={{ padding: "24px 0", opacity: 0.8 }}>Loading mods…</div>
            )}
            {!loading && !error && mods.length === 0 && total !== undefined && (
              <div style={{ padding: "24px 0", opacity: 0.8 }}>
                No mods match “{search.trim()}”.
              </div>
            )}
            {search.trim() !== "" && (
              <Focusable
                style={{ display: "flex", gap: "6px", marginTop: "8px" }}
              >
                {(["mods", "collections"] as const).map((scope) => (
                  <Focusable
                    key={scope}
                    onActivate={() => setSearchScope(scope)}
                    style={{
                      padding: "4px 14px",
                      borderRadius: "999px",
                      fontSize: "12.5px",
                      fontWeight: 600,
                      background:
                        searchScope === scope
                          ? NEXUS_ORANGE
                          : "rgba(255,255,255,0.08)",
                      color: searchScope === scope ? "#1a1d24" : undefined,
                    }}
                  >
                    {scope === "mods"
                      ? `Mods${total !== undefined ? ` (${total})` : ""}`
                      : `Collections (${searchCollections.length})`}
                  </Focusable>
                ))}
              </Focusable>
            )}
            {search.trim() !== "" && searchScope === "collections" ? (
              <Focusable
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  gap: "12px",
                  marginTop: "8px",
                }}
              >
                {searchCollections.map((c) => (
                  <CollectionCard
                      key={c.slug}
                      game={game}
                      c={c}
                      verdict={verdicts[c.slug]}
                    />
                ))}
                {searchCollections.length === 0 && (
                  <div style={{ opacity: 0.7, fontSize: "13px" }}>
                    No collections match "{search.trim()}".
                  </div>
                )}
              </Focusable>
            ) : (
            <Focusable
              autoFocus={!typedRecently()}
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))",
                gap: "14px",
                marginTop: "8px",
              }}
            >
              {mods.map((mod) => (
                <ModTile key={mod.modId} mod={mod} game={game} blur={blurAdult} />
              ))}
            </Focusable>
            )}
            {hasMore && (
              <Focusable style={{ margin: "16px auto 0", maxWidth: "320px" }}>
                <ButtonItem
                  layout="below"
                  disabled={loading}
                  onClick={() => fetchPage(nextOffset.current, true)}
                >
                  {loading ? "Loading…" : `Load more (${mods.length} shown)`}
                </ButtonItem>
              </Focusable>
            )}
          </div>
        )}
      </div>
      </Scroller>
    </Focusable>
  );
}
