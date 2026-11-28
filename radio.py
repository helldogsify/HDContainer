# -*- coding: utf-8 -*-
"""Internet radio for HDContainer (1.19.0): a short list of free background-music stations
in the tray menu, played without any extra package.

Design goals (user, 2026-11-28: «всё должно быть легко и минималистично», «не грузить компы
и сеть», «оптимизация сразу»):
- one row in the tray menu; it unfolds into the station list; no Settings page;
- light streams: the lowest-bitrate variant each station offers (most are 48-128 kbit/s);
- nothing runs while the radio is off: one worker thread exists only while a station plays,
  no tk timers at all (the menu is redrawn only when the state changes and it is open);
- decoding by Windows itself: Media Foundation source reader (HTTP + MP3 / AAC decoders
  are part of Windows), PCM to waveOut through a small ring of buffers (about 1.5 s),
  volume applied to the samples with audioop (stdlib of Python 3.12).

Verified 2026-11-28 on montenegro-pc: every station below opened and decoded through
MFCreateSourceReaderFromURL in 0.1-2.1 s (prototype in ..\\_radio_dev\\proto_mf.py).
"""
import time
import ctypes
import audioop
import threading
import traceback
from ctypes import wintypes
import tkinter as tk

import rec_audio as ra
from rec_audio import vcall, release

