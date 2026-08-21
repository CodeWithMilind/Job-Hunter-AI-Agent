# Hirevia — Uninstaller (Windows / PowerShell)
# Removes everything the installer created. Safe to re-run.
#
# Usage:
#   .\uninstall.ps1              # remove app, venv, models, cache (keeps configs)
#   .\uninstall.ps1 -Purge       # also delete profile.yaml / companies.yaml / results
#   .\uninstall.ps1 -KeepCache   # keep the $HOME\.jobradar seen-jobs cache
#

param(
    [switch]$Purge,
    [switch]$KeepCache,
    [switch]$Help
)

if ($Help) {
    Write-Host "Usage: .\uninstall.ps1 [-Purge] [-KeepCache]"
    Write-Host "  -Purge        also delete profile.yaml, companies.yaml, results"
    Write-Host "  -KeepCache    keep the ~/.jobradar seen-jobs cache"
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "-- Hirevia uninstaller --" -ForegroundColor Cyan
Write-Host "  Target: $ScriptDir"

# 1. Virtual environment
if (Test-Path (Join-Path $ScriptDir ".venv")) {
    Remove-Item -Recurse -Force (Join-Path $ScriptDir ".venv")
    Write-Host "  [OK] Removed virtual environment (.venv)" -ForegroundColor Green
} else {
    Write-Host "  [>>] No .venv found" -ForegroundColor Yellow
}

# 2. Downloaded binaries and models
foreach ($dir in @("bin", "models")) {
    $p = Join-Path $ScriptDir $dir
    if (Test-Path $p) {
        Remove-Item -Recurse -Force $p
        Write-Host "  [OK] Removed $dir/" -ForegroundColor Green
    } else {
        Write-Host "  [>>] No $dir/ found" -ForegroundColor Yellow
    }
}

# 3. Python bytecode caches
Get-ChildItem -Path $ScriptDir -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "  [OK] Removed __pycache__ directories" -ForegroundColor Green

# 4. Dashboard database
$db = Join-Path $ScriptDir "dashboard\jobradar_dashboard.db"
if (Test-Path $db) {
    Remove-Item -Force $db
    Write-Host "  [OK] Removed dashboard database" -ForegroundColor Green
} else {
    Write-Host "  [>>] No dashboard database found" -ForegroundColor Yellow
}

# 5. Seen-jobs cache (unless -KeepCache)
if (-not $KeepCache) {
    $cacheDir = Join-Path $HOME ".jobradar"
    $cacheDb  = Join-Path $cacheDir "seen_jobs.db"
    if (Test-Path $cacheDb) {
        Remove-Item -Force $cacheDb
        if ((Get-ChildItem $cacheDir -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) {
            Remove-Item -Force $cacheDir
        }
        Write-Host "  [OK] Removed ~/.jobradar seen-jobs cache" -ForegroundColor Green
    } else {
        Write-Host "  [>>] No ~/.jobradar cache found" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [>>] Keeping ~/.jobradar cache (-KeepCache)" -ForegroundColor Yellow
}

# 6. Config files (only with -Purge)
if ($Purge) {
    foreach ($f in @("profile.yaml", "companies.yaml", "results.csv", "results.json")) {
        $p = Join-Path $ScriptDir $f
        if (Test-Path $p) {
            Remove-Item -Force $p
            Write-Host "  [OK] Removed $f" -ForegroundColor Green
        }
    }
} else {
    Write-Host "  [>>] Keeping profile.yaml / companies.yaml (use -Purge to delete)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Hirevia uninstalled. Your profile and companies files are still in:" -ForegroundColor Green
Write-Host "    $ScriptDir"
Write-Host "  (Re-run with -Purge to remove them too.)" -ForegroundColor Green
