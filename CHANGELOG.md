# Changelog

This file tracks public, user-facing changes. Internal RC test notes and handoff documents are intentionally kept out of the public project tree.

## 1.0.2 — macOS 2026-10-19 / Windows 2026-10-20

### macOS
- Recover valid completed model `.part` files, discard oversized or corrupt partials, and retry failed resumed downloads from zero.
- Allow Qwen to fall back among ModelScope, Hugging Face mirror and revision-pinned official source without bypassing size and SHA-256 verification.
- Add manual fixed-version model links and Finder model storage location controls.

### Windows
- Isolate resumable model downloads per source, detect and recover corrupt/oversized partial downloads, and validate major weights with SHA-256.
- Add manual fixed-revision model links, model storage directory and Runtime directory controls.
- Changing a download source no longer accidentally cancels an in-progress model download.
- Place Python/CUDA Runtime install staging, pip cache and temporary unpack paths beneath Runtime so users who redirect Runtime to another drive move these temporary files as well.
- Include low-space diagnostics and regression tests for resumed downloads and Runtime staging.

### Distribution
- Publish Windows x64 portable ZIP and macOS Apple Silicon DMG with matching v1.0.2 user-facing versions.
- Windows portable package rebuilds WinUI and the frozen Windows Worker against the verified v1.0.1 support files and Python Core; the full archive and SHA-256 are checked before publication.
- Preserve external/user-managed models, Runtime, templates, histories and output data during app replacement.

## 1.0.1 — 2026-10-15

### Windows
- Fixed the crash when playing completed voice-clone output in the WinUI task/history panel. Audio stays inside the application and supports play/pause and position seeking.
- Fixed Whisper CUDA 12 cuBLAS dependency lookup in the separate ASR process launched for temporary voice reference transcription. Finds the existing Qwen/Mel PyTorch DLLs automatically; no machine-wide PATH or CUDA Toolkit setup is required when the managed runtime is complete.
- Improved reference-transcription error messages, UTF-8 subprocess output, and the option to supply spoken reference text manually when ASR is unavailable.

### macOS
- Added playback for saved and bundled voice templates alongside temporary reference audio.
- Unified the selected reference audio preview in the “常用人声” toolbar; changing the selected reference stops the previous preview.
- Retained existing generation, queued tasks, data, model/runtime installations and history.

### Distribution
- Refreshed the Windows portable archive and the Apple Silicon DMG, preserving user data and model caches during upgrade.
- Includes the playback Fix 1 and Whisper CUDA Fix 2 that were previously published as Windows previews, plus the macOS build 113 reference-preview changes.

## 1.0.0 — 2026-09-22

### Project
- Unified the public repository around both **Windows** and **macOS**.
- Updated the main README with platform-specific download, build, and API entry points.
- Kept source audio read-only by default; generated outputs use separate files instead of silently overwriting originals.

### Windows
- Added the native WinUI 3 desktop application and portable x64 packaging.
- Added local audio format conversion, Mel-Deux vocal/instrumental separation, Qwen3-TTS voice cloning, reusable voice templates, inline playback, and task history.
- Added in-app model/runtime management with automatic source selection for ModelScope, Hugging Face mirrors, domestic PyPI mirrors, and PyTorch CUDA sources.
- Added the opt-in loopback Agent API with Bearer authentication, capability discovery, asynchronous task IDs, task status queries, and compatibility endpoints.
- Added packaged Python Core so end users do not need to install a system Python runtime.
- Added release manifest and SHA-256 integrity files for portable builds.

### macOS
- Promoted the Apple Silicon build to **1.0.0** for macOS 14+.
- Aligned format conversion, Mel-Deux separation, and Qwen3-TTS voice cloning around one serial task queue with a consistent right-side task/history panel.
- Added queued voice-clone jobs, safe deletion for user-created voice templates, protected bundled templates, and simplified clone output filenames.
- Added robust M4A / AAC preprocessing for Mel-Deux separation while keeping original source audio read-only.
- Qwen3-TTS model downloads on macOS now use ModelScope directly; other model/runtime sources remain selectable in the app.
- Switched the desktop UI to a native light appearance with a consistent typography, card, input, hover, and task-panel system.
- Hardened the opt-in macOS local API by serializing worker execution to avoid concurrent AI jobs competing for local resources.
- Retains local playback, model/runtime management, diagnostic reporting, and bundled Python Core so normal users do not need Xcode, Homebrew, or a system Python installation.

### Distribution notes
- Windows and macOS binaries are published through GitHub Releases rather than GitHub Packages.
- Third-party model and asset licensing remains documented in `THIRD_PARTY_NOTICES.md`, `VOICE_ASSET_LICENSE.md`, and `BRANDING_ASSET_LICENSE.md`.
