# Battlefront II mods work on the Deck. Here is the whole recipe.

21 August 2026. "Shadow Lord Maul" applied to Star Wars Battlefront II (2017)
on a Legion Go 2 running SteamOS, visible in the Collection screen, with no
Windows tool and no desktop GUI anywhere in the chain.

This file is the recipe and the reasoning. `frostycli-swbf2.patch` is the diff
against [FrostyToolsuite](https://github.com/FrostyToolsuite/FrostyToolsuite)
master (commit ee2a587) that makes it possible: 893 lines across 7 files.

## The chain

1. **Build FrostyCli for linux-x64.** `dotnet publish FrostyCli -c Release -r
   linux-x64 --self-contained -p:PublishSingleFile=true`. It targets .NET 8 and
   runs natively on SteamOS. Ship the `Profiles`, `Sdk`, `Meta` and
   `ThirdParty` directories alongside it.
2. **Generate the type SDK, once per game version.** Needs the game RUNNING:
   `FrostyCli load <exe> --pid <pid>` dumps types from process memory
   (23,221 of them) via `/proc/<pid>/mem`. Set `kernel.yama.ptrace_scope=0`
   for the dump and restore it after. The pid is the process with a multi-GB
   RSS - `pgrep -f` also matches EA's proxy, which yields "No offset found for
   TypeInfo".
3. **Convert the mod.** `FrostyCli update-mod <exe> <mod.fbmod> --output
   <new.fbmod>`. Community mods are v2 to v5; the executor only reads v6.
   An unconverted mod is silently ignored - it generates a ModData with no
   changes at all.
4. **Generate ModData.** `FrostyCli mod <exe> <mods-dir> <ModData/Pack>`.
5. **Point the game at it.** See below. This was the single hardest part.

## Making the game read it

`GAME_DATA_DIR`, set as a persistent environment variable inside the Wine
prefix. Not launch options, not `user.cfg`, not `-dataPath`:

    # compatdata/1237950/pfx/user.reg, in the [Environment] section
    "GAME_DATA_DIR"="Z:\\home\\deck\\frosty\\moddata"

That is what FrostyFix does on Windows (`SetEnvironmentVariable(...,
EnvironmentVariableTarget.User)`) and the reason is the same on both
platforms: EA's launcher respawns the game and strips arguments. Steam launch
options are applied to the wrapper, not to the game.

Also required:

- `CryptBase.dll` beside the game exe (from Frosty v1's ThirdParty; v1
  explicitly DELETES bcrypt.dll and ships this instead).
- A DllOverride so it loads: `"cryptbase"="native,builtin"` under
  `[Software\\Wine\\AppDefaults\\starwarsbattlefrontii.exe\\DllOverrides]`.
- Use a path with no spaces for the pack (a symlink into the ModData folder
  works) - decky-launch-options mangles quoted values.

Proton rebuilds `user.reg` when it upgrades a prefix and drops added keys, so
both entries have to be re-asserted after that. Never run the game headless
with a different Proton than Steam uses: it downgrades the prefix and leaves
EA's app demanding a reinstall (INST-14-1627).

## Four bugs in FrostyToolsuite

All four corrupt data for ANY game the toolsuite supports, not just this one.
Each was found by measurement, and each is small.

**1. `SuperBundleManifest` was never implemented.** A `throw new
NotImplementedException()` in `FrostyModExecutor.GenerateMods`, beside a
22-line stub. This is the format Battlefront II uses. The implementation is
`SuperBundleManifest.cs` in this folder.

**2. `BinaryBundle.Modify` indexed the sha1 array wrongly.** The array is
GLOBAL - ebx, then res, then chunks - and indexed by `j`. The res and chunk
loops wrote `sha1[i]`, the per-category index:

    res[i] = modEntry;
    sha1[i] = modEntry.Sha1;   // must be sha1[j]

So modifying one texture overwrote the sha1 of EBX NUMBER i in every bundle it
touched. A Commando Droid retexture gave a Kylo lightsaber sound the texture's
hash, and the game crashed reading it. Before the fix that hash appeared on a
lightsaber sound, a stormtrooper VO line and a droideka texture; after, on
`t_bxcommanddroid_cs` with its correct 132-byte size.

**3. Placeholder sha1s survived into rebuilt bundles.** For an asset a mod only
ADDS to a bundle, `ProcessModResources` substituted the game's real sha1 AFTER
constructing the mod entry, which had already copied the placeholder:

    m_memoryData.TryAdd(entry.Sha1, data);
    modEntry = new EbxModEntry(ebx, data.Size);   // copies resource.Sha1 (zero)
    resource.Sha1 = entry.Sha1;                   // too late

Result: bundles listing assets with sha1 all-zeros, and the game crashing the
moment it drew the character using them. Three branches, ebx/res/chunk.

**4. The same ordering bug for OriginalSize**, which was not even settable. A
Maul movement sound came through with `originalSize=0`, so anything reading it
tried to decompress into a zero-byte buffer.

## What the manifest writer does

The format has no per-SuperBundle toc/sb files. `layout.toc`'s `manifest`
entry points at one global blob inside a cas file:

    u32 resourceInfoCount, u32 bundleCount, u32 chunkCount
    resourceInfos[]: { u32 manifestFileIdentifier, u32 offset, i64 size }
    bundles[]:       { i32 nameHash, i32 startIndex, i32 resourceCount, u64 0 }
    chunks[]:        { guid id, i32 resourceInfoIndex }

Each bundle's range is **the meta plus exactly one entry per asset**, in meta
order (ebx, res, chunks). Verified against Frosty v1's own output: 291 entries
for 290 assets, 16,181 for 16,180.

The original ranges are COALESCED - one entry can cover several physical cas
resources - so they are expanded against the game's `cas.cat` files first,
which yields one entry per asset in the original meta's order. That expansion
then lines up POSITIONALLY with the rebuilt meta, so unchanged assets need no
lookup at all. Locations resolve in this order:

1. data written this run, when the mod actually changes the asset
2. the expanded original entry at the same position
3. the base catalog, or the game's own asset record by name (for assets the
   mod adds that the game already owns)

