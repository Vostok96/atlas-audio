"""
Servidor FastAPI de Atlas Audio.

Diseño operativo:
  - Login personal con cookie firmada. No hay gestión multiusuario.
  - Una sola inferencia GPU a la vez para cuidar VRAM/estabilidad.
  - TTS/STT corren como trabajos con progreso, cancelación y descarga.
  - Subidas con streaming a disco y límites duros.

Lanzar:  python -m app.server
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .core import documents, stt, translate, tts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("atlas.server")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
JOBS_FILE = DATA_DIR / "jobs.json"


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Valor inválido para %s=%r. Usando %s.", name, raw, default)
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


# Límites conservadores para uso personal remoto.
MAX_TTS_CHARS = _env_int("ATLAS_MAX_TTS_CHARS", 60_000, minimum=1_000)
MAX_DOCUMENT_UPLOAD_BYTES = _env_int("ATLAS_MAX_DOCUMENT_MB", 50) * 1024 * 1024
MAX_AUDIO_UPLOAD_BYTES = _env_int("ATLAS_MAX_AUDIO_MB", 200) * 1024 * 1024
MAX_DOCUMENT_TEXT_CHARS = _env_int("ATLAS_MAX_DOCUMENT_TEXT_CHARS", 240_000, minimum=5_000)
MAX_TRANSLATION_TEXT_CHARS = _env_int("ATLAS_MAX_TRANSLATION_CHARS", 60_000, minimum=5_000)
MAX_ACTIVE_JOBS = _env_int("ATLAS_MAX_ACTIVE_JOBS", 3, minimum=1)
JOB_HISTORY_LIMIT = _env_int("ATLAS_JOB_HISTORY_LIMIT", 30, minimum=5)
SESSION_TTL_SECONDS = _env_int("ATLAS_SESSION_DAYS", 30, minimum=1) * 24 * 60 * 60
AUDIO_SUPPORTED = {
    ".aac",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
    ".wma",
}

# Mantener en 1 salvo que se quiera experimentar explícitamente.
GPU_CONCURRENCY = 1

# Descarga los modelos de VRAM tras cada trabajo.
# Recomendado para uso personal no continuo: la GPU queda libre entre sesiones.
# La primera petición de cada sesión tardará ~10-30 s extra en recargar el modelo.
UNLOAD_MODELS_AFTER_JOB = _env_bool("ATLAS_UNLOAD_MODELS", True)

AUTH_USERNAME = os.getenv("ATLAS_AUDIO_USERNAME", "denis")
AUTH_PASSWORD = os.getenv("ATLAS_AUDIO_PASSWORD", "")
SESSION_COOKIE = "atlas_audio_session"
SESSION_SECRET = os.getenv("ATLAS_AUDIO_SECRET") or secrets.token_urlsafe(48)
if not os.getenv("ATLAS_AUDIO_SECRET"):
    logger.warning(
        "ATLAS_AUDIO_SECRET no está definido. Las sesiones se invalidarán al reiniciar."
    )
if not AUTH_PASSWORD:
    logger.warning(
        "ATLAS_AUDIO_PASSWORD no está definido. La interfaz mostrará la pantalla de setup."
    )


@dataclass
class Job:
    id: str
    kind: str
    title: str
    payload: dict[str, Any] = field(default_factory=dict, repr=False)
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    progress: float = 0.0
    current: int = 0
    total: int = 0
    message: str = "En cola"
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    cancel_requested: bool = False


JOBS: dict[str, Job] = {}
JOB_ORDER: list[str] = []
JOB_QUEUE: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_ACTIVE_JOBS)
GPU_LOCK = asyncio.Lock()
WORKER_TASKS: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(_: FastAPI):
    _load_jobs()
    for worker_id in range(GPU_CONCURRENCY):
        WORKER_TASKS.append(asyncio.create_task(_job_worker(worker_id)))
    yield
    for task in WORKER_TASKS:
        task.cancel()
    await asyncio.gather(*WORKER_TASKS, return_exceptions=True)


app = FastAPI(title="Atlas Audio", version="1.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _now() -> float:
    return time.time()


def _touch(job: Job, message: str | None = None) -> None:
    job.updated_at = _now()
    if message is not None:
        job.message = message


def _active_job_count() -> int:
    return sum(1 for job in JOBS.values() if job.status in {"queued", "running"})


def _public_job(job: Job) -> dict[str, Any]:
    public_result = dict(job.result)
    public_result.pop("path", None)
    if public_result.pop("text", None):
        public_result["has_text"] = True
    if public_result.pop("transcript_text", None):
        public_result["has_transcript"] = True
    if public_result.pop("translated_text", None):
        public_result["has_translation"] = True
    segments = public_result.pop("segments", None)
    if isinstance(segments, list):
        public_result["segments_count"] = len(segments)
    return {
        "id": job.id,
        "kind": job.kind,
        "title": job.title,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "progress": job.progress,
        "current": job.current,
        "total": job.total,
        "message": job.message,
        "result": public_result,
        "error": job.error,
        "can_cancel": job.status in {"queued", "running"},
    }


def _job_to_record(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "title": job.title,
        "payload": {} if job.status not in {"queued", "running"} else job.payload,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "progress": job.progress,
        "current": job.current,
        "total": job.total,
        "message": job.message,
        "result": job.result,
        "error": job.error,
    }


def _persist_jobs() -> None:
    records = [
        _job_to_record(JOBS[job_id])
        for job_id in JOB_ORDER
        if job_id in JOBS and JOBS[job_id].status not in {"queued", "running"}
    ]
    tmp_path = JOBS_FILE.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(JOBS_FILE)


def _load_jobs() -> None:
    if JOBS:
        return
    if not JOBS_FILE.exists():
        return
    try:
        records = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo leer %s: %s", JOBS_FILE, exc)
        return
    if not isinstance(records, list):
        return

    loaded = 0
    for record in records[-JOB_HISTORY_LIMIT:]:
        if not isinstance(record, dict):
            continue
        status = record.get("status")
        if status in {"queued", "running"}:
            continue
        result = record.get("result") if isinstance(record.get("result"), dict) else {}
        if status == "done" and record.get("kind") in {"tts", "translate_audio"}:
            output_path = result.get("path")
            if not output_path or not Path(output_path).exists():
                continue
        job_id = str(record.get("id") or uuid.uuid4().hex[:12])
        job = Job(
            id=job_id,
            kind=str(record.get("kind") or "unknown"),
            title=str(record.get("title") or "Trabajo"),
            payload=record.get("payload") if isinstance(record.get("payload"), dict) else {},
            status=str(status or "failed"),
            created_at=float(record.get("created_at") or _now()),
            updated_at=float(record.get("updated_at") or _now()),
            progress=float(record.get("progress") or 0),
            current=int(record.get("current") or 0),
            total=int(record.get("total") or 0),
            message=str(record.get("message") or ""),
            result=result,
            error=record.get("error"),
        )
        JOBS[job.id] = job
        JOB_ORDER.append(job.id)
        loaded += 1
    if loaded:
        logger.info("Biblioteca cargada: %d trabajos.", loaded)


def _unlink_data_path(value: str | None) -> None:
    if not value:
        return
    try:
        path = Path(value).resolve()
        data_dir = DATA_DIR.resolve()
        if path != data_dir and data_dir in path.parents:
            path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo borrar %s: %s", value, exc)


def _delete_job_artifacts(job: Job) -> None:
    _unlink_data_path(job.result.get("path"))
    _unlink_data_path(job.payload.get("path"))


def _get_job_or_404(job_id: str) -> Job:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado.")
    return job


def _trim_jobs() -> None:
    while len(JOB_ORDER) > JOB_HISTORY_LIMIT:
        removable = next(
            (
                job_id
                for job_id in JOB_ORDER
                if JOBS.get(job_id) and JOBS[job_id].status not in {"queued", "running"}
            ),
            None,
        )
        if removable is None:
            return
        job = JOBS.get(removable)
        if job:
            _delete_job_artifacts(job)
        JOB_ORDER.remove(removable)
        JOBS.pop(removable, None)
        _persist_jobs()


def _enqueue_job(kind: str, title: str, payload: dict[str, Any], total: int = 0) -> Job:
    if _active_job_count() >= MAX_ACTIVE_JOBS:
        raise HTTPException(
            status_code=429,
            detail=f"Ya hay {MAX_ACTIVE_JOBS} trabajos activos. Espera o cancela uno.",
        )
    job = Job(
        id=uuid.uuid4().hex[:12],
        kind=kind,
        title=title,
        payload=payload,
        total=total,
    )
    JOBS[job.id] = job
    JOB_ORDER.append(job.id)
    _trim_jobs()
    try:
        JOB_QUEUE.put_nowait(job.id)
    except asyncio.QueueFull as exc:
        JOBS.pop(job.id, None)
        JOB_ORDER.remove(job.id)
        raise HTTPException(status_code=429, detail="La cola está llena.") from exc
    return job


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _session_signature(payload: str) -> str:
    return _b64(hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).digest())


def _make_session_token() -> str:
    payload = f"{AUTH_USERNAME}:{int(_now())}:{secrets.token_urlsafe(18)}"
    payload_b64 = _b64(payload.encode())
    return f"{payload_b64}.{_session_signature(payload_b64)}"


def _valid_session(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    payload_b64, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_session_signature(payload_b64), sig):
        return False
    try:
        payload = _unb64(payload_b64).decode()
        username, iat_raw, _nonce = payload.split(":", 2)
        iat = int(iat_raw)
    except (ValueError, UnicodeDecodeError):
        return False
    return username == AUTH_USERNAME and (_now() - iat) <= SESSION_TTL_SECONDS


def _safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _cookie_secure(request: Request) -> bool:
    if os.getenv("ATLAS_AUDIO_COOKIE_SECURE") is not None:
        return _env_bool("ATLAS_AUDIO_COOKIE_SECURE")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    return proto == "https"


def _is_public_path(path: str) -> bool:
    return path in {"/login", "/logout", "/api/health", "/favicon.ico"} or path.startswith(
        "/static/"
    )


def _is_authenticated(request: Request) -> bool:
    return bool(AUTH_PASSWORD) and _valid_session(request.cookies.get(SESSION_COOKIE))


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if _is_public_path(path) or _is_authenticated(request):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "No autenticado."}, status_code=401)
    return RedirectResponse(f"/login?next={quote(path)}", status_code=303)


def _render_login(request: Request, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": error,
            "next": _safe_next(request.query_params.get("next")),
            "username": AUTH_USERNAME,
            "password_configured": bool(AUTH_PASSWORD),
        },
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _is_authenticated(request):
        return RedirectResponse(_safe_next(request.query_params.get("next")), status_code=303)
    return _render_login(request)


@app.post("/login")
async def login_submit(request: Request):
    try:
        body = (await request.body()).decode("utf-8", errors="replace")
        form = parse_qs(body, keep_blank_values=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("No se pudo leer el formulario de login.")
        return _render_login(request, f"No se pudo leer el formulario: {exc}")

    username = (form.get("username") or [""])[0]
    password = (form.get("password") or [""])[0]
    next_path = (form.get("next") or ["/"])[0]
    if not AUTH_PASSWORD:
        return _render_login(
            request,
            "Define ATLAS_AUDIO_PASSWORD en la desktop antes de exponer el servicio.",
        )
    valid_user = hmac.compare_digest(username.strip().encode("utf-8"), AUTH_USERNAME.encode("utf-8"))
    valid_password = hmac.compare_digest(password.encode("utf-8"), AUTH_PASSWORD.encode("utf-8"))
    if not (valid_user and valid_password):
        return _render_login(request, "Usuario o contraseña incorrectos.")

    response = RedirectResponse(_safe_next(next_path), status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        _make_session_token(),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
    )
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "voices": tts.AVAILABLE_VOICES,
            "default_voice": tts.DEFAULT_VOICE,
            "max_tts_chars": MAX_TTS_CHARS,
            "max_document_mb": MAX_DOCUMENT_UPLOAD_BYTES // 1024 // 1024,
            "max_audio_mb": MAX_AUDIO_UPLOAD_BYTES // 1024 // 1024,
            "max_translation_chars": MAX_TRANSLATION_TEXT_CHARS,
            "translation_model": translate.DEFAULT_MODEL,
            "username": AUTH_USERNAME,
        },
    )


@app.get("/api/health")
async def health():
    return {"status": "ok", "auth": "enabled" if AUTH_PASSWORD else "setup_required"}


@app.get("/api/limits")
async def limits():
    return {
        "max_tts_chars": MAX_TTS_CHARS,
        "max_document_mb": MAX_DOCUMENT_UPLOAD_BYTES // 1024 // 1024,
        "max_audio_mb": MAX_AUDIO_UPLOAD_BYTES // 1024 // 1024,
        "max_translation_chars": MAX_TRANSLATION_TEXT_CHARS,
        "max_active_jobs": MAX_ACTIVE_JOBS,
        "gpu_concurrency": GPU_CONCURRENCY,
        "translation_model": translate.DEFAULT_MODEL,
        "translation_unloads_model": translate.UNLOAD_AFTER_TRANSLATION,
    }


@app.get("/api/voices")
async def get_voices():
    return {"voices": tts.AVAILABLE_VOICES, "default": tts.DEFAULT_VOICE}


@app.post("/api/tts/estimate")
async def tts_estimate(payload: dict):
    text = (payload or {}).get("text", "")
    if not text.strip():
        return {"fragments": 0, "chars": 0, "max_chars": MAX_TTS_CHARS}
    return {
        "fragments": tts.engine.count_fragments(text),
        "chars": len(text),
        "max_chars": MAX_TTS_CHARS,
    }


@app.post("/api/tts/jobs")
async def create_tts_job(payload: dict):
    text = (payload or {}).get("text", "").strip()
    voice = (payload or {}).get("voice", tts.DEFAULT_VOICE)
    title = ((payload or {}).get("title") or _title_from_text(text)).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Texto vacío.")
    if len(text) > MAX_TTS_CHARS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Texto demasiado largo ({len(text)} caracteres). "
                f"Máximo seguro por trabajo: {MAX_TTS_CHARS}."
            ),
        )
    fragments = tts.engine.count_fragments(text)
    job = _enqueue_job(
        "tts",
        title=title[:90] or "Lectura generada",
        payload={"text": text, "voice": voice, "speed": 1.0},
        total=fragments,
    )
    return JSONResponse(_public_job(job), status_code=202)


@app.post("/api/tts")
async def synthesize(payload: dict):
    """
    Endpoint síncrono legado. La UI nueva usa /api/tts/jobs.
    Se conserva limitado y serializado para no abrir una segunda vía de saturación.
    """
    text = (payload or {}).get("text", "").strip()
    voice = (payload or {}).get("voice", tts.DEFAULT_VOICE)
    if not text:
        raise HTTPException(status_code=400, detail="Texto vacío.")
    if len(text) > MAX_TTS_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Texto demasiado largo. Máximo seguro: {MAX_TTS_CHARS} caracteres.",
        )
    try:
        async with GPU_LOCK:
            mp3 = await asyncio.to_thread(
                tts.engine.synthesize_to_mp3_bytes, text, voice=voice, speed=1.0
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("Fallo en TTS")
        raise HTTPException(status_code=500, detail=f"Error en síntesis: {e}") from e
    return Response(content=mp3, media_type="audio/mpeg")


@app.post("/api/document")
async def upload_document(file: UploadFile = File(...)):
    tmp_path, _size = await _save_upload(
        file,
        allowed=documents.SUPPORTED,
        max_bytes=MAX_DOCUMENT_UPLOAD_BYTES,
    )
    try:
        text = documents.extract_text(tmp_path)
        if len(text) > MAX_DOCUMENT_TEXT_CHARS:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"El documento extraído tiene {len(text)} caracteres. "
                    f"Máximo de extracción: {MAX_DOCUMENT_TEXT_CHARS}."
                ),
            )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Error leyendo documento: {e}") from e
    finally:
        tmp_path.unlink(missing_ok=True)
    return JSONResponse(
        {
            "text": text,
            "chars": len(text),
            "max_tts_chars": MAX_TTS_CHARS,
            "warning": (
                f"Divide el texto antes de generar audio: límite seguro {MAX_TTS_CHARS}."
                if len(text) > MAX_TTS_CHARS
                else None
            ),
        }
    )


@app.post("/api/stt/jobs")
async def create_stt_job(file: UploadFile = File(...), language: str = Form("es")):
    tmp_path, _size = await _save_upload(
        file,
        allowed=AUDIO_SUPPORTED,
        max_bytes=MAX_AUDIO_UPLOAD_BYTES,
        fallback_suffix=".wav",
    )
    title = Path(file.filename or "Audio").stem[:90] or "Transcripción"
    try:
        job = _enqueue_job(
            "stt",
            title=title,
            payload={"path": str(tmp_path), "language": language},
        )
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise
    return JSONResponse(_public_job(job), status_code=202)


@app.post("/api/translate-audio/jobs")
async def create_translate_audio_job(
    file: UploadFile = File(...),
    source_language: str = Form("en"),
    voice: str = Form(tts.DEFAULT_VOICE),
):
    tmp_path, _size = await _save_upload(
        file,
        allowed=AUDIO_SUPPORTED,
        max_bytes=MAX_AUDIO_UPLOAD_BYTES,
        fallback_suffix=".wav",
    )
    title = Path(file.filename or "Audio").stem[:90] or "Audio traducido"
    source_language = (source_language or "en").strip().lower()
    if source_language not in {"en", "auto"}:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Por ahora se acepta ingles o auto.")
    try:
        job = _enqueue_job(
            "translate_audio",
            title=title,
            payload={"path": str(tmp_path), "source_language": source_language, "voice": voice},
        )
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise
    return JSONResponse(_public_job(job), status_code=202)


@app.post("/api/stt")
async def transcribe(file: UploadFile = File(...), language: str = Form("es")):
    """Endpoint síncrono legado. La UI nueva usa /api/stt/jobs."""
    tmp_path, _size = await _save_upload(
        file,
        allowed=AUDIO_SUPPORTED,
        max_bytes=MAX_AUDIO_UPLOAD_BYTES,
        fallback_suffix=".wav",
    )
    try:
        lang = None if language in ("", "auto") else language
        async with GPU_LOCK:
            result = await asyncio.to_thread(stt.engine.transcribe, str(tmp_path), language=lang)
    except Exception as e:  # noqa: BLE001
        logger.exception("Fallo en STT")
        raise HTTPException(status_code=500, detail=f"Error en transcripción: {e}") from e
    finally:
        tmp_path.unlink(missing_ok=True)
    return JSONResponse(result)


@app.get("/api/jobs")
async def list_jobs():
    jobs = [_public_job(JOBS[job_id]) for job_id in reversed(JOB_ORDER) if job_id in JOBS]
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    return _public_job(_get_job_or_404(job_id))


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    job = _get_job_or_404(job_id)
    if job.status not in {"queued", "running"}:
        return _public_job(job)
    job.cancel_requested = True
    if job.status == "queued":
        job.status = "cancelled"
        job.progress = 0
        _touch(job, "Cancelado antes de iniciar.")
        _persist_jobs()
    else:
        _touch(job, "Cancelación solicitada; se detendrá al terminar el fragmento actual.")
    return _public_job(job)


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    job = _get_job_or_404(job_id)
    if job.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Cancela el trabajo antes de borrarlo.")
    _delete_job_artifacts(job)
    JOBS.pop(job.id, None)
    if job.id in JOB_ORDER:
        JOB_ORDER.remove(job.id)
    _persist_jobs()
    return {"status": "deleted", "id": job.id}


@app.get("/api/jobs/{job_id}/audio")
async def job_audio(job_id: str):
    job = _get_job_or_404(job_id)
    if job.kind not in {"tts", "translate_audio"} or job.status != "done":
        raise HTTPException(status_code=404, detail="Audio no disponible.")
    output_path = Path(job.result.get("path", ""))
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Archivo de audio no encontrado.")
    return FileResponse(
        output_path,
        media_type="audio/mpeg",
        filename=job.result.get("filename", f"atlas-audio-{job.id}.mp3"),
    )


@app.get("/api/jobs/{job_id}/transcript")
async def job_transcript(job_id: str):
    job = _get_job_or_404(job_id)
    if job.kind == "translate_audio" and job.status == "done":
        return JSONResponse(
            {
                "language": job.result.get("source_language") or "en",
                "duration": job.result.get("source_duration", 0),
                "text": job.result.get("translated_text", ""),
                "source_text": job.result.get("transcript_text", ""),
                "segments": job.result.get("segments", []),
                "translation_model": job.result.get("translation_model"),
            }
        )
    if job.kind != "stt" or job.status != "done":
        raise HTTPException(status_code=404, detail="Transcripción no disponible.")
    return JSONResponse(job.result)


async def _save_upload(
    file: UploadFile,
    allowed: set[str] | None,
    max_bytes: int,
    fallback_suffix: str = ".bin",
) -> tuple[Path, int]:
    suffix = Path(file.filename or "").suffix.lower() or fallback_suffix
    if allowed is not None and suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Formato no soportado. Permitidos: {sorted(allowed)}",
        )

    total = 0
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=UPLOAD_DIR) as tmp:
            tmp_path = Path(tmp.name)
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Archivo demasiado grande. Máximo: {max_bytes // 1024 // 1024} MB.",
                    )
                tmp.write(chunk)
    except Exception:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        raise
    if tmp_path is None:
        raise HTTPException(status_code=400, detail="Archivo vacío.")
    return tmp_path, total


async def _job_worker(worker_id: int) -> None:
    logger.info("Worker GPU %d iniciado con concurrencia segura.", worker_id)
    while True:
        job_id = await JOB_QUEUE.get()
        job = JOBS.get(job_id)
        try:
            if not job:
                continue
            if job.cancel_requested or job.status == "cancelled":
                job.status = "cancelled"
                _touch(job, "Cancelado antes de iniciar.")
                continue

            job.status = "running"
            job.progress = 0.02
            _touch(job, "Preparando inferencia GPU...")
            async with GPU_LOCK:
                if job.kind == "tts":
                    await _run_tts_job(job)
                elif job.kind == "stt":
                    await _run_stt_job(job)
                elif job.kind == "translate_audio":
                    await _run_translate_audio_job(job)
                else:
                    raise RuntimeError(f"Tipo de trabajo desconocido: {job.kind}")

            if job.cancel_requested and job.status != "done":
                job.status = "cancelled"
                _touch(job, "Cancelado.")
        except tts.SynthesisCancelled:
            job.status = "cancelled"
            job.progress = 0
            _touch(job, "Cancelado.")
            output_path = OUTPUT_DIR / f"{job.id}.mp3"
            output_path.unlink(missing_ok=True)
        except translate.TranslationCancelled:
            job.status = "cancelled"
            job.progress = 0
            _touch(job, "Cancelado.")
            output_path = OUTPUT_DIR / f"{job.id}.mp3"
            output_path.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.exception("Fallo en trabajo %s", job_id)
            if job:
                job.status = "failed"
                job.error = str(e)
                _touch(job, "Falló el trabajo.")
        finally:
            if job and job.kind in {"stt", "translate_audio"}:
                path = job.payload.get("path")
                if path:
                    Path(path).unlink(missing_ok=True)
            if job and job.status not in {"queued", "running"}:
                _persist_jobs()
            JOB_QUEUE.task_done()


async def _run_tts_job(job: Job) -> None:
    text = job.payload["text"]
    voice = job.payload.get("voice", tts.DEFAULT_VOICE)
    output_path = OUTPUT_DIR / f"{job.id}.mp3"

    def progress(done: int, total: int, fragment: str) -> None:
        job.current = done
        job.total = total
        job.progress = max(0.02, done / max(total, 1))
        preview = fragment[:72].replace("\n", " ")
        _touch(job, f"Sintetizando {done}/{total}: {preview}")

    def cancelled() -> bool:
        return job.cancel_requested

    meta = await asyncio.to_thread(
        tts.engine.synthesize_to_mp3_file,
        text,
        output_path,
        voice=voice,
        speed=1.0,
        progress_callback=progress,
        cancel_callback=cancelled,
    )
    if job.cancel_requested:
        raise tts.SynthesisCancelled()

    if UNLOAD_MODELS_AFTER_JOB:
        tts.engine.unload()

    filename = f"{_slug(job.title) or 'atlas-audio'}-{job.id}.mp3"
    job.status = "done"
    job.progress = 1.0
    job.result = {
        "audio_url": f"/api/jobs/{job.id}/audio",
        "download_url": f"/api/jobs/{job.id}/audio",
        "filename": filename,
        "path": str(output_path),
        "fragments": meta.get("fragments", job.total),
        "duration": meta.get("duration", 0),
        "bytes": meta.get("bytes", 0),
        "voice": voice,
    }
    _touch(job, "Audio listo.")


async def _run_stt_job(job: Job) -> None:
    audio_path = job.payload["path"]
    language = job.payload.get("language", "es")
    lang = None if language in ("", "auto") else language

    def progress(segment_count: int, text_preview: str) -> None:
        job.current = segment_count
        job.progress = 0.15
        _touch(job, f"Transcribiendo... {segment_count} segmentos detectados.")
        if text_preview:
            job.message = f"Transcribiendo: {text_preview[:72]}"

    def cancelled() -> bool:
        return job.cancel_requested

    result = await asyncio.to_thread(
        stt.engine.transcribe,
        audio_path,
        language=lang,
        progress_callback=progress,
        cancel_callback=cancelled,
    )
    if job.cancel_requested:
        job.status = "cancelled"
        _touch(job, "Cancelado.")
        return

    if UNLOAD_MODELS_AFTER_JOB:
        stt.engine.unload()

    job.status = "done"
    job.progress = 1.0
    job.total = len(result.get("segments", []))
    job.current = job.total
    job.result = result
    _touch(job, "Transcripción lista.")


async def _run_translate_audio_job(job: Job) -> None:
    audio_path = job.payload["path"]
    source_language = job.payload.get("source_language", "en")
    voice = job.payload.get("voice", tts.DEFAULT_VOICE)
    lang = None if source_language in ("", "auto") else source_language

    def stt_progress(segment_count: int, text_preview: str) -> None:
        job.current = segment_count
        job.progress = min(0.34, 0.06 + (segment_count * 0.004))
        _touch(job, f"Transcribiendo ingles... {segment_count} segmentos.")
        if text_preview:
            job.message = f"Transcribiendo: {text_preview[:72]}"

    def translate_progress(done: int, total: int, preview: str) -> None:
        job.current = done
        job.total = total
        job.progress = 0.35 + (done / max(total, 1)) * 0.25
        _touch(job, f"Traduciendo {done}/{total}: {preview[:72]}")

    def tts_progress(done: int, total: int, fragment: str) -> None:
        job.current = done
        job.total = total
        job.progress = 0.65 + (done / max(total, 1)) * 0.33
        preview = fragment[:72].replace("\n", " ")
        _touch(job, f"Sintetizando audio en espanol {done}/{total}: {preview}")

    def cancelled() -> bool:
        return job.cancel_requested

    _touch(job, "Transcribiendo audio en ingles...")
    transcript = await asyncio.to_thread(
        stt.engine.transcribe,
        audio_path,
        language=lang,
        progress_callback=stt_progress,
        cancel_callback=cancelled,
    )
    if cancelled():
        raise translate.TranslationCancelled()

    transcript_text = (transcript.get("text") or "").strip()
    if not transcript_text:
        raise RuntimeError("No se detecto voz util en el audio.")
    if len(transcript_text) > MAX_TRANSLATION_TEXT_CHARS:
        raise RuntimeError(
            f"El transcript tiene {len(transcript_text)} caracteres. "
            f"Limite seguro para traducir y narrar: {MAX_TRANSLATION_TEXT_CHARS}."
        )

    # Descarga Whisper antes de que Qwen cargue — reduce pico de VRAM de ~13 GB a ~9 GB.
    if UNLOAD_MODELS_AFTER_JOB:
        stt.engine.unload()

    _touch(job, "Traduciendo a espanol con Ollama...")
    translated_text = await asyncio.to_thread(
        translate.translate_to_spanish,
        transcript_text,
        progress_callback=translate_progress,
        cancel_callback=cancelled,
    )
    if cancelled():
        raise translate.TranslationCancelled()
    if len(translated_text) > MAX_TTS_CHARS:
        raise RuntimeError(
            f"La traduccion tiene {len(translated_text)} caracteres. "
            f"Limite seguro para narrar: {MAX_TTS_CHARS}."
        )

    output_path = OUTPUT_DIR / f"{job.id}.mp3"
    _touch(job, "Generando MP3 en espanol...")
    meta = await asyncio.to_thread(
        tts.engine.synthesize_to_mp3_file,
        translated_text,
        output_path,
        voice=voice,
        speed=1.0,
        progress_callback=tts_progress,
        cancel_callback=cancelled,
    )
    if cancelled():
        raise tts.SynthesisCancelled()

    if UNLOAD_MODELS_AFTER_JOB:
        tts.engine.unload()

    filename = f"{_slug(job.title) or 'audio-traducido'}-es-{job.id}.mp3"
    job.status = "done"
    job.progress = 1.0
    job.result = {
        "audio_url": f"/api/jobs/{job.id}/audio",
        "download_url": f"/api/jobs/{job.id}/audio",
        "filename": filename,
        "path": str(output_path),
        "fragments": meta.get("fragments", job.total),
        "duration": meta.get("duration", 0),
        "bytes": meta.get("bytes", 0),
        "voice": voice,
        "source_language": transcript.get("language") or source_language,
        "source_duration": transcript.get("duration", 0),
        "segments": transcript.get("segments", []),
        "transcript_text": transcript_text,
        "translated_text": translated_text,
        "translation_model": translate.DEFAULT_MODEL,
    }
    _touch(job, "Traduccion narrada lista.")


def _title_from_text(text: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:60] or "Lectura generada"


def _slug(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9áéíóúñü]+", "-", value, flags=re.IGNORECASE)
    value = value.strip("-")
    return value[:50]


def main():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")


if __name__ == "__main__":
    main()
