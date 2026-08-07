"""Acceptance test for answer-key-blind Format-B MCQ extraction."""

from pathlib import Path

import cv2

from backend.services.gold_eval import load_gold_pages
from backend.services.mcq_extractor import extract_page
from backend.services.page_deskew import deskew_if_enabled
from backend.services.template_service import get_template_registry


GOLD_ROOT = Path("tests/fixtures/gold/seamo_2025_format_b")


def test_format_b_cv_extracts_every_mcq_exactly():
    """The production CV path must reproduce all visible Q1-Q20 marks."""
    registry = get_template_registry()
    errors = []

    for gold in load_gold_pages(GOLD_ROOT):
        image_path = Path(gold["_path"]).with_suffix(".png")
        image = cv2.imread(str(image_path))
        assert image is not None, image_path
        image, _ = deskew_if_enabled(image, enabled=True)
        template = registry.get_or_raise(gold["template_id"])
        adapted, _ = template.adapted_to_image(image)

        result = extract_page(
            image,
            adapted,
            page_number=1,
            registry=registry,
            deskew=False,
        )
        predicted = result.answers
        for question in range(1, 21):
            expected = str(gold["answers"][str(question)]).upper()
            actual = str(predicted.get(str(question), "BL")).upper()
            if actual != expected:
                errors.append(
                    (gold["page_id"], question, expected, actual, result.warning)
                )

    assert not errors, errors
