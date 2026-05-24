"""
textprep.py — Normalización y troceado de texto para TTS (español).

  - Convierte números a palabras (num2words, es).
  - Trocea por oraciones/párrafos respetando el límite de tokens del motor TTS.
  - Expande abreviaturas frecuentes (ampliable).

El troceado produce fragmentos que XTTSv2 sintetiza de forma estable,
los cuales se concatenan en el audio final.
"""

from __future__ import annotations

import re

try:
    from num2words import num2words
except ImportError:  # permitir importar el módulo aunque falte la dependencia
    num2words = None

# Límite conservador de caracteres por fragmento. XTTSv2 soporta ~400 tokens GPT;
# en español ~1 token ≈ 0.6 chars de media, así que ~380-420 chars es seguro.
MAX_CHARS = 380

# Abreviaturas frecuentes en textos académicos / microbiología.
# Ampliable según tu material de estudio.
ABBREVIATIONS = {
    r"\bDr\.": "Doctor",
    r"\bDra\.": "Doctora",
    r"\bSr\.": "Señor",
    r"\bSra\.": "Señora",
    r"\bp\.\s*ej\.": "por ejemplo",
    r"\betc\.": "etcétera",
    r"\bvs\.": "versus",
    r"\bca\.": "circa",
    r"\bfig\.": "figura",
    r"\bspp\.": "especies",
    r"\bsp\.": "especie",
    r"\bcf\.": "compárese",
    r"\bnº": "número",
    r"\bNº": "número",
    r"\bn°": "número",
}

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_MULTISPACE = re.compile(r"[ \t]+")
_MULTINEWLINE = re.compile(r"\n{3,}")


def _expand_abbreviations(text: str) -> str:
    for pattern, repl in ABBREVIATIONS.items():
        text = re.sub(pattern, repl, text)
    return text


def _number_to_words(match: re.Match) -> str:
    raw = match.group(0)
    if num2words is None:
        return raw
    try:
        # Maneja decimales con coma o punto.
        normalized = raw.replace(".", "").replace(",", ".") if "," in raw else raw
        value = float(normalized) if ("." in normalized) else int(normalized)
        return num2words(value, lang="es")
    except (ValueError, OverflowError):
        return raw


def normalize(text: str) -> str:
    """Limpia y normaliza el texto para que XTTSv2 lo pronuncie bien en español."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _expand_abbreviations(text)
    text = _NUMBER.sub(_number_to_words, text)
    # Guiones de corte de línea de PDFs: "micro-\nbiología" -> "microbiología"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = _MULTISPACE.sub(" ", text)
    text = _MULTINEWLINE.sub("\n\n", text)
    return text.strip()


def _split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    """Si una oración excede el límite, la parte por comas y, si hace falta, por palabras."""
    if len(sentence) <= max_chars:
        return [sentence]
    parts, buf = [], ""
    for piece in re.split(r"(?<=,)\s+", sentence):
        if len(buf) + len(piece) + 1 <= max_chars:
            buf = f"{buf} {piece}".strip()
        else:
            if buf:
                parts.append(buf)
            if len(piece) <= max_chars:
                buf = piece
            else:  # último recurso: trocear por palabras
                words, wbuf = piece.split(), ""
                for w in words:
                    if len(wbuf) + len(w) + 1 <= max_chars:
                        wbuf = f"{wbuf} {w}".strip()
                    else:
                        parts.append(wbuf)
                        wbuf = w
                if wbuf:
                    buf = wbuf
                else:
                    buf = ""
    if buf:
        parts.append(buf)
    return parts


def chunk(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """
    Divide el texto normalizado en fragmentos <= max_chars, respetando oraciones.
    Devuelve la lista de fragmentos lista para enviar a XTTSv2 uno por uno.
    """
    chunks: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        buf = ""
        for sentence in _SENTENCE_END.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            for piece in _split_long_sentence(sentence, max_chars):
                if len(buf) + len(piece) + 1 <= max_chars:
                    buf = f"{buf} {piece}".strip()
                else:
                    if buf:
                        chunks.append(buf)
                    buf = piece
        if buf:
            chunks.append(buf)
    return chunks


def prepare(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Atajo: normaliza y trocea en un solo paso."""
    return chunk(normalize(text), max_chars=max_chars)
