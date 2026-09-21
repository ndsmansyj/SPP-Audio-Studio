# SPP Audio Studio 0.2.0-rc6

RC6 主要解决“干净 Mac”安装与排障体验。

## 重点变化

- App 内置可迁移的 Python 3.11 Core
- 不再调用 macOS `/usr/bin/python3`
- 不需要 Xcode Command Line Tools / Homebrew / 系统 Python
- Qwen / MLX Runtime 可在 App 内安装
- Mel Runtime 可在 App 内安装，并自带所需 FFmpeg
- Runtime 安装只使用预编译 Apple Silicon 包，避免在用户电脑上源码编译
- 新增诊断日志与一键复制诊断报告
- 失败任务可直接复制错误
- DMG 增加 Applications 拖拽入口
- 正式切换为像素人物 + 鹈鹕 + 音频元素 App 图标

## 干净 Mac 测试重点

请优先用一台以前没装过 MLX、audio-separator、Python 开发环境的 Apple Silicon Mac 测试：

1. 首次打开不应再弹“python3 需要命令行开发者工具”
2. “模型与环境”应显示“App 核心已就绪，AI 组件待安装”
3. 在 App 内安装 Qwen / Mel Runtime
4. 下载对应模型
5. 测试人声分离和声音克隆
6. 如果失败，点击“复制诊断报告”直接反馈

## 安装

本项目没有 Apple Developer ID 签名。

如果 macOS 提示无法验证开发者：

系统设置 → 隐私与安全性 → 仍要打开

打开 DMG 后，将 SPP Audio Studio 拖到 Applications。

## 已验证

- bundled Python Core 在受限 PATH 下启动 Worker：PASS
- 干净 HOME 下 doctor / model-status / 默认人声种入：PASS
- 格式转换回归：PASS
- Mel-Deux 仅伴奏回归：PASS
- Qwen 声音克隆回归：PASS
- Qwen Runtime 的预编译依赖解析：PASS
- 内置 Python + managed PYTHONPATH 方式实际生成 Qwen 克隆语音：PASS
- Mel Runtime 的预编译依赖解析：PASS
- 不完整的模型下载不会误显示为“已安装”：PASS
- 日志文件自动创建：PASS
- App ad-hoc codesign：PASS
- DMG checksum：PASS
- DMG Applications 拖拽入口：PASS

## 仍需群测

完整的首次 Runtime 大包下载/安装仍需要更多不同网络和不同 Apple Silicon 机器验证。网络中断时可重试，并请提交诊断报告。

## License

项目代码：MIT。

Mel-Deux 模型：CC BY-NC 4.0（非商业）。
默认“盼盼 · 自然口播”参考音不属于 MIT 授权范围。
