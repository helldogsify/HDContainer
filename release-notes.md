## Reliability: no lost settings, boxes or files

This release fixes the problems found in a full code review.

**Settings, containers and boxes are never lost**
- A damaged settings, containers or boxes file (for example after a power cut) used to be replaced with empty data at the next save. Now the damaged file is kept, the last good copy (`.bak`) is loaded instead, and nothing empty is ever written over it.

**Containers**
- A container no longer collects duplicate entries for the same program each time it is opened, so it no longer starts extra copies of programs or grabs unrelated windows.
- A window removed in the container editor is really forgotten and does not come back next time.
- A container shortcut brings an already open container to the front.
- At Windows shutdown the windows of other programs are released properly.
- The "Update?" question no longer pops up over a fullscreen game, and never twice.
- Window previews, folder lookups and scheduled tasks no longer freeze the app for a moment.

**Boxes**
- Deleting a box no longer hides files it could not move back (a file open in a program): the box stays and says which files are left.
- Selected files stay selected correctly when new files appear in the box.
- Documents, Downloads, Pictures, OneDrive and similar folders are added to a box as links, never moved into it.
- A box over a network or USB folder no longer freezes the app.
- A box no longer sticks to the cursor after Alt+Tab during a click.
- "Refresh icons" really refreshes; less memory and graphics use.

**Voice input**
- The Gemini and Groq keys are shared only with your own PCs over Tailscale, and every hand-out is logged.
- Unsaved changes in Settings no longer affect dictation.
- Dictation can no longer get stuck in "busy".
- Groq limits are respected (waits after a 429 answer), the vocabulary hint is shortened for Groq.

**Converter, screenshots, torrents**
- PDF to PNG/JPG never overwrites existing files next to the PDF.
- Cancel no longer leaves a cut-off file; conversions started right after a batch are not lost.
- Photos keep their orientation and colours; CSV files from Russian Excel (cp1251, `;`) convert correctly.
- The screenshot overlay can never keep the mouse blocked.
- A new torrent never writes into an existing file or folder of the same name; deleting a torrent always deletes its files.
- Setup no longer takes over `.torrent` files when another torrent client handles them.
