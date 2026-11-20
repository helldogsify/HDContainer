# -*- coding: utf-8 -*-
"""Recorder ("Диктофон") for HDContainer: records everything the computer plays plus the
microphone into one audio file, for quick recordings of work calls (Zoom, Meet, Telegram…).

Start / stop: the hotkey (Ctrl+Shift+D by default, its own voice_sys.KeyHook instance), the
tray menu, or a click on the recording indicator (a small pill left of the dictation orb,
optional). Audio goes to a WAV in %LOCALAPPDATA%\\HDContainer\\recordings while recording
(header kept valid, so a crash loses nothing); after the stop a worker thread encodes it
(MP3 / M4A through Media Foundation, or keeps the WAV) and moves it to the chosen folder,
the Desktop by default. A sidecar JSON next to the WAV says where it goes: leftovers of a
crash are finished at the next start.

Everything UI runs on the tk thread; the hook only queues events, the worker posts
callables to self._ui_q.
"""
import os
import json
import time
import queue
import shutil
import ctypes
import threading
import traceback
import subprocess
from ctypes import wintypes
import tkinter as tk
from tkinter import filedialog

import voice_sys as vs
import rec_audio as ra

try:
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

# ---------------------------------------------------------------------------
#  Strings (7 languages, as in the main app)
# ---------------------------------------------------------------------------
RSTR = {
    "tab": {"en": "Recorder", "ru": "Диктофон", "es": "Grabadora", "pt": "Gravador", "de": "Rekorder", "fr": "Dictaphone", "zh": "录音机"},
    "menu_start": {"en": "Recorder: start recording", "ru": "Диктофон: начать запись", "es": "Grabadora: empezar a grabar", "pt": "Gravador: começar a gravar", "de": "Rekorder: Aufnahme starten", "fr": "Dictaphone : démarrer", "zh": "录音机：开始录音"},
    "menu_stop": {"en": "Stop recording · %s", "ru": "Остановить запись · %s", "es": "Detener grabación · %s", "pt": "Parar gravação · %s", "de": "Aufnahme beenden · %s", "fr": "Arrêter l'enregistrement · %s", "zh": "停止录音 · %s"},
    "intro": {
        "en": "Records everything the computer plays and your microphone into one file — for work calls in Zoom, Google Meet, Telegram and the like. Start and stop with the hotkey, from the tray menu or by clicking the indicator.",
        "ru": "Записывает в один файл всё, что звучит на компьютере, и твой микрофон — для рабочих созвонов в Zoom, Google Meet, Telegram и т. п. Включается и выключается горячей клавишей, из меню в трее или кликом по индикатору.",
        "es": "Graba en un archivo todo lo que suena en el ordenador y tu micrófono: para llamadas de trabajo en Zoom, Google Meet, Telegram, etc. Se inicia y detiene con el atajo, desde el menú de la bandeja o con un clic en el indicador.",
        "pt": "Grava num arquivo tudo o que toca no computador e o seu microfone — para chamadas de trabalho no Zoom, Google Meet, Telegram etc. Inicie e pare com o atalho, pelo menu da bandeja ou clicando no indicador.",
        "de": "Nimmt alles, was der Computer abspielt, und dein Mikrofon in einer Datei auf — für Arbeitsanrufe in Zoom, Google Meet, Telegram usw. Start und Stopp per Hotkey, über das Tray-Menü oder per Klick auf die Anzeige.",
        "fr": "Enregistre dans un fichier tout ce que joue l'ordinateur et ton micro — pour les appels de travail sur Zoom, Google Meet, Telegram, etc. Démarre et arrête avec le raccourci, le menu de la zone de notification ou un clic sur l'indicateur.",
        "zh": "把电脑播放的所有声音和你的麦克风录进同一个文件——适用于 Zoom、Google Meet、Telegram 等工作通话。用快捷键、托盘菜单或点击指示器开始和停止。"},
    "enable": {"en": "Enable the recorder hotkey", "ru": "Включить горячую клавишу диктофона", "es": "Activar el atajo de la grabadora", "pt": "Ativar o atalho do gravador", "de": "Rekorder-Hotkey aktivieren", "fr": "Activer le raccourci du dictaphone", "zh": "启用录音机快捷键"},
    "sec_hotkey": {"en": "Hotkey", "ru": "Горячая клавиша", "es": "Atajo", "pt": "Atalho", "de": "Hotkey", "fr": "Raccourci", "zh": "快捷键"},
    "hk_note": {"en": "The same keys start and stop the recording.", "ru": "Те же клавиши начинают и останавливают запись.", "es": "Las mismas teclas inician y detienen la grabación.", "pt": "As mesmas teclas iniciam e param a gravação.", "de": "Dieselben Tasten starten und beenden die Aufnahme.", "fr": "Les mêmes touches démarrent et arrêtent l'enregistrement.", "zh": "同一组按键开始和停止录音。"},
    "change": {"en": "Change…", "ru": "Изменить…", "es": "Cambiar…", "pt": "Alterar…", "de": "Ändern…", "fr": "Modifier…", "zh": "更改…"},
    "press_combo": {"en": "Press the new key combination…\nEsc — cancel", "ru": "Нажми новое сочетание клавиш…\nEsc — отмена", "es": "Pulsa la nueva combinación de teclas…\nEsc — cancelar", "pt": "Pressione a nova combinação de teclas…\nEsc — cancelar", "de": "Neue Tastenkombination drücken…\nEsc — abbrechen", "fr": "Appuie sur la nouvelle combinaison…\nEsc — annuler", "zh": "按下新的快捷键组合…\nEsc — 取消"},
    "hk_bad": {"en": "The recorder hotkey must be a combination (Ctrl/Alt/Shift/Win + key) or an F-key.", "ru": "Горячая клавиша диктофона должна быть сочетанием (Ctrl/Alt/Shift/Win + клавиша) или F-клавишей.", "es": "El atajo de la grabadora debe ser una combinación (Ctrl/Alt/Shift/Win + tecla) o una tecla F.", "pt": "O atalho do gravador deve ser uma combinação (Ctrl/Alt/Shift/Win + tecla) ou uma tecla F.", "de": "Der Rekorder-Hotkey muss eine Kombination (Strg/Alt/Umschalt/Win + Taste) oder eine F-Taste sein.", "fr": "Le raccourci du dictaphone doit être une combinaison (Ctrl/Alt/Maj/Win + touche) ou une touche F.", "zh": "录音机快捷键必须是组合键（Ctrl/Alt/Shift/Win + 键）或 F 键。"},
    "hk_taken": {"en": "These keys are already used for %s.", "ru": "Эти клавиши уже заняты: %s.", "es": "Estas teclas ya se usan para: %s.", "pt": "Estas teclas já são usadas para: %s.", "de": "Diese Tasten sind schon belegt: %s.", "fr": "Ces touches sont déjà utilisées : %s.", "zh": "这些按键已被占用：%s。"},
    "sec_sources": {"en": "What to record", "ru": "Что записывать", "es": "Qué grabar", "pt": "O que gravar", "de": "Was aufnehmen", "fr": "Quoi enregistrer", "zh": "录制内容"},
    "src_system": {"en": "Computer sound — the other people, videos, everything that plays", "ru": "Звук компьютера — собеседники, видео, всё, что играет", "es": "Sonido del ordenador: los demás, vídeos, todo lo que suena", "pt": "Som do computador — os outros, vídeos, tudo o que toca", "de": "Computerton — die anderen, Videos, alles, was läuft", "fr": "Son de l'ordinateur — les autres, les vidéos, tout ce qui joue", "zh": "电脑声音——对方、视频以及所有播放的声音"},
    "src_mic": {"en": "Microphone — your voice", "ru": "Микрофон — твой голос", "es": "Micrófono: tu voz", "pt": "Microfone — a sua voz", "de": "Mikrofon — deine Stimme", "fr": "Micro — ta voix", "zh": "麦克风——你的声音"},
    "mic_default": {"en": "Default microphone", "ru": "Микрофон по умолчанию", "es": "Micrófono predeterminado", "pt": "Microfone padrão", "de": "Standardmikrofon", "fr": "Micro par défaut", "zh": "默认麦克风"},
    "mic_none": {"en": "No microphones found", "ru": "Микрофоны не найдены", "es": "No se encontraron micrófonos", "pt": "Nenhum microfone encontrado", "de": "Keine Mikrofone gefunden", "fr": "Aucun micro trouvé", "zh": "未找到麦克风"},
    "phones_note": {"en": "With headphones the recording is cleaner: without them the microphone also hears the speakers.", "ru": "В наушниках запись чище: без них микрофон слышит ещё и колонки.", "es": "Con auriculares la grabación es más limpia: sin ellos el micrófono también oye los altavoces.", "pt": "Com fones a gravação fica mais limpa: sem eles o microfone também ouve os alto-falantes.", "de": "Mit Kopfhörern klingt die Aufnahme sauberer: ohne hört das Mikrofon auch die Lautsprecher.", "fr": "Avec un casque l'enregistrement est plus propre : sans lui, le micro entend aussi les haut-parleurs.", "zh": "戴耳机录音更干净：不戴时麦克风也会录到扬声器的声音。"},
    "sec_indicator": {"en": "Indicator", "ru": "Индикатор", "es": "Indicador", "pt": "Indicador", "de": "Anzeige", "fr": "Indicateur", "zh": "指示器"},
    "show_ind": {"en": "Show the recording indicator above the clock", "ru": "Показывать индикатор записи над часами", "es": "Mostrar el indicador de grabación sobre el reloj", "pt": "Mostrar o indicador de gravação acima do relógio", "de": "Aufnahmeanzeige über der Uhr zeigen", "fr": "Afficher l'indicateur au-dessus de l'horloge", "zh": "在时钟上方显示录音指示器"},
    "ind_note": {"en": "It shows the time; a click stops the recording.", "ru": "На нём идёт время; клик останавливает запись.", "es": "Muestra el tiempo; un clic detiene la grabación.", "pt": "Mostra o tempo; um clique para a gravação.", "de": "Sie zeigt die Zeit; ein Klick beendet die Aufnahme.", "fr": "Il affiche la durée ; un clic arrête l'enregistrement.", "zh": "显示录音时长；点击即停止录音。"},
    "sec_save": {"en": "Save to", "ru": "Куда сохранять", "es": "Guardar en", "pt": "Salvar em", "de": "Speichern in", "fr": "Enregistrer dans", "zh": "保存到"},
    "desktop": {"en": "Desktop", "ru": "Рабочий стол", "es": "Escritorio", "pt": "Área de trabalho", "de": "Desktop", "fr": "Bureau", "zh": "桌面"},
    "to_desktop": {"en": "To the Desktop", "ru": "На рабочий стол", "es": "Al escritorio", "pt": "Para a área de trabalho", "de": "Auf den Desktop", "fr": "Sur le bureau", "zh": "改回桌面"},
    "open_folder": {"en": "Open", "ru": "Открыть", "es": "Abrir", "pt": "Abrir", "de": "Öffnen", "fr": "Ouvrir", "zh": "打开"},
    "sec_format": {"en": "Format", "ru": "Формат", "es": "Formato", "pt": "Formato", "de": "Format", "fr": "Format", "zh": "格式"},
    "fmt_note": {"en": "MP3 and M4A: about 0.7 MB per minute. WAV: uncompressed, about 6 MB per minute.", "ru": "MP3 и M4A — около 0,7 МБ за минуту. WAV — без сжатия, около 6 МБ за минуту.", "es": "MP3 y M4A: unos 0,7 MB por minuto. WAV: sin comprimir, unos 6 MB por minuto.", "pt": "MP3 e M4A: cerca de 0,7 MB por minuto. WAV: sem compressão, cerca de 6 MB por minuto.", "de": "MP3 und M4A: etwa 0,7 MB pro Minute. WAV: unkomprimiert, etwa 6 MB pro Minute.", "fr": "MP3 et M4A : environ 0,7 Mo par minute. WAV : non compressé, environ 6 Mo par minute.", "zh": "MP3 和 M4A：每分钟约 0.7 MB。WAV：不压缩，每分钟约 6 MB。"},
    "file": {"en": "Recording", "ru": "Запись", "es": "Grabación", "pt": "Gravação", "de": "Aufnahme", "fr": "Enregistrement", "zh": "录音"},
    "saving": {"en": "Saving", "ru": "Сохраняю", "es": "Guardando", "pt": "Salvando", "de": "Speichere", "fr": "Enregistrement", "zh": "正在保存"},
    "saved": {"en": "Recording saved", "ru": "Запись сохранена", "es": "Grabación guardada", "pt": "Gravação salva", "de": "Aufnahme gespeichert", "fr": "Enregistrement sauvegardé", "zh": "录音已保存"},
    "recovered": {"en": "Recording recovered after a crash", "ru": "Восстановлена запись после сбоя", "es": "Grabación recuperada tras un fallo", "pt": "Gravação recuperada após uma falha", "de": "Aufnahme nach Absturz wiederhergestellt", "fr": "Enregistrement récupéré après un plantage", "zh": "已恢复崩溃前的录音"},
    "err_start": {"en": "Could not start recording", "ru": "Не удалось начать запись", "es": "No se pudo empezar a grabar", "pt": "Não foi possível começar a gravar", "de": "Aufnahme konnte nicht starten", "fr": "Impossible de démarrer l'enregistrement", "zh": "无法开始录音"},
    "err_nodev": {"en": "No sound device answered", "ru": "Не ответило ни одно звуковое устройство", "es": "Ningún dispositivo de sonido respondió", "pt": "Nenhum dispositivo de som respondeu", "de": "Kein Audiogerät hat geantwortet", "fr": "Aucun périphérique audio n'a répondu", "zh": "没有任何音频设备响应"},
    "err_nothing": {"en": "Turn on the computer sound or the microphone in Settings → Recorder", "ru": "Включи звук компьютера или микрофон в Настройки → Диктофон", "es": "Activa el sonido del ordenador o el micrófono en Ajustes → Grabadora", "pt": "Ative o som do computador ou o microfone em Configurações → Gravador", "de": "Schalte Computerton oder Mikrofon unter Einstellungen → Rekorder ein", "fr": "Active le son de l'ordinateur ou le micro dans Paramètres → Dictaphone", "zh": "请在 设置 → 录音机 中开启电脑声音或麦克风"},
    "err_encode": {"en": "Saved as WAV: compression failed", "ru": "Сохранено как WAV: сжать не удалось", "es": "Guardado como WAV: no se pudo comprimir", "pt": "Salvo como WAV: a compressão falhou", "de": "Als WAV gespeichert: Komprimierung fehlgeschlagen", "fr": "Enregistré en WAV : la compression a échoué", "zh": "已保存为 WAV：压缩失败"},
    "err_save": {"en": "Could not save the recording", "ru": "Не удалось сохранить запись", "es": "No se pudo guardar la grabación", "pt": "Não foi possível salvar a gravação", "de": "Aufnahme konnte nicht gespeichert werden", "fr": "Impossible de sauvegarder l'enregistrement", "zh": "无法保存录音"},
    "err_mic": {"en": "Microphone unavailable — recording the computer sound only", "ru": "Микрофон недоступен — пишу только звук компьютера", "es": "Micrófono no disponible: solo se graba el sonido del ordenador", "pt": "Microfone indisponível — gravando só o som do computador", "de": "Mikrofon nicht verfügbar — nur Computerton wird aufgenommen", "fr": "Micro indisponible — seul le son de l'ordinateur est enregistré", "zh": "麦克风不可用——只录制电脑声音"},
}

