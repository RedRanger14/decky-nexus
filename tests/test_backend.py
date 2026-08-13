import ast
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
import time
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

    class TCPConnector:
        # Only ever constructed as an argument to ClientSession, which
        # refuses to exist here - this keeps the failure "network is
        # disabled" rather than a confusing missing-attribute error.
        def __init__(self, *args, **kwargs):
            pass

    m.ClientError = ClientError
    m.ClientTimeout = ClientTimeout
    m.ClientSession = ClientSession
    m.TCPConnector = TCPConnector
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

    def test_language_english_excludes_tagged_translations(self):
        # Most mods carry NO language tag - "english" must EXCLUDE the
        # tagged translations, never REQUIRE the English tag (a strict
        # filter hid three quarters of the catalog in live testing).
        q = main._build_mods_query(with_search=False, language="english")
        self.assertIn("languageName", q)
        self.assertIn('{ value: "French", op: NOT_EQUALS }', q)
        self.assertIn('{ value: "Mandarin", op: NOT_EQUALS }', q)
        self.assertNotIn('{ value: "English"', q)

    def test_language_specific_shows_only_that_tag(self):
        q = main._build_mods_query(with_search=False, language="French")
        self.assertIn('languageName: [{ value: "French" }]', q)

    def test_language_all_adds_no_filter(self):
        q = main._build_mods_query(with_search=False, language="all")
        self.assertNotIn("languageName", q)

    def test_mod_language_pref_validates(self):
        self.assertEqual(main._valid_mod_language("French"), "French")
        self.assertEqual(main._valid_mod_language("all"), "all")
        self.assertEqual(main._valid_mod_language("Klingon"), "english")
        self.assertEqual(main._valid_mod_language(None), "english")

    def test_adult_content_ignores_legacy_local_toggle(self):
        # UK OSA-class age-verification laws: the gate is account-driven
        # (site preference + platform verification, see TestContentGate).
        # The pre-0.37 local key must stay dead - even a hand-edited
        # settings.json can't open the gate.
        settings = main._load_settings()
        settings["show_adult"] = True
        main._save_settings(settings)
        try:
            self.assertFalse(main._show_adult())
            result = run(main.Plugin().set_show_adult(True))
            self.assertFalse(result["ok"])
            self.assertIn("nexusmods.com", result["error"])
        finally:
            settings = main._load_settings()
            settings.pop("show_adult", None)
            main._save_settings(settings)

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


class TestRootFilesInstall(unittest.TestCase):
    """Root-payload archives (SSE Engine Fixes part 2's preloader) install
    into the game root, honoring the shipped Vortex override instructions,
    and uninstall removes exactly those files."""

    DOMAIN = "skyrimspecialedition"

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.tmp = tempfile.mkdtemp()
        self.scratch = os.path.join(self.tmp, "scratch")
        self.game = os.path.join(self.tmp, "game")
        os.makedirs(self.scratch)
        os.makedirs(self.game)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _put(self, rel, content="x"):
        path = os.path.join(self.scratch, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_vortex_override_copy_parsing(self):
        self._put(
            "vortex_override_instructions.json",
            json.dumps(
                {
                    "instructions": [
                        {"type": "copy", "source": "a.dll", "destination": "a.dll"},
                        {"type": "mkdir", "destination": "junk"},
                        {"type": "copy", "source": "../evil", "destination": "e"},
                    ]
                }
            ),
        )
        override = main._find_vortex_override(self.scratch)
        self.assertIsNotNone(override)
        copies = main._vortex_override_copies(override)
        # mkdir ignored, path traversal rejected
        self.assertEqual(copies, [("a.dll", "a.dll")])

    def test_unparseable_override_yields_no_copies(self):
        self._put("vortex_override_instructions.json", "{not json")
        override = main._find_vortex_override(self.scratch)
        self.assertEqual(main._vortex_override_copies(override), [])

    def test_install_and_uninstall_roundtrip(self):
        self._put("d3dx9_42.dll", "preloader")
        self._put(
            "vortex_override_instructions.json",
            json.dumps(
                {
                    "instructions": [
                        {
                            "type": "copy",
                            "source": "d3dx9_42.dll",
                            "destination": "d3dx9_42.dll",
                        }
                    ]
                }
            ),
        )
        result = run(
            main.Plugin()._install_root_files(
                self.scratch, self.game, self.DOMAIN, 32444, 999,
                "part2.7z", "SSE Engine Fixes part 2", "1.0", "",
                "collection", "test-collection",
            )
        )
        self.assertTrue(result["ok"])
        installed = os.path.join(self.game, "d3dx9_42.dll")
        self.assertTrue(os.path.isfile(installed))
        rec = (
            main._load_settings()["installed"][self.DOMAIN][result["folder"]]
        )
        self.assertEqual(rec["mode"], "files")
        self.assertEqual(rec["target"], ".")
        self.assertEqual(rec["files"], ["d3dx9_42.dll"])
        self.assertEqual(rec["collection_slug"], "test-collection")
        # uninstall removes exactly the recorded files + the record
        settings = main._load_settings()
        self.assertTrue(
            main._remove_files_record(
                self.DOMAIN, result["folder"], self.game, settings
            )
        )
        self.assertFalse(os.path.exists(installed))
        self.assertNotIn(
            result["folder"], settings["installed"][self.DOMAIN]
        )

    def test_no_override_copies_everything_but_instructions(self):
        self._put("winhttp.dll")
        self._put("tbb.dll")
        result = run(
            main.Plugin()._install_root_files(
                self.scratch, self.game, self.DOMAIN, 1, 2,
                "x.zip", "Preloader", "1.0", "", "", "",
            )
        )
        self.assertTrue(result["ok"])
        self.assertTrue(os.path.isfile(os.path.join(self.game, "winhttp.dll")))
        self.assertTrue(os.path.isfile(os.path.join(self.game, "tbb.dll")))


class TestCp77Routing(unittest.TestCase):
    """CP77 archives: game-root payloads (bin/red4ext/r6/engine/archive),
    bare .archive files, REDmod-format detection, tool detection. All
    framework shapes verified against the real archives (2026-08-04)."""

    def setUp(self):
        self.scratch = os.path.join(TEST_ROOT, "cp77-scratch")
        shutil.rmtree(self.scratch, ignore_errors=True)
        os.makedirs(self.scratch)

    def put(self, rel):
        p = os.path.join(self.scratch, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("x")

    def rels(self, files):
        return sorted(rel for rel, _src in files)

    def test_framework_shaped_payload_routes_by_root(self):
        # RED4ext's real shape: loader dll + red4ext tree
        self.put("bin/x64/winmm.dll")
        self.put("red4ext/RED4ext.dll")
        files, err = main._route_cp77_payload(self.scratch, "RED4ext")
        self.assertIsNone(err)
        self.assertEqual(
            self.rels(files), ["bin/x64/winmm.dll", "red4ext/RED4ext.dll"]
        )

    def test_wrapper_dir_unwraps(self):
        self.put("SomeMod-1.0/r6/scripts/somemod/main.reds")
        self.put("SomeMod-1.0/archive/pc/mod/somemod.archive")
        files, err = main._route_cp77_payload(self.scratch, "Some Mod")
        self.assertIsNone(err)
        self.assertEqual(
            self.rels(files),
            [
                "archive/pc/mod/somemod.archive",
                "r6/scripts/somemod/main.reds",
            ],
        )

    def test_bare_archive_files_go_flat(self):
        self.put("cool_car.archive")
        self.put("nested/cool_car.archive.xl")
        files, err = main._route_cp77_payload(self.scratch, "Cool Car")
        self.assertIsNone(err)
        self.assertEqual(
            self.rels(files),
            [
                "archive/pc/mod/cool_car.archive",
                "archive/pc/mod/cool_car.archive.xl",
            ],
        )

    def test_redmod_format_is_refused_with_guidance(self):
        self.put("mods/CoolMod/info.json")
        self.put("mods/CoolMod/archives/cool.archive")
        files, err = main._route_cp77_payload(self.scratch, "Cool Mod")
        self.assertIsNotNone(err)
        kind, message = err
        self.assertEqual(kind, "layout")
        self.assertIn("REDmod", message)

    def test_exe_archive_is_a_tool(self):
        self.put("CyberCAT/CyberCAT.exe")
        files, err = main._route_cp77_payload(self.scratch, "CyberCAT")
        self.assertIsNotNone(err)
        self.assertEqual(err[0], "tool")


class TestResetGameModding(unittest.TestCase):
    """One-button vanilla reset: records of every mode uninstall, the
    framework loader goes, and the game's plugin state clears."""

    DOMAIN = "resetgame"

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.install = os.path.join(main.STEAM_COMMON, "ResetGame")
        shutil.rmtree(self.install, ignore_errors=True)
        self.data = os.path.join(self.install, "Data")
        os.makedirs(self.data)
        # a dataDir mod, a root-files mod, and framework loader files
        with open(os.path.join(self.data, "CoolMod.esp"), "w") as f:
            f.write("x")
        with open(os.path.join(self.install, "d3dx9_42.dll"), "w") as f:
            f.write("x")
        with open(os.path.join(self.install, "skse64_loader.exe"), "w") as f:
            f.write("x")
        settings = main._load_settings()
        settings["installed"] = {
            self.DOMAIN: {
                "CoolMod": {
                    "mode": "dataDir",
                    "files": ["CoolMod.esp"],
                    "plugins": ["CoolMod.esp"],
                },
                "Preloader": {
                    "mode": "files",
                    "target": ".",
                    "files": ["d3dx9_42.dll"],
                },
            }
        }
        settings["framework_setup"] = {
            self.DOMAIN: {"launch_options_set": True, "enabled": True}
        }
        settings["collections"] = {self.DOMAIN: {"some-collection": {}}}
        main._save_settings(settings)

    def tearDown(self):
        shutil.rmtree(self.install, ignore_errors=True)

    def test_reset_removes_everything_tracked(self):
        result = run(
            main.Plugin().reset_game_modding(
                self.DOMAIN, "ResetGame", "Data", "dataDir", 0, "",
                "starred", ["skse64"],
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["removed"], 2)
        self.assertEqual(result["framework_files"], ["skse64_loader.exe"])
        self.assertFalse(
            os.path.exists(os.path.join(self.data, "CoolMod.esp"))
        )
        self.assertFalse(
            os.path.exists(os.path.join(self.install, "d3dx9_42.dll"))
        )
        settings = main._load_settings()
        for section in ("installed", "framework_setup", "collections"):
            self.assertNotIn(self.DOMAIN, settings.get(section, {}))
        # the game exe area is otherwise untouched
        self.assertTrue(os.path.isdir(self.data))

    def test_reset_rejects_bad_domain(self):
        result = run(
            main.Plugin().reset_game_modding("Bad!", "ResetGame", "Data")
        )
        self.assertFalse(result["ok"])


class TestUninstallCollection(unittest.TestCase):
    """Uninstalling a collection removes ONLY records carrying its slug -
    shared/loose mods stay - plus its registry and attention entries."""

    DOMAIN = "collgame"

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.install = os.path.join(main.STEAM_COMMON, "CollGame")
        shutil.rmtree(self.install, ignore_errors=True)
        self.data = os.path.join(self.install, "Data")
        os.makedirs(self.data)
        for name in ("FromColl.esp", "Loose.esp"):
            with open(os.path.join(self.data, name), "w") as f:
                f.write("x")
        settings = main._load_settings()
        settings["installed"] = {
            self.DOMAIN: {
                "FromColl": {
                    "mode": "dataDir",
                    "files": ["FromColl.esp"],
                    "plugins": [],
                    "source": "collection",
                    "collection_slug": "my-coll",
                },
                "Loose": {
                    "mode": "dataDir",
                    "files": ["Loose.esp"],
                    "plugins": [],
                    "source": "",
                    "collection_slug": "",
                },
            }
        }
        settings["collections"] = {
            self.DOMAIN: {"my-coll": {"title": "My Coll"}}
        }
        settings["collection_attention"] = {
            self.DOMAIN: {"my-coll": [{"file_id": 1}]}
        }
        main._save_settings(settings)

    def tearDown(self):
        shutil.rmtree(self.install, ignore_errors=True)

    def test_official_w3_dlc_is_never_deleted(self):
        # Legacy record pointing at the game's own DLC folder (from
        # before official-dlc patches merged in): the folder must survive
        # every cleanup path - deleting it destroyed Blood & Wine on
        # device (Steam verify required).
        bob = os.path.join(self.install, "dlc", "bob", "content")
        os.makedirs(bob)
        settings = main._load_settings()
        settings["installed"][self.DOMAIN]["SomePatch"] = {
            "mode": "folder",
            "target": "dlc",
            "folder": "bob",
            "source": "collection",
            "collection_slug": "my-coll",
        }
        main._save_settings(settings)
        result = run(
            main.Plugin().uninstall_collection(
                self.DOMAIN, "CollGame", "Data", "dataDir", 0, "",
                "starred", "my-coll",
            )
        )
        self.assertTrue(result["ok"])
        self.assertTrue(os.path.isdir(bob))
        self.assertNotIn(
            "SomePatch",
            main._load_settings()["installed"].get(self.DOMAIN, {}),
        )

    def test_removes_only_collection_records(self):
        result = run(
            main.Plugin().uninstall_collection(
                self.DOMAIN, "CollGame", "Data", "dataDir", 0, "",
                "starred", "my-coll",
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["removed"], 1)
        self.assertFalse(
            os.path.exists(os.path.join(self.data, "FromColl.esp"))
        )
        self.assertTrue(os.path.exists(os.path.join(self.data, "Loose.esp")))
        settings = main._load_settings()
        self.assertNotIn("FromColl", settings["installed"][self.DOMAIN])
        self.assertIn("Loose", settings["installed"][self.DOMAIN])
        self.assertNotIn(
            "my-coll", settings.get("collections", {}).get(self.DOMAIN, {})
        )
        self.assertNotIn(
            "my-coll",
            settings.get("collection_attention", {}).get(self.DOMAIN, {}),
        )


class TestCollectionAttention(unittest.TestCase):
    """Pending manual choices persist per collection so any later visit
    can resolve them (the Finish-setup flow)."""

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.plugin = main.Plugin()

    def test_set_get_clear_roundtrip(self):
        items = [
            {
                "file_id": 11,
                "mod_id": 22,
                "mod_name": "Choosy Mod",
                "file_name": "choosy.zip",
                "version": "1.0",
                "reason": "choices",
                "options": ["A", "B"],
            }
        ]
        result = run(
            self.plugin.set_collection_attention("skyrimspecialedition",
                                                 "test-coll", items)
        )
        self.assertTrue(result["ok"])
        got = run(
            self.plugin.get_collection_attention("skyrimspecialedition",
                                                 "test-coll")
        )
        self.assertEqual(got["items"][0]["mod_name"], "Choosy Mod")
        self.assertEqual(got["items"][0]["options"], ["A", "B"])
        # empty list clears
        run(
            self.plugin.set_collection_attention("skyrimspecialedition",
                                                 "test-coll", [])
        )
        got = run(
            self.plugin.get_collection_attention("skyrimspecialedition",
                                                 "test-coll")
        )
        self.assertEqual(got["items"], [])


class TestHelpers(unittest.TestCase):
    def test_force_rmtree_handles_plain_files(self):
        # NVAC's FOMOD staging cleanup crashed on 'readme - nvac.txt' -
        # _force_rmtree must delete files too, not just directories.
        tmp = tempfile.mkdtemp()
        f = os.path.join(tmp, "readme - nvac.txt")
        with open(f, "w") as fh:
            fh.write("x")
        main._force_rmtree(f)
        self.assertFalse(os.path.exists(f))
        main._force_rmtree(os.path.join(tmp, "does-not-exist"))
        shutil.rmtree(tmp, ignore_errors=True)

    def test_nvse_plugin_archive_is_a_data_payload(self):
        # The FNV NVSE-plugin convention: archives rooted at
        # NVSE/Plugins/ (no Data/ wrapper) - an entire collection failed
        # for want of the marker.
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "NVSE", "Plugins", "nvac.dll")
        os.makedirs(os.path.dirname(p))
        with open(p, "w") as fh:
            fh.write("x")
        self.assertIsNotNone(main._find_data_payload(tmp))
        shutil.rmtree(tmp, ignore_errors=True)

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

    @staticmethod
    def _callables(src: str):
        """(arg-tuple, backend name) for every callable<> in api.ts.

        The tuple is found by matching brackets rather than by regex: a
        non-greedy \\[(.*?)\\] stops at the first ']', which is the one
        inside 'names: string[]' whenever an array parameter is not the
        last one. That mis-parse reported a correct 4-arg signature as 3
        and failed this test for a bug that did not exist.
        """
        import re as _re

        for start in (m.end() for m in _re.finditer(r"callable<", src)):
            open_at = src.find("[", start)
            if open_at < 0:
                continue
            depth, i = 0, open_at
            while i < len(src):
                if src[i] in "<[({":
                    depth += 1
                elif src[i] in ">])}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            blob = src[open_at + 1 : i]
            call = _re.search(r'>\(\s*"([a-z0-9_]+)"\s*\)', src[i:])
            if call:
                yield blob, call.group(1)

    def test_every_api_ts_callable_fits_its_backend_signature(self):
        import inspect

        src = open(
            os.path.join(REPO_ROOT, "src", "api.ts"), encoding="utf-8"
        ).read()
        checked = 0
        for blob, name in self._callables(src):
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

    def test_utf16_moduleconfig_parses(self):
        # The 'FOMOD Creation Tool' writes UTF-16 LE with a BOM (seen in
        # the wild: SSE FPS Stabilizer). Read as UTF-8 this tokenized
        # NUL garbage into an empty wizard and the install fell through
        # to "no payload".
        cfg = os.path.join(self.scratch, "fomod", "ModuleConfig.xml")
        with open(cfg, "wb") as f:
            f.write(b"\xff\xfe" + self.CONFIG.encode("utf-16-le"))
        wizard, _ctx = main._parse_fomod(self.scratch, self.data)
        self.assertEqual(wizard["moduleName"], "Test Armor Pack")
        self.assertEqual(len(wizard["steps"]), 2)

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
        kind, conflicts = err
        self.assertEqual(kind, "conflicts")
        rel, owner, incoming, owner_path = conflicts[0]
        self.assertEqual(rel, "game/hit.ws")
        self.assertEqual(owner, "modExisting")
        # real, openable paths on a case-sensitive filesystem
        self.assertTrue(os.path.isfile(incoming))
        self.assertTrue(os.path.isfile(owner_path))

    def test_conflict_paths_survive_mixed_case(self):
        # Mods ship Game/Player/-style casing; the lowered rel is for
        # comparison only - reading must use REAL paths (r4player.ws
        # failed to merge on device because both sides were opened via
        # the lowercased path).
        conflict = os.path.join(
            self.mods, "modExisting", "content", "scripts", "Game",
            "Player", "R4Player.ws",
        )
        os.makedirs(os.path.dirname(conflict))
        with open(conflict, "w") as f:
            f.write("x")
        self.put("modNew/content/scripts/game/player/r4player.ws")
        mods, dlcs, xmls, err = main._route_witcher_payload(
            self.scratch, self.install, self.mods, "New Mod"
        )
        self.assertIsNotNone(err)
        kind, conflicts = err
        self.assertEqual(kind, "conflicts")
        rel, owner, incoming, owner_path = conflicts[0]
        self.assertEqual(rel, "game/player/r4player.ws")
        self.assertTrue(os.path.isfile(incoming))
        self.assertTrue(os.path.isfile(owner_path))

    def test_exe_archive_is_classified_as_tool(self):
        # Script Merger / W3 Mod Manager: desktop utilities, not mods.
        self.put("WitcherScriptMerger/WitcherScriptMerger.exe")
        mods, dlcs, xmls, err = main._route_witcher_payload(
            self.scratch, self.install, self.mods, "Script Merger"
        )
        self.assertIsNotNone(err)
        kind, message = err
        self.assertEqual(kind, "tool")
        self.assertIn("PC modding tool", message)

    def test_menu_xml_inside_mod_folder_is_found(self):
        # Increased Draw Distance layout: the XML lives INSIDE the mod
        # folder - the caller must move XMLs before folders (the old
        # order crashed with FileNotFoundError on device).
        self.put("modIDD/content/blob.bundle")
        self.put(
            "modIDD/bin/config/r4game/user_config_matrix/pc/modIDDConfig.xml"
        )
        mods, dlcs, xmls, err = main._route_witcher_payload(
            self.scratch, self.install, self.mods, "Increased Draw Distance"
        )
        self.assertIsNone(err)
        self.assertEqual(len(mods), 1)
        self.assertEqual(
            [os.path.basename(x) for x in xmls], ["modIDDConfig.xml"]
        )
        # the xml path sits under the mod folder - the ordering hazard
        self.assertTrue(xmls[0].startswith(mods[0]))

    def test_filelist_remove_strips_only_the_entry(self):
        pc = os.path.join(self.install, *main.W3_MENU_DIR.split("/"))
        os.makedirs(pc)
        with open(os.path.join(pc, "dx11filelist.txt"), "w") as f:
            f.write("audio.xml;" + chr(10) + "modCool.xml;" + chr(10))
        main._w3_filelist_remove(pc, "modCool.xml")
        content = open(os.path.join(pc, "dx11filelist.txt")).read()
        self.assertNotIn("modCool.xml", content)
        self.assertIn("audio.xml;", content)

    def test_remove_menu_xmls_deletes_files_and_filelist_lines(self):
        pc = os.path.join(self.install, *main.W3_MENU_DIR.split("/"))
        os.makedirs(pc)
        with open(os.path.join(pc, "modCool.xml"), "w") as f:
            f.write("<x/>")
        main._w3_filelist_append(pc, "modCool.xml")
        main._w3_remove_menu_xmls(self.install, {"menuXmls": ["modCool.xml"]})
        self.assertFalse(os.path.exists(os.path.join(pc, "modCool.xml")))
        content = open(os.path.join(pc, "dx11filelist.txt")).read()
        self.assertNotIn("modCool.xml", content)

    def test_reset_witcher_sweeps_orphans_and_menu_xmls(self):
        # Crashed installs strand unrecorded folders (bricked a boot on
        # device) - the witcher reset sweeps the whole mods dir + menu
        # registrations, records or not.
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        orphan = os.path.join(self.mods, "modOrphan", "content")
        os.makedirs(orphan)
        pc = os.path.join(self.install, *main.W3_MENU_DIR.split("/"))
        os.makedirs(pc)
        with open(os.path.join(pc, "modStale.xml"), "w") as f:
            f.write("<x/>")
        # NOT mod-prefixed: the naturaltorchlight class that survived the
        # old prefix sweep and crashed the game via its filelist line
        with open(os.path.join(pc, "naturaltorchlight.xml"), "w") as f:
            f.write("<x/>")
        with open(os.path.join(pc, "audio.xml"), "w") as f:
            f.write("<x/>")
        main._w3_filelist_append(pc, "modStale.xml")
        main._w3_filelist_append(pc, "naturaltorchlight.xml")
        # a dangling line with NO file behind it at all
        main._w3_filelist_append(pc, "ghost.xml")
        result = run(
            main.Plugin().reset_game_modding(
                "witcher3", self.GAME, "mods", "folder", 0, "", "starred",
                [], True,
            )
        )
        self.assertTrue(result["ok"])
        self.assertFalse(os.path.isdir(self.mods))
        self.assertFalse(os.path.exists(os.path.join(pc, "modStale.xml")))
        self.assertFalse(
            os.path.exists(os.path.join(pc, "naturaltorchlight.xml"))
        )
        self.assertTrue(os.path.exists(os.path.join(pc, "audio.xml")))
        content = open(os.path.join(pc, "dx11filelist.txt")).read()
        self.assertNotIn("modStale.xml", content)
        self.assertNotIn("naturaltorchlight.xml", content)
        self.assertNotIn("ghost.xml", content)

    def test_vanilla_menu_xml_restored_on_uninstall(self):
        # HD Reworked overwrites the game's own rendering.xml - uninstall
        # must restore the vanilla file, never delete it or strip its
        # filelist line.
        pc = os.path.join(self.install, *main.W3_MENU_DIR.split("/"))
        os.makedirs(pc)
        with open(os.path.join(pc, "rendering.xml"), "w") as f:
            f.write("<vanilla/>")
        with open(os.path.join(pc, "dx11filelist.txt"), "w") as f:
            f.write("rendering.xml;" + chr(10))
        # simulate the install's backup-then-overwrite
        shutil.copy2(
            os.path.join(pc, "rendering.xml"),
            os.path.join(pc, "rendering.xml" + main.W3_VANILLA_BACKUP_SUFFIX),
        )
        with open(os.path.join(pc, "rendering.xml"), "w") as f:
            f.write("<modded/>")
        main._w3_remove_menu_xmls(
            self.install, {"menuXmls": ["rendering.xml"]}
        )
        content = open(os.path.join(pc, "rendering.xml")).read()
        self.assertEqual(content, "<vanilla/>")
        self.assertFalse(
            os.path.exists(
                os.path.join(
                    pc, "rendering.xml" + main.W3_VANILLA_BACKUP_SUFFIX
                )
            )
        )
        filelist = open(os.path.join(pc, "dx11filelist.txt")).read()
        self.assertIn("rendering.xml;", filelist)

    def test_merge_survives_mixed_line_endings(self):
        # Mod files mix CRLF/LF freely - EOL noise must not read as a
        # conflict (it made EVERY real-world merge fail as unmergeable).
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        base = os.path.join(
            self.install, *main.W3_VANILLA_SCRIPTS.split("/"), "game",
            "hit.ws",
        )
        os.makedirs(os.path.dirname(base))
        with open(base, "wb") as f:
            f.write(b"a\r\nb\r\nc\r\nd\r\ne\r\n")  # vanilla: CRLF
        owner = os.path.join(
            self.mods, "modExisting", "content", "scripts", "game", "hit.ws"
        )
        os.makedirs(os.path.dirname(owner))
        with open(owner, "wb") as f:
            f.write(b"a\nOWNER\nc\nd\ne\n")  # mod author: LF
        incoming = os.path.join(self.scratch, "incoming_hit.ws")
        with open(incoming, "wb") as f:
            f.write(b"a\r\nb\r\nc\r\nNEW\r\ne\r\n")
        settings = main._load_settings()
        rels = main._w3_try_merge_conflicts(
            "witcher3", self.install, self.mods,
            [("game/hit.ws", "modExisting", incoming, owner)], settings,
        )
        self.assertEqual(rels, ["game/hit.ws"])

    def test_merge_handles_utf16_script_side(self):
        # Immersive Realtime Cutscenes ships r4player.ws as UTF-16 -
        # decoded as UTF-8 it merged NUL garbage into the game.
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        base = os.path.join(
            self.install, *main.W3_VANILLA_SCRIPTS.split("/"), "game",
            "hit.ws",
        )
        os.makedirs(os.path.dirname(base))
        with open(base, "wb") as f:
            f.write("a\r\nb\r\nc\r\nd\r\ne\r\n".encode("utf-8"))
        owner = os.path.join(
            self.mods, "modExisting", "content", "scripts", "game", "hit.ws"
        )
        os.makedirs(os.path.dirname(owner))
        with open(owner, "wb") as f:
            f.write(b"\xff\xfe" + "a\r\nOWNER\r\nc\r\nd\r\ne\r\n".encode("utf-16-le"))
        incoming = os.path.join(self.scratch, "incoming_hit.ws")
        with open(incoming, "wb") as f:
            f.write("a\r\nb\r\nc\r\nNEW\r\ne\r\n".encode("utf-8"))
        settings = main._load_settings()
        rels = main._w3_try_merge_conflicts(
            "witcher3", self.install, self.mods,
            [("game/hit.ws", "modExisting", incoming, owner)], settings,
        )
        self.assertEqual(rels, ["game/hit.ws"])
        merged = os.path.join(
            self.mods, main.W3_MERGED_MOD, "content", "scripts", "game",
            "hit.ws",
        )
        raw = open(merged, "rb").read()
        self.assertNotIn(b"\x00", raw)
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))  # UTF-8 BOM
        self.assertIn(b"OWNER", raw)
        self.assertIn(b"NEW", raw)

    def test_merge3_deadline_raises_and_try_merge_degrades(self):
        # A blown budget must degrade to "unmergeable", never freeze the
        # backend (r4player.ws is difflib's quadratic worst case).
        with self.assertRaises(TimeoutError):
            main._w3_merge3(["a"], ["b"], ["c"], deadline=0)

    def test_merge3_combines_distinct_regions(self):
        base = ["a\n", "b\n", "c\n", "d\n", "e\n"]
        ours = ["a\n", "B\n", "c\n", "d\n", "e\n"]     # changed line 2
        theirs = ["a\n", "b\n", "c\n", "D\n", "e\n"]   # changed line 4
        merged = main._w3_merge3(base, ours, theirs)
        self.assertEqual(merged, ["a\n", "B\n", "c\n", "D\n", "e\n"])

    def test_merge3_identical_changes_collapse(self):
        base = ["a\n", "b\n", "c\n"]
        ours = ["a\n", "X\n", "c\n"]
        theirs = ["a\n", "X\n", "c\n"]
        merged = main._w3_merge3(base, ours, theirs)
        self.assertEqual(merged, ["a\n", "X\n", "c\n"])

    def test_merge3_overlapping_changes_refuse(self):
        base = ["a\n", "b\n", "c\n"]
        ours = ["a\n", "OURS\n", "c\n"]
        theirs = ["a\n", "THEIRS\n", "c\n"]
        self.assertIsNone(main._w3_merge3(base, ours, theirs))

    def test_merge3_additions_at_different_points(self):
        base = ["a\n", "b\n", "c\n"]
        ours = ["new_top\n", "a\n", "b\n", "c\n"]
        theirs = ["a\n", "b\n", "c\n", "new_bottom\n"]
        merged = main._w3_merge3(base, ours, theirs)
        self.assertEqual(
            merged, ["new_top\n", "a\n", "b\n", "c\n", "new_bottom\n"]
        )

    def test_merge_conflicts_end_to_end_and_unmerge(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        # vanilla base
        base = os.path.join(
            self.install, *main.W3_VANILLA_SCRIPTS.split("/"), "game",
            "hit.ws",
        )
        os.makedirs(os.path.dirname(base))
        with open(base, "w") as f:
            f.write("a\nb\nc\nd\ne\n")
        # installed owner changed line 2
        owner = os.path.join(
            self.mods, "modExisting", "content", "scripts", "game", "hit.ws"
        )
        os.makedirs(os.path.dirname(owner))
        with open(owner, "w") as f:
            f.write("a\nOWNER\nc\nd\ne\n")
        # incoming mod changed line 4
        incoming = os.path.join(self.scratch, "incoming_hit.ws")
        with open(incoming, "w") as f:
            f.write("a\nb\nc\nNEW\ne\n")
        settings = main._load_settings()
        rels = main._w3_try_merge_conflicts(
            "witcher3", self.install, self.mods,
            [("game/hit.ws", "modExisting", incoming, owner)], settings,
        )
        self.assertEqual(rels, ["game/hit.ws"])
        main._w3_register_merge_participant(
            "witcher3", settings, rels, "modNew"
        )
        merged_path = os.path.join(
            self.mods, main.W3_MERGED_MOD, "content", "scripts", "game",
            "hit.ws",
        )
        self.assertEqual(
            open(merged_path, encoding="utf-8-sig").read().splitlines(),
            ["a", "OWNER", "c", "NEW", "e"],
        )
        self.assertEqual(
            settings["w3_merges"]["witcher3"]["game/hit.ws"]["mods"],
            ["modExisting", "modNew"],
        )
        # the new mod's own copy on disk (as the install would place it)
        newcopy = os.path.join(
            self.mods, "modNew", "content", "scripts", "game", "hit.ws"
        )
        os.makedirs(os.path.dirname(newcopy))
        with open(newcopy, "w") as f:
            f.write("a\nb\nc\nNEW\ne\n")
        # uninstall the new mod: one participant left -> merged copy goes,
        # the owner's own file wins again
        main._w3_unmerge(
            "witcher3", self.install, self.mods, "modNew", settings
        )
        self.assertFalse(os.path.exists(merged_path))
        self.assertNotIn(
            "game/hit.ws", settings["w3_merges"]["witcher3"]
        )

    def test_merge_conflicts_refuse_on_overlap(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        base = os.path.join(
            self.install, *main.W3_VANILLA_SCRIPTS.split("/"), "game",
            "hit.ws",
        )
        os.makedirs(os.path.dirname(base))
        with open(base, "w") as f:
            f.write("a\nb\nc\n")
        owner = os.path.join(
            self.mods, "modExisting", "content", "scripts", "game", "hit.ws"
        )
        os.makedirs(os.path.dirname(owner))
        with open(owner, "w") as f:
            f.write("a\nOWNER\nc\n")
        incoming = os.path.join(self.scratch, "incoming_hit.ws")
        with open(incoming, "w") as f:
            f.write("a\nTHEIRS\nc\n")
        settings = main._load_settings()
        rels = main._w3_try_merge_conflicts(
            "witcher3", self.install, self.mods,
            [("game/hit.ws", "modExisting", incoming, owner)], settings,
        )
        self.assertIsNone(rels)

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


class TestDloLaunchOptions(unittest.TestCase):
    """Undoing a framework's launch command on a decky-launch-options
    device means editing dlo's profile - clearing Steam's field leaves the
    stale command in dlo's replay (bricked the Skyrim reset, 2026-07-23)."""

    SKSE_SWAP = (
        "bash -c 'exec \"${@/SkyrimSELauncher.exe/skse64_loader.exe}\"'"
        " -- %command%"
    )

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.tmp = tempfile.mkdtemp()
        self.dlo_path = os.path.join(self.tmp, "settings.json")
        with open(self.dlo_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "profiles": {
                        "489830": {
                            "state": {},
                            "originalLaunchOptions": self.SKSE_SWAP,
                        },
                        "22370": {"state": {}, "originalLaunchOptions": ""},
                    },
                    "launchOptions": [],
                },
                f,
                indent=4,
            )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_original(self):
        self.assertEqual(
            main._dlo_get_original(self.dlo_path, 489830), self.SKSE_SWAP
        )
        self.assertIsNone(main._dlo_get_original(self.dlo_path, 999999))
        self.assertIsNone(main._dlo_get_original("/nonexistent", 489830))

    def test_clear_returns_previous_and_preserves_others(self):
        ok, previous = main._dlo_set_original(self.dlo_path, 489830, "")
        self.assertTrue(ok)
        self.assertEqual(previous, self.SKSE_SWAP)
        with open(self.dlo_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(
            data["profiles"]["489830"]["originalLaunchOptions"], ""
        )
        # sibling profiles and dlo's other keys untouched
        self.assertIn("22370", data["profiles"])
        self.assertIn("launchOptions", data)

    def test_set_creates_missing_profile(self):
        ok, previous = main._dlo_set_original(
            self.dlo_path, 377160, "loader.exe %command%"
        )
        self.assertTrue(ok)
        self.assertEqual(previous, "")
        self.assertEqual(
            main._dlo_get_original(self.dlo_path, 377160),
            "loader.exe %command%",
        )

    def test_missing_file_fails_gracefully(self):
        ok, previous = main._dlo_set_original("/nonexistent/x.json", 1, "")
        self.assertFalse(ok)
        self.assertIsNone(previous)

    def test_parse_vdf_launch_options(self):
        vdf = (
            '"apps"\n{\n'
            '\t"489830"\n\t{\n'
            '\t\t"LastPlayed"\t\t"123"\n'
            '\t\t"LaunchOptions"\t\t"~/.dlo/run %command%"\n'
            "\t}\n"
            '\t"413150"\n\t{\n'
            '\t\t"LaunchOptions"\t\t"\\"path/StardewModdingAPI\\" %command%"\n'
            "\t}\n}\n"
        )
        self.assertEqual(
            main._parse_vdf_launch_options(vdf, 489830),
            ["~/.dlo/run %command%"],
        )
        self.assertEqual(
            main._parse_vdf_launch_options(vdf, 413150),
            ['\\"path/StardewModdingAPI\\" %command%'],
        )
        self.assertEqual(main._parse_vdf_launch_options(vdf, 999), [])

    def test_clear_callable_clears_dlo_and_unmarks_step(self):
        plugin = main.Plugin()
        run(plugin.mark_launch_options_set("skyrimspecialedition"))
        orig = main._dlo_settings_path
        main._dlo_settings_path = lambda: self.dlo_path
        try:
            result = run(
                plugin.clear_framework_launch_options(
                    489830, "skyrimspecialedition"
                )
            )
        finally:
            main._dlo_settings_path = orig
        self.assertTrue(result["ok"])
        self.assertTrue(result["cleared_dlo"])
        self.assertFalse(result["use_steam_client"])
        self.assertEqual(main._dlo_get_original(self.dlo_path, 489830), "")
        state = run(plugin.get_framework_setup("skyrimspecialedition"))
        self.assertFalse(state["launch_options_set"])

    def test_clear_callable_without_dlo_defers_to_steam_client(self):
        plugin = main.Plugin()
        run(plugin.mark_launch_options_set("skyrimspecialedition"))
        orig = main._dlo_settings_path
        main._dlo_settings_path = lambda: os.path.join(self.tmp, "absent.json")
        try:
            result = run(
                plugin.clear_framework_launch_options(
                    489830, "skyrimspecialedition"
                )
            )
        finally:
            main._dlo_settings_path = orig
        self.assertTrue(result["ok"])
        self.assertFalse(result["cleared_dlo"])
        self.assertTrue(result["use_steam_client"])
        state = run(plugin.get_framework_setup("skyrimspecialedition"))
        self.assertFalse(state["launch_options_set"])

    def test_set_callable_writes_dlo_and_marks_step(self):
        plugin = main.Plugin()
        orig = main._dlo_settings_path
        main._dlo_settings_path = lambda: self.dlo_path
        try:
            result = run(
                plugin.set_framework_launch_options(
                    489830, "skyrimspecialedition", "new_loader %command%"
                )
            )
        finally:
            main._dlo_settings_path = orig
        self.assertTrue(result["ok"])
        self.assertEqual(result["previous"], self.SKSE_SWAP)
        self.assertEqual(
            main._dlo_get_original(self.dlo_path, 489830),
            "new_loader %command%",
        )
        state = run(plugin.get_framework_setup("skyrimspecialedition"))
        self.assertTrue(state["launch_options_set"])

    def test_set_callable_without_dlo_defers_to_steam_client(self):
        plugin = main.Plugin()
        orig = main._dlo_settings_path
        main._dlo_settings_path = lambda: os.path.join(self.tmp, "absent.json")
        try:
            result = run(
                plugin.set_framework_launch_options(
                    489830, "skyrimspecialedition", "x %command%"
                )
            )
        finally:
            main._dlo_settings_path = orig
        self.assertFalse(result["ok"])
        self.assertTrue(result["use_steam_client"])


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


class TestHostEnv(unittest.TestCase):
    """Decky Loader is a PyInstaller bundle, so plugins inherit an
    LD_LIBRARY_PATH aimed at its unpacked /tmp/_MEIxxxxxx directory. The
    older libreadline in there kills /bin/sh outright, and SteamOS ships
    7z as a /bin/sh wrapper - which is why 7z never ran and the
    three-deep extractor fallback was really two deep."""

    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "LD_LIBRARY_PATH",
                "LD_LIBRARY_PATH_ORIG",
                "LD_PRELOAD",
                "LD_PRELOAD_ORIG",
            )
        }
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_drops_pyinstaller_library_path(self):
        os.environ["LD_LIBRARY_PATH"] = "/tmp/_MEIabc123"
        self.assertNotIn("LD_LIBRARY_PATH", main._host_env())

    def test_restores_the_original_when_there_was_one(self):
        # Only PyInstaller's own addition goes; a value the system had
        # set before must survive, or we break tools that needed it.
        os.environ["LD_LIBRARY_PATH"] = "/tmp/_MEIabc123"
        os.environ["LD_LIBRARY_PATH_ORIG"] = "/opt/real/lib"
        env = main._host_env()
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/real/lib")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", env)

    def test_drops_ld_preload_too(self):
        os.environ["LD_PRELOAD"] = "/tmp/_MEIabc123/libsomething.so"
        self.assertNotIn("LD_PRELOAD", main._host_env())

    def test_keeps_the_rest_of_the_environment(self):
        os.environ["LD_LIBRARY_PATH"] = "/tmp/_MEIabc123"
        env = main._host_env()
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))

    def test_extra_values_are_added(self):
        env = main._host_env({"STEAM_COMPAT_DATA_PATH": "/x"})
        self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], "/x")

    def test_every_subprocess_passes_an_env(self):
        """A spawn without env= inherits the poisoned one. This is the
        test that stops the fix rotting the next time a tool is added -
        the failure mode is silent and looks like the tool's fault."""
        path = os.path.join(os.path.dirname(__file__), "..", "main.py")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source)
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None)
            if name not in ("create_subprocess_exec", "create_subprocess_shell"):
                continue
            if not any(kw.arg == "env" for kw in node.keywords):
                bad.append(node.lineno)
        self.assertEqual(bad, [], f"subprocess spawn without env= at lines {bad}")


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

    def test_case_merge_cache_matches_uncached_results(self):
        """The per-install directory cache is a speed optimisation only -
        it must resolve identically to re-reading the directory each time.
        A 2,000-file mod was spending its install re-listing Data/textures
        once per file."""
        base = os.path.join(TEST_ROOT, "case-merge-cached")
        shutil.rmtree(base, ignore_errors=True)
        os.makedirs(os.path.join(base, "Textures", "terrain"))
        os.makedirs(os.path.join(base, "Meshes"))
        rels = [
            "textures/armor/a.dds",
            "TEXTURES/TERRAIN/map.dds",
            "meshes/weapons/x.nif",
            "scripts/source/y.psc",
        ]
        cache: dict = {}
        for rel in rels:
            self.assertEqual(
                main._case_merge_rel(base, rel, cache),
                main._case_merge_rel(base, rel),
                rel,
            )

    def test_case_merge_cache_keeps_one_spelling_for_a_new_dir(self):
        """The bug this function exists to prevent, via the cache: two
        files in ONE mod naming the same new directory with different
        casing must land in the same directory. Uncached, the first
        file's mkdir made the second find it on disk; cached, the choice
        has to be remembered instead."""
        base = os.path.join(TEST_ROOT, "case-merge-new")
        shutil.rmtree(base, ignore_errors=True)
        os.makedirs(base)
        cache: dict = {}
        first = main._case_merge_rel(base, "SKSE/Plugins/a.dll", cache)
        second = main._case_merge_rel(base, "skse/plugins/b.dll", cache)
        self.assertEqual(first, "SKSE/Plugins/a.dll")
        self.assertEqual(second, "SKSE/Plugins/b.dll")
        # Same directory, so the game (and Wine) sees one tree, not two.
        self.assertEqual(
            os.path.dirname(first), os.path.dirname(second)
        )

    def test_case_merge_cache_is_not_shared_between_installs(self):
        """A fresh cache must see directories created since - each install
        builds its own, so a mod installed later adopts the casing of one
        installed earlier."""
        base = os.path.join(TEST_ROOT, "case-merge-fresh")
        shutil.rmtree(base, ignore_errors=True)
        os.makedirs(base)
        main._case_merge_rel(base, "Interface/a.swf", {})
        os.makedirs(os.path.join(base, "Interface"))
        self.assertEqual(
            main._case_merge_rel(base, "interface/b.swf", {}),
            "Interface/b.swf",
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
        # Asset-only mods ARE toggleable now: with no plugin to untick,
        # their files are parked out of Data instead. They used to be
        # marked untoggleable, which meant a UI overhaul or texture pack
        # could not be switched off at all - on device three interface
        # mods had to be uninstalled, losing the downloads, to get New
        # Vegas to start.
        self.assertTrue(by_name["TextureOnly"]["enabled"])
        self.assertTrue(by_name["TextureOnly"]["togglable"])

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

    def test_toggle_asset_only_mod_parks_its_files(self):
        """Previously refused outright - "its assets are always active".
        A mod with no plugin is switched off by moving its files out of
        Data, and back when it is switched on."""
        self.seed_mod("TextureOnly", ["textures/armor/shiny.dds"], [])
        shiny = os.path.join(
            main.STEAM_COMMON, self.GAME, "Data", "textures", "armor",
            "shiny.dds",
        )
        self.assertTrue(os.path.isfile(shiny))

        result = run(
            self.plugin.set_mod_enabled(
                self.GAME, "Data", "TextureOnly", False, *self.toggle_args()
            )
        )
        self.assertTrue(result["ok"], result)
        self.assertFalse(os.path.isfile(shiny))

        result = run(
            self.plugin.set_mod_enabled(
                self.GAME, "Data", "TextureOnly", True, *self.toggle_args()
            )
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(os.path.isfile(shiny))

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


class TestContentGate(unittest.TestCase):
    """Adult content is account-driven: site preference AND platform age
    verification must both hold (UK OSA - verification happens on the
    Nexus Mods platform, never on-device). No local override exists."""

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.plugin = main.Plugin()

    def _seed_gate(self, adult_pref, age_verified, api_key="k"):
        settings = main._load_settings()
        if api_key:
            settings["api_key"] = api_key
        settings["content_gate"] = {
            "adult_pref": adult_pref,
            "age_verified": age_verified,
            "blur_images": False,
            "checked_at": 0,
        }
        main._save_settings(settings)

    def test_defaults_closed_with_no_cached_gate(self):
        self.assertFalse(main._show_adult())

    def test_preference_alone_is_not_enough(self):
        self._seed_gate(adult_pref=True, age_verified=False)
        self.assertFalse(main._show_adult())

    def test_verification_alone_is_not_enough(self):
        self._seed_gate(adult_pref=False, age_verified=True)
        self.assertFalse(main._show_adult())

    def test_preference_plus_verification_opens_the_gate(self):
        self._seed_gate(adult_pref=True, age_verified=True)
        self.assertTrue(main._show_adult())

    def test_get_show_adult_reports_components(self):
        self._seed_gate(adult_pref=True, age_verified=False)
        result = run(self.plugin.get_show_adult())
        self.assertTrue(result["ok"])
        self.assertFalse(result["show_adult"])
        self.assertTrue(result["adult_pref"])
        self.assertFalse(result["age_verified"])

    def test_set_show_adult_has_no_local_override(self):
        result = run(self.plugin.set_show_adult(True))
        self.assertFalse(result["ok"])
        self.assertFalse(main._show_adult())

    def test_refresh_parses_graphql_and_caches(self):
        async def fake_gql(query, api_key=None):
            self.assertIn("preferences", query)
            self.assertIn("ageVerificationInfo", query)
            return {
                "preferences": {"adult": True, "adultBlurImages": False},
                "ageVerificationInfo": {"verified": True},
            }

        settings = main._load_settings()
        settings["api_key"] = "k"
        main._save_settings(settings)
        original = main._gql_query
        main._gql_query = fake_gql
        try:
            result = run(self.plugin.refresh_content_gate())
        finally:
            main._gql_query = original
        self.assertTrue(result["ok"])
        self.assertTrue(result["show_adult"])
        self.assertTrue(main._show_adult())

    def test_refresh_failure_keeps_cached_gate(self):
        self._seed_gate(adult_pref=True, age_verified=True)

        async def broken_gql(query, api_key=None):
            raise RuntimeError("API down")

        original = main._gql_query
        main._gql_query = broken_gql
        try:
            result = run(self.plugin.refresh_content_gate())
        finally:
            main._gql_query = original
        self.assertFalse(result["ok"])
        self.assertTrue(main._show_adult())

    def test_signed_out_clears_the_gate(self):
        self._seed_gate(adult_pref=True, age_verified=True, api_key=None)
        result = run(self.plugin.refresh_content_gate())
        self.assertFalse(result["ok"])
        self.assertFalse(main._show_adult())
        self.assertNotIn("content_gate", main._load_settings())

    def test_gate_adult_nodes_agrees_with_open_gate(self):
        # The v0.37.0 regression: gate open, but a stale client-side pass
        # still dropped every adult node ("search says 39, shows 6").
        self._seed_gate(adult_pref=True, age_verified=True)
        nodes = [{"name": "a", "adultContent": True}, {"name": "b"}]
        self.assertEqual(len(main._gate_adult_nodes(nodes)), 2)

    def test_gate_adult_nodes_filters_when_closed(self):
        nodes = [{"name": "a", "adultContent": True}, {"name": "b"}]
        self.assertEqual(
            [m["name"] for m in main._gate_adult_nodes(nodes)], ["b"]
        )
        v1 = [{"name": "a", "contains_adult_content": True}, {"name": "b"}]
        self.assertEqual(
            [m["name"] for m in main._gate_adult_nodes(v1, "contains_adult_content")],
            ["b"],
        )


class TestPrefixRuntime(unittest.TestCase):
    """CP77's install script downgrades the prefix VC++ runtime to 14.28;
    CET/RED4ext (built with VS 17.10+) then fail to load with error 998.
    fix_prefix_runtime copies the newest Proton-bundled CRT over."""

    APP_ID = 999001

    @staticmethod
    def _fake_pe(version):
        """Minimal blob carrying a VS_FIXEDFILEINFO signature + version."""
        major, minor, build, rev = version
        ms = (major << 16) | minor
        ls = (build << 16) | rev
        return (
            b"MZ" + b"\x00" * 62
            + b"\xbd\x04\xef\xfe"          # VS_FIXEDFILEINFO signature
            + b"\x00\x00\x01\x00"          # dwStrucVersion
            + ms.to_bytes(4, "little")
            + ls.to_bytes(4, "little")
            + b"\x00" * 16
        )

    def setUp(self):
        self.plugin = main.Plugin()
        self.sys32 = main._prefix_system32(self.APP_ID)
        os.makedirs(self.sys32, exist_ok=True)
        self.proton = os.path.join(
            main.STEAM_COMMON, "Proton 11.0", "files", "lib", "wine",
            "x86_64-windows",
        )
        os.makedirs(self.proton, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(os.path.dirname(os.path.dirname(self.sys32)), ignore_errors=True)
        shutil.rmtree(os.path.join(main.STEAM_COMMON, "Proton 11.0"), ignore_errors=True)

    def _write(self, directory, name, version):
        with open(os.path.join(directory, name), "wb") as f:
            f.write(self._fake_pe(version))

    def test_pe_version_parses_fixedfileinfo(self):
        self._write(self.sys32, "probe.dll", (14, 28, 29334, 0))
        self.assertEqual(
            main._pe_file_version(os.path.join(self.sys32, "probe.dll")),
            (14, 28, 29334, 0),
        )

    def test_upgrades_old_crt_and_backs_up(self):
        for name in main.CRT_DLLS:
            self._write(self.proton, name, (14, 42, 34433, 0))
        self._write(self.sys32, "msvcp140.dll", (14, 28, 29334, 0))
        self._write(self.sys32, "vcruntime140.dll", (14, 28, 29334, 0))
        result = run(self.plugin.fix_prefix_runtime(self.APP_ID))
        self.assertTrue(result["ok"])
        self.assertTrue(result["updated"])
        self.assertEqual(result["version"], "14.42.34433.0")
        self.assertEqual(
            main._pe_file_version(os.path.join(self.sys32, "msvcp140.dll")),
            (14, 42, 34433, 0),
        )
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.sys32, "msvcp140.dll" + main.CRT_BACKUP_SUFFIX)
            )
        )

    def test_current_crt_left_alone(self):
        for name in main.CRT_DLLS:
            self._write(self.proton, name, (14, 42, 34433, 0))
        self._write(self.sys32, "msvcp140.dll", (14, 44, 35211, 0))
        result = run(self.plugin.fix_prefix_runtime(self.APP_ID))
        self.assertTrue(result["ok"])
        self.assertFalse(result["updated"])

    def test_missing_prefix_reports_cleanly(self):
        result = run(self.plugin.fix_prefix_runtime(424242))
        self.assertFalse(result["ok"])
        self.assertIn("launch the game once", result["error"])


