#!/usr/bin/env bash
# Void, one line, any Linux:
#   curl -fsSL https://raw.githubusercontent.com/PLAYER99-void/void/main/get.sh | bash
# Ubuntu/Debian, Fedora and Arch (and their relatives): downloads the right package from the latest release (.deb / .rpm /
# Arch), installs it with its dependencies, then sets up your voice + speech models and starts Void. Everywhere else, or
# with --source: gets the source (git clone, or a plain download if there's no git) and runs install.sh.
#   … | bash -s -- --source     always install from source (it updates itself with git)
#   … | bash -s -- --package    package only (fail instead of falling back to source)
set -euo pipefail
REPO="PLAYER99-void/void"
MODE=auto
for a in "$@"; do
  case "$a" in
    --source) MODE=source ;;
    --package) MODE=package ;;
    -h|--help) echo "usage: curl -fsSL https://raw.githubusercontent.com/$REPO/main/get.sh | bash [-s -- --source | --package]"
               exit 0 ;;
  esac
done
say(){ printf '\033[1;32m●\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die(){ printf '\033[1;31m✕\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Linux ] || die "this installer is for Linux. On Windows, download VoidSetup.exe from https://github.com/$REPO/releases/latest"
[ "$(id -u)" -ne 0 ] || die "run it as your normal user (not root / sudo): Void is set up per user. It asks for your password when it needs it."
command -v curl >/dev/null 2>&1 || die "curl is missing — install it with your package manager, then run this again"
if command -v sudo >/dev/null 2>&1; then SUDO=sudo; elif command -v doas >/dev/null 2>&1; then SUDO=doas
else die "neither sudo nor doas is here — install one (Void needs it once, for the system packages)"; fi

ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/void"
TMP="$(mktemp -d)"; chmod 755 "$TMP"              # (apt reads the package as its own user)
trap 'rm -rf "$TMP"' EXIT

# which package fits this system
KIND=""
if command -v pacman >/dev/null 2>&1; then KIND=arch
elif command -v apt-get >/dev/null 2>&1; then KIND=deb
elif command -v dnf >/dev/null 2>&1; then KIND=rpm
fi
[ -f /etc/os-release ] && . /etc/os-release
say "Installing Void on ${PRETTY_NAME:-Linux}"

start_void(){                                     # pop him up, if there's a desktop to show him on
  if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then "$1" show >/dev/null 2>&1 & disown || true
    say "Done! He's on your screen. Say \"hey Void\", or run: void"
  else say "Done! Run 'void' from your desktop session to start him."; fi
  command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ] || {
    warn "He needs Claude Code to do the work. Install it, then run 'claude' once to sign in:"
    echo "    curl -fsSL https://claude.ai/install.sh | bash"; }
}

from_package(){
  local pat url f
  case "$KIND" in
    deb)  pat='_all\.deb$' ;;
    rpm)  pat='\.noarch\.rpm$' ;;
    arch) pat='-any\.pkg\.tar\.zst$' ;;
    *)    return 1 ;;
  esac
  say "Finding the latest release…"
  url=""
  [ -n "${VOID_NO_API:-}" ] || url="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
         | grep -o '"browser_download_url": *"[^"]*"' | sed 's/.*"\(http[^"]*\)"/\1/' | grep -E -e "$pat" | head -n 1)" || true   # (-e: Arch's starts with "-")
  if [ -z "$url" ]; then
    # GitHub's API limits anonymous requests (shared networks hit it): read the normal release page instead
    local tag
    tag="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" 2>/dev/null)" || true
    tag="${tag##*/}"
    if [ -n "$tag" ] && [ "$tag" != latest ]; then
      url="$(curl -fsSL "https://github.com/$REPO/releases/expanded_assets/$tag" 2>/dev/null \
             | grep -o 'href="[^"]*/releases/download/[^"]*"' | sed 's/^href="//; s/"$//' | grep -E -e "$pat" | head -n 1)" || true
      [ -n "$url" ] && url="https://github.com$url"
    fi
  fi
  [ -n "$url" ] || { warn "couldn't find a $KIND package in the latest release"; return 1; }
  f="$TMP/${url##*/}"
  say "Downloading ${url##*/}…"
  curl -fL --progress-bar -o "$f" "$url" || { warn "download failed"; return 1; }
  say "Installing it with its dependencies (your password may be asked)…"
  # (this runs as `from_package || …`, where bash ignores set -e: every step checks itself)
  case "$KIND" in
    deb)  { $SUDO apt-get update -qq || true; } && $SUDO apt-get install -y "$f" ;;
    rpm)  $SUDO dnf install -y "$f" ;;
    arch) $SUDO pacman -U --needed --noconfirm "$f" ;;
  esac || { warn "the package didn't install (a dependency your release doesn't have?)"; return 1; }
  # an earlier from-source install would shadow the package: its copy of the app and its launcher (your voices,
  # models and settings stay)
  if [ -d "$ROOT/app" ]; then say "Switching your earlier install over to the package"; rm -rf "$ROOT/app"; fi
  if [ -f "$HOME/.local/bin/void" ] && grep -q "Void launcher" "$HOME/.local/bin/void" 2>/dev/null; then rm -f "$HOME/.local/bin/void"; fi
  say "Setting up his voice and hearing (a few hundred MB, one time)…"
  /usr/bin/void setup || die "'void setup' didn't finish — run it again to see why: void setup"
  start_void /usr/bin/void
}

from_source(){
  local src="${VOID_SRC:-$HOME/.local/share/void-src}"
  if ! command -v git >/dev/null 2>&1; then
    case "$KIND" in                                # git lets "Update now" keep him current
      deb)  $SUDO apt-get update -qq || true; $SUDO apt-get install -y git || true ;;
      rpm)  $SUDO dnf install -y git || true ;;
      arch) $SUDO pacman -S --needed --noconfirm git || true ;;
    esac
  fi
  if [ -d "$src/.git" ]; then
    say "Updating the source in ${src}…"; git -C "$src" pull --ff-only || die "couldn't update $src (git pull)"
  elif command -v git >/dev/null 2>&1; then
    say "Getting the source (git clone) into ${src}…"; rm -rf "$src"
    git clone --depth 1 "https://github.com/$REPO.git" "$src" || die "git clone failed — check your internet connection"
  else
    warn "no git — downloading the source as an archive (you'll update by running this installer again)"
    rm -rf "$src"; mkdir -p "$src"
    curl -fL --progress-bar "https://github.com/$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$src" --strip-components=1 \
      || die "couldn't download the source — check your internet connection"
  fi
  say "Running install.sh (system packages, his voice and hearing)…"
  (cd "$src" && ./install.sh </dev/null) || die "install.sh didn't finish — run it again to see why: cd $src && ./install.sh"
  start_void "$HOME/.local/bin/void"
}

case "$MODE" in
  source)  from_source ;;
  package) from_package || die "no package for this system — try: … | bash -s -- --source" ;;
  auto)    if [ -n "$KIND" ]; then from_package || { warn "falling back to installing from source"; from_source; }
           else from_source; fi ;;
esac
