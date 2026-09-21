// SPP Audio Studio - native NCM converter
// Copyright (c) 2026 Song Panpan / 宋盼盼
// MIT License. Algorithm references/credits: see THIRD_PARTY_NOTICES.md.

import AppKit
import CommonCrypto
import UniformTypeIdentifiers

// ============================================================
// 本地特殊格式转换器（Swift 原生版）
// 无参数 → 图形界面；带参数 → 命令行转换
// ============================================================

// MARK: - 基础工具

extension Data {
    init?(hex: String) {
        var bytes = [UInt8]()
        var s = hex
        while s.count >= 2 {
            let sub = String(s.prefix(2))
            guard let b = UInt8(sub, radix: 16) else { return nil }
            bytes.append(b)
            s.removeFirst(2)
        }
        guard s.isEmpty else { return nil }
        self.init(bytes)
    }
    func u32LE(at offset: Int) -> UInt32 {
        var v: UInt32 = 0
        for i in 0..<4 { v |= UInt32(self[offset + i]) << (8 * i) }
        return v
    }
}

struct NcmError: Error, CustomStringConvertible {
    let message: String
    var description: String { message }
    init(_ message: String) { self.message = message }
}
struct SkipFileError: Error {}

// MARK: - 解密核心（与 ncmdump 0.1.1 对齐，2026-08 实测）

let CORE_KEY = Data(hex: "687A4852416D736F356B496E62617857")!   // "hzHRAmso5kInbaxW"
let META_KEY = Data(hex: "2331346C6A6B5F215C5D2630553C2728")!   // "#14ljk_!\]&0U<'("

func aesEcbDecrypt(_ data: Data, key: Data) -> Data? {
    var out = Data(count: data.count + 32)
    var outLen = 0
    let status = data.withUnsafeBytes { inBuf in
        key.withUnsafeBytes { keyBuf in
            out.withUnsafeMutableBytes { outBuf in
                CCCrypt(CCOperation(kCCDecrypt), CCAlgorithm(kCCAlgorithmAES),
                        CCOptions(kCCOptionECBMode),
                        keyBuf.baseAddress, key.count, nil,
                        inBuf.baseAddress, data.count,
                        outBuf.baseAddress, outBuf.count, &outLen)
            }
        }
    }
    guard status == kCCSuccess else { return nil }
    return out.prefix(outLen)
}

func pkcs7Unpad(_ data: Data) -> Data {
    guard let last = data.last else { return data }
    let pad = Int(last)
    guard pad > 0, pad <= 16, pad <= data.count else { return data }
    guard data.suffix(pad).allSatisfy({ $0 == last }) else { return data }
    return data.prefix(data.count - pad)
}

struct NcmParsed {
    let rc4Key: [UInt8]
    let format: String
    let audio: Data
    let metadata: [String: Any]
    let cover: Data?
}

