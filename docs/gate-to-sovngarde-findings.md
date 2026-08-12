# Gate To Sovngarde: what it takes to boot on SteamOS

The second most popular Skyrim collection (1,972 mods) booting on a
Legion Go 2 running SteamOS, verified 2026-08-11: main menu, new game,
world loads.

This is the first real entry for the known-bad database. The point of
recording it is that **nobody should have to repeat this**. Finding it
took roughly 150 launches across three days, most of them four minutes
each.

## The working configuration

- **1,945 plugins enabled**, 0 load-order violations, 0 disabled masters
- **30 plugins skipped** (list below)
- **2 SKSE DLLs skipped**: `LumaUtil.dll`, `BehaviorDataInjector.dll`
- ~390 Creation Club patches disabled, correctly and unrelated - their
  masters are CC content the account does not own

Two crashes were involved, at different addresses, one hidden behind the
other:

| Address | Where | Found by |
|---|---|---|
| `SkyrimSE.exe+01D74A0` | data load, `InitTESThread` | manual bisect, then the automated hunt |
| `SkyrimSE.exe+01D8845` | cell/worldspace init | crash-log suspect (`LumaUtil`) + the hunt |

## Skipped plugins

Every one is ESL-flagged, and every one is part of the collection's own
patch layer rather than a third-party mod. Sizes range from 210 bytes to
10 MB, so "tiny patch file" was a pattern in the first few, not a rule.

```
CC_Menagerie.esp                              CC_MenagerieECSS.esp
CC_MenagerieMysticism.esp                     GTS - Dac0da.esp
GTS - Dungeons Relocation.esp                 GTS - Easy Mode.esp
GTS - Elder Scrolling.esp                     GTS - New Armors.esp
GTS - Orpheus Replacer.esp                    GTS - Shortspears.esp
GTS - Taliesin Replacer.esp                   GTS - Vigilant.esp
GTS Patches - CC Stuff.esp                    GTS Patches - CC Stuff Part 2.esp
GTS Patches - CC Stuff Part 3.esp             GTS Patches - CC Stuff Part 4.esp
GTS Patches - Food.esp                        GTS Patches - Immersive Interactions.esp
GTS Patches - Landscapes Part 2.esp           GTS Patches - Old Synthesis.esp
GTS Patches - Quests.esp                      GTS Patches - Remove Rubble Hotfix.esp
GTS Patches - Scion.esp                       GTS Patches - Smithing Clean Up.esp
GTS_Traits.esp                                NJR - Bruma Patch.esp
StendarrsChosen.esp                           StendarrsChosen - Bruma Spawns Addon.esp
StendarrsChosen - No Skyrim Spawns.esp        StendarrsChosen - WyrmstoothSpawns Addon.esp
```

Only a handful are independent root causes; the rest are dependents that
cannot load without them. `_dependents_closure` derives the full set from
the roots, so a database entry only needs to record the roots.

## Clean-install verification (2026-08-11)

Skyrim deleted back to vanilla (20GB of orphaned mod files removed), then
the collection reinstalled from scratch against v0.84.0. It reached the
main menu.

The known-bad table did its job unattended: **24 of the 25 required
exclusions happened during install with no button and no user
involvement** - 9 roots from the table, and 15 dependents derived from
them as they were installed.

Three gaps the clean run exposed, none of them guesses:

1. **Skyrim rewrites Plugins.txt itself.** It re-enabled two of our skips
   (GTS_Traits, GTS - New Armors) during the run. This file was being
   treated as ours alone and it is not - it needs the read-only handling
   every other mod manager uses.
2. **The install-time dependent check only sees mods installed AFTER
   their master.** `GTS - Orpheus Replacer` installed before the master
   it needs was skipped, so nothing caught it. Needs a closure pass when
   the collection finishes, not only per-mod.
3. Two entries appear as dependents that are really something else -
   `dyndolod.esp` and `occlusion.esp` are generated output, not
   collection patches. Harmless here, worth understanding before the
   table is published.

Working set: 1,895 enabled of 2,337 listed, 25 skipped.

## Unattended verification (2026-08-12) - the standard met

Reset to vanilla, collection reinstalled overnight, launched in the
morning with nobody touching anything in between. It reached the main
menu first time and a new game loaded. Download pause and resume were
also exercised against the real CDN for the first time and behaved.

Reset then returned the game to vanilla and said so honestly. Measured
before the run: 1,543 records covering 222,047 file paths against
222,219 files on disk - 174 untracked, of which 158 were vanilla and the
remaining 16 were mod-written config and logs that no install had ever
placed.

That closes every Skyrim blocker. What made the difference over the
attended run:

