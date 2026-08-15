// Typed bridge to the Python backend (main.py). All callables are positional.
import { callable } from "@decky/api";

/** How a game's mods are stored and activated. "folder" per-mod dirs,
 * "dataDir" merged into Data/ with plugins.txt, "me3" outside the game
 * folder entirely with a generated .me3 profile (FromSoft games). */
export type InstallMode = "folder" | "dataDir" | "me3";

export interface NexusMod {
  modId: number;
  name: string;
  summary?: string;
  author: string;
  version: string;
  endorsements: number;
  downloads: number;
  thumbnailUrl?: string;
  /** Server-blurred thumbnail variant (v2 mods only) - used instead of
   * CSS blur when the account has "blur adult images" on. */
  thumbnailBlurredUrl?: string;
  pictureUrl?: string;
  updatedAt: string;
  adultContent: boolean;
  /** Full description (bbcode/html soup) - present via getModDetails */
  description?: string;
  /** Present via getModDetails: author profile + donation opt-in. */
  uploader?: {
    name?: string;
    memberId?: number;
    donationsEnabled?: boolean;
  };
}

export interface ModsResult {
  ok: boolean;
  total?: number;
  mods?: NexusMod[];
  error?: string;
}

export interface ModFile {
  file_id: number;
  name: string;
  file_name: string;
  version: string;
  size_kb: number;
  category_name: string;
  is_primary: boolean;
  description?: string;
}

export interface FilesResult {
  ok: boolean;
  files?: ModFile[];
  error?: string;
}

export interface InstallResult {
  ok: boolean;
  folder?: string;
  error?: string;
  /** Archive is a desktop modding tool (xEdit, patchers) - not
   * installable on-device; collections show it as skipped, not failed. */
  unsupported_tool?: boolean;
  /** Witcher script conflict with an installed mod - not retryable
   * without script merging; collections park it, not fail it. */
  script_conflict?: boolean;
  /** Conflicts with a mod that's already installed (two FromSoft mods
   * claiming regulation.bin). Retryable once the other one is disabled,
   * so it's parked with an explanation rather than called unsupported. */
  mod_conflict?: boolean;
  /** Archive layout we can't recognize - parked as skipped so it stops
   * counting as remaining (retrying can't change the layout). */
  unsupported_layout?: boolean;
  /** Option-style archive: the user must pick one of `options` and retry
   * with payload_choice set. */
  needs_choice?: boolean;
  options?: string[];
  /** FOMOD archive: show the wizard, then call installFomod with the
   * token and selected plugin ids. */
  needs_fomod?: boolean;
  fomod_token?: string;
  wizard?: unknown;
  /** Files actually written. On a repair pass this is how many were
   * missing - 0 means the mod was already complete. */
  added?: number;
  /** The installer ran but had nothing to install: none of the options it
   * offers exist in the archive. A permanent skip, not a retry - asking
   * again can only produce the same nothing. */
  nothing_staged?: boolean;
}

export interface InstalledMod {
  folder: string;
  enabled: boolean;
  tracked: boolean;
  name?: string;
  version?: string;
  mod_id?: number;
  /** dataDir mode: false when the mod has no plugin file to toggle */
  togglable?: boolean;
  /** Why it is switched off, when we switched it off for a reason. A mod
   * off deliberately and one the user turned off look identical without
   * this, so "why is this disabled?" gets answered by turning it back on -
   * which is how a device ended up not booting. */
  disabled_reason?: string;
  /** "collection" when installed as part of a collection */
  source?: string;
  /** Which collection (registered via registerCollection) */
  collection_slug?: string;
}

export interface InstalledCollectionInfo {
  title: string;
  thumb_url?: string;
  mod_count?: number;
  /** Member mod ids - membership beats record slugs (a shared mod
   * installed by another collection still belongs here). */
  mod_ids?: number[];
}

export interface InstalledResult {
  ok: boolean;
  mods?: InstalledMod[];
  /** slug -> display info for collections seen on this game */
  collections?: Record<string, InstalledCollectionInfo>;
  /** slug -> pending manual decisions (the Finish-setup queue) */
  attention?: Record<string, AttentionItem[]>;
  error?: string;
}

export interface InstallProgress {
  mod_id: number;
  phase: "downloading" | "extracting" | "paused" | "cancelled" | "done" | "error";
  percent: number;
  message?: string;
  /** Exact transfer accounting (downloading phase only). */
  bytes_done?: number;
  bytes_total?: number;
  /** Smoothed download speed, bytes/second. */
  bps?: number;
}

export interface AuthStatus {
  ok: boolean;
  name?: string;
  user_id?: number;
  is_premium?: boolean;
  error?: string;
  cleared?: boolean;
}

export interface GameStatus {
  installed: boolean;
  install_path: string;
  mods_path: string;
  mods_dir_exists: boolean;
  framework_installed?: boolean;
}

export const getMods = callable<
  [game_domain: string, sort: string, count: number, offset: number, search: string],
  ModsResult
>("get_mods");

export interface UpdateInfo {
  installed: string;
  current: string;
  update_available: boolean;
  /** The game's own log blamed this mod, so the update is not a nag - it
   * is the likely fix. */
  blamed?: boolean;
}

export const checkUpdates = callable<
  [
    game_domain: string,
    /** Folders to check even though a collection pinned them. A curator's
     * pin is normally respected; one the game cannot run is not. */
    force_folders: string[]
  ],
  { ok: boolean; updates?: Record<string, UpdateInfo>; error?: string }
