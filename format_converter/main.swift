// SPP Audio Studio - local format converter (workflow + metadata/cover tagging)
// Copyright (c) 2026 Song Panpan / 宋盼盼
// MIT License. Algorithm references/credits: see THIRD_PARTY_NOTICES.md.
//
// Build modes:
//   default              Built-in local conversion engine included.
//   -D SPP_NO_BUILTIN_NCM  No built-in engine; the file itself keeps only the
//                          workflow, tagging and output handling, and conversion
//                          is delegated to a user-supplied local tool
//                          (External Converter Adapter, see docs/EXTERNAL_CONVERTER.md).

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

// MARK: - 特殊格式解密核心（可选编译）
//
// 公开免费分发时可以加 -D SPP_NO_BUILTIN_NCM 编译出不带固定 key 与 AES 解密核心的
// 版本：这种情况下主程序只负责工作流、标签写入和输出管理，特殊格式的底层转换交给
// 用户在「模型与环境」中自行指定的本地兼容转换器（External Converter Adapter）。

#if !SPP_NO_BUILTIN_NCM

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

#endif  // !SPP_NO_BUILTIN_NCM

// MARK: - 标签写入（MP3 ID3v2.3 / FLAC Vorbis Comment + Picture）

func be32(_ n: Int) -> Data {
    Data([UInt8((n >> 24) & 255), UInt8((n >> 16) & 255), UInt8((n >> 8) & 255), UInt8(n & 255)])
}

func le32(_ n: Int) -> Data {
    Data([UInt8(n & 255), UInt8((n >> 8) & 255), UInt8((n >> 16) & 255), UInt8((n >> 24) & 255)])
}

struct TagBundle {
    var title = ""
    var artist = ""
    var album = ""
    /// 来源信息字段：MP3 写 TXXX，FLAC 写 Vorbis Comment 自定义键
    var sourceInfo: [(String, String)] = []
    var cover: Data?
}

func bundleIsEmpty(_ bundle: TagBundle) -> Bool {
    bundle.title.isEmpty && bundle.artist.isEmpty && bundle.album.isEmpty
        && bundle.sourceInfo.isEmpty && (bundle.cover?.isEmpty ?? true)
}

/// NCM metadata → 标签（内置转换路径使用）
func ncmTagBundle(_ meta: [String: Any]) -> TagBundle {
    var bundle = TagBundle()
    bundle.title = meta["musicName"] as? String ?? ""
    bundle.artist = (meta["artist"] as? [[Any]] ?? []).compactMap { $0.first as? String }.joined(separator: ", ")
    bundle.album = meta["album"] as? String ?? ""
    return bundle
}

/// 外部转换器的 sidecar（spp-meta.json）→ 标签；字段全部可选，缺省即不写。
func sidecarTagBundle(_ obj: [String: Any]) -> TagBundle {
    var bundle = TagBundle()
    bundle.title = obj["title"] as? String ?? ""
    bundle.artist = obj["artist"] as? String ?? ""
    bundle.album = obj["album"] as? String ?? ""
    if let musicID = obj["music_id"] as? String, !musicID.isEmpty {
        bundle.sourceInfo.append(("NETEASE_MUSIC_ID", musicID))
    }
    if let albumID = obj["album_id"] as? String, !albumID.isEmpty {
        bundle.sourceInfo.append(("NETEASE_ALBUM_ID", albumID))
    }
    return bundle
}

// MARK: - ID3v2 标签写入与合并

// ID3v2.3 只定义 00 = ISO-8859-1 与 01 = UTF-16；UTF-8 的 03 属于 ID3v2.4。
// 旧实现写了「v2.3 头 + UTF-8 frame」，严格播放器会整帧忽略；现在按目标版本分别编码：
// v2.3 → UTF-16 + BOM，v2.4 → UTF-8。
func utf16BOM(_ text: String) -> Data {
    var data = Data([0xFF, 0xFE])
    for unit in Array(text.utf16) {
        data.append(UInt8(unit & 0xFF))
        data.append(UInt8((unit >> 8) & 0xFF))
    }
    return data
}

func beSynchsafe(_ n: Int) -> Data {
    Data([UInt8((n >> 21) & 127), UInt8((n >> 14) & 127), UInt8((n >> 7) & 127), UInt8(n & 127)])
}

