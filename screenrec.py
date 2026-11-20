# -*- coding: utf-8 -*-
"""Screen recording ("Запись видео") for HDContainer.

The hotkey (Ctrl+Shift+Y by default, its own voice_sys.KeyHook instance) or the tray opens the
same frozen overlay as the screenshots: drag a region or click for the whole monitor. A panel
under the region switches computer sound / microphone / pointer / click rings (the choice is
remembered) and starts the recording. While it runs, a red pill above the clock shows the time
(click = stop) and a thin frame marks the region; both are WDA_EXCLUDEFROMCAPTURE windows, so
they are on the screen and never in the video. The same hotkey, the tray row or the pill stop it.

Files: while recording, %LOCALAPPDATA%\\HDContainer\\screenrec\\vid-<ts>.mp4 (fragmented MP4) +
vid-<ts>.wav (sound) + vid-<ts>.json (where it goes). After the stop a worker thread muxes them
into one MP4 (rec_video.mux) and moves it to the chosen folder, the Desktop by default.
Leftovers of a crash are finished at the next start, like the recorder's.

Everything UI runs on the tk thread; the hook only queues events, the worker posts callables
to self._ui_q.
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

from PIL import Image, ImageDraw, ImageFont, ImageTk

import voice_sys as vs
import rec_audio as ra
import rec_video as rv
import recorder
import screenshot

R = recorder.R                     # shared strings: folder, microphone, hotkey dialogs

# ---------------------------------------------------------------------------
#  Strings (7 languages, as in the main app)
# ---------------------------------------------------------------------------
VSTR = {
    "tab": {"en": "Screen recording", "ru": "Запись видео", "es": "Grabación de pantalla", "pt": "Gravação de tela", "de": "Bildschirmaufnahme", "fr": "Enregistrement d'écran", "zh": "录屏"},
    "menu_start": {"en": "Record video", "ru": "Записать видео", "es": "Grabar vídeo", "pt": "Gravar vídeo", "de": "Video aufnehmen", "fr": "Enregistrer une vidéo", "zh": "录制视频"},
    "menu_stop": {"en": "Stop video · %s", "ru": "Остановить видео · %s", "es": "Detener vídeo · %s", "pt": "Parar vídeo · %s", "de": "Video beenden · %s", "fr": "Arrêter la vidéo · %s", "zh": "停止录制 · %s"},
    "intro": {
        "en": "Records a part of the screen or the whole screen into an MP4 video. Press the hotkey, select a region (a click takes the whole screen), choose what to record on the panel and press “Record”. Stop with the same hotkey, from the tray menu or by clicking the indicator. The indicator and the frame around the region are not in the video.",
        "ru": "Записывает часть экрана или весь экран в видео MP4. Нажми горячую клавишу, выдели область (клик — весь экран), выбери на панели, что записывать, и нажми «Запись». Остановить — той же клавишей, из меню в трее или кликом по индикатору. Индикатор и рамка вокруг области в видео не попадают.",
        "es": "Graba una parte de la pantalla o toda la pantalla en un vídeo MP4. Pulsa el atajo, elige una zona (un clic toma toda la pantalla), elige en el panel qué grabar y pulsa «Grabar». Detén con el mismo atajo, desde el menú de la bandeja o con un clic en el indicador. El indicador y el marco de la zona no salen en el vídeo.",
        "pt": "Grava uma parte da tela ou a tela inteira num vídeo MP4. Pressione o atalho, selecione uma área (um clique pega a tela inteira), escolha no painel o que gravar e clique em “Gravar”. Pare com o mesmo atalho, pelo menu da bandeja ou clicando no indicador. O indicador e a moldura da área não aparecem no vídeo.",
        "de": "Nimmt einen Teil des Bildschirms oder den ganzen Bildschirm als MP4-Video auf. Hotkey drücken, Bereich aufziehen (Klick = ganzer Bildschirm), im Feld wählen, was aufgenommen wird, und „Aufnehmen“ drücken. Beenden mit demselben Hotkey, im Tray-Menü oder per Klick auf die Anzeige. Anzeige und Rahmen um den Bereich sind im Video nicht zu sehen.",
        "fr": "Enregistre une partie de l'écran ou tout l'écran dans une vidéo MP4. Appuie sur le raccourci, sélectionne une zone (un clic prend tout l'écran), choisis sur le panneau quoi enregistrer et clique sur « Enregistrer ». Arrête avec le même raccourci, le menu de la zone de notification ou un clic sur l'indicateur. L'indicateur et le cadre autour de la zone n'apparaissent pas dans la vidéo.",
        "zh": "把屏幕的一部分或整个屏幕录成 MP4 视频。按下快捷键，选择区域（点击即整个屏幕），在面板上选择录制内容，然后点“录制”。用同一快捷键、托盘菜单或点击指示器停止。指示器和区域边框不会出现在视频里。"},
    "hint": {"en": "Select the region to record  ·  click — whole screen  ·  Esc — cancel", "ru": "Выдели область записи  ·  клик — весь экран  ·  Esc — отмена", "es": "Elige la zona a grabar  ·  clic — toda la pantalla  ·  Esc — cancelar", "pt": "Selecione a área a gravar  ·  clique — tela inteira  ·  Esc — cancelar", "de": "Aufnahmebereich aufziehen  ·  Klick — ganzer Bildschirm  ·  Esc — abbrechen", "fr": "Sélectionne la zone à enregistrer  ·  clic — tout l'écran  ·  Esc — annuler", "zh": "选择录制区域  ·  点击 — 整个屏幕  ·  Esc — 取消"},
    "p_system": {"en": "Sound", "ru": "Звук ПК", "es": "Sonido", "pt": "Som", "de": "Ton", "fr": "Son", "zh": "系统声音"},
    "p_mic": {"en": "Microphone", "ru": "Микрофон", "es": "Micrófono", "pt": "Microfone", "de": "Mikrofon", "fr": "Micro", "zh": "麦克风"},
    "p_cursor": {"en": "Cursor", "ru": "Курсор", "es": "Cursor", "pt": "Cursor", "de": "Zeiger", "fr": "Curseur", "zh": "光标"},
    "p_clicks": {"en": "Clicks", "ru": "Клики", "es": "Clics", "pt": "Cliques", "de": "Klicks", "fr": "Clics", "zh": "点击"},
    "p_start": {"en": "Record", "ru": "Запись", "es": "Grabar", "pt": "Gravar", "de": "Aufnehmen", "fr": "Enregistrer", "zh": "录制"},
    "sec_what": {"en": "What to record", "ru": "Что записывать", "es": "Qué grabar", "pt": "O que gravar", "de": "Was aufnehmen", "fr": "Quoi enregistrer", "zh": "录制内容"},
    "src_system": {"en": "Computer sound", "ru": "Звук компьютера", "es": "Sonido del ordenador", "pt": "Som do computador", "de": "Computerton", "fr": "Son de l'ordinateur", "zh": "电脑声音"},
    "src_mic": {"en": "Microphone", "ru": "Микрофон", "es": "Micrófono", "pt": "Microfone", "de": "Mikrofon", "fr": "Micro", "zh": "麦克风"},
    "src_cursor": {"en": "Mouse pointer", "ru": "Курсор мыши", "es": "Puntero del ratón", "pt": "Ponteiro do mouse", "de": "Mauszeiger", "fr": "Pointeur de la souris", "zh": "鼠标指针"},
    "src_clicks": {"en": "Highlight clicks with a ring", "ru": "Подсвечивать клики кольцом", "es": "Resaltar los clics con un anillo", "pt": "Destacar os cliques com um anel", "de": "Klicks mit einem Ring hervorheben", "fr": "Mettre en évidence les clics par un anneau", "zh": "用圆环标出点击"},
    "what_note": {"en": "The same switches are on the panel under the selected region; the last choice is remembered.", "ru": "Те же переключатели есть на панели под выделенной областью; последний выбор запоминается.", "es": "Los mismos interruptores están en el panel bajo la zona elegida; se recuerda la última elección.", "pt": "Os mesmos botões estão no painel sob a área selecionada; a última escolha é lembrada.", "de": "Dieselben Schalter sind im Feld unter dem Bereich; die letzte Wahl wird gemerkt.", "fr": "Les mêmes interrupteurs sont sur le panneau sous la zone ; le dernier choix est mémorisé.", "zh": "所选区域下方的面板上有同样的开关；会记住上次的选择。"},
    "sec_video": {"en": "Video", "ru": "Видео", "es": "Vídeo", "pt": "Vídeo", "de": "Video", "fr": "Vidéo", "zh": "视频"},
    "fps": {"en": "Frames per second", "ru": "Кадров в секунду", "es": "Fotogramas por segundo", "pt": "Quadros por segundo", "de": "Bilder pro Sekunde", "fr": "Images par seconde", "zh": "每秒帧数"},
    "quality": {"en": "Quality", "ru": "Качество", "es": "Calidad", "pt": "Qualidade", "de": "Qualität", "fr": "Qualité", "zh": "画质"},
    "q_normal": {"en": "Normal", "ru": "Обычное", "es": "Normal", "pt": "Normal", "de": "Normal", "fr": "Normale", "zh": "标准"},
    "q_high": {"en": "High", "ru": "Высокое", "es": "Alta", "pt": "Alta", "de": "Hoch", "fr": "Haute", "zh": "高"},
    "video_note": {"en": "60 frames — for games and fast motion; on a large region the computer may manage fewer.", "ru": "60 кадров — для игр и быстрого движения; на большой области компьютер может успевать меньше.", "es": "60 fotogramas: para juegos y movimiento rápido; en una zona grande el ordenador puede lograr menos.", "pt": "60 quadros — para jogos e movimento rápido; numa área grande o computador pode conseguir menos.", "de": "60 Bilder — für Spiele und schnelle Bewegung; bei einem großen Bereich schafft der Computer evtl. weniger.", "fr": "60 images — pour les jeux et les mouvements rapides ; sur une grande zone l'ordinateur peut en faire moins.", "zh": "60 帧适合游戏和快速画面；区域较大时电脑可能达不到。"},
    "sec_during": {"en": "While recording", "ru": "Во время записи", "es": "Durante la grabación", "pt": "Durante a gravação", "de": "Während der Aufnahme", "fr": "Pendant l'enregistrement", "zh": "录制时"},
    "show_ind": {"en": "Show the indicator above the clock — time and a stop button", "ru": "Показывать индикатор над часами — время и кнопка «стоп»", "es": "Mostrar el indicador sobre el reloj: tiempo y botón de parada", "pt": "Mostrar o indicador acima do relógio — tempo e botão de parar", "de": "Anzeige über der Uhr zeigen — Zeit und Stopp-Knopf", "fr": "Afficher l'indicateur au-dessus de l'horloge — durée et bouton d'arrêt", "zh": "在时钟上方显示指示器——时长和停止按钮"},
    "show_frame": {"en": "Show a frame around the region", "ru": "Показывать рамку вокруг области", "es": "Mostrar un marco alrededor de la zona", "pt": "Mostrar uma moldura em volta da área", "de": "Rahmen um den Bereich zeigen", "fr": "Afficher un cadre autour de la zone", "zh": "在区域周围显示边框"},
    "during_note": {"en": "Neither of them gets into the video.", "ru": "Ни то, ни другое в видео не попадает.", "es": "Ninguno de los dos sale en el vídeo.", "pt": "Nenhum dos dois aparece no vídeo.", "de": "Beides ist im Video nicht zu sehen.", "fr": "Ni l'un ni l'autre n'apparaît dans la vidéo.", "zh": "两者都不会出现在视频里。"},
    "hk_note": {"en": "The same keys stop the recording.", "ru": "Те же клавиши останавливают запись.", "es": "Las mismas teclas detienen la grabación.", "pt": "As mesmas teclas param a gravação.", "de": "Dieselben Tasten beenden die Aufnahme.", "fr": "Les mêmes touches arrêtent l'enregistrement.", "zh": "同一组按键停止录制。"},
    "file": {"en": "Video", "ru": "Видео", "es": "Vídeo", "pt": "Vídeo", "de": "Video", "fr": "Vidéo", "zh": "视频"},
    "saved": {"en": "Video saved", "ru": "Видео сохранено", "es": "Vídeo guardado", "pt": "Vídeo salvo", "de": "Video gespeichert", "fr": "Vidéo enregistrée", "zh": "视频已保存"},
    "recovered": {"en": "Video recovered after a crash", "ru": "Восстановлено видео после сбоя", "es": "Vídeo recuperado tras un fallo", "pt": "Vídeo recuperado após uma falha", "de": "Video nach Absturz wiederhergestellt", "fr": "Vidéo récupérée après un plantage", "zh": "已恢复崩溃前的视频"},
    "err_start": {"en": "Could not start the video recording", "ru": "Не удалось начать запись видео", "es": "No se pudo empezar a grabar el vídeo", "pt": "Não foi possível começar a gravar o vídeo", "de": "Videoaufnahme konnte nicht starten", "fr": "Impossible de démarrer l'enregistrement vidéo", "zh": "无法开始录制视频"},
    "err_save": {"en": "Could not save the video", "ru": "Не удалось сохранить видео", "es": "No se pudo guardar el vídeo", "pt": "Não foi possível salvar o vídeo", "de": "Video konnte nicht gespeichert werden", "fr": "Impossible d'enregistrer la vidéo", "zh": "无法保存视频"},
    "err_raw": {"en": "Saved without processing: the sound is a separate file", "ru": "Сохранено без обработки: звук — отдельным файлом", "es": "Guardado sin procesar: el sonido va en un archivo aparte", "pt": "Salvo sem processamento: o som está num arquivo separado", "de": "Unbearbeitet gespeichert: der Ton ist eine eigene Datei", "fr": "Enregistré sans traitement : le son est un fichier à part", "zh": "未经处理保存：声音是单独的文件"},
    "err_sound": {"en": "No sound device answered — recording the video without sound", "ru": "Звуковые устройства не ответили — пишу видео без звука", "es": "Ningún dispositivo de sonido respondió: se graba el vídeo sin sonido", "pt": "Nenhum dispositivo de som respondeu — gravando o vídeo sem som", "de": "Kein Audiogerät hat geantwortet — Video wird ohne Ton aufgenommen", "fr": "Aucun périphérique audio n'a répondu — vidéo enregistrée sans son", "zh": "没有音频设备响应——录制无声视频"},
    "err_hk": {"en": "The screen recording hotkey must be a combination (Ctrl/Alt/Shift/Win + key) or an F-key.", "ru": "Горячая клавиша записи видео должна быть сочетанием (Ctrl/Alt/Shift/Win + клавиша) или F-клавишей.", "es": "El atajo de grabación de pantalla debe ser una combinación (Ctrl/Alt/Shift/Win + tecla) o una tecla F.", "pt": "O atalho da gravação de tela deve ser uma combinação (Ctrl/Alt/Shift/Win + tecla) ou uma tecla F.", "de": "Der Hotkey der Bildschirmaufnahme muss eine Kombination (Strg/Alt/Umschalt/Win + Taste) oder eine F-Taste sein.", "fr": "Le raccourci de l'enregistrement d'écran doit être une combinaison (Ctrl/Alt/Maj/Win + touche) ou une touche F.", "zh": "录屏快捷键必须是组合键（Ctrl/Alt/Shift/Win + 键）或 F 键。"},
}

LANG_GET = lambda: "en"


def V(key, *args):
    s = VSTR.get(key, {})
    txt = s.get(LANG_GET()) or s.get("en") or key
    return (txt % args) if args else txt


DEFAULTS = {
    "enabled": True,
    "hk_mods": vs.MOD_CONTROL | vs.MOD_SHIFT, "hk_vk": 0x59,     # Ctrl+Shift+Y: free in browsers and Telegram
    "system": True, "mic": False, "mic_id": "",
    "cursor": True, "clicks": False,
    "fps": 30, "quality": "normal",
    "indicator": True, "frame": True,
    "folder": "",                   # "" = the Desktop
}
FPS_CHOICES = (15, 30, 60)
QUALITY_CHOICES = ("normal", "high")
PLAIN_HOTKEYS_OK = frozenset(range(0x70, 0x88)) | {0x13, 0x91}   # F1-F24, Pause, ScrollLock
START_CHECK_S = 2.5
START_GIVE_UP_S = 10                # no first frame by then: the start failed
START_DELAY_MS = 150                # the overlay leaves the screen before the first frame
fmt_time = recorder.fmt_time


def _theme():
    return recorder._theme()


def _dpi():
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def _font(names, size):
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for n in names:
        try:
            return ImageFont.truetype(os.path.join(windir, "Fonts", n), size)
        except Exception:
            continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
#  Frame around the recorded region: four thin strips just OUTSIDE it, left out of capture
# ---------------------------------------------------------------------------
u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.GetAncestor.restype = ctypes.c_void_p
u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
u32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
u32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
u32.SetWindowDisplayAffinity.restype = wintypes.BOOL
u32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, wintypes.DWORD]
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80, 0x08000000
WDA_EXCLUDEFROMCAPTURE = 0x11
FRAME_COLOR = "#ff453a"


class RegionFrame:
    def __init__(self, root, rect, log):
        self.tops = []
        x, y, w, h = rect
        t = max(2, int(round(2 * _dpi())))
        ml, mt, mr, mb = screenshot.monitor_at(x + w // 2, y + h // 2)
        for sx, sy, sw, sh in ((x - t, y - t, w + 2 * t, t), (x - t, y + h, w + 2 * t, t),
                               (x - t, y, t, h), (x + w, y, t, h)):
            cx, cy = sx + sw // 2, sy + sh // 2
            if not (ml <= cx < mr and mt <= cy < mb):       # off this monitor: a whole-screen region
                continue
            top = tk.Toplevel(root)
            top.overrideredirect(True)
            top.attributes("-alpha", 0.0)
            top.attributes("-topmost", True)
            top.configure(bg=FRAME_COLOR)
            top.geometry("%dx%d+%d+%d" % (sw, sh, sx, sy))
            top.update_idletasks()
            hwnd = u32.GetAncestor(top.winfo_id(), 2)
            u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
                                  | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
            if not u32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
                log("screenrec: frame stays visible to capture (Windows older than 10 2004?)")
            top.attributes("-alpha", 0.85)
            self.tops.append(top)

    def destroy(self):
        for t in self.tops:
            try:
                t.destroy()
            except Exception:
                pass
        self.tops = []


# ---------------------------------------------------------------------------
#  Region picker: the screenshot overlay + a panel of switches under the region
# ---------------------------------------------------------------------------
PANEL_BG = (27, 27, 29)
PANEL_EDGE = (58, 59, 63)
CHIP_ON = (44, 62, 96)
CHIP_HOVER = (48, 50, 54)
TXT = (230, 232, 234)
TXT_DIM = (130, 135, 142)
ACCENT = (98, 155, 255)
REC = (255, 69, 58)
GLYPHS = {"system": chr(0xE767), "mic": chr(0xE720), "cursor": chr(0xE8B0), "clicks": chr(0xE7C9),
          "start": chr(0xE7C8), "cancel": chr(0xE711)}
TOGGLES = ("system", "mic", "cursor", "clicks")


class Picker(screenshot.Capture):
    SS = 3

    def __init__(self, ctl, img, origin):
        self.sel = None                 # chosen region (canvas coords) while the panel is shown
        self.panel_item = None
        self.panel_photo = None
        self.panel_xy = (0, 0)
        self.buttons = []               # (key, (x1, y1, x2, y2)) in canvas coords
        self.hover = None
        self.pressed = None
        s = _dpi()
        self.u = s
        k = self.SS
        self.f_txt = _font(["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"], int(13 * s * k))
        self.f_ico = _font(["segmdl2.ttf", "SegoeIcons.ttf"], int(15 * s * k))
        super().__init__(ctl, img, origin)

    def hint_text(self):
        return V("hint")

    # ---- pointer ----
    def _hit(self, e):
        for key, (a, b, c, d) in self.buttons:
            if a <= e.x < c and b <= e.y < d:
                return key
        return None

    def _motion(self, e):
        super()._motion(e)
        if self.sel is not None:
            h = self._hit(e)
            if h != self.hover:
                self.hover = h
                self._draw_panel()

    def _press(self, e):
        if self.sel is not None:
            hit = self._hit(e)
            if hit:
                self.pressed = hit
                return
            self._clear_sel()
        super()._press(e)

    def _release(self, e):
        if self.pressed is not None:
            key, self.pressed = self.pressed, None
            if self._hit(e) == key:
                self._act(key)
            return
        if self.start is None or self.done:
            return
        self.cur = (e.x, e.y)
        x1, y1, x2, y2 = self._box()
        if x2 - x1 < screenshot.MIN_DRAG and y2 - y1 < screenshot.MIN_DRAG:
            ml, mt, mr, mb = screenshot.monitor_at(e.x + self.ox, e.y + self.oy)
            w, h = self.img.size
            x1, y1 = max(0, ml - self.ox), max(0, mt - self.oy)
            x2, y2 = min(w, mr - self.ox), min(h, mb - self.oy)
        if x2 - x1 < 2 or y2 - y1 < 2:
            self.cancel()
            return
        self.start, self.cur = (x1, y1), (x2, y2)
        self._pending = False
        self._redraw()
        self.start = None
        self.sel = (x1, y1, x2, y2)
        self.hover = None
        self._draw_panel()

    def _clear_sel(self):
        self.sel = None
        self.buttons = []
        if self.panel_item is not None:
            self.cv.delete(self.panel_item)
            self.panel_item = None
        self.panel_photo = None

    def _act(self, key):
        if key in TOGGLES:
            self.ctl.set_now(key, not self.ctl.get(key))
            self._draw_panel()
        elif key == "start":
            self.start_recording()
        elif key == "cancel":
            self.cancel()

    def start_recording(self):
        """Start with the selected region (also the hotkey pressed again while the panel is up)."""
        if self.sel is None:
            self.cancel()
            return
        x1, y1, x2, y2 = self.sel
        rect = (x1 + self.ox, y1 + self.oy, x2 - x1, y2 - y1)
        self.close()
        self.ctl._on_pick(rect)

    # ---- the panel (a Pillow picture, so its corners are smooth) ----
    def _layout(self, d):
        u, k = self.u, self.SS
        items = []
        for key in TOGGLES:
            items.append((key, V("p_" + key)))
        items.append(("|", ""))
        items.append(("start", V("p_start")))
        items.append(("cancel", ""))
        pad, gap, ch = 6 * u, 4 * u, 36 * u
        x = pad
        out = []
        for key, label in items:
            if key == "|":
                out.append((key, label, x + 4 * u, x + 5 * u))
                x += 9 * u + gap
                continue
            if label:
                tw = d.textlength(label, font=self.f_txt) / k
                w = 12 * u + 18 * u + 8 * u + tw + 14 * u
            else:
                w = ch
            out.append((key, label, x, x + w))
            x += w + gap
        return out, x - gap + pad, ch + 2 * pad, pad

    def _draw_panel(self):
        if self.sel is None or self.done:
            return
        u, k = self.u, self.SS
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        items, pw, ph, pad = self._layout(probe)
        pw, ph = int(pw), int(ph)
        # place: under the region, else above it, else inside at its bottom; within its monitor
        x1, y1, x2, y2 = self.sel
        ml, mt, mr, mb = screenshot.monitor_at((x1 + x2) // 2 + self.ox, (y1 + y2) // 2 + self.oy)
        ml, mt, mr, mb = ml - self.ox, mt - self.oy, mr - self.ox, mb - self.oy
        m = int(12 * u)
        if y2 + m + ph <= mb - m:
            py = y2 + m
        elif y1 - m - ph >= mt + m:
            py = y1 - m - ph
        else:
            py = y2 - m - ph
        px = int((x1 + x2) / 2 - pw / 2)
        px = max(ml + m, min(mr - m - pw, px))
        # background: the frozen screen under the panel (dark, bright inside the selection)
        bg = self.img.crop((px, py, px + pw, py + ph)).point(lambda v: v * screenshot.DARK // 100)
        ix1, iy1, ix2, iy2 = max(px, x1), max(py, y1), min(px + pw, x2), min(py + ph, y2)
        if ix2 > ix1 and iy2 > iy1:
            bg.paste(self.img.crop((ix1, iy1, ix2, iy2)), (ix1 - px, iy1 - py))
        img = Image.new("RGBA", (pw * k, ph * k), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        dr.rounded_rectangle((0, 0, pw * k - 1, ph * k - 1), radius=int(12 * u * k), fill=PANEL_BG + (255,),
                             outline=PANEL_EDGE + (255,), width=max(1, int(u * k)))
        self.buttons = []
        cy = ph * k / 2
        top, bot = pad * k, (ph - pad) * k
        for key, label, a, b in items:
            if key == "|":
                dr.rectangle((a * k, (ph * 0.28) * k, b * k, (ph * 0.72) * k), fill=PANEL_EDGE + (255,))
                continue
            hov = key == self.hover
            on = self.ctl.get(key) if key in TOGGLES else True
            if key == "start":
                fill = (255, 96, 86) if hov else REC
                fg_i, fg_t = (255, 255, 255), (255, 255, 255)
            elif key == "cancel":
                fill = CHIP_HOVER if hov else None
                fg_i, fg_t = TXT if hov else TXT_DIM, TXT
            else:
                fill = (CHIP_ON if on else None)
                if hov:
                    fill = tuple(min(255, c + 12) for c in (fill or CHIP_HOVER))
                fg_i, fg_t = (ACCENT if on else TXT_DIM), (TXT if on else TXT_DIM)
            if fill:
                dr.rounded_rectangle((a * k, top, b * k, bot), radius=int(9 * u * k), fill=fill + (255,))
            if label:
                gx = a * k + 12 * u * k
                dr.text((gx, cy), GLYPHS[key], font=self.f_ico, fill=fg_i + (255,), anchor="lm")
                dr.text((gx + 26 * u * k, cy), label, font=self.f_txt, fill=fg_t + (255,), anchor="lm")
                if key in TOGGLES and not on:           # struck through: clearly "off"
                    gy = cy
                    dr.line((gx - 1 * u * k, gy + 8 * u * k, gx + 17 * u * k, gy - 8 * u * k),
                            fill=fg_i + (255,), width=max(1, int(1.6 * u * k)))
            else:
                dr.text(((a + b) / 2 * k, cy), GLYPHS[key], font=self.f_ico, fill=fg_i + (255,), anchor="mm")
            self.buttons.append((key, (px + int(a), py + int(pad), px + int(b), py + int(ph - pad))))
        img = img.resize((pw, ph), Image.LANCZOS)
        bg = bg.convert("RGBA")
        bg.alpha_composite(img)
        self.panel_img = bg.convert("RGB")
        self.panel_photo = ImageTk.PhotoImage(self.panel_img)
        if self.panel_item is None:
            self.panel_item = self.cv.create_image(px, py, image=self.panel_photo, anchor="nw")
        else:
            self.cv.itemconfigure(self.panel_item, image=self.panel_photo)
            self.cv.coords(self.panel_item, px, py)
        self.cv.tag_raise(self.panel_item)


# ---------------------------------------------------------------------------
#  Controller: hotkey, sessions, saving, settings tab, tray entry
# ---------------------------------------------------------------------------
class ScreenRecController:
    def __init__(self, app, get_lang, log, local_dir):
        global LANG_GET
        LANG_GET = get_lang
        self.app, self.root, self.log = app, app.root, log
        self.dir = os.path.join(local_dir, "screenrec")
        os.makedirs(self.dir, exist_ok=True)
        self.draft = None
        self._tab = None
        self._capture_dlg = None
        self.capture = None             # Picker overlay while it is on screen (name kept for Capture)
        self.session = None
        self.meta = None
        self.frame = None
        self._shown = False             # pill + frame are up (after the first frame)
        self._jobs_left = 0
        self._job_q = queue.Queue()
        self._ui_q = queue.Queue()
        self.ind = recorder.Indicator(self.root, self.stop, log, cls="HDContainerVideoIndicator",
                                      exclude=True, shift=self._ind_shift)
        threading.Thread(target=self._worker, daemon=True, name="hdc-screenrec-save").start()
        self.hook = vs.KeyHook(log)
        self.hook.start()
        self.register_hotkey()
        self.root.after(25, self._loop)
        self._job_q.put(("recover",))

    # ---- settings (draft while the Settings window is open, see voice.py) ----
    def get(self, k):
        src = self.draft if self.draft is not None else self.app.settings.get("screenrec", {})
        return src.get(k, DEFAULTS[k])

    def saved(self, k):
        return self.app.settings.get("screenrec", {}).get(k, DEFAULTS[k])

    def set(self, k, v):
        if self.draft is not None:
            self.draft[k] = v
            return
        self.set_now(k, v)

    def set_now(self, k, v):
        """Straight into the saved settings (the overlay's switches), and into an open draft."""
        d = dict(self.app.settings.get("screenrec", {}))
        d[k] = v
        self.app._set_setting("screenrec", d)
        if self.draft is not None:
            self.draft[k] = v

    def begin_edit(self):
        self.draft = dict(self.app.settings.get("screenrec", {}))

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
        self.app._set_setting("screenrec", new)
        self.register_hotkey()
        self.log("screenrec settings saved")

    # ---- hotkey ----
    def register_hotkey(self):
        self.hook.configure(self.saved("hk_mods"), self.saved("hk_vk"), self.saved("enabled"))
        self.hook.mask_taps = False
        self.log("screenrec hotkey %s hook=%s" % (self.hotkey_text(saved=True), "ok" if self.hook._hook else "FAILED"))

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
                elif ev[0] == "esc":
                    if self.capture is not None:
                        self.capture.cancel()
                elif ev[0] in ("captured", "capture_cancel"):
                    self._capture_done(ev)
            while True:
                try:
                    fn = self._ui_q.get_nowait()
                except queue.Empty:
                    break
                fn()
            s = self.session
            if s is not None and not self._shown and s.t0 is not None:
                self._show_live(s)
            if s is not None and not s.thread.is_alive():      # the capture died: keep what it wrote
                self.log("screenrec: capture thread ended by itself")
                self.stop()
        except Exception:
            self.log("screenrec loop:\n" + traceback.format_exc())
        self.root.after(25, self._loop)

    # ---- recording ----
    @property
    def recording(self):
        return self.session is not None

    def toggle(self):
        if self.session is not None:
            self.stop()
        elif self.capture is not None:
            self.capture.start_recording()
        else:
            self.pick()

    def pick(self):
        """Hotkey / tray: freeze the screen and let the user pick the region and the switches."""
        if self.capture is not None or self.session is not None:
            return
        sh = getattr(self.app, "screenshot", None)
        if sh is not None and sh.capture is not None:       # the screenshot overlay is up
            return
        try:
            img, origin = screenshot.grab_screen()
        except Exception:
            self.log("screenrec grab failed:\n" + traceback.format_exc())
            self.app._notify(V("tab"), V("err_start"), kind="error")
            return
        try:
            self.capture = Picker(self, img, origin)
        except Exception:
            self.capture = None
            self.log("screenrec overlay failed:\n" + traceback.format_exc())
            self.app._notify(V("tab"), V("err_start"), kind="error")

    def _on_cancel(self):
        self.capture = None

    def _on_region(self, img, quiet=False):     # Capture's own end, never reached by Picker
        self.capture = None

    def _on_pick(self, rect):
        self.capture = None
        self.root.after(START_DELAY_MS, lambda: self.start(rect))

    def _ind_shift(self):
        """Logical px to move left: the recorder's own pill sits where ours would."""
        rec = getattr(self.app, "recorder", None)
        ind = getattr(rec, "ind", None)
        if ind is not None and ind.state is not None and ind._pill_w:
            return ind._pill_w / max(0.5, ind.s) + 8
        return 0

    def _write_meta(self, meta):
        with open(meta["side"], "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)

    def start(self, rect):
        if self.session is not None:
            return
        x, y, w, h = rect
        rect = (x, y, w - w % 2, h - h % 2)
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        stem = os.path.join(self.dir, "vid-" + stamp)
        system, mic = self.saved("system"), self.saved("mic")
        meta = {"video": stem + ".mp4", "wav": stem + ".wav" if (system or mic) else None, "side": stem + ".json",
                "folder": self.saved("folder"), "name": "%s %s" % (V("file"), time.strftime("%Y-%m-%d %H-%M")),
                "started": time.time(), "offset": 0}
        try:
            self._write_meta(meta)
            self.session = rv.VideoSession(rect, meta["video"], meta["wav"], self.log, fps=self.saved("fps"),
                                           quality=self.saved("quality"), cursor=self.saved("cursor"),
                                           clicks=self.saved("clicks"), system=system, mic=mic,
                                           mic_id=self.saved("mic_id"))
        except Exception:
            self.log("screenrec start failed:\n" + traceback.format_exc())
            self.session = None
            self.app._notify(V("tab"), V("err_start"), kind="error")
            self._job_q.put(("drop", meta))
            return
        self.meta = meta
        self._shown = False
        self.log("screenrec: started -> %s" % meta["video"])
        self.root.after(int(START_CHECK_S * 1000), lambda s=self.session: self._start_check(s))

    def _show_live(self, sess):
        """The first frame is in (the encoder needs ~0.5 s to start): only now show the pill and
        the frame, so they mean "recording" and the timer matches the video."""
        self._shown = True
        since = max(0.0, (ra.qpc_hns() - sess.t0) / 1e7)
        self.meta["started"] = time.time() - since
        if self.saved("indicator"):
            self.ind.show_rec(self.meta["started"], sess.levels if sess.audio is not None else None)
        if self.saved("frame"):
            try:
                self.frame = RegionFrame(self.root, sess.rect, self.log)
            except Exception:
                self.log("screenrec frame failed:\n" + traceback.format_exc())

    def _start_check(self, sess, waited=START_CHECK_S):
        if sess is not self.session:
            return
        if not sess.frames and sess.error is None and sess.thread.is_alive() and waited < START_GIVE_UP_S:
            self.root.after(1000, lambda: self._start_check(sess, waited + 1))    # a slow encoder start
            return
        if sess.error is not None or not sess.frames:
            self.log("screenrec: no frames after %.1f s, stopping" % waited)
            meta, self.session, self.meta = self.meta, None, None
            sess.stop(wait=False)
            self._hide_frame()
            self.ind.hide()
            self._job_q.put(("drop", meta, sess))
            self.app._notify(V("tab"), V("err_start"), kind="error")
            return
        self.meta["offset"] = sess.audio_offset
        try:
            self._write_meta(self.meta)
        except Exception:
            self.log("screenrec sidecar:\n" + traceback.format_exc())
        a = sess.audio
        if a is not None:
            srcs = [s for s in list(a.out_srcs.values()) if s.alive]
            if a.mic_src is not None and a.mic_src.alive:
                srcs.append(a.mic_src)
            if not srcs:
                self.app._notify(V("tab"), V("err_sound"), kind="error")
            elif a.mic and (a.mic_src is None or not a.mic_src.alive):
                self.app._notify(V("tab"), R("err_mic"), kind="error")

    def _hide_frame(self):
        if self.frame is not None:
            self.frame.destroy()
            self.frame = None

    def stop(self):
        sess, meta = self.session, self.meta
        if sess is None:
            return
        self.session = self.meta = None
        sess.stop(wait=False)
        self._hide_frame()
        self.log("screenrec: stopped after %s" % fmt_time(time.time() - meta["started"]))
        self._jobs_left += 1
        if self.saved("indicator"):
            self.ind.show_saving(0.0)
        else:
            self.ind.hide()
        self._job_q.put(("save", meta, sess))

    def menu_text(self):
        if self.session is not None:
            return V("menu_stop", fmt_time(time.time() - self.meta["started"]))
        return V("menu_start")

    # ---- saving (worker thread) ----
    def _post(self, fn):
        self._ui_q.put(fn)

    def _worker(self):
        while True:
            job = self._job_q.get()
            try:
                if job[0] == "save":
                    job[2].thread.join()
                    job[1]["offset"] = job[2].audio_offset
                    self._save(job[1], recovered=False)
                elif job[0] == "drop":
                    if len(job) > 2:
                        job[2].thread.join()
                    self._remove_tmp(job[1])
                elif job[0] == "recover":
                    self._recover()
            except Exception:
                self.log("screenrec worker:\n" + traceback.format_exc())

    def _remove_tmp(self, meta):
        stem = os.path.splitext(meta["video"])[0]
        for p in (meta["video"], meta.get("wav"), stem + ".json", stem + ".out.mp4"):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    def _dest_dir(self, folder):
        for d in (folder, recorder.desktop_dir(), self.dir):
            if d and os.path.isdir(d):
                return d
        return self.dir

    def _unique(self, folder, name, ext):
        p = os.path.join(folder, name + ext)
        i = 2
        while os.path.exists(p):
            p = os.path.join(folder, "%s (%d)%s" % (name, i, ext))
            i += 1
        return p

    def _save(self, meta, recovered):
        video, wav = meta["video"], meta.get("wav")
        if wav and not os.path.exists(wav):
            wav = None
        stem = os.path.splitext(video)[0]
        dest_dir = self._dest_dir(meta.get("folder", ""))
        name = meta.get("name") or os.path.basename(stem)
        if not os.path.exists(video) or os.path.getsize(video) < 1024:
            self.log("screenrec: nothing recorded in %s" % video)
            self._remove_tmp(meta)
            self._post(lambda: self._saved(None, V("err_save"), recovered))
            return
        tmp = stem + ".out.mp4"
        warn = None
        try:
            last = [0.0]

            def prog(p):
                if p - last[0] >= 0.02:
                    last[0] = p
                    self._post(lambda p=p: self._progress(p))
            total = int(max(1.0, time.time() - meta.get("started", time.time())) * 1e7)
            rv.mux(video, wav, int(meta.get("offset") or 0), tmp, total_hns=total, progress=prog)
            out = self._unique(dest_dir, name, ".mp4")
            shutil.move(tmp, out)
        except Exception:
            self.log("screenrec mux failed:\n" + traceback.format_exc())
            try:                        # keep the raw files rather than lose the recording
                out = self._unique(dest_dir, name, ".mp4")
                shutil.move(video, out)
                if wav:
                    shutil.move(wav, self._unique(dest_dir, name, ".wav"))
                warn = V("err_raw")
            except Exception:
                self.log("screenrec raw save failed:\n" + traceback.format_exc())
                self._post(lambda: self._saved(None, V("err_save"), recovered))
                return
        self._remove_tmp(meta)
        self.log("screenrec: saved %s" % out)
        self._post(lambda: self._saved(out, warn, recovered))

    def _recover(self):
        busy = self.meta["video"] if self.meta else None
        for n in sorted(os.listdir(self.dir)):
            if not n.endswith(".json"):
                continue
            side = os.path.join(self.dir, n)
            try:
                with open(side, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                continue
            meta["video"] = meta.get("video") or os.path.splitext(side)[0] + ".mp4"
            meta["side"] = side
            if meta["video"] == busy:
                continue
            if meta.get("wav") and os.path.exists(meta["wav"]):
                try:
                    ra.repair_wav(meta["wav"])
                except Exception:
                    self.log("screenrec wav repair:\n" + traceback.format_exc())
            if not os.path.exists(meta["video"]) or os.path.getsize(meta["video"]) < 1024:
                self._remove_tmp(meta)
                continue
            self.log("screenrec: recovering %s" % meta["video"])
            meta["started"] = os.path.getmtime(meta["video"]) - 1      # progress only
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
            self.app._notify(V("tab"), warn or V("err_save"), kind="error")
            return
        title = V("recovered") if recovered else V("saved")
        self.app._notify(title, (warn + " · " if warn else "") + os.path.basename(path),
                         on_click=lambda p=path: self._reveal(p), kind="error" if warn else "done")

    def _reveal(self, path):
        try:
            subprocess.Popen(["explorer.exe", "/select,", path], close_fds=True)
        except Exception:
            self.log("screenrec reveal failed:\n" + traceback.format_exc())

    def shutdown(self):
        """App quit: a running recording is finished at the next start (recovery)."""
        try:
            self.hook.stop()
        except Exception:
            pass
        if self.capture is not None:
            try:
                self.capture.close()
            except Exception:
                pass
            self.capture = None
        sess, self.session = self.session, None
        if sess is not None:
            sess.stop(wait=True)
        self._hide_frame()
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

        def pills(label, options, key):
            row = tk.Frame(parent, bg=th["sf"])
            row.pack(fill="x", padx=pad, pady=(2, 6))
            tk.Label(row, text=label, bg=th["sf"], fg=th["dim"], font=th["font_sm"], width=18, anchor="w").pack(
                side="left")
            cur = self.get(key)
            for val, text in options:
                on = val == cur
                p = tk.Label(row, text=text, bg=th["ac"] if on else th["sf2"], fg="white" if on else th["tx"],
                             font=th["font_sm"], padx=12, pady=4, cursor="hand2")
                p.pack(side="left", padx=(0, 6))
                p.bind("<Button-1>", lambda _e, v=val: (self.set(key, v), self._rebuild()))

        note(V("intro"), pady=(8, 6))

        header(V("sec_what"))
        check(V("src_system"), "system")
        check(V("src_mic"), "mic", lambda v: self._rebuild())
        if self.get("mic"):
            row = tk.Frame(parent, bg=th["sf"])
            row.pack(fill="x", padx=pad + 34, pady=(0, 4))
            btn = a._ghost_btn(row, self._mic_label() + "   ⌄", lambda: self._mic_menu(btn))
            btn.pack(side="left")
        check(V("src_cursor"), "cursor")
        check(V("src_clicks"), "clicks")
        note(V("what_note"), pady=(4, 2))

        header(V("sec_video"))
        pills(V("fps"), [(f, str(f)) for f in FPS_CHOICES], "fps")
        pills(V("quality"), [(q, V("q_" + q)) for q in QUALITY_CHOICES], "quality")
        note(V("video_note"), pady=(2, 2))

        header(V("sec_during"))
        check(V("show_ind"), "indicator")
        check(V("show_frame"), "frame")
        note(V("during_note"), pady=(0, 2), indent=34)

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
        tk.Frame(parent, bg=th["sf"], height=16).pack()

    def _mics(self):
        try:
            return ra.list_devices(ra.E_CAPTURE)
        except Exception:
            self.log("screenrec mic list:\n" + traceback.format_exc())
            return []

    def _mic_label(self):
        mid = self.get("mic_id")
        if mid:
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
            self.log("screenrec open folder:\n" + traceback.format_exc())

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
            self.log("screenrec tab rebuild:\n" + traceback.format_exc())

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
        for attr, label in (("screenshot", lambda: screenshot.SS("tab")), ("recorder", lambda: R("tab"))):
            m = getattr(self.app, attr, None)
            if m is not None and m.get("enabled") and (m.get("hk_mods"), m.get("hk_vk")) == (mods, vk):
                return label()
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
                msg = V("err_hk")
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
