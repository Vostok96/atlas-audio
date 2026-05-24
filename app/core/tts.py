"""
tts.py — Motor de Text-to-Speech con Kokoro-82M (español).

- Carga perezosa del modelo (la primera petición lo inicializa; las siguientes reutilizan).
- Genera audio fragmento a fragmento (usando textprep.chunk) y lo escribe como MP3 128 kbps.
- Salida: MP3 128 kbps vía lameenc (puro Python, sin ffmpeg).

VRAM aproximada: < 1 GB. Convive con Whisper large-v3 (~4-5 GB) dentro de los 16 GB disponibles.

IMPORTANTE sobre la velocidad:
  Aquí 'speed' afecta a la PROSODIA del modelo (cómo de rápido habla la voz).
  El control 0.5x–3.0x de la interfaz NO usa esto: se hace en el reproductor con
  playbackRate, que preserva el tono. Dejamos speed=1.0 por defecto para la mejor
  naturalidad y el usuario ajusta la velocidad al escuchar.
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
DEFAULT_VOICE = "ef_dora"          # femenina, español, calidad 5/5
AVAILABLE_VOICES = {
    "ef_dora": "Dora (femenina, ES)",
    "em_alex": "Alex (masculina, ES)",
    "em_santa": "Santa (masculina, ES)",
}

# Pausa breve entre fragmentos (silencio) para que la lectura no suene atropellada.
_GAP_SECONDS = 0.18

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


class SynthesisCancelled(Exception):
    """Raised when a queued synthesis job is cancelled between fragments."""


class TTSEngine:
    """Envoltorio singleton-friendly sobre KPipeline de Kokoro."""

    def __init__(self, lang_code: str = "es", device: str | None = None) -> None:
        self.lang_code = lang_code
        self.device = device  # None => Kokoro elige (usa CUDA si está disponible)
        self._pipeline = None

    def _ensure_loaded(self) -> None:
        if self._pipeline is not None:
            return
        logger.info("Cargando Kokoro (lang=%s)...", self.lang_code)
        from kokoro import KPipeline  # import perezoso: arranque del server más rápido

        # Kokoro detecta CUDA automáticamente vía torch. device opcional.
        if self.device:
            self._pipeline = KPipeline(lang_code=self.lang_code, device=self.device)
        else:
            self._pipeline = KPipeline(lang_code=self.lang_code)
        logger.info("Kokoro cargado.")

    def synthesize(
        self,
        text: str,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
    ) -> np.ndarray:
        """
        Sintetiza 'text' completo y devuelve un único array float32 (mono, 24 kHz).
        El texto se normaliza y trocea internamente.
        """
        if voice not in AVAILABLE_VOICES:
            voice = DEFAULT_VOICE
        self._ensure_loaded()

        fragments = prepare(text)
        if not fragments:
            return np.zeros(0, dtype=np.float32)

        gap = np.zeros(int(SAMPLE_RATE * _GAP_SECONDS), dtype=np.float32)
        pieces: list[np.ndarray] = []

        for idx, fragment in enumerate(fragments):
            generator = self._pipeline(fragment, voice=voice, speed=speed)
            for _, _, audio in generator:
                arr = audio if isinstance(audio, np.ndarray) else audio.detach().cpu().numpy()
                pieces.append(arr.astype(np.float32))
            pieces.append(gap)
            if (idx + 1) % 25 == 0:
                logger.info("TTS progreso: %d/%d fragmentos", idx + 1, len(fragments))

        if not pieces:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(pieces)

    def synthesize_to_mp3_bytes(
        self, text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0
    ) -> bytes:
        """Igual que synthesize() pero devuelve bytes MP3 listos para HTTP."""
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
        """
        Sintetiza y escribe MP3 por fragmentos en disco.

        Esta ruta evita crear un array gigante en memoria, que es justo lo que
        queremos para capitulos largos y para cuidar la GPU/RAM durante sesiones
        remotas.
        """
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

    def count_fragments(self, text: str) -> int:
        """Cuántos fragmentos generará un texto (para estimaciones en la UI)."""
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
                raise SynthesisCancelled("Síntesis cancelada por el usuario.")

            generator = self._pipeline(fragment, voice=voice, speed=speed)
            fragment_samples = 0
            for _, _, audio in generator:
                if cancel_callback and cancel_callback():
                    raise SynthesisCancelled("Síntesis cancelada por el usuario.")
                arr = audio if isinstance(audio, np.ndarray) else audio.detach().cpu().numpy()
                arr = arr.astype(np.float32)
                sink.write(_encode_array(encoder, arr))
                fragment_samples += int(arr.size)

            sink.write(_encode_array(encoder, gap))
            total_samples += fragment_samples + int(gap.size)
            if progress_callback:
                progress_callback(idx, total, fragment)
            if idx % 25 == 0:
                logger.info("TTS progreso: %d/%d fragmentos", idx, total)

        sink.write(encoder.flush())
        return {
            "fragments": total,
            "duration": round(total_samples / SAMPLE_RATE, 2),
            "sample_rate": SAMPLE_RATE,
        }


def _pcm16_bytes(audio: np.ndarray) -> bytes:
    """Convierte float32 [-1,1] a PCM16."""
    if audio.size == 0:
        audio = np.zeros(1, dtype=np.float32)
    clipped = np.clip(audio, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    return pcm16.tobytes()


def _new_mp3_encoder(sample_rate: int, bitrate: int = 128):
    import lameenc

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(2)  # 2 = alta calidad
    return encoder


def _encode_array(encoder, audio: np.ndarray) -> bytes:
    return encoder.encode(_pcm16_bytes(audio))


def _to_mp3_bytes(audio: np.ndarray, sample_rate: int, bitrate: int = 128) -> bytes:
    """Convierte float32 [-1,1] a MP3 en memoria usando lameenc."""
    encoder = _new_mp3_encoder(sample_rate, bitrate=bitrate)
    mp3_data = encoder.encode(_pcm16_bytes(audio))
    mp3_data += encoder.flush()
    return mp3_data


# Instancia global perezosa (se importa desde server.py).
engine = TTSEngine(lang_code="es")
