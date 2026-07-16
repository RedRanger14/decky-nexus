# Pushes your Nexus Personal API key to the plugin's settings file on the device.
# Usage: .\set-api-key.ps1   (paste the key at the masked prompt, press Enter)
# The key goes straight from this prompt to the device over SSH - no temp files,
# no shell history, nothing logged.

$ErrorActionPreference = "Stop"
$cfg = Get-Content (Join-Path $PSScriptRoot ".vscode\settings.json") -Raw | ConvertFrom-Json
$target = "$($cfg.deckuser)@$($cfg.deckip)"

$sec = Read-Host "Paste your Nexus Personal API key (input is hidden)" -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)).Trim()
if (-not $key) { Write-Host "No key entered - nothing done."; exit 1 }

# tr scrubs BOM/CRLF junk that Windows pipes add; umask makes the file 600 on creation
$json = '{"api_key": "' + $key + '"}'
$json | ssh -p $cfg.deckport -o ConnectTimeout=10 $target "umask 077; tr -dc '[:print:]' > ~/homebrew/settings/Nexus-Mods/settings.json && chmod 600 ~/homebrew/settings/Nexus-Mods/settings.json && echo '>>> key saved on device'"
if ($LASTEXITCODE -ne 0) { throw "push failed - is the device awake?" }
Write-Host "Now reopen the plugin panel: Status should show your Nexus username." -ForegroundColor Green
