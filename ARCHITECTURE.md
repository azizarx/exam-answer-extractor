# System Architecture Diagrams

## 1. High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          USER / CLIENT                          │
└────────────────┬────────────────────────────────────────────────┘
                 │
                 │ HTTP/REST
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FASTAPI BACKEND                            │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    API ENDPOINTS                         │  │
│  │  • POST /upload          • GET /status/{id}             │  │
│  │  • GET /submission/{id}  • GET /submissions             │  │
│  │  • DELETE /submission/{id}                              │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────┬───────────────────────────┬────────────────────────┘
             │                           │
             │                           │ Queue Task
             ▼                           ▼
┌────────────────────────┐    ┌──────────────────────────┐
│  DIGITALOCEAN SPACES   │    │    CELERY WORKER         │
│                        │    │  (Background Tasks)      │
│  • Store PDFs          │    │                          │
│  • Store JSON Results  │    │  1. Download PDF         │
│  • Presigned URLs      │    │  2. Convert to Images    │
└────────────────────────┘    │  3. Extract Answers      │
                               │  4. Generate JSON        │
                               │  5. Upload Results       │
                               │  6. Save to DB           │
                               └──────────┬───────────────┘
                                          │
                                          ▼
                               ┌──────────────────────────┐
                               │   POSTGRESQL DATABASE    │
                               │                          │
                               │  • ExamSubmission        │
                               │  • MultipleChoiceAnswer  │
                               │  • FreeResponseAnswer    │
                               │  • ProcessingLog         │
                               └──────────────────────────┘
```

## 2. PDF Processing Pipeline

```
INPUT: PDF File
     │
     ▼
┌─────────────────────┐
│   PDF UPLOAD        │
│   (FastAPI)         │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   SAVE TO SPACES    │
│   (boto3)           │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   CREATE DB RECORD  │
│   Status: pending   │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   QUEUE TASK        │
│   (Celery)          │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   DOWNLOAD PDF      │
│   (from Spaces)     │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   PDF → IMAGES      │
│   (pdf2image)       │
│   • Page 1.png      │
│   • Page 2.png      │
│   • Page N.png      │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────────────────┐
│   EXTRACTION (Choose Method)    │
│                                 │
│   Method A          Method B    │
│   ┌─────────┐    ┌──────────┐  │
│   │  OCR    │    │ AI Vision│  │
│   │(Tesseract)   │ (GPT-4)  │  │
│   └─────────┘    └──────────┘  │
└─────────┬───────────────────────┘
          │
          ▼
┌─────────────────────┐
│   PARSE ANSWERS     │
│   • MCQ: Q1→A       │
│   • MCQ: Q2→C       │
│   • Free: Q1→Text   │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   VALIDATE          │
│   • Check format    │
│   • Check duplicates│
│   • Verify answers  │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   GENERATE JSON     │
│   {                 │
│     "mcq": [...],   │
│     "free": [...]   │
│   }                 │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   UPLOAD JSON       │
│   (to Spaces)       │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   SAVE TO DATABASE  │
│   Status: completed │
└─────────┬───────────┘
          │
          ▼
OUTPUT: Extracted Answers in DB + JSON
```

## 3. Data Flow Diagram

```
┌─────────────┐
│   CLIENT    │
└──────┬──────┘
       │ 1. Upload PDF
       ▼
┌─────────────────────┐
│    API ENDPOINT     │ ──────────┐
│   /api/v1/upload    │           │
└──────┬──────────────┘           │ 2. Store PDF
       │                          ▼
       │ 3. Create Record   ┌──────────────┐
       ▼                    │   SPACES     │
┌─────────────────────┐    │  (S3 API)    │
│    DATABASE         │    └──────────────┘
│  ┌──────────────┐   │           ▲
│  │ Submission   │   │           │ 8. Upload JSON
│  │ Status: pending │ │           │
│  └──────────────┘   │    ┌──────┴────────┐
└─────────┬───────────┘    │               │
          │ 4. Queue       │               │
          ▼                │               │
    ┌──────────┐           │               │
    │  REDIS   │           │               │
    │  Queue   │           │               │
    └─────┬────┘           │               │
          │ 5. Process     │               │
          ▼                │               │
    ┌─────────────┐        │               │
    │   CELERY    │────────┤               │
    │   WORKER    │        │               │
    └─────┬───────┘        │               │
          │ 6. Extract     │               │
          ▼                │               │
    ┌─────────────┐        │               │
    │  AI/OCR     │        │               │
    │  Extractor  │        │               │
    └─────┬───────┘        │               │
          │ 7. Generate    │               │
          ▼                │               │
    ┌─────────────┐        │               │
    │    JSON     │────────┘               │
    │  Generator  │                        │
    └─────┬───────┘                        │
          │ 9. Save Answers                │
          ▼                                │
