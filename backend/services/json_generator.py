"""
JSON Generator Service
Formats extracted exam data into structured JSON output
"""
import json
from typing import Dict, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class JSONGenerator:
    """Generates structured JSON from extracted exam data"""
    
    @staticmethod
    def generate(
        filename: str,
        multiple_choice: List[Dict],
        free_response: List[Dict],
        metadata: Optional[Dict] = None
    ) -> str:
        """
        Generate formatted JSON from extracted data
        
        Args:
            filename: Original PDF filename
            multiple_choice: List of MCQ answers
            free_response: List of free response answers
            metadata: Optional metadata about the extraction
            
        Returns:
            JSON string
        """
        output = {
            "filename": filename,
            "extraction_timestamp": datetime.utcnow().isoformat(),
            "total_multiple_choice": len(multiple_choice),
            "total_free_response": len(free_response),
            "multiple_choice": multiple_choice,
            "free_response": free_response
        }
        
        # Add metadata if provided
        if metadata:
            output["metadata"] = metadata
        
        json_str = json.dumps(output, indent=2, ensure_ascii=False)
        logger.info(f"Generated JSON for {filename}: {len(json_str)} bytes")
        return json_str
    
    @staticmethod
    def generate_with_validation(
        filename: str,
        extraction_result: Dict,
        validation_result: Optional[Dict] = None
    ) -> str:
        """
        Generate JSON with validation information
        
        Args:
            filename: Original PDF filename
            extraction_result: Dict from AI extractor
            validation_result: Optional validation results
            
        Returns:
            JSON string
        """
        mcq_answers = extraction_result.get("multiple_choice", [])
        fr_answers = extraction_result.get("free_response", [])
        candidate_infos = extraction_result.get("candidate_info", [])
        
        # Organize answers by page
        pages_data = {}
        
        # Initialize pages with candidate info if available
        for info in candidate_infos:
            page = info.get('page', 1)
            if page not in pages_data:
                pages_data[page] = {
                    "page_number": page,
                    "candidate_info": {k: v for k, v in info.items() if k != 'page'},
                    "multiple_choice": [],
                    "free_response": []
                }
            else:
                pages_data[page]["candidate_info"] = {k: v for k, v in info.items() if k != 'page'}
        
        # Process MCQ answers
        for mcq in mcq_answers:
            page = mcq.get('page', 1)
            if page not in pages_data:
                pages_data[page] = {
                    "page_number": page,
                    "multiple_choice": [],
                    "free_response": []
                }
            pages_data[page]["multiple_choice"].append({
                "question": mcq['question'],
                "answer": mcq['answer']
            })
        
        # Process Free Response answers
        for fr in fr_answers:
            page = fr.get('page', 1)
            if page not in pages_data:
                pages_data[page] = {
                    "page_number": page,
                    "multiple_choice": [],
                    "free_response": []
                }
            pages_data[page]["free_response"].append({
                "question": fr['question'],
                "response": fr['response']
            })
        
        # Convert to sorted list of submissions
        submissions = []
        for page in sorted(pages_data.keys()):
            page_data = pages_data[page]
            
            # Get candidate info for this page, or default to empty
            candidate_info = page_data.get("candidate_info", {
                "name": "",
                "id": "",
                "country": "",
                "level": ""
            })
            
            # Build combined answers list: MCQ then Free Response (back-to-back)
            combined_answers = []
            for mc in page_data["multiple_choice"]:
                combined_answers.append({
                    "type": "mcq",
                    "question": mc["question"],
                    "answer": mc["answer"],
                })
            for fr_item in page_data["free_response"]:
                combined_answers.append({
                    "type": "free_response",
                    "question": fr_item["question"],
                    "response": fr_item["response"],
                })

            submission = {
                "page_number": page,
                "candidate_information": {
                    "candidate_name": candidate_info.get("name", ""),
                    "candidate_number": candidate_info.get("id", ""),
                    "country": candidate_info.get("country", ""),
                    "level": candidate_info.get("level", "")
                },
                "multiple_choice": page_data["multiple_choice"],
                "free_response": page_data["free_response"],
                "answers": combined_answers,
                "summary": {
                    "multiple_choice_count": len(page_data["multiple_choice"]),
                    "free_response_count": len(page_data["free_response"])
                }
            }
            submissions.append(submission)
        
        output = {
            "document_information": {
                "filename": filename,
                "extraction_timestamp": datetime.utcnow().isoformat(),
                "total_pages": len(pages_data),
                "total_submissions": len(submissions),
                "total_multiple_choice": len(mcq_answers),
                "total_free_response": len(fr_answers)
            },
            "submissions": submissions
        }
        
        # Add validation info
        if validation_result:
            output["validation"] = validation_result
        
        # Add any errors from extraction
        if "errors" in extraction_result:
            output["extraction_errors"] = extraction_result["errors"]
        
        json_str = json.dumps(output, indent=2, ensure_ascii=False)
        logger.info(f"Generated validated JSON for {filename}")
        return json_str
    
    @staticmethod
    def generate_summary(extraction_result: Dict) -> Dict:
        """
        Generate a summary of the extraction
        
        Args:
            extraction_result: Dict from AI extractor
            
        Returns:
            Summary dict
        """
        mcq = extraction_result.get("multiple_choice", [])
        free_resp = extraction_result.get("free_response", [])
        
        summary = {
            "total_questions": len(mcq) + len(free_resp),
            "multiple_choice_count": len(mcq),
            "free_response_count": len(free_resp),
            "mcq_questions": [item['question'] for item in mcq],
            "free_response_questions": [item['question'] for item in free_resp]
        }
        
        # Answer distribution for MCQ
        if mcq:
            answer_counts = {}
            for item in mcq:
                ans = item['answer']
                answer_counts[ans] = answer_counts.get(ans, 0) + 1
            summary["mcq_answer_distribution"] = answer_counts
        
        return summary
    
    @staticmethod
    def parse(json_str: str) -> Dict:
        """
        Parse JSON string back to dict
        
        Args:
            json_str: JSON string
            
        Returns:
            Parsed dict
        """
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {str(e)}")
            return {}
    
    @staticmethod
    def format_for_display(data: Dict) -> str:
        """
        Format extraction data for human-readable display
        
        Args:
            data: Extraction result dict
            
        Returns:
            Formatted string
        """
        lines = []
        lines.append(f"=== Exam Answer Sheet: {data.get('filename', 'Unknown')} ===")
        lines.append(f"Extracted at: {data.get('extraction_timestamp', 'N/A')}")
        lines.append("")
        
        # Multiple Choice
        mcq = data.get('multiple_choice', [])
        if mcq:
            lines.append(f"Multiple Choice Answers ({len(mcq)}):")
            for item in mcq:
                lines.append(f"  Q{item['question']}: {item['answer']}")
        else:
            lines.append("Multiple Choice Answers: None")
        
        lines.append("")
        
        # Free Response
        free_resp = data.get('free_response', [])
        if free_resp:
            lines.append(f"Free Response Answers ({len(free_resp)}):")
            for item in free_resp:
                response = item['response'][:100] + "..." if len(item['response']) > 100 else item['response']
                lines.append(f"  Q{item['question']}: {response}")
        else:
            lines.append("Free Response Answers: None")
        
        return "\n".join(lines)


def get_json_generator() -> JSONGenerator:
    """Factory function to create JSONGenerator instance"""
    return JSONGenerator()
