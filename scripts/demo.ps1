param(
    [switch]$SkipEvaluation
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath ".env")) {
    throw "Missing .env. Copy .env.example to .env and configure AGENTPROBE_API_KEY before running make demo."
}

if (-not (Test-Path -LiteralPath "frontend\node_modules")) {
    throw "Frontend dependencies are missing. Run npm --prefix frontend ci first."
}

$apiKey = (
    & uv run --env-file .env --locked python -c `
        "from backend.app.core.config import Settings; print(Settings().api_key.get_secret_value())"
) -join ""
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($apiKey)) {
    throw "Set a non-empty AGENTPROBE_API_KEY in .env before running make demo."
}

foreach ($port in @(8000, 5173)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Stop that service before running make demo."
    }
}

$uvPath = (Get-Command "uv").Source
$npmPath = (Get-Command "npm.cmd").Source
$env:AGENTPROBE_LLM_PROVIDER = "mock"
$env:AGENTPROBE_DEMO_BAD_PROMPT = "false"
$apiProcess = Start-Process -FilePath $uvPath -ArgumentList @(
    "run",
    "--env-file",
    ".env",
    "--locked",
    "uvicorn",
    "backend.app.api:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8000"
) -WorkingDirectory $projectRoot -PassThru

$apiReady = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        $apiReady = (Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing).StatusCode -eq 200
        if ($apiReady) {
            break
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $apiReady) {
    Stop-Process -Id $apiProcess.Id -ErrorAction SilentlyContinue
    throw "The API did not become ready. Inspect the API terminal output."
}

if (-not $SkipEvaluation) {
    try {
        Write-Host "Running and saving the local evaluation suite..."
        $run = Invoke-RestMethod `
            -Method Post `
            -Uri "http://127.0.0.1:8000/runs" `
            -Headers @{ Authorization = "Bearer $apiKey" } `
            -ContentType "application/json" `
            -Body '{"suite":"all","repeats":1}'
        Write-Host "Saved run $($run.id): $($run.summary.passed)/$($run.summary.cases) passed."
    } catch {
        Stop-Process -Id $apiProcess.Id -ErrorAction SilentlyContinue
        throw "The evaluation request failed: $($_.Exception.Message)"
    }
}

$dashboardCommand = "`"$npmPath`" --prefix frontend run dev -- --host 127.0.0.1 --strictPort"
$dashboardProcess = Start-Process -FilePath $env:ComSpec -ArgumentList @(
    "/k",
    $dashboardCommand
) -WorkingDirectory $projectRoot -PassThru

$dashboardReady = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if (-not $dashboardReady) {
        try {
            $dashboardReady = (Invoke-WebRequest -Uri "http://127.0.0.1:5173/" -UseBasicParsing).StatusCode -eq 200
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if ($dashboardReady) {
        break
    }
}

if (-not $dashboardReady) {
    Stop-Process -Id $apiProcess.Id -ErrorAction SilentlyContinue
    Stop-Process -Id $dashboardProcess.Id -ErrorAction SilentlyContinue
    throw "The dashboard did not become ready. Inspect the API and dashboard terminal output."
}

Write-Host "API process PID: $($apiProcess.Id)"
Write-Host "Dashboard terminal PID: $($dashboardProcess.Id)"
Write-Host "API: http://127.0.0.1:8000"
Write-Host "Dashboard: http://127.0.0.1:5173"
Write-Host "Close the dashboard terminal; stop the API process with Stop-Process -Id $($apiProcess.Id)."
