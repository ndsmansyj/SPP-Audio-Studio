using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using SPPAudioStudio.Core;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace SPPAudioStudio.Windows;

public sealed partial class MainWindow : Window
{
    private AppWindow? _appWindow;
    private readonly NavigationView _navigation = new();
    private readonly MissingWorkerService _worker = new();
    private readonly TaskQueue _tasks = new();
    private readonly List<string> _selectedFiles = [];
    private ToolMode _workbenchMode = ToolMode.ConvertAndSeparate;
    private StackPanel? _taskList;
    private TextBlock? _selectionLabel;
    private TextBlock? _engineStatus;

    private static readonly SolidColorBrush CardBrush = new(ColorHelper.FromArgb(255, 23, 26, 34));
    private static readonly SolidColorBrush BorderBrush = new(ColorHelper.FromArgb(255, 53, 58, 71));
    private static readonly SolidColorBrush MutedBrush = new(ColorHelper.FromArgb(255, 156, 163, 181));
    private static readonly SolidColorBrush AccentBrush = new(ColorHelper.FromArgb(255, 124, 140, 255));

    public MainWindow()
    {
        InitializeComponent();
        Title = "SPP Audio Studio";
        SystemBackdrop = new Microsoft.UI.Xaml.Media.MicaBackdrop();
        ConfigureWindow();
        BuildShell();
        _ = RefreshWorkerStatusAsync();
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
        _navigation.PaneDisplayMode = NavigationViewPaneDisplayMode.Left;
        _navigation.IsPaneToggleButtonVisible = false;
        _navigation.IsBackButtonVisible = NavigationViewBackButtonVisible.Collapsed;
        _navigation.IsSettingsVisible = false;
        _navigation.IsPaneOpen = true;
        _navigation.OpenPaneLength = 215;
        _navigation.CompactPaneLength = 54;
        _navigation.AlwaysShowHeader = false;
        _navigation.Background = new SolidColorBrush(ColorHelper.FromArgb(220, 13, 15, 20));
        _navigation.PaneHeader = BrandHeader();

        _navigation.MenuItems.Add(MenuItem("", "音频工作台", "workbench"));
        _navigation.MenuItems.Add(MenuItem("", "格式转换", "convert"));
        _navigation.MenuItems.Add(MenuItem("", "人声分离", "separate"));
        _navigation.MenuItems.Add(MenuItem("", "声音克隆", "clone"));
        _navigation.MenuItems.Add(MenuItem("", "模型与环境", "environment"));
        _navigation.SelectionChanged += NavigationOnSelectionChanged;
        _navigation.SelectedItem = _navigation.MenuItems[0];
        Root.Children.Add(_navigation);
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
        _navigation.Content = new ScrollViewer
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
        page.Children.Add(NoticeCard("首次使用：安装模型和运行环境", "声音克隆需要 Qwen；人声分离需要 Mel。当前 Windows Worker 尚未集成。", "前往安装", () => SelectMenu("environment")));

        var modes = new ComboBox { Header = "处理模式", Width = 520, HorizontalAlignment = HorizontalAlignment.Left, SelectedIndex = 2 };
        modes.Items.Add("仅转换"); modes.Items.Add("仅分离"); modes.Items.Add("转换 + 分离");
        modes.SelectionChanged += (_, _) => _workbenchMode = modes.SelectedIndex switch { 0 => ToolMode.Convert, 1 => ToolMode.Separate, _ => ToolMode.ConvertAndSeparate };
        page.Children.Add(modes);
        page.Children.Add(SegmentedOptions("分离输出", ["仅伴奏（去人声）", "仅人声", "人声 + 伴奏"]));
        page.Children.Add(OutputCard("源文件旁边"));
        page.Children.Add(DropZone("把特殊格式 / FLAC / MP3 / WAV / M4A 拖到这里", "特殊格式会自动先转换，再进入 Mel-Deux；普通音频直接分离。", _workbenchMode));

        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        actions.Children.Add(ActionButton("选择文件…", async () => await PickFilesAsync(_workbenchMode)));
        actions.Children.Add(PrimaryButton("开始处理", () => StartSelectedTasks(_workbenchMode)));
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
        actions.Children.Add(PrimaryButton(mode == ToolMode.Convert ? "仅转换" : "仅分离", () => StartSelectedTasks(mode)));
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
        top.Children.Add(PrimaryButton("＋ 新建人声模板", ShowVoiceDialogAsync));
        page.Children.Add(top);

        var cards = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        cards.Children.Add(VoiceCard("默认人声", "Qwen TTS 人声模板", true));
        cards.Children.Add(VoiceCard("临时参考音", "偶尔用的声音，不保存模板", false));
        page.Children.Add(new ScrollViewer { HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, Content = cards });
        page.Children.Add(InfoCard("参考音建议", "推荐 5–15 秒、单人清晰说话、少背景音乐和回声。支持 WAV、M4A、MP3、FLAC、AAC、AIFF 或 CAF。\n参考音里的原话要与音频一致；安装 Whisper 后可以留空自动转写。"));
        page.Children.Add(SectionTitle("生成文案"));
        page.Children.Add(new TextBlock { Text = "克隆结果偶尔会有波动，可以多生成两版挑选；文案较长时建议分成两段生成。", Foreground = MutedBrush });
        var script = new TextBox
        {
            AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 180,
            Text = "大家好，我是宋盼盼。这是一段 SPP Audio Studio 的声音克隆测试。如果你能自然地听到这句话，说明人声模板、模型和本地推理都已经正常工作。"
        };
        page.Children.Add(script);
        page.Children.Add(OutputCard("App 默认目录"));
        var status = new TextBlock { Foreground = MutedBrush, VerticalAlignment = VerticalAlignment.Center };
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        actions.Children.Add(PrimaryButton("生成声音", () =>
        {
            var request = new VoiceCloneRequest("default", null, script.Text, null);
            status.Text = request.Validate() ?? MissingWorkerService.MissingMessage;
        }));
        actions.Children.Add(status);
        page.Children.Add(actions);
        page.Children.Add(InfoCard("当前模板：默认人声", "Windows UI 骨架保留模板选择、参考文本、备注、默认人声和试听入口。Worker 接入后启用实际生成。"));
        return page;
    }

