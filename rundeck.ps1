# Run one command on a device over SSH, prompting for the password here.
#
# The release-test Deck has no SSH key installed, so I cannot reach it the
# way I reach the Legion. Typing diagnostics on the Deck's on-screen
# keyboard is slow and error-prone, and a mistyped URL looks exactly like a
# real failure. This asks for the password in a PowerShell prompt on the
# PC instead: nothing is typed on the Deck, and no password appears in a
# command line, a log, or a chat message.
#
#   .\rundeck.ps1 -Device 192.168.1.x -Cmd "curl -sI https://example.com"
#   .\rundeck.ps1 -Device steamdeck.local -Sudo -Cmd "journalctl -u plugin_loader -n 50 --no-pager"

param(
    [Parameter(Mandatory = $true)][string]$Device,
    [Parameter(Mandatory = $true)][string]$Cmd,
    [switch]$Sudo,
    [string]$User = "deck",
    [int]$Port = 22
)

$ErrorActionPreference = "Stop"

$sec = Read-Host "password for $User@$Device" -AsSecureString
$pass = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))

# The device's own hostname may collide with another device on the network
# (two SteamOS machines are both "steamdeck" by default), so the host key
# check is relaxed to the point of not failing on a known-changed key. That
# is safe for a LAN device you physically own and are actively testing.
$sshOpts = @(
    "-p", $Port,
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "PreferredAuthentications=password",
    "-o", "PubkeyAuthentication=no"
)

if ($Sudo) {
    # Same handling as deploy.ps1: piped to sudo -S over stdin, scrubbed of
    # the CR and BOM bytes PowerShell adds to native stdin.
    $remote = "tr -dc '[:print:]\n' | sudo -S -p '' sh -c ""$Cmd"""
} else {
    $remote = $Cmd
}

Write-Host "Running on $Device ..." -ForegroundColor Cyan
$pass | ssh @sshOpts "$User@$Device" $remote
