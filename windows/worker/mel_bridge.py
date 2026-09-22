#!/usr/bin/env python3
from __future__ import annotations

import torch

if not torch.cuda.is_available():
    raise RuntimeError("CUDA 不可用；AI 推理禁止回退到 CPU")

from audio_separator.utils.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
