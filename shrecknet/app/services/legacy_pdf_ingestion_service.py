"""Temporary compatibility wrapper for the former PyMuPDF ingestion path."""

from __future__ import annotations

from app.services.pdf_embedding_service import PdfEmbeddingService


class LegacyPdfIngestionService(PdfEmbeddingService):
    """Old page-text/character-chunk pipeline retained for rollback only."""

