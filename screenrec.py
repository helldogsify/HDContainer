# -*- coding: utf-8 -*-
"""Screen recording ("Запись видео") for HDContainer.

The hotkey (Ctrl+Shift+B by default, its own voice_sys.KeyHook instance) or the tray opens the
same frozen overlay as the screenshots: drag a region or click for the whole monitor. A panel
under the region switches computer sound / microphone / pointer / click rings (the choice is
remembered) and starts the recording. The panel stays on the screen while it runs (user ruling
2026-11-20: switches available during the recording, the time on the panel instead of a pill in
the corner, draggable by a dotted grip), and a thin frame marks the region; both are
WDA_EXCLUDEFROMCAPTURE windows, so they are on the screen and never in the video. The same
hotkey, the tray row or Stop on the panel end it.

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
        "en": "Records a part of the screen or the whole screen into an MP4 video. Press the hotkey, select a region (a click takes the whole screen), choose what to record on the panel and press “Record”. The panel stays during the recording: switches work at once, it shows the time and stops with “Stop”; drag it by the dots. Stop also with the same hotkey or from the tray menu. The panel and the frame around the region are not in the video.",
        "ru": "Записывает часть экрана или весь экран в видео MP4. Нажми горячую клавишу, выдели область (клик — весь экран), выбери на панели, что записывать, и нажми «Запись». Панель остаётся на время записи: переключатели работают сразу, на ней идёт время и есть «Стоп»; перетаскивается за точки. Остановить можно и той же клавишей или из меню в трее. Панель и рамка вокруг области в видео не попадают.",
        "es": "Graba una parte de la pantalla o toda la pantalla en un vídeo MP4. Pulsa el atajo, elige una zona (un clic toma toda la pantalla), elige en el panel qué grabar y pulsa «Grabar». El panel se queda durante la grabación: los interruptores actúan al momento, muestra el tiempo y tiene «Detener»; se arrastra por los puntos. También se detiene con el mismo atajo o desde el menú de la bandeja. El panel y el marco de la zona no salen en el vídeo.",
        "pt": "Grava uma parte da tela ou a tela inteira num vídeo MP4. Pressione o atalho, selecione uma área (um clique pega a tela inteira), escolha no painel o que gravar e clique em “Gravar”. O painel fica durante a gravação: os botões agem na hora, ele mostra o tempo e tem “Parar”; arraste-o pelos pontos. Também para com o mesmo atalho ou pelo menu da bandeja. O painel e a moldura da área não aparecem no vídeo.",
        "de": "Nimmt einen Teil des Bildschirms oder den ganzen Bildschirm als MP4-Video auf. Hotkey drücken, Bereich aufziehen (Klick = ganzer Bildschirm), im Feld wählen, was aufgenommen wird, und „Aufnehmen“ drücken. Das Feld bleibt während der Aufnahme: Schalter wirken sofort, es zeigt die Zeit und hat „Stopp“; an den Punkten verschieben. Beenden auch mit demselben Hotkey oder im Tray-Menü. Feld und Rahmen um den Bereich sind im Video nicht zu sehen.",
        "fr": "Enregistre une partie de l'écran ou tout l'écran dans une vidéo MP4. Appuie sur le raccourci, sélectionne une zone (un clic prend tout l'écran), choisis sur le panneau quoi enregistrer et clique sur « Enregistrer ». Le panneau reste pendant l'enregistrement : les interrupteurs agissent tout de suite, il affiche la durée et a « Arrêter » ; on le déplace par les points. On arrête aussi avec le même raccourci ou le menu de la zone de notification. Le panneau et le cadre autour de la zone n'apparaissent pas dans la vidéo.",
        "zh": "把屏幕的一部分或整个屏幕录成 MP4 视频。按下快捷键，选择区域（点击即整个屏幕），在面板上选择录制内容，然后点“录制”。录制期间面板一直在：开关立即生效，显示时长并有“停止”按钮；拖动圆点可移动面板。也可用同一快捷键或托盘菜单停止。面板和区域边框不会出现在视频里。"},
    "hint": {"en": "Select the region to record  ·  click — whole screen  ·  Esc — cancel", "ru": "Выдели область записи  ·  клик — весь экран  ·  Esc — отмена", "es": "Elige la zona a grabar  ·  clic — toda la pantalla  ·  Esc — cancelar", "pt": "Selecione a área a gravar  ·  clique — tela inteira  ·  Esc — cancelar", "de": "Aufnahmebereich aufziehen  ·  Klick — ganzer Bildschirm  ·  Esc — abbrechen", "fr": "Sélectionne la zone à enregistrer  ·  clic — tout l'écran  ·  Esc — annuler", "zh": "选择录制区域  ·  点击 — 整个屏幕  ·  Esc — 取消"},
    "p_system": {"en": "Sound", "ru": "Звук ПК", "es": "Sonido", "pt": "Som", "de": "Ton", "fr": "Son", "zh": "系统声音"},
    "p_mic": {"en": "Microphone", "ru": "Микрофон", "es": "Micrófono", "pt": "Microfone", "de": "Mikrofon", "fr": "Micro", "zh": "麦克风"},
    "p_cursor": {"en": "Cursor", "ru": "Курсор", "es": "Cursor", "pt": "Cursor", "de": "Zeiger", "fr": "Curseur", "zh": "光标"},
    "p_clicks": {"en": "Clicks", "ru": "Клики", "es": "Clics", "pt": "Cliques", "de": "Klicks", "fr": "Clics", "zh": "点击"},
    "p_stop": {"en": "Stop", "ru": "Стоп", "es": "Detener", "pt": "Parar", "de": "Stopp", "fr": "Arrêter", "zh": "停止"},
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
    "show_frame": {"en": "Show a frame around the region", "ru": "Показывать рамку вокруг области", "es": "Mostrar un marco alrededor de la zona", "pt": "Mostrar uma moldura em volta da área", "de": "Rahmen um den Bereich zeigen", "fr": "Afficher un cadre autour de la zone", "zh": "在区域周围显示边框"},
    "during_note": {"en": "The panel with the time and the frame are not in the video.", "ru": "Панель со временем и рамка в видео не попадают.", "es": "El panel con el tiempo y el marco no salen en el vídeo.", "pt": "O painel com o tempo e a moldura não aparecem no vídeo.", "de": "Das Feld mit der Zeit und der Rahmen sind im Video nicht zu sehen.", "fr": "Le panneau avec la durée et le cadre n'apparaissent pas dans la vidéo.", "zh": "带时长的面板和边框不会出现在视频里。"},
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
    "hk_mods": vs.MOD_CONTROL | vs.MOD_SHIFT, "hk_vk": 0x42,     # Ctrl+Shift+B: the user's choice (2026-11-20)
    "system": True, "mic": False, "mic_id": "",
    "cursor": True, "clicks": False,
    "fps": 30, "quality": "normal",
    "frame": True,
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
#  The panel of switches: one picture for both places it lives in. Before the recording it is
#  drawn on the frozen overlay under the region (the overlay gets its mouse from a hook); during
#  the recording it is a layered window of its own (LivePanel), left out of capture, with the time
#  and Stop. Both are dragged by the dotted grip left of the last button.
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
PICK_ITEMS = TOGGLES + ("|", "start", "grip", "cancel")
REC_ITEMS = TOGGLES + ("|", "time", "stop", "grip")


class PanelArt:
    """Renders the panel (supersampled, smooth corners) as RGBA + the rectangles of its parts."""
    SS = 3

    def __init__(self):
        self.u = _dpi()
        k = self.SS
        self.f_txt = _font(["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"], int(13 * self.u * k))
        self.f_time = _font(["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"], int(14 * self.u * k))
        self.f_ico = _font(["segmdl2.ttf", "SegoeIcons.ttf"], int(15 * self.u * k))
        self._probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        self._cache = (None, None)

    def _layout(self, keys, texts):
        u, k = self.u, self.SS
        pad, gap, ch = 6 * u, 4 * u, 36 * u
        x = pad
        out = []
        for key in keys:
            if key == "|":
                out.append((key, x + 4 * u, x + 5 * u))
                x += 9 * u + gap
                continue
            if key == "grip":
                w = 22 * u
            elif key == "saving":
                w = self._probe.textlength(texts[key], font=self.f_txt) / k + 44 * u
            elif key == "time":
                w = self._probe.textlength("0:00:00" if len(texts[key]) > 5 else "00:00", font=self.f_time) / k + 34 * u
            elif texts.get(key):
                w = 12 * u + 18 * u + 8 * u + self._probe.textlength(texts[key], font=self.f_txt) / k + 14 * u
            else:
                w = ch
            out.append((key, x, x + w))
            x += w + gap
        return out, int(x - gap + pad), int(ch + 2 * pad), pad

    def render(self, keys, states, hover=None, elapsed=0.0, blink=True, progress=0.0):
        """keys: PICK_ITEMS / REC_ITEMS; states: {toggle: bool}. Returns (RGBA, [(key, rect)], w, h)."""
        texts = {t: V("p_" + t) for t in TOGGLES}
        texts.update({"start": V("p_start"), "stop": V("p_stop"), "time": fmt_time(elapsed),
                      "saving": "%s %d%%" % (R("saving"), int(progress * 100))})
        spin = int(time.time() * 12) if "saving" in keys else 0
        ck = (keys, tuple(sorted(states.items())), hover, texts["time"], blink, texts["saving"], spin, LANG_GET())
        if self._cache[0] == ck:
            return self._cache[1]
        u, k = self.u, self.SS
        items, pw, ph, pad = self._layout(keys, texts)
        img = Image.new("RGBA", (pw * k, ph * k), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        dr.rounded_rectangle((0, 0, pw * k - 1, ph * k - 1), radius=int(12 * u * k), fill=PANEL_BG + (255,),
                             outline=PANEL_EDGE + (255,), width=max(1, int(u * k)))
        rects = []
        cy = ph * k / 2
        top, bot = pad * k, (ph - pad) * k
        for key, a, b in items:
            if key == "|":
                dr.rectangle((a * k, (ph * 0.28) * k, b * k, (ph * 0.72) * k), fill=PANEL_EDGE + (255,))
                continue
            hov = key == hover
            if key == "saving":                     # spinner + "Saving 42%"
                r = 7 * u * k
                cx = a * k + 14 * u * k
                ang = (time.time() * 300) % 360
                dr.arc((cx - r, cy - r, cx + r, cy + r), ang, ang + 250, fill=TXT + (255,), width=max(2, int(2.2 * u * k)))
                dr.text((cx + 14 * u * k, cy), texts["saving"], font=self.f_txt, fill=TXT + (255,), anchor="lm")
                continue
            if key == "time":                       # pulsing red dot + the running time, not a button
                r = 5 * u * k
                cx = a * k + 12 * u * k
                if blink:
                    dr.ellipse((cx - r, cy - r, cx + r, cy + r), fill=REC + (255,))
                else:
                    dr.ellipse((cx - r, cy - r, cx + r, cy + r), outline=REC + (255,), width=max(1, int(1.6 * u * k)))
                dr.text((cx + 12 * u * k, cy), texts["time"], font=self.f_time, fill=TXT + (255,), anchor="lm")
                continue
            if key == "grip":                       # 2 x 3 dots: drag the panel by them
                col = TXT if hov else TXT_DIM
                if hov:
                    dr.rounded_rectangle((a * k, top, b * k, bot), radius=int(9 * u * k), fill=CHIP_HOVER + (255,))
                r = 1.6 * u * k
                gx = (a + b) / 2 * k
                for dx in (-3.2, 3.2):
                    for dy in (-6.4, 0, 6.4):
                        x, y = gx + dx * u * k, cy + dy * u * k
                        dr.ellipse((x - r, y - r, x + r, y + r), fill=col + (255,))
                rects.append((key, (int(a), int(pad), int(b), int(ph - pad))))
                continue
            on = states.get(key, True)
            if key in ("start", "stop"):
                fill = (255, 96, 86) if hov else REC
                fg_i = fg_t = (255, 255, 255)
            elif key == "cancel":
                fill = CHIP_HOVER if hov else None
                fg_i, fg_t = TXT if hov else TXT_DIM, TXT
            else:
                fill = CHIP_ON if on else None
                if hov:
                    fill = tuple(min(255, c + 12) for c in (fill or CHIP_HOVER))
                fg_i, fg_t = (ACCENT if on else TXT_DIM), (TXT if on else TXT_DIM)
            if fill:
                dr.rounded_rectangle((a * k, top, b * k, bot), radius=int(9 * u * k), fill=fill + (255,))
            label = texts.get(key)
            if label:
                gx = a * k + 12 * u * k
                if key == "stop":                   # a filled square
                    s = 5.5 * u * k
                    c0 = gx + 8 * u * k
                    dr.rounded_rectangle((c0 - s, cy - s, c0 + s, cy + s), radius=1.8 * u * k, fill=fg_i + (255,))
                else:
                    dr.text((gx, cy), GLYPHS[key], font=self.f_ico, fill=fg_i + (255,), anchor="lm")
                dr.text((gx + 26 * u * k, cy), label, font=self.f_txt, fill=fg_t + (255,), anchor="lm")
                if key in TOGGLES and not on:       # struck through: clearly "off"
                    dr.line((gx - 1 * u * k, cy + 8 * u * k, gx + 17 * u * k, cy - 8 * u * k),
                            fill=fg_i + (255,), width=max(1, int(1.6 * u * k)))
            else:
                dr.text(((a + b) / 2 * k, cy), GLYPHS[key], font=self.f_ico, fill=fg_i + (255,), anchor="mm")
            rects.append((key, (int(a), int(pad), int(b), int(ph - pad))))
        res = (img.resize((pw, ph), Image.LANCZOS), rects, pw, ph)
        self._cache = (ck, res)
        return res


def hit_rect(rects, x, y):
    for key, (a, b, c, d) in rects:
        if a <= x < c and b <= y < d:
            return key
    return None


class Picker(screenshot.Capture):
    """The screenshot overlay with the panel under the selected region."""

    def __init__(self, ctl, img, origin):
        self.sel = None                 # chosen region (canvas coords) while the panel is shown
        self.panel_item = None
        self.panel_photo = None
        self.panel_img = None
        self.panel_xy = (0, 0)          # panel's top-left, canvas coords
        self.panel_at = None            # set once the panel was dragged: it stays there
        self.drag = None                # (dx, dy) of the pointer inside the panel while dragging
        self._drag_due = False
        self.buttons = []               # (key, (x1, y1, x2, y2)) in canvas coords
        self.hover = None
        self.pressed = None
        self.art = PanelArt()
        super().__init__(ctl, img, origin)

    def hint_text(self):
        return V("hint")

    # ---- pointer ----
    def _hit(self, e):
        return hit_rect(self.buttons, e.x, e.y)

    def _motion(self, e):
        if self.drag is not None:
            self.panel_at = (e.x - self.drag[0], e.y - self.drag[1])
            if not self._drag_due:
                self._drag_due = True
                self.cv.after(12, self._drag_draw)
            return
        super()._motion(e)
        if self.sel is not None:
            h = self._hit(e)
            if h != self.hover:
                self.hover = h
                self.cv.configure(cursor="fleur" if h == "grip" else "hand2" if h else "crosshair")
                self._draw_panel()

    def _drag_draw(self):
        self._drag_due = False
        if not self.done:
            self._draw_panel()

    def _press(self, e):
        if self.sel is not None:
            hit = self._hit(e)
            if hit == "grip":
                self.drag = (e.x - self.panel_xy[0], e.y - self.panel_xy[1])
                return
            if hit:
                self.pressed = hit
                return
            self._clear_sel()
        super()._press(e)

    def _release(self, e):
        if self.drag is not None:
            self.drag = None
            self._draw_panel()
            return
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
        self.cv.configure(cursor="crosshair")

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
        pos = (self.panel_xy[0] + self.ox, self.panel_xy[1] + self.oy)
        self.close()
        self.ctl._on_pick(rect, pos)

    # ---- the panel ----
    def _draw_panel(self):
        if self.sel is None or self.done:
            return
        fg, rects, pw, ph = self.art.render(PICK_ITEMS, {t: self.ctl.get(t) for t in TOGGLES}, self.hover)
        iw, ih = self.img.size
        if self.panel_at is not None:
            px, py = self.panel_at
        else:   # under the region, else above it, else inside at its bottom; within its monitor
            x1, y1, x2, y2 = self.sel
            ml, mt, mr, mb = screenshot.monitor_at((x1 + x2) // 2 + self.ox, (y1 + y2) // 2 + self.oy)
            ml, mt, mr, mb = ml - self.ox, mt - self.oy, mr - self.ox, mb - self.oy
            m = int(12 * self.art.u)
            if y2 + m + ph <= mb - m:
                py = y2 + m
            elif y1 - m - ph >= mt + m:
                py = y1 - m - ph
            else:
                py = y2 - m - ph
            px = max(ml + m, min(mr - m - pw, int((x1 + x2) / 2 - pw / 2)))
        px, py = max(0, min(iw - pw, int(px))), max(0, min(ih - ph, int(py)))
        self.panel_xy = (px, py)
        # background: the frozen screen under the panel (dark, bright inside the selection)
        x1, y1, x2, y2 = self.sel
        bg = self.img.crop((px, py, px + pw, py + ph)).point(lambda v: v * screenshot.DARK // 100)
        ix1, iy1, ix2, iy2 = max(px, x1), max(py, y1), min(px + pw, x2), min(py + ph, y2)
        if ix2 > ix1 and iy2 > iy1:
            bg.paste(self.img.crop((ix1, iy1, ix2, iy2)), (ix1 - px, iy1 - py))
        bg = bg.convert("RGBA")
        bg.alpha_composite(fg)
        self.buttons = [(key, (px + a, py + b, px + c, py + d)) for key, (a, b, c, d) in rects]
        self.panel_img = bg.convert("RGB")
        self.panel_photo = ImageTk.PhotoImage(self.panel_img)
        if self.panel_item is None:
            self.panel_item = self.cv.create_image(px, py, image=self.panel_photo, anchor="nw")
        else:
            self.cv.itemconfigure(self.panel_item, image=self.panel_photo)
            self.cv.coords(self.panel_item, px, py)
        self.cv.tag_raise(self.panel_item)


# ---- the live panel: a layered window during the recording ----
g32 = ctypes.WinDLL("gdi32")
k32 = ctypes.WinDLL("kernel32")
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


def _d(fn, res, args):
    fn.restype, fn.argtypes = res, args


_d(u32.SetCapture, wintypes.HWND, [wintypes.HWND])
_d(u32.ReleaseCapture, wintypes.BOOL, [])
_d(u32.GetCursorPos, wintypes.BOOL, [ctypes.POINTER(wintypes.POINT)])
_d(u32.SetCursor, wintypes.HANDLE, [wintypes.HANDLE])
_d(u32.LoadCursorW, wintypes.HANDLE, [wintypes.HINSTANCE, ctypes.c_void_p])
_d(u32.GetWindowRect, wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)])
_d(u32.SetWindowPos, wintypes.BOOL, [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT])
_d(u32.ShowWindow, wintypes.BOOL, [wintypes.HWND, ctypes.c_int])
_d(u32.DestroyWindow, wintypes.BOOL, [wintypes.HWND])
_d(u32.DefWindowProcW, LRESULT, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM])
_d(u32.RegisterClassExW, wintypes.ATOM, [ctypes.POINTER(recorder.WNDCLASSEXW)])
_d(u32.CreateWindowExW, wintypes.HWND,
   [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p])
_d(u32.GetDC, wintypes.HDC, [wintypes.HWND])
_d(u32.ReleaseDC, ctypes.c_int, [wintypes.HWND, wintypes.HDC])
_d(u32.UpdateLayeredWindow, wintypes.BOOL,
   [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(recorder.SIZE), wintypes.HDC,
    ctypes.POINTER(wintypes.POINT), wintypes.DWORD, ctypes.POINTER(recorder.BLENDFUNCTION), wintypes.DWORD])
_d(k32.GetModuleHandleW, wintypes.HINSTANCE, [wintypes.LPCWSTR])
_d(g32.CreateCompatibleDC, wintypes.HDC, [wintypes.HDC])
_d(g32.DeleteDC, wintypes.BOOL, [wintypes.HDC])
_d(g32.CreateDIBSection, wintypes.HBITMAP, [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD])
_d(g32.SelectObject, wintypes.HGDIOBJ, [wintypes.HDC, wintypes.HGDIOBJ])
_d(g32.DeleteObject, wintypes.BOOL, [wintypes.HGDIOBJ])
WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TOPMOST = 0x80000, 0x8
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x4, 0x10, 0x40
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x200, 0x201, 0x202
WM_MOUSEACTIVATE, MA_NOACTIVATE, WM_SETCURSOR, WM_CAPTURECHANGED = 0x21, 3, 0x20, 0x215
IDC_ARROW, IDC_HAND, IDC_SIZEALL = 32512, 32649, 32646
TICK_MS = 50
_PANELS = {}                        # hwnd -> LivePanel: one window class, one procedure for all of them


def _panel_proc(hwnd, msg, wp, lp):
    p = _PANELS.get(hwnd)
    if p is not None:
        return p._wndproc(hwnd, msg, wp, lp)
    if msg == WM_MOUSEACTIVATE:
        return MA_NOACTIVATE
    return u32.DefWindowProcW(hwnd, msg, wp, lp)


_PANEL_PROC = WNDPROC(_panel_proc)
_PANEL_CLASS = []


class LivePanel:
    """The panel during the recording: switches act at once, the time runs, Stop saves.
    The window procedure never touches tk: dragging is pure Win32 (capture + SetWindowPos),
    clicks are queued for the tk timer."""

    def __init__(self, root, ctl, pos, log):
        self.root, self.ctl, self.log = root, ctl, log
        self.art = PanelArt()
        self.rects = []
        self.size = (0, 0)
        self.hover = None
        self.pressed = None
        self.drag = None
        self.clicks = []
        self._dc = None
        self._key = None
        self.mode = "rec"               # "saving": the same window shows the progress after Stop
        self.progress = 0.0
        hinst = k32.GetModuleHandleW(None)
        if not _PANEL_CLASS:
            wc = recorder.WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(wc)
            wc.lpfnWndProc = _PANEL_PROC
            wc.hInstance = hinst
            wc.lpszClassName = "HDContainerVideoPanel"
            _PANEL_CLASS.append(u32.RegisterClassExW(ctypes.byref(wc)))
        self.hwnd = u32.CreateWindowExW(WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                                        "HDContainerVideoPanel", "HDContainer screen recording", WS_POPUP,
                                        0, 0, 10, 10, None, None, hinst, None)
        if not self.hwnd:
            raise OSError("panel window failed: %d" % ctypes.get_last_error())
        _PANELS[self.hwnd] = self
        if not u32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE):
            log("screenrec: panel stays visible to capture (Windows older than 10 2004?)")
        self._render(force=True)
        x, y = self._clamp(*pos)
        u32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, self.size[0], self.size[1], SWP_NOACTIVATE | SWP_SHOWWINDOW)
        self.root.after(TICK_MS, self._tick)

    def _clamp(self, x, y):
        vx, vy, vw, vh = screenshot.virtual_screen()
        w, h = self.size
        return max(vx, min(vx + vw - w, int(x))), max(vy, min(vy + vh - h, int(y)))

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE
            if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE):
                x, y = ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value
                if msg == WM_LBUTTONDOWN:
                    key = hit_rect(self.rects, x, y)
                    if key == "grip":
                        self.drag = (x, y)
                        u32.SetCapture(hwnd)
                    else:
                        self.pressed = key
                    return 0
                if msg == WM_MOUSEMOVE and self.drag is not None:
                    p = wintypes.POINT()
                    u32.GetCursorPos(ctypes.byref(p))
                    nx, ny = self._clamp(p.x - self.drag[0], p.y - self.drag[1])
                    u32.SetWindowPos(hwnd, None, nx, ny, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                    return 0
                if msg == WM_LBUTTONUP:
                    if self.drag is not None:
                        self.drag = None
                        u32.ReleaseCapture()
                    elif self.pressed is not None:
                        if hit_rect(self.rects, x, y) == self.pressed:
                            self.clicks.append(self.pressed)     # handled by the tk timer
                        self.pressed = None
                    return 0
            if msg == WM_CAPTURECHANGED:
                self.drag = None
            if msg == WM_SETCURSOR:
                p = wintypes.POINT()
                u32.GetCursorPos(ctypes.byref(p))
                r = wintypes.RECT()
                u32.GetWindowRect(hwnd, ctypes.byref(r))
                key = hit_rect(self.rects, p.x - r.left, p.y - r.top)
                cur = IDC_SIZEALL if key == "grip" or self.drag else IDC_HAND if key else IDC_ARROW
                u32.SetCursor(u32.LoadCursorW(None, ctypes.c_void_p(cur)))
                return 1
        except Exception:
            pass
        return u32.DefWindowProcW(hwnd, msg, wp, lp)

    def _hover_now(self):
        if self.drag is not None:
            return "grip"
        p = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(p))
        r = wintypes.RECT()
        u32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return hit_rect(self.rects, p.x - r.left, p.y - r.top)

    def _tick(self):
        if not self.hwnd:
            return
        try:
            while self.clicks:
                key = self.clicks.pop(0)
                if self.mode == "rec":
                    self.ctl._live_act(key)
                if not self.hwnd:
                    return
            self.hover = self._hover_now()
            self._render()
        except Exception:
            self.ctl.log("screenrec panel tick:\n" + traceback.format_exc())
        if self.hwnd:
            self.root.after(TICK_MS, self._tick)

    def _render(self, force=False):
        if self.mode == "saving":
            img, rects, w, h = self.art.render(("saving",), {}, None, progress=self.progress)
        else:
            el = self.ctl.elapsed()
            blink = int(el * 2) % 2 == 0 or el < 0.5
            states = {t: self.ctl.live_state(t) for t in TOGGLES}
            img, rects, w, h = self.art.render(REC_ITEMS, states, self.hover, el, blink)
        key = self.art._cache[0]
        if key == self._key and not force:
            return
        self._key = key
        self.rects, self.size = rects, (w, h)
        self._blit(img)

    def _blit(self, img):
        W, H = img.size
        a = img.split()[3]
        r, g, b, _ = img.convert("RGBa").split()
        data = Image.merge("RGBA", (b, g, r, a)).tobytes()
        if self._dc is None or self._dc[3] != (W, H):
            self._free_dc()
            bmi = recorder.BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(bmi)
            bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = W, -H, 1, 32
            mdc = g32.CreateCompatibleDC(None)
            bits = ctypes.c_void_p()
            hbm = g32.CreateDIBSection(mdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            old = g32.SelectObject(mdc, hbm)
            self._dc = (mdc, hbm, old, (W, H), bits)
        mdc, _hbm, _old, _sz, bits = self._dc
        ctypes.memmove(bits, data, len(data))
        blend = recorder.BLENDFUNCTION(0, 0, 255, 1)
        sdc = u32.GetDC(None)
        try:
            u32.UpdateLayeredWindow(self.hwnd, sdc, None, ctypes.byref(recorder.SIZE(W, H)), mdc,
                                    ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), 2)
        finally:
            u32.ReleaseDC(None, sdc)

    def _free_dc(self):
        if self._dc:
            mdc, hbm, old, _sz, _bits = self._dc
            g32.SelectObject(mdc, old)
            g32.DeleteObject(hbm)
            g32.DeleteDC(mdc)
            self._dc = None

    def move(self, x, y):
        x, y = self._clamp(x, y)
        u32.SetWindowPos(self.hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)

    def saving(self, p=0.0):
        self.mode, self.progress = "saving", p
        self.pressed = None

    def position(self):
        r = wintypes.RECT()
        u32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.left, r.top

    def destroy(self):
        if self.hwnd:
            if self.drag is not None:
                u32.ReleaseCapture()
            _PANELS.pop(self.hwnd, None)
            u32.DestroyWindow(self.hwnd)
            self.hwnd = None
        self._free_dc()


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
        self._shown = False             # panel + frame are up (after the first frame)
        self.panel = None               # LivePanel of the running recording
        self.save_panel = None          # the same panel after Stop, showing the saving progress
        self._panel_pos = None          # where the overlay's panel was: the live one starts there
        self.live = {}                  # switches of the running recording
        self._jobs_left = 0
        self._job_q = queue.Queue()
        self._ui_q = queue.Queue()
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

    def _on_pick(self, rect, pos=None):
        self.capture = None
        self._panel_pos = pos
        self.root.after(START_DELAY_MS, lambda: self.start(rect))

    # ---- the live panel's side ----
    def live_state(self, key):
        return bool(self.live.get(key, self.saved(key)))

    def elapsed(self):
        if self.session is None or self.meta is None:
            return 0.0
        return max(0.0, time.time() - self.meta["started"])

    def _live_act(self, key):
        """A click on the live panel (tk thread)."""
        sess = self.session
        if key == "stop":
            self.stop()
        elif key in TOGGLES and sess is not None:
            on = not self.live_state(key)
            self.live[key] = on
            self.set_now(key, on)                   # remembered for the next recording too
            sess.set_option(key, on)
            if key in ("system", "mic") and on and self.meta is not None:
                self.meta["audio_used"] = True
                try:
                    self._write_meta(self.meta)
                except Exception:
                    self.log("screenrec sidecar:\n" + traceback.format_exc())
            self.log("screenrec: %s %s" % (key, "on" if on else "off"))

    def _panel_default_pos(self, rect, w, h):
        x, y, rw, rh = rect
        ml, mt, mr, mb = screenshot.monitor_at(x + rw // 2, y + rh // 2)
        m = int(12 * _dpi())
        py = y + rh + m if y + rh + m + h <= mb - m else max(mt + m, y - m - h) if y - m - h >= mt + m else mb - m - h - int(60 * _dpi())
        return max(ml + m, min(mr - m - w, x + rw // 2 - w // 2)), py

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
        self.live = {t: bool(self.saved(t)) for t in TOGGLES}
        meta = {"video": stem + ".mp4", "wav": stem + ".wav", "side": stem + ".json",
                "folder": self.saved("folder"), "name": "%s %s" % (V("file"), time.strftime("%Y-%m-%d %H-%M")),
                "started": time.time(), "offset": 0, "audio_used": bool(system or mic)}
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
        """The first frame is in (the encoder needs ~0.5 s to start): only now show the panel and
        the frame, so they mean "recording" and the timer matches the video."""
        self._shown = True
        since = max(0.0, (ra.qpc_hns() - sess.t0) / 1e7)
        self.meta["started"] = time.time() - since
        try:
            pos = self._panel_pos
            panel = LivePanel(self.root, self, pos or (0, 0), self.log)
            if pos is None:
                panel.move(*self._panel_default_pos(sess.rect, *panel.size))
            self.panel = panel
        except Exception:
            self.log("screenrec panel failed:\n" + traceback.format_exc())
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
            self._drop_panel()
            self._job_q.put(("drop", meta, sess))
            self.app._notify(V("tab"), V("err_start"), kind="error")
            return
        self.meta["offset"] = sess.audio_offset
        try:
            self._write_meta(self.meta)
        except Exception:
            self.log("screenrec sidecar:\n" + traceback.format_exc())
        a = sess.audio
        if a is not None and (a.system or a.mic):
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

    def _drop_panel(self):
        if self.panel is not None:
            self.panel.destroy()
            self.panel = None

    def stop(self):
        sess, meta = self.session, self.meta
        if sess is None:
            return
        self.session = self.meta = None
        sess.stop(wait=False)
        self._hide_frame()
        self.log("screenrec: stopped after %s" % fmt_time(time.time() - meta["started"]))
        self._jobs_left += 1
        if self.panel is not None:              # the same panel now shows the saving progress
            if self.save_panel is not None:
                self.save_panel.destroy()
            self.save_panel, self.panel = self.panel, None
            self.save_panel.saving(0.0)
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
                    job[1]["audio_used"] = job[2].audio_used
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
        mux_wav = wav if meta.get("audio_used", True) else None     # never switched on: no silent track
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
            rv.mux(video, mux_wav, int(meta.get("offset") or 0), tmp, total_hns=total, progress=prog)
            out = self._unique(dest_dir, name, ".mp4")
            shutil.move(tmp, out)
        except Exception:
            self.log("screenrec mux failed:\n" + traceback.format_exc())
            try:                        # keep the raw files rather than lose the recording
                out = self._unique(dest_dir, name, ".mp4")
                shutil.move(video, out)
                if mux_wav:
                    shutil.move(mux_wav, self._unique(dest_dir, name, ".wav"))
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
        if self.save_panel is not None:
            self.save_panel.progress = p

    def _saved(self, path, warn, recovered):
        self._jobs_left = max(0, self._jobs_left - 1)
        if self.save_panel is not None and self._jobs_left == 0:
            self.save_panel.destroy()
            self.save_panel = None
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
        self._drop_panel()
        if self.save_panel is not None:
            self.save_panel.destroy()
            self.save_panel = None

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

        tk.Frame(parent, bg=th["sf"], height=4).pack()

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

        header(V("sec_video"))
        pills(V("fps"), [(f, str(f)) for f in FPS_CHOICES], "fps")
        pills(V("quality"), [(q, V("q_" + q)) for q in QUALITY_CHOICES], "quality")

        header(V("sec_during"))
        check(V("show_frame"), "frame")

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
