#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.2.0-rc5}"
BUILD_NUMBER="${BUILD_NUMBER:-25}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BUILD="$ROOT/build/$STAMP"
APP="$BUILD/SPP Audio Studio.app"

mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources/worker"
mkdir -p "$APP/Contents/Resources/bin"
mkdir -p "$APP/Contents/Resources/default_voice"

# Build the macOS app icon from the bundled pelican SVG.
ICON_RENDER="$BUILD/icon-render"
ICONSET="$BUILD/AppIcon.iconset"
mkdir -p "$ICON_RENDER" "$ICONSET"
qlmanage -t -s 1024 -o "$ICON_RENDER" "$ROOT/assets/icon/pelican.svg" >/dev/null 2>&1
ICON_PNG="$ICON_RENDER/pelican.svg.png"

sips -z 16 16 "$ICON_PNG" --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32 "$ICON_PNG" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$ICON_PNG" --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64 "$ICON_PNG" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$ICON_PNG" --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256 "$ICON_PNG" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$ICON_PNG" --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512 "$ICON_PNG" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$ICON_PNG" --out "$ICONSET/icon_512x512.png" >/dev/null
cp "$ICON_PNG" "$ICONSET/icon_512x512@2x.png"
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

swiftc -swift-version 5 -parse-as-library "$ROOT/app/SPPAudioStudio.swift" \
  -framework SwiftUI -framework AppKit -framework AVFoundation -framework UniformTypeIdentifiers \
  -o "$APP/Contents/MacOS/SPPAudioStudio"

cp "$ROOT/worker/spp_worker.py" "$ROOT/worker/qwen_bridge.py" "$APP/Contents/Resources/worker/"
cp "$ROOT/assets/default_voice/reference.wav" "$ROOT/assets/default_voice/reference.txt" "$APP/Contents/Resources/default_voice/"

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

DMG="$BUILD/SPP-Audio-Studio-$VERSION.dmg"
hdiutil create -volname "SPP Audio Studio" -srcfolder "$APP" -format UDZO "$DMG"

echo "$APP"
echo "$DMG"
