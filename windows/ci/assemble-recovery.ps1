[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$WinUiDirectory,
    [Parameter(Mandatory=$true)][string]$FrozenWorkerDirectory,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$release = 'https://github.com/ndsmansyj/SPP-Audio-Studio/releases/download/v1.0.1'
$baseDir = Join-Path $repo 'windows/artifacts/previous-release'
$stage = Join-Path $repo 'windows/artifacts/recovery-stage'
New-Item -ItemType Directory -Path $baseDir -Force | Out-Null
$archive = Join-Path $baseDir 'SPPAudioStudio-1.0.1-win-x64.zip'
$hashFile = "$archive.sha256"
foreach ($file in @($archive, $hashFile)) {
    $url = "$release/$(Split-Path -Leaf $file)"
    & curl.exe --fail --location --retry 3 --connect-timeout 20 --output $file $url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $url" }
}
$checksumLine = (Get-Content $hashFile -TotalCount 1).Trim()
if ($checksumLine -notmatch '^([a-fA-F0-9]{64})\s+\*?SPPAudioStudio-1\.0\.1-win-x64\.zip$') {
    throw 'Invalid published v1.0.1 checksum file'
}
$expected = $Matches[1].ToLowerInvariant()
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expected -ne $actual) { throw "Base archive checksum mismatch" }
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
Expand-Archive -Path $archive -DestinationPath $stage -Force
$root = Join-Path $stage 'SPP Audio Studio'
foreach ($required in @('SPP Audio Studio.exe','python-core/python.exe','worker/qwen_bridge.py','worker/mel_bridge.py','worker/local_api.py')) {
    if (-not (Test-Path (Join-Path $root $required))) { throw "Missing base component $required" }
}
$appDest = Join-Path $root 'app'
$workerDest = Join-Path $root 'worker/SPPWorker'
Remove-Item $appDest -Recurse -Force
Remove-Item $workerDest -Recurse -Force
Copy-Item $WinUiDirectory $appDest -Recurse
Copy-Item $FrozenWorkerDirectory $workerDest -Recurse
$guiExe = Join-Path $appDest 'SPPAudioStudio.Windows.exe'
$workerExe = Join-Path $workerDest 'SPPWorker.exe'
foreach ($exe in @($guiExe,$workerExe)) {
    if (-not (Test-Path $exe -PathType Leaf)) { throw "Missing rebuilt component $exe" }
}
$guiVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($guiExe).FileVersion
if (-not $guiVersion.StartsWith('1.0.2.')) { throw "Invalid GUI version $guiVersion" }
foreach ($command in @('model-status','doctor')) {
    $lines = @(& $workerExe $command)
    if ($LASTEXITCODE -ne 0) { throw "Frozen worker smoke test failed: $command" }
    $last = $lines | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1
    if (-not $last) { throw "No JSON returned by frozen worker for $command" }
    if (-not ($last | ConvertFrom-Json).ok) { throw "Worker error: $last" }
}
$oldManifest = Join-Path $root 'release-manifest.json'
$oldSums = Join-Path $root 'SHA256SUMS'
Remove-Item $oldManifest, $oldSums -Force
$files = @(Get-ChildItem $root -Recurse -File | Sort-Object FullName)
$manifestFiles = @($files | ForEach-Object {
    $rel = $_.FullName.Substring($root.Length + 1).Replace('\','/')
    [ordered]@{ path=$rel; size=$_.Length; sha256=(Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
$manifest = [ordered]@{
    schemaVersion=1; version='1.0.2'; commit=$env:GITHUB_SHA
    runtimeIdentifier='win-x64'; portable=$true
    sourceBase='v1.0.1'; rebuilt=@('app/','worker/SPPWorker/')
    generatedAtUtc=[DateTime]::UtcNow.ToString('o')
    files=$manifestFiles
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $oldManifest -Encoding utf8
$sumLines = @(Get-ChildItem $root -Recurse -File | Sort-Object FullName | ForEach-Object {
    $rel = $_.FullName.Substring($root.Length + 1).Replace('\','/')
    '{0} *{1}' -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $rel
})
[IO.File]::WriteAllText($oldSums,($sumLines -join [char]10)+[char]10,[Text.Encoding]::ASCII)
New-Item $OutputDirectory -ItemType Directory -Force | Out-Null
$zip = Join-Path $OutputDirectory 'SPPAudioStudio-1.0.2-win-x64.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $root -DestinationPath $zip -CompressionLevel Optimal
$zipHash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText("$zip.sha256","$zipHash *SPPAudioStudio-1.0.2-win-x64.zip"+[char]10,[Text.Encoding]::ASCII)
$testPath = Join-Path $repo 'windows/artifacts/verify-1.0.2'
if (Test-Path $testPath) { Remove-Item $testPath -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath $testPath
foreach ($rel in @('SPP Audio Studio/SPP Audio Studio.exe',
                   'SPP Audio Studio/python-core/python.exe',
                   'SPP Audio Studio/app/SPPAudioStudio.Windows.exe',
                   'SPP Audio Studio/worker/SPPWorker/SPPWorker.exe')) {
    if (-not (Test-Path (Join-Path $testPath $rel))) { throw "Incomplete portable archive: $rel" }
}
Write-Host "Windows v1.0.2 complete portable ZIP: $zip"
Write-Host "SHA256: $zipHash"
