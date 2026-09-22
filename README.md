<div align="center">

<img src="assets/icon/readme_icon.png" width="156" height="156" alt="SPP Audio Studio 图标">

# SPP Audio Studio

**面向创作者的本地 AI 音频工作台**

Windows · macOS

格式转换 · 人声分离 · 声音克隆

</div>

---

SPP Audio Studio 把常用的本地音频处理集中到一个简单的桌面应用里：拖入文件、选择需要的结果，然后导出或试听。AI 模型与音频处理优先在本机运行。

## 下载

前往 [Releases](https://github.com/ndsmansyj/SPP-Audio-Studio/releases) 下载对应平台的最新版本。

| 平台 | 支持情况 | 说明 |
| --- | --- | --- |
| **Windows** | Windows 10 / 11 x64 | 便携 ZIP，AI 功能推荐 NVIDIA GPU |
| **macOS** | Apple Silicon · macOS 14+ | DMG，本地运行 |

> 首次使用 AI 功能时，需要在「模型与环境」页面下载对应模型与运行环境。

## 功能
| 功能 | 可以做什么 |
| --- | --- |
| **格式转换** | 处理受支持的本地音频文件，统一输出为可用格式。 |
| **人声分离** | 使用 Mel-Deux 导出人声、伴奏，或同时导出两者。 |
| **声音克隆** | 使用 Qwen3-TTS 和参考音生成语音，支持常用人声模板。 |

## 特点

- **双端支持**：同一项目同时维护 Windows 与 macOS 版本。
- **本地优先**：素材和 AI 推理尽量在本机完成。
- **源文件只读**：不覆盖原始音频，同名输出自动生成新文件名。
- **模型管理**：模型与运行环境可在应用内安装、检查和切换。
- **创作者工作流**：转换、分离、声音克隆、试听与输出集中在一个应用中。
- **可诊断**：遇到问题可以复制诊断信息，便于反馈与排查。

## Windows

Windows 版为 WinUI 3 独立实现，自带 Python Core，普通用户无需预装 Python。

模型下载会根据当前网络自动测速选源，包括 ModelScope、国内 PyPI 镜像、HF Mirror 与官方源等。

Windows 的开发、构建、Agent API 与技术说明见：

**[Windows README](windows/README.md)**

## macOS
macOS 版面向 Apple Silicon，当前要求 macOS 14+。

普通用户不需要额外安装 Xcode、Command Line Tools、Homebrew 或系统 Python。首次使用 AI 功能时，在「模型与环境」页面完成所需组件安装即可。

macOS 版的 Qwen3-TTS 模型固定从 ModelScope 下载；Mel-Deux、Whisper 与运行环境仍可在应用内选择镜像 / 官方下载源。

如果 macOS 提示“无法验证开发者”，可前往 **系统设置 → 隐私与安全性 → 仍要打开**。

## 反馈与诊断

如果遇到问题：

1. 打开「模型与环境」。
2. 点击 **复制诊断报告**。
3. 将报告粘贴到 GitHub Issue，并简单说明复现步骤。

诊断报告默认隐藏用户名、完整文件路径和声音克隆正文。

## 本机 API

SPP Audio Studio 提供本机接口，可用于连接本地脚本、自动化工作流或 Agent。

macOS 使用说明见 [LOCAL_API.md](LOCAL_API.md)，Windows 说明见 [windows/README.md](windows/README.md)。

## 从源码构建

Windows：

```powershell
.\windows\build.ps1 -Configuration Release -Platform x64
```

macOS：

```bash
./scripts/build_local.sh
```
## 许可证

项目代码采用 [MIT License](LICENSE)。

默认演示人声、像素头像 / 品牌图标和第三方模型分别遵循独立条款；使用或再分发前请阅读：

- [人声素材说明](VOICE_ASSET_LICENSE.md)
- [品牌与肖像素材说明](BRANDING_ASSET_LICENSE.md)
- [第三方声明](THIRD_PARTY_NOTICES.md)

Mel-Deux 模型采用 **CC BY-NC 4.0**，不适用于商业用途。
