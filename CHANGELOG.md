# Changelog

This file tracks public, user-facing changes. Internal RC test notes and handoff documents are intentionally kept out of the public project tree.

## Unreleased

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
- Apple Silicon desktop application remains available for macOS 14+.
- Includes local format conversion, Mel-Deux separation, Qwen3-TTS voice cloning, local playback, model/runtime management, and diagnostic reporting.
- Bundles its own Python Core so normal users do not need Xcode, Homebrew, or a system Python installation.

### Distribution notes
- Windows and macOS binaries are published through GitHub Releases rather than GitHub Packages.
- Third-party model and asset licensing remains documented in `THIRD_PARTY_NOTICES.md`, `VOICE_ASSET_LICENSE.md`, and `BRANDING_ASSET_LICENSE.md`.
