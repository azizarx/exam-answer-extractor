# Exam Answer Sheet Extraction System

A complete system for extracting answers from PDF exam sheets using AI-powered OCR and computer vision. The system automatically processes uploaded PDFs, extracts both multiple-choice and free-response answers, and stores them in a database with JSON backups in cloud storage.

## 🎯 Features

- **PDF Upload & Storage**: Upload exam PDFs to DigitalOcean Spaces (S3-compatible)
- **AI-Powered Extraction**: Uses OpenAI GPT-4 Vision API for accurate answer extraction
- **Dual Extraction Methods**: 
  - Traditional OCR with Tesseract (fallback/alternative)
  - AI Vision API (primary, more accurate)
- **Structured Data Output**: JSON format with validation
- **Database Storage**: PostgreSQL with SQLAlchemy ORM
- **Async Processing**: Background task queue with Celery
- **RESTful API**: FastAPI with automatic documentation
- **Complete Audit Trail**: Processing logs for every operation

## 🏗️ Architecture

```
User → Upload PDF → DigitalOcean Spaces (Storage)
            ↓
       FastAPI Backend
            ↓
     Celery Task Queue
            ↓
AI Extractor Service (GPT-4 Vision / OCR)
            ↓
Generate JSON → Save to Spaces → DB Insert
```

## 📋 Tech Stack

| Component | Technology |
|-----------|-----------|
| **Backend API** | FastAPI (Python 3.9+) |
| **AI Extraction** | OpenAI GPT-4 Vision API |
| **OCR (Alternative)** | Tesseract OCR + pytesseract |
| **PDF Processing** | pdf2image + Pillow |
| **Storage** | DigitalOcean Spaces (S3-compatible) |
| **Database** | PostgreSQL 12+ |
| **Task Queue** | Celery + Redis |
| **ORM** | SQLAlchemy |

## 🚀 Quick Start

### Prerequisites

