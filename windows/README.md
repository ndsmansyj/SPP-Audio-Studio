# SPP Audio Studio for Windows

当前 Windows 正式版为 **1.0.4**。从[发行页面](https://github.com/ndsmansyj/SPP-Audio-Studio/releases/tag/v1.0.4)下载完整 ZIP，解压到新目录，运行根目录的 `SPP Audio Studio.exe`。目标机器不需要预装 Python 或 .NET。

## 使用、升级与修复

发布版面向 Windows 10 / 11 x64。格式转换可独立使用；声音克隆、人声分离和自动转写使用 NVIDIA CUDA。首次使用 AI 功能时，在「模型与环境」中安装对应模型和运行环境。

升级时使用新目录，保留完整的 `app/`、`worker/`、`python-core/` 和启动器。不要从旧 ZIP 复制单个 EXE 或桥接脚本覆盖新版。用户数据默认保存在 `%LOCALAPPDATA%\SPP Audio Studio\`，包含模型、声音模板、运行环境、缓存、设置和历史，更新应用不会删除这些数据。

「模型与环境」分别展示模型、运行环境和硬件状态。安装记录存在不等于组件健康；运行环境完整也不等于当前机器具备 CUDA 推理能力。运行环境损坏时，使用对应卡片的修复操作。修复会先在暂存目录准备并校验新环境，成功后再替换旧环境。

Windows 短暂占用运行环境目录时，切换和回滚会在 10 秒内重试。持续失败会保留已校验的暂存目录，并明确提示本地切换受阻；不会因此换源重新下载。等相关任务结束、关闭其它 SPP 实例后，再点同一运行环境的修复，程序会重新校验并优先复用暂存文件。若回滚失败，原环境备份位置也会显示在错误中；请保留这些目录并保存诊断。

复制诊断或路径遇到剪贴板占用时会显示提示；也可以保存诊断文件。反馈问题时附上诊断文件、操作步骤和日志。FFmpeg 优先使用应用管理的副本，并检查实际执行结果，避免系统 PATH 中的空文件、无效链接或错误架构程序误报就绪。

## 代码目录

| 路径 | 职责 |
| --- | --- |
| `src/SPPAudioStudio.Windows` | WinUI 3 桌面界面 |
| `src/SPPAudioStudio.Core` | 文件策略、任务与 Worker 进程协议 |
| `src/AppLauncher` | 根目录原生启动器 |
| `src/FormatConverter` | .NET 8 本地音频格式转换 CLI |
| `worker` | Windows Worker、本机 API、模型与 CUDA 桥接 |
| `tests` | Python、C#、完整包与界面回归 |

## Windows Worker

开发模式使用 Python 3.11，发布版自带 Python Core。可用 `SPP_PYTHON_CORE` 指定解释器、`SPP_FORMAT_BIN` 指定格式转换器。下面的命令在仓库根目录执行：

```powershell
python windows/worker/spp_worker.py doctor
python windows/worker/spp_worker.py model-status
python windows/worker/spp_worker.py runtime-install qwen
python windows/worker/spp_worker.py runtime-install qwen --repair
python windows/worker/spp_worker.py model-download qwen
python windows/worker/spp_worker.py voice-list
```

运行环境名称为 `qwen`、`mel`、`asr`，模型名称为 `qwen`、`mel`、`whisper`。运行环境修复不删除模型和声音模板。`doctor` 命令成功只代表诊断执行完成；业务是否就绪应查看对应功能的检查结果。

模型按来源保存 `.part` 断点缓存，避免混用不同来源的响应。Qwen 在 ModelScope、HF Mirror 与 Hugging Face 官方之间测速选源；模型固定 revision，可获得 SHA-256 的大文件会校验尺寸和哈希。正确文件可跳过，损坏或超出预期大小的缓存会重建。

AI 推理保持 CUDA-only：Qwen 使用 SDPA + BF16，Whisper 使用 CTranslate2 CUDA FP16，Mel 在入口检查 CUDA。缺少 CUDA 时给出失败原因，不自动切到 CPU。跨进程 `gpu.lock` 串行化 AI 任务，避免并发争抢显存。

运行环境安装配方以 `worker/runtime_manifest.py` 为准，直接依赖也列在 `requirements-qwen.txt`、`requirements-mel.txt`、`requirements-asr.txt`。安装记录包含配方指纹、Python ABI 和实际安装的依赖元数据；这些记录不是所有传递依赖的离线 wheel 锁文件。旧版运行环境缺少新版验证记录时会提示修复，模型与声音模板继续保留。SPP 使用 Qwen3-TTS **12Hz** 模型，不因可选 25Hz 路径的 SoX 导入告警阻断 12Hz 工作流。

### 存储与磁盘空间

Windows Qwen 使用原始 CUDA 权重，与 macOS MLX 8-bit 权重不同。模型目录和 Python / PyTorch Runtime 分开存储；「模型存储位置」只迁移模型，不迁移 Runtime。

Runtime 安装暂存、pip 缓存和 TMP / TEMP 放在 `%LOCALAPPDATA%\SPP Audio Studio\Runtime` 下。该目录通过 Windows Junction 指向其他磁盘时，安装临时文件也会随之迁移。修复阶段需要同时容纳旧环境和新环境，请预留空间，不要删除正在使用的 Runtime 或 Windows Python 目录。

### 本机 API

发布包根目录提供 `SPP Agent API.cmd`。API 仅绑定 `127.0.0.1`，要求 Bearer Token；Token 和发现信息位于用户数据目录。

接口包括 `GET /v1/capabilities`、`GET /v1/status`、`GET /v1/voices`、`POST /v1/tasks` 与 `GET /v1/tasks/{task_id}`。长任务串行排队，返回 `task_id` 后可查询结果；旧的同步 `/v1/convert`、`/v1/separate`、`/v1/clone` 保留兼容。

## 构建工具链

版本以 `toolchain.json`、`global.json` 和项目文件为准。当前使用 .NET 8 SDK、Python 3.11、Visual Studio 2022 Build Tools、Windows SDK 22621；需安装托管桌面、C++ 和 WinUI 构建所需组件。

```powershell
# 只检查工具链
.\windows\bootstrap.ps1 -CheckOnly

# 安装缺少的工具；完成后重新打开终端
.\windows\bootstrap.ps1 -InstallMissing

# 准备 windows/.venv 与构建依赖
.\windows\bootstrap.ps1

# 编译并运行回归测试
.\windows\build.ps1 -Configuration Release -Platform x64
```

Git Bash 可使用同名 `.sh` 包装脚本。脚本从自身位置定位仓库；中间文件位于 `windows/artifacts/` 和 `windows/.venv/`。

单独运行不下载模型的测试：

```powershell
python -m unittest discover -s windows/tests -p 'test*.py' -v
dotnet test windows/tests/SPPAudioStudio.Core.Tests/SPPAudioStudio.Core.Tests.csproj -c Release
dotnet test windows/tests/FormatConverter.Tests/FormatConverter.Tests.csproj -c Release
```

PowerShell 脚本解析与真实 WinUI 测试应在 Windows 执行。无 GPU 的构建机可以验证诊断、界面、进程协议和包完整性，不能据此宣称已完成 CUDA 模型推理验收。

## 完整便携包发布

```powershell
.\windows\publish.ps1 -Version 1.0.4 -Platform x64
```

发布从当前源码构建全部组件，不拼接旧版 ZIP：

```text
SPP Audio Studio/
  SPP Audio Studio.exe
  SPP Agent API.cmd
  app/
  worker/
  python-core/
  release-manifest.json
  SHA256SUMS
```

WinUI 和转换器包含 .NET 运行时；Worker 使用 PyInstaller `--onedir`；CUDA 桥接使用随包提供的真实 CPython。输出 ZIP 为 `windows/artifacts/packages/SPPAudioStudio-1.0.4-win-x64.zip`，保留顶层文件夹，并附带 `.sha256`。

最终包验证应解压 ZIP，检查全部清单文件、大小、SHA-256、关键组件和源码提交，再从解压后的包启动界面。界面回归覆盖「模型与环境」异步检查完成与页面反复切换；仅看见主窗口不算通过。

```powershell
Get-FileHash .\windows\artifacts\packages\SPPAudioStudio-1.0.4-win-x64.zip -Algorithm SHA256
Get-Content .\windows\artifacts\packages\SPPAudioStudio-1.0.4-win-x64.zip.sha256
```

## 启动与 CI

```powershell
.\windows\launch.ps1
.\windows\launch.ps1 -Development
```

实际生效的 GitHub Actions 位于仓库根目录 `.github/workflows/`。Windows 稳定性流水线执行 Python 与 C# 回归、完整发布和最终包检查。原生 Windows 测试用真实目录句柄阻止重命名，再释放句柄验证重试。最终包中的 Worker 还会安装真实 ASR 依赖、修复旧环境，验证目录持续占用时保留环境，以及释放后不重复安装便可恢复。流水线保存 ZIP、校验和、安装日志与验证报告；正式发行上传已经验证的同一份 ZIP。该流程不下载 Whisper 模型，也不替代 CUDA 模型推理验收。

版本与来源提交记录在包内 `release-manifest.json`。发布标签、Release 说明和下载入口应指向相同的源码版本；历史恢复流程不再自动覆盖已有正式版资产。
