# -*- coding: utf-8 -*-
"""Screenshots for HDContainer.

The hotkey (Ctrl+Shift+A by default) freezes the screen: the capture is shown
darkened on a full-screen overlay and the mouse selects a region (a plain click
takes the whole monitor under the cursor). The region opens in a small editor:
pen, marker, frame, arrow, crop; "Copy to clipboard" puts the result in the
clipboard (CF_DIB + PNG) and closes the editor, "Save as…" writes a file.

Every capture is kept in %LOCALAPPDATA%\\HDContainer\\screenshots for N days
(30 by default), like the dictation history: tray → "Screenshot history" shows
them as thumbnails, each one can be copied again, edited or deleted.

Everything runs on the tk thread. The keyboard hook (its own voice_sys.KeyHook
instance) only queues events; a tk timer handles them. File writes go through
one worker thread so a capture and its later edit never interleave.
"""
import io
import os
import time
import queue
import ctypes
import threading
import traceback
from ctypes import wintypes
import tkinter as tk
from tkinter import filedialog

from PIL import Image, ImageDraw, ImageGrab, ImageTk

import voice_sys as vs

# ---------------------------------------------------------------------------
#  Strings (7 languages, as in the main app)
# ---------------------------------------------------------------------------
SSTR = {
    "tab": {"en": "Screenshots", "ru": "Скриншоты", "es": "Capturas", "pt": "Capturas de tela", "de": "Bildschirmfotos", "fr": "Captures d'écran", "zh": "截图"},
    "menu": {"en": "Take a screenshot", "ru": "Сделать скриншот", "es": "Hacer una captura", "pt": "Fazer captura de tela", "de": "Bildschirmfoto aufnehmen", "fr": "Faire une capture d'écran", "zh": "截取屏幕"},
    "hist_menu": {"en": "Screenshot history", "ru": "История скриншотов", "es": "Historial de capturas", "pt": "Histórico de capturas", "de": "Bildschirmfoto-Verlauf", "fr": "Historique des captures", "zh": "截图历史"},
    "intro": {
        "en": "Press the hotkey and the screen freezes. Drag to select a region, or just click to take the whole screen. The capture opens in a small editor: draw, mark, crop; “Copy to clipboard” closes it with the result in the clipboard.",
        "ru": "Нажми горячую клавишу — экран застывает. Выдели область мышкой или просто кликни, чтобы снять весь экран. Снимок откроется в маленьком редакторе: рисуй, выделяй, обрезай; «Скопировать в буфер» закроет его, а результат окажется в буфере обмена.",
        "es": "Pulsa el atajo y la pantalla se congela. Arrastra para elegir una zona o haz clic para capturar toda la pantalla. La captura se abre en un pequeño editor: dibuja, marca, recorta; «Copiar al portapapeles» lo cierra con el resultado en el portapapeles.",
        "pt": "Pressione o atalho e a tela congela. Arraste para escolher uma área ou apenas clique para capturar a tela inteira. A captura abre em um pequeno editor: desenhe, marque, recorte; “Copiar para a área de transferência” fecha-o com o resultado na área de transferência.",
        "de": "Drücke den Hotkey und der Bildschirm friert ein. Ziehe einen Bereich auf oder klicke einfach für den ganzen Bildschirm. Die Aufnahme öffnet sich in einem kleinen Editor: zeichnen, markieren, zuschneiden; „In die Zwischenablage“ schließt ihn mit dem Ergebnis in der Zwischenablage.",
        "fr": "Appuie sur le raccourci : l'écran se fige. Fais glisser pour choisir une zone, ou clique simplement pour tout l'écran. La capture s'ouvre dans un petit éditeur : dessiner, surligner, recadrer ; « Copier dans le presse-papiers » le ferme avec le résultat dans le presse-papiers.",
        "zh": "按下快捷键，屏幕冻结。拖动鼠标选择区域，或直接点击截取整个屏幕。截图会在小编辑器中打开：绘制、标记、裁剪；“复制到剪贴板”会关闭编辑器并把结果放入剪贴板。"},
    "enable": {"en": "Enable screenshots", "ru": "Включить скриншоты", "es": "Activar capturas", "pt": "Ativar capturas", "de": "Bildschirmfotos aktivieren", "fr": "Activer les captures", "zh": "启用截图"},
    "sec_hotkey": {"en": "Hotkey", "ru": "Горячая клавиша", "es": "Atajo", "pt": "Atalho", "de": "Hotkey", "fr": "Raccourci", "zh": "快捷键"},
    "change": {"en": "Change…", "ru": "Изменить…", "es": "Cambiar…", "pt": "Alterar…", "de": "Ändern…", "fr": "Modifier…", "zh": "更改…"},
    "press_combo": {"en": "Press the new key combination…\nEsc — cancel", "ru": "Нажми новое сочетание клавиш…\nEsc — отмена", "es": "Pulsa la nueva combinación de teclas…\nEsc — cancelar", "pt": "Pressione a nova combinação de teclas…\nEsc — cancelar", "de": "Neue Tastenkombination drücken…\nEsc — abbrechen", "fr": "Appuie sur la nouvelle combinaison…\nEsc — annuler", "zh": "按下新的快捷键组合…\nEsc — 取消"},
    "hk_bad": {"en": "The screenshot hotkey must be a combination (Ctrl/Alt/Shift/Win + key), an F-key or PrtSc.", "ru": "Горячая клавиша скриншота должна быть сочетанием (Ctrl/Alt/Shift/Win + клавиша), F-клавишей или PrtSc.", "es": "El atajo de captura debe ser una combinación (Ctrl/Alt/Shift/Win + tecla), una tecla F o PrtSc.", "pt": "O atalho de captura deve ser uma combinação (Ctrl/Alt/Shift/Win + tecla), uma tecla F ou PrtSc.", "de": "Der Hotkey muss eine Kombination (Strg/Alt/Umschalt/Win + Taste), eine F-Taste oder Druck sein.", "fr": "Le raccourci doit être une combinaison (Ctrl/Alt/Maj/Win + touche), une touche F ou Impr. écran.", "zh": "截图快捷键必须是组合键（Ctrl/Alt/Shift/Win + 键）、F 键或 PrtSc。"},
    "sec_after": {"en": "After the capture", "ru": "После снимка", "es": "Tras la captura", "pt": "Depois da captura", "de": "Nach der Aufnahme", "fr": "Après la capture", "zh": "截图之后"},
    "open_editor": {"en": "Open the editor", "ru": "Открыть редактор", "es": "Abrir el editor", "pt": "Abrir o editor", "de": "Editor öffnen", "fr": "Ouvrir l'éditeur", "zh": "打开编辑器"},
    "copy_now": {"en": "Copy to the clipboard right away", "ru": "Сразу скопировать в буфер", "es": "Copiar al portapapeles directamente", "pt": "Copiar direto para a área de transferência", "de": "Sofort in die Zwischenablage", "fr": "Copier directement dans le presse-papiers", "zh": "直接复制到剪贴板"},
    "sec_history": {"en": "History", "ru": "История", "es": "Historial", "pt": "Histórico", "de": "Verlauf", "fr": "Historique", "zh": "历史"},
    "hist_keep": {"en": "Keep screenshots", "ru": "Сохранять скриншоты", "es": "Guardar las capturas", "pt": "Manter as capturas", "de": "Bildschirmfotos aufbewahren", "fr": "Conserver les captures", "zh": "保存截图"},
    "keep_for": {"en": "Keep for", "ru": "Хранить", "es": "Conservar", "pt": "Manter por", "de": "Aufbewahren für", "fr": "Conserver pendant", "zh": "保留"},
    "days_n": {"en": "%d days", "ru": "%d дней", "es": "%d días", "pt": "%d dias", "de": "%d Tage", "fr": "%d jours", "zh": "%d 天"},
    "hist_note": {"en": "Files live in %s and are deleted after that time.", "ru": "Файлы лежат в %s и удаляются по истечении срока.", "es": "Los archivos están en %s y se borran pasado ese tiempo.", "pt": "Os arquivos ficam em %s e são apagados após esse prazo.", "de": "Die Dateien liegen in %s und werden danach gelöscht.", "fr": "Les fichiers sont dans %s et supprimés après ce délai.", "zh": "文件保存在 %s，到期后自动删除。"},
    "hist_open": {"en": "Open history", "ru": "Открыть историю", "es": "Abrir historial", "pt": "Abrir histórico", "de": "Verlauf öffnen", "fr": "Ouvrir l'historique", "zh": "打开历史"},
    "hist_folder": {"en": "Open folder", "ru": "Открыть папку", "es": "Abrir carpeta", "pt": "Abrir pasta", "de": "Ordner öffnen", "fr": "Ouvrir le dossier", "zh": "打开文件夹"},
    "hist_clear": {"en": "Clear history", "ru": "Очистить историю", "es": "Borrar historial", "pt": "Limpar histórico", "de": "Verlauf löschen", "fr": "Effacer l'historique", "zh": "清空历史"},
    "hist_sure": {"en": "Delete all?", "ru": "Удалить всё?", "es": "¿Borrar todo?", "pt": "Apagar tudo?", "de": "Alles löschen?", "fr": "Tout supprimer ?", "zh": "全部删除？"},
    "hist_empty": {"en": "No screenshots yet. Press %s.", "ru": "Скриншотов пока нет. Нажми %s.", "es": "Aún no hay capturas. Pulsa %s.", "pt": "Ainda não há capturas. Pressione %s.", "de": "Noch keine Bildschirmfotos. Drücke %s.", "fr": "Pas encore de capture. Appuie sur %s.", "zh": "还没有截图。按 %s。"},
    "hist_count": {"en": "Screenshots: %d", "ru": "Скриншотов: %d", "es": "Capturas: %d", "pt": "Capturas: %d", "de": "Bildschirmfotos: %d", "fr": "Captures : %d", "zh": "截图：%d"},
    "hist_more": {"en": "Show more", "ru": "Показать ещё", "es": "Mostrar más", "pt": "Mostrar mais", "de": "Mehr anzeigen", "fr": "Afficher plus", "zh": "显示更多"},
    "hint": {"en": "Select a region  ·  click — whole screen  ·  Esc — cancel", "ru": "Выдели область  ·  клик — весь экран  ·  Esc — отмена", "es": "Selecciona una zona  ·  clic — toda la pantalla  ·  Esc — cancelar", "pt": "Selecione uma área  ·  clique — tela inteira  ·  Esc — cancelar", "de": "Bereich aufziehen  ·  Klick — ganzer Bildschirm  ·  Esc — abbrechen", "fr": "Sélectionne une zone  ·  clic — tout l'écran  ·  Esc — annuler", "zh": "选择区域  ·  点击 — 整个屏幕  ·  Esc — 取消"},
    "editor_title": {"en": "Screenshot", "ru": "Скриншот", "es": "Captura", "pt": "Captura de tela", "de": "Bildschirmfoto", "fr": "Capture d'écran", "zh": "截图"},
    "t_pen": {"en": "Pen", "ru": "Карандаш", "es": "Lápiz", "pt": "Lápis", "de": "Stift", "fr": "Crayon", "zh": "画笔"},
    "t_marker": {"en": "Marker", "ru": "Маркер", "es": "Marcador", "pt": "Marcador", "de": "Marker", "fr": "Surligneur", "zh": "荧光笔"},
    "t_rect": {"en": "Frame", "ru": "Рамка", "es": "Marco", "pt": "Moldura", "de": "Rahmen", "fr": "Cadre", "zh": "边框"},
    "t_arrow": {"en": "Arrow", "ru": "Стрелка", "es": "Flecha", "pt": "Seta", "de": "Pfeil", "fr": "Flèche", "zh": "箭头"},
    "t_crop": {"en": "Crop", "ru": "Обрезать", "es": "Recortar", "pt": "Recortar", "de": "Zuschneiden", "fr": "Recadrer", "zh": "裁剪"},
    "t_undo": {"en": "Undo", "ru": "Отменить", "es": "Deshacer", "pt": "Desfazer", "de": "Rückgängig", "fr": "Annuler", "zh": "撤销"},
    "copy": {"en": "Copy to clipboard", "ru": "Скопировать в буфер", "es": "Copiar al portapapeles", "pt": "Copiar para a área de transferência", "de": "In die Zwischenablage", "fr": "Copier dans le presse-papiers", "zh": "复制到剪贴板"},
    "copy_short": {"en": "Copy", "ru": "Копировать", "es": "Copiar", "pt": "Copiar", "de": "Kopieren", "fr": "Copier", "zh": "复制"},
    "save_as": {"en": "Save as…", "ru": "Сохранить как…", "es": "Guardar como…", "pt": "Salvar como…", "de": "Speichern unter…", "fr": "Enregistrer sous…", "zh": "另存为…"},
    "copied": {"en": "Copied to the clipboard", "ru": "Скопировано в буфер", "es": "Copiado al portapapeles", "pt": "Copiado para a área de transferência", "de": "In die Zwischenablage kopiert", "fr": "Copié dans le presse-papiers", "zh": "已复制到剪贴板"},
    "saved": {"en": "Saved", "ru": "Сохранено", "es": "Guardado", "pt": "Salvo", "de": "Gespeichert", "fr": "Enregistré", "zh": "已保存"},
    "err_capture": {"en": "Could not capture the screen", "ru": "Не удалось снять экран", "es": "No se pudo capturar la pantalla", "pt": "Não foi possível capturar a tela", "de": "Bildschirm konnte nicht aufgenommen werden", "fr": "Impossible de capturer l'écran", "zh": "无法截取屏幕"},
    "err_clip": {"en": "Could not write to the clipboard", "ru": "Не удалось записать в буфер обмена", "es": "No se pudo escribir en el portapapeles", "pt": "Não foi possível gravar na área de transferência", "de": "Zwischenablage konnte nicht beschrieben werden", "fr": "Impossible d'écrire dans le presse-papiers", "zh": "无法写入剪贴板"},
    "delete": {"en": "Delete", "ru": "Удалить", "es": "Eliminar", "pt": "Excluir", "de": "Löschen", "fr": "Supprimer", "zh": "删除"},
    "png": {"en": "PNG image", "ru": "Изображение PNG", "es": "Imagen PNG", "pt": "Imagem PNG", "de": "PNG-Bild", "fr": "Image PNG", "zh": "PNG 图片"},
    "jpg": {"en": "JPEG image", "ru": "Изображение JPEG", "es": "Imagen JPEG", "pt": "Imagem JPEG", "de": "JPEG-Bild", "fr": "Image JPEG", "zh": "JPEG 图片"},
}

