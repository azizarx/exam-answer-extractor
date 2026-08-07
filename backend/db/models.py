"""
Database models for exam answers
"""
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship
from datetime import datetime
from backend.db.database import Base


class ExamSubmission(Base):
    """Model for exam submission metadata"""
    __tablename__ = "exam_submissions"
    
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False, index=True)
    original_pdf_key = Column(String(500), nullable=False)  # local storage path
    result_json_key = Column(String(500), nullable=True)  # local storage path for results
    template_id = Column(String(100), nullable=True, index=True)  # exam layout chosen at upload
    status = Column(String(50), default="pending", index=True)  # pending, processing, completed, failed
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    pages_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    
    # Relationships
    candidate_results = relationship("CandidateResult", back_populates="submission", cascade="all, delete-orphan")
    marking_runs = relationship("MarkingRun", back_populates="submission", cascade="all, delete-orphan")
    
    # Keep legacy relationships for backward compatibility during migration
    mcq_answers = relationship("MultipleChoiceAnswer", back_populates="submission", cascade="all, delete-orphan")
    free_responses = relationship("FreeResponseAnswer", back_populates="submission", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<ExamSubmission(id={self.id}, filename='{self.filename}', status='{self.status}')>"


class CandidateResult(Base):
    """Model for per-candidate extraction results.
    
    Supports dynamic header fields: the fixed columns (candidate_name, etc.)
    are kept for backward compatibility and common UZ1-style sheets.  Any
    additional header fields detected during dynamic format analysis are
    stored in the ``extra_fields`` JSON column.
    """
    __tablename__ = "candidate_results"
    
    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("exam_submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    page_number = Column(Integer, nullable=True)
    candidate_name = Column(String(255), nullable=True)
    candidate_number = Column(String(100), nullable=True, index=True)
    country = Column(String(100), nullable=True)
    paper_type = Column(String(50), nullable=True)
    template_id = Column(String(100), nullable=True, index=True)  # per-page layout (auto or forced)
    detection = Column(JSON, nullable=True)  # {method, raw_text, warning?}
    extra_fields = Column(JSON, nullable=True)  # dynamic header fields beyond the four above
    answers = Column(JSON, nullable=True)  # {"1": "D", "2": "B", "3": "BL", ...}
    drawing_questions = Column(JSON, nullable=True)  # {"31": "student text..."}
    marked_answers = Column(JSON, nullable=True)  # {"1": "P", "2": "B", ...} after marking
    marked_drawing = Column(JSON, nullable=True)  # {"31": "P", "32": "IM"} after marking
    score_correct = Column(Integer, nullable=True)
    score_total = Column(Integer, nullable=True)
    score_percentage = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    submission = relationship("ExamSubmission", back_populates="candidate_results")
    candidate_markings = relationship(
        "CandidateMarking", back_populates="candidate_result", cascade="all, delete-orphan"
    )
    
    def __repr__(self):
        return f"<CandidateResult(id={self.id}, candidate='{self.candidate_number}', page={self.page_number})>"


# ---------------------------- Exams domain -----------------------------


class Exam(Base):
    """Represents an exam with optional correction PDF and student PDFs."""

    __tablename__ = "exams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    correction_pdf_path = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    documents = relationship("ExamDocument", back_populates="exam", cascade="all, delete-orphan")
    generated_jsons = relationship("GeneratedJSON", back_populates="exam", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Exam(id={self.id}, name='{self.name}')>"


class ExamDocument(Base):
    """Student PDF uploads (can be large, per country)."""

    __tablename__ = "exam_documents"

    id = Column(Integer, primary_key=True, index=True)
    exam_id = Column(Integer, ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    country = Column(String(100), nullable=True, index=True)
    file_path = Column(String(500), nullable=False)
    pages_count = Column(Integer, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    exam = relationship("Exam", back_populates="documents")

    def __repr__(self):
        return f"<ExamDocument(id={self.id}, exam={self.exam_id}, country='{self.country}')>"


class GeneratedJSON(Base):
    """Stores generated JSON outputs per exam (download/delete)."""

    __tablename__ = "generated_jsons"

    id = Column(Integer, primary_key=True, index=True)
    exam_id = Column(Integer, ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    file_path = Column(String(500), nullable=False)
    filename = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    exam = relationship("Exam", back_populates="generated_jsons")

    def __repr__(self):
        return f"<GeneratedJSON(id={self.id}, exam={self.exam_id}, file='{self.filename}')>"


class AnswerKey(Base):
    """Model for answer keys used for auto-marking"""
    __tablename__ = "answer_keys"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_answer_keys_template_version"),
        Index("ix_answer_keys_template_active", "template_id", "is_active"),
        Index(
            "uq_answer_keys_one_active_template",
            "template_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active IS TRUE"),
        ),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)  # e.g. "UZ1 Paper A"
    paper_type = Column(String(50), nullable=True, index=True)
    answers = Column(JSON, nullable=False)  # {"1": "D", "2": "B", "3": "A", ...}
    drawing_key = Column(JSON, nullable=True)  # {"31": "circle", "32": "triangle"}
    total_questions = Column(Integer, nullable=True)
    template_id = Column(String(100), nullable=True, index=True)
    version = Column(Integer, nullable=False, default=1)
    source_filename = Column(String(255), nullable=True)
    source_sha256 = Column(String(64), nullable=True)
    total_marks = Column(Integer, nullable=True)
    question_spec = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    marking_runs = relationship("MarkingRun", back_populates="answer_key")

    def __repr__(self):
        return f"<AnswerKey(id={self.id}, name='{self.name}', paper_type='{self.paper_type}')>"


class MarkingRun(Base):
    """Auditable application of one answer-key version to a submission."""

    __tablename__ = "marking_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('unavailable', 'processing', 'completed', 'failed')",
            name="ck_marking_runs_status",
        ),
        Index("ix_marking_runs_submission_created", "submission_id", "created_at"),
        Index(
            "uq_marking_runs_one_processing_submission",
            "submission_id",
            unique=True,
            sqlite_where=text("status = 'processing'"),
            postgresql_where=text("status = 'processing'"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(
        Integer,
        ForeignKey("exam_submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    answer_key_id = Column(
        Integer, ForeignKey("answer_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status = Column(String(20), nullable=False, default="unavailable", index=True)
    key_provenance = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    submission = relationship("ExamSubmission", back_populates="marking_runs")
    answer_key = relationship("AnswerKey", back_populates="marking_runs")
    candidate_markings = relationship(
        "CandidateMarking", back_populates="marking_run", cascade="all, delete-orphan"
    )


class CandidateMarking(Base):
    """Immutable weighted marking output for one extracted candidate."""

    __tablename__ = "candidate_markings"
    __table_args__ = (
        UniqueConstraint(
            "marking_run_id",
            "candidate_result_id",
            name="uq_candidate_markings_run_candidate",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    marking_run_id = Column(
        Integer,
        ForeignKey("marking_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_result_id = Column(
        Integer,
        ForeignKey("candidate_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    awarded_marks = Column(Float, nullable=False)
    max_marks = Column(Float, nullable=False)
    percentage = Column(Float, nullable=False)
    outcomes = Column(JSON, nullable=False)
    answer_key_id = Column(
        Integer, ForeignKey("answer_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    marking_run = relationship("MarkingRun", back_populates="candidate_markings")
    candidate_result = relationship("CandidateResult", back_populates="candidate_markings")
    answer_key = relationship("AnswerKey")


class MultipleChoiceAnswer(Base):
    """Model for multiple choice answers (legacy — kept for backward compat)"""
    __tablename__ = "multiple_choice_answers"
    
    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("exam_submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    question_number = Column(Integer, nullable=False)
    selected_answer = Column(String(1), nullable=False)  # A, B, C, D, or E
    page_number = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    submission = relationship("ExamSubmission", back_populates="mcq_answers")
    
    def __repr__(self):
        return f"<MultipleChoiceAnswer(Q{self.question_number}={self.selected_answer})>"


class FreeResponseAnswer(Base):
    """Model for free response answers (legacy — kept for backward compat)"""
    __tablename__ = "free_response_answers"
    
    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("exam_submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    question_number = Column(Integer, nullable=False)
    response_text = Column(Text, nullable=False)
    word_count = Column(Integer, default=0)
    page_number = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    submission = relationship("ExamSubmission", back_populates="free_responses")
    
    def __repr__(self):
        preview = self.response_text[:50] + "..." if len(self.response_text) > 50 else self.response_text
        return f"<FreeResponseAnswer(Q{self.question_number}: '{preview}')>"


class ProcessingLog(Base):
    """Model for processing logs and audit trail"""
    __tablename__ = "processing_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("exam_submissions.id", ondelete="CASCADE"), nullable=True, index=True)
    action = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    message = Column(Text, nullable=True)
    extra_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    def __repr__(self):
        return f"<ProcessingLog(action='{self.action}', status='{self.status}')>"