class TestPluginMtimeStagger(unittest.TestCase):
    """FO3/FNV load plugins by file TIMESTAMP. A mod ESM extracted with a
    Jan-2000 archive mtime loaded before its own master on device and the
    game refused to boot. Restamp: vanilla masters first (canonical
    order, regardless of plugins.txt order), mod esms, then esps."""

    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="stagger-", dir=TEST_ROOT)
        self.ptxt = os.path.join(self.data, "Plugins.txt")

    def tearDown(self):
        shutil.rmtree(self.data, ignore_errors=True)

    def _seed(self, name, mtime):
        p = os.path.join(self.data, name)
        with open(p, "wb") as f:
            f.write(b"x")
        os.utime(p, (mtime, mtime))

    def test_restamps_vanilla_first_then_esms_then_esps(self):
        # plugins.txt deliberately lists a DLC BEFORE Fallout3.esm (seen
        # live) and a mod esm carries a Jan-2000 mtime.
        ancient = 946684800  # 2000-01-01
        self._seed("Anchorage.esm", 1700000200)
        self._seed("Fallout3.esm", 1700000100)
        self._seed("OldMod.esm", ancient)
        self._seed("SomeMod.esp", ancient)
        main._write_plugins_txt(
            self.ptxt,
            ["Anchorage.esm", "Fallout3.esm", "OldMod.esm", "SomeMod.esp"],
        )
        stamped = main._stagger_plugin_mtimes(
            self.data, self.ptxt, "listed", "fallout3"
        )
        self.assertEqual(stamped, 4)
        mt = lambda n: os.path.getmtime(os.path.join(self.data, n))
        self.assertLess(mt("Fallout3.esm"), mt("Anchorage.esm"))
        self.assertLess(mt("Anchorage.esm"), mt("OldMod.esm"))
        self.assertLess(mt("OldMod.esm"), mt("SomeMod.esp"))
        # Everything in the past so the next install lands after.
        self.assertLess(mt("SomeMod.esp"), time.time())

    def test_starred_style_untouched(self):
        self._seed("Mod.esp", 946684800)
        main._write_plugins_txt(self.ptxt, ["*Mod.esp"])
        stamped = main._stagger_plugin_mtimes(
            self.data, self.ptxt, "starred", "skyrimspecialedition"
        )
        self.assertEqual(stamped, 0)
        self.assertEqual(
            os.path.getmtime(os.path.join(self.data, "Mod.esp")), 946684800
        )

    def _seed_plugin(self, name, masters, mtime=946684800):
        p = os.path.join(self.data, name)
        with open(p, "wb") as f:
            f.write(TestPluginMasters._fake_plugin(masters))
        os.utime(p, (mtime, mtime))

    def test_dependency_order_beats_list_order(self):
        # Live case: a patch esp LISTED BEFORE the esp it masters, and a
        # mod esm mastering another mod esm - plugins.txt order produced
        # loads-before-master boot crashes.
        self._seed_plugin("AWOP Patch.esp", ["Fairfax.esp"])
        self._seed_plugin("Fairfax.esp", [])
        self._seed_plugin("Ranger.esm", ["DCInteriors.esm"])
        self._seed_plugin("DCInteriors.esm", [])
        main._write_plugins_txt(
            self.ptxt,
            ["Ranger.esm", "DCInteriors.esm", "AWOP Patch.esp", "Fairfax.esp"],
        )
        main._stagger_plugin_mtimes(self.data, self.ptxt, "listed", "fallout3")
        mt = lambda n: os.path.getmtime(os.path.join(self.data, n))
        self.assertLess(mt("DCInteriors.esm"), mt("Ranger.esm"))
        self.assertLess(mt("Fairfax.esp"), mt("AWOP Patch.esp"))
        # esm block still precedes esp block
        self.assertLess(mt("Ranger.esm"), mt("Fairfax.esp"))


class TestPrefixToolFilePick(unittest.TestCase):
    """The ESM Patcher (mod 25717) publishes PAIRED English/French MAIN
    files. The shared picker chose French - 174MB that then failed to
    unpack - so run_prefix_tool filters avoid-keywords itself across BOTH
    name and file_name, case-insensitively, and takes the newest MAIN."""

    # Trimmed from the live file list (2026-08-05).
    FILES = [
        {"file_id": 1000025119, "name": "Unofficial Fallout 3 ESM Patcher",
         "file_name": "Unofficial Fallout 3 ESM Patcher-25717-1-3.7z",
         "category_name": "OLD_VERSION", "is_primary": True},
        {"file_id": 1000026334, "name": "Installation Guide",
         "file_name": "Installation Guide-25717-G1-1.7z",
         "category_name": "OPTIONAL", "is_primary": False},
        {"file_id": 1000030170, "name": "Unofficial Fallout 3 ESM Patcher",
         "file_name": "Unofficial Fallout 3 ESM Patcher-25717-1-8.7z",
         "category_name": "MAIN", "is_primary": False},
        {"file_id": 1000030171,
         "name": "Patcher d'ESMs non officiel pour Fallout 3",
         "file_name": "Patcher d'ESMs non officiel pour Fallout 3-25717-1-8.7z",
         "category_name": "MAIN", "is_primary": False},
    ]

    @staticmethod
    def _pick(files, avoid):
        """Mirrors run_prefix_tool's selection block."""
        avoid = [k.lower() for k in avoid]
        cands = [
            f for f in files
            if not any(
                k in f"{f.get('name','')} {f.get('file_name','')}".lower()
                for k in avoid
            )
        ]
        mains = [
            f for f in cands
            if str(f.get("category_name", "")).upper() == "MAIN"
        ]
        pool = mains or cands
        return max(pool, key=lambda f: int(f["file_id"]), default=None)

    def test_picks_english_main_not_french(self):
        got = self._pick(self.FILES, ["non officiel", "guide"])
        self.assertEqual(got["file_id"], 1000030170)
        self.assertNotIn("officiel", got["name"].lower())

    def test_ignores_stale_primary_old_version(self):
        got = self._pick(self.FILES, ["non officiel", "guide"])
        self.assertEqual(got["category_name"], "MAIN")

    def test_avoid_matching_is_case_insensitive(self):
        got = self._pick(self.FILES, ["NON OFFICIEL", "GUIDE"])
        self.assertEqual(got["file_id"], 1000030170)


class TestProtonPicker(unittest.TestCase):
    """run_prefix_tool must use the Proton release that OWNS the game's
    prefix (compatdata/<id>/version), not whatever is newest."""

    APP_ID = 999004

    def setUp(self):
        self.steam_root = os.path.join(
            os.environ.get("DECKY_TEST_HOME", ""), ""
        )
        self.compat = os.path.join(
            main.decky.DECKY_USER_HOME, ".steam", "steam", "steamapps",
            "compatdata", str(self.APP_ID),
        )
        os.makedirs(self.compat, exist_ok=True)
        self.proton11 = os.path.join(main.STEAM_COMMON, "Proton 11.0")
        self.experimental = os.path.join(
            main.STEAM_COMMON, "Proton - Experimental"
        )

    def tearDown(self):
        shutil.rmtree(self.compat, ignore_errors=True)
        shutil.rmtree(self.proton11, ignore_errors=True)
        shutil.rmtree(self.experimental, ignore_errors=True)

    def _mk_proton(self, dirpath):
        os.makedirs(dirpath, exist_ok=True)
        with open(os.path.join(dirpath, "proton"), "w") as f:
            f.write("#!stub")

    def test_unpinned_prefers_experimental(self):
        # The prefix version file says "11.0-100" for BOTH standalone
        # Proton 11.0 AND Experimental - trusting it picked the wrong
        # build on device and wedged the prefix. Unpinned games run the
        # SteamOS default: Experimental wins even when 11.0 is installed.
        with open(os.path.join(self.compat, "version"), "w") as f:
            f.write("11.0-100\n")
        self._mk_proton(self.proton11)
        self._mk_proton(self.experimental)
        proton, compat, _root, err = main._proton_binary_for(self.APP_ID)
        self.assertEqual(err, "")
        self.assertIn("Experimental", proton)
        self.assertEqual(compat, self.compat)

    def test_version_file_is_the_fallback(self):
        with open(os.path.join(self.compat, "version"), "w") as f:
            f.write("11.0-100\n")
        self._mk_proton(self.proton11)  # no Experimental installed
        proton, _c, _r, err = main._proton_binary_for(self.APP_ID)
        self.assertEqual(err, "")
        self.assertIn("Proton 11.0", proton)

    def test_no_proton_reports_cleanly(self):
        _p, _c, _r, err = main._proton_binary_for(self.APP_ID)
        self.assertIn("No Proton", err)


