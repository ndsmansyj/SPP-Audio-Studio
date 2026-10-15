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

QWEN_MODEL = Path(os.environ.get("SPP_QWEN_MODEL", "")).expanduser()
ASR_PY = Path(os.environ.get("SPP_ASR_PY", "")).expanduser()
ASR_MODEL = Path(os.environ.get("SPP_ASR_MODEL", "")).expanduser()
ASR_SITE = os.environ.get("SPP_ASR_SITE", "")
IS_FROZEN = bool(getattr(sys, "frozen", False))


def add_managed_dll_directories() -> None:
    """Expose only the native library directory that PyTorch owns.

    Scanning every DLL directory below site-packages pollutes Windows' loader
    search order and can make unrelated native extensions resolve the wrong
    dependency. PyTorch already keeps its CUDA/runtime DLLs under torch/lib.
    """
    if not IS_FROZEN or not os.environ.get("PYTHONPATH"):
        return
    site = Path(os.environ["PYTHONPATH"])
    torch_lib = site / "torch" / "lib"
    if not torch_lib.is_dir() or not hasattr(os, "add_dll_directory"):
        return

    global _DLL_HANDLES
    try:
        _DLL_HANDLES = [os.add_dll_directory(str(torch_lib))]
    except OSError:
        _DLL_HANDLES = []


_DLL_HANDLES: list[object] = []
add_managed_dll_directories()

def find_ffmpeg() -> str | None:
    return os.environ.get("SPP_FFMPEG") or shutil.which("ffmpeg")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--ref-audio", required=True)
    p.add_argument("--ref-text", default="")
    p.add_argument("--text", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--temperature", type=float, default=0.9)
    p.add_argument("--top-p", type=float, default=1.0)
    p.add_argument("--top-k", type=int, default=50)
    p.add_argument("--repetition-penalty", type=float, default=1.05)
    return p


def require_cuda(torch_module) -> None:
    if not torch_module.cuda.is_available():
        raise RuntimeError("CUDA 不可用；AI 推理禁止回退到 CPU")


def find_asr_cuda_dll_directory() -> Path | None:
    """Find a complete cuBLAS 12 pair installed in one managed runtime.

    ASR is deliberately lightweight: the CUDA libraries live in the existing
    Qwen/Mel PyTorch runtime instead of being installed system-wide. Do not
    scan arbitrary site-packages DLL directories or require a global PATH.
    """
    candidates: list[Path] = []
    configured = os.environ.get("SPP_ASR_CUDA_DLL_DIR", "")
    if configured:
        candidates.append(Path(configured))
    if ASR_SITE:
        site = Path(ASR_SITE)
        runtime = site.parent.parent  # Runtime/asr/site-packages -> Runtime/
        candidates.extend([
            site / "ctranslate2",
            site / "nvidia" / "cublas" / "bin",
            runtime / "qwen" / "site-packages" / "torch" / "lib",
            runtime / "mel" / "site-packages" / "torch" / "lib",
        ])
    for directory in candidates:
        if (directory / "cublas64_12.dll").is_file() and (directory / "cublasLt64_12.dll").is_file():
            return directory
    return None


def build_asr_environment() -> dict[str, str]:
    """Provide CUDA 12 DLL search paths to the independent Whisper process."""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    if ASR_SITE:
        env["PYTHONPATH"] = ASR_SITE
    dll_dir = find_asr_cuda_dll_directory()
    if dll_dir is not None:
        env["SPP_ASR_CUDA_DLL_DIR"] = str(dll_dir)
        # CTranslate2 dynamically calls LoadLibrary for cuBLAS. Besides
        # os.add_dll_directory, explicitly prefix the child process PATH.
        env["PATH"] = str(dll_dir) + os.pathsep + env.get("PATH", "")
    return env


def transcribe(wav_path: Path) -> str:
    if not ASR_PY.is_file() or not ASR_MODEL.exists():
        raise RuntimeError("参考文本为空，但本机没有可用的 Whisper ASR；请填写参考文本。")
    script = (
        "import os, sys\n"
        "if sys.platform == 'win32' and os.environ.get('SPP_ASR_CUDA_DLL_DIR'):\n"
        "    import ctypes\n"
        "    directory = os.environ['SPP_ASR_CUDA_DLL_DIR']\n"
        "    _dll_search_handle = os.add_dll_directory(directory)\n"
        "    # Load by full path before CTranslate2 lazily resolves their names.\n"
        "    _cuda_dll_handles = [ctypes.WinDLL(os.path.join(directory, dll))\n"
        "                         for dll in ('cublasLt64_12.dll', 'cublas64_12.dll')]\n"
        "from faster_whisper import WhisperModel\n"
        "model = WhisperModel(sys.argv[2], device='cuda', device_index=0, compute_type='float16')\n"
        "segments, _ = model.transcribe(sys.argv[1], language='zh')\n"
        "print(''.join(s.text for s in segments).strip())\n"
    )
    command = [str(ASR_PY), "-c", script, str(wav_path), str(ASR_MODEL)]
    proc = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=build_asr_environment(), timeout=300
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if re.search(r"cublas(?:Lt)?64_12\.dll", detail, flags=re.IGNORECASE):
            raise RuntimeError(
                "Whisper GPU 转写无法加载 CUDA 12 cuBLAS。请检查 Qwen/Mel Runtime；"
                "也可以先在『临时参考音的原话』填写音频中的实际内容以跳过自动转写。"
                + (" 详细信息：" + detail[-400:] if detail else "")
            )
        raise RuntimeError("转写失败：" + detail[-900:])
    return proc.stdout.strip()


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 10000):
        p = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not p.exists():
            return p
    raise RuntimeError("无法生成不重名输出文件")
def main() -> int:
    args = build_parser().parse_args()
    ref = Path(args.ref_audio).expanduser().resolve()
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not ref.is_file():
        raise RuntimeError(f"参考音频不存在：{ref}")
    if not QWEN_MODEL.is_dir():
        raise RuntimeError(f"Qwen 模型不存在：{QWEN_MODEL}")

    import torch
    from qwen_tts import Qwen3TTSModel
    import soundfile as sf
    require_cuda(torch)

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg.exe；请加入 PATH")

    with tempfile.TemporaryDirectory(prefix="spp-qwen-") as tmp:
        tmpdir = Path(tmp)
        wav = tmpdir / "reference.wav"
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(ref),
             "-ar", "24000", "-ac", "1", str(wav)],
            capture_output=True, text=True
        )
        if proc.returncode != 0 or not wav.is_file():
            raise RuntimeError("参考音频转换失败：" + (proc.stderr or proc.stdout)[-500:])

        ref_text = args.ref_text.strip() or transcribe(wav)
        model = Qwen3TTSModel.from_pretrained(
            str(QWEN_MODEL), device_map="cuda:0",
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
        )
        kwargs = dict(
            text=args.text,
            ref_audio=str(wav),
            ref_text=ref_text,
            language="Chinese",
            temperature=args.temperature,
            top_p=args.top_p,
            repetition_penalty=args.repetition_penalty,
        )
        if args.top_k > 0:
            kwargs["top_k"] = args.top_k
        wavs, sample_rate = model.generate_voice_clone(**kwargs)
        if not wavs:
            raise RuntimeError("Qwen 生成完成但没有找到输出 WAV")

        stem = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", args.text)[:10] or "clone"
        final = unique_path(out_dir / f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}.wav")
        sf.write(str(final), wavs[0], sample_rate)
        duration = float(sf.info(str(final)).duration)

        print("RESULT_JSON=" + json.dumps({
            "path": str(final),
            "duration": duration,
            "ref_text": ref_text,
        }, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)
