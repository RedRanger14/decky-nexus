# Draft report for FrostyToolsuite

Not sent. Publishing to someone else's project is Michael's call. This is
ready to paste into an issue or a PR description when he wants it.

Repo: https://github.com/FrostyToolsuite/FrostyToolsuite
Found against master, commit ee2a587, applying mods to Star Wars Battlefront II
(2017) on Linux. Three of the four affect every game the toolsuite supports.

---

## 1. `BinaryBundle.Modify` indexes the sha1 array by the wrong counter

`FrostyModSupport/BinaryBundle.cs`. The sha1 array is global - ebx, then res,
then chunks - and walked with `j`. The res and chunk loops write `sha1[i]`,
the per-category index:

```csharp
if (inModInfo.Modified.Res.Contains(name))
{
    ResModEntry modEntry = inModifiedRes[name];
    res[i] = modEntry;
    sha1[i] = modEntry.Sha1;   // should be sha1[j]
```

The chunk loop has the same line. So modifying one res or chunk overwrites the
sha1 of EBX NUMBER i in every bundle the mod touches.

Observed: a Commando Droid retexture (one modified res) gave
`sound/weapons/lightsaber/swing/sw02_weapons_lightsaber_swing_fast_kyloren_var_03`
the texture's sha1, and separately a stormtrooper VO line and a droideka
texture in other bundles. The game crashed reading them. After changing `i` to
`j` the sha1 lands on `t_bxcommanddroid_cs` with its correct 132-byte size.

Severity: silent data corruption on any game, for any mod that modifies a res
or a chunk, which is most of them.

## 2. Placeholder sha1s reach rebuilt bundles

`FrostyModSupport/FrostyModExecutor.cs`, `ProcessModResources`, all three
branches (ebx, res, chunk). For an asset a mod only ADDS to a bundle, the
game's real sha1 is assigned to the resource AFTER the mod entry has already
copied it:

```csharp
Block<byte> data = AssetManager.GetRawAsset(entry);
m_memoryData.TryAdd(entry.Sha1, data);
modEntry = new EbxModEntry(ebx, data.Size);   // copies resource.Sha1 (zero)
resource.Sha1 = entry.Sha1;                   // too late
```

The rebuilt bundle then lists those assets with sha1 all-zeros. Observed as a
crash the moment the game drew a character whose bundle contained them.
Swapping the two lines fixes it.

## 3. Same ordering bug for `OriginalSize`, which is also not settable

Same three branches. `BaseModResource.OriginalSize` has no setter, so the
placeholder zero cannot be replaced at all. Observed:
`sound/characters/movement/hero/sw02_characters_movement_hero_darthmaul_robotlegs`
came through with `originalSize=0`, so anything reading it tried to decompress
into a zero-byte buffer. Fix is a setter plus assigning before construction.

## 4. `BundleFormat.SuperBundleManifest` is unimplemented

`FrostyModExecutor.GenerateMods` throws `NotImplementedException` for it, and
`SuperBundleActions/SuperBundleManifest.cs` is a 22-line stub. This is the
format Star Wars Battlefront II and pre-2019 Battlefield V layouts use.

We implemented it and it works on hardware. Notes for anyone reviewing, since
the format is not obvious:

- The manifest is GLOBAL, not per-SuperBundle, so the rebuild runs once after
  every cas archive is written and before the catalogs are.
- Each bundle's range is the meta plus exactly one entry per asset, in meta
  order. Verified against Frosty 1.x output: 291 entries for 290 assets,
  16,181 for 16,180.
- The ORIGINAL ranges are coalesced - one entry can cover several physical cas
  resources - so they must be expanded against the game's `cas.cat` files
  first. That expansion then lines up positionally with the rebuilt meta,
  which means unchanged assets need no lookup at all.
- Assets a mod adds that the game already owns resolve through the game's own
  asset record, not by sha1: their declared sha1 may not be in any catalog.
- Zero-size placeholder entries make the game hang on the loading screen
  rather than crash, which is a much harder symptom to diagnose. There should
  never be one.

The patch is in this folder (`frostycli-swbf2.patch`, against ee2a587). Happy
to split it into per-bug commits if that is easier to review, and to drop our
diagnostics (all env-gated) if you would rather not carry them.

## How these were found

A read-back oracle: generate a ModData pack, then make FrostyCli re-parse its
own output with the cache cleared. `FROSTY_VALIDATE_ALL=1` also decompresses
every res and chunk. On a working pack it reports
`res ok=84164 bad=0 | chunks ok=211743 bad=0`.

It never disagreed with the game: every time the check failed the game failed,
and when it passed the game ran. That might be worth having upstream as a test
command in its own right - it turns "a user says the game crashes" into a
named asset and a size.
