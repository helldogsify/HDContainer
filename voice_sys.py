# -*- coding: utf-8 -*-
"""
Системный слой голосового ввода — только ctypes, без сторонних пакетов.

  * Recorder      — запись микрофона через winmm waveIn (16 кГц, моно, 16 бит)
  * clipboard     — чтение/запись/снимок буфера обмена (со всеми HGLOBAL-форматами)
  * send_keys     — эмуляция нажатий (SendInput): Ctrl+V, Ctrl+Insert
  * uia_probe     — UI Automation: что в фокусе (поле ввода?) и что выделено
  * protect/unprotect — DPAPI для API-ключей в настройках
"""

import array
import base64
import ctypes
import io
import math
import threading
import time
import wave
from collections import deque
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm")
ole32 = ctypes.WinDLL("ole32")
oleaut32 = ctypes.WinDLL("oleaut32")
crypt32 = ctypes.WinDLL("crypt32")

HWND = wintypes.HWND
UINT = wintypes.UINT
DWORD = wintypes.DWORD
WORD = wintypes.WORD
BOOL = wintypes.BOOL
HANDLE = wintypes.HANDLE
LPCWSTR = wintypes.LPCWSTR
LONG = wintypes.LONG


def _decl(fn, restype, argtypes):
    fn.restype = restype
    fn.argtypes = argtypes


# ---------------------------------------------------------------------------
#  Клавиатура
# ---------------------------------------------------------------------------
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_ESCAPE = 0x1B
VK_INSERT = 0x2D
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_ADD = 0x6B
VK_V = 0x56

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

# клавиши, которым при эмуляции нужен флаг «extended» (навигационный блок и т.п.)
_EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
                 0x6F, 0x90, 0xA3, 0xA5, 0x5B, 0x5C, 0x5D}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


_decl(user32.SendInput, UINT, [UINT, ctypes.POINTER(INPUT), ctypes.c_int])
_decl(user32.GetAsyncKeyState, ctypes.c_short, [ctypes.c_int])
_decl(user32.MapVirtualKeyW, UINT, [UINT, UINT])
_decl(user32.GetKeyNameTextW, ctypes.c_int, [LONG, wintypes.LPWSTR, ctypes.c_int])
_decl(user32.RegisterHotKey, BOOL, [HWND, ctypes.c_int, UINT, UINT])
_decl(user32.UnregisterHotKey, BOOL, [HWND, ctypes.c_int])
_decl(user32.GetForegroundWindow, HWND, [])
_decl(user32.GetWindowThreadProcessId, DWORD, [HWND, ctypes.POINTER(DWORD)])
_decl(user32.GetClassNameW, ctypes.c_int, [HWND, wintypes.LPWSTR, ctypes.c_int])

MODIFIER_VKS = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def _key_input(vk, up):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = user32.MapVirtualKeyW(vk, 0) & 0xFF
    fl = KEYEVENTF_KEYUP if up else 0
    if vk in _EXTENDED_VKS:
        fl |= KEYEVENTF_EXTENDEDKEY
    inp.u.ki.dwFlags = fl
    return inp


def wait_modifiers_released(timeout=1.2):
    """Ждём, пока пользователь отпустит Shift/Ctrl/Alt/Win (после горячей клавиши)."""
    end = time.time() + timeout
    while time.time() < end:
        if not any(key_down(v) for v in MODIFIER_VKS):
            return True
        time.sleep(0.02)
    return False


def send_combo(mods, vk):
    """Нажать сочетание (mods — список VK модификаторов). Если физически ещё
    зажаты другие модификаторы — логически «отпускаем» их, чтобы Ctrl+V не
    превратился в Ctrl+Shift+V."""
    seq = []
    for m in MODIFIER_VKS:
        if key_down(m) and m not in mods:
            seq.append(_key_input(m, True))
    for m in mods:
        seq.append(_key_input(m, False))
    seq.append(_key_input(vk, False))
    seq.append(_key_input(vk, True))
    for m in reversed(mods):
        seq.append(_key_input(m, True))
    arr = (INPUT * len(seq))(*seq)
    return user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))


def vk_name(vk):
    ext = 1 if vk in _EXTENDED_VKS else 0
    sc = user32.MapVirtualKeyW(vk, 0)
    buf = ctypes.create_unicode_buffer(64)
    n = user32.GetKeyNameTextW((sc << 16) | (ext << 24), buf, 64) if sc else 0
    if vk == VK_ADD:
        return "Num +"
    if n:
        return buf.value
    return "VK%02X" % vk


