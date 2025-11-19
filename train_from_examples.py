"""
Batch Template Training from Example PDFs

This script processes all PDFs in backend/examples folder and creates training templates.
It will extract answers using AI, let you verify them, and save as templates.

Usage:
    python train_from_examples.py [template_name]
    
Example:
    python train_from_examples.py my_university_format
"""

import sys
import os
import json
from pathlib import Path
from backend.services.pdf_to_images import get_pdf_converter
from backend.services.ai_extractor import get_ai_extractor

def train_from_examples(template_name="default"):
    """Process all PDFs in backend/examples folder"""
    
    examples_dir = Path("backend/examples")
    
    if not examples_dir.exists():
        print(f"❌ Error: {examples_dir} directory not found")
        return
    
    # Find all PDFs
    pdf_files = list(examples_dir.glob("*.pdf"))
    
    if not pdf_files:
        print(f"❌ No PDF files found in {examples_dir}")
        return
    
    print(f"\n{'='*60}")
    print(f"🎓 Batch Template Training from Examples")
    print(f"{'='*60}\n")
    print(f"📁 Source: {examples_dir}")
    print(f"📋 Template: {template_name}")
    print(f"📄 Found {len(pdf_files)} PDF file(s)")
    print(f"\n{'='*60}\n")
    
    # Initialize services
    pdf_converter = get_pdf_converter()
    ai_extractor = get_ai_extractor()
    
    created_count = 0
    
    for pdf_idx, pdf_path in enumerate(pdf_files, 1):
        print(f"\n{'='*60}")
        print(f"Processing PDF {pdf_idx}/{len(pdf_files)}: {pdf_path.name}")
        print(f"{'='*60}\n")
        
        try:
            # Convert PDF to images
            print(f"🖼️  Converting PDF to images...")
            image_paths = pdf_converter.convert_from_file(str(pdf_path))
            print(f"✅ Converted to {len(image_paths)} page(s)")
            
            # Ask user how many pages to use as examples (to avoid too many)
            if len(image_paths) > 5:
                print(f"\n⚠️  This PDF has {len(image_paths)} pages.")
                print(f"💡 Tip: Using 2-3 representative pages is usually enough.")
                
                choice = input(f"\nHow many pages to use as training examples? (1-{min(5, len(image_paths))}) [3]: ").strip()
                try:
                    num_pages = int(choice) if choice else 3
                    num_pages = max(1, min(num_pages, len(image_paths), 5))
                except:
                    num_pages = 3
                
                # Ask which pages
                print(f"\nWhich pages? Options:")
                print(f"  1. First {num_pages} pages")
                print(f"  2. Last {num_pages} pages")
                print(f"  3. Evenly distributed pages")
                print(f"  4. Specify page numbers")
                
                page_choice = input(f"\nChoice (1-4) [3]: ").strip() or "3"
                
                if page_choice == "1":
                    selected_images = image_paths[:num_pages]
                elif page_choice == "2":
                    selected_images = image_paths[-num_pages:]
                elif page_choice == "4":
                    page_nums = input(f"Enter page numbers (1-{len(image_paths)}), comma-separated: ").strip()
                    selected_indices = [int(p.strip())-1 for p in page_nums.split(",") if p.strip().isdigit()]
                    selected_images = [image_paths[i] for i in selected_indices if 0 <= i < len(image_paths)]
                else:  # Option 3 - evenly distributed
                    step = max(1, len(image_paths) // num_pages)
                    selected_images = [image_paths[i] for i in range(0, len(image_paths), step)][:num_pages]
            else:
                # Use all pages if 5 or fewer
                selected_images = image_paths
            
            print(f"\n📋 Processing {len(selected_images)} page(s) as training examples...\n")
            
            # Process each selected page
            for page_idx, image_path in enumerate(selected_images, 1):
                print(f"\n{'─'*60}")
                print(f"Page {page_idx}/{len(selected_images)}: {Path(image_path).name}")
                print(f"{'─'*60}\n")
                
                # Extract with AI
                print(f"🤖 Running AI extraction...")
                extraction_result = ai_extractor.extract_from_image(image_path)
                
                # Show results
                print(f"\n📊 Initial AI Extraction:")
                print(f"  • MCQ Answers: {len(extraction_result.get('multiple_choice', []))}")
                print(f"  • Free Response: {len(extraction_result.get('free_response', []))}")
                
                if extraction_result.get('multiple_choice'):
                    print(f"\n  Multiple Choice:")
                    for mcq in extraction_result.get('multiple_choice', [])[:5]:
                        print(f"    Q{mcq['question']}: {mcq['answer']}")
                    if len(extraction_result.get('multiple_choice', [])) > 5:
                        print(f"    ... and {len(extraction_result.get('multiple_choice', [])) - 5} more")
                
                if extraction_result.get('free_response'):
                    print(f"\n  Free Response:")
                    for fr in extraction_result.get('free_response', [])[:2]:
                        response_preview = fr['response'][:60] + "..." if len(fr['response']) > 60 else fr['response']
                        print(f"    Q{fr['question']}: {response_preview}")
                    if len(extraction_result.get('free_response', [])) > 2:
                        print(f"    ... and {len(extraction_result.get('free_response', [])) - 2} more")
                
                # Validation
                validation = ai_extractor.validate_extraction(extraction_result)
                if not validation['is_valid']:
                    print(f"\n⚠️  Validation Warnings:")
                    for warning in validation['warnings']:
                        print(f"    • {warning}")
                
                # Ask user to confirm or edit
                print(f"\n{'─'*60}")
                print(f"Options:")
                print(f"  y - Use this extraction as-is")
                print(f"  e - Edit and save corrected version")
                print(f"  s - Skip this page")
                print(f"  q - Quit training")
                
                choice = input(f"\nChoice [y]: ").strip().lower() or 'y'
                
                if choice == 'q':
                    print(f"\n👋 Training stopped by user")
                    return
                
                if choice == 's':
                    print(f"⏭️  Skipped page {page_idx}")
                    continue
                
                if choice == 'e':
                    # Save to temp file for editing
                    temp_json = f"temp_edit_{page_idx}.json"
                    with open(temp_json, 'w') as f:
                        json.dump(extraction_result, f, indent=2)
                    
                    print(f"\n📝 Saved to: {temp_json}")
                    print(f"   Edit the file with your corrections, then press Enter...")
                    input()
                    
                    # Read back edited version
                    try:
                        with open(temp_json, 'r') as f:
                            extraction_result = json.load(f)
                        os.remove(temp_json)
                        print(f"✅ Loaded corrected extraction")
                    except Exception as e:
                        print(f"❌ Error reading edited file: {e}")
                        print(f"⏭️  Skipping this page")
                        continue
                
                # Save as template
                print(f"\n💾 Saving as training template...")
                result = ai_extractor.create_template(
                    image_path,
                    extraction_result,
                    template_name
                )
                
                print(f"✅ Template example created: {result['template']}/example_{result['example_number']}")
                created_count += 1
            
            # Clean up temporary images
            print(f"\n🧹 Cleaning up temporary files...")
            for img_path in image_paths:
                try:
                    os.remove(img_path)
                except:
                    pass
            
        except Exception as e:
            print(f"❌ Error processing {pdf_path.name}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    # Summary
    print(f"\n{'='*60}")
    print(f"✅ Training Complete!")
    print(f"{'='*60}\n")
    print(f"📋 Template: {template_name}")
    print(f"📝 Created: {created_count} training example(s)")
    print(f"📁 Location: backend/services/templates/{template_name}/")
    
    if created_count > 0:
        print(f"\n🚀 Next Steps:")
        print(f"   1. Test your template:")
        print(f"      python test_template.py <image_path> {template_name}")
        print(f"\n   2. Use in extraction:")
        print(f"      extractor.extract_with_template(image_path, '{template_name}')")
        print(f"\n   3. Add more examples anytime by running this script again!")
    else:
        print(f"\n⚠️  No training examples were created.")
        print(f"   Run the script again and approve some extractions.")
    
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    template_name = sys.argv[1] if len(sys.argv) > 1 else "default"
    
    print(f"\n💡 This will create training templates from PDFs in backend/examples/")
    print(f"   Template name: {template_name}")
    
    confirm = input(f"\nContinue? (y/n) [y]: ").strip().lower() or 'y'
    
    if confirm == 'y':
        try:
            train_from_examples(template_name)
        except KeyboardInterrupt:
            print(f"\n\n❌ Cancelled by user")
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
    else:
        print(f"❌ Cancelled")
