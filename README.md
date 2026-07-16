# Nexus Mods — Decky Loader Plugin

Browse, download, install, and enable/disable [Nexus Mods](https://www.nexusmods.com) content for the currently selected game — entirely from Gaming Mode with a controller, on SteamOS and Bazzite. No Desktop Mode required.

**Status:** early development. v1 targets a single game (Slay the Spire 2) as the proving ground.

See [starterFile.md](./starterFile.md) for the full project handover: motivation, market context, v1 scope, constraints, and build order.

## v1 Scope

- Detect the currently selected game (app ID via the Steam client APIs Decky exposes), mapped to its Nexus game domain (hardcoded for StS2 in v1).
- Controller-friendly browse/search UI for that game's Nexus mods, built on the Nexus Mods API.
- Download and extract archives into the game's `mods/` folder (Premium direct-download flow first).
- Enable/disable by moving mod folders between `mods/` and `mods-disabled/`, with state tracked in plugin config.
- Surface StS2's modded-vs-unmodded save file warning.

Out of scope for v1: multi-game support, FOMOD installers, load order, Proton-prefix games, collections, Vortex integration.

## Architecture

Standard Decky plugin layout (from the [official template](https://github.com/SteamDeckHomebrew/decky-plugin-template)):

- `src/` — React/TypeScript frontend rendered in the Quick Access Menu (`@decky/ui`, `@decky/api`).
- `main.py` — Python backend (Nexus API calls, downloads, archive extraction, folder moves).
- `plugin.json` — plugin metadata. Note: no `_root` flag; everything the plugin touches lives in the user's home directory.
- `defaults/` — files bundled alongside the built plugin in the zip.

Frontend ↔ backend communication: `callable()` for request/response, `decky.emit()` + `addEventListener` for backend-initiated events (e.g. download progress).

## Development

Prerequisites: Node.js ≥ 18, pnpm, Python 3.

```bash
pnpm i
pnpm run build   # bundles src/ into dist/index.js via rollup
```

### Deploying to hardware

**From Windows (this project's dev machine): `pnpm run deploy`** — runs [deploy.ps1](./deploy.ps1), which builds the frontend, packs the runtime files, ships them over SSH to `~/homebrew/plugins/Nexus-Mods/` on the device, and restarts `plugin_loader`. Uses Windows' built-in OpenSSH and tar; no Docker or decky CLI needed.

One-time device setup (Steam Deck, in Desktop Mode → Konsole):

1. `passwd` — set a password for the `deck` user if you never have.
2. `sudo systemctl enable --now sshd` — turn on SSH permanently.
3. Note the device's address: `steamdeck.local` usually resolves from Windows; otherwise get the IP from Settings → Internet.

One-time laptop setup:

1. Run `pnpm run deploy` once — it creates `.vscode/settings.json` (gitignored) from the defaults and exits.
2. Edit `.vscode/settings.json`: set `deckip` (hostname or IP) and `deckpass` (the password from step 1 above; used for `sudo` on the device). Leave `deckpass` as-is to be prompted each deploy instead of storing it.
3. Optional, to skip SSH password prompts: `ssh-keygen -t ed25519` then
   `type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh deck@steamdeck.local "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"`

Flags: `-SkipBuild` deploys the current `dist/` as-is; `-PackOnly` builds the tarball without touching the device.

The same script deploys to the Bazzite box — point `deckip`/`deckuser`/`deckdir` at it in `settings.json`. Both targets run Decky; Bazzite treats it as first-class, but expect occasional breakage after SteamOS updates.

Current test device: a Lenovo Legion Go 2 running SteamOS (hostname `steamdeck.local`, user `deck` — behaves identically to a Steam Deck for all plugin purposes). It suspends aggressively; the deploy script probes and retries, but wake the device before deploying. The device password must be printable ASCII (the deploy pipe scrubs non-printable bytes around it).

The template's `.vscode/` bash tasks and the [`decky` CLI](https://github.com/SteamDeckHomebrew/cli) zip pipeline remain the store-submission path later.

### Debugging on-device

- Backend log: `~/homebrew/logs/Nexus-Mods/` on the device, or `journalctl -u plugin_loader` for loader-level errors (plugin failed to load, Python exceptions at startup).
- Frontend console: enable *Allow Remote CEF Debugging* in Decky settings → Developer, then browse to `http://<device-ip>:8081` from the laptop and pick the QuickAccess/SharedJSContext target.

### Dev-loop smoke test

The current hello-world panel has a **Ping backend** button that round-trips a `callable`, displays backend environment info (user, home path, versions), and fires a backend-emitted event that surfaces as a toast — verifying all three communication channels work on real hardware.

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

BSD-3-Clause (inherited from the Decky plugin template — see [LICENSE](./LICENSE)).
