"""
Test dynamic format detection with both UZ1 and ZONE Z PDFs.
Converts the first page of each PDF to an image and runs format analysis.
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))

from backend.services.pdf_to_images import get_pdf_converter
from backend.services.ai_extractor import get_ai_extractor

def test_format_detection(pdf_name: str):
    """Test format analysis on the first page of a PDF."""
    pdf_path = os.path.join("backend", "examples", pdf_name)
    if not os.path.exists(pdf_path):
        print(f"  ❌ {pdf_name} not found at {pdf_path}")
        return

    print(f"\n{'='*60}")
    print(f"  Testing format detection: {pdf_name}")
    print(f"{'='*60}")

    # Convert first page to image
    converter = get_pdf_converter()
    images = converter.convert_from_file(pdf_path)
    print(f"  Pages: {len(images)}")

    if not images:
        print("  ❌ No images converted")
        return

    # Analyze format from first page
    extractor = get_ai_extractor()
    t0 = time.time()
    fmt = extractor.analyze_format(images[0])
    t1 = time.time()

    print(f"  ⏱️  Format analysis took {t1-t0:.1f}s")
    print(f"  📋 Description: {fmt.get('description', 'N/A')}")
    print(f"  📝 Header fields:")
    for f in fmt.get("header_fields", []):
        print(f"      - {f['key']} ({f['label']})")
    print(f"  📊 MCQ range: {fmt.get('mcq_range', 'N/A')}")
    print(f"  🎨 Drawing range: {fmt.get('drawing_range', 'N/A')}")
    print(f"  🔤 Answer options: {fmt.get('answer_options', 'N/A')}")

    # Build and show the prompt
    prompt = extractor.build_extraction_prompt(fmt)
    print(f"\n  📄 Generated extraction prompt ({len(prompt)} chars):")
    print("  " + "-"*40)
    for line in prompt.split("\n")[:20]:
        print(f"  {line}")
    print("  ...")

    # Cleanup images
    for img in images:
        try:
            os.unlink(img)
        except Exception:
            pass

    return fmt

if __name__ == "__main__":
    print("Dynamic Format Detection Test")
    print("="*60)

    fmt_uz1 = test_format_detection("UZ1-35.pdf")
    fmt_zone = test_format_detection("ZONE Z.pdf")

    if fmt_uz1 and fmt_zone:
        print(f"\n{'='*60}")
        print("  COMPARISON")
        print(f"{'='*60}")
        print(f"  UZ1 fields:  {[f['key'] for f in fmt_uz1.get('header_fields', [])]}")
        print(f"  ZONE fields: {[f['key'] for f in fmt_zone.get('header_fields', [])]}")
        print(f"  UZ1 MCQ:     {fmt_uz1.get('mcq_range')}")
        print(f"  ZONE MCQ:    {fmt_zone.get('mcq_range')}")
