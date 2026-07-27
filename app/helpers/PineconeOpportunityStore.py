"""
Pinecone vector store for opportunities. Class-based, uses LangChain OpenAI embeddings (text-embedding-3-large).
Expects PINECONE_API_KEY and PINECONE_INDEX in environment (.env or .enc).
Index must exist with dimension 3072 (OpenAI text-embedding-3-large).

All opportunity vectors are stored in and queried from the "opportunities" namespace (not default).

Data added to the vector DB (per opportunity):
- id: MongoDB opportunity _id (string)
- values: embedding of text = topics + speaking_format + delivery_mode + target_audiences + metadata.description
  (+ source.google_search_query when source.google_query is True)
- metadata: {"opportunity_id": <Mongo _id>}
"""
import logging
import os
from typing import List, Optional, Tuple

from langchain_openai import OpenAIEmbeddings
from pinecone import Pinecone

logger = logging.getLogger(__name__)

OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"
# text-embedding-3-large default dimension
EMBEDDING_DIMENSION = 3072
# Pinecone namespace for opportunity vectors (all upserts and queries use this namespace)
PINECONE_OPPORTUNITIES_NAMESPACE = "opportunities"


class OpportunityTextBuilder:
    """Builds text for embedding from opportunity or speaker profile (topics, formats, audiences, description)."""

    @staticmethod
    def from_opportunity(opp: dict) -> str:
        """Build text from opportunity: topics, speaking_format, delivery_mode, target_audiences, metadata.description,
        and when found via Google query, source.google_search_query."""
        parts = []
        topics = opp.get("topics") or []
        if isinstance(topics, list):
            parts.append(" ".join(str(t).strip() for t in topics if t))
        ai_predicted_topics = opp.get("aipredictedTopics") or []
        if isinstance(ai_predicted_topics, list):
            parts.append(" ".join(str(t).strip() for t in ai_predicted_topics if t))
        speaking_format = (opp.get("speaking_format") or "").strip()
        if speaking_format:
            parts.append(speaking_format)
        delivery_mode = (opp.get("delivery_mode") or "").strip()
        if delivery_mode:
            parts.append(delivery_mode)
        audiences = opp.get("target_audiences") or []
        if isinstance(audiences, list):
            parts.append(" ".join(str(a).strip() for a in audiences if a))
        meta = opp.get("metadata") or {}
        if isinstance(meta, dict) and meta.get("description"):
            parts.append(str(meta["description"]).strip())
        src = opp.get("source") or {}
        if isinstance(src, dict) and src.get("google_query"):
            gq = (src.get("google_search_query") or "").strip()
            if gq:
                parts.append(gq)
        return " ".join(parts).strip() or ""

    @staticmethod
    def _item_text(item) -> str:
        """One item to string: use 'name' if dict, else str and strip."""
        if isinstance(item, dict):
            return str((item.get("name") or item.get("slug") or "")).strip()
        return str(item).strip() if item is not None else ""

    @staticmethod
    def _to_str(value):  # str or list -> single string (space-joined if list)
        """Normalize value that may be string or list to a single string."""
        if value is None:
            return ""
        if isinstance(value, list):
            return " ".join(OpportunityTextBuilder._item_text(x) for x in value if x is not None).strip()
        return str(value).strip() if value else ""

    @staticmethod
    def from_speaker_profile(profile: dict) -> str:
        """
        Build search text aligned with opportunity embeddings:
        topics, speaking_formats, delivery_mode, target_audiences.
        (talk_description, key_takeaways, testimonial are excluded from vector search.)
        """
        parts = []
        topics = profile.get("topics") or []
        t_str = OpportunityTextBuilder._to_str(topics)
        if t_str:
            parts.append(t_str)
        speaking_formats = profile.get("speaking_formats") or []
        if isinstance(speaking_formats, list):
            parts.append(" ".join(OpportunityTextBuilder._item_text(s) for s in speaking_formats if s))
        delivery_str = OpportunityTextBuilder._to_str(profile.get("delivery_mode"))
        if delivery_str:
            parts.append(delivery_str)
        audiences = profile.get("target_audiences") or []
        a_str = OpportunityTextBuilder._to_str(audiences)
        if a_str:
            parts.append(a_str)
        return " ".join(parts).strip() or ""


