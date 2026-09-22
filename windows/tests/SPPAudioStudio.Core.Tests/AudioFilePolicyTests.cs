using Microsoft.VisualStudio.TestTools.UnitTesting;
using SPPAudioStudio.Core;

namespace SPPAudioStudio.Core.Tests;

[TestClass]
public sealed class AudioFilePolicyTests
{
    [DataTestMethod]
    [DataRow("song.ncm", ToolMode.Convert, true)]
    [DataRow("song.mp3", ToolMode.Convert, false)]
    [DataRow("song.ncm", ToolMode.Separate, false)]
    [DataRow("song.flac", ToolMode.Separate, true)]
    [DataRow("song.WAV", ToolMode.ConvertAndSeparate, true)]
    [DataRow("notes.txt", ToolMode.ConvertAndSeparate, false)]
    public void Supports_expected_extensions_for_each_mode(string path, ToolMode mode, bool expected) =>
        Assert.AreEqual(expected, AudioFilePolicy.IsSupported(path, mode));
}
