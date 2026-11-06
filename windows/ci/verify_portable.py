"""Verify the exact portable payload before it can become a release artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import tempfile
import zipfile

REQUIRED = (
    "SPP Audio Studio.exe", "SPP Agent API.cmd",
    "app/SPPAudioStudio.Windows.exe", "app/Microsoft.UI.Xaml.dll",
    "app/Assets/pixel_icon.ico", "app/Assets/pixel_icon.png",
    "worker/SPPWorker/SPPWorker.exe", "worker/bin/format_converter.exe",
    "worker/spp_worker.py", "worker/qwen_bridge.py", "worker/mel_bridge.py",
    "worker/local_api.py", "worker/media_tools.py", "worker/runtime_manifest.py", "python-core/python.exe", "python-core/python3.dll",
    "python-core/python311.dll", "python-core/Lib/site-packages/pip/__init__.py",
)
METADATA = {"release-manifest.json", "SHA256SUMS"}


def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_relative(value: str) -> str:
    path = pathlib.PurePosixPath(value)
    if (not value or "\\" in value or ":" in value or path.is_absolute()
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError(f"Unsafe portable path: {value!r}")
    return value


def verify_root(root: pathlib.Path, version: str | None = None, commit: str | None = None) -> dict:
    if not root.is_dir():
        raise ValueError(f"Portable root missing: {root}")
    paths = list(root.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("Portable payload must not contain symlinks")
    files = {path.relative_to(root).as_posix(): path for path in paths if path.is_file()}
    if len({name.casefold() for name in files}) != len(files):
        raise ValueError("Case-insensitive duplicate portable filenames")
    for name in files:
        safe_relative(name)
    # Windows component lookup is case-insensitive; the manifest and checksum
    # lists below still bind each exact archived spelling to its content.
    windows_names = {name.casefold() for name in files}
    missing = {name for name in REQUIRED if name.casefold() not in windows_names}
    missing.update(METADATA - files.keys())
    if missing:
        raise ValueError(f"Required portable components missing: {sorted(missing)}")
    if not any(name.startswith("app/") and name.endswith(".pri") for name in files):
        raise ValueError("WinUI PRI resources missing")
    if not any(name.startswith("worker/default_voice/") for name in files):
        raise ValueError("Default voice assets missing")
    if not any(re.fullmatch(r"python-core/Lib/site-packages/pip-[^/]+\.dist-info/METADATA", name) for name in files):
        raise ValueError("Bundled pip distribution metadata missing")
    for name, path in files.items():
        if path.suffix.lower() in (".exe", ".dll", ".ico", ".pri") and path.stat().st_size == 0:
            raise ValueError(f"Empty binary/resource: {name}")
    manifest = json.loads(files["release-manifest.json"].read_text(encoding="utf-8-sig"))
    if manifest.get("schemaVersion") != 2 or manifest.get("sourceBuild") is not True or manifest.get("portable") is not True:
        raise ValueError("A full source-built schema 2 portable manifest is required")
    if manifest.get("runtimeIdentifier") != "win-x64":
        raise ValueError("Unexpected portable runtime")
    if version is not None and manifest.get("version") != version:
        raise ValueError(f"Version mismatch: {manifest.get('version')!r} != {version!r}")
    if commit is not None and manifest.get("commit") != commit:
        raise ValueError(f"Source commit mismatch: {manifest.get('commit')!r} != {commit!r}")
    declared = {}
    for entry in manifest["files"]:
        name = safe_relative(entry["path"])
        if name in declared:
            raise ValueError(f"Duplicate manifest path: {name}")
        declared[name] = entry
    if set(declared) != set(files) - METADATA:
        raise ValueError(f"Manifest coverage mismatch: {sorted(set(declared) ^ (set(files) - METADATA))}")
    hashes = {name: digest(path) for name, path in files.items() if name != "SHA256SUMS"}
    for name, entry in declared.items():
        if entry["size"] != files[name].stat().st_size or entry["sha256"] != hashes[name]:
            raise ValueError(f"Manifest size/hash mismatch: {name}")
    sums = {}
    for line in files["SHA256SUMS"].read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) \*(.+)", line)
        if not match:
            raise ValueError(f"Invalid SHA256SUMS line: {line!r}")
        name = safe_relative(match[2])
        if name in sums:
            raise ValueError(f"Duplicate checksum path: {name}")
        sums[name] = match[1]
    if sums != hashes:
        raise ValueError("SHA256SUMS coverage/hash mismatch")
    return {"ok": True, "version": manifest["version"], "commit": manifest["commit"], "filesVerified": len(files)}


def verify_zip(archive: pathlib.Path, version: str | None = None, commit: str | None = None,
               extract_to: pathlib.Path | None = None, sha256_file: pathlib.Path | None = None) -> dict:
    if sha256_file is not None:
        line = sha256_file.read_text(encoding="utf-8-sig").strip()
        if line != f"{digest(archive)} *{archive.name}":
            raise ValueError("Archive SHA-256 sidecar mismatch")
    with tempfile.TemporaryDirectory(prefix="spp-verify-") as temp:
        target = extract_to if extract_to is not None else pathlib.Path(temp)
        if target.exists() and any(target.iterdir()):
            raise ValueError("Extraction target must be empty")
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as source:
            names = set()
            for info in source.infolist():
                name = safe_relative(info.filename.rstrip("/"))
                if not name.startswith("SPP Audio Studio/") and name != "SPP Audio Studio":
                    raise ValueError("ZIP must contain exactly one SPP Audio Studio root")
                if name.casefold() in names:
                    raise ValueError(f"Duplicate ZIP entry: {name}")
                names.add(name.casefold())
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"ZIP symlink prohibited: {name}")
            source.extractall(target)
        return verify_root(target / "SPP Audio Studio", version, commit)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--root", type=pathlib.Path)
    choice.add_argument("--zip", type=pathlib.Path)
    parser.add_argument("--version")
    parser.add_argument("--commit")
    parser.add_argument("--extract-to", type=pathlib.Path)
    parser.add_argument("--sha256-file", type=pathlib.Path)
    args = parser.parse_args()
    result = (verify_root(args.root, args.version, args.commit) if args.root is not None
              else verify_zip(args.zip, args.version, args.commit, args.extract_to, args.sha256_file))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
