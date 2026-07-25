"""
End-to-end test for the template-driven MCQ extraction pipeline.

1. Generates filled exam images by drawing dark rectangles on bubble positions
2. Runs the MCQ extractor against them
3. Verifies extracted answers match ground truth
"""

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

from backend.services.template_service import ExamTemplate, TemplateRegistry
from backend.services.mcq_extractor import extract_page

TEMPLATES_DIR = Path("backend/templates")
REF_IMAGES_DIR = TEMPLATES_DIR / "reference_images"
TEST_DATA_DIR = Path("tests/test_data")

# -----------------------------------------------------------------------
# Test cases: template_id, reference image, known answers
# -----------------------------------------------------------------------

TEST_CASES = [
    {
        "template_id": "seamo_2025_k",
        "ref_image": REF_IMAGES_DIR / "seamo_2025_page1.png",
        "answers": {
            1: "B", 2: "A", 3: "C", 4: "B", 5: "A",
            6: "C", 7: "B", 8: "A", 9: "C", 10: "B",
            11: "A", 12: "C", 13: "B", 14: "A", 15: "C",
        },
        "description": "SEAMO 2025 Paper K — 15 MCQ × 3 options, 2 columns (10+5)",
    },
    {
        "template_id": "seamo_2025_a",
        "ref_image": REF_IMAGES_DIR / "seamo_2025_page2.png",
        "answers": {i: ["A", "B", "C", "D", "E"][(i - 1) % 5] for i in range(1, 21)},
        "description": "SEAMO 2025 Paper A — 20 MCQ × 5 options, 1 column",
    },
    {
        "template_id": "seamo_x_2026_k",
        "ref_image": REF_IMAGES_DIR / "seamo_x_2026_page1.png",
        "answers": {
            1: "C", 2: "B", 3: "A", 4: "C", 5: "B",
            6: "A", 7: "C", 8: "B", 9: "A", 10: "C",
            11: "B", 12: "A", 13: "C", 14: "B", 15: "A",
        },
        "description": "SEAMO X 2026 Paper K — 15 MCQ × 3 options, 2 columns (10+5)",
    },
]


# -----------------------------------------------------------------------
# Fill bubbles on a blank image
# -----------------------------------------------------------------------

def fill_bubbles(
    image: np.ndarray,
    template: ExamTemplate,
    answers: dict,
) -> np.ndarray:
    """Draw dark rectangles on bubble positions for the given answers."""
    img = image.copy()

    for section in template.sections:
        if section.type != "mcq_grid":
            continue

        grid = section.grid
        if not grid:
            continue

        labels = grid.options
        questions_per_col = grid.questions_per_col or [grid.rows]
        question_number = section.question_start
        if grid.row_positions:
            all_row_ys = list(grid.row_positions)
        else:
            base_y = section.region.y + grid.first_row_offset
            all_row_ys = [
                int(base_y + row_index * grid.row_pitch)
                for row_index in range(grid.rows)
            ]

        for vc in range(grid.cols or 1):
            # Row y-positions for this visual column
            start_row = sum(questions_per_col[:vc])
            count = questions_per_col[vc] if vc < len(questions_per_col) else 0
            row_ys = all_row_ys[start_row : start_row + count]

            # Option x-positions for this visual column
            col_xs = grid.col_positions[vc] if vc < len(grid.col_positions) else grid.col_positions[0]

            for row_y in row_ys:
                if question_number > section.question_end:
                    break

                answer = answers.get(question_number)
                if answer and answer in labels:
                    opt_idx = labels.index(answer)
                    x_center = col_xs[opt_idx]

                    x1 = int(x_center - grid.cell_width / 2)
                    y1 = row_y + grid.cell_height
                    x2 = int(x_center + grid.cell_width / 2)
                    y2 = y1 + grid.bubble_height

                    # Dark gray fill — well below the 180 binary threshold
                    cv2.rectangle(img, (x1, y1), (x2, y2), (60, 60, 60), -1)

                question_number += 1

    return img


