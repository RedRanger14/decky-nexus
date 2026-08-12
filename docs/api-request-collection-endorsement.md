# Collection endorsement: what exists, and the one thing that does not

**Date:** 12 August 2026
**Verified against:** `https://api.nexusmods.com/v2/graphql` by introspection
and against the `nexus-api` source.

> **Correction.** An earlier version of this document said collection
> endorsement was impossible through the API. That was wrong, and the
> mistake is worth recording because it is easy to repeat: introspecting
> `__type(name: "Mutation") { fields { name } }` **silently omits deprecated
> fields**. The schema has 74 mutations that way and 96 with
> `fields(includeDeprecated: true)`. The mutation that endorses a collection
> is one of the 22 that were hidden.

## What already works

```graphql
mutation {
  endorse(modelId: 12345, modelType: "Collection", abstain: false) {
    success
  }
}
```

`Collection` includes the `Endorsable` concern, and `Commands::EndorseHandler`
carries the comment *"this command is not fully integrated with mods yet and
for now it should only be used with the collections"* — so collections are
what this path is for, despite the generic name.

It is marked deprecated (*"This mutation will be replaced using Interfaces
and Global IDs"*), but there is no replacement yet, and it is the only route.

Two rules differ from mods and both matter to a client:

- **The first download must be more than 12 hours old**, not 15 minutes
  (`EndorseHandler` passes `12.hours.ago` to `check_download_time`). Somebody
  who has just finished installing a collection **cannot endorse it yet** —
  which is precisely the moment they would want to.
- Endorsing your own collection, and endorsing one whose curator has set
  `allow_rating` to 0, are both refused.

Separately, `rate(id:, type: CollectionRevision, rating: positive | negative
| abstained)` sets the success rating on a revision. Also deprecated, also
live. `Ratable` is `CollectionRevision | Mod`.

## The actual gap: nothing reports the viewer's own state

`CollectionType` exposes `endorsements`, `overallRating`, `recentRating` and
their counts — all aggregate. There is **no `viewerEndorsed`**, and no
viewer-scoped rating field on `Collection` or `CollectionRevision`. The v1
`/user/endorsements.json` route serves `CurrentModEndorsement` only, so
neither API can answer "have I already endorsed this?".

`ModType` has had `viewerEndorsed` since the mod endorsement work.
Collections were simply never given the equivalent.

This is not cosmetic. Endorsements are append-only and the newest row wins,
so offering an "Endorse" toggle to someone who cannot be checked means
inviting an already-endorsed user to **silently abstain**.

## The PR

Branch `mf/collection-viewer-endorsed` on `Nexus-Mods/nexus-api`, off
`master`. Adds `CollectionType.viewerEndorsed: Boolean` mirroring `ModType`
exactly — true endorsed, false abstained, null never decided or logged out —
backed by `Loaders::CurrentCollectionEndorsementLoader` so a page of
collections costs one query.

Collections need more work than mods did. Mods have
`current_mod_endorsements`, which has already collapsed the history to one
row per user; collection endorsements live in the append-only polymorphic
`endorsements` table, so the loader takes the latest row per collection with
`DISTINCT ON`, breaks a `created_at` tie on `id`, and filters on
`endorsable_type` because the table is shared.

Specs cover loader batching, changed-mind ordering, the polymorphic type
filter, the error path, and each state through a real query including logged
out. **Not yet run** — the suite needs `devspace enter --container api`,
which is not available on this machine.

`rate` has the same missing viewer field and is deliberately left out to keep
the PR reviewable.

## What the plugin does today

As of v0.91.0 the collection page has an Endorse button, using the `endorse`
mutation. It is **one-way**: pressing it always endorses, never abstains.
Until `viewerEndorsed` lands there is no way to know the starting state, and
a toggle built on a guess is the exact failure described above. The 12-hour
refusal is caught and rendered as a sentence rather than
`TOO_SOON_AFTER_DOWNLOAD`.