class TestSeedGameIni(unittest.TestCase):
    """FO3's launcher hangs under Proton before creating FALLOUT.INI -
    seed it from the game's own Fallout_default.ini instead."""

    GAME = "Seed Game"
    APP_ID = 999003

    def setUp(self):
        self.plugin = main.Plugin()
        self.root = os.path.join(main.STEAM_COMMON, self.GAME)
        os.makedirs(self.root, exist_ok=True)
        self.dst = main._game_prefs_path(self.APP_ID, "SeedGame/GAME.INI")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.dst), ignore_errors=True)

    def test_seeds_when_missing(self):
        with open(os.path.join(self.root, "Default.ini"), "w") as f:
            f.write("[General]\nsLanguage=ENGLISH\n")
        result = run(
            self.plugin.seed_game_ini(
                self.GAME, self.APP_ID, "Default.ini", "SeedGame/GAME.INI"
            )
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["seeded"])
        self.assertIn("sLanguage", open(self.dst).read())

    def test_existing_ini_untouched(self):
        os.makedirs(os.path.dirname(self.dst), exist_ok=True)
        with open(self.dst, "w") as f:
            f.write("user content")
        with open(os.path.join(self.root, "Default.ini"), "w") as f:
            f.write("defaults")
        result = run(
            self.plugin.seed_game_ini(
                self.GAME, self.APP_ID, "Default.ini", "SeedGame/GAME.INI"
            )
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["seeded"])
        self.assertEqual(open(self.dst).read(), "user content")

    def test_missing_source_errors(self):
        result = run(
            self.plugin.seed_game_ini(
                self.GAME, self.APP_ID, "Nope.ini", "SeedGame/GAME.INI"
            )
        )
        self.assertFalse(result["ok"])


class TestPayloadShapes(unittest.TestCase):
    """Archive layouts seen in the wild during the TTW run (2026-08-05):
    Data/Video movie replacers and MO2 exports (meta.ini payload root)."""

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="payload-", dir=TEST_ROOT)

    def tearDown(self):
        shutil.rmtree(self.scratch, ignore_errors=True)

    def _mk(self, *rel_paths):
        for rel in rel_paths:
            path = os.path.join(self.scratch, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("x")

    def test_video_dir_is_a_data_marker(self):
        # 'Animated Main Menu Replacer for TTW': archive root = Video/*.bik
        self._mk("Video/NVRMainMenu.bik")
        self.assertEqual(main._find_data_payload(self.scratch), self.scratch)

    def test_keywords_dir_is_a_data_marker(self):
        # 'Long 15 - NCR Expansion': keywords/*.ini (kNVSE convention)
        self._mk("keywords/keywords_long-15.ini")
        self.assertEqual(main._find_data_payload(self.scratch), self.scratch)

    def test_mo2_export_wrapper_meta_ini(self):
        # 'Tweaks for TTW - Helmet Overlays Patch': wrapper/meta.ini +
        # a config folder that maps onto Data/<folder>/.
        self._mk(
            "Tweaks Patch/meta.ini",
            "Tweaks Patch/Helmet Overlay/TTW Tweaks - Power Helmets.txt",
        )
        payload = main._find_data_payload(self.scratch)
        self.assertEqual(
            payload, os.path.join(self.scratch, "Tweaks Patch")
        )
        # The MO2 metadata never lands in the game's Data dir.
        self.assertFalse(
            os.path.isfile(os.path.join(self.scratch, "Tweaks Patch", "meta.ini"))
        )

    def test_mo2_meta_ini_at_archive_root(self):
        self._mk("meta.ini", "Some Config/values.txt")
        self.assertEqual(main._find_data_payload(self.scratch), self.scratch)

    def test_plain_wrapper_still_resolves(self):
        # Regression guard: the existing wrapper->markers path unchanged.
        self._mk("WrapperFolder/textures/thing.dds")
        self.assertEqual(
            main._find_data_payload(self.scratch),
            os.path.join(self.scratch, "WrapperFolder"),
        )


class TestUserPrefs(unittest.TestCase):
    """Settings-tab values clamp server-side: a hand-edited settings.json
    can't produce a 50-way download stampede or a zero disk floor."""

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.plugin = main.Plugin()

    def test_defaults(self):
        prefs = main._user_prefs()
        self.assertEqual(prefs["parallel_downloads"], 4)
        self.assertEqual(prefs["prefetch_window"], 8)
        self.assertEqual(prefs["speed_cap_mbps"], 0)
        self.assertEqual(prefs["min_free_gb"], 5)

    def test_set_clamps_to_bounds(self):
        result = run(
            self.plugin.set_user_prefs(
                {"parallel_downloads": 99, "speed_cap_mbps": -5, "min_free_gb": 2}
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["prefs"]["parallel_downloads"], 8)
        self.assertEqual(result["prefs"]["speed_cap_mbps"], 0)
        self.assertEqual(result["prefs"]["min_free_gb"], 2)

    def test_junk_values_ignored(self):
        run(self.plugin.set_user_prefs({"prefetch_window": "junk", "bogus": 1}))
        prefs = main._user_prefs()
        self.assertEqual(prefs["prefetch_window"], 8)
        self.assertNotIn("bogus", prefs)

    def test_hand_edited_settings_clamped_on_read(self):
        settings = main._load_settings()
        settings["user_prefs"] = {"parallel_downloads": 500, "min_free_gb": 0}
        main._save_settings(settings)
        prefs = main._user_prefs()
        self.assertEqual(prefs["parallel_downloads"], 8)
        self.assertEqual(prefs["min_free_gb"], 1)


class TestPakPatchChain(unittest.TestCase):
    """RE Engine (RE4 remake) loads re_chunk_000.pak.patch_XXX.pak
    sequentially from the game root - a gap breaks everything past it.
    Installs allocate the next number; uninstalls renumber survivors."""

    GAME = "PakPatch Game"
    DOMAIN = "residentevil42023"

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.root = os.path.join(main.STEAM_COMMON, self.GAME)
        os.makedirs(self.root, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _touch(self, name):
        with open(os.path.join(self.root, name), "wb") as f:
            f.write(b"pak")

    def _record(self, key, files):
        settings = main._load_settings()
        installed = settings.setdefault("installed", {}).setdefault(
            self.DOMAIN, {}
        )
        installed[key] = {
            "mod_id": 1,
            "file_id": 1,
            "name": key,
            "mode": "files",
            "target": ".",
            "files": files,
            "pakpatch": True,
        }
        main._save_settings(settings)

    def test_name_format(self):
        self.assertEqual(main._pakpatch_name(7), "re_chunk_000.pak.patch_007.pak")
        self.assertTrue(main.RE4_PAK_RE.match("re_chunk_000.pak.patch_004.pak"))
        self.assertFalse(main.RE4_PAK_RE.match("re_chunk_000.pak"))

    def test_payload_discovery_paks_natives_reframework(self):
        scratch = os.path.join(self.root, "scratch")
        for rel in (
            "OptionA/moda.pak",
            "OptionB/modb.pak",
            "wrapper/natives/STM/tex.tex.724",
            "wrapper2/reframework/autorun/health_bars.lua",
        ):
            path = os.path.join(scratch, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"x")
        paks, natives, refs = main._pakpatch_payload(scratch)
        self.assertEqual([os.path.basename(p) for p in paks], ["moda.pak", "modb.pak"])
        self.assertEqual(len(natives), 1)
        self.assertTrue(natives[0].endswith("natives"))
        self.assertEqual(len(refs), 1)
        self.assertTrue(refs[0].endswith("reframework"))

    def test_ensure_config_key(self):
        cfg = os.path.join(self.root, "re4_fw_config.txt")
        main._ensure_config_key(cfg, "LooseFileLoader_Enabled", "true")
        self.assertIn("LooseFileLoader_Enabled=true", open(cfg).read())
        # Existing other keys survive; the target key is replaced not duped.
        with open(cfg, "w") as f:
            f.write("FontSize=16\nLooseFileLoader_Enabled=false\n")
        main._ensure_config_key(cfg, "LooseFileLoader_Enabled", "true")
        content = open(cfg).read()
        self.assertIn("FontSize=16", content)
        self.assertEqual(content.count("LooseFileLoader_Enabled"), 1)
        self.assertIn("LooseFileLoader_Enabled=true", content)

    def test_renumber_closes_gap_and_keeps_officials(self):
        # officials 000-001 (no record owns them), mods at 002/003/004
        for n in range(5):
            self._touch(main._pakpatch_name(n))
        self._record("ModA", [main._pakpatch_name(2)])
        self._record("ModB", [main._pakpatch_name(3)])
        self._record("ModC", [main._pakpatch_name(4)])
        # uninstall ModB's pak (the middle of the chain)
        os.remove(os.path.join(self.root, main._pakpatch_name(3)))
        settings = main._load_settings()
        settings["installed"][self.DOMAIN].pop("ModB")
        main._pakpatch_renumber(self.DOMAIN, self.root, settings)
        main._save_settings(settings)

        names = sorted(
            n for n in os.listdir(self.root) if main.RE4_PAK_RE.match(n)
        )
        self.assertEqual(
            names, [main._pakpatch_name(n) for n in range(4)]
        )  # 000,001 officials + 002 (A), 003 (C shifted down)
        recs = main._load_settings()["installed"][self.DOMAIN]
        self.assertEqual(recs["ModA"]["files"], [main._pakpatch_name(2)])
        self.assertEqual(recs["ModC"]["files"], [main._pakpatch_name(3)])

    def test_renumber_noop_when_chain_intact(self):
        self._touch(main._pakpatch_name(0))
        self._record("ModA", [main._pakpatch_name(0)])
        settings = main._load_settings()
        self.assertEqual(
            main._pakpatch_renumber(self.DOMAIN, self.root, settings), 0
        )

    def test_uninstall_mod_renumbers(self):
        for n in range(3):
            self._touch(main._pakpatch_name(n))
        # 000 official; A owns 001, B owns 002
        self._record("ModA", [main._pakpatch_name(1)])
        self._record("ModB", [main._pakpatch_name(2)])
        result = run(
            main.Plugin().uninstall_mod(
                self.DOMAIN, self.GAME, "._nexus_mods_unused", "ModA"
            )
        )
        self.assertTrue(result["ok"])
        names = sorted(
            n for n in os.listdir(self.root) if main.RE4_PAK_RE.match(n)
        )
        self.assertEqual(names, [main._pakpatch_name(0), main._pakpatch_name(1)])
        recs = main._load_settings()["installed"][self.DOMAIN]
        self.assertNotIn("ModA", recs)
        self.assertEqual(recs["ModB"]["files"], [main._pakpatch_name(1)])


class TestPluginMasters(unittest.TestCase):
    """The engine hard-fails at boot when an enabled plugin's master is
    absent ('X.esm is missing required files') - seen live with a TTW
    collection on a DLC-less FNV install. The checker parses TES4 MAST
    subrecords; disable_plugins drops the plugins.txt lines."""

    APP_ID = 999002
    GAME = "MasterTest Game"

    @staticmethod
    def _fake_plugin(masters):
        subs = b""
        for m in masters:
            name = m.encode("cp1252") + b"\x00"
            subs += b"MAST" + len(name).to_bytes(2, "little") + name
            subs += b"DATA" + (8).to_bytes(2, "little") + b"\x00" * 8
        head = b"TES4" + len(subs).to_bytes(4, "little") + b"\x00" * 16
        return head + subs

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.plugin = main.Plugin()
        self.data = os.path.join(main.STEAM_COMMON, self.GAME, "Data")
        os.makedirs(self.data, exist_ok=True)
        self.plugins_txt = main._plugins_txt_path(self.APP_ID, "FalloutNV/Plugins.txt")
        os.makedirs(os.path.dirname(self.plugins_txt), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(os.path.join(main.STEAM_COMMON, self.GAME), ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.plugins_txt), ignore_errors=True)

    def _seed(self, name, masters):
        with open(os.path.join(self.data, name), "wb") as f:
            f.write(self._fake_plugin(masters))

    def test_masters_parse_and_missing_detected(self):
        self._seed("FalloutNV.esm", [])
        self._seed("Good.esp", ["FalloutNV.esm"])
        self._seed("Broken.esm", ["FalloutNV.esm", "DeadMoney.esm"])
        main._write_plugins_txt(
            self.plugins_txt, ["FalloutNV.esm", "Good.esp", "Broken.esm"]
        )
        result = run(
            self.plugin.check_plugin_masters(
                self.GAME, "Data", self.APP_ID, "FalloutNV/Plugins.txt", "listed"
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["broken"]), 1)
        self.assertEqual(result["broken"][0]["plugin"], "Broken.esm")
        self.assertEqual(result["broken"][0]["missing"], ["DeadMoney.esm"])

    def test_master_case_is_insensitive(self):
        self._seed("falloutnv.esm", [])
        self._seed("Mod.esp", ["FalloutNV.ESM"])
        main._write_plugins_txt(self.plugins_txt, ["Mod.esp"])
        result = run(
            self.plugin.check_plugin_masters(
                self.GAME, "Data", self.APP_ID, "FalloutNV/Plugins.txt", "listed"
            )
        )
        self.assertEqual(result["broken"], [])

    def test_starred_style_only_checks_enabled(self):
        self._seed("Broken.esp", ["Ghost.esm"])
        main._write_plugins_txt(self.plugins_txt, ["Broken.esp"])  # unstarred
        result = run(
            self.plugin.check_plugin_masters(
                self.GAME, "Data", self.APP_ID, "FalloutNV/Plugins.txt", "starred"
            )
        )
        self.assertEqual(result["broken"], [])

    def test_disable_plugins_drops_lines(self):
        main._write_plugins_txt(
            self.plugins_txt, ["FalloutNV.esm", "Broken.esm", "*Starred.esp"]
        )
        result = run(
            self.plugin.disable_plugins(
                self.APP_ID,
                "FalloutNV/Plugins.txt",
                "listed",
                ["Broken.esm", "Starred.esp"],
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["disabled"], 2)
        self.assertEqual(
            main._read_plugins_txt(self.plugins_txt), ["FalloutNV.esm"]
        )

    def test_archive_cache_path_is_id_based_with_sane_ext(self):
        p = main._archive_cache_path(83614, 1000118408, "Animated Menu.rar")
        self.assertTrue(p.endswith("83614-1000118408.rar"))
        # Garbage extensions never make it into the local path.
        p = main._archive_cache_path(1, 2, "weird.file.name.<>?")
        self.assertTrue(p.endswith("1-2"))

    def test_download_archive_short_circuits_on_cache(self):
        # The aiohttp stub raises on ANY session use - this passing PROVES
        # a prefetched archive skips the network entirely.
        os.makedirs(main.DOWNLOADS_DIR, exist_ok=True)
        path = main._archive_cache_path(77, 88, "cached.zip")
        with open(path, "wb") as f:
            f.write(b"archive-bytes")
        try:
            err, got = run(
                main._download_archive("skyrim", 77, 88, "cached.zip", "key")
            )
        finally:
            os.remove(path)
        self.assertEqual(err, "")
        self.assertEqual(got, path)

    def test_safe_uri_encodes_spaces_and_keeps_query(self):
        # Live case: 'Animated Main Menu Replacer for TTW' - the CDN link
        # carries the raw file name; aiohttp rejects the spaces.
        raw = (
            "https://cf-files.nexusmods.com/cdn/130/83614/"
            "Animated Main Menu Replacer for TTW-83614-1-1698648778.rar"
            "?expires=1785895952&md5=qViuJ9BSdYc2I5fvf-Hzvg&user_id=1"
        )
        fixed = main._safe_uri(raw)
        self.assertIn("Animated%20Main%20Menu%20Replacer%20for%20TTW", fixed)
        self.assertIn("?expires=1785895952&md5=qViuJ9BSdYc2I5fvf-Hzvg", fixed)
        # Idempotent: already-encoded URLs pass through unchanged.
        self.assertEqual(main._safe_uri(fixed), fixed)

    def test_non_plugin_files_are_ignored(self):
        with open(os.path.join(self.data, "readme.txt"), "w") as f:
            f.write("not a plugin")
        main._write_plugins_txt(self.plugins_txt, ["readme.txt"])
        result = run(
            self.plugin.check_plugin_masters(
                self.GAME, "Data", self.APP_ID, "FalloutNV/Plugins.txt", "listed"
            )
        )
        self.assertEqual(result["broken"], [])


class TestGateToSovngardeFailures(unittest.TestCase):
    """The 15 mods that failed in a 1,954-mod Gate To Sovngarde install
    (device, 2026-08-07). Every archive shape here is one the log named."""

    def setUp(self):
        self.scratch = os.path.join(TEST_ROOT, "gts-shapes")
        shutil.rmtree(self.scratch, ignore_errors=True)
        os.makedirs(self.scratch)

    def tearDown(self):
        shutil.rmtree(self.scratch, ignore_errors=True)

    def _mk(self, *rels):
        for rel in rels:
            path = os.path.join(self.scratch, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("x")

    # -- framework addons that ship one Data subfolder --------------------

    def test_nemesis_engine_folder_is_a_payload(self):
        # 'USSEP Nemesis or Pandora Patch', '1st Person Vertical Aim Fix',
        # '(SBF) State Behavior Framework' - all refused on this shape.
        self._mk("Nemesis_Engine/mod/x.txt")
        self.assertEqual(main._find_data_payload(self.scratch), self.scratch)

    def test_comap_mapmarkers_folder_is_a_payload(self):
        # 'VIGILANT - CoMAP Addon', 'DAc0da - CoMAP Addon'
        self._mk("MapMarkers/Vigilant.json", "MapMarkers/Resources/a.dds")
        self.assertEqual(main._find_data_payload(self.scratch), self.scratch)

    def test_light_placer_folder_is_a_payload(self):
        # 'Holidays -x- Light Placer'
        self._mk("LightPlacer/megnoeu/a.json")
        self.assertEqual(main._find_data_payload(self.scratch), self.scratch)

    def test_seasons_folder_is_a_payload(self):
        # 'Thickets and Dead Shrub Swapper with Options'
        self._mk("Seasons/swap.ini")
        self.assertEqual(main._find_data_payload(self.scratch), self.scratch)

    # -- config-only mods --------------------------------------------------

    def test_loose_kid_ini_at_root_is_a_payload(self):
        # 'GuardsTalk': a single _KID.ini beside macOS zip metadata.
        self._mk("GuardsTalk_KID.ini", "__MACOSX/._GuardsTalk_KID.ini")
        main._strip_archive_junk(self.scratch)
        self.assertEqual(main._find_data_payload(self.scratch), self.scratch)
        # The macOS tree must never reach the game's Data dir.
        self.assertFalse(
            os.path.exists(os.path.join(self.scratch, "__MACOSX"))
        )

    def test_wrapped_config_only_mod_resolves_to_the_wrapper(self):
        # 'Very Important Cannibal Bug Fix': one wrapper folder holding
        # nothing but an _ANIO.ini.
        self._mk("Important Immersion Fix/VeryImportantCannibalFix_ANIO.ini")
        self.assertEqual(
            main._find_data_payload(self.scratch),
            os.path.join(self.scratch, "Important Immersion Fix"),
        )

    def test_folder_plus_loose_ini_is_a_payload(self):
        # 'Simple Fishing Overhaul - FLM Addon'
        self._mk(
            "SimpleFishingOverhaul (FLM)/a.txt",
            "SimpleFishingOverhaul_FLM.ini",
        )
        self.assertEqual(main._find_data_payload(self.scratch), self.scratch)

    def test_a_pc_tool_with_a_config_is_still_refused(self):
        # The guard on the rule above: an .exe means a desktop tool, and
        # those must keep being refused rather than dumped into Data/.
        self._mk("xEdit.exe", "xEdit.ini")
        self.assertIsNone(main._find_data_payload(self.scratch))

    def test_a_readme_only_archive_is_still_refused(self):
        self._mk("readme.txt", "changelog.txt")
        self.assertIsNone(main._find_data_payload(self.scratch))

    # -- a file where a directory belongs ---------------------------------

    def test_a_file_blocking_a_directory_is_cleared(self):
        # The crash: an earlier mod left a FILE at
        # Data/Textures/terrain/blackreach, so every later mod wanting
        # that directory died with FileExistsError - makedirs(exist_ok)
        # raises when the path exists and is not a directory. It killed
        # two installs outright, one of them a FOMOD.
        base = os.path.join(self.scratch, "Data", "Textures", "terrain")
        os.makedirs(base)
        with open(os.path.join(base, "blackreach"), "w") as f:
            f.write("debris")
        dst = os.path.join(base, "blackreach", "lod.dds")
        main._makedirs_for(dst)
        self.assertTrue(os.path.isdir(os.path.join(base, "blackreach")))
        with open(dst, "w") as f:
            f.write("x")
        self.assertTrue(os.path.isfile(dst))

    def test_makedirs_for_is_a_no_op_when_the_tree_is_fine(self):
        dst = os.path.join(self.scratch, "a", "b", "c.txt")
        main._makedirs_for(dst)
        main._makedirs_for(dst)  # idempotent
        self.assertTrue(os.path.isdir(os.path.dirname(dst)))


class TestScriptExtenderPlugins(unittest.TestCase):
    """A mod built for an older game will never load, and SKSE stops the
    whole game with a modal asking whether to continue. Parking the DLL
    (renamed, never deleted) is what lets the other 1,900 mods run."""

    GAME = "SE Plugin Test"
    APP_ID = 489830
    LOG = "Skyrim Special Edition/SKSE/skse64.log"

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        self.plugins = os.path.join(self.install, "Data", "SKSE", "Plugins")
        os.makedirs(self.plugins)
        for n in ("BehaviorDataInjector.dll", "Working.dll", "Broken.dll"):
            with open(os.path.join(self.plugins, n), "w") as f:
                f.write("x")
        self.log = main._game_prefs_path(self.APP_ID, self.LOG)
        os.makedirs(os.path.dirname(self.log), exist_ok=True)
        with open(self.log, "w") as f:
            f.write(
                "SKSE64 runtime: initialize (version = 2.2.6)\n"
                "checking plugin BehaviorDataInjector.dll\n"
                "plugin BehaviorDataInjector.dll (00000001 BDI 00010030) "
                "disabled, only compatible with versions earlier than "
                "1.6.629 0 (handle 0)\n"
                "checking plugin Working.dll\n"
                "plugin Working.dll (00000001 W 00010000) loaded correctly "
                "(handle 5)\n"
                "checking plugin Broken.dll\n"
                "plugin Broken.dll (00000001 B 00010000) disabled, fatal "
                "error occurred while loading plugin 0 (handle 7)\n"
            )
        self.plugin = main.Plugin()

    def tearDown(self):
        shutil.rmtree(self.install, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.log), ignore_errors=True)

    def _state(self):
        return run(
            self.plugin.get_script_extender_state(
                self.APP_ID, self.GAME, self.LOG
            )
        )

    def test_the_log_names_the_failures_and_why(self):
        s = self._state()
        self.assertTrue(s["available"])
        names = {f["name"]: f for f in s["failed"]}
        self.assertCountEqual(
            names, ["BehaviorDataInjector.dll", "Broken.dll"]
        )
        # The distinction the user needs: one is the author's problem,
        # the other might be fixable here.
        self.assertTrue(names["BehaviorDataInjector.dll"]["outdated"])
        self.assertFalse(names["Broken.dll"]["outdated"])
        self.assertIn("1.6.629", names["BehaviorDataInjector.dll"]["reason"])

    def test_parking_renames_rather_than_deletes(self):
        s = self._state()
        r = run(
            self.plugin.set_script_extender_plugins(
                self.GAME, s["plugins_dir"], ["BehaviorDataInjector.dll"], False
            )
        )
        self.assertTrue(r["ok"])
        self.assertEqual(r["changed"], 1)
        self.assertFalse(
            os.path.isfile(os.path.join(self.plugins, "BehaviorDataInjector.dll"))
        )
        # Still on disk, just not something SKSE will scan.
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    self.plugins,
                    "BehaviorDataInjector.dll" + main.SE_DISABLED_SUFFIX,
                )
            )
        )

    def test_a_parked_plugin_stops_being_reported_as_failing(self):
        s = self._state()
        run(
            self.plugin.set_script_extender_plugins(
                self.GAME, s["plugins_dir"], ["BehaviorDataInjector.dll"], False
            )
        )
        after = self._state()
        self.assertEqual(
            [f["name"] for f in after["failed"]], ["Broken.dll"]
        )
        self.assertEqual(after["parked"], ["BehaviorDataInjector.dll"])

    def test_restoring_puts_it_back(self):
        s = self._state()
        run(
            self.plugin.set_script_extender_plugins(
                self.GAME, s["plugins_dir"], ["BehaviorDataInjector.dll"], False
            )
        )
        r = run(
            self.plugin.set_script_extender_plugins(
                self.GAME, s["plugins_dir"], ["BehaviorDataInjector.dll"], True
            )
        )
        self.assertEqual(r["changed"], 1)
        self.assertTrue(
            os.path.isfile(os.path.join(self.plugins, "BehaviorDataInjector.dll"))
        )

    def test_it_refuses_a_folder_outside_the_game(self):
        r = run(
            self.plugin.set_script_extender_plugins(
                self.GAME, "/tmp", ["anything.dll"], False
            )
        )
        self.assertFalse(r["ok"])

    def test_no_log_yet_is_not_an_error(self):
        os.remove(self.log)
        s = self._state()
        self.assertTrue(s["ok"])
        self.assertFalse(s["available"])


def _make_plugin(path, masters=(), flags=0):
    """Smallest valid TES4 header: 24-byte record header then MAST subs."""
    data = b""
    for m in masters:
        raw = m.encode("cp1252") + b"\x00"
        data += b"MAST" + len(raw).to_bytes(2, "little") + raw
    head = (b"TES4" + len(data).to_bytes(4, "little")
            + flags.to_bytes(4, "little") + b"\x00" * 12)
    with open(path, "wb") as f:
        f.write(head + data)


class TestCollectionExtras(unittest.TestCase):
    """Every mod we install is source type "nexus". Two other types exist
    and were dropped without a word - which is how New Vegas's most popular
    collection installed "successfully" while missing Vanilla UI+, the base
    layer its whole HUD is built on."""

    def _manifest(self, mods):
        return {"mods": mods}

    def test_finds_a_browse_mod_with_everything_needed_to_fetch_it(self):
        extras = main._collection_extras(self._manifest([
            {
                "name": "Vanilla UI+ (VUI+) v9.48",
                "optional": False,
                "source": {
                    "type": "browse",
                    "url": "https://www.moddb.com/mods/vanilla-ui-plus",
                    "instructions": "Click the red Download Now button.",
                    "fileSize": 760435,
                    "md5": "984d63c6b39fdfe7990136fbfe502bdd",
                },
            },
        ]))
        self.assertEqual(len(extras["browse"]), 1)
        b = extras["browse"][0]
        self.assertIn("moddb.com", b["url"])
        # The curator's own words: without them the user is told to go
        # somewhere and not what to do when they get there.
        self.assertIn("Download Now", b["instructions"])
        self.assertEqual(b["size"], 760435)
        self.assertFalse(b["optional"])

    def test_finds_a_bundled_mod_and_where_it_lives(self):
        extras = main._collection_extras(self._manifest([
            {
                "name": "OneTweak But Really Updated",
                "optional": False,
                "source": {
                    "type": "bundle",
                    "fileExpression": "Bundled - OneTweak (v2.1.0.4)",
                    "fileSize": 180224,
                },
            },
        ]))
        self.assertEqual(len(extras["bundle"]), 1)
        self.assertEqual(
            extras["bundle"][0]["folder"], "Bundled - OneTweak (v2.1.0.4)"
        )

    def test_nexus_mods_are_not_extras(self):
        extras = main._collection_extras(self._manifest([
            {"name": "Ordinary", "source": {"type": "nexus", "modId": 1}},
        ]))
        self.assertEqual(extras["browse"], [])
        self.assertEqual(extras["bundle"], [])

    def test_an_empty_manifest_is_not_an_error(self):
        self.assertEqual(
            main._collection_extras({}), {"browse": [], "bundle": []}
        )

    def test_a_mod_with_no_source_is_ignored(self):
        extras = main._collection_extras(self._manifest([{"name": "Broken"}]))
        self.assertEqual(extras["browse"], [])
        self.assertEqual(extras["bundle"], [])

    def test_optional_is_carried_through(self):
        # A required manual download stops the collection working; an
        # optional one does not, and saying so is the difference between a
        # warning worth reading and one worth ignoring.
        extras = main._collection_extras(self._manifest([
            {"name": "Nice to have", "optional": True,
             "source": {"type": "browse", "url": "https://x"}},
        ]))
        self.assertTrue(extras["browse"][0]["optional"])


class TestBaselineBuildGuard(unittest.TestCase):
    """A baseline describes ONE build of the game.

    Games gain files afterwards - patches, and DLC for titles still being
    updated - and from the mods folder every one of those is
    indistinguishable from a mod leftover. Deleting a game file needs a
    Steam verify to undo; leaving a mod's config file behind is untidy.
    Those are not comparable, so when the build has moved the sweep stops
    and reports instead.

    The DLC case found this on device. A game update is the commoner one
    and would have been silent, because no name-based guard can know what
    a future patch will add."""

    def setUp(self):
        self.apps = os.path.dirname(main.STEAM_COMMON)
        os.makedirs(self.apps, exist_ok=True)
        self.manifest = os.path.join(self.apps, "appmanifest_999001.acf")

    def tearDown(self):
        try:
            os.remove(self.manifest)
        except OSError:
            pass

    def _write(self, build):
        lines = ['"AppState"', "{", '	"buildid"		"%s"' % build, "}"]
        with open(self.manifest, "w", encoding="utf-8") as f:
            f.write(chr(10).join(lines) + chr(10))

    def test_reads_the_installed_build(self):
        self._write("1510068")
        self.assertEqual(main._steam_build_id(999001), "1510068")

    def test_a_missing_manifest_is_not_an_error(self):
        self.assertEqual(main._steam_build_id(999002), "")

    def test_no_app_id_reads_nothing(self):
        self.assertEqual(main._steam_build_id(0), "")

    def test_the_reset_consults_the_build(self):
        # Guards the wiring: the predicate is no use uncalled.
        import inspect
        src = inspect.getsource(main.Plugin.reset_game_modding)
        self.assertIn("_steam_build_id", src)
        self.assertIn("game_changed", src)


class TestExternalPrerequisites(unittest.TestCase):
    """A mod that needs a file Nexus does not host is switched OFF, not
    installed and silently breaking the game.

    Device: three New Vegas interface mods need Vanilla UI+, hosted on
    ModDB. Left on, the game reaches the main-menu background and stops -
    no crash log, no error anyone can act on. The workaround was a
    terminal, then a manual toggle, and the audience is people holding a
    controller. So it is automatic and reversible instead."""

    GAME = "Prereq Test"

    def setUp(self):
        self.plugin = main.Plugin()
        root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(root, ignore_errors=True)
        self.data = os.path.join(root, "Data")
        os.makedirs(os.path.join(self.data, "menus"))
        for rel in ("menus/hud.xml", "menus/other.xml"):
            with open(os.path.join(self.data, *rel.split("/")), "w") as f:
                f.write(rel)
        settings = main._load_settings()
        settings.setdefault("installed", {})["newvegas_prereq"] = {
            "One HUD - oHUD": {
                "mode": "dataDir", "plugins": [], "name": "One HUD - oHUD",
                "files": ["menus/hud.xml", "menus/other.xml"],
            },
        }
        main._save_settings(settings)
        # Point the table at our fake domain for the duration.
        main.NEEDS_EXTERNAL_MOD["newvegas_prereq"] = {
            "one hud - ohud": {
                "needs_file": "Vanilla UI Plus.esp",
                "needs_name": "Vanilla UI+ (VUI+)",
            },
        }
        main._force_rmtree(
            main._parked_files_dir("newvegas_prereq", "One HUD - oHUD")
        )

    def tearDown(self):
        settings = main._load_settings()
        settings.get("installed", {}).pop("newvegas_prereq", None)
        settings.get("collection_attention", {}).pop("newvegas_prereq", None)
        main._save_settings(settings)
        main.NEEDS_EXTERNAL_MOD.pop("newvegas_prereq", None)
        main._force_rmtree(
            main._parked_files_dir("newvegas_prereq", "One HUD - oHUD")
        )

    def _apply(self):
        return run(self.plugin.apply_known_prerequisites(
            "newvegas_prereq", self.GAME, "Data", 0, "", "listed", "abc123"))

    def _present(self, rel):
        return os.path.isfile(os.path.join(self.data, *rel.split("/")))

    def test_parks_a_mod_whose_prerequisite_is_absent(self):
        r = self._apply()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["parked"], 2)
        self.assertEqual(r["mods"], ["One HUD - oHUD"])
        self.assertEqual(r["needs"], ["Vanilla UI+ (VUI+)"])
        self.assertFalse(self._present("menus/hud.xml"))

    def test_it_says_why_on_the_collection(self):
        self._apply()
        queue = (main._load_settings().get("collection_attention", {})
                 .get("newvegas_prereq", {}).get("abc123") or [])
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["reason"], "needs_external")
        self.assertIn("Vanilla UI+", queue[0]["detail"])
        self.assertIn("My Mods", queue[0]["detail"])

    def test_running_twice_parks_nothing_extra(self):
        self.assertEqual(self._apply()["parked"], 2)
        self.assertEqual(self._apply()["parked"], 0)

    def test_restores_the_mod_once_the_prerequisite_arrives(self):
        self._apply()
        self.assertFalse(self._present("menus/hud.xml"))
        # The user fetched VUI+ by hand and installed it.
        with open(os.path.join(self.data, "Vanilla UI Plus.esp"), "w") as f:
            f.write("esp")
        r = self._apply()
        self.assertEqual(r["restored"], 2)
        self.assertEqual(r["mods"], [])
        self.assertTrue(self._present("menus/hud.xml"))

    def test_a_game_with_no_table_is_left_alone(self):
        r = run(self.plugin.apply_known_prerequisites(
            "stardewvalley", self.GAME, "Data"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["parked"], 0)

    def test_rejects_a_bad_domain(self):
        r = run(self.plugin.apply_known_prerequisites("../evil", self.GAME))
        self.assertFalse(r["ok"])


class TestDataDirToggleParksFiles(unittest.TestCase):
    """Unticking a plugin does not switch a dataDir mod off.

    Its textures, meshes and interface XML sit in Data and keep loading,
    and a mod made only of those - a UI overhaul, a texture pack - could
    not be turned off AT ALL: the toggle refused with "its assets are
    always active". On device three interface mods had to be uninstalled
    to get New Vegas to start, losing the downloads, when all that was
    needed was for their files to stop being read."""

    GAME = "Park Test"
    APP_ID = 22380
    SUBPATH = "FalloutNV/Plugins.txt"

    def setUp(self):
        self.plugin = main.Plugin()
        root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(root, ignore_errors=True)
        self.data = os.path.join(root, "Data")
        os.makedirs(os.path.join(self.data, "menus", "main"))
        for rel in ("menus/main/hud.xml", "menus/main/other.xml"):
            with open(os.path.join(self.data, *rel.split("/")), "w") as f:
                f.write(rel)
        settings = main._load_settings()
        settings.setdefault("installed", {})["parktest"] = {
            "UI Mod": {
                "mode": "dataDir", "plugins": [],
                "files": ["menus/main/hud.xml", "menus/main/other.xml"],
            },
        }
        main._save_settings(settings)
        main._force_rmtree(main._parked_files_dir("parktest", "UI Mod"))

    def tearDown(self):
        settings = main._load_settings()
        settings.get("installed", {}).pop("parktest", None)
        main._save_settings(settings)
        main._force_rmtree(main._parked_files_dir("parktest", "UI Mod"))

    def _toggle(self, on):
        return run(self.plugin.set_mod_enabled(
            self.GAME, "Data", "UI Mod", on, "dataDir", "parktest",
            self.APP_ID, self.SUBPATH, "listed"))

    def _on_disk(self, rel):
        return os.path.isfile(os.path.join(self.data, *rel.split("/")))

    def test_a_mod_with_no_plugins_can_now_be_turned_off(self):
        r = self._toggle(False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["moved"], 2)
        self.assertFalse(self._on_disk("menus/main/hud.xml"))

    def test_turning_it_back_on_restores_every_file(self):
        self._toggle(False)
        r = self._toggle(True)
        self.assertEqual(r["moved"], 2)
        self.assertTrue(self._on_disk("menus/main/hud.xml"))
        self.assertTrue(self._on_disk("menus/main/other.xml"))

    def test_the_content_is_the_same_after_a_round_trip(self):
        self._toggle(False)
        self._toggle(True)
        with open(os.path.join(self.data, "menus", "main", "hud.xml")) as f:
            self.assertEqual(f.read(), "menus/main/hud.xml")

    def test_a_file_another_mod_also_provides_is_left_alone(self):
        """Whoever wrote last owns the file on disk, and it is not
        necessarily the mod being switched off - moving it would gut the
        other one."""
        settings = main._load_settings()
        settings["installed"]["parktest"]["Other Mod"] = {
            "mode": "dataDir", "plugins": [],
            "files": ["menus/main/other.xml"],
        }
        main._save_settings(settings)
        r = self._toggle(False)
        self.assertEqual(r["moved"], 1)
        self.assertEqual(r["shared"], 1)
        self.assertTrue(self._on_disk("menus/main/other.xml"))
        self.assertFalse(self._on_disk("menus/main/hud.xml"))

    def test_empty_directories_are_pruned(self):
        self._toggle(False)
        self.assertFalse(
            os.path.isdir(os.path.join(self.data, "menus", "main"))
        )


class TestBaselineRetakenOnReset(unittest.TestCase):
    """_record_vanilla_baseline writes once, on the theory that the first
    install is preceded by a clean folder. On device it was not: New
    Vegas's baseline held 30-odd mod files - TTWLods.esp, Titans of The
    New West, mil.esp, uio - because the game had been modded before this
    plugin ever saw it. A baseline containing mod files PROTECTS them from
    the sweep, the exact opposite of its purpose.

    After a reset the folder is as close to vanilla as it will ever be,
    and includes any DLC or patch content gained since. That is the moment
    to re-take it."""

    def test_reset_retakes_the_baseline(self):
        import inspect
        src = inspect.getsource(main.Plugin.reset_game_modding)
        self.assertIn("re-took the vanilla baseline", src)
        # And stamps the build, or the update guard is blind again.
        after = src[src.index("re-took the vanilla baseline") - 800:]
        self.assertIn("_steam_build_id", src)
        self.assertIn("baseline_build", src)
        self.assertTrue(after)

    def test_it_does_not_retake_after_a_failed_reset(self):
        # A reset that hit errors may have left mods behind; recording
        # those as "vanilla" would make them permanent.
        import inspect
        src = inspect.getsource(main.Plugin.reset_game_modding)
        self.assertIn("and not errors", src)


class TestGameOwnedContent(unittest.TestCase):
    """Reset must never delete content the user bought.

    Device, 2026-08-12: New Vegas's vanilla baseline was captured, the user
    then bought the Ultimate Edition DLC in a sale, and the next reset swept
    all nine DLC masters and their archives - because the baseline predated
    them and the sweep treats anything newer as arriving with modding. The
    game then refused to start, asking for the files we had just taken."""

    def test_dlc_masters_are_game_owned(self):
        for esm in main.VANILLA_MASTERS_BY_DOMAIN["newvegas"]:
            self.assertTrue(
                main._game_owned_name("newvegas", esm), esm
            )

    def test_dlc_archives_are_game_owned(self):
        # Named after their master: "DeadMoney - Main.bsa".
        for name in ("DeadMoney - Main.bsa", "ClassicPack - Main.bsa",
                     "CaravanPack - Main.bsa", "HonestHearts - Main.bsa"):
            self.assertTrue(main._game_owned_name("newvegas", name), name)

    def test_fallout3_dlc_too(self):
        for esm in main.VANILLA_MASTERS_BY_DOMAIN["fallout3"]:
            self.assertTrue(main._game_owned_name("fallout3", esm), esm)
        self.assertTrue(
            main._game_owned_name("fallout3", "ThePitt - Main.bsa")
        )

    def test_skyrim_creation_club_is_game_owned(self):
        # Bought or claimed from the Creation Club - not ours to delete.
        for name in ("ccbgssse001-fish.esm", "ccQDRSSE001-SurvivalMode.esl",
                     "ccbgssse025-advdsgs.bsa"):
            self.assertTrue(
                main._game_owned_name("skyrimspecialedition", name), name
            )

    def test_a_mod_that_merely_starts_similarly_is_not_protected(self):
        # "DeadMoneyAnnoyanceReducer.esp" is a mod, not Dead Money.
        self.assertFalse(
            main._game_owned_name("newvegas", "DeadMoneyAnnoyanceReducer.esp")
        )

    def test_ordinary_mod_files_are_not_protected(self):
        for name in ("SomeMod.esp", "textures", "MyMod - Main.bsa",
                     "nvse_config.ini"):
            self.assertFalse(main._game_owned_name("newvegas", name), name)

    def test_the_sweep_skips_game_owned_files(self):
        """Guards the wiring, not just the predicate - the sweep has to
        actually consult it."""
        import inspect
        src = inspect.getsource(main.Plugin.reset_game_modding)
        self.assertIn("_game_owned_name", src)


class TestBundleCaseMerge(unittest.TestCase):
    """A bundled mod's paths must adopt the casing already on disk.

    Device, 2026-08-12: the NVAO bundle ships NVSE/Plugins/Scripts with a
    capital P while the install had Data/NVSE/plugins. Copying verbatim
    created both. Wine resolves an exact-case match before scanning, so the
    script extender's request for Plugins found the new EMPTY directory and
    loaded none of its 56 plugins - the game died after the intro logos
    with nothing in any log to explain it."""

    def test_a_bundle_adopts_existing_directory_casing(self):
        data = os.path.join(TEST_ROOT, "bundlecase")
        shutil.rmtree(data, ignore_errors=True)
        os.makedirs(os.path.join(data, "NVSE", "plugins"))
        with open(os.path.join(data, "NVSE", "plugins", "real.dll"), "w") as f:
            f.write("existing")
        cache = {}
        merged = main._case_merge_rel(
            data, "NVSE/Plugins/Scripts/gr_Dynamite.txt", cache
        )
        self.assertTrue(
            merged.startswith("NVSE/plugins/"),
            f"expected the existing lowercase dir, got {merged!r}",
        )

    def test_the_installer_case_merges_every_bundled_file(self):
        """Guards the fix itself: the loop must call _case_merge_rel, or a
        bundle silently splits a directory in two again."""
        import inspect
        src = inspect.getsource(main.Plugin.install_collection_bundles)
        self.assertIn("_case_merge_rel", src)


class TestResolveFileConflicts(unittest.TestCase):
    """Per-PATH resolution. v0.97.0 reinstalled whole mods in collection
    order, which rewrites files they were not contesting and leapfrogs
    everything left alone - 47 wrong pairs became 92. Here each contested
    file is written exactly once by its rightful owner and nothing else on
    disk is touched, so no new conflict can be created."""

    GAME = "Resolve Test"

    def setUp(self):
        self.plugin = main.Plugin()
        root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(root, ignore_errors=True)
        self.data = os.path.join(root, "Data")
        os.makedirs(self.data)
        settings = main._load_settings()
        settings["api_key"] = "test-key"
        settings.setdefault("installed", {})["resolvetest"] = {
            "wanted": {
                "mod_id": 10, "file_id": 100, "file_name": "wanted.zip",
                "mode": "dataDir", "installed_at": 100, "install_seq": 1,
                "files": ["menus/main/hud.xml", "textures/keep.dds"],
            },
            "grabbed": {
                "mod_id": 20, "file_id": 200, "file_name": "grabbed.zip",
                "mode": "dataDir", "installed_at": 100, "install_seq": 2,
                "files": ["menus/main/hud.xml"],
            },
        }
        settings.pop("file_owner", None)
        main._save_settings(settings)
        # The loser is on disk, as it would be after a real install.
        main._makedirs_for(os.path.join(self.data, "menus", "main", "hud.xml"))
        with open(os.path.join(self.data, "menus", "main", "hud.xml"), "w") as f:
            f.write("GRABBED")
        # Stand in for the download: the archive the rightful owner ships.
        self.archive = os.path.join(TEST_ROOT, "wanted.zip")
        with zipfile.ZipFile(self.archive, "w") as zf:
            zf.writestr("Menus/main/hud.xml", "WANTED")
            zf.writestr("textures/keep.dds", "SHOULD NOT BE COPIED")

    def tearDown(self):
        settings = main._load_settings()
        settings.get("installed", {}).pop("resolvetest", None)
        settings.pop("file_owner", None)
        main._save_settings(settings)

    def _run(self, files=None):
        async def fake_download(domain, mod_id, file_id, file_name, key):
            return "", self.archive
        real = main._download_archive
        main._download_archive = fake_download
        try:
            return run(self.plugin.resolve_file_conflicts(
                "resolvetest", self.GAME, "Data", [20, 10], files))
        finally:
            main._download_archive = real

    def _hud(self):
        with open(os.path.join(self.data, "menus", "main", "hud.xml")) as f:
            return f.read()

    def test_rewrites_the_contested_file_from_the_rightful_owner(self):
        # Collection order is [20, 10], so mod 10 ("wanted") is last and
        # should own the file - even though "grabbed" installed later.
        self.assertEqual(self._hud(), "GRABBED")
        r = self._run()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["rewritten"], 1)
        self.assertEqual(self._hud(), "WANTED")

    def test_touches_nothing_it_was_not_asked_about(self):
        """The whole point: an uncontested file the owner also ships must
        NOT be written, or resolving one conflict creates others."""
        self._run()
        self.assertFalse(
            os.path.exists(os.path.join(self.data, "textures", "keep.dds"))
        )

    def test_records_the_settled_owner(self):
        self._run()
        owners = main._file_owner_overrides("resolvetest")
        self.assertEqual(owners.get("menus/main/hud.xml"), "wanted")

    def test_a_second_run_has_nothing_left_to_do(self):
        self.assertEqual(self._run()["rewritten"], 1)
        self.assertEqual(self._run()["rewritten"], 0)

    def test_can_be_narrowed_to_named_files(self):
        # So somebody can fix the interface without re-fetching a 2GB
        # texture pack.
        r = self._run(files=["something/else.xml"])
        self.assertEqual(r["rewritten"], 0)
        self.assertEqual(self._hud(), "GRABBED")

    def test_finds_the_file_however_the_author_nested_it(self):
        with zipfile.ZipFile(self.archive, "w") as zf:
            zf.writestr("Wrapper Folder/Menus/main/hud.xml", "WANTED")
        self.assertEqual(self._run()["rewritten"], 1)
        self.assertEqual(self._hud(), "WANTED")

    def test_a_file_missing_from_the_archive_is_reported_not_silent(self):
        with zipfile.ZipFile(self.archive, "w") as zf:
            zf.writestr("readme.txt", "nothing useful")
        r = self._run()
        self.assertEqual(r["rewritten"], 0)
        self.assertTrue(r["errors"])
        self.assertEqual(self._hud(), "GRABBED")

    def test_refuses_without_a_collection_order(self):
        r = run(self.plugin.resolve_file_conflicts(
            "resolvetest", self.GAME, "Data", []))
        self.assertFalse(r["ok"])

    def test_rejects_a_bad_domain(self):
        r = run(self.plugin.resolve_file_conflicts(
            "../evil", self.GAME, "Data", [1]))
        self.assertFalse(r["ok"])


