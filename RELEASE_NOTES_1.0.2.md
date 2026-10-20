# SPP Audio Studio 1.0.2 — Windows + macOS

**Windows x64 完整便携包与 macOS Apple Silicon DMG 均已发布。** 重点修复模型下载失败、异常断点续传与下载源回退，并改善 Windows CUDA Runtime 安装时的临时文件存储。

## 安装包

| 平台 | 文件 | 说明 |
| --- | --- | --- |
| Windows 10 / 11 x64 | `SPPAudioStudio-1.0.2-win-x64.zip` | 含启动器、WinUI、Windows Worker 和 Python Core |
| macOS 14+ / Apple Silicon | `SPP-Audio-Studio-1.0.2.dmg` | M 系列芯片 |

两个安装包及各自的 `.sha256` 校验文件均可在本页 Assets 中下载。

## 修复内容

**Windows：** 模型断点文件按下载源隔离，异常大小/校验失败时自动恢复或换源；提供模型下载地址、模型目录、Runtime 目录入口；下载源切换不会意外取消当前下载。CUDA Runtime 安装时的暂存、pip 缓存和临时解包现在跟随 Runtime 目录，便于通过已设置的 Junction 将其放在非系统盘。

**macOS：** Qwen 模型下载支持清理损坏的 `.part` 文件、校验并复用有效文件；ModelScope、Hugging Face 镜像与官方源可回退；新增手动模型下载地址与 Finder 目录入口。

## 安装和升级

- Windows：完整解压 ZIP 至独立文件夹，启动 `SPP Audio Studio.exe`，不要删除 `app`、`worker` 和 `python-core`。
- macOS：打开 DMG，拖动 App 到“应用程序”。若系统拦截自签名 App，可到“系统设置 → 隐私与安全性”确认打开。
- 旧版升级保留用户数据目录中的模型、历史和模板。首次使用 AI 功能仍需在「模型与环境」完成模型、Runtime 安装。
- Windows CUDA 和 macOS MLX 使用不同 Qwen 模型文件，不能直接互换。

## SHA-256

- Windows ZIP：`e7bc134d71c3fbd4d04c5f79bfa966311b4dec505699b2e83fdc955dc56213d0`
- macOS DMG：`dfca3e2355a9f344f42fe0f86cc973a255fd2d9c727a49eb361486b1457524aa`

## 测试和源码

- [Windows 完整构建、Worker 自检与 ZIP 校验：通过](https://github.com/ndsmansyj/SPP-Audio-Studio/actions/runs/39382611834)
- [Windows PR #14](https://github.com/ndsmansyj/SPP-Audio-Studio/pull/14) 与 [macOS PR #13](https://github.com/ndsmansyj/SPP-Audio-Studio/pull/13) 均已合并。
- Windows ZIP 在经过 SHA-256 检查的 1.0.1 支撑组件基础上重建了 1.0.2 WinUI 与 Worker，完整文件清单保存在 ZIP 内。`v1.0.2` 标签最初随 macOS 发布创建，Windows 后续提交可从 PR #14 和主分支核对。

自动化与打包测试通过，不等同于已经在所有用户硬件和网络环境中实测完整模型下载及 CUDA 推理。遇到问题请在 App 中复制诊断报告后提交 GitHub Issue。
