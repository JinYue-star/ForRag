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

# 仅当未设置、全空白、或过短时生成；若误把教程占位符写进环境变量也会重新生成
$tok = $env:RAG_ACCESS_TOKEN
$needToken = [string]::IsNullOrWhiteSpace($tok)
if (-not $needToken) {
    $t = $tok.Trim()
    if ($t.Length -lt 16) { $needToken = $true }
    elseif ($t -match '请换|placeholder|changeme|your-token|example|REPLACE') { $needToken = $true }
}
if ($needToken) {
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

$pythonCmd = "python"
if ($env:CONDA_PREFIX) {
    $condaPython = Join-Path $env:CONDA_PREFIX "python.exe"
    if (Test-Path $condaPython) {
        $pythonCmd = $condaPython
    }
}

& $pythonCmd -m uvicorn fastapi_service:app --host 127.0.0.1 --port $Port
