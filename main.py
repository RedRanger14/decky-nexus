import asyncio
import json
import os
import re
import shutil
import ssl
import time

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
# The Nexus acceptable-use policy requires clients to identify themselves,
# and the v2 endpoint's WAF rejects requests without a real User-Agent.
APP_HEADERS = {
    "Application-Name": "decky-nexus",
    "Application-Version": "0.1.0",
    "User-Agent": "decky-nexus/0.1.0 (SteamOS; Decky Loader plugin)",
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


def _build_mods_query(with_search: bool, trending_since=None) -> str:
    """Compose the browse query. WILDCARD does substring matching
    server-side; date filters take epoch seconds (verified - ISO datetimes
    break the backing Lucene query). 'Trending' = created within the window,
    sorted by downloads."""
    filters = ["gameDomainName: [{ value: $domain, op: EQUALS }]"]
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


def _pick_main_file(file_list: list):
    """Latest MAIN-category file; never trust is_primary alone."""
    mains = [f for f in file_list if f.get("category_name") == "MAIN"]
    if mains:
        return max(mains, key=lambda f: f["file_id"])
    return next(
        (f for f in file_list if f.get("category_name") != "OLD_VERSION"), None
    )


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
            "query": _build_mods_query(bool(search), trending_since),
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
                        "TOO_SOON_AFTER_DOWNLOAD": "Nexus Mods asks you to spend some time with a mod first - try again later",
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
            reqs = (
                nodes[0]["modRequirements"]["nexusRequirements"]["nodes"]
                if nodes
                else []
            )
            return {"ok": True, "requirements": reqs}
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
                "{ legacyMods(ids: [{gameId: %d, modId: %d}]) { nodes {%s\n description } } }"
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
                cur = _norm_version(node.get("version"))
                installed = _norm_version(rec.get("version"))
                updates[folder] = {
                    "installed": rec.get("version"),
                    "current": node.get("version"),
                    "update_available": bool(cur) and cur != installed,
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
        """Fetch specific mods (curated recommendations) in the given order."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        try:
            ids = [int(i) for i in (mod_ids or [])][:10]
        except (TypeError, ValueError):
            return {"ok": False, "error": "Invalid mod ids"}
        if not ids:
            return {"ok": True, "mods": []}
        api_key = _load_settings().get("api_key")
        try:
            game_id = await _resolve_game_id(game_domain, api_key)
            id_args = ", ".join(
                "{gameId: %d, modId: %d}" % (game_id, i) for i in ids
            )
            data = await _gql_query(
                "{ legacyMods(ids: [%s]) { nodes {%s} } }" % (id_args, MOD_FIELDS),
                api_key,
            )
            nodes = data["legacyMods"]["nodes"]
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

        mods = [
            _map_v1_mod(m)
            for m in body
            if m.get("name") and m.get("available", True)
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
    ) -> dict:
        """Wrapper so any unexpected failure reaches the UI as a real message
        instead of decky's generic 'Python Exception'."""
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
            )
        except Exception as e:  # noqa: BLE001 - surfaced to UI + logged
            decky.logger.exception(f"install_mod({mod_name!r}) crashed")
            await _emit_progress(mod_id, "error", 0, str(e))
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

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
    ) -> dict:
        settings = _load_settings()
        api_key = settings.get("api_key")
        if not api_key:
            return {"ok": False, "error": "Not signed in"}

        install_path, mods_path, disabled_path = _game_paths(install_dir, mods_subdir)
        if not os.path.isdir(install_path):
            return {"ok": False, "error": "Game install folder not found"}

        # 1) Ask for a download link (Premium-only endpoint; free users need
        #    key+expires params from a website nxm:// link - future work).
        link_url = (
            f"{NEXUS_API_BASE}/v1/games/{game_domain}/mods/{mod_id}"
            f"/files/{file_id}/download_link.json"
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
                    last_pct = -10
                    with open(archive_path, "wb") as out:
                        async for chunk in resp.content.iter_chunked(1 << 20):
                            out.write(chunk)
                            done += len(chunk)
                            if total:
                                pct = int(done * 100 / total)
                                if pct >= last_pct + 5:
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

        # Single top-level folder -> that IS the mod folder. Loose files ->
        # wrap them in a folder named after the mod.
        os.makedirs(mods_path, exist_ok=True)
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
        installed = settings.setdefault("installed", {}).setdefault(game_domain, {})
        installed[folder] = {
            "mod_id": mod_id,
            "file_id": file_id,
            "name": mod_name,
            "version": mod_version,
            "file_name": file_name,
            "installed_at": int(time.time()),
        }
        _save_settings(settings)

        decky.logger.info(f"installed {mod_name!r} -> {mods_path}/{folder}")
        await _emit_progress(mod_id, "done", 100)
        return {"ok": True, "folder": folder}

    async def install_framework(
        self, game_domain: str, mod_id: int, install_dir: str
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
            main = _pick_main_file(file_list)
            if not main:
                return {"ok": False, "error": "No downloadable file found"}

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

    # ---- Installed mods / enable & disable ----------------------------------

    async def get_installed_mods(
        self, game_domain: str, install_dir: str, mods_subdir: str
    ) -> dict:
        _, mods_path, disabled_path = _game_paths(install_dir, mods_subdir)
        records = _load_settings().get("installed", {}).get(game_domain, {})

        def scan(base: str, enabled: bool):
            if not os.path.isdir(base):
                return
            for folder in sorted(os.listdir(base)):
                if not os.path.isdir(os.path.join(base, folder)):
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
                    }
                )

        results: list = []
        scan(mods_path, True)
        scan(disabled_path, False)
        # Stable alphabetical order regardless of enabled state - toggling a
        # mod must not make it jump around the list.
        results.sort(key=lambda m: (m["name"] or m["folder"]).lower())
        return {"ok": True, "mods": results}

    async def set_mod_enabled(
        self, install_dir: str, mods_subdir: str, folder: str, enabled: bool
    ) -> dict:
        # Folder names come from our own directory scan, but never trust a
        # path component: refuse separators outright.
        if os.sep in folder or "/" in folder or folder in (".", ".."):
            return {"ok": False, "error": "Invalid mod folder name"}
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
        decky.logger.info(f"{'enabled' if enabled else 'disabled'} mod {folder!r}")
        return {"ok": True}

    async def set_all_mods_enabled(
        self, install_dir: str, mods_subdir: str, enabled: bool
    ) -> dict:
        """Move every mod folder at once - 'play vanilla' / 'restore mods'."""
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
        self, game_domain: str, install_dir: str, mods_subdir: str, folder: str
    ) -> dict:
        """Delete a mod's folder (wherever it lives) and forget its record."""
        if os.sep in folder or "/" in folder or folder in (".", ".."):
            return {"ok": False, "error": "Invalid mod folder name"}
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
        settings.get("installed", {}).get(game_domain, {}).pop(folder, None)
        _save_settings(settings)
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
    ) -> dict:
        """Remove every mod folder (enabled and disabled) except protected
        ones (framework components like SMAPI's SaveBackup)."""
        try:
            protected_set = {p.lower() for p in (protected or [])}
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
            status["framework_installed"] = installed and any(
                name.startswith(framework_file)
                for name in os.listdir(install_path)
            )
        decky.logger.info(f"game status for {install_dir!r}: {status}")
        return status

    # ---- Dev loop ----------------------------------------------------------

    # Dev-loop smoke test. Returns environment info and emits an event so the
    # backend -> frontend push channel gets exercised too.
    async def ping(self) -> dict:
        info = {
            "user": decky.DECKY_USER,
            "home": decky.DECKY_USER_HOME,
            "plugin_name": decky.DECKY_PLUGIN_NAME,
            "plugin_version": decky.DECKY_PLUGIN_VERSION,
            "decky_version": decky.DECKY_VERSION,
        }
        decky.logger.info(f"ping from frontend: {info}")
        await decky.emit("backend_event", "pong")
        return info

    # ---- Lifecycle ---------------------------------------------------------

    async def _main(self):
        decky.logger.info("Nexus Mods plugin loaded")

    async def _unload(self):
        decky.logger.info("Nexus Mods plugin unloading")

    async def _uninstall(self):
        decky.logger.info("Nexus Mods plugin uninstalled")
