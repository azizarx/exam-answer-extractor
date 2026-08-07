# Refactored Exam Extraction Pipeline - Architecture Document

## Executive Summary

This document describes the refactored OCR + LLM pipeline for processing exam PDFs. The new architecture reduces Gemini API costs by **60-80%** through intelligent preprocessing, layout clustering, and selective LLM usage.

---

## 1. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              PDF INPUT                                          │
│                         (May contain multiple exam formats)                      │
└─────────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 1: PDF CONVERSION                                                        │
│  ┌─────────────────────────────────────────────────────────────────────────────┐
│  │  PDFConverter (PyMuPDF)                                                     │
│  │  - Input: PDF file                                                          │
│  │  - Output: List[PIL.Image] at 300 DPI                                       │
│  │  - Memory: In-memory conversion, no temp files                              │
│  └─────────────────────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 2: PAGE PREPROCESSING (No LLM - Pure CV)                                 │
│  ┌─────────────────────────────────────────────────────────────────────────────┐
│  │  PageAnalyzer                                                               │
│  │  ├── Blank Detection: Skip pages with std_dev < 10                          │
│  │  ├── Quality Assessment: Sharpness + Contrast scoring                       │
│  │  └── Output: PageLayout objects with quality metrics                        │
│  └─────────────────────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 3: LAYOUT SEGMENTATION (No LLM - Pure CV)                                │
│  ┌─────────────────────────────────────────────────────────────────────────────┐
│  │  PageAnalyzer._detect_regions()                                             │
│  │  ├── Header Detection: Top 20% with text/lines                              │
│  │  ├── Answer Grid Detection: Hough circles for bubble patterns               │
│  │  ├── Drawing Area Detection: Large rectangular contours                     │
│  │  └── Layout Hashing: MD5 of normalized region positions                     │
│  └─────────────────────────────────────────────────────────────────────────────┘
│  ┌─────────────────────────────────────────────────────────────────────────────┐
│  │  LayoutClusterer                                                            │
│  │  ├── Groups pages with same layout_hash                                     │
│  │  ├── Identifies mixed exam formats in single PDF                            │
│  │  └── Selects best quality page per cluster                                  │
│  └─────────────────────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 4: FORMAT DETECTION (LLM - ONE call per unique layout)                   │
│  ┌─────────────────────────────────────────────────────────────────────────────┐
│  │  FormatDetector                                                             │
│  │  ├── Cache Key: layout_hash                                                 │
│  │  ├── Input: Representative page image (best quality from cluster)          │
│  │  ├── Output: ExamFormat (header fields, question ranges, options)           │
│  │  └── Token Usage: ~300 input + ~200 output per unique format                │
│  └─────────────────────────────────────────────────────────────────────────────┘
│                                                                                 │
│  COST SAVINGS: 50-page PDF with same format = 1 LLM call instead of 50         │
└─────────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 5: REGION EXTRACTION                                                     │
│  ┌─────────────────────────────────────────────────────────────────────────────┐
│  │  Region Cropping (No LLM)                                                   │
│  │  ├── Crop header region from each page                                      │
│  │  ├── Crop answer grid region(s)                                             │
│  │  └── Crop drawing area region(s)                                            │
│  └─────────────────────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 6: LIGHTWEIGHT CLASSIFICATION (Minimize LLM Usage)                       │
│  ┌─────────────────────────────────────────────────────────────────────────────┐
│  │  AnswerClassifier                                                           │
│  │  ├── CV Bubble Detection: Hough circles + fill ratio analysis              │
│  │  ├── Confident MCQ: Extract without LLM (fill_ratio > 0.4 = filled)        │
│  │  ├── Blank Detection: No filled bubbles = "BL"                              │
│  │  ├── Invalid Detection: Multiple fills in same row = "IN"                   │
│  │  └── Unclear: Queue for LLM verification                                    │
│  └─────────────────────────────────────────────────────────────────────────────┘
│                                                                                 │
│  DECISION: if CV confidence > 80% → skip LLM for MCQ extraction                │
└─────────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 7: SELECTIVE LLM EXTRACTION                                              │
│  ┌─────────────────────────────────────────────────────────────────────────────┐
│  │  OptimizedExtractor                                                         │
│  │                                                                             │
│  │  Path A: Header Only (if CV got all MCQ answers)                            │
│  │  ├── Input: Cropped header region (~50KB vs ~500KB full page)               │
│  │  ├── Prompt: 50 tokens (minimal)                                            │
│  │  └── Output: 100 tokens                                                     │
│  │                                                                             │
│  │  Path B: Targeted Questions (for unclear answers)                           │
│  │  ├── Input: Cropped answer region + list of unclear question numbers        │
│  │  ├── Prompt: 80 tokens                                                      │
│  │  └── Output: 50 tokens                                                      │
│  │                                                                             │
│  │  Path C: Full Page (fallback when CV fails completely)                      │
│  │  ├── Input: Full page image                                                 │
│  │  ├── Prompt: Format-aware prompt (150 tokens)                               │
│  │  └── Output: 300 tokens                                                     │
│  └─────────────────────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 8: OUTPUT ASSEMBLY                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────────┐
│  │  OutputAssembler                                                            │
│  │  ├── Merge CV + LLM results                                                 │
│  │  ├── Validate against schema                                                │
│  │  ├── Fill missing fields with null                                          │
│  │  ├── Normalize answer codes (A-E, BL, IN, DR)                               │
│  │  └── Output: List[CandidateResult] → JSON                                   │
│  └─────────────────────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Token Optimization Strategy

