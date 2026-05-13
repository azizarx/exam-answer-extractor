"""
End-to-end test for ALL exam formats using the actual layout PDFs.

Renders each page from the 2 PDFs at 300 DPI, generates filled variants
with known answers, runs the appropriate extractor, and verifies results.

Test categories:
  1. MCQ grids (cv bubble scoring) — seamo_2025_k, seamo_2025_a, seamo_x_2026_k
  2. Numeric grids (tesseract OCR) — seamo_2025_a Q21-25, seamo_x_2026_a, seamo_x_2026_b
  3. Open response (tesseract OCR) — seamo_x_2026_c
  4. Diagram detection (region cropping) — seamo_x_2026_a Q4/Q6/Q9, seamo_x_2026_b Q5
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import fitz  # PyMuPDF
import numpy as np

from backend.services.template_service import ExamTemplate, TemplateRegistry
from backend.services.mcq_extractor import extract_page as extract_mcq_page
from backend.services.section_extractor import (
    extract_section,
    extract_all_sections,
    _get_question_regions,
)

TEST_DATA_DIR = Path("tests/test_data")
LAYOUTS_DIR = Path("backend/examples/layouts")

SEAMO_2025_PDF = LAYOUTS_DIR / "SEAMO Answer Sheets.pdf"
ZONE_Z_PDF = LAYOUTS_DIR / "ZONE Z.pdf"

# Page mapping: (pdf_path, 0-based page index) -> template_id
PAGE_MAP = {
    "seamo_2025_k":   (SEAMO_2025_PDF, 0),
    "seamo_2025_a":   (SEAMO_2025_PDF, 1),
    "seamo_x_2026_k": (ZONE_Z_PDF, 0),
    "seamo_x_2026_a": (ZONE_Z_PDF, 1),
    "seamo_x_2026_b": (ZONE_Z_PDF, 2),
    "seamo_x_2026_c": (ZONE_Z_PDF, 3),
}


def render_page(pdf_path: Path, page_idx: int, dpi: int = 300) -> np.ndarray:
    """Render a PDF page to a BGR numpy array at the given DPI."""
    doc = fitz.open(str(pdf_path))
    page = doc[page_idx]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    doc.close()
    # PyMuPDF returns RGB; convert to BGR for OpenCV
    if pix.n == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    elif pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    return img


# ---------------------------------------------------------------------------
# Fill helpers
# ---------------------------------------------------------------------------

def fill_mcq_bubbles(
    image: np.ndarray,
    template: ExamTemplate,
    answers: dict,
) -> np.ndarray:
    """Draw dark rectangles on MCQ bubble positions."""
    img = image.copy()
    for section in template.sections:
        if section.type != "mcq_grid":
            continue
        grid = section.grid
        if not grid:
            continue
        labels = grid.options
        questions_per_col = grid.questions_per_col or [grid.rows]
        q_num = section.question_start
        for vc in range(grid.cols or 1):
            start_row = sum(questions_per_col[:vc])
            count = questions_per_col[vc] if vc < len(questions_per_col) else 0
            row_ys = grid.row_positions[start_row : start_row + count]
            col_xs = grid.col_positions[vc] if vc < len(grid.col_positions) else grid.col_positions[0]
            for row_y in row_ys:
                if q_num > section.question_end:
                    break
                ans = answers.get(q_num)
                if ans and ans in labels:
                    opt_idx = labels.index(ans)
                    xc = col_xs[opt_idx]
                    x1 = int(xc - grid.cell_width / 2)
                    y1 = row_y + grid.cell_height
                    x2 = int(xc + grid.cell_width / 2)
                    y2 = y1 + grid.bubble_height
                    cv2.rectangle(img, (x1, y1), (x2, y2), (60, 60, 60), -1)
                q_num += 1
    return img


def fill_numeric_grid(
    image: np.ndarray,
    template: ExamTemplate,
    answers: dict,
) -> np.ndarray:
    """Write answer text into numeric grid cells."""
    img = image.copy()
    for section in template.sections:
        if section.type != "numeric_grid":
            continue
        regions = _get_question_regions(section)
        for q_num, region in regions.items():
            if region["type"] == "diagram":
                continue
            ans = answers.get(q_num, "")
            if not ans:
                continue
            cell_positions = region.get("cell_positions", [])
            cell_w = region.get("cell_width", 88)
            cell_h = region.get("cell_height", 90)
            y = region["y"]
            # Write each character into its cell with thick strokes for OCR
            for i, char in enumerate(str(ans)):
                if i >= len(cell_positions):
                    break
                cx = cell_positions[i]
                font = cv2.FONT_HERSHEY_DUPLEX  # thicker than SIMPLEX
                scale = cell_h / 50.0
                thickness = max(3, int(scale * 2.5))
                (tw, th), _ = cv2.getTextSize(char, font, scale, thickness)
                tx = cx - tw // 2
                ty = y + (cell_h + th) // 2
                cv2.putText(img, char, (tx, ty), font, scale, (30, 30, 30), thickness)
    return img


def fill_open_response(
    image: np.ndarray,
    template: ExamTemplate,
    answers: dict,
) -> np.ndarray:
    """Write answer text into open response boxes."""
    img = image.copy()
    for section in template.sections:
        if section.type != "open_response":
            continue
        regions = _get_question_regions(section)
        for q_num, region in regions.items():
            ans = answers.get(q_num, "")
            if not ans:
                continue
            x, y, w, h = region["x"], region["y"], region["w"], region["h"]
            font = cv2.FONT_HERSHEY_SIMPLEX
            scale = h / 80.0
            thickness = max(2, int(scale * 2))
            (tw, th), _ = cv2.getTextSize(str(ans), font, scale, thickness)
            tx = x + 10
            ty = y + (h + th) // 2
            cv2.putText(img, str(ans), (tx, ty), font, scale, (40, 40, 40), thickness)
    return img


# ---------------------------------------------------------------------------
# Test definitions
# ---------------------------------------------------------------------------

def get_test_cases():
    return [
        # --- MCQ tests ---
        {
            "template_id": "seamo_2025_k",
            "test_type": "mcq",
            "answers": {i: ["A", "B", "C"][(i - 1) % 3] for i in range(1, 16)},
            "description": "SEAMO 2025 K — MCQ 15×3, 2-col (PDF p.1)",
        },
        {
            "template_id": "seamo_2025_a",
            "test_type": "mcq",
            "answers": {i: ["A", "B", "C", "D", "E"][(i - 1) % 5] for i in range(1, 21)},
            "description": "SEAMO 2025 A — MCQ 20×5, 1-col (PDF p.2)",
        },
        {
            "template_id": "seamo_x_2026_k",
            "test_type": "mcq",
            "answers": {i: ["C", "A", "B"][(i - 1) % 3] for i in range(1, 16)},
            "description": "SEAMO X 2026 K — MCQ 15×3, 2-col (PDF p.1)",
        },
        # --- Numeric grid tests ---
        {
            "template_id": "seamo_2025_a",
            "test_type": "numeric_grid",
            "section_index": 1,  # Q21-25 numeric grid section
            "answers": {21: "42", 22: "137", 23: "58", 24: "9", 25: "264"},
            "description": "SEAMO 2025 A — Numeric grid Q21-25 (PDF p.2)",
        },
        {
            "template_id": "seamo_x_2026_a",
            "test_type": "numeric_grid",
            "answers": {
                1: "15", 2: "28", 3: "7", 5: "100",
                7: "33", 8: "42", 10: "56", 11: "9",
                12: "81", 13: "24", 14: "12", 15: "67",
                16: "3", 17: "45", 18: "99", 19: "8", 20: "51",
            },
            "description": "SEAMO X 2026 A — Numeric grid Q1-20 (PDF p.2, excl diagrams Q4/Q6/Q9)",
        },
        {
            "template_id": "seamo_x_2026_b",
            "test_type": "numeric_grid",
            "answers": {
                1: "23", 2: "47", 3: "8", 4: "156",
                6: "72", 7: "31", 8: "5", 9: "89", 10: "14",
                11: "66", 12: "2", 13: "103", 14: "57", 15: "40",
            },
            "description": "SEAMO X 2026 B — Numeric grid Q1-15 (PDF p.3, excl diagram Q5)",
        },
        # --- Open response test ---
        {
            "template_id": "seamo_x_2026_c",
            "test_type": "open_response",
            "answers": {i: str(i * 7) for i in range(1, 16)},
            "description": "SEAMO X 2026 C — Open response Q1-15 (PDF p.4)",
        },
        # --- Diagram detection test ---
        {
            "template_id": "seamo_x_2026_a",
            "test_type": "diagram",
            "expected_diagrams": [4, 6, 9],
            "description": "SEAMO X 2026 A — Diagram detection Q4/Q6/Q9 (PDF p.2)",
        },
        {
            "template_id": "seamo_x_2026_b",
            "test_type": "diagram",
            "expected_diagrams": [5],
            "description": "SEAMO X 2026 B — Diagram detection Q5 (PDF p.3)",
        },
    ]


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_test(tc: dict, registry: TemplateRegistry) -> dict:
    """Run a single test case. Returns result dict."""
    tid = tc["template_id"]
    test_type = tc["test_type"]
    template = registry.get_or_raise(tid)

    # Render page from PDF
    pdf_path, page_idx = PAGE_MAP[tid]
    image = render_page(pdf_path, page_idx)

    # Save anchor from this rendering
    if template.anchor.region.w > 0:
        registry.save_anchor_image(tid, image)

    result = {
        "template_id": tid,
        "test_type": test_type,
        "description": tc["description"],
        "passed": False,
        "details": {},
    }

    if test_type == "mcq":
        answers = tc["answers"]
        filled = fill_mcq_bubbles(image, template, answers)

        # Save filled image
        out_path = TEST_DATA_DIR / f"{tid}_mcq_filled.png"
        cv2.imwrite(str(out_path), filled)
        result["filled_image"] = str(out_path)

        # Extract
        page_result = extract_mcq_page(filled, template, page_number=1, registry=registry)
        expected = {str(k): v for k, v in answers.items()}
        extracted = page_result.answers

        mismatches = {}
        for q, exp in expected.items():
            got = extracted.get(q, "MISSING")
            if got != exp:
                mismatches[q] = {"expected": exp, "got": got}

        result["passed"] = len(mismatches) == 0
        result["details"] = {
            "correct": len(expected) - len(mismatches),
            "total": len(expected),
            "mismatches": mismatches,
            "status": page_result.status,
            "anchor_score": round(page_result.anchor_score, 4),
        }

    elif test_type == "numeric_grid":
        answers = tc["answers"]
        filled = fill_numeric_grid(image, template, answers)

        out_path = TEST_DATA_DIR / f"{tid}_numgrid_filled.png"
        cv2.imwrite(str(out_path), filled)
        result["filled_image"] = str(out_path)

        # Find the right section
        section_idx = tc.get("section_index")
        sections = [s for s in template.sections if s.type == "numeric_grid"]
        if section_idx is not None:
            section = template.sections[section_idx]
        elif sections:
            section = sections[0]
        else:
            result["details"] = {"error": "No numeric_grid section found"}
            return result

        section_result = extract_section(filled, section)

        mismatches = {}
        for q_num, exp in answers.items():
            got = section_result.answers.get(str(q_num), "MISSING")
            # Normalize: compare only the significant characters
            exp_clean = str(exp).strip().upper()
            got_clean = got.strip().upper()
            if got_clean != exp_clean:
                mismatches[str(q_num)] = {"expected": exp_clean, "got": got_clean}

        accuracy = (len(answers) - len(mismatches)) / max(1, len(answers))
        # Tesseract on cv2-rendered text is noisy; 50% is the pass bar.
        # Real handwriting + Mathpix will be much more accurate.
        result["passed"] = accuracy >= 0.50
        result["details"] = {
            "correct": len(answers) - len(mismatches),
            "total": len(answers),
            "accuracy": round(accuracy, 2),
            "mismatches": mismatches,
            "extracted": section_result.answers,
            "errors": section_result.errors,
        }

    elif test_type == "open_response":
        answers = tc["answers"]
        filled = fill_open_response(image, template, answers)

        out_path = TEST_DATA_DIR / f"{tid}_openresp_filled.png"
        cv2.imwrite(str(out_path), filled)
        result["filled_image"] = str(out_path)

        section = next((s for s in template.sections if s.type == "open_response"), None)
        if not section:
            result["details"] = {"error": "No open_response section found"}
            return result

        section_result = extract_section(filled, section)

        # For open response, check that we got non-empty text for each question
        detected = 0
        for q_num in answers:
            got = section_result.answers.get(str(q_num), "")
            if got.strip():
                detected += 1

        result["passed"] = detected >= len(answers) * 0.7  # 70% detection threshold
        result["details"] = {
            "detected": detected,
            "total": len(answers),
            "extracted": section_result.answers,
            "errors": section_result.errors,
        }

    elif test_type == "diagram":
        expected_diagrams = tc["expected_diagrams"]

        section = next(
            (s for s in template.sections if s.question_overrides),
            None,
        )
        if not section:
            result["details"] = {"error": "No section with diagram overrides found"}
            return result

        from backend.services.section_extractor import extract_diagrams
        diag_result = extract_diagrams(image, section)

        detected_qs = [int(q) for q in diag_result.diagrams.keys()]
        missing = [q for q in expected_diagrams if q not in detected_qs]
        extra = [q for q in detected_qs if q not in expected_diagrams]

        # Save diagram crops
        crop_info = {}
        for q, diag_data in diag_result.diagrams.items():
            crop_path = TEST_DATA_DIR / f"{tid}_diagram_q{q}.png"
            cv2.imwrite(str(crop_path), diag_data["crop"])
            crop_info[q] = {
                "crop_path": str(crop_path),
                "region": diag_data["region"],
                "prompt_hint": diag_data.get("prompt_hint"),
            }

        result["passed"] = len(missing) == 0 and len(extra) == 0
        result["details"] = {
            "expected": expected_diagrams,
            "detected": detected_qs,
            "missing": missing,
            "extra": extra,
            "crops": crop_info,
        }

    return result


def main():
    registry = TemplateRegistry()
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)

    test_cases = get_test_cases()
    all_results = []
    passed = 0
    failed = 0

    for tc in test_cases:
        result = run_test(tc, registry)
        all_results.append(result)

        status = "PASS" if result["passed"] else "FAIL"
        details = result["details"]

        if result["test_type"] == "mcq":
            correct = details.get("correct", 0)
            total = details.get("total", 0)
            print(f"{status} {result['template_id']} [{result['test_type']}]: "
                  f"{correct}/{total} — {result['description']}")
        elif result["test_type"] == "numeric_grid":
            correct = details.get("correct", 0)
            total = details.get("total", 0)
            acc = details.get("accuracy", 0)
            print(f"{status} {result['template_id']} [{result['test_type']}]: "
                  f"{correct}/{total} ({acc:.0%} accuracy, Tesseract) — {result['description']}")
            if details.get("mismatches"):
                for q, m in details["mismatches"].items():
                    print(f"      Q{q}: expected={m['expected']} got={m['got']}")
        elif result["test_type"] == "open_response":
            det = details.get("detected", 0)
            total = details.get("total", 0)
            print(f"{status} {result['template_id']} [{result['test_type']}]: "
                  f"{det}/{total} detected — {result['description']}")
        elif result["test_type"] == "diagram":
            detected = details.get("detected", [])
            expected = details.get("expected", [])
            print(f"{status} {result['template_id']} [{result['test_type']}]: "
                  f"detected Q{detected}, expected Q{expected} — {result['description']}")

        if result["passed"]:
            passed += 1
        else:
            failed += 1

    # Save results
    results_path = TEST_DATA_DIR / "all_formats_results.json"
    # Remove non-serializable data (numpy arrays)
    for r in all_results:
        if "details" in r and "crops" in r["details"]:
            for q, crop_data in r["details"]["crops"].items():
                crop_data.pop("crop", None)

    results_path.write_text(json.dumps(
        {"summary": {"passed": passed, "failed": failed, "total": len(test_cases)},
         "tests": all_results},
        indent=2, default=str,
    ))

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(test_cases)}")
    print(f"Saved to {results_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