func synchsafeValue(_ data: Data, at offset: Int) -> Int? {
    guard offset + 4 <= data.count else { return nil }
    let bytes = [data[offset], data[offset + 1], data[offset + 2], data[offset + 3]]
    guard bytes.allSatisfy({ $0 & 0x80 == 0 }) else { return nil }
    return (Int(bytes[0]) << 21) | (Int(bytes[1]) << 14) | (Int(bytes[2]) << 7) | Int(bytes[3])
}

func plainValue32(_ data: Data, at offset: Int) -> Int? {
    guard offset + 4 <= data.count else { return nil }
    return (Int(data[offset]) << 24) | (Int(data[offset + 1]) << 16) | (Int(data[offset + 2]) << 8) | Int(data[offset + 3])
}

struct ID3Frame {
    let id: String
    /// 完整帧字节（id + size + flags + payload），原样保留以便安全回写
    let raw: Data
    let payload: Data
}

/// 只解析文件开头已有的 ID3v2.3 / v2.4 标签；结构异常一律返回 nil，由调用方退回保守写法。
func parseID3Tag(_ audio: Data) -> (version: Int, frames: [ID3Frame], audioOffset: Int)? {
    guard audio.count >= 10, audio.prefix(3) == Data("ID3".utf8) else { return nil }
    let version = Int(audio[3])
    guard version == 3 || version == 4 else { return nil }
    guard audio[5] & 0xE0 == 0 else { return nil }   // 同步化 / 扩展头 / experimental / footer：不碰
    guard let size = synchsafeValue(audio, at: 6), size > 0 else { return nil }
    let tagEnd = 10 + size
    guard tagEnd <= audio.count else { return nil }

    var frames: [ID3Frame] = []
    var offset = 10
    while offset + 10 <= tagEnd {
        if audio[offset] == 0 { break }   // 进入 padding
        guard let id = String(data: Data(audio[offset..<offset + 4]), encoding: .ascii),
              id.allSatisfy({ ($0.isUppercase && $0.isLetter) || $0.isNumber }) else { return nil }
        let rawSize = version == 4
            ? synchsafeValue(audio, at: offset + 4)
            : plainValue32(audio, at: offset + 4)
        guard let frameSize = rawSize, offset + 10 + frameSize <= tagEnd else { return nil }
        frames.append(ID3Frame(id: id,
                               raw: Data(audio[offset..<offset + 10 + frameSize]),
                               payload: Data(audio[offset + 10..<offset + 10 + frameSize])))
        offset += 10 + frameSize
    }
    return (version, frames, tagEnd)
}

func id3TextEncoding(_ version: Int) -> UInt8 { version == 4 ? 3 : 1 }   // v2.4 → UTF-8，v2.3 → UTF-16

func id3EncodedText(_ text: String, version: Int) -> Data {
    version == 4 ? Data(text.utf8) : utf16BOM(text)
}

func id3Frame(version: Int, id: String, payload: Data) -> Data {
    var frame = Data(id.utf8)
    frame.append(version == 4 ? beSynchsafe(payload.count) : be32(payload.count))
    frame.append(Data([0, 0]))
    frame.append(payload)
    return frame
}

func id3TextPayload(_ text: String, version: Int) -> Data {
    var payload = Data([id3TextEncoding(version)])
    payload.append(id3EncodedText(text, version: version))
    return payload
}

func id3UserTextPayload(description: String, value: String, version: Int) -> Data {
    let encoding = id3TextEncoding(version)
    var payload = Data([encoding])
    payload.append(id3EncodedText(description, version: version))
    payload.append(encoding == 1 ? Data([0, 0]) : Data([0]))
    payload.append(id3EncodedText(value, version: version))
    return payload
}

func id3CoverPayload(_ cover: Data) -> Data {
    let mime = cover.starts(with: Data([0x89, 0x50, 0x4E, 0x47])) ? "image/png" : "image/jpeg"
    var payload = Data([0])   // ISO-8859-1：description 为空，只需 1 字节终止符
    payload.append(Data(mime.utf8))
    payload.append(Data([0]))
    payload.append(Data([3])) // front cover
    payload.append(Data([0]))
    payload.append(cover)
    return payload
}

/// APIC 的图片类型（3 = front cover）；解析不出来返回 nil。
func id3CoverType(_ payload: Data) -> UInt8? {
    guard payload.count >= 4 else { return nil }
    var index = 1
    while index < payload.count, payload[index] != 0 { index += 1 }   // 跳过 MIME 串
    index += 1
    guard index < payload.count else { return nil }
    return payload[index]
}

