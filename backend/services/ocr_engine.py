"""
OCR Engine Service
Extracts text from images using Tesseract OCR
"""
import pytesseract
from PIL import Image
from typing import List, Dict, Optional
import logging
import re

logger = logging.getLogger(__name__)


class OCREngine:
    """OCR engine for text extraction from images"""
    
    def __init__(self, lang: str = 'eng', config: str = ''):
        """
        Initialize OCR engine
        
        Args:
            lang: Language code for OCR (e.g., 'eng', 'spa', 'fra')
            config: Custom Tesseract configuration string
        """
        self.lang = lang
        self.config = config
        logger.info(f"Initialized OCREngine with language={lang}")
    
    def extract_text(self, image_path: str) -> str:
        """
        Extract text from a single image
        
        Args:
            image_path: Path to image file
            
        Returns:
            Extracted text as string
        """
        try:
            image = Image.open(image_path)
            text = pytesseract.image_to_string(
                image,
                lang=self.lang,
                config=self.config
            )
            logger.info(f"Extracted {len(text)} characters from {image_path}")
            return text
        except Exception as e:
            logger.error(f"Failed to extract text from {image_path}: {str(e)}")
            return ""
    
    def extract_from_pil(self, image: Image.Image) -> str:
        """
        Extract text from PIL Image object
        
        Args:
            image: PIL Image object
            
        Returns:
            Extracted text as string
        """
        try:
            text = pytesseract.image_to_string(
                image,
                lang=self.lang,
                config=self.config
            )
            logger.info(f"Extracted {len(text)} characters from PIL image")
            return text
        except Exception as e:
            logger.error(f"Failed to extract text from PIL image: {str(e)}")
            return ""
    
    def extract_from_multiple(self, image_paths: List[str]) -> List[Dict[str, str]]:
        """
        Extract text from multiple images
        
        Args:
            image_paths: List of image file paths
            
        Returns:
            List of dicts with page number and extracted text
        """
        results = []
        for i, image_path in enumerate(image_paths, start=1):
            text = self.extract_text(image_path)
            results.append({
                "page": i,
                "image_path": image_path,
                "text": text
            })
        
        logger.info(f"Extracted text from {len(image_paths)} images")
        return results
    
    def extract_with_confidence(self, image_path: str) -> Dict:
        """
        Extract text with confidence scores
        
        Args:
            image_path: Path to image file
            
        Returns:
            Dict with text and confidence information
        """
        try:
            image = Image.open(image_path)
            data = pytesseract.image_to_data(
                image,
                lang=self.lang,
                output_type=pytesseract.Output.DICT
            )
            
            # Filter out low confidence results
            text_parts = []
            confidences = []
            
            for i, conf in enumerate(data['conf']):
                if int(conf) > 0:  # Valid confidence score
                    text_parts.append(data['text'][i])
                    confidences.append(int(conf))
            
            full_text = ' '.join(text_parts)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0
            
            logger.info(f"Extracted text with avg confidence {avg_confidence:.2f}%")
            
            return {
                "text": full_text,
                "average_confidence": avg_confidence,
                "word_count": len(text_parts)
            }
        except Exception as e:
            logger.error(f"Failed to extract text with confidence: {str(e)}")
            return {"text": "", "average_confidence": 0, "word_count": 0}


class AnswerParser:
    """Parser for extracting structured answers from OCR text"""
    
    @staticmethod
    def extract_multiple_choice(text: str) -> List[Dict[str, str]]:
        """
        Extract multiple choice answers from text
        
        Args:
            text: OCR extracted text
            
        Returns:
            List of dicts with question number and selected answer
        """
        # Pattern matches: Q1: A, Q1) B, Q1 - C, 1. A, etc.
        patterns = [
            r"Q[uestion]*\s*(\d+)\s*[:\-\)\.]*\s*([A-E])",  # Q1: A
            r"(\d+)\s*[:\-\)\.]\s*([A-E])",  # 1. A
            r"Question\s+(\d+)\s*[:\-]*\s*([A-E])",  # Question 1: A
        ]
        
        answers = []
        seen_questions = set()
        
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                question_num = match.group(1)
                answer = match.group(2).upper()
                
                # Avoid duplicates
                if question_num not in seen_questions:
                    answers.append({
                        "question": int(question_num),
                        "answer": answer
                    })
                    seen_questions.add(question_num)
        
        # Sort by question number
        answers.sort(key=lambda x: x['question'])
        
        logger.info(f"Extracted {len(answers)} multiple choice answers")
        return answers
    
    @staticmethod
    def extract_free_response(text: str) -> List[Dict[str, str]]:
        """
        Extract free response answers from text
        
        Args:
            text: OCR extracted text
            
        Returns:
            List of dicts with question number and response text
        """
        responses = []
        
        # Look for patterns like "Free Response 1:", "Essay Question 1:", etc.
        patterns = [
            r"(?:FREE\s+RESPONSE|ESSAY|SHORT\s+ANSWER)\s+(?:QUESTION\s+)?(\d+)\s*[:\-]?\s*(.*?)(?=(?:FREE\s+RESPONSE|ESSAY|SHORT\s+ANSWER|\Z))",
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE | re.DOTALL)
            for match in matches:
                question_num = match.group(1)
                response_text = match.group(2).strip()
                
                if response_text:
                    responses.append({
                        "question": int(question_num),
                        "response": response_text
                    })
        
        logger.info(f"Extracted {len(responses)} free response answers")
        return responses
    
    @staticmethod
    def extract_all(text: str) -> Dict:
        """
        Extract both multiple choice and free response answers
        
        Args:
            text: OCR extracted text
            
        Returns:
            Dict with both answer types
        """
        return {
            "multiple_choice": AnswerParser.extract_multiple_choice(text),
            "free_response": AnswerParser.extract_free_response(text)
        }


def get_ocr_engine(lang: str = 'eng') -> OCREngine:
    """Factory function to create OCREngine instance"""
    return OCREngine(lang=lang)
