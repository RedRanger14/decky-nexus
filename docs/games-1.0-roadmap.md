# 1.0 Game Support Roadmap

## AGREED 1.0 SCOPE (2026-08-11)

Ten games ship in 1.0. The rule is device-tested, not built — shipping a
game nobody has run is how you collect bad reviews from people who cannot
tell whose fault it is.

| Game | Mode | Testing state |
|---|---|---|
| Stardew Valley | folder | **verified 2026-08-14: three collections including the #1 (Stardew Very Expanded), save loaded, health check corroborated against SMAPI's own log** |
| Skyrim SE | dataDir | **the most heavily tested game here.** Automated crash hunt bisected a 100+ plugin load order to a single culprit; Gate to Sovngarde and Immersive Skyrim collections played. See `gate-to-sovngarde-findings.md` |
| Fallout 4 | dataDir | **PARTIAL (2026-08-15).** Small collections work (Historical Arsenal, 2026-08-04, first time). Large ones install correctly and then misbehave in-game, because a collection's load ORDER is not applied - see "modRules" below. Vault Boy 101 (521 mods) installed, booted after a long black screen, and hung on Unlimited Companion Framework's load-order warning |
| Fallout: New Vegas | dataDir | tested, pre-adult-content |
| Slay the Spire 2 | folder | **verified 2026-08-13 in depth**: #1 collection (Mesugaki the Spire) installs and boots clean - "Loaded 11 mods (11 total)", no errors; a second collection diagnosed to one mod throwing 1,041 exceptions and repaired automatically; individual mods, dependency auto-install and the verdict store all exercised. Most of the log-parsing, verdict and auto-repair machinery was built and proven here |
| The Witcher 3 | folder | tested 2026-07-24 (script-mod ceiling documented) |
| Resident Evil 4 | folder | **verified 2026-08-14: three collections, all worked. Tested WITH adult content**; endorsement bug found and fixed here |
| Cyberpunk 2077 | folder+frameworks | **verified 2026-08-14: Welcome to Night City (283 mods) boots** once two orphaned .reds files were removed. Five frameworks install and endorse individually. **Health check corroborated against redscript's own log, verified by deliberately breaking a compile on device** - see below |
| Elden Ring | me3 | **verified 2026-08-12: Seamless Co-op played between this Deck and a real Windows player.** The strongest result here - it proves the me3 route (real exe, EAC never bootstrapped) produces a client Windows players can actually host with, not just a game that launches. Unblocks DS3, Sekiro, AC6 and Nightreign, which share the loader |

### The biggest open gap: collection load ORDER (modRules)

Found properly on 2026-08-15, testing Vault Boy 101 (521 mods) on Fallout 4.

A Nexus collection does not merely list mods, it ships **modRules** -
explicit before/after constraints between named plugin files. The device's
New Vegas collection carries **1,442** of them. We install in list order and
never read the rules, so the resulting load order is whatever install order
happened to produce.

This is already written down in `get_collection_conflicts`, which is
DISABLED for exactly this reason: judged by list order it reported 782 files
as misplaced when modRules said they were fine. The detection was sound; the
intent it compared against was wrong.

The consequence in-game, verbatim from Vault Boy 101:

> Unlimited Companion Framework has detected a potential mod conflict... move
> EFF further down your load order or UCF will not function correctly

Nothing is broken mechanically - 451 records installed, 399 plugins enabled,
0 missing from Data - and the game still does not behave as the curator
intended, because the curator's ordering was never applied.

**This is the single highest-value Bethesda item left.** It is what makes
large collections work on Windows and not here, and it needs a topological
sort over the rules rather than a heuristic.

### Bethesda titles cost several times what other games do

Michael, 2026-08-15: *"I honestly think we could smash out 3-5 games for
every 1 Bethesda title - they have taken by far the most work"*.

The record supports it. Bethesda games are the only ones carrying all of:
plugins.txt dialects (starred / listed / timestamp-ordered), load order and
its 255-slot limit, ESL flags, missing masters, archive invalidation, a
script extender whose plugins are version-locked to the exe, per-game ini
surgery, and now modRules. Cyberpunk needed a router and a log parser;
Slay the Spire 2 needed a folder and a log parser.

Worth weighing when picking what to build next: the FromSoft family is one
registry block each, and Silksong/Balatro/Palworld reuse machinery that
already exists.

### Fallout 4 is NOT over the plugin limit - our counter was

Recorded because it nearly caused a wrong decision. The activation step logs
`load order now 362 of 254`, which counts every enabled plugin against the
FULL-slot limit. ESL-flagged plugins do not occupy those slots. Measured
from the plugin headers on device with 521 mods installed:

| | |
|---|---|
| enabled in Plugins.txt | 399 |
| ESL-flagged (free) | 167 |
| **full slots used** | **232 of 255** |
| missing from Data | 0 |

Every large Fallout 4 collection is ESL-heavy by design, so that counter is
wrong on all of them. Fix it before trusting any slot-pressure warning.

### Cut from 1.0: Fallout 3

Michael, 14 August 2026: "Lets remove fallout 3 from the 1.0 list."

It is the only supported game whose required modding tool cannot be made to
work here. The Anniversary Patcher downgrades Fallout3.exe to 1.7.0.3 so
FOSE can hook it, and on this device the patched exe does not boot - through
Proton Experimental, 9, 10 or 8, on a repaired prefix, with the game folder
cleaned and the load order verified empty. The patcher's output is
byte-identical every run, so it is not corruption.

Without that patcher there is no FOSE, and without FOSE most Fallout 3
collections - including Fallout Rebirth+, its most popular - do nothing.

What was learned here still applies to every other Bethesda game and is
already shipped: reset now sees the game folder, tools run under the Proton
the game uses, staged tool files are cleaned up, direct (non-Nexus) sources
in collections install, and deploy refuses to interrupt a running tool.

Worth revisiting after 1.0 if the patcher gains a Linux-friendly build or
FOSE supports 1.7.0.4 - neither is in our control.


**Moved to post-1.0** (built, never run on device): Hollow Knight
Silksong, Palworld, Mount & Blade II: Bannerlord.

**Post-1.0 queue**, cheapest first: the FromSoft family (Dark Souls 3,
Sekiro, Armored Core 6, Nightreign) is one registry block each - the me3
tier is game-agnostic and `ME3_GAMES` already maps all five domains - but
it inherits whatever Elden Ring has not proven yet. Then Silksong,
Palworld, Bannerlord (verify what exists), then Skyrim 2011, Balatro,
RDR2, BG3.

### Regression pass required before 1.0

The adult-content gate became account-driven in v0.37.0 (site preference
+ age verification, both required). Every game tested before that saw a
smaller catalogue than users will, so browse/search/install needs
re-running on: Stardew, Fallout 3, Fallout NV, Slay the Spire 2,
Cyberpunk 2077. RE4 was tested after the change and does not need it.