class TestImplicitMastersFO3FNV(unittest.TestCase):
    """FO3 and FNV load the base game and its DLC without being told, so
    those must never be written into Plugins.txt. Skyrim was guarded in
    v0.71.0 after a test caught the same fault; these two shipped without
    it, and on device the load-order check called all ten of New Vegas's
    implicit masters "installed but switched off" and offered to enable
    them - which renumbers every plugin after them, and the load index is
    what a save file records."""

    def test_new_vegas_covers_the_base_game_and_every_dlc(self):
        implicit = main.IMPLICIT_MASTERS_BY_DOMAIN["newvegas"]
        for esm in main.VANILLA_MASTERS_BY_DOMAIN["newvegas"]:
            self.assertIn(esm.lower(), implicit, esm)

    def test_fallout3_covers_the_base_game_and_every_dlc(self):
        implicit = main.IMPLICIT_MASTERS_BY_DOMAIN["fallout3"]
        for esm in main.VANILLA_MASTERS_BY_DOMAIN["fallout3"]:
            self.assertIn(esm.lower(), implicit, esm)

    def test_they_are_lowercased_like_every_other_domain(self):
        # The lookups compare against name.lower(); a capital here would
        # silently disable the guard.
        for domain in ("newvegas", "fallout3"):
            for name in main.IMPLICIT_MASTERS_BY_DOMAIN[domain]:
                self.assertEqual(name, name.lower())

    def test_an_implicit_master_is_never_offered_for_enabling(self):
        data = os.path.join(TEST_ROOT, "implicitfnv")
        shutil.rmtree(data, ignore_errors=True)
        os.makedirs(data)
        _make_plugin(os.path.join(data, "DeadMoney.esm"),
                     flags=main.PLUGIN_FLAG_MASTER)
        _make_plugin(os.path.join(data, "mod.esp"), masters=["DeadMoney.esm"])
        entries = [("mod.esp", True)]
        implicit = main.IMPLICIT_MASTERS_BY_DOMAIN["newvegas"]
        self.assertEqual(
            main._masters_to_enable(data, entries, implicit), []
        )
        # Without the guard it would be reported, which is the bug.
        self.assertEqual(
            len(main._masters_to_enable(data, entries, frozenset())), 1
        )


class TestGhostPlugins(unittest.TestCase):
    """Enabled but not installed. Harmless on Skyrim/FO4 where an entry is
    a line in a list; NOT harmless on FO3/FNV where presence in Plugins.txt
    IS activation. Found by hand on device when uninstalling oHUD left
    oHUD.esm listed - the delist only happens when the record carries a
    plugins list, and that one had lost its."""

    GAME = "Ghost Test"
    APP_ID = 22380
    SUBPATH = "AppData/Local/FalloutNV/Plugins.txt"

    def setUp(self):
        self.plugin = main.Plugin()
        self.data = os.path.join(main.STEAM_COMMON, self.GAME, "Data")
        shutil.rmtree(os.path.join(main.STEAM_COMMON, self.GAME),
                      ignore_errors=True)
        os.makedirs(self.data)
        self.txt = main._plugins_txt_path(self.APP_ID, self.SUBPATH)
        main._makedirs_for(self.txt)

    def _write(self, names):
        with open(self.txt, "w", encoding="utf-8") as f:
            for n in names:
                f.write(n + chr(10))

    def _listed(self):
        return [n for n, on in main._plugin_entries(
            main._read_plugins_txt(self.txt), "listed") if on]

    def _call(self):
        return run(self.plugin.remove_ghost_plugins(
            self.APP_ID, self.GAME, self.SUBPATH, "listed", "newvegas"))

    def test_finds_a_plugin_that_is_not_on_disk(self):
        _make_plugin(os.path.join(self.data, "real.esp"))
        self.assertEqual(
            main._ghost_plugins(self.data, ["real.esp", "gone.esm"]),
            ["gone.esm"],
        )

    def test_case_differences_are_not_ghosts(self):
        _make_plugin(os.path.join(self.data, "Real.esp"))
        self.assertEqual(main._ghost_plugins(self.data, ["real.esp"]), [])

    def test_delisting_leaves_the_real_plugins_alone(self):
        _make_plugin(os.path.join(self.data, "real.esp"))
        self._write(["real.esp", "oHUD.esm"])
        r = self._call()
        self.assertEqual(r["removed"], 1)
        self.assertEqual(r["names"], ["oHUD.esm"])
        self.assertEqual(self._listed(), ["real.esp"])

    def test_nothing_to_do_is_not_an_error(self):
        _make_plugin(os.path.join(self.data, "real.esp"))
        self._write(["real.esp"])
        r = self._call()
        self.assertTrue(r["ok"])
        self.assertEqual(r["removed"], 0)

    def test_a_missing_data_dir_invents_nothing(self):
        # Otherwise every plugin looks like a ghost and the "safe" repair
        # would delist the entire load order.
        self.assertEqual(
            main._ghost_plugins(os.path.join(TEST_ROOT, "nope"), ["a.esp"]), []
        )

    def test_rejects_a_bad_domain(self):
        result = run(self.plugin.remove_ghost_plugins(
            self.APP_ID, self.GAME, self.SUBPATH, "listed", "../evil"))
        self.assertFalse(result["ok"])


class TestFileConflicts(unittest.TestCase):
    """Overwriting is how collections work - the device install has 10,362
    shared paths across 867 mod-sets, nearly all of them deliberate. What
    matters is the 1,440 files where the mod that actually landed last was
    NOT the one the collection wanted to win, which is invisible in-game
    and cost a working HUD on New Vegas."""

    def _rec(self, mod_id, files, at, mode="dataDir"):
        return {"mod_id": mod_id, "files": files, "installed_at": at,
                "mode": mode}

    def test_deliberate_overrides_are_not_reported(self):
        # Later in the collection AND installed later: exactly right.
        records = {
            "under": self._rec(1, ["textures/a.dds"], 100),
            "over": self._rec(2, ["textures/a.dds"], 200),
        }
        self.assertEqual(_wrong(records, {1: 0, 2: 1}), [])

    def test_a_tie_is_reported_as_nothing_rather_than_guessed(self):
        """installed_at has one-second resolution and 627 of the device's
        764 records share a second with another. Resolving those by dict
        order is what made the first version of this report fiction."""
        records = {
            "a": self._rec(1, ["textures/a.dds"], 100),
            "b": self._rec(2, ["textures/a.dds"], 100),
        }
        self.assertEqual(_wrong(records, {2: 0, 1: 1}), [])

    def test_the_sequence_breaks_a_same_second_tie(self):
        records = {
            "a": dict(self._rec(1, ["textures/a.dds"], 100), install_seq=9),
            "b": dict(self._rec(2, ["textures/a.dds"], 100), install_seq=4),
        }
        # 'a' wrote last despite the identical second; the collection wants
        # 'b' (position 1) to win, so this IS a conflict.
        found = _wrong(records, {1: 0, 2: 1})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["actual"], "a")
        self.assertEqual(found[0]["intended"], "b")

    def test_the_sequence_increases_and_survives_a_restart(self):
        first = main._merge_install_record(None, {"mod_id": 1})["install_seq"]
        second = main._merge_install_record(None, {"mod_id": 2})["install_seq"]
        self.assertGreater(second, first)
        # Reseeding reads the highest sequence already on disk, so a
        # restart cannot hand out a number it has already used.
        main._INSTALL_SEQ = None
        third = main._merge_install_record(None, {"mod_id": 3})["install_seq"]
        self.assertGreater(third, 0)

    def test_reports_a_mod_that_won_out_of_turn(self):
        # The FOMOD case: parked during the run, installed by Finish setup
        # afterwards, so it beat what the collection put above it.
        records = {
            "hub": self._rec(1, ["config/a.ini", "config/b.ini"], 100),
            "fomod": self._rec(2, ["config/a.ini", "config/b.ini"], 999),
        }
        found = _wrong(records, {2: 0, 1: 1})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["actual"], "fomod")
        self.assertEqual(found[0]["intended"], "hub")
        self.assertEqual(found[0]["files"], 2)

    def test_orders_by_how_many_files_are_wrong(self):
        records = {
            "a": self._rec(1, ["1", "2", "3"], 100),
            "b": self._rec(2, ["1", "2", "3"], 999),
            "c": self._rec(3, ["9"], 100),
            "d": self._rec(4, ["9"], 999),
        }
        found = _wrong(records, {2: 0, 1: 1, 4: 2, 3: 3})
        self.assertEqual([g["files"] for g in found], [3, 1])

    def test_folder_mode_mods_never_conflict(self):
        # Each owns its own directory, so two listing manifest.json is not
        # a clash - counting it would invent conflicts on every game that
        # installs per-mod folders.
        records = {
            "a": self._rec(1, ["manifest.json"], 100, mode="folder"),
            "b": self._rec(2, ["manifest.json"], 999, mode="folder"),
        }
        self.assertEqual(_wrong(records, {2: 0, 1: 1}), [])

    def test_a_mod_outside_the_collection_is_left_alone(self):
        # Installed by hand: there is no curator intent to violate, and
        # calling it wrong would nag about a deliberate personal choice.
        records = {
            "collection": self._rec(1, ["textures/a.dds"], 100),
            "byhand": self._rec(2, ["textures/a.dds"], 999),
        }
        self.assertEqual(_wrong(records, {1: 0}), [])

    def test_case_differences_are_the_same_file(self):
        records = {
            "a": self._rec(1, ["Textures/A.dds"], 100),
            "b": self._rec(2, ["textures/a.dds"], 999),
        }
        self.assertEqual(len(_wrong(records, {2: 0, 1: 1})), 1)

    def test_one_owner_is_never_a_conflict(self):
        records = {"solo": self._rec(1, ["textures/a.dds"], 100)}
        self.assertEqual(_wrong(records, {1: 0}), [])

    def test_a_settled_owner_stops_being_reported(self):
        """After the fix rewrites a file from its rightful owner, the loser
        still has the higher install_seq - deliberately, because the fix
        does not reinstall it. Without honouring the settled owner the same
        file reports as wrong forever."""
        records = {
            "wanted": dict(self._rec(1, ["textures/a.dds"], 100), install_seq=1),
            "grabbed": dict(self._rec(2, ["textures/a.dds"], 100), install_seq=2),
        }
        order = {2: 0, 1: 1}
        self.assertEqual(len(_wrong(records, order)), 1)
        self.assertEqual(
            _wrong(records, order, {"textures/a.dds": "wanted"}), []
        )

    def test_a_settled_owner_that_is_still_wrong_is_reported(self):
        # Recording an override must not silence a genuine mismatch.
        records = {
            "wanted": dict(self._rec(1, ["textures/a.dds"], 100), install_seq=1),
            "grabbed": dict(self._rec(2, ["textures/a.dds"], 100), install_seq=2),
        }
        found = _wrong(records, {2: 0, 1: 1}, {"textures/a.dds": "grabbed"})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["intended"], "wanted")

    def test_resolve_list_is_in_collection_order(self):
        records = {
            "late": self._rec(7, ["a"], 999),
            "early": self._rec(3, ["a"], 100),
        }
        plugin = main.Plugin()
        settings = main._load_settings()
        settings.setdefault("installed", {})["conflicttest"] = records
        main._save_settings(settings)
        try:
            r = run(plugin.get_file_conflicts("conflicttest", [7, 3]))
            self.assertTrue(r["ok"])
            # Reporting is off until it reads modRules. List order is not
            # the curator's priority - the device's collection carries
            # 1,442 explicit rules, and by them the HUD stack was already
            # right while list order called it wrong.
            self.assertEqual(r["files"], 0)
            self.assertEqual(r["resolve"], [])
            self.assertFalse(main.CONFLICTS_USE_MOD_RULES)
        finally:
            settings = main._load_settings()
            settings.get("installed", {}).pop("conflicttest", None)
            main._save_settings(settings)

    def test_no_order_means_no_opinion(self):
        plugin = main.Plugin()
        r = run(plugin.get_file_conflicts("newvegas", []))
        self.assertTrue(r["ok"])
        self.assertEqual(r["conflicts"], [])

    def test_rejects_a_bad_domain(self):
        plugin = main.Plugin()
        self.assertFalse(run(plugin.get_file_conflicts("../evil", [1]))["ok"])


def _wrong(records, order, overrides=None):
    return main._wrong_winners(records, order, overrides)


