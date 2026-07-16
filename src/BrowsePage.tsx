import {
  ButtonItem,
  Dropdown,
  Focusable,
  Navigation,
  QuickAccessTab,
  Router,
  TextField,
} from "@decky/ui";
import { useEffect, useRef, useState } from "react";

import { ModsResult, NexusMod, getMods, getTrendingMods } from "./api";
import { SupportedGame, getActiveGame } from "./games";
import { setSelectedMod } from "./state";
import { NEXUS_ORANGE } from "./theme";

const SORT_OPTIONS = [
  { data: "featured", label: "Featured" },
  { data: "endorsements", label: "Most endorsed" },
  { data: "downloads", label: "Most downloaded" },
  { data: "updatedAt", label: "Recently updated" },
  { data: "createdAt", label: "Newest" },
];

const PAGE_SIZE = 24;
const ROW_SIZE = 8;

function openMod(game: SupportedGame, mod: NexusMod) {
  setSelectedMod({ game, mod });
  Navigation.Navigate("/nexus-mods/mod");
}

function statsLine(mod: NexusMod): string {
  return `${mod.author} · 👍 ${mod.endorsements.toLocaleString()} · ⬇ ${mod.downloads.toLocaleString()}`;
}

/** Big-and-bold hero card: full-bleed image, title on a gradient. */
function HeroCard({ mod, game }: { mod: NexusMod; game: SupportedGame }) {
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
      {(mod.pictureUrl ?? mod.thumbnailUrl) && (
        <img
          src={mod.pictureUrl ?? mod.thumbnailUrl}
          alt={mod.name}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
        />
      )}
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
        <div style={{ fontSize: "12px", opacity: 0.85 }}>{statsLine(mod)}</div>
      </div>
    </Focusable>
  );
}

function ModTile({ mod, game }: { mod: NexusMod; game: SupportedGame }) {
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
        <img
          src={mod.thumbnailUrl}
          alt={mod.name}
          style={{ width: "100%", aspectRatio: "16 / 9", objectFit: "cover", display: "block" }}
        />
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
          👍 {mod.endorsements.toLocaleString()} · ⬇ {mod.downloads.toLocaleString()}
        </div>
      </div>
    </Focusable>
  );
}

/** Section heading with a brand accent bar - the 10-foot-UI signpost. */
function SectionHeading({ title }: { title: string }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        margin: "18px 0 8px",
      }}
    >
      <div
        style={{
          width: "4px",
          height: "18px",
          background: NEXUS_ORANGE,
          borderRadius: "2px",
        }}
      />
      <h3 style={{ margin: 0 }}>{title}</h3>
    </div>
  );
}

/** Horizontally scrolling, controller-focusable carousel row. */
function ModCarousel({
  title,
  mods,
  game,
}: {
  title: string;
  mods: NexusMod[];
  game: SupportedGame;
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
        {mods.map((mod) => (
          <div key={mod.modId} style={{ width: "205px", flexShrink: 0 }}>
            <ModTile mod={mod} game={game} />
          </div>
        ))}
      </Focusable>
    </>
  );
}

