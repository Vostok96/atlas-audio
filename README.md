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
- **Lectura de documentos**: PDF, EPUB, TXT y pegado directo de texto.
- **Normalización de texto** para español: números a palabras, troceado por debajo
  del límite de ~510 tokens de Kokoro, manejo de párrafos y puntuación.
- **Transcripción** con faster-whisper `large-v3` en GPU (float16).
- **Acceso remoto** desde laptop / celular vía Tailscale (escucha en `0.0.0.0`).
- Pensado para **GPU Blackwell (sm_120)**: usa PyTorch con CUDA 12.8 (cu128).

## Requisitos

- Windows 11 (desktop-denis) con driver NVIDIA reciente (CUDA 12.8+).
- Python 3.12 o 3.13.
- Tailscale instalado y logueado (ya lo tienes: tailnet `vostok96.github`).
- `ffmpeg` en el PATH (para faster-whisper y conversión de audio).

## Instalación rápida (Windows / PowerShell)

```powershell
# 1. Clonar y entrar
git clone <tu-repo> atlas-tts
cd atlas-tts

# 2. Entorno virtual
python -m venv .venv
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

# 7. Lanzar
python -m app.server
```

Abre `http://localhost:8080` en el PC. Desde el celular/laptop (con Tailscale activo):
`http://desktop-denis:8080` o `http://100.87.126.36:8080`.

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
│   │   ├── textprep.py    # Normalización ES: números, siglas, troceado
│   │   └── documents.py   # Extracción de texto de PDF / EPUB / TXT
│   ├── templates/
│   │   └── index.html     # Interfaz (reproductor con control 0.5x–3x)
│   └── static/
│       ├── app.js
│       └── styles.css
├── scripts/
│   ├── check_gpu.py       # Verifica sm_120 / CUDA
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

## Licencias

Kokoro-82M: Apache 2.0. faster-whisper / CTranslate2: MIT. Uso libre.
