# Nexus Mods: Decky Loader Plugin (Unofficial)

Browse, download, install, and enable/disable [Nexus Mods](https://www.nexusmods.com) content for the currently selected game, entirely from Gaming Mode with a controller, on SteamOS and Bazzite. No Desktop Mode required.

> **Built with AI, tested by hand.** Nearly all of this plugin's code was
> written by Claude, Anthropic's AI. What makes it trustworthy is not the
> author but the process around it: hundreds of hours of human direction,
> hands-on testing, and every supported game installed, modded and played on
> real hardware before it shipped. We state this plainly because you deserve
> to know how the thing you are installing was made.

> **Unofficial, and in beta.** This is a community-built plugin. It is not an
> official Nexus Mods product, is not supported by Nexus Mods, and nothing
> here is endorsed by them. It uses their public API like any other
> third-party client. Bugs are ours, so please report them here rather than
> to Nexus Mods support.

**Status:** 1.2 beta. Supports the games listed below; Slay the Spire 2 was
the proving ground and is no longer the limit. Beta means the supported games
have each been installed, modded and played on real hardware, which is not
the same as everything working. Expect rough edges, and back up saves you
care about.

## Supported games

Twelve games, each one installed, modded and played on real hardware before
it shipped. That is what supported means here: not that the code has a config
entry for it, but that someone finished a session with mods running.

1. Cyberpunk 2077
2. Elden Ring
3. Fallout 4
4. Fallout: New Vegas
5. Helldivers 2 (*)
6. Mount & Blade II: Bannerlord
7. Resident Evil 4
8. Skyrim Special Edition
9. Slay the Spire 2
10. STAR WARS Battlefront II (2017) (**)
11. Stardew Valley
12. The Witcher 3

(**) Battlefront II mods are not files the game reads - they are compiled
into a data pack, which is why no Deck plugin has supported a Frostbite game
before. Step 1 in the panel downloads the compiler (40 MB, once). After that
installing works like any other game, except that every install or toggle
recompiles the pack, which takes a minute or two. Retextures, replacement
character models and multi-part mods all work: a mod that ships as a set
(base, text and weapon parts) installs whole, and one that offers
alternatives asks which you want by the author's own names for them.

The compiler is our own build. The format Battlefront II uses was
unimplemented upstream, and getting mods to render correctly took six fixes
between the format and the mod pipeline, all documented in
docs/frosty-swbf2/. Correctness is checked rather than hoped for: the
compiler's output was compared asset by asset against the desktop Frosty
Mod Manager's for the same mod, decompressed content included, and every
pack is read back before the plugin offers it.

Plugins for the desktop Frosty Mod Manager itself - BetterSabers is the most
popular - are not game mods and cannot work in Gaming Mode. They are labelled
in the store rather than left to fail on install.

(*) Helldivers 2 is a live-service game that changes its data files with
every update, which breaks most mods until their authors release new
versions. The plugin flags out-of-date mods with a PRE-UPDATE badge and can
filter to mods updated since the game's last patch, but a mod that installed
cleanly and does nothing usually just needs its author to catch up. That is
the game, not the plugin, and it is the same in every mod manager.

### On the roadmap

Groundwork exists for these and they are NOT ready. They may appear in the
plugin; treat anything you do with them as untested.

- Fallout 3
- Hollow Knight: Silksong
- Palworld

### Requested, not started

Asked for and on the list. No code exists for these yet, so they will not
appear in the plugin until they do.

- Final Fantasy XII: The Zodiac Age
- Mass Effect Legendary Edition
- Nier: Automata
- Skyrim VR

Want one moved up, or a new one added? Open a [game request][issues] and say
which. Adding a game means installing it, modding it and playing it on
hardware, so the list grows slowly and on purpose.

[issues]: https://github.com/RedRanger14/decky-nexus/issues

## What it does

- **Finds the game you are playing** and shows its Nexus mods, with search,
  sort and curated rails, on a controller. Games on an SD card or a second
  drive are found the same as internal ones, with nothing to configure.
- **Downloads and installs** into the right place for that game, whether that
  is a mods folder, the game's data directory, a Proton prefix, or a mod
  loader profile. Which one is per game and handled for you.
- **Collections**, installed in order, with a report at the end naming
  anything skipped and why.
- **FOMOD installers**, presented as a controller-friendly wizard rather than
  a desktop dialog.
- **Load order applied automatically** where a game needs one, from the
  collection's own ordering. Reordering it by hand is not supported yet.
- **Enable and disable** any installed mod, and reset a game to vanilla.
- **A health check** that reads the game's own logs and says what is actually
  broken, rather than guessing.
- **Updates**, checked against Nexus per mod, applied with one tap, and when
  an update offers versions the picker opens right there.
- **Filters next to sort**: the game's own categories, recently updated, and
  on live-service games "since the game's last update".
- **Load order for Bannerlord** is solved from the mods' own declared rules,
  and the launcher used is the one that honours them.
- **ReShade packages install** on games configured for it, with the launch
  options set automatically and the risk stated plainly: ReShade injects
  into the game's process, which anti-cheat may treat differently from
  ordinary mods. The warning is on the mod page before you download.
- **Endorsements and author support links**, so the people who made the mods
  still get credit.
- **When a mod was uploaded and last updated**, on its page, because "is this
  thing still alive?" is usually the first question.
- **Long installs say what they are doing**, with a named stage and a live
  percentage rather than a still bar, so a slow step cannot be mistaken for a
  hung one.
- **Mods that cannot work here are labelled before you download them**:
  desktop-only tools carry a badge on the tile and an explanation on the mod
  page, and a mod built for a different build of the game says so on its page
  and in My Mods.

Not supported yet: manual load order editing, Vortex profile import, and
mods that need a Windows tool to install.

## How to use it

The plugin shows mods for **the game you are currently looking at**, so it
needs to know which game you mean. There are two ways to tell it, and both
are things you were probably doing anyway:

- **Open the game's page in your library.** Select the game in Steam so its
  page is on screen, then open the Quick Access Menu. That is enough, the
  game does not need to be running.
- **Or launch the game.** While it is running, the plugin is scoped to it.

If you open the plugin from the Steam home screen or a menu, it has no game
to work with and will say so rather than guess.

From there:

- **Store** browses that game's mods and collections, with search and sort.
- **Downloads** shows what is in flight, and lets you pause everything.
- **My Mods** lists what is installed, and switches any of it on or off.
- **Updates** checks the installed mods against Nexus.
- **Health** reads the game's own logs and says what is actually broken,
  which is the page to open when something does not work.

Mods apply the next time the game starts. Nothing is written into the game
until you install something, and every supported game can be put back to
vanilla from the plugin.

## What you need

- **A Nexus Mods Premium account.** This is not optional. Free accounts
  cannot generate download links through the Nexus API, and the manual route
  free users take on a PC (clicking through the website, then a browser
  handoff) is not usable from Gaming Mode on a controller. With a free
  account you can browse here, but nothing will download.
- **Decky Loader**, installed on the Deck already.
- **The game installed through Steam.** Mods are applied to the Steam copy.

## Crediting mod authors

Mods exist because people give their work away, and a plugin that makes them
invisible is a plugin that quietly costs them. So:

- **Endorsing is one button**, on the mod page in the plugin, and it is the
  same endorsement that counts on the website.
- **Author support and donation links are carried through** to the mod page
  rather than stripped out, so a mod you get hours from is one tap from the
  place its author asked to be supported.
- **Authors and versions are shown** everywhere a mod is listed, not just the
  mod name.

If something you install turns out to be good, endorse it. It costs nothing
and it is the whole economy these mods run on.

## Installing

You need [Decky Loader](https://decky.xyz) first: this is a Decky plugin, not
a standalone app.

Installing is done from Desktop Mode and takes about two minutes.

**If you have never set a password on your Deck**, do that first, because
Decky needs it and so does this. In Desktop Mode open **Konsole** and run
`passwd`. It asks for a new password twice and nothing appears as you type,
which is normal. Then install Decky, then come back here.

1. **Switch to Desktop Mode.**

   Steam button -> **Power** -> **Switch to Desktop**. The Deck reboots into
   a desktop.

2. **Open this page on the Deck**, in the browser there, and open **Konsole**
   as well.

   Konsole is in the bottom-left menu (the launcher icon): type `konsole` and
   open it. Leave both open, you will switch between them.

3. **Copy this line, paste it into Konsole, and press Enter.**

   ```sh
   curl -L https://raw.githubusercontent.com/RedRanger14/decky-nexus/main/install.sh | sh
   ```

   **To paste, right-click in Konsole -> Paste.** The easiest way to
   right-click on a Deck is the **left trigger (L2)**. Ctrl+Shift+V does not
   work from the on-screen keyboard.

   Do **not** put `sudo` in front of it. The script asks for your password
   itself, at the one step that needs it. Running the whole thing as root
   looks in the wrong home folder and fails.

4. **Type your password when it asks.**

   Nothing appears on screen as you type, which is normal. It is the same
   password you set when you installed Decky, because Decky owns its plugin
   folder as root and installing into it needs permission.

5. **It installs, then asks for your Nexus Mods API key.**

   Leave it sitting at that prompt and go and fetch the key.

   In the browser: log in to [Nexus Mods](https://www.nexusmods.com) -> your
   profile picture, top right -> **Site preferences** -> **API Keys** in the
   left-hand menu -> scroll to the very bottom -> copy the **Personal API
   Key**.

6. **Back in Konsole, paste the key at the prompt and press Enter.**

   Left trigger (L2) to right-click -> Paste, then Enter. It saves the key,
   so you never type it on a controller.

   *Rather not paste your key into a terminal? Press Enter on its own to skip.
   You can add it on the plugin's Settings page instead.*

   Steam's interface will flicker during the install, and Decky may show a
   toast saying something failed. **Ignore it.** Restarting Decky restarts
   part of Steam's UI with it, and Decky reports losing its own connection as
   a failure. The terminal is the one telling the truth.

7. **Return to Gaming Mode.**

   The **Return to Gaming Mode** icon is on the desktop.

8. **Open the plugin.**

   Quick Access Menu (the **...** button) -> the **plug** icon -> **Nexus
   Mods**.

   The panel footer shows the version and the words `unofficial beta`. If your
   game is supported and installed, it will already have found it, and your
   key is already in place.

That is Desktop Mode done with for using the plugin. Browsing, installing
mods, collections and health checks all happen from the Quick Access Menu on
the controller.

Updating is the one exception, and it is the same two minutes: see below.

### Updating

Repeat the install: Desktop Mode, Konsole, same line. It fetches the newest
release, checks it is complete before removing the old version, and restarts
Decky. Your installed mods, API key and settings live outside the plugin
folder and are not touched.

There is no automatic update yet, and the plugin cannot update itself:
Decky's plugin folder is owned by root, which is why installing needs your
password in the first place. Automatic updates need this plugin to be served
from a Decky store, which is planned but does not exist yet. Until then,
check [Releases](https://github.com/RedRanger14/decky-nexus/releases) when
you fancy it. Nothing breaks by staying on an older version.

### If you would rather not run a script

Download the latest `Nexus-Mods-<version>.zip` from
[Releases](https://github.com/RedRanger14/decky-nexus/releases), put it in
your **Downloads** folder, then in Gaming Mode: Quick Access Menu -> plug icon
-> **gear** -> **General** -> turn on **Developer mode** at the bottom, then
the **Developer** tab -> **Install from zip** -> Home -> Downloads -> pick the
zip.

### A note on "Install from URL"

That same Developer tab offers **Install from URL**, and it does not work with
this plugin. Decky takes the plugin's name from the URL rather than from the
zip, then stops during install with nothing written to its log, so the screen
sits on "PARSING ZIP FILE" forever. The identical zip installs correctly
through **Install from zip**, and the download and archive were both verified
byte for byte on the device, so it is neither.

Do not use it, and do not wait for it. If it starts working, this section will
say so.

### Beta

This is a **beta**. It changes game files, and while every game it supports
can be returned to vanilla from inside the plugin, back up saves you care
about first. Report anything wrong on
[GitHub Issues](https://github.com/RedRanger14/decky-nexus/issues), the
plugin can package the details for you from the Health page.

## Architecture

Standard Decky plugin layout (from the [official template](https://github.com/SteamDeckHomebrew/decky-plugin-template)):

- `src/`: React/TypeScript frontend rendered in the Quick Access Menu (`@decky/ui`, `@decky/api`).
- `main.py`: Python backend (Nexus API calls, downloads, archive extraction, folder moves).
- `plugin.json`: plugin metadata. Note: no `_root` flag; everything the plugin touches lives in the user's home directory.
- `defaults/`: files bundled alongside the built plugin in the zip.

Frontend ↔ backend communication: `callable()` for request/response, `decky.emit()` + `addEventListener` for backend-initiated events (e.g. download progress).

## Development

Prerequisites: Node.js ≥ 18, pnpm, Python 3.

```bash
pnpm i
pnpm run build   # bundles src/ into dist/index.js via rollup
```

### Deploying to hardware

**From Windows (this project's dev machine): `pnpm run deploy`**: runs [deploy.ps1](./deploy.ps1), which builds the frontend, packs the runtime files, ships them over SSH to `~/homebrew/plugins/Nexus-Mods/` on the device, and restarts `plugin_loader`. Uses Windows' built-in OpenSSH and tar; no Docker or decky CLI needed.

One-time device setup (Steam Deck, in Desktop Mode → Konsole):

1. `passwd`: set a password for the `deck` user if you never have.
2. `sudo systemctl enable --now sshd`: turn on SSH permanently.
3. Note the device's address: `steamdeck.local` usually resolves from Windows; otherwise get the IP from Settings → Internet.

One-time laptop setup:

1. Run `pnpm run deploy` once: it creates `.vscode/settings.json` (gitignored) from the defaults and exits.
2. Edit `.vscode/settings.json`: set `deckip` (hostname or IP) and `deckpass` (the password from step 1 above; used for `sudo` on the device). Leave `deckpass` as-is to be prompted each deploy instead of storing it.
3. Optional, to skip SSH password prompts: `ssh-keygen -t ed25519` then
   `type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh deck@steamdeck.local "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"`

Flags: `-SkipBuild` deploys the current `dist/` as-is; `-PackOnly` builds the tarball without touching the device.

The same script deploys to the Bazzite box, point `deckip`/`deckuser`/`deckdir` at it in `settings.json`. Both targets run Decky; Bazzite treats it as first-class, but expect occasional breakage after SteamOS updates.

Current test device: a Lenovo Legion Go 2 running SteamOS (hostname `steamdeck.local`, user `deck`, behaves identically to a Steam Deck for all plugin purposes). It suspends aggressively; the deploy script probes and retries, but wake the device before deploying. The device password must be printable ASCII (the deploy pipe scrubs non-printable bytes around it).

The template's `.vscode/` bash tasks and the [`decky` CLI](https://github.com/SteamDeckHomebrew/cli) zip pipeline remain the store-submission path later.

### Debugging on-device

- Backend log: `~/homebrew/logs/Nexus-Mods/` on the device, or `journalctl -u plugin_loader` for loader-level errors (plugin failed to load, Python exceptions at startup).
- Frontend console: enable *Allow Remote CEF Debugging* in Decky settings → Developer, then browse to `http://<device-ip>:8081` from the laptop and pick the QuickAccess/SharedJSContext target.

### Dev-loop smoke test

The current hello-world panel has a **Ping backend** button that round-trips a `callable`, displays backend environment info (user, home path, versions), and fires a backend-emitted event that surfaces as a toast, verifying all three communication channels work on real hardware.

## Build Order

1. ✅ Hello-world plugin from the template; confirm dev loop on real hardware (Deck + Bazzite box).
2. Read selected game's app ID; hardcode StS2 → Nexus game domain mapping.
3. Nexus API auth (API key via settings page) + read-only browse UI.
4. Download + extract to `mods/` (Premium flow first).
5. Enable/disable via folder moves + state tracking.
6. Modded-save warning, polish, controller UX pass.
7. Dogfood with StS2 domain expert; QA/test plan.
8. Decide free-user `nxm://` story and second game candidate.

## License

BSD-3-Clause (inherited from the Decky plugin template, see [LICENSE](./LICENSE)).
