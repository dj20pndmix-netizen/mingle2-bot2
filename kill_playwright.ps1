# Kill playwright driver node.exe
Get-Process | Where-Object { $_.Path -like "*playwright\driver\node.exe*" } | ForEach-Object {
    Write-Host "Killing Playwright driver node PID: $($_.Id)"
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}

# Kill all ms-playwright chromium instances
Get-Process | Where-Object { $_.Path -like "*ms-playwright\chromium*" } | ForEach-Object {
    Write-Host "Killing Playwright Chromium process PID: $($_.Id)"
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 800

# Remove locks
$lockFiles = @(
    "browser_data\lockfile",
    "browser_data\SingletonLock",
    "browser_data\SingletonCookie",
    "browser_data\SingletonSocket"
)

foreach ($f in $lockFiles) {
    if (Test-Path $f) {
        Remove-Item $f -Force -ErrorAction SilentlyContinue
        Write-Host "Removed lock file: $f"
    }
}