>("check_updates");

export const getTrendingMods = callable<
  [game_domain: string, count: number],
  ModsResult
>("get_trending_mods");

export const getModsByIds = callable<
  [game_domain: string, mod_ids: number[]],
  ModsResult
>("get_mods_by_ids");

export const getModFiles = callable<[game_domain: string, mod_id: number], FilesResult>(
  "get_mod_files"
);

export const installMod = callable<
  [
    game_domain: string,
    mod_id: number,
    file_id: number,
    file_name: string,
    mod_name: string,
    mod_version: string,
    install_dir: string,
    mods_subdir: string,
    dl_key: string,
    dl_expires: string,
    install_mode: InstallMode,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    payload_choice: string,
    ue4ss_subdir: string,
    logicmods_subdir: string,
    launcher_xml_subpath: string,
    flat_extensions: string[],
    page_version: string,
    record_source: string,
    witcher_layout: boolean,
    collection_slug: string,
    cp77_layout: boolean,
    pakpatch_layout: boolean,
    repair_only: boolean
  ],
  InstallResult
>("install_mod");

export const getDisplayFix = callable<
  [
    app_id: number,
    prefs_subpath: string,
    section: string,
    settings: Record<string, string>
  ],
  {
    ok: boolean;
    exists?: boolean;
    compliant?: boolean;
    current?: Record<string, string>;
    error?: string;
  }
>("get_display_fix");

export const applyDisplayFix = callable<
  [
    app_id: number,
    prefs_subpath: string,
    section: string,
    settings: Record<string, string>,
    create: boolean
  ],
  { ok: boolean; error?: string }
>("apply_display_fix");

export const dismissUpdate = callable<
  [game_domain: string, folder: string, version: string],
  { ok: boolean; error?: string }
>("dismiss_update");

// Download AND extract, leaving the mod staged for a fast serial commit.
export const prepareModFile = callable<
  [game_domain: string, mod_id: number, file_id: number, file_name: string],
  { ok: boolean; prepared?: boolean; error?: string }
>("prepare_mod_file");

export const installFomod = callable<
  [token: string, selected_ids: string[]],
  InstallResult
>("install_fomod");

export const installFomodAuto = callable<
  [token: string, curator_choices: unknown],
  InstallResult
>("install_fomod_auto");

export const getCollectionManifest = callable<
  [slug: string, game_domain: string],
  { ok: boolean; choices?: Record<string, unknown>; error?: string }
>("get_collection_manifest");

export const resetGameModding = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    install_mode: InstallMode,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    framework_file_prefixes: string[],
    witcher_layout: boolean,
    framework_mod_folders: string[],
    /** [backup, original] pairs a modding tool made, restored so the setup
     * steps are honest again after a reset. */
    restore_on_reset: [string, string][],
    /** Directories besides modsSubdir that this game's mods write into, so
     * reset can find orphans no install record owns. */
    mod_write_dirs: string[]
  ],
  {
    ok: boolean;
    removed?: number;
    framework_files?: string[];
    cleared_dlo?: boolean;
    use_steam_client?: boolean;
    errors?: string[];
    /** Files left in the mods folder that no record accounted for. Zero
     * means the reset is verified, not merely finished. */
    leftovers?: number;
    /** Unrecorded files removed anyway - mod configs, logs and caches
     * that were written at runtime rather than installed. */
    swept?: number;
    leftover_examples?: string[];
    /** False for games modded before baselines existed - we cannot say
     * whether it reached vanilla, so we must not claim it did. */
    verified?: boolean;
    error?: string;
  }
>("reset_game_modding");

export const uninstallCollection = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    install_mode: InstallMode,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    slug: string
  ],
  { ok: boolean; removed?: number; errors?: string[]; error?: string }
>("uninstall_collection");

export interface AttentionItem {
  file_id: number;
  mod_id: number;
  mod_name: string;
  file_name: string;
  version: string;
  reason: string;
  options: string[];
  /** Free text explaining a skip the user cannot act on. */
  detail?: string;
}

export const setCollectionAttention = callable<
  [game_domain: string, slug: string, items: AttentionItem[]],
  { ok: boolean; count?: number; error?: string }
>("set_collection_attention");

export const getCollectionAttention = callable<
  [game_domain: string, slug: string],
  { ok: boolean; items?: AttentionItem[] }
>("get_collection_attention");

export const registerCollection = callable<
  [
    game_domain: string,
    slug: string,
    title: string,
    thumb_url: string,
    mod_count: number,
    mod_ids: number[],
    only_if_known: boolean
  ],
  { ok: boolean; skipped?: boolean; error?: string }
>("register_collection");

export const getFrameworkSetup = callable<
  [
    game_domain: string,
    /** What the game's template produces NOW. If what was written differs,
     * launch_options_current comes back false and the step offers itself
     * again instead of showing an uncorrectable tick. */
    expected: string
  ],
  {
    ok: boolean;
    launch_options_set?: boolean;
    launch_options_current?: boolean;
    launch_options_value?: string;
    enabled?: boolean;
  }
>("get_framework_setup");

export const markLaunchOptionsSet = callable<
  [game_domain: string, options: string],
  { ok: boolean; error?: string }
>("mark_launch_options_set");

