# 🚀 Quick Start Guide

Get your exam extraction system running in 5 minutes!

## Prerequisites Checklist

Before starting, ensure you have:

- [ ] Python 3.9 or higher
- [ ] PostgreSQL database
- [ ] Redis server
- [ ] Tesseract OCR installed
- [ ] Poppler utils installed
- [ ] DigitalOcean Spaces account
- [ ] OpenAI API key

**Need help installing?** See [WINDOWS_SETUP.md](WINDOWS_SETUP.md) for detailed Windows instructions.

## Installation (5 Steps)

### Step 1: Create Virtual Environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### Step 2: Install Dependencies

```powershell
pip install -r requirements.txt
```

### Step 3: Configure Environment

```powershell
Copy-Item .env.example .env
notepad .env
```

**Update these values in `.env`:**
```env
SPACES_KEY=your_digitalocean_spaces_key
SPACES_SECRET=your_digitalocean_spaces_secret
OPENAI_API_KEY=your_openai_api_key
DATABASE_URL=postgresql://username:password@localhost:5432/exam_db
```

### Step 4: Initialize Database

```powershell
# Create database in PostgreSQL
# Then run:
python init_db.py init
```

### Step 5: Start the Server

**Option A - Simple (Development):**
```powershell
python main.py
```

**Option B - Full System (Recommended):**
```powershell
.\start.ps1
```

This will automatically start:
- Redis server
- Celery worker
- FastAPI server

## First Test

Once the server is running:

1. **Open API docs**: http://localhost:8000/docs

2. **Upload a test PDF**:
   - Click on `POST /api/v1/upload`
   - Click "Try it out"
   - Choose your PDF file
   - Click "Execute"

3. **Check status** using the `submission_id` from response:
   - Use `GET /api/v1/status/{submission_id}`

4. **Get results** when status is "completed":
   - Use `GET /api/v1/submission/{submission_id}`

## Quick Test with Script

```powershell
# Make sure server is running first
python test_api.py
```

## Troubleshooting

### "Module not found" errors
```powershell
# Make sure virtual environment is activated
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### "Tesseract not found"
```powershell
# Add Tesseract to PATH
$env:PATH += ";C:\Program Files\Tesseract-OCR"
```

### "Database connection failed"
```powershell
# Check if PostgreSQL is running
# Verify DATABASE_URL in .env
```

### "Redis connection failed"
```powershell
# Start Redis
redis-server
# Or use Docker:
docker run -d -p 6379:6379 redis
```

## What's Next?

- 📖 Read the full [README.md](README.md) for detailed documentation
- 💻 Check [examples.py](examples.py) for programmatic usage
- 🔧 See [WINDOWS_SETUP.md](WINDOWS_SETUP.md) for Windows-specific setup
- 🌐 Visit http://localhost:8000/docs for interactive API documentation

## Project Structure Overview

```
Project1/
├── main.py                  # ⭐ Start here - FastAPI app
├── start.ps1               # 🚀 One-click startup script
├── test_api.py             # 🧪 Test your installation
├── examples.py             # 📚 Usage examples
├── backend/
│   ├── api/                # 🌐 REST API routes
│   ├── services/           # 🛠️ Core extraction logic
│   └── db/                 # 💾 Database models
└── README.md               # 📖 Full documentation
```

## API Endpoints Summary

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/upload` | POST | Upload PDF for processing |
| `/api/v1/status/{id}` | GET | Check processing status |
| `/api/v1/submission/{id}` | GET | Get extracted answers |
| `/api/v1/submissions` | GET | List all submissions |
| `/api/v1/submission/{id}` | DELETE | Delete a submission |

## Support

If you encounter issues:

1. Check the console logs
2. Visit http://localhost:8000/health
3. Review [README.md](README.md) troubleshooting section
4. Check [WINDOWS_SETUP.md](WINDOWS_SETUP.md) for Windows-specific help

---

**Ready to extract some exam answers? Start the server and visit http://localhost:8000/docs!** 🎉