export function BrowsePage() {
  const game = getActiveGame(
    Router.MainRunningApp ? Number(Router.MainRunningApp.appid) : undefined
  );

  const [sort, setSort] = useState("featured");
  const [search, setSearch] = useState("");

  // list mode
  const [mods, setMods] = useState<NexusMod[]>([]);
  const [total, setTotal] = useState<number | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const nextOffset = useRef(0);

  // home mode
  const [trending, setTrending] = useState<NexusMod[]>([]);
  const [newest, setNewest] = useState<NexusMod[]>([]);
  const [popular, setPopular] = useState<NexusMod[]>([]);

  const isHome = sort === "featured" && search.trim() === "";
  const effectiveSort = sort === "featured" ? "endorsements" : sort;

  const fetchPage = async (offset: number, append: boolean) => {
    setLoading(true);
    try {
      const result = await getMods(
        game.nexusDomain,
        effectiveSort,
        PAGE_SIZE,
        offset,
        search.trim()
      );
      if (result.ok) {
        setError(undefined);
        setTotal(result.total);
        nextOffset.current = offset + PAGE_SIZE;
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
    setTrending([]);
    setNewest([]);
    setPopular([]);
    setTotal(undefined);
    getTrendingMods(game.nexusDomain, 10).then(apply(setTrending));
    getMods(game.nexusDomain, "createdAt", ROW_SIZE, 0, "").then(apply(setNewest));
    getMods(game.nexusDomain, "endorsements", ROW_SIZE, 0, "").then((r) => {
      if (!cancelled && r.ok) {
        setPopular(r.mods ?? []);
        setTotal(r.total);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [game.appId]);

  useEffect(() => {
    if (isHome) return;
    // Debounce while typing; instant for sort changes.
    const timer = setTimeout(() => fetchPage(0, false), search ? 500 : 0);
    return () => clearTimeout(timer);
  }, [game.appId, sort, search]);

  const hasMore = total !== undefined && nextOffset.current < total;
  const heroMods = trending.slice(0, 2);
  const carouselTrending = trending.slice(2);

  return (
    // onCancel: B returns to the plugin's QAM panel instead of dumping the
    // user on the home screen with everything closed.
    <Focusable
      onCancel={() => {
        Navigation.NavigateBack();
        Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);
      }}
      style={{
        marginTop: "40px",
        height: "calc(100% - 40px)",
        overflowY: "auto",
        padding: "0 24px 24px",
        position: "relative",
      }}
    >
      {/* Game hero art as a faded backdrop banner behind the header. */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: "220px",
          overflow: "hidden",
          pointerEvents: "none",
          zIndex: 0,
        }}
      >
        <img
          src={`https://cdn.cloudflare.steamstatic.com/steam/apps/${game.appId}/library_hero.jpg`}
          alt=""
          onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            opacity: 0.28,
          }}
        />
        <div
          style={{
            position: "absolute",
            inset: 0,
            background:
              "linear-gradient(180deg, rgba(11,14,19,0.15) 0%, rgba(11,14,19,0.55) 55%, rgba(11,14,19,1) 100%)",
          }}
        />
      </div>

      <div style={{ position: "relative", zIndex: 1 }}>
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
          <div style={{ width: "300px", flexShrink: 0 }}>
            <TextField
              label="Search"
              value={search}
              bShowClearAction={true}
              onChange={(e) => setSearch(e?.target?.value ?? "")}
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
        </Focusable>

        {isHome ? (
          <>
            {/* ---- Hero: top two trending mods, big and bold ---- */}
            {heroMods.length > 0 && (
              <>
                <SectionHeading title="Trending now" />
                <Focusable
                  autoFocus={true}
                  style={{
                    display: "grid",
                    gridTemplateColumns:
                      heroMods.length > 1 ? "1fr 1fr" : "1fr",
                    gap: "14px",
                  }}
                >
                  {heroMods.map((mod) => (
                    <HeroCard key={mod.modId} mod={mod} game={game} />
                  ))}
                </Focusable>
              </>
            )}
            {trending.length === 0 && (
              <div style={{ padding: "20px 0", opacity: 0.8 }}>
                Loading mods…
              </div>
            )}
            <ModCarousel
              title="Also trending"
              mods={carouselTrending}
              game={game}
            />
            <ModCarousel title="New mods" mods={newest} game={game} />
            <ModCarousel title="All-time favourites" mods={popular} game={game} />
          </>
        ) : (
          <>
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
            <Focusable
              autoFocus={true}
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))",
                gap: "14px",
                marginTop: "8px",
              }}
            >
              {mods.map((mod) => (
                <ModTile key={mod.modId} mod={mod} game={game} />
              ))}
            </Focusable>
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
          </>
        )}
      </div>
    </Focusable>
  );
}
