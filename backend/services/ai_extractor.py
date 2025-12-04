"""
AI Extractor Service
Uses Google Gemini Vision API for intelligent answer extraction from exam sheets
"""
import google.generativeai as genai
from typing import List, Dict, Optional
import logging
import json
from PIL import Image
import os

from backend.config import get_settings

logger = logging.getLogger(__name__)


class AIExtractor:
    """AI-powered extractor using Google Gemini Vision API"""
    
    def __init__(self, template_dir: Optional[str] = None):
        settings = get_settings()
        genai.configure(api_key=settings.gemini_api_key)
        self.model = genai.GenerativeModel(settings.gemini_model)
        self.template_dir = template_dir or os.path.join(os.path.dirname(__file__), "templates")
        logger.info(f"Initialized AIExtractor with Gemini model: {settings.gemini_model}")
    
    def extract_from_pdf(
        self,
        pdf_path: str,
        extraction_prompt: Optional[str] = None
    ) -> Dict:
        """
        Extract answers directly from a PDF exam sheet using AI
        
        Args:
            pdf_path: Path to PDF file
            extraction_prompt: Custom prompt (uses default if None)
            
        Returns:
            Dict with extracted multiple choice and free response answers
        """
        try:
            # Default extraction prompt
            if extraction_prompt is None:
                extraction_prompt = """
You are analyzing an exam answer sheet. Extract ALL answers with high accuracy.

Extract:
1. Multiple choice answers - Question number and selected option (A, B, C, D, or E)
2. Free response answers - Question number and the full written text

Return ONLY valid JSON in this exact format:
{
  "multiple_choice": [
    {"question": 1, "answer": "A"},
    {"question": 2, "answer": "C"}
  ],
  "free_response": [
    {"question": 1, "response": "The complete answer text here..."},
    {"question": 2, "response": "Another response..."}
  ]
}

Important:
- Return empty arrays [] if no answers of that type are found
- Preserve exact spelling and punctuation in free responses
- Return ONLY the JSON, no additional text
"""
            
            # Upload PDF file to Gemini
            logger.info(f"Uploading PDF to Gemini: {pdf_path}")
            uploaded_file = genai.upload_file(pdf_path)
            
            # Call Gemini API with the PDF
            logger.info(f"Calling Google Gemini API for PDF analysis")
            response = self.model.generate_content([extraction_prompt, uploaded_file])
            
            # Parse response
            content = response.text
            logger.debug(f"Raw API response: {content}")
            
            # Extract JSON from response
            try:
                # Try to parse directly
                result = json.loads(content)
            except json.JSONDecodeError:
                # Try to extract JSON from markdown code blocks
                import re
                json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group(1))
                else:
                    logger.error(f"Failed to parse JSON from response: {content}")
                    result = {"multiple_choice": [], "free_response": []}
            
            logger.info(f"Successfully extracted {len(result.get('multiple_choice', []))} MCQ and {len(result.get('free_response', []))} free response answers from PDF")
            return result
            
        except Exception as e:
            logger.error(f"AI extraction failed for PDF {pdf_path}: {str(e)}")
            return {"multiple_choice": [], "free_response": [], "error": str(e)}
    
    def extract_from_image(
        self,
        image_path: str,
        extraction_prompt: Optional[str] = None,
        use_examples: bool = True
    ) -> Dict:
        """
        Extract answers from a single exam sheet image using AI
        
        Args:
            image_path: Path to exam sheet image
            extraction_prompt: Custom prompt (uses default if None)
            use_examples: Whether to include example images for few-shot learning
            
        Returns:
            Dict with extracted multiple choice and free response answers
        """
        try:
            # Default extraction prompt
            if extraction_prompt is None:
                extraction_prompt = """
You are analyzing an exam answer sheet. Extract ALL answers with high accuracy.

IMPORTANT FORMAT GUIDELINES:
- Look for bubble/circle selections for multiple choice (filled circles indicate answers)
- Look for handwritten text in designated areas for free response
- Question numbers are typically on the left side
- Multiple choice options are usually A, B, C, D, E in a row
- Free response areas are larger text boxes below questions
- Look for Candidate Name, Candidate Number/ID, Country, and Level at the top of the page

Extract:
1. Multiple choice answers - Question number and selected option (A, B, C, D, or E)
2. Free response answers - Question number and the full written text
3. Candidate Information - Name, Number/ID, Country, Level

Return ONLY valid JSON in this exact format:
{
  "candidate_info": {
    "name": "Candidate Name",
    "id": "Candidate ID",
    "country": "Country",
    "level": "Level"
  },
  "multiple_choice": [
    {"question": 1, "answer": "A"},
    {"question": 2, "answer": "C"}
  ],
  "free_response": [
    {"question": 1, "response": "The complete answer text here..."},
    {"question": 2, "response": "Another response..."}
  ]
}

Important:
- Return empty arrays [] if no answers of that type are found
- Preserve exact spelling and punctuation in free responses
- Return ONLY the JSON, no additional text
- Pay close attention to which bubble is filled/marked
- Ignore faint marks, only count clear selections
- If candidate info is missing, return empty strings
"""
            
            # Load image
            image = Image.open(image_path)
            
            # Call Gemini Vision API
            logger.info(f"Calling Google Gemini API for {image_path}")
            response = self.model.generate_content([extraction_prompt, image])
            
            # Parse response
            content = response.text
            logger.debug(f"Raw API response: {content}")
            
            # Extract JSON from response
            try:
                # Try to parse directly
                result = json.loads(content)
            except json.JSONDecodeError:
                # Try to extract JSON from markdown code blocks
                import re
                json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group(1))
                else:
                    logger.error(f"Failed to parse JSON from response: {content}")
                    result = {"multiple_choice": [], "free_response": []}
            
            logger.info(f"Successfully extracted {len(result.get('multiple_choice', []))} MCQ and {len(result.get('free_response', []))} free response answers")
            return result
            
        except Exception as e:
            logger.error(f"AI extraction failed for {image_path}: {str(e)}")
            return {"multiple_choice": [], "free_response": [], "error": str(e)}
    
    def _process_single_page(self, image_path: str, page_num: int, extraction_prompt: Optional[str] = None) -> Dict:
        """
        Process a single page (helper for parallel processing)
        
        Args:
            image_path: Path to image
            page_num: Page number (1-indexed)
            extraction_prompt: Custom prompt
            
        Returns:
            Dict with page results including page number
        """
        logger.info(f"Processing page {page_num}: {image_path}")
        result = self.extract_from_image(image_path, extraction_prompt)
        
        # Add page number to all extracted items
        if "candidate_info" in result:
            result["candidate_info"]['page'] = page_num
        
        for mcq in result.get("multiple_choice", []):
            mcq['page'] = page_num
        for fr in result.get("free_response", []):
            fr['page'] = page_num
        
        result['page_num'] = page_num
        result['image_path'] = image_path
        return result

    def extract_from_multiple_images(
        self,
        image_paths: List[str],
        extraction_prompt: Optional[str] = None,
        submission_id: Optional[int] = None,
        db=None,
        use_parallel: bool = True,
        max_workers: int = 4
    ) -> Dict:
        """
        Extract answers from multiple exam sheet images (with optional parallel processing)
        
        Args:
            image_paths: List of image paths
            extraction_prompt: Custom prompt (uses default if None)
            submission_id: Optional submission ID for logging
            db: Optional database session for logging
            use_parallel: Use multi-threading for parallel processing (default: True)
            max_workers: Maximum number of parallel workers (default: 4)
            
        Returns:
            Combined dict with all extracted answers
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import time
        
        all_mcq = []
        all_free_response = []
        all_candidate_info = []
        errors = []
        
        start_time = time.time()
        
        if use_parallel and len(image_paths) > 1:
            logger.info(f"Processing {len(image_paths)} pages in parallel with {max_workers} workers")
            
            # Process pages in parallel
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                future_to_page = {
                    executor.submit(self._process_single_page, image_path, i, extraction_prompt): i 
                    for i, image_path in enumerate(image_paths, start=1)
                }
                
                # Collect results as they complete
                for future in as_completed(future_to_page):
                    page_num = future_to_page[future]
                    try:
                        result = future.result()
                        
                        if "error" in result:
                            errors.append(f"Page {page_num}: {result['error']}")
                        
                        # Collect candidate info
                        candidate_name = ""
                        if "candidate_info" in result:
                            all_candidate_info.append(result["candidate_info"])
                            candidate_name = result["candidate_info"].get('name', "")
                        
                        all_mcq.extend(result.get("multiple_choice", []))
                        all_free_response.extend(result.get("free_response", []))
                        
                        # Log progress to ProcessingLog if db and submission_id are provided
                        if db is not None and submission_id is not None:
                            from backend.db.models import ProcessingLog
                            log_entry = ProcessingLog(
                                submission_id=submission_id,
                                action="page_progress",
                                status="info",
                                message=f"Processed page {page_num}",
                                extra_data={"page": page_num, "candidate_name": candidate_name}
                            )
                            db.add(log_entry)
                            db.commit()
                            logger.info(f"Logged page_progress: submission={submission_id}, page={page_num}, candidate={candidate_name}")
                    
                    except Exception as e:
                        logger.error(f"Error processing page {page_num}: {e}")
                        errors.append(f"Page {page_num}: {str(e)}")
        else:
            # Sequential processing (original behavior)
            logger.info(f"Processing {len(image_paths)} pages sequentially")
            for i, image_path in enumerate(image_paths, start=1):
                result = self._process_single_page(image_path, i, extraction_prompt)
                
                if "error" in result:
                    errors.append(f"Page {i}: {result['error']}")
                
                # Collect candidate info
                candidate_name = ""
                if "candidate_info" in result:
                    all_candidate_info.append(result["candidate_info"])
                    candidate_name = result["candidate_info"].get('name', "")
                
                all_mcq.extend(result.get("multiple_choice", []))
                all_free_response.extend(result.get("free_response", []))

                # Log progress to ProcessingLog if db and submission_id are provided
                if db is not None and submission_id is not None:
                    from backend.db.models import ProcessingLog
                    log_entry = ProcessingLog(
                        submission_id=submission_id,
                        action="page_progress",
                        status="info",
                        message=f"Processed page {i}",
                        extra_data={"page": i, "candidate_name": candidate_name}
                    )
                    db.add(log_entry)
                    db.commit()
                    logger.info(f"Logged page_progress: submission={submission_id}, page={i}, candidate={candidate_name}")
        
        elapsed_time = time.time() - start_time
        logger.info(f"Processing completed in {elapsed_time:.2f} seconds ({elapsed_time/len(image_paths):.2f}s per page)")
        
        # Sort by page number first, then by question number
        result = {
            "multiple_choice": sorted(all_mcq, key=lambda x: (x.get('page', 0), x['question'])),
            "free_response": sorted(all_free_response, key=lambda x: (x.get('page', 0), x['question'])),
            "candidate_info": all_candidate_info,
            "pages_processed": len(image_paths),
            "total_mcq": len(all_mcq),
            "total_free_response": len(all_free_response),
            "processing_time": round(elapsed_time, 2)
        }
        
        if errors:
            result["errors"] = errors
        
        logger.info(f"Combined extraction: {len(result['multiple_choice'])} MCQ, {len(result['free_response'])} free response")
        return result
    
    def extract_with_context(
        self,
        image_path: str,
        exam_context: Dict
    ) -> Dict:
        """
        Extract answers with additional context about the exam
        
        Args:
            image_path: Path to exam sheet image
            exam_context: Dict with exam details (e.g., total questions, question types)
            
        Returns:
            Dict with extracted answers
        """
        # Build context-aware prompt
        context_prompt = f"""
