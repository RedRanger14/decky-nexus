# Shipping & Update Distribution Plan

*(drafted 2026-08-04 — answering "how do users get updates without
GitHub?")*

## The answer: the official Decky plugin store

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
