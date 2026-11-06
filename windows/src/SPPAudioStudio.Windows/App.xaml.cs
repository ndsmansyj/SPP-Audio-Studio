using Microsoft.UI.Xaml;

namespace SPPAudioStudio.Windows;

public partial class App : Application
{
    private Window? _window;

    public App()
    {
        InitializeComponent();
        UnhandledException += (_, args) =>
        {
            try
            {
                var logDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SPP Audio Studio", "Logs");
                Directory.CreateDirectory(logDir);
                File.AppendAllText(
                    Path.Combine(logDir, "winui-crash.log"),
                    $"[{DateTime.Now:O}] {args.Exception}\r\n\r\n",
                    System.Text.Encoding.UTF8);
            }
            catch { }
            if (SmokeTestReport.Enabled) SmokeTestReport.Fail(args.Exception);
        };
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        try
        {
            var mainWindow = new MainWindow(SmokeTestReport.Enabled);
            _window = mainWindow;
            _window.Activate();
            if (SmokeTestReport.Enabled) _ = mainWindow.RunSmokeTestAsync();
        }
        catch (Exception error) when (SmokeTestReport.Enabled)
        {
            SmokeTestReport.Fail(error);
        }
    }
}