LANG_GET = lambda: "en"


def R(key, *args):
    s = RSTR.get(key, {})
    txt = s.get(LANG_GET()) or s.get("en") or key
    return (txt % args) if args else txt


DEFAULTS = {
    "enabled": True,
    "hk_mods": vs.MOD_CONTROL | vs.MOD_SHIFT, "hk_vk": 0x44,     # Ctrl+Shift+D ("Dictaphone")
    "indicator": True,
    "system": True, "mic": True,
    "mic_id": "",                   # WASAPI endpoint id; "" = the default microphone
    "folder": "",                   # "" = the Desktop
    "format": "mp3",                # mp3 / m4a / wav
}
FORMAT_CHOICES = ("mp3", "m4a", "wav")
PLAIN_HOTKEYS_OK = frozenset(range(0x70, 0x88)) | {0x13, 0x91}   # F1-F24, Pause, ScrollLock
START_CHECK_S = 2.5                 # after this many seconds a session with no live source is an error


def fmt_time(sec):
    sec = int(sec)
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def _theme():
    main = __import__("__main__")
    C = lambda n, d: getattr(main, n, d)
    return {"bg": C("COL_BG", "#1b1b1d"), "sf": C("COL_SURFACE", "#232427"), "sf2": C("COL_SURFACE2", "#2d2e31"),
            "bd": C("COL_BORDER", "#2a2b2c"), "hv": C("COL_HOVER", "#303236"), "ac": C("COL_ACCENT", "#4c8bf5"),
            "tx": C("COL_TEXT", "#e6e8ea"), "dim": C("COL_TEXT_DIM", "#9aa0a6"), "err": C("COL_DANGER", "#ff453a"),
            "font": C("FONT", ("Segoe UI", 10)), "font_sm": C("FONT_SM", ("Segoe UI", 9))}


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]


