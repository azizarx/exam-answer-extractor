"""
Template Creation Tool for Exam Format Training

This script helps you create training examples to teach the AI your specific exam format.
Run this after you've manually verified some extractions to improve future accuracy.

Usage:
    python create_template.py <image_path> <template_name>
    
Then manually edit the generated JSON file with the correct answers.
"""

import sys
import os
import json
from backend.services.ai_extractor import get_ai_extractor

def create_template_interactive():
    """Interactive template creation"""
    print("\n=== Exam Format Template Creator ===\n")
    
    # Get image path
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
    else:
        image_path = input("Enter path to example exam image: ").strip()
    
    if not os.path.exists(image_path):
        print(f"❌ Error: Image not found at {image_path}")
        return
    
    # Get template name
    if len(sys.argv) > 2:
        template_name = sys.argv[2]
    else:
        template_name = input("Enter template name (default/bubble_sheet/handwritten): ").strip() or "default"
    
    print(f"\n📋 Creating template example from: {image_path}")
    print(f"📁 Template category: {template_name}\n")
    
    # First, try to extract with AI
    print("🤖 Running AI extraction to get initial results...")
    extractor = get_ai_extractor()
    initial_extraction = extractor.extract_from_image(image_path)
    
    print("\n✅ Initial AI extraction:")
    print(json.dumps(initial_extraction, indent=2))
    
    # Ask if user wants to use this or edit
    print("\n" + "="*50)
    choice = input("\nUse this extraction? (y/n/edit): ").strip().lower()
    
    if choice == 'n':
        print("\n📝 Please provide the correct extraction manually.")
        print("Multiple choice format: [{\"question\": 1, \"answer\": \"A\"}, ...]")
        print("Free response format: [{\"question\": 1, \"response\": \"text...\"}, ...]\n")
        
        expected_output = {
            "multiple_choice": [],
            "free_response": []
        }
        
        # Get MCQ answers
        print("\nEnter multiple choice answers (format: question_num answer_letter)")
        print("Example: 1 A")
        print("Press Enter with no input when done.\n")
        while True:
            line = input("MCQ: ").strip()
            if not line:
                break
            try:
                q, a = line.split()
                expected_output["multiple_choice"].append({
                    "question": int(q),
                    "answer": a.upper()
                })
            except:
                print("Invalid format, try again")
        
        # Get free response
        print("\nEnter free response answers (press Enter twice when done with each)")
        print("Format: question_num then response text on next lines\n")
        while True:
            q_line = input("Question number (or press Enter to finish): ").strip()
            if not q_line:
                break
            try:
                q_num = int(q_line)
                print(f"Response for Q{q_num} (press Enter twice when done):")
                lines = []
                while True:
                    line = input()
                    if not line:
                        break
                    lines.append(line)
                
                if lines:
                    expected_output["free_response"].append({
                        "question": q_num,
                        "response": " ".join(lines)
                    })
            except:
                print("Invalid format, try again")
    
    elif choice == 'edit':
        # Save to temp file and let user edit
        temp_path = "temp_template.json"
        with open(temp_path, 'w') as f:
            json.dump(initial_extraction, f, indent=2)
        
        print(f"\n📝 Edit the extraction in: {temp_path}")
        print("Press Enter after saving your changes...")
        input()
        
        with open(temp_path, 'r') as f:
            expected_output = json.load(f)
        os.remove(temp_path)
    
    else:
        expected_output = initial_extraction
    
    # Create the template
    print("\n💾 Saving template...")
    result = extractor.create_template(image_path, expected_output, template_name)
    
    print(f"\n✅ Template created successfully!")
    print(f"   Template: {result['template']}")
    print(f"   Example #: {result['example_number']}")
    print(f"\n📁 Template files saved in: backend/services/templates/{template_name}/")
    print(f"\n💡 Add 2-3 examples to improve AI accuracy on your format!")
    print(f"\n🚀 Use with: extractor.extract_with_template(image_path, '{template_name}')")


if __name__ == "__main__":
    try:
        create_template_interactive()
    except KeyboardInterrupt:
        print("\n\n❌ Cancelled by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
