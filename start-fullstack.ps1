# PowerShell script to start BOTH backend and frontend

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Exam Answer Extractor - Full Stack Startup" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

$rootPath = Get-Location

# Function to start a service in a new window
function Start-ServiceWindow {
    param(
        [string]$Title,
        [string]$WorkingDirectory,
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
            cd '$WorkingDirectory'; `
            $Command `
        }"
}

Write-Host "Checking prerequisites..." -ForegroundColor Yellow
Write-Host ""

# Check Python
$pythonCheck = python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Python not found! Please install Python 3.9+" -ForegroundColor Red
    pause
    exit 1
}
Write-Host "✅ Python: $pythonCheck" -ForegroundColor Green

# Check Node.js
$nodeCheck = node --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Node.js not found! Please install Node.js 18+" -ForegroundColor Red
    pause
    exit 1
}
Write-Host "✅ Node.js: $nodeCheck" -ForegroundColor Green

Write-Host ""
Write-Host "Starting services..." -ForegroundColor Green
Write-Host ""

# Start Backend (FastAPI)
$backendPath = $rootPath
Start-ServiceWindow `
    -Title "Backend API (FastAPI)" `
    -WorkingDirectory $backendPath `
    -Command ".\venv\Scripts\Activate.ps1; python main.py"

Start-Sleep -Seconds 3

# Start Frontend (React)
$frontendPath = Join-Path $rootPath "frontend"
Start-ServiceWindow `
    -Title "Frontend (React + Vite)" `
    -WorkingDirectory $frontendPath `
    -Command "npm run dev"

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  All services started!" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Backend API: http://localhost:8000" -ForegroundColor Cyan
Write-Host "API Docs: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""
Write-Host "Frontend App: http://localhost:3000" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C in each window to stop services" -ForegroundColor Yellow
Write-Host ""

# Optional: Open browser
$openBrowser = Read-Host "Open frontend in browser? (y/n)"
if ($openBrowser -eq 'y' -or $openBrowser -eq 'Y') {
    Start-Sleep -Seconds 3
    Start-Process "http://localhost:3000"
}
