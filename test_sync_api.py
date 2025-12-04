import requests
import json
import os
from pathlib import Path

# Configuration
API_BASE_URL = "http://localhost:8000"
TEST_PDF_PATH = r"backend/examples/UZ1-35.pdf"

def test_sync_extraction():
    print(f"\n{'='*60}")
    print("TEST: Synchronous PDF Extraction")
    print(f"{'='*60}")

    if not os.path.exists(TEST_PDF_PATH):
        print(f"❌ Test PDF not found: {TEST_PDF_PATH}")
        return

    url = f"{API_BASE_URL}/extract/json"
    print(f"POST {url}")

    try:
        with open(TEST_PDF_PATH, 'rb') as f:
            files = {'file': (Path(TEST_PDF_PATH).name, f, 'application/pdf')}
            response = requests.post(url, files=files)

        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print("✅ Extraction successful!")
            print(f"Filename: {data.get('filename')}")
            print(f"MCQ Count: {data.get('total_multiple_choice')}")
            print(f"Free Response Count: {data.get('total_free_response')}")
            
            # Print a snippet of the result
            print("\nSnippet of results:")
            print(json.dumps(data, indent=2)[:500] + "...")
        else:
            print(f"❌ Extraction failed: {response.text}")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_sync_extraction()
