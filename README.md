<div align="center">

<img src="assets/icon/readme_icon.png" width="144" height="144" alt="SPP Audio Studio 图标">

# SPP Audio Studio

**面向创作者的本地 AI 音频工作台**

Windows · macOS ｜ 格式转换 · 人声分离 · 声音克隆

**当前版本：Windows v1.0.3 · macOS v1.0.2**

[下载 Windows v1.0.3](https://github.com/ndsmansyj/SPP-Audio-Studio/releases/tag/v1.0.3) · [项目入口](START-HERE.md) · [更新日志](CHANGELOG.md) · [反馈问题](https://github.com/ndsmansyj/SPP-Audio-Studio/issues)

</div>

---

SPP Audio Studio 将音频格式转换、人声分离、声音克隆和试听集中到一款桌面软件中。输入文件默认只读，生成结果另存；AI 模型与推理主要在本机运行。

## 下载与安装

| 平台 | 版本与安装包 | 系统要求 |
| --- | --- | --- |
| **Windows x64** | [SPPAudioStudio-1.0.3-win-x64.zip](https://github.com/ndsmansyj/SPP-Audio-Studio/releases/download/v1.0.3/SPPAudioStudio-1.0.3-win-x64.zip) | Windows 10 / 11 x64；AI 功能使用受支持的 NVIDIA GPU |
| **macOS** | [SPP-Audio-Studio-1.0.2.dmg](https://github.com/ndsmansyj/SPP-Audio-Studio/releases/download/v1.0.2/SPP-Audio-Studio-1.0.2.dmg) | Apple Silicon；macOS 14 或更高 |

**[Windows Release 与 SHA-256 校验文件](https://github.com/ndsmansyj/SPP-Audio-Studio/releases/tag/v1.0.3)** · [macOS v1.0.2](https://github.com/ndsmansyj/SPP-Audio-Studio/releases/tag/v1.0.2)

- **Windows：** 解压 ZIP 到独立文件夹，运行 `SPP Audio Studio.exe`。请勿只提取 EXE 或删除随附的 `app`、`worker`、`python-core` 文件夹。
- **macOS：** 打开 DMG，将 App 拖入“应用程序”文件夹；如果系统提示无法验证开发者，可在“系统设置 → 隐私与安全性”确认打开。
- **旧版本升级：** Windows 使用新版独立文件夹；macOS 可覆盖旧版应用。存放在用户数据目录的模型、模板和历史记录会保留。

普通用户无需自行安装系统 Python、Homebrew 或 Xcode。AI 功能首次运行时，进入软件的**「模型与环境」**安装所需的模型和运行环境。

## 功能

| 模块 | 功能 |
| --- | --- |
| **格式转换** | 导入支持的音频文件并另存转换结果 |
| **人声分离** | 使用 Mel-Deux 分离人声、伴奏或同时导出 |
| **声音克隆** | 使用 Qwen3-TTS 和参考音生成语音，管理常用人声模板 |
| **试听与历史** | 在应用内预览结果、管理任务和查看处理历史 |
| **模型管理** | 下载、校验或链接已有模型，检查本机运行环境 |

## Windows v1.0.3 更新重点

- 修复进入「模型与环境」后因 WinUI 控件资源缺失导致的闪退。
- 音频工具优先使用应用管理的 FFmpeg，并验证能否执行，避免系统 PATH 中的无效程序造成克隆失败。
- 改进运行环境健康检查和修复，区分已安装与功能就绪；生成、转写和人声分离使用各自的依赖状态。
- 复制诊断或路径失败时显示提示，提供诊断文件保存入口；补齐窗口图标发布资源。
- 从同一份源码完整构建 Windows 便携包，验证最终 ZIP 的文件清单和真实界面操作。

macOS 当前正式版本仍为 v1.0.2，下载与安装方式见上表。

**模型与 Runtime 是两类不同数据。** Windows 的“模型存储位置”只控制模型文件，不会迁移 CUDA / PyTorch Runtime。若 C 盘空间不足，请检查 Runtime 和系统盘的实际剩余空间。macOS 使用 MLX 8-bit Qwen 权重，Windows 使用不同的 CUDA 版本模型，两者不能直接互换。

Windows 构建、最终包完整性与界面回归结果见 [Actions](https://github.com/ndsmansyj/SPP-Audio-Studio/actions)。CUDA 推理要求与运行环境说明见 [Windows 文档](windows/README.md)。

## 故障排查

打开「模型与环境」→「刷新检查／重新检查」，使用**「复制诊断报告」**或保存诊断文件，并将结果发至 [GitHub Issues](https://github.com/ndsmansyj/SPP-Audio-Studio/issues)。运行环境标为需要修复时，使用对应卡片中的修复操作。下载失败时，也可以展开模型卡片中的手动下载入口并按照目录结构保存文件。

## 开发与接口

- [Windows 开发、模型与 Runtime 说明](windows/README.md)
- [macOS 本机 API](LOCAL_API.md)
- [完整更新记录](CHANGELOG.md)

从源码构建：

```powershell
.\windows\build.ps1 -Configuration Release -Platform x64
```

```bash
./scripts/build_local.sh
```

## 许可证与第三方素材

本项目代码采用 [MIT License](LICENSE)。内置默认演示人声、图标与第三方模型遵守各自许可，详情见 [人声素材说明](VOICE_ASSET_LICENSE.md)、[品牌素材说明](BRANDING_ASSET_LICENSE.md) 和 [第三方声明](THIRD_PARTY_NOTICES.md)。

**Mel-Deux 模型采用 CC BY-NC 4.0，不适用于商业用途。**