class TestDisableBlockedPlugins(unittest.TestCase):
    """Switching off mods whose master is not installed - but never the
    ones a DLC purchase would fix. On device that distinction was 115 mods
    against 4: a button treating them the same would have binned most of a
    collection one tap after telling the user what was wrong."""

    GAME = "Blocked Test"
    APP_ID = 22380
    SUBPATH = "AppData/Local/FalloutNV/Plugins.txt"

    def setUp(self):
        self.plugin = main.Plugin()
        self.data = os.path.join(
            main.STEAM_COMMON, self.GAME, "Data"
        )
        shutil.rmtree(os.path.join(main.STEAM_COMMON, self.GAME),
                      ignore_errors=True)
        os.makedirs(self.data)
        self.txt = main._plugins_txt_path(self.APP_ID, self.SUBPATH)
        main._makedirs_for(self.txt)
        settings = main._load_settings()
        settings.pop("skipped", None)
        main._save_settings(settings)

    def _plugin(self, name, masters=()):
        _make_plugin(os.path.join(self.data, name), masters=masters)
        return name

    def _write(self, names):
        with open(self.txt, "w", encoding="utf-8") as f:
            for n in names:
                f.write(n + chr(10))

    def _call(self):
        return run(self.plugin.disable_blocked_plugins(
            self.APP_ID, self.GAME, self.SUBPATH, "listed", "newvegas"
        ))

    def _listed(self):
        return [n for n, on in main._plugin_entries(
            main._read_plugins_txt(self.txt), "listed") if on]

    def test_switches_off_a_mod_whose_mod_master_is_absent(self):
        self._plugin("TTWLods.esp", masters=["TaleOfTwoWastelands.esm"])
        self._plugin("fine.esp")
        self._write(["TTWLods.esp", "fine.esp"])
        r = self._call()
        self.assertTrue(r["ok"])
        self.assertEqual(r["names"], ["TTWLods.esp"])
        self.assertEqual(self._listed(), ["fine.esp"])

    def test_leaves_mods_blocked_only_by_DLC_alone(self):
        # The user can buy Dead Money. Turning the mods off instead would
        # be throwing away what they came for.
        self._plugin("mil.esp", masters=["DeadMoney.esm"])
        self._write(["mil.esp"])
        r = self._call()
        self.assertEqual(r["disabled"], 0)
        self.assertEqual(self._listed(), ["mil.esp"])

    def test_a_mod_blocked_by_both_is_still_switched_off(self):
        # It cannot load even after the DLC purchase, so it is the second
        # master that decides.
        self._plugin(
            "both.esp", masters=["DeadMoney.esm", "TaleOfTwoWastelands.esm"]
        )
        self._write(["both.esp"])
        self.assertEqual(self._call()["disabled"], 1)

    def test_records_the_reason_so_a_repair_does_not_undo_it(self):
        self._plugin("TTWLods.esp", masters=["TaleOfTwoWastelands.esm"])
        self._write(["TTWLods.esp"])
        self._call()
        skips = main._load_skips("newvegas")
        self.assertIn("ttwlods.esp", skips)
        self.assertIn("TaleOfTwoWastelands.esm", skips["ttwlods.esp"]["reason"])
        self.assertFalse(skips["ttwlods.esp"]["root"])

    def test_nothing_to_do_is_not_an_error(self):
        self._plugin("fine.esp")
        self._write(["fine.esp"])
        r = self._call()
        self.assertTrue(r["ok"])
        self.assertEqual(r["disabled"], 0)

    def test_rejects_a_bad_domain(self):
        result = run(self.plugin.disable_blocked_plugins(
            self.APP_ID, self.GAME, self.SUBPATH, "listed", "../evil"
        ))
        self.assertFalse(result["ok"])


class TestMissingMasters(unittest.TestCase):
    """The third load-order fault: a master that is not on disk at all.

    Device, New Vegas 2026-08-12 - the game put up a modal naming one
    plugin (mil.esp) and quit. In fact 115 of 245 enabled plugins could
    not load, for want of five DLC the account did not own. The game
    never says that, and neither did we."""

    def setUp(self):
        self.data = os.path.join(TEST_ROOT, "missingmasters")
        shutil.rmtree(self.data, ignore_errors=True)
        os.makedirs(self.data)

    def _plugin(self, name, masters=()):
        _make_plugin(os.path.join(self.data, name), masters=masters)
        return name

    def test_reports_a_master_that_is_not_installed(self):
        self._plugin("mil.esp", masters=["DeadMoney.esm"])
        found = main._missing_masters(self.data, ["mil.esp"])
        self.assertEqual(found, [("DeadMoney.esm", ["mil.esp"])])

    def test_orders_by_how_many_mods_each_one_blocks(self):
        for n in ("a.esp", "b.esp", "c.esp"):
            self._plugin(n, masters=["HonestHearts.esm"])
        self._plugin("d.esp", masters=["CaravanPack.esm"])
        found = main._missing_masters(
            self.data, ["a.esp", "b.esp", "c.esp", "d.esp"]
        )
        self.assertEqual([m for m, _ in found],
                         ["HonestHearts.esm", "CaravanPack.esm"])

    def test_a_master_present_on_disk_is_not_missing(self):
        self._plugin("DeadMoney.esm")
        self._plugin("mil.esp", masters=["DeadMoney.esm"])
        self.assertEqual(main._missing_masters(self.data, ["mil.esp"]), [])

    def test_a_present_master_counts_even_when_switched_off(self):
        # Installed-but-disabled is _masters_to_enable's job and has a
        # working repair. Reporting it here too would tell the user to buy
        # DLC they already own.
        self._plugin("DeadMoney.esm")
        self.assertEqual(main._missing_masters(self.data, ["DeadMoney.esm"]), [])

    def test_implicit_masters_are_never_missing(self):
        # Skyrim.esm is not in plugins.txt and often not enumerated, but
        # the engine always loads it.
        self._plugin("mod.esp", masters=["Skyrim.esm"])
        self.assertEqual(
            main._missing_masters(self.data, ["mod.esp"], implicit={"skyrim.esm"}),
            [],
        )

    def test_case_differences_do_not_invent_a_missing_master(self):
        self._plugin("deadmoney.esm")
        self._plugin("mil.esp", masters=["DeadMoney.esm"])
        self.assertEqual(main._missing_masters(self.data, ["mil.esp"]), [])

    def test_a_plugin_not_on_disk_contributes_nothing(self):
        self.assertEqual(main._missing_masters(self.data, ["ghost.esp"]), [])

    def test_every_new_vegas_dlc_has_a_human_name(self):
        # The whole point of the row: "DeadMoney.esm" is not an action.
        for esm in main.VANILLA_MASTERS_BY_DOMAIN["newvegas"][1:]:
            self.assertIn(esm.lower(), main.DLC_MASTER_NAMES, esm)

    def test_every_fallout3_dlc_has_a_human_name(self):
        for esm in main.VANILLA_MASTERS_BY_DOMAIN["fallout3"][1:]:
            self.assertIn(esm.lower(), main.DLC_MASTER_NAMES, esm)


class TestLegacyFomodPackage(unittest.TestCase):
    """Before FOMOD became a folder convention, FOMM shipped installers as
    a single `.fomod` file - an ordinary archive under another extension.
    Much of the older New Vegas, FO3 and Oblivion catalogue is still
    packaged that way, and layout detection called every one of them
    unsupported. Found on device via Interior Lighting Overhaul
    (newvegas/35794) during the FNV regression pass."""

    def setUp(self):
        self.scratch = os.path.join(TEST_ROOT, "fomodpkg")
        shutil.rmtree(self.scratch, ignore_errors=True)
        os.makedirs(self.scratch)

    def _package(self, into, name="Mod Installer.fomod", config=True):
        """Write a .fomod (a zip by another name) into `into`."""
        os.makedirs(into, exist_ok=True)
        path = os.path.join(into, name)
        with zipfile.ZipFile(path, "w") as zf:
            if config:
                zf.writestr("fomod/ModuleConfig.xml", "<config/>")
            zf.writestr("core/Mod.esp", "plugin bytes")
        return path

    def test_unwraps_the_package_so_the_wizard_is_found(self):
        wrapper = os.path.join(self.scratch, "Interior_Lighting_Overhaul-35794")
        os.makedirs(wrapper)
        with open(os.path.join(wrapper, "ChangeLog.txt"), "w") as f:
            f.write("notes")
        self._package(wrapper)

        self.assertIsNone(main._fomod_config_path(self.scratch))
        unwrapped = run(main._unwrap_fomod_package(self.scratch))
        self.assertTrue(unwrapped)
        # The wizard is now discoverable, and its base resolves to the
        # folder the package sat in - which is what _parse_fomod uses.
        cfg = main._fomod_config_path(self.scratch)
        self.assertIsNotNone(cfg)
        self.assertEqual(os.path.dirname(os.path.dirname(cfg)), wrapper)
        self.assertTrue(os.path.isfile(os.path.join(wrapper, "core", "Mod.esp")))

    def test_the_package_file_is_removed_once_unwrapped(self):
        # Left behind it is 15MB of dead weight copied into the mod folder.
        wrapper = os.path.join(self.scratch, "Mod")
        self._package(wrapper)
        run(main._unwrap_fomod_package(self.scratch))
        self.assertEqual(
            [n for n in os.listdir(wrapper) if n.endswith(".fomod")], []
        )

    def test_leaves_an_archive_that_already_has_a_wizard_alone(self):
        # A normal FOMOD archive shipping a .fomod beside real content is
        # not second-guessed.
        os.makedirs(os.path.join(self.scratch, "fomod"))
        with open(
            os.path.join(self.scratch, "fomod", "ModuleConfig.xml"), "w"
        ) as f:
            f.write("<config/>")
        self._package(self.scratch, name="Bundled.fomod")
        self.assertEqual(run(main._unwrap_fomod_package(self.scratch)), "")
        self.assertTrue(
            os.path.isfile(os.path.join(self.scratch, "Bundled.fomod"))
        )

    def test_leaves_ambiguous_archives_alone(self):
        # Two packages: picking one would be a guess.
        self._package(self.scratch, name="A.fomod")
        self._package(self.scratch, name="B.fomod")
        self.assertEqual(run(main._unwrap_fomod_package(self.scratch)), "")

    def test_a_package_that_will_not_extract_is_left_in_place(self):
        wrapper = os.path.join(self.scratch, "Mod")
        os.makedirs(wrapper)
        broken = os.path.join(wrapper, "Broken.fomod")
        with open(broken, "wb") as f:
            f.write(b"not an archive at all")
        self.assertEqual(run(main._unwrap_fomod_package(self.scratch)), "")
        self.assertTrue(os.path.isfile(broken))

    def test_does_nothing_when_there_is_no_package(self):
        os.makedirs(os.path.join(self.scratch, "Data"))
        self.assertEqual(run(main._unwrap_fomod_package(self.scratch)), "")


class TestSlotUsage(unittest.TestCase):
    """Plugin slots are addressed by one byte, and crossing the limit does
    not announce itself - the game stops loading plugins past it or dies on
    the way in, and nothing says which of two thousand mods was the straw.

    The engine-dependent half matters most: bit 0x200 is the ESL flag on
    Skyrim SE and FO4 only. FO3 and New Vegas predate ESL, so honouring it
    there would count a plugin as costing nothing when it really occupies a
    slot - and the warning would never fire on a genuine overflow."""

    def setUp(self):
        self.data = os.path.join(TEST_ROOT, "slots")
        shutil.rmtree(self.data, ignore_errors=True)
        os.makedirs(self.data)

    def _plugin(self, name, flags=0):
        _make_plugin(os.path.join(self.data, name), flags=flags)
        return name

    def test_counts_full_and_light_separately(self):
        names = [
            self._plugin("a.esp"),
            self._plugin("b.esp"),
            self._plugin("light.esp", flags=main.PLUGIN_FLAG_LIGHT),
        ]
        self.assertEqual(main._slot_usage(self.data, names), (2, 1))

    def test_no_esl_engine_counts_every_plugin_as_a_full_slot(self):
        # The whole point: an FNV plugin carrying 0x200 for some other
        # reason must still be seen to occupy a slot.
        names = [
            self._plugin("a.esp"),
            self._plugin("looks_light.esp", flags=main.PLUGIN_FLAG_LIGHT),
        ]
        self.assertEqual(main._slot_usage(self.data, names, esl=False), (2, 0))

    def test_implicit_masters_still_occupy_slots(self):
        # Skyrim.esm and the DLC are never written to plugins.txt, but the
        # engine still gives them indices - leaving them out would report
        # five free slots that do not exist.
        self._plugin("skyrim.esm", flags=main.PLUGIN_FLAG_MASTER)
        names = [self._plugin("mod.esp")]
        self.assertEqual(
            main._slot_usage(self.data, names, implicit={"skyrim.esm"}), (2, 0)
        )

    def test_plugins_listed_but_not_on_disk_cost_nothing(self):
        names = [self._plugin("real.esp"), "ghost.esp"]
        self.assertEqual(main._slot_usage(self.data, names), (1, 0))

    def test_case_differences_do_not_double_count(self):
        self._plugin("Mod.esp")
        self.assertEqual(main._slot_usage(self.data, ["mod.esp", "MOD.ESP"]), (1, 0))

    def test_missing_data_directory_reports_nothing(self):
        self.assertEqual(main._slot_usage(os.path.join(TEST_ROOT, "nope"), ["a.esp"]),
                         (0, 0))

    def test_only_esl_games_have_a_light_tier(self):
        # The two ceilings are the same number - New Vegas reported
        # "maximum plugin limit of 254" on device, so the extra slot I had
        # reasoned it should get does not exist. What genuinely differs is
        # the light tier, which is what the panel must not mention on a
        # game that has none.
        self.assertEqual(main.NO_ESL_SLOT_LIMIT, main.FULL_SLOT_LIMIT)
        self.assertNotIn("fallout3", main.ESL_DOMAINS)
        self.assertNotIn("newvegas", main.ESL_DOMAINS)
        self.assertIn("skyrimspecialedition", main.ESL_DOMAINS)
        self.assertIn("fallout4", main.ESL_DOMAINS)


class TestLoadOrder(unittest.TestCase):
    """Skyrim/FO4 read plugins.txt AS the load order, and we only ever
    appended to it - so it was install order. On the device's Gate To
    Sovngarde install that left 557 of 1,960 enabled plugins ahead of a
    master they depend on: a crash on the way into the world."""

    GAME = "Load Order Test"
    APP_ID = 489830
    SUB = "Skyrim Special Edition/Plugins.txt"

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        self.data = os.path.join(self.install, "Data")
        os.makedirs(self.data)
        _make_plugin(os.path.join(self.data, "Base.esm"), flags=1)
        _make_plugin(os.path.join(self.data, "Town.esp"), ["Base.esm"])
        # The shape that breaks: a patch installed before what it patches.
        _make_plugin(os.path.join(self.data, "TownPatch.esp"),
                     ["Base.esm", "Town.esp"])
        _make_plugin(os.path.join(self.data, "Late.esm"), ["Base.esm"], flags=1)
        self.path = main._plugins_txt_path(self.APP_ID, self.SUB)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.plugin = main.Plugin()

    def tearDown(self):
        shutil.rmtree(self.install, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.path), ignore_errors=True)

    def _write(self, names):
        main._write_plugins_txt(
            self.path,
            ["# This file is used by Skyrim"] + ["*" + n for n in names],
        )

    def _state(self, domain=""):
        return run(self.plugin.get_load_order_state(
            self.APP_ID, self.GAME, self.SUB, "starred", domain))

    def _fix(self, domain=""):
        return run(self.plugin.fix_load_order(
            self.APP_ID, self.GAME, self.SUB, "starred", domain))

    def _order(self):
        return [n for n, _ in
                main._plugin_entries(main._read_plugins_txt(self.path))]

    def test_it_counts_plugins_listed_before_their_masters(self):
        self._write(["TownPatch.esp", "Town.esp", "Base.esm"])
        s = self._state()
        # TownPatch needs Base + Town (both later), Town needs Base.
        self.assertEqual(s["violations"], 3)
        self.assertEqual(s["total"], 3)

    def test_sorting_puts_every_plugin_after_its_masters(self):
        self._write(["TownPatch.esp", "Town.esp", "Base.esm"])
        r = self._fix()
        self.assertTrue(r["ok"])
        self.assertEqual(r["violations_after"], 0)
        self.assertEqual(self._order(), ["Base.esm", "Town.esp", "TownPatch.esp"])

    def test_masters_lead_because_the_engine_loads_them_first(self):
        # Late.esm is last in the file but master-flagged, so the game
        # loads it before any esp regardless. The file should say so.
        self._write(["Town.esp", "TownPatch.esp", "Base.esm", "Late.esm"])
        self._fix()
        order = self._order()
        self.assertEqual(order[:2], ["Base.esm", "Late.esm"])

    def test_an_esp_with_the_master_flag_sorts_as_a_master(self):
        # "ESM-flagged esp" is a normal, deliberate thing in Skyrim, and
        # the extension alone would file it under regular plugins.
        _make_plugin(os.path.join(self.data, "Flagged.esp"), flags=1)
        self._write(["Town.esp", "Base.esm", "Flagged.esp"])
        self._fix()
        self.assertIn("Flagged.esp", self._order()[:2])

    def test_disabled_plugins_are_kept(self):
        # Off and not needed by anything: it stays listed and stays off.
        # (Town.esp would be switched ON here, correctly - TownPatch
        # masters it - so an unrelated plugin is what tests this.)
        _make_plugin(os.path.join(self.data, "Spare.esp"))
        main._write_plugins_txt(self.path, [
            "# header", "*TownPatch.esp", "*Town.esp", "Spare.esp", "*Base.esm",
        ])
        self._fix()
        entries = main._plugin_entries(main._read_plugins_txt(self.path))
        self.assertEqual(
            {n: on for n, on in entries},
            {"Base.esm": True, "Town.esp": True, "TownPatch.esp": True,
             "Spare.esp": False},
        )

    def test_the_previous_order_is_kept_so_it_can_be_undone(self):
        self._write(["TownPatch.esp", "Town.esp", "Base.esm"])
        self._fix()
        backup = self.path + main.LOAD_ORDER_BACKUP
        self.assertTrue(os.path.isfile(backup))
        self.assertEqual(
            [n for n, _ in main._plugin_entries(main._read_plugins_txt(backup))],
            ["TownPatch.esp", "Town.esp", "Base.esm"],
        )

    def test_an_order_that_is_already_right_is_left_alone(self):
        self._write(["Base.esm", "Town.esp", "TownPatch.esp"])
        self.assertEqual(self._state()["violations"], 0)
        self._fix()
        self.assertEqual(self._order(), ["Base.esm", "Town.esp", "TownPatch.esp"])

    def test_a_master_cycle_falls_back_instead_of_mangling_the_file(self):
        # Two plugins mastering each other cannot be ordered. Refusing to
        # touch it beats emitting a confident wrong answer.
        _make_plugin(os.path.join(self.data, "A.esp"), ["B.esp"])
        _make_plugin(os.path.join(self.data, "B.esp"), ["A.esp"])
        self._write(["A.esp", "B.esp"])
        self._fix()
        self.assertEqual(set(self._order()), {"A.esp", "B.esp"})

    def test_a_missing_master_does_not_stop_the_sort(self):
        # 284 masters on the device are not installed at all. Those
        # plugins cannot load, but they must not break ordering for the
        # ones that can.
        _make_plugin(os.path.join(self.data, "Orphan.esp"), ["Nothing.esm"])
        self._write(["TownPatch.esp", "Orphan.esp", "Town.esp", "Base.esm"])
        r = self._fix()
        self.assertTrue(r["ok"])
        self.assertEqual(r["violations_after"], 0)
        self.assertIn("Orphan.esp", self._order())

    def test_a_master_that_is_installed_but_switched_off_is_found(self):
        # The device's actual crash: Skyrim ships the free Anniversary
        # Edition Creation Club files in Data but leaves them out of the
        # plugin list, so 139 enabled plugins depended on 13 masters that
        # were never turned on. Checking only "is the file on disk"
        # reported everything as fine.
        main._write_plugins_txt(self.path, ["*Town.esp", "Base.esm"])
        s = self._state()
        self.assertEqual(s["disabled_masters"], 1)
        self.assertEqual(s["examples"], ["Base.esm"])

    def test_fixing_switches_those_masters_on(self):
        main._write_plugins_txt(self.path, ["*TownPatch.esp", "*Town.esp",
                                            "Base.esm"])
        r = self._fix()
        self.assertTrue(r["ok"])
        self.assertEqual(r["enabled_masters"], 1)
        entries = dict(main._plugin_entries(main._read_plugins_txt(self.path)))
        self.assertTrue(entries["Base.esm"])
        self.assertEqual(self._state()["disabled_masters"], 0)

    def test_a_required_master_missing_from_the_list_is_added(self):
        # Creation Club files are on disk but absent from Plugins.txt
        # entirely - there is nothing to flip, so it has to be inserted.
        main._write_plugins_txt(self.path, ["*Town.esp"])
        r = self._fix()
        self.assertEqual(r["enabled_masters"], 1)
        self.assertEqual(self._order(), ["Base.esm", "Town.esp"])

    def test_enabling_a_master_brings_its_own_masters_too(self):
        _make_plugin(os.path.join(self.data, "Mid.esp"), ["Base.esm"])
        _make_plugin(os.path.join(self.data, "Top.esp"), ["Mid.esp"])
        main._write_plugins_txt(self.path, ["*Top.esp", "Mid.esp", "Base.esm"])
        self._fix()
        entries = dict(main._plugin_entries(main._read_plugins_txt(self.path)))
        self.assertTrue(entries["Mid.esp"], "the direct master")
        self.assertTrue(entries["Base.esm"], "the master's own master")

    def test_a_master_that_is_not_installed_is_left_alone(self):
        # Nothing to switch on, and pretending otherwise would report a
        # fix that cannot have happened.
        _make_plugin(os.path.join(self.data, "Orphan.esp"), ["Gone.esm"])
        main._write_plugins_txt(self.path, ["*Orphan.esp"])
        self.assertEqual(self._state()["disabled_masters"], 0)

    def test_it_does_not_switch_on_unrelated_plugins(self):
        # Turning things on that nobody asked for would silently change
        # what the user installed.
        _make_plugin(os.path.join(self.data, "Unrelated.esp"))
        main._write_plugins_txt(self.path, ["*Town.esp", "Base.esm",
                                            "Unrelated.esp"])
        self._fix()
        entries = dict(main._plugin_entries(main._read_plugins_txt(self.path)))
        self.assertFalse(entries["Unrelated.esp"])

    def test_the_games_own_masters_are_never_written_into_the_list(self):
        # Skyrim loads Skyrim.esm and its DLC implicitly and its launcher
        # never lists them. Writing them in renumbers every other plugin,
        # and the load index is what save files record - so this is a
        # save-breaking difference, not a cosmetic one. The first cut of
        # this feature happily "enabled" all five on the device.
        _make_plugin(os.path.join(self.data, "Skyrim.esm"), flags=1)
        _make_plugin(os.path.join(self.data, "Dawnguard.esm"),
                     ["Skyrim.esm"], flags=1)
        _make_plugin(os.path.join(self.data, "Mod.esp"),
                     ["Skyrim.esm", "Dawnguard.esm"])
        main._write_plugins_txt(self.path, ["*Mod.esp"])
        s = self._state("skyrimspecialedition")
        self.assertEqual(s["disabled_masters"], 0, "nothing to switch on")
        self._fix("skyrimspecialedition")
        self.assertEqual(self._order(), ["Mod.esp"])

    def test_base_masters_already_in_the_list_are_taken_back_out(self):
        main._write_plugins_txt(
            self.path, ["*Skyrim.esm", "*Update.esm", "*Town.esp", "*Base.esm"]
        )
        r = self._fix("skyrimspecialedition")
        self.assertEqual(r["removed_base_masters"], 2)
        self.assertNotIn("Skyrim.esm", self._order())
        self.assertNotIn("Update.esm", self._order())
        self.assertIn("Town.esp", self._order())

    def test_an_unknown_domain_leaves_the_list_exactly_as_found(self):
        # No implicit-master list for a game means no opinion about it;
        # guessing would be worse than doing nothing.
        main._write_plugins_txt(self.path, ["*Town.esp", "*Base.esm"])
        r = self._fix("someothergame")
        self.assertEqual(r["removed_base_masters"], 0)

    def test_timestamp_games_still_get_the_dependency_check(self):
        # FO3/FNV order by file mtime, so their file order is meaningless
        # and no violation count is reported. But "a master is installed
        # and switched off" is an engine-level fault with nothing to do
        # with which plugins.txt dialect a game speaks - Skyrim had 13 of
        # them breaking 139 plugins. Bailing out of the whole check
        # because half of it did not apply left these two games with no
        # dependency check at all.
        main._write_plugins_txt(self.path, ["Town.esp"])   # listed: no stars
        s = run(self.plugin.get_load_order_state(
            self.APP_ID, self.GAME, self.SUB, "listed", ""))
        self.assertTrue(s["supported"])
        self.assertTrue(s["timestamp_ordered"])
        self.assertEqual(s["violations"], 0, "order is by mtime; do not guess")
        self.assertEqual(s["disabled_masters"], 1)
        self.assertEqual(s["examples"], ["Base.esm"])

    def test_fixing_a_timestamp_game_switches_masters_on_without_stars(self):
        main._write_plugins_txt(self.path, ["Town.esp"])
        r = run(self.plugin.fix_load_order(
            self.APP_ID, self.GAME, self.SUB, "listed", ""))
        self.assertTrue(r["ok"])
        self.assertEqual(r["enabled_masters"], 1)
        raw = main._read_plugins_txt(self.path)
        self.assertNotIn("*Base.esm", raw, "listed style has no star prefix")
        self.assertIn("Base.esm", [l.strip() for l in raw])

    def test_a_master_switched_on_for_a_timestamp_game_is_restamped(self):
        # Enabling it is half the job: if it keeps a later mtime than its
        # dependents the engine still loads it after them.
        main._write_plugins_txt(self.path, ["Town.esp"])
        r = run(self.plugin.fix_load_order(
            self.APP_ID, self.GAME, self.SUB, "listed", ""))
        self.assertGreater(r.get("restamped", 0), 0)
        base = os.path.getmtime(os.path.join(self.data, "Base.esm"))
        town = os.path.getmtime(os.path.join(self.data, "Town.esp"))
        self.assertLess(base, town, "master must load first")

    def test_starred_games_are_unaffected_by_the_timestamp_path(self):
        self._write(["TownPatch.esp", "Town.esp", "Base.esm"])
        s = self._state()
        self.assertFalse(s["timestamp_ordered"])
        self.assertEqual(s["violations"], 3)


class TestInGameSignal(unittest.TestCase):
    """The save-load hunt needs to know the WORLD loaded, not that a menu
    appeared. Papyrus only logs when scripts run, so the log being written
    after launch is the signal."""

    APP_ID = 489830
    MARKER = "Skyrim Special Edition/Logs/Script/Papyrus.0.log"
    INI = "Skyrim Special Edition/Skyrim.ini"

    def setUp(self):
        self.plugin = main.Plugin()
        self.marker = main._game_prefs_path(self.APP_ID, self.MARKER)
        self.ini = main._game_prefs_path(self.APP_ID, self.INI)
        os.makedirs(os.path.dirname(self.marker), exist_ok=True)
        os.makedirs(os.path.dirname(self.ini), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(os.path.dirname(self.ini), ignore_errors=True)

    def test_no_log_yet_is_simply_not_in_game(self):
        # The normal state before anything loads - not an error.
        r = run(self.plugin.in_game_since(self.APP_ID, self.MARKER, 0))
        self.assertTrue(r["ok"])
        self.assertFalse(r["in_game"])

    def test_a_log_written_after_launch_means_the_world_loaded(self):
        with open(self.marker, "w") as f:
            f.write("[script] ok\n")
        launched = os.path.getmtime(self.marker) - 60
        self.assertTrue(
            run(self.plugin.in_game_since(self.APP_ID, self.MARKER, launched))["in_game"]
        )

    def test_a_log_left_over_from_a_previous_session_does_not_count(self):
        # Without the timestamp check, every launch would look successful
        # forever after the first one that reached the world.
        with open(self.marker, "w") as f:
            f.write("[script] old\n")
        launched = os.path.getmtime(self.marker) + 60
        self.assertFalse(
            run(self.plugin.in_game_since(self.APP_ID, self.MARKER, launched))["in_game"]
        )

    def test_switching_papyrus_logging_on_leaves_the_rest_of_the_ini_alone(self):
        with open(self.ini, "w", newline="") as f:
            f.write("[General]\r\nsLanguage=ENGLISH\r\n"
                    "[Papyrus]\r\nbEnableLogging=0\r\nfPostLoadUpdateTimeMS=500.0\r\n")
        r = run(self.plugin.enable_papyrus_logging(self.APP_ID, self.INI))
        self.assertTrue(r["ok"])
        with open(self.ini, "r", newline="") as f:
            out = f.read()
        self.assertIn("bEnableLogging=1", out)
        self.assertIn("sLanguage=ENGLISH", out)
        self.assertIn("fPostLoadUpdateTimeMS=500.0", out)
        self.assertIn("\r\n", out, "must not rewrite line endings")

    def test_a_missing_ini_is_reported_not_created(self):
        r = run(self.plugin.enable_papyrus_logging(self.APP_ID, self.INI))
        self.assertFalse(r["ok"])


class TestInstallRecordMerge(unittest.TestCase):
    """Several files of one mod must not erase each other's file lists.

    Records are keyed by mod NAME, and a collection routinely installs a
    main file plus patches from the same mod. Each install replaced the
    record outright, so every file but the last became untrackable and
    reset could not remove them. On a 1,972-mod collection that orphaned
    668 files and 20GB - twice - and both times it presented as "reset is
    broken" rather than "install forgot".
    """

    def _rec(self, file_id, files, plugins=()):
        return {"mod_id": 7, "file_id": file_id, "name": "A Mod",
                "files": list(files), "plugins": list(plugins)}

    def test_a_first_install_is_kept_as_is(self):
        r = self._rec(1, ["a.esp"])
        merged = main._merge_install_record(None, r)
        # Everything the caller passed survives, plus the ordering stamp
        # this path adds so two mods installed in the same second can still
        # be told apart. See _next_install_seq.
        self.assertGreater(merged.pop("install_seq"), 0)
        self.assertEqual(merged, r)
        self.assertNotIn("install_seq", r, "must not mutate the caller's dict")

    def test_a_second_file_of_the_same_mod_adds_to_the_list(self):
        first = self._rec(1, ["main.esp", "main.bsa"])
        second = self._rec(2, ["patch.esp"])
        merged = main._merge_install_record(first, second)
        self.assertEqual(
            merged["files"], ["main.esp", "main.bsa", "patch.esp"],
            "the first file's contents must remain removable")

    def test_reinstalling_the_same_file_replaces_rather_than_grows(self):
        # A repair pass: this file's list is already the whole truth for
        # it, and accumulating would keep names it no longer installs.
        first = self._rec(1, ["old.esp", "stale.esp"])
        again = self._rec(1, ["old.esp"])
        merged = main._merge_install_record(first, again)
        self.assertEqual(merged["files"], ["old.esp"])

    def test_duplicate_paths_across_files_are_not_doubled(self):
        first = self._rec(1, ["shared.esp"])
        second = self._rec(2, ["Shared.esp", "extra.esp"])
        merged = main._merge_install_record(first, second)
        self.assertEqual(len(merged["files"]), 2,
                         "same path in two files is still one file on disk")

    def test_plugins_merge_the_same_way(self):
        first = self._rec(1, ["a"], ["Main.esp"])
        second = self._rec(2, ["b"], ["Patch.esp"])
        merged = main._merge_install_record(first, second)
        self.assertEqual(merged["plugins"], ["Main.esp", "Patch.esp"])

    def test_the_newest_metadata_wins(self):
        first = self._rec(1, ["a"])
        first["version"] = "1.0"
        second = self._rec(2, ["b"])
        second["version"] = "2.0"
        merged = main._merge_install_record(first, second)
        self.assertEqual(merged["version"], "2.0")

    def test_every_contributing_file_id_is_remembered(self):
        merged = main._merge_install_record(
            self._rec(1, ["a"]), self._rec(2, ["b"]))
        self.assertEqual(merged["file_ids"], [1, 2])
        merged = main._merge_install_record(merged, self._rec(3, ["c"]))
        self.assertEqual(merged["file_ids"], [1, 2, 3])

    def test_three_files_of_one_mod_all_stay_removable(self):
        # The exact device shape: CC Myrwatch installed three times.
        rec = None
        for fid, files in ((1, ["one.esp"]), (2, ["two.esp"]),
                           (3, [f"f{i}" for i in range(42)])):
            rec = main._merge_install_record(rec, self._rec(fid, files))
        self.assertEqual(len(rec["files"]), 44)


class TestEnforceSkips(unittest.TestCase):
    """Skips must survive both the install order and the game itself.

    Clean install of a 1,972-mod collection, both found on device:
    GTS - Orpheus Replacer installed BEFORE the master it needs was
    skipped, so the per-install check never saw it; and Skyrim rewrote
    Plugins.txt mid-run and switched two skips back on.
    """

    GAME = "Enforce Test"
    DOMAIN = "enforcetest"
    APP_ID = 489830
    SUB = "Skyrim Special Edition/Plugins.txt"

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        self.data = os.path.join(self.install, "Data")
        os.makedirs(self.data)
        _make_plugin(os.path.join(self.data, "Base.esm"), flags=1)
        _make_plugin(os.path.join(self.data, "Bad.esp"), ["Base.esm"])
        _make_plugin(os.path.join(self.data, "NeedsBad.esp"), ["Bad.esp"])
        _make_plugin(os.path.join(self.data, "Fine.esp"), ["Base.esm"])
        self.path = main._plugins_txt_path(self.APP_ID, self.SUB)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        settings = main._load_settings()
        settings.setdefault("skipped", {})[self.DOMAIN] = {
            "bad.esp": {"reason": "crashes", "root": True}
        }
        main._save_settings(settings)
        self.plugin = main.Plugin()

    def tearDown(self):
        settings = main._load_settings()
        settings.get("skipped", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)
        shutil.rmtree(self.install, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.path), ignore_errors=True)

    def _args(self):
        return (self.APP_ID, self.GAME, self.SUB, "starred", self.DOMAIN)

    def _on(self):
        return {n for n, o in main._plugin_entries(
            main._read_plugins_txt(self.path), "starred") if o}

    def test_a_dependent_installed_before_its_master_is_caught(self):
        # The install-time check could not see this one: NeedsBad was
        # already installed and enabled when Bad was skipped.
        main._write_plugins_txt(
            self.path, ["*Base.esm", "Bad.esp", "*NeedsBad.esp", "*Fine.esp"])
        r = run(self.plugin.enforce_skips(*self._args()))
        self.assertEqual(r["new_dependents"], 1)
        self.assertNotIn("NeedsBad.esp", self._on())
        self.assertIn("Fine.esp", self._on(), "unrelated mods stay on")

    def test_a_skip_the_game_switched_back_on_is_switched_off_again(self):
        main._write_plugins_txt(
            self.path, ["*Base.esm", "*Bad.esp", "*Fine.esp"])
        r = run(self.plugin.enforce_skips(*self._args()))
        self.assertEqual(r["changed"], 1)
        self.assertNotIn("Bad.esp", self._on())

    def test_it_is_idempotent(self):
        main._write_plugins_txt(
            self.path, ["*Base.esm", "*Bad.esp", "*NeedsBad.esp", "*Fine.esp"])
        run(self.plugin.enforce_skips(*self._args()))
        first = self._on()
        second = run(self.plugin.enforce_skips(*self._args()))
        self.assertEqual(self._on(), first)
        self.assertEqual(second["changed"], 0,
                         "runs on every game exit - must stay quiet")

    def test_nothing_recorded_means_nothing_touched(self):
        settings = main._load_settings()
        settings.get("skipped", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)
        main._write_plugins_txt(self.path, ["*Base.esm", "*Bad.esp"])
        run(self.plugin.enforce_skips(*self._args()))
        self.assertEqual(self._on(), {"Base.esm", "Bad.esp"})

    def test_listed_style_drops_the_line_rather_than_unstarring_it(self):
        # FO3/FNV: presence IS activation, so the only way off is out.
        main._write_plugins_txt(self.path, ["Base.esm", "Bad.esp", "Fine.esp"])
        run(self.plugin.enforce_skips(
            self.APP_ID, self.GAME, self.SUB, "listed", self.DOMAIN))
        names = [n for n, _ in main._plugin_entries(
            main._read_plugins_txt(self.path), "listed")]
        self.assertNotIn("Bad.esp", names)
        self.assertIn("Fine.esp", names)


