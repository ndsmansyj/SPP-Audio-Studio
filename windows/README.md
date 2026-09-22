# SPP Audio Studio for Windows

Windows 独立实现位于本目录，macOS 代码保持不变。

## 目录

- `src/SPPAudioStudio.Core` — 平台无关的文件策略、任务、校验与 Worker 状态抽象。
- `src/SPPAudioStudio.Windows` — WinUI 3 / Windows App SDK 桌面应用。
- `worker` — Windows 专用 Python Worker、本机 API 与 CUDA 推理桥接。
- `tests/SPPAudioStudio.Core.Tests` — C# Core 测试。
- `src/FormatConverter` — 无额外运行时依赖的 .NET 8 本地音频格式转换 CLI。
- `tests/test_worker.py` — 不下载模型即可运行的 Worker 测试。

## 当前开发构建

```bash
dotnet test windows/SPPAudioStudio.Windows.sln -c Release
dotnet build windows/src/SPPAudioStudio.Windows/SPPAudioStudio.Windows.csproj -c Release -p:Platform=x64
python -m unittest discover -s windows/tests -v
```

应用默认窗口为 1980×1600，最小窗口为 1040×840，使用浅色主题。未成功连接 Worker 或模型未通过健康检查时，界面必须如实显示未就绪，不使用样例状态冒充真实运行状态。

## Windows Worker

格式转换由独立的 .NET CLI 负责，处理受支持的本地音频容器并保留原始音频编码；不会改写源文件，输出冲突时由应用侧生成新的文件名。

发布版面向 Windows 10/11 x64，自带 Python Core；目标机器不需要预装 Python。开发模式使用 Python 3.11。可通过 `SPP_PYTHON_CORE` 指定 Python，通过 `SPP_FORMAT_BIN` 指定转换器。状态保存在 `%LOCALAPPDATA%\SPP Audio Studio\` 下，包括 `Models`、`Voices`、`Runtime`、`Cache`、设置及历史记录。

CLI 与 macOS Worker 的主要命令保持一致：

```powershell
python windows/worker/spp_worker.py doctor
python windows/worker/spp_worker.py model-status
python windows/worker/spp_worker.py runtime-install qwen|mel|asr
python windows/worker/spp_worker.py model-download qwen|mel|whisper
python windows/worker/spp_worker.py voice-list
```

模型使用 `.part` 文件断点续传并自动测速选源。Qwen 会在 ModelScope、HF Mirror 与 Hugging Face 官方之间选择；Python 依赖会在清华、USTC、阿里云与 PyPI 官方之间选择；PyTorch CUDA 会在 SJTU 与官方源之间选择并检查目标 Windows wheel 是否存在。Qwen、Whisper 与 Mel 均固定 revision；可获得 SHA-256 的大文件会同时校验尺寸和哈希。测试不会下载模型。

AI 推理为 CUDA-only：Qwen 使用 SDPA + BF16，Whisper 使用 CTranslate2 CUDA FP16，Mel 在入口检查 CUDA；缺少 CUDA 时直接失败，禁止静默回退 CPU。跨进程 `gpu.lock` 串行化 Qwen/ASR 与 Mel 任务，避免 RTX 4080 16GB 上并发争抢显存。运行时固定为 `qwen-tts==0.1.1`、`faster-whisper==1.2.1`、`ctranslate2==4.8.2`、`audio-separator==0.47.0`。PyTorch 与 torchaudio 固定为 `2.11.0+cu126`；该组合已通过 PyTorch 官方 cu126 index 的 `pip index versions` 验证可用。

本机 API 仅绑定 `127.0.0.1` 并要求 Bearer Token。发布包可直接运行根目录的 `SPP Agent API.cmd`；默认 Token 与发现信息写在 `%LOCALAPPDATA%\\SPP Audio Studio\\` 下。

核心接口包括 `GET /v1/capabilities`、`GET /v1/status`、`GET /v1/voices`、`POST /v1/tasks` 与 `GET /v1/tasks/{task_id}`。长任务默认串行排队，返回 `task_id` 后可异步查询结果；旧的同步 `/v1/convert`、`/v1/separate`、`/v1/clone` 继续保留兼容。

## 开发与发布脚本

This directory owns the Windows implementation layout and its build tooling. The scripts are location-independent and work from PowerShell or Git Bash. They expect:

- `windows/src/`: one WinUI 3 .NET 8 `.csproj` (or pass `-Project`).
- `windows/worker/`: Python worker with `main.py`, `spp_worker.py`, `local_api.py`, or `__main__.py` (or pass `-WorkerEntryPoint`).
- `windows/worker/requirements.lock`: preferred pinned worker dependencies. `requirements.txt` is accepted with a reproducibility warning.

Intermediate build files stay below `windows/artifacts/` and `windows/.venv/`; the visible portable release is generated in the repository-root `SPP Audio Studio/` directory.

## Prerequisites and bootstrap

Pinned versions are recorded in `toolchain.json` and `global.json`. Visual Studio Build Tools must include the managed desktop build workload and Windows 11 SDK 22621.

PowerShell:

```powershell
# Check only; does not install or create a virtual environment.
.\windows\bootstrap.ps1 -CheckOnly

