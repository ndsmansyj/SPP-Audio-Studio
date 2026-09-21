import SwiftUI
import AppKit
import AVFoundation
import UniformTypeIdentifiers

enum SidebarItem: String, CaseIterable, Identifiable {
    case workbench = "工作台"
    case convert = "网易云转换"
    case separate = "人声分离"
    case clone = "声音克隆"
    case environment = "模型与环境"

    var id: String { rawValue }
    var icon: String {
        switch self {
        case .workbench: return "square.grid.2x2"
        case .convert: return "arrow.triangle.2.circlepath"
        case .separate: return "waveform.path.ecg"
        case .clone: return "waveform.badge.plus"
        case .environment: return "cpu"
        }
    }
}

enum ToolMode: String, CaseIterable, Identifiable {
    case convert = "仅转换"
    case separate = "仅分离"
    case convertAndSeparate = "转换 + 分离"
    var id: String { rawValue }
}

struct TaskItem: Identifiable {
    let id = UUID()
    let title: String
    let mode: String
    var status: String
    var detail: String
    var outputPath: String?
}
struct VoiceTemplate: Identifiable, Hashable {
    let id: String
    let name: String
    let referenceText: String
    let note: String
    let isDefault: Bool
}

enum WorkerError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let value): return value }
    }
}

