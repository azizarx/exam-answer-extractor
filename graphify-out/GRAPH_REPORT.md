# Graph Report - .  (2026-05-13)

## Corpus Check
- Corpus is ~37,943 words - fits in a single context window. You may not need a graph.

## Summary
- 515 nodes · 794 edges · 52 communities (31 shown, 21 thin omitted)
- Extraction: 80% EXTRACTED · 20% INFERRED · 0% AMBIGUOUS · INFERRED: 156 edges (avg confidence: 0.7)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_API Route Handlers|API Route Handlers]]
- [[_COMMUNITY_Submission & Answer Key Queries|Submission & Answer Key Queries]]
- [[_COMMUNITY_Image Preprocessing|Image Preprocessing]]
- [[_COMMUNITY_Frontend UI Components|Frontend UI Components]]
- [[_COMMUNITY_Database Models & Exam Creation|Database Models & Exam Creation]]
- [[_COMMUNITY_OCR Engine|OCR Engine]]
- [[_COMMUNITY_Page Layout Analysis|Page Layout Analysis]]
- [[_COMMUNITY_Spaces Client Integration|Spaces Client Integration]]
- [[_COMMUNITY_Database Layer|Database Layer]]
- [[_COMMUNITY_Local Storage Service|Local Storage Service]]
- [[_COMMUNITY_AI Extractor Service|AI Extractor Service]]
- [[_COMMUNITY_Extraction Pipeline Core|Extraction Pipeline Core]]
- [[_COMMUNITY_Pipeline Data Models|Pipeline Data Models]]
- [[_COMMUNITY_PDF to Images Converter|PDF to Images Converter]]
- [[_COMMUNITY_JSON Result Generator|JSON Result Generator]]
- [[_COMMUNITY_Pipeline LLM Extraction|Pipeline LLM Extraction]]
- [[_COMMUNITY_Answer Classifier|Answer Classifier]]
- [[_COMMUNITY_Refactored Pipeline Runner|Refactored Pipeline Runner]]
- [[_COMMUNITY_OCR Results Writer|OCR Results Writer]]
- [[_COMMUNITY_Layout Clustering|Layout Clustering]]
- [[_COMMUNITY_Format Detection|Format Detection]]
- [[_COMMUNITY_Gemini Client|Gemini Client]]
- [[_COMMUNITY_App Configuration|App Configuration]]
- [[_COMMUNITY_MCQ Grid Solution|MCQ Grid Solution]]
- [[_COMMUNITY_Template Creator|Template Creator]]
- [[_COMMUNITY_Model Checker|Model Checker]]
- [[_COMMUNITY_Backend Init|Backend Init]]
- [[_COMMUNITY_DB Init|DB Init]]
- [[_COMMUNITY_API Init|API Init]]
- [[_COMMUNITY_Services Init|Services Init]]
- [[_COMMUNITY_JSON Generator Rationale|JSON Generator Rationale]]
- [[_COMMUNITY_JSON Generator Rationale|JSON Generator Rationale]]
- [[_COMMUNITY_JSON Generator Rationale|JSON Generator Rationale]]
- [[_COMMUNITY_JSON Generator Rationale|JSON Generator Rationale]]
- [[_COMMUNITY_JSON Generator Rationale|JSON Generator Rationale]]
- [[_COMMUNITY_JSON Generator Rationale|JSON Generator Rationale]]
- [[_COMMUNITY_JSON Generator Rationale|JSON Generator Rationale]]
- [[_COMMUNITY_JSON Generator Rationale|JSON Generator Rationale]]
- [[_COMMUNITY_OCR Engine Rationale|OCR Engine Rationale]]
- [[_COMMUNITY_OCR Engine Rationale|OCR Engine Rationale]]
- [[_COMMUNITY_OCR Engine Rationale|OCR Engine Rationale]]
- [[_COMMUNITY_AI Extractor Rationale|AI Extractor Rationale]]
- [[_COMMUNITY_Image Preprocessor Rationale|Image Preprocessor Rationale]]
- [[_COMMUNITY_Image Preprocessor Rationale|Image Preprocessor Rationale]]
- [[_COMMUNITY_Image Preprocessor Rationale|Image Preprocessor Rationale]]
- [[_COMMUNITY_Image Preprocessor Rationale|Image Preprocessor Rationale]]