┌─────────────────────┐                    │
│    DATABASE         │                    │
│  ┌──────────────┐   │                    │
│  │ Submission   │   │                    │
│  │ Status: done │   │                    │
│  ├──────────────┤   │                    │
│  │ MCQ Answers  │   │                    │
│  ├──────────────┤   │                    │
│  │ Free Response│   │                    │
│  └──────────────┘   │                    │
└──────────┬──────────┘                    │
           │ 10. Retrieve                  │
           ▼                               │
      ┌─────────┐                          │
      │ CLIENT  │◄─────────────────────────┘
      │ Results │
      └─────────┘
```

## 4. Service Layer Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    BACKEND SERVICES                     │
│                                                         │
│  ┌────────────────────┐    ┌─────────────────────┐    │
│  │  SpacesClient      │    │  PDFConverter       │    │
│  │                    │    │                     │    │
│  │  • upload_pdf()    │    │  • convert_from_    │    │
│  │  • upload_json()   │    │    file()           │    │
│  │  • download_pdf()  │    │  • convert_from_    │    │
│  │  • download_json() │    │    bytes()          │    │
│  │  • list_files()    │    │  • get_page_count() │    │
│  │  • delete_file()   │    │                     │    │
│  └────────────────────┘    └─────────────────────┘    │
│                                                         │
│  ┌────────────────────┐    ┌─────────────────────┐    │
│  │  OCREngine         │    │  AIExtractor        │    │
│  │                    │    │                     │    │
│  │  • extract_text()  │    │  • extract_from_    │    │
│  │  • extract_from_   │    │    image()          │    │
│  │    multiple()      │    │  • extract_from_    │    │
│  │  • extract_with_   │    │    multiple()       │    │
│  │    confidence()    │    │  • validate_        │    │
│  │                    │    │    extraction()     │    │
│  └────────────────────┘    └─────────────────────┘    │
│                                                         │
│  ┌────────────────────┐    ┌─────────────────────┐    │
│  │  AnswerParser      │    │  JSONGenerator      │    │
│  │                    │    │                     │    │
│  │  • extract_mcq()   │    │  • generate()       │    │
│  │  • extract_free_   │    │  • generate_with_   │    │
│  │    response()      │    │    validation()     │    │
│  │  • extract_all()   │    │  • format_for_      │    │
│  │                    │    │    display()        │    │
│  └────────────────────┘    └─────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

## 5. Database Schema

```
┌────────────────────────────────────────┐
│         exam_submissions               │
├────────────────────────────────────────┤
│ id                  INTEGER PK         │
│ filename            VARCHAR(255)       │
│ original_pdf_key    VARCHAR(500)       │
│ result_json_key     VARCHAR(500)       │
│ status              VARCHAR(50)        │  ← pending/processing/
│ created_at          TIMESTAMP          │    completed/failed
│ processed_at        TIMESTAMP          │
│ pages_count         INTEGER            │
│ error_message       TEXT               │
└──────────┬─────────────────────────────┘
           │
           │ One-to-Many
           │
     ┌─────┴──────┬───────────────────┐
     │            │                   │
     ▼            ▼                   ▼
┌─────────────┐ ┌──────────────┐ ┌───────────────┐
│multiple_    │ │free_response_│ │processing_    │
│choice_      │ │answers       │ │logs           │
│answers      │ │              │ │               │
├─────────────┤ ├──────────────┤ ├───────────────┤
│id        PK │ │id         PK │ │id          PK │
│submission_id│ │submission_id │ │submission_id  │
│  FK         │ │  FK          │ │  FK           │
│question_    │ │question_     │ │action         │
│  number     │ │  number      │ │status         │
│selected_    │ │response_text │ │message        │
│  answer     │ │word_count    │ │metadata       │
│created_at   │ │created_at    │ │created_at     │
└─────────────┘ └──────────────┘ └───────────────┘
```

## 6. API Request/Response Flow

```
CLIENT REQUEST
     │
     │ POST /api/v1/upload
     │ Content-Type: multipart/form-data
     │ Body: [PDF file]
     │
     ▼