# (group, name, url, kbit/s): the lowest-bitrate variant of each station; every URL decoded
# through MF on 2026-11-28 (87 stations) (..\_radio_dev\proto_mf.py)
STATIONS = [
    ("liquid", "Bassdrive", "http://ice.bassdrive.net/stream56", 56),
    ("liquid", "Plush · Atmospheric", "https://azura.drmnbss.org/listen/plushrecs/radio.mp3", 320),
    ("liquid", "Caprice · Liquid", "http://79.111.119.111:8002/liquidfunk", 48),
    ("liquid", "Record · Liquid Funk", "https://radiorecord.hostingradio.ru/liquidfunk96.aacp", 96),
    ("liquid", "Liquid DnB", "https://antares.dribbcast.com/proxy/dave1/stream/", 128),
    ("liquid", "DNB FM", "https://air.dnbfm.ru/listen/player/play", 128),
    ("jungle", "Jungletrain", "https://stream.josh-hill.nl:8000/", 256),
    ("jungle", "Kniteforce", "https://kniteforce.out.airtime.pro/kniteforce_a", 192),
    ("hiphop", "181.FM · Old School", "http://listen.181fm.com/181-oldschool_128k.mp3", 128),
    ("hiphop", "Caprice · Old School", "http://213.141.131.10:8002/oldschoolhiphop", 48),
    ("hiphop", "Astounded Old School", "https://stream.laut.fm/astounded", 128),
    ("hiphop", "West Coast · G-Funk", "https://r.bgp.rodeo/listen/west_coast/radio.mp3", 320),
    ("hiphop", "Caprice · West Coast", "http://79.120.39.202:8002/gangstarap", 48),
    ("lofi", "Record · Lo-Fi", "https://radiorecord.hostingradio.ru/lofi96.aacp", 96),
    ("lofi", "REYFM · #lofi", "https://listen.reyfm.de/lofi_64kbps.mp3", 64),
    ("lofi", "Caprice · Lo-Fi", "http://79.111.14.76:8002/lo-fi", 48),
    ("lofi", "Hotmix Lo-Fi", "https://streaming.hotmixradio.com/hotmix-lofi-en-mp3", 128),
    ("lofi", "Chillofi", "http://streams.dez.ovh:8000/radio.mp3", 192),
    ("lofi", "SomaFM · Fluid", "https://ice5.somafm.com/fluid-128-mp3", 128),
    ("chill", "SomaFM · Groove Salad", "https://ice2.somafm.com/groovesalad-128-mp3", 128),
    ("chill", "SomaFM · Beat Blender", "https://ice4.somafm.com/beatblender-128-mp3", 128),
    ("chill", "Caprice · Trip-Hop", "http://79.111.14.76:8002/triphop", 48),
    ("chill", "Caprice · Downtempo", "http://79.120.77.11:8002/downtempo", 48),
    ("chill", "Bristol Sound", "https://thebristolsoundwebradio.stream.laut.fm/thebristolsoundwebradio", 128),
    ("ambient", "SomaFM · Drone Zone", "https://ice2.somafm.com/dronezone-128-mp3", 128),
    ("ambient", "SomaFM · Space Station", "https://ice6.somafm.com/spacestation-128-aac", 128),
    ("ambient", "Record · Ambient", "https://radiorecord.hostingradio.ru/ambient96.aacp", 96),
    ("house", "Caprice · Deep House", "http://79.120.39.202:8002/deephouse", 48),
    ("house", "Costa del Mar · Deep", "http://stream.cdm-chillout.com:8012/stream-mp3-Deep", 96),
    ("house", "Caprice · Future Garage", "http://79.120.39.202:8002/futuregarage", 48),
    ("jazz", "SomaFM · Sonic Universe", "https://ice4.somafm.com/sonicuniverse-128-mp3", 128),
    ("jazz", "Caprice · Acid Jazz", "http://79.111.14.76:8002/acidjazz", 48),
    ("jazz", "Caprice · Cool Jazz", "http://79.120.39.202:8002/cooljazz", 48),
    ("jazz", "Caprice · Smooth Jazz", "http://79.120.39.202:8002/smoothjazz", 48),
    ("synth", "Nightwave Plaza", "http://radio.plaza.one/mp3", 128),
    ("synth", "Caprice · Retrowave", "http://79.120.39.202:8002/retrowave", 48),
    ("synth", "Laut.fm · Vaporwave", "http://vaporwave.stream.laut.fm/vaporwave", 128),
    ("breaks", "Caprice · Broken Beat", "http://79.120.77.11:8002/brokenbeat", 48),
    ("breaks", "Caprice · Glitch-Hop", "http://79.111.14.76:8002/glitch", 48),
    ("breaks", "SomaFM · Dub Step Beyond", "https://ice4.somafm.com/dubstep-128-mp3", 128),
    ("idm", "SomaFM · cliqhop idm", "https://ice6.somafm.com/cliqhop-128-mp3", 128),
    ("idm", "Caprice · IDM", "http://79.111.14.76:8002/idm", 48),
    ("idm", "Caprice · Indie Electronic", "http://79.111.119.111:8002/indieelectronic", 48),
    ("techno", "Caprice · Dub Techno", "http://79.120.39.202:8002/dubtechno", 48),
    ("techno", "Caprice · Minimal Techno", "http://79.120.39.202:8002/mintechno", 48),
    ("techno", "Caprice · Minimal Tech House", "http://79.111.119.111:8002/mintechhouse", 48),
    ("techno", "Record · Techno", "https://radiorecord.hostingradio.ru/techno96.aacp", 96),
    ("trance", "Caprice · Dream Trance", "http://79.120.39.202:8002/dreamtrance", 48),
    ("trance", "Caprice · Forest Psytrance", "http://213.141.131.10:8002/forestpsytrance", 48),
    ("dub", "Caprice · Dub", "http://79.111.14.76:8002/dub", 48),
    ("dub", "Record · Reggae", "https://radiorecord.hostingradio.ru/reggae96.aacp", 96),
    ("house", "Caprice · Organic House", "http://79.120.12.130:8002/organichouse", 48),
    ("house", "Caprice · Balearic", "http://213.141.131.10:8002/balearichouse", 48),
    ("lounge", "SomaFM · Illinois Street Lounge", "https://ice5.somafm.com/illstreet-128-mp3", 128),
    ("lounge", "SomaFM · Secret Agent", "https://ice4.somafm.com/secretagent-128-mp3", 128),
    ("lounge", "Caprice · Lounge", "http://79.111.14.76:8002/lounge", 48),
    ("lounge", "Caprice · Bossa Nova", "http://79.111.14.76:8002/bossanova", 48),
    ("lounge", "Caprice · Electro Swing", "http://213.141.131.10:8002/electroswing", 48),
    ("funk", "SomaFM · Seven Inch Soul", "https://ice2.somafm.com/7soul-128-mp3", 128),
    ("funk", "Caprice · Electro Funk", "http://79.111.119.111:8002/electro", 48),
    ("funk", "Caprice · Soul Jazz", "http://79.111.14.76:8002/souljazz", 48),
    ("ambient", "SomaFM · Deep Space One", "https://ice4.somafm.com/deepspaceone-128-mp3", 128),
    ("ambient", "Caprice · New Age", "http://79.120.77.11:8002/newage", 48),
    ("classical", "Caprice · Piano", "http://79.120.77.11:8002/piano", 48),
    ("classical", "Caprice · Modern Classical", "http://79.120.39.202:8002/modernclassical", 48),
    ("classical", "Caprice · Neoclassical", "http://79.120.39.202:8002/neoclassical", 48),
    ("classical", "Caprice · Classical", "http://79.120.77.11:8002/classical", 48),
    ("synth", "Caprice · Cyberpunk", "http://79.120.12.130:8002/cyberpunk", 48),
    ("synth", "SomaFM · Vaporwaves", "https://ice5.somafm.com/vaporwaves-128-mp3", 128),
    ("rock", "Caprice · Classic Rock", "http://79.120.77.11:8002/rock", 48),
    ("rock", "Caprice · Blues Rock", "http://79.120.39.202:8002/bluesrock", 48),
    ("rock", "SomaFM · Underground 80s", "https://ice6.somafm.com/u80s-128-mp3", 128),
    ("rock", "SomaFM · Left Coast 70s", "https://ice2.somafm.com/seventies-128-mp3", 128),
    ("rock", "SomaFM · Indie Pop Rocks", "https://ice4.somafm.com/indiepop-128-mp3", 128),
    ("jazz", "Caprice · Hard Bop", "http://79.111.14.76:8002/hardbop", 48),
    ("jazz", "Caprice · Jazz Fusion", "http://79.111.119.111:8002/jazzfusion", 48),
    ("jazz", "Caprice · Piano Jazz", "http://79.111.119.111:8002/pianojazz", 48),
    ("jazz", "Caprice · Guitar Jazz", "http://79.120.77.11:8002/guitarjazz", 48),
    ("jazz", "101 Smooth Jazz", "http://jking.cdnstream1.com/b22139_128mp3", 128),
    ("classical", "Your Classical · Relax", "http://relax.stream.publicradio.org/relax.mp3", 128),
    ("classical", "Classic FM", "http://media-ice.musicradio.com/ClassicFMMP3", 128),
    ("classical", "France Musique", "http://direct.francemusique.fr/live/francemusique-midfi.mp3", 128),
    ("classical", "RMF Classic", "http://31.192.216.8/RMFCLASSIC48", 48),
    ("classical", "Caprice · Chamber Music", "http://213.141.131.10:8002/chamber", 48),
    ("classical", "Caprice · Contemporary Classical", "http://79.111.119.111:8002/contclass", 48),
    ("classical", "Caprice · Romantic", "http://79.120.39.202:8002/romantic", 48),
    ("classical", "Caprice · Organ", "http://79.120.77.11:8002/organ", 48),
]
GROUPS = ["liquid", "jungle", "breaks", "hiphop", "lofi", "chill", "ambient", "idm", "house", "techno",
          "trance", "dub", "lounge", "funk", "jazz", "synth", "classical", "rock", "own"]