export const getLaunchOptionsState = callable<
  [app_id: number],
  {
    ok: boolean;
    dlo_present?: boolean;
    dlo_options?: string | null;
    steam_options?: string[];
  }
>("get_launch_options_state");

/** dlo devices only - returns use_steam_client when the frontend should
 * fall back to SteamClient.Apps.SetAppLaunchOptions. */
export const setFrameworkLaunchOptions = callable<
  [app_id: number, game_domain: string, options: string],
  { ok: boolean; use_steam_client?: boolean; previous?: string; error?: string }
>("set_framework_launch_options");

export const clearFrameworkLaunchOptions = callable<
  [app_id: number, game_domain: string],
  {
    ok: boolean;
    cleared_dlo?: boolean;
    use_steam_client?: boolean;
    error?: string;
  }
>("clear_framework_launch_options");

export const setFrameworkEnabled = callable<
  [game_domain: string, enabled: boolean],
  { ok: boolean; error?: string }
>("set_framework_enabled");

export const installFramework = callable<
  [
    game_domain: string,
    mod_id: number,
    install_dir: string,
    install_kind: "smapi" | "copyRoot",
    detect_file: string,
    avoid_file_keywords: string[],
    install_subdir: string,
    /** So the vanilla baseline can be taken BEFORE the framework lands -
     * it is the first thing to touch the game folder. */
    mods_subdir: string,
    app_id: number
  ],
  { ok: boolean; install_path?: string; error?: string }
>("install_framework");

// Skyrim/FO4 read plugins.txt AS the load order. How many enabled
// plugins are listed before a master they need (i.e. will crash)?
export const getLoadOrderState = callable<
  [
    app_id: number,
    install_dir: string,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    game_domain: string
  ],
  {
    ok: boolean;
    supported?: boolean;
    total?: number;
    violations?: number;
    /** Masters installed but switched off, that enabled plugins need. */
    disabled_masters?: number;
    examples?: string[];
    /** Plugin slots consumed. The engine addresses plugins with one
     * byte: 254 ordinary, plus one shared index for all light ones. */
    full_slots?: number;
    full_slot_limit?: number;
    light_slots?: number;
    light_slot_limit?: number;
    /** Masters not on disk at all - usually DLC the account doesn't own.
     * `label` is the human name where we know it ("Dead Money"). */
    missing_masters?: { name: string; label?: string; needed_by: number }[];
    /** How many enabled plugins cannot load because of those. */
    blocked_plugins?: number;
    /** Enabled but not on disk - safe to delist, they cannot load. */
    ghost_plugins?: number;
    ghost_examples?: string[];
  }
>("get_load_order_state");

export const fixLoadOrder = callable<
  [
    app_id: number,
    install_dir: string,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    game_domain: string
  ],
  {
    ok: boolean;
    violations_before?: number;
    violations_after?: number;
    sorted?: number;
    enabled_masters?: number;
    removed_base_masters?: number;
    error?: string;
  }
>("fix_load_order");

/** Re-assert the skip set with its full dependency closure.
 *
 * Run after a collection finishes and when the game exits. The
 * per-install dependent check only sees a mod's own masters at install
 * time, so a mod installed BEFORE its master was skipped is never
 * reconsidered - and Skyrim rewrites Plugins.txt itself, which switched
 * two skips back on mid-run on device. */
export const enforceSkips = callable<
  [
    app_id: number,
    install_dir: string,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    game_domain: string
  ],
  { ok: boolean; changed?: number; new_dependents?: number }
>("enforce_skips");

/** Plugins already proven to break this game, so nobody has to find
 * them twice. Roots only - dependents are derived. */
export const getKnownBadState = callable<
  [
    app_id: number,
    install_dir: string,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    game_domain: string
  ],
  {
    ok: boolean;
    supported?: boolean;
    bad?: { name: string; reason: string }[];
    /** How many others cannot load without them. */
    extra?: number;
  }
>("get_known_bad_state");

export const applyKnownBad = callable<
  [
    app_id: number,
    install_dir: string,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    game_domain: string
  ],
  { ok: boolean; skipped?: number; extra?: number; error?: string }
>("apply_known_bad");

/** Automated hunt for the plugins that crash the game. Each cycle:
 * apply -> launch -> watch for a crash log -> record -> repeat. */
export const crashBisectStart = callable<
  [
    app_id: number,
    install_dir: string,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    game_domain: string,
    signature: string,
    log_subpath: string,
    keep_dlls: string[]
  ],
  {
    ok: boolean;
    total?: number;
    parked_dlls?: number;
    /** The address the hunt locked on to (auto-detected when the caller
     * passes an empty signature). */
    signature?: string;
    error?: string;
  }
>("crash_bisect_start");

export const crashBisectApply = callable<
  [],
  {
    ok: boolean;
    done?: boolean;
    testing?: number;
    enabled?: number;
    remaining?: number;
    launches?: number;
    skipped?: string[];
    error?: string;
  }
>("crash_bisect_apply");

export const crashBisectRecord = callable<
  [crashed: boolean],
  {
    ok: boolean;
    found?: string | null;
    /** Plugins skipped alongside `found` because they depend on it. */
    collateral?: string[];
    skipped?: string[];
    launches?: number;
    remaining?: number;
    done?: boolean;
    error?: string;
  }
>("crash_bisect_record");

export const crashBisectFinish = callable<
  [keep_skips: boolean],
  { ok: boolean; skipped?: string[]; restored_dlls?: number; error?: string }