    private StackPanel BuildEnvironmentPage()
    {
        var page = Page("模型与环境", "本机已有模型可直接链接；新机器可由 App 下载并管理。");
        page.Children.Add(NoticeCard("一键安装全部组件", "依次安装 Qwen、Mel 和 Whisper 的模型与运行环境；当前 Windows Worker 尚未集成，因此不会伪装安装成功。", "一键下载并安装", ShowMissingWorkerAsync));
        page.Children.Add(StatusStrip());

        var grid = new Grid { ColumnSpacing = 14 };
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        var qwen = ModelCard("Qwen3-TTS 1.7B 8bit", "声音克隆 · Windows Runtime", "约 3.1 GB", "Apache-2.0");
        var mel = ModelCard("Mel-Deux", "人声 / 伴奏分离", "约 435 MB", "CC BY-NC 4.0");
        Grid.SetColumn(mel, 1); grid.Children.Add(qwen); grid.Children.Add(mel);
        page.Children.Add(grid);
        page.Children.Add(ModelCard("Whisper large-v3-turbo（可选）", "临时参考音自动转写", "约 1.5 GB", "模型条款见 Hugging Face"));
        page.Children.Add(RuntimeCard());
        page.Children.Add(InfoCard("诊断与日志", "Windows 预览版只报告真实状态：Worker 尚未集成。后续接入统一 Worker 后，这里将提供诊断报告、最近错误和日志目录。"));
        page.Children.Add(InfoCard("环境详细检查", "✕ Windows Worker：缺失\n✕ Qwen Runtime：未配置\n✕ Qwen3-TTS 模型：未配置\n✕ Mel Separator Runtime：未配置\n✕ Mel-Deux 模型：未配置\n✕ Whisper 自动转写（可选）：未配置"));
        return page;
    }