RSTR = {
    "tab": {"en": "Radio", "ru": "Радио", "es": "Radio", "pt": "Rádio", "de": "Radio", "fr": "Radio", "zh": "电台"},
    "liquid": {"en": "Liquid · Atmospheric DnB"},
    "jungle": {"en": "Jungle"},
    "hiphop": {"en": "Old School Hip-Hop"},
    "lofi": {"en": "Lo-Fi · Beats"},
    "chill": {"en": "Chillout · Trip-Hop"},
    "ambient": {"en": "Ambient"},
    "house": {"en": "House · Garage"},
    "jazz": {"en": "Jazz"},
    "synth": {"en": "Synthwave · Vaporwave"},
    "breaks": {"en": "Breaks · Glitch · Dubstep"},
    "idm": {"en": "IDM · Electronica"},
    "techno": {"en": "Techno · Minimal"},
    "trance": {"en": "Trance · Psy"},
    "dub": {"en": "Dub · Reggae"},
    "lounge": {"en": "Lounge · Bossa · Swing"},
    "funk": {"en": "Funk · Soul"},
    "classical": {"en": "Classical · Piano"},
    "rock": {"en": "Rock · Indie"},
    "own": {"en": "Personal"},
    "own_empty": {"en": "empty for now", "ru": "пока пусто", "es": "vacío por ahora", "pt": "vazio por enquanto", "de": "noch leer", "fr": "vide pour l'instant", "zh": "暂无"},
    "paused": {"en": "Pause", "ru": "Пауза", "es": "Pausa", "pt": "Pausa", "de": "Pause", "fr": "Pause", "zh": "暂停"},
    "off": {"en": "Radio off", "ru": "Радио выключено", "es": "Radio apagada", "pt": "Rádio desligado", "de": "Radio aus", "fr": "Radio éteinte", "zh": "电台已关闭"},
    "hk_row": {"en": "Radio", "ru": "Радио", "es": "Radio", "pt": "Rádio", "de": "Radio", "fr": "Radio", "zh": "电台"},
    "hk_keys": {"en": "Ctrl + Num: 0 on/off · 1 ◀ · 2 pause · 3 ▶ · . genre (twice: back)", "ru": "Ctrl + Num: 0 вкл/выкл · 1 ◀ · 2 пауза · 3 ▶ · , жанр (дважды: назад)"},
    "no_link": {"en": "no connection", "ru": "нет связи", "es": "sin conexión", "pt": "sem conexão", "de": "keine Verbindung", "fr": "pas de connexion", "zh": "无连接"},
    "tip": {"en": "Wheel: volume", "ru": "Колесо мыши: громкость", "es": "Rueda: volumen", "pt": "Roda: volume", "de": "Mausrad: Lautstärke", "fr": "Molette : volume", "zh": "滚轮：音量"},
}
OWN = []        # ("own", name, url, kbit/s) from settings["radio"]["own"] = [[name, url], ...]
LANG_GET = lambda: "en"


def R(key):
    s = RSTR.get(key, {})
    return s.get(LANG_GET()) or s.get("en") or key


def station(name):
    for st in STATIONS + OWN:
        if st[1] == name:
            return st
    return None


# ---------------------------------------------------------------------------
#  waveOut (winmm): a few buffers in flight, CALLBACK_EVENT so the worker sleeps in the kernel
# ---------------------------------------------------------------------------
winmm = ctypes.WinDLL("winmm")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WAVE_MAPPER = 0xFFFFFFFF
CALLBACK_EVENT = 0x00050000
WHDR_DONE = 0x00000001


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]


class WAVEHDR(ctypes.Structure):
    pass


WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                    ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                    ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                    ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]
HWO = ctypes.c_void_p
winmm.waveOutOpen.argtypes = [ctypes.POINTER(HWO), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
                              ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
for _n in ("waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutWrite"):
    getattr(winmm, _n).argtypes = [HWO, ctypes.POINTER(WAVEHDR), wintypes.UINT]
for _n in ("waveOutReset", "waveOutClose", "waveOutPause", "waveOutRestart"):
    getattr(winmm, _n).argtypes = [HWO]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, ctypes.c_void_p]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

CHUNK_S = 0.25          # one waveOut buffer
QUEUE_N = 6             # buffers in flight: 1.5 s, enough for stream jitter (MF buffers too)
PREROLL_N = 3           # start the sound once this much is queued


