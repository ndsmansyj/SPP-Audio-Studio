namespace SPPAudioStudio.Core;

public enum ToolMode
{
    Convert,
    Separate,
    ConvertAndSeparate
}

public static class AudioFilePolicy
{
    private static readonly HashSet<string> AudioExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".ncm", ".mp3", ".flac", ".wav", ".m4a", ".aac", ".aif", ".aiff", ".caf"
    };

    public static bool IsSupported(string path, ToolMode mode)
    {
        var extension = Path.GetExtension(path);
        if (!AudioExtensions.Contains(extension)) return false;
        return mode switch
        {
            ToolMode.Convert => extension.Equals(".ncm", StringComparison.OrdinalIgnoreCase),
            ToolMode.Separate => !extension.Equals(".ncm", StringComparison.OrdinalIgnoreCase),
            _ => true
        };
    }
}

public enum TaskStatus { Waiting, Processing, Completed, Failed }

public sealed class AudioTask
{
    internal AudioTask(string path, ToolMode mode)
    {
        Id = Guid.NewGuid();
        Path = path;
        Title = System.IO.Path.GetFileName(path);
        Mode = mode;
    }

    public Guid Id { get; }
    public string Path { get; }
    public string Title { get; }
    public ToolMode Mode { get; }
    public TaskStatus Status { get; internal set; } = TaskStatus.Waiting;
    public string Detail { get; internal set; } = string.Empty;
    public string? OutputPath { get; internal set; }
}

public sealed class TaskQueue
{
    private readonly List<AudioTask> _items = [];
    public IReadOnlyList<AudioTask> Items => _items;

    public AudioTask Enqueue(string path, ToolMode mode)
    {
        if (_items.Any(item => string.Equals(item.Path, path, StringComparison.OrdinalIgnoreCase)))
            throw new InvalidOperationException("文件已在任务列表中");
        var task = new AudioTask(path, mode);
        _items.Insert(0, task);
        return task;
    }

    public void MarkProcessing(Guid id, string detail = "正在执行…") => Update(id, TaskStatus.Processing, detail, null);
    public void MarkCompleted(Guid id, string? outputPath) => Update(id, TaskStatus.Completed, "已完成", outputPath);
    public void MarkFailed(Guid id, string detail) => Update(id, TaskStatus.Failed, detail, null);

    private void Update(Guid id, TaskStatus status, string detail, string? outputPath)
    {
        var task = _items.Single(item => item.Id == id);
        task.Status = status;
        task.Detail = detail;
        task.OutputPath = outputPath;
    }
}

public sealed record WorkerStatus(bool IsReady, string Message);
public sealed record WorkerCommand(string Name, IReadOnlyList<string> Arguments);

public interface IWorkerService
{
    Task<WorkerStatus> CheckAsync(CancellationToken cancellationToken = default);
    Task<string> ExecuteAsync(WorkerCommand command, CancellationToken cancellationToken = default);
}

public sealed class WorkerUnavailableException(string message) : InvalidOperationException(message);

public sealed class MissingWorkerService : IWorkerService
{
    public const string MissingMessage = "Windows Worker 尚未集成；当前版本仅提供界面与 C# 核心骨架。";
    private readonly string _message;
    public MissingWorkerService(string? message = null) => _message = message ?? MissingMessage;
    public Task<WorkerStatus> CheckAsync(CancellationToken cancellationToken = default) =>
        Task.FromResult(new WorkerStatus(false, _message));

    public Task<string> ExecuteAsync(WorkerCommand command, CancellationToken cancellationToken = default) =>
        Task.FromException<string>(new WorkerUnavailableException(_message));
}

public sealed record VoiceCloneRequest(string? TemplateId, string? ReferenceAudioPath, string Script, string? OutputDirectory)
{
    public string? Validate()
    {
        if (string.IsNullOrWhiteSpace(Script)) return "先输入需要生成的文案";
        if (string.IsNullOrWhiteSpace(TemplateId) && string.IsNullOrWhiteSpace(ReferenceAudioPath))
            return "先建立一个人声模板，或选择临时参考音";
        return null;
    }
}

public sealed record VoiceTemplate(string Id, string Name, string ReferenceText, string Note, bool IsDefault);
public sealed record ModelComponent(string Name, string Purpose, string Size, string License, bool Installed, string Source, string Path);
