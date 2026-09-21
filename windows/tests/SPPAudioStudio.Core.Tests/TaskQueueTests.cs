using Microsoft.VisualStudio.TestTools.UnitTesting;
using SPPAudioStudio.Core;

namespace SPPAudioStudio.Core.Tests;

[TestClass]
public sealed class TaskQueueTests
{
    [TestMethod]
    public void Added_task_starts_waiting_and_can_transition_to_failed()
    {
        var queue = new TaskQueue();
        var task = queue.Enqueue("demo.wav", ToolMode.Separate);
        Assert.AreEqual(TaskStatus.Waiting, task.Status);
        queue.MarkFailed(task.Id, "Worker 尚未集成");
        Assert.AreEqual(TaskStatus.Failed, task.Status);
        Assert.AreEqual("Worker 尚未集成", task.Detail);
    }

    [TestMethod]
    public void Queue_rejects_duplicate_file_paths()
    {
        var queue = new TaskQueue();
        queue.Enqueue("C:/music/demo.wav", ToolMode.Separate);
        Assert.ThrowsException<InvalidOperationException>(() => queue.Enqueue("C:/music/demo.wav", ToolMode.Separate));
    }
}
