$ErrorActionPreference = 'Stop'
$root = 'C:\Asta'
$profile = Get-NetConnectionProfile -InterfaceAlias 'Wi-Fi'
if (-not $profile -or $profile.NetworkCategory -ne 'Private') {
    throw "ASTA mobile sync is restricted to a trusted Private Wi-Fi. Run scripts\setup_asta_mobile_lan.ps1 once as Administrator."
}
$ipAddress = Get-NetIPAddress -InterfaceAlias 'Wi-Fi' -AddressFamily IPv4 -AddressState Preferred |
    Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1 -ExpandProperty IPAddress
if (-not $ipAddress) { throw 'Could not find the laptop Wi-Fi IPv4 address.' }
$rule = Get-NetFirewallRule -DisplayName 'ASTA Mobile Sync HTTPS (Private LAN)' -ErrorAction SilentlyContinue
if (-not $rule) { throw 'Firewall rule missing. Run scripts\setup_asta_mobile_lan.ps1 once as Administrator.' }
$certDir = Join-Path $root 'certs'
$caCert = Join-Path $certDir 'asta-ca.crt'
$caKey = Join-Path $certDir 'asta-ca-key.pem'
$serverKey = Join-Path $certDir 'asta-key.pem'
$serverCert = Join-Path $certDir 'asta-cert.pem'
if (-not (Test-Path $caCert) -or -not (Test-Path $caKey)) { throw 'Local CA is missing. Contact ASTA support to recreate it safely.' }
$openssl = (Get-Command openssl -ErrorAction SilentlyContinue).Source
if (-not $openssl) { $openssl = 'C:\msys64\ucrt64\bin\openssl.exe' }
$san = & $openssl x509 -in $serverCert -noout -ext subjectAltName 2>$null
if (($san -join '') -notmatch [regex]::Escape("IP Address:$ipAddress")) {
    $ext = Join-Path $certDir 'asta-server.ext'
    $csr = Join-Path $certDir 'asta-server.csr'
    @("subjectAltName=IP:$ipAddress,DNS:localhost",'extendedKeyUsage=serverAuth','basicConstraints=critical,CA:FALSE','keyUsage=critical,digitalSignature,keyEncipherment') | Set-Content -Encoding ascii $ext
    & $openssl req -newkey rsa:2048 -nodes -keyout $serverKey -out $csr -subj "/CN=$ipAddress"
    if ($LASTEXITCODE -ne 0) { throw 'Could not generate the laptop HTTPS key.' }
    & $openssl x509 -req -in $csr -CA $caCert -CAkey $caKey -CAcreateserial -out $serverCert -days 825 -sha256 -extfile $ext
    if ($LASTEXITCODE -ne 0) { throw 'Could not sign the laptop HTTPS certificate.' }
    Remove-Item $ext,$csr -Force -ErrorAction SilentlyContinue
}
$envPath = Join-Path $root '.env'
$envText = Get-Content $envPath -Raw
if ($envText -match '(?m)^AUTH_COOKIE_SECURE=') { $envText = [regex]::Replace($envText,'(?m)^AUTH_COOKIE_SECURE=.*$','AUTH_COOKIE_SECURE=true',1) }
else { $envText += "`nAUTH_COOKIE_SECURE=true`n" }
$trusted = "TRUSTED_HOSTS=localhost,127.0.0.1,::1,$ipAddress,asta.local"
if ($envText -match '(?m)^TRUSTED_HOSTS=') { $envText = [regex]::Replace($envText,'(?m)^TRUSTED_HOSTS=.*$',[System.Text.RegularExpressions.MatchEvaluator]{ param($m) $trusted },1) }
else { $envText += "`n$trusted`n" }
[System.IO.File]::WriteAllText($envPath, $envText, [System.Text.UTF8Encoding]::new($false))
Write-Host "ASTA secure LAN server: https://$ipAddress`:8443"
Write-Host 'Keep the phone and laptop on the same trusted Wi-Fi. Install mobile\asta-home-ca.crt on Android, then sign in in ASTA Mobile.'
Push-Location (Join-Path $root 'backend')
try {
    & (Join-Path $root 'venv\Scripts\python.exe') -m uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile $serverKey --ssl-certfile $serverCert
} finally { Pop-Location }
