"""
Example usage of the exam extraction services
Demonstrates how to use the services programmatically (without API)
"""
import asyncio
from pathlib import Path
import json

from backend.services.space_client import get_spaces_client
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


def example_3_upload_to_spaces(json_data: str, filename: str):
    """
    Example 3: Upload results to DigitalOcean Spaces
    """
    print("\n" + "="*60)
    print("EXAMPLE 3: Upload to DigitalOcean Spaces")
    print("="*60)
    
    try:
        # Initialize Spaces client
        print("\n1️⃣ Connecting to DigitalOcean Spaces...")
        spaces_client = get_spaces_client()
        
        # Upload JSON
        print("\n2️⃣ Uploading JSON results...")
        result = spaces_client.upload_json(json_data, filename)
        
        print("   ✅ Upload successful!")
        print(f"   File: {result['filename']}")
        print(f"   Key: {result['key']}")
        print(f"   URL: {result['url']}")
        
        # List files
        print("\n3️⃣ Listing files in Spaces...")
        files = spaces_client.list_files("results/")
        print(f"   Found {len(files)} result files")
        for file in files[:5]:
            print(f"      - {file}")
        
        return result
        
    except Exception as e:
        print(f"   ❌ Upload failed: {str(e)}")
        print("   Make sure your Spaces credentials are configured in .env")
        return None


def example_4_complete_workflow(pdf_path: str):
    """
    Example 4: Complete end-to-end workflow
    PDF → Extract → Validate → Upload → Return results
    """
    print("\n" + "="*60)
    print("EXAMPLE 4: Complete Workflow")
    print("="*60)
    
    try:
        # 1. Convert PDF
        print("\n📄 Converting PDF...")
        pdf_converter = get_pdf_converter()
        image_paths = pdf_converter.convert_from_file(pdf_path)
        
        # 2. Extract with AI (better accuracy)
        print("🤖 Extracting with AI...")
        ai_extractor = get_ai_extractor()
        extraction = ai_extractor.extract_from_multiple_images(image_paths)
        
        # 3. Validate
        print("✅ Validating...")
        validation = ai_extractor.validate_extraction(extraction)
        
        # 4. Generate JSON
        print("📝 Generating JSON...")
        json_gen = get_json_generator()
        json_data = json_gen.generate_with_validation(
            Path(pdf_path).name,
            extraction,
            validation
        )
        
        # 5. Upload to Spaces
        print("☁️  Uploading to Spaces...")
        json_filename = f"{Path(pdf_path).stem}_results.json"
        upload_result = get_spaces_client().upload_json(json_data, json_filename)
        
        # 6. Display summary
        print("\n" + "="*60)
        print("✨ Workflow Complete!")
        print("="*60)
        print(f"📊 MCQ Answers: {len(extraction['multiple_choice'])}")
        print(f"✍️  Free Response: {len(extraction['free_response'])}")
        print(f"🔗 JSON URL: {upload_result['url']}")
        
        # Display sample answers
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
        print(f"\n❌ Workflow failed: {str(e)}")
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
        
        # Example 3: Upload to Spaces
        print("\n" + "🔄 Running Example 3...")
        example_3_upload_to_spaces(json_output_ai, "example_results.json")
        
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
