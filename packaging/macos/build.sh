#!/bin/bash
# Build Sleeve.app and Sleeve.dmg. Optional signing and notarization:
#   SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   NOTARY_PROFILE="sleeve-notary"   (created with: xcrun notarytool store-credentials)
set -euo pipefail
cd "$(dirname "$0")/../.."

python -m pip install -e ".[package]"
python packaging/fetch_ffmpeg.py
python packaging/make_icons.py
pyinstaller packaging/sleeve.spec --noconfirm --clean

APP="dist/Sleeve.app"
VERSION=$(python -c "import sleeve; print(sleeve.__version__)")
DMG="dist/Sleeve-${VERSION}.dmg"

if [[ -n "${SIGN_IDENTITY:-}" ]]; then
  codesign --force --options runtime --timestamp \
    --entitlements packaging/macos/entitlements.plist \
    --sign "$SIGN_IDENTITY" "$APP/Contents/Frameworks/ffmpeg/ffmpeg"
  codesign --force --deep --options runtime --timestamp \
    --entitlements packaging/macos/entitlements.plist \
    --sign "$SIGN_IDENTITY" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
fi

STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "Sleeve" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

if [[ -n "${SIGN_IDENTITY:-}" ]]; then
  codesign --sign "$SIGN_IDENTITY" --timestamp "$DMG"
fi
if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
fi
echo "Built $DMG"
