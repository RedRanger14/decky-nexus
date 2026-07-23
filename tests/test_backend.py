"""Backend tests. Stdlib-only (unittest) so they run anywhere Python does:

    python -m unittest discover -s tests -v

The decky and aiohttp modules are stubbed before importing main.py; all
filesystem paths point into a per-run temp directory. Network is disabled -
anything that would hit the Nexus Mods API raises immediately.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import zipfile

TEST_ROOT = tempfile.mkdtemp(prefix="decky-nexus-tests-")


def _make_decky_stub():
    import logging

    d = types.ModuleType("decky")
    home = os.path.join(TEST_ROOT, "home")
    d.DECKY_USER_HOME = home
    d.DECKY_HOME = os.path.join(home, "homebrew")
    d.DECKY_PLUGIN_SETTINGS_DIR = os.path.join(TEST_ROOT, "settings")
    d.DECKY_PLUGIN_RUNTIME_DIR = os.path.join(TEST_ROOT, "runtime")
    d.DECKY_PLUGIN_LOG_DIR = os.path.join(TEST_ROOT, "logs")
    d.DECKY_PLUGIN_DIR = os.path.join(TEST_ROOT, "plugin")
    d.DECKY_PLUGIN_NAME = "Nexus Mods"
    d.DECKY_PLUGIN_VERSION = "0.0.0-test"
    d.DECKY_PLUGIN_AUTHOR = "test"
    d.DECKY_VERSION = "0.0.0-test"
    d.DECKY_USER = "deck"
    d.logger = logging.getLogger("decky-test")

    async def emit(event, *args):
        pass

    d.emit = emit
    return d


def _make_aiohttp_stub():
    m = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientTimeout:
        def __init__(self, **kwargs):
            pass

    class ClientSession:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("network is disabled in tests")

    m.ClientError = ClientError
    m.ClientTimeout = ClientTimeout
    m.ClientSession = ClientSession
    return m


sys.modules.setdefault("decky", _make_decky_stub())
sys.modules.setdefault("aiohttp", _make_aiohttp_stub())
for sub in ("home", "settings", "runtime", "logs"):
    os.makedirs(os.path.join(TEST_ROOT, sub), exist_ok=True)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
import main  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def make_file(file_id, category, name, primary=False, version="1.0"):
    return {
        "file_id": file_id,
        "name": name,
        "file_name": f"{name}.zip",
        "version": version,
        "size_kb": 1,
        "category_name": category,
        "is_primary": primary,
        "description": "",
    }


class TestFileSelection(unittest.TestCase):
    """Regression: SMAPI's Nexus file list had is_primary stuck on a 2020
    OLD_VERSION file, which made the framework installer download a
    six-year-old build."""

    def test_pick_main_ignores_stale_primary_old_version(self):
        files = [
            make_file(25316, "OLD_VERSION", "SMAPI 3.4.1", primary=True),
            make_file(160380, "MAIN", "SMAPI 4.5.2"),
        ]
        self.assertEqual(main._pick_main_file(files)["file_id"], 160380)

    def test_pick_main_prefers_latest_main_by_file_id(self):
        files = [
            make_file(10, "MAIN", "older main"),
            make_file(20, "MAIN", "newer main"),
        ]
        self.assertEqual(main._pick_main_file(files)["file_id"], 20)

    def test_pick_main_falls_back_to_any_current_file(self):
        files = [
            make_file(1, "OLD_VERSION", "ancient"),
            make_file(2, "OPTIONAL", "optional thing"),
        ]
        self.assertEqual(main._pick_main_file(files)["file_id"], 2)

    def test_avoid_keywords_skip_other_store_builds(self):
        """Regression: SKSE's page hosts Steam AND GOG builds as MAIN files;
        the GOG one was uploaded later (higher file_id) and won the
        latest-MAIN rule, then refused to run against the Steam game."""
        files = [
            make_file(462377, "MAIN", "Skyrim Script Extender (SKSE64)  Steam", primary=True),
            make_file(470991, "MAIN", "Skyrim Script Extender (SKSE64) GOG"),
        ]
        # Without the filter the GOG build wins - that's the bug.
        self.assertEqual(main._pick_main_file(files)["file_id"], 470991)
        picked = main._pick_main_file(files, ["GOG"])
        self.assertEqual(picked["file_id"], 462377)

    def test_avoid_keywords_match_file_name_too(self):
        f = make_file(1, "MAIN", "Framework")
        f["file_name"] = "Framework-GOG-1.0.7z"
        self.assertIsNone(main._pick_main_file([f], ["gog"]))

    def test_avoid_keywords_exhausting_all_files_returns_none(self):
        files = [make_file(1, "MAIN", "Only GOG build")]
        self.assertIsNone(main._pick_main_file(files, ["GOG"]))

    def test_pick_main_returns_none_when_only_old_versions(self):
        files = [make_file(1, "OLD_VERSION", "ancient", primary=True)]
        self.assertIsNone(main._pick_main_file(files))

    def test_sort_demotes_stale_primary_old_version(self):
        files = [
            make_file(25316, "OLD_VERSION", "SMAPI 3.4.1", primary=True),
            make_file(160380, "MAIN", "SMAPI 4.5.2"),
            make_file(99, "OPTIONAL", "extra"),
        ]
        ordered = main._sort_mod_files(files)
        self.assertEqual(ordered[0]["file_id"], 160380)
        self.assertEqual(ordered[-1]["file_id"], 25316)


class TestModsQueryBuilder(unittest.TestCase):
    def test_base_query_uses_sort_variable(self):
        q = main._build_mods_query(with_search=False)
        self.assertIn("$sort: [ModsSort!]", q)
        self.assertIn("sort: $sort", q)
        self.assertNotIn("createdAt:", q)
        self.assertNotIn("$search", q)

    def test_search_adds_wildcard_filter(self):
        q = main._build_mods_query(with_search=True)
        self.assertIn("op: WILDCARD", q)
        self.assertIn("$search: String!", q)

    def test_trending_filters_by_epoch_and_sorts_by_downloads(self):
        q = main._build_mods_query(with_search=False, trending_since=1781740800)
        # date filters must be epoch seconds (ISO datetimes break the
        # backing Lucene query - verified against the live API)
        self.assertIn('createdAt: [{ value: "1781740800", op: GT }]', q)
        self.assertIn("downloads: { direction: DESC }", q)
        self.assertNotIn("$sort", q)

    def test_trending_composes_with_search(self):
        q = main._build_mods_query(with_search=True, trending_since=123)
        self.assertIn("op: WILDCARD", q)
        self.assertIn('value: "123"', q)

    def test_v1_mod_mapping(self):
        mapped = main._map_v1_mod(
            {
                "mod_id": 5,
                "name": "X",
                "endorsement_count": 7,
                "mod_downloads": 9,
                "picture_url": "http://p",
                "contains_adult_content": False,
            }
        )
        self.assertEqual(mapped["modId"], 5)
        self.assertEqual(mapped["endorsements"], 7)
        self.assertEqual(mapped["downloads"], 9)
        self.assertEqual(mapped["pictureUrl"], "http://p")


class TestModLoadLogParsing(unittest.TestCase):
    """Lines lifted from a real StS2 session log on the test device."""

    LINES = [
        "[INFO] Found mod manifest file /x/mods/ATA_IronClad/mod_manifest.json",
        "[INFO] Finished mod initialization for 'BaseLib' (BaseLib).",
        "[INFO] Finished mod initialization for 'Watcher' (Watcher).",
        "[INFO] Finished mod initialization for '机娘' (ATA_IronClad).",
        "[ERROR] [ATA-IronClad] Initialization failed: Patching exception in method X",
        "[ERROR] Tried to load mod with id ATA_IronClad, but a mod is already loaded with that name!",
        "[INFO]  --- RUNNING MODDED! --- Loaded 4 mods (5 total)",
    ]

    def test_parses_states_with_dash_underscore_normalization(self):
        status, modded = main._parse_mod_load_log(self.LINES)
        self.assertTrue(modded)
        self.assertEqual(status["baselib"]["state"], "loaded")
        self.assertEqual(status["watcher"]["state"], "loaded")
        # errors override 'finished initialization', and the [ATA-IronClad]
        # log tag must map onto the ATA_IronClad folder id
        self.assertEqual(status["ataironclad"]["state"], "error")
        self.assertIn("Patching exception", status["ataironclad"]["detail"])

    def test_no_modded_session(self):
        status, modded = main._parse_mod_load_log(
            ["[INFO] Finished mod initialization for 'X' (X)."]
        )
        self.assertFalse(modded)
        self.assertEqual(status["x"]["state"], "loaded")


class TestSmapiLogParsing(unittest.TestCase):
    """Lines lifted from the real SMAPI-latest.txt on the test device
    (SMAPI 4.5.2, ~/.config/StardewValley/ErrorLogs/)."""

    LINES = [
        "[16:43:06 INFO  SMAPI] SMAPI 4.5.2 with Stardew Valley 1.6.15 build 24356 on Unix 6.16.12.24",
        "[16:43:10 INFO  SMAPI] Loaded 5 mods:",
        "[16:43:10 INFO  SMAPI]    CJB Cheats Menu 1.42.0 by CJBok and Pathoschild | Simple in-game cheats menu!",
        "[16:43:10 INFO  SMAPI]    Console Commands 4.5.2 by SMAPI | Adds SMAPI console commands that let you manipulate the game.",
        "[16:43:10 INFO  SMAPI]    Content Patcher 2.9.1 by Pathoschild | Loads content packs which edit game data, images, and maps without changing the game files.",
        "[16:43:10 INFO  SMAPI]    Save Backup 4.5.2 by SMAPI | Automatically backs up all your saves once per day into its folder.",
        "",
        "[16:43:10 TRACE SMAPI]    Direct console access",
        "[16:43:11 ERROR SMAPI] Skipped mods",
        "[16:43:11 ERROR SMAPI] --------------------------------------------------",
        "[16:43:11 ERROR SMAPI]    These mods could not be added to your game.",
        "[16:43:11 ERROR SMAPI]       - Farm Type Manager 1.16.0 because it's no longer compatible.",
    ]

    def test_loaded_mods_map_to_folder_norms(self):
        status, modded = main._parse_smapi_log(self.LINES)
        self.assertTrue(modded)
        # 'CJB Cheats Menu' (log display name) must match folder 'CJBCheatsMenu'
        self.assertEqual(status["cjbcheatsmenu"]["state"], "loaded")
        self.assertEqual(status["consolecommands"]["state"], "loaded")
        self.assertEqual(status["contentpatcher"]["state"], "loaded")
        self.assertEqual(status["savebackup"]["state"], "loaded")

    def test_skipped_mods_become_errors_with_reason(self):
        status, _ = main._parse_smapi_log(self.LINES)
        self.assertEqual(status["farmtypemanager"]["state"], "error")
        self.assertIn("no longer compatible", status["farmtypemanager"]["detail"])

    def test_vanilla_log_reports_no_modded_session(self):
        status, modded = main._parse_smapi_log(
            ["[10:00:00 INFO  SMAPI] SMAPI 4.5.2 with Stardew Valley 1.6.15"]
        )
        self.assertFalse(modded)
        self.assertEqual(status, {})


class TestSmapiLoadStatusEndToEnd(unittest.TestCase):
    def test_reads_log_from_config_dir(self):
        log_dir = os.path.join(
            main.decky.DECKY_USER_HOME, ".config", "TestValley", "ErrorLogs"
        )
        os.makedirs(log_dir, exist_ok=True)
        with open(
            os.path.join(log_dir, "SMAPI-latest.txt"), "w", encoding="utf-8"
        ) as f:
            f.write(
                "[10:00:00 INFO  SMAPI] Loaded 1 mods:\n"
                "[10:00:00 INFO  SMAPI]    Cool Mod 1.0.0 by Someone | Does things.\n"
            )
        result = run(main.Plugin().get_smapi_load_status("TestValley"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["available"])
        self.assertTrue(result["modded_session"])
        self.assertEqual(result["status"]["coolmod"]["state"], "loaded")

    def test_missing_log_reports_unavailable(self):
        result = run(main.Plugin().get_smapi_load_status("NoSuchValley"))
        self.assertTrue(result["ok"])
        self.assertFalse(result["available"])

    def test_rejects_bad_dir(self):
        result = run(main.Plugin().get_smapi_load_status("../../etc"))
        self.assertFalse(result["ok"])


class TestHelpers(unittest.TestCase):
    def test_safe_name_strips_unsafe_characters(self):
        self.assertEqual(main._safe_name("Iron/clad:铁甲"), "Ironclad")
        self.assertEqual(main._safe_name("...hidden"), "hidden")
        self.assertEqual(main._safe_name("铁甲"), "mod")

    def test_norm_version(self):
        self.assertEqual(main._norm_version("v0.7"), "0.7")
        self.assertEqual(main._norm_version(" V1.2.3 "), "1.2.3")
        self.assertEqual(main._norm_version(None), "")

    def test_app_version_matches_package_json(self):
        with open(os.path.join(REPO_ROOT, "package.json"), encoding="utf-8") as f:
            pkg = json.load(f)
        self.assertEqual(main.APP_HEADERS["Application-Version"], pkg["version"])
        self.assertEqual(main.APP_HEADERS["Application-Name"], "decky-nexus")


class TestCallableArity(unittest.TestCase):
    """The frontend's api.ts callables and the backend's method signatures
    must agree on argument counts - decky binds positionally, and a
    mismatch kills dispatch BEFORE our error handling ever runs (the
    'Python Exception on every install' regression)."""

    @staticmethod
    def _count_ts_args(blob: str) -> int:
        depth = 0
        count = 0
        for ch in blob:
            if ch in "<[({":
                depth += 1
            elif ch in ">])}":
                depth -= 1
            elif ch == "," and depth == 0:
                count += 1
        return count + 1 if blob.strip() else 0

    def test_every_api_ts_callable_fits_its_backend_signature(self):
        import inspect
        import re as _re

        src = open(
            os.path.join(REPO_ROOT, "src", "api.ts"), encoding="utf-8"
        ).read()
        pattern = _re.compile(
            r"callable<\s*\[(.*?)\]\s*,.*?>\(\s*\"([a-z0-9_]+)\"\s*\)",
            _re.S,
        )
        checked = 0
        for m in pattern.finditer(src):
            blob, name = m.group(1), m.group(2)
            ts_args = self._count_ts_args(blob)
            method = getattr(main.Plugin, name, None)
            self.assertIsNotNone(method, f"api.ts calls unknown method {name}")
            params = [
                p
                for p in inspect.signature(method).parameters.values()
                if p.name != "self"
            ]
            required = sum(
                1 for p in params if p.default is inspect.Parameter.empty
            )
            self.assertGreaterEqual(
                ts_args, required,
                f"{name}: api.ts sends {ts_args} args but backend requires "
                f"{required}",
            )
            self.assertLessEqual(
                ts_args, len(params),
                f"{name}: api.ts sends {ts_args} args but backend accepts "
                f"at most {len(params)}",
            )
            checked += 1
        # Sanity: the scan actually found the callables.
        self.assertGreater(checked, 20, f"only matched {checked} callables")


class GameDirTestCase(unittest.TestCase):
    """Base for tests that need a fake game install under STEAM_COMMON."""

    GAME = "Test Game"

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        self.mods = os.path.join(self.install, "mods")
        self.disabled = os.path.join(self.install, "mods-disabled")
        shutil.rmtree(self.install, ignore_errors=True)
        os.makedirs(self.mods)
        # isolate settings between tests
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.plugin = main.Plugin()

    def add_mod(self, folder, enabled=True):
        base = self.mods if enabled else self.disabled
        path = os.path.join(base, folder)
        os.makedirs(path)
        with open(os.path.join(path, "dummy.txt"), "w") as f:
            f.write("x")


class TestEnableDisable(GameDirTestCase):
    def test_toggle_moves_folder(self):
        self.add_mod("CoolMod")
        result = run(
            self.plugin.set_mod_enabled(self.GAME, "mods", "CoolMod", False)
        )
        self.assertTrue(result["ok"])
        self.assertFalse(os.path.isdir(os.path.join(self.mods, "CoolMod")))
        self.assertTrue(os.path.isdir(os.path.join(self.disabled, "CoolMod")))

    def test_rejects_path_traversal(self):
        for evil in ("../evil", "a/b", ".."):
            result = run(
                self.plugin.set_mod_enabled(self.GAME, "mods", evil, False)
            )
            self.assertFalse(result["ok"], evil)

    def test_installed_list_sorted_alphabetically_regardless_of_state(self):
        self.add_mod("Zeta")
        self.add_mod("Alpha", enabled=False)
        self.add_mod("Mid")
        result = run(self.plugin.get_installed_mods("testgame", self.GAME, "mods"))
        names = [m["folder"] for m in result["mods"]]
        self.assertEqual(names, ["Alpha", "Mid", "Zeta"])


class TestUninstall(GameDirTestCase):
    def test_uninstall_all_keeps_protected_case_insensitively(self):
        self.add_mod("UserMod")
        self.add_mod("SaveBackup")
        self.add_mod("consolecommands")
        self.add_mod("DisabledMod", enabled=False)
        result = run(
            self.plugin.uninstall_all_mods(
                "testgame", self.GAME, "mods", ["SaveBackup", "ConsoleCommands"]
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["removed"], 2)
        self.assertTrue(os.path.isdir(os.path.join(self.mods, "SaveBackup")))
        self.assertTrue(os.path.isdir(os.path.join(self.mods, "consolecommands")))
        self.assertFalse(os.path.isdir(os.path.join(self.mods, "UserMod")))
        self.assertFalse(os.path.isdir(os.path.join(self.disabled, "DisabledMod")))

    def test_uninstall_single_rejects_traversal(self):
        result = run(
            self.plugin.uninstall_mod("testgame", self.GAME, "mods", "../oops")
        )
        self.assertFalse(result["ok"])

    def test_uninstall_removes_read_only_content(self):
        self.add_mod("Stubborn")
        target = os.path.join(self.mods, "Stubborn")
        os.chmod(target, 0o555)  # read-only dir (no-op on Windows, real on Linux)
        result = run(
            self.plugin.uninstall_mod("testgame", self.GAME, "mods", "Stubborn")
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertFalse(os.path.isdir(target))


class TestUe4ssRouting(unittest.TestCase):
    """UE4SS mods route to the loader's dirs: Lua/native mods as folders
    under ue4ss/Mods (with an enabled.txt drop-file), Blueprint paks flat
    into LogicMods."""

    GAME = "Palworld Test"
    UE4SS = "Pal/Binaries/Win64/ue4ss/Mods"
    LOGIC = "Pal/Content/Paks/LogicMods"

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        os.makedirs(self.install)
        self.scratch = os.path.join(TEST_ROOT, "ue4ss-scratch")
        shutil.rmtree(self.scratch, ignore_errors=True)
        os.makedirs(self.scratch)
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.plugin = main.Plugin()

    def put(self, rel):
        p = os.path.join(self.scratch, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("x")

    def seed_record(self, key, route):
        settings = main._load_settings()
        settings.setdefault("installed", {}).setdefault("palworld", {})[key] = {
            "mod_id": 1, "name": key, "version": "1.0", **route,
        }
        main._save_settings(settings)

    def test_lua_mod_routes_to_ue4ss_mods_with_enabled_marker(self):
        self.put("MapUnlocker/Scripts/main.lua")
        route = main._route_ue4ss_payload(
            self.scratch, self.install, self.UE4SS, self.LOGIC, "Map Unlocker"
        )
        self.assertEqual(route["mode"], "folder")
        self.assertEqual(route["target"], self.UE4SS)
        dst = os.path.join(self.install, *self.UE4SS.split("/"), "MapUnlocker")
        self.assertTrue(os.path.isfile(os.path.join(dst, "Scripts", "main.lua")))
        self.assertTrue(os.path.isfile(os.path.join(dst, "enabled.txt")))

    def test_native_dll_mod_routes_as_folder(self):
        self.put("TinyFollowers50/dlls/main.dll")
        self.put("TinyFollowers50/enabled.txt")
        route = main._route_ue4ss_payload(
            self.scratch, self.install, self.UE4SS, self.LOGIC, "Tiny Followers"
        )
        self.assertEqual(route["folder"], "TinyFollowers50")
        dst = os.path.join(
            self.install, *self.UE4SS.split("/"), "TinyFollowers50"
        )
        self.assertTrue(os.path.isfile(os.path.join(dst, "dlls", "main.dll")))

    def test_logicmods_paks_go_flat_into_logicmods(self):
        self.put("LogicMods/PalAnalyzer.pak")
        route = main._route_ue4ss_payload(
            self.scratch, self.install, self.UE4SS, self.LOGIC, "Pal Analyzer"
        )
        self.assertEqual(route["mode"], "files")
        self.assertEqual(route["files"], ["PalAnalyzer.pak"])
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    self.install, *self.LOGIC.split("/"), "PalAnalyzer.pak"
                )
            )
        )

    def test_target_records_list_toggle_uninstall(self):
        # Simulate an installed Lua mod: folder in place + record.
        self.put("MapUnlocker/Scripts/main.lua")
        route = main._route_ue4ss_payload(
            self.scratch, self.install, self.UE4SS, self.LOGIC, "MapUnlocker"
        )
        self.seed_record("MapUnlocker", route)
        mods = run(
            self.plugin.get_installed_mods("palworld", self.GAME, "Pal/Content/Paks/~mods")
        )["mods"]
        self.assertEqual(len(mods), 1)
        self.assertTrue(mods[0]["enabled"])
        # Disable: folder moves to the -disabled sibling of the UE4SS dir.
        result = run(
            self.plugin.set_mod_enabled(
                self.GAME, "Pal/Content/Paks/~mods", "MapUnlocker", False,
                "folder", "palworld",
            )
        )
        self.assertTrue(result["ok"], result.get("error"))
        base = os.path.join(self.install, *self.UE4SS.split("/"))
        self.assertTrue(os.path.isdir(base + "-disabled/MapUnlocker"))
        mods = run(
            self.plugin.get_installed_mods("palworld", self.GAME, "Pal/Content/Paks/~mods")
        )["mods"]
        self.assertFalse(mods[0]["enabled"])
        # Uninstall removes it from the routed location and the records.
        result = run(
            self.plugin.uninstall_mod(
                "palworld", self.GAME, "Pal/Content/Paks/~mods", "MapUnlocker"
            )
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertFalse(os.path.isdir(base + "-disabled/MapUnlocker"))
        self.assertEqual(
            main._load_settings()["installed"]["palworld"], {}
        )

    def test_logicmods_record_lists_untogglable_and_uninstalls(self):
        self.put("LogicMods/PalAnalyzer.pak")
        route = main._route_ue4ss_payload(
            self.scratch, self.install, self.UE4SS, self.LOGIC, "Pal Analyzer"
        )
        self.seed_record("Pal Analyzer", route)
        mods = run(
            self.plugin.get_installed_mods("palworld", self.GAME, "Pal/Content/Paks/~mods")
        )["mods"]
        self.assertEqual(mods[0]["togglable"], False)
        result = run(
            self.plugin.set_mod_enabled(
                self.GAME, "Pal/Content/Paks/~mods", "Pal Analyzer", False,
                "folder", "palworld",
            )
        )
        self.assertFalse(result["ok"])
        result = run(
            self.plugin.uninstall_mod(
                "palworld", self.GAME, "Pal/Content/Paks/~mods", "Pal Analyzer"
            )
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertFalse(
            os.path.isfile(
                os.path.join(
                    self.install, *self.LOGIC.split("/"), "PalAnalyzer.pak"
                )
            )
        )


class TestDeviceRuntimeConstraints(unittest.TestCase):
    def test_no_stdlib_xml_imports(self):
        """Decky's embedded Python has no xml package (no pyexpat) - the
        FOMOD wizard shipped dead because dev python has it. main.py must
        only use the bundled mini parser."""
        src = open(main.__file__, encoding="utf-8").read()
        self.assertNotIn("import xml.", src)
        self.assertNotIn("from xml ", src)
        self.assertNotIn("from xml.", src)


class TestFomod(unittest.TestCase):
    """FOMOD wizard parsing and staging against a representative
    ModuleConfig.xml (steps, groups, flags, conditional installs)."""

    CONFIG = """<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <moduleName>Test Armor Pack</moduleName>
  <requiredInstallFiles>
    <folder source="Core\\Data" destination="" priority="0" />
  </requiredInstallFiles>
  <installSteps order="Explicit">
    <installStep name="Resolution">
      <optionalFileGroups order="Explicit">
        <group name="Texture size" type="SelectExactlyOne">
          <plugins order="Explicit">
            <plugin name="4K">
              <description>Big textures</description>
              <files><folder source="Options\\4K" destination="textures" priority="1" /></files>
              <conditionFlags><flag name="res">4k</flag></conditionFlags>
              <typeDescriptor><type name="Recommended" /></typeDescriptor>
            </plugin>
            <plugin name="2K">
              <description>Small textures</description>
              <files><folder source="Options\\2K" destination="textures" priority="1" /></files>
              <conditionFlags><flag name="res">2k</flag></conditionFlags>
              <typeDescriptor><type name="Optional" /></typeDescriptor>
            </plugin>
          </plugins>
        </group>
      </optionalFileGroups>
    </installStep>
    <installStep name="Extras">
      <visible>
        <flagDependency flag="res" value="4k" />
      </visible>
      <optionalFileGroups order="Explicit">
        <group name="Extras" type="SelectAny">
          <plugins order="Explicit">
            <plugin name="Glow maps">
              <description>Shiny</description>
              <files><file source="Extras\\glow.dds" destination="textures/glow.dds" /></files>
              <typeDescriptor><type name="Optional" /></typeDescriptor>
            </plugin>
          </plugins>
        </group>
      </optionalFileGroups>
    </installStep>
  </installSteps>
  <conditionalFileInstalls>
    <patterns>
      <pattern>
        <dependencies operator="And">
          <flagDependency flag="res" value="4k" />
        </dependencies>
        <files><file source="Patches\\hd.esp" destination="hd.esp" /></files>
      </pattern>
    </patterns>
  </conditionalFileInstalls>
