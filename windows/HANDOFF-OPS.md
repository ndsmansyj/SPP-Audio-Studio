# Windows 版交接与运维手册

> 适用分支：`windows-beta`  
> 仓库：本文所有路径均以仓库根目录为基准。
> Windows 功能实现边界：仅修改 `windows/`；仓库根目录只允许调整发布目录的 `.gitignore` 规则。不得改动 macOS 的 `app/`、原始 `worker/`、`format_converter/`。

## 1. 当前阶段

Windows 版已具备 WinUI 3 五页界面、独立 Windows Worker、模型/Runtime 状态检查、格式转换、人声分离、声音克隆、本机 API、构建与便携发布脚本。

本轮完成的 UI/发布约定：

- 默认窗口：`1180 × 960`；最小窗口：`1040 × 840`。
- 五个页面首次创建后缓存；切换页面不再销毁并重建页面。
- “模型与环境”仅在本次进程第一次打开该页时自动执行 `doctor` 和 `model-status`；再次进入沿用已显示状态。
- 用户需要重新检测时点击“刷新检查”。
- 模型/Runtime 在卡片旁显示 `✓ 已就绪`、`未下载/未安装` 或 `检查中…`。
- 已就绪项隐藏下载、链接或安装按钮；全部就绪时隐藏顶部安装提示。
- 完整诊断 JSON 默认折叠在“查看详情”中。
- 最终可见发布目录约定为仓库根目录下的 `SPP Audio Studio\`，主程序直接位于该目录第一层，不再藏在 `windows\artifacts\publish\win-x64\app\`。

## 2. 关键目录

```text
SPP-Audio-Studio\
├─ windows\
│  ├─ src\SPPAudioStudio.Windows\       WinUI 3 主程序
│  ├─ src\SPPAudioStudio.Core\          核心状态与任务逻辑
│  ├─ src\FormatConverter\              Windows 格式转换器
│  ├─ worker\                            Windows Python Worker
│  ├─ tests\                             C# / Python 测试
│  ├─ build.ps1                          开发构建
│  ├─ publish.ps1                        便携发布
│  └─ launch.ps1                         启动脚本
└─ SPP Audio Studio\                     最终可见便携目录（发布时生成）
   ├─ SPPAudioStudio.Windows.exe
   ├─ worker\
   │  └─ bin\                         格式转换器
   ├─ release-manifest.json
   └─ SHA256SUMS
