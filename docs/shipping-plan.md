# Shipping & Update Distribution Plan

*(drafted 2026-08-04 — answering "how do users get updates without
GitHub?")*

## SUPERSEDED (2026-08-14): custom store first, official store later

Michael, 14 August 2026: ship 1.0 through a **custom store URL**, marked
**"beta"**, released **silently** at first, with UAT on a second Steam Deck
already prepped with Decky. The official store comes later, once real users
have given feedback.

This inverts the 2026-08-04 plan below, which called the official store "the
answer" and the URL route "fine for the beta circle, not for end users".
Recording the reversal rather than editing it away, because the reasoning
underneath is still sound and is what we come back to.

Why this is the better first move: the official-store PR has an unresolved
blocker (the AI-authorship declaration), a third-party tester requirement, a
SteamOS Preview-channel testing burden from the me3 binary, and a two-week
momentum rule that starts a clock we cannot control. None of that should sit
between a finished 1.0 and its first real user.

### The two custom routes are not the same thing

Verified against Decky Loader's docs, not tested by us:

| route | what the user does | update badges? |
|---|---|---|
| **Custom store channel** | Settings → General → Store Channel → Custom, paste our URL | **Yes** — appears in Browse with per-plugin update badges |
| **Install from URL** | paste a link to a release .zip | No — sideload, updated by hand |

**The catch on the store channel: it replaces the default store, it does not
add to it.** Decky supports one store at a time (multiple simultaneous
stores is an open request, issue #746). A tester pointed at ours loses the
normal Decky catalogue until they switch back.

For a prepped UAT device that is a non-issue. For real users it is a real
onboarding cost, and it is the strongest argument for the official store
eventually — so "custom store first" is a staging decision, not a
destination.

### What "beta" has to mean in the product

If 1.0 ships labelled beta, the label should appear where a user forms
expectations, not only in release notes — and the ten games' testing states
are already recorded per-game in `games-1.0-roadmap.md`, which is the honest
basis for saying what is proven and what is not.

## (2026-08-04, still the eventual destination) The official Decky plugin store

Decky Loader has a built-in plugin store (the "Browse plugins" tab every
Decky user already has). Plugins distributed through it get:

- **One-click installs** from inside Gaming Mode — no GitHub, no
  sideloading.
- **Automatic update surfacing**: Decky shows an "update available"
  badge per plugin and updates in one click. This is the answer to
  frequent releases as we add games — we publish, users see the badge.
- A **testing channel** for pre-release builds (store supports
  per-plugin testing versions), useful for staged rollouts of new game
  support.

## How we get in

1. Submission = PR to `SteamDeckHomebrew/decky-plugin-database` pointing
   at our repo + release. Their review covers: open-source license
   (BSD-3 ✓ already), no obfuscated code, backend code review (our
   Python is plain and readable ✓), `plugin.json` metadata + store
   assets (name, description, screenshots).
2. Releases must be reproducible from the repo — our `pnpm build` +
   plugin packaging already is; we'll add a GitHub Actions workflow that
   builds the store zip on tag push so every release is one `git tag`.
3. After first acceptance, updates are just new releases picked up by
   the store pipeline — review is lighter than initial submission.

## Interim (QA/beta before store acceptance)

GitHub Releases zips + Decky developer mode ("Install plugin from URL")
for testers. Fine for the beta circle, not for end users.

## Pre-1.0 checklist this implies

- [ ] GitHub Actions release workflow (build + zip on tag)
- [ ] Store assets: icon, screenshots (Steam Link captures work),
      store description ("Nexus Mods" naming rules apply)
- [ ] plugin.json review (name, author, flags)
- [ ] Decky store submission PR
- [ ] Internal: Nexus Mods API additions PR (adult-content preference
      exposure, 64-bit/populated file sizes) — boss-approved workflow:
      we create the PR, their team reviews. Needs repo access for the
      agent tooling.
