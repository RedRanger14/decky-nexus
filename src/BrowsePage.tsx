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

import { NexusMod, getMods } from "./api";
import { SupportedGame, getActiveGame } from "./games";
import { setSelectedMod } from "./state";

const SORT_OPTIONS = [
  { data: "endorsements", label: "Most endorsed" },
  { data: "downloads", label: "Most downloaded" },
  { data: "updatedAt", label: "Recently updated" },
  { data: "createdAt", label: "Newest" },
];

const PAGE_SIZE = 24;

function ModTile({ mod, game }: { mod: NexusMod; game: SupportedGame }) {
  return (
    <Focusable
      onActivate={() => {
        setSelectedMod({ game, mod });
        Navigation.Navigate("/nexus-mods/mod");
      }}
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

export function BrowsePage() {
  const game = getActiveGame(
    Router.MainRunningApp ? Number(Router.MainRunningApp.appid) : undefined
  );

  const [sort, setSort] = useState("endorsements");
  const [search, setSearch] = useState("");
  const [mods, setMods] = useState<NexusMod[]>([]);
  const [total, setTotal] = useState<number | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  // Raw offset into the server-side result set (client-side adult filtering
  // means mods.length can lag behind this).
  const nextOffset = useRef(0);

  const fetchPage = async (offset: number, append: boolean) => {
    setLoading(true);
    try {
      const result = await getMods(
        game.nexusDomain,
        sort,
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

  useEffect(() => {
    // Debounce while typing; instant for sort changes / initial load.
    const timer = setTimeout(() => fetchPage(0, false), search ? 500 : 0);
    return () => clearTimeout(timer);
  }, [game.appId, sort, search]);

  const hasMore = total !== undefined && nextOffset.current < total;

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
      }}
    >
      <Focusable
        style={{
          display: "flex",
          alignItems: "center",
          gap: "16px",
          padding: "12px 0",
        }}
      >
        <div style={{ flexShrink: 0, minWidth: 0 }}>
          <h2 style={{ margin: 0, whiteSpace: "nowrap", lineHeight: 1.15 }}>
            {game.displayName}
          </h2>
          <div style={{ fontSize: "13px", fontWeight: 400, opacity: 0.6 }}>
            {total !== undefined ? `${total.toLocaleString()} mods` : "loading…"}
          </div>
        </div>
        <div style={{ flexGrow: 1, maxWidth: "380px" }}>
          <TextField
            label="Search"
            value={search}
            bShowClearAction={true}
            onChange={(e) => setSearch(e?.target?.value ?? "")}
          />
        </div>
        <div style={{ width: "220px", flexShrink: 0, marginLeft: "auto" }}>
          <Dropdown
            rgOptions={SORT_OPTIONS}
            selectedOption={sort}
            onChange={(opt) => setSort(opt.data)}
            strDefaultLabel="Sort"
          />
        </div>
      </Focusable>

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

      {/* autoFocus pulls initial gamepad focus into the grid - without it,
          focus starts on the system header and "down" appears dead until the
          user first navigates up. */}
      <Focusable
        autoFocus={true}
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))",
          gap: "14px",
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
    </Focusable>
  );
}