/// 合并式写入：源文件已有 ID3v2 标签时保留其它帧、只替换 SPP 负责的字段，避免出现双标签。
func taggedMP3(_ audio: Data, bundle: TagBundle) -> Data {
    let existing = parseID3Tag(audio)
    let version = existing?.version ?? 3
    let hasCover = bundle.cover?.isEmpty == false

    var ours: [(String, Data)] = []
    if !bundle.title.isEmpty { ours.append(("TIT2", id3TextPayload(bundle.title, version: version))) }
    if !bundle.artist.isEmpty { ours.append(("TPE1", id3TextPayload(bundle.artist, version: version))) }
    if !bundle.album.isEmpty { ours.append(("TALB", id3TextPayload(bundle.album, version: version))) }
    for (key, value) in bundle.sourceInfo {
        ours.append(("TXXX", id3UserTextPayload(description: key, value: value, version: version)))
    }
    if hasCover, let cover = bundle.cover {
        ours.append(("APIC", id3CoverPayload(cover)))
    }
    guard !ours.isEmpty else { return audio }

    // 文本帧按 ID 接管；TXXX 与 APIC 可能有多条合法帧，用内容去重而不是整类删除
    let replacedIDs = Set(ours.map { $0.0 }).subtracting(["TXXX", "APIC"])
    var frames = Data()
    if let existing = existing {
        for frame in existing.frames {
            if replacedIDs.contains(frame.id) { continue }
            if hasCover, frame.id == "APIC", id3CoverType(frame.payload) == 3 { continue }
            if ours.contains(where: { $0.0 == frame.id && $0.1 == frame.payload }) { continue }
            frames.append(frame.raw)
        }
    }
    for (id, payload) in ours {
        frames.append(id3Frame(version: version, id: id, payload: payload))
    }

    let length = frames.count
    var result = Data("ID3".utf8)
    result.append(Data([UInt8(version), 0, 0]))
    result.append(beSynchsafe(length))
    result.append(frames)
    if let existing = existing {
        result.append(audio[existing.audioOffset...])
    } else {
        result.append(audio)
    }
    return result
}

func flacBlock(type: UInt8, last: Bool, payload: Data) -> Data {
    var result = Data([type | (last ? 0x80 : 0), UInt8((payload.count >> 16) & 255), UInt8((payload.count >> 8) & 255), UInt8(payload.count & 255)])
    result.append(payload)
    return result
}

struct FlacLayout {
    /// 除 VORBIS_COMMENT 外的原有 metadata 块，保持原有顺序（STREAMINFO 必须在最前）
    var otherBlocks: [(UInt8, Data)] = []
    var comments: [(String, String)] = []
    var vendor: String? = nil
    var commentParsed = false
    /// 存在但解析不了的 VORBIS_COMMENT 块：不能原样再写一遍（会出现两个同类型块），
    /// 因此单独记下来，写的时候用新块替换它。
    var rawCommentPayload: Data? = nil
    var pictures: [Data] = []
    var audioOffset = 0
}

/// 只解析 FLAC metadata 区，不改动音频帧；解析失败返回 nil，由调用方决定保守行为。
func parseFlacLayout(_ audio: Data) -> FlacLayout? {
    guard audio.starts(with: Data("fLaC".utf8)) else { return nil }
    var layout = FlacLayout()
    var offset = 4
    var last = false
    while !last && offset + 4 <= audio.count {
        let header = audio[offset]
        last = header & 0x80 != 0
        let kind = header & 0x7F
        let length = Int(audio[offset + 1]) << 16 | Int(audio[offset + 2]) << 8 | Int(audio[offset + 3])
        offset += 4
        guard offset + length <= audio.count else { return nil }
        let payload = Data(audio[offset..<offset + length])
        offset += length
        if kind == 4 {
            // FLAC 规范只允许一个 VORBIS_COMMENT 块：已有一个就不再收第二个
            guard !layout.commentParsed, layout.rawCommentPayload == nil else { continue }
            if let parsed = parseVorbisComment(payload) {
                layout.comments.append(contentsOf: parsed.0)
                if layout.vendor == nil { layout.vendor = parsed.1 }
                layout.commentParsed = true
            } else {
                layout.rawCommentPayload = payload
            }
        } else if kind == 6 {
            layout.pictures.append(payload)
        } else {
            layout.otherBlocks.append((kind, payload))
        }
    }
    guard last else { return nil }
    // 音频帧起始位置必须落在文件内，且第一个块必须是 STREAMINFO，否则不动这个文件
    guard offset <= audio.count, layout.otherBlocks.first?.0 == 0 else { return nil }
    layout.audioOffset = offset
    return layout
}

