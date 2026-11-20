# -*- coding: utf-8 -*-
"""Video engine of the screen recorder: GDI capture of a screen rectangle (with or without
the pointer), H.264 through the Media Foundation sink writer, and a final remux into a plain
MP4 together with the sound recorded by rec_audio (AAC).

No third-party packages: COM through raw vtables (the helpers of rec_audio).

While recording, video goes into a FRAGMENTED MP4 (playable up to the last fragment even if
the app dies) and the sound into the crash-tolerant WAV of rec_audio. After the stop, mux()
copies the H.264 samples as they are (no re-encode) into a normal MP4 and encodes the WAV
into an AAC track next to them. Both tracks share the QPC clock: the WAV starts at the
mixer's t0, the video at its first frame, and the difference is stored as audio_offset.

Windows marked WDA_EXCLUDEFROMCAPTURE (our indicator, the frame around the region) do not
appear in a BitBlt of the screen: verified on Windows 11 26200 (checks/probe_bitblt.py).
"""
import os
import time
import ctypes
import threading
import traceback
from ctypes import wintypes

import rec_audio as ra
from rec_audio import vcall, release, guid, GUID, COMError

# ---------------------------------------------------------------------------
#  Media Foundation constants
# ---------------------------------------------------------------------------
MFMediaType_Video = guid("73646976-0000-0010-8000-00AA00389B71")
MFVideoFormat_H264 = guid("34363248-0000-0010-8000-00AA00389B71")
MFVideoFormat_RGB32 = guid("00000016-0000-0010-8000-00AA00389B71")
MF_MT_FRAME_SIZE = guid("1652C33D-D6B2-4012-B834-72030849A37D")
MF_MT_FRAME_RATE = guid("C459A2E8-3D2C-4E44-B132-FEE5156C7BB0")
MF_MT_PIXEL_ASPECT_RATIO = guid("C6376A1E-8D0A-4027-BE45-6D9A0AD39BB6")
MF_MT_INTERLACE_MODE = guid("E2724BB8-E676-4806-B4B2-A8D6EFB44CCD")
MF_MT_AVG_BITRATE = guid("20332624-FB0D-4D9E-BD0D-CBF6786C102E")
MF_MT_DEFAULT_STRIDE = guid("644B4E48-1E02-4516-B0EB-C01CA9D49AC6")
MF_MT_MPEG2_PROFILE = guid("AD76A80B-2D5C-4E0B-B375-64E520137036")
MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS = guid("A634A91C-822B-41B9-A494-4DE4643612B0")
MF_TRANSCODE_CONTAINERTYPE = guid("150FF23F-4ABC-478B-AC4F-E1916FBA1CCA")
MFTranscodeContainerType_FMPEG4 = guid("9BA876F1-419F-4B77-A1E0-35959D9D4004")
MFTranscodeContainerType_MPEG4 = guid("DC6CD05D-B9D0-40EF-BD35-FA622C1AB28A")
IID_ICodecAPI = guid("901DB4C7-31CE-41A2-85DC-8FA0BF41B8DA")
CODECAPI_AVEncCommonRateControlMode = guid("1C0608E9-370C-4710-8A58-CB6181C42423")
CODECAPI_AVEncCommonQuality = guid("FCBF57A3-7EA5-4B0C-9644-69B40C39C391")
GUID_NULL = GUID()
MFVideoInterlace_Progressive = 2
H264_PROFILE_HIGH = 100
RC_QUALITY = 3                                  # eAVEncCommonRateControlMode_Quality
SOURCE_FIRST_VIDEO = 0xFFFFFFFC
SOURCE_ALL_STREAMS = 0xFFFFFFFE
READERF_ERROR, READERF_EOS = 0x1, 0x2
SET_U32 = ra.SET_U32
SET_GUID = ra.SET_GUID
SET_U64 = (ctypes.POINTER(GUID), ctypes.c_uint64)
SET_UNK = (ctypes.POINTER(GUID), ctypes.c_void_p)
VT_UI4 = 19