## RESOLVED (v0.182.0/v0.183.0): the health check was recommending the mod
## that broke the game

Welcome to Night City installs 283 mods and deliberately omits seven their
Nexus pages call required. The health check reported all seven as faults -
including **General Shadows Fixes**, whose orphaned `.reds` had been failing
every compile for weeks and taking the entire script stack with it. The fix
on offer was the cause of the fault.

The discriminator is not the curator's choices. It is the game's own log.

Established before writing any code, by rerunning the health check's own
query against all 268 Cyberpunk records on device:

| mod id | name | wanted by |
|---|---|---|
| 23074 | Romanced Enhanced Showers Feature | 4 |
| 5145 | Wardrobe Anywhere | 1 |
| **20405** | **General Shadows Fixes** | 1 |
| 9496 | Enable Finisher Ragdolls | 1 |
| 4789 | Cookedprefabs Nulled | 1 |
| 790 | Appearance Menu Mod | 1 |
| 14130 | Bug Fix - Base Fists and Arm Cyberware Attack Speed Fix | 1 |

Exactly seven, and mod 20405 is literally named "General Shadows Fixes"
against an orphaned file called `GeneralShadowsFixes.reds` - so the name
match the design needed was confirmed rather than assumed. A second rule
("the required mod is already on disk under another name") was designed and
then **dropped**: none of the seven matches anything in r6/scripts or any
record, so it would have been a guess.

What shipped:

- **`_redscript_report`** reads only `redscript_rCURRENT.log` - the rotated
  logs are full of problems since fixed - and matches failing `.reds`
  basenames against install records.
- **A collection's omission is informational unless the game complains.**
  Gated on a *positive* clean compile, so with no log nothing changes and
  the other nine games report exactly what they did before.
- **No completion line = every script mod is off**, not just the broken one.
  That is the page's headline and outranks any count of missing
  requirements.
- **An orphaned script named after a mod we were about to recommend becomes
  a verdict.** This is the loop closing: the plugin refuses on evidence
  instead of re-learning by breaking the game.
- **A mod owning a script that killed the compile is switched off**, not
  offered - one bad `.reds` is the single mod standing between the user and
  everything else. Only when the compile actually died; "errored but
  finished anyway" has never been observed, and acting on an unobserved
  state is how a check starts crying wolf.
- **Cyberpunk mods can be switched off at all now.** `mode: "files"` used to
  answer "no toggle - uninstall it instead", which was the wrong answer to a
  real question. They park outside the game like the dataDir tier. Gated on
  `target: "."` because Palworld's LogicMods share the mode and are
  deliberately untogglable - the suite caught that regression.

### Verified on device 2026-08-14, by breaking a compile on purpose

A planted orphan plus four bad lines appended to a real mod's script, with a
backup, then restored byte-identically afterwards. Every path exercised:

| behaviour | result |
|---|---|
| clean log demotes the seven | ✅ |
| dead compile headline | ✅ |
| orphan → verdict for mod 20405 | ✅ |
| owned failing script switched off (all 3 files parked) | ✅ |
| files-mode toggle off, then back on | ✅ both directions |
| verdict outlives the file that created it | ✅ |

**Two defects the tests could not have found**, both from Michael running it:

1. *"am I clicking install the 6 missing?"* - with the stack dead, the six
   omissions revert to looking like faults (correctly: there is no clean-log
   evidence either way), and the page offered to bulk-install six mods a
   curator deliberately left out on top of a setup that was not running.
   The button is now suppressed while the stack is dead.
2. The auto-disable acted on every read of the same log, so a user who
   switched a mod back on would lose it again the moment they reopened the
   page, with nothing explaining why. Now stamped and acted on once per log;
   a later session that says the same thing acts again.

Note for Crucible: both verdicts written during this test were artefacts of
deliberate sabotage. The Better Armor Tooltip one was removed afterwards -
**a verdict earned from a synthetic failure is a false record**, and a
harness that breaks things to test them will generate these constantly.

### RESOLVED (v0.184.0): Cyberpunk CET Lua mods now install

`_route_cp77_payload` recognised game-root prefixes and bare `.archive`
files, so a CET mod shipping only its Lua folder matched nothing and was
refused as "no Cyberpunk mod layout found" - a large Nexus category for this
game turned away.

Structure taken from the CET wiki's own Mod Structure page rather than
inferred: mods live in `bin/x64/plugins/cyber_engine_tweaks/mods/<my_mod>/`,
`init.lua` is the entry point CET looks for, and extra files may sit in that
folder or a subfolder of it. So **init.lua is the detector** - the one file
every CET mod must have.

- The author's folder name is preserved. It IS the mod's name to CET and to
  anything referencing it; a name derived from the Nexus page would break
  both. A name is derived only when `init.lua` sits at the archive root with
  no folder of its own.
- Checked BEFORE the bare-archive sweep, and archives inside a CET mod stay
  with it. Otherwise a mod shipping both would have had its `.archive` taken
  and its Lua left behind - half a mod installed, reported as success.
- A mod already shipping the full `bin/...` path still routes as a `bin`
  root, so it is not nested inside itself.
- No `init.lua` is still refused, and the message now names what was looked
  for.

**Not yet run on device.** Unit-tested only (9 tests).

### RESOLVED (v0.186.0-v0.191.0): reset could not sweep four of Cyberpunk's
### five mod directories

