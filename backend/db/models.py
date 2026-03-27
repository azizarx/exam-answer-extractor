"""
Database models for exam answers
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, JSON, ForeignKey, Float
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
    status = Column(String(50), default="pending", index=True)  # pending, processing, completed, failed
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    pages_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    
    # Relationships
    candidate_results = relationship("CandidateResult", back_populates="submission", cascade="all, delete-orphan")
    
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
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)  # e.g. "UZ1 Paper A"
    paper_type = Column(String(50), nullable=True, index=True)
    answers = Column(JSON, nullable=False)  # {"1": "D", "2": "B", "3": "A", ...}
    drawing_key = Column(JSON, nullable=True)  # {"31": "circle", "32": "triangle"}
    total_questions = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<AnswerKey(id={self.id}, name='{self.name}', paper_type='{self.paper_type}')>"


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