### Problem: Current System
- Sends FULL page image (~500KB) to Gemini for EVERY page
- Uses same verbose prompt (~500 tokens) for every page
- No caching of repeated layouts
- **Result: 50-page PDF = 50 × 500KB images + 50 × 500 tokens = ~25,000 input tokens**

### Solution: Refactored System

| Strategy | Savings | Implementation |
|----------|---------|----------------|
| **Format Caching** | 50-80% | Detect format once per unique layout, reuse for all pages |
| **Region Cropping** | 40-60% | Send cropped header (50KB) instead of full page (500KB) |
| **CV Pre-classification** | 30-50% | Use Hough circles for bubble detection, skip LLM for clear MCQ |
| **Minimal Prompts** | 20-30% | Format-aware prompts with only necessary instructions |
| **Batch Processing** | 10-20% | Group similar extractions |

### Token Usage Comparison

| Scenario | Current System | Refactored System |
|----------|---------------|-------------------|
| 50-page PDF, same format | 50 LLM calls | 1 format + 50 extraction |
| Format detection | ~500 tokens/page | ~500 tokens total |
| MCQ extraction (clear bubbles) | ~400 tokens/page | 0 (CV only) |
| MCQ extraction (unclear) | ~400 tokens/page | ~150 tokens (targeted) |
| Header extraction | ~400 tokens/page | ~150 tokens (cropped region) |
| **Total (50 pages, 80% clear MCQ)** | ~25,000 tokens | ~4,000 tokens |

---

## 3. Prompt Design

### 3.1 System Prompt (Set once per session)

```
You are an exam sheet data extractor. Your role is to accurately extract
student information and answers from scanned exam sheets.

Rules:
1. Return ONLY valid JSON - no markdown, no explanations
2. Use exact answer codes: A, B, C, D, E, BL (blank), IN (invalid), DR (drawing)
3. Return null for fields that cannot be read
4. Never guess or infer - only extract what is clearly visible
5. If multiple bubbles are filled for one question, return "IN"
```

### 3.2 Format Detection Prompt

```
Analyze this exam sheet structure. Return JSON:

{
    "header_fields": [
        {"key": "candidate_name", "label": "visible label text"},
        {"key": "candidate_number", "label": "visible label text"}
    ],
    "mcq_range": {"start": 1, "end": 30},
    "drawing_range": {"start": 31, "end": 35},
    "answer_options": ["A", "B", "C", "D"],
    "total_questions": 35,
    "description": "Brief format description"
}

Only include fields visible on the sheet. Set ranges to null if not present.
```

