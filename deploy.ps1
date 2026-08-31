# Deploys the plugin to a Steam Deck (or Bazzite box) over SSH, from Windows.
# Mirrors what the template's Linux VS Code tasks do: copy runtime files to
# ~/homebrew/plugins/<name>/, fix ownership, restart plugin_loader.
#
# One-time device setup and usage: see README.md "Deploying to hardware".
# Connection settings live in .vscode/settings.json (gitignored).
#
#   .\deploy.ps1              build + deploy + restart Decky
#   .\deploy.ps1 -SkipBuild   deploy whatever is already in dist/
#   .\deploy.ps1 -PackOnly    build the tarball locally, don't touch the device

param(
    [switch]$SkipBuild,
    [switch]$PackOnly,
    # Deploy even if the device is mid-download or mid-patch. Restarting
    # Decky kills that work, so this is off by default.
    [switch]$Force,
    # Deploy to this host/IP instead of the configured ones. Needed whenever
    # BOTH handhelds are awake: they each answer to steamdeck.local, and
    # mDNS hands the name to whichever claimed it first - on 2026-08-31 that
    # was the Deck while the target was the Legion at its raw IP.
    [string]$TargetHost = ""
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# ---- config ----------------------------------------------------------------
$settingsPath = Join-Path $root ".vscode\settings.json"
if (-not (Test-Path $settingsPath)) {
    Copy-Item (Join-Path $root ".vscode\defsettings.json") $settingsPath
    Write-Host "Created .vscode/settings.json - edit deckip/deckuser/deckpass for your device, then re-run." -ForegroundColor Yellow
    exit 1
}
$cfg = Get-Content $settingsPath -Raw | ConvertFrom-Json
# The folder is plugin.json's name VERBATIM, spaces and all - the same
# source release.ps1 and install.sh use.
#
# This used to strip spaces on a "template convention: no spaces in folder
# names" assumption, which is simply wrong for Decky: it installs into the
# plugin.json name as written, so a real user's Deck has a folder called
# "Nexus Mods" while every dev deploy went to "Nexus-Mods". Two consequences,
# both real: hardware testing was never testing the layout users get
# (including which settings directory Decky reads), and deploying onto a
# device that already had a released build left TWO plugin folders, so Decky
# loaded the plugin twice.
$pluginJson = Get-Content (Join-Path $root "plugin.json") -Raw | ConvertFrom-Json
$folder = $pluginJson.name
$pluginDir = "$($cfg.deckdir)/homebrew/plugins/$folder"
# The folder this used to deploy to. Removed on the device if it is still
# there, because leaving it means two copies of the plugin in the QAM.
$staleDir = "$($cfg.deckdir)/homebrew/plugins/$($folder -replace ' ', '-')"
# deckip is usually an mDNS name (steamdeck.local); Windows mDNS resolution is
# flaky, so deckipfallback (a raw LAN IP) is tried when the name doesn't answer.
$hosts = @($cfg.deckip)
if ($cfg.deckipfallback) { $hosts += $cfg.deckipfallback }
if ($TargetHost) { $hosts = @($TargetHost) }

# ---- build -----------------------------------------------------------------
if (-not $SkipBuild) {
    # pnpm writes progress to stderr even on success, and under
    # $ErrorActionPreference = "Stop" that becomes a terminating error on a
    # build that worked (only when this script is invoked directly - the
    # pnpm-run wrapper happened to mask it). The exit code is the verdict.
    $ErrorActionPreference = "Continue"
    pnpm run build 2>&1 | Write-Host
    $buildExit = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($buildExit -ne 0) { throw "frontend build failed" }
}
if (-not (Test-Path (Join-Path $root "dist\index.js"))) { throw "dist/index.js missing - run pnpm run build" }

# ---- stage the runtime files and pack them ---------------------------------
$stage = Join-Path $env:TEMP "decky-nexus-stage"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force (Join-Path $stage "dist") | Out-Null
Copy-Item (Join-Path $root "plugin.json"), (Join-Path $root "package.json"), (Join-Path $root "main.py"), (Join-Path $root "LICENSE"), (Join-Path $root "README.md") $stage
Copy-Item (Join-Path $root "dist\index.js") (Join-Path $stage "dist")

$tarball = Join-Path $env:TEMP "decky-nexus-deploy.tar.gz"
tar -czf $tarball -C $stage .
if ($LASTEXITCODE -ne 0) { throw "tar failed" }
Write-Host "Packed $tarball" -ForegroundColor Green
if ($PackOnly) { exit 0 }

# ---- remote install script (LF line endings - it runs under sh on the deck) -
$remoteScript = (
    'set -e',
    "PLUGIN_DIR=`"$pluginDir`"",
    "STALE_DIR=`"$staleDir`"",
    "SET_DIR=`"$($cfg.deckdir)/homebrew/settings`"",
    'mkdir -p "$PLUGIN_DIR"',
    'rm -rf "$PLUGIN_DIR/dist"',
    'tar -xzf /tmp/decky-nexus-deploy.tar.gz -C "$PLUGIN_DIR"',
    "chown -R $($cfg.deckuser):$($cfg.deckuser) `"`$PLUGIN_DIR`"",
    # Carry the old dev deployment's settings across BEFORE removing it, or
    # switching to the correct folder would silently cost this device its
    # API key and its record of what is installed. Only when the correct
    # folder has none of its own: never overwrite real settings.
    'if [ "$STALE_DIR" != "$PLUGIN_DIR" ] && [ -d "$STALE_DIR" ]; then',
    '  STALE_SET="$SET_DIR/$(basename "$STALE_DIR")"',
    '  GOOD_SET="$SET_DIR/$(basename "$PLUGIN_DIR")"',
    '  if [ -d "$STALE_SET" ] && [ ! -d "$GOOD_SET" ]; then',
    '    cp -r "$STALE_SET" "$GOOD_SET" && echo ">>> carried settings over from $STALE_SET"',
    "    chown -R $($cfg.deckuser):$($cfg.deckuser) `"`$GOOD_SET`"",
    '  fi',
    '  rm -rf "$STALE_DIR"',
    '  echo ">>> removed the old dev plugin folder $STALE_DIR (it would load as a second copy)"',
    'fi',
    'rm -f /tmp/decky-nexus-deploy.tar.gz /tmp/decky-nexus-remote.sh',
    'systemctl restart plugin_loader',
    'echo ">>> deployed to $PLUGIN_DIR, Decky restarted"'
) -join "`n"
$remoteScriptPath = Join-Path $env:TEMP "decky-nexus-remote.sh"
[IO.File]::WriteAllText($remoteScriptPath, $remoteScript + "`n")

# ---- ship + install --------------------------------------------------------
# ServerAlive*: if the device suspends mid-connection the session dies in ~20s
# instead of hanging forever.
$keepAlive = @("-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=4")

# Handhelds drop Wi-Fi aggressively in power save / suspend: probe first, retry a few times.
# ErrorActionPreference is relaxed for the probe: a failed hostname makes ssh write to
# stderr, and under "Stop" PowerShell turns that into a terminating error BEFORE the loop
# can try deckipfallback - so the IP fallback never ran on the one day mDNS was down.
$target = $null
foreach ($i in 1..3) {
    foreach ($h in $hosts) {
        if (-not $h) { continue }
        $candidate = "$($cfg.deckuser)@$h"
        $probe = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            ssh -p $cfg.deckport -o ConnectTimeout=8 -o BatchMode=yes -o StrictHostKeyChecking=accept-new @keepAlive $candidate "true"
        } catch {
            # unreachable host: fall through to the next candidate
        } finally {
            $ErrorActionPreference = $probe
        }
        if ($LASTEXITCODE -eq 0) {
            $target = $candidate
            if ($h -ne $cfg.deckip) { Write-Host "Reached the device at $h (mDNS name did not resolve)" -ForegroundColor Yellow }
            break
        }
    }
    if ($target) { break }
    Write-Host "Device not answering (attempt $i/3) - make sure it's awake..." -ForegroundColor Yellow
    Start-Sleep -Seconds 4
}
if (-not $target) { throw "cannot reach $($hosts -join ' / ') - wake the device and re-run" }

