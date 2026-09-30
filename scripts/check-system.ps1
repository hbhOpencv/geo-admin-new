$ErrorActionPreference = "Stop"

$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $packageRoot "backend"
$pythonExe = Join-Path $packageRoot "runtime\python\python.exe"
$database = Join-Path $backendDir "geology_norm.db"
$frontend = Join-Path $packageRoot "frontend\dist\index.html"

Write-Host "Checking delivery package: $packageRoot"
foreach ($required in @($pythonExe, $database, $frontend)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required runtime file is missing: $required"
    }
    Write-Host "[OK] $required"
}

Push-Location $backendDir
try {
    & $pythonExe -c "import fastapi,uvicorn,sqlalchemy,numpy,pandas,scipy,trimesh,shapely,rtree,triangle; print('[OK] Python core dependencies')"
    if ($LASTEXITCODE -ne 0) { throw "Python dependency check failed" }
    & $pythonExe -c "import sqlite3; c=sqlite3.connect('geology_norm.db'); print('[OK] database quick_check=' + c.execute('pragma quick_check').fetchone()[0]); c.close()"
    if ($LASTEXITCODE -ne 0) { throw "Database check failed" }
}
finally {
    Pop-Location
}

Write-Host "Environment check complete. URL: http://127.0.0.1:8000/login" -ForegroundColor Green
