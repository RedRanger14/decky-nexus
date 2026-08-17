# Print Decky Loader's own log from the device.
#
# Decky runs as a system service with no StandardOutput override, so its
# output goes to the systemd journal and reading it needs sudo. That is the
# one thing this session could not do over plain SSH, and it is the only
# place that says WHY an install failed: Decky's installer logs the reason
# and then returns without telling the UI, which is why a failed install
# sits on "PARSING ZIP FILE" forever.
#
#   .\getdeckylog.ps1           last 60 lines
#   .\getdeckylog.ps1 -Lines 200 -Filter "zip|plugin.json|fetch"
#
# Same sudo handling as deploy.ps1: the password is piped over ssh stdin and
# never appears in a command line or in this file.

param(
    # The device to read. Defaults to the dev device in settings, but the
    # release testing happens on a SECOND Steam Deck, and this session
    # already wasted diagnostics reading the wrong machine's logs.
    [string]$Device = "",
    [int]$Lines = 60,
    [string]$Filter = ""
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

$cfgPath = Join-Path $root ".vscode\settings.json"
if (-not (Test-Path $cfgPath)) {
    $cfgPath = Join-Path $root ".vscode\defsettings.json"
}
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$host_ = if ($Device) { $Device } else { $cfg.deckip }
$target = "$($cfg.deckuser)@$host_"
$port = if ($cfg.deckport) { $cfg.deckport } else { 22 }

$sudoPass = $cfg.deckpass
if ((-not $sudoPass) -or ($sudoPass -eq "ssap")) {
    $sec = Read-Host "sudo password for $($cfg.deckuser) on the device" -AsSecureString
    $sudoPass = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}

$cmd = "journalctl -u plugin_loader -n $Lines --no-pager"
if ($Filter) { $cmd += " | grep -aiE '$Filter'" }

Write-Host "Reading Decky's log from $target ..." -ForegroundColor Cyan
$sudoPass | ssh -p $port -o ConnectTimeout=10 $target `
    "tr -dc '[:print:]\n' | sudo -S -p '' sh -c ""$cmd"""
if ($LASTEXITCODE -ne 0) { throw "could not read the journal" }
