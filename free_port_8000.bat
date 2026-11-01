@echo off
title Free Port 8000
echo =========================================================
echo              Freeing Port 8000
echo =========================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command "$conns = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue; if ($conns) { $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique; foreach ($p in $pids) { Write-Host \"Terminating process PID $p holding port 8000...\"; Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }; Write-Host \"Port 8000 has been freed successfully!\" } else { Write-Host \"Port 8000 is already free. No process is using it.\" }"
echo =========================================================
pause
