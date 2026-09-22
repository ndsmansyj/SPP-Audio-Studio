#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$script_dir/publish.ps1")" "$@"