func parseVorbisComment(_ payload: Data) -> ([(String, String)], String)? {
    func le32at(_ offset: Int) -> Int? {
        guard offset + 4 <= payload.count else { return nil }
        return Int(payload[offset]) | (Int(payload[offset + 1]) << 8)
            | (Int(payload[offset + 2]) << 16) | (Int(payload[offset + 3]) << 24)
    }
    guard let vendorLength = le32at(0) else { return nil }
    let vendorStart = 4
    guard vendorStart + vendorLength + 4 <= payload.count else { return nil }
    let vendor = String(data: payload[vendorStart..<vendorStart + vendorLength], encoding: .utf8) ?? ""
    var offset = vendorStart + vendorLength
    guard let count = le32at(offset) else { return nil }
    offset += 4
    var entries: [(String, String)] = []
    for _ in 0..<count {
        guard let length = le32at(offset) else { return nil }
        offset += 4
        guard offset + length <= payload.count else { return nil }
        let item = String(data: payload[offset..<offset + length], encoding: .utf8) ?? ""
        offset += length
        if let separator = item.firstIndex(of: "=") {
            let key = String(item[item.startIndex..<separator]).uppercased()
            entries.append((key, String(item[item.index(after: separator)...])))
        }
    }
    return (entries, vendor)
}

func flacPicturePayload(_ cover: Data) -> Data {
    let mime = Data((cover.starts(with: Data([0x89, 0x50, 0x4E, 0x47])) ? "image/png" : "image/jpeg").utf8)
    var picture = be32(3)
    picture.append(be32(mime.count)); picture.append(mime)
    picture.append(be32(0))   // description
    for _ in 0..<4 { picture.append(be32(0)) } // 宽高深与调色板数：置 0 由播放器自行判断
    picture.append(be32(cover.count)); picture.append(cover)
    return picture
}

func flacPictureType(_ payload: Data) -> UInt32? {
    guard payload.count >= 4 else { return nil }
    return (UInt32(payload[0]) << 24) | (UInt32(payload[1]) << 16) | (UInt32(payload[2]) << 8) | UInt32(payload[3])
}

/// 合并式写入：保留原有 Vorbis Comment 与 Picture，只覆盖 SPP 明确要设的字段。
func taggedFLAC(_ audio: Data, bundle: TagBundle) -> Data {
    guard !bundleIsEmpty(bundle) else { return audio }
    guard let layout = parseFlacLayout(audio) else { return audio }

    let overridden = Set(["TITLE", "ARTIST", "ALBUM"]).union(bundle.sourceInfo.map { $0.0 })
    var fields = layout.comments.filter { !overridden.contains($0.0) }
    if !bundle.title.isEmpty { fields.append(("TITLE", bundle.title)) }
    if !bundle.artist.isEmpty { fields.append(("ARTIST", bundle.artist)) }
    if !bundle.album.isEmpty { fields.append(("ALBUM", bundle.album)) }
    fields.append(contentsOf: bundle.sourceInfo)

    var blocks = layout.otherBlocks
    // 规范只允许一个 VORBIS_COMMENT：原有块可读就保留它没被覆盖的字段；
    // 原本不可读也要用新块替换，绝不能把坏块原样再写一遍。
    if !fields.isEmpty || layout.commentParsed || layout.rawCommentPayload != nil {
        let vendor = Data((layout.vendor ?? "SPP Audio Studio").utf8)
        var comments = Data()
        comments.append(le32(vendor.count)); comments.append(vendor)
        comments.append(le32(fields.count))
        for (key, value) in fields {
            let item = Data("\(key)=\(value)".utf8)
            comments.append(le32(item.count)); comments.append(item)
        }
        blocks.append((4, comments))
    }
    // 原有 PICTURE 全部保留（如封底、艺人图）；仅把 3 = 正面封面换成新封面
    var pictures = layout.pictures
    if let cover = bundle.cover, !cover.isEmpty {
        let ours = flacPicturePayload(cover)
        pictures.removeAll { $0 == ours || flacPictureType($0) == 3 }
        pictures.append(ours)
    }
    blocks.append(contentsOf: pictures.map { (UInt8(6), $0) })

    var result = Data("fLaC".utf8)
    for (index, block) in blocks.enumerated() {
        result.append(flacBlock(type: block.0, last: index == blocks.count - 1, payload: block.1))
    }
    result.append(audio[layout.audioOffset...])
    return result
}

