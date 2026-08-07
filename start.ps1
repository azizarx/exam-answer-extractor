# PowerShell script to start all services
# Run this script to start the complete system

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Exam Answer Sheet Extraction System Startup" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# Check if virtual environment exists
if (-Not (Test-Path "venv\Scripts\Activate.ps1")) {
    Write-Host "❌ Virtual environment not found!" -ForegroundColor Red
    Write-Host "Please run: python -m venv venv" -ForegroundColor Yellow
    exit 1
}

# Check if .env exists
if (-Not (Test-Path ".env")) {
    Write-Host "❌ .env file not found!" -ForegroundColor Red
    Write-Host "Please copy .env.example to .env and configure it" -ForegroundColor Yellow
    exit 1
}

Write-Host "✅ Starting services..." -ForegroundColor Green
Write-Host ""

# Function to start a process in a new window
function Start-ServiceWindow {
    param(
        [string]$Title,
        [string]$Command
    )
    
    Write-Host "Starting: $Title" -ForegroundColor Yellow
    
    Start-Process powershell -ArgumentList `
        "-NoExit", `
        "-Command", `
        "& {`
            `$host.UI.RawUI.WindowTitle = '$Title'; `
            Write-Host '================================================' -ForegroundColor Cyan; `
            Write-Host '  $Title' -ForegroundColor Cyan; `
            Write-Host '================================================' -ForegroundColor Cyan; `
            Write-Host ''; `
            cd '$PWD'; `
            .\venv\Scripts\Activate.ps1; `
            $Command `
        }"
}

# Start Redis (if not running)
$redisRunning = Get-Process redis-server -ErrorAction SilentlyContinue
if (-Not $redisRunning) {
    Write-Host "Starting Redis..." -ForegroundColor Yellow
    Start-Process redis-server
    Start-Sleep -Seconds 2
}
else {
    Write-Host "✅ Redis already running" -ForegroundColor Green
}

# Check database
Write-Host "Checking database..." -ForegroundColor Yellow
.\venv\Scripts\Activate.ps1
$dbCheck = python -c "from backend.db.database import engine; from sqlalchemy import inspect; print(len(inspect(engine).get_table_names()))" 2>&1

if ($LASTEXITCODE -ne 0 -or $dbCheck -eq "0") {
    Write-Host "⚠️  Database not initialized. Running init..." -ForegroundColor Yellow
    python init_db.py init
}
else {
    Write-Host "✅ Database ready" -ForegroundColor Green
}

Write-Host ""

# Start Celery Worker
Start-ServiceWindow `
    -Title "Celery Worker" `
    -Command "celery -A backend.worker worker --loglevel=info -Q exam_processing --pool=solo"

Start-Sleep -Seconds 2

# Start FastAPI
Start-ServiceWindow `
    -Title "FastAPI Server" `
    -Command "python main.py"

Start-Sleep -Seconds 3

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "  All services started!" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "API Documentation: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "API Health Check: http://localhost:8000/health" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C in each window to stop services" -ForegroundColor Yellow
Write-Host ""