</config>"""

    def setUp(self):
        self.scratch = os.path.join(TEST_ROOT, "fomod-scratch")
        shutil.rmtree(self.scratch, ignore_errors=True)
        for rel, content in (
            ("fomod/ModuleConfig.xml", self.CONFIG),
            ("Core/Data/base.esp", "x"),
            ("Options/4K/armor/big.dds", "x"),
            ("Options/2K/armor/small.dds", "x"),
            ("Extras/glow.dds", "x"),
            ("Patches/hd.esp", "x"),
        ):
            p = os.path.join(self.scratch, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(content)
        self.data = os.path.join(TEST_ROOT, "fomod-data")
        shutil.rmtree(self.data, ignore_errors=True)
        os.makedirs(self.data)

    def test_wizard_parses_steps_groups_and_types(self):
        wizard, ctx = main._parse_fomod(self.scratch, self.data)
        self.assertEqual(wizard["moduleName"], "Test Armor Pack")
        self.assertEqual(len(wizard["steps"]), 2)
        group = wizard["steps"][0]["groups"][0]
        self.assertEqual(group["type"], "SelectExactlyOne")
        self.assertEqual(group["plugins"][0]["type"], "Recommended")
        # Step 2 visibility depends on the res flag
        vis = wizard["steps"][1]["visible"]
        self.assertTrue(main._fomod_eval_deps(vis, {"res": "4k"}))
        self.assertFalse(main._fomod_eval_deps(vis, {"res": "2k"}))

    def test_staging_applies_selection_flags_and_conditionals(self):
        _wizard, ctx = main._parse_fomod(self.scratch, self.data)
        staging = os.path.join(TEST_ROOT, "fomod-staging")
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging)
        # Choose 4K + glow maps: required base + 4K tree + glow + the
        # conditional hd.esp (res=4k flag) must all stage.
        count = main._fomod_stage(ctx, ["0.0.0", "1.0.0"], staging)
        self.assertEqual(count, 4)
        self.assertTrue(os.path.isfile(os.path.join(staging, "base.esp")))
        self.assertTrue(
            os.path.isfile(
                os.path.join(staging, "textures", "armor", "big.dds")
            )
        )
        self.assertTrue(
            os.path.isfile(os.path.join(staging, "textures", "glow.dds"))
        )
        self.assertTrue(os.path.isfile(os.path.join(staging, "hd.esp")))
        self.assertFalse(
            os.path.isfile(
                os.path.join(staging, "textures", "armor", "small.dds")
            )
        )

    def test_staging_2k_selection_skips_conditional(self):
        _wizard, ctx = main._parse_fomod(self.scratch, self.data)
        staging = os.path.join(TEST_ROOT, "fomod-staging2")
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging)
        count = main._fomod_stage(ctx, ["0.0.1"], staging)
        self.assertEqual(count, 2)  # base.esp + small.dds; no hd.esp
        self.assertFalse(os.path.isfile(os.path.join(staging, "hd.esp")))

    def test_windows_cased_sources_resolve(self):
        # XML says Core\\Data; on disk we made Core/Data - also try odd case
        _wizard, ctx = main._parse_fomod(self.scratch, self.data)
        resolved = main._fomod_case_resolve(
            ctx["fomod_base"], "CORE\\\\DATA"
        )
        self.assertIsNotNone(resolved)


class TestFomodCuratorChoices(unittest.TestCase):
    """Collection manifests record the curator's FOMOD selections (Vortex
    shape); the matcher maps them onto our wizard's plugin ids."""

    STEPS = [
        {
            "name": "Resolution",
            "groups": [
                {
                    "name": "Texture size",
                    "type": "SelectExactlyOne",
                    "plugins": [
                        {"id": "0.0.0", "name": "4K", "type": "Recommended"},
                        {"id": "0.0.1", "name": "2K", "type": "Optional"},
                    ],
                }
            ],
        },
        {
            "name": "Extras",
            "groups": [
                {
                    "name": "Extras",
                    "type": "SelectAny",
                    "plugins": [
                        {"id": "1.0.0", "name": "Glow maps", "type": "Optional"},
                        {"id": "1.0.1", "name": "Required core", "type": "Required"},
                    ],
                }
            ],
        },
    ]

    def test_curator_selection_matches_by_group_and_name(self):
        curator = {
            "type": "fomod",
            "options": [
                {"name": "Texture size", "choices": ["2K"]},
                {"name": "Extras", "choices": ["Glow maps"]},
            ],
        }
        ids = main._match_fomod_choices(self.STEPS, curator)
        self.assertIn("0.0.1", ids)       # curator picked 2K
        self.assertNotIn("0.0.0", ids)    # not the recommended 4K
        self.assertIn("1.0.0", ids)       # glow maps
        self.assertIn("1.0.1", ids)       # Required always in

    def test_no_curator_data_falls_back_to_defaults(self):
        ids = main._match_fomod_choices(self.STEPS, {})
        self.assertIn("0.0.0", ids)   # Recommended default
        self.assertIn("1.0.1", ids)   # Required
        self.assertNotIn("0.0.1", ids)

    def test_name_matching_is_normalized(self):
        curator = {"options": [{"name": "texture-size!", "choices": ["2k"]}]}
        ids = main._match_fomod_choices(self.STEPS, curator)
        self.assertIn("0.0.1", ids)


