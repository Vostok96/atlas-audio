"""
server.py — Servidor FastAPI de Atlas Audio.

Rutas web:
  GET  /                      -> interfaz (reproductor + control 0.5x-3x)

API:
  GET  /api/voices            -> voces disponibles
  POST /api/tts               -> { text, voice, speed } -> audio/wav
  POST /api/tts/estimate      -> { text } -> { fragments, approx_chars }
  POST /api/document          -> archivo (pdf/epub/txt) -> { text }
  POST /api/stt               -> archivo de audio -> { text, segments, ... }

Escucha en 0.0.0.0:8080 para que sea accesible vía Tailscale desde
laptop-denis, s23-ultra y nas-rm.

Lanzar:  python -m app.server
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .core import documents, stt, tts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("atlas.server")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Atlas Audio", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

MAX_TTS_CHARS = 200_000  # ~200 páginas. Suficiente para un libro completo.


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"voices": tts.AVAILABLE_VOICES, "default_voice": tts.DEFAULT_VOICE},
    )


@app.get("/api/voices")
async def get_voices():
    return {"voices": tts.AVAILABLE_VOICES, "default": tts.DEFAULT_VOICE}


@app.post("/api/tts/estimate")
async def tts_estimate(payload: dict):
    text = (payload or {}).get("text", "")
    if not text.strip():
        return {"fragments": 0, "chars": 0}
    return {"fragments": tts.engine.count_fragments(text), "chars": len(text)}


@app.post("/api/tts")
async def synthesize(payload: dict):
    text = (payload or {}).get("text", "").strip()
    voice = (payload or {}).get("voice", tts.DEFAULT_VOICE)
    speed = float((payload or {}).get("speed", 1.0))
    if not text:
        raise HTTPException(status_code=400, detail="Texto vacío.")
    if len(text) > MAX_TTS_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Texto demasiado largo ({len(text)} chars). Máximo {MAX_TTS_CHARS}.",
        )
    logger.info("TTS: %d chars, voz=%s", len(text), voice)
    try:
        wav = tts.engine.synthesize_to_wav_bytes(text, voice=voice, speed=speed)
    except Exception as e:  # noqa: BLE001
        logger.exception("Fallo en TTS")
        raise HTTPException(status_code=500, detail=f"Error en síntesis: {e}") from e
    return Response(content=wav, media_type="audio/wav")


@app.post("/api/document")
async def upload_document(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in documents.SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=f"Formato no soportado. Permitidos: {sorted(documents.SUPPORTED)}",
        )
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=UPLOAD_DIR) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        text = documents.extract_text(tmp_path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Error leyendo documento: {e}") from e
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return JSONResponse({"text": text, "chars": len(text)})


@app.post("/api/stt")
async def transcribe(file: UploadFile = File(...), language: str = Form("es")):
    suffix = Path(file.filename or "audio").suffix.lower() or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=UPLOAD_DIR) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        lang = None if language in ("", "auto") else language
        result = stt.engine.transcribe(tmp_path, language=lang)
    except Exception as e:  # noqa: BLE001
        logger.exception("Fallo en STT")
        raise HTTPException(status_code=500, detail=f"Error en transcripción: {e}") from e
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return JSONResponse(result)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


def main():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")


if __name__ == "__main__":
    main()
