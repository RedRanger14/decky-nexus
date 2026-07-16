# Linux mod-compatibility strategy

Problem discovered 2026-07-15/16 (case study: *Ironclad Skin-Crimson Blade Valkyrie*,
StS2 mod 854): mods that patch game code can work on Windows but fail on the
**native Linux build of the same game version**, because platform exports ship
different main assemblies (`release_info.json` → `main_assembly_hash` differs
between platforms for identical commits). In the observed case the mod's
RitsuLib-free fallback path crashed on Linux only; installing RitsuLib fixed it.
This makes some dependencies **mandatory on Linux while optional on Windows** —
a distinction Nexus's requirements system cannot currently express.

Pure asset mods (.pck replacers, e.g. Regent Cards Anime Rework) are unaffected;
the risk is concentrated in mods that do runtime code patching (Harmony etc.).

## Defence layers

- **Layer 0 — report upstream** (always): hand authors root-caused reports.
  The fix at source is authors try/catching per-patch or declaring the
  dependency. Done for mod 854.
- **Layer 1 — curated hints** (shipped): `src/compat.ts` maps (domain, modId)
  → verified platform note, rendered as a 🐧 banner on the mod detail page.
  Cheap, reliable, doesn't scale beyond dogfooding.
- **Layer 2 — generic failure guidance** (shipped): the plugin already parses
  per-mod load outcomes from the game's own log. When any enabled mod failed
  to load last session, the QAM shows a "failed to load — details" flow with
  the actual error text plus generic guidance (library mods like
  BaseLib/RitsuLib are often needed on Linux even when Nexus lists no
  requirements). Zero curation required.
- **Layer 3 — community/Nexus-side signals** (long-term): anonymized load
  outcomes per (mod, version, game version, platform) aggregated into a
  "verified loading on SteamOS" indicator. **Constraint (per owner):** this
  needs Nexus to officially adopt the project — beyond side-project scope.
  Small internal help is feasible; full backend infra is not. Park until the
  official-project conversation happens; the mod-854 case study is the pitch.

## Improvement ideas (roughly ascending effort)

1. Parse more failure signatures from the game log into friendlier ⚠ details:
   duplicate mod id, min_game_version mismatch, missing dependency vs patch
   exception (currently all shown as raw error text).
2. Auto-suggest the known library mods when a patch exception is detected:
   one-tap "Install BaseLib/RitsuLib and retry".
3. Move `compat.ts` hints to a JSON file fetched from a repo/gist at runtime —
   hints become updatable without a plugin release, and community PRs can add
   them. Good middle ground below Layer 3, no Nexus infra needed.
4. Show the hint at install time (browser detail page already does) AND as a
   toast right after a failed-load is first detected for a hinted mod.
5. Layer 3 proper, if/when officially adopted.
