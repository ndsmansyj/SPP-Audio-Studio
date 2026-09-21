# Changelog

## 0.2.0-rc6 — 2026-09-21

Clean-Mac runtime release candidate.

### Added
- Bundled relocatable Python 3.11 Core; the app no longer calls macOS `/usr/bin/python3`.
- In-app Qwen / MLX Runtime installer using pinned, prebuilt Apple Silicon wheels.
- In-app Mel Runtime installer using the MDXC dependency path and bundled FFmpeg.
- Diagnostic logs under `~/Library/Logs/SPP Audio Studio/` with rotation and path redaction.
- “复制诊断报告 / 复制最近错误 / 打开日志文件夹”.
- Copy-error action directly on failed tasks.
- DMG now contains an Applications drag target.
- Final pixel-art SPP Audio Studio app icon.

### Fixed
- Clean Macs no longer trigger Xcode Command Line Tools just to start the Worker.
- Runtime installation avoids source compilation on clean Macs.
- Qwen runtime dependency versions are pinned to the known-good local environment.

### Clean-Mac test focus
- Apple Silicon + macOS 14+.
- First Runtime installation on machines with no previous MLX/audio-separator environment.
- Network failure/retry behavior on model and Runtime downloads.

## 0.2.0-rc5 — 2026-09-21

Public group-test release with the pelican app icon and generic public-facing format-conversion wording.

### Added
- Native macOS SwiftUI shell for Apple Silicon.
- Local special-format conversion to original MP3/FLAC.
- Mel-Deux vocal/instrumental separation.
- Separation modes: instrumental only, vocals only, or both.
- Qwen3-TTS local voice cloning.
- Persistent voice templates and a bundled default “盼盼 · 自然口播” demo template.
- Remembered output folders.
- “Save beside source file” workflow for conversion/separation.
- Finder drag & drop.
- Local audio preview / pause.
- Model manager with HF Mirror / Hugging Face source selection.
- Local-model linking for users who already have models installed.
- Source files are treated as read-only; outputs never silently overwrite originals.

### Fixed
- Qwen clone no longer requires ffmpeg/ffprobe for reference conversion.
- Environment checker no longer misreports every dependency failure as “worker missing”.
- Drag/drop file-type filtering.
- Custom output directory fallback bugs.

### Known limitations
- Apple Silicon only.
- macOS 14+ is the current tested target.
- App is not signed with an Apple Developer ID; first launch may require manual approval in Privacy & Security.
- Model downloading is implemented, but a completely clean Mac may still need runtime work for Qwen/Mel. This RC is intentionally being released for clean-machine testing.
- Automatic in-app updating is planned but not yet enabled.
