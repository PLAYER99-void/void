<p align="center"><img src="app/static/icon.png" width="180" alt="Void"></p>

<h1 align="center">Void</h1>

<p align="center"><b>Your voice-first desktop companion for Claude Code.</b><br>
Say “hey Void” and he does the work.</p>

<p align="center">
  <a href="#install"><b>Install</b></a> &nbsp;·&nbsp;
  <a href="#your-first-two-minutes"><b>First steps</b></a> &nbsp;·&nbsp;
  <a href="#things-to-try"><b>Things to try</b></a> &nbsp;·&nbsp;
  <a href="#meet-him"><b>Features</b></a> &nbsp;·&nbsp;
  <a href="docs/GUIDE.md"><b>Full guide</b></a> &nbsp;·&nbsp;
  <a href="#need-help"><b>Help</b></a>
</p>

<p align="center">
  <a href="https://github.com/PLAYER99-void/void/releases/latest"><img src="https://img.shields.io/github/v/release/PLAYER99-void/void?style=flat-square&color=3d9b74&label=latest" alt="Latest release"></a>
  <a href="https://github.com/PLAYER99-void/void/actions/workflows/installer.yml"><img src="https://img.shields.io/github/actions/workflow/status/PLAYER99-void/void/installer.yml?style=flat-square&label=installer%20tests" alt="Installer tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-3d9b74?style=flat-square" alt="MIT license"></a>
</p>

<br>

<table>
<tr>
<td width="44%" align="center"><img src="assets/demo/talk.webp" width="300" alt="Void talking, captions appearing word by word"></td>
<td width="56%">

### Talk to your code

Void is a little orb that floats on your desktop. Tell him what you want, and he gets
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) to do it in your project. Then he tells you how it went.

- 🎙️ **Just say it.** "Hey Void, fix the failing tests."
- 👀 **Watch him work.** He holds up a tiny terminal so you see every step.
- 🔒 **Private.** Your voice never leaves your computer.
- ✨ **Out of your way.** No app window, no taskbar button. Just him.
- 🔝 **Or tuck him into your screen's edge.** Push him to any edge and he becomes a MacBook-style notch.

</td>
</tr>
</table>

## Install

