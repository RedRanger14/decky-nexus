import asyncio
import glob
import hashlib
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

# legacyMods(ids: [...]) returns at most 20 nodes and says nothing about the
# rest. Not an error, not a page cursor - the extra ids are simply absent
# from the response.
#
# Found 2026-08-13: 27 Slay the Spire 2 mods were installed, the update check
# asked about all 27, and RitsuLib - two minor versions behind a game build
# that was printing "Loaded 21 mods WITH ERRORS" across the main menu - was
# one of the 7 the API dropped. It had reported "no updates available". The
# thumbnail query batched in 40s and was quietly losing half of every batch.
#
# Anything asking for ids in bulk goes through _legacy_mods_in_batches.
LEGACY_MODS_PAGE = 20

MOD_FIELDS = """
      modId
      name
      summary
      author
      version
      endorsements
      downloads
      thumbnailUrl
      thumbnailBlurredUrl
      pictureUrl
      updatedAt
      adultContent
"""

TRENDING_WINDOW_DAYS = 30

# Language tags observed live on the v2 search index (2026-08-05). Most
# mods carry NO tag (untagged = original/English uploads), so "english"
# mode EXCLUDES the tagged translations rather than requiring the tag -
# a strict English filter would hide three quarters of the catalog.
MOD_LANGUAGES = (
    "English", "French", "German", "Spanish", "Italian", "Russian",
    "Polish", "Portuguese", "Mandarin", "Japanese", "Korean", "Czech",
    "Turkish", "Ukrainian", "Hungarian", "Dutch",
)


def _build_mods_query(
    with_search: bool,
    trending_since=None,
    include_adult: bool = False,
    language: str = "",
) -> str:
    """Compose the browse query. WILDCARD does substring matching
    server-side; date filters take epoch seconds (verified - ISO datetimes
    break the backing Lucene query). 'Trending' = created within the window,
    sorted by downloads. Adult content is excluded unless the user opted
    in (mirrors the site's default). language: 'english' excludes tagged
    translations; a specific tag shows only those; ''/'all' = everything."""
    filters = ["gameDomainName: [{ value: $domain, op: EQUALS }]"]
    if not include_adult:
        filters.append("adultContent: [{ value: false }]")
    if language == "english":
        excl = " ".join(
            '{ value: "%s", op: NOT_EQUALS }' % lang
            for lang in MOD_LANGUAGES
            if lang != "English"
        )
        filters.append(f"languageName: [{excl}]")
    elif language and language != "all":
        filters.append('languageName: [{ value: "%s" }]' % language)
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
    # Deliberately NOT cached in memory. Caching the parsed document would
    # save a re-parse per install on big collections, but it also makes
    # every caller share one mutable object - so anything that loads,
    # mutates and then decides not to save would start leaking into the
    # next reader. That is a subtle, state-corrupting class of bug in
    # exchange for the smallest of the available speedups; the merge and
    # threading work is where the time actually was.
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


_INSTALL_SEQ = None


def _next_install_seq() -> int:
    """A strictly increasing number stamped on each install record.

    `installed_at` is int(time.time()), and small mods install several per
    second: on the device's New Vegas collection 627 of 764 records shared
    a second with another record. A timestamp therefore cannot say which
    of two mods wrote a shared file last, and every one of those ties was
    resolved by dict iteration order rather than by what happened.

    Seeded from the highest sequence already on disk rather than a counter
    of its own, so it survives a restart without a second thing to keep in
    step - and a settings file restored from backup carries its own
    ordering with it.
    """
    global _INSTALL_SEQ
    if _INSTALL_SEQ is None:
        highest = 0
        for records in (_load_settings().get("installed") or {}).values():
            if not isinstance(records, dict):
                continue
            for rec in records.values():
                if isinstance(rec, dict):
                    highest = max(highest, int(rec.get("install_seq") or 0))
        _INSTALL_SEQ = highest
    _INSTALL_SEQ += 1
    return _INSTALL_SEQ


# ---- RE Engine pak-patch chain (RE4 remake) --------------------------------
# The engine loads re_chunk_000.pak.patch_XXX.pak SEQUENTIALLY from the
# game root - a gap breaks everything past it. Mods take the next number
# after whatever exists (official update paks included); uninstalls must
# renumber the survivors to close the gap.

RE4_PAK_RE = re.compile(r"^re_chunk_000\.pak\.patch_(\d{3})\.pak$", re.I)

# REFramework's per-game config (created next to its dinput8.dll). Its
# built-in LooseFileLoader makes natives/ trees load without Fluffy -
# verified against the shipped DLL's strings on device (2026-08-05).
RE4_REF_CONFIG = "re4_fw_config.txt"
REF_LOOSE_KEY = "LooseFileLoader_Enabled"


def _ensure_config_key(path: str, key: str, value: str) -> None:
    """Set key=value in a flat (sectionless) config file, creating the
    file if needed and replacing an existing line for the key."""
    lines = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    out, found = [], False
    for line in lines:
        if line.split("=", 1)[0].strip().lower() == key.lower():
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")


def _pakpatch_payload(scratch: str):
    """(paks, natives_dirs, reframework_dirs) discovered in an extracted
    RE Engine archive. paks are absolute .pak paths; natives roots are
    loose-file mods (Fluffy format); reframework roots hold script mods
    (autorun lua / plugins) that REFramework loads from the game root."""
    paks, natives_dirs, ref_dirs = [], [], []
    for root, dirs, names in os.walk(scratch):
        for d in list(dirs):
            if d.lower() == "natives":
                natives_dirs.append(os.path.join(root, d))
                dirs.remove(d)  # don't descend: the tree moves whole
            elif d.lower() == "reframework":
                ref_dirs.append(os.path.join(root, d))
                dirs.remove(d)
        for name in names:
            if name.lower().endswith(".pak"):
                paks.append(os.path.join(root, name))
    paks.sort()
    natives_dirs.sort()
    ref_dirs.sort()
    return paks, natives_dirs, ref_dirs


def _pakpatch_name(n: int) -> str:
    return f"re_chunk_000.pak.patch_{n:03d}.pak"


def _pakpatch_renumber(game_domain: str, install_path: str, settings: dict) -> int:
    """Close gaps in the patch-pak chain after an uninstall: paks no
    record owns (the game's own updates) keep their numbers; every
    recorded mod pak is renamed onto consecutive numbers above them and
    the records are updated in place. Returns how many were renamed."""
    installed = settings.get("installed", {}).get(game_domain, {})
    owned = set()
    for rec in installed.values():
        if rec.get("pakpatch"):
            owned.update(n.lower() for n in rec.get("files") or [])
    officials = []
    mod_paks = []
    try:
        names = os.listdir(install_path)
    except OSError:
        return 0
    for name in names:
        m = RE4_PAK_RE.match(name)
        if not m:
            continue
        if name.lower() in owned:
            mod_paks.append((int(m.group(1)), name))
        else:
            officials.append(int(m.group(1)))
    mod_paks.sort()
    renames = []
    next_n = max(officials, default=-1) + 1
    for _num, name in mod_paks:
        want = _pakpatch_name(next_n)
        if name != want:
            renames.append((name, want))
        next_n += 1
    # Two phases via temp names: a shift-down chain would otherwise
    # collide with a not-yet-moved neighbour.
    for i, (src, _dst) in enumerate(renames):
        os.rename(
            os.path.join(install_path, src),
            os.path.join(install_path, f"{src}.renum{i}"),
        )
    for i, (src, dst) in enumerate(renames):
        os.rename(
            os.path.join(install_path, f"{src}.renum{i}"),
            os.path.join(install_path, dst),
        )
    if renames:
        mapping = {s.lower(): d for s, d in renames}
        for rec in installed.values():
            if rec.get("pakpatch"):
                rec["files"] = [
                    mapping.get(n.lower(), n) for n in rec.get("files") or []
                ]
        decky.logger.info(
            f"pak-patch chain renumbered: {len(renames)} pak(s) shifted"
        )
    return len(renames)


def _safe_uri(uri: str) -> str:
    """Nexus CDN links carry the RAW archive file name - spaces included
    ('.../Animated Main Menu Replacer for TTW-83614-....rar?expires=...')
    - and aiohttp rejects such URLs. Percent-encode the path; the signed
    query is already encoded and stays untouched. safe='/%' keeps any
    existing escapes intact (idempotent). Verified live: the encoded URL
    passes the CDN's signature check."""
    parts = urllib.parse.urlsplit(uri)
    return urllib.parse.urlunsplit(
        parts._replace(path=urllib.parse.quote(parts.path, safe="/%"))
    )


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


def _vanilla_baseline(game_domain: str) -> list:
    return _load_settings().get("vanilla_baseline", {}).get(game_domain) or []


def _steam_build_id(app_id: int) -> str:
    """The installed build of a Steam app, or "" if it cannot be read.

    Reset compares this against the build the vanilla baseline was taken
    on. A baseline says "this is what the mods folder held before any mod
    went in" - but it says nothing about what the GAME has added since,
    and games gain files constantly: patches, and DLC for titles still
    being updated. Trusting a stale baseline is how reset deleted nine
    paid-for DLC masters on device.
    """
    if not app_id:
        return ""
    path = os.path.join(
        os.path.dirname(STEAM_COMMON), f"appmanifest_{int(app_id)}.acf"
    )
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.search(r'"buildid"\s+"(\d+)"', line)
                if m:
                    return m.group(1)
    except OSError:
        return ""
    return ""


def _record_vanilla_baseline(
    game_domain: str, mods_path: str, app_id: int = 0, extra_dirs: list = None,
    root: str = "",
) -> None:
    """Snapshot what the game's mod folder held before we touched it.

    Reset can only remove mods it has records for, and a record is
    written after the files are copied - so an install that dies in
    between orphans its files invisibly. On device that left 20GB and
    ~400 mods behind after a reset that reported "1543 mods removed, 0
    errors", and the only reason anyone noticed was that the main menu
    looked wrong.

    A baseline costs one directory listing and lets reset say honestly
    whether it actually got back to vanilla, without this file needing to
    know what vanilla looks like for every game - which is exactly the
    guess that let five mod files through the manual clean.
    """
    if not game_domain or not os.path.isdir(mods_path):
        return
    settings = _load_settings()
    have = settings.setdefault("vanilla_baseline", {})
    if game_domain in have:
        return
    try:
        have[game_domain] = sorted(os.listdir(mods_path))
    except OSError:
        return
    # And the game's own folder, not just its mod folder.
    #
    # Script extenders, audio libraries and ENBs install BESIDE the game
    # exe, and reset only ever looked at Data - so they survived every
    # reset ever performed. Michael's Fallout 3 still had three mod DLLs
    # (bass.dll, bassenc.dll, bassmix.dll) in the game root after several
    # "clean" resets, which means no baseline he ever tested from was
    # actually clean.
    # And every other directory this game's mods write into, so reset can
    # tell an orphan from a game file there too.
    # The game root has to be passed in, not derived: Cyberpunk's mods dir
    # is archive/pc/mod, so one dirname lands three levels short and the
    # baseline recorded nothing - which let a reset delete the game's own
    # r6/scripts content as an "orphan".
    game_root = root or os.path.dirname(mods_path.rstrip(os.sep))
    for rel in extra_dirs or []:
        if not _safe_rel_path(str(rel)):
            continue
        d = os.path.join(game_root, *str(rel).split("/"))
        store = settings.setdefault("vanilla_extra_baseline", {}).setdefault(
            game_domain, {}
        )
        if not os.path.exists(d):
            # A directory a vanilla install does not have provably contains
            # nothing of the game's, so record that as a fact: an EMPTY
            # baseline, not a missing one.
            #
            # This used to raise and be swallowed, so four of Cyberpunk's
            # five mod directories had no baseline at all - r6/tweaks,
            # red4ext/plugins and bin/x64/plugins do not exist until a mod
            # creates them. Reset skips any directory it has no baseline
            # for, quite rightly ("we do not know what the GAME put
            # there"), so anything whose install record was lost in those
            # directories was an orphan nothing could ever find. That is
            # exactly the fault that left two .reds files killing the whole
            # script stack for weeks, with four more places to happen and
            # CET mods newly landing in one of them.
            store[str(rel)] = []
            continue
        try:
            store[str(rel)] = sorted(os.listdir(d))
        except OSError:
            # Exists but unreadable. NOT the same thing: an empty baseline
            # here would claim the game owns nothing in a directory we
            # simply could not open, and reset would delete its contents.
            pass
    if game_root and os.path.isdir(game_root):
        try:
            settings.setdefault("vanilla_root_baseline", {})[game_domain] = (
                sorted(
                    n for n in os.listdir(game_root)
                    if os.path.isfile(os.path.join(game_root, n))
                )
            )
        except OSError:
            pass
    # Which build it describes. A later reset can then tell whether the
    # game itself has changed underneath the baseline.
    build = _steam_build_id(app_id)
    if build:
        settings.setdefault("baseline_build", {})[game_domain] = build
    _save_settings(settings)
    decky.logger.info(
        f"{game_domain}: recorded a {len(have[game_domain])}-entry vanilla "
        "baseline for the mods folder"
    )


def _merge_install_record(existing: dict, new: dict) -> dict:
    """Fold a new install into any record already under the same key.

    Records are keyed by mod NAME, but a collection routinely installs
    several FILES from one mod - a main file plus its patches. Each one
    replaced the previous record outright, so every file but the last
    became untrackable: reset could not remove what it had no record of.
    On a 1,972-mod collection that orphaned 668 files and 20GB, twice,
    and both times it looked like "reset is broken" rather than "install
    forgot".

    Re-keying by file id would fix it too, but the key is the mod's
    identity everywhere else - the uninstall row, the toggle, the folder
    shown to the user - so the file lists are merged instead and the mod
    stays one thing.

    A repeat of the SAME file (a repair pass) replaces rather than
    accumulates: its file list is already the whole truth for that file.
    """
    # Stamped here rather than at each of the eight call sites: this is
    # the one path every install record passes through, so it cannot be
    # forgotten when a ninth is added.
    new = dict(new, install_seq=_next_install_seq())
    if not existing:
        return new
    merged = dict(new)
    same_file = existing.get("file_id") == new.get("file_id")
    for field in ("files", "plugins"):
        old_vals = existing.get(field) or []
        new_vals = new.get(field) or []
        if same_file:
            merged[field] = new_vals
            continue
        seen, out = set(), []
        for v in list(old_vals) + list(new_vals):
            key = v.lower() if isinstance(v, str) else repr(v)
            if key in seen:
                continue
            seen.add(key)
            out.append(v)
        merged[field] = out
    if not same_file:
        # Which files of this mod are present, so a future uninstall or
        # repair can tell how the record was assembled.
        ids = list(existing.get("file_ids") or [existing.get("file_id")])
        if new.get("file_id") not in ids:
            ids.append(new.get("file_id"))
        merged["file_ids"] = [i for i in ids if i is not None]
    # Where the mod came from outlives which file of it is installed.
    #
    # Updating BaseLib inside a collection blanked its source and
    # collection_slug, because a plain install passes those as "". The mod
    # was still part of the collection - so cancelling the collection would
    # have walked past it and left an orphan, which is the exact failure
    # Michael asked to be careful about. An update is not a change of
    # provenance.
    for field in ("source", "collection_slug"):
        if not merged.get(field) and existing.get(field):
            merged[field] = existing[field]
    return merged


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "", name).strip().strip(".")
    return cleaned or "mod"


def _force_rmtree(path: str) -> None:
    """rmtree that survives read-only dirs shipped inside mod archives
    (seen in the wild: zip entries extracted without owner write) AND
    plain files (NVAC's 'readme - nvac.txt' crashed the FOMOD staging
    cleanup, which assumed directories)."""
    if not os.path.lexists(path):
        return
    if not os.path.isdir(path) or os.path.islink(path):
        try:
            os.remove(path)
        except OSError:
            pass
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


REQUIREMENT_FIELDS = """
 modId
 modRequirements {
   nexusRequirements { nodes { modName modId notes url } }
   dlcRequirements { notes gameExpansion { name } }
 }
"""


def _split_requirements(node: dict) -> dict:
    """One legacyMods node -> {"requirements", "dlc"}.

    Shared by the single-mod call and the batched one so the two cannot
    drift into disagreeing about the same mod.
    """
    reqs = (node or {}).get("modRequirements") or {}
    raw = ((reqs.get("nexusRequirements") or {}).get("nodes")) or []
    dlc = []
    for entry in reqs.get("dlcRequirements") or []:
        name = ((entry or {}).get("gameExpansion") or {}).get("name")
        if name:
            dlc.append({"name": str(name),
                        "notes": str((entry or {}).get("notes") or "")})
    return {"requirements": _normalize_requirements(raw), "dlc": dlc}


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
    """Adult content follows the Nexus Mods ACCOUNT, never a local toggle:
    UK OSA-class laws require age verification, and that happens on the
    platform. The gate opens only when the account's site preference says
    adult AND the account is age-verified (both read live via GraphQL by
    refresh_content_gate and cached here). No plugin-side opt-in exists,
    so an unverified user can never enable it from the device."""
    gate = _load_settings().get("content_gate") or {}
    return bool(gate.get("adult_pref")) and bool(gate.get("age_verified"))


def _gate_adult_nodes(nodes, key: str = "adultContent") -> list:
    """Client-side adult filtering that always AGREES with the gate. The
    server-side query filter is primary; this pass exists so a response
    that slips adult items through a gate-closed query still gets caught -
    and so a gate-open query is never silently re-filtered (the v0.37.0
    'search says 39, shows 6' regression)."""
    if _show_adult():
        return list(nodes)
    return [m for m in nodes if not m.get(key)]


async def _refresh_content_gate(api_key: str) -> dict:
    """Read the account's adult preference and age-verification status from
    the v2 GraphQL API (both fields resolve the apikey's user) and cache
    them in settings for the synchronous _show_adult() call sites."""
    data = await _gql_query(
        "{ preferences { adult adultBlurImages } ageVerificationInfo { verified } }",
        api_key,
    )
    prefs = data.get("preferences") or {}
    verification = data.get("ageVerificationInfo") or {}
    gate = {
        "adult_pref": bool(prefs.get("adult")),
        "age_verified": bool(verification.get("verified")),
        "blur_images": bool(prefs.get("adultBlurImages")),
        "checked_at": int(time.time()),
    }
    settings = _load_settings()
    settings["content_gate"] = gate
    _save_settings(settings)
    decky.logger.info(
        "Content gate refreshed: adult_pref=%s age_verified=%s"
        % (gate["adult_pref"], gate["age_verified"])
    )
    return gate


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


async def _legacy_mods_in_batches(
    game_id: int, mod_ids: list, fields: str, api_key=None
) -> list:
    """Fetch legacyMods for any number of ids, in batches the API answers
    fully.

    The response cap is LEGACY_MODS_PAGE and it is silent: ask for 27 and 20
    come back with no error and no cursor, so the caller cannot tell the
    difference between "that mod has no data" and "the API stopped talking".
    Every bulk id lookup goes through here so that cannot be got wrong in one
    place and right in another.
    """
    nodes = []
    for start in range(0, len(mod_ids), LEGACY_MODS_PAGE):
        chunk = mod_ids[start : start + LEGACY_MODS_PAGE]
        id_args = ", ".join(
            "{gameId: %d, modId: %d}" % (game_id, int(i)) for i in chunk
        )
        data = await _gql_query(
            "{ legacyMods(ids: [%s]) { nodes {%s} } }" % (id_args, fields),
            api_key,
        )
        nodes.extend(data["legacyMods"]["nodes"])
    return nodes


async def _resolve_game_id(game_domain: str, api_key=None) -> int:
    game_id = _GAME_ID_CACHE.get(game_domain)
    if game_id is None:
        data = await _gql_query(
            '{ game(domainName: "%s") { id } }' % game_domain, api_key
        )
        game_id = data["game"]["id"]
        _GAME_ID_CACHE[game_domain] = game_id
    return int(game_id)


def _download_forbidden_reason(body: str, is_premium=None) -> str:
    """Turn a 403 from the download-link endpoint into the truth.

    This used to say "Direct downloads need a Premium account" for every
    403, which was a guess dressed as a diagnosis. Michael installed Slay
    the Spire 2's most popular collection on a Premium account and got it
    twice - for a mod its author had deleted, and one Nexus had taken down
    for review. Both are ordinary things to happen to a collection, and
    telling someone to buy an account they already have is the worst
    possible answer.

    The endpoint says which it is, in the body:

        {"code":403,"message":"Mod not available: 502"}
        {"code":403,"message":"File currently not available. Library of
         Ruina (Mod ID: 368) is under moderation"}
    """
    message = ""
    try:
        parsed = json.loads(body or "{}")
        if isinstance(parsed, dict):
            message = str(parsed.get("message") or "")
    except (ValueError, TypeError):
        message = ""
    low = message.lower()
    if "under moderation" in low:
        return (
            "Nexus has taken this mod down while it is reviewed. Nothing "
            "you can do - it will come back, or it will not, and that is "
            "up to Nexus and the author."
        )
    if "not available" in low or "deleted" in low or "hidden" in low:
        return (
            "The author has removed this mod from Nexus, so it cannot be "
            "downloaded any more."
        )
    if is_premium:
        # Premium and still refused: whatever this is, it is not the
        # account, and guessing again would repeat the original mistake.
        return message or "Nexus refused the download for this file"
    return (
        "Direct downloads need a Premium account "
        "(free-user flow not implemented yet)"
    )


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
    # Gamebryo/FNV additions (an entire NVSE-plugin collection failed
    # for want of these, 2026-08-04): script-extender plugin trees,
    # loose shaders, XML menus, and the Data/config ini convention.
    "nvse", "fose", "f4se", "shaders", "menus", "config", "mcm",
    # TTW-run stragglers (2026-08-05): .bik movie replacers live in
    # Data/Video; kNVSE-era keyword configs in Data/keywords.
    "video", "keywords",
    # Gate To Sovngarde stragglers (2026-08-07): modern framework addons
    # ship ONE Data subfolder and nothing else recognisable, so the
    # payload check refused them outright. Each of these was a real
    # refusal in that run.
    "nemesis_engine", "mapmarkers", "lightplacer", "seasons",
    "distributedmods", "netscriptframework", "dialogueviews", "lodsettings",
    "planetdata", "calientetools", "tools", "source", "facegendata",
    "actors", "effects", "misc", "dyndolod",
}

# Junk some archives carry that must never reach the game folder: macOS
# zip metadata (a __MACOSX tree beside the payload) and Explorer/Finder
# droppings.
ARCHIVE_JUNK_DIRS = {"__macosx"}
ARCHIVE_JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini"}

# Binaries that load beside the game exe rather than from Data/. A
# Bethesda mod's dlls live in Data/SKSE/Plugins; a loose one at an
# archive's root is a preloader or graphics shim (SSE Engine Fixes'
# d3dx9_42.dll, ENB's d3d11.dll) and is useless anywhere but the game
# root - Engine Fixes aborts the launch when it cannot find its own.
ROOT_BINARY_EXTS = (".dll", ".asi")


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


# ---- Proton prefix VC++ runtime ------------------------------------------
# Cyberpunk's Steam install script runs the game's OWN bundled vcredist
# (2019, 14.28) inside the fresh Proton prefix, silently downgrading the
# CRT below what current CET/RED4ext builds need (VS 17.10+, 14.40+).
# Wine then fails their LoadLibrary with ERROR_NOACCESS ("Error: 998" /
# "No access to memory location"). Valve ships the genuine MS runtime
# (14.42+) inside every modern Proton - the fix is copying it over.

CRT_DLLS = (
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "concrt140.dll",
)
CRT_BACKUP_SUFFIX = ".decky-nexus-bak"
CRT_MIN_VERSION = (14, 40, 0, 0)


def _pe_file_version(path: str):
    """FileVersion of a PE as a 4-tuple, located via the VS_FIXEDFILEINFO
    signature (0xFEEF04BD) - no resource-tree walking, works on MS and
    Wine-built DLLs alike. None when unreadable or unversioned."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    sig = data.find(b"\xbd\x04\xef\xfe")
    if sig < 0 or sig + 16 > len(data):
        return None
    ms = int.from_bytes(data[sig + 8 : sig + 12], "little")
    ls = int.from_bytes(data[sig + 12 : sig + 16], "little")
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def _prefix_system32(app_id: int) -> str:
    return os.path.join(
        decky.DECKY_USER_HOME, ".steam", "steam", "steamapps", "compatdata",
        str(int(app_id)), "pfx", "drive_c", "windows", "system32",
    )


def _newest_proton_crt_dir():
    """(dir, version) of whichever installed Proton bundles the newest
    msvcp140.dll in its files/lib/wine/x86_64-windows payload."""
    best, best_ver = None, None
    pattern = os.path.join(
        STEAM_COMMON, "Proton*", "files", "lib", "wine", "x86_64-windows"
    )
    for cand in glob.glob(pattern):
        ver = _pe_file_version(os.path.join(cand, "msvcp140.dll"))
        if ver and (best_ver is None or ver > best_ver):
            best, best_ver = cand, ver
    return best, best_ver


def _proton_binary_for(app_id: int):
    """(proton_script, compatdata_dir, steam_root, err) - the tool MUST
    run under the same Proton build as the game: mixing builds in one
    prefix wedges its wineserver (learned live - the version file says
    '11.0-100' for BOTH standalone Proton 11.0 and Experimental, and
    picking the wrong one left FO3 unable to boot). Steam's per-app
    CompatToolMapping is the authority; unpinned = Experimental (the
    SteamOS default)."""
    steam_root = os.path.join(decky.DECKY_USER_HOME, ".steam", "steam")
    compat = os.path.join(
        steam_root, "steamapps", "compatdata", str(int(app_id))
    )
    candidates = []
    try:
        with open(
            os.path.join(steam_root, "config", "config.vdf"),
            "r", encoding="utf-8", errors="replace",
        ) as f:
            cfg = f.read()
        m = re.search(
            r'"%d"\s*\{\s*"name"\s*"([^"]*)"' % int(app_id), cfg
        )
        if m:
            tool = m.group(1)
            mv = re.match(r"proton_(\d+)", tool)
            if tool == "proton_experimental":
                candidates.append("Proton - Experimental")
            elif mv:
                candidates.append(f"Proton {mv.group(1)}.0")
    except OSError:
        pass
    # Steam's own default comes BEFORE anything read out of the prefix.
    #
    # I got this backwards in v0.165.0 and made it worse. config_info in
    # Fallout 3's prefix said "Proton 10.0", so the picker started choosing
    # 10.0 for tools - while the GAME was running under Experimental, which
    # is what Steam actually defaults to here. Running the exe by hand
    # printed the proof:
    #
    #   Proton: Upgrading prefix from 10.1000-105 to 11.0-100
    #
    # Experimental had been silently upgrading that prefix for weeks. So
    # what is written in the prefix describes what BUILT it, not what runs
    # it now, and preferring it guarantees exactly the mismatch this
    # function exists to avoid.
    candidates.append("Proton - Experimental")
    # Only as a last resort, for a device whose default is not Experimental:
    # config_info names a directory outright, the version file is ambiguous
    # ("11.0-100" is both standalone 11.0 and Experimental).
    try:
        with open(
            os.path.join(compat, "config_info"), "r", encoding="utf-8"
        ) as f:
            for line in f.read().splitlines():
                m = re.search(r"/common/(Proton[^/]*)/files/", line)
                if m:
                    candidates.append(m.group(1))
                    break
    except OSError:
        pass
    version = ""
    try:
        with open(os.path.join(compat, "version"), "r", encoding="utf-8") as f:
            version = f.read().strip()
    except OSError:
        pass
    m = re.match(r"(\d+)\.(\d+)", version)
    if m:
        candidates.append(f"Proton {m.group(1)}.0")
    for name in candidates:
        p = os.path.join(STEAM_COMMON, name, "proton")
        if os.path.isfile(p):
            return p, compat, steam_root, ""
    for p in sorted(glob.glob(os.path.join(STEAM_COMMON, "Proton*", "proton"))):
        return p, compat, steam_root, ""
    return "", compat, steam_root, "No Proton installation found on this device"


# Content that belongs to the GAME rather than to modding: base masters,
# DLC, and the archives that carry them. Creation Club files follow the
# same shape on Skyrim SE.
_CC_FILE_RE = re.compile(r"^cc[a-z]{3,4}(sse|fo4)\d{3}[-_ .]", re.IGNORECASE)


def _game_owned_name(game_domain: str, name: str) -> bool:
    """Is this file part of the game the user bought?

    Reset sweeps everything in the mods folder that is not in the vanilla
    baseline, on the reasoning that the baseline was captured before the
    first mod and so anything newer arrived with modding. That reasoning
    has a hole in it: the GAME can gain files afterwards.

    On device it did. New Vegas's baseline was captured, the user then
    bought the Ultimate Edition DLC in a sale, and the next reset deleted
    all nine DLC masters and their archives - content they had paid for
    that hour, removed by a button labelled "reset game modding". The game
    then refused to start, asking for the very files we had taken.

    A baseline cannot be the only guard, so game-owned content is never
    swept whatever the baseline says.
    """
    low = (name or "").lower()
    masters = IMPLICIT_MASTERS_BY_DOMAIN.get(game_domain) or frozenset()
    if low in masters:
        return True
    if _CC_FILE_RE.match(low):
        return True
    base, _, ext = low.rpartition(".")
    if ext not in ("bsa", "ba2", "esm", "esp", "esl"):
        return False
    # DLC archives are named after their master: "DeadMoney - Main.bsa",
    # "ClassicPack - Main.bsa".
    for master in masters:
        stem = master.rsplit(".", 1)[0]
        if base == stem or base.startswith(stem + " ") or base.startswith(
            stem + "-"
        ):
            return True
    return False


# Mods that need something Nexus does not host, and so cannot work on a
# console-style install where nobody is going to fetch it by hand.
#
# Switched OFF by default rather than installed-and-broken. Anyone who does
# go and get the external file can turn them back on in one tap - a user
# capable of downloading from ModDB has already shown they will tinker;
# a user who just wants the collection to start should never have to.
#
# Keyed on the install record name, lowercased. `needs_file` is looked for
# in the game's Data folder, so the check is a fact about the install
# rather than a guess.
# Mods that need something Nexus does not host, keyed by NEXUS MOD ID.
#
# Keyed by id rather than by our record name for two reasons: the record
# name is a sanitised display string that can drift, and the id is what the
# mod PAGE knows - so the same table warns a user browsing to the mod on its
# own, not only someone installing the collection it came from. Michael's
# point: "I don't want users to run into these problems individually as
# well as on collections."
#
# `needs_file` is looked for in the game's mods folder, so "have I got it?"
# is a fact about the install rather than a guess. Mods listed here are
# switched OFF when it is absent and restored when it appears.
MODS_NEEDING_EXTERNAL = {
    "newvegas": {
        # One HUD, Clean Vanilla Hud and the patch between them are the
        # interface layer of New Vegas's most popular collections, and all
        # three are built on Vanilla UI+ - which lives on ModDB, so no API
        # can fetch it. With it absent the game reaches the main-menu
        # background and stops, with nothing in any log a user could act on.
        44757: {
            "name": "One HUD - oHUD",
            "needs_file": "Vanilla UI Plus.esp",
            "needs_name": "Vanilla UI+ (VUI+)",
            "url": "https://www.moddb.com/mods/vanilla-ui-plus/downloads/vanilla-ui-plus-nv",
        },
        70001: {
            "name": "Clean Vanilla Hud",
            "needs_file": "Vanilla UI Plus.esp",
            "needs_name": "Vanilla UI+ (VUI+)",
            "url": "https://www.moddb.com/mods/vanilla-ui-plus/downloads/vanilla-ui-plus-nv",
        },
        84166: {
            "name": "One HUD - oHUD - Clean Vanilla Hud Patch",
            "needs_file": "Vanilla UI Plus.esp",
            "needs_name": "Vanilla UI+ (VUI+)",
            "url": "https://www.moddb.com/mods/vanilla-ui-plus/downloads/vanilla-ui-plus-nv",
        },
    },
}


# Collections that cannot work on a SteamOS/Gaming Mode install, and why.
# Said up front rather than after a 42 GB download.
#
# Keyed by slug because that is what the page has. The reason is shown
# verbatim, so it has to read as an explanation and not a refusal.
UNSUPPORTED_COLLECTIONS = {
    "newvegas": {
        "3fs9zx": {
            "title": "VeryLastKiss's TTW",
            "reason": (
                "This collection is built on Tale of Two Wastelands, which "
                "it does not include. TTW is not a Nexus mod - it is built "
                "from your own copy of Fallout 3 using a Windows installer, "
                "which cannot run in Gaming Mode. Without it around 70 of "
                "these mods have nothing to attach to and will be switched "
                "off, and the Fallout 3 content the collection exists for "
                "will not be there."
            ),
        },
    },
}


def _parked_files_dir(game_domain: str, record_key: str) -> str:
    """Where a disabled dataDir mod's files wait to be put back."""
    return os.path.join(
        decky.DECKY_PLUGIN_RUNTIME_DIR, "parked", game_domain,
        _safe_name(record_key),
    )


def _shared_paths(
    records: dict, record_key: str, also_off=(), modes=("dataDir",)
) -> set:
    """Recorded paths that another ACTIVE mod also provides.

    Moving one of these away takes the other mod's copy with it - whoever
    wrote last owns the file on disk, and it is not necessarily the mod
    being switched off. Left in place: a mod that stays half-active is a
    smaller wrong than one that silently guts another.

    But "another mod" has to mean another mod that is still ON. When a
    whole group goes off together there is nobody left to protect, and
    being cautious then defeats the point: on device the oHUD/Clean
    Vanilla Hud patch owns exactly two files, both shared with the other
    two interface mods, so with all three being switched off it had NOTHING
    movable and stayed fully active - keeping the one file that stops the
    game starting. `also_off` is the rest of the group, plus anything
    already parked.

    `modes` is which install modes count as a rival claim. It defaults to
    dataDir alone so nothing that relied on this changes; Cyberpunk's
    "files" mode passes its own, and it needs this every bit as much - 283
    mods dropping files into five shared game directories is exactly the
    situation where two records name the same path.
    """
    ignore = {k.lower() for k in also_off} | {record_key.lower()}
    mine = {f.lower() for f in (records.get(record_key) or {}).get("files") or []}
    shared = set()
    for key, rec in records.items():
        if key.lower() in ignore or rec.get("mode") not in modes:
            continue
        if rec.get("parked"):
            continue
        shared |= mine & {f.lower() for f in rec.get("files") or []}
    return shared


def _move_mod_files(src_root: str, dst_root: str, rels: list) -> int:
    """Move a list of relative paths between two trees, pruning empties."""
    moved = 0
    touched = set()
    for rel in rels:
        if not _safe_rel_path(rel):
            continue
        src = os.path.join(src_root, *rel.split("/"))
        if not os.path.isfile(src):
            continue
        dst = os.path.join(dst_root, *rel.split("/"))
        try:
            _makedirs_for(dst)
            shutil.move(src, dst)
        except OSError:
            continue
        moved += 1
        parent = os.path.dirname(src)
        while parent and len(parent) > len(src_root):
            touched.add(parent)
            parent = os.path.dirname(parent)
    for d in sorted(touched, key=len, reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass
    return moved


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
    section is appended to the file. Writes a one-time .decky-nexus.bak.

    Line endings are preserved. These are Windows programs' config files
    living inside a Proton prefix and they arrive CRLF; rewriting the
    whole file as LF is a change to every line of a file we were asked to
    touch one key in, and how forgiving the reader is isn't ours to
    assume.
    """
    lines = []
    newline = "\n"  # only for files we create; existing ones keep theirs
    trailing_newline = True
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            raw = f.read()
        lines = raw.splitlines()
        if "\r\n" in raw:
            newline = "\r\n"
        elif "\n" in raw:
            newline = "\n"
        trailing_newline = raw.endswith(("\n", "\r"))
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
                _canon, val = remaining.pop(key)
                # Keep the line's own shape - indentation, the key as the
                # program wrote it, and the spacing around '=' - so the
                # diff is the value and nothing else.
                head, sep, tail = line.partition("=")
                pad = tail[: len(tail) - len(tail.lstrip())] or (
                    " " if tail.startswith(" ") or not tail else ""
                )
                out.append(f"{head}{sep}{pad}{val}")
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
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(newline.join(out) + (newline if trailing_newline else ""))


def _read_plugins_txt(path: str) -> list:
    if not path or not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def _write_plugins_txt(path: str, lines: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


# TES4 header record flags.
PLUGIN_FLAG_MASTER = 0x00000001  # .esm-style: loads before regular plugins
PLUGIN_FLAG_LIGHT = 0x00000200  # ESL: shares the FE index, no slot of its own


def _plugin_header(path: str):
    """(flags, masters) from a Bethesda plugin's TES4 header (FO3/FNV/SSE
    /FO4 share the 24-byte record header + 4cc/uint16 subrecord format).
    None when the file isn't a plugin.

    One read for both, because the load-order sort asks about every
    plugin in the game and a collection can have two thousand of them.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(24)
            if len(head) < 24 or head[:4] != b"TES4":
                return None
            data_size = int.from_bytes(head[4:8], "little")
            flags = int.from_bytes(head[8:12], "little")
            data = f.read(min(data_size, 1 << 20))
    except OSError:
        return None
    masters, off = [], 0
    while off + 6 <= len(data):
        sub = data[off : off + 4]
        size = int.from_bytes(data[off + 4 : off + 6], "little")
        if sub == b"MAST" and size > 0:
            masters.append(
                data[off + 6 : off + 6 + size]
                .rstrip(b"\x00")
                .decode("cp1252", "replace")
            )
        off += 6 + size
    return flags, masters


def _plugin_masters(path: str):
    """MAST entries only. None when the file isn't a plugin; [] when it
    has no masters."""
    head = _plugin_header(path)
    return None if head is None else head[1]


def _topo_by_masters(data_path: str, group: list, cache: dict = None) -> list:
    """Stable dependency sort: mod plugins can master EACH OTHER (Rebirth+
    shipped an esm mastering another mod esm, and a patch esp mastering
    another esp) - listed order alone produced loads-before-master
    crashes on device. Cycles fall back to the input order."""
    idx = {n.lower(): i for i, n in enumerate(group)}
    indeg = {n.lower(): 0 for n in group}
    rev = {n.lower(): [] for n in group}
    for n in group:
        if cache is not None and n.lower() in cache:
            ms = cache[n.lower()][1]
        else:
            ms = _plugin_masters(os.path.join(data_path, n)) or []
        for m in ms:
            ml = m.lower()
            if ml in idx and ml != n.lower():
                indeg[n.lower()] += 1
                rev[ml].append(n.lower())
    ready = sorted([n for n in indeg if indeg[n] == 0], key=lambda x: idx[x])
    out = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for d in rev[n]:
            indeg[d] -= 1
            if indeg[d] == 0:
                ready.append(d)
                ready.sort(key=lambda x: idx[x])
    if len(out) != len(group):
        return group
    actual = {n.lower(): n for n in group}
    return [actual[n] for n in out]


def _enabled_plugins(path: str, style: str) -> list:
    """Plugin names the game will actually LOAD. starred: only '*'-lines;
    listed (FNV/FO3): every non-comment line."""
    names = []
    for line in _read_plugins_txt(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if style == "starred":
            if line.startswith("*"):
                names.append(line.lstrip("*").strip())
        else:
            names.append(line)
    return names


# The game's OWN masters: always loaded, never managed. Skyrim and FO4
# load these implicitly and their launchers never write them into
# plugins.txt - listing them there shifts every plugin's load index,
# which is what save files record, so it is not a cosmetic difference.
# Timestamp-ordered games (FO3/FNV): the engine loads ESMs then ESPs by
# file MTIME, not plugins.txt order. These must load first, in this order.
VANILLA_MASTERS_BY_DOMAIN = {
    "fallout3": [
        "Fallout3.esm", "Anchorage.esm", "ThePitt.esm",
        "BrokenSteel.esm", "PointLookout.esm", "Zeta.esm",
    ],
    "newvegas": [
        "FalloutNV.esm", "DeadMoney.esm", "HonestHearts.esm",
        "OldWorldBlues.esm", "LonesomeRoad.esm", "GunRunnersArsenal.esm",
        "ClassicPack.esm", "MercenaryPack.esm", "TribalPack.esm",
        "CaravanPack.esm",
    ],
}


IMPLICIT_MASTERS_BY_DOMAIN = {
    "skyrimspecialedition": {
        "skyrim.esm", "update.esm", "dawnguard.esm", "hearthfires.esm",
        "dragonborn.esm",
    },
    "skyrim": {
        "skyrim.esm", "update.esm", "dawnguard.esm", "hearthfires.esm",
        "dragonborn.esm",
    },
    "fallout4": {
        "fallout4.esm", "dlcrobot.esm", "dlcworkshop01.esm", "dlccoast.esm",
        "dlcworkshop02.esm", "dlcworkshop03.esm", "dlcnukaworld.esm",
        "dlcultrahighresolution.esm",
    },
    # FO3/FNV load the base game and its DLC without being told, same as
    # Skyrim - verified on device 2026-08-12 from the game's own log, where
    # forms resolved against DLC indices (0500BA43 is Dead Money's 05)
    # while not one DLC esm was listed in Plugins.txt.
    #
    # Left out until now, so the load-order check reported all ten as
    # "installed but switched off" and offered to switch them on. Doing
    # that renumbers every plugin after them, and the load index is what a
    # save file records - the same save-breaking repair that was caught by
    # a test on Skyrim in v0.71.0, still live here on two shipping games.
    "newvegas": {m.lower() for m in VANILLA_MASTERS_BY_DOMAIN["newvegas"]},
    "fallout3": {m.lower() for m in VANILLA_MASTERS_BY_DOMAIN["fallout3"]},
}




def _stagger_plugin_mtimes(
    data_path: str, plugins_txt_path: str, style: str, game_domain: str
) -> int:
    """FO3/FNV load order = plugin file TIMESTAMPS. Archive-extracted
    mods carry arbitrary mtimes (a Jan-2000 ESM loaded BEFORE its own
    master on device - guaranteed boot crash). Restamp every enabled
    plugin: vanilla masters first in canonical order, then mod ESMs,
    then ESPs, one minute apart, all in the past so future installs
    naturally land after. Returns how many were stamped."""
    if style != "listed":
        return 0
    vanilla = VANILLA_MASTERS_BY_DOMAIN.get(game_domain) or []
    names = _enabled_plugins(plugins_txt_path, style)
    try:
        real = {n.lower(): n for n in os.listdir(data_path)}
    except OSError:
        return 0
    vanilla_lower = [v.lower() for v in vanilla]

    def _topo(group: list) -> list:
        return _topo_by_masters(data_path, group)

    esms = [
        real[n.lower()] for n in names
        if n.lower().endswith(".esm")
        and n.lower() not in vanilla_lower
        and n.lower() in real
    ]
    esps = [
        real[n.lower()] for n in names
        if not n.lower().endswith(".esm") and n.lower() in real
    ]
    ordered = [
        real[v.lower()] for v in vanilla if v.lower() in real
    ] + _topo(esms) + _topo(esps)
    base = time.time() - (len(ordered) + 10) * 60
    stamped = 0
    for i, actual_name in enumerate(ordered):
        t = base + i * 60
        try:
            os.utime(os.path.join(data_path, actual_name), (t, t))
            stamped += 1
        except OSError:
            pass
    return stamped


LOAD_ORDER_BACKUP = ".decky-bak"


# Plugins proven to stop a game booting, keyed by Nexus domain. Only ROOT
# causes are listed: everything that depends on one is derived, because a
# mod cannot load without its master and listing the dependents by hand
# would rot the moment a collection changed.
#
# Entries earn their place by evidence, not suspicion - each one below
# either crashed the game on its own or survived a controlled A/B where
# it was the single variable.
KNOWN_BAD_PLUGINS = {
    "skyrimspecialedition": {
        # Gate To Sovngarde, verified on device 2026-08-08..11 across
        # roughly 150 launches. All are the collection's own patch files;
        # not one third-party mod was at fault.
        "njr - bruma patch.esp": "crashes Skyrim during data load",
        "gts - taliesin replacer.esp": "crashes Skyrim during data load",
        "cc_menagerieecss.esp": "crashes Skyrim during data load",
        "stendarrschosen - bruma spawns addon.esp":
            "crashes Skyrim during data load",
        "stendarrschosen - no skyrim spawns.esp":
            "crashes Skyrim during data load",
        "gts - vigilant.esp": "crashes Skyrim during data load",
        "gts_traits.esp": "crashes Skyrim during data load",
        "gts patches - scion.esp": "crashes Skyrim during data load",
        "gts patches - landscapes part 2.esp":
            "crashes Skyrim during cell setup",
    },
}


def _load_skips(game_domain: str) -> dict:
    """Plugins deliberately switched off, and why.

    Without this the tool cannot tell "off because it breaks the game"
    from "off by accident", so every routine that tidies the load order
    undoes the user's decisions. On device that resurrected 8 skipped
    plugins in one pass, because something still listed them as a master.
    """
    return _load_settings().get("skipped", {}).get(game_domain, {})


def _save_skips(game_domain: str, skips: dict) -> None:
    settings = _load_settings()
    settings.setdefault("skipped", {})[game_domain] = skips
    _save_settings(settings)

# The automated crash hunt's state, kept on disk so it survives a Decky
# restart mid-run (which happens - the plugin gets redeployed, the device
# sleeps) rather than losing an hour of launches.
BISECT_STATE = "crash_bisect.json"


def _bisect_state_path() -> str:
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, BISECT_STATE)


def _bisect_load() -> dict:
    try:
        with open(_bisect_state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _bisect_save(state: dict) -> None:
    os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
    with open(_bisect_state_path(), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _dependents_closure(data_path: str, names: list, targets: set) -> list:
    """Everything in `names` that cannot work without something in
    `targets`, transitively.

    A mod is not optional to the mods built on it. Skipping one without
    its dependents leaves them loading against a master that is no longer
    there, which crashes in its own right - so the hunt then "finds" each
    victim in turn. On the device's Gate To Sovngarde run that turned 3
    real culprits into 14 findings and cost about three hours of launches
    discovering consequences of its own first skip.
    """
    try:
        real = {f.lower(): f for f in os.listdir(data_path)}
    except OSError:
        return []
    masters = {}
    for n in names:
        f = real.get(n.lower())
        masters[n.lower()] = {
            m.lower()
            for m in (_plugin_masters(os.path.join(data_path, f)) or [])
        } if f else set()
    doomed = {t.lower() for t in targets}
    out = []
    changed = True
    while changed:
        changed = False
        for n in names:
            low = n.lower()
            if low in doomed:
                continue
            if masters[low] & doomed:
                doomed.add(low)
                out.append(n)
                changed = True
    return out


def _bisect_next_prefix(state: dict) -> int:
    """Which prefix length to test next.

    The hunt walks the load order as PREFIXES, not halves. `lo` is the
    longest prefix known to boot, `hi` the shortest known to crash; the
    culprit is whatever sits at index `lo` once they meet. Prefixes work
    where plain halving does not, for two reasons found the hard way on
    device: the load order is topologically sorted, so any prefix is
    dependency-complete and needs no masters invented for it; and a mod
    that boots in a small isolated group can still break on top of the
    full set, which a prefix test exposes and an isolated test misses.

    `hi` is only trustworthy once measured. At the start the full set is
    known bad - that is why anyone runs this - but after skipping a
    culprit we do NOT know the rest still crashes, and assuming it does
    makes the machine invent a culprit at the last index. So an unverified
    `hi` is tested directly before any halving resumes.
    """
    if not state.get("hi_verified"):
        return state["hi"]
    return (state["lo"] + state["hi"]) // 2


def _bisect_advance(state: dict, crashed: bool) -> dict:
    """Fold one launch result in, and name a culprit if it is now pinned."""
    mid = state.pop("testing", None)
    if mid is None:
        return state
    state.setdefault("launches", 0)
    state["launches"] += 1
    state["found"] = None
    if not state.get("hi_verified"):
        # We were checking whether anything is still wrong at all.
        if not crashed:
            state["lo"] = state["hi"]      # nothing left to find; stop
            return state
        state["hi_verified"] = True
        if state["hi"] > state["lo"] + 1:
            return state                  # start halving next time
    elif crashed:
        state["hi"] = mid
    else:
        state["lo"] = mid
    # lo boots, lo+1 crashes: index lo is the offender. Skipping it makes
    # prefix lo+1 equivalent to prefix lo, so the known-good edge moves up
    # by one and the search restarts - with hi unverified again, because
    # the remaining mods may now be fine.
    if state["hi"] == state["lo"] + 1:
        culprit = state["order"][state["lo"]]
        state.setdefault("skipped", []).append(culprit)
        state["found"] = culprit
        state["lo"] = state["lo"] + 1
        state["hi"] = len(state["order"])
        state["hi_verified"] = False
    return state


def _plugin_entries(lines: list, style: str = "starred") -> list:
    """(name, enabled) for the real entries, skipping comments/blanks.

    The two dialects disagree about what "enabled" means. Starred
    (Skyrim/FO4) marks active plugins with a leading '*'. Listed
    (FO3/FNV/Skyrim 2011) has no marker at all - being in the file IS
    being enabled. Reading a listed file with the starred rule reports
    every plugin as disabled, which silently turns any check built on
    this into a no-op.
    """
    out = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        starred = s.startswith("*")
        name = s[1:].strip() if starred else s
        out.append((name, True if style == "listed" else starred))
    return out


def _load_order_report(data_path: str, names: list, cache: dict = None) -> int:
    """How many plugins are listed BEFORE a master they depend on.

    Skyrim and Fallout 4 take plugins.txt order as the load order. A
    plugin listed before something it masters is not a nuance - the
    record it overrides does not exist yet, and the game crashes on the
    way in.
    """
    pos = {n.lower(): i for i, n in enumerate(names)}
    bad = 0
    for i, n in enumerate(names):
        if cache is not None and n.lower() in cache:
            ms = cache[n.lower()][1]
        else:
            ms = _plugin_masters(os.path.join(data_path, n)) or []
        for m in ms:
            j = pos.get(m.lower())
            if j is not None and j > i:
                bad += 1
    return bad


# Masters that are not a mod at all: paid content the account may simply
# not own. Named so the panel can say "Dead Money" rather than
# "DeadMoney.esm", which nobody can act on without already knowing it maps
# to a Steam DLC.
# Requirements that are desktop mod MANAGERS, not mods.
#
# Resident Evil 4 mods routinely list Fluffy Mod Manager as required. It is
# a Windows application for installing mods - which is what this plugin
# does - so the requirement is satisfied by the mod already being
# installed, and there is nothing to fetch. REFramework's built-in
# LooseFileLoader covers the Fluffy layout, which is why Michael's three
# RE4 collections all worked while the health check complained.
#
# Matched on name because these are listed as ordinary Nexus mods with real
# mod ids; nothing in the API marks them as tooling.
_MANAGER_REQUIREMENT_RE = re.compile(
    r"fluffy\s*(mod\s*)?manager|vortex|mod\s*organizer|MO2|"
    r"nexus\s*mod\s*manager|NMM",
    re.IGNORECASE,
)


def _dlc_checkable(game_domain: str) -> bool:
    """Whether a missing DLC can be proved rather than guessed.

    True only where expansions ship as master files in the game's data
    folder, which is what DLC_MASTER_NAMES enumerates. Everywhere else a
    "you need this DLC" warning would be a guess aimed at somebody who may
    well own it - worse than silence.
    """
    return game_domain in DLC_GAMES_WITH_MASTERS


DLC_GAMES_WITH_MASTERS = frozenset(
    {"newvegas", "fallout3", "skyrimspecialedition", "fallout4", "oblivion"}
)

DLC_MASTER_NAMES = {
    # Fallout: New Vegas
    "deadmoney.esm": "Dead Money",
    "honesthearts.esm": "Honest Hearts",
    "oldworldblues.esm": "Old World Blues",
    "lonesomeroad.esm": "Lonesome Road",
    "gunrunnersarsenal.esm": "Gun Runners' Arsenal",
    "classicpack.esm": "Classic Pack",
    "mercenarypack.esm": "Mercenary Pack",
    "tribalpack.esm": "Tribal Pack",
    "caravanpack.esm": "Caravan Pack",
    # Fallout 3
    "anchorage.esm": "Operation: Anchorage",
    "thepitt.esm": "The Pitt",
    "brokensteel.esm": "Broken Steel",
    "pointlookout.esm": "Point Lookout",
    "zeta.esm": "Mothership Zeta",
    # Skyrim SE
    "dawnguard.esm": "Dawnguard",
    "hearthfires.esm": "Hearthfires",
    "dragonborn.esm": "Dragonborn",
    # Fallout 4
    "dlcrobot.esm": "Automatron",
    "dlcworkshop01.esm": "Wasteland Workshop",
    "dlccoast.esm": "Far Harbor",
    "dlcworkshop02.esm": "Contraptions Workshop",
    "dlcworkshop03.esm": "Vault-Tec Workshop",
    "dlcnukaworld.esm": "Nuka-World",
}


def _ghost_plugins(data_path: str, names: list) -> list:
    """Plugins the load order enables that are not on disk.

    Harmless on Skyrim and FO4, where an entry is a line in a list. NOT
    harmless on FO3 and New Vegas, where presence in Plugins.txt IS
    activation - a leftover line is a phantom enabled plugin, and the count
    the panel reports is then a count of something that cannot load.

    Found by hand on device after uninstalling oHUD left `oHUD.esm` listed:
    the uninstall delists correctly only when the record carries a plugins
    list, and that one had lost its. Two lines here would have caught it
    immediately, so here they are.
    """
    try:
        real = {f.lower() for f in os.listdir(data_path)}
    except OSError:
        return []
    return [n for n in names if n.lower() not in real]


def _missing_masters(data_path: str, names: list, implicit: set = frozenset()):
    """Masters an enabled plugin needs that are not on disk AT ALL.

    The third distinct load-order fault, and the only one the user cannot
    fix by pressing a button here. `_masters_to_enable` covers a master
    that is installed but switched off; `_load_order_report` covers one
    that loads too late. This covers one that was never there - almost
    always game DLC the account does not own, and occasionally a mod the
    collection expected to be installed.

    The game reports it as a modal naming a single plugin, then quits. On
    device (New Vegas, 2026-08-12) that modal named `mil.esp` while 115 of
    245 enabled plugins were unloadable for want of five DLC masters, so
    what the game says is the tip of it.

    Returns [(master, [dependent, ...])] worst first.
    """
    try:
        real = {f.lower() for f in os.listdir(data_path)}
    except OSError:
        return []
    real |= {m.lower() for m in implicit}
    cache = {}
    missing = {}
    for n in names:
        key = n.lower()
        if key not in real:
            continue
        if key not in cache:
            f = next(
                (x for x in os.listdir(data_path) if x.lower() == key), None
            )
            head = _plugin_header(os.path.join(data_path, f)) if f else None
            cache[key] = head[1] if head else []
        for m in cache[key]:
            if m.lower() in real:
                continue
            missing.setdefault(m, []).append(n)
    return sorted(missing.items(), key=lambda kv: -len(kv[1]))


def _file_owners(records: dict) -> dict:
    """Lowercased relative path -> record keys that installed it.

    Only dataDir mods share a namespace. Folder-mode mods each own a
    directory, so two of them listing "manifest.json" is not a conflict,
    and me3 packages live outside the game entirely.
    """
    owners = {}
    for key, rec in records.items():
        if rec.get("mode") != "dataDir":
            continue
        for rel in rec.get("files") or []:
            owners.setdefault(rel.lower(), []).append(key)
    return owners


def _file_owner_overrides(game_domain: str, settings: dict = None) -> dict:
    """Paths whose owner was settled by hand, path -> record key.

    Written by resolve_file_conflicts after it rewrites a file from its
    rightful owner. Without this the same file reports as wrong forever:
    the losing mod still has the higher install_seq, because the fix
    deliberately does NOT reinstall it - reinstalling is what took the
    device from 47 bad pairs to 92.
    """
    settings = settings if settings is not None else _load_settings()
    return (settings.get("file_owner") or {}).get(game_domain, {})


def _wrong_winners(records: dict, order: dict, overrides: dict = None) -> list:
    """Files where the mod that actually landed last is NOT the one the
    collection wanted to.

    Overwriting is how collections WORK - a texture pack is meant to beat
    the mod under it, and this install has 10,362 shared paths across 867
    mod-sets. Reporting those as problems would bury the real fault in
    noise. The collection's own list order is the curator's statement of
    who should win, so the only thing worth reporting is where the result
    disagrees with it.

    Install order drifts from collection order for a reason we built in:
    a mod needing a FOMOD or a choice gets parked during the run and
    installed afterwards through Finish setup, so it lands last and beats
    everything it was supposed to lose to. On device that was Iron Sights
    Aligned taking 319 files from the collection's own patch hub, and
    Consistent Pip-Boy Icons v4 beating v5 in six separate pairs. Nothing
    reported any of it; the game just looked wrong.

    `order` maps mod_id -> position in the collection. Records whose mod
    is not in it are skipped rather than guessed at - a mod installed by
    hand has no curator intent to violate.

    Returns [{actual, intended, files, example, mod_ids}] worst first.
    """
    owners = _file_owners(records)
    overrides = overrides or {}
    ranked_cache = {}
    grouped = {}
    for path, keys in owners.items():
        keys = list(dict.fromkeys(keys))
        if len(keys) < 2:
            continue
        ranked = []
        for k in keys:
            if k not in ranked_cache:
                rec = records[k]
                ranked_cache[k] = (
                    order.get(rec.get("mod_id"), -1),
                    # Sequence first where we have it. Records written
                    # before install_seq existed fall back to the second
                    # they landed in, which ties - and a tie is reported
                    # as no conflict rather than a guessed one.
                    (rec.get("installed_at") or 0, rec.get("install_seq") or 0),
                    rec.get("mod_id"),
                )
            ranked.append((k,) + ranked_cache[k])
        if any(pos < 0 for _k, pos, _t, _m in ranked):
            continue
        intended = max(ranked, key=lambda r: r[1])
        settled = overrides.get(path)
        actual = (
            next((r for r in ranked if r[0] == settled), None)
            if settled
            else max(ranked, key=lambda r: r[2])
        ) or max(ranked, key=lambda r: r[2])
        if intended[0] == actual[0]:
            continue
        # Two records claiming the same instant with no sequence between
        # them: we genuinely do not know who wrote last, and inventing an
        # answer from dict order is how this shipped wrong the first time.
        top = max(r[2] for r in ranked)
        if sum(1 for r in ranked if r[2] == top) > 1:
            continue
        slot = grouped.setdefault(
            (actual[0], intended[0]),
            {
                "actual": actual[0],
                "intended": intended[0],
                "files": 0,
                "example": path,
                "mod_ids": sorted({actual[3], intended[3]} - {None}),
            },
        )
        slot["files"] += 1
    return sorted(grouped.values(), key=lambda g: -g["files"])


def _masters_to_enable(
    data_path: str, entries: list, implicit: set = frozenset(),
    skipped: set = frozenset()
) -> list:
    """Installed plugins an enabled plugin needs as a master, but which
    are switched off.

    Checking only "is the master file on disk" misses this entirely, and
    it is the worse half: Skyrim ships the free Anniversary Edition
    Creation Club files in Data but leaves them out of the plugin list,
    so a collection built on Fishing or Survival Mode installs perfectly
    and then dies on the way in. Device: 13 such masters, 139 enabled
    plugins depending on them.

    Transitive, because a master turned back on brings its own masters.
    """
    try:
        real = {f.lower(): f for f in os.listdir(data_path)}
    except OSError:
        return []
    listed = {n.lower() for n, _ in entries}
    on = {n.lower() for n, enabled in entries if enabled}
    # The game's own masters load whether or not anyone says so, so they
    # are never "missing" and must never be written into the list.
    on |= set(implicit)
    # A deliberate skip is not a mistake to repair. Switching one back on
    # because a dependent still names it as a master defeats the skip and
    # puts the crash back - the dependent is the thing that has to go.
    on |= set(skipped)
    add, frontier = set(), set(on)
    while frontier:
        nxt = set()
        for low in frontier:
            f = real.get(low)
            if not f:
                continue
            for m in _plugin_masters(os.path.join(data_path, f)) or []:
                ml = m.lower()
                # Not on disk is a different problem (and a different
                # message); only act on what we can actually switch on.
                if ml in on or ml in add or ml not in real:
                    continue
                add.add(ml)
                nxt.add(ml)
        frontier = nxt
    # Preserve the file's own spelling where it already lists the plugin,
    # otherwise the name as it sits on disk.
    spelled = {n.lower(): n for n, _ in entries}
    return [
        (spelled.get(low) or real[low], low in listed) for low in sorted(add)
    ]


# Every one of these engines addresses plugins with a single byte, and
# 0xFF is reserved for objects a save file creates - so 255 slots, not
# 256. Skyrim SE and FO4 then give up 0xFE as the shared index that every
# ESL-flagged plugin lives behind, leaving 254 ordinary ones and up to
# 4096 light. FO3 and New Vegas predate ESL entirely: they keep 0xFE as an
# ordinary slot and have no light tier at all.
FULL_SLOT_LIMIT = 254
LIGHT_SLOT_LIMIT = 4096
# 254, not 255: New Vegas said so itself on device with 256 plugins in
# the load order - "maximum plugin limit of 254". Trusting the engine
# over my reading of how the index byte is carved up.
NO_ESL_SLOT_LIMIT = 254

# Reading the ESL flag on a game that has no ESLs is not harmless: bit
# 0x200 means something else in the older engines, so a plugin carrying it
# would be counted as costing no slot and a real overflow would go
# unwarned. Keyed by domain rather than inferred from the plugins.txt
# dialect, which correlates today by coincidence.
ESL_DOMAINS = frozenset({"skyrimspecialedition", "fallout4"})

# File-conflict reporting is off until it reads the collection's modRules.
# List order is not intent: this device's collection ships 1,442 explicit
# rules, and by them the HUD stack was already correct while list order
# called 782 files misplaced. Flipping this on before the rules are read
# would offer to rewrite correct files with the wrong mod's version.
CONFLICTS_USE_MOD_RULES = False


def _slot_usage(
    data_path: str, names: list, implicit: set = frozenset(), esl: bool = True
):
    """(full, light) plugin slots the enabled set consumes.

    `esl` False (FO3, New Vegas) counts every plugin as a full slot: those
    engines have no light tier, and honouring a 0x200 bit there would
    quietly discount plugins that really do occupy a slot.

    Worth its own check because going over does not announce itself: the
    game simply stops loading plugins past the limit, or dies on the way
    in, and nothing in the interface says which of two thousand mods was
    the straw. The device's 1,972-mod collection sat at 208 of 254, so a
    larger one can plausibly cross it.
    """
    try:
        real = {f.lower(): f for f in os.listdir(data_path)}
    except OSError:
        return 0, 0
    full = light = 0
    seen = set()
    for n in list(names) + list(implicit):
        # Deduplicated case-insensitively, because that is how the lookup
        # below resolves: a plugins.txt listing the same plugin under two
        # spellings would otherwise be charged for two slots and warn early.
        key = n.lower()
        if key in seen:
            continue
        seen.add(key)
        f = real.get(key)
        if not f:
            continue
        head = _plugin_header(os.path.join(data_path, f))
        if head is None:
            continue
        if esl and head[0] & PLUGIN_FLAG_LIGHT:
            light += 1
        else:
            full += 1
    return full, light


def _sort_load_order(data_path: str, names: list) -> list:
    """Masters first, then everything else, each group in dependency
    order.

    Two rules, in this priority. Master-flagged plugins load before
    regular ones whatever the file says, so grouping them first makes the
    file mean what the engine will actually do. Within a group, a plugin
    must follow every master it names.

    Deliberately NOT a full LOOT sort: LOOT also carries thousands of
    hand-written rules about which mods should win a conflict, which we
    have no source for. This fixes the class that hard-crashes on load
    and leaves the rest in the order the collection's author chose.
    """
    try:
        real = {f.lower(): f for f in os.listdir(data_path)}
    except OSError:
        return list(names)
    cache = {}
    for n in names:
        f = real.get(n.lower())
        head = _plugin_header(os.path.join(data_path, f)) if f else None
        cache[n.lower()] = head if head else (0, [])
    masters = [n for n in names
               if n.lower().endswith(".esm")
               or cache[n.lower()][0] & PLUGIN_FLAG_MASTER]
    regular = [n for n in names if n not in set(masters)]
    return (_topo_by_masters(data_path, masters, cache)
            + _topo_by_masters(data_path, regular, cache))


def _rewrite_load_order(
    data_path: str, path: str, style: str, implicit: set = frozenset(),
    skipped: set = frozenset()
) -> dict:
    """Sort plugins.txt in place, keeping a copy of what was there.

    Disabled entries are carried along in the same sort: they do not
    load, so their position is irrelevant to the game, but leaving them
    where they were would scatter them through the file for no reason.
    """
    lines = _read_plugins_txt(path)
    if not lines:
        return {"ok": False, "error": "No plugins.txt to sort"}
    header = [l for l in lines if l.strip().startswith("#")]
    entries = _plugin_entries(lines, style)
    # Drop the game's own masters if anything ever wrote them in: they
    # load regardless, and their presence renumbers every other plugin.
    dropped = [n for n, _ in entries if n.lower() in implicit]
    entries = [(n, on) for n, on in entries if n.lower() not in implicit]
    before = _load_order_report(data_path, [n for n, on in entries if on])
    # Switch on the masters that enabled plugins need before ordering -
    # they have to be in the list for the sort to place them at all.
    turned_on = _masters_to_enable(data_path, entries, implicit, skipped)
    for name, already_listed in turned_on:
        if already_listed:
            entries = [(n, True if n.lower() == name.lower() else on)
                       for n, on in entries]
        else:
            entries.append((name, True))
    names = [n for n, _ in entries]
    enabled = {n.lower() for n, on in entries if on} - set(skipped)
    ordered = _sort_load_order(data_path, names)
    after = _load_order_report(
        data_path, [n for n in ordered if n.lower() in enabled]
    )
    # Never make it worse. A cycle or an unreadable Data folder falls
    # back to the input order, and rewriting the file for no gain just
    # risks the copy we would restore from.
    if after > before:
        return {"ok": False, "error": "Sort would not improve the order",
                "violations": before}
    try:
        shutil.copy2(path, path + LOAD_ORDER_BACKUP)
    except OSError:
        pass
    _write_plugins_txt(path, header + [
        ("*" + n if style != "listed" and n.lower() in enabled else n)
        for n in ordered
    ])
    return {"ok": True, "violations_before": before, "violations_after": after,
            "sorted": len(ordered), "enabled_masters": len(turned_on),
            "removed_base_masters": len(dropped)}


def _add_plugins(path: str, names: list, style: str = "starred",
                 game_domain: str = "", data_path: str = "") -> None:
    """Activate plugins. 'starred' (SSE/FO4): '*Name.esp' lines; 'listed'
    (FNV/FO3/Oldrim): a plugin's bare presence in the file activates it.

    A plugin already known to stop this game booting is listed but never
    activated. Switching it on and asking the user to switch it off again
    is a step we can simply not create - and on a listed-style game there
    is no "listed but off", so it is left out of the file entirely.
    """
    bad = KNOWN_BAD_PLUGINS.get(game_domain) or {}
    skips = _load_skips(game_domain) if game_domain else {}
    off = set(bad) | set(skips)
    lines = _read_plugins_txt(path)
    existing = {
        l.lstrip("*").strip().lower()
        for l in lines
        if l.strip() and not l.startswith("#")
    }
    newly_skipped = {}
    for name in names:
        low = name.lower()
        if low in existing:
            continue
        existing.add(low)
        if low in off:
            if low in bad and low not in skips:
                newly_skipped[low] = {"reason": bad[low], "root": True}
            if style != "listed":
                lines.append(name)      # listed, switched off
            continue
        # A mod cannot load without its master. Activating one whose
        # master we have deliberately switched off just moves the crash,
        # so it is left off too - checked against this plugin's own
        # header, which is one small read rather than a scan of the
        # thousands already installed.
        needs = set()
        if data_path:
            needs = {
                m.lower()
                for m in (_plugin_masters(os.path.join(data_path, name)) or [])
            }
        if needs & (off | set(newly_skipped)):
            newly_skipped[low] = {
                "reason": "needs a mod that breaks the game", "root": False,
            }
            if style != "listed":
                lines.append(name)
            continue
        lines.append(name if style == "listed" else "*" + name)
    _write_plugins_txt(path, lines)
    if newly_skipped:
        skips.update(newly_skipped)
        _save_skips(game_domain, skips)
        decky.logger.info(
            f"{game_domain}: installed but left off - "
            + ", ".join(sorted(newly_skipped))
        )


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


def _makedirs_for(dst: str) -> None:
    """Create a destination file's parent directories.

    os.makedirs(exist_ok=True) still raises FileExistsError when a path
    component exists as a FILE, and that killed two installs outright in
    the Gate To Sovngarde run: an earlier mod had left a file named
    Data/Textures/terrain/blackreach, so every later mod wanting that
    directory crashed. A stray extension-less file where a directory
    belongs is mod debris - the directory is what the game reads - so it
    is removed rather than allowed to fail the install.
    """
    parent = os.path.dirname(dst)
    if not parent:
        return
    try:
        os.makedirs(parent, exist_ok=True)
        return
    except FileExistsError:
        pass
    # Walk UP to whichever component exists as a file, clear it, retry.
    # Walking up with dirname rather than splitting on separators keeps
    # this correct on both the device and a Windows dev box.
    probe = parent
    blockers = []
    while probe and not os.path.isdir(probe):
        if os.path.isfile(probe) or os.path.islink(probe):
            blockers.append(probe)
        nxt = os.path.dirname(probe)
        if nxt == probe:
            break
        probe = nxt
    for blocker in blockers:
        decky.logger.warning(
            f"clearing file blocking a mod directory: {blocker}"
        )
        try:
            os.remove(blocker)
        except OSError as e:
            raise FileExistsError(
                f"{blocker} is a file where a folder is needed"
            ) from e
    os.makedirs(parent, exist_ok=True)


def _looks_like_data(dir_path: str) -> bool:
    try:
        names = os.listdir(dir_path)
    except OSError:
        return False
    has_exe = False
    loose_config = False
    for name in names:
        low = name.lower()
        if low.endswith(PLUGIN_EXTENSIONS) or low.endswith((".bsa", ".ba2")):
            return True
        if low in DATA_MARKER_DIRS and os.path.isdir(os.path.join(dir_path, name)):
            return True
        if low.endswith(".exe"):
            has_exe = True
        elif (
            low.endswith((".ini", ".json"))
            # meta.ini is Mod Organizer's export marker, not mod content -
            # claiming the payload here would skip the MO2 handler that
            # strips it, and ship it into the game's Data dir.
            and low != "meta.ini"
            and os.path.isfile(os.path.join(dir_path, name))
        ):
            loose_config = True
    # Config-only mods: KID, SPID, ANIO, FLM, CoMAP and Light Placer
    # addons ship a single .ini or .json that belongs in Data/ and nothing
    # else to recognise them by, so they were refused as "no recognizable
    # Data payload" (six of them in one Gate To Sovngarde run). An .exe
    # alongside means a PC tool rather than a mod - those keep being
    # refused, which is what the tool check downstream is for.
    return loose_config and not has_exe


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


def _mo2_payload(path: str):
    """Mod Organizer 2 exports mark their payload root with a meta.ini:
    the directory CONTAINING it maps 1:1 onto Data/ (seen live: 'Tweaks
    for TTW - Helmet Overlays Patch' = wrapper/meta.ini + a config
    folder). The metadata file itself is dropped so it never lands in
    the game."""
    try:
        for name in os.listdir(path):
            if name.lower() == "meta.ini":
                try:
                    os.remove(os.path.join(path, name))
                except OSError:
                    pass
                return path
    except OSError:
        pass
    return None


def _find_data_payload(scratch: str):
    """Locate the directory whose contents belong in Data/. Handles flat
    archives, a wrapping folder (loose readme-type files beside it are
    ignored), an explicit Data/ folder (up to two levels), and MO2
    exports (meta.ini marks the payload root). Returns None for
    unrecognizable layouts (e.g. FOMOD-only)."""
    if _looks_like_data(scratch):
        return scratch
    entries = os.listdir(scratch)
    dirs = [e for e in entries if os.path.isdir(os.path.join(scratch, e))]
    for d in dirs:
        if d.lower() == "data":
            return os.path.join(scratch, d)
    if _mo2_payload(scratch):
        return scratch
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
        if _mo2_payload(inner):
            return inner
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


def _case_merge_rel(base: str, rel: str, cache=None) -> str:
    """Adopt the on-disk casing of every existing path component under base.
    Wine resolves an exact-case match before falling back to a scan, so twin
    dirs like Data/Textures + Data/textures silently split mods in half -
    each request only ever sees one of them.

    Pass a dict as `cache` to reuse directory listings across the files of
    one install. Without it this is O(files x depth) listdir calls against
    the SAME few directories - and Skyrim's Data/textures can hold
    thousands of entries, so a 2,000-file mod spent most of its install
    re-reading directories it had already read.
    """
    resolved = []
    cur = base
    index = {} if cache is None else cache
    for part in rel.replace("\\", "/").split("/"):
        names = index.get(cur)
        if names is None:
            try:
                names = {e.lower(): e for e in os.listdir(cur)}
            except OSError:
                names = {}
            index[cur] = names
        low = part.lower()
        chosen = names.get(low)
        if chosen is None:
            # Nothing on disk yet: this install is about to create it.
            # Record the spelling we settled on so a later file differing
            # only in case lands in the SAME directory instead of a twin
            # (the exact split this function exists to prevent).
            chosen = part
            names[low] = part
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

# The game's own menu XMLs. Mods may legitimately OVERWRITE these (HD
# Reworked ships its own rendering.xml) - back the vanilla file up on
# overwrite and restore it on uninstall; never delete them or strip
# their filelist lines.
W3_VANILLA_MENU_XMLS = {
    "audio.xml", "display.xml", "gameplay.xml", "gamma.xml",
    "graphics.xml", "graphicsdx11.xml", "hdr.xml", "hidden.xml",
    "hud.xml", "input.xml", "localization.xml", "postprocess.xml",
    "rendering.xml",
}
W3_VANILLA_BACKUP_SUFFIX = ".decky-vanilla"


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


def _w3_filelist_remove(pc_dir: str, xml_name: str) -> None:
    for fl in ("dx11filelist.txt", "dx12filelist.txt"):
        path = _adopt_case(os.path.join(pc_dir, fl))
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            lines = f.read().splitlines()
        kept = [l for l in lines if l.strip() != f"{xml_name};"]
        if kept != lines:
            with open(path, "w", encoding="utf-8", newline="\r\n") as f:
                f.write("\n".join(kept) + "\n")


def _w3_remove_menu_xmls(install_path: str, rec: dict) -> None:
    """Undo a record's menu-XML registration: delete the XMLs from the
    user_config_matrix dir and strip their filelist lines. Leaving them
    behind (pre-v0.25 uninstalls did) kept dead mod menus around and, in
    the orphan case, contributed to boot failures. Vanilla-named XMLs
    (a mod overwrote the game's own file) are restored from the backup
    the install took, never deleted, and their filelist lines stay."""
    xmls = (rec or {}).get("menuXmls") or []
    if not xmls:
        return
    pc_dir = os.path.join(install_path, *W3_MENU_DIR.split("/"))
    for name in xmls:
        if not _safe_rel_path(name) or "/" in name:
            continue
        path = _adopt_case(os.path.join(pc_dir, name))
        if name.lower() in W3_VANILLA_MENU_XMLS:
            backup = path + W3_VANILLA_BACKUP_SUFFIX
            try:
                if os.path.isfile(backup):
                    if os.path.isfile(path):
                        os.remove(path)
                    os.rename(backup, path)
            except OSError:
                pass
            continue
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        _w3_filelist_remove(pc_dir, name)


# Auto script-merge: the game ships its vanilla script sources at
# content/content0/scripts - a real three-way merge base. Non-overlapping
# edits from different mods merge into a highest-priority merged mod
# (alphabetical load order: digits sort before letters), exactly Script
# Merger's model. Overlapping edits still refuse.
W3_VANILLA_SCRIPTS = "content/content0/scripts"
W3_MERGED_MOD = "mod0000_DeckyMerged"

# The game's own dlc/ folders. Mods legitimately PATCH these by shipping
# dlc/bob-style overlays - those must MERGE into the official folder
# (per-file record), never replace it: the old rmtree-and-move destroyed
# Blood & Wine and every free DLC, and uninstall then deleted the
# replacements (bricked a device install; Steam verify required).
W3_OFFICIAL_DLC = {"bob", "ep1"} | {f"dlc{i}" for i in range(1, 17)}


def _w3_merge3(
    base_lines: list, ours_lines: list, theirs_lines: list,
    deadline: float = None,
):
    """Three-way line merge. Returns the merged line list, or None when
    the two sides change overlapping regions differently (a genuine
    conflict). Identical changes collapse to one. Raises TimeoutError
    past the deadline - difflib is quadratic on files full of repeated
    lines (r4player.ws froze the whole backend for minutes)."""
    import difflib

    def regions(side_lines):
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("merge budget exceeded")
        sm = difflib.SequenceMatcher(
            None, base_lines, side_lines, autojunk=False
        )
        out = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                out.append((i1, i2, side_lines[j1:j2]))
        return out

    events = [(s, e, rep, "o") for s, e, rep in regions(ours_lines)]
    events += [(s, e, rep, "t") for s, e, rep in regions(theirs_lines)]
    events.sort(key=lambda x: (x[0], x[1], x[3]))
    merged = []
    pos = 0
    i = 0
    while i < len(events):
        cluster = [events[i]]
        cs, ce = events[i][0], events[i][1]
        j = i + 1
        while j < len(events) and events[j][0] < ce:
            cluster.append(events[j])
            ce = max(ce, events[j][1])
            j += 1
        sides = {c[3] for c in cluster}
        if len(sides) == 2:
            ours_part = [c[:3] for c in cluster if c[3] == "o"]
            theirs_part = [c[:3] for c in cluster if c[3] == "t"]
            if ours_part == theirs_part:
                # both mods made the identical change
                merged.extend(base_lines[pos:cs])
                p = cs
                for rs, re_, rep in ours_part:
                    merged.extend(base_lines[p:rs])
                    merged.extend(rep)
                    p = re_
                pos = ce
                i = j
                continue
            return None
        merged.extend(base_lines[pos:cs])
        p = cs
        for rs, re_, rep, _side in cluster:
            merged.extend(base_lines[p:rs])
            merged.extend(rep)
            p = re_
        pos = ce
        i = j
    merged.extend(base_lines[pos:])
    return merged


def _adopt_case_path(base: str, rel: str) -> str:
    """Adopt on-disk casing for EVERY component of rel under base -
    _adopt_case alone only fixes the last segment."""
    cur = base
    for part in rel.split("/"):
        cur = _adopt_case(os.path.join(cur, part))
    return cur


def _w3_read_lines(path: str):
    """Read a script EOL-normalized AND encoding-aware: mods ship .ws
    files as UTF-16 too (Immersive Realtime Cutscenes) - decoding those
    as UTF-8 merged NUL-riddled garbage into the game (boot-time compile
    error on device). BOM decides; residual NULs mean we misread -
    treat as unreadable rather than merge garbage."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    if "\x00" in text:
        return None
    return text.splitlines()


def _w3_write_script(path: str, lines: list) -> None:
    # UTF-8 with BOM + CRLF: the script compiler's happiest diet.
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")


def _w3_current_script(install_path: str, mods_path: str, rel: str,
                       owner_path: str):
    """The currently-winning version of a script: the merged copy when
    one exists, else the owning mod's real file path."""
    merged = os.path.join(
        mods_path, W3_MERGED_MOD, "content", "scripts", *rel.split("/")
    )
    if os.path.isfile(merged):
        return merged
    return owner_path


def _w3_try_merge_conflicts(
    game_domain: str, install_path: str, mods_path: str,
    conflicts: list, settings: dict,
) -> list:
    """Attempt a three-way merge for every conflict tuple
    (rel, owner_folder, incoming_path, owner_path). ALL must merge
    cleanly; on success the merged files land in the merged mod and
    participants are tracked for unmerge. Returns the list of merged
    rels, or None when any file can't merge."""
    staged = []
    for rel, owner, incoming, owner_path in conflicts:
        base = _adopt_case_path(
            os.path.join(install_path, *W3_VANILLA_SCRIPTS.split("/")), rel
        )
        base_lines = _w3_read_lines(base)
        ours_lines = _w3_read_lines(
            _w3_current_script(install_path, mods_path, rel, owner_path)
        )
        theirs_lines = _w3_read_lines(incoming)
        if base_lines is None or ours_lines is None or theirs_lines is None:
            decky.logger.info(
                f"W3 merge {rel!r}: missing side "
                f"(base={base_lines is not None}, "
                f"ours={ours_lines is not None}, "
                f"theirs={theirs_lines is not None})"
            )
            return None
        try:
            merged = _w3_merge3(
                base_lines, ours_lines, theirs_lines,
                deadline=time.monotonic() + 25,
            )
        except TimeoutError:
            decky.logger.info(
                f"W3 merge {rel!r}: timed out "
                f"(base {len(base_lines)} lines, ours "
                f"{len(ours_lines)}, theirs {len(theirs_lines)})"
            )
            return None
        if merged is None:
            decky.logger.info(
                f"W3 merge {rel!r}: overlapping edits "
                f"(base {len(base_lines)} lines, ours "
                f"{len(ours_lines)}, theirs {len(theirs_lines)})"
            )
            return None
        staged.append((rel, owner, merged))
    merges = settings.setdefault("w3_merges", {}).setdefault(game_domain, {})
    for rel, owner, merged in staged:
        dst = os.path.join(
            mods_path, W3_MERGED_MOD, "content", "scripts", *rel.split("/")
        )
        _w3_write_script(dst, merged)
        entry = merges.setdefault(rel, {"mods": [owner]})
        if owner not in entry["mods"]:
            entry["mods"].append(owner)
    # a friendly record so My Mods explains the folder
    installed = settings.setdefault("installed", {}).setdefault(
        game_domain, {}
    )
    if W3_MERGED_MOD not in installed:
        installed[W3_MERGED_MOD] = {
            "name": "Auto-merged scripts (keep enabled)",
            "version": "",
            "installed_at": int(time.time()),
            "source": "merge",
            "mode": "folder",
        }
    return [rel for rel, _o, _m in staged]


def _w3_register_merge_participant(
    game_domain: str, settings: dict, rels: list, folder: str
) -> None:
    merges = settings.setdefault("w3_merges", {}).setdefault(game_domain, {})
    for rel in rels:
        entry = merges.setdefault(rel, {"mods": []})
        if folder not in entry["mods"]:
            entry["mods"].append(folder)


def _w3_unmerge(
    game_domain: str, install_path: str, mods_path: str, folder: str,
    settings: dict,
) -> None:
    """A merge participant is being uninstalled: recompute each of its
    merged scripts from the remaining participants (or drop the merged
    copy when one participant is left - its own file wins again)."""
    merges = settings.get("w3_merges", {}).get(game_domain, {})
    for rel in list(merges.keys()):
        entry = merges[rel]
        if folder not in entry.get("mods", []):
            continue
        entry["mods"] = [m for m in entry["mods"] if m != folder]
        dst = os.path.join(
            mods_path, W3_MERGED_MOD, "content", "scripts", *rel.split("/")
        )
        remaining = [
            m
            for m in entry["mods"]
            if os.path.isfile(
                _adopt_case_path(
                    os.path.join(mods_path, m, "content", "scripts"), rel
                )
            )
        ]
        if len(remaining) <= 1:
            try:
                if os.path.isfile(dst):
                    os.remove(dst)
            except OSError:
                pass
            merges.pop(rel, None)
            continue
        base_lines = _w3_read_lines(
            _adopt_case_path(
                os.path.join(install_path, *W3_VANILLA_SCRIPTS.split("/")),
                rel,
            )
        )
        acc = base_lines
        ok = base_lines is not None
        if ok:
            deadline = time.monotonic() + 25
            for m in remaining:
                side = _w3_read_lines(
                    _adopt_case_path(
                        os.path.join(mods_path, m, "content", "scripts"),
                        rel,
                    )
                )
                try:
                    acc = (
                        _w3_merge3(base_lines, acc, side, deadline=deadline)
                        if side
                        else None
                    )
                except TimeoutError:
                    acc = None
                if acc is None:
                    ok = False
                    break
        if ok:
            _w3_write_script(dst, acc)
            entry["mods"] = remaining
        else:
            # can't cleanly recompute - keep the old merged file rather
            # than break the game; log for the health check to surface
            decky.logger.info(
                f"W3 unmerge of {folder!r}: could not recompute {rel!r}, "
                "keeping the previous merged copy"
            )
    # empty merged mod folder cleans itself up
    merged_root = os.path.join(mods_path, W3_MERGED_MOD)
    if os.path.isdir(merged_root):
        empty = not any(files for _r, _d, files in os.walk(merged_root))
        if empty:
            _force_rmtree(merged_root)


def _w3_prune_filelists(pc_dir: str) -> None:
    """Drop filelist lines whose XML no longer exists - a filelist entry
    pointing at a missing file crashes the game at the menu."""
    for fl in ("dx11filelist.txt", "dx12filelist.txt"):
        path = _adopt_case(os.path.join(pc_dir, fl))
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            lines = f.read().splitlines()
        kept = []
        for line in lines:
            name = line.strip().rstrip(";")
            if not name or os.path.isfile(
                _adopt_case(os.path.join(pc_dir, name))
            ):
                kept.append(line)
        if kept != lines:
            with open(path, "w", encoding="utf-8", newline="\r\n") as f:
                f.write("\n".join(kept) + "\n")


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
                    path = os.path.join(root, n)
                    rel = os.path.relpath(path, scripts)
                    # lowered rel for Wine-style comparison, REAL path
                    # for reading - Linux is case-sensitive and mods
                    # ship Game/Player/-style casing freely.
                    owners[rel.replace(os.sep, "/").lower()] = (
                        folder,
                        path,
                    )
    return owners


def _w3_payload_scripts(folder_path: str) -> list:
    """(lowered_rel, real_path) for every .ws in the payload."""
    scripts = os.path.join(folder_path, "content", "scripts")
    out = []
    if not os.path.isdir(scripts):
        return out
    for root, _dirs, names in os.walk(scripts):
        for n in names:
            if n.lower().endswith(".ws"):
                path = os.path.join(root, n)
                rel = os.path.relpath(path, scripts)
                out.append((rel.replace(os.sep, "/").lower(), path))
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

    if not mod_dirs and not dlc_dirs and not menu_xmls:
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
            tops = ", ".join(sorted(os.listdir(scratch))[:6])
            decky.logger.info(
                f"W3 {mod_name!r}: no layout; top level: {tops}"
            )
            # Desktop utilities (Script Merger, W3 Mod Manager) ship exes
            # instead of mod folders - classify them so collections skip
            # instead of failing.
            for root, _dirs, names in os.walk(scratch):
                for n in names:
                    if n.lower().endswith(".exe"):
                        return [], [], [], (
                            "tool",
                            f"{mod_name} looks like a PC modding tool "
                            f"({n}), not a mod the game loads - it needs "
                            "a desktop setup, so it was skipped.",
                        )
            # bin/ overlay mods (DX12 VRAM Relief: dlls + settings under
            # bin/, no mod folder) install into the game root instead.
            for e in os.listdir(scratch):
                if e.lower() == "bin" and os.path.isdir(
                    os.path.join(scratch, e)
                ):
                    return [], [], [], ("binoverlay", "")
            return [], [], menu_xmls, (
                "layout",
                "No Witcher 3 mod layout found in this archive (expected "
                "mod*/dlc* folders or a content/ folder). "
                f"It contains: {tops}",
            )

    # Script-conflict gate against everything already installed. The
    # caller attempts a three-way auto-merge before giving up.
    owners = _w3_installed_scripts(mods_path)
    conflicts = []
    for d in mod_dirs:
        for rel, incoming_path in _w3_payload_scripts(d):
            owned = owners.get(rel)
            if owned and owned[0].lower() != os.path.basename(d).lower():
                owner_folder, owner_path = owned
                conflicts.append(
                    (rel, owner_folder, incoming_path, owner_path)
                )
    if conflicts:
        return mod_dirs, dlc_dirs, menu_xmls, ("conflicts", conflicts)
    return mod_dirs, dlc_dirs, menu_xmls, None


# ---- Cyberpunk 2077 layout ---------------------------------------------------
# Framework-tier mods ship game-root-relative payloads across several
# known roots (all shapes verified by downloading the frameworks
# themselves, 2026-08-04): CET = bin/x64/version.dll + plugins/, RED4ext
# = bin/x64/winmm.dll + red4ext/, ArchiveXL/TweakXL = red4ext/plugins/
# + r6/, redscript = engine/tools + r6/. Bare .archive files still go
# flat into archive/pc/mod. REDmod-format (mods/<name>/info.json) needs
# the free DLC + '-modded' + a deploy step - skipped with a clear
# message for now.

CP77_ROOTS = ("archive", "bin", "red4ext", "r6", "engine")
CP77_ARCHIVE_DIR = "archive/pc/mod"

# Cyber Engine Tweaks Lua mods. Verified against the CET wiki's own
# "Mod Structure" page rather than inferred:
#
#   Cyberpunk 2077/bin/x64/plugins/cyber_engine_tweaks/mods/<my_mod>/init.lua
#
# "CET will be looking for an init.lua file inside your mod folder. This is
# the entry point of your mod, and gets executed when the game is launched."
# Extra files are allowed in that folder or a subfolder of it.
#
# So init.lua IS the detector, and it is a strong one: it is the one file CET
# requires and the one every CET mod therefore has. Mods that ship the whole
# bin/... path already routed as a "bin" root and still do - this covers the
# ones that ship only their own folder, which matched none of the known roots
# and were refused as "no Cyberpunk mod layout found". CET mods are a large
# category for this game, so that refusal was turning away real mods.
CP77_CET_DIR = "bin/x64/plugins/cyber_engine_tweaks/mods"
CP77_CET_ENTRY = "init.lua"


def _route_cp77_payload(scratch: str, mod_name: str):
    """Classify a CP77 archive. Returns (files, err) where files is a
    list of (game-root-relative rel, source path) and err is None or a
    (kind, message) tuple like the witcher router's."""

    def known_roots(base):
        try:
            entries = os.listdir(base)
        except OSError:
            return []
        return [
            e
            for e in entries
            if e.lower() in CP77_ROOTS
            and os.path.isdir(os.path.join(base, e))
        ]

    base = scratch
    roots = known_roots(base)
    if not roots:
        # single wrapper dir unwrap (versioned folders etc.)
        subs = [
            os.path.join(scratch, e)
            for e in os.listdir(scratch)
            if os.path.isdir(os.path.join(scratch, e))
        ]
        if len(subs) == 1 and known_roots(subs[0]):
            base = subs[0]
            roots = known_roots(base)
    if roots:
        files = []
        for r in roots:
            rp = os.path.join(base, r)
            for root_, _dirs, names in os.walk(rp):
                for n in names:
                    src = os.path.join(root_, n)
                    rel = os.path.relpath(src, base).replace(os.sep, "/")
                    if _safe_rel_path(rel):
                        files.append((rel, src))
        if files:
            return files, None
    # REDmod-format payload (mods/<name>/info.json) - must be checked
    # BEFORE the bare-archive sweep: REDmods contain .archive files that
    # would otherwise install flat into the wrong place.
    for cand in (scratch, *[
        os.path.join(scratch, e)
        for e in os.listdir(scratch)
        if os.path.isdir(os.path.join(scratch, e))
    ]):
        mods_dir = os.path.join(cand, "mods")
        if os.path.isdir(mods_dir):
            for sub in os.listdir(mods_dir):
                if os.path.isfile(
                    os.path.join(mods_dir, sub, "info.json")
                ):
                    return [], (
                        "layout",
                        f"{mod_name} is a REDmod-format mod - that needs "
                        "the free REDmod DLC and a deploy step we don't "
                        "support yet. Many mods offer a classic version "
                        "as a separate file.",
                    )
    # CET Lua mods: a folder holding init.lua, destined for
    # bin/x64/plugins/cyber_engine_tweaks/mods/<folder>/.
    #
    # Checked before the bare-archive sweep because a CET mod may ship
    # .archive files alongside its Lua, and the sweep would take those and
    # leave the Lua behind - installing half a mod and reporting success,
    # which is the worst kind of report.
    cet_dirs = []
    for root_, dirs, names in os.walk(scratch):
        if any(n.lower() == CP77_CET_ENTRY for n in names):
            cet_dirs.append(root_)
            # Its subfolders belong to it, so stop descending.
            dirs[:] = []
    cet_files, claimed = [], set()
    for d in cet_dirs:
        # The folder the author shipped IS the mod's name to CET and to
        # every mod that references it, so it is preserved. Only when
        # init.lua sits at the very top of the archive, with no folder of
        # its own, is a name derived from the Nexus mod name.
        folder = (
            _safe_name(mod_name).replace(" ", "_").lower()
            if os.path.normpath(d) == os.path.normpath(scratch)
            else os.path.basename(d)
        )
        for sub_root, _sub_dirs, names in os.walk(d):
            for n in names:
                src = os.path.join(sub_root, n)
                inner = os.path.relpath(src, d).replace(os.sep, "/")
                rel = f"{CP77_CET_DIR}/{folder}/{inner}"
                if _safe_rel_path(rel):
                    cet_files.append((rel, src))
                    claimed.add(src)
    # bare archive files (the classic drop-in tier)
    flat = []
    for root_, _dirs, names in os.walk(scratch):
        for n in names:
            src = os.path.join(root_, n)
            if n.lower().endswith((".archive", ".xl")) and src not in claimed:
                flat.append((f"{CP77_ARCHIVE_DIR}/{n}", src))
    if cet_files or flat:
        return cet_files + flat, None
    for root_, _dirs, names in os.walk(scratch):
        for n in names:
            if n.lower().endswith(".exe"):
                return [], (
                    "tool",
                    f"{mod_name} looks like a PC modding tool ({n}), not "
                    "a mod the game loads - it needs a desktop setup, so "
                    "it was skipped.",
                )
    # Lua, but not a CET mod. Cyber Engine Tweaks loads init.lua and nothing
    # else, so a .lua file without one is a script you paste into the CET
    # console rather than a mod anything installs. Cheat Script (mod 542) is
    # the case: it ships CheatScript/CheatScript.lua and its own page says
    # "just use it with the Cyber Engine Tweaks console". Refusing it is
    # right; refusing it as "no Cyberpunk mod layout found" told Michael the
    # archive was broken when it was doing exactly what it advertises.
    for root_, _dirs, names in os.walk(scratch):
        for n in names:
            if n.lower().endswith(".lua"):
                return [], (
                    "console_script",
                    f"{mod_name} is a console script, not an installable "
                    "mod - it has no init.lua, so Cyber Engine Tweaks will "
                    "not load it on its own. Open the CET console in game "
                    "and run it from there.",
                )
    tops = ", ".join(sorted(os.listdir(scratch))[:6])
    return [], (
        "layout",
        "No Cyberpunk mod layout found in this archive (expected "
        "archive/bin/red4ext/r6/engine roots, .archive files, or a CET "
        f"mod folder with init.lua). It contains: {tops}",
    )


# ---- me3 layout (FromSoftware games) ----------------------------------------
# FromSoft games load mods through me3, which reads ONE profile file (.me3,
# TOML) listing asset packages and native dlls. That file is this tier's
# activation manifest - the plugins.txt of the FromSoft world - so it is
# regenerated from the install records after every install, toggle and
# uninstall rather than edited in place.
#
# Mods live under the plugin's own runtime dir, never in the game folder:
# me3 boots the real exe (Game/eldenring.exe) instead of the anti-cheat
# launcher, and the deal we made is that the game install stays untouched
# and byte-identical to vanilla. Paths inside a .me3 file resolve relative
# to the file itself, so the whole tree is relocatable.

ME3_ROOT = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "me3")
ME3_BIN = os.path.join(ME3_ROOT, "bin", "me3")
ME3_WIN64 = os.path.join(ME3_ROOT, "bin", "win64")
ME3_PROFILES_DIR = os.path.join(ME3_ROOT, "profiles")
ME3_RELEASE_URL = (
    "https://github.com/garyttierney/me3/releases/latest/download/"
    "me3-linux-amd64.tar.gz"
)

# Nexus domain -> the game identifier me3 expects in [[supports]].
ME3_GAMES = {
    "eldenring": "eldenring",
    "darksouls3": "darksouls3",
    "sekiro": "sekiro",
    "armoredcore6firesofrubicon": "armoredcore6",
    "eldenringnightreign": "nightreign",
}

# Every FromSoft title writes a single .sl2 save; me3's savefile key points
# the modded session at its own copy so a vanilla character can never be
# written to. Non-overridable by design.
ME3_SAVEFILE = "deckynexus-modded.sl2"

# Natives are declared with their path and nothing else, deliberately.
#
# We used to hand Seamless Co-op `load_early` plus
# `initializer = { function = "modengine_ext_init" }`, on the reasoning
# that it needs to hook before the game starts. That crashed Elden Ring
# about eight seconds into every launch (device, 2026-08-07). me3 detects
# ModEngine2-style natives on its own and says so - "loaded native with
# me2 compatibility shim" - so our initializer was a second call on top
# of the one me3 had already made. Let me3 decide how to load a native;
# it knows more about the mod than a hard-coded table here can.

# Top-level names that mark an archive as an asset (package) mod: the
# DVDBND directory roots plus the params blob every balance mod ships.
ME3_ASSET_MARKERS = {
    "regulation.bin", "action", "asset", "chr", "cutscene", "event", "expansion",
    "font", "map", "material", "menu", "movie", "msg", "obj", "other", "param",
    "parts", "remo", "script", "sd", "sfx", "shader", "sound",
}


# Diagnostics-grade, like the launch-options peek: enough to answer "has
# Steam been told which Proton to use", not a VDF parser.
_VDF_COMPAT_TOOL_RE = re.compile(r'"(\d+)"\s*\{[^{}]*?"name"\s*"([^"]*)"', re.S)


def _steam_compat_tool(app_id: int) -> str:
    """The Proton Steam runs this app with: its own CompatToolMapping
    entry, else the global default (app 0). Empty means Steam has picked
    one implicitly and written nothing down - which matters because me3
    reads this same mapping, and when it finds nothing it falls back to
    the game's verified-Deck runtime, a Proton build that may well not
    be installed."""
    path = os.path.join(
        decky.DECKY_USER_HOME, ".steam", "steam", "config", "config.vdf"
    )
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    start = text.find("CompatToolMapping")
    if start < 0:
        return ""
    mapping = {}
    for m in _VDF_COMPAT_TOOL_RE.finditer(text[start:start + 20000]):
        mapping[m.group(1)] = m.group(2)
    return mapping.get(str(app_id)) or mapping.get("0") or ""


def _me3_profile_dir(game_domain: str) -> str:
    return os.path.join(ME3_PROFILES_DIR, game_domain)


def _me3_profile_path(game_domain: str) -> str:
    return os.path.join(_me3_profile_dir(game_domain), "deckynexus.me3")


def _me3_mods_dir(game_domain: str) -> str:
    return os.path.join(_me3_profile_dir(game_domain), "mods")


def _me3_toml_str(value: str) -> str:
    """TOML basic string. Mod names reach this from Nexus, so escape
    rather than trust."""
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )
    return f'"{escaped}"'


def _me3_records(settings: dict, game_domain: str) -> list:
    """(key, record) pairs for this game's me3 mods, in install order so
    the profile's load order is stable across rewrites."""
    records = settings.get("installed", {}).get(game_domain, {})
    me3 = [
        (key, rec)
        for key, rec in records.items()
        if (rec or {}).get("mode") == "me3"
    ]
    me3.sort(key=lambda kv: (kv[1].get("installed_at") or 0, kv[0].lower()))
    return me3


def _me3_regulation_owner(settings: dict, game_domain: str, skip_key: str = ""):
    """Which enabled mod currently owns regulation.bin, if any. Two of
    them cannot coexist: regulation.bin is one file holding every game
    param, so the second mod silently wins and the first appears broken."""
    for key, rec in _me3_records(settings, game_domain):
        if key == skip_key or not rec.get("regulation"):
            continue
        if rec.get("enabled", True):
            return rec.get("name") or key
    return None


def _write_me3_profile(game_domain: str, settings: dict) -> str:
    """Regenerate the .me3 profile from the install records; returns its
    path. Two lines are deliberately not configurable:

    - start_online is NEVER emitted. me3 blocks matchmaking by default and
      modded online play gets FromSoft accounts banned, so the plugin has
      no path - not a toggle, not a setting - that puts a modded session
      online.
    - savefile always redirects to a modded copy, so vanilla characters
      cannot be written to by a modded session.
    """
    game = ME3_GAMES.get(game_domain)
    if not game:
        raise ValueError(f"{game_domain} is not an me3 game")
    profile_dir = _me3_profile_dir(game_domain)
    os.makedirs(profile_dir, exist_ok=True)
    lines = [
        "# Generated by decky-nexus - edits are overwritten on the next",
        "# install, toggle or uninstall. Matchmaking stays off and modded",
        "# saves stay separate: both are enforced, not defaults.",
        'profileVersion = "v1"',
        f"savefile = {_me3_toml_str(ME3_SAVEFILE)}",
        "",
        "[[supports]]",
        f"game = {_me3_toml_str(game)}",
    ]
    for key, rec in _me3_records(settings, game_domain):
        folder = rec.get("folder") or key
        enabled = bool(rec.get("enabled", True))
        # Mod names come from Nexus; a newline in one would end the
        # comment and turn the rest into broken TOML.
        label = re.sub(r"\s+", " ", str(rec.get("name") or key))[:80]
        if rec.get("package"):
            sub = rec.get("package_subpath") or ""
            asset_path = f"mods/{folder}/{sub}" if sub else f"mods/{folder}"
            lines += [
                "",
                f"# {label}",
                "[[packages]]",
                f"id = {_me3_toml_str(folder)}",
                f"path = {_me3_toml_str(asset_path)}",
            ]
            if not enabled:
                lines.append("enabled = false")
        for native in rec.get("natives") or []:
            lines += [
                "",
                f"# {label}",
                "[[natives]]",
                f"path = {_me3_toml_str(f'mods/{folder}/{native}')}",
            ]
            if not enabled:
                lines.append("enabled = false")
    path = _me3_profile_path(game_domain)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _remove_me3_record(game_domain: str, record_key: str, settings: dict) -> bool:
    """Delete an me3 mod's folder and drop its record. The caller
    rewrites the profile once the whole batch is done. Returns True if
    such a record existed."""
    records = settings.get("installed", {}).get(game_domain, {})
    rec = records.get(record_key)
    if not rec or rec.get("mode") != "me3":
        return False
    folder = rec.get("folder") or record_key
    if _safe_rel_path(folder) and "/" not in folder:
        _force_rmtree(os.path.join(_me3_mods_dir(game_domain), folder))
    records.pop(record_key, None)
    return True


def _me3_coop_ini(settings: dict, game_domain: str):
    """Path of Seamless Co-op's settings ini, if that mod is installed.
    It ships beside ersc.dll and holds the session password."""
    for key, rec in _me3_records(settings, game_domain):
        if not any(
            os.path.basename(n).lower() == "ersc.dll"
            for n in rec.get("natives") or []
        ):
            continue
        folder = os.path.join(
            _me3_mods_dir(game_domain), rec.get("folder") or key
        )
        for root, _dirs, names in os.walk(folder):
            for name in names:
                if name.lower() == "ersc_settings.ini":
                    return os.path.join(root, name)
    return None


def _me3_payload_root(scratch: str) -> str:
    """Unwrap the version-named folder archives usually ship (Seamless
    Co-op's SeamlessCoop/, 'MyMod v1.2/'), stopping at the first level
    that holds mod content ITSELF - a marker dir or a dll sitting right
    there. Nested content doesn't count, or nothing would ever unwrap."""
    current = scratch
    for _ in range(3):
        try:
            entries = os.listdir(current)
        except OSError:
            return current
        here = [e.lower() for e in entries]
        if any(n in ME3_ASSET_MARKERS for n in here) or any(
            n.endswith(".dll") for n in here
        ):
            return current
        dirs = [e for e in entries if os.path.isdir(os.path.join(current, e))]
        # A lone folder beside a readme or two is a wrapper, not the mod.
        if len(dirs) == 1 and len(entries) <= 3:
            current = os.path.join(current, dirs[0])
            continue
        return current
    return current


def _me3_has_markers(path: str) -> bool:
    """Does this directory hold DVDBND override content? regulation.bin
    is decisive; a marker DIRECTORY counts only when it holds something
    other than dlls, so a mod folder that happens to be called script/
    or menu/ and contains one dll reads as the dll mod it is."""
    try:
        entries = os.listdir(path)
    except OSError:
        return False
    for name in entries:
        low = name.lower()
        if low not in ME3_ASSET_MARKERS:
            continue
        child = os.path.join(path, name)
        if os.path.isfile(child):
            return low == "regulation.bin"
        for _root, _dirs, names in os.walk(child):
            if any(not n.lower().endswith(".dll") for n in names):
                return True
    return False


def _me3_assets_subpath(path: str, depth: int = 0):
    """Where the DVDBND override content lives, relative to the payload
    root ("" = the root itself), or None if this mod ships no assets.
    me3's own documented layout puts them one level down in mod/ beside a
    sibling natives/, so a root-only check would silently drop half the
    mod."""
    if _me3_has_markers(path):
        return ""
    if depth >= 2:
        return None
    try:
        entries = sorted(os.listdir(path))
    except OSError:
        return None
    found = []
    for name in entries:
        child = os.path.join(path, name)
        if not os.path.isdir(child):
            continue
        deeper = _me3_assets_subpath(child, depth + 1)
        if deeper is not None:
            found.append(f"{name}/{deeper}" if deeper else name)
    if not found:
        return None
    # Leftover/backup folders sort ahead of the real payload often
    # enough to matter: the one carrying regulation.bin wins.
    for rel in found:
        if os.path.isfile(os.path.join(path, *rel.split("/"), "regulation.bin")):
            return rel
    return found[0]


def _me3_natives(path: str, assets_subpath):
    """Dlls me3 should load, relative to the payload root. Dlls sitting
    inside asset content are overridden game data, not mod hosts -
    force-loading one of those crashes the game. Everything OUTSIDE the
    asset tree is a candidate, including the natives/ folder me3's own
    layout puts beside it."""
    inside_assets = (
        (lambda rel: rel == assets_subpath
         or rel.startswith(f"{assets_subpath}/"))
        if assets_subpath
        else (lambda rel: rel.split("/")[0].lower() in ME3_ASSET_MARKERS)
        if assets_subpath == ""
        else (lambda _rel: False)
    )
    dlls = []
    for root, _dirs, names in os.walk(path):
        rel_dir = os.path.relpath(root, path).replace(os.sep, "/")
        if rel_dir != "." and inside_assets(rel_dir):
            continue
        for name in names:
            if not name.lower().endswith(".dll"):
                continue
            rel = os.path.relpath(os.path.join(root, name), path)
            dlls.append(rel.replace(os.sep, "/"))
    return sorted(dlls)


def _route_me3_payload(scratch: str, mod_name: str):
    """Decide what an extracted FromSoft archive is. Returns
    (payload_root, assets_subpath_or_None, dlls, None), or
    (None, None, [], (kind, message)) for archives we won't install."""
    root = _me3_payload_root(scratch)
    assets = _me3_assets_subpath(root)
    dlls = _me3_natives(root, assets)
    if assets is None and not dlls:
        names = sorted(os.listdir(root))
        exes = [n for n in names if n.lower().endswith(".exe")]
        if exes:
            return None, None, [], (
                "tool",
                f"{mod_name} is a Windows modding tool ({exes[0]}) rather "
                "than a mod the game loads - it needs a desktop setup, so "
                "it was skipped.",
            )
        return None, None, [], (
            "layout",
            "No FromSoft mod layout found in this archive (expected "
            "regulation.bin, game asset folders like parts/ or chr/, or a "
            f".dll). It contains: {', '.join(names[:6])}",
        )
    # Option-pack archives ship the same dll under several variant
    # folders. Loading two copies of one early-load native crashes the
    # game, and picking a variant for the user is a guess we shouldn't
    # make - so say which choice has to be made by hand.
    seen = {}
    for rel in dlls:
        seen.setdefault(os.path.basename(rel).lower(), []).append(rel)
    clashes = [paths for paths in seen.values() if len(paths) > 1]
    if clashes:
        variants = ", ".join(clashes[0][:3])
        return None, None, [], (
            "choice",
            f"{mod_name} contains several versions of the same mod "
            f"({variants}). Installing them all at once would crash the "
            "game - download the single version you want from the mod's "
            "Files tab instead.",
        )
    return root, assets, dlls, None


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
    """Normalise a FOMOD source/destination path.

    A destination of "." means the Data root, and authors write it often.
    Left alone it produced "./thing.esp", whose first component is "." -
    which the traversal guard rejects, so EVERY file of that option was
    silently dropped and the install staged nothing ("Store Entrance
    Doorbells", "YASTM": 0 files, no error). Dropping "." components here
    keeps ".." rejected, which is the case the guard is actually for.
    """
    parts = (path or "").replace("\\", "/").split("/")
    return "/".join(p for p in parts if p not in ("", "."))


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
                    _makedirs_for(dst)
                    if os.path.isfile(dst):
                        os.remove(dst)
                    shutil.copy2(os.path.join(root, n), dst)
                    count += 1
        else:
            target_rel = dest or os.path.basename(src)
            if not _safe_rel_path(target_rel):
                continue
            dst = os.path.join(staging, *target_rel.split("/"))
            _makedirs_for(dst)
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


# Script-extender plugin folders, keyed by the first component of the
# log path ("Skyrim Special Edition/SKSE/skse64.log" -> SKSE).
SE_PLUGIN_DIRS = {
    "Skyrim Special Edition": ("Data", "SKSE", "Plugins"),
    "Fallout4": ("Data", "F4SE", "Plugins"),
    "FalloutNV": ("Data", "NVSE", "Plugins"),
}
# Parked plugins keep their file and lose their extension - script
# extenders only scan *.dll, so this is enough to take one out of the
# game while leaving it trivially restorable.
SE_DISABLED_SUFFIX = ".decky-disabled"

# "plugin Foo.dll (00000001 Foo 00010030) disabled, <reason> 0 (handle 0)"
_SE_DISABLED_RE = re.compile(
    r"^plugin\s+(?P<name>\S+\.dll)\b.*?\bdisabled,\s*(?P<reason>.*?)"
    r"(?:\s+\d+\s*\(handle\s+\d+\))?\s*$",
    re.IGNORECASE,
)


def _parse_script_extender_log(path: str) -> list:
    """DLL plugins the extender refused to load, with its own wording.

    Two shapes matter and read very differently to a user: a plugin
    built for another game version ("only compatible with versions
    earlier than X"), which will never work until its author updates it,
    and one that failed to load at all, which is usually a missing
    dependency and often fixable.
    """
    out, seen = [], set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _SE_DISABLED_RE.match(line.strip())
                if not m:
                    continue
                name = m.group("name")
                if name.lower() in seen:
                    continue
                seen.add(name.lower())
                reason = m.group("reason").strip()
                out.append(
                    {
                        "name": name,
                        "reason": reason,
                        # Version-gated plugins are the author's problem;
                        # everything else may still be repairable here.
                        "outdated": "compatible with versions" in reason.lower(),
                    }
                )
    except OSError:
        return []
    return out


# A crash-log call stack frame, as written by CrashLoggerSSE / Buffout:
#   "[ 6][P] 0x6FFFF3894153 NPCWaterAIFix.dll+0024153"
# The [P]robable/[S]tack-scan marker is CrashLoggerSSE's; Buffout omits
# it, so it is optional and a missing marker is treated as probable.
_CRASH_FRAME_RE = re.compile(
    r"^\[\s*(?P<idx>\d+)\]\s*(?:\[(?P<kind>[PS])\])?\s*"
    r"0x[0-9A-Fa-f]+\s+(?P<mod>[^\s+]+)\+[0-9A-Fa-f]+",
)
_CRASH_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 /()_-]*:")


def _parse_crash_log(path: str) -> dict:
    """What was on the call stack when the game died.

    A plugin the extender loaded happily can still crash the game hours
    later, and that failure leaves no trace in the extender's own log -
    which is why this reads the crash log instead. Frames are returned in
    stack order; the caller decides which of them it can actually act on.
    """
    frames, seen, when, exc = [], set(), "", ""
    in_stack = False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not when and line.startswith("CRASH TIME:"):
                    when = line.split(":", 1)[1].strip()
                    continue
                if not exc and line.startswith("Unhandled exception"):
                    exc = line
                    continue
                if "CALL STACK" in line and line.endswith(":"):
                    in_stack = True
                    continue
                if not in_stack:
                    continue
                if _CRASH_HEADING_RE.match(line):
                    break
                m = _CRASH_FRAME_RE.match(line)
                if not m:
                    continue
                mod = m.group("mod")
                key = mod.lower()
                # Keep only the frame closest to the crash per module -
                # a DLL appearing again further up says nothing extra.
                if key in seen:
                    continue
                seen.add(key)
                frames.append(
                    {
                        "index": int(m.group("idx")),
                        "module": mod,
                        "probable": (m.group("kind") or "P") == "P",
                    }
                )
    except OSError:
        return {}
    return {"crashed_at": when, "exception": exc, "frames": frames}


# A crash REPORT: "crash-2026-08-08-12-08-13.log" (CrashLoggerSSE,
# Buffout) or "Crash_2026-08-08.txt" (.NET Script Framework). The
# separator is what matters - CrashLoggerSSE also keeps its own diary in
# "CrashLogger.log", which sits in the same folder, is written a beat
# LATER than the report it just wrote, and contains no call stack at all.
_CRASH_FILE_RE = re.compile(r"^crash[-_].*\.(log|txt)$", re.IGNORECASE)


def _newest_crash_log(dirs) -> str:
    """Most recent crash report across the places the loggers write to."""
    best, best_mtime = "", -1.0
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if not _CRASH_FILE_RE.match(n):
                continue
            p = os.path.join(d, n)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if mt > best_mtime:
                best, best_mtime = p, mt
    return best


def _smapi_log_path(config_dir_name: str) -> str:
    return os.path.join(
        decky.DECKY_USER_HOME, ".config", config_dir_name,
        "ErrorLogs", "SMAPI-latest.txt",
    )


# Failures a mod's own log marks as expected and handled. Not blame: the
# mod said out loud that it carried on. Verified against Slay the Spire 2
# 2026-08-13, where treating these as errors accused two working
# ecosystem libraries.
_OPTIONAL_FAILURE_RE = re.compile(
    r"\[Optional\]|Optional patch class failed and was skipped",
    re.IGNORECASE,
)


# How many attributed exceptions make a mod broken beyond argument.
#
# Relics Reminder threw between 1,005 and 1,077 of them per session on
# device - it throws from _Process, so once per frame, and the log just ends
# mid-flood where the game died. A working mod that logs one handled
# exception sits at 1 or 2. There is no honest reading of a four-figure
# count, which is what makes this the one case safe to act on unasked.
_AUTO_DISABLE_FLOOD = 25


def _record_mod_verdicts(
    game_domain: str, build: str, verdicts: list, state: str = "broken"
) -> int:
    """Remember that these mods could not run on this game build.

    Slay the Spire 2, 2026-08-13. Michael reset game modding, reinstalled the
    collection, and the game crashed on exactly the mod the plugin had
    already watched crash it twice - because the only record of that lived in
    a session log, and reset threw it away. Learning the same thing three
    times is not learning.

    Keyed by Nexus mod id and game BUILD. A mod is not broken in the
    abstract; it is broken against a build. When the game updates, or the
    mod's installed version changes, the verdict stops applying - which is
    the difference between a memory and a blacklist.
    """
    if not build:
        return 0
    settings = _load_settings()
    store = settings.setdefault("mod_verdicts", {}).setdefault(game_domain, {})
    added = 0
    for v in verdicts:
        mod_id = v.get("mod_id")
        if not mod_id:
            continue
        key = str(int(mod_id))
        entry = {
            "build": build,
            "version": v.get("version") or "",
            # "broken" cannot run and gets switched off. "stale" errors but
            # other mods depend on it, so the only remedy is an update - and
            # that distinction has to survive a reinstall, because a
            # collection puts its pinned version straight back. Michael
            # reset, reinstalled, and BaseLib 3.1.2 and RitsuLib 0.2.30
            # returned with all five errors, having been fixed minutes
            # earlier.
            "state": state,
            "why": (v.get("why") or "")[:200],
            "name": v.get("name") or "",
        }
        if store.get(key) != entry:
            added += 1
        store[key] = entry
    if added:
        _save_settings(settings)
    return added


def _known_broken_mods(
    game_domain: str, build: str, state: str = "broken"
) -> dict:
    """mod id -> verdict, for verdicts that still apply to this build.

    A verdict from an older build is deliberately dropped rather than
    upgraded: a game update is the single most likely thing to have fixed
    OR broken a mod, so the honest answer afterwards is "unknown".
    """
    if not build:
        return {}
    store = (_load_settings().get("mod_verdicts") or {}).get(game_domain) or {}
    return {
        int(mid): v for mid, v in store.items()
        if isinstance(v, dict) and v.get("build") == build
        and v.get("state") == state
    }


def _verdicts_for_build(game_domain: str, build: str) -> dict:
    """Every verdict that still applies to this build, whatever its state.

    _known_broken_mods answers "what should be switched off", so it filters
    to one state. This answers "what do we already know about this mod",
    which is a different question and has one job: never recommend
    installing something this device has already watched fail.

    Michael's Cyberpunk health check spent a day telling him to install
    General Shadows Fixes. Its script was the one failing to compile, and a
    single bad .reds takes every script mod with it - so the fix on offer
    was the cause of the fault.
    """
    if not build:
        return {}
    store = (_load_settings().get("mod_verdicts") or {}).get(game_domain) or {}
    out = {}
    for mid, v in store.items():
        if not isinstance(v, dict) or v.get("build") != build:
            continue
        try:
            out[int(mid)] = v
        except (TypeError, ValueError):
            continue
    return out


def _is_unambiguously_broken(info: dict) -> bool:
    """Whether this blame needs no judgement call.

    Two kinds:

    - a flood of attributed exceptions (see _AUTO_DISABLE_FLOOD)
    - "failed to load", where the mod never ran, so switching it off
      changes nothing except stopping the error box

    Deliberately NOT included: a single exception, and a "[Critical]" patch
    failure. Both mean part of a mod is unhappy, and on device two mods in
    that state did no visible harm - the game reached the menu and stayed
    there. Switching those off is a decision, so it stays on the button.
    """
    # A mod the game also reported as loaded is running. Whatever failed
    # inside it, switching the whole mod off is a bigger change than the
    # problem - and on device the one mod in this state was providing the
    # config screens for two others.
    if info.get("state") == "degraded":
        return False
    if int(info.get("errors") or 0) >= _AUTO_DISABLE_FLOOD:
        return True
    return "failed to load" in (info.get("detail") or "").lower()


def _godot_mod_manifests(mods_dir: str) -> dict:
    """Read every installed Godot mod's own manifest.

    Verified against Slay the Spire 2 on device, 2026-08-13. Each mod folder
    carries a JSON manifest - "mod_manifest.json" or "<Something>.json" -
    holding the mod's real id, its display name and, crucially, the ids of
    the mods it depends on. Two of them (BaseLib, TransformOrBanish) are
    written with a UTF-8 BOM, so utf-8-sig or nothing gets read at all.

    Returns folder -> {"id", "name", "deps"}. Folders with no readable
    manifest are simply absent; nothing here is load-bearing enough to fail
    over.
    """
    out = {}
    try:
        folders = sorted(os.listdir(mods_dir))
    except OSError:
        return out
    for folder in folders:
        path = os.path.join(mods_dir, folder)
        if not os.path.isdir(path):
            continue
        try:
            names = sorted(n for n in os.listdir(path)
                           if n.lower().endswith(".json"))
        except OSError:
            continue
        # mod_manifest.json first where both exist - it is the documented
        # name, and a mod shipping a second .json for its own data should
        # not win the coin toss.
        names.sort(key=lambda n: (n.lower() != "mod_manifest.json", n))
        for name in names:
            try:
                with open(os.path.join(path, name), "r",
                          encoding="utf-8-sig") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or not data.get("id"):
                continue
            # Both shapes appear in the wild, verified on device:
            #   "dependencies": ["BaseLib"]
            #   "dependencies": [{"id": "BaseLib", "min_version": "3.1.2"}]
            # str() on the dict form produced "{'id': 'BaseLib', ...}", which
            # matched nothing - so a mod declaring its dependency the richer
            # way silently failed to protect the library it needs.
            deps = data.get("dependencies")
            names = []
            if isinstance(deps, list):
                for dep in deps:
                    if isinstance(dep, dict):
                        if dep.get("id"):
                            names.append(str(dep["id"]))
                    elif dep:
                        names.append(str(dep))
            out[folder] = {
                "id": str(data["id"]),
                "name": str(data.get("name") or data["id"]),
                "deps": names,
            }
            break
    return out


# redscript writes its compilation errors to r6/logs, naming the .reds file
# and the symbol it could not resolve. Verbatim shape from device, with the
# path separators shown as forward slashes:
#
#   [ERROR ...] [UNRESOLVED_REF] At .../r6/scripts/GeneralShadowsFixes.reds:
#   7094:20: unresolved reference 'JobQueue'
#
# An unresolved reference to a GAME symbol means the script was built against
# a different game version. It matters more here than anywhere else because a
# single failing .reds blocks EVERY redscript mod - two orphaned files killed
# the whole script stack of every Cyberpunk collection Michael installed.
_REDS_ERROR_RE = re.compile(
    r"\[(UNRESOLVED_REF|UNRESOLVED_METHOD|SYNTAX_ERROR|TYPE_ERROR|"
    r"UNRESOLVED_FN|UNRESOLVED_TYPE)\]\s+At\s+(.+?\.reds)\s*:",
    re.IGNORECASE,
)
# Both shapes the message takes, read off the real log rather than guessed:
#
#   unresolved reference 'JobQueue'                     (UNRESOLVED_REF)
#   method 'GetStatValue' not found on 'GameObject'     (UNRESOLVED_METHOD)
#
# The second was missed by the first version of this, so every
# UNRESOLVED_METHOD came back with no symbol at all - which is the half of
# the evidence that says WHAT the script wanted from a game it no longer
# matches.
_REDS_SYMBOL_RE = re.compile(
    r"unresolved \w+ '([^']+)'|(?:method|member|field|function|type) "
    r"'([^']+)' not found",
    re.IGNORECASE,
)

# The message sits on the third line after the error - code, carets, then
# the reason - so a symbol further away than this belongs to something else.
# Without a bound, a file whose error carries no message at all keeps
# claiming lines until the next error, and inherits a symbol from a script
# hundreds of lines below it.
_REDS_SYMBOL_WINDOW = 4


def _redscript_symbol(line: str) -> str:
    m = _REDS_SYMBOL_RE.search(line)
    return (m.group(1) or m.group(2)) if m else ""


def _parse_redscript_log(lines: list) -> dict:
    """Which .reds files failed to compile, and why.

    Returns {script basename lowered: {"script", "kind", "symbol", "count"}}.
    Keyed by file rather than by mod because that is all the log knows - the
    caller matches those names against install records.
    """
    out = {}
    pending = None
    since = 0
    for line in lines:
        m = _REDS_ERROR_RE.search(line)
        if m:
            raw = m.group(2).replace(chr(92), "/")
            name = raw.rsplit("/", 1)[-1]
            entry = out.setdefault(name.lower(), {
                "script": name, "kind": m.group(1).upper(),
                "symbol": "", "count": 0,
            })
            entry["count"] += 1
            pending, since = entry, 0
            sym = _redscript_symbol(line)
            if sym and not entry["symbol"]:
                entry["symbol"] = sym
            continue
        if pending is None:
            continue
        since += 1
        if since > _REDS_SYMBOL_WINDOW:
            pending = None
            continue
        if not pending["symbol"]:
            sym = _redscript_symbol(line)
            if sym:
                pending["symbol"] = sym
                pending = None
    return out


# redscript ends a successful run with this and omits it when the compile
# dies. Verified against both logs on device: the clean one carries it and
# reports zero errors, the one from before the orphaned scripts were removed
# carries six errors and no completion line at all.
_REDS_DONE = "compilation complete"


def _redscript_log_path(install_path: str) -> str:
    return os.path.join(install_path, "r6", "logs", "redscript_rCURRENT.log")


def _redscript_report(install_path: str, records: dict) -> dict:
    """What the game itself said about its script mods, last time it ran.

    This is the discriminator the health check had been missing. Everything
    else it knows comes from mod pages - what an author says their mod
    needs - and a curator who deliberately omits a requirement looks
    identical to a user who forgot one. The game's own compiler does not
    have opinions about either: it names the file that failed.

    Only redscript_rCURRENT.log is read. redscript rotates the previous
    session out under a timestamped name, and those are full of problems
    that have since been fixed - reporting one would be the same bug as a
    stale "already fixed" line contradicting the findings above it.

    Returns {"ran", "compiled", "failures", "orphans"}. A failure carries
    the record that owns the file where one does; an orphan is a failing
    script no install record claims, which is how two dead files sat in
    r6/scripts for weeks with nothing accountable for them.
    """
    empty = {"ran": False, "compiled": False, "failures": [], "orphans": [],
             "stamp": "", "stale": False}
    path = _redscript_log_path(install_path)
    if not os.path.isfile(path):
        return empty
    try:
        st = os.stat(path)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return empty
    # Which log this is. A session that blamed a mod already happened, and
    # reading it again does not make it happen twice - so whatever we do
    # about it, we do once. See the auto-disable.
    stamp = f"{int(st.st_mtime)}:{st.st_size}"
    compiled = any(_REDS_DONE in ln.lower() for ln in lines)
    blamed = _parse_redscript_log(lines)
    # Evidence has a shelf life. A log describes the mods that were
    # installed when the game last RAN, and the moment anything is
    # installed or removed it stops describing what is there now.
    #
    # Michael hit this exactly: a collection failed to compile blaming
    # ScorpionTank, he uninstalled it, installed one he knew worked, and
    # the health check still reported the failure - "I booted the game to
    # check and it booted fine so the health report was stale". Worse than
    # the wrong display, it had already written a verdict against a mod
    # that was no longer installed.
    newest = 0
    for rec in (records or {}).values():
        try:
            newest = max(newest, int(rec.get("installed_at") or 0))
        except (TypeError, ValueError):
            continue
    stale = bool(newest and newest > st.st_mtime)
    # Which record owns each .reds on disk. Basename only: the log prints a
    # path under r6/scripts while a record stores the path it installed to,
    # and a mod may ship its scripts in a subfolder of its own.
    owner = {}
    for key, rec in (records or {}).items():
        for rel in rec.get("files") or []:
            name = str(rel).replace(chr(92), "/").rsplit("/", 1)[-1]
            if name.lower().endswith(".reds"):
                owner.setdefault(name.lower(), (key, rec))
    # Where a .reds could still be sitting. An uninstall does not rewrite
    # the log, so a script the log blames may simply be gone - and a
    # failure whose file no longer exists cannot still be breaking
    # anything. This is the other half of staleness, and the half that
    # catches an uninstall (which leaves no record, so no timestamp moves).
    on_disk = set()
    for sub in ("r6/scripts", "red4ext/plugins"):
        d = os.path.join(install_path, *sub.split("/"))
        for root_, _dirs, names in os.walk(d):
            for n in names:
                if n.lower().endswith(".reds"):
                    on_disk.add(n.lower())
    failures, orphans = [], []
    for low, info in sorted(blamed.items()):
        if low not in on_disk:
            # Named by the log, no longer on disk: already dealt with.
            continue
        hit = owner.get(low)
        entry = {
            "script": info["script"],
            "kind": info["kind"],
            "symbol": info["symbol"],
            "count": info["count"],
            "mod": (hit[1].get("name") or hit[0]) if hit else "",
            "record_key": hit[0] if hit else "",
            "mod_id": (hit[1].get("mod_id") or 0) if hit else 0,
            "version": (hit[1].get("version") or "") if hit else "",
        }
        (failures if hit else orphans).append(entry)
    return {
        "ran": True,
        "compiled": compiled,
        "failures": failures,
        "orphans": orphans,
        "stamp": stamp,
        "stale": stale,
    }


def _missing_manifest_deps(manifests: dict) -> list:
    """Which installed mods declare a dependency that is not installed.

    Read from the manifests alone, so this is knowable the moment a mod is
    installed. The first version of this check read the session log instead
    and therefore needed the game to have launched and failed first -
    Michael installed LustTravel2, opened Fixes, and found nothing, because
    nothing had gone wrong YET.

    Returns [{"folder", "name", "missing": [ids]}].
    """
    have = {
        (info.get("id") or "").strip().lower()
        for info in manifests.values()
        if info.get("id")
    }
    out = []
    for folder, info in sorted(manifests.items()):
        missing = [
            dep for dep in info.get("deps") or []
            if dep.strip().lower() not in have
        ]
        if missing:
            out.append({
                "folder": folder,
                "name": info.get("name") or folder,
                "missing": missing,
            })
    return out


def _tag_names_mod(tag: str, ident: str) -> bool:
    """Whether a log tag refers to the mod with this id or name.

    Mods log under a logger name they chose, not their id: RitsuLib's id is
    "STS2-RitsuLib" but it logs as "com.ritsukage.sts2-RitsuLib", and
    ModConfig logs as "sts2.piyixiajiuhenfen.modconfig". So a tag matches if
    it IS the id or ENDS with it at a separator - which caught all six
    blamed tags on device while a short id like "Lib" still cannot claim
    "BaseLib", because the character before it is a letter.
    """
    tag_l = (tag or "").strip().lower()
    ident_l = (ident or "").strip().lower()
    if not tag_l or not ident_l:
        return False
    if tag_l == ident_l:
        return True
    if len(ident_l) < 3 or not tag_l.endswith(ident_l):
        return False
    return tag_l[-len(ident_l) - 1] in ".-_ :/"


def _mods_needed_by_others(manifests: dict, keeping: set) -> dict:
    """id -> the folders still relying on it, among mods NOT being switched
    off.

    BaseLib really did throw a HarmonyException in the session that killed
    the game, and five installed mods declared it as a dependency. The
    manifests say so out loud, so there is no need to guess or to keep a
    hand-written list of which mods are libraries.
    """
    needed: dict = {}
    for folder, info in manifests.items():
        if folder not in keeping:
            continue
        for dep in info.get("deps") or []:
            needed.setdefault(dep.strip().lower(), []).append(folder)
    return needed



def _parse_mod_load_log(lines: list):
    """Turn a game session log into per-mod load outcomes. Returns
    (status dict keyed by normalized mod id, modded_session bool)."""

    def norm(mod_id: str) -> str:
        # log tags sometimes differ from manifest ids in dash/underscore
        return re.sub(r"[^a-z0-9]", "", mod_id.lower())

    loaded: set = set()
    errors: dict = {}
    blamed_counts: dict = {}
    # The tag a mod logs under is a logger name it picked, not its id:
    # RitsuLib logs as "com.ritsukage.sts2-RitsuLib". Keeping the raw tag
    # is what lets it be matched back to an installed folder.
    raw_tags: dict = {}
    pending_exc = ""
    modded_session = False
    for line in lines:
        if "RUNNING MODDED" in line:
            modded_session = True
            continue
        m = re.search(r"Finished mod initialization for '.*' \(([^)]+)\)", line)
        if m:
            loaded.add(norm(m.group(1)))
            continue
        # Slay the Spire 2's loader: "[WARN] [ts] RouteSuggest: Mod loaded"
        m = re.search(r"\] ([A-Za-z0-9_.]+): Mod loaded", line)
        if m:
            loaded.add(norm(m.group(1)))
            continue
        # Same opening words, two completely different problems. The
        # version one was being reported as "duplicate mod id", which sent
        # Michael looking for a clash that did not exist.
        m = re.search(
            r"Tried to load mod with id (\S+?), but its declared min game "
            r"version (\S+) is higher than the current game version (\S+)",
            line,
        )
        if m:
            errors[norm(m.group(1))] = (
                f"needs game version {m.group(2)}, and this game is "
                f"{m.group(3).lstrip('v')} - it is built for a NEWER build "
                f"than the one installed"
            )
            raw_tags.setdefault(norm(m.group(1)), m.group(1))
            continue
        m = re.search(r"Tried to load mod with id (\S+?),", line)
        if m:
            errors.setdefault(norm(m.group(1)), "duplicate mod id")
            raw_tags.setdefault(norm(m.group(1)), m.group(1))
            continue
        # The clearest error the loader produces, and the one the parser
        # missed: Enchanted Offerings did not load at all because BaseLib
        # was not installed, the banner said "Loaded 3 mods (4 total)", and
        # the plugin reported no problems.
        m = re.search(
            r"Tried to load mod (\S+?), but it depends on mods which have "
            r"not been loaded: (.+?)!?$",
            line,
        )
        if m:
            needed = m.group(2).strip().rstrip("!")
            errors[norm(m.group(1))] = (
                f"needs {needed}, which is not installed"
            )
            raw_tags.setdefault(norm(m.group(1)), m.group(1))
            continue
        m = re.match(r"\[ERROR\] \[([^\]]+)\] (.*)", line)
        if m:
            # The mod's own patcher saying it planned for this. RitsuLib
            # logs two "[Optional] ... Failed" lines out of 163 patches and
            # then "161 applied, 2 failed" - it is working. Blaming it read
            # as broken, and it is a library 21 other mods sit on, so
            # switching it off would have taken all of them down.
            if _OPTIONAL_FAILURE_RE.search(m.group(2)):
                continue
            errors.setdefault(norm(m.group(1)), m.group(2)[:160])
            raw_tags.setdefault(norm(m.group(1)), m.group(1))
            continue
        # A .NET mod loader names the mod it could not load.
        m = re.search(r"while loading mod ([A-Za-z0-9_.-]+)", line)
        if m:
            errors[norm(m.group(1))] = "failed to load"
            raw_tags.setdefault(norm(m.group(1)), m.group(1))
            continue
        # An exception block: the FIRST stack frame after it is where the
        # throw happened, so that mod is the culprit. Later frames are the
        # libraries it called through - blaming those would switch off a
        # shared dependency and take working mods with it.
        m = re.match(r"ERROR: System\.([A-Za-z]+Exception)", line)
        if m:
            pending_exc = m.group(1)
            continue
        if pending_exc:
            frame = re.match(r"\s+at ([A-Za-z0-9_]+)\.", line)
            if frame:
                blamed = frame.group(1)
                if blamed not in ("Godot", "System", "HarmonyLib", "MegaCrit"):
                    detail = (
                        "calls something this version of the game does not "
                        "have - built for a different game build"
                        if pending_exc in (
                            "MissingMethodException", "MissingFieldException",
                            "TypeLoadException",
                        )
                        else f"threw {pending_exc}"
                    )
                    errors.setdefault(norm(blamed), detail)
                    raw_tags.setdefault(norm(blamed), blamed)
                    blamed_counts[norm(blamed)] = (
                        blamed_counts.get(norm(blamed), 0) + 1
                    )
                pending_exc = ""

    status = {key: {"state": "loaded", "detail": ""} for key in loaded}
    for key, detail in errors.items():
        status[key] = {
            # A mod can be BOTH. ModConfig 0.2.3 announced "initialized!",
            # registered 16 config entries across two other mods and reported
            # state=Loaded - and also failed to inject one duplicate tab. It
            # is running and degraded, not broken, and calling that "error"
            # made a working mod look like the reason the game was unhappy.
            "state": "degraded" if key in loaded else "error",
            "detail": detail,
            "errors": blamed_counts.get(key, 0),
            "tag": raw_tags.get(key, key),
        }
    return status, modded_session


async def _is_process_running(name: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "pgrep",
            "-x",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=_host_env(),
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


async def _emit_progress(
    mod_id: int,
    phase: str,
    percent: int,
    message: str = "",
    bytes_done=None,
    bytes_total=None,
    bps=None,
):
    payload = {
        "mod_id": mod_id,
        "phase": phase,
        "percent": percent,
        "message": message,
    }
    if bytes_done is not None:
        payload["bytes_done"] = int(bytes_done)
    if bytes_total is not None:
        payload["bytes_total"] = int(bytes_total)
    if bps is not None:
        payload["bps"] = int(bps)
    await decky.emit("install_progress", payload)


# ---- User preferences (Settings tab) ---------------------------------------
# Clamped server-side so a hand-edited settings.json can't produce a
# 50-way download stampede or a zero-byte disk floor.

USER_PREF_BOUNDS = {
    # name: (default, min, max)
    "parallel_downloads": (4, 1, 8),
    "prefetch_window": (8, 2, 16),
    # How many mods are extracted AHEAD of the installer. Extraction is
    # the CPU-bound half of an install and touches nothing shared, so it
    # parallelises safely - but each prepared mod is an extracted tree
    # sitting on disk, so the window stays small. 0 disables it entirely
    # and restores the old strictly-serial behaviour.
    "extract_ahead": (2, 0, 4),
    "speed_cap_mbps": (0, 0, 200),  # 0 = unlimited
    "min_free_gb": (5, 1, 50),
}


def _valid_mod_language(value) -> str:
    if value in ("english", "all"):
        return value
    if isinstance(value, str) and value in MOD_LANGUAGES:
        return value
    return "english"


def _user_prefs() -> dict:
    stored = _load_settings().get("user_prefs") or {}
    prefs = {}
    for name, (default, lo, hi) in USER_PREF_BOUNDS.items():
        try:
            value = int(stored.get(name, default))
        except (TypeError, ValueError):
            value = default
        prefs[name] = max(lo, min(hi, value))
    prefs["mod_language"] = _valid_mod_language(stored.get("mod_language"))
    return prefs


# Global token bucket shared by every concurrent download - N parallel
# streams split the cap instead of each taking it.
_throttle_state = {"last": 0.0, "allowance": 0.0}
_throttle_lock = None  # created lazily on the running loop


async def _throttle(nbytes: int, cap_bytes: float) -> None:
    if cap_bytes <= 0:
        return
    global _throttle_lock
    if _throttle_lock is None:
        _throttle_lock = asyncio.Lock()
    async with _throttle_lock:
        now = time.monotonic()
        st = _throttle_state
        if st["last"]:
            st["allowance"] = min(
                cap_bytes, st["allowance"] + (now - st["last"]) * cap_bytes
            )
        else:
            st["allowance"] = cap_bytes
        st["last"] = now
        if nbytes > st["allowance"]:
            wait = (nbytes - st["allowance"]) / cap_bytes
            st["allowance"] = 0.0
        else:
            st["allowance"] -= nbytes
            wait = 0.0
    if wait > 0:
        await asyncio.sleep(min(wait, 5.0))


def _free_disk_gb(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / (1 << 30)
    except OSError:
        return float("inf")


# A prepared extraction is the scratch dir plus this marker file, written
# only after the extract AND the permission pass have finished. Without
# it, an extraction interrupted half way (plugin reload, power loss)
# would look exactly like a finished one and install half a mod.
PREPARED_MARKER = ".decky-prepared"


def _extract_scratch(mod_id: int, file_id: int) -> str:
    """Where a mod file is extracted before it is committed to the game.
    Shared by the installer and the extract-ahead worker - the names MUST
    match or the work is done twice."""
    return os.path.join(DOWNLOADS_DIR, f"extract-{mod_id}-{file_id}")


def _archive_cache_path(mod_id: int, file_id: int, file_name: str) -> str:
    """Local archive path for a mod file. Built from ids so non-ASCII
    upstream filenames can't produce a broken local path; bsdtar detects
    the format from content anyway. Shared by the installer and the
    prefetcher - the names MUST match for the cache to hit."""
    ext = os.path.splitext(file_name or "")[1]
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext):
        ext = ""
    return os.path.join(DOWNLOADS_DIR, f"{mod_id}-{file_id}{ext}")


# One session for every mod-file transfer, instead of one per request.
# A collection pays two requests per mod (resolve the link, then the CDN),
# and a fresh ClientSession meant a fresh TCP + TLS handshake for each -
# 400 handshakes across a 200-mod collection, all of it latency before a
# single byte of mod. Keep-alive reuses the connections instead.
#
# No default timeout on the session: link lookups want seconds, a 2 GB
# archive wants half an hour, so each request passes its own.
_HTTP_SESSION = None


async def _http_session():
    global _HTTP_SESSION
    if _HTTP_SESSION is None or _HTTP_SESSION.closed:
        _HTTP_SESSION = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(
                limit=16,
                # Prefetch runs several transfers at once against one CDN
                # host; the default cap would serialise them.
                limit_per_host=8,
                ttl_dns_cache=300,
                keepalive_timeout=60,
                ssl=SSL_CONTEXT,
            )
        )
    return _HTTP_SESSION


async def _close_http_session() -> None:
    global _HTTP_SESSION
    if _HTTP_SESSION is not None and not _HTTP_SESSION.closed:
        await _HTTP_SESSION.close()
    _HTTP_SESSION = None


# ---- download control (pause / cancel / resume) -----------------------------
# One GLOBAL pause gate rather than per-download switches. Pausing during
# a collection run has to stall the whole prefetch pipeline, and a single
# flag does that naturally: every in-flight download parks at its next
# chunk, the pump's slots stay occupied, and resume releases everything at
# once. Cancel is per-download, and only consumed by a download actually
# in flight - a mark left on a mod that is not downloading would silently
# kill the user's next retry of it.
_DL_PAUSED = False
_DL_ACTIVE: set = set()   # mod_ids currently inside _download_archive
_DL_CANCEL: set = set()   # mod_ids to abort at their next chunk


class _DownloadCancelled(Exception):
    pass


def _resume_plan(part_size: int, status: int, content_range: str,
                 content_length: int) -> tuple:
    """How to continue a download given what the server said to our Range
    request. Returns (mode, offset, total): mode is 'append' or 'restart',
    offset is where our progress counter starts, total is the full file
    size (0 when unknown).

    Pause works by closing the connection and keeping the .part - holding
    a socket open across a pause measured in hours only invites the
    server to drop it. So resuming correctly from a Range response is the
    heart of the feature, and the part that must not be guessed at:
    appending to the wrong offset corrupts an archive in a way nothing
    notices until extraction fails.
    """
    if status == 206 and part_size > 0:
        # "bytes 100-999/1000" - the server tells us both where it is
        # resuming from and the full size.
        m = re.match(r"bytes\s+(\d+)-\d*/(\d+|\*)", content_range or "")
        if m:
            start = int(m.group(1))
            total = 0 if m.group(2) == "*" else int(m.group(2))
            if start != part_size:
                # Resuming from anywhere but the end of what we have
                # would interleave two different byte ranges.
                return "restart", 0, total
            return "append", part_size, total
        # 206 with no parseable Content-Range: trust the status, derive
        # the total from what remains.
        total = part_size + content_length if content_length else 0
        return "append", part_size, total
    # 200 means the server ignored the Range (or we never sent one):
    # it is sending the whole file from byte zero.
    return "restart", 0, content_length or 0


async def _wait_while_paused(mod_id: int, pct: int) -> None:
    """Park an in-flight download until resume (or its own cancel)."""
    if not _DL_PAUSED:
        return
    await _emit_progress(mod_id, "paused", pct)
    while _DL_PAUSED and mod_id not in _DL_CANCEL:
        await asyncio.sleep(0.4)


# How long a tool's output files must sit unchanged before the tool is
# treated as finished with them.
#
# Set after killing the Fallout Anniversary Patcher the instant Fallout3.exe
# changed size, which produced a 15MB executable that was the right length
# and not a working program. Twelve seconds is far longer than the gap
# between writes inside a single patch pass, and still well under the
# three-minute timeout it replaces.
_TOOL_QUIET_SECONDS = 12


async def _register_download(game_domain: str, mod_id: int, api_key) -> bool:
    """Ask Nexus for a download link so the download is on record.

    Requesting the link is what registers a download against an account -
    it is how every client does it - and the plugin skips it whenever the
    archive is already cached. That is invisible until somebody tries to
    endorse and is told they have not downloaded the mod.

    Uses the mod's newest file, because the endorsement check is per MOD,
    not per file. Returns whether a link came back; never raises, since
    this only ever runs as a repair on a path that has already failed.
    """
    try:
        headers = _api_headers(api_key)
        base = f"{NEXUS_API_BASE}/v1/games/{game_domain}/mods/{int(mod_id)}"
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20)
        ) as session:
            async with session.get(
                f"{base}/files.json", headers=headers, ssl=SSL_CONTEXT
            ) as resp:
                if resp.status != 200:
                    return False
                files = (await resp.json()).get("files") or []
            newest = max(
                (f for f in files if f.get("category_name") == "MAIN") or files,
                key=lambda f: int(f.get("file_id") or 0),
                default=None,
            )
            if not newest:
                return False
            async with session.get(
                f"{base}/files/{int(newest['file_id'])}/download_link.json",
                headers=headers, ssl=SSL_CONTEXT,
            ) as resp:
                ok = resp.status == 200
        decky.logger.info(
            f"registered a download for {game_domain}/{mod_id} so it can be "
            f"endorsed (cached install never asked for a link): ok={ok}"
        )
        return ok
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError):
        return False


async def _download_direct_file(url: str, md5: str, size: int):
    """Fetch a collection's "direct" download and prove it is what the
    curator published. Returns (error, path).

    This is the only place the plugin fetches from a host that is not
    Nexus, over a URL written by a third party, and FOSE's own site is
    plain HTTP with no certificate to check. The manifest's md5 is what
    makes that defensible, so a mismatch fails outright rather than
    warning - a file that is not the one the curator hashed has no
    business being unpacked into somebody's game folder.
    """
    if not re.match(r"https?://", url or "", re.I):
        return "Not an http(s) URL", ""
    stem = hashlib.md5((url or "").encode()).hexdigest()
    dest = os.path.join(DOWNLOADS_DIR, f"direct-{stem}.bin")
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    digest = hashlib.md5()
    got = 0
    try:
        timeout = aiohttp.ClientTimeout(total=600, sock_connect=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url, headers=APP_HEADERS, ssl=SSL_CONTEXT,
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    return f"download failed (HTTP {resp.status})", ""
                with open(dest, "wb") as out:
                    async for chunk in resp.content.iter_chunked(1 << 18):
                        out.write(chunk)
                        digest.update(chunk)
                        got += len(chunk)
                        # A curator-declared size is also a ceiling. Without
                        # it a redirect to something enormous would fill the
                        # deck before anything checked the hash.
                        if size and got > max(size * 4, size + (1 << 20)):
                            raise ValueError("file is far larger than declared")
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError,
            ValueError) as e:
        try:
            os.remove(dest)
        except OSError:
            pass
        return f"{type(e).__name__}: {e}", ""
    if size and got != size:
        os.remove(dest)
        return f"wrong size ({got} bytes, expected {size})", ""
    if md5 and digest.hexdigest().lower() != md5.lower():
        os.remove(dest)
        return "checksum did not match what the collection published", ""
    return "", dest


async def _download_archive(
    game_domain: str,
    mod_id: int,
    file_id: int,
    file_name: str,
    api_key: str,
    dl_key: str = "",
    dl_expires: int = 0,
) -> tuple:
    """Fetch a mod file to the local cache: resolve the (Premium)
    download link, stream to <path>.part, rename when complete. A
    completed cached archive short-circuits - that's what lets the
    collection pipeline download ahead while installs run. Returns
    (error, archive_path); error is '' on success."""
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    archive_path = _archive_cache_path(mod_id, file_id, file_name)
    try:
        if os.path.getsize(archive_path) > 0:
            # Prefetched (or a retry after install-stage failure). .part
            # files never rename on failure, so a completed file is whole.
            await _emit_progress(mod_id, "downloading", 100)
            return "", archive_path
    except OSError:
        pass

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

    async def _resolve_uri() -> tuple:
        """(err, uri). A function rather than straight-line code because
        CDN links expire: a download resumed after a long pause needs a
        fresh link, not the one minted an hour ago."""
        # Parallel prefetching bursts this endpoint - back off and retry
        # on rate limits / transient 5xx instead of failing the mod's row.
        links = None
        last_err = "Download link error"
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(2 * attempt)
            try:
                session = await _http_session()
                async with session.get(
                    link_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 403:
                        body = await resp.text()
                        return (
                            _download_forbidden_reason(
                                body, _load_settings().get("is_premium")
                            ),
                            "",
                        )
                    if resp.status in (429, 500, 502, 503):
                        last_err = f"Download link error (HTTP {resp.status})"
                        decky.logger.warning(
                            f"link fetch {game_domain}/{mod_id}: "
                            f"HTTP {resp.status}, attempt {attempt + 1}/3"
                        )
                        continue
                    if resp.status != 200:
                        return f"Download link error (HTTP {resp.status})", ""
                    links = await resp.json()
                    break
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = f"Network error: {type(e).__name__}"
                continue
        if links is None:
            return last_err, ""
        if not links or not isinstance(links, list):
            return "Nexus Mods returned no download locations", ""
        found = links[0].get("URI") or links[0].get("uri")
        if not found:
            return "Nexus Mods returned a malformed download link", ""
        return "", found

    err, uri = await _resolve_uri()
    if err:
        return err, ""

    prefs = _user_prefs()
    cap_bytes = prefs["speed_cap_mbps"] * (1 << 20)
    min_free = prefs["min_free_gb"]
    if _free_disk_gb(DOWNLOADS_DIR) < min_free:
        return (
            f"Low disk space (under {min_free} GB free) - free some space "
            "or lower the minimum in Settings",
            "",
        )

    part_path = archive_path + ".part"
    await _emit_progress(mod_id, "downloading", 0)
    # The whole transfer runs inside one control loop. Pause exits the
    # connection cleanly (a socket held open across an hours-long pause
    # just invites the server to drop it), parks at the gate, and resumes
    # with a Range request from wherever the .part ends. Transport errors
    # take the same path - which is why a failed download no longer
    # deletes its .part: a dropped connection at 90% of 10GB used to cost
    # all ten.
    _DL_ACTIVE.add(mod_id)
    known_total = 0
    transport_failures = 0
    try:
        while True:
            if mod_id in _DL_CANCEL:
                raise _DownloadCancelled()
            try:
                part_now = os.path.getsize(part_path)
            except OSError:
                part_now = 0
            await _wait_while_paused(
                mod_id,
                int(part_now * 100 / known_total) if known_total else 0,
            )
            if mod_id in _DL_CANCEL:
                raise _DownloadCancelled()
            req_headers = (
                {"Range": f"bytes={part_now}-"} if part_now else {}
            )
            disk_low = False
            paused = False
            try:
                timeout = aiohttp.ClientTimeout(total=1800, sock_connect=30)
                session = await _http_session()
                async with session.get(
                    _safe_uri(uri), headers=req_headers, timeout=timeout
                ) as resp:
                    if resp.status == 403:
                        # The CDN link outlived its welcome (long pause,
                        # slow retry) - mint a fresh one and go again.
                        err, uri = await _resolve_uri()
                        if err:
                            return err, ""
                        continue
                    if resp.status in (416,):
                        # Range not satisfiable: our .part disagrees with
                        # the file on the server. Start over.
                        try:
                            os.remove(part_path)
                        except OSError:
                            pass
                        continue
                    if resp.status in (429, 500, 502, 503):
                        transport_failures += 1
                        if transport_failures >= 3:
                            return (
                                f"CDN download failed (HTTP {resp.status})",
                                "",
                            )
                        await asyncio.sleep(2 * transport_failures)
                        continue
                    if resp.status not in (200, 206):
                        return f"CDN download failed (HTTP {resp.status})", ""
                    mode, done, total = _resume_plan(
                        part_now,
                        resp.status,
                        resp.headers.get("Content-Range") or "",
                        int(resp.headers.get("Content-Length") or 0),
                    )
                    known_total = total or known_total
                    last_pct = -1
                    # Speed: EMA over inter-emit deltas, emitted at least
                    # every half-second so big files still tick.
                    last_t = time.monotonic()
                    last_done = done
                    ema_bps = 0.0
                    chunk_count = 0
                    with open(
                        part_path, "ab" if mode == "append" else "wb"
                    ) as out:
                        async for chunk in resp.content.iter_chunked(1 << 20):
                            if mod_id in _DL_CANCEL:
                                raise _DownloadCancelled()
                            if _DL_PAUSED:
                                paused = True
                                break
                            out.write(chunk)
                            done += len(chunk)
                            await _throttle(len(chunk), cap_bytes)
                            chunk_count += 1
                            if chunk_count % 256 == 0 and (
                                _free_disk_gb(DOWNLOADS_DIR) < min_free
                            ):
                                disk_low = True
                                break
                            now = time.monotonic()
                            pct = int(done * 100 / total) if total else 0
                            if pct > last_pct or now - last_t >= 0.5:
                                dt = max(now - last_t, 1e-3)
                                inst = (done - last_done) / dt
                                ema_bps = (
                                    inst
                                    if ema_bps == 0
                                    else 0.6 * ema_bps + 0.4 * inst
                                )
                                last_pct = pct
                                last_t = now
                                last_done = done
                                await _emit_progress(
                                    mod_id,
                                    "downloading",
                                    pct,
                                    bytes_done=done,
                                    bytes_total=total or None,
                                    bps=ema_bps,
                                )
                if paused:
                    continue
                if disk_low:
                    # Deleted rather than kept for resume: low disk is the
                    # one failure where holding a multi-GB .part makes the
                    # problem worse, and this path is self-healing.
                    try:
                        os.remove(part_path)
                    except OSError:
                        pass
                    return (
                        f"Low disk space (under {min_free} GB free) - "
                        "download stopped safely; free space or lower the "
                        "minimum in Settings",
                        "",
                    )
                if total and done < total:
                    # The server closed the stream early without an error
                    # status. The .part is intact - resume, don't restart.
                    transport_failures += 1
                    if transport_failures >= 3:
                        return "CDN connection kept dropping - try again", ""
                    await asyncio.sleep(2)
                    continue
                os.replace(part_path, archive_path)
                return "", archive_path
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                transport_failures += 1
                if transport_failures >= 3:
                    # The .part stays: the next attempt (tonight or next
                    # week) resumes from it instead of starting over.
                    return f"Download failed: {type(e).__name__}", ""
                await asyncio.sleep(2)
                continue
    except _DownloadCancelled:
        try:
            os.remove(part_path)
        except OSError:
            pass
        await _emit_progress(mod_id, "cancelled", 0)
        return "Cancelled", ""
    finally:
        _DL_ACTIVE.discard(mod_id)
        _DL_CANCEL.discard(mod_id)


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


def _strip_archive_junk(root: str) -> None:
    """Delete metadata some archives carry beside the payload. A mod
    zipped on a Mac brings a __MACOSX tree, which is not mod content and
    must not be merged into the game folder - and it also masks the real
    payload, since an archive of 'GuardsTalk_KID.ini + __MACOSX' does not
    look like a single-folder mod until the junk is gone."""
    for current, dirs, names in os.walk(root, topdown=True):
        for d in list(dirs):
            if d.lower() in ARCHIVE_JUNK_DIRS:
                dirs.remove(d)
                _force_rmtree(os.path.join(current, d))
        for n in names:
            if n.lower() in ARCHIVE_JUNK_FILES:
                try:
                    os.remove(os.path.join(current, n))
                except OSError:
                    pass


def _host_env(extra: dict = None) -> dict:
    """The environment external tools must run in.

    Decky Loader ships as a PyInstaller bundle, so every plugin inherits
    LD_LIBRARY_PATH pointing at its unpacked /tmp/_MEIxxxxxx directory.
    That directory carries its own libreadline, older than the system
    one, and /bin/sh links against readline - so ANY subprocess that
    happens to be a shell script dies before its real program is even
    reached:

        /bin/sh: symbol lookup error: /bin/sh: undefined symbol:
        rl_trim_arg_from_keyseq

    On SteamOS /usr/bin/7z is exactly that kind of wrapper, two lines
    that exec /usr/lib/7zip/7z. So 7z has never once run from inside
    this plugin: every "7z: failed" in the log was this, not the
    archive, and the fallback chain has quietly been two extractors
    deep instead of three since the day it was written.

    PyInstaller stashes the real value in LD_LIBRARY_PATH_ORIG, so put
    that back - or drop the variable entirely when there was nothing
    there to begin with, which is the case on a normal Deck.
    """
    env = dict(os.environ)
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        original = env.pop(f"{var}_ORIG", None)
        if original:
            env[var] = original
        else:
            env.pop(var, None)
    if extra:
        env.update(extra)
    return env


# Extractors in preference order. bsdtar reads nearly everything and is
# always present on SteamOS, but libarchive refuses two RAR variants that
# Nexus is full of - RAR3 with a VM program filter, and RAR5 with a large
# dictionary ("Declared dictionary size is not supported"). Three mods in
# one Gate To Sovngarde run died on exactly those, so when bsdtar fails
# we retry with 7z and then unrar, both of which handle them.
_EXTRACTORS = (
    ("bsdtar", lambda a, d: ["bsdtar", "-xf", a, "-C", d]),
    ("7z", lambda a, d: ["7z", "x", "-y", f"-o{d}", a]),
    ("unrar", lambda a, d: ["unrar", "x", "-y", "-o+", a, d + os.sep]),
)


async def _extract_archive(archive_path: str, dest_dir: str) -> str:
    """Extract an archive into dest_dir, trying each available extractor
    until one succeeds. Returns '' on success, error text otherwise."""
    errors = []
    for name, build in _EXTRACTORS:
        if not shutil.which(name):
            continue
        if errors:
            # A failed attempt can leave a half-written tree behind; the
            # next extractor must not merge into someone else's debris.
            _force_rmtree(dest_dir)
            os.makedirs(dest_dir, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                *build(archive_path, dest_dir),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=_host_env(),
            )
            _, err = await proc.communicate()
        except OSError as e:
            errors.append(f"{name}: {e}")
            continue
        if proc.returncode == 0:
            if errors:
                decky.logger.info(
                    f"{name} extracted {os.path.basename(archive_path)} after "
                    f"{len(errors)} extractor(s) failed"
                )
            _strip_archive_junk(dest_dir)
            return ""
        errors.append(
            f"{name}: " + (err.decode(errors="replace").strip()[:200] or "failed")
        )

    # Tried whatever the extension claims: plenty of mod archives lie
    # about it, and a legacy .fomod package is a zip under another name.
    # ZipFile raises on anything that is not one, which is the only test
    # that actually matters here.
    import zipfile

    try:
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(dest_dir)
        _strip_archive_junk(dest_dir)
        return ""
    except Exception as e:  # noqa: BLE001 - report to UI
        errors.append(f"zipfile: {e}")
    if not errors:
        return "no extractor available for this archive type"
    return " | ".join(errors)[:300]


async def _unwrap_fomod_package(scratch: str) -> str:
    """Extract a legacy `.fomod` package sitting inside an archive.

    Before FOMOD became a folder convention, FOMM shipped installers as a
    single `.fomod` file - an ordinary 7z/zip under a different extension,
    holding the mod plus its `fomod/ModuleConfig.xml`. Plenty of the older
    New Vegas, FO3 and Oblivion catalogue is still packaged that way, and
    Interior Lighting Overhaul (newvegas/35794) is one: the download is a
    folder containing a changelog and a 15 MB `.fomod`, so layout
    detection found no Data payload and called the mod unsupported. The
    wizard inside it is one we can already drive.

    Only unwrapped when there is exactly one candidate and the tree has no
    FOMOD config of its own - a normal archive that merely ships a .fomod
    alongside real content is left alone rather than second-guessed.

    Returns the package filename it unwrapped, or "".
    """
    if _fomod_config_path(scratch):
        return ""
    packages = [
        os.path.join(root, n)
        for root, _dirs, names in os.walk(scratch)
        for n in names
        if n.lower().endswith(".fomod")
    ]
    if len(packages) != 1:
        return ""
    package = packages[0]
    # Extracted aside rather than in place: _extract_archive wipes the
    # destination between failed extractors, which would take the package
    # itself with it and leave nothing for the next one to try.
    staging = os.path.join(os.path.dirname(scratch.rstrip(os.sep)),
                           os.path.basename(scratch.rstrip(os.sep)) + "-fomod")
    _force_rmtree(staging)
    os.makedirs(staging, exist_ok=True)
    err = await _extract_archive(package, staging)
    if err:
        _force_rmtree(staging)
        decky.logger.info(
            f"fomod package {os.path.basename(package)} would not extract: {err}"
        )
        return ""
    dest = os.path.dirname(package)
    for name in os.listdir(staging):
        target = os.path.join(dest, name)
        if os.path.isdir(target):
            _force_rmtree(target)
        elif os.path.isfile(target):
            os.remove(target)
        shutil.move(os.path.join(staging, name), target)
    _force_rmtree(staging)
    try:
        os.remove(package)
    except OSError:
        pass
    decky.logger.info(
        f"unwrapped legacy fomod package {os.path.basename(package)}"
    )
    return os.path.basename(package)


async def _fetch_collection_manifest(slug: str, game_domain: str, api_key):
    """Download and extract a collection's own archive.

    Returns (scratch_dir, manifest_dict). The CALLER removes the scratch
    dir - the archive holds more than the manifest (bundled mods, INI
    patches), and every use of it so far threw those away unread.
    """
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
        timeout=aiohttp.ClientTimeout(total=180)
    ) as session:
        async with session.get(
            f"{NEXUS_API_BASE}{link_path}",
            headers=_api_headers(api_key),
            ssl=SSL_CONTEXT,
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Manifest link HTTP {resp.status}")
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
            raise RuntimeError("No manifest download link")
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        arc = os.path.join(DOWNLOADS_DIR, f"collection-{slug}.arc")
        async with session.get(_safe_uri(uri), ssl=SSL_CONTEXT) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Manifest download HTTP {resp.status}")
            with open(arc, "wb") as out:
                while True:
                    chunk = await resp.content.read(262144)
                    if not chunk:
                        break
                    out.write(chunk)
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
        raise RuntimeError(err)
    manifest_path = None
    for root, _dirs, names in os.walk(scratch):
        for n in names:
            if n.lower() == "collection.json":
                manifest_path = os.path.join(root, n)
                break
    if not manifest_path:
        _force_rmtree(scratch)
        raise RuntimeError("collection.json not found")
    with open(manifest_path, "r", encoding="utf-8-sig") as f:
        return scratch, json.load(f)


def _collection_extras(manifest: dict) -> dict:
    """The parts of a collection that are not a Nexus download.

    Every mod we install comes from source type "nexus". Two other types
    exist and were being dropped without a word:

    - "browse": hosted somewhere else entirely, so no API can fetch it.
      Vortex opens the page and the user downloads it by hand. This
      collection has one, Vanilla UI+, and it is the base layer the whole
      HUD stack is built on - so skipping it silently produced a game that
      booted to an error nobody could explain.
    - "bundle": shipped INSIDE the collection archive, already downloaded
      by the time we read the manifest. Nothing needed fetching and we
      still did not install them.
    - "direct": a plain URL the curator supplied, with an md5 to check it
      against. Fallout Rebirth+ has exactly one - FOSE, the Fallout script
      extender - marked optional: false, and it is the layer the whole
      collection runs on. Dropping it produced a collection that installed
      "with no mods left hanging" and then crashed on launch, which is the
      worst possible way to fail: nothing to see, and no clue why.
    """
    browse, bundle, direct = [], [], []
    for mod in manifest.get("mods") or []:
        source = mod.get("source") or {}
        kind = source.get("type")
        if kind == "browse":
            browse.append(
                {
                    "name": mod.get("name") or "",
                    "url": source.get("url") or "",
                    "instructions": source.get("instructions") or "",
                    "size": int(source.get("fileSize") or 0),
                    "md5": source.get("md5") or "",
                    "optional": bool(mod.get("optional")),
                }
            )
        elif kind == "bundle":
            bundle.append(
                {
                    "name": mod.get("name") or "",
                    "folder": source.get("fileExpression") or "",
                    "size": int(source.get("fileSize") or 0),
                    "optional": bool(mod.get("optional")),
                }
            )
        elif kind == "direct" and source.get("url"):
            direct.append(
                {
                    "name": mod.get("name") or "",
                    "url": source.get("url") or "",
                    "md5": (source.get("md5") or "").lower(),
                    "size": int(source.get("fileSize") or 0),
                    "optional": bool(mod.get("optional")),
                    # "dinput" is a DLL injector that lives beside the game
                    # exe, not in Data. FOSE is one.
                    "kind": ((mod.get("details") or {}).get("type") or ""),
                }
            )
    return {"browse": browse, "bundle": bundle, "direct": direct}


class Plugin:
    # ---- Nexus account -----------------------------------------------------

    async def set_api_key(self, api_key: str) -> dict:
        """Validate a key against the Nexus API; persist it only if valid.
        An empty string clears the stored key."""
        api_key = (api_key or "").strip()
        settings = _load_settings()
        if not api_key:
            settings.pop("api_key", None)
            settings.pop("content_gate", None)
            _save_settings(settings)
            decky.logger.info("API key cleared")
            return {"ok": False, "cleared": True, "error": "No API key set"}
        result = await _validate_key(api_key)
        if result.get("ok"):
            settings["api_key"] = api_key
            # Kept so a refused download can be diagnosed honestly. A 403
            # used to be reported as "you need Premium" on an account that
            # already had it, because nothing on this side knew.
            settings["is_premium"] = bool(result.get("is_premium"))
            _save_settings(settings)
            decky.logger.info(
                f"API key saved for user {result.get('name')} "
                f"(premium={result.get('is_premium')})"
            )
            try:
                await _refresh_content_gate(api_key)
            except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                # Non-fatal: sign-in succeeded; the QAM refreshes the gate
                # again on mount.
                decky.logger.warning(f"Content gate refresh at sign-in failed: {e}")
        else:
            decky.logger.warning(f"API key rejected: {result.get('error')}")
        return result

    async def get_auth_status(self) -> dict:
        settings = _load_settings()
        api_key = settings.get("api_key")
        if not api_key:
            return {"ok": False, "error": "No API key set"}
        result = await _validate_key(api_key)
        if result.get("ok") and bool(
            settings.get("is_premium")
        ) != bool(result.get("is_premium")):
            settings["is_premium"] = bool(result.get("is_premium"))
            _save_settings(settings)
        return result

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
                bool(search),
                trending_since,
                include_adult=_show_adult(),
                language=_user_prefs()["mod_language"],
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
        mods = _gate_adult_nodes(page["nodes"])
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
        status = endorsement.get("endorse_status") or "Undecided"
        # What we did outranks a stale read.
        #
        # Nexus's single-mod endpoint kept reporting "Undecided" for mods
        # this account had just endorsed successfully, so every deploy - which
        # remounts the panel and re-reads - showed them as un-endorsed again.
        # Michael: "surely it should know if I have already endorsed?"
        #
        # Only ever upgrades to Endorsed, and only from our own record of a
        # call that returned 200. If the user abstains we record that too, so
        # this cannot resurrect an endorsement they took back.
        if status == "Undecided":
            mine = (_load_settings().get("endorsed") or {}).get(
                game_domain, {}
            ).get(str(int(mod_id)))
            if mine:
                status = "Endorsed"
        return {
            "ok": True,
            "status": status,
            # The QAM endorses framework mods (SKSE, SMAPI...) that were
            # installed by a Step button, not browsed for, so nothing on
            # that screen knows the version the endorse call requires.
            "version": str(body.get("version") or ""),
        }

    async def set_endorsement(
        self, game_domain: str, mod_id: int, version: str, endorse: bool,
        _retried: bool = False,
    ) -> dict:
        """Endorse or abstain. Nexus Mods enforces its own rules (must have
        downloaded the mod, a cool-down after downloading, not your own mod)
        - map those to friendly messages."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        api_key = _load_settings().get("api_key")
        if not api_key:
            return {"ok": False, "error": "Not signed in"}
        if not (version or "").strip():
            # Endorsing from the QAM's Step 1 row: the framework was
            # installed for the user rather than picked from a mod page,
            # so look the version up instead of making every caller
            # carry one. A wrong version is rejected outright.
            state = await self.get_endorsement(game_domain, mod_id)
            version = state.get("version") or ""
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
                        # Remembered because the read-back cannot be relied
                        # on - see get_endorsement.
                        settings = _load_settings()
                        settings.setdefault("endorsed", {}).setdefault(
                            game_domain, {}
                        )[str(int(mod_id))] = bool(endorse)
                        _save_settings(settings)
                        return {
                            "ok": True,
                            "status": "Endorsed" if endorse else "Abstained",
                        }
                    message = str(body.get("message") or body.get("error") or "")
                    # NOT_DOWNLOADED_MOD on a mod that IS installed means we
                    # never asked Nexus for a download link - the archive
                    # cache short-circuits before that call, so nothing was
                    # ever registered. Michael could not endorse REFramework
                    # after installing it twice, and the API said
                    # NOT_DOWNLOADED_MOD for every version string tried.
                    #
                    # Registering it now is the honest repair: the download
                    # genuinely happened, the author is owed the count, and
                    # this is the same request the install would have made
                    # had it not been served from cache. Once only - a retry
                    # loop here would inflate somebody's download numbers.
                    if "NOT_DOWNLOADED_MOD" in message and not _retried:
                        if await _register_download(game_domain, mod_id, api_key):
                            return await self.set_endorsement(
                                game_domain, mod_id, version, endorse,
                                _retried=True,
                            )
                    friendly = {
                        # Nexus registers a download some minutes after
                        # it happens, so pressing endorse right after a
                        # Step 1 install hits this - and telling someone
                        # they have not downloaded a mod the plugin just
                        # installed for them reads as a plain bug.
                        # Verified on device 2026-08-12 with xNVSE: the
                        # same call succeeded later, unchanged.
                        "NOT_DOWNLOADED_MOD": (
                            "Nexus Mods hasn't registered the download yet. "
                            "If you just installed this, try again in a few minutes"
                        ),
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
        resolve the numeric game id once, then query via legacyMods.

        Returns Nexus mod requirements AND game DLC requirements. Michael,
        2026-08-13: "file to file and DLC requirements is something the
        website has put a lot of work into tackling and it feeds vortex via
        api so I would have thought its available to us too". It is - the
        plugin was asking for one of the three fields on ModRequirements.

        Verified live: New Vegas mod 65000 returns
        {"gameExpansion": {"name": "Dead Money"}}, which is the same fact
        DLC_MASTER_NAMES holds by hand for four games, from the authority
        instead of from me. It is also knowable BEFORE the download, where
        the hand-written table only catches it after a failed boot.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        api_key = _load_settings().get("api_key")
        try:
            game_id = await _resolve_game_id(game_domain, api_key)
            data = await _gql_query(
                "{ legacyMods(ids: [{gameId: %d, modId: %d}]) { nodes { "
                "modRequirements { nexusRequirements { nodes "
                "{ modName modId notes url } } "
                "dlcRequirements { notes gameExpansion { name } } "
                "} } } }"
                % (game_id, int(mod_id)),
                api_key,
            )
            nodes = data["legacyMods"]["nodes"]
            split = _split_requirements(nodes[0] if nodes else {})
            return {"ok": True, **split}
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
    collection { id name summary user { name } }
    modFiles {
      fileId
      optional
      file {
        fileId
        modId
        name
        version
        sizeInBytes
        mod { name game { domainName } }
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
            collection_id = int(coll.get("id") or 0)
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
                        # Collections pin files from OTHER domains too
                        # (Bethini Pie lives under "site") - installs can
                        # only serve this game's domain, the rest are
                        # desktop utilities to skip. Verified live.
                        "domain": (
                            ((f.get("mod") or {}).get("game") or {}).get(
                                "domainName"
                            )
                            or ""
                        ),
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
                    "id": collection_id,
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

    async def endorse_collection(
        self, collection_id: int, endorse: bool = True
    ) -> dict:
        """Endorse a collection, or abstain.

        Uses the generic `endorse` mutation rather than the mod-specific
        pair: it is marked deprecated on the schema ("will be replaced
        using Interfaces and Global IDs") but it is the only thing that
        can endorse a collection, and its handler says outright it is
        meant for exactly this.

        The rules differ from mods in a way worth knowing: the collection
        must have been first downloaded MORE THAN 12 HOURS ago, not 15
        minutes. Someone who just finished installing one cannot endorse
        it yet, so that refusal needs a real sentence rather than a code.
        """
        api_key = _load_settings().get("api_key")
        if not api_key:
            return {"ok": False, "error": "Not signed in"}
        if not collection_id:
            return {"ok": False, "error": "Unknown collection"}
        mutation = """
mutation EndorseCollection($modelId: Int!, $modelType: String!, $abstain: Boolean) {
  endorse(modelId: $modelId, modelType: $modelType, abstain: $abstain) {
    success
  }
}"""
        try:
            data = await _gql_query_vars(
                mutation,
                {
                    "modelId": int(collection_id),
                    "modelType": "Collection",
                    "abstain": not endorse,
                },
                api_key,
            )
        except RuntimeError as e:
            message = str(e)
            friendly = {
                "TOO_SOON_AFTER_DOWNLOAD": (
                    "You can endorse a collection 12 hours after downloading it"
                ),
                "cannot endorse this content yet": (
                    "You can endorse a collection 12 hours after downloading it"
                ),
                "Own Content": "You can't endorse your own collection",
                "Endorsing Not Allowed": (
                    "This curator has turned off endorsements"
                ),
                "NOT_ENDORSABLE": "This collection can't be endorsed",
            }
            for code, text in friendly.items():
                if code.lower() in message.lower():
                    return {"ok": False, "error": text}
            return {"ok": False, "error": message}
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return {"ok": False, "error": f"Network error: {type(e).__name__}"}
        if not ((data.get("endorse") or {}).get("success")):
            return {"ok": False, "error": "Nexus Mods did not accept it"}
        return {"ok": True, "status": "Endorsed" if endorse else "Abstained"}

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
                    _safe_uri(uri), ssl=SSL_CONTEXT
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

    async def get_collection_extras(
        self, slug: str, game_domain: str
    ) -> dict:
        """The mods in a collection that are not ordinary Nexus downloads.

        Reported BEFORE an install rather than discovered afterwards. On
        device the one browse-type mod in New Vegas's most popular
        collection was Vanilla UI+, the base layer the HUD is built on -
        skipped silently, so the collection installed "successfully" and
        then failed at boot with an error about XML not matching code.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug or ""):
            return {"ok": False, "error": "Invalid collection slug"}
        api_key = _load_settings().get("api_key")
        scratch = None
        try:
            scratch, manifest = await _fetch_collection_manifest(
                slug, game_domain, api_key
            )
            extras = _collection_extras(manifest)
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError,
                KeyError, ValueError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            if scratch:
                _force_rmtree(scratch)
        decky.logger.info(
            f"collection extras {slug!r}: {len(extras['browse'])} manual "
            f"download(s), {len(extras['bundle'])} bundled mod(s), "
            f"{len(extras['direct'])} direct download(s)"
        )
        return {"ok": True, **extras}

    async def install_collection_direct(
        self, slug: str, game_domain: str, install_dir: str,
        mods_subdir: str = "Data", app_id: int = 0,
        plugins_subpath: str = "", plugins_style: str = "starred",
    ) -> dict:
        """Install a collection's "direct" mods - a plain URL the curator
        supplied rather than a Nexus file.

        Fallout Rebirth+ has one: FOSE, from fose.silverlock.org, marked
        optional: false. It is the script extender the entire collection
        runs on, and it was being dropped without a word - so 168 mods
        installed "with no mods left hanging" and the game then crashed on
        launch with nothing to look at.

        The manifest supplies an md5 and a byte count for each, and both
        are checked. That matters more here than anywhere else in the
        plugin: this is the one place we fetch from a host that is not
        Nexus, over a URL a third party wrote, and FOSE's own site is
        plain HTTP with no certificate to trust. A hash the curator
        published is what makes that safe - so a mismatch is a hard
        failure, never a warning.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug or ""):
            return {"ok": False, "error": "Invalid collection slug"}
        api_key = _load_settings().get("api_key")
        scratch = None
        try:
            scratch, manifest = await _fetch_collection_manifest(
                slug, game_domain, api_key
            )
            wanted = _collection_extras(manifest)["direct"]
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError,
                KeyError, ValueError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            if scratch:
                _force_rmtree(scratch)
        if not wanted:
            return {"ok": True, "installed": 0, "names": [], "errors": []}
        install_path = os.path.join(STEAM_COMMON, install_dir)
        if not os.path.isdir(install_path):
            return {"ok": False, "error": "Game install folder not found"}
        done, errors, skipped = [], [], []
        for entry in wanted:
            name = entry["name"] or "a direct download"
            err, path = await _download_direct_file(
                entry["url"], entry["md5"], entry["size"]
            )
            if err:
                (skipped if entry["optional"] else errors).append(
                    f"{name}: {err}"
                )
                continue
            work = os.path.join(DOWNLOADS_DIR, f"direct-{abs(hash(name))}")
            _force_rmtree(work)
            os.makedirs(work)
            exerr = await _extract_archive(path, work)
            try:
                os.remove(path)
            except OSError:
                pass
            if exerr:
                _force_rmtree(work)
                errors.append(f"{name}: could not unpack ({exerr})")
                continue
            # A "dinput" injector sits beside the game exe; anything else
            # is ordinary mod content and belongs under the mods dir.
            target = (
                install_path if entry["kind"] == "dinput"
                else os.path.join(install_path, mods_subdir)
            )
            # Case-merged against what is on disk, same as every other
            # install path: a directory that differs only in case is a
            # different directory to us and the same one to Wine, and that
            # split the script extender's plugin folder in two once already.
            files, case_cache = [], {}
            copy_err = ""
            for root, _dirs, names in os.walk(work):
                for n in names:
                    full = os.path.join(root, n)
                    rel = os.path.relpath(full, work).replace(os.sep, "/")
                    if not _safe_rel_path(rel):
                        continue
                    rel = _case_merge_rel(target, rel, case_cache)
                    dst = os.path.join(target, *rel.split("/"))
                    try:
                        _makedirs_for(dst)
                        shutil.copy2(full, dst)
                    except OSError as e:
                        copy_err = f"{rel}: {e}"
                        break
                    files.append(rel)
                if copy_err:
                    break
            _force_rmtree(work)
            if copy_err:
                errors.append(f"{name}: {copy_err}")
                continue
            settings = _load_settings()
            records = settings.setdefault("installed", {}).setdefault(
                game_domain, {}
            )
            records[name] = _merge_install_record(records.get(name), {
                "name": name,
                "mode": "dataDir" if entry["kind"] != "dinput" else "files",
                "files": files,
                "source": "collection",
                "collection_slug": slug,
                "direct_url": entry["url"],
                "version": "",
                "installed_at": int(time.time()),
            })
            _save_settings(settings)
            done.append(name)
            decky.logger.info(
                f"installed direct download {name!r} ({len(files)} file(s)) "
                f"from {entry['url']}"
            )
        return {"ok": True, "installed": len(done), "names": done,
                "skipped": skipped[:4], "errors": errors[:4]}

    async def install_collection_bundles(
        self, slug: str, game_domain: str, install_dir: str,
        mods_subdir: str = "Data", app_id: int = 0,
        plugins_subpath: str = "", plugins_style: str = "starred",
    ) -> dict:
        """Install the mods a collection ships inside its own archive.

        Nothing here needs fetching from Nexus - the files arrive with the
        manifest we already download for its FOMOD choices, and were being
        thrown away with the rest of it. On device that was OneTweak and an
        NVAO animation pack, both non-optional, in the collection every
        other mod was installed from.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug or ""):
            return {"ok": False, "error": "Invalid collection slug"}
        api_key = _load_settings().get("api_key")
        _install_path, data_path, _unused = _game_paths(install_dir, mods_subdir)
        if not os.path.isdir(data_path):
            return {"ok": False, "error": f"{mods_subdir} not found"}
        scratch = None
        try:
            scratch, manifest = await _fetch_collection_manifest(
                slug, game_domain, api_key
            )
            bundles = _collection_extras(manifest)["bundle"]
            if not bundles:
                return {"ok": True, "installed": 0, "mods": [], "errors": []}
            # The archive lays them out under bundled/<fileExpression>/.
            roots = {}
            for root, dirs, _names in os.walk(scratch):
                if os.path.basename(root).lower() == "bundled":
                    for d in dirs:
                        roots[d.lower()] = os.path.join(root, d)
            settings = _load_settings()
            installed = settings.setdefault("installed", {}).setdefault(
                game_domain, {}
            )
            done, errors, plugins_added = [], [], []
            for b in bundles:
                src = roots.get((b["folder"] or "").lower())
                if not src:
                    errors.append(f"{b['name']}: not in the archive")
                    continue
                written, plugins = [], []
                # Case-merged against what is already on disk, exactly as
                # the normal dataDir install does. Skipping this created
                # Data/NVSE/Plugins beside the existing Data/NVSE/plugins
                # on device: the NVAO bundle ships a capital P, Wine then
                # resolved NVSE's request to the new empty directory, and
                # the script extender loaded NONE of its 56 plugins. The
                # game died after the intro logos with nothing in any log
                # to say why.
                case_cache: dict = {}
                for root, _dirs, names in os.walk(src):
                    for n in names:
                        full = os.path.join(root, n)
                        rel = os.path.relpath(full, src).replace(os.sep, "/")
                        if not _safe_rel_path(rel):
                            continue
                        rel = _case_merge_rel(data_path, rel, case_cache)
                        dst = os.path.join(data_path, *rel.split("/"))
                        try:
                            _makedirs_for(dst)
                            shutil.copy2(full, dst)
                        except OSError as e:
                            errors.append(f"{b['name']}: {rel}: {e}")
                            continue
                        written.append(rel)
                        if "/" not in rel and rel.lower().endswith(
                            (".esp", ".esm", ".esl")
                        ):
                            plugins.append(rel)
                if not written:
                    errors.append(f"{b['name']}: nothing to install")
                    continue
                record = {
                    "mod_id": None,
                    "file_id": None,
                    "name": b["name"],
                    "version": "",
                    "file_name": b["folder"],
                    "installed_at": int(time.time()),
                    "source": "collection-bundle",
                    "collection_slug": slug,
                    "mode": "dataDir",
                    "files": written,
                    "plugins": plugins,
                }
                installed[b["name"]] = _merge_install_record(
                    installed.get(b["name"]), record
                )
                plugins_added.extend(plugins)
                done.append(b["name"])
            _save_settings(settings)
            if plugins_added and plugins_subpath:
                _add_plugins(
                    _plugins_txt_path(app_id, plugins_subpath),
                    plugins_added, plugins_style, game_domain, data_path,
                )
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError,
                KeyError, ValueError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            if scratch:
                _force_rmtree(scratch)
        decky.logger.info(
            f"collection bundles {slug!r}: installed {len(done)} "
            f"({', '.join(done)}), {len(errors)} error(s)"
        )
        return {
            "ok": True,
            "installed": len(done),
            "mods": done,
            "errors": errors[:8],
        }

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

    async def check_updates(
        self, game_domain: str, force_folders: list = None
    ) -> dict:
        """Compare installed (tracked) mod versions against current Nexus
        versions. Version strings in the wild are messy, so 'update available'
        means 'differs from what we installed', normalized for a leading v.

        `force_folders` are checked even though a collection pinned them.
        See the source == "collection" skip below for why that needs an
        exception.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        records = _load_settings().get("installed", {}).get(game_domain, {})
        tracked = {
            folder: rec for folder, rec in records.items() if rec.get("mod_id")
        }
        if not tracked:
            return {"ok": True, "updates": {}}
        api_key = _load_settings().get("api_key")
        forced = {f for f in (force_folders or []) if f}
        try:
            game_id = await _resolve_game_id(game_domain, api_key)
            nodes = await _legacy_mods_in_batches(
                game_id,
                [int(rec["mod_id"]) for rec in tracked.values()],
                " modId version ",
                api_key,
            )
            current = {n["modId"]: n for n in nodes}
            updates = {}
            for folder, rec in tracked.items():
                node = current.get(rec["mod_id"])
                if not node:
                    continue
                # Collection installs are pinned by the curator - nagging
                # users to update them off-plan does more harm than good.
                #
                # Unless the pin has demonstrably failed. Slay the Spire 2,
                # 2026-08-13: a collection pinned BaseLib 3.1.2 and RitsuLib
                # 0.2.30 against a game build that wants 3.3.8 and 0.5.11,
                # the game printed "Loaded 21 mods WITH ERRORS" across the
                # main menu, and this skip meant the plugin looked at that
                # and reported no updates available. A pin the game cannot
                # run is not a plan worth respecting.
                if rec.get("source") == "collection" and folder not in forced:
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
                    # So the panel can say why it is nagging about a mod a
                    # collection pinned.
                    "blamed": folder in forced,
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
                        env=_host_env(),
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
                    env=_host_env(),
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
        batches the API answers fully, capped at 200 total."""
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
            nodes = await _legacy_mods_in_batches(
                game_id, ids, MOD_FIELDS, api_key
            )
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
            for m in _gate_adult_nodes(body, "contains_adult_content")
            if m.get("name") and m.get("available", True)
        ][: int(count)]
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
        cp77_layout: bool = False,
        pakpatch_layout: bool = False,
        repair_only: bool = False,
    ) -> dict:
        """Wrapper so any unexpected failure reaches the UI as a real message
        instead of decky's generic 'Python Exception'. dl_key/dl_expires are
        the website-issued free-download token from an nxm:// link;
        payload_choice picks a folder from an option-style archive."""
        try:
            result = await self._install_mod_inner(
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
                cp77_layout,
                pakpatch_layout,
                repair_only,
            )
            if not result.get("ok") and result.get("error"):
                # UI rows show failures the log never saw - record every
                # failed install so remote diagnosis has evidence.
                decky.logger.warning(
                    f"install {mod_name!r} ({game_domain}/{mod_id}) "
                    f"failed: {result['error']}"
                )
            return result
        except Exception as e:  # noqa: BLE001 - surfaced to UI + logged
            decky.logger.exception(f"install_mod({mod_name!r}) crashed")
            await _emit_progress(mod_id, "error", 0, str(e))
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def get_user_prefs(self) -> dict:
        return {"ok": True, "prefs": _user_prefs()}

    async def set_downloads_paused(self, paused: bool) -> dict:
        """Pause or resume every download at once.

        Global on purpose: mid-collection there can be eight transfers
        and a prefetch pump feeding them, and "pause" means "stop using
        my bandwidth", not "stop this one file". Each transfer closes its
        connection at the next chunk and keeps its .part; resume picks
        every one of them back up with a Range request.
        """
        global _DL_PAUSED
        _DL_PAUSED = bool(paused)
        decky.logger.info(
            f"downloads {'paused' if _DL_PAUSED else 'resumed'} "
            f"({len(_DL_ACTIVE)} in flight)"
        )
        return {"ok": True, "paused": _DL_PAUSED,
                "in_flight": len(_DL_ACTIVE)}

    async def cancel_download(self, mod_id: int) -> dict:
        """Abort one in-flight download and delete its partial file.

        Only downloads actually in flight can be cancelled: a mark left
        on a mod that is not downloading would sit there and silently
        kill the user's next retry of it.
        """
        if mod_id not in _DL_ACTIVE:
            return {"ok": False, "error": "That download isn't running"}
        _DL_CANCEL.add(mod_id)
        return {"ok": True}

    async def get_download_control(self) -> dict:
        """Paused state + in-flight count, so a reopened Downloads page
        shows the truth rather than whatever it last remembered."""
        return {"ok": True, "paused": _DL_PAUSED,
                "in_flight": len(_DL_ACTIVE)}

    async def get_disk_usage(self) -> dict:
        """Free/total space on the downloads volume - drives the Downloads
        page disk gauge (paired with the min_free_gb floor)."""
        try:
            os.makedirs(DOWNLOADS_DIR, exist_ok=True)
            usage = shutil.disk_usage(DOWNLOADS_DIR)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "total_gb": round(usage.total / (1 << 30), 1),
            "free_gb": round(usage.free / (1 << 30), 1),
            "min_free_gb": _user_prefs()["min_free_gb"],
        }

    async def set_user_prefs(self, prefs: dict) -> dict:
        """Store Settings-tab values. Unknown keys are ignored; known
        ones clamp to their bounds (see USER_PREF_BOUNDS)."""
        settings = _load_settings()
        stored = settings.setdefault("user_prefs", {})
        for name, (default, lo, hi) in USER_PREF_BOUNDS.items():
            if name in (prefs or {}):
                try:
                    stored[name] = max(lo, min(hi, int(prefs[name])))
                except (TypeError, ValueError):
                    pass
        if "mod_language" in (prefs or {}):
            stored["mod_language"] = _valid_mod_language(prefs["mod_language"])
        _save_settings(settings)
        merged = _user_prefs()
        decky.logger.info(f"user prefs updated: {merged}")
        return {"ok": True, "prefs": merged}

    async def prefetch_mod_file(
        self,
        game_domain: str,
        mod_id: int,
        file_id: int,
        file_name: str = "",
    ) -> dict:
        """Download a mod file into the archive cache WITHOUT installing.
        The collection pipeline runs several of these concurrently ahead
        of the serial installer, so the network stays busy while each
        mod extracts/installs - installs then hit the cache instantly."""
        api_key = _load_settings().get("api_key")
        if not api_key:
            return {"ok": False, "error": "Not signed in"}
        err, archive_path = await _download_archive(
            game_domain, mod_id, file_id, file_name, api_key
        )
        if err:
            # No error event: the serial installer will retry this file
            # itself and surface the REAL failure on its row.
            decky.logger.warning(f"prefetch {game_domain}/{mod_id}: {err}")
            return {"ok": False, "error": err}
        # Distinct phase so the UI can say "waiting to install" instead
        # of sitting silently at 100% downloaded.
        await _emit_progress(mod_id, "queued", 100)
        return {"ok": True, "path": archive_path}

    async def prepare_mod_file(
        self,
        game_domain: str,
        mod_id: int,
        file_id: int,
        file_name: str = "",
    ) -> dict:
        """Download AND extract a mod file, leaving it ready for install.

        Extraction is the expensive part of a cached install (measured on
        device: 579-1408ms of a 736-1586ms install), it is CPU-bound, and
        it touches nothing shared - so it can run for the NEXT mods while
        the current one is being committed to the game folder. The commit
        stays strictly serial in collection order, because for dataDir
        games the order files overwrite each other IS the load order and
        re-ordering afterwards cannot undo it.

        Leaves the scratch dir where _install_mod_inner looks for it; if
        anything here fails the installer just does the work itself.
        """
        api_key = _load_settings().get("api_key")
        if not api_key:
            return {"ok": False, "error": "Not signed in"}
        err, archive_path = await _download_archive(
            game_domain, mod_id, file_id, file_name, api_key
        )
        if err:
            decky.logger.warning(f"prepare {game_domain}/{mod_id}: {err}")
            return {"ok": False, "error": err}
        scratch = _extract_scratch(mod_id, file_id)
        ready = scratch + PREPARED_MARKER
        if os.path.isfile(ready):
            return {"ok": True, "prepared": True}
        _force_rmtree(scratch)
        os.makedirs(scratch, exist_ok=True)
        err = await _extract_archive(archive_path, scratch)
        if err:
            # Leave it to the installer, which owns the error reporting
            # and the eviction of a corrupt archive.
            _force_rmtree(scratch)
            decky.logger.info(f"prepare {game_domain}/{mod_id}: {err[:120]}")
            return {"ok": False, "error": err}
        await asyncio.to_thread(_normalize_perms, scratch)
        # The marker is written last, so a half-done prepare (crash, or
        # the plugin reloading mid-extract) is never mistaken for ready.
        with open(ready, "w") as f:
            f.write(str(int(time.time())))
        await _emit_progress(mod_id, "queued", 100)
        return {"ok": True, "prepared": True}

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
            _makedirs_for(dst)
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
        _new_record = {
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
        installed[record_key] = _merge_install_record(
            installed.get(record_key), _new_record
        )
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
        cp77_layout: bool = False,
        pakpatch_layout: bool = False,
        repair_only: bool = False,
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

        # Per-phase timings, logged once per mod. Collection installs are
        # the slow path and guessing at where the time goes has not served
        # us well - this makes it a measurement instead.
        _t0 = time.monotonic()
        _phase = {}

        # 1+2) Resolve the download link and fetch to the archive cache -
        # a completed prefetch (collection pipeline) short-circuits here.
        err, archive_path = await _download_archive(
            game_domain, mod_id, file_id, file_name, api_key, dl_key, dl_expires
        )
        if err:
            return {"ok": False, "error": err}
        _phase["download"] = time.monotonic() - _t0

        # 3) Extract to a scratch dir, then move into mods/. The
        # extract-ahead worker may already have done this for us while the
        # previous mod was being committed - in which case take its work
        # and go straight to the merge.
        await _emit_progress(mod_id, "extracting", 100)
        scratch = _extract_scratch(mod_id, file_id)
        prepared = os.path.isfile(scratch + PREPARED_MARKER)
        if prepared:
            try:
                os.remove(scratch + PREPARED_MARKER)
            except OSError:
                pass
        else:
            _force_rmtree(scratch)
            os.makedirs(scratch, exist_ok=True)
            err = await _extract_archive(archive_path, scratch)
            if err:
                _force_rmtree(scratch)
                # Evict the cached archive: a corrupt download must not
                # keep short-circuiting every retry.
                try:
                    os.remove(archive_path)
                except OSError:
                    pass
                await _emit_progress(mod_id, "error", 0, err)
                return {"ok": False, "error": f"Extraction failed: {err}"}
        _phase["extract"] = time.monotonic() - _t0 - _phase["download"]
        if not prepared:
            # Archives in the wild ship read-only entries; normalize
            # before moving. One chmod per file, so a worker thread -
            # inline, a mod with thousands of files froze every download
            # in flight. (A prepared scratch has had this done already.)
            await asyncio.to_thread(_normalize_perms, scratch)
        _phase["perms"] = (
            time.monotonic() - _t0 - _phase["download"] - _phase["extract"]
        )
        if prepared:
            _phase["prepared"] = 1.0

        def _log_phases(kind: str, extra: str = "") -> None:
            spent = ", ".join(f"{k} {v * 1000:.0f}ms" for k, v in _phase.items())
            total = (time.monotonic() - _t0) * 1000
            decky.logger.info(
                f"install timing {mod_name!r} [{kind}] total {total:.0f}ms "
                f"({spent}){extra}"
            )

        entries = os.listdir(scratch)
        if not entries:
            _force_rmtree(scratch)
            return {"ok": False, "error": "Archive was empty"}

        # A legacy .fomod package is an archive in its own right; unwrap it
        # so everything below sees the wizard rather than an opaque file.
        if await _unwrap_fomod_package(scratch):
            entries = os.listdir(scratch)

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
                            "repair_only": repair_only,
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
                    # ...unless they are dlls. A Bethesda mod's dlls live
                    # in Data/SKSE/Plugins, never loose in Data/ - a dll
                    # at the archive root belongs BESIDE the game exe
                    # (SSE Engine Fixes part 2's d3dx9_42.dll preloader,
                    # ENB binaries). Sending those to Data/ installs a
                    # file the game never looks at, and Engine Fixes then
                    # refuses to start the game (device, 2026-08-08).
                    if any(e.lower().endswith(ROOT_BINARY_EXTS) for e in entries):
                        decky.logger.info(
                            f"install {mod_name!r}: loose binaries at the "
                            "archive root - installing beside the game exe"
                        )
                        return await self._install_root_files(
                            scratch, install_path, game_domain, mod_id,
                            file_id, file_name, mod_name, mod_version,
                            page_version, record_source, collection_slug,
                        )
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
            _record_vanilla_baseline(game_domain, mods_path)
            os.makedirs(mods_path, exist_ok=True)

            def _merge_data_payloads():
                """Thousands of file moves per mod: run them in a worker so
                the event loop keeps servicing the prefetcher's downloads.
                Inline, a big mod's merge stalled every download in flight
                for as long as it took."""
                files_rel, plugins = [], []
                seen_rel = set()
                added = 0
                # One directory index shared by every file in this install.
                case_cache: dict = {}
                for payload in payload_dirs:
                    for root, _dirs, names in os.walk(payload):
                        for name in names:
                            src_file = os.path.join(root, name)
                            rel = os.path.relpath(src_file, payload)
                            if not _safe_rel_path(rel):
                                continue
                            # Reuse existing on-disk casing so we never
                            # create twin dirs (Textures vs textures) that
                            # Wine splits between.
                            rel = _case_merge_rel(mods_path, rel, case_cache)
                            dst = os.path.join(mods_path, *rel.split("/"))
                            if os.path.isfile(dst):
                                if repair_only:
                                    # Repair restores what went missing; it
                                    # must NOT overwrite. A file that is
                                    # already there is either this mod's or
                                    # a later mod's deliberate override,
                                    # and re-asserting it would undo the
                                    # collection's conflict order.
                                    if rel not in seen_rel:
                                        seen_rel.add(rel)
                                        files_rel.append(rel)
                                    if (
                                        "/" not in rel
                                        and rel.lower().endswith(PLUGIN_EXTENSIONS)
                                        and rel not in plugins
                                    ):
                                        plugins.append(rel)
                                    continue
                                os.remove(dst)
                            _makedirs_for(dst)
                            shutil.move(src_file, dst)
                            added += 1
                            if rel not in seen_rel:
                                seen_rel.add(rel)
                                files_rel.append(rel)
                            if (
                                "/" not in rel
                                and rel.lower().endswith(PLUGIN_EXTENSIONS)
                                and rel not in plugins
                            ):
                                plugins.append(rel)
                return files_rel, plugins, added

            _merge_t = time.monotonic()
            files_rel, plugins, added = await asyncio.to_thread(
                _merge_data_payloads
            )
            _phase["merge"] = time.monotonic() - _merge_t
            _force_rmtree(scratch)
            try:
                os.remove(archive_path)
            except OSError:
                pass
            if not files_rel:
                return {"ok": False, "error": "Archive contained no files"}
            if plugins and plugins_subpath:
                ptxt = _plugins_txt_path(app_id, plugins_subpath)
                _add_plugins(ptxt, plugins, plugins_style,
                             game_domain, mods_path)
                # FO3/FNV: load order = file timestamps; restamp so a
                # Jan-2000 archive mtime can't load before its master.
                _stagger_plugin_mtimes(
                    mods_path, ptxt, plugins_style, game_domain
                )
            record_key = _safe_name(mod_name)
            settings = _load_settings()  # re-read: parallel installs
            installed = settings.setdefault("installed", {}).setdefault(
                game_domain, {}
            )
            _new_record = {
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
            installed[record_key] = _merge_install_record(
                installed.get(record_key), _new_record
            )
            _save_settings(settings)
            decky.logger.info(
                f"{'repaired' if repair_only else 'installed'} {mod_name!r} "
                f"into Data/ ({len(files_rel)} files, {len(plugins)} plugins"
                f"{f', {added} restored' if repair_only else ''})"
            )
            _log_phases("dataDir", f", {len(files_rel)} files")
            await _emit_progress(mod_id, "done", 100)
            return {"ok": True, "folder": record_key, "added": added}

        # FromSoft games: mods never enter the game folder. The payload
        # becomes one folder under the plugin's me3 profile dir, and the
        # regenerated .me3 profile is what actually activates it. This
        # runs BEFORE the UE4SS gate below - that gate fires on a stray
        # enabled.txt anywhere in the archive, which would refuse a
        # perfectly installable FromSoft mod over an unrelated loader.
        if install_mode == "me3":
            root, assets_subpath, dlls, route_err = _route_me3_payload(
                scratch, mod_name
            )
            if route_err:
                kind, message = route_err
                decky.logger.info(f"me3 {mod_name!r}: {kind}: {message}")
                _force_rmtree(scratch)
                await _emit_progress(mod_id, "error", 0, kind)
                return {
                    "ok": False,
                    "error": message,
                    ("unsupported_tool" if kind == "tool" else
                     "unsupported_layout"): True,
                }
            folder = _safe_name(mod_name)
            settings = _load_settings()  # re-read: parallel installs
            has_regulation = os.path.isfile(
                os.path.join(root, *(assets_subpath or "").split("/"),
                             "regulation.bin")
                if assets_subpath
                else os.path.join(root, "regulation.bin")
            )
            if has_regulation:
                owner = _me3_regulation_owner(settings, game_domain, folder)
                if owner:
                    _force_rmtree(scratch)
                    await _emit_progress(mod_id, "error", 0, "regulation clash")
                    # A conflict, not a bad archive: disabling the other
                    # owner makes this installable, so it must not be
                    # parked as permanently unsupported.
                    return {
                        "ok": False,
                        "mod_conflict": True,
                        "error": (
                            f"{mod_name} replaces regulation.bin, and "
                            f"{owner} already does. Only one mod can own "
                            "that file - disable or uninstall the other "
                            "one first, or use a merged version of the two."
                        ),
                    }
            dest = os.path.join(_me3_mods_dir(game_domain), folder)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            _force_rmtree(dest)
            shutil.move(root, dest)
            _force_rmtree(scratch)
            try:
                os.remove(archive_path)
            except OSError:
                pass
            installed = settings.setdefault("installed", {}).setdefault(
                game_domain, {}
            )
            installed[folder] = {
                "mod_id": mod_id,
                "file_id": file_id,
                "name": mod_name,
                "version": mod_version,
                "file_name": file_name,
                "installed_at": int(time.time()),
                "page_version": page_version,
                "source": record_source,
                "collection_slug": collection_slug,
                "mode": "me3",
                "folder": folder,
                "package": assets_subpath is not None,
                "package_subpath": assets_subpath or "",
                "natives": dlls,
                "regulation": has_regulation,
                "enabled": True,
            }
            _write_me3_profile(game_domain, settings)
            _save_settings(settings)
            decky.logger.info(
                f"installed me3 mod {mod_name!r} -> {folder} "
                f"(assets={assets_subpath!r}, natives={dlls}, "
                f"regulation={has_regulation})"
            )
            await _emit_progress(mod_id, "done", 100)
            return {"ok": True, "folder": folder}

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
            _new_record = {
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
            installed[record_key] = _merge_install_record(
                installed.get(record_key), _new_record
            )
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
            merged_rels = []
            if w3_err and w3_err[0] == "conflicts":
                conflicts = w3_err[1]
                settings_now = _load_settings()
                # Auto-merge is OFF by default (2026-07-24). A line-merge
                # can produce a structurally-valid script that still won't
                # compile - and a bad merged mod even crashed BEFORE the
                # compile stage on device. Skipping the second mod with a
                # clear note is the reliable default: the game always
                # boots, the higher-priority mod wins, and merging is an
                # explicit opt-in (settings 'w3_auto_merge') for when a
                # future health-check can validate the result.
                merged_rels = None
                if settings_now.get("w3_auto_merge"):
                    # Worker thread: difflib on 10k-line scripts froze the
                    # whole event loop when run inline.
                    merged_rels = await asyncio.to_thread(
                        _w3_try_merge_conflicts,
                        game_domain, install_path, mods_path, conflicts,
                        settings_now,
                    )
                if merged_rels is not None:
                    for d in mod_dirs:
                        _w3_register_merge_participant(
                            game_domain, settings_now, merged_rels,
                            os.path.basename(d),
                        )
                    _save_settings(settings_now)
                    decky.logger.info(
                        f"W3 {mod_name!r}: auto-merged "
                        f"{len(merged_rels)} script(s) into {W3_MERGED_MOD}"
                    )
                    w3_err = None
                else:
                    merged_rels = []
                    rel, owner, _src, _osrc = conflicts[0]
                    decky.logger.info(
                        f"W3 {mod_name!r}: script conflict with {owner!r} "
                        f"on scripts/{rel} (auto_merge="
                        f"{bool(settings_now.get('w3_auto_merge'))})"
                    )
                    w3_err = (
                        "conflict",
                        f"Skipped: '{mod_name}' edits scripts/{rel}, which "
                        f"'{owner}' already changed. Kept the installed one "
                        "to keep the game bootable.",
                    )
            if w3_err:
                kind, message = w3_err
                if kind == "binoverlay":
                    # bin/-only overlay: drop readme clutter, install the
                    # bin tree into the game root as a files-mode record.
                    for e in list(os.listdir(scratch)):
                        if e.lower() != "bin":
                            p = os.path.join(scratch, e)
                            _force_rmtree(p) if os.path.isdir(p) else os.remove(p)
                    return await self._install_root_files(
                        scratch, install_path, game_domain, mod_id,
                        file_id, file_name, mod_name, mod_version,
                        page_version, record_source, collection_slug,
                    )
                _force_rmtree(scratch)
                await _emit_progress(mod_id, "error", 0, kind)
                result = {"ok": False, "error": message}
                if kind == "tool":
                    result["unsupported_tool"] = True
                elif kind == "conflict":
                    # Not retryable without script merging - the UI
                    # parks these instead of counting them as missing.
                    result["script_conflict"] = True
                elif kind == "layout":
                    result["unsupported_layout"] = True
                return result
            _record_vanilla_baseline(game_domain, mods_path)
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
            # Menu XMLs FIRST: they often live INSIDE a mod folder
            # (modX/bin/config/.../pc/), and moving the folder first made
            # the XML's scratch path vanish (crashed Increased Draw
            # Distance on device).
            pc_dir = os.path.join(install_path, *W3_MENU_DIR.split("/"))
            xml_names = []
            if menu_xmls:
                os.makedirs(pc_dir, exist_ok=True)
                for x in menu_xmls:
                    name = os.path.basename(x)
                    dstx = _adopt_case(os.path.join(pc_dir, name))
                    if os.path.isfile(dstx):
                        # Overwriting one of the game's own menu XMLs
                        # (HD Reworked ships rendering.xml): keep the
                        # vanilla copy so uninstall can restore it.
                        if (
                            name.lower() in W3_VANILLA_MENU_XMLS
                            and not os.path.isfile(
                                dstx + W3_VANILLA_BACKUP_SUFFIX
                            )
                        ):
                            shutil.copy2(
                                dstx, dstx + W3_VANILLA_BACKUP_SUFFIX
                            )
                        os.remove(dstx)
                    shutil.move(x, dstx)
                    _w3_filelist_append(pc_dir, name)
                    xml_names.append(name)
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
                dst = _adopt_case(os.path.join(dlc_root, folder))
                if os.path.basename(dst).lower() in W3_OFFICIAL_DLC or (
                    folder.lower() in W3_OFFICIAL_DLC
                ):
                    # Official DLC patch: merge files INTO the game's
                    # folder with a per-file record - never replace it.
                    rels = []
                    for root, _dirs, names in os.walk(d):
                        for name in names:
                            rel = os.path.relpath(
                                os.path.join(root, name), d
                            ).replace(os.sep, "/")
                            if not _safe_rel_path(rel):
                                continue
                            tgt = os.path.join(dst, *rel.split("/"))
                            os.makedirs(os.path.dirname(tgt), exist_ok=True)
                            if os.path.isfile(tgt):
                                os.remove(tgt)
                            shutil.move(os.path.join(root, name), tgt)
                            rels.append(rel)
                    rec_key = f"{_safe_name(mod_name)}_{folder}"
                    installed[rec_key] = {
                        **base_rec,
                        "name": f"{mod_name} ({folder} patch)",
                        "mode": "files",
                        "target": f"dlc/{folder}",
                        "files": rels,
                    }
                    first_folder = first_folder or rec_key
                    continue
                _force_rmtree(dst)
                shutil.move(d, dst)
                installed[folder] = {
                    **base_rec,
                    "name": f"{mod_name} ({folder})",
                    "target": "dlc",
                    "folder": folder,
                }
                first_folder = first_folder or folder
            if xml_names and first_folder and first_folder in installed:
                installed[first_folder]["menuXmls"] = xml_names
            elif xml_names and not first_folder:
                # XML-only archive (pure menu/config mods): files-mode
                # record so it lists and uninstalls like everything else.
                installed[_safe_name(mod_name)] = {
                    **base_rec,
                    "name": mod_name,
                    "mode": "files",
                    "target": W3_MENU_DIR,
                    "files": xml_names,
                    "menuXmls": xml_names,
                }
                first_folder = _safe_name(mod_name)
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

        # RE Engine pak-patch layout (RE4 remake): .paks take the next
        # numbers in the patch chain; loose-file mods (natives/ trees,
        # Fluffy format) merge into the game root and load via
        # REFramework's built-in LooseFileLoader (enabled in its config).
        if pakpatch_layout:
            paks, natives_dirs, ref_dirs = _pakpatch_payload(scratch)
            if not paks and not natives_dirs and not ref_dirs:
                _force_rmtree(scratch)
                try:
                    os.remove(archive_path)
                except OSError:
                    pass
                await _emit_progress(mod_id, "error", 0, "no payload")
                # unsupported_layout: retrying can't change the archive -
                # collections park it with a note instead of failing
                # forever (ReShade presets, desktop-tool payloads).
                return {
                    "ok": False,
                    "unsupported_layout": True,
                    "error": "No installable payload (.pak, natives or "
                    "reframework) - ReShade presets and desktop-tool "
                    "archives can't be used on this device",
                }
            # Multi-pak archives are almost always OPTION packs (one pak
            # per variant) - installing all 21 of 'Max Stack Sizes' was
            # wrong. Offer the choice; '*' merges everything.
            if len(paks) > 1 and not natives_dirs and not ref_dirs:
                if payload_choice == "*":
                    pass  # install all below
                elif payload_choice:
                    chosen = [
                        p
                        for p in paks
                        if os.path.relpath(p, scratch).replace(os.sep, "/")
                        == payload_choice
                    ]
                    if not chosen:
                        _force_rmtree(scratch)
                        return {"ok": False, "error": "Chosen pak wasn't found"}
                    paks = chosen
                else:
                    options = [
                        os.path.relpath(p, scratch).replace(os.sep, "/")
                        for p in paks
                    ]
                    _force_rmtree(scratch)
                    try:
                        os.remove(archive_path)
                    except OSError:
                        pass
                    await _emit_progress(mod_id, "error", 0, "choose a pak")
                    return {"ok": False, "needs_choice": True, "options": options}
            existing = []
            for name in os.listdir(install_path):
                m = RE4_PAK_RE.match(name)
                if m:
                    existing.append(int(m.group(1)))
            next_n = max(existing, default=-1) + 1
            assigned = []
            for src in paks:
                dst_name = _pakpatch_name(next_n)
                next_n += 1
                shutil.move(src, os.path.join(install_path, dst_name))
                assigned.append(dst_name)
            # Loose trees merge into the game root with per-file records:
            # natives/ (Fluffy-format assets - needs REFramework's loose
            # loader switched on) and reframework/ (script mods).
            loose_rel = []
            seen_loose = set()
            case_cache: dict = {}

            def _merge_tree(src_root: str, root_name: str):
                for root, _dirs, names in os.walk(src_root):
                    for name in names:
                        src_file = os.path.join(root, name)
                        rel = os.path.join(
                            root_name, os.path.relpath(src_file, src_root)
                        ).replace(os.sep, "/")
                        if not _safe_rel_path(rel):
                            continue
                        rel = _case_merge_rel(install_path, rel, case_cache)
                        dst = os.path.join(install_path, *rel.split("/"))
                        _makedirs_for(dst)
                        if os.path.isfile(dst):
                            os.remove(dst)
                        shutil.move(src_file, dst)
                        if rel not in seen_loose:
                            seen_loose.add(rel)
                            loose_rel.append(rel)

            def _merge_loose():
                for nd in natives_dirs:
                    _merge_tree(nd, "natives")
                for rd in ref_dirs:
                    _merge_tree(rd, "reframework")

            await asyncio.to_thread(_merge_loose)
            if natives_dirs:
                _ensure_config_key(
                    os.path.join(install_path, RE4_REF_CONFIG),
                    REF_LOOSE_KEY,
                    "true",
                )
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
            _new_record = {
                "mod_id": mod_id,
                "file_id": file_id,
                "name": mod_name,
                "version": mod_version,
                "file_name": file_name,
                "mode": "files",
                "target": ".",
                "files": assigned + loose_rel,
                "pakpatch": True,
                "source": record_source or "browse",
                "collection_slug": collection_slug,
            }
            installed[record_key] = _merge_install_record(
                installed.get(record_key), _new_record
            )
            _save_settings(settings)
            decky.logger.info(
                f"installed RE4 {mod_name!r}: {len(assigned)} pak(s), "
                f"{len(loose_rel)} loose file(s)"
            )
            await _emit_progress(mod_id, "done", 100)
            return {"ok": True, "folder": record_key}

        # Cyberpunk layout: game-root-relative payloads across the known
        # roots (bin/red4ext/r6/engine/archive) or bare .archive files -
        # everything lands as an exact-file record for clean uninstall.
        if cp77_layout:
            cp_files, cp_err = _route_cp77_payload(scratch, mod_name)
            if cp_err:
                kind, message = cp_err
                decky.logger.info(f"CP77 {mod_name!r}: {kind}: {message}")
                _force_rmtree(scratch)
                await _emit_progress(mod_id, "error", 0, kind)
                result = {"ok": False, "error": message}
                if kind == "tool":
                    result["unsupported_tool"] = True
                else:
                    result["unsupported_layout"] = True
                return result
            installed_rel = []
            for rel, src in cp_files:
                dst = os.path.join(install_path, *rel.split("/"))
                _makedirs_for(dst)
                if os.path.isfile(dst):
                    os.remove(dst)
                shutil.move(src, dst)
                installed_rel.append(rel)
            _force_rmtree(scratch)
            try:
                os.remove(archive_path)
            except OSError:
                pass
            settings = _load_settings()
            installed = settings.setdefault("installed", {}).setdefault(
                game_domain, {}
            )
            record_key = _safe_name(mod_name)
            _new_record = {
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
            installed[record_key] = _merge_install_record(
                installed.get(record_key), _new_record
            )
            _save_settings(settings)
            decky.logger.info(
                f"installed CP77 {mod_name!r}: {len(installed_rel)} "
                "file(s) into game roots"
            )
            await _emit_progress(mod_id, "done", 100)
            return {"ok": True, "folder": record_key}

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
            _record_vanilla_baseline(game_domain, mods_path)
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
            _new_record = {
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
            installed[record_key] = _merge_install_record(
                installed.get(record_key), _new_record
            )
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
        _record_vanilla_baseline(game_domain, mods_path)
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
                decky.logger.warning(
                    f"FOMOD token {token!r} expired before it was finished"
                )
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
                # Logged, not just returned: a FOMOD that quietly staged
                # nothing left no trace anywhere, so two collection mods
                # failed for a week with the log showing only that their
                # options had matched.
                decky.logger.warning(
                    f"FOMOD {entry.get('mod_name')!r}: staged 0 files from "
                    f"{len(selected_ids or [])} selected option(s)"
                )
                # A permanent skip, not a question to ask again. On device
                # a collection listed a SECOND file of Iron Sights Aligned
                # whose installer offers options none of whose sources are
                # in the archive - so every attempt stages nothing, and
                # Finish setup kept presenting it as work outstanding. The
                # mod itself was already installed from its main file.
                return {
                    "ok": False,
                    "nothing_staged": True,
                    "error": (
                        "This installer has nothing to install - the "
                        "options it offers are not in the archive. Skipped."
                    ),
                }

            _, mods_path, _unused = _game_paths(
                entry["install_dir"], entry["mods_subdir"]
            )
            _record_vanilla_baseline(entry.get("game_domain", ""), mods_path)
            os.makedirs(mods_path, exist_ok=True)

            repair_only = bool(entry.get("repair_only"))

            def _merge_staged():
                files_rel, plugins = [], []
                seen_rel = set()
                added = 0
                case_cache: dict = {}
                for root, _dirs, names in os.walk(staging):
                    for name in names:
                        src_file = os.path.join(root, name)
                        rel = os.path.relpath(src_file, staging)
                        if not _safe_rel_path(rel):
                            continue
                        rel = _case_merge_rel(mods_path, rel, case_cache)
                        dst = os.path.join(mods_path, *rel.split("/"))
                        if os.path.isfile(dst):
                            # Repair restores what went missing and never
                            # overwrites - see the dataDir merge.
                            if repair_only:
                                if rel not in seen_rel:
                                    seen_rel.add(rel)
                                    files_rel.append(rel)
                                if "/" not in rel and rel.lower().endswith(
                                    PLUGIN_EXTENSIONS
                                ) and rel not in plugins:
                                    plugins.append(rel)
                                continue
                            os.remove(dst)
                        _makedirs_for(dst)
                        shutil.move(src_file, dst)
                        added += 1
                        if rel not in seen_rel:
                            seen_rel.add(rel)
                            files_rel.append(rel)
                        if "/" not in rel and rel.lower().endswith(
                            PLUGIN_EXTENSIONS
                        ) and rel not in plugins:
                            plugins.append(rel)
                return files_rel, plugins, added

            files_rel, plugins, added = await asyncio.to_thread(_merge_staged)
            _force_rmtree(scratch)
            if plugins and entry["plugins_subpath"]:
                ptxt = _plugins_txt_path(
                    entry["app_id"], entry["plugins_subpath"]
                )
                _add_plugins(ptxt, plugins, entry["plugins_style"],
                             entry.get("game_domain", ""),
                             entry.get("mods_path", ""))
                _stagger_plugin_mtimes(
                    mods_path, ptxt, entry["plugins_style"],
                    entry["game_domain"],
                )
            record_key = _safe_name(entry["mod_name"])
            settings = _load_settings()
            installed = settings.setdefault("installed", {}).setdefault(
                entry["game_domain"], {}
            )
            _new_record = {
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
            installed[record_key] = _merge_install_record(
                installed.get(record_key), _new_record
            )
            _save_settings(settings)
            decky.logger.info(
                f"{'repaired' if repair_only else 'installed'} FOMOD "
                f"{entry['mod_name']!r}: {len(files_rel)} files, "
                f"{len(plugins)} plugins, {len(selected_ids or [])} options"
                f"{f', {added} restored' if repair_only else ''}"
            )
            await _emit_progress(entry["mod_id"], "done", 100)
            return {"ok": True, "folder": record_key, "added": added}
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
        return await self._install_framework_inner(
            game_domain,
            mod_id,
            install_dir,
            install_kind,
            detect_file,
            avoid_file_keywords,
            install_subdir,
        )

    async def seed_game_ini(
        self,
        install_dir: str,
        app_id: int,
        source_rel: str,
        prefs_subpath: str,
    ) -> dict:
        """Copy the game's default ini into the prefix Documents when the
        real one is missing. FO3's launcher normally creates FALLOUT.INI
        on first run - but that launcher hangs under Proton and never
        gets that far, so the game exe has nothing to boot with."""
        if not _safe_rel_path(source_rel) or not _safe_rel_path(prefs_subpath):
            return {"ok": False, "error": "Invalid path"}
        dst = _game_prefs_path(app_id, prefs_subpath)
        if os.path.isfile(dst):
            return {"ok": True, "seeded": False}
        src = os.path.join(STEAM_COMMON, install_dir, *source_rel.split("/"))
        if not os.path.isfile(src):
            return {"ok": False, "error": f"{source_rel} not found in game dir"}
        _makedirs_for(dst)
        shutil.copy2(src, dst)
        decky.logger.info(f"seeded {prefs_subpath} from {source_rel}")
        return {"ok": True, "seeded": True}

    async def run_prefix_tool(
        self,
        game_domain: str,
        mod_id: int,
        install_dir: str,
        app_id: int,
        exe_hint: str = "",
        avoid_file_keywords: list = None,
        verify_changed: list = None,
        timeout_sec: int = 180,
    ) -> dict:
        """Download a Windows modding TOOL from Nexus Mods (author gets
        the credit) and run it INSIDE the game's Proton prefix from the
        game dir - the exe-patcher class (FO3's ESM Patcher, Anniversary
        Patcher) the mod pipeline rightly refuses to 'install'. Success
        is judged by whether the files the tool exists to modify actually
        CHANGED (verify_changed): console patchers end on a 'press any
        key' that never comes headless, so exit codes lie."""
        decky.logger.info(
            f"prefix tool {game_domain}/{mod_id}: requested "
            f"(exe_hint={exe_hint!r}, verify={verify_changed})"
        )

        def _fail(stage: str, message: str) -> dict:
            # Every bail-out is logged AND persisted: silent early returns
            # made a failed ESM-Patcher run invisible (2026-08-06), and
            # toasts vanish before the user can read them.
            decky.logger.warning(
                f"prefix tool {game_domain}/{mod_id}: FAILED at {stage}: "
                f"{message}"
            )
            st = _load_settings()
            st.setdefault("prefix_tool_last", {}).setdefault(
                game_domain, {}
            )[str(mod_id)] = {
                "ok": False,
                "stage": stage,
                "message": message,
                "at": int(time.time()),
            }
            _save_settings(st)
            return {"ok": False, "error": message, "stage": stage}

        api_key = _load_settings().get("api_key")
        if not api_key:
            return _fail("auth", "Not signed in")
        install_path = os.path.join(STEAM_COMMON, install_dir)
        if not os.path.isdir(install_path):
            return _fail("game", "Game install folder not found")
        proton, compat, steam_root, perr = _proton_binary_for(app_id)
        if perr:
            return _fail("proton", perr)
        if not os.path.isdir(os.path.join(compat, "pfx")):
            return _fail(
                "prefix", "No Proton prefix yet - launch the game once first"
            )

        files = await self.get_mod_files(game_domain, mod_id)
        if not files.get("ok"):
            return _fail("files", files.get("error") or "file list failed")
        # The ESM Patcher publishes PAIRED English/French MAIN files, and
        # the shared picker kept choosing French (174MB, then failed to
        # unpack). Filter here: drop anything matching an avoid keyword in
        # EITHER field, case-insensitively, then take the newest MAIN.
        avoid = [k.lower() for k in (avoid_file_keywords or [])]
        candidates = []
        for f in files.get("files") or []:
            blob = f"{f.get('name', '')} {f.get('file_name', '')}".lower()
            if any(k in blob for k in avoid):
                continue
            candidates.append(f)
        mains = [
            f
            for f in candidates
            if str(f.get("category_name", "")).upper() == "MAIN"
        ]
        pool = mains or candidates
        main = max(pool, key=lambda f: int(f.get("file_id") or 0), default=None)
        if not main:
            return _fail("pick", "No downloadable file found")
        decky.logger.info(
            f"prefix tool {game_domain}/{mod_id}: picked "
            f"{main.get('name')!r} (file {main.get('file_id')})"
        )
        err, archive_path = await _download_archive(
            game_domain, mod_id, main["file_id"],
            main.get("file_name") or "", api_key,
        )
        if err:
            return _fail("download", err)
        scratch = os.path.join(DOWNLOADS_DIR, f"tool-{mod_id}")
        _force_rmtree(scratch)
        os.makedirs(scratch)
        exerr = await _extract_archive(archive_path, scratch)
        try:
            os.remove(archive_path)
        except OSError:
            pass
        if exerr:
            _force_rmtree(scratch)
            await _emit_progress(mod_id, "error", 0, "extract failed")
            return _fail("extract", f"Extraction failed: {exerr}")

        exes = []
        for root, _dirs, names in os.walk(scratch):
            for name in names:
                if name.lower().endswith(".exe"):
                    p = os.path.join(root, name)
                    exes.append((os.path.getsize(p), p))
        if exe_hint:
            hinted = [
                e
                for e in exes
                if exe_hint.lower() in os.path.basename(e[1]).lower()
            ]
            exes = hinted or exes
        if not exes:
            _force_rmtree(scratch)
            await _emit_progress(mod_id, "error", 0, "no exe")
            return _fail("exe", "No tool exe in this archive")
        exe_path = max(exes)[1]
        # Never re-run a patcher that already did its work: these tools
        # apply a binary diff expecting the ORIGINAL file, so a second
        # pass can corrupt what the first one fixed.
        already = _load_settings().get("prefix_tools", {}).get(
            game_domain, {}
        )
        if str(mod_id) in already:
            _force_rmtree(scratch)
            return {
                "ok": True,
                "changed": already[str(mod_id)].get("changed") or [],
                "already_applied": True,
            }
        tool_dir = os.path.dirname(exe_path)
        exe_rel = os.path.relpath(exe_path, tool_dir).replace(os.sep, "/")

        # Stage the tool's files beside the game exe (these patchers
        # expect CWD = game dir). NEVER overwrite existing game files;
        # everything staged is removed afterwards.
        staged = []
        stage_err = ""
        for root, _dirs, names in os.walk(tool_dir):
            for name in names:
                src = os.path.join(root, name)
                rel = os.path.relpath(src, tool_dir).replace(os.sep, "/")
                if not _safe_rel_path(rel):
                    continue
                dst = os.path.join(install_path, *rel.split("/"))
                if os.path.exists(dst):
                    # Already staged by an earlier run of this same tool.
                    #
                    # This used to fail the whole step: "already exists in
                    # the game folder - not overwriting it". Michael had run
                    # the Fallout 3 ESM patcher successfully weeks ago, so
                    # its exe was sitting there, and every attempt since has
                    # failed for the single reason that it had already
                    # worked. He remembered FO3 working and could not see
                    # why it now would not - because the guard against
                    # clobbering somebody's file was also refusing to reuse
                    # OUR OWN copy of a tool we put there.
                    #
                    # Reuse it. It is the tool's own executable, extracted
                    # from the same mod, and running it again is the entire
                    # point of pressing the button.
                    if src == exe_path:
                        decky.logger.info(
                            f"prefix tool {game_domain}/{mod_id}: {name} is "
                            "already staged from an earlier run - reusing it"
                        )
                        exe_path = dst
                        # Staged, therefore ours to remove. Leaving it out
                        # of this list is why Patcher.exe, its readme and
                        # xdelta3.* sat in Michael's Fallout 3 folder for
                        # days after the run that put them there.
                        staged.append(dst)
                    continue
                _makedirs_for(dst)
                shutil.copy2(src, dst)
                try:
                    os.chmod(dst, 0o755)
                except OSError:
                    pass
                staged.append(dst)
            if stage_err:
                break
        _force_rmtree(scratch)

        def _unstage():
            for p in staged:
                try:
                    os.remove(p)
                except OSError:
                    pass

        if stage_err:
            _unstage()
            await _emit_progress(mod_id, "error", 0, "staging blocked")
            return {"ok": False, "error": stage_err}

        before = {}
        for rel in verify_changed or []:
            p = os.path.join(install_path, *rel.split("/"))
            try:
                st = os.stat(p)
                before[rel] = (st.st_mtime_ns, st.st_size)
            except OSError:
                before[rel] = None

        # Proton must run as the deck user. The backend normally IS deck
        # (runuser refused with "may not be used by non-root users" on
        # device) - only drop privileges when actually root.
        exe_abs = os.path.join(install_path, *exe_rel.split("/"))
        run_env = _host_env({
            "STEAM_COMPAT_CLIENT_INSTALL_PATH": steam_root,
            "STEAM_COMPAT_DATA_PATH": compat,
        })
        if getattr(os, "geteuid", lambda: 1000)() == 0:
            cmd = [
                "runuser", "-u", "deck", "--", "env",
                f"STEAM_COMPAT_CLIENT_INSTALL_PATH={steam_root}",
                f"STEAM_COMPAT_DATA_PATH={compat}",
                "python3", proton, "run", exe_abs,
            ]
        else:
            cmd = ["python3", proton, "run", exe_abs]
        decky.logger.info(
            f"prefix tool {game_domain}/{mod_id}: running {exe_rel!r} "
            f"via {os.path.basename(os.path.dirname(proton))!r}"
        )
        await _emit_progress(mod_id, "extracting", 100)
        timed_out = False
        output = b""
        rc = -1
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=install_path,
                env=run_env,
                # Own session: on timeout the WHOLE tree dies (killing
                # just the proton wrapper orphaned Patcher.exe on device).
                start_new_session=True,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            # A stream of ENTERs answers the 'press any key to continue'
            # prompts these console patchers open with. Some never take the
            # hint: the Anniversary Patcher finishes its work in about 90
            # seconds and then sits at a prompt, so waiting for the process
            # to exit meant waiting out the whole timeout behind a button
            # that looked frozen. Michael, watching it: "step 3 seems to
            # have gotten stuck".
            #
            # The verify files are the real signal - they are what decides
            # success afterwards anyway - so poll them and stop as soon as
            # the work is visibly done.
            comm = asyncio.ensure_future(
                proc.communicate(input=b"\r\n" * 8)
            )

            def _changed_now():
                for rel, snap in before.items():
                    fp = os.path.join(install_path, *rel.split("/"))
                    try:
                        st2 = os.stat(fp)
                        now = (st2.st_mtime_ns, st2.st_size)
                    except OSError:
                        now = None
                    if now != snap:
                        return True
                return False

            def _kill_tree():
                try:
                    import signal

                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    proc.kill()

            def _fingerprint_now():
                out = []
                for rel in before:
                    fp = os.path.join(install_path, *rel.split("/"))
                    try:
                        st2 = os.stat(fp)
                        out.append((rel, st2.st_mtime_ns, st2.st_size))
                    except OSError:
                        out.append((rel, 0, 0))
                return tuple(out)

            waited = 0.0
            quiet = 0
            last_seen = None
            budget = max(30, int(timeout_sec))
            while True:
                done_set, _pending = await asyncio.wait({comm}, timeout=2)
                if comm in done_set:
                    output, _ = comm.result()
                    rc = proc.returncode
                    break
                waited += 2
                # Changed is NOT finished. A file changes the moment writing
                # STARTS, and killing on that wrote a 15MB exe half way
                # through: right size, wrong contents, and a game that hung
                # on the Steam spinner. Michael, correctly: "you have just
                # broken the modding tools somehow."
                #
                # So wait for the files to go QUIET - unchanged across
                # several polls after having changed - before deciding the
                # tool is done with them.
                if before and _changed_now():
                    fingerprint = _fingerprint_now()
                    if fingerprint == last_seen:
                        quiet += 2
                    else:
                        quiet = 0
                        last_seen = fingerprint
                    if quiet < _TOOL_QUIET_SECONDS:
                        continue
                    decky.logger.info(
                        f"prefix tool {game_domain}/{mod_id}: files changed "
                        f"and have been still for {quiet}s after "
                        f"{int(waited)}s - closing the tool rather than "
                        "waiting out the timeout"
                    )
                    _kill_tree()
                    try:
                        output, _ = await asyncio.wait_for(comm, timeout=15)
                    except (asyncio.TimeoutError, OSError):
                        output = b""
                    rc = 0
                    break
                if waited >= budget:
                    timed_out = True
                    _kill_tree()
                    try:
                        await asyncio.wait_for(comm, timeout=15)
                    except (asyncio.TimeoutError, OSError):
                        pass
                    break
        except OSError as e:
            _unstage()
            await _emit_progress(mod_id, "error", 0, str(e))
            return {"ok": False, "error": f"Could not run the tool: {e}"}
        finally:
            _unstage()

        changed = []
        for rel, snap in before.items():
            p = os.path.join(install_path, *rel.split("/"))
            try:
                st = os.stat(p)
                now = (st.st_mtime_ns, st.st_size)
            except OSError:
                now = None
            if now != snap:
                changed.append(rel)
        ok = bool(changed) if verify_changed else (rc == 0 and not timed_out)
        tail = output.decode(errors="replace")[-800:]
        decky.logger.info(
            f"prefix tool {game_domain}/{mod_id}: ok={ok} rc={rc} "
            f"timed_out={timed_out} changed={changed} tail={tail[-200:]!r}"
        )
        if ok:
            settings = _load_settings()
            done = settings.setdefault("prefix_tools", {}).setdefault(
                game_domain, {}
            )
            done[str(mod_id)] = {"at": int(time.time()), "changed": changed}
            settings.setdefault("prefix_tool_last", {}).setdefault(
                game_domain, {}
            ).pop(str(mod_id), None)
            _save_settings(settings)
            await _emit_progress(mod_id, "done", 100)
        else:
            await _emit_progress(mod_id, "error", 0, "tool failed")
        return {
            "ok": ok,
            "changed": changed,
            "timed_out": timed_out,
            "rc": rc,
            "output": tail,
        }

    # ---- me3 (FromSoft mod loader) ----------------------------------
    # me3 is a NATIVE Linux binary: it launches the game through its own
    # Proton prefix by running the real exe (Game/eldenring.exe) instead
    # of start_protected_game.exe, so EasyAntiCheat is never bootstrapped
    # and nothing in the game folder is touched. We keep our own copy
    # under the plugin's data dir - no root, no XDG pollution, and
    # uninstalling the plugin takes it with us.

    async def get_me3_status(self) -> dict:
        """Is our me3 copy present, does it run, and what version?

        The version is cosmetic; 'does it run at all' is not - a binary
        that unpacked but can't execute (wrong arch, noexec mount) would
        otherwise only surface as the game failing to start.
        """
        if not os.path.isfile(ME3_BIN):
            return {"ok": True, "installed": False}

        async def me3(*args):
            proc = await asyncio.create_subprocess_exec(
                ME3_BIN, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=_host_env(),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
            return out.decode(errors="replace")

        try:
            text = await me3("--version")
            version = re.search(r"(\d+\.\d+\.\d+)", text)
            if not version:
                # Older builds only print the version inside `info`.
                text = await me3("info")
                version = re.search(r"(\d+\.\d+\.\d+)", text)
        except (OSError, asyncio.TimeoutError) as e:
            return {"ok": True, "installed": True, "error": str(e)}
        return {
            "ok": True,
            "installed": True,
            "version": version.group(1) if version else "",
            "info": text[:400],
        }

    async def install_me3(self) -> dict:
        """Fetch the portable Linux tarball and unpack our own copy.
        GitHub-only distribution - there is no Nexus page for me3, so no
        author credit is being bypassed here."""
        os.makedirs(ME3_ROOT, exist_ok=True)
        tarball = os.path.join(ME3_ROOT, "me3-linux-amd64.tar.gz")
        try:
            timeout = aiohttp.ClientTimeout(total=600, sock_connect=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    ME3_RELEASE_URL, headers=APP_HEADERS, ssl=SSL_CONTEXT,
                    allow_redirects=True,
                ) as resp:
                    if resp.status != 200:
                        return {
                            "ok": False,
                            "error": f"me3 download failed (HTTP {resp.status})",
                        }
                    with open(tarball, "wb") as out:
                        async for chunk in resp.content.iter_chunked(1 << 20):
                            out.write(chunk)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return {"ok": False, "error": f"me3 download failed: {type(e).__name__}"}
        err = await _extract_archive(tarball, ME3_ROOT)
        try:
            os.remove(tarball)
        except OSError:
            pass
        if err:
            return {"ok": False, "error": f"me3 unpack failed: {err}"}
        # The tarball nests everything under a versioned dir on some
        # releases - flatten so bin/me3 is always where we expect.
        if not os.path.isfile(ME3_BIN):
            for entry in sorted(os.listdir(ME3_ROOT)):
                nested = os.path.join(ME3_ROOT, entry)
                if not os.path.isfile(os.path.join(nested, "bin", "me3")):
                    continue
                for item in os.listdir(nested):
                    # Our profiles and mods live in here too. A release
                    # that ever shipped a same-named entry must not take
                    # every installed mod with it.
                    if item in ("profiles",):
                        continue
                    dest = os.path.join(ME3_ROOT, item)
                    _force_rmtree(dest)
                    shutil.move(os.path.join(nested, item), dest)
                _force_rmtree(nested)
                break
        if not os.path.isfile(ME3_BIN):
            return {"ok": False, "error": "me3 binary missing after unpack"}
        os.chmod(ME3_BIN, 0o755)
        status = await self.get_me3_status()
        decky.logger.info(f"me3 installed: {status.get('version') or '?'}")
        if status.get("error"):
            # The binary is on disk but won't run - report it now rather
            # than let the launch command fail silently at game start.
            return {
                "ok": False,
                "error": f"me3 unpacked but won't run: {status['error']}",
            }
        return {"ok": True, "version": status.get("version", "")}

    async def get_me3_state(
        self, game_domain: str, install_dir: str, app_id: int = 0
    ) -> dict:
        """Everything the FromSoft panel needs in one call: whether our
        me3 copy is there, whether a Proton it can use is installed, and
        what the generated profile currently activates."""
        status = await self.get_me3_status()
        install_path = os.path.join(STEAM_COMMON, install_dir)
        protons = []
        if os.path.isdir(STEAM_COMMON):
            protons = sorted(
                name
                for name in os.listdir(STEAM_COMMON)
                if name.lower().startswith("proton")
                and os.path.isdir(os.path.join(STEAM_COMMON, name))
            )
        settings = _load_settings()
        records = _me3_records(settings, game_domain)
        profile = _me3_profile_path(game_domain)
        return {
            "ok": True,
            "installed": bool(status.get("installed")),
            "version": status.get("version", ""),
            "error": status.get("error", ""),
            "game_installed": os.path.isdir(install_path),
            "protons": protons,
            # me3 falls back to a per-game verified runtime when Steam has
            # no compat tool mapped for the app; for Elden Ring that is
            # Proton 8.0, so its absence is worth flagging up front.
            "proton8": any(p.lower().startswith("proton 8") for p in protons),
            "compat_tool": _steam_compat_tool(app_id) if app_id else "",
            "profile_path": profile,
            "profile_exists": os.path.isfile(profile),
            "mods": len(records),
            "natives": sum(len(r.get("natives") or []) for _k, r in records),
            "regulation_owner": _me3_regulation_owner(settings, game_domain),
            "coop_installed": bool(_me3_coop_ini(settings, game_domain)),
        }

    async def get_me3_launch_command(self, game_domain: str) -> dict:
        """The Steam launch command that boots the game through me3.

        %command% is accepted and discarded: Steam requires it to know
        the string is a wrapper, but the whole point is to run the real
        exe through me3 instead of Steam's anti-cheat launcher. Going
        through Steam's launch options (rather than running me3 from a
        terminal) is what keeps Steam Input and the overlay attached."""
        if game_domain not in ME3_GAMES:
            return {"ok": False, "error": f"{game_domain} is not an me3 game"}
        settings = _load_settings()
        profile = _write_me3_profile(game_domain, settings)
        parts = [ME3_BIN, ME3_WIN64, profile]
        if any("'" in p for p in parts):
            return {"ok": False, "error": "Plugin path contains a quote"}
        command = (
            "bash -c 'exec \"{bin}\" --windows-binaries-dir \"{win}\" "
            "launch -p \"{profile}\"' -- %command%"
        ).format(bin=ME3_BIN, win=ME3_WIN64, profile=profile)
        return {"ok": True, "command": command, "profile_path": profile}

    async def get_me3_coop_password(self, game_domain: str) -> dict:
        """Seamless Co-op matches players by a shared password kept in
        ersc_settings.ini next to the dll - surfacing it in the QAM saves
        a Desktop Mode trip to type it in a text editor."""
        path = _me3_coop_ini(_load_settings(), game_domain)
        if not path:
            return {"ok": True, "installed": False, "password": ""}
        values = _read_ini_settings(path, "PASSWORD", ["cooppassword"])
        return {
            "ok": True,
            "installed": True,
            "password": values.get("cooppassword", ""),
        }

    async def set_me3_coop_password(
        self, game_domain: str, password: str
    ) -> dict:
        path = _me3_coop_ini(_load_settings(), game_domain)
        if not path:
            return {"ok": False, "error": "Seamless Co-op is not installed"}
        value = (password or "").strip()
        if len(value) > 100 or "\n" in value or "\r" in value:
            return {"ok": False, "error": "That password is not usable"}
        try:
            _patch_ini_settings(path, "PASSWORD", {"cooppassword": value})
        except OSError as e:
            return {"ok": False, "error": str(e)}
        decky.logger.info(f"seamless co-op password updated for {game_domain}")
        return {"ok": True}

    async def get_prefix_tools_state(self, game_domain: str) -> dict:
        settings = _load_settings()
        done = settings.get("prefix_tools", {}).get(game_domain, {})
        last = settings.get("prefix_tool_last", {}).get(game_domain, {})
        skipped = settings.get("prefix_tools_skipped", {}).get(game_domain, {})
        return {
            "ok": True,
            "done": {int(k): True for k in done},
            "last": {int(k): v for k, v in last.items()},
            "skipped": {int(k): True for k in skipped},
        }

    async def skip_prefix_tools(
        self, game_domain: str, mod_ids: list, skipped: bool = True
    ) -> dict:
        """Mark tools as deliberately skipped (or un-skip them). A step
        the user can never complete must not nag forever - but the mods
        that depend on the tool genuinely won't work, so the UI says so
        rather than pretending the work is done."""
        settings = _load_settings()
        store = settings.setdefault("prefix_tools_skipped", {}).setdefault(
            game_domain, {}
        )
        for mod_id in mod_ids or []:
            if skipped:
                store[str(int(mod_id))] = {"at": int(time.time())}
            else:
                store.pop(str(int(mod_id)), None)
        _save_settings(settings)
        decky.logger.info(
            f"prefix tools {game_domain}: skipped={skipped} for {mod_ids}"
        )
        return {"ok": True}

    async def fix_prefix_runtime(self, app_id: int) -> dict:
        """Bring the game prefix's VC++ runtime up to the newest one any
        installed Proton bundles. Idempotent: reports updated=False when
        the prefix is already current. See the CRT_DLLS block comment for
        why Cyberpunk prefixes ship a too-old runtime."""
        sys32 = _prefix_system32(app_id)
        if not os.path.isdir(sys32):
            return {
                "ok": False,
                "error": "No Proton prefix for this game yet - "
                "launch the game once first",
            }
        have = _pe_file_version(os.path.join(sys32, "msvcp140.dll"))
        src_dir, src_ver = _newest_proton_crt_dir()
        if not src_dir:
            return {"ok": False, "error": "No Proton runtime found on this device"}
        if have and have >= src_ver:
            return {
                "ok": True,
                "updated": False,
                "version": ".".join(map(str, have)),
            }
        copied = 0
        for name in CRT_DLLS:
            src = os.path.join(src_dir, name)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(sys32, name)
            backup = dst + CRT_BACKUP_SUFFIX
            try:
                if os.path.isfile(dst) and not os.path.isfile(backup):
                    shutil.copy2(dst, backup)
                shutil.copy2(src, dst)
                os.chmod(dst, 0o644)
                copied += 1
            except OSError as e:
                return {"ok": False, "error": f"Could not update {name}: {e}"}
        decky.logger.info(
            f"prefix {app_id}: VC++ runtime {have} -> {src_ver} ({copied} DLLs)"
        )
        return {
            "ok": True,
            "updated": True,
            "version": ".".join(map(str, src_ver)),
            "previous": ".".join(map(str, have)) if have else None,
        }

    async def get_prefix_runtime_state(self, app_id: int) -> dict:
        """Read-only twin of fix_prefix_runtime, so the panel can OFFER
        the fix instead of only doing it during first-time setup.

        A game whose setup finished long ago still has the prefix its
        Steam install script gave it: Skyrim's ships VC++ 14.0 (2017),
        and 37 of its SKSE plugins - every one that links the runtime
        dynamically rather than statically - failed to load against it
        (device, 2026-08-08). Nothing in the game says why; SKSE just
        logs "fatal error occurred while loading plugin".
        """
        sys32 = _prefix_system32(app_id)
        if not os.path.isdir(sys32):
            return {"ok": True, "prefix_exists": False}
        have = _pe_file_version(os.path.join(sys32, "msvcp140.dll"))
        _src_dir, src_ver = _newest_proton_crt_dir()
        # Plugins built against VS2019+ import vcruntime140_1.dll, which
        # the 2017 redist does not ship at all - its absence is the same
        # fault as an old version and has to count as outdated.
        missing_140_1 = not _pe_file_version(
            os.path.join(sys32, "vcruntime140_1.dll")
        )
        return {
            "ok": True,
            "prefix_exists": True,
            "have": ".".join(map(str, have)) if have else "",
            "newest": ".".join(map(str, src_ver)) if src_ver else "",
            "outdated": bool(
                src_ver and (not have or have < src_ver or missing_140_1)
            ),
        }

    async def _install_framework_inner(
        self,
        game_domain: str,
        mod_id: int,
        install_dir: str,
        install_kind: str,
        detect_file: str,
        avoid_file_keywords: list,
        install_subdir: str,
    ) -> dict:
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
                async with session.get(_safe_uri(uri), ssl=SSL_CONTEXT) as resp:
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
                        _makedirs_for(dst)
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

    async def check_plugin_masters(
        self,
        install_dir: str,
        mods_subdir: str,
        app_id: int,
        plugins_subpath: str,
        plugins_style: str = "starred",
    ) -> dict:
        """Which ENABLED plugins reference master files that aren't in the
        data folder? The engine hard-fails at boot on a missing master
        ('X.esm is missing required files') - the #1 "game won't start"
        cause after a collection that assumes DLC or external
        prerequisites (e.g. TTW). Returns [{plugin, missing:[...]}]."""
        data_dir = os.path.join(STEAM_COMMON, install_dir, mods_subdir)
        if not os.path.isdir(data_dir):
            return {"ok": False, "error": "Game data folder not found"}
        if not plugins_subpath:
            return {"ok": True, "broken": []}
        enabled = _enabled_plugins(
            _plugins_txt_path(app_id, plugins_subpath), plugins_style
        )

        def scan():
            real = {n.lower(): n for n in os.listdir(data_dir)}
            broken = []
            for name in enabled:
                actual = real.get(name.lower())
                if not actual:
                    continue
                masters = _plugin_masters(os.path.join(data_dir, actual))
                if not masters:
                    continue
                missing = [m for m in masters if m.lower() not in real]
                if missing:
                    broken.append({"plugin": actual, "missing": missing})
            return broken

        broken = await asyncio.to_thread(scan)
        if broken:
            decky.logger.warning(
                f"{install_dir}: {len(broken)} enabled plugin(s) have "
                f"missing masters, e.g. {broken[0]}"
            )
        return {"ok": True, "broken": broken}

    async def get_script_extender_state(
        self, app_id: int, install_dir: str, log_subpath: str
    ) -> dict:
        """Which script-extender DLL plugins failed to load last launch.

        Some mods are simply built for an older game than the one Steam
        ships, and no amount of installing fixes that - SKSE stops the
        game with a modal asking whether to continue, which is a dead end
        on a handheld and reads as "your setup is broken" when one mod of
        two thousand is stale. Reading the extender's own log lets the
        panel offer to set those aside.
        """
        if not log_subpath:
            return {"ok": True, "available": False}
        log_path = _game_prefs_path(app_id, log_subpath)
        plugins_dir = os.path.join(
            STEAM_COMMON, install_dir, *SE_PLUGIN_DIRS.get(
                log_subpath.split("/")[0], ("Data", "SKSE", "Plugins")
            )
        )
        parked, live_names = [], {}
        if os.path.isdir(plugins_dir):
            for n in os.listdir(plugins_dir):
                if n.endswith(SE_DISABLED_SUFFIX):
                    parked.append(n[: -len(SE_DISABLED_SUFFIX)])
                elif n.lower().endswith(".dll"):
                    live_names[n.lower()] = n
            parked.sort()
        parked_lower = {p.lower() for p in parked}
        se_log_at = os.path.getmtime(log_path) if os.path.isfile(log_path) else 0.0
        crash = self._crash_culprits(log_path, live_names, parked_lower, se_log_at)
        if not se_log_at:
            return {
                "ok": True,
                "available": False,
                "parked": parked,
                "plugins_dir": plugins_dir,
                "crash": crash,
            }
        failed = _parse_script_extender_log(log_path)
        # A plugin already set aside cannot have failed this run; its
        # entry is just the last log that still mentions it.
        failed = [f for f in failed if f["name"].lower() not in parked_lower]
        return {
            "ok": True,
            "available": True,
            "failed": failed,
            "parked": parked,
            "plugins_dir": plugins_dir,
            "crash": crash,
            "log_at": int(se_log_at),
        }

    @staticmethod
    def _crash_culprits(
        log_path: str, live_names: dict, parked_lower: set, se_log_at: float
    ) -> dict:
        """Mod DLLs that were on the stack when the game last crashed.

        Only plugins sitting in the extender's own folder are offered,
        which is both the honest limit of what we can act on and a
        precise filter - Windows and Proton DLLs live elsewhere, so no
        list of names to ignore is needed.
        """
        se_dir = os.path.dirname(log_path)
        newest = _newest_crash_log((se_dir, os.path.join(se_dir, "Crashlogs")))
        if not newest:
            return {}
        crash_at = os.path.getmtime(newest)
        # The extender rewrites its log at every launch, so a newer one
        # means the game has started since - the crash is history.
        if se_log_at and se_log_at > crash_at:
            return {}
        parsed = _parse_crash_log(newest)
        culprits = []
        for fr in parsed.get("frames") or []:
            actual = live_names.get(fr["module"].lower())
            if not actual or actual.lower() in parked_lower:
                continue
            culprits.append(
                {"name": actual, "frame": fr["index"], "probable": fr["probable"]}
            )
        if not culprits:
            return {}
        # Nearest the crash first: frame 0 is where it died, and a
        # probable frame is real evidence where a stack scan is a guess.
        culprits.sort(key=lambda c: (not c["probable"], c["frame"]))
        return {
            "culprits": culprits,
            "crashed_at": parsed.get("crashed_at") or "",
            "log": os.path.basename(newest),
        }

    async def set_script_extender_plugins(
        self, install_dir: str, plugins_dir: str, names: list, enabled: bool
    ) -> dict:
        """Park a DLL plugin (or bring one back) by renaming it. The file
        is never deleted - the extender only scans *.dll, so a suffix is
        enough to take it out of the game, and the user can always have
        it back."""
        base = os.path.abspath(os.path.join(STEAM_COMMON, install_dir))
        target_dir = os.path.abspath(plugins_dir or "")
        # The directory comes from the frontend; never touch anything
        # outside the game it names.
        if not target_dir.startswith(base + os.sep):
            return {"ok": False, "error": "Refusing to touch that folder"}
        if not os.path.isdir(target_dir):
            return {"ok": False, "error": "No script-extender plugins folder"}
        changed, errors = [], []
        for name in names or []:
            clean = os.path.basename(str(name))
            if not clean or not clean.lower().endswith(".dll"):
                continue
            live = os.path.join(target_dir, clean)
            parked = live + SE_DISABLED_SUFFIX
            src, dst = (parked, live) if enabled else (live, parked)
            if not os.path.isfile(src):
                continue
            try:
                os.replace(src, dst)
                changed.append(clean)
            except OSError as e:
                errors.append(f"{clean}: {e}")
        decky.logger.info(
            f"{'restored' if enabled else 'parked'} {len(changed)} "
            f"script-extender plugin(s) in {target_dir}"
        )
        return {"ok": True, "changed": len(changed), "errors": errors}

    async def disable_plugins(
        self,
        app_id: int,
        plugins_subpath: str,
        plugins_style: str,
        plugin_names: list,
    ) -> dict:
        """Deactivate plugins by dropping their plugins.txt lines (files
        stay in the data folder). Used to make a game bootable again when
        enabled plugins have missing masters."""
        path = _plugins_txt_path(app_id, plugins_subpath)
        targets = {str(n).lower() for n in plugin_names or []}
        if not targets:
            return {"ok": True, "disabled": 0}
        keep, removed = [], 0
        for line in _read_plugins_txt(path):
            bare = line.lstrip("*").strip().lower()
            if bare in targets and not line.strip().startswith("#"):
                removed += 1
                continue
            keep.append(line)
        _write_plugins_txt(path, keep)
        decky.logger.info(
            f"disabled {removed} plugin(s) in {plugins_subpath} "
            f"(missing-master cleanup)"
        )
        return {"ok": True, "disabled": removed}

    async def check_game_file(self, install_dir: str, rel_path: str) -> dict:
        """Does a file exist inside a game's install dir? Used to detect
        native-Linux builds (e.g. UnityPlayer.so) that mod loaders can't
        hook."""
        if not _safe_rel_path(rel_path or ""):
            return {"ok": False, "error": "Invalid path"}
        path = os.path.join(STEAM_COMMON, install_dir, *rel_path.split("/"))
        return {"ok": True, "exists": os.path.exists(path)}

    async def get_show_adult(self) -> dict:
        gate = _load_settings().get("content_gate") or {}
        return {
            "ok": True,
            "show_adult": _show_adult(),
            "adult_pref": bool(gate.get("adult_pref")),
            "age_verified": bool(gate.get("age_verified")),
            "blur_adult": bool(gate.get("blur_images")),
        }

    async def set_show_adult(self, value: bool) -> dict:
        # See _show_adult: the gate is account-driven (site preference +
        # platform age verification) - there is deliberately no local
        # override in either direction.
        return {
            "ok": False,
            "error": "Adult content follows your Nexus Mods account settings "
            "and age verification - change it on nexusmods.com",
        }

    async def refresh_content_gate(self) -> dict:
        """Re-read the account's adult preference + age-verification status.
        Called by the QAM on mount and after sign-in. Errors leave the
        cached gate untouched (fail closed only if nothing was cached)."""
        api_key = _load_settings().get("api_key")
        if not api_key:
            settings = _load_settings()
            if settings.pop("content_gate", None) is not None:
                _save_settings(settings)
            return {"ok": False, "error": "Not signed in"}
        try:
            gate = await _refresh_content_gate(api_key)
        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            decky.logger.warning(f"Content gate refresh failed: {e}")
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "show_adult": bool(gate["adult_pref"]) and bool(gate["age_verified"]),
            "adult_pref": gate["adult_pref"],
            "age_verified": gate["age_verified"],
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

    async def get_framework_setup(
        self, game_domain: str, expected: str = ""
    ) -> dict:
        """Whether the launch command has been set, and whether what was
        set is still what we would set today.

        The flag used to mean only "we did this once", so a changed
        template could never be applied: Fallout 3's launch command grew a
        FOSE branch, and the step showed "Launch fix applied" with no
        button to press. Michael: "I cant press step 1 again."

        `expected` is what the game's template produces now. When it
        differs from what was written, the step offers itself again -
        which is the difference between a tick that means "done" and one
        that means "done, once, to a value nobody remembers".
        """
        state = _load_settings().get("framework_setup", {}).get(game_domain, {})
        launch_set = bool(state.get("launch_options_set"))
        stored = state.get("launch_options_value") or ""
        # No expectation asked for, or nothing recorded from before this
        # existed: fall back to the old meaning rather than nagging every
        # user who set theirs up months ago.
        current = True
        if launch_set and expected and stored:
            current = stored.strip() == expected.strip()
        return {
            "ok": True,
            "launch_options_set": launch_set,
            "launch_options_current": current,
            "launch_options_value": stored,
            "enabled": bool(state.get("enabled", launch_set)),
        }

    async def mark_launch_options_set(
        self, game_domain: str, options: str = ""
    ) -> dict:
        """Record that the launch command was set, and WHAT was set.

        The value is the point: without it "applied ✓" cannot be checked
        against a template that has since changed, and the step becomes a
        tick nobody can undo.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        settings = _load_settings()
        settings.setdefault("framework_setup", {})[game_domain] = {
            "launch_options_set": True,
            "launch_options_value": options or "",
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
        if ok:
            settings = _load_settings()
            state = settings.setdefault("framework_setup", {}).setdefault(
                game_domain, {}
            )
            state["launch_options_value"] = options or ""
            _save_settings(settings)
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
        witcher_layout: bool = False,
        framework_mod_folders: list = None,
        restore_on_reset: list = None,
        mod_write_dirs: list = None,
    ) -> dict:
        """One-button return to vanilla: uninstall every tracked mod (all
        record modes), remove framework loader files by prefix (copyRoot
        installs keep no manifest), delete the plugins file (the game
        regenerates it), clear this game's plugin state, and clear the
        launch command (dlo's replayed profile here; non-dlo devices get
        use_steam_client back and the frontend clears Steam's field).
        Files installed outside this plugin are not touched - EXCEPT on
        witcher-layout games, where the mods dir is 100% mod-owned and
        crashed installs can strand unrecorded folders that break script
        compilation (bricked a device boot): there the whole mods dir,
        every mod*.xml menu file, and their filelist lines are swept."""
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
        restored = []
        root_leftovers = []
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
                elif mode == "me3":
                    if _remove_me3_record(game_domain, key, settings):
                        removed += 1
                elif game_domain in ME3_GAMES:
                    # An me3 game's records are never game-dir-relative.
                    # The folder fallback below would resolve this one
                    # INSIDE the game install and delete there, which is
                    # the one thing this tier promises never to do.
                    decky.logger.warning(
                        f"skipping unrecognized {mode!r} record {key!r} on "
                        f"{game_domain} - me3 games own nothing in the game dir"
                    )
                    settings.get("installed", {}).get(game_domain, {}).pop(
                        key, None
                    )
                else:
                    target = rec.get("target")
                    folder = rec.get("folder") or key
                    base = (
                        os.path.join(install_path, *target.split("/"))
                        if target
                        else mods_path
                    )
                    if (
                        target == "dlc"
                        and folder.lower() in W3_OFFICIAL_DLC
                    ):
                        decky.logger.info(
                            f"refusing to delete official DLC {folder!r}"
                        )
                    else:
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
            if not pl or pl.startswith("."):
                continue
            # A prefix with a slash is an EXACT relative path, not a prefix.
            #
            # Cyberpunk's five loaders install into bin/x64 and red4ext, and
            # this loop was top-level only - so none of them could be
            # declared and every reset left all five behind with Step 1 still
            # ticked. Matched exactly rather than by prefix on purpose: "bin"
            # as a prefix would delete the game.
            if "/" in pl or "\\" in pl:
                rel = str(prefix).replace("\\", "/")
                if not _safe_rel_path(rel):
                    continue
                target = os.path.join(install_path, *rel.split("/"))
                if not os.path.lexists(target):
                    continue
                try:
                    if os.path.isdir(target) and not os.path.islink(target):
                        _force_rmtree(target)
                    else:
                        os.remove(target)
                    framework_files.append(rel)
                except OSError as e:
                    errors.append(f"{rel}: {e}")
                continue
            for name in sorted(os.listdir(install_path)):
                p = os.path.join(install_path, name)
                if not name.lower().startswith(pl):
                    continue
                try:
                    # Loaders ship directories too (SMAPI's smapi-internal/):
                    # removing only files left the framework installed and
                    # its setup step still ticked after a "reset to vanilla".
                    if os.path.isdir(p) and not os.path.islink(p):
                        _force_rmtree(p)
                    else:
                        os.remove(p)
                    framework_files.append(name)
                except OSError as e:
                    errors.append(f"{name}: {e}")
        # Mods the framework bundles with itself (SMAPI's ConsoleCommands /
        # SaveBackup). They have no install record, so the loop above never
        # sees them - but leaving them behind isn't vanilla either.
        for folder in framework_mod_folders or []:
            fl = str(folder)
            if not fl or "/" in fl or "\\" in fl or fl.startswith("."):
                continue
            for base in (mods_path, disabled_path):
                p = os.path.join(base, fl)
                if os.path.isdir(p) and not os.path.islink(p):
                    try:
                        _force_rmtree(p)
                        framework_files.append(f"{mods_subdir}/{fl}")
                    except OSError as e:
                        errors.append(f"{fl}: {e}")
        if install_mode == "dataDir" and plugins_subpath and app_id:
            p = _plugins_txt_path(app_id, plugins_subpath)
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError as e:
                errors.append(f"plugins.txt: {e}")
        if witcher_layout:
            # Orphan-proof sweep: vanilla TW3 has no mods dir, and its
            # menu-XML set is a fixed whitelist - EVERY other xml is
            # mod-installed (they are NOT all mod-prefixed: the
            # naturaltorchlight/FastTravelPack class survived the old
            # prefix-based sweep and their dangling filelist lines
            # crashed the game at the menu).
            _force_rmtree(mods_path)
            pc_dir = os.path.join(install_path, *W3_MENU_DIR.split("/"))
            if os.path.isdir(pc_dir):
                for name in sorted(os.listdir(pc_dir)):
                    low = name.lower()
                    if low.endswith(W3_VANILLA_BACKUP_SUFFIX):
                        # Mod overwrote a vanilla menu XML - restore it.
                        base = os.path.join(
                            pc_dir, name[: -len(W3_VANILLA_BACKUP_SUFFIX)]
                        )
                        try:
                            if os.path.isfile(base):
                                os.remove(base)
                            os.rename(os.path.join(pc_dir, name), base)
                        except OSError as e:
                            errors.append(f"{name}: {e}")
                    elif (
                        low.endswith(".xml")
                        and low not in W3_VANILLA_MENU_XMLS
                    ):
                        try:
                            os.remove(os.path.join(pc_dir, name))
                        except OSError as e:
                            errors.append(f"{name}: {e}")
                        _w3_filelist_remove(pc_dir, name)
                # belt-and-braces: no filelist line may point at a
                # missing file
                _w3_prune_filelists(pc_dir)
        if game_domain in ME3_GAMES:
            # The whole me3 tree for this game is plugin-owned, so vanilla
            # means removing it outright - profile included, or a stale
            # one would still be named by the launch command.
            _force_rmtree(_me3_profile_dir(game_domain))
            # The loader itself is the FromSoft equivalent of SMAPI: leave
            # it and the setup step still reads "installed", which is what
            # made a working reset look like it had done nothing. It is
            # shared by all five games though, so it only goes with the
            # last one - resetting Elden Ring must not break a modded
            # Dark Souls III.
            if not any(
                settings.get("installed", {}).get(other)
                for other in ME3_GAMES
                if other != game_domain
            ):
                _force_rmtree(ME3_ROOT)
                framework_files.append("me3 (mod loader)")
        # Anything WE renamed is ours to undo. A parked script-extender
        # plugin keeps its file and loses its extension, so a reset that
        # ignores them leaves the panel offering to restore mods that no
        # longer exist - and a reinstall inherits skips from a setup that
        # is gone.
        unparked = 0
        se_dir = os.path.join(
            install_path,
            *SE_PLUGIN_DIRS.get(
                (plugins_subpath or "").split("/")[0],
                ("Data", "SKSE", "Plugins"),
            ),
        )
        if os.path.isdir(se_dir):
            for name in os.listdir(se_dir):
                if not name.endswith(SE_DISABLED_SUFFIX):
                    continue
                try:
                    os.remove(os.path.join(se_dir, name))
                    unparked += 1
                except OSError as e:
                    errors.append(f"{name}: {e}")
        # Check our own work. Reset removes what it has records for, and a
        # record is written after the files are copied - so an interrupted
        # install leaves files nothing knows about. On device that meant
        # "1543 mods removed, 0 errors" while 20GB and ~400 mods stayed
        # put, and the only thing that caught it was the main menu looking
        # wrong. Reporting a success we have not verified is worse than
        # reporting a partial failure.
        baseline = settings.get("vanilla_baseline", {}).get(game_domain) or []
        leftovers = []
        removed_leftovers = 0
        # A baseline describes one build of the game. Games gain files
        # afterwards - patches, and DLC for titles still being updated -
        # and every one of those looks identical to a mod leftover from
        # here. Deleting a game file is unrecoverable without a Steam
        # verify; leaving a mod's config file behind is untidy. Those are
        # not comparable, so when the build has moved we report and stop.
        baseline_build = (
            settings.get("baseline_build", {}).get(game_domain) or ""
        )
        now_build = _steam_build_id(app_id)
        game_changed = bool(baseline_build and now_build
                            and baseline_build != now_build)
        if game_changed:
            decky.logger.info(
                f"reset {game_domain!r}: game build changed "
                f"{baseline_build} -> {now_build} since the baseline, so "
                "untracked files are reported rather than swept"
            )
        if baseline and not game_changed and os.path.isdir(mods_path):
            try:
                leftovers = sorted(
                    set(os.listdir(mods_path)) - set(baseline)
                )
            except OSError:
                leftovers = []
            # Sweep what the records could never cover: config files, logs
            # and caches that mods WRITE while running, in folders that
            # only exist because of mods. Nothing installed them, so
            # nothing can uninstall them - a device reset left 16 behind
            # in Data/SKSE and Data/seasons, and a "vanilla" game with mod
            # config in it is not vanilla.
            #
            # The baseline is the guard: it was captured before the first
            # mod was ever installed, so anything outside it arrived with
            # modding. Without a baseline nothing is swept, because then
            # we would only be guessing at what the user started with.
            for name in list(leftovers):
                # Never the game's own content. The baseline predates any
                # DLC bought later, so without this the sweep deletes what
                # the user paid for - it did exactly that on device.
                if _game_owned_name(game_domain, name):
                    leftovers.remove(name)
                    continue
                p = os.path.join(mods_path, name)
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p)
                    else:
                        os.remove(p)
                    leftovers.remove(name)
                    removed_leftovers += 1
                except OSError as e:
                    errors.append(f"{name}: {e}")
        if baseline and game_changed and os.path.isdir(mods_path):
            try:
                leftovers = sorted(set(os.listdir(mods_path)) - set(baseline))
            except OSError:
                leftovers = []
        # Directories a game's mods write into that are NOT the mods folder.
        #
        # Cyberpunk mods land in five places - archive/pc/mod, r6/scripts,
        # r6/tweaks, red4ext/plugins, bin/x64/plugins - and reset only ever
        # looked at the first. Files there are normally removed by install
        # record, so an ordinary uninstall works; but a record lost to an
        # interrupted install or an older build leaves a file no reset can
        # ever find. Two such orphans in r6/scripts
        # (GeneralShadowsFixes.reds, QuickMelee Sandevistan Fix.reds) had
        # been failing redscript compilation for weeks - and one bad .reds
        # disables EVERY script mod, so the whole stack was dead with
        # nothing owning the cause.
        #
        # Swept on the same terms as the mods folder: only what the baseline
        # did not have and no record claims, and never when the game itself
        # has changed underneath the baseline.
        extra_leftovers = []
        base_extra = (
            _load_settings().get("vanilla_extra_baseline", {}).get(game_domain)
            or {}
        )
        owned_names = set()
        for rec in (records or {}).values():
            for f in rec.get("files") or []:
                owned_names.add(
                    os.path.basename(str(f).replace("\\", "/")).lower()
                )
        for rel in mod_write_dirs or []:
            if not _safe_rel_path(str(rel)):
                continue
            d = os.path.join(install_path, *str(rel).split("/"))
            if not os.path.isdir(d):
                continue
            # No baseline for this directory means we do not know what the
            # GAME put there, and everything in it would read as an orphan.
            # Skipping is the only safe answer: deleting r6/scripts because
            # nobody had recorded it would break the game outright.
            if str(rel) not in base_extra:
                continue
            known = {n.lower() for n in (base_extra.get(str(rel)) or [])}
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for n in names:
                if n.lower() in known or n.lower() in owned_names:
                    continue
                extra_leftovers.append(f"{rel}/{n}")
                if game_changed:
                    continue
                try:
                    t = os.path.join(d, n)
                    if os.path.isdir(t) and not os.path.islink(t):
                        _force_rmtree(t)
                    else:
                        os.remove(t)
                    removed_leftovers += 1
                except OSError as e:
                    errors.append(f"{rel}/{n}: {e}")
        if extra_leftovers:
            decky.logger.info(
                f"reset {game_domain!r}: {len(extra_leftovers)} unaccounted "
                f"entry(ies) outside the mods folder: "
                f"{', '.join(extra_leftovers[:6])}"
            )
        # Files a mod left BESIDE the game exe. Reported, never deleted:
        # the game's own files live here too, and the baseline may predate
        # a game update. Michael's Fallout 3 carried three mod DLLs through
        # several "clean" resets because nothing ever looked here.
        root_baseline = (
            _load_settings().get("vanilla_root_baseline", {}).get(game_domain)
        )
        if root_baseline:
            try:
                now = {
                    n for n in os.listdir(install_path)
                    if os.path.isfile(os.path.join(install_path, n))
                }
                root_leftovers = sorted(now - set(root_baseline))
            except OSError:
                root_leftovers = []
            if root_leftovers:
                decky.logger.warning(
                    f"reset {game_domain!r}: {len(root_leftovers)} file(s) "
                    f"in the game folder that vanilla did not have: "
                    f"{', '.join(root_leftovers[:8])}"
                )
        # Deliberately NOT "mod_verdicts". Reset means start the mods
        # clean, not forget what the game can run - and those two got
        # confused once already: Michael reset, reinstalled, and the game
        # died on the same mod for the third time because the only record
        # of it lived in a session log. A verdict is a fact about a game
        # build and a mod version, not about what happens to be installed.
        # "auto_fixed" IS cleared, unlike mod_verdicts above. The two look
        # similar and are opposites: a verdict is knowledge about a game
        # build and a mod version, true whatever is installed, while
        # auto_fixed is a list of things done to THIS install. Keeping it
        # across a reset made the health check contradict itself - "RitsuLib
        # sorted out already" directly above "LustTravel2 needs RitsuLib".
        # Undo anything a modding tool did to the GAME itself, using the
        # backup the tool made. Reset removes mods, and a rewritten game exe
        # is not a mod - so it used to survive, and "reset game modding"
        # came back with Step 3 still ticked and no way for the user to
        # redo it. Only restores from a backup the tool wrote itself.
        for pair in restore_on_reset or []:
            try:
                backup, original = pair[0], pair[1]
            except (TypeError, IndexError):
                continue
            if not (_safe_rel_path(backup) and _safe_rel_path(original)):
                continue
            src = os.path.join(install_path, *backup.split("/"))
            dst = os.path.join(install_path, *original.split("/"))
            if not os.path.isfile(src):
                continue
            try:
                shutil.copy2(src, dst)
                os.remove(src)
                restored.append(original)
                decky.logger.info(
                    f"reset restored {original!r} from {backup!r}"
                )
            except OSError as e:
                errors.append(f"{original}: {e}")
        for section in ("installed", "collections", "framework_setup",
                        "collection_attention", "w3_merges", "skipped",
                        "auto_fixed", "update_attempts",
                        # Cleared only because the game files those tools
                        # changed have just been put back above.
                        "prefix_tools", "prefix_tools_skipped"):
            settings.get(section, {}).pop(game_domain, None)
        # Re-take the baseline now, because THIS is the only moment we can
        # be sure what vanilla looks like.
        #
        # _record_vanilla_baseline writes once and never again, on the
        # theory that the first install is preceded by a clean folder. It
        # is not always: on device the New Vegas baseline was captured
        # with 30-odd mod files already in Data - TTWLods.esp, Titans of
        # The New West, mil.esp, uio - because the game had been modded
        # before this plugin ever saw it. A baseline holding mod files
        # PROTECTS those files from the sweep, which is the exact opposite
        # of what it is for.
        #
        # After a reset the folder is as close to vanilla as it will ever
        # be, and it now includes whatever DLC or patch content the game
        # has gained since. Stamped with the build so a later update is
        # still detectable.
        # Files parked by a disabled mod live outside the game folder, so
        # nothing above would ever find them.
        parked_root = os.path.join(
            decky.DECKY_PLUGIN_RUNTIME_DIR, "parked", game_domain
        )
        if os.path.isdir(parked_root):
            _force_rmtree(parked_root)
            decky.logger.info(f"reset {game_domain!r}: cleared parked files")
        if os.path.isdir(mods_path) and not errors:
            try:
                fresh = sorted(os.listdir(mods_path))
            except OSError:
                fresh = []
            if fresh:
                settings.setdefault("vanilla_baseline", {})[game_domain] = fresh
                # The other mod directories get their baseline at the same
                # moment, for the same reason. Recorded HERE rather than at
                # install time because install has no idea which directories
                # a game's mods can write into - and without a baseline the
                # sweep above refuses to touch them at all, so the first
                # reset teaches it and every later one can find orphans.
                for rel in mod_write_dirs or []:
                    if not _safe_rel_path(str(rel)):
                        continue
                    d = os.path.join(install_path, *str(rel).split("/"))
                    extra = settings.setdefault(
                        "vanilla_extra_baseline", {}
                    ).setdefault(game_domain, {})
                    if not os.path.exists(d):
                        # Same rule as the first-install recorder, and this
                        # is the path that actually runs for a device that
                        # has already been modded: a directory that is not
                        # there once the mods are gone is one the GAME does
                        # not have, so record that as an empty baseline
                        # rather than swallowing the error and leaving the
                        # directory permanently unsweepable.
                        extra[str(rel)] = []
                        continue
                    try:
                        extra[str(rel)] = sorted(os.listdir(d))
                    except OSError:
                        # Exists but unreadable - never claim it is empty.
                        pass
                build = _steam_build_id(app_id)
                if build:
                    settings.setdefault("baseline_build", {})[game_domain] = build
                decky.logger.info(
                    f"reset {game_domain!r}: re-took the vanilla baseline, "
                    f"{len(fresh)} entries at build {build or 'unknown'} "
                    f"(was {len(baseline)})"
                )
        _save_settings(settings)
        cleared_dlo = False
        if app_id and _dlo_present():
            ok, _prev = _dlo_set_original(_dlo_settings_path(), app_id, "")
            cleared_dlo = ok
        if leftovers:
            decky.logger.warning(
                f"reset {game_domain!r}: {len(leftovers)} file(s) remain that "
                f"no record covered, e.g. {leftovers[:5]}"
            )
        decky.logger.info(
            f"reset {game_domain!r}: {removed} mods removed, framework "
            f"files {framework_files}, {unparked} parked plugin(s) cleared, "
            f"dlo cleared={cleared_dlo}, {len(errors)} errors, "
            f"{removed_leftovers} unrecorded leftovers swept, "
            f"{len(leftovers)} still there"
        )
        return {
            "ok": True,
            "removed": removed,
            # Game files a modding tool had rewritten, put back from the
            # tool's own backup, so the setup steps become honest again.
            "restored": restored,
            "framework_files": framework_files,
            "cleared_dlo": cleared_dlo,
            "use_steam_client": bool(app_id) and not cleared_dlo,
            "errors": errors,
            # Files present that were not there before we started, and
            # that no record accounted for. Zero means the reset is
            # verified, not merely finished.
            "leftovers": len(leftovers),
            # The same question asked of the GAME folder, where script
            # extenders and audio DLLs live. Named rather than deleted -
            # the game's own files are in there too.
            "root_leftovers": root_leftovers[:12],
            # Unaccounted entries in the OTHER directories a game's mods
            # write into (r6/scripts, red4ext/plugins ...).
            "extra_leftovers": extra_leftovers[:12],
            "swept": removed_leftovers,
            # True when the game has been patched or gained DLC since the
            # baseline, so untracked files were listed rather than
            # deleted. The UI must not call that a failed reset.
            "game_changed": game_changed,
            "leftover_examples": leftovers[:8],
            "verified": bool(baseline),
        }

    async def uninstall_collection(
        self,
        game_domain: str,
        install_dir: str,
        mods_subdir: str,
        install_mode: str = "folder",
        app_id: int = 0,
        plugins_subpath: str = "",
        plugins_style: str = "starred",
        slug: str = "",
    ) -> dict:
        """Remove every mod THIS collection installed (records carrying
        its slug), plus its registry and attention entries. Mods shared
        with other sources (individual installs, other collections) keep
        their records and stay. slug '__earlier__' targets pre-v0.17
        collection records that predate slugs."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not slug:
            return {"ok": False, "error": "Missing collection slug"}
        settings = _load_settings()
        install_path, mods_path, disabled_path = _game_paths(
            install_dir, mods_subdir
        )
        records = dict(settings.get("installed", {}).get(game_domain, {}))

        def belongs(rec: dict) -> bool:
            if slug == "__earlier__":
                return rec.get("source") == "collection" and not rec.get(
                    "collection_slug"
                )
            return rec.get("collection_slug") == slug

        removed = 0
        errors = []
        for key, rec in sorted(records.items()):
            if not belongs(rec):
                continue
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
                elif mode == "me3":
                    if _remove_me3_record(game_domain, key, settings):
                        removed += 1
                else:
                    target = rec.get("target")
                    folder = rec.get("folder") or key
                    base = (
                        os.path.join(install_path, *target.split("/"))
                        if target
                        else mods_path
                    )
                    if (
                        target == "dlc"
                        and folder.lower() in W3_OFFICIAL_DLC
                    ):
                        decky.logger.info(
                            f"refusing to delete official DLC {folder!r}"
                        )
                    else:
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
                    _w3_unmerge(
                        game_domain, install_path, mods_path, folder,
                        settings,
                    )
                _w3_remove_menu_xmls(install_path, rec)
            except OSError as e:
                errors.append(f"{key}: {e}")
        settings.get("collections", {}).get(game_domain, {}).pop(slug, None)
        settings.get("collection_attention", {}).get(game_domain, {}).pop(
            slug, None
        )
        if game_domain in ME3_GAMES:
            _write_me3_profile(game_domain, settings)
        _save_settings(settings)
        # Witcher-class games: never leave a filelist line pointing at a
        # deleted menu XML (crashes the game at the menu).
        pc_dir = os.path.join(install_path, *W3_MENU_DIR.split("/"))
        if os.path.isdir(pc_dir):
            _w3_prune_filelists(pc_dir)
        decky.logger.info(
            f"uninstalled collection {slug!r} from {game_domain!r}: "
            f"{removed} mods removed, {len(errors)} errors"
        )
        return {"ok": True, "removed": removed, "errors": errors}

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
        mod_ids: list = None,
        only_if_known: bool = False,
    ) -> dict:
        """Remember a collection's display info + member mod ids. Records
        only carry the slug of the collection that INSTALLED them - the
        id list lets My Mods count mods shared with other collections or
        installed individually. only_if_known refreshes an existing entry
        (viewing a collection must not register it)."""
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not slug:
            return {"ok": False, "error": "Missing collection slug"}
        try:
            ids = [int(i) for i in (mod_ids or [])][:1000]
        except (TypeError, ValueError):
            ids = []
        settings = _load_settings()
        if only_if_known and slug not in settings.get("collections", {}).get(
            game_domain, {}
        ):
            return {"ok": True, "skipped": True}
        settings.setdefault("collections", {}).setdefault(game_domain, {})[
            slug
        ] = {
            "title": title or slug,
            "thumb_url": thumb_url or "",
            "mod_count": int(mod_count or 0),
            "mod_ids": ids,
            "at": int(time.time()),
        }
        _save_settings(settings)
        return {"ok": True}

    # ---- Installed mods / enable & disable ----------------------------------

    async def get_load_order_state(
        self, app_id: int, install_dir: str, plugins_subpath: str,
        plugins_style: str, game_domain: str = ""
    ) -> dict:
        """What is wrong with the load order: ordering, dependencies, or both.

        Skyrim and Fallout 4 read plugins.txt as the load order, and we
        only ever appended to it - so the order was the order things
        happened to be installed in. On the device's 1,960-plugin
        collection that put 557 plugins ahead of a master they depend on,
        which is a crash on the way into the world, not a nuance.

        FO3 and New Vegas order by file TIMESTAMP instead, so their file
        order means nothing and the violations count is not reported for
        them. The dependency half applies just as much though: the same
        Skyrim install had 13 masters sitting installed-but-off with 139
        plugins depending on them, and that fault has nothing to do with
        which dialect of plugins.txt a game speaks. Bailing out on the
        whole check because the ordering half is irrelevant left the two
        Gamebryo games with no dependency check at all.
        """
        if not plugins_subpath:
            return {"ok": True, "supported": False}
        timestamp_ordered = plugins_style == "listed"
        path = _plugins_txt_path(app_id, plugins_subpath)
        data_path = os.path.join(STEAM_COMMON, install_dir, "Data")
        if not os.path.isfile(path) or not os.path.isdir(data_path):
            return {"ok": True, "supported": False}
        implicit = IMPLICIT_MASTERS_BY_DOMAIN.get(game_domain, frozenset())
        skips = set(_load_skips(game_domain))
        entries = [(n, on) for n, on in
                   _plugin_entries(_read_plugins_txt(path), plugins_style)
                   if n.lower() not in implicit]
        enabled = [n for n, on in entries if on]
        needed = _masters_to_enable(data_path, entries, implicit, skips)
        absent = _missing_masters(data_path, enabled, implicit)
        ghosts = await asyncio.to_thread(_ghost_plugins, data_path, enabled)
        esl = game_domain in ESL_DOMAINS
        full, light = await asyncio.to_thread(
            _slot_usage, data_path, enabled, implicit, esl)
        return {
            "ok": True,
            "supported": True,
            "total": len(enabled),
            # Meaningless where the engine orders by file timestamp -
            # reporting a number the user cannot act on is worse than
            # reporting none.
            "violations": 0 if timestamp_ordered
            else _load_order_report(data_path, enabled),
            "timestamp_ordered": timestamp_ordered,
            "disabled_masters": len(needed),
            "examples": [n for n, _ in needed[:3]],
            # Masters that are not installed at all - usually DLC the
            # account does not own. Named for humans where we can.
            "missing_masters": [
                {
                    "name": m,
                    "label": DLC_MASTER_NAMES.get(m.lower(), ""),
                    "needed_by": len(deps),
                }
                for m, deps in absent[:12]
            ],
            "blocked_plugins": len({d for _m, deps in absent for d in deps}),
            # Enabled but not on disk. Safe to delist: they cannot load.
            "ghost_plugins": len(ghosts),
            "ghost_examples": ghosts[:3],
            "full_slots": full,
            "full_slot_limit": FULL_SLOT_LIMIT if esl else NO_ESL_SLOT_LIMIT,
            "light_slots": light,
            # 0 says "this engine has no light tier", which the panel reads
            # as "do not mention light slots at all".
            "light_slot_limit": LIGHT_SLOT_LIMIT if esl else 0,
        }

    async def disable_blocked_plugins(
        self, app_id: int, install_dir: str, plugins_subpath: str,
        plugins_style: str, game_domain: str
    ) -> dict:
        """Switch off mods that name a master which is not installed.

        Deliberately excludes DLC masters. "Turn off 115 mods" and "buy
        Dead Money for three pounds" are not equivalent answers, and a
        button that quietly picks the first would throw away most of a
        collection the user had every intention of running.

        What is left after DLC is the honest case for this: variant
        patches a collection ships for setups you may not have - a Project
        Nevada version of a tweak, a Tale of Two Wastelands LOD patch.
        They cannot load, the game refuses to start because of them, and
        no download here can help since TTW is a desktop conversion that
        is not even on Nexus.

        Recorded as skips with the reason, so a later load-order repair
        does not switch them back on and put the modal back.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not _safe_rel_path(plugins_subpath):
            return {"ok": False, "error": "Invalid plugins path"}
        path = _plugins_txt_path(app_id, plugins_subpath)
        _, data_path, _unused = _game_paths(install_dir, "Data")
        implicit = IMPLICIT_MASTERS_BY_DOMAIN.get(game_domain, frozenset())
        entries = _plugin_entries(_read_plugins_txt(path), plugins_style)
        enabled = [n for n, on in entries if on and n.lower() not in implicit]
        absent = await asyncio.to_thread(
            _missing_masters, data_path, enabled, implicit
        )
        blocked = {}
        for master, deps in absent:
            if master.lower() in DLC_MASTER_NAMES:
                continue
            for d in deps:
                blocked.setdefault(d, set()).add(master)
        if not blocked:
            return {"ok": True, "disabled": 0, "names": []}
        _set_plugins_active(path, list(blocked), False, plugins_style)
        skips = _load_skips(game_domain)
        for name, masters in blocked.items():
            skips[name.lower()] = {
                "reason": (
                    f"needs {', '.join(sorted(masters))}, which "
                    f"{'is' if len(masters) == 1 else 'are'} not installed"
                ),
                "root": False,
            }
        _save_skips(game_domain, skips)
        names = sorted(blocked)
        decky.logger.info(
            f"disabled {len(names)} plugin(s) blocked by absent masters: "
            f"{', '.join(names[:6])}"
        )
        return {"ok": True, "disabled": len(names), "names": names}

    async def get_file_conflicts(
        self, game_domain: str, mod_order: list = None
    ) -> dict:
        """Files where the wrong mod won, judged against collection order.

        `mod_order` is the collection's mod ids in list order - the
        curator's statement of who should overwrite whom. Without it there
        is no intent to compare against and nothing is reported, which is
        correct: mods installed individually have no agreed priority.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        # DISABLED until it reads modRules. A collection's list order is
        # NOT its statement of priority: this one ships 1,442 explicit
        # modRules ("before"/"after" between named files), and by those
        # rules the New Vegas HUD stack - oHUD, then Clean Vanilla Hud,
        # then the patch - was already in the RIGHT order, while list
        # order called it wrong. So the 782 files this reported as
        # misplaced were largely correct, and the fix it offered would
        # have rewritten them to the wrong owners.
        #
        # The detection machinery is sound; the intent it compares against
        # was wrong. Rebuilt on modRules, not deleted.
        if not CONFLICTS_USE_MOD_RULES:
            return {"ok": True, "conflicts": [], "files": 0, "pairs": 0,
                    "resolve": []}
        order = {
            int(m): i for i, m in enumerate(mod_order or []) if m is not None
        }
        if not order:
            return {"ok": True, "conflicts": [], "files": 0}
        settings = _load_settings()
        records = settings.get("installed", {}).get(game_domain, {})
        wrong = await asyncio.to_thread(
            _wrong_winners, records, order,
            _file_owner_overrides(game_domain, settings),
        )
        resolve = sorted(
            {m for g in wrong for m in g["mod_ids"] if m in order},
            key=lambda m: order[m],
        )
        decky.logger.info(
            f"file conflicts {game_domain}: {sum(g['files'] for g in wrong)} "
            f"file(s) won by the wrong mod across {len(wrong)} pair(s)"
        )
        return {
            "ok": True,
            "conflicts": [
                {
                    "actual": g["actual"],
                    "intended": g["intended"],
                    "files": g["files"],
                    "example": g["example"],
                }
                for g in wrong[:12]
            ],
            "files": sum(g["files"] for g in wrong),
            "pairs": len(wrong),
            # Every mod involved, in collection order: reinstalling them in
            # this sequence lands each one exactly where the curator put it.
            "resolve": resolve,
        }

    async def remove_ghost_plugins(
        self, app_id: int, install_dir: str, plugins_subpath: str,
        plugins_style: str, game_domain: str
    ) -> dict:
        """Delist plugins that are enabled but not installed.

        Always safe, which is what separates this from the other repairs:
        a plugin with no file cannot load whatever the list says, so
        removing the line changes nothing about what the game does - it
        only stops the tool counting and reporting something that is not
        there.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not _safe_rel_path(plugins_subpath):
            return {"ok": False, "error": "Invalid plugins path"}
        path = _plugins_txt_path(app_id, plugins_subpath)
        _, data_path, _unused = _game_paths(install_dir, "Data")
        entries = _plugin_entries(_read_plugins_txt(path), plugins_style)
        enabled = [n for n, on in entries if on]
        ghosts = await asyncio.to_thread(_ghost_plugins, data_path, enabled)
        if not ghosts:
            return {"ok": True, "removed": 0, "names": []}
        _remove_plugins(path, ghosts)
        decky.logger.info(
            f"delisted {len(ghosts)} plugin(s) that are not installed: "
            f"{', '.join(ghosts[:6])}"
        )
        return {"ok": True, "removed": len(ghosts), "names": ghosts}

    async def resolve_file_conflicts(
        self, game_domain: str, install_dir: str, mods_subdir: str,
        mod_order: list = None, files: list = None
    ) -> dict:
        """Rewrite each contested file from the mod the collection wanted
        to own it, and nothing else.

        This is the fix v0.97.0 got wrong. That version reinstalled whole
        mods in collection order, which rewrites every file they own -
        including files they were never contesting - so each reinstalled mod
        leapfrogged everything after it that had been left alone. The device
        went from 47 wrong pairs to 92, and doing it "properly" that way
        needs the closure over shared files: 352 of 852 mods, 41% of the
        collection.

        Per PATH instead. Each contested file is written exactly once, by
        its rightful owner, and every other file on disk is untouched - so
        no new conflict can be created, and order stops mattering because
        nothing is racing. One archive is fetched per owning mod rather than
        one per mod in a closure.

        `files` optionally narrows it to specific paths, so a user can fix
        the interface without re-fetching a texture pack.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        api_key = _load_settings().get("api_key")
        if not api_key:
            return {"ok": False, "error": "Not signed in"}
        order = {
            int(m): i for i, m in enumerate(mod_order or []) if m is not None
        }
        if not order:
            return {"ok": False, "error": "No collection order to work from"}
        settings = _load_settings()
        records = settings.get("installed", {}).get(game_domain, {})
        _install_path, data_path, _unused = _game_paths(install_dir, mods_subdir)
        wanted = {f.lower() for f in (files or [])}

        owners = await asyncio.to_thread(_file_owners, records)
        overrides = _file_owner_overrides(game_domain, settings)
        # path -> record key that should own it, grouped by that owner so
        # each archive is fetched once.
        by_owner = {}
        for path, keys in owners.items():
            keys = list(dict.fromkeys(keys))
            if len(keys) < 2:
                continue
            if wanted and path not in wanted:
                continue
            ranked = [
                (k, order.get(records[k].get("mod_id"), -1))
                for k in keys
            ]
            if any(pos < 0 for _k, pos in ranked):
                continue
            intended = max(ranked, key=lambda r: r[1])[0]
            if overrides.get(path) == intended:
                continue
            by_owner.setdefault(intended, []).append(path)

        if not by_owner:
            return {"ok": True, "rewritten": 0, "mods": 0, "errors": []}

        rewritten = 0
        errors = []
        settled = settings.setdefault("file_owner", {}).setdefault(
            game_domain, {}
        )
        for key in sorted(by_owner, key=lambda k: order[records[k]["mod_id"]]):
            rec = records[key]
            paths = by_owner[key]
            err, archive = await _download_archive(
                game_domain, int(rec["mod_id"]), int(rec["file_id"]),
                rec.get("file_name") or "", api_key,
            )
            if err:
                errors.append(f"{key}: {err}")
                continue
            scratch = _extract_scratch(
                int(rec["mod_id"]), int(rec["file_id"])
            ) + "-owner"
            _force_rmtree(scratch)
            os.makedirs(scratch, exist_ok=True)
            err = await _extract_archive(archive, scratch)
            if err:
                _force_rmtree(scratch)
                errors.append(f"{key}: {err}")
                continue
            # The archive's own layout is not the game's: find each wanted
            # path by its tail, wherever the author buried it.
            index = {}
            for root, _dirs, names in os.walk(scratch):
                for n in names:
                    full = os.path.join(root, n)
                    rel = os.path.relpath(full, scratch).replace(os.sep, "/")
                    index[rel.lower()] = full
            for path in paths:
                src = index.get(path)
                if not src:
                    src = next(
                        (
                            v
                            for k2, v in index.items()
                            if k2.endswith("/" + path)
                        ),
                        None,
                    )
                if not src:
                    errors.append(f"{key}: {path} not in the archive")
                    continue
                dst = os.path.join(data_path, *path.split("/"))
                try:
                    _makedirs_for(dst)
                    shutil.copy2(src, dst)
                except OSError as e:
                    errors.append(f"{key}: {path}: {e}")
                    continue
                settled[path] = key
                rewritten += 1
            _force_rmtree(scratch)
        _save_settings(settings)
        decky.logger.info(
            f"resolved {rewritten} contested file(s) across {len(by_owner)} "
            f"owning mod(s), {len(errors)} error(s)"
        )
        return {
            "ok": True,
            "rewritten": rewritten,
            "mods": len(by_owner),
            "errors": errors[:8],
        }

    async def apply_collection_plugins(
        self, slug: str, game_domain: str, install_dir: str,
        mods_subdir: str = "Data", app_id: int = 0,
        plugins_subpath: str = "", plugins_style: str = "starred",
    ) -> dict:
        """Enable exactly the plugins the collection's manifest lists.

        We activate every plugin found in every archive. A curator picks
        which ones should be ON, and a mod routinely ships several: LOD
        generation helpers meant for xEdit rather than play, an "(alt)"
        variant beside the normal one, per-DLC patches for DLC the setup
        may not have. The manifest states the answer; we were deriving our
        own and getting a different one.

        On device that was 21 plugins the collection never asked for -
        FNVLODGen, four separate LOD kits, a Dead Money "(alt)" patch
        beside the plain one - taking the load order to 256 against an
        engine limit of 254, so the game refused to start.

        Only touches plugins belonging to mods THIS collection installed: a
        mod the user added is their business. Implicit masters are never
        written either way. A deliberate skip stays skipped, because those
        are plugins whose masters are absent and the manifest cannot know
        what this particular setup is missing.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug or ""):
            return {"ok": False, "error": "Invalid collection slug"}
        if not _safe_rel_path(plugins_subpath):
            return {"ok": False, "error": "Invalid plugins path"}
        api_key = _load_settings().get("api_key")
        scratch = None
        try:
            scratch, manifest = await _fetch_collection_manifest(
                slug, game_domain, api_key
            )
            wanted = {
                (pl.get("name") or "").lower()
                for pl in manifest.get("plugins") or []
                if pl.get("enabled") and pl.get("name")
            }
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError,
                KeyError, ValueError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            if scratch:
                _force_rmtree(scratch)
        if not wanted:
            return {"ok": False, "error": "The manifest lists no plugins"}

        settings = _load_settings()
        records = settings.get("installed", {}).get(game_domain, {})
        ours = {
            pl.lower()
            for rec in records.values()
            if (rec.get("collection_slug") or "") == slug
            for pl in rec.get("plugins") or []
        }
        implicit = IMPLICIT_MASTERS_BY_DOMAIN.get(game_domain, frozenset())
        skips = set(_load_skips(game_domain))
        path = _plugins_txt_path(app_id, plugins_subpath)
        _install_path, data_path, _unused = _game_paths(
            install_dir, mods_subdir
        )
        enabled = _enabled_plugins(path, plugins_style)
        low = {n.lower(): n for n in enabled}

        turn_off = [
            low[n] for n in low
            if n not in wanted and n in ours and n not in implicit
        ]
        turn_on = [
            n for n in wanted
            if n not in low and n not in skips and n not in implicit
            and os.path.isfile(os.path.join(data_path, n))
        ]
        if turn_off:
            _set_plugins_active(path, turn_off, False, plugins_style)
        if turn_on:
            _add_plugins(path, turn_on, plugins_style, game_domain, data_path)
        if (turn_off or turn_on) and plugins_style == "listed":
            await asyncio.to_thread(
                _stagger_plugin_mtimes, data_path, path, plugins_style,
                game_domain,
            )
        limit = (
            FULL_SLOT_LIMIT if game_domain in ESL_DOMAINS
            else NO_ESL_SLOT_LIMIT
        )
        after = len(_enabled_plugins(path, plugins_style)) + len(implicit)
        decky.logger.info(
            f"collection plugins {slug!r}: switched off {len(turn_off)}, on "
            f"{len(turn_on)}; load order now {after} of {limit}"
        )
        return {
            "ok": True,
            "disabled": len(turn_off),
            "enabled": len(turn_on),
            "names_off": sorted(turn_off)[:12],
            "total": after,
            "limit": limit,
        }

    async def apply_known_prerequisites(
        self, game_domain: str, install_dir: str, mods_subdir: str = "Data",
        app_id: int = 0, plugins_subpath: str = "",
        plugins_style: str = "starred", slug: str = "",
    ) -> dict:
        """Switch off mods that need a file Nexus does not host.

        Console-first: a mod that cannot work is OFF, not installed and
        silently breaking the game. Nothing to read, nothing to tap.

        The three New Vegas interface mods here need Vanilla UI+, which is
        hosted on ModDB. Left on, the game reaches the main menu background
        and stops - no crash log, no error the user can act on. Off, the
        collection starts and the page says which mods are waiting and what
        they are waiting for. Anyone who fetches VUI+ by hand can turn them
        back on in one tap, and by definition they are already tinkering.

        Reversible: if the prerequisite IS present, anything parked for
        want of it is restored.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        table = MODS_NEEDING_EXTERNAL.get(game_domain) or {}
        if not table:
            return {"ok": True, "parked": 0, "restored": 0, "mods": []}
        _install_path, data_path, _unused = _game_paths(
            install_dir, mods_subdir
        )
        if not os.path.isdir(data_path):
            return {"ok": False, "error": f"{mods_subdir} not found"}
        try:
            on_disk = {f.lower() for f in os.listdir(data_path)}
        except OSError:
            on_disk = set()
        settings = _load_settings()
        records = settings.get("installed", {}).get(game_domain, {})
        parked, restored, waiting = 0, 0, []
        # Every mod this pass will switch off, so a file shared only within
        # the group is still movable.
        group = {
            key for key, rec in records.items()
            if rec.get("mod_id") in table and rec.get("mode") == "dataDir"
            and table[rec["mod_id"]]["needs_file"].lower() not in on_disk
        }
        for key, rec in records.items():
            entry = table.get(rec.get("mod_id"))
            if not entry or rec.get("mode") != "dataDir":
                continue
            have = entry["needs_file"].lower() in on_disk
            rels = [
                r for r in (rec.get("files") or [])
                if r.lower() not in _shared_paths(records, key, group)
            ]
            park_dir = _parked_files_dir(game_domain, key)
            if have:
                if rec.get("parked"):
                    restored += _move_mod_files(park_dir, data_path, rels)
                    _force_rmtree(park_dir)
                    rec.pop("parked", None)
                    rec.pop("needs_external", None)
                continue
            if not rec.get("parked"):
                moved = _move_mod_files(data_path, park_dir, rels)
                parked += moved
                # Marked off even when it moved nothing. Several mods in a
                # group can list the same file, and whichever reaches it
                # first takes it - so the others legitimately move zero and
                # were being left flagged as ON. On device that showed the
                # oHUD patch enabled while both its files sat in another
                # mod's park directory, which would have made "turn it back
                # on" a no-op.
                rec["parked"] = True
                rec["needs_external"] = entry["needs_name"]
            waiting.append({"mod": rec.get("name") or key,
                            "needs": entry["needs_name"]})
            for plugin in rec.get("plugins") or []:
                if plugins_subpath:
                    _set_plugins_active(
                        _plugins_txt_path(app_id, plugins_subpath),
                        [plugin], False, plugins_style,
                    )
        if waiting and slug and re.fullmatch(r"[A-Za-z0-9_-]+", slug):
            queue = settings.setdefault("collection_attention", {}).setdefault(
                game_domain, {}
            ).setdefault(slug, [])
            known = {
                (a.get("mod_name"), a.get("reason")) for a in queue
            }
            for w in waiting:
                if (w["mod"], "needs_external") in known:
                    continue
                queue.append({
                    "file_id": 0, "mod_id": 0, "mod_name": w["mod"],
                    "file_name": "", "version": "",
                    "reason": "needs_external", "options": [],
                    "detail": (
                        f"Switched off because it needs {w['needs']}, which "
                        "is not hosted on Nexus Mods. Get that mod and turn "
                        "this back on in My Mods."
                    ),
                })
        _save_settings(settings)
        if parked or restored:
            decky.logger.info(
                f"prerequisites {game_domain!r}: parked {parked} file(s) "
                f"across {len(waiting)} mod(s), restored {restored}"
            )
        return {
            "ok": True,
            "parked": parked,
            "restored": restored,
            "mods": [w["mod"] for w in waiting],
            "needs": sorted({w["needs"] for w in waiting}),
        }

    async def get_collection_support(
        self, game_domain: str, slug: str
    ) -> dict:
        """Whether we know this collection cannot work here, and why.

        Said before the download, not after. The TTW collection is 42 GB and
        needs a conversion that cannot be built in Gaming Mode at all, so
        finding out afterwards costs a day.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        entry = (UNSUPPORTED_COLLECTIONS.get(game_domain) or {}).get(slug or "")
        if not entry:
            return {"ok": True, "supported": True}
        return {
            "ok": True,
            "supported": False,
            "reason": entry["reason"],
            "title": entry.get("title") or "",
        }

    async def cancel_collection_install(
        self, game_domain: str, slug: str, install_dir: str,
        mods_subdir: str = "Data", app_id: int = 0,
        plugins_subpath: str = "", plugins_style: str = "starred",
        mod_ids: list = None,
    ) -> dict:
        """Abandon a collection: stop its downloads and remove what THIS
        collection installed.

        `mod_ids` is what the run actually installed, from the page. Only
        records carrying this collection's slug AND appearing in that list
        are removed - so a mod the user installed on their own before, or
        one that belongs to a different collection, is left alone even
        though the collection lists it too. Getting that wrong deletes
        somebody's existing setup, so the default is to remove nothing.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug or ""):
            return {"ok": False, "error": "Invalid collection slug"}
        wanted = {int(m) for m in (mod_ids or []) if m is not None}
        settings = _load_settings()
        records = settings.get("installed", {}).get(game_domain, {})
        removable = [
            key for key, rec in records.items()
            if (rec.get("collection_slug") or "") == slug
            and rec.get("mod_id") in wanted
        ]
        kept_other = sum(
            1 for rec in records.values()
            if rec.get("mod_id") in wanted
            and (rec.get("collection_slug") or "") != slug
        )
        removed, errors = 0, []
        for key in removable:
            try:
                result = await self.uninstall_mod(
                    game_domain, install_dir, mods_subdir, key,
                    "dataDir" if records[key].get("mode") == "dataDir"
                    else records[key].get("mode") or "folder",
                    app_id, plugins_subpath, plugins_style,
                )
            except Exception as e:  # noqa: BLE001 - report, keep going
                errors.append(f"{key}: {type(e).__name__}")
                continue
            if result.get("ok"):
                removed += 1
            else:
                errors.append(f"{key}: {result.get('error')}")
        settings = _load_settings()
        settings.get("collection_attention", {}).get(game_domain, {}).pop(
            slug, None
        )
        settings.get("collections", {}).get(game_domain, {}).pop(slug, None)
        _save_settings(settings)
        decky.logger.info(
            f"cancelled collection {slug!r}: removed {removed} mod(s), left "
            f"{kept_other} that were installed outside it, {len(errors)} errors"
        )
        return {
            "ok": True,
            "removed": removed,
            "kept": kept_other,
            "errors": errors[:8],
        }

    async def get_mod_support(self, game_domain: str, mod_id: int) -> dict:
        """Whether this mod needs something we cannot install, and what.

        Answered for a SINGLE mod so the warning reaches someone who found
        it by browsing, not only someone installing the collection it came
        from. The same three New Vegas interface mods that stop the game
        starting are one search away from any user.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        entry = (MODS_NEEDING_EXTERNAL.get(game_domain) or {}).get(int(mod_id))
        if not entry:
            return {"ok": True, "supported": True}
        return {
            "ok": True,
            "supported": False,
            "needs_name": entry["needs_name"],
            "url": entry.get("url") or "",
            "reason": (
                f"This mod needs {entry['needs_name']}, which is not hosted "
                "on Nexus Mods and cannot be downloaded here. Installed "
                "without it, the game will not start - so it is switched off "
                "until you add it yourself."
            ),
        }

    async def disable_failing_mods(
        self, game_domain: str, install_dir: str, mods_subdir: str,
        game_user_dir: str, install_mode: str = "folder", app_id: int = 0,
        plugins_subpath: str = "", plugins_style: str = "starred",
        protected_ids: list = None, dry_run: bool = False,
        auto_only: bool = False, exclude: list = None,
    ) -> dict:
        """Switch off the mods the last session blamed for errors.

        Slay the Spire 2, 2026-08-13: a collection produced 1,078
        MissingMethodExceptions and killed the game five seconds into the
        main menu. The game's own log names the mod each exception was
        thrown IN - so the outdated ones are knowable, and switching them
        off is a tap rather than a hunt.

        Only mods the log blames DIRECTLY. _parse_mod_load_log attributes an
        exception to the first stack frame, not to the libraries beneath it,
        because a shared dependency appears in every trace as a victim and
        disabling it would take the working mods with it.

        Libraries other mods depend on are named as still-erroring rather
        than switched off. BaseLib genuinely threw a HarmonyException in the
        session that killed the game, and five installed mods declare it as
        a dependency - their own manifests say so, so the plugin does not
        have to guess which mods are libraries. `protected_ids` is a second
        belt for games whose mods do not declare dependencies.

        `dry_run` returns exactly the same answer without touching anything,
        which is what the panel row is built from. The row saying "9 mods
        broke" and the button switching off 3 was its own bug.

        `auto_only` narrows this to the mods no judgement is needed on, for
        acting without being asked: see _AUTO_DISABLE_FLOOD.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not re.fullmatch(r"[A-Za-z0-9 ._-]+", game_user_dir or ""):
            return {"ok": False, "error": "Invalid game user dir"}
        log_path = os.path.join(
            decky.DECKY_USER_HOME, ".local", "share", game_user_dir,
            "logs", "godot.log",
        )
        if not os.path.isfile(log_path):
            return {"ok": True, "disabled": 0, "names": [],
                    "error": "No session log yet - launch the game once"}
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                status, _modded = _parse_mod_load_log(f.read().splitlines())
        except OSError as e:
            return {"ok": False, "error": str(e)}
        blamed = {
            key: info for key, info in status.items()
            if info.get("state") in ("error", "degraded")
            and (not auto_only or _is_unambiguously_broken(info))
        }
        if not blamed:
            return {"ok": True, "disabled": 0, "names": []}
        records = _load_settings().get("installed", {}).get(game_domain, {})
        keep = {int(m) for m in (protected_ids or []) if m is not None}
        _install, mods_path, _disabled = _game_paths(install_dir, mods_subdir)
        _disabled = _disabled or os.path.join(mods_path + "-disabled")
        manifests = _godot_mod_manifests(mods_path)

        def blame_for(key: str, rec: dict):
            """The log entry naming this installed mod, if any.

            Five of the nine blamed tags on device matched nothing until the
            manifests were read: the log tag is a logger name, not a mod
            name, so "com.ritsukage.sts2-RitsuLib" never looked like the
            folder "RitsuLib".
            """
            folder = rec.get("folder") or key
            info = manifests.get(folder) or {}
            for hit in blamed.values():
                tag = hit.get("tag") or ""
                if (
                    _tag_names_mod(tag, info.get("id") or "")
                    or _tag_names_mod(tag, info.get("name") or "")
                    or _tag_names_mod(tag, folder)
                ):
                    return hit
            # Older games with no manifest: fall back to the loose match
            # that was here before, so nothing regresses.
            return (
                blamed.get(_norm_mod_id(key))
                or blamed.get(_norm_mod_id(rec.get("name") or ""))
                or blamed.get(_norm_mod_id(rec.get("folder") or ""))
            )

        def already_off(key: str, rec: dict) -> bool:
            """A mod switched off earlier must not be counted again.

            The log does not change when a mod is disabled - the session
            that blamed it already happened - so without this the row keeps
            offering to switch off mods that are already off.
            """
            if rec.get("enabled") is False or rec.get("parked"):
                return True
            folder = rec.get("folder") or key
            # Only ask the filesystem about mods the filesystem tracks. A
            # dataDir mod (Bethesda) has no folder of its own - its files
            # live in Data - so an isdir check would call every one of them
            # switched off.
            if os.path.isdir(os.path.join(_disabled, folder)):
                return True
            return not os.path.isdir(os.path.join(mods_path, folder)) and (
                os.path.isdir(mods_path)
                and rec.get("mode") in (None, "", "folder")
            )

        # A mod updated moments ago is judged on a log its old version
        # wrote. Two mods were switched off on device that had newer
        # versions waiting - the blame was real, but the remedy was wrong.
        spared = {n for n in (exclude or []) if n}
        candidates = [
            (key, rec, hit)
            for key, rec in list(records.items())
            for hit in [blame_for(key, rec)]
            if hit and not already_off(key, rec)
            and (rec.get("name") or key) not in spared
        ]
        # Whatever survives this pass is what dependencies are judged
        # against: hold back a library only if something staying on needs it.
        going = {
            (rec.get("folder") or key) for key, rec, _h in candidates
            if rec.get("mod_id") not in keep
        }
        keeping = {f for f in manifests if f not in going}
        needed = _mods_needed_by_others(manifests, keeping)
        off, held, held_details, errors = [], [], [], []
        for key, rec, hit in candidates:
            folder = rec.get("folder") or key
            mod_id = (manifests.get(folder) or {}).get("id") or ""
            dependents = needed.get(mod_id.strip().lower(), [])
            if rec.get("mod_id") in keep or dependents:
                held.append(rec.get("name") or key)
                held_details.append({
                    "name": rec.get("name") or key,
                    "mod_id": rec.get("mod_id"),
                    "version": rec.get("version") or "",
                    "why": hit.get("detail") or "errored last run",
                })
                continue
            if dry_run:
                off.append({"name": rec.get("name") or key,
                            "why": hit.get("detail") or "errored last run",
                            "mod_id": rec.get("mod_id"),
                            "version": rec.get("version") or ""})
                continue
            try:
                result = await self.set_mod_enabled(
                    install_dir, mods_subdir, key, False, install_mode,
                    game_domain, app_id, plugins_subpath, plugins_style,
                )
            except Exception as e:  # noqa: BLE001 - report, keep going
                errors.append(f"{key}: {type(e).__name__}")
                continue
            if result.get("ok"):
                off.append({"name": rec.get("name") or key,
                            "why": hit.get("detail") or "errored last run",
                            "mod_id": rec.get("mod_id"),
                            "version": rec.get("version") or ""})
            else:
                errors.append(f"{key}: {result.get('error')}")
        if not dry_run:
            decky.logger.info(
                f"disabled {len(off)} mod(s) the last session blamed: "
                f"{', '.join(m['name'] for m in off[:6])}"
                + (f"; left {len(held)} other mods depend on: "
                   f"{', '.join(held[:4])}" if held else "")
            )
        return {
            "ok": True,
            "disabled": len(off),
            "names": [m["name"] for m in off],
            "details": off[:12],
            "held": held[:6],
            "held_details": held_details[:12],
            "errors": errors[:6],
            # Blamed regardless of what was done about it - a held-back
            # library that is two minor versions behind the game is the most
            # likely thing to be fixed by an update.
            "blamed_folders": sorted(
                {rec.get("folder") or key for key, rec, _h in candidates}
                | {
                    rec.get("folder") or key
                    for key, rec in records.items()
                    if blame_for(key, rec)
                }
            ),
        }

    async def apply_known_verdicts(
        self, game_domain: str, install_dir: str, mods_subdir: str,
        install_mode: str = "folder", app_id: int = 0,
        plugins_subpath: str = "", plugins_style: str = "starred",
        protected_ids: list = None,
    ) -> dict:
        """Switch off mods already known not to run on this game build,
        before the user ever launches.

        This is the step that stops the first crash. Michael reset game
        modding, reinstalled the collection and the game died on the same mod
        for the third time - every fix so far only worked AFTER a crash had
        produced a log to read.

        Only verdicts for the installed build, and only for the same mod
        version they were recorded against: an update is the most likely
        thing to have fixed it, so a newer version starts from innocent.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        build = _steam_build_id(app_id)
        known = _known_broken_mods(game_domain, build)
        # Stale ones are handled first and differently. A collection reinstall
        # restores its pinned version, so BaseLib 3.1.2 and RitsuLib 0.2.30
        # came back with all five errors minutes after being fixed - the
        # verdict knew, and finish-setup had no way to act on it.
        stale = _known_broken_mods(game_domain, build, "stale")
        updated = []
        if stale:
            records = _load_settings().get("installed", {}).get(
                game_domain, {}
            )
            names = [
                rec.get("name") or key
                for key, rec in records.items()
                if rec.get("mod_id") in stale
                and (stale[rec["mod_id"]].get("version") or "")
                == (rec.get("version") or "")
            ]
            updated = await self._update_held_mods(
                game_domain, install_dir, mods_subdir, install_mode, app_id,
                plugins_subpath, plugins_style, names,
            )
        if not known:
            return {"ok": True, "disabled": 0, "names": [], "held": [],
                    "updated": updated}
        _install, mods_path, _dis = _game_paths(install_dir, mods_subdir)
        manifests = _godot_mod_manifests(mods_path)
        keep = {int(m) for m in (protected_ids or []) if m is not None}
        records = _load_settings().get("installed", {}).get(game_domain, {})
        going, targets = set(), []
        for key, rec in list(records.items()):
            mod_id = rec.get("mod_id")
            verdict = known.get(mod_id) if mod_id else None
            if not verdict or mod_id in keep:
                continue
            # A different version than the one that failed has not been
            # tried yet. Assuming it is still broken would pin a user to a
            # fix that has already shipped.
            if (verdict.get("version") or "") != (rec.get("version") or ""):
                continue
            if rec.get("enabled") is False or rec.get("parked"):
                continue
            targets.append((key, rec, verdict))
            going.add(rec.get("folder") or key)
        if not targets:
            return {"ok": True, "disabled": 0, "names": [], "held": [],
                    "updated": updated}
        needed = _mods_needed_by_others(
            manifests, {f for f in manifests if f not in going}
        )
        off, held, errors = [], [], []
        for key, rec, verdict in targets:
            folder = rec.get("folder") or key
            mod_id = (manifests.get(folder) or {}).get("id") or ""
            if needed.get(mod_id.strip().lower()):
                held.append(rec.get("name") or key)
                continue
            try:
                result = await self.set_mod_enabled(
                    install_dir, mods_subdir, key, False, install_mode,
                    game_domain, app_id, plugins_subpath, plugins_style,
                )
            except Exception as e:  # noqa: BLE001 - report, keep going
                errors.append(f"{key}: {type(e).__name__}")
                continue
            if result.get("ok"):
                off.append(rec.get("name") or key)
            else:
                errors.append(f"{key}: {result.get('error')}")
        decky.logger.info(
            f"switched off {len(off)} mod(s) already known not to run on "
            f"{game_domain} build {build}: {', '.join(off[:6])}"
        )
        return {"ok": True, "disabled": len(off), "names": off,
                "held": held[:6], "updated": updated, "errors": errors[:6]}

    async def get_known_mod_verdict(
        self, game_domain: str, mod_id: int, app_id: int = 0
    ) -> dict:
        """Whether we have watched this mod fail on the build installed now.

        For the mod's own page. Michael: "I dont want users to run into these
        problems indicually as well as on collections" - and unlike the
        hand-written table next door, this one fills itself in.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        try:
            verdict = _known_broken_mods(
                game_domain, _steam_build_id(app_id)
            ).get(int(mod_id))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Invalid mod id"}
        if not verdict:
            return {"ok": True, "known": False}
        return {
            "ok": True,
            "known": True,
            "version": verdict.get("version") or "",
            "why": verdict.get("why") or "",
        }

    async def get_health_check(
        self, game_domain: str, install_dir: str, mods_subdir: str,
        app_id: int = 0, framework_ids: list = None,
    ) -> dict:
        """What is wrong with this setup that the user cannot see.

        Michael asked for this months ago and was talked out of it. One day
        of testing Slay the Spire 2 settled the argument: two mods silently
        did not load for want of a library, a collection's pinned libraries
        broke four more, mods were switched off that had fixes published,
        and a collection listed mods Nexus no longer serves. Every one of
        those was knowable, and none of it appeared anywhere a player looks.

        Checks each installed mod against what its Nexus page says it
        needs - other mods, and game DLC. The DLC half is the one that
        matters most for Bethesda games, where a missing expansion is not
        discoverable until the game refuses to start, and where Michael
        lost a day to exactly that on New Vegas.

        Where the game keeps a log we can read, what the game said beats
        what a mod page implies. Welcome to Night City installs 283 mods and
        deliberately omits seven the pages call required; it boots, and its
        script stack compiles clean. Those seven are the curator's decision,
        not a fault - and one of them, General Shadows Fixes, is the mod
        whose script was breaking the game. Reported as problems, they sent
        Michael to install the cause of the fault. So a requirement a
        collection left out is only a problem when the game complains about
        it, and Stardew is the case that proves it cuts both ways: SMAPI's
        log confirmed a collection there genuinely had left out something
        needed.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        records = _load_settings().get("installed", {}).get(game_domain, {})
        tracked = {
            key: rec for key, rec in records.items()
            if rec.get("mod_id") and rec.get("enabled") is not False
        }
        install_path, _mods_path, _dis = _game_paths(install_dir, mods_subdir)
        data_path = _game_paths(install_dir, mods_subdir)[1]
        build = _steam_build_id(app_id)
        # Asked of every record, not just the enabled ones: a failing script
        # belonging to a mod already switched off is exactly what we must
        # not report again, and one belonging to no record at all is the
        # orphan case that cost weeks.
        script = _redscript_report(install_path, records)
        verdicts = _verdicts_for_build(game_domain, build)
        have_ids = {int(rec["mod_id"]) for rec in tracked.values()}
        # The framework is installed, but not as a tracked mod - it arrives
        # through Step 1, not the mod list. Without this every SMAPI mod
        # reads as missing SMAPI: 77 of them on Michael's Stardew, on a
        # setup that booted perfectly and showed every mod in the config
        # menu. A health check that cries wolf 77 times is worse than none.
        for fid in framework_ids or []:
            try:
                have_ids.add(int(fid))
            except (TypeError, ValueError):
                pass
        # Which DLC the user actually owns, read from disk rather than from
        # the store: a master file present in Data is the only proof that
        # survives a reinstall, a family share or a regional edition.
        owned_dlc = set()
        try:
            for name in os.listdir(data_path):
                human = DLC_MASTER_NAMES.get(name.lower())
                if human:
                    owned_dlc.add(human)
        except OSError:
            pass
        needs_mods, needs_dlc, needs_external, errors = [], [], [], []
        # One query per 20 mods rather than one per mod. At 14 mods the
        # difference is invisible; on a 500-mod Fallout 3 collection it is
        # 25 requests instead of 500, which is the difference between a
        # pause and a screen that looks hung.
        by_mod = {}
        try:
            api_key = _load_settings().get("api_key")
            game_id = await _resolve_game_id(game_domain, api_key)
            nodes = await _legacy_mods_in_batches(
                game_id,
                sorted({int(rec["mod_id"]) for rec in tracked.values()}),
                REQUIREMENT_FIELDS,
                api_key,
            )
            by_mod = {int(n["modId"]): _split_requirements(n) for n in nodes}
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError,
                KeyError, ValueError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        for key, rec in sorted(tracked.items()):
            reqs = by_mod.get(int(rec["mod_id"]))
            if reqs is None:
                # Asked for and not returned. Saying nothing beats claiming
                # a mod has no requirements because the API skipped it.
                errors.append(f"{key}: no data returned")
                continue
            name = rec.get("name") or key
            missing = [
                {"name": r.get("modName") or f"Mod {r.get('modId')}",
                 "mod_id": r.get("modId"),
                 "notes": r.get("notes") or ""}
                for r in reqs.get("requirements") or []
                if (r.get("modId") or 0) > 0
                and int(r["modId"]) not in have_ids
                and not re.search(r"optional", r.get("notes") or "", re.I)
                # A mod manager is not a missing dependency: this plugin IS
                # the manager, and the mod is already installed.
                and not _MANAGER_REQUIREMENT_RE.search(r.get("modName") or "")
            ]
            if missing:
                needs_mods.append({
                    "name": name, "mod_id": rec["mod_id"], "missing": missing,
                    # Who put this mod here. A curator who leaves a
                    # requirement out of a 283-mod set that boots has made a
                    # decision; a user installing one mod by hand has not.
                    "from_collection": rec.get("source") == "collection",
                    "record_key": key,
                })
            # "or BaseLib on Github (declared version in description of my
            # files)" is an alternative SOURCE for a mod already installed
            # from Nexus, not a second thing to go and get. Authors write
            # these constantly, and reporting one as missing sends the user
            # after something they already have - which is exactly what the
            # health check exists to stop.
            off_nexus = [
                {"name": r.get("modName") or "an off-site file",
                 "url": r.get("url") or ""}
                for r in reqs.get("requirements") or []
                if (r.get("modId") or 0) <= 0 and r.get("url")
                and not re.match(r"\s*or", r.get("modName") or "", re.I)
            ]
            if off_nexus:
                needs_external.append({"name": name, "files": off_nexus})
            # DLC is only checkable where the game keeps its expansions as
            # master files we can see. Elsewhere, saying nothing beats
            # guessing "you do not own this" at somebody who does.
            if owned_dlc or _dlc_checkable(game_domain):
                short = [
                    d["name"] for d in reqs.get("dlc") or []
                    if d.get("name")
                    and d["name"].strip().lower() not in {
                        o.lower() for o in owned_dlc
                    }
                ]
                if short:
                    needs_dlc.append({"name": name, "dlc": short})

        # An orphaned .reds nobody owns, whose filename IS the name of a mod
        # we are about to recommend, is that mod's script left behind by an
        # install whose record was lost. Verified on device: the file was
        # GeneralShadowsFixes.reds and the requirement is mod 20405 "General
        # Shadows Fixes". So the mod has already been tried here and its
        # script does not compile against this game build - which is a
        # verdict, and the strongest reason there is not to suggest it.
        wanted_names = {
            _norm_mod_id(m["name"]): m
            for f in needs_mods for m in f["missing"] if m.get("mod_id")
        }
        # A log written before the mods changed says nothing about the mods
        # that are there now, so it earns no verdicts and moves nothing.
        learned = []
        for orphan in ([] if script["stale"] else script["orphans"]):
            stem = _norm_mod_id(orphan["script"].rsplit(".", 1)[0])
            want = wanted_names.get(stem)
            if not want:
                continue
            why = (
                f"{orphan['script']} does not compile on this game build"
                + (f" - {orphan['kind'].lower().replace('_', ' ')} "
                   f"{orphan['symbol']}" if orphan["symbol"] else "")
            )
            learned.append({"mod_id": want["mod_id"], "name": want["name"],
                            "why": why, "version": ""})
        if learned:
            _record_mod_verdicts(game_domain, build, learned)
            verdicts = _verdicts_for_build(game_domain, build)

        # Never recommend a mod this device has already watched fail.
        known_bad = []
        for finding in needs_mods:
            kept = []
            for m in finding["missing"]:
                v = verdicts.get(int(m.get("mod_id") or 0))
                if v:
                    known_bad.append({
                        "name": v.get("name") or m["name"],
                        "for": finding["name"],
                        "why": v.get("why") or "",
                        # Carried so the page can open it. A user told not to
                        # install something is exactly the user who wants to
                        # read its page and decide for themselves.
                        "mod_id": int(m.get("mod_id") or 0),
                    })
                else:
                    kept.append(m)
            finding["missing"] = kept
        needs_mods = [f for f in needs_mods if f["missing"]]

        # The corroboration. A requirement a collection deliberately omitted
        # is informational UNLESS the game's own log complains about that
        # mod - and only where there is a log to ask. No log means nothing
        # has changed for the eight games that have no redscript at all.
        blamed_keys = {
            f["record_key"] for f in script["failures"] if f["record_key"]
        }
        needs_mods_info = []
        if script["ran"] and script["compiled"] and not script["stale"]:
            keep = []
            for finding in needs_mods:
                if (finding["from_collection"]
                        and finding["record_key"] not in blamed_keys):
                    needs_mods_info.append(finding)
                else:
                    keep.append(finding)
            needs_mods = keep

        # One bad .reds stops EVERY script mod loading, so a mod that owns a
        # failing script is not a judgement call - it is the thing standing
        # between the user and every other script mod they installed. It
        # goes off, and the page says so afterwards.
        #
        # Only when the compile actually DIED. Both logs on device are
        # unambiguous - six errors and no completion line, or zero errors
        # and one - but "errored and finished anyway" has never been seen
        # here, and switching a mod off on a guess about a state nobody has
        # observed is how a check starts crying wolf. That case gets
        # reported and left alone.
        switched_off = []
        for fail in (
            script["failures"]
            if not script["compiled"] and not script["stale"]
            else []
        ):
            rec = records.get(fail["record_key"]) or {}
            if rec.get("enabled") is False or rec.get("parked"):
                continue
            # Acted on this log already. The session that blamed the mod
            # already happened and re-reading it does not make it happen
            # again - so a user who decides they want the mod anyway and
            # switches it back on would otherwise lose it the moment they
            # reopened this page, with nothing to explain why. They get it
            # back when the game next runs and says so again.
            if rec.get("auto_off_log") == script["stamp"]:
                continue
            result = await self.set_mod_enabled(
                install_dir, mods_subdir, fail["record_key"], False,
                "folder", game_domain, app_id, "", "starred", None,
            )
            if not result.get("ok"):
                errors.append(f"{fail['record_key']}: {result.get('error')}")
                continue
            switched_off.append({"name": fail["mod"], "script": fail["script"],
                                 "why": fail["symbol"] or fail["kind"]})
            stamped = _load_settings()
            target = (stamped.get("installed", {}).get(game_domain, {})
                      .get(fail["record_key"]))
            if target is not None:
                target["auto_off_log"] = script["stamp"]
                _save_settings(stamped)
            if fail["mod_id"]:
                _record_mod_verdicts(game_domain, build, [{
                    "mod_id": fail["mod_id"], "name": fail["mod"],
                    "version": fail["version"],
                    "why": f"{fail['script']} failed to compile",
                }])
            decky.logger.warning(
                f"switched off {fail['mod']!r}: {fail['script']} failed to "
                f"compile, which stops every script mod loading"
            )
        return {
            "ok": True,
            "checked": len(tracked),
            # What the game itself reported, and the only part of this
            # report that is evidence rather than inference.
            "script_log": {
                "ran": script["ran"],
                "compiled": script["compiled"],
                # The game has not run since the mods changed, so nothing
                # here describes what is installed now. Said out loud
                # rather than silently suppressed: silence is a bug, and
                # "launch it once" is a thing the user can actually do.
                "stale": script["stale"],
                "failures": script["failures"][:10],
                "orphans": script["orphans"][:10],
                "switched_off": switched_off[:10],
            },
            # Requirements a collection left out that the game has not
            # complained about. Shown, because silence is a bug - but not
            # as faults, because they are not faults.
            "needs_mods_info": needs_mods_info[:20],
            # Mods we will not recommend, and why.
            "known_bad": known_bad[:10],
            # What the plugin already put right on its own. Without this the
            # page reads as broken: Michael installed LustTravel2 without its
            # libraries, opened the QAM - which installed them - then ran the
            # check and saw nothing, with no way to tell "nothing was wrong"
            # apart from "this does not work".
            # Only what is STILL here. An uninstall does not go through
            # reset, so the log can outlive the mod it names either way, and
            # a stale line contradicting the findings above it is worse than
            # no line at all.
            "already_fixed": [
                entry for entry in (
                    (_load_settings().get("auto_fixed") or {}).get(game_domain)
                    or []
                )
                if isinstance(entry, dict) and entry.get("name")
                and any(
                    (rec.get("name") or key) == entry["name"]
                    for key, rec in records.items()
                )
            ][-6:],
            "needs_mods": needs_mods[:20],
            "needs_dlc": needs_dlc[:20],
            "needs_external": needs_external[:20],
            "owned_dlc": sorted(owned_dlc),
            "errors": errors[:5],
        }

    async def get_blamed_folders(
        self, game_domain: str, install_dir: str, mods_subdir: str,
        game_user_dir: str,
    ) -> dict:
        """The installed mods the game's last session blamed. Reads only.

        Its one job is to tell the update check where to look. A collection
        pins mod versions on purpose and is normally left alone, but a pin
        the game prints "Loaded 21 mods WITH ERRORS" about has stopped being
        a plan - and on device the two libraries responsible were 3.1.2
        against 3.3.8 and 0.2.30 against 0.5.11.
        """
        result = await self.disable_failing_mods(
            game_domain, install_dir, mods_subdir, game_user_dir,
            "folder", 0, "", "starred", None, True, False,
        )
        if not result.get("ok"):
            return result
        return {"ok": True, "folders": result.get("blamed_folders") or []}

    async def _install_missing_requirements(
        self, game_domain: str, install_dir: str, mods_subdir: str,
        install_mode: str, app_id: int, plugins_subpath: str,
        plugins_style: str, needy: list,
    ) -> list:
        """Install the libraries a mod said it needed and did not get.

        Verified on device twice. Enchanted Offerings did not load because
        BaseLib was absent; LustTravel2 did not load because RitsuLib was.
        Both times the loader said so in one line and the mod's own Nexus
        page listed the missing library with its mod id:

            RitsuLib (137) - "Required base library"

        So no table of known libraries is needed, and none is kept: the mod
        page is the authority on what the mod needs. Requirements whose
        notes say "optional" are left alone, and anything not hosted on
        Nexus (modId 0 - redistributables and the like) cannot be installed
        from here.

        `needy` is [{"name", "mod_id"}]. Returns [{"name", "for"}] of what
        was installed and which mod wanted it.
        """
        if not needy:
            return []
        records = _load_settings().get("installed", {}).get(game_domain, {})
        have_ids = {
            rec.get("mod_id") for rec in records.values() if rec.get("mod_id")
        }
        done = []
        for want in needy:
            mod_id = want.get("mod_id")
            if not mod_id:
                continue
            try:
                reqs = await self.get_mod_requirements(
                    game_domain, int(mod_id)
                )
                if not reqs.get("ok"):
                    continue
                for req in reqs.get("requirements") or []:
                    rid = req.get("modId") or 0
                    if rid <= 0 or rid in have_ids:
                        continue
                    if re.search(r"optional", req.get("notes") or "", re.I):
                        continue
                    files = await self.get_mod_files(game_domain, rid)
                    newest = (files.get("files") or [None])[0]
                    if not newest:
                        continue
                    name = req.get("modName") or f"Mod {rid}"
                    result = await self.install_mod(
                        game_domain, rid, newest["file_id"],
                        newest["file_name"], name,
                        newest.get("version") or "", install_dir,
                        mods_subdir, "", "", install_mode, app_id,
                        plugins_subpath, plugins_style,
                    )
                    if result.get("ok"):
                        have_ids.add(rid)
                        done.append({"name": name,
                                     "for": want.get("name") or ""})
                        # Remembered so the health check can say what it
                        # already sorted out, instead of looking broken by
                        # finding nothing.
                        settings = _load_settings()
                        log = settings.setdefault("auto_fixed", {}).setdefault(
                            game_domain, []
                        )
                        log.append({"name": name,
                                    "for": want.get("name") or ""})
                        settings["auto_fixed"][game_domain] = log[-12:]
                        _save_settings(settings)
                        decky.logger.info(
                            f"installed {name!r} because "
                            f"{want.get('name')!r} needs it and it was "
                            f"missing"
                        )
            except Exception as e:  # noqa: BLE001 - best effort, never fatal
                decky.logger.warning(
                    f"could not resolve requirements for "
                    f"{want.get('name')!r}: {type(e).__name__}: {e}"
                )
        return done

    async def _update_held_mods(
        self, game_domain: str, install_dir: str, mods_subdir: str,
        install_mode: str, app_id: int, plugins_subpath: str,
        plugins_style: str, held_names: list,
    ) -> list:
        """Install the newest version of each blamed mod that cannot be
        switched off. Returns [{name, from, to}] for what actually changed.

        Never raises: this is a best-effort repair running behind a panel
        open, and a failed update has to leave the mod exactly as it was.
        """
        if not held_names:
            return []
        settings = _load_settings()
        records = settings.get("installed", {}).get(game_domain, {})
        wanted = {n for n in held_names if n}
        done = []
        self._no_update_for = []
        # Which (mod, version) pairs have already been asked about. Asking
        # again cannot help - the answer only changes when the mod's author
        # publishes something or the user installs a different version - and
        # this is what lets the update pass run on every panel open instead
        # of being gated behind "once per session log", which is why
        # Michael's Ryoshu update sat unoffered.
        tried = settings.setdefault("update_attempts", {}).setdefault(
            game_domain, {}
        )
        for key, rec in list(records.items()):
            name = rec.get("name") or key
            if name not in wanted or not rec.get("mod_id"):
                continue
            mod_key = str(int(rec["mod_id"]))
            if tried.get(mod_key) == (rec.get("version") or ""):
                self._no_update_for.append(name)
                continue
            try:
                files = await self.get_mod_files(
                    game_domain, int(rec["mod_id"])
                )
                newest = (files.get("files") or [None])[0]
                if not newest:
                    continue
                have = _norm_version(rec.get("version"))
                latest = _norm_version(newest.get("version"))
                if not latest or not _is_newer_version(latest, have):
                    # Nothing newer exists. Worth saying out loud rather
                    # than leaving the user to chase it: ModConfig 0.2.3 is
                    # the newest file on its page (19 April 2026) and the
                    # game has moved on since, so only its author can fix
                    # it.
                    self._no_update_for.append(rec.get("name") or key)
                    settings = _load_settings()
                    settings.setdefault("update_attempts", {}).setdefault(
                        game_domain, {}
                    )[mod_key] = rec.get("version") or ""
                    _save_settings(settings)
                    continue
                result = await self.install_mod(
                    game_domain, int(rec["mod_id"]), newest["file_id"],
                    newest["file_name"], name, newest.get("version") or "",
                    install_dir, mods_subdir, "", "", install_mode, app_id,
                    plugins_subpath, plugins_style,
                )
                if result.get("ok"):
                    done.append({"name": name, "from": rec.get("version") or "",
                                 "to": newest.get("version") or ""})
                    decky.logger.info(
                        f"updated {name!r} {rec.get('version')} -> "
                        f"{newest.get('version')} because the game blamed it "
                        f"and a newer version exists"
                    )
            except Exception as e:  # noqa: BLE001 - best effort, never fatal
                decky.logger.warning(
                    f"could not update blamed mod {name!r}: "
                    f"{type(e).__name__}: {e}"
                )
        return done

    async def repair_failing_mods(
        self, game_domain: str, install_dir: str, mods_subdir: str,
        game_user_dir: str, install_mode: str = "folder", app_id: int = 0,
        plugins_subpath: str = "", plugins_style: str = "starred",
        protected_ids: list = None,
    ) -> dict:
        """Switch off the mods that are broken beyond argument, and report
        what is left for the user to decide on.

        The button worked and should not have existed. Michael's standing
        rule: if the plugin can detect it, it fixes it - a button is a
        failure, because the audience is someone holding a handheld who has
        never heard of a stack trace. Here the plugin knew which mod threw
        1,041 exceptions and still waited to be asked.

        Acts once per session log. If the user deliberately switches a mod
        back on, opening the panel again must not silently switch it off
        - so the log this was done for is remembered, and a decision is
        only revisited when the game has actually run again.
        """
        if not re.fullmatch(r"[a-z0-9_-]+", game_domain or ""):
            return {"ok": False, "error": "Invalid game domain"}
        if not re.fullmatch(r"[A-Za-z0-9 ._-]+", game_user_dir or ""):
            return {"ok": False, "error": "Invalid game user dir"}
        log_path = os.path.join(
            decky.DECKY_USER_HOME, ".local", "share", game_user_dir,
            "logs", "godot.log",
        )
        # Dependencies are read from the manifests, not the log, so a gap is
        # knowable before the game has ever run. Michael installed
        # LustTravel2, opened Fixes and found nothing, because the check
        # needed a failed launch to have happened first.
        _inst, mods_now, _dis = _game_paths(install_dir, mods_subdir)
        gaps = _missing_manifest_deps(_godot_mod_manifests(mods_now))
        installed_deps = []
        if gaps:
            records_now = _load_settings().get("installed", {}).get(
                game_domain, {}
            )
            by_folder = {
                (rec.get("folder") or key): rec
                for key, rec in records_now.items()
            }
            installed_deps = await self._install_missing_requirements(
                game_domain, install_dir, mods_subdir, install_mode, app_id,
                plugins_subpath, plugins_style,
                [
                    {"name": g["name"],
                     "mod_id": (by_folder.get(g["folder"]) or {}).get("mod_id")}
                    for g in gaps
                ],
            )
        try:
            st = os.stat(log_path)
            signature = f"{int(st.st_mtime)}:{st.st_size}"
        except OSError:
            return {"ok": True, "repaired": 0, "names": [], "held": [],
                    "updated": [], "no_update": [],
                    "installed_deps": installed_deps,
                    "remaining": [], "note": ""}
        settings = _load_settings()
        seen = (
            settings.setdefault("auto_disabled", {}).get(game_domain) or {}
        )
        already = seen.get("log") == signature
        repaired = {"disabled": 0, "names": [], "details": [], "held": []}
        updated, no_update = [], []
        # The update pass runs every time; the disable pass runs once per
        # session log. They need different guards because they carry
        # different risks: switching a mod off contradicts a user who just
        # switched it back on, whereas installing a newer version is
        # idempotent - after it lands there is nothing newer to find, and
        # _update_held_mods remembers every (mod, version) it has already
        # asked about. Sharing one guard is why Michael's Ryoshu update
        # never got offered.
        if True:
            # Update BEFORE switching anything off. An update keeps the mod
            # and is the likeliest fix; switching off is what is left when
            # there is no newer version. Doing it the other way round
            # switched off Remove Multiplayer Player Limit and Refresh
            # Ancient on device, both of which had updates waiting - 1.3.3
            # against a published 1.4.3 in one case.
            pre = await self.disable_failing_mods(
                game_domain, install_dir, mods_subdir, game_user_dir,
                install_mode, app_id, plugins_subpath, plugins_style,
                protected_ids, True, False,
            )
            if pre.get("ok"):
                # A mod that could not load for want of a library is not
                # broken and does not need switching off - it needs the
                # library. Done before anything else so the mod is judged
                # on a run where it had what it asked for.
                installed_deps += await self._install_missing_requirements(
                    game_domain, install_dir, mods_subdir, install_mode,
                    app_id, plugins_subpath, plugins_style,
                    [
                        d for d in (pre.get("details") or [])
                        if "is not installed" in (d.get("why") or "")
                    ],
                )
                # Every mod already known to error on this build and still at
                # the version that did, whether or not the LATEST log
                # mentions it. Michael's Ryoshu was blamed two runs ago, was
                # still at 0.2.8 against a published 0.3.6, and the fix sat
                # in the Updates list while Fixes said nothing - because the
                # repair only ever read the most recent log.
                remembered = []
                stale_now = _known_broken_mods(
                    game_domain, _steam_build_id(app_id), "stale"
                )
                if stale_now:
                    records_now = _load_settings().get("installed", {}).get(
                        game_domain, {}
                    )
                    remembered = [
                        rec.get("name") or key
                        for key, rec in records_now.items()
                        if rec.get("mod_id") in stale_now
                        and (stale_now[rec["mod_id"]].get("version") or "")
                        == (rec.get("version") or "")
                    ]
                # A mod that was only missing a library is not a candidate
                # for updating or switching off: it has not had a fair run.
                fixed_by_deps = {d["for"] for d in installed_deps}
                blamed_names = (
                    [
                        d["name"] for d in (pre.get("details") or [])
                        if d["name"] not in fixed_by_deps
                    ]
                    + list(pre.get("held") or [])
                    + remembered
                )
                updated = await self._update_held_mods(
                    game_domain, install_dir, mods_subdir, install_mode,
                    app_id, plugins_subpath, plugins_style, blamed_names,
                )
                no_update = list(getattr(self, "_no_update_for", []))
                # What the OLD version did is worth remembering: a
                # collection reinstall pins it straight back.
                _record_mod_verdicts(
                    game_domain, _steam_build_id(app_id),
                    [
                        d for d in (pre.get("details") or [])
                        + (pre.get("held_details") or [])
                        if d.get("name") in {u["name"] for u in updated}
                    ],
                    "stale",
                )
        if not already:
            repaired = await self.disable_failing_mods(
                game_domain, install_dir, mods_subdir, game_user_dir,
                install_mode, app_id, plugins_subpath, plugins_style,
                protected_ids, False, True,
                [u["name"] for u in updated]
                + [d["for"] for d in installed_deps],
            )
            if not repaired.get("ok"):
                return repaired
            settings = _load_settings()
            settings.setdefault("auto_disabled", {})[game_domain] = {
                "log": signature,
                "names": repaired.get("names") or [],
            }
            _save_settings(settings)
            if repaired.get("names"):
                decky.logger.info(
                    f"automatically switched off {len(repaired['names'])} "
                    f"mod(s) the game could not run: "
                    f"{', '.join(repaired['names'][:6])}"
                )
            # Remember it, so a reset and reinstall does not have to crash
            # the game again to find out the same thing.
            noted = _record_mod_verdicts(
                game_domain, _steam_build_id(app_id),
                repaired.get("details") or [],
            )
            if noted:
                decky.logger.info(
                    f"recorded {noted} broken-mod verdict(s) for "
                    f"{game_domain} build {_steam_build_id(app_id)}"
                )
        else:
            repaired = {"disabled": 0, "names": seen.get("names") or [],
                        "details": [], "held": [], "ok": True}
        # A blamed mod that CANNOT be switched off - because other mods
        # depend on it - has exactly one remedy left: a newer version.
        #
        # Verified on device, 2026-08-13. The collection pinned BaseLib 3.1.2
        # and RitsuLib 0.2.30 against a build wanting 3.3.8 and 0.5.11.
        # Updating those two took the blamed count from 5 to 1 and the error
        # lines from 182 to 3 - it fixed the two mods that DEPEND on RitsuLib
        # as well, which is why switching a library off is never the answer.
        #
        # Bounded on purpose: only held-back mods, and only when a newer
        # version exists. Everything switchable is already switched off, so
        # this never downloads for a mod that had a cheaper remedy.
        # Whatever is still blamed after that is a judgement call, so it
        # stays on the button rather than being decided for the user.
        rest = await self.disable_failing_mods(
            game_domain, install_dir, mods_subdir, game_user_dir,
            install_mode, app_id, plugins_subpath, plugins_style,
            protected_ids, True, False, [u["name"] for u in updated],
        )
        # EVERY blamed mod gets a verdict, not just the ones something was
        # done about. The mods left to the user's judgement were recording
        # nothing, so a mod blamed two launches ago was forgotten the moment
        # the log rotated - which is how Ryoshu 0.2.8 came to sit beside a
        # published 0.3.6 with the plugin saying nothing.
        if rest.get("ok"):
            _record_mod_verdicts(
                game_domain, _steam_build_id(app_id),
                (rest.get("held_details") or []) + (rest.get("details") or []),
                "stale",
            )
        return {
            "ok": True,
            "repaired": len(repaired.get("names") or []),
            "names": repaired.get("names") or [],
            "updated": updated,
            # Libraries installed because a mod asked for them and they were
            # not there. [{"name", "for"}].
            "installed_deps": installed_deps,
            # Blamed, cannot be switched off, and nothing newer to move to -
            # a dead end that only the mod's author can clear.
            "no_update": no_update,
            "held": rest.get("held") or [],
            "remaining": rest.get("details") or [],
            # Every mod the log blamed, whether acted on, held back or left
            # to the user. A collection pinning a version the game cannot
            # run is exactly when the pin should stop being respected, so
            # these get update-checked even though a curator chose them.
            "blamed_folders": rest.get("blamed_folders") or [],
        }

    async def get_known_bad_state(
        self, app_id: int, install_dir: str, plugins_subpath: str,
        plugins_style: str, game_domain: str
    ) -> dict:
        """Installed plugins we already know break this game.

        The point of the crash hunt was never to make every user run it.
        Once a plugin is proven to stop the game booting, the next person
        who installs the same collection should never see the crash at
        all - so the finding is data, not a memory of a debugging session.
        """
        if not plugins_subpath:
            return {"ok": True, "supported": False}
        table = KNOWN_BAD_PLUGINS.get(game_domain) or {}
        if not table:
            return {"ok": True, "supported": True, "bad": [], "extra": 0}
        path = _plugins_txt_path(app_id, plugins_subpath)
        data_path = os.path.join(STEAM_COMMON, install_dir, "Data")
        entries = _plugin_entries(_read_plugins_txt(path), plugins_style)
        listed = [n for n, _ in entries]
        on = {n.lower() for n, enabled in entries if enabled}
        roots = [n for n in listed if n.lower() in table and n.lower() in on]
        if not roots:
            return {"ok": True, "supported": True, "bad": [], "extra": 0}
        dependents = await asyncio.to_thread(
            _dependents_closure, data_path, listed, set(roots))
        live_deps = [n for n in dependents if n.lower() in on]
        return {
            "ok": True, "supported": True,
            "bad": [{"name": n, "reason": table[n.lower()]} for n in roots],
            "extra": len(live_deps),
        }

    async def apply_known_bad(
        self, app_id: int, install_dir: str, plugins_subpath: str,
        plugins_style: str, game_domain: str
    ) -> dict:
        """Switch off the known-bad plugins and everything that needs
        them, and record WHY so nothing switches them back on."""
        if not plugins_subpath:
            return {"ok": False, "error": "This game has no plugin list"}
        table = KNOWN_BAD_PLUGINS.get(game_domain) or {}
        path = _plugins_txt_path(app_id, plugins_subpath)
        data_path = os.path.join(STEAM_COMMON, install_dir, "Data")
        lines = _read_plugins_txt(path)
        header = [l for l in lines if l.strip().startswith("#")]
        entries = _plugin_entries(lines, plugins_style)
        listed = [n for n, _ in entries]
        roots = [n for n in listed if n.lower() in table]
        if not roots:
            return {"ok": True, "skipped": 0, "extra": 0}
        dependents = await asyncio.to_thread(
            _dependents_closure, data_path, listed, set(roots))
        skips = _load_skips(game_domain)
        for n in roots:
            skips[n.lower()] = {"reason": table[n.lower()], "root": True}
        for n in dependents:
            skips.setdefault(
                n.lower(),
                {"reason": "needs a mod that breaks the game", "root": False},
            )
        _save_skips(game_domain, skips)
        off = set(skips)
        starred = plugins_style != "listed"
        _write_plugins_txt(path, header + [
            (n if n.lower() in off else ("*" + n if starred else n))
            for n, _ in entries
        ])
        _rewrite_load_order(
            data_path, path, plugins_style,
            IMPLICIT_MASTERS_BY_DOMAIN.get(game_domain, frozenset()),
            off,
        )
        decky.logger.info(
            f"{game_domain}: skipped {len(roots)} known-bad plugin(s) "
            f"and {len(dependents)} that depend on them"
        )
        return {"ok": True, "skipped": len(roots), "extra": len(dependents)}

    async def enforce_skips(
        self, app_id: int, install_dir: str, plugins_subpath: str,
        plugins_style: str, game_domain: str
    ) -> dict:
        """Make the load order match what we have decided is off, and take
        the full dependency closure with it.

        Run after a collection finishes and again when the game exits.
        Two things make it necessary, both found on a clean install of a
        1,972-mod collection:

        The per-install dependent check can only see a mod's OWN masters
        at the moment it installs, so a mod installed BEFORE its master
        was skipped is never reconsidered - one slipped through exactly
        that way. A pass over the finished set catches those.

        And Skyrim rewrites Plugins.txt itself: two skips came back on
        during a run. Re-asserting on exit means whatever the game did,
        the next launch starts from our state rather than its.
        """
        if not plugins_subpath:
            return {"ok": True, "changed": 0}
        path = _plugins_txt_path(app_id, plugins_subpath)
        if not os.path.isfile(path):
            return {"ok": True, "changed": 0}
        data_path = os.path.join(STEAM_COMMON, install_dir, "Data")
        skips = _load_skips(game_domain)
        if not skips:
            return {"ok": True, "changed": 0}
        lines = _read_plugins_txt(path)
        header = [l for l in lines if l.strip().startswith("#")]
        entries = _plugin_entries(lines, plugins_style)
        listed = [n for n, _ in entries]
        dependents = await asyncio.to_thread(
            _dependents_closure, data_path, listed, set(skips))
        for n in dependents:
            skips.setdefault(
                n.lower(),
                {"reason": "needs a mod that breaks the game", "root": False},
            )
        off = set(skips)
        # Only report a change when one was actually needed - this runs on
        # every game exit and a log line per launch saying "0" is noise.
        changed = [n for n, on in entries if on and n.lower() in off]
        if changed or dependents:
            _save_skips(game_domain, skips)
            starred = plugins_style != "listed"
            _write_plugins_txt(path, header + [
                (n if n.lower() in off else ("*" + n if starred else n))
                for n, on in entries
                if not (plugins_style == "listed" and n.lower() in off)
            ])
            _rewrite_load_order(
                data_path, path, plugins_style,
                IMPLICIT_MASTERS_BY_DOMAIN.get(game_domain, frozenset()),
                off,
            )
            decky.logger.info(
                f"{game_domain}: enforced skips - {len(changed)} switched "
                f"back off, {len(dependents)} dependent(s) newly caught"
            )
        return {"ok": True, "changed": len(changed),
                "new_dependents": len(dependents)}

    async def fix_load_order(
        self, app_id: int, install_dir: str, plugins_subpath: str,
        plugins_style: str, game_domain: str = ""
    ) -> dict:
        """Switch on the dependencies that are off, then sort so nothing
        loads before its masters."""
        if not plugins_subpath:
            return {"ok": False, "error": "This game has no plugin list"}
        path = _plugins_txt_path(app_id, plugins_subpath)
        data_path = os.path.join(STEAM_COMMON, install_dir, "Data")
        r = _rewrite_load_order(
            data_path, path, plugins_style,
            IMPLICIT_MASTERS_BY_DOMAIN.get(game_domain, frozenset()),
            set(_load_skips(game_domain)),
        )
        if r.get("ok"):
            # FO3/FNV load by file timestamp, so switching a master back on
            # is only half the job - it also has to be stamped earlier than
            # everything that needs it, or it is enabled and still loading
            # after its dependents.
            if plugins_style == "listed":
                r["restamped"] = await asyncio.to_thread(
                    _stagger_plugin_mtimes, data_path, path,
                    plugins_style, game_domain
                )
            decky.logger.info(
                f"load order fixed: {r['violations_before']} -> "
                f"{r['violations_after']} violations, "
                f"{r.get('enabled_masters', 0)} master(s) switched on, "
                f"{r.get('restamped', 0)} restamped"
            )
        return r

    async def crash_bisect_start(
        self, app_id: int, install_dir: str, plugins_subpath: str,
        plugins_style: str, game_domain: str, signature: str,
        log_subpath: str, keep_dlls: list
    ) -> dict:
        """Begin an automated hunt for the plugins that crash the game.

        Two days of doing this by hand on the device's 1,960-mod Skyrim
        found five separate culprits, each a tiny ESL patch, at roughly a
        dozen four-minute launches apiece. Every wrong turn came from ME
        varying something between steps - restoring mod DLLs that then
        crashed on absent forms, or reading a result without checking the
        crash address. A machine does not get bored and does not forget
        to check.
        """
        if not plugins_subpath or plugins_style == "listed":
            return {"ok": False, "error": "This game's load order isn't a list"}
        path = _plugins_txt_path(app_id, plugins_subpath)
        # A previous hunt that was interrupted leaves the load order
        # halfway through a test. Snapshotting "whatever is enabled right
        # now" as the search space then hunts inside a fraction of the
        # mods and can never find a culprit outside it - on device that
        # silently reduced 1,947 plugins to 968. So restore the pristine
        # list first if one was kept, and only take a fresh backup when
        # there is nothing to restore from.
        pristine = path + ".decky-bisect-orig"
        if os.path.isfile(pristine):
            try:
                shutil.copy2(pristine, path)
                decky.logger.info(
                    "crash hunt: restored the load order left by an "
                    "interrupted run before starting"
                )
            except OSError:
                pass
        entries = _plugin_entries(_read_plugins_txt(path), plugins_style)
        order = [n for n, on in entries if on]
        if len(order) < 4:
            return {"ok": False, "error": "Not enough mods to search"}
        # An empty signature means "chase whatever crash just happened".
        # Hardcoding one per game in the registry meant the hunt could only
        # ever chase the crash we happened to know about; the moment the
        # first fault was fixed a second surfaced at a different address
        # and the feature was useless against it.
        if not signature:
            se_dir = os.path.dirname(_game_prefs_path(app_id, log_subpath))
            newest = _newest_crash_log(
                (se_dir, os.path.join(se_dir, "Crashlogs")))
            if not newest:
                return {"ok": False, "error":
                        "No crash log to work from - launch the game once"}
            parsed = _parse_crash_log(newest)
            # The MODULE alone is far too broad - nearly every crash is
            # "in SkyrimSE.exe", so that would count the facegen crash and
            # the data-load crash as the same fault. The offset is what
            # identifies one specific fault, so take it from the exception
            # line the same way crash_since reports it.
            m = re.search(r"at (0x[0-9A-Fa-f]+)\s+(\S+\+[0-9A-Fa-f]+)",
                          parsed.get("exception") or "")
            if not m:
                return {"ok": False, "error":
                        "Could not read a crash address from the latest log"}
            signature = m.group(2)
        if not os.path.isfile(pristine):
            try:
                shutil.copy2(path, pristine)
            except OSError:
                pass
        # Park every mod DLL except the few the game genuinely needs.
        #
        # This is the whole reason the first overnight run went nowhere. A
        # hunt turns half the plugins off; SKSE plugins that look up forms
        # from their own plugin then die on a null, which is a crash the
        # hunt correctly refuses to count - and then retries, forever,
        # because that outcome is perfectly repeatable. BladeAndBlunt did
        # exactly this 20 times in a row. Parking them removes the failure
        # instead of politely declining to learn from it.
        parked = []
        se_dir = os.path.join(
            STEAM_COMMON, install_dir,
            *SE_PLUGIN_DIRS.get(log_subpath.split("/")[0],
                                ("Data", "SKSE", "Plugins"))
        )
        keep = {str(n).lower() for n in (keep_dlls or [])}
        if os.path.isdir(se_dir):
            for n in sorted(os.listdir(se_dir)):
                if not n.lower().endswith(".dll") or n.lower() in keep:
                    continue
                try:
                    os.replace(os.path.join(se_dir, n),
                               os.path.join(se_dir, n + SE_DISABLED_SUFFIX))
                    parked.append(n)
                except OSError:
                    pass
            # A hunt restarted while a previous one's DLLs are still parked
            # would otherwise record nothing to restore and leave 160-odd
            # mods switched off for good.
            already = {n for n in parked}
            for n in sorted(os.listdir(se_dir)):
                if not n.endswith(SE_DISABLED_SUFFIX):
                    continue
                live = n[: -len(SE_DISABLED_SUFFIX)]
                if live.lower() not in keep and live not in already:
                    parked.append(live)
        state = {
            "app_id": app_id, "install_dir": install_dir,
            "plugins_subpath": plugins_subpath, "plugins_style": plugins_style,
            "game_domain": game_domain, "signature": signature,
            "order": order, "skipped": [], "lo": 0, "hi": len(order),
            "launches": 0, "found": None,
            "se_dir": se_dir, "parked_dlls": parked,
        }
        _bisect_save(state)
        decky.logger.info(
            f"crash hunt started over {len(order)} plugins, "
            f"{len(parked)} mod DLL(s) parked for the duration"
        )
        return {"ok": True, "total": len(order), "parked_dlls": len(parked),
                "signature": signature}

    async def crash_bisect_apply(self) -> dict:
        """Write the load order for the next launch."""
        state = _bisect_load()
        if not state:
            return {"ok": False, "error": "No hunt in progress"}
        if state["hi"] <= state["lo"]:
            return {"ok": True, "done": True, "skipped": state["skipped"]}
        mid = _bisect_next_prefix(state)
        state["testing"] = mid
        _bisect_save(state)
        keep = {n.lower() for n in state["order"][:mid]}
        keep -= {n.lower() for n in state["skipped"]}
        path = _plugins_txt_path(state["app_id"], state["plugins_subpath"])
        lines = _read_plugins_txt(path)
        header = [l for l in lines if l.strip().startswith("#")]
        entries = _plugin_entries(lines, state["plugins_style"])
        starred = state["plugins_style"] != "listed"
        _write_plugins_txt(path, header + [
            ("*" + n if starred and n.lower() in keep else n)
            for n, _ in entries
        ])
        # Pull in the masters the prefix needs and put it in load order -
        # skipping this is how a "clean" test ends up crashing on missing
        # content instead of on the thing being tested.
        data_path = os.path.join(STEAM_COMMON, state["install_dir"], "Data")
        _rewrite_load_order(
            data_path, path, state["plugins_style"],
            IMPLICIT_MASTERS_BY_DOMAIN.get(state["game_domain"], frozenset()),
        )
        enabled = len([1 for _, on in
                       _plugin_entries(_read_plugins_txt(path),
                                       state["plugins_style"]) if on])
        return {
            "ok": True, "done": False, "testing": mid, "enabled": enabled,
            "remaining": state["hi"] - state["lo"],
            "launches": state["launches"], "skipped": state["skipped"],
        }

    async def crash_bisect_record(self, crashed: bool) -> dict:
        """Fold in one launch's outcome."""
        state = _bisect_load()
        if not state:
            return {"ok": False, "error": "No hunt in progress"}
        state = _bisect_advance(state, bool(crashed))
        collateral = []
        if state.get("found"):
            data_path = os.path.join(STEAM_COMMON, state["install_dir"], "Data")
            collateral = await asyncio.to_thread(
                _dependents_closure, data_path, state["order"],
                set(state["skipped"]),
            )
            if collateral:
                state["skipped"].extend(collateral)
            decky.logger.info(
                f"crash hunt found {state['found']}"
                + (f" (+{len(collateral)} that depend on it)"
                   if collateral else "")
            )
        _bisect_save(state)
        return {
            "ok": True, "found": state.get("found"),
            "collateral": collateral,
            "skipped": state["skipped"], "launches": state["launches"],
            "remaining": max(0, state["hi"] - state["lo"]),
            "done": state["hi"] <= state["lo"],
        }

    async def crash_bisect_finish(self, keep_skips: bool) -> dict:
        """Put every mod back, minus the ones we found (or all of them)."""
        state = _bisect_load()
        if not state:
            return {"ok": False, "error": "No hunt in progress"}
        path = _plugins_txt_path(state["app_id"], state["plugins_subpath"])
        lines = _read_plugins_txt(path)
        header = [l for l in lines if l.strip().startswith("#")]
        entries = _plugin_entries(lines, state["plugins_style"])
        off = {n.lower() for n in state["skipped"]} if keep_skips else set()
        on = {n.lower() for n in state["order"]} - off
        _write_plugins_txt(path, header + [
            ("*" + n if n.lower() in on else n) for n, _ in entries
        ])
        data_path = os.path.join(STEAM_COMMON, state["install_dir"], "Data")
        _rewrite_load_order(
            data_path, path, state["plugins_style"],
            IMPLICIT_MASTERS_BY_DOMAIN.get(state["game_domain"], frozenset()),
        )
        # Put back every DLL the hunt parked. Leaving these off would end
        # the hunt by quietly disabling 160 working mods.
        restored = 0
        se_dir = state.get("se_dir") or ""
        for n in state.get("parked_dlls") or []:
            src = os.path.join(se_dir, n + SE_DISABLED_SUFFIX)
            if os.path.isfile(src):
                try:
                    os.replace(src, os.path.join(se_dir, n))
                    restored += 1
                except OSError:
                    pass
        decky.logger.info(f"crash hunt over: restored {restored} mod DLL(s)")
        # Drop the pristine copy: a finished hunt has already put the load
        # order back, and keeping it would make the NEXT hunt restore a
        # list that predates whatever the user installed since.
        for stale in (_bisect_state_path(), path + ".decky-bisect-orig"):
            try:
                os.remove(stale)
            except OSError:
                pass
        return {"ok": True, "skipped": state["skipped"] if keep_skips else [],
                "restored_dlls": restored}

    async def crash_bisect_status(self) -> dict:
        state = _bisect_load()
        if not state:
            return {"ok": True, "running": False}
        return {
            "ok": True, "running": True, "launches": state.get("launches", 0),
            "skipped": state.get("skipped", []),
            "remaining": max(0, state["hi"] - state["lo"]),
            "total": len(state["order"]),
        }

    async def crash_since(self, app_id: int, log_subpath: str, after: float) -> dict:
        """The newest crash report written after `after`, if any.

        This is how the hunt reads its own results instead of asking the
        user what they saw - and it checks the exception address, because
        a mod dying on a form its own plugin no longer provides looks like
        a crash but says nothing about the fault being hunted.
        """
        if not log_subpath:
            return {"ok": True, "crash": None}
        se_dir = os.path.dirname(_game_prefs_path(app_id, log_subpath))
        newest = _newest_crash_log((se_dir, os.path.join(se_dir, "Crashlogs")))
        if not newest or os.path.getmtime(newest) <= after:
            return {"ok": True, "crash": None}
        addr = ""
        try:
            with open(newest, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "Unhandled exception" in line:
                        m = re.search(r"at (0x[0-9A-Fa-f]+)\s+(\S+)", line)
                        if m:
                            addr = f"{m.group(2)} {m.group(1)}"
                        break
        except OSError:
            pass
        return {
            "ok": True,
            "crash": {"log": os.path.basename(newest), "address": addr,
                      "at": int(os.path.getmtime(newest))},
        }

    async def in_game_since(
        self, app_id: int, marker_subpath: str, after: float
    ) -> dict:
        """Has the game actually reached the world since `after`?

        Reaching the main menu is not the same as playing, and the whole
        point of the save-load hunt is faults that only appear once the
        world loads. Papyrus only logs when scripts run, and scripts run
        in the world - so the log being written after launch is a real
        "we are in" signal rather than "a window appeared".
        """
        if not marker_subpath:
            return {"ok": True, "in_game": False}
        path = _game_prefs_path(app_id, marker_subpath)
        try:
            return {
                "ok": True,
                "in_game": os.path.getmtime(path) > after,
                "at": int(os.path.getmtime(path)),
            }
        except OSError:
            # Not written yet, which is the normal state before loading.
            return {"ok": True, "in_game": False}

    async def enable_papyrus_logging(
        self, app_id: int, prefs_subpath: str
    ) -> dict:
        """Turn on the script log the save-load hunt watches for.

        Off by default in Skyrim, and without it the hunt has no way to
        tell "the save loaded" from "the user never pressed Continue".
        """
        if not prefs_subpath:
            return {"ok": False, "error": "This game has no ini to set"}
        path = _game_prefs_path(app_id, prefs_subpath)
        if not os.path.isfile(path):
            return {"ok": False, "error": "Game ini not found"}
        try:
            _patch_ini_settings(path, "Papyrus", {"bEnableLogging": "1"})
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    async def get_installed_count(self, game_domain: str) -> dict:
        """How many mods we have installed for a game.

        Cheap on purpose - the panel asks on every open just to size the
        "this will take a while" notice at launch, and the full listing
        walks the whole game folder.
        """
        records = _load_settings().get("installed", {}).get(game_domain, {})
        return {"ok": True, "mods": len(records)}

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
        if install_mode == "me3":
            settings = _load_settings()
            results = [
                {
                    "folder": key,
                    "enabled": bool(rec.get("enabled", True)),
                    "tracked": True,
                    "name": rec.get("name") or key,
                    "version": rec.get("version") or "",
                    "mod_id": rec.get("mod_id"),
                    "togglable": True,
                    "source": rec.get("source") or "",
                    "collection_slug": rec.get("collection_slug") or "",
                }
                for key, rec in _me3_records(settings, game_domain)
            ]
            results.sort(key=lambda m: (m["name"] or "").lower())
            return {
                "ok": True,
                "mods": results,
                "collections": settings.get("collections", {}).get(
                    game_domain, {}
                ),
                "attention": settings.get("collection_attention", {}).get(
                    game_domain, {}
                ),
            }

        if install_mode == "dataDir":
            settings = _load_settings()
            records = settings.get("installed", {}).get(game_domain, {})
            skips = _load_skips(game_domain) if game_domain else {}
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
                # `parked` means its files were moved out of Data, which
                # is the only way to switch off a mod made purely of
                # assets - a UI overhaul or a texture pack.
                parked = bool(rec.get("parked"))
                enabled = not parked and (
                    (not plugins) or any(p.lower() in active for p in plugins)
                )
                # WHY it is off, in the user's words, on the row where they
                # see it off. Without this a mod switched off for a real
                # reason looks identical to one the user turned off - so on
                # device the answer to "why is this disabled?" was to turn
                # it back on, which put the game back to not booting.
                why = ""
                if parked and rec.get("needs_external"):
                    why = (
                        f"Needs {rec['needs_external']}, which is not on "
                        "Nexus Mods. Add it, then switch this back on."
                    )
                elif not enabled:
                    for pl in plugins:
                        note = skips.get(pl.lower())
                        if note and note.get("reason"):
                            why = str(note["reason"])
                            break
                results.append(
                    {
                        "folder": key,
                        "enabled": enabled,
                        "tracked": True,
                        "name": rec.get("name") or key,
                        "version": rec.get("version") or "",
                        "mod_id": rec.get("mod_id"),
                        # Every dataDir mod can be toggled now: without a
                        # plugin its files are parked instead. The old
                        # `bool(plugins)` marked asset-only mods as
                        # untoggleable and left "its assets are always
                        # active" as the only answer.
                        "togglable": True,
                        "disabled_reason": why,
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
                "attention": settings.get("collection_attention", {}).get(
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
                        "enabled": rec.get("enabled") is not False,
                        "tracked": True,
                        "name": rec.get("name") or key,
                        "version": rec.get("version") or "",
                        "mod_id": rec.get("mod_id"),
                        # Game-root-relative files can be parked outside the
                        # game and put back, so they toggle. Anything routed
                        # into a single subdirectory (Palworld's LogicMods)
                        # still cannot.
                        "togglable": target == ".",
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
        settings_now = _load_settings()
        return {
            "ok": True,
            "mods": results,
            "collections": settings_now.get("collections", {}).get(
                game_domain, {}
            ),
            "attention": settings_now.get("collection_attention", {}).get(
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
        if install_mode == "me3":
            # Nothing moves on disk: the profile decides what loads, so a
            # toggle is one 'enabled' flag and a rewrite.
            settings = _load_settings()
            rec = settings.get("installed", {}).get(game_domain, {}).get(folder)
            if not rec or rec.get("mode") != "me3":
                return {"ok": False, "error": f"{folder} is not tracked"}
            if enabled and rec.get("regulation"):
                owner = _me3_regulation_owner(settings, game_domain, folder)
                if owner:
                    return {
                        "ok": False,
                        "error": (
                            f"{owner} owns regulation.bin right now - "
                            "disable it before enabling this one."
                        ),
                    }
            rec["enabled"] = bool(enabled)
            _write_me3_profile(game_domain, settings)
            _save_settings(settings)
            decky.logger.info(
                f"{'enabled' if enabled else 'disabled'} me3 mod {folder!r}"
            )
            return {"ok": True}

        if install_mode == "dataDir":
            rec = (
                _load_settings()
                .get("installed", {})
                .get(game_domain, {})
                .get(folder)
            )
            if not rec:
                return {"ok": False, "error": f"{folder} is not tracked"}
            settings = _load_settings()
            records = settings.get("installed", {}).get(game_domain, {})
            rec = records.get(folder) or rec
            plugins = rec.get("plugins") or []
            # Refuse to switch a mod back on when it still cannot work.
            # On device a collection shipped a patch for a mod it never
            # asked you to install, so the patch had no master; we switched
            # it off correctly, the user could not see why, turned it back
            # on, and the game stopped booting. Saying no with the reason is
            # the whole difference.
            if enabled and plugins:
                _install_path, data_now, _u = _game_paths(
                    install_dir, mods_subdir
                )
                blocked = _missing_masters(
                    data_now, plugins,
                    IMPLICIT_MASTERS_BY_DOMAIN.get(game_domain, frozenset()),
                )
                if blocked:
                    names = ", ".join(m for m, _deps in blocked[:3])
                    return {
                        "ok": False,
                        "error": (
                            f"{folder} needs {names}, which "
                            f"{'is' if len(blocked) == 1 else 'are'} not "
                            "installed. Switching it on stops the game "
                            "starting, so it has been left off."
                        ),
                    }
            if plugins and plugins_subpath:
                _set_plugins_active(
                    _plugins_txt_path(app_id, plugins_subpath),
                    plugins,
                    enabled,
                    plugins_style,
                )
            # Unticking a plugin does NOT switch a dataDir mod off. Its
            # textures, meshes and interface XML sit in Data and keep
            # loading, and a mod made only of those - a UI overhaul, a
            # texture pack - could not be turned off at all. On device
            # three interface mods had to be UNINSTALLED to get New Vegas
            # to start, losing the download, when all that was needed was
            # for their files to stop being read.
            _install_path, data_path, _unused = _game_paths(
                install_dir, mods_subdir
            )
            park = _parked_files_dir(game_domain, folder)
            rels = list(rec.get("files") or [])
            shared = _shared_paths(records, folder)
            movable = [r for r in rels if r.lower() not in shared]
            if enabled:
                moved = _move_mod_files(park, data_path, movable)
                _force_rmtree(park)
                rec.pop("parked", None)
            else:
                moved = _move_mod_files(data_path, park, movable)
                if moved:
                    rec["parked"] = True
            _save_settings(settings)
            decky.logger.info(
                f"{'enabled' if enabled else 'disabled'} {folder!r}: "
                f"{len(plugins)} plugin(s), {moved} file(s) moved"
                + (f", {len(shared)} left (another mod provides them)"
                   if shared else "")
            )
            return {
                "ok": True,
                "moved": moved,
                "shared": len(shared),
            }

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
        if rec and rec.get("mode") == "files" and rec.get("target") == ".":
            # Cyberpunk. Its mods are loose files scattered across five game
            # directories, so there is no folder to move aside - which is
            # why this used to answer "no toggle, uninstall it instead".
            #
            # Gated on target "." - game-root-relative paths - because
            # "files" mode is shared with Palworld's LogicMods, which route
            # into one subdirectory and are deliberately untogglable. That
            # game has never been run on device, and a change to it riding
            # along with a Cyberpunk fix is the most expensive kind of bug
            # there is.
            #
            # That was the wrong answer to a real question. One .reds that
            # will not compile takes EVERY script mod down with it, and the
            # remedy for that is to stop loading one file, not to throw away
            # a download. The dataDir tier has parked files outside the game
            # for exactly this reason since New Vegas; the only difference
            # here is that the paths are relative to the install root rather
            # than to Data.
            settings = _load_settings()
            records = settings.get("installed", {}).get(game_domain, {})
            rec = records.get(folder) or rec
            install_path = os.path.join(STEAM_COMMON, install_dir)
            park = _parked_files_dir(game_domain, folder)
            shared = _shared_paths(records, folder, modes=("files",))
            movable = [
                r for r in (rec.get("files") or [])
                if r.lower() not in shared
            ]
            if enabled:
                moved = _move_mod_files(park, install_path, movable)
                _force_rmtree(park)
                rec.pop("parked", None)
                rec.pop("disabled_reason", None)
            else:
                moved = _move_mod_files(install_path, park, movable)
                # Marked off even when nothing moved, for the same reason
                # the dataDir tier does: a file another record also claims
                # stays put, and a mod made only of those would otherwise
                # read as still on.
                rec["parked"] = True
            rec["enabled"] = bool(enabled)
            _save_settings(settings)
            decky.logger.info(
                f"{'enabled' if enabled else 'disabled'} files-mode mod "
                f"{folder!r}: {moved} file(s) moved"
                + (f", {len(shared)} left (another mod provides them)"
                   if shared else "")
            )
            return {"ok": True, "moved": moved, "shared": len(shared)}
        if rec and rec.get("target"):
            install_path = os.path.join(STEAM_COMMON, install_dir)
            base = os.path.join(install_path, *rec["target"].split("/"))
            real = rec.get("folder") or folder
            if rec["target"] == "dlc" and real.lower() in W3_OFFICIAL_DLC:
                return {
                    "ok": False,
                    "error": "This entry patches one of the game's own DLC "
                    "folders - it can't be toggled by moving the folder.",
                }
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
        if install_mode == "me3":
            settings = _load_settings()
            moved = 0
            errors = []
            owner = None
            for key, rec in _me3_records(settings, game_domain):
                want = bool(enabled)
                if want and rec.get("regulation"):
                    # Re-enabling everything must not activate two
                    # regulation.bin owners. The first one wins, and the
                    # rest say so instead of quietly staying off.
                    if owner:
                        want = False
                        errors.append(
                            f"{rec.get('name') or key}: left off - "
                            f"{owner} owns regulation.bin"
                        )
                    else:
                        owner = rec.get("name") or key
                if bool(rec.get("enabled", True)) != want:
                    rec["enabled"] = want
                    moved += 1
            _write_me3_profile(game_domain, settings)
            _save_settings(settings)
            decky.logger.info(
                f"{'enabled' if enabled else 'disabled'} all me3 mods: "
                f"{moved} changed, {len(errors)} left off"
            )
            return {"ok": True, "moved": moved, "errors": errors}
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
        if install_mode == "me3":
            settings = _load_settings()
            if not _remove_me3_record(game_domain, folder, settings):
                return {"ok": False, "error": f"{folder} is not tracked"}
            _write_me3_profile(game_domain, settings)
            _save_settings(settings)
            decky.logger.info(f"uninstalled me3 mod {folder!r}")
            return {"ok": True}
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
                if (
                    rec.get("target") == "dlc"
                    and real.lower() in W3_OFFICIAL_DLC
                ):
                    # NEVER delete the game's own DLC (legacy records
                    # from before official-dlc patches merged in).
                    decky.logger.info(
                        f"refusing to delete official DLC {real!r} "
                        f"(record {folder!r} dropped)"
                    )
                else:
                    _force_rmtree(os.path.join(base, real))
                    _force_rmtree(os.path.join(base + "-disabled", real))
            _w3_remove_menu_xmls(install_path, rec)
            settings["installed"][game_domain].pop(folder, None)
            if rec.get("pakpatch"):
                # RE Engine: a gap in the patch chain breaks every pak
                # past it - shift the surviving mod paks down.
                _pakpatch_renumber(game_domain, install_path, settings)
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
        # A merge participant leaving: recompute its merged scripts from
        # the remaining participants.
        _w3_unmerge(
            game_domain, os.path.join(STEAM_COMMON, install_dir),
            mods_path, folder, settings,
        )
        _save_settings(settings)
        if dropped:
            install_root = os.path.join(STEAM_COMMON, install_dir)
            _w3_remove_menu_xmls(install_root, dropped)
            pc_dir = os.path.join(install_root, *W3_MENU_DIR.split("/"))
            if os.path.isdir(pc_dir):
                _w3_prune_filelists(pc_dir)
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
            if install_mode == "me3":
                settings = _load_settings()
                removed_list, kept = [], []
                for key, _rec in _me3_records(settings, game_domain):
                    if key.lower() in protected_set:
                        kept.append(key)
                        continue
                    if _remove_me3_record(game_domain, key, settings):
                        removed_list.append(key)
                _write_me3_profile(game_domain, settings)
                _save_settings(settings)
                decky.logger.info(
                    f"uninstall_all (me3): removed {removed_list}, kept {kept}"
                )
                return {"ok": True, "removed": len(removed_list), "kept": kept}
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
        await _close_http_session()
        decky.logger.info("Nexus Mods plugin unloading")

    async def _uninstall(self):
        decky.logger.info("Nexus Mods plugin uninstalled")