class TestDownloadResumePlan(unittest.TestCase):
    """How a download continues from a .part after pause or failure.

    Appending at the wrong offset corrupts an archive in a way nothing
    notices until extraction fails - so every branch of this decision is
    pinned, not sampled. The rest of pause/resume is I/O plumbing; this
    is the part that must be exactly right.
    """

    def test_a_clean_range_resume_appends(self):
        mode, offset, total = main._resume_plan(
            100, 206, "bytes 100-999/1000", 900)
        self.assertEqual((mode, offset, total), ("append", 100, 1000))

    def test_a_server_resuming_from_the_wrong_place_forces_a_restart(self):
        # We have 100 bytes; the server resumes from 50. Appending would
        # interleave two different ranges of the file.
        mode, offset, total = main._resume_plan(
            100, 206, "bytes 50-999/1000", 950)
        self.assertEqual(mode, "restart")

    def test_a_206_with_unknown_total_still_appends(self):
        mode, offset, total = main._resume_plan(
            100, 206, "bytes 100-999/*", 900)
        self.assertEqual((mode, offset), ("append", 100))
        self.assertEqual(total, 0, "unknown total must not be invented")

    def test_a_206_with_no_content_range_derives_the_total(self):
        mode, offset, total = main._resume_plan(100, 206, "", 900)
        self.assertEqual((mode, offset, total), ("append", 100, 1000))

    def test_a_server_ignoring_range_restarts_from_zero(self):
        # 200 means "here is the whole file" no matter what we asked for.
        mode, offset, total = main._resume_plan(100, 200, "", 1000)
        self.assertEqual((mode, offset, total), ("restart", 0, 1000))

    def test_a_fresh_download_is_just_a_restart_at_zero(self):
        mode, offset, total = main._resume_plan(0, 200, "", 1000)
        self.assertEqual((mode, offset, total), ("restart", 0, 1000))

    def test_garbled_content_range_falls_back_to_trusting_the_206(self):
        mode, offset, total = main._resume_plan(
            100, 206, "bytes=nonsense", 900)
        self.assertEqual((mode, offset, total), ("append", 100, 1000))


class TestDownloadControls(unittest.TestCase):
    """Pause is global, cancel is per-download and only for downloads
    actually in flight - a mark left on an idle mod would silently kill
    its next retry."""

    def setUp(self):
        self.plugin = main.Plugin()
        main._DL_PAUSED = False
        main._DL_ACTIVE.clear()
        main._DL_CANCEL.clear()

    tearDown = setUp

    def test_pause_and_resume_flip_the_global_gate(self):
        r = run(self.plugin.set_downloads_paused(True))
        self.assertTrue(r["paused"])
        self.assertTrue(main._DL_PAUSED)
        r = run(self.plugin.set_downloads_paused(False))
        self.assertFalse(r["paused"])
        self.assertFalse(main._DL_PAUSED)

    def test_cancel_refuses_a_mod_that_is_not_downloading(self):
        r = run(self.plugin.cancel_download(42))
        self.assertFalse(r["ok"])
        self.assertNotIn(42, main._DL_CANCEL,
                         "no mark may be left to kill a later retry")

    def test_cancel_marks_only_an_in_flight_download(self):
        main._DL_ACTIVE.add(42)
        r = run(self.plugin.cancel_download(42))
        self.assertTrue(r["ok"])
        self.assertIn(42, main._DL_CANCEL)

    def test_the_paused_wait_releases_on_resume(self):
        async def scenario():
            main._DL_PAUSED = True
            waiter = asyncio.create_task(main._wait_while_paused(1, 50))
            await asyncio.sleep(0.05)
            self.assertFalse(waiter.done(), "must hold while paused")
            main._DL_PAUSED = False
            await asyncio.wait_for(waiter, timeout=2)
        run(scenario())

    def test_the_paused_wait_releases_on_that_downloads_cancel(self):
        # A cancel issued DURING a pause must not sit blocked behind the
        # global gate until someone resumes everything.
        async def scenario():
            main._DL_PAUSED = True
            waiter = asyncio.create_task(main._wait_while_paused(7, 10))
            await asyncio.sleep(0.05)
            main._DL_CANCEL.add(7)
            await asyncio.wait_for(waiter, timeout=2)
        run(scenario())

    def test_control_state_reports_the_truth(self):
        main._DL_ACTIVE.update({1, 2, 3})
        main._DL_PAUSED = True
        r = run(self.plugin.get_download_control())
        self.assertEqual((r["paused"], r["in_flight"]), (True, 3))


class TestResetVerifiesItsOwnWork(unittest.TestCase):
    """Reset must not report success it has not checked.

    Device: "1543 mods removed, 0 errors" while 20GB and roughly 400 mods
    stayed in Data, because an install that dies between copying files and
    writing its record leaves nothing to remove them by. The only thing
    that caught it was the main menu looking wrong.
    """

    GAME = "Reset Verify Test"
    DOMAIN = "resetverifytest"
    APP_ID = 489830

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        self.mods = os.path.join(self.install, "Data")
        os.makedirs(self.mods)
        for n in ("Skyrim.esm", "Skyrim - Misc.bsa"):
            with open(os.path.join(self.mods, n), "w") as f:
                f.write("vanilla")
        self.plugin = main.Plugin()

    def tearDown(self):
        settings = main._load_settings()
        for sec in ("vanilla_baseline", "installed"):
            settings.get(sec, {}).pop(self.DOMAIN, None)
        main._save_settings(settings)
        shutil.rmtree(self.install, ignore_errors=True)

    def _reset(self):
        return run(self.plugin.reset_game_modding(
            self.DOMAIN, self.GAME, "Data", "dataDir", self.APP_ID))

    def test_the_baseline_is_only_taken_once(self):
        main._record_vanilla_baseline(self.DOMAIN, self.mods)
        first = main._vanilla_baseline(self.DOMAIN)
        with open(os.path.join(self.mods, "AMod.esp"), "w") as f:
            f.write("x")
        main._record_vanilla_baseline(self.DOMAIN, self.mods)
        self.assertEqual(main._vanilla_baseline(self.DOMAIN), first,
                         "a later snapshot would bless the mods as vanilla")

    def test_a_clean_reset_reports_no_leftovers(self):
        main._record_vanilla_baseline(self.DOMAIN, self.mods)
        r = self._reset()
        self.assertTrue(r["verified"])
        self.assertEqual(r["leftovers"], 0)

    def test_unrecorded_files_are_swept_not_merely_reported(self):
        main._record_vanilla_baseline(self.DOMAIN, self.mods)
        # Runtime droppings: config, logs and caches that mods WRITE
        # while running. Nothing installed them, so nothing can uninstall
        # them - a device reset left 16 in Data/SKSE and Data/seasons.
        for n in ("Ghost.esp", "Ghost.bsa"):
            with open(os.path.join(self.mods, n), "w") as f:
                f.write("x")
        os.makedirs(os.path.join(self.mods, "SKSE", "Plugins"))
        with open(os.path.join(self.mods, "SKSE", "Plugins", "x.ini"), "w") as f:
            f.write("x")
        r = self._reset()
        self.assertEqual(r["swept"], 3)
        self.assertEqual(r["leftovers"], 0, "vanilla means vanilla")
        self.assertFalse(os.path.exists(os.path.join(self.mods, "SKSE")))

    def test_the_sweep_never_touches_the_vanilla_baseline(self):
        main._record_vanilla_baseline(self.DOMAIN, self.mods)
        with open(os.path.join(self.mods, "Ghost.esp"), "w") as f:
            f.write("x")
        self._reset()
        left = sorted(os.listdir(self.mods))
        self.assertEqual(left, ["Skyrim - Misc.bsa", "Skyrim.esm"])

    def test_without_a_baseline_nothing_is_swept(self):
        # No snapshot means no idea what the user started with, and
        # deleting on a guess is worse than leaving a mess.
        with open(os.path.join(self.mods, "Ghost.esp"), "w") as f:
            f.write("x")
        r = self._reset()
        self.assertEqual(r["swept"], 0)
        self.assertTrue(os.path.exists(os.path.join(self.mods, "Ghost.esp")))

    def test_without_a_baseline_it_says_so_rather_than_claiming_clean(self):
        # Games modded before this existed have no snapshot. Reporting
        # zero leftovers there would be a guess dressed as a fact.
        r = self._reset()
        self.assertFalse(r["verified"])


class TestKnownBadNeverSwitchedOn(unittest.TestCase):
    """A plugin known to break the game is never activated in the first
    place.

    Switching it on and then asking the user to switch it off is a step we
    can simply not create. The console audience should never learn that
    some of their mods are broken - the tool knows, so it handles it.
    """

    GAME = "Install Skip Test"
    DOMAIN = "installskiptest"
    APP_ID = 489830
    SUB = "Skyrim Special Edition/Plugins.txt"

    def setUp(self):
        self.data = os.path.join(main.STEAM_COMMON, self.GAME, "Data")
        shutil.rmtree(os.path.join(main.STEAM_COMMON, self.GAME),
                      ignore_errors=True)
        os.makedirs(self.data)
        _make_plugin(os.path.join(self.data, "Bad.esp"))
        _make_plugin(os.path.join(self.data, "NeedsBad.esp"), ["Bad.esp"])
        _make_plugin(os.path.join(self.data, "Fine.esp"))
        self.path = main._plugins_txt_path(self.APP_ID, self.SUB)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        main._write_plugins_txt(self.path, [])
        main.KNOWN_BAD_PLUGINS[self.DOMAIN] = {"bad.esp": "crashes on load"}

    def tearDown(self):
        main.KNOWN_BAD_PLUGINS.pop(self.DOMAIN, None)
        settings = main._load_settings()
        settings.get("skipped", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)
        shutil.rmtree(os.path.join(main.STEAM_COMMON, self.GAME),
                      ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.path), ignore_errors=True)

    def _add(self, *names):
        main._add_plugins(self.path, list(names), "starred",
                          self.DOMAIN, self.data)

    def _state(self):
        return dict(main._plugin_entries(
            main._read_plugins_txt(self.path), "starred"))

    def test_a_known_bad_plugin_is_listed_but_off(self):
        self._add("Fine.esp", "Bad.esp")
        st = self._state()
        self.assertTrue(st["Fine.esp"])
        self.assertFalse(st["Bad.esp"], "never switched on in the first place")

    def test_the_reason_is_recorded_at_install_time(self):
        self._add("Bad.esp")
        self.assertEqual(
            main._load_skips(self.DOMAIN)["bad.esp"]["reason"],
            "crashes on load")

    def test_something_needing_it_is_left_off_too(self):
        # Installed AFTER its master was skipped, which is the normal
        # order - masters come first in a sorted collection install.
        self._add("Bad.esp")
        self._add("NeedsBad.esp")
        self.assertFalse(self._state()["NeedsBad.esp"])
        self.assertFalse(main._load_skips(self.DOMAIN)["needsbad.esp"]["root"])

    def test_unrelated_mods_are_activated_normally(self):
        self._add("Bad.esp", "NeedsBad.esp", "Fine.esp")
        self.assertTrue(self._state()["Fine.esp"])

    def test_a_game_with_no_known_bad_list_installs_everything(self):
        main.KNOWN_BAD_PLUGINS.pop(self.DOMAIN, None)
        self._add("Bad.esp", "NeedsBad.esp", "Fine.esp")
        self.assertTrue(all(self._state().values()))

    def test_a_listed_style_game_leaves_it_out_of_the_file(self):
        # There is no "listed but off" in that dialect: presence IS
        # activation, so the only way to skip is to not list it.
        main._write_plugins_txt(self.path, [])
        main._add_plugins(self.path, ["Bad.esp", "Fine.esp"], "listed",
                          self.DOMAIN, self.data)
        names = [n for n, _ in main._plugin_entries(
            main._read_plugins_txt(self.path), "listed")]
        self.assertEqual(names, ["Fine.esp"])


class TestResetClearsOurOwnArtefacts(unittest.TestCase):
    """Reset means vanilla, including the things this plugin renamed.

    Device: after a reset the panel still offered to "restore 2 skipped
    plugins" for mods that no longer existed, because parked DLLs keep
    their file and only lose their extension. The recorded skips survived
    too, so a fresh install would have inherited decisions about a setup
    that was gone.
    """

    GAME = "Reset Artefact Test"
    DOMAIN = "resetartefacttest"
    APP_ID = 489830
    SUB = "Skyrim Special Edition/Plugins.txt"

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        self.se = os.path.join(self.install, "Data", "SKSE", "Plugins")
        os.makedirs(self.se)
        with open(os.path.join(self.se, "Live.dll"), "w") as f:
            f.write("x")
        for n in ("Parked.dll", "AlsoParked.dll"):
            with open(os.path.join(self.se, n + main.SE_DISABLED_SUFFIX),
                      "w") as f:
                f.write("x")
        settings = main._load_settings()
        settings.setdefault("skipped", {})[self.DOMAIN] = {
            "bad.esp": {"reason": "crashes", "root": True}
        }
        main._save_settings(settings)
        self.plugin = main.Plugin()

    def tearDown(self):
        settings = main._load_settings()
        settings.get("skipped", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)
        shutil.rmtree(self.install, ignore_errors=True)

    def _reset(self):
        return run(self.plugin.reset_game_modding(
            self.DOMAIN, self.GAME, "Data", "dataDir", self.APP_ID, self.SUB))

    def test_parked_plugins_are_removed_not_left_orphaned(self):
        r = self._reset()
        self.assertTrue(r["ok"])
        left = [n for n in os.listdir(self.se)
                if n.endswith(main.SE_DISABLED_SUFFIX)]
        self.assertEqual(left, [], "nothing to restore should remain")

    def test_it_does_not_delete_plugins_it_never_parked(self):
        self._reset()
        self.assertIn("Live.dll", os.listdir(self.se))

    def test_recorded_skips_go_with_the_mods_they_were_about(self):
        self._reset()
        self.assertEqual(main._load_skips(self.DOMAIN), {})


class TestKnownBadPlugins(unittest.TestCase):
    """Plugins proven to break a game are switched off automatically, and
    STAY off.

    The crash hunt exists so that one person finds a fault, not so every
    user reruns it. And a skip has to be sticky: on device, fix_load_order
    switched 8 skipped plugins back on because something still named them
    as a master, which put the crash straight back.
    """

    GAME = "Known Bad Test"
    APP_ID = 489830
    SUB = "Skyrim Special Edition/Plugins.txt"
    DOMAIN = "knownbadtest"

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        self.data = os.path.join(self.install, "Data")
        os.makedirs(self.data)
        _make_plugin(os.path.join(self.data, "Base.esm"), flags=1)
        _make_plugin(os.path.join(self.data, "Bad.esp"), ["Base.esm"])
        _make_plugin(os.path.join(self.data, "NeedsBad.esp"), ["Bad.esp"])
        _make_plugin(os.path.join(self.data, "Deeper.esp"), ["NeedsBad.esp"])
        _make_plugin(os.path.join(self.data, "Fine.esp"), ["Base.esm"])
        self.path = main._plugins_txt_path(self.APP_ID, self.SUB)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        main._write_plugins_txt(self.path, [
            "*Base.esm", "*Bad.esp", "*NeedsBad.esp", "*Deeper.esp", "*Fine.esp",
        ])
        main.KNOWN_BAD_PLUGINS[self.DOMAIN] = {"bad.esp": "crashes on load"}
        self.plugin = main.Plugin()

    def tearDown(self):
        main.KNOWN_BAD_PLUGINS.pop(self.DOMAIN, None)
        settings = main._load_settings()
        settings.get("skipped", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)
        shutil.rmtree(self.install, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.path), ignore_errors=True)

    def _args(self):
        return (self.APP_ID, self.GAME, self.SUB, "starred", self.DOMAIN)

    def _on(self):
        return {n for n, o in main._plugin_entries(
            main._read_plugins_txt(self.path), "starred") if o}

    def test_it_reports_what_it_knows_is_broken(self):
        s = run(self.plugin.get_known_bad_state(*self._args()))
        self.assertEqual([b["name"] for b in s["bad"]], ["Bad.esp"])
        self.assertEqual(s["extra"], 2, "NeedsBad and Deeper cannot load")
        self.assertIn("crashes", s["bad"][0]["reason"])

    def test_applying_it_takes_the_dependents_too(self):
        r = run(self.plugin.apply_known_bad(*self._args()))
        self.assertEqual((r["skipped"], r["extra"]), (1, 2))
        self.assertEqual(self._on(), {"Base.esm", "Fine.esp"})

    def test_a_skip_survives_a_load_order_repair(self):
        # The device failure: NeedsBad still names Bad as a master, so the
        # master repair helpfully switched Bad back on.
        run(self.plugin.apply_known_bad(*self._args()))
        run(self.plugin.fix_load_order(*self._args()))
        self.assertNotIn("Bad.esp", self._on(), "the skip must be sticky")
        self.assertNotIn("NeedsBad.esp", self._on())

    def test_the_reason_is_recorded_not_just_the_fact(self):
        run(self.plugin.apply_known_bad(*self._args()))
        skips = main._load_skips(self.DOMAIN)
        self.assertTrue(skips["bad.esp"]["root"])
        self.assertFalse(skips["needsbad.esp"]["root"])
        self.assertIn("crashes", skips["bad.esp"]["reason"])

    def test_a_skipped_plugin_is_not_reported_as_a_missing_master(self):
        run(self.plugin.apply_known_bad(*self._args()))
        st = run(self.plugin.get_load_order_state(*self._args()))
        self.assertEqual(st["disabled_masters"], 0,
                         "a deliberate skip is not a fault to repair")

    def test_applying_twice_changes_nothing(self):
        run(self.plugin.apply_known_bad(*self._args()))
        first = self._on()
        run(self.plugin.apply_known_bad(*self._args()))
        self.assertEqual(self._on(), first)

    def test_a_game_with_nothing_known_is_left_alone(self):
        main.KNOWN_BAD_PLUGINS.pop(self.DOMAIN, None)
        s = run(self.plugin.get_known_bad_state(*self._args()))
        self.assertEqual(s["bad"], [])
        run(self.plugin.apply_known_bad(*self._args()))
        self.assertEqual(len(self._on()), 5, "nothing switched off")


class TestHuntStartsFromTheFullList(unittest.TestCase):
    """A hunt must search every mod, not the leftovers of the last one.

    Device: an interrupted run left the load order halfway through a test.
    Starting again snapshotted "whatever is enabled right now" as the
    whole search space, quietly reducing 1,947 plugins to 968 - so a
    culprit in the other half could never be found, and the game was
    missing half its mods the entire time.
    """

    GAME = "Hunt Restore Test"
    APP_ID = 489830
    SUB = "Skyrim Special Edition/Plugins.txt"
    LOG = "Skyrim Special Edition/SKSE/skse64.log"

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        self.data = os.path.join(self.install, "Data")
        os.makedirs(os.path.join(self.data, "SKSE", "Plugins"))
        self.names = [f"m{i}.esp" for i in range(10)]
        for n in self.names:
            _make_plugin(os.path.join(self.data, n))
        self.path = main._plugins_txt_path(self.APP_ID, self.SUB)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # A crash log for the signature auto-detect to read.
        se = os.path.dirname(main._game_prefs_path(self.APP_ID, self.LOG))
        os.makedirs(se, exist_ok=True)
        with open(os.path.join(se, "crash-2026-08-11-00-00-00.log"), "w") as f:
            f.write('Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at '
                    "0x0001401D8845 SkyrimSE.exe+01D8845\n"
                    "CALL STACK ([P]robable / [S]tack scan):\n"
                    "\t[ 0][P] 0x0001401D8845 SkyrimSE.exe+01D8845 -> 1+0x1\n")
        self.plugin = main.Plugin()

    def tearDown(self):
        shutil.rmtree(self.install, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.path), ignore_errors=True)

    def _start(self):
        return run(self.plugin.crash_bisect_start(
            self.APP_ID, self.GAME, self.SUB, "starred",
            "skyrimspecialedition", "", self.LOG, []))

    def test_it_searches_every_enabled_mod(self):
        main._write_plugins_txt(self.path, ["*" + n for n in self.names])
        self.assertEqual(self._start()["total"], 10)

    def test_a_half_disabled_list_from_an_interrupted_run_is_restored(self):
        main._write_plugins_txt(self.path, ["*" + n for n in self.names])
        r = self._start()
        self.assertEqual(r["total"], 10)
        # Simulate the interruption: a test applied, then Decky restarted.
        main._write_plugins_txt(
            self.path,
            ["*" + n for n in self.names[:5]] + self.names[5:])
        again = self._start()
        self.assertEqual(again["total"], 10,
                         "the second hunt must not inherit the first's cut")

    def test_the_detected_signature_carries_its_offset(self):
        main._write_plugins_txt(self.path, ["*" + n for n in self.names])
        self.assertEqual(self._start()["signature"], "SkyrimSE.exe+01D8845")

    def test_a_finished_hunt_leaves_no_stale_backup(self):
        main._write_plugins_txt(self.path, ["*" + n for n in self.names])
        self._start()
        run(self.plugin.crash_bisect_finish(True))
        self.assertFalse(
            os.path.isfile(self.path + ".decky-bisect-orig"),
            "a stale copy would restore a list predating later installs")


class TestHuntSignatureDetection(unittest.TestCase):
    """The hunt has to identify ONE fault, not a module.

    First cut took the module name off the top call-stack frame, which
    gave "SkyrimSE.exe" - and nearly every Skyrim crash is in
    SkyrimSE.exe, so the data-load crash and the facegen crash would have
    counted as the same thing. Caught by reading the state file on device
    before it had run a single launch.
    """

    APP_ID = 489830
    LOG = "Skyrim Special Edition/SKSE/skse64.log"

    EXC = ('Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at '
           "0x0001401D8845 SkyrimSE.exe+01D8845\tmov rax, [rcx+0x30]\n")

    # The same pattern crash_bisect_start uses to read a fault's identity.
    SIG_RE = r"at (0x[0-9A-Fa-f]+)\s+(\S+\+[0-9A-Fa-f]+)"

    def setUp(self):
        self.se_dir = os.path.dirname(main._game_prefs_path(self.APP_ID, self.LOG))
        os.makedirs(self.se_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.se_dir, ignore_errors=True)

    def _write(self, body, name="crash-2026-08-11-14-58-13.log"):
        with open(os.path.join(self.se_dir, name), "w") as f:
            f.write(body)

    def test_the_offset_is_part_of_the_signature(self):
        self._write(
            "CRASH TIME: 2026-08-11 14:58:13\n" + self.EXC
            + "CALL STACK ([P]robable / [S]tack scan):\n"
            "\t[ 0][P] 0x0001401D8845 SkyrimSE.exe+01D8845 -> 1+0x1\n"
        )
        parsed = main._parse_crash_log(
            os.path.join(self.se_dir, "crash-2026-08-11-14-58-13.log"))
        import re as _re
        m = _re.search(self.SIG_RE, parsed.get("exception") or "")
        self.assertIsNotNone(m, "exception line must yield an address")
        self.assertEqual(m.group(2), "SkyrimSE.exe+01D8845")
        self.assertNotEqual(
            m.group(2), "SkyrimSE.exe",
            "the module alone matches every crash in the game")

    def test_two_different_faults_in_the_same_module_differ(self):
        # +01D74A0 (data load) vs +0CEC9C8 (facegen). Both "SkyrimSE.exe".
        sigs = set()
        for off in ("01D74A0", "0CEC9C8"):
            line = ('Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at '
                    f"0x00014{off} SkyrimSE.exe+{off}\n")
            import re as _re
            m = _re.search(self.SIG_RE, line)
            sigs.add(m.group(2))
        self.assertEqual(len(sigs), 2, "must tell the two faults apart")


class TestDependentsClosure(unittest.TestCase):
    """Skipping a mod has to take everything built on it.

    Device, Gate To Sovngarde: the hunt reported 14 broken plugins. Only
    3 were independent - the other 11 each mastered one of those 3, so
    they crashed because the hunt had disabled their master, and it spent
    about three hours discovering the consequences of its own first skip.
    """

    GAME = "Closure Test"

    def setUp(self):
        self.data = os.path.join(main.STEAM_COMMON, self.GAME, "Data")
        shutil.rmtree(os.path.join(main.STEAM_COMMON, self.GAME),
                      ignore_errors=True)
        os.makedirs(self.data)

    def tearDown(self):
        shutil.rmtree(os.path.join(main.STEAM_COMMON, self.GAME),
                      ignore_errors=True)

    def _mk(self, name, masters=()):
        _make_plugin(os.path.join(self.data, name), masters)

    def test_direct_dependents_come_too(self):
        self._mk("Root.esp")
        self._mk("Child.esp", ["Root.esp"])
        self._mk("Unrelated.esp")
        out = main._dependents_closure(
            self.data, ["Root.esp", "Child.esp", "Unrelated.esp"], {"Root.esp"})
        self.assertEqual(out, ["Child.esp"])

    def test_the_chain_is_followed_all_the_way_down(self):
        # GTS_Traits -> New Armors -> Orpheus Replacer was the real shape.
        self._mk("A.esp")
        self._mk("B.esp", ["A.esp"])
        self._mk("C.esp", ["B.esp"])
        self._mk("D.esp", ["C.esp"])
        out = main._dependents_closure(
            self.data, ["A.esp", "B.esp", "C.esp", "D.esp"], {"A.esp"})
        self.assertEqual(set(out), {"B.esp", "C.esp", "D.esp"})

    def test_a_plugin_needing_any_one_doomed_master_is_doomed(self):
        self._mk("A.esp")
        self._mk("Fine.esp")
        self._mk("Both.esp", ["Fine.esp", "A.esp"])
        out = main._dependents_closure(
            self.data, ["A.esp", "Fine.esp", "Both.esp"], {"A.esp"})
        self.assertEqual(out, ["Both.esp"])

    def test_nothing_depending_on_it_means_nothing_extra(self):
        self._mk("Lonely.esp")
        self._mk("Other.esp")
        self.assertEqual(
            main._dependents_closure(
                self.data, ["Lonely.esp", "Other.esp"], {"Lonely.esp"}),
            [],
        )

    def test_the_targets_are_not_returned_as_their_own_dependents(self):
        self._mk("A.esp")
        self._mk("B.esp", ["A.esp"])
        out = main._dependents_closure(
            self.data, ["A.esp", "B.esp"], {"A.esp", "B.esp"})
        self.assertEqual(out, [])

    def test_a_master_cycle_terminates(self):
        self._mk("X.esp", ["Y.esp"])
        self._mk("Y.esp", ["X.esp"])
        self._mk("Z.esp", ["X.esp"])
        out = main._dependents_closure(
            self.data, ["X.esp", "Y.esp", "Z.esp"], {"X.esp"})
        self.assertEqual(set(out), {"Y.esp", "Z.esp"})

    def test_a_plugin_missing_from_disk_is_skipped_not_crashed_on(self):
        self._mk("A.esp")
        out = main._dependents_closure(
            self.data, ["A.esp", "Ghost.esp"], {"A.esp"})
        self.assertEqual(out, [])


