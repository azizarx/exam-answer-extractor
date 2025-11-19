# AI Training Guide: Improving Exam Format Recognition

This guide explains how to "train" the AI to better recognize your specific exam answer sheet format.

## 🎯 Overview

The system uses **Google Gemini Vision AI**, which cannot be traditionally "trained" but can be guided using several techniques:

1. **Few-Shot Learning** ⭐ (Best for immediate results)
2. **Improved Prompts** (Enhanced instructions)
3. **Template System** (Format examples)
4. **Context-Aware Extraction** (Metadata hints)

---

## 1. Few-Shot Learning (Recommended) ⭐

**What it is:** Show the AI 2-3 example images with their correct outputs before asking it to extract from a new image.

**Advantages:**
- ✅ Works immediately, no training required
- ✅ Very effective for specific formats
- ✅ Can be updated anytime
- ✅ Uses the same API, no additional cost

### How to Use:

#### Step 1: Create Template Examples

```bash
# Run the template creation tool
python create_template.py path/to/example_exam.png my_exam_format
```

This will:
1. Extract answers from your example image using AI
2. Let you verify/correct the results
3. Save the image + correct answers as a template

#### Step 2: Add More Examples (2-3 total recommended)

```bash
python create_template.py path/to/example2.png my_exam_format
python create_template.py path/to/example3.png my_exam_format
```

#### Step 3: Use Template-Based Extraction

```python
from backend.services.ai_extractor import get_ai_extractor

extractor = get_ai_extractor()

# Use your template
result = extractor.extract_with_template(
    image_path="new_exam_to_extract.png",
    template_name="my_exam_format"
)
```

### Template Directory Structure:

```
backend/services/templates/
├── my_exam_format/
│   ├── example_1.png
│   ├── example_1.json
│   ├── example_2.png
│   ├── example_2.json
│   └── example_3.png
│   └── example_3.json
└── bubble_sheet/
    ├── example_1.png
    └── example_1.json
```

**Example JSON format:**
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
      "response": "The mitochondria is the powerhouse of the cell..."
    }
  ]
}
```

---

## 2. Improved Prompt Engineering

**What it is:** Enhance the AI instructions with specific details about your format.

### Enhanced Prompt Example:

```python
custom_prompt = """
You are analyzing a STANDARDIZED BUBBLE SHEET exam with this specific format:

FORMAT DETAILS:
- Questions 1-50 are multiple choice on pages 1-2
- Each MCQ has bubbles for A, B, C, D, E (filled = selected)
- Question numbers are printed in small boxes on the left
- Questions 51-60 are free response on pages 3-4
- Free response areas are lined boxes below each question number
- Student handwriting may be messy but is in English cursive
- Some bubbles may have stray marks - only count clearly filled ones

SPECIAL INSTRUCTIONS:
- Ignore bubbles that are lightly marked or crossed out
- For free response, capture ALL handwritten text, even if partially illegible
- Write "[illegible]" for text you cannot read clearly
- Question numbers are sequential, don't skip numbers

Return ONLY valid JSON with the extracted answers.
{
  "multiple_choice": [{"question": 1, "answer": "A"}],
  "free_response": [{"question": 51, "response": "text..."}]
}
"""

result = extractor.extract_from_image(image_path, custom_prompt)
```

### Tips for Better Prompts:
- Describe bubble/checkbox appearance
- Mention specific page layouts
- Explain handwriting style
- Define edge cases (multiple marks, erasures)
- Specify question numbering pattern

---

## 3. Context-Aware Extraction

**What it is:** Provide metadata about the exam to help guide extraction.

### Example Usage:

```python
exam_context = {
    "exam_name": "Biology Midterm - Fall 2024",
    "total_mcq": 50,
    "total_free_response": 10,
    "mcq_options": ["A", "B", "C", "D"],  # Only 4 options
    "special_instructions": "Questions 1-25 on page 1, 26-50 on page 2"
}

