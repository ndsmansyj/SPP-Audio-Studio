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
$publishRoot = Join-Path $ArtifactsRoot "publish\$runtime"
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
        "/p:PublishDir=$appOutput\",
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

        Invoke-Native $venvPython '-m' 'PyInstaller' '--noconfirm' '--clean' '--onedir' '--name' 'SPPWorker' '--collect-all' 'pip' '--hidden-import' 'qwen_bridge' '--hidden-import' 'mel_bridge' '--hidden-import' 'timeit' '--hidden-import' 'pickletools' '--distpath' $workerOutput '--workpath' (Join-Path $pyInstallerWork 'work') '--specpath' $pyInstallerWork $entryPoint

        $workerRoot = Join-Path $PSScriptRoot 'worker'
        foreach ($name in @('assets', 'config', 'configs')) {
            $source = Join-Path $workerRoot $name
            if (Test-Path $source -PathType Container) {
                Copy-Item $source (Join-Path $workerOutput $name) -Recurse -Force
            }
        }
        foreach ($name in @('qwen_bridge.py', 'mel_bridge.py')) {
            $source = Join-Path $workerRoot $name
            if (Test-Path $source -PathType Leaf) {
                Copy-Item $source (Join-Path $workerOutput $name) -Force
            }
        }
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
        Compress-Archive -Path (Join-Path $publishRoot '*') -DestinationPath $archive -CompressionLevel Optimal
        $archiveHash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText("$archive.sha256", "$archiveHash *$([IO.Path]::GetFileName($archive))`n", [Text.Encoding]::ASCII)
        Write-Host "Portable archive: $archive"
    }

    Write-Host "Publish complete: $publishRoot"
} finally {
    Pop-Location
}
