import asyncio
import glob
import json
import os
import re
import shutil
import ssl
import time
import urllib.parse

import aiohttp

import decky

# v1: main Steam library only. TODO: parse libraryfolders.vdf for SD-card /
# secondary library installs before adding games likely to live there.
STEAM_COMMON = os.path.join(
    decky.DECKY_USER_HOME, ".steam", "steam", "steamapps", "common"
)

SETTINGS_PATH = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
DOWNLOADS_DIR = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "downloads")
SAVE_BACKUPS_DIR = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "save-backups")
STEAM_USERDATA = os.path.join(decky.DECKY_USER_HOME, ".steam", "steam", "userdata")

NEXUS_API_BASE = "https://api.nexusmods.com"
NEXUS_V2_GRAPHQL = f"{NEXUS_API_BASE}/v2/graphql"


def _read_app_version() -> str:
    """Single source of truth: the package.json sitting next to main.py
    (repo root in dev, the plugin dir on device)."""
    try:
        with open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "package.json"),
            encoding="utf-8",
        ) as f:
            return json.load(f).get("version") or "0.0.0"
    except (OSError, ValueError):
        return "0.0.0"


APP_VERSION = _read_app_version()
# The Nexus acceptable-use policy requires clients to identify themselves,
# and the v2 endpoint's WAF rejects requests without a real User-Agent.
APP_HEADERS = {
    "Application-Name": "decky-nexus",
    "Application-Version": APP_VERSION,
    "User-Agent": f"decky-nexus/{APP_VERSION} (SteamOS; Decky Loader plugin)",
}


def _make_ssl_context() -> ssl.SSLContext:
    """Decky's bundled Python can't always find the OS trust store, which
    makes aiohttp fail with ClientConnectorCertificateError. Point it at a
    CA bundle explicitly, trying certifi first, then the SteamOS paths."""
    candidates = []
    try:
        import certifi

        candidates.append(certifi.where())
    except ImportError:
        pass
    candidates += ["/etc/ssl/certs/ca-certificates.crt", "/etc/ssl/cert.pem"]
    for cafile in candidates:
        if cafile and os.path.isfile(cafile):
            try:
                return ssl.create_default_context(cafile=cafile)
            except ssl.SSLError:
                continue
    return ssl.create_default_context()


SSL_CONTEXT = _make_ssl_context()

MOD_FIELDS = """
      modId
      name
      summary
      author
      version
      endorsements
      downloads
      thumbnailUrl
      pictureUrl
      updatedAt
      adultContent
"""

TRENDING_WINDOW_DAYS = 30


def _build_mods_query(
    with_search: bool, trending_since=None, include_adult: bool = False
) -> str:
    """Compose the browse query. WILDCARD does substring matching
    server-side; date filters take epoch seconds (verified - ISO datetimes
    break the backing Lucene query). 'Trending' = created within the window,
    sorted by downloads. Adult content is excluded unless the user opted
    in (mirrors the site's default)."""
    filters = ["gameDomainName: [{ value: $domain, op: EQUALS }]"]
    if not include_adult:
        filters.append("adultContent: [{ value: false }]")
    params = "$domain: String!, $count: Int!, $offset: Int!"
    if with_search:
        filters.append("name: [{ value: $search, op: WILDCARD }]")
        params += ", $search: String!"
    if trending_since is not None:
        filters.append(
            'createdAt: [{ value: "%d", op: GT }]' % int(trending_since)
        )
        sort_part = "[{ downloads: { direction: DESC } }]"
    else:
        sort_part = "$sort"
        params += ", $sort: [ModsSort!]"
    return f"""
query BrowseMods({params}) {{
  mods(
    filter: {{ {" ".join(filters)} }}
    sort: {sort_part}
    count: $count
    offset: $offset
  ) {{
    nodesCount
    nodes {{{MOD_FIELDS}}}
  }}
}}
"""

SORT_FIELDS = {
    "endorsements",
    "downloads",
    "updatedAt",
    "createdAt",
    "relevance",
    "trending",
}

# domain -> numeric game id, resolved once per session via GraphQL
_GAME_ID_CACHE: dict = {}

# v1 file categories worth showing (main, patch, optional, old version, misc).
# Old versions are included deliberately: installing one is how users revert.
VISIBLE_FILE_CATEGORIES = {1, 2, 3, 4, 5}


def _load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_settings(settings: dict) -> None:
    os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    # The settings file holds the API key - keep it owner-only.
    os.chmod(SETTINGS_PATH, 0o600)


def _api_headers(api_key=None) -> dict:
    headers = dict(APP_HEADERS)
    if api_key:
        headers["apikey"] = api_key
    return headers


def _game_paths(install_dir: str, mods_subdir: str):
    install_path = os.path.join(STEAM_COMMON, install_dir)
    mods_path = os.path.join(install_path, mods_subdir)
    disabled_path = os.path.join(install_path, f"{mods_subdir}-disabled")
    return install_path, mods_path, disabled_path


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "", name).strip().strip(".")
    return cleaned or "mod"


def _force_rmtree(path: str) -> None:
    """rmtree that survives read-only dirs shipped inside mod archives
    (seen in the wild: zip entries extracted without owner write)."""
    if not os.path.lexists(path):
        return
    for root, _dirs, _files in os.walk(path):
        try:
            os.chmod(root, 0o755)
        except OSError:
            pass
    shutil.rmtree(path)


def _normalize_perms(path: str) -> None:
    """Make extracted content sane: dirs 755, files 644, so later moves,
    replaces, and uninstalls never fight archive permission bits."""
    for root, _dirs, files in os.walk(path):
        try:
            os.chmod(root, 0o755)
        except OSError:
            pass
        for name in files:
            fp = os.path.join(root, name)
            if not os.path.islink(fp):
                try:
                    os.chmod(fp, 0o644)
                except OSError:
                    pass


def _normalize_requirements(raw: list) -> list:
    """The v2 API returns requirement modId as a STRING, and external
    requirements (VC++ redist links etc.) come through as modId "0" with an
    empty name - only real Nexus mods (modId > 0) are openable in-app."""
    reqs = []
    for r in raw or []:
        try:
            rid = int(r.get("modId") or 0)
        except (TypeError, ValueError):
            rid = 0
        reqs.append(
            {
                "modName": r.get("modName") or "",
                "modId": rid,
                "notes": r.get("notes") or "",
                "url": r.get("url") or "",
            }
        )
    return reqs


def _collection_sort_field(sort: str) -> str:
    """Frontend sort keys -> collectionsV2 sort fields (verified against
    the live API - 'totalDownloads' does not exist; it's 'downloads')."""
    return {
        "endorsements": "endorsements",
        "downloads": "downloads",
        "updatedAt": "updatedAt",
        "createdAt": "createdAt",
        "trending": "recentRating",
    }.get(sort, "endorsements")


def _show_adult() -> bool:
    """Hard-locked to False: UK OSA-class laws require age verification
    before adult content can be shown, verification happens on the Nexus
    Mods platform, and the API exposes no way to read that status - so
    the plugin must not offer its own opt-in. Re-enable only when the
    API can report the account's verified content preferences."""
    return False


