$ErrorActionPreference = "Stop"
$Dll = Join-Path $PSScriptRoot "..\bin\ad_tracking_engine.dll"
$Dll = [IO.Path]::GetFullPath($Dll)

Write-Host "========================================"
Write-Host "广告追踪引擎 - 发布安全检查"
Write-Host "========================================"

if (!(Test-Path $Dll)) { throw "找不到 DLL: $Dll" }

$p = Get-Item $Dll
Write-Host ("DLL 大小: {0:N0} bytes" -f $p.Length)
Write-Host ("SHA256: " + (Get-FileHash $Dll -Algorithm SHA256).Hash)

$forbidden = @("*.pdb","*.dSYM","*.rlib","*.rmeta")
foreach ($pattern in $forbidden) {
  $hits = Get-ChildItem (Split-Path $Dll) -Filter $pattern -ErrorAction SilentlyContinue
  if ($hits) { throw "发布目录存在禁止文件: $pattern" }
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
  if ($text.Contains($needle)) { throw "疑似敏感字符串泄露: $needle" }
}

Write-Host "结果: PASS"
