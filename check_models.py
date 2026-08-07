"""
Check available Gemini models with your API key

This helps determine which model names to use.
"""

import google.generativeai as genai
from backend.config import get_settings

settings = get_settings()
genai.configure(api_key=settings.gemini_api_key)

print("\n" + "="*60)
print("Available Gemini Models")
print("="*60 + "\n")

try:
    for model in genai.list_models():
        if 'generateContent' in model.supported_generation_methods:
            print(f"✅ {model.name}")
            print(f"   Display Name: {model.display_name}")
            print(f"   Description: {model.description}")
            print(f"   Methods: {', '.join(model.supported_generation_methods)}")
            print()
except Exception as e:
    print(f"❌ Error listing models: {e}")
    print(f"\nTrying to use gemini-pro directly...")
    
    # Test gemini-pro
    try:
        model = genai.GenerativeModel('gemini-pro')
        print(f"✅ gemini-pro is available!")
    except Exception as e2:
        print(f"❌ gemini-pro failed: {e2}")

print("="*60)
print(f"Current setting: {settings.gemini_model}")
print("="*60 + "\n")
