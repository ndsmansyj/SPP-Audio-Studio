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
$msbuild = Get-VsMsBuildPath
$runtime = "win-$Platform"
$output = Join-Path $ArtifactsRoot "build\$Configuration\$Platform\app"
New-Item $output -ItemType Directory -Force | Out-Null

Push-Location $PSScriptRoot
try {
    $buildArguments = @(
        $appProject,
        '/nologo', '/m', '/t:Build',
        "/p:Configuration=$Configuration",
        "/p:Platform=$Platform",
        "/p:RuntimeIdentifier=$runtime",
        "/p:OutputPath=$output"
    )
    if (-not $NoRestore) { $buildArguments += '/restore' }
    Invoke-Native $msbuild @buildArguments

    $converterProject = Join-Path $PSScriptRoot 'src\FormatConverter\FormatConverter.csproj'
    $converterOutput = Join-Path $PSScriptRoot 'bin'
    New-Item $converterOutput -ItemType Directory -Force | Out-Null
    if (-not $NoRestore) {
        Invoke-Native 'dotnet' 'restore' $converterProject
    }
    Invoke-Native 'dotnet' 'build' $converterProject '--configuration' $Configuration '--no-restore' '--output' $converterOutput

    if (-not $SkipWorker) {
        $workerRoot = Join-Path $PSScriptRoot 'worker'
        if (-not (Test-Path $workerRoot -PathType Container)) {
            throw "Expected Python worker directory not found: $workerRoot"
        }
        Invoke-Python '-m' 'compileall' '-q' $workerRoot
    }

    if (-not $SkipTests) {
        $testsRoot = Join-Path $PSScriptRoot 'tests'
        $testProjects = @(Get-ChildItem $testsRoot -Filter '*Tests.csproj' -File -Recurse)
        foreach ($testProject in $testProjects) {
            if (-not $NoRestore) {
                Invoke-Native 'dotnet' 'restore' $testProject.FullName
            }
            Invoke-Native 'dotnet' 'test' $testProject.FullName '--configuration' $Configuration '--no-restore' "-p:Platform=$Platform"
        }

        if (-not $SkipWorker -and (Test-Path $testsRoot -PathType Container)) {
            $python = Get-VenvPython
            Invoke-Native $python '-m' 'unittest' 'discover' '-s' $testsRoot '-p' 'test*.py' '-v'
        }
    }

    Write-Host "Build complete: $output"
} finally {
    Pop-Location
}
