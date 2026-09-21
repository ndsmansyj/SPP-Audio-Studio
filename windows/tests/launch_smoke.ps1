[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [int]$TimeoutSeconds = 15
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$exe = (Resolve-Path $Executable).Path
$process = Start-Process $exe -WorkingDirectory (Split-Path $exe -Parent) -PassThru
try {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        $process.Refresh()
        if ($process.HasExited) {
            throw "Application exited before creating a stable window (exit code $($process.ExitCode))."
        }
    } while ($process.MainWindowHandle -eq 0 -and [DateTime]::UtcNow -lt $deadline)

    if ($process.MainWindowHandle -eq 0) {
        throw "Application stayed alive but did not create a top-level window within $TimeoutSeconds seconds."
    }

    Start-Sleep -Seconds 2
    $process.Refresh()
    if ($process.HasExited) {
        throw "Application window was created, then the process exited with code $($process.ExitCode)."
    }

    [pscustomobject]@{
        ProcessId = $process.Id
        WindowHandle = $process.MainWindowHandle
        WindowTitle = $process.MainWindowTitle
    } | ConvertTo-Json -Compress
} finally {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
}
