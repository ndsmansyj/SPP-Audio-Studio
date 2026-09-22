using Microsoft.VisualStudio.TestTools.UnitTesting;
using SPPAudioStudio.Core;

namespace SPPAudioStudio.Core.Tests;

[TestClass]
public sealed class ProcessWorkerServiceTests
{
    [TestMethod]
    public async Task Executes_worker_without_shell_and_returns_last_json_object()
    {
        var runner = new FakeProcessRunner(new ProcessRunResult(0,
            ["downloading", "{\"event\":\"download_start\",\"file\":\"a b.bin\"}", "{\"ok\":true,\"output\":\"C:\\\\Music\\\\a b.mp3\"}"], []));
        var service = new ProcessWorkerService(
            new WorkerLaunch("python.exe", ["C:\\repo path\\windows\\worker\\spp_worker.py"]), runner,
            TimeSpan.FromSeconds(5));
        var progress = new List<WorkerProgress>();
        service.Progress += (_, item) => progress.Add(item);

        var json = await service.ExecuteAsync(new WorkerCommand("convert", ["C:\\Music\\a & b.ncm", "--output-dir", "D:\\Out Dir"]));

        Assert.AreEqual("python.exe", runner.LastRequest!.FileName);
        CollectionAssert.AreEqual(new[] {
            "C:\\repo path\\windows\\worker\\spp_worker.py", "convert", "C:\\Music\\a & b.ncm", "--output-dir", "D:\\Out Dir"
        }, runner.LastRequest.Arguments.ToArray());
        Assert.IsFalse(runner.LastRequest.UseShellExecute);
        StringAssert.Contains(json, "a b.mp3");
        Assert.AreEqual("download_start", progress.Single().Event);
    }

    [TestMethod]
    public async Task Nonzero_exit_uses_worker_json_error()
    {
        var runner = new FakeProcessRunner(new ProcessRunResult(4,
            ["{\"ok\":false,\"error\":\"模型缺失\"}"], ["trace"]));
        var service = new ProcessWorkerService(new WorkerLaunch("worker.exe", []), runner);

        var ex = await Assert.ThrowsExceptionAsync<WorkerCommandException>(() =>
            service.ExecuteAsync(new WorkerCommand("separate", ["song.mp3"])));

        StringAssert.Contains(ex.Message, "模型缺失");
        Assert.AreEqual(4, ex.ExitCode);
    }

    [TestMethod]
    public async Task Cancellation_is_forwarded_to_process_runner()
    {
        var runner = new FakeProcessRunner(new ProcessRunResult(0, ["{\"ok\":true}"], []), waitForCancellation: true);
        var service = new ProcessWorkerService(new WorkerLaunch("worker.exe", []), runner);
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        try
        {
            await service.ExecuteAsync(new WorkerCommand("doctor", []), cancellation.Token);
            Assert.Fail("Expected cancellation.");
        }
        catch (OperationCanceledException) { }
        Assert.IsTrue(runner.SawCancellation);
    }

    [TestMethod]
    public async Task Timeout_kills_long_running_command()
    {
        var runner = new FakeProcessRunner(new ProcessRunResult(0, ["{\"ok\":true}"], []), waitForCancellation: true);
        var service = new ProcessWorkerService(new WorkerLaunch("worker.exe", []), runner, TimeSpan.FromMilliseconds(20));

        await Assert.ThrowsExceptionAsync<WorkerTimeoutException>(() =>
            service.ExecuteAsync(new WorkerCommand("doctor", [])));
        Assert.IsTrue(runner.SawCancellation);
    }

    [TestMethod]
    public async Task Check_uses_doctor_ready_field()
    {
        var runner = new FakeProcessRunner(new ProcessRunResult(0,
            ["{\"ok\":true,\"ready\":false,\"core_ready\":true}"], []));
        var service = new ProcessWorkerService(new WorkerLaunch("worker.exe", []), runner);

        var status = await service.CheckAsync();

        Assert.IsFalse(status.IsReady);
        StringAssert.Contains(status.Message, "组件");
        Assert.AreEqual("doctor", runner.LastRequest!.Arguments.Single());
    }

    [TestMethod]
    public void Locator_prefers_published_executable_then_developer_script()
    {
        var root = Path.Combine(Path.GetTempPath(), "spp-locator-" + Guid.NewGuid());
        try
        {
            var app = Directory.CreateDirectory(Path.Combine(root, "windows", "artifacts", "app")).FullName;
            var published = Path.Combine(root, "windows", "artifacts", "worker", "SPPWorker", "SPPWorker.exe");
            Directory.CreateDirectory(Path.GetDirectoryName(published)!);
            File.WriteAllText(published, "exe");
            var launch = WorkerLocator.Find(app, "python-test.exe");
            Assert.AreEqual(published, launch.FileName);
            Assert.AreEqual(0, launch.PrefixArguments.Count);

            File.Delete(published);
            var script = Path.Combine(root, "windows", "worker", "spp_worker.py");
            Directory.CreateDirectory(Path.GetDirectoryName(script)!);
            File.WriteAllText(script, "print(1)");
            launch = WorkerLocator.Find(app, "python-test.exe");
            Assert.AreEqual("python-test.exe", launch.FileName);
            CollectionAssert.AreEqual(new[] { script }, launch.PrefixArguments.ToArray());
        }
        finally { Directory.Delete(root, true); }
    }

    private sealed class FakeProcessRunner(ProcessRunResult result, bool waitForCancellation = false) : IProcessRunner
    {
        public ProcessRunRequest? LastRequest { get; private set; }
        public bool SawCancellation { get; private set; }

        public async Task<ProcessRunResult> RunAsync(ProcessRunRequest request, Action<string>? onOutput,
            CancellationToken cancellationToken)
        {
            LastRequest = request;
            foreach (var line in result.StandardOutput) onOutput?.Invoke(line);
            if (waitForCancellation)
            {
                try { await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken); }
                catch (OperationCanceledException) { SawCancellation = true; throw; }
            }
            cancellationToken.ThrowIfCancellationRequested();
            return result;
        }
    }
}
