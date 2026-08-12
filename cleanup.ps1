<#
.SYNOPSIS
    Reclaims disk space by removing regenerable build artefacts and caches.

.DESCRIPTION
    Dry run by default — it prints what it would delete and how much that frees,
    and changes nothing. Add -Apply to actually delete.

    Everything it touches is regenerable:
      .next            rebuilt by `npm run dev` / `npm run build`
      node_modules     restored by `npm install`      (-Deep only)
      .venv            restored by `pip install -r`   (-Deep only)
      __pycache__      rebuilt by Python on next import
      *.db / *.db-wal  local SQLite dev databases     (-IncludeDb only)
      npm / pip cache  re-downloaded on next install

    It never touches source, .env files, or alembic/versions.

.PARAMETER Apply
    Actually delete. Without this, nothing is removed.

.PARAMETER Deep
    Also remove node_modules and .venv. Frees the most, costs a reinstall.

.PARAMETER IncludeDb
    Also remove local SQLite databases. You lose local demo data.

.EXAMPLE
    .\cleanup.ps1
    See what could be freed.

.EXAMPLE
    .\cleanup.ps1 -Apply
    Remove build caches. Safe — no reinstall needed.

.EXAMPLE
    .\cleanup.ps1 -Apply -Deep
    Also remove node_modules and .venv. Requires reinstall afterwards.
#>

[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Deep,
    [switch]$IncludeDb
)

$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$totalBytes = 0

function Format-Size([double]$bytes) {
    if ($bytes -ge 1GB) { return "{0:N2} GB" -f ($bytes / 1GB) }
    if ($bytes -ge 1MB) { return "{0:N1} MB" -f ($bytes / 1MB) }
    if ($bytes -ge 1KB) { return "{0:N0} KB" -f ($bytes / 1KB) }
    return "$bytes B"
}

function Get-DirSize([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return 0 }
    try {
        return (Get-ChildItem -LiteralPath $path -Recurse -File -Force -ErrorAction SilentlyContinue |
                Measure-Object -Property Length -Sum).Sum
    } catch { return 0 }
}

