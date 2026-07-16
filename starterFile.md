# Project Handover: Decky Loader Plugin for Nexus Mods

*Date:* 15 July 2026
*Status:* Idea validated, v1 scope drafted, ready for spec/build
*Owner:* QA engineer (15 yrs), Nexus Mods employee. Prior side projects: a Windows app onboarding first-time modders (now with the company), a slot game, and a Slay the Spire 2 companion website.

---

## The Idea

A Decky Loader plugin for SteamOS/Bazzite that lets users browse, download, install, and enable/disable Nexus Mods content for the currently selected game — entirely from Gaming Mode with a controller, without ever switching to Desktop Mode.

*Motivation:* The owner has a Steam Deck and a living-room Bazzite PC (4K TV, RTX 4070 Ti Super) and wants couch-friendly modding. No product currently serves this: modding on Linux handhelds today means Desktop Mode workarounds, manual file copying, or waiting for desktop-class tools.

## Why the Gap Exists (Market Context, verified July 2026)

- *No Nexus-related Decky plugin exists.* The Decky ecosystem is crowded around theming (CSS Loader), performance (PowerTools, LSFG), launcher unification (NonSteamLaunchers/Unifideck), and library metadata (SteamGridDB, HLTB) — but nothing for mod management.
- *The Nexus Mods app is dead.* Cancelled internally in 2025, announced publicly January 2026. Development consolidated back into Vortex. (Owner has internal context on the reasons.)
- *Vortex is coming to SteamOS in 2026* per the public Nexus roadmap, targeting vanilla Steam hardware (Steam Deck, Steam Machine) only. Bazzite and other distros are explicitly unsupported officially, though Vortex is open source and community ports are expected. Crucially, Vortex will be a *Desktop Mode application* — browsing still happens on the website, downloads via nxm:// protocol links from the "Mod Manager Download" button. Nobody is building the Gaming Mode / controller-first layer. That's the differentiator.
- *Steam Workshop overlap:* some games now have first-party Workshop support (including the chosen test game — see below). The plugin's value is strongest for the large set of games with Nexus communities but no Workshop presence, and for Nexus-exclusive mods.

## Test Game: Slay the Spire 2

Chosen deliberately — near-ideal v1 conditions, and the owner's partner knows the game inside out (built-in domain-expert tester).

Verified facts (as of July 2026):

- Native Linux build exists; no Proton prefix complications.
- Mods are simple drop-ins: extract to ~/.steam/steam/steamapps/common/Slay the Spire 2/mods/. The game has shipped with a built-in mod loader since launch — it detects mods on startup and prompts a restart into modded mode.
- The game keeps *separate save files for modded and unmodded play*. Surfacing this warning in the plugin UI is a differentiating quality-of-life touch.
- Active Nexus community: roughly 889 mods and 30 collections on the StS2 Nexus page.
- Wrinkle: Major Update 2 (v0.107.1, 19 June 2026) added official Steam Workshop support with an in-game Mods menu (checkbox enable/disable, recompile on restart). The plugin still adds value for Nexus-hosted/Nexus-exclusive mods, and StS2 remains the low-risk proving ground before expanding to games with no Workshop at all.

## Proposed v1 Scope

*In scope:*

1. Decky plugin (standard architecture: React/TypeScript frontend in the Quick Access Menu + Python backend). Start from the official plugin template.
2. Detect the currently selected/focused game via the Steam client APIs Decky exposes (app ID lookup).
3. Controller-friendly browse/search UI for that game's Nexus mods, built on the Nexus Mods API (not an embedded website — clunky with a controller).
4. Download and install: extract archive into the game's mods/ folder.
5. Enable/disable: move mod folders between mods/ and a mods-disabled/ sibling directory; track state in plugin config.
6. Surface the modded-vs-unmodded save warning for StS2.
7. Single supported game: Slay the Spire 2.

*Out of scope for v1:* multi-game support, FOMOD installers, load order management, games living inside Proton prefixes, collections, Vortex integration.

## Key Technical Constraints & Open Questions

- *Download permissions:* the Nexus API only issues direct download links to Premium users. Free users go via the website's "Mod Manager Download" button and the nxm:// protocol handler. The free-user flow needs a design answer — register the plugin as an nxm handler, or scope v1 to Premium. Owner has internal channels to explore options here.
- *API terms:* Nexus API usage requires a registered application / acceptable-use compliance. As an employee, clarify the right way to register (and whether this becomes an official or sanctioned project now that it's moving to a work account).
- *IP/ownership:* originally conceived as a personal side project, now moving to work context — worth formalising early.
- *Decky on Bazzite:* works well in practice (Bazzite treats Decky as a first-class citizen), but Decky officially targets SteamOS; occasional breakage after SteamOS updates is a known ecosystem reality. Plan for it in test strategy.
- *Distribution:* decide between the official Decky store (review process, discoverability) vs. install-from-zip during early testing.

## Suggested Build Order

1. Hello-world Decky plugin from the template; confirm dev loop on real hardware (Deck + Bazzite box).
2. Read selected game's app ID; hardcode StS2 mapping to its Nexus game domain.
3. Nexus API auth (API key via settings page) + read-only browse UI.
4. Download + extract to mods/ (Premium flow first, since it's the simple path).
5. Enable/disable via folder moves + state tracking.
6. Modded-save warning, polish, controller UX pass.
7. Dogfood with partner as StS2 domain expert; owner drives QA/test plan.
8. Decide free-user nxm:// story and second game candidate.

## Future Directions

- "Vortex remote control" mode once Vortex lands on SteamOS: Vortex does the heavy install logic in the background, the plugin becomes the Gaming Mode front end for browse/toggle.
- Expand to other drop-in-folder games (e.g. Stardew Valley-class simplicity) before attempting anything with FOMOD/load orders.
- Collections support; update checking for installed mods.