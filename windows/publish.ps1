[CmdletBinding()]
param(
    [ValidateSet('x64', 'arm64')][string]$Platform = 'x64',
    [string]$Project,
    [string]$WorkerEntryPoint,
    [string]$Version = '0.0.0-dev',
    [switch]$NoRestore,
    [switch]$SkipWorker,
    [switch]$NoArchive
)

. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Command 'dotnet' 'Run windows/bootstrap.ps1 -InstallMissing.'
$appProject = Resolve-AppProject $Project
$msbuild = Get-VsMsBuildPath
$runtime = "win-$Platform"
$publishRoot = Join-Path $RepoRoot 'SPP Audio Studio'
$appOutput = Join-Path $publishRoot 'app'
$workerOutput = Join-Path $publishRoot 'worker'
$packageRoot = Join-Path $ArtifactsRoot 'packages'
Reset-Directory $publishRoot
New-Item $appOutput -ItemType Directory -Force | Out-Null
New-Item $packageRoot -ItemType Directory -Force | Out-Null

Push-Location $PSScriptRoot
try {
    $publishArguments = @(
        $appProject,
        '/nologo', '/m', '/t:Publish',
        '/p:Configuration=Release',
        "/p:Platform=$Platform",
        "/p:RuntimeIdentifier=$runtime",
        "/p:PublishDir=$appOutput",
        '/p:WindowsPackageType=None',
        '-p:WindowsAppSDKSelfContained=true',
        '/p:SelfContained=true',
        '/p:PublishTrimmed=false',
        '/p:PublishSingleFile=false',
        '/p:DebugType=embedded',
        '/p:DebugSymbols=false'
    )
    if (-not $NoRestore) { $publishArguments += '/restore' }
    Invoke-Native $msbuild @publishArguments

    # Keep the portable root clean: the WinUI payload lives in app\ and a
    # tiny native launcher is the only user-facing executable at the root.
    $launcherSourceRoot = Join-Path $PSScriptRoot 'src\AppLauncher'
    $launcherWork = Join-Path $ArtifactsRoot "obj\launcher-$runtime"
    Reset-Directory $launcherWork
    $launcherRes = Join-Path $launcherWork 'launcher.res'
    $launcherExe = Join-Path $publishRoot 'SPP Audio Studio.exe'
    $vsInstallation = Get-VsInstallationPath -RequireUniversalBuildTools
    $vsDevCmd = Join-Path $vsInstallation 'Common7\Tools\VsDevCmd.bat'
    if (-not (Test-Path $vsDevCmd -PathType Leaf)) { throw "VsDevCmd.bat not found: $vsDevCmd" }
    $launcherCommand = "call `"$vsDevCmd`" -arch=$Platform -host_arch=x64 >nul && pushd `"$launcherSourceRoot`" && rc.exe /nologo /fo`"$launcherRes`" launcher.rc && cl.exe /nologo /O2 /MT /EHsc /std:c++17 /utf-8 /DUNICODE /D_UNICODE launcher.cpp `"$launcherRes`" /link /SUBSYSTEM:WINDOWS user32.lib /OUT:`"$launcherExe`""
    Invoke-Native 'cmd.exe' '/d' '/s' '/c' $launcherCommand

    $converterProject = Join-Path $PSScriptRoot 'src\FormatConverter\FormatConverter.csproj'
    if (-not $SkipWorker) { $converterOutput = Join-Path $workerOutput 'bin' } else { $converterOutput = Join-Path $appOutput 'bin' }
    New-Item $converterOutput -ItemType Directory -Force | Out-Null
    Invoke-Native 'dotnet' 'publish' $converterProject '--configuration' 'Release' '--runtime' $runtime '--self-contained' 'true' '--output' $converterOutput '-p:PublishSingleFile=true' '-p:IncludeNativeLibrariesForSelfExtract=true'

    if (-not $SkipWorker) {
        $entryPoint = Resolve-WorkerEntryPoint $WorkerEntryPoint
        $venvPython = Get-VenvPython
        $pyInstallerWork = Join-Path $ArtifactsRoot "obj\pyinstaller-$runtime"
        Reset-Directory $pyInstallerWork
        New-Item $workerOutput -ItemType Directory -Force | Out-Null

        Invoke-Native $venvPython '-m' 'PyInstaller' '--noconfirm' '--clean' '--onedir' '--name' 'SPPWorker' '--distpath' $workerOutput '--workpath' (Join-Path $pyInstallerWork 'work') '--specpath' $pyInstallerWork $entryPoint

        # SPPWorker.exe is only the command/UI host. AI runtimes run in the
        # portable CPython core below, so the frozen worker does not carry a
        # duplicate stdlib or pip/ML bridge payload.
        $pythonBase = (& $venvPython -c 'import sys; print(sys.base_prefix)').Trim()
        if (-not $pythonBase) { throw 'Unable to resolve the build Python base prefix.' }
        $stdlibSource = Join-Path $pythonBase 'Lib'
        $stdlibDllSource = Join-Path $pythonBase 'DLLs'
        $stdlibExcludes = @('site-packages', '__pycache__', 'test', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', 'venv')

        $workerRoot = Join-Path $PSScriptRoot 'worker'
        foreach ($name in @('assets', 'config', 'configs')) {
            $source = Join-Path $workerRoot $name
            if (Test-Path $source -PathType Container) {
                Copy-Item $source (Join-Path $workerOutput $name) -Recurse -Force
            }
        }
        foreach ($name in @('qwen_bridge.py', 'mel_bridge.py', 'local_api.py')) {
            $source = Join-Path $workerRoot $name
            if (Test-Path $source -PathType Leaf) {
                Copy-Item $source (Join-Path $workerOutput $name) -Force
            }
        }

        $agentLauncherSource = Join-Path $workerRoot 'start_agent_api.cmd'
        if (-not (Test-Path $agentLauncherSource -PathType Leaf)) {
            throw "Agent API launcher not found: $agentLauncherSource"
        }
        Copy-Item $agentLauncherSource (Join-Path $publishRoot 'SPP Agent API.cmd') -Force

        $defaultVoiceSource = Join-Path $RepoRoot 'assets\default_voice'
        if (-not (Test-Path $defaultVoiceSource -PathType Container)) {
            throw "Bundled default voice assets not found: $defaultVoiceSource"
        }
        Copy-Item $defaultVoiceSource (Join-Path $workerOutput 'default_voice') -Recurse -Force

        # AI bridge processes need a real CPython interpreter. PyInstaller's
        # embedded interpreter is suitable for the UI worker but not for large
        # native ML stacks (torch/scipy/audio-separator). Ship a compact core.
        $pythonCore = Join-Path $publishRoot 'python-core'
        New-Item $pythonCore -ItemType Directory -Force | Out-Null
        foreach ($name in @('python.exe', 'python3.dll', 'python311.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
            $source = Join-Path $pythonBase $name
            if (Test-Path $source -PathType Leaf) {
                Copy-Item $source $pythonCore -Force
            }
        }
        $coreLib = Join-Path $pythonCore 'Lib'
        $coreDll = Join-Path $pythonCore 'DLLs'
        New-Item $coreLib -ItemType Directory -Force | Out-Null
        New-Item $coreDll -ItemType Directory -Force | Out-Null
        Get-ChildItem $stdlibSource -Force | Where-Object Name -notin $stdlibExcludes | ForEach-Object {
            Copy-Item $_.FullName $coreLib -Recurse -Force
        }
        Get-ChildItem $stdlibDllSource -Force | ForEach-Object {
            Copy-Item $_.FullName $coreDll -Recurse -Force
        }
        $coreSitePackages = Join-Path $coreLib 'site-packages'
        New-Item $coreSitePackages -ItemType Directory -Force | Out-Null
        $corePip = Join-Path (Split-Path $venvPython) '..\Lib\site-packages\pip'
        if (-not (Test-Path $corePip -PathType Container)) {
            throw "pip package not found in build venv: $corePip"
        }
        Copy-Item $corePip (Join-Path $coreSitePackages 'pip') -Recurse -Force
    }

    $commit = 'unknown'
    if (Get-Command git -ErrorAction SilentlyContinue) {
        $commitResult = & git -C $RepoRoot rev-parse HEAD 2>$null
        if ($LASTEXITCODE -eq 0) { $commit = $commitResult.Trim() }
    }

    $files = @(Get-ChildItem $publishRoot -File -Recurse | Sort-Object FullName)
    $manifestFiles = @($files | ForEach-Object {
        $relative = $_.FullName.Substring($publishRoot.Length + 1).Replace('\', '/')
        $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        [ordered]@{ path = $relative; size = $_.Length; sha256 = $hash }
    })
    $releaseManifest = [ordered]@{
        schemaVersion = 1
        version = $Version
        commit = $commit
        runtimeIdentifier = $runtime
        portable = $true
        generatedAtUtc = [DateTime]::UtcNow.ToString('o')
        files = $manifestFiles
    }
    $manifestPath = Join-Path $publishRoot 'release-manifest.json'
    $releaseManifest | ConvertTo-Json -Depth 6 | Set-Content $manifestPath -Encoding UTF8

    $checksumPath = Join-Path $publishRoot 'SHA256SUMS'
    $checksumLines = @(Get-ChildItem $publishRoot -File -Recurse | Where-Object Name -ne 'SHA256SUMS' | Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($publishRoot.Length + 1).Replace('\', '/')
        "{0} *{1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $relative
    })
    [IO.File]::WriteAllText($checksumPath, ($checksumLines -join "`n") + "`n", [Text.Encoding]::ASCII)

    if (-not $NoArchive) {
        $archive = Join-Path $packageRoot "SPPAudioStudio-$Version-$runtime.zip"
        if (Test-Path $archive) { Remove-Item $archive -Force }
        Compress-Archive -Path $publishRoot -DestinationPath $archive -CompressionLevel Optimal
        $archiveHash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText("$archive.sha256", "$archiveHash *$([IO.Path]::GetFileName($archive))`n", [Text.Encoding]::ASCII)
        Write-Host "Portable archive: $archive"
    }

    Write-Host "Publish complete: $publishRoot"
} finally {
    Pop-Location
}
