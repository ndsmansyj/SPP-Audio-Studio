#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, quote_plus

# Source scripts, embedded Python Core, and the frozen worker share these helpers.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from media_tools import ffmpeg_environment, install_ffmpeg, probe_ffmpeg, resolve_ffmpeg
from runtime_manifest import (
    ASR_RUNTIME_PACKAGES, MEL_RUNTIME_DEPS, MEL_RUNTIME_PACKAGE,
    PYTORCH_CUDA_PACKAGES, QWEN_RUNTIME_PACKAGES, SCHEMA_VERSION,
    distribution_inventory, manifest_id, runtime_health,
)

# The WinUI host reads Worker stdout/stderr as UTF-8. Force the frozen
# interpreter to use the same encoding instead of the active Windows code page.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

APP_NAME = "SPP Audio Studio"
LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
DATA_DIR = LOCAL_APP_DATA / APP_NAME
CACHE_DIR = LOCAL_APP_DATA / APP_NAME / "Cache"
VOICE_DIR = DATA_DIR / "Voices"
MODEL_DIR = DATA_DIR / "Models"
RUNTIME_DIR = DATA_DIR / "Runtime"
GPU_LOCK_FILE = RUNTIME_DIR / "gpu.lock"
HISTORY_FILE = DATA_DIR / "history.jsonl"
SETTINGS_FILE = DATA_DIR / "settings.json"

IS_FROZEN = bool(getattr(sys, "frozen", False))


def _app_root() -> Path:
    if IS_FROZEN:
        # published: <root>/worker/SPPWorker/SPPWorker.exe
        return Path(sys.executable).resolve().parent.parent.parent
    return Path(os.environ.get("SPP_APP_ROOT", str(DATA_DIR))).expanduser()


APP_ROOT = _app_root()


def _configured_model_dir() -> Path:
    try:
        if SETTINGS_FILE.is_file():
            settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if settings.get("model_storage") == "portable":
                return APP_ROOT / "Models"
    except Exception:
        pass
    return DATA_DIR / "Models"


MODEL_DIR = _configured_model_dir()
OUTPUT_ROOT = APP_ROOT / "out"
CLONE_OUTPUT_DIR = OUTPUT_ROOT / "Clone"
SEPARATION_OUTPUT_DIR = OUTPUT_ROOT / "Music Separation"


def _bundled_format_binary() -> Path:
    # Published layout:
    #   worker/SPPWorker/SPPWorker.exe
    #   worker/bin/format_converter.exe
    if IS_FROZEN:
        worker_root = Path(sys.executable).resolve().parent.parent
        return worker_root / "bin" / "format_converter.exe"
    return Path(__file__).resolve().parent.parent / "bin" / "format_converter.exe"


def _bundled_python_core() -> Path:
    if not IS_FROZEN:
        return Path(os.environ.get("SPP_PYTHON_CORE", sys.executable)).expanduser()
    # Published layout: worker/SPPWorker/SPPWorker.exe and sibling python-core/python.exe.
    return Path(sys.executable).resolve().parent.parent.parent / "python-core" / "python.exe"


PYTHON_CORE = _bundled_python_core()
BUNDLED_FORMAT_BIN = _bundled_format_binary()
FORMAT_BIN = Path(os.environ.get("SPP_FORMAT_BIN", str(BUNDLED_FORMAT_BIN))).expanduser()
MEL_MODEL = os.environ.get("SPP_MEL_MODEL", "becruily_deux.ckpt")
MEL_REGISTRY_NAME = "Roformer Model: Mel-Band RoFormer Deux by becruily"
MEL_CONFIG = "config_deux_becruily.yaml"


def _bundled_worker_file(name: str) -> Path:
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent.parent / name
    return Path(__file__).with_name(name)


BRIDGE = _bundled_worker_file("qwen_bridge.py")
MEL_BRIDGE = _bundled_worker_file("mel_bridge.py")


def _default_voice_dir() -> Path:
    override = os.environ.get("SPP_DEFAULT_VOICE_DIR")
    if override:
        return Path(override).expanduser()
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent.parent / "default_voice"
    return Path(__file__).resolve().parents[2] / "assets" / "default_voice"


DEFAULT_VOICE_DIR = _default_voice_dir()
QWEN_RUNTIME_DIR = RUNTIME_DIR / "qwen"
QWEN_SITE = QWEN_RUNTIME_DIR / "site-packages"
MEL_RUNTIME_DIR = RUNTIME_DIR / "mel"
MEL_SITE = MEL_RUNTIME_DIR / "site-packages"
ASR_RUNTIME_DIR = RUNTIME_DIR / "asr"
ASR_SITE = ASR_RUNTIME_DIR / "site-packages"

PYTORCH_CUDA_OFFICIAL = "https://download.pytorch.org/whl/cu126"
PYTORCH_CUDA_SJTU = "https://mirror.sjtu.edu.cn/pytorch-wheels/cu126"
PYTORCH_CUDA_INDEX = PYTORCH_CUDA_OFFICIAL
PYPI_OFFICIAL = "https://pypi.org/simple"
PYPI_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
PYPI_USTC = "https://mirrors.ustc.edu.cn/pypi/simple"
PYPI_ALIYUN = "https://mirrors.aliyun.com/pypi/simple"

QWEN_REPO = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
QWEN_REVISION = "fd4b254389122332181a7c3db7f27e918eec64e3"
MEL_REPO = "becruily/mel-band-roformer-deux"
MEL_REVISION = "2da74427d682a3df47a774378fc24d7a1a0cdaad"
WHISPER_REPO = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
WHISPER_REVISION = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
HF_OFFICIAL = "https://huggingface.co"
HF_MIRROR = "https://hf-mirror.com"
MODELSCOPE_ENDPOINT = "https://modelscope.cn"
AUTO_PROBE_BYTES = 512 * 1024
AUTO_PROBE_TIMEOUT_SECONDS = 6

QWEN_FILES = {
    "config.json": {"size": 4494, "sha256": None},
    "generation_config.json": {"size": 245, "sha256": None},
    "merges.txt": {"size": 1671839, "sha256": None},
    "model.safetensors": {"size": 3857413744, "sha256": "38fc7fc51c5e776e840414b6fd443962e9411b9654888fd7913e4da643cb857c"},
    "preprocessor_config.json": {"size": 127, "sha256": None},
    "speech_tokenizer/config.json": {"size": 2336, "sha256": None},
    "speech_tokenizer/configuration.json": {"size": 76, "sha256": None},
    "speech_tokenizer/model.safetensors": {"size": 682293092, "sha256": "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"},
    "speech_tokenizer/preprocessor_config.json": {"size": 234, "sha256": None},
    "tokenizer_config.json": {"size": 7344, "sha256": None},
    "vocab.json": {"size": 2776833, "sha256": None},
}
MEL_FILES = {
    "becruily_deux.ckpt": {"size": 435006815, "sha256": "10255c02295bf3e3865d4ee50ff752d7b19b124ed5fd93b147babc4333eda3aa"},
    "config_deux_becruily.yaml": {"size": 1175, "sha256": None},
}
WHISPER_FILES = {
    "config.json": {"size": 2263, "sha256": None},
    "model.bin": {"size": 1617884929, "sha256": "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"},
    "preprocessor_config.json": {"size": 340, "sha256": None},
    "tokenizer.json": {"size": 2710337, "sha256": None},
    "vocabulary.json": {"size": 1068114, "sha256": None},
}


def ensure_dirs() -> None:
    for p in (DATA_DIR, CACHE_DIR, VOICE_DIR, MODEL_DIR, RUNTIME_DIR, CLONE_OUTPUT_DIR, SEPARATION_OUTPUT_DIR):
        p.mkdir(parents=True, exist_ok=True)


def emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return code


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 10000):
        candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成不重名输出：{path}")
def append_history(kind: str, source: str, output: str, status: str = "done") -> None:
    ensure_dirs()
    item = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": kind,
        "source": source,
        "output": output,
        "status": status,
    }
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def run_capture(cmd: list[str], env: dict | None = None, timeout: float | None = None) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged.pop("PYTHONPATH", None)
    merged["PYTHONIOENCODING"] = "utf-8"
    if env:
        merged.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=merged, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _managed_command(kind: str, args: list[str]) -> list[str]:
    """Run managed AI code in a real CPython interpreter."""
    script = {"qwen": BRIDGE, "mel": MEL_BRIDGE}[kind]
    return [str(PYTHON_CORE), str(script), *args]


def _managed_bridge_available(kind: str) -> bool:
    return {"qwen": BRIDGE, "mel": MEL_BRIDGE}[kind].is_file()


