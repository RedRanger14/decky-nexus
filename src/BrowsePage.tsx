import {
  ButtonItem,
  Dropdown,
  Focusable,
  Router,
  ScrollPanelGroup,
  TextField,
} from "@decky/ui";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { FaArrowDown, FaCheck, FaThumbsUp } from "react-icons/fa";

import {
  CollectionSummary,
  ModsResult,
  NexusMod,
  findCollection,
  getCollections,
  getGameCategories,
  getInstalledMods,
  getMods,
  getModsByIds,
  getShowAdult,
  getTrendingMods,
  CollectionVerdictState,
  getCollectionVerdicts,
} from "./api";
import {
  ALL_GAMES,
  SupportedGame,
  frameworkModIds,
  getActiveGame,
  modeParams,
} from "./games";
import {
  ADULT_LINK_NOTE,
  LINK_HINT,
  hiddenCollectionsNote,
  parseCollectionLink,
  unsupportedLinkNote,
} from "./collectionLink";
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
import { AdultBadge, PageBackdrop, SectionHeading, StackedThumb } from "./chrome";
import { NEXUS_ORANGE, PAGE_SCROLLER } from "./theme";
import { storeHeaderPlan, STORE_HEADER_EDGE } from "./panelRules";
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
// How many mods a rail FETCHES - the ceiling, not the row length. The row
// measures its own width and shows exactly as many as fit beside the
// View-all card, so a rail never hides its own exit off-screen (it did on
// the Deck) and never leaves a hole on a TV (it did there too - the fixed
// five-plus-one was sized for a 1280px guess that matched neither screen).
const ROW_SIZE = 8;
// The narrowest a tile may go before the row drops a column.
const TILE_MIN = 180;
const TILE_GAP = 10;

/** The badge, and the rule behind it.
 *
 * Michael asked for a "Verified on Deck" mark like the site's EASY INSTALL
 * one. The bar is deliberately high: Fallout 4's Vault Boy 101 installed
 * 451 of 454 mods, applied its load order, booted and started a new game
 * while rendering every surface magenta - so "it installed" is worth
 * showing and is NOT verification. Only real playtime after the install
 * earns the green mark, and that evidence comes from Steam, not from us.
 */
/** Off, deliberately.
 *
 * EldenBoobs earned VERIFIED ON DECK having skipped all 16 of its mods: the
 * run recorded one install that never landed, so later playtime promoted an
 * empty collection to verified. Michael: "ive got cold feet about the badges
 * - lets remove them for now - just hide them." Right call - a badge that
 * overstates is worse than no badge, and the counting is not trustworthy yet.
 *
 * The evidence keeps being recorded either way, so this is one line to turn
 * back on once "installed" means what it says.
 */
const SHOW_VERIFIED_BADGES = false;

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

/** Measures the pinned nav+search, so focus scrolling can stop short of it.
 *
 * A sticky block paints OVER the content: the scroller has no idea part of
 * its viewport is covered, so scrolling the focused hero "into view" put it
 * under the pinned block. Michael: "now the top part of hero mods are being
 * cut off". Steam honours CSS scroll-padding here - that is already why
 * scrollPaddingBottom keeps the last row clear of the SteamOS footer bar -
 * so the fix is the same trick at the top, with the height measured rather
 * than guessed, since the header grows with the game art and title.
 */
function usePinnedTop() {
  const ref = useRef<HTMLDivElement>(null);
  // Tabs plus a 52px art row, until the real thing has been measured.
  const [height, setHeight] = useState(128);
  useLayoutEffect(() => {
    const measure = () => {
      const h = ref.current?.offsetHeight;
      if (h) setHeight(h);
    };
    measure();
    // Again once the header art has loaded and settled the row's height.
    const timer = setTimeout(measure, 300);
    return () => clearTimeout(timer);
  }, []);
  return { ref, height };
}

/** Focus item `index` of the grid inside `wrapper` (its first child), the
 * way the rest of this page moves focus: DOM focus on the element the
 * D-pad lands on, which Steam's navigator follows. */