LANG_GET = lambda: "en"


def SS(key, *args):
    s = SSTR.get(key, {})
    txt = s.get(LANG_GET()) or s.get("en") or key
    return (txt % args) if args else txt


DEFAULTS = {
    "enabled": True,
    "hk_mods": vs.MOD_CONTROL | vs.MOD_SHIFT, "hk_vk": 0x41,     # Ctrl+Shift+A
    "editor": True,                 # False: the capture goes to the clipboard at once
    "history": True, "keep_days": 30,
    "save_dir": "",                 # last "Save as…" folder
}
# keys allowed as the hotkey WITHOUT a modifier: F1-F24, Pause, ScrollLock, PrtSc.
# A single modifier is refused: it would fire on every Ctrl / Shift press.
PLAIN_HOTKEYS_OK = frozenset(range(0x70, 0x88)) | {0x13, 0x91, 0x2C}
KEEP_CHOICES = (7, 14, 30, 90)
THUMB_W, THUMB_H = 232, 140
MIN_DRAG = 4                        # a shorter drag is a click = whole monitor
UNDO_MAX = 15
PAGE = 60                           # history cards shown per "Show more"
DARK = 45                           # brightness (%) of the frozen screen outside the selection

ICON_FONT = "Segoe MDL2 Assets"
TOOLS = (("pen", "", "t_pen"), ("marker", "", "t_marker"), ("rect", "", "t_rect"),
         ("arrow", "", "t_arrow"), ("crop", "", "t_crop"))
