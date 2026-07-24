from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional
from app.schemas.PyObjectId import PyObjectId
from bson import ObjectId


class ScraperSchema(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=ObjectId, alias="_id")
    name: Optional[str] = None
    sourceName: Optional[str] = Field(None, alias="sourceName")
    url: str
    description: Optional[str] = None
    status: Optional[str] = None
    userId: Optional[str] = Field(None, alias="user_id")
    createdAt: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


class ScraperCreateSchema(BaseModel):
    sourceName: str = Field(..., alias="sourceName")
    url: str
    description: Optional[str] = None

    class Config:
        populate_by_name = True


class ScraperUpdateSchema(BaseModel):
    sourceName: Optional[str] = Field(None, alias="sourceName")
    url: Optional[str] = None
    description: Optional[str] = None

    class Config:
        populate_by_name = True