def _pip_command(args: list[str]) -> list[str]:
    return [str(PYTHON_CORE), "-m", "pip", *args]


def _verification_script(kind: str) -> str:
    imports = {
        "asr": "import ctranslate2, faster_whisper, imageio_ffmpeg",
        "qwen": "import qwen_tts, torch, torchaudio, soundfile, imageio_ffmpeg",
        "mel": "from audio_separator.separator import Separator\nimport torch, onnxruntime, imageio_ffmpeg",
    }
    # Imports validate the installation. Hardware is a separate result: installing
    # again cannot create a GPU or fix an unsupported NVIDIA driver. No model loads.
    hardware = """
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA 不可用，请检查 NVIDIA 显卡与驱动')
    dtype = torch.float32
    if KIND == 'qwen':
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError('此显卡不支持 Qwen 所需的 CUDA BF16')
        dtype = torch.bfloat16
    tensor = torch.ones((2, 2), device='cuda', dtype=dtype)
    result = tensor @ tensor
    torch.cuda.synchronize()
    del tensor, result
""" if kind != "asr" else """
    if ctranslate2.get_cuda_device_count() < 1:
        raise RuntimeError('CUDA 不可用，请检查 NVIDIA 显卡与驱动')
    if sys.platform == 'win32':
        import ctypes
        directory = os.environ.get('SPP_ASR_CUDA_DLL_DIR', '')
        if not directory:
            raise RuntimeError('Whisper 需要托管 Qwen/Mel 的 CUDA 12 cuBLAS 组件')
        search_handle = os.add_dll_directory(directory)
        dll_handles = [ctypes.WinDLL(os.path.join(directory, name))
                       for name in ('cublasLt64_12.dll', 'cublas64_12.dll')]
"""
    return ("import json, os, sys\n" + imports[kind] + "\n"
            + f"KIND = {kind!r}\n"
            + "hardware = {'ready': False, 'reason': ''}\ntry:\n" + hardware
            + "    hardware['ready'] = True\n"
            + "except Exception as exc:\n    hardware['reason'] = str(exc)\n"
            + "print('SPP_RUNTIME_PROBE=' + json.dumps({'hardware': hardware, "
              "'python_abi': f'{sys.version_info.major}.{sys.version_info.minor}', "
              "'ffmpeg': imageio_ffmpeg.get_ffmpeg_exe()}, ensure_ascii=False))")


def _probe_result(proc: subprocess.CompletedProcess) -> dict:
    prefix = "SPP_RUNTIME_PROBE="
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith(prefix):
            return json.loads(line[len(prefix):])
    raise ValueError("运行环境验证未返回完整结果")


def _verify_command(kind: str, site: Path) -> list[str]:
    return [str(PYTHON_CORE), "-c", _verification_script(kind)]


@contextmanager
def gpu_lock(timeout: float = 3600):
    """Serialize CUDA jobs across worker processes on Windows."""
    ensure_dirs()
    import msvcrt
    handle = GPU_LOCK_FILE.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    deadline = time.monotonic() + timeout
    while True:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            break
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise TimeoutError("等待 CUDA GPU 锁超时")
            time.sleep(0.1)
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def media_tool_status() -> dict:
    return resolve_ffmpeg(
        [QWEN_RUNTIME_DIR, MEL_RUNTIME_DIR, ASR_RUNTIME_DIR, RUNTIME_DIR / "tools"],
        [QWEN_SITE, MEL_SITE, ASR_SITE], os.environ.get("SPP_FFMPEG"),
        canonical_dir=RUNTIME_DIR / "tools" / "ffmpeg",
    )


def locate_ffmpeg() -> Path | None:
    report = media_tool_status()
    return Path(report["path"]) if report["ready"] else None


def _runtime_health(kind: str) -> dict:
    root = {"qwen": QWEN_RUNTIME_DIR, "mel": MEL_RUNTIME_DIR, "asr": ASR_RUNTIME_DIR}[kind]
    health = runtime_health(kind, root)
    if health["healthy"] and kind == "mel" and not _mel_registry_ready(root / "site-packages"):
        health.update(healthy=False, status="repair_required", reason="Mel 模型注册信息缺失，请修复运行环境")
    if health["healthy"] and not PYTHON_CORE.is_file():
        health.update(healthy=False, status="repair_required", reason="App 内置 Python Core 缺失，请修复应用安装")
    return health


def _live_runtime_health(kind: str, health: dict) -> dict:
    if not health["healthy"]:
        return health
    site = {"qwen": QWEN_SITE, "mel": MEL_SITE, "asr": ASR_SITE}[kind]
    result = dict(health)
    try:
        proc = _verify_python_import(kind, site)
        if proc.returncode:
            raise ValueError((proc.stderr or proc.stdout or "组件导入失败")[-1000:])
        result.update(_probe_result(proc), reason="组件导入与安装指纹检查通过")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result.update(healthy=False, status="repair_required", reason=str(exc))
    return result