> **You need [Claude Code](https://docs.anthropic.com/en/docs/claude-code) first.** Install it, then run `claude` once in a
> terminal to sign in. Void is the voice and face; Claude Code does the work.

<table>
<tr>
<th width="50%"><img src="https://img.shields.io/badge/Windows_10_%2F_11-0078D4?style=flat-square&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZmlsbD0iI2ZmZiIgZD0iTTAgMy40IDkuOCAydjkuNEgwem0xMC45LTEuNkwyNCAwdjExLjNIMTAuOXpNMCAxMi42aDkuOFYyMkwwIDIwLjZ6bTEwLjkgMEgyNFYyNGwtMTMuMS0xLjh6Ii8%2BPC9zdmc%2B" alt="Windows 10 / 11"></th>
<th width="50%"><img src="https://img.shields.io/badge/Linux-333333?style=flat-square&logo=linux&logoColor=white" alt="Linux"></th>
</tr>
<tr>
<td valign="top">

<p align="center"><a href="https://github.com/PLAYER99-void/void/releases/latest/download/VoidSetup.exe"><img src="https://img.shields.io/badge/Download-VoidSetup.exe-0078D4?style=for-the-badge&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZmlsbD0iI2ZmZiIgZD0iTTAgMy40IDkuOCAydjkuNEgwem0xMC45LTEuNkwyNCAwdjExLjNIMTAuOXpNMCAxMi42aDkuOFYyMkwwIDIwLjZ6bTEwLjkgMEgyNFYyNGwtMTMuMS0xLjh6Ii8%2BPC9zdmc%2B" alt="Download VoidSetup.exe for Windows"></a></p>

1. Download **VoidSetup.exe** and run it.
2. Wait a few minutes while it sets up his voice and hearing.
3. Find **Void** in your Start menu.

<sub>No admin needed. Uninstall from Settings → Apps.<br>
"Windows protected your PC"? Click <b>More info → Run anyway</b>.</sub>

</td>
<td valign="top">

<p align="center">
<img src="https://img.shields.io/badge/-Ubuntu-E95420?style=flat-square&logo=ubuntu&logoColor=white" alt="Ubuntu">
<img src="https://img.shields.io/badge/-Debian-A81D33?style=flat-square&logo=debian&logoColor=white" alt="Debian">
<img src="https://img.shields.io/badge/-Fedora-51A2DA?style=flat-square&logo=fedora&logoColor=white" alt="Fedora">
<img src="https://img.shields.io/badge/-Arch_Linux-1793D1?style=flat-square&logo=archlinux&logoColor=white" alt="Arch Linux">
</p>

**One line, any distro.** Paste it into a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/PLAYER99-void/void/main/get.sh | bash
```

<details>
<summary><b>What that line does, and other ways to install</b></summary>

It picks the right package for your system, downloads and installs it with everything it needs, sets up his
voice and hearing, and starts him. On a distro without a package, it gets the source and installs that instead.
**Update now** in his settings keeps him current either way.

**Or download a package yourself** (.deb · .rpm · Arch)

<p align="center">
<a href="https://github.com/PLAYER99-void/void/releases/latest"><img src="https://img.shields.io/badge/Ubuntu_%2F_Debian-.deb-E95420?style=for-the-badge&logo=ubuntu&logoColor=white" alt="Download the .deb for Ubuntu and Debian"></a><br>
<a href="https://github.com/PLAYER99-void/void/releases/latest"><img src="https://img.shields.io/badge/Fedora-.rpm-51A2DA?style=for-the-badge&logo=fedora&logoColor=white" alt="Download the .rpm for Fedora"></a><br>
<a href="https://github.com/PLAYER99-void/void/releases/latest"><img src="https://img.shields.io/badge/Arch_Linux-.pkg.tar.zst-1793D1?style=for-the-badge&logo=archlinux&logoColor=white" alt="Download the package for Arch Linux"></a>
</p>

Download the one for your distro from the [latest release](https://github.com/PLAYER99-void/void/releases/latest), then:

| | |
|---|---|
| <img src="https://img.shields.io/badge/-Ubuntu_%2F_Debian-E95420?style=flat-square&logo=ubuntu&logoColor=white" alt="Ubuntu / Debian"> | `sudo apt install ./void-claude_*_all.deb` |
| <img src="https://img.shields.io/badge/-Fedora-51A2DA?style=flat-square&logo=fedora&logoColor=white" alt="Fedora"> | `sudo dnf install ./void-claude-*.noarch.rpm` |
| <img src="https://img.shields.io/badge/-Arch_Linux-1793D1?style=flat-square&logo=archlinux&logoColor=white" alt="Arch Linux"> | `sudo pacman -U ./void-claude-*.pkg.tar.zst` |

Then `void setup` once (his voice and hearing) and `void`.

**Or from source** (git clone)

```bash
git clone https://github.com/PLAYER99-void/void.git
cd void && ./install.sh
void
```

It gets the system packages for you (pacman, apt or dnf) and updates itself with `git pull`.

<sub>Gentoo or system-wide? See the <a href="docs/GUIDE.md#install-from-source">guide</a>.</sub>

</details>

</td>
</tr>
</table>

### macOS

<p align="center"><img src="https://img.shields.io/badge/-macOS-000000?style=flat-square&logo=apple&logoColor=white" alt="macOS"></p>

> [!NOTE]
> The Mac version lives on the [`macos` branch of `ohshitgorillas/void`](https://github.com/ohshitgorillas/void/tree/macos),
> a fork of Void kept up to date with the main project. It is built and tested on macOS by the fork's maintainer; the
> main project's author doesn't have a Mac and can't vouch for it.

## Your first two minutes

| | Do this | What happens |
|:-:|---|---|
| **1** | Start Void | He appears in the bottom-right corner. Drag him anywhere; he remembers the spot. |
| **2** | Say **"hey Void"** | His ring lights up and moves with your voice. (Or just **click** him.) |
| **3** | Ask for something | He works in the current folder. Say "switch to my project" or right-click → *folder* to change it. |
| **4** | **Right-click** him | His menu: talk, folder, history, show my screen, hover mode, settings. |
| **5** | **Push him into a screen edge** | He merges into it as a notch, with just his eyes. Click the notch for his menu (click again to close it). Drag him out to get the orb back. |

> [!TIP]
> Say **"stop"** to interrupt him, hold **Space** for push-to-talk, or just start **typing** for a text box. Press
> **Esc** to back out of anything. **Ctrl+N** (or saying "new chat") starts a fresh conversation.

## Things to try

| 💻 Coding | 🖥️ Your screen | ⚡ Instant |
|---|---|---|
| "Hey Void, run the tests" | "What's this error on my screen?" | "Timer 10 minutes" |
| "Fix the failing test" | "Explain what I copied" | "Volume up" |
| "What does this project do?" | "Watch my screen" (live view) | "Open firefox" |
| "Make the button blue" | "Did that fix the layout?" | "Remind me at 5 to call mom" |

<sub>Before running commands he asks first: say "yes", "no" or "always". Change that in Settings → Brain.</sub>

## Meet him

<table>
<tr>
<td align="center" width="50%"><img src="assets/demo/work.webp" width="300" alt="Void working on his little terminal"><br>
<b>He does the work</b><br><sub>Reads, edits and runs commands on a little terminal he holds like a sheet of paper.</sub></td>
<td align="center" width="50%"><img src="assets/demo/listen.webp" width="300" alt="Void listening, his ring moving with your voice"><br>
<b>He listens</b><br><sub>Say "hey Void", click him, or keep talking right after he answers.</sub></td>
</tr>
<tr>
<td align="center"><img src="assets/demo/moods.webp" width="300" alt="Void cycling through his moods"><br>
<b>He reacts</b><br><sub>Happy, curious, shy, sleepy, starry-eyed… his face follows what he says, word by word.</sub></td>
<td align="center"><img src="assets/demo/skins.webp" width="300" alt="Void trying on skins and colours"><br>
<b>Dress him up</b><br><sub>Cat ears, halo, crown, headphones, a bow, and any colours you like.</sub></td>
</tr>
<tr>
<td align="center"><img src="assets/demo/pet.webp" width="300" alt="Void being petted"><br>
<b>Pet him</b><br><sub>Rub the mouse over him and he purrs. Click him too much and he dodges.</sub></td>
<td align="center"><img src="assets/demo/remember.webp" width="300" alt="Void remembering where your blog is, then switching to it"><br>
<b>He remembers</b><br><sub>Say "remember my blog is in ~/code/blog" and he knows it in every chat.</sub></td>
</tr>
<tr>
<td align="center"><img src="assets/demo/notch.webp" width="300" alt="Void as a notch at the top of the screen: listening, talking, running a command, and his menu"><br>
<b>He becomes a notch</b><br><sub>He glows while you talk, and his words and commands drop out of the notch. Clicks go through to what's under it.</sub></td>
<td align="center"><img src="assets/demo/edges.webp" width="300" alt="Void merging into the top, right, bottom and left edges of the screen"><br>
<b>Top, bottom, left or right</b><br><sub>Drag him into any edge and he melts into it. Drag him out and he's the orb again. Windows and Linux.</sub></td>
</tr>
</table>

<p align="center"><a href="docs/GUIDE.md"><b>See everything he can do in the full guide →</b></a></p>

## Need help?

Run **`void doctor`** first. It checks your mic, his voice and Claude Code in a few seconds.

| Problem | Fix |
|---|---|
| He can't hear me | Check the level meter in `void doctor`, then pick the right mic in Settings → Voice. |
| "I can't find Claude Code" | Install [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and run `claude` once to sign in. |
| Windows: "DLL load failed" | Install the [Visual C++ runtime](https://aka.ms/vs/17/release/vc_redist.x64.exe), then `void restart`. |
| Something else | Right-click him → Settings → More → **Copy diagnostics**, then **Report a problem** and paste it. Or see [Troubleshooting](docs/GUIDE.md#troubleshooting). |

<br>

<p align="center">
  <sub>Speech-to-text by <a href="https://github.com/SYSTRAN/faster-whisper">faster-whisper</a> · voice by <a href="https://github.com/thewh1teagle/kokoro-onnx">Kokoro</a> · brains by <a href="https://docs.anthropic.com/en/docs/claude-code">Claude Code</a><br>
  Everything runs on your computer · <a href="LICENSE">MIT license</a></sub>
</p>