- **v0.86.0** - several files of one mod no longer erase each other's
  record. 212 of the 1,543 records are multi-file; before this, all but
  the last file of each was untrackable, which is what left 668 files
  and 26GB behind a "successful" reset. Twice.
- **v0.85.0** - the skip set is re-asserted when a collection finishes
  (catching a dependent installed before its master was skipped) and
  when the game exits (undoing Skyrim's own rewrite of Plugins.txt).
- **v0.87.0** - reset sweeps what no record could cover, guarded by a
  baseline captured before the first mod was ever installed.

## Second collection: Immersive and Adult (2026-08-12)

559 mods, the most popular Skyrim collection. Reset to vanilla, installed
from scratch, booted - **no intervention at any point**.

None of Gate To Sovngarde's nine known-bad entries applied; they are that
collection's own patch files. What transferred was the machinery, not the
data, which is the outcome the table was designed for.

Two faults it exposed instead, both ours and both fixed:

1. **7z had never run.** Decky Loader is a PyInstaller bundle, so plugins
   inherit LD_LIBRARY_PATH pointing at its unpacked /tmp/_MEIxxxxxx
   directory, whose libreadline is older than the system one. SteamOS
   ships /usr/bin/7z as a /bin/sh wrapper, and /bin/sh links readline, so
   it died on a symbol lookup before reaching the archive. The three-deep
   extractor fallback had been two deep since the day it was written, and
   the log blamed the archives. Surfaced by one mod shipping a Deflate64
   zip - the one format where bsdtar, unrar and Python's zipfile all
   refuse and only 7z can read it. Fixed in v0.89.0 for all six
   subprocess spawns, with an AST test that fails on a spawn missing
   `env=`.
2. **The Uninstall count read as failure.** 546 collection entries, 454
   install records, because 78 of its mods ship more than one file. Both
   numbers were right and counting different units, which is worse than
   one being wrong - it reads as 92 mods silently missing while every row
   shows a tick. Fixed in v0.90.0.

The plugin slot ceiling never came close: this collection is nowhere near
the 254 full slots, same as Gate To Sovngarde at 208. The v0.89.0 warning
exists for the 2,444-mod collection, which is the first plausible
candidate to cross it.

## Bugs in the plugin this exposed, all fixed

These were ours, not the collection's, and every one would have hit any
large Bethesda setup:

1. **plugins.txt was install order, not load order.** 557 of 1,960
   plugins were listed before a master they depend on. We only ever
   appended. Fixed in v0.69.0 with a masters-first topological sort;
   FO3/FNV order by timestamp and already had the equivalent.
2. **13 masters installed but switched off**, with 139 plugins depending
   on them - mostly the free Anniversary Edition Creation Club files that
   Skyrim ships in Data but leaves out of the plugin list. Fixed in
   v0.71.0, transitively.
3. **Base masters written into plugins.txt.** The first cut of the master
   repair enabled `Skyrim.esm` and the four DLC, which renumbers every
   plugin after them - and the load index is what save files record.
   Caught by a test, not on device.
4. **`_plugin_entries` assumed the starred dialect**, so every plugin in
   a `listed` file (FO3/FNV) parsed as disabled. Any check built on it
   was a silent no-op on exactly the two games it was meant for.
5. **The VC++ runtime in the prefix was 2016-era**, failing 37 SKSE
   plugins with nothing but "fatal error occurred while loading".

## What is still wrong, and matters before this ships

**The tool does not record WHY a plugin is off.** A deliberate skip and
an incidental one look identical in plugins.txt, so anything that tidies
up undoes the user's decisions:

- `fix_load_order` re-enabled 8 skipped plugins because something still
  listed them as a master (fixed here by applying the dependents closure
  by hand)
- the crash hunt's `finish` restored `LumaUtil.dll` and
  `BehaviorDataInjector.dll`, which the user had deliberately skipped,
  because it could not tell them from DLLs it had parked itself

A skip needs to be sticky and carry its dependents automatically. This is
also what makes "do not download these" work: a download-time skip has to
survive every later repair.

**The hunt verifies with mod DLLs parked, then restores them.** So "done"
means "boots without mod DLLs", not "boots in the real setup". It should
re-verify after restoring.

**The user is the integration layer.** Three separate mechanisms
(crash-log suspect, script-extender skip, automated hunt) and the human
decides between them. It should be one button that triages: park the
DLLs named in the crash log, launch, bisect DLLs if that helps, bisect
plugins if it does not, report once at the end.

## Cosmetic, unfixed

Community Shaders compiles none of its shaders: Wine's `d3dcompiler_47`
does not support HLSL 2021 (`namespace`), so every shader fails with
`E5000: syntax error, unexpected KW_NAMESPACE`. Not a crash, and the same
shape as the VC++ runtime fix - drop a compiler that supports it into the
prefix.
