#!/usr/bin/env bash
# Void installer — Arch, Debian/Ubuntu and Fedora (and their relatives). Everything goes in your home folder;
# only the system packages below need sudo.
#   ./install.sh          everything (system packages, the app, your voice + speech models)
#   ./install.sh --user   just your part (voice + speech models) — what `void setup` runs after a system-wide
#                         `sudo ninja -C build install` (Gentoo & co.)
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_ONLY=0; [ "${1:-}" = --user ] && USER_ONLY=1
ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/void"
BIN="$HOME/.local/bin"
VOICE="${VOID_VOICE:-en_US-amy-medium}"   # any piper voice name, e.g. en_US-ryan-medium, en_GB-alba-medium
say(){ printf '\033[1;32m●\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m!\033[0m %s\n' "$*"; }
export PATH="$BIN:$PATH"
MAC=0; [ "$(uname -s)" = Darwin ] && MAC=1

# What each package is for:
#   uv: python env · espeak-ng: fallback voice · pulse/alsa tools: mic recording + playing his voice
#   PyGObject + WebKit2GTK 4.1 + GStreamer plugins: the see-through window and its sound effects
#   gtk-layer-shell: the overlay on Wayland · grim/slurp/wl-clipboard (Wayland) + maim/xclip (X11): screenshots & clipboard
#   libnotify: "done!" notifications · playerctl / brightnessctl: "pause the music", "dim the screen"
if [ $USER_ONLY = 1 ]; then
  PM=none; pkgs=()            # system-wide install: the system packages are yours to manage (see the README)
elif [ $MAC = 1 ]; then
  PM=none; pkgs=()            # macOS: his window, mic and speakers all come from PyPI (PySide6, sounddevice)
  command -v espeak-ng >/dev/null 2>&1 || say "macOS: no system packages needed (optional robot fallback voice: brew install espeak-ng)"
elif command -v pacman >/dev/null 2>&1; then
  PM=pacman
  pkgs=(uv espeak-ng curl libpulse alsa-utils python-gobject python-cairo webkit2gtk-4.1 gst-plugins-base gst-plugins-good
        grim slurp wl-clipboard maim xclip libnotify gtk-layer-shell playerctl brightnessctl)
  have(){ pacman -Qq "$1" >/dev/null 2>&1; }
  # --needed, no -y: never upgrade single packages (Arch doesn't support partial upgrades)
  get(){ sudo pacman -S --needed --noconfirm "$@"; }
elif command -v apt-get >/dev/null 2>&1; then
  PM=apt
  pkgs=(espeak-ng curl pulseaudio-utils alsa-utils python3-venv python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good grim slurp wl-clipboard maim xclip libnotify-bin
        gir1.2-gtklayershell-0.1 playerctl brightnessctl)
  have(){ dpkg -s "$1" >/dev/null 2>&1; }
  get(){ sudo apt-get install -y "$@"; }
elif command -v dnf >/dev/null 2>&1; then
  PM=dnf
  pkgs=(uv python3.12 espeak-ng curl pulseaudio-utils alsa-utils python3-gobject python3-cairo gtk3 webkit2gtk4.1
        gstreamer1-plugins-base gstreamer1-plugins-good grim slurp wl-clipboard maim xclip libnotify gtk-layer-shell
        playerctl brightnessctl)
  have(){ rpm -q "$1" >/dev/null 2>&1; }
  get(){ sudo dnf install -y "$@"; }
elif command -v emerge >/dev/null 2>&1; then
  PM=none
  warn "Gentoo: install the dependencies with emerge first (list in the README, \"Gentoo\" section), then either run"
  warn "  ./install.sh again, or do the system-wide install: ./configure && ninja -C build && sudo ninja -C build install"
else
  warn "No pacman, apt or dnf here. Install these yourself (names vary by distro), then run ./install.sh again:"
  warn "  uv, espeak-ng, PyGObject, WebKit2GTK 4.1 (GObject introspection), gtk-layer-shell, GStreamer base+good plugins,"
  warn "  pulseaudio/pipewire-pulse tools (parecord, paplay), grim, slurp, wl-clipboard, maim, xclip, libnotify, playerctl"
  PM=none