function focusGridItem(wrapper: HTMLElement | null, index: number) {
  const item = wrapper?.firstElementChild?.children[index] as
    | HTMLElement
    | undefined;
  if (!item) return;
  const target = item.matches("[tabindex]")
    ? item
    : item.querySelector<HTMLElement>("[tabindex]");
  target?.focus();
  target?.scrollIntoView({ block: "nearest" });
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

/** What a link typed into the search box resolved to (#30). */
type LinkHit =
  | { kind: "found"; game: SupportedGame; collection: CollectionSummary }
  | { kind: "adult" }
  | { kind: "unsupported"; domain: string };

/** One quiet line under a heading: what the page is NOT showing, and why.
 * Renders nothing for empty text so callers need not guard it. */
function HiddenNote({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div style={{ fontSize: "12.5px", opacity: 0.7, margin: "0 0 8px" }}>
      {text}
    </div>
  );
}

function CollectionCard({
  game,
  c,
  fromList,
  verdict,
  blur,
}: {
  game: SupportedGame;
  c: CollectionSummary;
  /** Opened from the all-collections list: B on the collection page
   * must return THERE, not to the store home. */
  fromList?: boolean;
  /** What this device has done with it, if anything. */
  verdict?: CollectionVerdictState;
  /** The account's "blur adult images" preference. Mod tiles honoured it
   * from the start; collection tiles showed adult art plain to an account
   * that had asked for it blurred, because the listing never fetched the
   * flag. Michael has exactly that setting. */
  blur?: boolean;
}) {
  const blurred = !!blur && !!c.adultContent;
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
      <div style={{ position: "relative", flexShrink: 0 }}>
        <div style={blurred ? { filter: "blur(10px)" } : undefined}>
          <StackedThumb
            src={c.thumbnailUrl}
            width={72}
            height={64}
            peek={5}
            fit="contain"
          />
        </div>
        {blurred && <AdultBadge />}
      </div>
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
              background: "rgba(200, 130, 50, 0.92)",
              color: "#fff",
            }}
          >
            {c.built_for
              ? `BUILT FOR ${c.built_for.toUpperCase()}`
              : "NEEDS AN OLDER GAME"}
          </div>
        )}
        {SHOW_VERIFIED_BADGES && verdict && <VerifiedBadge state={verdict} />}
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
        {mod.preGameUpdate && (
          <div
            style={{
              display: "inline-flex",
              marginTop: "4px",
              padding: "1px 7px",
              borderRadius: "3px",
              fontSize: "9.5px",
              fontWeight: 700,
              letterSpacing: "0.4px",
              background: "rgba(200, 130, 50, 0.92)",
              color: "#fff",
            }}
          >
            PRE-UPDATE
          </div>
        )}
        {/* Curated: a mod this plugin can NEVER install (a desktop tool with
            a big endorsement count). Saying so on the tile beats letting the
            install fail and look like our bug - BetterSabers is the most
            endorsed mod for Battlefront II. */}
        {game.incompatibleMods?.[mod.modId] !== undefined && (
          <div
            style={{
              display: "inline-flex",
              marginTop: "4px",
              padding: "1px 7px",
              borderRadius: "3px",
              fontSize: "9.5px",
              fontWeight: 700,
              letterSpacing: "0.4px",
              background: "rgba(170, 60, 60, 0.92)",
              color: "#fff",
            }}
          >
            DESKTOP APP ONLY
          </div>
        )}
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
  // No fixed size: the grid gives it the same column as the tiles beside
  // it, and the row's stretch matches its height to theirs.
  return (
    <Focusable
      onActivate={onActivate}
      style={{
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
  // Measured, not guessed: Steam's logical resolution is no guide to the
  // panel this actually renders on (the fixed-width row was cut off on the
  // Deck AND underfilled a TV at the same time). The row watches its own
  // width and picks the column count from it.
  const rowRef = useRef<HTMLDivElement>(null);
  const [cols, setCols] = useState(lastRailCols);
  useEffect(() => {
    const el = rowRef.current;
    if (!el) return;
    const compute = () => {
      const width = el.clientWidth;
      if (width <= 0) return;
      const fit = Math.floor((width + TILE_GAP) / (TILE_MIN + TILE_GAP));
      const next = Math.max(2, Math.min(fit, ROW_SIZE + 1));
      lastRailCols = next;
      setCols(next);
    };
    compute();
    const watcher = new ResizeObserver(compute);
    watcher.observe(el);
    return () => watcher.disconnect();
  }, []);

  if (mods.length === 0) return null;
  // The View-all card is always the LAST column; mods get the rest. A rail
  // with fewer mods than columns just renders shorter.
  const shown = onViewAll ? cols - 1 : cols;
  return (
    <>
      <SectionHeading title={title} />
      {/* The ref lives on a plain div: Focusable is Steam's component and
          whether it forwards refs is not ours to rely on - a null ref here
          would freeze the measurement at the default, silently. */}
      <div ref={rowRef}>
      <Focusable
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
          gap: `${TILE_GAP}px`,
          paddingBottom: "6px",
        }}
      >
        {mods.slice(0, shown).map((mod) => (
          <ModTile key={mod.modId} mod={mod} game={game} blur={blur} />
        ))}
        {onViewAll && <ViewAllCard onActivate={onViewAll} />}
      </Focusable>
      </div>
    </>
  );
}

