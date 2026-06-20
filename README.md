<div align="center">

<img src="assets/icon-1024.png" width="128" alt="HDContainer">

# HDContainer

**Group any Windows windows into one container — one taskbar button, native everything.**

[![Download](https://img.shields.io/badge/Download-Setup.exe-4c8bf5?style=for-the-badge)](https://github.com/helldogsify/HDContainer/releases/latest)
[![Release](https://img.shields.io/github/v/release/helldogsify/HDContainer?style=for-the-badge)](https://github.com/helldogsify/HDContainer/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge)](LICENSE)

</div>

---

HDContainer lets you bundle several independent program windows — VS Code, Chrome, a folder, anything — into a single **container** that behaves as one app: **one taskbar button**, one Alt+Tab entry, shared minimize (incl. <kbd>Win</kbd>+<kbd>D</kbd>). Unlike window-embedding hacks, each window stays a real top-level window, so **keyboard, clipboard and keyboard-layout switching (Alt+Shift) keep working natively** — no freezes, no input glitches.

It lives entirely in the **system tray**. No main window gets in your way.

## ✨ Features

- **True grouping** — multiple windows → one taskbar button & one Alt+Tab entry.
- **Native input** — full keyboard, clipboard and global layout switching per window. Built on Win32 *window ownership*, never `SetParent` reparenting.
- **Place windows anywhere** — a container is an invisible full-screen owner; arrange or maximize windows freely.
- **Multiple containers at once** — each its own taskbar button.
- **Color labels** — tint a container's icon panel so you can tell them apart at a glance.
- **Custom name & icon** per container.
- **Desktop shortcuts** — one click reopens a container and **relaunches its apps in their last positions** (even folders reopen to the same path).
- **Smart minimize** — minimizing a window inside a container temporarily detaches it (its own taskbar button); restoring snaps it back.
- **7 languages** — English, Русский, Español, Português, Deutsch, Français, 中文 (auto-detected).
- **Start with Windows** and **automatic updates** from GitHub releases.
- **Tiny & dependency-free** — a single Python file using only `tkinter` + `ctypes`.

<div align="center">
<img src="assets/color-labels.png" width="420" alt="Color labels">
<br><sub>Per-container color labels</sub>
</div>

## ⬇️ Install

1. Download **[HDContainer-Setup.exe](https://github.com/helldogsify/HDContainer/releases/latest)** from the latest release.
2. Run it — installs per-user, **no admin rights needed**.
3. Launch **HDContainer** from the Start menu / desktop. Look for its icon in the **system tray**.

The app checks GitHub for updates and can update itself (toggle in **Settings**).

## 🚀 Usage

Right-click the tray icon:

- **Create container…** → name it, then pick windows from the Alt+Tab-style preview grid (multi-select).
- **Per container**: activate/deactivate, add windows, rename, set icon, **color label**, create a desktop shortcut, delete.
- **Settings…** → language, start-with-Windows, auto-update, a mini-guide, and credits.

**Tips**
- Quit from the tray (**Quit**) to release all windows safely.
- A desktop shortcut launches a container and reopens its apps — great for one-click workspaces.

## 🛠️ Build from source

Requirements: Windows, [Python 3.10+](https://www.python.org/), [PyInstaller](https://pyinstaller.org/), and [Inno Setup 6](https://jrsoftware.org/isdl.php) (for the installer).

```powershell
pip install pyinstaller
# build the exe + installer
powershell -ExecutionPolicy Bypass -File build.ps1
```

Or just the portable exe:

```powershell
python -m PyInstaller --onefile --noconsole --name HDContainer --icon HDContainer.ico --clean -y window_container.py
```

## 🧠 How it works (the short version)

Each active container is an invisible, click-through, full-screen **owner window**. Member windows are made *owned* by it via `SetWindowLongPtr(GWLP_HWNDPARENT)` — **not** reparented. Ownership gives, for free: members float above the (invisible) host, hide/show with it (group minimize, Win+D), and drop off the taskbar/Alt+Tab — all **without merging input queues**, which is exactly why native keyboard and the global Alt+Shift layout switch keep working. A unique per-window AppUserModelID keeps each container as its own taskbar button.

## 💛 Support

Vibe-coded by **hdk** with [Claude Code](https://claude.com/claude-code). Free for everyone.

If it saved you some sanity, you can tip the author:

**USDT · TRON (TRC20)**
```
TWG8Y5EyaqQf8GsJKJVhcaAMFZxxHoPWzC
```

## 📄 License

[MIT](LICENSE) © hdk
