"""
Image Pre-processing Service
Provides functions to analyze and clean up images before they are sent to an OCR or AI service.
"""
from PIL import Image, ImageStat
import cv2
import numpy as np
import logging

logger = logging.getLogger(__name__)


class ImagePreprocessor:
    """A class to perform pre-processing checks on images."""

    @staticmethod
    def is_blank(
        image_path: str,
        threshold: float = 10.0,
        min_ink_ratio: float = 0.0020,
        min_edge_ratio: float = 0.0010,
    ) -> bool:
        """
        Check if an image is likely blank or has very little content.

        It does this by calculating the standard deviation of the pixel values
        in a grayscale version of the image. A low standard deviation suggests
        that the pixels are very uniform in color (e.g., all white or all black).

        Args:
            image_path: The path to the image file.
            threshold: The standard deviation threshold below which an image
                       is considered blank. Defaults to 10.0.

        Returns:
            True if the image is likely blank, False otherwise.
        """
        try:
            with Image.open(image_path) as img:
                # Convert to grayscale for a single channel analysis
                grayscale_img = img.convert('L')
                
                # Get statistics for the grayscale image
                stats = ImageStat.Stat(grayscale_img)
                
                # The standard deviation is in the second position of the tuple
                std_dev = stats.stddev[0]
                
                logger.debug(f"Image: {image_path}, Std Dev: {std_dev:.2f}, Threshold: {threshold}")
                
                gray_np = np.array(grayscale_img)
                ink_mask = cv2.threshold(gray_np, 245, 255, cv2.THRESH_BINARY_INV)[1]
                edge_map = cv2.Canny(gray_np, 70, 170)

                ink_ratio = float(np.count_nonzero(ink_mask)) / float(ink_mask.size)
                edge_ratio = float(np.count_nonzero(edge_map)) / float(edge_map.size)

                # Require low variance + very low ink + very low edges to classify as blank.
                is_blank_page = (
                    std_dev < threshold
                    and ink_ratio < min_ink_ratio
                    and edge_ratio < min_edge_ratio
                )
                if is_blank_page:
                    logger.info(
                        "Detected blank page: %s (std=%.2f, ink=%.5f, edges=%.5f)",
                        image_path,
                        std_dev,
                        ink_ratio,
                        edge_ratio,
                    )
                
                return is_blank_page
        except Exception as e:
            logger.error(f"Could not check if image is blank '{image_path}': {e}")
            # In case of error, assume it's not blank to avoid skipping a valid page
            return False

    @staticmethod
    def preprocess_pil_image(image: Image.Image, mode: str = "balanced") -> Image.Image:
        """
        Improve scan clarity for bubble/text extraction while preserving structure.

        balanced: moderate enhancement for most scans
        aggressive: stronger thresholding for faint/low-contrast scans
        """
        rgb_image = image.convert("RGB")
        bgr = cv2.cvtColor(np.array(rgb_image), cv2.COLOR_RGB2BGR)
        normalized = ImagePreprocessor._normalize_size(bgr)

        gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
        denoised = cv2.medianBlur(gray, 3)

        clip_limit = 2.5 if mode == "aggressive" else 2.0
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
        contrast = clahe.apply(denoised)

        block_size = 29 if mode == "aggressive" else 35
        c_value = 11 if mode == "aggressive" else 9
        adaptive = cv2.adaptiveThreshold(
            contrast,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block_size,
            c_value,
        )

        kernel = np.ones((2, 2), np.uint8)
        clean = cv2.morphologyEx(adaptive, cv2.MORPH_OPEN, kernel, iterations=1)

        binary_weight = 0.45 if mode == "aggressive" else 0.30
        merged = cv2.addWeighted(contrast, 1.0 - binary_weight, clean, binary_weight, 0)

        sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        sharpened = cv2.filter2D(merged, -1, sharpen_kernel)
        final_rgb = cv2.cvtColor(sharpened, cv2.COLOR_GRAY2RGB)
        return Image.fromarray(final_rgb)

    @staticmethod
    def preprocess_image_path(image_path: str, mode: str = "balanced") -> Image.Image:
        """Load and preprocess an image path for extraction."""
        with Image.open(image_path) as image:
            return ImagePreprocessor.preprocess_pil_image(image, mode=mode)

    @staticmethod
    def _normalize_size(
        image: np.ndarray,
        min_long_edge: int = 1800,
        max_long_edge: int = 2600,
    ) -> np.ndarray:
        """
        Normalize long edge into a stable range.
        Upscaling low-res scans helps bubble and digit visibility.
        """
        height, width = image.shape[:2]
        long_edge = max(height, width)

        scale = 1.0
        if long_edge < min_long_edge:
            scale = min_long_edge / float(long_edge)
        elif long_edge > max_long_edge:
            scale = max_long_edge / float(long_edge)

        if abs(scale - 1.0) < 0.01:
            return image

        new_width = max(1, int(round(width * scale)))
        new_height = max(1, int(round(height * scale)))
        return cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_CUBIC)