┌────────────────────────┐
│  FastAPI Endpoint      │
│  @router.post("/upload")│
└──────────┬─────────────┘
           │
           │ Validate file type
           │
           ▼
┌────────────────────────┐
│  SpacesClient          │
│  upload_pdf()          │
└──────────┬─────────────┘
           │
           │ Store in cloud
           │
           ▼
┌────────────────────────┐
│  Database              │
│  Create ExamSubmission │
└──────────┬─────────────┘
           │
           │ Queue background task
           │
           ▼
┌────────────────────────┐
│  Return Response       │
│  {                     │
│    submission_id: 123, │
│    status: "success",  │
│    message: "..."      │
│  }                     │
└────────────────────────┘
```

## 7. Technology Stack Layers

```
┌─────────────────────────────────────────┐
│           PRESENTATION LAYER            │
│  ┌───────────────────────────────────┐  │
│  │  FastAPI Auto-Generated Docs      │  │
│  │  (Swagger UI / ReDoc)             │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
                   ▲
                   │
┌─────────────────────────────────────────┐
│           APPLICATION LAYER             │
│  ┌───────────────────────────────────┐  │
│  │  FastAPI REST API                 │  │
│  │  Pydantic Validation              │  │
│  │  Background Tasks (Celery)        │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
                   ▲
                   │
┌─────────────────────────────────────────┐
│            BUSINESS LAYER               │
│  ┌───────────────────────────────────┐  │
│  │  Service Layer                    │  │
│  │  • SpacesClient                   │  │
│  │  • PDFConverter                   │  │
│  │  • OCREngine                      │  │
│  │  • AIExtractor                    │  │
│  │  • JSONGenerator                  │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
                   ▲
                   │
┌─────────────────────────────────────────┐
│           DATA ACCESS LAYER             │
│  ┌───────────────────────────────────┐  │
│  │  SQLAlchemy ORM                   │  │
│  │  Database Models                  │  │
│  │  Session Management               │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
                   ▲
                   │
┌─────────────────────────────────────────┐
│          INFRASTRUCTURE LAYER           │
│  ┌───────────────┬───────────────────┐  │
│  │ PostgreSQL    │ DigitalOcean      │  │
│  │ Database      │ Spaces            │  │
│  ├───────────────┼───────────────────┤  │
│  │ Redis         │ OpenAI API        │  │
│  │ Message Queue │ (GPT-4 Vision)    │  │
│  └───────────────┴───────────────────┘  │
└─────────────────────────────────────────┘
```

## 8. Deployment Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    LOAD BALANCER                        │
│                   (nginx / HAProxy)                     │
└────────────┬──────────────────────┬─────────────────────┘
             │                      │
       ┌─────┴─────┐         ┌─────┴─────┐
       │           │         │           │
       ▼           ▼         ▼           ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ FastAPI  │ │ FastAPI  │ │ Celery   │ │ Celery   │
│ Instance │ │ Instance │ │ Worker   │ │ Worker   │
│    1     │ │    2     │ │    1     │ │    2     │
└────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
     │            │             │            │
     └────────────┴─────────────┴────────────┘
                  │
         ┌────────┴────────┐
         │                 │
         ▼                 ▼
  ┌──────────────┐  ┌──────────────┐
  │ PostgreSQL   │  │    Redis     │
  │  (Primary)   │  │   Message    │
  │              │  │    Broker    │
  └──────────────┘  └──────────────┘
         │
         ▼
  ┌──────────────┐
  │ PostgreSQL   │
  │  (Replica)   │
  │  Read-Only   │
  └──────────────┘

         ▲
         │
         │ All instances connect
         │
         ▼
  ┌──────────────┐
  │ DigitalOcean │
  │   Spaces     │
  │  (S3 API)    │
  └──────────────┘
```

---

**Legend:**
- `│` `─` `┌` `┐` `└` `┘` `├` `┤` `┬` `┴` : Box drawing
- `▼` : Data flow direction
- `◄` : Response flow
- PK : Primary Key
- FK : Foreign Key

---

These diagrams provide a complete visual understanding of the system architecture, data flow, and component interactions.