#if !SPP_NO_BUILTIN_NCM
func builtinConvertFile(src: URL, outDir: String, fmtChoice: String, skip: Bool) throws -> URL {
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
    let bundle = ncmTagBundle(parsed.metadata)
    var bundleWithCover = bundle
    bundleWithCover.cover = parsed.cover
    let tagged = parsed.format.lowercased() == "flac"
        ? taggedFLAC(decoded, bundle: bundleWithCover)
        : taggedMP3(decoded, bundle: bundleWithCover)
    try tagged.write(to: outURL)
    return outURL
}
#endif  // !SPP_NO_BUILTIN_NCM

// MARK: - 转换引擎（内置引擎 / 外部转换器适配器）

/// 转换引擎设置。优先级：命令行参数 > 环境变量 > 本地配置文件。
///
/// 配置文件：`~/Library/Application Support/SPP Audio Studio/external_converter.json`
/// 形如：`{"command": "/绝对路径/你的本地转换器"}`
enum ConverterEngineSettings {
    static let envKey = "SPP_EXTERNAL_CONVERTER"

    static var configURL: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory())
        return base.appendingPathComponent("SPP Audio Studio/external_converter.json")
    }

    static func savedPath() -> String? {
        guard let data = try? Data(contentsOf: configURL),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let command = object["command"] as? String else { return nil }
        let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : (trimmed as NSString).expandingTildeInPath
    }

    static func save(_ path: String) {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        let url = configURL
        do {
            if trimmed.isEmpty {
                if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
                return
            }
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            let payload: [String: String] = ["command": (trimmed as NSString).expandingTildeInPath]
            let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: url, options: .atomic)
        } catch {
            // 设置写不进去不能影响转换本身
        }
    }

    static func resolvedPath(cliValue: String? = nil) -> String? {
        let candidates = [
            cliValue,
            ProcessInfo.processInfo.environment[envKey],
            savedPath(),
        ]
        for candidate in candidates {
            if let value = candidate?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty {
                return (value as NSString).expandingTildeInPath
            }
        }
        return nil
    }

    static var builtinAvailable: Bool {
        #if SPP_NO_BUILTIN_NCM
        return false
        #else
        return true
        #endif
    }

    static var summary: String {
        builtinAvailable ? "内置引擎" : "不含内置引擎（仅外部转换器）"
    }
}

/// External Converter Adapter。
///
/// 这个适配器只做编排：调用你本机自备的转换器，读回它产出的音频文件和可选
/// sidecar，再由 SPP 写入标签/封面并落到输出目录。SPP 不下载、不捆绑、不推荐
/// 任何具体转换器，也不接触任何账号或云端服务。
///
/// 约定（见 docs/EXTERNAL_CONVERTER.md）：
///   `<转换器> --in <源文件> --out <工作目录> --format <mp3|flac|original>`
/// 退出码 0 表示成功；输出目录里出现音频文件即视为成功。
/// 可选 sidecar：工作目录下的 `spp-meta.json`，字段 title / artist / album /
/// music_id / album_id / cover（图片路径）/ cover_base64。
struct ExternalConverterAdapter {
    let executable: URL

    /// 未配置返回 nil；配置了但不可执行直接抛错，避免悄悄退回内置引擎。
    static func make(path: String?) throws -> ExternalConverterAdapter? {
        guard let path, !path.isEmpty else { return nil }
        let url = URL(fileURLWithPath: path)
        guard FileManager.default.isExecutableFile(atPath: url.path) else {
            throw NcmError("外部转换器不可执行：\(url.path)")
        }
        return ExternalConverterAdapter(executable: url)
    }

    static func normalizedFormat(_ fmtChoice: String) -> String {
        let lower = fmtChoice.lowercased()
        if lower.contains("flac") { return "flac" }
        if lower.contains("mp3") { return "mp3" }
        return "original"
    }

