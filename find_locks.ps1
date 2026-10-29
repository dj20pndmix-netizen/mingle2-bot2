Get-Process | Where-Object { $_.ProcessName -match "python|chrome|node" } | Select-Object Id, ProcessName, Path
