import SwiftUI
import Foundation
import AppKit
import AVFoundation
import UniformTypeIdentifiers

enum SidebarItem: String, CaseIterable, Identifiable {
    case workbench = "工作台"
    case convert = "格式转换"
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
    let isBundled: Bool
}

enum WorkerError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let value): return value }
    }
}


final class DiagnosticLogger {
    static let shared = DiagnosticLogger()

    let directory: URL
    private let maxBytes = 2 * 1024 * 1024
    private let generations = 5
    private let queue = DispatchQueue(label: "com.spp.audio-studio.logger")

    private init() {
        let base = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/SPP Audio Studio", isDirectory: true)
        directory = base
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
    }

    func sanitize(_ text: String) -> String {
        var value = text.replacingOccurrences(of: NSHomeDirectory(), with: "~")
        if let regex = try? NSRegularExpression(pattern: #"/Users/[^/\s]+"#) {
            let range = NSRange(value.startIndex..<value.endIndex, in: value)
            value = regex.stringByReplacingMatches(in: value, range: range, withTemplate: "~")
        }
        if let regex = try? NSRegularExpression(pattern: #"~/[^\s\"']+"#) {
            let range = NSRange(value.startIndex..<value.endIndex, in: value)
            value = regex.stringByReplacingMatches(in: value, range: range, withTemplate: "~/<redacted>")
        }
        return value
    }

    func append(_ file: String, _ message: String) {
        queue.async {
            let url = self.directory.appendingPathComponent(file)
            self.rotateIfNeeded(url)
            let formatter = ISO8601DateFormatter()
            let line = "[\(formatter.string(from: Date()))] \(self.sanitize(message))\n"
            guard let data = line.data(using: .utf8) else { return }
            if !FileManager.default.fileExists(atPath: url.path) {
                FileManager.default.createFile(atPath: url.path, contents: data)
                return
            }
            do {
                let handle = try FileHandle(forWritingTo: url)
                try handle.seekToEnd()
                try handle.write(contentsOf: data)
                try handle.close()
            } catch {}
        }
    }

    func tail(_ file: String, lines: Int = 20) -> [String] {
        let url = directory.appendingPathComponent(file)
        guard let data = try? Data(contentsOf: url),
              let text = String(data: data.suffix(64 * 1024), encoding: .utf8) else { return [] }
        return Array(text.split(separator: "\n").suffix(lines)).map(String.init)
    }

    private func rotateIfNeeded(_ url: URL) {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
              let size = attrs[.size] as? NSNumber,
              size.intValue >= maxBytes else { return }
        for index in stride(from: generations - 1, through: 1, by: -1) {
            let src = URL(fileURLWithPath: url.path + ".\(index)")
            let dst = URL(fileURLWithPath: url.path + ".\(index + 1)")
            if FileManager.default.fileExists(atPath: src.path) {
                try? FileManager.default.removeItem(at: dst)
                try? FileManager.default.moveItem(at: src, to: dst)
            }
        }
        let first = URL(fileURLWithPath: url.path + ".1")
        try? FileManager.default.removeItem(at: first)
        try? FileManager.default.moveItem(at: url, to: first)
    }
}

