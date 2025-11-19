"""
Test Template-Based Extraction

Usage:
    python test_template.py <image_path> <template_name>
"""

import sys
import json
from backend.services.ai_extractor import get_ai_extractor

def test_extraction():
    if len(sys.argv) < 2:
        print("Usage: python test_template.py <image_path> [template_name]")
        print("\nExample:")
        print("  python test_template.py test_exam.png my_format")
        print("  python test_template.py test_exam.png  # uses default template")
        return
    
    image_path = sys.argv[1]
    template_name = sys.argv[2] if len(sys.argv) > 2 else "default"
    
    print(f"\n🧪 Testing Extraction")
    print(f"📄 Image: {image_path}")
    print(f"📋 Template: {template_name}")
    print("-" * 60)
    
    extractor = get_ai_extractor()
    
    # Test with template
    print("\n🤖 Extracting with template...")
    result = extractor.extract_with_template(image_path, template_name)
    
    print("\n📊 Results:")
    print(json.dumps(result, indent=2))
    
    # Validate
    print("\n✅ Validation:")
    validation = extractor.validate_extraction(result)
    print(f"  Valid: {validation['is_valid']}")
    print(f"  MCQ Count: {validation['mcq_count']}")
    print(f"  Free Response Count: {validation['free_response_count']}")
    
    if validation['warnings']:
        print("\n⚠️  Warnings:")
        for warning in validation['warnings']:
            print(f"  - {warning}")
    
    print("\n" + "=" * 60)
    print("✅ Test complete!")

if __name__ == "__main__":
    try:
        test_extraction()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