# Install missing SDKs with winget (elevates through the installer if needed).
.\windows\bootstrap.ps1 -InstallMissing

# Create/update windows/.venv and install packaging + worker dependencies.
.\windows\bootstrap.ps1
```

Git Bash:

```bash
./windows/bootstrap.sh -CheckOnly
./windows/bootstrap.sh -InstallMissing
./windows/bootstrap.sh
```

After `-InstallMissing`, open a new shell before running bootstrap again so Windows refreshes `PATH`.

## Build and test

```powershell
.\windows\build.ps1
.\windows\build.ps1 -Configuration Release -Platform x64
.\windows\build.ps1 -Project C:\path\to\App.csproj -SkipWorker
```

```bash
./windows/build.sh -Configuration Release -Platform x64
```

The build restores and compiles the WinUI project, byte-compiles the worker, runs any `*Tests.csproj`, and runs `windows/worker/tests` with `unittest` when present. Use `-SkipTests`, `-SkipWorker`, or `-NoRestore` only for deliberate incremental work.

## Publish a portable release

```powershell
.\windows\publish.ps1 -Version 1.0.0 -Platform x64
```

```bash
./windows/publish.sh -Version 1.0.0 -Platform x64
```

The release strategy is **unpackaged, self-contained, portable**:

- WinUI app: .NET self-contained publish plus Windows App SDK self-contained files.
- Python worker: PyInstaller `--onedir`; no system Python is required on the target machine.
- Output tree: `SPP Audio Studio\` at the repository root, with `SPPAudioStudio.Windows.exe` directly in that folder and the frozen Worker under `worker\`.
- Archive: `windows/artifacts/packages/SPPAudioStudio-<version>-win-x64.zip`; extracting it preserves the top-level `SPP Audio Studio/` folder.
- Integrity: `SPP Audio Studio/release-manifest.json`, `SPP Audio Studio/SHA256SUMS`, and an archive `.sha256` file.

This is intentionally not MSIX: portable builds avoid certificate/install requirements and are suitable for CI artifacts and developer previews. Add an MSIX signing pipeline separately when an installer identity and signing certificate are available.

Verify an archive checksum:

```powershell
Get-FileHash .\windows\artifacts\packages\SPPAudioStudio-1.0.0-win-x64.zip -Algorithm SHA256
Get-Content .\windows\artifacts\packages\SPPAudioStudio-1.0.0-win-x64.zip.sha256
```

## Launch

Launch the published app:

```powershell
.\windows\launch.ps1
.\windows\launch.ps1 -StartWorker
```

Development mode uses `dotnet run` and the virtual-environment worker:

```powershell
.\windows\launch.ps1 -Development -StartWorker
```

Git Bash equivalents:

```bash
./windows/launch.sh
./windows/launch.sh -Development -StartWorker
```

Pass application or worker arguments with PowerShell arrays, for example:

```powershell
.\windows\launch.ps1 -StartWorker -AppArguments @('--verbose') -WorkerArguments @('--port','8765')
```

## CI

`ci/windows.yml` contains the Windows GitHub Actions workflow but lives under `windows/` to keep this branch scoped. Copy it to `.github/workflows/windows.yml` when integrating the Windows branches:

```powershell
Copy-Item .\windows\ci\windows.yml .\.github\workflows\windows.yml
```

## Script validation

```powershell
python -m unittest windows.tests.test_packaging -v
```

The tests parse every PowerShell script, validate the manifests/wrappers, and check release-integrity generation is present.