func parseNcm(_ data: Data) throws -> NcmParsed {
    guard data.count > 20 else { throw NcmError("文件太小或已损坏") }
    guard data.prefix(8) == Data("CTENFDAM".utf8) else { throw NcmError("不是有效的 .ncm 文件") }
    var off = 10

    // key 段：xor 0x64 → AES-ECB(CORE_KEY) → unpad → [17:]
    let keyLen = Int(data.u32LE(at: off)); off += 4
    guard off + keyLen <= data.count else { throw NcmError("文件结构损坏（key 段越界）") }
    let keyData = Data(data[off..<off + keyLen].map { $0 ^ 0x64 }); off += keyLen
    guard let keyPlainAll = aesEcbDecrypt(keyData, key: CORE_KEY) else { throw NcmError("key 解密失败") }
    let keyPlain = pkcs7Unpad(keyPlainAll)
    guard keyPlain.count > 17 else { throw NcmError("key 数据异常") }
    let rc4Key = [UInt8](keyPlain[17..<keyPlain.count])

    // meta 段：xor 0x63 → [22:] → base64 → AES-ECB(META_KEY) → unpad → "music:" → JSON
    var format = "mp3"
    var metadata: [String: Any] = [:]
    let metaLen = Int(data.u32LE(at: off)); off += 4
    if metaLen > 0 {
        guard off + metaLen <= data.count else { throw NcmError("文件结构损坏（meta 段越界）") }
        let metaData = Data(data[off..<off + metaLen].map { $0 ^ 0x63 }); off += metaLen
        guard metaData.count > 22 else { throw NcmError("meta 数据异常") }
        guard let b64 = Data(base64Encoded: metaData[22...]) else { throw NcmError("meta base64 解码失败") }
        guard let metaPlainAll = aesEcbDecrypt(b64, key: META_KEY) else { throw NcmError("meta 解密失败") }
        let metaStr = String(data: pkcs7Unpad(metaPlainAll), encoding: .utf8) ?? ""
        if let r = metaStr.range(of: "music:"),
           let obj = try? JSONSerialization.jsonObject(
               with: Data(metaStr[r.upperBound...].utf8)) as? [String: Any] {
            metadata = obj
            format = obj["format"] as? String ?? format
        }
    } else {
        format = data.count > 1024 * 1024 * 16 ? "flac" : "mp3"
    }

    // 跳过 crc32 + image version（5 字节）
    off += 5

    // 封面段
    var cover: Data? = nil
    if off + 8 <= data.count {
        let coverLen = Int(data.u32LE(at: off)); off += 4
        let imgSize = Int(data.u32LE(at: off)); off += 4
        if imgSize > 0, off + imgSize <= data.count {
            cover = Data(data[off..<off + imgSize])
            off += imgSize
        }
        let rest = coverLen - imgSize
        if rest > 0, off + rest <= data.count { off += rest }
    }

    guard off < data.count else { throw NcmError("没有音频数据") }
    return NcmParsed(rc4Key: rc4Key, format: format, audio: Data(data[off..<data.count]), metadata: metadata, cover: cover)
}

func buildXorStream(_ S: [UInt8]) -> [UInt8] {
    var stream = [UInt8](repeating: 0, count: 256)
    for i in 0..<256 {
        let si = Int(S[i])
        let sj = Int(S[(i + si) & 0xff])
        stream[i] = S[(si + sj) & 0xff]
    }
    return stream
}

func be32(_ n: Int) -> Data {
    Data([UInt8((n >> 24) & 255), UInt8((n >> 16) & 255), UInt8((n >> 8) & 255), UInt8(n & 255)])
}

func le32(_ n: Int) -> Data {
    Data([UInt8(n & 255), UInt8((n >> 8) & 255), UInt8((n >> 16) & 255), UInt8((n >> 24) & 255)])
}

func songFields(_ meta: [String: Any]) -> [(String, String, String)] {
    let artists = (meta["artist"] as? [[Any]] ?? []).compactMap { $0.first as? String }.joined(separator: ", ")
    let title = meta["musicName"] as? String ?? ""
    let album = meta["album"] as? String ?? ""
    return [("TIT2", "TITLE", title), ("TPE1", "ARTIST", artists), ("TALB", "ALBUM", album)].filter { !$0.2.isEmpty }
}

func taggedMP3(_ audio: Data, meta: [String: Any], cover: Data?) -> Data {
    var frames = Data()
    for (id, _, value) in songFields(meta) {
        var payload = Data([3]) // UTF-8 text frame
        payload.append(Data(value.utf8))
        frames.append(Data(id.utf8))
        frames.append(be32(payload.count))
        frames.append(Data([0, 0]))
        frames.append(payload)
    }
    if let cover, !cover.isEmpty {
        let mime = cover.starts(with: Data([0x89, 0x50, 0x4e, 0x47])) ? "image/png" : "image/jpeg"
        var payload = Data([3])
        payload.append(Data(mime.utf8))
        payload.append(Data([0, 3, 0])) // front cover
        payload.append(cover)
        frames.append(Data("APIC".utf8))
        frames.append(be32(payload.count))
        frames.append(Data([0, 0]))
        frames.append(payload)
    }
    guard !frames.isEmpty else { return audio }
    let length = frames.count
    let synchsafe = Data([UInt8((length >> 21) & 127), UInt8((length >> 14) & 127), UInt8((length >> 7) & 127), UInt8(length & 127)])
    var result = Data("ID3".utf8)
    result.append(Data([3, 0, 0]))
    result.append(synchsafe)
    result.append(frames)
    result.append(audio)
    return result
}

