"""
PDF to Images Conversion Service
Converts PDF pages to PNG images for OCR processing using PyMuPDF (no external dependencies)
"""
import pymupdf  # PyMuPDF
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple
from PIL import Image
import logging
import tempfile
import os
import io

logger = logging.getLogger(__name__)


def _render_page_chunk(
    pdf_path: str,
    page_indices: List[int],
    image_paths: List[str],
    dpi: int,
) -> int:
    """Render a contiguous set of pages with one document handle."""
    zoom = dpi / 72.0
    mat = pymupdf.Matrix(zoom, zoom)
    doc = pymupdf.open(pdf_path)
    try:
        for i in page_indices:
            pix = doc[i].get_pixmap(matrix=mat)
            pix.save(image_paths[i])
    finally:
        doc.close()
    return len(page_indices)


def _chunk_indices(n_pages: int, n_chunks: int) -> List[List[int]]:
    n_chunks = max(1, min(n_chunks, n_pages))
    size = (n_pages + n_chunks - 1) // n_chunks
    return [list(range(i, min(i + size, n_pages))) for i in range(0, n_pages, size)]


class PDFConverter:
    """Converts PDF files to images"""

    def __init__(self, dpi: int = 200, fmt: str = "PNG", max_workers: Optional[int] = None):
        self.dpi = dpi
        self.fmt = fmt
        if max_workers is None:
            try:
                from backend.config import get_settings
                max_workers = int(get_settings().max_pdf_render_workers)
            except Exception:
                max_workers = 4
        self.max_workers = max(1, int(max_workers))
        logger.info(
            "Initialized PDFConverter with DPI=%s, format=%s, workers=%s",
            dpi, fmt, self.max_workers,
        )

    def convert_from_file(self, pdf_path: str, output_dir: str = None) -> List[str]:
        """Convert PDF file to images using PyMuPDF (chunk-parallel)."""
        try:
            if output_dir is None:
                output_dir = tempfile.mkdtemp()

            os.makedirs(output_dir, exist_ok=True)

            logger.info("Converting PDF: %s", pdf_path)
            with pymupdf.open(pdf_path) as pdf_document:
                n_pages = len(pdf_document)

            base_name = os.path.splitext(os.path.basename(pdf_path))[0]
            image_paths = [
                os.path.join(output_dir, f"{base_name}_page_{i + 1}.{self.fmt.lower()}")
                for i in range(n_pages)
            ]

            chunks = _chunk_indices(n_pages, self.max_workers)
            if len(chunks) == 1:
                _render_page_chunk(pdf_path, chunks[0], image_paths, self.dpi)
            else:
                with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
                    futs = [
                        pool.submit(_render_page_chunk, pdf_path, chunk, image_paths, self.dpi)
                        for chunk in chunks
                    ]
                    for fut in as_completed(futs):
                        fut.result()

            logger.info("Successfully converted %s pages from %s", len(image_paths), pdf_path)
            return image_paths

        except Exception as e:
            logger.error("Failed to convert PDF %s: %s", pdf_path, e)
            raise Exception(f"PDF conversion failed: {str(e)}")

    def convert_from_bytes(
        self, pdf_bytes: bytes, output_dir: str = None, filename_prefix: str = "page"
    ) -> List[str]:
        """Convert PDF bytes to images using PyMuPDF."""
        try:
            if output_dir is None:
                output_dir = tempfile.mkdtemp()

            os.makedirs(output_dir, exist_ok=True)

            tmp_pdf = os.path.join(output_dir, f"{filename_prefix}_src.pdf")
            with open(tmp_pdf, "wb") as fh:
                fh.write(pdf_bytes)
            paths = self.convert_from_file(tmp_pdf, output_dir=output_dir)
            renamed = []
            for i, p in enumerate(paths):
                dest = os.path.join(
                    output_dir, f"{filename_prefix}_{i + 1}.{self.fmt.lower()}"
                )
                if os.path.abspath(p) != os.path.abspath(dest):
                    os.replace(p, dest)
                renamed.append(dest)
            try:
                os.remove(tmp_pdf)
            except OSError:
                pass
            return renamed

        except Exception as e:
            logger.error("Failed to convert PDF bytes: %s", e)
            raise Exception(f"PDF conversion failed: {str(e)}")

    def get_images_as_pil(self, pdf_path: str) -> List[Image.Image]:
        """Convert PDF to PIL Image objects (in-memory, no disk I/O)."""
        try:
            logger.info("Converting PDF to PIL images: %s", pdf_path)
            pdf_document = pymupdf.open(pdf_path)
            images = []

            zoom = self.dpi / 72
            mat = pymupdf.Matrix(zoom, zoom)

            for page_num in range(len(pdf_document)):
                page = pdf_document[page_num]
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data))
                images.append(img)

            pdf_document.close()
            logger.info("Converted %s pages to PIL images", len(images))
            return images
        except Exception as e:
            logger.error("Failed to convert PDF to PIL images: %s", e)
            raise Exception(f"PDF conversion failed: {str(e)}")

    def get_page_count(self, pdf_path: str) -> int:
        """Get the number of pages in a PDF without full conversion."""
        try:
            pdf_document = pymupdf.open(pdf_path)
            count = len(pdf_document)
            pdf_document.close()
            logger.info("PDF %s has %s pages", pdf_path, count)
            return count
        except Exception as e:
            logger.error("Failed to get page count for %s: %s", pdf_path, e)
            return 0


def get_pdf_converter(dpi: int = 300) -> PDFConverter:
    """Factory function to create PDFConverter instance"""
    return PDFConverter(dpi=dpi)
