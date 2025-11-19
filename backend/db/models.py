"""
Database models for exam answers
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, JSON, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from backend.db.database import Base


class ExamSubmission(Base):
    """Model for exam submission metadata"""
    __tablename__ = "exam_submissions"
    
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False, index=True)
    original_pdf_key = Column(String(500), nullable=False)  # S3/Spaces key
    result_json_key = Column(String(500), nullable=True)  # S3/Spaces key for results
    status = Column(String(50), default="pending", index=True)  # pending, processing, completed, failed
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    pages_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    
    # Relationships
    mcq_answers = relationship("MultipleChoiceAnswer", back_populates="submission", cascade="all, delete-orphan")
    free_responses = relationship("FreeResponseAnswer", back_populates="submission", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<ExamSubmission(id={self.id}, filename='{self.filename}', status='{self.status}')>"


class MultipleChoiceAnswer(Base):
    """Model for multiple choice answers"""
    __tablename__ = "multiple_choice_answers"
    
    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("exam_submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    question_number = Column(Integer, nullable=False)
    selected_answer = Column(String(1), nullable=False)  # A, B, C, D, or E
    page_number = Column(Integer, nullable=True)  # Which page this answer is from
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    submission = relationship("ExamSubmission", back_populates="mcq_answers")
    
    def __repr__(self):
        return f"<MultipleChoiceAnswer(Q{self.question_number}={self.selected_answer})>"


class FreeResponseAnswer(Base):
    """Model for free response answers"""
    __tablename__ = "free_response_answers"
    
    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("exam_submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    question_number = Column(Integer, nullable=False)
    response_text = Column(Text, nullable=False)
    word_count = Column(Integer, default=0)
    page_number = Column(Integer, nullable=True)  # Which page this answer is from
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    submission = relationship("ExamSubmission", back_populates="free_responses")
    
    def __repr__(self):
        preview = self.response_text[:50] + "..." if len(self.response_text) > 50 else self.response_text
        return f"<FreeResponseAnswer(Q{self.question_number}: '{preview}')>"


class ProcessingLog(Base):
    """Model for processing logs and audit trail"""
    __tablename__ = "processing_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("exam_submissions.id", ondelete="CASCADE"), nullable=True, index=True)
    action = Column(String(100), nullable=False)  # upload, extract, validate, save, etc.
    status = Column(String(50), nullable=False)  # success, error, warning
    message = Column(Text, nullable=True)
    extra_data = Column(JSON, nullable=True)  # Additional data (renamed from metadata to avoid SQLAlchemy conflict)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    def __repr__(self):
        return f"<ProcessingLog(action='{self.action}', status='{self.status}')>"
