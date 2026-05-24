param(
    [string]$BaseUrl = "https://audio.microbiolog-ia.com",
    [string]$Username = "denis",
    [string]$Password
)

$ErrorActionPreference = "Stop"

if (-not $Password) {
    throw "Falta -Password para probar el login."
}

$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

Write-Host "[1/5] Health público"
$health = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/api/health" -TimeoutSec 10
Write-Host $health.Content

Write-Host "[2/5] API debe bloquear sin sesión"
try {
    Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/api/voices" -TimeoutSec 10 | Out-Null
    throw "La API respondió sin login. Revisa la autenticación."
} catch {
    if ($_.Exception.Response.StatusCode.value__ -ne 401) {
        throw
    }
    Write-Host "OK: 401 sin sesión"
}

Write-Host "[3/5] Login"
Invoke-WebRequest `
    -UseBasicParsing `
    -Uri "$BaseUrl/login" `
    -Method POST `
    -Body @{ username = $Username; password = $Password; next = "/" } `
    -WebSession $session `
    -TimeoutSec 10 | Out-Null

Write-Host "[4/5] Voces y límites autenticados"
$voices = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/api/voices" -WebSession $session -TimeoutSec 10
$limits = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/api/limits" -WebSession $session -TimeoutSec 10
Write-Host $voices.Content
Write-Host $limits.Content

Write-Host "[5/5] Estimación TTS ligera"
$estimate = Invoke-WebRequest `
    -UseBasicParsing `
    -Uri "$BaseUrl/api/tts/estimate" `
    -Method POST `
    -ContentType "application/json" `
    -Body '{"text":"En 1542 el Doctor García estudió un cultivo."}' `
    -WebSession $session `
    -TimeoutSec 10
Write-Host $estimate.Content

Write-Host "[OK] Pruebas remotas básicas completadas."