func flacBlock(type: UInt8, last: Bool, payload: Data) -> Data {
    var result = Data([type | (last ? 0x80 : 0), UInt8((payload.count >> 16) & 255), UInt8((payload.count >> 8) & 255), UInt8(payload.count & 255)])
    result.append(payload)
    return result
}

func taggedFLAC(_ audio: Data, meta: [String: Any], cover: Data?) -> Data {
    guard audio.starts(with: Data("fLaC".utf8)) else { return audio }
    guard !songFields(meta).isEmpty || (cover?.isEmpty == false) else { return audio }
    var blocks: [(UInt8, Data)] = []
    var offset = 4
    var last = false
    while !last && offset + 4 <= audio.count {
        let header = audio[offset]
        last = header & 0x80 != 0
        let kind = header & 0x7f
        let length = Int(audio[offset + 1]) << 16 | Int(audio[offset + 2]) << 8 | Int(audio[offset + 3])
        offset += 4
        guard offset + length <= audio.count else { return audio }
        if kind != 4 && kind != 6 { blocks.append((kind, Data(audio[offset..<offset + length]))) }
        offset += length
    }
    guard last else { return audio }
    let vendor = Data("SPP Audio Studio".utf8)
    var comments = Data()
    comments.append(le32(vendor.count)); comments.append(vendor)
    let fields = songFields(meta)
    comments.append(le32(fields.count))
    for (_, key, value) in fields {
        let item = Data("\(key)=\(value)".utf8)
        comments.append(le32(item.count)); comments.append(item)
    }
    blocks.append((4, comments))
    if let cover, !cover.isEmpty {
        let mime = Data((cover.starts(with: Data([0x89, 0x50, 0x4e, 0x47])) ? "image/png" : "image/jpeg").utf8)
        var picture = be32(3)
        picture.append(be32(mime.count)); picture.append(mime)
        picture.append(be32(0)) // description
        for _ in 0..<4 { picture.append(be32(0)) } // dimensions and depth
        picture.append(be32(cover.count)); picture.append(cover)
        blocks.append((6, picture))
    }
    var result = Data("fLaC".utf8)
    for (index, block) in blocks.enumerated() {
        result.append(flacBlock(type: block.0, last: index == blocks.count - 1, payload: block.1))
    }
    result.append(audio[offset...])
    return result
}

func convertFile(src: URL, outDir: String, fmtChoice: String, skip: Bool) throws -> URL {
    let data = try Data(contentsOf: src)
    let parsed = try parseNcm(data)
    // Decryption preserves the original codec; it does not transcode.
    let ext = parsed.format.lowercased()
    let outURL = URL(fileURLWithPath: outDir)
        .appendingPathComponent(src.deletingPathExtension().lastPathComponent)
        .appendingPathExtension(ext == "flac" ? "flac" : "mp3")
    if skip && FileManager.default.fileExists(atPath: outURL.path) {
        throw SkipFileError()
    }
    let key = parsed.rc4Key
    guard !key.isEmpty else { throw NcmError("RC4 key 为空") }
    var S = Array(0...255).map { UInt8($0) }
    var j = 0
    for i in 0..<256 {
        j = (j + Int(S[i]) + Int(key[i % key.count])) & 0xff
        S.swapAt(i, j)
    }
    let stream = buildXorStream(S)
    let bytes = [UInt8](parsed.audio)
    var plain = [UInt8](repeating: 0, count: bytes.count)
    for (k, b) in bytes.enumerated() {
        plain[k] = b ^ stream[(k + 1) & 0xff]
    }
    let decoded = Data(plain)
    let tagged = parsed.format.lowercased() == "flac"
        ? taggedFLAC(decoded, meta: parsed.metadata, cover: parsed.cover)
        : taggedMP3(decoded, meta: parsed.metadata, cover: parsed.cover)
    try tagged.write(to: outURL)
    return outURL
}

// MARK: - 命令行模式

