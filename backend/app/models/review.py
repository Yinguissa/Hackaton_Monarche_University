from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field


class Review(SQLModel, table=True):
    __tablename__ = "reviews"

    id: Optional[int] = Field(default=None, primary_key=True)
    email_id: str = Field(index=True, foreign_key="emails.email_id")
    reason: Optional[str] = None              # review_reason copied from comparison_results
    human_decision: Optional[str] = None      # approve | correct | retry
    final_result: Optional[str] = None        # JSON-encoded corrected record, when human_decision == correct
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
