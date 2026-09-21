# Third-party notices

SPP Audio Studio's own source code is released under the MIT License. Models, libraries and bundled/optional components keep their own upstream licenses.

## Qwen3-TTS / MLX model

Default voice-cloning model:
- `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit`
- License: Apache-2.0
- Upstream: Hugging Face MLX Community / Qwen3-TTS

The model is **not included in this repository**. The app can download it on demand.

## Mel-Deux

Default vocal-separation model:
- `becruily/mel-band-roformer-deux`
- License: CC BY-NC 4.0

Important: this model's license is **non-commercial**. The app source being MIT does not change the model's license. If you want to use or redistribute SPP Audio Studio commercially, review/replace the Mel-Deux model first.

The model is **not included in this repository**. The app can download it on demand.

## audio-separator

SPP Audio Studio uses the `audio-separator` Python project as the separation runtime/CLI layer.
- License: MIT
- Upstream: `nomadkaraoke/python-audio-separator`

## MLX Audio

Qwen local inference uses `mlx-audio`.
- License: MIT
- Upstream: `Blaizzy/mlx-audio`

## NCM conversion

The native Swift NCM converter in `ncm/main.swift` is a local reimplementation of the commonly documented NCM container/decryption flow and was validated against existing ncmdump implementations.

Related/open-source references include:
- `taurusxin/ncmdump` — MIT
- `ww-rm/ncmdump-py` — MIT

The converter is intended for files the user is legally entitled to process. SPP Audio Studio does not download music or bypass account access controls.

## Apple frameworks

The macOS UI and local playback use Apple system frameworks including SwiftUI, AppKit, AVFoundation and UniformTypeIdentifiers.
