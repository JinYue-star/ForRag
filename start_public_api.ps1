param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

function New-SecureToken {
    $chars = @()
    $chars += [char[]]'abcdefghijklmnopqrstuvwxyz'
    $chars += [char[]]'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    $chars += [char[]]'0123456789'
    -join (1..40 | ForEach-Object { $chars | Get-Random })
}

if (-not $env:RAG_ACCESS_TOKEN) {
    $env:RAG_ACCESS_TOKEN = New-SecureToken
}

if (-not $env:RAG_ALLOWED_ORIGINS) {
    $env:RAG_ALLOWED_ORIGINS = "http://127.0.0.1:3000,http://localhost:3000,https://jinyue-star.github.io"
}

Write-Host "Starting secured FastAPI on 127.0.0.1:$Port ..."
Write-Host ""
Write-Host "Access token:"
Write-Host $env:RAG_ACCESS_TOKEN
Write-Host ""
Write-Host "Local endpoints:"
Write-Host ("http://127.0.0.1:{0}/health" -f $Port)
Write-Host ("http://127.0.0.1:{0}/api/v1/qa" -f $Port)
Write-Host ""
Write-Host "Use cloudflared or another tunnel if you need public HTTPS access."

py -3.12 -m uvicorn fastapi_service:app --host 127.0.0.1 --port $Port