    func run(src: URL, workDir: URL, fmtChoice: String) throws {
        // 输出重定向到文件而不是管道：转换器输出较多时管道会把它顶死
        let stdoutURL = workDir.appendingPathComponent("converter.stdout.log")
        let stderrURL = workDir.appendingPathComponent("converter.stderr.log")
        FileManager.default.createFile(atPath: stdoutURL.path, contents: nil)
        FileManager.default.createFile(atPath: stderrURL.path, contents: nil)
        guard let stdoutHandle = FileHandle(forWritingAtPath: stdoutURL.path),
              let stderrHandle = FileHandle(forWritingAtPath: stderrURL.path) else {
            throw NcmError("无法创建外部转换器的日志文件")
        }
        defer {
            try? stdoutHandle.close()
            try? stderrHandle.close()
        }

        let process = Process()
        process.executableURL = executable
        process.arguments = ["--in", src.path, "--out", workDir.path, "--format", Self.normalizedFormat(fmtChoice)]
        process.standardOutput = stdoutHandle
        process.standardError = stderrHandle

        // 兜底超时：转换器挂死时不要让界面一直等
        let watchdog = DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + 900, execute: watchdog)
        defer { watchdog.cancel() }

        do {
            try process.run()
        } catch {
            throw NcmError("外部转换器无法启动：\(error.localizedDescription)")
        }
        process.waitUntilExit()

        guard process.terminationStatus == 0 else {
            let text = (try? String(contentsOf: stderrURL, encoding: .utf8)) ?? ""
            let fallback = (try? String(contentsOf: stdoutURL, encoding: .utf8)) ?? ""
            let detail = (text.isEmpty ? fallback : text).trimmingCharacters(in: .whitespacesAndNewlines)
            throw NcmError("外部转换器退出码 \(process.terminationStatus)：\(String(detail.suffix(400)))")
        }
    }
}

let externalAudioExtensions: Set<String> = ["mp3", "flac"]

