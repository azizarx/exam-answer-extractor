# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Exam answer sheet extraction system: upload PDF exam sheets, extract MCQ and free-response answers using AI vision (Google Gemini) and OCR (Tesseract), store results in a database. FastAPI backend + React frontend.

## Commands

### Backend
```bash
python main.py                    # Start FastAPI server on :8000 (auto-reloads in debug)
pip install -r requirements.txt   # Install Python dependencies
python -c "from backend.db.database import init_db; init_db()"  # Create DB tables
```

### Frontend
```bash
cd frontend && npm install        # Install JS dependencies
cd frontend && npm run dev        # Vite dev server on :3000
cd frontend && npm run build      # Production build
cd frontend && npm run lint       # ESLint
```

### Full Stack
```bash
./start-fullstack.ps1             # PowerShell: starts backend + frontend together
```

No test suite exists yet. API can be tested interactively at http://localhost:8000/docs.

## Architecture

### Extraction Pipeline (the core logic)

Two pipeline paths controlled by `Settings.use_optimized_pipeline` (default: True):

1. **Optimized pipeline** (`extraction_pipeline.py` → `optimized_extractor.py`):
   - `RefactoredPipeline` orchestrates multi-stage extraction
   - `PageAnalyzer` does CV-based layout detection (header, answer grid, drawing regions)
   - `LayoutClusterer` groups pages by layout similarity → one LLM call per unique layout
   - `FormatDetector` calls Gemini once per layout to detect exam format
   - `AnswerClassifier` attempts CV-only answer extraction before falling back to LLM
   - `OptimizedAIExtractor` wraps this as a drop-in replacement for the legacy extractor

2. **Legacy pipeline** (`ai_extractor.py`): `AIExtractor` sends every page to Gemini Vision directly

### Request Flow
```
Upload PDF → local_storage.py saves file
           → pdf_to_images.py converts pages to images
           → image_preprocessor.py enhances contrast/clarity
           → extraction_pipeline runs (CV analysis → selective LLM calls)
           → json_generator.py formats structured output
           → Results saved to DB + optional Spaces upload
```

### Key Services (all in `backend/services/`)
- `gemini_client.py` — model selection with ordered fallback list
- `page_analyzer.py` — CV-based page layout detection (`PageLayout`, `BoundingBox`, `DetectedRegion`)
- `ocr_engine.py` — Tesseract OCR wrapper (alternative to AI extraction)
- `ocr_results_writer.py` — saves OCR debug artifacts to `storage/OCRResults/`
- `space_client.py` — optional DigitalOcean Spaces (S3) upload/download
- `local_storage.py` — filesystem storage for uploads and results under `./storage/`
- `NewMcqSolution.py` — optional UZ-specific MCQ grid reader using template matching

### Database
- SQLite by default (`exam_db.sqlite`), supports PostgreSQL via `DATABASE_URL`
- SQLAlchemy ORM with models in `backend/db/models.py`
- Key models: `Exam` → `ExamDocument` → `ExamSubmission` → `CandidateResult` → `AnswerKey` → `GeneratedJSON`
- `CandidateResult.extra_fields` (JSON column) holds dynamic header fields beyond the fixed four

### Frontend
- React 18 + Vite + Tailwind CSS
- Pages: `UploadPage` (drag-and-drop PDF upload), `TrackingPage` (polling status), `ApiDocsPage`
- Shared components in `components/common/` (Button, Card, Badge, Alert, etc.)
- API client in `services/api.js` using Axios, base URL defaults to `http://localhost:8000`

## Configuration

All config via environment variables (`.env` file), managed by `backend/config.py` using pydantic-settings `BaseSettings`. Key settings:
- `GEMINI_API_KEY` — required for AI extraction
- `GEMINI_MODEL` / `GEMINI_FALLBACK_MODELS` — model selection with auto-fallback
- `DATABASE_URL` — defaults to SQLite if empty/unset
- `USE_OPTIMIZED_PIPELINE` — toggle between optimized and legacy extraction
- `ENABLE_IMAGE_PREPROCESSING` / `PREPROCESSING_MODE` — image enhancement before extraction
- `USE_UZ_MCQ_SOLVER` — enable template-based MCQ grid reading for UZ exam sheets