MAX_W, MAX_H = 3840, 2160                       # larger regions are scaled down to fit (H.264 levels)
MIN_SIDE = 64
AUDIO_AVG = 16000                               # AAC 128 kbit/s mono
AUDIO_TAIL = 2000000                            # hns of sound kept past the last video frame
QUALITY_BPP = {"normal": 0.07, "high": 0.14}    # bits per pixel per frame -> mean bitrate
QUALITY_LEVEL = {"normal": 60, "high": 80}      # for encoders that take a quality mode


def _mfapi():
    return ra._mf()


def _attrs(items):
    """IMFAttributes with (guid, value) items: int -> UINT32, GUID -> GUID."""
    mp, _ = _mfapi()
    a = ctypes.c_void_p()
    hr = mp.MFCreateAttributes(ctypes.byref(a), max(1, len(items)))
    if hr < 0:
        raise COMError(hr, "MFCreateAttributes")
    for k, v in items:
        if isinstance(v, GUID):
            vcall(a, 24, SET_GUID, ctypes.byref(k), ctypes.byref(v), what="SetGUID")
        else:
            vcall(a, 21, SET_U32, ctypes.byref(k), int(v), what="SetUINT32")
    return a


def _video_type(sub, w, h, fps, bitrate=None, stride=None):
    mp, _ = _mfapi()
    t = ctypes.c_void_p()
    hr = mp.MFCreateMediaType(ctypes.byref(t))
    if hr < 0:
        raise COMError(hr, "MFCreateMediaType")
    vcall(t, 24, SET_GUID, ctypes.byref(ra.MF_MT_MAJOR_TYPE), ctypes.byref(MFMediaType_Video), what="SetGUID")
    vcall(t, 24, SET_GUID, ctypes.byref(ra.MF_MT_SUBTYPE), ctypes.byref(sub), what="SetGUID")
    vcall(t, 22, SET_U64, ctypes.byref(MF_MT_FRAME_SIZE), (w << 32) | h, what="SetUINT64")
    vcall(t, 22, SET_U64, ctypes.byref(MF_MT_FRAME_RATE), (fps << 32) | 1, what="SetUINT64")
    vcall(t, 22, SET_U64, ctypes.byref(MF_MT_PIXEL_ASPECT_RATIO), (1 << 32) | 1, what="SetUINT64")
    vcall(t, 21, SET_U32, ctypes.byref(MF_MT_INTERLACE_MODE), MFVideoInterlace_Progressive, what="SetUINT32")
    if bitrate:
        vcall(t, 21, SET_U32, ctypes.byref(MF_MT_AVG_BITRATE), int(bitrate), what="SetUINT32")
        vcall(t, 21, SET_U32, ctypes.byref(MF_MT_MPEG2_PROFILE), H264_PROFILE_HIGH, what="SetUINT32")
    if stride:
        vcall(t, 21, SET_U32, ctypes.byref(MF_MT_DEFAULT_STRIDE), stride & 0xFFFFFFFF, what="SetUINT32")
    return t


def _sample(data_ptr, nbytes, t, dur):
    """IMFSample with one memory buffer holding a copy of nbytes at data_ptr."""
    mp, _ = _mfapi()
    buf = ctypes.c_void_p()
    hr = mp.MFCreateMemoryBuffer(nbytes, ctypes.byref(buf))
    if hr < 0:
        raise COMError(hr, "MFCreateMemoryBuffer")
    smp = ctypes.c_void_p()
    try:
        p = ctypes.c_void_p()
        vcall(buf, 3, (ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_void_p),
              ctypes.byref(p), None, None, what="Lock")
        ctypes.memmove(p, data_ptr, nbytes)
        vcall(buf, 4, (), what="Unlock")
        vcall(buf, 6, (wintypes.DWORD,), nbytes, what="SetCurrentLength")
        hr = mp.MFCreateSample(ctypes.byref(smp))
        if hr < 0:
            raise COMError(hr, "MFCreateSample")
        vcall(smp, 42, (ctypes.c_void_p,), buf, what="AddBuffer")
        vcall(smp, 36, (ctypes.c_int64,), int(t), what="SetSampleTime")
        vcall(smp, 38, (ctypes.c_int64,), int(dur), what="SetSampleDuration")
    except Exception:
        release(smp)
        raise
    finally:
        release(buf)
    return smp


