[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')][string]$Configuration = 'Debug',
    [ValidateSet('x64', 'arm64')][string]$Platform = 'x64',
    [string]$Project,
    [switch]$NoRestore,
    [switch]$SkipWorker,
    [switch]$SkipTests
)

. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Command 'dotnet' 'Run windows/bootstrap.ps1 -InstallMissing.'
$appProject = Resolve-AppProject $Project
$runtime = "win-$Platform"
$output = Join-Path $ArtifactsRoot "build\$Configuration\$Platform\app"
New-Item $output -ItemType Directory -Force | Out-Null

Push-Location $PSScriptRoot
try {
    if (-not $NoRestore) {
        Invoke-Native 'dotnet' 'restore' $appProject '-r' $runtime "-p:Platform=$Platform"
    }

    $buildArguments = @(
        'build', $appProject,
        '--configuration', $Configuration,
        '--runtime', $runtime,
        '--no-restore',
        '--output', $output,
        "-p:Platform=$Platform"
    )
    Invoke-Native 'dotnet' @buildArguments

    $converterProject = Join-Path $PSScriptRoot 'src\FormatConverter\FormatConverter.csproj'
    $converterOutput = Join-Path $PSScriptRoot 'bin'
    New-Item $converterOutput -ItemType Directory -Force | Out-Null
    Invoke-Native 'dotnet' 'build' $converterProject '--configuration' $Configuration '--no-restore' '--output' $converterOutput

    if (-not $SkipWorker) {
        $workerRoot = Join-Path $PSScriptRoot 'worker'
        if (-not (Test-Path $workerRoot -PathType Container)) {
            throw "Expected Python worker directory not found: $workerRoot"
        }
        Invoke-Python '-m' 'compileall' '-q' $workerRoot
    }

    if (-not $SkipTests) {
        $testProjects = @(Get-ChildItem (Join-Path $PSScriptRoot 'src') -Filter '*Tests.csproj' -File -Recurse)
        foreach ($testProject in $testProjects) {
            Invoke-Native 'dotnet' 'test' $testProject.FullName '--configuration' $Configuration '--no-restore' "-p:Platform=$Platform"
        }

        $pythonTests = Join-Path $PSScriptRoot 'worker\tests'
        if (-not $SkipWorker -and (Test-Path $pythonTests -PathType Container)) {
            $python = Get-VenvPython
            Invoke-Native $python '-m' 'unittest' 'discover' '-s' $pythonTests '-v'
        }
    }

    Write-Host "Build complete: $output"
} finally {
    Pop-Location
}