```

`windows/artifacts/` 仍用于中间构建、PyInstaller 工作目录和 ZIP 包，不作为用户寻找主程序的位置。

## 3. 本机数据与模型现状

当前 Worker 的托管数据仍位于：

```text
%LOCALAPPDATA%\SPP Audio Studio\
├─ Models\
├─ Runtime\
├─ Voices\
├─ Cache\
└─ history.jsonl
```

本机当前已有：

- Qwen3-TTS 1.7B 模型与 Qwen Runtime
- Mel-Deux 模型与 Mel Runtime
- Whisper large-v3-turbo 模型与 ASR Runtime

### 尚未冻结的模型目录决策

用户倾向将模型和 Runtime 与便携软件统一管理。推荐最终结构：

```text
SPP Audio Studio\
├─ SPPAudioStudio.Windows.exe
├─ Models\
├─ Runtime\
├─ Voices\
├─ Cache\
├─ worker\
└─ bin\
```

但当前代码仍使用 `%LOCALAPPDATA%`，本轮没有迁移现有模型，也没有复制大模型。后续实施时应：

1. 默认优先检测软件目录是否可写；
2. 便携模式使用软件根目录下的 `Models/Runtime`；
3. 若位于 `Program Files` 或目录不可写，则回退 `%LOCALAPPDATA%`；
4. 支持环境变量或配置文件覆盖数据根目录；
5. 迁移前先识别已有 `%LOCALAPPDATA%` 数据，提供“使用现有目录 / 移动到软件目录”，禁止静默复制几十 GB；
6. manifest、模型 revision、大小和哈希校验必须继续保留。

## 4. 日常开发命令

以下命令在仓库根目录执行。

### 快速构建 UI（不冻结 Worker、不跑全量测试）

```powershell
.\windows\build.ps1 -Configuration Release -Platform x64 -SkipWorker -SkipTests
```

### 完整开发测试

```powershell
dotnet test .\windows\SPPAudioStudio.Windows.sln -c Release
dotnet test .\windows\tests\FormatConverter.Tests\FormatConverter.Tests.csproj -c Release -p:Platform=x64
python -m unittest discover -s windows\tests -v
```

### 源码 Worker 状态

```powershell
python .\windows\worker\spp_worker.py doctor
python .\windows\worker\spp_worker.py model-status
```

### 发布便携版

```powershell
.\windows\publish.ps1 -Version <版本号> -Platform x64
```

预期输出：

```text
.\SPP Audio Studio\SPPAudioStudio.Windows.exe
.\SPP Audio Studio\worker\...
.\windows\artifacts\packages\SPPAudioStudio-<版本号>-win-x64.zip
```

### 启动发布版

```powershell
.\windows\launch.ps1
```

## 5. 发布验收清单

构建成功不能替代运行验收。发布前必须分别记录：

- [ ] WinUI Release x64 构建 0 error
- [ ] 源码窗口真实启动并保持运行
- [ ] 五页可切换，窗口尺寸统一
- [ ] 环境页首次检查后，离开再进入不重新检测
- [ ] “刷新检查”可主动更新状态
- [ ] 源码 Worker `doctor` / `model-status` 正常
- [ ] 冻结 Worker EXE 可启动
- [ ] 格式转换真实输入通过
- [ ] Mel CUDA 真实分离通过
- [ ] Whisper CUDA 真实转写通过
- [ ] Qwen CUDA 真实声音克隆通过
- [ ] 发布目录第一层可见 `SPPAudioStudio.Windows.exe`
- [ ] ZIP 解压后保留顶层 `SPP Audio Studio/` 文件夹
- [ ] 发布 EXE 在便携目录中真实启动
- [ ] `release-manifest.json`、`SHA256SUMS` 和 ZIP SHA-256 一致
- [ ] 在无开发环境依赖的干净 Windows 11 x64 环境验证

## 6. 已知风险与未完成项

1. **模型存放策略未迁移**：当前仍使用 `%LOCALAPPDATA%`，便携根目录方案只完成评估，尚未实现。
2. **最终发布包未重建**：本轮按要求只完成 UI、缓存和发布路径规则，不能把旧 ZIP 当作新产物。
3. **冻结 Worker 曾有 `timeit` 缺失问题**：发布脚本已加入 `--hidden-import timeit`，仍需在下一次完整发布后重新实测声音克隆。
4. **发布版 WinUI 曾出现退出码 `-1073741189`**：后续完整发布必须再次做冷启动验证，不能只验证开发构建。
5. **Mel-Deux 许可**：`CC BY-NC 4.0`，商业发布前必须处理授权风险。
6. **页面缓存生命周期**：当前缓存维持到应用退出；模型安装完成后由命令流程或“刷新检查”更新，切页本身不会刷新。
7. **首页首次使用提示**：目前仍固定存在，后续可复用全局环境状态，在全部就绪时自动隐藏，避免与环境页状态不一致。

## 7. 修改入口

- UI、页面缓存、环境状态：`windows/src/SPPAudioStudio.Windows/MainWindow.xaml.cs`
- Worker 数据根与模型路径：`windows/worker/spp_worker.py` 顶部 `DATA_DIR`、`MODEL_DIR`、`RUNTIME_DIR`
- 便携发布目录：`windows/publish.ps1`
- 发布版启动目录：`windows/launch.ps1`
- 发布/运维说明：`windows/README.md`、本文档

## 8. 维护原则

- 不将“目录存在”视为模型或 Runtime 已安装；以 manifest、文件完整性和健康检查为准。
- 不把“构建成功”写成“功能可用”；真实 CUDA 推理必须逐项验证。
- UI 不显示样例就绪状态；所有状态来自 Worker。
- 用户正在生成任务时，不移动模型、不重启 Worker、不做抢显存测试。
- 大模型下载优先国内镜像，并保留断点续传、固定 revision、大小/哈希校验。
- 发布路径、启动脚本、README 和 ZIP 内目录必须同步修改，避免出现两套路径。
