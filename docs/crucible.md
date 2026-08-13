# Crucible

**A harness that finds out which mods work, so users don't have to.**

**Date:** 13 August 2026
**Name:** Crucible — Michael's pick, 13 August 2026. Anvil was the
alternative and was dropped for a good reason: CurseForge has built its
brand around forge and anvil imagery, so borrowing it would read as
following them. A crucible is where something is tested to destruction,
which is the job.

If the results ever become a public feed, that feed is the **Compatibility
Index** — the tool's name should not be the data's name.

**Status:** idea, not scheduled. Nothing here is built.
**Origin:** Michael, after a Slay the Spire 2 collection closed the game
twice: *"there are so many mods for so many games and testing them all
manually is just an impossibility - could we build something like the hunt
feature for skyrim where it tries downloading mods automatically and booting
the games - as it learns which ones fail it can build a database and label
them so users dont have to find this out themselves."*

Explicitly **not part of the plugin**. A separate tool that runs on
dedicated hardware and produces data the plugin consumes.

---

## Why this exists

Every compatibility warning in the plugin today is a table I wrote by hand
after hitting the problem on device:

| Table | What it holds | How it got there |
|---|---|---|
| `MODS_NEEDING_EXTERNAL` | 3 New Vegas mods needing Vanilla UI+ | Three failed boots and a crash log |
| `UNSUPPORTED_COLLECTIONS` | 1 collection (VeryLastKiss's TTW) | A 42 GB download that could never work |
| `KNOWN_BAD_STATE` | per-game known bad configs | Same, one at a time |

That is the correct set of facts and an indefensible way to gather them. It
covers what one person happened to install on one device. There are ~32,000
Stardew Valley mods alone.

The harness replaces the *acquisition* of those facts, not their use. The
plugin keeps doing exactly what it does now; the tables stop being
hand-written.

## The oracle already exists

This is the part that makes it cheap. The classifier is written and
verified:

- `_parse_mod_load_log` (main.py) reads a Godot session log and attributes
  each exception to the mod that threw it, from the **first** stack frame -
  not the libraries beneath it. Verified against 4 real Slay the Spire 2
  sessions, 2026-08-13.
- It ignores failures a mod's own log marks `[Optional]` or "skipped",
  because those are the mod saying it planned for them. Without this it
  accused two working ecosystem libraries.
- `_godot_mod_manifests` reads each mod's manifest for its real id and its
  declared dependencies, which is what lets a logger name like
  `com.ritsukage.sts2-RitsuLib` be matched back to an installed folder.
- `disable_failing_mods(..., dry_run=True)` already returns
  `{names, details, held}` for a session without touching anything.

So a harness does not need to invent a verdict engine. It needs to drive a
loop and record what the existing one says.

### Games where an oracle is possible

| Game family | Oracle strength | What we get |
|---|---|---|
| Godot/.NET (StS2) | **Strong** - log names the mod and counts its exceptions | per-mod verdict with evidence |
| SMAPI (Stardew) | **Strong** - `_parse_smapi_log`, SMAPI names the mod | per-mod verdict with evidence |
| Bethesda (SSE/FNV/FO3/FO4) | **Weak** - no attribution | reached-menu yes/no, missing masters, plugin count, script-extender plugin failures |
| Everything else | **None yet** | reached-menu only |

Be honest about the weak row. For Bethesda games a verdict is "this mod
alone did not stop the game starting", which is real but much less than "this
mod is compatible".

## Shape

Straight enumeration, not the Skyrim hunt. The hunt bisects a broken load
order to find one culprit; here most mods work and we want a verdict for
each, so bisection buys nothing.

```
for mod in worklist:
    reset game to baseline        # reset_game_modding, build-id guarded
    install mod (+ its stated dependencies)
    launch, wait for menu or death, kill
    read log -> verdict + evidence
    uninstall
    record
```

Every step is an existing plugin function. The harness is the loop, the
worklist and the store.

### Throughput

~90 s per mod optimistically (install, launch, reach menu, kill,
uninstall) = ~40/hour = **~350 per overnight run**.

Against 32,000 Stardew mods that is nothing. Against **the top 200 mods of
each of the 8 supported games** it is about two nights, and that is the
version worth building. Users install popular mods; popular mods are the
ones whose breakage costs the most.

### Re-verification is the real product

A mod does not "become" incompatible - the **game updates** and breaks it.
That event is detectable: `_steam_build_id(app_id)` already exists and the
reset flow already guards on it. So:

> when a game's build id changes, re-run its worklist

That is the loop that produces something no manual process can: a
compatibility answer that is current, with a known date and a known game
build.

## Output

One signed JSON file per game that the plugin fetches, treating it like any
other remote data - never as instructions.

```json
{
  "domain": "slaythespire2",
  "game_build": "19283746",
  "checked_at": "2026-08-13",
  "mods": [
    {
      "mod_id": 284,
      "verdict": "broken",
      "reason": "missing_method",
      "evidence": "MissingMethodException: CombatManager.get_IsPlayPhase(), 1041 occurrences in RelicsReminder._Process",
      "needs": []
    },
    { "mod_id": 103, "verdict": "works", "dependents_seen": 5 }
  ]
}
```

Verdict vocabulary, deliberately small:

- `works` - loaded, no attributed errors
- `broken` - attributed errors, with the log line
- `needs_external` - requires a file not on Nexus (the Vanilla UI+ case)
- `unknown` - anything we cannot explain

`unknown` is the important one. See the constraints.

## Constraints - not negotiable

**1. A verdict without evidence is a rumour.** Every `broken` carries the
log line that justifies it. Anything that failed for a reason we cannot name
is `unknown`, never `broken`. A wrong "incompatible" label costs a mod
author downloads and costs us the right to publish these at all.

**2. Not on the only test device.** The harness holds a machine at full duty
and constantly rewrites a game install. Sharing that with the device used
for QA means neither job gets done. Dedicated hardware or a container.

**3. Nexus has to know.** A few hundred automated downloads a night against
a personal API key is not something to discover after the fact.

> Michael, 2026-08-13: *"I honestly think nexus would be keen - there is
> probably a way we can tag the downloads so it doesnt affect our stats
> too. I think my manager would love an improvement of knowledge of which
> mods are bad and which arent or compatible etc."*

So the conversation to have is not "may we" but "how should these be
tagged". Two things to raise:

- **Download attribution.** Harness downloads should be excluded from a
  mod's public download count, or marked so they can be. A mod author's
  stats are their livelihood signal and 200 synthetic downloads distort
  them. Ask what mechanism exists - a header, a dedicated key class, an
  agreed User-Agent. The plugin already sends an identifiable User-Agent
  built from package.json, so a distinct harness UA is trivial.
- **Where the data lives.** If this is useful to Nexus - and the framing
  above says it is - the results belong somewhere Nexus owns, not in a file
  I host. That is a much better outcome than the plugin shipping its own
  copy, and it changes the design: the plugin would read a Nexus field
  rather than a side-channel JSON.

**4. Rate limits and courtesy.** Sequential, one download at a time, honour
the API's limits. This is a good citizen or it is nothing.

## First step, when it's time

Do not start with 8 games and 1,600 mods. Start with the ~27 Slay the Spire
2 mods already installed on the device and have the loop produce an
individual verdict for each one overnight. Either it agrees with what we
established by hand on 2026-08-13 - Relics Reminder broken with 1,041
exceptions, BaseLib/ModConfig/RitsuLib working and depended upon, three mods
failing to load - or the oracle is not as good as it looks.

That is a day of work and it settles whether any of the rest is worth
building.

## A local seed already runs

Added 13 August 2026, in the plugin rather than the harness, because the
same gap kept biting: every fix only worked *after* a crash had produced a
log to read, so a reset and reinstall put the broken mod back and the game
died on it again.

The plugin now keeps its own verdict store — the same shape this document
proposes, filled in from one device instead of a fleet:

```
mod_verdicts[domain][mod_id] = {build, version, state, why, name}
```

- **Written** when a session log blames a mod unambiguously (a flood of
  attributed exceptions, or "failed to load").
- **Scoped to the game build and the mod version.** A build change retires
  the verdict, because a game update is the most likely thing to have fixed
  or broken a mod. A different installed version starts from innocent.
- **Survives `reset_game_modding`** — deliberately, with a test that fails if
  someone adds `mod_verdicts` to the sections reset clears.
- **Applied at collection finish-setup**, before the first launch, and
  surfaced on the mod's own page.

First real contents, Slay the Spire 2 build 23811903:

| mod id | mod | version | verdict |
|---|---|---|---|
| 284 | Relics Reminder | 1.1.0 | 1,056 MissingMethodExceptions from `_Process` |
| 21 | Remove Multiplayer Player Limit | 0.1.6 | failed to load |
| 107 | Campfire Trading | 1.0 | failed to load |
| 468 | Refresh Ancient | 1.3.3 | failed to load |

That table is what Crucible would produce at scale, and it is already the
right shape to publish. Which means the harness's job is narrower than it
first looked: **not designing the verdict format, but filling this table in
without a human having to crash a game to add a row.**

## What already exists to build on

| Piece | Where |
|---|---|
| verdict engine (Godot) | `_parse_mod_load_log`, `disable_failing_mods` |
| verdict engine (SMAPI) | `_parse_smapi_log`, `get_smapi_load_status` |
| mod manifests + dependency graph | `_godot_mod_manifests`, `_mods_needed_by_others` |
| clean baseline + restore | `reset_game_modding`, build-id guard |
| install / uninstall one mod | `install_mod`, `uninstall_mod` |
| game build detection | `_steam_build_id` |
| missing masters (Bethesda oracle) | `_missing_masters`, `get_load_order_state` |
| launch and watch | the Skyrim crash hunt's launch/watch loop |
| hand-written tables this would replace | `MODS_NEEDING_EXTERNAL`, `UNSUPPORTED_COLLECTIONS`, `KNOWN_BAD_STATE` |