class TestCrashBisectMachine(unittest.TestCase):
    """The automated hunt's decision logic, tested without a game.

    Doing this by hand on device found five culprits over two days, and
    every wasted launch came from a human varying something between steps.
    These tests pin the arithmetic so the machine cannot repeat that.
    """

    def _run(self, order, bad, limit=200):
        """Drive the machine against a known-bad set; return what it found
        and how many launches it took."""
        state = {"order": list(order), "skipped": [], "lo": 0,
                 "hi": len(order), "launches": 0, "found": None,
                 "hi_verified": False}
        launches = 0
        while state["hi"] > state["lo"] and launches < limit:
            mid = main._bisect_next_prefix(state)
            state["testing"] = mid
            live = set(order[:mid]) - set(state["skipped"])
            crashed = bool(live & set(bad))
            state = main._bisect_advance(state, crashed)
            launches += 1
        return state["skipped"], launches

    def test_it_finds_a_single_culprit(self):
        order = [f"m{i}.esp" for i in range(64)]
        found, _ = self._run(order, {"m40.esp"})
        self.assertEqual(found, ["m40.esp"])

    def test_it_finds_every_culprit_not_just_the_first(self):
        order = [f"m{i}.esp" for i in range(64)]
        bad = {"m5.esp", "m30.esp", "m31.esp", "m63.esp"}
        found, _ = self._run(order, bad)
        self.assertEqual(set(found), bad)

    def test_adjacent_culprits_are_both_found(self):
        # Device: NJR - Bruma Patch and CC_MenagerieECSS sat at 1813 and
        # 1814. An off-by-one when moving the known-good edge would skip
        # the second one entirely.
        order = [f"m{i}.esp" for i in range(32)]
        found, _ = self._run(order, {"m10.esp", "m11.esp"})
        self.assertEqual(found, ["m10.esp", "m11.esp"])

    def test_the_first_and_last_plugin_are_both_reachable(self):
        order = [f"m{i}.esp" for i in range(32)]
        self.assertEqual(self._run(order, {"m0.esp"})[0], ["m0.esp"])
        self.assertEqual(self._run(order, {"m31.esp"})[0], ["m31.esp"])

    def test_a_clean_load_order_finds_nothing_and_stops(self):
        order = [f"m{i}.esp" for i in range(64)]
        found, launches = self._run(order, set())
        self.assertEqual(found, [])
        # One launch: it checks the full set, sees it boot, and stops.
        self.assertEqual(launches, 1)

    def test_it_costs_about_log2_launches_per_culprit(self):
        # 1,960 plugins by hand took ~12 launches per culprit. The machine
        # must not be worse, or automating it buys nothing.
        #
        # log2(2048) = 11 narrowing launches, plus one at the start to
        # confirm the crash reproduces and one at the end to confirm
        # nothing else is left. Both are launches a careful human would
        # also spend - and twice this session I skipped the "is it still
        # the same crash" check and paid for it.
        order = [f"m{i}.esp" for i in range(2048)]
        found, launches = self._run(order, {"m1500.esp"})
        self.assertEqual(found, ["m1500.esp"])
        self.assertLessEqual(launches, 13)

    def test_every_plugin_being_broken_still_terminates(self):
        order = [f"m{i}.esp" for i in range(16)]
        found, launches = self._run(order, set(order))
        self.assertEqual(set(found), set(order))
        self.assertLess(launches, 200, "must not loop forever")

    def test_a_result_arriving_with_nothing_under_test_is_ignored(self):
        # Guards against a stray record() - e.g. the panel retrying after
        # a Decky restart - corrupting the bounds.
        state = {"order": ["a.esp", "b.esp"], "skipped": [], "lo": 0,
                 "hi": 2, "launches": 0, "hi_verified": True}
        after = main._bisect_advance(dict(state), True)
        self.assertEqual(after["lo"], 0)
        self.assertEqual(after["hi"], 2)
        self.assertEqual(after.get("launches", 0), 0)


class TestCrashCulprits(unittest.TestCase):
    """A plugin SKSE loads happily can still crash the game later, and
    that leaves nothing in skse64.log - the only record is the crash log.
    Device, 2026-08-08: NPCWaterAIFix.dll logged "loaded successfully"
    and then took Skyrim down three minutes later."""

    GAME = "Crash Test"
    APP_ID = 489830
    LOG = "Skyrim Special Edition/SKSE/skse64.log"

    # Trimmed from the real crash-2026-08-08-12-08-13.log.
    CRASH = (
        "CRASH TIME: 2026-08-08 12:08:13\n"
        "Skyrim SSE v1.6.1170\n"
        "\n"
        'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x0001401D74A0\n'
        "\n"
        "CALL STACK ([P]robable / [S]tack scan):\n"
        "\t[ 0][P] 0x0001401D74A0      SkyrimSE.exe+01D74A0 -> 14371+0x10\n"
        "\t[ 6][P] 0x6FFFF3894153 NPCWaterAIFix.dll+0024153\n"
        "\t[ 7][P] 0x000140CD0DBD      SkyrimSE.exe+0CD0DBD -> 68445+0x3D\n"
        "\t[ 8][P] 0x6FFFFFED0C59      kernel32.dll+0010C59\n"
        "\t[ 9][P] 0x6FFFFFF4FB8F         ntdll.dll+000FB8F\n"
        "\t[10][S] 0x6FFFF1230000 Working.dll+0001234\n"
        "\n"
        "REGISTERS:\n"
        "\tRAX 0x2104             (size_t) [8452]\n"
    )

    def setUp(self):
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        self.plugins = os.path.join(self.install, "Data", "SKSE", "Plugins")
        os.makedirs(self.plugins)
        for n in ("NPCWaterAIFix.dll", "Working.dll"):
            with open(os.path.join(self.plugins, n), "w") as f:
                f.write("x")
        self.log = main._game_prefs_path(self.APP_ID, self.LOG)
        self.se_dir = os.path.dirname(self.log)
        os.makedirs(self.se_dir, exist_ok=True)
        with open(self.log, "w") as f:
            f.write("plugin NPCWaterAIFix.dll (1 NPC Water AI Fix 5) loaded "
                    "correctly (handle 88)\n")
        self.crash = os.path.join(self.se_dir, "crash-2026-08-08-12-08-13.log")
        self._write_crash(self.CRASH)
        # CrashLoggerSSE's own diary lives in the same folder and is
        # written a beat AFTER the report - so "newest file starting with
        # crash" picks it, and it has no call stack. That is exactly how
        # this came back empty against the real device folder.
        self._write_crash(
            "[info] CrashLoggerSSE v1-24-0-0 loaded\n",
            os.path.join(self.se_dir, "CrashLogger.log"),
            offset=61,
        )
        self.plugin = main.Plugin()

    def _write_crash(self, body, where=None, offset=60):
        path = where or self.crash
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body)
        # Order the logs explicitly - the real signal is which is newer,
        # and same-second writes would make this a coin toss.
        launch = os.path.getmtime(self.log)
        os.utime(path, (launch + offset, launch + offset))
        return path

    def tearDown(self):
        shutil.rmtree(self.install, ignore_errors=True)
        shutil.rmtree(self.se_dir, ignore_errors=True)

    def _crash(self):
        return run(
            self.plugin.get_script_extender_state(
                self.APP_ID, self.GAME, self.LOG
            )
        ).get("crash") or {}

    def test_it_names_the_mod_dll_and_ignores_the_game_and_windows(self):
        c = self._crash()
        names = [x["name"] for x in c["culprits"]]
        # SkyrimSE.exe, kernel32 and ntdll are on the same stack and are
        # not ours to touch; the filter is "is it in the plugins folder".
        self.assertEqual(names[0], "NPCWaterAIFix.dll")
        self.assertNotIn("kernel32.dll", names)
        self.assertNotIn("SkyrimSE.exe", names)
        self.assertEqual(c["crashed_at"], "2026-08-08 12:08:13")

    def test_a_stack_scan_hit_ranks_below_a_real_frame(self):
        # Working.dll is at frame 10 but only via stack scan, so it must
        # never outrank a genuine frame - a scanned hit is a leftover
        # value that happens to look like a return address.
        c = self._crash()
        by_name = {x["name"]: x for x in c["culprits"]}
        self.assertFalse(by_name["Working.dll"]["probable"])
        self.assertEqual(c["culprits"][0]["name"], "NPCWaterAIFix.dll")

    def test_a_dll_not_in_the_plugins_folder_is_not_offered(self):
        os.remove(os.path.join(self.plugins, "NPCWaterAIFix.dll"))
        names = [x["name"] for x in self._crash().get("culprits", [])]
        self.assertNotIn("NPCWaterAIFix.dll", names)

    def test_a_launch_since_the_crash_retires_it(self):
        # The extender rewrites its log every launch. A newer one means
        # the game has started since, so the crash is history and the
        # panel must stop nagging about it.
        self._write_crash(self.CRASH, offset=-60)
        self.assertEqual(self._crash(), {})

    def test_parking_the_suspect_clears_the_report(self):
        s = run(
            self.plugin.get_script_extender_state(
                self.APP_ID, self.GAME, self.LOG
            )
        )
        run(
            self.plugin.set_script_extender_plugins(
                self.GAME, s["plugins_dir"], ["NPCWaterAIFix.dll"], False
            )
        )
        names = [x["name"] for x in self._crash().get("culprits", [])]
        self.assertNotIn("NPCWaterAIFix.dll", names)

    def test_it_finds_logs_in_the_crashlogs_subfolder(self):
        os.remove(self.crash)
        self._write_crash(
            self.CRASH, os.path.join(self.se_dir, "Crashlogs", "crash-1.log")
        )
        self.assertEqual(
            self._crash()["culprits"][0]["name"], "NPCWaterAIFix.dll"
        )

    def test_buffout_style_frames_without_a_marker_still_parse(self):
        os.remove(self.crash)
        self._write_crash(
            "CALL STACK:\n"
            "\t[0] 0x7FF612340000 Fallout4.exe+1234567\n"
            "\t[1] 0x7FF6ABCD0000 NPCWaterAIFix.dll+0024153\n",
            os.path.join(self.se_dir, "crash-buffout.log"),
        )
        c = self._crash()
        self.assertEqual(c["culprits"][0]["name"], "NPCWaterAIFix.dll")
        # No [P]/[S] column at all, so every frame is taken at face value.
        self.assertTrue(c["culprits"][0]["probable"])

    def test_the_loggers_own_diary_is_not_mistaken_for_a_report(self):
        # CrashLogger.log is the newest "crash*" file in the folder and
        # holds no call stack. Matching it produced an empty report
        # against the real device folder while every synthetic test
        # passed, because no test had a diary in it.
        self.assertEqual(
            self._crash()["culprits"][0]["name"], "NPCWaterAIFix.dll"
        )

    def test_no_crash_log_is_just_an_empty_report(self):
        os.remove(self.crash)
        self.assertEqual(self._crash(), {})


class TestRootBinaryPayload(unittest.TestCase):
    """SSE Engine Fixes part 2 ships three loose dlls that must sit
    beside SkyrimSE.exe. They went into Data/ instead, and Engine Fixes
    then refused to start the game: "did not pre-load ... verify the
    installation of d3dx9_42.dll" (device, 2026-08-08)."""

    DOMAIN = "skyrimspecialedition"
    GAME = "Root Binary Test"
    MOD, FILE = 17230, 669324

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        os.makedirs(os.path.join(self.install, "Data"))
        with open(os.path.join(self.install, "SkyrimSE.exe"), "w") as f:
            f.write("game")
        settings = main._load_settings()
        settings["api_key"] = "k"
        main._save_settings(settings)
        os.makedirs(main.DOWNLOADS_DIR, exist_ok=True)
        shutil.rmtree(main._extract_scratch(self.MOD, self.FILE), ignore_errors=True)
        self.plugin = main.Plugin()

    def tearDown(self):
        shutil.rmtree(self.install, ignore_errors=True)

    def _install(self, members):
        archive = main._archive_cache_path(self.MOD, self.FILE, "p2.zip")
        with zipfile.ZipFile(archive, "w") as z:
            for rel in members:
                z.writestr(rel, "x")
        return run(
            self.plugin.install_mod(
                self.DOMAIN, self.MOD, self.FILE, "p2.zip",
                "SSE Engine Fixes part 2", "1.0", self.GAME, "Data", "", "",
                "dataDir", 489830, "Skyrim Special Edition/Plugins.txt",
                "starred",
            )
        )

    def test_loose_dlls_install_beside_the_game_exe(self):
        result = self._install(["d3dx9_42.dll", "tbb.dll", "tbbmalloc.dll"])
        self.assertTrue(result["ok"], result.get("error"))
        for name in ("d3dx9_42.dll", "tbb.dll", "tbbmalloc.dll"):
            self.assertTrue(
                os.path.isfile(os.path.join(self.install, name)), name
            )
            # Emphatically NOT in Data/, where the game never looks.
            self.assertFalse(
                os.path.isfile(os.path.join(self.install, "Data", name)), name
            )
        rec = main._load_settings()["installed"][self.DOMAIN][
            "SSE Engine Fixes part 2"
        ]
        self.assertEqual(rec["mode"], "files")
        self.assertEqual(rec["target"], ".")

    def test_uninstall_removes_them_from_the_root(self):
        self._install(["d3dx9_42.dll", "tbb.dll"])
        run(
            self.plugin.uninstall_mod(
                self.DOMAIN, self.GAME, "Data", "SSE Engine Fixes part 2",
                "dataDir", 489830, "Skyrim Special Edition/Plugins.txt",
            )
        )
        self.assertFalse(
            os.path.isfile(os.path.join(self.install, "d3dx9_42.dll"))
        )
        self.assertTrue(os.path.isfile(os.path.join(self.install, "SkyrimSE.exe")))

    def test_loose_config_files_still_go_to_data(self):
        # The rule is about binaries only - a config-only mod must keep
        # landing in Data/ (that was its own hard-won fix).
        result = self._install(["MyMod_KID.ini", "swaps.json"])
        self.assertTrue(result["ok"], result.get("error"))
        data = os.path.join(self.install, "Data")
        self.assertTrue(os.path.isfile(os.path.join(data, "MyMod_KID.ini")))
        self.assertFalse(
            os.path.isfile(os.path.join(self.install, "MyMod_KID.ini"))
        )


class TestRepairOnlyInstall(unittest.TestCase):
    """Repair restores files a partial install dropped. It must never
    overwrite: a file already on disk is either this mod's or a LATER
    mod's deliberate override, and re-asserting it would silently undo
    the collection's conflict order."""

    DOMAIN = "skyrimspecialedition"
    GAME = "Repair Test"
    MOD, FILE = 777, 888

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        self.data = os.path.join(self.install, "Data")
        os.makedirs(self.data)
        settings = main._load_settings()
        settings["api_key"] = "k"
        main._save_settings(settings)
        os.makedirs(main.DOWNLOADS_DIR, exist_ok=True)
        shutil.rmtree(main._extract_scratch(self.MOD, self.FILE), ignore_errors=True)
        self.plugin = main.Plugin()

    def tearDown(self):
        shutil.rmtree(self.install, ignore_errors=True)

    def _install(self, repair):
        archive = main._archive_cache_path(self.MOD, self.FILE, "m.zip")
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("Data/Framework.esp", "from-archive")
            z.writestr("Data/textures/shared.dds", "from-archive")
            z.writestr("Data/meshes/only-here.nif", "from-archive")
        return run(
            self.plugin.install_mod(
                self.DOMAIN, self.MOD, self.FILE, "m.zip", "Partial Mod",
                "1.0", self.GAME, "Data", "", "", "dataDir", 489830,
                "Skyrim Special Edition/Plugins.txt", "starred",
                "", "", "", "", None, "", "", False, "", False, False, repair,
            )
        )

    def _write(self, rel, body):
        path = os.path.join(self.data, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body)

    def _read(self, rel):
        with open(os.path.join(self.data, *rel.split("/"))) as f:
            return f.read()

    def test_repair_adds_only_what_is_missing(self):
        # A later mod owns shared.dds; Framework.esp went missing.
        self._write("textures/shared.dds", "from-a-later-mod")
        result = self._install(repair=True)
        self.assertTrue(result["ok"], result.get("error"))
        # Restored.
        self.assertEqual(self._read("Framework.esp"), "from-archive")
        self.assertEqual(self._read("meshes/only-here.nif"), "from-archive")
        # NOT clobbered - this is the whole point.
        self.assertEqual(self._read("textures/shared.dds"), "from-a-later-mod")
        self.assertEqual(result["added"], 2)

    def test_a_normal_install_still_overwrites(self):
        self._write("textures/shared.dds", "from-a-later-mod")
        result = self._install(repair=False)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(self._read("textures/shared.dds"), "from-archive")
        self.assertEqual(result["added"], 3)

    def test_repairing_a_complete_mod_changes_nothing(self):
        self._install(repair=False)
        result = self._install(repair=True)
        self.assertTrue(result["ok"], result.get("error"))
        # 0 added is what the UI reports as "already complete".
        self.assertEqual(result["added"], 0)
        self.assertEqual(self._read("Framework.esp"), "from-archive")

    def test_repair_records_the_whole_mod_not_just_what_it_added(self):
        # Uninstall has to remove everything the mod owns, including the
        # files repair found already present.
        self._write("textures/shared.dds", "from-a-later-mod")
        self._install(repair=True)
        rec = main._load_settings()["installed"][self.DOMAIN]["Partial Mod"]
        self.assertCountEqual(
            rec["files"],
            ["Framework.esp", "textures/shared.dds", "meshes/only-here.nif"],
        )
        self.assertEqual(rec["plugins"], ["Framework.esp"])


class TestFomodDotDestination(unittest.TestCase):
    """A FOMOD destination of "." means the Data root. It produced
    "./file", whose first component the traversal guard rejects, so every
    file of the option was silently dropped - 'Store Entrance Doorbells'
    and 'YASTM' failed this way in Gate To Sovngarde, staging 0 files
    with no error anywhere."""

    def test_dot_destination_normalises_to_the_root(self):
        self.assertEqual(main._fomod_norm_source("."), "")
        self.assertEqual(main._fomod_norm_source("./meshes"), "meshes")
        self.assertEqual(main._fomod_norm_source(".\\meshes"), "meshes")
        self.assertEqual(main._fomod_norm_source("main/./sub"), "main/sub")

    def test_ordinary_paths_are_unchanged(self):
        self.assertEqual(main._fomod_norm_source("meshes/armor"), "meshes/armor")
        self.assertEqual(main._fomod_norm_source("options\\sneak"), "options/sneak")
        self.assertEqual(main._fomod_norm_source("/leading/"), "leading")
        self.assertEqual(main._fomod_norm_source(""), "")

    def test_traversal_is_still_rejected(self):
        # ".." must survive normalisation so the guard still catches it -
        # that is the case the guard exists for.
        self.assertIn("..", main._fomod_norm_source("../../etc/passwd"))
        self.assertFalse(main._safe_rel_path(main._fomod_norm_source("../x")))

    def test_a_dot_destination_stages_its_files(self):
        # End to end through the stager, the shape both failures had.
        base = tempfile.mkdtemp()
        try:
            src = os.path.join(base, "main")
            os.makedirs(os.path.join(src, "meshes"))
            for rel in ("Doorbell.esp", "meshes/bell.nif"):
                p = os.path.join(src, *rel.split("/"))
                with open(p, "w") as f:
                    f.write("x")
            ctx = {
                "fomod_base": base,
                "required": [
                    {"kind": "folder", "source": "main", "dest": ".",
                     "priority": 0}
                ],
                "conditional": [],
                "plugin_index": {},
                "steps": [],
            }
            staging = os.path.join(base, "__staged__")
            os.makedirs(staging)
            self.assertEqual(main._fomod_stage(ctx, [], staging), 2)
            self.assertTrue(
                os.path.isfile(os.path.join(staging, "Doorbell.esp"))
            )
            self.assertTrue(
                os.path.isfile(os.path.join(staging, "meshes", "bell.nif"))
            )
        finally:
            shutil.rmtree(base, ignore_errors=True)


class TestPrepareAheadOfInstall(unittest.TestCase):
    """Extract-ahead: the installer must consume a prepared extraction,
    and must never consume a half-finished one."""

    DOMAIN = "skyrimspecialedition"
    GAME = "Prepare Test"
    MOD, FILE = 4242, 8484

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        os.makedirs(os.path.join(self.install, "Data"))
        settings = main._load_settings()
        settings["api_key"] = "k"
        main._save_settings(settings)
        os.makedirs(main.DOWNLOADS_DIR, exist_ok=True)
        self.archive = main._archive_cache_path(self.MOD, self.FILE, "m.zip")
        with zipfile.ZipFile(self.archive, "w") as z:
            z.writestr("Data/Prepared.esp", "x")
            z.writestr("Data/textures/p.dds", "x")
        self.scratch = main._extract_scratch(self.MOD, self.FILE)
        shutil.rmtree(self.scratch, ignore_errors=True)
        self.plugin = main.Plugin()

    def tearDown(self):
        shutil.rmtree(self.install, ignore_errors=True)
        shutil.rmtree(self.scratch, ignore_errors=True)

    def _install(self):
        return run(
            self.plugin.install_mod(
                self.DOMAIN, self.MOD, self.FILE, "m.zip", "Prepared Mod",
                "1.0", self.GAME, "Data", "", "", "dataDir", 489830,
                "Skyrim Special Edition/Plugins.txt", "starred",
            )
        )

    def test_prepare_then_install_uses_the_prepared_extraction(self):
        prep = run(
            self.plugin.prepare_mod_file(self.DOMAIN, self.MOD, self.FILE, "m.zip")
        )
        self.assertTrue(prep["ok"], prep.get("error"))
        self.assertTrue(os.path.isfile(self.scratch + main.PREPARED_MARKER))
        result = self._install()
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(
            os.path.isfile(os.path.join(self.install, "Data", "Prepared.esp"))
        )
        # Consumed: the marker is gone and the scratch cleaned up, so a
        # later install of the same file cannot pick up a stale tree.
        self.assertFalse(os.path.isfile(self.scratch + main.PREPARED_MARKER))
        self.assertFalse(os.path.isdir(self.scratch))

    def test_install_without_prepare_still_works(self):
        # extract_ahead = 0, or the prepare lost the race: the installer
        # does the extraction itself exactly as before.
        result = self._install()
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(
            os.path.isfile(os.path.join(self.install, "Data", "Prepared.esp"))
        )

    def test_a_half_finished_extraction_is_not_treated_as_prepared(self):
        # Scratch exists with partial content but NO marker (interrupted
        # extract). Installing must redo it rather than ship half a mod.
        os.makedirs(os.path.join(self.scratch, "Data"), exist_ok=True)
        with open(os.path.join(self.scratch, "Data", "Partial.esp"), "w") as f:
            f.write("half")
        result = self._install()
        self.assertTrue(result["ok"], result.get("error"))
        data = os.path.join(self.install, "Data")
        self.assertTrue(os.path.isfile(os.path.join(data, "Prepared.esp")))
        # The debris from the interrupted attempt never reached the game.
        self.assertFalse(os.path.isfile(os.path.join(data, "Partial.esp")))

    def test_preparing_twice_is_cheap_and_idempotent(self):
        first = run(
            self.plugin.prepare_mod_file(self.DOMAIN, self.MOD, self.FILE, "m.zip")
        )
        second = run(
            self.plugin.prepare_mod_file(self.DOMAIN, self.MOD, self.FILE, "m.zip")
        )
        self.assertTrue(first["ok"] and second["ok"])
        self.assertTrue(os.path.isfile(self.scratch + main.PREPARED_MARKER))


