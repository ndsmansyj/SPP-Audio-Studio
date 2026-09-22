[CmdletBinding()]
param(
    [ValidateSet('x64', 'arm64')][string]$Platform = 'x64',
    [string]$Project,
    [string]$WorkerEntryPoint,
    [switch]$Development,
    [switch]$StartWorker,
    [string[]]$AppArguments = @(),
    [string[]]$WorkerArguments = @()
)

. (Join-Path $PSScriptRoot 'common.ps1')
$workerProcess = $null

function Start-OptionalArgumentProcess {
    param([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory)
    $parameters = @{
        FilePath = $FilePath
        WorkingDirectory = $WorkingDirectory
        PassThru = $true
    }
    if ($Arguments -and $Arguments.Count -gt 0) { $parameters.ArgumentList = $Arguments }
    return Start-Process @parameters
}

if ($StartWorker) {
    if ($Development) {
        $entryPoint = Resolve-WorkerEntryPoint $WorkerEntryPoint
        $python = Get-VenvPython
        $workerProcess = Start-OptionalArgumentProcess -FilePath $python -Arguments (@($entryPoint) + $WorkerArguments) -WorkingDirectory (Split-Path $entryPoint -Parent)
    } else {
        $workerDirectory = Join-Path $RepoRoot 'SPP Audio Studio\worker'
        $workerExe = Get-ChildItem $workerDirectory -Filter 'SPPWorker.exe' -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $workerExe) { throw "Published worker not found below $workerDirectory. Run windows/publish.ps1 first." }
        $workerProcess = Start-OptionalArgumentProcess -FilePath $workerExe.FullName -Arguments $WorkerArguments -WorkingDirectory $workerExe.DirectoryName
    }
    Write-Host "Worker started (PID $($workerProcess.Id))."
}

try {
    if ($Development) {
        Assert-Command 'dotnet' 'Run windows/bootstrap.ps1 -InstallMissing.'
        $appProject = Resolve-AppProject $Project
        & dotnet run --project $appProject --configuration Debug "-p:Platform=$Platform" -- @AppArguments
        if ($LASTEXITCODE -ne 0) { throw "Application exited with code $LASTEXITCODE." }
    } else {
        $appDirectory = Join-Path $RepoRoot 'SPP Audio Studio'
        $launcher = Join-Path $appDirectory 'SPP Audio Studio.exe'
        if (-not (Test-Path $launcher -PathType Leaf)) {
            throw "Published launcher not found: $launcher. Run windows/publish.ps1 first."
        }
        $process = Start-OptionalArgumentProcess -FilePath $launcher -Arguments $AppArguments -WorkingDirectory $appDirectory
        Write-Host "Application started (PID $($process.Id)): $launcher"
    }
} catch {
    if ($workerProcess -and -not $workerProcess.HasExited) { Stop-Process -Id $workerProcess.Id -Force }
    throw
}
