# Nexus Mods v3 REST API — findings (2026-07-23)

Source: the official `nexus-api-typescript-sdk` (Nexus Mods, SDK 1.0.0,
OpenAPI-generated) + live probes from the test device with a personal
apikey. Base URL: `https://api.nexusmods.com/v3`.

We don't need the SDK itself (TypeScript; our HTTP lives in main.py) —
its value is as **the authoritative map of the v3 surface** (zod schemas
in `src/services/*/models` document exact response shapes).

## Verified live with a personal apikey

- `GET /games/{domain}/mods/{game_scoped_id}` → 200. Returns the
  **global id** (`"id": "7318624284988"` for SkyUI 12604) — the key into
  every `/mods/{id}/...` and `/mod-files/...` endpoint. v3 uses a global
  id space, not per-game ids.
- `GET /games/{domain}/trending-mods` → 200 (public top-5 feed,
  excludes adult + unpublished).
- `GET /mods/{game_scoped_id}/...` → 404 (confirms: global ids only).
- `GET /donations/wallet` → 401 "Please provide an authentication
  method" — account-scoped endpoints want user OAuth (access token),
  not the apikey.

## Endpoint map (read-side highlights)

| Area | Endpoints | Why we care |
|---|---|---|
| Dependencies | `/mod-file-versions/{id}/dependencies`, `.../dependencies/ranges/materialized`, batch variants, `/mods/{id}/file-versions-with-dependency-counts` | **Structured, versioned requirements** — could replace name-matching in `classifyRequirement` and make "install all required" exact. Needs global ids via the game-scoped lookup first. |
| Mods | `/mods/batch`, `/games/{domain}/mods/{game_scoped_id}`, `/games/{domain}/trending-mods` | Batch display details incl. status (moderated/hidden/removed distinguishable) + adult flag. |
| Files | `/mod-files/{id}`, `/mod-files/{id}/versions`, `/games/{domain}/mod-file-versions/{game_scoped_id}` | File/version metadata in the new id space. |
| Games | `/games` (search), `/games/{domain}/dlcs` | DLC lists — relevant to dependency DLC edges. |
| Donations | `/donations/wallet`, `/donations/transactions/send`, store endpoints | Donation Points exist in the API — sending DP to authors is possible **but OAuth-gated**. Future: in-plugin "support this author" with DP once we add a user-auth flow. |
| Collections | `POST /collections` etc. | Author/upload side, not consumer side. |

## Gaps that stay on the internal-API-request list

- No users/content-preferences endpoint in v3 either (no Users service
  at all) — the age-verified adult-content status request stands (see
  api-request-mod-media.md).
- Download links / collection browsing remain v1 + GraphQL v2 — v3
  doesn't cover them yet.

## Backlog seeds

- Requirements v2: game-scoped lookup → global id → materialized
  dependency ranges (batch) → exact required-mod graph.
- Donation Points support once a user OAuth flow exists (apikey can't).