def out_size(w, h):
    """Encoded frame size for a w x h region: even sides, scaled down past MAX_W x MAX_H."""
    k = min(1.0, MAX_W / float(w), MAX_H / float(h))
    ow, oh = max(MIN_SIDE, int(w * k)), max(MIN_SIDE, int(h * k))
    return ow - ow % 2, oh - oh % 2


def bitrate_for(w, h, fps, quality):
    return int(max(1500000, min(40000000, w * h * fps * QUALITY_BPP.get(quality, 0.07))))


# ---------------------------------------------------------------------------
#  H.264 writer (sink writer, RGB32 frames in)
# ---------------------------------------------------------------------------
class H264Writer:
    """Frames (top-down BGRX, w*4 per row) -> H.264 in a (fragmented) MP4. Call from one thread."""

    def __init__(self, path, w, h, fps, quality, log, hardware=True, fragmented=True):
        self.path, self.w, self.h, self.fps, self.log = path, w, h, fps, log
        self.quality = quality
        self.bitrate = bitrate_for(w, h, fps, quality)
        self.writer = None
        self.idx = 0
        self.frames = 0
        self.hardware = hardware
        self.fragmented = fragmented
        self._open()

    def _open(self):
        _mp, rw = _mfapi()
        items = [(MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS, 1 if self.hardware else 0)]
        if self.fragmented:
            items.append((MF_TRANSCODE_CONTAINERTYPE, MFTranscodeContainerType_FMPEG4))
        attrs = _attrs(items)
        out_t = in_t = None
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
            w = ctypes.c_void_p()
            hr = rw.MFCreateSinkWriterFromURL(ctypes.c_wchar_p(self.path), None, attrs, ctypes.byref(w))
            if hr < 0:
                raise COMError(hr, "MFCreateSinkWriterFromURL")
            self.writer = w
            out_t = _video_type(MFVideoFormat_H264, self.w, self.h, self.fps, bitrate=self.bitrate)
            idx = wintypes.DWORD()
            vcall(w, 3, (ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)), out_t, ctypes.byref(idx), what="AddStream")
            self.idx = idx.value
            in_t = _video_type(MFVideoFormat_RGB32, self.w, self.h, self.fps, stride=self.w * 4)
            vcall(w, 4, (wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p), self.idx, in_t, None,
                  what="SetInputMediaType")
            self._quality_mode()
            vcall(w, 5, (), what="BeginWriting")
        except Exception:
            release(self.writer)
            self.writer = None
            if self.hardware:
                self.log("screenrec: hardware encoder refused (%s), software" % traceback.format_exc().strip().splitlines()[-1])
                self.hardware = False
                return self._open()
            raise
        finally:
            for p in (in_t, out_t, attrs):
                release(p)

    def _quality_mode(self):
        """Quality-based rate control where the encoder takes it: a still screen then costs
        almost nothing instead of the full mean bitrate. Encoders that refuse keep CBR/VBR."""
        api = ctypes.c_void_p()
        try:
            vcall(self.writer, 12, (wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(GUID),
                                    ctypes.POINTER(ctypes.c_void_p)),
                  self.idx, ctypes.byref(GUID_NULL), ctypes.byref(IID_ICodecAPI), ctypes.byref(api),
                  what="GetServiceForStream")
            for key, val in ((CODECAPI_AVEncCommonRateControlMode, RC_QUALITY),
                             (CODECAPI_AVEncCommonQuality, QUALITY_LEVEL.get(self.quality, 60))):
                v = ra.PROPVARIANT()
                v.vt, v.ptr = VT_UI4, val
                try:
                    vcall(api, 9, (ctypes.POINTER(GUID), ctypes.POINTER(ra.PROPVARIANT)),
                          ctypes.byref(key), ctypes.byref(v), what="ICodecAPI.SetValue")
                except COMError as ex:
                    self.log("screenrec: encoder keeps its rate control (%s)" % ex)
                    break
        except COMError as ex:
            self.log("screenrec: no ICodecAPI (%s)" % ex)
        finally:
            release(api)

    def write(self, ptr, t, dur):
        smp = _sample(ptr, self.w * self.h * 4, t, dur)
        try:
            vcall(self.writer, 6, (wintypes.DWORD, ctypes.c_void_p), self.idx, smp, what="WriteSample")
        except COMError:
            if self.frames == 0 and self.hardware:      # some hardware encoders fail only on data
                self.log("screenrec: hardware encoder failed on the first frame, software")
                release(self.writer)
                self.writer = None
                self.hardware = False
                self._open()
                vcall(self.writer, 6, (wintypes.DWORD, ctypes.c_void_p), self.idx, smp, what="WriteSample")
            else:
                raise
        finally:
            release(smp)
        self.frames += 1

    def close(self):
        if self.writer:
            try:
                if self.frames:
                    vcall(self.writer, 11, (), what="Finalize")
            finally:
                release(self.writer)
                self.writer = None