You are analyzing an exam answer sheet with the following details:
- Total MCQ questions: {exam_context.get('total_mcq', 'unknown')}
- Total free response questions: {exam_context.get('total_free_response', 'unknown')}
- Exam name: {exam_context.get('exam_name', 'N/A')}

Extract ALL answers accurately.

Return ONLY valid JSON in this exact format:
{{
  "multiple_choice": [
    {{"question": 1, "answer": "A"}}
  ],
  "free_response": [
    {{"question": 1, "response": "The complete answer text..."}}
  ]
}}
"""
        
        return self.extract_from_image(image_path, context_prompt)
    
    def extract_with_template(
        self,
        image_path: str,
        template_name: str = "default"
    ) -> Dict:
        """
        Extract answers using a pre-defined template with example images
        
        This uses few-shot learning by showing the AI example images and their
        expected outputs, making it recognize your specific format better.
        
        Args:
            image_path: Path to exam sheet image to extract from
            template_name: Name of template to use (default, bubble_sheet, handwritten, etc.)
            
        Returns:
            Dict with extracted answers
        """
        # Build few-shot prompt with examples
        prompt_parts = ["""
You are analyzing an exam answer sheet. I will show you example images of this format first, 
then the actual image to extract from.

LEARN FROM THESE EXAMPLES:
"""]
        
        # Load template examples if they exist
        template_path = os.path.join(self.template_dir, template_name)
        if os.path.exists(template_path):
            example_files = sorted([f for f in os.listdir(template_path) if f.endswith(('.png', '.jpg', '.jpeg'))])
            
            for example_file in example_files[:3]:  # Use up to 3 examples
                example_path = os.path.join(template_path, example_file)
                json_path = example_path.rsplit('.', 1)[0] + '.json'
                
                if os.path.exists(json_path):
                    with open(json_path, 'r') as f:
                        expected_output = json.load(f)
                    
                    example_image = Image.open(example_path)
                    prompt_parts.append(f"\n--- EXAMPLE {len([p for p in prompt_parts if isinstance(p, Image.Image)]) + 1} ---")
                    prompt_parts.append(str(example_image))
                    prompt_parts.append(f"\nExpected output for this example:\n{json.dumps(expected_output, indent=2)}\n")
        
        # Add the actual image to extract from
        prompt_parts.append("""