class TestWitcherRouting(unittest.TestCase):
    """TW3 archives: mod*/dlc* folders, menu XMLs into both filelists,
    and the script-conflict gate."""

    GAME = "The Witcher 3 Test"

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        self.mods = os.path.join(self.install, "mods")
        os.makedirs(self.mods)
        self.scratch = os.path.join(TEST_ROOT, "w3-scratch")
        shutil.rmtree(self.scratch, ignore_errors=True)
        os.makedirs(self.scratch)

    def put(self, rel):
        p = os.path.join(self.scratch, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("x")

    def test_classifies_mod_dlc_and_menu_xml(self):
        self.put("mods/modCoolThing/content/blob.bundle")
        self.put("dlc/dlcCoolThing/content/blob.bundle")
        self.put("bin/config/r4game/user_config_matrix/pc/coolthing.xml")
        mods, dlcs, xmls, err = main._route_witcher_payload(
            self.scratch, self.install, self.mods, "Cool Thing"
        )
        self.assertIsNone(err)
        self.assertEqual([os.path.basename(d) for d in mods], ["modCoolThing"])
        self.assertEqual([os.path.basename(d) for d in dlcs], ["dlcCoolThing"])
        self.assertEqual([os.path.basename(x) for x in xmls], ["coolthing.xml"])

    def test_loose_content_wraps_with_mod_prefix(self):
        self.put("content/texture.cache")
        mods, dlcs, xmls, err = main._route_witcher_payload(
            self.scratch, self.install, self.mods, "Loose Pack"
        )
        self.assertIsNone(err)
        self.assertEqual(len(mods), 1)
        self.assertTrue(os.path.basename(mods[0]).startswith("mod"))

    def test_script_conflict_is_refused(self):
        conflict = os.path.join(
            self.mods, "modExisting", "content", "scripts", "game", "hit.ws"
        )
        os.makedirs(os.path.dirname(conflict))
        with open(conflict, "w") as f:
            f.write("x")
        self.put("modNew/content/scripts/game/hit.ws")
        mods, dlcs, xmls, err = main._route_witcher_payload(
            self.scratch, self.install, self.mods, "New Mod"
        )
        self.assertIsNotNone(err)
        self.assertIn("modExisting", err)

    def test_filelist_append_is_idempotent(self):
        pc = os.path.join(self.install, *main.W3_MENU_DIR.split("/"))
        os.makedirs(pc)
        with open(os.path.join(pc, "dx11filelist.txt"), "w") as f:
            f.write("existing.xml;" + chr(10))
        main._w3_filelist_append(pc, "coolthing.xml")
        main._w3_filelist_append(pc, "coolthing.xml")
        content = open(os.path.join(pc, "dx11filelist.txt")).read()
        self.assertEqual(content.count("coolthing.xml;"), 1)
        self.assertIn("existing.xml;", content)
        # dx12 list created and populated too
        self.assertIn(
            "coolthing.xml;",
            open(os.path.join(pc, "dx12filelist.txt")).read(),
        )


class TestBannerlordModules(unittest.TestCase):
    """Module activation lives in LauncherData.xml; the Id comes from the
    module's SubModule.xml, not the folder name."""

    LAUNCHER_XML = (
        '<?xml version="1.0"?>\n'
        "<UserData>\n  <GameType>Singleplayer</GameType>\n"
        "  <SingleplayerData>\n    <ModDatas>\n"
        "      <UserModData><Id>Native</Id><IsSelected>true</IsSelected></UserModData>\n"
        "      <UserModData><Id>SandBox</Id><IsSelected>true</IsSelected></UserModData>\n"
        "    </ModDatas>\n  </SingleplayerData>\n</UserData>\n"
    )

    def setUp(self):
        self.dir = os.path.join(TEST_ROOT, "bannerlord")
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        self.xml = os.path.join(self.dir, "LauncherData.xml")
        with open(self.xml, "w") as f:
            f.write(self.LAUNCHER_XML)

    def read(self):
        return open(self.xml).read()

    def test_submodule_id_prefers_value_attribute(self):
        mod = os.path.join(self.dir, "CoolModule")
        os.makedirs(mod)
        with open(os.path.join(mod, "SubModule.xml"), "w") as f:
            f.write('<Module><Name value="Cool"/><Id value="CoolMod"/></Module>')
        self.assertEqual(main._submodule_id(mod), "CoolMod")
        self.assertIsNone(main._submodule_id(os.path.join(self.dir, "nope")))

    def test_append_new_module_entry(self):
        self.assertTrue(main._set_module_selected(self.xml, "CoolMod", True))
        content = self.read()
        self.assertIn("<Id>CoolMod</Id>", content)
        self.assertIn("Native", content)  # existing entries preserved

    def test_toggle_existing_entry(self):
        main._set_module_selected(self.xml, "CoolMod", True)
        main._set_module_selected(self.xml, "CoolMod", False)
        import xml.etree.ElementTree as ET

        root = ET.parse(self.xml).getroot()
        entry = next(
            e for e in root.iter("UserModData")
            if (e.find("Id").text or "") == "CoolMod"
        )
        self.assertEqual(entry.find("IsSelected").text, "false")

    def test_remove_entry(self):
        main._set_module_selected(self.xml, "CoolMod", True)
        main._remove_module_entry(self.xml, "CoolMod")
        self.assertNotIn("CoolMod", self.read())
        self.assertIn("Native", self.read())

    def test_missing_file_is_nonfatal(self):
        missing = os.path.join(self.dir, "nope", "LauncherData.xml")
        self.assertFalse(main._set_module_selected(missing, "X", True))
        main._remove_module_entry(missing, "X")  # must not raise

    def test_hidden_folders_excluded_from_listing(self):
        """Official Bannerlord modules live in Modules/ - they must never
        show up as toggleable mods."""
        game = "Bannerlord Test"
        install = os.path.join(main.STEAM_COMMON, game)
        shutil.rmtree(install, ignore_errors=True)
        for folder in ("Native", "SandBox", "CoolMod"):
            os.makedirs(os.path.join(install, "Modules", folder))
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        result = run(
            main.Plugin().get_installed_mods(
                "mountandblade2bannerlord", game, "Modules",
                "folder", 0, "", "starred", ["Native", "SandBox"],
            )
        )
        self.assertEqual([m["folder"] for m in result["mods"]], ["CoolMod"])


class TestFlatFileMods(unittest.TestCase):
    """Cyberpunk archive tier: the game loads FILES from archive/pc/mod,
    so installs place matching files flat with per-file records."""

    GAME = "Cyberpunk Test"

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        os.makedirs(os.path.join(self.install, "archive", "pc", "mod"))
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.plugin = main.Plugin()

    def seed_files_record(self, key, names):
        for n in names:
            with open(
                os.path.join(self.install, "archive", "pc", "mod", n), "w"
            ) as f:
                f.write("x")
        settings = main._load_settings()
        settings.setdefault("installed", {}).setdefault(
            "cyberpunk2077", {}
        )[key] = {
            "mod_id": 1, "name": key, "version": "1.0",
            "mode": "files", "target": "archive/pc/mod", "files": names,
        }
        main._save_settings(settings)

    def test_files_record_lists_and_uninstalls(self):
        self.seed_files_record("Cool Retex", ["cool.archive", "cool.xl"])
        mods = run(
            self.plugin.get_installed_mods(
                "cyberpunk2077", self.GAME, "archive/pc/mod"
            )
        )["mods"]
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0]["togglable"], False)
        result = run(
            self.plugin.uninstall_mod(
                "cyberpunk2077", self.GAME, "archive/pc/mod", "Cool Retex"
            )
        )
        self.assertTrue(result["ok"], result.get("error"))
        left = os.listdir(os.path.join(self.install, "archive", "pc", "mod"))
        self.assertEqual(left, [])


