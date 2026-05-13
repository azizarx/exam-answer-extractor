"""
Test the extraction pipeline on real student exam papers.

Processes every page of:
  - backend/examples/UZ1-35.pdf (35 pages, SEAMO 2025)
  - backend/examples/ZONE Z.pdf (69 pages, SEAMO X 2026)

For each page:
  1. Render at 300 DPI
  2. Auto-detect which template matches
  3. Run MCQ extraction (if template has mcq_grid sections)
  4. Run numeric grid / open response / diagram extraction (other sections)
  5. Report results
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import fitz
import numpy as np

from backend.services.template_service import TemplateRegistry
from backend.services.mcq_extractor import extract_page as extract_mcq_page
from backend.services.section_extractor import extract_section, extract_diagrams

TEST_DATA_DIR = Path("tests/test_data/real_exams")

PDFS = [
    ("backend/examples/UZ1-35.pdf", "UZ1-35"),
    ("backend/examples/ZONE Z.pdf", "ZONE_Z"),
]


def render_page(pdf_path: str, page_idx: int, dpi: int = 300) -> np.ndarray:
    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    doc.close()
    if pix.n == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    elif pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    return img


def get_page_text(pdf_path: str, page_idx: int) -> str:
    doc = fitz.open(pdf_path)
    text = doc[page_idx].get_text()
    doc.close()
    return text


def main():
    registry = TemplateRegistry()
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Pre-save anchor images from reference renders
    ref_anchors = {
        "seamo_2025_k": ("backend/templates/reference_images/seamo_2025_page1.png", "seamo_2025_k"),
        "seamo_2025_a": ("backend/templates/reference_images/seamo_2025_page2.png", "seamo_2025_a"),
        "seamo_x_2026_k": ("backend/templates/reference_images/seamo_x_2026_page1.png", "seamo_x_2026_k"),
        "seamo_x_2026_a": ("backend/templates/reference_images/seamo_x_2026_page2.png", "seamo_x_2026_a"),
        "seamo_x_2026_b": ("backend/templates/reference_images/seamo_x_2026_page3.png", "seamo_x_2026_b"),
        "seamo_x_2026_c": ("backend/templates/reference_images/seamo_x_2026_page4.png", "seamo_x_2026_c"),
    }
    for tid, (ref_path, _) in ref_anchors.items():
        ref_img = cv2.imread(ref_path)
        if ref_img is not None:
            registry.save_anchor_image(tid, ref_img)

    all_results = []
    total_pages = 0
    detected_pages = 0
    mcq_pages = 0
    other_pages = 0

    for pdf_path, pdf_label in PDFS:
        doc = fitz.open(pdf_path)
        num_pages = len(doc)
        doc.close()

        print(f"\n{'='*60}")
        print(f"{pdf_label}: {num_pages} pages")
        print(f"{'='*60}")

        for page_idx in range(num_pages):
            total_pages += 1
            page_num = page_idx + 1

            # Render
            t0 = time.time()
            image = render_page(pdf_path, page_idx)
            render_time = time.time() - t0

            # Get OCR text for detection hints
            ocr_text = get_page_text(pdf_path, page_idx)

            # Auto-detect template
            t1 = time.time()
            detection = registry.detect(image, filename=pdf_label, ocr_text=ocr_text)
            detect_time = time.time() - t1

            if detection is None:
                print(f"  p{page_num:3d}: NO MATCH")
                all_results.append({
                    "pdf": pdf_label, "page": page_num,
                    "template": None, "status": "no_match",
                })
                continue

            template, anchor_score, (dx, dy) = detection
            detected_pages += 1

            page_result = {
                "pdf": pdf_label,
                "page": page_num,
                "template": template.id,
                "anchor_score": round(anchor_score, 4),
                "dx": dx, "dy": dy,
                "render_ms": round(render_time * 1000),
                "detect_ms": round(detect_time * 1000),
                "sections": {},
            }

            # Extract MCQ sections
            mcq_sections = [s for s in template.sections if s.type == "mcq_grid"]
            if mcq_sections:
                t2 = time.time()
                mcq_result = extract_mcq_page(image, template, page_number=page_num, registry=registry)
                mcq_time = time.time() - t2

                mcq_answers = mcq_result.answers
                mcq_status = mcq_result.status
                mcq_coverage = mcq_result.coverage

                page_result["sections"]["mcq"] = {
                    "status": mcq_status,
                    "answers": mcq_answers,
                    "coverage": round(mcq_coverage, 2),
                    "extract_ms": round(mcq_time * 1000),
                }

                if mcq_status == "ok":
                    mcq_pages += 1

            # Extract other sections
            for section in template.sections:
                if section.type == "mcq_grid":
                    continue

                t3 = time.time()
                sec_result = extract_section(image, section, dx, dy)
                sec_time = time.time() - t3

                sec_key = f"{section.type}_q{section.question_start}_{section.question_end}"
                page_result["sections"][sec_key] = {
                    "type": section.type,
                    "answers": sec_result.answers,
                    "diagrams": list(sec_result.diagrams.keys()),
                    "errors": sec_result.errors,
                    "extract_ms": round(sec_time * 1000),
                }

                # Save diagram crops
                for q, diag_data in sec_result.diagrams.items():
                    crop_path = TEST_DATA_DIR / f"{pdf_label}_p{page_num}_diagram_q{q}.png"
                    cv2.imwrite(str(crop_path), diag_data["crop"])

                other_pages += 1

            all_results.append(page_result)

            # Print summary line
            sections_summary = []
            for sec_key, sec_data in page_result["sections"].items():
                if sec_key == "mcq":
                    n_answers = len(sec_data["answers"])
                    cov = sec_data["coverage"]
                    sections_summary.append(f"MCQ:{n_answers}ans/{cov:.0%}cov")
                else:
                    n_answers = sum(1 for v in sec_data["answers"].values() if v.strip())
                    n_diag = len(sec_data.get("diagrams", []))
                    parts = []
                    if n_answers:
                        parts.append(f"{n_answers}ans")
                    if n_diag:
                        parts.append(f"{n_diag}diag")
                    sections_summary.append(f"{sec_data['type']}:{'/'.join(parts) or 'empty'}")

            print(f"  p{page_num:3d}: {template.id:20s} score={anchor_score:.3f} dx={dx:+4d} dy={dy:+4d} | {' | '.join(sections_summary)}")

    # Save results
    results_path = TEST_DATA_DIR / "results.json"
    summary = {
        "total_pages": total_pages,
        "detected": detected_pages,
        "unmatched": total_pages - detected_pages,
        "mcq_extracted": mcq_pages,
    }
    results_path.write_text(json.dumps(
        {"summary": summary, "pages": all_results},
        indent=2, default=str,
    ))

    print(f"\n{'='*60}")
    print(f"Summary: {detected_pages}/{total_pages} pages matched a template")
    print(f"  MCQ extracted: {mcq_pages} pages")
    print(f"  Unmatched: {total_pages - detected_pages} pages")
    print(f"Results saved to {results_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
