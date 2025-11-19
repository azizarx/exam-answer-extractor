# ✅ Setup Checklist

Use this checklist to ensure everything is properly configured before running the system.

## 📋 Prerequisites Check

### System Requirements

- [ ] **Python 3.9 or higher** installed
  ```powershell
  python --version
  ```
  Should show: `Python 3.9.x` or higher

- [ ] **Node.js 16 or higher** installed
  ```powershell
  node --version
  ```
  Should show: `v16.x.x` or higher

- [ ] **npm** installed (comes with Node.js)
  ```powershell
  npm --version
  ```
  Should show version number

- [ ] **PostgreSQL 12+** installed and running
  ```powershell
  Get-Service postgresql*
  ```
  Status should be "Running"

- [ ] **Redis** installed and running
  ```powershell
  redis-cli ping
  ```
  Should return: `PONG`

- [ ] **Tesseract OCR** installed
  ```powershell
  tesseract --version
  ```
  Should show version information

- [ ] **Poppler** installed (for PDF processing)
  - Verify `pdftoppm` is in PATH:
  ```powershell
  pdftoppm -v
  ```

### API Keys & Accounts

- [ ] **OpenAI API Key** obtained
  - Get from: https://platform.openai.com/api-keys
  - Note: Requires paid account for GPT-4 Vision

- [ ] **DigitalOcean Spaces** account created
  - Get from: https://cloud.digitalocean.com/spaces
  - [ ] Spaces bucket created (e.g., `exam-answer-sheets`)
  - [ ] Access key generated
  - [ ] Secret key generated

## 🔧 Backend Setup

### Virtual Environment

- [ ] Virtual environment created
  ```powershell
  python -m venv venv
  ```

- [ ] Virtual environment activated
  ```powershell
  .\venv\Scripts\Activate.ps1
  ```
  Prompt should show `(venv)`

- [ ] Dependencies installed
  ```powershell
  pip install -r requirements.txt
  ```

- [ ] Verify installation
  ```powershell
  pip list
  ```
  Should show all required packages

### Configuration Files

- [ ] `.env` file created from template
  ```powershell
  Copy-Item .env.example .env
  ```

- [ ] DigitalOcean Spaces configured in `.env`:
  - [ ] `SPACES_REGION` set (e.g., `sgp1`)
  - [ ] `SPACES_ENDPOINT` set (e.g., `https://sgp1.digitaloceanspaces.com`)
  - [ ] `SPACES_KEY` set (your access key)
  - [ ] `SPACES_SECRET` set (your secret key)
  - [ ] `SPACES_BUCKET` set (your bucket name)

- [ ] OpenAI configured in `.env`:
  - [ ] `OPENAI_API_KEY` set (your API key)
  - [ ] `OPENAI_MODEL` set (default: `gpt-4o`)

- [ ] Database configured in `.env`:
  - [ ] `DATABASE_URL` set
  - Format: `postgresql://username:password@localhost:5432/exam_db`

- [ ] Redis configured in `.env`:
  - [ ] `REDIS_URL` set (default: `redis://localhost:6379/0`)

- [ ] Tesseract path configured in `.env`:
  - [ ] `TESSERACT_CMD` set (default: `C:\\Program Files\\Tesseract-OCR\\tesseract.exe`)

- [ ] Poppler path configured in `.env`:
  - [ ] `POPPLER_PATH` set (default: `C:\\poppler\\Library\\bin`)

### Database Setup

- [ ] PostgreSQL database created
  ```powershell
  psql -U postgres
  CREATE DATABASE exam_db;
  \q
  ```

- [ ] Database tables initialized
  ```powershell
  python -c "from backend.db.database import init_db; init_db()"
  ```

- [ ] Verify tables created
  ```powershell
  psql -U postgres -d exam_db -c "\dt"
  ```
  Should show: `exam_submissions`, `multiple_choice_answers`, `free_response_answers`, `processing_logs`

### Backend Testing

- [ ] Backend starts without errors
  ```powershell
  python main.py
  ```

- [ ] API documentation accessible
  - Open: http://localhost:8000/docs
  - Should see Swagger UI

- [ ] Health check passes
  ```powershell
  curl http://localhost:8000/health
  ```
  Should return: `{"status":"healthy"}`

## 🎨 Frontend Setup

### Dependencies

- [ ] Navigate to frontend directory
  ```powershell
  cd frontend
  ```

- [ ] Node modules installed
  ```powershell
  npm install
  ```

- [ ] Verify installation
  ```powershell
  npm list --depth=0
  ```
  Should show all dependencies

### Configuration

