# Build the release zip that users install.
#
# deploy.ps1 makes a tar.gz for the device; Decky's store and its
# "install from URL" both want a .zip whose SINGLE top-level folder is the
# plugin directory name. Same staged files, different wrapper, so the thing
# a stranger downloads is built by the same step that has been deploying to
# hardware all along rather than assembled by hand at release time.
#
#   .\release.ps1            build dist\Nexus-Mods-<version>.zip
#
# Then: GitHub > Releases > Draft a new release > attach the zip.
# The download URL it gives you is what goes in the README and the store.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

$pkg = Get-Content (Join-Path $root "package.json") -Raw | ConvertFrom-Json
$version = $pkg.version
# The zip's top folder must be plugin.json's "name", not a hyphenated
# variant. Decky reads the folder name out of the zip AND the name out of
# plugin.json, and ours disagreed: folder "Nexus-Mods" against name "Nexus
# Mods". Every failure inside Decky's installer returns without telling the
# UI, so the disagreement presented as "PARSING ZIP FILE" forever rather
# than an error. Taken from plugin.json so the two cannot drift again.
$pluginJson = Get-Content (Join-Path $root "plugin.json") -Raw | ConvertFrom-Json
$pluginDir = $pluginJson.name
Write-Host "Plugin folder in zip: '$pluginDir' (from plugin.json)" -ForegroundColor Cyan

Write-Host "Building v$version ..." -ForegroundColor Cyan
# pnpm writes progress to stderr even on success, and with
# $ErrorActionPreference = "Stop" PowerShell turns that into a terminating
# NativeCommandError - the script died on a build that had worked. Only the
# exit code says whether it built, so judge it by that alone.
$ErrorActionPreference = "Continue"
pnpm run build 2>&1 | Write-Host
$buildExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($buildExit -ne 0) { throw "frontend build failed" }
if (-not (Test-Path (Join-Path $root "dist\index.js"))) {
    throw "dist/index.js missing"
}

$stage = Join-Path $env:TEMP "decky-nexus-release"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
$plugin = Join-Path $stage $pluginDir
New-Item -ItemType Directory -Force (Join-Path $plugin "dist") | Out-Null

# The same five files deploy.ps1 stages, plus the built frontend. If that
# list ever changes, change it in both places.
Copy-Item `
    (Join-Path $root "plugin.json"), `
    (Join-Path $root "package.json"), `
    (Join-Path $root "main.py"), `
    (Join-Path $root "LICENSE"), `
    (Join-Path $root "README.md") `
    $plugin
Copy-Item (Join-Path $root "dist\index.js") (Join-Path $plugin "dist")

$out = Join-Path $root "dist\Nexus-Mods-$version.zip"
if (Test-Path $out) { Remove-Item -Force $out }
# NOT Compress-Archive. PowerShell writes Windows path separators into the
# zip entries, so a Linux tool sees one file literally named
# "Nexus-Mods\LICENSE" instead of a folder, and Decky sat on "PARSING ZIP
# FILE" forever. Python's zipfile writes forward slashes, which is what the
# format specifies. The bug is invisible on Windows, where the separators
# are normalised away on read: it took listing the entries on the device.
$pyFile = Join-Path $env:TEMP "decky-nexus-zip.py"
Copy-Item (Join-Path $root "tools\makezip.py") $pyFile
# Same reason as the build above: anything python writes to stderr, warnings
# included, would otherwise be fatal. The exit code is the verdict.
$ErrorActionPreference = "Continue"
python $pyFile $stage $out 2>&1 | Write-Host
$zipExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($zipExit -ne 0) { throw "zip build failed" }

# The gate that makes this script the only sane way to build a release: the
# same checks makestore.py runs before it will index an artifact. v1.4.0 was
# published with a hand-built zip whose top folder was "Nexus-Mods" against a
# plugin.json name of "Nexus Mods", and nothing caught it until a user could
# not update.
$ErrorActionPreference = "Continue"
python (Join-Path $root "tools\checkzip.py") $out 2>&1 | Write-Host
$checkExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($checkExit -ne 0) { throw "the zip that was just built will not install" }

$size = [math]::Round((Get-Item $out).Length / 1KB, 1)
Write-Host ""
Write-Host ">>> $out  ($size KB)" -ForegroundColor Green
Write-Host ""
Write-Host "Next:" -ForegroundColor Cyan
Write-Host "  1. GitHub > Releases > Draft a new release"
Write-Host "  2. Tag: v$version   Title: v$version"
Write-Host "  3. Tick 'Set as a pre-release' while this is a beta"
Write-Host "  4. Attach $(Split-Path $out -Leaf), then Publish"
Write-Host ""
Write-Host "  The zip's download URL is then:"
Write-Host "  https://github.com/RedRanger14/decky-nexus/releases/download/v$version/Nexus-Mods-$version.zip"