GLYPH_UNDO = ""
COLORS = ("#ff3b30", "#ffd60a", "#30d158", "#0a84ff", "#ffffff", "#111111")
WIDTHS = (2, 4, 8)
MARKER_ALPHA = 120

# ---------------------------------------------------------------------------
#  Win32: monitors
# ---------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
MONITOR_DEFAULTTONEAREST = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.MonitorFromPoint.restype = ctypes.c_void_p
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]


def virtual_screen():
    g = user32.GetSystemMetrics
    return g(SM_XVIRTUALSCREEN), g(SM_YVIRTUALSCREEN), g(SM_CXVIRTUALSCREEN), g(SM_CYVIRTUALSCREEN)


def monitor_at(x, y, work=False):
    """Monitor (or its work area) rectangle (l, t, r, b) under a screen point."""
    mon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        r = mi.rcWork if work else mi.rcMonitor
        return r.left, r.top, r.right, r.bottom
    vx, vy, vw, vh = virtual_screen()
    return vx, vy, vx + vw, vy + vh


def grab_screen():
    """Frozen picture of the whole virtual screen + its origin in screen pixels.
    The process is per-monitor DPI aware, so pixels here are physical pixels and
    match GetSystemMetrics / the mouse."""
    vx, vy, vw, vh = virtual_screen()
    img = ImageGrab.grab(all_screens=True)
    if img.size != (vw, vh):            # Pillow and Windows disagree: trust the picture
        vw, vh = img.size
    return img.convert("RGB"), (vx, vy)