### 3.3 Header Extraction Prompt (Cropped Region)

```
Extract student info from this header.
Return: {"candidate_name": str|null, "candidate_number": str|null,
         "country": str|null, "paper_type": str|null}
```

### 3.4 Answer Extraction Prompt (Full Page)

```
Extract answers Q{start}-Q{end}. Options: {A, B, C, D}
Return: {"1": "A", "2": "BL", "3": "IN", ...}
Codes: A-E=answer, BL=blank, IN=invalid (multiple/unclear)
```

### 3.5 Targeted Answer Prompt (Specific Questions)

```
Verify answers for questions: {list_of_unclear}
Current readings: {cv_extracted_answers}
Return corrections only: {"5": "B", "12": "IN"}
```

---

## 4. Question Type Handling

### 4.1 MCQ Questions

```python
def handle_mcq(self, image, question_num, grid_region):
    # Step 1: CV Detection
    bubbles = self.detect_bubbles(image, grid_region)
    filled = [b for b in bubbles if b.fill_ratio > 0.4]

    if len(filled) == 0:
        return "BL"  # Blank
    elif len(filled) == 1:
        return filled[0].option  # A, B, C, D, or E
    else:
        return "IN"  # Invalid - multiple marks
```

### 4.2 Drawing Questions

```python
def handle_drawing(self, image, question_num, drawing_region):
    # Always return "DR" - don't try to interpret
    # Content is not extracted, just flagged as drawing question
    return "DR"
```

### 4.3 Blank Detection

```python
def is_blank_answer(self, bubbles_for_question):
    # No bubbles filled above threshold
    return all(b.fill_ratio < 0.3 for b in bubbles_for_question)
```

### 4.4 Invalid Detection

```python
def is_invalid_answer(self, bubbles_for_question):
    filled = [b for b in bubbles if b.fill_ratio > 0.4]
    return len(filled) > 1  # Multiple marks = invalid
```

---

## 5. Mixed Exam Format Handling

### Detection Strategy

```python
def detect_mixed_formats(self, layouts: List[PageLayout]):
    """
    Groups pages by layout structure.
    Different exam formats will have different layout hashes.
    """
    clusters = {}
    for layout in layouts:
        hash_key = layout.layout_hash
        if hash_key not in clusters:
            clusters[hash_key] = {
                "pages": [],
                "format": None  # Will be detected
            }
        clusters[hash_key]["pages"].append(layout.page_number)

    return clusters
```

### Processing Flow

```
PDF with mixed formats detected:
├── Cluster A (pages 1-25): Format "Standard MCQ 30Q"
│   ├── Detect format ONCE using page 1
│   └── Apply to pages 1-25
├── Cluster B (pages 26-50): Format "Extended MCQ 40Q + Drawing"
│   ├── Detect format ONCE using page 26
│   └── Apply to pages 26-50
└── Output: Combined results with format tracking
```

---

## 6. Failure Handling

### Graceful Degradation

```python
def extract_with_fallback(self, image, layout, format):
    try:
        # Try CV extraction first
        result = self.cv_extract(image, layout)
        if result.confidence > 0.8:
            return result
    except CVExtractionError:
        pass

    try:
        # Fall back to LLM
        result = self.llm_extract(image, format)
        return result
    except LLMExtractionError as e:
        # Final fallback: return empty with nulls
        return CandidateExtraction(
            page_number=layout.page_number,
            candidate_name=None,
            candidate_number=None,
            country=None,
            answers={str(q): "BL" for q in range(1, format.total_questions + 1)},
            errors=[str(e)]
        )
```

### Error Codes in Output

```json
{
    "candidate_number": null,
    "answers": {
        "1": "A",
        "2": "B",
        "3": "IN",
        "4": "BL",
        "5": null
    },
    "_extraction_errors": ["Question 5: OCR failed, region unclear"]
}
```

