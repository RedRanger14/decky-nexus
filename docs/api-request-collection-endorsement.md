# API request: let a client endorse or rate a collection

**Requested by:** Michael Finney (QA)
**Date:** 12 August 2026
**Consumer:** Decky Loader plugin for Nexus Mods (SteamOS/Steam Deck Gaming
Mode mod browser — personal side project with the company's blessing). Any
API consumer that installs collections has the same gap, Vortex included.

## Summary

A signed-in user can endorse a **mod** through the API, but there is no way
to endorse or rate a **collection**. The counts are readable and the
mutations do not exist, so a client can show a collection's endorsements
and its success rating while being unable to contribute either one.

This matters most where it is least visible: someone who installs a 546-mod
collection on a handheld in Gaming Mode has no browser to fall back to.
Their opportunity to say the collection worked is the moment it finished
installing, in the tool that installed it, or never.

## Current state of the schema

Verified 2026-08-12 by introspection against
`https://api.nexusmods.com/v2/graphql` with a personal API key.

**Readable today.** `Collection` exposes `endorsements`, `overallRating`,
`overallRatingCount`, `recentRating`, `recentRatingCount` and
`latestPublishedRevisionRating`; `CollectionRevision` exposes
`overallRating` and `overallRatingCount`.

**Writable today.** Of the 74 mutations on the schema, exactly two concern
endorsement, and both are mod-only:

```
createModEndorsement(modUid: String)
abstainFromModEndorsement(modUid: String)
```

Nothing else in the list touches endorsements or ratings. The
collection-related mutations are all authoring or moderation operations —
`createCollection`, `editCollection`, `listCollection`, `unlistCollection`,
`discardCollection`, `addBadgeToCollection`, `removeBadgeFromCollection`,
`closeCollectionBugReport`, and the moderation set. Probed by name and
rejected as non-existent: `rateRevision`, `endorseCollection`,
`createCollectionEndorsement`, `collectionRating`, `rateCollection`.

There is also no viewer-scoped field on `Collection` — no
`viewerHasEndorsed` or equivalent — so even the current state of the
signed-in user's own endorsement cannot be read back. `viewerHasIgnored`
and `viewerIsBlocked` exist, which suggests the pattern is available and
simply was not extended here.

## What is being asked for

Mirroring the mod shape would be enough, and would need no new concepts:

```
createCollectionEndorsement(collectionId: ID!)
abstainFromCollectionEndorsement(collectionId: ID!)
```

plus a viewer field on `Collection` so a client can render the correct
state rather than guessing:

```
viewerEndorsement  # Endorsed | Abstained | Undecided
```

If the success rating (thumbs up/down on a revision) is the more
appropriate mechanism for collections, a `rateRevision(revisionId, rating)`
mutation and a matching viewer field would serve the same purpose. Either
one closes the gap; the current position is that neither is reachable.

Same-shaped error codes as the mod endpoint would be welcome, so a client
can explain a refusal in plain language rather than surfacing a code —
`TOO_SOON_AFTER_DOWNLOAD` in particular is worth having, since a collection
install ends with the user right there, well inside any cooldown.

## What the plugin does in the meantime

Endorses the mods, not the collection. Individual mods can be endorsed from
their own page, and as of v0.90.0 framework mods (SKSE, SMAPI, REFramework)
can be endorsed straight from the Quick Access Menu row that installs them
— those authors were previously unreachable, because nobody has a reason to
open the mod page for something a Step button installed.

Collection curators get nothing, which is the wrong outcome for the people
whose work is the reason a 546-mod install boots at all.
