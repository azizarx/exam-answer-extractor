"""
Pydantic schemas for API request/response validation
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class MultipleChoiceAnswerSchema(BaseModel):
    """Schema for multiple choice answer"""
    question: int = Field(..., description="Question number")
    answer: str = Field(..., pattern="^[A-E]$", description="Selected answer (A-E)")


class FreeResponseAnswerSchema(BaseModel):
    """Schema for free response answer"""
    question: int = Field(..., description="Question number")
    response: str = Field(..., description="Response text")


class ExtractionResultSchema(BaseModel):
    """Schema for extraction result"""
    filename: str
    extraction_timestamp: str
    total_multiple_choice: int
    total_free_response: int
    multiple_choice: List[MultipleChoiceAnswerSchema]
    free_response: List[FreeResponseAnswerSchema]
    metadata: Optional[dict] = None
    validation: Optional[dict] = None
    errors: Optional[List[str]] = None
    pages_processed: Optional[int] = None


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
    mcq_count: int
    free_response_count: int
    error_message: Optional[str] = None
    current_page: Optional[int] = None
    current_candidate_name: Optional[str] = None


class SubmissionDetailResponse(BaseModel):
    """Schema for submission details with answers"""
    submission_id: int
    filename: str
    status: str
    created_at: datetime
    processed_at: Optional[datetime]
    multiple_choice: List[MultipleChoiceAnswerSchema]
    free_response: List[FreeResponseAnswerSchema]
    
    class Config:
        from_attributes = True


class ErrorResponse(BaseModel):
    """Schema for error responses"""
    error: str
    detail: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
