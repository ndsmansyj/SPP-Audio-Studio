using Microsoft.VisualStudio.TestTools.UnitTesting;
using SPPAudioStudio.Core;

namespace SPPAudioStudio.Core.Tests;

[TestClass]
public sealed class WorkerServiceTests
{
    [TestMethod]
    public async Task Missing_worker_is_reported_honestly()
    {
        var service = new MissingWorkerService();
        var status = await service.CheckAsync();
        Assert.IsFalse(status.IsReady);
        StringAssert.Contains(status.Message, "尚未集成");
        await Assert.ThrowsExceptionAsync<WorkerUnavailableException>(() => service.ExecuteAsync(new WorkerCommand("doctor", [])));
    }
}
