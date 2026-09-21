<div align="center">

<img src="assets/icon/readme_icon.png" width="156" height="156" alt="SPP Audio Studio 图标">

# SPP Audio Studio

**Mac 上的本地音频工作台**

格式转换 · 人声分离 · 声音克隆

<sub>Apple Silicon · macOS 14+ · 当前处于 RC 测试阶段</sub>

</div>

---

SPP Audio Studio 把常用的本地音频处理集中到一个简单的桌面应用里：拖入文件，选择需要的结果，然后导出或试听。

| 功能 | 可以做什么 |
| --- | --- |
| **格式转换** | 处理受支持的本地音频文件，统一导出为常用音频格式。 |
| **人声分离** | 使用 Mel-Deux 导出人声、伴奏，或同时导出两者。 |
| **声音克隆** | 使用 Qwen3-TTS 和参考音生成语音，支持常用人声模板。 |

## 特点

- **本地优先**：音频处理默认在本机完成。
- **拖入即用**：尽量减少命令行、环境配置和重复操作。
- **源文件只读**：不会覆盖原始音频；同名输出会自动加序号。
- **环境集成**：AI 模型与运行环境可以在「模型与环境」页面安装和检查。
- **统一工作流**：转换、分离、克隆、试听和输出集中在同一个应用中。
- **可诊断**：遇到问题时可以直接复制诊断报告，便于反馈和排查。

## 下载与首次打开

1. 在 [Releases](https://github.com/ndsmansyj/SPP-Audio-Studio/releases) 下载最新 DMG。
2. 打开 DMG，把 **SPP Audio Studio** 拖到 **Applications**。
3. 当前免费测试版没有 Apple Developer ID。如果 macOS 提示“无法验证开发者”，前往 **系统设置 → 隐私与安全性 → 仍要打开**。

当前版本自带 Python Core，普通用户**不需要额外安装 Xcode、Command Line Tools、Homebrew 或系统 Python**。

首次使用 AI 功能时，在「模型与环境」页面完成所需组件安装即可。

## 反馈与诊断

如果遇到问题：

1. 打开「模型与环境」。
2. 点击 **复制诊断报告**。
3. 将报告粘贴到 GitHub Issue，并简单说明复现步骤。

诊断报告默认隐藏用户名、完整文件路径和声音克隆正文。

## 本机 API

SPP Audio Studio 提供本机 API，可用于连接本地脚本、Agent 或自动化工作流。

使用说明见 [LOCAL_API.md](LOCAL_API.md)。

## 从源码构建

项目由 SwiftUI 界面和 Python Worker 组成。构建脚本面向 Apple Silicon macOS，并需要本机可用的 Swift 编译环境与 Python 3.11 Core。

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
