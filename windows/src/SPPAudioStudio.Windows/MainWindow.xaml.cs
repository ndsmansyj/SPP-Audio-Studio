using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Microsoft.UI.Xaml.Shapes;
using SPPAudioStudio.Core;
using System.Diagnostics;
using System.Text.Json;
using IOPath = System.IO.Path;
using CoreTaskStatus = SPPAudioStudio.Core.TaskStatus;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace SPPAudioStudio.Windows;

public sealed partial class MainWindow : Window
{
    private AppWindow? _appWindow;
    private readonly Dictionary<string, ScrollViewer> _pageCache = [];
    private readonly Dictionary<string, Button> _navButtons = [];
    private readonly Grid _navigation;
    private readonly ContentControl _pageHost = new();
    private readonly IWorkerService _worker;
    private readonly TaskQueue _tasks = new();
    private ToolMode _workbenchMode = ToolMode.ConvertAndSeparate;
    private readonly List<StackPanel> _taskLists = [];
    private TextBlock? _engineStatus;
    private string _separationKeep = "instrumental";
    private string _downloadSource = "auto";
    private ComboBox? _modelStorageCombo;
    private TextBlock? _modelRootLabel;
    private string _modelRootPath = "";
    private bool _syncingModelStorage;
    private readonly List<VoiceTemplate> _voices = [];
    private readonly Dictionary<string, string> _voiceFolders = [];
    private string? _selectedVoiceId;
    private CancellationTokenSource? _operationCancellation;
    private readonly Dictionary<string, TextBlock> _modelStateLabels = [];
    private readonly Dictionary<string, ProgressBar> _modelProgressBars = [];
    private readonly Dictionary<string, TextBlock> _modelProgressLabels = [];
    private readonly Dictionary<string, Button> _modelDownloadButtons = [];
    private readonly Dictionary<string, Button> _modelLinkButtons = [];
    private readonly Dictionary<string, TextBlock> _runtimeStateLabels = [];
    private readonly Dictionary<string, Button> _runtimeInstallButtons = [];
    private Border? _environmentInstallNotice;
    private Border? _workbenchInstallNotice;
    private string _lastDoctorJson = "";
    private string _lastModelStatusJson = "";

    private sealed class FileSelectionState
    {
        public List<string> Files { get; } = [];
        public TextBlock Label { get; } = new() { Text = "尚未选择文件", Foreground = MutedBrush };
    }

    private sealed class OutputSelectionState
    {
        public string Mode { get; set; } = "default";
        public string? CustomDirectory { get; set; }
    }

    private static readonly SolidColorBrush CardBrush = new(ColorHelper.FromArgb(255, 255, 255, 255));
    private static readonly SolidColorBrush BorderBrush = new(ColorHelper.FromArgb(255, 225, 230, 239));
    private static readonly SolidColorBrush MutedBrush = new(ColorHelper.FromArgb(255, 105, 115, 134));
    private static readonly SolidColorBrush AccentBrush = new(ColorHelper.FromArgb(255, 102, 112, 246));
    private static readonly SolidColorBrush SuccessBrush = new(ColorHelper.FromArgb(255, 39, 142, 73));
    private static readonly SolidColorBrush SidebarBrush = new(ColorHelper.FromArgb(255, 250, 251, 253));
    private static readonly SolidColorBrush SoftAccentBrush = new(ColorHelper.FromArgb(255, 241, 243, 255));
    private static readonly SolidColorBrush TextBrush = new(ColorHelper.FromArgb(255, 31, 41, 55));

    public MainWindow()
    {
        InitializeComponent();
        _navigation = NavigationRoot;
        _worker = CreateWorker();
        if (_worker is ProcessWorkerService processWorker)
            processWorker.Progress += (_, progress) => DispatcherQueue.TryEnqueue(() => ShowProgress(progress));
        Title = "SPP Audio Studio";
        ConfigureWindow();
        BuildShell();
        _ = RefreshWorkerStatusAsync();
    }

    private static IWorkerService CreateWorker()
    {
        try { return ProcessWorkerService.CreateDefault(); }
        catch (WorkerUnavailableException error) { return new MissingWorkerService(error.Message); }
    }

    private void ConfigureWindow()
    {
        var hwnd = WindowNative.GetWindowHandle(this);
        var id = Win32Interop.GetWindowIdFromWindow(hwnd);
        _appWindow = AppWindow.GetFromWindowId(id);
        _appWindow.Resize(new global::Windows.Graphics.SizeInt32(1320, 1440));
        _appWindow.SetIcon(IOPath.Combine(AppContext.BaseDirectory, "Assets", "pixel_icon.ico"));
        _appWindow.Changed += (_, args) =>
        {
            if (!args.DidSizeChange || _appWindow is null) return;
            var size = _appWindow.Size;
            if (size.Width < 1040 || size.Height < 840)
                _appWindow.Resize(new global::Windows.Graphics.SizeInt32(Math.Max(1040, size.Width), Math.Max(840, size.Height)));
        };
    }

