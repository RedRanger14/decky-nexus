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
  pictureUrl?: string;
  updatedAt: string;
  adultContent: boolean;
  /** Full description (bbcode/html soup) - present via getModDetails */
  description?: string;
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
}

export interface InstalledResult {
  ok: boolean;
  mods?: InstalledMod[];
  error?: string;
}

export interface InstallProgress {
  mod_id: number;
  phase: "downloading" | "extracting" | "done" | "error";
  percent: number;
  message?: string;
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
    record_source: string
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

export const getFrameworkSetup = callable<
  [game_domain: string],
  { ok: boolean; launch_options_set?: boolean; enabled?: boolean }
>("get_framework_setup");

export const markLaunchOptionsSet = callable<
  [game_domain: string],
  { ok: boolean; error?: string }
>("mark_launch_options_set");

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
  [game_domain: string, count: number, search: string],
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

export const getShowAdult = callable<
  [],
  { ok: boolean; show_adult?: boolean }
>("get_show_adult");
export const setShowAdult = callable<
  [value: boolean],
  { ok: boolean }
>("set_show_adult");

export const setApiKey = callable<[api_key: string], AuthStatus>("set_api_key");
export const getAuthStatus = callable<[], AuthStatus>("get_auth_status");
export const getGameStatus = callable<
  [install_dir: string, mods_subdir: string, framework_file: string],
  GameStatus
>("get_game_status");
