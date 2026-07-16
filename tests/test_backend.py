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


if __name__ == "__main__":
    unittest.main()