- [ ] Frontend `.env` file created (optional, uses defaults)
  ```powershell
  Copy-Item .env.example .env
  ```

- [ ] Backend API URL configured (if needed)
  - Default: `http://localhost:8000`
  - Set `VITE_API_URL` in `.env` if different

### Frontend Testing

- [ ] Frontend starts without errors
  ```powershell
  npm run dev
  ```

- [ ] Frontend accessible in browser
  - Open: http://localhost:3000
  - Should see upload page

- [ ] No console errors
  - Open browser DevTools (F12)
  - Check Console tab for errors

## 🚀 Full Stack Testing

### Start Services

- [ ] Redis running
  ```powershell
  redis-server
  # Or: Start-Service Redis
  ```

- [ ] PostgreSQL running
  ```powershell
  Get-Service postgresql*
  ```

- [ ] Backend running
  ```powershell
  # Terminal 1
  .\start.ps1
  ```

- [ ] Frontend running
  ```powershell
  # Terminal 2
  cd frontend
  .\start.ps1
  ```

### Functional Tests

- [ ] Upload test PDF via web interface
  - Go to http://localhost:3000
  - Drag and drop a PDF or click "Browse Files"
  - Click "Start Extraction"

- [ ] Verify upload successful
  - Should redirect to tracking page
  - Should see submission ID

- [ ] Track processing status
  - Status should update automatically
  - Should progress: Uploading → Processing → Extracting → Complete

- [ ] View extraction results
  - Results should display when complete
  - Should see multiple choice answers
  - Should see free response answers (if any)

- [ ] Export results
  - Click "Export Results"
  - JSON file should download

- [ ] API endpoints working
  ```powershell
  # Check health
  curl http://localhost:8000/health
  
  # List submissions
  curl http://localhost:8000/submissions
  ```

## 🔍 Troubleshooting Verification

### If Issues Occur

- [ ] Check all services are running:
  - [ ] PostgreSQL service
  - [ ] Redis service
  - [ ] Backend server
  - [ ] Frontend dev server

- [ ] Check environment variables:
  ```powershell
  # Backend
  Get-Content .env
  
  # Frontend
  Get-Content frontend\.env
  ```

- [ ] Check logs:
  - [ ] Backend console output
  - [ ] Frontend console output
  - [ ] Browser DevTools console

- [ ] Verify network access:
  ```powershell
  # Backend API
  curl http://localhost:8000/health
  
  # Frontend
  curl http://localhost:3000
  ```

- [ ] Check database connection:
  ```powershell
  psql -U postgres -d exam_db -c "SELECT COUNT(*) FROM exam_submissions;"
  ```

## ✨ Optional Enhancements

- [ ] Configure Celery for background processing
  ```powershell
  # Terminal 3
  celery -A backend.worker worker --loglevel=info --pool=solo
  ```

- [ ] Set up monitoring
  - [ ] Check Celery Flower (if installed)
  - [ ] Monitor Redis with `redis-cli monitor`
  - [ ] Monitor PostgreSQL with pgAdmin

- [ ] Production deployment
  - [ ] Set up HTTPS
  - [ ] Configure production database
  - [ ] Set up process manager (PM2, systemd)
  - [ ] Configure reverse proxy (Nginx)

## 🎯 Success Criteria

### Minimum Requirements Met

- [x] All prerequisites installed
- [x] Backend starts without errors
- [x] Frontend starts without errors
- [x] Can upload PDF
- [x] Can view extraction results
- [x] Database stores submissions
- [x] Cloud storage receives files

### Optional Features Working

- [ ] Background processing with Celery
- [ ] Real-time status updates
- [ ] PDF preview
- [ ] Export to JSON
- [ ] Multiple submission tracking

## 📚 Next Steps

Once all checks pass:

1. **Start using the system**
   ```powershell
   .\start-fullstack.ps1
   ```

2. **Read the documentation**
   - See `INDEX.md` for all documentation
   - See `GETTING_STARTED.md` for usage guide

3. **Customize as needed**
   - Frontend: Edit `frontend/src/components/`
   - Backend: Edit `backend/` services
   - See `ARCHITECTURE.md` for design details

## 🆘 Still Having Issues?

1. Review `GETTING_STARTED.md` - Common Issues section
2. Check `WINDOWS_SETUP.md` - Troubleshooting section
3. Enable debug logging in `.env`:
   ```env
   LOG_LEVEL=DEBUG
   ```
4. Check API documentation: http://localhost:8000/docs

---

## ✅ Checklist Complete!

If all items are checked, you're ready to use the system! 🎉

Run: `.\start-fullstack.ps1` and start extracting exam answers!