def hotkey_label(mods, vk):
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_WIN:
        parts.append("Win")
    parts.append(vk_name(vk))
    return " + ".join(parts)


def current_mods():
    m = 0
    if key_down(VK_CONTROL):
        m |= MOD_CONTROL
    if key_down(VK_MENU):
        m |= MOD_ALT
    if key_down(VK_SHIFT):
        m |= MOD_SHIFT
    if key_down(VK_LWIN) or key_down(VK_RWIN):
        m |= MOD_WIN
    return m


def class_name(h):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, buf, 256)
    return buf.value


def window_pid(h):
    pid = DWORD(0)
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return pid.value


# ---------------------------------------------------------------------------
#  Каретка (GetGUIThreadInfo) — дешёвый признак «курсор стоит в поле ввода»
# ---------------------------------------------------------------------------
class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("flags", DWORD), ("hwndActive", HWND),
                ("hwndFocus", HWND), ("hwndCapture", HWND), ("hwndMenuOwner", HWND),
                ("hwndMoveSize", HWND), ("hwndCaret", HWND), ("rcCaret", wintypes.RECT)]


_decl(user32.GetGUIThreadInfo, BOOL, [DWORD, ctypes.POINTER(GUITHREADINFO)])


def caret_info(fg):
    """(hwndFocus, hwndCaret) для потока окна fg."""
    tid = user32.GetWindowThreadProcessId(fg, None)
    gi = GUITHREADINFO()
    gi.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(gi)):
        return 0, 0
    return gi.hwndFocus or 0, gi.hwndCaret or 0


# ---------------------------------------------------------------------------
#  Микрофон (winmm waveIn)
# ---------------------------------------------------------------------------
WAVE_MAPPER = 0xFFFFFFFF
WAVE_FORMAT_PCM = 1
WHDR_DONE = 0x00000001


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", WORD), ("nChannels", WORD), ("nSamplesPerSec", DWORD),
                ("nAvgBytesPerSec", DWORD), ("nBlockAlign", WORD),
                ("wBitsPerSample", WORD), ("cbSize", WORD)]


class WAVEHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", DWORD),
                ("dwBytesRecorded", DWORD), ("dwUser", ctypes.c_size_t),
                ("dwFlags", DWORD), ("dwLoops", DWORD), ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_size_t)]


class WAVEINCAPSW(ctypes.Structure):
    _fields_ = [("wMid", WORD), ("wPid", WORD), ("vDriverVersion", UINT),
                ("szPname", ctypes.c_wchar * 32), ("dwFormats", DWORD),
                ("wChannels", WORD), ("wReserved1", WORD)]


_HWAVEIN = ctypes.c_void_p
_decl(winmm.waveInGetNumDevs, UINT, [])
_decl(winmm.waveInGetDevCapsW, UINT, [ctypes.c_size_t, ctypes.POINTER(WAVEINCAPSW), UINT])
_decl(winmm.waveInOpen, UINT, [ctypes.POINTER(_HWAVEIN), UINT, ctypes.POINTER(WAVEFORMATEX),
                               ctypes.c_size_t, ctypes.c_size_t, DWORD])
_decl(winmm.waveInPrepareHeader, UINT, [_HWAVEIN, ctypes.POINTER(WAVEHDR), UINT])
_decl(winmm.waveInUnprepareHeader, UINT, [_HWAVEIN, ctypes.POINTER(WAVEHDR), UINT])
_decl(winmm.waveInAddBuffer, UINT, [_HWAVEIN, ctypes.POINTER(WAVEHDR), UINT])
_decl(winmm.waveInStart, UINT, [_HWAVEIN])
_decl(winmm.waveInReset, UINT, [_HWAVEIN])
_decl(winmm.waveInClose, UINT, [_HWAVEIN])


def list_mics():
    out = []
    for i in range(winmm.waveInGetNumDevs()):
        caps = WAVEINCAPSW()
        if winmm.waveInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            out.append(caps.szPname)
    return out


def _mic_index(name):
    if not name:
        return WAVE_MAPPER
    for i, n in enumerate(list_mics()):
        if n == name:
            return i
    return WAVE_MAPPER               # устройство пропало — берём системное по умолчанию


