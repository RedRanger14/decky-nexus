# 1.0 Game Support Roadmap

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
| Elden Ring | 1245620 | `eldenring` | 4333 | 7,317 | 2.2M |
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
14. **Elden Ring** — needs a product decision (anti-cheat/offline).

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
