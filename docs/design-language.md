# Design language

Rules for the plugin's own full-screen pages (Store, Mod detail,
Collection detail, Downloads, My Mods, Updates). The QAM panel
deliberately stays native Steam styling and is out of scope.

Written down because "looks about right" kept producing rows of buttons
at four different widths. Every size here should be derivable from a
rule, not chosen by eye.

## Symmetry and asymmetry are both deliberate

The governing rule: **if two elements are not the same size, the
difference must come from a rule the eye can infer.** A button that is
merely *bigger* reads as a mistake; a button that spans exactly the two
beneath it reads as a decision.

Concretely, on the detail pages:

- Every button in a row is the **same width**. They share the row's
  space equally (`flexBasis: 0; flexGrow: 1`), so they shrink and grow
  together as actions appear and disappear. No button gets a wider slot
  for being more important.
- The page's **one main action** (Install this mod / Install the
  collection) sits alone on the row above, and is **exactly as wide as
  the whole row beneath it**. Its prominence comes from spanning them
  and from being taller (44px vs default), not from an arbitrary width.
- This is what `ACTION_COLUMN` + `actionColumnWidth(count)` in
  `theme.ts` implement: the column is `count × 240 + gaps` wide, the
  hero is `100%` of it, and the secondaries divide it. Two buttons below
  means a hero two buttons wide; three means three.

So: buttons within a row are symmetric; the hero is asymmetric to them
by an exact multiple. Both are legible as intent.

## Sizes

| Token | Value | Notes |
|---|---|---|
| `ACTION_BUTTON_MAX` | 240px | one action's natural width |
| `ACTION_GAP` | 10px | between buttons, and between the two rows |
| hero height | 44px | the only height that differs, and only for the one main action |
| tile width (store rails) | 195px | 5 tiles + the View-all card fit 1280 without scrolling |

Rails are sized so their **exit is visible**: a "View all" card the user
has to scroll to find may as well not exist. If a rail's length changes,
re-check that `n × TILE_WIDTH + gaps` still fits the screen.

## Labels

Buttons never wrap. A two-line label makes its button taller than its
neighbours and breaks the row's rhythm, which is worse than the words
being shorter. `ACTION_BUTTON` carries `nowrap` + ellipsis as a
backstop, but the real fix is the label: prefer `+ optional (12)` over
`Install All (inc 12 optional)`.

## Page furniture

Shared in `chrome.tsx` so the three full-screen pages read as one app:

- **`PageBackdrop`** — the page's own artwork, faded and (for arbitrary
  mod art) blurred, behind the header. It scrolls away with the content
  so it never competes once the user is reading.
- **`SectionHeading`** — the brand-orange accent bar plus a title. This
  is the 10-foot-UI signpost; use it for every section, not `<h3>`.
- **`StatChip`** — header facts (endorsements, downloads, version, mod
  count, size) as pills rather than a dot-separated sentence, so they
  can be scanned rather than read.
- **`StackedThumb`** — a collection thumbnail with card layers fanned
  behind it. A collection is a *deck of mods*, and it should look like
  one at a glance. Use `fit="contain"` wherever the artwork is meant to
  be looked at (page headers); `cover` is only acceptable on small tiles
  where cropping is expected.

## Colour

Brand orange `#da8e35` marks the primary action, section accents, and
progress. It is not decoration - if something is orange, it is either
the main action or it is happening right now.

Progress is shown by **filling the control itself** left-to-right
(collection rows, the install hero, the QAM prefix-tool button) rather
than by a separate bar. One language for "this is working" everywhere.

## Gamepad first

- Anything interactive is a `Focusable` or `DialogButton`; focus order
  follows visual order.
- Focus lands on the page's main action on mount, not on the first
  focusable in the DOM.
- Never move focus while the on-screen keyboard is up - it dismisses it.
