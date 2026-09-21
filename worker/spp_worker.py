#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

HOME = Path.home()
APP_NAME = "SPP Audio Studio"
DATA_DIR = HOME / "Library" / "Application Support" / APP_NAME
CACHE_DIR = HOME / "Library" / "Caches" / APP_NAME
VOICE_DIR = DATA_DIR / "Voices"
MODEL_DIR = DATA_DIR / "Models"
RUNTIME_DIR = DATA_DIR / "Runtime"
HISTORY_FILE = DATA_DIR / "history.jsonl"
SETTINGS_FILE = DATA_DIR / "settings.json"

BUNDLED_FORMAT_BIN = Path(__file__).resolve().parent.parent / "bin" / "format_converter"
FORMAT_BIN = Path(os.environ.get("SPP_FORMAT_BIN", str(BUNDLED_FORMAT_BIN))).expanduser()
MEL_MODEL = os.environ.get("SPP_MEL_MODEL", "becruily_deux.ckpt")
MEL_REGISTRY_NAME = "Roformer Model: Mel-Band RoFormer Deux by becruily"
MEL_CONFIG = "config_deux_becruily.yaml"
BRIDGE = Path(__file__).with_name("qwen_bridge.py")
MEL_BRIDGE = Path(__file__).with_name("mel_bridge.py")
DEFAULT_VOICE_DIR = Path(os.environ.get("SPP_DEFAULT_VOICE_DIR", "")).expanduser()
PYTHON_CORE = Path(os.environ.get("SPP_PYTHON_CORE", sys.executable)).expanduser()
QWEN_RUNTIME_DIR = RUNTIME_DIR / "qwen"
QWEN_SITE = QWEN_RUNTIME_DIR / "site-packages"
MEL_RUNTIME_DIR = RUNTIME_DIR / "mel"
MEL_SITE = MEL_RUNTIME_DIR / "site-packages"
ASR_RUNTIME_DIR = RUNTIME_DIR / "asr"
ASR_SITE = ASR_RUNTIME_DIR / "site-packages"

QWEN_RUNTIME_PACKAGES = [
    "mlx-audio==0.4.7",
    "mlx==0.32.0",
    "mlx-metal==0.32.0",
    "mlx-lm==0.31.3",
    "transformers==5.14.1",
    "tokenizers==0.22.2",
    "numpy==2.4.6",
    "scipy==1.17.1",
    "soundfile==0.14.0",
]
ASR_RUNTIME_PACKAGES = ["mlx-whisper==0.4.3", "mlx==0.32.0", "mlx-metal==0.32.0", "torch==2.13.0", "numpy==2.4.6", "imageio-ffmpeg==0.6.0"]
MEL_RUNTIME_PACKAGE = "audio-separator==0.47.0"
# Mel-Deux uses the MDXC path. diffq is only required by Demucs in audio-separator,
# so RC6 installs the needed all-wheel dependencies explicitly and skips diffq.
MEL_RUNTIME_DEPS = [
    "beartype>=0.18.5,<0.19.0", "einops>=0.7", "julius>=0.2",
    "librosa>=0.10", "ml_collections", "numpy>=2", "onnx-weekly",
    "onnxruntime==1.29.0",
    "onnx2torch-py313>=1.6", "packaging", "pydub>=0.25", "pyyaml",
    "requests>=2", "resampy>=0.4", "rotary-embedding-torch>=0.6.1,<0.7.0",
    "samplerate==0.1.0", "scipy>=1.13.0,<2.0.0", "six>=1.16",
    "soundfile>=0.12", "torch>=2.13,<3", "tqdm", "imageio-ffmpeg==0.6.0",
]
PYPI_OFFICIAL = "https://pypi.org/simple"
PYPI_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

QWEN_REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
QWEN_REVISION = "e7dd0585652209fa0d7783659aad4e8a324de11c"
MEL_REPO = "becruily/mel-band-roformer-deux"
MEL_REVISION = "2da74427d682a3df47a774378fc24d7a1a0cdaad"
WHISPER_REPO = "mlx-community/whisper-large-v3-turbo"
WHISPER_REVISION = "main"
HF_OFFICIAL = "https://huggingface.co"
HF_MIRROR = "https://hf-mirror.com"