func runCLI(_ args: [String]) -> Int32 {
    var files: [String] = []
    var outDir = (NSHomeDirectory() as NSString).appendingPathComponent("Music/SPP Audio Studio/Converted")
    var fmt = "mp3"
    var i = 0
    while i < args.count {
        let a = args[i]
        if a == "--out" { i += 1; if i < args.count { outDir = args[i] } }
        else if a == "--fmt" { i += 1; if i < args.count { fmt = args[i] } }
        else if a.hasSuffix(".ncm") { files.append(a) }
        i += 1
    }
    guard !files.isEmpty else {
        FileHandle.standardError.write(Data(
            "用法: ncm_converter <文件.ncm ...> [--out 输出目录] [--fmt mp3|flac|原格式]\n无参数启动图形界面\n".utf8))
        return 2
    }
    try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
    var ok = 0, skipped = 0, failed = 0
    for f in files {
        let name = (f as NSString).lastPathComponent
        do {
            let out = try convertFile(src: URL(fileURLWithPath: f), outDir: outDir,
                                      fmtChoice: fmt, skip: true)
            print("✓ \(name) → \(out.lastPathComponent)")
            ok += 1
        } catch is SkipFileError {
            skipped += 1
            print("⏭ \(name) 已存在，跳过")
        } catch {
            failed += 1
            print("✗ \(name): \(error)")
        }
    }
    print("完成：成功 \(ok)，跳过 \(skipped)，失败 \(failed)")
    return failed > 0 ? 1 : 0
}

// MARK: - 拖拽区

final class DropView: NSView {
    weak var delegate: AppDelegate?
    var isDragging = false { didSet { needsDisplay = true } }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        registerForDraggedTypes([.fileURL])
    }
    required init?(coder: NSCoder) { fatalError() }

    override func draw(_ dirtyRect: NSRect) {
        let color = isDragging ? NSColor.controlAccentColor : NSColor.tertiaryLabelColor
        color.setStroke()
        let path = NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 10, yRadius: 10)
        path.lineWidth = 2
        var dash: [CGFloat] = [6, 4]
        path.setLineDash(&dash, count: 2, phase: 0)
        path.stroke()
        let text = "把 .ncm 文件或文件夹拖到这里\n（也可以点击选择文件）"
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 13),
            .foregroundColor: NSColor.secondaryLabelColor
        ]
        let size = (text as NSString).size(withAttributes: attrs)
        (text as NSString).draw(
            at: NSPoint(x: (bounds.width - size.width) / 2,
                        y: (bounds.height - size.height) / 2),
            withAttributes: attrs)
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        isDragging = true
        return .copy
    }
    override func draggingExited(_ sender: NSDraggingInfo?) {
        isDragging = false
    }
    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool { true }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        isDragging = false
        var urls: [URL] = []
        let opts: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
        if let items = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: opts) as? [URL] {
            urls = items
        }
        if !urls.isEmpty { delegate?.addDroppedURLs(urls) }
        return true
    }
    override func mouseDown(with event: NSEvent) {
        delegate?.pickFiles()
    }
}

// MARK: - 主界面

