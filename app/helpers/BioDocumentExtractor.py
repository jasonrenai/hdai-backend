"""
Extract plain text from speaker bio documents (PDF, DOCX) stored in Azure Blob.
"""
import io
import os
from urllib.parse import urlparse

MAX_BIO_DOCUMENT_BYTES = 10 * 1024 * 1024  # 10 MB


def extension_from_url(file_url: str) -> str:
    path = urlparse((file_url or "").strip()).path
    return os.path.splitext(path)[1].lower()


def extract_text_from_bytes(data: bytes, file_url: str) -> str:
    """
    Extract text from document bytes. Supports PDF and DOCX only.
    Raises ValueError for unsupported types, empty extraction, or oversize files.
    """
    if len(data) > MAX_BIO_DOCUMENT_BYTES:
        raise ValueError("Bio document exceeds the 10 MB size limit")

    ext = extension_from_url(file_url)
    if ext == ".pdf":
        return _extract_pdf(data)
    if ext == ".docx":
        return _extract_docx(data)
    if ext == ".doc":
        raise ValueError("Legacy .doc files are not supported; please upload PDF or DOCX")
    raise ValueError(f"Unsupported bio document type: {ext or 'unknown'}")


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts).strip()


def _extract_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = []
    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()
