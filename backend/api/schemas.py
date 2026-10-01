"""
Pydantic schemas for API request/response validation
"""
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, List, Literal, Optional, Union
from datetime import datetime


# ── New flat format schemas ──────────────────────────────────────────

JsonScalar = Union[str, int, float, bool, None]
MarkingStatus = Literal["unavailable", "processing", "completed", "failed"]
OutcomeStatus = Literal["correct", "incorrect", "blank", "invalid", "needs_review"]


class QuestionOutcomeSchema(BaseModel):
    question_number: int
    status: OutcomeStatus
    response: JsonScalar = None
    awarded_marks: float
    max_marks: float
    normalizer: str
    judge_source: Optional[str] = None
    judge_verdict: Optional[str] = None
    judge_reason: Optional[str] = None


class CandidateWeightedMarkingSchema(BaseModel):
    candidate_result_id: int
    candidate_number: str = ""
    awarded_marks: float
    max_marks: float
    percentage: float
    outcomes: List[QuestionOutcomeSchema] = Field(default_factory=list)


class AnswerKeyProvenanceSchema(BaseModel):
    answer_key_id: Optional[int] = None
    template_id: Optional[str] = None
    version: Optional[int] = None
    source_filename: Optional[str] = None
    source_sha256: Optional[str] = None
    total_marks: Optional[int] = None


class MarkingRunMetadataSchema(BaseModel):
    id: int
    status: MarkingStatus
    answer_key_id: Optional[int] = None
    provenance: Optional[AnswerKeyProvenanceSchema] = None
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime


class MarkingRunDetailSchema(MarkingRunMetadataSchema):
    candidates: List[CandidateWeightedMarkingSchema] = Field(default_factory=list)


class SubmissionMarkingDetailSchema(BaseModel):
    submission_id: int
    latest_run: Optional[MarkingRunDetailSchema] = None
    history: List[MarkingRunMetadataSchema] = Field(default_factory=list)


class ManualRemarkRequest(BaseModel):
    answer_key_id: Optional[int] = None


class ManualRemarkResponse(BaseModel):
    status: Literal["success"] = "success"
    submission_id: int
    total_candidates_marked: int
    run: MarkingRunDetailSchema


class ConfirmReviewRequest(BaseModel):
    """Human correction of a candidate: answers and/or identity fields.

    Every field is optional and defaults preserve the original
    confirm-review behaviour, so existing API callers are unaffected.
    """
    answers: Dict[str, str] = Field(default_factory=dict)
    clear_all_review: bool = False
    candidate_name: Optional[str] = Field(
        None, description="Set to correct a misread name; omit to leave unchanged"
    )
    candidate_number: Optional[str] = Field(
        None, description="Set to correct a misread candidate number"
    )
    country: Optional[str] = None
    paper_type: Optional[str] = None
    remark: bool = Field(
        False,
        description=(
            "Re-mark this candidate after applying the edit. The run stays "
            "complete: other candidates' marks carry forward unchanged."
        ),
    )
    edited_by: Optional[str] = Field(
        None, description="Recorded in the edit history; free text, unauthenticated"
    )


class ConfirmReviewResponse(BaseModel):
    status: Literal["success"] = "success"
    candidate_result_id: int
    needs_review_questions: List[str] = Field(default_factory=list)
    answers: Dict[str, str] = Field(default_factory=dict)
    candidate_name: str = ""
    candidate_number: str = ""
    country: str = ""
    paper_type: str = ""
    edits_recorded: int = Field(
        0, description="How many field/answer changes this request actually applied"
    )
    remarked: bool = False
    marking_run_id: Optional[int] = None
    remark_error: Optional[str] = None
    remark_job_id: Optional[int] = None


class AnswerKeyMetadataSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    template_id: Optional[str] = None
    version: int
    source_filename: Optional[str] = None
    source_sha256: Optional[str] = None
    total_questions: Optional[int] = None
    total_marks: Optional[int] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

class CandidateResultSchema(BaseModel):
    """Schema for a single candidate's extracted result.
    
    The four fixed header fields (candidate_name, candidate_number, country,
    paper_type) are kept for backward compatibility.  Any additional header
    fields discovered during dynamic format analysis are stored in
    ``extra_fields``.
    """
    id: Optional[int] = Field(None, description="Stable persisted candidate result ID")
    candidate_name: str = Field("", description="Full name of the candidate")
    candidate_number: str = Field("", description="Candidate ID / number")
    country: str = Field("", description="Country")
    paper_type: str = Field("", description="Paper type or level")
    page_number: Optional[int] = Field(
        None,
        description="1-based page of the uploaded PDF this candidate was read from",
    )
    template_id: Optional[str] = Field(
        None, description="Detected or forced layout template for this page"
    )
    detection: Optional[Dict[str, Any]] = Field(
        None, description="Layout detection metadata (method, raw_text, warning)"
    )
    extra_fields: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Additional header / trust metadata. Values may be strings or nested "
            "JSON (e.g. answer_trust dict, needs_review_questions list)."
        ),
    )
    answers: Dict[str, str] = Field(
        default_factory=dict,
        description='MCQ answers as {"1": "D", "2": "B", "3": "BL", "4": "IN", ...}'
    )
    drawing_questions: Optional[Dict[str, str]] = Field(
        None,
        description='Free-response / drawing answers as {"31": "student text..."}'
    )
    marking: Optional[CandidateWeightedMarkingSchema] = None


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
    stage: Optional[str] = None
    queue_position: Optional[int] = None
    pages_completed: int = 0
    attempt: int = 0
    job_id: Optional[int] = None
    archive_status: Optional[str] = None
    archive_error: Optional[str] = None


class SubmissionDetailResponse(BaseModel):
    """Schema for submission details with candidate results"""
    submission_id: int
    filename: str
    status: str
    created_at: datetime
    processed_at: Optional[datetime]
    candidates: List[CandidateResultSchema] = []
    latest_marking: Optional[MarkingRunMetadataSchema] = None
    
    class Config:
        from_attributes = True


class MarkedExportSubmissionSchema(BaseModel):
    id: int
    filename: str
    template_id: Optional[str] = None
    status: str
    pages_count: int
    created_at: datetime
    processed_at: Optional[datetime] = None


class MarkedCandidateExportSchema(BaseModel):
    id: int
    page_number: Optional[int] = None
    candidate_name: str = ""
    candidate_number: str = ""
    country: str = ""
    paper_type: str = ""
    extra_fields: Optional[Dict[str, Any]] = None
    answers: Dict[str, str] = Field(default_factory=dict)
    drawing_questions: Optional[Dict[str, str]] = None
    marking: CandidateWeightedMarkingSchema


class MarkedExportSchema(BaseModel):
    submission: MarkedExportSubmissionSchema
    marking: MarkingRunMetadataSchema
    candidates: List[MarkedCandidateExportSchema] = Field(default_factory=list)


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
