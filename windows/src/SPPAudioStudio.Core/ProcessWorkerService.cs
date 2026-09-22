using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace SPPAudioStudio.Core;

public sealed record WorkerLaunch(string FileName, IReadOnlyList<string> PrefixArguments);
public sealed record ProcessRunRequest(string FileName, IReadOnlyList<string> Arguments, bool UseShellExecute = false);
public sealed record ProcessRunResult(int ExitCode, IReadOnlyList<string> StandardOutput, IReadOnlyList<string> StandardError);
public sealed record WorkerProgress(string Event, string Json);

public interface IProcessRunner
{
    Task<ProcessRunResult> RunAsync(ProcessRunRequest request, Action<string>? onOutput,
        CancellationToken cancellationToken);
}

public sealed class SystemProcessRunner : IProcessRunner
{
    public async Task<ProcessRunResult> RunAsync(ProcessRunRequest request, Action<string>? onOutput,
        CancellationToken cancellationToken)
    {
        var start = new ProcessStartInfo
        {
            FileName = request.FileName,
            UseShellExecute = request.UseShellExecute,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        foreach (var argument in request.Arguments) start.ArgumentList.Add(argument);
        using var process = new Process { StartInfo = start };
        var stdout = new List<string>();
        var stderr = new List<string>();
        process.OutputDataReceived += (_, e) =>
        {
            if (e.Data is null) return;
            lock (stdout) stdout.Add(e.Data);
            onOutput?.Invoke(e.Data);
        };
        process.ErrorDataReceived += (_, e) => { if (e.Data is not null) lock (stderr) stderr.Add(e.Data); };
        if (!process.Start()) throw new WorkerUnavailableException($"无法启动 Windows Worker：{request.FileName}");
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        try
        {
            await process.WaitForExitAsync(cancellationToken);
            process.WaitForExit();
        }
        catch (OperationCanceledException)
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
            throw;
        }
        return new ProcessRunResult(process.ExitCode, stdout.ToArray(), stderr.ToArray());
    }
}

public static class WorkerLocator
{
    public static WorkerLaunch Find(string baseDirectory, string? pythonExecutable = null)
    {
        var start = new DirectoryInfo(Path.GetFullPath(baseDirectory));
        foreach (var directory in Ancestors(start))
        {
            foreach (var relative in new[] { "worker/SPPWorker/SPPWorker.exe", "worker/spp_worker.exe", "spp_worker.exe" })
            {
                var executable = Path.Combine(directory.FullName, relative.Replace('/', Path.DirectorySeparatorChar));
                if (File.Exists(executable)) return new WorkerLaunch(executable, []);
            }
        }
        foreach (var directory in Ancestors(start))
        {
            var script = Path.Combine(directory.FullName, "windows", "worker", "spp_worker.py");
            if (File.Exists(script)) return new WorkerLaunch(pythonExecutable ?? "python", [script]);
            script = Path.Combine(directory.FullName, "worker", "spp_worker.py");
            if (File.Exists(script)) return new WorkerLaunch(pythonExecutable ?? "python", [script]);
        }
        throw new WorkerUnavailableException("找不到 Windows Worker（spp_worker.exe 或 windows/worker/spp_worker.py）。");
    }

    private static IEnumerable<DirectoryInfo> Ancestors(DirectoryInfo? directory)
    {
        for (var current = directory; current is not null; current = current.Parent) yield return current;
    }
}

public sealed class WorkerCommandException(string message, int exitCode, string diagnostic) : InvalidOperationException(message)
{
    public int ExitCode { get; } = exitCode;
    public string Diagnostic { get; } = diagnostic;
}

public sealed class WorkerTimeoutException(string message) : TimeoutException(message);

public sealed class ProcessWorkerService : IWorkerService
{
    private readonly WorkerLaunch _launch;
    private readonly IProcessRunner _runner;
    private readonly TimeSpan _timeout;
    public event EventHandler<WorkerProgress>? Progress;

    public ProcessWorkerService(WorkerLaunch launch, IProcessRunner? runner = null, TimeSpan? timeout = null)
    {
        _launch = launch;
        _runner = runner ?? new SystemProcessRunner();
        _timeout = timeout ?? TimeSpan.FromHours(6);
    }

    public static ProcessWorkerService CreateDefault() => new(WorkerLocator.Find(AppContext.BaseDirectory));

    public async Task<WorkerStatus> CheckAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            using var document = JsonDocument.Parse(await ExecuteAsync(new WorkerCommand("doctor", []), cancellationToken));
            var root = document.RootElement;
            var ready = root.TryGetProperty("ready", out var value) && value.GetBoolean();
            return new WorkerStatus(ready, ready ? "本地引擎就绪" : "Worker 可用，部分模型或运行组件尚未就绪");
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            return new WorkerStatus(false, error.Message);
        }
    }

    public async Task<string> ExecuteAsync(WorkerCommand command, CancellationToken cancellationToken = default)
    {
        var arguments = _launch.PrefixArguments.Concat(new[] { command.Name }).Concat(command.Arguments).ToArray();
        using var timeout = new CancellationTokenSource(_timeout);
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, timeout.Token);
        ProcessRunResult result;
        try
        {
            result = await _runner.RunAsync(new ProcessRunRequest(_launch.FileName, arguments), HandleOutput, linked.Token);
        }
        catch (OperationCanceledException) when (timeout.IsCancellationRequested && !cancellationToken.IsCancellationRequested)
        {
            throw new WorkerTimeoutException($"Windows Worker 执行超时（{_timeout}）。");
        }
        var json = result.StandardOutput.LastOrDefault(IsJsonObject);
        if (json is null)
            throw new WorkerCommandException("Windows Worker 未返回有效 JSON。", result.ExitCode,
                string.Join(Environment.NewLine, result.StandardError));
        using var document = JsonDocument.Parse(json);
        var ok = document.RootElement.TryGetProperty("ok", out var okValue) && okValue.GetBoolean();
        if (result.ExitCode != 0 || !ok)
        {
            var message = document.RootElement.TryGetProperty("error", out var error) ? error.GetString() : null;
            throw new WorkerCommandException(message ?? $"Windows Worker 失败（退出码 {result.ExitCode}）。",
                result.ExitCode, string.Join(Environment.NewLine, result.StandardError));
        }
        return json;
    }

    private void HandleOutput(string line)
    {
        if (!IsJsonObject(line)) return;
        using var document = JsonDocument.Parse(line);
        if (document.RootElement.TryGetProperty("event", out var eventName))
            Progress?.Invoke(this, new WorkerProgress(eventName.GetString() ?? "progress", line));
    }

    private static bool IsJsonObject(string line)
    {
        try { return JsonDocument.Parse(line).RootElement.ValueKind == JsonValueKind.Object; }
        catch (JsonException) { return false; }
    }
}
