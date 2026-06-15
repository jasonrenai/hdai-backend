"""
Background processing: download bio document from Azure, extract text, summarize with OpenAI,
and persist bio_document_summary on the speaker profile and bio_document_summaries collection.
"""
import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Optional

from fastapi import BackgroundTasks
from openai import OpenAI

from app.helpers.AzureStorage import AzureBlobUploader
from app.helpers.BioDocumentExtractor import extract_text_from_bytes
from app.models.BioDocumentSummary import BioDocumentSummaryModel
from app.models.SpeakerProfile import SpeakerProfileModel

logger = logging.getLogger(__name__)

MAX_LLM_INPUT_CHARS = 12000

SYSTEM_PROMPT = """You are an expert at summarizing professional speaker biographies.
Given text extracted from a speaker's bio document, write a concise factual summary suitable for event matching and outreach.
Include expertise areas, credentials, speaking experience, notable achievements, and audience fit when present.
Do not invent facts not supported by the source text.
Write 150-300 words in clear professional prose."""


class BioDocumentSummaryService:
    def __init__(
        self,
        speaker_profile_model: Optional[SpeakerProfileModel] = None,
        bio_document_summary_model: Optional[BioDocumentSummaryModel] = None,
    ):
        self.model = speaker_profile_model or SpeakerProfileModel()
        self.summary_model = bio_document_summary_model or BioDocumentSummaryModel()
        self.azure = AzureBlobUploader()

    async def _sync_state(
        self,
        profile_id: str,
        bio_document_url: Optional[str],
        *,
        status: Any = ...,
        summary: Any = ...,
        error: Any = ...,
        summarized_at: Any = ...,
    ) -> None:
        """
        Write full processing state to bio_document_summaries.
        Update bio_document_summary on speaker profile only when summary is provided.
        """
        url = (bio_document_url or "").strip() or None
        await self.summary_model.upsert_for_profile(
            profile_id,
            bio_document_url=url if url is not None else ...,
            status=status,
            summary=summary,
            error=error,
            summarized_at=summarized_at,
        )
        if summary is not ...:
            await self.model.set_bio_document_summary(profile_id, summary)

    async def handle_bio_document_url_change(
        self,
        profile_id: str,
        old_url: Optional[str],
        new_url: Optional[str],
        background_tasks: BackgroundTasks,
    ) -> None:
        """
        Clear enrichment when URL is removed; enqueue summarization when URL is new/changed.
        """
        old_norm = (old_url or "").strip()
        new_norm = (new_url or "").strip()

        if not new_norm:
            if old_norm:
                await self.model.clear_bio_document_summary(profile_id)
                await self.summary_model.clear_for_profile(profile_id)
            return

        if new_norm == old_norm:
            return

        await self._sync_state(
            profile_id,
            new_norm,
            status="pending",
            summary=None,
            error=None,
            summarized_at=None,
        )
        background_tasks.add_task(self.run_summarize, profile_id, new_norm)

    async def run_summarize(self, profile_id: str, bio_document_url: str) -> None:
        """Background task entry point."""
        url = (bio_document_url or "").strip()
        if not url:
            return

        await self._sync_state(
            profile_id,
            url,
            status="processing",
            error=None,
        )

        try:
            summary = await asyncio.to_thread(self._sync_summarize, url)
            await self._sync_state(
                profile_id,
                url,
                status="completed",
                summary=summary,
                error=None,
                summarized_at=datetime.utcnow(),
            )
            logger.info("Bio document summary completed profile_id=%s", profile_id)
        except Exception as e:
            logger.exception("Bio document summary failed profile_id=%s", profile_id)
            await self._sync_state(
                profile_id,
                url,
                status="failed",
                summary=None,
                error=str(e),
                summarized_at=None,
            )

    def _sync_summarize(self, bio_document_url: str) -> str:
        data = self.azure.download_blob_from_url(bio_document_url)
        text = extract_text_from_bytes(data, bio_document_url)
        if not text.strip():
            raise ValueError("No readable text found in bio document")
        return self._summarize_with_llm(text)

    def _summarize_with_llm(self, text: str) -> str:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured")

        content = text.strip()
        if len(content) > MAX_LLM_INPUT_CHARS:
            content = content[:MAX_LLM_INPUT_CHARS]

        client = OpenAI(api_key=api_key)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Bio document text:\n\n{content}"},
            ],
            temperature=0.2,
        )
        summary = (response.choices[0].message.content or "").strip()
        if not summary:
            raise ValueError("OpenAI returned an empty summary")
        return summary
