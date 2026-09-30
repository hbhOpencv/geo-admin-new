$ErrorActionPreference = "Stop"

$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = (Resolve-Path (Join-Path $packageRoot "runtime\python\python.exe")).Path
$pidFile = Join-Path $packageRoot ".runtime\backend.pid"

if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    Write-Host "No delivery server PID was found. The system may already be stopped."
    exit 0
}

$processId = [int](Get-Content -LiteralPath $pidFile -Raw)
$process = Get-Process -Id $processId -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "The system is already stopped."
    exit 0
}

if ($process.Path -ne $pythonExe) {
    throw "PID $processId does not belong to this delivery runtime. Refusing to stop it."
}

Stop-Process -Id $processId
Remove-Item -LiteralPath $pidFile -Force
Write-Host "System stopped."