QWEN_FILES = {
    "config.json": 5522,
    "generation_config.json": 245,
    "merges.txt": 1671839,
    "model.safetensors": 2417320525,
    "model.safetensors.index.json": 78070,
    "preprocessor_config.json": 127,
    "speech_tokenizer/config.json": 2336,
    "speech_tokenizer/configuration.json": 76,
    "speech_tokenizer/model.safetensors": 682293092,
    "speech_tokenizer/preprocessor_config.json": 234,
    "tokenizer_config.json": 7344,
    "vocab.json": 2776833,
}
MEL_FILES = {
    "becruily_deux.ckpt": 435006815,
    "config_deux_becruily.yaml": 1175,
}
WHISPER_FILES = {"config.json": 268, "weights.safetensors": 1613977612}


def ensure_dirs() -> None:
    for p in (DATA_DIR, CACHE_DIR, VOICE_DIR, MODEL_DIR, RUNTIME_DIR):
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


def run_capture(cmd: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged.pop("PYTHONPATH", None)
    if env:
        merged.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, env=merged)


def locate_ffmpeg() -> Path | None:
    candidates = [
        shutil.which("ffmpeg"),
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        str(MEL_RUNTIME_DIR / "ffmpeg/ffmpeg"),
        str(ASR_RUNTIME_DIR / "ffmpeg/ffmpeg"),
    ]
    for value in candidates:
        if value:
            p = Path(value)
            if p.is_file() and os.access(p, os.X_OK):
                return p
    return None