    private Border StatusStrip()
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        row.Children.Add(new FontIcon { Glyph = "", Foreground = new SolidColorBrush(Colors.Orange) });
        row.Children.Add(new TextBlock { Text = "Windows Worker 尚未集成，AI 组件暂不可用", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center });
        var source = new ComboBox { Width = 210, SelectedIndex = 0, Margin = new Thickness(20, 0, 0, 0) };
        source.Items.Add("自动（镜像优先）"); source.Items.Add("HF 镜像"); source.Items.Add("Hugging Face 官方");
        row.Children.Add(source);
        return Card(row, 16);
    }

    private Border ModelCard(string title, string subtitle, string size, string license)
    {
        var body = new StackPanel { Spacing = 11 };
        var heading = new Grid();
        heading.ColumnDefinitions.Add(new ColumnDefinition()); heading.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var names = new StackPanel { Spacing = 3 };
        names.Children.Add(new TextBlock { Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 16 });
        names.Children.Add(new TextBlock { Text = subtitle, Foreground = MutedBrush, FontSize = 12 });
        var pill = new Border { CornerRadius = new CornerRadius(12), Background = new SolidColorBrush(ColorHelper.FromArgb(255, 53, 43, 26)), Padding = new Thickness(9, 4, 9, 4), Child = new TextBlock { Text = "未安装", Foreground = new SolidColorBrush(Colors.Orange), FontSize = 12 } };
        Grid.SetColumn(pill, 1); heading.Children.Add(names); heading.Children.Add(pill); body.Children.Add(heading);
        body.Children.Add(new TextBlock { Text = $"▣ {size}    {license}", Foreground = MutedBrush, FontSize = 12 });
        body.Children.Add(new TextBlock { Text = "尚未配置", Foreground = MutedBrush, FontFamily = new FontFamily("Consolas"), FontSize = 11 });
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 9 };
        buttons.Children.Add(PrimaryButton("下载到 App", ShowMissingWorkerAsync));
        buttons.Children.Add(ActionButton("链接本地…", ShowMissingWorkerAsync));
        body.Children.Add(buttons);
        return Card(body, 16);
    }

    private Border RuntimeCard()
    {
        var body = new StackPanel { Spacing = 12 };
        body.Children.Add(SectionTitle("推理运行环境"));
        body.Children.Add(new TextBlock { Text = "Windows 版将使用 App 管理的 Python / 原生运行组件；本次提交仅提供可编译 UI 与命令边界。", Foreground = MutedBrush, TextWrapping = TextWrapping.Wrap });
        body.Children.Add(RuntimeRow("Qwen Runtime", "未配置"));
        body.Children.Add(RuntimeRow("Mel Separator", "未配置"));
        body.Children.Add(RuntimeRow("Whisper ASR（可选）", "未配置"));
        return Card(body, 16);
    }

    private StackPanel RuntimeRow(string label, string state)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        row.Children.Add(new TextBlock { Text = $"○  {label}：{state}", Width = 420, VerticalAlignment = VerticalAlignment.Center });
        row.Children.Add(ActionButton("安装", ShowMissingWorkerAsync));
        return row;
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

    private static StackPanel SegmentedOptions(string title, string[] options)
    {
        var panel = new StackPanel { Spacing = 8, MaxWidth = 520, HorizontalAlignment = HorizontalAlignment.Left };
        panel.Children.Add(SectionTitle(title));
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 4 };
        for (var i = 0; i < options.Length; i++) row.Children.Add(new RadioButton { Content = options[i], IsChecked = i == 0, GroupName = title });
        panel.Children.Add(row);
        return panel;
    }

    private static Border OutputCard(string primaryLabel)
    {
        var body = new StackPanel { Spacing = 8 };
        body.Children.Add(SectionTitle("导出位置"));
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 18 };
        row.Children.Add(new RadioButton { Content = primaryLabel, IsChecked = true, GroupName = primaryLabel + "output" });
        row.Children.Add(new RadioButton { Content = "自定义目录", GroupName = primaryLabel + "output" });
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

    private void StartSelectedTasks(ToolMode mode)
    {
        foreach (var path in _selectedFiles)
        {
            try
            {
                var item = _tasks.Enqueue(path, mode);
                _tasks.MarkFailed(item.Id, MissingWorkerService.MissingMessage);
            }
            catch (InvalidOperationException) { }
        }
        RenderTasks();
    }

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
            var body = new StackPanel { Spacing = 3 };
            body.Children.Add(new TextBlock { Text = task.Title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
            body.Children.Add(new TextBlock { Text = $"失败 · {task.Detail}", Foreground = new SolidColorBrush(Colors.OrangeRed), TextWrapping = TextWrapping.Wrap, FontSize = 12 });
            _taskList.Children.Add(Card(body, 12));
        }
    }

    private async Task RefreshWorkerStatusAsync()
    {
        var status = await _worker.CheckAsync();
        if (_engineStatus is not null) _engineStatus.Text = status.IsReady ? "本地引擎就绪" : "环境需检查";
    }

    private void SelectMenu(string tag)
    {
        var item = _navigation.MenuItems.OfType<NavigationViewItem>().FirstOrDefault(item => Equals(item.Tag, tag));
        if (item is not null) _navigation.SelectedItem = item;
        ShowPage(tag);
    }

    private async void ShowMissingWorkerAsync() => await ShowMessageAsync("功能尚未接入", MissingWorkerService.MissingMessage);

    private async void ShowVoiceDialogAsync()
    {
        var content = new StackPanel { Spacing = 12, Width = 460 };
        content.Children.Add(new TextBox { Header = "模板名称", PlaceholderText = "例如：盼盼 · 自然口播" });
        content.Children.Add(new Button { Content = "选择参考音…" });
        content.Children.Add(new TextBox { Header = "参考音频里说了什么" });
        content.Children.Add(new TextBox { Header = "备注", PlaceholderText = "例如：日常短视频 / 轻松自然" });
        content.Children.Add(new CheckBox { Content = "设为默认人声" });
        var dialog = new ContentDialog { Title = "新建人声模板", Content = content, PrimaryButtonText = "保存模板", CloseButtonText = "取消", XamlRoot = Root.XamlRoot };
        var result = await dialog.ShowAsync();
        if (result == ContentDialogResult.Primary) await ShowMessageAsync("尚未保存", MissingWorkerService.MissingMessage);
    }

    private async Task ShowMessageAsync(string title, string message)
    {
        var dialog = new ContentDialog { Title = title, Content = message, CloseButtonText = "知道了", XamlRoot = Root.XamlRoot };
        await dialog.ShowAsync();
    }
}
