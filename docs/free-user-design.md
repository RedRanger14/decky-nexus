# Free-user downloads — design doc

**Status:** design for review, no code. **Author:** plugin project, 2026-07-16.

## The constraint (why "just slower" isn't possible)

The API's `download_link.json` endpoint refuses free accounts outright
(HTTP 403) unless the request carries `key` + `expires` query parameters.
Those tokens are minted **only by the website's "Slow download" button**,
delivered to mod managers as an `nxm://` protocol link:

```
nxm://{gameDomain}/mods/{modId}/files/{fileId}?key={key}&expires={unix}&user_id={id}
```

This is a business gate, not a bandwidth setting: the free flow routes users
through the website, where ads and the Premium upsell live. No API parameter
requests a "capped-speed link" directly. (Sources: NexusMods.App and Vortex
implementations, both verified in docs/research/nexus-api-notes.md; the API
Acceptable Use Policy.)

Consequently a compliant free-user flow must orchestrate the website
round-trip, not bypass it.

## Proposed architecture: the nxm:// relay

1. **Handler registration (backend, one-time):** write a user-level desktop
   entry (`~/.local/share/applications/nexus-decky-nxm.desktop`) with
   `MimeType=x-scheme-handler/nxm` whose Exec is a tiny relay script, run
   `update-desktop-database`, then
   `xdg-settings set default-url-scheme-handler nxm <id>`. This is exactly
   NexusMods.App's Linux pattern and needs no root on SteamOS (all under
   $HOME).
2. **Relay script:** appends the received nxm URL (plus timestamp) to a queue
   file under the plugin's runtime dir. Nothing else - no parsing, no network.
3. **Backend watcher:** the plugin backend polls/watches the queue; on a new
   nxm URL it parses game/mod/file/key/expires, calls `download_link.json`
   with the token, and runs the existing download→extract→install pipeline.
   Every downstream feature (progress, records, badges) is unchanged.
4. **Frontend UX (free account detected via `is_premium: false`):**
   - Install button becomes **"Get via nexusmods.com (free)"**.
   - Press → plugin opens the mod's files page in a browser and shows a
     "waiting for your download click…" state.
   - User presses **Slow download** on the site → nxm fires → relay → plugin
     toasts "Downloading <mod>…" and finishes automatically.
   - Timeout/cancel path returns to the normal button.

## Open questions (ranked by risk)

1. **Does Gaming Mode's embedded browser dispatch nxm:// to the OS handler?**
   The whole one-device UX hinges on this. Steam's CEF browser may swallow
   unknown schemes. **Spike required** (fastest test: register a dummy
   handler that writes a file; open a mod page in the Gaming Mode browser;
   click Slow download). If it doesn't dispatch: fallback is Desktop Mode
   for the click (ugly but honest), or a companion QR flow (click happens on
   the user's phone → still needs the token to reach the device: not viable
   without server help - see §Strategic).
2. **Website login inside the Gaming Mode browser** - separate cookie jar;
   user must log in once there. Acceptable friction, needs testing on
   hardware (virtual keyboard in browser overlay).
3. **Handler survival** across SteamOS updates (user-level files usually
   survive; verify) and coexistence with Vortex-on-SteamOS later (both would
   claim nxm; last-writer-wins via xdg-settings - detect and warn).
4. **Queue security:** the relay accepts any nxm URL fired at the system.
   Parse strictly (regexes from NXMUrl.cs patterns), validate domain/ids
   against supported games, never shell out with URL content.

## Kill switch (owner requirement)

Free-user flow ships behind a flag, **default off**:

- `FREE_FLOW_ENABLED` in the games/config registry; when off, free accounts
  see today's behavior (a clear "Premium account required for direct
  downloads" message with the mod's website page as reference).
- Rationale: the company may reasonably object that a couch flow, even one
  that opens the website, changes the free-user ad economics. Turning it on
  is a decision for the internal conversation, not a default.

## Strategic alternative (recommended in parallel)

The clean fix is upstream: a **registered-application affordance** in the
API - e.g. server-capped free download links issued to approved clients, or
a device-code flow where the website click can happen on any logged-in
device and the token is delivered via the API rather than a local protocol
handler. That preserves the business gate (the click still happens on
nexusmods.com, ads included) while removing the fragile OS-handler hop.
This plugin is the concrete motivating case study; pairs naturally with the
existing application-registration conversation.

## Phasing

- **Phase 0 (shipped):** Premium-only with graceful messaging.
- **Phase 1 (spike, ~day):** Gaming Mode browser nxm dispatch test on the
  Legion Go. Decides everything downstream.
- **Phase 2 (build, ~days):** relay + watcher + free-user UX, behind the
  kill switch, validated with a free test account (QA has them).
- **Phase 3 (strategic):** upstream API affordance proposal.

## Verification notes

- All claims about token behavior verified against a Premium account +
  public client source; the exact free-account API responses should be
  re-verified with a **free test account** before Phase 2 (owner can source
  one internally).
