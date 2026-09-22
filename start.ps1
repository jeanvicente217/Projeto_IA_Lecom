# Sobe a API localmente (Windows / PowerShell)
# Uso:  .\start.ps1            -> instala deps (se necessário) e sobe com --reload
#       .\start.ps1 -Ingest    -> roda a ingestão do último xlsx em data/exports antes de subir
param(
    [switch]$Ingest,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Criando ambiente virtual..." -ForegroundColor Cyan
    python -m venv .venv
}

$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -q -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Arquivo .env criado a partir do .env.example. Preencha OPENAI_API_KEY antes de continuar." -ForegroundColor Yellow
    exit 1
}

New-Item -ItemType Directory -Force -Path "data\exports", "data\chroma" | Out-Null

if ($Ingest) {
    & $py -m app.ingest
}

& $py -m uvicorn app.main:app --reload --host 0.0.0.0 --port $Port
