#!/usr/bin/env bash
# Builds Void.app and Void.dmg from this checkout, on a Mac. The app carries its own Python (a standalone CPython 3.12
# that uv fetches) with Void's libraries installed in it, so it needs neither the system python nor Homebrew.
#   bash packaging/macos/build.sh [output folder]      (default: build/macos)
# Signed ad-hoc (no developer ID yet). Each user's voice + speech models are downloaded on first run, as on Linux.
set -euo pipefail
cd "$(dirname "$0")/../.."
VER="$(tr -d '[:space:]' < app/VERSION)"
OUT="$(mkdir -p "${1:-build/macos}" && cd "${1:-build/macos}" && pwd)"
JOBS=$(( $(sysctl -n hw.ncpu) / 2 ))
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
APPB="$OUT/Void.app"; RES="$APPB/Contents/Resources"; PY="$RES/python/bin/python3"
rm -rf "$APPB" "$OUT/Void.dmg"
mkdir -p "$APPB/Contents/MacOS" "$RES"

# his Python: python-build-standalone, which runs from wherever it's copied to
uv python install --install-dir "$WORK/py" 3.12 >/dev/null
cp -R "$(find "$WORK/py" -maxdepth 1 -name 'cpython-3.12*' -type d | head -n 1)" "$RES/python"
rm -f "$RES"/python/lib/python3.12/EXTERNALLY-MANAGED          # it's Void's own interpreter, not a system one
# the same libraries install.sh puts in ~/.local/share/void/venv on a Mac
uv pip install -q --python "$PY" faster-whisper pillow PySide6 sounddevice piper-tts kokoro-onnx

rsync -a --exclude __pycache__ app/ "$RES/app/"
install -m644 LICENSE "$RES/LICENSE"
install -m755 packaging/macos/launcher "$APPB/Contents/MacOS/Void"
sed "s/@VERSION@/$VER/g" packaging/macos/Info.plist > "$APPB/Contents/Info.plist"
mkdir -p "$WORK/Void.iconset"
for s in 16 32 128 256 512; do
  sips -z $s $s app/static/icon.png --out "$WORK/Void.iconset/icon_${s}x${s}.png" >/dev/null
  [ $s = 512 ] || sips -z $((s * 2)) $((s * 2)) app/static/icon.png --out "$WORK/Void.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$WORK/Void.iconset" -o "$RES/Void.icns"

# only what Void runs: the Qt it imports (and what that links), arm64 only, no pip / Tk / headers / tests / tools
"$PY" -I packaging/macos/slim.py "$RES"

# bytecode now, so nothing is ever written into the signed bundle (the launcher also turns writing it off)
find "$RES" -name __pycache__ -type d -prune -exec rm -rf {} +
"$PY" -m compileall -q -j "$JOBS" --invalidation-mode unchecked-hash "$RES/python/lib" "$RES/app" >/dev/null || true

# ad-hoc signature: every Mach-O inside first, then the bundle around them
find "$RES" -type f -print0 | xargs -0 file --no-pad | awk -F': ' '/: Mach-O/ && !/\(for architecture / {print $1}' | tr '\n' '\0' \
  | xargs -0 -n 50 -P "$JOBS" codesign --force --sign - --timestamp=none 2>"$WORK/sign.log" \
  || { grep -v 'replacing existing signature' "$WORK/sign.log" >&2; exit 1; }
codesign --force --sign - --timestamp=none "$APPB"

# the disk image: the app next to a link to Applications, to drag it onto
mkdir -p "$WORK/dmg"
cp -cR "$APPB" "$WORK/dmg/Void.app"
ln -s /Applications "$WORK/dmg/Applications"
hdiutil create -quiet -volname Void -srcfolder "$WORK/dmg" -fs HFS+ -format UDZO -ov "$OUT/Void.dmg"

# checks
plutil -lint "$APPB/Contents/Info.plist"
codesign --verify --deep --strict "$APPB" && echo "$APPB: signature valid"
hdiutil verify -quiet "$OUT/Void.dmg" && echo "$OUT/Void.dmg: image valid"
HOME="$WORK/home" XDG_DATA_HOME="$WORK/home/data" XDG_CONFIG_HOME="$WORK/home/config" XDG_CACHE_HOME="$WORK/home/cache" \
  PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PATH=/usr/bin:/bin \
  "$PY" -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import server, window_win, faster_whisper, piper, kokoro_onnx, sounddevice
print("bundled python", sys.version.split()[0], "imports server, window_win and the speech libraries")' "$RES/app"
HOME="$WORK/home" XDG_DATA_HOME="$WORK/home/data" XDG_CONFIG_HOME="$WORK/home/config" XDG_CACHE_HOME="$WORK/home/cache" \
  PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PATH=/usr/bin:/bin \
  "$PY" -I packaging/macos/qtcheck.py "$RES/app"
codesign --verify --deep --strict "$APPB"                       # still sealed after running it
echo "Void $VER: $APPB ($(du -sh "$APPB" | cut -f1)), $OUT/Void.dmg ($(du -sh "$OUT/Void.dmg" | cut -f1))"
