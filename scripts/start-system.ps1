$ErrorActionPreference = "Stop"

$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $packageRoot "backend"
$pythonExe = Join-Path $packageRoot "runtime\python\python.exe"
$stateDir = Join-Path $packageRoot ".runtime"
$logDirName = -join ([char[]](0x8FD0, 0x884C, 0x65E5, 0x5FD7))
$logDir = Join-Path $packageRoot $logDirName
$pidFile = Join-Path $stateDir "backend.pid"
$stdoutLog = Join-Path $logDir "backend.out.log"
$stderrLog = Join-Path $logDir "backend.err.log"
$healthUrl = "http://127.0.0.1:8000/health"
$loginUrl = "http://127.0.0.1:8000/login"

foreach ($required in @(
    $pythonExe,
    (Join-Path $backendDir "geology_norm.db"),
    (Join-Path $packageRoot "frontend\dist\index.html")
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required runtime file is missing: $required"
    }
}

try {
    $existing = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    if ($existing.ok -and $existing.delivery -eq "geo-admin-2026-08-20") {
        Write-Host "The system is already running: $loginUrl"
        Start-Process $loginUrl
        exit 0
    }
    throw "Port 8000 is already used by another service. Stop it before starting this delivery package."
} catch {
    if ($_.Exception.Message -like "Port 8000*") { throw }
}

New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "admin123"
$env:JWT_SECRET = "geo-admin-delivery-2026-08-20-local-review"
$env:CORS_ORIGINS = "http://127.0.0.1:8000"

$process = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $backendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru
Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ascii

for ($attempt = 1; $attempt -le 60; $attempt++) {
    Start-Sleep -Seconds 1
    if ($process.HasExited) {
        Write-Host "The server exited during startup. Error log:" -ForegroundColor Red
        if (Test-Path -LiteralPath $stderrLog) {
            Get-Content -LiteralPath $stderrLog -Tail 30
        }
        exit 1
    }
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        if ($health.ok) {
            Write-Host "System started: $loginUrl" -ForegroundColor Green
            Write-Host "Username: admin    Password: admin123"
            Start-Process $loginUrl
            exit 0
        }
    } catch {
        # Keep waiting while the application initializes.
    }
}

Write-Host "Health check timed out after 60 seconds. See: $stderrLog" -ForegroundColor Red
exit 1
