"""
documents.py — Extracción de texto de PDF, EPUB y TXT para alimentar el TTS.

Devuelve texto plano. La normalización (números, troceado) la hace textprep
más adelante en el pipeline de TTS.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("atlas.documents")

SUPPORTED = {".pdf", ".epub", ".txt", ".md"}


def extract_text(path: str | Path) -> str:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _from_pdf(path)
    if ext == ".epub":
        return _from_epub(path)
    if ext in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    raise ValueError(f"Formato no soportado: {ext}. Soportados: {sorted(SUPPORTED)}")


def _from_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts = []
    for i, page in enumerate(reader.pages):
        try:
            parts.append(page.extract_text() or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("PDF página %d ilegible: %s", i, e)
    return "\n\n".join(parts)


def _from_epub(path: Path) -> str:
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup

    book = epub.read_epub(str(path))
    parts = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        text = soup.get_text(separator=" ")
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)