def to_dib(img):
    """CF_DIB payload: a 24-bit bottom-up BITMAPINFOHEADER bitmap (a BMP without
    its 14-byte file header). Every Windows app that pastes pictures reads it."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    return buf.getvalue()[14:]


def to_png(img, fast=False):
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=1 if fast else 6)
    return buf.getvalue()


def _theme():
    main = __import__("__main__")
    C = lambda n, d: getattr(main, n, d)
    return {"bg": C("COL_BG", "#1b1b1d"), "sf": C("COL_SURFACE", "#232427"), "sf2": C("COL_SURFACE2", "#2d2e31"),
            "bd": C("COL_BORDER", "#2a2b2c"), "hv": C("COL_HOVER", "#303236"), "ac": C("COL_ACCENT", "#4c8bf5"),
            "tx": C("COL_TEXT", "#e6e8ea"), "dim": C("COL_TEXT_DIM", "#9aa0a6"), "err": C("COL_DANGER", "#ff453a"),
            "font": C("FONT", ("Segoe UI", 10)), "font_sm": C("FONT_SM", ("Segoe UI", 9))}


def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ---------------------------------------------------------------------------
#  Controller: hotkey, history, settings tab, tray entries
# ---------------------------------------------------------------------------
class ScreenshotController:
    def __init__(self, app, get_lang, log, local_dir):
        global LANG_GET
        LANG_GET = get_lang
        self.app, self.root, self.log = app, app.root, log
        self.dir = os.path.join(local_dir, "screenshots")
        self.thumb_dir = os.path.join(self.dir, "thumbs")
        self.draft = None
        self.capture = None             # Capture overlay while it is on screen
        self.hist_win = None
        self._tab = None                # (parent, win, pad) while the Settings tab is shown
        self._capture_dlg = None
        self._hist = {}
        self._thumb_jobs = set()
        self._io_q = queue.Queue()
        self._ui_q = queue.Queue()      # callables for the tk thread, posted by the worker
        threading.Thread(target=self._io_worker, daemon=True, name="hdc-shot-io").start()
        self.hook = vs.KeyHook(log)
        self.hook.start()
        self.register_hotkey()
        self.root.after(25, self._loop)
        self.root.after(30000, self._cleanup_tick)

    # ---- settings (draft while the Settings window is open, see voice.py) ----
    def get(self, k):
        src = self.draft if self.draft is not None else self.app.settings.get("screenshot", {})
        return src.get(k, DEFAULTS[k])

    def set(self, k, v):
        if self.draft is not None:
            self.draft[k] = v
            return
        self.set_now(k, v)

    def set_now(self, k, v):
        """Write straight to the saved settings, draft or not (last Save-as folder)."""
        d = dict(self.app.settings.get("screenshot", {}))
        d[k] = v
        self.app._set_setting("screenshot", d)
        if self.draft is not None:
            self.draft[k] = v

    def begin_edit(self):
        self.draft = dict(self.app.settings.get("screenshot", {}))

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
        self.app._set_setting("screenshot", new)
        self.register_hotkey()
        self._io(self._cleanup)
        self.log("screenshot settings saved")

    # ---- hotkey ----
    def register_hotkey(self):
        self.hook.configure(self.get("hk_mods"), self.get("hk_vk"), self.get("enabled"))
        self.hook.mask_taps = False
        self.log("screenshot hotkey %s hook=%s" % (self.hotkey_text(), "ok" if self.hook._hook else "FAILED"))

    def hotkey_text(self):
        return vs.hotkey_label(self.get("hk_mods"), self.get("hk_vk"))

    def _loop(self):
        # hook events and worker results are handled here, in a tk timer — never in a callback
        try:
            while True:
                try:
                    ev = self.hook.events.get_nowait()
                except queue.Empty:
                    break
                if ev[0] == "press":
                    self.shoot()
                elif ev[0] in ("captured", "capture_cancel"):
                    self._capture_done(ev)
            while True:
                try:
                    fn = self._ui_q.get_nowait()
                except queue.Empty:
                    break
                fn()
        except Exception:
            self.log("screenshot loop:\n" + traceback.format_exc())
        self.root.after(25, self._loop)

    # ---- taking a screenshot ----
    def shoot(self):
        """Hotkey / tray: freeze the screen and let the user pick a region."""
        if self.capture is not None:
            return
        try:
            img, origin = grab_screen()
        except Exception:
            self.log("screen grab failed:\n" + traceback.format_exc())
            self.app._notify(SS("editor_title"), SS("err_capture"), kind="error")
            return
        try:
            self.capture = Capture(self, img, origin)
        except Exception:
            self.capture = None
            self.log("capture overlay failed:\n" + traceback.format_exc())

    def _on_region(self, img):
        self.capture = None
        path = self._new_path() if self.get("history") else None
        if path:
            self._io(lambda: self._write(path, img))
        if self.get("editor"):
            Editor(self, img, path)
        else:
            self.copy_image(img, path)

    def _on_cancel(self):
        self.capture = None

    def copy_image(self, img, path=None, notify=True):
        """Result → clipboard (and into its history file). Returns True on success."""
        try:
            ok = vs.clip_set_image(to_dib(img), to_png(img, fast=True))
        except Exception:
            ok = False
            self.log("clipboard image failed:\n" + traceback.format_exc())
        if path:
            self._io(lambda: self._write(path, img))
        if notify:
            if ok:
                self.app._notify(SS("editor_title"), SS("copied"), kind="copied")
            else:
                self.app._notify(SS("editor_title"), SS("err_clip"), kind="error")
        return ok

    # ---- history files ----
    def _new_path(self):
        base = time.strftime("Screenshot_%Y-%m-%d_%H-%M-%S")
        path = os.path.join(self.dir, base + ".png")
        n = 2
        while os.path.exists(path):
            path = os.path.join(self.dir, "%s_%d.png" % (base, n))
            n += 1
        return path

    def _thumb_path(self, path):
        return os.path.join(self.thumb_dir, os.path.splitext(os.path.basename(path))[0] + ".jpg")

    def _io(self, fn):
        self._io_q.put(fn)

    def _io_worker(self):
        while True:
            fn = self._io_q.get()
            try:
                fn()
            except Exception:
                self.log("screenshot io:\n" + traceback.format_exc())

    def _write(self, path, img):
        os.makedirs(self.thumb_dir, exist_ok=True)
        tmp = path + ".tmp"
        img.save(tmp, "PNG")
        os.replace(tmp, path)
        self._make_thumb(path, img)
        self._ui_q.put(self._history_refresh)

    def _make_thumb(self, path, img=None):
        os.makedirs(self.thumb_dir, exist_ok=True)
        im = (img or Image.open(path)).convert("RGB")
        im.thumbnail((THUMB_W, THUMB_H))
        bg = Image.new("RGB", (THUMB_W, THUMB_H), _hex_rgb(_theme()["sf2"]))
        bg.paste(im, ((THUMB_W - im.width) // 2, (THUMB_H - im.height) // 2))
        tp = self._thumb_path(path)
        bg.save(tp + ".tmp", "JPEG", quality=85)
        os.replace(tp + ".tmp", tp)

    def entries(self):
        """[(path, mtime)] newest first."""
        out = []
        try:
            with os.scandir(self.dir) as it:
                for e in it:
                    if e.is_file() and e.name.lower().endswith(".png"):
                        try:
                            out.append((e.path, e.stat().st_mtime))
                        except OSError:
                            pass
        except OSError:
            pass
        out.sort(key=lambda t: t[1], reverse=True)
        return out

    def _cleanup_tick(self):
        self._io(self._cleanup)
        self.root.after(6 * 3600 * 1000, self._cleanup_tick)

    def _cleanup(self):
        """Delete captures (and their thumbnails) older than keep_days."""
        try:
            days = int(self.get("keep_days"))
        except (TypeError, ValueError):
            days = DEFAULTS["keep_days"]
        limit = time.time() - max(1, days) * 86400
        gone = 0
        for path, mt in self.entries():
            if mt < limit:
                for p in (path, self._thumb_path(path)):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                gone += 1
        try:                                            # thumbnails whose capture is gone
            names = {os.path.splitext(os.path.basename(p))[0] for p, _ in self.entries()}
            with os.scandir(self.thumb_dir) as it:
                for e in it:
                    if e.is_file() and os.path.splitext(e.name)[0] not in names:
                        os.remove(e.path)
        except OSError:
            pass
        if gone:
            self.log("screenshot history: removed %d old files" % gone)
            self._ui_q.put(self._history_refresh)

    def delete_file(self, path):
        for p in (path, self._thumb_path(path)):
            try:
                os.remove(p)
            except OSError:
                pass
        self._history_refresh()

    def clear_history(self):
        for path, _ in self.entries():
            self.delete_file(path)
        self._history_refresh()

    def open_file(self, path):
        try:
            img = Image.open(path).convert("RGB")
        except Exception:
            self.log("screenshot open failed: %r" % path)
            return
        Editor(self, img, path)

    def copy_file(self, path):
        try:
            img = Image.open(path).convert("RGB")
        except Exception:
            self.log("screenshot open failed: %r" % path)
            return
        self.copy_image(img, None)

    def open_folder(self):
        os.makedirs(self.dir, exist_ok=True)
        os.startfile(self.dir)

    def shutdown(self):
        try:
            self.hook.stop()
            if self.capture is not None:
                self.capture.close()
        except Exception as ex:
            self.log("screenshot shutdown: %r" % ex)

    # ---- history window ----
    def open_history(self):
        if self.hist_win and self.hist_win.winfo_exists():
            self.hist_win.lift()
            self.hist_win.focus_force()
            return
        th = _theme()
        win = self.app._dialog(SS("hist_menu"), 800, 620)
        win.resizable(True, True)
        win.minsize(540, 360)
        win.attributes("-topmost", False)
        self.hist_win = win
        h = self._hist = {"shown": PAGE, "refs": [], "sure_at": 0.0}

        top = tk.Frame(win, bg=th["sf"])
        top.pack(fill="x", padx=16, pady=(14, 6))
        h["count"] = tk.Label(top, text="", bg=th["sf"], fg=th["dim"], font=th["font_sm"])
        h["count"].pack(side="left")
        h["clear"] = self.app._ghost_btn(top, SS("hist_clear"), self._clear_click)
        h["clear"].pack(side="right")
        self.app._ghost_btn(top, SS("hist_folder"), self.open_folder).pack(side="right", padx=(0, 8))

        box = tk.Frame(win, bg=th["sf"])
        box.pack(fill="both", expand=True, padx=(16, 8), pady=(0, 12))
        cv = tk.Canvas(box, bg=th["sf"], highlightthickness=0, bd=0)
        sb = self.app._dark_scrollbar(box, cv)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        body = tk.Frame(cv, bg=th["sf"])
        wid = cv.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))

        def resized(e):
            cv.itemconfigure(wid, width=e.width)
            cols = max(1, (e.width - 8) // (THUMB_W + 20))
            if cols != h.get("cols"):
                h["cols"] = cols
                self._history_refresh()
        cv.bind("<Configure>", resized)
        win.bind("<MouseWheel>", lambda e: cv.yview_scroll(int(-e.delta / 120) * 3, "units"))
        win.bind("<Escape>", lambda e: win.destroy())
        h["cv"], h["body"], h["th"] = cv, body, th
        win.focus_force()

    def _clear_click(self):
        h = self._hist
        if time.time() - h.get("sure_at", 0) < 3.0:
            h["sure_at"] = 0.0
            self.clear_history()
            return
        h["sure_at"] = time.time()
        btn = h.get("clear")
        if btn and btn.winfo_exists():
            btn.configure(text=SS("hist_sure"))
            self.root.after(3100, lambda: btn.winfo_exists() and time.time() - h.get("sure_at", 0) >= 3.0
                            and btn.configure(text=SS("hist_clear")))

    def _history_refresh(self):
        win = self.hist_win
        try:
            if not (win and win.winfo_exists() and self._hist.get("body")):
                return
        except Exception:
            return
        try:
            self._history_fill()
        except Exception:
            self.log("screenshot history fill:\n" + traceback.format_exc())

    def _history_fill(self):
        h, th = self._hist, self._hist["th"]
        body = h["body"]
        for w in body.winfo_children():
            w.destroy()
        h["refs"] = []
        items = self.entries()
        h["count"].configure(text=SS("hist_count", len(items)) if items else "")
        if not items:
            tk.Label(body, text=SS("hist_empty", self.hotkey_text()), bg=th["sf"], fg=th["dim"],
                     font=th["font"]).pack(pady=40)
            return
        cols = max(1, h.get("cols", 3))
        shown = items[:h["shown"]]
        for i, (path, mt) in enumerate(shown):
            card = tk.Frame(body, bg=th["sf2"])
            card.grid(row=i // cols, column=i % cols, padx=(0, 10), pady=(0, 10), sticky="n")
            photo = self._thumb_photo(path)
            if photo:
                h["refs"].append(photo)
                pic = tk.Label(card, image=photo, bg=th["sf2"], cursor="hand2", bd=0)
            else:                                   # thumbnail still being made by the worker
                pic = tk.Canvas(card, width=THUMB_W, height=THUMB_H, bg=th["sf2"], highlightthickness=0,
                                bd=0, cursor="hand2")
                pic.create_text(THUMB_W // 2, THUMB_H // 2, text="…", fill=th["dim"], font=("Segoe UI", 14))
            pic.pack(padx=6, pady=(6, 2))
            pic.bind("<Button-1>", lambda _e, p=path: self.open_file(p))
            foot = tk.Frame(card, bg=th["sf2"])
            foot.pack(fill="x", padx=8, pady=(0, 6))
            tk.Label(foot, text=time.strftime("%d.%m %H:%M", time.localtime(mt)), bg=th["sf2"], fg=th["dim"],
                     font=th["font_sm"]).pack(side="left")
            x = tk.Label(foot, text="✕", bg=th["sf2"], fg=th["dim"], font=("Segoe UI", 10), cursor="hand2")
            x.pack(side="right")
            x.bind("<Button-1>", lambda _e, p=path: self.delete_file(p))
            cb = self.app._accent_btn(foot, SS("copy_short"), lambda p=path: self.copy_file(p))
            cb.configure(padx=10, pady=2, font=th["font_sm"])
            cb.pack(side="right", padx=(0, 10))
        if len(items) > len(shown):
            more = self.app._ghost_btn(body, SS("hist_more"), self._history_more)
            more.grid(row=len(shown) // cols + 1, column=0, columnspan=cols, pady=(4, 10))
        h["cv"].yview_moveto(0)

    def _history_more(self):
        self._hist["shown"] += PAGE
        self._history_refresh()

    def _thumb_photo(self, path):
        tp = self._thumb_path(path)
        try:
            if os.path.exists(tp) and os.path.getmtime(tp) >= os.path.getmtime(path) - 1:
                return ImageTk.PhotoImage(Image.open(tp))
        except Exception:
            pass
        if path not in self._thumb_jobs:                 # make it in the worker, redraw after
            self._thumb_jobs.add(path)

            def job():
                try:
                    self._make_thumb(path)
                finally:
                    self._thumb_jobs.discard(path)
                    self._ui_q.put(self._history_refresh)
            self._io(job)
        return None

    # ---- Settings tab ----
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

        def radio(options, key):
            cur = self.get(key)
            for val, label in options:
                row = tk.Frame(parent, bg=th["sf"])
                row.pack(fill="x", padx=pad, pady=2)
                on = val == cur
                dot = tk.Label(row, text="◉" if on else "○", bg=th["sf"], fg=th["ac"] if on else th["dim"],
                               font=("Segoe UI Symbol", 12), cursor="hand2")
                dot.pack(side="left")
                lb = tk.Label(row, text=label, bg=th["sf"], fg=th["tx"] if on else th["dim"], font=th["font"],
                              cursor="hand2", anchor="w")
                lb.pack(side="left", padx=(8, 0))

                def pick(_e=None, v=val):
                    self.set(key, v)
                    self._rebuild()
                dot.bind("<Button-1>", pick)
                lb.bind("<Button-1>", pick)

        note(SS("intro"), pady=(16, 6))
        check(SS("enable"), "enabled")

        header(SS("sec_hotkey"))
        row = tk.Frame(parent, bg=th["sf"])
        row.pack(fill="x", padx=pad, pady=(6, 2))
        tk.Label(row, text=self.hotkey_text(), bg=th["sf2"], fg=th["tx"], font=("Consolas", 11),
                 padx=12, pady=5).pack(side="left")
        a._ghost_btn(row, SS("change"), self._capture_hotkey).pack(side="left", padx=10)

        header(SS("sec_after"))
        radio([(True, SS("open_editor")), (False, SS("copy_now"))], "editor")

        header(SS("sec_history"))
        check(SS("hist_keep"), "history", lambda v: self._rebuild())
        if self.get("history"):
            row = tk.Frame(parent, bg=th["sf"])
            row.pack(fill="x", padx=pad, pady=(2, 4))
            tk.Label(row, text=SS("keep_for"), bg=th["sf"], fg=th["dim"], font=th["font_sm"]).pack(side="left", padx=(0, 10))
            cur = self.get("keep_days")
            for d in KEEP_CHOICES:
                on = d == cur
                pill = tk.Label(row, text=SS("days_n", d), bg=th["ac"] if on else th["sf2"],
                                fg="white" if on else th["tx"], font=th["font_sm"], padx=10, pady=3, cursor="hand2")
                pill.pack(side="left", padx=(0, 6))
                pill.bind("<Button-1>", lambda _e, v=d: (self.set("keep_days", v), self._rebuild()))
            note(SS("hist_note", self.dir), pady=(2, 6))
        row = tk.Frame(parent, bg=th["sf"])
        row.pack(fill="x", padx=pad, pady=(4, 2))
        a._ghost_btn(row, SS("hist_open"), self.open_history).pack(side="left")
        tk.Frame(parent, bg=th["sf"], height=16).pack()

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
            self.log("screenshot tab rebuild:\n" + traceback.format_exc())

    def _capture_hotkey(self):
        th = _theme()
        dlg = self.app._dialog(SS("sec_hotkey"), 420, 170)
        tk.Label(dlg, text=SS("press_combo"), bg=th["sf"], fg=th["tx"], font=("Segoe UI", 11),
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

    def _capture_done(self, ev):
        bad = False
        if ev[0] == "captured":
            mods, vk = ev[1], ev[2]
            if mods == 0 and vk not in PLAIN_HOTKEYS_OK:
                bad = True
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
        if bad:
            self.app._info(SS("sec_hotkey"), SS("hk_bad"))


# ---------------------------------------------------------------------------
#  Full-screen overlay: the frozen screen, region selection
# ---------------------------------------------------------------------------
class Capture:
    def __init__(self, ctl, img, origin):
        self.ctl, self.img = ctl, img
        self.ox, self.oy = origin
        w, h = img.size
        top = tk.Toplevel(ctl.root)
        self.top = top
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg="black")
        top.geometry("%dx%d+%d+%d" % (w, h, self.ox, self.oy))
        cv = tk.Canvas(top, width=w, height=h, bg="black", highlightthickness=0, bd=0, cursor="crosshair")
        cv.pack(fill="both", expand=True)
        self.cv = cv
        self.dark = ImageTk.PhotoImage(img.point(lambda v: v * DARK // 100))
        cv.create_image(0, 0, image=self.dark, anchor="nw")
        self.sel_photo = None
        self.sel_item = cv.create_image(0, 0, anchor="nw", state="hidden")
        self.rect = cv.create_rectangle(0, 0, 0, 0, outline="#ffffff", width=1, state="hidden")
        self.hx = cv.create_line(0, 0, 0, 0, fill="#9aa0a6")
        self.hy = cv.create_line(0, 0, 0, 0, fill="#9aa0a6")
        self.size_bg = cv.create_rectangle(0, 0, 0, 0, fill="#1b1b1d", outline="", state="hidden")
        self.size_txt = cv.create_text(0, 0, text="", fill="#e6e8ea", font=("Segoe UI", 9), anchor="nw", state="hidden")
        # hint at the top of the monitor under the cursor
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        ml, mt, mr, _mb = monitor_at(pt.x, pt.y)
        hx = (ml + mr) // 2 - self.ox
        hy = mt - self.oy + 28
        t = cv.create_text(hx, hy, text=SS("hint"), fill="#e6e8ea", font=("Segoe UI", 11), anchor="n")
        bb = cv.bbox(t)
        self.hint_bg = cv.create_rectangle(bb[0] - 16, bb[1] - 8, bb[2] + 16, bb[3] + 8, fill="#1b1b1d", outline="#2a2b2c")
        cv.tag_raise(t, self.hint_bg)
        self.hint = t
        self.start = None
        self.cur = None
        self._pending = False
        self.done = False
        cv.bind("<Motion>", self._motion)
        cv.bind("<ButtonPress-1>", self._press)
        cv.bind("<B1-Motion>", self._drag)
        cv.bind("<ButtonRelease-1>", self._release)
        cv.bind("<ButtonPress-3>", lambda e: self.cancel())
        top.bind("<Escape>", lambda e: self.cancel())
        top.bind("<FocusOut>", self._focus_out)
        self._crosshair(pt.x - self.ox, pt.y - self.oy)
        top.lift()
        top.after(30, self._grab_focus)

    def _grab_focus(self):
        try:
            if self.top.winfo_exists() and not self.done:
                self.top.focus_force()
        except Exception:
            pass

    def _focus_out(self, _e):
        # another window took the focus (an alert, a game...): take it back so Esc
        # keeps working while the overlay is up; the mouse works either way.
        # focus_get() is None only when the focus left this application: moving
        # between our own widgets must not start a focus ping-pong.
        def check():
            try:
                if not self.done and self.top.winfo_exists() and self.top.focus_get() is None:
                    self._grab_focus()
            except Exception:
                pass
        if not self.done:
            self.top.after(150, check)

    def _crosshair(self, x, y):
        w, h = self.img.size
        self.cv.coords(self.hx, 0, y, w, y)
        self.cv.coords(self.hy, x, 0, x, h)

    def _motion(self, e):
        if self.start is None:
            self._crosshair(e.x, e.y)

    def _press(self, e):
        self.start = self.cur = (e.x, e.y)
        self.cv.itemconfigure(self.hint, state="hidden")
        self.cv.itemconfigure(self.hint_bg, state="hidden")
        self.cv.itemconfigure(self.hx, state="hidden")
        self.cv.itemconfigure(self.hy, state="hidden")

    def _drag(self, e):
        if self.start is None:
            return
        self.cur = (e.x, e.y)
        if not self._pending:
            self._pending = True
            self.cv.after(12, self._redraw)

    def _box(self):
        w, h = self.img.size
        (x1, y1), (x2, y2) = self.start, self.cur
        x1, x2 = sorted((max(0, min(w, x1)), max(0, min(w, x2))))
        y1, y2 = sorted((max(0, min(h, y1)), max(0, min(h, y2))))
        return x1, y1, x2, y2

    def _redraw(self):
        self._pending = False
        if self.start is None or self.done:
            return
        x1, y1, x2, y2 = self._box()
        cv = self.cv
        if x2 - x1 < 1 or y2 - y1 < 1:
            cv.itemconfigure(self.sel_item, state="hidden")
            cv.itemconfigure(self.rect, state="hidden")
            return
        self.sel_photo = ImageTk.PhotoImage(self.img.crop((x1, y1, x2, y2)))
        cv.itemconfigure(self.sel_item, image=self.sel_photo, state="normal")
        cv.coords(self.sel_item, x1, y1)
        cv.coords(self.rect, x1, y1, x2, y2)
        cv.itemconfigure(self.rect, state="normal")
        txt = "%d × %d" % (x2 - x1, y2 - y1)
        w, h = self.img.size
        tx = min(x1 + 4, w - 90)
        ty = y2 + 6 if y2 + 28 < h else y1 - 26
        cv.coords(self.size_txt, tx + 6, ty + 2)
        cv.itemconfigure(self.size_txt, text=txt, state="normal")
        bb = cv.bbox(self.size_txt)
        cv.coords(self.size_bg, bb[0] - 6, bb[1] - 2, bb[2] + 6, bb[3] + 2)
        cv.itemconfigure(self.size_bg, state="normal")
        cv.tag_raise(self.size_bg)
        cv.tag_raise(self.size_txt)

    def _release(self, e):
        if self.start is None or self.done:
            return
        self.cur = (e.x, e.y)
        x1, y1, x2, y2 = self._box()
        if x2 - x1 < MIN_DRAG and y2 - y1 < MIN_DRAG:
            # a click: the whole monitor under the cursor
            ml, mt, mr, mb = monitor_at(e.x + self.ox, e.y + self.oy)
            w, h = self.img.size
            x1, y1 = max(0, ml - self.ox), max(0, mt - self.oy)
            x2, y2 = min(w, mr - self.ox), min(h, mb - self.oy)
        if x2 - x1 < 1 or y2 - y1 < 1:
            self.cancel()
            return
        region = self.img.crop((x1, y1, x2, y2))
        self.close()
        try:
            self.ctl._on_region(region)
        except Exception:
            self.ctl.capture = None
            self.ctl.log("screenshot region failed:\n" + traceback.format_exc())

    def cancel(self):
        self.close()
        self.ctl._on_cancel()

    def close(self):
        self.done = True
        try:
            self.top.destroy()
        except Exception:
            pass
        self.sel_photo = self.dark = None


# ---------------------------------------------------------------------------
#  Editor: pen, marker, frame, arrow, crop → clipboard / file
# ---------------------------------------------------------------------------
class Editor:
    def __init__(self, ctl, img, path):
        self.ctl, self.path = ctl, path
        self.img = img.convert("RGB")
        self.undo = []
        self.tool, self.color, self.width = "pen", COLORS[0], WIDTHS[1]
        self.scale, self.ox, self.oy = 1.0, 0, 0
        self.photo = None
        self.pts = []
        self.item = None
        self._render_job = None
        th = self.th = _theme()
        app = ctl.app

        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        wl, wt, wr, wb = monitor_at(pt.x, pt.y, work=True)
        iw, ih = self.img.size
        w = max(640, min(iw + 40, int((wr - wl) * 0.92)))
        h = max(440, min(ih + 40 + 52 + 60, int((wb - wt) * 0.9)))
        win = self.win = app._dialog(SS("editor_title"), w, h)
        win.resizable(True, True)
        win.minsize(560, 380)
        win.attributes("-topmost", False)
        # centre on the monitor where the mouse is
        win.geometry("+%d+%d" % (wl + (wr - wl - w) // 2, wt + (wb - wt - h) // 2))

        bar = tk.Frame(win, bg=th["sf"])
        bar.pack(fill="x", padx=12, pady=(10, 6))
        self.tool_btns = {}
        for key, glyph, name in TOOLS:
            b = tk.Label(bar, text=glyph, bg=th["sf"], fg=th["dim"], font=(ICON_FONT, 13), padx=9, pady=5,
                         cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda _e, k=key: self._set_tool(k))
            b.bind("<Enter>", lambda _e, b=b, n=name: self._hover(b, SS(n)))
            b.bind("<Leave>", lambda _e, b=b: self._hover(b, None))
            self.tool_btns[key] = b
        tk.Frame(bar, bg=th["bd"], width=1).pack(side="left", fill="y", padx=10, pady=6)
        self.color_btns = {}
        for c in COLORS:
            cv = tk.Canvas(bar, width=22, height=22, bg=th["sf"], highlightthickness=0, bd=0, cursor="hand2")
            cv.create_oval(4, 4, 18, 18, fill=c, outline=c)
            cv.pack(side="left", padx=2)
            cv.bind("<Button-1>", lambda _e, c=c: self._set_color(c))
            self.color_btns[c] = cv
        tk.Frame(bar, bg=th["bd"], width=1).pack(side="left", fill="y", padx=10, pady=6)
        self.width_btns = {}
        for wd in WIDTHS:
            cv = tk.Canvas(bar, width=26, height=22, bg=th["sf"], highlightthickness=0, bd=0, cursor="hand2")
            r = 2 + wd // 2
            cv.create_oval(13 - r, 11 - r, 13 + r, 11 + r, fill=th["tx"], outline=th["tx"])
            cv.pack(side="left", padx=1)
            cv.bind("<Button-1>", lambda _e, v=wd: self._set_width(v))
            self.width_btns[wd] = cv
        tk.Frame(bar, bg=th["bd"], width=1).pack(side="left", fill="y", padx=10, pady=6)
        u = tk.Label(bar, text=GLYPH_UNDO, bg=th["sf"], fg=th["dim"], font=(ICON_FONT, 13), padx=9, pady=5,
                     cursor="hand2")
        u.pack(side="left")
        u.bind("<Button-1>", lambda _e: self._undo())
        u.bind("<Enter>", lambda _e: self._hover(u, SS("t_undo")))
        u.bind("<Leave>", lambda _e: self._hover(u, None))
        self.tip = tk.Label(bar, text="", bg=th["sf"], fg=th["dim"], font=th["font_sm"])
        self.tip.pack(side="left", padx=12)

        bottom = tk.Frame(win, bg=th["sf"])
        bottom.pack(side="bottom", fill="x")
        tk.Frame(bottom, bg=th["bd"], height=1).pack(fill="x")
        brow = tk.Frame(bottom, bg=th["sf"])
        brow.pack(fill="x", padx=16, pady=10)
        self.size_lbl = tk.Label(brow, text="", bg=th["sf"], fg=th["dim"], font=th["font_sm"])
        self.size_lbl.pack(side="left")
        app._accent_btn(brow, SS("copy"), self.copy).pack(side="right")
        app._ghost_btn(brow, SS("save_as"), self.save_as).pack(side="right", padx=(0, 8))

        cv = self.cv = tk.Canvas(win, bg=th["bg"], highlightthickness=0, bd=0, cursor="crosshair")
        cv.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.img_item = cv.create_image(0, 0, anchor="nw")
        cv.bind("<Configure>", lambda e: self._schedule_render())
        cv.bind("<ButtonPress-1>", self._press)
        cv.bind("<B1-Motion>", self._drag)
        cv.bind("<ButtonRelease-1>", self._release)
        win.bind("<Control-Key>", self._ctrl_key)
        win.bind("<Return>", lambda e: self.copy())
        win.bind("<Escape>", lambda e: self.close())
        self._set_tool("pen")
        self._set_color(self.color)
        self._set_width(self.width)
        win.focus_force()

    # ---- toolbar ----
    def _hover(self, b, text):
        th = self.th
        on = b is self.tool_btns.get(self.tool)
        b.configure(bg=th["hv"] if text else (th["sf2"] if on else th["sf"]))
        self.tip.configure(text=text or "")

    def _set_tool(self, key):
        self.tool = key
        th = self.th
        for k, b in self.tool_btns.items():
            on = k == key
            b.configure(bg=th["sf2"] if on else th["sf"], fg=th["ac"] if on else th["dim"])

    def _set_color(self, c):
        self.color = c
        th = self.th
        for k, cv in self.color_btns.items():
            cv.delete("ring")
            if k == c:
                cv.create_oval(1, 1, 21, 21, outline=th["tx"], width=2, tags="ring")

    def _set_width(self, w):
        self.width = w
        th = self.th
        for k, cv in self.width_btns.items():
            cv.configure(bg=th["sf2"] if k == w else th["sf"])

    def _ctrl_key(self, e):
        code = e.keycode
        if code == 0x5A:                  # Ctrl+Z
            self._undo()
        elif code == 0x53:                # Ctrl+S
            self.save_as()
        elif code == 0x43:                # Ctrl+C
            self.copy()
        else:
            return None
        return "break"

    # ---- display ----
    def _schedule_render(self):
        if self._render_job:
            self.cv.after_cancel(self._render_job)
        self._render_job = self.cv.after(30, self._render)

    def _render(self):
        self._render_job = None
        cv = self.cv
        cw, ch = cv.winfo_width(), cv.winfo_height()
        if cw < 20 or ch < 20:
            return
        iw, ih = self.img.size
        self.scale = min(1.0, (cw - 16) / iw, (ch - 16) / ih)
        dw, dh = max(1, round(iw * self.scale)), max(1, round(ih * self.scale))
        disp = self.img if self.scale >= 1.0 else self.img.resize((dw, dh), Image.BILINEAR)
        self.photo = ImageTk.PhotoImage(disp)
        self.ox, self.oy = (cw - dw) // 2, (ch - dh) // 2
        cv.coords(self.img_item, self.ox, self.oy)
        cv.itemconfigure(self.img_item, image=self.photo)
        self.size_lbl.configure(text="%d × %d" % (iw, ih) + ("" if self.scale >= 1.0 else "   ·   %d %%" % round(self.scale * 100)))

    def _to_img(self, x, y):
        iw, ih = self.img.size
        return (max(0.0, min(iw, (x - self.ox) / self.scale)), max(0.0, min(ih, (y - self.oy) / self.scale)))

    def _to_disp(self, x, y):
        return (self.ox + x * self.scale, self.oy + y * self.scale)

    # ---- drawing ----
    def _press(self, e):
        if self.photo is None:
            return
        self.pts = [self._to_img(e.x, e.y)]
        x, y = self._to_disp(*self.pts[0])
        cv, th = self.cv, self.th
        w = max(1.0, self.width * self.scale)
        if self.tool == "pen":
            self.item = cv.create_line(x, y, x, y, fill=self.color, width=w, capstyle="round", joinstyle="round")
        elif self.tool == "marker":
            self.item = cv.create_line(x, y, x, y, fill=self.color, width=w * 3, capstyle="round",
                                       joinstyle="round", stipple="gray50")
        elif self.tool == "rect":
            self.item = cv.create_rectangle(x, y, x, y, outline=self.color, width=w)
        elif self.tool == "arrow":
            a = self._arrow_dims()
            self.item = cv.create_line(x, y, x, y, fill=self.color, width=w, arrow="last", capstyle="round",
                                       arrowshape=tuple(v * self.scale for v in a))
        elif self.tool == "crop":
            self.item = cv.create_rectangle(x, y, x, y, outline="#ffffff", width=1, dash=(6, 4))

    def _drag(self, e):
        if self.item is None:
            return
        p = self._to_img(e.x, e.y)
        if self.tool in ("pen", "marker"):
            if p != self.pts[-1]:
                self.pts.append(p)
            flat = []
            for q in self.pts:
                flat.extend(self._to_disp(*q))
            self.cv.coords(self.item, *flat)
        else:
            self.pts = [self.pts[0], p]
            x1, y1 = self._to_disp(*self.pts[0])
            x2, y2 = self._to_disp(*p)
            self.cv.coords(self.item, x1, y1, x2, y2)

    def _release(self, e):
        if self.item is None:
            return
        self._drag(e)
        self.cv.delete(self.item)
        self.item = None
        try:
            self._commit()
        except Exception:
            self.ctl.log("screenshot edit failed:\n" + traceback.format_exc())
        self.pts = []

    def _arrow_dims(self):
        w = self.width
        return (10 + 3 * w, 12 + 3.5 * w, 4 + 1.5 * w)      # Tk arrowshape: d1, d2, d3 (image pixels)

    def _push(self):
        self.undo.append(self.img)
        del self.undo[:-UNDO_MAX]
        self.img = self.img.copy()

    def _undo(self):
        if not self.undo:
            return
        self.img = self.undo.pop()
        self._render()

    def _commit(self):
        pts = self.pts
        if not pts:
            return
        rgb = _hex_rgb(self.color)
        w = self.width
        if self.tool == "crop":
            if len(pts) < 2:
                return
            x1, y1 = pts[0]
            x2, y2 = pts[1]
            box = (int(min(x1, x2)), int(min(y1, y2)), int(round(max(x1, x2))), int(round(max(y1, y2))))
            if box[2] - box[0] < 2 or box[3] - box[1] < 2:
                return
            self._push()
            self.img = self.img.crop(box)
            self._render()
            return
        self._push()
        if self.tool == "pen":
            d = ImageDraw.Draw(self.img)
            seq = [tuple(p) for p in (pts if len(pts) > 1 else pts * 2)]
            d.line(seq, fill=rgb, width=w, joint="curve")
            r = w / 2
            for (x, y) in (seq[0], seq[-1]):
                d.ellipse([x - r, y - r, x + r, y + r], fill=rgb)
        elif self.tool == "marker":
            layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            seq = [tuple(p) for p in (pts if len(pts) > 1 else pts * 2)]
            mw = w * 3
            ld.line(seq, fill=rgb + (MARKER_ALPHA,), width=mw, joint="curve")
            r = mw / 2
            for (x, y) in (seq[0], seq[-1]):
                ld.ellipse([x - r, y - r, x + r, y + r], fill=rgb + (MARKER_ALPHA,))
            self.img = Image.alpha_composite(self.img.convert("RGBA"), layer).convert("RGB")
        elif self.tool == "rect":
            if len(pts) < 2:
                return
            (x1, y1), (x2, y2) = pts
            d = ImageDraw.Draw(self.img)
            d.rectangle([min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)], outline=rgb, width=w)
        elif self.tool == "arrow":
            if len(pts) < 2:
                return
            draw_arrow(self.img, pts[0], pts[1], rgb, w, self._arrow_dims())
        self._render()

    # ---- results ----
    def copy(self):
        if self.ctl.copy_image(self.img, self.path):
            self.close()

    def save_as(self):
        init_dir = self.ctl.get("save_dir") or os.path.join(os.path.expanduser("~"), "Pictures")
        if not os.path.isdir(init_dir):
            init_dir = os.path.expanduser("~")
        name = os.path.splitext(os.path.basename(self.path))[0] if self.path else time.strftime("Screenshot_%Y-%m-%d_%H-%M-%S")
        p = filedialog.asksaveasfilename(parent=self.win, defaultextension=".png", initialdir=init_dir,
                                         initialfile=name + ".png",
                                         filetypes=[(SS("png"), "*.png"), (SS("jpg"), "*.jpg;*.jpeg")])
        if not p:
            return
        try:
            if p.lower().endswith((".jpg", ".jpeg")):
                self.img.save(p, "JPEG", quality=92)
            else:
                self.img.save(p, "PNG")
        except Exception:
            self.ctl.log("screenshot save failed:\n" + traceback.format_exc())
            return
        self.ctl.set_now("save_dir", os.path.dirname(p))
        if self.path:
            img = self.img
            self.ctl._io(lambda: self.ctl._write(self.path, img))
        self.ctl.app._notify(SS("editor_title"), SS("saved"), kind="done")

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass
        self.undo = []
        self.photo = None


def draw_arrow(img, p1, p2, rgb, width, dims):
    """Line from p1 to p2 with a filled triangular head at p2 (image coordinates)."""
    import math
    (x1, y1), (x2, y2) = p1, p2
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    d = ImageDraw.Draw(img)
    if length < 1:
        r = width
        d.ellipse([x2 - r, y2 - r, x2 + r, y2 + r], fill=rgb)
        return
    ux, uy = dx / length, dy / length
    head_len, head_w = dims[1], dims[2] * 2
    head_len = min(head_len, length)
    bx, by = x2 - ux * head_len, y2 - uy * head_len          # base centre of the head
    px, py = -uy, ux                                        # perpendicular
    d.line([(x1, y1), (bx, by)], fill=rgb, width=width)
    r = width / 2
    d.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=rgb)
    d.polygon([(x2, y2), (bx + px * head_w, by + py * head_w), (bx - px * head_w, by - py * head_w)], fill=rgb)
