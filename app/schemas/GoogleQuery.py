from typing import List

from pydantic import BaseModel, Field


class GoogleQueryCreateSchema(BaseModel):
    """Schema for submitting a Google search query to be processed in background."""

    query: str
    topic: List[str] = Field(default_factory=list, description="Topics associated with this query")