def desktop_dir():
    """The real Desktop folder (OneDrive-redirected or renamed ones too)."""
    try:
        g = _GUID()
        ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"), ctypes.byref(g))
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
            try:
                if p.value and os.path.isdir(p.value):
                    return p.value
            finally:
                ctypes.windll.ole32.CoTaskMemFree(p)
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


# ---------------------------------------------------------------------------
#  Recording indicator: a small layered pill left of the dictation orb
# ---------------------------------------------------------------------------
u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32")
k32 = ctypes.WinDLL("kernel32")
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


def _d(fn, res, args):
    fn.restype, fn.argtypes = res, args


_d(u32.RegisterClassExW, wintypes.ATOM, [ctypes.POINTER(WNDCLASSEXW)])
_d(u32.CreateWindowExW, wintypes.HWND,
   [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p])
_d(u32.DefWindowProcW, LRESULT, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM])
_d(u32.DestroyWindow, wintypes.BOOL, [wintypes.HWND])
_d(u32.ShowWindow, wintypes.BOOL, [wintypes.HWND, ctypes.c_int])
_d(u32.SetWindowPos, wintypes.BOOL, [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT])
_d(u32.GetDC, wintypes.HDC, [wintypes.HWND])
_d(u32.ReleaseDC, ctypes.c_int, [wintypes.HWND, wintypes.HDC])
_d(u32.UpdateLayeredWindow, wintypes.BOOL,
   [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(SIZE), wintypes.HDC,
    ctypes.POINTER(wintypes.POINT), wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD])
