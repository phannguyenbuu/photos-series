$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$agentRoot = Join-Path (Split-Path -Parent $taskRoot) 'Goxprint\GoxAgent'
$target = [IO.Path]::GetFullPath((Join-Path $agentRoot 'dist\printagent.exe'))
$built = Join-Path $taskRoot 'output\photo-series-release\printagent.exe'
$backup = Join-Path $agentRoot 'dist\printagent.before-photo-series.exe'
if (-not (Test-Path -LiteralPath $built)) { throw 'New build is missing' }
python (Join-Path $PSScriptRoot 'check_safe_agent_restart.py')
if ($LASTEXITCODE -ne 0) { throw 'PrintAgent restart deferred to preserve active photo work.' }
if (-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $target -Destination $backup }
$processes = Get-CimInstance Win32_Process -Filter "Name='printagent.exe'" | Where-Object { $_.ExecutablePath -eq $target }
foreach ($process in $processes) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 1000
Copy-Item -LiteralPath $built -Destination $target -Force
Start-Process -FilePath $target -WorkingDirectory $agentRoot -WindowStyle Hidden
$ready = $false
for ($attempt = 0; $attempt -lt 50; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest 'http://127.0.0.1:9173/api/photo-series/jobs/probe-missing' -SkipHttpErrorCheck -TimeoutSec 1
        if ($response.StatusCode -eq 404 -and $response.Content -match 'error') { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    $newProcesses = Get-CimInstance Win32_Process -Filter "Name='printagent.exe'" | Where-Object { $_.ExecutablePath -eq $target }
    foreach ($process in $newProcesses) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
    Copy-Item -LiteralPath $backup -Destination $target -Force
    Start-Process -FilePath $target -WorkingDirectory $agentRoot -WindowStyle Hidden
    throw 'New PrintAgent failed health check; previous executable restored.'
}
Write-Output "Activated PrintAgent with photo series API: $target"
