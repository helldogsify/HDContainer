# -*- coding: utf-8 -*-
"""Audio engine of the recorder: WASAPI capture (loopback + microphone), a mixer on one
time line, a crash-tolerant WAV writer and a Media Foundation encoder (MP3 / M4A).

No third-party packages: COM is called through raw vtables with ctypes, mixing is done
with audioop (C, in the stdlib of Python 3.12 that the release build uses).

Time line. Every WASAPI packet carries the QPC time of its first frame. A source places
its packets back to back and re-anchors to that time stamp only when it drifts by more than
SYNC_TOL. So a loopback stream that delivers nothing during silence simply leaves a gap
(zeros), and two devices with slightly different clocks stay aligned to the wall clock.
The mixer keeps the last MIX_LAG seconds open for late packets and writes everything older.
"""
import os
import time
import struct
import ctypes
import threading
import traceback
from ctypes import wintypes

try:
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import audioop
except ImportError:                 # Python 3.13+: slow pure-Python fallbacks below
    audioop = None

RATE = 48000                        # output: 48 kHz, mono, 16-bit
CHANNELS = 1
WIDTH = 2
FRAME = CHANNELS * WIDTH
SYNC_TOL = int(RATE * 0.04)         # re-anchor a source to its time stamps beyond 40 ms of drift
MIX_LAG = 0.6                       # s kept open for late packets before they are written
BUFFER_HNS = 2000000                # 200 ms WASAPI buffer
POLL_S = 0.01

# ---------------------------------------------------------------------------
#  COM plumbing
# ---------------------------------------------------------------------------
ole32 = ctypes.WinDLL("ole32")
kernel32 = ctypes.WinDLL("kernel32")
HRESULT_T = ctypes.c_long
COINIT_MULTITHREADED = 0x0
CLSCTX_ALL = 0x17


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def guid(s):
    g = GUID()
    ole32.CLSIDFromString(ctypes.c_wchar_p("{" + s + "}"), ctypes.byref(g))
    return g


class COMError(OSError):
    def __init__(self, hr, what=""):
        self.hr = hr & 0xFFFFFFFF
        super().__init__("%s failed: 0x%08X" % (what, self.hr))


_PROTOS = {}


def vcall(obj, idx, argtypes, *args, what="COM call"):
    """Call method #idx of the COM interface pointer obj; raise COMError on a failed HRESULT."""
    key = (idx, tuple(argtypes))
    proto = _PROTOS.get(key)
    if proto is None:
        proto = _PROTOS[key] = ctypes.WINFUNCTYPE(HRESULT_T, ctypes.c_void_p, *argtypes)
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    hr = proto(vtbl[idx])(obj, *args)
    if hr < 0:
        raise COMError(hr, what)
    return hr


def release(obj):
    if obj:
        try:
            vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(obj)
        except Exception:
            pass


def co_init():
    ole32.CoInitializeEx(None, COINIT_MULTITHREADED)    # RPC_E_CHANGED_MODE is fine too


CLSID_MMDeviceEnumerator = guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioClient = guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
IID_IAudioCaptureClient = guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317")
E_RENDER, E_CAPTURE = 0, 1
E_CONSOLE, E_MULTIMEDIA, E_COMMUNICATIONS = 0, 1, 2
DEVICE_STATE_ACTIVE = 1
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
BUF_SILENT, BUF_TIMESTAMP_ERROR = 0x2, 0x4
AUDCLNT_E_DEVICE_INVALIDATED = 0x88890004
E_NOTFOUND = 0x80070490
VT_LPWSTR = 31


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


PKEY_Device_FriendlyName = PROPERTYKEY(guid("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 14)


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("ptr", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", ctypes.c_ushort), ("nChannels", ctypes.c_ushort),
                ("nSamplesPerSec", ctypes.c_uint32), ("nAvgBytesPerSec", ctypes.c_uint32),
                ("nBlockAlign", ctypes.c_ushort), ("wBitsPerSample", ctypes.c_ushort),
                ("cbSize", ctypes.c_ushort)]


WAVE_FORMAT_PCM, WAVE_FORMAT_IEEE_FLOAT, WAVE_FORMAT_EXTENSIBLE = 1, 3, 0xFFFE


def pcm_format(rate=RATE, ch=CHANNELS, bits=16):
    f = WAVEFORMATEX()
    f.wFormatTag, f.nChannels, f.nSamplesPerSec, f.wBitsPerSample = WAVE_FORMAT_PCM, ch, rate, bits
    f.nBlockAlign = ch * bits // 8
    f.nAvgBytesPerSec = rate * f.nBlockAlign
    return f


def _enumerator():
    p = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(p))
    if hr < 0:
        raise COMError(hr, "CoCreateInstance(MMDeviceEnumerator)")
    return p


def _default_device(en, flow, role):
    p = ctypes.c_void_p()
    try:
        vcall(en, 4, (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)), flow, role,
              ctypes.byref(p), what="GetDefaultAudioEndpoint")
    except COMError as ex:
        if ex.hr == E_NOTFOUND:                 # no device of that kind at all
            return None
        raise
    return p