async def _gql_query_vars(query: str, variables: dict, api_key=None) -> dict:
    """Like _gql_query but with GraphQL variables (collections queries)."""
    headers = {
        **_api_headers(api_key),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=20)
    ) as session:
        async with session.post(
            NEXUS_V2_GRAPHQL,
            json={"query": query, "variables": variables},
            headers=headers,
            ssl=SSL_CONTEXT,
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            body = await resp.json()
            if body.get("errors"):
                raise RuntimeError(
                    body["errors"][0].get("message", "GraphQL error")
                )
            return body["data"]


async def _gql_query(query: str, api_key=None) -> dict:
    """POST one GraphQL query to the v2 endpoint; returns `data` or raises
    RuntimeError with a readable message."""
    headers = {
        **_api_headers(api_key),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=20)
    ) as session:
        async with session.post(
            NEXUS_V2_GRAPHQL, json={"query": query}, headers=headers, ssl=SSL_CONTEXT
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            body = await resp.json()
            if body.get("errors"):
                raise RuntimeError(body["errors"][0].get("message", "GraphQL error"))
            return body["data"]


async def _resolve_game_id(game_domain: str, api_key=None) -> int:
    game_id = _GAME_ID_CACHE.get(game_domain)
    if game_id is None:
        data = await _gql_query(
            '{ game(domainName: "%s") { id } }' % game_domain, api_key
        )
        game_id = data["game"]["id"]
        _GAME_ID_CACHE[game_domain] = game_id
    return int(game_id)


def _norm_version(version) -> str:
    return (version or "").strip().lstrip("vV")


def _map_v1_mod(m: dict) -> dict:
    """Map a REST v1 mod object onto the same shape our GraphQL v2 queries
    return, so the frontend can reuse tiles/detail pages transparently."""
    return {
        "modId": m.get("mod_id"),
        "name": m.get("name") or f"Mod {m.get('mod_id')}",
        "summary": m.get("summary") or "",
        "author": m.get("author") or m.get("uploaded_by") or "",
        "version": m.get("version") or "",
        "endorsements": m.get("endorsement_count") or 0,
        "downloads": m.get("mod_downloads") or 0,
        "thumbnailUrl": m.get("picture_url"),
        "pictureUrl": m.get("picture_url"),
        "updatedAt": m.get("updated_time") or "",
        "adultContent": bool(m.get("contains_adult_content")),
    }


def _sort_mod_files(files: list) -> list:
    """Old versions last even when Nexus's is_primary flag is stale and
    points at one (seen in the wild: SMAPI's primary flag stuck on a 2020
    file). Within current files, primary first."""
    files.sort(
        key=lambda f: (
            f["category_name"] == "OLD_VERSION",
            not f["is_primary"],
            f["category_name"],
            f["name"],
        )
    )
    return files


def _pick_main_file(file_list: list, avoid_keywords: list = ()):
    """Latest MAIN-category file; never trust is_primary alone.

    avoid_keywords drops files for other stores by name: SKSE publishes
    Steam and GOG builds as separate MAIN files on one mod page, and the
    GOG one (uploaded later, so a higher file_id) refuses to run against
    the Steam game. No fallback past the filter - installing a known-wrong
    build is worse than reporting there's nothing suitable."""
    avoid = [k.lower() for k in avoid_keywords if k]
    if avoid:
        file_list = [
            f
            for f in file_list
            if not any(
                k in (f.get("name") or "").lower()
                or k in (f.get("file_name") or "").lower()
                for k in avoid
            )
        ]
    mains = [f for f in file_list if f.get("category_name") == "MAIN"]
    if mains:
        return max(mains, key=lambda f: f["file_id"])
    return next(
        (f for f in file_list if f.get("category_name") != "OLD_VERSION"), None
    )


NXM_QUEUE_NAME = "nxm-queue.log"

# ---- launch options (dlo-aware) ----------------------------------------------
# The decky-launch-options plugin, when installed, rewrites every game's
# Steam launch options to "~/.dlo/run %command%" and replays the real
# command from its own settings file. Undoing a framework's launch command
# on such a device means editing dlo's profile - clearing Steam's field
# would leave the stale command in dlo's replay (this bricked the Skyrim
# vanilla reset, 2026-07-23: dlo kept exec'ing a deleted skse64_loader.exe).
# Steam's own localconfig.vdf can't be edited safely while Steam runs, so
# non-dlo devices set/clear the field from the frontend via
# SteamClient.Apps.SetAppLaunchOptions instead.


def _dlo_settings_path() -> str:
    return os.path.join(decky.DECKY_USER_HOME, ".dlo", "settings.json")


def _dlo_present() -> bool:
    return os.path.isfile(_dlo_settings_path())


def _dlo_get_original(path: str, app_id: int):
    """The app's originalLaunchOptions from dlo's settings, or None when
    there is no readable settings file / profile."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    prof = (data.get("profiles") or {}).get(str(int(app_id)))
    if not isinstance(prof, dict):
        return None
    return str(prof.get("originalLaunchOptions") or "")


def _dlo_set_original(path: str, app_id: int, value: str):
    """Set profiles[app_id].originalLaunchOptions in dlo's settings and
    return (ok, previous_value). Creates the profile when missing - dlo
    treats absent profiles as empty, so a fresh one is safe. Other
    profiles and dlo's own file format (indent=4) are preserved."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False, None
    profiles = data.setdefault("profiles", {})
    prof = profiles.setdefault(
        str(int(app_id)), {"state": {}, "originalLaunchOptions": ""}
    )
    previous = str(prof.get("originalLaunchOptions") or "")
    prof["originalLaunchOptions"] = str(value or "")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except OSError:
        return False, previous
    return True, previous


_VDF_LAUNCH_OPTIONS_RE = re.compile(r'"LaunchOptions"\s+"((?:[^"\\]|\\.)*)"')


def _parse_vdf_launch_options(text: str, app_id: int) -> list:
    """Distinct LaunchOptions values near the app's id in a localconfig.vdf
    body. Diagnostics-grade: VDF isn't fully parsed, values are picked from
    a bounded window after each id occurrence."""
    out = []
    needle = f'"{int(app_id)}"'
    idx = text.find(needle)
    while idx != -1:
        m = _VDF_LAUNCH_OPTIONS_RE.search(text, idx, idx + 4000)
        if m and m.group(1) not in out:
            out.append(m.group(1))
        idx = text.find(needle, idx + 1)
    return out


def _read_steam_launch_options(app_id: int) -> list:
    """Read-only peek at every Steam account's localconfig.vdf."""
    out = []
    for cfg in glob.glob(
        os.path.join(STEAM_USERDATA, "*", "config", "localconfig.vdf")
    ):
        try:
            with open(cfg, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for val in _parse_vdf_launch_options(text, app_id):
            if val not in out:
                out.append(val)
    return out


# ---- dataDir install mode (Skyrim-class games) -------------------------------
# Mods merge into the game's Data/ dir instead of per-mod folders: installs
# record a per-file manifest, enable/disable toggles the '*' activation flag
# in plugins.txt (which lives inside the Proton prefix), uninstall removes
# exactly the manifest's files.

PLUGIN_EXTENSIONS = (".esp", ".esm", ".esl")
DATA_MARKER_DIRS = {
    "meshes", "textures", "scripts", "interface", "sound", "music",
    "seq", "skse", "strings", "shadersfx", "grass", "materials",
}


def _adopt_case(path: str) -> str:
    """If the exact path is missing but a sibling differs only by case,
    return the sibling - Wine-created files can carry any casing."""
    if os.path.exists(path):
        return path
    parent, want = os.path.dirname(path), os.path.basename(path).lower()
    try:
        for entry in os.listdir(parent):
            if entry.lower() == want:
                return os.path.join(parent, entry)
    except OSError:
        pass
    return path


def _prefix_user_path(app_id: int, *parts: str) -> str:
    """A path inside the Proton prefix's Windows user profile."""
    return os.path.join(
        decky.DECKY_USER_HOME, ".steam", "steam", "steamapps", "compatdata",
        str(int(app_id)), "pfx", "drive_c", "users", "steamuser", *parts,
    )


def _plugins_txt_path(app_id: int, subpath: str) -> str:
    """Plugins.txt for a Proton game lives inside its compat prefix. The
    game creates it through Wine's case-insensitive lookup, so the on-disk
    casing can differ from ours - reuse an existing file of any casing
    rather than create a duplicate next to it."""
    return _adopt_case(
        _prefix_user_path(app_id, "AppData", "Local", *subpath.split("/"))
    )


def _game_prefs_path(app_id: int, subpath: str) -> str:
    """A game's prefs ini under Documents/My Games in the Proton prefix."""
    return _adopt_case(
        _prefix_user_path(app_id, "Documents", "My Games", *subpath.split("/"))
    )


def _read_ini_settings(path: str, section: str, keys: list) -> dict:
    """Current values of the given keys in [section]. Case-insensitive on
    section and key names (Bethesda inis mix casings freely)."""
    values = {}
    if not os.path.isfile(path):
        return values
    want = {k.lower(): k for k in keys}
    in_section = False
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_section = stripped[1:-1].lower() == section.lower()
                continue
            if in_section and "=" in stripped and not stripped.startswith((";", "#")):
                key, _, val = stripped.partition("=")
                canon = want.get(key.strip().lower())
                if canon:
                    values[canon] = val.strip()
    return values


def _patch_ini_settings(path: str, section: str, settings: dict) -> None:
    """Set keys in [section], preserving everything else (order, comments,
    unrelated sections). Missing keys are appended to the section; a missing
    section is appended to the file. Writes a one-time .decky-nexus.bak."""
    lines = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        backup = path + ".decky-nexus.bak"
        if not os.path.isfile(backup):
            shutil.copy2(path, backup)

    remaining = {k.lower(): (k, v) for k, v in settings.items()}
    out = []
    in_section = False
    section_end = None  # where to append keys the section doesn't have yet
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section:
                section_end = len(out)
            in_section = stripped[1:-1].lower() == section.lower()
            out.append(line)
            continue
        if in_section and "=" in stripped and not stripped.startswith((";", "#")):
            key = stripped.partition("=")[0].strip().lower()
            if key in remaining:
                canon, val = remaining.pop(key)
                out.append(f"{canon}={val}")
                continue
        out.append(line)
    if in_section:
        section_end = len(out)

    if remaining:
        pairs = [f"{k}={v}" for k, v in remaining.values()]
        if section_end is not None:
            out[section_end:section_end] = pairs
        else:
            if out and out[-1].strip():
                out.append("")
            out.append(f"[{section}]")
            out.extend(pairs)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")


def _read_plugins_txt(path: str) -> list:
    if not path or not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def _write_plugins_txt(path: str, lines: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def _add_plugins(path: str, names: list, style: str = "starred") -> None:
    """Activate plugins. 'starred' (SSE/FO4): '*Name.esp' lines; 'listed'
    (FNV/FO3/Oldrim): a plugin's bare presence in the file activates it."""
    lines = _read_plugins_txt(path)
    existing = {
        l.lstrip("*").strip().lower()
        for l in lines
        if l.strip() and not l.startswith("#")
    }
    for name in names:
        if name.lower() not in existing:
            existing.add(name.lower())
            lines.append(name if style == "listed" else "*" + name)
    _write_plugins_txt(path, lines)


def _set_plugins_active(
    path: str, names: list, active: bool, style: str = "starred"
) -> None:
    if style == "listed":
        # Presence IS activation: enable = list, disable = delist.
        if active:
            _add_plugins(path, names, style)
        else:
            _remove_plugins(path, names)
        return
    targets = {n.lower() for n in names}
    out = []
    for line in _read_plugins_txt(path):
        bare = line.lstrip("*").strip()
        if bare.lower() in targets and not line.startswith("#"):
            out.append(("*" + bare) if active else bare)
        else:
            out.append(line)
    _write_plugins_txt(path, out)


def _active_plugins(path: str, style: str = "starred") -> set:
    """Lower-cased names of plugins the file currently activates."""
    active = set()
    for line in _read_plugins_txt(path):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if style == "listed":
            active.add(stripped.lower())
        elif line.startswith("*"):
            active.add(line[1:].strip().lower())
    return active


def _remove_plugins(path: str, names: list) -> None:
    targets = {n.lower() for n in names}
    lines = [
        l
        for l in _read_plugins_txt(path)
        if l.lstrip("*").strip().lower() not in targets
    ]
    _write_plugins_txt(path, lines)


def _looks_like_data(dir_path: str) -> bool:
    try:
        names = os.listdir(dir_path)
    except OSError:
        return False
    for name in names:
        low = name.lower()
        if low.endswith(PLUGIN_EXTENSIONS) or low.endswith((".bsa", ".ba2")):
            return True
        if low in DATA_MARKER_DIRS and os.path.isdir(os.path.join(dir_path, name)):
            return True
    return False


def _looks_like_ue4ss_mod(scratch: str) -> bool:
    """UE4SS mods come in three shapes: Scripts/main.lua (Lua), a LogicMods
    dir (Blueprint), or dlls/main.dll (native) - usually with an
    enabled.txt marker. All need the UE4SS loader, which we don't support
    yet (open Proton bug) - installing them silently produces 'nothing
    happened' reports."""
    for root, dirs, names in os.walk(scratch):
        low = [n.lower() for n in names]
        base = os.path.basename(root).lower()
        if "main.lua" in low and base == "scripts":
            return True
        if "main.dll" in low and base == "dlls":
            return True
        if "enabled.txt" in low:
            return True
        if any(d.lower() == "logicmods" for d in dirs):
            return True
    return False


def _route_ue4ss_payload(
    scratch: str,
    install_path: str,
    ue4ss_subdir: str,
    logicmods_subdir: str,
    mod_name: str,
) -> dict:
    """Place a UE4SS-shaped payload where the loader actually looks:
    Lua/native mods as folders under ue4ss/Mods (with an enabled.txt
    drop-file), Blueprint paks flat into LogicMods. Returns the install
    record to store."""
    # Blueprint mods: LogicMods/*.pak anywhere in the payload.
    logic_paks = []
    for root, dirs, names in os.walk(scratch):
        if os.path.basename(root).lower() == "logicmods":
            logic_paks.extend(
                os.path.join(root, n)
                for n in names
                if n.lower().endswith(".pak")
            )
    if logic_paks:
        target = os.path.join(install_path, *logicmods_subdir.split("/"))
        os.makedirs(target, exist_ok=True)
        moved = []
        for src in logic_paks:
            name = os.path.basename(src)
            dst = os.path.join(target, name)
            if os.path.isfile(dst):
                os.remove(dst)
            shutil.move(src, dst)
            moved.append(name)
        return {"mode": "files", "target": logicmods_subdir, "files": moved}

    # Lua / native mods: the folder containing Scripts/ or dlls/ IS the mod.
    entries = os.listdir(scratch)
    if len(entries) == 1 and os.path.isdir(os.path.join(scratch, entries[0])):
        src, folder = os.path.join(scratch, entries[0]), entries[0]
    else:
        folder = _safe_name(mod_name)
        src = os.path.join(scratch, folder)
        os.makedirs(src, exist_ok=True)
        for e in entries:
            if e != folder:
                shutil.move(os.path.join(scratch, e), os.path.join(src, e))
    target = os.path.join(install_path, *ue4ss_subdir.split("/"))
    os.makedirs(target, exist_ok=True)
    dst = os.path.join(target, folder)
    _force_rmtree(dst)
    shutil.move(src, dst)
    # enabled.txt activates a mod without touching mods.txt load order.
    marker = os.path.join(dst, "enabled.txt")
    if not os.path.isfile(marker):
        with open(marker, "w", encoding="utf-8") as f:
            f.write("")
    return {"mode": "folder", "target": ue4ss_subdir, "folder": folder}


def _find_data_payload(scratch: str):
    """Locate the directory whose contents belong in Data/. Handles flat
    archives, a wrapping folder (loose readme-type files beside it are
    ignored), and an explicit Data/ folder (up to two levels). Returns None
    for unrecognizable layouts (e.g. FOMOD-only)."""
    if _looks_like_data(scratch):
        return scratch
    entries = os.listdir(scratch)
    dirs = [e for e in entries if os.path.isdir(os.path.join(scratch, e))]
    for d in dirs:
        if d.lower() == "data":
            return os.path.join(scratch, d)
    if len(dirs) == 1:
        inner = os.path.join(scratch, dirs[0])
        if _looks_like_data(inner):
            return inner
        inner_dirs = [
            e for e in os.listdir(inner) if os.path.isdir(os.path.join(inner, e))
        ]
        for d in inner_dirs:
            if d.lower() == "data":
                return os.path.join(inner, d)
        if len(inner_dirs) == 1:
            deep = os.path.join(inner, inner_dirs[0])
            if _looks_like_data(deep):
                return deep
    return None


def _payload_options(scratch: str, max_depth: int = 3) -> list:
    """Option-folder archives (mini-FOMODs): folders that each resolve to a
    Data payload, possibly nested (category dirs holding per-item payloads,
    or wrapper/00 Data/sub-package trees). Recurses past folders that don't
    resolve themselves, skipping fomod metadata dirs. Returns scratch-
    relative paths for the user to pick from (or merge all)."""
    options = []

    def walk(base: str, rel: str, depth: int) -> None:
        try:
            entries = os.listdir(base)
        except OSError:
            return
        for d in sorted(entries, key=str.lower):
            if d.lower() == "fomod":
                continue
            p = os.path.join(base, d)
            if not os.path.isdir(p):
                continue
            r = f"{rel}/{d}" if rel else d
            if _looks_like_data(p) or _find_data_payload(p) is not None:
                options.append(r)
            elif depth < max_depth:
                walk(p, r, depth + 1)

    walk(scratch, "", 0)
    return options


def _find_vortex_override(scratch: str):
    """Path of a top-level vortex_override_instructions.json, if the
    archive ships one (root-payload mods like SSE Engine Fixes part 2 use
    it to say where their files belong)."""
    try:
        for name in os.listdir(scratch):
            if name.lower() == "vortex_override_instructions.json":
                return os.path.join(scratch, name)
    except OSError:
        pass
    return None


def _vortex_override_copies(override_path: str) -> list:
    """(source, destination) pairs from Vortex override instructions.
    Only 'copy' instructions are honored; anything unparseable yields []
    so the caller falls back to copy-everything-to-root."""
    try:
        with open(override_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    instructions = data if isinstance(data, list) else data.get("instructions")
    out = []
    for ins in instructions or []:
        if not isinstance(ins, dict) or ins.get("type") != "copy":
            continue
        src = str(ins.get("source") or "")
        dst = str(ins.get("destination") or src)
        if src and _safe_rel_path(src) and _safe_rel_path(dst):
            out.append((src, dst))
    return out


def _remove_data_dir_record(
    game_domain: str, record_key: str, data_path: str,
    app_id: int, plugins_subpath: str, settings: dict,
) -> bool:
    """Delete a dataDir-mode mod's manifest files, prune empty dirs,
    deactivate+remove its plugins, drop the record. Returns True if the
    record existed."""
    records = settings.get("installed", {}).get(game_domain, {})
    rec = records.get(record_key)
    if not rec or rec.get("mode") != "dataDir":
        return False
    dirs_touched = set()
    for rel in rec.get("files") or []:
        if not _safe_rel_path(rel):
            continue
        target = os.path.join(data_path, *rel.split("/"))
        try:
            if os.path.isfile(target):
                os.remove(target)
        except OSError:
            pass
        parent = os.path.dirname(target)
        while parent and len(parent) > len(data_path):
            dirs_touched.add(parent)
            parent = os.path.dirname(parent)
    for d in sorted(dirs_touched, key=len, reverse=True):
        try:
            os.rmdir(d)  # only succeeds when empty
        except OSError:
            pass
    plugins = rec.get("plugins") or []
    if plugins and plugins_subpath:
        _remove_plugins(_plugins_txt_path(app_id, plugins_subpath), plugins)
    records.pop(record_key, None)
    return True


def _remove_files_record(
    game_domain: str, record_key: str, install_path: str, settings: dict
) -> bool:
    """Delete a files-mode record's files (paths relative to the game
    root or the record's target dir), prune empty dirs, drop the record.
    Returns True if such a record existed."""
    records = settings.get("installed", {}).get(game_domain, {})
    rec = records.get(record_key)
    if not rec or rec.get("mode") != "files":
        return False
    target = rec.get("target") or "."
    base = (
        install_path
        if target in (".", "")
        else os.path.join(install_path, *target.split("/"))
    )
    for rel in rec.get("files") or []:
        if not _safe_rel_path(rel):
            continue
        path = os.path.join(base, *rel.split("/"))
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        parent = os.path.dirname(path)
        while len(parent) > len(base):
            try:
                os.rmdir(parent)
            except OSError:
                break
            parent = os.path.dirname(parent)
    records.pop(record_key, None)
    return True


def _safe_rel_path(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    return all(p not in ("", ".", "..") for p in parts)


def _case_merge_rel(base: str, rel: str) -> str:
    """Adopt the on-disk casing of every existing path component under base.
    Wine resolves an exact-case match before falling back to a scan, so twin
    dirs like Data/Textures + Data/textures silently split mods in half -
    each request only ever sees one of them."""
    resolved = []
    cur = base
    for part in rel.replace("\\", "/").split("/"):
        try:
            entries = os.listdir(cur)
        except OSError:
            entries = []
        match = next((e for e in entries if e.lower() == part.lower()), None)
        chosen = match if match is not None else part
        resolved.append(chosen)
        cur = os.path.join(cur, chosen)
    return "/".join(resolved)


def _version_tuple(v: str):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums) if nums else None


def _is_newer_version(current: str, installed: str) -> bool:
    """Numeric-aware: '6.11' is NEWER than '6.9' (string compare says older).
    Unparseable versions fall back to plain inequality."""
    c, i = _version_tuple(current), _version_tuple(installed)
    if c is None or i is None:
        return bool(current) and current != installed
    return c > i



# ---- Minimal XML -------------------------------------------------------------
# Decky's embedded Python ships WITHOUT the xml package (no pyexpat), so
# xml.etree is unavailable on device (worked in dev, crashed in the field).
# FOMOD ModuleConfig / SubModule.xml / LauncherData.xml are simple XML, so
# this compact regex tokenizer covers them: elements, attributes, text,
# comments, CDATA, self-closing tags, and basic entities. Not a general
# XML parser - good enough for well-formed mod metadata.

_XML_TOKEN = re.compile(
    r"<!--.*?-->"                 # comments
    r"|<!\[CDATA\[.*?\]\]>"   # cdata
    r"|<\?.*?\?>"               # declarations
    r"|<[^>]+>"                   # tags
    r"|[^<]+",                    # text
    re.S,
)
_XML_ATTR = re.compile(r"""([\w:.-]+)\s*=\s*("([^"]*)"|'([^']*)')""")


def _xml_unescape(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&#39;", "'")
        .replace("&amp;", "&")
    )


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class XmlNode:
    __slots__ = ("tag", "attrib", "text", "children")

    def __init__(self, tag: str):
        self.tag = tag
        self.attrib = {}
        self.text = ""
        self.children = []

    def get(self, name, default=None):
        return self.attrib.get(name, default)

    def find(self, tag):
        for c in self.children:
            if c.tag == tag:
                return c
        return None

    def findall(self, tag):
        return [c for c in self.children if c.tag == tag]

    def iter(self, tag=None):
        if tag is None or self.tag == tag:
            yield self
        for c in self.children:
            yield from c.iter(tag)

    def __iter__(self):
        return iter(self.children)

    def append(self, node):
        self.children.append(node)

    def remove(self, node):
        self.children.remove(node)


def xml_parse(text: str) -> XmlNode:
    root = XmlNode("__root__")
    stack = [root]
    for m in _XML_TOKEN.finditer(text):
        tok = m.group(0)
        if tok.startswith("<!--") or tok.startswith("<?"):
            continue
        if tok.startswith("<![CDATA["):
            stack[-1].text += tok[9:-3]
            continue
        if tok.startswith("</"):
            if len(stack) > 1:
                stack.pop()
            continue
        if tok.startswith("<"):
            body = tok[1:-1].strip()
            self_closing = body.endswith("/")
            if self_closing:
                body = body[:-1].rstrip()
            if body.startswith("!"):
                continue  # doctype etc.
            space = body.find(" ")
            tag = body if space < 0 else body[:space]
            node = XmlNode(tag)
            if space >= 0:
                for am in _XML_ATTR.finditer(body[space:]):
                    node.attrib[am.group(1)] = _xml_unescape(
                        am.group(3) if am.group(3) is not None else am.group(4) or ""
                    )
            stack[-1].append(node)
            if not self_closing:
                stack.append(node)
            continue
        # text
        stack[-1].text += _xml_unescape(tok)
    # single document element expected
    for c in root.children:
        return c
    return root


def xml_parse_file(path: str) -> XmlNode:
    """BOM-aware read: FOMOD tooling ships UTF-16 ModuleConfigs in the
    wild (the 'FOMOD Creation Tool' writes UTF-16 LE) - reading those as
    UTF-8 tokenizes NUL-riddled garbage into an empty wizard."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    return xml_parse(text)


def xml_serialize(node: XmlNode, indent: int = 0) -> str:
    pad = "  " * indent
    attrs = "".join(
        ' %s="%s"' % (k, _xml_escape(v)) for k, v in node.attrib.items()
    )
    text = (node.text or "").strip()
    if not node.children and not text:
        return "%s<%s%s />" % (pad, node.tag, attrs)
    if not node.children:
        return "%s<%s%s>%s</%s>" % (pad, node.tag, attrs, _xml_escape(text), node.tag)
    inner = "\n".join(xml_serialize(c, indent + 1) for c in node.children)
    return "%s<%s%s>\n%s\n%s</%s>" % (pad, node.tag, attrs, inner, pad, node.tag)


def xml_write_file(path: str, node: XmlNode) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n')
        f.write(xml_serialize(node))
        f.write("\n")



# ---- Witcher 3 layout --------------------------------------------------------
# Next-gen TW3 (research-verified): mod folders (must start with "mod")
# go to <game>/mods/, DLC-sized components to <game>/dlc/, menu-mod XMLs
# to bin/config/r4game/user_config_matrix/pc/ AND appended (with a
# semicolon) to dx11filelist.txt + dx12filelist.txt. Script mods editing
# the same .ws file as an installed mod are refused - unresolved script
# conflicts are a fatal compile error at launch and Script Merger is
# Windows-only.

W3_MENU_DIR = "bin/config/r4game/user_config_matrix/pc"


def _w3_filelist_append(pc_dir: str, xml_name: str) -> None:
    for fl in ("dx11filelist.txt", "dx12filelist.txt"):
        path = _adopt_case(os.path.join(pc_dir, fl))
        lines = []
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                lines = f.read().splitlines()
        entry = f"{xml_name};"
        if entry not in [l.strip() for l in lines]:
            lines.append(entry)
            with open(path, "w", encoding="utf-8", newline="\r\n") as f:
                f.write("\n".join(lines) + "\n")


def _w3_installed_scripts(mods_path: str) -> dict:
    """Installed script paths -> owning mod folder (for conflict checks)."""
    owners = {}
    if not os.path.isdir(mods_path):
        return owners
    for folder in os.listdir(mods_path):
        scripts = os.path.join(mods_path, folder, "content", "scripts")
        if not os.path.isdir(scripts):
            continue
        for root, _dirs, names in os.walk(scripts):
            for n in names:
                if n.lower().endswith(".ws"):
                    rel = os.path.relpath(os.path.join(root, n), scripts)
                    owners[rel.replace(os.sep, "/").lower()] = folder
    return owners


def _w3_payload_scripts(folder_path: str) -> list:
    scripts = os.path.join(folder_path, "content", "scripts")
    out = []
    if not os.path.isdir(scripts):
        return out
    for root, _dirs, names in os.walk(scripts):
        for n in names:
            if n.lower().endswith(".ws"):
                rel = os.path.relpath(os.path.join(root, n), scripts)
                out.append(rel.replace(os.sep, "/").lower())
    return out


def _route_witcher_payload(
    scratch: str, install_path: str, mods_path: str, mod_name: str
):
    """Classify a TW3 archive into mod folders, dlc folders, and menu
    XMLs. Returns (mod_folders, dlc_folders, menu_xmls, error) with paths
    still inside scratch - the caller moves them."""
    mod_dirs, dlc_dirs, menu_xmls = [], [], []

    def classify_dir(path: str, name: str) -> bool:
        low = name.lower()
        if low.startswith("mod"):
            mod_dirs.append(path)
            return True
        if low.startswith("dlc") and low != "dlc":
            dlc_dirs.append(path)
            return True
        return False

    def scan_level(base: str, depth: int) -> None:
        try:
            entries = os.listdir(base)
        except OSError:
            return
        for e in entries:
            p = os.path.join(base, e)
            low = e.lower()
            if os.path.isdir(p):
                if low == "mods" or low == "dlc":
                    # container dirs: their children are the real items
                    for child in os.listdir(p):
                        cp = os.path.join(p, child)
                        if os.path.isdir(cp):
                            if low == "mods":
                                mod_dirs.append(cp)
                            else:
                                dlc_dirs.append(cp)
                    continue
                if classify_dir(p, e):
                    continue
                if low == "bin":
                    continue  # handled by the xml sweep below
                if depth < 2:
                    scan_level(p, depth + 1)
            elif low.endswith(".xml"):
                # menu xmls also ship loose or under bin/.../pc
                if "user_config_matrix" in p.replace(os.sep, "/").lower():
                    menu_xmls.append(p)

    scan_level(scratch, 0)
    # xml sweep for the canonical bin path anywhere in the tree
    for root, _dirs, names in os.walk(scratch):
        if "user_config_matrix" in root.replace(os.sep, "/").lower():
            for n in names:
                if n.lower().endswith(".xml"):
                    p = os.path.join(root, n)
                    if p not in menu_xmls:
                        menu_xmls.append(p)

    if not mod_dirs and not dlc_dirs:
        # Loose content/ at root: wrap as a mod folder.
        if os.path.isdir(os.path.join(scratch, "content")):
            wrap = os.path.join(scratch, "mod" + _safe_name(mod_name))
            os.makedirs(wrap, exist_ok=True)
            shutil.move(
                os.path.join(scratch, "content"),
                os.path.join(wrap, "content"),
            )
            mod_dirs.append(wrap)
        else:
            return [], [], menu_xmls, (
                "No Witcher 3 mod layout found in this archive (expected "
                "mod*/dlc* folders or a content/ folder)"
            )

    # Script-conflict gate against everything already installed.
    owners = _w3_installed_scripts(mods_path)
    for d in mod_dirs:
        for rel in _w3_payload_scripts(d):
            owner = owners.get(rel)
            if owner and owner.lower() != os.path.basename(d).lower():
                return [], [], [], (
                    f"Script conflict: this mod and '{owner}' both edit "
                    f"scripts/{rel}. Merging scripts needs Script Merger "
                    "(Windows-only) - not supported on Steam Deck yet."
                )
    return mod_dirs, dlc_dirs, menu_xmls, None


# ---- Bannerlord module activation ------------------------------------------
# Modules are folders under Modules/, but the launcher only loads ones
# selected in LauncherData.xml (Documents/Mount and Blade II Bannerlord/
# Configs/). The Id comes from each module's SubModule.xml - NOT the folder
# name. Vortex manages the same file, so the shape is battle-tested.


def _submodule_id(module_dir: str):
    """The module Id from SubModule.xml ('<Id value="X"/>' style)."""
    path = os.path.join(module_dir, "SubModule.xml")
    if not os.path.isfile(path):
        return None
    try:
        root = xml_parse_file(path)
        node = root.find("Id")
        if node is None:
            return None
        return node.get("value") or (node.text or "").strip() or None
    except Exception:  # noqa: BLE001 - malformed community XML
        return None


def _launcher_xml_path(app_id: int, subpath: str) -> str:
    return _adopt_case(
        _prefix_user_path(app_id, "Documents", *subpath.split("/"))
    )


def _set_module_selected(path: str, module_id: str, selected: bool) -> bool:
    """Set (or append) a module's IsSelected in LauncherData.xml. Best
    effort: returns False when the file doesn't exist yet (launcher never
    run) - the launcher also auto-detects modules, so this is convenience,
    not correctness."""
    if not os.path.isfile(path):
        return False
    try:
        root = xml_parse_file(path)
        parent = None
        for node in root.iter():
            for child in node:
                if child.tag == "UserModData":
                    parent = node
                    break
            if parent is not None:
                break
        for entry in root.iter("UserModData"):
            id_node = entry.find("Id")
            if id_node is not None and (id_node.text or "").strip() == module_id:
                sel = entry.find("IsSelected")
                if sel is None:
                    sel = XmlNode("IsSelected")
                    entry.append(sel)
                sel.text = "true" if selected else "false"
                xml_write_file(path, root)
                return True
        if parent is None:
            # No entries yet: use a ModDatas container if one exists.
            parent = next(
                (n for n in root.iter() if n.tag == "ModDatas"), None
            )
        if parent is None:
            return False
        entry = XmlNode("UserModData")
        id_node = XmlNode("Id")
        id_node.text = module_id
        entry.append(id_node)
        sel = XmlNode("IsSelected")
        sel.text = "true" if selected else "false"
        entry.append(sel)
        parent.append(entry)
        xml_write_file(path, root)
        return True
    except Exception:  # noqa: BLE001
        return False


def _remove_module_entry(path: str, module_id: str) -> None:
    if not os.path.isfile(path):
        return
    try:
        root = xml_parse_file(path)
        for node in root.iter():
            for child in list(node):
                if child.tag == "UserModData":
                    id_node = child.find("Id")
                    if (
                        id_node is not None
                        and (id_node.text or "").strip() == module_id
                    ):
                        node.remove(child)
        xml_write_file(path, root)
    except Exception:  # noqa: BLE001
        pass



# ---- FOMOD installers ---------------------------------------------------------
# The Bethesda-ecosystem install wizard: fomod/ModuleConfig.xml describes
# steps -> groups -> options with condition flags and conditional file
# installs. We parse it into a JSON wizard for the frontend, keep the
# extracted archive pending, and apply the user's selections through the
# normal dataDir merge. Spec: FOMOD ModuleConfig 5.0 (as implemented by
# Vortex/MO2); images and edge-case dependency types are v1-simplified.

PENDING_FOMODS: dict = {}
FOMOD_TTL_SECONDS = 30 * 60


def _fomod_config_path(scratch: str):
    for root, dirs, names in os.walk(scratch):
        for n in names:
            if n.lower() == "moduleconfig.xml" and os.path.basename(
                root
            ).lower() == "fomod":
                return os.path.join(root, n)
    return None


def _fomod_norm_source(path: str) -> str:
    return (path or "").replace("\\", "/").strip("/")


def _fomod_case_resolve(base: str, rel: str):
    """Resolve an XML source path against the archive case-insensitively
    (authors write Windows-cased paths)."""
    cur = base
    for part in _fomod_norm_source(rel).split("/"):
        if not part:
            continue
        try:
            entries = os.listdir(cur)
        except OSError:
            return None
        match = next((e for e in entries if e.lower() == part.lower()), None)
        if match is None:
            return None
        cur = os.path.join(cur, match)
    return cur


def _fomod_parse_files(node) -> list:
    """<files> node -> [{kind, source, dest, priority}]"""
    out = []
    if node is None:
        return out
    for child in node:
        tag = child.tag.lower()
        if tag not in ("file", "folder"):
            continue
        out.append(
            {
                "kind": tag,
                "source": child.get("source") or "",
                "dest": child.get("destination"),
                "priority": int(child.get("priority") or 0),
            }
        )
    return out


def _fomod_parse_deps(node, data_path: str):
    """Composite dependency -> a tree the frontend can evaluate with flag
    state only: file/game dependencies are baked to constants here."""
    if node is None:
        return None
    conds = []
    for child in node:
        tag = child.tag.lower()
        if tag == "flagdependency":
            conds.append(
                {
                    "kind": "flag",
                    "name": child.get("flag") or "",
                    "value": child.get("value") or "",
                }
            )
        elif tag == "filedependency":
            want = (child.get("state") or "Active").lower()
            fname = child.get("file") or ""
            exists = os.path.exists(os.path.join(data_path, fname))
            ok = exists if want in ("active", "inactive") else not exists
            conds.append({"kind": "const", "value": bool(ok)})
        elif tag == "dependencies":
            sub = _fomod_parse_deps(child, data_path)
            if sub:
                conds.append(sub)
        else:
            # gameDependency / fommDependency: assume satisfied
            conds.append({"kind": "const", "value": True})
    return {
        "kind": "group",
        "op": (node.get("operator") or "And").lower(),
        "conds": conds,
    }


def _fomod_eval_deps(tree, flags: dict) -> bool:
    if tree is None:
        return True
    kind = tree.get("kind")
    if kind == "const":
        return bool(tree.get("value"))
    if kind == "flag":
        return flags.get(tree.get("name") or "") == (tree.get("value") or "")
    conds = tree.get("conds") or []
    results = [_fomod_eval_deps(c, flags) for c in conds]
    if not results:
        return True
    return any(results) if tree.get("op") == "or" else all(results)


def _fomod_plugin_type(plugin_node, data_path: str) -> str:
    td = plugin_node.find("typeDescriptor")
    if td is None:
        return "Optional"
    t = td.find("type")
    if t is not None:
        return t.get("name") or "Optional"
    dt = td.find("dependencyType")
    if dt is not None:
        # Evaluate patterns whose deps are already decidable (file/game
        # baked); flag-dependent patterns fall back to the default type.
        default = "Optional"
        d = dt.find("defaultType")
        if d is not None:
            default = d.get("name") or "Optional"
        patterns = dt.find("patterns")
        if patterns is not None:
            for pat in patterns.findall("pattern"):
                deps = _fomod_parse_deps(pat.find("dependencies"), data_path)

                def has_flags(tree) -> bool:
                    if not tree:
                        return False
                    if tree.get("kind") == "flag":
                        return True
                    return any(
                        has_flags(c) for c in tree.get("conds") or []
                    )

                if deps and not has_flags(deps) and _fomod_eval_deps(deps, {}):
                    t2 = pat.find("type")
                    if t2 is not None:
                        return t2.get("name") or default
        return default
    return "Optional"


def _parse_fomod(scratch: str, data_path: str):
    """ModuleConfig.xml -> (wizard dict for the frontend, applier context).
    Returns None when the archive has no parsable FOMOD config."""
    cfg = _fomod_config_path(scratch)
    if not cfg:
        return None
    try:
        root = xml_parse_file(cfg)
    except Exception:  # noqa: BLE001 - malformed community XML
        return None
    fomod_base = os.path.dirname(os.path.dirname(cfg))

    name_node = root.find("moduleName")
    module_name = (
        (name_node.text or "").strip() if name_node is not None else ""
    )

    required = _fomod_parse_files(root.find("requiredInstallFiles"))

    steps = []
    steps_node = root.find("installSteps")
    plugin_index = {}
    if steps_node is not None:
        for si, step in enumerate(steps_node.findall("installStep")):
            groups = []
            ofg = step.find("optionalFileGroups")
            if ofg is not None:
                for gi, group in enumerate(ofg.findall("group")):
                    plugins = []
                    plugins_node = group.find("plugins")
                    if plugins_node is not None:
                        for pi, plugin in enumerate(
                            plugins_node.findall("plugin")
                        ):
                            pid = f"{si}.{gi}.{pi}"
                            desc = plugin.find("description")
                            flags = {}
                            cf = plugin.find("conditionFlags")
                            if cf is not None:
                                for fl in cf.findall("flag"):
                                    flags[fl.get("name") or ""] = (
                                        fl.text or ""
                                    ).strip()
                            files = _fomod_parse_files(plugin.find("files"))
                            ptype = _fomod_plugin_type(plugin, data_path)
                            plugin_index[pid] = {
                                "files": files,
                                "flags": flags,
                            }
                            plugins.append(
                                {
                                    "id": pid,
                                    "name": plugin.get("name") or f"Option {pi + 1}",
                                    "description": (
                                        (desc.text or "").strip()
                                        if desc is not None
                                        else ""
                                    ),
                                    "type": ptype,
                                    "flags": flags,
                                }
                            )
                    groups.append(
                        {
                            "name": group.get("name") or "",
                            "type": group.get("type") or "SelectAny",
                            "plugins": plugins,
                        }
                    )
            steps.append(
                {
                    "name": step.get("name") or f"Step {si + 1}",
                    "visible": _fomod_parse_deps(
                        step.find("visible"), data_path
                    ),
                    "groups": groups,
                }
            )

    conditional = []
    cfi = root.find("conditionalFileInstalls")
    if cfi is not None:
        patterns = cfi.find("patterns")
        if patterns is not None:
            for pat in patterns.findall("pattern"):
                conditional.append(
                    {
                        "deps": _fomod_parse_deps(
                            pat.find("dependencies"), data_path
                        ),
                        "files": _fomod_parse_files(pat.find("files")),
                    }
                )

    wizard = {"moduleName": module_name, "steps": steps}
    ctx = {
        "fomod_base": fomod_base,
        "required": required,
        "conditional": conditional,
        "plugin_index": plugin_index,
        "steps": steps,
    }
    return wizard, ctx


def _fomod_selected_files(ctx: dict, selected_ids: list):
    """The user's selections -> ordered file operations (priority applied:
    ascending, later overwrites) + the final flag state."""
    flags = {}
    ops = list(ctx["required"])
    for pid in selected_ids:
        entry = ctx["plugin_index"].get(pid)
        if not entry:
            continue
        ops.extend(entry["files"])
        flags.update(entry["flags"])
    for pattern in ctx["conditional"]:
        if _fomod_eval_deps(pattern["deps"], flags):
            ops.extend(pattern["files"])
    ops.sort(key=lambda o: o.get("priority") or 0)
    return ops, flags


def _fomod_stage(ctx: dict, selected_ids: list, staging: str) -> int:
    """Copy the selected sources into a staging dir shaped like a Data
    payload; returns the number of files staged."""
    ops, _flags = _fomod_selected_files(ctx, selected_ids)
    base = ctx["fomod_base"]
    count = 0
    for op in ops:
        src = _fomod_case_resolve(base, op["source"])
        if src is None:
            decky.logger.info(f"fomod: source missing: {op['source']!r}")
            continue
        dest = op["dest"]
        dest = _fomod_norm_source(
            dest if dest is not None else op["source"]
        )
        if op["kind"] == "folder" or os.path.isdir(src):
            for root, _dirs, names in os.walk(src):
                for n in names:
                    rel = os.path.relpath(os.path.join(root, n), src)
                    rel = rel.replace(os.sep, "/")
                    target_rel = f"{dest}/{rel}" if dest else rel
                    if not _safe_rel_path(target_rel):
                        continue
                    dst = os.path.join(staging, *target_rel.split("/"))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if os.path.isfile(dst):
                        os.remove(dst)
                    shutil.copy2(os.path.join(root, n), dst)
                    count += 1
        else:
            target_rel = dest or os.path.basename(src)
            if not _safe_rel_path(target_rel):
                continue
            dst = os.path.join(staging, *target_rel.split("/"))
            os.makedirs(os.path.dirname(dst) or staging, exist_ok=True)
            if os.path.isfile(dst):
                os.remove(dst)
            shutil.copy2(src, dst)
            count += 1
    return count


def _match_fomod_choices(steps: list, curator_choices) -> list:
    """Map curator FOMOD choices (Vortex manifest shape: nested dicts with
    group names and selected option-name lists) onto our wizard's plugin
    ids. Groups without curator data fall back to defaults (Required +
    Recommended, or the first option of a pick-one group). Name matching
    is normalized - manifests and ModuleConfigs disagree on punctuation."""

    def norm(t: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (t or "").lower())

    # Collect group-name -> set of selected option names from whatever
    # structure the manifest uses (defensively walked).
    selected_by_group: dict = {}
    loose_selected: set = set()

    def walk(node):
        if isinstance(node, dict):
            gname = node.get("name") or node.get("group")
            opts = node.get("choices") or node.get("options")
            if gname and isinstance(opts, list) and all(
                isinstance(o, str) for o in opts
            ):
                selected_by_group.setdefault(norm(str(gname)), set()).update(
                    norm(o) for o in opts
                )
            elif (
                gname
                and isinstance(node.get("idx"), (int, str))
                or (gname and node.get("selected") is True)
            ):
                loose_selected.add(norm(str(gname)))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(curator_choices)

    ids = []
    for step in steps:
        for group in step.get("groups") or []:
            plugins = group.get("plugins") or []
            gkey = norm(group.get("name") or "")
            curated = selected_by_group.get(gkey)
            picked = []
            for plugin in plugins:
                pkey = norm(plugin.get("name") or "")
                if plugin.get("type") == "Required":
                    picked.append(plugin["id"])
                elif curated is not None and pkey in curated:
                    picked.append(plugin["id"])
                elif curated is None and pkey in loose_selected:
                    picked.append(plugin["id"])
            if not picked and curated is None:
                # No curator data for this group: defaults.
                if group.get("type") == "SelectAll":
                    picked = [p["id"] for p in plugins]
                else:
                    recommended = [
                        p["id"]
                        for p in plugins
                        if p.get("type") == "Recommended"
                    ]
                    picked = recommended
                    if not picked and group.get("type") in (
                        "SelectExactlyOne",
                        "SelectAtLeastOne",
                    ) and plugins:
                        picked = [plugins[0]["id"]]
            ids.extend(picked)
    return ids


def _prune_pending_fomods() -> None:
    now = time.time()
    for token in list(PENDING_FOMODS):
        if now - PENDING_FOMODS[token].get("at", 0) > FOMOD_TTL_SECONDS:
            entry = PENDING_FOMODS.pop(token)
            _force_rmtree(entry.get("scratch") or "")


def _parse_nxm_url(url: str):
    """Strictly parse an nxm:// mod-file link (the website's 'Slow download' /
    'Mod Manager Download' handoff). Returns None for anything else -
    including nxm://oauth callbacks and malformed/hostile input."""
    try:
        parts = urllib.parse.urlsplit((url or "").strip())
    except ValueError:
        return None
    if parts.scheme != "nxm":
        return None
    domain = parts.netloc.lower()
    if not re.fullmatch(r"[a-z0-9_-]+", domain):
        return None
    m = re.fullmatch(r"/mods/(\d+)/files/(\d+)", parts.path)
    if not m:
        return None
    query = urllib.parse.parse_qs(parts.query)

    def q(name):
        values = query.get(name) or [""]
        return values[0]

    return {
        "game_domain": domain,
        "mod_id": int(m.group(1)),
        "file_id": int(m.group(2)),
        "key": q("key"),
        "expires": q("expires"),
        "user_id": q("user_id"),
    }


def _norm_mod_id(mod_id: str) -> str:
    """Log display names and folder names differ in spaces/dashes/case -
    'CJB Cheats Menu' vs folder 'CJBCheatsMenu'. Normalize both sides."""
    return re.sub(r"[^a-z0-9]", "", mod_id.lower())


def _parse_smapi_log(lines: list):
    """Parse SMAPI-latest.txt into per-mod load outcomes (format verified on
    device, SMAPI 4.5.2). Loaded mods appear under 'Loaded N mods:' as
    '   <Name> <version> by <author> | <summary>'; skipped mods appear as
    '   - <Name> <version> because <reason>'."""
    loaded: set = set()
    errors: dict = {}
    modded_session = False
    in_loaded = False
    for raw in lines:
        # strip the '[HH:MM:SS LEVEL Source] ' prefix
        msg = re.sub(r"^\[[^\]]*\]\s?", "", raw.rstrip())
        if re.match(r"Loaded \d+ mods:", msg):
            modded_session = True
            in_loaded = True
            continue
        if in_loaded:
            m = re.match(r"\s{2,}(.+?)\s+\d[^\s]*\s+by\s+", msg)
            if m:
                loaded.add(_norm_mod_id(m.group(1)))
                continue
            in_loaded = False
        m = re.match(r"\s*-\s+(.+?)(?:\s+\d[^\s]*)?\s+because\s+(.+)$", msg)
        if m:
            errors[_norm_mod_id(m.group(1))] = m.group(2)[:160]

    status = {key: {"state": "loaded", "detail": ""} for key in loaded}
    for key, detail in errors.items():
        status[key] = {"state": "error", "detail": detail}
    return status, modded_session


def _smapi_log_path(config_dir_name: str) -> str:
    return os.path.join(
        decky.DECKY_USER_HOME, ".config", config_dir_name,
        "ErrorLogs", "SMAPI-latest.txt",
    )


def _parse_mod_load_log(lines: list):
    """Turn a game session log into per-mod load outcomes. Returns
    (status dict keyed by normalized mod id, modded_session bool)."""

    def norm(mod_id: str) -> str:
        # log tags sometimes differ from manifest ids in dash/underscore
        return re.sub(r"[^a-z0-9]", "", mod_id.lower())

    loaded: set = set()
    errors: dict = {}
    modded_session = False
    for line in lines:
        if "RUNNING MODDED" in line:
            modded_session = True
            continue
        m = re.search(r"Finished mod initialization for '.*' \(([^)]+)\)", line)
        if m:
            loaded.add(norm(m.group(1)))
            continue
        m = re.search(r"Tried to load mod with id (\S+?),", line)
        if m:
            errors.setdefault(norm(m.group(1)), "duplicate mod id")
            continue
        m = re.match(r"\[ERROR\] \[([^\]]+)\] (.*)", line)
        if m:
            errors.setdefault(norm(m.group(1)), m.group(2)[:160])

    status = {key: {"state": "loaded", "detail": ""} for key in loaded}
    for key, detail in errors.items():
        status[key] = {"state": "error", "detail": detail}
    return status, modded_session


async def _is_process_running(name: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "pgrep",
            "-x",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return (await proc.wait()) == 0
    except OSError:
        return False


def _newest_mtime(path: str) -> float:
    newest = 0.0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, name)))
            except OSError:
                pass
    return newest