>("crash_bisect_finish");

export const crashBisectStatus = callable<
  [],
  {
    ok: boolean;
    running?: boolean;
    launches?: number;
    skipped?: string[];
    remaining?: number;
    total?: number;
  }
>("crash_bisect_status");

/** Has the game reached the WORLD (not just the menu) since `after`?
 * Papyrus only logs when scripts run, and scripts run in the world. */
export const inGameSince = callable<
  [app_id: number, marker_subpath: string, after: number],
  { ok: boolean; in_game?: boolean; at?: number }
>("in_game_since");

/** Switch on the script log the save-load hunt watches for. */
export const enablePapyrusLogging = callable<
  [app_id: number, prefs_subpath: string],
  { ok: boolean; error?: string }
>("enable_papyrus_logging");

/** Newest crash report written after `after` (unix seconds), with the
 * exception address so a different crash isn't mistaken for this one. */
export const crashSince = callable<
  [app_id: number, log_subpath: string, after: number],
  {
    ok: boolean;
    crash?: { log: string; address: string; at: number } | null;
  }
>("crash_since");

/** Pause or resume EVERY download - "pause" means "stop using my
 * bandwidth". Each transfer keeps its .part and resumes with an HTTP
 * Range request, so nothing is lost across the gap. */
export const setDownloadsPaused = callable<
  [paused: boolean],
  { ok: boolean; paused?: boolean; in_flight?: number }
>("set_downloads_paused");

/** Abort one in-flight download and delete its partial file. */
export const cancelDownload = callable<
  [mod_id: number],
  { ok: boolean; error?: string }
>("cancel_download");

/** Paused state + in-flight count, so a reopened Downloads page shows
 * the truth rather than whatever it last remembered. */
export const getDownloadControl = callable<
  [],
  { ok: boolean; paused?: boolean; in_flight?: number }
>("get_download_control");

// Just the count, for sizing the "this will take a while" launch notice.
export const getInstalledCount = callable<
  [game_domain: string],
  { ok: boolean; mods?: number }
>("get_installed_count");

export const getInstalledMods = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    install_mode: InstallMode,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    hidden_folders: string[]
  ],
  InstalledResult
>("get_installed_mods");

export const setModEnabled = callable<
  [
    install_dir: string,
    mods_subdir: string,
    folder: string,
    enabled: boolean,
    install_mode: InstallMode,
    game_domain: string,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed"
  ],
  { ok: boolean; error?: string }
>("set_mod_enabled");

export const setAllModsEnabled = callable<
  [
    install_dir: string,
    mods_subdir: string,
    enabled: boolean,
    install_mode: InstallMode,
    game_domain: string,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed"
  ],
  { ok: boolean; moved?: number; errors?: string[]; error?: string }
>("set_all_mods_enabled");

export const uninstallMod = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    folder: string,
    install_mode: InstallMode,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed"
  ],
  { ok: boolean; error?: string }
>("uninstall_mod");

export const uninstallAllMods = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    protected_folders: string[],
    install_mode: InstallMode,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed"
  ],
  { ok: boolean; removed?: number; kept?: string[]; error?: string }
>("uninstall_all_mods");

export interface SaveAccount {
  account_id: string;
  vanilla_profiles: number;
  has_modded: boolean;
  last_write: number;
}

export interface SaveStatus {
  ok: boolean;
  accounts?: SaveAccount[];
  active_account?: string | null;
  game_running?: boolean;
  error?: string;
}

export const getSaveStatus = callable<
  [app_id: number, process_name: string],
  SaveStatus
>("get_save_status");

export const copySavesToModded = callable<
  [app_id: number, account_id: string, process_name: string],
  { ok: boolean; profiles?: number; backup?: string | null; error?: string }
>("copy_saves_to_modded");

export interface ModRequirement {
  modName: string;
  modId: number;
  notes?: string;
  url?: string;
}

export interface CollectionSummary {
  name: string;
  slug: string;
  summary: string;
  endorsements: number;
  author: string;
  thumbnailUrl?: string;
  modCount: number;
  totalSize: number;
}

export interface CollectionFile {
  modId: number;
  fileId: number;
  modName: string;
  fileName: string;
  version: string;
  sizeKb: number;
  optional: boolean;
  /** The mod's own game domain - collections pin cross-domain utilities
   * (e.g. Bethini Pie under "site") that can't install into this game. */
  domain?: string;
}

export interface CollectionDetail {
  /** Numeric collection id - the endorse mutation takes this, not the slug. */
  id: number;
  name: string;
  summary: string;
  author: string;
  revision?: number;
  modCount: number;
  totalSize: number;
  files: CollectionFile[];
  externals: { name: string; url: string; optional: boolean }[];
}

export const getCollections = callable<
  [
    game_domain: string,
    count: number,
    search: string,
    sort: string,
    offset: number
  ],
  { ok: boolean; collections?: CollectionSummary[]; error?: string }
>("get_collections");

export const getCollection = callable<
  [slug: string, game_domain: string],
  { ok: boolean; collection?: CollectionDetail; error?: string }
>("get_collection");

export const getModDetails = callable<
  [game_domain: string, mod_id: number],
  { ok: boolean; mod?: NexusMod; error?: string }
>("get_mod_details");

export const getEndorsement = callable<
  [game_domain: string, mod_id: number],
  { ok: boolean; status?: string; version?: string; error?: string }
