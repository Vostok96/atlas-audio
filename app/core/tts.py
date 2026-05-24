"""
tts.py — Motor TTS con Coqui XTTSv2 (multilingüe, español).

Lazy loading (~1.9 GB descarga en el primer arranque).
Fragmenta el texto con textprep, sintetiza fragmento a fragmento y
escribe MP3 128 kbps con lameenc sin cargar el audio completo en RAM.

VRAM: ~2.5 GB cargado · ~4 GB pico síntesis (RTX 5070 Ti: safe).
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import BinaryIO, Callable

import numpy as np

from .textprep import prepare

logger = logging.getLogger("atlas.tts")

SAMPLE_RATE = 24000
LANGUAGE = "es"
DEFAULT_VOICE = "Alma María"
AVAILABLE_VOICES = {
    "Alma María":      "Alma (femenina · ES)",
    "Ana Florence":    "Ana (femenina · ES)",
    "Nova Hogarth":    "Nova (femenina · ES)",
    "Claribel Dervla": "Claribel (femenina · neutra)",
    "Luis Moray":      "Luis (masculino · ES)",
    "Marcos Rudaski":  "Marcos (masculino · ES)",
}

_GAP_SECONDS = 0.15

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


class SynthesisCancelled(Exception):
    """Raised when a synthesis job is cancelled between fragments."""


class TTSEngine:
    """Envoltorio singleton sobre Coqui XTTSv2."""

    def __init__(self) -> None:
        self._tts = None

    def _ensure_loaded(self) -> None:
        if self._tts is not None:
            return
        logger.info("Cargando Coqui XTTSv2 (~1.9 GB, primera vez descarga el modelo)...")
        from TTS.api import TTS  # lazy import: no bloquea el arranque del servidor

        self._tts = TTS(
            model_name="tts_models/multilingual/multi-dataset/xtts_v2",
            gpu=True,
            progress_bar=False,
        )
        logger.info("XTTSv2 listo. Hablantes: %d", len(self._tts.speakers or []))

    def synthesize_to_mp3_bytes(
        self, text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0
    ) -> bytes:
        sink = io.BytesIO()
        self._synthesize_to_mp3_sink(text, sink, voice=voice, speed=speed)
        return sink.getvalue()

    def synthesize_to_mp3_file(
        self,
        text: str,
        output_path: str | Path,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        progress_callback: ProgressCallback | None = None,
        cancel_callback: CancelCallback | None = None,
    ) -> dict:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as sink:
            meta = self._synthesize_to_mp3_sink(
                text,
                sink,
                voice=voice,
                speed=speed,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
        meta["bytes"] = output_path.stat().st_size
        return meta

    def unload(self) -> None:
        if self._tts is None:
            return
        del self._tts
        self._tts = None
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
        logger.info("XTTSv2 descargado de VRAM.")

    def count_fragments(self, text: str) -> int:
        return len(prepare(text))

    def _synthesize_to_mp3_sink(
        self,
        text: str,
        sink: BinaryIO,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        bitrate: int = 128,
        progress_callback: ProgressCallback | None = None,
        cancel_callback: CancelCallback | None = None,
    ) -> dict:
        if voice not in AVAILABLE_VOICES:
            voice = DEFAULT_VOICE
        self._ensure_loaded()

        fragments = prepare(text)
        encoder = _new_mp3_encoder(SAMPLE_RATE, bitrate=bitrate)
        gap = np.zeros(int(SAMPLE_RATE * _GAP_SECONDS), dtype=np.float32)
        total_samples = 0

        if not fragments:
            sink.write(_encode_array(encoder, np.zeros(1, dtype=np.float32)))
            sink.write(encoder.flush())
            return {"fragments": 0, "duration": 0.0, "sample_rate": SAMPLE_RATE}

        total = len(fragments)
        for idx, fragment in enumerate(fragments, start=1):
            if cancel_callback and cancel_callback():
                raise SynthesisCancelled()

            wav = self._tts.tts(
                text=fragment,
                speaker=voice,
                language=LANGUAGE,
                speed=speed,
                split_sentences=False,
            )
            arr = np.array(wav, dtype=np.float32)
            sink.write(_encode_array(encoder, arr))
            sink.write(_encode_array(encoder, gap))
            total_samples += arr.size + gap.size

            if progress_callback:
                progress_callback(idx, total, fragment)
            if idx % 10 == 0:
                logger.info("TTS progreso: %d/%d fragmentos", idx, total)

        sink.write(encoder.flush())
        return {
            "fragments": total,
            "duration": round(total_samples / SAMPLE_RATE, 2),
            "sample_rate": SAMPLE_RATE,
        }


def _pcm16_bytes(audio: np.ndarray) -> bytes:
    if audio.size == 0:
        audio = np.zeros(1, dtype=np.float32)
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


def _new_mp3_encoder(sample_rate: int, bitrate: int = 128):
    import lameenc
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(2)
    return encoder


def _encode_array(encoder, audio: np.ndarray) -> bytes:
    return encoder.encode(_pcm16_bytes(audio))


# Instancia global perezosa (se importa desde server.py).
engine = TTSEngine()