--- NOW EXTRACT FROM THIS IMAGE ---
Using the same format as the examples above, extract ALL answers from this exam sheet.

Return ONLY valid JSON in this exact format:
{
  "multiple_choice": [
    {"question": 1, "answer": "A"}
  ],
  "free_response": [
    {"question": 1, "response": "The complete answer text..."}
  ]
}
""")
        
        actual_image = Image.open(image_path)
        prompt_parts.append(str(actual_image))
        try:
            logger.info(f"Using few-shot learning with template: {template_name}")
            response = self.model.generate_content(prompt_parts)
            content = response.text
            # Parse JSON response
            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                import re
                json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group(1))
                else:
                    logger.error(f"Failed to parse JSON from response: {content}")
                    result = {"multiple_choice": [], "free_response": []}
            logger.info(f"Extracted {len(result.get('multiple_choice', []))} MCQ and {len(result.get('free_response', []))} free response")
            return result
        except Exception as e:
            logger.error(f"Template-based extraction failed: {str(e)}")
            # Fallback to regular extraction
            return self.extract_from_image(image_path)
    
    def create_template(
        self,
        example_image_path: str,
        expected_output: Dict,
        template_name: str = "default"
    ):
        """
        Create a template example for few-shot learning
        
        This saves an example image and its expected output to teach the AI
        your specific format.
        
        Args:
            example_image_path: Path to example exam sheet image
            expected_output: Dict with the correct extraction for this example
            template_name: Name for this template category
        """
        template_path = os.path.join(self.template_dir, template_name)
        os.makedirs(template_path, exist_ok=True)
        
        # Count existing examples
        existing = len([f for f in os.listdir(template_path) if f.startswith('example_')])
        
        # Copy image and save JSON
        example_num = existing + 1
        image_dest = os.path.join(template_path, f"example_{example_num}.png")
        json_dest = os.path.join(template_path, f"example_{example_num}.json")
        
        # Copy image
        import shutil
        shutil.copy2(example_image_path, image_dest)
        
        # Save expected output
        with open(json_dest, 'w') as f:
            json.dump(expected_output, f, indent=2)
        
        logger.info(f"Created template example {example_num} for '{template_name}' template")
        return {"template": template_name, "example_number": example_num}
    
    def validate_extraction(self, extraction_result: Dict) -> Dict:
        """
        Validate extracted data for common issues
        
        Args:
            extraction_result: Dict from extract_from_image
            
        Returns:
            Dict with validation results and warnings
        """
        warnings = []
        
        mcq = extraction_result.get("multiple_choice", [])
        free_resp = extraction_result.get("free_response", [])
        
        # Check for duplicate questions
        mcq_questions = [item['question'] for item in mcq]
        if len(mcq_questions) != len(set(mcq_questions)):
            warnings.append("Duplicate MCQ question numbers detected")
        
        # Check for invalid MCQ answers
        valid_answers = {'A', 'B', 'C', 'D', 'E'}
        for item in mcq:
            if item['answer'] not in valid_answers:
                warnings.append(f"Invalid MCQ answer '{item['answer']}' for Q{item['question']}")
        
        # Check for empty free responses
        for item in free_resp:
            if not item['response'].strip():
                warnings.append(f"Empty free response for Q{item['question']}")
        
        return {
            "is_valid": len(warnings) == 0,
            "warnings": warnings,
            "mcq_count": len(mcq),
            "free_response_count": len(free_resp)
        }


def get_ai_extractor() -> AIExtractor:
    """Factory function to create AIExtractor instance"""
    return AIExtractor()