def _save_layout(account_id: str, app_id: int):
    """StS2 keeps vanilla saves in remote/profileN/ and modded saves in a
    mirrored remote/modded/profileN/ tree (verified on device)."""
    remote = os.path.join(STEAM_USERDATA, account_id, str(app_id), "remote")
    profiles = []
    if os.path.isdir(remote):
        profiles = sorted(
            d
            for d in os.listdir(remote)
            if re.fullmatch(r"profile\d+", d)
            and os.path.isdir(os.path.join(remote, d))
        )
    return remote, profiles, os.path.join(remote, "modded")


async def _emit_progress(mod_id: int, phase: str, percent: int, message: str = ""):
    await decky.emit(
        "install_progress",
        {"mod_id": mod_id, "phase": phase, "percent": percent, "message": message},
    )


async def _validate_key(api_key: str) -> dict:
    """Ask Nexus who this key belongs to. Doubles as our 'login' check.
    (This endpoint does not consume API rate-limit quota.)"""
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.get(
                f"{NEXUS_API_BASE}/v1/users/validate.json",
                headers=_api_headers(api_key),
                ssl=SSL_CONTEXT,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "ok": True,
                        "name": data.get("name"),
                        "user_id": data.get("user_id"),
                        "is_premium": bool(data.get("is_premium")),
                    }
                if resp.status == 401:
                    return {"ok": False, "error": "Invalid API key"}
                return {"ok": False, "error": f"Nexus Mods API error (HTTP {resp.status})"}
    except aiohttp.ClientError as e:
        return {"ok": False, "error": f"Network error: {type(e).__name__}"}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Nexus Mods API timed out"}


