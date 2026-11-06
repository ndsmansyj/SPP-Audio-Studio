"""Windows runtime recipes and installation identity (not a wheel lock).

These recipes retain upstream version ranges. Each successful installation
records the actual distribution metadata so later drift is visible. A fully
reproducible wheel lock must be resolved and tested on Windows separately.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

SCHEMA_VERSION = 2
PYTORCH_CUDA_PACKAGES = ["torch==2.11.0+cu126", "torchaudio==2.11.0+cu126"]
QWEN_RUNTIME_PACKAGES = PYTORCH_CUDA_PACKAGES + ["qwen-tts==0.1.1", "soundfile>=0.12", "imageio-ffmpeg>=0.6"]
ASR_RUNTIME_PACKAGES = ["faster-whisper==1.2.1", "ctranslate2==4.8.2", "imageio-ffmpeg>=0.6"]
MEL_RUNTIME_PACKAGE = "audio-separator==0.47.0"
MEL_RUNTIME_DEPS = [
    "beartype>=0.18.5,<0.19.0", "einops>=0.7", "julius>=0.2",
    "librosa>=0.10", "ml_collections", "numpy>=2", "onnx-weekly",
    "onnxruntime==1.29.0", "onnx2torch>=1.5", "packaging", "pydub>=0.25",
    "pyyaml", "requests>=2", "resampy>=0.4", "rotary-embedding-torch>=0.6.1,<0.7.0",
    "samplerate>=0.1", "scipy>=1.13.0,<2.0.0", "six>=1.16",
    "soundfile>=0.12", *PYTORCH_CUDA_PACKAGES, "tqdm", "imageio-ffmpeg>=0.6",
]
PACKAGES = {"qwen": QWEN_RUNTIME_PACKAGES, "mel": [MEL_RUNTIME_PACKAGE, *MEL_RUNTIME_DEPS],
            "asr": ASR_RUNTIME_PACKAGES}
MODULES = {"qwen": ["qwen_tts", "torch", "torchaudio", "imageio_ffmpeg"],
           "mel": ["audio_separator", "torch", "onnxruntime", "imageio_ffmpeg"],
           "asr": ["faster_whisper", "ctranslate2", "imageio_ffmpeg"]}


def manifest_id(kind: str) -> str:
    value = {"schema": SCHEMA_VERSION, "runtime": kind, "packages": PACKAGES[kind],
             "python_abi": f"{sys.version_info.major}.{sys.version_info.minor}",
             "verification": "imports-cuda-probe-managed-ffmpeg-v1"}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def distribution_inventory(site: Path) -> dict[str, str]:
    result = {}
    for metadata in sorted(site.glob("*.dist-info/METADATA")):
        result[str(metadata.parent.name)] = hashlib.sha256(metadata.read_bytes()).hexdigest()
    return result


def runtime_health(kind: str, root: Path) -> dict:
    marker_path = root / "installed.json"
    installed = marker_path.is_file() or (root / "site-packages").is_dir()
    result = {"installed": installed, "healthy": False,
              "status": "repair_required" if installed else "missing", "reason": "运行环境未安装"}
    if not installed:
        return result
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if not isinstance(marker, dict):
            raise ValueError("运行环境安装记录格式损坏，请修复运行环境")
        if marker.get("schema_version") != SCHEMA_VERSION or marker.get("manifest_id") != manifest_id(kind):
            raise ValueError("运行环境来自旧版或安装配方已变化，请修复运行环境")
        site = root / "site-packages"
        if any(not (site / module).exists() for module in MODULES[kind]):
            raise ValueError("运行环境组件缺失，请修复运行环境")
        inventory = distribution_inventory(site)
        if not inventory or inventory != marker.get("distributions"):
            raise ValueError("运行环境依赖记录缺失或已变化，请修复运行环境")
        if not (root / "ffmpeg" / "ffmpeg.exe").is_file():
            raise ValueError("托管 FFmpeg 缺失，请修复运行环境")
        result.update(healthy=True, status="ready", reason="安装指纹与组件检查通过",
                      verified_at=marker.get("installed_at"), hardware=marker.get("hardware", {}))
    except (OSError, ValueError, TypeError) as exc:
        result["reason"] = str(exc)
    return result