class WaveOut:
    def __init__(self, rate, ch, bits):
        self.block = ch * bits // 8
        self.rate = rate
        fmt = WAVEFORMATEX(1, ch, rate, rate * self.block, self.block, bits, 0)
        self.event = kernel32.CreateEventW(None, False, False, None)
        self.h = HWO()
        mm = winmm.waveOutOpen(ctypes.byref(self.h), WAVE_MAPPER, ctypes.byref(fmt), self.event, 0, CALLBACK_EVENT)
        if mm:
            kernel32.CloseHandle(self.event)
            raise OSError("waveOutOpen failed: %d" % mm)
        self.lock = threading.Lock()
        self.busy = []          # [(WAVEHDR, buffer)] in order
        self.paused = True
        winmm.waveOutPause(self.h)

    def reap(self):
        while self.busy and self.busy[0][0].dwFlags & WHDR_DONE:
            hdr, _buf = self.busy.pop(0)
            winmm.waveOutUnprepareHeader(self.h, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))

    def write(self, data, stop):
        """Queue data; blocks (in the kernel) while QUEUE_N buffers are in flight."""
        while True:
            with self.lock:
                if self.h is None or stop.is_set():
                    return
                self.reap()
                if len(self.busy) < QUEUE_N:
                    buf = ctypes.create_string_buffer(data, len(data))
                    hdr = WAVEHDR()
                    hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
                    hdr.dwBufferLength = len(data)
                    winmm.waveOutPrepareHeader(self.h, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
                    winmm.waveOutWrite(self.h, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
                    self.busy.append((hdr, buf))
                    if self.paused and len(self.busy) >= PREROLL_N:
                        self.paused = False
                        winmm.waveOutRestart(self.h)
                    return
            kernel32.WaitForSingleObject(self.event, 500)

    def starving(self):
        """True when everything queued has been played (the stream stalled): preroll again."""
        with self.lock:
            if self.h is None:
                return
            self.reap()
            if not self.busy and not self.paused:
                self.paused = True
                winmm.waveOutPause(self.h)

    def close(self):
        with self.lock:
            h, self.h = self.h, None
            if h is None:
                return
            winmm.waveOutReset(h)
            for hdr, _buf in self.busy:
                winmm.waveOutUnprepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
            self.busy = []
            winmm.waveOutClose(h)

    def dispose(self):
        """Worker only, after close(): the event may still be waited on until then."""
        self.close()
        if self.event:
            kernel32.CloseHandle(self.event)
            self.event = None


# ---------------------------------------------------------------------------
#  Player: one worker thread per started station
# ---------------------------------------------------------------------------
FIRST_AUDIO = 0xFFFFFFFD
READ_ARGS = (wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
             ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_void_p))
U32_ARGS = (ctypes.POINTER(ra.GUID), ctypes.POINTER(ctypes.c_uint32))
RETRY_S = (2, 5, 10, 20, 30)        # reconnect pauses; after the last one the player gives up


class Player:
    """state: 'connecting' | 'playing' | 'error'; on_state(player) is called from the worker."""

    def __init__(self, url, volume, on_state, log):
        self.url = url
        self.volume = volume
        self.on_state = on_state
        self.log = log
        self.state = "connecting"
        self.stop_ev = threading.Event()
        self.out = None
        self.thread = threading.Thread(target=self._run, name="radio", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()
        out = self.out
        if out is not None:
            out.close()             # silent at once, even while ReadSample still waits on the network

    def _set(self, state):
        if self.state != state and not self.stop_ev.is_set():
            self.state = state
            try:
                self.on_state(self)
            except Exception:
                pass

    def _run(self):
        ra.co_init()
        mp, rw = ra._mf()
        mp.MFStartup(ra.MF_VERSION, 0)
        fails = 0
        try:
            while not self.stop_ev.is_set():
                played = False
                try:
                    played = self._session(mp, rw)
                except Exception as ex:
                    self.log("radio: %s: %r" % (self.url, ex))
                if self.stop_ev.is_set():
                    break
                fails = 0 if played else fails + 1
                if fails >= len(RETRY_S):
                    self._set("error")
                    break
                self._set("connecting")
                if self.stop_ev.wait(RETRY_S[max(0, fails - 1)]):
                    break
        finally:
            mp.MFShutdown()

    def _session(self, mp, rw):
        """One connection: open, decode, play until the stream ends or stop. Returns True
        when sound was played (the next failure then counts from zero)."""
        reader = ptype = ctype = None
        played = False
        try:
            reader = ctypes.c_void_p()
            hr = rw.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(self.url), None, ctypes.byref(reader))
            if hr < 0:
                raise ra.COMError(hr, "MFCreateSourceReaderFromURL")
            if self.stop_ev.is_set():
                return False
            ptype = ctypes.c_void_p()
            mp.MFCreateMediaType(ctypes.byref(ptype))
            vcall(ptype, 24, ra.SET_GUID, ctypes.byref(ra.MF_MT_MAJOR_TYPE), ctypes.byref(ra.MFMediaType_Audio))
            vcall(ptype, 24, ra.SET_GUID, ctypes.byref(ra.MF_MT_SUBTYPE), ctypes.byref(ra.MFAudioFormat_PCM))
            vcall(reader, 7, (wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p), FIRST_AUDIO, None, ptype,
                  what="SetCurrentMediaType")
            ctype = ctypes.c_void_p()
            vcall(reader, 6, (wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)), FIRST_AUDIO, ctypes.byref(ctype),
                  what="GetCurrentMediaType")

            def u32(k):
                v = ctypes.c_uint32()
                vcall(ctype, 7, U32_ARGS, ctypes.byref(k), ctypes.byref(v), what="GetUINT32")
                return v.value
            rate = u32(ra.MF_MT_AUDIO_SAMPLES_PER_SECOND)
            ch = u32(ra.MF_MT_AUDIO_NUM_CHANNELS)
            bits = u32(ra.MF_MT_AUDIO_BITS_PER_SAMPLE)
            out = WaveOut(rate, ch, bits)
            self.out = out
            if self.stop_ev.is_set():
                return False
            width = bits // 8
            chunk = int(rate * CHUNK_S) * out.block
            pend = bytearray()
            idx, flags, ts = wintypes.DWORD(), wintypes.DWORD(), ctypes.c_int64()
            p, n = ctypes.c_void_p(), wintypes.DWORD()
            while not self.stop_ev.is_set():
                smp = ctypes.c_void_p()
                vcall(reader, 9, READ_ARGS, FIRST_AUDIO, 0, ctypes.byref(idx), ctypes.byref(flags),
                      ctypes.byref(ts), ctypes.byref(smp), what="ReadSample")
                if flags.value & 3:             # error / end of stream
                    release(smp)
                    break
                if not smp.value:
                    continue
                buf = ctypes.c_void_p()
                try:
                    vcall(smp, 41, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(buf), what="ToContiguous")
                    vcall(buf, 3, (ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)),
                          ctypes.byref(p), None, ctypes.byref(n), what="Lock")
                    pend += ctypes.string_at(p, n.value)
                    vcall(buf, 4, (), what="Unlock")
                finally:
                    release(buf)
                    release(smp)
                out.starving()
                while len(pend) >= chunk:
                    data = bytes(pend[:chunk])
                    del pend[:chunk]
                    v = self.volume
                    if v < 0.999:
                        data = audioop.mul(data, width, v * v)      # square: closer to how loudness is heard
                    out.write(data, self.stop_ev)
                    if not played:
                        played = True
                        self._set("playing")
            return played
        finally:
            out, self.out = self.out, None
            if out is not None:
                out.dispose()
            release(ctype)
            release(ptype)
            release(reader)


