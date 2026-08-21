"""Cropping a diagram question's drawing out of a scanned page."""

import cv2
import numpy as np
import pytest

from backend.services.diagram_crops import (
    CROP_NAME_RE,
    crop_filename,
    extract_diagram_crops,
)
from backend.services.template_service import get_template_registry


DIAGRAM_TEMPLATES = {
    "seamo_x_2026_a": [4, 6, 9],
    "seamo_x_2026_b": [5],
}


def _blank_reference(template_id: str) -> np.ndarray:
    page = {
        "seamo_x_2026_a": "seamo_x_2026_page2.png",
        "seamo_x_2026_b": "seamo_x_2026_page3.png",
    }[template_id]
    image = cv2.imread(f"backend/templates/reference_images/{page}")
    assert image is not None, f"missing reference scan {page}"
    return image


def test_crop_filename_matches_the_pattern_the_api_will_accept():
    name = crop_filename(3, 9)
    assert name == "p3_q9.png"
    assert CROP_NAME_RE.match(name)


@pytest.mark.parametrize("hostile", ["../escape.png", "p3_q9.png/../x", "p3_q9.jpg", "p-1_q9.png"])
def test_crop_name_pattern_rejects_anything_not_generated_here(hostile):
    assert not CROP_NAME_RE.match(hostile)


@pytest.mark.parametrize(("template_id", "questions"), DIAGRAM_TEMPLATES.items())
def test_every_diagram_question_produces_a_crop(tmp_path, template_id, questions):
    template = get_template_registry().get_or_raise(template_id)
    bgr = _blank_reference(template_id)
    page_template, _scale = template.adapted_to_image(bgr)

    saved = extract_diagram_crops(bgr, page_template, 3, tmp_path)

    assert sorted(int(q) for q in saved) == sorted(questions)
    for question, entry in saved.items():
        assert entry["source"] == "template_region"
        path = tmp_path / entry["file"]
        assert path.is_file()
        assert path.name == crop_filename(3, int(question))


@pytest.mark.parametrize(("template_id", "questions"), DIAGRAM_TEMPLATES.items())
def test_crop_frames_the_whole_scaffold_not_just_its_top(tmp_path, template_id, questions):
    """The fixed boxes this replaced clipped the drawing in half.

    A crop that cuts the clock's hands or the grid's lower row is worse than
    useless for comparison, so assert ink reaches the bottom third.
    """
    template = get_template_registry().get_or_raise(template_id)
    bgr = _blank_reference(template_id)
    page_template, _scale = template.adapted_to_image(bgr)
    saved = extract_diagram_crops(bgr, page_template, 1, tmp_path)

    for question in questions:
        crop = cv2.imread(str(tmp_path / saved[str(question)]["file"]), cv2.IMREAD_GRAYSCALE)
        assert crop is not None
        height, width = crop.shape
        assert height > 150 and width > 150, f"Q{question} crop is too small: {width}x{height}"
        ink = crop < 200
        bottom_third = ink[int(height * 0.66):, :]
        assert bottom_third.any(), f"Q{question} crop is cut off above the drawing"


def test_no_crop_dir_means_no_work_and_no_crash():
    template = get_template_registry().get_or_raise("seamo_x_2026_a")
    bgr = _blank_reference("seamo_x_2026_a")
    page_template, _scale = template.adapted_to_image(bgr)
    assert extract_diagram_crops(bgr, page_template, 1, None) == {}


def test_blank_page_still_yields_a_crop_from_the_search_band(tmp_path):
    """A page with no scaffold found falls back to the band, never to nothing.

    Marking distinguishes "no crop stored" from "crop shows nothing", so the
    crop step must not silently produce the former for a readable page.
    """
    template = get_template_registry().get_or_raise("seamo_x_2026_a")
    white = np.full((3510, 2482, 3), 255, dtype=np.uint8)
    page_template, _scale = template.adapted_to_image(white)

    saved = extract_diagram_crops(white, page_template, 2, tmp_path)

    assert sorted(int(q) for q in saved) == [4, 6, 9]
    for entry in saved.values():
        assert (tmp_path / entry["file"]).is_file()
