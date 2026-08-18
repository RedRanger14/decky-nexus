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

# Optional: your Nexus Mods API key, saved for you so it never has to be
# typed on a controller. The clipboard does not survive the switch from
# Desktop Mode to Gaming Mode, so a key copied from the website is gone by
# the time the plugin asks for it.
#
#   curl -L .../install.sh | sh -s -- YOUR_API_KEY
API_KEY="${1:-}"

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
# Copy in beside the old version, then swap. The previous version of this
# did `rm -rf` and then `cp`, which means a copy that fails for any reason
# (full disk, permissions, a cancelled sudo) leaves the user with NO plugin
# at all rather than the one they had. The swap below is the last thing to
# happen and the only destructive step.
sudo sh -c "
    mkdir -p '$PLUGIN_DIR' &&
    rm -rf '$PLUGIN_DIR/.$PLUGIN.new' &&
    cp -r '$TMP/stage/$PLUGIN' '$PLUGIN_DIR/.$PLUGIN.new' &&
    chown -R $(id -u):$(id -g) '$PLUGIN_DIR/.$PLUGIN.new' &&
    rm -rf '$PLUGIN_DIR/$PLUGIN' &&
    mv '$PLUGIN_DIR/.$PLUGIN.new' '$PLUGIN_DIR/$PLUGIN'
" || die "install failed - your previous version, if any, is untouched"

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
# Ask for the key rather than making it an argument. Passing it on the end of
# the command means the user has to land a paste after a space, and on a
# handheld they do not: Michael's paste produced `sh -s --YlpTRX...`, which
# sh read as one invalid option. A prompt has no such edge.
#
# Read from /dev/tty, not stdin: this whole script arrives through a pipe
# from curl, so stdin is the script itself and `read` would consume it.
if [ -z "$API_KEY" ] && [ -r /dev/tty ]; then
    say ""
    say "Paste your Nexus Mods API key now, or press Enter to skip."
    say "  (Nexus Mods -> your profile -> Site preferences -> API Keys ->"
    say "   scroll to the bottom. Right-click to paste: left trigger is L2.)"
    printf 'API key: '
    read -r API_KEY < /dev/tty || API_KEY=""
fi

if [ -n "$API_KEY" ]; then
    # Written to the SETTINGS directory, not the plugin directory: settings
    # are owned by the user, survive updates, and an update never touches
    # them. Also means this works before the plugin has ever been opened.
    SET_DIR="$HOME/homebrew/settings/Nexus-Mods"
    mkdir -p "$SET_DIR"
    python3 -c 'import json, os, sys
path, key = sys.argv[1], sys.argv[2].strip()
data = {}
if os.path.isfile(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        data = {}
data["api_key"] = key
with open(path, "w") as f:
    json.dump(data, f, indent=2)
' "$SET_DIR/settings.json" "$API_KEY" || die "could not save the API key"
    say ""
    say "API key saved. The plugin will use it straight away, and an update"
    say "will not overwrite it."
else
    say ""
    say "No API key saved. Add one on the plugin's Settings page, or run this"
    say "script again and paste the key when it asks."
fi
