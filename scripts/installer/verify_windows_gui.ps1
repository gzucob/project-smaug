# Exercise the public Windows GUI binaries in an isolated temporary directory.
# A native browser stub captures the URL; no browser, services or shortcuts change.
param([Parameter(Mandatory = $true)][string]$InputDirectory)
$ErrorActionPreference = 'Stop'
$inputRoot = (Resolve-Path -LiteralPath $InputDirectory).Path
$root = Join-Path ([System.IO.Path]::GetTempPath()) ('smaug-desktop-verify-' + [guid]::NewGuid())
$previousPath = $env:Path
$previousLog = $env:SMAUG_GUI_OPEN_LOG
New-Item -ItemType Directory -Path (Join-Path $root 'bin') -Force | Out-Null
function Wait-BrowserLaunch([string]$ExpectedUrl) {
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $lines = @(Get-Content -LiteralPath $env:SMAUG_GUI_OPEN_LOG -ErrorAction SilentlyContinue)
        if ($lines.Count -eq 2) { break }
        if ((Get-Date) -gt $deadline) { throw 'Browser was not launched' }
        Start-Sleep -Milliseconds 100
    } while ($true)
    if ($lines[1] -ne $ExpectedUrl) { throw 'Wrong browser URL' }
    $browser = Get-Process -Id ([int]$lines[0]) -ErrorAction SilentlyContinue
    if ($browser -and !$browser.WaitForExit(10000)) { throw 'Browser stub did not exit' }
    Remove-Item -LiteralPath $env:SMAUG_GUI_OPEN_LOG
}
try {
    $source = Join-Path $root 'browser.go'
    @'
package main
import ("fmt"; "os")
func main() {
    if len(os.Args) > 1 {
        record := fmt.Sprintf("%d\n%s", os.Getpid(), os.Args[len(os.Args)-1])
        if err := os.WriteFile(os.Getenv("SMAUG_GUI_OPEN_LOG"), []byte(record), 0600); err != nil {
            os.Exit(1)
        }
    }
}
'@ | Set-Content -LiteralPath $source -Encoding ascii
    Push-Location -LiteralPath $root
    try {
        & go build -o (Join-Path $root 'bin/rundll32.exe') $source
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not build browser stub' }
    $env:Path = (Join-Path $root 'bin') + ';' + $env:Path
    $env:SMAUG_GUI_OPEN_LOG = Join-Path $root 'opened.log'
    foreach ($edition in @('full', 'backend')) {
        $dir = Join-Path $root ($edition + ' installation with spaces')
        $name = if ($edition -eq 'full') { 'smaug-installer.exe' } else { 'smaug-backend-installer.exe' }
        $binary = Join-Path $inputRoot $name
        $arguments = @('--dir', ('"' + $dir + '"'), 'gui')
        $manager = Start-Process -FilePath $binary -ArgumentList $arguments -PassThru
        try {
            $session = Join-Path $dir 'gui-session.json'
            $deadline = (Get-Date).AddSeconds(30)
            while (!(Test-Path -LiteralPath $session)) {
                if ($manager.HasExited -or (Get-Date) -gt $deadline) { throw 'GUI failed to open' }
                Start-Sleep -Milliseconds 100
            }
            $url = (Get-Content -LiteralPath $session | ConvertFrom-Json).url
            $origin = ([uri]$url).GetLeftPart([System.UriPartial]::Authority)
            $state = Invoke-RestMethod ($url + 'status')
            if ($state.edition -ne $edition -or $state.os -ne 'windows' -or $state.installed) {
                throw 'Unexpected initial installation state'
            }
            $page = Invoke-WebRequest $url -UseBasicParsing
            if ($page.Content -notmatch 'Instalar o Smaug') { throw 'Missing installation page' }
            foreach ($asset in @('app.js', 'style.css', 'icon.svg')) {
                $response = Invoke-WebRequest ($url + $asset) -UseBasicParsing
                if ($response.StatusCode -ne 200) { throw "Missing GUI asset: $asset" }
            }
            Wait-BrowserLaunch $url
            $second = Start-Process -FilePath $binary -ArgumentList $arguments -PassThru -Wait
            if ($second.ExitCode -ne 0) { throw 'Second launch failed' }
            if ((Get-Content -LiteralPath $session | ConvertFrom-Json).url -ne $url) {
                throw 'Second launch created a duplicate GUI session'
            }
            Wait-BrowserLaunch $url
            Invoke-RestMethod ($url + 'action') -Method Post -ContentType 'application/json' -Headers @{Origin = $origin} -Body '{"action":"close"}' | Out-Null
            if (!$manager.WaitForExit(10000) -or $manager.ExitCode -ne 0) { throw 'GUI failed to close cleanly' }
            if (Test-Path -LiteralPath $session) { throw 'Session was not cleaned up' }
            Write-Output "Verified native Windows GUI: $edition (assets, browser dispatch, singleton, shutdown)"
        } finally {
            if (!$manager.HasExited) { Stop-Process -Id $manager.Id }
        }
    }
} finally {
    $env:Path = $previousPath
    $env:SMAUG_GUI_OPEN_LOG = $previousLog
    Remove-Item -LiteralPath $root -Recurse -Force
}
