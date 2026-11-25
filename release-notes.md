## Lighter and faster: almost no load while idle, 3x quicker start, memory only when needed

A full performance pass over the whole program. Features work as before; the only visible changes are listed under "Memory only when needed".

- **Idle load cut by ~95-100%.** Hotkeys, screenshots, the recorder, screen recording, key scripts, devices and boxes used to check their queues 20-50 times a second each, about 235 wake-ups a second in total. Now they sleep until something actually happens. Measured CPU per idle minute: 1.3 -> 0.02 s on a desktop PC, 0.33 -> 0.00 s and 1.83 -> 0.08 s on two laptops.
- **Starts about 3x faster.** The installer now puts the program unpacked into its folder instead of one big exe that unpacked ~80 MB into the temp folder at every start: 1.1 -> 0.3 s on a desktop PC, 1.0 -> 0.4 s and 4.5-8.6 -> 1.4-2.5 s on laptops. The portable exe of the release stays a single file.
- **Half the processes, a third less memory.** No more launcher process next to the program and its watchdog; the watchdog no longer loads the whole program (29 -> 13 MB). Memory the program no longer uses is given back to Windows when you are not working in its windows (working set ~70 -> ~10 MB after start).
- **One keyboard hook instead of five.** Dictation, screenshots, the recorder, screen recording and key scripts share one low-level keyboard hook, so every key press in Windows passes one hook instead of up to five, and hotkeys react without the 20-25 ms polling delay.
- **Memory only when needed:**
  - The local speech model (Whisper) is no longer kept loaded: it starts while you speak and is unloaded after 3 idle minutes (was 20). "Keep the model loaded" in Settings is now off by default and was switched off once on update; you can turn it back on.
  - The torrent engine runs only while something downloads (or seeds): a paused torrent leaves it, and at start it is not even loaded for paused torrents. Resume continues where it stopped.
  - The spare Claude Code process of "Share Claude Code" stops after 3 unused minutes.
  - The double-click-on-the-desktop mouse hook of the boxes is active only while the desktop is in front.
- **Boxes** notice new files at once through Windows change notifications instead of re-reading every box folder every 2 seconds; container previews are taken when you leave the container instead of every 4 seconds.
- **Devices link:** fewer packets, sockets and wake-ups while it is on.
- The crash log is cut when it grows over 1 MB.