# ---------------------------------------------------------------------------
#  GDI screen grabber (with the pointer and click rings drawn in)
# ---------------------------------------------------------------------------
u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32")


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hCursor", ctypes.c_void_p),
                ("ptScreenPos", wintypes.POINT)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def _d(fn, res, args):
    fn.restype, fn.argtypes = res, args


_d(u32.GetDC, ctypes.c_void_p, [ctypes.c_void_p])
_d(u32.ReleaseDC, ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p])
_d(u32.GetCursorInfo, wintypes.BOOL, [ctypes.POINTER(CURSORINFO)])
_d(u32.GetIconInfo, wintypes.BOOL, [ctypes.c_void_p, ctypes.POINTER(ICONINFO)])
_d(u32.DrawIconEx, wintypes.BOOL, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_int, wintypes.UINT, ctypes.c_void_p, wintypes.UINT])
_d(u32.GetAsyncKeyState, ctypes.c_short, [ctypes.c_int])
_d(u32.GetSystemMetrics, ctypes.c_int, [ctypes.c_int])
_d(g32.CreateCompatibleDC, ctypes.c_void_p, [ctypes.c_void_p])
_d(g32.DeleteDC, wintypes.BOOL, [ctypes.c_void_p])
_d(g32.CreateDIBSection, ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD])
_d(g32.SelectObject, ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p])
_d(g32.DeleteObject, wintypes.BOOL, [ctypes.c_void_p])
_d(g32.BitBlt, wintypes.BOOL, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wintypes.DWORD])
_d(g32.StretchBlt, wintypes.BOOL, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.DWORD])
_d(g32.SetStretchBltMode, ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
_d(g32.SetBrushOrgEx, wintypes.BOOL, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p])
_d(g32.CreatePen, ctypes.c_void_p, [ctypes.c_int, ctypes.c_int, wintypes.DWORD])
_d(g32.GetStockObject, ctypes.c_void_p, [ctypes.c_int])
_d(g32.Ellipse, wintypes.BOOL, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int])
SRCCOPY = 0x00CC0020
HALFTONE = 4
DI_NORMAL = 3
CURSOR_SHOWING = 1
NULL_BRUSH = 5
PS_SOLID = 0
CLICK_HOLD = 0.3                    # s a click ring stays after the button is released
CLICK_COLORS = {0x01: 0x0AD6FF, 0x02: 0xFF8A0A}     # COLORREF (0x00BBGGRR): left yellow, right blue