# ---------------------------------------------------------------------------
#  Controller: tray menu rows, saved station and volume (settings["radio"])
# ---------------------------------------------------------------------------
ICON_MUSIC, ICON_STOP, ICON_PLAY = chr(0xE8D6), chr(0xE71A), chr(0xE768)


class RadioController:
    def __init__(self, app, lang_get, log):
        global LANG_GET
        LANG_GET = lang_get
        self.app = app
        self.log = log
        self.player = None
        self.current = None             # station name while a player exists
        self.open = False               # list unfolded in the tray menu
        s = app.settings.get("radio", {}) if isinstance(app.settings.get("radio"), dict) else {}
        self.volume = min(1.0, max(0.0, float(s.get("volume", 0.6))))
        OWN[:] = [("own", str(n), str(u), 0) for n, u in (s.get("own") or []) if n and u]
        self.groups_open = set(g for g in (s.get("open") or []) if g in GROUPS)
        self.genre_last = {g: n for g, n in (s.get("genre_last") or {}).items() if station(n)}
        self.keys_on = bool(s.get("keys", True))
        self.paused = False
        self.track = ""                 # now playing title of the current station ("" = unknown)
        self._genre_job = None          # pending single «.» (a second press within GENRE_DOUBLE_MS = back)
        self.caption = None
        self.keys = NumpadKeys(lambda act: app.post(lambda: self.key(act)))
        if self.keys_on:
            self._hook(True)
        self.last = s.get("last") if station(s.get("last") or "") else None

    # ---- playback ----
    def play(self, name):
        st = station(name)
        if st is None:
            return
        self.stop(save=False)
        self.current = name
        self.last = name
        self.paused = False
        self.genre_last[st[0]] = name
        self.player = Player(st[2], self.volume, self._on_state, self.log)
        self._save()
        self._fetch_track(st)

    def stop(self, save=True):
        pl, self.player = self.player, None
        self.current = None
        if pl is not None:
            pl.stop()
        if save:
            self._save()

    def state(self):
        return self.player.state if self.player is not None else None

    def _on_state(self, player):            # worker thread
        def ui():
            if player is self.player:
                self.app._repopup()
        self.app.post(ui)

    def set_volume(self, v):
        self.volume = min(1.0, max(0.0, round(v, 2)))
        if self.player is not None:
            self.player.volume = self.volume

    def _save(self):
        try:
            d = dict(self.app.settings.get("radio") or {})      # keeps "own"
            d.update(volume=self.volume, last=self.last, open=sorted(self.groups_open),
                     genre_last=self.genre_last, keys=self.keys_on)
            self.app._set_setting("radio", d)
        except Exception:
            self.log("radio settings save failed:\n" + traceback.format_exc())

    def shutdown(self):
        self.stop(save=False)
        self._hook(False)
        if self.caption is not None:
            self.caption.destroy()
            self.caption = None

    # ---- now playing title: once per pick, on a short-lived thread ----
    def _fetch_track(self, st):
        self.track = ""
        name = st[1]

        def run():
            try:
                t = fetch_title(st[2])
            except Exception as ex:
                self.log("radio title %s: %r" % (name, ex))
                return
            if t:
                self.app.post(lambda: self._got_track(name, t))
        threading.Thread(target=run, name="radio-title", daemon=True).start()

    def _got_track(self, name, t):
        if name != self.current:
            return
        self.track = t
        if self.caption is not None:
            self.caption.update_track(R(station(name)[0]), name, t)

    # ---- Ctrl + Numpad ----
    def _hook(self, on):
        try:
            import voice_sys as vs
            if on:
                vs.HUB.add(self.keys)
            else:
                vs.HUB.remove(self.keys)
        except Exception:
            self.log("radio keys hook failed:\n" + traceback.format_exc())

    def set_keys(self, on):
        self.keys_on = bool(on)
        self._hook(self.keys_on)
        self._save()

    def _say(self, genre="", name="", track=""):
        if self.caption is None:
            self.caption = Caption(self.app.root, self.log)
        self.caption.show(genre, name, track)

    def _announce(self):
        st = station(self.current) if self.current else None
        if st is not None:
            self._say(R(st[0]), st[1], self.track)

    def _genres(self):
        return [g for g in GROUPS if any(s[0] == g for s in STATIONS + OWN)]

    def _here(self):
        """Genre and station the keys move from: the playing one, else the last one."""
        st = station(self.current or self.last or "")
        if st is None:
            st = STATIONS[0]
        return st

    def _pick_genre(self, step):
        gs = self._genres()
        here = self._here()
        g = gs[(gs.index(here[0]) + step) % len(gs)] if here[0] in gs else gs[0]
        names = [s[1] for s in STATIONS + OWN if s[0] == g]
        self.play(self.genre_last.get(g) if self.genre_last.get(g) in names else names[0])
        self._announce()
        self.app._repopup()

    GENRE_DOUBLE_MS = 350

    def key(self, act):
        """tk thread: one Ctrl+Numpad action."""
        if act == "genre":
            # one press = next genre, but only after a short wait: a second press within it
            # means «back» (user: «Alt+.+. - вернуть предыдущий жанр», now Ctrl), and no stream is
            # opened for the genre that would be skipped at once
            if self._genre_job is not None:
                self.app.root.after_cancel(self._genre_job)
                self._genre_job = None
                self._pick_genre(-1)
            else:
                def single():
                    self._genre_job = None
                    self._pick_genre(1)
                self._genre_job = self.app.root.after(self.GENRE_DOUBLE_MS, single)
            return
        if act == "power":
            if self.player is not None:
                self.stop()
                self._say(name=R("off"))
            else:
                self.play(self._here()[1])
                self._announce()
        elif act == "pause":
            if self.player is not None:
                name = self.current
                self.stop()
                self.paused = True
                self._say(name=R("paused"), track=name)
            else:
                self.play(self._here()[1])
                self._announce()
        elif act in ("prev", "next"):
            here = self._here()
            names = [s[1] for s in STATIONS + OWN if s[0] == here[0]]
            i = names.index(here[1]) if here[1] in names else 0
            self.play(names[(i + (1 if act == "next" else -1)) % len(names)])
            self._announce()
        self.app._repopup()

    # ---- tray menu ----
    def build_popup(self, parent, width, col, font, font_sm, icon_font):
        """One row: «Радио» (or the playing station + volume) with play/stop on the right;
        a click unfolds the station list in place; the wheel over it changes the volume."""
        st = self.state()
        playing = st is not None
        row = tk.Frame(parent, bg=col["sf"], height=32, width=width)
        row.pack(fill="x")
        row.pack_propagate(False)
        g = tk.Label(row, text=ICON_MUSIC, bg=col["sf"], fg=col["ac"] if playing else col["dim"],
                     font=(icon_font, 11), width=2, cursor="hand2")
        g.pack(side="left", padx=(14, 8))
        btn = tk.Label(row, text=ICON_STOP if playing else ICON_PLAY, bg=col["sf"],
                       fg=col["tx"] if playing or self.last else col["dim"],
                       font=(icon_font, 10), width=3, cursor="hand2")
        btn.pack(side="right", fill="y", padx=(0, 6))
        # connecting = the name dimmed with an ellipsis, no connection = the name in red
        text = (self.current + ("…" if st == "connecting" else "")) if playing else R("tab")
        fg = {"connecting": col["dim"], "error": col["err"]}.get(st, col["tx"])
        vol = tk.Label(row, text="%d%%" % round(self.volume * 100) if st == "playing" else "",
                       bg=col["sf"], fg=col["dim"], font=font_sm)
        vol.pack(side="right", padx=(4, 0))
        t = tk.Label(row, text=text, bg=col["sf"], fg=fg, font=font, anchor="w", cursor="hand2")
        t.pack(side="left", fill="x", expand=True)
        widgets = (row, g, t, vol, btn)

        def paint(c):
            for w in widgets:
                w.configure(bg=c)

        def fold(_e=None):
            self.open = not self.open
            self.app._repopup()

        def toggle(_e=None):
            if self.player is not None:
                self.stop()
            elif self.last:
                self.play(self.last)
            else:
                self.open = True
            self.app._repopup()
            return "break"

        def wheel(e):
            if self.player is None:
                return
            self.set_volume(self.volume + (0.05 if e.delta > 0 else -0.05))
            vol.configure(text="%d%%" % round(self.volume * 100))
            self._save_later()
        for w in widgets:
            w.bind("<Enter>", lambda _e: paint(col["hv"]))
            w.bind("<Leave>", lambda _e: paint(col["sf"]))
            w.bind("<MouseWheel>", wheel)
            if w is not btn:
                w.bind("<Button-1>", fold)
        btn.bind("<Button-1>", toggle)
        try:
            import lan_ui
            lan_ui.Tooltip(t, R("no_link") if st == "error" else R("tip"), lan_ui.colors())
        except Exception:
            pass
        if not self.open:
            return
        cur = station(self.current) if self.current else None
        for grp in GROUPS:
            items = [s[1] for s in STATIONS + OWN if s[0] == grp]
            opened = grp in self.groups_open
            self._group_row(parent, grp, opened, cur is not None and cur[0] == grp, col, font, icon_font)
            if not opened:
                continue
            for name in items:
                self._station_row(parent, name, width, col, font)
            if not items:
                tk.Label(parent, text=R("own_empty"), bg=col["sf"], fg=col["dim"], font=font_sm,
                         anchor="w").pack(fill="x", padx=(58, 14), pady=(0, 2))

    def _group_row(self, parent, grp, opened, has_current, col, font, icon_font):
        """A genre header: a click folds / unfolds its stations (the open set is saved)."""
        row = tk.Frame(parent, bg=col["sf"], cursor="hand2")
        row.pack(fill="x")
        ch = tk.Label(row, text=chr(0xE70E) if opened else chr(0xE70D), bg=col["sf"], fg=col["dim"],
                      font=(icon_font, 7), cursor="hand2")
        ch.pack(side="left", padx=(44, 6))
        t = tk.Label(row, text=R(grp), bg=col["sf"], fg=col["ac"] if has_current else col["tx"], font=font,
                     anchor="w", cursor="hand2")
        t.pack(side="left", fill="x", expand=True, padx=(0, 14), ipady=2)
        parts = (row, ch, t)

        def click(_e):
            self.groups_open.symmetric_difference_update({grp})
            self._save()
            self.app._repopup()
        for w in parts:
            w.bind("<Enter>", lambda _e: [x.configure(bg=col["hv"]) for x in parts])
            w.bind("<Leave>", lambda _e: [x.configure(bg=col["sf"]) for x in parts])
            w.bind("<Button-1>", click)

    def _station_row(self, parent, name, width, col, font):
        cur = name == self.current
        r = tk.Label(parent, text=name, bg=col["sf"], fg=col["ac"] if cur else col["tx"], font=font,
                     anchor="w", cursor="hand2", padx=0)
        r.pack(fill="x", padx=(58, 14), ipady=1)

        def click(_e):
            if name == self.current:
                self.stop()
            else:
                self.play(name)
            self.app._repopup()
        r.bind("<Enter>", lambda _e: r.configure(bg=col["hv"]))
        r.bind("<Leave>", lambda _e: r.configure(bg=col["sf"]))
        r.bind("<Button-1>", click)

    _save_job = None

    def _save_later(self):
        """Wheel ticks come in bursts: write the settings file once, 1 s after the last."""
        if self._save_job is not None:
            try:
                self.app.root.after_cancel(self._save_job)
            except Exception:
                pass

        def run():
            self._save_job = None
            self._save()
        self._save_job = self.app.root.after(1000, run)