def cmd_doctor(_: argparse.Namespace) -> int:
    env = resolve_environment()
    health = env["runtime_health"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        health = dict(zip(health, pool.map(lambda item: _live_runtime_health(*item), health.items())))
    ffmpeg = media_tool_status()
    mel_model_dir = Path(env["mel_model_dir"])
    qwen_model = Path(env["qwen_model"])
    asr_model = Path(env["asr_model"])
    checks = {
        "bundled_python_core": PYTHON_CORE.is_file() and os.access(PYTHON_CORE, os.X_OK),
        "format_converter_binary": FORMAT_BIN.is_file() and os.access(FORMAT_BIN, os.X_OK),
        "qwen_runtime": health["qwen"]["healthy"],
        "qwen_model": _model_files_complete(qwen_model, QWEN_FILES),
        "qwen_bridge": _managed_bridge_available("qwen"),
        "mel_runtime": health["mel"]["healthy"],
        "mel_model": _model_files_complete(mel_model_dir, MEL_FILES),
        "mel_config": (mel_model_dir / MEL_CONFIG).is_file(),
        "mel_ffmpeg": ffmpeg["ready"],
        "asr_optional": health["asr"]["healthy"] and _model_files_complete(asr_model, WHISPER_FILES),
    }
    def capability(kind: str, required: list[str]) -> dict:
        missing = [name for name in required if not checks[name]]
        hardware = health[kind].get("hardware", {})
        ready = not missing and hardware.get("ready") is True
        reason = health[kind]["reason"] if not health[kind]["healthy"] else (
            "缺少：" + ", ".join(missing) if missing else hardware.get("reason", "硬件尚未验证"))
        return {"ready": ready, "reason": "可用" if ready else reason,
                "hardware_ready": hardware.get("ready") is True}
    capabilities = {
        "format_conversion": {"ready": checks["format_converter_binary"],
                              "reason": "可用" if checks["format_converter_binary"] else "格式转换器缺失"},
        "voice_clone": capability("qwen", ["bundled_python_core", "qwen_runtime", "qwen_model", "qwen_bridge", "mel_ffmpeg"]),
        "separation": capability("mel", ["bundled_python_core", "mel_runtime", "mel_model", "mel_config", "mel_ffmpeg"]),
        "transcription": capability("asr", ["bundled_python_core", "asr_optional"]),
    }
    return emit({
        "ok": True,
        "core_ready": checks["bundled_python_core"] and checks["format_converter_binary"],
        "ready": all(capabilities[name]["ready"] for name in ("format_conversion", "voice_clone", "separation")),
        "checks": checks, "runtime_health": health, "capabilities": capabilities, "media_tools": {"ffmpeg": ffmpeg},
        "optional_dependencies": {"sox": {"required": False, "reason": "当前 Qwen 12Hz 模型不使用 25Hz SoX 归一化路径"}},
        "paths": {
            "data": str(DATA_DIR), "cache": str(CACHE_DIR), "voices": str(VOICE_DIR),
            "models": str(MODEL_DIR), "runtime": str(RUNTIME_DIR), "python_core": str(PYTHON_CORE),
            "qwen_model": str(qwen_model), "mel_model_dir": str(mel_model_dir),
            "ffmpeg": ffmpeg["path"], "asr_model": str(asr_model),
        },
    })


def cmd_convert(args: argparse.Namespace) -> int:
    ensure_dirs()
    src = Path(args.input).expanduser().resolve()
    if not src.is_file():
        return emit({"ok": False, "error": f"文件不存在：{src}"}, 2)
    if src.suffix.lower() != ".ncm":
        return emit({"ok": False, "error": "convert 当前只接受 .ncm"}, 2)
    if not FORMAT_BIN.is_file():
        return emit({"ok": False, "error": f"格式转换器不存在：{FORMAT_BIN}"}, 3)

    out_root = Path(args.output_dir).expanduser() if args.output_dir else SEPARATION_OUTPUT_DIR
    out_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ncm-", dir=CACHE_DIR))
    try:
        proc = run_capture([str(FORMAT_BIN), str(src), "--out", str(tmp)])
        produced = [p for p in tmp.iterdir() if p.is_file() and p.suffix.lower() in {".mp3", ".flac"}]
        if proc.returncode != 0 or not produced:
            msg = (proc.stderr or proc.stdout or "NCM 转换失败").strip()
            return emit({"ok": False, "error": msg[-1200:]}, 4)
        final = unique_path(out_root / produced[0].name)
        shutil.copy2(produced[0], final)
        append_history("convert", str(src), str(final))
        return emit({"ok": True, "output": str(final), "stdout": proc.stdout.strip()})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
def cmd_separate(args: argparse.Namespace) -> int:
    ensure_dirs()
    src = Path(args.input).expanduser().resolve()
    if not src.is_file():
        return emit({"ok": False, "error": f"文件不存在：{src}"}, 2)
    env = resolve_environment()
    mel_model_dir = Path(env["mel_model_dir"])
    if not env["separator_runtime_installed"]:
        return emit({"ok": False, "error": "Mel Runtime 未安装，请先在“模型与环境”中安装运行环境。"}, 3)
    if env["separator_source"] == "linked":
        return emit({"ok": False, "error": "外部 Separator 无法验证 CUDA-only；请安装托管 Mel CUDA Runtime。"}, 3)

    out_dir = Path(args.output_dir).expanduser() if args.output_dir else SEPARATION_OUTPUT_DIR / src.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="mel-", dir=CACHE_DIR))
    fmt = args.format.upper()
    ext = "." + fmt.lower().replace("mp3", "mp3")

    base_args = [
        "-m", MEL_MODEL,
        "--model_file_dir", str(mel_model_dir),
        "--output_dir", str(tmp), "--output_format", fmt,
    ]
    if fmt == "MP3":
        base_args += ["--output_bitrate", args.bitrate]
    base_args.append(str(src))

    mel_env = {}
    if env["separator_source"] == "linked":
        cmd = [str(env["separator_bin"])] + base_args
    else:
        cmd = _managed_command("mel", base_args)
        mel_env["PYTHONPATH"] = str(env["mel_site"])
        mel_env["PYTHONNOUSERSITE"] = "1"

    try:
        ffmpeg = locate_ffmpeg()
        if not ffmpeg:
            return emit({"ok": False, "error": "没有可执行的 FFmpeg，请修复运行环境。"}, 3)
        mel_env.update(ffmpeg_environment(ffmpeg, {"PATH": os.environ.get("PATH", "")}))
        mel_env["PYTHONPATH"] = str(env["mel_site"])
        mel_env["PYTHONNOUSERSITE"] = "1"
        mel_env["SPP_RUNTIME_ROOT"] = str(RUNTIME_DIR)
        with gpu_lock():
            proc = run_capture(cmd, mel_env)
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "Mel-Deux 分离失败").strip()
            return emit({"ok": False, "error": msg[-2000:]}, 4)
        vocals = next(iter(tmp.glob("*_(Vocals)_becruily_deux.*")), None)
        inst = next(iter(tmp.glob("*_(Instrumental)_becruily_deux.*")), None)
        if not vocals or not inst:
            return emit({"ok": False, "error": "分离完成但没有找到两条输出"}, 5)
        result = {"ok": True}
        if args.keep in ("both", "vocals"):
            v_out = unique_path(out_dir / f"{src.stem} (Vocals){vocals.suffix or ext}")
            shutil.copy2(vocals, v_out)
            result["vocals"] = str(v_out)
        if args.keep in ("both", "instrumental"):
            i_out = unique_path(out_dir / f"{src.stem} (Instrumental){inst.suffix or ext}")
            shutil.copy2(inst, i_out)
            result["instrumental"] = str(i_out)
        append_history("separate", str(src), str(out_dir))
        return emit(result)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def bridge_clone(ref_audio: Path, text: str, ref_text: str, output_dir: Path,
                 temperature: float, top_p: float, top_k: int, repetition_penalty: float) -> dict:
    env_cfg = resolve_environment()
    qwen_model = Path(env_cfg["qwen_model"])
    if not env_cfg["qwen_runtime_installed"]:
        raise RuntimeError("Qwen Runtime 未安装，请先在“模型与环境”中安装运行环境。")
    if not qwen_model.is_dir():
        raise RuntimeError(f"Qwen 模型不存在：{qwen_model}")

    qwen_python = Path(env_cfg["qwen_python"])
    bridge_args = [
        "--ref-audio", str(ref_audio), "--text", text,
        "--output-dir", str(output_dir),
        "--temperature", str(temperature), "--top-p", str(top_p),
        "--top-k", str(top_k), "--repetition-penalty", str(repetition_penalty),
    ]
    if ref_text:
        bridge_args += ["--ref-text", ref_text]

    if env_cfg["qwen_python_source"] == "managed":
        cmd = _managed_command("qwen", bridge_args)
    else:
        cmd = [str(qwen_python), str(BRIDGE), *bridge_args]

    bridge_env = {
        "SPP_QWEN_MODEL": str(qwen_model),
        "SPP_ASR_PY": str(env_cfg["asr_python"]),
        "SPP_ASR_MODEL": str(env_cfg["asr_model"]),
        "SPP_ASR_SITE": str(env_cfg["asr_site"] if env_cfg["asr_python_source"] == "managed" else ""),
        "PYTHONNOUSERSITE": "1",
    }
    if env_cfg["qwen_python_source"] == "managed":
        bridge_env["PYTHONPATH"] = str(env_cfg["qwen_site"])

    ffmpeg = locate_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("没有可执行的 FFmpeg，请修复运行环境。")
    tool_env = ffmpeg_environment(ffmpeg, {"PATH": os.environ.get("PATH", "")})
    tool_env.update(bridge_env)
    bridge_env = tool_env
    bridge_env["SPP_RUNTIME_ROOT"] = str(RUNTIME_DIR)

    with gpu_lock():
        proc = run_capture(cmd, bridge_env)
    marker = "RESULT_JSON="
    result_line = next((x for x in reversed(proc.stdout.splitlines()) if x.startswith(marker)), None)
    if proc.returncode != 0 or not result_line:
        msg = (proc.stderr + "\n" + proc.stdout).strip()
        raise RuntimeError(msg[-2500:] or "Qwen 克隆失败")
    return json.loads(result_line[len(marker):])


def cmd_clone(args: argparse.Namespace) -> int:
    ensure_dirs()
    ref = Path(args.ref_audio).expanduser().resolve()
    if not ref.is_file():
        return emit({"ok": False, "error": f"参考音频不存在：{ref}"}, 2)
    out_dir = Path(args.output_dir).expanduser() if args.output_dir else CLONE_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = bridge_clone(
            ref, args.text, args.ref_text or "", out_dir,
            args.temperature, args.top_p, args.top_k, args.repetition_penalty,
        )
        append_history("clone", str(ref), result.get("path", str(out_dir)))
        return emit({"ok": True, **result})
    except Exception as e:
        return emit({"ok": False, "error": str(e)}, 4)


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", name, flags=re.UNICODE).strip("-_")
    return s[:40] or "voice"


def load_settings() -> dict:
    if SETTINGS_FILE.is_file():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_settings(settings: dict) -> None:
    ensure_dirs()
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _pick_path(configured: str | None, managed: Path) -> tuple[Path, str]:
    if configured:
        p = Path(configured).expanduser()
        if p.exists():
            return p, "linked"
    if managed.exists():
        return managed, "managed"
    return managed, "missing"


def _marker_exists(folder: Path) -> bool:
    return (folder / "installed.json").is_file()


def _linked_runtime_health(name: str) -> dict:
    return {"installed": True, "healthy": False, "status": "repair_required", "source": "linked",
            "reason": f"外部 {name} 环境未经应用验证；修复运行环境后将优先使用托管环境"}