>("get_endorsement");

export const setEndorsement = callable<
  [game_domain: string, mod_id: number, version: string, endorse: boolean],
  { ok: boolean; status?: string; error?: string }
>("set_endorsement");

// Collections have no viewer-endorsement field on the API yet (a PR is
// open for it), so this is deliberately one-way: pressing Endorse always
// records an endorsement. A toggle would be guesswork, and guessing wrong
// turns somebody's existing endorsement OFF.
export const endorseCollection = callable<
  [collection_id: number, endorse: boolean],
  { ok: boolean; status?: string; error?: string }
>("endorse_collection");

// Switches off mods whose master is not installed. DLC masters are
// excluded backend-side: buying the DLC is the better answer and the
// difference on device was 115 mods against 4.
export const disableBlockedPlugins = callable<
  [
    app_id: number,
    install_dir: string,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    game_domain: string
  ],
  { ok: boolean; disabled?: number; names?: string[]; error?: string }
>("disable_blocked_plugins");

// Files where the wrong mod won, judged against the collection's own
// order. Overwriting is normal - only disagreements with the curator's
// intent are reported, and mod_order is what expresses that intent.
// Always safe: a plugin with no file cannot load whatever the list says.
export const removeGhostPlugins = callable<
  [
    app_id: number,
    install_dir: string,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    game_domain: string
  ],
  { ok: boolean; removed?: number; names?: string[]; error?: string }
>("remove_ghost_plugins");

// Mods a collection ships inside its own archive - no download needed.
export const installCollectionBundles = callable<
  [
    slug: string,
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed"
  ],
  { ok: boolean; installed?: number; mods?: string[]; errors?: string[]; error?: string }
>("install_collection_bundles");

// Enable exactly the plugins the collection's manifest lists. We switch on
// every plugin in every archive; a curator picks which should be ON.
export const applyCollectionPlugins = callable<
  [
    slug: string,
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed"
  ],
  {
    ok: boolean;
    disabled?: number;
    enabled?: number;
    names_off?: string[];
    total?: number;
    limit?: number;
    error?: string;
  }
>("apply_collection_plugins");

// Mods hosted off Nexus (browse) and shipped inside the archive (bundle).
export const getCollectionExtras = callable<
  [slug: string, game_domain: string],
  {
    ok: boolean;
    browse?: {
      name: string;
      url: string;
      instructions: string;
      size: number;
      md5: string;
      optional: boolean;
    }[];
    bundle?: { name: string; folder: string; size: number; optional: boolean }[];
    error?: string;
  }
>("get_collection_extras");

// Switches off mods needing a file Nexus does not host, and restores them
// if the user fetches it. Console-first: a mod that cannot work is OFF.
export const applyKnownPrerequisites = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    slug: string
  ],
  {
    ok: boolean;
    parked?: number;
    restored?: number;
    mods?: string[];
    needs?: string[];
    error?: string;
  }
>("apply_known_prerequisites");

// Abandon a collection: only what IT installed is removed. mod_ids is what
// the run actually installed - a mod the user had already, or one from
// another collection, is left alone even though this collection lists it.
export const cancelCollectionInstall = callable<
  [
    game_domain: string,
    slug: string,
    install_dir: string,
    mods_subdir: string,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    mod_ids: number[]
  ],
  { ok: boolean; removed?: number; kept?: number; errors?: string[]; error?: string }
>("cancel_collection_install");

// Whether we know a collection cannot work on SteamOS, said before the
// download rather than after it.
export const getCollectionSupport = callable<
  [game_domain: string, slug: string],
  { ok: boolean; supported?: boolean; reason?: string; title?: string; error?: string }
>("get_collection_support");

// Whether ONE mod needs something Nexus does not host. Keyed by mod id so
// the warning reaches a user who found it by browsing, not only someone
// installing the collection it came from.
export const getModSupport = callable<
  [game_domain: string, mod_id: number],
  {
    ok: boolean;
    supported?: boolean;
    needs_name?: string;
    url?: string;
    reason?: string;
    error?: string;
  }
>("get_mod_support");

// Install a collection's "direct" mods - a plain URL the curator supplied
// rather than a Nexus file, verified against the md5 the manifest publishes.
export const installCollectionDirect = callable<
  [
    slug: string,
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed"
  ],
  {
    ok: boolean;
    installed?: number;
    names?: string[];
    skipped?: string[];
    errors?: string[];
    error?: string;
  }
>("install_collection_direct");

// Switch off mods already known not to run on the installed game build,
// before the user ever launches. The step that stops the first crash.
export const applyKnownVerdicts = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    install_mode: InstallMode,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    protected_ids: number[]
  ],
  {
    ok: boolean;
    disabled?: number;
    names?: string[];
    held?: string[];
    error?: string;
  }
>("apply_known_verdicts");

// Whether we have watched this mod fail on the build installed right now.
// Unlike the hand-written table next door, this one fills itself in.
export const getKnownModVerdict = callable<
  [game_domain: string, mod_id: number, app_id: number],
  {
    ok: boolean;
    known?: boolean;
    version?: string;
    why?: string;
    error?: string;
  }
>("get_known_mod_verdict");

