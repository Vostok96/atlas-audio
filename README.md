# Atlas Audio — Lector y Transcriptor local (Kokoro + faster-whisper)

Servicio local de audio para estudiar: convierte **textos largos (libros, artículos, PDFs)
a audio fluido y natural** (TTS con Kokoro-82M) y **transcribe audio a texto**
(STT con faster-whisper). Diseñado para correr en `desktop-denis` (RTX 5070 Ti),
servirse vía **Tailscale** y, en producción, alojarse en `nas-rm` bajo el dominio
`microbiolog-ia.com`, como hermano de tu proyecto `atlas` (Qwen / redacción de tesis).

## Características

- **TTS de máxima calidad** con Kokoro-82M (Apache 2.0, voz natural por defecto, español).
- **Control de velocidad de reproducción 0.5x – 3.0x** en el reproductor, sin
  re-generar el audio y sin distorsión de tono (HTML5 `playbackRate`, pitch preservado).
- **Cola de trabajos segura para GPU**: TTS y STT se ejecutan de uno en uno.
- **Login personal** con usuario + contraseña y cookie firmada, sin gestión multiusuario.
- **Biblioteca local**: cada TTS termina como MP3 descargable en `data/output/`.
- **Lectura de documentos**: PDF, EPUB, TXT y pegado directo de texto.
- **Normalización de texto** para español: números a palabras, troceado por debajo
  del límite de ~510 tokens de Kokoro, manejo de párrafos y puntuación.
- **Transcripción** con faster-whisper `large-v3` en GPU (float16).
- **Traducción de audio EN → ES**: subes WAV/MP3/M4A/MP4 en inglés, Atlas transcribe,
  traduce con Ollama/Qwen y genera un MP3 en español con Kokoro.
- **Acceso remoto** desde laptop / celular vía Tailscale (escucha en `0.0.0.0`).
- Pensado para **GPU Blackwell (sm_120)**: usa PyTorch con CUDA 12.8 (cu128).

## Requisitos

- Windows 11 (desktop-denis) con driver NVIDIA reciente (CUDA 12.8+).
- Python 3.12. Kokoro 0.9.x no publica wheels para Python 3.13/3.14.
- Tailscale instalado y logueado (ya lo tienes: tailnet `vostok96.github`).
- `ffmpeg` en el PATH (para faster-whisper y conversión de audio).

## Configuración segura para uso personal

Atlas Audio está pensado para una sola persona, pero si se expone por
`audio.microbiolog-ia.com` debe arrancar con contraseña y cookie segura:

```powershell
$env:ATLAS_AUDIO_USERNAME="denis"
$env:ATLAS_AUDIO_PASSWORD="elige-una-contraseña-larga"
$env:ATLAS_AUDIO_SECRET="genera-un-secreto-largo-aleatorio"
$env:ATLAS_AUDIO_COOKIE_SECURE="1"
$env:ATLAS_OLLAMA_BASE_URL="http://127.0.0.1:11434"
$env:ATLAS_TRANSLATION_MODEL="qwen3:14b"
$env:ATLAS_TRANSLATION_UNLOAD="1"
```

Límites conservadores por defecto:

- `ATLAS_MAX_TTS_CHARS=60000` por trabajo TTS.
- `ATLAS_MAX_DOCUMENT_MB=50` para PDF/EPUB/TXT/MD.
- `ATLAS_MAX_DOCUMENT_TEXT_CHARS=240000` para texto extraído de documentos largos.
- `ATLAS_MAX_AUDIO_MB=200` para STT.
- `ATLAS_MAX_TRANSLATION_CHARS=60000` para traducir y narrar audio EN → ES.
- `ATLAS_MAX_ACTIVE_JOBS=3` en cola/ejecución.
- La inferencia GPU queda serializada: **una tarea a la vez**.

El servidor ya trae login web de un solo usuario; no hace falta base de datos de usuarios.
Si `ATLAS_AUDIO_PASSWORD` no está definido, la pantalla de login mostrará aviso
de setup y no abrirá la app.

## Instalación rápida (Windows / PowerShell)

```powershell
# 1. Clonar y entrar
git clone <tu-repo> atlas-tts
cd atlas-tts

# 2. Entorno virtual
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. PyTorch para Blackwell (sm_120) — LA LÍNEA DE ORO, no la cambies
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128

# 4. Resto de dependencias
pip install -r requirements.txt

# 5. (Solo la primera vez) instalar espeak-ng para el fallback fonético de Kokoro
#    Descarga el instalador desde: https://github.com/espeak-ng/espeak-ng/releases
#    En Windows instala el .msi y reinicia la terminal.

# 6. Verificar la GPU (DEBE imprimir True y el nombre de tu tarjeta sin warnings)
python scripts/check_gpu.py

# 7. Configurar acceso personal y lanzar
$env:ATLAS_AUDIO_USERNAME="denis"
$env:ATLAS_AUDIO_PASSWORD="elige-una-contraseña-larga"
$env:ATLAS_AUDIO_SECRET="genera-un-secreto-largo-aleatorio"
$env:ATLAS_AUDIO_COOKIE_SECURE="0"   # usa 1 cuando entres por HTTPS/dominio
python -m app.server
```