class TestDataDirInstallEndToEnd(unittest.TestCase):
    """The Skyrim-class path, exercised for real: archive -> extract ->
    case-merged move into Data/ -> per-file record -> plugins.txt. The
    merge runs in a worker thread (so downloads keep flowing during it)
    and shares one directory-listing cache across the mod's files - both
    are invisible to unit tests of the helpers, so the whole path gets a
    test of its own."""

    DOMAIN = "skyrimspecialedition"
    GAME = "Skyrim Merge Test"
    APP_ID = 489830
    PLUGINS = "Skyrim Special Edition/Plugins.txt"

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        self.data = os.path.join(self.install, "Data")
        # Pre-existing capitalised dir: the archive's lowercase spelling
        # must merge INTO it, not create a twin beside it.
        os.makedirs(os.path.join(self.data, "Textures", "armor"))
        settings = main._load_settings()
        settings["api_key"] = "k"
        main._save_settings(settings)
        plugins_path = main._plugins_txt_path(self.APP_ID, self.PLUGINS)
        shutil.rmtree(os.path.dirname(plugins_path), ignore_errors=True)
        self.plugins_path = plugins_path
        self.plugin = main.Plugin()

    def tearDown(self):
        shutil.rmtree(self.install, ignore_errors=True)

    def _install(self, name, members, mod_id=1, file_id=1):
        os.makedirs(main.DOWNLOADS_DIR, exist_ok=True)
        archive = main._archive_cache_path(mod_id, file_id, "mod.zip")
        with zipfile.ZipFile(archive, "w") as z:
            for rel in members:
                z.writestr(rel, "x")
        return run(
            self.plugin.install_mod(
                self.DOMAIN, mod_id, file_id, "mod.zip", name, "1.0",
                self.GAME, "Data", "", "", "dataDir", self.APP_ID,
                self.PLUGINS, "starred",
            )
        )

    def test_payload_merges_into_data_with_existing_casing(self):
        result = self._install(
            "Cool Armour",
            [
                "Data/CoolArmour.esp",
                "Data/textures/armor/cool.dds",
                "Data/meshes/armor/cool.nif",
            ],
        )
        self.assertTrue(result["ok"], result.get("error"))
        # Merged into the EXISTING Textures/, no lowercase twin.
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.data, "Textures", "armor", "cool.dds")
            )
        )
        self.assertNotIn("textures", os.listdir(self.data))
        self.assertTrue(os.path.isfile(os.path.join(self.data, "CoolArmour.esp")))
        self.assertTrue(
            os.path.isfile(os.path.join(self.data, "meshes", "armor", "cool.nif"))
        )

    def test_record_lists_every_file_and_activates_the_plugin(self):
        self._install(
            "Cool Armour",
            ["Data/CoolArmour.esp", "Data/textures/armor/cool.dds"],
        )
        rec = main._load_settings()["installed"][self.DOMAIN]["Cool Armour"]
        self.assertEqual(rec["mode"], "dataDir")
        self.assertEqual(rec["plugins"], ["CoolArmour.esp"])
        self.assertCountEqual(
            rec["files"], ["CoolArmour.esp", "Textures/armor/cool.dds"]
        )
        with open(self.plugins_path, encoding="utf-8") as f:
            self.assertIn("*CoolArmour.esp", f.read())

    def test_two_mods_naming_a_new_dir_differently_share_one_dir(self):
        # The cache remembers the spelling chosen for a directory this
        # install created; the second mod then adopts it from disk.
        self._install("First", ["Data/SKSE/Plugins/a.dll"], 1, 1)
        self._install("Second", ["Data/skse/plugins/b.dll"], 2, 2)
        skse = [d for d in os.listdir(self.data) if d.lower() == "skse"]
        self.assertEqual(skse, ["SKSE"])
        plugins_dir = os.path.join(self.data, "SKSE", "Plugins")
        self.assertCountEqual(os.listdir(plugins_dir), ["a.dll", "b.dll"])

    def test_one_mod_naming_a_new_dir_both_ways_lands_in_one_dir(self):
        self._install(
            "Mixed Case",
            ["Data/Scripts/Source/a.psc", "Data/scripts/source/b.psc"],
        )
        scripts = [d for d in os.listdir(self.data) if d.lower() == "scripts"]
        self.assertEqual(scripts, ["Scripts"])
        self.assertCountEqual(
            os.listdir(os.path.join(self.data, "Scripts", "Source")),
            ["a.psc", "b.psc"],
        )

    def test_uninstall_removes_exactly_what_the_record_lists(self):
        self._install(
            "Cool Armour",
            ["Data/CoolArmour.esp", "Data/textures/armor/cool.dds"],
        )
        result = run(
            self.plugin.uninstall_mod(
                self.DOMAIN, self.GAME, "Data", "Cool Armour", "dataDir",
                self.APP_ID, self.PLUGINS, "starred",
            )
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertFalse(os.path.isfile(os.path.join(self.data, "CoolArmour.esp")))
        self.assertFalse(
            os.path.isfile(
                os.path.join(self.data, "Textures", "armor", "cool.dds")
            )
        )


class TestIniPatchFidelity(unittest.TestCase):
    """These are Windows programs' config files inside a Proton prefix.
    Setting one key rewrote every line of Seamless Co-op's CRLF ini as LF
    (device, 2026-08-07) - a whole-file change we were never asked for."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "ersc_settings.ini")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, text):
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def _raw(self):
        with open(self.path, encoding="utf-8", newline="") as f:
            return f.read()

    def test_crlf_files_stay_crlf(self):
        self._write(
            "[PASSWORD]\r\n\r\n; Session password\r\ncooppassword = \r\n"
        )
        main._patch_ini_settings(self.path, "PASSWORD", {"cooppassword": "hunter2"})
        raw = self._raw()
        self.assertNotIn("\n", raw.replace("\r\n", ""))
        self.assertEqual(raw.count("\r\n"), 4)
        self.assertIn("cooppassword = hunter2", raw)

    def test_lf_files_stay_lf(self):
        self._write("[Archive]\nbInvalidateOlderFiles=0\n")
        main._patch_ini_settings(
            self.path, "Archive", {"bInvalidateOlderFiles": "1"}
        )
        raw = self._raw()
        self.assertNotIn("\r", raw)
        self.assertIn("bInvalidateOlderFiles=1", raw)

    def test_spacing_around_equals_is_the_files_own(self):
        self._write("[S]\r\nspaced = old\r\ntight=old\r\n")
        main._patch_ini_settings(self.path, "S", {"spaced": "new", "tight": "new"})
        raw = self._raw()
        self.assertIn("spaced = new", raw)
        self.assertIn("tight=new", raw)

    def test_a_file_with_no_trailing_newline_does_not_gain_one(self):
        self._write("[S]\r\nkey = old")
        main._patch_ini_settings(self.path, "S", {"key": "new"})
        self.assertEqual(self._raw(), "[S]\r\nkey = new")

    def test_only_the_targeted_line_changes(self):
        original = (
            "[GAMEPLAY]\r\n\r\n; a comment\r\nallow_invaders = 1\r\n\r\n"
            "[PASSWORD]\r\ncooppassword = \r\n\r\n[SAVE]\r\n"
            "save_file_extension = co2\r\n"
        )
        self._write(original)
        main._patch_ini_settings(self.path, "PASSWORD", {"cooppassword": "pw"})
        before = original.split("\r\n")
        after = self._raw().split("\r\n")
        self.assertEqual(len(before), len(after))
        differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(differing), 1, f"changed lines: {differing}")


class TestResetToVanillaRegressions(unittest.TestCase):
    """Reset has regressed more than once - SMAPI surviving it, dlo still
    replaying a deleted SKSE loader, the me3 loader staying installed. It
    is the one action a user reaches for when everything is already
    broken, so every install mode gets pinned here."""

    GAME = "Reset Test Game"

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        os.makedirs(self.install)
        shutil.rmtree(main.ME3_ROOT, ignore_errors=True)
        self.plugin = main.Plugin()

    def tearDown(self):
        shutil.rmtree(self.install, ignore_errors=True)
        shutil.rmtree(main.ME3_ROOT, ignore_errors=True)

    def _mk(self, rel, body="x"):
        path = os.path.join(self.install, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body)
        return path

    def _records(self, domain, records):
        settings = main._load_settings()
        settings.setdefault("installed", {})[domain] = records
        main._save_settings(settings)

    def _has(self, rel):
        return os.path.exists(os.path.join(self.install, *rel.split("/")))

    def _reset(self, domain, mods_subdir="mods", **kw):
        return run(
            self.plugin.reset_game_modding(
                domain, self.GAME, mods_subdir,
                kw.get("install_mode", "folder"),
                kw.get("app_id", 0),
                kw.get("plugins_subpath", ""),
                kw.get("plugins_style", "starred"),
                kw.get("framework_file_prefixes", []),
                kw.get("witcher_layout", False),
                kw.get("framework_mod_folders", []),
            )
        )

    # -- folder mode ------------------------------------------------------

    def test_folder_mode_removes_enabled_and_disabled_mods(self):
        self._mk("mods/ModA/file.txt")
        self._mk("mods-disabled/ModB/file.txt")
        self._mk("mods/Untracked/file.txt")
        self._records("testgame", {"ModA": {"name": "A"}, "ModB": {"name": "B"}})
        result = self._reset("testgame")
        self.assertTrue(result["ok"])
        self.assertEqual(result["removed"], 2)
        self.assertFalse(self._has("mods/ModA"))
        self.assertFalse(self._has("mods-disabled/ModB"))
        # Not ours to delete - the dialog promises as much.
        self.assertTrue(self._has("mods/Untracked"))

    # -- dataDir mode -----------------------------------------------------

    def test_datadir_mode_removes_manifest_files_and_the_plugins_file(self):
        self._mk("Data/meshes/thing.nif")
        self._mk("Data/Cool.esp")
        self._mk("Data/Vanilla.esm")
        plugins = main._plugins_txt_path(489830, "Skyrim/Plugins.txt")
        os.makedirs(os.path.dirname(plugins), exist_ok=True)
        with open(plugins, "w") as f:
            f.write("*Cool.esp\n")
        self._records(
            "skyrimtest",
            {
                "Cool Mod": {
                    "name": "Cool Mod",
                    "mode": "dataDir",
                    "files": ["meshes/thing.nif", "Cool.esp"],
                    "plugins": ["Cool.esp"],
                }
            },
        )
        result = self._reset(
            "skyrimtest", "Data", install_mode="dataDir", app_id=489830,
            plugins_subpath="Skyrim/Plugins.txt",
        )
        self.assertTrue(result["ok"])
        self.assertFalse(self._has("Data/Cool.esp"))
        self.assertFalse(self._has("Data/meshes/thing.nif"))
        self.assertTrue(self._has("Data/Vanilla.esm"))  # the game's own
        self.assertFalse(os.path.isfile(plugins))  # game regenerates it

    # -- files mode -------------------------------------------------------

    def test_files_mode_removes_exactly_the_recorded_files(self):
        self._mk("archive/pc/mod/cool.archive")
        self._mk("archive/pc/mod/other.archive")
        self._records(
            "cp77test",
            {
                "Cool": {
                    "name": "Cool",
                    "mode": "files",
                    "target": ".",
                    "files": ["archive/pc/mod/cool.archive"],
                }
            },
        )
        self._reset("cp77test", "archive/pc/mod")
        self.assertFalse(self._has("archive/pc/mod/cool.archive"))
        self.assertTrue(self._has("archive/pc/mod/other.archive"))

    # -- me3 mode ---------------------------------------------------------

    def _seed_me3(self, domain, mod="Some Mod"):
        os.makedirs(os.path.join(main.ME3_ROOT, "bin"), exist_ok=True)
        with open(main.ME3_BIN, "w") as f:
            f.write("#!/bin/sh\n")
        folder = os.path.join(main._me3_mods_dir(domain), mod)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "regulation.bin"), "w") as f:
            f.write("x")
        settings = main._load_settings()
        settings.setdefault("installed", {}).setdefault(domain, {})[mod] = {
            "name": mod, "mode": "me3", "folder": mod, "package": True,
            "natives": [], "enabled": True, "installed_at": 1,
        }
        main._save_settings(settings)
        main._write_me3_profile(domain, main._load_settings())

    def test_me3_reset_removes_the_profile_and_the_loader(self):
        self._seed_me3("eldenring")
        self.assertTrue(os.path.isfile(main.ME3_BIN))
        result = self._reset("eldenring", "._nexus_mods_unused", install_mode="me3")
        self.assertTrue(result["ok"])
        self.assertFalse(os.path.exists(main._me3_profile_dir("eldenring")))
        # Leaving it behind is what made a working reset look like a no-op:
        # the setup step kept reporting the loader as installed.
        self.assertFalse(os.path.exists(main.ME3_BIN))
        self.assertIn("me3 (mod loader)", result["framework_files"])

    def test_me3_loader_survives_while_another_fromsoft_game_uses_it(self):
        self._seed_me3("eldenring")
        self._seed_me3("darksouls3")
        self._reset("eldenring", "._nexus_mods_unused", install_mode="me3")
        self.assertFalse(os.path.exists(main._me3_profile_dir("eldenring")))
        # Resetting one game must not break another that is still modded.
        self.assertTrue(os.path.isfile(main.ME3_BIN))
        self.assertTrue(os.path.exists(main._me3_profile_dir("darksouls3")))

    def test_me3_reset_never_touches_the_game_folder(self):
        self._mk("Game/eldenring.exe")
        self._mk("Game/start_protected_game.exe")
        self._seed_me3("eldenring")
        self._reset("eldenring", "._nexus_mods_unused", install_mode="me3")
        self.assertTrue(self._has("Game/eldenring.exe"))
        self.assertTrue(self._has("Game/start_protected_game.exe"))

    # -- shared behaviour -------------------------------------------------

    def test_every_state_section_for_the_game_is_cleared(self):
        settings = main._load_settings()
        for section in ("installed", "collections", "framework_setup",
                        "collection_attention", "w3_merges"):
            settings.setdefault(section, {})["testgame"] = {"x": {"name": "x"}}
            settings[section]["othergame"] = {"keep": {"name": "keep"}}
        main._save_settings(settings)
        self._reset("testgame")
        after = main._load_settings()
        for section in ("installed", "collections", "framework_setup",
                        "collection_attention", "w3_merges"):
            self.assertNotIn("testgame", after.get(section, {}), section)
            # Another game's state is none of this reset's business.
            self.assertIn("othergame", after.get(section, {}), section)

    def test_launch_command_is_cleared_from_the_launch_options_plugin(self):
        # Regression: after a Skyrim reset the game would not boot because
        # dlo still replayed a launch command pointing at the deleted SKSE
        # loader - Steam's own field never held it.
        dlo = main._dlo_settings_path()
        os.makedirs(os.path.dirname(dlo), exist_ok=True)
        with open(dlo, "w") as f:
            json.dump(
                {"profiles": {"489830": {
                    "state": {},
                    "originalLaunchOptions": "bash -c 'exec skse64_loader.exe'",
                }}},
                f,
            )
        try:
            result = self._reset("testgame", app_id=489830)
            self.assertTrue(result["cleared_dlo"])
            self.assertFalse(result["use_steam_client"])
            self.assertEqual(main._dlo_get_original(dlo, 489830), "")
        finally:
            os.remove(dlo)

    def test_missing_game_folder_is_an_error_not_a_silent_success(self):
        shutil.rmtree(self.install)
        result = self._reset("testgame")
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])

    def test_a_bad_game_domain_is_refused(self):
        result = self._reset("../../etc")
        self.assertFalse(result["ok"])


class TestResetRemovesTheFramework(unittest.TestCase):
    """Reset-to-vanilla left SMAPI installed (reported on device): it only
    deleted game-root FILES, so smapi-internal/ and the bundled mods
    survived and the setup step stayed ticked. Layout mirrors a real
    SMAPI 4.5.2 install."""

    DOMAIN = "stardewvalley"
    GAME = "Stardew Valley"

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.root, ignore_errors=True)
        for rel in (
            "StardewModdingAPI",
            "StardewModdingAPI.dll",
            "StardewModdingAPI.deps.json",
            "StardewModdingAPI.runtimeconfig.json",
            "StardewModdingAPI.xml",
            "smapi-internal/MonoMod.dll",
            "Mods/ConsoleCommands/manifest.json",
            "Mods/SaveBackup/manifest.json",
            "Mods/PlayerMod/manifest.json",
            # Game files that must survive, plus the save backups SMAPI
            # writes - the reset dialog promises saves aren't touched.
            "Stardew Valley",
            "Stardew Valley.dll",
            "StardewValley",
            "Content/data.xnb",
            "save-backups/2026-08-06 backup.zip",
        ):
            path = os.path.join(self.root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("x")
        settings = main._load_settings()
        settings.setdefault("installed", {})[self.DOMAIN] = {
            "PlayerMod": {"mod_id": 1, "name": "Player Mod"}
        }
        main._save_settings(settings)
        self.plugin = main.Plugin()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _reset(self):
        return run(
            self.plugin.reset_game_modding(
                self.DOMAIN, self.GAME, "Mods", "folder", 413150, "", "starred",
                ["StardewModdingAPI", "smapi-internal"], False,
                ["SaveBackup", "ConsoleCommands", "ErrorHandler"],
            )
        )

    def _exists(self, rel):
        return os.path.exists(os.path.join(self.root, *rel.split("/")))

    def test_loader_files_and_directories_both_go(self):
        result = self._reset()
        self.assertTrue(result["ok"])
        self.assertFalse(self._exists("StardewModdingAPI"))
        self.assertFalse(self._exists("StardewModdingAPI.dll"))
        self.assertFalse(self._exists("StardewModdingAPI.xml"))
        # The directory is what the old file-only sweep left behind.
        self.assertFalse(self._exists("smapi-internal"))

    def test_bundled_mods_go_but_the_players_mods_are_recorded_removals(self):
        self._reset()
        self.assertFalse(self._exists("Mods/ConsoleCommands"))
        self.assertFalse(self._exists("Mods/SaveBackup"))
        self.assertFalse(self._exists("Mods/PlayerMod"))

    def test_game_files_and_save_backups_survive(self):
        self._reset()
        self.assertTrue(self._exists("Stardew Valley"))
        self.assertTrue(self._exists("Stardew Valley.dll"))
        self.assertTrue(self._exists("StardewValley"))
        self.assertTrue(self._exists("Content/data.xnb"))
        # Saves are not touched - the confirm dialog says so.
        self.assertTrue(self._exists("save-backups/2026-08-06 backup.zip"))

    def test_reset_reports_what_it_removed(self):
        result = self._reset()
        self.assertIn("smapi-internal", result["framework_files"])
        self.assertIn("Mods/SaveBackup", result["framework_files"])
        self.assertEqual(result["errors"], [])


class TestMe3Layout(unittest.TestCase):
    """FromSoft tier. The promises that got this tier approved are code,
    not conventions: the generated profile can never put a modded session
    online, and it always redirects the save file."""

    DOMAIN = "eldenring"
    GAME = "ELDEN RING"

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.root = os.path.join(main.STEAM_COMMON, self.GAME)
        os.makedirs(self.root, exist_ok=True)
        shutil.rmtree(main._me3_profile_dir(self.DOMAIN), ignore_errors=True)
        self.plugin = main.Plugin()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(main._me3_profile_dir(self.DOMAIN), ignore_errors=True)

    # -- payload routing ------------------------------------------------

    def _scratch(self, files):
        scratch = os.path.join(TEST_ROOT, "me3-scratch")
        shutil.rmtree(scratch, ignore_errors=True)
        for rel in files:
            path = os.path.join(scratch, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"x")
        return scratch

    def test_asset_mod_is_a_package(self):
        scratch = self._scratch(["regulation.bin", "parts/wp_a_0000.partsbnd"])
        root, assets, dlls, err = main._route_me3_payload(scratch, "Reforged")
        self.assertIsNone(err)
        self.assertEqual(assets, "")  # assets sit at the payload root
        self.assertEqual(dlls, [])
        self.assertEqual(root, scratch)

    def test_dll_mod_is_a_native(self):
        scratch = self._scratch(["SeamlessCoop/ersc.dll",
                                 "SeamlessCoop/ersc_settings.ini"])
        root, assets, dlls, err = main._route_me3_payload(scratch, "Seamless")
        self.assertIsNone(err)
        self.assertIsNone(assets)
        self.assertEqual(dlls, ["ersc.dll"])
        # The wrapper folder is unwrapped so the dll path stays shallow.
        self.assertTrue(root.endswith("SeamlessCoop"))

    def test_me3s_own_layout_keeps_both_halves(self):
        # The layout me3's docs recommend: assets in mod/, dll in
        # natives/. Reading only the root would install half the mod.
        scratch = self._scratch([
            "TheMod/mod/regulation.bin",
            "TheMod/mod/parts/a.partsbnd",
            "TheMod/natives/hook.dll",
            "TheMod/themod.me3",
        ])
        _root, assets, dlls, err = main._route_me3_payload(scratch, "The Mod")
        self.assertIsNone(err)
        self.assertEqual(assets, "mod")
        self.assertEqual(dlls, ["natives/hook.dll"])

    def test_dlls_inside_asset_content_are_not_force_loaded(self):
        # A dll shipped as overridden game data is not a mod host -
        # loading it as a native crashes the game.
        scratch = self._scratch(["regulation.bin", "sfx/embedded.dll",
                                 "sfx/sound.bnk"])
        _root, assets, dlls, err = main._route_me3_payload(scratch, "FX Mod")
        self.assertIsNone(err)
        self.assertEqual(assets, "")
        self.assertEqual(dlls, [])

    def test_natives_survive_an_unwrappable_wrapper(self):
        # Too many top-level entries to unwrap, so the asset path is
        # 'MyMod v1.2/mod'. Blacklisting its first component would take
        # the sibling natives/ with it and half-install the mod.
        scratch = self._scratch([
            "MyMod v1.2/mod/regulation.bin",
            "MyMod v1.2/natives/hook.dll",
            "README.txt",
            "Changelog.txt",
            "screenshot.jpg",
        ])
        _root, assets, dlls, err = main._route_me3_payload(scratch, "My Mod")
        self.assertIsNone(err)
        self.assertEqual(assets, "MyMod v1.2/mod")
        self.assertEqual(dlls, ["MyMod v1.2/natives/hook.dll"])

    def test_a_dll_in_a_marker_named_folder_is_still_a_dll_mod(self):
        # 'script' is a DVDBND root name, but a folder holding nothing
        # but a dll is a mod host, not overridden game data.
        scratch = self._scratch(["script/mymod.dll"])
        _root, assets, dlls, err = main._route_me3_payload(scratch, "Script Mod")
        self.assertIsNone(err)
        self.assertIsNone(assets)
        self.assertEqual(dlls, ["script/mymod.dll"])

    def test_option_packs_are_refused_rather_than_double_loaded(self):
        # Two copies of one early-load native crashes the game, and
        # picking a variant for the user is a guess.
        scratch = self._scratch(["Full version/ersc.dll",
                                 "Lite version/ersc.dll"])
        _root, _a, _d, err = main._route_me3_payload(scratch, "Seamless")
        self.assertIsNotNone(err)
        self.assertEqual(err[0], "choice")
        self.assertIn("ersc.dll", err[1])

    def test_backup_folder_does_not_beat_the_real_payload(self):
        scratch = self._scratch(["_old/parts/stale.bnd",
                                 "mod/regulation.bin"])
        _root, assets, _d, err = main._route_me3_payload(scratch, "My Mod")
        self.assertIsNone(err)
        self.assertEqual(assets, "mod")

    def test_a_stray_enabled_txt_does_not_route_to_the_ue4ss_refusal(self):
        # The UE4SS gate fires on enabled.txt anywhere in the archive;
        # an me3 game must reach its own branch first.
        result = self._install("Marked Mod", ["regulation.bin", "enabled.txt"])
        self.assertTrue(result["ok"], result.get("error"))

    def test_windows_tool_is_refused_not_installed(self):
        scratch = self._scratch(["ModEngine2/modengine2_launcher.exe",
                                 "ModEngine2/readme.txt"])
        _root, _a, _d, err = main._route_me3_payload(scratch, "Mod Engine 2")
        self.assertIsNotNone(err)
        self.assertEqual(err[0], "tool")

    def test_unrecognizable_archive_reports_contents(self):
        scratch = self._scratch(["notes.txt", "screenshot.png"])
        _root, _a, _d, err = main._route_me3_payload(scratch, "Whatever")
        self.assertEqual(err[0], "layout")
        self.assertIn("notes.txt", err[1])

    # -- profile writer -------------------------------------------------

    def _record(self, key, **over):
        settings = main._load_settings()
        rec = {
            "mod_id": 1,
            "file_id": 1,
            "name": key,
            "installed_at": over.pop("installed_at", 1000),
            "mode": "me3",
            "folder": key,
            "package": True,
            "natives": [],
            "regulation": False,
            "enabled": True,
        }
        rec.update(over)
        settings.setdefault("installed", {}).setdefault(self.DOMAIN, {})[key] = rec
        main._save_settings(settings)
        return settings

    def test_profile_never_goes_online_and_always_isolates_saves(self):
        settings = self._record("Reforged", regulation=True)
        path = main._write_me3_profile(self.DOMAIN, settings)
        with open(path, encoding="utf-8") as f:
            body = f.read()
        # The two promises, asserted directly.
        self.assertNotIn("start_online", body)
        self.assertIn(f"savefile = \"{main.ME3_SAVEFILE}\"", body)
        self.assertIn('profileVersion = "v1"', body)
        self.assertIn('game = "eldenring"', body)
        self.assertIn('path = "mods/Reforged"', body)

    def test_natives_are_declared_with_a_path_and_nothing_else(self):
        # Regression: adding load_early + initializer for Seamless Co-op
        # crashed Elden Ring ~8s into every launch. me3 recognises
        # ModEngine2-style natives itself ("loaded native with me2
        # compatibility shim"), so our initializer ran a second time on
        # top of me3's. Loading is me3's call, not ours.
        settings = self._record(
            "Seamless Coop", package=False, natives=["ersc.dll"]
        )
        with open(main._write_me3_profile(self.DOMAIN, settings)) as f:
            body = f.read()
        self.assertIn('path = "mods/Seamless Coop/ersc.dll"', body)
        self.assertNotIn("load_early", body)
        self.assertNotIn("initializer", body)
        self.assertNotIn("modengine_ext_init", body)
        # dll-only mods contribute no package entry
        self.assertNotIn("[[packages]]", body)

    def test_disabled_mod_stays_listed_but_off(self):
        settings = self._record("Reforged", enabled=False)
        with open(main._write_me3_profile(self.DOMAIN, settings)) as f:
            body = f.read()
        self.assertIn("[[packages]]", body)
        self.assertIn("enabled = false", body)

    def test_profile_order_follows_install_order(self):
        self._record("First", installed_at=100)
        settings = self._record("Second", installed_at=200)
        with open(main._write_me3_profile(self.DOMAIN, settings)) as f:
            body = f.read()
        self.assertLess(body.index("mods/First"), body.index("mods/Second"))

    def test_quotes_in_a_mod_name_cannot_break_the_toml(self):
        settings = self._record('Weird" Mod', folder='Weird" Mod')
        with open(main._write_me3_profile(self.DOMAIN, settings)) as f:
            body = f.read()
        self.assertIn('path = "mods/Weird\\" Mod"', body)

    # -- install / toggle / uninstall ------------------------------------

    def _install(self, mod_name, files, mod_id=1, file_id=1):
        """Full install path with the archive pre-seeded in the cache, so
        no network is touched (the aiohttp stub would raise)."""
        settings = main._load_settings()
        settings["api_key"] = "k"
        main._save_settings(settings)
        os.makedirs(main.DOWNLOADS_DIR, exist_ok=True)
        archive = main._archive_cache_path(mod_id, file_id, "mod.zip")
        with zipfile.ZipFile(archive, "w") as z:
            for rel in files:
                z.writestr(rel, "x")
        return run(
            self.plugin.install_mod(
                self.DOMAIN, mod_id, file_id, "mod.zip", mod_name, "1.0",
                self.GAME, "._nexus_mods_unused", "", "", "me3", 1245620,
            )
        )

    def _profile_body(self):
        with open(main._me3_profile_path(self.DOMAIN), encoding="utf-8") as f:
            return f.read()

    def test_package_path_points_at_the_asset_subfolder(self):
        result = self._install(
            "The Mod",
            ["TheMod/mod/regulation.bin", "TheMod/natives/hook.dll"],
        )
        self.assertTrue(result["ok"], result.get("error"))
        body = self._profile_body()
        self.assertIn('path = "mods/The Mod/mod"', body)
        self.assertIn('path = "mods/The Mod/natives/hook.dll"', body)

    def test_install_lands_outside_the_game_folder(self):
        result = self._install("Reforged", ["regulation.bin", "parts/a.bnd"])
        self.assertTrue(result["ok"], result.get("error"))
        installed = os.path.join(
            main._me3_mods_dir(self.DOMAIN), "Reforged", "regulation.bin"
        )
        self.assertTrue(os.path.isfile(installed))
        # The deal: the game install is never written to.
        self.assertEqual(os.listdir(self.root), [])
        self.assertIn('path = "mods/Reforged"', self._profile_body())

    def test_second_regulation_bin_is_refused_by_name(self):
        self._install("Reforged", ["regulation.bin"], mod_id=1, file_id=1)
        result = self._install("Convergence", ["regulation.bin"], 2, 2)
        self.assertFalse(result["ok"])
        # A conflict, not a bad archive: collections must park it as
        # resolvable, not as permanently unsupported.
        self.assertTrue(result["mod_conflict"])
        self.assertNotIn("unsupported_layout", result)
        self.assertIn("Reforged", result["error"])
        # The loser leaves nothing behind.
        self.assertNotIn(
            "Convergence", os.listdir(main._me3_mods_dir(self.DOMAIN))
        )

    def test_regulation_clash_clears_once_the_owner_is_disabled(self):
        self._install("Reforged", ["regulation.bin"], 1, 1)
        run(
            self.plugin.set_mod_enabled(
                self.GAME, "._nexus_mods_unused", "Reforged", False,
                "me3", self.DOMAIN,
            )
        )
        result = self._install("Convergence", ["regulation.bin"], 2, 2)
        self.assertTrue(result["ok"], result.get("error"))

    def test_toggle_rewrites_the_profile_without_moving_files(self):
        self._install("Reforged", ["regulation.bin"])
        folder = os.path.join(main._me3_mods_dir(self.DOMAIN), "Reforged")
        run(
            self.plugin.set_mod_enabled(
                self.GAME, "._nexus_mods_unused", "Reforged", False,
                "me3", self.DOMAIN,
            )
        )
        self.assertTrue(os.path.isdir(folder))  # nothing moved
        self.assertIn("enabled = false", self._profile_body())
        mods = run(
            self.plugin.get_installed_mods(
                self.DOMAIN, self.GAME, "._nexus_mods_unused", "me3"
            )
        )["mods"]
        self.assertEqual([m["enabled"] for m in mods], [False])
        self.assertTrue(mods[0]["togglable"])

    def test_uninstall_removes_the_folder_and_the_profile_entry(self):
        self._install("Reforged", ["regulation.bin"])
        result = run(
            self.plugin.uninstall_mod(
                self.DOMAIN, self.GAME, "._nexus_mods_unused", "Reforged",
                "me3",
            )
        )
        self.assertTrue(result["ok"])
        self.assertFalse(
            os.path.isdir(
                os.path.join(main._me3_mods_dir(self.DOMAIN), "Reforged")
            )
        )
        self.assertNotIn("[[packages]]", self._profile_body())

    def test_reset_removes_the_whole_me3_tree(self):
        self._install("Reforged", ["regulation.bin"])
        result = run(
            self.plugin.reset_game_modding(
                self.DOMAIN, self.GAME, "._nexus_mods_unused", "me3", 1245620,
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["removed"], 1)
        # A stale profile would still be named by the launch command.
        self.assertFalse(os.path.exists(main._me3_profile_dir(self.DOMAIN)))

    def test_enable_all_says_which_mod_it_left_off(self):
        self._install("Reforged", ["regulation.bin"], 1, 1)
        run(
            self.plugin.set_mod_enabled(
                self.GAME, "._nexus_mods_unused", "Reforged", False,
                "me3", self.DOMAIN,
            )
        )
        self._install("Convergence", ["regulation.bin"], 2, 2)
        result = run(
            self.plugin.set_all_mods_enabled(
                self.GAME, "._nexus_mods_unused", True, "me3", self.DOMAIN
            )
        )
        self.assertTrue(result["ok"])
        # One regulation owner enabled, the other explained - not silent.
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("regulation.bin", result["errors"][0])
        self.assertEqual(self._profile_body().count("enabled = false"), 1)

    def test_reset_never_deletes_inside_the_game_folder(self):
        # A record shape we never write, but reset's folder fallback
        # would resolve it against the game dir and delete there.
        stray = os.path.join(self.root, "Game", "Legacy")
        os.makedirs(stray)
        settings = main._load_settings()
        settings.setdefault("installed", {}).setdefault(self.DOMAIN, {})[
            "Legacy"
        ] = {"name": "Legacy", "folder": "Legacy", "target": "Game"}
        main._save_settings(settings)
        run(
            self.plugin.reset_game_modding(
                self.DOMAIN, self.GAME, "._nexus_mods_unused", "me3", 1245620,
            )
        )
        self.assertTrue(os.path.isdir(stray))
        self.assertNotIn(
            "Legacy",
            main._load_settings().get("installed", {}).get(self.DOMAIN, {}),
        )

    # -- Proton preflight -------------------------------------------------

    def test_compat_tool_prefers_the_app_then_the_global_default(self):
        config = os.path.join(
            main.decky.DECKY_USER_HOME, ".steam", "steam", "config", "config.vdf"
        )
        os.makedirs(os.path.dirname(config), exist_ok=True)
        with open(config, "w", encoding="utf-8") as f:
            f.write(
                '"InstallConfigStore"\n{\n "Software"\n {\n'
                '  "CompatToolMapping"\n  {\n'
                '   "0"\n   {\n    "name"  "proton_experimental"\n'
                '    "config"  ""\n   }\n'
                '   "1245620"\n   {\n    "name"  "proton_9"\n'
                '    "config"  ""\n   }\n  }\n }\n}\n'
            )
        try:
            self.assertEqual(main._steam_compat_tool(1245620), "proton_9")
            # An unmapped app inherits Steam's global default.
            self.assertEqual(main._steam_compat_tool(999999), "proton_experimental")
        finally:
            os.remove(config)

    def test_compat_tool_is_empty_when_steam_wrote_nothing(self):
        # The real state on the test device: a Verified game running on an
        # implicit default, so me3 has to fall back to Proton 8.0.
        self.assertEqual(main._steam_compat_tool(1245620), "")

    # -- launch command --------------------------------------------------

    def test_launch_command_wraps_steam_and_stays_offline(self):
        result = run(self.plugin.get_me3_launch_command(self.DOMAIN))
        self.assertTrue(result["ok"])
        command = result["command"]
        # Steam only treats the string as a wrapper when %command% is there.
        self.assertIn("%command%", command)
        self.assertIn(main.ME3_BIN, command)
        self.assertIn("--windows-binaries-dir", command)
        self.assertIn(main._me3_profile_path(self.DOMAIN), command)
        self.assertNotIn("--online", command)
        # Writing the command also (re)generates the profile it names.
        self.assertTrue(os.path.isfile(main._me3_profile_path(self.DOMAIN)))

    def test_launch_command_refuses_a_game_me3_cannot_load(self):
        result = run(self.plugin.get_me3_launch_command("skyrimspecialedition"))
        self.assertFalse(result["ok"])

    # -- Seamless Co-op password ----------------------------------------

    def test_coop_password_round_trips_through_the_mod_ini(self):
        self._install(
            "Seamless Coop", ["SeamlessCoop/ersc.dll",
                              "SeamlessCoop/ersc_settings.ini"]
        )
        self.assertTrue(
            run(self.plugin.get_me3_coop_password(self.DOMAIN))["installed"]
        )
        self.assertTrue(
            run(self.plugin.set_me3_coop_password(self.DOMAIN, "deckcrew"))["ok"]
        )
        self.assertEqual(
            run(self.plugin.get_me3_coop_password(self.DOMAIN))["password"],
            "deckcrew",
        )

    def test_coop_password_is_absent_without_the_mod(self):
        result = run(self.plugin.get_me3_coop_password(self.DOMAIN))
        self.assertTrue(result["ok"])
        self.assertFalse(result["installed"])


if __name__ == "__main__":
    unittest.main()