1. **Python 3.9+** installed
2. **PostgreSQL** database running
3. **Redis** server (for Celery)
4. **Tesseract OCR** installed:
   - Windows: Download from [GitHub](https://github.com/UB-Mannheim/tesseract/wiki)
   - Linux: `sudo apt-get install tesseract-ocr`
   - macOS: `brew install tesseract`
5. **Poppler** (for pdf2image):
   - Windows: Download from [Poppler Windows](https://github.com/oschwartz10612/poppler-windows/releases)
   - Linux: `sudo apt-get install poppler-utils`
   - macOS: `brew install poppler`
6. **DigitalOcean Spaces** account with bucket created
7. **OpenAI API** key

### Installation

1. **Clone or navigate to the project directory:**

```powershell
cd c:\Users\azizn\OneDrive\Desktop\Project1
```

2. **Create a virtual environment:**

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

3. **Install dependencies:**

```powershell
pip install -r requirements.txt
```

4. **Configure environment variables:**

Copy `.env.example` to `.env` and fill in your credentials:

```powershell
Copy-Item .env.example .env
notepad .env
```

Required configuration:
```env
# DigitalOcean Spaces
SPACES_REGION=sgp1
SPACES_ENDPOINT=https://sgp1.digitaloceanspaces.com
SPACES_KEY=your_actual_spaces_key
SPACES_SECRET=your_actual_spaces_secret
SPACES_BUCKET=exam-answer-sheets

# OpenAI
OPENAI_API_KEY=your_actual_openai_key
OPENAI_MODEL=gpt-4o

# Database
DATABASE_URL=postgresql://username:password@localhost:5432/exam_db

# Redis
REDIS_URL=redis://localhost:6379/0
```

5. **Create the database:**

```powershell
# Using PostgreSQL CLI
psql -U postgres
CREATE DATABASE exam_db;
\q
```

6. **Initialize database tables:**

```powershell
python -c "from backend.db.database import init_db; init_db()"
```

### Running the Application

#### Option 1: Full Stack (Backend + Frontend) ⭐ RECOMMENDED

Start everything with one command:

```powershell
.\start-fullstack.ps1
```

This will start:
- Backend API at http://localhost:8000
- Frontend App at http://localhost:3000

**Then open your browser to http://localhost:3000**

#### Option 2: Backend Only (API Development)

Start the FastAPI server:

```powershell
python main.py
```

The API will be available at `http://localhost:8000`

- **API Documentation**: http://localhost:8000/docs
- **Alternative Docs**: http://localhost:8000/redoc

#### Option 3: Production Mode (with Celery)

**Terminal 1 - Start Redis** (if not running as service):

```powershell
redis-server
```

**Terminal 2 - Start Celery Worker:**

```powershell
celery -A backend.worker worker --loglevel=info -Q exam_processing --pool=solo
```

Note: Use `--pool=solo` on Windows, or `--pool=prefork` on Linux/macOS

**Terminal 3 - Start FastAPI:**

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 4 - Start Frontend (Optional):**

```powershell
cd frontend
npm run dev
```

## 🌐 Frontend Web Interface

### Quick Start

The easiest way to use the system is through the web interface:

1. **Start the full stack:**
```powershell
.\start-fullstack.ps1
```

2. **Open your browser:**
```
http://localhost:3000
```

3. **Upload a PDF:**
   - Drag and drop your exam answer sheet
   - Or click "Browse Files"
   - Click "Start Extraction"

4. **Track Progress:**
   - Automatically redirected to tracking page
   - Real-time status updates
   - Results appear when complete

5. **View & Export Results:**
   - See all extracted answers
   - Export as JSON
   - Beautiful, responsive UI

### Frontend Features

✨ **Beautiful UI** - Modern design with Tailwind CSS  
🎯 **Drag & Drop** - Intuitive file upload  
⚡ **Real-time Updates** - Live processing status  
📱 **Responsive** - Works on all devices  
💾 **Export** - Download results as JSON  
🎨 **Component-based** - Easy to maintain  

See `frontend/README.md` for more details.

---

## 📖 API Usage (Programmatic)

### 1. Upload PDF for Processing

```bash
POST /api/v1/upload
Content-Type: multipart/form-data

file: [PDF file]
```

**Example with curl:**

```bash
curl -X POST "http://localhost:8000/api/v1/upload" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@exam_answer_sheet.pdf"
```

**Response:**
```json
{
  "status": "success",
  "message": "PDF uploaded successfully. Processing started.",
  "submission_id": 1,
  "filename": "exam_answer_sheet.pdf",
  "spaces_key": "pdfs/exam_answer_sheet.pdf"
}
```

### 2. Check Processing Status

```bash
GET /api/v1/status/{submission_id}
```

**Example:**

```bash
curl -X GET "http://localhost:8000/api/v1/status/1"
```

**Response:**
```json
{
  "submission_id": 1,
  "filename": "exam_answer_sheet.pdf",
  "status": "completed",
  "created_at": "2025-11-17T10:30:00",
  "processed_at": "2025-11-17T10:31:45",
  "pages_count": 3,
  "mcq_count": 25,
  "free_response_count": 3,
  "error_message": null
}
```

Status values:
- `pending` - Upload complete, waiting for processing
- `processing` - Currently extracting answers
- `completed` - Extraction complete, data saved
- `failed` - Extraction failed (see error_message)

### 3. Get Extracted Answers

```bash
GET /api/v1/submission/{submission_id}
```

**Example:**

```bash
curl -X GET "http://localhost:8000/api/v1/submission/1"
```

**Response:**
```json
{
  "submission_id": 1,
  "filename": "exam_answer_sheet.pdf",
  "status": "completed",
  "created_at": "2025-11-17T10:30:00",
  "processed_at": "2025-11-17T10:31:45",
  "multiple_choice": [
    {"question": 1, "answer": "A"},
    {"question": 2, "answer": "C"},
    {"question": 3, "answer": "B"}
  ],
  "free_response": [
    {
      "question": 1,
      "response": "Photosynthesis is the process by which plants convert light energy..."
    },
    {
      "question": 2,
      "response": "The main causes of World War I included..."
    }
  ]
}
```

### 4. List All Submissions

```bash
GET /api/v1/submissions?skip=0&limit=100&status=completed
```

### 5. Delete Submission

```bash
DELETE /api/v1/submission/{submission_id}
```

## 🗂️ Project Structure

```
Project1/
├── backend/
│   ├── __init__.py
│   ├── config.py                 # Configuration management
│   ├── worker.py                 # Celery background tasks
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py            # API endpoints
│   │   └── schemas.py           # Pydantic models
│   ├── services/
│   │   ├── __init__.py
│   │   ├── space_client.py      # DigitalOcean Spaces client
│   │   ├── pdf_to_images.py     # PDF conversion
│   │   ├── ocr_engine.py        # Tesseract OCR
│   │   ├── ai_extractor.py      # OpenAI Vision API
│   │   └── json_generator.py    # JSON formatting
│   └── db/
│       ├── __init__.py
│       ├── database.py          # DB connection
│       └── models.py            # SQLAlchemy models
├── main.py                      # FastAPI application
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment template
├── .env                         # Your configuration (create this)
└── README.md                    # This file
```

## 🔧 Configuration Details

### Database Models

**ExamSubmission**: Tracks each PDF submission
- Filename, upload timestamp, processing status
- Links to Spaces storage keys
- Processing metadata

**MultipleChoiceAnswer**: Stores MCQ answers
- Question number and selected option (A-E)

**FreeResponseAnswer**: Stores written answers
- Question number and response text
- Automatic word count

**ProcessingLog**: Audit trail
- All processing actions and outcomes

### Service Architecture

#### 1. **SpacesClient** (`space_client.py`)
- Upload/download PDFs and JSON files
- S3-compatible API using boto3
- Presigned URL generation for secure access

#### 2. **PDFConverter** (`pdf_to_images.py`)
- Converts PDF pages to high-resolution images
- Configurable DPI (default: 300)
- Batch processing support

#### 3. **OCREngine** (`ocr_engine.py`)
- Tesseract-based text extraction
- Confidence score tracking
- Pattern-based answer parsing

#### 4. **AIExtractor** (`ai_extractor.py`)
- OpenAI GPT-4 Vision API integration
- Structured JSON extraction with prompts
- Multi-page document support
- Built-in validation

#### 5. **JSONGenerator** (`json_generator.py`)
- Formats extracted data into standardized JSON
- Adds metadata and timestamps
- Validation results inclusion

## 🧪 Testing

### Test with Sample PDF

1. Create a simple test PDF with answers
2. Upload using the API
3. Monitor processing in logs
4. Retrieve results

### API Testing with Swagger

Visit `http://localhost:8000/docs` for interactive API testing.

## 🐛 Troubleshooting

### Common Issues

**1. Import errors for packages**
- Solution: Ensure virtual environment is activated and all packages installed
```powershell
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**2. Tesseract not found**
- Solution: Install Tesseract and add to PATH, or set in code:
```python
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
```

**3. Poppler not found**
- Solution: Download Poppler and add `bin/` folder to PATH

**4. Database connection error**
- Solution: Verify PostgreSQL is running and DATABASE_URL is correct

**5. Redis connection error**
- Solution: Start Redis server or update REDIS_URL

**6. OpenAI API errors**
- Solution: Check API key validity and account credits

## 🔐 Security Considerations

**Production Deployment:**

1. **Environment Variables**: Never commit `.env` file
2. **API Keys**: Rotate regularly, use key management service
3. **CORS**: Configure `allow_origins` appropriately in `main.py`
4. **Database**: Use strong passwords, enable SSL
5. **File Upload**: Add virus scanning, size limits
6. **Authentication**: Add JWT or OAuth2 for API access
7. **Rate Limiting**: Implement to prevent abuse

## 📊 Monitoring & Logging

All operations are logged with timestamps and details:

- **Application logs**: Console output with configurable level
- **Database logs**: ProcessingLog table tracks all actions
- **Error tracking**: Failures captured with full stack traces

## 🚀 Deployment

### Docker Deployment (Recommended)

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Cloud Deployment

- **AWS**: Deploy on EC2 or ECS with RDS PostgreSQL
- **DigitalOcean**: Use App Platform or Droplets
- **Heroku**: Use with Heroku Postgres add-on
- **Azure**: Deploy to App Service with Azure Database

## 📝 License

This project is provided as-is for educational and commercial use.

## 🤝 Contributing

Contributions welcome! Areas for improvement:

- Frontend UI for file upload
- Batch processing of multiple PDFs
- Answer key comparison and grading
- Export to Excel/CSV
- Student identification from PDFs
- Handwriting recognition improvements

## 📧 Support

For issues or questions:
- Check logs in console output
- Review API documentation at `/docs`
- Verify all environment variables are set
- Ensure external services (DB, Redis, Spaces) are accessible

---

**Built with ❤️ using FastAPI, OpenAI, and modern Python tools**