_d(u32.SystemParametersInfoW, wintypes.BOOL, [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT])
_d(u32.LoadCursorW, wintypes.HANDLE, [wintypes.HINSTANCE, ctypes.c_void_p])
_d(u32.GetCursorPos, wintypes.BOOL, [ctypes.POINTER(wintypes.POINT)])
_d(k32.GetModuleHandleW, wintypes.HINSTANCE, [wintypes.LPCWSTR])
_d(g32.CreateCompatibleDC, wintypes.HDC, [wintypes.HDC])
_d(g32.DeleteDC, wintypes.BOOL, [wintypes.HDC])
_d(g32.CreateDIBSection, wintypes.HBITMAP, [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD])
_d(g32.SelectObject, wintypes.HGDIOBJ, [wintypes.HDC, wintypes.HGDIOBJ])
_d(g32.DeleteObject, wintypes.BOOL, [wintypes.HGDIOBJ])

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000, 0x8, 0x80, 0x08000000
HWND_TOPMOST = -1
SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x10, 0x40
SW_HIDE = 0
WM_LBUTTONUP, WM_MOUSEACTIVATE, MA_NOACTIVATE = 0x0202, 0x0021, 3
ULW_ALPHA = 2
SPI_GETWORKAREA = 0x30
REC_RED = (255, 69, 58)
PILL_BG = (9, 12, 22, 232)


def _dpi_scale():
    try:
        return u32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def _font(names, size):
    for n in names:
        try:
            return ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", n), size)
        except Exception:
            continue
    return ImageFont.load_default()