Never a zero placeholder: hundreds of those per bundle made the game hang on
the loading screen rather than crash, which is a much harder symptom to read.

Because the manifest is global rather than per-SuperBundle, the whole rebuild
runs ONCE after every cas archive is written, and before the catalogs are.

## Verification, so a boot is never wasted

Make FrostyCli re-parse its own output:

    cp <game>/starwarsbattlefrontii.exe <ModData>/     # the loader needs an exe
    mv Caches/starwarsii.cache /tmp/                   # force a real parse
    FROSTY_VALIDATE_ALL=1 ./FrostyCli load "<ModData>/starwarsbattlefrontii.exe"

The working build reports `res ok=84164 bad=0 | chunks ok=211743 bad=0` and
indexes every ebx. This oracle caught every one of the bugs above, and it is
honest: when it failed, the game failed too, and when it passed, the game ran.

Diagnostics in the patch, all env-gated: `FROSTY_TRACE_WRITES`,
`FROSTY_VERIFY_WRITES` (every block must hash to its filed sha1),
`FROSTY_CHECK_META`, `FROSTY_TRACE_ASSET=<name fragment>`,
`FROSTY_WATCH_SHA1=<prefix>`, `FROSTY_SKIP_MANIFEST`, `FROSTY_VALIDATE_ALL`.

## What this means for the plugin

Every step is scriptable and headless, which is what the plugin needs:

- Ship FrostyCli + its support folders with the plugin, or fetch on first use.
- SDK generation needs the game running once, which fits a "Step 1" prompt.
- Install: download the .fbmod, convert it, regenerate ModData for the whole
  enabled set, then set the prefix registry entries.
- Enable/disable a mod: regenerate ModData from the new set. There is no
  per-mod toggle in this format.
- Refuse gracefully: run the read-back before offering the mod as installed,
  because a mod whose archive is internally inconsistent can be detected
  rather than left to crash the game.

## Test mods used

- **Shadow Lord Maul** (mod 13974, Maul variant): the one that proved it.
  Recent, v5, adds 329 assets, replaces a hero - visible in the Collection.
- **Commando Droid Retexture** (13574): tiny and clean, but a poor visual test
  since several droid variants look alike.
- **Rey Model for Darth Maul** (91): modifies one ebx per bundle and ships no
  mesh, so it made Maul invisible. Useful as the first sign of life, not as
  proof.

