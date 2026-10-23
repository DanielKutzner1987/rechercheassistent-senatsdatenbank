param(
    [ValidateSet('Start', 'Import', 'Stop')][string]$Action = 'Start',
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
function Run-Docker {
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw 'Docker-Schritt fehlgeschlagen. Siehe Ausgabe oben.' }
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker Desktop fehlt. Bitte https://www.docker.com/products/docker-desktop/ installieren und starten.'
}
& docker info --format '{{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker ist nicht bereit. Bitte Docker Desktop mit Linux-Containern starten und erneut versuchen.' }
if ($Action -eq 'Stop') {
    Run-Docker compose stop
    Write-Host 'Gestoppt. PDFs, Schluessel, Chats und Daten-Volumes bleiben erhalten.'
    exit 0
}
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
$ports = @{ API_PORT = 8094; WEBUI_PORT = 3002 }
foreach ($line in Get-Content -LiteralPath '.env') {
    if ($line -match '^\s*(API_PORT|WEBUI_PORT)\s*=\s*([0-9]+)\s*$') {
        $ports[$Matches[1]] = [int]$Matches[2]
    }
}
foreach ($port in $ports.Values) {
    if ($port -lt 1 -or $port -gt 65535) { throw 'Port in .env muss zwischen 1 und 65535 liegen.' }
}
$secretDirectory = Join-Path (Get-Location) 'secrets'
$keyPath = Join-Path $secretDirectory 'gemini_api_key.txt'
if (-not (Test-Path -LiteralPath $keyPath) -or (Get-Item -LiteralPath $keyPath).Length -eq 0) {
    Write-Host 'Der Test sendet ausgewaehlte PDF-Texte und Fragen an Google.'
    Write-Host 'Der eigene API-Schluessel wird lokal als Datei gespeichert und nicht angezeigt.'
    $accountSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls $secretDirectory /grant:r "*${accountSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' /inheritance:r | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Schluesselordner konnte nicht geschuetzt werden.' }
    $secureKey = Read-Host 'Eigenen Gemini-Schluessel einfuegen' -AsSecureString
    $pointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        $plainKey = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer).Trim()
        if ($plainKey -notmatch '^(AIza|AQ\.)[A-Za-z0-9._~+/=\-]{16,}$') { throw 'Schluesselformat ungueltig.' }
        [System.IO.File]::WriteAllText($keyPath, $plainKey, (New-Object System.Text.UTF8Encoding($false)))
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        $plainKey = $null
        $secureKey = $null
    }
}
Run-Docker compose config --quiet
Write-Host 'Anwendung wird vorbereitet. Beim ersten Mal werden Images und das Embedding-Modell geladen.'
Run-Docker compose build
if ($Action -eq 'Start' -and -not (Get-ChildItem -LiteralPath 'data' -Filter '*.pdf' -Recurse -File | Select-Object -First 1)) {
    Run-Docker compose run --rm demo
}
# The test project has separate names, ports and volumes from other stacks.
Run-Docker compose stop app webui
Run-Docker compose up -d ollama qdrant
Run-Docker compose run --rm index
Run-Docker compose up -d app webui
$apiUrl = 'http://localhost:' + $ports.API_PORT
$webUrl = 'http://localhost:' + $ports.WEBUI_PORT
Write-Host 'Warte auf Datenbestand und Weboberflaeche ...'
$ready = $false
for ($attempt = 0; $attempt -lt 90; $attempt++) {
    try {
        $models = Invoke-RestMethod -Uri ($apiUrl + '/v1/models') -TimeoutSec 3
        $web = Invoke-WebRequest -Uri $webUrl -UseBasicParsing -TimeoutSec 3
        if ($models.data -and $web.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $ready) { throw 'Start noch nicht bestaetigt. Bitte docker compose logs --tail 60 app webui pruefen und Starter erneut oeffnen.' }
Write-Host ('Bereit: Rechercheassistent - Senatsdatenbank auf ' + $webUrl)
Write-Host 'Ersten lokalen Benutzer anlegen und Modell Rechercheassistent - Senatsdatenbank waehlen.'
Write-Host 'Kein Gemini-Testaufruf gesendet. Das Fenster darf geschlossen werden.'
if (-not $NoBrowser) { Start-Process $webUrl }
