"""Backend tests. Stdlib-only (unittest) so they run anywhere Python does:

    python -m unittest discover -s tests -v

The decky and aiohttp modules are stubbed before importing main.py; all
filesystem paths point into a per-run temp directory. Network is disabled -
anything that would hit the Nexus Mods API raises immediately.
"""

import ast
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
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
        # A mod that finished initialization AND logged an error is
        # DEGRADED, not broken. This used to read "error", on the theory that
        # an error overrides a successful init - until ModConfig 0.2.3 on
        # device announced "initialized!", registered 16 config entries for
        # two other mods, reported state=Loaded, and also failed to inject
        # one duplicate tab. Calling that "error" made a working mod look
        # like the reason the game was unhappy.
        #
        # The [ATA-IronClad] log tag must still map onto the ATA_IronClad
        # folder id.
        self.assertEqual(status["ataironclad"]["state"], "degraded")
        self.assertIn("Patching exception", status["ataironclad"]["detail"])

    def test_a_missing_dependency_is_reported_against_the_mod(self):
        # Verbatim from device. Michael installed four mods individually,
        # the game booted, the banner said "Loaded 3 mods (4 total)" and
        # the plugin reported nothing wrong - because this line shape was
        # not parsed at all. It is the clearest error the loader produces.
        status, _modded = main._parse_mod_load_log([
            "[INFO] RUNNING MODDED",
            "[ERROR] Tried to load mod EnchantedOfferings, but it depends "
            "on mods which have not been loaded: BaseLib!",
        ])
        self.assertEqual(status["enchantedofferings"]["state"], "error")
        self.assertEqual(
            status["enchantedofferings"]["detail"],
            "needs BaseLib, which is not installed",
        )

    def test_several_missing_dependencies_are_all_named(self):
        status, _modded = main._parse_mod_load_log([
            "[ERROR] Tried to load mod Thing, but it depends on mods which "
            "have not been loaded: BaseLib, RitsuLib!",
        ])
        self.assertIn("BaseLib, RitsuLib", status["thing"]["detail"])

    def test_a_mod_built_for_a_newer_game_says_so(self):
        # Verbatim from device. Two different problems open with the same
        # words, and this one was being reported as "duplicate mod id",
        # sending Michael after a clash that did not exist.
        status, _m = main._parse_mod_load_log([
            "[ERROR] Tried to load mod with id ChizuruIroncladSkin, but its "
            "declared min game version 0.110.0 is higher than the current "
            "game version v0.107.1",
        ])
        detail = status["chizuruironcladskin"]["detail"]
        self.assertIn("needs game version 0.110.0", detail)
        self.assertIn("this game is 0.107.1", detail)
        self.assertIn("NEWER build", detail)
        self.assertNotIn("duplicate", detail)

    def test_a_real_duplicate_id_is_still_called_one(self):
        status, _m = main._parse_mod_load_log([
            "[ERROR] Tried to load mod with id Thing, but a mod with that "
            "id is already loaded",
        ])
        self.assertEqual(status["thing"]["detail"], "duplicate mod id")

    def test_a_mod_that_never_loaded_is_still_an_error(self):
        status, _modded = main._parse_mod_load_log([
            "[INFO] RUNNING MODDED",
            "[ERROR] Exception thrown while loading mod Doomed: "
            "System.Reflection.ReflectionTypeLoadException: nope",
        ])
        self.assertEqual(status["doomed"]["state"], "error")

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

    # --- CET Lua mods ----------------------------------------------------
    # Structure taken from the CET wiki's own Mod Structure page:
    # bin/x64/plugins/cyber_engine_tweaks/mods/<my_mod>/init.lua, where
    # init.lua is the entry point CET looks for and extra files may sit in
    # that folder or a subfolder. These matched none of the known roots and
    # were refused as "no Cyberpunk mod layout found" - a large category of
    # this game's mods turned away.

    def test_cet_mod_folder_routes_to_the_cet_mods_dir(self):
        self.put("betterVehicleFirstPerson/init.lua")
        self.put("betterVehicleFirstPerson/config.json")
        files, err = main._route_cp77_payload(self.scratch, "Better Vehicle FP")
        self.assertIsNone(err)
        self.assertEqual(self.rels(files), [
            "bin/x64/plugins/cyber_engine_tweaks/mods/"
            "betterVehicleFirstPerson/config.json",
            "bin/x64/plugins/cyber_engine_tweaks/mods/"
            "betterVehicleFirstPerson/init.lua",
        ])

    def test_the_authors_folder_name_is_preserved(self):
        # It IS the mod's name to CET and to any mod that references it, so
        # renaming it to the Nexus mod name would break both.
        self.put("cyber_vehicle_overhaul/init.lua")
        files, _err = main._route_cp77_payload(
            self.scratch, "Cyber Vehicle Overhaul REDUX v2")
        self.assertIn("mods/cyber_vehicle_overhaul/init.lua", files[0][0])

    def test_subfolders_of_a_cet_mod_are_kept(self):
        self.put("my_mod/init.lua")
        self.put("my_mod/modules/ui.lua")
        self.put("my_mod/data/en-us.json")
        files, err = main._route_cp77_payload(self.scratch, "My Mod")
        self.assertIsNone(err)
        self.assertEqual(self.rels(files), [
            "bin/x64/plugins/cyber_engine_tweaks/mods/my_mod/data/en-us.json",
            "bin/x64/plugins/cyber_engine_tweaks/mods/my_mod/init.lua",
            "bin/x64/plugins/cyber_engine_tweaks/mods/my_mod/modules/ui.lua",
        ])

    def test_a_bare_init_lua_gets_a_folder_from_the_mod_name(self):
        # Nothing else to name it after. CET requires the mod to live in a
        # folder, so one is made rather than refusing a valid mod.
        self.put("init.lua")
        files, err = main._route_cp77_payload(self.scratch, "Cheat Script")
        self.assertIsNone(err)
        self.assertEqual(self.rels(files), [
            "bin/x64/plugins/cyber_engine_tweaks/mods/cheat_script/init.lua",
        ])

    def test_a_mod_shipping_the_full_cet_path_is_unchanged(self):
        # This shape already worked as a "bin" root and must keep working -
        # routing it twice would nest it inside itself.
        self.put("bin/x64/plugins/cyber_engine_tweaks/mods/thing/init.lua")
        files, err = main._route_cp77_payload(self.scratch, "Thing")
        self.assertIsNone(err)
        self.assertEqual(self.rels(files), [
            "bin/x64/plugins/cyber_engine_tweaks/mods/thing/init.lua",
        ])

    def test_a_cet_mod_shipping_archives_too_installs_both(self):
        # Half a mod installed with a success report is the worst kind of
        # report. The archive sweep used to take the .archive and leave the
        # Lua behind.
        self.put("hud_mod/init.lua")
        self.put("hud_mod.archive")
        files, err = main._route_cp77_payload(self.scratch, "HUD Mod")
        self.assertIsNone(err)
        self.assertEqual(self.rels(files), [
            "archive/pc/mod/hud_mod.archive",
            "bin/x64/plugins/cyber_engine_tweaks/mods/hud_mod/init.lua",
        ])

    def test_an_archive_inside_a_cet_mod_stays_with_the_mod(self):
        # A mod's own asset must not be torn out into archive/pc/mod - CET
        # mods can carry data files of any extension.
        self.put("some_mod/init.lua")
        self.put("some_mod/data/bundled.archive")
        files, err = main._route_cp77_payload(self.scratch, "Some Mod")
        self.assertIsNone(err)
        self.assertEqual(self.rels(files), [
            "bin/x64/plugins/cyber_engine_tweaks/mods/some_mod/data/"
            "bundled.archive",
            "bin/x64/plugins/cyber_engine_tweaks/mods/some_mod/init.lua",
        ])

    def test_several_cet_mods_in_one_archive_all_install(self):
        self.put("mod_one/init.lua")
        self.put("mod_two/init.lua")
        files, err = main._route_cp77_payload(self.scratch, "Bundle")
        self.assertIsNone(err)
        self.assertEqual(self.rels(files), [
            "bin/x64/plugins/cyber_engine_tweaks/mods/mod_one/init.lua",
            "bin/x64/plugins/cyber_engine_tweaks/mods/mod_two/init.lua",
        ])

    def test_lua_without_init_is_named_as_a_console_script(self):
        # Cheat Script (mod 542) ships CheatScript/CheatScript.lua and no
        # init.lua, because its own page says "just use it with the Cyber
        # Engine Tweaks console". CET loads init.lua and nothing else, so
        # refusing it is right - but refusing it as "no Cyberpunk mod layout
        # found" told Michael the archive was broken when it was doing
        # exactly what it advertises.
        self.put("CheatScript/CheatScript.lua")
        self.put("CheatScript/ReadMe.txt")
        files, err = main._route_cp77_payload(self.scratch, "Cheat Script 2.2")
        self.assertEqual(files, [])
        self.assertEqual(err[0], "console_script")
        self.assertIn("console", err[1].lower())
        self.assertIn("Cheat Script 2.2", err[1])

    def test_an_archive_with_nothing_recognisable_still_says_so(self):
        self.put("readme.txt")
        self.put("notes/whatever.txt")
        files, err = main._route_cp77_payload(self.scratch, "Not A Mod")
        self.assertIsNotNone(err)
        self.assertEqual(err[0], "layout")
        self.assertIn("init.lua", err[1])


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
        # Comments first: a parameter worth documenting often needs a comma
        # to document it, and "Reported, never switched off" counted as an
        # extra argument.
        blob = re.sub(r"/\*.*?\*/", "", blob, flags=re.DOTALL)
        blob = re.sub(r"//.*", "", blob)
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

    def test_the_default_outranks_what_built_the_prefix(self):
        # I had this the other way round in v0.165.0 and made things worse.
        # config_info describes what BUILT the prefix, not what runs it now:
        # Fallout 3's said "Proton 10.0" while Experimental had been quietly
        # upgrading that prefix for weeks ("Proton: Upgrading prefix from
        # 10.1000-105 to 11.0-100"). Preferring it made the picker choose a
        # build the game was not using - the exact mismatch this function
        # exists to prevent. config_info is a last resort only.
        nl = chr(10)
        with open(os.path.join(self.compat, "version"), "w") as f:
            f.write("10.1000-105" + nl)
        with open(os.path.join(self.compat, "config_info"), "w") as f:
            f.write(
                "10.1000-105" + nl
                + "/home/deck/.local/share/Steam/steamapps/common/"
                + "Proton 10.0/files/share/fonts/" + nl
            )
        p10 = os.path.join(main.STEAM_COMMON, "Proton 10.0")
        self._mk_proton(p10)
        self._mk_proton(self.experimental)
        try:
            proton, _c, _r, err = main._proton_binary_for(self.APP_ID)
        finally:
            # Shared temp root: leaving it behind made the next test find a
            # Proton it never installed.
            shutil.rmtree(p10, ignore_errors=True)
        self.assertEqual(err, "")
        self.assertIn("Experimental", proton)

    def test_config_info_is_used_when_experimental_is_absent(self):
        # A device whose default is not Experimental still gets a sensible
        # answer rather than the first Proton on disk.
        nl = chr(10)
        with open(os.path.join(self.compat, "config_info"), "w") as f:
            f.write(
                "10.1000-105" + nl
                + "/home/deck/.local/share/Steam/steamapps/common/"
                + "Proton 10.0/files/share/fonts/" + nl
            )
        p10 = os.path.join(main.STEAM_COMMON, "Proton 10.0")
        self._mk_proton(p10)
        try:
            proton, _c, _r, err = main._proton_binary_for(self.APP_ID)
        finally:
            shutil.rmtree(p10, ignore_errors=True)
        self.assertEqual(err, "")
        self.assertIn("Proton 10.0", proton)

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
            main._collection_extras({}),
            {"browse": [], "bundle": [], "direct": []},
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


class TestCancelCollectionInstall(unittest.TestCase):
    """Abandoning a collection must remove what IT installed and nothing
    else. A mod the user installed on their own before, or one belonging to
    another collection, stays - even when this collection lists it too.
    Getting that wrong deletes somebody's existing setup."""

    GAME = "Cancel Test"

    def setUp(self):
        self.plugin = main.Plugin()
        root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(root, ignore_errors=True)
        self.data = os.path.join(root, "Data")
        os.makedirs(self.data)
        for rel in ("fromcollection.esp", "byhand.esp", "othercollection.esp"):
            with open(os.path.join(self.data, rel), "w") as f:
                f.write(rel)
        settings = main._load_settings()
        settings.setdefault("installed", {})["canceltest"] = {
            "From Collection": {
                "mode": "dataDir", "mod_id": 1, "collection_slug": "abc123",
                "plugins": [], "files": ["fromcollection.esp"],
            },
            "By Hand": {
                "mode": "dataDir", "mod_id": 2, "collection_slug": "",
                "plugins": [], "files": ["byhand.esp"],
            },
            "Other Collection": {
                "mode": "dataDir", "mod_id": 3, "collection_slug": "zzz999",
                "plugins": [], "files": ["othercollection.esp"],
            },
        }
        main._save_settings(settings)

    def tearDown(self):
        settings = main._load_settings()
        settings.get("installed", {}).pop("canceltest", None)
        main._save_settings(settings)

    def _cancel(self, mod_ids):
        return run(self.plugin.cancel_collection_install(
            "canceltest", "abc123", self.GAME, "Data", 0, "", "listed",
            mod_ids))

    def _there(self, rel):
        return os.path.isfile(os.path.join(self.data, rel))

    def test_removes_only_this_collection_s_mods(self):
        # All three are in the collection's list, only one was installed BY
        # the collection.
        r = self._cancel([1, 2, 3])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["removed"], 1)
        self.assertEqual(r["kept"], 2)
        self.assertFalse(self._there("fromcollection.esp"))
        self.assertTrue(self._there("byhand.esp"))
        self.assertTrue(self._there("othercollection.esp"))

    def test_a_mod_not_in_the_run_is_untouched(self):
        # The run had not reached it, so it is not ours to remove.
        r = self._cancel([])
        self.assertEqual(r["removed"], 0)
        self.assertTrue(self._there("fromcollection.esp"))

    def test_the_records_go_with_the_files(self):
        self._cancel([1])
        recs = main._load_settings()["installed"]["canceltest"]
        self.assertNotIn("From Collection", recs)
        self.assertIn("By Hand", recs)

    def test_the_collection_is_deregistered(self):
        settings = main._load_settings()
        settings.setdefault("collections", {}).setdefault(
            "canceltest", {})["abc123"] = {"title": "Test"}
        settings.setdefault("collection_attention", {}).setdefault(
            "canceltest", {})["abc123"] = [{"reason": "tool"}]
        main._save_settings(settings)
        self._cancel([1])
        settings = main._load_settings()
        self.assertNotIn(
            "abc123", settings.get("collections", {}).get("canceltest", {})
        )
        self.assertNotIn(
            "abc123",
            settings.get("collection_attention", {}).get("canceltest", {}),
        )

    def test_rejects_a_bad_slug(self):
        r = run(self.plugin.cancel_collection_install(
            "canceltest", "../evil", self.GAME))
        self.assertFalse(r["ok"])


class TestBlameFailingMods(unittest.TestCase):
    """Name the mod that BROKE, not the library it called through.

    Slay the Spire 2, 2026-08-13: a collection produced 1,078
    MissingMethodExceptions and crashed the game five seconds into the main
    menu. The stack traces name several mods per exception - the one that
    threw, then the libraries beneath it. STS2RitsuLib appeared in 853
    frames as a victim; blaming it would switch off a shared dependency and
    take working mods with it."""

    LOG = [
        "[WARN] [2026-08-13 15:24:04.238] RouteSuggest: Mod loaded",
        "[ERROR] Exception thrown while loading mod STS2Trade: "
        "System.Reflection.ReflectionTypeLoadException: Unable to load",
        "ERROR: System.MissingMethodException: Method not found: "
        "'Boolean MegaCrit.Sts2.Core.Combat.CombatManager.get_IsPlayPhase()'.",
        "   at RelicsReminder.UnceasingTopLastCardIcon._Process(Double delta)",
        "   at STS2RitsuLib.Helper.Invoke()",
        "   at HarmonyLib.Patch.Call()",
        "   at Godot.Node.InvokeGodotClassMethod(godot_string_name& method)",
        "ERROR: System.MissingMethodException: Method not found: "
        "'MegaCrit.Sts2.Core.Combat.CombatState Creature.get_CombatState()'.",
        "   at RelicsReminder.ArtOfWarFootIcon._Process(Double delta)",
        "   at Godot.Node.InvokeGodotClassMethod(godot_string_name& method)",
    ]

    def _parse(self):
        return main._parse_mod_load_log(self.LOG)

    def test_blames_the_mod_that_threw(self):
        status, _ = self._parse()
        self.assertEqual(status["relicsreminder"]["state"], "error")
        self.assertEqual(status["relicsreminder"]["errors"], 2)

    def test_does_not_blame_the_library_it_called_through(self):
        # STS2RitsuLib is beneath RelicsReminder in the same trace.
        status, _ = self._parse()
        self.assertNotIn("sts2ritsulib", status)

    def test_never_blames_the_engine_or_the_game(self):
        status, _ = self._parse()
        for innocent in ("godot", "harmonylib", "megacrit", "system"):
            self.assertNotIn(innocent, status)

    def test_catches_a_mod_that_failed_to_load_outright(self):
        status, _ = self._parse()
        self.assertEqual(status["sts2trade"]["detail"], "failed to load")

    def test_explains_a_version_mismatch_in_plain_words(self):
        status, _ = self._parse()
        detail = status["relicsreminder"]["detail"]
        self.assertIn("different game build", detail)
        # No exception class names - the user cannot act on those.
        self.assertNotIn("Exception", detail)

    def test_a_working_mod_is_still_reported_loaded(self):
        status, modded = self._parse()
        self.assertTrue(modded is False or modded is True)
        self.assertEqual(status["routesuggest"]["state"], "loaded")

    def test_a_clean_log_blames_nobody(self):
        status, _ = main._parse_mod_load_log([
            "[WARN] [2026-08-13 16:00:00.000] RouteSuggest: Mod loaded",
        ])
        self.assertEqual(
            [k for k, v in status.items() if v["state"] == "error"], []
        )


class TestDisableFailingMods(unittest.TestCase):
    """One tap for "switch off the outdated ones", using the game's own log
    to decide which. Michael asked for it after a Slay the Spire 2
    collection crashed the game with 1,078 exceptions from mods built for a
    different game build."""

    GAME = "Blame Test"
    USER_DIR = "BlameTestUser"

    def setUp(self):
        self.plugin = main.Plugin()
        root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(root, ignore_errors=True)
        self.mods = os.path.join(root, "mods")
        for name in ("RelicsReminder", "STS2RitsuLib", "RouteSuggest"):
            os.makedirs(os.path.join(self.mods, name))
            with open(os.path.join(self.mods, name, "mod.dll"), "w") as f:
                f.write(name)
        self.logdir = os.path.join(
            main.decky.DECKY_USER_HOME, ".local", "share", self.USER_DIR,
            "logs",
        )
        os.makedirs(self.logdir, exist_ok=True)
        with open(os.path.join(self.logdir, "godot.log"), "w") as f:
            f.write(chr(10).join([
                "[WARN] [2026-08-13 15:24:04.238] RouteSuggest: Mod loaded",
                "ERROR: System.MissingMethodException: Method not found: "
                "'Boolean CombatManager.get_IsPlayPhase()'.",
                "   at RelicsReminder.Icon._Process(Double delta)",
                "   at STS2RitsuLib.Helper.Invoke()",
            ]) + chr(10))
        settings = main._load_settings()
        settings.setdefault("installed", {})["blametest"] = {
            "RelicsReminder": {"mode": "folder", "folder": "RelicsReminder",
                               "name": "RelicsReminder", "files": []},
            "STS2RitsuLib": {"mode": "folder", "folder": "STS2RitsuLib",
                             "name": "STS2RitsuLib", "files": []},
            "RouteSuggest": {"mode": "folder", "folder": "RouteSuggest",
                             "name": "RouteSuggest", "files": []},
        }
        main._save_settings(settings)

    def tearDown(self):
        settings = main._load_settings()
        settings.get("installed", {}).pop("blametest", None)
        main._save_settings(settings)
        shutil.rmtree(
            os.path.join(main.decky.DECKY_USER_HOME, ".local", "share",
                         self.USER_DIR),
            ignore_errors=True,
        )

    def _run(self):
        return run(self.plugin.disable_failing_mods(
            "blametest", self.GAME, "mods", self.USER_DIR, "folder"))

    def test_disables_the_mod_the_log_blames(self):
        r = self._run()
        self.assertTrue(r["ok"], r)
        self.assertIn("RelicsReminder", r["names"])

    def test_leaves_the_library_it_called_through_alone(self):
        # Disabling a shared dependency takes working mods with it.
        r = self._run()
        self.assertNotIn("STS2RitsuLib", r["names"])

    def test_leaves_a_working_mod_alone(self):
        r = self._run()
        self.assertNotIn("RouteSuggest", r["names"])

    def test_says_why_each_one_went_off(self):
        r = self._run()
        why = {d["name"]: d["why"] for d in r["details"]}
        self.assertIn("different game build", why["RelicsReminder"])

    def test_a_clean_log_disables_nothing(self):
        with open(os.path.join(self.logdir, "godot.log"), "w") as f:
            f.write("[WARN] [ts] RouteSuggest: Mod loaded" + chr(10))
        self.assertEqual(self._run()["disabled"], 0)

    def test_no_log_is_not_an_error(self):
        os.remove(os.path.join(self.logdir, "godot.log"))
        r = self._run()
        self.assertTrue(r["ok"])
        self.assertEqual(r["disabled"], 0)

    def test_rejects_a_bad_domain(self):
        r = run(self.plugin.disable_failing_mods(
            "../evil", self.GAME, "mods", self.USER_DIR))
        self.assertFalse(r["ok"])

    # --- the two safety rules, from the real device logs -----------------

    def test_a_libraries_own_error_never_switches_it_off(self):
        # Verified on device: BaseLib really did throw a HarmonyException,
        # and 21 mods sat on it. Switching it off cures nothing and breaks
        # everything, so protected_ids holds it back and says so.
        settings = main._load_settings()
        settings["installed"]["blametest"]["STS2RitsuLib"]["mod_id"] = 137
        main._save_settings(settings)
        os.makedirs(os.path.join(self.mods, "BaseLib"), exist_ok=True)
        with open(os.path.join(self.logdir, "godot.log"), "w") as f:
            f.write(chr(10).join([
                "[ERROR] [STS2RitsuLib] HarmonyLib.HarmonyException: "
                "Patching exception in method null",
            ]) + chr(10))
        r = run(self.plugin.disable_failing_mods(
            "blametest", self.GAME, "mods", self.USER_DIR, "folder", 0,
            "", "starred", [137]))
        self.assertTrue(r["ok"], r)
        self.assertNotIn("STS2RitsuLib", r["names"])
        self.assertIn("STS2RitsuLib", r["held"])

    def test_without_protection_a_blamed_library_does_go_off(self):
        # Proves the guard is what saved it, not an accident of matching.
        settings = main._load_settings()
        settings["installed"]["blametest"]["STS2RitsuLib"]["mod_id"] = 137
        main._save_settings(settings)
        with open(os.path.join(self.logdir, "godot.log"), "w") as f:
            f.write("[ERROR] [STS2RitsuLib] HarmonyLib.HarmonyException: "
                    "Patching exception in method null" + chr(10))
        r = run(self.plugin.disable_failing_mods(
            "blametest", self.GAME, "mods", self.USER_DIR, "folder", 0,
            "", "starred", []))
        self.assertIn("STS2RitsuLib", r["names"])

    def test_a_failure_the_mod_calls_optional_is_not_blame(self):
        # RitsuLib logs 2 "[Optional] ... Failed" out of 163 patches and
        # then "161 applied" - it is working. Same for a mod that says it
        # skipped an optional patch class. Blaming either accused two
        # working libraries on the device.
        with open(os.path.join(self.logdir, "godot.log"), "w") as f:
            f.write(chr(10).join([
                "[ERROR] [STS2RitsuLib] [Patcher - framework core] "
                "[Optional] combat_hook_lifecycle - Failed",
                "[ERROR] [RouteSuggest] Optional patch class failed and "
                "was skipped: type=RouteSuggest.Patches.Nav, error=x",
            ]) + chr(10))
        r = self._run()
        self.assertEqual(r["disabled"], 0, r["names"])

    def test_a_real_error_alongside_an_optional_one_still_counts(self):
        with open(os.path.join(self.logdir, "godot.log"), "w") as f:
            f.write(chr(10).join([
                "[ERROR] [STS2RitsuLib] [Optional] hook - Failed",
                "[ERROR] [RelicsReminder] while loading mod RelicsReminder",
            ]) + chr(10))
        r = self._run()
        self.assertEqual(r["names"], ["RelicsReminder"])

    # --- matching a logger name back to an installed mod -----------------

    def _manifest(self, folder: str, mod_id: str, deps=()):
        os.makedirs(os.path.join(self.mods, folder), exist_ok=True)
        with open(os.path.join(self.mods, folder, "mod_manifest.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"id": mod_id, "name": folder,
                       "dependencies": list(deps)}, f)

    def test_a_namespaced_logger_name_finds_its_mod(self):
        # The gap that made this button miss five of nine blamed mods on
        # device: RitsuLib logs as "com.ritsukage.sts2-RitsuLib", which
        # looks nothing like the folder it is installed in.
        self._manifest("STS2RitsuLib", "STS2-RitsuLib")
        with open(os.path.join(self.logdir, "godot.log"), "w") as f:
            f.write("[ERROR] [com.ritsukage.sts2-RitsuLib] [Patcher] "
                    "[Critical] archaic_tooth - Failed" + chr(10))
        self.assertEqual(self._run()["names"], ["STS2RitsuLib"])

    def test_a_mod_others_depend_on_is_held_back(self):
        # No hardcoded list: RouteSuggest's own manifest says it needs
        # STS2RitsuLib, so switching RitsuLib off would break it.
        self._manifest("STS2RitsuLib", "STS2-RitsuLib")
        self._manifest("RouteSuggest", "RouteSuggest", ["STS2-RitsuLib"])
        with open(os.path.join(self.logdir, "godot.log"), "w") as f:
            f.write("[ERROR] [STS2-RitsuLib] HarmonyLib.HarmonyException: "
                    "Patching exception in method null" + chr(10))
        r = self._run()
        self.assertEqual(r["names"], [])
        self.assertEqual(r["held"], ["STS2RitsuLib"])

    def test_a_library_whose_only_dependent_is_also_going_goes_too(self):
        # RouteSuggest is being switched off in this same pass, so nothing
        # is left that needs RitsuLib and holding it back protects nobody.
        self._manifest("STS2RitsuLib", "STS2-RitsuLib")
        self._manifest("RouteSuggest", "RouteSuggest", ["STS2-RitsuLib"])
        with open(os.path.join(self.logdir, "godot.log"), "w") as f:
            f.write(chr(10).join([
                "[ERROR] [STS2-RitsuLib] HarmonyLib.HarmonyException: x",
                "[ERROR] [RouteSuggest] while loading mod RouteSuggest",
            ]) + chr(10))
        r = self._run()
        self.assertEqual(sorted(r["names"]), ["RouteSuggest", "STS2RitsuLib"])
        self.assertEqual(r["held"], [])

    # --- dry run ---------------------------------------------------------

    def _dry(self):
        return run(self.plugin.disable_failing_mods(
            "blametest", self.GAME, "mods", self.USER_DIR, "folder", 0,
            "", "starred", None, True))

    def test_a_dry_run_answers_the_same_but_changes_nothing(self):
        # The panel row is built from this. It saying "9 mods broke" while
        # the button switched off 3 was a bug in its own right.
        dry = self._dry()
        self.assertEqual(dry["names"], ["RelicsReminder"])
        self.assertTrue(os.path.isdir(
            os.path.join(self.mods, "RelicsReminder")))
        wet = self._run()
        self.assertEqual(wet["names"], dry["names"])
        self.assertFalse(os.path.isdir(
            os.path.join(self.mods, "RelicsReminder")))

    def test_a_dry_run_holds_back_the_same_mods(self):
        self._manifest("STS2RitsuLib", "STS2-RitsuLib")
        self._manifest("RouteSuggest", "RouteSuggest", ["STS2-RitsuLib"])
        with open(os.path.join(self.logdir, "godot.log"), "w") as f:
            f.write("[ERROR] [STS2-RitsuLib] HarmonyLib.HarmonyException: x"
                    + chr(10))
        self.assertEqual(self._dry()["held"], ["STS2RitsuLib"])


class TestAutoRepairFailingMods(unittest.TestCase):
    """Acting without being asked, for the mods no judgement is needed on.

    Michael's standing rule: if the plugin can detect it, it fixes it - a
    button is a failure. It had detected the mod that threw 1,041
    exceptions and still waited to be told."""

    GAME = "Repair Test"
    USER_DIR = "RepairTestUser"
    FLOOD = chr(10).join(
        ["ERROR: System.MissingMethodException: Method not found: 'x'.",
         "   at RelicsReminder.Icon._Process(Double delta)"] * 40
    )

    def setUp(self):
        self.plugin = main.Plugin()
        root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(root, ignore_errors=True)
        self.mods = os.path.join(root, "mods")
        for name in ("RelicsReminder", "DeadMod", "Grumpy", "Fine"):
            os.makedirs(os.path.join(self.mods, name))
        self.logdir = os.path.join(
            main.decky.DECKY_USER_HOME, ".local", "share", self.USER_DIR,
            "logs",
        )
        os.makedirs(self.logdir, exist_ok=True)
        self._log(chr(10).join([
            self.FLOOD,
            # Verbatim shape from the device log: no [tag] bracket, so
            # this is the branch that reports "failed to load".
            "[ERROR] Exception thrown while loading mod DeadMod: "
            "System.Reflection.ReflectionTypeLoadException: Unable to "
            "load one or more of the requested types.",
            "[ERROR] [Grumpy] [Patcher - main] [Critical] some_hook - Failed",
        ]))
        settings = main._load_settings()
        settings.setdefault("installed", {})["repairtest"] = {
            n: {"mode": "folder", "folder": n, "name": n, "files": []}
            for n in ("RelicsReminder", "DeadMod", "Grumpy", "Fine")
        }
        settings.pop("auto_disabled", None)
        settings.get("update_attempts", {}).pop("repairtest", None)
        settings.get("mod_verdicts", {}).pop("repairtest", None)
        main._save_settings(settings)

    def tearDown(self):
        settings = main._load_settings()
        settings.get("installed", {}).pop("repairtest", None)
        settings.get("update_attempts", {}).pop("repairtest", None)
        settings.get("mod_verdicts", {}).pop("repairtest", None)
        settings.pop("auto_disabled", None)
        main._save_settings(settings)
        shutil.rmtree(os.path.join(main.STEAM_COMMON, self.GAME),
                      ignore_errors=True)
        shutil.rmtree(
            os.path.join(main.decky.DECKY_USER_HOME, ".local", "share",
                         self.USER_DIR),
            ignore_errors=True,
        )

    def _log(self, body: str):
        with open(os.path.join(self.logdir, "godot.log"), "w") as f:
            f.write(body + chr(10))

    def _repair(self):
        return run(self.plugin.repair_failing_mods(
            "repairtest", self.GAME, "mods", self.USER_DIR, "folder"))

    def test_a_flood_of_exceptions_is_switched_off_unasked(self):
        r = self._repair()
        self.assertTrue(r["ok"], r)
        self.assertIn("RelicsReminder", r["names"])
        self.assertFalse(os.path.isdir(
            os.path.join(self.mods, "RelicsReminder")))

    def test_a_mod_that_never_loaded_is_switched_off_unasked(self):
        # It was not running, so this costs nothing and stops the error box.
        self.assertIn("DeadMod", self._repair()["names"])

    def test_an_ambiguous_failure_is_left_for_the_user(self):
        # A [Critical] patch failure means part of a mod is unhappy. Two
        # mods in that state did no visible harm on device, so switching
        # them off is a decision and stays on the button.
        r = self._repair()
        self.assertNotIn("Grumpy", r["names"])
        self.assertTrue(os.path.isdir(os.path.join(self.mods, "Grumpy")))
        self.assertEqual([d["name"] for d in r["remaining"]], ["Grumpy"])

    def test_a_healthy_mod_is_untouched(self):
        r = self._repair()
        self.assertNotIn("Fine", r["names"])
        self.assertNotIn("Fine", [d["name"] for d in r["remaining"]])

    def test_it_does_not_fight_a_user_who_switches_one_back_on(self):
        # The log does not change when a mod is re-enabled, so a second
        # look at the same session must not undo the user's decision.
        self._repair()
        run(self.plugin.set_mod_enabled(
            self.GAME, "mods", "RelicsReminder", True, "folder",
            "repairtest"))
        again = self._repair()
        # RelicsReminder and DeadMod: it remembers what it did rather than
        # re-deciding, which is the whole point.
        self.assertEqual(again["repaired"], 2)
        self.assertTrue(
            os.path.isdir(os.path.join(self.mods, "RelicsReminder")),
            "re-enabled mod was switched off again behind the user's back",
        )

    def test_a_new_session_is_judged_again(self):
        self._repair()
        run(self.plugin.set_mod_enabled(
            self.GAME, "mods", "RelicsReminder", True, "folder",
            "repairtest"))
        # The game ran again and blamed it again: that is new evidence.
        time.sleep(1.1)
        self._log(self.FLOOD)
        self._repair()
        self.assertFalse(
            os.path.isdir(os.path.join(self.mods, "RelicsReminder")),
            "a fresh session blaming the same mod was ignored",
        )

    def test_an_already_disabled_mod_is_not_offered_again(self):
        self._repair()
        self.assertNotIn(
            "DeadMod", [d["name"] for d in self._repair()["remaining"]])

    def test_no_log_is_not_an_error(self):
        os.remove(os.path.join(self.logdir, "godot.log"))
        r = self._repair()
        self.assertTrue(r["ok"])
        self.assertEqual(r["repaired"], 0)

    def test_rejects_a_bad_domain(self):
        self.assertFalse(run(self.plugin.repair_failing_mods(
            "../evil", self.GAME, "mods", self.USER_DIR))["ok"])

    # --- updating what cannot be switched off ----------------------------
    # Verified on device: the collection pinned BaseLib 3.1.2 and RitsuLib
    # 0.2.30 against a build wanting 3.3.8 and 0.5.11. Updating those two
    # took the blamed count from 5 to 1 and the error lines from 182 to 3 -
    # it fixed the two mods that DEPEND on RitsuLib as well.

    def _library_setup(self, latest="3.3.8"):
        """Grumpy is a library RouteSuggest needs, so it can only be fixed
        by updating it."""
        for folder, mod_id, deps in (
            ("Grumpy", 103, []), ("RouteSuggest", 55, ["Grumpy"]),
        ):
            os.makedirs(os.path.join(self.mods, folder), exist_ok=True)
            with open(os.path.join(self.mods, folder, "mod_manifest.json"),
                      "w", encoding="utf-8") as f:
                json.dump({"id": folder, "name": folder,
                           "dependencies": deps}, f)
        settings = main._load_settings()
        settings["installed"]["repairtest"]["Grumpy"] = {
            "mode": "folder", "folder": "Grumpy", "name": "Grumpy",
            "mod_id": 103, "version": "3.1.2", "files": [],
        }
        settings["installed"]["repairtest"]["RouteSuggest"] = {
            "mode": "folder", "folder": "RouteSuggest",
            "name": "RouteSuggest", "mod_id": 55, "version": "1.0",
            "files": [],
        }
        main._save_settings(settings)
        self._log("[ERROR] [Grumpy] HarmonyLib.HarmonyException: "
                  "Patching exception in method null")
        self.calls = []

        async def fake_files(game_domain, mod_id):
            return {"ok": True, "files": [
                {"file_id": 99, "file_name": "grumpy.zip", "version": latest}]}

        async def fake_install(*a, **k):
            self.calls.append(a[:6])
            settings = main._load_settings()
            settings["installed"]["repairtest"]["Grumpy"]["version"] = latest
            main._save_settings(settings)
            return {"ok": True}

        self.plugin.get_mod_files = fake_files
        self.plugin.install_mod = fake_install

    def test_a_held_back_library_is_updated_instead(self):
        self._library_setup()
        r = self._repair()
        self.assertEqual(
            r["updated"], [{"name": "Grumpy", "from": "3.1.2", "to": "3.3.8"}])
        self.assertTrue(os.path.isdir(os.path.join(self.mods, "Grumpy")))
        # Not reported as still held back: it was dealt with. "held" is for
        # blamed mods nothing could be done about.
        self.assertEqual(r["held"], [])

    def test_a_broken_mod_with_an_update_is_updated_not_switched_off(self):
        # The ordering bug, from device: Remove Multiplayer Player Limit and
        # Refresh Ancient were switched off while 0.1.7 and 1.4.3 sat
        # published on their pages. An update keeps the mod; switching off
        # is what is left when there is no newer version.
        async def fake_files(game_domain, mod_id):
            return {"ok": True, "files": [
                {"file_id": 5, "file_name": "r.zip", "version": "2.0.0"}]}

        installs = []

        async def fake_install(*a, **k):
            installs.append(a[4])
            settings = main._load_settings()
            settings["installed"]["repairtest"]["DeadMod"]["version"] = "2.0.0"
            main._save_settings(settings)
            return {"ok": True}

        settings = main._load_settings()
        settings["installed"]["repairtest"]["DeadMod"]["mod_id"] = 21
        settings["installed"]["repairtest"]["DeadMod"]["version"] = "0.1.6"
        main._save_settings(settings)
        self.plugin.get_mod_files = fake_files
        self.plugin.install_mod = fake_install
        r = self._repair()
        self.assertIn("DeadMod", [u["name"] for u in r["updated"]])
        self.assertNotIn("DeadMod", r["names"])
        self.assertTrue(os.path.isdir(os.path.join(self.mods, "DeadMod")))

    def test_a_broken_mod_with_no_update_is_still_switched_off(self):
        # The remedy of last resort has to survive the reordering.
        async def no_newer(game_domain, mod_id):
            return {"ok": True, "files": [
                {"file_id": 5, "file_name": "r.zip", "version": "0.0.1"}]}

        settings = main._load_settings()
        settings["installed"]["repairtest"]["DeadMod"]["mod_id"] = 21
        settings["installed"]["repairtest"]["DeadMod"]["version"] = "0.1.6"
        main._save_settings(settings)
        self.plugin.get_mod_files = no_newer
        r = self._repair()
        self.assertIn("DeadMod", r["names"])
        self.assertFalse(os.path.isdir(os.path.join(self.mods, "DeadMod")))

    def test_no_update_available_means_no_download(self):
        # Already newest: touching it would be churn for nothing.
        self._library_setup(latest="3.1.2")
        r = self._repair()
        self.assertEqual(r["updated"], [])
        self.assertEqual(self.calls, [])

    def test_an_older_published_version_is_not_installed(self):
        # ModConfig's page version was genuinely LOWER than what the
        # collection installed. Downgrading it would be a regression.
        self._library_setup(latest="0.1.3")
        self.assertEqual(self._repair()["updated"], [])

    def test_a_failed_update_is_not_fatal(self):
        self._library_setup()

        async def boom(*a, **k):
            raise RuntimeError("network down")

        self.plugin.install_mod = boom
        r = self._repair()
        self.assertTrue(r["ok"])
        self.assertEqual(r["updated"], [])

    # --- installing the library a mod asked for --------------------------
    # Verified twice on device: Enchanted Offerings did not load without
    # BaseLib, LustTravel2 did not load without RitsuLib, and both mod pages
    # listed the missing library with its Nexus mod id.

    def _needs_library(self):
        settings = main._load_settings()
        settings["installed"]["repairtest"]["DeadMod"]["mod_id"] = 965
        main._save_settings(settings)
        self._log("[ERROR] Tried to load mod DeadMod, but it depends on "
                  "mods which have not been loaded: STS2-RitsuLib!")
        self.installs = []

        async def fake_reqs(game_domain, mod_id):
            return {"ok": True, "requirements": [
                {"modName": "RitsuLib", "modId": 137,
                 "notes": "Required base library", "url": ""},
                {"modName": "KitLib", "modId": 418,
                 "notes": "optional developer toolkit", "url": ""},
                {"modName": "VC++ redist", "modId": 0, "notes": "", "url": "x"},
            ]}

        async def fake_files(game_domain, mod_id):
            return {"ok": True, "files": [
                {"file_id": 1, "file_name": "lib.zip", "version": "0.5.11"}]}

        async def fake_install(*a, **k):
            self.installs.append(a[1])
            return {"ok": True}

        self.plugin.get_mod_requirements = fake_reqs
        self.plugin.get_mod_files = fake_files
        self.plugin.install_mod = fake_install

    def test_a_gap_is_filled_before_the_game_has_ever_run(self):
        # The whole point: no log, nothing has failed, and the library is
        # still installed because the manifest said it was needed.
        self._needs_library()
        os.remove(os.path.join(self.logdir, "godot.log"))
        os.makedirs(os.path.join(self.mods, "DeadMod"), exist_ok=True)
        with open(os.path.join(self.mods, "DeadMod", "mod_manifest.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"id": "DeadMod", "name": "DeadMod",
                       "dependencies": [{"id": "STS2-RitsuLib"}]}, f)
        r = self._repair()
        self.assertTrue(r["ok"], r)
        self.assertEqual(
            r["installed_deps"], [{"name": "RitsuLib", "for": "DeadMod"}])

    def test_the_missing_library_is_installed(self):
        self._needs_library()
        r = self._repair()
        self.assertEqual(
            r["installed_deps"], [{"name": "RitsuLib", "for": "DeadMod"}])
        self.assertIn(137, self.installs)

    def test_an_optional_requirement_is_left_alone(self):
        # KitLib is listed as an optional developer toolkit. Installing it
        # would be helping myself to somebody's disk.
        self._needs_library()
        self._repair()
        self.assertNotIn(418, self.installs)

    def test_an_off_nexus_requirement_cannot_be_installed(self):
        self._needs_library()
        self._repair()
        self.assertNotIn(0, self.installs)

    def test_the_mod_is_not_switched_off_for_missing_a_library(self):
        # It is not broken - it never got a fair run. Switching it off would
        # mean the user installed a mod and lost it to a fixable cause.
        self._needs_library()
        r = self._repair()
        self.assertNotIn("DeadMod", r["names"])
        self.assertTrue(os.path.isdir(os.path.join(self.mods, "DeadMod")))

    def test_a_library_already_installed_is_not_installed_again(self):
        self._needs_library()
        settings = main._load_settings()
        settings["installed"]["repairtest"]["Fine"]["mod_id"] = 137
        main._save_settings(settings)
        r = self._repair()
        self.assertNotIn(137, self.installs)
        self.assertEqual(r["installed_deps"], [])

    def test_it_does_not_re_download_on_every_panel_open(self):
        self._library_setup()
        self._repair()
        before = len(self.calls)
        self._repair()
        self.assertEqual(len(self.calls), before)

    def test_a_mod_blamed_in_an_earlier_session_is_still_offered_its_update(
        self,
    ):
        # Michael's Ryoshu: blamed two launches ago, still at the version
        # that failed, a newer one published - and the fix appeared in the
        # Updates list while Fixes said nothing, because the repair only
        # ever read the most recent log.
        self._library_setup()
        main._record_mod_verdicts("repairtest", "1", [
            {"mod_id": 103, "version": "3.1.2", "name": "Grumpy",
             "why": "errored"}], "stale")
        # A log that says nothing about Grumpy at all.
        self._log("[WARN] [ts] Fine: Mod loaded")
        orig = main._steam_build_id
        main._steam_build_id = lambda app_id: "1"
        try:
            r = self._repair()
        finally:
            main._steam_build_id = orig
        self.assertEqual(
            r["updated"], [{"name": "Grumpy", "from": "3.1.2", "to": "3.3.8"}])

    def test_the_same_version_is_only_asked_about_once(self):
        # After being told there is nothing newer, asking again cannot
        # change the answer until the mod's version changes.
        self._library_setup(latest="3.1.2")
        self._repair()
        asked = len(self.calls)
        self._repair()
        self._repair()
        self.assertEqual(len(self.calls), asked)

    def test_the_update_pass_is_not_blocked_by_the_disable_guard(self):
        # Two different risks, two different guards: switching a mod off
        # contradicts a user who just re-enabled it, while installing a
        # newer version is idempotent.
        self._library_setup()
        self._repair()                      # sets the once-per-log guard
        settings = main._load_settings()
        settings["installed"]["repairtest"]["Grumpy"]["version"] = "3.1.2"
        settings.get("update_attempts", {}).pop("repairtest", None)
        main._save_settings(settings)
        r = self._repair()                  # same log, guard already set
        self.assertEqual([u["name"] for u in r["updated"]], ["Grumpy"])

    def test_a_switchable_mod_is_switched_off_not_downloaded(self):
        # Everything with a cheaper remedy already took it, so this never
        # downloads for a mod that could simply be turned off.
        self._library_setup()
        self._repair()
        self.assertEqual([c[4] for c in self.calls], ["Grumpy"])


class TestLegacyModsBatching(unittest.TestCase):
    """The Nexus v2 legacyMods(ids:) response caps at 20 nodes and says
    nothing about the rest - no error, no cursor, the extra ids are simply
    absent.

    Found 2026-08-13 the hard way: 27 Slay the Spire 2 mods were installed,
    the game was printing "Loaded 21 mods WITH ERRORS" across the main menu
    because RitsuLib was 3 minor versions behind, and the plugin reported no
    updates available - RitsuLib was one of the 7 ids the API dropped. On a
    546-mod collection this silently ignored 526 mods."""

    def setUp(self):
        self.original = main._gql_query
        self.asked = []

        async def fake_gql(query, api_key=None):
            ids = [int(m) for m in re.findall(r"modId: (\d+)", query)]
            self.asked.append(ids)
            # Behave exactly like the real endpoint: answer the first 20 and
            # stay silent about the others.
            return {
                "legacyMods": {
                    "nodes": [
                        {"modId": i, "version": f"9.{i}"}
                        for i in ids[:main.LEGACY_MODS_PAGE]
                    ]
                }
            }

        main._gql_query = fake_gql
        main._GAME_ID_CACHE["batchtest"] = 1

    def tearDown(self):
        main._gql_query = self.original
        main._GAME_ID_CACHE.pop("batchtest", None)
        settings = main._load_settings()
        settings.get("installed", {}).pop("batchtest", None)
        main._save_settings(settings)

    def test_every_id_comes_back_when_there_are_more_than_a_page(self):
        got = run(main._legacy_mods_in_batches(
            1, list(range(1, 28)), " modId version "))
        self.assertEqual(len(got), 27)
        self.assertEqual({n["modId"] for n in got}, set(range(1, 28)))

    def test_it_asks_in_pages_the_api_answers_fully(self):
        run(main._legacy_mods_in_batches(1, list(range(1, 28)), " modId "))
        self.assertEqual([len(a) for a in self.asked], [20, 7])

    def test_exactly_one_page_is_one_request(self):
        run(main._legacy_mods_in_batches(1, list(range(1, 21)), " modId "))
        self.assertEqual(len(self.asked), 1)

    def test_no_ids_makes_no_request(self):
        self.assertEqual(run(main._legacy_mods_in_batches(1, [], " modId ")), [])
        self.assertEqual(self.asked, [])

    def test_the_update_check_sees_past_the_first_page(self):
        # The bug as the user met it: the 27th mod's update was invisible.
        settings = main._load_settings()
        settings.setdefault("installed", {})["batchtest"] = {
            f"Mod{i}": {"mod_id": i, "version": "1.0.0", "mode": "folder"}
            for i in range(1, 28)
        }
        main._save_settings(settings)
        r = run(main.Plugin().check_updates("batchtest"))
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(r["updates"]), 27)
        self.assertTrue(r["updates"]["Mod27"]["update_available"])


class TestCollectionPinnedUpdates(unittest.TestCase):
    """A collection pins mod versions on purpose, so the update check
    normally leaves them alone. Not when the game cannot run the pin."""

    def setUp(self):
        self.original = main._gql_query

        async def fake_gql(query, api_key=None):
            ids = [int(m) for m in re.findall(r"modId: (\d+)", query)]
            return {"legacyMods": {"nodes": [
                {"modId": i, "version": "3.3.8"} for i in ids]}}

        main._gql_query = fake_gql
        main._GAME_ID_CACHE["pintest"] = 1
        settings = main._load_settings()
        settings.setdefault("installed", {})["pintest"] = {
            "BaseLib": {"mod_id": 103, "version": "3.1.2",
                        "source": "collection", "mode": "folder"},
            "Quiet": {"mod_id": 104, "version": "3.1.2",
                      "source": "collection", "mode": "folder"},
        }
        main._save_settings(settings)

    def tearDown(self):
        main._gql_query = self.original
        main._GAME_ID_CACHE.pop("pintest", None)
        settings = main._load_settings()
        settings.get("installed", {}).pop("pintest", None)
        main._save_settings(settings)

    def test_a_pinned_mod_is_left_alone_by_default(self):
        r = run(main.Plugin().check_updates("pintest"))
        self.assertEqual(r["updates"], {})

    def test_a_pinned_mod_the_game_blamed_is_checked_anyway(self):
        r = run(main.Plugin().check_updates("pintest", ["BaseLib"]))
        self.assertTrue(r["updates"]["BaseLib"]["update_available"])
        self.assertTrue(r["updates"]["BaseLib"]["blamed"])

    def test_the_other_pinned_mods_stay_quiet(self):
        # Only the mod the game complained about earns the exception.
        r = run(main.Plugin().check_updates("pintest", ["BaseLib"]))
        self.assertNotIn("Quiet", r["updates"])


class TestModVerdictMemory(unittest.TestCase):
    """Remembering that a mod could not run, so a reset and reinstall does
    not have to crash the game to find out again.

    Michael reset game modding, reinstalled the Slay the Spire 2 collection
    and the game died on exactly the mod the plugin had already watched kill
    it twice. Every fix up to that point only worked after a crash had
    produced a log to read."""

    def setUp(self):
        settings = main._load_settings()
        settings.pop("mod_verdicts", None)
        main._save_settings(settings)

    def tearDown(self):
        settings = main._load_settings()
        settings.pop("mod_verdicts", None)
        settings.get("installed", {}).pop("verdicttest", None)
        main._save_settings(settings)

    def _note(self, build="100", mod_id=284, version="1.2.0"):
        return main._record_mod_verdicts("verdicttest", build, [
            {"mod_id": mod_id, "version": version, "name": "Relics Reminder",
             "why": "calls something this version of the game does not have"},
        ])

    def test_a_verdict_survives_being_written_and_read(self):
        self._note()
        known = main._known_broken_mods("verdicttest", "100")
        self.assertEqual(known[284]["state"], "broken")
        self.assertIn("does not have", known[284]["why"])

    def test_a_game_update_retires_the_verdict(self):
        # A build change is the single most likely thing to have fixed or
        # broken a mod, so afterwards the honest answer is "unknown".
        self._note(build="100")
        self.assertEqual(main._known_broken_mods("verdicttest", "101"), {})

    def test_an_unknown_build_is_not_a_verdict(self):
        self._note()
        self.assertEqual(main._known_broken_mods("verdicttest", ""), {})

    def test_a_verdict_with_no_build_is_not_recorded(self):
        # Better to know nothing than to record a fact with no scope.
        self.assertEqual(self._note(build=""), 0)
        self.assertEqual(main._known_broken_mods("verdicttest", "100"), {})

    def test_writing_the_same_verdict_twice_changes_nothing(self):
        self.assertEqual(self._note(), 1)
        self.assertEqual(self._note(), 0)

    def test_a_verdict_needs_a_mod_id(self):
        self.assertEqual(main._record_mod_verdicts("verdicttest", "100", [
            {"name": "no id", "why": "x"}]), 0)


class TestApplyKnownVerdicts(unittest.TestCase):
    """Switching off what is already known not to run, BEFORE the first
    launch. This is the step that stops the first crash."""

    GAME = "Verdict Test"

    def setUp(self):
        self.plugin = main.Plugin()
        root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(root, ignore_errors=True)
        self.mods = os.path.join(root, "mods")
        for name in ("RelicsReminder", "BaseLib", "Fine"):
            os.makedirs(os.path.join(self.mods, name))
        self.build = "555"
        self._orig_build = main._steam_build_id
        main._steam_build_id = lambda app_id: self.build
        settings = main._load_settings()
        settings.setdefault("installed", {})["verdicttest"] = {
            "RelicsReminder": {"mod_id": 284, "version": "1.2.0",
                               "mode": "folder", "name": "Relics Reminder"},
            "BaseLib": {"mod_id": 103, "version": "3.1.2",
                        "mode": "folder", "name": "BaseLib"},
            "Fine": {"mod_id": 999, "version": "1.0.0",
                     "mode": "folder", "name": "Fine"},
        }
        settings.pop("mod_verdicts", None)
        main._save_settings(settings)

    def tearDown(self):
        main._steam_build_id = self._orig_build
        settings = main._load_settings()
        settings.pop("mod_verdicts", None)
        settings.get("installed", {}).pop("verdicttest", None)
        main._save_settings(settings)
        shutil.rmtree(os.path.join(main.STEAM_COMMON, self.GAME),
                      ignore_errors=True)

    def _note(self, mod_id=284, version="1.2.0", build=None):
        main._record_mod_verdicts("verdicttest", build or self.build, [
            {"mod_id": mod_id, "version": version, "name": "x", "why": "y"}])

    def _apply(self):
        return run(self.plugin.apply_known_verdicts(
            "verdicttest", self.GAME, "mods", "folder", 1))

    def test_a_known_broken_mod_is_off_before_the_first_launch(self):
        self._note()
        r = self._apply()
        self.assertEqual(r["names"], ["Relics Reminder"])
        self.assertFalse(os.path.isdir(
            os.path.join(self.mods, "RelicsReminder")))

    def test_a_mod_with_no_verdict_is_untouched(self):
        self._note()
        self._apply()
        self.assertTrue(os.path.isdir(os.path.join(self.mods, "Fine")))

    def test_a_newer_version_starts_from_innocent(self):
        # The verdict was against 1.2.0. Assuming 1.3.0 is still broken
        # would hold a user back from a fix that has already shipped.
        self._note(version="1.1.0")
        r = self._apply()
        self.assertEqual(r["disabled"], 0)
        self.assertTrue(os.path.isdir(
            os.path.join(self.mods, "RelicsReminder")))

    def test_a_verdict_from_an_older_build_does_not_apply(self):
        self._note(build="554")
        self.assertEqual(self._apply()["disabled"], 0)

    def test_a_library_other_mods_need_is_held_back(self):
        # Same rule as everywhere else: never take the working mods down
        # with the broken one.
        with open(os.path.join(self.mods, "BaseLib", "mod_manifest.json"),
                  "w") as f:
            json.dump({"id": "BaseLib", "name": "BaseLib"}, f)
        with open(os.path.join(self.mods, "Fine", "mod_manifest.json"),
                  "w") as f:
            json.dump({"id": "Fine", "dependencies": ["BaseLib"]}, f)
        self._note(mod_id=103, version="3.1.2")
        r = self._apply()
        self.assertEqual(r["names"], [])
        self.assertEqual(r["held"], ["BaseLib"])

    def test_no_verdicts_is_a_no_op(self):
        self.assertEqual(self._apply()["disabled"], 0)

    def test_it_does_not_switch_off_what_is_already_off(self):
        self._note()
        self._apply()
        self.assertEqual(self._apply()["disabled"], 0)

    def test_the_mod_page_can_ask_about_one_mod(self):
        self._note()
        r = run(self.plugin.get_known_mod_verdict("verdicttest", 284, 1))
        self.assertTrue(r["known"])
        self.assertEqual(r["version"], "1.2.0")
        clean = run(self.plugin.get_known_mod_verdict("verdicttest", 999, 1))
        self.assertFalse(clean["known"])

    def test_rejects_a_bad_domain(self):
        self.assertFalse(run(self.plugin.apply_known_verdicts(
            "../evil", self.GAME, "mods"))["ok"])
        self.assertFalse(run(self.plugin.get_known_mod_verdict(
            "../evil", 1, 1))["ok"])

    def test_a_stale_library_is_updated_at_install_not_disabled(self):
        # The gap Michael hit: reset, reinstall, and the collection put
        # BaseLib 3.1.2 and RitsuLib 0.2.30 straight back with all five
        # errors, minutes after they had been fixed. Verdicts only covered
        # mods that get switched off, so finish-setup had nothing to act on.
        main._record_mod_verdicts("verdicttest", self.build, [
            {"mod_id": 103, "version": "3.1.2", "name": "BaseLib",
             "why": "HarmonyException"}], "stale")
        calls = []

        async def fake_files(game_domain, mod_id):
            return {"ok": True, "files": [
                {"file_id": 7, "file_name": "b.zip", "version": "3.3.8"}]}

        async def fake_install(*a, **k):
            calls.append(a[4])
            return {"ok": True}

        self.plugin.get_mod_files = fake_files
        self.plugin.install_mod = fake_install
        r = self._apply()
        self.assertEqual(
            r["updated"], [{"name": "BaseLib", "from": "3.1.2", "to": "3.3.8"}])
        self.assertEqual(calls, ["BaseLib"])
        # And it is NOT switched off - other mods need it.
        self.assertTrue(os.path.isdir(os.path.join(self.mods, "BaseLib")))

    def test_a_stale_verdict_against_another_version_is_ignored(self):
        main._record_mod_verdicts("verdicttest", self.build, [
            {"mod_id": 103, "version": "2.0.0", "name": "BaseLib",
             "why": "x"}], "stale")
        called = []
        self.plugin.install_mod = lambda *a, **k: called.append(1)
        self.assertEqual(self._apply()["updated"], [])
        self.assertEqual(called, [])

    def test_broken_and_stale_are_kept_apart(self):
        self._note(mod_id=284, version="1.2.0")
        main._record_mod_verdicts("verdicttest", self.build, [
            {"mod_id": 103, "version": "3.1.2", "name": "BaseLib",
             "why": "x"}], "stale")
        broken = main._known_broken_mods("verdicttest", self.build)
        stale = main._known_broken_mods("verdicttest", self.build, "stale")
        self.assertEqual(list(broken), [284])
        self.assertEqual(list(stale), [103])

    def test_reset_game_modding_does_not_forget_the_verdicts(self):
        # The exact moment the feature has to survive. Reset means start the
        # mods clean, not relearn from a third crash which mod kills the
        # game - so if a future change adds "mod_verdicts" to the list of
        # sections reset clears, this fails.
        self._note()
        run(self.plugin.reset_game_modding(
            "verdicttest", self.GAME, "mods", "folder", 1))
        self.assertIn(
            284, main._known_broken_mods("verdicttest", self.build),
            "reset threw away what the plugin had learned",
        )


class TestDownloadForbiddenReason(unittest.TestCase):
    """A 403 from the download-link endpoint is not automatically a Premium
    problem.

    Michael installed Slay the Spire 2's most popular collection on a
    Premium account and was told twice that direct downloads need Premium.
    One mod had been deleted by its author, the other taken down by Nexus
    for review - and the endpoint says which, in the body, which this code
    was throwing away. Bodies below are verbatim from the API."""

    DELETED = '{"code":403,"message":"Mod not available: 502"}'
    MODERATED = ('{"code":403,"message":"File currently not available. '
                 'Library of Ruina (Mod ID: 368) is under moderation"}')

    def test_a_mod_under_moderation_says_so(self):
        msg = main._download_forbidden_reason(self.MODERATED, True)
        self.assertIn("taken this mod down while it is reviewed", msg)
        self.assertNotIn("Premium", msg)

    def test_a_deleted_mod_says_so(self):
        msg = main._download_forbidden_reason(self.DELETED, True)
        self.assertIn("author has removed this mod", msg)
        self.assertNotIn("Premium", msg)

    def test_the_reason_does_not_depend_on_the_account(self):
        # A free user hitting a deleted mod is not a Premium problem
        # either, and telling them to buy an account would not help.
        for premium in (True, False, None):
            self.assertIn(
                "author has removed",
                main._download_forbidden_reason(self.DELETED, premium),
            )

    def test_a_free_account_still_gets_the_premium_message(self):
        # Wording changed 2026-08-18 - "Premium account" became "needs
        # Premium" in a sentence that leads with the situation rather than
        # the requirement. Asserting the fact, not the phrasing.
        msg = main._download_forbidden_reason('{"code":403}', False)
        self.assertIn("Premium", msg)
        self.assertIn("free", msg.lower())

    def test_a_premium_account_is_never_told_to_get_premium(self):
        # The bug, stated as a test. Whatever an unrecognised 403 means on
        # a Premium account, it is not the account.
        msg = main._download_forbidden_reason('{"code":403}', True)
        self.assertNotIn("Premium", msg)

    def test_an_unparseable_body_does_not_crash(self):
        for body in ("", "<html>502 Bad Gateway</html>", None):
            self.assertTrue(main._download_forbidden_reason(body, False))

    def test_an_unknown_message_is_passed_through_for_premium(self):
        msg = main._download_forbidden_reason(
            '{"code":403,"message":"Rate limit exceeded"}', True)
        self.assertEqual(msg, "Rate limit exceeded")


class TestGodotModManifests(unittest.TestCase):
    """Reading each mod's own manifest, which is what makes matching a log
    tag to an installed mod possible at all.

    All of this is shaped by the real Slay the Spire 2 mods dir on device,
    2026-08-13: 27 mods, ids that do not match their folder names, two
    files written with a UTF-8 BOM, and dependency lists that say which
    mods are libraries so the plugin does not have to guess."""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _mod(self, folder: str, filename: str, body: str, bom=False):
        os.makedirs(os.path.join(self.root, folder), exist_ok=True)
        path = os.path.join(self.root, folder, filename)
        with open(path, "w", encoding="utf-8-sig" if bom else "utf-8") as f:
            f.write(body)

    def test_reads_id_name_and_dependencies(self):
        self._mod("RitsuLib", "mod_manifest.json", json.dumps(
            {"id": "STS2-RitsuLib", "name": "RitsuLib", "dependencies": []}))
        got = main._godot_mod_manifests(self.root)
        self.assertEqual(got["RitsuLib"]["id"], "STS2-RitsuLib")
        self.assertEqual(got["RitsuLib"]["name"], "RitsuLib")
        self.assertEqual(got["RitsuLib"]["deps"], [])

    def test_a_bom_does_not_hide_the_manifest(self):
        # BaseLib and TransformOrBanish are both written with a BOM on
        # device. Read as plain utf-8 they raise, and both mods vanish -
        # which is how BaseLib came to look like it had no manifest.
        self._mod("BaseLib", "BaseLib.json",
                  json.dumps({"id": "BaseLib", "name": "BaseLib"}), bom=True)
        self.assertEqual(
            main._godot_mod_manifests(self.root)["BaseLib"]["id"], "BaseLib")

    def test_finds_a_manifest_named_after_the_mod(self):
        self._mod("Campfire Trading", "STS2Trade.json", json.dumps(
            {"id": "STS2Trade", "name": "Campfire Trading",
             "dependencies": ["BaseLib"]}))
        got = main._godot_mod_manifests(self.root)["Campfire Trading"]
        self.assertEqual(got["id"], "STS2Trade")
        self.assertEqual(got["deps"], ["BaseLib"])

    def test_prefers_mod_manifest_json_over_another_json(self):
        self._mod("Both", "aaa-data.json", json.dumps({"id": "WRONG"}))
        self._mod("Both", "mod_manifest.json", json.dumps({"id": "RIGHT"}))
        self.assertEqual(
            main._godot_mod_manifests(self.root)["Both"]["id"], "RIGHT")

    def test_missing_dependencies_key_is_an_empty_list(self):
        # Several mods write "dependencies": null or omit it entirely.
        self._mod("A", "A.json", json.dumps({"id": "A", "dependencies": None}))
        self._mod("B", "B.json", json.dumps({"id": "B"}))
        got = main._godot_mod_manifests(self.root)
        self.assertEqual(got["A"]["deps"], [])
        self.assertEqual(got["B"]["deps"], [])

    def test_unreadable_or_idless_json_is_skipped_not_fatal(self):
        self._mod("Broken", "x.json", "{ not json")
        self._mod("NoId", "y.json", json.dumps({"name": "no id here"}))
        self._mod("Fine", "z.json", json.dumps({"id": "Fine"}))
        got = main._godot_mod_manifests(self.root)
        self.assertNotIn("Broken", got)
        self.assertNotIn("NoId", got)
        self.assertIn("Fine", got)

    def test_a_missing_mods_dir_is_empty_not_an_error(self):
        self.assertEqual(
            main._godot_mod_manifests(os.path.join(self.root, "nope")), {})


class TestManifestDependencyShapes(unittest.TestCase):
    """Both dependency shapes appear in the wild, and only one was handled.

    Verified on device 2026-08-13: the collection's mods wrote
    ["BaseLib"], while Enchanted Offerings wrote
    [{"id": "BaseLib", "min_version": "3.1.2"}]. str() on the dict form
    produced "{'id': 'BaseLib', ...}", which matched nothing - so a mod
    declaring its dependency the richer way silently failed to protect the
    library it needs from being switched off."""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _mod(self, folder, deps):
        os.makedirs(os.path.join(self.root, folder), exist_ok=True)
        with open(os.path.join(self.root, folder, "mod_manifest.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"id": folder, "name": folder,
                       "dependencies": deps}, f)

    def test_the_string_shape_still_works(self):
        self._mod("A", ["BaseLib"])
        self.assertEqual(
            main._godot_mod_manifests(self.root)["A"]["deps"], ["BaseLib"])

    def test_the_dict_shape_yields_the_id(self):
        self._mod("A", [{"id": "BaseLib", "min_version": "3.1.2"}])
        self.assertEqual(
            main._godot_mod_manifests(self.root)["A"]["deps"], ["BaseLib"])

    def test_a_mixture_is_handled(self):
        self._mod("A", ["ModConfig", {"id": "BaseLib"}])
        self.assertEqual(
            main._godot_mod_manifests(self.root)["A"]["deps"],
            ["ModConfig", "BaseLib"])

    def test_a_dict_with_no_id_is_dropped_not_stringified(self):
        self._mod("A", [{"min_version": "1.0"}, ""])
        self.assertEqual(main._godot_mod_manifests(self.root)["A"]["deps"], [])

    def test_the_dict_shape_protects_its_library(self):
        # The consequence, stated end to end: BaseLib must read as needed.
        self._mod("BaseLib", [])
        self._mod("A", [{"id": "BaseLib", "min_version": "3.1.2"}])
        manifests = main._godot_mod_manifests(self.root)
        needed = main._mods_needed_by_others(manifests, {"A", "BaseLib"})
        self.assertEqual(needed.get("baselib"), ["A"])


class TestMissingManifestDeps(unittest.TestCase):
    """A dependency gap read from the manifests, so it is knowable the
    moment a mod is installed.

    The first version of this check read the session log, so it needed the
    game to have launched and failed first. Michael installed LustTravel2,
    opened Fixes and found nothing - because nothing had gone wrong yet."""

    def test_a_missing_dependency_is_found_with_no_log_at_all(self):
        gaps = main._missing_manifest_deps({
            "LustTravel2": {"id": "LustTravel2", "name": "LustTravel2",
                            "deps": ["STS2-RitsuLib"]},
        })
        self.assertEqual(gaps, [{"folder": "LustTravel2",
                                 "name": "LustTravel2",
                                 "missing": ["STS2-RitsuLib"]}])

    def test_a_satisfied_dependency_is_not_a_gap(self):
        self.assertEqual(main._missing_manifest_deps({
            "LustTravel2": {"id": "LustTravel2", "name": "LustTravel2",
                            "deps": ["STS2-RitsuLib"]},
            "RitsuLib": {"id": "STS2-RitsuLib", "name": "RitsuLib",
                         "deps": []},
        }), [])

    def test_dependency_ids_match_case_insensitively(self):
        # The log writes STS2-RitsuLib, the manifest id is STS2-RitsuLib,
        # and a mod may write either casing.
        self.assertEqual(main._missing_manifest_deps({
            "A": {"id": "A", "name": "A", "deps": ["baselib"]},
            "BaseLib": {"id": "BaseLib", "name": "BaseLib", "deps": []},
        }), [])

    def test_several_gaps_are_all_reported(self):
        gaps = main._missing_manifest_deps({
            "A": {"id": "A", "name": "A", "deps": ["BaseLib"]},
            "B": {"id": "B", "name": "B", "deps": ["BaseLib", "ModConfig"]},
        })
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[1]["missing"], ["BaseLib", "ModConfig"])

    def test_no_mods_is_no_gaps(self):
        self.assertEqual(main._missing_manifest_deps({}), [])

    def test_a_mod_with_no_declared_dependencies_is_never_a_gap(self):
        self.assertEqual(main._missing_manifest_deps({
            "A": {"id": "A", "name": "A", "deps": []},
        }), [])


class TestEndorsementIsRemembered(unittest.TestCase):
    """Nexus kept reporting "Undecided" for mods this account had just
    endorsed, so every deploy - which remounts the panel and re-reads -
    showed them un-endorsed again. Michael asked, fairly, whether it should
    not already know that he had endorsed them."""

    DOMAIN = "endtest"

    def tearDown(self):
        settings = main._load_settings()
        settings.get("endorsed", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)

    def _remember(self, mod_id, value):
        settings = main._load_settings()
        settings.setdefault("endorsed", {}).setdefault(
            self.DOMAIN, {})[str(mod_id)] = value
        main._save_settings(settings)

    def test_our_own_endorsement_beats_an_undecided_read(self):
        src = open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read()
        start = src.index("async def get_endorsement")
        body = src[start:src.index(chr(10) + "    async def ", start + 10)]
        self.assertIn('if status == "Undecided":', body)
        self.assertIn('status = "Endorsed"', body)

    def test_it_only_ever_upgrades(self):
        # A remote "Endorsed" or "Abstained" must pass through untouched, so
        # this can never contradict Nexus in the other direction.
        src = open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read()
        start = src.index("async def get_endorsement")
        body = src[start:src.index(chr(10) + "    async def ", start + 10)]
        self.assertNotIn('status = "Undecided"', body.split(
            'if status == "Undecided":')[1])

    def test_abstaining_is_recorded_too(self):
        # Otherwise taking an endorsement back would be undone by the very
        # memory meant to preserve it.
        src = open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read()
        start = src.index("async def set_endorsement")
        body = src[start:src.index(chr(10) + "    async def ", start + 10)]
        self.assertIn("] = bool(endorse)", body)


class TestEndorseRegistersDownload(unittest.TestCase):
    """Endorsing a mod Nexus has no download for.

    Michael could not endorse REFramework after installing it twice. The
    API said NOT_DOWNLOADED_MOD for every version string tried, because
    _download_archive short-circuits on a cached archive and never requests
    a download link - and requesting the link is what registers a download.
    He cares because it costs mod authors their endorsements."""

    def test_the_repair_runs_once_and_only_on_that_error(self):
        src = open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read()
        start = src.index("async def set_endorsement")
        body = src[start:src.index(chr(10) + "    async def ", start + 10)]
        self.assertIn('if "NOT_DOWNLOADED_MOD" in message and not _retried:',
                      body)
        self.assertIn("_register_download(", body)
        # Exactly one retry: a loop here would inflate download counts.
        self.assertEqual(body.count("_retried=True"), 1)

    def test_registering_never_raises(self):
        # It only ever runs as a repair on a path that has already failed,
        # so an exception there would replace a useful message with a stack
        # trace.
        src = open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read()
        start = src.index("async def _register_download")
        body = src[start:start + 2200]
        self.assertIn("except (aiohttp.ClientError", body)
        self.assertIn("return False", body)

    def test_it_asks_for_the_newest_main_file(self):
        # The endorsement check is per MOD, so any real file registers it -
        # but picking the newest MAIN keeps it the same file an install
        # would have fetched.
        src = open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read()
        start = src.index("async def _register_download")
        body = src[start:start + 2200]
        self.assertIn('category_name") == "MAIN"', body)
        self.assertIn("download_link.json", body)


class TestRedscriptLogParsing(unittest.TestCase):
    """Lines lifted from the real r6/logs/redscript log on device.

    A single failing .reds blocks EVERY redscript mod, which is why two
    orphaned files killed the whole script stack of every Cyberpunk
    collection Michael installed - and why naming them matters."""

    LINES = [
        "[ERROR - Fri, 14 Aug 2026 15:44:08 +0100] [UNRESOLVED_REF] At "
        "S:" + chr(92) + "steamapps" + chr(92) + "common" + chr(92) + "Cyberpunk 2077" + chr(92)
        + "r6" + chr(92) + "scripts" + chr(92) + "GeneralShadowsFixes.reds:7094:20:",
        "unresolved reference 'JobQueue'",
        "[ERROR - Fri] [UNRESOLVED_METHOD] At S:" + chr(92) + "r6" + chr(92) + "scripts"
        + chr(92) + "QuickMelee Sandevistan Fix.reds:4:28:",
    ]

    def test_it_names_the_script_that_failed(self):
        got = main._parse_redscript_log(self.LINES)
        self.assertEqual(
            got["generalshadowsfixes.reds"]["script"],
            "GeneralShadowsFixes.reds")
        self.assertIn("quickmelee sandevistan fix.reds", got)

    def test_it_captures_the_symbol_from_the_next_line(self):
        # The reference is on the line AFTER the error, which is what makes
        # this "built for a different game version" rather than "broken".
        got = main._parse_redscript_log(self.LINES)
        self.assertEqual(got["generalshadowsfixes.reds"]["symbol"], "JobQueue")

    def test_repeats_of_one_script_are_counted_not_duplicated(self):
        got = main._parse_redscript_log(self.LINES + self.LINES)
        self.assertEqual(got["generalshadowsfixes.reds"]["count"], 2)
        self.assertEqual(len(got), 2)

    def test_a_clean_log_blames_nothing(self):
        self.assertEqual(main._parse_redscript_log(
            ["[INFO] Compilation complete", ""]), {})

    def test_the_error_kind_is_kept(self):
        got = main._parse_redscript_log(self.LINES)
        self.assertEqual(
            got["quickmelee sandevistan fix.reds"]["kind"],
            "UNRESOLVED_METHOD")

    def test_an_unresolved_method_says_what_it_wanted(self):
        # The other message shape, verbatim from the same log. The first
        # version of this only matched "unresolved reference 'X'", so every
        # UNRESOLVED_METHOD came back with no symbol - losing the half of
        # the evidence that says what the script wanted from a game it no
        # longer matches.
        got = main._parse_redscript_log(self.LINES + [
            " let x = scriptInterface.executionOwner.GetStatValue(\"S\");",
            "                         ^^^^^^^^^^^^^^",
            "method 'GetStatValue' not found on 'GameObject'",
        ])
        self.assertEqual(
            got["quickmelee sandevistan fix.reds"]["symbol"], "GetStatValue")

    def test_a_distant_symbol_is_not_attributed_to_an_earlier_script(self):
        # The message sits three lines under its error. Without a bound, a
        # script whose error carries no message keeps claiming lines until
        # the next error and inherits a symbol from somewhere else entirely.
        got = main._parse_redscript_log([
            self.LINES[2],
            "code", "carets", "", "", "", "", "",
            "unresolved reference 'SomethingElse'",
        ])
        self.assertEqual(
            got["quickmelee sandevistan fix.reds"]["symbol"], "")


class TestRedscriptReport(unittest.TestCase):
    """Matching what the compiler said against what we installed.

    The health check had no way to tell a curator's deliberate omission
    from a user's mistake, so it reported seven of Welcome to Night City's
    283 mods as faults - including the one whose orphaned script was
    breaking the game."""

    GAME = "Redscript Test"

    ERRORS = [
        "[ERROR - Fri] [UNRESOLVED_REF] At S:" + chr(92) + "r6" + chr(92)
        + "scripts" + chr(92) + "GeneralShadowsFixes.reds:7094:20:",
        "    let jobQueue = JobQueue.Create();",
        "                   ^^^^^^^^",
        "unresolved reference 'JobQueue'",
    ]
    DONE = ["[INFO - Fri] Compilation complete",
            "[INFO - Fri] Output successfully saved to final.redscripts.modded"]

    def setUp(self):
        self.root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "r6", "logs"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _put_script(self, rel="r6/scripts/GeneralShadowsFixes.reds"):
        """The blamed .reds, on disk. A failure whose file has been deleted
        is not reported: an uninstall leaves no record behind, so nothing
        else would say the problem is gone."""
        p = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()

    def _log(self, lines):
        with open(main._redscript_log_path(self.root), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines))

    def test_no_log_means_the_game_has_not_been_asked(self):
        # The state every game without a script compiler is permanently in.
        # It must never be confused with "the game said nothing is wrong".
        r = main._redscript_report(self.root, {})
        self.assertFalse(r["ran"])
        self.assertFalse(r["compiled"])

    def test_a_clean_log_is_a_positive_answer(self):
        self._log(["[INFO] Compiling files"] + self.DONE)
        r = main._redscript_report(self.root, {})
        self.assertTrue(r["ran"])
        self.assertTrue(r["compiled"])
        self.assertEqual(r["failures"], [])

    def test_a_failed_compile_has_no_completion_line(self):
        # Verified against both real logs on device: six errors and no
        # completion, or zero errors and one.
        self._log(self.ERRORS)
        r = main._redscript_report(self.root, {})
        self.assertTrue(r["ran"])
        self.assertFalse(r["compiled"])

    def test_a_failing_script_is_matched_to_the_mod_that_owns_it(self):
        self._put_script()
        self._log(self.ERRORS)
        r = main._redscript_report(self.root, {
            "Shadow Fixes": {
                "name": "Shadow Fixes", "mod_id": 20405, "version": "1.2",
                "files": ["r6/scripts/GeneralShadowsFixes.reds"],
            },
        })
        self.assertEqual(len(r["failures"]), 1)
        self.assertEqual(r["failures"][0]["mod"], "Shadow Fixes")
        self.assertEqual(r["failures"][0]["mod_id"], 20405)
        self.assertEqual(r["orphans"], [])

    def test_a_deleted_script_is_no_longer_reported(self):
        # An uninstall does not rewrite the log and leaves no record behind,
        # so a blamed .reds that has since been removed would otherwise be
        # reported for ever. Michael: "even after I uninstalled it ... the
        # collection is still reporting a script failure".
        self._log(self.ERRORS)
        r = main._redscript_report(self.root, {})
        self.assertEqual(r["failures"], [])
        self.assertEqual(r["orphans"], [])

    def test_a_failing_script_nobody_owns_is_an_orphan(self):
        # The case that cost weeks: two .reds files left by installs whose
        # records were lost, failing every compile with nothing accountable.
        self._put_script()
        self._log(self.ERRORS)
        r = main._redscript_report(self.root, {})
        self.assertEqual(r["failures"], [])
        self.assertEqual(r["orphans"][0]["script"], "GeneralShadowsFixes.reds")
        self.assertEqual(r["orphans"][0]["symbol"], "JobQueue")

    def test_a_mod_shipping_its_script_in_a_subfolder_still_matches(self):
        # The log prints a path under r6/scripts; the record stores where it
        # installed to, and many mods use a folder of their own.
        self._log([
            "[ERROR] [SYNTAX_ERROR] At S:" + chr(92) + "r6" + chr(92)
            + "scripts" + chr(92) + "VendorsXL" + chr(92) + "VendorsXL.reds:1:1:",
        ])
        self._put_script("r6/scripts/VendorsXL/VendorsXL.reds")
        r = main._redscript_report(self.root, {
            "VendorsXL": {"name": "VendorsXL", "mod_id": 19679,
                          "files": ["r6/scripts/VendorsXL/VendorsXL.reds"]},
        })
        self.assertEqual(r["failures"][0]["mod"], "VendorsXL")

    def test_only_the_current_log_is_read(self):
        # redscript rotates the previous session out under a timestamped
        # name. Those are full of problems that have since been fixed, and
        # reporting one is the same bug as a stale "already fixed" line
        # contradicting the findings above it.
        self._log(self.DONE)
        with open(os.path.join(self.root, "r6", "logs",
                               "redscript_r2026-08-14_15-48-23.log"),
                  "w", encoding="utf-8") as f:
            f.write("\n".join(self.ERRORS))
        r = main._redscript_report(self.root, {})
        self.assertTrue(r["compiled"])
        self.assertEqual(r["orphans"], [])


class TestHealthCheck(unittest.TestCase):
    """The screen Michael asked for months ago and was talked out of.

    One day of Slay the Spire 2 settled it: two mods silently did not load
    for want of a library, stale pinned libraries broke four more, mods were
    switched off that had fixes published. All knowable, none surfaced."""

    GAME = "Health Test"

    def setUp(self):
        self.plugin = main.Plugin()
        root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(root, ignore_errors=True)
        self.data = os.path.join(root, "Data")
        os.makedirs(self.data)
        settings = main._load_settings()
        settings.setdefault("installed", {})["newvegas"] = {
            "oHUD": {"mod_id": 44757, "name": "One HUD", "mode": "dataDir"},
            "Rigged Odds": {"mod_id": 65000, "name": "Rigged Odds",
                            "mode": "dataDir"},
        }
        main._save_settings(settings)
        # Stubbed at the GraphQL layer, not at get_mod_requirements, so the
        # batching and the node-splitting are both under test - the health
        # check asks in pages of 20 now rather than once per mod.
        self.reqs = {
            44757: {"requirements": [
                {"modName": "UIO", "modId": 57174, "notes": "", "url": ""},
                {"modName": "MCM", "modId": 42507, "notes": "optional",
                 "url": ""},
                {"modName": "Vanilla UI+", "modId": 0, "notes": "",
                 "url": "moddb.com/mods/vanilla-ui-plus"},
            ], "dlc": []},
            65000: {"requirements": [], "dlc": ["Dead Money"]},
        }
        self.batches = []
        self._orig_gql = main._gql_query
        main._GAME_ID_CACHE["newvegas"] = 130

        async def fake_gql(query, api_key=None):
            ids = [int(m) for m in re.findall(r"modId: (\d+)", query)]
            self.batches.append(ids)
            nodes = []
            for mid in ids:
                spec = self.reqs.get(mid, {"requirements": [], "dlc": []})
                nodes.append({
                    "modId": mid,
                    "modRequirements": {
                        "nexusRequirements": {"nodes": spec["requirements"]},
                        "dlcRequirements": [
                            {"notes": "", "gameExpansion": {"name": d}}
                            for d in spec["dlc"]
                        ],
                    },
                })
            return {"legacyMods": {"nodes": nodes}}

        main._gql_query = fake_gql

    def tearDown(self):
        main._gql_query = self._orig_gql
        main._GAME_ID_CACHE.pop("newvegas", None)
        settings = main._load_settings()
        settings.get("installed", {}).pop("newvegas", None)
        main._save_settings(settings)
        shutil.rmtree(os.path.join(main.STEAM_COMMON, self.GAME),
                      ignore_errors=True)

    def _check(self, framework_ids=None):
        return run(self.plugin.get_health_check(
            "newvegas", self.GAME, "Data", 1, framework_ids))

    def test_a_mod_manager_is_not_a_missing_dependency(self):
        # Resident Evil 4 mods list Fluffy Mod Manager as required. It is a
        # Windows app for installing mods, which is what this plugin does -
        # so there is nothing to fetch, and Michael's three RE4 collections
        # all worked while the health check complained about it.
        for name in ("Fluffy Mod Manager", "Vortex", "Mod Organizer 2"):
            self.reqs[44757]["requirements"] = [
                {"modName": name, "modId": 99, "notes": "", "url": ""}]
            missing = [
                m["name"] for f in self._check()["needs_mods"]
                for m in f["missing"]
            ]
            self.assertEqual(missing, [], name)

    def test_a_real_mod_with_manager_in_its_name_is_still_checked(self):
        # "Generic Mod Config Menu" must not be swept up by the same rule.
        self.reqs[44757]["requirements"] = [
            {"modName": "Generic Mod Config Menu", "modId": 99,
             "notes": "", "url": ""}]
        missing = [
            m["name"] for f in self._check()["needs_mods"]
            for m in f["missing"]
        ]
        self.assertEqual(missing, ["Generic Mod Config Menu"])

    def test_the_framework_is_not_reported_missing(self):
        # It arrives through Step 1, not the mod list, so it is not a
        # tracked mod. Michael's Stardew reported all 77 of its mods as
        # missing SMAPI on a setup that booted perfectly and showed every
        # mod in the config menu. A check that cries wolf 77 times is
        # worse than no check.
        self.reqs[44757]["requirements"].append(
            {"modName": "SMAPI", "modId": 2400, "notes": "", "url": ""})
        missing = [
            m["name"] for f in self._check([2400])["needs_mods"]
            for m in f["missing"]
        ]
        self.assertNotIn("SMAPI", missing)

    def test_a_framework_that_is_not_declared_is_still_reported(self):
        # Passing no framework ids must not silently excuse anything.
        self.reqs[44757]["requirements"].append(
            {"modName": "SMAPI", "modId": 2400, "notes": "", "url": ""})
        missing = [
            m["name"] for f in self._check()["needs_mods"]
            for m in f["missing"]
        ]
        self.assertIn("SMAPI", missing)

    def test_it_asks_in_pages_not_once_per_mod(self):
        # The point of the change: a 500-mod Fallout 3 collection was going
        # to make 500 sequential requests and look hung.
        settings = main._load_settings()
        settings["installed"]["newvegas"].update({
            f"Mod{i}": {"mod_id": 1000 + i, "name": f"Mod{i}",
                        "mode": "dataDir"}
            for i in range(45)
        })
        main._save_settings(settings)
        self._check()
        self.assertEqual([len(b) for b in self.batches], [20, 20, 7])

    def test_a_mod_the_api_skips_is_reported_not_assumed_clean(self):
        # legacyMods truncates silently, so a missing node must never read
        # as "this mod needs nothing".
        async def half_answer(query, api_key=None):
            return {"legacyMods": {"nodes": []}}

        main._gql_query = half_answer
        r = self._check()
        self.assertEqual(r["needs_mods"], [])
        self.assertEqual(len(r["errors"]), 2)

    def test_a_missing_required_mod_is_reported(self):
        r = self._check()
        self.assertTrue(r["ok"], r)
        self.assertEqual(
            [m["name"] for m in r["needs_mods"]], ["One HUD"])
        self.assertEqual(
            [x["name"] for x in r["needs_mods"][0]["missing"]], ["UIO"])

    def test_an_optional_requirement_is_not_a_problem(self):
        r = self._check()
        names = [x["name"] for x in r["needs_mods"][0]["missing"]]
        self.assertNotIn("MCM", names)

    def test_an_installed_requirement_is_not_reported(self):
        settings = main._load_settings()
        settings["installed"]["newvegas"]["UIO"] = {
            "mod_id": 57174, "name": "UIO", "mode": "dataDir"}
        main._save_settings(settings)
        self.assertEqual(self._check()["needs_mods"], [])

    def test_an_alternative_source_is_not_a_missing_requirement(self):
        # Verbatim from device: The Watcher lists "or BaseLib on Github
        # (declared version in description of my files)". That is another
        # way to get a mod already installed from Nexus, not a second thing
        # to fetch - and reporting it sent Michael after something he had.
        self.reqs[44757]["requirements"].append(
            {"modName": "or BaseLib on Github (declared version in "
                        "description of my files)",
             "modId": 0, "notes": "",
             "url": "https://github.com/Alchyr/BaseLib-StS2/releases/"})
        names = [
            f["files"][0]["name"] for f in self._check()["needs_external"]
        ]
        self.assertNotIn(
            "or BaseLib on Github (declared version in description of my "
            "files)", names)

    def test_a_real_off_nexus_file_is_still_reported(self):
        # The distinction has to hold both ways: Vanilla UI+ is a genuine
        # off-site requirement and must survive the "or" rule.
        r = self._check()
        self.assertEqual(r["needs_external"][0]["files"][0]["name"],
                         "Vanilla UI+")

    def test_an_off_nexus_file_is_reported_with_where_to_get_it(self):
        # The Vanilla UI+ case, which cost three failed boots to find by
        # hand. modId 0 with a url means Nexus cannot supply it.
        r = self._check()
        self.assertEqual(r["needs_external"][0]["name"], "One HUD")
        self.assertIn("moddb.com", r["needs_external"][0]["files"][0]["url"])

    def test_missing_dlc_is_reported_by_name(self):
        r = self._check()
        self.assertEqual(r["needs_dlc"], [{"name": "Rigged Odds",
                                           "dlc": ["Dead Money"]}])

    def test_owned_dlc_is_proved_from_disk_not_guessed(self):
        # A master file in Data is the only proof that survives a
        # reinstall, a family share or a regional edition.
        open(os.path.join(self.data, "DeadMoney.esm"), "w").close()
        r = self._check()
        self.assertEqual(r["needs_dlc"], [])
        self.assertIn("Dead Money", r["owned_dlc"])

    def test_a_disabled_mod_is_not_checked(self):
        settings = main._load_settings()
        settings["installed"]["newvegas"]["oHUD"]["enabled"] = False
        main._save_settings(settings)
        self.assertEqual(self._check()["needs_mods"], [])

    def test_dlc_is_only_claimed_where_it_can_be_proved(self):
        # Slay the Spire 2 has no expansions as master files, so a "you
        # need this DLC" warning there would be a guess.
        self.assertTrue(main._dlc_checkable("newvegas"))
        self.assertFalse(main._dlc_checkable("slaythespire2"))

    def test_rejects_a_bad_domain(self):
        self.assertFalse(run(self.plugin.get_health_check(
            "../evil", self.GAME, "Data"))["ok"])

    # --- the two halves must never contradict each other -----------------

    def test_a_fix_for_a_mod_that_is_gone_is_not_reported(self):
        # Michael: KitLib and RitsuLib listed under "sorted out already"
        # while the findings above said LustTravel2 still needed them. A
        # stale line contradicting the findings is worse than no line.
        settings = main._load_settings()
        settings.setdefault("auto_fixed", {})["newvegas"] = [
            {"name": "Ghost Mod", "for": "One HUD"},
            {"name": "One HUD", "for": "Rigged Odds"},
        ]
        main._save_settings(settings)
        try:
            names = [e["name"] for e in self._check()["already_fixed"]]
            self.assertNotIn("Ghost Mod", names)
            self.assertIn("One HUD", names)
        finally:
            settings = main._load_settings()
            settings.get("auto_fixed", {}).pop("newvegas", None)
            main._save_settings(settings)

    def test_reset_forgets_what_it_fixed_but_keeps_what_it_learned(self):
        # The distinction that got confused: a verdict is knowledge about a
        # game build and a mod version, true whatever is installed. A fix
        # log is a list of things done to THIS install, and reset destroys
        # the install.
        settings = main._load_settings()
        settings.setdefault("auto_fixed", {})["newvegas"] = [
            {"name": "RitsuLib", "for": "LustTravel2"}]
        main._save_settings(settings)
        main._record_mod_verdicts("newvegas", "b1", [
            {"mod_id": 284, "version": "1.0", "name": "X", "why": "y"}])
        try:
            run(self.plugin.reset_game_modding(
                "newvegas", self.GAME, "Data", "dataDir", 1))
            after = main._load_settings()
            self.assertEqual(
                (after.get("auto_fixed") or {}).get("newvegas"), None)
            self.assertIn(284, main._known_broken_mods("newvegas", "b1"))
        finally:
            settings = main._load_settings()
            settings.get("auto_fixed", {}).pop("newvegas", None)
            settings.get("mod_verdicts", {}).pop("newvegas", None)
            main._save_settings(settings)


class TestHealthCheckCorroboration(unittest.TestCase):
    """Ask the game, do not infer.

    Welcome to Night City installs 283 Cyberpunk mods and deliberately omits
    seven their pages call required. It boots, and its script stack compiles
    clean. Reported as faults, those seven sent Michael to install General
    Shadows Fixes - whose orphaned .reds was the thing breaking his game.
    A curator's omissions are not the discriminator; the game's log is."""

    GAME = "Corroboration Test"
    DONE = ["[INFO] Compiling files", "[INFO] Compilation complete"]
    BROKEN = [
        "[ERROR] [UNRESOLVED_REF] At S:" + chr(92) + "r6" + chr(92)
        + "scripts" + chr(92) + "GeneralShadowsFixes.reds:7094:20:",
        "    let jobQueue = JobQueue.Create();",
        "                   ^^^^^^^^",
        "unresolved reference 'JobQueue'",
    ]

    def setUp(self):
        self.plugin = main.Plugin()
        self.root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "r6", "logs"))
        os.makedirs(os.path.join(self.root, "archive", "pc", "mod"))
        settings = main._load_settings()
        settings.setdefault("installed", {})["cyberpunk2077"] = {
            "One More Light": {
                "mod_id": 9001, "name": "One More Light", "mode": "files",
                "target": ".",
                "source": "collection", "collection_slug": "iszwwe",
                "files": ["archive/pc/mod/oml.archive"],
            },
        }
        settings.pop("mod_verdicts", None)
        main._save_settings(settings)
        main._GAME_ID_CACHE["cyberpunk2077"] = 3333
        self._orig_gql = main._gql_query
        self._orig_build = main._steam_build_id
        main._steam_build_id = lambda app_id: "23811903"
        # The real finding: mod 20405 "General Shadows Fixes", required by
        # exactly one installed mod, absent from a 283-mod set that boots.
        self.reqs = {9001: [
            {"modName": "General Shadows Fixes", "modId": 20405,
             "notes": "", "url": ""},
        ]}

        async def fake_gql(query, api_key=None):
            ids = [int(m) for m in re.findall(r"modId: (\d+)", query)]
            return {"legacyMods": {"nodes": [{
                "modId": mid,
                "modRequirements": {
                    "nexusRequirements": {"nodes": self.reqs.get(mid, [])},
                    "dlcRequirements": [],
                },
            } for mid in ids]}}

        main._gql_query = fake_gql

    def tearDown(self):
        main._gql_query = self._orig_gql
        main._steam_build_id = self._orig_build
        main._GAME_ID_CACHE.pop("cyberpunk2077", None)
        settings = main._load_settings()
        settings.get("installed", {}).pop("cyberpunk2077", None)
        settings.get("mod_verdicts", {}).pop("cyberpunk2077", None)
        main._save_settings(settings)
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(
            os.path.join(main.decky.DECKY_PLUGIN_RUNTIME_DIR, "parked",
                         "cyberpunk2077"),
            ignore_errors=True)

    def _put_script(self, name="GeneralShadowsFixes.reds"):
        """The blamed .reds, actually on disk.

        A failure whose file has been deleted is not reported any more - an
        uninstall leaves no record behind, so nothing else would tell us the
        problem is gone.
        """
        d = os.path.join(self.root, "r6", "scripts")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, name), "w").close()

    def _log(self, lines):
        with open(main._redscript_log_path(self.root), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _check(self):
        return run(self.plugin.get_health_check(
            "cyberpunk2077", self.GAME, "archive/pc/mod", 1091500, None))

    # --- the discriminator ------------------------------------------------

    def test_no_log_leaves_the_finding_exactly_as_it_was(self):
        # The state the other nine games are permanently in. Nothing about
        # this work may change what they report.
        r = self._check()
        self.assertEqual(len(r["needs_mods"]), 1)
        self.assertEqual(r["needs_mods_info"], [])
        self.assertFalse(r["script_log"]["ran"])

    def test_a_clean_compile_demotes_a_collections_omission(self):
        self._log(self.DONE)
        r = self._check()
        self.assertEqual(r["needs_mods"], [])
        self.assertEqual(len(r["needs_mods_info"]), 1)
        self.assertEqual(
            r["needs_mods_info"][0]["missing"][0]["name"],
            "General Shadows Fixes")

    def test_a_hand_installed_mod_is_still_a_problem(self):
        # The curator's decision is the whole justification for demoting
        # this. Somebody who installed one mod themselves has made no such
        # decision and genuinely is missing a dependency.
        settings = main._load_settings()
        rec = settings["installed"]["cyberpunk2077"]["One More Light"]
        rec["source"] = ""
        rec.pop("collection_slug", None)
        main._save_settings(settings)
        self._log(self.DONE)
        r = self._check()
        self.assertEqual(len(r["needs_mods"]), 1)
        self.assertEqual(r["needs_mods_info"], [])

    def test_a_blamed_mod_stays_a_problem_even_from_a_collection(self):
        # Corroboration cuts both ways: the game complaining about this
        # mod's own script is exactly when a collection's omission IS the
        # fault, which is the Stardew case in Cyberpunk's clothing.
        settings = main._load_settings()
        settings["installed"]["cyberpunk2077"]["One More Light"]["files"] = [
            "r6/scripts/GeneralShadowsFixes.reds"]
        main._save_settings(settings)
        self._put_script()
        self._log(self.BROKEN)
        r = self._check()
        self.assertEqual(r["needs_mods_info"], [])

    # --- never recommend the thing that broke the game --------------------

    def test_an_orphan_named_after_a_required_mod_becomes_a_verdict(self):
        # GeneralShadowsFixes.reds, owned by no record, and mod 20405
        # "General Shadows Fixes" listed as required. The file is that mod's
        # script left behind by an install whose record was lost - so the
        # mod has already been tried here and does not compile.
        self._put_script()
        self._log(self.BROKEN)
        r = self._check()
        self.assertIn(20405,
                      main._verdicts_for_build("cyberpunk2077", "23811903"))
        suggested = [
            m["name"] for f in r["needs_mods"] for m in f["missing"]
        ]
        self.assertNotIn("General Shadows Fixes", suggested)
        self.assertEqual(r["known_bad"][0]["name"], "General Shadows Fixes")

    def test_a_mod_the_collection_found_deleted_is_never_suggested(self):
        # Fallout 4's health check recommended "Glowing Eyes - DELETED" and
        # "More Clothes and Textures" - the second being one the collection
        # run had JUST skipped as a 404. What one part of the plugin
        # learns, the rest should not have to rediscover.
        settings = main._load_settings()
        settings.setdefault("collection_attention", {})["cyberpunk2077"] = {
            "iszwwe": [{
                "file_id": 1, "mod_id": 20405,
                "mod_name": "General Shadows Fixes",
                "reason": "unavailable", "options": [],
            }],
        }
        main._save_settings(settings)
        try:
            r = self._check()
            self.assertEqual(r["needs_mods"], [])
            self.assertEqual(r["known_bad"][0]["mod_id"], 20405)
            self.assertIn("no longer available", r["known_bad"][0]["why"])
        finally:
            settings = main._load_settings()
            settings.get("collection_attention", {}).pop("cyberpunk2077", None)
            main._save_settings(settings)

    def test_a_mod_with_a_verdict_is_never_suggested_again(self):
        main._record_mod_verdicts("cyberpunk2077", "23811903", [
            {"mod_id": 20405, "name": "General Shadows Fixes",
             "version": "", "why": "its script does not compile"}])
        r = self._check()
        self.assertEqual(r["needs_mods"], [])
        self.assertEqual(r["known_bad"][0]["for"], "One More Light")

    def test_a_verdict_from_another_build_does_not_silence_the_check(self):
        # A game update is the most likely thing to have fixed a mod, so the
        # verdict retires with the build rather than becoming a blacklist.
        main._record_mod_verdicts("cyberpunk2077", "OLD", [
            {"mod_id": 20405, "name": "General Shadows Fixes",
             "version": "", "why": "x"}])
        self.assertEqual(len(self._check()["needs_mods"]), 1)

    # --- acting on it -----------------------------------------------------

    def test_a_mod_whose_script_killed_the_compile_is_switched_off(self):
        # One bad .reds stops EVERY script mod loading, so this is not a
        # judgement call - it is the single mod standing between the user
        # and everything else they installed.
        settings = main._load_settings()
        settings["installed"]["cyberpunk2077"]["One More Light"]["files"] = [
            "r6/scripts/GeneralShadowsFixes.reds"]
        main._save_settings(settings)
        os.makedirs(os.path.join(self.root, "r6", "scripts"), exist_ok=True)
        open(os.path.join(self.root, "r6", "scripts",
                          "GeneralShadowsFixes.reds"), "w").close()
        self._log(self.BROKEN)
        r = self._check()
        self.assertEqual(r["script_log"]["switched_off"][0]["name"],
                         "One More Light")
        after = main._load_settings()["installed"]["cyberpunk2077"]
        self.assertIs(after["One More Light"]["enabled"], False)
        # And the file is genuinely out of the game's way, not just flagged.
        self.assertFalse(os.path.exists(os.path.join(
            self.root, "r6", "scripts", "GeneralShadowsFixes.reds")))

    def test_nothing_is_switched_off_when_the_compile_finished(self):
        # "errored but finished anyway" has never been observed on device.
        # Acting on a state nobody has seen is how a check starts crying
        # wolf, so that case is reported and left alone.
        settings = main._load_settings()
        settings["installed"]["cyberpunk2077"]["One More Light"]["files"] = [
            "r6/scripts/GeneralShadowsFixes.reds"]
        main._save_settings(settings)
        self._put_script()
        self._log(self.BROKEN + ["[INFO] Compilation complete"])
        r = self._check()
        self.assertEqual(r["script_log"]["switched_off"], [])
        self.assertEqual(len(r["script_log"]["failures"]), 1)

    def test_a_mod_the_user_turned_back_on_is_left_on(self):
        # The fight a user cannot win. We switch a mod off, they decide they
        # want it anyway and switch it back on - and the very next look at
        # this page switches it off again, because the log has not changed
        # and cannot until the game runs. Nothing on screen would explain
        # it. Acted-on-once per log; the next session gets a fresh say.
        settings = main._load_settings()
        settings["installed"]["cyberpunk2077"]["One More Light"]["files"] = [
            "r6/scripts/GeneralShadowsFixes.reds"]
        main._save_settings(settings)
        os.makedirs(os.path.join(self.root, "r6", "scripts"), exist_ok=True)
        open(os.path.join(self.root, "r6", "scripts",
                          "GeneralShadowsFixes.reds"), "w").close()
        self._log(self.BROKEN)
        self.assertEqual(
            len(self._check()["script_log"]["switched_off"]), 1)
        # The user turns it back on.
        settings = main._load_settings()
        rec = settings["installed"]["cyberpunk2077"]["One More Light"]
        rec["enabled"] = True
        rec.pop("parked", None)
        main._save_settings(settings)
        self.assertEqual(self._check()["script_log"]["switched_off"], [])
        after = main._load_settings()["installed"]["cyberpunk2077"]
        self.assertIsNot(after["One More Light"]["enabled"], False)

    def test_a_fresh_session_that_still_fails_switches_it_off_again(self):
        # The other half: the guard is per LOG, not permanent. If the game
        # runs again and says the same thing, that is a new statement.
        settings = main._load_settings()
        settings["installed"]["cyberpunk2077"]["One More Light"]["files"] = [
            "r6/scripts/GeneralShadowsFixes.reds"]
        main._save_settings(settings)
        os.makedirs(os.path.join(self.root, "r6", "scripts"), exist_ok=True)
        open(os.path.join(self.root, "r6", "scripts",
                          "GeneralShadowsFixes.reds"), "w").close()
        self._log(self.BROKEN)
        self._check()
        settings = main._load_settings()
        rec = settings["installed"]["cyberpunk2077"]["One More Light"]
        rec["enabled"] = True
        rec.pop("parked", None)
        main._save_settings(settings)
        # A new session writes a new log - different size is enough here.
        self._log(self.BROKEN + ["[INFO] another line from a later run"])
        # Parking pruned the empty directory on the way out.
        os.makedirs(os.path.join(self.root, "r6", "scripts"), exist_ok=True)
        open(os.path.join(self.root, "r6", "scripts",
                          "GeneralShadowsFixes.reds"), "w").close()
        self.assertEqual(
            len(self._check()["script_log"]["switched_off"]), 1)

    # --- evidence has a shelf life -----------------------------------------

    def _install_after_the_log(self):
        """Make the newest install newer than the log, as installing a
        collection does."""
        settings = main._load_settings()
        rec = settings["installed"]["cyberpunk2077"]["One More Light"]
        rec["installed_at"] = int(os.path.getmtime(
            main._redscript_log_path(self.root))) + 60
        main._save_settings(settings)

    def test_a_log_older_than_the_last_install_is_marked_stale(self):
        # Michael: a collection failed to compile blaming ScorpionTank, he
        # uninstalled it, installed one he knew worked, and the page still
        # reported the failure. "I booted the game to check and it booted
        # fine so the health report was stale."
        self._log(self.BROKEN)
        self._install_after_the_log()
        r = self._check()
        self.assertTrue(r["script_log"]["stale"])

    def test_a_stale_log_writes_no_verdicts(self):
        # The durable harm. A verdict from a log describing mods that are no
        # longer installed blacklists a mod for the whole game build.
        self._log(self.BROKEN)
        self._install_after_the_log()
        self._check()
        self.assertEqual(
            main._verdicts_for_build("cyberpunk2077", "23811903"), {})

    def test_a_stale_log_switches_nothing_off(self):
        settings = main._load_settings()
        settings["installed"]["cyberpunk2077"]["One More Light"]["files"] = [
            "r6/scripts/GeneralShadowsFixes.reds"]
        main._save_settings(settings)
        os.makedirs(os.path.join(self.root, "r6", "scripts"), exist_ok=True)
        open(os.path.join(self.root, "r6", "scripts",
                          "GeneralShadowsFixes.reds"), "w").close()
        self._log(self.BROKEN)
        self._install_after_the_log()
        r = self._check()
        self.assertEqual(r["script_log"]["switched_off"], [])
        after = main._load_settings()["installed"]["cyberpunk2077"]
        self.assertIsNot(after["One More Light"].get("enabled"), False)

    def test_a_stale_clean_log_does_not_vouch_for_the_new_mods(self):
        # The demotion rests on the game having said it is happy WITH THESE
        # mods. A clean compile from before they arrived says nothing.
        self._log(self.DONE)
        self._install_after_the_log()
        r = self._check()
        self.assertEqual(r["needs_mods_info"], [])
        self.assertEqual(len(r["needs_mods"]), 1)

    def test_a_failure_whose_script_is_gone_is_not_reported(self):
        # The other half of staleness, and the one an uninstall causes: no
        # record is left behind, so no timestamp moves, but the .reds the
        # log blames has been deleted and cannot still break anything.
        self._log(self.BROKEN)
        r = self._check()
        self.assertEqual(r["script_log"]["orphans"], [])
        self.assertEqual(r["script_log"]["failures"], [])

    def test_a_mod_already_switched_off_is_not_switched_off_again(self):
        # The log does not change when a mod is disabled - the session that
        # blamed it already happened - so without this every check would
        # report the same repair for ever.
        settings = main._load_settings()
        rec = settings["installed"]["cyberpunk2077"]["One More Light"]
        rec["files"] = ["r6/scripts/GeneralShadowsFixes.reds"]
        rec["enabled"] = False
        main._save_settings(settings)
        self._log(self.BROKEN)
        self.assertEqual(self._check()["script_log"]["switched_off"], [])


class TestFilesModeToggle(unittest.TestCase):
    """Cyberpunk mods are loose files across five game directories, so there
    is no folder to move aside - and this used to answer "no toggle,
    uninstall it instead".

    That was the wrong answer to a real question: one .reds that will not
    compile takes every script mod with it, and the remedy is to stop
    loading one file, not to throw away a download."""

    GAME = "Files Mode Test"

    def setUp(self):
        self.plugin = main.Plugin()
        self.root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "r6", "scripts"))
        for name in ("a.reds", "b.reds"):
            open(os.path.join(self.root, "r6", "scripts", name), "w").close()
        settings = main._load_settings()
        settings.setdefault("installed", {})["filestest"] = {
            "Mod A": {"mod_id": 1, "name": "Mod A", "mode": "files",
                      "target": ".",
                      "files": ["r6/scripts/a.reds"]},
            "Mod B": {"mod_id": 2, "name": "Mod B", "mode": "files",
                      "target": ".",
                      "files": ["r6/scripts/b.reds"]},
        }
        main._save_settings(settings)

    def tearDown(self):
        settings = main._load_settings()
        settings.get("installed", {}).pop("filestest", None)
        main._save_settings(settings)
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(
            os.path.join(main.decky.DECKY_PLUGIN_RUNTIME_DIR, "parked",
                         "filestest"),
            ignore_errors=True)

    def _set(self, folder, enabled):
        return run(self.plugin.set_mod_enabled(
            self.GAME, "archive/pc/mod", folder, enabled, "folder",
            "filestest", 0, "", "starred", None))

    def test_switching_off_moves_the_files_out_of_the_game(self):
        self.assertTrue(self._set("Mod A", False)["ok"])
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "r6", "scripts", "a.reds")))
        # And only its own: the other mod is untouched.
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "r6", "scripts", "b.reds")))

    def test_switching_back_on_puts_them_where_they_were(self):
        self._set("Mod A", False)
        self.assertTrue(self._set("Mod A", True)["ok"])
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "r6", "scripts", "a.reds")))
        rec = main._load_settings()["installed"]["filestest"]["Mod A"]
        self.assertIsNot(rec.get("parked"), True)

    def test_a_file_another_mod_also_claims_is_left_alone(self):
        # 283 mods dropping files into five shared directories is exactly
        # where two records name the same path, and whoever wrote last owns
        # the copy on disk. Moving it would gut the other mod.
        settings = main._load_settings()
        settings["installed"]["filestest"]["Mod B"]["files"] = [
            "r6/scripts/b.reds", "r6/scripts/a.reds"]
        main._save_settings(settings)
        r = self._set("Mod A", False)
        self.assertEqual(r["shared"], 1)
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "r6", "scripts", "a.reds")))


class TestCollectionNeedsDowngrade(unittest.TestCase):
    """A collection that needs an older game says so, and we never read it.

    A StoryWealth is the #1 Fallout 4 collection - 908 mods, 115 GB, 14,662
    endorsements - and step 6 of its instructions is "Downgrade the game!".
    Michael installed all of it, booted, and got seven F4SE plugins refused
    as "incompatible with the current version of the game" and a crash
    before the main menu. The requirement was in the description the plugin
    already downloads."""

    # Lifted from the live description, 2026-08-15.
    REAL = (
        "ab and in the top bar click DEPLOY MODS. *** ## Step 6. Downgrade "
        "the game! *GOG.com users can skip this step as GOG still uses the "
        "old version of the game.* **If you w"
    )
    REAL_TOOL = (
        'Go to the Dashboard and find the "**Fallout 4 Downgrader**".\\ '
        "*Let the patcher do it's job for a few seconds. Done.*"
    )
    REAL_FILE = (
        "Make sure to grab the **(Not Next-Gen compatible)** version. For "
        "file-conflicts the mod of your choice should go **After**."
    )

    def test_it_finds_the_downgrade_instruction(self):
        self.assertIn("Downgrade the game",
                      main._collection_downgrade_reason(self.REAL))

    def test_it_finds_the_named_tool(self):
        self.assertTrue(main._collection_downgrade_reason(self.REAL_TOOL))

    def test_it_finds_the_not_next_gen_file_note(self):
        self.assertTrue(main._collection_downgrade_reason(self.REAL_FILE))

    def test_the_quote_is_a_sentence_not_a_url_fragment(self):
        # The first live run quoted "ivery.nexusmods.com/mods/1151/images/
        # 68877-1759206362-988431549.jpg) *** ## Step 6. Downgrade the
        # game!" at the user, because these descriptions are mostly
        # markdown images.
        quote = main._collection_downgrade_reason(
            "![clean](https://staticdelivery.nexusmods.com/mods/1151/images/"
            "68877-1759206362-988431549.jpg) *** ## Step 6. Downgrade the "
            "game! *GOG.com users can skip this step.*"
        )
        self.assertTrue(quote)
        self.assertNotIn("http", quote)
        self.assertNotIn(".jpg", quote)
        self.assertNotIn("![", quote)
        self.assertIn("Downgrade the game", quote)

    def test_it_quotes_the_curator_rather_than_paraphrasing(self):
        # Someone about to lose a 115 GB download deserves the actual
        # sentence, not our summary of it.
        quote = main._collection_downgrade_reason(self.REAL)
        self.assertIn("GOG", quote)
        self.assertGreater(len(quote), 40)

    def test_html_markup_does_not_hide_the_instruction(self):
        self.assertTrue(main._collection_downgrade_reason(
            "<h2>Step 6. <b>Downgrade</b> the game!</h2>"
        ) or main._collection_downgrade_reason(
            "<h2>Step 6. Downgrade the game!</h2>"
        ))

    def test_a_collection_that_needs_no_downgrade_is_left_alone(self):
        # The opposite statement appears just as often, and blocking a
        # working collection is worse than the problem.
        for text in (
            "You do not need to downgrade the game for this collection.",
            "No need to downgrade the game - it is next-gen compatible.",
            "This collection is Next-Gen compatible out of the box.",
            "Install the mods in order and launch.",
            "",
        ):
            self.assertEqual(
                main._collection_downgrade_reason(text), "", text)

    def test_the_check_never_blocks_when_the_lookup_fails(self):
        # It exists to save a download, not to become one more thing that
        # can go wrong.
        plugin = main.Plugin()
        orig = main._gql_query_vars

        async def boom(*a, **k):
            raise RuntimeError("nexus is down")

        main._gql_query_vars = boom
        try:
            r = run(plugin.get_collection_support("fallout4", "5atq9t"))
        finally:
            main._gql_query_vars = orig
        self.assertTrue(r["ok"])
        self.assertTrue(r["supported"])

    def test_a_downgrade_collection_is_refused_before_the_download(self):
        plugin = main.Plugin()
        orig = main._gql_query_vars

        async def fake(query, variables, api_key=None):
            return {"collection": {"name": "A StoryWealth",
                                   "description": self.REAL}}

        main._gql_query_vars = fake
        try:
            r = run(plugin.get_collection_support("fallout4", "5atq9t"))
        finally:
            main._gql_query_vars = orig
        self.assertFalse(r["supported"])
        self.assertTrue(r["needs_downgrade"])
        self.assertIn("older version", r["reason"].lower())
        self.assertIn("Downgrade the game", r["reason"])

    def test_a_downgrade_collection_is_flagged_not_hidden(self):
        # Michael: the store page "is kind of a highlights page and we
        # shouldn't show things you can't install". The description comes
        # back on the SAME list query, so this costs no extra requests.
        plugin = main.Plugin()
        orig = main._gql_query_vars

        async def fake(query, variables, api_key=None):
            self.assertIn("description", query)
            return {"collectionsV2": {"nodes": [
                {"name": "A StoryWealth", "slug": "5atq9t",
                 "description": self.REAL,
                 "latestPublishedRevision": {"modCount": 908}},
                {"name": "Such Fallout 4", "slug": "u6moyd",
                 "description": "Install the mods and play.",
                 "latestPublishedRevision": {"modCount": 112}},
            ]}}

        main._gql_query_vars = fake
        try:
            r = run(plugin.get_collections("fallout4", 10, "", "endorsements", 0))
        finally:
            main._gql_query_vars = orig
        # Warned, not hidden: an advanced user may know their setup and
        # want to pick through it. Hiding it takes that choice away and
        # explains nothing.
        names = [c["name"] for c in r["collections"]]
        self.assertEqual(names, ["A StoryWealth", "Such Fallout 4"])
        flagged = {c["name"]: c.get("needs_older_game")
                   for c in r["collections"]}
        self.assertTrue(flagged["A StoryWealth"])
        self.assertFalse(flagged["Such Fallout 4"])

    def test_the_row_refills_instead_of_coming_back_short(self):
        # Hiding two of Fallout 4's top collections turned a row of eight
        # into a row of three. Michael: "it should fill with the top
        # collections that can be installed". The filter must cost the
        # blocked entries their place, not the whole row its size.
        plugin = main.Plugin()
        orig = main._gql_query_vars
        seen = []

        async def fake(query, variables, api_key=None):
            off = variables["offset"]
            take = variables["count"]
            seen.append((off, take))
            nodes = []
            for i in range(off, off + take):
                bad = i % 3 == 0          # every third needs a downgrade
                nodes.append({
                    "name": f"C{i}", "slug": f"s{i}",
                    "description": self.REAL if bad else "Just install it.",
                    "latestPublishedRevision": {"modCount": 10},
                })
            return {"collectionsV2": {"nodes": nodes}}

        main._gql_query_vars = fake
        try:
            r = run(plugin.get_collections("fallout4", 8, "", "endorsements", 0))
        finally:
            main._gql_query_vars = orig
        self.assertEqual(len(r["collections"]), 8, "row came back short")
        self.assertTrue(all("C" in c["name"] for c in r["collections"]))
        # And it reports how far the SOURCE got, or the caller re-requests
        # rows it has already shown.
        self.assertGreater(r["next_offset"], 8)
        self.assertGreaterEqual(len(seen), 1)

    def test_a_catalogue_that_all_needs_downgrading_is_all_flagged(self):
        # This used to assert an empty row, back when such collections
        # were hidden. Warning beats hiding, so the row is full and every
        # tile carries the mark.
        plugin = main.Plugin()
        orig = main._gql_query_vars
        calls = []

        async def all_bad(query, variables, api_key=None):
            calls.append(variables["offset"])
            return {"collectionsV2": {"nodes": [
                {"name": f"B{i}", "slug": f"b{i}",
                 "description": self.REAL,
                 "latestPublishedRevision": {"modCount": 1}}
                for i in range(variables["count"])
            ]}}

        main._gql_query_vars = all_bad
        try:
            r = run(plugin.get_collections("fallout4", 8, "", "endorsements", 0))
        finally:
            main._gql_query_vars = orig
        self.assertEqual(len(r["collections"]), 8)
        self.assertTrue(
            all(c.get("needs_older_game") for c in r["collections"]))
        self.assertLessEqual(len(calls), main.COLLECTION_BACKFILL_ROUNDS)

    def test_it_stops_when_the_source_runs_out(self):
        # A short page from the API means there is no more list, and asking
        # again for the same nothing is just latency.
        plugin = main.Plugin()
        orig = main._gql_query_vars
        calls = []

        async def few(query, variables, api_key=None):
            calls.append(variables["offset"])
            return {"collectionsV2": {"nodes": [
                {"name": "Only", "slug": "only",
                 "description": "fine",
                 "latestPublishedRevision": {"modCount": 1}}
            ]}}

        main._gql_query_vars = few
        try:
            r = run(plugin.get_collections("fallout4", 8, "", "endorsements", 0))
        finally:
            main._gql_query_vars = orig
        self.assertEqual(len(r["collections"]), 1)
        self.assertFalse(r["collections"][0].get("needs_older_game"))
        self.assertEqual(len(calls), 1, "kept asking after the list ended")

    def test_the_hand_written_table_still_wins(self):
        # VeryLastKiss's TTW is refused for a different reason entirely, and
        # a network lookup must not get the chance to overrule it.
        plugin = main.Plugin()
        r = run(plugin.get_collection_support("newvegas", "3fs9zx"))
        self.assertFalse(r["supported"])
        self.assertIn("Tale of Two Wastelands", r["reason"])


class TestBrowseHidesKnownBrokenMods(unittest.TestCase):
    """The browse rows recommend things. A mod this device has already
    watched fail on the build it is running should not be one of them."""

    DOMAIN = "browsetest"
    BUILD = "555000"

    def setUp(self):
        self._orig_build = main._steam_build_id
        main._steam_build_id = lambda app_id: self.BUILD if app_id else ""
        main._record_mod_verdicts(self.DOMAIN, self.BUILD, [
            {"mod_id": 284, "name": "Relics Reminder", "version": "1.1.0",
             "why": "1,056 MissingMethodExceptions"}])

    def tearDown(self):
        main._steam_build_id = self._orig_build
        settings = main._load_settings()
        settings.get("mod_verdicts", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)

    def _mods(self):
        return [{"modId": 284, "name": "Relics Reminder"},
                {"modId": 103, "name": "BaseLib"}]

    def test_a_mod_with_a_broken_verdict_is_hidden(self):
        kept, hidden = main._hide_known_broken(self.DOMAIN, 1, self._mods())
        self.assertEqual([m["name"] for m in kept], ["BaseLib"])
        self.assertEqual(hidden, ["Relics Reminder"])

    def test_without_an_app_id_nothing_is_hidden(self):
        # A caller that has not been updated must get exactly what it
        # always got.
        kept, hidden = main._hide_known_broken(self.DOMAIN, 0, self._mods())
        self.assertEqual(len(kept), 2)
        self.assertEqual(hidden, [])

    def test_a_verdict_from_another_build_does_not_hide_it(self):
        # A game update retires the verdict and the mod comes straight back
        # onto the page, same rule as everywhere else.
        main._steam_build_id = lambda app_id: "999999"
        kept, _hidden = main._hide_known_broken(self.DOMAIN, 1, self._mods())
        self.assertEqual(len(kept), 2)

    def test_search_is_unaffected_by_the_highlights_rule(self):
        # Hiding is a recommendation decision, not a pretence the mod does
        # not exist - anyone searching for it by name still finds it.
        import re as _re
        src = open(main.__file__, encoding="utf-8").read()
        start = src.index("def _hide_known_broken")
        # Whitespace-normalised: the sentence wraps across source lines.
        window = _re.sub(r"\s+", " ", src[start:start + 1200]).lower()
        self.assertIn("still reachable by search", window)


class TestDownloadSurvivesConnectionDrops(unittest.TestCase):
    """The device fell off the wifi 79% into a 521-mod, 119 GB collection.

    Four downloads froze at 12:35 and were still frozen two and a half hours
    later. Pause and resume could not shift them: the pause flag is checked
    once per chunk and no chunk ever arrived, so the coroutine never got
    control back to see it. Only a read timeout can break that, and there
    was none - the request carried total=1800 and no sock_read.

    Michael: "although it was an accident, it is a very valid real world
    test so we need to be able to handle connection drops"."""

    def _download_source(self):
        src = open(main.__file__, encoding="utf-8").read()
        start = src.index("_DL_ACTIVE.add(mod_id)")
        return src[start:src.index("async def _validate_key")]

    def test_a_silent_socket_is_noticed_in_seconds_not_half_an_hour(self):
        body = self._download_source()
        self.assertIn("sock_read=DOWNLOAD_STALL_SECONDS", body)
        self.assertLessEqual(main.DOWNLOAD_STALL_SECONDS, 90)

    def test_a_healthy_long_download_is_never_killed_on_duration(self):
        # total=1800 also aborted a legitimate 4 GB file on slow wifi at
        # thirty minutes. Silence is the thing worth measuring, not length.
        body = self._download_source()
        self.assertIn("total=None", body)
        self.assertNotIn("total=1800", body)

    def test_it_absorbs_an_outage_long_enough_for_wifi_to_return(self):
        # Three tries two seconds apart covered a hiccup and nothing else,
        # so an outage shorter than a kettle boil killed the collection.
        self.assertGreaterEqual(main.DOWNLOAD_TRANSPORT_RETRIES, 6)
        total = sum(
            main._transport_backoff(i)
            for i in range(1, main.DOWNLOAD_TRANSPORT_RETRIES)
        )
        self.assertGreaterEqual(total, 120, "gives up inside two minutes")

    def test_backoff_is_capped_so_it_does_not_double_into_hours(self):
        for attempt in range(1, 20):
            self.assertLessEqual(main._transport_backoff(attempt), 60)
        self.assertLess(
            main._transport_backoff(1), main._transport_backoff(4),
            "backoff does not back off",
        )

    def test_every_retry_resumes_from_the_part_file(self):
        # A 119 GB collection cannot restart files from zero on every
        # wobble. The Range header and the .part are what make an outage
        # cost seconds instead of the whole download.
        body = self._download_source()
        self.assertIn('{"Range": f"bytes={part_now}-"}', body)
        self.assertIn("resuming from the part file", body)

    def test_the_user_is_told_rather_than_left_watching_a_frozen_bar(self):
        # A stalled bar that says nothing is indistinguishable from a hang,
        # which is exactly why pause/resume got pressed.
        body = self._download_source()
        self.assertIn("connection lost - retrying in", body)

    def test_a_retry_reports_the_bytes_actually_on_disk(self):
        # Michael pulled the wifi and watched the bar drop to zero. The
        # retry reported part_now - the size when the ATTEMPT started,
        # which is 0 for a file being fetched for the first time - so it
        # claimed no progress at the exact moment the user most needs to
        # see that theirs is safe.
        body = self._download_source()
        retry = body[body.index("connection lost - retrying in") - 900:]
        self.assertIn("os.path.getsize(part_path)", retry)
        self.assertIn("bytes_done=have", retry)
        self.assertNotIn("bytes_done=part_now", retry)


class TestScriptExtenderFailuresNameTheMod(unittest.TestCase):
    """The extender reports filenames. A player needs mods.

    Michael, booting Vault Boy 101 (521 mods) on Fallout 4:

        po3_SpellPerkItemDistributorF4.dll: disabled, incompatible with the
        current version of the game

    Nothing in that names the mod to update, the mod to switch off, or even
    which mod it is - and it arrived on a black screen. The install records
    already know which mod wrote which file."""

    # Verbatim from the device's f4se.log.
    LINES = [
        "F4SE runtime: initialize (version = 0.7.8 010B0DD0, os = 6.2)",
        "checking plugin Buffout4.dll",
        "plugin Buffout4.dll (00000000  00000000) no version data 0 (handle 0)",
        "checking plugin po3_SpellPerkItemDistributorF4.dll",
        "plugin po3_SpellPerkItemDistributorF4.dll (00000001 SPID 01000000) "
        "disabled, incompatible with current version of the game 0 (handle 0)",
    ]

    def setUp(self):
        self.dir = os.path.join(TEST_ROOT, "se-log")
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        self.log = os.path.join(self.dir, "f4se.log")
        with open(self.log, "w", encoding="utf-8") as f:
            f.write("\n".join(self.LINES))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_refused_plugin_is_named_by_its_mod(self):
        got = main._se_failures_with_owners(self.log, {
            "Spell Perk Item Distributor": {
                "name": "Spell Perk Item Distributor", "mod_id": 48365,
                "files": [
                    "F4SE/Plugins/po3_SpellPerkItemDistributorF4.dll",
                    "F4SE/Plugins/po3_SpellPerkItemDistributorF4.ini",
                ],
            },
        })
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["mod"], "Spell Perk Item Distributor")
        self.assertEqual(got[0]["mod_id"], 48365)
        self.assertEqual(got[0]["dll"], "po3_SpellPerkItemDistributorF4.dll")

    def test_a_version_mismatch_is_marked_as_the_authors_problem(self):
        # "Only its author can fix this" and "something it needs is
        # missing" lead to completely different advice, so the distinction
        # has to survive to the page.
        got = main._se_failures_with_owners(self.log, {})
        self.assertTrue(got[0]["outdated"])

    def test_a_plugin_no_record_owns_still_gets_reported(self):
        # Better a filename than silence: it is still the reason the game
        # showed a modal.
        got = main._se_failures_with_owners(self.log, {})
        self.assertEqual(got[0]["mod"], "")
        self.assertEqual(got[0]["dll"], "po3_SpellPerkItemDistributorF4.dll")

    def test_plugins_that_loaded_are_not_reported(self):
        # 44 of 57 loaded on device. Reporting those would bury the two
        # that matter.
        got = main._se_failures_with_owners(self.log, {})
        self.assertNotIn("Buffout4.dll", [g["dll"] for g in got])

    def test_no_log_means_nothing_to_say(self):
        self.assertEqual(
            main._se_failures_with_owners(
                os.path.join(self.dir, "absent.log"), {}), [])

    def test_only_version_mismatches_are_set_aside(self):
        # Parking one changes nothing about what loads - the game had
        # already refused it - it only stops the modal before every main
        # menu. But "failed to load" is often a missing dependency and may
        # still be repairable here, so those are reported and left alone.
        src = open(main.__file__, encoding="utf-8").read()
        start = src.index("se_parked.append")
        window = src[start - 900:start]
        self.assertIn('if not f["outdated"]:', window)
        self.assertIn("continue", window)
        self.assertIn("SE_DISABLED_SUFFIX", window)

    def test_the_parked_file_is_renamed_never_deleted(self):
        # The extender only scans *.dll, so a suffix is enough - and the
        # user can always have the mod back.
        src = open(main.__file__, encoding="utf-8").read()
        start = src.index("se_parked.append")
        window = src[start - 400:start]
        self.assertIn("os.replace(live, live + SE_DISABLED_SUFFIX)", window)
        self.assertNotIn("os.remove", window)


class TestCollectionLoadOrder(unittest.TestCase):
    """Load order IS file order in plugins.txt, and the collection ships it.

    Vault Boy 101's manifest carries 417 plugin entries already in the
    curator's order. We read that list as a SET to decide what to enable
    and threw the sequence away, so the load order came out as whatever
    install order produced. In-game that reads as mods fighting: Michael's
    run hung on Unlimited Companion Framework's own warning to "move EFF
    further down your load order"."""

    def setUp(self):
        self.dir = os.path.join(TEST_ROOT, "loadorder")
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        self.path = os.path.join(self.dir, "Plugins.txt")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, lines):
        main._write_plugins_txt(self.path, lines)

    def _read(self):
        return main._read_plugins_txt(self.path)

    def test_it_applies_the_curators_sequence(self):
        self._write(["*EFF.esp", "*UCF.esp"])
        moved = main._reorder_plugins(self.path, ["UCF.esp", "EFF.esp"])
        self.assertEqual(moved, 2)
        self.assertEqual(self._read(), ["*UCF.esp", "*EFF.esp"])

    def test_it_never_switches_anything_on_or_off(self):
        # The marker travels with its line, so reordering cannot activate
        # a plugin the user had switched off.
        self._write(["*EFF.esp", "UCF.esp"])
        main._reorder_plugins(self.path, ["UCF.esp", "EFF.esp"])
        self.assertEqual(self._read(), ["UCF.esp", "*EFF.esp"])

    def test_a_mod_the_user_added_is_left_exactly_where_it_was(self):
        # Only the collection's own plugins are permuted, and only across
        # the positions they already occupy.
        self._write(["*EFF.esp", "*MyOwnMod.esp", "*UCF.esp"])
        main._reorder_plugins(self.path, ["UCF.esp", "EFF.esp"])
        self.assertEqual(
            self._read(), ["*UCF.esp", "*MyOwnMod.esp", "*EFF.esp"])

    def test_comments_and_blanks_survive(self):
        self._write(["# This file is used by Fallout 4", "", "*B.esp",
                     "*A.esp"])
        main._reorder_plugins(self.path, ["A.esp", "B.esp"])
        self.assertEqual(
            self._read(),
            ["# This file is used by Fallout 4", "", "*A.esp", "*B.esp"])

    def test_a_plugin_the_collection_does_not_name_is_untouched(self):
        self._write(["*Zed.esp", "*A.esp"])
        moved = main._reorder_plugins(self.path, ["A.esp"])
        self.assertEqual(moved, 0)
        self.assertEqual(self._read(), ["*Zed.esp", "*A.esp"])

    def test_an_already_correct_order_moves_nothing(self):
        # The steady state. Rewriting the file every launch would churn
        # mtimes on the timestamp-ordered engines for no reason.
        self._write(["*A.esp", "*B.esp", "*C.esp"])
        moved = main._reorder_plugins(
            self.path, ["A.esp", "B.esp", "C.esp"])
        self.assertEqual(moved, 0)

    def test_case_differences_do_not_defeat_it(self):
        # Plugins.txt casing comes from the game, the manifest's from the
        # curator, and they disagree constantly.
        self._write(["*eff.ESP", "*Ucf.esp"])
        main._reorder_plugins(self.path, ["UCF.esp", "EFF.esp"])
        self.assertEqual(self._read(), ["*Ucf.esp", "*eff.ESP"])

    def test_it_is_applied_only_where_file_order_is_load_order(self):
        # FO3/New Vegas order by file TIMESTAMP; rewriting their plugins
        # file would achieve nothing and churn it every run.
        import inspect
        src = inspect.getsource(main.Plugin.apply_collection_plugins)
        cut = src.index("_reorder_plugins")
        self.assertIn('plugins_style != "listed"', src[cut - 400:cut])


class TestUninstallNeverEatsGameFiles(unittest.TestCase):
    """A mod's record must never be able to delete the game's own files.

    Fallout 4, 2026-08-15: a 451-mod collection installed perfectly, the
    load order was applied, the game booted and started a new game - and
    every surface rendered magenta. All nine of "Fallout4 - Textures1.ba2"
    through "Textures9.ba2" were missing, removed when a reset uninstalled
    records that listed them as their own files. Mod-supplied hair and eyes
    drew correctly; skin, clothing and walls had no textures at all.

    _game_owned_name already recognised them. It guarded the leftover sweep
    and was never asked here."""

    DOMAIN = "fallout4"

    def setUp(self):
        self.data = os.path.join(TEST_ROOT, "eatgame", "Data")
        shutil.rmtree(os.path.dirname(self.data), ignore_errors=True)
        os.makedirs(self.data)
        for n in ("Fallout4 - Textures1.ba2", "Fallout4 - Meshes.ba2",
                  "DLCCoast - Main.ba2", "TotallyAMod.ba2",
                  "SomeMod - Textures.ba2"):
            with open(os.path.join(self.data, n), "w") as f:
                f.write("x")

    def tearDown(self):
        shutil.rmtree(os.path.dirname(self.data), ignore_errors=True)

    def _remove(self, files):
        settings = {"installed": {self.DOMAIN: {"Greedy Mod": {
            "mode": "dataDir", "name": "Greedy Mod", "files": files,
        }}}}
        main._remove_data_dir_record(
            self.DOMAIN, "Greedy Mod", self.data, 0, "", settings)

    def test_a_vanilla_texture_archive_survives(self):
        self._remove(["Fallout4 - Textures1.ba2"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.data, "Fallout4 - Textures1.ba2")))

    def test_every_vanilla_archive_shape_survives(self):
        self._remove(["Fallout4 - Meshes.ba2", "DLCCoast - Main.ba2"])
        for n in ("Fallout4 - Meshes.ba2", "DLCCoast - Main.ba2"):
            self.assertTrue(os.path.isfile(os.path.join(self.data, n)), n)

    def test_the_mod_s_own_files_are_still_removed(self):
        # The guard must not turn uninstall into a no-op.
        self._remove(["TotallyAMod.ba2", "SomeMod - Textures.ba2"])
        for n in ("TotallyAMod.ba2", "SomeMod - Textures.ba2"):
            self.assertFalse(os.path.isfile(os.path.join(self.data, n)), n)

    def test_a_mixed_record_loses_only_its_own(self):
        # The real shape of the bug: one record claiming both.
        self._remove(["Fallout4 - Textures1.ba2", "TotallyAMod.ba2"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.data, "Fallout4 - Textures1.ba2")))
        self.assertFalse(os.path.isfile(
            os.path.join(self.data, "TotallyAMod.ba2")))


class TestAddressLibraryVersion(unittest.TestCase):
    """One fact behind a whole screen of DLL failures.

    "Such Fallout 4" (112 mods) installed cleanly, then asked for a newer
    Address Library, refused six plugins and crashed on the way in. Its
    description says nothing about downgrading, so the description check
    could not catch it - but the filesystem states it plainly:

        Data/F4SE/Plugins/version-1-10-163-0.bin   library for 1.10.163
        f4se_1_11_221.dll                          loader for 1.11.221

    Every plugin built on that library fails, and they are not individually
    broken - they all fail for the same one reason."""

    def setUp(self):
        self.root = os.path.join(TEST_ROOT, "addrlib")
        shutil.rmtree(self.root, ignore_errors=True)
        self.plugins = os.path.join(self.root, "Data", "F4SE", "Plugins")
        os.makedirs(self.plugins)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _put(self, root_files=(), lib_files=()):
        for n in root_files:
            open(os.path.join(self.root, n), "w").close()
        for n in lib_files:
            open(os.path.join(self.plugins, n), "w").close()

    def test_it_reads_both_versions_off_disk(self):
        # The exact filenames from the device.
        self._put(["f4se_1_11_221.dll"], ["version-1-10-163-0.bin"])
        got = main._address_library_state(self.root, self.plugins)
        self.assertEqual(got["runtime"], "1.11.221")
        self.assertEqual(got["have"], ["1.10.163"])
        self.assertFalse(got["matches"])

    def test_a_matching_library_is_not_a_problem(self):
        self._put(["f4se_1_11_221.dll"], ["version-1-11-221-0.bin"])
        self.assertTrue(
            main._address_library_state(self.root, self.plugins)["matches"])

    def test_several_libraries_pass_if_one_fits(self):
        # Users accumulate these; only one has to match.
        self._put(["f4se_1_11_221.dll"],
                  ["version-1-10-163-0.bin", "version-1-11-221-0.bin"])
        self.assertTrue(
            main._address_library_state(self.root, self.plugins)["matches"])

    def test_no_library_at_all_is_not_a_problem(self):
        # Plenty of setups never need one, and inventing a fault there is
        # exactly the crying wolf this check exists to avoid.
        self._put(["f4se_1_11_221.dll"], [])
        self.assertTrue(
            main._address_library_state(self.root, self.plugins)["matches"])

    def test_no_script_extender_is_not_a_problem(self):
        self._put([], ["version-1-10-163-0.bin"])
        self.assertTrue(
            main._address_library_state(self.root, self.plugins)["matches"])

    def test_it_reads_skyrims_loader_too(self):
        self._put(["skse64_1_6_1170.dll"], ["version-1-6-640-0.bin"])
        got = main._address_library_state(self.root, self.plugins)
        self.assertEqual(got["runtime"], "1.6.1170")
        self.assertFalse(got["matches"])

    def test_nothing_is_set_aside_while_the_library_is_wrong(self):
        # Parking six plugins one by one would hide the single reason all
        # six failed behind six green ticks.
        src = open(main.__file__, encoding="utf-8").read()
        cut = src.index("for f in se_failed if addrlib")
        self.assertIn('if addrlib["matches"] else []', src[cut:cut + 120])


class TestAddressLibraryTargetBeforeDownload(unittest.TestCase):
    """Read the target build off the collection, not off the wreckage.

    "Such Fallout 4" says nothing about downgrading, so the description
    check passed it, and only after 57 GB and a crash did the version files
    on disk disagree. The collection page already knew: every pinned file
    arrives with its name and version before a byte is downloaded, and the
    Address Library names the build it was built for."""

    def test_it_reads_the_target_from_the_pinned_version(self):
        files = [
            {"modName": "Some Weapon Mod", "fileName": "gun.7z",
             "version": "2.1"},
            {"modName": "Address Library for F4SE Plugins",
             "fileName": "Address Library-47327-1-10-163-0-171.7z",
             "version": "1.10.163.0"},
        ]
        self.assertEqual(main._address_library_target(files), "1.10.163")

    def test_a_mod_id_is_never_mistaken_for_a_game_build(self):
        # The filename carries the mod id first: 47327-1-10-163-0 parses as
        # 47327.1.10 unless five-digit leads are rejected.
        files = [{"modName": "Address Library",
                  "fileName": "Address Library-47327-1-10-163-0-171.7z",
                  "version": ""}]
        self.assertNotEqual(main._address_library_target(files), "47327.1.10")

    def test_a_collection_without_one_says_nothing(self):
        # Most collections have no Address Library at all, and inventing a
        # target for them would block them for nothing.
        self.assertEqual(main._address_library_target([
            {"modName": "Just A Mod", "fileName": "a.7z", "version": "1.0"},
        ]), "")
        self.assertEqual(main._address_library_target([]), "")

    def test_the_runtime_comes_from_the_installed_extender(self):
        root = os.path.join(TEST_ROOT, "sertest")
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root)
        try:
            open(os.path.join(root, "f4se_1_11_221.dll"), "w").close()
            self.assertEqual(main._script_extender_runtime(root), "1.11.221")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_no_extender_installed_means_no_opinion(self):
        root = os.path.join(TEST_ROOT, "sertest-empty")
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root)
        try:
            self.assertEqual(main._script_extender_runtime(root), "")
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestBlockedCollectionsLeaveTheStore(unittest.TestCase):
    """The two "this cannot work here" checks must agree.

    Michael: "I cant see any marked knowing they wont work." The
    description check HIDES a collection from the store list; the Address
    Library check only spoke when you opened its page. So Such Fallout 4 -
    which says nothing about downgrading and needs 1.10.163 - sat in the
    list looking perfectly normal after the plugin had already refused it
    once."""

    def test_a_flagged_collection_is_still_offered_with_a_warning(self):
        plugin = main.Plugin()
        settings = main._load_settings()
        settings.setdefault("collection_blocked", {})["blocktest"] = {
            "u6moyd": {"target": "1.10.163", "runtime": "1.11.221"},
        }
        main._save_settings(settings)
        orig = main._gql_query_vars

        async def fake(query, variables, api_key=None):
            return {"collectionsV2": {"nodes": [
                {"name": "Such Fallout 4", "slug": "u6moyd",
                 "description": "Just install it.",
                 "latestPublishedRevision": {"modCount": 112}},
                {"name": "Fine One", "slug": "okok",
                 "description": "Just install it.",
                 "latestPublishedRevision": {"modCount": 10}},
            ]}}

        main._gql_query_vars = fake
        try:
            r = run(plugin.get_collections(
                "blocktest", 8, "", "endorsements", 0))
        finally:
            main._gql_query_vars = orig
            settings = main._load_settings()
            settings.get("collection_blocked", {}).pop("blocktest", None)
            main._save_settings(settings)
        # Warned, NOT hidden. Michael: "I dont know if the collection
        # should dissapear, that feels a little too far" - somebody who
        # knows their setup cannot act on something they cannot see.
        names = [c["name"] for c in r["collections"]]
        self.assertEqual(names, ["Such Fallout 4", "Fine One"])
        flagged = {c["name"]: c.get("needs_older_game") for c in r["collections"]}
        self.assertTrue(flagged["Such Fallout 4"])
        self.assertFalse(flagged["Fine One"])

    def test_nothing_is_hidden_before_anything_has_looked(self):
        # The block is LEARNED. A collection nobody has opened is offered
        # normally - we do not pretend to know what we have not checked.
        plugin = main.Plugin()
        orig = main._gql_query_vars

        async def fake(query, variables, api_key=None):
            return {"collectionsV2": {"nodes": [
                {"name": "Unknown One", "slug": "newslug",
                 "description": "Just install it.",
                 "latestPublishedRevision": {"modCount": 5}},
            ]}}

        main._gql_query_vars = fake
        try:
            r = run(plugin.get_collections(
                "blocktest", 8, "", "endorsements", 0))
        finally:
            main._gql_query_vars = orig
        self.assertEqual(len(r["collections"]), 1)


class TestBundledMe3LoaderIsIgnored(unittest.TestCase):
    """The Convergence bundles me3 itself, for both platforms.

    Michael, testing Elden Ring: "i installed a mod organiser fine but then
    failed on instlaling mod the convergence". The refusal read:

        The Convergence contains several versions of the same mod
        (me3/Linux/win64/me3_mod_host.dll, me3/Windows/me3_mod_host.dll)

    Those are not versions of a mod. They are per-platform builds of the
    LOADER, shipped so a Windows user has one download - and we install our
    own me3 and launch through it. So the biggest Elden Ring overhaul there
    is was refused outright, and the user was told to pick a version that
    does not exist as a choice."""

    def setUp(self):
        self.scratch = os.path.join(TEST_ROOT, "me3-conv")
        shutil.rmtree(self.scratch, ignore_errors=True)
        os.makedirs(self.scratch)

    def tearDown(self):
        shutil.rmtree(self.scratch, ignore_errors=True)

    def put(self, rel):
        p = os.path.join(self.scratch, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()

    def test_the_bundled_loader_is_recognised(self):
        for d in ("me3", "me3/Linux/win64", "ModEngine2", "modengine"):
            self.assertTrue(main._me3_bundled_loader(d), d)

    def test_real_mod_folders_are_not(self):
        for d in ("natives", "parts", "chr", "mods", "regulation"):
            self.assertFalse(main._me3_bundled_loader(d), d)

    def test_the_convergence_shape_installs(self):
        # The real archive: mod content plus a bundled me3 for two
        # platforms.
        self.put("regulation.bin")
        self.put("parts/aaa.partsbnd.dcx")
        self.put("me3/Linux/win64/me3_mod_host.dll")
        self.put("me3/Windows/me3_mod_host.dll")
        root, assets, dlls, err = main._route_me3_payload(
            self.scratch, "The Convergence")
        self.assertIsNone(err, err)
        # The loader's dlls are not offered as natives...
        self.assertEqual(dlls, [])
        # ...and the mod's own content is what gets installed.
        self.assertIsNotNone(root)

    def test_a_genuine_option_pack_is_still_refused(self):
        # The safeguard must survive: two copies of one early-load native
        # crash the game, and choosing for the user is a guess.
        self.put("Full version/ersc.dll")
        self.put("Lite version/ersc.dll")
        _root, _assets, _dlls, err = main._route_me3_payload(
            self.scratch, "Some Mod")
        self.assertIsNotNone(err)
        self.assertEqual(err[0], "choice")


class TestWitcherExpansionsAreProvable(unittest.TestCase):
    """Owning Blood & Wine is a directory check, not a guess.

    Michael was running base-game Witcher 3 and the plugin could not say
    so: a mod needing Blood & Wine installed silently and did nothing,
    which looks exactly like a broken mod. The expansions live in dlc/ as
    "ep1" and "bob", so this is as provable as a Bethesda master file -
    reached a different way."""

    def setUp(self):
        self.root = os.path.join(TEST_ROOT, "w3dlc")
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "dlc"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _add(self, *folders):
        for f in folders:
            os.makedirs(os.path.join(self.root, "dlc", f), exist_ok=True)

    def test_base_game_owns_no_expansions(self):
        self.assertEqual(main._owned_expansions("witcher3", self.root), set())

    def test_each_expansion_is_recognised_by_its_folder(self):
        self._add("ep1")
        self.assertEqual(
            main._owned_expansions("witcher3", self.root),
            {"Hearts of Stone"},
        )
        self._add("bob")
        self.assertEqual(
            main._owned_expansions("witcher3", self.root),
            {"Hearts of Stone", "Blood and Wine"},
        )

    def test_a_mod_folder_is_not_an_expansion(self):
        self._add("modFriendlyHUD", "dlc1", "dlcFriendlyMeditation")
        self.assertEqual(main._owned_expansions("witcher3", self.root), set())

    def test_witcher_3_can_now_be_asked_about_dlc_at_all(self):
        # It was excluded, so every DLC requirement there went unchecked.
        self.assertTrue(main._dlc_checkable("witcher3"))
        self.assertTrue(main._dlc_checkable("newvegas"))
        # And a game whose expansions we cannot prove still says nothing.
        self.assertFalse(main._dlc_checkable("slaythespire2"))
        self.assertFalse(main._dlc_checkable("cyberpunk2077"))

    def test_a_missing_dlc_folder_is_not_an_error(self):
        shutil.rmtree(os.path.join(self.root, "dlc"))
        self.assertEqual(main._owned_expansions("witcher3", self.root), set())


class TestWitcherOfficialDlcSurvivesNewReleases(unittest.TestCase):
    """A hardcoded DLC list goes stale the day the game gains one.

    Michael, 2026-08-16: "There is a big piece of DLC coming soon so we
    need to be prepared for that." The guard listed dlc1 to dlc16, so a
    seventeenth would have been taken for a mod folder and deleted - the
    New Vegas incident with different filenames. The comment above it even
    records that an earlier version "destroyed Blood & Wine and every free
    DLC" and needed a Steam verify."""

    def test_the_dlc_that_exist_today_are_protected(self):
        for name in ("dlc1", "dlc16", "bob", "ep1", "DLC9", "Bob"):
            self.assertTrue(main._w3_official_dlc(name), name)

    def test_a_dlc_released_tomorrow_is_protected_too(self):
        for name in ("dlc17", "dlc20", "dlc99", "dlc4a"):
            self.assertTrue(main._w3_official_dlc(name), name)

    def test_a_mod_folder_is_still_removable(self):
        # Mods conventionally use a "mod" prefix; deleting nothing would be
        # as bad as deleting everything.
        for name in ("modFriendlyHUD", "modLimitlessHorse", "dlcmod",
                     "mydlc", "", "dlc"):
            self.assertFalse(main._w3_official_dlc(name), repr(name))


class TestNoMangledRegexEscapes(unittest.TestCase):
    """Word boundaries that had been replaced by actual backspace bytes.

    Editing main.py through a shell heredoc turns "\\b" into 0x08, and the
    result still compiles, still imports, and reads correctly in an editor -
    it just never matches. Three had been sitting in shipped code:

      \\bMO2\\b  \\bNMM\\b   so those two mod managers were never filtered
                          out of the health check's missing requirements
      \\s*or\\b   so "or BaseLib on Github" - an alternative SOURCE for a
                          mod already installed - was reported as a missing
                          off-Nexus file

    Nothing catches this by reading. A test has to."""

    def test_no_control_characters_anywhere_in_the_backend(self):
        raw = open(main.__file__, encoding="utf-8", newline="").read()
        for ch, name in ((chr(8), "backspace"), (chr(11), "vertical tab"),
                         (chr(12), "form feed")):
            self.assertNotIn(
                ch, raw,
                f"main.py contains a raw {name} - almost certainly a "
                f"regex escape mangled by a shell heredoc",
            )

    def test_no_control_characters_in_the_frontend_either(self):
        # The guard covered main.py only, so the same heredoc mangled a \b
        # in panelRules.ts into 0x08 and the requirement-notes regex silently
        # matched nothing. TypeScript compiles it, the editor renders it, and
        # only a passing-looking test failing revealed it. Every source file
        # this project edits through a shell now gets the same check.
        # main.py sits AT the repo root, so one dirname, not two.
        root = os.path.dirname(os.path.abspath(main.__file__))
        src = os.path.join(root, "src")
        if not os.path.isdir(src):
            self.skipTest("no src/ here - partial checkout")
        checked = 0
        for folder, _dirs, names in os.walk(src):
            for name in names:
                if not name.endswith((".ts", ".tsx")):
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8", newline="") as fh:
                    raw = fh.read()
                checked += 1
                for ch, label in ((chr(8), "backspace"),
                                  (chr(11), "vertical tab"),
                                  (chr(12), "form feed")):
                    self.assertNotIn(
                        ch, raw,
                        f"src/{name} contains a raw {label} - almost "
                        "certainly a regex escape mangled by a shell heredoc",
                    )
        # Any src/ at all must yield files; the Linux run copies a subset,
        # so this asserts "the walk worked", not a file count.
        self.assertGreater(checked, 0, "found no frontend sources to check")

    def test_the_short_mod_manager_names_are_recognised(self):
        for name in ("MO2", "NMM", "Fluffy Mod Manager", "Vortex",
                     "Mod Organizer 2"):
            self.assertTrue(
                main._MANAGER_REQUIREMENT_RE.search(name), name)

    def test_a_real_mod_is_not_swept_up_with_them(self):
        # The word boundary is the whole point: these must NOT match.
        for name in ("Generic Mod Config Menu", "Nemo2", "MO2Tools",
                     "NMMirror"):
            self.assertIsNone(
                main._MANAGER_REQUIREMENT_RE.search(name), name)


class TestVerifiedOnDeck(unittest.TestCase):
    """A collection is verified when somebody PLAYED it.

    Michael, on a screenshot of Fallout 4 rendering every surface magenta:
    "I think we jumped the gun on celebrating the install". That collection
    had installed 451 of 454 mods, applied its load order, booted, and
    reached a new game. Every signal the plugin could see said it worked.

    So the evidence comes from outside the plugin - Steam's own playtime,
    which cannot be faked by installing successfully."""

    def _entry(self, at=1000, playtime_at=50):
        return {"at": at, "playtime_at": playtime_at, "build": "b1"}

    def test_installing_is_not_verification(self):
        # Never launched: LastPlayed predates the install and no minutes
        # have been added.
        self.assertEqual(
            main._collection_verified_state(self._entry(), 900, 50),
            "installed",
        )

    def test_booting_is_not_verification_either(self):
        # This is the exact state that fooled us: it started, and it was
        # broken.
        self.assertEqual(
            main._collection_verified_state(self._entry(), 1500, 50),
            "booted",
        )

    def test_a_couple_of_minutes_is_still_not_verification(self):
        # Long enough to reach a menu and quit, which proves nothing.
        self.assertEqual(
            main._collection_verified_state(self._entry(), 1500, 53),
            "booted",
        )

    def test_real_playtime_after_the_install_earns_it(self):
        self.assertEqual(
            main._collection_verified_state(
                self._entry(), 1500, 50 + main.VERIFIED_PLAYED_MINUTES),
            "played",
        )

    def test_play_from_BEFORE_the_install_does_not_count(self):
        # 400 hours of vanilla Fallout says nothing about this collection,
        # which is why the playtime at install time is recorded.
        entry = self._entry(playtime_at=24000)
        self.assertEqual(
            main._collection_verified_state(entry, 900, 24000), "installed")

    def test_a_game_update_retires_the_badge(self):
        # Same rule as every other verdict here: after a patch nobody knows
        # whether a 500-mod setup still works.
        src = open(main.__file__, encoding="utf-8").read()
        start = src.index("async def get_collection_verdicts")
        window = src[start:start + 2000]
        self.assertIn('entry["build"] != build', window)
        self.assertIn("continue", window)

    def test_the_recorded_entry_carries_what_it_needs(self):
        src = open(main.__file__, encoding="utf-8").read()
        start = src.index("def _record_collection_verdict")
        window = src[start:start + 1800]
        for field in ('"playtime_at"', '"build"', '"at"', '"plugin_version"'):
            self.assertIn(field, window, field)


class TestPrefixToolRestaging(unittest.TestCase):
    """A tool already staged by an earlier successful run must be reused,
    not treated as an obstacle.

    Michael ran the Fallout 3 ESM patcher successfully weeks ago, so its exe
    was sitting in the game folder. Every attempt since failed with "already
    exists in the game folder - not overwriting it" - failing for the single
    reason that it had already worked. He remembered FO3 working and could
    not see why it now would not."""

    def test_the_guard_no_longer_fails_on_our_own_file(self):
        src = os.path.join(REPO_ROOT, "main.py")
        body = open(src, encoding="utf-8").read()
        start = body.index("async def run_prefix_tool")
        window = body[start:start + 20000]
        self.assertIn("already staged from an earlier run", window)
        # The failure it used to raise is gone: stage_err is now only ever
        # set by something that is genuinely a problem.
        self.assertNotIn('stage_err = (', window)

    def test_a_reused_file_is_still_cleaned_up_afterwards(self):
        # v0.154.0 stopped the run failing on an already-staged exe but
        # never added it back to the unstage list, so Patcher.exe, its
        # readme and xdelta3.* accumulated in the game folder for days.
        body = open(os.path.join(REPO_ROOT, "main.py"),
                    encoding="utf-8").read()
        start = body.index("already staged from an earlier run")
        window = body[start:start + 700]
        self.assertIn("staged.append(dst)", window)

    def test_it_reuses_rather_than_skipping_silently(self):
        # Reusing means pointing exe_path at the staged copy. Skipping
        # without that leaves the tool with nothing to run, which would be
        # a quieter version of the same bug.
        body = open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read()
        start = body.index("already staged from an earlier run")
        self.assertIn("exe_path = dst", body[start:start + 400])


class TestDirectCollectionSources(unittest.TestCase):
    """A collection can list a plain URL instead of a Nexus file, and
    those were being dropped without a word.

    Fallout Rebirth+ has exactly one: FOSE, from fose.silverlock.org,
    marked optional: false. It is the script extender the whole collection
    runs on. 168 mods installed "with no mods left hanging" and the game
    crashed on launch with nothing to look at."""

    ENTRY = {
        "name": "Fallout Script Extender (FOSE)",
        "optional": False,
        "details": {"type": "dinput"},
        "source": {
            "type": "direct",
            "url": "http://fose.silverlock.org/beta/fose_v1_3_beta2.7z",
            "md5": "a4672b55b502de3482ecc71f27bd174a",
            "fileSize": 360250,
        },
    }

    def test_a_direct_source_is_seen_at_all(self):
        got = main._collection_extras({"mods": [self.ENTRY]})["direct"]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["name"], "Fallout Script Extender (FOSE)")
        self.assertEqual(got[0]["md5"],
                         "a4672b55b502de3482ecc71f27bd174a")
        self.assertEqual(got[0]["size"], 360250)
        self.assertFalse(got[0]["optional"])

    def test_a_dinput_injector_is_marked_as_one(self):
        # FOSE lives beside the game exe, not in Data. Putting it in Data
        # would install it precisely nowhere useful.
        got = main._collection_extras({"mods": [self.ENTRY]})["direct"]
        self.assertEqual(got[0]["kind"], "dinput")

    def test_a_direct_entry_with_no_url_is_ignored(self):
        entry = json.loads(json.dumps(self.ENTRY))
        entry["source"]["url"] = ""
        self.assertEqual(
            main._collection_extras({"mods": [entry]})["direct"], [])

    def test_the_other_source_types_still_work(self):
        got = main._collection_extras({"mods": [
            self.ENTRY,
            {"name": "B", "source": {"type": "browse", "url": "x"}},
            {"name": "C", "source": {"type": "bundle",
                                     "fileExpression": "c"}},
            {"name": "D", "source": {"type": "nexus"}},
        ]})
        self.assertEqual(len(got["direct"]), 1)
        self.assertEqual(len(got["browse"]), 1)
        self.assertEqual(len(got["bundle"]), 1)


class TestDirectDownloadVerification(unittest.TestCase):
    """The one place the plugin fetches from a host that is not Nexus,
    over a URL a third party wrote, to a site with no certificate to
    check. The curator's md5 is what makes that defensible."""

    def test_a_non_http_url_is_refused(self):
        for url in ("file:///etc/passwd", "ftp://x/y", "", "javascript:x"):
            err, path = run(main._download_direct_file(url, "", 0))
            self.assertTrue(err, url)
            self.assertEqual(path, "")

    def test_the_checks_are_ordered_size_then_hash(self):
        # Both must be capable of failing the download on their own.
        src = open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read()
        start = src.index("async def _download_direct_file")
        body = src[start:start + 3000]
        self.assertIn("wrong size", body)
        self.assertIn("checksum did not match", body)
        # And a mismatch must delete the file rather than leave it around
        # for something else to find.
        self.assertEqual(body.count("os.remove(dest)"), 3)

    def test_a_declared_size_is_also_a_ceiling(self):
        # Without it a redirect to something enormous fills the deck
        # before anything gets as far as checking the hash.
        src = open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read()
        start = src.index("async def _download_direct_file")
        self.assertIn("far larger than declared", src[start:start + 3000])


class TestLaunchOptionsStayCurrent(unittest.TestCase):
    """"Applied ✓" has to mean "applied, and still what we would apply".

    Fallout 3's launch command grew a FOSE branch and the step showed a
    tick with no button. Michael: "I cant press step 1 again." A tick
    nobody can undo is worse than no tick."""

    DOMAIN = "lotest"
    OLD = "bash -c 'exec a' -- %command%"
    NEW = "bash -c 'exec b' -- %command%"

    def setUp(self):
        self.plugin = main.Plugin()

    def tearDown(self):
        settings = main._load_settings()
        settings.get("framework_setup", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)

    def test_a_matching_command_reads_as_current(self):
        run(self.plugin.mark_launch_options_set(self.DOMAIN, self.OLD))
        r = run(self.plugin.get_framework_setup(self.DOMAIN, self.OLD))
        self.assertTrue(r["launch_options_set"])
        self.assertTrue(r["launch_options_current"])

    def test_a_changed_template_offers_the_step_again(self):
        run(self.plugin.mark_launch_options_set(self.DOMAIN, self.OLD))
        r = run(self.plugin.get_framework_setup(self.DOMAIN, self.NEW))
        self.assertTrue(r["launch_options_set"])
        self.assertFalse(r["launch_options_current"])

    def test_re_applying_makes_it_current_again(self):
        run(self.plugin.mark_launch_options_set(self.DOMAIN, self.OLD))
        run(self.plugin.mark_launch_options_set(self.DOMAIN, self.NEW))
        self.assertTrue(run(self.plugin.get_framework_setup(
            self.DOMAIN, self.NEW))["launch_options_current"])

    def test_a_setup_from_before_this_existed_is_left_alone(self):
        # No recorded value means we cannot tell, and nagging every user
        # who set theirs up months ago would be worse than staying quiet.
        settings = main._load_settings()
        settings.setdefault("framework_setup", {})[self.DOMAIN] = {
            "launch_options_set": True, "enabled": True}
        main._save_settings(settings)
        self.assertTrue(run(self.plugin.get_framework_setup(
            self.DOMAIN, self.NEW))["launch_options_current"])

    def test_no_expectation_never_reports_stale(self):
        # me3 has no template to compare against.
        run(self.plugin.mark_launch_options_set(self.DOMAIN, self.OLD))
        self.assertTrue(run(self.plugin.get_framework_setup(
            self.DOMAIN, ""))["launch_options_current"])

    def test_whitespace_alone_is_not_a_change(self):
        run(self.plugin.mark_launch_options_set(self.DOMAIN, self.OLD))
        self.assertTrue(run(self.plugin.get_framework_setup(
            self.DOMAIN, f"  {self.OLD}  "))["launch_options_current"])


class TestPrefixToolEarlyFinish(unittest.TestCase):
    """A patcher that finishes its work and then sits at a prompt must not
    hold the step open for the whole timeout.

    The Anniversary Patcher rewrote Fallout3.exe about 90 seconds in and
    then waited at "press any key" that never comes headless, so the button
    stayed busy for the full three minutes. Michael: "step 3 seems to have
    gotten stuck"."""

    @staticmethod
    def _body():
        """The whole function, not a fixed-size slice of it.

        These tests used to read src[start:start + 14000] and started
        failing the moment the function grew past that - which looked like
        a broken timeout rather than a short window."""
        src = open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read()
        start = src.index("async def run_prefix_tool")
        end = src.index(chr(10) + "    async def ", start + 10)
        return src[start:end]

    def test_it_watches_the_verify_files_rather_than_the_process(self):
        body = self._body()
        self.assertIn("_changed_now", body)
        self.assertIn("closing the tool rather than", body)

    def test_it_waits_for_the_files_to_go_quiet_not_merely_change(self):
        # The regression this replaced: killing on first change wrote a
        # 15MB exe half way through, right size and not a program.
        body = self._body()
        self.assertIn("_TOOL_QUIET_SECONDS", body)
        self.assertIn("quiet < _TOOL_QUIET_SECONDS", body)
        self.assertIn("_fingerprint_now", body)

    def test_the_quiet_period_is_longer_than_a_poll(self):
        # A one-poll wait would be no better than killing on first change.
        src = open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read()
        self.assertIn("_TOOL_QUIET_SECONDS = ", src)
        value = int(
            src.split("_TOOL_QUIET_SECONDS = ")[1].split(chr(10))[0]
        )
        self.assertGreaterEqual(value, 6)

    def test_the_timeout_is_still_enforced(self):
        # A tool that changes nothing must still be killed, or the step
        # hangs forever instead of failing honestly.
        body = self._body()
        self.assertIn("if waited >= budget:", body)
        self.assertIn("timed_out = True", body)

    def test_the_process_tree_is_killed_either_way(self):
        # Killing only the proton wrapper orphaned Patcher.exe on device.
        body = self._body()
        self.assertEqual(body.count("_kill_tree()"), 3)
        self.assertIn("os.killpg", body)


class TestResetFindsOrphansOutsideTheModsFolder(unittest.TestCase):
    """Cyberpunk mods write into five directories and reset looked at one.

    Two orphaned .reds files in r6/scripts - owned by no install record,
    left by an install whose record was lost - had been failing redscript
    compilation for weeks. One bad .reds disables EVERY script mod, so the
    whole script stack was dead with nothing accounting for the cause, and
    no reset could find them."""

    GAME = "Orphan Test"
    DOMAIN = "orphantest"
    DIRS = ["r6/scripts", "red4ext/plugins", "r6/tweaks"]
    # Declared as a directory this game's mods write into, but one a VANILLA
    # install does not have - which is true of four of Cyberpunk's five, and
    # was the whole reason they never got a baseline.
    ABSENT = "r6/tweaks"

    def setUp(self):
        self.root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.root, ignore_errors=True)
        for d in ["archive/pc/mod"] + [
            x for x in self.DIRS if x != self.ABSENT
        ]:
            os.makedirs(os.path.join(self.root, *d.split("/")))
        # Vanilla content in one of them, recorded before any mod lands.
        with open(os.path.join(self.root, "r6", "scripts", "vanilla.reds"),
                  "w") as f:
            f.write("game")
        main._record_vanilla_baseline(
            self.DOMAIN, os.path.join(self.root, "archive", "pc", "mod"),
            0, self.DIRS, self.root)

    def tearDown(self):
        settings = main._load_settings()
        for k in ("vanilla_baseline", "vanilla_root_baseline",
                  "vanilla_extra_baseline", "installed"):
            settings.get(k, {}).pop(self.DOMAIN, None)
        main._save_settings(settings)
        shutil.rmtree(self.root, ignore_errors=True)

    def _orphan(self, rel, name):
        with open(os.path.join(self.root, *rel.split("/"), name), "w") as f:
            f.write("orphan")

    def _reset(self):
        return run(main.Plugin().reset_game_modding(
            self.DOMAIN, self.GAME, "archive/pc/mod", "folder", 0, "",
            "starred", None, False, None, None, self.DIRS))

    def test_an_orphan_no_record_owns_is_found_and_removed(self):
        self._orphan("r6/scripts", "GeneralShadowsFixes.reds")
        r = self._reset()
        self.assertTrue(r["ok"], r)
        self.assertIn("r6/scripts/GeneralShadowsFixes.reds",
                      r["extra_leftovers"])
        self.assertFalse(os.path.exists(os.path.join(
            self.root, "r6", "scripts", "GeneralShadowsFixes.reds")))

    def test_the_games_own_files_survive(self):
        self._orphan("r6/scripts", "orphan.reds")
        self._reset()
        self.assertTrue(os.path.isfile(os.path.join(
            self.root, "r6", "scripts", "vanilla.reds")))

    def test_a_file_an_install_record_owns_is_left_to_the_record(self):
        # Removing it here would double-handle it and could beat the
        # record's own cleanup to the punch.
        self._orphan("red4ext/plugins", "MineNow.dll")
        settings = main._load_settings()
        settings.setdefault("installed", {})[self.DOMAIN] = {
            "Some Mod": {"mode": "folder", "name": "Some Mod",
                         "files": ["red4ext/plugins/MineNow.dll"]}}
        main._save_settings(settings)
        r = self._reset()
        self.assertNotIn("red4ext/plugins/MineNow.dll",
                         r["extra_leftovers"])

    def test_nothing_declared_means_nothing_swept(self):
        self._orphan("r6/scripts", "orphan.reds")
        r = run(main.Plugin().reset_game_modding(
            self.DOMAIN, self.GAME, "archive/pc/mod", "folder", 0, "",
            "starred", None, False, None, None, None))
        self.assertEqual(r["extra_leftovers"], [])
        self.assertTrue(os.path.isfile(os.path.join(
            self.root, "r6", "scripts", "orphan.reds")))

    def test_traversal_is_refused(self):
        r = run(main.Plugin().reset_game_modding(
            self.DOMAIN, self.GAME, "archive/pc/mod", "folder", 0, "",
            "starred", None, False, None, None, ["../../etc"]))
        self.assertEqual(r["extra_leftovers"], [])

    def test_a_directory_vanilla_does_not_have_is_baselined_as_empty(self):
        # The gap this closes. Four of Cyberpunk's five mod directories do
        # not exist in a vanilla install - r6/tweaks, red4ext/plugins and
        # bin/x64/plugins are created by the first mod - so os.listdir
        # raised, the error was swallowed, and they got no baseline at all.
        # Reset then skipped them for ever, and anything in them whose
        # install record was lost was an orphan nothing could find.
        #
        # "Vanilla does not have this directory" is a FACT worth recording,
        # and it is not the same as "we never looked".
        settings = main._load_settings()
        base = settings["vanilla_extra_baseline"][self.DOMAIN]
        self.assertEqual(base.get("r6/tweaks"), [])
        self.assertEqual(base.get("r6/scripts"), ["vanilla.reds"])

    def test_an_orphan_in_a_never_vanilla_directory_is_now_found(self):
        # The consequence: this file used to be permanently unreachable.
        os.makedirs(os.path.join(self.root, "r6", "tweaks"), exist_ok=True)
        self._orphan("r6/tweaks", "leftover.yaml")
        r = self._reset()
        self.assertIn("r6/tweaks/leftover.yaml", r["extra_leftovers"])
        self.assertFalse(os.path.exists(os.path.join(
            self.root, "r6", "tweaks", "leftover.yaml")))

    def test_an_unreadable_directory_is_still_left_alone(self):
        # Exists but cannot be listed is NOT "vanilla has nothing here".
        # Claiming empty there would have reset delete the contents of a
        # directory we simply could not open.
        real = os.listdir

        def boom(path):
            if path.replace(os.sep, "/").endswith("r6/scripts"):
                raise PermissionError("nope")
            return real(path)

        main.os.listdir = boom
        try:
            main._record_vanilla_baseline(
                "unreadtest",
                os.path.join(self.root, "archive", "pc", "mod"),
                0, ["r6/scripts"], self.root)
        finally:
            main.os.listdir = real
        base = (main._load_settings().get("vanilla_extra_baseline", {})
                .get("unreadtest") or {})
        self.assertNotIn("r6/scripts", base)
        settings = main._load_settings()
        settings.get("vanilla_extra_baseline", {}).pop("unreadtest", None)
        main._save_settings(settings)

    def test_reset_rebaselines_even_when_the_mods_folder_is_gone(self):
        # Found on device, 2026-08-15, doing this for real. Cyberpunk's mods
        # folder is archive/pc/mod - the game does not ship it, so reset
        # removes it entirely - and the re-take was gated on that folder
        # existing and being non-empty. So the one game most in need of a
        # fresh baseline was the one game that never got one, and a stale
        # baseline holding 16 .archive MOD files survived a clean reset,
        # protecting every one of them from all future sweeps.
        #
        # An absent or empty mods folder is a fact about vanilla, not a
        # reason to skip.
        settings = main._load_settings()
        settings.setdefault("vanilla_baseline", {})[self.DOMAIN] = [
            "someones-mod.archive"]
        main._save_settings(settings)
        shutil.rmtree(os.path.join(self.root, "archive"), ignore_errors=True)
        r = self._reset()
        self.assertTrue(r["ok"], r)
        after = main._load_settings()
        self.assertEqual(after["vanilla_baseline"][self.DOMAIN], [])
        # And the extra directories are baselined in the same pass, which is
        # the whole point of doing it here.
        extra = after["vanilla_extra_baseline"][self.DOMAIN]
        self.assertEqual(extra.get(self.ABSENT), [])

    def test_reset_also_rebaselines_the_game_root(self):
        # Device, 2026-08-15: Cyberpunk's vanilla_root_baseline was EMPTY
        # while 17 vanilla files sat in the game root. The leftover report
        # for that folder is gated on having a baseline at all, so an empty
        # one silently switches the check off - and a mod DLL beside the
        # exe would never be reported. That is the Fallout 3 failure, where
        # three mod DLLs survived several "clean" resets because nothing
        # ever looked there.
        with open(os.path.join(self.root, "Game.exe"), "w") as f:
            f.write("game")
        settings = main._load_settings()
        settings.setdefault("vanilla_root_baseline", {})[self.DOMAIN] = []
        main._save_settings(settings)
        r = self._reset()
        self.assertTrue(r["ok"], r)
        after = (main._load_settings()
                 .get("vanilla_root_baseline", {}).get(self.DOMAIN))
        self.assertIn("Game.exe", after)

    def test_every_install_time_baseline_is_stamped(self):
        # The root cause of the whole unstamped fleet. All five install-time
        # callers passed only (game_domain, mods_path), so the baseline went
        # in with no build stamp and no game-folder listing - and an
        # unstamped baseline used to read as "the game has not changed".
        # Six of nine games on device were in that state, every one of them
        # baselined at first install and never reset.
        #
        # Asserted on the call sites because that is where it went wrong:
        # the function always accepted these arguments and nobody passed
        # them.
        src = open(main.__file__, encoding="utf-8").read()
        # The old two-argument form is what left every baseline unstamped.
        self.assertNotIn("_record_vanilla_baseline(game_domain, mods_path)",
                         src)
        starts = [m.start() for m in
                  re.finditer(r"_record_vanilla_baseline\(", src)]
        # The definition plus five call sites.
        self.assertGreaterEqual(len(starts), 6, starts)
        for i in starts[1:]:
            window = src[i:i + 220]
            self.assertIn(
                "app_id", window,
                f"baseline call with no build stamp: {window[:120]!r}",
            )

    def test_a_mods_folder_the_game_never_ships_is_baselined_as_empty(self):
        # _record_vanilla_baseline bailed out when the mods folder did not
        # exist yet - which is the normal state for Cyberpunk's
        # archive/pc/mod and Slay the Spire 2's mods before the first mod
        # arrives. So the games whose mods folder is 100% mod-owned, the
        # ones where a baseline is most useful, were the only ones that
        # never got one at install time.
        settings = main._load_settings()
        settings.get("vanilla_baseline", {}).pop("freshtest", None)
        main._save_settings(settings)
        absent = os.path.join(self.root, "archive", "pc", "mod")
        shutil.rmtree(os.path.join(self.root, "archive"), ignore_errors=True)
        main._record_vanilla_baseline("freshtest", absent, 0, None, self.root)
        try:
            after = main._load_settings()
            self.assertEqual(after["vanilla_baseline"]["freshtest"], [])
        finally:
            settings = main._load_settings()
            for sec in ("vanilla_baseline", "vanilla_root_baseline"):
                settings.get(sec, {}).pop("freshtest", None)
            main._save_settings(settings)

    def test_the_framework_install_takes_the_baseline_first(self):
        # New Vegas, on a brand new install: Step 1 put xNVSE down, then
        # the first mod install recorded the baseline - so eight nvse_*
        # files and Data/NVSE went in as "vanilla". The framework is the
        # first thing to touch the game folder, so it has to record the
        # baseline, not inherit one taken after it.
        import inspect
        src = inspect.getsource(main.Plugin.install_framework)
        self.assertIn("_record_vanilla_baseline", src)
        self.assertLess(
            src.index("_record_vanilla_baseline"),
            src.index("_install_framework_inner("),
            "the baseline is taken after the framework is installed",
        )
        # Optional trailing args, so a caller that has not been updated
        # behaves exactly as before rather than recording a wrong baseline.
        self.assertIn('mods_subdir: str = ""', src)
        self.assertIn("if mods_subdir:", src)

    def test_an_empty_mods_baseline_still_sweeps(self):
        # "Recorded as empty" is not "never recorded", and `or []` had
        # collapsed the two. Cyberpunk's archive/pc/mod and Slay the Spire
        # 2's mods folder are not shipped by their games, so both baseline
        # correctly as [] - and both then had their sweep switched off, on
        # the very games the baseline work had just been done for.
        #
        # An empty baseline is the strongest statement there is: the game
        # owns nothing here, so anything present arrived with modding.
        settings = main._load_settings()
        settings.setdefault("vanilla_baseline", {})[self.DOMAIN] = []
        main._save_settings(settings)
        mods = os.path.join(self.root, "archive", "pc", "mod")
        os.makedirs(mods, exist_ok=True)
        with open(os.path.join(mods, "orphan.archive"), "w") as f:
            f.write("x")
        r = self._reset()
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["verified"])
        self.assertFalse(os.path.exists(os.path.join(mods, "orphan.archive")))

    def test_a_never_recorded_baseline_is_still_left_alone(self):
        # The other side of the same distinction: no baseline at all means
        # we do not know what the game put there, and nothing is touched.
        settings = main._load_settings()
        settings.get("vanilla_baseline", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)
        mods = os.path.join(self.root, "archive", "pc", "mod")
        os.makedirs(mods, exist_ok=True)
        with open(os.path.join(mods, "unknown.archive"), "w") as f:
            f.write("x")
        r = self._reset()
        self.assertFalse(r["verified"])
        self.assertTrue(os.path.exists(os.path.join(mods, "unknown.archive")))

    def test_an_unstamped_baseline_reports_instead_of_sweeping(self):
        # Skyrim SE on device, 2026-08-15: baseline_build was None, because
        # the baseline predates the stamp. The guard read that as "the game
        # has not changed" and swept - so a Bethesda patch followed by a
        # reset would have deleted the new vanilla files as orphans, on the
        # most heavily tested game in the project.
        #
        # A baseline of unknown age says nothing about the game on disk.
        settings = main._load_settings()
        settings.get("baseline_build", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)
        self._orphan("r6/scripts", "orphan.reds")
        orig = main._steam_build_id
        main._steam_build_id = lambda app_id: "999999"
        try:
            r = self._reset()
        finally:
            main._steam_build_id = orig
        self.assertTrue(r["game_changed"])
        # Named, so the user still learns about it...
        self.assertIn("r6/scripts/orphan.reds", r["extra_leftovers"])
        # ...but not deleted, because we cannot prove it is not a game file.
        self.assertTrue(os.path.isfile(os.path.join(
            self.root, "r6", "scripts", "orphan.reds")))

    def test_an_unreadable_current_build_changes_nothing(self):
        # If neither build is known we have learned nothing, so behaviour
        # is exactly as before - otherwise every game whose appmanifest we
        # cannot read would quietly stop sweeping.
        settings = main._load_settings()
        settings.get("baseline_build", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)
        self._orphan("r6/scripts", "orphan.reds")
        r = self._reset()  # app_id 0, so no build is readable either side
        self.assertFalse(r["game_changed"])
        self.assertFalse(os.path.exists(os.path.join(
            self.root, "r6", "scripts", "orphan.reds")))

    def test_a_directory_with_no_baseline_is_left_completely_alone(self):
        # The dangerous case. With no record of what the GAME put there,
        # every file reads as an orphan - and deleting r6/scripts because
        # nobody had baselined it would break the game outright.
        settings = main._load_settings()
        settings.get("vanilla_extra_baseline", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)
        self._orphan("r6/scripts", "orphan.reds")
        r = self._reset()
        self.assertEqual(r["extra_leftovers"], [])
        self.assertTrue(os.path.isfile(os.path.join(
            self.root, "r6", "scripts", "vanilla.reds")))
        self.assertTrue(os.path.isfile(os.path.join(
            self.root, "r6", "scripts", "orphan.reds")))


class TestResetRemovesNestedFrameworkFiles(unittest.TestCase):
    """Cyberpunk's five loaders install into bin/x64 and red4ext, and the
    cleanup loop was top-level only - so none of them could be declared and
    every reset left all five behind with Step 1 still ticked.

    Matched EXACTLY when a path contains a slash, never by prefix: "bin" as
    a prefix would delete the game."""

    GAME = "Nested FW Test"

    def setUp(self):
        self.root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.root, ignore_errors=True)
        for d in ("Data", "bin/x64/plugins", "red4ext/plugins/TweakXL",
                  "engine/tools"):
            os.makedirs(os.path.join(self.root, *d.split("/")))
        for f in ("bin/x64/version.dll", "bin/x64/Game.exe",
                  "engine/keep.txt"):
            with open(os.path.join(self.root, *f.split("/")), "w") as fh:
                fh.write("x")

    def tearDown(self):
        settings = main._load_settings()
        settings.get("vanilla_baseline", {}).pop("nestfw", None)
        settings.get("vanilla_root_baseline", {}).pop("nestfw", None)
        main._save_settings(settings)
        shutil.rmtree(self.root, ignore_errors=True)

    def _reset(self, prefixes):
        return run(main.Plugin().reset_game_modding(
            "nestfw", self.GAME, "Data", "dataDir", 0, "", "starred",
            prefixes, False, None, None))

    def test_a_nested_directory_is_removed(self):
        r = self._reset(["bin/x64/plugins", "red4ext/plugins/TweakXL"])
        self.assertTrue(r["ok"], r)
        self.assertFalse(os.path.isdir(
            os.path.join(self.root, "bin", "x64", "plugins")))
        self.assertFalse(os.path.isdir(
            os.path.join(self.root, "red4ext", "plugins", "TweakXL")))

    def test_a_nested_file_is_removed(self):
        self._reset(["bin/x64/version.dll"])
        self.assertFalse(os.path.isfile(
            os.path.join(self.root, "bin", "x64", "version.dll")))

    def test_the_game_survives(self):
        # The whole point: bin/ and engine/ are vanilla.
        self._reset(["bin/x64/plugins", "engine/tools",
                     "bin/x64/version.dll"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.root, "bin", "x64", "Game.exe")))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "bin")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.root, "engine", "keep.txt")))

    def test_a_nested_path_is_never_prefix_matched(self):
        # "bin/x64/plug" must not take bin/x64/plugins with it.
        self._reset(["bin/x64/plug"])
        self.assertTrue(os.path.isdir(
            os.path.join(self.root, "bin", "x64", "plugins")))

    def test_traversal_is_refused(self):
        self._reset(["../../../etc"])
        self.assertTrue(os.path.isdir(self.root))


class TestResetSeesTheGameFolder(unittest.TestCase):
    """Reset only ever looked at the mod folder.

    Script extenders, audio libraries and ENBs install BESIDE the game exe,
    so they survived every reset ever performed. Michael's Fallout 3 still
    had bass.dll, bassenc.dll and bassmix.dll in the game root after
    several "clean" resets - which means no baseline he tested from was
    ever actually clean, on any Bethesda game."""

    GAME = "Root Baseline Test"
    DOMAIN = "rootbase"

    def setUp(self):
        self.root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "Data"))
        for n in ("Game.exe", "binkw32.dll"):
            with open(os.path.join(self.root, n), "w") as f:
                f.write("vanilla")
        settings = main._load_settings()
        settings.get("vanilla_baseline", {}).pop(self.DOMAIN, None)
        settings.get("vanilla_root_baseline", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)

    def tearDown(self):
        settings = main._load_settings()
        settings.get("vanilla_baseline", {}).pop(self.DOMAIN, None)
        settings.get("vanilla_root_baseline", {}).pop(self.DOMAIN, None)
        main._save_settings(settings)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_the_baseline_records_the_game_folder_too(self):
        main._record_vanilla_baseline(
            self.DOMAIN, os.path.join(self.root, "Data"), 0)
        got = (main._load_settings().get("vanilla_root_baseline") or {}).get(
            self.DOMAIN)
        self.assertEqual(got, ["Game.exe", "binkw32.dll"])

    def test_a_dll_dropped_beside_the_exe_is_noticed(self):
        main._record_vanilla_baseline(
            self.DOMAIN, os.path.join(self.root, "Data"), 0)
        with open(os.path.join(self.root, "bass.dll"), "w") as f:
            f.write("a mod put this here")
        base = set((main._load_settings().get("vanilla_root_baseline") or {})
                   .get(self.DOMAIN) or [])
        now = {n for n in os.listdir(self.root)
               if os.path.isfile(os.path.join(self.root, n))}
        self.assertEqual(sorted(now - base), ["bass.dll"])

    def test_the_game_folder_baseline_is_only_taken_once(self):
        # Otherwise a mod's files become "vanilla" the moment they land.
        d = os.path.join(self.root, "Data")
        main._record_vanilla_baseline(self.DOMAIN, d, 0)
        with open(os.path.join(self.root, "bass.dll"), "w") as f:
            f.write("x")
        main._record_vanilla_baseline(self.DOMAIN, d, 0)
        got = (main._load_settings().get("vanilla_root_baseline") or {}).get(
            self.DOMAIN)
        self.assertNotIn("bass.dll", got)


class TestResetUndoesModdingTools(unittest.TestCase):
    """Reset has to return the setup steps to honest, not just remove mods.

    Michael reset Fallout 3 and Steps 2 and 3 stayed ticked. Both were
    factually true - the game had been booted, and the Anniversary Patcher
    really had rewritten Fallout3.exe - and both were useless, because
    nothing he could press would redo them."""

    GAME = "Reset Tools Test"

    def setUp(self):
        self.plugin = main.Plugin()
        self.root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "Data"))
        with open(os.path.join(self.root, "Game.exe"), "w") as f:
            f.write("patched")
        with open(os.path.join(self.root, "Game_backup.exe"), "w") as f:
            f.write("original")
        settings = main._load_settings()
        settings.setdefault("prefix_tools", {})["resettools"] = {
            "24913": {"at": 1, "changed": ["Game.exe"]}}
        main._save_settings(settings)

    def tearDown(self):
        settings = main._load_settings()
        settings.get("prefix_tools", {}).pop("resettools", None)
        settings.get("vanilla_baseline", {}).pop("resettools", None)
        main._save_settings(settings)
        shutil.rmtree(self.root, ignore_errors=True)

    def _reset(self, pairs):
        return run(self.plugin.reset_game_modding(
            "resettools", self.GAME, "Data", "dataDir", 0, "", "starred",
            None, False, None, pairs))

    def test_the_game_exe_is_put_back_from_the_tools_backup(self):
        r = self._reset([["Game_backup.exe", "Game.exe"]])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["restored"], ["Game.exe"])
        with open(os.path.join(self.root, "Game.exe")) as f:
            self.assertEqual(f.read(), "original")

    def test_the_backup_is_consumed_so_it_cannot_be_applied_twice(self):
        self._reset([["Game_backup.exe", "Game.exe"]])
        self.assertFalse(
            os.path.exists(os.path.join(self.root, "Game_backup.exe")))

    def test_the_tool_becomes_available_again(self):
        self._reset([["Game_backup.exe", "Game.exe"]])
        done = (main._load_settings().get("prefix_tools") or {}).get(
            "resettools")
        self.assertIsNone(done, "Step 3 would still show as applied")

    def test_a_missing_backup_is_not_an_error(self):
        os.remove(os.path.join(self.root, "Game_backup.exe"))
        r = self._reset([["Game_backup.exe", "Game.exe"]])
        self.assertTrue(r["ok"])
        self.assertEqual(r["restored"], [])

    def test_nothing_declared_means_nothing_touched(self):
        r = self._reset(None)
        self.assertEqual(r["restored"], [])
        with open(os.path.join(self.root, "Game.exe")) as f:
            self.assertEqual(f.read(), "patched")

    def test_a_path_outside_the_game_is_refused(self):
        r = self._reset([["../../../etc/passwd", "Game.exe"]])
        self.assertEqual(r["restored"], [])
        with open(os.path.join(self.root, "Game.exe")) as f:
            self.assertEqual(f.read(), "patched")


class TestLogTagMatching(unittest.TestCase):
    """Mods log under a logger name they chose, not their id. Five of the
    nine blamed tags in the real crash log matched nothing until this."""

    def test_an_exact_id_matches(self):
        self.assertTrue(main._tag_names_mod("BaseLib", "BaseLib"))
        self.assertTrue(main._tag_names_mod("RelicsReminder",
                                            "relicsreminder"))

    def test_a_namespaced_tag_matches_its_id(self):
        self.assertTrue(main._tag_names_mod(
            "com.ritsukage.sts2-RitsuLib", "STS2-RitsuLib"))
        self.assertTrue(main._tag_names_mod(
            "sts2.piyixiajiuhenfen.modconfig", "ModConfig"))
        self.assertTrue(main._tag_names_mod(
            "com.ritsukage.sts2-multiplayerpotionview",
            "STS2-MultiPlayerPotionView"))

    def test_a_partial_word_does_not_match(self):
        # The danger of suffix matching: "Lib" must not claim "BaseLib",
        # because BaseLib is the library everything else depends on.
        self.assertFalse(main._tag_names_mod("BaseLib", "Lib"))
        self.assertFalse(main._tag_names_mod("SuperRelicsReminder",
                                             "RelicsReminder"))

    def test_a_very_short_id_only_matches_exactly(self):
        self.assertTrue(main._tag_names_mod("ab", "ab"))
        self.assertFalse(main._tag_names_mod("x.ab", "ab"))

    def test_blanks_never_match(self):
        self.assertFalse(main._tag_names_mod("", "BaseLib"))
        self.assertFalse(main._tag_names_mod("BaseLib", ""))
        self.assertFalse(main._tag_names_mod("BaseLib", "   "))


class TestDependencyProtection(unittest.TestCase):
    """Which mods are libraries, taken from the manifests rather than a
    hand-written list. Five installed mods declared BaseLib."""

    MANIFESTS = {
        "BaseLib": {"id": "BaseLib", "name": "BaseLib", "deps": []},
        "RitsuLib": {"id": "STS2-RitsuLib", "name": "RitsuLib", "deps": []},
        "Campfire Trading": {"id": "STS2Trade", "name": "Campfire Trading",
                             "deps": ["BaseLib"]},
        "Show Player Hand Cards": {"id": "STS2-ShowPlayerHandCards",
                                   "name": "Show Player Hand Cards",
                                   "deps": ["STS2-RitsuLib"]},
    }

    def test_a_library_something_still_needs_is_reported(self):
        keeping = {"BaseLib", "RitsuLib", "Campfire Trading"}
        needed = main._mods_needed_by_others(self.MANIFESTS, keeping)
        self.assertEqual(needed["baselib"], ["Campfire Trading"])

    def test_a_library_nothing_needs_any_more_is_free(self):
        # Its only dependent is being switched off in this same pass.
        keeping = {"BaseLib", "RitsuLib"}
        needed = main._mods_needed_by_others(self.MANIFESTS, keeping)
        self.assertNotIn("sts2-ritsulib", needed)
        self.assertNotIn("baselib", needed)

    def test_dependency_ids_are_matched_case_insensitively(self):
        needed = main._mods_needed_by_others(
            self.MANIFESTS, {"Show Player Hand Cards"})
        self.assertIn("sts2-ritsulib", needed)


class TestCallableArityCounter(unittest.TestCase):
    """The arity check is only a safety net if its own counter is right.
    It miscounted a documented parameter because the doc comment contained
    a comma."""

    count = staticmethod(TestCallableArity._count_ts_args)

    def test_counts_plain_arguments(self):
        self.assertEqual(self.count("a: string, b: number"), 2)

    def test_a_comma_inside_a_comment_is_not_an_argument(self):
        self.assertEqual(
            self.count(
                "a: string,"
                + chr(10)
                + "/** Reported, never switched off. */"
                + chr(10)
                + "b: number[]"
            ),
            2,
        )

    def test_a_line_comment_comma_is_not_an_argument(self):
        self.assertEqual(self.count("a: string // one, two" + chr(10)), 1)

    def test_nested_generics_are_still_one_argument(self):
        self.assertEqual(self.count("a: Record<string, number>"), 1)

    def test_empty_is_zero(self):
        self.assertEqual(self.count("   "), 0)


class TestPerModSupport(unittest.TestCase):
    """The same warning has to reach someone who found the mod by
    browsing, not only someone installing the collection it came from.

    Michael: "I don't want users to run into these problems individually as
    well as on collections." The three New Vegas interface mods that stop
    the game starting are one search away from any user."""

    def test_a_mod_needing_an_off_nexus_file_is_flagged(self):
        for mod_id in (44757, 70001, 84166):
            r = run(main.Plugin().get_mod_support("newvegas", mod_id))
            self.assertTrue(r["ok"])
            self.assertFalse(r["supported"], mod_id)
            self.assertEqual(r["needs_name"], "Vanilla UI+ (VUI+)")
            # And where to get it, or the warning is a dead end.
            self.assertIn("moddb.com", r["url"])
            # And what happens if they install it anyway.
            self.assertIn("will not start", r["reason"])

    def test_an_ordinary_mod_is_supported(self):
        r = run(main.Plugin().get_mod_support("newvegas", 51664))
        self.assertTrue(r["supported"])

    def test_the_table_is_keyed_by_id_not_name(self):
        # Record names are sanitised display strings that drift; the mod id
        # is what a mod page knows, which is the whole point of this table.
        for mod_id in main.MODS_NEEDING_EXTERNAL["newvegas"]:
            self.assertIsInstance(mod_id, int)

    def test_rejects_a_bad_domain(self):
        r = run(main.Plugin().get_mod_support("../evil", 1))
        self.assertFalse(r["ok"])


class TestUnsupportedCollections(unittest.TestCase):
    """Saying a collection cannot work HERE, before the download rather
    than after it. The TTW collection is 42 GB and needs a conversion that
    cannot be built in Gaming Mode at all."""

    def test_reports_the_known_unsupported_one(self):
        r = run(main.Plugin().get_collection_support("newvegas", "3fs9zx"))
        self.assertTrue(r["ok"])
        self.assertFalse(r["supported"])
        self.assertIn("Tale of Two Wastelands", r["reason"])
        # The reason has to say what the user loses, not just "no".
        self.assertIn("switched off", r["reason"])

    def test_anything_else_is_supported(self):
        r = run(main.Plugin().get_collection_support("newvegas", "jscbqj"))
        self.assertTrue(r["supported"])

    def test_rejects_a_bad_domain(self):
        r = run(main.Plugin().get_collection_support("../evil", "x"))
        self.assertFalse(r["ok"])


class TestRefuseEnablingBrokenMod(unittest.TestCase):
    """Switching a mod back on must not be allowed to break the game.

    Device, NPC Overhaul: the collection ships "Mojave Raiders - The Living
    Desert Patch" and never asks you to install Mojave Raiders itself, so
    the patch has no master. We switched it off correctly - and because the
    row gave no reason, the natural response to "why is this disabled?" was
    to turn it back on, after which the game stopped booting."""

    GAME = "Refuse Test"
    APP_ID = 22380
    SUBPATH = "FalloutNV/Plugins.txt"

    def setUp(self):
        self.plugin = main.Plugin()
        root = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(root, ignore_errors=True)
        self.data = os.path.join(root, "Data")
        os.makedirs(self.data)
        _make_plugin(os.path.join(self.data, "patch.esp"),
                     masters=["TheMainMod.esp"])
        _make_plugin(os.path.join(self.data, "fine.esp"))
        self.txt = main._plugins_txt_path(self.APP_ID, self.SUBPATH)
        main._makedirs_for(self.txt)
        with open(self.txt, "w", encoding="utf-8") as f:
            f.write("")
        settings = main._load_settings()
        settings.setdefault("installed", {})["refusetest"] = {
            "Patch Mod": {"mode": "dataDir", "plugins": ["patch.esp"],
                          "files": ["patch.esp"]},
            "Fine Mod": {"mode": "dataDir", "plugins": ["fine.esp"],
                         "files": ["fine.esp"]},
        }
        main._save_settings(settings)

    def tearDown(self):
        settings = main._load_settings()
        settings.get("installed", {}).pop("refusetest", None)
        main._save_settings(settings)

    def _enable(self, folder):
        return run(self.plugin.set_mod_enabled(
            self.GAME, "Data", folder, True, "dataDir", "refusetest",
            self.APP_ID, self.SUBPATH, "listed"))

    def test_refuses_a_mod_whose_master_is_absent(self):
        r = self._enable("Patch Mod")
        self.assertFalse(r["ok"])
        self.assertIn("TheMainMod.esp", r["error"])
        # And says what it did instead of leaving the user guessing.
        self.assertIn("left off", r["error"])

    def test_it_stays_off(self):
        self._enable("Patch Mod")
        self.assertEqual(main._enabled_plugins(self.txt, "listed"), [])

    def test_a_healthy_mod_still_enables(self):
        r = self._enable("Fine Mod")
        self.assertTrue(r["ok"], r)
        self.assertIn("fine.esp", main._enabled_plugins(self.txt, "listed"))

    def test_the_master_arriving_makes_it_enableable(self):
        _make_plugin(os.path.join(self.data, "TheMainMod.esp"))
        r = self._enable("Patch Mod")
        self.assertTrue(r["ok"], r)


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
                "mod_id": 4001,
                "files": ["menus/hud.xml", "menus/other.xml"],
            },
        }
        main._save_settings(settings)
        # Point the table at our fake domain for the duration.
        main.MODS_NEEDING_EXTERNAL["newvegas_prereq"] = {
            4001: {
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
        main.MODS_NEEDING_EXTERNAL.pop("newvegas_prereq", None)
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

    def test_a_group_can_park_files_it_only_shares_with_itself(self):
        """Device: the oHUD/Clean Vanilla Hud patch owns exactly two files,
        both shared with the other two interface mods. With all three going
        off, "leave shared files alone" left it nothing to move - so it
        stayed fully active and kept hud_main_menu.xml, the one file that
        stops the game starting. Being cautious about a mod that is also
        being switched off protects nobody."""
        settings = main._load_settings()
        recs = settings["installed"]["newvegas_prereq"]
        recs["The Patch"] = {
            "mode": "dataDir", "plugins": [], "name": "The Patch",
            "mod_id": 4002,
            # Both of its files belong to the other mod too.
            "files": ["menus/hud.xml", "menus/other.xml"],
        }
        main._save_settings(settings)
        main.MODS_NEEDING_EXTERNAL["newvegas_prereq"][4002] = {
            "needs_file": "Vanilla UI Plus.esp",
            "needs_name": "Vanilla UI+ (VUI+)",
        }
        try:
            r = self._apply()
            self.assertEqual(sorted(r["mods"]),
                             ["One HUD - oHUD", "The Patch"])
            # Both are recorded OFF even though only one of them actually
            # moved the shared files - whichever got there first took them.
            recs = main._load_settings()["installed"]["newvegas_prereq"]
            self.assertTrue(recs["The Patch"].get("parked"))
            self.assertTrue(recs["One HUD - oHUD"].get("parked"))
            # The contested files are gone, because nothing still on is
            # relying on them.
            self.assertFalse(self._present("menus/hud.xml"))
            self.assertFalse(self._present("menus/other.xml"))
        finally:
            main._force_rmtree(
                main._parked_files_dir("newvegas_prereq", "The Patch")
            )

    def test_a_file_shared_with_a_mod_staying_on_is_still_left(self):
        settings = main._load_settings()
        settings["installed"]["newvegas_prereq"]["Unrelated Mod"] = {
            "mode": "dataDir", "plugins": [], "name": "Unrelated Mod",
            "files": ["menus/other.xml"],
        }
        main._save_settings(settings)
        r = self._apply()
        self.assertEqual(r["parked"], 1)
        self.assertFalse(self._present("menus/hud.xml"))
        self.assertTrue(self._present("menus/other.xml"))

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
        #
        # Asserted on the guard itself rather than on a source substring:
        # this used to match the literal "and not errors", which broke the
        # moment the condition moved onto its own line - a test failing for
        # a reformat teaches nothing.
        import inspect
        src = inspect.getsource(main.Plugin.reset_game_modding)
        head = src[: src.index("re-took the vanilla baseline")]
        guard = head.rindex("if not errors:")
        retake = head.rindex('settings.setdefault("vanilla_baseline"')
        self.assertLess(
            guard, retake,
            "the baseline re-take is no longer behind the no-errors guard",
        )


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

    def test_updating_a_collection_mod_keeps_it_in_the_collection(self):
        # Found on device: updating BaseLib inside a collection blanked its
        # source and collection_slug, because a plain install passes those
        # as "". Cancelling the collection would then have walked past it
        # and left an orphan - the exact failure Michael asked to be
        # careful about. An update is not a change of provenance.
        existing = {"mod_id": 103, "file_id": 1, "version": "3.1.2",
                    "source": "collection", "collection_slug": "q9rlkd"}
        merged = main._merge_install_record(
            existing,
            {"mod_id": 103, "file_id": 2, "version": "3.3.8",
             "source": "", "collection_slug": ""},
        )
        self.assertEqual(merged["source"], "collection")
        self.assertEqual(merged["collection_slug"], "q9rlkd")
        self.assertEqual(merged["version"], "3.3.8")

    def test_a_deliberate_new_provenance_still_wins(self):
        merged = main._merge_install_record(
            {"mod_id": 1, "file_id": 1, "source": "collection",
             "collection_slug": "old"},
            {"mod_id": 1, "file_id": 2, "source": "collection",
             "collection_slug": "new"},
        )
        self.assertEqual(merged["collection_slug"], "new")

    def test_a_standalone_install_gains_no_provenance(self):
        merged = main._merge_install_record(
            {"mod_id": 1, "file_id": 1},
            {"mod_id": 1, "file_id": 2, "source": "", "collection_slug": ""},
        )
        self.assertEqual(merged.get("source"), "")
        self.assertEqual(merged.get("collection_slug"), "")

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
        # ONE directory, whichever casing won. The point of the test is that
        # a mod naming Data/Scripts and Data/scripts does not produce two
        # folders where the game reads one - not which of the two spellings
        # ends up on disk. Asserting "Scripts" specifically passed on
        # Windows, where the filesystem folds case for you, and failed on
        # Linux, where the first spelling seen wins. CI runs on Linux.
        scripts = [d for d in os.listdir(self.data) if d.lower() == "scripts"]
        self.assertEqual(len(scripts), 1, scripts)
        source = [
            d for d in os.listdir(os.path.join(self.data, scripts[0]))
            if d.lower() == "source"
        ]
        self.assertEqual(len(source), 1, source)
        self.assertCountEqual(
            os.listdir(os.path.join(self.data, scripts[0], source[0])),
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


class TestRegulationClashBeforeDownload(unittest.TestCase):
    """ELDEN RING Reforged downloaded 2.7GB and was then refused, because
    the regulation.bin clash was only detectable after extraction. The
    archive listing Nexus already publishes answers it first."""

    def test_regulation_is_found_at_any_depth(self):
        tree = {"children": [
            {"name": "readme.txt", "type": "file"},
            {"name": "reforged", "type": "directory", "children": [
                {"name": "param", "type": "directory", "children": [
                    {"name": "regulation.bin", "type": "file"},
                ]},
            ]},
        ]}
        self.assertTrue(main._preview_has_regulation(tree["children"]))

    def test_an_archive_without_it_is_not_a_clash(self):
        tree = [
            {"name": "mod.dll", "type": "file"},
            {"name": "chr", "type": "directory", "children": [
                {"name": "c0000.partsbnd.dcx", "type": "file"},
            ]},
        ]
        self.assertFalse(main._preview_has_regulation(tree))

    def test_a_regulation_folder_is_not_the_file(self):
        # Matched on the leaf name, so a directory that merely mentions it
        # in a path cannot raise a false clash.
        self.assertFalse(main._preview_has_regulation(
            [{"name": "regulation", "type": "directory", "children": []}]
        ))

    def test_junk_in_the_tree_is_survivable(self):
        # Third-party JSON: nulls, strings and numbers must not raise.
        self.assertFalse(main._preview_has_regulation(
            [None, "regulation.bin", 7, {"children": None}]
        ))

    def test_no_current_owner_asks_the_network_nothing(self):
        # The cost of the check must be zero for the common case.
        with mock.patch.object(main, "_load_settings", return_value={}), \
                mock.patch.object(main, "_me3_regulation_owner",
                                  return_value=None), \
                mock.patch.object(main.aiohttp, "ClientSession") as session:
            owner = run(main._regulation_owner_before_download(
                "eldenring", 541, 9999, "ERR"))
        self.assertEqual(owner, "")
        session.assert_not_called()

    def test_both_gates_speak_with_one_voice(self):
        # Two wordings for one refusal is how a UI ends up handling only
        # the one someone tested.
        body = main._regulation_clash_error("ERR", "The Convergence")
        self.assertTrue(body["mod_conflict"])
        self.assertIn("The Convergence", body["error"])
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        # One wording, however many callers - counting callers just makes
        # the test fail every time a third place needs to say it.
        self.assertEqual(source.count("replaces regulation.bin, and"), 1)

    def test_a_broken_preview_never_blocks_an_install(self):
        # It is a courtesy check on optional metadata. Any failure of it
        # must fall through to the post-extract gate, never refuse. The
        # existing me3 suite found this: the first version let the test
        # harness's "network is disabled" RuntimeError escape.
        for boom in (RuntimeError("network is disabled"), KeyError("files"),
                     ValueError("not json")):
            with mock.patch.object(main, "_load_settings", return_value={}), \
                    mock.patch.object(main, "_me3_regulation_owner",
                                      return_value="The Convergence"), \
                    mock.patch.object(main.aiohttp, "ClientSession",
                                      side_effect=boom):
                owner = run(main._regulation_owner_before_download(
                    "eldenring", 541, 9999, "ERR"))
            self.assertIsNone(owner, f"{type(boom).__name__} escaped the gate")

    def test_what_one_download_taught_is_not_paid_for_twice(self):
        # Nexus publishes no content listing for some files - The
        # Convergence's are 404 - so the only way to know was to open the
        # archive. Having opened it once, the answer is kept.
        with mock.patch.object(main, "_load_settings", return_value={
            "regulation_facts": {"eldenring": {"3419": True}},
        }), mock.patch.object(main, "_me3_regulation_owner",
                              return_value="ERR - ELDEN RING Reforged"), \
                mock.patch.object(main.aiohttp, "ClientSession") as session:
            owner = run(main._regulation_owner_before_download(
                "eldenring", 3419, 48403, "The Convergence"))
        self.assertEqual(owner, "ERR - ELDEN RING Reforged")
        session.assert_not_called()

    def test_a_mod_known_clean_is_not_re_checked_either(self):
        with mock.patch.object(main, "_load_settings", return_value={
            "regulation_facts": {"eldenring": {"9999": False}},
        }), mock.patch.object(main, "_me3_regulation_owner",
                              return_value="ERR"), \
                mock.patch.object(main.aiohttp, "ClientSession") as session:
            owner = run(main._regulation_owner_before_download(
                "eldenring", 9999, 1, "Some UI mod"))
        self.assertEqual(owner, "")
        session.assert_not_called()

    def test_never_seen_is_not_the_same_as_clean(self):
        self.assertIsNone(main._known_regulation_mod({}, "eldenring", 3419))
        self.assertIs(main._known_regulation_mod(
            {"regulation_facts": {"eldenring": {"3419": False}}},
            "eldenring", 3419), False)

    def test_a_preview_link_with_spaces_is_still_fetchable(self):
        # ERR's own patch file: the link carries the upload's file name,
        # spaces and all, and a raw space is not a legal request target.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("urllib.parse.quote(link", source)

    def test_only_a_recorded_verdict_skips_a_mod(self):
        # Age is information; a verdict is evidence. The age rule skipped A
        # Better Nude Body, verified working on this build, and every
        # texture mod in EldenBoobs. Michael: "i think you have been far
        # too broad with that brush... we need to mark ones specifically
        # incompatible even if it means more testing."
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        wrapper = source[source.index("        payload_choice picks a folder"):]
        wrapper = wrapper[:wrapper.index("    async def get_user_prefs")]
        # The skip and the disable both hang off the verdict...
        self.assertIn("_known_broken_mods(", wrapper)
        self.assertIn("if broken and record_source", wrapper)
        self.assertIn('result.get("ok") and broken', wrapper)
        # ...and age reaches the user as a warning and nothing else.
        self.assertNotIn("stale_note and record_source", wrapper)
        self.assertIn('result["warning"] = stale_note', wrapper)

    def test_a_verdict_is_about_a_version_not_a_mod(self):
        # Seamless Co-op 1.5.1, the version Elden Essentials pins, fails on
        # this build; 1.9.9 from the mod page works, because the mod is hard
        # version-locked to the game. _known_broken_mods filters on build
        # only, so without this the verdict would take the working release
        # with it - and Michael has a co-op setup he asked us not to break.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        wrapper = source[source.index("        payload_choice picks a folder"):]
        wrapper = wrapper[:wrapper.index("    async def get_user_prefs")]
        # The comparison lives in _verdict_covers_version now, which also
        # handles "this version and older" - see
        # TestAVerdictCanCoverOlderVersions for the behaviour itself.
        self.assertIn("_verdict_covers_version(broken, mod_version)", wrapper)

    def test_a_stale_native_installs_switched_off(self):
        # From scratch, nobody should have to know which mod to disable.
        # Michael, after being handed four dll names: "package it up nicely
        # so that when I install from scratch the user doesnt have to
        # disable individual mods etc."
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        wrapper = source[source.index("        payload_choice picks a folder"):]
        wrapper = wrapper[:wrapper.index("    async def get_user_prefs")]
        # One check, both paths: skip in a collection, off on its own.
        self.assertIn('record_source == "collection"', wrapper)
        self.assertIn("_disable_me3_record(", wrapper)
        self.assertIn('result["installed_disabled"] = True', wrapper)

    def test_a_loader_is_exempt_from_the_older_patch_rule(self):
        # Elden Mod Loader was last updated in 2022 and the date rule
        # skipped it, taking out the one mod in the collection that works.
        # A proxy loader loads other dlls; it does not search game code, so
        # a game patch cannot age it out. Michael: "its skipped every mod
        # in the collection. i thought it should leave Elden mod loader?"
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        wrapper = source[source.index("        payload_choice picks a folder"):]
        wrapper = wrapper[:wrapper.index("    async def get_user_prefs")]
        self.assertIn("is_framework", wrapper)
        self.assertIn("not is_framework", wrapper)
        # From the game's own config, not a hardcoded mod id.
        self.assertIn("framework_ids", wrapper)

    def test_disabling_a_record_rewrites_the_profile(self):
        # A record flipped off without rewriting the profile still loads:
        # the profile is what me3 reads, not our settings file.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("def _disable_me3_record"):]
        fn = fn[:fn.index("def _me3_records")]
        self.assertIn("_write_me3_profile(", fn)
        self.assertIn("_save_settings(", fn)
        self.assertIn('rec["enabled"] = False', fn)
        # And it must refuse to touch anything that is not an me3 record.
        self.assertIn('rec.get("mode") != "me3"', fn)

    def test_the_gate_runs_before_the_worker(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        gate = source.index("_regulation_owner_before_download(\n")
        worker = source.index("await self._install_mod_inner(")
        self.assertLess(
            gate, worker,
            "the pre-download gate must sit above the install worker, or "
            "the download happens first and the refusal is worthless"
        )


class TestErrNativesAreNotPlumbing(unittest.TestCase):
    """ERR's install registered 13 natives: four were ERR's, the rest were
    a bundled mod host and the author's opt-in pile."""

    def test_a_top_level_bundled_loader_is_excluded(self):
        # The Convergence ships its copy as a top-level me3/ folder.
        self.assertTrue(main._me3_bundled_loader("me3"))
        self.assertTrue(main._me3_bundled_loader("modengine2/bin"))

    def test_errs_bundled_modengine_tree_must_not_load(self):
        # Learned by crashing. ERR keeps a whole ModEngine2 distribution at
        # internals/modengine/, dlls included, built for ITS loader and ITS
        # config. Registering them as our natives crashed Elden Ring within
        # seconds of launch; ERR loading assets only is the state that works.
        # Michael: "I reinstalled ERR and now the game crashes almost
        # instantly". Do not "fix" this into loading them again.
        self.assertTrue(main._me3_bundled_loader("internals/modengine/dll"))
        self.assertTrue(main._me3_bundled_loader(
            "internals/modengine/bin/win64"))

    def test_a_top_level_bundled_loader_is_excluded_too(self):
        self.assertTrue(main._me3_bundled_loader("modengine2/bin"))

    def test_loader_plumbing_is_excluded_by_name_anywhere(self):
        # The name test catches what the folder test cannot: libzstd sits
        # under internals/launcher/, named after no loader at all.
        self.assertTrue(main._me3_loader_binary(
            "internals/launcher/libzstd.dll"))

    def test_eldens_own_loader_is_not_plumbing(self):
        # EML's dinput8.dll IS the mod, and other mods depend on it.
        self.assertFalse(main._me3_loader_binary("dinput8.dll"))

    def test_real_mod_directories_are_left_alone(self):
        self.assertFalse(main._me3_bundled_loader("dll"))
        self.assertFalse(main._me3_bundled_loader("mod/parts"))
        self.assertFalse(main._me3_bundled_loader("internals/launcher"))

    def test_the_authors_optional_pile_does_not_auto_load(self):
        # UltrawideFix and UltrawideFixNoDelay are alternatives, not a set.
        self.assertTrue(main._me3_optional_native(
            "dll/optional/UltrawideFix.dll"))
        self.assertTrue(main._me3_optional_native(
            "dll/optional/UltrawideFixNoDelay.dll"))
        self.assertTrue(main._me3_optional_native("optionals/x.dll"))

    def test_a_dll_named_optional_is_not_a_folder_named_optional(self):
        # Only directories decide this - a dll called optional.dll ships.
        self.assertFalse(main._me3_optional_native("dll/optional.dll"))
        self.assertFalse(main._me3_optional_native("reforged.dll"))


class TestLooseFromSoftAssets(unittest.TestCase):
    """A Better Nude Body ships its meshes at the archive root with no
    parts/ folder, and was refused for having no layout while being
    nothing but game assets."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _touch(self, *names):
        for n in names:
            open(os.path.join(self.dir, n), "w").close()

    def test_the_suffix_names_the_folder(self):
        self.assertEqual(
            main._me3_loose_asset_dir("bd_f_0000.partsbnd.dcx"), "parts")
        self.assertEqual(
            main._me3_loose_asset_dir("c0000.chrbnd.dcx"), "chr")
        self.assertIsNone(main._me3_loose_asset_dir("readme.txt"))
        # Ambiguous on purpose: a texbnd could dress either.
        self.assertIsNone(main._me3_loose_asset_dir("c0000.texbnd.dcx"))

    def test_abnbs_own_file_list_is_sorted_and_installable(self):
        self._touch("bd_f_0000.partsbnd.dcx", "fc_f_0100.partsbnd.dcx",
                    "lg_f_0000.partsbnd.dcx", "readme.txt")
        moved = main._sort_loose_me3_assets(self.dir)
        self.assertEqual(sorted(moved), ["parts"])
        self.assertEqual(len(moved["parts"]), 3)
        self.assertTrue(os.path.isfile(
            os.path.join(self.dir, "parts", "bd_f_0000.partsbnd.dcx")))
        # Not an asset, so left exactly where the author put it.
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "readme.txt")))
        # And the archive now reads as the package it always was.
        self.assertIsNotNone(main._me3_assets_subpath(self.dir))

    def test_an_archive_with_no_loose_assets_is_untouched(self):
        self._touch("readme.txt", "screenshot.png")
        self.assertEqual(main._sort_loose_me3_assets(self.dir), {})
        self.assertEqual(len(os.listdir(self.dir)), 2)

    def test_a_proper_layout_is_never_reshuffled(self):
        os.makedirs(os.path.join(self.dir, "parts"))
        open(os.path.join(self.dir, "parts", "x.partsbnd.dcx"), "w").close()
        self.assertEqual(main._sort_loose_me3_assets(self.dir), {})
        self.assertTrue(os.path.isfile(
            os.path.join(self.dir, "parts", "x.partsbnd.dcx")))


class TestDlcRequirementInProse(unittest.TestCase):
    """Nexus added a structured dlcRequirements field recently, so it is the
    right answer and will fill in over time - but the backlog is enormous.
    Eagle Rising declares nothing there while its description says "Warsails
    is required for mod to work", and Michael's device has no War Sails: his
    crash exactly, after the module's own DLLs had loaded fine."""

    def test_the_sentence_that_caused_the_crash(self):
        quote = main._dlc_requirement_quote(
            "Rather a small patch, that brings mod on 1.3+. Warsails is "
            "required for mod to work, purly becouse mod team don't have "
            "much time to work on other version."
        )
        self.assertIn("Warsails is required", quote)

    def test_ownership_phrasings(self):
        for text in (
            "Be sure that you own Warsails DLC before you start.",
            "You must own the War Sails expansion for this to load.",
            "This mod requires the Blood Feuds DLC to function properly.",
        ):
            self.assertTrue(main._dlc_requirement_quote(text), text)

    def test_the_opposite_statement_is_not_a_requirement(self):
        # "No DLC required" is as common as the real thing - the same trap
        # the downgrade check hit, so the same negation guard.
        for text in (
            "No DLC required! Works with the base game.",
            "You do not need any DLC to use this mod.",
        ):
            self.assertEqual(main._dlc_requirement_quote(text), "", text)

    def test_a_mod_requirement_is_not_a_dlc_requirement(self):
        self.assertEqual(main._dlc_requirement_quote(
            "[font=Arial]RBM is required, including the combat module."), "")

    def test_markup_does_not_hide_the_sentence(self):
        quote = main._dlc_requirement_quote(
            "<p>Read this.</p><br/>Warsails DLC is required for the map.")
        self.assertIn("Warsails", quote)

    def test_the_quote_is_bounded(self):
        long = "x" * 400 + ". This mod requires the War Sails DLC to work. "
        self.assertLessEqual(len(main._dlc_requirement_quote(long)), 220)

    def test_the_structured_field_takes_priority(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def get_mod_requirements"):]
        fn = fn[:fn.index("# ---- Collections")]
        # Only consulted when the declared field is empty: a declared
        # requirement is better data than a sentence about one.
        self.assertIn('if not split.get("dlc"):', fn)
        self.assertIn("description", fn)


class TestMultiModuleBranchIsGated(unittest.TestCase):
    """Seven v1.2-era code mods from Eagle Rising installed ENABLED while
    thirty-eight single-module ones were skipped: Modules/-layout archives
    take the multi-module branch, and only the single-module branch was
    gated. One of the seven pinned v1.2.0.* explicitly. The gate now exists
    in both branches; this pins the second one."""

    def test_both_install_branches_carry_the_gate(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        multi = source[source.index("skipped_children = []"):]
        multi = multi[:multi.index("_save_settings(settings)")]
        self.assertIn("_bl_manifest_game_mismatch(dst", multi)
        self.assertIn("_bl_module_ships_dll(dst)", multi)
        self.assertIn("_collection_built_for(", multi)
        # And it removes what it skipped rather than leaving orphan folders.
        self.assertIn("_force_rmtree(dst)", multi)


class TestEraQuarantine(unittest.TestCase):
    """Records installed before the era gate existed sailed past it, and one
    of them crashed the game silently after everything newer was fixed. The
    health check applies the same rule to what is already on disk."""

    def test_the_health_check_runs_the_quarantine(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("era_quarantined = (", source)
        fn = source[source.index("async def _bl_quarantine_era_locked"):]
        fn = fn[:fn.index("def _bl_module_ships_dll")]
        # User-owned records are never touched.
        self.assertIn('rec or {}).get("collection_slug")', fn)
        # Deactivated, not deleted: one tick tries it again.
        self.assertIn("_set_module_selected(launcher, module_id, False)", fn)
        self.assertNotIn("_force_rmtree", fn)


class TestRecordOwnershipIsFirstCome(unittest.TestCase):
    """Michael installed ButterLib on its own, then a collection containing
    it: the record was claimed by the collection, so ButterLib vanished from
    his flat mod list and would have been ripped out by a collection
    uninstall. A mod the user installed deliberately stays theirs."""

    def test_a_users_mod_is_not_claimed_by_a_collection(self):
        existing = {"mod_id": 2018, "name": "ButterLib",
                    "collection_slug": "", "source": "", "files": ["a"]}
        merged = main._merge_install_record(existing, {
            "mod_id": 2018, "name": "ButterLib",
            "collection_slug": "pjkqjk", "source": "collection",
            "files": ["a"],
        })
        self.assertEqual(merged.get("collection_slug"), "")
        self.assertEqual(merged.get("source"), "")

    def test_a_collections_own_mod_keeps_its_slug(self):
        existing = {"mod_id": 5, "name": "X",
                    "collection_slug": "pjkqjk", "source": "collection",
                    "files": ["a"]}
        merged = main._merge_install_record(existing, {
            "mod_id": 5, "name": "X",
            "collection_slug": "pjkqjk", "source": "collection",
            "files": ["a"],
        })
        self.assertEqual(merged.get("collection_slug"), "pjkqjk")

    def test_a_fresh_install_keeps_whatever_it_says(self):
        merged = main._merge_install_record({}, {
            "mod_id": 5, "collection_slug": "pjkqjk",
            "source": "collection", "files": [],
        })
        self.assertEqual(merged.get("collection_slug"), "pjkqjk")


class TestBannerlordManifestGate(unittest.TestCase):
    """A Bannerlord module pins its game branch INSIDE the archive:
    SubModule.xml carries version attributes against the official modules.
    A collection targeting v1.2.11 on a v1.4.8 game should skip those mods
    with a named reason - the Elden Ring rule - not wear a blanket badge
    that reads as blocking everything. Michael: "What weve done for
    collections in other games is skipped the mods inside the collection
    that are broken or outdated rather than ruling out the entire
    collection - can something similar not be applied here?"."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _module(self, xml):
        with open(os.path.join(self.dir, "SubModule.xml"), "w",
                  encoding="utf-8") as f:
            f.write(xml)

    def test_a_v12_pin_on_a_v14_game_is_a_mismatch(self):
        self._module('<Module><Id value="X" /><DependedModuleMetadatas>'
                     '<DependedModuleMetadata id="Native" '
                     'order="LoadAfterThis" version="v1.2.*" />'
                     '</DependedModuleMetadatas></Module>')
        self.assertEqual(
            main._bl_manifest_game_mismatch(self.dir, "v1.4.8"), "v1.2")

    def test_a_matching_pin_is_not_a_mismatch(self):
        self._module('<Module><Id value="X" /><DependedModuleMetadatas>'
                     '<DependedModuleMetadata id="Native" '
                     'order="LoadAfterThis" version="v1.4.*" />'
                     '</DependedModuleMetadatas></Module>')
        self.assertEqual(
            main._bl_manifest_game_mismatch(self.dir, "v1.4.8"), "")

    def test_declaring_nothing_is_not_a_mismatch(self):
        # Most older mods declare nothing; the observed-crash verdicts
        # handle those. This gate acts only on an explicit claim.
        self._module('<Module><Id value="X" /><DependedModules>'
                     '<DependedModule Id="Native" />'
                     '</DependedModules></Module>')
        self.assertEqual(
            main._bl_manifest_game_mismatch(self.dir, "v1.4.8"), "")

    def test_dependent_version_on_official_modules_counts(self):
        self._module('<Module><Id value="X" /><DependedModules>'
                     '<DependedModule Id="Native" DependentVersion="v1.2.8" />'
                     '</DependedModules></Module>')
        self.assertEqual(
            main._bl_manifest_game_mismatch(self.dir, "v1.4.8"), "v1.2")

    def test_the_butr_placeholder_is_not_a_pin(self):
        # ButterLib, UIExtenderEx and MCM all declare version="v1.0.0.*"
        # against the official modules while running fine on v1.4.8: it is
        # BUTR's "any version" placeholder. Reading it as a real 1.0 pin
        # would have skipped the entire working library stack on its next
        # reinstall - MCM's real manifest is the fixture here.
        self._module('<Module><Id value="Bannerlord.MBOptionScreen" />'
                     '<DependedModuleMetadatas>'
                     '<DependedModuleMetadata id="Native" '
                     'order="LoadAfterThis" version="v1.0.0.*" />'
                     '<DependedModuleMetadata id="SandBoxCore" '
                     'order="LoadAfterThis" version="v1.0.0.*" '
                     'optional="true" /></DependedModuleMetadatas></Module>')
        self.assertEqual(
            main._bl_manifest_game_mismatch(self.dir, "v1.4.8"), "")

    def test_constraints_on_other_mods_do_not_count(self):
        # A version pin against RBM or MCM says nothing about the GAME.
        self._module('<Module><Id value="X" /><DependedModuleMetadatas>'
                     '<DependedModuleMetadata id="Bannerlord.MBOptionScreen" '
                     'order="LoadBeforeThis" version="v5.1.1" />'
                     '</DependedModuleMetadatas></Module>')
        self.assertEqual(
            main._bl_manifest_game_mismatch(self.dir, "v1.4.8"), "")

    def test_a_dll_module_is_recognised(self):
        self._module('<Module><Id value="X" /><SubModules><SubModule>'
                     '<DLLName value="ServeAsSoldier.dll"/>'
                     '</SubModule></SubModules></Module>')
        self.assertTrue(main._bl_module_ships_dll(self.dir))

    def test_an_asset_module_is_not(self):
        self._module('<Module><Id value="X" /><SubModules/></Module>')
        self.assertFalse(main._bl_module_ships_dll(self.dir))

    def test_the_collection_scope_dll_rule_is_wired(self):
        # Undeclared CODE mods from a version-pinned collection skip; the
        # same mod installed alone is untouched (record_source gates it).
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        i = source.index("_bl_module_ships_dll(os.path.join(mods_path, folder))")
        block = source[i - 600:i + 600]
        self.assertIn('record_source == "collection"', block)
        self.assertIn("_collection_built_for(", block)

    def test_unknown_installed_version_makes_no_claim(self):
        self._module('<Module><Id value="X" /><DependedModuleMetadatas>'
                     '<DependedModuleMetadata id="Native" '
                     'order="LoadAfterThis" version="v1.2.*" />'
                     '</DependedModuleMetadatas></Module>')
        self.assertEqual(main._bl_manifest_game_mismatch(self.dir, ""), "")


class TestBrowseFilters(unittest.TestCase):
    """The filter dropdown next to sort. Server-side via the v2 filter
    schema (categoryName, updatedAt) - introspected live before building,
    because the API is the boundary of what a filter can offer."""

    def test_category_goes_into_the_query(self):
        q = main._build_mods_query(False, category="Audio")
        self.assertIn('categoryName: [{ value: "Audio" }]', q)

    def test_category_quotes_are_stripped_not_injected(self):
        q = main._build_mods_query(False, category='Au"dio')
        self.assertIn('categoryName: [{ value: "Audio" }]', q)

    def test_updated_since_uses_epoch_seconds(self):
        # ISO datetimes break the backing Lucene query - the trending
        # window learned this first; the filter reuses the convention.
        q = main._build_mods_query(False, updated_since=1787240000)
        self.assertIn('updatedAt: [{ value: "1787240000", op: GT }]', q)

    def test_no_filter_means_no_filter_clauses(self):
        q = main._build_mods_query(False)
        self.assertNotIn("categoryName", q)
        self.assertNotIn("updatedAt: [", q)

    def test_minus_one_means_since_the_games_own_update(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def get_mods("):]
        fn = fn[:fn.index("async def _fetch")]
        self.assertIn("days == -1 and app_id", fn)
        self.assertIn("_game_updated_at(int(app_id))", fn)


class TestSkyrimCatalogQuarantine(unittest.TestCase):
    """Bethesda's 2026-08 update rewrote ContentCatalog.txt: entry keys went
    from "CSV2_5658" to "CSV2_<guid>" plus an AchievementSafe field, and a
    DOWNGRADED exe dies at boot parsing the guid (std::invalid_argument) -
    a crash with no visible cause. Community-verified fix: remove the file,
    the game regenerates it. Quarantined only when BOTH facts hold: new
    format AND old exe (by PE link timestamp, because a downgrade replaces
    the exe behind Steam's back and the manifest buildid lies)."""

    OLD_ENTRY = '"CSV2_5658" : { "Files" : [ "cc.bsa" ], "Title" : "X" }'
    NEW_ENTRY = ('"CSV2_9bbcdace-4556-4e87-b821-0c9b6f2958d0" : '
                 '{ "AchievementSafe" : false, "Title" : "X" }')

    def _pe(self, path, stamp):
        # A minimal valid PE: MZ header, e_lfanew at 0x3C pointing at the
        # PE signature, then machine+sections, then TimeDateStamp. Built
        # with bytes([...]) throughout - escape sequences in this file have
        # been mangled by shell heredocs twice already.
        head = bytearray(0x40)
        head[0:2] = b"MZ"
        head[0x3C:0x40] = (0x40).to_bytes(4, "little")
        pe = (bytes([0x50, 0x45, 0, 0])          # "PE", two zero bytes
              + bytes([0x64, 0x86, 0x01, 0x00])  # machine, section count
              + stamp.to_bytes(4, "little"))
        with open(path, "wb") as f:
            f.write(bytes(head) + pe)

    def test_pe_timestamp_reads_the_link_time(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        exe = os.path.join(d, "SkyrimSE.exe")
        self._pe(exe, 1700000000)
        self.assertEqual(main._pe_timestamp(exe), 1700000000)

    def test_pe_timestamp_rejects_non_pe(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "x.exe")
        with open(p, "wb") as f:
            f.write(b"not an exe at all, just bytes" * 4)
        self.assertEqual(main._pe_timestamp(p), 0)

    def test_the_new_format_is_recognised_and_the_old_is_not(self):
        self.assertTrue(main._SKYRIM_CATALOG_GUID_RE.search(self.NEW_ENTRY))
        self.assertFalse(main._SKYRIM_CATALOG_GUID_RE.search(self.OLD_ENTRY))

    def test_quarantine_needs_both_facts(self):
        # New-format catalog + OLD exe -> quarantined. Same catalog with a
        # CURRENT exe -> untouched, because the new exe reads it fine and
        # deleting would churn (the game regenerates it every launch).
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        exe = os.path.join(d, "SkyrimSE.exe")
        catalog_dir = os.path.join(
            d, "pfx", "drive_c", "users", "steamuser", "AppData", "Local",
            "Skyrim Special Edition")
        os.makedirs(catalog_dir)
        catalog = os.path.join(catalog_dir, "ContentCatalog.txt")

        import unittest.mock as mock
        with mock.patch.object(main, "_prefix_drive_c",
                               side_effect=lambda app_id, *p:
                               os.path.join(d, "pfx", "drive_c", *p)):
            # current exe: no action
            self._pe(exe, main._SKYRIM_UPDATE_EPOCH + 1000)
            with open(catalog, "w") as f:
                f.write(self.NEW_ENTRY)
            self.assertEqual(main._skyrim_cc_catalog_fix(489830, d), "")
            self.assertTrue(os.path.isfile(catalog))
            # old exe + old-format catalog: no action
            self._pe(exe, 1650000000)
            with open(catalog, "w") as f:
                f.write(self.OLD_ENTRY)
            self.assertEqual(main._skyrim_cc_catalog_fix(489830, d), "")
            self.assertTrue(os.path.isfile(catalog))
            # old exe + new-format catalog: quarantined
            with open(catalog, "w") as f:
                f.write(self.NEW_ENTRY)
            moved = main._skyrim_cc_catalog_fix(489830, d)
            self.assertEqual(moved, "ContentCatalog.txt.pre-update-backup")
            self.assertFalse(os.path.isfile(catalog))
            self.assertTrue(os.path.isfile(catalog + ".pre-update-backup"))


class TestModFileGameVersionDefault(unittest.TestCase):
    """The Engine Fixes dialog chain, third act. The preloader fix worked,
    the SKSE update worked, and then EngineFixes.dll 7.0.20 - the newest
    MAIN - died on a 1.7.99 game asking for an address library file that
    will never exist, while "Engine Fixes 7.0.21 beta for Skyrim AE 1.7.99"
    sat one row down the same page. When a file names the installed game's
    version, the author has answered the version question; the default
    should read the answer."""

    FILES = [
        {"file_id": 725753, "category_name": "MAIN",
         "name": "Engine Fixes - Main File", "version": "7.0.20",
         "description": ""},
        {"file_id": 794484, "category_name": "MAIN",
         "name": "Engine Fixes 7.0.21 beta for Skyrim AE 1.7.99",
         "version": "7.0.21", "description": ""},
        {"file_id": 489502, "category_name": "OLD_VERSION",
         "name": "(Part 1) SSE Engine Fixes for 1.6.1170 (v6.2)",
         "version": "6.2", "description": ""},
    ]

    def _with_exe(self, version_tuple):
        real = main._pe_file_version
        main._pe_file_version = lambda path: version_tuple
        self.addCleanup(setattr, main, "_pe_file_version", real)

    def _match(self):
        async def fake_files(domain, mid):
            return {"ok": True, "files": self.FILES}
        real = main.Plugin.get_mod_files
        main.Plugin.get_mod_files = lambda self_, d, m: fake_files(d, m)
        self.addCleanup(setattr, main.Plugin, "get_mod_files", real)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(main.Plugin().match_file_to_game(
                "skyrimspecialedition", 17230, "Skyrim Special Edition",
                "SkyrimSE.exe",
            ))
        finally:
            loop.close()

    def test_the_game_binary_version_is_readable_as_an_endpoint(self):
        # The companion gate and anything else that reasons about "which
        # game build is this really" needs the exe's own answer - the
        # manifest's buildid lies about downgraded games.
        real = main._pe_file_version
        main._pe_file_version = lambda path: (1, 7, 99, 0)
        self.addCleanup(setattr, main, "_pe_file_version", real)
        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(main.Plugin().get_game_binary_version(
                "Skyrim Special Edition", "SkyrimSE.exe"))
        finally:
            loop.close()
        self.assertEqual(r.get("version"), "1.7.99")

    def test_the_latest_game_gets_the_file_that_names_it(self):
        self._with_exe((1, 7, 99, 0))
        r = self._match()
        self.assertEqual(r.get("file_id"), 794484)
        self.assertEqual(r.get("game_version"), "1.7.99")

    def test_a_downgraded_game_gets_the_old_version_file(self):
        # The correct build for 1.6.1170 is an OLD_VERSION file. That is the
        # entire point: newest-anything is the wrong axis for these mods.
        self._with_exe((1, 6, 1170, 0))
        r = self._match()
        self.assertEqual(r.get("file_id"), 489502)

    def test_a_version_nobody_names_changes_nothing(self):
        self._with_exe((1, 6, 640, 0))
        r = self._match()
        self.assertEqual(r.get("file_id"), 0)

    def test_no_process_name_means_no_opinion(self):
        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(main.Plugin().match_file_to_game(
                "skyrimspecialedition", 17230, "Skyrim Special Edition", "",
            ))
        finally:
            loop.close()
        self.assertEqual(r.get("file_id"), 0)


class TestFrameworkUpdates(unittest.TestCase):
    """Script extenders write no install record, so check_updates - which
    walks records - could never see them. SKSE, the mod everything else
    depends on, has never appeared in the Updates tab. Michael, with 2.2.6
    sitting on a 1.7.99 game: "its quite an important one to need updating."

    Read from disk rather than from a new record, because every existing
    install has no record and a record can drift from the binary."""

    def _check(self, files, exe_version, loader_version=(0, 2, 3, 0)):
        """Run check_framework_update against a fake page and exe."""
        async def fake_files(domain, mid):
            return {"ok": True, "files": files}
        real_files = main.Plugin.get_mod_files
        main.Plugin.get_mod_files = lambda self_, d, m: fake_files(d, m)
        self.addCleanup(setattr, main.Plugin, "get_mod_files", real_files)

        real_pe = main._pe_file_version
        exe_name = "SkyrimSE.exe"

        def fake_pe(path):
            return exe_version if path.endswith(exe_name) else loader_version

        main._pe_file_version = fake_pe
        self.addCleanup(setattr, main, "_pe_file_version", real_pe)

        real_paths = main._game_paths
        world = tempfile.mkdtemp(prefix="fwsup-")
        self.addCleanup(shutil.rmtree, world, ignore_errors=True)
        with open(os.path.join(world, "skse64_loader.exe"), "wb") as fh:
            fh.write(b"MZ")
        main._game_paths = lambda install_dir, subdir: (world, world, world)
        self.addCleanup(setattr, main, "_game_paths", real_paths)

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                main.Plugin().check_framework_update(
                    "skyrimspecialedition", 30379, "Skyrim Special Edition",
                    "skse64_loader.exe", exe_name, ["GOG"],
                )
            )
        finally:
            loop.close()

    # The REAL SKSE page as of 2026-08-27, the day Bethesda shipped 1.7.104.
    SKSE_REAL = [
        {"file_id": 792372, "category_name": "MAIN", "version": "2.3.0",
         "name": "Skyrim Script Extender (SKSE64) Steam",
         "description": "Compatible with Skyrim Special Edition 1.7.99 from Steam"},
        {"file_id": 470991, "category_name": "MAIN", "version": "2.2.6",
         "name": "Skyrim Script Extender (SKSE64) GOG",
         "description": "Compatible with Skyrim Special Edition 1.6.1179 from GOG.com"},
        {"file_id": 792256, "category_name": "OLD_VERSION", "version": "2.2.8",
         "name": "Skyrim Script Extender (SKSE64) Steam",
         "description": "Compatible with Skyrim Special Edition 1.6.1170 from Steam"},
    ]

    def test_a_game_newer_than_every_published_build_says_so(self):
        # Bethesda shipped 1.7.104 mid-afternoon; SKSE's newest was for
        # 1.7.99. Michael got "you are using a newer version of Skyrim than
        # this SKSE64 supports" and the plugin had said nothing at all.
        r = self._check(self.SKSE_REAL, (1, 7, 104, 0))
        self.assertTrue(r.get("unsupported_game"))
        self.assertEqual(r.get("game_version"), "1.7.104")
        self.assertEqual(r.get("newest_supported"), "1.7.99")
        # NOT an update: there is nothing to install that would help.
        self.assertFalse(r.get("update_available"))

    def test_a_supported_game_is_not_called_unsupported(self):
        r = self._check(self.SKSE_REAL, (1, 6, 1170, 0))
        self.assertFalse(r.get("unsupported_game"))
        self.assertTrue(r.get("update_available"))
        self.assertEqual(r.get("target_version"), "2.2.8")

    def test_an_unreadable_exe_stays_silent(self):
        # No version means no opinion - never warn on a guess about the mod
        # everything else depends on.
        r = self._check(self.SKSE_REAL, None)
        self.assertFalse(r.get("unsupported_game"))

    def test_a_page_that_states_no_versions_stays_silent(self):
        # Without stated versions we cannot tell "unsupported" from "the
        # author never writes versions down".
        vague = [{"file_id": 1, "category_name": "MAIN", "version": "1",
                  "name": "Script Extender", "description": "Install it."}]
        r = self._check(vague, (1, 7, 104, 0))
        self.assertFalse(r.get("unsupported_game"))

    def test_a_loader_and_its_page_version_agree_on_the_numbers(self):
        # SKSE: skse64_loader.exe reports 0.2.2.6 for the file called
        # "2.2.6" - a leading zero component. F4SE reports 0.7.9.0 for the
        # file called "0.7.9" - a trailing one. Anchoring either end fails
        # one of them, so the match is a contiguous run of numbers.
        self.assertTrue(main._framework_build_matches((0, 2, 2, 6), "2.2.6"))
        self.assertTrue(main._framework_build_matches((0, 7, 9, 0), "0.7.9"))

    def test_a_mismatched_build_is_reported(self):
        # The exact case on device: 2.2.6 installed, 2.3.0 needed.
        self.assertFalse(main._framework_build_matches((0, 2, 2, 6), "2.3.0"))
        self.assertFalse(main._framework_build_matches((0, 7, 7, 0), "0.7.9"))

    def test_an_unreadable_loader_is_not_called_a_match(self):
        # No version means no opinion, and the caller treats non-match as
        # "offer the right build" - which is the safe direction here.
        self.assertFalse(main._framework_build_matches(None, "2.3.0"))

    def test_a_longer_wanted_version_cannot_match(self):
        self.assertFalse(main._framework_build_matches((2, 3), "2.3.0.1"))

    def test_versions_are_compared_as_numbers_not_text(self):
        self.assertEqual(main._version_parts("v2.3.0-beta"), [2, 3, 0])
        self.assertEqual(main._version_parts(""), [])

    def test_a_missing_loader_is_not_an_update(self):
        # Not installed is Step 1's business. Reporting an update for
        # something absent would put a row in the tab that cannot apply.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def check_framework_update"):]
        fn = fn[:fn.index("async def install_framework")]
        self.assertIn("if not os.path.isfile(loader):", fn)
        self.assertIn('"update_available": False', fn)

    def test_the_target_is_the_game_s_build_not_the_newest(self):
        # A downgraded game's correct SKSE is an OLD_VERSION file. Offering
        # "the newest" is what broke this in the first place.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def check_framework_update"):]
        fn = fn[:fn.index("async def install_framework")]
        self.assertIn("_framework_file_for_game_version(", fn)
        self.assertNotIn("_pick_main_file(", fn)


class TestFomodGameVersion(unittest.TestCase):
    """The week's root cause, found at the end of it. Engine Fixes' FOMOD
    offers an AE dll and an SE dll and declares with gameDependency which
    game version wants which; we treated gameDependency as always-satisfied,
    so the wizard offered both as equal choices to a person whose game is
    CALLED Skyrim Special Edition. The natural pick is the wrong dll, which
    loads and then kills the game asking for an address library file that
    will never exist - the same dll produced both of this week's REL/ID
    dialogs on 1.7.99 and on 1.6.1170. XML below is the REAL ModuleConfig
    from the mod, trimmed."""

    XML = (
        '<config><moduleName>EngineFixes</moduleName>'
        '<requiredInstallFiles><folder source="Required" destination=""/>'
        '</requiredInstallFiles>'
        '<installSteps order="Explicit"><installStep name="Main">'
        '<optionalFileGroups order="Explicit">'
        '<group name="DLL" type="SelectExactlyOne"><plugins order="Explicit">'
        '<plugin name="SSE v1.6.1170 (Anniversary Edition)">'
        '<description>For 1.6.1170</description>'
        '<files><folder source="AE/SKSE/Plugins" destination="SKSE/Plugins"/></files>'
        '<typeDescriptor><dependencyType><defaultType name="Optional"/>'
        '<patterns>'
        '<pattern><dependencies><gameDependency version="1.6.1170"/></dependencies>'
        '<type name="Recommended"/></pattern>'
        '<pattern><dependencies><gameDependency version="1.5.97"/></dependencies>'
        '<type name="Optional"/></pattern>'
        '</patterns></dependencyType></typeDescriptor></plugin>'
        '<plugin name="SSE v1.5.97 (Special Edition)">'
        '<description>For 1.5.97</description>'
        '<files><folder source="SE/SKSE/Plugins" destination="SKSE/Plugins"/></files>'
        '<typeDescriptor><dependencyType><defaultType name="Optional"/>'
        '<patterns>'
        '<pattern><dependencies><gameDependency version="1.6.1170"/></dependencies>'
        '<type name="Optional"/></pattern>'
        '<pattern><dependencies><gameDependency version="1.5.97"/></dependencies>'
        '<type name="Recommended"/></pattern>'
        '</patterns></dependencyType></typeDescriptor></plugin>'
        '</plugins></group></optionalFileGroups></installStep></installSteps>'
        '</config>'
    )

    def _wizard(self, game_version):
        scratch = tempfile.mkdtemp(prefix="fomod-gv-")
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        os.makedirs(os.path.join(scratch, "fomod"))
        with open(os.path.join(scratch, "fomod", "ModuleConfig.xml"), "w",
                  encoding="utf-8") as fh:
            fh.write(self.XML)
        wizard, _ctx = main._parse_fomod(scratch, scratch, game_version)
        return wizard["steps"][0]["groups"][0]["plugins"]

    def test_the_installed_game_marks_its_own_build(self):
        # 1.6.1170: the AE option is Recommended and SAYS it matches; the SE
        # option says which game it is actually for. Michael picked "Special
        # Edition" twice because that is his game's name - the label is the
        # fix, the type alone was not enough.
        ae, se = self._wizard("1.6.1170")
        self.assertEqual(ae["type"], "Recommended")
        self.assertIn("matches your game (1.6.1170)", ae["name"])
        self.assertEqual(se["type"], "Optional")
        self.assertIn("for game 1.5.97", se["name"])

    def test_a_downgrade_below_both_recommends_neither_wrongly(self):
        # Game 1.5.97: SE is the recommended one, AE marked for 1.6.1170.
        ae, se = self._wizard("1.5.97")
        self.assertEqual(se["type"], "Recommended")
        self.assertIn("matches your game (1.5.97)", se["name"])
        self.assertIn("for game 1.6.1170", ae["name"])

    def test_no_game_version_keeps_the_old_behaviour(self):
        # An unreadable exe must not change anything: gameDependency reads
        # as satisfied, names stay the author's.
        ae, se = self._wizard("")
        self.assertNotIn("matches your game", ae["name"])
        self.assertNotIn("for game", se["name"])

    def test_versions_compare_numerically_not_textually(self):
        # 1.7.99 >= 1.6.1170 must hold even though "1.7.99" < "1.6.1170" as
        # strings. On 1.7.99 the AE (1.6.1170+) option is the recommended
        # one of the two on this page.
        ae, se = self._wizard("1.7.99")
        self.assertEqual(ae["type"], "Recommended")


class TestCompanionRecordsFollowTheirParent(unittest.TestCase):
    def test_check_updates_never_offers_a_companion_alone(self):
        # The Updates tab offered "Engine Fixes - SKSE64 Preloader" and
        # applying it errored - the GOOD outcome, since installLatest picks
        # the page's default file, which is a different file entirely.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def check_updates"):]
        fn = fn[:fn.index("async def", 10)]
        self.assertIn('rec.get("source") != "companion"', fn)


class TestFrameworkGameVersionMatch(unittest.TestCase):
    """Script extenders are compiled against ONE game binary and publish one
    file per build, with the game version in the description. Installing the
    newest against a deliberately downgraded game was our first
    multiple-report bug: "I have specifically downgraded because I knew how
    much shit Skyrim 1.7.99 would break." The file list here is the REAL
    SKSE page, captured 2026-08-24."""

    SKSE = [
        {"file_id": 792372, "category_name": "MAIN",
         "name": "Skyrim Script Extender (SKSE64) Steam", "version": "2.3.0",
         "description": "Compatible with Skyrim Special Edition 1.7.99 from Steam"},
        {"file_id": 470991, "category_name": "MAIN",
         "name": "Skyrim Script Extender (SKSE64) GOG", "version": "2.2.6",
         "description": "Compatible with Skyrim Special Edition 1.6.1179 from GOG.com"},
        {"file_id": 462377, "category_name": "OLD_VERSION",
         "name": "Skyrim Script Extender (SKSE64)  Steam", "version": "2.2.6",
         "description": "Compatible with Skyrim Special Edition 1.6.1170 from Steam"},
        {"file_id": 792256, "category_name": "OLD_VERSION",
         "name": "Skyrim Script Extender (SKSE64) Steam", "version": "2.2.8",
         "description": "Compatible with Skyrim Special Edition 1.6.1170 from Steam"},
        {"file_id": 255897, "category_name": "OLD_VERSION",
         "name": "Skyrim Script Extender (SKSE64)", "version": "2.1.2",
         "description": "Compatible with Skyrim Special Edition 1.6.318 from Steam"},
    ]

    def _with_exe_version(self, version_tuple):
        real = main._pe_file_version
        main._pe_file_version = lambda path: version_tuple
        self.addCleanup(setattr, main, "_pe_file_version", real)

    def test_a_downgraded_game_gets_its_own_build(self):
        self._with_exe_version((1, 6, 1170, 0))
        f, ver = main._framework_file_for_game_version(self.SKSE, ["GOG"], "x")
        self.assertEqual(ver, "1.6.1170")
        # TWO files claim 1.6.1170 (2.2.6 and 2.2.8): the newer upload wins.
        self.assertEqual(f["file_id"], 792256)

    def test_the_latest_game_still_gets_the_latest(self):
        self._with_exe_version((1, 7, 99, 0))
        f, ver = main._framework_file_for_game_version(self.SKSE, ["GOG"], "x")
        self.assertEqual(f["file_id"], 792372)

    def test_avoided_stores_stay_avoided(self):
        # 1.6.1179 exists ONLY as the GOG build; matching it would install a
        # binary that refuses to run against the Steam game.
        self._with_exe_version((1, 6, 1179, 0))
        f, ver = main._framework_file_for_game_version(self.SKSE, ["GOG"], "x")
        self.assertIsNone(f)
        self.assertEqual(ver, "1.6.1179")

    def test_versions_do_not_match_inside_longer_ones(self):
        # 1.6.117 must not match inside "1.6.1170", nor 1.6.11 inside either.
        self._with_exe_version((1, 6, 117, 0))
        f, _ = main._framework_file_for_game_version(self.SKSE, [], "x")
        self.assertIsNone(f)

    def test_an_unreadable_exe_changes_nothing(self):
        # No version, no opinion: the caller falls back to newest-MAIN,
        # which is yesterday's behaviour, not an error.
        self._with_exe_version(None)
        f, ver = main._framework_file_for_game_version(self.SKSE, [], "x")
        self.assertIsNone(f)
        self.assertEqual(ver, "")

    def test_the_installer_actually_asks_for_the_match(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def _install_framework_inner"):]
        fn = fn[:fn.index("if install_kind ==")]
        self.assertIn("_framework_file_for_game_version(", fn)
        self.assertIn("process_name", fn)


class TestAdultGateRegions(unittest.TestCase):
    """Age verification exists only where the law demands it (the UK's OSA).
    Requiring it from everyone meant a Dutch user - whose website happily
    shows adult content on the preference alone - could never open the gate
    from the plugin, because the verification flow does not exist in their
    country. The plugin now mirrors the platform: preference everywhere,
    plus verification only where required. Confirmed against the API's own
    source: the country list is Rails config, the jurisdiction is
    Cloudflare's CF-IPCountry."""

    def _gate(self, **kw):
        settings = main._load_settings()
        settings["content_gate"] = kw
        main._save_settings(settings)
        self.addCleanup(main._save_settings, {})

    def test_preference_alone_opens_the_gate_where_law_allows(self):
        self._gate(adult_pref=True, age_verified=False,
                   verification_required=False, country="NL")
        self.assertTrue(main._show_adult())

    def test_the_uk_still_requires_verification(self):
        self._gate(adult_pref=True, age_verified=False,
                   verification_required=True, country="GB")
        self.assertFalse(main._show_adult())

    def test_a_verified_uk_account_is_open(self):
        self._gate(adult_pref=True, age_verified=True,
                   verification_required=True, country="GB")
        self.assertTrue(main._show_adult())

    def test_the_preference_is_never_optional(self):
        self._gate(adult_pref=False, age_verified=True,
                   verification_required=False, country="NL")
        self.assertFalse(main._show_adult())

    def test_a_gate_cached_before_the_region_field_stays_closed(self):
        # Old caches have no verification_required key. They must read as
        # required - the stricter old rule - until the next refresh, never
        # as open.
        self._gate(adult_pref=True, age_verified=False)
        self.assertFalse(main._show_adult())

    def test_an_unknown_country_fails_closed(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def _refresh_content_gate"):]
        fn = fn[:fn.index("async def _gql_query_vars")]
        self.assertIn("if country else True", fn)

    def test_the_endpoints_report_one_rule(self):
        # show_adult restated in an endpoint is how two rules drift apart.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def refresh_content_gate"):]
        fn = fn[:fn.index("async def dismiss_update")]
        self.assertIn('"show_adult": _show_adult()', fn)
        self.assertNotIn('gate["adult_pref"]) and bool(gate["age_verified"]', fn)


class TestSkyrimCatalogSelfHeals(unittest.TestCase):
    """A new-format ContentCatalog crashes a DOWNGRADED Skyrim at boot with
    std::invalid_argument on the guid key and nothing visible to the player.
    Confirmed by Nexus's own investigation (2026-08): Bethesda re-keyed
    Creations to guids, and the community fix is removing the file, which
    the game regenerates per-exe at launch.

    The repair existed since 1.0.2 but only ran inside the Health page,
    which is no use to someone whose game will not start."""

    def test_the_repair_runs_when_the_panel_opens(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def get_game_status"):]
        fn = fn[:fn.index("async def", 10)]
        self.assertIn("_skyrim_cc_catalog_fix(app_id, install_path)", fn)
        # Gated on the game actually being Skyrim: every other game would
        # otherwise pay for a lookup that can never apply to it.
        self.assertIn('"SkyrimSE.exe"', fn)

    def _world(self, exe_stamp, catalog_text):
        """A fake game + prefix, with the exe's PE timestamp forced."""
        world = tempfile.mkdtemp(prefix="skyrim-cc-")
        self.addCleanup(shutil.rmtree, world, ignore_errors=True)
        install = os.path.join(world, "common", "Skyrim Special Edition")
        os.makedirs(install)
        with open(os.path.join(install, "SkyrimSE.exe"), "wb") as fh:
            fh.write(b"MZ")
        real_stamp = main._pe_timestamp
        main._pe_timestamp = lambda path: exe_stamp
        self.addCleanup(setattr, main, "_pe_timestamp", real_stamp)

        local = os.path.join(world, "pfx", "Local", "Skyrim Special Edition")
        os.makedirs(local)
        catalog = os.path.join(local, "ContentCatalog.txt")
        if catalog_text is not None:
            with open(catalog, "w", encoding="utf-8") as fh:
                fh.write(catalog_text)
        real_prefix = main._prefix_drive_c
        main._prefix_drive_c = lambda app_id, *parts: os.path.join(
            world, "pfx", *[p for p in parts if p not in
                            ("users", "steamuser", "AppData")]
        )
        self.addCleanup(setattr, main, "_prefix_drive_c", real_prefix)
        return install, catalog

    NEW_FORMAT = (
        '{ "CSV2_9bbcdace-4556-4e87-b821-0c9b6f2958d0" : { '
        '"AchievementSafe" : false, "Title" : "Necromantic Grimoire" } }'
    )
    OLD_FORMAT = '{ "CSV2_5658" : { "Title" : "Necromantic Grimoire" } }'

    def test_a_downgraded_exe_with_a_new_catalog_is_repaired(self):
        install, catalog = self._world(1_780_000_000, self.NEW_FORMAT)
        moved = main._skyrim_cc_catalog_fix(1, install)
        self.assertTrue(moved)
        self.assertFalse(os.path.isfile(catalog))
        # Quarantined, never deleted: it is the user's purchase history.
        self.assertTrue(os.path.isfile(catalog + ".pre-update-backup"))

    def test_a_current_exe_is_left_alone(self):
        # The new format is correct for the new exe. Touching it would churn
        # a file the game rewrites every launch.
        install, catalog = self._world(1_790_000_000, self.NEW_FORMAT)
        self.assertEqual(main._skyrim_cc_catalog_fix(1, install), "")
        self.assertTrue(os.path.isfile(catalog))

    def test_an_old_catalog_on_an_old_exe_is_left_alone(self):
        install, catalog = self._world(1_780_000_000, self.OLD_FORMAT)
        self.assertEqual(main._skyrim_cc_catalog_fix(1, install), "")
        self.assertTrue(os.path.isfile(catalog))

    def test_it_is_idempotent(self):
        # The regenerated file is old-format, so a second pass must do
        # nothing - this runs on every panel open.
        install, catalog = self._world(1_780_000_000, self.NEW_FORMAT)
        self.assertTrue(main._skyrim_cc_catalog_fix(1, install))
        with open(catalog, "w", encoding="utf-8") as fh:
            fh.write(self.OLD_FORMAT)
        self.assertEqual(main._skyrim_cc_catalog_fix(1, install), "")

    def test_no_catalog_at_all_is_not_an_error(self):
        install, _catalog = self._world(1_780_000_000, None)
        self.assertEqual(main._skyrim_cc_catalog_fix(1, install), "")


class TestPalworldPalSchema(unittest.TestCase):
    """PalSchema mods are folders of json under the framework's schema dirs
    (verified against a live mod: <Name>/raw/<file>.json and nothing else).
    They carry none of the UE4SS markers, so without their own route they
    fell through to the pak path and landed json where nothing reads it."""

    def _payload(self, layout):
        scratch = tempfile.mkdtemp(prefix="palschema-")
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        for rel in layout:
            p = os.path.join(scratch, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("{}")
        return scratch

    def test_a_real_palschema_shape_is_recognised(self):
        s = self._payload(["mEi_Lab_InstantCost1_P/raw/InstantCost1_P.json"])
        self.assertTrue(main._looks_like_palschema_mod(s))
        # And it is NOT a UE4SS mod: no Scripts, dlls or enabled.txt.
        self.assertFalse(main._looks_like_ue4ss_mod(s))

    def test_a_pak_or_lua_mod_is_not_claimed(self):
        s = self._payload(["BNLrelease_P.pak"])
        self.assertFalse(main._looks_like_palschema_mod(s))
        s2 = self._payload(["MapUnlocker/Scripts/main.lua",
                            "MapUnlocker/enabled.txt"])
        self.assertFalse(main._looks_like_palschema_mod(s2))

    def test_json_outside_schema_dirs_is_not_claimed(self):
        # Plenty of mods ship a config.json; that is not a PalSchema mod.
        s = self._payload(["SomeMod/config.json"])
        self.assertFalse(main._looks_like_palschema_mod(s))

    def test_routing_keeps_the_wrapper_as_the_mods_identity(self):
        s = self._payload(["mEi_Lab_InstantCost1_P/raw/InstantCost1_P.json"])
        game = tempfile.mkdtemp(prefix="palgame-")
        self.addCleanup(shutil.rmtree, game, ignore_errors=True)
        route = main._route_palschema_payload(
            s, game, "Pal/Binaries/Win64/ue4ss/Mods/PalSchema/mods", "Instant Research"
        )
        self.assertEqual(route["folder"], "mEi_Lab_InstantCost1_P")
        self.assertEqual(route["mode"], "folder")
        self.assertTrue(os.path.isfile(os.path.join(
            game, "Pal", "Binaries", "Win64", "ue4ss", "Mods", "PalSchema",
            "mods", "mEi_Lab_InstantCost1_P", "raw", "InstantCost1_P.json",
        )))

    def test_loose_schema_dirs_get_wrapped_with_the_mods_name(self):
        s = self._payload(["raw/things.json"])
        game = tempfile.mkdtemp(prefix="palgame2-")
        self.addCleanup(shutil.rmtree, game, ignore_errors=True)
        route = main._route_palschema_payload(
            s, game, "PalSchema/mods", "Loose Mod"
        )
        self.assertEqual(route["folder"], "Loose Mod")
        self.assertTrue(os.path.isfile(os.path.join(
            game, "PalSchema", "mods", "Loose Mod", "raw", "things.json",
        )))

    def test_the_install_branch_requires_palschema_itself(self):
        # json copied into a skeleton of the framework's dirs silently never
        # loads, so a missing PalSchema is a refusal with the fix named.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        i = source.index("_looks_like_palschema_mod(scratch)")
        block = source[i:i + 1600]
        self.assertIn('os.path.join(schema_root, "dlls", "main.dll")', block)
        self.assertIn("Run Step 1", block)


class TestFrameworkFlattenSubdirRelative(unittest.TestCase):
    def test_the_wrapper_that_is_the_mod_survives(self):
        # PalSchema ships PalSchema/dlls/main.dll destined for ue4ss/Mods.
        # The flatten guard compared its wrapper against the full detect
        # path's first component ("Pal"), so the folder that IS the mod got
        # flattened away into loose files.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index('detect_rel = detect_file'):]
        fn = fn[:600]
        self.assertIn("install_subdir.lower()", fn)
        self.assertIn('detect_rel.split("/")[0].lower()', fn)


class TestSteamLibraries(unittest.TestCase):
    """Games on an SD card or a second drive live in another Steam library,
    listed in libraryfolders.vdf. The first bug report from a real user was
    Witcher 3 on a microSD showing "game not found": only the main library
    was ever searched. Everything here runs against fake libraries on disk,
    because the parsing and the fallbacks are exactly where this breaks."""

    def _make_world(self):
        world = tempfile.mkdtemp(prefix="steam-libs-")
        self.addCleanup(shutil.rmtree, world, ignore_errors=True)
        main_apps = os.path.join(world, "main", "steamapps")
        sd_apps = os.path.join(world, "sdcard", "steamapps")
        os.makedirs(os.path.join(main_apps, "common"), exist_ok=True)
        os.makedirs(os.path.join(sd_apps, "common"), exist_ok=True)
        vdf = (
            '"libraryfolders"' + chr(10) + "{" + chr(10)
            + '  "0"' + chr(10) + "  {" + chr(10)
            + '    "path"    "' + os.path.join(world, "main").replace(os.sep, "/") + '"' + chr(10)
            + "  }" + chr(10)
            + '  "1"' + chr(10) + "  {" + chr(10)
            + '    "path"    "' + os.path.join(world, "sdcard").replace(os.sep, "/") + '"' + chr(10)
            + "  }" + chr(10) + "}" + chr(10)
        )
        with open(os.path.join(main_apps, "libraryfolders.vdf"), "w",
                  encoding="utf-8") as fh:
            fh.write(vdf)
        old = main.STEAM_COMMON
        main.STEAM_COMMON = os.path.join(main_apps, "common")
        self.addCleanup(setattr, main, "STEAM_COMMON", old)
        return main_apps, sd_apps

    def test_a_game_on_the_sd_card_is_found(self):
        main_apps, sd_apps = self._make_world()
        game = os.path.join(sd_apps, "common", "The Witcher 3")
        os.makedirs(os.path.join(game, "mods"))
        install_path, mods_path, _dis = main._game_paths("The Witcher 3", "mods")
        self.assertEqual(os.path.realpath(install_path), os.path.realpath(game))
        self.assertTrue(os.path.isdir(mods_path))

    def test_the_main_library_wins_when_both_have_the_game(self):
        # Steam does not install one game twice, but a leftover folder on a
        # removed-and-restored card could look like one. The main library is
        # searched first, deterministically.
        main_apps, sd_apps = self._make_world()
        for apps in (main_apps, sd_apps):
            os.makedirs(os.path.join(apps, "common", "Skyrim"), exist_ok=True)
        install_path, _m, _d = main._game_paths("Skyrim", "Data")
        self.assertTrue(
            os.path.realpath(install_path).startswith(
                os.path.realpath(main_apps)
            )
        )

    def test_a_missing_game_still_names_the_conventional_path(self):
        # "Not installed" messaging shows this path; it must stay the main
        # library's, not become empty.
        self._make_world()
        install_path, _m, _d = main._game_paths("Not A Game", "mods")
        self.assertIn("Not A Game", install_path)
        self.assertTrue(install_path.startswith(main.STEAM_COMMON))

    def test_the_prefix_follows_the_game_onto_the_card(self):
        # compatdata sits in the library that holds the game. The Frosty
        # redirect writes into this prefix, so pointing at the main library
        # for an SD-card game would edit a registry the game never reads.
        main_apps, sd_apps = self._make_world()
        pfx = os.path.join(sd_apps, "compatdata", "292030", "pfx", "drive_c")
        os.makedirs(pfx)
        got = main._prefix_drive_c(292030)
        self.assertEqual(os.path.realpath(got), os.path.realpath(pfx))

    def test_a_game_with_no_prefix_anywhere_falls_back_to_main(self):
        main_apps, _sd = self._make_world()
        got = main._prefix_drive_c(111)
        self.assertTrue(
            os.path.realpath(got).startswith(os.path.realpath(main_apps))
        )

    def test_the_appmanifest_is_found_in_the_owning_library(self):
        main_apps, sd_apps = self._make_world()
        acf = os.path.join(sd_apps, "appmanifest_292030.acf")
        with open(acf, "w", encoding="utf-8") as fh:
            fh.write('"AppState" { "buildid" "424242" }')
        self.assertEqual(main._steam_build_id(292030), "424242")

    def test_no_vdf_means_the_main_library_only(self):
        main_apps, _sd = self._make_world()
        os.remove(os.path.join(main_apps, "libraryfolders.vdf"))
        libs = main._steam_libraries()
        self.assertEqual(
            [os.path.realpath(p) for p in libs],
            [os.path.realpath(main_apps)],
        )

    def test_the_main_library_is_not_listed_twice(self):
        # libraryfolders.vdf lists the main library too. Duplicates would
        # make every lookup stat the same directories twice.
        main_apps, sd_apps = self._make_world()
        libs = [os.path.realpath(p) for p in main._steam_libraries()]
        self.assertEqual(len(libs), len(set(libs)))
        self.assertEqual(libs[0], os.path.realpath(main_apps))

    def test_an_unplugged_library_is_skipped(self):
        # The vdf remembers a card that is not inserted; its path must not
        # produce phantom lookups.
        main_apps, sd_apps = self._make_world()
        shutil.rmtree(os.path.dirname(sd_apps))
        libs = [os.path.realpath(p) for p in main._steam_libraries()]
        self.assertEqual(libs, [os.path.realpath(main_apps)])


class TestFrostbiteGames(unittest.TestCase):
    """Battlefront II mods are COMPILED, not copied: an .fbmod is converted and
    the whole enabled set is rebuilt into a ModData tree, then the game is
    redirected at it. Everything here exists because that differs from every
    other supported game (see docs/frosty-swbf2/WORKING.md)."""

    def test_the_toolkit_upgrade_is_versioned_and_silent(self):
        # Build 2 of the compiler fixed replaced meshes rendering as shards.
        # A user cannot be asked to know their compiler is stale, so an
        # installed toolkit older than the current build is replaced at the
        # next install or toggle, invisibly.
        self.assertGreaterEqual(main.FROSTY_TOOLKIT_VERSION, 2)
        self.assertIn(f"frosty-toolkit-{main.FROSTY_TOOLKIT_VERSION}",
                      main.FROSTY_TOOLKIT_URL)
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        for fn_name in ("async def install_frosty_mod",
                        "async def set_frosty_mod_enabled"):
            fn = source[source.index(fn_name):]
            fn = fn[:3000]
            self.assertIn("_frosty_ensure_toolkit()", fn,
                          f"{fn_name} can run a stale compiler")

    def test_a_missing_marker_reads_as_the_old_build(self):
        # Build 1 predates the marker, so its absence must mean "stale", or
        # every existing install would never upgrade.
        home = tempfile.mkdtemp(prefix="frosty-marker-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        old_runtime = main.decky.DECKY_PLUGIN_RUNTIME_DIR
        main.decky.DECKY_PLUGIN_RUNTIME_DIR = home
        self.addCleanup(
            setattr, main.decky, "DECKY_PLUGIN_RUNTIME_DIR", old_runtime
        )
        os.makedirs(main._frosty_root(), exist_ok=True)
        self.assertFalse(main._frosty_toolkit_current())
        with open(main._frosty_toolkit_marker(), "w", encoding="utf-8") as fh:
            fh.write(str(main.FROSTY_TOOLKIT_VERSION))
        self.assertTrue(main._frosty_toolkit_current())
        # A future build must also count as current, or a downgrade loop
        # starts the moment two plugin versions coexist.
        with open(main._frosty_toolkit_marker(), "w", encoding="utf-8") as fh:
            fh.write(str(main.FROSTY_TOOLKIT_VERSION + 1))
        self.assertTrue(main._frosty_toolkit_current())

    def test_installing_the_toolkit_stamps_and_clears_the_cache(self):
        # The SDK cache was produced by the previous build. Keeping it is how
        # a fixed compiler goes on producing yesterday's bugs.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def _frosty_install_toolkit"):]
        fn = fn[:fn.index("def _frosty_game_exe")]
        self.assertIn("_frosty_toolkit_marker()", fn)
        self.assertIn('_force_rmtree(os.path.join(root, "Caches"))', fn)

    def test_a_first_install_is_still_the_users_step(self):
        # The silent path is for UPGRADES. The first 40 MB download stays the
        # QAM's explicit Step 1 - a brand new user should see what is being
        # set up, not have it happen mid-install.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def _frosty_ensure_toolkit"):]
        fn = fn[:fn.index("def _frosty_installed")]
        self.assertIn("Install the mod compiler first", fn)

    def test_the_toolkit_download_is_pinned(self):
        # A truncated 40 MB download would otherwise fail much later, inside
        # the compiler, with an incomprehensible error.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("FROSTY_TOOLKIT_SHA256", source)
        fn = source[source.index("async def _frosty_install_toolkit"):]
        fn = fn[:fn.index("def _frosty_game_exe")]
        self.assertIn("hashlib.sha256()", fn)
        self.assertIn("digest.hexdigest() != FROSTY_TOOLKIT_SHA256", fn)

    def test_the_compiler_gets_a_clean_environment(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def _frosty_run"):]
        fn = fn[:fn.index("def _strip_ansi")]
        self.assertIn("env=_host_env()", fn)

    def test_a_pack_is_verified_before_it_is_offered(self):
        # The read-back check is what turns "the game crashed" into "this mod
        # was not applied". It never disagreed with the game on device.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def _frosty_compile"):]
        fn = fn[:fn.index("def _prefix_drive_c")]
        # The call gained a progress argument; the point is that "load" runs.
        self.assertIn('_frosty_run(["load", check_exe]', fn)
        self.assertIn("the game cannot read", fn)
        # A failed verification must leave nothing behind.
        self.assertIn("_force_rmtree(pack)", fn)

    def test_a_failed_install_rolls_every_part_back_out(self):
        # The enabled set must always be one that compiles, or the game is
        # left broken by a mod the user cannot see to remove. A mod can be
        # several parts now (The Mandalorian ships Base, Text and Weapon), so
        # a half-installed set is its own kind of broken.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def install_frosty_mod"):]
        fn = fn[:fn.index("async def set_frosty_mod_enabled")]
        self.assertIn("for name in written:", fn)
        self.assertIn("os.remove(os.path.join(mods_dir, name))", fn)
        self.assertIn("await _frosty_compile", fn)

    def test_a_failed_toggle_puts_every_part_back(self):
        # Toggling a multi-part mod moves several files. Failing halfway and
        # leaving some moved would compile a set the user never chose.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def set_frosty_mod_enabled"):]
        fn = fn[:fn.index("async def uninstall_frosty_mod")]
        self.assertIn("for back_src, back_dst in moved:", fn)
        self.assertIn("os.replace(back_dst, back_src)", fn)
        # Twice: once if a move itself fails, once if the compile does.
        self.assertEqual(fn.count("os.replace(back_dst, back_src)"), 2)

    def test_the_redirect_goes_in_the_prefix_registry(self):
        # Not launch options: EA's launcher respawns the game and strips them.
        # Proven on device with a deliberately corrupted pack, which the game
        # ignored entirely until GAME_DATA_DIR was set here.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("def _frosty_prefix_setup"):]
        fn = fn[:fn.index("async def _frosty_compile")]
        self.assertIn("GAME_DATA_DIR", fn)
        # The path to user.reg lives in its own helper now, because the
        # self-repair check reads the same file.
        self.assertIn("_frosty_redirect_reg(", fn)
        reg = source[source.index("def _frosty_redirect_reg"):]
        self.assertIn("user.reg", reg[:reg.index("def _frosty_override_section")])
        self.assertIn("cryptbase", fn)
        # bcrypt and CryptBase are alternative hooks; both present breaks Wine.
        self.assertIn("bcrypt.dll", fn)

    def test_reset_undoes_everything_it_did(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def reset_frosty"):]
        fn = fn[:fn.index("async def get_game_status")]
        # The redirect is cleared through _frosty_redirect_clear now, which
        # is the only place that knows the registry path.
        for undo in ("_frosty_pack_dir", "_frosty_mods_dir", "CryptBase.dll",
                     "_frosty_redirect_clear"):
            self.assertIn(undo, fn, f"reset leaves {undo} behind")

    def test_multi_mod_archives_ask_rather_than_guess(self):
        # Frostbite archives routinely hold several .fbmod files - alternative
        # looks the author expects a choice between. Same contract as
        # Helldivers 2 variants.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def install_frosty_mod"):]
        fn = fn[:fn.index("async def set_frosty_mod_enabled")]
        self.assertIn('"needs_choice": True', fn)
        self.assertIn("payload_choice", fn)

    def test_conversion_is_not_optional(self):
        # An unconverted mod is silently ignored by the compiler: it produces a
        # pack with no changes, which looks exactly like a broken plugin.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def install_frosty_mod"):]
        fn = fn[:fn.index("async def set_frosty_mod_enabled")]
        self.assertIn('"update-mod"', fn)


    def test_a_compile_the_game_cannot_see_is_not_a_success(self):
        # Wine holds the registry in memory and flushes it on shutdown, so a
        # write made while the prefix is alive is reverted. Reporting success
        # then would send the user to a boot with no mods in it.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def _frosty_compile"):]
        fn = fn[:fn.index("def _prefix_drive_c")]
        self.assertIn("if not _frosty_redirect_ok(app_id):", fn)
        after = fn[fn.index("if not _frosty_redirect_ok(app_id):"):]
        self.assertIn('"ok": False', after[:600])

    def test_a_stored_warning_comes_back_out_of_the_listing(self):
        # Behavioural, through the real endpoint. The previous test for this
        # checked that a setter existed in one branch of the install handler,
        # which passed while the warning was invisible everywhere it
        # mattered. Michael, fairly: "surely there is a simple test you could
        # have written to check this."
        home = tempfile.mkdtemp(prefix="frosty-warn-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        old_runtime = main.decky.DECKY_PLUGIN_RUNTIME_DIR
        main.decky.DECKY_PLUGIN_RUNTIME_DIR = home
        self.addCleanup(
            setattr, main.decky, "DECKY_PLUGIN_RUNTIME_DIR", old_runtime
        )
        mods_dir = main._frosty_mods_dir("starwarsbattlefront22017")
        os.makedirs(mods_dir, exist_ok=True)
        with open(os.path.join(mods_dir, "Aged Mod.fbmod"), "wb") as fh:
            fh.write(b"x")

        settings = main._load_settings()
        settings.setdefault("installed", {})["starwarsbattlefront22017"] = {
            "Aged Mod": {
                "mod_id": 2042, "name": "Aged Mod", "mode": "frosty",
                "warning": "built for a different build",
            },
        }
        main._save_settings(settings)
        self.addCleanup(main._save_settings, {})

        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(main.Plugin().get_installed_mods(
                "starwarsbattlefront22017", "STAR WARS Battlefront II", "Data",
                "frosty", 1237950,
            ))
        finally:
            loop.close()
        mods = r.get("mods") or []
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0].get("warning"), "built for a different build")

    def test_a_mod_with_no_warning_reports_an_empty_one(self):
        # An absent key and a present-but-empty one render differently in the
        # UI, and most mods are fine.
        home = tempfile.mkdtemp(prefix="frosty-nowarn-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        old_runtime = main.decky.DECKY_PLUGIN_RUNTIME_DIR
        main.decky.DECKY_PLUGIN_RUNTIME_DIR = home
        self.addCleanup(
            setattr, main.decky, "DECKY_PLUGIN_RUNTIME_DIR", old_runtime
        )
        mods_dir = main._frosty_mods_dir("starwarsbattlefront22017")
        os.makedirs(mods_dir, exist_ok=True)
        with open(os.path.join(mods_dir, "Fine Mod.fbmod"), "wb") as fh:
            fh.write(b"x")
        settings = main._load_settings()
        settings.setdefault("installed", {})["starwarsbattlefront22017"] = {
            "Fine Mod": {"mod_id": 1, "name": "Fine Mod", "mode": "frosty"},
        }
        main._save_settings(settings)
        self.addCleanup(main._save_settings, {})
        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(main.Plugin().get_installed_mods(
                "starwarsbattlefront22017", "STAR WARS Battlefront II", "Data",
                "frosty", 1237950,
            ))
        finally:
            loop.close()
        self.assertEqual((r.get("mods") or [{}])[0].get("warning"), "")

    def test_the_compilers_version_warning_is_not_thrown_away(self):
        # FrostyCli said, in as many words, "Mod Battle Damaged Vader
        # (Cracked) was made for a different version of the game, it might or
        # might not work". Only ERROR lines were kept, so that went in the bin
        # and the mod installed reporting success and rendered as shards.
        # Shadow Lord Maul replaces meshes too and produces no such warning,
        # which is the whole difference between the two.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("FROSTY_VERSION_WARN", source)
        fn = source[source.index("async def _frosty_run"):]
        fn = fn[:fn.index("def _strip_ansi")]
        self.assertIn("FROSTY_VERSION_WARN in line", fn)

    def test_the_warning_reaches_the_caller_and_the_record(self):
        # A warning read once at install time is forgotten by the time the
        # character looks wrong, so it is stored with the mod.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        comp = source[source.index("async def _frosty_compile"):]
        comp = comp[:comp.index("def _prefix_drive_c")]
        self.assertIn('result["warning"]', comp)

        inst = source[source.index("async def install_frosty_mod"):]
        inst = inst[:inst.index("async def set_frosty_mod_enabled")]
        self.assertIn('"warning": result.get("warning")', inst)

        listing = source[source.index('if install_mode == "frosty":'):]
        listing = listing[:listing.index('if install_mode == "me3":')]
        self.assertIn('"warning": rec.get("warning")', listing)

    def test_a_clean_compile_carries_no_warning(self):
        # The inverse matters: warning text on every install would train the
        # user to ignore it, and most Battlefront II mods are fine.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        comp = source[source.index("async def _frosty_compile"):]
        comp = comp[:comp.index("def _prefix_drive_c")]
        self.assertIn("if warns:", comp)

    def test_variant_labels_keep_only_what_differs(self):
        # Battle Damaged Darth Vader (mod 2042) ships three .fbmod files whose
        # names differ in one word, at the END. Three buttons of near-identical
        # text is unreadable on a TV; the author's actual options are Cracked,
        # Full Helm and NOHelmet.
        got = main._payload_choice_labels([
            "Battle Damaged Vader v1.8/Battle Damaged Vader 1.8 (Cracked).fbmod",
            "Battle Damaged Vader v1.8/Battle Damaged Vader 1.8 (Full Helm).fbmod",
            "Battle Damaged Vader v1.8/Battle Damaged Vader 1.8 (NOHelmet).fbmod",
        ])
        self.assertEqual(got, ["Cracked", "Full Helm", "NOHelmet"])

    def test_a_single_option_keeps_its_whole_name(self):
        # With nothing to compare against there is no shared text to strip,
        # and a bare extension-less name is all the user has to go on.
        self.assertEqual(
            main._payload_choice_labels(["Shadow Lord Maul (Maul).fbmod"]),
            ["Shadow Lord Maul (Maul)"],
        )

    def test_labels_never_collapse_into_duplicates(self):
        # Same file name in different folders: trimming to the file name would
        # produce two identical buttons, which is worse than two long ones.
        got = main._payload_choice_labels(["A/Same Name.fbmod",
                                           "B/Same Name.fbmod"])
        self.assertEqual(len(set(got)), 2, got)

    def test_labels_line_up_with_their_options(self):
        # The VALUE sent back is still the path - only the label changes - so
        # a mismatch in length or order would install the wrong variant.
        opts = ["x/Red.fbmod", "x/Green.fbmod", "x/Blue.fbmod"]
        got = main._payload_choice_labels(opts)
        self.assertEqual(len(got), len(opts))
        self.assertEqual(got, ["Red", "Green", "Blue"])

    def test_labels_are_never_empty(self):
        # An empty button cannot be chosen, and trimming shared text is
        # exactly how one would become empty.
        for opts in (["Mod.fbmod", "Mod .fbmod"],
                     ["pack/A.fbmod", "pack/AB.fbmod"],
                     ["(1).fbmod", "(2).fbmod"]):
            for label in main._payload_choice_labels(opts):
                self.assertTrue(label.strip(), f"empty label from {opts}")

    def test_a_multi_part_archive_can_install_all_of_it(self):
        # This started out refusing to merge, on the assumption that several
        # .fbmod files are always alternatives. Checking real mods killed
        # that: The Mandalorian ships Base, Text and Weapon parts plus a
        # .fbcollection listing them, and Ahsoka Tano ships a main mod, icon
        # variants and a Green Saber add-on. One part alone is not the mod.
        # The compiler takes a DIRECTORY of .fbmod files, so this was only
        # ever the installer's assumption.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def install_frosty_mod"):]
        fn = fn[:fn.index("async def set_frosty_mod_enabled")]
        self.assertIn('"merge_allowed": True', fn)
        self.assertIn("_payload_choice_labels(options)", fn)
        self.assertIn('payload_choice == "*"', fn)

    def test_an_author_declared_set_is_not_put_to_the_user(self):
        # A .fbcollection is the author saying, in Frosty's own format, that
        # these parts go together. There is nothing to ask.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def install_frosty_mod"):]
        fn = fn[:fn.index("async def set_frosty_mod_enabled")]
        self.assertIn(".fbcollection", fn)
        self.assertIn("declared_set", fn)

    def test_a_frosty_manager_plugin_is_named_for_what_it_is(self):
        # BetterSabers is the most endorsed mod for this game and its archive
        # holds one file: BetterSabersPlugin.dll. It extends the desktop Frosty
        # Mod Manager's interface. "No mod files in this archive" is true and
        # sounds like a fault in us.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def install_frosty_mod"):]
        fn = fn[:fn.index("async def set_frosty_mod_enabled")]
        self.assertIn('"unsupported_tool": True', fn)
        self.assertIn("desktop Frosty Mod Manager", fn)

    def test_every_part_of_a_mod_is_found_from_its_record(self):
        # Disk names are derived from the mod name for a single part and from
        # the part name for several, so the record's list is the only reliable
        # answer to "what belongs to this mod".
        rec = {"files": ["A - Base.fbmod", "A - Weapon.fbmod", "notes.txt"]}
        self.assertEqual(
            main._frosty_record_files(rec, "A"),
            ["A - Base.fbmod", "A - Weapon.fbmod"],
        )

    def test_an_old_record_with_no_file_list_still_works(self):
        # Records written before multi-part installs existed have no list.
        # Falling back to the derived name is what keeps them togglable.
        self.assertEqual(
            main._frosty_record_files({}, "Shadow Lord Maul"),
            ["Shadow Lord Maul.fbmod"],
        )

    def test_disabling_the_last_mod_restores_vanilla(self):
        # Deleting the pack is not enough: GAME_DATA_DIR kept pointing at a
        # symlink whose target had gone, and the game refused to boot at all.
        # The comment that used to sit in _frosty_compile said the game would
        # treat a missing pack as vanilla. It does not.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def _frosty_compile"):]
        fn = fn[:fn.index("def _prefix_drive_c")]
        block = fn[fn.index("if not enabled:"):]
        block = block[:block.index("return {")]
        self.assertIn("os.remove(link)", block, "the dangling symlink is left")
        self.assertIn("_frosty_redirect_clear(", block,
                      "the game is still pointed at a pack that is gone")

    def test_the_redirect_is_cleared_through_one_helper(self):
        # reset_frosty had its own copy of the registry path, with the same
        # dot-dot too many, so it had never cleared the redirect either. Two
        # copies of a path is two chances to get it wrong.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def reset_frosty"):]
        fn = fn[:fn.index("async def get_game_status")]
        self.assertIn("_frosty_redirect_clear(", fn)
        self.assertNotIn('"..", "..", "user.reg"', fn)
        # And nowhere else in the file either.
        self.assertNotIn('_prefix_drive_c(app_id, "..", "..", "user.reg")',
                         source)

    def test_clearing_the_redirect_removes_only_that_line(self):
        # It edits a live Wine registry. Taking anything else out with it
        # would break the prefix for every game in it.
        reg = os.path.join(tempfile.mkdtemp(), "user.reg")
        self.addCleanup(shutil.rmtree, os.path.dirname(reg), ignore_errors=True)
        body = (
            "WINE REGISTRY Version 2" + chr(10) + chr(10)
            + "[Environment] 1785139150" + chr(10)
            + "#time=1dd1d9dce72e6fc" + chr(10)
            + '"GAME_DATA_DIR"="Z:' + chr(92) + chr(92) + 'somewhere"' + chr(10)
            + '"TEMP"="C:' + chr(92) + chr(92) + 'temp"' + chr(10) + chr(10)
            + "[Other] 1" + chr(10)
        )
        with open(reg, "w", encoding="utf-8", newline="") as fh:
            fh.write(body)

        real = main._frosty_redirect_reg
        main._frosty_redirect_reg = lambda app_id: reg
        self.addCleanup(setattr, main, "_frosty_redirect_reg", real)
        self.assertTrue(main._frosty_redirect_clear(1237950))

        with open(reg, encoding="utf-8") as fh:
            after = fh.read()
        self.assertNotIn("GAME_DATA_DIR", after)
        self.assertIn('"TEMP"', after)
        self.assertIn("[Environment]", after)
        self.assertIn("[Other]", after)
        # Idempotent: a second pass has nothing to do.
        self.assertFalse(main._frosty_redirect_clear(1237950))

    def test_the_verification_says_what_it_is_doing(self):
        # It runs with a cold cache deliberately - a warm one would skip the
        # data it exists to read - so it costs about 45 seconds every time.
        # One unchanging sentence for that long reads as a freeze.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def _frosty_compile"):]
        fn = fn[:fn.index("def _prefix_drive_c")]
        block = fn[fn.index("async def _emit_verify"):]
        self.assertIn("message.lower()", block[:600])

    def test_installed_frostbite_mods_are_listed(self):
        # This is END TO END through the real endpoint on purpose. The listing
        # branch was originally patched in by matching 'install_mode == "me3"',
        # which appears eight times in main.py, so it landed in
        # _install_mod_inner - a function install_mode "frosty" never reaches.
        # The mod installed and applied in game and My Mods stayed empty. A
        # source-text test would have passed happily.
        home = tempfile.mkdtemp(prefix="frosty-list-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        old_runtime = main.decky.DECKY_PLUGIN_RUNTIME_DIR
        main.decky.DECKY_PLUGIN_RUNTIME_DIR = home
        self.addCleanup(
            setattr, main.decky, "DECKY_PLUGIN_RUNTIME_DIR", old_runtime
        )

        mods_dir = main._frosty_mods_dir("starwarsbattlefront22017")
        os.makedirs(os.path.join(mods_dir, "disabled"), exist_ok=True)
        with open(os.path.join(mods_dir, "Shadow Lord Maul.fbmod"), "wb") as fh:
            fh.write(b"x")
        with open(os.path.join(mods_dir, "disabled", "Old Skin.fbmod"), "wb") as fh:
            fh.write(b"x")

        settings = main._load_settings()
        settings.setdefault("installed", {})["starwarsbattlefront22017"] = {
            "Shadow Lord Maul": {
                "mod_id": 13974, "name": "Shadow Lord Maul", "version": "1.0",
                "mode": "frosty",
            },
            "Old Skin": {
                "mod_id": 1, "name": "Old Skin", "version": "2.0",
                "mode": "frosty",
            },
            "Not Frostbite": {
                "mod_id": 2, "name": "Not Frostbite", "mode": "folder",
            },
        }
        main._save_settings(settings)
        self.addCleanup(main._save_settings, {})

        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(main.Plugin().get_installed_mods(
                "starwarsbattlefront22017", "STAR WARS Battlefront II", "Data",
                "frosty", 1237950,
            ))
        finally:
            loop.close()

        self.assertTrue(r.get("ok"))
        by_name = dict((m["name"], m) for m in r.get("mods", []))
        self.assertIn("Shadow Lord Maul", by_name)
        self.assertTrue(by_name["Shadow Lord Maul"]["enabled"])
        # A parked mod is still installed, just switched off.
        self.assertIn("Old Skin", by_name)
        self.assertFalse(by_name["Old Skin"]["enabled"])
        # A record from another game's install mode is not ours to show.
        self.assertNotIn("Not Frostbite", by_name)

    def test_a_record_with_no_file_is_not_listed(self):
        # Disk decides. A record left behind by a failed install must not
        # appear as something the user can toggle.
        home = tempfile.mkdtemp(prefix="frosty-ghost-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        old_runtime = main.decky.DECKY_PLUGIN_RUNTIME_DIR
        main.decky.DECKY_PLUGIN_RUNTIME_DIR = home
        self.addCleanup(
            setattr, main.decky, "DECKY_PLUGIN_RUNTIME_DIR", old_runtime
        )
        os.makedirs(main._frosty_mods_dir("starwarsbattlefront22017"),
                    exist_ok=True)
        settings = main._load_settings()
        settings.setdefault("installed", {})["starwarsbattlefront22017"] = {
            "Ghost": {"mod_id": 9, "name": "Ghost", "mode": "frosty"},
        }
        main._save_settings(settings)
        self.addCleanup(main._save_settings, {})

        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(main.Plugin().get_installed_mods(
                "starwarsbattlefront22017", "STAR WARS Battlefront II", "Data",
                "frosty", 1237950,
            ))
        finally:
            loop.close()
        self.assertEqual(r.get("mods"), [])

    def test_the_registry_path_lands_inside_the_prefix(self):
        # THE bug. drive_c/../.. is compatdata/<id>, not pfx, so the redirect
        # was written to a path that does not exist - which the setup function
        # reads as "no prefix here" and returns from silently. It had never
        # once written the registry, so the game always loaded vanilla data
        # while the install reported success.
        reg = main._frosty_redirect_reg(1237950)
        parts = os.path.normpath(reg).split(os.sep)
        self.assertEqual(parts[-1], "user.reg")
        self.assertEqual(parts[-2], "pfx", f"resolved outside the prefix: {reg}")
        self.assertEqual(parts[-3], "1237950")
        self.assertNotIn("drive_c", parts)

    def test_progress_never_leaves_its_window(self):
        # Each stage owns a slice of the bar. Overshooting one would make the
        # bar jump backwards when the next stage starts.
        sent = []

        async def emit(pct, message):
            sent.append(pct)

        bar = main._FrostyProgress(55, 30, emit)
        for line in (
            "INFO - Loading profile STAR WARS",
            "INFO - Loading ebx from cache",
            "INFO - Finished initializing",
        ):
            bar.line(line)
        for _ in range(600):
            bar.tick(1.0)
        self.assertGreaterEqual(bar.percent(), 55)
        self.assertLessEqual(bar.percent(), 85)

    def test_the_bar_moves_while_the_compiler_says_nothing(self):
        # Measured on device: a cold cache spends 35 seconds inside "Indexing
        # Ebx" printing NOTHING. A parked bar there is what Michael sat
        # through and read as a failure.
        async def emit(pct, message):
            pass

        bar = main._FrostyProgress(0, 100, emit)
        bar.line("INFO - Indexing Ebx")
        start = bar.percent()
        for _ in range(15):
            bar.tick(1.0)
        self.assertGreater(bar.percent(), start, "the bar sat still")

    def test_the_creep_stops_short_of_real_progress(self):
        # Guessing is allowed; claiming a milestone that has not happened is
        # not. If indexing takes three times as long as usual the bar must
        # stall just below the next real step rather than reach it.
        async def emit(pct, message):
            pass

        bar = main._FrostyProgress(0, 100, emit)
        bar.line("INFO - Indexing Ebx")
        nxt = dict((n, p) for n, p, _m, _s in main._FrostyProgress.STEPS)
        for _ in range(400):
            bar.tick(1.0)
        self.assertLess(bar.percent(), nxt["Indexed ebx"])

    def test_progress_is_only_sent_when_it_changes(self):
        # The frontend redraws on every event; a tick a second that repeats
        # the same number is pure noise.
        sent = []

        async def emit(pct, message):
            sent.append(pct)

        bar = main._FrostyProgress(0, 100, emit)
        bar.line("INFO - Loading profile STAR WARS")
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(bar.send())
            loop.run_until_complete(bar.send())
        finally:
            loop.close()
        self.assertEqual(len(sent), 1)

    def test_a_warm_cache_run_still_reports_progress(self):
        # Warm, the whole job is six seconds and takes an entirely different
        # set of log lines. Both paths have to drive the bar.
        async def emit(pct, message):
            pass

        bar = main._FrostyProgress(0, 100, emit)
        for line in ("INFO - Loading ebx from cache",
                     "INFO - Loading res from cache",
                     "INFO - Loading chunks from cache"):
            bar.line(line)
        self.assertGreaterEqual(bar.percent(), 40)

    def test_bundle_lines_advance_the_compile(self):
        async def emit(pct, message):
            pass

        bar = main._FrostyProgress(0, 100, emit)
        bar.line("INFO - Finished initializing")
        before = bar.percent()
        for _ in range(30):
            bar.line("INFO - RANGEBUILD bundle=8B3AE028 orig=184 built=270")
        self.assertGreater(bar.percent(), before)
        self.assertLessEqual(bar.percent(), 100)

    def test_a_failed_compiler_run_is_always_logged(self):
        # This whole class of bug is invisible without it: the device log
        # recorded a rolled-back install with no reason attached.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def _frosty_run"):]
        fn = fn[:fn.index("def _strip_ansi")]
        self.assertIn("decky.logger.error if rc != 0", fn)
        self.assertIn("readline()", fn)

    def test_the_redirect_is_checked_not_assumed(self):
        # Without GAME_DATA_DIR the game boots perfectly and ignores every
        # mod. That is the least diagnosable failure the plugin has, and it
        # happened twice on device, so the state has to be readable.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("def _frosty_redirect_ok"):]
        fn = fn[:fn.index("def _frosty_prefix_setup")]
        self.assertIn("GAME_DATA_DIR", fn)
        self.assertIn("_frosty_override_section()", fn)

    def test_a_missing_redirect_repairs_itself(self):
        # Wine flushes its in-memory registry over user.reg on shutdown, so a
        # redirect written during an install can be reverted by something
        # unrelated later. Opening the game's page must put it back.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def get_frosty_state"):]
        fn = fn[:fn.index("async def install_frosty_toolkit")]
        self.assertIn("_frosty_redirect_ok(", fn)
        self.assertIn("_frosty_prefix_setup", fn)
        self.assertIn('"redirect_ok"', fn)

    def test_the_registry_key_keeps_its_backslashes(self):
        # user.reg needs two characters where a path needs one. A hand-edited
        # prefix on the test device had them all eaten, leaving a key called
        # "SoftwareWineAppDefaultsstarwarsbattlefrontii.exeDllOverrides" that
        # Wine ignored, so the override silently was not set.
        section = main._frosty_override_section()
        self.assertIn(chr(92) * 2 + "Wine" + chr(92) * 2, section)
        self.assertTrue(section.startswith("[Software" + chr(92) * 2))
        self.assertTrue(section.endswith("DllOverrides]"))

    def test_a_mangled_key_is_removed_rather_than_joined(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("def _frosty_prefix_setup"):]
        fn = fn[:fn.index("async def _frosty_compile")]
        self.assertIn("SoftwareWineAppDefaults", fn)
        self.assertIn("re.sub(", fn)

    def test_a_dead_datapath_is_cleared(self):
        # Every Frosty guide online tells the user to add one, and it is the
        # first thing they paste in when a mod does not show up. Pointing the
        # game at a directory that does not exist is not worth preserving.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("def _frosty_prefix_setup"):]
        fn = fn[:fn.index("async def _frosty_compile")]
        self.assertIn("dataPath", fn)
        self.assertIn("os.path.isdir(unix)", fn)

    def test_a_live_datapath_is_left_alone(self):
        # The inverse matters just as much: the plugin must not delete a
        # setting that is doing its job.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("def _frosty_prefix_setup"):]
        fn = fn[:fn.index("async def _frosty_compile")]
        self.assertIn("keep.append(line)", fn)


class TestReshadeInstall(unittest.TestCase):
    """Michael: "Build the reshade but lets just put a warning on related
    mods that it might trigger the anti cheat because its injected." A
    ReShade-shaped archive (no patch files, reshade markers) installs
    beside the exe with per-file records; the result carries the warning
    and the frontend applies the dxgi override launch options."""

    def test_the_install_path_exists_and_warns(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        i = source.index("if is_reshade and reshade_subdir:")
        block = source[i:i + 4500]
        self.assertIn('"reshade": True', block)
        self.assertIn('"target": reshade_subdir', block)
        self.assertIn("use at", block)
        self.assertIn("own risk", block)

    def test_without_a_subdir_the_refusal_stands(self):
        # Games with no reshade config keep the honest refusal.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        i = source.index("if is_reshade and reshade_subdir:")
        tail = source[i:i + 5000]
        self.assertIn("does not install for this game", tail)

    def test_reshade_named_mods_warn_before_download(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def _hd2_stale_warning"):]
        fn = fn[:fn.index("async def _stale_native_warning")]
        self.assertIn('"reshade" in (mod_name or "").lower()', fn)
        self.assertIn("injection is a different", fn)


class TestHd2StaleWarning(unittest.TestCase):
    """The 2026-08-19 repack settled how HD2 mods age: every game update
    invalidates patch files built before it, and authors re-release within
    days - a mod updated twenty hours after the repack shipped plain patch
    files. So the warning line is the game's own lastupdated, not a
    birthday; this replaced a 365-day rule within a day of writing it."""

    def test_hd2_routes_to_the_update_rule(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def _stale_native_warning"):]
        fn = fn[:fn.index("async def get_mod_files")]
        self.assertIn('if game_domain == "helldivers2":', fn)
        self.assertIn("_hd2_stale_warning(", fn)

    def test_the_line_is_the_games_own_update(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        i = source.index("async def _hd2_stale_warning")
        block = source[i:source.index("async def _stale_native_warning", i)]
        self.assertIn("_game_updated_at(app_id)", block)
        self.assertIn("uploaded >= game_updated", block)
        # And it stays a warning, because sound mods sometimes survive.
        self.assertIn("sometimes survive", block)
        self.assertNotIn('"blocked": True', block)

    def test_browse_nodes_carry_the_same_fact(self):
        # The tile badge and the pre-download warning must agree, so both
        # read the game's lastupdated. Stamped at every tile source: the
        # browse filter, the hero/recommended batch, and trending.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(source.count("_stamp_pre_update(game_domain,"), 3)

    def test_the_stamp_is_a_fact_not_a_verdict(self):
        # The real timeline: the repack landed 2026-08-19 16:26; BFA's
        # file is from the 13th (stamped), the Suomi skin from the 20th
        # (not stamped).
        game_updated = 1787156760
        nodes = [
            {"modId": 1, "updatedAt": "2026-08-13T00:00:00+00:00"},
            {"modId": 2, "updatedAt": "2026-08-20T12:57:00+00:00"},
            {"modId": 3},  # unknown date: no claim
        ]
        import unittest.mock as mock
        with mock.patch.object(main, "_game_updated_at",
                               return_value=game_updated):
            main._stamp_pre_update("helldivers2", nodes)
        self.assertTrue(nodes[0].get("preGameUpdate"))
        self.assertNotIn("preGameUpdate", nodes[1])
        self.assertNotIn("preGameUpdate", nodes[2])

    def test_other_games_are_never_stamped(self):
        nodes = [{"modId": 1, "updatedAt": "2020-01-01T00:00:00+00:00"}]
        main._stamp_pre_update("skyrimspecialedition", nodes)
        self.assertNotIn("preGameUpdate", nodes[0])


class TestHelldivers2Patches(unittest.TestCase):
    """HD2 mods are <hash>.patch_N file swaps in data/. The game loads
    patch_0, patch_1, ... per archive hash, so two mods patching the same
    archive coexist by renumbering - the community norm. Without it, the
    flat branch would silently overwrite the first mod's patch_0."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_grouping_keeps_triplets_together(self):
        groups = main._hd2_patch_groups([
            "/x/9bc33b7058a2bd5a.patch_0",
            "/x/9bc33b7058a2bd5a.patch_0.gpu_resources",
            "/x/9bc33b7058a2bd5a.patch_0.stream",
            "/x/readme.txt",
        ])
        key = ("/x", "9bc33b7058a2bd5a", 0)
        self.assertEqual(list(groups), [key])
        self.assertEqual(len(groups[key]), 3)

    def test_a_weapons_pack_merge_keeps_every_gun(self):
        # Five guns, five folders, every one <hash>.patch_0. Keyed without
        # the folder these fold into ONE group and four guns vanish in the
        # move loop. Michael: "I should be able to have all of them as
        # they are different guns."
        paths = []
        for gun in ("Sickle", "Scythe", "Dagger", "Punisher", "Blitzer"):
            paths.append("/x/%s/9ba626afa44a3aa3.patch_0" % gun)
        groups = main._hd2_patch_groups(paths)
        self.assertEqual(len(groups), 5)

    def test_screenshots_and_readmes_stay_behind(self):
        self.assertEqual(main._hd2_patch_groups(
            ["/x/readme.txt", "/x/preview.png", "/x/02582f3da1f8daf5"]), {})

    def test_the_second_mod_takes_the_next_number(self):
        open(os.path.join(self.dir, "9bc33b7058a2bd5a.patch_0"), "w").close()
        self.assertEqual(main._hd2_next_free_number(
            self.dir, "9bc33b7058a2bd5a", set()), 1)

    def test_numbers_fill_gaps_not_just_append(self):
        open(os.path.join(self.dir, "9bc33b7058a2bd5a.patch_1"), "w").close()
        self.assertEqual(main._hd2_next_free_number(
            self.dir, "9bc33b7058a2bd5a", set()), 0)

    def test_a_reinstall_reuses_its_own_numbers(self):
        # Otherwise every reinstall stacks patch_1, patch_2, ... forever.
        open(os.path.join(self.dir, "9bc33b7058a2bd5a.patch_0"), "w").close()
        self.assertEqual(main._hd2_next_free_number(
            self.dir, "9bc33b7058a2bd5a",
            {"9bc33b7058a2bd5a.patch_0"}), 0)

    def test_other_archives_numbers_do_not_interfere(self):
        open(os.path.join(self.dir, "02582f3da1f8daf5.patch_0"), "w").close()
        self.assertEqual(main._hd2_next_free_number(
            self.dir, "9bc33b7058a2bd5a", set()), 0)

    def test_variant_folders_are_a_choice_not_a_bulk_install(self):
        # "Super Destroyer RGB" ships 275 files as ~90 colour variants,
        # every folder holding the same 9ba626afa44a3aa3.patch_0. Installing
        # them all would renumber ninety alternatives into ninety slots.
        paths = []
        for colour in ("Bright Green", "Dark Blue", "Red"):
            for suffix in ("", ".gpu_resources", ".stream"):
                paths.append(
                    "/x/Front Ship Tiny Lights/%s/"
                    "9ba626afa44a3aa3.patch_0%s" % (colour, suffix))
        groups = main._hd2_variant_groups(paths)
        self.assertEqual(len(groups), 3)
        for members in groups.values():
            self.assertEqual(len(members), 3)  # the triplet stays together

    def test_folders_with_different_numbers_install_together(self):
        # Automaton Helmets ships patch_461 in Commissar/ and patch_463 in
        # Incen/ - a set the author split up, not a choice.
        paths = [
            "/x/Commissar/9ba626afa44a3aa3.patch_461",
            "/x/Commissar/9ba626afa44a3aa3.patch_461.stream",
            "/x/Incen/9ba626afa44a3aa3.patch_463",
            "/x/Incen/9ba626afa44a3aa3.patch_463.stream",
        ]
        self.assertEqual(main._hd2_variant_groups(paths), {})

    def test_a_single_folder_is_never_a_choice(self):
        self.assertEqual(main._hd2_variant_groups(
            ["/x/9bc33b7058a2bd5a.patch_0"]), {})

    def test_merge_all_installs_every_folder(self):
        # The first version refused "*" as "impossible by construction",
        # which was wrong: renumbering gives each folder its own patch
        # slot, exactly as it does for two separate mods. A weapons pack
        # is a SET, not alternatives.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        i = source.index('if payload_choice == "*":', source.index(
            "variants = _hd2_variant_groups(flat)"))
        block = source[i:i + 500]
        self.assertIn("pass", block)
        self.assertNotIn('"error"', block.split("elif")[0])

    def test_the_installer_asks_rather_than_guessing(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        i = source.index("variants = _hd2_variant_groups(flat)")
        block = source[i:source.index("_record_vanilla_baseline", i)]
        self.assertIn('"needs_choice": True', block)
        # And an explicit pick is honoured rather than re-asked.
        self.assertIn("if payload_choice:", block)

    def test_the_flat_branch_renumbers_for_hd2(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        # To the record write, not a byte count: a fixed window silently
        # stops covering the code the moment the block above it grows.
        # To the flat-mode record's target, which only the patch path
        # writes - the ReShade block above it now also mentions record
        # keys, so the old anchor cut the slice short.
        i = source.index("if flat_extensions or hd2_layout:")
        block = source[i:source.index('"target": mods_subdir', i)]
        self.assertIn("_hd2_next_free_number(", block)
        self.assertIn("_hd2_patch_groups(", block)


class TestCollectionVersionPinning(unittest.TestCase):
    """Every collection revision declares its target game version, and the
    top three Bannerlord collections all target older builds. The prose
    downgrade check could never see "Best&Correct Mods 1.2.11": its target
    lives in a field, not in a sentence about downgrading. Michael installed
    it on v1.4.8 and got the game's own submodule load error for a mod whose
    name says "for v1.2.10"."""

    def test_the_crashing_case_is_flagged(self):
        self.assertTrue(main._versions_mismatch(["v1.2.11"], "v1.4.8"))

    def test_author_lagging_one_patch_is_not_flagged(self):
        # v1.4.7 on v1.4.8 is the ordinary case; flagging it would cry wolf
        # on collections that work, which is the age-rule mistake again.
        self.assertFalse(main._versions_mismatch(["v1.4.7"], "v1.4.8"))

    def test_any_matching_ref_clears_the_flag(self):
        self.assertFalse(
            main._versions_mismatch(["v1.2.11", "v1.4.2"], "v1.4.8"))

    def test_no_claim_without_evidence(self):
        self.assertFalse(main._versions_mismatch([], "v1.4.8"))
        self.assertFalse(main._versions_mismatch(["v1.2.11"], ""))
        self.assertFalse(main._versions_mismatch(["beta"], "v1.4.8"))

    def test_bannerlord_version_reads_from_the_native_manifest(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("def _bl_installed_game_version"):]
        fn = fn[:fn.index("def _versions_mismatch")]
        self.assertIn('"Native", "SubModule.xml"', fn)

    def test_the_collections_query_asks_for_game_versions(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("gameVersions { reference }", source)
        fn = source[source.index("async def get_collections"):]
        fn = fn[:fn.index("async def get_mods")]
        self.assertIn("_versions_mismatch(", fn)


class TestBannerlordLoadOrder(unittest.TestCase):
    """Bannerlord modules declare their own load order in SubModule.xml, and
    our launcher writer appended in install order. The most popular
    collection then broke at launch: "Bannerlord.ButterLib is loaded before
    the BetterExceptionWindow!" - ButterLib itself declares
    BetterExceptionWindow order="LoadBeforeThis". The data to prevent it was
    on disk all along."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _module(self, folder, mod_id=None, before=(), after=()):
        d = os.path.join(self.dir, "Modules", folder)
        os.makedirs(d, exist_ok=True)
        mid = mod_id or folder
        parts = ['<Module><Id value="%s" />' % mid]
        if before:
            parts.append("<DependedModuleMetadatas>")
            for b in before:
                parts.append(
                    '<DependedModuleMetadata id="%s" order="LoadBeforeThis" '
                    'optional="true" />' % b)
            parts.append("</DependedModuleMetadatas>")
        if after:
            parts.append("<ModulesToLoadAfterThis>")
            for a in after:
                parts.append('<Module Id="%s" />' % a)
            parts.append("</ModulesToLoadAfterThis>")
        parts.append("</Module>")
        with open(os.path.join(d, "SubModule.xml"), "w", encoding="utf-8") as f:
            f.write("".join(parts))

    def _launcher(self, order):
        path = os.path.join(self.dir, "LauncherData.xml")
        rows = "".join(
            "<UserModData><Id>%s</Id><IsSelected>true</IsSelected>"
            "</UserModData>" % m for m in order)
        with open(path, "w", encoding="utf-8") as f:
            f.write("<UserData><SingleplayerData><ModDatas>%s</ModDatas>"
                    "</SingleplayerData></UserData>" % rows)
        return path

    def _order(self, path):
        import re as _re
        with open(path, encoding="utf-8") as f:
            return _re.findall(r"<Id>([^<]+)</Id>", f.read())

    def test_michaels_exact_case(self):
        # ButterLib declares BEW LoadBeforeThis; launcher had ButterLib at
        # the top and BEW at slot 60.
        self._module("Bannerlord.ButterLib",
                     before=("BetterExceptionWindow",))
        self._module("BetterExceptionWindow")
        self._module("Native")
        path = self._launcher(
            ["Bannerlord.ButterLib", "Native", "BetterExceptionWindow"])
        moved = main._bl_apply_launcher_order(path, self.dir)
        self.assertGreater(moved, 0)
        order = self._order(path)
        self.assertLess(order.index("BetterExceptionWindow"),
                        order.index("Bannerlord.ButterLib"))

    def test_loads_after_this_pushes_the_module_up(self):
        # Harmony's declaration: the official modules load AFTER it.
        self._module("Bannerlord.Harmony", after=("Native",))
        self._module("Native")
        path = self._launcher(["Native", "Bannerlord.Harmony"])
        main._bl_apply_launcher_order(path, self.dir)
        order = self._order(path)
        self.assertEqual(order, ["Bannerlord.Harmony", "Native"])

    def test_case_differences_between_launcher_and_manifest_still_match(self):
        # Real case on device: launcher says "Sandbox", the folder and
        # manifest say "SandBox".
        self._module("SandBox", mod_id="SandBox")
        self._module("ModX", before=("sandbox",))
        path = self._launcher(["ModX", "Sandbox"])
        main._bl_apply_launcher_order(path, self.dir)
        order = self._order(path)
        self.assertEqual(order, ["Sandbox", "ModX"])

    def test_a_satisfied_order_is_left_untouched(self):
        self._module("A")
        self._module("B", before=("A",))
        path = self._launcher(["A", "B"])
        before_text = open(path, encoding="utf-8").read()
        self.assertEqual(main._bl_apply_launcher_order(path, self.dir), 0)
        self.assertEqual(open(path, encoding="utf-8").read(), before_text)

    def test_unconstrained_modules_keep_their_relative_order(self):
        for m in ("M1", "M2", "M3"):
            self._module(m)
        path = self._launcher(["M1", "M2", "M3"])
        main._bl_apply_launcher_order(path, self.dir)
        self.assertEqual(self._order(path), ["M1", "M2", "M3"])

    def test_a_cycle_keeps_current_order_rather_than_inventing_one(self):
        self._module("A", before=("B",))
        self._module("B", before=("A",))
        path = self._launcher(["A", "B"])
        main._bl_apply_launcher_order(path, self.dir)
        self.assertEqual(self._order(path), ["A", "B"])

    def test_selection_flags_survive_the_reorder(self):
        self._module("On", before=("Off",))
        self._module("Off")
        path = os.path.join(self.dir, "LauncherData.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("<UserData><SingleplayerData><ModDatas>"
                    "<UserModData><Id>On</Id><IsSelected>true</IsSelected>"
                    "</UserModData>"
                    "<UserModData><Id>Off</Id><IsSelected>false</IsSelected>"
                    "</UserModData>"
                    "</ModDatas></SingleplayerData></UserData>")
        main._bl_apply_launcher_order(path, self.dir)
        text = open(path, encoding="utf-8").read()
        self.assertLess(text.index("<Id>Off</Id>"), text.index("<Id>On</Id>"))
        import re as _re
        # xml_write_file pretty-prints, so allow whitespace between tags.
        flags = dict(_re.findall(
            r"<Id>([^<]+)</Id>\s*<IsSelected>([^<]+)</IsSelected>", text))
        self.assertEqual(flags, {"On": "true", "Off": "false"})


class TestBrowsePagingHonesty(unittest.TestCase):
    """Two paging defects found by a user searching the store:

    - The Load more button sat there doing nothing at the end of a search,
      because page fullness cannot distinguish "filtered short" from "no
      more mods" and only the backend, which saw the raw pages, knows.
    - Backfill fetched double, trimmed the surplus, and advanced next_offset
      past everything fetched, so the trimmed rows were silently skipped by
      the next page."""

    def test_the_source_reports_exhaustion(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("    async def get_mods("):]
        fn = fn[:fn.index("async def get_endorsement")]
        self.assertIn('"has_more"', fn)
        self.assertIn("exhausted = True", fn)

    def test_next_offset_stops_at_the_last_row_used(self):
        # Item-by-item consumption: the offset advances per raw row taken,
        # and the loop stops the moment the page is full - so nothing
        # fetched-but-unshown is ever skipped.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("    async def get_mods("):]
        fn = fn[:fn.index("async def get_endorsement")]
        self.assertIn("for node in raw:", fn)
        self.assertIn("src_offset += 1", fn)
        # The trim-after-the-fact is gone.
        self.assertNotIn("mods = mods[:wanted]", fn)


class TestBlseLaunchScript(unittest.TestCase):
    """BLSE under Proton: all three entry points die on a TypeLoadException
    for 0Harmony because Mono resolves the field type eagerly, before BLSE's
    own assembly resolver exists. MONO_PATH at the Harmony module's bin dir
    fixes it - measured on device, control run threw, both path forms
    survived. Delivered as a backend-written script at a no-space path,
    because decky-launch-options mangles quoted env assignments."""

    def test_the_script_is_written_and_executable(self):
        path = main._ensure_blse_launch_script()
        self.assertTrue(os.path.isfile(path))
        if os.name != "nt":
            self.assertTrue(os.access(path, os.X_OK))
        self.assertNotIn(" ", path, "the whole point is a no-space path")

    def test_the_script_is_refreshed_when_stale(self):
        path = main._ensure_blse_launch_script()
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\necho old\n")
        main._ensure_blse_launch_script()
        with open(path, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("MONO_PATH", body)

    def test_the_script_sets_mono_path_and_swaps_the_exe(self):
        body = main._BLSE_SCRIPT_BODY
        self.assertIn('export MONO_PATH="Z:$harmony"', body)
        # LauncherEx, not the vanilla wrapper: the vanilla launcher re-sorts
        # and rewrites LauncherData.xml at launch, ignoring optional ordering
        # metadata - it clobbered the topological sort within a minute.
        # Metadata-aware sorting is LauncherEx's headline feature.
        self.assertIn(
            "${@/TaleWorlds.MountAndBlade.Launcher.exe/"
            "Bannerlord.BLSE.LauncherEx.exe}", body)
        self.assertNotIn("Bannerlord.BLSE.Launcher.exe}", body.replace(
            "Bannerlord.BLSE.LauncherEx.exe}", ""))

    def test_missing_harmony_degrades_to_vanilla_not_unbootable(self):
        # The one property that must never regress: our setup step bricked
        # the game once by swapping to an exe that could not start.
        body = main._BLSE_SCRIPT_BODY
        tail = body[body.rindex("fi"):]
        self.assertIn('exec "$@"', tail)

    def test_game_status_maintains_the_script(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def get_game_status"):]
        fn = fn[:fn.index("decky.logger.info(f\"game status")]
        self.assertIn("_ensure_blse_launch_script()", fn)


class TestFrameworkCleanupWildcard(unittest.TestCase):
    """BLSE drops eight files into the game's bin folder with no manifest, so
    reset left all of them and Step 1 then offered "Install remaining
    frameworks (1)" on a machine with nothing installed."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.bin = os.path.join(self.dir, "bin", "Win64_Shipping_Client")
        os.makedirs(self.bin)
        for name in ("Bannerlord.BLSE.Launcher.exe",
                     "Bannerlord.BLSE.Shared.dll",
                     "Bannerlord.exe", "TaleWorlds.Library.dll"):
            open(os.path.join(self.bin, name), "w").close()

    def _reset(self, prefixes):
        return run(self.plugin.reset_game_modding(
            "mountandblade2bannerlord", os.path.basename(self.dir), "Modules",
            0, "", "starred", [], prefixes,
        )) if hasattr(self, "plugin") else None

    def test_the_wildcard_removes_only_the_matching_files(self):
        # Behavioural, because this code DELETES things: build a real game
        # folder, reset it, and check what survived.
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        game = "BL Wildcard Test"
        install = os.path.join(main.STEAM_COMMON, game)
        shutil.rmtree(install, ignore_errors=True)
        binpath = os.path.join(install, "bin", "Win64_Shipping_Client")
        os.makedirs(binpath)
        self.addCleanup(shutil.rmtree, install, ignore_errors=True)
        for name in ("Bannerlord.BLSE.Launcher.exe",
                     "Bannerlord.BLSE.Shared.dll",
                     "Bannerlord.exe", "TaleWorlds.Library.dll"):
            open(os.path.join(binpath, name), "w").close()
        plugin = main.Plugin()
        result = run(plugin.reset_game_modding(
            "mountandblade2bannerlord", game, "Modules", "folder", 0, "",
            "starred", ["bin/Win64_Shipping_Client/Bannerlord.BLSE.*"],
        ))
        self.assertTrue(result.get("ok"), result)
        left = sorted(os.listdir(binpath))
        self.assertEqual(left, ["Bannerlord.exe", "TaleWorlds.Library.dll"],
                         "reset removed the wrong files")

    def test_a_bare_folder_wildcard_cannot_be_declared(self):
        # "bin/*" would delete the game. The pattern needs a stem.
        source = open(main.__file__, encoding="utf-8").read()
        block = source[source.index('head, sep, tail = rel.rpartition("/")'):]
        block = block[:block.index("target = os.path.join(install_path")]
        self.assertIn("len(tail) > 1", block)

    def test_blse_declares_its_files_for_reset(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src", "games.ts")
        with open(path, encoding="utf-8") as fh:
            games = fh.read()
        self.assertIn(
            '"bin/Win64_Shipping_Client/Bannerlord.BLSE.*"', games,
            "BLSE has no cleanup prefixes, so reset leaves it behind and "
            "Step 1 reports the wrong count")


class TestFrameworkModuleActivation(unittest.TestCase):
    """Harmony installed into Modules/ and sat there DISABLED, so the game
    ignored it and BLSE could not find it. Michael, at the launcher: "i can
    see harmony in the mod list and its disabled, shall I enable it?" A setup
    step should not need that question."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _module(self, module_id, loads_first):
        d = os.path.join(self.dir, "Modules", module_id)
        os.makedirs(d)
        after = ("<ModulesToLoadAfterThis><Module Id=\"Native\" />"
                 "</ModulesToLoadAfterThis>") if loads_first else ""
        with open(os.path.join(d, "SubModule.xml"), "w", encoding="utf-8") as f:
            f.write("<Module><Id value=\"%s\" />%s</Module>" % (module_id, after))

    def test_harmony_declares_that_it_loads_first(self):
        self._module("Bannerlord.Harmony", True)
        self.assertTrue(main._bl_module_loads_first(
            self.dir, "Bannerlord.Harmony"))

    def test_an_ordinary_module_does_not(self):
        self._module("OpenSourceArmory", False)
        self.assertFalse(main._bl_module_loads_first(
            self.dir, "OpenSourceArmory"))

    def test_a_missing_module_is_not_assumed_to_load_first(self):
        self.assertFalse(main._bl_module_loads_first(self.dir, "NotThere"))

    def test_a_load_first_module_is_inserted_ahead_of_the_others(self):
        path = os.path.join(self.dir, "LauncherData.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("<UserData><SingleplayerData><ModDatas>"
                    "<UserModData><Id>Native</Id>"
                    "<IsSelected>true</IsSelected></UserModData>"
                    "</ModDatas></SingleplayerData></UserData>")
        self.assertTrue(main._set_module_selected(
            path, "Bannerlord.Harmony", True, True))
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertLess(text.index("Bannerlord.Harmony"), text.index("Native"),
                        "Harmony must sit ahead of the official modules")

    def test_an_ordinary_module_is_appended(self):
        path = os.path.join(self.dir, "LauncherData.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("<UserData><SingleplayerData><ModDatas>"
                    "<UserModData><Id>Native</Id>"
                    "<IsSelected>true</IsSelected></UserModData>"
                    "</ModDatas></SingleplayerData></UserData>")
        self.assertTrue(main._set_module_selected(path, "SomeMod", True, False))
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertGreater(text.index("SomeMod"), text.index("Native"))

    def test_the_framework_install_activates_the_module(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def install_framework"):]
        fn = fn[:fn.index("async def seed_game_ini")]
        self.assertIn("_set_module_selected(", fn)
        self.assertIn("_bl_module_loads_first(", fn)
        self.assertIn('result["activated"]', fn)


class TestBannerlordShaderCache(unittest.TestCase):
    """Open Source Armory ships a shader cache compiled 9 July 2026. The game
    updated on 11 August, rejects the cache, and crashes at the splash screen
    with nothing said to the user. Verified on device: crashed twice with the
    cache present, booted twice with it removed, item XMLs then loaded with an
    empty error log."""

    LINE = ("rgl_post_warning_line: Shader cache version of the external "
            "module (OpenSourceArmory) is invalid.")

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_the_games_own_words_are_parsed(self):
        self.assertEqual(
            main._BL_SHADER_INVALID_RE.findall(self.LINE), ["OpenSourceArmory"])

    def test_an_ordinary_log_line_is_not_a_match(self):
        self.assertEqual(main._BL_SHADER_INVALID_RE.findall(
            "rglShader_manager::read_compressed_shader_cache_package : 0.1"),
            [])

    def _stage(self, module, with_record=True):
        os.makedirs(os.path.join(self.dir, "Modules", module, "Shaders", "D3D11"))
        open(os.path.join(self.dir, "Modules", module, "Shaders", "D3D11",
                          "compressed_shader_cache.sack"), "w").close()
        recs = {module: {"mode": "folder", "moduleId": module,
                         "name": "Open Source Armory"}} if with_record else {}
        return {"installed": {"mountandblade2bannerlord": recs}}

    def test_our_own_mods_cache_is_moved_aside(self):
        settings = self._stage("OpenSourceArmory")
        with mock.patch.object(main, "_load_settings", return_value=settings),                 mock.patch.object(main, "_bl_invalid_shader_modules",
                                  return_value=["OpenSourceArmory"]):
            fixed = main._bl_clear_stale_shader_caches(
                "mountandblade2bannerlord", self.dir, 261550)
        self.assertEqual(fixed, ["Open Source Armory"])
        base = os.path.join(self.dir, "Modules", "OpenSourceArmory")
        self.assertFalse(os.path.isdir(os.path.join(base, "Shaders")))
        # Moved, not destroyed: reinstalling the mod is not the only way back.
        self.assertTrue(os.path.isdir(os.path.join(base, "Shaders.invalid")))

    def test_a_module_we_did_not_install_is_left_alone(self):
        # If the game ever rejects an OFFICIAL module's cache that is a game
        # files problem, and deleting from the game's own modules would be a
        # different and much worse thing to do.
        settings = self._stage("Native", with_record=False)
        with mock.patch.object(main, "_load_settings", return_value=settings),                 mock.patch.object(main, "_bl_invalid_shader_modules",
                                  return_value=["Native"]):
            fixed = main._bl_clear_stale_shader_caches(
                "mountandblade2bannerlord", self.dir, 261550)
        self.assertEqual(fixed, [])
        self.assertTrue(os.path.isdir(
            os.path.join(self.dir, "Modules", "Native", "Shaders")))

    def test_nothing_rejected_means_nothing_touched(self):
        settings = self._stage("OpenSourceArmory")
        with mock.patch.object(main, "_load_settings", return_value=settings),                 mock.patch.object(main, "_bl_invalid_shader_modules",
                                  return_value=[]):
            self.assertEqual(main._bl_clear_stale_shader_caches(
                "mountandblade2bannerlord", self.dir, 261550), [])
        self.assertTrue(os.path.isdir(os.path.join(
            self.dir, "Modules", "OpenSourceArmory", "Shaders")))

    def test_a_cache_older_than_the_game_build_is_stripped_at_install(self):
        # Catching it at install beats waiting for a crash: the reactive
        # version needs the game to have died once, with no message, and the
        # user to think of opening the Health page.
        os.makedirs(os.path.join(self.dir, "Modules", "OSA", "Shaders", "D3D11"))
        cache = os.path.join(self.dir, "Modules", "OSA", "Shaders", "D3D11",
                             "compressed_shader_cache.sack")
        open(cache, "w").close()
        os.utime(cache, (1_000_000, 1_000_000))  # long before the game build
        settings = {"installed": {"mountandblade2bannerlord": {
            "OSA": {"name": "Open Source Armory"}}}}
        with mock.patch.object(main, "_load_settings", return_value=settings),                 mock.patch.object(main, "_game_updated_at",
                                  return_value=2_000_000):
            moved = main._bl_strip_outdated_shader_caches(
                "mountandblade2bannerlord", self.dir, 261550)
        self.assertEqual(moved, ["Open Source Armory"])
        self.assertTrue(os.path.isdir(
            os.path.join(self.dir, "Modules", "OSA", "Shaders.invalid")))

    def test_a_cache_newer_than_the_game_build_is_kept(self):
        # A valid cache is a real benefit - the game loads faster with it.
        os.makedirs(os.path.join(self.dir, "Modules", "OSA", "Shaders", "D3D11"))
        cache = os.path.join(self.dir, "Modules", "OSA", "Shaders", "D3D11",
                             "compressed_shader_cache.sack")
        open(cache, "w").close()
        os.utime(cache, (3_000_000, 3_000_000))
        settings = {"installed": {"mountandblade2bannerlord": {"OSA": {}}}}
        with mock.patch.object(main, "_load_settings", return_value=settings),                 mock.patch.object(main, "_game_updated_at",
                                  return_value=2_000_000):
            self.assertEqual(main._bl_strip_outdated_shader_caches(
                "mountandblade2bannerlord", self.dir, 261550), [])
        self.assertTrue(os.path.isdir(
            os.path.join(self.dir, "Modules", "OSA", "Shaders")))

    def test_an_unknown_game_build_strips_nothing(self):
        # No date to compare against means no action, not a guess.
        os.makedirs(os.path.join(self.dir, "Modules", "OSA", "Shaders", "D3D11"))
        open(os.path.join(self.dir, "Modules", "OSA", "Shaders", "D3D11",
                          "x.sack"), "w").close()
        settings = {"installed": {"mountandblade2bannerlord": {"OSA": {}}}}
        with mock.patch.object(main, "_load_settings", return_value=settings),                 mock.patch.object(main, "_game_updated_at", return_value=0):
            self.assertEqual(main._bl_strip_outdated_shader_caches(
                "mountandblade2bannerlord", self.dir, 261550), [])

    def test_only_bannerlord_runs_this(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        block = source[source.index("shader_caches_fixed = ("):]
        block = block[:block.index("script = _redscript_report")]
        self.assertIn('game_domain == "mountandblade2bannerlord"', block)


class TestW3GameBranchVariants(unittest.TestCase):
    """Witcher 3 ships two branches and mods follow, often on separate Nexus
    pages - so matching requirements by mod id cannot tell that one satisfies
    the other. Health told Michael to install two CLASSIC mods he already had
    in Next-Gen form, and the one-tap button would have put classic files
    into a next-gen install."""

    def test_the_two_branches_of_one_mod_match(self):
        # His exact case.
        self.assertEqual(
            main._w3_variant_key("(Classic) Base Appearances Special Expansion"),
            main._w3_variant_key("(Next Gen) Base Appearances Special Expansion"),
        )

    def test_a_version_labelled_branch_matches_too(self):
        # 1.32 is how authors name the pre-next-gen build.
        self.assertEqual(
            main._w3_variant_key("Upscaled UI - HUD Elements - 1.32"),
            main._w3_variant_key("Upscaled UI - HUD Elements (Next-Gen)"),
        )

    def test_different_mods_still_differ(self):
        # The third finding was genuine and must stay reported.
        self.assertNotEqual(
            main._w3_variant_key("Promotional Atmosphere Lighting Mod"),
            main._w3_variant_key("Novigrad Sewers Lighting Improved"),
        )

    def test_punctuation_and_case_do_not_matter(self):
        self.assertEqual(
            main._w3_variant_key("Friendly HUD"),
            main._w3_variant_key("friendly  hud!"),
        )

    def test_a_name_that_is_only_a_branch_label_matches_nothing(self):
        # Reduces to "", which is excluded from the have-set on purpose.
        self.assertEqual(main._w3_variant_key("(Next Gen)"), "")

    def test_only_witcher_3_is_affected(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        block = source[source.index("        have_ids = {int(rec"):]
        block = block[:block.index("needs_mods.append")]
        self.assertIn('game_domain == "witcher3" else set()', block)
        self.assertIn('game_domain == "witcher3"', block)


class TestW3DebugOverlaysAreSwitchedOff(unittest.TestCase):
    """New Lightning FX ships Debug Mode ON, so the #1 Witcher 3 collection
    puts a debug panel over the game for everyone who installs it. Michael
    had no way to know which of 162 mod folders it belonged to, or that the
    switch was three menus deep."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_only_debug_keys_are_flipped(self):
        text = ("modStatus=true" + chr(10) + "modDebugInfo=true" + chr(10) +
                "modAllowStrike=true" + chr(10) + "modDebugMode=true" + chr(10))
        out, keys = main._w3_quiet_debug_text(text)
        self.assertEqual(keys, ["modDebugInfo", "modDebugMode"])
        self.assertIn("modStatus=true", out)
        self.assertIn("modAllowStrike=true", out)
        self.assertIn("modDebugInfo=false", out)
        self.assertIn("modDebugMode=false", out)

    def test_a_debug_key_already_off_is_left_alone(self):
        out, keys = main._w3_quiet_debug_text("modDebugMode=false" + chr(10))
        self.assertEqual(keys, [])
        self.assertEqual(out, "modDebugMode=false" + chr(10))

    def test_other_settings_are_untouched(self):
        # This file holds the user's graphics and controls too.
        text = "[Rendering]" + chr(10) + "TextureMemoryBudget=800" + chr(10)
        out, keys = main._w3_quiet_debug_text(text)
        self.assertEqual((out, keys), (text, []))

    def test_the_file_is_backed_up_before_it_is_touched(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("def _w3_quiet_debug_overlays"):]
        fn = fn[:fn.index("def _prefix_user_path")]
        self.assertIn(".decky-nexus.bak", fn)
        self.assertIn("shutil.copy2(path, backup)", fn)
        # Once - a second run must not overwrite the original backup with
        # an already-modified file.
        self.assertIn("if not os.path.isfile(backup):", fn)

    def test_what_was_switched_off_is_remembered(self):
        # The run that fixes it is the only run that CAN report it - the
        # next check finds nothing on and says nothing. Michael: "I clicked
        # refresh and saw a brief message about debug mode but then it went
        # once the refresh finished."
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("def _w3_quiet_debug_overlays"):]
        fn = fn[:fn.index("def _prefix_user_path")]
        self.assertIn('setdefault("w3_debug_quieted", {})', fn)
        check = source[source.index("        if game_domain == \"witcher3\" and app_id:"):]
        check = check[:check.index("script = _redscript_report")]
        # Reported from the store, not from this run's return value.
        self.assertIn('get("w3_debug_quieted")', check)

    def test_both_renderers_settings_files_are_covered(self):
        # Next-gen DX12 writes dx12user.settings; classic writes
        # user.settings; a device can have either or both.
        self.assertEqual(
            main._W3_SETTINGS_FILES, ("dx12user.settings", "user.settings"))


class TestW3MergeSafety(unittest.TestCase):
    """Groundwork for turning auto-merge back on. It has been off since
    2026-07-24 because a merged script crashed the game before the compile
    stage, and nothing checked the result. Two lessons taken from Vortex's
    own Witcher 3 extension, read for this."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_merging_is_on_unless_switched_off(self):
        # Michael, after the test run: "I booted and played for a few mins
        # and no errors at all." 25 of 30 previously-skipped mods merged
        # into 36 scripts on the #1 collection. Off since 2026-07-24 for a
        # crash whose likely cause - no compile trigger - is now fixed.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('settings_now.get("w3_auto_merge", True)', source)
        # An explicit false must still turn it off.
        self.assertNotIn('settings_now.get("w3_auto_merge"):', source)

    def test_a_compile_trigger_is_written(self):
        # Vortex ships mod0000____CompilationTrigger because the game caches
        # compiled scripts: a correct merge can otherwise do nothing at all.
        path = main._w3_write_compile_trigger(self.dir)
        self.assertTrue(os.path.isdir(
            os.path.join(self.dir, main.W3_COMPILE_TRIGGER,
                         "content", "scripts")))
        self.assertTrue(os.path.isfile(
            os.path.join(path, "decky-nexus.txt")))
        # Sorts before any real mod, like the merged folder itself.
        self.assertTrue(main.W3_COMPILE_TRIGGER.startswith("mod0000"))

    def test_writing_it_twice_does_not_clobber_the_note(self):
        main._w3_write_compile_trigger(self.dir)
        note = os.path.join(self.dir, main.W3_COMPILE_TRIGGER,
                            "decky-nexus.txt")
        with open(note, "a", encoding="utf-8") as f:
            f.write("edited by hand")
        main._w3_write_compile_trigger(self.dir)
        with open(note, encoding="utf-8") as f:
            self.assertIn("edited by hand", f.read())

    def test_the_trigger_is_actually_written_after_a_merge(self):
        # It existed as a helper for four versions without a single caller.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("_w3_write_compile_trigger(mods_path)", source)
        merge_block = source[source.index("if merged_rels is not None:"):]
        merge_block = merge_block[:merge_block.index("else:")]
        self.assertIn("_w3_write_compile_trigger(", merge_block)

    def test_a_merge_missing_a_contributor_is_stale(self):
        # Vortex's MergeDataViolationError in miniature: the merged file
        # still carries a departed mod's edits, so switching that mod off
        # leaves its changes in the game with nothing saying so.
        settings = {"w3_merges": {"witcher3": {
            "game/player/r4player.ws": {"mods": ["modA", "modB"]}}}}
        os.makedirs(os.path.join(self.dir, "modA"))
        stale = main._w3_stale_merges(settings, "witcher3", self.dir)
        self.assertEqual(stale, [("game/player/r4player.ws", "modB")])

    def test_a_complete_merge_is_not_stale(self):
        settings = {"w3_merges": {"witcher3": {
            "game/player/r4player.ws": {"mods": ["modA", "modB"]}}}}
        for m in ("modA", "modB"):
            os.makedirs(os.path.join(self.dir, m))
        self.assertEqual(main._w3_stale_merges(settings, "witcher3", self.dir), [])

    def test_no_merges_recorded_is_not_an_error(self):
        self.assertEqual(main._w3_stale_merges({}, "witcher3", self.dir), [])


class TestIncompatibilityIsPairwise(unittest.TestCase):
    """First Person Souls breaks ERR's menus - "?MenuText?" on character
    selects, no Reforged menus - but it is a native, so no files overlap and
    it is presumably fine on its own. Michael: "I think mark first person
    souls incompatible". Marked WITH ERR, not in the abstract."""

    PAIRS = {"mod_incompat": {"eldenring": {
        "3266": {"with": 541, "why": "It replaces the menus ERR provides."}}}}

    def _settings(self, err_enabled):
        s = dict(self.PAIRS)
        s["installed"] = {"eldenring": {"ERR": {
            "mode": "me3", "mod_id": 541, "name": "ERR - ELDEN RING Reforged",
            "enabled": err_enabled, "installed_at": 1,
        }}}
        return s

    def test_refused_while_the_other_mod_is_on(self):
        pair = main._incompatible_partner(
            self._settings(True), "eldenring", 3266)
        self.assertIsNotNone(pair)
        self.assertEqual(pair[0], "ERR - ELDEN RING Reforged")

    def test_allowed_once_the_other_mod_is_switched_off(self):
        # Switching it off is the remedy, so it must actually work.
        self.assertIsNone(main._incompatible_partner(
            self._settings(False), "eldenring", 3266))

    def test_allowed_when_the_other_mod_is_absent(self):
        # On its own it is not a broken mod, and must not be treated as one.
        s = dict(self.PAIRS)
        s["installed"] = {"eldenring": {}}
        self.assertIsNone(main._incompatible_partner(s, "eldenring", 3266))

    def test_an_unrecorded_mod_is_unaffected(self):
        self.assertIsNone(main._incompatible_partner(
            self._settings(True), "eldenring", 9999))


class TestAVerdictCanCoverOlderVersions(unittest.TestCase):
    """Seamless Co-op is hard version-locked to the game build, so every
    release before the current one fails identically. Elden Essentials pins
    1.5.1, EldenBoobs pins 1.4.3, and the working install is 1.9.9.
    Recording them one at a time is whack-a-mole."""

    def test_an_exact_version_still_matches(self):
        e = {"version": "1.5.1"}
        self.assertTrue(main._verdict_covers_version(e, "1.5.1"))
        self.assertFalse(main._verdict_covers_version(e, "1.4.3"))

    def test_upto_covers_anything_older(self):
        e = {"version": "1.5.1", "upto": True}
        self.assertTrue(main._verdict_covers_version(e, "1.4.3"))
        self.assertTrue(main._verdict_covers_version(e, "1.5.1"))
        self.assertTrue(main._verdict_covers_version(e, "0.9"))

    def test_upto_leaves_the_working_release_alone(self):
        # The one that matters: Michael has 1.9.9 installed and working,
        # and asked us not to break his co-op setup.
        e = {"version": "1.5.1", "upto": True}
        self.assertFalse(main._verdict_covers_version(e, "1.9.9"))
        self.assertFalse(main._verdict_covers_version(e, "1.10.0"))

    def test_no_recorded_version_means_every_version(self):
        self.assertTrue(main._verdict_covers_version({}, "2.0"))

    def test_an_unreadable_version_is_not_swept_up(self):
        e = {"version": "1.5.1", "upto": True}
        self.assertFalse(main._verdict_covers_version(e, "beta"))


class TestTwoModsCannotOwnTheSameFile(unittest.TestCase):
    """Elden Essentials ships ERR and First Person Souls together and both
    replace the same msg files. One wins; the other's changes vanish
    silently - Michael saw "?MenuText?" on character selects and lost ERR's
    menus. He asked for "a fomod sort of thing... let the user choose one or
    the other with a bit of info about what they are"."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _pkg(self, name, *rel_paths):
        root = os.path.join(self.dir, name)
        for rel in rel_paths:
            full = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, "w").close()
        return root

    def test_it_lists_files_not_folders(self):
        root = self._pkg("a", "msg/engus/menu.msgbnd.dcx", "parts/x.dcx")
        self.assertEqual(
            main._me3_package_files(root),
            {"msg/engus/menu.msgbnd.dcx", "parts/x.dcx"},
        )

    def test_different_files_in_the_same_folder_do_not_clash(self):
        # ABNB and ERR both provide parts/ and run together fine - verified
        # in the character creator. Folder overlap must never be a clash.
        abnb = main._me3_package_files(
            self._pkg("abnb", "parts/bd_f_0000.partsbnd.dcx"))
        err = main._me3_package_files(
            self._pkg("err", "parts/am_m_9999.partsbnd.dcx"))
        self.assertEqual(abnb & err, set())

    def test_the_same_file_is_a_clash(self):
        fps = main._me3_package_files(
            self._pkg("fps", "msg/engus/menu.msgbnd.dcx"))
        err = main._me3_package_files(
            self._pkg("err2", "msg/engus/menu.msgbnd.dcx"))
        self.assertEqual(fps & err, {"msg/engus/menu.msgbnd.dcx"})

    def test_a_missing_package_dir_is_not_a_clash(self):
        self.assertEqual(main._me3_package_files("/no/such/path"), set())

    def test_a_disabled_mod_cannot_own_anything(self):
        # Switching the other one off IS the remedy, so it has to work
        # without uninstalling anything.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("def _me3_asset_clash"):]
        fn = fn[:fn.index("def _preview_has_dll")]
        self.assertIn('not rec.get("enabled", True)', fn)


class TestReportBodyStaysShort(unittest.TestCase):
    """The report goes into a GitHub URL. An over-long one fails, and fails
    with a 500 when the user has to sign in on the way - which is exactly
    when it happened to Michael."""

    def test_the_log_is_named_not_embedded(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("async def build_report"):]
        fn = fn[:fn.index("async def get_health_check")]
        # The log tail used to be pasted into the body wholesale.
        self.assertNotIn("_plugin_log_tail(", fn)
        self.assertIn("### Log", fn)

    def test_the_helper_still_exists_for_other_callers(self):
        # Kept: a future attachment or a bug-report file would want it.
        self.assertTrue(callable(main._plugin_log_tail))


class TestFreeAccountIsToldPlainly(unittest.TestCase):
    """Michael tried a free account: the download failed, correctly, but the
    message read like a missing feature rather than "this will not work for
    you". A free user deserves to know where they stand in one sentence."""

    def test_a_free_account_is_told_it_will_not_work(self):
        msg = main._download_forbidden_reason("{}", False)
        self.assertIn("free", msg.lower())
        self.assertIn("Premium", msg)
        # No roadmap language: "not implemented yet" reads as "coming soon".
        self.assertNotIn("not implemented", msg.lower())
        self.assertNotIn("yet", msg.lower())

    def test_a_deleted_mod_does_not_blame_the_account(self):
        # The original bug this function was written for: telling a Premium
        # user to buy Premium because an author deleted a mod.
        msg = main._download_forbidden_reason(
            '{"code":403,"message":"Mod not available: 502"}', False)
        self.assertIn("removed", msg.lower())
        self.assertNotIn("Premium", msg)

    def test_moderation_is_named_as_moderation(self):
        msg = main._download_forbidden_reason(
            '{"code":403,"message":"File currently not available. X is '
            'under moderation"}', True)
        self.assertIn("reviewed", msg.lower())

    def test_one_wording_everywhere(self):
        # A second copy of this message drifted for weeks. Both 403 paths
        # now call the same function.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn('"error": "Direct downloads need a Premium', source)
        self.assertGreaterEqual(source.count("_download_forbidden_reason("), 3)


class TestABadgeCannotOverstate(unittest.TestCase):
    """EldenBoobs skipped all 16 of its mods, was recorded as installed
    anyway, and a later Elden Ring session promoted that to VERIFIED ON
    DECK. Michael: "Why has Elden boobs been given a verifed badge when we
    havent done a successful install confirmation?" """

    def test_a_run_that_installed_nothing_records_no_verdict(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        fn = source[source.index("def _record_collection_verdict"):]
        fn = fn[:fn.index("def _collection_verdicts(")] if "def _collection_verdicts(" in fn else fn[:4000]
        self.assertIn("if int(mods or 0) <= 0:", fn)
        self.assertIn("no verdict recorded", fn)

    def test_the_frontend_guards_it_too(self):
        import os as _os
        path = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "src", "CollectionPage.tsx")
        with open(path, encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("if (installedRequiredCount > 0)", page)


class TestOnlyCodeCanAgeOut(unittest.TestCase):
    """The older-patch rule skipped A Better Nude Body - a 2022 asset mod
    verified working in the character creator on this exact build - plus
    every texture mod in EldenBoobs. Michael: "just because its older
    doesnt mean its broke." A mesh cannot fail a signature scan; it never
    scans anything. Only code can age out."""

    def test_a_dll_anywhere_in_the_archive_counts(self):
        self.assertTrue(main._preview_has_dll(
            [{"name": "mod", "type": "directory", "children": [
                {"name": "SkipTheIntro.dll", "type": "file"}]}]))

    def test_an_archive_of_meshes_ships_no_code(self):
        # ABNB's real file list.
        self.assertFalse(main._preview_has_dll([
            {"name": "bd_f_0000.partsbnd.dcx", "type": "file"},
            {"name": "fc_f_0100.partsbnd.dcx", "type": "file"},
            {"name": "lg_f_0000.partsbnd.dcx", "type": "file"},
        ]))

    def test_a_texture_pack_ships_no_code(self):
        self.assertFalse(main._preview_has_dll([
            {"name": "parts", "type": "directory", "children": [
                {"name": "am_m_1234.tpf.dcx", "type": "file"}]},
            {"name": "readme.txt", "type": "file"},
        ]))

    def test_junk_in_the_tree_is_survivable(self):
        self.assertFalse(main._preview_has_dll([None, "x.dll", 3, {}]))

    def test_a_known_asset_mod_is_never_re_checked(self):
        with mock.patch.object(main, "_load_settings", return_value={
            "native_facts": {"eldenring": {"1153": False}},
        }), mock.patch.object(main.aiohttp, "ClientSession") as session:
            self.assertIs(run(main._mod_ships_dll("eldenring", 1153, 1)), False)
        session.assert_not_called()

    def test_no_evidence_means_no_action(self):
        # None, not False: "we could not look" must never read as "it is
        # code" NOR silently as "it is not" for a caller that treats False
        # as proof. The stale check requires True specifically.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        check = source[source.index("async def _stale_native_warning"):]
        check = check[:check.index("async def get_mod_files")]
        self.assertIn("ships_dll is not True", check)


class TestStaleNativeWarning(unittest.TestCase):
    """Every dll in Elden Ring's Performance and QoL collection except the
    loader pops "Could not find signature!" on build 22984413 - a blocking
    Win32 dialog that reads as a frozen game. Proven by isolation on
    device, not inferred: with the loader alone it boots clean."""

    JAN_2022 = 1641000000      # the collection's dlls
    JUL_2026 = 1783000000      # the game's current patch

    def test_a_2022_dll_against_a_2026_patch_is_flagged(self):
        note = main._stale_native_note(
            self.JAN_2022, self.JUL_2026, "Unlock the framerate")
        self.assertIn("January 2022", note)
        self.assertIn("Could not find signature", note)
        # It must tell the user what to do when it happens.
        self.assertIn("My Mods", note)

    def test_a_mod_newer_than_the_patch_is_silent(self):
        self.assertEqual(main._stale_native_note(
            self.JUL_2026, self.JAN_2022, "Fresh mod"), "")

    def test_a_mod_from_around_the_patch_is_silent(self):
        # Authors update within weeks of a patch; warning about those would
        # cry wolf on exactly the mods that DO work.
        self.assertEqual(main._stale_native_note(
            self.JUL_2026 - 10 * 86400, self.JUL_2026, "Recent mod"), "")

    def test_err_at_sixty_days_is_maintained_not_stale(self):
        # Measured, not assumed: ERR's release is 60 days older than the
        # patch and boots clean. A 45-day threshold flagged it, and a
        # warning that fires on the good ones teaches the user to ignore it.
        self.assertEqual(main._stale_native_note(
            self.JUL_2026 - 60 * 86400, self.JUL_2026, "ERR"), "")

    def test_twenty_one_months_behind_still_warns(self):
        # Skip the intro logos: old enough to fail, and it does.
        self.assertIn("Could not find signature", main._stale_native_note(
            self.JUL_2026 - 640 * 86400, self.JUL_2026, "Skip the intro"))

    def test_unknown_dates_never_warn(self):
        self.assertEqual(main._stale_native_note(0, self.JUL_2026, "x"), "")
        self.assertEqual(main._stale_native_note(self.JAN_2022, 0, "x"), "")

    def test_an_archived_file_id_still_gets_a_date(self):
        # get_mod_files hides ARCHIVED and OLD_VERSION for the UI, but a
        # collection pins exact file ids - often now-archived ones. Looking
        # those up in the FILTERED list returned no date, so the check went
        # silent and the Performance and QoL run skipped none of its four
        # stale dlls. The lookup must read the unfiltered list.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        check = source[source.index("async def _stale_native_warning"):]
        check = check[:check.index("async def get_mod_files")]
        self.assertIn("_file_uploaded_at", check)
        # The filtered list is the bug, so the call must be gone.
        self.assertNotIn("self.get_mod_files", check)

    def test_the_manifests_own_lowercase_key_is_read(self):
        # Elden Ring's appmanifest spells it "lastupdated". A
        # case-sensitive match returned 0 for every game, and 0 means
        # cannot-tell, so the warning went silent everywhere and looked
        # like it was working. Michael: "I cant see the box".
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        os.makedirs(os.path.join(d, "common"), exist_ok=True)
        acf = os.path.join(d, "appmanifest_1245620.acf")
        tab = chr(9)
        body = chr(10).join([
            chr(34) + "AppState" + chr(34), "{",
            tab + chr(34) + "buildid" + chr(34) + tab + chr(34) + "22984413" + chr(34),
            tab + chr(34) + "lastupdated" + chr(34) + tab + chr(34) + "1780000000" + chr(34),
            "}",
        ])
        with open(acf, "w") as f:
            f.write(body + chr(10))
        with mock.patch.object(main, "STEAM_COMMON",
                               os.path.join(d, "common")):
            self.assertEqual(main._game_updated_at(1245620), 1780000000)
            self.assertEqual(main._game_updated_at(0), 0)
            self.assertEqual(main._game_updated_at(999999), 0)


class TestFolderModeFomod(unittest.TestCase):
    """A Palworld pak archive with a FOMOD wizard: mutually exclusive
    variants of one Pal in a single archive. The FOMOD check lived only
    in the dataDir branch, so folder-mode games installed EVERY variant
    at once - eight Bushi paks fighting over one Pal, and the chimera
    renders that implies (device, 2026-08-28, SexyBushiFomodInstaller)."""

    DOMAIN = "palworld"
    GAME = "Fomod Pak Test"
    MOD, FILE = 4321, 98765

    CONFIG = """<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <moduleName>Sexy Bushi</moduleName>
  <installSteps order="Explicit">
    <installStep name="Variant">
      <optionalFileGroups order="Explicit">
        <group name="Pick one" type="SelectExactlyOne">
          <plugins order="Explicit">
            <plugin name="SFW">
              <description>Safe</description>
              <files><file source="01.SexyBushi_SFW_P.pak" destination="01.SexyBushi_SFW_P.pak" /></files>
              <typeDescriptor><type name="Recommended" /></typeDescriptor>
            </plugin>
            <plugin name="NSFW">
              <description>Not safe</description>
              <files><file source="05.SexyBushi_NSFW_P.pak" destination="05.SexyBushi_NSFW_P.pak" /></files>
              <typeDescriptor><type name="Optional" /></typeDescriptor>
            </plugin>
          </plugins>
        </group>
      </optionalFileGroups>
    </installStep>
  </installSteps>
</config>"""

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        self.mods = os.path.join(
            self.install, "Pal", "Content", "Paks", "~mods"
        )
        os.makedirs(self.mods)
        settings = main._load_settings()
        settings["api_key"] = "k"
        main._save_settings(settings)
        os.makedirs(main.DOWNLOADS_DIR, exist_ok=True)
        shutil.rmtree(
            main._extract_scratch(self.MOD, self.FILE), ignore_errors=True
        )
        archive = main._archive_cache_path(self.MOD, self.FILE, "bushi.zip")
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("fomod/ModuleConfig.xml", self.CONFIG)
            z.writestr("01.SexyBushi_SFW_P.pak", "sfw")
            z.writestr("05.SexyBushi_NSFW_P.pak", "nsfw")
        self.plugin = main.Plugin()

    def tearDown(self):
        shutil.rmtree(self.install, ignore_errors=True)
        main.PENDING_FOMODS.clear()

    def _start(self):
        return run(self.plugin.install_mod(
            self.DOMAIN, self.MOD, self.FILE, "bushi.zip", "Sexy Bushi",
            "1.0", self.GAME, "Pal/Content/Paks/~mods", "", "", "folder", 0,
        ))

    def _paks(self):
        found = []
        for _root, _dirs, names in os.walk(self.mods):
            found += [n for n in names if n.endswith(".pak")]
        return sorted(found)

    def test_the_wizard_is_offered_not_bypassed(self):
        # Before the fix this returned ok with BOTH paks on disk.
        r = self._start()
        self.assertTrue(r.get("needs_fomod"), r)
        self.assertTrue(r.get("fomod_token"))
        self.assertTrue(r["wizard"]["steps"])
        self.assertEqual(self._paks(), [])

    def test_finishing_installs_only_the_chosen_variant(self):
        r = self._start()
        plugins = r["wizard"]["steps"][0]["groups"][0]["plugins"]
        sfw = next(p for p in plugins if p["name"].startswith("SFW"))
        done = run(self.plugin.install_fomod(r["fomod_token"], [sfw["id"]]))
        self.assertTrue(done.get("ok"), done)
        self.assertEqual(self._paks(), ["01.SexyBushi_SFW_P.pak"])
        recs = main._load_settings()["installed"][self.DOMAIN]
        self.assertTrue(
            any(v.get("mod_id") == self.MOD for v in recs.values()), recs
        )

    def test_curator_choices_finish_it_without_a_wizard(self):
        # The collection path: Vortex-manifest-shaped choices answer the
        # wizard, and only the curator's variant lands.
        r = self._start()
        done = run(self.plugin.install_fomod_auto(
            r["fomod_token"], [{"name": "Pick one", "choices": ["NSFW"]}]
        ))
        self.assertTrue(done.get("ok"), done)
        self.assertEqual(self._paks(), ["05.SexyBushi_NSFW_P.pak"])

    def test_the_finish_survives_the_archive_cache_being_evicted(self):
        # The finish re-enters the installer, whose download step is
        # normally short-circuited by the cached archive. If the cache
        # was cleaned between park and finish, the staged selection is
        # already a prepared scratch - the download result is unused.
        r = self._start()
        plugins = r["wizard"]["steps"][0]["groups"][0]["plugins"]
        sfw = next(p for p in plugins if p["name"].startswith("SFW"))

        async def fake_download(*a, **k):
            return "", os.path.join(TEST_ROOT, "gone.zip")

        real = main._download_archive
        main._download_archive = fake_download
        try:
            done = run(
                self.plugin.install_fomod(r["fomod_token"], [sfw["id"]])
            )
        finally:
            main._download_archive = real
        self.assertTrue(done.get("ok"), done)
        self.assertEqual(self._paks(), ["01.SexyBushi_SFW_P.pak"])


class TestReleaseZipGate(unittest.TestCase):
    """A release zip that cannot install must not be publishable.

    v1.4.0 shipped with its top folder named "Nexus-Mods" against a
    plugin.json name of "Nexus Mods", because the zip was hand-built with
    Compress-Archive instead of by release.ps1. Decky reports that
    disagreement by sitting on "PARSING ZIP FILE" forever and install.sh
    dies with "extraction did not produce Nexus Mods/plugin.json", so the
    only symptom anybody saw was a user saying the update does not work and
    falling back to 1.0.0.

    release.ps1 already carried a comment warning about exactly this. A
    comment did not stop it, so the check now lives in the code that runs on
    every path to publishing, and here.
    """

    def setUp(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
        import makestore

        self.check = makestore.check_zip
        with open(os.path.join(REPO_ROOT, "plugin.json"), encoding="utf-8") as f:
            self.plugin_name = json.load(f)["name"]
        self.tmp = os.path.join(TEST_ROOT, "zipgate")
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.makedirs(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _zip(self, entries: dict, name="rel.zip") -> str:
        path = os.path.join(self.tmp, name)
        with zipfile.ZipFile(path, "w") as z:
            for rel, body in entries.items():
                z.writestr(rel, body)
        return path

    def _good_entries(self, top=None, version="9.9.9"):
        top = top if top is not None else self.plugin_name
        return {
            f"{top}/plugin.json": json.dumps({"name": self.plugin_name}),
            f"{top}/package.json": json.dumps({"version": version}),
            f"{top}/main.py": "x",
            f"{top}/dist/index.js": "x",
            f"{top}/LICENSE": "x",
            f"{top}/README.md": "x",
        }

    def test_a_correct_zip_passes(self):
        z = self._zip(self._good_entries())
        self.assertEqual(self.check(z, self.plugin_name, "9.9.9"), [])

    def test_the_hyphenated_folder_that_shipped_is_refused(self):
        # The exact v1.4.0 mistake.
        z = self._zip(self._good_entries(top="Nexus-Mods"))
        problems = self.check(z, self.plugin_name, "9.9.9")
        self.assertTrue(problems)
        self.assertIn("top-level folder", problems[0])
        self.assertIn("Nexus-Mods", problems[0])

    def test_windows_separators_are_refused(self):
        # Invisible on Windows, which is how it shipped once before: a Linux
        # tool sees one file called "Nexus Mods\LICENSE", not a folder.
        #
        # The entry has to be forged. zipfile rewrites os.sep to "/" on
        # write, so on Windows there is no way through the library to
        # produce the name Compress-Archive produces. Patching the bytes is:
        # the replacement is the same length, so every offset in the local
        # header and the central directory stays valid.
        path = os.path.join(self.tmp, "backslash.zip")
        good = f"{self.plugin_name}/LICENSE"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(good, "x")
        with open(path, "rb") as f:
            raw = f.read()
        with open(path, "wb") as f:
            f.write(raw.replace(good.encode(), good.replace("/", "\\").encode()))
        # orig_filename, not namelist(): on Windows zipfile normalises the
        # separator away as it reads, so namelist() would report this forged
        # zip as clean. That is the same trap the check itself fell into.
        self.assertIn(
            "\\",
            zipfile.ZipFile(path).infolist()[0].orig_filename,
            "forging the entry failed",
        )
        problems = self.check(path, self.plugin_name, "9.9.9")
        self.assertTrue(
            any("Windows separators" in p for p in problems), problems
        )

    def test_a_missing_runtime_file_is_refused(self):
        for drop in ("plugin.json", "main.py", "dist/index.js"):
            entries = self._good_entries()
            del entries[f"{self.plugin_name}/{drop}"]
            z = self._zip(entries, name=f"drop-{drop.replace('/', '_')}.zip")
            problems = self.check(z, self.plugin_name, "9.9.9")
            self.assertTrue(problems, f"dropping {drop} must be refused")

    def test_a_zip_of_the_wrong_version_is_refused(self):
        # Publishing last release's artifact under this release's tag: the
        # filename would say 9.9.9 and the running plugin would be 1.0.0.
        z = self._zip(self._good_entries(version="1.0.0"))
        problems = self.check(z, self.plugin_name, "9.9.9")
        self.assertTrue(any("1.0.0" in p for p in problems))

    def test_two_top_level_folders_are_refused(self):
        entries = self._good_entries()
        entries["Other/thing.txt"] = "x"
        z = self._zip(entries)
        problems = self.check(z, self.plugin_name, "9.9.9")
        self.assertTrue(any("ONE top-level folder" in p for p in problems))

    def test_an_empty_or_corrupt_zip_is_refused_not_crashed_on(self):
        empty = os.path.join(self.tmp, "empty.zip")
        with zipfile.ZipFile(empty, "w"):
            pass
        self.assertTrue(self.check(empty, self.plugin_name, "9.9.9"))
        junk = os.path.join(self.tmp, "junk.zip")
        with open(junk, "wb") as f:
            f.write(b"not a zip at all")
        self.assertTrue(self.check(junk, self.plugin_name, "9.9.9"))

    def test_install_sh_and_plugin_json_agree_on_the_folder_name(self):
        """install.sh hardcodes PLUGIN and refuses anything else. If someone
        renames the plugin in plugin.json, the installer must be renamed with
        it or every install dies at the extraction check."""
        with open(os.path.join(REPO_ROOT, "install.sh"), encoding="utf-8") as f:
            script = f.read()
        self.assertIn(
            f'PLUGIN="{self.plugin_name}"',
            script,
            "install.sh's PLUGIN must match plugin.json's name exactly",
        )

    def test_deploy_ps1_uses_the_same_folder_users_get(self):
        """deploy.ps1 stripped the space out of the plugin name, so every dev
        deploy went to "Nexus-Mods" while a real install goes to "Nexus
        Mods". Hardware testing was therefore never testing the layout users
        get, and deploying onto a device that already had a released build
        left TWO plugin folders for Decky to load."""
        with open(os.path.join(REPO_ROOT, "deploy.ps1"), encoding="utf-8") as f:
            script = f.read()
        stripping = [
            line
            for line in script.splitlines()
            if "$folder =" in line
            and "-replace ' ', '-'" in line
            and not line.strip().startswith("#")
        ]
        self.assertEqual(
            stripping, [], "deploy.ps1 must not strip spaces from the folder"
        )
        self.assertIn(
            "$pluginJson.name",
            script,
            "deploy.ps1 must take the folder from plugin.json, like the others",
        )

    def test_release_ps1_takes_the_folder_name_from_plugin_json(self):
        """Not from a literal. The literal is what drifted."""
        with open(os.path.join(REPO_ROOT, "release.ps1"), encoding="utf-8") as f:
            script = f.read()
        self.assertIn("$pluginJson.name", script)
        self.assertIn("checkzip.py", script, "release.ps1 must gate on the check")
        # Compress-Archive is what produced the broken zip. It is named in a
        # comment warning against it, so only CALLS count.
        called = [
            line
            for line in script.splitlines()
            if "Compress-Archive" in line and not line.strip().startswith("#")
        ]
        self.assertEqual(called, [], "release.ps1 must not call Compress-Archive")

    def test_any_built_zip_in_dist_is_installable(self):
        """Catches a stale hand-built artifact sitting in dist/ waiting to be
        uploaded. Skips when there is nothing built."""
        dist = os.path.join(REPO_ROOT, "dist")
        zips = [
            os.path.join(dist, n)
            for n in (os.listdir(dist) if os.path.isdir(dist) else [])
            if n.endswith(".zip")
        ]
        if not zips:
            self.skipTest("no release zip built")
        with open(os.path.join(REPO_ROOT, "package.json"), encoding="utf-8") as f:
            version = json.load(f)["version"]
        for z in zips:
            problems = self.check(z, self.plugin_name, version)
            self.assertEqual(problems, [], f"{os.path.basename(z)}: {problems}")


class TestBg3Mode(unittest.TestCase):
    """Baldur's Gate 3, native Linux build: paks into the Larian profile's
    Mods dir, registered in modsettings.lsx from each pak's embedded
    meta.lsx. The parse was proven against ImpUI's real pak on device
    before this existed; these tests hold the whole mode to that shape
    with synthetic LSPK v18 paks built byte by byte."""

    DOMAIN = "baldursgate3"
    GAME = "BG3 Test"
    MOD, FILE = 7777, 55555

    # The REAL baseline captured from the game's first run on the Legion
    # (2026-08-31), plus one foreign entry standing in for a mod.io install
    # the plugin must never disturb.
    BASELINE = """<?xml version="1.0" encoding="UTF-8"?>
<save>
    <version major="4" minor="8" revision="0" build="700"/>
    <region id="ModuleSettings">
        <node id="root">
            <children>
                <node id="Mods">
                    <children>
                        <node id="ModuleShortDesc">
                            <attribute id="Folder" type="LSString" value="GustavX"/>
                            <attribute id="MD5" type="LSString" value=""/>
                            <attribute id="Name" type="LSString" value="GustavX"/>
                            <attribute id="PublishHandle" type="uint64" value="0"/>
                            <attribute id="UUID" type="guid" value="cb555efe-2d9e-131f-8195-a89329d218ea"/>
                            <attribute id="Version64" type="int64" value="36028797018963968"/>
                        </node>
                        <node id="ModuleShortDesc">
                            <attribute id="Folder" type="LSString" value="ModIoThing"/>
                            <attribute id="MD5" type="LSString" value=""/>
                            <attribute id="Name" type="LSString" value="Mod.io Thing"/>
                            <attribute id="PublishHandle" type="uint64" value="42"/>
                            <attribute id="UUID" type="guid" value="11111111-2222-3333-4444-555555555555"/>
                            <attribute id="Version64" type="int64" value="1"/>
                        </node>
                    </children>
                </node>
            </children>
        </node>
    </region>
</save>
"""

    @staticmethod
    def _lz4_store(data: bytes) -> bytes:
        """Literal-only LZ4 block: valid per spec (the last sequence ends
        after its literals), and enough to compress anything for a test."""
        out = bytearray()
        n = len(data)
        if n < 15:
            out.append(n << 4)
        else:
            out.append(0xF0)
            rest = n - 15
            while rest >= 255:
                out.append(255)
                rest -= 255
            out.append(rest)
        out += data
        return bytes(out)

    @classmethod
    def _make_pak(cls, uuid, name="Test Mod", folder="TestMod",
                  version="72198331526283346", with_meta=True,
                  compress_meta=False):
        """A minimal LSPK v18 pak: 40-byte header, member data, then the
        LZ4-compressed file table (272-byte entries)."""
        members = []
        if with_meta:
            meta = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<save><region id="Config"><node id="root"><children>'
                '<node id="ModuleInfo">'
                f'<attribute id="Folder" type="LSString" value="{folder}"/>'
                f'<attribute id="Name" type="LSString" value="{name}"/>'
                f'<attribute id="UUID" type="guid" value="{uuid}"/>'
                f'<attribute id="Version64" type="int64" value="{version}"/>'
                "</node></children></node></region></save>"
            ).encode()
            if compress_meta:
                members.append(
                    (f"Mods/{folder}/meta.lsx", cls._lz4_store(meta),
                     len(meta), 2)
                )
            else:
                members.append(
                    (f"Mods/{folder}/meta.lsx", meta, len(meta), 0)
                )
        members.append(("Public/Whatever/data.bin", b"payload", 7, 0))
        blobs, entries = b"", []
        offset = 40
        for mname, blob, unc, method in members:
            entries.append((mname, offset, method, len(blob), unc))
            blobs += blob
            offset += len(blob)
        table = b""
        for mname, off, method, disk, unc in entries:
            table += mname.encode().ljust(256, b"\0")
            table += (off & 0xFFFFFFFF).to_bytes(4, "little")
            table += (off >> 32).to_bytes(2, "little")
            table += bytes([0, method])
            table += disk.to_bytes(4, "little")
            table += unc.to_bytes(4, "little")
        ctable = cls._lz4_store(table)
        fl_off = 40 + len(blobs)
        header = (
            b"LSPK" + (18).to_bytes(4, "little")
            + fl_off.to_bytes(8, "little")
            + (8 + len(ctable)).to_bytes(4, "little")
            + bytes([0, 0]) + b"\0" * 16 + (1).to_bytes(2, "little")
        )
        assert len(header) == 40, len(header)
        return (
            header + blobs
            + len(entries).to_bytes(4, "little")
            + len(ctable).to_bytes(4, "little")
            + ctable
        )

    def setUp(self):
        if os.path.isfile(main.SETTINGS_PATH):
            os.remove(main.SETTINGS_PATH)
        self.install = os.path.join(main.STEAM_COMMON, self.GAME)
        shutil.rmtree(self.install, ignore_errors=True)
        os.makedirs(os.path.join(self.install, "bin"))
        os.makedirs(os.path.join(self.install, "Data"))
        # The guard reads the REAL /proc for a live bg3, so on a machine
        # where someone is actually playing, every install in this class
        # was refused and 29 tests failed - on Linux only, because Windows
        # never has bg3 running. A test must not depend on what the
        # machine happens to be doing; the guard has its own test that
        # stubs this True deliberately.
        self._real_running = main._bg3_running
        main._bg3_running = lambda: False
        self._real_root = main.BG3_PROFILE_ROOT
        main.BG3_PROFILE_ROOT = os.path.join(TEST_ROOT, "bg3-profile")
        shutil.rmtree(main.BG3_PROFILE_ROOT, ignore_errors=True)
        pub = os.path.join(main.BG3_PROFILE_ROOT, "PlayerProfiles", "Public")
        os.makedirs(pub)
        with open(os.path.join(pub, "modsettings.lsx"), "w") as f:
            f.write(self.BASELINE)
        settings = main._load_settings()
        settings["api_key"] = "k"
        main._save_settings(settings)
        os.makedirs(main.DOWNLOADS_DIR, exist_ok=True)
        shutil.rmtree(
            main._extract_scratch(self.MOD, self.FILE), ignore_errors=True
        )
        self.plugin = main.Plugin()

    def tearDown(self):
        shutil.rmtree(main.BG3_PROFILE_ROOT, ignore_errors=True)
        main.BG3_PROFILE_ROOT = self._real_root
        main._bg3_running = self._real_running
        shutil.rmtree(self.install, ignore_errors=True)

    def _archive(self, entries: dict):
        archive = main._archive_cache_path(self.MOD, self.FILE, "m.zip")
        with zipfile.ZipFile(archive, "w") as z:
            for rel, body in entries.items():
                z.writestr(rel, body)

    def _install(self, mod_name="Test Mod"):
        return run(self.plugin.install_mod(
            self.DOMAIN, self.MOD, self.FILE, "m.zip", mod_name, "1.0",
            self.GAME, "Data", "", "", "bg3", 1086940,
        ))

    def _uuids_in_modsettings(self):
        doc = main.xml_parse_file(main._bg3_modsettings_path())
        out = []
        for node in doc.iter("node"):
            if node.get("id") == "ModuleShortDesc":
                for a in node.findall("attribute"):
                    if a.get("id") == "UUID":
                        out.append(a.get("value"))
        return out

    # ---- the decompressor is held to real LZ4, not just our stored form
    def test_lz4_match_sequences_decode(self):
        # token 0x35: 3 literals then a 9-byte match at offset 3 -
        # overlapping copy, the case naive slicing gets wrong.
        block = bytes([0x35]) + b"abc" + (3).to_bytes(2, "little")
        self.assertEqual(
            main._lz4_block_decompress(block, 12), b"abcabcabcabc"
        )

    def test_lz4_literal_extension_decodes(self):
        data = bytes(range(256)) * 3
        self.assertEqual(
            main._lz4_block_decompress(self._lz4_store(data), len(data)),
            data,
        )

    def test_pak_meta_reads_from_synthetic_and_compressed(self):
        for compress in (False, True):
            pak = self._make_pak(
                "aaaa1111-0000-0000-0000-000000000001",
                compress_meta=compress,
            )
            path = os.path.join(TEST_ROOT, "probe.pak")
            with open(path, "wb") as f:
                f.write(pak)
            metas = main._lspk_pak_metas(path)
            self.assertEqual(len(metas), 1, f"compress={compress}")
            self.assertEqual(
                metas[0]["uuid"], "aaaa1111-0000-0000-0000-000000000001"
            )
            self.assertEqual(metas[0]["folder"], "TestMod")
            self.assertEqual(metas[0]["version64"], "72198331526283346")

    def test_install_registers_and_preserves_foreign_entries(self):
        self._archive({"TestMod.pak": self._make_pak(
            "aaaa1111-0000-0000-0000-000000000001")})
        r = self._install()
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(os.path.isfile(
            os.path.join(main._bg3_mods_dir(), "TestMod.pak")))
        uuids = self._uuids_in_modsettings()
        # GustavX FIRST - the game's own campaign must stay at the top -
        # the mod.io entry untouched, ours appended.
        self.assertEqual(uuids[0], "cb555efe-2d9e-131f-8195-a89329d218ea")
        self.assertIn("11111111-2222-3333-4444-555555555555", uuids)
        self.assertIn("aaaa1111-0000-0000-0000-000000000001", uuids)
        rec = main._load_settings()["installed"][self.DOMAIN]["Test Mod"]
        self.assertEqual(rec["mode"], "bg3")
        self.assertEqual(rec["files"], ["TestMod.pak"])

    def test_disable_pulls_the_pak_and_the_registration(self):
        self._archive({"TestMod.pak": self._make_pak(
            "aaaa1111-0000-0000-0000-000000000001")})
        self._install()
        r = run(self.plugin.set_mod_enabled(
            self.GAME, "Data", "Test Mod", False, "bg3", self.DOMAIN))
        self.assertTrue(r.get("ok"), r)
        self.assertFalse(os.path.isfile(
            os.path.join(main._bg3_mods_dir(), "TestMod.pak")))
        self.assertTrue(os.path.isfile(
            os.path.join(main._bg3_disabled_dir(), "TestMod.pak")))
        uuids = self._uuids_in_modsettings()
        self.assertNotIn("aaaa1111-0000-0000-0000-000000000001", uuids)
        self.assertIn("11111111-2222-3333-4444-555555555555", uuids)
        r = run(self.plugin.set_mod_enabled(
            self.GAME, "Data", "Test Mod", True, "bg3", self.DOMAIN))
        self.assertTrue(r.get("ok"), r)
        self.assertIn(
            "aaaa1111-0000-0000-0000-000000000001",
            self._uuids_in_modsettings(),
        )

    def test_uninstall_cleans_files_registration_and_record(self):
        self._archive({"TestMod.pak": self._make_pak(
            "aaaa1111-0000-0000-0000-000000000001")})
        self._install()
        r = run(self.plugin.uninstall_mod(
            self.DOMAIN, self.GAME, "Data", "Test Mod", "bg3"))
        self.assertTrue(r.get("ok"), r)
        self.assertFalse(os.path.isfile(
            os.path.join(main._bg3_mods_dir(), "TestMod.pak")))
        self.assertNotIn(
            "aaaa1111-0000-0000-0000-000000000001",
            self._uuids_in_modsettings(),
        )
        self.assertNotIn(
            "Test Mod",
            main._load_settings().get("installed", {}).get(self.DOMAIN, {}),
        )
        # The game's own entries survived every step of the round trip.
        self.assertEqual(
            self._uuids_in_modsettings(),
            [
                "cb555efe-2d9e-131f-8195-a89329d218ea",
                "11111111-2222-3333-4444-555555555555",
            ],
        )

    def test_without_first_launch_nothing_is_touched(self):
        os.remove(main._bg3_modsettings_path())
        self._archive({"TestMod.pak": self._make_pak(
            "aaaa1111-0000-0000-0000-000000000001")})
        r = self._install()
        self.assertFalse(r.get("ok"))
        self.assertIn("Launch", r.get("error", ""))
        self.assertFalse(os.path.isdir(main._bg3_mods_dir()))

    def test_a_pakless_archive_names_the_real_problem(self):
        self._archive({"DWrite.dll": b"MZwindows", "readme.txt": b"hi"})
        r = self._install("Some SE Mod")
        self.assertFalse(r.get("ok"))
        self.assertIn("Script Extender", r.get("error", ""))

    def test_an_override_pak_without_meta_still_installs(self):
        self._archive({"Override.pak": self._make_pak(
            "unused", with_meta=False)})
        r = self._install("Pure Override")
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(os.path.isfile(
            os.path.join(main._bg3_mods_dir(), "Override.pak")))
        # No registration to write, and the game's list is untouched.
        self.assertEqual(len(self._uuids_in_modsettings()), 2)

    def test_listing_reads_the_records(self):
        self._archive({"TestMod.pak": self._make_pak(
            "aaaa1111-0000-0000-0000-000000000001")})
        self._install()
        r = run(self.plugin.get_installed_mods(
            self.DOMAIN, self.GAME, "Data", "bg3", 1086940))
        self.assertTrue(r.get("ok"), r)
        mods = r["mods"]
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0]["name"], "Test Mod")
        self.assertTrue(mods[0]["enabled"])
        self.assertTrue(mods[0]["togglable"])

    # ---- Script Extender detection -------------------------------------
    # The native Linux build cannot run the extender, so an SE mod installs
    # perfectly and does nothing. Verified on device against the real "BG3
    # Essentials" collection (2026-08-31): the marker split those mods
    # cleanly, no false positive either way.
    #   marker present: Auto Wares - MCM, Database Cleaner, Records,
    #     Volition Cabinet, Tooltip Manager, Mod Configuration Menu
    #   marker absent:  Distinctive Dyes, Better Inventory UI, Better Hotbar 2

    @classmethod
    def _make_se_pak(cls, uuid, folder="SEMod"):
        """A pak carrying a ScriptExtender/Config.json, the way every SE mod
        checked on device does."""
        meta = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<save><region id="Config"><node id="root"><children>'
            '<node id="ModuleInfo">'
            f'<attribute id="Folder" type="LSString" value="{folder}"/>'
            f'<attribute id="Name" type="LSString" value="SE Mod"/>'
            f'<attribute id="UUID" type="guid" value="{uuid}"/>'
            '<attribute id="Version64" type="int64" value="1"/>'
            "</node></children></node></region></save>"
        ).encode()
        members = [
            (f"Mods/{folder}/meta.lsx", meta, len(meta), 0),
            (
                f"Mods/{folder}/ScriptExtender/Config.json",
                b'{"RequiredVersion":20}',
                22,
                0,
            ),
        ]
        blobs, entries = b"", []
        offset = 40
        for mname, blob, unc, method in members:
            entries.append((mname, offset, method, len(blob), unc))
            blobs += blob
            offset += len(blob)
        table = b""
        for mname, off, method, disk, unc in entries:
            table += mname.encode().ljust(256, b"\0")
            table += (off & 0xFFFFFFFF).to_bytes(4, "little")
            table += (off >> 32).to_bytes(2, "little")
            table += bytes([0, method])
            table += disk.to_bytes(4, "little")
            table += unc.to_bytes(4, "little")
        ctable = cls._lz4_store(table)
        fl_off = 40 + len(blobs)
        header = (
            b"LSPK" + (18).to_bytes(4, "little")
            + fl_off.to_bytes(8, "little")
            + (8 + len(ctable)).to_bytes(4, "little")
            + bytes([0, 0]) + b"\0" * 16 + (1).to_bytes(2, "little")
        )
        return (
            header + blobs
            + len(entries).to_bytes(4, "little")
            + len(ctable).to_bytes(4, "little")
            + ctable
        )

    def test_a_script_extender_pak_is_detected(self):
        path = os.path.join(TEST_ROOT, "se.pak")
        with open(path, "wb") as f:
            f.write(self._make_se_pak("bbbb2222-0000-0000-0000-000000000002"))
        self.assertTrue(main._pak_needs_script_extender(path))

    def test_a_plain_pak_is_not_flagged(self):
        path = os.path.join(TEST_ROOT, "plain.pak")
        with open(path, "wb") as f:
            f.write(self._make_pak("cccc3333-0000-0000-0000-000000000003"))
        self.assertFalse(main._pak_needs_script_extender(path))

    def test_a_broken_pak_is_not_flagged(self):
        """A warning we cannot justify is worse than none, and the install
        path reports real parse failures separately."""
        path = os.path.join(TEST_ROOT, "junk.pak")
        with open(path, "wb") as f:
            f.write(b"not a pak at all")
        self.assertFalse(main._pak_needs_script_extender(path))

    def test_an_se_mod_installs_but_carries_the_warning(self):
        self._archive({"SEMod.pak": self._make_se_pak(
            "bbbb2222-0000-0000-0000-000000000002")})
        r = self._install("An SE Mod")
        # Installed, not refused: the files are harmless and the author's
        # mod is not ours to veto. But it must SAY it will do nothing.
        self.assertTrue(r.get("ok"), r)
        self.assertIn("Script Extender", r.get("warning", ""))
        rec = main._load_settings()["installed"][self.DOMAIN]["An SE Mod"]
        # On the record, so it survives the toast and reappears in My Mods.
        self.assertIn("Script Extender", rec.get("warning", ""))
        self.assertIn("native Linux", rec.get("warning", ""))

    def test_a_plain_mod_carries_no_warning(self):
        self._archive({"TestMod.pak": self._make_pak(
            "aaaa1111-0000-0000-0000-000000000001")})
        r = self._install()
        self.assertTrue(r.get("ok"), r)
        self.assertNotIn("warning", r)
        rec = main._load_settings()["installed"][self.DOMAIN]["Test Mod"]
        self.assertNotIn("warning", rec)

    def test_collection_uninstall_removes_paks_and_registrations(self):
        """Without a bg3 branch this fell through to the folder logic, which
        looks for a DIRECTORY under the game's Data dir: nothing was deleted,
        yet the record was popped and counted as removed - leaving every pak
        installed and registered with nothing left to manage it by."""
        self._archive({"TestMod.pak": self._make_pak(
            "aaaa1111-0000-0000-0000-000000000001")})
        # Keywords, not positions: install_mod takes 30-odd parameters and
        # its own comments warn that they are passed positionally from the
        # frontend, so a miscount here would test the wrong thing quietly.
        r = run(self.plugin.install_mod(
            self.DOMAIN, self.MOD, self.FILE, "m.zip", "Coll Mod", "1.0",
            self.GAME, "Data",
            install_mode="bg3", app_id=1086940,
            record_source="collection", collection_slug="slug1",
        ))
        self.assertTrue(r.get("ok"), r)
        rec = main._load_settings()["installed"][self.DOMAIN]["Coll Mod"]
        self.assertEqual(rec.get("collection_slug"), "slug1")
        out = run(self.plugin.uninstall_collection(
            self.DOMAIN, self.GAME, "Data",
            install_mode="bg3", app_id=1086940, slug="slug1",
        ))
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("removed"), 1)
        self.assertFalse(os.path.isfile(
            os.path.join(main._bg3_mods_dir(), "TestMod.pak")))
        self.assertNotIn(
            "aaaa1111-0000-0000-0000-000000000001",
            self._uuids_in_modsettings(),
        )
        self.assertNotIn(
            "Coll Mod",
            main._load_settings().get("installed", {}).get(self.DOMAIN, {}),
        )
        # And the game's own entries are still there afterwards.
        self.assertEqual(len(self._uuids_in_modsettings()), 2)

    def test_two_files_from_one_mod_page_share_one_record(self):
        """Collections pin several FILES from one page (Better Hotbar 2
        ships three variants). Replacing the record orphaned the earlier
        pak and its registration: the DIQ collection's 951 installs left
        829 records, and its uninstall left 125 entries and a pak nothing
        owned. One record must own every file installed from a page."""
        self._archive({"VariantA.pak": self._make_pak(
            "aaaa1111-0000-0000-0000-000000000001", folder="VarA")})
        self.assertTrue(self._install("Multi File Mod").get("ok"))
        self._archive({"VariantB.pak": self._make_pak(
            "dddd4444-0000-0000-0000-000000000004", folder="VarB")})
        self.assertTrue(self._install("Multi File Mod").get("ok"))
        rec = main._load_settings()["installed"][self.DOMAIN][
            "Multi File Mod"
        ]
        self.assertEqual(
            sorted(rec["files"]), ["VariantA.pak", "VariantB.pak"]
        )
        uuids = self._uuids_in_modsettings()
        self.assertIn("aaaa1111-0000-0000-0000-000000000001", uuids)
        self.assertIn("dddd4444-0000-0000-0000-000000000004", uuids)
        # And the uninstall takes BOTH paks and BOTH registrations with it.
        r = run(self.plugin.uninstall_mod(
            self.DOMAIN, self.GAME, "Data", "Multi File Mod", "bg3"))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(os.listdir(main._bg3_mods_dir()), [])
        self.assertEqual(len(self._uuids_in_modsettings()), 2)

    def test_reinstalling_the_same_file_does_not_duplicate(self):
        self._archive({"TestMod.pak": self._make_pak(
            "aaaa1111-0000-0000-0000-000000000001")})
        self.assertTrue(self._install().get("ok"))
        self._archive({"TestMod.pak": self._make_pak(
            "aaaa1111-0000-0000-0000-000000000001")})
        self.assertTrue(self._install().get("ok"))
        rec = main._load_settings()["installed"][self.DOMAIN]["Test Mod"]
        self.assertEqual(rec["files"], ["TestMod.pak"])
        uuids = self._uuids_in_modsettings()
        self.assertEqual(
            uuids.count("aaaa1111-0000-0000-0000-000000000001"), 1
        )

    def test_a_loose_file_mod_installs_into_data(self):
        """The first collection run refused 49 of these as 'needs the
        Script Extender'. They are Generated/ trees of textures - verified
        against four real archives - and they belong under the game's
        Data dir with a per-file record."""
        self._archive({
            "Generated/Public/Shared/Assets/skin.DDS": b"x",
            "Generated/Public/SharedDev/Assets/head.GR2": b"y",
            "readme.txt": b"notes",
        })
        r = self._install("Vivid Something")
        self.assertTrue(r.get("ok"), r)
        data = os.path.join(self.install, "Data")
        self.assertTrue(os.path.isfile(os.path.join(
            data, "Generated", "Public", "Shared", "Assets", "skin.DDS")))
        rec = main._load_settings()["installed"][self.DOMAIN][
            "Vivid Something"
        ]
        self.assertEqual(rec["mode"], "files")
        self.assertEqual(rec["target"], "Data")
        self.assertEqual(len(rec["files"]), 2)
        # The readme was NOT dumped into the game.
        self.assertFalse(os.path.isfile(os.path.join(data, "readme.txt")))
        # And modsettings was not touched: loose files have no registration.
        self.assertEqual(len(self._uuids_in_modsettings()), 2)

    def test_a_wrapped_loose_mod_finds_its_tree(self):
        # Shadowheart Hair Tweak ships "Shadowheart Hair Tweak/Generated/..."
        self._archive({
            "Wrap Folder/Generated/Public/Shared/Assets/hair.gr2": b"x",
        })
        r = self._install("Wrapped Loose")
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(os.path.isfile(os.path.join(
            self.install, "Data", "Generated", "Public", "Shared",
            "Assets", "hair.gr2")))

    def test_loose_uninstall_removes_files_and_prunes_dirs(self):
        self._archive({
            "Generated/Public/Shared/Assets/skin.DDS": b"x",
        })
        self.assertTrue(self._install("Vivid Something").get("ok"))
        r = run(self.plugin.uninstall_mod(
            self.DOMAIN, self.GAME, "Data", "Vivid Something", "bg3"))
        self.assertTrue(r.get("ok"), r)
        data = os.path.join(self.install, "Data")
        self.assertFalse(os.path.exists(
            os.path.join(data, "Generated")))
        self.assertNotIn(
            "Vivid Something",
            main._load_settings().get("installed", {}).get(self.DOMAIN, {}),
        )

    def test_loose_mods_list_and_refuse_to_toggle_with_a_reason(self):
        self._archive({
            "Generated/Public/Shared/Assets/skin.DDS": b"x",
        })
        self.assertTrue(self._install("Vivid Something").get("ok"))
        r = run(self.plugin.get_installed_mods(
            self.DOMAIN, self.GAME, "Data", "bg3", 1086940))
        mods = {m["name"]: m for m in r["mods"]}
        self.assertIn("Vivid Something", mods)
        self.assertFalse(mods["Vivid Something"]["togglable"])
        t = run(self.plugin.set_mod_enabled(
            self.GAME, "Data", "Vivid Something", False, "bg3", self.DOMAIN))
        self.assertFalse(t.get("ok"))
        self.assertIn("uninstall", t.get("error", ""))

    def test_reset_removes_loose_mods_too(self):
        self._archive({
            "Generated/Public/Shared/Assets/skin.DDS": b"x",
        })
        self.assertTrue(self._install("Vivid Something").get("ok"))
        r = run(self.plugin.uninstall_all_mods(
            self.DOMAIN, self.GAME, "Data", [], "bg3", 1086940))
        self.assertTrue(r.get("ok"), r)
        self.assertFalse(os.path.exists(
            os.path.join(self.install, "Data", "Generated")))

    def test_a_mixed_archive_installs_both_and_uninstalls_both(self):
        """Rare shape: a pak plus loose texture files in one archive."""
        self._archive({
            "TestMod.pak": self._make_pak(
                "aaaa1111-0000-0000-0000-000000000001"),
            "Generated/Public/Shared/Assets/extra.DDS": b"x",
        })
        r = self._install("Mixed Mod")
        self.assertTrue(r.get("ok"), r)
        rec = main._load_settings()["installed"][self.DOMAIN]["Mixed Mod"]
        self.assertEqual(rec["mode"], "bg3")
        self.assertEqual(rec["loose_files"],
                         ["Generated/Public/Shared/Assets/extra.DDS"])
        self.assertTrue(os.path.isfile(os.path.join(
            self.install, "Data", "Generated", "Public", "Shared",
            "Assets", "extra.DDS")))
        r = run(self.plugin.uninstall_mod(
            self.DOMAIN, self.GAME, "Data", "Mixed Mod", "bg3"))
        self.assertTrue(r.get("ok"), r)
        self.assertFalse(os.path.isfile(os.path.join(
            self.install, "Data", "Generated", "Public", "Shared",
            "Assets", "extra.DDS")))
        self.assertEqual(os.listdir(main._bg3_mods_dir()), [])

    def test_a_dll_only_archive_still_names_the_extender(self):
        self._archive({"DWrite.dll": b"MZwindows"})
        r = self._install("The Extender Itself")
        self.assertFalse(r.get("ok"))
        self.assertIn("Script Extender", r.get("error", ""))

    # ---- zstd-compressed meta (LSPK method 3) ---------------------------
    def test_zstd_meta_registers_via_the_decompressor(self):
        # Integration: method 3 must route through _zstd_decompress. The
        # real decompression is covered by the next test where a zstd
        # backend exists (always true on the device, where it matters).
        meta = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<save><region id="Config"><node id="root"><children>'
            '<node id="ModuleInfo">'
            '<attribute id="Folder" type="LSString" value="ZMod"/>'
            '<attribute id="Name" type="LSString" value="Z Mod"/>'
            '<attribute id="UUID" type="guid" '
            'value="eeee5555-0000-0000-0000-000000000005"/>'
            '<attribute id="Version64" type="int64" value="1"/>'
            "</node></children></node></region></save>"
        ).encode()
        pak = self._make_pak("ignored")  # rebuilt below with method 3
        # Build a pak whose meta entry claims method 3 with fake bytes,
        # and stub the decompressor to return the real meta.
        members = [("Mods/ZMod/meta.lsx", b"ZSTDBYTES", len(meta), 3)]
        blobs, entries = b"", []
        offset = 40
        for mname, blob, unc, method in members:
            entries.append((mname, offset, method, len(blob), unc))
            blobs += blob
            offset += len(blob)
        table = b""
        for mname, off, method, disk, unc in entries:
            table += mname.encode().ljust(256, b"\0")
            table += (off & 0xFFFFFFFF).to_bytes(4, "little")
            table += (off >> 32).to_bytes(2, "little")
            table += bytes([0, method])
            table += disk.to_bytes(4, "little")
            table += unc.to_bytes(4, "little")
        ctable = self._lz4_store(table)
        pak = (
            b"LSPK" + (18).to_bytes(4, "little")
            + (40 + len(blobs)).to_bytes(8, "little")
            + (8 + len(ctable)).to_bytes(4, "little")
            + bytes([0, 0]) + b"\0" * 16 + (1).to_bytes(2, "little")
            + blobs
            + len(entries).to_bytes(4, "little")
            + len(ctable).to_bytes(4, "little") + ctable
        )
        path = os.path.join(TEST_ROOT, "z.pak")
        with open(path, "wb") as f:
            f.write(pak)
        real = main._zstd_decompress
        calls = []

        def fake(blob, out_size=0):
            calls.append(blob)
            return meta

        main._zstd_decompress = fake
        try:
            metas = main._lspk_pak_metas(path)
        finally:
            main._zstd_decompress = real
        self.assertEqual(calls, [b"ZSTDBYTES"])
        self.assertEqual(
            metas[0]["uuid"], "eeee5555-0000-0000-0000-000000000005"
        )

    def test_zstd_roundtrip_where_a_backend_exists(self):
        """Real decompression. Runs wherever zstd exists - which includes
        the Legion, where every test run happens before a deploy ships."""
        import shutil as _sh
        payload = b"the same bytes " * 100
        blob = None
        try:
            import zstandard as _z
            blob = _z.ZstdCompressor().compress(payload)
        except ImportError:
            exe = _sh.which("zstd")
            if exe:
                import subprocess
                p = subprocess.run(
                    [exe, "--stdout"], input=payload, capture_output=True
                )
                if p.returncode == 0:
                    blob = p.stdout
        if blob is None:
            self.skipTest("no zstd backend on this machine")
        self.assertEqual(
            main._zstd_decompress(blob, len(payload)), payload
        )

    def test_listings_carry_the_warning(self):
        """My Mods has rendered mod.warning for weeks; the bg3 listing
        never sent the field, so Michael went looking for the Script
        Extender warning and could not find it."""
        self._archive({"SEMod.pak": self._make_se_pak(
            "bbbb2222-0000-0000-0000-000000000002")})
        self.assertTrue(self._install("An SE Mod").get("ok"))
        r = run(self.plugin.get_installed_mods(
            self.DOMAIN, self.GAME, "Mods", "bg3", 1086940))
        mods = {m["name"]: m for m in r["mods"]}
        self.assertIn("Script Extender", mods["An SE Mod"].get("warning", ""))

    def test_a_collection_se_mod_installs_switched_off(self):
        """Michael: "I would all collections to install but the broken/not
        supported ones just skip/disable by default - we are aiming for a
        console-like audience". An SE mod can never run on the native
        build, so from a collection it arrives OFF: pak parked in
        Mods-disabled, no registration, warning on the record."""
        self._archive({"SEMod.pak": self._make_se_pak(
            "bbbb2222-0000-0000-0000-000000000002")})
        r = run(self.plugin.install_mod(
            self.DOMAIN, self.MOD, self.FILE, "m.zip", "Coll SE Mod", "1.0",
            self.GAME, "Mods",
            install_mode="bg3", app_id=1086940,
            record_source="collection", collection_slug="slugse",
        ))
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r.get("disabled"))
        self.assertFalse(os.path.isfile(
            os.path.join(main._bg3_mods_dir(), "SEMod.pak")))
        self.assertTrue(os.path.isfile(
            os.path.join(main._bg3_disabled_dir(), "SEMod.pak")))
        self.assertNotIn(
            "bbbb2222-0000-0000-0000-000000000002",
            self._uuids_in_modsettings(),
        )
        rec = main._load_settings()["installed"][self.DOMAIN]["Coll SE Mod"]
        self.assertFalse(rec["enabled"])
        self.assertIn("Script Extender", rec.get("warning", ""))
        # The listing shows it off, with the reason, and still togglable -
        # switching it back on is the same one tap every auto-off mod has.
        lst = run(self.plugin.get_installed_mods(
            self.DOMAIN, self.GAME, "Mods", "bg3", 1086940))
        row = {m["name"]: m for m in lst["mods"]}["Coll SE Mod"]
        self.assertFalse(row["enabled"])
        self.assertTrue(row["togglable"])
        self.assertIn("Script Extender", row.get("warning", ""))

    def test_a_direct_se_install_keeps_the_users_choice(self):
        """The person read the page and chose it: installed ENABLED, with
        the warning. A registered pak whose scripts never run is harmless,
        and the author's mod is not ours to veto."""
        self._archive({"SEMod.pak": self._make_se_pak(
            "bbbb2222-0000-0000-0000-000000000002")})
        r = self._install("An SE Mod")
        self.assertTrue(r.get("ok"), r)
        self.assertNotIn("disabled", r)
        self.assertTrue(os.path.isfile(
            os.path.join(main._bg3_mods_dir(), "SEMod.pak")))
        self.assertIn(
            "bbbb2222-0000-0000-0000-000000000002",
            self._uuids_in_modsettings(),
        )

    def test_this_class_does_not_depend_on_a_live_game(self):
        """29 tests here failed on the Legion and passed on Windows, purely
        because someone was playing BG3 at the time: _bg3_running reads the
        real /proc, so the guard refused every test install. A suite whose
        result depends on what the machine is doing is not a suite."""
        self.assertIs(
            main._bg3_running(), False,
            "setUp must stub _bg3_running so ambient state cannot decide",
        )
        with open(os.path.join(REPO_ROOT, "tests", "test_backend.py"),
                  encoding="utf-8") as f:
            src = f.read()
        i = src.index("class TestBg3Mode")
        setup = src[i : src.index("def tearDown", i)]
        self.assertIn("_bg3_running = lambda: False", setup)

    def test_every_mutation_refuses_while_the_game_runs(self):
        """Moving paks under a loading game hung it at 94% on device
        (2026-09-01) - and the mover was the plugin's own maintainer, so a
        human warning is not enough. Every bg3 mutation path checks."""
        self._archive({"TestMod.pak": self._make_pak(
            "aaaa1111-0000-0000-0000-000000000001")})
        self.assertTrue(self._install().get("ok"))
        real = main._bg3_running
        main._bg3_running = lambda: True
        try:
            self._archive({"TestMod.pak": self._make_pak(
                "aaaa1111-0000-0000-0000-000000000001")})
            r = self._install("Another Mod")
            self.assertFalse(r.get("ok"))
            self.assertIn("running", r.get("error", ""))
            r = run(self.plugin.set_mod_enabled(
                self.GAME, "Mods", "Test Mod", False, "bg3", self.DOMAIN))
            self.assertFalse(r.get("ok"))
            r = run(self.plugin.uninstall_mod(
                self.DOMAIN, self.GAME, "Mods", "Test Mod", "bg3"))
            self.assertFalse(r.get("ok"))
            r = run(self.plugin.uninstall_all_mods(
                self.DOMAIN, self.GAME, "Mods", [], "bg3", 1086940))
            self.assertFalse(r.get("ok"))
            r = run(self.plugin.uninstall_collection(
                self.DOMAIN, self.GAME, "Mods",
                install_mode="bg3", app_id=1086940, slug="any",
            ))
            self.assertFalse(r.get("ok"))
        finally:
            main._bg3_running = real
        # And with the game closed, the same calls work again.
        r = run(self.plugin.set_mod_enabled(
            self.GAME, "Mods", "Test Mod", False, "bg3", self.DOMAIN))
        self.assertTrue(r.get("ok"), r)

    # ---- broken-dependency pass -----------------------------------------
    # Goon+ shipped three NPC redesigns depending on Witcher gear paks the
    # curator never included, and a new game hung at 80% (device,
    # 2026-09-01). Paks declare their dependencies in meta.lsx; anything
    # whose dependency is neither registered nor a Larian engine module
    # goes off, with the reason.

    @classmethod
    def _make_dep_pak(cls, uuid, folder, dep_uuid=None, dep_name=""):
        dep_xml = ""
        if dep_uuid:
            dep_xml = (
                '<node id="Dependencies"><children>'
                '<node id="ModuleShortDesc">'
                f'<attribute id="UUID" type="guid" value="{dep_uuid}"/>'
                f'<attribute id="Name" type="LSString" value="{dep_name}"/>'
                "</node></children></node>"
            )
        meta = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<save><region id="Config"><node id="root"><children>'
            + dep_xml +
            '<node id="ModuleInfo">'
            f'<attribute id="Folder" type="LSString" value="{folder}"/>'
            f'<attribute id="Name" type="LSString" value="{folder}"/>'
            f'<attribute id="UUID" type="guid" value="{uuid}"/>'
            '<attribute id="Version64" type="int64" value="1"/>'
            "</node></children></node></region></save>"
        ).encode()
        members = [(f"Mods/{folder}/meta.lsx", meta, len(meta), 0)]
        blobs, entries = b"", []
        offset = 40
        for mname, blob, unc, method in members:
            entries.append((mname, offset, method, len(blob), unc))
            blobs += blob
            offset += len(blob)
        table = b""
        for mname, off, method, disk, unc in entries:
            table += mname.encode().ljust(256, b"\0")
            table += (off & 0xFFFFFFFF).to_bytes(4, "little")
            table += (off >> 32).to_bytes(2, "little")
            table += bytes([0, method])
            table += disk.to_bytes(4, "little")
            table += unc.to_bytes(4, "little")
        ctable = cls._lz4_store(table)
        return (
            b"LSPK" + (18).to_bytes(4, "little")
            + (40 + len(blobs)).to_bytes(8, "little")
            + (8 + len(ctable)).to_bytes(4, "little")
            + bytes([0, 0]) + b"\0" * 16 + (1).to_bytes(2, "little")
            + blobs
            + len(entries).to_bytes(4, "little")
            + len(ctable).to_bytes(4, "little") + ctable
        )

    def test_a_mod_with_a_missing_dependency_is_switched_off(self):
        self._archive({"Broken.pak": self._make_dep_pak(
            "aaaa1111-0000-0000-0000-00000000000a", "BrokenMod",
            dep_uuid="9999aaaa-0000-0000-0000-000000000099",
            dep_name="Witcher Gear Pack")})
        self.assertTrue(self._install("Broken Mod").get("ok"))
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertTrue(r.get("ok"), r)
        names = [d["name"] for d in r["disabled"]]
        self.assertEqual(names, ["Broken Mod"])
        self.assertIn("Witcher Gear Pack", r["disabled"][0]["reason"])
        rec = main._load_settings()["installed"][self.DOMAIN]["Broken Mod"]
        self.assertFalse(rec["enabled"])
        self.assertIn("Witcher Gear Pack", rec.get("warning", ""))
        self.assertTrue(os.path.isfile(
            os.path.join(main._bg3_disabled_dir(), "Broken.pak")))
        self.assertNotIn(
            "aaaa1111-0000-0000-0000-00000000000a",
            self._uuids_in_modsettings(),
        )

    def test_a_dependency_satisfied_by_another_mod_stays_on(self):
        self._archive({"Provider.pak": self._make_dep_pak(
            "bbbb2222-0000-0000-0000-00000000000b", "ProviderMod")})
        self.assertTrue(self._install("Provider").get("ok"))
        self._archive({"Consumer.pak": self._make_dep_pak(
            "cccc3333-0000-0000-0000-00000000000c", "ConsumerMod",
            dep_uuid="bbbb2222-0000-0000-0000-00000000000b",
            dep_name="ProviderMod")})
        self.assertTrue(self._install("Consumer").get("ok"))
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["disabled"], [])

    def test_a_dependency_on_an_engine_module_is_fine(self):
        self._archive({"UsesEngine.pak": self._make_dep_pak(
            "dddd4444-0000-0000-0000-00000000000d", "UsesEngine",
            dep_uuid="cb555efe-2d9e-131f-8195-a89329d218ea",
            dep_name="GustavX")})
        self.assertTrue(self._install("Uses Engine").get("ok"))
        # And one by NAME only, unregistered uuid (DiceSet_06 etc).
        self._archive({"UsesDice.pak": self._make_dep_pak(
            "eeee5555-0000-0000-0000-00000000000e", "UsesDice",
            dep_uuid="12345678-0000-0000-0000-0000000000ff",
            dep_name="DiceSet_06")})
        self.assertTrue(self._install("Uses Dice").get("ok"))
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["disabled"], [])

    def test_disabling_a_provider_cascades_to_its_dependents(self):
        # A needs a missing pak; B needs A. Both must fall, in one pass.
        self._archive({"A.pak": self._make_dep_pak(
            "aaaa6666-0000-0000-0000-00000000006a", "ModA",
            dep_uuid="9999aaaa-0000-0000-0000-000000000099",
            dep_name="Missing Thing")})
        self.assertTrue(self._install("Mod A").get("ok"))
        self._archive({"B.pak": self._make_dep_pak(
            "bbbb7777-0000-0000-0000-00000000007b", "ModB",
            dep_uuid="aaaa6666-0000-0000-0000-00000000006a",
            dep_name="ModA")})
        self.assertTrue(self._install("Mod B").get("ok"))
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(
            sorted(d["name"] for d in r["disabled"]), ["Mod A", "Mod B"]
        )

    def test_the_pass_refuses_while_the_game_runs(self):
        real = main._bg3_running
        main._bg3_running = lambda: True
        try:
            r = run(self.plugin.bg3_disable_broken_deps(
                self.DOMAIN, self.GAME))
            self.assertFalse(r.get("ok"))
            self.assertIn("running", r.get("error", ""))
        finally:
            main._bg3_running = real

    # ---- stats: the rule that was withdrawn -------------------------------
    # v1.5.2 switched off any mod whose stat entries inherited from a name
    # nobody defined (Tasha's Cauldron: `new entry "TW_Dye_Consort"` then
    # `using "TW_Dye_Consort"`). That verdict came from reading the file,
    # never from a boot, and it was wrong: the pattern is the ordinary
    # override idiom, and on 2026-09-02 an A/B boot with all nine parked
    # mods back on settled at the press-any-key screen in 50s, like the
    # control. Twenty-four false positives, no true one. Stats files no
    # longer decide anything; the pass only heals what the rule parked.

    @classmethod
    def _make_stats_pak(cls, uuid, folder, stats_text):
        meta = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<save><region id="Config"><node id="root"><children>'
            '<node id="ModuleInfo">'
            f'<attribute id="Folder" type="LSString" value="{folder}"/>'
            f'<attribute id="Name" type="LSString" value="{folder}"/>'
            f'<attribute id="UUID" type="guid" value="{uuid}"/>'
            '<attribute id="Version64" type="int64" value="1"/>'
            "</node></children></node></region></save>"
        ).encode()
        stats = stats_text.encode()
        members = [
            (f"Mods/{folder}/meta.lsx", meta, len(meta), 0),
            (f"Public/{folder}/Stats/Generated/Data/Object.txt",
             stats, len(stats), 0),
        ]
        blobs, entries = b"", []
        offset = 40
        for mname, blob, unc, method in members:
            entries.append((mname, offset, method, len(blob), unc))
            blobs += blob
            offset += len(blob)
        table = b""
        for mname, off, method, disk, unc in entries:
            table += mname.encode().ljust(256, b"\0")
            table += (off & 0xFFFFFFFF).to_bytes(4, "little")
            table += (off >> 32).to_bytes(2, "little")
            table += bytes([0, method])
            table += disk.to_bytes(4, "little")
            table += unc.to_bytes(4, "little")
        ctable = cls._lz4_store(table)
        return (
            b"LSPK" + (18).to_bytes(4, "little")
            + (40 + len(blobs)).to_bytes(8, "little")
            + (8 + len(ctable)).to_bytes(4, "little")
            + bytes([0, 0]) + b"\0" * 16 + (1).to_bytes(2, "little")
            + blobs
            + len(entries).to_bytes(4, "little")
            + len(ctable).to_bytes(4, "little") + ctable
        )

    # Verbatim shape of the real defect.
    SELF_REF_STATS = (
        'new entry "TW_Dye_Consort"\n'
        'type "Object"\n'
        'using "TW_Dye_Consort"\n'
        'data "Rarity" "Legendary"\n'
    )
    HEALTHY_STATS = (
        'new entry "MY_Dye_Base"\n'
        'type "Object"\n'
        'data "Rarity" "Common"\n'
        '\n'
        'new entry "MY_Dye_Child"\n'
        'type "Object"\n'
        'using "MY_Dye_Base"\n'
    )
    def test_a_self_inheriting_mod_is_left_switched_on(self):
        """Measured 2026-09-02: nine mods parked for exactly this shape
        booted together to the press-any-key screen in 50s. The pass must
        not touch them, whatever their stats files say."""
        self._archive({"SelfRef.pak": self._make_stats_pak(
            "aaaa1111-0000-0000-0000-0000000000f1", "TW_Outfits",
            self.SELF_REF_STATS)})
        self.assertTrue(self._install("Tashas Cauldron of Outfits").get("ok"))
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["disabled"], [], r)
        rec = main._load_settings()["installed"][self.DOMAIN][
            "Tashas Cauldron of Outfits"]
        self.assertTrue(rec.get("enabled", True))
        self.assertNotIn("warning", rec)
        self.assertTrue(os.path.isfile(
            os.path.join(main._bg3_mods_dir(), "SelfRef.pak")))
        self.assertIn(
            "aaaa1111-0000-0000-0000-0000000000f1",
            self._uuids_in_modsettings(),
        )

    def test_the_health_pass_leaves_healthy_mods_alone(self):
        self._archive({"Fine.pak": self._make_stats_pak(
            "aaaa1111-0000-0000-0000-0000000000f2", "Fine",
            self.HEALTHY_STATS)})
        self.assertTrue(self._install("A Fine Mod").get("ok"))
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["disabled"], [])

    def test_paks_are_read_by_seeking_not_slurped(self):
        """A collection's mod folder ran to 31GB on device. A health check
        that read each pak whole would be unusable, so the reader seeks -
        and this holds it to that by handing it a pak with a huge declared
        body it must never read."""
        path = os.path.join(TEST_ROOT, "seek.pak")
        with open(path, "wb") as f:
            f.write(self._make_stats_pak(
                "aaaa1111-0000-0000-0000-0000000000f5", "Seeky",
                self.HEALTHY_STATS))
        f2, entries = main._lspk_open(path)
        try:
            self.assertTrue(any(
                e[0].endswith("meta.lsx") for e in entries))
            # The handle is open and positioned lazily: nothing was
            # decompressed just to list the entries.
            self.assertFalse(f2.closed)
        finally:
            f2.close()

    def test_every_removal_path_clears_bg3_mods(self):
        """Four separate endpoints can remove mods, and bg3 needed a branch
        in each. They were fixed ONE AT A TIME as each bit: uninstall_mod,
        then uninstall_collection, then uninstall_all_mods, and finally
        reset_game_modding - which was missed until a reset before a small
        collection left the previous collection's 97 paks on disk AND
        registered, so a "31-mod cosmetic collection" booted 128 mods and
        hung at 80%.

        A bg3 record is FILES in the Larian profile, not a folder under the
        game, so the generic folder fallback deletes nothing while still
        dropping the record: it looks like it worked. This test drives all
        four for real rather than trusting that each was remembered.
        """
        paths = []

        def fresh(name, uuid, slug=""):
            self._archive({f"{name}.pak": self._make_pak(uuid, folder=name)})
            r = run(self.plugin.install_mod(
                self.DOMAIN, self.MOD, self.FILE, "m.zip", name, "1.0",
                self.GAME, "Mods",
                install_mode="bg3", app_id=1086940,
                record_source="collection" if slug else "",
                collection_slug=slug,
            ))
            self.assertTrue(r.get("ok"), r)
            p = os.path.join(main._bg3_mods_dir(), f"{name}.pak")
            self.assertTrue(os.path.isfile(p), f"{name} did not install")
            paths.append(p)
            return p

        def assert_gone(p, endpoint):
            self.assertFalse(
                os.path.isfile(p),
                f"{endpoint} left {os.path.basename(p)} on disk",
            )
            left = main._load_settings().get("installed", {}).get(
                self.DOMAIN, {})
            self.assertEqual(
                [k for k, v in left.items() if v.get("mode") == "bg3"], [],
                f"{endpoint} left a bg3 record behind",
            )
            # And nothing of ours may survive in the game's mod list.
            self.assertEqual(
                self._uuids_in_modsettings(),
                [
                    "cb555efe-2d9e-131f-8195-a89329d218ea",
                    "11111111-2222-3333-4444-555555555555",
                ],
                f"{endpoint} left a registration in modsettings.lsx",
            )

        # 1. uninstall_mod
        p = fresh("PathOne", "aaaa0001-0000-0000-0000-000000000001")
        r = run(self.plugin.uninstall_mod(
            self.DOMAIN, self.GAME, "Mods", "PathOne", "bg3"))
        self.assertTrue(r.get("ok"), r)
        assert_gone(p, "uninstall_mod")

        # 2. uninstall_collection
        p = fresh("PathTwo", "aaaa0002-0000-0000-0000-000000000002", "slugX")
        r = run(self.plugin.uninstall_collection(
            self.DOMAIN, self.GAME, "Mods",
            install_mode="bg3", app_id=1086940, slug="slugX"))
        self.assertTrue(r.get("ok"), r)
        assert_gone(p, "uninstall_collection")

        # 3. uninstall_all_mods (My Mods "remove everything")
        p = fresh("PathThree", "aaaa0003-0000-0000-0000-000000000003")
        r = run(self.plugin.uninstall_all_mods(
            self.DOMAIN, self.GAME, "Mods", [], "bg3", 1086940))
        self.assertTrue(r.get("ok"), r)
        assert_gone(p, "uninstall_all_mods")

        # 4. reset_game_modding (the one that was missed)
        p = fresh("PathFour", "aaaa0004-0000-0000-0000-000000000004")
        r = run(self.plugin.reset_game_modding(
            self.DOMAIN, self.GAME, "Mods", "bg3", 1086940))
        self.assertTrue(r.get("ok"), r)
        assert_gone(p, "reset_game_modding")

    def test_reset_also_clears_loose_files_and_disabled_paks(self):
        """Reset must sweep what a disable parked and what a loose-file mod
        merged into Data/, not only what is currently active."""
        # A parked pak.
        self._archive({"Parked.pak": self._make_pak(
            "aaaa0005-0000-0000-0000-000000000005", folder="Parked")})
        self.assertTrue(self._install("Parked Mod").get("ok"))
        run(self.plugin.set_mod_enabled(
            self.GAME, "Mods", "Parked Mod", False, "bg3", self.DOMAIN))
        parked = os.path.join(main._bg3_disabled_dir(), "Parked.pak")
        self.assertTrue(os.path.isfile(parked))
        # A loose-file mod.
        self._archive({"Generated/Public/Shared/Assets/t.DDS": b"x"})
        self.assertTrue(self._install("Loose Mod").get("ok"))
        loose = os.path.join(
            self.install, "Data", "Generated", "Public", "Shared",
            "Assets", "t.DDS")
        self.assertTrue(os.path.isfile(loose))

        r = run(self.plugin.reset_game_modding(
            self.DOMAIN, self.GAME, "Mods", "bg3", 1086940))
        self.assertTrue(r.get("ok"), r)
        self.assertFalse(os.path.isfile(parked), "reset left a parked pak")
        self.assertFalse(os.path.isfile(loose), "reset left loose files")
        self.assertEqual(
            [k for k, v in main._load_settings().get("installed", {})
             .get(self.DOMAIN, {}).items()], [],
            "reset left records behind",
        )

    # ---- the divider bug: '>' inside an attribute value -------------------
    def test_xml_attribute_values_may_contain_greater_than(self):
        """Load Order Tabs names its modules '--------------------------->
        Accesories'. The tokenizer ended the tag at that first '>', every
        later attribute nested under the wrong node, the UUID was never
        seen, and 37 modules went unregistered - which is why the game's
        own menu had to be used to enable them."""
        doc = main.xml_parse(
            '<save><node id="ModuleInfo">'
            '<attribute id="Name" type="FixedString" '
            'value="---------------------------&gt;  Accesories"/>'
            '<attribute id="Name2" type="FixedString" '
            'value="--------------------------->  Accesories"/>'
            '<attribute id="UUID" type="FixedString" '
            'value="81cca0ce-a866-4731-8da2-486f3a2ce52d"/>'
            "</node></save>"
        )
        node = doc.find("node")
        attrs = {a.get("id"): a.get("value") for a in node.findall("attribute")}
        self.assertEqual(attrs.get("Name2"), "--------------------------->  Accesories")
        self.assertEqual(attrs.get("UUID"), "81cca0ce-a866-4731-8da2-486f3a2ce52d")

    def test_a_divider_pak_registers(self):
        pak = self._make_pak(
            "81cca0ce-a866-4731-8da2-486f3a2ce52d",
            name="--------------------------->             Accesories",
            folder="Plus Divisions - Accesories",
        )
        path = os.path.join(TEST_ROOT, "divider.pak")
        with open(path, "wb") as f:
            f.write(pak)
        metas = main._lspk_pak_metas(path)
        self.assertEqual(len(metas), 1, "the arrow in the name must not hide the UUID")
        self.assertEqual(metas[0]["uuid"], "81cca0ce-a866-4731-8da2-486f3a2ce52d")

    # ---- the Gustav guard ----------------------------------------------------
    def test_the_base_game_pak_is_plausible(self):
        # Gustav.pak, read off the device: 145,832 files, a 4,862,848-byte
        # compressed table at offset 13,181,890,472 in a 12,575MB file.
        self.assertTrue(main._lspk_table_plausible(
            145832, 4862848, 13181890472, 12575 * 1048576 + 5_000_000))

    def test_a_table_past_the_end_of_the_file_is_not(self):
        self.assertFalse(main._lspk_table_plausible(100, 5000, 40, 1000))
        self.assertFalse(main._lspk_table_plausible(0, 100, 40, 10_000))
        self.assertFalse(main._lspk_table_plausible(10, 0, 40, 10_000))

    # ---- LZ4 fast path -------------------------------------------------------
    def test_lz4_non_overlapping_match_copies_correctly(self):
        # 8 literals, then a match of 8 at offset 8: no overlap, one slice.
        block = bytes([0x84]) + b"abcdefgh" + (8).to_bytes(2, "little")
        self.assertEqual(
            main._lz4_block_decompress(block, 16), b"abcdefghabcdefgh")

    # ---- healing what the withdrawn rule parked --------------------------------
    # The two warning texts the rule wrote, v1.5.2 and v1.5.4. Records on
    # real devices carry one or the other.
    OLD_RULE_WARNINGS = (
        "Its data has 16 item(s) that inherit from themselves (for example "
        "'Shout_WildShape_Badger'), which makes the game loop forever while "
        "loading instead of starting.",
        "Its data has 3 item(s) that inherit from something that does not "
        "exist anywhere (for example 'X' from 'Y'), so the game cannot load "
        "them correctly. That is a fault in the mod, so it was left "
        "switched off.",
    )

    def test_overriding_a_vanilla_entry_is_not_flagged(self):
        """UnlockLevelCurve does this 16 times, Vanilla Equipment Overhaul
        686 times: `new entry "X"` / `using "X"` where X is the game's own
        entry. The first version of the rule parked all 15 such mods."""
        self._archive({"Override.pak": self._make_stats_pak(
            "aaaa1111-0000-0000-0000-0000000000a1", "UnlockLevelCurve",
            'new entry "Shout_WildShape_Badger"\ntype "SpellData"\n'
            'using "Shout_WildShape_Badger"\ndata "Level" "13"\n')})
        self.assertTrue(self._install("UnlockLevelCurve").get("ok"))
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["disabled"], [], r)

    def test_overriding_another_enabled_mods_entry_is_fine(self):
        self._archive({"Provider.pak": self._make_stats_pak(
            "bbbb2222-0000-0000-0000-0000000000b2", "ProviderMod",
            'new entry "MY_Base_Item"\ntype "Object"\n')})
        self.assertTrue(self._install("Provider").get("ok"))
        self._archive({"Patch.pak": self._make_stats_pak(
            "cccc3333-0000-0000-0000-0000000000c3", "PatchMod",
            'new entry "MY_Base_Item"\ntype "Object"\nusing "MY_Base_Item"\n'
            'data "Rarity" "Legendary"\n')})
        self.assertTrue(self._install("Patch").get("ok"))
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertEqual(r["disabled"], [], r)

    def test_a_mod_parked_by_either_old_rule_comes_back(self):
        """Fifteen legitimate mods were parked by v1.5.2 and nine more by
        v1.5.4, each version with its own warning text. Both come back on:
        paks moved back, registration rewritten, warning gone."""
        for i, warning in enumerate(self.OLD_RULE_WARNINGS):
            key = f"Parked {i}"
            uuid = f"aaaa1111-0000-0000-0000-0000000000a{i}"
            self._archive({f"Parked{i}.pak": self._make_stats_pak(
                uuid, f"Parked{i}", self.SELF_REF_STATS)})
            self.assertTrue(self._install(key).get("ok"))
            # Simulate the old rule's verdict.
            run(self.plugin.set_mod_enabled(
                self.GAME, "Mods", key, False, "bg3", self.DOMAIN))
            s = main._load_settings()
            s["installed"][self.DOMAIN][key]["warning"] = warning
            main._save_settings(s)
            self.assertNotIn(uuid, self._uuids_in_modsettings())
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(sorted(r["healed"]), ["Parked 0", "Parked 1"])
        for i in range(len(self.OLD_RULE_WARNINGS)):
            key = f"Parked {i}"
            rec = main._load_settings()["installed"][self.DOMAIN][key]
            self.assertTrue(rec["enabled"], f"{key} must come back on")
            self.assertNotIn("warning", rec)
            self.assertTrue(os.path.isfile(
                os.path.join(main._bg3_mods_dir(), f"Parked{i}.pak")))
            self.assertIn(
                f"aaaa1111-0000-0000-0000-0000000000a{i}",
                self._uuids_in_modsettings(),
                "its registration must be written back",
            )

    def test_a_stale_warning_on_an_enabled_mod_is_cleared(self):
        """The A/B run switched the parked mods back on through the
        ordinary toggle, which leaves the warning text in place. The heal
        clears it so My Mods stops showing a verdict that was withdrawn."""
        self._archive({"Fine.pak": self._make_stats_pak(
            "aaaa1111-0000-0000-0000-0000000000f2", "Fine",
            self.HEALTHY_STATS)})
        self.assertTrue(self._install("A Fine Mod").get("ok"))
        s = main._load_settings()
        s["installed"][self.DOMAIN]["A Fine Mod"]["warning"] = (
            self.OLD_RULE_WARNINGS[1])
        main._save_settings(s)
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["healed"], ["A Fine Mod"])
        rec = main._load_settings()["installed"][self.DOMAIN]["A Fine Mod"]
        self.assertTrue(rec.get("enabled", True))
        self.assertNotIn("warning", rec)

    def test_a_user_disabled_mod_is_left_alone_by_the_heal(self):
        # Only records the OLD RULE parked are re-enabled. One the user
        # switched off stays off, whatever its stats look like.
        self._archive({"Fine.pak": self._make_stats_pak(
            "aaaa1111-0000-0000-0000-0000000000f2", "Fine",
            self.HEALTHY_STATS)})
        self.assertTrue(self._install("A Fine Mod").get("ok"))
        run(self.plugin.set_mod_enabled(
            self.GAME, "Mods", "A Fine Mod", False, "bg3", self.DOMAIN))
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertEqual(r["healed"], [])
        rec = main._load_settings()["installed"][self.DOMAIN]["A Fine Mod"]
        self.assertFalse(rec["enabled"])

    def test_a_script_extender_mod_stays_parked_through_the_heal(self):
        # Parked for a reason that still holds (the SE cannot run here)
        # is not the withdrawn rule's doing, and must stay parked.
        self._archive({"Fine.pak": self._make_stats_pak(
            "aaaa1111-0000-0000-0000-0000000000f2", "Fine",
            self.HEALTHY_STATS)})
        self.assertTrue(self._install("Needs SE").get("ok"))
        run(self.plugin.set_mod_enabled(
            self.GAME, "Mods", "Needs SE", False, "bg3", self.DOMAIN))
        s = main._load_settings()
        s["installed"][self.DOMAIN]["Needs SE"]["warning"] = main.BG3_SE_UNAVAILABLE
        main._save_settings(s)
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertEqual(r["healed"], [])
        rec = main._load_settings()["installed"][self.DOMAIN]["Needs SE"]
        self.assertFalse(rec["enabled"])
        self.assertEqual(rec["warning"], main.BG3_SE_UNAVAILABLE)

    # ---- records repaired from the paks on disk ----------------------------------
    # On device (2026-09-02) 35 load-order divider paks sat in Mods with no
    # modsettings entry: their record was written by the tokenizer that lost
    # UUIDs on a `>` inside an attribute, so the stored metadata had no UUID
    # to register. The game had registered them itself when Michael pressed
    # enable-all, and the next rewrite dropped them again ("I had to enable
    # the mods this time"). The pak on disk knows its own UUID; the pass
    # re-reads it.

    def test_stored_metadata_that_lost_its_uuid_is_repaired_from_the_pak(self):
        self._archive({"Divider.pak": self._make_stats_pak(
            "dddd4444-0000-0000-0000-0000000000d4", "Divider",
            self.HEALTHY_STATS)})
        self.assertTrue(self._install("Load Order Tabs").get("ok"))
        # Simulate the old tokenizer's record: metadata without a UUID.
        s = main._load_settings()
        for meta in s["installed"][self.DOMAIN]["Load Order Tabs"]["bg3_mods"]:
            meta["uuid"] = ""
        main._save_settings(s)
        # ...and the entry gone from modsettings, as the game's own rewrite
        # left it on device. The writer alone cannot bring it back: with no
        # stored UUID it owns nothing to register.
        ms_path = main._bg3_modsettings_path()
        with open(ms_path, encoding="utf-8") as f:
            ms = f.read()
        ms = re.sub(
            r'<node id="ModuleShortDesc">(?:(?!</node>).)*?dddd4444-0000-0000'
            r'-0000-0000000000d4(?:(?!</node>).)*?</node>\s*', "", ms,
            flags=re.S)
        with open(ms_path, "w", encoding="utf-8") as f:
            f.write(ms)
        self.assertNotIn(
            "dddd4444-0000-0000-0000-0000000000d4", self._uuids_in_modsettings())
        self.assertEqual(main._write_bg3_modsettings(s, self.DOMAIN), "")
        self.assertNotIn(
            "dddd4444-0000-0000-0000-0000000000d4", self._uuids_in_modsettings(),
            "without a stored UUID the writer has nothing to register")
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["repaired"], ["Load Order Tabs"])
        rec = main._load_settings()["installed"][self.DOMAIN]["Load Order Tabs"]
        self.assertEqual(
            [m["uuid"] for m in rec["bg3_mods"]],
            ["dddd4444-0000-0000-0000-0000000000d4"])
        self.assertIn(
            "dddd4444-0000-0000-0000-0000000000d4", self._uuids_in_modsettings())
        # A second pass has nothing left to repair.
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertEqual(r["repaired"], [])

    def test_a_disabled_records_pak_left_in_mods_is_parked(self):
        """Two disabled records on device still had their paks in Mods. The
        record is the truth; the pak follows it."""
        self._archive({"Stray.pak": self._make_stats_pak(
            "eeee5555-0000-0000-0000-0000000000e5", "Stray",
            self.HEALTHY_STATS)})
        self.assertTrue(self._install("Stray Mod").get("ok"))
        run(self.plugin.set_mod_enabled(
            self.GAME, "Mods", "Stray Mod", False, "bg3", self.DOMAIN))
        shutil.move(os.path.join(main._bg3_disabled_dir(), "Stray.pak"),
                    os.path.join(main._bg3_mods_dir(), "Stray.pak"))
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["repaired"], ["Stray Mod"])
        self.assertFalse(os.path.exists(
            os.path.join(main._bg3_mods_dir(), "Stray.pak")))
        self.assertTrue(os.path.isfile(
            os.path.join(main._bg3_disabled_dir(), "Stray.pak")))
        self.assertNotIn(
            "eeee5555-0000-0000-0000-0000000000e5", self._uuids_in_modsettings())

    def test_an_enabled_records_pak_left_parked_comes_back(self):
        self._archive({"Lost.pak": self._make_stats_pak(
            "ffff6666-0000-0000-0000-0000000000f6", "Lost",
            self.HEALTHY_STATS)})
        self.assertTrue(self._install("Lost Mod").get("ok"))
        os.makedirs(main._bg3_disabled_dir(), exist_ok=True)
        shutil.move(os.path.join(main._bg3_mods_dir(), "Lost.pak"),
                    os.path.join(main._bg3_disabled_dir(), "Lost.pak"))
        r = run(self.plugin.bg3_disable_broken_deps(self.DOMAIN, self.GAME))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["repaired"], ["Lost Mod"])
        self.assertTrue(os.path.isfile(
            os.path.join(main._bg3_mods_dir(), "Lost.pak")))
        self.assertFalse(os.path.exists(
            os.path.join(main._bg3_disabled_dir(), "Lost.pak")))
        self.assertIn(
            "ffff6666-0000-0000-0000-0000000000f6", self._uuids_in_modsettings())

    def test_home_relative_docs_check(self):
        real = main.HOME_ROOT
        main.HOME_ROOT = TEST_ROOT
        try:
            marker = os.path.join(TEST_ROOT, "somefile.txt")
            with open(marker, "w") as f:
                f.write("x")
            r = run(self.plugin.check_docs_file(0, "~/somefile.txt"))
            self.assertTrue(r["exists"])
            r = run(self.plugin.check_docs_file(0, "~/absent.txt"))
            self.assertFalse(r["exists"])
            r = run(self.plugin.check_docs_file(0, "~/../escape"))
            self.assertFalse(r.get("ok", True))
        finally:
            main.HOME_ROOT = real


class TestBg3BootHunt(unittest.TestCase):
    """The boot-hunt classifier, held to the numbers actually measured on
    device (2026-09-02). Judging a boot is the whole risk in an unattended
    bisection: call a healthy boot a spin and it condemns an innocent mod,
    call a spin healthy and the hunt walks straight past the culprit."""

    def setUp(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
        import bg3boothunt

        self.h = bg3boothunt

    @staticmethod
    def _code(path):
        """Source with comments and docstrings stripped.

        These tests assert on what the tool DOES. Without stripping, a
        comment mentioning "rungameid" would satisfy a check that the code
        never calls it - and this file's own comments mention every one of
        these mistakes by name.
        """
        import io as _io
        import tokenize

        with _io.open(path, encoding="utf-8") as f:
            src = f.read()
        lines = src.splitlines(keepends=True)
        # Absolute offset of the start of each 1-based line.
        starts = [0]
        for ln in lines:
            starts.append(starts[-1] + len(ln))

        def off(pos):
            row, col = pos
            return starts[row - 1] + col

        try:
            toks = list(tokenize.generate_tokens(_io.StringIO(src).readline))
        except tokenize.TokenError:
            return src
        # Blank out comment and docstring RANGES in the original text
        # rather than rebuilding from tokens: rebuilding turned
        # "timeout=20" into three separate pieces, so a search for
        # "timeout=" found nothing and the test lied about the code.
        blanks = []
        prev_meaningful = None
        for tok in toks:
            if tok.type == tokenize.COMMENT:
                blanks.append((off(tok.start), off(tok.end)))
                continue
            if tok.type == tokenize.STRING and prev_meaningful in (
                None, tokenize.INDENT, tokenize.NEWLINE, tokenize.NL,
            ):
                blanks.append((off(tok.start), off(tok.end)))
            if tok.type not in (tokenize.NL, tokenize.COMMENT):
                prev_meaningful = tok.type
        chars = list(src)
        for a, b in blanks:
            for i in range(a, min(b, len(chars))):
                if chars[i] != "\n":
                    chars[i] = " "
        return "".join(chars)

    def _series(self, specs):
        """specs: list of (cpu_ticks_delta, rss_delta_mb, io_delta_mb),
        turned into the cumulative samples the classifier reads."""
        out = [{"cpu_ticks": 0, "rss_mb": 1800, "io_mb": 1000}]
        for cpu, rss, io in specs:
            last = out[-1]
            out.append({
                "cpu_ticks": last["cpu_ticks"] + cpu,
                "rss_mb": last["rss_mb"] + rss,
                "io_mb": last["io_mb"] + io,
            })
        return out

    # ---- the measured spin ------------------------------------------------
    # 79% world load: ~3180 ticks per 10s (three cores), RSS 1975MB flat,
    # zero bytes read or written. 94% boot: ~2050 ticks (two cores), same
    # flatness. Both must read as spins.
    def test_the_measured_three_core_spin_is_caught(self):
        s = self._series([(3180, 0, 0)] * 7)
        self.assertEqual(self.h.classify(s, False, False), "spin")

    def test_the_measured_two_core_spin_is_caught(self):
        s = self._series([(2050, 1, 0)] * 7)
        self.assertEqual(self.h.classify(s, False, False), "spin")

    def test_a_spin_needs_to_persist_before_it_is_called(self):
        """Three samples of pinned CPU is a loading stutter, not a verdict."""
        s = self._series([(3180, 0, 0)] * 3)
        self.assertEqual(self.h.classify(s, False, False), "watching")

    def test_one_core_busy_is_not_a_spin(self):
        # A single busy core is ordinary work; the fault pinned 2-3.
        s = self._series([(1000, 0, 0)] * 8)
        self.assertNotEqual(self.h.classify(s, False, False), "spin")

    def test_growing_memory_is_never_a_spin(self):
        # The control run grew RSS by 966MB on its way to the menu while
        # burning plenty of CPU. Loading is busy AND productive - and not
        # yet success either (test_loading_is_not_success).
        s = self._series([(3180, 140, 2)] * 8)
        self.assertEqual(self.h.classify(s, False, False), "watching")

    def test_disk_activity_is_never_a_spin(self):
        s = self._series([(3180, 0, 40)] * 8)
        self.assertEqual(self.h.classify(s, False, False), "watching")

    def test_a_boot_that_loads_and_then_hangs_is_a_spin(self):
        """The 94% hang. The game loaded for a while (memory up by a
        gigabyte, real I/O) and THEN sat pinned with flat memory. The first
        classifier called a boot "ok" the moment memory had grown by 150MB,
        so a hang late in the loading bar - the only kind Michael actually
        saw - could never have been caught, and an A/B built on it would
        have cleared every mod."""
        s = self._series([(3180, 300, 60)] * 3 + [(2050, 0, 0)] * 7)
        self.assertEqual(self.h.classify(s, False, False), "spin")

    def test_loading_is_not_success(self):
        """Growth means the game is still loading, nothing more. Success is
        only the settled press-any-key screen."""
        s = self._series([(3180, 300, 60)] * 5)
        self.assertNotEqual(self.h.classify(s, False, False), "ok")
        self.assertNotEqual(self.h.classify(s, True, False), "ok")

    # ---- healthy boots ----------------------------------------------------
    def test_the_measured_control_boot_reads_as_ok(self):
        # Real numbers: rss +966MB, io +8MB inside one sample, then the
        # press-any-key screen: memory flat, CPU low, profile written.
        s = self._series([(900, 966, 8)] + [(200, 0, 0)] * 3)
        self.assertEqual(self.h.classify(s, True, False), "ok")

    def test_settled_at_the_press_any_key_screen_is_ok(self):
        """Michael: "you have to press a button to see the menu". A game
        idling there has a loaded game's memory, low CPU, and has written
        its profile files - that combination is success, not a stall."""
        s = self._series([(200, 0, 0)] * 4)
        s = [dict(x, rss_mb=x["rss_mb"] + 1000) for x in s]
        self.assertEqual(self.h.classify(s, True, False), "ok")

    def test_the_measured_118_mod_boot_settles_as_ok(self):
        """Real samples from the Legion, 2026-09-02, 118 mods enabled: two
        samples of loading (0.7GB per sample, 1.7-2.6 cores), then memory
        flat at 2.1GB and 640-680 ticks per sample at the press-any-key
        screen. The first idle threshold (0.6 core) sat BELOW that, so a
        healthy boot could only ever have read as inconclusive."""
        s = self._series([
            (1674, 688, 0), (2605, 705, 57),
            (679, -5, 0), (642, -1, 0), (655, 0, 0),
        ])
        self.assertEqual(self.h.classify(s, True, False), "ok")
        # Without the profile files it is still too early to call.
        self.assertNotEqual(self.h.classify(s, False, False), "ok")

    def test_idle_without_profile_files_is_not_yet_ok(self):
        # Low CPU and flat memory but nothing written: too early to call.
        s = self._series([(200, 0, 0)] * 4)
        s = [dict(x, rss_mb=x["rss_mb"] + 1000) for x in s]
        self.assertNotEqual(self.h.classify(s, False, False), "ok")

    # ---- the honesty requirement -----------------------------------------
    def test_running_out_of_time_is_inconclusive_not_spin(self):
        """The first version of this called a timeout a spin, which would
        have blamed whichever half happened to be loaded."""
        s = self._series([(700, 2, 0)] * 8)
        self.assertEqual(self.h.classify(s, False, True), "inconclusive")

    def test_too_few_samples_says_watching(self):
        self.assertEqual(self.h.classify([], False, False), "watching")
        self.assertEqual(
            self.h.classify([{"cpu_ticks": 0, "rss_mb": 1, "io_mb": 1}],
                            False, False),
            "watching",
        )

    # ---- the lessons that cost a run each --------------------------------
    def test_it_launches_with_applaunch_not_the_store_url(self):
        """steam://rungameid opened the STORE PAGE and the game never
        started, which the watcher read as failure."""
        src = self._code(os.path.join(REPO_ROOT, "tools", "bg3boothunt.py"))
        self.assertIn('"-applaunch"', src)
        self.assertNotIn("rungameid", src)

    def test_every_subprocess_call_has_a_timeout(self):
        """One pkill that never returned wedged an entire run reading its
        pipe."""
        src = self._code(os.path.join(REPO_ROOT, "tools", "bg3boothunt.py"))
        self.assertIn("timeout=", src)
        # subprocess.run must only be reached through the wrapper that
        # always passes a timeout.
        self.assertEqual(
            src.count("subprocess.run("), 1,
            "subprocess.run belongs only in run_cmd, which times out",
        )

    def test_it_is_not_named_after_a_stdlib_module(self):
        """The first version was bisect.py and shadowed the stdlib, dying
        during import with no output at all."""
        import importlib.util

        name = "bg3boothunt"
        self.assertIsNone(
            importlib.util.find_spec(name) if name in sys.stdlib_module_names
            else None
        )
        self.assertNotIn(name, sys.stdlib_module_names)

    def test_it_kills_the_game_before_changing_mod_state(self):
        """The plugin refuses to touch mods while bg3 runs. Every toggle in
        the first run was refused, leaving a control run that tested
        nothing."""
        src = self._code(os.path.join(REPO_ROOT, "tools", "bg3boothunt.py"))
        i = src.index("def apply_state(")
        body = src[i : i + 700]
        self.assertIn("kill_game", body)
        self.assertIn("refusing to guess", body)

    def test_it_restores_the_mod_set_on_every_exit_path(self):
        src = self._code(os.path.join(REPO_ROOT, "tools", "bg3boothunt.py"))
        i = src.index("def hunt(")
        body = src[i:]
        self.assertIn("finally:", body)
        self.assertIn("SIGINT", body)
        self.assertIn("restore-only", src)

    def test_it_samples_the_child_not_the_shim(self):
        """The shim sits in do_wait on its child: sampling it showed 93MB
        and 0% CPU, which nearly sent the diagnosis chasing a phantom."""
        src = self._code(os.path.join(REPO_ROOT, "tools", "bg3boothunt.py"))
        i = src.index("def game_pid(")
        self.assertIn("children", src[i : i + 600])

    def test_a_failing_control_run_aborts_instead_of_bisecting(self):
        src = self._code(os.path.join(REPO_ROOT, "tools", "bg3boothunt.py"))
        i = src.index("control run")
        self.assertIn("ABORT", src[i : i + 900])


if __name__ == "__main__":
    unittest.main()
