"""
PDF to Images Conversion Service
Converts PDF pages to PNG images for OCR processing using PyMuPDF (no external dependencies)
"""
import pymupdf  # PyMuPDF
from typing import List
from PIL import Image
import logging
import tempfile
import os
import io

logger = logging.getLogger(__name__)


class PDFConverter:
    """Converts PDF files to images"""
    
    def __init__(self, dpi: int = 300, fmt: str = 'PNG'):
        """
        Initialize PDF converter
        
        Args:
            dpi: Resolution for image conversion (higher = better quality, slower)
            fmt: Output image format (PNG, JPEG, etc.)
        """
        self.dpi = dpi
        self.fmt = fmt
        logger.info(f"Initialized PDFConverter with DPI={dpi}, format={fmt}")
    
    def convert_from_file(self, pdf_path: str, output_dir: str = None) -> List[str]:
        """
        Convert PDF file to images using PyMuPDF
        
        Args:
            pdf_path: Path to PDF file
            output_dir: Directory to save images (if None, uses temp directory)
            
        Returns:
            List of image file paths
        """
        try:
            # Create output directory if needed
            if output_dir is None:
                output_dir = tempfile.mkdtemp()
            
            os.makedirs(output_dir, exist_ok=True)
            
            # Open PDF
            logger.info(f"Converting PDF: {pdf_path}")
            pdf_document = pymupdf.open(pdf_path)
            
            # Save images to disk
            image_paths = []
            base_name = os.path.splitext(os.path.basename(pdf_path))[0]
            
            # Calculate zoom factor for DPI
            zoom = self.dpi / 72  # 72 is the default DPI
            mat = pymupdf.Matrix(zoom, zoom)
            
            for page_num in range(len(pdf_document)):
                page = pdf_document[page_num]
                pix = page.get_pixmap(matrix=mat)
                
                image_path = os.path.join(output_dir, f"{base_name}_page_{page_num + 1}.{self.fmt.lower()}")
                pix.save(image_path)
                image_paths.append(image_path)
                logger.debug(f"Saved page {page_num + 1} to {image_path}")
            
            pdf_document.close()
            logger.info(f"Successfully converted {len(image_paths)} pages from {pdf_path}")
            return image_paths
            
        except Exception as e:
            logger.error(f"Failed to convert PDF {pdf_path}: {str(e)}")
            raise Exception(f"PDF conversion failed: {str(e)}")
    
    def convert_from_bytes(self, pdf_bytes: bytes, output_dir: str = None, filename_prefix: str = "page") -> List[str]:
        """
        Convert PDF bytes to images
        
        Args:
            pdf_bytes: PDF file as bytes
            output_dir: Directory to save images (if None, uses temp directory)
            filename_prefix: Prefix for output filenames
            
        Returns:
            List of image file paths
        """
        try:
            # Create output directory if needed
            if output_dir is None:
                output_dir = tempfile.mkdtemp()
            
            os.makedirs(output_dir, exist_ok=True)
            
            # Convert PDF bytes to images
            logger.info(f"Converting PDF from bytes")
            images = convert_from_bytes(pdf_bytes, dpi=self.dpi)
            
            # Save images to disk
            image_paths = []
            
            for i, image in enumerate(images, start=1):
                image_path = os.path.join(output_dir, f"{filename_prefix}_{i}.{self.fmt.lower()}")
                image.save(image_path, self.fmt)
                image_paths.append(image_path)
                logger.debug(f"Saved page {i} to {image_path}")
            
            logger.info(f"Successfully converted {len(images)} pages from bytes")
            return image_paths
            
        except Exception as e:
            logger.error(f"Failed to convert PDF bytes: {str(e)}")
            raise Exception(f"PDF conversion failed: {str(e)}")
    
    def get_images_as_pil(self, pdf_path: str) -> List[Image.Image]:
        """
        Convert PDF to PIL Image objects (in-memory, no disk I/O)
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            List of PIL Image objects
        """
        try:
            logger.info(f"Converting PDF to PIL images: {pdf_path}")
            pdf_document = pymupdf.open(pdf_path)
            images = []
            
            # Calculate zoom factor for DPI
            zoom = self.dpi / 72
            mat = pymupdf.Matrix(zoom, zoom)
            
            for page_num in range(len(pdf_document)):
                page = pdf_document[page_num]
                pix = page.get_pixmap(matrix=mat)
                # Convert pixmap to PIL Image
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data))
                images.append(img)
            
            pdf_document.close()
            logger.info(f"Converted {len(images)} pages to PIL images")
            return images
        except Exception as e:
            logger.error(f"Failed to convert PDF to PIL images: {str(e)}")
            raise Exception(f"PDF conversion failed: {str(e)}")
    
    def get_page_count(self, pdf_path: str) -> int:
        """
        Get the number of pages in a PDF without full conversion
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Number of pages
        """
        try:
            pdf_document = pymupdf.open(pdf_path)
            count = len(pdf_document)
            pdf_document.close()
            logger.info(f"PDF {pdf_path} has {count} pages")
            return count
        except Exception as e:
            logger.error(f"Failed to get page count for {pdf_path}: {str(e)}")
            return 0


def get_pdf_converter(dpi: int = 300) -> PDFConverter:
    """Factory function to create PDFConverter instance"""
    return PDFConverter(dpi=dpi)
