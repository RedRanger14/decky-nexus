# Nexus Mods: Decky Loader Plugin (Unofficial)

Browse, download, install, and enable/disable [Nexus Mods](https://www.nexusmods.com) content for the currently selected game, entirely from Gaming Mode with a controller, on SteamOS and Bazzite. No Desktop Mode required.

> **Unofficial, and in beta.** This is a community-built plugin. It is not an
> official Nexus Mods product, is not supported by Nexus Mods, and nothing
> here is endorsed by them. It uses their public API like any other
> third-party client. Bugs are ours, so please report them here rather than
> to Nexus Mods support.

**Status:** 1.0 beta. Supports the games listed below; Slay the Spire 2 was
the proving ground and is no longer the limit. Beta means the supported games
have each been installed, modded and played on real hardware, which is not
the same as everything working. Expect rough edges, and back up saves you
care about.

## Supported games

Nine games, each one installed, modded and played on real hardware before it
shipped. That is what supported means here: not that the code has a config
entry for it, but that someone finished a session with mods running.

1. Cyberpunk 2077
2. Elden Ring
3. Fallout 4
4. Fallout: New Vegas
5. Resident Evil 4
6. Skyrim Special Edition
7. Slay the Spire 2
8. Stardew Valley
9. The Witcher 3

### On the roadmap

Groundwork exists for these and they are NOT ready. They may appear in the
plugin; treat anything you do with them as untested.

- Fallout 3
- Hollow Knight: Silksong
- Mount & Blade II: Bannerlord
- Palworld

Want one moved up, or a new one added? Open a [game request][issues] and say
which. Adding a game means installing it, modding it and playing it on
hardware, so the list grows slowly and on purpose.

[issues]: https://github.com/RedRanger14/decky-nexus/issues

## What it does

- **Finds the game you are playing** and shows its Nexus mods, with search,
  sort and curated rails, on a controller.
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
- **Endorsements and author support links**, so the people who made the mods
  still get credit.

Not supported yet: manual load order editing, Vortex profile import, and
mods that need a Windows tool to install.

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

This is the only part that needs Desktop Mode, and it takes about two
minutes. Everything after it happens in Gaming Mode on the controller.

**1. Switch to Desktop Mode.** Steam button -> **Power** -> **Switch to
Desktop**. The Deck reboots into a desktop.

**2. Open this page on the Deck**, in the browser there. You want to copy the
command below rather than type it, because it is long and a single wrong
character looks exactly like a broken install.

**3. Copy this line:**

```sh
curl -L https://raw.githubusercontent.com/RedRanger14/decky-nexus/main/install.sh | sh
```

**4. Open Konsole.** Bottom-left menu (the launcher icon) -> type `konsole`
-> open it.

**5. Paste and press Enter.** Pasting in Konsole is **Ctrl+Shift+V**, or
right-click -> Paste. If you have no keyboard attached, **Steam button + X**
brings up the on-screen one.

**6. Type your password when it asks.** Nothing appears on screen as you
type, which is normal. This is the password for the Deck itself; if you have
never set one, run `passwd` first and pick one.

*(Decky owns its plugin folder as root, so installing anything into it needs
your password. Decky's own installer asks for the same thing.)*

**7. Wait for it to say `Done`.** It prints each step as it goes: finding the
release, downloading, extracting, installing, restarting Decky.

**8. Return to Gaming Mode.** The **Return to Gaming Mode** icon is on the
desktop.

**9. Open the Quick Access Menu** (the **...** button) -> the **plug** icon
-> **Nexus Mods**.

The panel footer shows the version and the words `unofficial beta`. If your
game is supported and installed, it will already have found it.

### Updating

Run the same line again. It fetches the newest release, checks it is complete
before removing the old version, and restarts Decky. Your installed mods, API
key and settings live outside the plugin folder and are not touched.

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