final class AppState: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published var tasks: [TaskItem] = []
    @Published var doctorChecks: [String: Bool] = [:]
    @Published var doctorOK = false
    @Published var voices: [VoiceTemplate] = []
    @Published var voiceBusy = false
    @Published var voiceStatus = ""
    @Published var lastVoiceOutput: String?

    @Published var downloadSource = "auto"
    @Published var qwenInstalled = false
    @Published var qwenModelSource = "missing"
    @Published var qwenModelPath = ""
    @Published var melInstalled = false
    @Published var melModelSource = "missing"
    @Published var melModelPath = ""
    @Published var qwenRuntimeInstalled = false
    @Published var qwenRuntimeSource = "missing"
    @Published var separatorInstalled = false
    @Published var separatorSource = "missing"
    @Published var modelBusy: String?
    @Published var modelStatusMessage = ""

    @Published var previewPath: String?
    @Published var previewIsPlaying = false
    @Published var previewStatus = ""
    private var previewPlayer: AVAudioPlayer?

    override init() {
        super.init()
    }

    private var workerURL: URL? {
        Bundle.main.url(forResource: "spp_worker", withExtension: "py", subdirectory: "worker")
    }

    private var bundledNCM: URL? {
        Bundle.main.url(forResource: "ncm_converter", withExtension: nil, subdirectory: "bin")
    }

    private var bundledDefaultVoice: URL? {
        Bundle.main.resourceURL?.appendingPathComponent("default_voice", isDirectory: true)
    }

    private func workerEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env.removeValue(forKey: "PYTHONPATH")
        if let ncm = bundledNCM { env["SPP_NCM_BIN"] = ncm.path }
        if let voice = bundledDefaultVoice { env["SPP_DEFAULT_VOICE_DIR"] = voice.path }
        return env
    }
    private func runWorkerSync(_ arguments: [String]) throws -> [String: Any] {
        guard let worker = workerURL else {
            throw WorkerError.message("App 内缺少统一 Worker")
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [worker.path] + arguments
        process.environment = workerEnvironment()

        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr
        try process.run()
        process.waitUntilExit()

        let outData = stdout.fileHandleForReading.readDataToEndOfFile()
        let errData = stderr.fileHandleForReading.readDataToEndOfFile()
        let outText = String(data: outData, encoding: .utf8) ?? ""
        let errText = String(data: errData, encoding: .utf8) ?? ""
        let jsonLine = outText.split(separator: "\n").map(String.init).last { $0.hasPrefix("{") }

        if let line = jsonLine,
           let data = line.data(using: .utf8),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            if (json["ok"] as? Bool) == false {
                throw WorkerError.message((json["error"] as? String) ?? "任务失败")
            }
            return json
        }
        throw WorkerError.message((errText + "\n" + outText).trimmingCharacters(in: .whitespacesAndNewlines))
    }

    private func mutateTask(_ id: UUID, status: String, detail: String, output: String? = nil) {
        DispatchQueue.main.async {
            guard let index = self.tasks.firstIndex(where: { $0.id == id }) else { return }
            self.tasks[index].status = status
            self.tasks[index].detail = detail
            if let output { self.tasks[index].outputPath = output }
        }
    }
    func process(
        _ urls: [URL],
        mode: ToolMode,
        outputMode: String = "beside",
        customOutputDir: String = "",
        keep: String = "instrumental"
    ) {
        guard !urls.isEmpty else { return }
        let items: [(URL, UUID)] = urls.map { url in
            let item = TaskItem(title: url.lastPathComponent, mode: mode.rawValue, status: "等待中", detail: "", outputPath: nil)
            tasks.insert(item, at: 0)
            return (url, item.id)
        }
        DispatchQueue.global(qos: .userInitiated).async {
            for (url, id) in items {
                self.mutateTask(id, status: "处理中", detail: "正在执行…")
                do {
                    let output = try self.processOne(
                        url,
                        mode: mode,
                        outputMode: outputMode,
                        customOutputDir: customOutputDir,
                        keep: keep
                    )
                    self.mutateTask(id, status: "完成", detail: "已完成", output: output)
                } catch {
                    self.mutateTask(id, status: "失败", detail: error.localizedDescription)
                }
            }
        }
    }

    private func effectiveOutputDir(for source: URL, outputMode: String, customOutputDir: String) -> String {
        if outputMode == "custom" && !customOutputDir.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return NSString(string: customOutputDir).expandingTildeInPath
        }
        return source.deletingLastPathComponent().path
    }

    private func processOne(
        _ url: URL,
        mode: ToolMode,
        outputMode: String,
        customOutputDir: String,
        keep: String
    ) throws -> String? {
        let targetDir = effectiveOutputDir(for: url, outputMode: outputMode, customOutputDir: customOutputDir)

        switch mode {
        case .convert:
            guard url.pathExtension.lowercased() == "ncm" else {
                throw WorkerError.message("仅转换模式当前用于 .ncm")
            }
            let json = try runWorkerSync(["convert", url.path, "--output-dir", targetDir])
            return json["output"] as? String

        case .separate:
            let json = try runWorkerSync([
                "separate", url.path,
                "--output-dir", targetDir,
                "--keep", keep
            ])
            return (json["instrumental"] as? String) ?? (json["vocals"] as? String)

        case .convertAndSeparate:
            if url.pathExtension.lowercased() == "ncm" {
                let converted = try runWorkerSync(["convert", url.path, "--output-dir", targetDir])
                guard let audio = converted["output"] as? String else {
                    throw WorkerError.message("转换完成但没有拿到输出路径")
                }
                let separated = try runWorkerSync([
                    "separate", audio,
                    "--output-dir", targetDir,
                    "--keep", keep
                ])
                return (separated["instrumental"] as? String) ?? (separated["vocals"] as? String) ?? audio
            } else {
                let separated = try runWorkerSync([
                    "separate", url.path,
                    "--output-dir", targetDir,
                    "--keep", keep
                ])
                return (separated["instrumental"] as? String) ?? (separated["vocals"] as? String) ?? url.path
            }
        }
    }
    func refreshDoctor() {
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let json = try self.runWorkerSync(["doctor"])
                let checks = json["checks"] as? [String: Bool] ?? [:]
                DispatchQueue.main.async {
                    self.doctorChecks = checks
                    self.doctorOK = json["ready"] as? Bool ?? false
                }
            } catch {
                DispatchQueue.main.async {
                    self.doctorOK = false
                    self.doctorChecks = ["worker": false]
                }
            }
        }
    }

    func refreshModels() {
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let json = try self.runWorkerSync(["model-status"])
                let models = json["models"] as? [String: Any] ?? [:]
                let runtime = json["runtime"] as? [String: Any] ?? [:]
                let qwen = models["qwen"] as? [String: Any] ?? [:]
                let mel = models["mel_deux"] as? [String: Any] ?? [:]
                let qrun = runtime["qwen_python"] as? [String: Any] ?? [:]
                let sep = runtime["separator"] as? [String: Any] ?? [:]
                DispatchQueue.main.async {
                    self.downloadSource = json["download_source"] as? String ?? "auto"
                    self.qwenInstalled = qwen["installed"] as? Bool ?? false
                    self.qwenModelSource = qwen["source"] as? String ?? "missing"
                    self.qwenModelPath = qwen["path"] as? String ?? ""
                    self.melInstalled = mel["installed"] as? Bool ?? false
                    self.melModelSource = mel["source"] as? String ?? "missing"
                    self.melModelPath = mel["path"] as? String ?? ""
                    self.qwenRuntimeInstalled = qrun["installed"] as? Bool ?? false
                    self.qwenRuntimeSource = qrun["source"] as? String ?? "missing"
                    self.separatorInstalled = sep["installed"] as? Bool ?? false
                    self.separatorSource = sep["source"] as? String ?? "missing"
                }
            } catch {
                DispatchQueue.main.async {
                    self.modelStatusMessage = error.localizedDescription
                }
            }
        }
    }

    func setDownloadSource(_ source: String) {
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                _ = try self.runWorkerSync(["download-source", source])
                DispatchQueue.main.async { self.downloadSource = source }
            } catch {
                DispatchQueue.main.async { self.modelStatusMessage = error.localizedDescription }
            }
        }
    }

    func linkModel(kind: String, url: URL) {
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let flag = kind == "qwen" ? "--qwen-model-dir" : "--mel-model-dir"
                _ = try self.runWorkerSync(["model-link", flag, url.path])
                DispatchQueue.main.async { self.modelStatusMessage = "已链接本地模型" }
                self.refreshModels()
                self.refreshDoctor()
            } catch {
                DispatchQueue.main.async { self.modelStatusMessage = error.localizedDescription }
            }
        }
    }

    func downloadModel(_ kind: String) {
        guard modelBusy == nil else { return }
        modelBusy = kind
        modelStatusMessage = kind == "qwen" ? "正在下载 Qwen3-TTS…" : "正在下载 Mel-Deux…"
        let source = downloadSource
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                _ = try self.runWorkerSync(["model-download", kind, "--source", source])
                DispatchQueue.main.async {
                    self.modelBusy = nil
                    self.modelStatusMessage = "模型下载完成"
                }
                self.refreshModels()
                self.refreshDoctor()
            } catch {
                DispatchQueue.main.async {
                    self.modelBusy = nil
                    self.modelStatusMessage = error.localizedDescription
                }
            }
        }
    }

    func seedDefaultVoice() {
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                _ = try self.runWorkerSync(["seed-default-voice"])
                self.refreshVoices()
            } catch {
                DispatchQueue.main.async { self.voiceStatus = error.localizedDescription }
            }
        }
    }

    func refreshVoices() {
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let json = try self.runWorkerSync(["voice-list"])
                let rows = json["templates"] as? [[String: Any]] ?? []
                let values = rows.map {
                    VoiceTemplate(
                        id: $0["id"] as? String ?? UUID().uuidString,
                        name: $0["name"] as? String ?? "未命名",
                        referenceText: $0["reference_text"] as? String ?? "",
                        note: $0["note"] as? String ?? "",
                        isDefault: $0["default"] as? Bool ?? false
                    )
                }
                DispatchQueue.main.async { self.voices = values }
            } catch {
                DispatchQueue.main.async { self.voiceStatus = error.localizedDescription }
            }
        }
    }
    func saveVoice(name: String, ref: URL, refText: String, note: String, makeDefault: Bool) {
        DispatchQueue.global(qos: .userInitiated).async {
            var args = ["voice-save", "--name", name, "--ref-audio", ref.path]
            if !refText.isEmpty { args += ["--ref-text", refText] }
            if !note.isEmpty { args += ["--note", note] }
            if makeDefault { args.append("--default") }
            do {
                _ = try self.runWorkerSync(args)
                DispatchQueue.main.async { self.voiceStatus = "人声模板已保存" }
                self.refreshVoices()
            } catch {
                DispatchQueue.main.async { self.voiceStatus = error.localizedDescription }
            }
        }
    }

    func setDefaultVoice(_ id: String) {
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                _ = try self.runWorkerSync(["voice-default", id])
                self.refreshVoices()
            } catch {
                DispatchQueue.main.async { self.voiceStatus = error.localizedDescription }
            }
        }
    }

    func clone(templateID: String?, temporaryRef: URL?, text: String, outputDir: String = "") {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            voiceStatus = "先输入需要生成的文案"
            return
        }
        voiceBusy = true
        voiceStatus = "正在生成…"
        lastVoiceOutput = nil
        let expandedOutput = NSString(string: outputDir).expandingTildeInPath
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let json: [String: Any]
                if let ref = temporaryRef {
                    var args = ["clone", "--ref-audio", ref.path, "--text", text]
                    if !expandedOutput.isEmpty { args += ["--output-dir", expandedOutput] }
                    json = try self.runWorkerSync(args)
                } else if let id = templateID {
                    var args = ["voice-clone", id, "--text", text]
                    if !expandedOutput.isEmpty { args += ["--output-dir", expandedOutput] }
                    json = try self.runWorkerSync(args)
                } else if let first = self.voices.first(where: { $0.isDefault }) ?? self.voices.first {
                    var args = ["voice-clone", first.id, "--text", text]
                    if !expandedOutput.isEmpty { args += ["--output-dir", expandedOutput] }
                    json = try self.runWorkerSync(args)
                } else {
                    throw WorkerError.message("先建立一个人声模板，或选择临时参考音")
                }
                DispatchQueue.main.async {
                    self.voiceBusy = false
                    self.voiceStatus = "生成完成"
                    self.lastVoiceOutput = json["path"] as? String
                }
            } catch {
                DispatchQueue.main.async {
                    self.voiceBusy = false
                    self.voiceStatus = error.localizedDescription
                }
            }
        }
    }

    func togglePreview(_ path: String?) {
        guard let path, FileManager.default.fileExists(atPath: path) else {
            previewStatus = "试听文件不存在"
            return
        }

        if previewPath == path, let player = previewPlayer {
            if player.isPlaying {
                player.pause()
                previewIsPlaying = false
                previewStatus = "已暂停"
            } else {
                player.play()
                previewIsPlaying = true
                previewStatus = "正在试听"
            }
            return
        }

        previewPlayer?.stop()
        do {
            let player = try AVAudioPlayer(contentsOf: URL(fileURLWithPath: path))
            player.delegate = self
            player.prepareToPlay()
            guard player.play() else {
                throw WorkerError.message("无法播放这个音频文件")
            }
            previewPlayer = player
            previewPath = path
            previewIsPlaying = true
            previewStatus = "正在试听"
        } catch {
            previewPlayer = nil
            previewPath = nil
            previewIsPlaying = false
            previewStatus = "试听失败：" + error.localizedDescription
        }
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        DispatchQueue.main.async {
            self.previewIsPlaying = false
            self.previewStatus = flag ? "试听完成" : "试听中断"
        }
    }

    func reveal(_ path: String?) {
        guard let path else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
    }
}

