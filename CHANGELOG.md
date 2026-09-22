# Changelog

This file tracks public, user-facing changes. Internal RC test notes and handoff documents are intentionally kept out of the public project tree.

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
