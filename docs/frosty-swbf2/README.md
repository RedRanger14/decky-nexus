# Battlefront II (2017): the Frostbite pipeline, and where it stands

Working notes from 21 August 2026, kept because the next session should not
have to rediscover any of it. `frostycli-swbf2.patch` is the full diff against
[FrostyToolsuite](https://github.com/FrostyToolsuite/FrostyToolsuite) master
(commit ee2a587), and `SuperBundleManifest.cs` is the implementation on its own.

## Why this exists

Battlefront II mods are `.fbmod` files applied by Frosty, a Windows .NET app.
The plugin cannot drive a desktop GUI, so the question was whether the pipeline
can be run headlessly on the device. It can, except for one step.

FrostyCli (the v2 rewrite) targets .NET 8 and runs natively on SteamOS. It
supports two bundle formats and leaves the third - `SuperBundleManifest`, which
is exactly the one Battlefront II uses - as `throw new NotImplementedException()`
in a switch case, beside a 22-line stub file. Upstream has been quiet since
August 2025.

## What is proven on hardware

Everything except the last step, all on the Legion:

- FrostyCli built from source for linux-x64, self-contained, runs natively.
- Its SWBF2 profile is recognised and the game's data indexes fine.
- The SDK generates from the RUNNING game via `/proc/<pid>/mem`: 23,221 types.
  Needs `ptrace_scope=0` for the dump only, and the real pid is the one with a
  multi-GB RSS - `pgrep -f` also matches EA's proxy, which yields "No offset
  found for TypeInfo".
- A 2021-era mod converts v3 to v6 with `update-mod`.
- `mod` generates a complete ModData tree (258 MB) with exit 0.
- The engine reads that tree: the crash moved from "vanilla boot" to a crash
  DURING data load, which is how we knew the redirect worked.

Runtime plumbing that works, for the record: `user.cfg` beside the exe holding
`-dataPath Z:\...`, plus `CryptBase.dll` (v1's hook; v1 explicitly deletes
bcrypt.dll) and a per-exe DllOverride in the prefix registry. Env vars survive
dlo but not reliably EA's relauncher, which is why `user.cfg` is the channel.

## The one remaining defect

Generated data fails a read-back. Forcing FrostyCli to re-parse its own output
(clear `Caches/starwarsii.cache`, then `load` against the ModData with the exe
copied in) raises:

    Indexing Ebx -> ZStandard: "Destination buffer is too small"

Isolated by three controls, which is the useful part:

| Run | Read-back |
| --- | --- |
| ModData with NO mods | passes |
| ModData with the mod, manifest pass skipped (`FROSTY_SKIP_MANIFEST=1`) | passes |
| ModData with the mod, manifest pass active | fails |

So the generation machinery and the read-back oracle are both sound, and the
defect is in the manifest rebuild. Instrumenting the indexing loop names the
casualty:

    ASSETFAIL name="sound/characters/heroes/darthvader/
    sw02_characters_heroes_darthvader_breathing_lowhealth_var_01"
    sha1=09824daf... originalSize=3600

That asset is NOT in the mod. It is an unmodified sibling inside a bundle the
mod does touch, so rewriting that bundle's meta is breaking entries it should
have left alone.

## SOLVED: what a correct patched manifest looks like

Frosty v1 was run again on Windows with the mod actually applied
(`mods.json` non-empty, ModData 265.7 MB - essentially the same size as our
258 MB, so our output volume was never the problem). Parsing its manifest blob
settles the algorithm:

| | v1 no-mod | v1 mod applied |
| --- | --- | --- |
| resourceInfos | 1,097,786 | **2,489,514** |
| bundles | 4,777 | 4,777 |
| chunks | 180,913 | 180,913 |

Bundle and chunk counts unchanged; the resource table more than DOUBLES. v1
expands every bundle's range from the original's coalesced form into one entry
per asset. Verified by arithmetic against our own METACHECK counts:

| bundle | v1 resourceCount | our asset count (ebx+res+chunk) | 1 + assets |
| --- | --- | --- | --- |
| 2FCC648F | 291 | 81 + 30 + 179 = 290 | 291 |
| 7CDC9960 | 16,181 | 11,764 + 1,902 + 2,514 = 16,180 | 16,181 |

The original manifest gave 2FCC648F just 71 entries for those 290 assets, which
is why positional indexing failed.

So the layout is: `range[0]` = the bundle meta, then exactly one entry per
asset in meta order (ebx, then res, then chunks), each holding that asset's
COMPRESSED location - the same (file, offset, size) the catalog carries. v1
derives these by walking the catalog (`casList`) per entry; the equivalent in
v2 is `ResourceManager.GetFileInfo(sha1)`.

That makes the "per-asset rebuild" dead end below the RIGHT SHAPE after all.
The reason it still raised zstd errors is most likely chunk handling: a chunk
with a logicalOffset / firstMip needs its sub-range described (offset +
rangeStart, size = rangeEnd - rangeStart), and unmodified chunks were written
with their whole-file location instead. v1 handles chunk ranges explicitly.

Note also: the blob is written to a NEW cas each run (cas_61 for the no-mod
run, cas_62 with the mod), and with a mod applied its SIZE changes - so the
"size unchanged" observation from the no-mod run does not generalise.

## Two dead ends, so they are not repeated

- **Positional indexing.** `BinaryBundle.Modify`'s callback index does not map
  to the manifest range: one bundle reported index 74 against a 71-entry range.
  The manifest does not hold one resourceInfo per asset.
- **Rebuilding the range per asset** from each entry's loader file info. This
  turned out to be the right shape (see above) - it failed on chunk
  sub-ranges, not on the concept. Keep it, fix the chunks.

The current implementation preserves every original entry and patches only
modified ones, located by their original (cas file, offset). That is closer,
but something in the meta rewrite still mis-pairs an unmodified entry.

## Correction: the first PC reference was a no-mod run

The first ModData generated on the Windows PC had `mods.json` = `[]`. Frosty
had the mod AVAILABLE but never APPLIED it: Vortex deploys the .fbmod into
Frosty's mods folder, while Frosty only compiles what is in its own Applied
Mods list, and the Vortex integration did not add it.

So the byte-diff of that run's layout.toc against the original shows only how
Frosty RELOCATES the manifest, not how it patches entries for a real mod:

- sha1, cas index (60 -> 61) and offset (-> 0) change; size does not
- the blob is written raw at offset 0 of a brand-new cas file
- `Data/Win32` is symlinked; `chunkmanifest`, `initfs_Win32` and `layout.toc`
  are real copies

Useful mechanics, but NOT evidence about entry patching. An earlier version of
this document overstated it as a "hard invariant" - it is not, until a run with
a mod actually applied is compared.

Also worth knowing: Steam copies need Dyvinia's DatapathFixPlugin.dll in
Frosty's Plugins folder or Frosty launches the game against the ORIGINAL data
(a silent vanilla boot). Vortex cannot install it - it tries to extract a bare
.dll as an archive and reports the download as corrupt.

## The next step, and why

Frosty v1 on Windows applying the SAME mod to the SAME game, then diff its
ModData against ours: the manifest blob, the rewritten bundle meta, and the
catalog. v1 is known-good here, so the diff turns a reverse-engineering problem
into a comparison. Michael was downloading Battlefront II on the PC for exactly
this.

Specific things to compare:

1. Does v1 rewrite the bundle meta at all for a manifest-format game, or patch
   the manifest range only?
2. What does v1's manifest range look like for the touched bundle - same entry
   count as the original, or expanded?
3. Where does v1 place the rebuilt meta, and at what size?

## Things worth knowing

- `m_modDataPath` is `ModData/<pack>/<patchPath>`, NOT the pack root. Writing
  `Data/layout.toc` under it produces `Patch/Data/layout.toc` while the real
  `Data/layout.toc` stays a symlink to the original - the engine then reads
  original manifest offsets against rebuilt cas files and crashes. That crash
  named `/Data/layout.toc` in its minidump; `cmp -l` returning zero
  differences between "our" layout and the original was the tell.
- Never run the game headless with a different Proton than Steam uses. Doing so
  downgraded the prefix ("Removing newer prefix") and left EA's app demanding a
  reinstall (INST-14-1627). Steam uses Proton - Experimental for this title.
- SWBF2 keeps `layout.toc` in `Data/`, not `Patch/`. `ResolvePath` builds paths
  without checking the disk, so existence has to be tested.
