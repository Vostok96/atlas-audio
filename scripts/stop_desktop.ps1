param(
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"

$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $connections) {
    Write-Host "[Atlas Audio] No hay servidor escuchando en el puerto $Port."
    exit 0
}

$stopped = 0
foreach ($conn in $connections) {
    $pidToStop = [int]$conn.OwningProcess
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pidToStop" -ErrorAction SilentlyContinue
    if (-not $proc) { continue }

    $cmd = [string]$proc.CommandLine
    $isAtlasAudio = $cmd -match "app\.server" -and $cmd -match "atlas-tts"
    if (-not $isAtlasAudio) {
        Write-Warning "Puerto $Port lo usa PID $pidToStop, pero no parece Atlas Audio. No lo detengo."
        continue
    }

    Write-Host "[Atlas Audio] Deteniendo PID $pidToStop..."
    Stop-Process -Id $pidToStop -Force
    $stopped += 1
}

if ($stopped -eq 0) {
    Write-Host "[Atlas Audio] No se detuvo ningun proceso."
} else {
    Write-Host "[Atlas Audio] Servidor detenido."
}