async def _extract_archive(archive_path: str, dest_dir: str) -> str:
    """Extract with bsdtar (handles zip/7z/rar on SteamOS); fall back to
    Python's zipfile for .zip if bsdtar is missing. Returns '' on success,
    error text otherwise."""
    if shutil.which("bsdtar"):
        proc = await asyncio.create_subprocess_exec(
            "bsdtar",
            "-xf",
            archive_path,
            "-C",
            dest_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            return err.decode(errors="replace")[:300] or "bsdtar failed"
        return ""
    if archive_path.lower().endswith(".zip"):
        import zipfile

        try:
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(dest_dir)
            return ""
        except Exception as e:  # noqa: BLE001 - report to UI
            return f"zip extraction failed: {e}"
    return "no extractor available for this archive type"


class Plugin:
    # ---- Nexus account -----------------------------------------------------

    async def set_api_key(self, api_key: str) -> dict:
        """Validate a key against the Nexus API; persist it only if valid.
        An empty string clears the stored key."""
        api_key = (api_key or "").strip()
        settings = _load_settings()
        if not api_key:
            settings.pop("api_key", None)
            _save_settings(settings)
            decky.logger.info("API key cleared")
            return {"ok": False, "cleared": True, "error": "No API key set"}
        result = await _validate_key(api_key)
        if result.get("ok"):
            settings["api_key"] = api_key
            _save_settings(settings)
            decky.logger.info(
                f"API key saved for user {result.get('name')} "
                f"(premium={result.get('is_premium')})"
            )
        else:
            decky.logger.warning(f"API key rejected: {result.get('error')}")
        return result

    async def get_auth_status(self) -> dict:
        api_key = _load_settings().get("api_key")
        if not api_key:
            return {"ok": False, "error": "No API key set"}
        return await _validate_key(api_key)

    # ---- Mod browsing (GraphQL v2) ------------------------------------------

    async def get_mods(
        self,
        game_domain: str,
        sort: str = "endorsements",
        count: int = 10,
        offset: int = 0,
        search: str = "",
    ) -> dict:
        """Browse or search a game's mods, sorted server-side. Public data -
        works without a key, but we send it when present."""
        if sort not in SORT_FIELDS:
            return {"ok": False, "error": f"Unknown sort {sort!r}"}
        search = (search or "").strip()
        trending_since = (
            int(time.time()) - TRENDING_WINDOW_DAYS * 86400
            if sort == "trending"
            else None
        )
        variables = {
            "domain": game_domain,
            "count": count,
            "offset": offset,
        }
        if trending_since is None:
            variables["sort"] = [{sort: {"direction": "DESC"}}]
        if search:
            variables["search"] = search
        payload = {
            "query": _build_mods_query(
                bool(search), trending_since, include_adult=_show_adult()
            ),
            "variables": variables,
        }
        headers = {
            **_api_headers(_load_settings().get("api_key")),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            ) as session:
                async with session.post(
                    NEXUS_V2_GRAPHQL, json=payload, headers=headers, ssl=SSL_CONTEXT
                ) as resp:
                    if resp.status != 200:
                        return {
                            "ok": False,
                            "error": f"Nexus Mods API error (HTTP {resp.status})",
                        }
                    body = await resp.json()
        except aiohttp.ClientError as e:
            return {"ok": False, "error": f"Network error: {type(e).__name__}"}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Nexus Mods API timed out"}

        if body.get("errors"):
            msg = body["errors"][0].get("message", "unknown GraphQL error")
            decky.logger.warning(f"get_mods GraphQL error: {msg}")
            return {"ok": False, "error": f"Nexus Mods query error: {msg}"}

        page = body["data"]["mods"]
        # Adult-content filtering happens here for now; make it a setting later.
        mods = [m for m in page["nodes"] if not m.get("adultContent")]
        decky.logger.info(
            f"get_mods({game_domain!r}, sort={sort}): "
            f"{len(mods)}/{page['nodesCount']} mods returned"
        )
        return {"ok": True, "total": page["nodesCount"], "mods": mods}

    async def get_endorsement(self, game_domain: str, mod_id: int) -> dict:
        """The signed-in user's endorsement state for a mod. The v1
        single-mod endpoint reports it when authenticated."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        api_key = _load_settings().get("api_key")
        if not api_key:
            return {"ok": True, "status": "unknown"}
        url = f"{NEXUS_API_BASE}/v1/games/{game_domain}/mods/{int(mod_id)}.json"
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.get(
                    url, headers=_api_headers(api_key), ssl=SSL_CONTEXT
                ) as resp:
                    if resp.status != 200:
                        return {"ok": True, "status": "unknown"}
                    body = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return {"ok": True, "status": "unknown"}
        endorsement = body.get("endorsement") or {}
        return {"ok": True, "status": endorsement.get("endorse_status") or "Undecided"}

    async def set_endorsement(
        self, game_domain: str, mod_id: int, version: str, endorse: bool
    ) -> dict:
        """Endorse or abstain. Nexus Mods enforces its own rules (must have
        downloaded the mod, a cool-down after downloading, not your own mod)
        - map those to friendly messages."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        api_key = _load_settings().get("api_key")
        if not api_key:
            return {"ok": False, "error": "Not signed in"}
        action = "endorse" if endorse else "abstain"
        url = (
            f"{NEXUS_API_BASE}/v1/games/{game_domain}/mods/{int(mod_id)}"
            f"/{action}.json"
        )
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.post(
                    url,
                    headers=_api_headers(api_key),
                    json={"version": version or "1"},
                    ssl=SSL_CONTEXT,
                ) as resp:
                    try:
                        body = await resp.json()
                    except Exception:  # noqa: BLE001 - non-JSON error body
                        body = {}
                    if resp.status == 200:
                        return {
                            "ok": True,
                            "status": "Endorsed" if endorse else "Abstained",
                        }
                    message = str(body.get("message") or body.get("error") or "")
                    friendly = {
                        "NOT_DOWNLOADED_MOD": "You can only endorse mods you've downloaded",
                        "TOO_SOON_AFTER_DOWNLOAD": "Wait 15 minutes after downloading to endorse",
                        "IS_OWN_MOD": "You can't endorse your own mod",
                    }
                    for code, text in friendly.items():
                        if code in message:
                            return {"ok": False, "error": text}
                    return {
                        "ok": False,
                        "error": message or f"Nexus Mods API error (HTTP {resp.status})",
                    }
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return {"ok": False, "error": f"Network error: {type(e).__name__}"}

    async def get_mod_requirements(self, game_domain: str, mod_id: int) -> dict:
        """Nexus-listed requirements for a mod (public v2 data). Two-step:
        resolve the numeric game id once, then query via legacyMods."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        api_key = _load_settings().get("api_key")
        try:
            game_id = await _resolve_game_id(game_domain, api_key)
            data = await _gql_query(
                "{ legacyMods(ids: [{gameId: %d, modId: %d}]) { nodes { "
                "modRequirements { nexusRequirements { nodes "
                "{ modName modId notes url } } } } } }"
                % (game_id, int(mod_id)),
                api_key,
            )
            nodes = data["legacyMods"]["nodes"]
            raw = (
                nodes[0]["modRequirements"]["nexusRequirements"]["nodes"]
                if nodes
                else []
            )
            return {"ok": True, "requirements": _normalize_requirements(raw)}
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, KeyError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ---- Collections ---------------------------------------------------------
    # Curated mod lists with pinned file ids. Queries mirror the Nexus labs
    # collections downloader (verified field names); installs run through
    # the normal per-game pipeline one file at a time, in collection order.

    async def get_collections(
        self,
        game_domain: str,
        count: int = 8,
        search: str = "",
        sort: str = "endorsements",
        offset: int = 0,
    ) -> dict:
        """Most-endorsed published collections for a game, optionally
        name-filtered (the search toggle on the browse page)."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        api_key = _load_settings().get("api_key")
        search_filter = (
            "generalSearch: [{ value: $search, op: WILDCARD }]"
            if search
            else ""
        )
        search_param = ", $search: String!" if search else ""
        query = """
query TrendingCollections($gameDomain: String!, $count: Int, $offset: Int%SEARCHPARAM%) {
  collectionsV2(
    filter: {
      gameDomain: [{ value: $gameDomain }]
      hasPublishedRevision: [{ value: true }]
      %SEARCH%
      %ADULT%
    }
    sort: [{ %SORT%: { direction: DESC } }]
    count: $count
    offset: $offset
  ) {
    nodes {
      name
      slug
      summary
      endorsements
      tileImage { thumbnailUrl(size: small) }
      user { name }
      latestPublishedRevision { modCount totalSize }
    }
  }
}"""
        sort_field = _collection_sort_field(sort)
        query = (
            query.replace("%SEARCHPARAM%", search_param)
            .replace("%SEARCH%", search_filter)
            .replace("%SORT%", sort_field)
            .replace(
                "%ADULT%",
                "" if _show_adult() else "adultContent: [{ value: false }]",
            )
        )
        variables = {
            "gameDomain": game_domain,
            "count": int(count),
            "offset": int(offset),
        }
        if search:
            variables["search"] = search
        try:
            data = await _gql_query_vars(query, variables, api_key)
            out = []
            for n in data["collectionsV2"]["nodes"]:
                rev = n.get("latestPublishedRevision") or {}
                out.append(
                    {
                        "name": n.get("name") or "",
                        "slug": n.get("slug") or "",
                        "summary": n.get("summary") or "",
                        "endorsements": n.get("endorsements") or 0,
                        "author": (n.get("user") or {}).get("name") or "",
                        "thumbnailUrl": (n.get("tileImage") or {}).get(
                            "thumbnailUrl"
                        ),
                        "modCount": rev.get("modCount") or 0,
                        "totalSize": int(rev.get("totalSize") or 0),
                    }
                )
            decky.logger.info(
                f"get_collections({game_domain!r}): {len(out)} returned"
            )
            return {"ok": True, "collections": out}
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, KeyError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def get_collection(self, slug: str, game_domain: str) -> dict:
        """A collection's latest revision: ordered, pinned mod files."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug or ""):
            return {"ok": False, "error": "Invalid collection slug"}
        api_key = _load_settings().get("api_key")
        query = """
query GetCollection($slug: String!, $domainName: String!) {
  collectionRevision(slug: $slug, domainName: $domainName) {
    revisionNumber
    modCount
    totalSize
    collection { name summary user { name } }
    modFiles {
      fileId
      optional
      file {
        fileId
        modId
        name
        version
        sizeInBytes
        mod { name }
      }
    }
    externalResources { name resourceType resourceUrl optional }
  }
}"""
        try:
            data = await _gql_query_vars(
                query, {"slug": slug, "domainName": game_domain}, api_key
            )
            rev = data["collectionRevision"]
            coll = rev.get("collection") or {}
            files = []
            for mf in rev.get("modFiles") or []:
                f = mf.get("file") or {}
                if not f.get("modId") or not f.get("fileId"):
                    continue
                files.append(
                    {
                        "modId": int(f["modId"]),
                        "fileId": int(f["fileId"]),
                        "modName": (f.get("mod") or {}).get("name")
                        or f.get("name")
                        or "",
                        "fileName": f.get("name") or "",
                        "version": f.get("version") or "",
                        "sizeKb": int(int(f.get("sizeInBytes") or 0) / 1024),
                        "optional": bool(mf.get("optional")),
                    }
                )
            externals = [
                {
                    "name": r.get("name") or "",
                    "url": r.get("resourceUrl") or "",
                    "optional": bool(r.get("optional")),
                }
                for r in rev.get("externalResources") or []
            ]
            decky.logger.info(
                f"get_collection({slug!r}): {len(files)} files, "
                f"{len(externals)} external"
            )
            return {
                "ok": True,
                "collection": {
                    "name": coll.get("name") or slug,
                    "summary": coll.get("summary") or "",
                    "author": (coll.get("user") or {}).get("name") or "",
                    "revision": rev.get("revisionNumber"),
                    "modCount": rev.get("modCount") or len(files),
                    "totalSize": int(rev.get("totalSize") or 0),
                    "files": files,
                    "externals": externals,
                },
            }
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, KeyError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def get_collection_manifest(
        self, slug: str, game_domain: str
    ) -> dict:
        """Download the collection's own manifest (collection.json inside
        the revision archive) and return per-file FOMOD choices - the
        curator's wizard selections, so collection installs can run FOMODs
        hands-off."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug or ""):
            return {"ok": False, "error": "Invalid collection slug"}
        api_key = _load_settings().get("api_key")
        try:
            data = await _gql_query_vars(
                """
query Link($slug: String!, $domainName: String!) {
  collectionRevision(slug: $slug, domainName: $domainName) { downloadLink }
}""",
                {"slug": slug, "domainName": game_domain},
                api_key,
            )
            link_path = data["collectionRevision"]["downloadLink"]
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120)
            ) as session:
                async with session.get(
                    f"{NEXUS_API_BASE}{link_path}",
                    headers=_api_headers(api_key),
                    ssl=SSL_CONTEXT,
                ) as resp:
                    if resp.status != 200:
                        return {
                            "ok": False,
                            "error": f"Manifest link HTTP {resp.status}",
                        }
                    body = await resp.json()
                uri = None
                if isinstance(body, dict):
                    uri = body.get("download_url") or body.get("uri")
                    if not uri and isinstance(body.get("download_links"), list):
                        links = body["download_links"]
                        uri = links[0].get("URI") if links else None
                elif isinstance(body, list) and body:
                    uri = body[0].get("URI")
                if not uri:
                    return {"ok": False, "error": "No manifest download link"}
                os.makedirs(DOWNLOADS_DIR, exist_ok=True)
                arc = os.path.join(DOWNLOADS_DIR, f"collection-{slug}.arc")
                async with session.get(
                    uri.replace(" ", "%20"), ssl=SSL_CONTEXT
                ) as resp:
                    if resp.status != 200:
                        return {
                            "ok": False,
                            "error": f"Manifest download HTTP {resp.status}",
                        }
                    with open(arc, "wb") as out:
                        out.write(await resp.read())
            scratch = os.path.join(DOWNLOADS_DIR, f"collection-{slug}")
            _force_rmtree(scratch)
            os.makedirs(scratch)
            err = await _extract_archive(arc, scratch)
            try:
                os.remove(arc)
            except OSError:
                pass
            if err:
                _force_rmtree(scratch)
                return {"ok": False, "error": err}
            manifest_path = None
            for root, _dirs, names in os.walk(scratch):
                for n in names:
                    if n.lower() == "collection.json":
                        manifest_path = os.path.join(root, n)
                        break
            if not manifest_path:
                _force_rmtree(scratch)
                return {"ok": False, "error": "collection.json not found"}
            with open(manifest_path, "r", encoding="utf-8-sig") as f:
                manifest = json.load(f)
            _force_rmtree(scratch)
            choices = {}
            for mod in manifest.get("mods") or []:
                source = mod.get("source") or {}
                file_id = source.get("fileId")
                mod_choices = mod.get("choices")
                if file_id and mod_choices:
                    choices[str(file_id)] = mod_choices
            decky.logger.info(
                f"collection manifest {slug!r}: {len(choices)} mods carry "
                f"installer choices"
            )
            return {"ok": True, "choices": choices}
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, KeyError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def get_mod_details(self, game_domain: str, mod_id: int) -> dict:
        """Single mod with full description - used by the detail page and for
        opening a required mod's page from a requirement chip."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        api_key = _load_settings().get("api_key")
        try:
            game_id = await _resolve_game_id(game_domain, api_key)
            data = await _gql_query(
                "{ legacyMods(ids: [{gameId: %d, modId: %d}]) { nodes {%s\n"
                " description uploader { name memberId donationsEnabled } } } }"
                % (game_id, int(mod_id), MOD_FIELDS),
                api_key,
            )
            nodes = data["legacyMods"]["nodes"]
            if not nodes:
                return {"ok": False, "error": "Mod not found"}
            return {"ok": True, "mod": nodes[0]}
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, KeyError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def check_updates(self, game_domain: str) -> dict:
        """Compare installed (tracked) mod versions against current Nexus
        versions. Version strings in the wild are messy, so 'update available'
        means 'differs from what we installed', normalized for a leading v."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        records = _load_settings().get("installed", {}).get(game_domain, {})
        tracked = {
            folder: rec for folder, rec in records.items() if rec.get("mod_id")
        }
        if not tracked:
            return {"ok": True, "updates": {}}
        api_key = _load_settings().get("api_key")
        try:
            game_id = await _resolve_game_id(game_domain, api_key)
            ids = ", ".join(
                "{gameId: %d, modId: %d}" % (game_id, int(rec["mod_id"]))
                for rec in tracked.values()
            )
            data = await _gql_query(
                "{ legacyMods(ids: [%s]) { nodes { modId version } } }" % ids,
                api_key,
            )
            current = {n["modId"]: n for n in data["legacyMods"]["nodes"]}
            updates = {}
            for folder, rec in tracked.items():
                node = current.get(rec["mod_id"])
                if not node:
                    continue
                # Collection installs are pinned by the curator - nagging
                # users to update them off-plan does more harm than good.
                if rec.get("source") == "collection":
                    continue
                cur = _norm_version(node.get("version"))
                # Compare in page-version units when we recorded them - the
                # FILE version and the mod PAGE version are different
                # numbering schemes and cross-comparing loops forever.
                installed = _norm_version(
                    rec.get("page_version") or rec.get("version")
                )
                if rec.get("ignore_update") and _norm_version(
                    rec["ignore_update"]
                ) == cur:
                    continue
                updates[folder] = {
                    "installed": rec.get("version"),
                    "current": node.get("version"),
                    "update_available": _is_newer_version(cur, installed),
                }
            decky.logger.info(
                f"check_updates({game_domain!r}): "
                f"{sum(1 for u in updates.values() if u['update_available'])} of "
                f"{len(updates)} tracked mods have updates"
            )
            return {"ok": True, "updates": updates}
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, KeyError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def get_mod_load_status(self, game_user_dir: str) -> dict:
        """Parse the game's last-session log into per-mod load outcomes, so
        the UI can distinguish 'installed' from 'actually loaded by the game'
        - the difference between a broken mod and a broken plugin."""
        if not re.fullmatch(r"[A-Za-z0-9 ._-]+", game_user_dir or ""):
            return {"ok": False, "error": "Invalid game user dir"}
        log_path = os.path.join(
            decky.DECKY_USER_HOME, ".local", "share", game_user_dir,
            "logs", "godot.log",
        )
        if not os.path.isfile(log_path):
            return {"ok": True, "available": False}
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError as e:
            return {"ok": False, "error": str(e)}

        status, modded_session = _parse_mod_load_log(lines)
        return {
            "ok": True,
            "available": True,
            "modded_session": modded_session,
            "status": status,
        }

    async def get_smapi_load_status(self, config_dir_name: str) -> dict:
        """Per-mod load outcomes from SMAPI's own log - Stardew's equivalent
        of the Godot log parser. Same response shape."""
        if not re.fullmatch(r"[A-Za-z0-9 ._-]+", config_dir_name or ""):
            return {"ok": False, "error": "Invalid config dir"}
        log_path = _smapi_log_path(config_dir_name)
        if not os.path.isfile(log_path):
            return {"ok": True, "available": False}
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError as e:
            return {"ok": False, "error": str(e)}
        status, modded_session = _parse_smapi_log(lines)
        return {
            "ok": True,
            "available": True,
            "modded_session": modded_session,
            "status": status,
        }

    # ---- Free-user groundwork: nxm:// relay ----------------------------------
    # Registered handler + queue only; the full free-user flow stays behind
    # the kill switch until the internal conversation blesses it (see
    # docs/free-user-design.md). Also serves the Phase 1 dispatch spike.

    async def register_nxm_handler(self) -> dict:
        """Install a user-level nxm:// handler: a relay script that appends
        received URLs to a queue file, registered via a desktop entry."""
        try:
            runtime = decky.DECKY_PLUGIN_RUNTIME_DIR
            os.makedirs(runtime, exist_ok=True)
            queue = os.path.join(runtime, NXM_QUEUE_NAME)
            script = os.path.join(runtime, "nxm-relay.sh")
            with open(script, "w", encoding="utf-8", newline="\n") as f:
                f.write('#!/bin/sh\necho "$(date +%s) $1" >> "' + queue + '"\n')
            os.chmod(script, 0o755)

            apps_dir = os.path.join(
                decky.DECKY_USER_HOME, ".local", "share", "applications"
            )
            os.makedirs(apps_dir, exist_ok=True)
            desktop_id = "nexus-mods-decky-nxm.desktop"
            with open(
                os.path.join(apps_dir, desktop_id), "w",
                encoding="utf-8", newline="\n",
            ) as f:
                f.write(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=Nexus Mods (Decky) NXM Relay\n"
                    f"Exec={script} %u\n"
                    "MimeType=x-scheme-handler/nxm;\n"
                    "NoDisplay=true\n"
                    "Terminal=false\n"
                )

            tools = {}
            for cmd in (
                ["update-desktop-database", apps_dir],
                ["xdg-settings", "set", "default-url-scheme-handler", "nxm", desktop_id],
            ):
                if shutil.which(cmd[0]):
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    tools[cmd[0]] = (await proc.wait()) == 0
                else:
                    tools[cmd[0]] = False
            decky.logger.info(f"nxm handler registered (tools: {tools})")
            return {"ok": True, "tools": tools}
        except Exception as e:  # noqa: BLE001 - surfaced to UI + logged
            decky.logger.exception("register_nxm_handler crashed")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def unregister_nxm_handler(self) -> dict:
        try:
            apps_dir = os.path.join(
                decky.DECKY_USER_HOME, ".local", "share", "applications"
            )
            desktop = os.path.join(apps_dir, "nexus-mods-decky-nxm.desktop")
            removed = os.path.isfile(desktop)
            if removed:
                os.remove(desktop)
            if shutil.which("update-desktop-database"):
                proc = await asyncio.create_subprocess_exec(
                    "update-desktop-database", apps_dir,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            return {"ok": True, "removed": removed}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def get_nxm_queue(self, clear: bool = False) -> dict:
        """Read (and optionally clear) the relay queue: raw lines for
        diagnostics plus strictly-parsed mod-file entries."""
        queue = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, NXM_QUEUE_NAME)
        if not os.path.isfile(queue):
            return {"ok": True, "raw": [], "entries": []}
        try:
            with open(queue, "r", encoding="utf-8", errors="replace") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            if clear:
                open(queue, "w").close()
        except OSError as e:
            return {"ok": False, "error": str(e)}
        entries = []
        for line in lines:
            parts = line.split(" ", 1)
            url = parts[1] if len(parts) == 2 else parts[0]
            parsed = _parse_nxm_url(url)
            if parsed:
                entries.append(parsed)
        return {"ok": True, "raw": lines[-10:], "entries": entries[-10:]}

    # ---- Debugging -----------------------------------------------------------

    async def get_debug_info(
        self, game_user_dir: str = "", smapi_config_dir: str = ""
    ) -> dict:
        """Tails of the plugin's own log and the game's mod-loader log
        (Godot via game_user_dir, or SMAPI via smapi_config_dir). Read-only.
        With neither, returns only the plugin log."""
        if game_user_dir and not re.fullmatch(r"[A-Za-z0-9 ._-]+", game_user_dir):
            return {"ok": False, "error": "Invalid game user dir"}
        if smapi_config_dir and not re.fullmatch(
            r"[A-Za-z0-9 ._-]+", smapi_config_dir
        ):
            return {"ok": False, "error": "Invalid config dir"}

        def tail_of(path: str, n: int) -> str:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    return "\n".join(f.read().splitlines()[-n:])
            except OSError as e:
                return f"(could not read: {e})"

        result = {"ok": True}

        try:
            log_dir = decky.DECKY_PLUGIN_LOG_DIR
            logs = [
                os.path.join(log_dir, f)
                for f in os.listdir(log_dir)
                if f.endswith(".log")
            ]
            newest = max(logs, key=os.path.getmtime, default=None)
            result["plugin_log"] = tail_of(newest, 40) if newest else "(no plugin log)"
        except OSError as e:
            result["plugin_log"] = f"(error: {e})"

        if smapi_config_dir and not game_user_dir:
            smapi_log = _smapi_log_path(smapi_config_dir)
            if not os.path.isfile(smapi_log):
                result["game_log_mod_lines"] = "(SMAPI log not found - has the game run through SMAPI?)"
                return result
            try:
                with open(smapi_log, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
                mod_lines = [
                    l
                    for l in lines
                    if re.search(r"Loaded \d+ mods|\bby\b.*\||because|Skipped", l)
                ]
                result["game_log_mod_lines"] = (
                    "\n".join(mod_lines[-40:]) or "(no mod-related lines)"
                )
                result["game_log_tail"] = "\n".join(lines[-25:])
            except OSError as e:
                result["game_log_mod_lines"] = f"(error: {e})"
            return result

        if not game_user_dir:
            result["game_log_mod_lines"] = "(no game log adapter for this game)"
            return result

        game_log = os.path.join(
            decky.DECKY_USER_HOME, ".local", "share", game_user_dir,
            "logs", "godot.log",
        )
        if os.path.isfile(game_log):
            try:
                with open(game_log, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
                mod_lines = [l for l in lines if "mod" in l.lower()]
                result["game_log_mod_lines"] = (
                    "\n".join(mod_lines[-40:]) or "(no mod-related lines)"
                )
                result["game_log_tail"] = "\n".join(lines[-25:])
            except OSError as e:
                result["game_log_mod_lines"] = f"(error: {e})"
        else:
            result["game_log_mod_lines"] = "(game log not found - has the game run?)"
        return result

    async def get_mods_by_ids(self, game_domain: str, mod_ids) -> dict:
        """Fetch specific mods in the given order. Used by the curated
        rail (a handful) AND My Mods thumbnails (every installed mod) -
        batches of 40 per query, capped at 200 total."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        try:
            ids = [int(i) for i in (mod_ids or [])][:200]
        except (TypeError, ValueError):
            return {"ok": False, "error": "Invalid mod ids"}
        if not ids:
            return {"ok": True, "mods": []}
        api_key = _load_settings().get("api_key")
        try:
            game_id = await _resolve_game_id(game_domain, api_key)
            nodes = []
            for start in range(0, len(ids), 40):
                chunk = ids[start : start + 40]
                id_args = ", ".join(
                    "{gameId: %d, modId: %d}" % (game_id, i) for i in chunk
                )
                data = await _gql_query(
                    "{ legacyMods(ids: [%s]) { nodes {%s} } }"
                    % (id_args, MOD_FIELDS),
                    api_key,
                )
                nodes.extend(data["legacyMods"]["nodes"])
            order = {mod_id: idx for idx, mod_id in enumerate(ids)}
            nodes.sort(key=lambda n: order.get(n.get("modId"), len(ids)))
            return {"ok": True, "mods": nodes}
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, KeyError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def get_trending_mods(self, game_domain: str, count: int = 10) -> dict:
        """Genuinely-trending mods from the v1 API (a signal v2 doesn't
        expose), mapped to the standard mod shape."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        url = f"{NEXUS_API_BASE}/v1/games/{game_domain}/mods/trending.json"
        headers = _api_headers(_load_settings().get("api_key"))
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            ) as session:
                async with session.get(url, headers=headers, ssl=SSL_CONTEXT) as resp:
                    if resp.status != 200:
                        return {
                            "ok": False,
                            "error": f"Nexus Mods API error (HTTP {resp.status})",
                        }
                    body = await resp.json()
        except aiohttp.ClientError as e:
            return {"ok": False, "error": f"Network error: {type(e).__name__}"}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Nexus Mods API timed out"}

        show_adult = _show_adult()
        mods = [
            _map_v1_mod(m)
            for m in body
            if m.get("name")
            and m.get("available", True)
            and (show_adult or not m.get("contains_adult_content"))
        ]
        mods = [m for m in mods if not m["adultContent"]][: int(count)]
        return {"ok": True, "total": len(mods), "mods": mods}

    # ---- Mod files & install (REST v1) --------------------------------------

    async def get_mod_files(self, game_domain: str, mod_id: int) -> dict:
        url = f"{NEXUS_API_BASE}/v1/games/{game_domain}/mods/{mod_id}/files.json"
        headers = _api_headers(_load_settings().get("api_key"))
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            ) as session:
                async with session.get(url, headers=headers, ssl=SSL_CONTEXT) as resp:
                    if resp.status != 200:
                        return {
                            "ok": False,
                            "error": f"Nexus Mods API error (HTTP {resp.status})",
                        }
                    body = await resp.json()
        except aiohttp.ClientError as e:
            return {"ok": False, "error": f"Network error: {type(e).__name__}"}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Nexus Mods API timed out"}

        files = [
            {
                "file_id": f["file_id"],
                "name": f.get("name") or f.get("file_name") or "file",
                "file_name": f.get("file_name") or f"{mod_id}-{f['file_id']}.zip",
                "version": f.get("version") or "",
                "size_kb": f.get("size_kb") or f.get("size") or 0,
                "category_name": f.get("category_name") or "",
                "is_primary": bool(f.get("is_primary")),
                "description": f.get("description") or "",
            }
            for f in body.get("files", [])
            if f.get("category_id") in VISIBLE_FILE_CATEGORIES
        ]
        return {"ok": True, "files": _sort_mod_files(files)}

    async def install_mod(
        self,
        game_domain: str,
        mod_id: int,
        file_id: int,
        file_name: str,
        mod_name: str,
        mod_version: str,
        install_dir: str,
        mods_subdir: str,
        dl_key: str = "",
        dl_expires: str = "",
        install_mode: str = "folder",
        app_id: int = 0,
        plugins_subpath: str = "",
        plugins_style: str = "starred",
        payload_choice: str = "",
        ue4ss_subdir: str = "",
        logicmods_subdir: str = "",
        launcher_xml_subpath: str = "",
        flat_extensions: list = None,
        page_version: str = "",
        record_source: str = "",
        witcher_layout: bool = False,
        collection_slug: str = "",
    ) -> dict:
        """Wrapper so any unexpected failure reaches the UI as a real message
        instead of decky's generic 'Python Exception'. dl_key/dl_expires are
        the website-issued free-download token from an nxm:// link;
        payload_choice picks a folder from an option-style archive."""
        try:
            return await self._install_mod_inner(
                game_domain,
                mod_id,
                file_id,
                file_name,
                mod_name,
                mod_version,
                install_dir,
                mods_subdir,
                dl_key,
                dl_expires,
                install_mode,
                app_id,
                plugins_subpath,
                plugins_style,
                payload_choice,
                ue4ss_subdir,
                logicmods_subdir,
                launcher_xml_subpath,
                flat_extensions,
                page_version,
                record_source,
                witcher_layout,
                collection_slug,
            )
        except Exception as e:  # noqa: BLE001 - surfaced to UI + logged
            decky.logger.exception(f"install_mod({mod_name!r}) crashed")
            await _emit_progress(mod_id, "error", 0, str(e))
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def _install_root_files(
        self, scratch: str, install_path: str, game_domain: str,
        mod_id: int, file_id: int, file_name: str, mod_name: str,
        mod_version: str, page_version: str, record_source: str,
        collection_slug: str,
    ) -> dict:
        """Install an archive into the GAME ROOT following its Vortex
        override instructions (root-payload mods like SSE Engine Fixes
        part 2: preloader dlls that live beside the game exe, not in
        Data/). Falls back to copying everything when the instructions
        don't parse. Records mode='files' target='.' so uninstall removes
        exactly these files."""
        override = _find_vortex_override(scratch)
        copies = _vortex_override_copies(override) if override else []
        if not copies:
            copies = []
            for root, _dirs, names in os.walk(scratch):
                for name in names:
                    rel = os.path.relpath(os.path.join(root, name), scratch)
                    rel = rel.replace(os.sep, "/")
                    if rel.lower() == "vortex_override_instructions.json":
                        continue
                    if _safe_rel_path(rel):
                        copies.append((rel, rel))
        installed_rel = []
        for src_rel, dst_rel in copies:
            src = os.path.join(scratch, *src_rel.split("/"))
            if not os.path.isfile(src):
                continue
            dst = os.path.join(install_path, *dst_rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isfile(dst):
                os.remove(dst)
            shutil.move(src, dst)
            installed_rel.append(dst_rel)
        _force_rmtree(scratch)
        if not installed_rel:
            await _emit_progress(mod_id, "error", 0, "no root files")
            return {"ok": False, "error": "Nothing usable in this archive"}
        settings = _load_settings()
        installed = settings.setdefault("installed", {}).setdefault(
            game_domain, {}
        )
        record_key = _safe_name(mod_name)
        installed[record_key] = {
            "mod_id": mod_id,
            "file_id": file_id,
            "name": mod_name,
            "version": mod_version,
            "file_name": file_name,
            "installed_at": int(time.time()),
            "page_version": page_version,
            "source": record_source,
            "collection_slug": collection_slug,
            "mode": "files",
            "target": ".",
            "files": installed_rel,
        }
        _save_settings(settings)
        decky.logger.info(
            f"installed {mod_name!r} into game root "
            f"({len(installed_rel)} files, vortex override: {bool(override)})"
        )
        await _emit_progress(mod_id, "done", 100)
        return {"ok": True, "folder": record_key}

    async def _install_mod_inner(
        self,
        game_domain: str,
        mod_id: int,
        file_id: int,
        file_name: str,
        mod_name: str,
        mod_version: str,
        install_dir: str,
        mods_subdir: str,
        dl_key: str = "",
        dl_expires: str = "",
        install_mode: str = "folder",
        app_id: int = 0,
        plugins_subpath: str = "",
        plugins_style: str = "starred",
        payload_choice: str = "",
        ue4ss_subdir: str = "",
        logicmods_subdir: str = "",
        launcher_xml_subpath: str = "",
        flat_extensions: list = None,
        page_version: str = "",
        record_source: str = "",
        witcher_layout: bool = False,
        collection_slug: str = "",
    ) -> dict:
        settings = _load_settings()
        api_key = settings.get("api_key")
        if not api_key:
            await _emit_progress(mod_id, "error", 0, "not signed in")
            return {"ok": False, "error": "Not signed in"}

        install_path, mods_path, disabled_path = _game_paths(install_dir, mods_subdir)
        if not os.path.isdir(install_path):
            await _emit_progress(mod_id, "error", 0, "game not found")
            return {"ok": False, "error": "Game install folder not found"}

        # 1) Ask for a download link (Premium-only endpoint; free users need
        #    key+expires params from a website nxm:// link - future work).
        link_url = (
            f"{NEXUS_API_BASE}/v1/games/{game_domain}/mods/{mod_id}"
            f"/files/{file_id}/download_link.json"
        )
        if dl_key:
            # free-account flow: website-issued token from the nxm:// link
            link_url += (
                f"?key={urllib.parse.quote(dl_key)}"
                f"&expires={urllib.parse.quote(str(dl_expires))}"
            )
        headers = _api_headers(api_key)
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                async with session.get(
                    link_url, headers=headers, ssl=SSL_CONTEXT
                ) as resp:
                    if resp.status == 403:
                        return {
                            "ok": False,
                            "error": "Direct downloads need a Premium account "
                            "(free-user flow not implemented yet)",
                        }
                    if resp.status != 200:
                        return {
                            "ok": False,
                            "error": f"Download link error (HTTP {resp.status})",
                        }
                    links = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return {"ok": False, "error": f"Network error: {type(e).__name__}"}

        if not links or not isinstance(links, list):
            return {"ok": False, "error": "Nexus Mods returned no download locations"}
        uri = links[0].get("URI") or links[0].get("uri")
        if not uri:
            return {"ok": False, "error": "Nexus Mods returned a malformed download link"}

        # 2) Download with progress events. Archive name is built from ids so
        #    non-ASCII upstream filenames can't produce a broken local path;
        #    bsdtar detects the format from content anyway.
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        ext = os.path.splitext(file_name or "")[1]
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext):
            ext = ""
        archive_path = os.path.join(DOWNLOADS_DIR, f"{mod_id}-{file_id}{ext}")
        await _emit_progress(mod_id, "downloading", 0)
        try:
            timeout = aiohttp.ClientTimeout(total=1800, sock_connect=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(uri, ssl=SSL_CONTEXT) as resp:
                    if resp.status != 200:
                        return {
                            "ok": False,
                            "error": f"CDN download failed (HTTP {resp.status})",
                        }
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    last_pct = -1
                    with open(archive_path, "wb") as out:
                        async for chunk in resp.content.iter_chunked(1 << 20):
                            out.write(chunk)
                            done += len(chunk)
                            if total:
                                pct = int(done * 100 / total)
                                if pct > last_pct:
                                    last_pct = pct
                                    await _emit_progress(
                                        mod_id, "downloading", pct
                                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return {"ok": False, "error": f"Download failed: {type(e).__name__}"}

        # 3) Extract to a scratch dir, then move into mods/.
        await _emit_progress(mod_id, "extracting", 100)
        scratch = os.path.join(DOWNLOADS_DIR, f"extract-{mod_id}-{file_id}")
        _force_rmtree(scratch)
        os.makedirs(scratch, exist_ok=True)
        err = await _extract_archive(archive_path, scratch)
        if err:
            _force_rmtree(scratch)
            await _emit_progress(mod_id, "error", 0, err)
            return {"ok": False, "error": f"Extraction failed: {err}"}
        # Archives in the wild ship read-only entries; normalize before moving.
        _normalize_perms(scratch)

        entries = os.listdir(scratch)
        if not entries:
            _force_rmtree(scratch)
            return {"ok": False, "error": "Archive was empty"}

        if install_mode == "dataDir":
            # FOMOD wizard archives: parse the wizard, park the extraction,
            # and let the user pick options in the UI - install_fomod
            # finishes the job with the same merge below.
            if not payload_choice and _fomod_config_path(scratch):
                _, data_path_now, _unused2 = _game_paths(
                    install_dir, mods_subdir
                )
                parsed = _parse_fomod(scratch, data_path_now)
                if parsed:
                    wizard, ctx = parsed
                    if wizard["steps"]:
                        _prune_pending_fomods()
                        token = f"{mod_id}-{file_id}-{int(time.time())}"
                        PENDING_FOMODS[token] = {
                            "at": time.time(),
                            "scratch": scratch,
                            "ctx": ctx,
                            "game_domain": game_domain,
                            "mod_id": mod_id,
                            "file_id": file_id,
                            "file_name": file_name,
                            "mod_name": mod_name,
                            "mod_version": mod_version,
                            "install_dir": install_dir,
                            "mods_subdir": mods_subdir,
                            "app_id": app_id,
                            "plugins_subpath": plugins_subpath,
                            "plugins_style": plugins_style,
                            "page_version": page_version,
                            "record_source": record_source,
                            "collection_slug": collection_slug,
                        }
                        try:
                            os.remove(archive_path)
                        except OSError:
                            pass
                        await _emit_progress(
                            mod_id, "error", 0, "fomod wizard"
                        )
                        return {
                            "ok": False,
                            "needs_fomod": True,
                            "fomod_token": token,
                            "wizard": wizard,
                        }
                    # Wizard-less FOMOD (only requiredInstallFiles): stage
                    # it directly and continue as a normal payload.
                    staging = os.path.join(scratch, "__fomod_staged__")
                    os.makedirs(staging, exist_ok=True)
                    if _fomod_stage(ctx, [], staging) > 0:
                        for e in os.listdir(scratch):
                            if e != "__fomod_staged__":
                                _force_rmtree(os.path.join(scratch, e))
            # Skyrim-class: merge the payload into Data/, record a per-file
            # manifest, activate any plugin files in plugins.txt.
            payload = _find_data_payload(scratch)
            if payload is None and payload_choice:
                if not _safe_rel_path(payload_choice):
                    _force_rmtree(scratch)
                    return {"ok": False, "error": "Invalid folder choice"}
                chosen = os.path.join(scratch, *payload_choice.split("/"))
                if os.path.isdir(chosen):
                    payload = _find_data_payload(chosen)
            payload_dirs = [payload] if payload else []
            if not payload_dirs and payload_choice == "*":
                # "Install everything": merge every discovered option -
                # replacer packs ship dozens of per-item folders meant to
                # combine into one install.
                payload_dirs = [
                    _find_data_payload(os.path.join(scratch, *opt.split("/")))
                    for opt in _payload_options(scratch)
                ]
                payload_dirs = [p for p in payload_dirs if p]
            if not payload_dirs and payload_choice not in ("", "*"):
                _force_rmtree(scratch)
                await _emit_progress(mod_id, "error", 0, "bad folder choice")
                return {"ok": False, "error": "Chosen folder wasn't usable"}
            if not payload_dirs:
                options = _payload_options(scratch)
                if len(options) == 1:
                    # Only one folder actually resolves (e.g. a FOMOD whose
                    # numbered core is the sole real payload) - just use it.
                    payload_dirs = [
                        _find_data_payload(
                            os.path.join(scratch, *options[0].split("/"))
                        )
                    ]
                elif options:
                    decky.logger.info(
                        f"install {mod_name!r}: offering "
                        f"{len(options)} payload options"
                    )
                    _force_rmtree(scratch)
                    try:
                        os.remove(archive_path)
                    except OSError:
                        pass
                    await _emit_progress(mod_id, "error", 0, "choose a folder")
                    return {
                        "ok": False,
                        "needs_choice": True,
                        "options": options,
                    }
            payload_dirs = [p for p in payload_dirs if p]
            if not payload_dirs:
                # Root-payload archives (e.g. SSE Engine Fixes part 2's
                # preloader): no Data payload, but the mod ships Vortex
                # override instructions saying where files go - honor them
                # and install into the game root as a files-mode record.
                if _find_vortex_override(scratch):
                    return await self._install_root_files(
                        scratch, install_path, game_domain, mod_id, file_id,
                        file_name, mod_name, mod_version, page_version,
                        record_source, collection_slug,
                    )
                top_paths = [os.path.join(scratch, e) for e in entries]
                # Bare loose files (a BOS _SWAP.ini, a config archive):
                # nothing to unwrap - the archive root IS the Data payload.
                if (
                    entries
                    and all(os.path.isfile(p) for p in top_paths)
                    and not any(e.lower().endswith(".exe") for e in entries)
                ):
                    payload_dirs = [scratch]
                else:
                    exes = [
                        n
                        for root, _dirs, names in os.walk(scratch)
                        for n in names
                        if n.lower().endswith(".exe")
                    ]
                    if exes:
                        # Desktop modding tools (xEdit, patchers): they
                        # don't install into the game and can't run here.
                        _force_rmtree(scratch)
                        await _emit_progress(mod_id, "error", 0, "pc tool")
                        return {
                            "ok": False,
                            "unsupported_tool": True,
                            "error": f"{mod_name} looks like a PC modding "
                            f"tool ({exes[0]}), not a mod the game loads - "
                            "it needs a desktop setup, so it was skipped.",
                        }
            if not payload_dirs:
                tops = ", ".join(sorted(entries)[:6])
                # Log one level deeper - top-level names alone have not
                # been enough to diagnose unrecognized layouts.
                second = []
                for e in sorted(entries)[:4]:
                    p = os.path.join(scratch, e)
                    if os.path.isdir(p):
                        inner = ", ".join(sorted(os.listdir(p))[:5])
                        second.append(f"{e}/[{inner}]")
                decky.logger.info(
                    f"install {mod_name!r}: no payload; archive top level: "
                    f"{tops}; second level: {'; '.join(second) or '(files only)'}"
                )
                _force_rmtree(scratch)
                await _emit_progress(mod_id, "error", 0, "no payload")
                return {
                    "ok": False,
                    "error": "This archive has no recognizable Data payload "
                    "(FOMOD/optioned installers aren't supported yet). "
                    f"It contains: {tops}",
                }
            os.makedirs(mods_path, exist_ok=True)
            files_rel, plugins = [], []
            for payload in payload_dirs:
                for root, _dirs, names in os.walk(payload):
                    for name in names:
                        src_file = os.path.join(root, name)
                        rel = os.path.relpath(src_file, payload)
                        if not _safe_rel_path(rel):
                            continue
                        # Reuse existing on-disk casing so we never create
                        # twin dirs (Textures vs textures) that Wine splits
                        # between.
                        rel = _case_merge_rel(mods_path, rel)
                        dst = os.path.join(mods_path, *rel.split("/"))
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        if os.path.isfile(dst):
                            os.remove(dst)
                        shutil.move(src_file, dst)
                        if rel not in files_rel:
                            files_rel.append(rel)
                        if (
                            "/" not in rel
                            and rel.lower().endswith(PLUGIN_EXTENSIONS)
                            and rel not in plugins
                        ):
                            plugins.append(rel)
            _force_rmtree(scratch)
            try:
                os.remove(archive_path)
            except OSError:
                pass
            if not files_rel:
                return {"ok": False, "error": "Archive contained no files"}
            if plugins and plugins_subpath:
                _add_plugins(
                    _plugins_txt_path(app_id, plugins_subpath),
                    plugins,
                    plugins_style,
                )
            record_key = _safe_name(mod_name)
            settings = _load_settings()  # re-read: parallel installs
            installed = settings.setdefault("installed", {}).setdefault(
                game_domain, {}
            )
            installed[record_key] = {
                "mod_id": mod_id,
                "file_id": file_id,
                "name": mod_name,
                "version": mod_version,
                "file_name": file_name,
                "installed_at": int(time.time()),
                "page_version": page_version,
                "source": record_source,
                "collection_slug": collection_slug,
                "mode": "dataDir",
                "files": files_rel,
                "plugins": plugins,
            }
            _save_settings(settings)
            decky.logger.info(
                f"installed {mod_name!r} into Data/ "
                f"({len(files_rel)} files, {len(plugins)} plugins)"
            )
            await _emit_progress(mod_id, "done", 100)
            return {"ok": True, "folder": record_key}

        # UE4SS script/Blueprint mods: route them to the loader's dirs when
        # the game declares UE4SS support; refuse with a clear message when
        # it doesn't (files placed elsewhere silently never load).
        if _looks_like_ue4ss_mod(scratch):
            if not ue4ss_subdir:
                _force_rmtree(scratch)
                return {
                    "ok": False,
                    "error": "This is a UE4SS script mod - the UE4SS loader "
                    "isn't supported for this game yet. Pak-based mods "
                    "work.",
                }
            install_path = os.path.join(STEAM_COMMON, install_dir)
            route = _route_ue4ss_payload(
                scratch, install_path, ue4ss_subdir,
                logicmods_subdir or ue4ss_subdir, mod_name,
            )
            _force_rmtree(scratch)
            try:
                os.remove(archive_path)
            except OSError:
                pass
            record_key = route.get("folder") or _safe_name(mod_name)
            settings = _load_settings()  # re-read: parallel installs
            installed = settings.setdefault("installed", {}).setdefault(
                game_domain, {}
            )
            installed[record_key] = {
                "mod_id": mod_id,
                "file_id": file_id,
                "name": mod_name,
                "version": mod_version,
                "file_name": file_name,
                "installed_at": int(time.time()),
                "page_version": page_version,
                "source": record_source,
                "collection_slug": collection_slug,
                **route,
            }
            _save_settings(settings)
            decky.logger.info(
                f"installed UE4SS mod {mod_name!r} -> {route.get('target')!r}"
            )
            await _emit_progress(mod_id, "done", 100)
            return {"ok": True, "folder": record_key}

        # Witcher 3: classify into mod folders / dlc folders / menu XMLs,
        # gate on script conflicts, and register menu XMLs in both
        # filelists (next-gen requirement).
        if witcher_layout:
            install_path = os.path.join(STEAM_COMMON, install_dir)
            mod_dirs, dlc_dirs, menu_xmls, w3_err = _route_witcher_payload(
                scratch, install_path, mods_path, mod_name
            )
            if w3_err:
                _force_rmtree(scratch)
                return {"ok": False, "error": w3_err}
            os.makedirs(mods_path, exist_ok=True)
            settings = _load_settings()  # re-read: parallel installs
            installed = settings.setdefault("installed", {}).setdefault(
                game_domain, {}
            )
            base_rec = {
                "mod_id": mod_id,
                "file_id": file_id,
                "version": mod_version,
                "file_name": file_name,
                "installed_at": int(time.time()),
                "page_version": page_version,
                "source": record_source,
                "collection_slug": collection_slug,
            }
            first_folder = None
            for d in mod_dirs:
                folder = os.path.basename(d)
                dst = os.path.join(mods_path, folder)
                _force_rmtree(dst)
                shutil.move(d, dst)
                installed[folder] = {
                    **base_rec,
                    "name": mod_name if len(mod_dirs) == 1 else folder,
                }
                first_folder = first_folder or folder
            dlc_root = os.path.join(install_path, "dlc")
            os.makedirs(dlc_root, exist_ok=True)
            for d in dlc_dirs:
                folder = os.path.basename(d)
                dst = os.path.join(dlc_root, folder)
                _force_rmtree(dst)
                shutil.move(d, dst)
                installed[folder] = {
                    **base_rec,
                    "name": f"{mod_name} ({folder})",
                    "target": "dlc",
                    "folder": folder,
                }
                first_folder = first_folder or folder
            pc_dir = os.path.join(install_path, *W3_MENU_DIR.split("/"))
            xml_names = []
            if menu_xmls:
                os.makedirs(pc_dir, exist_ok=True)
                for x in menu_xmls:
                    name = os.path.basename(x)
                    dstx = os.path.join(pc_dir, name)
                    if os.path.isfile(dstx):
                        os.remove(dstx)
                    shutil.move(x, dstx)
                    _w3_filelist_append(pc_dir, name)
                    xml_names.append(name)
                if first_folder and first_folder in installed:
                    installed[first_folder]["menuXmls"] = xml_names
            _save_settings(settings)
            _force_rmtree(scratch)
            try:
                os.remove(archive_path)
            except OSError:
                pass
            decky.logger.info(
                f"installed W3 {mod_name!r}: {len(mod_dirs)} mod folder(s), "
                f"{len(dlc_dirs)} dlc, {len(xml_names)} menu xml(s)"
            )
            await _emit_progress(mod_id, "done", 100)
            return {"ok": True, "folder": first_folder or _safe_name(mod_name)}

        # Flat-file games (Cyberpunk archive/pc/mod): the game loads files,
        # not folders - move matching files flat and keep a per-file record.
        if flat_extensions:
            exts = tuple(e.lower() for e in flat_extensions)
            flat = []
            for root, _dirs, names in os.walk(scratch):
                flat.extend(
                    os.path.join(root, n)
                    for n in names
                    if n.lower().endswith(exts)
                )
            if not flat:
                _force_rmtree(scratch)
                return {
                    "ok": False,
                    "error": "No loadable mod files found in this archive "
                    f"(expected {', '.join(flat_extensions)})",
                }
            os.makedirs(mods_path, exist_ok=True)
            moved = []
            for src in flat:
                name = os.path.basename(src)
                dst = os.path.join(mods_path, name)
                if os.path.isfile(dst):
                    os.remove(dst)
                shutil.move(src, dst)
                moved.append(name)
            _force_rmtree(scratch)
            try:
                os.remove(archive_path)
            except OSError:
                pass
            record_key = _safe_name(mod_name)
            settings = _load_settings()  # re-read: parallel installs
            installed = settings.setdefault("installed", {}).setdefault(
                game_domain, {}
            )
            installed[record_key] = {
                "mod_id": mod_id,
                "file_id": file_id,
                "name": mod_name,
                "version": mod_version,
                "file_name": file_name,
                "installed_at": int(time.time()),
                "page_version": page_version,
                "source": record_source,
                "collection_slug": collection_slug,
                "mode": "files",
                "target": mods_subdir,
                "files": moved,
            }
            _save_settings(settings)
            decky.logger.info(
                f"installed {mod_name!r}: {len(moved)} flat files -> "
                f"{mods_subdir!r}"
            )
            await _emit_progress(mod_id, "done", 100)
            return {"ok": True, "folder": record_key}

        # Archives that ship the mods DIRECTORY itself (Bannerlord zips
        # rooted at Modules/, Stardew at Mods/, BepInEx at plugins/):
        # unwrap it, or the mod nests invisibly (Modules/Modules/X).
        os.makedirs(mods_path, exist_ok=True)
        target_name = os.path.basename(mods_subdir.rstrip("/")).lower()
        if (
            len(entries) == 1
            and entries[0].lower() == target_name
            and os.path.isdir(os.path.join(scratch, entries[0]))
        ):
            wrapper = os.path.join(scratch, entries[0])
            scratch_entries = os.listdir(wrapper)
            children = [
                e
                for e in scratch_entries
                if os.path.isdir(os.path.join(wrapper, e))
            ]
            if children:
                # Install every child as its own mod folder (multi-module
                # archives are common on Bannerlord).
                settings = _load_settings()  # re-read: parallel installs
                installed_rec = settings.setdefault("installed", {}).setdefault(
                    game_domain, {}
                )
                for child in children:
                    dst = os.path.join(mods_path, child)
                    _force_rmtree(dst)
                    shutil.move(os.path.join(wrapper, child), dst)
                    rec = {
                        "mod_id": mod_id,
                        "file_id": file_id,
                        "name": mod_name if len(children) == 1 else child,
                        "version": mod_version,
                        "file_name": file_name,
                        "installed_at": int(time.time()),
                "page_version": page_version,
                "source": record_source,
                "collection_slug": collection_slug,
                    }
                    if launcher_xml_subpath:
                        module_id = _submodule_id(dst)
                        if module_id:
                            rec["moduleId"] = module_id
                            rec["launcherXml"] = launcher_xml_subpath
                            _set_module_selected(
                                _launcher_xml_path(app_id, launcher_xml_subpath),
                                module_id,
                                True,
                            )
                    installed_rec[child] = rec
                _save_settings(settings)
                _force_rmtree(scratch)
                try:
                    os.remove(archive_path)
                except OSError:
                    pass
                decky.logger.info(
                    f"installed {mod_name!r}: unwrapped "
                    f"{entries[0]}/ -> {children}"
                )
                await _emit_progress(mod_id, "done", 100)
                return {"ok": True, "folder": children[0]}

        # Single top-level folder -> that IS the mod folder. Loose files ->
        # wrap them in a folder named after the mod.
        if len(entries) == 1 and os.path.isdir(os.path.join(scratch, entries[0])):
            folder = entries[0]
            src = os.path.join(scratch, folder)
        else:
            folder = _safe_name(mod_name)
            src = scratch

        # Replace any previous copy (enabled or disabled) - reinstall/update.
        for base in (mods_path, disabled_path):
            old = os.path.join(base, folder)
            if os.path.isdir(old):
                _force_rmtree(old)
        # Note: the StS2 loader accepts any *.json manifest in the mod folder
        # (both "<id>.json" and legacy "mod_manifest.json") - do NOT create
        # extra manifest copies; the loader treats each as a separate mod and
        # errors on the duplicate id.
        shutil.move(src, os.path.join(mods_path, folder))
        _force_rmtree(scratch)
        try:
            os.remove(archive_path)
        except OSError:
            pass

        # 4) Record the install.
        record = {
            "mod_id": mod_id,
            "file_id": file_id,
            "name": mod_name,
            "version": mod_version,
            "file_name": file_name,
            "installed_at": int(time.time()),
                "page_version": page_version,
                "source": record_source,
                "collection_slug": collection_slug,
        }
        # Bannerlord-style launcher games: modules need selecting in the
        # launcher's XML config; the Id lives in the module's SubModule.xml.
        if launcher_xml_subpath:
            module_id = _submodule_id(os.path.join(mods_path, folder))
            if module_id:
                record["moduleId"] = module_id
                record["launcherXml"] = launcher_xml_subpath
                activated = _set_module_selected(
                    _launcher_xml_path(app_id, launcher_xml_subpath),
                    module_id,
                    True,
                )
                decky.logger.info(
                    f"module {module_id!r} activation: "
                    f"{'ok' if activated else 'deferred (no LauncherData.xml yet)'}"
                )
        settings = _load_settings()  # re-read: parallel installs
        installed = settings.setdefault("installed", {}).setdefault(game_domain, {})
        installed[folder] = record
        _save_settings(settings)

        decky.logger.info(f"installed {mod_name!r} -> {mods_path}/{folder}")
        await _emit_progress(mod_id, "done", 100)
        return {"ok": True, "folder": folder}

    async def install_fomod(self, token: str, selected_ids: list) -> dict:
        """Finish a parked FOMOD install with the user's wizard selections.
        Stages the selected sources, then runs the same dataDir merge as a
        normal install (case-merged paths, per-file manifest, plugins.txt
        activation)."""
        try:
            entry = PENDING_FOMODS.pop(token, None)
            if not entry:
                return {
                    "ok": False,
                    "error": "This install expired - start it again",
                }
            scratch = entry["scratch"]
            staging = os.path.join(scratch, "__fomod_staged__")
            _force_rmtree(staging)
            os.makedirs(staging)
            staged = _fomod_stage(entry["ctx"], list(selected_ids or []), staging)
            if staged == 0:
                _force_rmtree(scratch)
                return {"ok": False, "error": "Nothing selected to install"}

            _, mods_path, _unused = _game_paths(
                entry["install_dir"], entry["mods_subdir"]
            )
            os.makedirs(mods_path, exist_ok=True)
            files_rel, plugins = [], []
            for root, _dirs, names in os.walk(staging):
                for name in names:
                    src_file = os.path.join(root, name)
                    rel = os.path.relpath(src_file, staging)
                    if not _safe_rel_path(rel):
                        continue
                    rel = _case_merge_rel(mods_path, rel)
                    dst = os.path.join(mods_path, *rel.split("/"))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if os.path.isfile(dst):
                        os.remove(dst)
                    shutil.move(src_file, dst)
                    if rel not in files_rel:
                        files_rel.append(rel)
                    if "/" not in rel and rel.lower().endswith(
                        PLUGIN_EXTENSIONS
                    ) and rel not in plugins:
                        plugins.append(rel)
            _force_rmtree(scratch)
            if plugins and entry["plugins_subpath"]:
                _add_plugins(
                    _plugins_txt_path(
                        entry["app_id"], entry["plugins_subpath"]
                    ),
                    plugins,
                    entry["plugins_style"],
                )
            record_key = _safe_name(entry["mod_name"])
            settings = _load_settings()
            installed = settings.setdefault("installed", {}).setdefault(
                entry["game_domain"], {}
            )
            installed[record_key] = {
                "mod_id": entry["mod_id"],
                "file_id": entry["file_id"],
                "name": entry["mod_name"],
                "version": entry["mod_version"],
                "file_name": entry["file_name"],
                "installed_at": int(time.time()),
                "page_version": entry.get("page_version") or "",
                "source": entry.get("record_source") or "",
                "collection_slug": entry.get("collection_slug") or "",
                "mode": "dataDir",
                "files": files_rel,
                "plugins": plugins,
            }
            _save_settings(settings)
            decky.logger.info(
                f"installed FOMOD {entry['mod_name']!r}: {len(files_rel)} "
                f"files, {len(plugins)} plugins, "
                f"{len(selected_ids or [])} options"
            )
            await _emit_progress(entry["mod_id"], "done", 100)
            return {"ok": True, "folder": record_key}
        except Exception as e:  # noqa: BLE001 - surfaced to UI + logged
            decky.logger.exception("install_fomod crashed")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def install_fomod_auto(self, token: str, curator_choices) -> dict:
        """Finish a parked FOMOD using a collection curator's recorded
        choices instead of showing the wizard."""
        entry = PENDING_FOMODS.get(token)
        if not entry:
            return {"ok": False, "error": "This install expired - retry it"}
        ids = _match_fomod_choices(
            entry["ctx"]["steps"], curator_choices or {}
        )
        decky.logger.info(
            f"fomod auto-install: matched {len(ids)} options from curator "
            f"choices for {entry['mod_name']!r}"
        )
        return await self.install_fomod(token, ids)

    async def install_framework(
        self,
        game_domain: str,
        mod_id: int,
        install_dir: str,
        install_kind: str = "smapi",
        detect_file: str = "StardewModdingAPI",
        avoid_file_keywords: list = None,
        install_subdir: str = "",
    ) -> dict:
        """Download a mod-loader framework (e.g. SMAPI) from Nexus - so the
        author gets the download credit - and run its unattended installer
        against the game folder. Verified for SMAPI's installer, which
        supports --install --game-path for mod managers."""
        try:
            api_key = _load_settings().get("api_key")
            if not api_key:
                return {"ok": False, "error": "Not signed in"}
            install_path = os.path.join(STEAM_COMMON, install_dir)
            if not os.path.isdir(install_path):
                return {"ok": False, "error": "Game install folder not found"}

            files = await self.get_mod_files(game_domain, mod_id)
            if not files.get("ok"):
                return files
            file_list = files.get("files") or []
            main = _pick_main_file(file_list, avoid_file_keywords or [])
            if not main:
                return {"ok": False, "error": "No downloadable file found"}
            decky.logger.info(
                f"framework pick for {game_domain}/{mod_id}: "
                f"{main.get('file_name')!r} (file {main.get('file_id')})"
            )

            link_url = (
                f"{NEXUS_API_BASE}/v1/games/{game_domain}/mods/{mod_id}"
                f"/files/{main['file_id']}/download_link.json"
            )
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=300, sock_connect=30)
            ) as session:
                async with session.get(
                    link_url, headers=_api_headers(api_key), ssl=SSL_CONTEXT
                ) as resp:
                    if resp.status == 403:
                        return {
                            "ok": False,
                            "error": "Direct downloads need a Premium account",
                        }
                    if resp.status != 200:
                        return {
                            "ok": False,
                            "error": f"Download link error (HTTP {resp.status})",
                        }
                    links = await resp.json()
                uri = (links[0].get("URI") or links[0].get("uri")) if links else None
                if not uri:
                    return {"ok": False, "error": "Malformed download link"}
                os.makedirs(DOWNLOADS_DIR, exist_ok=True)
                archive_path = os.path.join(
                    DOWNLOADS_DIR, f"framework-{mod_id}.zip"
                )
                async with session.get(uri, ssl=SSL_CONTEXT) as resp:
                    if resp.status != 200:
                        return {
                            "ok": False,
                            "error": f"CDN download failed (HTTP {resp.status})",
                        }
                    with open(archive_path, "wb") as out:
                        async for chunk in resp.content.iter_chunked(1 << 20):
                            out.write(chunk)

            scratch = os.path.join(DOWNLOADS_DIR, f"framework-{mod_id}")
            _force_rmtree(scratch)
            os.makedirs(scratch)
            err = await _extract_archive(archive_path, scratch)
            try:
                os.remove(archive_path)
            except OSError:
                pass
            if err:
                _force_rmtree(scratch)
                return {"ok": False, "error": f"Extraction failed: {err}"}

            if install_kind == "copyRoot":
                # SKSE-style: the archive is the game-dir payload, usually
                # inside one versioned wrapper folder - flatten and merge.
                # install_subdir targets loaders that live deeper than the
                # game root (UE4SS: Pal/Binaries/Win64).
                dest_root = install_path
                if install_subdir:
                    if not _safe_rel_path(install_subdir):
                        return {"ok": False, "error": "Invalid install subdir"}
                    dest_root = os.path.join(
                        install_path, *install_subdir.split("/")
                    )
                    os.makedirs(dest_root, exist_ok=True)
                src = scratch
                top = os.listdir(scratch)
                # Flatten a single version-wrapper folder (skse64_2_02_06/),
                # but NOT a real structure dir: BLSE ships bin/... which is
                # already game-root-relative - flattening it would dump
                # Win64_Shipping_Client at the root. The detect file's first
                # path component tells us which dirs are structural.
                detect_root = detect_file.split("/")[0].lower()
                if (
                    len(top) == 1
                    and os.path.isdir(os.path.join(scratch, top[0]))
                    and top[0].lower() != detect_root
                ):
                    src = os.path.join(scratch, top[0])
                for root, _dirs, names in os.walk(src):
                    for name in names:
                        rel = os.path.relpath(os.path.join(root, name), src)
                        if not _safe_rel_path(rel):
                            continue
                        dst = os.path.join(dest_root, rel)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        if os.path.isfile(dst):
                            os.remove(dst)
                        shutil.move(os.path.join(root, name), dst)
                        if rel.lower().endswith(".exe") or "." not in os.path.basename(rel):
                            try:
                                os.chmod(dst, 0o755)
                            except OSError:
                                pass
                _force_rmtree(scratch)
                # detect_file may itself be a subpath relative to the game
                # root (matches get_game_status).
                if "/" in detect_file:
                    installed = os.path.exists(
                        os.path.join(install_path, *detect_file.split("/"))
                    )
                else:
                    installed = any(
                        n.lower() == detect_file.lower()
                        or n.startswith(detect_file)
                        for n in os.listdir(dest_root)
                    )
                if not installed:
                    return {
                        "ok": False,
                        "error": "Framework files not found after extraction",
                    }
                decky.logger.info(f"framework (copyRoot) installed into {dest_root}")
                return {"ok": True, "install_path": dest_root}

            # SMAPI's bundled installer is interactive-only (its unattended
            # flags don't exist, and 'install on Linux.sh' doesn't forward
            # args anyway - verified on device). Its documented manual
            # install is deterministic instead: extract internal/linux/
            # install.dat into the game folder, then provide
            # <detect_file>.deps.json by copying the game's own deps.json.
            install_dat = None
            for root, _dirs, names in os.walk(scratch):
                for name in names:
                    if (
                        name == "install.dat"
                        and os.path.basename(root) == "linux"
                    ):
                        install_dat = os.path.join(root, name)
            if not install_dat:
                _force_rmtree(scratch)
                return {
                    "ok": False,
                    "error": "No Linux install payload found in the archive",
                }

            err = await _extract_archive(install_dat, install_path)
            _force_rmtree(scratch)
            if err:
                return {"ok": False, "error": f"Framework extraction failed: {err}"}

            game_deps = os.path.join(install_path, f"{install_dir}.deps.json")
            framework_deps = os.path.join(
                install_path, "StardewModdingAPI.deps.json"
            )
            if os.path.isfile(game_deps) and not os.path.isfile(framework_deps):
                shutil.copy2(game_deps, framework_deps)
            launcher = os.path.join(install_path, "StardewModdingAPI")
            if os.path.isfile(launcher):
                os.chmod(launcher, 0o755)

            installed = any(
                name.startswith("StardewModdingAPI")
                for name in os.listdir(install_path)
            )
            if not installed:
                return {
                    "ok": False,
                    "error": "Framework files not found after extraction",
                }
            decky.logger.info(f"framework installed into {install_path}")
            return {"ok": True, "install_path": install_path}
        except Exception as e:  # noqa: BLE001 - surfaced to UI + logged
            decky.logger.exception("install_framework crashed")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # The plugin can't read Steam's launch options back, so it records that
    # the user completed the launch-options step per game, and whether the
    # framework is currently enabled (launch options applied) or disabled
    # (cleared - game launches vanilla).
    async def check_docs_file(self, app_id: int, subpath: str) -> dict:
        """Does a file exist under the prefix's Documents? Used for
        first-run notices (e.g. Bannerlord's LauncherData.xml only exists
        once the game has run)."""
        if not _safe_rel_path(subpath or ""):
            return {"ok": False, "error": "Invalid path"}
        path = _prefix_user_path(app_id, "Documents", *subpath.split("/"))
        return {"ok": True, "exists": os.path.exists(_adopt_case(path))}

    async def check_game_file(self, install_dir: str, rel_path: str) -> dict:
        """Does a file exist inside a game's install dir? Used to detect
        native-Linux builds (e.g. UnityPlayer.so) that mod loaders can't
        hook."""
        if not _safe_rel_path(rel_path or ""):
            return {"ok": False, "error": "Invalid path"}
        path = os.path.join(STEAM_COMMON, install_dir, *rel_path.split("/"))
        return {"ok": True, "exists": os.path.exists(path)}

    async def get_show_adult(self) -> dict:
        return {"ok": True, "show_adult": _show_adult()}

    async def set_show_adult(self, value: bool) -> dict:
        # See _show_adult: locked off until the Nexus Mods API exposes
        # the account's age-verified content preferences.
        return {
            "ok": False,
            "error": "Adult content is unavailable pending age-verification "
            "support in the Nexus Mods API",
        }

    async def dismiss_update(
        self, game_domain: str, folder: str, version: str
    ) -> dict:
        """Remember that the user declined this version - the update stops
        appearing until a NEWER version exists."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        settings = _load_settings()
        rec = settings.get("installed", {}).get(game_domain, {}).get(folder)
        if not rec:
            return {"ok": False, "error": f"{folder} is not tracked"}
        rec["ignore_update"] = version
        _save_settings(settings)
        decky.logger.info(
            f"update {version!r} dismissed for {folder!r} ({game_domain})"
        )
        return {"ok": True}

    async def get_display_fix(
        self, app_id: int, prefs_subpath: str, section: str, settings: dict
    ) -> dict:
        """Check a game's prefs ini for overlay-hostile display settings
        (e.g. Skyrim's exclusive fullscreen crashes when the Steam UI takes
        over the screen)."""
        try:
            path = _game_prefs_path(app_id, prefs_subpath)
            if not os.path.isfile(path):
                return {"ok": True, "exists": False, "compliant": True}
            current = _read_ini_settings(path, section, list(settings.keys()))
            compliant = all(
                current.get(k, "").lower() == str(v).lower()
                for k, v in settings.items()
            )
            return {
                "ok": True,
                "exists": True,
                "compliant": compliant,
                "current": current,
            }
        except OSError as e:
            return {"ok": False, "error": str(e)}

    async def apply_display_fix(
        self,
        app_id: int,
        prefs_subpath: str,
        section: str,
        settings: dict,
        create: bool = False,
    ) -> dict:
        """Patch a prefs/config ini (backs the file up once as
        .decky-nexus.bak). create=True writes the file if it doesn't exist
        yet - setup inis like Fallout4Custom.ini start out absent."""
        try:
            path = _game_prefs_path(app_id, prefs_subpath)
            if not os.path.isfile(path) and not create:
                return {
                    "ok": False,
                    "error": "Prefs file not found - launch the game once first",
                }
            _patch_ini_settings(path, section, settings)
            decky.logger.info(
                f"display fix applied to {prefs_subpath!r}: {settings}"
            )
            return {"ok": True}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    async def get_framework_setup(self, game_domain: str) -> dict:
        state = _load_settings().get("framework_setup", {}).get(game_domain, {})
        launch_set = bool(state.get("launch_options_set"))
        return {
            "ok": True,
            "launch_options_set": launch_set,
            "enabled": bool(state.get("enabled", launch_set)),
        }

    async def mark_launch_options_set(self, game_domain: str) -> dict:
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        settings = _load_settings()
        settings.setdefault("framework_setup", {})[game_domain] = {
            "launch_options_set": True,
            "enabled": True,
            "at": int(time.time()),
        }
        _save_settings(settings)
        return {"ok": True}

    async def get_launch_options_state(self, app_id: int) -> dict:
        """What launches this app: dlo's replayed command (when the
        decky-launch-options plugin is installed) + a read-only peek at
        Steam's own field for diagnostics."""
        dlo = _dlo_present()
        return {
            "ok": True,
            "dlo_present": dlo,
            "dlo_options": (
                _dlo_get_original(_dlo_settings_path(), app_id) if dlo else None
            ),
            "steam_options": _read_steam_launch_options(app_id),
        }

    async def set_framework_launch_options(
        self, app_id: int, game_domain: str, options: str
    ) -> dict:
        """dlo devices: write the framework's launch command into dlo's
        profile (Steam's field already holds dlo's wrapper) and mark the
        setup step done. Non-dlo devices get use_steam_client back and the
        frontend sets Steam's field via SteamClient."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not _dlo_present():
            return {"ok": False, "use_steam_client": True}
        ok, previous = _dlo_set_original(
            _dlo_settings_path(), app_id, options
        )
        if not ok:
            return {
                "ok": False,
                "error": "Could not update the launch-options plugin's settings",
            }
        settings = _load_settings()
        settings.setdefault("framework_setup", {})[game_domain] = {
            "launch_options_set": True,
            "enabled": True,
            "at": int(time.time()),
        }
        _save_settings(settings)
        decky.logger.info(
            f"launch options set via dlo for {game_domain!r} (app {app_id})"
        )
        return {"ok": True, "previous": previous}

    async def clear_framework_launch_options(
        self, app_id: int, game_domain: str
    ) -> dict:
        """Undo the framework's launch command so the game boots vanilla.
        On dlo devices this clears dlo's replayed profile (clearing Steam's
        field alone would leave the stale command in the replay); otherwise
        use_steam_client tells the frontend to clear Steam's field. Always
        unmarks the setup step."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        cleared_dlo = False
        previous = None
        if _dlo_present():
            ok, previous = _dlo_set_original(_dlo_settings_path(), app_id, "")
            if not ok:
                return {
                    "ok": False,
                    "error": "Could not update the launch-options plugin's settings",
                }
            cleared_dlo = True
        settings = _load_settings()
        state = settings.setdefault("framework_setup", {}).setdefault(
            game_domain, {}
        )
        state["launch_options_set"] = False
        _save_settings(settings)
        decky.logger.info(
            f"launch options cleared for {game_domain!r} (app {app_id}, "
            f"dlo={cleared_dlo}, was {previous!r})"
        )
        return {
            "ok": True,
            "cleared_dlo": cleared_dlo,
            "use_steam_client": not cleared_dlo,
        }

    async def set_framework_enabled(self, game_domain: str, enabled: bool) -> dict:
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        settings = _load_settings()
        state = settings.setdefault("framework_setup", {}).setdefault(
            game_domain, {}
        )
        state["enabled"] = bool(enabled)
        _save_settings(settings)
        decky.logger.info(
            f"framework for {game_domain!r} marked "
            f"{'enabled' if enabled else 'disabled'}"
        )
        return {"ok": True}

    async def reset_game_modding(
        self,
        game_domain: str,
        install_dir: str,
        mods_subdir: str,
        install_mode: str = "folder",
        app_id: int = 0,
        plugins_subpath: str = "",
        plugins_style: str = "starred",
        framework_file_prefixes: list = None,
    ) -> dict:
        """One-button return to vanilla: uninstall every tracked mod (all
        record modes), remove framework loader files by prefix (copyRoot
        installs keep no manifest), delete the plugins file (the game
        regenerates it), clear this game's plugin state, and clear the
        launch command (dlo's replayed profile here; non-dlo devices get
        use_steam_client back and the frontend clears Steam's field).
        Files installed outside this plugin are not touched."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        settings = _load_settings()
        install_path, mods_path, disabled_path = _game_paths(
            install_dir, mods_subdir
        )
        if not os.path.isdir(install_path):
            return {"ok": False, "error": "Game install folder not found"}
        records = dict(settings.get("installed", {}).get(game_domain, {}))
        removed = 0
        errors = []
        for key, rec in sorted(records.items()):
            try:
                mode = rec.get("mode") or "folder"
                if mode == "files":
                    if _remove_files_record(
                        game_domain, key, install_path, settings
                    ):
                        removed += 1
                elif mode == "dataDir":
                    if _remove_data_dir_record(
                        game_domain, key, mods_path, app_id,
                        plugins_subpath, settings,
                    ):
                        removed += 1
                else:
                    target = rec.get("target")
                    folder = rec.get("folder") or key
                    base = (
                        os.path.join(install_path, *target.split("/"))
                        if target
                        else mods_path
                    )
                    for cand in (
                        os.path.join(base, folder),
                        os.path.join(base + "-disabled", folder),
                        os.path.join(disabled_path, folder),
                    ):
                        if os.path.isdir(cand):
                            _force_rmtree(cand)
                    settings.get("installed", {}).get(game_domain, {}).pop(
                        key, None
                    )
                    removed += 1
            except OSError as e:
                errors.append(f"{key}: {e}")
        framework_files = []
        for prefix in framework_file_prefixes or []:
            pl = str(prefix).lower()
            if not pl or "/" in pl or "\\" in pl or pl.startswith("."):
                continue
            for name in sorted(os.listdir(install_path)):
                p = os.path.join(install_path, name)
                if name.lower().startswith(pl) and os.path.isfile(p):
                    try:
                        os.remove(p)
                        framework_files.append(name)
                    except OSError as e:
                        errors.append(f"{name}: {e}")
        if install_mode == "dataDir" and plugins_subpath and app_id:
            p = _plugins_txt_path(app_id, plugins_subpath)
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError as e:
                errors.append(f"plugins.txt: {e}")
        for section in ("installed", "collections", "framework_setup",
                        "collection_attention"):
            settings.get(section, {}).pop(game_domain, None)
        _save_settings(settings)
        cleared_dlo = False
        if app_id and _dlo_present():
            ok, _prev = _dlo_set_original(_dlo_settings_path(), app_id, "")
            cleared_dlo = ok
        decky.logger.info(
            f"reset {game_domain!r}: {removed} mods removed, framework "
            f"files {framework_files}, dlo cleared={cleared_dlo}, "
            f"{len(errors)} errors"
        )
        return {
            "ok": True,
            "removed": removed,
            "framework_files": framework_files,
            "cleared_dlo": cleared_dlo,
            "use_steam_client": bool(app_id) and not cleared_dlo,
            "errors": errors,
        }

    async def set_collection_attention(
        self, game_domain: str, slug: str, items: list
    ) -> dict:
        """Persist which of a collection's mods still need manual choices
        (FOMOD wizards / option folders) so the collection page can show
        and resolve them on any later visit. An empty list clears."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not slug:
            return {"ok": False, "error": "Missing collection slug"}
        settings = _load_settings()
        section = settings.setdefault("collection_attention", {}).setdefault(
            game_domain, {}
        )
        clean = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            clean.append(
                {
                    "file_id": int(item.get("file_id") or 0),
                    "mod_id": int(item.get("mod_id") or 0),
                    "mod_name": str(item.get("mod_name") or ""),
                    "file_name": str(item.get("file_name") or ""),
                    "version": str(item.get("version") or ""),
                    "reason": str(item.get("reason") or "choices"),
                    "options": [str(o) for o in (item.get("options") or [])],
                }
            )
        if clean:
            section[slug] = clean
        else:
            section.pop(slug, None)
        _save_settings(settings)
        return {"ok": True, "count": len(clean)}

    async def get_collection_attention(
        self, game_domain: str, slug: str
    ) -> dict:
        items = (
            _load_settings()
            .get("collection_attention", {})
            .get(game_domain, {})
            .get(slug, [])
        )
        return {"ok": True, "items": items}

    async def register_collection(
        self,
        game_domain: str,
        slug: str,
        title: str,
        thumb_url: str = "",
        mod_count: int = 0,
    ) -> dict:
        """Remember a collection's display info when its install starts -
        records only carry the slug; My Mods needs the title and banner."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not slug:
            return {"ok": False, "error": "Missing collection slug"}
        settings = _load_settings()
        settings.setdefault("collections", {}).setdefault(game_domain, {})[
            slug
        ] = {
            "title": title or slug,
            "thumb_url": thumb_url or "",
            "mod_count": int(mod_count or 0),
            "at": int(time.time()),
        }
        _save_settings(settings)
        return {"ok": True}

    # ---- Installed mods / enable & disable ----------------------------------

    async def get_installed_mods(
        self,
        game_domain: str,
        install_dir: str,
        mods_subdir: str,
        install_mode: str = "folder",
        app_id: int = 0,
        plugins_subpath: str = "",
        plugins_style: str = "starred",
        hidden_folders: list = None,
    ) -> dict:
        if install_mode == "dataDir":
            settings = _load_settings()
            records = settings.get("installed", {}).get(game_domain, {})
            active = set()
            if plugins_subpath:
                active = _active_plugins(
                    _plugins_txt_path(app_id, plugins_subpath), plugins_style
                )
            results = []
            for key, rec in records.items():
                mode = rec.get("mode")
                if mode == "files":
                    # Root-files installs (vortex-override payloads) have
                    # no plugin to toggle - present but always active.
                    results.append(
                        {
                            "folder": key,
                            "enabled": True,
                            "tracked": True,
                            "name": rec.get("name") or key,
                            "version": rec.get("version") or "",
                            "mod_id": rec.get("mod_id"),
                            "togglable": False,
                            "source": rec.get("source") or "",
                            "collection_slug": rec.get("collection_slug") or "",
                        }
                    )
                    continue
                if mode != "dataDir":
                    continue
                plugins = rec.get("plugins") or []
                enabled = (not plugins) or any(
                    p.lower() in active for p in plugins
                )
                results.append(
                    {
                        "folder": key,
                        "enabled": enabled,
                        "tracked": True,
                        "name": rec.get("name") or key,
                        "version": rec.get("version") or "",
                        "mod_id": rec.get("mod_id"),
                        "togglable": bool(plugins),
                        "source": rec.get("source") or "",
                        "collection_slug": rec.get("collection_slug") or "",
                    }
                )
            results.sort(key=lambda m: (m["name"] or "").lower())
            return {
                "ok": True,
                "mods": results,
                "collections": settings.get("collections", {}).get(
                    game_domain, {}
                ),
            }

        _, mods_path, disabled_path = _game_paths(install_dir, mods_subdir)
        records = _load_settings().get("installed", {}).get(game_domain, {})

        hidden = {h.lower() for h in (hidden_folders or [])}

        def scan(base: str, enabled: bool):
            if not os.path.isdir(base):
                return
            for folder in sorted(os.listdir(base)):
                if not os.path.isdir(os.path.join(base, folder)):
                    continue
                if folder.lower() in hidden:
                    continue
                rec = records.get(folder)
                results.append(
                    {
                        "folder": folder,
                        "enabled": enabled,
                        "tracked": rec is not None,
                        "name": (rec or {}).get("name") or folder,
                        "version": (rec or {}).get("version") or "",
                        "mod_id": (rec or {}).get("mod_id"),
                        "source": (rec or {}).get("source") or "",
                        "collection_slug": (rec or {}).get("collection_slug")
                        or "",
                    }
                )

        results: list = []
        scan(mods_path, True)
        scan(disabled_path, False)
        # Mods routed to alternate targets (UE4SS dirs, LogicMods) don't
        # live in the scanned dirs - list them from their records.
        install_path = os.path.join(STEAM_COMMON, install_dir)
        for key, rec in records.items():
            target = rec.get("target")
            if not target:
                continue
            if rec.get("mode") == "files":
                results.append(
                    {
                        "folder": key,
                        "enabled": True,
                        "tracked": True,
                        "name": rec.get("name") or key,
                        "version": rec.get("version") or "",
                        "mod_id": rec.get("mod_id"),
                        "togglable": False,
                        "source": rec.get("source") or "",
                        "collection_slug": rec.get("collection_slug") or "",
                    }
                )
                continue
            base = os.path.join(install_path, *target.split("/"))
            folder = rec.get("folder") or key
            if os.path.isdir(os.path.join(base, folder)):
                enabled = True
            elif os.path.isdir(os.path.join(base + "-disabled", folder)):
                enabled = False
            else:
                continue  # record is stale; hide rather than mislead
            results.append(
                {
                    "folder": key,
                    "enabled": enabled,
                    "tracked": True,
                    "name": rec.get("name") or key,
                    "version": rec.get("version") or "",
                    "mod_id": rec.get("mod_id"),
                    "source": rec.get("source") or "",
                    "collection_slug": rec.get("collection_slug") or "",
                }
            )
        # Stable alphabetical order regardless of enabled state - toggling a
        # mod must not make it jump around the list.
        results.sort(key=lambda m: (m["name"] or m["folder"]).lower())
        return {
            "ok": True,
            "mods": results,
            "collections": _load_settings().get("collections", {}).get(
                game_domain, {}
            ),
        }

    async def set_mod_enabled(
        self,
        install_dir: str,
        mods_subdir: str,
        folder: str,
        enabled: bool,
        install_mode: str = "folder",
        game_domain: str = "",
        app_id: int = 0,
        plugins_subpath: str = "",
        plugins_style: str = "starred",
        hidden_folders: list = None,
    ) -> dict:
        if install_mode == "dataDir":
            rec = (
                _load_settings()
                .get("installed", {})
                .get(game_domain, {})
                .get(folder)
            )
            if not rec:
                return {"ok": False, "error": f"{folder} is not tracked"}
            plugins = rec.get("plugins") or []
            if not plugins:
                return {
                    "ok": False,
                    "error": "This mod has no plugin file to toggle - its "
                    "assets are always active",
                }
            _set_plugins_active(
                _plugins_txt_path(app_id, plugins_subpath),
                plugins,
                enabled,
                plugins_style,
            )
            decky.logger.info(
                f"{'enabled' if enabled else 'disabled'} plugins for {folder!r}"
            )
            return {"ok": True}

        # Folder names come from our own directory scan, but never trust a
        # path component: refuse separators outright.
        if os.sep in folder or "/" in folder or folder in (".", ".."):
            return {"ok": False, "error": "Invalid mod folder name"}
        rec = (
            _load_settings()
            .get("installed", {})
            .get(game_domain, {})
            .get(folder)
            if game_domain
            else None
        )
        if rec and rec.get("target"):
            if rec.get("mode") == "files":
                return {
                    "ok": False,
                    "error": "This mod has no toggle - uninstall it instead",
                }
            install_path = os.path.join(STEAM_COMMON, install_dir)
            base = os.path.join(install_path, *rec["target"].split("/"))
            real = rec.get("folder") or folder
            src_base, dst_base = (
                (base + "-disabled", base) if enabled else (base, base + "-disabled")
            )
            src = os.path.join(src_base, real)
            dst = os.path.join(dst_base, real)
            if not os.path.isdir(src):
                return {"ok": False, "error": f"{real} not found"}
            os.makedirs(dst_base, exist_ok=True)
            shutil.move(src, dst)
            decky.logger.info(
                f"{'enabled' if enabled else 'disabled'} {real!r} in "
                f"{rec['target']!r}"
            )
            return {"ok": True}
        _, mods_path, disabled_path = _game_paths(install_dir, mods_subdir)
        src_base, dst_base = (
            (disabled_path, mods_path) if enabled else (mods_path, disabled_path)
        )
        src = os.path.join(src_base, folder)
        dst = os.path.join(dst_base, folder)
        if not os.path.isdir(src):
            return {"ok": False, "error": f"{folder} not found in {src_base}"}
        os.makedirs(dst_base, exist_ok=True)
        if os.path.isdir(dst):
            return {"ok": False, "error": f"{folder} already exists in {dst_base}"}
        os.rename(src, dst)
        # Launcher-selected modules (Bannerlord): keep LauncherData.xml in
        # step so the launcher doesn't re-run a disabled module.
        if rec and rec.get("moduleId") and rec.get("launcherXml"):
            _set_module_selected(
                _launcher_xml_path(app_id, rec["launcherXml"]),
                rec["moduleId"],
                enabled,
            )
        decky.logger.info(f"{'enabled' if enabled else 'disabled'} mod {folder!r}")
        return {"ok": True}

    async def set_all_mods_enabled(
        self,
        install_dir: str,
        mods_subdir: str,
        enabled: bool,
        install_mode: str = "folder",
        game_domain: str = "",
        app_id: int = 0,
        plugins_subpath: str = "",
        plugins_style: str = "starred",
    ) -> dict:
        """Move every mod folder at once - 'play vanilla' / 'restore mods'.
        In dataDir mode, toggles every tracked mod's plugins instead."""
        if install_mode == "dataDir":
            records = _load_settings().get("installed", {}).get(game_domain, {})
            path = _plugins_txt_path(app_id, plugins_subpath)
            moved = 0
            for rec in records.values():
                plugins = rec.get("plugins") or []
                if rec.get("mode") == "dataDir" and plugins:
                    _set_plugins_active(path, plugins, enabled, plugins_style)
                    moved += 1
            return {"ok": True, "moved": moved, "errors": []}
        _, mods_path, disabled_path = _game_paths(install_dir, mods_subdir)
        src_base, dst_base = (
            (disabled_path, mods_path) if enabled else (mods_path, disabled_path)
        )
        if not os.path.isdir(src_base):
            return {"ok": True, "moved": 0, "errors": []}
        os.makedirs(dst_base, exist_ok=True)
        moved = 0
        errors = []
        for folder in sorted(os.listdir(src_base)):
            src = os.path.join(src_base, folder)
            if not os.path.isdir(src):
                continue
            dst = os.path.join(dst_base, folder)
            if os.path.isdir(dst):
                errors.append(f"{folder}: already exists in target")
                continue
            os.rename(src, dst)
            moved += 1
        decky.logger.info(
            f"{'enabled' if enabled else 'disabled'} all mods: {moved} moved, "
            f"{len(errors)} conflicts"
        )
        return {"ok": True, "moved": moved, "errors": errors}

    async def uninstall_mod(
        self,
        game_domain: str,
        install_dir: str,
        mods_subdir: str,
        folder: str,
        install_mode: str = "folder",
        app_id: int = 0,
        plugins_subpath: str = "",
        plugins_style: str = "starred",
    ) -> dict:
        """Delete a mod's folder (or, in dataDir mode, its manifest files)
        and forget its record."""
        if os.sep in folder or "/" in folder or folder in (".", ".."):
            return {"ok": False, "error": "Invalid mod folder name"}
        if install_mode == "dataDir":
            settings = _load_settings()
            install_path, data_path, _unused = _game_paths(
                install_dir, mods_subdir
            )
            # Root-files records (vortex-override installs like the Engine
            # Fixes preloader) coexist with dataDir manifests here.
            if _remove_files_record(game_domain, folder, install_path, settings):
                _save_settings(settings)
                decky.logger.info(f"uninstalled root-files mod {folder!r}")
                return {"ok": True}
            if not _remove_data_dir_record(
                game_domain, folder, data_path, app_id, plugins_subpath, settings
            ):
                return {"ok": False, "error": f"{folder} is not tracked"}
            _save_settings(settings)
            decky.logger.info(f"uninstalled dataDir mod {folder!r}")
            return {"ok": True}
        # Alternate-target records (UE4SS dirs, LogicMods) uninstall from
        # where they were routed, not the scanned mods dir.
        settings = _load_settings()
        rec = settings.get("installed", {}).get(game_domain, {}).get(folder)
        if rec and rec.get("target"):
            install_path = os.path.join(STEAM_COMMON, install_dir)
            base = os.path.join(install_path, *rec["target"].split("/"))
            if rec.get("mode") == "files":
                for name in rec.get("files") or []:
                    if not _safe_rel_path(name) or "/" in name:
                        continue
                    try:
                        os.remove(os.path.join(base, name))
                    except OSError:
                        pass
            else:
                real = rec.get("folder") or folder
                _force_rmtree(os.path.join(base, real))
                _force_rmtree(os.path.join(base + "-disabled", real))
            settings["installed"][game_domain].pop(folder, None)
            _save_settings(settings)
            decky.logger.info(
                f"uninstalled {folder!r} from {rec.get('target')!r}"
            )
            return {"ok": True}
        _, mods_path, disabled_path = _game_paths(install_dir, mods_subdir)
        removed = False
        for base in (mods_path, disabled_path):
            target = os.path.join(base, folder)
            if os.path.isdir(target):
                _force_rmtree(target)
                removed = True
        if not removed:
            return {"ok": False, "error": f"{folder} not found"}
        settings = _load_settings()
        dropped = settings.get("installed", {}).get(game_domain, {}).pop(
            folder, None
        )
        _save_settings(settings)
        if dropped and dropped.get("moduleId") and dropped.get("launcherXml"):
            _remove_module_entry(
                _launcher_xml_path(app_id, dropped["launcherXml"]),
                dropped["moduleId"],
            )
        decky.logger.info(f"uninstalled mod {folder!r}")
        return {"ok": True}

    # ---- Save profiles (vanilla <-> modded) ----------------------------------

    async def get_save_status(self, app_id: int, process_name: str) -> dict:
        """Report per-Steam-account save state for a game that splits
        vanilla and modded saves (StS2 pattern)."""
        accounts = []
        if os.path.isdir(STEAM_USERDATA):
            for account_id in sorted(os.listdir(STEAM_USERDATA)):
                if not re.fullmatch(r"\d+", account_id):
                    continue
                remote, profiles, modded = _save_layout(account_id, app_id)
                if not os.path.isdir(remote) or not profiles:
                    continue
                accounts.append(
                    {
                        "account_id": account_id,
                        "vanilla_profiles": len(profiles),
                        "has_modded": os.path.isdir(modded),
                        "last_write": _newest_mtime(remote),
                    }
                )
        active = max(accounts, key=lambda a: a["last_write"], default=None)
        return {
            "ok": True,
            "accounts": accounts,
            "active_account": active["account_id"] if active else None,
            "game_running": await _is_process_running(process_name),
        }

    async def copy_saves_to_modded(
        self, app_id: int, account_id: str, process_name: str
    ) -> dict:
        """Copy vanilla profile(s) over the modded save tree so existing
        progress carries into modded play. The previous modded tree is moved
        into a timestamped backup first. One-way by design: modded saves may
        reference mod content that vanilla can't load."""
        try:
            if not re.fullmatch(r"\d+", account_id or ""):
                return {"ok": False, "error": "Invalid account id"}
            if await _is_process_running(process_name):
                return {"ok": False, "error": "Close the game first"}

            remote, profiles, modded = _save_layout(account_id, app_id)
            if not profiles:
                return {"ok": False, "error": "No vanilla save profiles found"}

            backup = None
            if os.path.isdir(modded):
                os.makedirs(SAVE_BACKUPS_DIR, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                backup = os.path.join(
                    SAVE_BACKUPS_DIR, f"{app_id}-{account_id}-{stamp}"
                )
                shutil.move(modded, backup)
            os.makedirs(modded)
            for profile in profiles:
                shutil.copytree(
                    os.path.join(remote, profile), os.path.join(modded, profile)
                )
            decky.logger.info(
                f"copied {len(profiles)} vanilla profile(s) -> modded for account "
                f"{account_id} (backup: {backup})"
            )
            return {"ok": True, "profiles": len(profiles), "backup": backup}
        except Exception as e:  # noqa: BLE001 - surfaced to UI + logged
            decky.logger.exception("copy_saves_to_modded crashed")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def uninstall_all_mods(
        self,
        game_domain: str,
        install_dir: str,
        mods_subdir: str,
        protected=None,
        install_mode: str = "folder",
        app_id: int = 0,
        plugins_subpath: str = "",
        plugins_style: str = "starred",
    ) -> dict:
        """Remove every mod folder (enabled and disabled) except protected
        ones (framework components like SMAPI's SaveBackup)."""
        try:
            protected_set = {p.lower() for p in (protected or [])}
            if install_mode == "dataDir":
                settings = _load_settings()
                _, data_path, _unused = _game_paths(install_dir, mods_subdir)
                records = settings.get("installed", {}).get(game_domain, {})
                keys = [
                    k for k, r in records.items() if r.get("mode") == "dataDir"
                ]
                removed_list, kept = [], []
                for key in keys:
                    if key.lower() in protected_set:
                        kept.append(key)
                        continue
                    if _remove_data_dir_record(
                        game_domain, key, data_path, app_id,
                        plugins_subpath, settings,
                    ):
                        removed_list.append(key)
                _save_settings(settings)
                decky.logger.info(
                    f"uninstall_all (dataDir): removed {removed_list}, kept {kept}"
                )
                return {"ok": True, "removed": len(removed_list), "kept": kept}
            _, mods_path, disabled_path = _game_paths(install_dir, mods_subdir)
            settings = _load_settings()
            records = settings.get("installed", {}).get(game_domain, {})
            removed, kept = [], []
            for base in (mods_path, disabled_path):
                if not os.path.isdir(base):
                    continue
                for folder in sorted(os.listdir(base)):
                    target = os.path.join(base, folder)
                    if not os.path.isdir(target):
                        continue
                    if folder.lower() in protected_set:
                        kept.append(folder)
                        continue
                    _force_rmtree(target)
                    records.pop(folder, None)
                    removed.append(folder)
            _save_settings(settings)
            decky.logger.info(
                f"uninstall_all_mods({game_domain!r}): removed {removed}, kept {kept}"
            )
            return {"ok": True, "removed": len(removed), "kept": kept}
        except Exception as e:  # noqa: BLE001 - surfaced to UI + logged
            decky.logger.exception("uninstall_all_mods crashed")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ---- Game detection ----------------------------------------------------

    # Reports whether a supported game is installed, whether its mods folder
    # exists yet, and (when the game needs a community mod loader like SMAPI)
    # whether that framework is present in the install dir.
    async def get_game_status(
        self, install_dir: str, mods_subdir: str, framework_file: str = ""
    ) -> dict:
        install_path, mods_path, _ = _game_paths(install_dir, mods_subdir)
        installed = os.path.isdir(install_path)
        status = {
            "installed": installed,
            "install_path": install_path,
            "mods_path": mods_path,
            "mods_dir_exists": os.path.isdir(mods_path),
        }
        if framework_file:
            if "/" in framework_file:
                status["framework_installed"] = installed and os.path.exists(
                    os.path.join(install_path, *framework_file.split("/"))
                )
            else:
                status["framework_installed"] = installed and any(
                    name.startswith(framework_file)
                    for name in os.listdir(install_path)
                )
        decky.logger.info(f"game status for {install_dir!r}: {status}")
        return status

    # ---- Dev loop ----------------------------------------------------------

    # Dev-loop smoke test. Returns environment info and emits an event so the
    # backend -> frontend push channel gets exercised too.
    async def ping(self, emit_event: bool = False) -> dict:
        info = {
            "user": decky.DECKY_USER,
            "home": decky.DECKY_USER_HOME,
            "plugin_name": decky.DECKY_PLUGIN_NAME,
            "plugin_version": decky.DECKY_PLUGIN_VERSION,
            "decky_version": decky.DECKY_VERSION,
        }
        decky.logger.info(f"ping from frontend: {info}")
        if emit_event:
            await decky.emit("backend_event", "pong")
        return info

    # ---- Lifecycle ---------------------------------------------------------

    async def _main(self):
        decky.logger.info("Nexus Mods plugin loaded")

    async def _unload(self):
        decky.logger.info("Nexus Mods plugin unloading")

    async def _uninstall(self):
        decky.logger.info("Nexus Mods plugin uninstalled")
