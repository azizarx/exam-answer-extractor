"""Programmatic scan degradations for CV stress-testing against gold pages."""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

import cv2
import numpy as np


def rotate(image: np.ndarray, degrees: float) -> np.ndarray:
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), degrees, 1.0)
    return cv2.warpAffine(image, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)


def crop_margins(image: np.ndarray, fraction: float = 0.03) -> np.ndarray:
    h, w = image.shape[:2]
    dx, dy = int(w * fraction), int(h * fraction)
    cropped = image[dy : h - dy, dx : w - dx]
    return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)


def jpeg_degrade(image: np.ndarray, quality: int = 35) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return image
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def downscale_up(image: np.ndarray, scale: float = 0.55) -> np.ndarray:
    h, w = image.shape[:2]
    small = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))))
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


DEFAULT_STRESS: List[Tuple[str, Callable[[np.ndarray], np.ndarray]]] = [
    ("identity", lambda im: im.copy()),
    ("rotate_p2", lambda im: rotate(im, 2.0)),
    ("rotate_m2", lambda im: rotate(im, -2.0)),
    ("rotate_p5", lambda im: rotate(im, 5.0)),
    ("crop_3pct", lambda im: crop_margins(im, 0.03)),
    ("jpeg_35", lambda im: jpeg_degrade(im, 35)),
    ("downscale_55", lambda im: downscale_up(im, 0.55)),
]


def apply_named(image: np.ndarray, name: str) -> np.ndarray:
    for n, fn in DEFAULT_STRESS:
        if n == name:
            return fn(image)
    raise KeyError(f"Unknown stress transform: {name}")


def all_stress_names() -> List[str]:
    return [n for n, _ in DEFAULT_STRESS]
