# Cloudflare Tunnel Setup Script
# This script starts two cloudflare tunnels for Recall.ai integration

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Starting Cloudflare Tunnels..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Start WebSocket tunnel (port 8765)
Write-Host "Starting WebSocket Tunnel (Port 8765)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cloudflared tunnel --url http://localhost:8765"
Start-Sleep -Seconds 3

# Start API tunnel (port 8000)
Write-Host "Starting API Tunnel (Port 8000)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cloudflared tunnel --url http://localhost:8000"
Start-Sleep -Seconds 3

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "✅ Both tunnels started!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Look at the NEW WINDOWS that opened." -ForegroundColor Cyan
Write-Host "Each window shows a URL like:" -ForegroundColor Cyan
Write-Host "  https://abc-def-123.trycloudflare.com" -ForegroundColor White
Write-Host ""
Write-Host "COPY both URLs and provide them to update .env.local" -ForegroundColor Yellow
Write-Host ""
Write-Host "Press any key to continue..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