final class AppDelegate: NSObject, NSApplicationDelegate, NSTableViewDataSource, NSTableViewDelegate {
    var window: NSWindow!
    var tableView: NSTableView!
    var outField: NSTextField!
    var fmtPopup: NSPopUpButton!
    var startButton: NSButton!
    var progress: NSProgressIndicator!
    var statusLabel: NSTextField!
    var logView: NSTextView!
    var files: [URL] = []
    var converting = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 600),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "格式转换"
        window.center()
        window.setFrameAutosaveName("NcmConverter")
        window.contentView = buildUI()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    private func makeLabel(_ text: String, _ size: CGFloat = 13, _ bold: Bool = false,
                           _ color: NSColor = .labelColor) -> NSTextField {
        let l = NSTextField(labelWithString: text)
        l.font = NSFont.systemFont(ofSize: size, weight: bold ? .bold : .regular)
        l.textColor = color
        return l
    }

    private func buildUI() -> NSView {
        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 10
        root.edgeInsets = NSEdgeInsets(top: 18, left: 18, bottom: 18, right: 18)

        let title = makeLabel("♪ 本地格式转换器", 20, true)
        root.addArrangedSubview(title)
        root.addArrangedSubview(makeLabel("把 .ncm 文件拖进来，一键转成 mp3 / flac", 12, false, .secondaryLabelColor))

        let drop = DropView(frame: NSRect(x: 0, y: 0, width: 0, height: 92))
        drop.delegate = self
        drop.widthAnchor.constraint(greaterThanOrEqualToConstant: 400).isActive = true
        root.addArrangedSubview(drop)
        root.setCustomSpacing(12, after: drop)

        // 文件列表
        tableView = NSTableView()
        tableView.addTableColumn(NSTableColumn(identifier: NSUserInterfaceItemIdentifier("file")))
        tableView.headerView = nil
        tableView.rowHeight = 24
        tableView.delegate = self
        tableView.dataSource = self
        tableView.allowsMultipleSelection = true
        let scroll = NSScrollView()
        scroll.documentView = tableView
        scroll.hasVerticalScroller = true
        scroll.heightAnchor.constraint(equalToConstant: 130).isActive = true
        scroll.widthAnchor.constraint(greaterThanOrEqualToConstant: 400).isActive = true
        root.addArrangedSubview(scroll)

        // 按钮行
        let btnRow = NSStackView()
        btnRow.orientation = .horizontal
        btnRow.spacing = 8
        let addBtn = NSButton(title: "＋ 添加文件", target: self, action: #selector(pickFiles))
        let rmBtn = NSButton(title: "移除选中", target: self, action: #selector(removeSelected))
        let clearBtn = NSButton(title: "清空", target: self, action: #selector(clearFiles))
        btnRow.addArrangedSubview(addBtn)
        btnRow.addArrangedSubview(rmBtn)
        btnRow.addArrangedSubview(clearBtn)
        root.addArrangedSubview(btnRow)

        // 输出目录
        let outRow = NSStackView()
        outRow.orientation = .horizontal
        outRow.spacing = 8
        outRow.addArrangedSubview(makeLabel("输出目录", 13))
        outField = NSTextField(string: (NSHomeDirectory() as NSString).appendingPathComponent("Music/SPP Audio Studio/Converted"))
        outField.widthAnchor.constraint(greaterThanOrEqualToConstant: 240).isActive = true
        let browseBtn = NSButton(title: "浏览…", target: self, action: #selector(browseOut))
        outRow.addArrangedSubview(outField)
        outRow.addArrangedSubview(browseBtn)
        root.addArrangedSubview(outRow)

        // 格式
        let fmtRow = NSStackView()
        fmtRow.orientation = .horizontal
        fmtRow.spacing = 8
        fmtRow.addArrangedSubview(makeLabel("输出格式", 13))
        fmtPopup = NSPopUpButton()
        fmtPopup.addItems(withTitles: ["原格式（MP3 / FLAC）"])
        fmtRow.addArrangedSubview(fmtPopup)
        root.addArrangedSubview(fmtRow)

        // 开始按钮 + 打开输出文件夹（同一行）
        let actionRow = NSStackView()
        actionRow.orientation = .horizontal
        actionRow.alignment = .centerY
        actionRow.spacing = 8
        startButton = NSButton(title: "开始转换", target: self, action: #selector(startConvert))
        startButton.bezelStyle = .rounded
        startButton.controlSize = .large
        startButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 360).isActive = true
        let openBtn = NSButton(title: "打开输出文件夹", target: self, action: #selector(openOutDir))
        actionRow.addArrangedSubview(startButton)
        actionRow.addArrangedSubview(openBtn)
        root.addArrangedSubview(actionRow)

        // 进度
        progress = NSProgressIndicator()
        progress.style = .bar
        progress.isIndeterminate = false
        progress.minValue = 0
        progress.maxValue = 100
        progress.widthAnchor.constraint(greaterThanOrEqualToConstant: 400).isActive = true
        root.addArrangedSubview(progress)
        statusLabel = makeLabel("就绪", 12)
        root.addArrangedSubview(statusLabel)

        // 日志（终端风格：固定深底白字，任何外观模式下都清晰）
        let logScroll = NSScrollView()
        logView = NSTextView()
        logView.isEditable = false
        logView.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        logView.textColor = .white
        logView.backgroundColor = NSColor(calibratedWhite: 0.13, alpha: 1)
        logScroll.documentView = logView
        logScroll.hasVerticalScroller = true
        logScroll.drawsBackground = true
        logScroll.backgroundColor = NSColor(calibratedWhite: 0.13, alpha: 1)
        logScroll.heightAnchor.constraint(equalToConstant: 96).isActive = true
        logScroll.widthAnchor.constraint(greaterThanOrEqualToConstant: 400).isActive = true
        root.addArrangedSubview(logScroll)

        // 铺满
        let wrap = NSView()
        root.translatesAutoresizingMaskIntoConstraints = false
        wrap.addSubview(root)
        NSLayoutConstraint.activate([
            root.topAnchor.constraint(equalTo: wrap.topAnchor),
            root.bottomAnchor.constraint(equalTo: wrap.bottomAnchor),
            root.leadingAnchor.constraint(equalTo: wrap.leadingAnchor),
            root.trailingAnchor.constraint(equalTo: wrap.trailingAnchor),
            root.widthAnchor.constraint(greaterThanOrEqualToConstant: 560)
        ])
        return wrap
    }

    // MARK: 文件管理

    func addDroppedURLs(_ urls: [URL]) {
        var added = 0
        for u in urls {
            if u.hasDirectoryPath {
                if let items = try? FileManager.default.contentsOfDirectory(
                    at: u, includingPropertiesForKeys: nil) {
                    for f in items where f.pathExtension.lowercased() == "ncm" {
                        if addFile(f) { added += 1 }
                    }
                }
            } else if u.pathExtension.lowercased() == "ncm" {
                if addFile(u) { added += 1 }
            } else {
                log("忽略非 .ncm 文件：\(u.lastPathComponent)")
            }
        }
        if added > 0 {
            log("已加入 \(added) 个文件，共 \(files.count) 个")
            refreshStatus()
        }
    }

    @discardableResult
    func addFile(_ u: URL) -> Bool {
        if files.contains(u) { return false }
        files.append(u)
        tableView.reloadData()
        return true
    }

    @objc func pickFiles() {
        if converting { return }
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [UTType(filenameExtension: "ncm") ?? .data]
        panel.directoryURL = URL(fileURLWithPath: NSHomeDirectory() + "/Music")
        if panel.runModal() == .OK {
            var added = 0
            for u in panel.urls { if addFile(u) { added += 1 } }
            if added > 0 { log("已加入 \(added) 个文件，共 \(files.count) 个"); refreshStatus() }
        }
    }

    @objc func removeSelected() {
        let rows = tableView.selectedRowIndexes
        guard !rows.isEmpty else { return }
        var removed = 0
        for r in rows.sorted(by: >) {
            if r < files.count { files.remove(at: r); removed += 1 }
        }
        tableView.reloadData()
        log("已移除选中，剩 \(files.count) 个")
        refreshStatus()
    }

    @objc func clearFiles() {
        files.removeAll()
        tableView.reloadData()
        log("已清空列表")
        refreshStatus()
    }

    @objc func browseOut() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        if panel.runModal() == .OK, let d = panel.url {
            outField.stringValue = d.path
        }
    }

    @objc func openOutDir() {
        let d = outField.stringValue.isEmpty
            ? (NSHomeDirectory() as NSString).appendingPathComponent("Music/SPP Audio Studio/Converted")
            : outField.stringValue
        try? FileManager.default.createDirectory(atPath: d, withIntermediateDirectories: true)
        NSWorkspace.shared.open(URL(fileURLWithPath: d))
    }

    // MARK: 转换

    @objc func startConvert() {
        if converting { return }
        guard !files.isEmpty else {
            alert("请先拖入或添加 .ncm 文件", style: .informational)
            return
        }
        var outDir = outField.stringValue.trimmingCharacters(in: .whitespaces)
        if outDir.isEmpty { outDir = (NSHomeDirectory() as NSString).appendingPathComponent("Music/SPP Audio Studio/Converted") }
        do {
            try FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
        } catch {
            alert("输出目录无法创建：\n\(error.localizedDescription)", style: .warning)
            return
        }
        let fmt = fmtPopup.titleOfSelectedItem ?? "mp3"
        let list = files
        converting = true
        startButton.isEnabled = false
        progress.doubleValue = 0
        log(String(repeating: "─", count: 40))
        log("开始转换 \(list.count) 个文件 → \(outDir)（格式：\(fmt)）")

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var ok = 0, skipped = 0, failed = 0
            var errors: [String] = []
            let total = list.count
            for (idx, u) in list.enumerated() {
                DispatchQueue.main.async {
                    self?.statusLabel.stringValue = "正在转换 \(idx + 1)/\(total)：\(u.lastPathComponent)"
                    self?.log("[\(idx + 1)/\(total)] \(u.lastPathComponent)")
                }
                do {
                    let out = try convertFile(src: u, outDir: outDir, fmtChoice: fmt, skip: true)
                    ok += 1
                    DispatchQueue.main.async {
                        self?.log("    ✓ → \(out.lastPathComponent)")
                    }
                } catch is SkipFileError {
                    skipped += 1
                    DispatchQueue.main.async {
                        self?.log("    ⏭ 已存在，跳过：\(u.lastPathComponent)")
                    }
                } catch {
                    failed += 1
                    errors.append("\(u.lastPathComponent): \(error)")
                    DispatchQueue.main.async {
                        self?.log("    ✗ 失败：\(u.lastPathComponent)")
                    }
                }
                DispatchQueue.main.async {
                    self?.progress.doubleValue = Double(idx + 1) / Double(total) * 100
                }
            }
            DispatchQueue.main.async {
                self?.finishConvert(ok: ok, skipped: skipped, failed: failed, errors: errors)
            }
        }
    }

    func finishConvert(ok: Int, skipped: Int, failed: Int, errors: [String]) {
        converting = false
        startButton.isEnabled = true
        statusLabel.stringValue = "完成：成功 \(ok)，跳过 \(skipped)，失败 \(failed)"
        log("全部完成：成功 \(ok)，跳过 \(skipped)，失败 \(failed)")
        for e in errors { log("  • \(e)") }
        NSSound.beep()
        if ok > 0 {
            alert("转换完成！\n\n成功 \(ok) 个，跳过 \(skipped) 个，失败 \(failed) 个\n\n输出目录：\n\(outField.stringValue)", style: .informational)
        } else if failed > 0 {
            alert("没有转换成功，失败 \(failed) 个。\n详情见日志。", style: .warning)
        }
    }

    func refreshStatus() {
        statusLabel.stringValue = "共 \(files.count) 个文件"
    }

    func log(_ msg: String) {
        // 必须显式带颜色/字体属性，否则 NSTextView 用默认黑色渲染 append 的纯文本
        let attrs: [NSAttributedString.Key: Any] = [
            .foregroundColor: NSColor.white,
            .font: NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        ]
        logView.textStorage?.append(NSAttributedString(string: msg + "\n", attributes: attrs))
        logView.scrollToEndOfDocument(nil)
    }

    func alert(_ msg: String, style: NSAlert.Style) {
        let a = NSAlert()
        a.messageText = "格式转换"
        a.informativeText = msg
        a.alertStyle = style
        a.addButton(withTitle: "好")
        a.runModal()
    }

    // MARK: NSTableViewDataSource

    func numberOfRows(in tableView: NSTableView) -> Int { files.count }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let id = NSUserInterfaceItemIdentifier("cell")
        let cell: NSTextField
        if let reused = tableView.makeView(withIdentifier: id, owner: nil) as? NSTextField {
            cell = reused
        } else {
            cell = NSTextField(labelWithString: "")
            cell.identifier = id
            cell.lineBreakMode = .byTruncatingMiddle
        }
        cell.stringValue = files[row].lastPathComponent
        cell.toolTip = files[row].path
        return cell
    }
}

// MARK: - 入口

let args = CommandLine.arguments
if args.count > 1 {
    exit(runCLI(Array(args.dropFirst())))
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