// Everything wrong with a setup that the game will not mention until it
// refuses to start: mods missing their required mods, mods needing DLC that
// is not installed, and requirements hosted off Nexus.
export const getHealthCheck = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    app_id: number,
    /** Frameworks arrive through Step 1, not the mod list, so they are not
     * tracked mods - without these every SMAPI mod reads as missing SMAPI. */
    framework_ids: number[]
  ],
  {
    ok: boolean;
    checked?: number;
    needs_mods?: {
      name: string;
      mod_id?: number;
      missing?: { name: string; mod_id?: number; notes?: string }[];
    }[];
    /** Requirements a COLLECTION left out that the game has not complained
     * about. A curator omitting seven mods from a 283-mod set that boots
     * has made a decision, not a mistake - so these are shown, because
     * silence is a bug, but not counted as faults. */
    needs_mods_info?: {
      name: string;
      mod_id?: number;
      missing?: { name: string; mod_id?: number; notes?: string }[];
    }[];
    needs_dlc?: { name: string; dlc?: string[] }[];
    needs_external?: {
      name: string;
      files?: { name: string; url: string }[];
    }[];
    owned_dlc?: string[];
    /** Libraries the plugin installed on a mod's behalf before you asked.
     * Shown so a clean report cannot be mistaken for a broken check. */
    already_fixed?: { name: string; for: string }[];
    /** Mods we refuse to recommend because this device has already watched
     * them fail. The health check spent a day telling Michael to install
     * the very mod whose script was breaking his game. */
    known_bad?: {
      name: string;
      for: string;
      why: string;
      mod_id?: number;
    }[];
    /** What the game's own compiler said, last time it ran. */
    script_log?: {
      ran: boolean;
      compiled: boolean;
      /** The game has not run since the mods changed, so nothing in here
       * describes what is installed now. */
      stale?: boolean;
      failures: {
        script: string;
        kind: string;
        symbol: string;
        count: number;
        mod: string;
      }[];
      orphans: { script: string; kind: string; symbol: string }[];
      switched_off: { name: string; script: string; why: string }[];
    };
    errors?: string[];
    error?: string;
  }
>("get_health_check");

// The installed mods the game's last session blamed. Reads only - its job
// is to tell checkUpdates which collection-pinned mods have earned a look.
export const getBlamedFolders = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    game_user_dir: string
  ],
  { ok: boolean; folders?: string[]; error?: string }
>("get_blamed_folders");

// Switch off the mods that are broken beyond argument, and report what is
// left for the user to decide on. Acts once per session log, so a mod the
// user deliberately switches back on stays on.
export const repairFailingMods = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    game_user_dir: string,
    install_mode: InstallMode,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    protected_ids: number[]
  ],
  {
    ok: boolean;
    repaired?: number;
    names?: string[];
    held?: string[];
    /** Blamed mods that could not be switched off (other mods need them)
     * and were updated instead - the only remedy they have. */
    updated?: { name: string; from: string; to: string }[];
    /** Libraries installed because a mod asked for them and they were not
     * there. `for` is the mod that wanted it. */
    installed_deps?: { name: string; for: string }[];
    /** Blamed, cannot be switched off, and nothing newer exists - a dead
     * end only the mod's author can clear. */
    no_update?: string[];
    remaining?: { name: string; why: string }[];
    /** Every mod the log blamed, however it was handled. Fed back into
     * checkUpdates: a collection pin the game cannot run has earned an
     * update check even though a curator chose it. */
    blamed_folders?: string[];
    error?: string;
  }
>("repair_failing_mods");

// Switch off the mods the game's own last session blamed for errors. Only
// direct culprits - the first stack frame - never the libraries beneath.
export const disableFailingMods = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    game_user_dir: string,
    install_mode: InstallMode,
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    /** Ecosystem libraries other mods sit on. Reported, never switched
     * off - taking BaseLib down takes 21 working mods with it. */
    protected_ids: number[],
    /** Same answer without touching anything. The panel row is built from
     * this so it cannot disagree with what the button does. */
    dry_run: boolean
  ],
  {
    ok: boolean;
    disabled?: number;
    names?: string[];
    details?: { name: string; why: string }[];
    held?: string[];
    errors?: string[];
    error?: string;
  }
>("disable_failing_mods");

export const getFileConflicts = callable<
  [game_domain: string, mod_order: number[]],
  {
    ok: boolean;
    conflicts?: { actual: string; intended: string; files: number; example: string }[];
    files?: number;
    pairs?: number;
    /** Mods to reinstall, already in collection order. */
    resolve?: number[];
    error?: string;
  }
>("get_file_conflicts");

// Rewrites each contested file from the mod the collection wanted to own
// it, and nothing else. Per PATH, not per mod: reinstalling whole mods is
// what took the device from 47 wrong pairs to 92.
export const resolveFileConflicts = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    mod_order: number[],
    files: string[]
  ],
  {
    ok: boolean;
    rewritten?: number;
    mods?: number;
    errors?: string[];
    error?: string;
  }
>("resolve_file_conflicts");

export const getModRequirements = callable<
  [game_domain: string, mod_id: number],
  { ok: boolean; requirements?: ModRequirement[]; error?: string }
>("get_mod_requirements");

export interface ModLoadState {
  state: "loaded" | "error";
  detail: string;
}

export const getModLoadStatus = callable<
  [game_user_dir: string],
  {
    ok: boolean;
    available?: boolean;
    modded_session?: boolean;
    status?: Record<string, ModLoadState>;
    error?: string;
  }
>("get_mod_load_status");

