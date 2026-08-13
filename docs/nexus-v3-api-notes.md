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

## `legacyMods(ids:)` truncates at 20, silently

**Found:** 13 August 2026, on device, against
`https://api.nexusmods.com/v2/graphql`.

`legacyMods(ids: [{gameId, modId}, ...])` returns **at most 20 nodes**
regardless of how many ids you pass. Measured directly:

| ids asked | nodes returned |
|---|---|
| 10 | 10 |
| 20 | 20 |
| 21 | 20 |
| 25 | 20 |
| 40 | 20 |

There is **no error, no `errors` entry, and no page cursor** on the
connection. The surplus ids are simply absent from `nodes`, so a caller
cannot distinguish "that mod has no data" from "the API stopped answering".

**How it bit us.** 27 Slay the Spire 2 mods were installed. `check_updates`
asked about all 27, got 20, and RitsuLib - three minor versions behind a
game build that was printing *"Loaded 21 mods WITH ERRORS"* across the main
menu - was one of the seven the API dropped. The plugin reported "no updates
available". A separate call site batched in 40s and was losing half of every
batch. On the 546-mod New Vegas collection, 526 mods were being ignored.

**Worth raising with the API team.** Silent truncation is the failure mode a
client cannot defend against by inspection - it looks exactly like a
successful, complete response. Either of these would fix it:

- return an error when `ids` exceeds the limit, or
- expose the limit on the connection (`pageInfo`/`totalCount`) so a client
  can tell it has been cut off.

Documenting the number would help too; nothing in the schema states it.

**Our workaround:** `_legacy_mods_in_batches` (main.py) chunks every bulk id
lookup at `LEGACY_MODS_PAGE = 20`. Its tests use a fake endpoint that also
answers only the first 20, so any future caller that skips the helper fails
in tests rather than in the field.

## `modRequirements` has three fields and we were using one

**Found:** 13 August 2026, prompted by Michael: *"file to file and DLC
requirements is something the website has put a lot of work into tackling and
it feeds vortex via api so I would have thought its available to us too"*.
It is.

`Mod.modRequirements` (GraphQL v2):

| field | what it gives | used? |
|---|---|---|
| `nexusRequirements` | required Nexus mods, with `modId` | yes, since v0.x |
| **`dlcRequirements`** | **required game DLC, by name** | **now, v0.145.0** |
| `modsRequiringThisMod` | reverse dependencies — who needs this mod | not yet |

`dlcRequirements` returns `[ModRequirementsDlc]`, each
`{ notes, gameExpansion { id gameId name } }`. Verified live:

```
newvegas mod 65000 "Rigged Odds - Casino Cheat Mod"
  -> [{"gameExpansion": {"gameId": "130", "id": "1,130",
                         "name": "Dead Money"}, "notes": ""}]
```

**Why it matters.** `DLC_MASTER_NAMES` in main.py holds this fact by hand for
four games, and only catches a missing DLC *after* a failed boot, by reading
the plugin names inside a downloaded mod. This is the same fact from the
authority, available **before the download** — which is the difference
between "Dead Money is required, and you do not own it" on the mod page and
`mil.esp is missing required files: DeadMoney.esm` after a 2 GB download and
a crash.

`modsRequiringThisMod` is the other half of a health check: it answers "what
breaks if I remove this?", which is currently only inferrable from Godot mod
manifests and not at all for Bethesda games.

Also on `ModFile`: `requirementsAlert` (Int) — not yet investigated, but the
name suggests per-FILE requirement flags, which is the "file to file" half of
Michael's point.