final class AppState: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published var tasks: [TaskItem] = []
    @Published var doctorChecks: [String: Bool] = [:]
    @Published var coreReady = false
    @Published var doctorOK = false
    @Published var voices: [VoiceTemplate] = []
    @Published var voiceBusy = false
    @Published var voiceStatus = ""
    @Published var lastVoiceOutput: String?

    @Published var downloadSource = "auto"
    @Published var whisperInstalled = false
    @Published var whisperModelSource = "missing"
    @Published var whisperModelPath = ""
    @Published var asrRuntimeInstalled = false
    @Published var asrRuntimeSource = "missing"
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
    @Published var batchBusy = false
    @Published var batchProgress = ""
    @Published var modelBusy: String?
    @Published var runtimeBusy: String?
    @Published var modelStatusMessage = ""

    @Published var previewPath: String?
    @Published var previewIsPlaying = false
    @Published var previewStatus = ""

    @Published var lastError = ""
    @Published var lastTaskSummary = "尚无任务"
    @Published var diagnosticStatus = ""

    private var previewPlayer: AVAudioPlayer?
    private let logger = DiagnosticLogger.shared
    private let taskExecutionQueue = DispatchQueue(label: "com.spp.audio-studio.task-execution", qos: .userInitiated)

    override init() {
        super.init()
        logger.append("app.log", "app launch version=\(appVersion)")
    }

    private var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown"
    }

    private var workerURL: URL? {
        Bundle.main.url(forResource: "spp_worker", withExtension: "py", subdirectory: "worker")
    }

    private var bundledFormatConverter: URL? {
        Bundle.main.url(forResource: "format_converter", withExtension: nil, subdirectory: "bin")
    }

    private var bundledDefaultVoice: URL? {
        Bundle.main.resourceURL?.appendingPathComponent("default_voice", isDirectory: true)
    }

    private var bundledPythonCore: URL? {
        Bundle.main.resourceURL?.appendingPathComponent("runtime/python/bin/python3.11")
    }

    private func workerEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env.removeValue(forKey: "PYTHONPATH")
        env.removeValue(forKey: "PYTHONHOME")
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if let converter = bundledFormatConverter { env["SPP_FORMAT_BIN"] = converter.path }
        if let voice = bundledDefaultVoice { env["SPP_DEFAULT_VOICE_DIR"] = voice.path }
        if let python = bundledPythonCore {
            env["SPP_PYTHON_CORE"] = python.path
            env["PATH"] = python.deletingLastPathComponent().path + ":/usr/bin:/bin:/usr/sbin:/sbin"
        }
        return env
    }
    private func recordError(_ message: String, context: String) {
        let safe = logger.sanitize(message)
        logger.append("app.log", "error context=\(context) message=\(safe)")
        DispatchQueue.main.async {
            self.lastError = safe
        }
    }

    private func runWorkerSync(_ arguments: [String]) throws -> [String: Any] {
        let command = arguments.first ?? "unknown"
        let logFile = (command.contains("download") || command.contains("runtime-install")) ? "install.log" : "worker.log"

        guard let worker = workerURL else {
            let message = "App 内缺少统一 Worker"
            recordError(message, context: command)
            throw WorkerError.message(message)
        }
        guard let python = bundledPythonCore,
              FileManager.default.isExecutableFile(atPath: python.path) else {
            let message = "App 内缺少 Python Core"
            recordError(message, context: command)
            throw WorkerError.message(message)
        }

        logger.append(logFile, "worker start command=\(command)")

        let process = Process()
        process.executableURL = python
        process.arguments = [worker.path] + arguments
        process.environment = workerEnvironment()

        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr

        do {
            try process.run()
        } catch {
            let message = "Worker 启动失败：\(error.localizedDescription)"
            recordError(message, context: command)
            logger.append(logFile, message)
            throw WorkerError.message(message)
        }

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
                let message = (json["error"] as? String) ?? "任务失败"
                recordError(message, context: command)
                logger.append(logFile, "worker failed command=\(command) exit=\(process.terminationStatus) message=\(message)")
                throw WorkerError.message(message)
            }
            logger.append(logFile, "worker done command=\(command) exit=\(process.terminationStatus)")
            return json
        }

        let combined = (errText + "\n" + outText).trimmingCharacters(in: .whitespacesAndNewlines)
        let message = combined.isEmpty ? "Worker 返回异常（exit \(process.terminationStatus)）" : combined
        recordError(message, context: command)
        logger.append(logFile, "worker parse failure command=\(command) exit=\(process.terminationStatus) message=\(message)")
        throw WorkerError.message(message)
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
        taskExecutionQueue.async {
            for (url, id) in items {
                let summary = "\(mode.rawValue) · .\(url.pathExtension.lowercased())"
                DispatchQueue.main.async { self.lastTaskSummary = summary + " · 处理中" }
                self.logger.append("app.log", "task start mode=\(mode.rawValue) ext=.\(url.pathExtension.lowercased())")
                self.mutateTask(id, status: "处理中", detail: "正在执行…")
                do {
                    let output = try self.processOne(
                        url,
                        mode: mode,
                        outputMode: outputMode,
                        customOutputDir: customOutputDir,
                        keep: keep
                    )
                    DispatchQueue.main.async { self.lastTaskSummary = summary + " · 完成" }
                    self.logger.append("app.log", "task done mode=\(mode.rawValue) ext=.\(url.pathExtension.lowercased())")
                    self.mutateTask(id, status: "完成", detail: "已完成", output: output)
                } catch {
                    let message = self.logger.sanitize(error.localizedDescription)
                    DispatchQueue.main.async { self.lastTaskSummary = summary + " · 失败" }
                    self.recordError(message, context: "task \(mode.rawValue)")
                    self.mutateTask(id, status: "失败", detail: message)
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
                throw WorkerError.message("仅转换模式当前用于受支持的特殊格式")
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
                    self.coreReady = json["core_ready"] as? Bool ?? false
                    self.doctorOK = json["ready"] as? Bool ?? false
                }
            } catch {
                DispatchQueue.main.async {
                    self.coreReady = false
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
                let whisper = models["whisper"] as? [String: Any] ?? [:]
                let asr = runtime["asr_python"] as? [String: Any] ?? [:]
                let qwen = models["qwen"] as? [String: Any] ?? [:]
                let mel = models["mel_deux"] as? [String: Any] ?? [:]
                let qrun = runtime["qwen_python"] as? [String: Any] ?? [:]
                let sep = runtime["separator"] as? [String: Any] ?? [:]
                DispatchQueue.main.async {
                    self.downloadSource = json["download_source"] as? String ?? "auto"
                    self.whisperInstalled = whisper["installed"] as? Bool ?? false
                    self.whisperModelSource = whisper["source"] as? String ?? "missing"
                    self.whisperModelPath = whisper["path"] as? String ?? ""
                    self.asrRuntimeInstalled = asr["installed"] as? Bool ?? false
                    self.asrRuntimeSource = asr["source"] as? String ?? "missing"
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
                let flag = kind == "qwen" ? "--qwen-model-dir" : (kind == "whisper" ? "--asr-model" : "--mel-model-dir")
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
        modelStatusMessage = kind == "qwen" ? "正在下载 Qwen3-TTS…" : (kind == "whisper" ? "正在下载 Whisper…" : "正在下载 Mel-Deux…")
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

    func installRuntime(_ kind: String) {
        guard runtimeBusy == nil else { return }
        runtimeBusy = kind
        let label = kind == "qwen" ? "Qwen / MLX" : (kind == "asr" ? "Whisper ASR" : "Mel Separator")
        modelStatusMessage = "正在安装 \(label) 运行环境…"
        logger.append("install.log", "runtime install requested kind=\(kind)")
        let source = downloadSource
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                _ = try self.runWorkerSync(["runtime-install", kind, "--source", source])
                DispatchQueue.main.async {
                    self.runtimeBusy = nil
                    self.modelStatusMessage = "\(label) 运行环境安装完成"
                }
                self.logger.append("install.log", "runtime install completed kind=\(kind)")
                self.refreshModels()
                self.refreshDoctor()
            } catch {
                let message = self.logger.sanitize(error.localizedDescription)
                DispatchQueue.main.async {
                    self.runtimeBusy = nil
                    self.modelStatusMessage = message
                }
                self.recordError(message, context: "runtime-install \(kind)")
            }
        }
    }

    func installAllComponents() {
        guard !batchBusy && modelBusy == nil && runtimeBusy == nil else { return }
        let work: [(String, String)] = [
            ("runtime", "qwen"), ("model", "qwen"),
            ("runtime", "mel"), ("model", "mel"),
            ("runtime", "asr"), ("model", "whisper")
        ].filter { type, kind in
            switch (type, kind) {
            case ("runtime", "qwen"): return !qwenRuntimeInstalled
            case ("model", "qwen"): return !qwenInstalled
            case ("runtime", "mel"): return !separatorInstalled
            case ("model", "mel"): return !melInstalled
            case ("runtime", "asr"): return !asrRuntimeInstalled
            case ("model", "whisper"): return !whisperInstalled
            default: return false
            }
        }
        guard !work.isEmpty else {
            batchProgress = "所需组件已经安装"
            return
        }
        batchBusy = true
        let source = downloadSource
        DispatchQueue.global(qos: .userInitiated).async {
            for (index, item) in work.enumerated() {
                let (type, kind) = item
                let label = kind == "qwen" ? "Qwen" : (kind == "mel" ? "Mel" : "Whisper")
                DispatchQueue.main.async {
                    self.batchProgress = "第 \(index + 1)/\(work.count) 项：\(type == "runtime" ? "安装" : "下载") \(label)…"
                }
                do {
                    let command = type == "runtime" ? "runtime-install" : "model-download"
                    _ = try self.runWorkerSync([command, kind, "--source", source])
                } catch {
                    let message = self.logger.sanitize(error.localizedDescription)
                    DispatchQueue.main.async {
                        self.batchBusy = false
                        self.batchProgress = "\(label) 失败：\(message)"
                    }
                    self.recordError(message, context: "install-all \(kind)")
                    self.refreshModels()
                    self.refreshDoctor()
                    return
                }
            }
            DispatchQueue.main.async {
                self.batchBusy = false
                self.batchProgress = "模型与运行环境安装完成"
            }
            self.refreshModels()
            self.refreshDoctor()
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
                        isDefault: $0["default"] as? Bool ?? false,
                        isBundled: $0["bundled"] as? Bool ?? false
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

    func deleteVoice(_ id: String) {
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                _ = try self.runWorkerSync(["voice-delete", id])
                DispatchQueue.main.async { self.voiceStatus = "人声模板已删除" }
                self.refreshVoices()
            } catch {
                DispatchQueue.main.async { self.voiceStatus = error.localizedDescription }
            }
        }
    }

    func clone(templateID: String?, temporaryRef: URL?, temporaryRefText: String = "", text: String, outputDir: String = "") {
        let script = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !script.isEmpty else {
            voiceStatus = "先输入需要生成的文案"
            return
        }

        let title = String(script.prefix(18))
        let item = TaskItem(
            title: title.isEmpty ? "声音克隆" : title,
            mode: "声音克隆",
            status: "等待中",
            detail: "已加入生成队列",
            outputPath: nil
        )
        tasks.insert(item, at: 0)
        voiceStatus = "已加入生成队列"
        let taskID = item.id
        let expandedOutput = NSString(string: outputDir).expandingTildeInPath

        taskExecutionQueue.async {
            self.mutateTask(taskID, status: "处理中", detail: "正在生成…")
            DispatchQueue.main.async {
                self.voiceBusy = true
                self.voiceStatus = "正在生成…"
            }
            do {
                let json: [String: Any]
                if let ref = temporaryRef {
                    var args = ["clone", "--ref-audio", ref.path, "--text", script]
                    let referenceText = temporaryRefText.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !referenceText.isEmpty { args += ["--ref-text", referenceText] }
                    if !expandedOutput.isEmpty { args += ["--output-dir", expandedOutput] }
                    json = try self.runWorkerSync(args)
                } else if let id = templateID {
                    var args = ["voice-clone", id, "--text", script]
                    if !expandedOutput.isEmpty { args += ["--output-dir", expandedOutput] }
                    json = try self.runWorkerSync(args)
                } else if let first = self.voices.first(where: { $0.isDefault }) ?? self.voices.first {
                    var args = ["voice-clone", first.id, "--text", script]
                    if !expandedOutput.isEmpty { args += ["--output-dir", expandedOutput] }
                    json = try self.runWorkerSync(args)
                } else {
                    throw WorkerError.message("先建立一个人声模板，或选择临时参考音")
                }

                let output = json["path"] as? String
                self.mutateTask(taskID, status: "完成", detail: "生成完成", output: output)
                DispatchQueue.main.async {
                    self.voiceBusy = false
                    self.voiceStatus = "生成完成"
                    self.lastVoiceOutput = output
                }
            } catch {
                let message = self.logger.sanitize(error.localizedDescription)
                self.recordError(message, context: "task 声音克隆")
                self.mutateTask(taskID, status: "失败", detail: message)
                DispatchQueue.main.async {
                    self.voiceBusy = false
                    self.voiceStatus = message
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

    private func copyToPasteboard(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    private var architectureName: String {
        #if arch(arm64)
        return "arm64 / Apple Silicon"
        #else
        return "unknown"
        #endif
    }

    func makeDiagnosticReport() -> String {
        let pythonOK = bundledPythonCore.map { FileManager.default.isExecutableFile(atPath: $0.path) } ?? false
        let recent = (
            logger.tail("app.log", lines: 8) +
            logger.tail("worker.log", lines: 8) +
            logger.tail("install.log", lines: 8)
        ).suffix(18).joined(separator: "\n")

        return """
        SPP Audio Studio Diagnostic Report
        Version: \(appVersion)
        macOS: \(ProcessInfo.processInfo.operatingSystemVersionString)
        Architecture: \(architectureName)

        Core:
        Bundled Python Core: \(pythonOK ? "OK" : "MISSING")
        Environment Ready: \(doctorOK ? "YES" : "NO")
        Format Converter: \((doctorChecks["format_converter_binary"] ?? false) ? "OK" : "MISSING")

        Qwen:
        Model: \(qwenInstalled ? "Installed" : "Missing")
        Runtime: \(qwenRuntimeInstalled ? "Installed" : "Missing")
        Runtime Source: \(qwenRuntimeSource)

        Mel-Deux:
        Model: \(melInstalled ? "Installed" : "Missing")
        Runtime: \(separatorInstalled ? "Installed" : "Missing")
        Runtime Source: \(separatorSource)
        FFmpeg: \((doctorChecks["mel_ffmpeg"] ?? false) ? "OK" : "Missing")

        Last Task:
        \(lastTaskSummary)

        Last Error:
        \(lastError.isEmpty ? "None" : logger.sanitize(lastError))

        Recent Log:
        \(recent.isEmpty ? "No recent log entries." : recent)
        """
    }

    func copyDiagnosticReport() {
        copyToPasteboard(makeDiagnosticReport())
        diagnosticStatus = "诊断报告已复制"
        logger.append("app.log", "diagnostic report copied")
    }

    func copyLastError() {
        let text = lastError.isEmpty ? "SPP Audio Studio：暂无最近错误。" : logger.sanitize(lastError)
        copyToPasteboard(text)
        diagnosticStatus = "最近错误已复制"
        logger.append("app.log", "last error copied")
    }

    func copyErrorText(_ text: String) {
        copyToPasteboard(logger.sanitize(text))
        diagnosticStatus = "错误信息已复制"
        logger.append("app.log", "task error copied")
    }

    func openLogFolder() {
        try? FileManager.default.createDirectory(at: logger.directory, withIntermediateDirectories: true)
        NSWorkspace.shared.open(logger.directory)
        diagnosticStatus = "已打开日志文件夹"
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
                .frame(minWidth: 1280, minHeight: 760)
                .preferredColorScheme(.light)
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
                case .workbench: WorkbenchView(selection: $selection)
                case .convert: FileToolView(mode: .convert, title: "格式转换", subtitle: "特殊格式 → 原始 MP3 / FLAC")
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
    @Binding var selection: SidebarItem?
    @EnvironmentObject var state: AppState
    @State private var files: [URL] = []
    @State private var mode: ToolMode = .convertAndSeparate
    @State private var isTargeted = false
    @AppStorage("workbench.outputMode") private var outputMode = "beside"
    @AppStorage("workbench.outputDir") private var outputDir = ""
    @AppStorage("workbench.keep") private var keep = "instrumental"

    var body: some View {
        HStack(alignment: .top, spacing: 20) {
            VStack(alignment: .leading, spacing: 20) {
                HeaderBlock(title: "音频工作台", subtitle: "转换音乐、人声分离、克隆声音。常用操作尽量一处完成。")
            if !state.qwenInstalled || !state.melInstalled || !state.qwenRuntimeInstalled || !state.separatorInstalled {
                HStack(spacing: 14) {
                    Image(systemName: "arrow.down.circle")
                        .font(.title2)
                        .foregroundStyle(Color.accentColor)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("首次使用：安装模型和运行环境")
                            .font(.headline)
                        Text("声音克隆需要 Qwen；人声分离需要 Mel。请到「模型与环境」完成对应安装。")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 8)
                    Button("前往安装") { selection = .environment }
                        .buttonStyle(.borderedProminent)
                }
                .padding(16)
                .background(Color.accentColor.opacity(0.09), in: RoundedRectangle(cornerRadius: 14))
            }
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

                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)

            TaskSidebarView(modes: Set([
                ToolMode.convert.rawValue,
                ToolMode.separate.rawValue,
                ToolMode.convertAndSeparate.rawValue
            ]))
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
            .fill(isTargeted ? Color.accentColor.opacity(0.12) : Color(nsColor: .controlBackgroundColor))
            .overlay(
                RoundedRectangle(cornerRadius: 18)
                    .strokeBorder(isTargeted ? Color.accentColor : Color(nsColor: .separatorColor),
                                  style: StrokeStyle(lineWidth: 1.2, dash: [8, 7]))
            )
            .overlay(
                VStack(spacing: 9) {
                    Image(systemName: "plus.circle").font(.system(size: 32, weight: .light))
                    Text("把特殊格式 / FLAC / MP3 / WAV / M4A 拖到这里").font(.headline)
                    Text(mode == .convertAndSeparate ? "特殊格式会自动先转换，再进入 Mel-Deux；普通音频直接分离。" : "支持批量加入任务队列")
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
        HStack(alignment: .top, spacing: 20) {
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
                .fill(isTargeted ? Color.accentColor.opacity(0.12) : Color(nsColor: .controlBackgroundColor))
                .overlay(
                    RoundedRectangle(cornerRadius: 18)
                        .strokeBorder(
                            isTargeted ? Color.accentColor : Color(nsColor: .separatorColor),
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
                             ? "支持批量特殊格式文件"
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

                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)

            TaskSidebarView(modes: Set([mode.rawValue]))
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
    let modes: Set<String>?

    init(modes: Set<String>? = nil) {
        self.modes = modes
    }

    private var visibleTasks: [TaskItem] {
        let filtered = state.tasks.filter { task in
            guard let modes else { return true }
            return modes.contains(task.mode)
        }
        return Array(filtered.prefix(30))
    }

    var body: some View {
        if visibleTasks.isEmpty {
            ContentUnavailableView("还没有任务", systemImage: "tray", description: Text("等待、处理中和已完成任务都会显示在这里。"))
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            List(visibleTasks) { task in
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 9) {
                        Image(systemName: icon(for: task.status))
                            .foregroundStyle(color(for: task.status))
                        Text(task.title)
                            .font(.subheadline.weight(.medium))
                            .lineLimit(1)
                        Spacer()
                        Text(task.status)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Text(task.detail.isEmpty ? task.mode : task.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)

                    HStack(spacing: 12) {
                        Text(task.mode)
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                        Spacer()
                        if task.status == "失败" {
                            Button {
                                state.copyErrorText(task.detail)
                            } label: {
                                Image(systemName: "doc.on.doc")
                            }
                            .buttonStyle(.plain)
                            .help("复制错误")
                        }
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
                }
                .padding(.vertical, 5)
            }
            .listStyle(.inset)
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

struct TaskSidebarView: View {
    @EnvironmentObject var state: AppState
    let modes: Set<String>?

    init(modes: Set<String>? = nil) {
        self.modes = modes
    }

    private var matchingTasks: [TaskItem] {
        state.tasks.filter { task in
            guard let modes else { return true }
            return modes.contains(task.mode)
        }
    }

    private var summary: String {
        let running = matchingTasks.filter { $0.status == "处理中" }.count
        let waiting = matchingTasks.filter { $0.status == "等待中" }.count
        let history = matchingTasks.filter { $0.status == "完成" || $0.status == "失败" }.count
        if running > 0 { return "处理中 \(running) · 等待 \(waiting) · 历史 \(history)" }
        if waiting > 0 { return "等待 \(waiting) · 历史 \(history)" }
        return history > 0 ? "历史 \(history)" : "暂无任务"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("任务队列").font(.headline)
                Spacer()
                Text(summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Divider()
            TaskListView(modes: modes)
            if !state.previewStatus.isEmpty {
                Text(state.previewStatus)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .frame(width: 340, alignment: .topLeading)
        .frame(maxHeight: .infinity, alignment: .topLeading)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(Color(nsColor: .separatorColor))
        )
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
    @State private var temporaryRefText = ""
    @State private var text = "大家好，我是宋盼盼。这是一段 SPP Audio Studio 的声音克隆测试。如果你能自然地听到这句话，说明人声模板、模型和本地推理都已经正常工作。"
    @State private var showAdd = false
    @State private var pendingDeleteVoice: VoiceTemplate?
    @AppStorage("clone.outputMode") private var outputMode = "default"
    @AppStorage("clone.outputDir") private var outputDir = ""

    var body: some View {
        HStack(alignment: .top, spacing: 20) {
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
                        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 15))
                        .overlay(RoundedRectangle(cornerRadius: 15).strokeBorder(Color(nsColor: .separatorColor)))
                    }
                    .buttonStyle(.plain)
                }
            }

            VStack(alignment: .leading, spacing: 5) {
                Label("参考音建议", systemImage: "waveform")
                    .font(.headline)
                Text("推荐 5–15 秒、单人清晰说话、少背景音乐和回声。支持 WAV、M4A、MP3，也可选 FLAC、AAC、AIFF 或 CAF。")
                Text("参考音里的原话要与音频一致；安装 Whisper 后可以留空自动转写。")
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))

            if temporaryRef != nil {
                VStack(alignment: .leading, spacing: 6) {
                    Text("临时参考音的原话").font(.headline)
                    TextField("输入参考音频里说的话", text: $temporaryRefText, axis: .vertical)
                        .lineLimit(2...4)
                        .textFieldStyle(.roundedBorder)
                    Text("未配置 Whisper 时需填写。这里填写参考音说的话，不是要生成的文案。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Text("生成文案").font(.headline)
            Text("克隆结果偶尔会有波动，可以多生成两版挑选；文案较长时建议分成两段生成。")
                .font(.caption)
                .foregroundStyle(.secondary)
            TextEditor(text: $text)
                .font(.system(size: 16))
                .scrollContentBackground(.hidden)
                .padding(10)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
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
                        temporaryRefText: temporaryRefText,
                        text: text,
                        outputDir: effectiveCloneOutputDir
                    )
                } label: {
                    Label("生成声音", systemImage: "waveform.badge.plus")
                }
                .buttonStyle(.borderedProminent)
                .disabled(outputMode == "custom" && outputDir.isEmpty)

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
                    if !selected.isBundled {
                        Button("删除模板", role: .destructive) {
                            pendingDeleteVoice = selected
                        }
                    }
                    if !selected.isDefault {
                        Button("设为默认") { state.setDefaultVoice(selected.id) }
                    } else {
                        Label("默认", systemImage: "pin.fill").foregroundStyle(.secondary)
                    }
                }
            }
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)

            TaskSidebarView(modes: Set(["声音克隆"]))
        }
        .sheet(isPresented: $showAdd) {
            AddVoiceSheet(isPresented: $showAdd)
                .environmentObject(state)
        }
        .confirmationDialog(
            "删除人声模板？",
            isPresented: Binding(
                get: { pendingDeleteVoice != nil },
                set: { if !$0 { pendingDeleteVoice = nil } }
            ),
            presenting: pendingDeleteVoice
        ) { voice in
            Button("删除「\(voice.name)」", role: .destructive) {
                state.deleteVoice(voice.id)
                if selectedVoice == voice.id { selectedVoice = nil }
                pendingDeleteVoice = nil
            }
            Button("取消", role: .cancel) {
                pendingDeleteVoice = nil
            }
        } message: { _ in
            Text("只会删除 SPP Audio Studio 保存的模板副本，不会删除你原始的参考音频。")
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
            temporaryRefText = ""
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
                        .fill(Color.primary.opacity(0.08))
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
            .background(active ? Color.accentColor.opacity(0.16) : Color(nsColor: .controlBackgroundColor),
                        in: RoundedRectangle(cornerRadius: 15))
            .overlay(
                RoundedRectangle(cornerRadius: 15)
                    .strokeBorder(active ? Color.accentColor : Color(nsColor: .separatorColor))
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
            TextField("参考音频里说了什么", text: $refText)
            Text("未配置 Whisper 时需填写参考音频的原话。")
                .font(.caption)
                .foregroundStyle(.secondary)
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
        "bundled_python_core": "App 内置 Python Core",
        "format_converter_binary": "本地格式转换器",
        "qwen_runtime": "Qwen / MLX Runtime",
        "qwen_model": "Qwen3-TTS 模型",
        "qwen_bridge": "Qwen 桥接层",
        "mel_runtime": "Mel Separator Runtime",
        "mel_model": "Mel-Deux 模型",
        "mel_config": "Mel-Deux 配置",
        "mel_ffmpeg": "Mel-Deux FFmpeg",
        "asr_optional": "Whisper 自动转写（可选）"
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

                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("一键安装全部组件").font(.headline)
                        Text("依次安装 Qwen、Mel 和 Whisper 的模型与运行环境；已安装的项目会跳过。约需 5 GB 模型空间。")
                            .font(.caption).foregroundStyle(.secondary)
                        if !state.batchProgress.isEmpty {
                            Text(state.batchProgress).font(.caption).foregroundStyle(.secondary)
                                .lineLimit(3).textSelection(.enabled)
                        }
                    }
                    Spacer(minLength: 8)
                    Button {
                        state.installAllComponents()
                    } label: {
                        if state.batchBusy { ProgressView().controlSize(.small) }
                        Text(state.batchBusy ? "安装中…" : "一键下载并安装")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(state.batchBusy || state.modelBusy != nil || state.runtimeBusy != nil)
                }
                .padding(16)
                .background(Color.accentColor.opacity(0.09), in: RoundedRectangle(cornerRadius: 14))

                HStack(spacing: 10) {
                    Image(systemName: state.doctorOK ? "checkmark.seal.fill" : (state.coreReady ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"))
                        .foregroundStyle(state.doctorOK ? .green : (state.coreReady ? .green : .orange))
                        .font(.title2)
                    Text(state.doctorOK
                         ? "当前环境全部就绪"
                         : (state.coreReady ? "App 核心已就绪，AI 组件待安装" : "核心组件需要处理"))
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

                modelCard(
                    kind: "whisper",
                    title: "Whisper large-v3-turbo（可选）",
                    subtitle: "临时参考音自动转写 · MLX",
                    size: "约 1.5 GB",
                    license: "模型条款见 Hugging Face",
                    installed: state.whisperInstalled,
                    source: state.whisperModelSource,
                    path: state.whisperModelPath
                )

                runtimeCard

                if !state.modelStatusMessage.isEmpty {
                    Text(state.modelStatusMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }

                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text("诊断与日志").font(.headline)
                        Spacer()
                        if !state.diagnosticStatus.isEmpty {
                            Text(state.diagnosticStatus)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Text("群测遇到问题时，优先点“复制诊断报告”直接发给开发者。报告默认隐藏用户名、完整路径和声音克隆文案。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    HStack(spacing: 10) {
                        Button("复制诊断报告") { state.copyDiagnosticReport() }
                            .buttonStyle(.borderedProminent)
                        Button("复制最近错误") { state.copyLastError() }
                            .disabled(state.lastError.isEmpty)
                        Button("打开日志文件夹") { state.openLogFolder() }
                    }
                }
                .padding(16)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))

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
                            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
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
                .disabled(state.modelBusy != nil || state.batchBusy)

                Button("链接本地…") { chooseModelFolder(kind: kind) }
                    .disabled(state.modelBusy != nil || state.batchBusy)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Color(nsColor: .separatorColor)))
    }

    private var runtimeCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("推理运行环境").font(.headline)
                Spacer()
                Text(runtimeSummary)
                    .font(.caption)
                    .foregroundStyle((state.qwenRuntimeInstalled && state.separatorInstalled) ? .green : .orange)
            }

            Text("RC6 自带 Python Core，不需要 Xcode Command Line Tools。Qwen / Mel 的推理依赖可以在这里一次安装，安装完成后离线使用。")
                .font(.caption)
                .foregroundStyle(.secondary)

            HStack {
                Label("Qwen / MLX：\(sourceLabel(state.qwenRuntimeSource))",
                      systemImage: state.qwenRuntimeInstalled ? "checkmark.circle.fill" : "xmark.circle")
                Spacer()
                if !state.qwenRuntimeInstalled {
                    Button {
                        state.installRuntime("qwen")
                    } label: {
                        if state.runtimeBusy == "qwen" {
                            ProgressView().controlSize(.small)
                            Text("安装中…")
                        } else {
                            Text("安装 Qwen Runtime")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(state.runtimeBusy != nil || state.batchBusy)
                }
            }

            HStack {
                Label("Mel Separator：\(sourceLabel(state.separatorSource))",
                      systemImage: state.separatorInstalled ? "checkmark.circle.fill" : "xmark.circle")
                Spacer()
                if !state.separatorInstalled {
                    Button {
                        state.installRuntime("mel")
                    } label: {
                        if state.runtimeBusy == "mel" {
                            ProgressView().controlSize(.small)
                            Text("安装中…")
                        } else {
                            Text("安装 Mel Runtime")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(state.runtimeBusy != nil || state.batchBusy)
                }
            }

            HStack {
                Label("Whisper ASR（可选）：\(sourceLabel(state.asrRuntimeSource))",
                      systemImage: state.asrRuntimeInstalled ? "checkmark.circle.fill" : "xmark.circle")
                Spacer()
                if !state.asrRuntimeInstalled {
                    Button { state.installRuntime("asr") } label: {
                        if state.runtimeBusy == "asr" {
                            ProgressView().controlSize(.small)
                            Text("安装中…")
                        } else {
                            Text("安装 Whisper Runtime")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(state.runtimeBusy != nil || state.batchBusy)
                }
            }

            Text("运行环境安装会使用 App 内置 Python 和预编译包，不要求用户安装 Homebrew、Xcode 或系统 Python。")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(16)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
    }

    private var runtimeSummary: String {
        (state.qwenRuntimeInstalled && state.separatorInstalled) ? "已就绪" : "需要安装"
    }

    private func statusPill(installed: Bool, source: String) -> some View {
        Text(installed ? sourceLabel(source) : "未安装")
            .font(.caption.weight(.medium))
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .background(Color(nsColor: .controlBackgroundColor), in: Capsule())
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
