# Nexus Mods API — research notes

Mined 2026-07-15 from three official open-source repos (permalinked to pinned commits by a
research pass; see the linked files for ground truth). Sources:

| Repo | Commit | Status |
|---|---|---|
| [Nexus-Mods/NexusMods.App](https://github.com/Nexus-Mods/NexusMods.App) | `48284f3` | Archived Feb 2026 — reflects final/current API usage |
| [Nexus-Mods/node-nexus-api](https://github.com/Nexus-Mods/node-nexus-api) | `97ad222` | Active (official TS client) |
| [Nexus-Mods/Vortex](https://github.com/Nexus-Mods/Vortex) | `7d88cee` | Active |

## A. Authentication

### OAuth 2.0 (Authorization Code + PKCE) — how NexusMods.App logs in
- Authorize: `https://users.nexusmods.com/oauth/authorize`; token: `https://users.nexusmods.com/oauth/token`.
- App client id `"nma"`, redirect `nxm://oauth/callback`; Vortex client id `"vortex_loopback"`, redirect `http://127.0.0.1:<port>` (temporary local HTTP server). **Client ids + redirects are whitelisted server-side — we can't borrow them; a Decky OAuth client would need Nexus to register one** (internal conversation).
- Params: `response_type=code`, `scope=openid profile email`, PKCE S256, `state` (UUID). Token response: `access_token` (Bearer), `expires_in`, `refresh_token`, `created_at`. Refresh: `grant_type=refresh_token`.
- Identity for OAuth sessions: `GET https://users.nexusmods.com/oauth/userinfo` → `{sub, name, avatar, membership_roles[]}`, roles incl. `premium`, `lifetimepremium`.
- Key files: `NexusMods.Networking.NexusWebApi/Auth/OAuth.cs`, `OAuthJob.cs`, `Auth/OAuth2MessageFactory.cs`; Vortex `renderer/src/extensions/nexus_integration/util/oauth.ts`.

### Personal API key (what we use in v1)
- Header `apikey: <key>` (case-insensitive; node client uses `APIKEY`). App supports it as an alternate mode (`Auth/ApiKeyMessageFactory.cs`), validated via v1 `users/validate.json`.

### Required identifying headers (Acceptable Use Policy)
- `Application-Name` (keep stable across versions), `Application-Version`, sane `User-Agent`.
- AUP (help article 114): **personal API keys are for testing/personal use only — public-facing apps must register for an application-specific key**; never store users' keys server-side; no en-masse fetching to rehost; no blank/impersonating metadata.

## B. REST API v1 (`https://api.nexusmods.com/v1`)

- `GET /v1/users/validate.json` → `user_id, key, name, email, profile_url, is_premium, is_supporter` (+ legacy `is_premium?`/`is_supporter?` duplicates). Does not consume rate-limit quota.
- Mod lists per game (fixed 10-ish results, no paging/search):
  `GET /v1/games/{domain}/mods/latest_added|latest_updated|trending` → `IModInfo[]`
  `GET /v1/games/{domain}/mods/updated?period=1d|1w|1m` → `[{mod_id, latest_file_update, latest_mod_activity}]`
- `IModInfo`: `mod_id, game_id, domain_name, category_id, name?, summary?, description?(bbcode), version, author, uploaded_by, user{}, status, available, picture_url?, created/updated timestamps, endorsement_count, mod_downloads, mod_unique_downloads, contains_adult_content`. Optional fields are absent on hidden/moderated mods.
- Files: `GET /v1/games/{d}/mods/{modId}/files.json` → `{files: IFileInfo[], file_updates}`. `IFileInfo`: `file_id, category_id (1=main,2=patch,3=optional,4=old,6=deleted,7=archived), category_name, name, file_name, version, size_kb/size_in_bytes, uploaded_timestamp, is_primary, description, changelog_html, external_virus_scan_url`.
- **Download link:** `GET /v1/games/{d}/mods/{modId}/files/{fileId}/download_link.json`
  - Premium: plain call works (Premium-only endpoint).
  - Free users: must append `?key=<key>&expires=<unix>` from a website-generated nxm:// link.
  - Response: `[{name, short_name (CDN id), URI}]` — note uppercase `URI`. Links expire in minutes; don't store.
- Rate limits: **20,000/day (resets 00:00 GMT), then 500/hour** (current help-article numbers; the old 2,500/100 figures are outdated). Read response headers `x-rl-daily-limit/remaining/reset`, `x-rl-hourly-*`. node client self-throttles (token bucket, warns not to manipulate) and on 429 blocks until the next hour if quota is truly gone. 521/HTML bodies = API down/CDN block — handle gracefully.

## C. GraphQL v2 (`https://api.nexusmods.com/v2/graphql`)

- The App's primary data plane (StrawberryShake client). Auth: OAuth Bearer; in API-key mode the App sends v2 requests **unauthenticated** (public reads work anonymously). node client sends `APIKEY` header on v2 but nothing confirms the server honors it for viewer-specific fields — **treat "v2 auth with API key" as unverified** (verify empirically; public reads are what our browse needs anyway).
- **Mod search/browse — the query our browse UI wants:**
  `mods(count, offset, filter: ModsFilter, sort: [ModsSort!]) : ModPage { nodes: [Mod!]!, nodesCount }`
  - `ModsFilter`: `gameDomainName/gameId [EQUALS]`, `name [wildcard]`, `nameStemmed`, `author`, `categoryName`, `adultContent`, `downloads`, `endorsements`, `createdAt/updatedAt`, `fileSize`, nested `filter` + `op: AND|OR`.
  - `ModsSort`: `createdAt, updatedAt, downloads, endorsements, name, relevance, random`.
  - v2 `Mod`: `modId, uid, name, summary, version, author, uploader{}, pictureUrl, thumbnailUrl(+Large/Blurred), downloads, endorsements, fileSize, createdAt, updatedAt, adultContent, status, game{}` + viewer fields (need auth).
- Other v2 roots used by official clients: `game(id|domainName)`, `legacyMods(ids)`, `modFiles(modId, gameId)`, `modFilesByUid`, `categories(gameId, global)`, `collection(slug)`, `collectionRevision(… ){ downloadLink }`, `collectionsV2(filter, sort, count, offset)`, `modRequirements(modId, gameDomainName)`, `fileHashes(md5s)`.
- v2 `uid` = 64-bit `(gameId << 32) | modId`, passed as string.

## D. nxm:// protocol

```
nxm://{gameDomain}/mods/{modId}/files/{fileId}?key={key}&expires={unix}&user_id={userId}
nxm://{gameDomain}/collections/{slug}/revisions/{rev|latest}
nxm://oauth/callback?code=…&state=…
```

Linux handler registration (NexusMods.App pattern — the model for a future free-user flow):
1. Write a `.desktop` file to `$XDG_DATA_HOME/applications/` with `MimeType=x-scheme-handler/nxm` and `Exec=<binary> %u`.
2. `update-desktop-database <dir>`.
3. `xdg-settings set default-url-scheme-handler nxm <desktop-id>`.
(Gotcha the App handles: write a `#!/bin/sh` wrapper if the Exec path needs escaping — xdg-utils bug.)
Key files: `NexusMods.Backend/OS/LinuxInterop.Protocol.cs`, `com.nexusmods.app.desktop`, `Abstractions.NexusWebApi/Types/NXMUrl.cs`; Vortex `NXMUrl.ts` (regex parser). Vortex has no Linux desktop-file registration (Electron/Windows-oriented).

## E. Implications for this plugin

1. **v1 (now):** personal API key + v1 validate is correct for dogfooding; browse via v2 public `mods` query (or v1 trending/latest fallback); downloads via v1 `download_link` (owner is Premium).
2. **Before public distribution:** register an application with Nexus (app-specific key at minimum; ideally an OAuth client id + whitelisted redirect — loopback `127.0.0.1` like Vortex fits a Decky backend well since we can bind a local port). AUP forbids shipping on personal-key-only UX.
3. **Free-user flow later:** nxm handler registration on SteamOS Gaming Mode is the open design question — the App's desktop-file approach assumes a desktop session; Gaming Mode routing needs investigation.
4. **Licensing:** NexusMods.App and Vortex are GPL-3.0 — patterns and endpoint facts are fine to use; do not copy code verbatim into this BSD-3 repo without resolving licensing.
