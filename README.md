<div align="center">

<img src="assets/icon-1024.png" width="128" alt="HDContainer">

# HDContainer

### Group the windows for a task into one app — and switch your whole workspace with a single Alt+Tab.

[![Download](https://img.shields.io/badge/Download-Setup.exe-4c8bf5?style=for-the-badge)](https://github.com/helldogsify/HDContainer/releases/latest)
[![Release](https://img.shields.io/github/v/release/helldogsify/HDContainer?style=for-the-badge)](https://github.com/helldogsify/HDContainer/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge)](LICENSE)

</div>

---

## The problem

You almost never work in a single app. A task is a **set of windows open together** — an editor, a browser to check your changes, a terminal, maybe a show playing on the side. That's your rig for *that* task.

Then you switch to something else — writing a document that you want full‑screen. When you come back to coding, you're mashing **Alt+Tab** to dig out each window and dragging them back into place. Every single time. And after a reboot you rebuild the whole layout from scratch.

## What HDContainer does

It makes a set of windows behave like **one program**. You drop everything a task needs into one **container** — from then on it's a single taskbar button and a single Alt+Tab entry.

So switching tasks becomes **one keystroke**: Alt+Tab and your entire working set flips at once — coding rig out, writing rig in, right where you left it. No window hunting, no re‑arranging.

> Coding → **Alt+Tab** → your research paper. One press, the whole set of tools in front of you changed.

And it's **not** a hack that jams windows inside another and breaks your typing. Every window stays a normal window, so the keyboard, clipboard and the **Alt+Shift** layout switch all keep working — HDContainer only *groups* them.

It lives in the **system tray** — no main window in your way.

## How you use it

- **Make a container**, give it a name, and pick its windows from an Alt+Tab‑style preview grid.
- **Run several containers at once** — each is its own taskbar button. Alt+Tab between them like between apps.
- **Color‑label** a container so you can tell your "work" rig from your "research" rig at a glance.
- **Give it an icon** — drop in any image (PNG, JPG, WebP…) like setting an avatar; it's cropped to a square (transparency kept) and becomes the container's taskbar and shortcut icon.
- **Save it as a desktop shortcut** — one click reopens the container. Windows that are still open snap back exactly where they were; apps that were closed get relaunched (their exact size/position can't always be reproduced). Your workspace survives reboots.
- **Tick a container in the tray menu** to switch it on or off — no digging into submenus.
- **Minimize one window** inside a container and it steps out on its own; restore it and it snaps back into the group.

<div align="center">
<img src="assets/color-labels.png" width="420" alt="Color labels">
<br><sub>Color-label each container to tell your workspaces apart</sub>
</div>

## Voice input

Hold **Right Ctrl** anywhere and talk (any key or combination can be set instead). A small animated indicator appears above the clock while HDContainer listens; let go and your speech is turned into text.

- **Nothing selected**: dictation. Speech is recognized, then cleaned up by an LLM (punctuation, misheard words, filler words) and typed into the text field under your cursor. If the cursor isn't in a text field, the text goes to the clipboard.
- **Text selected**: what you say is an instruction for that text, like *"translate into English"*, *"tidy this up"* or *"check for mistakes"*. The result replaces the selection, or goes to the clipboard if the text can't be edited. If you ask a question about the text (*"what does this mean?"*), the answer goes to the clipboard and your text is left untouched.

- **Commands while dictating.** Start or end your dictation with an instruction, like *"…see you at ten. Translate to English"*, *"make it more formal"* or *"as a list"*. It is applied to the text and left out of the result. Commands are looked for mainly at the beginning and the end. A phrase addressed to someone else (*"Masha, translate this contract into English"*) stays part of your message.

Everything is set up in **tray → Voice input**:

| | Options |
|---|---|
| Speech recognition | **Local Whisper** ([whisper.cpp](https://github.com/ggml-org/whisper.cpp)): offline, free and private. The model (base / small / large‑v3‑turbo) is downloaded on first use and kept in memory while you use it. Or any **OpenAI‑compatible** transcription API (Groq, OpenAI, your own server). |
| Text processing | **Claude Code**: uses your Claude subscription through the `claude` CLI, with no API key. **Anthropic API key.** **Any OpenAI‑compatible API**: OpenAI, OpenRouter, Gemini, Groq, DeepSeek, Mistral, xAI, or local Ollama / LM Studio. |
| Hotkey | Right Ctrl by default, or any key or combination; *hold to talk* or *press to start / press to stop*. Right Ctrl pressed together with another key (Ctrl+C…) works as usual. Esc cancels. |
| Behavior | Paste automatically or just copy; keep your original clipboard; editable LLM instructions; a vocabulary hint for names and terms. |

API keys are stored encrypted with Windows DPAPI under your user account.

**One Claude Code for all your PCs.** On the computer that has Claude Code, turn on *Voice input → Share Claude Code with other computers*. On your other computers, choose *Claude on another computer* and paste the address and key it shows. Those computers then need neither Claude Code nor an API key. The shared endpoint is OpenAI‑compatible, accepts only private networks (Tailscale, LAN) and requires the key. It keeps a Claude process warmed up, and the other PC asks it to warm up as soon as you start talking.

## Install

1. Download **[HDContainer-Setup.exe](https://github.com/helldogsify/HDContainer/releases/latest)**.
2. Run it — installs per‑user, **no admin rights**.
3. Launch **HDContainer**; it sits in the system tray. Right‑click it to start.

It quietly checks GitHub for new versions and can update itself (toggle in **Settings**). Prefer no installer? Grab the portable **HDContainer.exe** from the same release.

Available in English, Русский, Español, Português, Deutsch, Français and 中文 (auto‑detected, switchable in Settings).

## How it works under the hood

Each active container is an invisible **owner window** — a real, minimizable window kept fully transparent via a layered surface (so Win+D and the taskbar button genuinely minimize the whole group). Member windows are made *owned* by it via `SetWindowLongPtr(GWLP_HWNDPARENT)` — they are **not** reparented. Ownership alone gives you: members float above the (invisible) host, hide and show with it (group minimize, Win+D), and collapse into one taskbar/Alt+Tab entry — all **without merging input queues**, which is exactly why native typing and the global Alt+Shift layout switch keep working. A unique per‑window AppUserModelID keeps each container as its own taskbar button.

Mostly stdlib: `tkinter` + `ctypes`, plus [Pillow](https://python-pillow.org/) for container icons (decoding any image, square‑cropping, writing the `.ico`) and the voice indicator. Voice input lives in `voice*.py`: the microphone is read through `winmm`, the selection and the focused text field are detected through UI Automation, and the result is pasted with `SendInput`. The Claude Code, Anthropic and OpenAI‑compatible connectors are plain HTTP or subprocess calls, so there are no extra packages.

## Build from source

Needs Windows, [Python 3.10+](https://www.python.org/) with [PyInstaller](https://pyinstaller.org/), and [Inno Setup 6](https://jrsoftware.org/isdl.php) for the installer.

```powershell
pip install pyinstaller pillow
powershell -ExecutionPolicy Bypass -File build.ps1   # exe + installer
```

Portable exe only:

```powershell
python -m PyInstaller --onefile --noconsole --name HDContainer --icon HDContainer.ico --clean -y window_container.py
```

## Support

Vibe‑coded by **hdk** with [Claude Code](https://claude.com/claude-code). Free for everyone.

If it saved you some window‑juggling, you can tip the author:

**USDT · TRON (TRC20)**
```
TWG8Y5EyaqQf8GsJKJVhcaAMFZxxHoPWzC
```

## License

[MIT](LICENSE) © hdk
