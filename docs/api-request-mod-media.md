# API request: expose per-mod image galleries in GraphQL v2

**Requested by:** Michael Finney (QA)
**Date:** 16 July 2026
**Consumer:** Decky Loader plugin for Nexus Mods (SteamOS/Steam Deck Gaming
Mode mod browser — internal side project, may formalize). Any API consumer
rendering mod pages would benefit equally (Vortex successor work, third-party
managers using the public GraphQL API).

## Summary

Mod pages on the website have an IMAGES tab (author-uploaded gallery — e.g.
`stardewvalley` mod 2400 has 20+ images; `slaythespire2` mod 854 has 11). This
gallery is **not reachable through the public GraphQL v2 API**: clients can
render at most the single header image. Request: make a mod's gallery media
queryable by mod identity.

## Current state of the schema (verified 2026-07-16 via introspection on
`https://api.nexusmods.com/v2/graphql`)

- The `Mod` type exposes only single-image fields:
  `pictureUrl`, `thumbnailUrl`, `thumbnailLargeUrl`, `thumbnailBlurredUrl`,
  `thumbnailLargeBlurredUrl`. No gallery/media collection field.
- The root `media(filter: MediaSearchFilter, sort, count, offset)` query
  serves the *community* media section. `MediaSearchFilter` input fields are:
  `filter, op, gameId, gameName, createdAt, adultContent, type, owner,
  mediaStatus, generalSearch` — **no mod id / mod uid**, so it cannot be
  scoped to one mod's gallery (and author galleries may not be in that
  dataset at all).
- The `Image` type already models everything a gallery needs:
  `url, thumbnailUrl, title, caption, adult, createdAt, owner, id, …` — so no
  new object types appear necessary.

## Requested change (either variant works)

**Option A — field on `Mod` (preferred by us):**

```graphql
type Mod {
  # ...existing fields...
  mediaGallery(count: Int, offset: Int): ImagePage!
}
```

Usage we'd issue:

```graphql
{
  legacyMods(ids: [{ gameId: 1303, modId: 2400 }]) {
    nodes {
      name
      mediaGallery(count: 20) {
        nodes { url thumbnailUrl title caption adult }
        nodesCount
      }
    }
  }
}
```

**Option B — extend `MediaSearchFilter`:**

Add `modId` (and/or `modUid`) comparison fields to `MediaSearchFilter`, with
author-gallery media included in that dataset:

```graphql
{
  media(filter: { gameId: [{ value: "1303", op: EQUALS }],
                  modId: [{ value: "2400", op: EQUALS }] }, count: 20) {
    nodes { ... on Image { url thumbnailUrl title caption adult } }
  }
}
```

## Details that matter to consumers

- **Auth:** should work with the same access as other public mod metadata
  (anonymous / apikey), matching `mods` and `legacyMods` behavior.
- **Ordering:** the author's page ordering (gallery order) if available,
  else `createdAt`.
- **Adult flags per image** are needed (the `Image.adult` field exists) so
  clients can filter without dropping the whole gallery.
- Thumbnails at reasonable sizes matter more than originals for handheld
  clients; existing `thumbnailUrl` is fine if it's a few hundred px wide.

## Why not workarounds

- Scraping the website gallery violates the API acceptable-use spirit, is
  Cloudflare-hostile, and breaks on redesigns.
- The v1 REST API exposes only `picture_url` (single image).

## Impact if shipped

Mod detail views in controller-first/handheld clients can show what a mod
actually looks like — for content mods (reskins, portraits, maps) images are
effectively the product description. Our plugin's detail page already
reserves the layout for a gallery; consumption is a one-day change once the
field exists.

---

## Additional request: account content preferences (2026-07-23)

The plugin filters adult content with a local toggle (off by default).
The right behavior is adopting the user's **site** preference, but the
v2 GraphQL schema exposes no `currentUser`/`viewer` query to apikey
clients (verified by probe), and v1 `users/validate.json` doesn't
include content preferences either.

**Request**: expose the account's content-preference flags (adult
content on/off, and ideally per-category preferences) to personal-
apikey-authenticated clients — e.g. a `currentUser { contentPreferences }`
field or an extension of `users/validate.json`.

**Consumer**: decky-nexus syncs its filter default from the account, so
the handheld experience matches what the user chose on the website.

### Update 2026-07-23: this is now also an age-verification issue

The plugin's local adult-content toggle has been **removed** (v0.15.1,
hard-locked off). Rationale: UK OSA-class laws require age verification
before adult content is shown; verification happens on the Nexus Mods
platform (with real per-check costs); the API gives clients no way to
know whether the account passed it. A client-side opt-in would let an
unverified user bypass the site's age gate, so no opt-in can exist here
until the API can answer "is this account verified + opted in?".

**Revised request**: expose the account's *age-verified* content
preferences (not just the raw preference flags) to authenticated API
clients. A single boolean ("account may see adult content") is enough
for parity. The v3 REST API (api.nexusmods.com/v3) already has
account-scoped endpoints (donations wallet) behind user OAuth - that
auth path may be the natural home for this.
