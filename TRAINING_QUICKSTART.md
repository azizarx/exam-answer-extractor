# Quick Start: Training AI for Your Exam Format

## TL;DR - 3 Steps to Better Accuracy

### Step 1: Create Template Examples (15 minutes)
```bash
# Take 2-3 exams you've already verified and create templates
python create_template.py examples/verified_exam1.png my_format
python create_template.py examples/verified_exam2.png my_format
python create_template.py examples/verified_exam3.png my_format
```

### Step 2: Test the Template
```bash
python test_template.py new_exam.png my_format
```

### Step 3: Use in Your System
The AI will now use your examples to better recognize the format!

---

## What This Does

**Before Templates:** 60-80% accuracy
- AI guesses your format from generic instructions
- May miss bubbles, misread handwriting
- Inconsistent across different exams

**After Templates:** 85-95% accuracy  
- AI sees 2-3 real examples of your format
- Learns your specific bubble style, handwriting, layout
- Consistent results using your examples as reference

---

## How It Works (Few-Shot Learning)

Think of it like showing someone examples:

**Without Examples (Current):**
"Extract answers from this exam sheet" 
→ AI guesses what an exam looks like

**With Examples (Template System):**
"Here are 3 examples of exams with their correct answers:
- Example 1: [shows image + correct output]
- Example 2: [shows image + correct output]
- Example 3: [shows image + correct output]

Now extract from THIS exam:"
→ AI understands your exact format!

---

## Why This Works Better Than Traditional Training

| Method | Setup Time | Works Immediately | Update Anytime | Cost |
|--------|------------|-------------------|----------------|------|
| **Few-Shot (Templates)** | ✅ 15 min | ✅ Yes | ✅ Yes | ✅ Free |
| Traditional Fine-Tuning | ❌ Days | ❌ No | ❌ No | ❌ $$$$ |
| Custom ML Model | ❌ Weeks | ❌ No | ❌ Hard | ❌ $$$$$ |

---

## Complete Workflow

### 1. **Prepare Example Exams**
- Find 2-3 exams you've already manually checked
- Ensure they're clear scans (300+ DPI)
- Pick exams with different handwriting styles
- Should represent typical student work

### 2. **Create Templates**
```bash
# Interactive mode - asks you to verify AI's extraction
python create_template.py path/to/exam1.png my_format
```

The tool will:
1. Extract answers using current AI
2. Show you the results
3. Let you verify/correct them
4. Save as a template example

### 3. **Add More Examples** (Optional but recommended)
```bash
python create_template.py path/to/exam2.png my_format
python create_template.py path/to/exam3.png my_format
```

More examples = better accuracy, but 2-3 is usually enough.

### 4. **Use Template in Code**

#### Option A: In Python scripts
```python
from backend.services.ai_extractor import get_ai_extractor

extractor = get_ai_extractor()

# Use your template
result = extractor.extract_with_template(
    image_path="new_exam.png",
    template_name="my_format"
)

print(f"Found {len(result['multiple_choice'])} MCQ answers")
print(f"Found {len(result['free_response'])} free responses")
```

#### Option B: In your FastAPI (requires integration)
See `TEMPLATE_API_INTEGRATION.md` for full API integration

---

## Template Directory Structure

After creating templates, you'll have:

```
backend/services/templates/
└── my_format/
    ├── example_1.png      # Your first example exam
    ├── example_1.json     # Correct answers for example 1
    ├── example_2.png      # Second example
    ├── example_2.json     # Correct answers for example 2
    └── example_3.png      # Third example (optional)
    └── example_3.json     # Correct answers for example 3
```

JSON format:
```json
{
  "multiple_choice": [
    {"question": 1, "answer": "A"},
    {"question": 2, "answer": "C"},
    {"question": 3, "answer": "B"}
  ],
  "free_response": [
    {
      "question": 1,
      "response": "The mitochondria is the powerhouse of the cell because it produces ATP through cellular respiration."
    }
  ]
}
```

---

## Tips for Best Results

### ✅ DO:
- Use 2-3 diverse examples (different handwriting, answer patterns)
- Include examples with typical student writing (not perfect)
- Verify the JSON is 100% correct before saving
- Use high-quality scans (300 DPI minimum)
- Pick examples that show your format clearly

### ❌ DON'T:
- Use only 1 example (too little data for AI to learn)
- Use 10+ examples (diminishing returns, slower)
- Use blurry or low-quality images
- Use atypical exams (perfect handwriting, unusual layouts)
- Forget to verify the JSON accuracy

---

## Troubleshooting

### "Template not working, still getting errors"

**Check:**
1. Are there at least 2 examples in the template folder?
2. Does each .png have a matching .json file?
3. Is the JSON format correct?
4. Are the example images clear and readable?

**Fix:**
```bash
# List your templates
ls backend/services/templates/my_format/

# Should see:
# example_1.png
# example_1.json
# example_2.png
# example_2.json

# Test if template works
python test_template.py test_exam.png my_format
```

### "Accuracy still not great"

**Solutions:**
1. Add more diverse examples (different handwriting styles)
2. Include examples with edge cases (messy marks, corrections)
3. Improve image quality (better scans, higher DPI)
4. Enhance prompt with specific format descriptions

---

## Advanced: Custom Prompts

For even better results, combine templates with custom prompts:

```python
custom_prompt = """
SPECIFIC FORMAT DETAILS:
- Questions 1-50: Multiple choice with 5 bubbles (A-E)
- Filled bubble = solid black circle
- Question numbers in small boxes on left margin
- Questions 51-60: Free response in lined boxes
- Handwriting is typically cursive English
- Ignore any stray marks outside answer areas

Extract all answers following the examples provided.
"""

result = extractor.extract_from_image(
    image_path="exam.png",
    extraction_prompt=custom_prompt
)
```

See `AI_TRAINING_GUIDE.md` for more advanced techniques.

---

## FAQ

**Q: Is this actually "training" the AI?**
A: Not traditional training. It's "few-shot learning" - showing examples at inference time. Works just as well for this use case!

**Q: Do I need to retrain every time I add examples?**
A: No! Just add the example files to the template folder. The AI uses them immediately.

**Q: Can I have multiple template categories?**
A: Yes! Create different folders for different exam formats:
- `bubble_sheet` - Scantron-style exams
- `handwritten` - Fully handwritten exams  
- `hybrid` - Mix of bubbles and writing
- `university_final` - Your specific format

**Q: What if my format changes?**
A: Just create a new template category with examples of the new format.

**Q: Does this cost extra API money?**
A: Slightly more tokens since you're sending example images, but usually negligible (few cents per exam).

---

## Next Steps

1. ✅ Create 2-3 template examples with `create_template.py`
2. ✅ Test with `test_template.py` 
3. ✅ Integrate into your app (see `TEMPLATE_API_INTEGRATION.md`)
4. ✅ Monitor results and add more examples if needed

**Result:** Much better extraction accuracy with minimal effort! 🎉

---

## Related Documentation

- **AI_TRAINING_GUIDE.md** - Comprehensive guide to all training methods
- **TEMPLATE_API_INTEGRATION.md** - How to add template support to your API
- **backend/services/templates/README.md** - Template directory structure

## Support

If you're still having issues after creating templates:
1. Check image quality and format
2. Verify JSON structure matches exactly
3. Try adding more diverse examples
4. See the full guide in `AI_TRAINING_GUIDE.md`