# ---------------------------------------------------------------------------
#  Now playing title: one short look at the ICY metadata (about 16 KB), only when a
#  station is picked; no polling (user: «не грузить сеть»)
# ---------------------------------------------------------------------------
def fetch_title(url, timeout=6.0):
    import re
    import urllib.request
    req = urllib.request.Request(url, headers={"Icy-MetaData": "1", "User-Agent": "HDContainer"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        mi = int(r.headers.get("icy-metaint") or 0)
        if not mi or mi > 256 * 1024:
            return ""
        left = mi
        while left > 0:
            b = r.read(min(left, 16384))
            if not b:
                return ""
            left -= len(b)
        n = r.read(1)
        if not n or not n[0]:
            return ""
        meta = r.read(n[0] * 16)
    text = ""
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            text = meta.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    m = re.search(r"StreamTitle='(.*?)';", text, re.S)
    t = (m.group(1) if m else "").strip(" -\x00")
    return "" if t.lower() in ("", "unknown", "-", "live") else t


# ---------------------------------------------------------------------------
#  Ctrl + Numpad keys (a handler of voice_sys.HUB, the one shared keyboard hook)
# ---------------------------------------------------------------------------
VK_NUM0, VK_NUM1, VK_NUM2, VK_NUM3 = 0x60, 0x61, 0x62, 0x63
VK_DECIMAL, VK_SEPARATOR = 0x6E, 0x6C
KEY_ACT = {VK_NUM0: "power", VK_NUM1: "prev", VK_NUM2: "pause", VK_NUM3: "next",
           VK_DECIMAL: "genre", VK_SEPARATOR: "genre"}
_OTHER_MODS = (0x10, 0x12, 0x5B, 0x5C)             # Shift, Alt, Win: another chord, not ours


class NumpadKeys:
    """Ctrl + Num0 / Num1 / Num2 / Num3 / Num-decimal (user: «только все Alt+», then «давай на
    Ctrl управление радио»: Ctrl has no Alt-code typing, no window menu and no Left-Alt
    dictation to collide with). Ctrl alone is never swallowed."""

    def __init__(self, on_key):
        self.on_key = on_key            # called on the hook thread: must only queue
        self._swallowed = set()

    def _handle(self, vk, up):
        act = KEY_ACT.get(vk)
        if act is None:
            return False
        if up:
            if vk in self._swallowed:
                self._swallowed.discard(vk)
                return True
            return False
        import voice_sys as vs
        u = ctypes.windll.user32
        if not u.GetAsyncKeyState(0x11) & 0x8000:            # Ctrl
            return False
        if any(u.GetAsyncKeyState(m) & 0x8000 for m in _OTHER_MODS) or vs.hotkeys_excluded_now():
            return False
        if vk not in self._swallowed:   # auto-repeat of a held key: one action only
            self._swallowed.add(vk)
            self.on_key(act)
        return True


# ---------------------------------------------------------------------------
#  Caption at the top of the screen: genre / station / track in white, then it fades out.
#  Layered, click-through, never activated; a tk timer runs only while it is visible.
# ---------------------------------------------------------------------------
class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


_u32 = ctypes.WinDLL("user32")
_u32.MonitorFromPoint.restype = ctypes.c_void_p
_u32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
_u32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


class Caption:
    SHOW_MS, FADE_MS, STEP_MS = 2600, 450, 30
    CLS = "HDContainerRadioCaption"

    def __init__(self, root, log):
        import recorder as rc           # its Win32 / Pillow helpers (already loaded by the app)
        self.rc, self.root, self.log = rc, root, log
        self.hwnd = None
        self._dc = None
        self._job = None
        self._alpha = 255
        self._lines = None
        self._img = None
        self._xy = (0, 0)
        self._proc = rc.WNDPROC(self._wndproc)
        self.s = rc._dpi_scale()

    def _wndproc(self, hwnd, msg, wp, lp):
        if msg == 0x0021:                       # WM_MOUSEACTIVATE
            return 3                            # MA_NOACTIVATE
        return self.rc.u32.DefWindowProcW(hwnd, msg, wp, lp)

    def _create(self):
        rc = self.rc
        hinst = rc.k32.GetModuleHandleW(None)
        wc = rc.WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(rc.WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = self.CLS
        rc.u32.RegisterClassExW(ctypes.byref(wc))
        ex = rc.WS_EX_LAYERED | rc.WS_EX_TOPMOST | rc.WS_EX_TOOLWINDOW | rc.WS_EX_NOACTIVATE | 0x20  # TRANSPARENT
        self.hwnd = rc.u32.CreateWindowExW(ex, self.CLS, "HDContainer radio", rc.WS_POPUP,
                                           0, 0, 10, 10, None, None, hinst, None)

    def _render(self, lines):
        from PIL import Image, ImageDraw, ImageFilter
        rc, s = self.rc, self.s
        f_small = rc._font(["segoeui.ttf", "arial.ttf"], int(12 * s))
        f_big = rc._font(["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"], int(19 * s))
        f_mid = rc._font(["segoeui.ttf", "arial.ttf"], int(14 * s))
        genre, name, track = lines
        specs = []                              # (text, font, alpha)
        if genre:
            specs.append((genre.upper(), f_small, 175))
        if name:
            specs.append((name, f_big, 255))
        if track:
            specs.append((track if len(track) <= 90 else track[:89] + "…", f_mid, 220))
        probe = ImageDraw.Draw(Image.new("L", (1, 1)))
        boxes = [probe.textbbox((0, 0), t, font=f) for t, f, _a in specs]
        gap, pad = int(4 * s), int(18 * s)
        w = max(b[2] - b[0] for b in boxes) + 2 * pad
        h = sum(b[3] - b[1] for b in boxes) + gap * (len(specs) - 1) + 2 * pad
        mask = Image.new("L", (w, h), 0)        # all text, for the halo
        fill = Image.new("L", (w, h), 0)        # text with each line's own opacity
        dm, df = ImageDraw.Draw(mask), ImageDraw.Draw(fill)
        y = pad
        for (t, f, a), b in zip(specs, boxes):
            x = (w - (b[2] - b[0])) // 2 - b[0]
            dm.text((x, y - b[1]), t, font=f, fill=255)
            df.text((x, y - b[1]), t, font=f, fill=a)
            y += (b[3] - b[1]) + gap
        # a soft dark halo so the white text reads on any background
        halo = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(5 * s))
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        img.putalpha(halo.point(lambda v: int(v * 0.55)))
        white = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        white.putalpha(fill)
        return Image.alpha_composite(img, white)

    def show(self, genre, name, track=""):
        try:
            self._lines = (genre, name, track)
            if not self.hwnd:
                self._create()
            self._img = self._render(self._lines)
            self._xy = self._pos(*self._img.size)
            self._alpha = 255
            self._blit()
            self.rc.u32.ShowWindow(self.hwnd, 4)          # SW_SHOWNOACTIVATE
            if self._job is not None:
                self.root.after_cancel(self._job)
            self._job = self.root.after(self.SHOW_MS, self._fade)
        except Exception:
            self.log("radio caption failed:\n" + traceback.format_exc())

    def update_track(self, genre, name, track):
        """The title came after the caption appeared: redraw it if it still shows this station."""
        if self.hwnd and self._lines and self._lines[1] == name and self._job is not None:
            self.show(genre, name, track)

    def _fade(self):
        self._alpha -= max(1, int(255 * self.STEP_MS / self.FADE_MS))
        if self._alpha <= 0:
            self._job = None
            self._lines = None
            self.rc.u32.ShowWindow(self.hwnd, 0)
            return
        self._blit()
        self._job = self.root.after(self.STEP_MS, self._fade)

    def _pos(self, W, H):
        """Top centre of the work area of the monitor under the mouse."""
        p = wintypes.POINT()
        _u32.GetCursorPos(ctypes.byref(p))
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        _u32.GetMonitorInfoW(_u32.MonitorFromPoint(p, 2), ctypes.byref(mi))
        r = mi.rcWork
        return r.left + (r.right - r.left - W) // 2, r.top + int(10 * self.s)

    def _blit(self):
        from PIL import Image
        rc, img = self.rc, self._img
        W, H = img.size
        if self._dc is None or self._dc[3] != (W, H) or self._dc[5] is not img:
            a = img.split()[3]
            r, g, b, _ = img.convert("RGBa").split()
            data = Image.merge("RGBA", (b, g, r, a)).tobytes()
            if self._dc is None or self._dc[3] != (W, H):
                self._free_dc()
                bmi = rc.BITMAPINFOHEADER()
                bmi.biSize = ctypes.sizeof(rc.BITMAPINFOHEADER)
                bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = W, -H, 1, 32
                mdc = rc.g32.CreateCompatibleDC(None)
                bits = ctypes.c_void_p()
                hbm = rc.g32.CreateDIBSection(mdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
                old = rc.g32.SelectObject(mdc, hbm)
                self._dc = [mdc, hbm, old, (W, H), bits, None]
            ctypes.memmove(self._dc[4], data, len(data))
            self._dc[5] = img                   # fade steps only change the global alpha
        mdc = self._dc[0]
        blend = rc.BLENDFUNCTION(0, 0, max(0, min(255, self._alpha)), 1)
        sdc = rc.u32.GetDC(None)
        try:
            rc.u32.UpdateLayeredWindow(self.hwnd, sdc, ctypes.byref(wintypes.POINT(*self._xy)),
                                       ctypes.byref(rc.SIZE(W, H)), mdc, ctypes.byref(wintypes.POINT(0, 0)),
                                       0, ctypes.byref(blend), rc.ULW_ALPHA)
        finally:
            rc.u32.ReleaseDC(None, sdc)

    def _free_dc(self):
        if self._dc:
            mdc, hbm, old = self._dc[:3]
            self.rc.g32.SelectObject(mdc, old)
            self.rc.g32.DeleteObject(hbm)
            self.rc.g32.DeleteDC(mdc)
            self._dc = None

    def destroy(self):
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self._free_dc()
        if self.hwnd:
            self.rc.u32.DestroyWindow(self.hwnd)
            self.hwnd = None
