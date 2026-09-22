#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-1.0.0}"
BUILD_NUMBER="${BUILD_NUMBER:-100}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BUILD="$ROOT/build/$STAMP"
APP="$BUILD/SPP Audio Studio.app"
PYTHON_CORE_SOURCE="${SPP_PYTHON_CORE_SOURCE:-$HOME/.local/share/uv/python/cpython-3.11.15-macos-aarch64-none}"

if [ ! -x "$PYTHON_CORE_SOURCE/bin/python3.11" ]; then
  echo "Missing relocatable Python 3.11 core: $PYTHON_CORE_SOURCE" >&2
  echo "Set SPP_PYTHON_CORE_SOURCE to a relocatable Apple Silicon Python 3.11 directory." >&2
  exit 2
fi

mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources/worker"
mkdir -p "$APP/Contents/Resources/bin"
mkdir -p "$APP/Contents/Resources/default_voice"
mkdir -p "$APP/Contents/Resources/runtime"

# Use the prebuilt pixel-art iconset/ICNS. It is generated with nearest-neighbour
# scaling so the selected pixel-art look stays crisp in Finder and Dock.
cp "$ROOT/assets/icon/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

swiftc -swift-version 5 -parse-as-library "$ROOT/app/SPPAudioStudio.swift" \
  -framework SwiftUI -framework AppKit -framework AVFoundation -framework UniformTypeIdentifiers \
  -o "$APP/Contents/MacOS/SPPAudioStudio"

cp "$ROOT/worker/spp_worker.py" "$ROOT/worker/qwen_bridge.py" "$ROOT/worker/mel_bridge.py" "$ROOT/worker/local_api.py" "$APP/Contents/Resources/worker/"
cp "$ROOT/assets/default_voice/reference.wav" "$ROOT/assets/default_voice/reference.txt" "$APP/Contents/Resources/default_voice/"

# Bundle a relocatable Python core so clean Macs never need /usr/bin/python3 or Xcode Command Line Tools.
ditto "$PYTHON_CORE_SOURCE" "$APP/Contents/Resources/runtime/python"
chmod 755 "$APP/Contents/Resources/runtime/python/bin/python3.11"

swiftc -O "$ROOT/format_converter/main.swift" \
  -framework AppKit -framework UniformTypeIdentifiers \
  -o "$APP/Contents/Resources/bin/format_converter"

chmod 755 "$APP/Contents/MacOS/SPPAudioStudio" "$APP/Contents/Resources/bin/format_converter"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleDisplayName</key><string>SPP Audio Studio</string>
<key>CFBundleExecutable</key><string>SPPAudioStudio</string>
<key>CFBundleIdentifier</key><string>com.spp.audio-studio</string>
<key>CFBundleName</key><string>SPP Audio Studio</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundleShortVersionString</key><string>$VERSION</string>
<key>CFBundleVersion</key><string>$BUILD_NUMBER</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

codesign --force --deep --sign - "$APP"

DMG_ROOT="$BUILD/dmg-root"
mkdir -p "$DMG_ROOT"
ditto "$APP" "$DMG_ROOT/SPP Audio Studio.app"
ln -s /Applications "$DMG_ROOT/Applications"

DMG="$BUILD/SPP-Audio-Studio-$VERSION.dmg"
hdiutil create -volname "SPP Audio Studio" -srcfolder "$DMG_ROOT" -format UDZO "$DMG"

echo "$APP"
echo "$DMG"
