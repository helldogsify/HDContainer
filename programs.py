"""Installed programs for the «which program» pickers (hotkey exclusions, key scripts).

Settings only need an exe name (lower case, what the foreground window's process is called),
so a program does not have to run to be chosen. Sources, all read-only:
  * Start menu shortcuts (all users + current user) -> their .exe targets;
  * Steam libraries (libraryfolders.vdf + appmanifest_*.acf) and Epic Games manifests -> the
    main exe of each game (_main_exes); games rarely have a Start menu .lnk (Steam makes .url).
Helpers, uninstallers, anti-cheats, servers and Windows' own tools are left out: one program =
one row, usually.
The scan takes ~0.5-2 s, so it runs on a worker thread and is cached for the app's lifetime
(refresh() starts it again). No tkinter here.
"""
import ctypes
import glob
import json
import os
import re
import threading
import winreg
from ctypes import wintypes

VP = ctypes.c_void_p
HRESULT = ctypes.c_long
ole32 = ctypes.windll.ole32

# helper executables that are never the window a user plays or works in
_JUNK = re.compile(r"unins|uninst|setup|install|crash|redist|prereq|report|dxweb|dotnet|"
                   r"helper|update|patcher|bugsplat|cefprocess|subprocess|webengine|"
                   r"elevate|notification|repair|activation|register|benchmark|diagnos|"
                   r"anticheat|^eac|eac\.exe$|bootstrap|inject|service|server|console|"
                   r"upload|record|graphics|tools?\.exe$|util|compiler|ffmpeg|"
                   r"^7z|^python|^java|^node|"
                   r"^(cmd|wscript|cscript|msiexec|rundll32|control|mmc|dllhost|conhost|explorer)\.exe$", re.I)
# Start menu entries that are not the program itself
_JUNK_LNK = re.compile(r"uninstall|reset|readme|read me|help|manual|documentation|website|license|"
                       r"release notes|changelog|safe mode|debug|settings|install|repair|"
                       r"удал|справк|сброс|настро", re.I)

_lock = threading.Lock()
_items = None          # list of (display name, exe lower case), sorted by name
_running = False
_listeners = []


class GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]


def _guid(s):
    g = GUID()
    ole32.CLSIDFromString(s, ctypes.byref(g))
    return g


CLSID_ShellLink = _guid("{00021401-0000-0000-C000-000000000046}")
IID_IShellLinkW = _guid("{000214F9-0000-0000-C000-000000000046}")
IID_IPersistFile = _guid("{0000010B-0000-0000-C000-000000000046}")


def _vcall(obj, index, restype, argtypes, *args):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(VP)))[0]
    return ctypes.WINFUNCTYPE(restype, VP, *argtypes)(vtbl[index])(obj, *args)


def _release(obj):
    if obj:
        try:
            _vcall(obj, 2, ctypes.c_ulong, [])
        except Exception:
            pass


def _lnk_targets(paths):
    """{lnk path: target path} for .lnk files; needs COM on the calling thread."""
    out = {}
    psl, ppf = VP(), VP()
    if ole32.CoCreateInstance(ctypes.byref(CLSID_ShellLink), None, 1, ctypes.byref(IID_IShellLinkW),
                              ctypes.byref(psl)) != 0:
        return out
    try:
        if _vcall(psl, 0, HRESULT, [VP, ctypes.POINTER(VP)], ctypes.byref(IID_IPersistFile), ctypes.byref(ppf)) != 0:
            return out
        buf = ctypes.create_unicode_buffer(1024)
        for p in paths:
            try:
                if _vcall(ppf, 5, HRESULT, [wintypes.LPCWSTR, wintypes.DWORD], p, 0) != 0:   # IPersistFile::Load
                    continue
                buf.value = ""
                # IShellLinkW::GetPath(pszFile, cch, pfd = NULL, fFlags = 0)
                if _vcall(psl, 3, HRESULT, [wintypes.LPWSTR, ctypes.c_int, VP, wintypes.DWORD],
                          buf, 1024, None, 0) == 0 and buf.value:
                    out[p] = buf.value
            except Exception:
                pass
    finally:
        _release(ppf)
        _release(psl)
    return out


def _start_menu(add):
    """Start menu shortcuts to .exe files; Windows' own tools (targets under the Windows folder)
    and the Uninstall / Help / Settings entries are left out."""
    roots = [os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
             os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs")]
    windir = os.path.normcase(os.environ.get("WINDIR", r"C:\Windows")) + os.sep
    links = []
    for r in roots:
        if os.path.isdir(r):
            links += [l for l in glob.glob(os.path.join(r, "**", "*.lnk"), recursive=True)
                      if not _JUNK_LNK.search(os.path.splitext(os.path.basename(l))[0])]
    ole32.CoInitializeEx(None, 2)            # apartment threaded; S_FALSE if already done
    try:
        targets = _lnk_targets(links)
    finally:
        ole32.CoUninitialize()
    for lnk, target in targets.items():
        if target.lower().endswith(".exe") and not os.path.normcase(target).startswith(windir):
            add(os.path.splitext(os.path.basename(lnk))[0], target)


def _exes(folder, depth=4, limit=4000):
    """Exe files of a game folder down to Binaries/Win64 depth, support folders skipped."""
    out, seen = [], 0
    base = os.path.normpath(folder).count(os.sep)
    for root, dirs, files in os.walk(folder):
        if root.count(os.sep) - base >= depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not re.search(r"redist|prereq|directx|support|crash|anticheat|battleye|"
                                                     r"dotnet|tools|launcher|engine$|thirdparty|plugins", d, re.I)]
        for f in files:
            seen += 1
            if f.lower().endswith(".exe"):
                out.append(os.path.join(root, f))
        if seen > limit:
            break
    return out