# Refuse to deploy over work in progress.
#
# Deploying restarts Decky, which kills whatever the plugin was doing. On
# 2026-08-14 that happened mid-run of the Fallout 3 Anniversary Patcher: the
# task died before recording its result, the patcher was orphaned, and the
# half-written Fallout3.exe left the game hanging on the Steam spinner. It
# cost a morning to find, and the deploy could simply have said no.
#
# -Force overrides, because sometimes killing a stuck run is the point.
if (-not $Force) {
    # One line, no quotes the remote shell can trip over: a here-string
    # version of this failed to parse and silently checked nothing.
    # Three traps found writing this probe, so it is base64'd rather than
    # quoted: PowerShell strips the quotes before ssh sees them, pgrep -f
    # matches the probe's OWN command line, and an abandoned .part file
    # lingers for ever. So: match on process NAME, skip the wine services
    # that are always up, and only count a download whose file was written
    # in the last minute.
    $script = @(
        'b=',
        'ps -eo comm | grep -iE "[.]exe$" | grep -viE "^(services|winedevice|plugplay|explorer|rpcss|svchost|conhost|tabtip|start|wineboot)[.]exe$" | head -1 | grep -q . && b=tool',
        '[ -n "$(find /home/deck/homebrew/data/Nexus-Mods/downloads -name "*.part" -mmin -1 2>/dev/null)" ] && b=download',
        'echo $b'
    ) -join "`n"
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script))
    $probe = "echo $b64 | base64 -d | sh"
    $busy = (ssh -p $cfg.deckport -o ConnectTimeout=8 @keepAlive $target $probe) -join ''
    if ($busy.Trim() -eq 'tool') {
        Write-Host ""
        Write-Host "REFUSING TO DEPLOY - a modding tool is running on the device." -ForegroundColor Red
        Write-Host "Restarting Decky now kills it mid-write. That is exactly how" -ForegroundColor Yellow
        Write-Host "Fallout3.exe ended up half-patched on 2026-08-14." -ForegroundColor Yellow
        Write-Host "Wait for it to finish, or re-run with -Force." -ForegroundColor Yellow
        exit 1
    }
    if ($busy.Trim() -eq 'download') {
        Write-Host ""
        Write-Host "REFUSING TO DEPLOY - a download is in progress." -ForegroundColor Red
        Write-Host "Restarting Decky abandons it. Re-run with -Force to override." -ForegroundColor Yellow
        exit 1
    }
}

Write-Host "Copying to $target ..." -ForegroundColor Cyan
scp -P $cfg.deckport -o ConnectTimeout=10 @keepAlive $tarball $remoteScriptPath "${target}:/tmp/"
if ($LASTEXITCODE -ne 0) { throw "scp failed - is the device awake and sshd enabled?" }

$sudoPass = $cfg.deckpass
if ((-not $sudoPass) -or ($sudoPass -eq "ssap")) {
    $sec = Read-Host "sudo password for $($cfg.deckuser) on the device" -AsSecureString
    $sudoPass = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}

# password is piped to sudo -S over ssh stdin, so it never appears in a command line.
# tr scrubs the CR and BOM bytes that Windows PowerShell pipes prepend/append to
# native program stdin - only printable chars + the newline survive. This means
# the device password must be printable ASCII.
$sudoPass | ssh -p $cfg.deckport -o ConnectTimeout=10 @keepAlive $target "tr -dc '[:print:]\n' | sudo -S -p '' sh /tmp/decky-nexus-remote.sh"
if ($LASTEXITCODE -ne 0) { throw "remote install failed" }

Write-Host "Done. Open the Quick Access Menu (...) -> plug icon -> Nexus Mods" -ForegroundColor Green