class Indicator:
    W0, H0 = 230, 44                # logical window size; the pill is drawn right-aligned inside
    GAP_RIGHT = 118                 # logical px from the work-area edge: leaves room for the dictation orb
    SS = 3                          # supersampling

    def __init__(self, root, on_click, log):
        self.root, self.on_click, self.log = root, on_click, log
        self.hwnd = None
        self.state = None           # "rec" / "saving" / None
        self.start = 0.0
        self.progress = 0.0
        self.levels = lambda: (0.0, 0.0)
        self._clicked = False
        self._ticking = False
        self._dc = None
        self._bars = [0.0] * 4
        self.s = _dpi_scale()
        self.W, self.H = int(self.W0 * self.s), int(self.H0 * self.s)
        self.f_time = _font(["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"], int(13 * self.s * self.SS))
        self._proc = WNDPROC(self._wndproc)

    def _create(self):
        hinst = k32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hCursor = u32.LoadCursorW(None, ctypes.c_void_p(32649))      # IDC_HAND
        wc.lpszClassName = "HDContainerRecIndicator"
        u32.RegisterClassExW(ctypes.byref(wc))
        ex = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        self.hwnd = u32.CreateWindowExW(ex, "HDContainerRecIndicator", "HDContainer recorder",
                                        WS_POPUP, 0, 0, self.W, self.H, None, None, hinst, None)

    def _wndproc(self, hwnd, msg, wp, lp):
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_LBUTTONUP:
            self._clicked = True            # never touch tk from a native callback
            return 0
        return u32.DefWindowProcW(hwnd, msg, wp, lp)

    def _pos(self):
        rc = wintypes.RECT()
        u32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rc), 0)
        cy = rc.bottom - int(68 * self.s)            # same line as the dictation orb
        return rc.right - int(self.GAP_RIGHT * self.s) - self.W, cy - self.H // 2

    def show_rec(self, start, levels):
        self.state, self.start, self.levels = "rec", start, levels
        self._show()

    def show_saving(self, p=0.0):
        self.state, self.progress = "saving", p
        self._show()

    def hide(self):
        self.state = None
        if self.hwnd:
            u32.ShowWindow(self.hwnd, SW_HIDE)

    def destroy(self):
        self.hide()
        self._free_dc()
        if self.hwnd:
            u32.DestroyWindow(self.hwnd)
            self.hwnd = None

    def _show(self):
        if not HAVE_PIL:
            return
        try:
            if not self.hwnd:
                self._create()
            x, y = self._pos()
            self._blit(self._render())
            u32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, self.W, self.H, SWP_NOACTIVATE | SWP_SHOWWINDOW)
            if not self._ticking:
                self._ticking = True
                self.root.after(100, self._tick)
        except Exception:
            self.log("recorder indicator show failed:\n" + traceback.format_exc())

    def _hover(self):
        if not self.hwnd:
            return False
        p = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(p))
        x, y = self._pos()
        return x + self.W - self._pill_w <= p.x < x + self.W and y <= p.y < y + self.H

    def _tick(self):
        if self.state is None or not self.hwnd:
            self._ticking = False
            return
        if self._clicked:
            self._clicked = False
            if self.state == "rec":
                try:
                    self.on_click()
                except Exception:
                    self.log("recorder indicator click failed:\n" + traceback.format_exc())
        try:
            if self.state is not None:
                self._blit(self._render())
        except Exception:
            self.log("recorder indicator render failed:\n" + traceback.format_exc())
        self.root.after(100, self._tick)

    _pill_w = 0

    def _render(self):
        k, s = self.SS, self.s
        W, H = self.W * k, self.H * k
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        u = s * k
        hover = self.state == "rec" and self._hover()
        if self.state == "rec":
            text = fmt_time(time.time() - self.start)
        else:
            text = "%s %d%%" % (R("saving"), int(self.progress * 100))
        tw = d.textlength(text, font=self.f_time)
        pw = int(tw + (78 if self.state == "rec" else 46) * u)
        ph = int(32 * u)
        x1 = W - int(2 * u)
        x0 = x1 - pw
        y0 = (H - ph) // 2
        self._pill_w = pw // k + 4
        d.rounded_rectangle((x0, y0, x1, y0 + ph), radius=ph // 2, fill=PILL_BG,
                            outline=REC_RED + ((200 if hover else 110),), width=max(1, int(1.2 * u)))
        cx, cy = x0 + int(17 * u), y0 + ph // 2
        t = time.time()
        if self.state == "rec":
            if hover:                                              # stop square
                r = 5.5 * u
                d.rounded_rectangle((cx - r, cy - r, cx + r, cy + r), radius=1.8 * u, fill=REC_RED + (255,))
            else:                                                  # pulsing red dot
                import math
                p = 0.5 + 0.5 * math.sin(t * 3.2)
                rg = (8 + 3 * p) * u
                d.ellipse((cx - rg, cy - rg, cx + rg, cy + rg), fill=REC_RED + (int(50 + 40 * p),))
                r = 5.5 * u
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=REC_RED + (255,))
            tx = cx + int(13 * u)
            d.text((tx, cy), text, font=self.f_time, fill=(236, 240, 247, 255), anchor="lm")
            # level bars: computer sound (2 left) and microphone (2 right)
            out, mic = self.levels()
            tgt = [out, out * 0.7, mic * 0.7, mic]
            for i in range(4):
                self._bars[i] += (tgt[i] - self._bars[i]) * 0.5
            bx = tx + tw + int(10 * u)
            bw, gap = 3 * u, 2.4 * u
            for i, v in enumerate(self._bars):
                hh = (3 + 14 * min(1.0, v)) * u
                x = bx + i * (bw + gap) + (2 * u if i >= 2 else 0)
                col = (236, 240, 247, 230) if i < 2 else REC_RED + (230,)
                d.rounded_rectangle((x, cy - hh / 2, x + bw, cy + hh / 2), radius=bw / 2, fill=col)
        else:
            r = 7 * u
            a = (t * 300) % 360
            d.arc((cx - r, cy - r, cx + r, cy + r), a, a + 250, fill=(236, 240, 247, 255), width=max(2, int(2.2 * u)))
            d.text((cx + int(13 * u), cy), text, font=self.f_time, fill=(236, 240, 247, 255), anchor="lm")
        return img.resize((self.W, self.H), Image.LANCZOS)

    def _blit(self, img):
        W, H = img.size
        a = img.split()[3]
        r, g, b, _ = img.convert("RGBa").split()
        data = Image.merge("RGBA", (b, g, r, a)).tobytes()
        if self._dc is None or self._dc[3] != (W, H):
            self._free_dc()
            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = W, -H, 1, 32
            mdc = g32.CreateCompatibleDC(None)
            bits = ctypes.c_void_p()
            hbm = g32.CreateDIBSection(mdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            old = g32.SelectObject(mdc, hbm)
            self._dc = (mdc, hbm, old, (W, H), bits)
        mdc, _hbm, _old, _sz, bits = self._dc
        ctypes.memmove(bits, data, len(data))
        x, y = self._pos()
        blend = BLENDFUNCTION(0, 0, 255, 1)
        sdc = u32.GetDC(None)
        try:
            u32.UpdateLayeredWindow(self.hwnd, sdc, ctypes.byref(wintypes.POINT(x, y)), ctypes.byref(SIZE(W, H)),
                                    mdc, ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
        finally:
            u32.ReleaseDC(None, sdc)

    def _free_dc(self):
        if self._dc:
            mdc, hbm, old, _sz, _bits = self._dc
            g32.SelectObject(mdc, old)
            g32.DeleteObject(hbm)
            g32.DeleteDC(mdc)
            self._dc = None


# ---------------------------------------------------------------------------
#  Controller: hotkey, sessions, saving, settings tab, tray entry
# ---------------------------------------------------------------------------
class RecorderController:
    def __init__(self, app, get_lang, log, local_dir):
        global LANG_GET
        LANG_GET = get_lang
        self.app, self.root, self.log = app, app.root, log
        self.dir = os.path.join(local_dir, "recordings")
        os.makedirs(self.dir, exist_ok=True)
        self.draft = None
        self._tab = None
        self._capture_dlg = None
        self.session = None
        self.meta = None                # sidecar of the running session
        self._jobs_left = 0             # recordings waiting for / in encoding
        self._job_q = queue.Queue()
        self._ui_q = queue.Queue()
        self.ind = Indicator(self.root, self.toggle, log)
        threading.Thread(target=self._worker, daemon=True, name="hdc-rec-save").start()
        self.hook = vs.KeyHook(log)
        self.hook.start()
        self.register_hotkey()
        self.root.after(25, self._loop)
        self._job_q.put(("recover",))

    # ---- settings (draft while the Settings window is open, see voice.py) ----
    def get(self, k):
        src = self.draft if self.draft is not None else self.app.settings.get("recorder", {})
        return src.get(k, DEFAULTS[k])

    def saved(self, k):
        return self.app.settings.get("recorder", {}).get(k, DEFAULTS[k])

    def set(self, k, v):
        if self.draft is not None:
            self.draft[k] = v
            return
        d = dict(self.app.settings.get("recorder", {}))
        d[k] = v
        self.app._set_setting("recorder", d)

    def begin_edit(self):
        self.draft = dict(self.app.settings.get("recorder", {}))

    def discard(self):
        self.draft = None
        self._tab = None
        self.hook.capture = False

    def commit(self):
        new, self.draft = self.draft, None
        self._tab = None
        self.hook.capture = False
        if new is None:
            return
        self.app._set_setting("recorder", new)
        self.register_hotkey()
        self.log("recorder settings saved")

    # ---- hotkey ----
    def register_hotkey(self):
        self.hook.configure(self.saved("hk_mods"), self.saved("hk_vk"), self.saved("enabled"))
        self.hook.mask_taps = False
        self.log("recorder hotkey %s hook=%s" % (self.hotkey_text(saved=True), "ok" if self.hook._hook else "FAILED"))

    def hotkey_text(self, saved=False):
        g = self.saved if saved else self.get
        return vs.hotkey_label(g("hk_mods"), g("hk_vk"))

    def _loop(self):
        try:
            while True:
                try:
                    ev = self.hook.events.get_nowait()
                except queue.Empty:
                    break
                if ev[0] == "press":
                    self.toggle()
                elif ev[0] in ("captured", "capture_cancel"):
                    self._capture_done(ev)
            while True:
                try:
                    fn = self._ui_q.get_nowait()
                except queue.Empty:
                    break
                fn()
        except Exception:
            self.log("recorder loop:\n" + traceback.format_exc())
        self.root.after(25, self._loop)

    # ---- recording ----
    @property
    def recording(self):
        return self.session is not None

    def toggle(self):
        if self.session is not None:
            self.stop()
        else:
            self.start()

    def _dest_dir(self, folder):
        for d in (folder, desktop_dir(), self.dir):
            if d and os.path.isdir(d):
                return d
        return self.dir

    def start(self):
        if self.session is not None:
            return
        system, mic = self.saved("system"), self.saved("mic")
        if not (system or mic):
            self.app._notify(R("err_start"), R("err_nothing"), kind="error")
            return
        stamp = time.strftime("%Y-%m-%d %H-%M-%S")
        stem = "rec-" + stamp.replace(" ", "_")
        wav = os.path.join(self.dir, stem + ".wav")
        meta = {"wav": wav, "folder": self.saved("folder"), "format": self.saved("format"),
                "name": "%s %s" % (R("file"), time.strftime("%Y-%m-%d %H-%M")), "started": time.time()}
        try:
            with open(os.path.join(self.dir, stem + ".json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
            self.session = ra.Session(wav, self.log, system=system, mic=mic, mic_id=self.saved("mic_id"))
        except Exception:
            self.log("recorder start failed:\n" + traceback.format_exc())
            self.session = None
            self.app._notify(R("err_start"), R("err_nodev"), kind="error")
            self._job_q.put(("drop", meta))
            return
        self.meta = meta
        self.log("recorder: started -> %s" % wav)
        if self.saved("indicator"):
            self.ind.show_rec(time.time(), self.session.levels)
        self.root.after(int(START_CHECK_S * 1000), lambda s=self.session: self._start_check(s))

    def _start_check(self, sess):
        if sess is not self.session:
            return
        srcs = list(sess.out_srcs.values())
        mic = sess.mic_src
        alive = [s for s in srcs if s.alive] + ([mic] if mic is not None and mic.alive else [])
        if not alive:
            self.log("recorder: no source is alive, stopping")
            meta, self.session, self.meta = self.meta, None, None
            sess.stop(wait=False)
            self.ind.hide()
            self._job_q.put(("drop", meta, sess))
            self.app._notify(R("err_start"), R("err_nodev"), kind="error")
        elif sess.mic and (mic is None or not mic.alive):
            self.app._notify(R("tab"), R("err_mic"), kind="error")

    def stop(self):
        sess, meta = self.session, self.meta
        if sess is None:
            return
        self.session = self.meta = None
        sess.stop(wait=False)
        self.log("recorder: stopped after %s" % fmt_time(time.time() - meta["started"]))
        self._jobs_left += 1
        if self.saved("indicator"):
            self.ind.show_saving(0.0)
        else:
            self.ind.hide()
        self._job_q.put(("save", meta, sess))

    def menu_text(self):
        if self.session is not None:
            return R("menu_stop", fmt_time(time.time() - self.meta["started"]))
        return R("menu_start")

    # ---- saving (worker thread) ----
    def _post(self, fn):
        self._ui_q.put(fn)

    def _worker(self):
        while True:
            job = self._job_q.get()
            try:
                if job[0] == "save":
                    job[2].thread.join()
                    self._save(job[1], recovered=False)
                elif job[0] == "drop":
                    if len(job) > 2:
                        job[2].thread.join()
                    self._remove_tmp(job[1])
                elif job[0] == "recover":
                    self._recover()
            except Exception:
                self.log("recorder worker:\n" + traceback.format_exc())

    def _remove_tmp(self, meta):
        stem = os.path.splitext(meta["wav"])[0]
        for p in (meta["wav"], stem + ".json", stem + ".mp3", stem + ".m4a"):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    def _unique(self, folder, name, ext):
        p = os.path.join(folder, name + ext)
        i = 2
        while os.path.exists(p):
            p = os.path.join(folder, "%s (%d)%s" % (name, i, ext))
            i += 1
        return p

    def _save(self, meta, recovered):
        wav = meta["wav"]
        fmt = meta.get("format", "mp3")
        stem = os.path.splitext(wav)[0]
        dest_dir = self._dest_dir(meta.get("folder", ""))
        warn = None
        try:
            if fmt in ra.FORMATS:
                tmp = stem + "." + fmt
                try:
                    last = [0.0]

                    def prog(p):
                        if p - last[0] >= 0.02:
                            last[0] = p
                            self._post(lambda p=p: self._progress(p))
                    ra.encode(wav, tmp, fmt, progress=prog)
                    src, ext = tmp, "." + fmt
                except Exception:
                    self.log("recorder encode failed:\n" + traceback.format_exc())
                    if os.path.exists(tmp):
                        os.remove(tmp)
                    src, ext, warn = wav, ".wav", R("err_encode")
            else:
                src, ext = wav, ".wav"
            out = self._unique(dest_dir, meta.get("name") or os.path.basename(stem), ext)
            shutil.move(src, out)
        except Exception:
            self.log("recorder save failed:\n" + traceback.format_exc())
            self._post(lambda: self._saved(None, R("err_save"), recovered))
            return
        self._remove_tmp(meta)
        self.log("recorder: saved %s" % out)
        self._post(lambda: self._saved(out, warn, recovered))

    def _recover(self):
        busy = self.meta["wav"] if self.meta else None
        for n in sorted(os.listdir(self.dir)):
            if not n.endswith(".json"):
                continue
            side = os.path.join(self.dir, n)
            try:
                with open(side, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                continue
            wav = meta.get("wav") or os.path.splitext(side)[0] + ".wav"
            meta["wav"] = wav
            if wav == busy:
                continue
            if not os.path.exists(wav) or os.path.getsize(wav) <= 44 + ra.RATE * ra.FRAME // 2:
                self._remove_tmp(meta)              # nothing worth keeping (< 0.5 s)
                continue
            ra.repair_wav(wav)
            self.log("recorder: recovering %s" % wav)
            self._jobs_left += 1
            self._save(meta, recovered=True)

    # ---- results (tk thread) ----
    def _progress(self, p):
        if self.ind.state == "saving":
            self.ind.progress = p

    def _saved(self, path, warn, recovered):
        self._jobs_left = max(0, self._jobs_left - 1)
        if self.ind.state == "saving" and self._jobs_left == 0:
            self.ind.hide()
        if path is None:
            self.app._notify(R("tab"), warn or R("err_save"), kind="error")
            return
        title = R("recovered") if recovered else R("saved")
        self.app._notify(title, (warn + " · " if warn else "") + os.path.basename(path),
                         on_click=lambda p=path: self._reveal(p), kind="error" if warn else "done")

    def _reveal(self, path):
        try:
            subprocess.Popen(["explorer.exe", "/select,", path], close_fds=True)
        except Exception:
            self.log("recorder reveal failed:\n" + traceback.format_exc())

    def shutdown(self):
        """App quit: stop the hook; a running recording is finished at the next start (recovery)."""
        try:
            self.hook.stop()
        except Exception:
            pass
        sess, self.session = self.session, None
        if sess is not None:
            sess.stop(wait=True)
        self.ind.destroy()

    # ---- settings tab ----
    def build_into(self, parent, win, pad):
        th = _theme()
        self._tab = (parent, win, pad)
        if self.draft is None:
            self.begin_edit()
        a = self.app

        def header(text, top=20):
            tk.Label(parent, text=text, bg=th["sf"], fg=th["tx"], font=("Segoe UI Semibold", 11)).pack(
                anchor="w", padx=pad, pady=(top, 6))

        def note(text, pady=(0, 4), indent=0):
            tk.Label(parent, text=text, bg=th["sf"], fg=th["dim"], font=th["font_sm"], justify="left",
                     wraplength=560 - indent, anchor="w").pack(anchor="w", fill="x", padx=(pad + indent, pad), pady=pady)

        def check(text, key, on_change=None):
            def setter(v):
                self.set(key, bool(v))
                if on_change:
                    on_change(v)
            a._setting_check(parent, text, bool(self.get(key)), setter, pad)

        def pills(options, key):
            row = tk.Frame(parent, bg=th["sf"])
            row.pack(fill="x", padx=pad, pady=(2, 4))
            cur = self.get(key)
            for val, label in options:
                on = val == cur
                p = tk.Label(row, text=label, bg=th["ac"] if on else th["sf2"], fg="white" if on else th["tx"],
                             font=th["font_sm"], padx=12, pady=4, cursor="hand2")
                p.pack(side="left", padx=(0, 6))
                p.bind("<Button-1>", lambda _e, v=val: (self.set(key, v), self._rebuild()))

        note(R("intro"), pady=(8, 6))

        header(R("sec_hotkey"), top=12)
        a._hotkey_link(parent, self.hotkey_text() if self.get("enabled") else None, pad)
        note(R("hk_note"), pady=(6, 2))

        header(R("sec_sources"))
        check(R("src_system"), "system")
        check(R("src_mic"), "mic", lambda v: self._rebuild())
        if self.get("mic"):
            row = tk.Frame(parent, bg=th["sf"])
            row.pack(fill="x", padx=pad + 34, pady=(0, 4))
            btn = a._ghost_btn(row, self._mic_label() + "   ⌄", lambda: self._mic_menu(btn))
            btn.pack(side="left")
        note(R("phones_note"), pady=(4, 2))

        header(R("sec_indicator"))
        check(R("show_ind"), "indicator")
        note(R("ind_note"), pady=(0, 2), indent=34)

        header(R("sec_save"))
        folder = self.get("folder")
        shown = folder if folder and os.path.isdir(folder) else R("desktop")
        row = tk.Frame(parent, bg=th["sf"])
        row.pack(fill="x", padx=pad, pady=(2, 2))
        a._ghost_btn(row, R("change"), self._pick_folder).pack(side="right")
        a._ghost_btn(row, R("open_folder"), lambda: self._open_dir(self._dest_dir(self.get("folder")))).pack(
            side="right", padx=(0, 8))
        tk.Label(row, text=shown, bg=th["bg"], fg=th["tx"], font=th["font"], padx=10, pady=6,
                 anchor="w", justify="left", wraplength=330).pack(side="left", fill="x", expand=True, padx=(0, 8))
        if folder:
            back = tk.Label(parent, text=R("to_desktop"), bg=th["sf"], fg=th["ac"], font=th["font_sm"],
                            cursor="hand2")
            back.pack(anchor="w", padx=pad, pady=(6, 0))
            back.bind("<Button-1>", lambda _e: (self.set("folder", ""), self._rebuild()))

        header(R("sec_format"))
        pills([(f, f.upper()) for f in FORMAT_CHOICES], "format")
        note(R("fmt_note"), pady=(2, 2))
        tk.Frame(parent, bg=th["sf"], height=16).pack()

    def _mics(self):
        try:
            return ra.list_devices(ra.E_CAPTURE)
        except Exception:
            self.log("recorder mic list:\n" + traceback.format_exc())
            return []

    def _mic_label(self):
        mid = self.get("mic_id")
        if not mid:
            return R("mic_default")
        for i, n in self._mics():
            if i == mid:
                return n
        return R("mic_default")

    def _mic_menu(self, btn):
        m = tk.Menu(btn, tearoff=0)
        m.add_command(label=R("mic_default"), command=lambda: (self.set("mic_id", ""), self._rebuild()))
        mics = self._mics()
        if mics:
            m.add_separator()
        for i, n in mics:
            m.add_command(label=n, command=lambda i=i: (self.set("mic_id", i), self._rebuild()))
        if not mics:
            m.add_command(label=R("mic_none"), state="disabled")
        try:
            m.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())
        finally:
            m.grab_release()

    def _pick_folder(self):
        tab = self._tab
        p = filedialog.askdirectory(parent=tab[1] if tab else None, initialdir=self._dest_dir(self.get("folder")),
                                    mustexist=True)
        if p:
            self.set("folder", os.path.normpath(p))
            self._rebuild()

    def _open_dir(self, d):
        try:
            os.startfile(d)
        except Exception:
            self.log("recorder open folder:\n" + traceback.format_exc())

    def _rebuild(self):
        tab = self._tab
        if not tab:
            return
        parent, win, pad = tab
        try:
            if not parent.winfo_exists():
                return
            for w in parent.winfo_children():
                w.destroy()
            self.build_into(parent, win, pad)
        except Exception:
            self.log("recorder tab rebuild:\n" + traceback.format_exc())

    def _capture_hotkey(self):
        th = _theme()
        dlg = self.app._dialog(R("sec_hotkey"), 420, 170)
        tk.Label(dlg, text=R("press_combo"), bg=th["sf"], fg=th["tx"], font=("Segoe UI", 11),
                 justify="center").pack(expand=True)
        self._capture_dlg = dlg
        self.hook.capture = True

        def closed():
            self.hook.capture = False
            self._capture_dlg = None
            try:
                dlg.destroy()
            except Exception:
                pass
        dlg.protocol("WM_DELETE_WINDOW", closed)
        dlg.focus_force()

    def _taken_by(self, mods, vk):
        """Name of another feature that already uses this hotkey, or None."""
        sh = getattr(self.app, "screenshot", None)
        if sh is not None and sh.get("enabled") and (sh.get("hk_mods"), sh.get("hk_vk")) == (mods, vk):
            return __import__("screenshot").SS("tab")
        vo = getattr(self.app, "voice", None)
        if vo is not None:
            try:
                if (vo.get("hk_mods"), vo.get("hk_vk")) == (mods, vk):
                    return __import__("voice").V("menu")
            except Exception:
                pass
        return None

    def _capture_done(self, ev):
        msg = None
        if ev[0] == "captured":
            mods, vk = ev[1], ev[2]
            if mods == 0 and vk not in PLAIN_HOTKEYS_OK:
                msg = R("hk_bad")
            else:
                other = self._taken_by(mods, vk)
                if other:
                    msg = R("hk_taken", other)
                else:
                    self.set("hk_mods", mods)
                    self.set("hk_vk", vk)
        dlg, self._capture_dlg = self._capture_dlg, None
        if dlg:
            try:
                dlg.destroy()
            except Exception:
                pass
        self._rebuild()
        getattr(self.app, "_hotkeys_refresh", lambda: None)()
        if msg:
            self.app._info(R("sec_hotkey"), msg)
