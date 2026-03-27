"""
Diagnostic script to check PDF page count and conversion
"""
import fitz  # PyMuPDF
from backend.services.pdf_to_images import PDFConverter

# Test PDF path
pdf_path = "backend/examples/UZ1-35.pdf"

# Check actual page count
print("="*60)
print("PDF DIAGNOSTIC")
print("="*60)
print(f"PDF File: {pdf_path}\n")

# Method 1: Using PyMuPDF directly
pdf_doc = fitz.open(pdf_path)
page_count = len(pdf_doc)
print(f"Method 1 (PyMuPDF): {page_count} pages")
pdf_doc.close()

# Method 2: Using our PDFConverter
print("\nConverting PDF to images...")
converter = PDFConverter()
image_paths = converter.convert_from_file(pdf_path)
print(f"Method 2 (PDFConverter): {len(image_paths)} pages converted")

# List first and last few pages
print(f"\nFirst 3 images:")
for img in image_paths[:3]:
    print(f"  - {img}")

print(f"\nLast 3 images:")
for img in image_paths[-3:]:
    print(f"  - {img}")

print(f"\n✓ Total images created: {len(image_paths)}")

# Cleanup
print("\nCleaning up images...")
from pathlib import Path
for img in image_paths:
    try:
        Path(img).unlink(missing_ok=True)
    except:
        pass
print("✓ Cleanup complete")
