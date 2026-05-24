param(
    [string]$NasIp = "100.75.119.68",
    [string]$NasUser = "Denis R.",
    [string]$SshKey = "$HOME\.ssh\id_ed25519_atlas",
    [string]$Domain = "audio.microbiolog-ia.com",
    [string]$DesktopTarget = "100.87.126.36:8080"
)

$ErrorActionPreference = "Stop"

# SEGURIDAD: NAS_SUDO_PASSWORD pasa el password a sudo via 'echo | sudo -S',
# que queda visible en 'ps aux' del NAS durante la ejecucion.
# Solo usar en red privada (Tailscale). En produccion usa NOPASSWD en sudoers
# o un usuario dedicado con permisos minimos sobre /etc/nginx/server.d.
$sudoPassword = $env:NAS_SUDO_PASSWORD

if ([string]::IsNullOrWhiteSpace($sudoPassword)) {
    throw "Define NAS_SUDO_PASSWORD en el entorno antes de ejecutar este deploy."
}

function Invoke-Nas {
    param([string]$Command)
    & ssh -i $SshKey -o StrictHostKeyChecking=no -l $NasUser $NasIp $Command
}

$remoteConf = "/tmp/audio_microbiolog_ia.conf"
$targetDir = "/etc/nginx/server.d"
$targetConf = "$targetDir/audio_microbiolog_ia.conf"

$nginxConf = @"
server {
    listen 80;
    server_name $Domain;

    client_max_body_size 200M;

    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;
    add_header Permissions-Policy "microphone=(), camera=(), geolocation=()" always;

    location / {
        proxy_pass http://$DesktopTarget;
        proxy_http_version 1.1;
        proxy_set_header Host `$host;
        proxy_set_header X-Real-IP `$remote_addr;
        proxy_set_header X-Forwarded-For `$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto `$scheme;
        proxy_read_timeout 1800s;
        proxy_send_timeout 1800s;
        proxy_buffering off;
    }
}
"@

Write-Host "[1/4] Copiando configuración temporal al NAS..."
$nginxConf | & ssh -i $SshKey -o StrictHostKeyChecking=no -l $NasUser $NasIp "cat > $remoteConf"

Write-Host "[2/4] Instalando virtual host con sudo..."
Invoke-Nas "echo '$sudoPassword' | sudo -S mkdir -p $targetDir && echo '$sudoPassword' | sudo -S mv $remoteConf $targetConf && echo '$sudoPassword' | sudo -S chown root:root $targetConf && echo '$sudoPassword' | sudo -S chmod 644 $targetConf && echo '$sudoPassword' | sudo -S rm -f /etc/nginx/conf.d/audio_microbiolog_ia.conf"

Write-Host "[3/4] Validando Nginx..."
Invoke-Nas "echo '$sudoPassword' | sudo -S nginx -t"

Write-Host "[4/4] Recargando Nginx..."
Invoke-Nas "echo '$sudoPassword' | sudo -S nginx -s reload"

Write-Host "[OK] Proxy instalado para https://$Domain -> http://$DesktopTarget"
