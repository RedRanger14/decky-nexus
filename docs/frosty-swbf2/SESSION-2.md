# Battlefront II, session 2: the redirect is solved

21 August 2026. The headline: **the game now loads our modded data, and a mod
visibly changed the game.** Read this before touching the port again.

## GAME_DATA_DIR is the redirect, and only that

Frosty on Windows does not pass `-dataPath` to the game. FrostyFix sets a
persistent USER environment variable, deliberately, because launchers respawn
the game and strip arguments:

    Environment.SetEnvironmentVariable("GAME_DATA_DIR", packPath,
        EnvironmentVariableTarget.User);

On the Deck that means the Wine prefix's own registry, NOT Steam launch
options (EA's relauncher eats those) and NOT `user.cfg`:

    [Environment]  in compatdata/1237950/pfx/user.reg
    "GAME_DATA_DIR"="Z:\\home\\deck\\frosty\\moddata"

Proven by a deliberately broken build: with a ModData whose `layout.toc` was
truncated to 4000 of 19901 bytes, the game booted happily BEFORE the variable
was set, and CRASHED after. Everything we chased for hours before that was the
DLL hook, never the data.

Runtime pieces that go with it: `CryptBase.dll` beside the exe (Frosty v1
deletes bcrypt.dll and ships this instead) plus a per-exe DllOverride for
`cryptbase` in the prefix registry. Note Proton rebuilds `user.reg` on prefix
upgrades and drops added keys - both the override and GAME_DATA_DIR have to be
re-asserted after that.

## First visible result

The "Rey Model for Darth Maul" mod made Maul INVISIBLE with a floating
lightsaber. That was the pipeline reaching the screen for the first time: the
appearance reference swapped, the assets behind it did not load. That mod only
modifies one ebx per bundle and ships no mesh, so it was a weak test - but it
proved the chain end to end.

## What the writer now does, and why

Structure matches Frosty v1 exactly: **one entry per asset plus the meta**
(`built = assets + 1`, verified against v1's 291-for-290 and 16181-for-16180).
Three fixes got there:

1. **Entries for ADDED assets.** A mod that adds assets to a bundle (the Kylo
   mod adds 119 ebx, 35 res, 253 chunks) needs range entries for them.
   Patching only modified assets is what produced the invisible model.
2. **Build in META ORDER.** The rebuilt meta is the order the game reads, so
   the range is assembled by walking it - patching the original range in place
   breaks alignment the moment anything is added.
3. **Look in every install chunk writer.** Mod data is written per
   SUPERBUNDLE, so an asset added to a bundle in chunk 1 may have had its data
   written through chunk 2's writer. Checking only one writer left a third of
   a model as unloadable placeholders.

Locations per asset, in order of preference: data we wrote (only when the mod
actually changes the asset), the expanded original entry, the base catalog,
then a zero placeholder.

## The remaining blocker: mod resource data

Read-back still fails on hero VO assets, always the same shape:

    PARSEFAIL name="sound/vo/mp/hero/vader/core/..._callyoda2"
    originalSize=3440 dataLen=3440 size=104

104 compressed bytes inflating to exactly the right 3440 bytes of unparseable
content. The sha1 is in `m_data`, meaning the MOD supplies that blob, so this
is not our copying of base data (a fix for that is in anyway: for this format
we no longer duplicate unchanged base assets into our cas, because the catalog
is keyed by sha1 and a bad copy SHADOWS the correct base entry game-wide).

Suspicion, not yet proven: these are resources stored as base+delta patch
entries, and `update-mod`'s v2/v3/v5 to v6 conversion does not carry the merge
- so the mod's own data for them is a delta being treated as a whole asset.
Frosty v1 reads those mod versions natively and never converts.

Ways to test next, cheapest first:

1. A mod that touches no VO/sound assets at all (pure texture or model
   replacer). If read-back passes, the blocker is scoped to those resources.
2. Apply the SAME mod with Frosty v1 on the PC and compare what it writes for
   that sha1.
3. Read `ModUpdater`'s v5 path and check how it carries `archiveOffset` /
   `compressedSize` for patch-type resources.

## Verification without boots

The oracle that made all of this possible: generate, then make FrostyCli
re-parse its own output.

    cp <game>/starwarsbattlefrontii.exe <ModData>/     # loader needs an exe
    mv Caches/starwarsii.cache /tmp/                   # force a real parse
    ./FrostyCli load "<ModData>/starwarsbattlefrontii.exe"

Exit 0 means the data is self-consistent. Controls worth keeping: a no-mod
ModData passes, and `FROSTY_SKIP_MANIFEST=1` (mod applied, manifest untouched)
passes. Diagnostics built in, all env-gated: `FROSTY_TRACE_WRITES`,
`FROSTY_VERIFY_WRITES` (every block hashes to its filed sha1 - zero mismatches
so far), `FROSTY_CHECK_META`, `FROSTY_TRACE_ASSET=<name fragment>`.

## Practical notes

- The Legion's WiFi degrades after a day of uptime; transfers start resetting.
  A reboot fixes it.
- Test mods from the Collection screen, which renders every character - no
  need to start a match.
- Vortex + Frosty are a chain, not alternatives: Vortex deploys .fbmod files,
  Frosty compiles them, and Frosty only compiles what is in its own Applied
  Mods list. Steam copies also need DatapathFixPlugin.dll in Frosty's Plugins
  folder or it launches against original data.