Test from the Collection screen - it renders every character, so there is no
need to start a match.

## Mods built for a different game build

Battle Damaged Darth Vader (mod 2042, main file dated January 2021) installs
cleanly, passes the read-back check, and renders Vader as a mass of shards
with a magenta wash. The pack is not corrupt: with the cache cleared,
`FROSTY_VALIDATE_ALL=1 FrostyCli load` reports
`res ok=84130 bad=0 | chunks ok=211705 bad=0`.

What separates it from a mod that works is one line of FrostyCli output:

```
WARN - Mod Battle Damaged Vader (Cracked) was made for a different version
       of the game, it might or might not work
```

Shadow Lord Maul, compiled against the same game (`head.txt` is 489592 for
both packs), produces no such line and looks correct.

Things that turned out NOT to be the difference, each checked rather than
assumed:

- Meshes. Both mods replace meshes. Maul contains
  `maulshadow_body_mesh` and its `_mesh_mesh/blocks` chunks.
- `ModifiedShaderBlockDepot`. Both contain it, so a missing handler is not
  the explanation. The Handlers directory is empty in our toolkit build and
  that has not stopped a mesh mod working.
- Per-bundle entry accounting. For all 196 bundles the mod touches,
  `written + original + catalog == assets` and `built == assets + 1`, so
  there is no off-by-one shifting assets onto each other's data.

The real difference is what each mod does to the game's own assets. Maul ADDS
a character under new paths (`a0_maulshadow/...`). Vader REPLACES vanilla
paths (`characters/hero/darthvader/darthvader_01/darthvader_01_mesh`), and it
was built against a build of the game where those assets differed.

So the plugin surfaces that warning rather than discarding it. `_frosty_run`
kept only ERROR lines, which is why the one useful sentence never reached
the user. It is now stored with the mod, so My Mods shows it too - the moment
it matters is weeks after the install toast has gone.

## Replaced meshes rendered as shards: three compiler bugs, none of them the mod

The "built for a different version of the game" theory above did not survive
contact with The Mandalorian (2022, no version warning, same shards). What
actually separated working mods from broken ones was replaced TEXTURES work,
replaced MESHES shatter. Diffing our pack against one built by real Frosty v1
for the same mod (the PC still had v1's ModData for Battle Damaged Vader)
found three independent causes:

1. **Streaming-table sub-ranging (ours).** The manifest's chunks table is the
   streaming view and always covers the whole chunk; the firstMip sub-range
   belongs only to bundle entries. We applied the sub-range in both places,
   so every replaced mesh streamed a fragment of itself. Vanilla and v1 agree
   on the full-blob semantics.

2. **No ShaderBlockDepot handler (upstream gap).** A mesh mod ships shader
   block DELTAS (handler hash 0x89EF2205) that must be merged into the
   game's depot at apply time. The new toolsuite's executor skips handler
   resources when no handler is registered, and none shipped, so the game
   kept its original shader blocks for a mesh that was no longer there.
   Ported v1's merge handler (ShaderBlockDepotHandler.cs in this folder),
   which also required finishing the executor's handler plumbing: joining
   the game entry's bundles for handler resources, making ResModEntry's
   OriginalSize and ResMeta settable, and exempting handler output from the
   SuperBundleManifest base-copy skip (our own earlier fix was silently
   discarding the merge results).

3. **ZSTD_compress binding missing its level argument (upstream bug #6).**
   The real function takes five arguments; the binding passed four, so the
   compression level was register garbage and output was stored raw.

Proof of equivalence: a `dump` command was added to FrostyCli that emits
every asset's identity, sizes, res meta and cas location as JSON, plus a
sha1 of the DECOMPRESSED payload. For Battle Damaged Vader (Cracked), all
27 res in the touched bundles - including both merged shader depots - and
all 8138 chunks now match real Frosty v1's pack: content byte-identical,
res meta byte-identical, originalSize equal. FROSTY_VALIDATE_ALL stays
green: res ok=84130 bad=0, chunks ok=211705 bad=0.

Shipped as toolkit build 2 (release tag frosty-toolkit-2). The plugin
upgrades an installed toolkit silently at the next install or toggle, and
clears the SDK cache on upgrade so a fixed compiler cannot keep reading
yesterday's cache.
