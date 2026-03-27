"""
Image Pre-processing Service
Provides functions to analyze and clean up images before they are sent to an OCR or AI service.
"""
from PIL import Image, ImageStat
import logging

logger = logging.getLogger(__name__)

class ImagePreprocessor:
    """A class to perform pre-processing checks on images."""

    @staticmethod
    def is_blank(image_path: str, threshold: float = 10.0) -> bool:
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
                
                # If std dev is very low, it's likely a blank page
                is_blank_page = std_dev < threshold
                if is_blank_page:
                    logger.info(f"Detected blank page: {image_path} (Std Dev: {std_dev:.2f})")
                
                return is_blank_page
        except Exception as e:
            logger.error(f"Could not check if image is blank '{image_path}': {e}")
            # In case of error, assume it's not blank to avoid skipping a valid page
            return False
