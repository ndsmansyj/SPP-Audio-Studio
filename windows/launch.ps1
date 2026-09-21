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
        $workerDirectory = Join-Path $ArtifactsRoot "publish\win-$Platform\worker"
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
        $appDirectory = Join-Path $ArtifactsRoot "publish\win-$Platform\app"
        $executables = @(Get-ChildItem $appDirectory -Filter '*.exe' -File -ErrorAction SilentlyContinue | Where-Object Name -notlike '*Worker*')
        if ($executables.Count -eq 0) { throw "Published app not found below $appDirectory. Run windows/publish.ps1 first." }
        if ($executables.Count -gt 1) {
            $preferred = @($executables | Where-Object BaseName -notin @('createdump', 'apphost'))
            if ($preferred.Count -eq 1) { $executables = $preferred }
        }
        if ($executables.Count -ne 1) { throw "Unable to select app executable: $($executables.Name -join ', ')" }
        $app = $executables[0]
        $process = Start-OptionalArgumentProcess -FilePath $app.FullName -Arguments $AppArguments -WorkingDirectory $app.DirectoryName
        Write-Host "Application started (PID $($process.Id)): $($app.FullName)"
    }
} catch {
    if ($workerProcess -and -not $workerProcess.HasExited) { Stop-Process -Id $workerProcess.Id -Force }
    throw
}