@main
struct SPPAudioStudioApp: App {
    @StateObject private var state = AppState()
    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(state)
                .frame(minWidth: 1040, minHeight: 720)
                .preferredColorScheme(.dark)
                .onAppear {
                    state.refreshDoctor()
                    state.refreshModels()
                    state.seedDefaultVoice()
                }
        }
        .windowStyle(.automatic)
    }
}
struct RootView: View {
    @State private var selection: SidebarItem? = .workbench

    var body: some View {
        NavigationSplitView {
            List(SidebarItem.allCases, selection: $selection) { item in
                Label(item.rawValue, systemImage: item.icon)
                    .tag(item)
                    .padding(.vertical, 5)
            }
            .navigationSplitViewColumnWidth(min: 190, ideal: 215)
        } detail: {
            Group {
                switch selection ?? .workbench {
                case .workbench: WorkbenchView()
                case .convert: FileToolView(mode: .convert, title: "网易云转换", subtitle: "NCM → 原始 MP3 / FLAC")
                case .separate: FileToolView(mode: .separate, title: "人声分离", subtitle: "Mel-Deux · 去人声 / 提取人声 / 双轨输出")
                case .clone: VoiceCloneView()
                case .environment: EnvironmentView()
                }
            }
            .padding(26)
        }
    }
}

