<div align="center">

<img src="assets/icon-1024.png" width="128" alt="HDContainer">

# HDContainer

### One small tray app for Windows that does seven everyday jobs: groups a task's windows into one app, keeps your desktop tidy in boxes, turns your voice into text, records your calls, takes and annotates screenshots, converts files from the right‑click menu, downloads torrents, and opens the drives of your other PCs.

[![Download](https://img.shields.io/badge/Download-Setup.exe-4c8bf5?style=for-the-badge)](https://github.com/helldogsify/HDContainer/releases/latest)
[![Release](https://img.shields.io/github/v/release/helldogsify/HDContainer?style=for-the-badge)](https://github.com/helldogsify/HDContainer/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge)](LICENSE)

</div>

---

## What's inside

| | |
|---|---|
| **[Window containers](#window-containers)** | A set of windows becomes one program: one taskbar button, one Alt+Tab entry. Switch your whole workspace with a single keystroke. |
| **[Desktop boxes](#desktop-boxes)** | Panels on the desktop that hold files and shortcuts. They roll up into a title bar, open on hover, line up with even gaps and stay under your windows. |
| **[Voice input](#voice-input)** | Hold **Left Alt** and talk. The text appears where your cursor is, cleaned up by an LLM. Select text first and your words become an instruction for it. |
| **[Recorder](#recorder)** | Press **Ctrl+Shift+D** and everything the computer plays plus your microphone goes into one audio file — a Zoom or Meet call with both sides. Press again and an MP3 lands on your Desktop. |
| **[Screenshots](#screenshots)** | Press **Ctrl+Shift+A**: the screen freezes, drag to pick a region or click for the whole screen. Draw, mark and crop in a small editor, then one button puts it in the clipboard. Captures are kept for 30 days. |
| **[Key scripts](#key-scripts)** | One key becomes a sequence in the program you choose: press **E** in a game and it gets **E, 2, E** with human-like random timing. Switch each script on and off from the tray menu. |
| **[Convert to](#convert-files-from-the-right-click-menu)** | Right‑click any image, audio, video, document or table in Explorer → **Convert to** → pick a format. |
| **[Torrents](#torrents)** | Open a `.torrent` file and it starts downloading. Progress, pause and delete are in the tray menu. |
| **[Devices](#devices)** | Your computers with HDContainer on one Wi‑Fi find each other. Link them once with a code, then open any drive of the other PC from the tray: browse, download, upload, drag files in. |

Everything lives in the **system tray** — there is no main window. Click the tray icon for the menu; **Settings** has a section for each tool. Installs per‑user, **no admin rights**, updates itself.

## Window containers

### The problem

You almost never work in a single app. A task is a **set of windows open together** — an editor, a browser to check your changes, a terminal, maybe a show playing on the side. That's your rig for *that* task.

Then you switch to something else — writing a document that you want full‑screen. When you come back to coding, you're mashing **Alt+Tab** to dig out each window and dragging them back into place. Every single time. And after a reboot you rebuild the whole layout from scratch.

### What a container does

It makes a set of windows behave like **one program**. You drop everything a task needs into one **container** — from then on it's a single taskbar button and a single Alt+Tab entry.

So switching tasks becomes **one keystroke**: Alt+Tab and your entire working set flips at once — coding rig out, writing rig in, right where you left it. No window hunting, no re‑arranging.

> Coding → **Alt+Tab** → your research paper. One press, the whole set of tools in front of you changed.

And it's **not** a hack that jams windows inside another and breaks your typing. Every window stays a normal window, so the keyboard, clipboard and the **Alt+Shift** layout switch all keep working — HDContainer only *groups* them.

### How you use it

- **Make a container** (tray → **Create container**), give it a name, and pick its windows from an Alt+Tab‑style preview grid.
- **Run several containers at once** — each is its own taskbar button with a real live preview. Alt+Tab between them like between apps.
- **Minimize and restore the whole set** from its taskbar button or with Win+D. Windows come back exactly as they were — full‑screen ones full‑screen, the rest at their size and position.
- **Minimize one window** inside a container and it steps out on its own; restore it and it snaps back into the group. Minimizing the last visible window minimizes the container.
- **Color‑label** a container so you can tell your "work" rig from your "research" rig at a glance.
- **Give it an icon** — drop in any image (PNG, JPG, WebP…) like setting an avatar; it's cropped to a square (transparency kept) and becomes the container's taskbar and shortcut icon.
- **Save it as a desktop shortcut** — one click reopens the container. Windows that are still open snap back exactly where they were; apps that were closed get relaunched (their exact size/position can't always be reproduced). Your workspace survives reboots.
- **Tick a container in the tray menu** to switch it on or off — no digging into submenus.

<div align="center">
<img src="assets/color-labels.png" width="420" alt="Color labels">
<br><sub>Color-label each container to tell your workspaces apart</sub>
</div>

## Desktop boxes

A box is a panel on the desktop that holds files, folders and shortcuts. Boxes live in the desktop layer itself: they stay under every window, are there after Win+D, and never show up in the taskbar or Alt+Tab.

- **Rolled up until you need it**: a box sits on the desktop as just its title and opens as soon as you hover it, rolling back up when the mouse leaves. An open box is exactly as tall as its icons need (down to the bottom of the screen, then it scrolls). A red counter on the title shows files that arrived since you last looked.
- **Make a box** from the tray menu (**New box**) or by right‑clicking any box. Drag files, folders and shortcuts in from the desktop or any folder; drag them out to the desktop, another box, a folder or any app (a chat, a browser upload…). **This PC, the Recycle Bin** and the other standard desktop icons move into a box too, and come back to the desktop when you take them out.
- **It works like Explorer**: double‑click opens, right‑click shows the full Explorer menu (Open with, Send to, archivers, Properties…), Ctrl+click selects several, Cut in Explorer and **Paste** into a box. Double‑click a box's title to rename it.
- **Boxes line up by themselves**: drag one next to another and it sticks with the same gap as the boxes already there, its edges align with its neighbours and the screen edges; dragging the right edge snaps the width to whole columns of icons.
- **Arrange them in one click** in **Settings → Boxes → Arrange**: along the top or the bottom (left, center or right) or in a column at the left or right edge. Width and gap have sliders and change the boxes as you move them. A box in the lower half of the screen opens upwards, with its title as a footer.
- **Your look** in **Settings → Boxes**, for all boxes or for one box: background color, background transparency, transparency under the cursor, the title font (any installed font, size, bold) and its color; rounded corners, a border, the new‑file counter and the icon size. A live preview on your wallpaper shows the result, and the boxes on the desktop follow as you change things.
- **Hide everything** with a double‑click on an empty spot of the desktop: boxes and desktop icons disappear and come back the same way.
- **Your own order**: press an icon and move it inside the box — it lifts and the others glide aside, like app icons on a phone. Drag it past the edge to take it out.
- **More in the box menu**: sort by name, type, date or your own order, lock position and size, keep a box visible on double‑click, refresh icons, open the box folder.
- **A box for any folder**: a box can show a folder of your choice — Downloads, a project folder — instead of its own.
- **Moving from iTop Easy Desktop**: **Settings → Boxes → Import boxes from iTop Easy Desktop** recreates your boxes with their names, positions and roll‑up.

The files really move: each box is a folder in `Desktop\HDContainer Boxes` (hidden, so the desktop stays clean). Nothing is kept in a database and no driver is installed — uninstalling HDContainer turns that folder back into a normal one with all your files.

## Voice input

Hold **Left Alt** anywhere and talk (any key or combination can be set instead). A small animated indicator appears above the clock while HDContainer listens; let go and your speech is turned into text.

- **Nothing selected**: dictation. Speech is recognized, then cleaned up by an LLM (punctuation, misheard words, filler words) and typed into the text field under your cursor. If the cursor isn't in a text field, the text goes to the clipboard.
- **Text selected**: what you say is an instruction for that text, like *"translate into English"*, *"tidy this up"* or *"check for mistakes"*. The result replaces the selection, or goes to the clipboard if the text can't be edited. If you ask a question about the text (*"what does this mean?"*), the answer goes to the clipboard and your text is left untouched.
- **Commands while dictating.** Start or end your dictation with an instruction, like *"…see you at ten. Translate to English"*, *"make it more formal"* or *"as a list"*. It is applied to the text and left out of the result. Commands are looked for mainly at the beginning and the end. A phrase addressed to someone else (*"Masha, translate this contract into English"*) stays part of your message.
- **Dictation history.** Every result is kept on your PC (the last 200), so a text pasted into the wrong place or overwritten in the clipboard isn't lost: tray → **Dictation history** to search, copy it again or delete. Can be turned off.

Everything is set up in **Settings → Voice input**:

| | Options |
|---|---|
| Speech recognition | **Local Whisper** ([whisper.cpp](https://github.com/ggml-org/whisper.cpp)): offline, free and private. The model (base / small / large‑v3‑turbo) is downloaded on first use and kept in memory while you use it. Or any **OpenAI‑compatible** transcription API (Groq, OpenAI, your own server). |
| Text processing | **Claude Code**: uses your Claude subscription through the `claude` CLI, with no API key. **Anthropic API key.** **Any OpenAI‑compatible API**: OpenAI, OpenRouter, Gemini, Groq, DeepSeek, Mistral, xAI, or local Ollama / LM Studio. |
| Hotkey | Left Alt by default, or any key or combination; *hold to talk* or *press to start / press to stop*. Left Alt pressed together with another key (Alt+Tab, Alt+Shift…) works as usual. Esc cancels. |
| Behavior | Paste automatically or just copy; keep your original clipboard; editable LLM instructions; a vocabulary hint for names and terms. |

API keys are stored encrypted with Windows DPAPI under your user account.

**One Claude Code for all your PCs.** On the computer that has Claude Code, turn on *Voice input → Share Claude Code with other computers*. On your other computers, choose *Claude on another computer* and paste the address and key it shows. Those computers then need neither Claude Code nor an API key. The shared endpoint is OpenAI‑compatible, accepts only private networks (Tailscale, LAN) and requires the key. It keeps a Claude process warmed up, and the other PC asks it to warm up as soon as you start talking.

## Recorder

For quick recordings of work calls. Press **Ctrl+Shift+D** (or tray → **Recorder: start recording**) and HDContainer records everything the computer plays — the other people in Zoom, Google Meet, Telegram, a video — together with your microphone, into one file. Press the same keys again, pick **Stop recording** in the tray, or click the indicator, and the recording is saved to your Desktop (or a folder you choose) as MP3, M4A or WAV.

- While it records, a small red pill left of the clock shows the time and the sound levels; a click stops it. It can be turned off.
- All default playback devices are recorded, so call apps that play through the "communications" device are heard too; plugging in headphones mid-call is followed.
- Audio is written to disk as it comes: if the app or the PC crashes, the recording is finished and saved at the next start.
- MP3 and M4A are about 0.7 MB per minute (encoded by Windows itself, nothing to install). With headphones the result is cleaner: without them the microphone hears the speakers too.

Check the rules of your country and of the people you call: in many places a call may be recorded only when everyone knows.

## Screenshots

Press **Ctrl+Shift+A** (any key or combination can be set instead) and the screen freezes. Drag to select a region, or just click to take the whole screen. Esc cancels.

The capture opens in a small editor: **pen**, **marker**, **frame**, **arrow** and **crop**, six colors, three line widths, Ctrl+Z to undo. **Copy to clipboard** (or Enter) closes the editor with the result in the clipboard, ready to paste into a chat, a document or an image editor; **Save as…** writes a PNG or JPEG file.

Every capture is kept on your PC for 30 days (7 to 90 in Settings), like the dictation history: tray → **Screenshot history** shows them as thumbnails; copy one again, open it in the editor or delete it. The editor can be skipped: *Settings → Screenshots → Copy to the clipboard right away*.

## Key scripts

*Settings → Key scripts → New script*: pick the **key**, the keys it **presses** in order, and the **programs** it works in (it never fires anywhere else). Each script has a switch in the tray menu.

- The pause between keys and how long each key is held are random within the ranges you set, spread the way people press keys (most presses near the typical value, a few longer), never the same twice. Under each range a dot says whether it looks human: green, yellow with advice, red when no person presses that fast or that evenly.
- If the first key of the sequence is the trigger itself (E → E, 2, E), your own press goes to the program as it is, and the rest follows after you release it.
- *Enter pauses the script until the next Enter*: Enter opens a game chat, Enter sends it (Esc closes it), and in between the key works as usual. Only Enter in the script's program counts.
- The keys are sent as scan codes, the way games read them. Programs running as administrator do not accept keys from a normal program, and games with anti-cheat can tell synthetic keys apart: check the game's rules before using scripts there.

## Convert files from the right-click menu

Right-click any image, audio, video, document or data file in Explorer (or a whole selection of them) → **Convert to** → pick a format. The result lands next to the original with the same name and the new extension (`report.docx` → `report.md`; if the name is taken you get `report (2).md`). A selection is converted one file after another in a single progress window.

| Files | Converts to | Engine |
|---|---|---|
| png, jpg, webp, heic/heif, avif, bmp, gif, tiff, ico | png, jpg, webp, heic, avif, bmp, gif, ico, tiff, pdf | Pillow + libheif (built in) |
| svg | png, jpg, pdf | Edge/Chrome, headless |
| mp3, wav, ogg, flac, m4a, aac, wma, opus, aiff, amr | mp3, wav, ogg, flac, m4a, aac, opus | [FFmpeg](https://github.com/BtbN/FFmpeg-Builds) |
| mp4, mkv, mov, avi, webm, wmv, m4v, ts, flv, mpg, 3gp, mts, vob | mp4, mkv, webm, mov, avi, gif + mp3/wav/m4a (audio track) | FFmpeg |
| srt, vtt, ass, ssa | srt, vtt, ass | FFmpeg |
| md, docx, html, txt, rtf, odt, epub, fb2, tex, rst, org | **pdf**, docx, md, html, txt, rtf, odt, epub, fb2, tex, rst | [Pandoc](https://github.com/jgm/pandoc) (+ Edge for pdf) |
| pdf | png, jpg (one image per page), txt, html, docx, md, epub, odt, rtf | [Poppler](https://github.com/oschwartz10612/poppler-windows) (+ Pandoc) |
| csv, tsv, json, xlsx (first sheet) | csv, tsv, json, xlsx, html, pdf | built in |

FFmpeg (~190 MB), Pandoc (~40 MB) and Poppler (~45 MB) are downloaded from their GitHub releases the first time they're needed — or in advance from **Settings → Converter** — and verified against the SHA‑256 digest GitHub publishes. PDF output uses the Microsoft Edge (or Chrome) already on the PC in headless mode. On Windows 11 the item is under **Show more options** (or Shift + right‑click). The menu is a few per‑user registry keys — no admin rights; turn it off in **Settings → Converter**, and it is removed on uninstall.

## Torrents

Open a `.torrent` file with HDContainer and it starts downloading right away — no windows, no questions: the indicator above the clock shows **Download started** with the name, so you know it was taken. Each download shows up in the tray menu with its progress, speed and time left, plus two buttons: pause/resume and delete (it asks first, and removes the partly downloaded files). Click a name to open its folder. When a download finishes, the indicator above the clock shows its name — click it to open the folder. Tray → **Download history** keeps the last 200 finished downloads with their date, size and folder: search by name, click one to open it in Explorer. Removing an entry or clearing the history never touches the files.

**Settings → Torrents** holds the save folder (your Downloads by default) and the presets, all on by default: maximum speed with no limits, forced seed requests while seeds are scarce, merging seeds from every tracker, the DHT and a second copy of the same torrent, leaving the swarm as soon as the download finishes, and removing finished downloads from the list. The download history can be turned off there too. Unfinished downloads continue after a restart. Built on [libtorrent](https://libtorrent.org/).

The installer makes HDContainer the handler for `.torrent` files. If another torrent client keeps opening them, right‑click a `.torrent` → **Open with** → **HDContainer** → **Always**. The first download makes Windows ask whether HDContainer may use the network — allow it, or the speed drops.

## Devices

**Settings → Devices → Link my devices on the local network**. Every computer with HDContainer on the same network shows up under **Nearby**; press **Link**, check that the other computer shows the same 6‑digit code and press **Link** there. From then on the two are linked for good: they find each other by themselves whenever they share a network, and the tray menu lists your devices with a green dot when they are online.

Click a device to open its files: drives and the Desktop, Downloads and Documents folders on the left, the folder on the right. Double‑click opens a file (it is copied over first), right‑click downloads a file or a whole folder, renames or deletes (into the Recycle Bin of that computer). Upload files or folders with the button in the corner or just drag them in from Explorer. Progress and speed show at the bottom.

- **Direct and fast**: the computers talk to each other straight over your Wi‑Fi or cable, without a server in between.
- **Private**: a linked device has full access to all drives of the other one, so linking needs the code on both screens; everything that travels between them is encrypted (AES‑256‑GCM with keys agreed at pairing, kept protected by Windows).
- **Away from home**: if you use [Tailscale](https://tailscale.com/), a linked device is reached through it when the two are not on the same network.
- The first time you switch it on Windows may ask to allow HDContainer through the firewall (one prompt); the rule allows only your local network and Tailscale.

## Install

1. Download **[HDContainer-Setup.exe](https://github.com/helldogsify/HDContainer/releases/latest)**.
2. Run it — installs per‑user, **no admin rights**.
3. Launch **HDContainer**; it sits in the system tray. Click its icon to open the menu.

It quietly checks GitHub for new versions and can update itself (**Settings → General**); the downloaded installer is checked against the SHA‑256 digest GitHub publishes for the release before it runs. Prefer no installer? Grab the portable **HDContainer.exe** from the same release — a portable copy only tells you about new versions and opens the release page, it doesn't install anything on its own, and `.torrent` files aren't associated with it.

The executables aren't code‑signed yet, so on first run Windows SmartScreen may show "Windows protected your PC" — choose **More info → Run anyway**.

Available in English, Русский, Español, Português, Deutsch, Français and 中文 (auto‑detected, switchable in Settings).

## How it works under the hood

Each active container is an invisible **owner window** — a real, minimizable window kept fully transparent via a layered surface (so Win+D and the taskbar button genuinely minimize the whole group). Member windows are made *owned* by it via `SetWindowLongPtr(GWLP_HWNDPARENT)` — they are **not** reparented. Ownership alone gives you: members float above the (invisible) host, hide and show with it (group minimize, Win+D), and collapse into one taskbar/Alt+Tab entry — all **without merging input queues**, which is exactly why native typing and the global Alt+Shift layout switch keep working. A unique per‑window AppUserModelID keeps each container as its own taskbar button.

The app is Python — `tkinter` for the UI and `ctypes` for the Win32 work — packed into one exe with PyInstaller:

| Part | Files | Built on |
|---|---|---|
| Tray, containers, settings, updater | `window_container.py` | stdlib + [Pillow](https://python-pillow.org/) for icons |
| Desktop boxes | `boxes.py` | layered child windows of the desktop icon layer drawn with Pillow; Explorer thumbnails, context menus and OLE drag‑and‑drop through COM |
| Voice input | `voice.py`, `voice_engines.py`, `voice_sys.py` | microphone through `winmm`, selection and text fields through UI Automation, pasting with `SendInput`; whisper.cpp server downloaded on first use; LLM connectors are plain HTTP or a `claude` subprocess |
| Converter | `convert.py` | Pillow + pillow‑heif; FFmpeg, Pandoc and Poppler downloaded on demand; Edge/Chrome for PDF |
| Torrents | `torrents.py` | [libtorrent](https://libtorrent.org/) |
| Key scripts | `macros.py` | low-level keyboard hook (installed only while a script is on) and `SendInput` with scan codes |
| Devices | `lan.py`, `lan_crypto.py`, `lan_ui.py`, `lan_cli.py` | UDP broadcast discovery, TCP with a Diffie‑Hellman pairing code, AES‑GCM from Windows CNG (`bcrypt.dll`); `lan_cli.py` is a command line for scripts |

## Build from source

Needs Windows, [Python 3.10+](https://www.python.org/) (releases are built with 3.12), and [Inno Setup 6](https://jrsoftware.org/isdl.php) for the installer.

```powershell
pip install pyinstaller pillow pillow-heif libtorrent
powershell -ExecutionPolicy Bypass -File build.ps1   # dist\HDContainer.exe + dist\HDContainer-Setup.exe
```

The build stops if the torrent module doesn't import, and the release workflow checks voice input and the converter the same way, so a missing package can't silently drop a feature from the exe.

Releases are built by GitHub Actions: a push to `main` with a new version number in `installer.iss` and `window_container.py` builds both files on a Windows runner and publishes release `v<version>` with `release-notes.md` as its description.

## Support

Vibe‑coded by **hdk** with [Claude Code](https://claude.com/claude-code). Free for everyone.

If it saved you some window‑juggling, you can tip the author:

**USDT · TRON (TRC20)**
```
TWG8Y5EyaqQf8GsJKJVhcaAMFZxxHoPWzC
```

## License

[MIT](LICENSE) © hdk
