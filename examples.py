"""
Example usage of the exam extraction services
Demonstrates how to use the services programmatically (without API)
"""
import asyncio
from pathlib import Path
import json

from backend.services.local_storage import get_local_storage
from backend.services.pdf_to_images import get_pdf_converter
from backend.services.ocr_engine import get_ocr_engine, AnswerParser
from backend.services.ai_extractor import get_ai_extractor
from backend.services.json_generator import get_json_generator


def example_1_basic_ocr_extraction(pdf_path: str):
    """
    Example 1: Basic OCR extraction workflow
    Uses Tesseract OCR for text extraction
    """
    print("\n" + "="*60)
    print("EXAMPLE 1: Basic OCR Extraction")
    print("="*60)
    
    # Step 1: Convert PDF to images
    print("\n1️⃣ Converting PDF to images...")
    pdf_converter = get_pdf_converter(dpi=300)
    image_paths = pdf_converter.convert_from_file(pdf_path)
    print(f"   Created {len(image_paths)} images")
    
    # Step 2: Extract text with OCR
    print("\n2️⃣ Extracting text with OCR...")
    ocr_engine = get_ocr_engine()
    all_text = ""
    for i, image_path in enumerate(image_paths, 1):
        print(f"   Processing page {i}/{len(image_paths)}...")
        text = ocr_engine.extract_text(image_path)
        all_text += text + "\n\n"
    
    print(f"   Extracted {len(all_text)} characters")
    
    # Step 3: Parse answers
    print("\n3️⃣ Parsing answers...")
    parser = AnswerParser()
    results = parser.extract_all(all_text)
    
    print(f"   Found {len(results['multiple_choice'])} MCQ answers")
    print(f"   Found {len(results['free_response'])} free response answers")
    
    # Step 4: Generate JSON
    print("\n4️⃣ Generating JSON...")
    json_generator = get_json_generator()
    json_output = json_generator.generate(
        filename=Path(pdf_path).name,
        multiple_choice=results['multiple_choice'],
        free_response=results['free_response'],
        metadata={"method": "ocr", "pages": len(image_paths)}
    )
    
    print("   JSON generated successfully")
    print("\n📄 Sample output:")
    print(json_output[:500] + "...")
    
    return json_output


def example_2_ai_vision_extraction(pdf_path: str):
    """
    Example 2: AI Vision extraction workflow
    Uses OpenAI GPT-4 Vision API for intelligent extraction
    """
    print("\n" + "="*60)
    print("EXAMPLE 2: AI Vision Extraction")
    print("="*60)
    
    # Step 1: Convert PDF to images
    print("\n1️⃣ Converting PDF to images...")
    pdf_converter = get_pdf_converter(dpi=300)
    image_paths = pdf_converter.convert_from_file(pdf_path)
    print(f"   Created {len(image_paths)} images")
    
    # Step 2: Extract with AI
    print("\n2️⃣ Extracting with AI Vision...")
    ai_extractor = get_ai_extractor()
    extraction_result = ai_extractor.extract_from_multiple_images(image_paths)
    
    print(f"   Found {len(extraction_result['multiple_choice'])} MCQ answers")
    print(f"   Found {len(extraction_result['free_response'])} free response answers")
    
    # Step 3: Validate
    print("\n3️⃣ Validating extraction...")
    validation_result = ai_extractor.validate_extraction(extraction_result)
    
    if validation_result['is_valid']:
        print("   ✅ Validation passed")
    else:
        print("   ⚠️  Validation warnings:")
        for warning in validation_result['warnings']:
            print(f"      - {warning}")
    
    # Step 4: Generate JSON
    print("\n4️⃣ Generating JSON with validation...")
    json_generator = get_json_generator()
    json_output = json_generator.generate_with_validation(
        filename=Path(pdf_path).name,
        extraction_result=extraction_result,
        validation_result=validation_result
    )
    
    print("   JSON generated successfully")
    print("\n📄 Sample output:")
    parsed = json.loads(json_output)
    print(f"   Multiple Choice: {parsed['total_multiple_choice']}")
    print(f"   Free Response: {parsed['total_free_response']}")
    
    return json_output