## God Nodes (most connected - your core abstractions)
1. `OptimizedAIExtractor` - 25 edges
2. `PageAnalyzer` - 23 edges
3. `OptimizedExtractor` - 22 edges
4. `get_settings()` - 20 edges
5. `get_local_storage()` - 16 edges
6. `RefactoredPipeline` - 16 edges
7. `AIExtractor` - 15 edges
8. `get_ai_extractor()` - 14 edges
9. `LayoutClusterer` - 13 edges
10. `FormatDetector` - 13 edges

## Surprising Connections (you probably didn't know these)
- `example_1_basic_ocr_extraction()` --calls--> `get_ocr_engine()`  [INFERRED]
  examples.py → backend/services/ocr_engine.py
- `example_1_basic_ocr_extraction()` --calls--> `AnswerParser`  [INFERRED]
  examples.py → backend/services/ocr_engine.py
- `create_template_interactive()` --calls--> `get_ai_extractor()`  [INFERRED]
  create_template.py → backend/services/ai_extractor.py
- `main()` --calls--> `init_db()`  [INFERRED]
  init_db.py → backend/db/database.py
- `main()` --calls--> `drop_db()`  [INFERRED]
  init_db.py → backend/db/database.py

## Communities (52 total, 21 thin omitted)

### Community 0 - "API Route Handlers"
Cohesion: 0.06
Nodes (55): delete_answer_key(), delete_json(), delete_submission(), download_json(), extract_and_mark(), extract_exam_document(), extract_json(), get_status() (+47 more)

### Community 1 - "Submission & Answer Key Queries"
Cohesion: 0.07
Nodes (40): get_answer_key(), get_submission(), list_answer_keys(), list_submissions(), List all answer keys., Get a single answer key., Get full submission details with all candidate results, List all submissions with optional filtering          Args:         skip: Number (+32 more)

### Community 2 - "Image Preprocessing"
Cohesion: 0.08
Nodes (22): ImagePreprocessor, _normalize_size(), preprocess_image_path(), preprocess_pil_image(), Image Pre-processing Service Provides functions to analyze and clean up images b, A class to perform pre-processing checks on images., get_default_extractor(), get_extractor() (+14 more)

### Community 3 - "Frontend UI Components"
Cohesion: 0.1
Nodes (16): Alert(), Badge(), Button(), Card(), LoadingSpinner(), ProgressBar(), apiClient, examAPI (+8 more)

### Community 4 - "Database Models & Exam Creation"
Cohesion: 0.07
Nodes (24): create_answer_key(), create_exam(), Create a new answer key for auto-marking., Base, AnswerKey, CandidateResult, Exam, ExamDocument (+16 more)

### Community 5 - "OCR Engine"
Cohesion: 0.1
Nodes (16): AnswerParser, extract_all(), extract_free_response(), extract_multiple_choice(), get_ocr_engine(), OCREngine, OCR Engine Service Extracts text from images using Tesseract OCR, Extract text from multiple images                  Args:             image_paths (+8 more)

### Community 6 - "Page Layout Analysis"
Cohesion: 0.12
Nodes (12): BoundingBox, PageAnalyzer, Detect if page is blank using standard deviation., Assess image quality based on:         - Sharpness (Laplacian variance), Detect and classify regions using contour analysis and heuristics., Detect the header region (typically top 10-20% with student info)., Detect MCQ answer grids by looking for:         - Regular patterns of circles/bu, Detect drawing/free-response areas (large rectangular regions). (+4 more)

