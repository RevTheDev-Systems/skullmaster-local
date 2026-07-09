#!/bin/zsh
# Build and install the "SkullMaster iQ" macOS app bundle into ~/Applications
# so it appears in Launchpad / Spotlight / the Applications menu. Idempotent —
# re-run after changing the icon or launcher. Requires the repo at ~/notebooklm-local.
set -u

REPO="${0:A:h:h}"                       # repo root (parent of scripts/)
APP="$HOME/Applications/SkullMaster iQ.app"

echo "Installing app bundle → $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp "$REPO/assets/icon.icns" "$APP/Contents/Resources/icon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>          <string>SkullMaster iQ</string>
  <key>CFBundleDisplayName</key>   <string>SkullMaster iQ</string>
  <key>CFBundleIdentifier</key>    <string>com.skullmaster.iq</string>
  <key>CFBundleVersion</key>       <string>1.0.0</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>CFBundlePackageType</key>   <string>APPL</string>
  <key>CFBundleExecutable</key>    <string>SkullMaster iQ</string>
  <key>CFBundleIconFile</key>      <string>icon</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/SkullMaster iQ" <<'LAUNCH'
#!/bin/zsh
exec "$HOME/notebooklm-local/scripts/launcher.sh"
LAUNCH
chmod +x "$APP/Contents/MacOS/SkullMaster iQ"
chmod +x "$REPO/scripts/launcher.sh"

# Locally built, but strip quarantine just in case, then register + refresh icon.
xattr -dr com.apple.quarantine "$APP" 2>/dev/null
LSREG="/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister"
"$LSREG" -f "$APP"
touch "$APP"

echo "Done. Find 'SkullMaster iQ' in Launchpad / Spotlight / ~/Applications."
