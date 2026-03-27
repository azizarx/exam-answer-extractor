"""
Quick test script to verify the new flat JSON format and marking endpoints.
Run with: python test_new_format.py
Requires the server running at http://localhost:8000
"""
import requests
import json
import sys

BASE = "http://localhost:8000"
PDF_PATH = "backend/examples/UZ1-35.pdf"

def test_answer_key_crud():
    print("=== Test: Answer Key CRUD ===")
    
    # List existing
    r = requests.get(f"{BASE}/answer-keys")
    print(f"  GET /answer-keys: {r.status_code} -> {len(r.json())} keys")
    
    # Create a new key
    key_data = {
        "name": "UZ1 Paper D",
        "paper_type": "D",
        "answers": {str(i): "B" for i in range(1, 21)},
        "drawing_key": {"21": "9", "22": "111"}
    }
    r = requests.post(f"{BASE}/answer-keys", json=key_data)
    print(f"  POST /answer-keys: {r.status_code}")
    if r.status_code == 200:
        key_id = r.json()["id"]
        print(f"    Created key ID: {key_id}")
        
        # Get by ID
        r = requests.get(f"{BASE}/answer-keys/{key_id}")
        print(f"  GET /answer-keys/{key_id}: {r.status_code}")
        
        # Delete
        r = requests.delete(f"{BASE}/answer-keys/{key_id}")
        print(f"  DELETE /answer-keys/{key_id}: {r.status_code}")
    
    print()

def test_extract_json():
    print("=== Test: Extract JSON (new flat format) ===")
    files = {"file": open(PDF_PATH, "rb")}
    r = requests.post(f"{BASE}/extract/json", files=files, timeout=300)
    print(f"  POST /extract/json: {r.status_code}")
    
    if r.status_code == 200:
        data = r.json()
        candidates = data.get("candidates", [])
        print(f"  Total candidates: {len(candidates)}")
        
        if candidates:
            c = candidates[0]
            print(f"  First candidate:")
            print(f"    Name: {c.get('candidate_name')}")
            print(f"    Number: {c.get('candidate_number')}")
            print(f"    Country: {c.get('country')}")
            print(f"    Paper: {c.get('paper_type')}")
            print(f"    Answers: {len(c.get('answers', {}))} questions")
            print(f"    Drawing: {len(c.get('drawing_questions', {}))} questions")
            
            # Verify BL/IN codes are present
            all_answers = []
            for cand in candidates:
                all_answers.extend(cand.get("answers", {}).values())
            bl_count = all_answers.count("BL")
            in_count = all_answers.count("IN")
            print(f"  BL (blank) answers found: {bl_count}")
            print(f"  IN (invalid) answers found: {in_count}")
    
    print()
    return data if r.status_code == 200 else None

def test_inline_marking():
    print("=== Test: Extract + Mark (inline key) ===")
    answer_key = {str(i): "B" for i in range(1, 21)}
    mark_req = json.dumps({
        "answer_key": answer_key,
        "drawing_key": {"21": "9"}
    })
    
    files = {"file": open(PDF_PATH, "rb")}
    r = requests.post(
        f"{BASE}/extract/json/mark",
        files=files,
        data={"mark_request": mark_req},
        timeout=300
    )
    print(f"  POST /extract/json/mark: {r.status_code}")
    
    if r.status_code == 200:
        data = r.json()
        candidates = data.get("candidates", [])
        print(f"  Total candidates: {len(candidates)}")
        
        for c in candidates[:3]:
            name = c.get("candidate_name", "?")
            score = c.get("score", {})
            marked = c.get("marked_answers", {})
            p_count = sum(1 for v in marked.values() if v == "P")
            print(f"    {name}: {score.get('correct')}/{score.get('total')} ({score.get('percentage')}%) - {p_count} correct")
    
    print()

if __name__ == "__main__":
    try:
        requests.get(BASE, timeout=3)
    except Exception:
        print(f"ERROR: Server not reachable at {BASE}. Start it first.")
        sys.exit(1)
    
    test_answer_key_crud()
    
    # Only run extraction tests if --extract flag is passed (they take time)
    if "--extract" in sys.argv:
        test_extract_json()
        test_inline_marking()
    else:
        print("Skipping extraction tests (pass --extract to run them)")
    
    print("All tests completed!")