def test_fill_bubbles_uses_pitch_derived_rows_when_positions_are_empty():
    template = TemplateRegistry().get_or_raise("seamo_2025_a")
    section = next(section for section in template.sections if section.type == "mcq_grid")
    grid = section.grid
    image = np.full(
        (template.page_size[1], template.page_size[0], 3),
        255,
        dtype=np.uint8,
    )

    filled = fill_bubbles(image, template, {question: "A" for question in range(1, 21)})

    x_center = grid.col_positions[0][0]
    expected_row_ys = [
        int(section.region.y + grid.first_row_offset + index * grid.row_pitch)
        for index in range(20)
    ]
    assert all(
        np.array_equal(
            filled[row_y + grid.cell_height + grid.bubble_height // 2, x_center],
            np.array([60, 60, 60], dtype=np.uint8),
        )
        for row_y in expected_row_ys
    )


def test_main_leaves_tracked_artifacts_unchanged():
    artifact_paths = [
        TEST_DATA_DIR / "results.json",
        *(TEST_DATA_DIR / f"{tc['template_id']}_filled.png" for tc in TEST_CASES),
        *(TEMPLATES_DIR / f"{tc['template_id']}_anchor.png" for tc in TEST_CASES),
    ]
    snapshots = {
        path: (path.read_bytes(), path.stat())
        for path in artifact_paths
    }

    try:
        assert main() == 0
        assert all(
            path.read_bytes() == content
            and path.stat().st_mtime_ns == stat.st_mtime_ns
            for path, (content, stat) in snapshots.items()
        )
    finally:
        for path, (content, stat) in snapshots.items():
            if path.read_bytes() != content:
                path.write_bytes(content)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


# -----------------------------------------------------------------------
# Main test runner
# -----------------------------------------------------------------------

def main(output_dir=None):
    if output_dir is not None:
        return _run(Path(output_dir))

    with TemporaryDirectory(prefix="mcq_pipeline_") as temporary_dir:
        return _run(Path(temporary_dir))


def _run(output_dir: Path):
    registry = TemplateRegistry()
    output_dir.mkdir(parents=True, exist_ok=True)

    total_pass = 0
    total_fail = 0
    all_results = []

    for tc in TEST_CASES:
        tid = tc["template_id"]
        template = registry.get_or_raise(tid)
        ref_img = cv2.imread(str(tc["ref_image"]))
        assert ref_img is not None, f"Cannot read {tc['ref_image']}"

        # Generate filled image
        filled = fill_bubbles(ref_img, template, tc["answers"])
        filled_path = output_dir / f"{tid}_filled.png"
        cv2.imwrite(str(filled_path), filled)

        # Run extraction
        result = extract_page(filled, template, page_number=1, registry=registry)

        # Compare
        expected = {str(k): v for k, v in tc["answers"].items()}
        extracted = result.answers
        mismatches = []
        per_question = []

        for q, exp in sorted(expected.items(), key=lambda x: int(x[0])):
            got = extracted.get(q, "MISSING")
            match = got == exp
            per_question.append({"question": int(q), "expected": exp, "got": got, "pass": match})
            if not match:
                mismatches.append(f"  Q{q}: expected={exp} got={got}")

        passed = len(mismatches) == 0
        test_result = {
            "template_id": tid,
            "description": tc["description"],
            "passed": passed,
            "correct": len(expected) - len(mismatches),
            "total": len(expected),
            "filled_image": str(filled_path),
            "diagnostics": result.diagnostics,
            "answers": per_question,
        }
        all_results.append(test_result)

        if mismatches:
            print(f"FAIL {tid}: {len(mismatches)}/{len(expected)} wrong")
            for m in mismatches:
                print(m)
            print(f"  status={result.status} anchor={result.anchor_score:.3f} "
                  f"dx={result.dx} dy={result.dy} coverage={result.coverage:.2f}")
            total_fail += 1
        else:
            print(f"PASS {tid}: {len(expected)}/{len(expected)} correct "
                  f"({tc['description']})")
            total_pass += 1

    # Save results JSON
    results_path = output_dir / "results.json"
    results_json = {
        "summary": {
            "passed": total_pass,
            "failed": total_fail,
            "total": len(TEST_CASES),
        },
        "tests": all_results,
    }
    results_path.write_text(json.dumps(results_json, indent=2))
    print(f"\nResults saved to {results_path}")

    print(f"\n{'='*60}")
    print(f"Results: {total_pass} passed, {total_fail} failed out of {len(TEST_CASES)}")

    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
