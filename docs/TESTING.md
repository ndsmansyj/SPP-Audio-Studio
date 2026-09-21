# RC6 干净 Mac 群测清单

目标：确认 SPP Audio Studio 在一台没有安装 Xcode、Homebrew、Python、MLX 或 audio-separator 的 Apple Silicon Mac 上也能正常启动和完成首次配置。

## 1. 首次启动

当前测试版没有 Apple Developer ID 签名。如果双击被 macOS 阻止：

1. 打开“系统设置”
2. 进入“隐私与安全性”
3. 找到被阻止的 SPP Audio Studio
4. 点击“仍要打开”

这是当前免费分发方式下的预期行为。

**RC6 不应该再弹出“python3 命令需要使用命令行开发者工具”或要求安装 Xcode Command Line Tools。** 如果仍然出现，请直接截图并反馈。

## 2. 安装方式

打开 DMG 后，把 **SPP Audio Studio** 拖到 **Applications**。

请从“应用程序”里打开，不要长期直接在 DMG 中运行。

## 3. 模型与 Runtime

打开“模型与环境”：

- App 内置 Python Core 应显示已就绪
- 干净 Mac 上 Qwen / Mel Runtime 初始显示未安装是正常的
- 点击“安装 Qwen Runtime”或“安装 Mel Runtime”
- 再下载对应模型
- 安装过程不应该要求 Xcode、Homebrew 或 Terminal

如果网络不稳导致失败，可以重试；失败后请点 **复制诊断报告**。

## 4. 最小功能测试

### 特殊格式转换

- 拖入 1 个自己有权处理的受支持特殊格式文件
- 选择“源文件旁边”
- 确认生成 MP3 或 FLAC
- 点击试听
- 确认源文件没有被修改、移动或删除

### 人声分离

- 先准备 Mel Runtime + Mel-Deux 模型
- 拖入 MP3 / FLAC / WAV
- 默认选择“仅伴奏（去人声）”
- 确认只输出 Instrumental
- 切换“仅人声”和“人声 + 伴奏”再测一次
- 点击试听

### 声音克隆

- 先准备 Qwen Runtime + Qwen 模型
- 打开默认“盼盼 · 自然口播”
- 保留默认测试文稿
- 点击生成
- 点击试听

## 5. 日志与反馈

遇到问题优先：

1. 打开“模型与环境”
2. 点击 **复制诊断报告**
3. 直接把文字粘贴到群里或 GitHub Issue

诊断报告默认会隐藏用户名、完整文件路径和声音克隆正文。

必要时还可以：

- 复制最近错误
- 打开日志文件夹

日志默认位于：

`~/Library/Logs/SPP Audio Studio/`

## 6. 反馈时请附上

- Mac 芯片：
- macOS 版本：
- 是否干净 Mac：
- SPP Audio Studio 版本：
- 哪一步失败：
- 诊断报告：
- 截图：

请不要在公开 Issue 上传私人参考音、商业项目素材或受版权保护的整首歌曲。