### Community 7 - "Spaces Client Integration"
Cohesion: 0.09
Nodes (13): get_spaces_client(), DigitalOcean Spaces Client Service Handles file upload/download operations with, Uploads a single image file to a structured archive path in Spaces., Download a PDF from Spaces to local filesystem                  Args:, Download JSON data from Spaces                  Args:             key: Object ke, List files in Spaces with optional prefix filter                  Args:, Client for interacting with DigitalOcean Spaces (S3-compatible), Delete a file from Spaces                  Args:             key: Object key to (+5 more)

### Community 8 - "Database Layer"
Cohesion: 0.1
Nodes (17): drop_db(), get_db(), init_db(), Database connection and session management, Dependency function to get database session     Yields a session and closes it a, Initialize database tables, Drop all database tables (use with caution!), check_tables() (+9 more)

### Community 9 - "Local Storage Service"
Cohesion: 0.16
Nodes (8): LocalStorage, Local file storage utilities for saving and retrieving uploads/results., Simple local filesystem storage with upload/result directories., Return a filesystem-safe version of the provided filename., Persist a PDF upload to disk., Persist JSON results to disk., Read stored JSON results; returns None if missing., Delete a stored file if it exists.

### Community 10 - "AI Extractor Service"
Cohesion: 0.18
Nodes (10): AIExtractor, build_extraction_prompt(), AI Extractor Service Uses Google Gemini Vision API for intelligent answer extrac, Extract data from a single exam-sheet image., Process a single page with retry logic., Extract data from multiple exam-sheet images with dynamic format detection., AI-powered extractor using Google Gemini Vision API with dynamic format detectio, Validate extracted data for common issues. (+2 more)

### Community 11 - "Extraction Pipeline Core"
Cohesion: 0.25
Nodes (8): _is_blank_token(), _is_invalid_token(), _normalize_range(), OptimizedExtractor, _question_key(), Refactored AI Extraction Pipeline - Token-optimized Gemini usage - Multi-stage p, Token-optimized extraction using Gemini.     Key strategies:     1. Send cropped, Convert raw dict to CandidateExtraction.

### Community 12 - "Pipeline Data Models"
Cohesion: 0.22
Nodes (15): Enum, CandidateExtraction, ExamFormat, ExtractionContext, Detected exam format structure - cached per unique layout., Context passed to extraction functions., Extraction result for a single candidate/page., AnswerBubble (+7 more)

### Community 13 - "PDF to Images Converter"
Cohesion: 0.14
Nodes (8): PDFConverter, PDF to Images Conversion Service Converts PDF pages to PNG images for OCR proces, Convert PDF to PIL Image objects (in-memory, no disk I/O)                  Args:, Get the number of pages in a PDF without full conversion                  Args:, Converts PDF files to images, Initialize PDF converter                  Args:             dpi: Resolution for, Convert PDF file to images using PyMuPDF                  Args:             pdf_, Convert PDF bytes to images using PyMuPDF          Args:             pdf_bytes:

### Community 14 - "JSON Result Generator"
Cohesion: 0.2
Nodes (6): generate(), generate_with_validation(), JSONGenerator, _normalize_candidate(), JSON Generator Service Formats extracted exam data into structured JSON output., Generates structured JSON from extracted exam data

### Community 15 - "Pipeline LLM Extraction"
Cohesion: 0.18
Nodes (6): Extract header fields from cropped header region., Extract answers for a range of questions., Full page extraction when CV pre-processing is insufficient.         Uses two-pa, Focused MCQ-only extraction for better accuracy., Build a token-efficient full extraction prompt., Parse JSON from LLM response with tolerant fallbacks.

### Community 16 - "Answer Classifier"
Cohesion: 0.22
Nodes (5): AnswerClassifier, Lightweight classification of answers using CV when possible.     Only escalates, Attempt to classify MCQ answers using computer vision.          Returns:, Estimate marked MCQ options from the detected answer grid.         Returns only, Determine if LLM extraction is needed.