private let supportedAudioExtensions: Set<String> = [
    "ncm", "mp3", "flac", "wav", "m4a", "aac", "aif", "aiff", "caf"
]

private func droppedFileURL(from item: Any?) -> URL? {
    if let url = item as? URL { return url }
    if let nsurl = item as? NSURL { return nsurl as URL }
    if let data = item as? Data {
        return URL(dataRepresentation: data, relativeTo: nil)
    }
    if let text = item as? String {
        if let url = URL(string: text), url.isFileURL { return url }
        return URL(fileURLWithPath: text)
    }
    return nil
}

private func isSupportedAudio(_ url: URL) -> Bool {
    supportedAudioExtensions.contains(url.pathExtension.lowercased())
}

private func isSupported(_ url: URL, for mode: ToolMode) -> Bool {
    guard isSupportedAudio(url) else { return false }
    let ext = url.pathExtension.lowercased()
    switch mode {
    case .convert:
        return ext == "ncm"
    case .separate:
        return ext != "ncm"
    case .convertAndSeparate:
        return true
    }
}

struct OutputDestinationControls: View {
    @Binding var mode: String
    @Binding var directory: String
    let primaryValue: String
    let primaryLabel: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("导出位置").font(.headline)
            Picker("导出位置", selection: $mode) {
                Text(primaryLabel).tag(primaryValue)
                Text("自定义目录").tag("custom")
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 430)

            if mode == "custom" {
                HStack {
                    Text(directory.isEmpty ? "还没选择目录" : directory)
                        .font(.caption)
                        .foregroundStyle(directory.isEmpty ? .secondary : .primary)
                        .lineLimit(1)
                        .textSelection(.enabled)
                    Spacer()
                    Button("选择目录…") { chooseDirectory() }
                }
            }
        }
    }

    private func chooseDirectory() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "选择"
        if panel.runModal() == .OK, let url = panel.url {
            directory = url.path
        }
    }
}

struct WorkbenchView: View {
    @EnvironmentObject var state: AppState
    @State private var files: [URL] = []
    @State private var mode: ToolMode = .convertAndSeparate
    @State private var isTargeted = false
    @AppStorage("workbench.outputMode") private var outputMode = "beside"
    @AppStorage("workbench.outputDir") private var outputDir = ""
    @AppStorage("workbench.keep") private var keep = "instrumental"

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HeaderBlock(title: "音频工作台", subtitle: "转换音乐、人声分离、克隆声音。常用操作尽量一处完成。")
            Picker("模式", selection: $mode) {
                ForEach(ToolMode.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 520)

            if mode != .convert {
                Picker("分离输出", selection: $keep) {
                    Text("仅伴奏（去人声）").tag("instrumental")
                    Text("仅人声").tag("vocals")
                    Text("人声 + 伴奏").tag("both")
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 520)
            }

            OutputDestinationControls(
                mode: $outputMode,
                directory: $outputDir,
                primaryValue: "beside",
                primaryLabel: "源文件旁边"
            )

            dropZone
            HStack {
                Button("选择文件…") { chooseFiles() }
                Button("开始处理") {
                    state.process(
                        files,
                        mode: mode,
                        outputMode: outputMode,
                        customOutputDir: outputDir,
                        keep: keep
                    )
                }
                    .buttonStyle(.borderedProminent)
                    .disabled(files.isEmpty || (outputMode == "custom" && outputDir.isEmpty))
                if !files.isEmpty {
                    Button("清空列表") { files.removeAll() }
                }
                Spacer()
                statusBadge
            }
            if !files.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    Text("待处理").font(.headline)
                    ForEach(files, id: \.path) { url in
                        HStack {
                            Image(systemName: url.pathExtension.lowercased() == "ncm" ? "music.note.list" : "waveform")
                            Text(url.lastPathComponent).lineLimit(1)
                            Spacer()
                            Text(url.pathExtension.uppercased()).foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 3)
                    }
                }
            }

            Divider()
            Text("任务").font(.headline)
            TaskListView()
            Spacer(minLength: 0)
        }
    }

    private var statusBadge: some View {
        Label(state.doctorOK ? "本地引擎就绪" : "环境需检查",
              systemImage: state.doctorOK ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
            .foregroundStyle(state.doctorOK ? .green : .orange)
            .font(.subheadline)
    }

    private var dropZone: some View {
        RoundedRectangle(cornerRadius: 18)
            .fill(isTargeted ? Color.accentColor.opacity(0.12) : Color.white.opacity(0.035))
            .overlay(
                RoundedRectangle(cornerRadius: 18)
                    .strokeBorder(isTargeted ? Color.accentColor : Color.white.opacity(0.16),
                                  style: StrokeStyle(lineWidth: 1.2, dash: [8, 7]))
            )
            .overlay(
                VStack(spacing: 9) {
                    Image(systemName: "plus.circle").font(.system(size: 32, weight: .light))
                    Text("把 NCM / FLAC / MP3 / WAV / M4A 拖到这里").font(.headline)
                    Text(mode == .convertAndSeparate ? "NCM 会自动先转换，再进入 Mel-Deux；普通音频直接分离。" : "支持批量加入任务队列")
                        .font(.caption).foregroundStyle(.secondary)
                }
            )
            .frame(height: 180)
            .onDrop(of: [UTType.fileURL.identifier], isTargeted: $isTargeted) { providers in
                for provider in providers {
                    provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                        guard let url = droppedFileURL(from: item), isSupported(url, for: mode) else { return }
                        DispatchQueue.main.async {
                            if !files.contains(url) { files.append(url) }
                        }
                    }
                }
                return true
            }
    }

    private func chooseFiles() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        if panel.runModal() == .OK {
            files = panel.urls.filter { isSupported($0, for: mode) }
        }
    }
}