def _tokens(*names):
    out = set()
    for n in names:
        for t in re.split(r"[^a-z0-9]+", (n or "").lower()):
            if len(t) >= 4 and t not in ("edition", "remastered", "definitive", "special", "game", "the"):
                out.add(t)
    return out


def _main_exes(folder, *names):
    """The exe(s) whose window a game shows: an Unreal «*-Shipping.exe» if there is one (the
    launcher next to it only starts it), else the exes named after the game (title or folder
    words), else the biggest exe. 32-bit / ARM / launcher twins are dropped when a plain one
    exists."""
    exes = [e for e in _exes(folder) if not _JUNK.search(os.path.basename(e))]
    if not exes:
        return []
    stem = lambda e: os.path.splitext(os.path.basename(e))[0].lower()
    ship = [e for e in exes if stem(e).endswith("-shipping")]
    if ship:
        pick = ship
    else:
        toks = _tokens(*names)
        pick = [e for e in exes if any(t in stem(e) for t in toks)]
        starts = [e for e in pick if any(stem(e).startswith(t) for t in toks)]
        pick = starts or pick
        if not pick:
            try:
                pick = [max(exes, key=os.path.getsize)]
            except OSError:
                pick = exes[:1]
    plain = [e for e in pick if not re.search(r"arm64|launcher|32$|x86$", stem(e))]
    pick = plain or pick
    if len(pick) > 1:                        # wallpaper32 + wallpaper64: one bitness is enough
        sixty = [e for e in pick if re.search(r"64", stem(e))]
        if sixty and len(sixty) < len(pick) and all(re.sub(r"64", "", stem(e)) in {re.sub(r"32", "", stem(x)) for x in pick} for e in sixty):
            pick = sixty
    seen, out = set(), []
    for e in pick:
        if stem(e) not in seen:
            seen.add(stem(e))
            out.append(e)
    return out[:3]


def _vdf_paths(text):
    return [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', text)]


def _steam(add):
    steam = None
    for hive, key, val in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                           (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, key) as k:
                steam = os.path.normpath(winreg.QueryValueEx(k, val)[0])
                break
        except OSError:
            pass
    if not steam:
        return
    add("Steam", os.path.join(steam, "steam.exe"))
    libs = {steam}
    try:
        with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
            libs |= {os.path.normpath(p) for p in _vdf_paths(f.read())}
    except OSError:
        pass
    for lib in libs:
        for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
            try:
                with open(acf, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            try:
                t = raw.decode("utf-8")
            except UnicodeDecodeError:
                t = raw.decode("cp1252", errors="replace")
            name = re.search(r'"name"\s+"([^"]*)"', t)
            inst = re.search(r'"installdir"\s+"([^"]*)"', t)
            if not inst:
                continue
            title = name.group(1) if name else inst.group(1)
            if re.search(r"redistributable|steamworks|proton|steam linux runtime|dedicated server|sdk$", title, re.I):
                continue
            for exe in _main_exes(os.path.join(lib, "steamapps", "common", inst.group(1)), title, inst.group(1)):
                add(title, exe, True)


def _epic(add):
    d = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), r"Epic\EpicGamesLauncher\Data\Manifests")
    for item in glob.glob(os.path.join(d, "*.item")):
        try:
            with open(item, encoding="utf-8", errors="replace") as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        loc, title = m.get("InstallLocation") or "", m.get("DisplayName") or ""
        if not loc or not os.path.isdir(loc):
            continue
        exes = _main_exes(loc, title, os.path.basename(loc))
        if not exes and m.get("LaunchExecutable"):
            exes = [os.path.join(loc, m["LaunchExecutable"])]
        for e in exes:
            add(title, e, True)


def scan():
    """[(display name, exe lower case)] sorted by name, one row per exe. A game's name wins
    over a shortcut's name; the junk filter applies to every source."""
    best = {}                       # exe -> (name, from a game library)

    def add(name, path, game=False):
        exe = os.path.basename(path or "").strip().lower()
        if not exe.endswith(".exe") or (_JUNK.search(exe) and not game):
            return
        name = (name or "").strip()
        old = best.get(exe)
        if (old is None or (game and not old[1])
                or (game == old[1] and name and (not old[0] or len(name) < len(old[0])))):
            best[exe] = (name, game)        # several shortcuts: the shortest name («VLC media player»)
    for src in (_steam, _epic, _start_menu):
        try:
            src(add)
        except Exception:
            pass
    rows = [(n or os.path.splitext(e)[0], e) for e, (n, _g) in best.items()]
    rows.sort(key=lambda r: (r[0].lower(), r[1]))
    return rows


def _worker():
    global _items, _running
    try:
        rows = scan()
    except Exception:
        rows = []
    with _lock:
        _items, _running = rows, False
        ls = list(_listeners)
        _listeners.clear()
    for cb in ls:
        try:
            cb()
        except Exception:
            pass


def refresh():
    global _running
    with _lock:
        if _running:
            return
        _running = True
    threading.Thread(target=_worker, name="programs-scan", daemon=True).start()


def get(on_ready=None):
    """Cached rows, or None while the first scan runs (it is started if needed). on_ready is
    called from the worker thread when a running scan ends: it must not touch tkinter."""
    with _lock:
        rows, busy = _items, _running
        if on_ready is not None and (rows is None or busy):
            _listeners.append(on_ready)
    if rows is None and not busy:
        refresh()
    return rows
