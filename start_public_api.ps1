param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

Write-Host "Starting FastAPI on 0.0.0.0:$Port ..."

$publicIp = ""
try {
    $publicIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 8).ToString().Trim()
} catch {
    Write-Warning "Cannot fetch public IP automatically."
}

if ($publicIp) {
    Write-Host ""
    Write-Host "=== Public endpoints (port open + NAT required) ==="
    Write-Host ("http://{0}:{1}/health" -f $publicIp, $Port)
    Write-Host ("http://{0}:{1}/api/v1/qa" -f $publicIp, $Port)
    Write-Host ""
}

py -3.12 -m uvicorn fastapi_service:app --host 0.0.0.0 --port $Port
