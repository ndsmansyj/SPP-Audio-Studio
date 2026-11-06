"""Executable media tools shared by diagnostics and every AI subprocess.

File existence is not an execution test (Windows app aliases can be reparse
points or fail with WinError 193). Never advertise a candidate before running it.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PROBE_TIMEOUT = 5


def probe_ffmpeg(path: Path, timeout: float = PROBE_TIMEOUT) -> dict:
    try:
        proc = subprocess.run(
            [str(path), "-version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        version = next((line.strip() for line in output.splitlines()
                        if line.lower().startswith("ffmpeg version ")), "")
        if proc.returncode == 0 and version:
            return {"ready": True, "path": str(path), "version": version, "reason": ""}
        reason = f"exit {proc.returncode}: {output.strip()[-300:]}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        reason = str(exc)
    return {"ready": False, "path": str(path), "version": "", "reason": reason}


def ffmpeg_candidates(runtime_dirs: list[Path], sites: list[Path],
                      explicit: str | None = None) -> list[Path]:
    candidates = [root / "ffmpeg" / "ffmpeg.exe" for root in runtime_dirs]
    for site in sites:
        candidates.extend(sorted((site / "imageio_ffmpeg" / "binaries").glob("ffmpeg*.exe")))
    if explicit:
        candidates.append(Path(explicit))
    system = shutil.which("ffmpeg")
    if system:
        candidates.append(Path(system))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def install_ffmpeg(source: Path, destination: Path) -> dict:
    """Publish a validated, canonically named executable for PATH consumers."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".ffmpeg-", suffix=".exe", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        temporary.chmod(0o755)
        report = probe_ffmpeg(temporary)
        if not report["ready"]:
            raise RuntimeError("FFmpeg 验证失败：" + report["reason"])
        os.replace(temporary, destination)
        return {**report, "path": str(destination)}
    finally:
        temporary.unlink(missing_ok=True)


def resolve_ffmpeg(runtime_dirs: list[Path], sites: list[Path],
                   explicit: str | None = None, canonical_dir: Path | None = None) -> dict:
    failures: list[dict] = []
    for candidate in ffmpeg_candidates(runtime_dirs, sites, explicit):
        # Missing candidates are normal before installation. A reparse point
        # which is a file must still be executed, even if its size is zero.
        if not candidate.is_file():
            continue
        report = probe_ffmpeg(candidate)
        if report["ready"]:
            if canonical_dir is not None and candidate.name.lower() != "ffmpeg.exe":
                try:
                    report = install_ffmpeg(candidate, canonical_dir / "ffmpeg.exe")
                except (OSError, RuntimeError) as exc:
                    failures.append({"path": str(candidate), "reason": str(exc)})
                    continue
            return {**report, "failures": failures}
        failures.append({"path": str(candidate), "reason": report["reason"]})
    return {"ready": False, "path": "", "version": "", "failures": failures,
            "reason": "没有可执行的 FFmpeg；请在模型与环境中修复运行环境。"}


def ffmpeg_environment(path: Path, base: dict | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env.update({"SPP_FFMPEG": str(path), "IMAGEIO_FFMPEG_EXE": str(path),
                "FFMPEG_BINARY": str(path),
                "PATH": str(path.parent) + os.pathsep + env.get("PATH", "")})
    return env
