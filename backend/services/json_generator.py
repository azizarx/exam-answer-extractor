"""
JSON Generator Service
Formats extracted exam data into structured JSON output.

Supports **dynamic fields** – the candidate objects may contain any header
fields detected during format analysis (not just the original UZ1 set of
candidate_name / candidate_number / country / paper_type).  The generator
preserves whatever keys the AI extractor returns.

Auto-marking codes:
  P  = correct answer
  BL = blank (unanswered)
  IN = invalid (two or more marks)
  IM = incorrect drawing / free-response
  <student_answer> = wrong MCQ answer (e.g. "B" when correct was "D")
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
        extraction_result: Dict,
        validation_result: Optional[Dict] = None
    ) -> str:
        """Generate the flat per-candidate JSON from extraction results.

        All header fields returned by the AI extractor are preserved as-is,
        so this works with any exam format (UZ1, ZONE Z, etc.).
        """
        candidates_raw = extraction_result.get("candidates", [])

        candidates = []
        for raw in candidates_raw:
            # Copy every key EXCEPT internal metadata
            candidate = {
                k: v for k, v in raw.items()
                if k not in ("page_number",)
            }
            candidates.append(candidate)

        json_str = json.dumps(candidates, indent=2, ensure_ascii=False)
        logger.info(f"Generated JSON for {filename}: {len(candidates)} candidates, {len(json_str)} bytes")
        return json_str
    
    @staticmethod
    def generate_with_validation(
        filename: str,
        extraction_result: Dict,
        validation_result: Optional[Dict] = None
    ) -> str:
        """Generate JSON with metadata envelope for storage.

        Preserves all dynamic header fields from the AI extractor.
        """
        candidates_raw = extraction_result.get("candidates", [])

        candidates = []
        for raw in candidates_raw:
            candidate = {
                k: v for k, v in raw.items()
                if k not in ("page_number",)
            }
            candidates.append(candidate)

        output = {
            "document_information": {
                "filename": filename,
                "extraction_timestamp": datetime.utcnow().isoformat(),
                "total_candidates": len(candidates),
                "pages_processed": extraction_result.get("pages_processed", 0),
                "pages_with_data": extraction_result.get("pages_with_data", 0),
                "processing_time": extraction_result.get("processing_time", 0),
            },
            "candidates": candidates,
        }

        # Include detected format metadata if available
        if "detected_format" in extraction_result:
            output["document_information"]["detected_format"] = extraction_result["detected_format"]

        logs = {}
        if validation_result:
            logs["validation"] = validation_result

        if "errors" in extraction_result:
            logs["extraction_errors"] = extraction_result["errors"]

        if logs:
            output["logs"] = logs

        json_str = json.dumps(output, indent=2, ensure_ascii=False)
        logger.info(f"Generated validated JSON for {filename}: {len(candidates)} candidates")
        return json_str

    @staticmethod
    def generate_minimal(
        filename: str,
        extraction_result: Dict,
    ) -> str:
        """Generate a minimal JSON output for downstream consumption.

        The output is a list of candidate objects with only the most important
        fields:
          - answers: mapping of question number → answer (including "DR" for drawing)
          - candidate_number: identifier for the student
          - paper_type: exam variant/type

        This avoids including extra/unused header fields and keeps output stable
        even when page formats vary.
        """

        candidates_raw = extraction_result.get("candidates", [])
        output_candidates = []

        for raw in candidates_raw:
            answers = dict(raw.get("answers") or {})
            drawing = raw.get("drawing_questions") or {}
            for q in drawing.keys():
                answers[str(q)] = "DR"

            candidate_number = (
                raw.get("candidate_number")
                or raw.get("candidate_id")
                or raw.get("id")
                or ""
            )
            paper_type = raw.get("paper_type") or raw.get("paper") or ""

            output_candidates.append({
                "answers": answers,
                "candidate_number": str(candidate_number) if candidate_number is not None else "",
                "paper_type": str(paper_type) if paper_type is not None else "",
            })

        json_str = json.dumps(output_candidates, indent=2, ensure_ascii=False)
        logger.info(f"Generated minimal JSON for {filename}: {len(output_candidates)} candidates")
        return json_str
    
    @staticmethod
    def mark_answers(
        candidates: List[Dict],
        answer_key: Dict[str, str],
        drawing_key: Optional[Dict[str, str]] = None
    ) -> List[Dict]:
        """
        Auto-mark candidate answers against an answer key.
        
        Marking rules:
        - MCQ correct  → "P"
        - MCQ wrong    → student's answer letter (e.g. "B")
        - MCQ blank    → "BL"  (stays)
        - MCQ invalid  → "IN"  (stays)
        - Drawing correct  → "P"
        - Drawing wrong    → "IM"
        
        Args:
            candidates: List of candidate dicts with 'answers' and 'drawing_questions'
            answer_key: Dict mapping question number str → correct answer letter
                        e.g. {"1": "D", "2": "B", "3": "A", ...}
            drawing_key: Optional dict mapping question number str → expected response keyword(s)
                        e.g. {"31": "circle", "32": "triangle"}
                        If None, drawing questions are not auto-marked.
            
        Returns:
            List of marked candidate dicts (new 'marked_answers' and 'marked_drawing' fields)
        """
        marked_candidates = []
        
        for candidate in candidates:
            marked = dict(candidate)  # shallow copy
            
            # Mark MCQ answers
            marked_answers = {}
            student_answers = candidate.get("answers", {})
            
            for q_num, correct_answer in answer_key.items():
                student_answer = student_answers.get(str(q_num), "BL")
                
                if student_answer == "BL":
                    marked_answers[str(q_num)] = "BL"
                elif student_answer == "IN":
                    marked_answers[str(q_num)] = "IN"
                elif student_answer.upper() == correct_answer.upper():
                    marked_answers[str(q_num)] = "P"
                else:
                    # Wrong answer — keep the student's answer letter
                    marked_answers[str(q_num)] = student_answer
            
            marked["marked_answers"] = marked_answers
            
            # Mark drawing / free-response questions
            if drawing_key:
                marked_drawing = {}
                student_drawing = candidate.get("drawing_questions", {})
                
                for q_num, expected in drawing_key.items():
                    student_response = student_drawing.get(str(q_num), "")
                    
                    if not student_response or student_response.strip() == "":
                        marked_drawing[str(q_num)] = "BL"
                    elif expected.lower() in student_response.lower():
                        marked_drawing[str(q_num)] = "P"
                    else:
                        marked_drawing[str(q_num)] = "IM"
                
                marked["marked_drawing"] = marked_drawing
            
            # Calculate score
            total_correct = sum(1 for v in marked_answers.values() if v == "P")
            total_questions = len(answer_key)
            if drawing_key and "marked_drawing" in marked:
                total_correct += sum(1 for v in marked["marked_drawing"].values() if v == "P")
                total_questions += len(drawing_key)
            
            marked["score"] = {
                "correct": total_correct,
                "total": total_questions,
                "percentage": round((total_correct / total_questions * 100), 1) if total_questions > 0 else 0
            }
            
            marked_candidates.append(marked)
        
        return marked_candidates
    
    @staticmethod
    def generate_summary(extraction_result: Dict) -> Dict:
        """
        Generate a summary of the extraction
        
        Args:
            extraction_result: Dict from AI extractor (new format)
            
        Returns:
            Summary dict
        """
        candidates = extraction_result.get("candidates", [])
        
        total_answers = 0
        total_drawing = 0
        answer_distribution: Dict[str, int] = {}
        
        for candidate in candidates:
            answers = candidate.get("answers", {})
            drawing = candidate.get("drawing_questions", {})
            total_answers += len(answers)
            total_drawing += len(drawing)
            
            for answer in answers.values():
                answer_distribution[answer] = answer_distribution.get(answer, 0) + 1
        
        return {
            "total_candidates": len(candidates),
            "total_answers": total_answers,
            "total_drawing_questions": total_drawing,
            "answer_distribution": answer_distribution,
            "pages_processed": extraction_result.get("pages_processed", 0),
            "pages_with_data": extraction_result.get("pages_with_data", 0),
            "processing_time": extraction_result.get("processing_time", 0),
        }
    
    @staticmethod
    def parse(json_str: str) -> Dict:
        """
        Parse JSON string back to dict
        
        Args:
            json_str: JSON string
            
        Returns:
            Parsed dict or list
        """
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {str(e)}")
            return {}
    
    @staticmethod
    def format_for_display(data) -> str:
        """Format extraction data for human-readable display.

        Handles dynamic header fields — prints all keys that are not
        ``answers``, ``drawing_questions``, ``marked_answers``, ``marked_drawing``,
        ``score``, or ``page_number``.
        """
        lines = []
        SKIP_KEYS = {"answers", "drawing_questions", "marked_answers", "marked_drawing", "score", "page_number"}

        if isinstance(data, dict):
            doc_info = data.get("document_information", {})
            lines.append(f"=== Exam Answer Sheet: {doc_info.get('filename', 'Unknown')} ===")
            lines.append(f"Extracted at: {doc_info.get('extraction_timestamp', 'N/A')}")
            lines.append(f"Total candidates: {doc_info.get('total_candidates', 0)}")
            lines.append("")
            candidates = data.get("candidates", [])
        elif isinstance(data, list):
            candidates = data
            lines.append("=== Exam Results ===")
            lines.append("")
        else:
            return "Invalid data format"

        for i, candidate in enumerate(candidates, 1):
            lines.append(f"--- Candidate {i} ---")

            # Print all header/metadata fields dynamically
            for key, value in candidate.items():
                if key in SKIP_KEYS:
                    continue
                label = key.replace("_", " ").title()
                lines.append(f"  {label}: {value}")

            answers = candidate.get("answers", {})
            if answers:
                lines.append(f"  Answers ({len(answers)}):")
                for q_num in sorted(answers.keys(), key=lambda x: int(x) if x.isdigit() else 0):
                    lines.append(f"    Q{q_num}: {answers[q_num]}")

            drawing = candidate.get("drawing_questions", {})
            if drawing:
                lines.append(f"  Drawing Questions ({len(drawing)}):")
                for q_num in sorted(drawing.keys(), key=lambda x: int(x) if x.isdigit() else 0):
                    resp = drawing[q_num]
                    if len(str(resp)) > 80:
                        resp = str(resp)[:80] + "..."
                    lines.append(f"    Q{q_num}: {resp}")

            marked = candidate.get("marked_answers", {})
            if marked:
                lines.append(f"  Marked Answers:")
                for q_num in sorted(marked.keys(), key=lambda x: int(x) if x.isdigit() else 0):
                    lines.append(f"    Q{q_num}: {marked[q_num]}")

            score = candidate.get("score")
            if score:
                lines.append(f"  Score: {score['correct']}/{score['total']} ({score['percentage']}%)")

            lines.append("")

        return "\n".join(lines)


def get_json_generator() -> JSONGenerator:
    """Factory function to create JSONGenerator instance"""
    return JSONGenerator()
