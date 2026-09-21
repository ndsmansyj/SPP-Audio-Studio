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

BUNDLED_NCM_BIN = Path(__file__).resolve().parent.parent / "bin" / "ncm_converter"
NCM_BIN = Path(os.environ.get("SPP_NCM_BIN", str(BUNDLED_NCM_BIN))).expanduser()
MEL_MODEL = os.environ.get("SPP_MEL_MODEL", "becruily_deux.ckpt")
BRIDGE = Path(__file__).with_name("qwen_bridge.py")
DEFAULT_VOICE_DIR = Path(os.environ.get("SPP_DEFAULT_VOICE_DIR", "")).expanduser()

QWEN_REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
QWEN_REVISION = "e7dd0585652209fa0d7783659aad4e8a324de11c"
MEL_REPO = "becruily/mel-band-roformer-deux"
MEL_REVISION = "2da74427d682a3df47a774378fc24d7a1a0cdaad"
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
        str(RUNTIME_DIR / "ffmpeg/ffmpeg"),
    ]
    for value in candidates:
        if value:
            p = Path(value)
            if p.is_file() and os.access(p, os.X_OK):
                return p
    return None


def cmd_doctor(_: argparse.Namespace) -> int:
    env = resolve_environment()
    separator_bin = Path(env["separator_bin"])
    mel_model_dir = Path(env["mel_model_dir"])
    qwen_python = Path(env["qwen_python"])
    qwen_model = Path(env["qwen_model"])
    asr_python = Path(env["asr_python"])
    asr_model = Path(env["asr_model"])
    checks = {
        "ncm_binary": NCM_BIN.is_file() and os.access(NCM_BIN, os.X_OK),
        "separator_binary": separator_bin.is_file() and os.access(separator_bin, os.X_OK),
        "mel_model": (mel_model_dir / MEL_MODEL).is_file(),
        "mel_config": (mel_model_dir / "config_deux_becruily.yaml").is_file(),
        "mel_ffmpeg": locate_ffmpeg() is not None,
        "qwen_python": qwen_python.is_file(),
        "qwen_model": qwen_model.is_dir(),
        "qwen_bridge": BRIDGE.is_file(),
        "asr_python": asr_python.is_file(),
        "asr_model": asr_model.exists(),
    }
    return emit({
        "ok": True,
        "ready": all(checks.values()),
        "checks": checks,
        "paths": {
            "data": str(DATA_DIR), "cache": str(CACHE_DIR), "voices": str(VOICE_DIR),
            "models": str(MODEL_DIR), "runtime": str(RUNTIME_DIR),
            "qwen_model": str(qwen_model), "mel_model_dir": str(mel_model_dir),
            "ffmpeg": str(locate_ffmpeg() or ""),
        },
    })
def cmd_convert(args: argparse.Namespace) -> int:
    ensure_dirs()
    src = Path(args.input).expanduser().resolve()
    if not src.is_file():
        return emit({"ok": False, "error": f"文件不存在：{src}"}, 2)
    if src.suffix.lower() != ".ncm":
        return emit({"ok": False, "error": "convert 当前只接受 .ncm"}, 2)
    if not NCM_BIN.is_file():
        return emit({"ok": False, "error": f"NCM 转换器不存在：{NCM_BIN}"}, 3)

    out_root = Path(args.output_dir).expanduser() if args.output_dir else src.parent / "SPP Audio"
    out_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ncm-", dir=CACHE_DIR))
    try:
        proc = run_capture([str(NCM_BIN), str(src), "--out", str(tmp)])
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
    separator_bin = Path(env["separator_bin"])
    mel_model_dir = Path(env["mel_model_dir"])
    if not separator_bin.is_file():
        return emit({"ok": False, "error": f"分离器不存在：{separator_bin}"}, 3)

    out_dir = Path(args.output_dir).expanduser() if args.output_dir else src.parent / "SPP Audio" / src.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="mel-", dir=CACHE_DIR))
    fmt = args.format.upper()
    ext = "." + fmt.lower().replace("mp3", "mp3")
    cmd = [
        str(separator_bin), "-m", MEL_MODEL,
        "--model_file_dir", str(mel_model_dir),
        "--output_dir", str(tmp), "--output_format", fmt,
    ]
    if fmt == "MP3":
        cmd += ["--output_bitrate", args.bitrate]
    cmd.append(str(src))
    try:
        ffmpeg = locate_ffmpeg()
        mel_path = "/usr/bin:/bin:/usr/sbin:/sbin"
        if ffmpeg:
            mel_path = str(ffmpeg.parent) + ":" + mel_path
        proc = run_capture(cmd, {"PATH": mel_path})
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
    qwen_python = Path(env_cfg["qwen_python"])
    qwen_model = Path(env_cfg["qwen_model"])
    if not qwen_python.is_file():
        raise RuntimeError(f"Qwen 运行环境不存在：{qwen_python}")
    if not qwen_model.is_dir():
        raise RuntimeError(f"Qwen 模型不存在：{qwen_model}")
    cmd = [
        str(qwen_python), str(BRIDGE),
        "--ref-audio", str(ref_audio), "--text", text,
        "--output-dir", str(output_dir),
        "--temperature", str(temperature), "--top-p", str(top_p),
        "--top-k", str(top_k), "--repetition-penalty", str(repetition_penalty),
    ]
    if ref_text:
        cmd += ["--ref-text", ref_text]
    proc = run_capture(cmd, {
        "SPP_QWEN_MODEL": str(qwen_model),
        "SPP_ASR_PY": str(env_cfg["asr_python"]),
        "SPP_ASR_MODEL": str(env_cfg["asr_model"]),
    })
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
    qwen_python, qwen_python_source = _pick_path(
        paths.get("qwen_python"),
        RUNTIME_DIR / "qwen/bin/python",
    )
    separator_bin, separator_source = _pick_path(
        paths.get("separator_bin"),
        RUNTIME_DIR / "mel/bin/audio-separator",
    )
    asr_python, asr_python_source = _pick_path(
        paths.get("asr_python"),
        RUNTIME_DIR / "asr/bin/python",
    )
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
        "separator_bin": separator_bin,
        "separator_source": separator_source,
        "asr_python": asr_python,
        "asr_python_source": asr_python_source,
        "asr_model": asr_model,
        "asr_model_source": asr_model_source,
        "download_source": settings.get("download_source", "auto"),
    }


def cmd_model_status(_: argparse.Namespace) -> int:
    env = resolve_environment()
    payload = {
        "ok": True,
        "download_source": env["download_source"],
        "models": {
            "qwen": {
                "repo": QWEN_REPO,
                "path": str(env["qwen_model"]),
                "source": env["qwen_model_source"],
                "installed": Path(env["qwen_model"]).is_dir(),
            },
            "mel_deux": {
                "repo": MEL_REPO,
                "path": str(env["mel_model_dir"]),
                "source": env["mel_model_source"],
                "installed": (Path(env["mel_model_dir"]) / MEL_MODEL).is_file(),
            },
        },
        "runtime": {
            "qwen_python": {
                "path": str(env["qwen_python"]),
                "source": env["qwen_python_source"],
                "installed": Path(env["qwen_python"]).is_file(),
            },
            "separator": {
                "path": str(env["separator_bin"]),
                "source": env["separator_source"],
                "installed": Path(env["separator_bin"]).is_file(),
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
    md.add_argument("model", choices=["qwen", "mel"])
    md.add_argument("--source", choices=["auto", "mirror", "official"])
    md.set_defaults(func=cmd_model_download)
    return p


def main() -> int:
    ensure_dirs()
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