class PineconeOpportunityStore:
    """
    Class-based Pinecone store for opportunities. Uses LangChain OpenAI embeddings (text-embedding-3-large).
    PINECONE_API_KEY and PINECONE_INDEX must be set (e.g. in .env or .enc).
    All vectors are stored in and queried from the "opportunities" namespace.

    Data stored per vector in the opportunities namespace:
    - id: MongoDB opportunity _id (string)
    - values: embedding vector from text built from (topics, speaking_format, delivery_mode, target_audiences, metadata.description)
    - metadata: {"opportunity_id": <Mongo _id>}
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        index_name: Optional[str] = None,
        embedding_model: str = OPENAI_EMBEDDING_MODEL,
        namespace: str = PINECONE_OPPORTUNITIES_NAMESPACE,
    ):
        self._api_key = api_key or os.getenv("PINECONE_API_KEY")
        self._index_name = index_name or os.getenv("PINECONE_INDEX")
        self._embedding_model = embedding_model
        self._namespace = namespace
        self._embeddings = None
        self._index = None

    def _get_embeddings(self):
        """Lazy-init LangChain OpenAI embeddings (text-embedding-3-large)."""
        if self._embeddings is None:
            self._embeddings = OpenAIEmbeddings(
                model=self._embedding_model,
                openai_api_key=os.getenv("OPENAI_API_KEY"),
            )
        return self._embeddings

    def _get_index(self):
        """Lazy-init Pinecone index."""
        if self._index is None:
            if not self._api_key or not self._index_name:
                raise ValueError("PINECONE_API_KEY and PINECONE_INDEX must be set")
            pc = Pinecone(api_key=self._api_key)
            self._index = pc.Index(self._index_name)
        return self._index

    def is_configured(self) -> bool:
        """Return True if Pinecone and OpenAI are configured."""
        if not self._api_key or not self._index_name:
            return False
        if not os.getenv("OPENAI_API_KEY"):
            return False
        return True

    def embed_text(self, text: str) -> Optional[List[float]]:
        """Return embedding vector for text using LangChain OpenAI (text-embedding-3-large)."""
        if not text or not str(text).strip():
            return None
        try:
            embeddings = self._get_embeddings()
            return embeddings.embed_query(text.strip()[:8000])
        except Exception as e:
            logger.warning("OpenAI embedding failed: %s", e)
            return None

    def upsert_opportunity(self, opportunity_id: str, opp: dict) -> bool:
        """
        Build text from opportunity, embed, upsert to Pinecone.
        Vector id is opportunity_id (Mongo _id). Metadata stores opportunity_id for retrieval.
        """
        if not self.is_configured():
            logger.debug("Pinecone not configured: PINECONE_API_KEY or PINECONE_INDEX missing")
            return False
        text = OpportunityTextBuilder.from_opportunity(opp)
        if not text:
            logger.debug("Opportunity %s has no text for embedding", opportunity_id)
            return False
        vector = self.embed_text(text)
        if not vector:
            return False
        try:
            index = self._get_index()
            index.upsert(
                vectors=[{
                    "id": opportunity_id,
                    "values": vector,
                    "metadata": {"opportunity_id": opportunity_id},
                }],
                namespace=self._namespace,
            )
            logger.debug("Pinecone upserted opportunity_id=%s namespace=%s", opportunity_id, self._namespace)
            return True
        except Exception as e:
            logger.warning("Pinecone upsert failed for %s: %s", opportunity_id, e)
            return False

    def query_similar_opportunity_ids(
        self,
        query_text: str,
        top_k: int = 10,
        min_score: Optional[float] = None,
    ) -> Tuple[List[str], List[float]]:
        """
        Embed query_text, query Pinecone, return list of opportunity_id (Mongo _id) and scores in order of similarity.
        If min_score is set, only includes matches with score >= min_score (Pinecone score typically 0-1 for cosine).
        """
        if not self.is_configured():
            return [], []
        vector = self.embed_text(query_text)
        if not vector:
            return [], []
        try:
            index = self._get_index()
            result = index.query(
                vector=vector,
                top_k=top_k,
                include_metadata=True,
                namespace=self._namespace,
            )
            ids: List[str] = []
            scores: List[float] = []
            for match in (result.matches or []):
                score = getattr(match, "score", None)
                if min_score is not None and (score is None or score < min_score):
                    continue
                oid = (match.metadata or {}).get("opportunity_id") or getattr(match, "id", None)
                if oid:
                    ids.append(str(oid))
                    scores.append(float(score) if score is not None else 0.0)
            return ids, scores
        except Exception as e:
            logger.warning("Pinecone query failed: %s", e)
            return [], []
