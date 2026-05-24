"""
stt.py — Motor de Speech-to-Text con faster-whisper (large-v3, GPU float16).

- Carga perezosa del modelo.
- Transcribe un archivo de audio y devuelve texto + segmentos con marcas de tiempo.
- Usa CTranslate2 (independiente de los kernels de PyTorch), muy rápido en Blackwell.

VRAM aproximada de large-v3 en float16: ~4-5 GB.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger("atlas.stt")

DEFAULT_MODEL = "large-v3"
ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


class STTEngine:
    def __init__(
        self,
        model_size: str = DEFAULT_MODEL,
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        logger.info("Cargando faster-whisper %s (%s/%s)...",
                    self.model_size, self.device, self.compute_type)
        from faster_whisper import WhisperModel

        try:
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        except Exception as e:  # noqa: BLE001 — fallback a CPU si la GPU falla
            logger.warning("GPU no disponible para STT (%s). Usando CPU int8.", e)
            self._model = WhisperModel(
                self.model_size, device="cpu", compute_type="int8"
            )
        logger.info("faster-whisper cargado.")

    def unload(self) -> None:
        """Descarga faster-whisper de VRAM. Se recarga automáticamente en la siguiente transcripción."""
        if self._model is None:
            return
        del self._model
        self._model = None
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
        logger.info("faster-whisper descargado de VRAM.")

    def transcribe(
        self,
        audio_path: str,
        language: str | None = "es",
        vad_filter: bool = True,
        progress_callback: ProgressCallback | None = None,
        cancel_callback: CancelCallback | None = None,
    ) -> dict:
        """
        Transcribe un archivo de audio. Devuelve:
          { "language": str, "duration": float, "text": str,
            "segments": [ {start, end, text}, ... ] }
        """
        self._ensure_loaded()
        segments_gen, info = self._model.transcribe(
            audio_path,
            language=language,
            vad_filter=vad_filter,
            beam_size=5,
        )

        segments = []
        full_text_parts = []
        for seg in segments_gen:
            if cancel_callback and cancel_callback():
                break
            segments.append(
                {"start": round(seg.start, 2), "end": round(seg.end, 2),
                 "text": seg.text.strip()}
            )
            full_text_parts.append(seg.text.strip())
            if progress_callback:
                progress_callback(len(segments), seg.text.strip())

        return {
            "language": info.language,
            "duration": round(info.duration, 2),
            "text": " ".join(full_text_parts).strip(),
            "segments": segments,
        }


engine = STTEngine()