class Recorder:
    """Пишет микрофон в фоне, пока не вызван stop(). level — громкость 0..1
    для анимации, levels — история последних уровней."""
    RATE = 16000
    CHUNK = 1600                     # 50 мс (16 кГц × 2 байта × 0,05)
    NBUF = 12

    def __init__(self, device_name=""):
        self.device_name = device_name
        self.level = 0.0
        self.levels = deque([0.0] * 48, maxlen=48)
        self.peak_rms = 0.0
        self.chunks = []
        self.error = None
        self._stop = threading.Event()
        self._opened = threading.Event()
        self._thr = None

    def start(self):
        self._thr = threading.Thread(target=self._run, daemon=True)
        self._thr.start()
        self._opened.wait(2.0)
        return self.error is None

    def _consume(self, data):
        if not data:
            return
        self.chunks.append(data)
        a = array.array("h")
        a.frombytes(data[: len(data) // 2 * 2])
        if not a:
            return
        rms = math.sqrt(sum(x * x for x in a) / len(a)) / 32768.0
        self.peak_rms = max(self.peak_rms, rms)
        db = 20 * math.log10(rms + 1e-9)
        lvl = max(0.0, min(1.0, (db + 52) / 40))
        self.level = lvl if lvl > self.level else self.level * 0.6 + lvl * 0.4
        self.levels.append(lvl)

    def _run(self):
        h = _HWAVEIN()
        fmt = WAVEFORMATEX(WAVE_FORMAT_PCM, 1, self.RATE, self.RATE * 2, 2, 16, 0)
        rc = winmm.waveInOpen(ctypes.byref(h), _mic_index(self.device_name),
                              ctypes.byref(fmt), 0, 0, 0)
        if rc != 0:
            self.error = "mic_open_%d" % rc
            self._opened.set()
            return
        bufs = [ctypes.create_string_buffer(self.CHUNK) for _ in range(self.NBUF)]
        hdrs = [WAVEHDR() for _ in range(self.NBUF)]
        hsz = ctypes.sizeof(WAVEHDR)
        for b, hd in zip(bufs, hdrs):
            hd.lpData = ctypes.addressof(b)
            hd.dwBufferLength = self.CHUNK
            winmm.waveInPrepareHeader(h, ctypes.byref(hd), hsz)
            winmm.waveInAddBuffer(h, ctypes.byref(hd), hsz)
        winmm.waveInStart(h)
        self._opened.set()
        idx = 0
        try:
            while True:
                hd = hdrs[idx]
                if hd.dwFlags & WHDR_DONE:
                    self._consume(bufs[idx].raw[:hd.dwBytesRecorded])
                    hd.dwFlags &= ~WHDR_DONE
                    hd.dwBytesRecorded = 0
                    winmm.waveInAddBuffer(h, ctypes.byref(hd), hsz)
                    idx = (idx + 1) % self.NBUF
                    continue
                if self._stop.is_set():
                    break
                time.sleep(0.01)
            # reset возвращает все буферы (с тем, что успело записаться) — дочитываем по порядку
            winmm.waveInReset(h)
            for k in range(self.NBUF):
                hd = hdrs[(idx + k) % self.NBUF]
                if hd.dwFlags & WHDR_DONE and hd.dwBytesRecorded:
                    self._consume(bufs[(idx + k) % self.NBUF].raw[:hd.dwBytesRecorded])
        finally:
            for hd in hdrs:
                winmm.waveInUnprepareHeader(h, ctypes.byref(hd), hsz)
            winmm.waveInClose(h)

    def stop(self):
        self._stop.set()
        if self._thr:
            self._thr.join(2.0)

    @property
    def duration(self):
        return sum(len(c) for c in self.chunks) / float(self.RATE * 2)

    def wav_bytes(self):
        bio = io.BytesIO()
        w = wave.open(bio, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(self.RATE)
        w.writeframes(b"".join(self.chunks))
        w.close()
        return bio.getvalue()


# ---------------------------------------------------------------------------
#  Буфер обмена
# ---------------------------------------------------------------------------
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

_decl(user32.OpenClipboard, BOOL, [HWND])
_decl(user32.CloseClipboard, BOOL, [])
_decl(user32.EmptyClipboard, BOOL, [])
_decl(user32.GetClipboardData, HANDLE, [UINT])
_decl(user32.SetClipboardData, HANDLE, [UINT, HANDLE])
_decl(user32.EnumClipboardFormats, UINT, [UINT])
_decl(user32.GetClipboardSequenceNumber, DWORD, [])
_decl(user32.RegisterClipboardFormatW, UINT, [LPCWSTR])
_decl(user32.GetClipboardFormatNameW, ctypes.c_int, [UINT, wintypes.LPWSTR, ctypes.c_int])
_decl(kernel32.GlobalAlloc, HANDLE, [UINT, ctypes.c_size_t])
_decl(kernel32.GlobalLock, ctypes.c_void_p, [HANDLE])
_decl(kernel32.GlobalUnlock, BOOL, [HANDLE])
_decl(kernel32.GlobalSize, ctypes.c_size_t, [HANDLE])
_decl(kernel32.GlobalFree, HANDLE, [HANDLE])

# форматы, у которых данные — НЕ HGLOBAL (GDI-объекты, display-форматы): не копируем
_SKIP_FORMATS = {2, 3, 9, 14, 0x80, 0x81, 0x82, 0x83, 0x8E}
_CLIP_MAX = 32 * 1024 * 1024


CLIP_HWND = None                     # окно-владелец буфера (задаёт приложение)


def _open_clip(retries=25):
    for _ in range(retries):
        if user32.OpenClipboard(CLIP_HWND):
            return True
        time.sleep(0.02)
    return False


def clip_seq():
    return user32.GetClipboardSequenceNumber()


def _hglobal_bytes(hmem):
    size = kernel32.GlobalSize(hmem)
    if not size or size > _CLIP_MAX:
        return None
    p = kernel32.GlobalLock(hmem)
    if not p:
        return None
    try:
        return ctypes.string_at(p, size)
    finally:
        kernel32.GlobalUnlock(hmem)


def _set_bytes(fmt, data):
    hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE, max(1, len(data)))
    if not hmem:
        return False
    p = kernel32.GlobalLock(hmem)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(hmem)
    if not user32.SetClipboardData(fmt, hmem):
        kernel32.GlobalFree(hmem)
        return False
    return True


def clip_get_text():
    if not _open_clip():
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def clip_get_format(name):
    """Данные именованного формата (bytes) или None."""
    fmt = user32.RegisterClipboardFormatW(name)
    if not _open_clip():
        return None
    try:
        h = user32.GetClipboardData(fmt)
        return _hglobal_bytes(h) if h else None
    finally:
        user32.CloseClipboard()


def clip_set_text(text, exclude_history=False):
    if not _open_clip():
        return False
    try:
        user32.EmptyClipboard()
        ok = _set_bytes(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
        if exclude_history:
            # временная вставка не должна оседать в журнале буфера (Win+V) и облаке
            for nm in ("ExcludeClipboardContentFromMonitorProcessing",
                       "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
                fmt = user32.RegisterClipboardFormatW(nm)
                _set_bytes(fmt, b"\0\0\0\0")
        return ok
    finally:
        user32.CloseClipboard()


def clip_snapshot():
    """Снимок всех «переносимых» форматов буфера: [(fmt, bytes)]."""
    out = []
    if not _open_clip():
        return None
    try:
        fmt = user32.EnumClipboardFormats(0)
        total = 0
        while fmt:
            if fmt not in _SKIP_FORMATS and not (0x300 <= fmt <= 0x3FF):
                h = user32.GetClipboardData(fmt)
                data = _hglobal_bytes(h) if h else None
                if data is not None and total + len(data) <= _CLIP_MAX:
                    out.append((fmt, data))
                    total += len(data)
            fmt = user32.EnumClipboardFormats(fmt)
    finally:
        user32.CloseClipboard()
    return out


def clip_restore(snap):
    if snap is None or not _open_clip():
        return False
    try:
        user32.EmptyClipboard()
        for fmt, data in snap:
            _set_bytes(fmt, data)
        return True
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------------------
#  UI Automation (ручной вызов COM-vtable, как IPropertyStore в основном файле)
# ---------------------------------------------------------------------------
class GUID(ctypes.Structure):
    _fields_ = [("Data1", DWORD), ("Data2", WORD), ("Data3", WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(s):
    g = GUID()
    ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
    return g


class VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("data", ctypes.c_ubyte * 16)]


_decl(ole32.CoInitializeEx, ctypes.c_long, [ctypes.c_void_p, DWORD])
_decl(ole32.CoUninitialize, None, [])
_decl(ole32.CoCreateInstance, ctypes.c_long,
      [ctypes.POINTER(GUID), ctypes.c_void_p, DWORD, ctypes.POINTER(GUID),
       ctypes.POINTER(ctypes.c_void_p)])
_decl(ole32.CLSIDFromString, ctypes.c_long, [ctypes.c_wchar_p, ctypes.POINTER(GUID)])
_decl(oleaut32.VariantClear, ctypes.c_long, [ctypes.POINTER(VARIANT)])
_decl(oleaut32.SysFreeString, None, [ctypes.c_void_p])

CLSID_CUIAutomation = _guid("{ff48dba4-60ef-4201-aa87-54103eef594e}")
IID_IUIAutomation = _guid("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")
IID_IUIAutomationTextPattern = _guid("{32eba289-3583-42c9-9c59-3b6d9a1e9b6a}")

UIA_TextPatternId = 10014
UIA_ProcessIdPropertyId = 30002
UIA_ControlTypePropertyId = 30003
UIA_HasKeyboardFocusPropertyId = 30008
UIA_IsKeyboardFocusablePropertyId = 30009
UIA_IsEnabledPropertyId = 30010
UIA_ClassNamePropertyId = 30012
UIA_IsValuePatternAvailablePropertyId = 30043
UIA_ValueIsReadOnlyPropertyId = 30046
UIA_IsTextPatternAvailablePropertyId = 30040

UIA_ComboBoxControlTypeId = 50003
UIA_EditControlTypeId = 50004
UIA_DocumentControlTypeId = 50030

VT_I4, VT_BSTR, VT_BOOL = 3, 8, 11


def _vcall(obj, index, argtypes, *args):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


def _release(obj):
    if obj:
        try:
            _vcall(obj, 2, [])
        except Exception:
            pass


def _prop(el, pid):
    v = VARIANT()
    if _vcall(el, 10, [ctypes.c_int, ctypes.POINTER(VARIANT)], pid, ctypes.byref(v)) != 0:
        return None
    try:
        if v.vt == VT_I4:
            return ctypes.c_int.from_buffer_copy(bytes(v.data[:4])).value
        if v.vt == VT_BOOL:
            return ctypes.c_short.from_buffer_copy(bytes(v.data[:2])).value != 0
        if v.vt == VT_BSTR:
            p = ctypes.c_void_p.from_buffer_copy(bytes(v.data[:8])).value
            return ctypes.wstring_at(p) if p else ""
        return None
    finally:
        oleaut32.VariantClear(ctypes.byref(v))


def _selection_text(el, limit=200000):
    tp = ctypes.c_void_p()
    hr = _vcall(el, 14, [ctypes.c_int, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
                UIA_TextPatternId, ctypes.byref(IID_IUIAutomationTextPattern), ctypes.byref(tp))
    if hr != 0 or not tp.value:
        return None
    arr = ctypes.c_void_p()
    try:
        if _vcall(tp.value, 5, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(arr)) != 0 \
                or not arr.value:
            return None
        n = ctypes.c_int(0)
        _vcall(arr.value, 3, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(n))
        parts = []
        for i in range(n.value):
            rng = ctypes.c_void_p()
            if _vcall(arr.value, 4, [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                      i, ctypes.byref(rng)) != 0 or not rng.value:
                continue
            try:
                bstr = ctypes.c_void_p()
                if _vcall(rng.value, 12, [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                          limit, ctypes.byref(bstr)) == 0 and bstr.value:
                    parts.append(ctypes.wstring_at(bstr.value))
                    oleaut32.SysFreeString(bstr.value)
            finally:
                _release(rng.value)
        return "\n".join(parts)
    finally:
        _release(arr.value)
        _release(tp.value)


def uia_probe(fg):
    """Что сейчас в фокусе. -> dict:
       editable: True/False/None (None — не удалось понять)
       selection: str | None (None — TextPattern недоступен, выделение неизвестно)
       ctrl, cls — для лога."""
    res = {"editable": None, "selection": None, "ctrl": 0, "cls": "", "uia": False}
    hr_init = ole32.CoInitializeEx(None, 0)          # MTA
    auto = ctypes.c_void_p()
    el = ctypes.c_void_p()
    try:
        if ole32.CoCreateInstance(ctypes.byref(CLSID_CUIAutomation), None, 1,
                                  ctypes.byref(IID_IUIAutomation), ctypes.byref(auto)) != 0:
            return res
        if _vcall(auto.value, 8, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(el)) != 0 \
                or not el.value:
            return res
        pid = _prop(el.value, UIA_ProcessIdPropertyId)
        if fg and pid and pid != window_pid(fg):
            return res                                  # фокус не в окне, где нажали хоткей
        res["uia"] = True
        ctrl = _prop(el.value, UIA_ControlTypePropertyId) or 0
        res["ctrl"] = ctrl
        res["cls"] = _prop(el.value, UIA_ClassNamePropertyId) or ""
        has_val = _prop(el.value, UIA_IsValuePatternAvailablePropertyId)
        ro = _prop(el.value, UIA_ValueIsReadOnlyPropertyId) if has_val else None
        enabled = _prop(el.value, UIA_IsEnabledPropertyId)
        if enabled is False:
            res["editable"] = False
        elif ctrl == UIA_EditControlTypeId:
            res["editable"] = ro is not True
        elif ctrl in (UIA_DocumentControlTypeId, UIA_ComboBoxControlTypeId):
            res["editable"] = (ro is False)
        else:
            res["editable"] = False
        if _prop(el.value, UIA_IsTextPatternAvailablePropertyId):
            res["selection"] = _selection_text(el.value) or ""
    except Exception as ex:                             # ctypes ловит и AV через SEH
        res["error"] = repr(ex)
    finally:
        _release(el.value)
        _release(auto.value)
        if hr_init in (0, 1):
            ole32.CoUninitialize()
    return res


# ---------------------------------------------------------------------------
#  Job-объект: дочерние процессы (whisper-server, claude) умирают вместе с нами,
#  даже если нас убили или мы упали
# ---------------------------------------------------------------------------
class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in
                ("r_ops", "w_ops", "o_ops", "r_bytes", "w_bytes", "o_bytes")]


class _JOB_BASIC(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", DWORD),
                ("SchedulingClass", DWORD)]


class _JOB_EXT(ctypes.Structure):
    _fields_ = [("Basic", _JOB_BASIC), ("Io", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


_decl(kernel32.CreateJobObjectW, HANDLE, [ctypes.c_void_p, LPCWSTR])
_decl(kernel32.SetInformationJobObject, BOOL, [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD])
_decl(kernel32.AssignProcessToJobObject, BOOL, [HANDLE, HANDLE])
_JOB = None


def adopt_process(proc):
    """Привязать subprocess.Popen к job «убить при закрытии». Ошибки игнорируем."""
    global _JOB
    try:
        if _JOB is None:
            _JOB = kernel32.CreateJobObjectW(None, None) or 0
            if _JOB:
                info = _JOB_EXT()
                info.Basic.LimitFlags = 0x2000          # KILL_ON_JOB_CLOSE
                kernel32.SetInformationJobObject(_JOB, 9, ctypes.byref(info),
                                                 ctypes.sizeof(info))
        if _JOB:
            kernel32.AssignProcessToJobObject(_JOB, int(proc._handle))
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  DPAPI — ключи в настройках храним зашифрованными под текущего пользователя
# ---------------------------------------------------------------------------
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_decl(crypt32.CryptProtectData, BOOL,
      [ctypes.POINTER(DATA_BLOB), LPCWSTR, ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
       ctypes.c_void_p, DWORD, ctypes.POINTER(DATA_BLOB)])
_decl(crypt32.CryptUnprotectData, BOOL,
      [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
       ctypes.c_void_p, DWORD, ctypes.POINTER(DATA_BLOB)])
_decl(kernel32.LocalFree, ctypes.c_void_p, [ctypes.c_void_p])


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def protect(text):
    if not text:
        return ""
    src, _keep = _blob(text.encode("utf-8"))
    out = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(src), "HDContainer", None, None, None,
                                    0x1, ctypes.byref(out)):
        return ""
    try:
        return "dpapi:" + base64.b64encode(ctypes.string_at(out.pbData, out.cbData)).decode()
    finally:
        kernel32.LocalFree(out.pbData)


def unprotect(s):
    if not s:
        return ""
    if not s.startswith("dpapi:"):
        return s
    try:
        src, _keep = _blob(base64.b64decode(s[6:]))
        out = DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None,
                                          0x1, ctypes.byref(out)):
            return ""
        try:
            return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
        finally:
            kernel32.LocalFree(out.pbData)
    except Exception:
        return ""