def resolve_environment() -> dict:
    ensure_dirs()
    settings = load_settings()
    paths = settings.get("paths", {})

    qwen_model, qwen_model_source = _pick_path(
        paths.get("qwen_model_dir"),
        MODEL_DIR / "Qwen3-TTS-12Hz-1.7B-Base",
    )
    mel_model_dir, mel_source = _pick_path(
        paths.get("mel_model_dir"),
        MODEL_DIR / "Mel-Deux",
    )

    health = {kind: _runtime_health(kind) for kind in ("qwen", "mel", "asr")}
    qwen_link = Path(paths["qwen_python"]).expanduser() if paths.get("qwen_python") else None
    qwen_managed = health["qwen"]["healthy"]
    if qwen_managed:
        qwen_python = PYTHON_CORE
        qwen_python_source = "managed"
        qwen_runtime_installed = True
    elif qwen_link and qwen_link.is_file():
        qwen_python = qwen_link
        qwen_python_source = "linked"
        qwen_runtime_installed = True
        health["qwen"] = _linked_runtime_health("Qwen")
    else:
        qwen_python = PYTHON_CORE
        qwen_python_source = "missing"
        qwen_runtime_installed = False

    separator_link = Path(paths["separator_bin"]).expanduser() if paths.get("separator_bin") else None
    mel_managed = health["mel"]["healthy"] and _managed_bridge_available("mel")
    if mel_managed:
        separator_bin = MEL_BRIDGE
        separator_source = "managed"
        separator_runtime_installed = True
    elif separator_link and separator_link.is_file():
        separator_bin = separator_link
        separator_source = "linked"
        separator_runtime_installed = True
        health["mel"] = _linked_runtime_health("Mel")
    else:
        separator_bin = MEL_BRIDGE
        separator_source = "missing"
        separator_runtime_installed = False

    asr_link = Path(paths["asr_python"]).expanduser() if paths.get("asr_python") else None
    asr_managed = health["asr"]["healthy"]
    if asr_managed:
        asr_python, asr_python_source, asr_runtime_installed = PYTHON_CORE, "managed", True
    elif asr_link and asr_link.is_file():
        asr_python, asr_python_source, asr_runtime_installed = asr_link, "linked", True
        health["asr"] = _linked_runtime_health("Whisper")
    else:
        asr_python, asr_python_source, asr_runtime_installed = PYTHON_CORE, "missing", False
    asr_model, asr_model_source = _pick_path(
        paths.get("asr_model"),
        MODEL_DIR / "whisper-turbo",
    )

    return {
        "qwen_model": qwen_model,
        "qwen_model_source": qwen_model_source,
        "mel_model_dir": mel_model_dir,
        "mel_model_source": mel_source,
        "qwen_python": qwen_python,
        "qwen_python_source": qwen_python_source,
        "qwen_runtime_installed": qwen_runtime_installed,
        "qwen_site": QWEN_SITE,
        "separator_bin": separator_bin,
        "separator_source": separator_source,
        "separator_runtime_installed": separator_runtime_installed,
        "mel_site": MEL_SITE,
        "asr_python": asr_python,
        "asr_python_source": asr_python_source,
        "asr_runtime_installed": asr_runtime_installed,
        "asr_site": ASR_SITE,
        "asr_model": asr_model,
        "asr_model_source": asr_model_source,
        "download_source": settings.get("download_source", "auto"),
        "runtime_health": health,
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_files_complete(folder: Path, files: dict[str, dict]) -> bool:
    for relpath, metadata in files.items():
        target = folder / relpath
        if not target.is_file() or target.stat().st_size != metadata["size"]:
            return False
        if metadata["sha256"] and _file_sha256(target) != metadata["sha256"]:
            return False
    return True


def cmd_model_status(_: argparse.Namespace) -> int:
    env = resolve_environment()
    qwen_model_path = Path(env["qwen_model"])
    mel_model_path = Path(env["mel_model_dir"])
    payload = {
        "ok": True,
        "download_source": env["download_source"],
        "model_storage": load_settings().get("model_storage", "appdata"),
        "model_root": str(MODEL_DIR),
        "runtime_root": str(RUNTIME_DIR),
        "runtime_health": env["runtime_health"],
        "runtime_free_bytes": shutil.disk_usage(RUNTIME_DIR).free,
        "python_core": {
            "path": str(PYTHON_CORE),
            "installed": PYTHON_CORE.is_file() and os.access(PYTHON_CORE, os.X_OK),
        },
        "models": {
            "whisper": {
                "repo": WHISPER_REPO,
                "revision": WHISPER_REVISION,
                "path": str(env["asr_model"]),
                "source": env["asr_model_source"],
                "installed": _model_files_complete(Path(env["asr_model"]), WHISPER_FILES),
            },
            "qwen": {
                "repo": QWEN_REPO,
                "revision": QWEN_REVISION,
                "path": str(env["qwen_model"]),
                "source": env["qwen_model_source"],
                "installed": _model_files_complete(qwen_model_path, QWEN_FILES),
            },
            "mel_deux": {
                "repo": MEL_REPO,
                "revision": MEL_REVISION,
                "path": str(env["mel_model_dir"]),
                "source": env["mel_model_source"],
                "installed": _model_files_complete(mel_model_path, MEL_FILES),
            },
        },
        "runtime": {
            "asr_python": {
                "path": str(env["asr_python"] if env["asr_python_source"] == "linked" else env["asr_site"]),
                "source": env["asr_python_source"],
                "installed": bool(env["asr_runtime_installed"]),
            },
            "qwen_python": {
                "path": str(env["qwen_python"] if env["qwen_python_source"] == "linked" else env["qwen_site"]),
                "source": env["qwen_python_source"],
                "installed": bool(env["qwen_runtime_installed"]),
            },
            "separator": {
                "path": str(env["separator_bin"] if env["separator_source"] == "linked" else env["mel_site"]),
                "source": env["separator_source"],
                "installed": bool(env["separator_runtime_installed"]),
            },
        },
    }
    return emit(payload)


def cmd_model_link(args: argparse.Namespace) -> int:
    settings = load_settings()
    paths = settings.setdefault("paths", {})
    mapping = {
        "qwen_model_dir": args.qwen_model_dir,
        "mel_model_dir": args.mel_model_dir,
        "qwen_python": args.qwen_python,
        "separator_bin": args.separator_bin,
        "asr_python": args.asr_python,
        "asr_model": args.asr_model,
    }
    for key, value in mapping.items():
        if value:
            p = Path(value).expanduser().absolute()
            if not p.exists():
                return emit({"ok": False, "error": f"路径不存在：{p}"}, 2)
            paths[key] = str(p)
    save_settings(settings)
    return cmd_model_status(args)


def cmd_download_source(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings["download_source"] = args.source
    save_settings(settings)
    return emit({
        "ok": True,
        "download_source": args.source,
        "official": HF_OFFICIAL,
        "mirror": HF_MIRROR,
    })


def cmd_model_storage(args: argparse.Namespace) -> int:
    global MODEL_DIR
    settings = load_settings()
    settings["model_storage"] = args.storage
    save_settings(settings)
    MODEL_DIR = APP_ROOT / "Models" if args.storage == "portable" else DATA_DIR / "Models"
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    return emit({
        "ok": True,
        "model_storage": args.storage,
        "model_root": str(MODEL_DIR),
    })


def _pypi_endpoint_order(source: str) -> list[str]:
    domestic = [PYPI_MIRROR, PYPI_USTC, PYPI_ALIYUN]
    if source == "mirror":
        return domestic + [PYPI_OFFICIAL]
    if source == "official":
        return [PYPI_OFFICIAL] + domestic
    return domestic + [PYPI_OFFICIAL]


def _probe_pypi_endpoint(curl: str, endpoint: str) -> dict:
    url = endpoint.rstrip("/") + "/pip/"
    cmd = [
        curl, "-L", "--fail", "--silent", "--show-error",
        "--connect-timeout", "3",
        "--max-time", str(AUTO_PROBE_TIMEOUT_SECONDS),
        "--range", "0-131071",
        "-o", os.devnull,
        "-w", "%{size_download}|%{time_total}|%{http_code}",
        url,
    ]
    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    elapsed = max(time.monotonic() - started, 0.001)
    downloaded = 0
    seconds = elapsed
    http_code = 0
    try:
        size_text, time_text, code_text = (proc.stdout or "").strip().split("|")[-3:]
        downloaded = int(float(size_text))
        seconds = max(float(time_text), 0.001)
        http_code = int(code_text)
    except (TypeError, ValueError):
        pass
    ok = proc.returncode == 0 and downloaded > 0 and 200 <= http_code < 400
    return {
        "endpoint": endpoint,
        "ok": ok,
        "bytes": downloaded,
        "seconds": seconds,
        "bps": (downloaded / seconds) if ok else 0.0,
        "http_code": http_code,
    }


def _resolve_pypi_endpoint_order(source: str, runtime: str) -> list[str]:
    if source != "auto":
        return _pypi_endpoint_order(source)

    curl = shutil.which("curl")
    if not curl:
        return _pypi_endpoint_order("mirror")

    domestic = [PYPI_MIRROR, PYPI_USTC, PYPI_ALIYUN]
    endpoints = domestic + [PYPI_OFFICIAL]
    print(json.dumps({
        "event": "runtime_source_probe_start",
        "runtime": runtime,
        "endpoints": endpoints,
    }, ensure_ascii=False), flush=True)

    with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
        results = list(pool.map(lambda endpoint: _probe_pypi_endpoint(curl, endpoint), endpoints))

    for result in results:
        print(json.dumps({
            "event": "runtime_source_probe_result",
            "runtime": runtime,
            **result,
        }, ensure_ascii=False), flush=True)

    by_endpoint = {result["endpoint"]: result for result in results}
    domestic_ok = [by_endpoint[e] for e in domestic if by_endpoint.get(e, {}).get("ok")]
    official = by_endpoint.get(PYPI_OFFICIAL, {"bps": 0.0, "ok": False})
    best_domestic = max(domestic_ok, key=lambda item: item["bps"]) if domestic_ok else None

    if best_domestic and (not official["ok"] or best_domestic["bps"] >= official["bps"] * 0.85):
        primary = best_domestic["endpoint"]
    elif official["ok"]:
        primary = PYPI_OFFICIAL
    else:
        primary = PYPI_MIRROR

    remaining = [endpoint for endpoint in endpoints if endpoint != primary]
    remaining.sort(key=lambda endpoint: by_endpoint.get(endpoint, {}).get("bps", 0.0), reverse=True)
    order = [primary] + remaining

    selected = by_endpoint.get(primary, {})
    print(json.dumps({
        "event": "runtime_source_selected",
        "runtime": runtime,
        "endpoint": primary,
        "bps": selected.get("bps", 0.0),
        "fallback": order[1] if len(order) > 1 else "",
    }, ensure_ascii=False), flush=True)
    return order


def _probe_pytorch_endpoint(curl: str, endpoint: str) -> dict:
    url = endpoint.rstrip("/") + "/torch/"
    started = time.monotonic()
    proc = subprocess.run(
        [curl, "-L", "--fail", "--silent", "--show-error",
         "--connect-timeout", "3", "--max-time", str(AUTO_PROBE_TIMEOUT_SECONDS), url],
        capture_output=True, text=True, check=False,
    )
    elapsed = max(time.monotonic() - started, 0.001)
    body = proc.stdout or ""
    required = "torch-2.11.0+cu126-cp311-cp311-win_amd64.whl"
    required_encoded = "torch-2.11.0%2Bcu126-cp311-cp311-win_amd64.whl"
    compatible = required in body or required_encoded in body
    downloaded = len(body.encode("utf-8"))
    ok = proc.returncode == 0 and compatible and downloaded > 0
    return {
        "endpoint": endpoint,
        "ok": ok,
        "bytes": downloaded,
        "seconds": elapsed,
        "bps": (downloaded / elapsed) if ok else 0.0,
        "compatible": compatible,
    }


def _resolve_pytorch_endpoint_order(source: str, runtime: str) -> list[str]:
    if source == "official":
        return [PYTORCH_CUDA_OFFICIAL, PYTORCH_CUDA_SJTU]
    if source == "mirror":
        return [PYTORCH_CUDA_SJTU, PYTORCH_CUDA_OFFICIAL]

    curl = shutil.which("curl")
    if not curl:
        return [PYTORCH_CUDA_SJTU, PYTORCH_CUDA_OFFICIAL]

    endpoints = [PYTORCH_CUDA_SJTU, PYTORCH_CUDA_OFFICIAL]
    print(json.dumps({
        "event": "torch_source_probe_start",
        "runtime": runtime,
        "endpoints": endpoints,
    }, ensure_ascii=False), flush=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda endpoint: _probe_pytorch_endpoint(curl, endpoint), endpoints))

    for result in results:
        print(json.dumps({
            "event": "torch_source_probe_result",
            "runtime": runtime,
            **result,
        }, ensure_ascii=False), flush=True)

    by_endpoint = {result["endpoint"]: result for result in results}
    sjtu = by_endpoint.get(PYTORCH_CUDA_SJTU, {"ok": False, "bps": 0.0})
    official = by_endpoint.get(PYTORCH_CUDA_OFFICIAL, {"ok": False, "bps": 0.0})
    if sjtu["ok"] and (not official["ok"] or sjtu["bps"] >= official["bps"] * 0.85):
        order = [PYTORCH_CUDA_SJTU, PYTORCH_CUDA_OFFICIAL]
    elif official["ok"]:
        order = [PYTORCH_CUDA_OFFICIAL, PYTORCH_CUDA_SJTU]
    else:
        order = [PYTORCH_CUDA_SJTU, PYTORCH_CUDA_OFFICIAL]

    selected = by_endpoint.get(order[0], {})
    print(json.dumps({
        "event": "torch_source_selected",
        "runtime": runtime,
        "endpoint": order[0],
        "bps": selected.get("bps", 0.0),
        "fallback": order[1],
    }, ensure_ascii=False), flush=True)
    return order


def _verify_python_import(kind: str, site: Path) -> subprocess.CompletedProcess:
    env = {"PYTHONPATH": str(site), "PYTHONNOUSERSITE": "1"}
    if kind == "asr":
        for root in (QWEN_RUNTIME_DIR, MEL_RUNTIME_DIR):
            dll_dir = root / "site-packages" / "torch" / "lib"
            if all((dll_dir / name).is_file() for name in ("cublas64_12.dll", "cublasLt64_12.dll")):
                env["SPP_ASR_CUDA_DLL_DIR"] = str(dll_dir)
                env["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
                break
    return run_capture(_verify_command(kind, site), env, timeout=60)


def _mel_registry_ready(site: Path) -> bool:
    try:
        data = json.loads((site / "audio_separator/models.json").read_text(encoding="utf-8"))
        return data.get("roformer_download_list", {}).get(MEL_REGISTRY_NAME, {}).get(MEL_MODEL) == MEL_CONFIG
    except (OSError, ValueError):
        return False


def _install_mel_registry(site: Path) -> None:
    path = site / "audio_separator/models.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("roformer_download_list", {})[MEL_REGISTRY_NAME] = {MEL_MODEL: MEL_CONFIG}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")


def _runtime_installed(kind: str) -> bool:
    if not _runtime_health(kind)["healthy"]:
        return False
    root = {"qwen": QWEN_RUNTIME_DIR, "mel": MEL_RUNTIME_DIR, "asr": ASR_RUNTIME_DIR}[kind]
    return probe_ffmpeg(root / "ffmpeg" / "ffmpeg.exe")["ready"]


def _promote_runtime(staged: Path, final: Path) -> None:
    """Same-volume directory promotion with rollback if the replacement fails."""
    backup = final.with_name(f".{final.name}-runtime-previous-{time.time_ns()}")
    had_previous = final.exists()
    if had_previous:
        os.replace(final, backup)
    try:
        os.replace(staged, final)
    except OSError:
        if had_previous:
            # Do not delete either directory if rollback itself fails. The error
            # includes the retained backup path so the installation is recoverable.
            try:
                os.replace(backup, final)
            except OSError as rollback_error:
                raise RuntimeError(f"替换失败，原运行环境保留在 {backup}；恢复失败：{rollback_error}") from rollback_error
        raise
    if had_previous:
        shutil.rmtree(backup, ignore_errors=True)


def _runtime_install_storage() -> tuple[Path, Path]:
    # Putting staging AND pip cache below RUNTIME_DIR matters when it is a
    # Windows junction to another drive. Otherwise %LOCALAPPDATA%/Cache,
    # pip wheels and temp unpacking can still exhaust C: despite the junction.
    staging = RUNTIME_DIR / ".spp-install-staging"
    pip_cache = RUNTIME_DIR / ".spp-pip-cache"
    staging.mkdir(parents=True, exist_ok=True)
    pip_cache.mkdir(parents=True, exist_ok=True)
    return staging, pip_cache


def cmd_runtime_install(args: argparse.Namespace) -> int:
    ensure_dirs()
    kind = args.runtime
    source = args.source or load_settings().get("download_source", "auto")

    if not PYTHON_CORE.is_file() or not os.access(PYTHON_CORE, os.X_OK):
        return emit({"ok": False, "error": "App 内置 Python Core 不可用。"}, 3)

    if not getattr(args, "repair", False) and _runtime_installed(kind):
        health = _live_runtime_health(kind, _runtime_health(kind))
        if health["healthy"]:
            return emit({"ok": True, "runtime": kind, "installed": True,
                         "already_installed": True, "runtime_health": health})

    if kind == "qwen":
        packages = QWEN_RUNTIME_PACKAGES
        install_steps = [(QWEN_RUNTIME_PACKAGES, False)]
    elif kind == "asr":
        packages = ASR_RUNTIME_PACKAGES
        install_steps = [(ASR_RUNTIME_PACKAGES, False)]
    else:
        packages = [MEL_RUNTIME_PACKAGE] + MEL_RUNTIME_DEPS
        # audio-separator declares diffq for its optional Demucs path. Mel-Deux is MDXC,
        # so install the wheel itself without dependencies, then only the all-wheel MDXC deps.
        install_steps = [([MEL_RUNTIME_PACKAGE], True), (MEL_RUNTIME_DEPS, False)]

    final_dir = {"qwen": QWEN_RUNTIME_DIR, "mel": MEL_RUNTIME_DIR, "asr": ASR_RUNTIME_DIR}[kind]
    errors: list[str] = []
    staging_root, pip_cache = _runtime_install_storage()
    available = shutil.disk_usage(staging_root).free
    if kind in ("qwen", "mel") and available < 10 * 1024**3:
        print(json.dumps({
            "event": "runtime_space_warning", "runtime": kind,
            "free_bytes": available, "staging_root": str(staging_root),
            "message": "运行环境与 CUDA 包可能需要约 10GB 以上可用空间。"
                       "当前空间偏少，请检查 Runtime 及临时缓存所在盘。",
        }, ensure_ascii=False), flush=True)
    endpoint_order = _resolve_pypi_endpoint_order(source, kind)
    torch_order = _resolve_pytorch_endpoint_order(source, kind) if kind in ("qwen", "mel") else [None]
    attempts = [(endpoint, torch_endpoint) for endpoint in endpoint_order for torch_endpoint in torch_order]

    for endpoint, torch_endpoint in attempts:
        temp_dir = Path(tempfile.mkdtemp(prefix=f"runtime-{kind}-", dir=staging_root))
        temp_work = temp_dir / "temp"
        temp_work.mkdir(parents=True, exist_ok=True)
        site = temp_dir / "site-packages"
        site.mkdir(parents=True, exist_ok=True)

        print(json.dumps({
            "event": "runtime_install_start",
            "runtime": kind,
            "index": endpoint,
            "torch_index": torch_endpoint,
            "packages": packages,
        }, ensure_ascii=False), flush=True)

        install_failed = False
        for step_packages, no_deps in install_steps:
            cmd = _pip_command([
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--only-binary=:all:",
                "--target", str(site),
                "--index-url", endpoint,
            ])
            if kind in ("qwen", "mel") and torch_endpoint:
                cmd += ["--extra-index-url", torch_endpoint]
            if no_deps:
                cmd.append("--no-deps")
            cmd += step_packages

            proc = run_capture(cmd, {
                "PIP_CACHE_DIR": str(pip_cache),
                "TMP": str(temp_work),
                "TEMP": str(temp_work),
                "TMPDIR": str(temp_work),
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PYTHONNOUSERSITE": "1",
            })
            if proc.returncode != 0:
                source_label = endpoint if not torch_endpoint else f"{endpoint} + {torch_endpoint}"
                errors.append(f"{source_label}: pip exit {proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}")
                install_failed = True
                break

        if install_failed:
            shutil.rmtree(temp_dir, ignore_errors=True)
            continue

        try:
            if kind == "mel":
                _install_mel_registry(site)
            verify = _verify_python_import(kind, site)
            if verify.returncode != 0:
                raise RuntimeError("组件导入验证失败：" + (verify.stderr or verify.stdout)[-1000:])
            verification = _probe_result(verify)
            if verification.get("python_abi") != f"{sys.version_info.major}.{sys.version_info.minor}":
                raise RuntimeError("内置 Python Core 与 Worker 的 Python ABI 不一致，请修复应用安装")
            # Every runtime owns a canonical ffmpeg.exe. pydub/audio-separator
            # invoke this bare name; imageio's versioned filename alone is insufficient.
            ffmpeg_errors = []
            for candidate in sorted((site / "imageio_ffmpeg" / "binaries").glob("ffmpeg*.exe")):
                try:
                    media = install_ffmpeg(candidate, temp_dir / "ffmpeg" / "ffmpeg.exe")
                    break
                except (OSError, RuntimeError) as exc:
                    ffmpeg_errors.append(str(exc))
            else:
                raise RuntimeError("托管 FFmpeg 验证失败：" + (" | ".join(ffmpeg_errors) or "安装包未提供 FFmpeg"))
            inventory = distribution_inventory(site)
            if not inventory:
                raise RuntimeError("安装缺少 Python 依赖元数据，不能标记为健康")
            shutil.rmtree(temp_work, ignore_errors=True)
            marker = {
                "schema_version": SCHEMA_VERSION, "manifest_id": manifest_id(kind),
                "runtime": kind, "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "python": str(PYTHON_CORE), "python_abi": verification["python_abi"],
                "packages": packages, "distributions": inventory,
                "source": endpoint, "torch_source": torch_endpoint,
                "hardware": verification["hardware"], "ffmpeg_version": media["version"],
            }
            (temp_dir / "installed.json").write_text(
                json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
            staged_health = runtime_health(kind, temp_dir)
            if not staged_health["healthy"]:
                raise RuntimeError(staged_health["reason"])
            # Existing jobs keep their runtime until finished. Staging and import
            # validation never modify the active installation.
            with gpu_lock(timeout=60):
                _promote_runtime(temp_dir, final_dir)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{endpoint}: {exc}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            continue

        print(json.dumps({
            "event": "runtime_install_done",
            "runtime": kind,
            "path": str(final_dir),
            "index": endpoint,
            "torch_index": torch_endpoint,
        }, ensure_ascii=False), flush=True)
        return emit({"ok": True, "runtime": kind, "installed": True, "path": str(final_dir),
                     "repaired": bool(getattr(args, "repair", False)),
                     "runtime_health": runtime_health(kind, final_dir), "hardware": verification["hardware"]})

    return emit({
        "ok": False,
        "error": "运行环境安装失败：" + " | ".join(errors[-2:]),
    }, 4)


def _endpoint_order(source: str) -> list[str]:
    if source == "mirror":
        return [HF_MIRROR, HF_OFFICIAL]
    if source == "official":
        return [HF_OFFICIAL, HF_MIRROR]
    return [HF_MIRROR, HF_OFFICIAL]


def _model_download_url(endpoint: str, repo: str, revision: str, relpath: str) -> str:
    if endpoint == MODELSCOPE_ENDPOINT:
        return (
            f"{endpoint}/api/v1/models/{repo}/repo"
            f"?Revision=master&FilePath={quote_plus(relpath)}"
        )
    return f"{endpoint}/{repo}/resolve/{revision}/{quote(relpath, safe='/')}"


def _probe_hf_endpoint(curl: str, endpoint: str, repo: str, revision: str, relpath: str) -> dict:
    url = _model_download_url(endpoint, repo, revision, relpath)
    cmd = [
        curl, "-L", "--fail", "--silent", "--show-error",
        "--connect-timeout", "3",
        "--max-time", str(AUTO_PROBE_TIMEOUT_SECONDS),
        "--range", f"0-{AUTO_PROBE_BYTES - 1}",
        "-o", os.devnull,
        "-w", "%{size_download}|%{time_total}|%{http_code}",
        url,
    ]
    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    elapsed = max(time.monotonic() - started, 0.001)
    downloaded = 0
    seconds = elapsed
    http_code = 0
    try:
        size_text, time_text, code_text = (proc.stdout or "").strip().split("|")[-3:]
        downloaded = int(float(size_text))
        seconds = max(float(time_text), 0.001)
        http_code = int(code_text)
    except (TypeError, ValueError):
        pass
    ok = proc.returncode == 0 and downloaded > 0 and 200 <= http_code < 400
    return {
        "endpoint": endpoint,
        "ok": ok,
        "bytes": downloaded,
        "seconds": seconds,
        "bps": (downloaded / seconds) if ok else 0.0,
        "http_code": http_code,
    }


def _resolve_model_endpoint_order(source: str, repo: str, revision: str, files: dict, model: str) -> list[str]:
    if source != "auto":
        # Keep ModelScope available as a SHA256-gated Qwen fallback.
        order = _endpoint_order(source)
        return order + [MODELSCOPE_ENDPOINT] if repo == QWEN_REPO else order

    curl = shutil.which("curl")
    if not curl:
        return [MODELSCOPE_ENDPOINT, HF_MIRROR, HF_OFFICIAL] if repo == QWEN_REPO else [HF_MIRROR, HF_OFFICIAL]

    probe_relpath = max(files.items(), key=lambda item: item[1].get("size", 0))[0]
    domestic = [HF_MIRROR]
    if repo == QWEN_REPO:
        domestic.insert(0, MODELSCOPE_ENDPOINT)
    endpoints = domestic + [HF_OFFICIAL]

    print(json.dumps({
        "event": "source_probe_start",
        "model": model,
        "file": probe_relpath,
        "endpoints": endpoints,
    }, ensure_ascii=False), flush=True)

    with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
        results = list(pool.map(
            lambda endpoint: _probe_hf_endpoint(curl, endpoint, repo, revision, probe_relpath),
            endpoints,
        ))

    for result in results:
        print(json.dumps({
            "event": "source_probe_result",
            "model": model,
            **result,
        }, ensure_ascii=False), flush=True)

    by_endpoint = {result["endpoint"]: result for result in results}
    domestic_ok = [by_endpoint[e] for e in domestic if by_endpoint.get(e, {}).get("ok")]
    official = by_endpoint.get(HF_OFFICIAL, {"bps": 0.0, "ok": False})
    best_domestic = max(domestic_ok, key=lambda item: item["bps"]) if domestic_ok else None

    if best_domestic and (not official["ok"] or best_domestic["bps"] >= official["bps"] * 0.85):
        primary = best_domestic["endpoint"]
    elif official["ok"]:
        primary = HF_OFFICIAL
    else:
        primary = domestic[0]

    remaining = [endpoint for endpoint in endpoints if endpoint != primary]
    remaining.sort(key=lambda endpoint: by_endpoint.get(endpoint, {}).get("bps", 0.0), reverse=True)
    order = [primary] + remaining

    selected = by_endpoint.get(primary, {})
    print(json.dumps({
        "event": "source_selected",
        "model": model,
        "endpoint": primary,
        "bps": selected.get("bps", 0.0),
        "fallback": order[1] if len(order) > 1 else "",
    }, ensure_ascii=False), flush=True)
    return order


def _download_one(
    repo: str,
    revision: str,
    relpath: str,
    metadata: dict,
    target: Path,
    source: str,
    *,
    model: str,
    completed_before: int,
    model_total: int,
    endpoint_order: list[str] | None = None,
) -> int:
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("未找到 curl.exe；请安装 curl 并加入 PATH")
    target.parent.mkdir(parents=True, exist_ok=True)
    expected_size = metadata["size"]
    expected_sha256 = metadata["sha256"]

    def emit_progress(current_file_bytes: int, endpoint: str | None = None) -> None:
        completed = min(model_total, completed_before + current_file_bytes)
        percent = round((completed / model_total) * 100, 1) if model_total else 100.0
        print(json.dumps({
            "event": "download_progress",
            "model": model,
            "file": relpath,
            "file_bytes": current_file_bytes,
            "file_total_bytes": expected_size,
            "completed_bytes": completed,
            "total_bytes": model_total,
            "percent": percent,
            "endpoint": endpoint,
        }, ensure_ascii=False), flush=True)

    if (target.is_file() and target.stat().st_size == expected_size
            and (expected_sha256 is None or _file_sha256(target) == expected_sha256)):
        print(json.dumps({
            "event": "skip", "model": model, "file": relpath, "bytes": expected_size
        }, ensure_ascii=False), flush=True)
        emit_progress(expected_size)
        return expected_size

    # Older builds shared one .part across hosts; never resume it on
    # another host. A complete verified legacy .part can be adopted safely.
    legacy_part = target.with_suffix(target.suffix + ".part")
    if legacy_part.is_file() and legacy_part.stat().st_size == expected_size:
        if expected_sha256 is None or _file_sha256(legacy_part) == expected_sha256:
            legacy_part.replace(target)
            emit_progress(expected_size)
            return expected_size

    errors = []
    for endpoint in (endpoint_order or _endpoint_order(source)):
        endpoint_tag = re.sub(r"[^A-Za-z0-9]+", "-", endpoint).strip("-")[-48:] or "source"
        part = target.with_suffix(target.suffix + f".part.{endpoint_tag}")
        url = _model_download_url(endpoint, repo, revision, relpath)

        if part.is_file() and part.stat().st_size == expected_size:
            if expected_sha256 is None or _file_sha256(part) == expected_sha256:
                part.replace(target)
                emit_progress(expected_size, endpoint)
                return expected_size

        if part.is_file() and part.stat().st_size >= expected_size:
            print(json.dumps({
                "event": "download_reset", "model": model, "file": relpath,
                "endpoint": endpoint, "reason": "invalid_partial",
                "actual_bytes": part.stat().st_size, "expected_bytes": expected_size,
            }, ensure_ascii=False), flush=True)
            part.unlink()

        for attempt in range(2):
            resumed = part.is_file() and part.stat().st_size > 0
            print(json.dumps({
                "event": "download_start", "model": model, "file": relpath,
                "endpoint": endpoint, "expected_bytes": expected_size,
                "total_bytes": model_total, "resumed": resumed, "attempt": attempt + 1,
            }, ensure_ascii=False), flush=True)
            cmd = [
                curl, "-L", "--fail", "--silent", "--show-error", "--retry", "3",
                "--retry-delay", "2", "--connect-timeout", "15",
            ]
            if resumed:
                cmd += ["-C", "-"]
            cmd += ["-o", str(part), url]
            proc = subprocess.Popen(cmd)
            last_size = -1
            while proc.poll() is None:
                current_size = part.stat().st_size if part.exists() else 0
                if current_size != last_size:
                    emit_progress(min(current_size, expected_size), endpoint)
                    last_size = current_size
                time.sleep(0.25)
            current_size = part.stat().st_size if part.exists() else 0
            emit_progress(min(current_size, expected_size), endpoint)
            valid_size = proc.returncode == 0 and current_size == expected_size
            actual_hash = _file_sha256(part) if valid_size and expected_sha256 else None
            if valid_size and (expected_sha256 is None or actual_hash == expected_sha256):
                part.replace(target)
                print(json.dumps({
                    "event": "download_done", "model": model, "file": relpath,
                    "bytes": expected_size, "endpoint": endpoint,
                }, ensure_ascii=False), flush=True)
                emit_progress(expected_size, endpoint)
                return expected_size

            error = (f"SHA-256 校验失败（{actual_hash}）"
                     if valid_size else f"curl={proc.returncode}, size={current_size}, expected={expected_size}")
            print(json.dumps({
                "event": "download_failure", "model": model, "file": relpath,
                "endpoint": endpoint, "reason": error,
            }, ensure_ascii=False), flush=True)
            if resumed and attempt == 0:
                part.unlink(missing_ok=True)
                continue
            errors.append(f"{endpoint}: {error}")
            # Keep smaller interrupted parts for their own source only.
            if part.exists() and (proc.returncode == 0 or current_size >= expected_size):
                part.unlink()
            break
    raise RuntimeError(f"下载失败 {relpath}: " + " | ".join(errors))


def cmd_model_download(args: argparse.Namespace) -> int:
    ensure_dirs()
    settings = load_settings()
    source = args.source or settings.get("download_source", "auto")
    if args.model == "qwen":
        repo, revision, files = QWEN_REPO, QWEN_REVISION, QWEN_FILES
        target_dir = MODEL_DIR / "Qwen3-TTS-12Hz-1.7B-Base"
    elif args.model == "whisper":
        repo, revision, files = WHISPER_REPO, WHISPER_REVISION, WHISPER_FILES
        target_dir = MODEL_DIR / "whisper-turbo"
    else:
        repo, revision, files = MEL_REPO, MEL_REVISION, MEL_FILES
        target_dir = MODEL_DIR / "Mel-Deux"

    try:
        total = sum(metadata["size"] for metadata in files.values())
        print(json.dumps({
            "event": "model_begin", "model": args.model,
            "repo": repo, "total_bytes": total, "source": source
        }, ensure_ascii=False), flush=True)
        endpoint_order = _resolve_model_endpoint_order(source, repo, revision, files, args.model)
        completed = 0
        for relpath, metadata in files.items():
            completed += _download_one(
                repo,
                revision,
                relpath,
                metadata,
                target_dir / relpath,
                source,
                model=args.model,
                completed_before=completed,
                model_total=total,
                endpoint_order=endpoint_order,
            )
        print(json.dumps({
            "event": "model_done", "model": args.model,
            "path": str(target_dir), "total_bytes": total,
            "percent": 100.0
        }, ensure_ascii=False), flush=True)
        return emit({"ok": True, "model": args.model, "path": str(target_dir), "bytes": total})
    except Exception as e:
        return emit({"ok": False, "error": str(e)}, 4)


def cmd_seed_default_voice(_: argparse.Namespace) -> int:
    ensure_dirs()
    target = VOICE_DIR / "盼盼-自然口播"
    cfg_path = target / "config.json"
    if cfg_path.is_file():
        return emit({"ok": True, "seeded": False, "reason": "exists", "id": "盼盼-自然口播"})

    ref = DEFAULT_VOICE_DIR / "reference.wav"
    ref_text_file = DEFAULT_VOICE_DIR / "reference.txt"
    if not ref.is_file() or not ref_text_file.is_file():
        return emit({"ok": False, "error": f"App 内置默认人声资源缺失：{DEFAULT_VOICE_DIR}"}, 2)

    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ref, target / "reference.wav")
    ref_text = ref_text_file.read_text(encoding="utf-8").strip()
    cfg = {
        "id": "盼盼-自然口播",
        "name": "盼盼 · 自然口播",
        "reference_audio": "reference.wav",
        "reference_text": ref_text,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "params": {
            "temperature": 0.9,
            "top_p": 1.0,
            "top_k": 50,
            "repetition_penalty": 1.05,
        },
        "note": "日常短视频 / 自然口播",
        "bundled": True,
    }
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    settings = load_settings()
    current_default = settings.get("default_voice")
    if not current_default or not (VOICE_DIR / current_default / "config.json").is_file():
        settings["default_voice"] = cfg["id"]
        save_settings(settings)
    return emit({"ok": True, "seeded": True, "id": cfg["id"]})


def cmd_voice_save(args: argparse.Namespace) -> int:
    ensure_dirs()
    ref = Path(args.ref_audio).expanduser().resolve()
    if not ref.is_file():
        return emit({"ok": False, "error": f"参考音频不存在：{ref}"}, 2)
    base = slugify(args.name)
    folder = VOICE_DIR / base
    if folder.exists():
        for i in range(2, 1000):
            candidate = VOICE_DIR / f"{base}-{i}"
            if not candidate.exists():
                folder = candidate
                break
    folder.mkdir(parents=True)
    target = folder / ("reference" + ref.suffix.lower())
    shutil.copy2(ref, target)
    cfg = {
        "id": folder.name,
        "name": args.name,
        "reference_audio": target.name,
        "reference_text": args.ref_text or "",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "params": {
            "temperature": args.temperature,
            "top_p": args.top_p,
            "top_k": args.top_k,
            "repetition_penalty": args.repetition_penalty,
        },
        "note": args.note or "",
    }
    (folder / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.default:
        settings = load_settings()
        settings["default_voice"] = cfg["id"]
        save_settings(settings)
    return emit({"ok": True, "template": cfg, "folder": str(folder)})


def read_templates() -> list[dict]:
    ensure_dirs()
    default_id = load_settings().get("default_voice")
    items = []
    for cfg_path in sorted(VOICE_DIR.glob("*/config.json")):
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg["default"] = cfg.get("id") == default_id
            cfg["folder"] = str(cfg_path.parent)
            items.append(cfg)
        except Exception:
            continue
    return items


def cmd_voice_list(_: argparse.Namespace) -> int:
    return emit({"ok": True, "templates": read_templates()})
def cmd_voice_default(args: argparse.Namespace) -> int:
    folder = VOICE_DIR / args.id
    if not (folder / "config.json").is_file():
        return emit({"ok": False, "error": f"人声模板不存在：{args.id}"}, 2)
    settings = load_settings()
    settings["default_voice"] = args.id
    save_settings(settings)
    return emit({"ok": True, "default_voice": args.id})


def cmd_voice_delete(args: argparse.Namespace) -> int:
    ensure_dirs()
    root = VOICE_DIR.resolve()
    folder = (VOICE_DIR / args.id).resolve()
    if folder.parent != root:
        return emit({"ok": False, "error": "无效的人声模板 ID"}, 2)
    cfg_path = folder / "config.json"
    if not cfg_path.is_file():
        return emit({"ok": False, "error": f"人声模板不存在：{args.id}"}, 2)
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return emit({"ok": False, "error": "人声模板配置损坏，无法安全删除"}, 2)
    if cfg.get("bundled"):
        return emit({"ok": False, "error": "内置人声模板不能删除"}, 2)

    settings = load_settings()
    was_default = settings.get("default_voice") == args.id
    shutil.rmtree(folder)
    if was_default:
        remaining = read_templates()
        if remaining:
            settings["default_voice"] = remaining[0]["id"]
        else:
            settings.pop("default_voice", None)
        save_settings(settings)
    return emit({"ok": True, "deleted": args.id})


def cmd_voice_clone(args: argparse.Namespace) -> int:
    folder = VOICE_DIR / args.id
    cfg_path = folder / "config.json"
    if not cfg_path.is_file():
        return emit({"ok": False, "error": f"人声模板不存在：{args.id}"}, 2)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    ref = folder / cfg["reference_audio"]
    p = cfg.get("params", {})
    ns = argparse.Namespace(
        ref_audio=str(ref), text=args.text, ref_text=cfg.get("reference_text", ""),
        output_dir=args.output_dir,
        temperature=float(p.get("temperature", 0.9)),
        top_p=float(p.get("top_p", 1.0)),
        top_k=int(p.get("top_k", 50)),
        repetition_penalty=float(p.get("repetition_penalty", 1.05)),
    )
    return cmd_clone(ns)


def add_tts_params(p: argparse.ArgumentParser) -> None:
    p.add_argument("--temperature", type=float, default=0.9)
    p.add_argument("--top-p", type=float, default=1.0)
    p.add_argument("--top-k", type=int, default=50)
    p.add_argument("--repetition-penalty", type=float, default=1.05)
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="spp-worker", description="SPP Audio Studio local worker")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("convert")
    c.add_argument("input")
    c.add_argument("--output-dir")
    c.set_defaults(func=cmd_convert)

    s = sub.add_parser("separate")
    s.add_argument("input")
    s.add_argument("--output-dir")
    s.add_argument("--format", choices=["MP3", "WAV", "FLAC"], default="MP3")
    s.add_argument("--bitrate", default="192k")
    s.add_argument("--keep", choices=["instrumental", "vocals", "both"], default="instrumental")
    s.set_defaults(func=cmd_separate)

    cl = sub.add_parser("clone")
    cl.add_argument("--ref-audio", required=True)
    cl.add_argument("--ref-text", default="")
    cl.add_argument("--text", required=True)
    cl.add_argument("--output-dir")
    add_tts_params(cl)
    cl.set_defaults(func=cmd_clone)
    vs = sub.add_parser("voice-save")
    vs.add_argument("--name", required=True)
    vs.add_argument("--ref-audio", required=True)
    vs.add_argument("--ref-text", default="")
    vs.add_argument("--note", default="")
    vs.add_argument("--default", action="store_true")
    add_tts_params(vs)
    vs.set_defaults(func=cmd_voice_save)

    seed = sub.add_parser("seed-default-voice")
    seed.set_defaults(func=cmd_seed_default_voice)

    vl = sub.add_parser("voice-list")
    vl.set_defaults(func=cmd_voice_list)

    vd = sub.add_parser("voice-default")
    vd.add_argument("id")
    vd.set_defaults(func=cmd_voice_default)

    vdel = sub.add_parser("voice-delete")
    vdel.add_argument("id")
    vdel.set_defaults(func=cmd_voice_delete)

    vc = sub.add_parser("voice-clone")
    vc.add_argument("id")
    vc.add_argument("--text", required=True)
    vc.add_argument("--output-dir")
    vc.set_defaults(func=cmd_voice_clone)

    ms = sub.add_parser("model-status")
    ms.set_defaults(func=cmd_model_status)

    ml = sub.add_parser("model-link")
    ml.add_argument("--qwen-model-dir")
    ml.add_argument("--mel-model-dir")
    ml.add_argument("--qwen-python")
    ml.add_argument("--separator-bin")
    ml.add_argument("--asr-python")
    ml.add_argument("--asr-model")
    ml.set_defaults(func=cmd_model_link)

    ds = sub.add_parser("download-source")
    ds.add_argument("source", choices=["auto", "mirror", "official"])
    ds.set_defaults(func=cmd_download_source)

    storage = sub.add_parser("model-storage")
    storage.add_argument("storage", choices=["appdata", "portable"])
    storage.set_defaults(func=cmd_model_storage)

    md = sub.add_parser("model-download")
    md.add_argument("model", choices=["qwen", "mel", "whisper"])
    md.add_argument("--source", choices=["auto", "mirror", "official"])
    md.set_defaults(func=cmd_model_download)

    ri = sub.add_parser("runtime-install")
    ri.add_argument("runtime", choices=["qwen", "mel", "asr"])
    ri.add_argument("--source", choices=["auto", "mirror", "official"])
    ri.add_argument("--repair", action="store_true", help="暂存验证后替换运行环境，失败保留原安装")
    ri.set_defaults(func=cmd_runtime_install)
    return p


def main() -> int:
    ensure_dirs()
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