class TestSaves(unittest.TestCase):
    ACCOUNT = "123456789"
    APP_ID = 999001

    def setUp(self):
        self.remote = os.path.join(
            main.STEAM_USERDATA, self.ACCOUNT, str(self.APP_ID), "remote"
        )
        shutil.rmtree(os.path.join(main.STEAM_USERDATA, self.ACCOUNT), ignore_errors=True)
        os.makedirs(os.path.join(self.remote, "profile1", "saves"))
        with open(
            os.path.join(self.remote, "profile1", "saves", "progress.save"), "w"
        ) as f:
            f.write("{}")
        self.plugin = main.Plugin()

    def test_save_layout_finds_profiles(self):
        remote, profiles, modded = main._save_layout(self.ACCOUNT, self.APP_ID)
        self.assertEqual(profiles, ["profile1"])
        self.assertEqual(remote, self.remote)

    def test_copy_creates_modded_tree_and_backs_up_existing(self):
        # first copy: creates modded/profile1
        result = run(
            self.plugin.copy_saves_to_modded(self.APP_ID, self.ACCOUNT, "no-such-process")
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.remote, "modded", "profile1", "saves", "progress.save")
            )
        )
        self.assertIsNone(result["backup"])
        # second copy: previous modded tree moves to a backup
        result2 = run(
            self.plugin.copy_saves_to_modded(self.APP_ID, self.ACCOUNT, "no-such-process")
        )
        self.assertTrue(result2["ok"], result2.get("error"))
        self.assertIsNotNone(result2["backup"])
        self.assertTrue(os.path.isdir(result2["backup"]))

    def test_rejects_bad_account_id(self):
        result = run(
            self.plugin.copy_saves_to_modded(self.APP_ID, "../etc", "proc")
        )
        self.assertFalse(result["ok"])