function Remove-Target([string]$path, [string]$label, [string]$restore) {
    if (-not (Test-Path -LiteralPath $path)) { return }

    $size = Get-DirSize $path
    if ($size -eq 0 -and -not (Test-Path -LiteralPath $path -PathType Container)) { return }
    $script:totalBytes += $size

    $sizeText = (Format-Size $size).PadLeft(10)
    Write-Host "  $sizeText  " -NoNewline -ForegroundColor Yellow
    Write-Host $label -NoNewline
    Write-Host "  ($restore)" -ForegroundColor DarkGray

    if ($Apply) {
        try {
            Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
        } catch {
            Write-Host "              could not remove: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
}

Write-Host ""
Write-Host "AURA cleanup" -ForegroundColor Cyan
if (-not $Apply) {
    Write-Host "DRY RUN — nothing will be deleted. Re-run with -Apply to remove." -ForegroundColor DarkYellow
}
Write-Host ""

# ---------------------------------------------------------------------------
# Project artefacts (on this drive)
# ---------------------------------------------------------------------------

Write-Host "Project build caches" -ForegroundColor White
Remove-Target "$root\frontend\.next"          "frontend\.next"              "npm run dev"
Remove-Target "$root\frontend\out"            "frontend\out"                "npm run build"
Remove-Target "$root\frontend\.turbo"         "frontend\.turbo"             "next rebuild"
Remove-Target "$root\backend\.pytest_cache"   "backend\.pytest_cache"       "pytest"
Remove-Target "$root\backend\.ruff_cache"     "backend\.ruff_cache"         "ruff"
Remove-Target "$root\backend\.mypy_cache"     "backend\.mypy_cache"         "mypy"

# __pycache__ under app/ and tests/ only — .venv is handled by -Deep so we don't
# spend minutes walking thousands of dependency folders for a few MB.
$pycache = Get-ChildItem -LiteralPath "$root\backend" -Directory -Recurse -Force `
    -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notlike "*\.venv\*" }
if ($pycache) {
    $size = ($pycache | ForEach-Object { Get-DirSize $_.FullName } | Measure-Object -Sum).Sum
    $totalBytes += $size
    Write-Host "  $((Format-Size $size).PadLeft(10))  " -NoNewline -ForegroundColor Yellow
    Write-Host "backend __pycache__ ($($pycache.Count) dirs)" -NoNewline
    Write-Host "  (python)" -ForegroundColor DarkGray
    if ($Apply) {
        $pycache | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

if ($IncludeDb) {
    Write-Host ""
    Write-Host "Local databases" -ForegroundColor White
    Get-ChildItem -LiteralPath "$root\backend" -File -Force `
        -Include "*.db", "*.db-wal", "*.db-shm" -ErrorAction SilentlyContinue |
        ForEach-Object {
            $totalBytes += $_.Length
            Write-Host "  $((Format-Size $_.Length).PadLeft(10))  " -NoNewline -ForegroundColor Yellow
            Write-Host "backend\$($_.Name)" -NoNewline
            Write-Host "  (recreated on next boot)" -ForegroundColor DarkGray
            if ($Apply) { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
        }
}

if ($Deep) {
    Write-Host ""
    Write-Host "Dependencies — reinstall needed after this" -ForegroundColor White
    Remove-Target "$root\frontend\node_modules" "frontend\node_modules" "npm install"
    Remove-Target "$root\backend\.venv"         "backend\.venv"         "python -m venv .venv; pip install -r requirements.txt"
}

# ---------------------------------------------------------------------------
# System caches — these live on C:, which is what the workspace actually needs
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "System caches on C: (what the Claude workspace needs)" -ForegroundColor White

$npmCache = Join-Path $env:LOCALAPPDATA "npm-cache"
$pipCache = Join-Path $env:LOCALAPPDATA "pip\cache"

foreach ($cache in @(
    @{ Path = $npmCache; Label = "npm cache";  Restore = "npm cache clean --force" },
    @{ Path = $pipCache; Label = "pip cache";  Restore = "pip cache purge" }
)) {
    $size = Get-DirSize $cache.Path
    if ($size -gt 0) {
        Write-Host "  $((Format-Size $size).PadLeft(10))  " -NoNewline -ForegroundColor Yellow
        Write-Host $cache.Label -NoNewline
        Write-Host "  ($($cache.Restore))" -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "  Not deleted automatically — run these yourself:" -ForegroundColor DarkGray
Write-Host "    npm cache clean --force" -ForegroundColor DarkGray
Write-Host "    pip cache purge" -ForegroundColor DarkGray
Write-Host "    cleanmgr /d C:            # Windows Disk Cleanup" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host ("-" * 60) -ForegroundColor DarkGray
if ($Apply) {
    Write-Host "Freed roughly $(Format-Size $totalBytes)" -ForegroundColor Green
} else {
    Write-Host "Would free roughly $(Format-Size $totalBytes)" -ForegroundColor Cyan
    Write-Host "Re-run with -Apply to delete." -ForegroundColor DarkYellow
}

Write-Host ""
Write-Host "Drive space:" -ForegroundColor White
Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
    Where-Object { $_.Used -ne $null -and ($_.Used + $_.Free) -gt 0 } |
    ForEach-Object {
        $free = $_.Free
        $total = $_.Used + $_.Free
        $pct = [math]::Round(($free / $total) * 100)
        $colour = if ($pct -lt 10) { "Red" } elseif ($pct -lt 20) { "Yellow" } else { "Green" }
        Write-Host "  $($_.Name):  " -NoNewline
        Write-Host "$((Format-Size $free)) free of $(Format-Size $total)  ($pct%)" -ForegroundColor $colour
    }

Write-Host ""
Write-Host "The Claude workspace unpacks under C:\Users\$env:USERNAME\AppData\Roaming\Claude," -ForegroundColor DarkGray
Write-Host "so it needs free space on C: specifically. Freeing D: will not help it." -ForegroundColor DarkGray
Write-Host ""