def _device_by_id(en, dev_id):
    p = ctypes.c_void_p()
    vcall(en, 5, (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)), dev_id, ctypes.byref(p),
          what="GetDevice")
    return p


def _device_id(dev):
    s = ctypes.c_void_p()
    vcall(dev, 5, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(s), what="GetId")
    try:
        return ctypes.wstring_at(s.value)
    finally:
        ole32.CoTaskMemFree(s)


def _device_name(dev):
    ps = ctypes.c_void_p()
    try:
        vcall(dev, 4, (wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)), 0, ctypes.byref(ps),
              what="OpenPropertyStore")
        pv = PROPVARIANT()
        vcall(ps, 5, (ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)),
              ctypes.byref(PKEY_Device_FriendlyName), ctypes.byref(pv), what="GetValue")
        try:
            return ctypes.wstring_at(pv.ptr) if pv.vt == VT_LPWSTR and pv.ptr else ""
        finally:
            ole32.PropVariantClear(ctypes.byref(pv))
    except COMError:
        return ""
    finally:
        release(ps)


def _in_mta(fn):
    """Run fn in a fresh MTA thread (the tk thread is STA and may not be COM-initialized)."""
    out = {}

    def run():
        co_init()
        try:
            out["v"] = fn()
        except Exception as ex:
            out["e"] = ex
        finally:
            ole32.CoUninitialize()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(5)
    if "e" in out:
        raise out["e"]
    return out.get("v")


def list_devices(flow):
    """[(id, friendly name)] of the active endpoints of one direction."""
    def work():
        en = _enumerator()
        col = ctypes.c_void_p()
        res = []
        try:
            vcall(en, 3, (ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)), flow,
                  DEVICE_STATE_ACTIVE, ctypes.byref(col), what="EnumAudioEndpoints")
            n = wintypes.UINT()
            vcall(col, 3, (ctypes.POINTER(wintypes.UINT),), ctypes.byref(n), what="GetCount")
            for i in range(n.value):
                d = ctypes.c_void_p()
                vcall(col, 4, (wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)), i, ctypes.byref(d),
                      what="Item")
                try:
                    res.append((_device_id(d), _device_name(d)))
                finally:
                    release(d)
        finally:
            release(col)
            release(en)
        return res
    return _in_mta(work) or []


def default_ids(en):
    """Ids of the default render endpoints (console + communications: call apps often play on
    the latter) and of the default capture endpoint, for the device watcher."""
    out = {"render": [], "capture": None}
    for role in (E_CONSOLE, E_COMMUNICATIONS):
        d = _default_device(en, E_RENDER, role)
        if d:
            try:
                i = _device_id(d)
                if i not in out["render"]:
                    out["render"].append(i)
            finally:
                release(d)
    d = _default_device(en, E_CAPTURE, E_CONSOLE)
    if d:
        try:
            out["capture"] = _device_id(d)
        finally:
            release(d)
    return out


def qpc_hns():
    c, f = ctypes.c_int64(), ctypes.c_int64()
    kernel32.QueryPerformanceCounter(ctypes.byref(c))
    kernel32.QueryPerformanceFrequency(ctypes.byref(f))
    return c.value * 10000000 // f.value


