#!/bin/sh
# Install the Nexus Mods plugin for Decky Loader.
#
#   curl -L https://raw.githubusercontent.com/RedRanger14/decky-nexus/main/install.sh | sh
#
# Run it again any time to update. Your installed mods, API key and settings
# live outside the plugin folder and are not touched.
#
# Why this exists: Decky's own "Install from URL" takes the plugin name from
# the URL rather than from the zip, then stops during install with nothing
# written to its log, leaving the screen on "PARSING ZIP FILE" forever. The
# same zip installs correctly by hand, so this does by script exactly what a
# person would do by hand, and says what it is doing at each step.
#
# Everything here is deliberate about one thing: never leave a half-installed
# plugin behind. The new version is staged and verified complete BEFORE the
# old one is removed.

set -eu

REPO="RedRanger14/decky-nexus"
PLUGIN="Nexus Mods"
PLUGIN_DIR="$HOME/homebrew/plugins"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

say "Nexus Mods plugin installer"
say ""

# ---- checks before anything is downloaded ---------------------------------

[ -d "$HOME/homebrew" ] || die "Decky Loader is not installed (no ~/homebrew).
Install Decky first: https://decky.xyz"

command -v curl >/dev/null 2>&1 || die "curl is required and is not installed"
command -v python3 >/dev/null 2>&1 || die "python3 is required and is not installed"

# ---- find the newest release ----------------------------------------------
# /releases rather than /releases/latest, because this is published as a
# pre-release while it is in beta and "latest" ignores those entirely.

say "Looking up the newest release..."
API="https://api.github.com/repos/$REPO/releases"
URL="$(curl -fsSL "$API" \
    | python3 -c '
import json, sys
releases = json.load(sys.stdin)
for release in releases:
    if release.get("draft"):
        continue
    for asset in release.get("assets") or []:
        if asset["name"].endswith(".zip"):
            print(asset["browser_download_url"])
            sys.exit(0)
sys.exit(1)
')" || die "no release with a .zip asset found at github.com/$REPO/releases"

VERSION="$(basename "$(dirname "$URL")")"
say "Found $VERSION"

# ---- download and verify ---------------------------------------------------

say "Downloading..."
curl -fsSL "$URL" -o "$TMP/plugin.zip" || die "download failed: $URL"

python3 - "$TMP/plugin.zip" "$PLUGIN" <<'PY' || die "the downloaded file is not a usable plugin zip"
import sys, zipfile
path, plugin = sys.argv[1], sys.argv[2]
try:
    names = zipfile.ZipFile(path).namelist()
except Exception as e:
    print("not a zip:", e)
    sys.exit(1)
need = f"{plugin}/plugin.json"
if need not in names:
    print("expected", need, "- got", names[:4])
    sys.exit(1)
PY

say "Extracting..."
python3 -m zipfile -e "$TMP/plugin.zip" "$TMP/stage"
[ -f "$TMP/stage/$PLUGIN/plugin.json" ] || die "extraction did not produce $PLUGIN/plugin.json"
[ -f "$TMP/stage/$PLUGIN/main.py" ] || die "extraction is missing main.py"
[ -f "$TMP/stage/$PLUGIN/dist/index.js" ] || die "extraction is missing dist/index.js"

# ---- install ---------------------------------------------------------------
# Decky owns its plugin folder as root, so this needs sudo. It will ask for
# your password, the same as installing Decky itself did. Nothing is sent
# anywhere and the password is not stored.

say ""
say "Installing to $PLUGIN_DIR/$PLUGIN (this needs your password)..."
sudo sh -c "
    mkdir -p '$PLUGIN_DIR' &&
    rm -rf '$PLUGIN_DIR/$PLUGIN' &&
    cp -r '$TMP/stage/$PLUGIN' '$PLUGIN_DIR/' &&
    chown -R $(id -u):$(id -g) '$PLUGIN_DIR/$PLUGIN'
" || die "install failed"

# Restarting Decky restarts part of Steam's UI with it, so Decky loses its
# own frontend connection and shows a toast saying something failed. The
# install has already finished by then. Said out loud here because a user
# who reads "failed" after "Done" will believe the toast over the terminal.
say "Restarting Decky (Steam's interface will flicker)..."
sudo systemctl restart plugin_loader || die "could not restart Decky"

# Evidence, not optimism: read the version back off disk before claiming
# success.
INSTALLED="$(python3 -c '
import json, sys
try:
    print(json.load(open(sys.argv[1]))["version"])
except Exception:
    sys.exit(1)
' "$PLUGIN_DIR/$PLUGIN/package.json" 2>/dev/null)"     || die "installed, but could not read the version back - check Decky's plugin list"

say ""
say "Done. Version $INSTALLED is installed."
say ""
say "If Decky showed a toast saying something failed, ignore it: restarting"
say "Decky disconnects its own interface for a moment and it reports that as"
say "a failure. The plugin is installed - this script just read it back."
say "Open the Quick Access Menu, press the plug icon, and Nexus Mods is there."
say ""
say "You need a Nexus Mods Premium account to download mods. Add your API key"
say "in the plugin's Settings page the first time you open it."