// The last measured column count, so the next rail (and the next visit)
// first paints at the right width instead of flashing a guess.
let lastRailCols = 5;

// Remembered across page opens, like lastRailCols: the first frame renders
// with the last known width instead of a guess, so tiers do not visibly
// snap on every navigation.
let lastHeaderWidth = 0;

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
  // The filter beside sort. "" = all; "d:N" = updated within N days
  // ("d:-1" = since the game's own last update); anything else is one of
  // the game's category names, fetched once per game.
  const [filter, setFilter] = useState("");
  const [categories, setCategories] = useState<string[]>([]);
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
    setFilter("");
    getGameCategories(game.nexusDomain).then((r) =>
      setCategories(r.ok ? r.categories ?? [] : [])
    );
  }, [game.appId]);
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
  // A filter means the user wants the LIST, even from the featured home.
  const isHome = sort === "featured" && effectiveSearch === "" && filter === "";
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

  // Issue #32: pressing Load more dropped the focus highlight, and from
  // then on the D-pad scrolled the page instead of stepping between
  // tiles. The mods button disabled itself while the page loaded, and
  // Steam's navigator lets go of an element that becomes disabled, so the
  // press itself left nothing focused. Now the button stays enabled (a
  // second press while loading is ignored), and focus moves to the first
  // tile of the new page once it arrives, which is where the user was
  // heading.
  const modGridRef = useRef<HTMLDivElement>(null);
  const collectionGridRef = useRef<HTMLDivElement>(null);
  const focusAfterLoad = useRef<{
    grid: "mods" | "collections";
    index: number;
  } | null>(null);
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
  // How many collections the account's adult gate kept off each list, so
  // the page can say so instead of leaving a gap (#30). One per fetch,
  // because each has its own total.
  const [railHidden, setRailHidden] = useState(0);
  const [allHidden, setAllHidden] = useState(0);
  const [searchHidden, setSearchHidden] = useState(0);
  // A link, or a bare slug, in the search box, looked up directly. The
  // ordinary search runs beside it; this is one more result.
  const [linkHit, setLinkHit] = useState<LinkHit | null>(null);
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
        const page = (r.ok && r.collections) || [];
        // Nothing new to move focus to, and a pending move would keep the
        // button ignoring presses.
        if (page.length === 0 && focusAfterLoad.current?.grid === "collections")
          focusAfterLoad.current = null;
        if (!r.ok) return;
        if (offset === 0) setAllHidden(r.adult_hidden ?? 0);
        setCollectionsHasMore(page.length >= 30);
        setAllCollections((prev) => (append ? [...prev, ...page] : page));
      }
    );
  };
  useEffect(() => {
    if (!collectionsMode) return;
    focusAfterLoad.current = null;
    setAllCollections([]);
    setCollectionsHasMore(true);
    fetchCollectionsPage(0, false);
  }, [collectionsMode, collectionsSort, game.appId]);

  const fetchPage = async (offset: number, append: boolean) => {
    if (!append) focusAfterLoad.current = null;
    setLoading(true);
    try {
      const result = await getMods(
        game.nexusDomain,
        effectiveSort,
        PAGE_SIZE,
        offset,
        effectiveSearch,
        game.appId,
        filter.startsWith("d:") ? "" : filter,
        filter.startsWith("d:") ? parseInt(filter.slice(2), 10) : 0
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
        // The backend says whether the source has more; page fullness is
        // only the fallback for a backend that predates has_more.
        setLastPageFull(
          result.has_more ?? (result.mods ?? []).length >= PAGE_SIZE
        );
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
      if (!cancelled && r.ok) {
        setCollections(r.collections ?? []);
        setRailHidden(r.adult_hidden ?? 0);
      }
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
      setSearchHidden(0);
      return;
    }
    const timer = setTimeout(() => {
      getCollections(game.nexusDomain, 20, effectiveSearch, "endorsements", 0).then((r) => {
        if (r.ok) {
          setSearchCollections(r.collections ?? []);
          setSearchHidden(r.adult_hidden ?? 0);
        }
      });
    }, 500);
    return () => clearTimeout(timer);
  }, [game.appId, search]);

  // A link, or the six characters after /collections/ in one, names ONE
  // collection. The search above matches names only, so a link found
  // nothing (#30). A link can name any game; the answer says which, and
  // the card opens it under that game.
  useEffect(() => {
    const link = parseCollectionLink(search);
    if (isHome || !link) {
      setLinkHit(null);
      return;
    }
    let stale = false;
    const timer = setTimeout(() => {
      findCollection(link.slug, link.domain ?? game.nexusDomain).then((r) => {
        if (stale) return;
        if (!r.ok || !r.found) {
          setLinkHit(null);
        } else if (r.adult_hidden) {
          setLinkHit({ kind: "adult" });
        } else {
          const target = ALL_GAMES.find((g) => g.nexusDomain === r.game_domain);
          if (!target || !r.collection) {
            setLinkHit({ kind: "unsupported", domain: r.game_domain ?? "" });
          } else {
            setLinkHit({ kind: "found", game: target, collection: r.collection });
          }
        }
      });
    }, 500);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
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
  }, [game.appId, sort, search, filter]);

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

  useEffect(() => {
    const want = focusAfterLoad.current;
    if (!want) return;
    const count = want.grid === "mods" ? mods.length : allCollections.length;
    if (count <= want.index) {
      // A page that brought nothing: focus never left the button, so
      // leave it there rather than jumping later on some unrelated change.
      if (want.grid === "mods" && !loading) focusAfterLoad.current = null;
      return;
    }
    focusAfterLoad.current = null;
    const wrapper =
      want.grid === "mods" ? modGridRef.current : collectionGridRef.current;
    // After Steam's own pass over the new nodes, as with the mode switch.
    const timer = setTimeout(() => focusGridItem(wrapper, want.index), 60);
    return () => clearTimeout(timer);
  }, [mods, allCollections, loading]);
  // Curated recommendations take the hero slots (the "start here" mods -
  // libraries and loaders); games without curation fall back to trending.
  // Always TWO heroes: a single curated pick stretched across the whole
  // hero band looks broken - blend trending in to fill the pair. Mods the
  // user already has installed never take a slot (they'd be dead weight
  // right after Step 1); if literally everything is installed, fall back
  // to the unfiltered blend rather than showing an empty band.
  // Frameworks never take a hero slot, installed or not. They have no
  // install records, so installedIds cannot see them - BLSE sat in the hero
  // band on a device where it was already installed and running. And even
  // uninstalled, a framework is Step 1's job; a hero tile saying "install
  // BLSE" duplicates the setup flow one screen away.
  const fwIds = new Set([
    ...frameworkModIds(game),
    // Desktop tools the game's config names: HD2's two most-endorsed
    // "mods" are Windows mod managers, which would headline the hero band
    // on a device that cannot run either.
    ...(game.heroExcludeModIds ?? []),
  ]);
  const heroBlend = [
    ...recommended,
    ...trending.filter((t) => !recommended.some((r) => r.modId === t.modId)),
  ].filter((m) => !fwIds.has(m.modId));
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
  const pinned = usePinnedTop();

  // The header sizes itself from its own measured width, exactly like the
  // rails: Steam's logical resolution is no guide to the panel this renders
  // on, and the fixed-width version was wider than the Deck's real width -
  // the filter hung off the screen edge, and focusing it panned the whole
  // page sideways (device photos, 2026-08-31).
  const headerRef = useRef<HTMLDivElement>(null);
  const [headerWidth, setHeaderWidth] = useState(lastHeaderWidth);
  useEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    const compute = () => {
      const width = el.clientWidth;
      if (width <= 0) return;
      lastHeaderWidth = width;
      setHeaderWidth(width);
    };
    compute();
    const watcher = new ResizeObserver(compute);
    watcher.observe(el);
    return () => watcher.disconnect();
  }, []);
  const header = storeHeaderPlan(headerWidth);

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
          ...PAGE_SCROLLER,
          // The same trick at the top, so focusing a row never slides it
          // under the pinned nav and search.
          scrollPaddingTop: `${pinned.height}px`,
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
          ref={pinned.ref}
          style={{
            position: "sticky",
            top: 0,
            zIndex: 3,
            // The page's own dark ground, so content passing behind is
            // hidden rather than ghosting through the tabs.
            background: "#0e141b",
            margin: "0 -24px",
            padding: "0 24px 6px",
            boxShadow: "0 6px 12px -8px rgba(0,0,0,0.9)",
          }}
        >
          <TabBar currentId="store" />
        {/* ---- Header: [game art] [title/count] ..... [search] [sort] ----
            Inside the sticky block on purpose. Pinning the tabs alone left
            the search scrolled off the top, which is the half Michael was
            actually reaching for. Nav and search are the two things a store
            page must never hide. */}
        {/* The ref lives on a plain div, same as the rails: Focusable is
            Steam's component and whether it forwards refs is not ours to
            rely on. */}
        <div ref={headerRef}>
        <Focusable
          style={{
            display: "flex",
            alignItems: "center",
            gap: `${header.gap}px`,
            padding: "12px 0",
          }}
        >
          {/* Every size below comes from storeHeaderPlan, which is derived
              from this row's MEASURED width - tested in panelRules.test.mjs
              to fit at every width. The previous fixed sizes assumed the
              Deck's logical width and were wider than the real one, so the
              filter hung off screen and focusing it panned the page. The
              controls keep words at every tier: the icon-square experiment
              proved the icons meant nothing. */}
          {header.artHeight > 0 && (
            <img
              src={`https://cdn.cloudflare.steamstatic.com/steam/apps/${game.appId}/header.jpg`}
              alt=""
              onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
              style={{
                height: `${header.artHeight}px`,
                borderRadius: "6px",
                flexShrink: 0,
              }}
            />
          )}
          <div
            style={{
              flexShrink: 1,
              minWidth: `${header.titleMin}px`,
              overflow: "hidden",
            }}
          >
            <h2
              style={{
                margin: 0,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                lineHeight: 1.15,
              }}
            >
              {game.displayName}
            </h2>
            <div style={{ fontSize: "13px", fontWeight: 400, opacity: 0.6 }}>
              {total !== undefined ? `${total.toLocaleString()} mods` : "loading…"}
            </div>
          </div>
          <div style={{ flexGrow: 1 }} />
          {!collectionsMode && (
          <>
          <div
            style={{
              flexGrow: 1,
              flexShrink: 1,
              minWidth: `${header.searchMin}px`,
              maxWidth: `${header.searchMax}px`,
            }}
          >
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
          <div style={{ width: `${header.controlWidth}px`, flexShrink: 0 }}>
            <Dropdown
              rgOptions={SORT_OPTIONS}
              selectedOption={sort}
              onChange={(opt) => setSort(opt.data)}
              strDefaultLabel="Sort"
            />
          </div>
          <div
            style={{
              width: `${header.controlWidth}px`,
              flexShrink: 0,
              // The last control needs air against the screen edge.
              marginRight: `${STORE_HEADER_EDGE}px`,
            }}
          >
            <Dropdown
              rgOptions={[
                { data: "", label: "All mods" },
                // The recency options lead because they answer the
                // question this filter exists for on live-service games:
                // "which of these actually work right now?"
                ...(game.hd2Layout
                  ? [{ data: "d:-1", label: "Since game update" }]
                  : []),
                { data: "d:7", label: "Updated this week" },
                { data: "d:30", label: "Updated this month" },
                ...categories.map((c) => ({ data: c, label: c })),
              ]}
              selectedOption={filter}
              onChange={(opt) => setFilter(opt.data)}
              strDefaultLabel="Filter"
            />
          </div>
          </>
          )}
        </Focusable>
        </div>
        </div>

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
            <HiddenNote text={hiddenCollectionsNote(allHidden, false)} />
            <div ref={collectionGridRef}>
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
                  blur={blurAdult}
                />
              ))}
            </Focusable>
            </div>
            {allCollections.length === 0 && (
              <div style={{ opacity: 0.8, padding: "12px 0" }}>
                Loading collections…
              </div>
            )}
            {collectionsHasMore && allCollections.length > 0 && (
              <Focusable style={{ margin: "14px auto 0", maxWidth: "320px" }}>
                <ButtonItem
                  layout="below"
                  onClick={() => {
                    // The collections version never disabled itself, but
                    // left focus on the button while the new cards landed
                    // above it. Same move as the mods list (issue #32).
                    if (focusAfterLoad.current) return;
                    focusAfterLoad.current = {
                      grid: "collections",
                      index: allCollections.length,
                    };
                    fetchCollectionsPage(allCollections.length, true);
                  }}
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
                <HiddenNote text={hiddenCollectionsNote(railHidden, false)} />
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
                      blur={blurAdult}
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
            {search.trim() !== "" && linkHit && (
              <div style={{ marginTop: "10px" }}>
                {linkHit.kind === "found" ? (
                  <>
                    <SectionHeading title="From your link" />
                    {linkHit.game.appId !== game.appId && (
                      <HiddenNote
                        text={`A ${linkHit.game.displayName} collection.`}
                      />
                    )}
                    <Focusable
                      style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(3, 1fr)",
                        gap: "12px",
                      }}
                    >
                      <CollectionCard
                        game={linkHit.game}
                        c={linkHit.collection}
                        verdict={verdicts[linkHit.collection.slug]}
                        blur={blurAdult}
                      />
                    </Focusable>
                  </>
                ) : (
                  <HiddenNote
                    text={
                      linkHit.kind === "adult"
                        ? ADULT_LINK_NOTE
                        : unsupportedLinkNote(linkHit.domain)
                    }
                  />
                )}
              </div>
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
              <>
              <div style={{ marginTop: "8px" }}>
                <HiddenNote text={hiddenCollectionsNote(searchHidden, true)} />
              </div>
              <Focusable
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  gap: "12px",
                }}
              >
                {searchCollections.map((c) => (
                  <CollectionCard
                      key={c.slug}
                      game={game}
                      c={c}
                      verdict={verdicts[c.slug]}
                      blur={blurAdult}
                    />
                ))}
                {searchCollections.length === 0 && (
                  <div style={{ opacity: 0.7, fontSize: "13px" }}>
                    No collections match "{search.trim()}".
                    {!searchHidden && !linkHit && ` ${LINK_HINT}`}
                  </div>
                )}
              </Focusable>
              </>
            ) : (
            <div ref={modGridRef}>
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
            </div>
            )}
            {hasMore && (
              <Focusable style={{ margin: "16px auto 0", maxWidth: "320px" }}>
                <ButtonItem
                  layout="below"
                  // Never disabled: see focusAfterLoad (issue #32).
                  onClick={() => {
                    if (loading) return;
                    focusAfterLoad.current = { grid: "mods", index: mods.length };
                    fetchPage(nextOffset.current, true);
                  }}
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