# ---------------------------------------------------------------------------
#  Sample conversion (only when the audio engine refuses AUTOCONVERTPCM)
# ---------------------------------------------------------------------------
class Converter:
    """Device mix format -> 16-bit mono RATE."""

    def __init__(self, wf_ptr):
        wf = ctypes.cast(wf_ptr, ctypes.POINTER(WAVEFORMATEX)).contents
        self.ch, self.rate, self.bits = wf.nChannels, wf.nSamplesPerSec, wf.wBitsPerSample
        tag = wf.wFormatTag
        if tag == WAVE_FORMAT_EXTENSIBLE and wf.cbSize >= 22:
            # SubFormat GUID starts 8 bytes after the WAVEFORMATEX header; Data1 = format tag
            tag = ctypes.c_uint32.from_address(wf_ptr + ctypes.sizeof(WAVEFORMATEX) + 6).value
        self.float = tag == WAVE_FORMAT_IEEE_FLOAT
        self.state = None

    def __call__(self, data):
        import array
        if self.float and self.bits == 32:
            a = array.array("f")
            a.frombytes(data)
            if self.ch > 1:                       # average the first two channels
                l, r = a[0::self.ch], a[1::self.ch]
                vals = [(x + y) * 16383.5 for x, y in zip(l, r)]
            else:
                vals = [x * 32767.0 for x in a]
            out = array.array("h", [32767 if v > 32767 else -32768 if v < -32768 else int(v) for v in vals])
            pcm = out.tobytes()
        else:
            w = self.bits // 8
            pcm = data if w == 2 else _lin2lin(data, w, 2)
            if self.ch > 1:
                a = array.array("h")
                a.frombytes(pcm)
                if self.ch > 2:
                    a = array.array("h", [v for i in range(len(a) // self.ch) for v in a[i * self.ch:i * self.ch + 2]])
                pcm = _tomono(a.tobytes())
        if self.rate != RATE:
            pcm, self.state = _ratecv(pcm, self.rate, self.state)
        return pcm


def _lin2lin(data, w, w2):
    if audioop:
        return audioop.lin2lin(data, w, w2)
    raise RuntimeError("no audioop")


def _tomono(stereo16):
    if audioop:
        return audioop.tomono(stereo16, 2, 0.5, 0.5)
    import array
    a = array.array("h")
    a.frombytes(stereo16)
    return array.array("h", [(x + y) // 2 for x, y in zip(a[0::2], a[1::2])]).tobytes()


def _ratecv(pcm, rate, state):
    if audioop:
        return audioop.ratecv(pcm, 2, 1, rate, RATE, state)
    raise RuntimeError("no audioop")


def _add(a, b):
    if audioop:
        return audioop.add(a, b, 2)
    import array
    x, y = array.array("h"), array.array("h")
    x.frombytes(a)
    y.frombytes(b)
    return array.array("h", [max(-32768, min(32767, p + q)) for p, q in zip(x, y)]).tobytes()


def _mul(a, k):
    if audioop:
        return audioop.mul(a, 2, k)
    import array
    x = array.array("h")
    x.frombytes(a)
    return array.array("h", [max(-32768, min(32767, int(p * k))) for p in x]).tobytes()


def _rms(a):
    if not a:
        return 0
    if audioop:
        return audioop.rms(a, 2)
    import array
    x = array.array("h")
    x.frombytes(a[:4000])
    return int((sum(v * v for v in x) / max(1, len(x))) ** 0.5)


# ---------------------------------------------------------------------------
#  Mixer + WAV writer
# ---------------------------------------------------------------------------
class WavWriter:
    """16-bit mono WAV whose header is kept valid while it grows (a crash leaves a playable file)."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "wb")
        self.n = 0
        self._header()

    def _header(self):
        self.f.seek(0)
        self.f.write(b"RIFF" + struct.pack("<I", 36 + self.n) + b"WAVEfmt " +
                     struct.pack("<IHHIIHH", 16, 1, CHANNELS, RATE, RATE * FRAME, FRAME, 16) +
                     b"data" + struct.pack("<I", self.n))
        self.f.seek(0, 2)

    def write(self, data):
        if data:
            self.f.write(data)
            self.n += len(data)

    def sync(self):
        self._header()
        self.f.flush()

    def close(self):
        if self.f:
            self.sync()
            self.f.close()
            self.f = None


def repair_wav(path):
    """Fix the sizes in the header of a WAV left by a crash. Returns the data size."""
    size = os.path.getsize(path)
    n = max(0, size - 44)
    n -= n % FRAME
    with open(path, "r+b") as f:
        f.seek(4)
        f.write(struct.pack("<I", 36 + n))
        f.seek(40)
        f.write(struct.pack("<I", n))
    return n


class Mixer:
    def __init__(self, writer):
        self.w = writer
        self.lock = threading.Lock()
        self.t0 = qpc_hns()
        self.base = 0                   # frame index of buf[0]
        self.buf = bytearray()
        self.written = 0                # frames written

    def now_frame(self):
        return (qpc_hns() - self.t0) * RATE // 10000000

    def add(self, src, pcm, qpc):
        n = len(pcm) // FRAME
        if n <= 0:
            return
        if qpc:
            ts = (qpc - self.t0) * RATE // 10000000
            if src.next is None or abs(ts - src.next) > SYNC_TOL:
                src.next = ts
        elif src.next is None:
            src.next = self.now_frame() - n
        pos = src.next
        src.next += n
        if src.gain != 1.0:
            pcm = _mul(pcm, src.gain)
        with self.lock:
            if pos < self.base:                         # too late: drop what is already written
                cut = (self.base - pos) * FRAME
                if cut >= len(pcm):
                    return
                pcm, pos = pcm[cut:], self.base
            off = (pos - self.base) * FRAME
            end = off + len(pcm)
            if end > len(self.buf):
                self.buf.extend(bytes(end - len(self.buf)))
            self.buf[off:end] = _add(bytes(self.buf[off:end]), pcm)

    def flush(self, final=False):
        with self.lock:
            upto = self.base + len(self.buf) // FRAME if final else self.now_frame() - int(MIX_LAG * RATE)
            if upto <= self.base:
                return
            k = (upto - self.base) * FRAME
            if k > len(self.buf):
                self.buf.extend(bytes(k - len(self.buf)))      # nobody played anything: silence
            chunk = bytes(self.buf[:k])
            del self.buf[:k]
            self.base = upto
        self.w.write(chunk)
        self.written += len(chunk) // FRAME


# ---------------------------------------------------------------------------
#  One WASAPI capture stream in its own thread
# ---------------------------------------------------------------------------
class Source:
    def __init__(self, mixer, dev_id, loopback, log, gain=1.0):
        self.mixer, self.dev_id, self.loopback, self.log = mixer, dev_id, loopback, log
        self.gain = gain
        self.next = None
        self.level = 0.0                # 0..1, for the indicator
        self.alive = True
        self.error = None
        self.converted = False          # True: our own conversion (AUTOCONVERTPCM refused)
        self._stop = threading.Event()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="hdc-rec-%s" % ("out" if loopback else "mic"))
        self.thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        co_init()
        en = dev = client = cap = None
        wf = ctypes.c_void_p()
        try:
            en = _enumerator()
            dev = _device_by_id(en, self.dev_id)
            client = ctypes.c_void_p()
            vcall(dev, 3, (ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                  ctypes.byref(IID_IAudioClient), CLSCTX_ALL, None, ctypes.byref(client), what="Activate")
            base = AUDCLNT_STREAMFLAGS_LOOPBACK if self.loopback else 0
            conv = None
            fmt = pcm_format()
            init_args = (ctypes.c_int, wintypes.DWORD, ctypes.c_int64, ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p)
            try:
                vcall(client, 3, init_args, 0,
                      base | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
                      BUFFER_HNS, 0, ctypes.addressof(fmt), None, what="Initialize(autoconvert)")
            except COMError as ex:
                self.log("recorder: autoconvert refused (%s), converting myself" % ex)
                release(client)
                client = ctypes.c_void_p()
                vcall(dev, 3, (ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                      ctypes.byref(IID_IAudioClient), CLSCTX_ALL, None, ctypes.byref(client), what="Activate")
                vcall(client, 8, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(wf), what="GetMixFormat")
                conv = Converter(wf.value)
                self.converted = True
                vcall(client, 3, init_args, 0, base, BUFFER_HNS, 0, wf.value, None, what="Initialize")
            block = (conv.ch * conv.bits // 8) if conv else FRAME
            cap = ctypes.c_void_p()
            vcall(client, 14, (ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)),
                  ctypes.byref(IID_IAudioCaptureClient), ctypes.byref(cap), what="GetService")
            vcall(client, 10, (), what="Start")
            self.ready.set()
            nxt = wintypes.UINT()
            pdata = ctypes.c_void_p()
            nfr = wintypes.UINT()
            flags = wintypes.DWORD()
            devpos, qpc = ctypes.c_uint64(), ctypes.c_uint64()
            ga = (ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.DWORD),
                  ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64))
            while not self._stop.is_set():
                vcall(cap, 5, (ctypes.POINTER(wintypes.UINT),), ctypes.byref(nxt), what="GetNextPacketSize")
                if not nxt.value:
                    self.level *= 0.85
                    time.sleep(POLL_S)
                    continue
                vcall(cap, 3, ga, ctypes.byref(pdata), ctypes.byref(nfr), ctypes.byref(flags),
                      ctypes.byref(devpos), ctypes.byref(qpc), what="GetBuffer")
                n = nfr.value
                if flags.value & BUF_SILENT or not pdata.value:
                    raw = bytes(n * block)
                else:
                    raw = ctypes.string_at(pdata.value, n * block)
                vcall(cap, 4, (wintypes.UINT,), n, what="ReleaseBuffer")
                pcm = conv(raw) if conv else raw
                lvl = min(1.0, _rms(pcm) / 6000.0)
                self.level = lvl if lvl > self.level else self.level * 0.85 + lvl * 0.15
                ts = 0 if flags.value & BUF_TIMESTAMP_ERROR else qpc.value
                if conv and conv.rate != RATE:
                    ts = 0                      # resampled packets: keep them contiguous
                self.mixer.add(self, pcm, ts)
            try:
                vcall(client, 11, (), what="Stop")
            except COMError:
                pass
        except COMError as ex:
            self.error = ex
            if ex.hr != AUDCLNT_E_DEVICE_INVALIDATED:
                self.log("recorder source %s: %s" % ("loopback" if self.loopback else "mic", ex))
        except Exception as ex:
            self.error = ex
            self.log("recorder source crashed:\n" + traceback.format_exc())
        finally:
            self.alive = False
            self.ready.set()
            if wf.value:
                ole32.CoTaskMemFree(wf)
            for p in (cap, client, dev, en):
                release(p)
            ole32.CoUninitialize()


# ---------------------------------------------------------------------------
#  A recording session: sources + device watcher + writer thread
# ---------------------------------------------------------------------------
class Session:
    """Record the computer's output (every default render device) and/or a microphone into
    a WAV at wav_path. mic_id "" = the default microphone (followed when it changes)."""

    def __init__(self, wav_path, log, system=True, mic=True, mic_id="", mic_gain=1.0):
        self.log = log
        self.system, self.mic, self.mic_id, self.mic_gain = system, mic, mic_id, mic_gain
        self.writer = WavWriter(wav_path)
        self.mixer = Mixer(self.writer)
        self.out_srcs = {}              # render device id -> Source
        self.mic_src = None
        self.used = bool(system or mic) # was any source ever on (the screen recorder drops a silent track)
        self._stop = threading.Event()
        self._kick = threading.Event()  # set_sources: sync the devices now, not in 2 s
        self.started = time.time()
        self.thread = threading.Thread(target=self._run, daemon=True, name="hdc-rec-main")
        self.thread.start()

    @property
    def path(self):
        return self.writer.path

    def levels(self):
        out = max([s.level for s in list(self.out_srcs.values()) if s.alive] or [0.0])
        mic = self.mic_src.level if self.mic_src is not None and self.mic_src.alive else 0.0
        return out, mic

    def seconds(self):
        return self.mixer.written / RATE

    def set_sources(self, system=None, mic=None):
        """Switch the computer sound / the microphone on or off while recording."""
        if system is not None:
            self.system = bool(system)
        if mic is not None:
            self.mic = bool(mic)
        self.used = self.used or self.system or self.mic
        self._kick.set()

    def _sync_devices(self, en):
        if not self.system and self.out_srcs:
            old, self.out_srcs = self.out_srcs, {}
            for s in old.values():
                s.stop()
        if not self.mic and self.mic_src is not None:
            self.mic_src.stop()
            self.mic_src = None
        if not (self.system or self.mic):
            return
        ids = default_ids(en)
        if self.system:
            for i in ids["render"]:
                s = self.out_srcs.get(i)
                if s is None or not s.alive:
                    self.out_srcs[i] = Source(self.mixer, i, True, self.log)
                    self.log("recorder: loopback on %s" % i)
        if self.mic:
            want = self.mic_id or ids["capture"]
            s = self.mic_src
            if want and (s is None or not s.alive or s.dev_id != want):
                if s is not None:
                    s.stop()
                self.mic_src = Source(self.mixer, want, False, self.log, gain=self.mic_gain)
                self.log("recorder: mic %s" % want)
                if self.mic_id:                 # a chosen mic that is gone: fall back to the default one
                    self.mic_src.ready.wait(2)
                    if not self.mic_src.alive and ids["capture"]:
                        self.mic_src = Source(self.mixer, ids["capture"], False, self.log, gain=self.mic_gain)
                        self.log("recorder: chosen mic failed, default mic %s" % ids["capture"])
                        self.mic_id = ""

    def _run(self):
        co_init()
        en = None
        try:
            en = _enumerator()
            last_dev = 0.0
            last_sync = time.time()
            while not self._stop.is_set():
                now = time.time()
                if now - last_dev >= 2.0 or self._kick.is_set():
                    self._kick.clear()
                    last_dev = now
                    try:
                        self._sync_devices(en)
                    except Exception:
                        self.log("recorder device sync:\n" + traceback.format_exc())
                self.mixer.flush()
                if now - last_sync >= 5.0:
                    last_sync = now
                    self.writer.sync()
                self._kick.wait(0.25)
                if self._stop.is_set():
                    break
        except Exception:
            self.log("recorder session crashed:\n" + traceback.format_exc())
        finally:
            for s in list(self.out_srcs.values()) + ([self.mic_src] if self.mic_src else []):
                s.stop()
            for s in list(self.out_srcs.values()) + ([self.mic_src] if self.mic_src else []):
                s.thread.join(1.5)
            try:
                self.mixer.flush(final=True)
            except Exception:
                self.log("recorder final flush:\n" + traceback.format_exc())
            self.writer.close()
            release(en)
            ole32.CoUninitialize()

    def stop(self, wait=True):
        self._stop.set()
        self._kick.set()
        if wait:
            self.thread.join(5)


# ---------------------------------------------------------------------------
#  Media Foundation encoder: WAV (16-bit mono RATE) -> MP3 / M4A
# ---------------------------------------------------------------------------
mfplat = mfrw = None
MF_VERSION = 0x00020070
MF_MT_MAJOR_TYPE = guid("48EBA18E-F8C9-4687-BF11-0A74C9F96A8F")
MF_MT_SUBTYPE = guid("F7E34C9A-42E8-4714-B74B-CB29D72C35E5")
MF_MT_AUDIO_NUM_CHANNELS = guid("37E48BF5-645E-4C5B-89DE-ADA9E29B696A")
MF_MT_AUDIO_SAMPLES_PER_SECOND = guid("5FAEEAE7-0290-4C31-9E8A-C534F68D9DBA")
MF_MT_AUDIO_BITS_PER_SAMPLE = guid("F2DEB57F-40FA-4764-AA33-ED4F2D1FF669")
MF_MT_AUDIO_AVG_BYTES_PER_SECOND = guid("1AAB75C8-CFEF-451C-AB95-AC034B8E1731")
MF_MT_AUDIO_BLOCK_ALIGNMENT = guid("322DE230-9EEB-43BD-AB7A-FF412251541D")
MFMediaType_Audio = guid("73647561-0000-0010-8000-00AA00389B71")
MFAudioFormat_PCM = guid("00000001-0000-0010-8000-00AA00389B71")
MFAudioFormat_MP3 = guid("00000055-0000-0010-8000-00AA00389B71")
MFAudioFormat_AAC = guid("00001610-0000-0010-8000-00AA00389B71")
SET_U32 = (ctypes.POINTER(GUID), ctypes.c_uint32)
SET_GUID = (ctypes.POINTER(GUID), ctypes.POINTER(GUID))


def _mf():
    global mfplat, mfrw
    if mfplat is None:
        mfplat = ctypes.WinDLL("mfplat")
        mfrw = ctypes.WinDLL("mfreadwrite")
    return mfplat, mfrw


def _media_type(sub, rate, ch, bits=None, avg=None, align=None):
    mp, _ = _mf()
    t = ctypes.c_void_p()
    hr = mp.MFCreateMediaType(ctypes.byref(t))
    if hr < 0:
        raise COMError(hr, "MFCreateMediaType")
    vcall(t, 24, SET_GUID, ctypes.byref(MF_MT_MAJOR_TYPE), ctypes.byref(MFMediaType_Audio), what="SetGUID")
    vcall(t, 24, SET_GUID, ctypes.byref(MF_MT_SUBTYPE), ctypes.byref(sub), what="SetGUID")
    for k, v in ((MF_MT_AUDIO_SAMPLES_PER_SECOND, rate), (MF_MT_AUDIO_NUM_CHANNELS, ch),
                 (MF_MT_AUDIO_BITS_PER_SAMPLE, bits), (MF_MT_AUDIO_AVG_BYTES_PER_SECOND, avg),
                 (MF_MT_AUDIO_BLOCK_ALIGNMENT, align)):
        if v is not None:
            vcall(t, 21, SET_U32, ctypes.byref(k), v, what="SetUINT32")
    return t


FORMATS = {"mp3": (MFAudioFormat_MP3, 12000), "m4a": (MFAudioFormat_AAC, 12000)}   # 96 kbit/s mono: plenty for speech


def encode(wav_path, out_path, fmt, progress=None, cancel=None):
    """Encode a WAV written by WavWriter into fmt ('mp3' / 'm4a') with Media Foundation.
    Runs in the calling thread (call it from a worker). Raises on failure."""
    sub, avg = FORMATS[fmt]
    mp, rw = _mf()
    co_init()
    hr = mp.MFStartup(MF_VERSION, 0)
    if hr < 0:
        raise COMError(hr, "MFStartup")
    writer = in_t = out_t = None
    try:
        with open(wav_path, "rb") as f:
            hdr = f.read(44)
            if hdr[:4] != b"RIFF" or hdr[8:16] != b"WAVEfmt ":
                raise ValueError("not a recorder WAV")
            ch, rate = struct.unpack("<HI", hdr[22:28])
            bits = struct.unpack("<H", hdr[34:36])[0]
            total = max(0, os.path.getsize(wav_path) - 44)
            writer = ctypes.c_void_p()
            hr = rw.MFCreateSinkWriterFromURL(ctypes.c_wchar_p(out_path), None, None, ctypes.byref(writer))
            if hr < 0:
                raise COMError(hr, "MFCreateSinkWriterFromURL")
            out_t = _media_type(sub, rate, ch, bits=16 if fmt == "m4a" else None, avg=avg)
            idx = wintypes.DWORD()
            vcall(writer, 3, (ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)), out_t, ctypes.byref(idx),
                  what="AddStream")
            in_t = _media_type(MFAudioFormat_PCM, rate, ch, bits=bits, avg=rate * ch * bits // 8,
                               align=ch * bits // 8)
            vcall(writer, 4, (wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p), idx, in_t, None,
                  what="SetInputMediaType")
            vcall(writer, 5, (), what="BeginWriting")
            block = ch * bits // 8
            chunk = rate * block                 # 1 s per sample
            done = 0
            while True:
                if cancel is not None and cancel():
                    raise RuntimeError("cancelled")
                data = f.read(chunk)
                data = data[:len(data) - len(data) % block]
                if not data:
                    break
                buf = ctypes.c_void_p()
                hr = mp.MFCreateMemoryBuffer(len(data), ctypes.byref(buf))
                if hr < 0:
                    raise COMError(hr, "MFCreateMemoryBuffer")
                smp = ctypes.c_void_p()
                try:
                    p = ctypes.c_void_p()
                    vcall(buf, 3, (ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_void_p),
                          ctypes.byref(p), None, None, what="Lock")
                    ctypes.memmove(p, data, len(data))
                    vcall(buf, 4, (), what="Unlock")
                    vcall(buf, 6, (wintypes.DWORD,), len(data), what="SetCurrentLength")
                    hr = mp.MFCreateSample(ctypes.byref(smp))
                    if hr < 0:
                        raise COMError(hr, "MFCreateSample")
                    vcall(smp, 42, (ctypes.c_void_p,), buf, what="AddBuffer")
                    frames = len(data) // block
                    vcall(smp, 36, (ctypes.c_int64,), done * 10000000 // rate, what="SetSampleTime")
                    vcall(smp, 38, (ctypes.c_int64,), frames * 10000000 // rate, what="SetSampleDuration")
                    vcall(writer, 6, (wintypes.DWORD, ctypes.c_void_p), idx, smp, what="WriteSample")
                    done += frames
                finally:
                    release(smp)
                    release(buf)
                if progress is not None and total:
                    progress(min(1.0, done * block / total))
            vcall(writer, 11, (), what="Finalize")
    finally:
        for p in (in_t, out_t, writer):
            release(p)
        mp.MFShutdown()
        ole32.CoUninitialize()
