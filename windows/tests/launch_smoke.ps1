[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$ReportPath,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [int]$TimeoutSeconds = 180
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$exe = (Resolve-Path $Executable).Path
$report = [IO.Path]::GetFullPath($ReportPath)
New-Item (Split-Path $report -Parent) -ItemType Directory -Force | Out-Null
if (Test-Path $report) { Remove-Item $report -Force }
$oldReport = $env:SPP_SMOKE_REPORT
$env:SPP_SMOKE_REPORT = $report
$process = $null
try {
    $process = Start-Process $exe -ArgumentList '--smoke-test' -WorkingDirectory (Split-Path $exe -Parent) -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while (-not $process.HasExited -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 250
        $process.Refresh()
    }
    if (-not $process.HasExited) { throw "WinUI smoke timed out after $TimeoutSeconds seconds; report: $report" }
    if ($process.ExitCode -ne 0) { throw "WinUI smoke failed with exit code $($process.ExitCode); report: $report" }
    if (-not (Test-Path $report -PathType Leaf)) { throw 'WinUI exited without completing the smoke report.' }
    $result = Get-Content $report -Raw | ConvertFrom-Json
    if ($result.ok -ne $true -or $result.appVersion -ne $ExpectedVersion) { throw 'WinUI smoke reported failure or a stale app version.' }
    foreach ($page in @('workbench', 'clone', 'environment')) {
        if ($page -notin $result.pagesVisited) { throw "WinUI smoke did not visit $page." }
    }
    if ($result.environmentRefreshes -lt 3 -or $result.doctorReceived -ne $true -or $result.modelStatusReceived -ne $true) {
        throw 'WinUI smoke did not finish all asynchronous worker refreshes.'
    }
    Get-Content $report -Raw | Write-Output
} finally {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    if ($null -eq $oldReport) { Remove-Item Env:SPP_SMOKE_REPORT -ErrorAction SilentlyContinue } else { $env:SPP_SMOKE_REPORT = $oldReport }
}
