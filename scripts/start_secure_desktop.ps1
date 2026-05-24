# El password llega como string porque se asigna a una variable de entorno de texto plano.
# SecureString no añadiría protección real aquí.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingPlainTextForPassword', 'Password')]
param(
    [string]$Username        = "denis",
    [string]$Password        = "AtlasAudio2026",
    [string]$Secret          = "Kx9mPqR2nYvT3wLdH7sF1gBcZ4aEj5oU8iN6pA0",
    [string]$TranslationModel = "qwen3:14b",
    [int]$Port               = 8080,
    [switch]$SkipUpdate
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

# Actualizar código desde GitHub (se puede saltar con -SkipUpdate)
if (-not $SkipUpdate) {
    Write-Host "[Atlas Audio] Actualizando codigo desde GitHub..."
    & git pull origin master
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "git pull fallo. Continuando con el codigo local."
    }
}

$py312 = Get-Command py -ErrorAction SilentlyContinue
if (-not $py312) {
    throw "No encuentro el launcher 'py'. Instala Python 3.12 con: winget install Python.Python.3.12"
}

$versionCheck = & py -3.12 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
if ($LASTEXITCODE -ne 0 -or $versionCheck.Trim() -ne "3.12") {
    throw "Python 3.12 no esta instalado. Instalalo con: winget install Python.Python.3.12"
}

if (Test-Path ".\.venv\Scripts\python.exe") {
    $venvVersion = & ".\.venv\Scripts\python.exe" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    if ($venvVersion.Trim() -ne "3.12") {
        Write-Host "[Atlas Audio] Rehaciendo .venv porque no usa Python 3.12..."
        Remove-Item ".\.venv" -Recurse -Force
    }
}

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    py -3.12 -m venv .venv
}

$python = ".\.venv\Scripts\python.exe"

$env:ATLAS_AUDIO_USERNAME        = $Username
$env:ATLAS_AUDIO_PASSWORD        = $Password
$env:ATLAS_AUDIO_SECRET          = $Secret
$env:ATLAS_AUDIO_COOKIE_SECURE   = "1"

# Limites conservadores para uso personal remoto.
$env:ATLAS_MAX_TTS_CHARS             = "60000"
$env:ATLAS_MAX_DOCUMENT_MB           = "50"
$env:ATLAS_MAX_DOCUMENT_TEXT_CHARS   = "240000"
$env:ATLAS_MAX_AUDIO_MB              = "200"
$env:ATLAS_MAX_TRANSLATION_CHARS     = "60000"
$env:ATLAS_MAX_ACTIVE_JOBS           = "3"
$env:ATLAS_SESSION_DAYS              = "30"
$env:ATLAS_OLLAMA_BASE_URL           = "http://127.0.0.1:11434"
$env:ATLAS_TRANSLATION_MODEL         = $TranslationModel
$env:ATLAS_TRANSLATION_UNLOAD        = "1"
$env:ATLAS_TRANSLATION_KEEP_ALIVE    = "2m"

# Descarga Kokoro y Whisper de VRAM tras cada trabajo.
# La GPU queda libre entre sesiones; la primera peticion recarga (~10-30 s).
$env:ATLAS_UNLOAD_MODELS = "1"

Write-Host "[Atlas Audio] Actualizando pip..."
& $python -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "Fallo la actualizacion de pip." }

Write-Host "[Atlas Audio] Instalando PyTorch CUDA 12.8 para RTX 50xx..."
& $python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128 --quiet
if ($LASTEXITCODE -ne 0) { throw "Fallo la instalacion de PyTorch cu128." }

Write-Host "[Atlas Audio] Instalando dependencias..."
& $python -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { throw "Fallo la instalacion de requirements.txt." }

Write-Host "[Atlas Audio] Verificando GPU..."
& $python scripts\check_gpu.py
if ($LASTEXITCODE -ne 0) { throw "Fallo la verificacion de GPU." }

Write-Host "[Atlas Audio] Verificando Ollama para traduccion..."
try {
    $tags = Invoke-RestMethod -Uri "$env:ATLAS_OLLAMA_BASE_URL/api/tags" -TimeoutSec 5
    $modelNames = @($tags.models | ForEach-Object { $_.name })
    $modelBase = ($TranslationModel -split ":")[0]
    $available = $false
    foreach ($name in $modelNames) {
        if ($name -eq $TranslationModel -or ($name -split ":")[0] -eq $modelBase) {
            $available = $true
            break
        }
    }
    if ($available) {
        Write-Host "[OK] Ollama listo con modelo $TranslationModel."
    } else {
        Write-Warning "Ollama responde, pero no veo $TranslationModel. La traduccion fallara hasta instalarlo."
    }
} catch {
    Write-Warning "Ollama no responde en $env:ATLAS_OLLAMA_BASE_URL. TTS/STT funcionaran; traduccion requiere Ollama."
}

Write-Host "[Atlas Audio] Iniciando servidor en 0.0.0.0:$Port"
Write-Host "[Atlas Audio] Usuario: $Username"
& $python -m uvicorn app.server:app --host 0.0.0.0 --port $Port --log-level info