class Grabber:
    """Copies the screen rectangle (x, y, w, h) into a top-down 32-bit DIB of ow x oh."""

    def __init__(self, x, y, w, h, ow, oh, cursor=True, clicks=False):
        self.x, self.y, self.w, self.h, self.ow, self.oh = x, y, w, h, ow, oh
        self.cursor, self.clicks = cursor, clicks
        self.kx, self.ky = ow / float(w), oh / float(h)
        self.sdc = u32.GetDC(None)
        self.mdc = g32.CreateCompatibleDC(self.sdc)
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(bi)
        bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = ow, -oh, 1, 32
        self.bits = ctypes.c_void_p()
        self.hbm = g32.CreateDIBSection(self.mdc, ctypes.byref(bi), 0, ctypes.byref(self.bits), None, 0)
        if not self.hbm:
            self.close()
            raise OSError("CreateDIBSection failed")
        self.old = g32.SelectObject(self.mdc, self.hbm)
        if (ow, oh) != (w, h):
            g32.SetStretchBltMode(self.mdc, HALFTONE)
            g32.SetBrushOrgEx(self.mdc, 0, 0, None)
        self._hot = {}
        self._pens = {}
        self._click_until = {}
        self._swap = bool(u32.GetSystemMetrics(23))         # SM_SWAPBUTTON

    def grab(self):
        if (self.ow, self.oh) == (self.w, self.h):
            ok = g32.BitBlt(self.mdc, 0, 0, self.w, self.h, self.sdc, self.x, self.y, SRCCOPY)
        else:
            ok = g32.StretchBlt(self.mdc, 0, 0, self.ow, self.oh, self.sdc, self.x, self.y, self.w, self.h, SRCCOPY)
        if self.cursor or self.clicks:
            self._pointer()
        return bool(ok)

    def _hotspot(self, hc):
        hs = self._hot.get(hc)
        if hs is None:
            ii = ICONINFO()
            if u32.GetIconInfo(hc, ctypes.byref(ii)):
                hs = (ii.xHotspot, ii.yHotspot)
                for b in (ii.hbmMask, ii.hbmColor):
                    if b:
                        g32.DeleteObject(b)
            else:
                hs = (0, 0)
            if len(self._hot) > 64:
                self._hot.clear()
            self._hot[hc] = hs
        return hs

    def _pointer(self):
        ci = CURSORINFO()
        ci.cbSize = ctypes.sizeof(ci)
        if not u32.GetCursorInfo(ctypes.byref(ci)):
            return
        px = int((ci.ptScreenPos.x - self.x) * self.kx)
        py = int((ci.ptScreenPos.y - self.y) * self.ky)
        if self.clicks:
            now = time.time()
            for btn in (0x01, 0x02):
                phys = (0x02 if btn == 0x01 else 0x01) if self._swap else btn
                if u32.GetAsyncKeyState(phys) & 0x8000:
                    self._click_until[btn] = now + CLICK_HOLD
                if self._click_until.get(btn, 0) > now:
                    self._ring(px, py, CLICK_COLORS[btn], self._click_until[btn] - now)
        if self.cursor and ci.flags & CURSOR_SHOWING and ci.hCursor:
            hx, hy = self._hotspot(ci.hCursor)
            u32.DrawIconEx(self.mdc, px - hx, py - hy, ci.hCursor, 0, 0, 0, None, DI_NORMAL)

    def _ring(self, x, y, color, left):
        k = max(self.kx, 0.5)
        r = int((14 + 8 * (1.0 - min(1.0, left / CLICK_HOLD))) * k * _dpi())
        width = max(2, int(3 * k * _dpi()))
        pen = self._pens.get((color, width))
        if pen is None:
            pen = self._pens[(color, width)] = g32.CreatePen(PS_SOLID, width, color)
        op = g32.SelectObject(self.mdc, pen)
        ob = g32.SelectObject(self.mdc, g32.GetStockObject(NULL_BRUSH))
        g32.Ellipse(self.mdc, x - r, y - r, x + r, y + r)
        g32.SelectObject(self.mdc, ob)
        g32.SelectObject(self.mdc, op)

    def close(self):
        if getattr(self, "old", None):
            g32.SelectObject(self.mdc, self.old)
            self.old = None
        if getattr(self, "hbm", None):
            g32.DeleteObject(self.hbm)
            self.hbm = None
        for p in getattr(self, "_pens", {}).values():
            g32.DeleteObject(p)
        self._pens = {}
        if self.mdc:
            g32.DeleteDC(self.mdc)
            self.mdc = None
        if self.sdc:
            u32.ReleaseDC(None, self.sdc)
            self.sdc = None


