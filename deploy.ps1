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
    [switch]$PackOnly
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
$folder = $cfg.pluginname -replace ' ', '-'   # template convention: no spaces in the folder name
$pluginDir = "$($cfg.deckdir)/homebrew/plugins/$folder"
$target = "$($cfg.deckuser)@$($cfg.deckip)"

# ---- build -----------------------------------------------------------------
if (-not $SkipBuild) {
    pnpm run build
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
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
    'mkdir -p "$PLUGIN_DIR"',
    'rm -rf "$PLUGIN_DIR/dist"',
    'tar -xzf /tmp/decky-nexus-deploy.tar.gz -C "$PLUGIN_DIR"',
    "chown -R $($cfg.deckuser):$($cfg.deckuser) `"`$PLUGIN_DIR`"",
    'rm -f /tmp/decky-nexus-deploy.tar.gz /tmp/decky-nexus-remote.sh',
    'systemctl restart plugin_loader',
    'echo ">>> deployed to $PLUGIN_DIR, Decky restarted"'
) -join "`n"
$remoteScriptPath = Join-Path $env:TEMP "decky-nexus-remote.sh"
[IO.File]::WriteAllText($remoteScriptPath, $remoteScript + "`n")

# ---- ship + install --------------------------------------------------------
# Handhelds drop Wi-Fi aggressively in power save / suspend: probe first, retry a few times
$probeOk = $false
foreach ($i in 1..3) {
    ssh -p $cfg.deckport -o ConnectTimeout=8 -o BatchMode=yes $target "true"
    if ($LASTEXITCODE -eq 0) { $probeOk = $true; break }
    Write-Host "Device not answering (attempt $i/3) - make sure it's awake..." -ForegroundColor Yellow
    Start-Sleep -Seconds 4
}
if (-not $probeOk) { throw "cannot reach $target - wake the device and re-run" }

# ServerAlive*: if the device suspends mid-transfer the session dies in ~20s
# instead of hanging forever.
$keepAlive = @("-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=4")

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
