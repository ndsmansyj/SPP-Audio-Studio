# SPP Audio Studio 项目入口

## 当前发行

| 平台 | 正式版本 | 下载 |
| --- | --- | --- |
| Windows 10 / 11 x64 | **1.0.4** | [完整便携 ZIP](https://github.com/ndsmansyj/SPP-Audio-Studio/releases/download/v1.0.4/SPPAudioStudio-1.0.4-win-x64.zip) · [Release / SHA-256](https://github.com/ndsmansyj/SPP-Audio-Studio/releases/tag/v1.0.4) |
| macOS 14+ Apple Silicon | **1.0.2** | [DMG](https://github.com/ndsmansyj/SPP-Audio-Studio/releases/download/v1.0.2/SPP-Audio-Studio-1.0.2.dmg) |

Windows 请完整解压到新目录，运行根目录的 `SPP Audio Studio.exe`。用户数据目录中的模型、模板和历史会保留；不要复制旧版 `app/` 或 `worker/` 覆盖新版文件。如果此前选择了便携模型存储，请保留旧目录，并在「模型与环境」中链接原有模型文件夹；旧目录下的 `out/` 输出也请保留。

## 使用与排障

- [安装、功能与故障排查](README.md)
- [Windows 构建、Runtime 与诊断](windows/README.md)
- [版本更新记录](CHANGELOG.md)
- [构建与回归结果](https://github.com/ndsmansyj/SPP-Audio-Studio/actions)
- [反馈问题](https://github.com/ndsmansyj/SPP-Audio-Studio/issues)

## 开发入口

Windows UI 位于 `windows/src/SPPAudioStudio.Windows/`；任务与进程协议位于 `windows/src/SPPAudioStudio.Core/`；运行环境、模型和音频任务位于 `windows/worker/`。

Windows 1.0.4 修复运行环境目录切换被 Windows 拒绝访问时的重试、回滚与暂存复用。发布流程加入真实 ASR 安装和目录占用后的恢复验收；保留 1.0.3 的界面与诊断修复，以及现有 CUDA-only 推理策略。

以当前 `main` 和 Release 对应的提交为源码入口。历史 Windows 1.0.2 标签早于其最终 Windows 构建提交；复核旧版本时同时查阅该 Release 的来源说明。

macOS 开发入口为 `app/` 与 `scripts/build_local.sh`。本次 Windows 修复不改变 macOS 1.0.2 的应用版本。