`modWriteDirs` lists five, but the device's `vanilla_extra_baseline` held
only `r6/scripts` - the other four do not exist in a vanilla install, so
`os.listdir` raised and the error was swallowed. The sweep deliberately
skips any directory with no baseline ("we do not know what the GAME put
there"), so a CET mod, RED4ext plugin or tweak whose install record was lost
was an orphan nothing could ever find. Exactly the failure that left two
`.reds` files killing the script stack for weeks, with four more places to
produce it.

**A directory a vanilla install does not have provably contains nothing of
the game's**, so that is now recorded as an empty baseline rather than a
missing one. A directory that exists but cannot be READ is still skipped -
claiming empty there would have reset delete the contents of something we
merely failed to open.

#### Doing it on device found three more, none of which a test could have

Run 2026-08-15, with Michael resetting and the numbers checked after each.

1. **The re-take skipped the one game that needed it.** `reset_game_modding`
   re-takes the baseline afterwards, because "THIS is the only moment we can
   be sure what vanilla looks like" - but it was gated on the mods folder
   existing AND being non-empty. Cyberpunk's mods folder is
   `archive/pc/mod`, which the game does not ship, so a correct reset
   removes it and the whole block was skipped. His reset completed with no
   errors and changed no baseline at all.

2. **Both baselines were protecting mod files from every sweep.**
   `vanilla_baseline` held 16 `.archive` files with names like
   `WINGDEER_FemV_HAIR_BELLE_NO32`; `vanilla_extra_baseline` held four more
   in `r6/scripts`, including `ZKV_EnableFinisherRagdolls.reds` and a folder
   `Arm Cyberware Attack Speed Fix` still sitting on disk. Both were
   captured when the game was already modded. A baseline holding mod files
   PROTECTS them, which is the exact opposite of its purpose, and no number
   of clean resets could dislodge them. Cleared by hand, with a backup.

3. **An empty root baseline switched the game-folder check off.**
   `vanilla_root_baseline` was `[]` while 17 vanilla files sat in the game
   root, and the leftover report is gated on `if root_baseline:` - so a mod
   DLL beside the exe would never have been reported. That is the Fallout 3
   failure the check was built for. Reset now re-takes this one too.

Final state, verified: mods baseline `[]`, all four extra directories `[]`,
root baseline 17 files, stamped at build 20383525.

**Worth remembering for every other game:** a baseline captured on a device
that was modded before the plugin ever saw it is worse than no baseline,
and until now nothing could repair one. Any long-standing install may have
the same problem, and the remedy is the same - reset, which now re-takes all
three baselines honestly.

### Baseline health, all games (audited on device 2026-08-15)

The audit found the same defect three more times, each in a different place
where an absent or empty value was read as "no information":

| where | absent/empty meant | now |
|---|---|---|
| `vanilla_extra_baseline[dir]` | directory never swept | absent = game owns nothing there |
| `vanilla_root_baseline` | game-folder check silently off | re-taken on every reset |
| `baseline_build` | **"the game has not changed"** | unknown = report, never delete |
| `vanilla_baseline == []` | mods folder never swept | recorded-empty is a real baseline |

The `baseline_build` one was the dangerous one: Skyrim SE's was unstamped,
so a Bethesda patch followed by a reset would have swept the new vanilla
files as orphans, on the most heavily tested game here.

State after the audit:

| game | records | baseline | note |
|---|---|---|---|
| Cyberpunk 2077 | live | ✅ stamped, all 4 extra dirs | done first, three defects found doing it |
| Skyrim SE | 0 | ✅ stamped 13189953 | Data was provably vanilla (36 = 36) |
| Slay the Spire 2 | 0 | ✅ stamped 23811903 | mods folder empty |
| Fallout 4 | 148 | ⚠ none at all | 486 files in Data; reset sweeps nothing today |
| Stardew | 85 | ⚠ unstamped | live setup |
| Witcher 3 | 77 | ⚠ unstamped | live setup |
| Elden Ring | 1 | ⚠ unstamped | me3 keeps mods outside the game folder - low impact |
| New Vegas | 0 | ❌ polluted | ~16 of 52 entries are TTW mod files, on disk AND baselined |
| Fallout 3 | 0 | n/a | cut from 1.0, game not installed |

The ⚠ rows are **safe, not broken**: an unstamped baseline now reports
rather than deletes. They self-heal the next time each game is reset for a
real reason, so there is no case for destroying a tested setup to fix one.

New Vegas is the only one needing a reinstall - a reset cannot help, because
the mod files are on disk and baselined, and Steam's verify restores missing
files without removing extra ones.

### How a baseline gets polluted, and why that is the safe error

After a game update, reset reports leftovers instead of deleting them (it
cannot tell a new game file from a mod orphan) and then re-takes the
baseline from the folder as it stands - which blesses any orphan still
there. That is almost certainly how New Vegas ended up with Tale of Two
Wastelands content in its baseline.

It stays that way on purpose. Blessing an orphan leaves an untidy folder;
excluding it risks deleting genuine new game content on the next reset.
Those are not comparable.

**Consequence for early-access games** (Michael, 2026-08-15: "sts2 is in
early access so it will get frequent updates"): every update moves the build
id, so the first reset after each one reports rather than sweeps, and
re-stamps. The cheap habit that avoids drift is to reset while nothing is
installed - the folder is then unambiguously clean, so there is no orphan to
bless. That is exactly the state StS2 was reset in.

## Decky store submission: pre-PR checklist (researched 2026-08-11)

Sources: decky-plugin-database README + `.github/PULL_REQUEST_TEMPLATE/
plugin_addition.md`, wiki `plugin-dev/review-and-testing`.

**Blocking decision.** The plugin-addition template requires the
declaration *"Generative AI was NOT used to write a majority of the code
I am submitting"*, and the template header warns PRs may be denied for
suspected AI usage. This codebase is majority AI-written and every commit
carries a `Co-Authored-By: Claude` trailer. Michael's call (2026-08-11):
submit anyway with a written explanation to the Decky team, and look at
alternative distribution if they decline.

**Repo hygiene**
- [ ] Delete `defaults/defaults.txt` (reviewers call this out explicitly)
- [ ] Delete `assets/` unless it is an `icon.png` the plugin actually uses
- [x] `pnpm-lock.yaml` on `lockfileVersion: 9.0` (CI requirement)
- [x] LICENSE present (BSD 3-Clause)
- [x] No unused `backend/` directory

**plugin.json**
- [ ] Remove the `debug` flag before submitting
- [ ] Replace the store `image` - it currently points at Decky's own
      PluginLoader OpenGraph placeholder. No dimensions are documented
      anywhere in the wiki, templates or database; the template default
      is a GitHub OpenGraph URL, which renders 1280x640 (2:1). A square
      logo will letterbox.
- [ ] Description should lead with the Gaming Mode differentiator

**Testing burden - note the stricter path**
We download me3 from `github.com/garyttierney/me3/releases`, a
third-party pre-built binary. That moves us off the easy route (Stable
*or* Beta) onto **SteamOS Preview channel testing required**. A reviewer
will also follow that URL and assess it.
- [ ] Third-party tester posts a report on the PR (required to merge)
- [ ] Optionally test two other plugins' PRs - not required, but
      deprioritised without it
- [ ] Note the momentum rule: no visible testing progress for ~2 weeks
      and the PR gets closed



Target list from Nexus Mods internal analytics (unique users starting mod
downloads). "Modding Tools" (7.5M uniques, #1) is the tools domain, not a
game — excluded from plugin scope.

All Nexus data below verified live against the v2 API on 2026-07-21.

## Verified Nexus registry data

| Game | Steam appId | Nexus domain | gameId | Mods | Uniques |
|---|---|---|---|---|---|
| Stardew Valley ✅ shipped | 413150 | `stardewvalley` | 1303 | 32,186 | 4.4M |
| Skyrim Special Edition ✅ shipped | 489830 | `skyrimspecialedition` | 1704 | 135,505 | 3.1M |
| Cyberpunk 2077 | 1091500 | `cyberpunk2077` | 3333 | 22,581 | 2.4M |
| Elden Ring ✅ built (me3) | 1245620 | `eldenring` | 4333 | 7,317 | 2.2M |
| Fallout 4 | 377160 | `fallout4` | 1151 | 75,193 | 1.5M |
| Baldur's Gate 3 | 1086940 | `baldursgate3` | 3474 | 18,598 | 1.3M |
| Fallout: New Vegas | 22380 | `newvegas` | 130 | 41,363 | 1.3M |
| Red Dead Redemption 2 | 1174180 | `reddeadredemption2` | 3024 | 5,560 | 1.2M |
| Skyrim (2011) | 72850 | `skyrim` | 110 | 73,002 | 1.2M |
| Resident Evil 4 (2023) | 2050650 | `residentevil42023` | 5195 | 5,075 | 0.95M |
| M&B II: Bannerlord | 261550 | `mountandblade2bannerlord` | 3174 | 8,230 | 0.93M |
| Fallout 3 (user add) | 22300/22370 GOTY | `fallout3` | 120 | 17,150 | — |
| Palworld (user add: Deck-popular) | 1623730 | `palworld` | 6063 | 2,577 | — |
| The Witcher 3 (user add) | 292030 | `witcher3` | 952 | 8,807 | — |
| Balatro (bonus) | 2379780 | `balatro` | 6217 | 721 | — |
| HK: Silksong (bonus) | 1030300 | `hollowknightsilksong` | 8136 | 773 | — |
| Subnautica 2 (bonus) | TBD (early access) | `subnautica2` | 9198 | 244 | — |

Subnautica 2: UE5 like Palworld — its top mod is a UE4SS build, so
expect the same pak-easy / UE4SS-hard split; reuses the v0.3.0 UE4SS
machinery. Research pass needed for its paths.

## Research: The Witcher 3 next-gen (completed 2026-07-22)

MODERATE — and better than feared: **REDmod does not apply to TW3**
(that was a Cyberpunk-only mechanism; the confusion came from a
site-wide news article). No deploy step, no launch flag.

- Mods = `mod*`-prefixed folders into `<game>/mods/` (folder mode;
  create dir, lowercase). Many mods also ship `dlc*` folders → 
  `<game>/dlc/`. REDkit-era (4.04a+) mods use the same folders.
- Menu-mod XMLs → `bin/config/r4game/user_config_matrix/pc/` AND the
  filename must be appended (semicolon-terminated) to dx11filelist.txt
  + dx12filelist.txt — automatable, idempotent (Deck runs DX11).
- **Script conflicts are the hard wall**: two mods editing the same
  `.ws` → fatal compile error at launch; Script Merger is Windows GUI.
  v1 = allow script-free mods + non-colliding single script mods,
  refuse collisions with a clear message (collision scan = compare
  `content/scripts/**` paths across installed mods — we have
  manifests). Linux CLI merger (apocalyptech/w3scriptmerge) = v2 spike.
- Load order: alphabetical; `mods.settings` INI in prefix Documents
  for priority/disable (v2).
- Exe: bin/x64/witcher3.exe (+x64_dx12 variant); dir "The Witcher 3";
  ~200-bundle hard limit remains on 4.04 (fine for v1 scale).
- v1 mapping: folder mode + a dlc-component extension + filelist
  automation + script-collision gate.

Deck-popularity picks (user, 2026-07-21): Palworld + RDR2 promoted into
the 1.0 wave; Balatro + Silksong as bonus targets. Note Balatro's and
Silksong's #1 mods ARE their frameworks (Steamodded, BepInEx 5) hosted
on Nexus — framework installs route through the API with author credit,
same as SMAPI/SKSE.

Top mod per game (API sanity check): CP77 → Cyber Engine Tweaks,
Elden Ring → Seamless Co-op, FO4 → CBBE, BG3 → BG3 Mod Fixer,
FNV → NVAC, RDR2 → Rampage Trainer, Skyrim → SkyUI,
RE4 → Fluffy Mod Manager, Bannerlord → MCM.

## Architecture fit (draft — device verification pending for all)

Our existing install modes and what each game needs:

- **folder** (per-mod folders in a mods dir): StS2, Stardew (via SMAPI).
  Likely fits: **Bannerlord** (Modules/<Name>), with a module-activation
  step on top.
- **dataDir** (merge into Data/, per-file manifests, Plugins.txt `*`
  activation, script-extender framework): Skyrim SE. Likely fits:
  **Fallout 4** (same engine family, F4SE), **Fallout: New Vegas** and
  **Skyrim 2011** (older variant — plugins.txt may activate by plain
  listing, no `*` prefix; needs a `pluginsTxtStyle` registry flag).
- **New mode needed — pak/patch drop-in**: **RE4 remake** (RE Engine
  `re_chunk_000.pak.patch_XXX.pak` sequential naming in the game root),
  possibly simple enough as a dataDir variant with a naming allocator.
- **New mode needed — pak + activation manifest**: **Baldur's Gate 3**
  (.pak into the prefix's Larian AppData Mods dir + modsettings.lsx
  entries with UUID/metadata read from the pak). Patch-7 native mod
  support may simplify this significantly.
- **Root-file frameworks, feasibility TBD**: **Cyberpunk 2077**
  (archive/pc/mod drop-ins are easy; CET/RED4ext under Proton need a
  winhttp dll override), **Elden Ring** (Mod Engine 2 + anti-cheat
  bypass — support decision pending research), **RDR2** (script hook +
  Rockstar launcher complications).

## Suggested build order (updated with all research + user picks)

Tier 1 — reuse existing machinery, biggest wins first:
1. **Fallout 4** — dataDir clone of SSE (registry entry landed,
   experimental; needs device verification). Biggest catalog.
2. **Hollow Knight: Silksong** — copyRoot + folder mode as-is; the
   Deck modding tools gap makes this a visibility win.
3. **Palworld (pak tier)** — folder mode + mkdir; Deck-popular.
4. **Cyberpunk 2077 (archive tier)** — drop-in .archive files.
5. **Bannerlord** — folder mode into Modules/ + LauncherData.xml step.

Tier 2 — one new mechanism each:
6. **Balatro** — prefix-AppData mods root + two-part framework.
7. **Fallout: New Vegas** — listed plugins.txt (done) + mtime ordering
   + invalidation ini + xNVSE overrides recipe.
8. **RE4 remake** — pak-patch sequence allocator.
9. **RDR2** (user-promoted) — LML/ScriptHook copyRoot + overrides
   launch option + "story mode only" warning.
10. **Skyrim 2011** — FNV sibling (still sold on Steam).

Tier 3 — big or gated:
11. **Baldur's Gate 3** — LSPK parser + modsettings.lsx + dual-build.
12. **Fallout 3** — dual SKUs + FOSE needs exe downgrade (Anniversary
    Patcher run inside the prefix).
13. **Palworld UE4SS tier** — blocked on the Proton subfolder bug.
14. ~~**Elden Ring** — needs a product decision (anti-cheat/offline).~~
    Decision made (boss-approved with safeguards, 2026-08-06); built in
    v0.52.0 — see the me3 section below.

## Cross-game platform work this exposes

- `pluginsTxtStyle: "starred" | "listed"` for Gamebryo-era games.
- Display-mode doctor (shipped for Skyrim SE) generalizes: FO4 has the
  same exclusive-fullscreen crash class; add its ini block when the FO4
  entry lands.
- Framework tiers: some games have several semi-required frameworks
  (CP77's CET/RED4ext/ArchiveXL/TweakXL) — the single-framework Step 1
  needs to become a checklist.
- Option-folder chooser (shipped) will matter everywhere.

*(Research findings from the per-game deep dives land below as they
complete.)*

---

## Research: Elden Ring / RDR2 / RE4 / Bannerlord (completed 2026-07-21)

### Mount & Blade II: Bannerlord — EASY, ship in 1.0
- Mods are folders under `Modules/<Name>/`; the launcher detects any
  folder with a valid `SubModule.xml`. [VERIFIED docs.bannerlordmodding.com]
- Activation: `LauncherData.xml` at
  `compatdata/261550/pfx/drive_c/users/steamuser/Documents/Mount and
  Blade II Bannerlord/Configs/LauncherData.xml` — per-module
  `<UserModData><Id>…</Id><IsSelected>true</IsSelected></UserModData>`,
  after the official modules (Native, SandBoxCore, CustomBattle,
  SandBox, StoryMode). File doesn't exist until the launcher runs once.
  The `Id` comes from the mod's SubModule.xml, NOT the folder name.
  Vortex manages it the same way (good precedent).
- `_MODULES_*…*_MODULES_` command-line bypass exists but is riskier via
  %command% (Steam starts TaleWorlds' launcher) — prefer the XML.
- ProtonDB Gold, no anti-cheat. Fits our **folder mode** plus a new
  small "module activation" adapter (XML edit + Id extraction).

### Resident Evil 4 (2023) — EASY–MEDIUM, ship in 1.0 (pak tier)
- RE Engine loads `re_chunk_000.pak.patch_XXX.pak` **sequentially** from
  the game root; a mod pak must take `<highest existing>+1`, gaps break
  the chain. [VERIFIED FluffyQuack's REtool docs]
- So: extract the .pak from the (Fluffy-format) archive, compute next
  number, drop in root. Uninstall must renumber anything above (our
  per-file manifests can track this). Official updates shipping a new
  patch pak collide with mod numbering — needs update-breakage
  detection (known community pain point).
- Loose `natives/` files do NOT load without REFramework's loose-file
  loader — v1 supports pak-format mods only, refuse loose-file mods
  with a clear message.
- REFramework itself = `dinput8.dll` in root + launch option
  `WINEDLLOVERRIDES="dinput8=n,b" %command%`; works on Deck.
- New install mode needed: **pakPatch** (sequence allocator + manifest).

### Red Dead Redemption 2 — MEDIUM, promoted to 1.0 (user: Deck-popular)
- ScriptHookRDR2 (`ScriptHookRDR2.dll` + `dinput8.dll` in root) +
  Lenny's Mod Loader (`vfs.asi`, `lml.ini`, dlls + `lml/` folder); asset
  mods = `lml/<ModName>/` with per-mod install.xml. `lml/mods.xml`
  auto-generates and auto-enables — good for headless installs.
- Requires launch option
  `WINEDLLOVERRIDES="dinput8,ScriptHookRDR2=n,b" %command%` on Deck
  [VERIFIED community tutorial], plus Rockstar launcher interposing and
  a hard "story mode only — never Red Dead Online" warning.

## Research: Palworld / Balatro / Silksong (completed 2026-07-21)

### Hollow Knight: Silksong — EASY, everything already exists
- BepInEx 5 package IS on Nexus (mod 26 / newer 986): standard zip into
  game root (`BepInEx/` + winhttp.dll + doorstop_config.ini) → our
  copyRoot framework mode as-is. Launch option
  `WINEDLLOVERRIDES="winhttp=n,b" %command%` + force Proton.
- Mods = DLLs into `BepInEx/plugins/<name>/` → our folder mode.
- Deck quirk: Steam defaults to the Windows build under Proton anyway
  (native build has controller bugs); guides standardize on Proton.
- **r2modman's Deck launch for Silksong is currently broken** (open
  issue) — a genuine opening for us to be THE way to mod it on deck.
- Dir "Hollow Knight Silksong"; exe "Hollow Knight Silksong.exe".

### Balatro — EASY-MEDIUM
- Stack: lovely-injector (`version.dll` into game dir +
  `WINEDLLOVERRIDES="version=n,b" %command%`) + Steamodded, which is
  itself just a mod folder — and it's Nexus mod 45 (author credit ✓).
- Mods live in the PREFIX, not the game dir:
  `compatdata/2379780/pfx/.../AppData/Roaming/Balatro/Mods` — needs a
  prefix-AppData mods root (backend already has the path helper).
  No activation file; SMODS scans folders → folder mode.
- lovely-injector is GitHub-distributed (like BG3SE) — framework
  install can't route through Nexus for that piece.
- Dir/exe: Balatro / Balatro.exe (fused LÖVE binary).

### Palworld — pak tier VERIFIED; UE4SS tier BUILT in v0.3.0 (2026-07-22)
UE4SS support shipped: framework = Nexus mod 3405 (Linux-fixes fork)
via copyRoot into Pal/Binaries/Win64 + dwmapi override; Lua/native
mods → ue4ss/Mods folders with enabled.txt; Blueprint paks → LogicMods
flat with per-file records. Awaiting on-device verification. Reuse for
Subnautica 2 when its entry lands. Original assessment below.

### (superseded) Palworld — pak tier EASY, UE4SS tier HARD (gate it)
- Pak mods: create `Pal/Content/Paks/~mods/`, drop paks, zero Proton
  config → folder mode with fixed subpath.
- UE4SS: Palworld uses a FORK (Okaetsu experimental-palworld; Nexus
  mirrors 3035/1121, Linux-fixes fork 3405). Installs into
  `Pal/Binaries/Win64` (not root), needs `dwmapi=n,b` override, and has
  an OPEN Proton bug (ue4ss subfolder DLLs not loading, #1189). Its
  `ue4ss/Mods/mods.txt` enable file is directly analogous to our
  plugins.txt code. Verdict: ship pak-only, gate UE4SS behind the
  Linux-fixes fork later.
- Palworld 1.0 (July 2026) added Steam Workshop incl. a UE4SS item —
  detect/refuse double-injection if the Workshop copy is present.
- Dir "Palworld"; detect process on Palworld-Win64-Shipping.exe.

## Research: Bethesda family (completed 2026-07-21)

### Fallout 4 — EASY (dataDir clone), first new entry
- Plugins.txt: `AppData/Local/Fallout4/Plugins.txt` (no space), starred
  format like SSE (since patch 1.5). Never write lines for Fallout4.esm
  or DLC — the game hardcodes them. [VERIFIED LOOT docs]
- F4SE: Nexus mod **42147**; current 0.7.8 ↔ runtime 1.11.221. Archive
  = wrapper folder w/ `f4se_loader.exe` + versioned dll + Data/Scripts
  (+ ignorable `src/`) → our copyRoot flatten works as-is.
- **Loose files need an ini block** in `Documents/My Games/Fallout4/
  Fallout4Custom.ini` (create if absent): `[Archive]
  bInvalidateOlderFiles=1, sResourceDataDirsFinal=`. Our ini-doctor
  machinery covers this (needs create-if-missing).
- Same exclusive-fullscreen crash class as SSE → same displayFix
  (Fallout4Prefs.ini [Display]).
- Exes: `Fallout4.exe` / `Fallout4Launcher.exe`; launch = same bash
  substitution recipe as SKSE (extrapolated — verify on device).

### Fallout: New Vegas — MEDIUM
- Plugins.txt `AppData/Local/FalloutNV/plugins.txt`, **plain listing**
  (= our "listed" style, confirmed). BUT load order = **plugin file
  timestamps**, not the file — needs an mtime-staggering step on
  install. [VERIFIED LOOT docs]
- ArchiveInvalidation required: `bInvalidateOlderFiles=1` +
  dummy-BSA prepend to SArchiveList in Fallout.ini.
- xNVSE (Nexus **67883**): wrapper archive, `nvse_loader.exe`; on Deck
  NO launcher substitution needed — its steam-loader dll auto-injects,
  with `WINEDLLOVERRIDES="nvse_1_4.dll=n,b;nvse_editor_1_4.dll=n,b;
  nvse_steam_loader.dll=n,b;d3dx9_38.dll=n,b" %command%`
  [VERIFIED Steam Linux modding guide]. Exes: FalloutNV.exe /
  FalloutNVLauncher.exe.

### Skyrim 2011 — MEDIUM (and still purchasable!)
- Base game 72850 IS still sold on Steam (LE bundle was the delisting).
- plugins.txt `AppData/Local/Skyrim/plugins.txt`, plain listing; line
  order = load order (patch 1.4.26+) — "listed" style, no timestamp
  step needed.
- SKSE classic: silverlock only (skse_1_07_03.7z under /beta/), ALSO a
  free Steam app (365720) — possibly the lowest-friction install path.
  Auto-loads via skse_steam_loader.dll on Steam launches; Proton
  behavior untested. Exes: TESV.exe / SkyrimLauncher.exe.

### Fallout 3 — MEDIUM-HARD (the complicated one)
- TWO SKUs, both sold: base 22300 → `Fallout 3`, GOTY 22370 →
  `Fallout 3 goty` — two registry entries, shared
  `AppData/Local/Fallout3/plugins.txt` (listed + timestamp order).
- ArchiveInvalidation like FNV (canonical mod: fallout3 mod 944).
- **FOSE doesn't support the current Steam exe (1.7.0.4)** — the
  community fix is lStewieAl's Anniversary Patcher (mod 24913):
  downgrades to 1.7.0.3, 4GB-patches, and makes FOSE auto-load with no
  launch options. But it's a Windows exe we'd have to run inside the
  prefix — real implementation cost. FOSE archive is FLAT (no wrapper)
  → framework installer needs a wrapper/flat flag.
- Proton quirks: silent radio needs `protontricks quartz lavfilters`;
  multicore freeze needs `bUseThreadedAI=1` + `iNumHWThreads=2` in
  FALLOUT.INI. Exes: Fallout3.exe / FalloutLauncher.exe (not
  Fallout3Launcher).

### Cross-cutting (Bethesda)
- plugins.txt dialects: starred+ordered (SSE/FO4) · listed+ordered
  (Skyrim 2011) · listed+timestamp (FNV/FO3 — needs mtime staggering).
- Framework archives: wrapper (SKSE/F4SE/xNVSE) vs flat (FOSE); all
  carry `src/` junk worth excluding from copyRoot.
- Ini writes: FO4 loose-files block, FNV/FO3 invalidation, FO3
  multicore — all fit the ini-doctor machinery.

## Research: BG3 + Cyberpunk 2077 (completed 2026-07-21)

### Cyberpunk 2077 — archive tier EASY, framework tier MEDIUM
- **Archive mods**: `.archive` files drop into `<game>/archive/pc/mod/`,
  auto-loaded, alphabetical override, no deploy step, no REDmod needed —
  current through patch 2.2. [VERIFIED wiki.redmodding.org]
  Fits a near-**folder** mode (flat file drop with manifests).
- **Framework stack** (the "core" mods content mods require), all Nexus
  IDs verified: Cyber Engine Tweaks **107** (bin/x64/version.dll proxy +
  plugins dir), RED4ext **2380** (bin/x64/winmm.dll + red4ext/), redscript
  **1511** (engine/tools + r6/scripts/), ArchiveXL **4198** and TweakXL
  **4197** (RED4ext plugins). Multi-framework = Step-1 checklist work.
- **Proton launch option** for CET+RED4ext:
  `WINEDLLOVERRIDES="winmm,version=n,b" %command%` (Proton 8+, no
  winetricks). The Nexus Mods App ships full CP2077 Deck support doing
  exactly this — good precedent.
- **REDmod**: skip. Requires redMod.exe deploy + -modded flag; raw
  archive/pc/mod placement is the community norm and needs neither.
- Exe: `bin/x64/Cyberpunk2077.exe`; install dir "Cyberpunk 2077".

### Baldur's Gate 3 — MEDIUM-HARD, new machinery
- .pak mods → Proton prefix
  `compatdata/1086940/pfx/.../AppData/Local/Larian Studios/Baldur's
  Gate 3/Mods`; **placement alone is NOT enough** — paks not listed in
  `PlayerProfiles/Public/modsettings.lsx` are treated as disabled.
  [VERIFIED Nexus Mods App dev docs]
- modsettings.lsx: `ModOrder` (UUID sequence) + `Mods`
  (ModuleShortDesc: Folder/MD5/Name/UUID/Version64, GustavDev stays
  first). Metadata comes from `meta.lsx` inside the pak (LSPK format,
  v18, LZ4 file list — LSLib is the reference implementation).
- **Wrinkle**: BG3 has a NATIVE Linux Deck build — no pfx there; paths
  live under `~/.local/share/Larian Studios/...`. Must detect which
  build. Patch-7 in-game mod manager (mod.io) coexists with external
  paks but rewrites modsettings.lsx when touched.
- BG3SE (script extender): DWrite.dll into bin/ +
  `WINEDLLOVERRIDES="DWrite.dll=n,b" %command%`; GitHub-only official
  distribution (can't route through Nexus for credit — like SKSE's
  silverlock situation, but without a Nexus mirror we control).
- Exes: bin/bg3.exe (Vulkan), bin/bg3_dx11.exe; dir "Baldurs Gate 3".
- Cost: LSPK parser + lsx editor + dual-build detection. Highest
  engineering cost of the viable list — schedule after the cheap wins.

### Elden Ring — HARD, defer (1.2+)
- All modding runs through Mod Engine: EAC skipped, offline only; using
  mods online risks bans. Mod Engine 2 is archived and breaks under
  naive Proton launch; the viable path is **me3** (garyttierney/me3):
  native Linux/Deck support, scriptable CLI (`me3 launch -p profile`),
  offline by default, save isolation. A later integration would bundle
  me3 + generate `.me3` profiles. Not 1.0 material; needs a product
  decision on the anti-cheat/offline UX.

## Robustness backlog (found during Skyrim reset, 2026-07-23)

- **Crash-safe install journal**: installs that die between file-copy and
  record-write orphan their files forever (the Skyrim reset surfaced ~100
  unrecorded plugins + ~2,600 loose meshes/textures from the pre-v0.14
  crashed collection runs). Write the record incrementally (journal the
  file list as it's copied, mark complete at the end) so an interrupted
  install is visible and cleanable instead of invisible.

## Feature backlog: My Mods "Health check" (requested 2026-07-23)

A diagnostics section on the My Mods screen that surfaces conflicts,
errors and warnings per game: missing masters (esp requires an esm that
isn't installed/enabled), framework missing or version-outdated while
mods need it, launch command pointing at a missing loader (detectable
via get_launch_options_state), stale records, file conflicts between
mods (two records claiming the same path - the per-file manifests make
this cheap to compute), and known device quirks. Ties together the
existing masters-checker and stash-disable backlog items.

## RESOLVED (v0.18.0): SSE FPS Stabilizer archive layout

The second-level logging pinned it: the archive HAS Fomod/ModuleConfig.xml
but the file is UTF-16 LE with a BOM (the "FOMOD Creation Tool" writes
UTF-16). xml_parse_file read it as UTF-8, tokenized NUL garbage into an
empty wizard, and the install fell through to "no payload". Reproduced by
downloading the archive on-device and running the plugin's own parser
against it. Fix: BOM-aware decoding in xml_parse_file (utf-16 both
endians + utf-8-sig), pinned by test_utf16_moduleconfig_parses.

## Feature backlog: resumable FOMOD wizards (requested 2026-07-23)

Long wizards (JK's Interiors Patch Collection: 39 steps) should support
"do some now, come back later": persist the in-progress selections +
step index per pending FOMOD (the attention entry is the natural home)
and restore the wizard where the user left off instead of restarting.
Low priority per Michael. The RB/LB step shortcuts (v0.21.0) cover the
main pain meanwhile.

## Witcher 3 device verification (2026-07-24, v0.23 testing)

The v0.13 machinery held up on device: 35+ mods installed in one run of
"TW3 Lightly Modded" - folder mods, multi-folder mods, dlc routing, and
menu-XML registration all worked; the carry-weight single-mod journey
worked first time. Findings:

- FIXED (v0.24.0): menu XMLs inside a mod folder (Increased Draw
  Distance: modX/bin/config/.../pc/) crashed - the caller moved folders
  before XMLs. XMLs now move first; regression test added.
- W3 Mod Manager / Script Merger install attempts now classify as PC
  tools (exe detection in the witcher router) instead of raw failures.
- **Script conflicts are the #1 remaining W3 gap**: 4 of the 5
  collection failures were the conflict gate correctly refusing mods
  that edit scripts owned by earlier installs (verified by re-running
  the router against the real mods dir on device). Curated W3
  collections ASSUME Script Merger. Next step when W3 returns to the
  front burner: the Linux CLI merge spike (apocalyptech/w3scriptmerge)
  or a minimal built-in three-way merge for non-overlapping hunks.

## Witcher 3 script-mod ceiling (established 2026-07-24, device testing)

Extensive on-device testing of large mixed W3 collections established a
hard, honest scope limit:

- **Mechanical install works**: folder/dlc/menu-XML placement, official-
  DLC protection (merge-not-replace), PC-tool and bin-overlay handling,
  filelist registration - all verified.
- **Auto script-merge is OFF by default** (v0.32.4). A line-based 3-way
  merge can produce a structurally-valid script (balanced braces/parens,
  correct encoding) that still won't compile, and a bad merged mod
  crashed the game BEFORE the compile stage. Not safe unattended. Engine
  retained behind `w3_auto_merge` for a future health-check that can
  validate before committing.
- **Large script-heavy soup won't boot regardless**: with the one merged
  file's mods removed, a 68-mod setup reached "compiling scripts" then
  failed - other mods' script interdependencies + load-order needs. This
  is the PC-Witcher Script-Merger reality; unattended install can't lift
  it.

1.0 W3 scope: single collections / modest script-mod counts install and
boot cleanly (conflicts skip with a note). Very large multi-collection
setups are explicitly out of scope - the health-check feature is where
we'll diagnose "this combination won't compile" for the user rather than
attempt it.

## Fallout 4: VERIFIED on device (2026-08-04)

Cleanest first-run of any game so far, per Michael: foundational mods
(F4SE + setup inis) installed without error, several individual mods
verified in-game (MCM config, extra character-creator options), and the
"The Fallout Historical Arsenal" collection installed and worked first
time with zero errors. dataDir machinery is solid across both Bethesda
games. Starter saves installed on device (mod 44704, Act One complete).

## Future-games watchlist (user adds, 2026-08-04)

- **Assassin's Creed Black Flag Resynced** — OUT with a modding
  community. Verified live: domain `assassinscreedblackflagresynced`,
  gameId 9408. Needs a research pass (paths/framework).
- **Moonlight Peaks** — OUT with a modding community. Verified live:
  domain `moonlightpeaks`, gameId 9480. Needs a research pass.

## Elden Ring / FromSoft family (research 2026-08-06, me3 path)

Verified: me3 v0.12.1 (garyttierney/me3) is the maintained successor to
the archived Mod Engine 2 - a NATIVE Linux binary installing under $HOME
(no root, survives SteamOS updates). It launches the game itself through
the game's own Proton prefix and bypasses EAC simply by running
Game/eldenring.exe instead of start_protected_game.exe. Matchmaking is
blocked by default (start_online = false) and that does NOT affect
Seamless Co-op, which uses its own Steam P2P.

Family (all first-class in me3): ER 1245620 `eldenring`, DS3 374320
`darksouls3`, Sekiro 814380 `sekiro`, AC6 1888160
`armoredcore6firesofrubicon`, Nightreign 2622380 `eldenringnightreign`.

Plugin plan: bootstrap me3 (portable tarball, plugin-owned), write
~/.config/me3/profiles/deckynexus-<game>.me3, install mods as
packages/<mod>/ (asset mods incl. regulation.bin) or natives/<mod>/
(DLL mods), and launch via Steam launch options so Steam keeps Steam
Input + overlay. Seamless Co-op = ersc.dll as a native with
load_early + initializer { function = "modengine_ext_init" }, password
surfaced in the QAM, savefile isolation always on.

Blockers: Proton 8.0 must be installed (me3's Deck fallback for ER;
preflight it), Steam Input focus when launched outside Steam, one
regulation.bin only (refuse a second, don't silently pick a winner),
and regulation.bin is coupled to the game patch.

### BUILT (v0.52.0, 2026-08-06) — awaiting device verification

Elden Ring ships as the first `installMode: "me3"` game. What landed:

- **Loader bootstrap**: `install_me3` fetches the portable Linux tarball
  into the plugin's runtime dir (`<runtime>/me3/bin/me3` + `bin/win64`),
  flattens a versioned wrapper, and verifies the binary actually runs
  (`--version`, falling back to `info`) rather than just existing.
- **Profile writer** (`_write_me3_profile`) is the tier's plugins.txt:
  regenerated from the install records on every install, toggle,
  uninstall and reset. `start_online` is never emitted and `savefile`
  always redirects — both asserted by tests, not left to convention.
- **Mods live outside the game folder** (`<runtime>/me3/profiles/
  eldenring/mods/<Mod>/`), so the install stays byte-identical to
  vanilla. Routing handles assets-at-root, me3's documented
  `mod/` + `natives/` layout, and wrapper folders; dlls sitting inside
  asset content are NOT force-loaded as natives.
- **regulation.bin gate**: a second owner is refused by name, and the
  refusal lifts once the first is disabled or uninstalled.
- **Seamless Co-op**: installs as a plain native, and the session
  password is editable from the QAM (reads/writes ersc_settings.ini).
  Natives get a `path` and nothing else — see the launch-crash note.
- **Launch command** comes from the backend (`get_me3_launch_command`),
  since the paths are plugin-owned rather than game-relative:
  `bash -c 'exec "<me3>" --windows-binaries-dir "<win64>" launch -p
  "<profile>"' -- %command%`. %command% is accepted and discarded —
  Steam needs it to treat the string as a wrapper, and going through
  Steam keeps Steam Input and the overlay attached.

Verified against me3 v0.12.1 sources while building: `-p` accepts a full
path (so profiles stay plugin-owned, no XDG writes); the portable dist
needs `--windows-binaries-dir`; me3 picks Proton from Steam's own
CompatToolMapping for the app, falling back to the game's verified-Deck
runtime — so the panel warns only when NO Proton is present.

Proton is Step 1 (v0.54.0). me3 asks Steam which Proton to use and Steam
only answers if it's been told: a Verified game runs on an implicit
default written down nowhere, so me3 falls back to the game's
verified-Deck runtime (Proton 8.0 for ER) — which the test device didn't
have installed. That combination fails at launch with nothing useful
said. The step writes the mapping with one tap, picking the newest
numbered Proton Steam reports as available, so unmodded play is
unchanged. Tool names come from `GetAvailableCompatTools`, never derived
from folder names: Valve's own Proton builds ship no
`compatibilitytool.vdf` (verified on device).

### VERIFIED on device (2026-08-12)

A real Seamless Co-op session, two players, with a second QA on the other
end - which is why this sat since 6 August: it cannot be tested alone.
Steam Input survived the wrapper, so launching through Steam rather than
calling me3 directly was the right call.

That clears the whole me3 tier's unknowns. **DS3, Sekiro, AC6 and
Nightreign are now one registry block each** - the backend already maps
all five domains, the profile writer is shared, and the only
game-specific parts are the app id, the domain and the detect file.

What this proves beyond Elden Ring: the EAC bypass by launching
Game/eldenring.exe, savefile isolation onto a separate .sl2, the
password round-trip through ersc_settings.ini from the QAM, and natives
declared with a path only (the v0.57.0 lesson).

### RESOLVED (v0.57.0): Seamless Co-op crashed Elden Ring on launch

Every launch died ~8s in, with a byte-identical me3 log ending at
`hooking system allocator` and no error. Bisected on device with a
launch harness (write profile variant → launch through Steam → poll for
the game process → dump the me3 log):

| variant | result |
|---|---|
| me3, no mods | boots |
| ERSC with `load_early` + `initializer` | crash at ~8s |
| same, `mem_patch = false` | crash at ~8s |
| ERSC as a plain native | boots |

The cause was ours. me3 already recognises ModEngine2-style natives and
says so in its log — `loaded native with me2 compatibility shim` — so the
`initializer = { function = "modengine_ext_init" }` we generated ran a
second initialisation on top of me3's. The `modengine_ext_init` symbol
really is exported by ersc.dll (confirmed with `strings`), which is what
made the wrong recipe look plausible.

Lesson for the tier: **declare natives with a path and let me3 decide how
to load them.** It knows more about a given mod than a table in this repo
can. Anything else needs evidence from a launch, not from docs.

Known gap (deliberate): **option-pack archives** — one download holding
"Full version/" and "Lite version/" — are refused with a message telling
the user to pick a single file, rather than routed through a chooser.
Installing both would load two copies of the same early-load native and
crash the game, and picking one for the user is a guess. The dataDir
tier's `needs_choice` / PayloadChoiceModal flow is the model when this
comes back round; it needs an me3-shaped definition of what an "option"
is before it's worth wiring.

Colleague's manual methods (2026-08-06), useful as fallbacks/context:
copy eldenring.exe over start_protected_game.exe (works but breaks
vanilla online and Steam file-verification undoes it - the plugin must
NOT do this); a bat that taskkills EasyAntiCheat_EOS.exe and starts
eldenring.exe with SteamAppId set; and UXM Selective Unpacker
(Nordgaren/UXM-Selective-Unpack) which unpacks the archives and patches
the exe for loose files - a no-loader route, but it rewrites the game
install and needs ~60GB, so it stays out of scope for v1.
