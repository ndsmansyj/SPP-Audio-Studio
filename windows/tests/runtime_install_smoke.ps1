[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkerExecutable,
    [Parameter(Mandatory = $true)][string]$ReportPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$worker = (Resolve-Path $WorkerExecutable).Path
$report = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ReportPath)
$logDirectory = Join-Path (Split-Path $report -Parent) (([IO.Path]::GetFileNameWithoutExtension($report)) + '-logs')
New-Item $logDirectory -ItemType Directory -Force | Out-Null
$utf8 = [Text.UTF8Encoding]::new($false)
$runId = [Guid]::NewGuid().ToString('N')
$isolatedRoot = Join-Path ([IO.Path]::GetTempPath()) ('spp-runtime-smoke-' + $runId)
$oldLocalAppData = [Environment]::GetEnvironmentVariable('LOCALAPPDATA', 'Process')
$directoryHandle = $null
$failure = $null
$evidence = [ordered]@{
    ok = $false
    startedAt = [DateTime]::UtcNow.ToString('o')
    workerExecutable = $worker
    isolatedLocalAppData = $isolatedRoot
    steps = [Collections.Generic.List[object]]::new()
}

function Assert-Smoke([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Invoke-SmokeCommand([string]$Name, [string]$Executable, [string[]]$Arguments, [int]$TimeoutSeconds) {
    $stdoutPath = Join-Path $logDirectory ($Name + '.stdout.log')
    $stderrPath = Join-Path $logDirectory ($Name + '.stderr.log')
    $step = [ordered]@{
        name = $Name
        arguments = $Arguments
        stdoutLog = $stdoutPath
        stderrLog = $stderrPath
        timeoutSeconds = $TimeoutSeconds
    }
    $evidence.steps.Add($step)
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.WorkingDirectory = Split-Path $Executable -Parent
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = $utf8
    $startInfo.StandardErrorEncoding = $utf8
    foreach ($argument in $Arguments) { $startInfo.ArgumentList.Add($argument) }
    # Set it explicitly as well as on this process so every subprocess stays isolated.
    $startInfo.Environment['LOCALAPPDATA'] = $isolatedRoot
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $started = $false
    $stdoutTask = $null
    $stderrTask = $null
    $watch = [Diagnostics.Stopwatch]::StartNew()
    Write-Host "Runtime smoke: $Name"
    try {
        if (-not $process.Start()) { throw "Could not start $Executable" }
        $started = $true
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        while (-not $process.WaitForExit(1000)) {
            if ($watch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                $step['timedOut'] = $true
                $process.Kill($true)
                $process.WaitForExit(15000) | Out-Null
                throw "$Name timed out after $TimeoutSeconds seconds."
            }
        }
        if (-not [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($stdoutTask, $stderrTask), 15000)) {
            throw "$Name exited but its output streams did not close."
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $step['exitCode'] = $process.ExitCode
        return @{ ExitCode = $process.ExitCode; Stdout = $stdout; Stderr = $stderr; Step = $step }
    } catch {
        $step['error'] = $_.Exception.Message
        throw
    } finally {
        if ($started -and -not $process.HasExited) {
            $process.Kill($true)
            $process.WaitForExit(15000) | Out-Null
        }
        if ($null -ne $stdoutTask -and $null -ne $stderrTask) {
            try { [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($stdoutTask, $stderrTask), 15000) | Out-Null } catch { }
        }
        foreach ($capture in @(@($stdoutTask, $stdoutPath), @($stderrTask, $stderrPath))) {
            $task = $capture[0]
            $text = if ($null -ne $task -and $task.IsCompletedSuccessfully) { $task.GetAwaiter().GetResult() } else { '[Output capture did not complete.]' }
            [IO.File]::WriteAllText($capture[1], $text, $utf8)
        }
        $step['elapsedSeconds'] = [Math]::Round($watch.Elapsed.TotalSeconds, 2)
        $process.Dispose()
    }
}

function Invoke-Repair([string]$Name, [int]$TimeoutSeconds = 1800) {
    $run = Invoke-SmokeCommand $Name $worker @('runtime-install', 'asr', '--repair', '--source', 'official') $TimeoutSeconds
    $messages = @(
        foreach ($line in ($run.Stdout -split '\r?\n')) {
            if ($line.TrimStart().StartsWith('{')) { $line | ConvertFrom-Json -AsHashtable }
        }
    )
    $results = @($messages | Where-Object { $_.ContainsKey('ok') })
    Assert-Smoke ($results.Count -eq 1) "$Name must emit exactly one final JSON result; see its logs."
    $starts = @($messages | Where-Object { $_.ContainsKey('event') -and $_['event'] -eq 'runtime_install_start' })
    $run.Step['installStartEvents'] = $starts.Count
    $run.Step['result'] = $results[0]
    return @{ ExitCode = $run.ExitCode; Result = $results[0]; Starts = $starts.Count; Step = $run.Step }
}

function Assert-PreservedSentinels {
    foreach ($sentinel in $sentinels.GetEnumerator()) {
        Assert-Smoke (Test-Path $sentinel.Key -PathType Leaf) "User-data sentinel disappeared: $($sentinel.Key)"
        Assert-Smoke ([IO.File]::ReadAllText($sentinel.Key) -ceq $sentinel.Value) "User-data sentinel changed: $($sentinel.Key)"
    }
}

try {
    Assert-Smoke $IsWindows 'This acceptance test requires Windows and PowerShell 7.'
    Assert-Smoke ([IO.Path]::GetFileName($worker) -ieq 'SPPWorker.exe') 'Pass the frozen SPPWorker.exe from the assembled release package.'
    [Environment]::SetEnvironmentVariable('LOCALAPPDATA', $isolatedRoot, 'Process')
    $dataRoot = Join-Path $isolatedRoot 'SPP Audio Studio'
    $asrRoot = Join-Path $dataRoot 'Runtime/asr'
    New-Item (Join-Path $asrRoot 'site-packages') -ItemType Directory -Force | Out-Null
    $legacyIdentity = Join-Path $asrRoot 'legacy-identity.txt'
    [IO.File]::WriteAllText($legacyIdentity, $runId, $utf8)
    [IO.File]::WriteAllText((Join-Path $asrRoot 'installed.json'), '{"schema_version":1,"runtime":"asr"}', $utf8)
    $sentinels = @{}
    foreach ($relative in @('Models/smoke-preserve/model.sentinel', 'Voices/smoke-preserve/template.sentinel')) {
        $path = Join-Path $dataRoot $relative
        New-Item (Split-Path $path -Parent) -ItemType Directory -Force | Out-Null
        $sentinels[$path] = $runId + ':' + $relative
        [IO.File]::WriteAllText($path, $sentinels[$path], $utf8)
    }

    # Real pip wheels, imports and managed FFmpeg validation run in the frozen worker.
    # A GPU is deliberately not required; no model is downloaded or inferred.
    $initial = Invoke-Repair '01-legacy-repair'
    Assert-Smoke ($initial.ExitCode -eq 0 -and $initial.Result.ok -eq $true) 'Repairing the legacy ASR installation failed.'
    Assert-Smoke ($initial.Result.runtime_health.healthy -eq $true) 'The repaired ASR runtime is not healthy.'
    Assert-Smoke ($initial.Starts -eq 1) 'The initial repair must perform one real package installation.'
    Assert-Smoke (-not (Test-Path $legacyIdentity)) 'The legacy ASR directory was not replaced.'
    Assert-PreservedSentinels
    $initial.Step['legacySchemaReplaced'] = 1
    $initial.Step['userDataPreserved'] = $true

    $activeIdentity = Join-Path $asrRoot 'active-identity.txt'
    [IO.File]::WriteAllText($activeIdentity, $runId, $utf8)
    $activeMarkerHash = (Get-FileHash (Join-Path $asrRoot 'installed.json') -Algorithm SHA256).Hash
    if (-not ('SPPRuntimeSmoke.NativeMethods' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace SPPRuntimeSmoke {
    public static class NativeMethods {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(string name, uint access, uint share,
            IntPtr security, uint creation, uint flags, IntPtr template);
        public static SafeFileHandle HoldDirectory(string path) {
            // GENERIC_READ, SHARE_READ | SHARE_WRITE (no SHARE_DELETE), OPEN_EXISTING,
            // FILE_FLAG_BACKUP_SEMANTICS: block directory rename using a real Windows handle.
            var handle = CreateFileW(path, 0x80000000, 3, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero);
            if (handle.IsInvalid) {
                int error = Marshal.GetLastWin32Error();
                handle.Dispose();
                throw new Win32Exception(error);
            }
            return handle;
        }
    }
}
'@
    }
    $directoryHandle = [SPPRuntimeSmoke.NativeMethods]::HoldDirectory($asrRoot)
    try {
        $blocked = Invoke-Repair '02-directory-locked'
        Assert-Smoke ($blocked.ExitCode -eq 4 -and $blocked.Result.ok -eq $false) 'A locked active directory must fail activation with exit code 4.'
        Assert-Smoke ($blocked.Result.phase -eq 'activate') 'The locked-directory failure was not classified as activation.'
        Assert-Smoke ($blocked.Result.error_code -eq 'runtime_activation_blocked') 'The expected activation-blocked error code was not returned.'
        Assert-Smoke ($blocked.Starts -eq 1) 'Activation failure triggered another package installation or mirror fallback.'
        $staged = [IO.Path]::GetFullPath([string]$blocked.Result.staging_path)
        $stagingRoot = [IO.Path]::GetFullPath((Join-Path $dataRoot 'Runtime/.spp-install-staging'))
        Assert-Smoke ((Split-Path $staged -Parent) -ieq $stagingRoot) 'The retained stage is outside the isolated staging directory.'
        Assert-Smoke (Test-Path $staged -PathType Container) 'The verified staging directory was discarded after activation failed.'
        $stagedMarkerHash = (Get-FileHash (Join-Path $staged 'installed.json') -Algorithm SHA256).Hash
        Assert-Smoke ((Get-FileHash (Join-Path $asrRoot 'installed.json') -Algorithm SHA256).Hash -eq $activeMarkerHash) 'The active runtime marker changed while activation was blocked.'
        Assert-Smoke ([IO.File]::ReadAllText($activeIdentity) -ceq $runId) 'The previous ASR directory was not preserved.'
        Assert-PreservedSentinels
        $blocked.Step['activeRuntimePreserved'] = $true
        $blocked.Step['activeMarkerSha256'] = $activeMarkerHash
        $blocked.Step['verifiedStagePreserved'] = $true
        $blocked.Step['stagedMarkerSha256'] = $stagedMarkerHash
    } finally {
        $directoryHandle.Dispose()
        $directoryHandle = $null
    }

    $resumed = Invoke-Repair '03-resume-verified-stage' 180
    Assert-Smoke ($resumed.ExitCode -eq 0 -and $resumed.Result.ok -eq $true) 'Repair did not recover after releasing the directory handle.'
    Assert-Smoke ($resumed.Result.runtime_health.healthy -eq $true) 'The resumed ASR runtime is not healthy.'
    Assert-Smoke ($resumed.Starts -eq 0) 'The saved verified stage was not reused; pip started again.'
    Assert-Smoke (-not (Test-Path $staged)) 'The reused stage was not moved into the active runtime.'
    Assert-Smoke ((Get-FileHash (Join-Path $asrRoot 'installed.json') -Algorithm SHA256).Hash -eq $stagedMarkerHash) 'The active runtime does not match the retained verified stage.'
    Assert-Smoke (-not (Test-Path $activeIdentity)) 'The previous ASR directory remained active after recovery.'
    Assert-PreservedSentinels
    $resumed.Step['savedStageActivated'] = $true
    $resumed.Step['userDataPreserved'] = $true

    $ffmpeg = Invoke-SmokeCommand '04-managed-ffmpeg' (Join-Path $asrRoot 'ffmpeg/ffmpeg.exe') @('-version') 30
    Assert-Smoke ($ffmpeg.ExitCode -eq 0 -and $ffmpeg.Stdout -match 'ffmpeg version') 'The activated managed FFmpeg binary did not run successfully.'
    $evidence['ok'] = $true
} catch {
    $failure = $_
    $evidence['error'] = $_.Exception.Message
    $evidence['errorDetail'] = $_.ToString()
} finally {
    if ($null -ne $directoryHandle) { $directoryHandle.Dispose() }
    [Environment]::SetEnvironmentVariable('LOCALAPPDATA', $oldLocalAppData, 'Process')
    try {
        if (Test-Path $isolatedRoot) { Remove-Item $isolatedRoot -Recurse -Force }
        $evidence['isolatedDataRemoved'] = $true
    } catch {
        $evidence['isolatedDataRemoved'] = $false
        $evidence['cleanupError'] = $_.Exception.Message
    }
    $evidence['completedAt'] = [DateTime]::UtcNow.ToString('o')
    [IO.File]::WriteAllText($report, ($evidence | ConvertTo-Json -Depth 20), $utf8)
}

Get-Content $report -Raw | Write-Output
if ($null -ne $failure) { throw "Runtime installation acceptance failed: $($failure.Exception.Message) Report: $report" }
