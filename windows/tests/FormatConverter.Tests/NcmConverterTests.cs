using Microsoft.VisualStudio.TestTools.UnitTesting;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using SppAudioStudio.FormatConverter;

namespace FormatConverter.Tests;

[TestClass]
public sealed class NcmConverterTests
{
    private static readonly byte[] CoreKey = Convert.FromHexString("687A4852416D736F356B496E62617857");
    private static readonly byte[] MetaKey = Convert.FromHexString("2331346C6A6B5F215C5D2630553C2728");

    [TestMethod]
    public void ConvertFile_DecryptsMp3AndWritesId3MetadataAndCover()
    {
        using var temp = new TempDirectory();
        byte[] audio = [0xff, 0xfb, 0x90, 0x64, 1, 2, 3, 4];
        byte[] cover = [0x89, 0x50, 0x4e, 0x47, 9, 8, 7];
        var metadata = new Dictionary<string, object?>
        {
            ["format"] = "mp3", ["musicName"] = "测试曲目", ["album"] = "Album",
            ["artist"] = new object[] { new object[] { "Artist A", 1 }, new object[] { "Artist B", 2 } }
        };
        string input = Path.Combine(temp.Path, "song.ncm");
        File.WriteAllBytes(input, MakeNcm(audio, metadata, cover));
        string outputDirectory = Path.Combine(temp.Path, "out");

        ConversionResult result = NcmConverter.ConvertFile(input, outputDirectory);

        Assert.AreEqual(ConversionStatus.Converted, result.Status);
        Assert.AreEqual(Path.Combine(outputDirectory, "song.mp3"), result.OutputPath);
        byte[] output = File.ReadAllBytes(result.OutputPath);
        CollectionAssert.AreEqual(Encoding.ASCII.GetBytes("ID3"), output[..3]);
        string tagText = Encoding.UTF8.GetString(output);
        StringAssert.Contains(tagText, "测试曲目");
        StringAssert.Contains(tagText, "Artist A, Artist B");
        StringAssert.Contains(tagText, "Album");
        StringAssert.Contains(tagText, "image/png");
        CollectionAssert.AreEqual(audio, output[^audio.Length..]);
    }

    internal static byte[] MakeNcm(byte[] plainAudio, Dictionary<string, object?>? metadata, byte[]? cover = null, byte[]? rc4Key = null)
    {
        rc4Key ??= Encoding.ASCII.GetBytes("local-test-key");
        byte[] encryptedAudio = CryptAudio(plainAudio, rc4Key);
        byte[] keyPlain = Encoding.ASCII.GetBytes("neteasecloudmusic").Concat(rc4Key).ToArray();
        byte[] encryptedKey = EncryptPkcs7(keyPlain, CoreKey).Select(b => (byte)(b ^ 0x64)).ToArray();
        byte[] encryptedMeta = [];
        if (metadata is not null)
        {
            byte[] metaPlain = Encoding.UTF8.GetBytes("music:" + JsonSerializer.Serialize(metadata));
            string base64 = Convert.ToBase64String(EncryptPkcs7(metaPlain, MetaKey));
            encryptedMeta = Encoding.ASCII.GetBytes("163 key(Don't modify):" + base64).Select(b => (byte)(b ^ 0x63)).ToArray();
        }
        cover ??= [];
        using var stream = new MemoryStream();
        stream.Write(Encoding.ASCII.GetBytes("CTENFDAM"));
        stream.Write([0, 0]);
        WriteUInt32(stream, encryptedKey.Length); stream.Write(encryptedKey);
        WriteUInt32(stream, encryptedMeta.Length); stream.Write(encryptedMeta);
        stream.Write(new byte[5]);
        WriteUInt32(stream, cover.Length); WriteUInt32(stream, cover.Length); stream.Write(cover);
        stream.Write(encryptedAudio);
        return stream.ToArray();
    }

    private static byte[] EncryptPkcs7(byte[] data, byte[] key)
    {
        using var aes = Aes.Create(); aes.Key = key; aes.Mode = CipherMode.ECB; aes.Padding = PaddingMode.PKCS7;
        return aes.CreateEncryptor().TransformFinalBlock(data, 0, data.Length);
    }

    private static byte[] CryptAudio(byte[] bytes, byte[] key)
    {
        byte[] box = Enumerable.Range(0, 256).Select(i => (byte)i).ToArray();
        int j = 0;
        for (int i = 0; i < 256; i++) { j = (j + box[i] + key[i % key.Length]) & 0xff; (box[i], box[j]) = (box[j], box[i]); }
        byte[] result = new byte[bytes.Length];
        for (int i = 0; i < bytes.Length; i++)
        {
            int x = (i + 1) & 0xff;
            byte stream = box[(box[x] + box[(x + box[x]) & 0xff]) & 0xff];
            result[i] = (byte)(bytes[i] ^ stream);
        }
        return result;
    }

    private static void WriteUInt32(Stream stream, int value) => stream.Write(BitConverter.GetBytes((uint)value));

    private sealed class TempDirectory : IDisposable
    {
        public string Path { get; } = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "spp-ncm-tests-" + Guid.NewGuid().ToString("N"));
        public TempDirectory() => Directory.CreateDirectory(Path);
        public void Dispose() => Directory.Delete(Path, true);
    }
}
