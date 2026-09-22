using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace SppAudioStudio.FormatConverter;

public enum ConversionStatus { Converted, Skipped }
public sealed record ConversionResult(ConversionStatus Status, string OutputPath);
public sealed class NcmException(string message) : Exception(message);

public static class NcmConverter
{
    private static readonly byte[] CoreKey = Convert.FromHexString("687A4852416D736F356B496E62617857");
    private static readonly byte[] MetaKey = Convert.FromHexString("2331346C6A6B5F215C5D2630553C2728");

    public static ConversionResult ConvertFile(string input, string outputDirectory)
    {
        if (!File.Exists(input)) throw new NcmException("文件不存在：" + input);
        if (!input.EndsWith(".ncm", StringComparison.OrdinalIgnoreCase)) throw new NcmException("输入必须是 .ncm 文件");
        byte[] data = File.ReadAllBytes(input);
        Parsed parsed = Parse(data);
        Directory.CreateDirectory(outputDirectory);
        string extension = parsed.Format.Equals("flac", StringComparison.OrdinalIgnoreCase) ? ".flac" : ".mp3";
        string output = Path.Combine(outputDirectory, Path.GetFileNameWithoutExtension(input) + extension);
        if (File.Exists(output)) return new(ConversionStatus.Skipped, output);
        byte[] decoded = CryptAudio(parsed.Audio, parsed.Rc4Key);
        byte[] tagged = extension == ".flac" ? TagFlac(decoded, parsed.Metadata, parsed.Cover) : TagMp3(decoded, parsed.Metadata, parsed.Cover);
        string temp = output + ".part-" + Guid.NewGuid().ToString("N");
        try { File.WriteAllBytes(temp, tagged); File.Move(temp, output); }
        finally { if (File.Exists(temp)) File.Delete(temp); }
        return new(ConversionStatus.Converted, output);
    }

    private sealed record Parsed(byte[] Rc4Key, string Format, byte[] Audio, JsonElement Metadata, byte[]? Cover);

    private static Parsed Parse(byte[] data)
    {
        if (data.Length <= 20 || Encoding.ASCII.GetString(data, 0, 8) != "CTENFDAM") throw new NcmException("不是有效的 .ncm 文件");
        int offset = 10;
        byte[] key = ReadBlock(data, ref offset, "key").Select(b => (byte)(b ^ 0x64)).ToArray();
        byte[] keyPlain = Decrypt(key, CoreKey);
        if (keyPlain.Length <= 17) throw new NcmException("key 数据异常");
        byte[] rc4 = keyPlain[17..];
        byte[] metaBlock = ReadBlock(data, ref offset, "meta");
        string format = "mp3"; JsonElement metadata = default;
        if (metaBlock.Length > 0)
        {
            byte[] metaXor = metaBlock.Select(b => (byte)(b ^ 0x63)).ToArray();
            if (metaXor.Length <= 22) throw new NcmException("meta 数据异常");
            byte[] encrypted = Convert.FromBase64String(Encoding.ASCII.GetString(metaXor, 22, metaXor.Length - 22));
            string text = Encoding.UTF8.GetString(Decrypt(encrypted, MetaKey));
            int marker = text.IndexOf("music:", StringComparison.Ordinal);
            if (marker >= 0) { metadata = JsonDocument.Parse(text[(marker + 6)..]).RootElement.Clone(); if (metadata.TryGetProperty("format", out var f)) format = f.GetString() ?? format; }
        }
        if (offset + 5 > data.Length) throw new NcmException("文件结构损坏"); offset += 5;
        byte[]? cover = null;
        if (offset + 8 <= data.Length)
        {
            int coverLength = ReadInt(data, ref offset), imageSize = ReadInt(data, ref offset);
            if (imageSize > 0 && imageSize <= coverLength && offset + imageSize <= data.Length) cover = data[offset..(offset + imageSize)];
            offset += Math.Min(Math.Max(coverLength, imageSize), data.Length - offset);
        }
        if (offset >= data.Length) throw new NcmException("没有音频数据");
        return new(rc4, format, data[offset..], metadata, cover);
    }

    private static byte[] ReadBlock(byte[] data, ref int offset, string name)
    {
        if (offset + 4 > data.Length) throw new NcmException($"文件结构损坏（{name} 段长度缺失）");
        int length = ReadInt(data, ref offset); if (length < 0 || offset + length > data.Length) throw new NcmException($"文件结构损坏（{name} 段越界）");
        byte[] result = data[offset..(offset + length)]; offset += length; return result;
    }
    private static int ReadInt(byte[] data, ref int offset) { int n = BitConverter.ToInt32(data, offset); offset += 4; return n; }
    private static byte[] Decrypt(byte[] data, byte[] key) { using var aes = Aes.Create(); aes.Key = key; aes.Mode = CipherMode.ECB; aes.Padding = PaddingMode.PKCS7; try { return aes.CreateDecryptor().TransformFinalBlock(data, 0, data.Length); } catch { throw new NcmException("AES 解密失败"); } }
    private static byte[] CryptAudio(byte[] bytes, byte[] key)
    {
        byte[] box = Enumerable.Range(0, 256).Select(i => (byte)i).ToArray(); int j = 0;
        for (int i = 0; i < 256; i++) { j = (j + box[i] + key[i % key.Length]) & 255; (box[i], box[j]) = (box[j], box[i]); }
        byte[] result = new byte[bytes.Length];
        for (int i = 0; i < bytes.Length; i++) { int x = (i + 1) & 255; result[i] = (byte)(bytes[i] ^ box[(box[x] + box[(x + box[x]) & 255]) & 255]); }
        return result;
    }

