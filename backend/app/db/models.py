from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class KnowledgeBase(SQLModel, table=True):
    __tablename__ = "knowledge_bases"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=100)
    description: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
