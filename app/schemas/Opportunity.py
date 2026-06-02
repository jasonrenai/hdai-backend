from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# JSON body field "type" for POST /opportunities/email-content/generate (string slugs only).
EmailAuthorityType = Literal[
    "association_membership",
    "experience_expertise",
    "case_study_results",
]


class OpportunitySourceSchema(BaseModel):
    """Source of the opportunity: Google query search or direct URL scraping."""

    google_query: bool = False  # True if found via Google query search, False if from direct URL scrape
    source_url: str = ""  # URL that was scraped (search result URL or the single URL scraped)
    google_search_query: str = ""  # When google_query is True, the SERP query text (also used for vector search text)


class OpportunitySchema(BaseModel):
    """Schema for extracted speaking opportunities from LLM."""

    link: str = ""
    event_name: str = ""
    location: str = ""
    topics: List[str] = Field(default_factory=list, alias="topics")
    start_date: Optional[str] = None  # Event start date (ISO format YYYY-MM-DD); future only
    end_date: Optional[str] = None   # Event end date (ISO format); for one-day events same as start_date
    speaking_format: str = "Not available"  # Workshop, Panel discussion, etc.
    delivery_mode: str = ""  # Virtual or in person
    target_audiences: List[str] = Field(default_factory=list)  # General Audience, managers, etc.
    source: Optional[OpportunitySourceSchema] = None  # How this opportunity was found (google query vs URL)
    isQualified: Optional[bool] = None  # False: kept in Mongo but not embedded in vector DB
    reasonForUnqualify: Optional[str] = None  # Human-readable when isQualified is False
    metadata: dict = Field(default_factory=dict)

    class Config:
        populate_by_name = True


class UrlScrapeCreateSchema(BaseModel):
    """Schema for creating a URL scrape job."""

    url: str
    topics: Optional[List[str]] = None  # Optional; allowed values from speaker_profile_chatbot.TOPICS


class GenerateOpportunityEmailContentSchema(BaseModel):
    """Body for POST /email-content/generate. Use JSON key \"type\" (string slug)."""

    model_config = ConfigDict(populate_by_name=True)

    speaker_profile_id: str
    opportunity_id: str
    user_suggestion_prompt: Optional[str] = None
    authority_type: EmailAuthorityType = Field(
        "experience_expertise",
        alias="type",
        description=(
            "Authority framing (string): association_membership = Association/Membership "
            "('I belong to your world'); experience_expertise = Experience/Expertise "
            "('I've done this at scale'); case_study_results = Case Study/Results "
            "('Here's proof it works')."
        ),
    )


class OpportunityActivityUpdateSchema(BaseModel):
    """Partial update for opportunity activity (wishlist / applied / accepted / expired / archived / outcomes)."""

    opportunityId: str
    speaker_id: str
    isWishlist: Optional[bool] = None
    isApplied: Optional[bool] = None
    isAccepted: Optional[bool] = None
    isExpired: Optional[bool] = None
    isArchived: Optional[bool] = None
    outcomes: Optional[str] = None  # User notes; omit to leave unchanged, send null to clear