    private static IEnumerable<(string Id, string Key, string Value)> Fields(JsonElement meta)
    {
        string title = Get(meta, "musicName"), album = Get(meta, "album"); string artists = "";
        if (meta.ValueKind == JsonValueKind.Object && meta.TryGetProperty("artist", out var a) && a.ValueKind == JsonValueKind.Array) artists = string.Join(", ", a.EnumerateArray().Where(x => x.ValueKind == JsonValueKind.Array && x.GetArrayLength() > 0).Select(x => x[0].GetString()).Where(x => !string.IsNullOrEmpty(x)));
        if (title.Length > 0) yield return ("TIT2", "TITLE", title); if (artists.Length > 0) yield return ("TPE1", "ARTIST", artists); if (album.Length > 0) yield return ("TALB", "ALBUM", album);
    }
    private static string Get(JsonElement e, string name) => e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var p) ? p.GetString() ?? "" : "";
    private static byte[] TagMp3(byte[] audio, JsonElement meta, byte[]? cover)
    {
        using var frames = new MemoryStream();
        foreach (var f in Fields(meta)) { byte[] p = [3, .. Encoding.UTF8.GetBytes(f.Value)]; Frame(frames, f.Id, p); }
        if (cover is { Length: > 0 }) { string mime = IsPng(cover) ? "image/png" : "image/jpeg"; byte[] p = [3, .. Encoding.ASCII.GetBytes(mime), 0, 3, 0, .. cover]; Frame(frames, "APIC", p); }
        if (frames.Length == 0) return audio; byte[] body = frames.ToArray(); using var result = new MemoryStream(); result.Write(Encoding.ASCII.GetBytes("ID3")); result.Write([3, 0, 0]); result.Write([(byte)(body.Length >> 21), (byte)(body.Length >> 14 & 127), (byte)(body.Length >> 7 & 127), (byte)(body.Length & 127)]); result.Write(body); result.Write(audio); return result.ToArray();
    }
    private static void Frame(Stream s, string id, byte[] payload) { s.Write(Encoding.ASCII.GetBytes(id)); s.Write([(byte)(payload.Length >> 24), (byte)(payload.Length >> 16), (byte)(payload.Length >> 8), (byte)payload.Length, 0, 0]); s.Write(payload); }
    private static byte[] TagFlac(byte[] audio, JsonElement meta, byte[]? cover)
    {
        if (audio.Length < 4 || Encoding.ASCII.GetString(audio, 0, 4) != "fLaC") return audio;
        var blocks = new List<(byte Type, byte[] Payload)>(); int offset = 4; bool last = false;
        while (!last && offset + 4 <= audio.Length)
        {
            byte header = audio[offset++]; last = (header & 0x80) != 0; byte type = (byte)(header & 0x7f); int length = audio[offset++] << 16 | audio[offset++] << 8 | audio[offset++];
            if (offset + length > audio.Length) return audio;
            if (type != 4 && type != 6) blocks.Add((type, audio[offset..(offset + length)])); offset += length;
        }
        if (!last) return audio;
        var fields = Fields(meta).ToArray(); using var comment = new MemoryStream(); byte[] vendor = Encoding.UTF8.GetBytes("SPP Audio Studio"); WriteLe(comment, vendor.Length); comment.Write(vendor); WriteLe(comment, fields.Length);
        foreach (var f in fields) { byte[] item = Encoding.UTF8.GetBytes(f.Key + "=" + f.Value); WriteLe(comment, item.Length); comment.Write(item); } blocks.Add((4, comment.ToArray()));
        if (cover is { Length: > 0 }) { string mime = IsPng(cover) ? "image/png" : "image/jpeg"; using var picture = new MemoryStream(); WriteBe(picture, 3); byte[] m = Encoding.ASCII.GetBytes(mime); WriteBe(picture, m.Length); picture.Write(m); WriteBe(picture, 0); for (int i = 0; i < 4; i++) WriteBe(picture, 0); WriteBe(picture, cover.Length); picture.Write(cover); blocks.Add((6, picture.ToArray())); }
        using var result = new MemoryStream(); result.Write(Encoding.ASCII.GetBytes("fLaC")); for (int i = 0; i < blocks.Count; i++) { var b = blocks[i]; result.WriteByte((byte)(b.Type | (i == blocks.Count - 1 ? 0x80 : 0))); result.Write([(byte)(b.Payload.Length >> 16), (byte)(b.Payload.Length >> 8), (byte)b.Payload.Length]); result.Write(b.Payload); } result.Write(audio[offset..]); return result.ToArray();
    }
    private static void WriteLe(Stream s, int n) => s.Write(BitConverter.GetBytes(n));
    private static void WriteBe(Stream s, int n) => s.Write([(byte)(n >> 24), (byte)(n >> 16), (byte)(n >> 8), (byte)n]);
    private static bool IsPng(byte[] b) => b.Length >= 4 && b[..4].SequenceEqual(new byte[] { 0x89, 0x50, 0x4e, 0x47 });
}

internal static class Program
{
    public static int Main(string[] args)
    {
        try
        {
            if (args.Length < 3 || args[1] != "--out") { Console.Error.WriteLine("用法: format_converter.exe <input.ncm> --out <dir>"); return 2; }
            var result = NcmConverter.ConvertFile(args[0], args[2]); Console.WriteLine(result.Status == ConversionStatus.Skipped ? $"⏭ 已存在，跳过：{Path.GetFileName(args[0])}" : $"✓ {Path.GetFileName(args[0])} → {Path.GetFileName(result.OutputPath)}"); return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine("✗ " + ex.Message); return 1; }
    }
}
