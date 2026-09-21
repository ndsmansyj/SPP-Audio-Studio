using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
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
    private readonly Grid _navigation;
    private readonly ContentControl _pageHost = new();
    private readonly IWorkerService _worker;
    private readonly TaskQueue _tasks = new();
    private readonly List<string> _selectedFiles = [];
    private ToolMode _workbenchMode = ToolMode.ConvertAndSeparate;
    private StackPanel? _taskList;
    private TextBlock? _selectionLabel;
    private TextBlock? _engineStatus;
    private string? _outputDirectory;
    private string _separationKeep = "instrumental";
    private string _downloadSource = "auto";
    private readonly List<VoiceTemplate> _voices = [];
    private string? _selectedVoiceId;
    private CancellationTokenSource? _operationCancellation;

    private static readonly SolidColorBrush CardBrush = new(ColorHelper.FromArgb(255, 23, 26, 34));
    private static readonly SolidColorBrush BorderBrush = new(ColorHelper.FromArgb(255, 53, 58, 71));
    private static readonly SolidColorBrush MutedBrush = new(ColorHelper.FromArgb(255, 156, 163, 181));
    private static readonly SolidColorBrush AccentBrush = new(ColorHelper.FromArgb(255, 124, 140, 255));

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
        _appWindow.Resize(new global::Windows.Graphics.SizeInt32(1180, 800));
        _appWindow.SetIcon("Assets/pixel_icon.ico");
        _appWindow.Changed += (_, args) =>
        {
            if (!args.DidSizeChange || _appWindow is null) return;
            var size = _appWindow.Size;
            if (size.Width < 1040 || size.Height < 720)
                _appWindow.Resize(new global::Windows.Graphics.SizeInt32(Math.Max(1040, size.Width), Math.Max(720, size.Height)));
        };
    }

    private void BuildShell()
    {
        _navigation.Children.Clear();
        _navigation.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(215) });
        _navigation.ColumnDefinitions.Add(new ColumnDefinition());
        var pane = new StackPanel { Spacing = 6, Padding = new Thickness(12), Background = new SolidColorBrush(ColorHelper.FromArgb(220, 13, 15, 20)) };
        pane.Children.Add(BrandHeader());
        foreach (var (text, tag) in new[] { ("音频工作台", "workbench"), ("格式转换", "convert"), ("人声分离", "separate"), ("声音克隆", "clone"), ("模型与环境", "environment") })
        {
            var button = new Button { Content = text, HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Left, Tag = tag };
            button.Click += (_, _) => ShowPage(tag);
            pane.Children.Add(button);
        }
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
        var panel = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10, Margin = new Thickness(12, 18, 0, 18) };
        panel.Children.Add(new Border
        {
            Width = 34, Height = 34, CornerRadius = new CornerRadius(9), Background = AccentBrush,
            Child = new TextBlock { Text = "SPP", FontSize = 11, FontWeight = Microsoft.UI.Text.FontWeights.Bold, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center }
        });
        var labels = new StackPanel { Spacing = 1 };
        labels.Children.Add(new TextBlock { Text = "SPP Audio Studio", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 14 });
        labels.Children.Add(new TextBlock { Text = "Windows Preview", Foreground = MutedBrush, FontSize = 11 });
        panel.Children.Add(labels);
        return panel;
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
        _pageHost.Content = new ScrollViewer
        {
            HorizontalScrollMode = ScrollMode.Disabled,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Content = tag switch
            {
                "convert" => BuildFileToolPage(ToolMode.Convert, "格式转换", "特殊格式 → 原始 MP3 / FLAC"),
                "separate" => BuildFileToolPage(ToolMode.Separate, "人声分离", "Mel-Deux · 去人声 / 提取人声 / 双轨输出"),
                "clone" => BuildClonePage(),
                "environment" => BuildEnvironmentPage(),
                _ => BuildWorkbenchPage()
            }
        };
    }

    private StackPanel Page(string title, string subtitle)
    {
        var page = new StackPanel { Spacing = 20, Padding = new Thickness(26), HorizontalAlignment = HorizontalAlignment.Stretch };
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
        _selectedFiles.Clear();
        var page = Page("音频工作台", "转换音乐、人声分离、克隆声音。常用操作尽量一处完成。");
        page.Children.Add(NoticeCard("首次使用：安装模型和运行环境", "声音克隆需要 Qwen；人声分离需要 Mel。状态来自本机 Worker 实时检查。", "前往安装", () => SelectMenu("environment")));

        var modes = new ComboBox { Header = "处理模式", Width = 520, HorizontalAlignment = HorizontalAlignment.Left, SelectedIndex = 2 };
        modes.Items.Add("仅转换"); modes.Items.Add("仅分离"); modes.Items.Add("转换 + 分离");
        modes.SelectionChanged += (_, _) => _workbenchMode = modes.SelectedIndex switch { 0 => ToolMode.Convert, 1 => ToolMode.Separate, _ => ToolMode.ConvertAndSeparate };
        page.Children.Add(modes);
        page.Children.Add(SegmentedOptions("分离输出", ["仅伴奏（去人声）", "仅人声", "人声 + 伴奏"]));
        page.Children.Add(OutputCard("源文件旁边"));
        page.Children.Add(DropZone("把特殊格式 / FLAC / MP3 / WAV / M4A 拖到这里", "特殊格式会自动先转换，再进入 Mel-Deux；普通音频直接分离。", _workbenchMode));

        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        actions.Children.Add(ActionButton("选择文件…", async () => await PickFilesAsync(_workbenchMode)));
        actions.Children.Add(PrimaryButton("开始处理", () => _ = StartSelectedTasksAsync(_workbenchMode)));
        actions.Children.Add(ActionButton("取消", CancelCurrentOperation));
        actions.Children.Add(ActionButton("清空列表", () => { _selectedFiles.Clear(); UpdateSelectionLabel(); }));
        _engineStatus = new TextBlock { Text = "环境需检查", Foreground = new SolidColorBrush(Colors.Orange), VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(16, 0, 0, 0) };
        actions.Children.Add(_engineStatus);
        page.Children.Add(actions);
        _selectionLabel = new TextBlock { Text = "尚未选择文件", Foreground = MutedBrush };
        page.Children.Add(_selectionLabel);
        page.Children.Add(new Rectangle { Height = 1, Fill = BorderBrush, HorizontalAlignment = HorizontalAlignment.Stretch });
        page.Children.Add(SectionTitle("任务"));
        _taskList = new StackPanel { Spacing = 8 };
        RenderTasks();
        page.Children.Add(_taskList);
        return page;
    }

    private StackPanel BuildFileToolPage(ToolMode mode, string title, string subtitle)
    {
        _selectedFiles.Clear();
        var page = Page(title, subtitle);
        if (mode == ToolMode.Separate) page.Children.Add(SegmentedOptions("保留内容", ["仅伴奏（去人声）", "仅人声", "人声 + 伴奏"]));
        page.Children.Add(OutputCard("源文件旁边"));
        page.Children.Add(DropZone("把文件拖到这里", mode == ToolMode.Convert ? "支持批量特殊格式文件" : "支持 MP3 / FLAC / WAV / M4A 等音频", mode));
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        actions.Children.Add(ActionButton("选择文件…", async () => await PickFilesAsync(mode)));
        actions.Children.Add(PrimaryButton(mode == ToolMode.Convert ? "仅转换" : "仅分离", () => _ = StartSelectedTasksAsync(mode)));
        actions.Children.Add(ActionButton("取消", CancelCurrentOperation));
        actions.Children.Add(ActionButton("清空", () => { _selectedFiles.Clear(); UpdateSelectionLabel(); }));
        page.Children.Add(actions);
        _selectionLabel = new TextBlock { Text = "尚未选择文件", Foreground = MutedBrush };
        page.Children.Add(_selectionLabel);
        _taskList = new StackPanel { Spacing = 8 };
        RenderTasks();
        page.Children.Add(_taskList);
        return page;
    }

    private StackPanel BuildClonePage()
    {
        var page = Page("声音克隆", "常用人声保存一次，以后选模板、粘文案、直接生成。");
        var top = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        top.Children.Add(SectionTitle("常用人声"));
        var cards = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        top.Children.Add(PrimaryButton("＋ 新建人声模板", () => _ = ShowVoiceDialogAsync(cards)));
        page.Children.Add(top);
        page.Children.Add(new ScrollViewer { HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, Content = cards });
        page.Children.Add(InfoCard("参考音建议", "推荐 5–15 秒、单人清晰说话、少背景音乐和回声。参考文本应与音频一致；安装 Whisper 后可留空自动转写。"));
        page.Children.Add(SectionTitle("生成文案"));
        var script = new TextBox { AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 180, PlaceholderText = "输入要生成的文案" };
        page.Children.Add(script);
        page.Children.Add(OutputCard("App 默认目录"));
        var status = new TextBlock { Foreground = MutedBrush, VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap };
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        actions.Children.Add(PrimaryButton("生成声音", () => _ = CloneVoiceAsync(script.Text, status)));
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
            var json = await _worker.ExecuteAsync(new WorkerCommand("voice-list", []));
            using var document = JsonDocument.Parse(json);
            _voices.Clear(); cards.Children.Clear();
            foreach (var item in document.RootElement.GetProperty("templates").EnumerateArray())
            {
                var voice = new VoiceTemplate(
                    item.GetProperty("id").GetString()!, item.GetProperty("name").GetString()!,
                    item.TryGetProperty("reference_text", out var text) ? text.GetString() ?? "" : "",
                    item.TryGetProperty("note", out var note) ? note.GetString() ?? "" : "",
                    item.TryGetProperty("default", out var value) && value.GetBoolean());
                _voices.Add(voice);
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

    private async Task CloneVoiceAsync(string script, TextBlock status)
    {
        var validation = new VoiceCloneRequest(_selectedVoiceId, null, script, _outputDirectory).Validate();
        if (validation is not null) { status.Text = validation; return; }
        _operationCancellation = new CancellationTokenSource();
        status.Text = "正在生成…";
        try
        {
            var args = new List<string> { _selectedVoiceId!, "--text", script };
            if (!string.IsNullOrWhiteSpace(_outputDirectory)) { args.Add("--output-dir"); args.Add(_outputDirectory); }
            var json = await RunWorkerAsync("voice-clone", args, _operationCancellation.Token);
            var output = ReadOutput(json);
            status.Text = output is null ? "生成完成" : $"生成完成：{output}";
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
        var page = Page("模型与环境", "本机已有模型可直接链接；新机器可由 App 下载并管理。");
        var status = new TextBlock { Text = "正在读取 Worker 状态…", Foreground = MutedBrush, TextWrapping = TextWrapping.Wrap };
        page.Children.Add(NoticeCard("一键安装全部组件", "安装 Qwen、Mel、Whisper 的模型与运行环境。下载较大，可随时取消。", "一键下载并安装", () => _ = InstallAllAsync(status)));
        page.Children.Add(StatusStrip(status));
        var grid = new Grid { ColumnSpacing = 14 };
        grid.ColumnDefinitions.Add(new ColumnDefinition()); grid.ColumnDefinitions.Add(new ColumnDefinition());
        var qwen = ModelCard("qwen", "Qwen3-TTS 1.7B 8bit", "声音克隆 · Windows Runtime", "约 4.0 GB", "Apache-2.0", status);
        var mel = ModelCard("mel", "Mel-Deux", "人声 / 伴奏分离", "约 435 MB", "CC BY-NC 4.0", status);
        Grid.SetColumn(mel, 1); grid.Children.Add(qwen); grid.Children.Add(mel); page.Children.Add(grid);
        page.Children.Add(ModelCard("whisper", "Whisper large-v3-turbo（可选）", "临时参考音自动转写", "约 1.5 GB", "模型条款见 Hugging Face", status));
        page.Children.Add(RuntimeCard(status));
        page.Children.Add(InfoCard("诊断与日志", "下方状态由 doctor 与 model-status 实时返回；失败信息不会被隐藏。"));
        page.Children.Add(Card(status, 14));
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

    private Border ModelCard(string key, string title, string subtitle, string size, string license, TextBlock status)
    {
        var body = new StackPanel { Spacing = 11 };
        body.Children.Add(new TextBlock { Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 16 });
        body.Children.Add(new TextBlock { Text = subtitle, Foreground = MutedBrush, FontSize = 12 });
        body.Children.Add(new TextBlock { Text = $"▣ {size}    {license}", Foreground = MutedBrush, FontSize = 12 });
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 9 };
        buttons.Children.Add(PrimaryButton("下载到 App", () => _ = RunEnvironmentCommandAsync("model-download", [key, "--source", _downloadSource], status)));
        buttons.Children.Add(ActionButton("链接本地…", () => _ = LinkModelAsync(key, status)));
        body.Children.Add(buttons);
        return Card(body, 16);
    }

    private Border RuntimeCard(TextBlock status)
    {
        var body = new StackPanel { Spacing = 12 };
        body.Children.Add(SectionTitle("推理运行环境"));
        body.Children.Add(new TextBlock { Text = "运行组件安装到 App 数据目录，不修改系统 Python。", Foreground = MutedBrush });
        body.Children.Add(RuntimeRow("Qwen Runtime", "qwen", status));
        body.Children.Add(RuntimeRow("Mel Separator", "mel", status));
        body.Children.Add(RuntimeRow("Whisper ASR（可选）", "asr", status));
        return Card(body, 16);
    }

    private StackPanel RuntimeRow(string label, string key, TextBlock status)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        row.Children.Add(new TextBlock { Text = label, Width = 420, VerticalAlignment = VerticalAlignment.Center });
        row.Children.Add(ActionButton("安装", () => _ = RunEnvironmentCommandAsync("runtime-install", [key, "--source", _downloadSource], status)));
        return row;
    }

    private async Task RefreshEnvironmentAsync(TextBlock status)
    {
        try
        {
            var doctor = await _worker.ExecuteAsync(new WorkerCommand("doctor", []));
            var models = await _worker.ExecuteAsync(new WorkerCommand("model-status", []));
            using var modelDocument = JsonDocument.Parse(models);
            if (modelDocument.RootElement.TryGetProperty("download_source", out var source))
                _downloadSource = source.GetString() ?? "auto";
            status.Text = "Doctor\n" + PrettyJson(doctor) + "\n\n模型与运行环境\n" + PrettyJson(models);
        }
        catch (Exception error) { status.Text = "检查失败：" + error.Message; }
    }

    private async Task SetDownloadSourceAsync(string source, TextBlock status)
    {
        _downloadSource = source;
        await RunEnvironmentCommandAsync("download-source", [source], status);
    }

    private async Task RunEnvironmentCommandAsync(string command, IReadOnlyList<string> arguments, TextBlock status)
    {
        _operationCancellation?.Cancel();
        _operationCancellation = new CancellationTokenSource();
        status.Text = $"正在执行 {command}…";
        try
        {
            var json = await RunWorkerAsync(command, arguments, _operationCancellation.Token);
            status.Text = PrettyJson(json);
            await RefreshWorkerStatusAsync();
        }
        catch (OperationCanceledException) { status.Text = "已取消"; }
        catch (Exception error) { status.Text = error.Message; }
    }

    private async Task InstallAllAsync(TextBlock status)
    {
        foreach (var (command, component) in new[] {
            ("runtime-install", "qwen"), ("model-download", "qwen"),
            ("runtime-install", "mel"), ("model-download", "mel"),
            ("runtime-install", "asr"), ("model-download", "whisper") })
        {
            await RunEnvironmentCommandAsync(command, [component, "--source", _downloadSource], status);
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

    private void ShowProgress(WorkerProgress progress)
    {
        if (_engineStatus is not null) _engineStatus.Text = $"处理中：{progress.Event}";
    }

    private Border DropZone(string title, string subtitle, ToolMode mode)
    {
        var body = new StackPanel { Spacing = 9, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        body.Children.Add(new FontIcon { Glyph = "", FontSize = 32, Foreground = AccentBrush });
        body.Children.Add(new TextBlock { Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, TextAlignment = TextAlignment.Center });
        body.Children.Add(new TextBlock { Text = subtitle, Foreground = MutedBrush, FontSize = 12, TextAlignment = TextAlignment.Center });
        var border = new Border
        {
            Height = 180, CornerRadius = new CornerRadius(18), BorderThickness = new Thickness(1), BorderBrush = BorderBrush,
            Background = new SolidColorBrush(ColorHelper.FromArgb(120, 23, 26, 34)), Child = body, AllowDrop = true
        };
        border.DragOver += (_, e) => { e.AcceptedOperation = DataPackageOperation.Copy; e.DragUIOverride.Caption = "添加到任务列表"; };
        border.Drop += async (_, e) => await HandleDropAsync(e, mode);
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

    private Border OutputCard(string primaryLabel)
    {
        var body = new StackPanel { Spacing = 8 };
        body.Children.Add(SectionTitle("导出位置"));
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 18 };
        var automatic = new RadioButton { Content = primaryLabel, IsChecked = true, GroupName = primaryLabel + "output" };
        var custom = new RadioButton { Content = "自定义目录…", GroupName = primaryLabel + "output" };
        var path = new TextBlock { Text = "自动", Foreground = MutedBrush, VerticalAlignment = VerticalAlignment.Center };
        automatic.Checked += (_, _) => { _outputDirectory = null; path.Text = "自动"; };
        custom.Checked += async (_, _) =>
        {
            var picker = new FolderPicker { SuggestedStartLocation = PickerLocationId.MusicLibrary };
            picker.FileTypeFilter.Add("*");
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            var folder = await picker.PickSingleFolderAsync();
            if (folder is null) { automatic.IsChecked = true; return; }
            _outputDirectory = folder.Path;
            path.Text = folder.Path;
        };
        row.Children.Add(automatic); row.Children.Add(custom); row.Children.Add(path);
        body.Children.Add(row);
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
        var card = Card(grid, 16); card.Background = new SolidColorBrush(ColorHelper.FromArgb(255, 24, 28, 48)); card.BorderBrush = AccentBrush; return card;
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

    private async Task PickFilesAsync(ToolMode mode)
    {
        var picker = new FileOpenPicker { SuggestedStartLocation = PickerLocationId.MusicLibrary, ViewMode = PickerViewMode.List };
        foreach (var extension in new[] { ".ncm", ".mp3", ".flac", ".wav", ".m4a", ".aac", ".aif", ".aiff", ".caf" }) picker.FileTypeFilter.Add(extension);
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        var files = await picker.PickMultipleFilesAsync();
        AddFiles(files.Select(file => file.Path), mode);
    }

    private async Task HandleDropAsync(DragEventArgs args, ToolMode mode)
    {
        if (!args.DataView.Contains(StandardDataFormats.StorageItems)) return;
        var items = await args.DataView.GetStorageItemsAsync();
        AddFiles(items.OfType<StorageFile>().Select(file => file.Path), mode);
    }

    private void AddFiles(IEnumerable<string> paths, ToolMode mode)
    {
        foreach (var path in paths.Where(path => AudioFilePolicy.IsSupported(path, mode)))
            if (!_selectedFiles.Contains(path, StringComparer.OrdinalIgnoreCase)) _selectedFiles.Add(path);
        UpdateSelectionLabel();
    }

    private void UpdateSelectionLabel()
    {
        if (_selectionLabel is null) return;
        _selectionLabel.Text = _selectedFiles.Count == 0 ? "尚未选择文件" : $"已选择 {_selectedFiles.Count} 个文件\n" + string.Join("\n", _selectedFiles.Select(System.IO.Path.GetFileName));
    }

    private async Task StartSelectedTasksAsync(ToolMode mode)
    {
        if (_selectedFiles.Count == 0) return;
        _operationCancellation?.Cancel();
        _operationCancellation = new CancellationTokenSource();
        foreach (var path in _selectedFiles.ToArray())
        {
            AudioTask item;
            try { item = _tasks.Enqueue(path, mode); }
            catch (InvalidOperationException) { continue; }
            _tasks.MarkProcessing(item.Id);
            RenderTasks();
            try
            {
                var output = await ProcessAudioAsync(path, mode, _operationCancellation.Token);
                _tasks.MarkCompleted(item.Id, output);
            }
            catch (OperationCanceledException) { _tasks.MarkFailed(item.Id, "已取消"); }
            catch (Exception error) { _tasks.MarkFailed(item.Id, error.Message); }
            RenderTasks();
        }
        await RefreshWorkerStatusAsync();
    }

    private async Task<string?> ProcessAudioAsync(string path, ToolMode mode, CancellationToken cancellationToken)
    {
        if (mode == ToolMode.Convert)
            return ReadOutput(await RunWorkerAsync("convert", WithOutput([path]), cancellationToken));
        if (mode == ToolMode.Separate)
            return ReadOutput(await RunWorkerAsync("separate", WithOutput([path, "--keep", _separationKeep]), cancellationToken));

        var source = path;
        if (IOPath.GetExtension(path).Equals(".ncm", StringComparison.OrdinalIgnoreCase))
        {
            var converted = await RunWorkerAsync("convert", WithOutput([path]), cancellationToken);
            source = ReadOutput(converted) ?? throw new InvalidOperationException("转换完成但 Worker 未返回输出路径。");
        }
        return ReadOutput(await RunWorkerAsync("separate", WithOutput([source, "--keep", _separationKeep]), cancellationToken));
    }

    private IReadOnlyList<string> WithOutput(IEnumerable<string> arguments)
    {
        var values = arguments.ToList();
        if (!string.IsNullOrWhiteSpace(_outputDirectory)) { values.Add("--output-dir"); values.Add(_outputDirectory); }
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
        if (_taskList is null) return;
        _taskList.Children.Clear();
        if (_tasks.Items.Count == 0)
        {
            _taskList.Children.Add(InfoCard("还没有任务", "处理过的任务会显示在这里。"));
            return;
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
            _taskList.Children.Add(Card(grid, 12));
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