    private void BuildShell()
    {
        _navigation.Children.Clear();
        _navigation.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(215) });
        _navigation.ColumnDefinitions.Add(new ColumnDefinition());
        var pane = new StackPanel { Spacing = 6, Padding = new Thickness(12), Background = SidebarBrush };
        pane.Children.Add(BrandHeader());
        foreach (var (text, tag) in new[] { ("音频工作台", "workbench"), ("格式转换", "convert"), ("人声分离", "separate"), ("声音克隆", "clone"), ("模型与环境", "environment") })
        {
            var button = new Button
            {
                Content = text,
                HorizontalAlignment = HorizontalAlignment.Stretch,
                HorizontalContentAlignment = HorizontalAlignment.Left,
                Tag = tag,
                Padding = new Thickness(14, 10, 14, 10),
                CornerRadius = new CornerRadius(9)
            };
            button.Click += (_, _) => ShowPage(tag);
            _navButtons[tag] = button;
            pane.Children.Add(button);
        }
        pane.Children.Add(SidebarGuideCard(
            "首次使用",
            "请先进入「模型与环境」下载并检查 Qwen、Mel 与 Whisper。模型和运行环境默认保存在本机 App 数据目录。"));
        pane.Children.Add(SidebarGuideCard(
            "免责声明",
            "请确保你拥有音频与声音的使用授权，并自行确认第三方模型许可及生成内容的合规性。"));
        Grid.SetColumn(pane, 0);
        _navigation.Children.Add(pane);
        _pageHost.HorizontalAlignment = HorizontalAlignment.Stretch;
        _pageHost.VerticalAlignment = VerticalAlignment.Stretch;
        Grid.SetColumn(_pageHost, 1);
        _navigation.Children.Add(_pageHost);
        ShowPage("workbench");
    }

    private static UIElement BrandHeader()
    {
        var panel = new StackPanel
        {
            Spacing = 8,
            Margin = new Thickness(10, 18, 10, 20),
            HorizontalAlignment = HorizontalAlignment.Center
        };
        panel.Children.Add(new Image
        {
            Width = 72,
            Height = 72,
            HorizontalAlignment = HorizontalAlignment.Center,
            Stretch = Stretch.Uniform,
            Source = new BitmapImage(new Uri("ms-appx:///Assets/pixel_icon.png"))
        });
        panel.Children.Add(new TextBlock
        {
            Text = "SPP Audio Studio",
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            FontSize = 15,
            HorizontalAlignment = HorizontalAlignment.Center
        });
        panel.Children.Add(new TextBlock
        {
            Text = "Windows Preview",
            Foreground = MutedBrush,
            FontSize = 11,
            HorizontalAlignment = HorizontalAlignment.Center
        });
        return panel;
    }

    private static Border SidebarGuideCard(string title, string detail)
    {
        var body = new StackPanel { Spacing = 4 };
        body.Children.Add(new TextBlock { Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 12 });
        body.Children.Add(new TextBlock { Text = detail, Foreground = MutedBrush, FontSize = 11, TextWrapping = TextWrapping.Wrap, LineHeight = 17 });
        return new Border
        {
            Child = body,
            Padding = new Thickness(12),
            Margin = new Thickness(0, 10, 0, 0),
            CornerRadius = new CornerRadius(10),
            Background = new SolidColorBrush(ColorHelper.FromArgb(255, 255, 255, 255)),
            BorderBrush = BorderBrush,
            BorderThickness = new Thickness(1)
        };
    }

    private static NavigationViewItem MenuItem(string glyph, string text, string tag) => new()
    {
        Content = text,
        Tag = tag,
        Icon = new FontIcon { Glyph = glyph, FontFamily = new FontFamily("Segoe Fluent Icons") }
    };

    private void NavigationOnSelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.SelectedItemContainer?.Tag is string tag) ShowPage(tag);
    }

    private void ShowPage(string tag)
    {
        if (!_pageCache.TryGetValue(tag, out var viewer))
        {
            var page = tag switch
            {
                "convert" => BuildFileToolPage(ToolMode.Convert, "格式转换", "特殊格式 → 原始 MP3 / FLAC"),
                "separate" => BuildFileToolPage(ToolMode.Separate, "人声分离", "Mel-Deux · 去人声 / 提取人声 / 双轨输出"),
                "clone" => BuildClonePage(),
                "environment" => BuildEnvironmentPage(),
                _ => BuildWorkbenchPage()
            };
            viewer = new ScrollViewer
            {
                HorizontalScrollMode = ScrollMode.Disabled,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                Content = page
            };
            _pageCache[tag] = viewer;
        }

        _pageHost.Content = viewer;
        UpdateNavigationSelection(tag);
    }

    private void UpdateNavigationSelection(string selectedTag)
    {
        foreach (var (tag, button) in _navButtons)
        {
            var selected = tag == selectedTag;
            button.Background = selected ? SoftAccentBrush : new SolidColorBrush(Colors.Transparent);
            button.Foreground = selected ? AccentBrush : TextBrush;
            button.FontWeight = selected ? Microsoft.UI.Text.FontWeights.SemiBold : Microsoft.UI.Text.FontWeights.Normal;
            button.BorderThickness = new Thickness(0);
        }
    }

    private StackPanel Page(string title, string subtitle)
    {
        var page = new StackPanel { Spacing = 16, Padding = new Thickness(24, 22, 24, 22), HorizontalAlignment = HorizontalAlignment.Stretch };
        page.Children.Add(Header(title, subtitle));
        return page;
    }

    private static StackPanel Header(string title, string subtitle)
    {
        var header = new StackPanel { Spacing = 5 };
        header.Children.Add(new TextBlock { Text = title, FontSize = 28, FontWeight = Microsoft.UI.Text.FontWeights.Bold });
        header.Children.Add(new TextBlock { Text = subtitle, Foreground = MutedBrush, TextWrapping = TextWrapping.Wrap });
        return header;
    }

    private StackPanel BuildWorkbenchPage()
    {
        var selection = new FileSelectionState();
        var output = new OutputSelectionState();
        var page = Page("音频工作台", "转换音乐、人声分离、克隆声音。常用操作尽量一处完成。");
        _workbenchInstallNotice = NoticeCard("首次使用：安装模型和运行环境", "声音克隆需要 Qwen；人声分离需要 Mel。状态来自本机 Worker 实时检查。", "前往安装", () => SelectMenu("environment"));
        _workbenchInstallNotice.Visibility = Visibility.Collapsed;
        page.Children.Add(_workbenchInstallNotice);

        var modes = new ComboBox { Header = "处理模式", Width = 520, HorizontalAlignment = HorizontalAlignment.Left, SelectedIndex = 2 };
        modes.Items.Add("仅转换"); modes.Items.Add("仅分离"); modes.Items.Add("转换 + 分离");
        modes.SelectionChanged += (_, _) => _workbenchMode = modes.SelectedIndex switch { 0 => ToolMode.Convert, 1 => ToolMode.Separate, _ => ToolMode.ConvertAndSeparate };
        page.Children.Add(modes);
        page.Children.Add(SegmentedOptions("分离输出", ["仅伴奏（去人声）", "仅人声", "人声 + 伴奏"]));
        page.Children.Add(OutputCard(@"SPP Audio Studio\out\Music Separation", output));
        page.Children.Add(DropZone("把特殊格式 / FLAC / MP3 / WAV / M4A 拖到这里", "特殊格式会自动先转换，再进入 Mel-Deux；普通音频直接分离。", () => _workbenchMode, selection));

        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        actions.Children.Add(ActionButton("选择文件…", async () => await PickFilesAsync(_workbenchMode, selection)));
        actions.Children.Add(PrimaryButton("开始处理", () => _ = StartSelectedTasksAsync(_workbenchMode, selection, output)));
        actions.Children.Add(ActionButton("取消", CancelCurrentOperation));
        actions.Children.Add(ActionButton("清空列表", () => { selection.Files.Clear(); UpdateSelectionLabel(selection); }));
        _engineStatus = new TextBlock { Text = "环境需检查", Foreground = new SolidColorBrush(Colors.Orange), VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(16, 0, 0, 0) };
        actions.Children.Add(_engineStatus);
        page.Children.Add(actions);
        page.Children.Add(selection.Label);
        page.Children.Add(new Rectangle { Height = 1, Fill = BorderBrush, HorizontalAlignment = HorizontalAlignment.Stretch });
        page.Children.Add(SectionTitle("任务"));
        var taskList = new StackPanel { Spacing = 8 };
        _taskLists.Add(taskList);
        RenderTasks();
        page.Children.Add(taskList);
        return page;
    }

    private StackPanel BuildFileToolPage(ToolMode mode, string title, string subtitle)
    {
        var selection = new FileSelectionState();
        var output = new OutputSelectionState();
        var page = Page(title, subtitle);
        if (mode == ToolMode.Separate) page.Children.Add(SegmentedOptions("保留内容", ["仅伴奏（去人声）", "仅人声", "人声 + 伴奏"]));
        page.Children.Add(OutputCard(@"SPP Audio Studio\out\Music Separation", output));
        page.Children.Add(DropZone("把文件拖到这里", mode == ToolMode.Convert ? "支持批量特殊格式文件" : "支持 MP3 / FLAC / WAV / M4A 等音频", () => mode, selection));
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        actions.Children.Add(ActionButton("选择文件…", async () => await PickFilesAsync(mode, selection)));
        actions.Children.Add(PrimaryButton(mode == ToolMode.Convert ? "仅转换" : "仅分离", () => _ = StartSelectedTasksAsync(mode, selection, output)));
        actions.Children.Add(ActionButton("取消", CancelCurrentOperation));
        actions.Children.Add(ActionButton("清空", () => { selection.Files.Clear(); UpdateSelectionLabel(selection); }));
        page.Children.Add(actions);
        page.Children.Add(selection.Label);
        var taskList = new StackPanel { Spacing = 8 };
        _taskLists.Add(taskList);
        RenderTasks();
        page.Children.Add(taskList);
        return page;
    }

    private StackPanel BuildClonePage()
    {
        var output = new OutputSelectionState();
        var page = Page("声音克隆", "常用人声保存一次，以后选模板、粘文案、直接生成。");
        var top = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        top.Children.Add(SectionTitle("常用人声"));
        var cards = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        top.Children.Add(PrimaryButton("＋ 新建人声模板", () => _ = ShowVoiceDialogAsync(cards)));
        page.Children.Add(top);
        page.Children.Add(new ScrollViewer { HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, Content = cards });
        page.Children.Add(InfoCard("参考音建议", "推荐 5–15 秒、单人清晰说话、少背景音乐和回声。参考文本应与音频一致；安装 Whisper 后可留空自动转写。"));
        page.Children.Add(SectionTitle("生成文案"));
        var script = new TextBox
        {
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinHeight = 180,
            PlaceholderText = "输入要生成的文案",
            Text = "大家好，我是宋盼盼。这是一段 SPP Audio Studio 的声音克隆测试。如果你能自然地听到这句话，说明人声模板、模型和本地推理都已经正常工作。"
        };
        page.Children.Add(script);
        page.Children.Add(OutputCard(@"SPP Audio Studio\out\Clone", output));
        var status = new TextBlock { Foreground = MutedBrush, VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap };
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        actions.Children.Add(PrimaryButton("生成声音", () => _ = CloneVoiceAsync(script.Text, status, output)));
        actions.Children.Add(ActionButton("取消", CancelCurrentOperation));
        actions.Children.Add(status);
        page.Children.Add(actions);
        _ = RefreshVoicesAsync(cards, status);
        return page;
    }

    private async Task RefreshVoicesAsync(StackPanel cards, TextBlock status)
    {
        try
        {
            await _worker.ExecuteAsync(new WorkerCommand("seed-default-voice", []));
            var json = await _worker.ExecuteAsync(new WorkerCommand("voice-list", []));
            using var document = JsonDocument.Parse(json);
            _voices.Clear();
            _voiceFolders.Clear();
            cards.Children.Clear();
            foreach (var item in document.RootElement.GetProperty("templates").EnumerateArray())
            {
                var voice = new VoiceTemplate(
                    item.GetProperty("id").GetString()!, item.GetProperty("name").GetString()!,
                    item.TryGetProperty("reference_text", out var text) ? text.GetString() ?? "" : "",
                    item.TryGetProperty("note", out var note) ? note.GetString() ?? "" : "",
                    item.TryGetProperty("default", out var value) && value.GetBoolean());
                _voices.Add(voice);
                if (item.TryGetProperty("folder", out var folder) && folder.ValueKind == JsonValueKind.String)
                    _voiceFolders[voice.Id] = folder.GetString() ?? "";
                if (voice.IsDefault || _selectedVoiceId is null) _selectedVoiceId = voice.Id;
                var button = new Button { Content = $"{(voice.IsDefault ? "★ " : "")}{voice.Name}\n{voice.Note}", MinWidth = 190, MinHeight = 66 };
                button.Click += (_, _) => { _selectedVoiceId = voice.Id; status.Text = $"当前模板：{voice.Name}"; };
                var voicePanel = new StackPanel { Spacing = 5 };
                voicePanel.Children.Add(button);
                if (!voice.IsDefault)
                    voicePanel.Children.Add(ActionButton("设为默认", () => _ = SetDefaultVoiceAsync(voice.Id, cards, status)));
                cards.Children.Add(voicePanel);
            }
            status.Text = _voices.Count == 0 ? "尚无人声模板，请先新建。" : $"已加载 {_voices.Count} 个人声模板";
        }
        catch (Exception error) { status.Text = error.Message; }
    }

    private async Task CloneVoiceAsync(string script, TextBlock status, OutputSelectionState output)
    {
        var templateFolder = _selectedVoiceId is not null && _voiceFolders.TryGetValue(_selectedVoiceId, out var folder)
            ? folder
            : null;
        var outputDirectory = ResolveOutputDirectory(output, templateFolder);
        var validation = new VoiceCloneRequest(_selectedVoiceId, null, script, outputDirectory).Validate();
        if (validation is not null) { status.Text = validation; return; }
        _operationCancellation = new CancellationTokenSource();
        status.Text = "正在生成…";
        try
        {
            var args = new List<string> { _selectedVoiceId!, "--text", script };
            if (!string.IsNullOrWhiteSpace(outputDirectory)) { args.Add("--output-dir"); args.Add(outputDirectory); }
            var json = await RunWorkerAsync("voice-clone", args, _operationCancellation.Token);
            var resultPath = ReadOutput(json);
            status.Text = resultPath is null ? "生成完成" : $"生成完成：{resultPath}";
        }
        catch (OperationCanceledException) { status.Text = "已取消"; }
        catch (Exception error) { status.Text = error.Message; }
    }

    private async Task SetDefaultVoiceAsync(string id, StackPanel cards, TextBlock status)
    {
        try
        {
            await _worker.ExecuteAsync(new WorkerCommand("voice-default", [id]));
            _selectedVoiceId = id;
            await RefreshVoicesAsync(cards, status);
        }
        catch (Exception error) { status.Text = error.Message; }
    }

    private StackPanel BuildEnvironmentPage()
    {
        _modelStateLabels.Clear();
        _modelDownloadButtons.Clear();
        _modelLinkButtons.Clear();
        _runtimeStateLabels.Clear();
        _runtimeInstallButtons.Clear();
        var page = Page("模型与环境", "本机模型与运行环境");
        var status = new TextBlock { Text = "正在检查本机 AI 环境…", Foreground = MutedBrush, TextWrapping = TextWrapping.Wrap };
        _environmentInstallNotice = NoticeCard("本机 AI 环境", "检测到缺失组件时，可在这里一键补齐。", "安装缺失组件", () => _ = InstallAllAsync(status));
        _environmentInstallNotice.Visibility = Visibility.Collapsed;
        page.Children.Add(_environmentInstallNotice);
        page.Children.Add(StatusStrip(status));
        page.Children.Add(ModelStorageCard(status));
        var grid = new Grid { ColumnSpacing = 14 };
        grid.ColumnDefinitions.Add(new ColumnDefinition()); grid.ColumnDefinitions.Add(new ColumnDefinition());
        var qwen = ModelCard("qwen", "Qwen3-TTS 1.7B", "声音克隆 · 本地 CUDA", "约 4.5 GB", "Apache-2.0", status);
        var mel = ModelCard("mel", "Mel-Deux", "人声 / 伴奏分离", "约 435 MB", "CC BY-NC 4.0", status);
        Grid.SetColumn(mel, 1); grid.Children.Add(qwen); grid.Children.Add(mel); page.Children.Add(grid);
        page.Children.Add(ModelCard("whisper", "Whisper large-v3-turbo", "参考音自动转写 · 可选", "约 1.6 GB", "模型条款见 Hugging Face", status));
        page.Children.Add(RuntimeCard(status));
        page.Children.Add(DiagnosticsCard());
        _ = RefreshEnvironmentAsync(status);
        return page;
    }

    private Border StatusStrip(TextBlock status)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        row.Children.Add(new FontIcon { Glyph = "", Foreground = new SolidColorBrush(Colors.Orange) });
        row.Children.Add(new TextBlock { Text = "下载源", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center });
        var source = new ComboBox { Width = 210, SelectedIndex = _downloadSource switch { "mirror" => 1, "official" => 2, _ => 0 } };
        source.Items.Add("自动（镜像优先）"); source.Items.Add("HF 镜像"); source.Items.Add("Hugging Face 官方");
        source.SelectionChanged += (_, _) => _ = SetDownloadSourceAsync(source.SelectedIndex switch { 1 => "mirror", 2 => "official", _ => "auto" }, status);
        row.Children.Add(source);
        row.Children.Add(ActionButton("刷新检查", () => _ = RefreshEnvironmentAsync(status)));
        row.Children.Add(ActionButton("取消当前操作", CancelCurrentOperation));
        return Card(row, 16);
    }

    private Border ModelStorageCard(TextBlock status)
    {
        var body = new StackPanel { Spacing = 8 };
        body.Children.Add(SectionTitle("模型存储位置"));
        body.Children.Add(new TextBlock
        {
            Text = "可选择 C 盘 AppData，或把模型放在软件根目录的 Models 文件夹。切换位置不会自动搬动已有模型。",
            Foreground = MutedBrush,
            FontSize = 12,
            TextWrapping = TextWrapping.Wrap
        });

        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        _modelStorageCombo = new ComboBox { Width = 260, SelectedIndex = -1 };
        _modelStorageCombo.Items.Add("C 盘 AppData（默认）");
        _modelStorageCombo.Items.Add(@"软件目录\Models（便携）");
        _modelStorageCombo.SelectionChanged += (_, _) =>
        {
            if (_syncingModelStorage || _modelStorageCombo.SelectedIndex < 0) return;
            _ = SetModelStorageAsync(_modelStorageCombo.SelectedIndex == 1 ? "portable" : "appdata", status);
        };
        row.Children.Add(_modelStorageCombo);
        row.Children.Add(ActionButton("打开模型目录", OpenModelDirectory));
        body.Children.Add(row);

        _modelRootLabel = new TextBlock { Text = "正在读取模型目录…", Foreground = MutedBrush, FontSize = 11, TextWrapping = TextWrapping.Wrap };
        body.Children.Add(_modelRootLabel);
        return Card(body, 14);
    }

    private async Task SetModelStorageAsync(string storage, TextBlock status)
    {
        status.Text = "正在切换模型存储位置…";
        try
        {
            await _worker.ExecuteAsync(new WorkerCommand("model-storage", [storage]));
            await RefreshEnvironmentAsync(status);
        }
        catch (Exception error) { status.Text = "切换失败：" + error.Message; }
    }

    private void OpenModelDirectory()
    {
        if (string.IsNullOrWhiteSpace(_modelRootPath)) return;
        try
        {
            System.IO.Directory.CreateDirectory(_modelRootPath);
            Process.Start(new ProcessStartInfo
            {
                FileName = "explorer.exe",
                Arguments = $"\"{_modelRootPath}\"",
                UseShellExecute = true
            });
        }
        catch { }
    }

    private Border ModelCard(string key, string title, string subtitle, string size, string license, TextBlock status)
    {
        var body = new StackPanel { Spacing = 9 };
        var heading = new Grid();
        heading.ColumnDefinitions.Add(new ColumnDefinition()); heading.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        heading.Children.Add(new TextBlock { Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 16 });
        var state = new TextBlock { Text = "检查中…", Foreground = MutedBrush, VerticalAlignment = VerticalAlignment.Center };
        Grid.SetColumn(state, 1); heading.Children.Add(state); _modelStateLabels[key] = state;
        body.Children.Add(heading);
        body.Children.Add(new TextBlock { Text = subtitle, Foreground = MutedBrush, FontSize = 12 });
        body.Children.Add(new TextBlock { Text = $"▣ {size}    {license}", Foreground = MutedBrush, FontSize = 12 });
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 9 };
        var download = PrimaryButton("下载模型", () => _ = RunEnvironmentCommandAsync("model-download", [key, "--source", _downloadSource], status));
        download.Visibility = Visibility.Collapsed;
        _modelDownloadButtons[key] = download; buttons.Children.Add(download);
        var link = ActionButton("链接本地…", () => _ = LinkModelAsync(key, status));
        link.Visibility = Visibility.Collapsed;
        _modelLinkButtons[key] = link;
        buttons.Children.Add(link);
        body.Children.Add(buttons);

        var progress = new ProgressBar
        {
            Minimum = 0,
            Maximum = 100,
            Height = 4,
            Visibility = Visibility.Collapsed
        };
        var progressLabel = new TextBlock
        {
            Foreground = MutedBrush,
            FontSize = 11,
            TextWrapping = TextWrapping.Wrap,
            Visibility = Visibility.Collapsed
        };
        _modelProgressBars[key] = progress;
        _modelProgressLabels[key] = progressLabel;
        body.Children.Add(progress);
        body.Children.Add(progressLabel);
        return Card(body, 16);
    }

    private Border RuntimeCard(TextBlock status)
    {
        var body = new StackPanel { Spacing = 12 };
        body.Children.Add(SectionTitle("推理运行环境"));
        body.Children.Add(new TextBlock { Text = "运行组件安装到 App 数据目录，不修改系统 Python。", Foreground = MutedBrush });
        body.Children.Add(RuntimeRow("Qwen Runtime", "qwen_python", status));
        body.Children.Add(RuntimeRow("Mel Separator", "separator", status));
        body.Children.Add(RuntimeRow("Whisper ASR（可选）", "asr_python", status));
        return Card(body, 16);
    }

    private StackPanel RuntimeRow(string label, string key, TextBlock status)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        row.Children.Add(new TextBlock { Text = label, Width = 420, VerticalAlignment = VerticalAlignment.Center });
        var state = new TextBlock { Text = "检查中…", Foreground = MutedBrush, Width = 90, VerticalAlignment = VerticalAlignment.Center };
        _runtimeStateLabels[key] = state; row.Children.Add(state);
        var runtimeCommand = key switch { "qwen_python" => "qwen", "separator" => "mel", "asr_python" => "asr", _ => key };
        var install = ActionButton("安装", () => _ = RunEnvironmentCommandAsync("runtime-install", [runtimeCommand, "--source", _downloadSource], status));
        install.Visibility = Visibility.Collapsed;
        _runtimeInstallButtons[key] = install;
        row.Children.Add(install);
        return row;
    }

    private async Task RefreshEnvironmentAsync(TextBlock status)
    {
        try
        {
            var doctor = await _worker.ExecuteAsync(new WorkerCommand("doctor", []));
            _lastDoctorJson = doctor;
            var models = await _worker.ExecuteAsync(new WorkerCommand("model-status", []));
            _lastModelStatusJson = models;
            using var doctorDocument = JsonDocument.Parse(doctor);
            using var modelDocument = JsonDocument.Parse(models);
            if (modelDocument.RootElement.TryGetProperty("download_source", out var source))
                _downloadSource = source.GetString() ?? "auto";

            var storage = modelDocument.RootElement.TryGetProperty("model_storage", out var storageValue)
                ? storageValue.GetString() ?? "appdata"
                : "appdata";
            _modelRootPath = modelDocument.RootElement.TryGetProperty("model_root", out var rootValue)
                ? rootValue.GetString() ?? ""
                : "";
            if (_modelRootLabel is not null)
                _modelRootLabel.Text = string.IsNullOrWhiteSpace(_modelRootPath) ? "模型目录不可用" : _modelRootPath;
            if (_modelStorageCombo is not null)
            {
                _syncingModelStorage = true;
                _modelStorageCombo.SelectedIndex = storage == "portable" ? 1 : 0;
                _syncingModelStorage = false;
            }

            UpdateEnvironmentIndicators(modelDocument.RootElement, doctorDocument.RootElement);
            status.Text = "本机 AI 环境已检查";
        }
        catch (Exception error) { status.Text = "检查失败：" + error.Message; }
    }

    private void UpdateEnvironmentIndicators(JsonElement modelStatus, JsonElement doctor)
    {
        var checks = doctor.TryGetProperty("checks", out var doctorChecks) ? doctorChecks : default;
        bool Check(string key) => checks.ValueKind == JsonValueKind.Object
            && checks.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.True;
        if (modelStatus.TryGetProperty("models", out var models))
        {
            foreach (var (key, statusKey) in new[] { ("qwen", "qwen"), ("mel", "mel_deux"), ("whisper", "whisper") })
            {
                var installed = models.TryGetProperty(statusKey, out var item)
                    && item.TryGetProperty("installed", out var value) && value.GetBoolean();
                var healthy = key switch
                {
                    "qwen" => Check("qwen_model") && Check("qwen_bridge"),
                    "mel" => Check("mel_model") && Check("mel_config"),
                    "whisper" => Check("asr_optional"),
                    _ => false
                };
                var ready = installed && healthy;
                if (_modelStateLabels.TryGetValue(key, out var label))
                {
                    label.Text = ready ? "✓ 已就绪" : installed ? "需要修复" : "未下载";
                    label.Foreground = ready ? SuccessBrush : new SolidColorBrush(Colors.Orange);
                }
                if (_modelDownloadButtons.TryGetValue(key, out var button))
                {
                    button.Content = installed ? "修复模型" : "下载模型";
                    button.IsEnabled = true;
                    button.Visibility = ready ? Visibility.Collapsed : Visibility.Visible;
                }
                if (_modelLinkButtons.TryGetValue(key, out var link))
                    link.Visibility = ready ? Visibility.Collapsed : Visibility.Visible;
                if (ready)
                {
                    if (_modelProgressBars.TryGetValue(key, out var progress))
                        progress.Visibility = Visibility.Collapsed;
                    if (_modelProgressLabels.TryGetValue(key, out var progressLabel))
                        progressLabel.Visibility = Visibility.Collapsed;
                }
            }
        }
        if (modelStatus.TryGetProperty("runtime", out var runtime))
        {
            foreach (var key in new[] { "qwen_python", "separator", "asr_python" })
            {
                if (!_runtimeStateLabels.TryGetValue(key, out var label)) continue;
                var installed = runtime.TryGetProperty(key, out var item)
                    && item.TryGetProperty("installed", out var value) && value.GetBoolean();
                var healthy = key switch
                {
                    "qwen_python" => Check("qwen_runtime") && Check("qwen_bridge"),
                    "separator" => Check("mel_runtime") && Check("mel_ffmpeg"),
                    "asr_python" => Check("asr_optional"),
                    _ => false
                };
                var ready = installed && healthy;
                label.Text = ready ? "✓ 已就绪" : installed ? "需要修复" : "未安装";
                label.Foreground = ready ? SuccessBrush : new SolidColorBrush(Colors.Orange);
                if (_runtimeInstallButtons.TryGetValue(key, out var button))
                    button.Visibility = ready ? Visibility.Collapsed : Visibility.Visible;
            }
        }
        var allReady = _modelStateLabels.Values.All(label => label.Text.StartsWith("✓"))
            && _runtimeStateLabels.Values.All(label => label.Text.StartsWith("✓"));
        if (_environmentInstallNotice is not null)
            _environmentInstallNotice.Visibility = allReady ? Visibility.Collapsed : Visibility.Visible;
    }

    private string BuildDiagnosticsText() =>
        $"SPP Audio Studio Windows 诊断\n\nDoctor\n{PrettyJsonOrUnavailable(_lastDoctorJson)}\n\n模型与运行环境\n{PrettyJsonOrUnavailable(_lastModelStatusJson)}";

    private void CopyDiagnostics()
    {
        var package = new DataPackage();
        package.SetText(BuildDiagnosticsText());
        Clipboard.SetContent(package);
        Clipboard.Flush();
    }

    private void ShowDiagnostics()
    {
        var dialog = new ContentDialog
        {
            Title = "诊断详情",
            Content = new ScrollViewer { MaxHeight = 520, Content = new TextBlock { Text = BuildDiagnosticsText(), FontFamily = new FontFamily("Consolas"), TextWrapping = TextWrapping.Wrap } },
            PrimaryButtonText = "复制诊断",
            CloseButtonText = "关闭",
            XamlRoot = Content.XamlRoot
        };
        dialog.PrimaryButtonClick += (_, _) => CopyDiagnostics();
        _ = dialog.ShowAsync();
    }

    private async Task SetDownloadSourceAsync(string source, TextBlock status)
    {
        _downloadSource = source;
        await RunEnvironmentCommandAsync("download-source", [source], status, false);
    }

    private async Task RunEnvironmentCommandAsync(string command, IReadOnlyList<string> arguments, TextBlock status, bool refreshEnvironment = true)
    {
        _operationCancellation?.Cancel();
        _operationCancellation = new CancellationTokenSource();
        status.Text = $"正在执行 {command}…";
        var modelKey = command == "model-download" && arguments.Count > 0 ? arguments[0] : null;
        try
        {
            var json = await RunWorkerAsync(command, arguments, _operationCancellation.Token);
            status.Text = PrettyJson(json);
            await RefreshWorkerStatusAsync();
            if (refreshEnvironment) await RefreshEnvironmentAsync(status);
        }
        catch (OperationCanceledException)
        {
            status.Text = "已取消";
            if (modelKey is not null) SetModelProgressFailed(modelKey, "已取消");
        }
        catch (Exception error)
        {
            status.Text = error.Message;
            if (modelKey is not null) SetModelProgressFailed(modelKey, "下载失败");
        }
        finally
        {
            if (modelKey is not null && _modelDownloadButtons.TryGetValue(modelKey, out var button))
                button.IsEnabled = true;
        }
    }

    private async Task InstallAllAsync(TextBlock status)
    {
        foreach (var (command, component) in new[] {
            ("runtime-install", "qwen"), ("model-download", "qwen"),
            ("runtime-install", "mel"), ("model-download", "mel"),
            ("runtime-install", "asr"), ("model-download", "whisper") })
        {
            await RunEnvironmentCommandAsync(command, [component, "--source", _downloadSource], status, false);
            if (_operationCancellation?.IsCancellationRequested == true) break;
        }
        await RefreshEnvironmentAsync(status);
    }

    private async Task LinkModelAsync(string key, TextBlock status)
    {
        var picker = new FolderPicker { SuggestedStartLocation = PickerLocationId.Downloads };
        picker.FileTypeFilter.Add("*");
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        var folder = await picker.PickSingleFolderAsync();
        if (folder is null) return;
        var flag = key switch { "qwen" => "--qwen-model-dir", "mel" => "--mel-model-dir", _ => "--asr-model" };
        await RunEnvironmentCommandAsync("model-link", [flag, folder.Path], status);
        await RefreshEnvironmentAsync(status);
    }

    private static string PrettyJson(string json)
    {
        using var document = JsonDocument.Parse(json);
        return JsonSerializer.Serialize(document.RootElement, new JsonSerializerOptions { WriteIndented = true });
    }

    private static string PrettyJsonOrUnavailable(string json)
    {
        if (string.IsNullOrWhiteSpace(json)) return "尚无诊断数据，请先刷新检查。";
        try { return PrettyJson(json); }
        catch (JsonException) { return json; }
    }

    private void ShowProgress(WorkerProgress progress)
    {
        if (_engineStatus is not null) _engineStatus.Text = $"处理中：{progress.Event}";
        if (progress.Event is not ("model_begin" or "download_start" or "download_progress" or "download_done" or "model_done"))
            return;

        using var document = JsonDocument.Parse(progress.Json);
        var root = document.RootElement;
        if (!root.TryGetProperty("model", out var modelValue)) return;
        var model = modelValue.GetString();
        if (string.IsNullOrWhiteSpace(model)) return;

        if (_modelDownloadButtons.TryGetValue(model, out var button))
            button.IsEnabled = progress.Event == "model_done";

        if (!_modelProgressBars.TryGetValue(model, out var bar)
            || !_modelProgressLabels.TryGetValue(model, out var label))
            return;

        bar.Visibility = Visibility.Visible;
        label.Visibility = Visibility.Visible;

        if (progress.Event == "model_begin")
        {
            bar.Value = 0;
            label.Text = "准备下载…";
            if (_modelStateLabels.TryGetValue(model, out var state)) state.Text = "准备下载…";
            return;
        }

        if (progress.Event == "download_progress")
        {
            var percent = root.TryGetProperty("percent", out var percentValue) ? percentValue.GetDouble() : 0;
            var completed = root.TryGetProperty("completed_bytes", out var completedValue) ? completedValue.GetInt64() : 0;
            var total = root.TryGetProperty("total_bytes", out var totalValue) ? totalValue.GetInt64() : 0;
            var file = root.TryGetProperty("file", out var fileValue) ? IOPath.GetFileName(fileValue.GetString()) : "";
            bar.Value = Math.Clamp(percent, 0, 100);
            label.Text = $"{percent:0.0}%  ·  {FormatBytes(completed)} / {FormatBytes(total)}" +
                         (string.IsNullOrWhiteSpace(file) ? "" : $"  ·  {file}");
            if (_modelStateLabels.TryGetValue(model, out var state)) state.Text = $"下载中 {percent:0}%";
            return;
        }

        if (progress.Event == "model_done")
        {
            bar.Value = 100;
            label.Text = "100% · 下载完成，正在检查…";
            if (_modelStateLabels.TryGetValue(model, out var state)) state.Text = "下载完成";
        }
    }

    private void SetModelProgressFailed(string model, string message)
    {
        if (_modelStateLabels.TryGetValue(model, out var state)) state.Text = message;
        if (_modelProgressLabels.TryGetValue(model, out var label))
        {
            label.Text = message;
            label.Visibility = Visibility.Visible;
        }
        if (_modelProgressBars.TryGetValue(model, out var bar))
            bar.Visibility = Visibility.Collapsed;
    }

    private static string FormatBytes(long bytes)
    {
        if (bytes <= 0) return "0 B";
        string[] units = ["B", "KB", "MB", "GB", "TB"];
        var value = (double)bytes;
        var index = 0;
        while (value >= 1024 && index < units.Length - 1)
        {
            value /= 1024;
            index++;
        }
        return $"{value:0.#} {units[index]}";
    }

    private Border DropZone(string title, string subtitle, Func<ToolMode> mode, FileSelectionState selection)
    {
        var body = new StackPanel { Spacing = 9, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        body.Children.Add(new FontIcon { Glyph = "", FontSize = 32, Foreground = AccentBrush });
        body.Children.Add(new TextBlock { Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, TextAlignment = TextAlignment.Center });
        body.Children.Add(new TextBlock { Text = subtitle, Foreground = MutedBrush, FontSize = 12, TextAlignment = TextAlignment.Center });
        var border = new Border
        {
            Height = 145, CornerRadius = new CornerRadius(18), BorderThickness = new Thickness(1), BorderBrush = BorderBrush,
            Background = new SolidColorBrush(ColorHelper.FromArgb(255, 248, 249, 253)), Child = body, AllowDrop = true
        };
        border.DragOver += (_, e) => { e.AcceptedOperation = DataPackageOperation.Copy; e.DragUIOverride.Caption = "添加到任务列表"; };
        border.Drop += async (_, e) => await HandleDropAsync(e, mode(), selection);
        return border;
    }

    private StackPanel SegmentedOptions(string title, string[] options)
    {
        var panel = new StackPanel { Spacing = 8, MaxWidth = 620, HorizontalAlignment = HorizontalAlignment.Left };
        panel.Children.Add(SectionTitle(title));
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 4 };
        var values = new[] { "instrumental", "vocals", "both" };
        for (var i = 0; i < options.Length; i++)
        {
            var radio = new RadioButton { Content = options[i], IsChecked = i == 0, GroupName = title };
            var value = values[Math.Min(i, values.Length - 1)];
            radio.Checked += (_, _) => _separationKeep = value;
            row.Children.Add(radio);
        }
        panel.Children.Add(row);
        return panel;
    }

    private Border OutputCard(string defaultLabel, OutputSelectionState output)
    {
        var body = new StackPanel { Spacing = 8 };
        body.Children.Add(SectionTitle("导出位置"));
        var group = $"output-{Guid.NewGuid():N}";
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 18 };
        var automatic = new RadioButton { Content = "默认目录", IsChecked = true, GroupName = group };
        var original = new RadioButton { Content = "原音频文件夹", GroupName = group };
        var custom = new RadioButton { Content = "指定文件夹", GroupName = group };
        var choose = new Button { Content = "选择…", Padding = new Thickness(12, 6, 12, 6) };
        var path = new TextBlock
        {
            Text = defaultLabel,
            Foreground = MutedBrush,
            VerticalAlignment = VerticalAlignment.Center,
            TextWrapping = TextWrapping.Wrap
        };
        automatic.Checked += (_, _) =>
        {
            output.Mode = "default";
            output.CustomDirectory = null;
            path.Text = defaultLabel;
        };
        original.Checked += (_, _) =>
        {
            output.Mode = "original";
            output.CustomDirectory = null;
            path.Text = "跟随原音频所在文件夹";
        };

        async Task ChooseCustomFolderAsync()
        {
            try
            {
                var picker = new FolderPicker { SuggestedStartLocation = PickerLocationId.MusicLibrary };
                picker.FileTypeFilter.Add("*");
                InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
                var folder = await picker.PickSingleFolderAsync();
                if (folder is null)
                {
                    if (string.IsNullOrWhiteSpace(output.CustomDirectory))
                        automatic.IsChecked = true;
                    return;
                }
                output.Mode = "custom";
                output.CustomDirectory = folder.Path;
                custom.IsChecked = true;
                path.Text = folder.Path;
            }
            catch (Exception error)
            {
                automatic.IsChecked = true;
                path.Text = "选择文件夹失败：" + error.Message;
            }
        }

        custom.Click += async (_, _) => await ChooseCustomFolderAsync();
        choose.Click += async (_, _) => await ChooseCustomFolderAsync();
        row.Children.Add(automatic);
        row.Children.Add(original);
        row.Children.Add(custom);
        row.Children.Add(choose);
        body.Children.Add(row);
        body.Children.Add(path);
        return Card(body, 14);
    }

    private static Border NoticeCard(string title, string detail, string action, Action callback)
    {
        var grid = new Grid { ColumnSpacing = 14 };
        grid.ColumnDefinitions.Add(new ColumnDefinition()); grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var copy = new StackPanel { Spacing = 4 };
        copy.Children.Add(new TextBlock { Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        copy.Children.Add(new TextBlock { Text = detail, Foreground = MutedBrush, TextWrapping = TextWrapping.Wrap });
        var button = PrimaryButton(action, callback); Grid.SetColumn(button, 1); grid.Children.Add(copy); grid.Children.Add(button);
        var card = Card(grid, 16); card.Background = SoftAccentBrush; card.BorderBrush = AccentBrush; return card;
    }

    private Border DiagnosticsCard()
    {
        var row = new Grid { ColumnSpacing = 10 };
        row.ColumnDefinitions.Add(new ColumnDefinition());
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var copy = new StackPanel { Spacing = 2, VerticalAlignment = VerticalAlignment.Center };
        copy.Children.Add(new TextBlock { Text = "诊断与日志", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        copy.Children.Add(new TextBlock { Text = "遇到问题时复制诊断发给维护者即可。", Foreground = MutedBrush, FontSize = 11 });
        row.Children.Add(copy);
        var copyButton = ActionButton("复制诊断", CopyDiagnostics); Grid.SetColumn(copyButton, 1); row.Children.Add(copyButton);
        var detailButton = ActionButton("查看详情", ShowDiagnostics); Grid.SetColumn(detailButton, 2); row.Children.Add(detailButton);
        return Card(row, 12);
    }

    private static Border InfoCard(string title, string detail)
    {
        var body = new StackPanel { Spacing = 6 };
        body.Children.Add(SectionTitle(title));
        body.Children.Add(new TextBlock { Text = detail, Foreground = MutedBrush, TextWrapping = TextWrapping.Wrap, LineHeight = 21 });
        return Card(body, 14);
    }

    private static Border VoiceCard(string name, string note, bool selected)
    {
        var body = new StackPanel { Width = 170, Height = 95, Spacing = 8 };
        body.Children.Add(new Border { Width = 34, Height = 34, CornerRadius = new CornerRadius(17), Background = BorderBrush, Child = new TextBlock { Text = name[..1], HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, FontWeight = Microsoft.UI.Text.FontWeights.Bold } });
        body.Children.Add(new TextBlock { Text = name, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        body.Children.Add(new TextBlock { Text = note, Foreground = MutedBrush, FontSize = 12, TextTrimming = TextTrimming.CharacterEllipsis });
        var card = Card(body, 14); card.Width = 200; card.BorderBrush = selected ? AccentBrush : BorderBrush; return card;
    }

    private static Border Card(UIElement child, double padding) => new()
    {
        Child = child, Padding = new Thickness(padding), CornerRadius = new CornerRadius(14),
        Background = CardBrush, BorderBrush = BorderBrush, BorderThickness = new Thickness(1),
        HorizontalAlignment = HorizontalAlignment.Stretch
    };

    private static TextBlock SectionTitle(string text) => new() { Text = text, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 16 };

    private static Button ActionButton(string text, Action action)
    {
        var button = new Button { Content = text, Padding = new Thickness(14, 8, 14, 8) };
        button.Click += (_, _) => action();
        return button;
    }

    private static Button PrimaryButton(string text, Action action)
    {
        var button = ActionButton(text, action);
        button.Background = AccentBrush;
        button.Foreground = new SolidColorBrush(Colors.White);
        return button;
    }

    private async Task PickFilesAsync(ToolMode mode, FileSelectionState selection)
    {
        var picker = new FileOpenPicker { SuggestedStartLocation = PickerLocationId.MusicLibrary, ViewMode = PickerViewMode.List };
        foreach (var extension in new[] { ".ncm", ".mp3", ".flac", ".wav", ".m4a", ".aac", ".aif", ".aiff", ".caf" }) picker.FileTypeFilter.Add(extension);
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        var files = await picker.PickMultipleFilesAsync();
        AddFiles(files.Select(file => file.Path), mode, selection);
    }

    private async Task HandleDropAsync(DragEventArgs args, ToolMode mode, FileSelectionState selection)
    {
        if (!args.DataView.Contains(StandardDataFormats.StorageItems)) return;
        var items = await args.DataView.GetStorageItemsAsync();
        AddFiles(items.OfType<StorageFile>().Select(file => file.Path), mode, selection);
    }

    private static void AddFiles(IEnumerable<string> paths, ToolMode mode, FileSelectionState selection)
    {
        foreach (var path in paths.Where(path => AudioFilePolicy.IsSupported(path, mode)))
            if (!selection.Files.Contains(path, StringComparer.OrdinalIgnoreCase)) selection.Files.Add(path);
        UpdateSelectionLabel(selection);
    }

    private static void UpdateSelectionLabel(FileSelectionState selection)
    {
        selection.Label.Text = selection.Files.Count == 0 ? "尚未选择文件" : $"已选择 {selection.Files.Count} 个文件\n" + string.Join("\n", selection.Files.Select(System.IO.Path.GetFileName));
    }

    private async Task StartSelectedTasksAsync(ToolMode mode, FileSelectionState selection, OutputSelectionState output)
    {
        if (selection.Files.Count == 0) return;
        _operationCancellation?.Cancel();
        _operationCancellation = new CancellationTokenSource();
        foreach (var path in selection.Files.ToArray())
        {
            AudioTask item;
            try { item = _tasks.Enqueue(path, mode); }
            catch (InvalidOperationException) { continue; }
            _tasks.MarkProcessing(item.Id);
            RenderTasks();
            try
            {
                var result = await ProcessAudioAsync(path, mode, output, _operationCancellation.Token);
                _tasks.MarkCompleted(item.Id, result);
            }
            catch (OperationCanceledException) { _tasks.MarkFailed(item.Id, "已取消"); }
            catch (Exception error) { _tasks.MarkFailed(item.Id, error.Message); }
            RenderTasks();
        }
        await RefreshWorkerStatusAsync();
    }

    private async Task<string?> ProcessAudioAsync(string path, ToolMode mode, OutputSelectionState output, CancellationToken cancellationToken)
    {
        if (mode == ToolMode.Convert)
            return ReadOutput(await RunWorkerAsync("convert", WithOutput([path], path, output), cancellationToken));
        if (mode == ToolMode.Separate)
            return ReadOutput(await RunWorkerAsync("separate", WithOutput([path, "--keep", _separationKeep], path, output), cancellationToken));

        var source = path;
        if (IOPath.GetExtension(path).Equals(".ncm", StringComparison.OrdinalIgnoreCase))
        {
            var converted = await RunWorkerAsync("convert", WithOutput([path], path, output), cancellationToken);
            source = ReadOutput(converted) ?? throw new InvalidOperationException("转换完成但 Worker 未返回输出路径。");
        }
        return ReadOutput(await RunWorkerAsync("separate", WithOutput([source, "--keep", _separationKeep], path, output), cancellationToken));
    }

    private static string? ResolveOutputDirectory(OutputSelectionState output, string? sourcePath)
    {
        if (output.Mode == "custom") return output.CustomDirectory;
        if (output.Mode != "original" || string.IsNullOrWhiteSpace(sourcePath)) return null;
        if (System.IO.Directory.Exists(sourcePath)) return sourcePath;
        return IOPath.GetDirectoryName(sourcePath);
    }

    private static IReadOnlyList<string> WithOutput(IEnumerable<string> arguments, string sourcePath, OutputSelectionState output)
    {
        var values = arguments.ToList();
        var directory = ResolveOutputDirectory(output, sourcePath);
        if (!string.IsNullOrWhiteSpace(directory))
        {
            values.Add("--output-dir");
            values.Add(directory);
        }
        return values;
    }

    private Task<string> RunWorkerAsync(string command, IReadOnlyList<string> arguments, CancellationToken cancellationToken) =>
        _worker.ExecuteAsync(new WorkerCommand(command, arguments), cancellationToken);

    private static string? ReadOutput(string json)
    {
        using var document = JsonDocument.Parse(json);
        foreach (var property in new[] { "output", "path", "instrumental", "vocals", "folder" })
            if (document.RootElement.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.String)
                return value.GetString();
        return null;
    }

    private void CancelCurrentOperation() => _operationCancellation?.Cancel();

    private void RenderTasks()
    {
        foreach (var taskList in _taskLists)
        {
            taskList.Children.Clear();
            if (_tasks.Items.Count == 0)
            {
                taskList.Children.Add(InfoCard("还没有任务", "处理过的任务会显示在这里。"));
                continue;
            }
            foreach (var task in _tasks.Items.Take(20))
            {
                var grid = new Grid { ColumnSpacing = 10 };
                grid.ColumnDefinitions.Add(new ColumnDefinition());
                grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                var body = new StackPanel { Spacing = 3 };
                body.Children.Add(new TextBlock { Text = task.Title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
                var label = task.Status switch { CoreTaskStatus.Waiting => "等待", CoreTaskStatus.Processing => "处理中", CoreTaskStatus.Completed => "完成", _ => "失败" };
                var color = task.Status == CoreTaskStatus.Failed ? Colors.OrangeRed : task.Status == CoreTaskStatus.Completed ? Colors.LightGreen : Colors.LightGray;
                body.Children.Add(new TextBlock { Text = $"{label} · {task.Detail}", Foreground = new SolidColorBrush(color), TextWrapping = TextWrapping.Wrap, FontSize = 12 });
                grid.Children.Add(body);
                if (!string.IsNullOrWhiteSpace(task.OutputPath))
                {
                    var open = ActionButton("打开位置", () => OpenOutput(task.OutputPath));
                    Grid.SetColumn(open, 1); grid.Children.Add(open);
                }
                taskList.Children.Add(Card(grid, 12));
            }
        }
    }

    private static void OpenOutput(string path)
    {
        var target = Directory.Exists(path) ? path : IOPath.GetDirectoryName(path);
        if (!string.IsNullOrWhiteSpace(target))
            Process.Start(new ProcessStartInfo("explorer.exe", target) { UseShellExecute = true });
    }

    private async Task RefreshWorkerStatusAsync()
    {
        var status = await _worker.CheckAsync();
        if (_engineStatus is not null) _engineStatus.Text = status.IsReady ? "本地引擎就绪" : "环境需检查";
        if (_workbenchInstallNotice is not null)
            _workbenchInstallNotice.Visibility = status.IsReady ? Visibility.Collapsed : Visibility.Visible;
    }

    private void SelectMenu(string tag) => ShowPage(tag);

    private async Task ShowVoiceDialogAsync(StackPanel cards)
    {
        var content = new StackPanel { Spacing = 12, Width = 460 };
        var name = new TextBox { Header = "模板名称", PlaceholderText = "例如：盼盼 · 自然口播" };
        var reference = new TextBlock { Text = "尚未选择参考音", Foreground = MutedBrush, TextWrapping = TextWrapping.Wrap };
        string? referencePath = null;
        var pick = new Button { Content = "选择参考音…" };
        pick.Click += async (_, _) =>
        {
            var picker = new FileOpenPicker { SuggestedStartLocation = PickerLocationId.MusicLibrary };
            foreach (var extension in new[] { ".wav", ".m4a", ".mp3", ".flac", ".aac", ".aif", ".aiff", ".caf" }) picker.FileTypeFilter.Add(extension);
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            var file = await picker.PickSingleFileAsync();
            if (file is not null) { referencePath = file.Path; reference.Text = file.Path; }
        };
        var referenceText = new TextBox { Header = "参考音频里说了什么" };
        var note = new TextBox { Header = "备注", PlaceholderText = "例如：日常短视频 / 轻松自然" };
        var makeDefault = new CheckBox { Content = "设为默认人声" };
        content.Children.Add(name); content.Children.Add(pick); content.Children.Add(reference);
        content.Children.Add(referenceText); content.Children.Add(note); content.Children.Add(makeDefault);
        var dialog = new ContentDialog { Title = "新建人声模板", Content = content, PrimaryButtonText = "保存模板", CloseButtonText = "取消", XamlRoot = Root.XamlRoot };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
        if (string.IsNullOrWhiteSpace(name.Text) || string.IsNullOrWhiteSpace(referencePath))
        {
            await ShowMessageAsync("无法保存", "请填写模板名称并选择参考音。");
            return;
        }
        var args = new List<string> { "--name", name.Text, "--ref-audio", referencePath, "--ref-text", referenceText.Text, "--note", note.Text };
        if (makeDefault.IsChecked == true) args.Add("--default");
        try
        {
            await _worker.ExecuteAsync(new WorkerCommand("voice-save", args));
            await RefreshVoicesAsync(cards, new TextBlock());
        }
        catch (Exception error) { await ShowMessageAsync("保存失败", error.Message); }
    }

    private async Task ShowMessageAsync(string title, string message)
    {
        var dialog = new ContentDialog { Title = title, Content = message, CloseButtonText = "知道了", XamlRoot = Root.XamlRoot };
        await dialog.ShowAsync();
    }
}