struct FileToolView: View {
    @EnvironmentObject var state: AppState
    let mode: ToolMode
    let title: String
    let subtitle: String

    @State private var files: [URL] = []
    @State private var isTargeted = false

    @AppStorage("convert.outputMode") private var convertOutputMode = "beside"
    @AppStorage("convert.outputDir") private var convertOutputDir = ""
    @AppStorage("separate.outputMode") private var separateOutputMode = "beside"
    @AppStorage("separate.outputDir") private var separateOutputDir = ""
    @AppStorage("separate.keep") private var separateKeep = "instrumental"

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HeaderBlock(title: title, subtitle: subtitle)

            if mode == .separate {
                Picker("保留内容", selection: $separateKeep) {
                    Text("仅伴奏（去人声）").tag("instrumental")
                    Text("仅人声").tag("vocals")
                    Text("人声 + 伴奏").tag("both")
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 520)
            }

            OutputDestinationControls(
                mode: outputModeBinding,
                directory: outputDirBinding,
                primaryValue: "beside",
                primaryLabel: "源文件旁边"
            )

            RoundedRectangle(cornerRadius: 18)
                .fill(isTargeted ? Color.accentColor.opacity(0.12) : Color.white.opacity(0.035))
                .overlay(
                    RoundedRectangle(cornerRadius: 18)
                        .strokeBorder(
                            isTargeted ? Color.accentColor : Color.white.opacity(0.12),
                            style: StrokeStyle(lineWidth: 1.2, dash: [8, 7])
                        )
                )
                .overlay(
                    VStack(spacing: 10) {
                        Image(systemName: mode == .convert ? "arrow.triangle.2.circlepath" : "waveform.path.ecg")
                            .font(.system(size: 34, weight: .light))
                        Text(files.isEmpty ? "把文件拖到这里" : "已选择 \(files.count) 个文件")
                            .font(.headline)
                        Text(mode == .convert
                             ? "支持批量 .ncm"
                             : "支持 MP3 / FLAC / WAV / M4A 等音频")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Button("选择文件…") { chooseFiles() }
                    }
                )
                .frame(height: 190)
                .onDrop(of: [UTType.fileURL.identifier], isTargeted: $isTargeted) { providers in
                    handleDrop(providers)
                }

            if !files.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(files, id: \.path) { url in
                        HStack {
                            Image(systemName: "waveform")
                            Text(url.lastPathComponent).lineLimit(1)
                            Spacer()
                            Button {
                                files.removeAll { $0 == url }
                            } label: {
                                Image(systemName: "xmark.circle")
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }

            HStack {
                Button(mode.rawValue) {
                    state.process(
                        files,
                        mode: mode,
                        outputMode: currentOutputMode,
                        customOutputDir: currentOutputDir,
                        keep: mode == .separate ? separateKeep : "instrumental"
                    )
                }
                .buttonStyle(.borderedProminent)
                .disabled(files.isEmpty || (currentOutputMode == "custom" && currentOutputDir.isEmpty))

                if !files.isEmpty {
                    Button("清空") { files.removeAll() }
                }
                Spacer()
            }

            TaskListView()
            Spacer()
        }
    }

    private var currentOutputMode: String {
        mode == .convert ? convertOutputMode : separateOutputMode
    }

    private var currentOutputDir: String {
        mode == .convert ? convertOutputDir : separateOutputDir
    }

    private var outputModeBinding: Binding<String> {
        Binding(
            get: { currentOutputMode },
            set: {
                if mode == .convert { convertOutputMode = $0 }
                else { separateOutputMode = $0 }
            }
        )
    }

    private var outputDirBinding: Binding<String> {
        Binding(
            get: { currentOutputDir },
            set: {
                if mode == .convert { convertOutputDir = $0 }
                else { separateOutputDir = $0 }
            }
        )
    }

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        for provider in providers {
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                guard let url = droppedFileURL(from: item), isSupported(url, for: mode) else { return }
                DispatchQueue.main.async {
                    guard !self.files.contains(url) else { return }
                    self.files.append(url)
                }
            }
        }
        return true
    }

    private func chooseFiles() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        if panel.runModal() == .OK {
            files = panel.urls.filter { isSupported($0, for: mode) }
        }
    }
}

