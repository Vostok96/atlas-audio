param(
    [string]$Username = "denis",
    [string]$Password,
    [string]$Secret,
    [string]$TranslationModel = "qwen3:14b",
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"

if (-not $Password) {
    throw "Falta -Password. Usa una contraseña larga para ATLAS_AUDIO_PASSWORD."
}

if (-not $Secret) {
    throw "Falta -Secret. Usa un secreto largo para ATLAS_AUDIO_SECRET."
}

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$py312 = Get-Command py -ErrorAction SilentlyContinue
if (-not $py312) {
    throw "No encuentro el launcher 'py'. Instala Python 3.12 con: winget install Python.Python.3.12"
}

$versionCheck = & py -3.12 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
if ($LASTEXITCODE -ne 0 -or $versionCheck.Trim() -ne "3.12") {
    throw "Python 3.12 no está instalado. Instálalo con: winget install Python.Python.3.12"
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

$env:ATLAS_AUDIO_USERNAME = $Username
$env:ATLAS_AUDIO_PASSWORD = $Password
$env:ATLAS_AUDIO_SECRET = $Secret
$env:ATLAS_AUDIO_COOKIE_SECURE = "1"

# Límites conservadores para uso personal remoto.
$env:ATLAS_MAX_TTS_CHARS = "60000"
$env:ATLAS_MAX_DOCUMENT_MB = "50"
$env:ATLAS_MAX_DOCUMENT_TEXT_CHARS = "240000"
$env:ATLAS_MAX_AUDIO_MB = "200"
$env:ATLAS_MAX_TRANSLATION_CHARS = "60000"
$env:ATLAS_MAX_ACTIVE_JOBS = "3"
$env:ATLAS_SESSION_DAYS = "30"
$env:ATLAS_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
$env:ATLAS_TRANSLATION_MODEL = $TranslationModel
$env:ATLAS_TRANSLATION_UNLOAD = "1"
$env:ATLAS_TRANSLATION_KEEP_ALIVE = "2m"

Write-Host "[Atlas Audio] Actualizando pip..."
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Falló la actualización de pip." }

Write-Host "[Atlas Audio] Instalando PyTorch CUDA 12.8 para RTX 50xx..."
& $python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw "Falló la instalación de PyTorch cu128." }

Write-Host "[Atlas Audio] Instalando dependencias de Atlas Audio..."
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Falló la instalación de requirements.txt." }

Write-Host "[Atlas Audio] Verificando GPU..."
& $python scripts\check_gpu.py
if ($LASTEXITCODE -ne 0) { throw "Falló la verificación de GPU." }

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
& $python -m uvicorn app.server:app --host 0.0.0.0 --port $Port --log-level info