fi

if [ "$PM" != none ]; then
  say "System packages ($PM)…"
  missing=(); for p in "${pkgs[@]}"; do have "$p" || missing+=("$p"); done
  if [ ${#missing[@]} -gt 0 ]; then
    [ "$PM" = apt ] && sudo apt-get update -qq
    if ! get "${missing[@]}"; then
      warn "couldn't install them all at once — trying one by one (some extras may not exist on your distro)"
      for p in "${missing[@]}"; do get "$p" >/dev/null 2>&1 || warn "  skipped: $p"; done
    fi
  else say "all system packages already installed"; fi
fi

# the parts he can't work without
if [ $MAC = 0 ] && ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk","3.0"); gi.require_version("WebKit2","4.1")' 2>/dev/null; then
  warn "PyGObject + WebKit2GTK 4.1 aren't working — his window can't open. Install them and run ./install.sh again."
  [ "$PM" = pacman ] && warn "(if pacman complained, your system is probably behind — run 'sudo pacman -Syu' first)"
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python environment tool)…"
  mkdir -p "$BIN" "$ROOT"
  # from PyPI (where the speech libraries come from too), else from astral.sh
  if python3 -m venv "$ROOT/uv-tool" >/dev/null 2>&1 && "$ROOT/uv-tool/bin/pip" install -q uv; then
    ln -sf "$ROOT/uv-tool/bin/uv" "$BIN/uv"
  else
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$BIN" INSTALLER_NO_MODIFY_PATH=1 sh \
      || { warn "couldn't install uv — see https://docs.astral.sh/uv/ and run ./install.sh again"; exit 1; }
  fi
fi

mkdir -p "$ROOT/voices" "$BIN"; [ $MAC = 1 ] || mkdir -p "$HOME/.local/share/applications"
if [ $USER_ONLY = 1 ]; then
  APPD="$SRC/app"             # the system-wide copy (e.g. /usr/share/void/app)
else
  say "Copying app to $ROOT"
  rm -rf "$ROOT/app"; cp -r "$SRC/app" "$ROOT/app"
  APPD="$ROOT/app"
fi
rm -f "$ROOT/window.mode"     # re-pick the window render mode with the new defaults
# where this install came from, for "Update now": a git clone updates itself; a system-wide install (package/meson)
# is updated by the package manager
if [ $USER_ONLY = 1 ]; then echo package > "$ROOT/source"; else printf '%s\n' "$SRC" > "$ROOT/source"; fi

say "Python env (3.12 via uv — the speech libraries don't all support the newest Python yet)…"
# 3.12 from the system if it has it, else uv fetches one; last resort: the system's own python3
if [ ! -x "$ROOT/venv/bin/python" ]; then
  uv venv --python 3.12 "$ROOT/venv" 2>/dev/null \
    || { uv python install 3.12 && uv venv --python 3.12 "$ROOT/venv"; } \
    || { warn "no Python 3.12 available — trying your system Python (some speech libraries may not install)"
         uv venv --python "$(command -v python3)" "$ROOT/venv"; }
fi
uv pip install --python "$ROOT/venv/bin/python" -U faster-whisper pillow   # pillow: shrinks screenshots (fewer tokens)
# macOS: his window is Qt (as on Windows) and the mic / speakers go through PortAudio. No -U on PySide6: a working
# Qt is ~1 GB, so one that's already there is kept
[ $MAC = 1 ] && uv pip install --python "$ROOT/venv/bin/python" PySide6 && uv pip install --python "$ROOT/venv/bin/python" -U sounddevice
uv pip install --python "$ROOT/venv/bin/python" -U piper-tts \
  || warn "piper-tts failed to install — Void will use espeak-ng (robotic) instead"

# Kokoro: his main voice — far more natural than piper, still all local. Small int8 model (~120 MB with the voices).
# GitHub first; if that's blocked, kokoro_fetch.py builds the same files from Hugging Face.
KDIR="$ROOT/voices/kokoro"
if uv pip install --python "$ROOT/venv/bin/python" -U kokoro-onnx; then
  say "Kokoro voice…"
  "$ROOT/venv/bin/python" "$APPD/kokoro_fetch.py" "$KDIR" \
    || { rm -f "$KDIR"/*.part; warn "Kokoro download failed — Void will use piper until it can fetch it"; }
else
  warn "kokoro-onnx failed to install — Void will use piper instead"
fi

IFS=- read -r loc spk q <<<"$VOICE"; lang="${loc%%_*}"
url="https://huggingface.co/rhasspy/piper-voices/resolve/main/$lang/$loc/$spk/$q/$VOICE"
if [ ! -f "$ROOT/voices/$VOICE.onnx" ]; then
  say "Downloading voice ${VOICE}…"
  if curl -fL --progress-bar -o "$ROOT/voices/$VOICE.onnx.part" "$url.onnx" \
     && curl -fsL -o "$ROOT/voices/$VOICE.onnx.json" "$url.onnx.json"; then
    mv "$ROOT/voices/$VOICE.onnx.part" "$ROOT/voices/$VOICE.onnx"
  else
    rm -f "$ROOT/voices/$VOICE".onnx*; warn "voice download failed — falling back to espeak-ng"
  fi
fi

if [ -e /proc/driver/nvidia/version ]; then
  say "NVIDIA GPU found — adding CUDA libraries for fast, accurate speech recognition (~1 GB)…"
  uv pip install --python "$ROOT/venv/bin/python" -U nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*" \
    || warn "couldn't install CUDA libs — speech recognition will run on the CPU"
fi

say "Fetching the speech-recognition model…"
"$ROOT/venv/bin/python" "$APPD/server.py" --warm \
  || warn "couldn't pre-load the speech model; it will download on first use"

[ $USER_ONLY = 1 ] || install -m755 "$SRC/bin/void" "$BIN/void"
"$ROOT/venv/bin/python" "$APPD/server.py" --sync-autostart >/dev/null 2>&1 \
  && say "Void will start when you log in (turn off: Settings → More)" || true
[ $USER_ONLY = 1 ] || [ $MAC = 1 ] || cat > "$HOME/.local/share/applications/void.desktop" <<DESK
[Desktop Entry]
Name=Void
Comment=Voice shell for Claude Code
Exec=$BIN/void
Icon=$ROOT/app/static/icon.png
Type=Application
Categories=Utility;Development;
StartupWMClass=Void
DESK

# restart a running server/window so they pick up the new files
pkill -f "$APPD/server.py" >/dev/null 2>&1 || true
pkill -f "$APPD/window.py" >/dev/null 2>&1 || true
pkill -f "$APPD/window_win.py" >/dev/null 2>&1 || true    # (macOS: the Qt window)
pkill -f -- "--user-data-dir=$ROOT/browser" >/dev/null 2>&1 || true    # an old browser-window fallback, if one is open

echo
if ! command -v claude >/dev/null 2>&1 && [ ! -x "$BIN/claude" ]; then
  warn "Claude Code CLI not found. Install it, then run 'claude' once to sign in:"
  echo "    curl -fsSL https://claude.ai/install.sh | bash"
fi
[ $USER_ONLY = 1 ] || case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN isn't on your PATH — add it to your shell rc";; esac
say "Done! Run:  void        (again to hide him · 'void doctor' if the mic acts up)"
[ $MAC = 1 ] && { echo "   Tip: bind a key to 'void listen' (Shortcuts, Raycast, skhd…). The first time he listens or looks,"
                  echo "   macOS asks to let your terminal use the microphone / record the screen: say yes."; exit 0; }
echo "   Tip: bind a key to 'void listen' in your desktop's keyboard shortcuts, e.g."
echo "     Hyprland: bind = SUPER, A, exec, void listen      sway / i3: bindsym \$mod+a exec void listen"
echo "     GNOME / KDE / XFCE: Settings → Keyboard → Shortcuts → add a custom one running: void listen"