def example_3_save_results_locally(json_data: str, filename: str):
    """
    Example 3: Save structured results to local storage
    """
    print("\n" + "="*60)
    print("EXAMPLE 3: Save Results Locally")
    print("="*60)

    storage = get_local_storage()
    print("\n1️⃣ Persisting JSON to local storage...")
    result = storage.save_json(json_data, filename)
    print("   ✅ Saved successfully!")
    print(f"   Relative Path: {result['relative_path']}")
    print(f"   Absolute Path: {result['absolute_path']}")

    print("\n2️⃣ Reading JSON back...")
    raw = storage.read_json(result['relative_path'])
    snippet = raw[:200] + "..." if raw and len(raw) > 200 else raw
    print(f"   Preview: {snippet}")

    return result


def example_4_complete_workflow(pdf_path: str):
    """Complete workflow: PDF → Extract → Validate → Save JSON (local)."""
    print("\n" + "="*60)
    print("EXAMPLE 4: Complete Workflow")
    print("="*60)
    try:
        print("\n📄 Converting PDF...")
        pdf_converter = get_pdf_converter()
        image_paths = pdf_converter.convert_from_file(pdf_path)
        print(f"   Generated {len(image_paths)} page images")
        print("🤖 Extracting with AI...")
        ai_extractor = get_ai_extractor()
        extraction = ai_extractor.extract_from_multiple_images(image_paths)
        print("✅ Validating...")
        validation = ai_extractor.validate_extraction(extraction)
        print("📝 Generating JSON...")
        json_gen = get_json_generator()
        json_data = json_gen.generate_with_validation(Path(pdf_path).name, extraction, validation)
        print("💾 Saving results locally...")
        json_filename = f"{Path(pdf_path).stem}_results.json"
        upload_result = get_local_storage().save_json(json_data, json_filename)
        print("\n" + "="*60)
        print("✨ Workflow Complete!")
        print("="*60)
        print(f"📊 MCQ Answers: {len(extraction['multiple_choice'])}")
        print(f"✍️  Free Response: {len(extraction['free_response'])}")
        print(f"🗂️  Stored JSON: {upload_result['relative_path']}")
        if extraction['multiple_choice']:
            print("\n📝 Sample MCQ Answers:")
            for mcq in extraction['multiple_choice'][:5]:
                print(f"   Q{mcq['question']}: {mcq['answer']}")
        return {
            "extraction": extraction,
            "validation": validation,
            "json_data": json_data,
            "upload_result": upload_result
        }
    except Exception as e:
        print(f"\n❌ Workflow failed: {e}")
        return None


def main():
    """Main example runner"""
    print("\n" + "="*70)
    print("  EXAM EXTRACTION SYSTEM - PROGRAMMATIC USAGE EXAMPLES")
    print("="*70)
    
    # Check for test PDF
    test_pdf = "test_exam.pdf"
    if not Path(test_pdf).exists():
        print(f"\n⚠️  Test PDF not found: {test_pdf}")
        print("Please create or download a test PDF exam sheet")
        print("\nYou can:")
        print("1. Create a simple PDF with exam answers")
        print("2. Update the test_pdf variable with your PDF path")
        return
    
    print(f"\n✅ Using test PDF: {test_pdf}\n")
    
    # Run examples
    try:
        # Example 1: OCR extraction
        print("\n" + "🔄 Running Example 1...")
        json_output_ocr = example_1_basic_ocr_extraction(test_pdf)
        
        input("\nPress Enter to continue to Example 2...")
        
        # Example 2: AI Vision extraction
        print("\n" + "🔄 Running Example 2...")
        json_output_ai = example_2_ai_vision_extraction(test_pdf)
        
        input("\nPress Enter to continue to Example 3...")
        
        print("\n" + "🔄 Running Example 3...")
        example_3_save_results_locally(json_output_ai, "example_results.json")
        
        input("\nPress Enter to continue to Example 4...")
        
        # Example 4: Complete workflow
        print("\n" + "🔄 Running Example 4...")
        result = example_4_complete_workflow(test_pdf)
        
        print("\n" + "="*70)
        print("  ALL EXAMPLES COMPLETED!")
        print("="*70)
        print("\nThese examples show how to use the services programmatically.")
        print("For production use, prefer the REST API (see README.md)")
        
    except KeyboardInterrupt:
        print("\n\n⏸️  Examples interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Example failed: {str(e)}")
        print("Make sure:")
        print("  1. All dependencies are installed")
        print("  2. .env file is configured correctly")
        print("  3. Tesseract and Poppler are installed")
        print("  4. OpenAI API key is valid")


if __name__ == "__main__":
    main()
