using Microsoft.VisualStudio.TestTools.UnitTesting;
using SPPAudioStudio.Core;

namespace SPPAudioStudio.Core.Tests;

[TestClass]
public sealed class VoiceCloneRequestTests
{
    [TestMethod]
    public void Empty_script_is_invalid() =>
        Assert.AreEqual("先输入需要生成的文案", new VoiceCloneRequest(null, null, "  ", null).Validate());

    [TestMethod]
    public void Request_needs_template_or_reference_audio() =>
        Assert.AreEqual("先建立一个人声模板，或选择临时参考音", new VoiceCloneRequest(null, null, "测试文案", null).Validate());

    [TestMethod]
    public void Template_and_script_form_a_valid_request() =>
        Assert.IsNull(new VoiceCloneRequest("default", null, "测试文案", null).Validate());
}