struct TaskListView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        if state.tasks.isEmpty {
            ContentUnavailableView("还没有任务", systemImage: "tray", description: Text("处理过的任务会显示在这里。"))
                .frame(maxHeight: 180)
        } else {
            List(state.tasks.prefix(20)) { task in
                HStack(spacing: 12) {
                    Image(systemName: icon(for: task.status))
                        .foregroundStyle(color(for: task.status))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(task.title).lineLimit(1)
                        Text(task.detail.isEmpty ? task.mode : task.detail)
                            .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                    Spacer()
                    Text(task.status).font(.caption).foregroundStyle(.secondary)
                    if task.outputPath != nil {
                        Button {
                            state.togglePreview(task.outputPath)
                        } label: {
                            Image(systemName:
                                state.previewPath == task.outputPath && state.previewIsPlaying
                                ? "pause.circle.fill"
                                : "play.circle"
                            )
                        }
                        .buttonStyle(.plain)
                        .help(state.previewPath == task.outputPath && state.previewIsPlaying ? "暂停试听" : "试听")

                        Button {
                            state.reveal(task.outputPath)
                        } label: {
                            Image(systemName: "folder")
                        }
                        .buttonStyle(.plain)
                        .help("在 Finder 中显示")
                    }
                }
                .padding(.vertical, 4)
            }
            .listStyle(.inset)
            .frame(minHeight: 190)
        }
    }

    private func icon(for status: String) -> String {
        switch status {
        case "完成": return "checkmark.circle.fill"
        case "失败": return "xmark.circle.fill"
        case "处理中": return "hourglass.circle.fill"
        default: return "circle"
        }
    }

    private func color(for status: String) -> Color {
        switch status {
        case "完成": return .green
        case "失败": return .red
        case "处理中": return .blue
        default: return .secondary
        }
    }
}

struct HeaderBlock: View {
    let title: String
    let subtitle: String

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.system(size: 28, weight: .bold))
            Text(subtitle).foregroundStyle(.secondary)
        }
    }
}
struct VoiceCloneView: View {
    @EnvironmentObject var state: AppState
    @State private var selectedVoice: String?
    @State private var temporaryRef: URL?
    @State private var text = "大家好，我是宋盼盼。这是一段 SPP Audio Studio 的声音克隆测试。如果你能自然地听到这句话，说明人声模板、模型和本地推理都已经正常工作。"
    @State private var showAdd = false
    @AppStorage("clone.outputMode") private var outputMode = "default"
    @AppStorage("clone.outputDir") private var outputDir = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                HeaderBlock(title: "声音克隆", subtitle: "常用人声保存一次，以后选模板、粘文案、直接生成。")
                Spacer()
                Button("＋ 新建人声模板") { showAdd = true }
            }

            Text("常用人声").font(.headline)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 12) {
                    ForEach(state.voices) { voice in
                        voiceCard(voice)
                    }
                    Button {
                        chooseTemporaryRef()
                    } label: {
                        VStack(alignment: .leading, spacing: 8) {
                            Image(systemName: "plus.circle").font(.title2)
                            Text("临时参考音").font(.headline)
                            Text(temporaryRef?.lastPathComponent ?? "偶尔用的声音，不保存模板")
                                .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                        .frame(width: 170, height: 95, alignment: .leading)
                        .padding(14)
                        .background(Color.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 15))
                        .overlay(RoundedRectangle(cornerRadius: 15).strokeBorder(Color.white.opacity(0.12)))
                    }
                    .buttonStyle(.plain)
                }
            }

            Text("生成文案").font(.headline)
            TextEditor(text: $text)
                .font(.system(size: 16))
                .scrollContentBackground(.hidden)
                .padding(10)
                .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 12))
                .frame(minHeight: 180)

            VStack(alignment: .leading, spacing: 8) {
                Text("导出位置").font(.headline)
                Picker("导出位置", selection: $outputMode) {
                    Text("App 默认目录").tag("default")
                    if temporaryRef != nil {
                        Text("参考音频旁边").tag("reference")
                    }
                    Text("自定义目录").tag("custom")
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: temporaryRef == nil ? 430 : 620)

                if outputMode == "custom" {
                    HStack {
                        Text(outputDir.isEmpty ? "还没选择目录" : outputDir)
                            .font(.caption)
                            .foregroundStyle(outputDir.isEmpty ? .secondary : .primary)
                            .lineLimit(1)
                            .textSelection(.enabled)
                        Spacer()
                        Button("选择目录…") { chooseOutputDirectory() }
                    }
                }
            }

            HStack(spacing: 12) {
                Button {
                    let id = temporaryRef == nil ? effectiveVoiceID : nil
                    state.clone(
                        templateID: id,
                        temporaryRef: temporaryRef,
                        text: text,
                        outputDir: effectiveCloneOutputDir
                    )
                } label: {
                    if state.voiceBusy {
                        ProgressView().controlSize(.small)
                        Text("生成中…")
                    } else {
                        Label("生成声音", systemImage: "waveform.badge.plus")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(state.voiceBusy || (outputMode == "custom" && outputDir.isEmpty))

                if let output = state.lastVoiceOutput {
                    Button {
                        state.togglePreview(output)
                    } label: {
                        Label(
                            state.previewPath == output && state.previewIsPlaying ? "暂停" : "试听",
                            systemImage: state.previewPath == output && state.previewIsPlaying
                                ? "pause.fill"
                                : "play.fill"
                        )
                    }
                    Button("在 Finder 中显示") { state.reveal(output) }
                }
                Text(state.voiceStatus).font(.caption).foregroundStyle(.secondary)
                Spacer()
            }

            if let selected = selectedTemplate {
                Divider()
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("当前模板：\(selected.name)").font(.headline)
                        if !selected.referenceText.isEmpty {
                            Text("参考文本：\(selected.referenceText)")
                                .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                        if !selected.note.isEmpty {
                            Text(selected.note).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    if !selected.isDefault {
                        Button("设为默认") { state.setDefaultVoice(selected.id) }
                    } else {
                        Label("默认", systemImage: "pin.fill").foregroundStyle(.secondary)
                    }
                }
            }
            Spacer()
        }
        .sheet(isPresented: $showAdd) {
            AddVoiceSheet(isPresented: $showAdd)
                .environmentObject(state)
        }
        .onAppear {
            state.refreshVoices()
            if selectedVoice == nil { selectedVoice = effectiveVoiceID }
            if outputMode == "reference" && temporaryRef == nil { outputMode = "default" }
        }
    }

    private var effectiveVoiceID: String? {
        selectedVoice ?? state.voices.first(where: { $0.isDefault })?.id ?? state.voices.first?.id
    }

    private var selectedTemplate: VoiceTemplate? {
        guard temporaryRef == nil, let id = effectiveVoiceID else { return nil }
        return state.voices.first(where: { $0.id == id })
    }

    private var effectiveCloneOutputDir: String {
        if outputMode == "custom" {
            return outputDir
        }
        if outputMode == "reference", let ref = temporaryRef {
            return ref.deletingLastPathComponent().path
        }
        return ""
    }

    private func chooseOutputDirectory() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "选择"
        if panel.runModal() == .OK, let url = panel.url {
            outputDir = url.path
        }
    }

    private func chooseTemporaryRef() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        if panel.runModal() == .OK, let url = panel.url,
           isSupportedAudio(url), url.pathExtension.lowercased() != "ncm" {
            temporaryRef = url
            selectedVoice = nil
        }
    }
    private func voiceCard(_ voice: VoiceTemplate) -> some View {
        let active = temporaryRef == nil && effectiveVoiceID == voice.id
        return Button {
            temporaryRef = nil
            selectedVoice = voice.id
            if outputMode == "reference" { outputMode = "default" }
        } label: {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Circle()
                        .fill(Color.white.opacity(0.09))
                        .frame(width: 34, height: 34)
                        .overlay(Text(String(voice.name.prefix(1))).fontWeight(.bold))
                    Spacer()
                    if voice.isDefault {
                        Image(systemName: "pin.fill").font(.caption).foregroundStyle(.secondary)
                    }
                }
                Text(voice.name).font(.headline).lineLimit(1)
                Text(voice.note.isEmpty ? "Qwen TTS 人声模板" : voice.note)
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            .frame(width: 170, height: 95, alignment: .leading)
            .padding(14)
            .background(active ? Color.accentColor.opacity(0.16) : Color.white.opacity(0.035),
                        in: RoundedRectangle(cornerRadius: 15))
            .overlay(
                RoundedRectangle(cornerRadius: 15)
                    .strokeBorder(active ? Color.accentColor : Color.white.opacity(0.12))
            )
        }
        .buttonStyle(.plain)
    }
}

