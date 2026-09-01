from typing import List

from pydantic import BaseModel, Field


class GoogleQueryCreateSchema(BaseModel):
    """Schema for submitting a Google search query to be processed in background."""

    query: str
    relatedTopics: List[str] = Field(
        default_factory=list,
        description="Related topics for this query (catalog or custom strings)",
    )

