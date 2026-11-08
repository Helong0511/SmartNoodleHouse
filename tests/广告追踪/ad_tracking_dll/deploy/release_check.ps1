$ErrorActionPreference = "Stop"
$Dll = Join-Path $PSScriptRoot "..\bin\ad_tracking_engine.dll"
$Dll = [IO.Path]::GetFullPath($Dll)

Write-Host "========================================"
Write-Host "Ad Tracking Engine - Release Security Check"
Write-Host "========================================"

if (!(Test-Path $Dll)) { throw "DLL not found: $Dll" }

$p = Get-Item $Dll
Write-Host ("DLL size: {0:N0} bytes" -f $p.Length)
Write-Host ("SHA256: " + (Get-FileHash $Dll -Algorithm SHA256).Hash)

$forbidden = @("*.pdb","*.dSYM","*.rlib","*.rmeta")
foreach ($pattern in $forbidden) {
  $hits = Get-ChildItem (Split-Path $Dll) -Filter $pattern -ErrorAction SilentlyContinue
  if ($hits) { throw "Forbidden file found in release directory: $pattern" }
}

$bytes = [IO.File]::ReadAllBytes($Dll)
$text = [Text.Encoding]::ASCII.GetString($bytes)

$leaks = @(
  "sk-",
  "api_key",
  "private_key",
  "secret_key",
  "BEGIN PRIVATE KEY",
  "BEGIN RSA PRIVATE KEY"
)
foreach ($needle in $leaks) {
  if ($text.Contains($needle)) { throw "Possible sensitive string leak: $needle" }
}

Write-Host "Result: PASS"