_DPI = []


def _dpi():
    if not _DPI:
        try:
            _DPI.append(u32.GetDpiForSystem() / 96.0)
        except Exception:
            _DPI.append(1.0)
    return _DPI[0]


# ---------------------------------------------------------------------------
#  A recording: capture thread + optional rec_audio session
# ---------------------------------------------------------------------------
class VideoSession:
    """Record the screen rectangle rect = (x, y, w, h) into video_path (fragmented MP4) and,
    with system / mic on, the sound into wav_path. Check .error after .ready is set."""

    def __init__(self, rect, video_path, wav_path, log, fps=30, quality="normal", cursor=True,
                 clicks=False, system=True, mic=False, mic_id="", hardware=True):
        self.rect = tuple(int(v) for v in rect)
        self.video_path, self.wav_path, self.log = video_path, wav_path, log
        self.fps = int(fps)
        self.quality, self.cursor, self.clicks = quality, cursor, clicks
        self.want_hw = hardware
        self.size = out_size(self.rect[2], self.rect[3])
        self.audio = None
        self.grabber = None
        self.t0 = None                  # QPC (hns) of the first video frame
        self.frames = 0
        self.dropped = 0
        self.hardware = None
        self.error = None
        self.started = time.time()
        self._stop = threading.Event()
        self.ready = threading.Event()
        # always a sound session: computer sound / mic can be switched on during the recording
        self.audio = ra.Session(wav_path, log, system=system, mic=mic, mic_id=mic_id)
        self.thread = threading.Thread(target=self._run, daemon=True, name="hdc-screenrec")
        self.thread.start()

    def set_option(self, key, on):
        """The panel's switches during the recording."""
        on = bool(on)
        if key in ("cursor", "clicks"):
            setattr(self, key, on)
            if self.grabber is not None:
                setattr(self.grabber, key, on)
        elif key == "system":
            self.audio.set_sources(system=on)
        elif key == "mic":
            self.audio.set_sources(mic=on)

    @property
    def audio_used(self):
        return self.audio is not None and self.audio.used

    def levels(self):
        return self.audio.levels() if self.audio is not None else (0.0, 0.0)

    def seconds(self):
        return self.frames / float(self.fps) if self.t0 is None else (ra.qpc_hns() - self.t0) / 1e7

    @property
    def audio_offset(self):
        """hns from the first video frame to the first WAV sample (negative: audio began earlier)."""
        if self.audio is None or self.t0 is None:
            return 0
        return self.audio.mixer.t0 - self.t0

    def _run(self):
        ra.co_init()
        mp, _ = _mfapi()
        mp.MFStartup(ra.MF_VERSION, 0)
        grab = writer = None
        try:
            x, y, w, h = self.rect
            ow, oh = self.size
            grab = self.grabber = Grabber(x, y, w, h, ow, oh, self.cursor, self.clicks)
            writer = H264Writer(self.video_path, ow, oh, self.fps, self.quality, self.log, hardware=self.want_hw)
            self.hardware = writer.hardware
            self.log("screenrec: %dx%d at (%d,%d) -> %dx%d, %d fps, %d kbit/s, %s encoder" % (
                w, h, x, y, ow, oh, self.fps, writer.bitrate // 1000, "hardware" if writer.hardware else "software"))
            self.ready.set()
            step = 10000000 // self.fps
            nxt = ra.qpc_hns()
            last_t = -step
            while not self._stop.is_set():
                now = ra.qpc_hns()
                if now < nxt:
                    self._stop.wait((nxt - now) / 1e7)
                    continue
                if now - nxt > step:                    # fell behind: skip the missed slots
                    self.dropped += (now - nxt) // step
                    nxt = now
                nxt += step
                grab.grab()
                if self.t0 is None:
                    self.t0 = now
                t = now - self.t0
                if t <= last_t:
                    t = last_t + 1
                writer.write(grab.bits, t, step)
                self.hardware = writer.hardware
                last_t = t
                self.frames += 1
        except Exception as ex:
            self.error = ex
            self.log("screenrec capture failed:\n" + traceback.format_exc())
        finally:
            self.ready.set()
            try:
                if writer is not None:
                    writer.close()
            except Exception:
                self.log("screenrec finalize failed:\n" + traceback.format_exc())
            if grab is not None:
                grab.close()
            if self.audio is not None:
                self.audio.stop(wait=True)
            mp.MFShutdown()
            ra.ole32.CoUninitialize()

    def stop(self, wait=True):
        self._stop.set()
        if wait:
            self.thread.join(10)


# ---------------------------------------------------------------------------
#  Final file: H.264 copied as it is + the WAV encoded to AAC, interleaved by time
# ---------------------------------------------------------------------------
def mux(video_path, wav_path, audio_offset, out_path, total_hns=0, progress=None):
    """Write out_path (plain MP4). wav_path may be None. Returns the number of video frames.
    A video cut short by a crash is copied up to the last readable sample."""
    mp, rw = _mfapi()
    ra.co_init()
    hr = mp.MFStartup(ra.MF_VERSION, 0)
    if hr < 0:
        raise COMError(hr, "MFStartup")
    reader = writer = vtype = a_out = a_in = attrs = None
    wav = None
    try:
        reader = ctypes.c_void_p()
        hr = rw.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(video_path), None, ctypes.byref(reader))
        if hr < 0:
            raise COMError(hr, "MFCreateSourceReaderFromURL")
        vcall(reader, 4, (wintypes.DWORD, wintypes.BOOL), SOURCE_ALL_STREAMS, False, what="SetStreamSelection")
        vcall(reader, 4, (wintypes.DWORD, wintypes.BOOL), SOURCE_FIRST_VIDEO, True, what="SetStreamSelection")
        vtype = ctypes.c_void_p()
        vcall(reader, 6, (wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)), SOURCE_FIRST_VIDEO,
              ctypes.byref(vtype), what="GetCurrentMediaType")
        attrs = _attrs([(MF_TRANSCODE_CONTAINERTYPE, MFTranscodeContainerType_MPEG4)])
        if os.path.exists(out_path):
            os.remove(out_path)
        writer = ctypes.c_void_p()
        hr = rw.MFCreateSinkWriterFromURL(ctypes.c_wchar_p(out_path), None, attrs, ctypes.byref(writer))
        if hr < 0:
            raise COMError(hr, "MFCreateSinkWriterFromURL")
        vi = wintypes.DWORD()
        vcall(writer, 3, (ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)), vtype, ctypes.byref(vi), what="AddStream")
        ai = wintypes.DWORD()
        rate = block = 0
        if wav_path and os.path.exists(wav_path) and os.path.getsize(wav_path) > 44:
            wav = open(wav_path, "rb")
            hdr = wav.read(44)
            if hdr[:4] != b"RIFF" or hdr[8:16] != b"WAVEfmt ":
                raise ValueError("not a recorder WAV")
            import struct
            ch, rate = struct.unpack("<HI", hdr[22:28])
            bits = struct.unpack("<H", hdr[34:36])[0]
            block = ch * bits // 8
            a_out = ra._media_type(ra.MFAudioFormat_AAC, rate, ch, bits=16, avg=AUDIO_AVG)
            vcall(writer, 3, (ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)), a_out, ctypes.byref(ai),
                  what="AddStream(audio)")
            a_in = ra._media_type(ra.MFAudioFormat_PCM, rate, ch, bits=bits, avg=rate * block, align=block)
            vcall(writer, 4, (wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p), ai.value, a_in, None,
                  what="SetInputMediaType(audio)")
            skip = max(0, -int(audio_offset)) * rate // 10000000      # audio that began before the video
            wav.seek(44 + skip * block)
        vcall(writer, 5, (), what="BeginWriting")

        a_pos = max(0, int(audio_offset))       # hns of the next audio chunk on the video time line
        a_chunk = rate * block // 2 if wav else 0
        a_left = wav is not None
        frames = 0
        flags, idx = wintypes.DWORD(), wintypes.DWORD()
        ts = ctypes.c_int64()
        v_next = None                           # (time, sample) read but not yet written
        v_left = True
        last_p = 0.0
        v_last = 0
        while v_left or a_left:
            if v_left and v_next is None:
                smp = ctypes.c_void_p()
                try:
                    vcall(reader, 9, (wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                      ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_int64),
                                      ctypes.POINTER(ctypes.c_void_p)),
                          SOURCE_FIRST_VIDEO, 0, ctypes.byref(idx), ctypes.byref(flags), ctypes.byref(ts),
                          ctypes.byref(smp), what="ReadSample")
                except COMError:
                    if frames == 0:
                        raise
                    v_left = False              # a truncated file: keep what was readable
                    smp = ctypes.c_void_p()
                if flags.value & (READERF_ERROR | READERF_EOS):
                    v_left = False
                if smp.value:
                    v_next = (ts.value, smp)
                elif not v_left:
                    pass
                else:
                    continue
            if a_left and (v_next is None or a_pos <= v_next[0]):
                data = wav.read(a_chunk)
                data = data[:len(data) - len(data) % block]
                if not data or (not v_left and v_next is None and a_pos > v_last + AUDIO_TAIL):
                    a_left = False
                    continue
                n = len(data) // block
                buf = ctypes.create_string_buffer(data, len(data))
                smp = _sample(ctypes.addressof(buf), len(data), a_pos, n * 10000000 // rate)
                try:
                    vcall(writer, 6, (wintypes.DWORD, ctypes.c_void_p), ai.value, smp, what="WriteSample(audio)")
                finally:
                    release(smp)
                a_pos += n * 10000000 // rate
                continue
            if v_next is not None:
                t, smp = v_next
                v_next = None
                try:
                    vcall(writer, 6, (wintypes.DWORD, ctypes.c_void_p), vi.value, smp, what="WriteSample(video)")
                finally:
                    release(smp)
                frames += 1
                v_last = t
                if progress is not None and total_hns:
                    p = min(1.0, t / float(total_hns))
                    if p - last_p >= 0.02:
                        last_p = p
                        progress(p)
        if frames == 0:
            raise ValueError("no video frames")
        vcall(writer, 11, (), what="Finalize")
        return frames
    finally:
        if wav is not None:
            wav.close()
        for p in (a_in, a_out, vtype, attrs, writer, reader):
            release(p)
        mp.MFShutdown()
        ra.ole32.CoUninitialize()


# ---------------------------------------------------------------------------
#  Self-test: python rec_video.py OUT_DIR [seconds] [x y w h] [fps]
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    out = sys.argv[1]
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 5
    rect = tuple(int(v) for v in sys.argv[3:7]) if len(sys.argv) > 6 else (0, 0, 1280, 720)
    fps = int(sys.argv[7]) if len(sys.argv) > 7 else 30
    os.makedirs(out, exist_ok=True)
    vp, wp = os.path.join(out, "t.fmp4.mp4"), os.path.join(out, "t.wav")
    s = VideoSession(rect, vp, wp, print, fps=fps, cursor=True, clicks=True, system=True, mic=False,
                     hardware=os.environ.get("HDC_SOFT") != "1")
    s.ready.wait(10)
    time.sleep(secs)
    s.stop()
    print("frames", s.frames, "dropped", s.dropped, "fps %.1f" % (s.frames / secs), "offset ms", s.audio_offset / 1e4,
          "error", s.error)
    t = time.time()
    n = mux(vp, wp, s.audio_offset, os.path.join(out, "final.mp4"), total_hns=int(secs * 1e7))
    print("mux frames", n, "in %.2f s" % (time.time() - t))
