"""
Test script to compare sequential vs parallel extraction performance
"""
import time
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath('.'))

from backend.services.ai_extractor import AIExtractor
from backend.services.pdf_to_images import PDFConverter
from backend.config import get_settings

def test_extraction_performance():
    """Compare sequential vs parallel extraction speeds"""
    
    # Test PDF path
    test_pdf = "backend/examples/UZ1-35.pdf"
    
    if not os.path.exists(test_pdf):
        print(f"❌ Test PDF not found: {test_pdf}")
        return
    
    print("=" * 70)
    print("EXTRACTION PERFORMANCE TEST")
    print("=" * 70)
    print(f"Test PDF: {test_pdf}\n")
    
    # Convert PDF to images first
    print("Converting PDF to images...")
    pdf_converter = PDFConverter()
    image_paths = pdf_converter.convert_from_file(test_pdf)
    print(f"✓ Converted to {len(image_paths)} images\n")
    
    # Initialize AI extractor
    ai_extractor = AIExtractor()
    
    # Test 1: Sequential processing
    print("-" * 70)
    print("TEST 1: Sequential Processing (use_parallel=False)")
    print("-" * 70)
    start_time = time.time()
    result_sequential = ai_extractor.extract_from_multiple_images(
        image_paths,
        use_parallel=False
    )
    sequential_time = time.time() - start_time
    
    print(f"✓ Completed in {sequential_time:.2f} seconds")
    print(f"  - MCQ answers: {len(result_sequential.get('multiple_choice', []))}")
    print(f"  - Free response: {len(result_sequential.get('free_response', []))}")
    print(f"  - Pages processed: {result_sequential.get('pages_processed', 0)}")
    print(f"  - Average time per page: {sequential_time/len(image_paths):.2f}s\n")
    
    # Test 2: Parallel processing (4 workers)
    print("-" * 70)
    print("TEST 2: Parallel Processing (use_parallel=True, max_workers=4)")
    print("-" * 70)
    start_time = time.time()
    result_parallel = ai_extractor.extract_from_multiple_images(
        image_paths,
        use_parallel=True,
        max_workers=4
    )
    parallel_time = time.time() - start_time
    
    print(f"✓ Completed in {parallel_time:.2f} seconds")
    print(f"  - MCQ answers: {len(result_parallel.get('multiple_choice', []))}")
    print(f"  - Free response: {len(result_parallel.get('free_response', []))}")
    print(f"  - Pages processed: {result_parallel.get('pages_processed', 0)}")
    print(f"  - Average time per page: {parallel_time/len(image_paths):.2f}s\n")
    
    # Calculate speedup
    speedup = sequential_time / parallel_time if parallel_time > 0 else 0
    time_saved = sequential_time - parallel_time
    percent_faster = ((sequential_time - parallel_time) / sequential_time * 100) if sequential_time > 0 else 0
    
    print("=" * 70)
    print("PERFORMANCE SUMMARY")
    print("=" * 70)
    print(f"Sequential time:  {sequential_time:.2f}s")
    print(f"Parallel time:    {parallel_time:.2f}s")
    print(f"Time saved:       {time_saved:.2f}s")
    print(f"Speedup:          {speedup:.2f}x faster")
    print(f"Improvement:      {percent_faster:.1f}% faster")
    print("=" * 70)
    
    # Cleanup
    print("\nCleaning up temporary images...")
    from pathlib import Path
    for img in image_paths:
        try:
            Path(img).unlink(missing_ok=True)
        except Exception as e:
            print(f"Warning: Could not delete {img}: {e}")
    
    print("✓ Test completed!")

if __name__ == "__main__":
    test_extraction_performance()
