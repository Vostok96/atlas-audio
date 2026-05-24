"""
translate.py - Traduccion asistida por Ollama para Atlas Audio.

La traduccion se hace fuera del proceso de Kokoro/faster-whisper: este modulo
solo llama al servidor local de Ollama por HTTP. Por defecto usa qwen3:14b,
que es el modelo del Atlas de tesis en la desktop.
"""

from __future__ import annotations

import json
import logging
import os
import re
import textwrap
import urllib.error
import urllib.request
from typing import Callable

logger = logging.getLogger("atlas.translate")

DEFAULT_BASE_URL = (
    os.getenv("ATLAS_OLLAMA_BASE_URL")
    or os.getenv("OLLAMA_BASE_URL")
    or "http://127.0.0.1:11434"
)
DEFAULT_MODEL = os.getenv("ATLAS_TRANSLATION_MODEL") or os.getenv("OLLAMA_MODEL") or "qwen3:14b"
DEFAULT_KEEP_ALIVE = os.getenv("ATLAS_TRANSLATION_KEEP_ALIVE", "2m")
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("ATLAS_TRANSLATION_TIMEOUT_SECONDS", "240"))
DEFAULT_CHUNK_CHARS = int(os.getenv("ATLAS_TRANSLATION_CHUNK_CHARS", "3000"))
UNLOAD_AFTER_TRANSLATION = os.getenv("ATLAS_TRANSLATION_UNLOAD", "1").lower() in {
    "1",
    "true",
    "yes",
    "on",
    "si",
    "sí",
}

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


class TranslationCancelled(Exception):
    """Raised when the queued translation job is cancelled between chunks."""


class TranslationError(RuntimeError):
    """Raised when Ollama cannot translate the supplied text."""


def translate_to_spanish(
    text: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
) -> str:
    chunks = _chunk_text(text)
    if not chunks:
        return ""

    model = model or DEFAULT_MODEL
    base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    translated_parts: list[str] = []

    try:
        for idx, chunk in enumerate(chunks, start=1):
            if cancel_callback and cancel_callback():
                raise TranslationCancelled("Traduccion cancelada por el usuario.")
            translated = _translate_chunk(chunk, model=model, base_url=base_url)
            translated_parts.append(translated)
            if progress_callback:
                progress_callback(idx, len(chunks), translated[:120])
    finally:
        if UNLOAD_AFTER_TRANSLATION:
            unload_model(model=model, base_url=base_url)

    return "\n\n".join(part.strip() for part in translated_parts if part.strip()).strip()


def unload_model(*, model: str | None = None, base_url: str | None = None) -> None:
    """Ask Ollama to evict the translation model from VRAM."""
    model = model or DEFAULT_MODEL
    base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    payload = {"model": model, "messages": [], "keep_alive": 0}
    try:
        _post_json(f"{base_url}/api/chat", payload, timeout=20)
        logger.info("Modelo Ollama descargado de VRAM: %s", model)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo descargar %s de VRAM: %s", model, exc)


def _translate_chunk(chunk: str, *, model: str, base_url: str) -> str:
    prompt = (
        "/no_think\n"
        "Eres un traductor tecnico para estudio universitario de microbiologia.\n"
        "Traduce el siguiente transcript del ingles al espanol natural y claro.\n"
        "Reglas: no resumas, no agregues comentarios, conserva nombres propios, "
        "siglas, unidades, genes, especies, titulos de articulos y terminos tecnicos "
        "cuando convenga. Devuelve solo la traduccion final.\n\n"
        "<transcript>\n"
        f"{chunk.strip()}\n"
        "</transcript>"
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": DEFAULT_KEEP_ALIVE,
        "options": {
            "temperature": 0.1,
            "top_p": 0.9,
            "num_ctx": 8192,
        },
    }
    data = _post_json(f"{base_url}/api/generate", payload, timeout=DEFAULT_TIMEOUT_SECONDS)
    if data.get("error"):
        raise TranslationError(str(data["error"]))
    response = str(data.get("response") or "").strip()
    response = _strip_thinking(response)
    if not response:
        raise TranslationError("Ollama no devolvio texto traducido.")
    return response


def _post_json(url: str, payload: dict, *, timeout: int) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise TranslationError(f"No se pudo conectar con Ollama en {url}: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TranslationError("Ollama devolvio una respuesta no valida.") from exc


def _strip_thinking(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def _chunk_text(text: str, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n")).strip()
    if not text:
        return []

    max_chars = max(800, max_chars)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append(" ".join(current).strip())
                current = []
                current_len = 0
            chunks.extend(textwrap.wrap(sentence, width=max_chars, break_long_words=False))
            continue

        projected = current_len + len(sentence) + (1 if current else 0)
        if current and projected > max_chars:
            chunks.append(" ".join(current).strip())
            current = [sentence]
            current_len = len(sentence)
        else:
            current.append(sentence)
            current_len = projected

    if current:
        chunks.append(" ".join(current).strip())
    return chunks