export const getSmapiLoadStatus = callable<
  [config_dir_name: string],
  {
    ok: boolean;
    available?: boolean;
    modded_session?: boolean;
    status?: Record<string, ModLoadState>;
    error?: string;
  }
>("get_smapi_load_status");

export const getDebugInfo = callable<
  [game_user_dir: string, smapi_config_dir: string],
  {
    ok: boolean;
    plugin_log?: string;
    game_log_mod_lines?: string;
    game_log_tail?: string;
    error?: string;
  }
>("get_debug_info");

// ---- Free-user groundwork (nxm:// relay - see docs/free-user-design.md) ----

export interface NxmEntry {
  game_domain: string;
  mod_id: number;
  file_id: number;
  key: string;
  expires: string;
  user_id: string;
}

export const registerNxmHandler = callable<
  [],
  { ok: boolean; tools?: Record<string, boolean>; error?: string }
>("register_nxm_handler");

export const unregisterNxmHandler = callable<
  [],
  { ok: boolean; removed?: boolean; error?: string }
>("unregister_nxm_handler");

export const getNxmQueue = callable<
  [clear: boolean],
  { ok: boolean; raw?: string[]; entries?: NxmEntry[]; error?: string }
>("get_nxm_queue");

export const checkDocsFile = callable<
  [app_id: number, subpath: string],
  { ok: boolean; exists?: boolean; error?: string }
>("check_docs_file");

export const checkGameFile = callable<
  [install_dir: string, rel_path: string],
  { ok: boolean; exists?: boolean; error?: string }
>("check_game_file");

export interface UserPrefs {
  /** Concurrent collection downloads (1-8, default 4). */
  parallel_downloads: number;
  /** Archives buffered ahead of the serial installer (2-16, default 8). */
  prefetch_window: number;
  /** Mods extracted ahead of the serial installer (0-4, default 2).
   * Extraction is the CPU-bound half of an install and shares nothing,
   * so it overlaps safely; 0 restores strictly-serial installs. */
  extract_ahead: number;
  /** Total download cap in MB/s shared across streams; 0 = unlimited. */
  speed_cap_mbps: number;
  /** Downloads pause when free disk falls below this many GB. */
  min_free_gb: number;
  /** Browse language: 'english' hides tagged translations, 'all' shows
   * everything, a specific tag (e.g. 'French') shows only those. */
  mod_language: string;
}

export const getDiskUsage = callable<
  [],
  {
    ok: boolean;
    total_gb?: number;
    free_gb?: number;
    min_free_gb?: number;
    error?: string;
  }
>("get_disk_usage");

export const getUserPrefs = callable<
  [],
  { ok: boolean; prefs?: UserPrefs; error?: string }
>("get_user_prefs");

export const setUserPrefs = callable<
  [prefs: Partial<UserPrefs>],
  { ok: boolean; prefs?: UserPrefs; error?: string }
>("set_user_prefs");

// Downloads a mod file into the archive cache without installing - the
// collection pipeline runs several concurrently ahead of the serial
// installer so the network never idles during extract/install.
export const prefetchModFile = callable<
  [game_domain: string, mod_id: number, file_id: number, file_name: string],
  { ok: boolean; path?: string; error?: string }
>("prefetch_mod_file");

// Enabled plugins whose master files are absent from the data folder -
// the engine refuses to boot on these ("X.esm is missing required files").
export const checkPluginMasters = callable<
  [
    install_dir: string,
    mods_subdir: string,
    app_id: number,
    plugins_subpath: string,
    plugins_style: string
  ],
  {
    ok: boolean;
    broken?: { plugin: string; missing: string[] }[];
    error?: string;
  }
>("check_plugin_masters");

// Deactivates plugins (plugins.txt lines removed; files stay) so the
// game boots again after a missing-masters diagnosis.
export const disablePlugins = callable<
  [
    app_id: number,
    plugins_subpath: string,
    plugins_style: string,
    plugin_names: string[]
  ],
  { ok: boolean; disabled?: number; error?: string }
>("disable_plugins");

// Downloads a Windows modding tool from Nexus Mods and runs it inside
// the game's Proton prefix (exe patchers: FO3's ESM/Anniversary
// patchers). Success = the files the tool exists to modify changed.
export const runPrefixTool = callable<
  [
    game_domain: string,
    mod_id: number,
    install_dir: string,
    app_id: number,
    exe_hint: string,
    avoid_file_keywords: string[],
    verify_changed: string[],
    timeout_sec: number
  ],
  {
    ok: boolean;
    changed?: string[];
    timed_out?: boolean;
    rc?: number;
    output?: string;
    /** Which phase bailed (auth/game/proton/prefix/files/pick/download) */
    stage?: string;
    already_applied?: boolean;
    error?: string;
  }
>("run_prefix_tool");

export interface PrefixToolFailure {
  ok: false;
  stage: string;
  message: string;
  at: number;
}

// me3: the FromSoft mod loader (Elden Ring, DS3, Sekiro, AC6,
// Nightreign). Native Linux binary, kept as our own copy; it launches
// the game past EasyAntiCheat and offline-by-default.
export const getMe3Status = callable<
  [],
  {
    ok: boolean;
    installed?: boolean;
    version?: string;
    info?: string;
    error?: string;
  }
>("get_me3_status");

export const installMe3 = callable<
  [],
  { ok: boolean; version?: string; error?: string }
>("install_me3");

