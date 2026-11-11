[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 6789,
    [switch]$Demo,
    [switch]$NoWorker
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found: $python. Run 'uv sync --all-packages' from the project root first."
}

# Keep local previews isolated from the Docker service's /state and /queue mounts.
$stateDir = Join-Path $projectRoot 'local-state'
$queueDir = Join-Path $stateDir 'queue'
$postprocessDir = Join-Path $stateDir 'postprocess'
New-Item -ItemType Directory -Force -Path $queueDir, $postprocessDir | Out-Null

if ($Demo) {
    $demoDir = Join-Path $stateDir 'demo-media'
    New-Item -ItemType Directory -Force -Path $demoDir, (Join-Path $queueDir 'processing') | Out-Null
    $subtitlePath = Join-Path $demoDir 'Example.en.srt'
    $outputPath = Join-Path $demoDir 'Example.zh.srt'
    Set-Content -LiteralPath $subtitlePath -Encoding utf8 -Value "1`n00:00:00,000 --> 00:00:01,000`nHello"
    Set-Content -LiteralPath (Join-Path $demoDir 'Example.progress') -Encoding utf8 -Value '{"line":42}'
    Set-Content -LiteralPath (Join-Path $demoDir 'Example.zh.partial.srt') -Encoding utf8 -Value 'Partial translated subtitle'
    $job = @{
        job_id = 'local-progress-preview'
        subtitle_path = $subtitlePath
        output_path = $outputPath
        source_code = 'en'
        target_code = 'zh'
        stage = 'Sending subtitle batches to Gemini'
        started_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    } | ConvertTo-Json -Compress
    Set-Content -LiteralPath (Join-Path $queueDir 'processing\local-progress-preview.json') -Encoding utf8 -Value $job
    $NoWorker = $true
}

$env:STATE_DIR = $stateDir
$env:QUEUE_DIR = $queueDir
$env:LOG_DIR = Join-Path $stateDir 'logs'
$env:APP_CONFIG_PATH = Join-Path $stateDir 'config.json'
$env:POSTPROCESS_TARGETS_PATH = Join-Path $postprocessDir 'targets.json'
$env:TMDB_CACHE_PATH = Join-Path $stateDir 'cache\tmdb_cache.json'
# The console requires Basic Auth. Local preview binds to 127.0.0.1, so skip auth
# unless CONSOLE_USER / CONSOLE_PASSWORD are already set in this shell.
if (-not ($env:CONSOLE_USER -and $env:CONSOLE_PASSWORD)) { $env:CONSOLE_AUTH = 'off' }
$appRoot = Join-Path $projectRoot 'apps\worker'
$env:STATIC_DIR = Join-Path $appRoot 'static'

$arguments = @((Join-Path $appRoot 'worker.py'), '--host', '127.0.0.1', '--port', $Port)
if ($NoWorker) { $arguments += '--no-worker' }

Write-Host "Local console: http://localhost:$Port" -ForegroundColor Cyan
if ($Demo) { Write-Host 'Demo processing job created. Open Queue to preview the live progress card.' -ForegroundColor Yellow }
& $python @arguments