class TestFrameworkSetupState(unittest.TestCase):
    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.plugin = main.Plugin()

    def test_launch_options_lifecycle(self):
        state = run(self.plugin.get_framework_setup("testgame"))
        self.assertFalse(state["launch_options_set"])
        run(self.plugin.mark_launch_options_set("testgame"))
        state = run(self.plugin.get_framework_setup("testgame"))
        self.assertTrue(state["launch_options_set"])
        self.assertTrue(state["enabled"])
        run(self.plugin.set_framework_enabled("testgame", False))
        state = run(self.plugin.get_framework_setup("testgame"))
        self.assertTrue(state["launch_options_set"])
        self.assertFalse(state["enabled"])

    def test_rejects_bad_domain(self):
        result = run(self.plugin.mark_launch_options_set("Bad Domain!"))
        self.assertFalse(result["ok"])


class TestNxmParsing(unittest.TestCase):
    def test_parses_full_free_download_link(self):
        entry = main._parse_nxm_url(
            "nxm://stardewvalley/mods/2400/files/160380"
            "?key=AbC-123&expires=1784226013&user_id=39089805"
        )
        self.assertEqual(entry["game_domain"], "stardewvalley")
        self.assertEqual(entry["mod_id"], 2400)
        self.assertEqual(entry["file_id"], 160380)
        self.assertEqual(entry["key"], "AbC-123")
        self.assertEqual(entry["expires"], "1784226013")

    def test_parses_premium_link_without_token(self):
        entry = main._parse_nxm_url("nxm://slaythespire2/mods/46/files/6344")
        self.assertEqual(entry["mod_id"], 46)
        self.assertEqual(entry["key"], "")

    def test_rejects_non_mod_links(self):
        self.assertIsNone(main._parse_nxm_url("nxm://oauth/callback?code=x"))
        self.assertIsNone(
            main._parse_nxm_url("nxm://game/collections/abc/revisions/1")
        )
        self.assertIsNone(main._parse_nxm_url("https://evil.example/mods/1/files/2"))
        self.assertIsNone(main._parse_nxm_url("nxm://bad domain!/mods/1/files/2"))
        self.assertIsNone(main._parse_nxm_url(""))
        self.assertIsNone(main._parse_nxm_url("nxm://x/mods/abc/files/2"))

    def test_queue_roundtrip(self):
        queue = os.path.join(main.decky.DECKY_PLUGIN_RUNTIME_DIR, main.NXM_QUEUE_NAME)
        os.makedirs(os.path.dirname(queue), exist_ok=True)
        with open(queue, "w", encoding="utf-8") as f:
            f.write("1784200000 nxm://stardewvalley/mods/5/files/9?key=k&expires=1&user_id=2\n")
            f.write("1784200001 not-a-url\n")
        result = run(main.Plugin().get_nxm_queue(clear=True))
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(result["entries"][0]["mod_id"], 5)
        self.assertEqual(len(result["raw"]), 2)
        # cleared
        result2 = run(main.Plugin().get_nxm_queue(clear=False))
        self.assertEqual(result2["raw"], [])

    def test_register_writes_handler_files(self):
        result = run(main.Plugin().register_nxm_handler())
        self.assertTrue(result["ok"], result.get("error"))
        desktop = os.path.join(
            main.decky.DECKY_USER_HOME, ".local", "share", "applications",
            "nexus-mods-decky-nxm.desktop",
        )
        self.assertTrue(os.path.isfile(desktop))
        with open(desktop, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("MimeType=x-scheme-handler/nxm;", content)
        self.assertIn("nxm-relay.sh %u", content)
        self.assertTrue(
            os.path.isfile(
                os.path.join(main.decky.DECKY_PLUGIN_RUNTIME_DIR, "nxm-relay.sh")
            )
        )


class TestEndorsements(unittest.TestCase):
    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.plugin = main.Plugin()

    def test_status_unknown_without_key(self):
        result = run(self.plugin.get_endorsement("stardewvalley", 2400))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "unknown")

    def test_set_requires_sign_in(self):
        result = run(self.plugin.set_endorsement("stardewvalley", 2400, "1.0", True))
        self.assertFalse(result["ok"])
        self.assertIn("signed in", result["error"].lower())

    def test_rejects_bad_domain(self):
        result = run(self.plugin.set_endorsement("../evil", 1, "1", True))
        self.assertFalse(result["ok"])


