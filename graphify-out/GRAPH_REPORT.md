# Graph Report - .  (2026-05-07)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 481 nodes · 778 edges · 39 communities (20 shown, 19 thin omitted)
- Extraction: 80% EXTRACTED · 20% INFERRED · 0% AMBIGUOUS · INFERRED: 154 edges (avg confidence: 0.7)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f4720c16`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 38|Community 38]]

## God Nodes (most connected - your core abstractions)
1. `OptimizedAIExtractor` - 25 edges
2. `get_settings()` - 17 edges
3. `RefactoredPipeline` - 15 edges
4. `get_local_storage()` - 15 edges
5. `get_ai_extractor()` - 13 edges
6. `OCREngine` - 13 edges
7. `LayoutClusterer` - 13 edges
8. `get_pdf_converter()` - 13 edges
9. `FormatDetector` - 13 edges
10. `AnswerClassifier` - 13 edges

## Surprising Connections (you probably didn't know these)
- `Detects exam format using Gemini.     Key optimization: Only calls LLM once per` --rationale_for--> `FormatDetector`  [EXTRACTED]
  backend/services/extraction_pipeline.py → docs/PIPELINE_ARCHITECTURE.md
- `Lightweight classification of answers using CV when possible.     Only escalate` --rationale_for--> `AnswerClassifier`  [EXTRACTED]
  backend/services/extraction_pipeline.py → docs/PIPELINE_ARCHITECTURE.md
- `FormatDetector` --uses--> `RegionType`  [INFERRED]
  docs/PIPELINE_ARCHITECTURE.md → backend/services/page_analyzer.py
- `AnswerClassifier` --uses--> `RegionType`  [INFERRED]
  docs/PIPELINE_ARCHITECTURE.md → backend/services/page_analyzer.py
- `FormatDetector` --uses--> `QuestionType`  [INFERRED]
  docs/PIPELINE_ARCHITECTURE.md → backend/services/page_analyzer.py

## Communities (39 total, 19 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.05
Nodes (48): Enum, AnswerClassifier, FormatDetector, CandidateExtraction, ExamFormat, ExtractionContext, Refactored AI Extraction Pipeline - Token-optimized Gemini usage - Multi-stage, Main entry point: process list of page images.          Pipeline stages: (+40 more)

### Community 1 - "Community 1"
Cohesion: 0.05
Nodes (42): backend.api.routes, backend.db.models, backend.services.ai_extractor, backend.services.pdf_to_images, backend.services.space_client, backend.worker, Celery worker for background task processing, drop_db() (+34 more)

### Community 2 - "Community 2"
Cohesion: 0.07
Nodes (30): process_exam_pdf(), ProcessingLog, Model for processing logs and audit trail, build_extraction_prompt(), get_ai_extractor(), AI Extractor Service Uses Google Gemini Vision API for intelligent answer extra, Extract data from a single exam-sheet image., Process a single page with retry logic. (+22 more)

### Community 3 - "Community 3"
Cohesion: 0.07
Nodes (39): create_answer_key(), delete_json(), delete_submission(), download_json(), extract_and_mark(), extract_json(), get_answer_key(), get_status() (+31 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (31): extract_exam_document(), Config, get_settings(), Application configuration management, Application settings loaded from environment variables, Get cached settings instance, Settings, BaseSettings (+23 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (34): get_submission(), list_submissions(), Get full submission details with all candidate results, List all submissions with optional filtering          Args:         skip: Num, Get the raw JSON result file for a submission, AnswerKeySchema, CandidateResultSchema, ErrorResponse (+26 more)

### Community 6 - "Community 6"
Cohesion: 0.09
Nodes (14): Alert(), Button(), LoadingSpinner(), TrackingPage(), apiClient, examAPI, requestUrl, ACTION_LABELS (+6 more)

### Community 7 - "Community 7"
Cohesion: 0.11
Nodes (16): _is_blank_token(), _is_invalid_token(), _normalize_range(), _question_key(), Extract only header fields from a page image using the vision model., Token-optimized extraction using Gemini.     Key strategies:     1. Send cropp, Extract header fields from cropped header region., Extract answers for a range of questions. (+8 more)

### Community 8 - "Community 8"
Cohesion: 0.15
Nodes (20): create_exam(), upload_student_pdf(), Base, AnswerKey, CandidateResult, Exam, ExamDocument, ExamSubmission (+12 more)

### Community 9 - "Community 9"
Cohesion: 0.13
Nodes (15): example_1_basic_ocr_extraction(), Example 1: Basic OCR extraction workflow     Uses Tesseract OCR for text extrac, AnswerParser, extract_all(), extract_free_response(), extract_multiple_choice(), get_ocr_engine(), OCREngine (+7 more)

### Community 10 - "Community 10"
Cohesion: 0.17
Nodes (8): Local file storage utilities for saving and retrieving uploads/results., Simple local filesystem storage with upload/result directories., Return a filesystem-safe version of the provided filename., Persist a PDF upload to disk., Persist JSON results to disk., Read stored JSON results; returns None if missing., Delete a stored file if it exists., Delete a file from Spaces                  Args:             key: Object key

### Community 11 - "Community 11"
Cohesion: 0.15
Nodes (9): get_pdf_converter(), PDFConverter, PDF to Images Conversion Service Converts PDF pages to PNG images for OCR proce, Convert PDF to PIL Image objects (in-memory, no disk I/O)                  Arg, Get the number of pages in a PDF without full conversion                  Args, Converts PDF files to images, Factory function to create PDFConverter instance, Convert PDF file to images using PyMuPDF                  Args:             p (+1 more)

### Community 12 - "Community 12"
Cohesion: 0.22
Nodes (5): generate(), generate_with_validation(), _normalize_candidate(), JSON Generator Service Formats extracted exam data into structured JSON output., Generates structured JSON from extracted exam data

### Community 13 - "Community 13"
Cohesion: 0.29
Nodes (7): backend.services.ocr_engine, Tesseract OCR, _confidence_stats(), OCR artifact writer. Stores OCR output in a structured OCRResults directory for, Persist per-page OCR outputs and a summary JSON report., _safe_token(), _to_json()

### Community 14 - "Community 14"
Cohesion: 0.47
Nodes (4): extract_uz_mcq_from_images(), _load_reference_grid(), Extract UZ MCQ answers from page image paths.      Returns:     - answers_by_, _score_row_options()

### Community 15 - "Community 15"
Cohesion: 0.33
Nodes (6): Adobe Acrobat Pro 11.0.0 Paper Capture Plug-in, NAPS2, UZ1-35 Document, UZ1-35 Variant A, ZONE Z Document, Scanned Image

### Community 16 - "Community 16"
Cohesion: 0.4
Nodes (4): API package initialization, Backend package initialization, Database package initialization, Services package initialization

## Knowledge Gaps
- **210 isolated node(s):** `Check available Gemini models with your API key  This helps determine which mo`, `Template Creation Tool for Exam Format Training  This script helps you create`, `Interactive template creation`, `Example usage of the exam extraction services Demonstrates how to use the servi`, `Example 1: Basic OCR extraction workflow     Uses Tesseract OCR for text extrac` (+205 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **19 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `OptimizedAIExtractor` connect `Community 2` to `Community 0`, `Community 4`, `Community 7`?**
  _High betweenness centrality (0.167) - this node is a cross-community bridge._
- **Why does `get_ai_extractor()` connect `Community 2` to `Community 1`, `Community 3`, `Community 4`?**
  _High betweenness centrality (0.095) - this node is a cross-community bridge._
- **Why does `get_settings()` connect `Community 4` to `Community 2`, `Community 3`?**
  _High betweenness centrality (0.092) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `OptimizedAIExtractor` (e.g. with `ProcessingLog` and `ai_extractor.py`) actually correct?**
  _`OptimizedAIExtractor` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 14 inferred relationships involving `get_settings()` (e.g. with `extract_exam_document()` and `_save_ocr_results()`) actually correct?**
  _`get_settings()` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `RefactoredPipeline` (e.g. with `.__init__()` and `PageLayout`) actually correct?**
  _`RefactoredPipeline` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 14 inferred relationships involving `get_local_storage()` (e.g. with `example_3_save_results_locally()` and `example_4_complete_workflow()`) actually correct?**
  _`get_local_storage()` has 14 INFERRED edges - model-reasoned connections that need verification._