struct AddVoiceSheet: View {
    @EnvironmentObject var state: AppState
    @Binding var isPresented: Bool
    @State private var name = ""
    @State private var refText = ""
    @State private var note = ""
    @State private var refURL: URL?
    @State private var makeDefault = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("新建人声模板").font(.title2.bold())
            TextField("模板名称，例如：盼盼 · 自然口播", text: $name)
            HStack {
                Text(refURL?.lastPathComponent ?? "还没选择参考音")
                    .foregroundStyle(refURL == nil ? .secondary : .primary)
                    .lineLimit(1)
                Spacer()
                Button("选择参考音…") { chooseRef() }
            }
            TextField("参考音频里说了什么（可留空自动识别）", text: $refText)
            TextField("备注，例如：日常短视频 / 轻松自然", text: $note)
            Toggle("设为默认人声", isOn: $makeDefault)
            Spacer()
            HStack {
                Spacer()
                Button("取消") { isPresented = false }
                Button("保存模板") {
                    if let refURL, !name.trimmingCharacters(in: .whitespaces).isEmpty {
                        state.saveVoice(name: name, ref: refURL, refText: refText, note: note, makeDefault: makeDefault)
                        isPresented = false
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(refURL == nil || name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(24)
        .frame(width: 520, height: 330)
    }

    private func chooseRef() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        if panel.runModal() == .OK, let url = panel.url,
           isSupportedAudio(url), url.pathExtension.lowercased() != "ncm" {
            refURL = url
        }
    }
}

struct EnvironmentView: View {
    @EnvironmentObject var state: AppState

    private let labels: [String: String] = [
        "ncm_binary": "NCM 原生转换器",
        "separator_binary": "Mel-Deux 分离引擎",
        "mel_model": "Mel-Deux 模型",
        "mel_config": "Mel-Deux 配置",
        "qwen_python": "Qwen Python 环境",
        "qwen_model": "Qwen3-TTS 模型",
        "qwen_bridge": "Qwen 桥接层",
        "mel_ffmpeg": "Mel-Deux FFmpeg",
        "asr_python": "Whisper ASR 环境",
        "asr_model": "Whisper 模型"
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack {
                    HeaderBlock(title: "模型与环境", subtitle: "本机已有模型可直接链接；新机器可由 App 下载并管理。")
                    Spacer()
                    Button("重新检查") {
                        state.refreshDoctor()
                        state.refreshModels()
                    }
                }

                HStack(spacing: 10) {
                    Image(systemName: state.doctorOK ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(state.doctorOK ? .green : .orange)
                        .font(.title2)
                    Text(state.doctorOK ? "当前环境全部就绪" : "有组件需要处理")
                        .font(.headline)
                    Spacer()
                    Picker("下载源", selection: Binding(
                        get: { state.downloadSource },
                        set: { state.setDownloadSource($0) }
                    )) {
                        Text("自动（镜像优先）").tag("auto")
                        Text("HF 镜像").tag("mirror")
                        Text("Hugging Face 官方").tag("official")
                    }
                    .pickerStyle(.menu)
                    .frame(width: 210)
                }

                HStack(alignment: .top, spacing: 14) {
                    modelCard(
                        kind: "qwen",
                        title: "Qwen3-TTS 1.7B 8bit",
                        subtitle: "声音克隆 · MLX",
                        size: "约 3.1 GB",
                        license: "Apache-2.0",
                        installed: state.qwenInstalled,
                        source: state.qwenModelSource,
                        path: state.qwenModelPath
                    )
                    modelCard(
                        kind: "mel",
                        title: "Mel-Deux",
                        subtitle: "人声 / 伴奏分离",
                        size: "约 435 MB",
                        license: "CC BY-NC 4.0",
                        installed: state.melInstalled,
                        source: state.melModelSource,
                        path: state.melModelPath
                    )
                }

                runtimeCard

                if !state.modelStatusMessage.isEmpty {
                    Text(state.modelStatusMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }

                Divider()

                DisclosureGroup("环境详细检查") {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 245), spacing: 12)], spacing: 12) {
                        ForEach(state.doctorChecks.keys.sorted(), id: \.self) { key in
                            let ok = state.doctorChecks[key] ?? false
                            HStack {
                                Image(systemName: ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                                    .foregroundStyle(ok ? .green : .red)
                                Text(labels[key] ?? key)
                                Spacer()
                                Text(ok ? "已就绪" : "缺失")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .padding(14)
                            .background(Color.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 12))
                        }
                    }
                    .padding(.top, 10)
                }

                Text("说明：模型下载到 App 自己的 Models 目录；“链接本地”只记录路径，不复制、不移动原模型。下载失败时会按所选策略自动切换镜像 / 官方源。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .onAppear { state.refreshModels() }
    }

    private func modelCard(
        kind: String,
        title: String,
        subtitle: String,
        size: String,
        license: String,
        installed: Bool,
        source: String,
        path: String
    ) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).font(.headline)
                    Text(subtitle).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                statusPill(installed: installed, source: source)
            }

