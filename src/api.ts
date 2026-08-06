// Typed bridge to the Python backend (main.py). All callables are positional.
import { callable } from "@decky/api";

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
  phase: "downloading" | "extracting" | "done" | "error";
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
}

export const checkUpdates = callable<
  [game_domain: string],
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
    install_mode: "folder" | "dataDir",
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
    pakpatch_layout: boolean
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
    install_mode: "folder" | "dataDir",
    app_id: number,
    plugins_subpath: string,
    plugins_style: "starred" | "listed",
    framework_file_prefixes: string[],
    witcher_layout: boolean
  ],
  {
    ok: boolean;
    removed?: number;
    framework_files?: string[];
    cleared_dlo?: boolean;
    use_steam_client?: boolean;
    errors?: string[];
    error?: string;
  }
>("reset_game_modding");

export const uninstallCollection = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    install_mode: "folder" | "dataDir",
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
  [game_domain: string],
  { ok: boolean; launch_options_set?: boolean; enabled?: boolean }
>("get_framework_setup");

export const markLaunchOptionsSet = callable<
  [game_domain: string],
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
    install_subdir: string
  ],
  { ok: boolean; install_path?: string; error?: string }
>("install_framework");

export const getInstalledMods = callable<
  [
    game_domain: string,
    install_dir: string,
    mods_subdir: string,
    install_mode: "folder" | "dataDir",
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
    install_mode: "folder" | "dataDir",
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
    install_mode: "folder" | "dataDir",
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
    install_mode: "folder" | "dataDir",
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
    install_mode: "folder" | "dataDir",
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
  { ok: boolean; status?: string; error?: string }
>("get_endorsement");

export const setEndorsement = callable<
  [game_domain: string, mod_id: number, version: string, endorse: boolean],
  { ok: boolean; status?: string; error?: string }
>("set_endorsement");

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

export const getPrefixToolsState = callable<
  [game_domain: string],
  { ok: boolean; done?: Record<number, boolean> }
>("get_prefix_tools_state");

// Copies a game-dir default ini into the prefix Documents when missing
// (FO3's launcher hangs under Proton before creating FALLOUT.INI).
export const seedGameIni = callable<
  [install_dir: string, app_id: number, source_rel: string, prefs_subpath: string],
  { ok: boolean; seeded?: boolean; error?: string }
>("seed_game_ini");

// Upgrades the game prefix's VC++ runtime from the newest installed
// Proton's bundled copy (idempotent). CP77's install script downgrades
// the prefix CRT below what CET/RED4ext need (error 998 at boot).
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
