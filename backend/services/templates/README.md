# Template Directory

This directory stores example images and their correct extraction outputs for few-shot learning.

## Structure

Each subdirectory represents a template category:

```
templates/
├── default/              # General exam format
│   ├── example_1.png
│   ├── example_1.json
│   ├── example_2.png
│   └── example_2.json
├── bubble_sheet/         # Bubble/scantron format
├── handwritten/          # Fully handwritten exams
└── your_custom_format/   # Your specific format
```

## Creating Templates

Use the template creation tool:

```bash
python create_template.py path/to/example.png template_name
```

## JSON Format

Each `.json` file should match its corresponding image:

```json
{
  "multiple_choice": [
    {"question": 1, "answer": "A"},
    {"question": 2, "answer": "C"}
  ],
  "free_response": [
    {
      "question": 1,
      "response": "Complete answer text here..."
    }
  ]
}
```

## Using Templates

```python
from backend.services.ai_extractor import get_ai_extractor

extractor = get_ai_extractor()
result = extractor.extract_with_template("exam.png", "bubble_sheet")
```