export interface Me3State {
  ok: boolean;
  installed: boolean;
  version?: string;
  error?: string;
  game_installed?: boolean;
  /** Proton builds present in the Steam library */
  protons?: string[];
  /** me3's fallback runtime for Elden Ring when Steam maps none */
  proton8?: boolean;
  /** Proton Steam has mapped for this app (or the global default).
   * Empty means Steam chose one implicitly and wrote nothing down, so
   * me3 has to fall back - see proton8. */
  compat_tool?: string;
  profile_path?: string;
  profile_exists?: boolean;
  mods?: number;
  natives?: number;
  /** Mod currently owning regulation.bin, if any */
  regulation_owner?: string | null;
  coop_installed?: boolean;
}

export const getMe3State = callable<
  [game_domain: string, install_dir: string, app_id: number],
  Me3State
>("get_me3_state");

// Rebuilds the profile and returns the Steam launch command that boots
// the game through me3 (offline, modded saves kept separate).
export const getMe3LaunchCommand = callable<
  [game_domain: string],
  { ok: boolean; command?: string; profile_path?: string; error?: string }
>("get_me3_launch_command");

export const getMe3CoopPassword = callable<
  [game_domain: string],
  { ok: boolean; installed?: boolean; password?: string }
>("get_me3_coop_password");

export const setMe3CoopPassword = callable<
  [game_domain: string, password: string],
  { ok: boolean; error?: string }
>("set_me3_coop_password");

export const getPrefixToolsState = callable<
  [game_domain: string],
  {
    ok: boolean;
    done?: Record<number, boolean>;
    /** Persisted last failure per tool - toasts vanish too fast to read */
    last?: Record<number, PrefixToolFailure>;
    /** Tools the user chose to skip */
    skipped?: Record<number, boolean>;
  }
>("get_prefix_tools_state");

export const skipPrefixTools = callable<
  [game_domain: string, mod_ids: number[], skipped: boolean],
  { ok: boolean }
>("skip_prefix_tools");

// Copies a game-dir default ini into the prefix Documents when missing
// (FO3's launcher hangs under Proton before creating FALLOUT.INI).
export const seedGameIni = callable<
  [install_dir: string, app_id: number, source_rel: string, prefs_subpath: string],
  { ok: boolean; seeded?: boolean; error?: string }
>("seed_game_ini");

// Upgrades the game prefix's VC++ runtime from the newest installed
// Proton's bundled copy (idempotent). CP77's install script downgrades
// the prefix CRT below what CET/RED4ext need (error 998 at boot).
export interface ScriptExtenderPlugin {
  name: string;
  reason: string;
  /** Built for an older game version - only its author can fix it. */
  outdated: boolean;
}

/** A mod DLL that was on the call stack when the game last crashed. */
export interface CrashCulprit {
  name: string;
  /** Stack depth: 0 is where it died, so lower is stronger evidence. */
  frame: number;
  /** A real stack frame, as opposed to a stack-scan guess. */
  probable: boolean;
}

export interface CrashReport {
  culprits?: CrashCulprit[];
  crashed_at?: string;
  log?: string;
}

// DLL plugins the script extender refused to load last launch, plus
// anything implicated in a crash since - two different failures with the
// same fix, so they arrive together.
export const getScriptExtenderState = callable<
  [app_id: number, install_dir: string, log_subpath: string],
  {
    ok: boolean;
    available?: boolean;
    failed?: ScriptExtenderPlugin[];
    parked?: string[];
    plugins_dir?: string;
    crash?: CrashReport;
    log_at?: number;
  }
>("get_script_extender_state");

// Park a DLL plugin (rename, never delete) or bring it back.
export const setScriptExtenderPlugins = callable<
  [install_dir: string, plugins_dir: string, names: string[], enabled: boolean],
  { ok: boolean; changed?: number; errors?: string[]; error?: string }
>("set_script_extender_plugins");

// Read-only: is the prefix's VC++ runtime older than the newest Proton's?
export const getPrefixRuntimeState = callable<
  [app_id: number],
  {
    ok: boolean;
    prefix_exists?: boolean;
    have?: string;
    newest?: string;
    outdated?: boolean;
  }
>("get_prefix_runtime_state");

export const fixPrefixRuntime = callable<
  [app_id: number],
  {
    ok: boolean;
    updated?: boolean;
    version?: string;
    previous?: string;
    error?: string;
  }
>("fix_prefix_runtime");

export const getShowAdult = callable<
  [],
  {
    ok: boolean;
    show_adult?: boolean;
    adult_pref?: boolean;
    age_verified?: boolean;
    blur_adult?: boolean;
  }
>("get_show_adult");
export const setShowAdult = callable<
  [value: boolean],
  { ok: boolean }
>("set_show_adult");
// Re-reads the account's adult preference + age-verification status from
// the Nexus Mods API and caches it backend-side. Called on QAM mount and
// after sign-in; the gate is account-driven with no local override.
export const refreshContentGate = callable<
  [],
  {
    ok: boolean;
    show_adult?: boolean;
    adult_pref?: boolean;
    age_verified?: boolean;
    error?: string;
  }
>("refresh_content_gate");

export const setApiKey = callable<[api_key: string], AuthStatus>("set_api_key");
export const getAuthStatus = callable<[], AuthStatus>("get_auth_status");
export const getGameStatus = callable<
  [install_dir: string, mods_subdir: string, framework_file: string],
  GameStatus
>("get_game_status");
