from __future__ import annotations

import cv2
import numpy as np

from backend.services.diagram_cv import extract_seamo_x_a_q9


def _sheet(*filled_sectors: int) -> np.ndarray:
    height, width = 2200, 1600
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    center = (round(0.43 * width), round(0.55 * height))
    radius = 105
    for sector in filled_sectors:
        cv2.ellipse(
            image,
            center,
            (radius - 3, radius - 3),
            0,
            sector * 30 + 1,
            sector * 30 + 29,
            (0, 0, 0),
            -1,
        )
    cv2.circle(image, center, radius, (0, 0, 0), 3)
    for angle in range(0, 360, 30):
        radians = np.deg2rad(angle)
        endpoint = (
            round(center[0] + radius * np.cos(radians)),
            round(center[1] + radius * np.sin(radians)),
        )
        cv2.line(image, center, endpoint, (0, 0, 0), 2)
    return image


def test_sector_cv_returns_exact_clock_interval():
    result = extract_seamo_x_a_q9(_sheet(1))

    assert result.status == "ok"
    assert result.sectors == (1,)
    assert result.answer == "Pie chart: shaded sector 4-5 o'clock"


def test_sector_cv_distinguishes_adjacent_and_multiple_sectors():
    adjacent = extract_seamo_x_a_q9(_sheet(2))
    multiple = extract_seamo_x_a_q9(_sheet(1, 4))

    assert adjacent.sectors == (2,)
    assert adjacent.answer == "Pie chart: shaded sector 5-6 o'clock"
    assert multiple.sectors == (1, 4)
    assert multiple.answer == "Pie chart: shaded sectors 4-5 and 7-8 o'clock"


def test_sector_cv_reports_blank_without_inventing_shading():
    result = extract_seamo_x_a_q9(_sheet())

    assert result.status == "blank"
    assert result.answer == "BL"
    assert result.sectors == ()
