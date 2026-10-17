# Run from the extracted, checksummed release. Windows PowerShell 5.1 or newer.
$ErrorActionPreference = 'Stop'
foreach ($line in Get-Content -LiteralPath (Join-Path $PSScriptRoot 'checksums.txt')) {
    $parts = $line -split '  ', 2
    if ($parts.Count -ne 2 -or $parts[1] -match '[\\:]|(^|/)\.\.(/|$)') { throw 'Invalid checksum file' }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PSScriptRoot $parts[1])).Hash.ToLowerInvariant()
    if ($actual -ne $parts[0]) { throw "Checksum mismatch: $($parts[1])" }
}
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { 'arm64' } else { 'amd64' }
$binary = Join-Path $PSScriptRoot "smaugctl-windows-$arch.exe"
$operation = if ((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'edition.txt') -Raw).Trim() -eq 'mcp') { @('mcp', 'setup') } else { @('install') }
if ($args.Count -ge 2 -and $args[0] -eq '--dir') {
    $target = $args[1]
    $remaining = @($args | Select-Object -Skip 2)
    & $binary --dir $target @operation --source $PSScriptRoot @remaining
} else {
    & $binary @operation --source $PSScriptRoot @args
}
exit $LASTEXITCODE
