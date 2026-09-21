# Changelog

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