result = extractor.extract_with_context(image_path, exam_context)
```

This helps the AI know:
- How many answers to expect
- What option letters are valid
- Overall exam structure

---

## 4. Format-Specific Tips

### For Bubble Sheet Formats:
```python
# Create a bubble sheet template
prompt = """
BUBBLE SHEET FORMAT:
- Each question has 5 circles in a row
- Filled circle = selected answer
- Only ONE circle should be filled per question
- Ignore partially filled or lightly marked circles
- Question numbers are to the left of the circles
"""
```

### For Handwritten Formats:
```python
prompt = """
HANDWRITTEN FORMAT:
- Student writes answers in designated boxes
- Handwriting may be cursive or print
- Boxes are clearly outlined with borders
- Some text may be crossed out - ignore crossed-out text
- Question number appears above or to left of each box
"""
```

### For Mixed Formats:
```python
prompt = """
MIXED FORMAT:
- Section A (Questions 1-30): Multiple choice bubbles
- Section B (Questions 31-35): Short answer (handwritten)
- Section C (Questions 36-40): Long essay (handwritten)

Process each section with its appropriate method.
"""
```

---

## 5. Testing and Validation

### Test Your Templates:

```python
from backend.services.ai_extractor import get_ai_extractor

extractor = get_ai_extractor()

# Extract using your template
result = extractor.extract_with_template("test_exam.png", "my_exam_format")

# Validate the extraction
validation = extractor.validate_extraction(result)

print(f"Valid: {validation['is_valid']}")
print(f"MCQ Found: {validation['mcq_count']}")
print(f"Free Response Found: {validation['free_response_count']}")

if validation['warnings']:
    print("Warnings:")
    for warning in validation['warnings']:
        print(f"  - {warning}")
```

---

## 6. Integration with Your System

### Update routes.py to use templates:

```python
# In backend/api/routes.py, modify the extraction call:

# Check if user has a preferred template
template_name = submission.metadata.get("template_name", "default")

# Use template-based extraction
extraction_result = ai_extractor.extract_with_template(
    image_paths[0],
    template_name=template_name
)
```

### Add template selection in frontend:

```javascript
// In frontend upload form, add:
<select name="template">
  <option value="default">Standard Format</option>
  <option value="bubble_sheet">Bubble Sheet</option>
  <option value="handwritten">Handwritten</option>
  <option value="my_custom">My Custom Format</option>
</select>
```

---

## 7. Best Practices

### Creating Good Templates:
1. ✅ Use 2-3 diverse examples (different handwriting, answer patterns)
2. ✅ Include examples with edge cases (messy marks, corrections)
3. ✅ Verify the JSON output is 100% accurate
4. ✅ Use high-quality, clear scans (300 DPI minimum)
5. ✅ Ensure examples represent typical student work

### Prompt Engineering Tips:
1. ✅ Be specific about visual appearance
2. ✅ Describe the happy path AND edge cases
3. ✅ Use examples in the prompt itself
4. ✅ Mention what to IGNORE (stray marks, watermarks)
5. ✅ Request specific output format explicitly

### Iterative Improvement:
1. Start with 1-2 template examples
2. Test on real exams
3. Identify failure patterns
4. Add examples that cover those cases
5. Refine prompts based on errors
6. Repeat until accuracy is acceptable

---

## 8. Advanced: Fine-Tuning (Future Option)

**Note:** Gemini doesn't currently support custom fine-tuning, but if needed, you could:

### Option A: Use OpenAI GPT-4 Vision
- Supports fine-tuning with custom datasets
- Upload 50+ labeled examples
- Create a fine-tuned model

### Option B: Use Custom OCR + ML Pipeline
- Train custom object detection for bubbles/checkboxes
- Use OCR for text regions
- Train classifier for handwriting recognition
- More complex but fully customizable

---

## 🚀 Quick Start

### To immediately improve recognition:

```bash
# 1. Create 2-3 template examples
python create_template.py examples/exam1.png my_format
python create_template.py examples/exam2.png my_format

# 2. Restart your backend
python main.py

# 3. Test extraction
python test_template.py test_exam.png my_format
```

### Expected Improvements:
- **Without templates:** 60-80% accuracy
- **With 2-3 templates:** 85-95% accuracy
- **With optimized prompts:** 90-98% accuracy

---

## 📞 Support

If extraction accuracy is still low after using templates:
1. Check image quality (resolution, clarity, lighting)
2. Ensure examples truly represent the format
3. Try more specific prompts describing visual features
4. Consider hybrid approach (OCR + AI + templates)

---

## Summary Table

| Method | Setup Time | Accuracy Gain | Cost | Maintenance |
|--------|------------|---------------|------|-------------|
| Few-Shot Learning | 15 min | ⭐⭐⭐⭐⭐ | Free | Low |
| Improved Prompts | 5 min | ⭐⭐⭐⭐ | Free | Low |
| Context Metadata | 2 min | ⭐⭐⭐ | Free | Medium |
| Custom OCR+ML | Days | ⭐⭐⭐⭐⭐ | High | High |

**Recommended:** Start with Few-Shot Learning templates (2-3 examples) + improved prompts.
