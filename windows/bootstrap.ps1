[CmdletBinding()]
param(
    [switch]$InstallMissing,
    [switch]$CheckOnly,
    [switch]$SkipPythonPackages
)

. (Join-Path $PSScriptRoot 'common.ps1')
$manifest = Get-ToolchainManifest

function Test-CommandAvailable([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-VersionAtLeast([string]$Actual, [string]$Minimum) {
    try { return ([version]$Actual -ge [version]$Minimum) } catch { return $false }
}

function Install-WingetPackage {
    param([string]$Id, [string[]]$ExtraArguments = @())
    Assert-Command 'winget.exe' 'Install App Installer from the Microsoft Store, then retry.'
    $arguments = @(
        'install', '--id', $Id, '--exact', '--source', 'winget',
        '--accept-package-agreements', '--accept-source-agreements', '--silent'
    ) + $ExtraArguments
    Invoke-Native 'winget.exe' @arguments
}

function Get-VsWherePath {
    $candidate = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $candidate -PathType Leaf) { return $candidate }
    return $null
}

Push-Location $PSScriptRoot
try {
    $missing = New-Object System.Collections.Generic.List[string]

    if (-not (Test-CommandAvailable 'dotnet.exe') -and -not (Test-CommandAvailable 'dotnet')) {
        $missing.Add('Microsoft.DotNet.SDK.8')
    } else {
        $dotnetVersion = (& dotnet --version).Trim()
        if ($LASTEXITCODE -ne 0 -or -not (Test-VersionAtLeast $dotnetVersion $manifest.dotnetSdk) -or -not $dotnetVersion.StartsWith('8.0.')) {
            throw "The .NET SDK must be 8.0 and at least $($manifest.dotnetSdk); dotnet reported '$dotnetVersion'."
        }
        Write-Host ".NET SDK: $dotnetVersion"
    }

    try {
        $pythonCommand = @(Get-PythonCommand)
        $pythonVersionText = (& $pythonCommand[0] @($pythonCommand | Select-Object -Skip 1) --version 2>&1).ToString().Trim()
        $pythonVersion = $pythonVersionText -replace '^Python\s+', ''
        if ($LASTEXITCODE -ne 0 -or -not $pythonVersion.StartsWith('3.11.') -or -not (Test-VersionAtLeast $pythonVersion $manifest.python)) {
            throw "Python must be 3.11 and at least $($manifest.python); interpreter reported '$pythonVersionText'."
        }
        Write-Host $pythonVersionText
    } catch {
        Write-Warning $_.Exception.Message
        $missing.Add('Python.Python.3.11')
    }

    $vswhere = Get-VsWherePath
    $hasBuildTools = $false
    if ($vswhere) {
        $installation = & $vswhere -latest -products '*' -requires Microsoft.Component.MSBuild -property installationPath
        $hasBuildTools = [bool]$installation
        if ($hasBuildTools) { Write-Host "MSBuild toolchain: $installation" }
    }
    if (-not $hasBuildTools) { $missing.Add('Microsoft.VisualStudio.2022.BuildTools') }

    if ($missing.Count -gt 0 -and -not $InstallMissing) {
        throw "Missing prerequisites: $($missing -join ', '). Re-run with -InstallMissing."
    }

    if ($InstallMissing) {
        foreach ($id in $missing) {
            if ($id -eq 'Microsoft.VisualStudio.2022.BuildTools') {
                Install-WingetPackage $id @(
                    '--override',
                    '--wait --passive --norestart --add Microsoft.VisualStudio.Workload.ManagedDesktopBuildTools --add Microsoft.VisualStudio.Component.Windows11SDK.22621 --includeRecommended'
                )
            } else {
                Install-WingetPackage $id
            }
        }
        if ($missing.Count -gt 0) {
            Write-Host 'Prerequisites were installed. Start a new shell and run bootstrap again to refresh PATH.'
            exit 0
        }
    }

    if ($CheckOnly) {
        Write-Host 'Windows developer prerequisites are available.'
        exit 0
    }

    $venv = Join-Path $PSScriptRoot '.venv'
    if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe') -PathType Leaf)) {
        Write-Host "Creating Python build environment at $venv"
        Invoke-Python '-m' 'venv' $venv
    }

    if (-not $SkipPythonPackages) {
        $venvPython = Get-VenvPython
        Invoke-Native $venvPython '-m' 'pip' 'install' '--disable-pip-version-check' '--upgrade' 'pip'
        Invoke-Native $venvPython '-m' 'pip' 'install' '--disable-pip-version-check' '-r' (Join-Path $PSScriptRoot 'requirements-build.txt')

        $workerRoot = Join-Path $PSScriptRoot 'worker'
        $workerLock = Join-Path $workerRoot 'requirements.lock'
        $workerRequirements = Join-Path $workerRoot 'requirements.txt'
        if (Test-Path $workerLock -PathType Leaf) {
            Invoke-Native $venvPython '-m' 'pip' 'install' '--disable-pip-version-check' '-r' $workerLock
        } elseif (Test-Path $workerRequirements -PathType Leaf) {
            Write-Warning 'Using unpinned windows/worker/requirements.txt; add requirements.lock for reproducible releases.'
            Invoke-Native $venvPython '-m' 'pip' 'install' '--disable-pip-version-check' '-r' $workerRequirements
        } elseif (Test-Path $workerRoot -PathType Container) {
            Write-Warning 'No worker requirements file found; only packaging dependencies were installed.'
        }
    }

    Write-Host 'Bootstrap complete.'
} finally {
    Pop-Location
}
