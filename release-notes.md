Security and reliability release after a full audit of the code base.

**Updates**
- The downloaded installer is verified against the SHA-256 digest GitHub publishes for the release; only the asset named `HDContainer-Setup.exe` is accepted; pre-release tags are ignored; "No" on the update prompt skips that version instead of asking again every 3 hours.
- A portable copy no longer runs the installer silently (it opens the release page instead).
- Releases are built from pinned actions and packages, published only once both files are uploaded, and the exe carries version info.

**Containers**
- Container and settings files are written atomically; a damaged entry no longer wipes the other containers.
- Saved window positions are moved back on screen when the monitor they were on is gone.
- The tray icon comes back after Explorer restarts; a second instance started at the same time (autostart + shortcut) no longer runs twice.
- Fixed icon handle leaks when changing a container's color or avatar.

**Voice input**
- Hold-to-talk with a non-modifier hotkey (F9, Ctrl+Space…) no longer stops after half a second.
- A plain key (Enter, Space, a letter) can't be chosen as the hotkey anymore — it would stop working in every app.
- Multi-line results are copied, not pasted, into terminals (each pasted line would run as a command).
- If the paste is rejected (window running as administrator), the text stays in the clipboard instead of being lost.
- Requests with API keys no longer follow redirects (keys could leak to another host); non-JSON responses give a readable error.
- "Claude on another computer" server: request size limit, socket timeouts, no port sharing, failed logins logged, Claude started without MCP servers.
- Dictated text is no longer written to the debug log; the log is trimmed on start.

**Installer**
- Uninstall now asks before deleting the Whisper models too, removes the autostart entry enabled from Settings, and waits for the crash-recovery helper to exit before deleting files.
- Warns when an older all-users installation exists that this per-user setup can't update.