class TestExtractZipFallback(unittest.TestCase):
    def test_zip_extraction_via_available_extractor(self):
        src = os.path.join(TEST_ROOT, "payload")
        os.makedirs(os.path.join(src), exist_ok=True)
        archive = os.path.join(TEST_ROOT, "sample.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("ModFolder/manifest.json", '{"id": "ModFolder"}')
        dest = os.path.join(TEST_ROOT, "extracted")
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest)
        err = run(main._extract_archive(archive, dest))
        self.assertEqual(err, "")
        self.assertTrue(
            os.path.isfile(os.path.join(dest, "ModFolder", "manifest.json"))
        )


class TestModLoadStatusEndToEnd(unittest.TestCase):
    def test_reads_log_from_game_user_dir(self):
        log_dir = os.path.join(
            main.decky.DECKY_USER_HOME, ".local", "share", "TestGame", "logs"
        )
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "godot.log"), "w", encoding="utf-8") as f:
            f.write(
                "[INFO] Finished mod initialization for 'CoolMod' (CoolMod).\n"
                "[INFO]  --- RUNNING MODDED! --- Loaded 1 mods (1 total)\n"
            )
        result = run(main.Plugin().get_mod_load_status("TestGame"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["available"])
        self.assertTrue(result["modded_session"])
        self.assertEqual(result["status"]["coolmod"]["state"], "loaded")

    def test_missing_log_reports_unavailable(self):
        result = run(main.Plugin().get_mod_load_status("NoSuchGame"))
        self.assertTrue(result["ok"])
        self.assertFalse(result["available"])

    def test_rejects_bad_dir_name(self):
        result = run(main.Plugin().get_mod_load_status("../../etc"))
        self.assertFalse(result["ok"])


class TestPluginsTxt(unittest.TestCase):
    """dataDir mode (Skyrim): plugin activation lives in plugins.txt inside
    the game's Proton prefix; enabled = '*' prefix."""

    def setUp(self):
        self.path = os.path.join(TEST_ROOT, "plugins-txt", "plugins.txt")
        shutil.rmtree(os.path.dirname(self.path), ignore_errors=True)

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_path_points_into_proton_prefix(self):
        path = main._plugins_txt_path(489830, "Skyrim Special Edition/Plugins.txt")
        expected = os.path.join(
            main.decky.DECKY_USER_HOME, ".steam", "steam", "steamapps",
            "compatdata", "489830", "pfx", "drive_c", "users", "steamuser",
            "AppData", "Local", "Skyrim Special Edition", "Plugins.txt",
        )
        self.assertEqual(path, expected)

    def test_path_reuses_existing_file_of_any_casing(self):
        """The game writes 'Plugins.txt' via Wine's case-insensitive lookup;
        we must adopt whatever casing is on disk, never create a twin.
        (On case-insensitive filesystems the OS collapses the two names
        itself - the invariant is the same either way: one file.)"""
        nominal = main._plugins_txt_path(489831, "Test Game/Plugins.txt")
        parent = os.path.dirname(nominal)
        os.makedirs(parent, exist_ok=True)
        with open(os.path.join(parent, "PLUGINS.TXT"), "w") as f:
            f.write("# header\n")
        try:
            resolved = main._plugins_txt_path(489831, "Test Game/Plugins.txt")
            main._add_plugins(resolved, ["Mod.esp"])
            matches = [
                e for e in os.listdir(parent) if e.lower() == "plugins.txt"
            ]
            self.assertEqual(len(matches), 1, matches)
            self.assertIn(
                "*Mod.esp",
                main._read_plugins_txt(os.path.join(parent, matches[0])),
            )
        finally:
            shutil.rmtree(os.path.dirname(parent))

    def test_read_missing_file_is_empty(self):
        self.assertEqual(main._read_plugins_txt(self.path), [])

    def test_add_appends_starred_and_dedupes_case_insensitively(self):
        main._write_plugins_txt(self.path, ["*SkyUI_SE.esp", "unofficial.esp"])
        main._add_plugins(self.path, ["skyui_se.esp", "NewMod.esp"])
        self.assertEqual(
            self.read(), ["*SkyUI_SE.esp", "unofficial.esp", "*NewMod.esp"]
        )

    def test_set_active_toggles_star_and_preserves_order(self):
        main._write_plugins_txt(
            self.path, ["# comment", "*A.esp", "*B.esp", "C.esp"]
        )
        main._set_plugins_active(self.path, ["B.esp"], False)
        self.assertEqual(self.read(), ["# comment", "*A.esp", "B.esp", "C.esp"])
        main._set_plugins_active(self.path, ["b.esp", "C.esp"], True)
        self.assertEqual(self.read(), ["# comment", "*A.esp", "*B.esp", "*C.esp"])

    def test_remove_drops_lines_regardless_of_star(self):
        main._write_plugins_txt(self.path, ["*A.esp", "B.esp", "*C.esp"])
        main._remove_plugins(self.path, ["a.esp", "C.esp"])
        self.assertEqual(self.read(), ["B.esp"])

    def test_add_plugins_dedupes_within_one_call(self):
        """Regression: merge-all installs passed the same esp twice and it
        landed in Plugins.txt twice (the existing-set never updated)."""
        main._add_plugins(self.path, ["Penitus.esp", "penitus.esp", "Other.esp"])
        self.assertEqual(self.read(), ["*Penitus.esp", "*Other.esp"])

    def test_listed_style_presence_is_activation(self):
        """FNV/FO3/2011-Skyrim: no stars - a plugin listed in the file IS
        active, disable = delist."""
        main._add_plugins(self.path, ["Mod.esp"], style="listed")
        self.assertEqual(self.read(), ["Mod.esp"])
        self.assertEqual(
            main._active_plugins(self.path, "listed"), {"mod.esp"}
        )
        main._set_plugins_active(self.path, ["Mod.esp"], False, style="listed")
        self.assertEqual(self.read(), [])
        self.assertEqual(main._active_plugins(self.path, "listed"), set())
        main._set_plugins_active(self.path, ["Mod.esp"], True, style="listed")
        self.assertEqual(self.read(), ["Mod.esp"])

    def test_starred_style_active_plugins(self):
        main._write_plugins_txt(
            self.path, ["# note", "*On.esp", "Off.esp"]
        )
        self.assertEqual(main._active_plugins(self.path), {"on.esp"})
        # Same file read as listed-style would count both non-comments.
        self.assertEqual(
            main._active_plugins(self.path, "listed"), {"*on.esp", "off.esp"}
        )


class TestDataPayload(unittest.TestCase):
    """dataDir mode: find the directory whose contents belong in Data/."""

    def setUp(self):
        self.scratch = os.path.join(TEST_ROOT, "payload-scratch")
        shutil.rmtree(self.scratch, ignore_errors=True)
        os.makedirs(self.scratch)

    def put(self, rel):
        path = os.path.join(self.scratch, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("x")

    def test_flat_archive_with_plugin_is_the_payload(self):
        self.put("CoolMod.esp")
        self.assertEqual(main._find_data_payload(self.scratch), self.scratch)

    def test_marker_dir_counts_as_data(self):
        self.put("textures/armor/shiny.dds")
        self.assertEqual(main._find_data_payload(self.scratch), self.scratch)

    def test_explicit_data_folder_wins(self):
        self.put("Data/CoolMod.esp")
        self.put("readme.txt")
        self.assertEqual(
            main._find_data_payload(self.scratch),
            os.path.join(self.scratch, "Data"),
        )

    def test_single_wrapper_folder_is_unwrapped(self):
        self.put("CoolMod-1.0/CoolMod.esp")
        self.assertEqual(
            main._find_data_payload(self.scratch),
            os.path.join(self.scratch, "CoolMod-1.0"),
        )

    def test_wrapper_containing_data_folder(self):
        self.put("CoolMod-1.0/Data/CoolMod.esp")
        self.assertEqual(
            main._find_data_payload(self.scratch),
            os.path.join(self.scratch, "CoolMod-1.0", "Data"),
        )

    def test_fomod_only_archive_is_rejected(self):
        self.put("fomod/ModuleConfig.xml")
        self.put("00 Core/CoolMod.esp")
        self.assertIsNone(main._find_data_payload(self.scratch))

    def test_wrapper_beside_loose_readme_is_unwrapped(self):
        """Regression: archives shipping 'ModFolder/ + readme.txt' were
        refused because the wrapper rule demanded a lone entry."""
        self.put("CoolMod-1.0/CoolMod.esp")
        self.put("readme.txt")
        self.assertEqual(
            main._find_data_payload(self.scratch),
            os.path.join(self.scratch, "CoolMod-1.0"),
        )

    def test_option_folders_are_offered_as_choices(self):
        """Mini-FOMOD archives: several alternative folders, each a valid
        payload - surfaced for the user to pick instead of refused."""
        self.put("Slim Axes/meshes/weapons/axe.nif")
        self.put("Slim Maces/meshes/weapons/mace.nif")
        self.put("readme.txt")
        self.assertIsNone(main._find_data_payload(self.scratch))
        self.assertEqual(
            main._payload_options(self.scratch), ["Slim Axes", "Slim Maces"]
        )

    def test_option_folders_inside_wrapper(self):
        self.put("IronEdge/1. Standalone/IronEdge.esp")
        self.put("IronEdge/2. Replacer/meshes/weapons/iron/sword.nif")
        self.assertEqual(
            main._payload_options(self.scratch),
            ["IronEdge/1. Standalone", "IronEdge/2. Replacer"],
        )

    def test_fomod_with_single_real_folder_has_one_option(self):
        self.put("fomod/ModuleConfig.xml")
        self.put("00 Core/CoolMod.esp")
        self.assertEqual(main._payload_options(self.scratch), ["00 Core"])

    def test_nested_category_folders_recurse_to_leaf_payloads(self):
        """Real archive (Slimmer weapons): category dirs holding per-item
        payload folders - options must come from the deeper level."""
        self.put("battleaxes/daedric/meshes/weapons/daedric/axe.nif")
        self.put("battleaxes/dragonbone/meshes/weapons/db/axe.nif")
        self.put("maces/iron/meshes/weapons/iron/mace.nif")
        # "maces" holds a single payload child, so the wrapper rule offers
        # the category itself; multi-child categories offer each leaf.
        self.assertEqual(
            main._payload_options(self.scratch),
            ["battleaxes/daedric", "battleaxes/dragonbone", "maces"],
        )

    def test_fomod_dir_beside_variants_is_skipped(self):
        """Real archive (Iron greatswords): fomod/ metadata beside the
        variants blocked the old single-wrapper unwrap."""
        self.put("fomod/ModuleConfig.xml")
        self.put("Greatswords/Variant 1/meshes/weapons/iron/gs.nif")
        self.put("Greatswords/Variant 2/meshes/weapons/iron/gs.nif")
        self.assertEqual(
            main._payload_options(self.scratch),
            ["Greatswords/Variant 1", "Greatswords/Variant 2"],
        )

    def test_deep_wrapper_data_subpackages(self):
        """Real archive (Imperial Armors Retexture): wrapper/00 Data/<sub-
        packages> - three levels down."""
        self.put("Wrapper/00 Data/AmidianAddon/textures/armor/a.dds")
        self.put("Wrapper/00 Data/AmidianAddon - Sleeves/meshes/armor/b.nif")
        self.assertEqual(
            main._payload_options(self.scratch),
            [
                "Wrapper/00 Data/AmidianAddon",
                "Wrapper/00 Data/AmidianAddon - Sleeves",
            ],
        )

    def test_ue4ss_mod_shapes_are_detected(self):
        """Palworld field report: UE4SS Lua mods (Scripts/main.lua) and
        Blueprint mods (LogicMods dir) installed silently but can never
        load without the unsupported UE4SS loader."""
        self.put("MapUnlocker/Scripts/main.lua")
        self.assertTrue(main._looks_like_ue4ss_mod(self.scratch))
        shutil.rmtree(self.scratch)
        os.makedirs(self.scratch)
        self.put("LogicMods/PalAnalyzer.pak")
        self.assertTrue(main._looks_like_ue4ss_mod(self.scratch))
        shutil.rmtree(self.scratch)
        os.makedirs(self.scratch)
        # Native UE4SS mods: dlls/main.dll + enabled.txt (Tiny Followers)
        self.put("TinyFollowers50/dlls/main.dll")
        self.assertTrue(main._looks_like_ue4ss_mod(self.scratch))
        shutil.rmtree(self.scratch)
        os.makedirs(self.scratch)
        self.put("CoolPakMod/NoCollision_P.pak")
        self.put("CoolPakMod/Scripts.txt")
        self.assertFalse(main._looks_like_ue4ss_mod(self.scratch))

    def test_safe_rel_path_rejects_traversal(self):
        self.assertTrue(main._safe_rel_path("meshes/armor/x.nif"))
        for evil in ("../x", "a/../b", "a//b", ".", ".."):
            self.assertFalse(main._safe_rel_path(evil), evil)

    def test_case_merge_adopts_existing_dir_casing(self):
        """Regression: A Quality World Map created Data/Textures, then other
        mods created Data/textures - Wine resolves the exact-case dir first,
        so half the mods' textures became invisible to the game."""
        base = os.path.join(TEST_ROOT, "case-merge")
        shutil.rmtree(base, ignore_errors=True)
        os.makedirs(os.path.join(base, "Textures", "terrain"))
        self.assertEqual(
            main._case_merge_rel(base, "textures/armor/imperial/a.dds"),
            "Textures/armor/imperial/a.dds",
        )
        # Deeper components reuse existing casing too.
        self.assertEqual(
            main._case_merge_rel(base, "TEXTURES/TERRAIN/map.dds"),
            "Textures/terrain/map.dds",
        )
        # Nothing existing: the payload's own casing is kept.
        self.assertEqual(
            main._case_merge_rel(base, "meshes/weapons/x.nif"),
            "meshes/weapons/x.nif",
        )

    def test_ini_patch_preserves_and_sets(self):
        """Display-mode doctor: patch [Display] keys in place, preserving
        comments, unrelated sections, and adding what's missing."""
        path = os.path.join(TEST_ROOT, "prefs", "SkyrimPrefs.ini")
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write(
                "[General]\nsLanguage=ENGLISH\n\n"
                "[Display]\n; comment kept\nbFull Screen=1\n"
                "iSize W=2560\n\n[Audio]\nfVolume=1.0\n"
            )
        main._patch_ini_settings(
            path, "Display", {"bFull Screen": "0", "bBorderless": "1"}
        )
        content = open(path).read()
        self.assertIn("bFull Screen=0", content)
        self.assertIn("bBorderless=1", content)
        self.assertIn("; comment kept", content)
        self.assertIn("sLanguage=ENGLISH", content)
        self.assertIn("iSize W=2560", content)
        self.assertIn("fVolume=1.0", content)
        # New key landed inside [Display], not after [Audio]
        self.assertLess(content.index("bBorderless=1"), content.index("[Audio]"))
        # One-time backup written
        self.assertTrue(os.path.isfile(path + ".decky-nexus.bak"))
        self.assertIn("bFull Screen=1", open(path + ".decky-nexus.bak").read())
        # Read-back: case-insensitive keys, values as stored
        vals = main._read_ini_settings(
            path, "display", ["BFULL SCREEN", "bBorderless"]
        )
        self.assertEqual(vals["BFULL SCREEN"], "0")

    def test_ini_patch_creates_missing_section(self):
        path = os.path.join(TEST_ROOT, "prefs2", "Prefs.ini")
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("[General]\nsLanguage=ENGLISH\n")
        main._patch_ini_settings(path, "Display", {"bBorderless": "1"})
        content = open(path).read()
        self.assertIn("[Display]\nbBorderless=1", content)

    def test_check_game_file_rejects_traversal(self):
        for evil in ("../secrets", "a/../../b", ".."):
            result = run(main.Plugin().check_game_file("Game", evil))
            self.assertFalse(result["ok"], evil)

    def test_check_game_file_detects_native_marker(self):
        install = os.path.join(main.STEAM_COMMON, "NativeGame")
        shutil.rmtree(install, ignore_errors=True)
        os.makedirs(install)
        open(os.path.join(install, "UnityPlayer.so"), "w").write("")
        result = run(main.Plugin().check_game_file("NativeGame", "UnityPlayer.so"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["exists"])
        result = run(main.Plugin().check_game_file("NativeGame", "game.exe"))
        self.assertFalse(result["exists"])

    def test_requirement_normalization(self):
        """Regression: the v2 API returns requirement modId as a STRING and
        external requirements (VC++ redist links) as modId "0" with an empty
        name - clicking one requested mod #0 and surfaced a RuntimeError."""
        raw = [
            {"modName": "", "modId": "0",
             "notes": "MO2 2.5.2+ will not start without this",
             "url": "https://aka.ms/vs/17/release/vc_redist.x64.exe"},
            {"modName": "SkyUI", "modId": "12604", "notes": None, "url": None},
        ]
        reqs = main._normalize_requirements(raw)
        self.assertEqual(reqs[0]["modId"], 0)
        self.assertEqual(reqs[1]["modId"], 12604)
        self.assertIsInstance(reqs[1]["modId"], int)
        self.assertEqual(reqs[1]["notes"], "")
        self.assertEqual(main._normalize_requirements(None), [])

    def test_collection_sort_fields_are_valid_api_names(self):
        """Regression: 'downloads' was mapped to a nonexistent field
        (totalDownloads) - the API errored and the UI showed zero results.
        These names are verified against the live collectionsV2 schema."""
        self.assertEqual(main._collection_sort_field("downloads"), "downloads")
        self.assertEqual(
            main._collection_sort_field("endorsements"), "endorsements"
        )
        self.assertEqual(main._collection_sort_field("updatedAt"), "updatedAt")
        self.assertEqual(main._collection_sort_field("createdAt"), "createdAt")
        # unknown keys fall back safely
        self.assertEqual(main._collection_sort_field("bogus"), "endorsements")
        # every mapped value must be one of the schema-verified fields
        for key in ("endorsements", "downloads", "updatedAt", "createdAt", "trending"):
            self.assertIn(
                main._collection_sort_field(key),
                {"endorsements", "downloads", "updatedAt", "createdAt", "recentRating"},
            )

    def test_version_compare_is_numeric(self):
        """Regression: SkyUI 6.11 installed showed '6.9 available' - string
        comparison thinks 6.9 is a different (hence 'new') version."""
        self.assertFalse(main._is_newer_version("6.9", "6.11"))
        self.assertTrue(main._is_newer_version("6.12", "6.11"))
        self.assertTrue(main._is_newer_version("1.0", "0.9.9"))
        self.assertFalse(main._is_newer_version("1.0", "1.0"))
        # Unparseable versions fall back to plain inequality.
        self.assertTrue(main._is_newer_version("beta", "alpha"))
        self.assertFalse(main._is_newer_version("", "1.0"))


class TestDataDirFlows(unittest.TestCase):
    """dataDir mode end-to-end against seeded install records: list with
    star-derived enabled state, toggle, uninstall exactly the manifest."""

    GAME = "Skyrim Special Edition"
    DOMAIN = "skyrimspecialedition"
    APP_ID = 489830
    SUBPATH = "Skyrim Special Edition/Plugins.txt"

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        self.data = os.path.join(self.install, "Data")
        shutil.rmtree(self.install, ignore_errors=True)
        os.makedirs(self.data)
        self.plugins_txt = main._plugins_txt_path(self.APP_ID, self.SUBPATH)
        shutil.rmtree(
            os.path.dirname(os.path.dirname(self.plugins_txt)), ignore_errors=True
        )
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.plugin = main.Plugin()
        # The game's own base file must survive every mod operation.
        self.put_data_file("Skyrim.esm")

    def put_data_file(self, rel):
        path = os.path.join(self.data, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("x")

    def seed_mod(self, key, files, plugins, active=True):
        for rel in files:
            self.put_data_file(rel)
        if plugins:
            main._add_plugins(self.plugins_txt, plugins)
            if not active:
                main._set_plugins_active(self.plugins_txt, plugins, False)
        settings = main._load_settings()
        settings.setdefault("installed", {}).setdefault(self.DOMAIN, {})[key] = {
            "mod_id": 1,
            "file_id": 1,
            "name": key,
            "version": "1.0",
            "mode": "dataDir",
            "files": files,
            "plugins": plugins,
        }
        main._save_settings(settings)

    def mode_args(self):
        """(install_mode, app_id, plugins_subpath) - matches modeParams()."""
        return "dataDir", self.APP_ID, self.SUBPATH

    def toggle_args(self):
        """set_mod_enabled/set_all_mods_enabled put game_domain after mode."""
        return "dataDir", self.DOMAIN, self.APP_ID, self.SUBPATH

    def test_list_reads_enabled_from_plugins_txt(self):
        self.seed_mod("SkyUI", ["SkyUI_SE.esp", "SkyUI_SE.bsa"], ["SkyUI_SE.esp"])
        self.seed_mod("Disabled", ["Off.esp"], ["Off.esp"], active=False)
        self.seed_mod("TextureOnly", ["textures/armor/shiny.dds"], [])
        result = run(
            self.plugin.get_installed_mods(
                self.DOMAIN, self.GAME, "Data", *self.mode_args()
            )
        )
        self.assertTrue(result["ok"])
        by_name = {m["folder"]: m for m in result["mods"]}
        self.assertTrue(by_name["SkyUI"]["enabled"])
        self.assertTrue(by_name["SkyUI"]["togglable"])
        self.assertFalse(by_name["Disabled"]["enabled"])
        # Asset-only mods have nothing to toggle and count as always active.
        self.assertTrue(by_name["TextureOnly"]["enabled"])
        self.assertFalse(by_name["TextureOnly"]["togglable"])

    def test_toggle_stars_and_unstars_plugins(self):
        self.seed_mod("SkyUI", ["SkyUI_SE.esp"], ["SkyUI_SE.esp"])
        result = run(
            self.plugin.set_mod_enabled(
                self.GAME, "Data", "SkyUI", False, *self.toggle_args()
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(main._read_plugins_txt(self.plugins_txt), ["SkyUI_SE.esp"])
        result = run(
            self.plugin.set_mod_enabled(
                self.GAME, "Data", "SkyUI", True, *self.toggle_args()
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(main._read_plugins_txt(self.plugins_txt), ["*SkyUI_SE.esp"])

    def test_toggle_asset_only_mod_is_refused(self):
        self.seed_mod("TextureOnly", ["textures/armor/shiny.dds"], [])
        result = run(
            self.plugin.set_mod_enabled(
                self.GAME, "Data", "TextureOnly", False, *self.toggle_args()
            )
        )
        self.assertFalse(result["ok"])

    def test_toggle_all_flips_every_tracked_plugin(self):
        self.seed_mod("A", ["A.esp"], ["A.esp"])
        self.seed_mod("B", ["B.esp"], ["B.esp"])
        result = run(
            self.plugin.set_all_mods_enabled(
                self.GAME, "Data", False, *self.toggle_args()
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["moved"], 2)
        self.assertEqual(
            main._read_plugins_txt(self.plugins_txt), ["A.esp", "B.esp"]
        )

    def test_uninstall_removes_only_manifest_files(self):
        self.seed_mod(
            "SkyUI",
            ["SkyUI_SE.esp", "interface/skyui/config.txt"],
            ["SkyUI_SE.esp"],
        )
        self.seed_mod("Other", ["Other.esp"], ["Other.esp"])
        result = run(
            self.plugin.uninstall_mod(
                self.DOMAIN, self.GAME, "Data", "SkyUI", *self.mode_args()
            )
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertFalse(os.path.isfile(os.path.join(self.data, "SkyUI_SE.esp")))
        # Emptied directories are pruned; other mods and the game's own
        # files are untouched.
        self.assertFalse(os.path.isdir(os.path.join(self.data, "interface")))
        self.assertTrue(os.path.isfile(os.path.join(self.data, "Skyrim.esm")))
        self.assertTrue(os.path.isfile(os.path.join(self.data, "Other.esp")))
        self.assertEqual(main._read_plugins_txt(self.plugins_txt), ["*Other.esp"])
        records = main._load_settings().get("installed", {}).get(self.DOMAIN, {})
        self.assertNotIn("SkyUI", records)
        self.assertIn("Other", records)

    def test_uninstall_untracked_mod_fails(self):
        result = run(
            self.plugin.uninstall_mod(
                self.DOMAIN, self.GAME, "Data", "Ghost", *self.mode_args()
            )
        )
        self.assertFalse(result["ok"])

    def test_uninstall_all_respects_protected(self):
        self.seed_mod("UserMod", ["UserMod.esp"], ["UserMod.esp"])
        self.seed_mod("Precious", ["Precious.esp"], ["Precious.esp"])
        result = run(
            self.plugin.uninstall_all_mods(
                self.DOMAIN, self.GAME, "Data", ["precious"], *self.mode_args()
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["kept"], ["Precious"])
        self.assertFalse(os.path.isfile(os.path.join(self.data, "UserMod.esp")))
        self.assertTrue(os.path.isfile(os.path.join(self.data, "Precious.esp")))
        self.assertEqual(
            main._read_plugins_txt(self.plugins_txt), ["*Precious.esp"]
        )


if __name__ == "__main__":
    unittest.main()
