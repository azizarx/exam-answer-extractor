# Windows Setup Guide

## Prerequisites Installation

### 1. Python 3.9+
Download from https://www.python.org/downloads/

### 2. PostgreSQL
1. Download from https://www.postgresql.org/download/windows/
2. Install with default settings
3. Remember your password for the postgres user
4. Open pgAdmin 4 and create database:
   ```sql
   CREATE DATABASE exam_db;
   ```

### 3. Redis
1. Download Redis for Windows from:
   https://github.com/microsoftarchive/redis/releases
2. Or use Docker: `docker run -d -p 6379:6379 redis`
3. Or use Memurai (Redis alternative for Windows):
   https://www.memurai.com/get-memurai

### 4. Tesseract OCR
1. Download installer from:
   https://github.com/UB-Mannheim/tesseract/wiki
2. Install to default location: `C:\Program Files\Tesseract-OCR`
3. Add to PATH:
   - Open System Properties → Environment Variables
   - Add `C:\Program Files\Tesseract-OCR` to PATH

### 5. Poppler (for PDF processing)
1. Download from:
   https://github.com/oschwartz10612/poppler-windows/releases
2. Extract to `C:\poppler`
3. Add `C:\poppler\Library\bin` to PATH

### 6. Visual C++ Redistributable
Some packages require this:
https://aka.ms/vs/17/release/vc_redist.x64.exe

## Quick Setup

Open PowerShell in project directory:

```powershell
# 1. Create virtual environment
python -m venv venv

# 2. Activate virtual environment
.\venv\Scripts\Activate.ps1

# If you get execution policy error, run:
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy environment file
Copy-Item .env.example .env

# 5. Edit .env with your credentials
notepad .env

# 6. Initialize database
python init_db.py init

# 7. Run the application
python main.py
```

## Running with Celery (Background Processing)

Open 3 separate PowerShell windows:

**Window 1 - Redis:**
```powershell
redis-server
# Or if using Docker:
# docker run -d -p 6379:6379 redis
```

**Window 2 - Celery Worker:**
```powershell
.\venv\Scripts\Activate.ps1
celery -A backend.worker worker --loglevel=info -Q exam_processing --pool=solo
```

**Window 3 - FastAPI:**
```powershell
.\venv\Scripts\Activate.ps1
python main.py
```

## Testing

```powershell
# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Run tests
python test_api.py
```

## Common Windows Issues

### Issue: "python not recognized"
**Solution:** Add Python to PATH or use full path:
```powershell
C:\Users\YourName\AppData\Local\Programs\Python\Python311\python.exe
```

### Issue: "Tesseract not found"
**Solution:** Manually set path in code or add to environment:
```powershell
$env:PATH += ";C:\Program Files\Tesseract-OCR"
```

### Issue: "poppler not found"
**Solution:** Add poppler bin to PATH:
```powershell
$env:PATH += ";C:\poppler\Library\bin"
```

### Issue: PowerShell execution policy
**Solution:**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Issue: Long PATH issue with venv
**Solution:** Create venv closer to root:
```powershell
cd C:\
mkdir projects
cd projects
# Then create project here
```

## Firewall Configuration

If you need to access the API from other machines:

1. Open Windows Defender Firewall
2. Click "Advanced settings"
3. Create new Inbound Rule
4. Allow TCP port 8000
5. Allow from specific IPs or all

## Production Deployment on Windows Server

### Using IIS with FastAPI

1. Install IIS with CGI support
2. Install HttpPlatformHandler module
3. Configure web.config:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>
    <handlers>
      <add name="httpPlatformHandler" path="*" verb="*" 
           modules="httpPlatformHandler" resourceType="Unspecified"/>
    </handlers>
    <httpPlatform processPath="C:\path\to\venv\Scripts\python.exe"
                  arguments="C:\path\to\main.py"
                  startupTimeLimit="60">
      <environmentVariables>
        <environmentVariable name="PORT" value="%HTTP_PLATFORM_PORT%" />
      </environmentVariables>
    </httpPlatform>
  </system.webServer>
</configuration>
```

### Using Windows Service

Install as Windows service using NSSM:
```powershell
# Download NSSM from https://nssm.cc/download
nssm install ExamAPI "C:\path\to\venv\Scripts\python.exe" "C:\path\to\main.py"
nssm start ExamAPI
```

## Resource Requirements

**Minimum:**
- CPU: 2 cores
- RAM: 4 GB
- Disk: 10 GB
- Network: Stable internet for OpenAI API

**Recommended:**
- CPU: 4+ cores
- RAM: 8 GB
- Disk: 50 GB (for PDF storage)
- Network: 100 Mbps+

## Monitoring

Install Windows Performance Monitor counters for:
- CPU usage
- Memory usage
- Network I/O
- Disk I/O

Or use third-party tools:
- Prometheus + Grafana
- New Relic
- DataDog

---

For support, check logs in console output or contact your system administrator.
