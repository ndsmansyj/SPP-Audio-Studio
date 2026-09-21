#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.2.0-rc4}"
BUILD_NUMBER="${BUILD_NUMBER:-23}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BUILD="$ROOT/build/$STAMP"
APP="$BUILD/SPP Audio Studio.app"

mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources/worker"
mkdir -p "$APP/Contents/Resources/bin"
mkdir -p "$APP/Contents/Resources/default_voice"

swiftc -swift-version 5 -parse-as-library "$ROOT/app/SPPAudioStudio.swift" \
  -framework SwiftUI -framework AppKit -framework AVFoundation -framework UniformTypeIdentifiers \
  -o "$APP/Contents/MacOS/SPPAudioStudio"

cp "$ROOT/worker/spp_worker.py" "$ROOT/worker/qwen_bridge.py" "$APP/Contents/Resources/worker/"
cp "$ROOT/assets/default_voice/reference.wav" "$ROOT/assets/default_voice/reference.txt" "$APP/Contents/Resources/default_voice/"

swiftc -O "$ROOT/ncm/main.swift" \
  -framework AppKit -framework UniformTypeIdentifiers \
  -o "$APP/Contents/Resources/bin/ncm_converter"

chmod 755 "$APP/Contents/MacOS/SPPAudioStudio" "$APP/Contents/Resources/bin/ncm_converter"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleDisplayName</key><string>SPP Audio Studio</string>
<key>CFBundleExecutable</key><string>SPPAudioStudio</string>
<key>CFBundleIdentifier</key><string>com.spp.audio-studio</string>
<key>CFBundleName</key><string>SPP Audio Studio</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>$VERSION</string>
<key>CFBundleVersion</key><string>$BUILD_NUMBER</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

codesign --force --deep --sign - "$APP"

DMG="$BUILD/SPP-Audio-Studio-$VERSION.dmg"
hdiutil create -volname "SPP Audio Studio" -srcfolder "$APP" -format UDZO "$DMG"

echo "$APP"
echo "$DMG"