func locateConverterOutput(in workDir: URL) throws -> URL {
    let entries = (try? FileManager.default.contentsOfDirectory(at: workDir, includingPropertiesForKeys: [.fileSizeKey])) ?? []
    let audio = entries.filter { externalAudioExtensions.contains($0.pathExtension.lowercased()) }
    guard let best = audio.max(by: { left, right in
        let leftSize = (try? left.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        let rightSize = (try? right.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        return leftSize < rightSize
    }) else {
        throw NcmError("外部转换器没有产出 MP3 或 FLAC 文件")
    }
    return best
}

func converterSidecar(in workDir: URL, produced: URL) -> [String: Any]? {
    let candidates = [
        workDir.appendingPathComponent("spp-meta.json"),
        produced.appendingPathExtension("spp-meta.json"),
    ]
    for url in candidates {
        guard let data = try? Data(contentsOf: url),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
        return object
    }
    return nil
}

func converterSidecarCover(_ sidecar: [String: Any], workDir: URL) -> Data? {
    if let base64 = sidecar["cover_base64"] as? String,
       let data = Data(base64Encoded: base64.trimmingCharacters(in: .whitespacesAndNewlines)),
       !data.isEmpty {
        return data
    }
    guard let raw = sidecar["cover"] as? String else { return nil }
    let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return nil }
    let url = trimmed.hasPrefix("/")
        ? URL(fileURLWithPath: trimmed)
        : workDir.appendingPathComponent(trimmed)
    guard let data = try? Data(contentsOf: url), !data.isEmpty else { return nil }
    return data
}

func uniqueOutputURL(basedOn url: URL) -> URL {
    let directory = url.deletingLastPathComponent()
    let base = url.deletingPathExtension().lastPathComponent
    let ext = url.pathExtension
    var index = 1
    while index < 1000 {
        let candidate = directory.appendingPathComponent("\(base) \(index)").appendingPathExtension(ext)
        if !FileManager.default.fileExists(atPath: candidate.path) { return candidate }
        index += 1
    }
    return directory.appendingPathComponent("\(base) \(UUID().uuidString)").appendingPathExtension(ext)
}

/// 外部转换器路线：源文件只读，转换在工作目录里发生，最后复制到输出目录。
func externalConvertFile(
    src: URL,
    outDir: String,
    fmtChoice: String,
    skip: Bool,
    adapter: ExternalConverterAdapter
) throws -> URL {
    let fm = FileManager.default
    try fm.createDirectory(atPath: outDir, withIntermediateDirectories: true)
    let workDir = fm.temporaryDirectory.appendingPathComponent("spp-external-\(UUID().uuidString)")
    try fm.createDirectory(at: workDir, withIntermediateDirectories: true)
    defer { try? fm.removeItem(at: workDir) }

    try adapter.run(src: src, workDir: workDir, fmtChoice: fmtChoice)

    let produced = try locateConverterOutput(in: workDir)
    let ext = produced.pathExtension.lowercased()
    let baseURL = URL(fileURLWithPath: outDir)
        .appendingPathComponent(src.deletingPathExtension().lastPathComponent)
        .appendingPathExtension(ext)
    if skip && fm.fileExists(atPath: baseURL.path) { throw SkipFileError() }
    let outURL = fm.fileExists(atPath: baseURL.path) ? uniqueOutputURL(basedOn: baseURL) : baseURL

    var data = try Data(contentsOf: produced)
    if let sidecar = converterSidecar(in: workDir, produced: produced) {
        var bundle = sidecarTagBundle(sidecar)
        if let cover = converterSidecarCover(sidecar, workDir: workDir) { bundle.cover = cover }
        if !bundleIsEmpty(bundle) {
            if ext == "flac" {
                data = taggedFLAC(data, bundle: bundle)
            } else if ext == "mp3" {
                data = taggedMP3(data, bundle: bundle)
            }
        }
    }
    try data.write(to: outURL, options: .atomic)
    return outURL
}

/// 统一入口：配了外部转换器就用外部转换器，否则用内置引擎（未编入时给出明确提示）。
func convertFile(src: URL, outDir: String, fmtChoice: String, skip: Bool, converterPath: String? = nil) throws -> URL {
    let configured = ConverterEngineSettings.resolvedPath(cliValue: converterPath)
    if let adapter = try ExternalConverterAdapter.make(path: configured) {
        return try externalConvertFile(src: src, outDir: outDir, fmtChoice: fmtChoice, skip: skip, adapter: adapter)
    }
    #if SPP_NO_BUILTIN_NCM
    throw NcmError("本版本不含内置特殊格式引擎。请先用 --converter 或 SPP_EXTERNAL_CONVERTER 指定本机自备的转换器。")
    #else
    return try builtinConvertFile(src: src, outDir: outDir, fmtChoice: fmtChoice, skip: skip)
    #endif
}

// MARK: - 命令行模式

func runCLI(_ args: [String]) -> Int32 {
    if args.contains("--engine") || args.contains("--help") {
        print("引擎：\(ConverterEngineSettings.summary)")
        print("外部转换器：\(ConverterEngineSettings.resolvedPath() ?? "未设置")")
        print("设置文件：\(ConverterEngineSettings.configURL.path)")
        print("用法: format_converter <文件.ncm ...> [--out 输出目录] [--fmt mp3|flac|原格式] [--converter 转换器路径]")
        return 0
    }
    var files: [String] = []
    var outDir = (NSHomeDirectory() as NSString).appendingPathComponent("Music/SPP Audio Studio/Converted")
    var fmt = "mp3"
    var converterPath: String?
    var i = 0
    while i < args.count {
        let a = args[i]
        if a == "--out" { i += 1; if i < args.count { outDir = args[i] } }
        else if a == "--fmt" { i += 1; if i < args.count { fmt = args[i] } }
        else if a == "--converter" { i += 1; if i < args.count { converterPath = args[i] } }
        else if a.hasSuffix(".ncm") { files.append(a) }
        i += 1
    }
    guard !files.isEmpty else {
        FileHandle.standardError.write(Data(
            "用法: format_converter <文件.ncm ...> [--out 输出目录] [--fmt mp3|flac|原格式] [--converter 转换器路径]\n无参数启动图形界面；--engine 查看当前转换引擎\n".utf8))
        return 2
    }
    try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
    var ok = 0, skipped = 0, failed = 0
    for f in files {
        let name = (f as NSString).lastPathComponent
        do {
            let out = try convertFile(src: URL(fileURLWithPath: f), outDir: outDir,
                                      fmtChoice: fmt, skip: true, converterPath: converterPath)
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
