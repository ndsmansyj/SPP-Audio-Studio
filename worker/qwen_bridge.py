#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

QWEN_MODEL = Path(os.environ.get("SPP_QWEN_MODEL", "")).expanduser()
ASR_PY = Path(os.environ.get("SPP_ASR_PY", "")).expanduser()
ASR_MODEL = Path(os.environ.get("SPP_ASR_MODEL", "")).expanduser()
ASR_SITE = os.environ.get("SPP_ASR_SITE", "")


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
def transcribe(wav_path: Path) -> str:
    if not ASR_PY.is_file() or not ASR_MODEL.exists():
        raise RuntimeError("参考文本为空，但本机没有可用的 Whisper ASR；请填写参考文本。")
    script = (
        "import sys\n"
        "import mlx_whisper\n"
        "r = mlx_whisper.transcribe(sys.argv[1], "
        "path_or_hf_repo=sys.argv[2], language='zh')\n"
        "print(r['text'].strip())\n"
    )
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    if ASR_SITE:
        env["PYTHONPATH"] = ASR_SITE
    proc = subprocess.run(
        [str(ASR_PY), "-c", script, str(wav_path), str(ASR_MODEL)],
        capture_output=True, text=True, env=env, timeout=300
    )
    if proc.returncode != 0:
        raise RuntimeError("转写失败：" + (proc.stderr or proc.stdout)[-500:])
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

    from mlx_audio.tts.utils import load_model
    from mlx_audio.tts.generate import generate_audio

    with tempfile.TemporaryDirectory(prefix="spp-qwen-") as tmp:
        tmpdir = Path(tmp)
        wav = tmpdir / "reference.wav"
        proc = subprocess.run(
            ["/usr/bin/afconvert", str(ref), str(wav),
             "-f", "WAVE", "-d", "LEI16@24000", "-c", "1"],
            capture_output=True, text=True
        )
        if proc.returncode != 0 or not wav.is_file():
            raise RuntimeError("参考音频转换失败：" + (proc.stderr or proc.stdout)[-500:])

        ref_text = args.ref_text.strip() or transcribe(wav)
        model = load_model(str(QWEN_MODEL))
        prefix = "spp_" + time.strftime("%Y%m%d_%H%M%S")
        kwargs = dict(
            model=model,
            text=args.text,
            ref_audio=str(wav),
            ref_text=ref_text,
            lang_code="Chinese",
            output_path=str(out_dir),
            file_prefix=prefix,
            temperature=args.temperature,
            top_p=args.top_p,
            repetition_penalty=args.repetition_penalty,
            verbose=False,
        )
        if args.top_k > 0:
            kwargs["top_k"] = args.top_k
        generate_audio(**kwargs)

        candidates = sorted(out_dir.glob(prefix + "*.wav"))
        if not candidates:
            raise RuntimeError("Qwen 生成完成但没有找到输出 WAV")

        stem = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", args.text)[:10] or "clone"
        final = unique_path(out_dir / f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}.wav")
        candidates[-1].replace(final)
        import soundfile as sf
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
