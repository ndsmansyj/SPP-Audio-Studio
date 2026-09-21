# SPP Audio Studio 0.2.0-rc4

首个公开群测版本。

## 这版能做什么

- NCM 批量转换为原始 MP3 / FLAC
- Mel-Deux 人声分离：仅伴奏 / 仅人声 / 双轨
- Qwen3-TTS 本地声音克隆
- 常用人声模板
- 输出目录记忆
- Finder 拖拽
- 一键试听 / 暂停
- HF 镜像 / Hugging Face 模型下载源
- 已有模型可直接链接
- 源文件只读，不删除、不覆盖

## 系统

- Apple Silicon Mac
- 当前测试目标 macOS 14+
- Intel Mac 暂不支持

## 第一次打开

本项目没有 Apple Developer ID 签名。

如果 macOS 提示“无法验证开发者”，请到：

系统设置 → 隐私与安全性 → 仍要打开

## 群测最重要的一项

NCM 转换是原生 Swift，应该可以直接使用。

Qwen / Mel 的模型下载已经集成，但完全自包含 Runtime 仍在打磨中。对于从没装过 Qwen / MLX / audio-separator 的干净 Mac，AI 模块可能提示 Runtime 缺失。

如果遇到问题，请截图“模型与环境”页面并提交 Issue，注明：

- Mac 芯片
- macOS 版本
- 是否是干净 Mac
- 错误原文

## License

项目代码：MIT。

注意 Mel-Deux 模型本身是 CC BY-NC 4.0（非商业）；默认“盼盼 · 自然口播”参考音不属于 MIT 授权范围。详情见仓库内 THIRD_PARTY_NOTICES.md 与 VOICE_ASSET_LICENSE.md。
