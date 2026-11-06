using System.Text.Json;

namespace SPPAudioStudio.Windows;

internal static class SmokeTestReport
{
    internal static bool Enabled => System.Environment.GetCommandLineArgs()
        .Any(argument => string.Equals(argument, "--smoke-test", StringComparison.OrdinalIgnoreCase));

    internal static string AppVersion => typeof(App).Assembly.GetName().Version?.ToString(3) ?? "unknown";

    internal static void Fail(Exception error) => WriteAndExit(new
    {
        ok = false,
        appVersion = AppVersion,
        error = error.ToString(),
        completedAt = DateTimeOffset.UtcNow
    }, 1);

    internal static void WriteAndExit(object report, int exitCode)
    {
        try
        {
            var path = System.Environment.GetEnvironmentVariable("SPP_SMOKE_REPORT");
            if (string.IsNullOrWhiteSpace(path) || !Path.IsPathFullyQualified(path))
                throw new InvalidOperationException("SPP_SMOKE_REPORT must be an absolute file path.");
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var temporaryPath = path + ".tmp";
            File.WriteAllText(temporaryPath, JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
            File.Move(temporaryPath, path, true);
        }
        catch
        {
            // The launcher also requires a fresh success report, so report failures cannot pass CI.
            exitCode = 1;
        }
        System.Environment.Exit(exitCode);
    }
}
