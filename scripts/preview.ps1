[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 6790,
    [switch]$Reset,    # rebuild the seeded state from scratch (only a folder the seed script made)
    [switch]$Rebuild,  # build apps\web again even when dist looks current
    [switch]$Live,     # a real worker and a real Gemini key: episodes are NOT pre-translated, Request really translates
    [string]$KeyFile = '',   # Live: file holding the key; default local-state\secrets\gemini.key, else $env:GEMINI_API_KEY
    [int]$MaxCallsPerDay = 60  # Live: a hard cap on model calls for the whole run (MAX_CALLS_PER_DAY)
)

# One command to look at the console with real rows and no key, no Bazarr, no network (the worker runs with
# --no-worker, so nothing is translated and nothing is read from Bazarr). It seeds an isolated state under
# local-state\preview, builds the React app when its sources are newer than dist, and serves both on 127.0.0.1.
# -Live: the real queue worker runs and a real Gemini key is used, so Request / Retranslate / Extract / Try really call the
# model. The key is read here and handed to the worker through its environment (it seeds the key table on the first run
# of a state, so use -Reset after changing the key); it is never printed or written to a file by this script. There is no
# Bazarr (BAZARR_URL points nowhere, nothing is read or refreshed) and GST_ONLY_PATHS confines work to the demo media.
# Port 6790, not 6789: start-local.ps1 and a Docker service keep 6789. Ctrl+C stops it.
$ErrorActionPreference = 'Stop'
$savedKey = $env:GEMINI_API_KEY   # only used by -Live, before the fake preview clears it
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found: $python. Run 'uv sync --locked --all-packages' from the project root first."
}

$web = Join-Path $projectRoot 'apps\web'
$dist = Join-Path $web 'dist'
$built = Join-Path $dist 'index.html'
$stale = -not (Test-Path -LiteralPath $built)
if (-not $stale) {
    $builtAt = (Get-Item -LiteralPath $built).LastWriteTimeUtc
    $newest = Get-ChildItem -LiteralPath (Join-Path $web 'src'), (Join-Path $web 'public') -Recurse -File |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    $stale = $newest.LastWriteTimeUtc -gt $builtAt -or
        (Get-Item -LiteralPath (Join-Path $web 'package.json')).LastWriteTimeUtc -gt $builtAt
}
if ($Rebuild -or $stale) {
    Write-Host 'Building apps\web ...' -ForegroundColor Cyan
    Push-Location $web
    try {
        if (-not (Test-Path -LiteralPath (Join-Path $web 'node_modules'))) { npm ci; if ($LASTEXITCODE) { throw 'npm ci failed' } }
        npm run build
        if ($LASTEXITCODE) { throw 'npm run build failed' }
    } finally { Pop-Location }
}

# An earlier preview still serving this state would hold its files open (a reset could not delete them): stop it, but
# only a worker of this project; anything else on the port is somebody else's and is left alone.
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    if ($owner.CommandLine -notlike '*apps\server\worker.py*') { throw "Port $Port is used by another program (pid $($listener.OwningProcess)); pass -Port." }
    Write-Host "Stopping the earlier preview on port $Port (pid $($listener.OwningProcess))" -ForegroundColor Yellow
    Stop-Process -Id $listener.OwningProcess -Force
    # Until the port is free the old console still answers; a caller that polls it must not mistake it for the new one.
    for ($i = 0; $i -lt 20 -and (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 500 }
}

$stateDir = Join-Path $projectRoot 'local-state\preview'
$seed = @('run', '--locked', '--directory', (Join-Path $projectRoot 'apps\server'), 'python', '-m', 'tests.preview_seed', '--state-dir', $stateDir)
if ($Reset) { $seed += '--reset' }
if ($Live) { $seed += '--live' }
$liveKey = ''
if ($Live) {
    # Read the key before anything is built or deleted, so a missing key costs nothing.
    if (-not $KeyFile) { $KeyFile = Join-Path $projectRoot 'local-state\secrets\gemini.key' }
    if (Test-Path -LiteralPath $KeyFile) { $liveKey = (Get-Content -LiteralPath $KeyFile -Raw).Trim() }
    elseif ($savedKey) { $liveKey = $savedKey.Trim() }
    if (-not $liveKey) { throw "-Live needs a Gemini key: put it alone in $KeyFile (git-ignored) or set GEMINI_API_KEY in this shell. It is not printed." }
}
& uv @seed
if ($LASTEXITCODE) { throw 'seeding the preview state failed' }

$env:STATE_DIR = $stateDir
$env:DATA_DIR = $stateDir
$env:QUEUE_DIR = Join-Path $stateDir 'queue'
$env:LOG_DIR = Join-Path $stateDir 'logs'
$env:APP_CONFIG_PATH = Join-Path $stateDir 'config.json'
$env:POSTPROCESS_TARGETS_PATH = Join-Path $stateDir 'postprocess\targets.json'
$env:TMDB_CACHE_PATH = Join-Path $stateDir 'cache\tmdb_cache.json'
$env:STATIC_DIR = $dist
Remove-Item Env:GEMINI_API_KEY, Env:GEMINI_API_KEY1, Env:GEMINI_API_KEY2 -ErrorAction SilentlyContinue  # the fake preview never sees a key
$env:CONSOLE_AUTH = 'off'   # bound to 127.0.0.1 only
New-Item -ItemType Directory -Force -Path $env:QUEUE_DIR | Out-Null
$workerArgs = @('--host', '127.0.0.1', '--port', $Port)
if ($Live) {
    $env:GEMINI_API_KEY = $liveKey
    # The cap is a setting, not an environment variable: config.json is imported on the first run of a state (use -Reset).
    # Written without a byte-order mark (Windows PowerShell's -Encoding utf8 adds one, and the worker's json.loads refuses it).
    $json = @{ max_calls_per_day = $MaxCallsPerDay } | ConvertTo-Json
    [System.IO.File]::WriteAllText($env:APP_CONFIG_PATH, $json, (New-Object System.Text.UTF8Encoding($false)))
    $env:BAZARR_URL = 'http://127.0.0.1:1'           # nothing to ask; fails at once instead of waiting on a name
    $env:GST_ONLY_PATHS = Join-Path $stateDir 'preview-media'
} else {
    $workerArgs += '--no-worker'
}

$mode = if ($Live) { "LIVE (real Gemini key, cap $MaxCallsPerDay model calls/day)" } else { 'fake (no model calls)' }
Write-Host "Preview console: http://127.0.0.1:$Port  [$mode]  (state: $stateDir)" -ForegroundColor Cyan
& $python (Join-Path $projectRoot 'apps\server\worker.py') @workerArgs
