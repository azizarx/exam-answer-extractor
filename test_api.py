"""
Test script for the exam extraction system
"""
import requests
import time
import json
from pathlib import Path

# Configuration
API_BASE_URL = "http://localhost:8000/api/v1"
TEST_PDF_PATH = "test_exam.pdf"  # Replace with your test PDF path


def test_upload(pdf_path: str):
    """Test PDF upload endpoint"""
    print(f"\n{'='*60}")
    print("TEST 1: Upload PDF")
    print(f"{'='*60}")
    
    if not Path(pdf_path).exists():
        print(f"❌ Test PDF not found: {pdf_path}")
        print("Please create a test PDF or update TEST_PDF_PATH")
        return None
    
    with open(pdf_path, 'rb') as f:
        files = {'file': (Path(pdf_path).name, f, 'application/pdf')}
        response = requests.post(f"{API_BASE_URL}/upload", files=files)
    
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print("✅ Upload successful!")
        print(json.dumps(data, indent=2))
        return data.get('submission_id')
    else:
        print(f"❌ Upload failed: {response.text}")
        return None


def test_status(submission_id: int):
    """Test status check endpoint"""
    print(f"\n{'='*60}")
    print("TEST 2: Check Processing Status")
    print(f"{'='*60}")
    
    max_attempts = 30
    for attempt in range(max_attempts):
        response = requests.get(f"{API_BASE_URL}/status/{submission_id}")
        
        if response.status_code == 200:
            data = response.json()
            status = data.get('status')
            
            print(f"\rAttempt {attempt + 1}/{max_attempts} - Status: {status}", end='')
            
            if status == 'completed':
                print("\n✅ Processing completed!")
                print(json.dumps(data, indent=2))
                return True
            elif status == 'failed':
                print(f"\n❌ Processing failed: {data.get('error_message')}")
                return False
            
            time.sleep(2)  # Wait 2 seconds before checking again
        else:
            print(f"\n❌ Status check failed: {response.text}")
            return False
    
    print("\n⏱️ Processing timeout - taking longer than expected")
    return False


def test_get_results(submission_id: int):
    """Test get submission results endpoint"""
    print(f"\n{'='*60}")
    print("TEST 3: Retrieve Extracted Answers")
    print(f"{'='*60}")
    
    response = requests.get(f"{API_BASE_URL}/submission/{submission_id}")
    
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print("✅ Results retrieved successfully!")
        
        print(f"\n📊 Summary:")
        print(f"  Multiple Choice: {len(data.get('multiple_choice', []))} answers")
        print(f"  Free Response: {len(data.get('free_response', []))} answers")
        
        print(f"\n📝 Multiple Choice Answers:")
        for mcq in data.get('multiple_choice', [])[:5]:  # Show first 5
            print(f"  Q{mcq['question']}: {mcq['answer']}")
        
        if len(data.get('multiple_choice', [])) > 5:
            print(f"  ... and {len(data.get('multiple_choice', [])) - 5} more")
        
        print(f"\n✍️ Free Response Answers:")
        for fr in data.get('free_response', [])[:2]:  # Show first 2
            preview = fr['response'][:100] + "..." if len(fr['response']) > 100 else fr['response']
            print(f"  Q{fr['question']}: {preview}")
        
        if len(data.get('free_response', [])) > 2:
            print(f"  ... and {len(data.get('free_response', [])) - 2} more")
        
        return True
    else:
        print(f"❌ Failed to retrieve results: {response.text}")
        return False


def test_list_submissions():
    """Test list submissions endpoint"""
    print(f"\n{'='*60}")
    print("TEST 4: List All Submissions")
    print(f"{'='*60}")
    
    response = requests.get(f"{API_BASE_URL}/submissions?limit=10")
    
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Found {len(data)} submissions")
        
        for sub in data[:3]:  # Show first 3
            print(f"\n  ID: {sub['submission_id']}")
            print(f"  Filename: {sub['filename']}")
            print(f"  Status: {sub['status']}")
            print(f"  MCQ: {sub['mcq_count']}, Free Response: {sub['free_response_count']}")
        
        return True
    else:
        print(f"❌ Failed to list submissions: {response.text}")
        return False


def test_health_check():
    """Test health check endpoint"""
    print(f"\n{'='*60}")
    print("TEST 0: Health Check")
    print(f"{'='*60}")
    
    try:
        response = requests.get("http://localhost:8000/health", timeout=5)
        
        if response.status_code == 200:
            print("✅ API is healthy and running")
            print(json.dumps(response.json(), indent=2))
            return True
        else:
            print(f"❌ Health check failed: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to API. Is the server running?")
        print("   Start with: python main.py")
        return False


def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("EXAM EXTRACTION SYSTEM - TEST SUITE")
    print("="*60)
    
    # Test 0: Health check
    if not test_health_check():
        print("\n⚠️ Server is not running. Please start the server first.")
        return
    
    # Test 1: Upload
    submission_id = test_upload(TEST_PDF_PATH)
    if not submission_id:
        print("\n⚠️ Upload failed. Skipping remaining tests.")
        print("Make sure you have a test PDF at:", TEST_PDF_PATH)
        return
    
    # Test 2: Status (with polling)
    if not test_status(submission_id):
        print("\n⚠️ Processing failed or timed out.")
    
    # Test 3: Get results
    test_get_results(submission_id)
    
    # Test 4: List submissions
    test_list_submissions()
    
    print(f"\n{'='*60}")
    print("TEST SUITE COMPLETE")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
