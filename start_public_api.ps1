param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

Write-Host "启动 FastAPI（监听 0.0.0.0:$Port）..."

$publicIp = ""
try {
    $publicIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 8).ToString().Trim()
} catch {
    Write-Warning "无法自动获取公网 IP，请手动查看。"
}

if ($publicIp) {
    Write-Host ""
    Write-Host "=== 外网访问地址（需放行端口并做端口映射） ==="
    Write-Host "http://$publicIp`:$Port/health"
    Write-Host "http://$publicIp`:$Port/api/v1/qa"
    Write-Host ""
}

python -m uvicorn fastapi_service:app --host 0.0.0.0 --port $Port