---

## 7. Tool Recommendations

### Core Tools (Already in use - keep)

| Tool | Purpose | Status |
|------|---------|--------|
| PyMuPDF | PDF to image | ✅ Keep |
| Gemini 2.0 Flash | Vision LLM | ✅ Keep |
| Pillow | Image manipulation | ✅ Keep |

### New Tools to Add

| Tool | Purpose | Why |
|------|---------|-----|
| **OpenCV** | Layout detection, bubble finding | Fast CV operations, no LLM needed |
| **scikit-image** | Advanced image preprocessing | Deskewing, noise reduction |
| **Tesseract** (optional) | Local OCR for header text | Reduce LLM calls for text extraction |

### Installation

```bash
pip install opencv-python scikit-image pytesseract
```

---

## 8. Implementation Pseudocode

```python
class ExamExtractionPipeline:
    def process_pdf(self, pdf_path: str) -> List[Dict]:
        # Stage 1: Convert PDF to images
        images = self.pdf_converter.convert(pdf_path)

        # Stage 2: Preprocess and filter
        layouts = []
        for i, img in enumerate(images):
            layout = self.page_analyzer.analyze(img, page_num=i+1)
            if not layout.is_blank:
                layouts.append(layout)

        # Stage 3: Cluster by layout
        clusters = self.layout_clusterer.cluster(layouts)

        # Stage 4: Detect format per cluster (ONE LLM call each)
        formats = {}
        for cluster_id, pages in clusters.items():
            rep_page = self.get_best_quality_page(pages, layouts)
            formats[cluster_id] = self.format_detector.detect(
                images[rep_page - 1],
                layouts[rep_page - 1]
            )

        # Stage 5-7: Extract per page
        results = []
        for layout in layouts:
            format = formats[layout.format_group]

            # Try CV first
            cv_answers, unclear = self.classifier.classify_cv(
                images[layout.page_number - 1],
                layout,
                format
            )

            if len(unclear) < format.total_questions * 0.2:
                # CV was mostly successful
                header = self.extractor.extract_header_only(
                    images[layout.page_number - 1],
                    layout.header_region
                )
                # Verify only unclear questions with LLM
                verified = self.extractor.verify_answers(
                    images[layout.page_number - 1],
                    unclear,
                    cv_answers
                )
                answers = {**cv_answers, **verified}
            else:
                # Fall back to full LLM extraction
                extraction = self.extractor.extract_full(
                    images[layout.page_number - 1],
                    format
                )
                header = extraction
                answers = extraction.answers

            results.append({
                "candidate_name": header.candidate_name,
                "candidate_number": header.candidate_number,
                "country": header.country,
                "answers": answers
            })

        # Stage 8: Validate and return
        return self.validate_output(results)
```

---

## 9. Migration Plan

### Phase 1: Add CV Layer (Non-breaking)
1. Add `page_analyzer.py` alongside existing code
2. Add layout detection to preprocessing
3. No changes to extraction yet

### Phase 2: Format Caching
1. Add `FormatDetector` with caching
2. Modify `AIExtractor.analyze_format()` to use cache
3. Measure token savings

### Phase 3: Selective LLM
1. Add `AnswerClassifier` for CV bubble detection
2. Route clear answers through CV path
3. Route unclear answers through LLM

### Phase 4: Region Cropping
1. Crop header regions before LLM calls
2. Update prompts to be region-specific
3. Measure additional savings

---

## 10. Expected Improvements

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| LLM calls per 50-page PDF | 51 | 11-15 | **70% reduction** |
| Tokens per PDF | ~25,000 | ~5,000 | **80% reduction** |
| Processing time | ~120s | ~45s | **60% faster** |
| Mixed format handling | ❌ Fails | ✅ Automatic | **New capability** |
| Cost per PDF | ~$0.25 | ~$0.05 | **80% savings** |