def cmd_doctor(_: argparse.Namespace) -> int:
    env = resolve_environment()
    mel_model_dir = Path(env["mel_model_dir"])
    qwen_model = Path(env["qwen_model"])
    asr_python = Path(env["asr_python"])
    asr_model = Path(env["asr_model"])

    checks = {
        "bundled_python_core": PYTHON_CORE.is_file() and os.access(PYTHON_CORE, os.X_OK),
        "format_converter_binary": FORMAT_BIN.is_file() and os.access(FORMAT_BIN, os.X_OK),
        "qwen_runtime": bool(env["qwen_runtime_installed"]),
        "qwen_model": _model_files_complete(qwen_model, QWEN_FILES),
        "qwen_bridge": BRIDGE.is_file(),
        "mel_runtime": bool(env["separator_runtime_installed"]),
        "mel_model": _model_files_complete(mel_model_dir, MEL_FILES),
        "mel_config": (mel_model_dir / "config_deux_becruily.yaml").is_file(),
        "mel_ffmpeg": locate_ffmpeg() is not None,
        "asr_optional": bool(env["asr_runtime_installed"]) and _model_files_complete(asr_model, WHISPER_FILES),
    }
    required = [
        "bundled_python_core",
        "format_converter_binary",
        "qwen_runtime",
        "qwen_model",
        "qwen_bridge",
        "mel_runtime",
        "mel_model",
        "mel_config",
        "mel_ffmpeg",
    ]
    core_ready = checks["bundled_python_core"] and checks["format_converter_binary"]
    return emit({
        "ok": True,
        "core_ready": core_ready,
        "ready": all(checks[key] for key in required),
        "checks": checks,
        "paths": {
            "data": str(DATA_DIR), "cache": str(CACHE_DIR), "voices": str(VOICE_DIR),
            "models": str(MODEL_DIR), "runtime": str(RUNTIME_DIR),
            "python_core": str(PYTHON_CORE),
            "qwen_model": str(qwen_model), "mel_model_dir": str(mel_model_dir),
            "ffmpeg": str(locate_ffmpeg() or ""),
            "asr_model": str(asr_model),
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

    out_root = Path(args.output_dir).expanduser() if args.output_dir else src.parent / "SPP Audio"
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

    out_dir = Path(args.output_dir).expanduser() if args.output_dir else src.parent / "SPP Audio" / src.stem
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
        cmd = [str(PYTHON_CORE), str(MEL_BRIDGE)] + base_args
        mel_env["PYTHONPATH"] = str(env["mel_site"])
        mel_env["PYTHONNOUSERSITE"] = "1"

    try:
        ffmpeg = locate_ffmpeg()
        mel_path = "/usr/bin:/bin:/usr/sbin:/sbin"
        if ffmpeg:
            mel_path = str(ffmpeg.parent) + ":" + mel_path
        mel_env["PATH"] = mel_path
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
    cmd = [
        str(qwen_python), str(BRIDGE),
        "--ref-audio", str(ref_audio), "--text", text,
        "--output-dir", str(output_dir),
        "--temperature", str(temperature), "--top-p", str(top_p),
        "--top-k", str(top_k), "--repetition-penalty", str(repetition_penalty),
    ]
    if ref_text:
        cmd += ["--ref-text", ref_text]

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
    if ffmpeg:
        bridge_env["PATH"] = str(ffmpeg.parent) + ":/usr/bin:/bin:/usr/sbin:/sbin"

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
    out_dir = Path(args.output_dir).expanduser() if args.output_dir else DATA_DIR / "Outputs" / "Voice"
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


def resolve_environment() -> dict:
    ensure_dirs()
    settings = load_settings()
    paths = settings.get("paths", {})

    qwen_model, qwen_model_source = _pick_path(
        paths.get("qwen_model_dir"),
        MODEL_DIR / "Qwen3-TTS-12Hz-1.7B-Base-8bit",
    )
    mel_model_dir, mel_source = _pick_path(
        paths.get("mel_model_dir"),
        MODEL_DIR / "Mel-Deux",
    )

    qwen_link = Path(paths["qwen_python"]).expanduser() if paths.get("qwen_python") else None
    qwen_managed = (
        _marker_exists(QWEN_RUNTIME_DIR)
        and (QWEN_SITE / "mlx_audio").exists()
        and PYTHON_CORE.is_file()
    )
    if qwen_link and qwen_link.is_file():
        qwen_python = qwen_link
        qwen_python_source = "linked"
        qwen_runtime_installed = True
    elif qwen_managed:
        qwen_python = PYTHON_CORE
        qwen_python_source = "managed"
        qwen_runtime_installed = True
    else:
        qwen_python = PYTHON_CORE
        qwen_python_source = "missing"
        qwen_runtime_installed = False

    separator_link = Path(paths["separator_bin"]).expanduser() if paths.get("separator_bin") else None
    mel_managed = (
        _marker_exists(MEL_RUNTIME_DIR)
        and (MEL_SITE / "audio_separator").exists()
        and (MEL_SITE / "onnxruntime").exists()
        and _mel_registry_ready(MEL_SITE)
        and MEL_BRIDGE.is_file()
        and PYTHON_CORE.is_file()
    )
    if separator_link and separator_link.is_file():
        separator_bin = separator_link
        separator_source = "linked"
        separator_runtime_installed = True
    elif mel_managed:
        separator_bin = MEL_BRIDGE
        separator_source = "managed"
        separator_runtime_installed = True
    else:
        separator_bin = MEL_BRIDGE
        separator_source = "missing"
        separator_runtime_installed = False

    asr_link = Path(paths["asr_python"]).expanduser() if paths.get("asr_python") else None
    asr_managed = _marker_exists(ASR_RUNTIME_DIR) and (ASR_SITE / "mlx_whisper").is_dir() and (ASR_RUNTIME_DIR / "ffmpeg/ffmpeg").is_file() and PYTHON_CORE.is_file()
    if asr_link and asr_link.is_file():
        asr_python, asr_python_source, asr_runtime_installed = asr_link, "linked", True
    elif asr_managed:
        asr_python, asr_python_source, asr_runtime_installed = PYTHON_CORE, "managed", True
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
    }


def _model_files_complete(folder: Path, files: dict[str, int]) -> bool:
    for relpath, expected_size in files.items():
        target = folder / relpath
        if not target.is_file() or target.stat().st_size != expected_size:
            return False
    return True


def cmd_model_status(_: argparse.Namespace) -> int:
    env = resolve_environment()
    qwen_model_path = Path(env["qwen_model"])
    mel_model_path = Path(env["mel_model_dir"])
    payload = {
        "ok": True,
        "download_source": env["download_source"],
        "python_core": {
            "path": str(PYTHON_CORE),
            "installed": PYTHON_CORE.is_file() and os.access(PYTHON_CORE, os.X_OK),
        },
        "models": {
            "whisper": {
                "repo": WHISPER_REPO,
                "path": str(env["asr_model"]),
                "source": env["asr_model_source"],
                "installed": _model_files_complete(Path(env["asr_model"]), WHISPER_FILES),
            },
            "qwen": {
                "repo": QWEN_REPO,
                "path": str(env["qwen_model"]),
                "source": env["qwen_model_source"],
                "installed": _model_files_complete(qwen_model_path, QWEN_FILES),
            },
            "mel_deux": {
                "repo": MEL_REPO,
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



def _pypi_endpoint_order(source: str) -> list[str]:
    if source == "mirror":
        return [PYPI_MIRROR, PYPI_OFFICIAL]
    if source == "official":
        return [PYPI_OFFICIAL, PYPI_MIRROR]
    return [PYPI_MIRROR, PYPI_OFFICIAL]


def _verify_python_import(site: Path, imports: str) -> subprocess.CompletedProcess:
    return run_capture(
        [str(PYTHON_CORE), "-c", imports],
        {
            "PYTHONPATH": str(site),
            "PYTHONNOUSERSITE": "1",
        },
    )


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
    if kind == "qwen":
        return _marker_exists(QWEN_RUNTIME_DIR) and (QWEN_SITE / "mlx_audio").exists()
    if kind == "asr":
        return _marker_exists(ASR_RUNTIME_DIR) and (ASR_SITE / "mlx_whisper").exists() and (ASR_RUNTIME_DIR / "ffmpeg/ffmpeg").is_file()
    return _marker_exists(MEL_RUNTIME_DIR) and (MEL_SITE / "audio_separator").exists() and (MEL_SITE / "onnxruntime").exists() and _mel_registry_ready(MEL_SITE)


def cmd_runtime_install(args: argparse.Namespace) -> int:
    ensure_dirs()
    kind = args.runtime
    source = args.source or load_settings().get("download_source", "auto")

    if not PYTHON_CORE.is_file() or not os.access(PYTHON_CORE, os.X_OK):
        return emit({"ok": False, "error": "App 内置 Python Core 不可用。"}, 3)

    if _runtime_installed(kind):
        return emit({"ok": True, "runtime": kind, "installed": True, "already_installed": True})

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

    for endpoint in _pypi_endpoint_order(source):
        temp_dir = Path(tempfile.mkdtemp(prefix=f"runtime-{kind}-", dir=CACHE_DIR))
        site = temp_dir / "site-packages"
        site.mkdir(parents=True, exist_ok=True)

        print(json.dumps({
            "event": "runtime_install_start",
            "runtime": kind,
            "index": endpoint,
            "packages": packages,
        }, ensure_ascii=False), flush=True)

        install_failed = False
        for step_packages, no_deps in install_steps:
            cmd = [
                str(PYTHON_CORE), "-m", "pip", "install",
                "--disable-pip-version-check",
                "--no-input",
                "--only-binary=:all:",
                "--break-system-packages",
                "--target", str(site),
                "--index-url", endpoint,
            ]
            if no_deps:
                cmd.append("--no-deps")
            cmd += step_packages

            proc = run_capture(cmd, {
                "PIP_CACHE_DIR": str(CACHE_DIR / "pip"),
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PYTHONNOUSERSITE": "1",
            })
            if proc.returncode != 0:
                errors.append(f"{endpoint}: pip exit {proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}")
                install_failed = True
                break

        if install_failed:
            shutil.rmtree(temp_dir, ignore_errors=True)
            continue

        if kind == "asr":
            verify = _verify_python_import(site, "import mlx_whisper, mlx, imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")
            if verify.returncode != 0:
                errors.append(f"{endpoint}: ASR import failed: {(verify.stderr or verify.stdout)[-800:]}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                continue
            if verify.returncode == 0:
                ffmpeg_path = Path(verify.stdout.strip().splitlines()[-1])
                if not ffmpeg_path.is_file():
                    errors.append(f"{endpoint}: ASR ffmpeg executable missing")
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    continue
                ffmpeg_dir = temp_dir / "ffmpeg"
                ffmpeg_dir.mkdir(parents=True, exist_ok=True)
                ffmpeg_target = ffmpeg_dir / "ffmpeg"
                shutil.copy2(ffmpeg_path, ffmpeg_target)
                ffmpeg_target.chmod(0o755)
        elif kind == "qwen":
            verify = _verify_python_import(site, "import mlx_audio, mlx; print('QWEN_RUNTIME_OK')")
            if verify.returncode != 0:
                errors.append(f"{endpoint}: Qwen import failed: {(verify.stderr or verify.stdout)[-800:]}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                continue
        else:
            _install_mel_registry(site)
            verify = _verify_python_import(
                site,
                "from audio_separator.separator import Separator; import onnxruntime, imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())",
            )
            if verify.returncode != 0:
                errors.append(f"{endpoint}: Mel import failed: {(verify.stderr or verify.stdout)[-800:]}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                continue

            ffmpeg_path = Path(verify.stdout.strip().splitlines()[-1])
            if not ffmpeg_path.is_file():
                errors.append(f"{endpoint}: imageio-ffmpeg executable missing")
                shutil.rmtree(temp_dir, ignore_errors=True)
                continue
            ffmpeg_dir = temp_dir / "ffmpeg"
            ffmpeg_dir.mkdir(parents=True, exist_ok=True)
            ffmpeg_target = ffmpeg_dir / "ffmpeg"
            shutil.copy2(ffmpeg_path, ffmpeg_target)
            ffmpeg_target.chmod(0o755)

        marker = {
            "runtime": kind,
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "python": str(PYTHON_CORE),
            "packages": packages,
            "source": endpoint,
        }
        (temp_dir / "installed.json").write_text(
            json.dumps(marker, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if final_dir.exists():
            backup = CACHE_DIR / f"{kind}-runtime-previous-{int(time.time())}"
            shutil.move(str(final_dir), str(backup))
        shutil.move(str(temp_dir), str(final_dir))

        print(json.dumps({
            "event": "runtime_install_done",
            "runtime": kind,
            "path": str(final_dir),
            "index": endpoint,
        }, ensure_ascii=False), flush=True)
        return emit({"ok": True, "runtime": kind, "installed": True, "path": str(final_dir)})

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


def _download_one(repo: str, revision: str, relpath: str, expected_size: int, target: Path, source: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size == expected_size:
        print(json.dumps({
            "event": "skip", "file": relpath, "bytes": expected_size
        }, ensure_ascii=False), flush=True)
        return

    part = target.with_suffix(target.suffix + ".part")
    errors = []
    for endpoint in _endpoint_order(source):
        url = f"{endpoint}/{repo}/resolve/{revision}/{quote(relpath, safe='/')}"
        print(json.dumps({
            "event": "download_start", "file": relpath,
            "endpoint": endpoint, "expected_bytes": expected_size
        }, ensure_ascii=False), flush=True)
        cmd = [
            "/usr/bin/curl", "-L", "--fail", "--silent", "--show-error", "--retry", "3",
            "--retry-delay", "2", "--connect-timeout", "15",
            "-C", "-", "-o", str(part), url,
        ]
        proc = subprocess.run(cmd)
        if proc.returncode == 0 and part.is_file() and part.stat().st_size == expected_size:
            part.replace(target)
            print(json.dumps({
                "event": "download_done", "file": relpath,
                "bytes": expected_size, "endpoint": endpoint
            }, ensure_ascii=False), flush=True)
            return
        errors.append(f"{endpoint}: curl={proc.returncode}, size={part.stat().st_size if part.exists() else 0}")
    raise RuntimeError(f"下载失败 {relpath}: " + " | ".join(errors))


def cmd_model_download(args: argparse.Namespace) -> int:
    ensure_dirs()
    settings = load_settings()
    source = args.source or settings.get("download_source", "auto")
    if args.model == "qwen":
        repo, revision, files = QWEN_REPO, QWEN_REVISION, QWEN_FILES
        target_dir = MODEL_DIR / "Qwen3-TTS-12Hz-1.7B-Base-8bit"
    elif args.model == "whisper":
        repo, revision, files = WHISPER_REPO, WHISPER_REVISION, WHISPER_FILES
        target_dir = MODEL_DIR / "whisper-turbo"
    else:
        repo, revision, files = MEL_REPO, MEL_REVISION, MEL_FILES
        target_dir = MODEL_DIR / "Mel-Deux"

    try:
        total = sum(files.values())
        print(json.dumps({
            "event": "model_begin", "model": args.model,
            "repo": repo, "total_bytes": total, "source": source
        }, ensure_ascii=False), flush=True)
        for relpath, size in files.items():
            _download_one(repo, revision, relpath, size, target_dir / relpath, source)
        print(json.dumps({
            "event": "model_done", "model": args.model,
            "path": str(target_dir), "total_bytes": total
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

    md = sub.add_parser("model-download")
    md.add_argument("model", choices=["qwen", "mel", "whisper"])
    md.add_argument("--source", choices=["auto", "mirror", "official"])
    md.set_defaults(func=cmd_model_download)

    ri = sub.add_parser("runtime-install")
    ri.add_argument("runtime", choices=["qwen", "mel", "asr"])
    ri.add_argument("--source", choices=["auto", "mirror", "official"])
    ri.set_defaults(func=cmd_runtime_install)
    return p


def main() -> int:
    ensure_dirs()
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
