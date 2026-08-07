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

    def test_seamless_coop_gets_its_early_load_hook(self):
        settings = self._record(
            "Seamless Coop", package=False, natives=["ersc.dll"]
        )
        with open(main._write_me3_profile(self.DOMAIN, settings)) as f:
            body = f.read()
        self.assertIn('path = "mods/Seamless Coop/ersc.dll"', body)
        self.assertIn("load_early = true", body)
        self.assertIn('initializer = { function = "modengine_ext_init" }', body)
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
