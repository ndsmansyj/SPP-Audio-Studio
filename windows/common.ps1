Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:WindowsRoot = $PSScriptRoot
$script:RepoRoot = Split-Path -Parent $script:WindowsRoot
$script:ArtifactsRoot = Join-Path $script:WindowsRoot 'artifacts'

function Get-ToolchainManifest {
    Get-Content (Join-Path $script:WindowsRoot 'toolchain.json') -Raw | ConvertFrom-Json
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Get-PythonCommand {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
    if ($python) { return @($python.Source) }

    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { return @($py.Source, '-3.11') }

    throw 'Python 3.11 was not found. Run windows/bootstrap.ps1 -InstallMissing.'
}

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $command = @(Get-PythonCommand)
    $exe = $command[0]
    $prefix = @()
    if ($command.Count -gt 1) { $prefix = $command[1..($command.Count - 1)] }
    Invoke-Native $exe @prefix @Arguments
}

function Get-VenvPython {
    $python = Join-Path $script:WindowsRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $python -PathType Leaf)) {
        throw 'Build virtual environment is missing. Run windows/bootstrap.ps1 first.'
    }
    return $python
}

function Resolve-AppProject {
    param([string]$Project)
    if ($Project) {
        $resolved = Resolve-Path $Project -ErrorAction Stop
        return $resolved.Path
    }

    $sourceRoot = Join-Path $script:WindowsRoot 'src'
    if (-not (Test-Path $sourceRoot -PathType Container)) {
        throw "Expected WinUI source directory not found: $sourceRoot"
    }

    $projects = @(Get-ChildItem $sourceRoot -Filter '*.csproj' -File -Recurse)
    if ($projects.Count -eq 0) {
        throw "No .csproj found below $sourceRoot. Pass -Project explicitly if the project is elsewhere."
    }

    $winUi = @($projects | Where-Object {
        Select-String -Path $_.FullName -Pattern '<UseWinUI>\s*true\s*</UseWinUI>' -Quiet
    })
    if ($winUi.Count -eq 1) { return $winUi[0].FullName }
    if ($projects.Count -eq 1) { return $projects[0].FullName }

    throw "Multiple app projects found. Pass -Project. Candidates: $($projects.FullName -join ', ')"
}

function Resolve-WorkerEntryPoint {
    param([string]$EntryPoint)
    if ($EntryPoint) { return (Resolve-Path $EntryPoint -ErrorAction Stop).Path }

    $workerRoot = Join-Path $script:WindowsRoot 'worker'
    foreach ($name in @('main.py', 'spp_worker.py', 'local_api.py', '__main__.py')) {
        $candidate = Join-Path $workerRoot $name
        if (Test-Path $candidate -PathType Leaf) { return $candidate }
    }
    throw "No worker entry point found below $workerRoot. Pass -WorkerEntryPoint."
}

function Assert-Command {
    param([Parameter(Mandatory = $true)][string]$Name, [string]$InstallHint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        if ($InstallHint) { throw "$Name is required. $InstallHint" }
        throw "$Name is required."
    }
}

function Reset-Directory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-Path $Path) { Remove-Item $Path -Recurse -Force }
    New-Item $Path -ItemType Directory -Force | Out-Null
}
