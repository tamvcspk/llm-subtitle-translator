[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 6790,
    [switch]$Reset,    # rebuild the seeded state from scratch (only a folder the seed script made)
    [switch]$Rebuild,  # build apps\web again even when dist looks current
    [switch]$Live,     # a real worker and a real Gemini key: episodes are NOT pre-translated, Request really translates
    [switch]$Bazarr,   # a stand-in Bazarr (scripts\fake_bazarr.py, port 6791), the catalog sync running, an older queue to settle; still no key and no job ever runs
    [ValidateRange(1, 65535)]
    [int]$BazarrPort = 6791,
    [string]$KeyFile = '',   # Live: file holding the key; default local-state\secrets\gemini.key, else $env:GEMINI_API_KEY
    # Live: a hard cap on model calls for the whole run (max_calls_per_day). Free tier: Flash 10K/day, Flash Lite 150K/day,
    # so 1000 leaves room for other users of the same project and still stops a runaway loop.
    [int]$MaxCallsPerDay = 1000,
    [int]$MaxCallsPerJob = 20,  # Live: a hard cap on model calls of one job (max_calls_per_job, same as the code default)
    # Live: the light model does every task; the strong one only takes hard jobs (gst_fallback_model) and the judge.
    # Pick models with free-tier quota on the key's project (AI Studio rate-limit page): gemini-3.5-flash has none there.
    [string]$LightModel = 'gemini-3.1-flash-lite',
    [string]$StrongModel = 'gemini-3.8-flash'
)

# One command to look at the console with real rows and no key, no Bazarr, no network (the worker runs with
# --no-worker, so nothing is translated and nothing is read from Bazarr). It seeds an isolated state under
# local-state\preview, builds the React app when its sources are newer than dist, and serves both on 127.0.0.1.
# -Live: the real queue worker runs and a real Gemini key is used, so Request / Retranslate / Extract / Try really call the
# model. The key is read here and handed to the worker through its environment (it seeds the key table on the first run
# of a state, so use -Reset after changing the key); it is never printed or written to a file by this script. There is no
# Bazarr (BAZARR_URL points nowhere, nothing is read or refreshed) and GST_ONLY_PATHS confines work to the demo media.
# -Bazarr: the catalog sync loop runs against scripts\fake_bazarr.py (Sync now really reads something; open
# http://127.0.0.1:6791/__fake/shrink to make it answer with a much smaller library, /__fake/restore to undo), the old queue
# folders of an earlier worker are left in the state so the start-up settles them, and the queue itself never runs (--no-queue,
# no key): nothing can call a model. Not together with -Live.
# Port 6790, not 6789: start-local.ps1 and a Docker service keep 6789. Ctrl+C stops it.
$ErrorActionPreference = 'Stop'
if ($Live -and $Bazarr) { throw '-Bazarr and -Live cannot be used together: -Live has no Bazarr on purpose.' }
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
if ($Bazarr) { $seed += '--old-queue' }
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
    # job_settle_seconds 0: the seed has just written the source files and the default 600 s wait would hold the first job for ~10 minutes.
    # Models are pinned here, not left to the code defaults: extract, summarize and research run on the light model, the
    # strong one is only the fallback layer and the judge. suggest_from_facts off: the look for proposals after a job is a
    # spend nobody clicked; turn it on in Settings when the plan's Money section allows it (the judge is off by default).
    $json = @{
        max_calls_per_day = $MaxCallsPerDay; max_calls_per_job = $MaxCallsPerJob; job_settle_seconds = 0
        gst_model = $LightModel; memory_model = $LightModel; summarize_model = $LightModel; research_model = $LightModel
        gst_fallback_model = $StrongModel; judge_model = $StrongModel
        suggest_from_facts = $false
    } | ConvertTo-Json
    [System.IO.File]::WriteAllText($env:APP_CONFIG_PATH, $json, (New-Object System.Text.UTF8Encoding($false)))
    $env:BAZARR_URL = 'http://127.0.0.1:1'           # nothing to ask; fails at once instead of waiting on a name
    $env:GST_ONLY_PATHS = Join-Path $stateDir 'preview-media'
} elseif ($Bazarr) {
    # The catalog sync runs, the queue does not. The address and key are read once, on the first run of a state (-Reset).
    $env:BAZARR_URL = "http://127.0.0.1:$BazarrPort"
    $env:BAZARR_API_KEY = 'preview'
    $env:GST_ONLY_PATHS = Join-Path $stateDir 'preview-media'
    $workerArgs += '--no-queue'
} else {
    $workerArgs += '--no-worker'
}

$fake = $null
if ($Bazarr) {
    $busy = Get-NetTCPConnection -LocalPort $BazarrPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($busy) {
        $other = Get-CimInstance Win32_Process -Filter "ProcessId = $($busy.OwningProcess)"
        if ($other.CommandLine -notlike '*fake_bazarr.py*') { throw "Port $BazarrPort is used by another program (pid $($busy.OwningProcess)); pass -BazarrPort." }
        Stop-Process -Id $busy.OwningProcess -Force
        Start-Sleep -Milliseconds 500
    }
    $fake = Start-Process -FilePath $python -PassThru -WindowStyle Hidden -ArgumentList @(
        (Join-Path $projectRoot 'scripts\fake_bazarr.py'), '--media', (Join-Path $stateDir 'preview-media'), '--port', $BazarrPort
    )
    Write-Host "Stand-in Bazarr: http://127.0.0.1:$BazarrPort  (/__fake/shrink, /__fake/restore, /__fake/state)" -ForegroundColor Cyan
}

$mode = if ($Live) { "LIVE (real Gemini key, $LightModel, strong $StrongModel, cap $MaxCallsPerDay calls/day, $MaxCallsPerJob/job; settings are read on the first run of a state: use -Reset)" } elseif ($Bazarr) { 'fake, with a stand-in Bazarr and an older queue (no model calls, the queue never runs)' } else { 'fake (no model calls)' }
Write-Host "Preview console: http://127.0.0.1:$Port  [$mode]  (state: $stateDir)" -ForegroundColor Cyan
try {
    & $python (Join-Path $projectRoot 'apps\server\worker.py') @workerArgs
} finally {
    if ($fake -and -not $fake.HasExited) { Stop-Process -Id $fake.Id -Force }
}