            HStack(spacing: 16) {
                Label(size, systemImage: "internaldrive")
                Text(license)
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            Text(path.isEmpty ? "尚未配置" : path)
                .font(.caption2.monospaced())
                .foregroundStyle(.secondary)
                .lineLimit(2)
                .textSelection(.enabled)

            HStack {
                Button {
                    state.downloadModel(kind)
                } label: {
                    if state.modelBusy == kind {
                        ProgressView().controlSize(.small)
                        Text("下载中…")
                    } else {
                        Text(installed && source == "managed" ? "重新下载" : "下载到 App")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(state.modelBusy != nil)

                Button("链接本地…") { chooseModelFolder(kind: kind) }
                    .disabled(state.modelBusy != nil)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Color.white.opacity(0.10)))
    }

    private var runtimeCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("推理运行环境").font(.headline)
                Spacer()
                Text(runtimeSummary)
                    .font(.caption)
                    .foregroundStyle((state.qwenRuntimeInstalled && state.separatorInstalled) ? .green : .orange)
            }
            Text("Qwen 与 Mel-Deux 除了模型，还需要本地推理环境。你的机器当前直接链接现有环境；通用版会把这一层做成 App 管理的 Runtime，一次安装后离线使用。")
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack(spacing: 18) {
                Label("Qwen / MLX：\(sourceLabel(state.qwenRuntimeSource))",
                      systemImage: state.qwenRuntimeInstalled ? "checkmark.circle.fill" : "xmark.circle")
                Label("Mel Separator：\(sourceLabel(state.separatorSource))",
                      systemImage: state.separatorInstalled ? "checkmark.circle.fill" : "xmark.circle")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(16)
        .background(Color.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 14))
    }

    private var runtimeSummary: String {
        (state.qwenRuntimeInstalled && state.separatorInstalled) ? "已就绪" : "需要安装"
    }

    private func statusPill(installed: Bool, source: String) -> some View {
        Text(installed ? sourceLabel(source) : "未安装")
            .font(.caption.weight(.medium))
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .background(Color.white.opacity(0.07), in: Capsule())
            .foregroundStyle(installed ? .green : .orange)
    }

    private func sourceLabel(_ source: String) -> String {
        switch source {
        case "linked": return "已链接本机"
        case "managed": return "App 管理"
        case "legacy": return "自动发现本机"
        default: return "未配置"
        }
    }

    private func chooseModelFolder(kind: String) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "链接"
        panel.message = kind == "qwen"
            ? "选择 Qwen3-TTS-12Hz-1.7B-Base-8bit 模型目录"
            : "选择包含 becruily_deux.ckpt 的 Mel-Deux 模型目录"
        if panel.runModal() == .OK, let url = panel.url {
            state.linkModel(kind: kind, url: url)
        }
    }
}
