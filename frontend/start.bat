@echo off
REM Start frontend development server

echo ================================================
echo   Exam Extractor Frontend - Starting...
echo ================================================
echo.

REM Check if node_modules exists
if not exist "node_modules" (
    echo Installing dependencies...
    call npm install
    if errorlevel 1 (
        echo Failed to install dependencies
        pause
        exit /b 1
    )
)

REM Check if .env exists
if not exist ".env" (
    echo Creating .env file...
    copy .env.example .env
    echo .env file created
)

echo.
echo Starting development server...
echo.
echo Frontend will be available at: http://localhost:3000
echo Make sure the backend is running at: http://localhost:8000
echo.
echo Press Ctrl+C to stop the server
echo.

call npm run dev
