"""
Pydantic schemas for API request/response validation
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime


# ── New flat format schemas ──────────────────────────────────────────

class CandidateResultSchema(BaseModel):
    """Schema for a single candidate's extracted result.
    
    The four fixed header fields (candidate_name, candidate_number, country,
    paper_type) are kept for backward compatibility.  Any additional header
    fields discovered during dynamic format analysis are stored in
    ``extra_fields``.
    """
    candidate_name: str = Field("", description="Full name of the candidate")
    candidate_number: str = Field("", description="Candidate ID / number")
    country: str = Field("", description="Country")
    paper_type: str = Field("", description="Paper type or level")
    extra_fields: Optional[Dict[str, str]] = Field(
        None,
        description="Additional header fields detected during dynamic format analysis"
    )
    answers: Dict[str, str] = Field(
        default_factory=dict,
        description='MCQ answers as {"1": "D", "2": "B", "3": "BL", "4": "IN", ...}'
    )
    drawing_questions: Dict[str, str] = Field(
        default_factory=dict,
        description='Free-response / drawing answers as {"31": "student text..."}'
    )


class MarkedCandidateResultSchema(CandidateResultSchema):
    """Schema for a marked candidate result (after comparing to answer key)"""
    marked_answers: Dict[str, str] = Field(
        default_factory=dict,
        description='Marked MCQ: P=correct, BL=blank, IN=invalid, else student wrong answer letter'
    )
    marked_drawing: Dict[str, str] = Field(
        default_factory=dict,
        description='Marked drawing: P=correct, BL=blank, IM=incorrect'
    )
    score: Optional[Dict] = Field(
        None,
        description='Score object: {"correct": 25, "total": 30, "percentage": 83.3}'
    )


class AnswerKeySchema(BaseModel):
    """Schema for creating / updating an answer key"""
    name: str = Field(..., description="Name of the answer key, e.g. 'UZ1 Paper A'")
    paper_type: Optional[str] = Field(None, description="Paper type filter")
    answers: Dict[str, str] = Field(
        ..., description='Correct MCQ answers: {"1": "D", "2": "B", ...}'
    )
    drawing_key: Optional[Dict[str, str]] = Field(
        None, description='Correct drawing keywords: {"31": "circle"}'
    )


class AnswerKeyResponse(AnswerKeySchema):
    """Schema for answer key response"""
    id: int
    total_questions: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class MarkRequest(BaseModel):
    """Schema for marking request"""
    answer_key_id: Optional[int] = Field(None, description="ID of an existing answer key")
    answer_key: Optional[Dict[str, str]] = Field(None, description="Inline answer key")
    drawing_key: Optional[Dict[str, str]] = Field(None, description="Inline drawing key")


class ExtractionResultSchema(BaseModel):
    """Schema for extraction result (new flat format)"""
    document_information: Optional[Dict] = None
    candidates: List[CandidateResultSchema] = []
    validation: Optional[Dict] = None
    extraction_errors: Optional[List[str]] = None


# ── Legacy schemas (kept for backward compatibility) ─────────────────

class MultipleChoiceAnswerSchema(BaseModel):
    """Schema for multiple choice answer (legacy)"""
    question: int = Field(..., description="Question number")
    answer: str = Field(..., description="Selected answer")


class FreeResponseAnswerSchema(BaseModel):
    """Schema for free response answer (legacy)"""
    question: int = Field(..., description="Question number")
    response: str = Field(..., description="Response text")


class UploadResponse(BaseModel):
    """Schema for upload response"""
    status: str
    message: str
    submission_id: int
    filename: str
    storage_path: str


class ProcessingStatusResponse(BaseModel):
    """Schema for processing status"""
    submission_id: int
    filename: str
    status: str
    created_at: datetime
    processed_at: Optional[datetime]
    pages_count: int
    candidates_count: int = 0
    answers_count: int = 0
    drawing_count: int = 0
    error_message: Optional[str] = None
    current_page: Optional[int] = None
    current_candidate_name: Optional[str] = None


class SubmissionDetailResponse(BaseModel):
    """Schema for submission details with candidate results"""
    submission_id: int
    filename: str
    status: str
    created_at: datetime
    processed_at: Optional[datetime]
    candidates: List[CandidateResultSchema] = []
    
    class Config:
        from_attributes = True


class ErrorResponse(BaseModel):
    """Schema for error responses"""
    error: str
    detail: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ---------------------------- Exam Schemas -----------------------------


class ExamCreateSchema(BaseModel):
    name: str = Field(..., description="Exam name")


class ExamResponse(BaseModel):
    id: int
    name: str
    correction_pdf_path: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ExamDocumentResponse(BaseModel):
    id: int
    exam_id: int
    country: Optional[str]
    file_path: str
    pages_count: Optional[int]
    uploaded_at: datetime

    class Config:
        from_attributes = True


class GeneratedJSONResponse(BaseModel):
    id: int
    exam_id: int
    filename: str
    file_path: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ExamDetailResponse(ExamResponse):
    documents: List[ExamDocumentResponse] = []
    generated_jsons: List[GeneratedJSONResponse] = []
