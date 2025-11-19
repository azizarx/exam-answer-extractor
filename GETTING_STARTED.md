# 🚀 Getting Started - Quick Guide

This guide will get you up and running with the Exam Answer Sheet Extraction System in minutes.

## 📝 What This System Does

Uploads PDF exam answer sheets → AI extracts answers → Saves to database + cloud storage → Beautiful web interface to view results

## ⚡ Fastest Way to Start

### 1️⃣ One-Time Setup (5 minutes)

**Step 1: Install Prerequisites**

You need these installed on your computer:
- ✅ Python 3.9+ ([Download](https://www.python.org/downloads/))
- ✅ Node.js 16+ ([Download](https://nodejs.org/))
- ✅ PostgreSQL 12+ ([Download](https://www.postgresql.org/download/))
- ✅ Redis ([Download for Windows](https://github.com/microsoftarchive/redis/releases))
- ✅ Tesseract OCR ([Download for Windows](https://github.com/UB-Mannheim/tesseract/wiki))
- ✅ Poppler ([Download for Windows](https://github.com/oschwartz10612/poppler-windows/releases))

**Step 2: Get API Keys**

- OpenAI API Key: https://platform.openai.com/api-keys
- DigitalOcean Spaces: https://cloud.digitalocean.com/spaces

**Step 3: Setup Backend**

```powershell
# Navigate to project
cd c:\Users\azizn\OneDrive\Desktop\Project1

# Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Configure environment
Copy-Item .env.example .env
notepad .env  # Add your API keys and credentials

# Initialize database
python -c "from backend.db.database import init_db; init_db()"
```

**Step 4: Setup Frontend**

```powershell
# Install frontend dependencies
cd frontend
npm install
cd ..
```

### 2️⃣ Daily Usage (10 seconds)

**Start Everything:**

```powershell
.\start-fullstack.ps1
```

**Open Browser:**

Go to http://localhost:3000

That's it! 🎉

---

## 🎯 Using the System

### Web Interface (Easiest)

1. **Upload PDF**
   - Drag and drop your exam answer sheet PDF
   - Or click "Browse Files"
   - PDF is validated automatically

2. **Start Extraction**
   - Click "Start Extraction" button
   - Automatically redirected to tracking page

3. **Track Progress**
   - Real-time status updates
   - Shows: Uploading → Processing → Extracting → Complete

4. **View Results**
   - Multiple choice answers displayed in grid
   - Free response answers in cards
   - Export results as JSON

### API Interface (For Developers)

**Upload PDF:**
```bash
curl -X POST "http://localhost:8000/upload" \
  -F "file=@exam.pdf" \
  -F "student_id=12345" \
  -F "exam_id=MATH101"
```

**Check Status:**
```bash
curl "http://localhost:8000/status/{submission_id}"
```

**Get Results:**
```bash
curl "http://localhost:8000/submission/{submission_id}"
```

See http://localhost:8000/docs for full API documentation.

---

## 📂 Project Structure

```
Project1/
├── backend/                    # Backend API
│   ├── api/                   # REST endpoints
│   ├── services/              # Business logic
│   │   ├── ai_extractor.py   # OpenAI GPT-4 Vision
│   │   ├── ocr_engine.py     # Tesseract OCR
│   │   ├── pdf_to_images.py  # PDF processing
│   │   ├── json_generator.py # JSON formatting
│   │   └── space_client.py   # Cloud storage
│   └── db/                    # Database models
├── frontend/                   # React web app
│   ├── src/
│   │   ├── components/        # Reusable UI
│   │   ├── pages/             # Pages
│   │   └── services/          # API client
│   └── public/
├── main.py                    # Backend entry point
├── start-fullstack.ps1        # Start everything
└── README.md                  # Full documentation
```

---

## 🎨 Features Overview

### Backend
- ✅ RESTful API with FastAPI
- ✅ AI-powered extraction (GPT-4 Vision)
- ✅ Fallback OCR (Tesseract)
- ✅ Cloud storage (DigitalOcean Spaces)
- ✅ PostgreSQL database
- ✅ Background processing (Celery)
- ✅ Automatic API documentation

### Frontend
- ✅ Modern React UI
- ✅ Drag & drop upload
- ✅ Real-time status tracking
- ✅ Beautiful results display
- ✅ JSON export
- ✅ Fully responsive
- ✅ Component-based architecture

---

## 🔧 Common Issues & Solutions

### "Module not found" errors
```powershell
# Backend
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Frontend
cd frontend
npm install
```

### Database connection errors
```powershell
# Check PostgreSQL is running
Get-Service postgresql*

# Test connection
psql -U postgres -d exam_db
```

### Redis connection errors
```powershell
# Start Redis
redis-server

# Or on Windows with Redis as a service
Start-Service Redis
```

### Port already in use
```powershell
# Backend (port 8000)
netstat -ano | findstr :8000
taskkill /PID <PID> /F

# Frontend (port 3000)
netstat -ano | findstr :3000
taskkill /PID <PID> /F
```

### Tesseract not found
- Add Tesseract to PATH: `C:\Program Files\Tesseract-OCR`
- Restart PowerShell after adding to PATH

### Poppler not found
- Add Poppler bin folder to PATH: `C:\path\to\poppler\bin`
- Restart PowerShell after adding to PATH

---

## 📚 Next Steps

- **Read Full Documentation**: See `README.md` for complete details
- **API Reference**: Visit http://localhost:8000/docs when backend is running
- **Frontend Guide**: See `frontend/README.md` for component details
- **Architecture**: See `ARCHITECTURE.md` for system design
- **Windows Setup**: See `WINDOWS_SETUP.md` for detailed Windows instructions

---

## 🆘 Getting Help

### Check Status Endpoints

**Backend Health:**
```powershell
curl http://localhost:8000/health
```

**Frontend:**
Visit http://localhost:3000 (should see upload page)

### Enable Debug Logging

Edit `.env`:
```env
LOG_LEVEL=DEBUG
```

Restart services to see detailed logs.

### Still Stuck?

1. Check `QUICKSTART.md` for step-by-step guide
2. Review `WINDOWS_SETUP.md` for Windows-specific help
3. Check API docs at http://localhost:8000/docs
4. Review logs in console output

---

## 🎯 Quick Command Reference

```powershell
# Start everything
.\start-fullstack.ps1

# Start backend only
.\start.ps1

# Start frontend only
cd frontend
.\start.ps1

# Stop everything
# Close terminal windows or press Ctrl+C

# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Run tests
pytest tests/

# View API docs
# Open http://localhost:8000/docs
```

---

## 🚀 You're Ready!

Run `.\start-fullstack.ps1` and open http://localhost:3000 to start using the system!
