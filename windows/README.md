# SPP Audio Studio for Windows

Windows 独立实现位于本目录，macOS 代码保持不变。

## 目录

- `src/SPPAudioStudio.Core` — 平台无关的文件策略、任务、校验与 Worker 状态抽象。
- `src/SPPAudioStudio.Windows` — WinUI 3 / Windows App SDK 桌面应用。
- `worker` — Windows 专用 Python Worker、本机 API 与 CUDA 推理桥接。
- `tests/SPPAudioStudio.Core.Tests` — C# Core 测试。
- `tests/test_worker.py` — 不下载模型即可运行的 Worker 测试。

## 当前开发构建

```bash
dotnet test windows/SPPAudioStudio.Windows.sln -c Release
dotnet build windows/src/SPPAudioStudio.Windows/SPPAudioStudio.Windows.csproj -c Release -p:Platform=x64
python -m unittest discover -s windows/tests -v
```

应用最小窗口为 1040×720，固定深色主题。未成功连接 Worker 或模型未通过健康检查时，界面必须如实显示未就绪，不使用样例状态冒充真实运行状态。

## Windows Worker

要求 Windows 11 x64、Python 3.11/3.12、`curl.exe` 与 FFmpeg。可通过 `SPP_PYTHON_CORE` 指定 App 自带 Python，通过 `SPP_FORMAT_BIN` 指定转换器。状态保存在 `%LOCALAPPDATA%\SPP Audio Studio\` 下，包括 `Models`、`Voices`、`Runtime`、`Cache`、设置及历史记录。

CLI 与 macOS Worker 的主要命令保持一致：

```powershell
python windows/worker/spp_worker.py doctor
python windows/worker/spp_worker.py model-status
python windows/worker/spp_worker.py runtime-install qwen|mel|asr
python windows/worker/spp_worker.py model-download qwen|mel|whisper
python windows/worker/spp_worker.py voice-list
```

模型使用 `.part` 文件断点续传，自动模式优先 `hf-mirror.com`，失败后回退 Hugging Face 官方源。测试不会下载模型。真实 CUDA 就绪状态必须由独立运行时导入、模型加载及短任务 smoke test 决定。

本机 API 仅绑定 `127.0.0.1`，需要 Bearer Token：

```powershell
python windows/worker/local_api.py --port 8765 --token <your-secret>
```

`GET /v1/status` 和 `POST /v1/convert`、`/v1/separate`、`/v1/clone` 均要求 `Authorization: Bearer <token>`。
