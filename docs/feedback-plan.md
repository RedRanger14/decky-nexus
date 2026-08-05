# User Feedback & Bug Reporting Plan

Requested 2026-08-05. Goal: structured, diagnosable reports for bugs,
feature requests, and game-support requests - never "it doesn't work"
with zero context.

## Recommendation: three layers, one system of record

### 1. GitHub Issue Forms = the system of record

GitHub's **issue forms** (`.github/ISSUE_TEMPLATE/*.yml`) render as real
web forms with required dropdowns and text fields - users literally
cannot submit without the structure. Free, searchable, dedupe-friendly,
and it lives where the code ships (the Decky store links to the repo
anyway). Three forms:

- **bug_report.yml** - required fields:
  - Plugin version (the QAM badge)
  - Device (dropdown: Steam Deck LCD / Deck OLED / Legion Go / ROG Ally /
    other handheld / desktop)
  - SteamOS or distro version
  - Game (dropdown of supported games + "other")
  - What were you doing (dropdown: browsing / installing single mod /
    installing collection / Finish setup / launching game / other)
  - Mod or collection link (nexusmods.com URL)
  - What happened vs what you expected
  - Diagnostic bundle (paste box - see layer 2)
- **feature_request.yml** - what/why/how-often; device+version optional.
- **game_support.yml** - game name, Nexus domain URL, store link,
  does-it-run-on-Deck, framework the community uses (free text).

Labels auto-applied per form (`bug`, `enhancement`, `game-request`), so
triage starts sorted. `config.yml` disables blank issues.

### 2. In-plugin "Report a problem" = the diagnostic heavy lifting

A button on the Settings page (and maybe on error toasts later) that
generates a **diagnostic bundle** the backend already knows how to
collect:

```
plugin: v0.42.0 · decky 3.2.1 · SteamOS 3.7.4 (holo)
device: Legion Go 2 · free disk 812 GB
game: Fallout New Vegas (22380) · proton: default
prefs: parallel=4 window=8 cap=0 minfree=5
last 40 log lines: ...
installed for game: 799 records (3 collections)
attention queue: 18 items (tool x18)
```

Two delivery options, both controller-friendly:
- **QR code** rendered on-screen encoding a prefilled GitHub issue URL
  (`/issues/new?template=bug_report.yml&version=...&device=...`) - the
  user scans with their phone, the form arrives pre-filled with
  everything but their description. No typing on-device, report happens
  on a real keyboard. (URL length limits mean the log tail may need to
  be pasted by hand from layer-3 upload or trimmed.)
- **Copy to clipboard** for users browsing GitHub on-device.

The bundle deliberately excludes the API key and any account data.

### 3. Discord = community, not tracker

A Discord server is great for "is anyone else seeing this?", quick
triage, and hype - and terrible as a system of record (no search from
outside, reports scroll away, no state). If we run one:
- **#support forum channel** with a required post template (Discord
  forum tags: device, game) - and pinned guidance that real bugs get
  promoted to GitHub issues (by us, with a link back).
- Feature requests channel where upvotes = reactions; monthly sweep
  promotes winners to GitHub.
- GitHub webhook posting new releases + closed issues into #changelog -
  closes the loop, users see their reports fixed.

### Why not Discord-only / form service / email
- Discord-only: reports evaporate, no dedupe, no status tracking.
- Google Forms / Typeform: another silo, no linkage to code or releases.
- Email: unstructured by definition - the exact failure mode to avoid.

## Build order (when we pick this up)

1. `.github/ISSUE_TEMPLATE/` forms - an hour, ships value immediately;
   the repo may need to go public first (currently personal/private -
   decide the org/home for 1.0 alongside the Decky store submission).
2. Backend `get_diagnostics` callable + Settings-page "Report a problem"
   section with copy + QR (a QR is just an SVG - no dependency needed,
   or a tiny inlined generator).
3. Discord server once there are actual users (store launch), with the
   webhook + forum template.

## Decisions (Michael, 2026-08-05)
- **Repo home: personal (RedRanger14)** - it's linked to the work org,
  which is fine; issue-form URLs and store metadata point there.
- **"Report a problem" appears on error surfaces too**, not just
  Settings: failed collection rows and error toasts get a path into the
  same prefilled-diagnostics flow.
