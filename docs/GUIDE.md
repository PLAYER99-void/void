<p align="center"><a href="../README.md"><img src="../assets/void.png" width="96" alt="Void"></a></p>

<h1 align="center">The Void guide</h1>

<p align="center">Everything Void can do, and how to set him up just the way you like.<br>
New here? Start with the <a href="../README.md">README</a>: it gets you running in two minutes.</p>

---

**Contents**

| Using him | Making him yours | Setup and help |
|---|---|---|
| [Talking to him](#talking-to-him) | [Asking before he acts](#asking-before-he-acts) | [Install from source](#install-from-source) |
| [Things he does instantly](#things-he-does-instantly) | [Memory and history](#memory-and-history) | [Linux desktops](#linux-desktops) |
| [Showing him your screen](#showing-him-your-screen) | [His face and personality](#his-face-and-personality) | [Starting at login](#starting-at-login) |
| [While he works](#while-he-works) | [Skins and colours](#skins-and-colours) | [Troubleshooting](#troubleshooting) |
| [Hover mode](#hover-mode) | [Config file](#config-file) | [How it works](#how-it-works) |
| [Top bar (notch)](#top-bar-notch) | | [Updates](#updates) |
| [Commands](#commands) | | |

---

## Talking to him

| | |
|---|---|
| say **"hey Void"** | wakes him up (on by default; Settings → Voice). "Hey Void, run the tests" works in one go |
| **left-click** him | talk; he stops listening when you stop talking |
| **right-click** him | his sparks swirl out into the menu: talk · folder · history · show my screen · hover · settings · hide. Click him (or right-click again) to close it |
| **Ctrl+N** or say "new chat" | starts a fresh conversation |
| hold **Space** | push-to-talk |
| **click** him or tap **Space** while he's working or talking | cuts him off. A click then listens; holding Space lets you talk straight away |
| just **type** | a box pops up under him; **Enter** sends. 📷 attaches a screenshot (right-click it for your clipboard) |
| **drag** him | park him anywhere; he remembers the spot (outside hover mode). Drop him right at a screen edge and he becomes the [top bar](#top-bar-notch) |
| **drag a panel's header** | moves Settings or History (and him with it) somewhere handier |
| **Esc** | close menus → cancel listening → stop Claude → shush → hide |

**Hidden means off.** Once he's hidden (Esc, *hide* in his menu) the mic is closed and he stops talking — he can't hear
"hey Void" until you bring him back with the Start menu / app launcher, your hotkey, or `void`. `void stop` quits him
completely.

**Push-to-talk hotkey.** Hold **Win+Space** (the Windows key, or Super on Linux) anywhere, even while another app is in front, to talk; let go and
he answers. Change it in Settings → Voice. On Windows Void watches for the combo himself. On Linux, bind the press
and release in your desktop:

| desktop | in your config |
|---|---|
| Hyprland | `bind = SUPER, SPACE, exec, void ptt-down` and `bindr = SUPER, SPACE, exec, void ptt-up` |
| Hyprland 0.56+ (Lua) | `hl.bind("SUPER + SPACE", hl.dsp.exec_cmd("void ptt-down"))` and the same with `void ptt-up` and `{ release = true }` |
| sway / i3 | `bindsym Mod4+space exec void ptt-down` and `bindsym --release Mod4+space exec void ptt-up` |
| GNOME / KDE / others | shortcuts only fire on press there: bind `void listen` instead (he stops when you stop talking) |

After he answers, keep talking for a few seconds and he still hears you, no "hey Void" needed (`follow_up` in the
config).

**Interrupting him.** Say **"stop"** (or "wait", "shh", "be quiet") while he's talking and he stops. Say **"hey Void,
…"** mid-sentence to cut in with something new. Settings → Voice → "Interrupt him by voice".

Settings → Look → "Click through around him" lets clicks on the see-through space go to the window behind (off by
default).

## Things he does instantly

These never wait for Claude:

| say | |
|---|---|
| "volume up" · "set volume to 40" · "mute" | sound |
| "pause" · "next song" · "what's playing" | music (Linux, via playerctl) |
| "brightness down" | screen brightness |
| "timer 10 minutes" · "remind me in 20 minutes to stretch" · "remind me at 5 to call mom" | he pings you, out loud and with a notification |
| "how long is left" · "cancel my timers" | |
| "what time is it" · "what's the date" | |
| "open firefox" | any installed app |
| "lock my screen" | |
| "switch to my blog" | jumps to a project folder. Teach him with "remember my blog is in ~/code/blog", or add it in Settings → Brain |

Everything else goes to Claude. **Settings → Brain → Model → Auto** sends quick chit-chat to the fast model and real
work to the full one.

## Showing him your screen

- Say "look at my screen", "what's this error on my screen?" or "explain what I copied", and he grabs a screenshot or
  your clipboard and sends it along. You can also use 📷 in the typing box, "show my screen" in the menu, or `void look`.
  He takes just the window you're using (say "my whole screen" for everything) and shrinks it, so a look costs about a
  third fewer tokens.
- **Live view:** say **"watch my screen"** (or `void live`, `/live`) and a green **LIVE** sticker appears on him. While
  it's on, everything you say comes with a fresh picture of your window, so "why is this red?" just works. He only
  opens the picture when your question is about the screen, and nothing at all is sent while you're quiet. Say
  **"stop watching"** to end it; it also turns itself off after 10 quiet minutes (`live_minutes` in the config).
- **He looks by himself when a task needs it:** if you ask "is the build still running?" or "did that fix the
  layout?", he can take a quick look at your window on his own (`void see`) — you'll see him glance with his looking
  face. He only looks during a task that needs it, never in the background, and he can only look: no clicking or typing.
- Screenshots go to `~/.cache/void/shots` (only the last 30 are kept).

## While he works

**The status pill.** A bright spark circles him and a pill up top says what he's doing and for how long ("running npm
test · 0:12") for as long as Claude is busy. When he answers, the words appear as he says them.

**The terminal.** While he works he holds a small terminal like a piece of paper, just under his eyes, turned to face
him, so you see it from the back (the text reads mirrored and the top leans toward you). It shows what he's really
doing: the lines he's changing (red `-` out, green `+` in), the command he runs and its output, the file he's reading,
search hits and his plan. While he reads, his eyes rest on it and it stays still. Every several seconds he looks away
(left, right, up, or at you) and takes the paper with him. The title says what it is ("editing server.py", "Run the
tests") with a ✓ or ✕ when it's done. In Root mode it wears a red ROOT sticker (on the side you see, so it reads the
right way round), and commands get a red border and a `#` prompt. It goes away when he starts answering. It only swings
out to a side that has room, so it never runs off the screen when he's parked in a corner, and it never covers his
eyes. Settings → Look → Terminal turns it off, or switches it to **Faces you (readable)** so you can read along.

**Long answers.** When an answer is long or has code, a **↗ full answer** button appears under him. It opens the whole
thing with proper formatting and a copy button on every code block. (Not in the top bar; open History there instead.)

**When a long task finishes** while he's hidden, you get a desktop notification. You can also have him pop back up
(Settings → More).

## Hover mode

Turn it on in the menu, in Settings → Look, or with `void hover`. **He becomes your mouse cursor**: a small, plain dot
that covers the real pointer, with no sparks or glow so he isn't distracting. His eyes look the way you move. Clicks go
straight through him. On Hyprland the normal pointer is hidden underneath.

**Call him** (`void`, `void listen`, "hey Void", or `void hover` again) and he glides from the cursor back to his spot and
grows back to full size, ready to listen. `void stop` always brings the normal cursor back. Works on Hyprland and X11
desktops. Other Wayland desktops don't tell apps where the mouse is, so it isn't available on sway or KDE, and on GNOME
he only keeps up while the mouse is over older (X11) apps.

## Top bar (notch)

Instead of floating, he can **merge into an edge of your screen** as a black notch, like the one on a MacBook, with
just his eyes showing. Everything else works the same.

- **Turn it on:** **drag him into a screen edge**: top, bottom,
  left or right. Let go with the mouse right at the edge and he merges in.
- **Get the orb back:** drag the notch away from the edge and he pops out under your mouse (the notch melts back into the wall).
  He remembers both where you parked the orb and which edge he likes.
- **Talking:** the whole notch glows while you talk (brighter the louder you are) and pulses while he speaks. The
  "hey Void" flash matches the notch.
- **His words and commands** drop out of the notch itself: it grows to fit the caption, or his terminal while he works
  (always readable, in the same style as his words), then tucks back in. No status pill.
- **Click the notch** to open his menu around it; click again to close it. (In the notch, talk with "hey Void",
  Space, or 🎙 in the menu.)
- **His menu** fans out around the notch. Settings and History open as usual, and you can drag them by the header; he
  goes back to his edge when you close them.
- **Clicks go through** everything except a small area around his eyes, so you can still click and drag things under
  the notch.
- **Where it works:** Windows, Linux with layer-shell (Hyprland, sway, KDE Plasma, river, niri…) and X11 or GNOME
  (through XWayland). On sway there's no global mouse position, so push him past the edge to dock him. Turning on
  hover mode leaves the top bar.

## Commands

| command | what it does |
|---|---|
| `void` | show / hide him |
| `void listen` | pop up and start listening (bind this to a hotkey) |
| `void look` | screenshot, then listen |
| `void live` · `void see` | live view · take one look |
| `void hover` | hover mode on / off |
| `void doctor` | mic check: lists inputs, records 3 seconds, shows a level meter |
| `void stress` | voice stutter test |
| `void render default\|nodmabuf\|nocomp` | how he's drawn (Linux) |
| `void restart` · `void stop` | restart · quit |
| `void log` | the server log |

In the typing box: `/new` `/cd <dir>` `/history` `/memory` `/remember …` `/forget …` `/look` `/clip` `/hover`
`/model <opus|sonnet|haiku>` `/voice` `/eyes <colour|random>` `/whites <colour|random>` `/pupils <slit|circle|dot>`
`/mute` `/stop`

---

## Asking before he acts

Settings → Brain:

- **Ask me first** (default): he edits files in your project on his own, but before running a command, going online,
  or changing a file outside the project or in a sensitive place (shell and PowerShell profiles, startup / autostart
  folders, Void's and Claude's own files and settings, `~/.ssh`, `~/.local/bin`, git hooks and config) he shows a card,
  asks out loud, and waits. Say "yes", "no" or "always", or click / press Y / N. "Always" means no more asking for
  that *exact* command until you start a new chat, continue an old one or switch folders. It isn't offered for
  commands that run other commands (`&&`, `;`, `|`, `$(…)`…): those are asked about every time.
- **Just do it**: runs anything without asking.
- **Look, don't touch**: reads and explains, changes nothing.
- **Root mode** (Linux): like "just do it", plus `sudo` works. When he needs root, a small password card appears right
  next to him (no separate window) — type it and press Enter. Your password goes straight to sudo and is never stored.

## Memory and history

- Say **"remember …"** ("remember my main project is in ~/code/app") and he knows it in every future chat.
  Say "forget …" to drop a note, or "what do you remember?" to see them. You can also edit notes in the 🧠 panel.
- A new chat also gets a short summary of your last few conversations, so he has some context.
- **History** (menu, or **Ctrl+H**) keeps every exchange. Click one to read the full answer, copy it, ask it again, or
  **continue that chat**.
- Memory (`memory.json`) and history (`history.jsonl`) live in `~/.local/share/void` on Linux and `%LOCALAPPDATA%\void`
  on Windows. Both are plain files you can read or delete.

## His face and personality

- **Pupils:** slits, circles, dots, big circles, hearts and spinning stars. They follow his mood and what Claude is doing.
- **Moods while working:** reading (eyes sweep along lines), editing (concentrated squint), running a command (eyes on
  the terminal, a sweat drop), searching the web (darting around), asking permission (puppy eyes).
- **Other moods:** happy, beaming "^ ^", excited (stars), love (hearts, when you say thanks), shy, wink, curious,
  confused, surprised, scared (trembling pupils), sad (droopy eyes and a tear when something fails), bored, annoyed,
  sleepy.
- **Motion:** he doesn't bob or bounce; all the life is in his eyes.
- **Talking:** he talks like a person, in time with his words: mostly looking at you with small eye movements, glancing
  off to the side while he finds the words, a little nod at the end of a sentence, a head tilt on a question.
- **He means it:** Claude marks how it means each sentence with a little tag (`[proud]`, `[sorry]`, `[unsure]`,
  `[thinking]`, `[curious]`, `[laugh]`, `[warm]`, `[serious]`, `[excited]`, `[surprised]`, `[happy]`, `[neutral]`), and
  his face changes right on that word: a proud "^ ^" when something's fixed, puppy eyes when he's sorry, one brow up when
  he's unsure, looking up and away while he thinks, a giggle when he laughs. The tags are never shown or spoken.
  (Replies without tags, like his quick answers, fall back to reading the words.)
- **Personality:** he doesn't chatter: no greetings, no comments about your computer, no pop-ups you didn't ask for. He
  gets sleepier late at night. Rub the mouse back and forth over him and he purrs ("^ ^" and little hearts). Click him
  too many times and he dodges.
- **Hanging out:** every few seconds of quiet he does a little routine: looks around, rolls (the whole ball, or just his
  eyes), yawns, chases one of his sparks with his eyes, hums and sways, peeks, stretches, whistles, gets a bit dizzy,
  shakes it off, or blinks a few times. Move the mouse over him and he stops to look at you.
- **Sound effects:** soft little blips when he wakes up, opens his menu, finishes a task or reminds you. Settings →
  Voice → Sound effects.

## Skins and colours

Settings → Look:

- **Colours:** tap any part of him in the preview (body, either pupil, either eye white, or his skin) and pick a colour
  with the colour square, the hue slider, a hex code, the swatches or your recent colours. Turn off **Matching eyes** to
  give him two different eyes. **🎲 all** rolls a whole new look; **Surprise me** does it every time he appears.
- **Skins:** cat ears, bunny ears, bear ears, antenna, devil horns, halo, crown, sprout, party hat, headphones or a bow.
  Ears twitch and droop or perk up with his mood, and the antenna and sprout wobble when he moves.
- **Typing:** `/skin cat`, `/body #c9a8f0`, `/eyes pink`, `/whites mint`.

## Config file

Almost everything is in Settings. The file is `~/.config/void/config.json` on Linux and `%APPDATA%\void\config.json` on
Windows.

| key | default | |
|---|---|---|
| `trust` | `ask` | `ask`, `trust` or `readonly` (see [Asking before he acts](#asking-before-he-acts)) |
| `wake_word` | `true` | "hey Void" |
| `autostart` | `true` | start when you log in |
| `listen_on_show` | `false` | start listening as soon as he pops up |
| `barge_in` | `true` | say "stop" while he talks |
| `follow_up` | `4` | seconds after he stops talking that he still hears you without "hey Void" (`0` = off) |
| `sfx` | `true` | sound effects |
| `look` | | body / pupils / whites / skin (set in Settings → Look) |
| `projects` | `{}` | name → folder, for "switch to …" |
| `model` | `""` | `auto`, `opus`, `sonnet`, `haiku` |
| `hover` | `false` | float next to the cursor |
| `tts` | `auto` | voice engine: `auto` (Kokoro, then piper, then espeak), `kokoro`, `piper`, `espeak`, `off` |
| `voice` | `girl` | girl, cute, guy, deep, british, story, robot (Settings → Voice) |
| `kokoro_voice` | | any Kokoro voice id, e.g. `af_sky`, `am_puck`, `bf_emma` — overrides the preset |
| `whisper_model` | `auto` | `small.en` on an NVIDIA GPU, `base.en` on CPU; or `tiny.en` / `medium.en` |
| `stt_hint` | coding words | words he should expect (add your project names) |
| `mic_device` | `""` | input to record from (Settings → Voice lists them) |
| `ptt_hotkey` | `Win+Space` | hold to talk from anywhere (Windows; on Linux bind `void ptt-down` / `void ptt-up`); `""` = off |
| `update_check` | `true` | ask GitHub twice a day whether a newer Void is out (nothing about you is sent); Settings → More shows it with an **Update now** button |
| `mic_auto` | `true` | keep your voice at a good level: after you speak he turns the Windows mic level up if you came in quiet, down if you clipped, and boosts it himself past 100% (Windows) |
| `extra_args` | `[]` | extra CLI flags for `claude` |

---

## Install from source

**Windows** — download the folder (green **Code** button → **Download ZIP**, then unzip it — or `git clone`), open it,
and in PowerShell run:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

Everything goes in `%LOCALAPPDATA%\void`, no admin needed. Then open a new terminal and run `void`, or start **Void**
from the Start menu. If your PC doesn't have the
[Microsoft Visual C++ runtime](https://aka.ms/vs/17/release/vc_redist.x64.exe), install it too (VoidSetup.exe does this
for you).

**Gentoo (or any distro, system-wide)** — install the dependencies with `emerge` (WebKitGTK and gtk-layer-shell need the
`introspection` USE flag):

```bash
sudo emerge --ask --noreplace dev-build/meson dev-build/ninja dev-python/pygobject net-libs/webkit-gtk:4.1 \
  gui-libs/gtk-layer-shell media-libs/gst-plugins-base media-libs/gst-plugins-good app-accessibility/espeak-ng \
  dev-python/uv gui-apps/grim gui-apps/slurp gui-apps/wl-clipboard x11-libs/libnotify media-sound/playerctl
```

You also need the PulseAudio/PipeWire command-line tools (`parecord`, `paplay`). Then build and install, and once per
user download the voice and speech models:

```bash
./configure && ninja -C build && sudo ninja -C build install
void setup
void
```

Nothing is compiled (Void is Python); `ninja install` puts the app in `/usr/share/void`, `void` in `/usr/bin`, plus a
menu entry and icon. `./configure --prefix=/usr/local` works too. To uninstall: `sudo ninja -C build uninstall`.

## Linux desktops

| desktop | what he is |
|---|---|
| **Wayland with layer-shell**: Hyprland, sway, KDE Plasma, river, niri, Wayfire, labwc, COSMIC… | a true overlay, like a bar or a notification: above every app on every workspace, never tiled, no taskbar entry, no window rules needed |
| **GNOME (Wayland)** | GNOME has no layer-shell, so he runs through XWayland as an always-on-top window on every workspace |
| **X11**: i3, bspwm, Openbox, XFCE, Cinnamon, MATE, KDE on X11… | an always-on-top borderless window on every workspace, kept out of the taskbar |

He starts in the bottom-right corner; drag him anywhere and he remembers the spot. To be see-through on a bare X11
window manager (i3, bspwm, Openbox) you need a compositor running, e.g. `picom`.

**Bind a hotkey** to `void listen`:

| desktop | how |
|---|---|
| Hyprland | `bind = SUPER, A, exec, void listen` in `~/.config/hypr/hyprland.conf` |
| sway / i3 | `bindsym $mod+a exec void listen` in your config |
| GNOME / KDE / XFCE / Cinnamon | Settings → Keyboard → Shortcuts → add a custom shortcut that runs `void listen` |

On Windows, right-click the Void shortcut → Properties → Shortcut key.

`VOID_NO_LAYER=1 void` skips the overlay and uses a normal always-on-top window. `VOID_BROWSER=1 void` opens him in a
plain browser app window instead (handy if WebKitGTK misbehaves).

## Updates

Settings → More shows which version you have and whether a newer one is out (Void asks GitHub's public release list
when he starts, every 6 hours and when you open Settings; **Check now** asks straight away, and **Check for updates**
turns it off). **Update now** installs it: on Windows it runs the new
VoidSetup.exe quietly and Void comes back by himself. On Linux:

| installed with | **Update now** |
|---|---|
| the `.deb` (Ubuntu / Debian), `.rpm` (Fedora) or Arch package from a release | downloads the new package from the release and installs it with `pkexec` (your desktop asks for your password), then restarts him |
| `git clone` + `install.sh` | runs `git pull` and `install.sh` for you |
| anything else (Gentoo, a system-wide `ninja install`) | tells you a new version is out; update the way you installed |

If an update can't finish (you cancelled the password prompt, say), Settings → More shows the exact command to run.

## Starting at login

Void starts when you log in (Settings → More → "Start when I log in"). On Windows that's a shortcut in your Startup
folder. On Linux it's `~/.config/autostart/void.desktop`, which GNOME, KDE, XFCE, Cinnamon and MATE pick up; on Hyprland
it also adds a marked `exec-once` line to `hyprland.conf`. Turning the setting off removes them. On sway / i3 / other
bare window managers, add `exec void show` to your config.

He's listening for "hey Void" straight away. Turn on Settings → Voice → "Listen as soon as he pops up" if you want `void`
to start listening immediately too.

## Troubleshooting

**Start with `void doctor`.** It lists your mics, records 3 seconds with a level meter, and says which voice and Claude
Code it found. Logs: `void log`.

**Everywhere**

- **He can't hear you:** run `void doctor` and keep talking for the 3 seconds. It shows the mic it recorded (and on a
  2-channel mic, each channel), how loud you came in, and whether there was any talking in it at all. It also saves
  what he heard as `doctor.wav` in Void's data folder, so you can listen. **"NO VOICE … only background noise"** means
  the mic sends no voice to Void: it's muted on the headset (a button, or its own app such as INZONE Hub or
  G HUB), or it's not the mic you're talking into. Pick the right one in Settings → Voice. Headsets that put your
  voice on the second channel only are handled automatically since 1.5.4. (Since 1.4.3,
  mics with long names, or two devices with the same name, are found reliably; update if yours wasn't.)
- **Can't click something under him:** turn on Settings → Look → "Click through around him". In the top bar only the
  area around his eyes takes clicks anyway.
- **"I can't find Claude Code":** install [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and run `claude`
  once to sign in.
- **Speech stutters:** run `void stress`. He says a few sentences four times — on a quiet computer, with every CPU core
  flat out, with Void itself busy, and with both — and it tells you if the voice ever ran dry and how close it came. If
  the test says your voice is too slow for the computer, pick a lighter one.
- **"not allowed: Bash":** you're on "Look, don't touch". Switch it in Settings → Brain.

**Windows**

- **"Windows protected your PC" when running VoidSetup.exe:** click **More info → Run anyway**. The installer isn't
  code-signed yet; it's built from this repo by GitHub Actions.
- **"DLL load failed" or "faster-whisper not installed" in `void log`:** the Microsoft Visual C++ runtime is missing.
  Install it from [aka.ms/vs/17/release/vc_redist.x64.exe](https://aka.ms/vs/17/release/vc_redist.x64.exe), then
  `void restart`.
- **The mic level stays near zero:** check the plug is in the mic jack (often pink), try the other set of jacks, and
  check the level in Sound settings → Recording → your mic → Properties → Levels.
- **Older NVIDIA cards (GTX 10-series and before):** since 1.5.0 Void asks the card which number formats it handles and
  stays on the GPU (`void doctor` shows e.g. `small.en on cuda (int8_float32)`). If it still says "using CPU", copy the
  whole `void doctor` output into a bug report.
- **"GPU speech-to-text failed … switching to CPU":** it still works, just slower. For an NVIDIA card, add the CUDA
  libraries: `uv pip install --python "$env:LOCALAPPDATA\void\venv\Scripts\python.exe" -U nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"`, then `void restart`.

**Linux**

- **He doesn't stay on top / looks like a normal window:** run `void doctor` — the first lines say whether he's an
  overlay here and why not. Check `gtk-layer-shell` is installed (Debian/Ubuntu: `gir1.2-gtklayershell-0.1`). If he
  can't be an overlay he uses XWayland instead, so XWayland needs to be available too.
- **He flashes and disappears:** WebKit is crashing on your GPU driver (common on NVIDIA + Wayland). The launcher
  retries with safer render modes and remembers the one that works. Pick one yourself with `void render nocomp`
  (steadiest on NVIDIA), or reset with `void render default`.
- **Black box around him:** nothing is doing transparency. On a bare X11 window manager start a compositor (`picom`).
- **Screenshots fail:** install `grim slurp` (Hyprland, sway and other wlroots desktops), `spectacle` (KDE),
  `gnome-screenshot` (GNOME) or `maim` (X11). For the clipboard, install `wl-clipboard` or `xclip`.
- His voice is played by the server straight to PipeWire/Pulse (not the window), with almost a second of sound kept
  buffered. `"voice_out": "page"` in the config plays it in the window instead. His face and his voice run on separate
  CPU cores on computers with 4+ cores; `"cpu_split": false` turns that off.

## How it works

```
 your mic ──► speech-to-text ──► Claude Code ──► his voice ──► your speakers
              (faster-whisper)   (on your PC)    (Kokoro)
```

The server records the mic itself (PipeWire / Pulse / ALSA on Linux, the Windows audio system on Windows), speech-to-text
is [faster-whisper](https://github.com/SYSTRAN/faster-whisper), the voice is
[Kokoro](https://github.com/thewh1teagle/kokoro-onnx) (or piper / espeak-ng as fallbacks), and every request goes to
Claude Code on your machine. The server only listens on `127.0.0.1` behind a per-launch token, so nothing else on your
network can talk to him.

---

<p align="center"><a href="../README.md">← Back to the README</a></p>
