"""
tts.py — Motor de Text-to-Speech con Kokoro-82M (español).

- Carga perezosa del modelo (la primera petición lo inicializa; las siguientes reutilizan).
- Genera audio fragmento a fragmento (usando textprep.chunk) y lo concatena.
- Devuelve WAV a 24 kHz (formato nativo de Kokoro).

VRAM aproximada: < 1 GB. Convive con Qwen en la misma GPU sin problema.

IMPORTANTE sobre la velocidad:
  Aquí 'speed' afecta a la PROSODIA del modelo (cómo de rápido habla la voz).
  El control 0.5x–3.0x de la interfaz NO usa esto: se hace en el reproductor con
  playbackRate, que preserva el tono. Dejamos speed=1.0 por defecto para la mejor
  naturalidad y el usuario ajusta la velocidad al escuchar.
"""

from __future__ import annotations

import io
import logging

import lameenc
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
        audio = self.synthesize(text, voice=voice, speed=speed)
        return _to_mp3_bytes(audio, SAMPLE_RATE)

    def count_fragments(self, text: str) -> int:
        """Cuántos fragmentos generará un texto (para estimaciones en la UI)."""
        return len(prepare(text))


def _to_mp3_bytes(audio: np.ndarray, sample_rate: int, bitrate: int = 128) -> bytes:
    """Convierte float32 [-1,1] a MP3 en memoria usando lameenc (sin dependencias de sistema)."""
    if audio.size == 0:
        audio = np.zeros(1, dtype=np.float32)
    clipped = np.clip(audio, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(2)  # 2 = alta calidad
    mp3_data = encoder.encode(pcm16.tobytes())
    mp3_data += encoder.flush()
    return mp3_data


# Instancia global perezosa (se importa desde server.py).
engine = TTSEngine(lang_code="es")
