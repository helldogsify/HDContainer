## Devices: your computers open each other's drives

- New **Settings -> Devices**. Computers with HDContainer on the same network find each other by themselves. Press **Link**, check that both screens show the same 6-digit code, press **Link** on the other computer, and the two stay linked from then on.
- Linked devices are listed in the tray menu with a green dot when they are online. Click one to open its files: drives, Desktop, Downloads and Documents on the left, the folder on the right.
- Open a file with a double click, download files and whole folders, rename, delete into the Recycle Bin of that computer, upload with the button or by dragging files and folders in from Explorer. Progress and speed are shown at the bottom.
- Direct over your Wi-Fi or cable, no server in between; when the two are on different networks and Tailscale is running, the link goes through Tailscale. On a 5 GHz Wi-Fi laptop a 100 MB file went at 60 MB/s, about twice as fast as copying it over Tailscale with scp.
- A linked device has full access to all drives, so pairing needs the code on both screens, and everything is encrypted (AES-256-GCM, keys agreed at pairing and stored protected by Windows).
- Switching it on adds one Windows Firewall rule for HDContainer, limited to the local network and Tailscale (Windows may ask once).
- `lan_cli.py` in the source is a command line for scripts: list devices, browse, copy files both ways, rename, delete.