### Community 17 - "Refactored Pipeline Runner"
Cohesion: 0.24
Nodes (6): Extract data from a single page., Extract only header fields from a page image using the vision model., Fallback candidate result when full-page LLM extraction fails., Convert extractions to final JSON output format., Main pipeline orchestrating all stages.     Processes PDFs with minimal LLM toke, RefactoredPipeline

### Community 18 - "OCR Results Writer"
Cohesion: 0.31
Nodes (7): _confidence_stats(), get_ocr_results_writer(), OCRResultsWriter, OCR artifact writer. Stores OCR output in a structured OCRResults directory for, Persist per-page OCR outputs and a summary JSON report., _safe_token(), _to_json()

### Community 19 - "Layout Clustering"
Cohesion: 0.22
Nodes (5): Main entry point: process list of page images.          Pipeline stages:, LayoutClusterer, Groups pages with similar layouts together.     This allows us to detect differe, Group pages by layout similarity.         Returns: {format_group_id: [page_numbe, Get the best quality page from a cluster to use for format detection.

### Community 20 - "Format Detection"
Cohesion: 0.28
Nodes (5): FormatDetector, Detect exam format from representative page.         Uses cache to avoid redunda, Parse LLM response into ExamFormat., Fallback format when detection fails., Detects exam format using Gemini.     Key optimization: Only calls LLM once per

### Community 21 - "Gemini Client"
Cohesion: 0.36
Nodes (6): _candidate_models(), create_gemini_model(), _parse_models(), Gemini model selection helpers. Provides safe model fallback selection with a si, Configure Gemini client and create a model with fallback-aware selection.     Re, _resolve_available_model()

### Community 22 - "App Configuration"
Cohesion: 0.33
Nodes (5): Config, Application configuration management, Application settings loaded from environment variables, Settings, BaseSettings

### Community 23 - "MCQ Grid Solution"
Cohesion: 0.47
Nodes (4): extract_uz_mcq_from_images(), _load_reference_grid(), Extract UZ MCQ answers from page image paths.      Returns:     - answers_by_pag, _score_row_options()

### Community 24 - "Template Creator"
Cohesion: 0.5
Nodes (3): create_template_interactive(), Template Creation Tool for Exam Format Training  This script helps you create tr, Interactive template creation

## Knowledge Gaps
- **203 isolated node(s):** `Database initialization and management script`, `Check if tables exist in database`, `Main database management function`, `Batch Template Training from Example PDFs  This script processes all PDFs in bac`, `Process all PDFs in backend/examples folder` (+198 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **21 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `OptimizedAIExtractor` connect `Image Preprocessing` to `API Route Handlers`, `Database Models & Exam Creation`, `Page Layout Analysis`, `AI Extractor Service`, `Pipeline Data Models`, `Refactored Pipeline Runner`?**
  _High betweenness centrality (0.280) - this node is a cross-community bridge._
- **Why does `get_ai_extractor()` connect `API Route Handlers` to `Template Creator`, `AI Extractor Service`, `Image Preprocessing`?**
  _High betweenness centrality (0.154) - this node is a cross-community bridge._
- **Why does `get_settings()` connect `API Route Handlers` to `OCR Engine`, `Spaces Client Integration`, `Local Storage Service`, `Gemini Client`, `App Configuration`?**
  _High betweenness centrality (0.125) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `OptimizedAIExtractor` (e.g. with `RefactoredPipeline` and `CandidateExtraction`) actually correct?**
  _`OptimizedAIExtractor` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 11 inferred relationships involving `PageAnalyzer` (e.g. with `OptimizedAIExtractor` and `ExamFormat`) actually correct?**
  _`PageAnalyzer` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `OptimizedExtractor` (e.g. with `PageLayout` and `RegionType`) actually correct?**
  _`OptimizedExtractor` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 17 inferred relationships involving `get_settings()` (e.g. with `extract_exam_document()` and `_save_ocr_results()`) actually correct?**
  _`get_settings()` has 17 INFERRED edges - model-reasoned connections that need verification._