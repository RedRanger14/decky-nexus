# How installing works

> **WIP.** Written 18 August 2026, after the install route was rebuilt. The
> script has been dry-run on hardware but the two `sudo` steps at the end
> have NOT been executed on a real device yet. Treat the last two steps as
> designed rather than proven until someone has run it start to finish.

## The one line

```sh
curl -L https://raw.githubusercontent.com/RedRanger14/decky-nexus/main/install.sh | sh
```

Pasted into Konsole in Desktop Mode. Same shape as Decky's own installer,
deliberately: it is the only thing a user has to type, and it is the same
line for a first install and for every update afterwards.

## What it does, in order

1. **Checks Decky is installed.** No `~/homebrew` means Decky is not there,
   and the script stops with a link rather than creating a folder Decky will
   never read.
2. **Checks `curl` and `python3` exist.** Both ship with SteamOS. `unzip`
   deliberately is not used, because it is not guaranteed to be present;
   extraction goes through `python3 -m zipfile`.
3. **Finds the newest release.** It reads
   `api.github.com/repos/RedRanger14/decky-nexus/releases` and takes the
   first `.zip` asset from the first non-draft release.

   Not `/releases/latest`: that endpoint ignores pre-releases, and the beta
   is published as a pre-release. Using it would have found nothing on the
   very first real user.
4. **Downloads the zip** to a temporary directory that is removed on exit,
   success or failure.
5. **Verifies the archive before touching anything.** It must open as a zip
   and must contain `Nexus Mods/plugin.json` exactly. This is the check that
   would have caught the two bugs found on 17 August: a zip built with
   Windows path separators, and a top-level folder named `Nexus-Mods` when
   `plugin.json` says `Nexus Mods`.
6. **Extracts to a staging directory** and confirms `plugin.json`, `main.py`
   and `dist/index.js` all arrived.
7. **Only now removes the installed version** and copies the new one into
   `~/homebrew/plugins/Nexus Mods`, then fixes ownership. Needs `sudo`,
   because Decky owns that folder as root.
8. **Restarts Decky** with `systemctl restart plugin_loader`.
9. **Prints what to do next**: Quick Access Menu, plug icon, and a reminder
   that a Nexus Mods Premium account is required.

## The rule the ordering encodes

Nothing existing is destroyed until the replacement is downloaded, verified
and extracted. If the network drops, the release is malformed, or the zip is
half a file, the user still has the version they had this morning. The one
outcome worth engineering against is a plugin folder containing neither a
working old version nor a working new one.

## Updating

The same line. It fetches whatever the newest release is and replaces the
plugin folder. Settings, the API key, install records, mod verdicts and
downloaded archives all live outside the plugin folder, in
`~/homebrew/settings/Nexus-Mods` and `~/homebrew/data/Nexus-Mods`, and are
not touched.

## What is verified, and what is not

Verified on the test Deck (192.168.50.202) on 18 August:

- The raw URL serves the script.
- The script parses, finds v0.259.0, downloads it, verifies the archive,
  extracts it, and passes every file check.
- The zip itself installs correctly through Decky's own "Install from zip",
  producing `~/homebrew/plugins/Nexus Mods` with the right version.

NOT yet verified:

- The `sudo` copy and the Decky restart, which were stubbed out in the dry
  run. This is the part to test in the morning.
- Behaviour when a previous version is already installed. It should replace
  it in place; that path has not been run.
- Behaviour with no network, or a partial download. The checks are written
  for it, but written is not tested.

## Why not Decky's "Install from URL"

It does not work with this plugin, and the failure is silent.

Decky takes the plugin name from the URL rather than from the zip (the log
shows `Installing v0.259.0 from URL`, having taken the release tag as the
name), then stops during install with nothing written to its journal. The UI
sits on "PARSING ZIP FILE" indefinitely, because every failure path in
Decky's installer returns without emitting a completion event.

The same zip installs correctly through "Install from zip", and the download
was confirmed on the device at HTTP 200 and 335,942 bytes. So it is neither
the archive nor the network.

Worth reporting upstream: an installer that can hang forever with nothing in
its log is a bug in its own right, separately from the naming.

## Open questions

- Should the script offer to install Decky if it is missing, rather than
  linking to it? Leaning no: piping one installer into another is a lot of
  trust to ask, and Decky's own instructions are better than ours.
- Should it verify a checksum? There is nothing to check against yet. If
  releases ever carry one, this is where it goes, next to the zip
  verification in step 5.
- Should it pin a version? Right now it always takes the newest. A
  `--version` flag would help someone rolling back after a bad release, and
  there is no way to do that today except downloading the zip by hand.