También puedes usar el script incluido:

```powershell
.\scripts\start_secure_desktop.ps1 `
  -Username "denis" `
  -Password "tu-contraseña-larga" `
  -Secret "tu-secreto-largo" `
  -TranslationModel "qwen3:14b"
```

Desde la laptop, valida la desktop con:

```powershell
.\scripts\test_remote_desktop.ps1 `
  -BaseUrl "https://audio.microbiolog-ia.com" `
  -Username "denis" `
  -Password "tu-contraseña-larga"
```

Cuando termines de usar `audio.microbiolog-ia.com`, detén Atlas Audio:

```powershell
.\scripts\stop_desktop.ps1
```

Abre `http://localhost:8080` en el PC. Desde el celular/laptop (con Tailscale activo):
`http://desktop-denis:8080` o `http://100.87.126.36:8080`.

## Traducción de audio con Ollama

El flujo EN → ES reutiliza la misma cola segura:

1. faster-whisper transcribe el audio en inglés.
2. Ollama traduce el transcript con `ATLAS_TRANSLATION_MODEL` (`qwen3:14b` por defecto).
3. Atlas pide a Ollama descargar el modelo de VRAM (`ATLAS_TRANSLATION_UNLOAD=1`).
4. Kokoro narra la traducción en español y guarda el MP3 en `data/output/`.

Esto mantiene una sola tarea pesada a la vez y evita dejar Qwen ocupando VRAM cuando
Kokoro empieza a sintetizar.

## Despliegue en producción (nas-rm + dominio)

El NAS (Linux) hace de **reverse proxy**, pero la **inferencia sigue en desktop-denis**
(la GPU vive ahí). Patrón idéntico a tu `atlas` actual:

1. El servicio TTS corre en `desktop-denis:8080`.
2. En `nas-rm`, un bloque de Nginx/Caddy proxea `audio.microbiolog-ia.com` →
   `http://desktop-denis:8080` a través de la red Tailscale.
3. TLS lo gestiona el reverse proxy (Caddy hace HTTPS automático).

Ver `scripts/Caddyfile.example` y `scripts/nginx.example.conf`.

## Estructura

```
atlas-tts/
├── app/
│   ├── server.py          # FastAPI: rutas web + API
│   ├── core/
│   │   ├── tts.py         # Motor Kokoro (carga perezosa, troceado, generación)
│   │   ├── stt.py         # Motor faster-whisper
│   │   ├── translate.py   # Traducción vía Ollama/Qwen
│   │   ├── textprep.py    # Normalización ES: números, siglas, troceado
│   │   └── documents.py   # Extracción de texto de PDF / EPUB / TXT
│   ├── templates/
│   │   ├── index.html     # Interfaz: trabajos, biblioteca, reproductor
│   │   └── login.html     # Login personal
│   └── static/
│       ├── app.js
│       └── styles.css
├── scripts/
│   ├── check_gpu.py       # Verifica sm_120 / CUDA
│   ├── start_secure_desktop.ps1
│   ├── stop_desktop.ps1
│   ├── Caddyfile.example
│   └── nginx.example.conf
├── data/                  # uploads/ y output/ (ignorado por git)
├── requirements.txt
└── README.md
```

## Notas sobre Kokoro y español

- Voces: `ef_dora` (femenina), `em_alex`, `em_santa` (masculinas). `lang_code='es'`.
- Kokoro no expande números ni siglas; `textprep.py` los normaliza antes de sintetizar.
- Límite ~510 tokens por fragmento → el texto se trocea por oraciones/párrafos.
- VRAM de Kokoro: <1 GB, así que convive sin problema con Qwen en la misma GPU.

## API principal

- `GET /api/health` → estado básico público.
- `GET /api/limits` → límites activos.
- `POST /api/tts/jobs` → crea trabajo TTS y devuelve `job.id`.
- `POST /api/stt/jobs` → crea trabajo STT y devuelve `job.id`.
- `POST /api/translate-audio/jobs` → crea trabajo EN → ES y devuelve `job.id`.
- `GET /api/jobs` / `GET /api/jobs/{id}` → progreso y resultado.
- `POST /api/jobs/{id}/cancel` → solicita cancelación.
- `DELETE /api/jobs/{id}` → borra el trabajo y sus archivos locales.
- `GET /api/jobs/{id}/audio` → MP3 generado.
- `GET /api/jobs/{id}/transcript` → JSON de transcripción o traducción.

Los endpoints síncronos antiguos (`/api/tts` y `/api/stt`) siguen existiendo,
pero la interfaz usa trabajos para evitar timeouts y controlar mejor la carga.

## Licencias

Kokoro-82M: Apache 2.0. faster-whisper / CTranslate2: MIT. Uso libre.
