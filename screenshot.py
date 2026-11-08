# -*- coding: utf-8 -*-
"""Screenshots for HDContainer.

The hotkey (Ctrl+Shift+A by default) freezes the screen: the capture is shown
darkened on a full-screen overlay and the mouse selects a region (a plain click
takes the whole monitor under the cursor). The region opens in a small editor:
pen, marker, line, arrow, frame, circle, text, crop; "Copy to clipboard" puts the result in the
clipboard (CF_DIB + PNG) and closes the editor, "Save as…" writes a file.

Every capture is kept in %LOCALAPPDATA%\\HDContainer\\screenshots for N days
(30 by default), like the dictation history: tray → "Screenshot history" shows
them as thumbnails, each one can be copied again, edited or deleted.

The overlay is a picture over the screen and nothing else: it never takes the focus
(a game would minimise), it is left out of screen capture (a stream keeps showing the
live screen), and mouse and Esc come through low-level hooks. When a game holds the
mouse, moves are kept from it too and the capture goes straight to the clipboard,
because the editor window would take the focus.

Everything runs on the tk thread. The keyboard hook (its own voice_sys.KeyHook
instance) only queues events; a tk timer handles them. File writes go through
one worker thread so a capture and its later edit never interleave.
"""
import io
import os
import sys
import math
import time
import queue
import ctypes
import threading
import traceback
from collections import namedtuple
from ctypes import wintypes
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageGrab, ImageTk

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
    "t_line": {"en": "Line", "ru": "Линия", "es": "Línea", "pt": "Linha", "de": "Linie", "fr": "Ligne", "zh": "直线"},
    "t_ellipse": {"en": "Circle", "ru": "Круг", "es": "Círculo", "pt": "Círculo", "de": "Kreis", "fr": "Cercle", "zh": "圆形"},
    "t_text": {"en": "Text", "ru": "Текст", "es": "Texto", "pt": "Texto", "de": "Text", "fr": "Texte", "zh": "文字"},
    "t_redo": {"en": "Redo", "ru": "Повторить", "es": "Rehacer", "pt": "Refazer", "de": "Wiederholen", "fr": "Rétablir", "zh": "重做"},
    "h_angle": {"en": "Shift — 45° steps", "ru": "Shift — шаг 45°", "es": "Shift — pasos de 45°", "pt": "Shift — passos de 45°", "de": "Umschalt — 45°-Schritte", "fr": "Maj — pas de 45°", "zh": "Shift — 45° 步进"},
    "h_square": {"en": "Shift — square", "ru": "Shift — квадрат", "es": "Shift — cuadrado", "pt": "Shift — quadrado", "de": "Umschalt — Quadrat", "fr": "Maj — carré", "zh": "Shift — 正方形"},
    "h_circle": {"en": "Shift — perfect circle", "ru": "Shift — ровный круг", "es": "Shift — círculo perfecto", "pt": "Shift — círculo perfeito", "de": "Umschalt — exakter Kreis", "fr": "Maj — cercle parfait", "zh": "Shift — 正圆"},
    "h_text": {"en": "Click where the text goes", "ru": "Кликни, где нужен текст", "es": "Haz clic donde va el texto", "pt": "Clique onde vai o texto", "de": "Klicke, wo der Text stehen soll", "fr": "Clique là où placer le texte", "zh": "点击要放置文字的位置"},
    "h_typing": {"en": "Enter — done  ·  Shift+Enter — new line  ·  Esc — cancel", "ru": "Enter — готово  ·  Shift+Enter — новая строка  ·  Esc — отмена", "es": "Enter — listo  ·  Shift+Enter — nueva línea  ·  Esc — cancelar", "pt": "Enter — pronto  ·  Shift+Enter — nova linha  ·  Esc — cancelar", "de": "Enter — fertig  ·  Umschalt+Enter — neue Zeile  ·  Esc — abbrechen", "fr": "Entrée — terminé  ·  Maj+Entrée — nouvelle ligne  ·  Échap — annuler", "zh": "Enter — 完成  ·  Shift+Enter — 换行  ·  Esc — 取消"},
    "h_crop": {"en": "Select what to keep", "ru": "Выдели, что оставить", "es": "Selecciona lo que quieres conservar", "pt": "Selecione o que manter", "de": "Wähle aus, was bleiben soll", "fr": "Sélectionne ce qu'il faut garder", "zh": "选择要保留的区域"},
    "h_mark": {"en": "Drag — move  ·  right click — delete", "ru": "Тяни — подвинуть  ·  правый клик — удалить", "es": "Arrastra — mover  ·  clic derecho — eliminar", "pt": "Arraste — mover  ·  clique direito — excluir", "de": "Ziehen — verschieben  ·  Rechtsklick — löschen", "fr": "Glisse — déplacer  ·  clic droit — supprimer", "zh": "拖动 — 移动  ·  右键 — 删除"},
    "copy": {"en": "Copy to clipboard", "ru": "Скопировать в буфер", "es": "Copiar al portapapeles", "pt": "Copiar para a área de transferência", "de": "In die Zwischenablage", "fr": "Copier dans le presse-papiers", "zh": "复制到剪贴板"},
    "copy_short": {"en": "Copy", "ru": "Копировать", "es": "Copiar", "pt": "Copiar", "de": "Kopieren", "fr": "Copier", "zh": "复制"},
    "save_as": {"en": "Save as…", "ru": "Сохранить как…", "es": "Guardar como…", "pt": "Salvar como…", "de": "Speichern unter…", "fr": "Enregistrer sous…", "zh": "另存为…"},
    "copied": {"en": "Copied to the clipboard", "ru": "Скопировано в буфер", "es": "Copiado al portapapeles", "pt": "Copiado para a área de transferência", "de": "In die Zwischenablage kopiert", "fr": "Copié dans le presse-papiers", "zh": "已复制到剪贴板"},
    "saved": {"en": "Saved", "ru": "Сохранено", "es": "Guardado", "pt": "Salvo", "de": "Gespeichert", "fr": "Enregistré", "zh": "已保存"},
    "err_capture": {"en": "Could not capture the screen", "ru": "Не удалось снять экран", "es": "No se pudo capturar la pantalla", "pt": "Não foi possível capturar a tela", "de": "Bildschirm konnte nicht aufgenommen werden", "fr": "Impossible de capturer l'écran", "zh": "无法截取屏幕"},
    "err_clip": {"en": "Could not write to the clipboard", "ru": "Не удалось записать в буфер обмена", "es": "No se pudo escribir en el portapapeles", "pt": "Não foi possível gravar na área de transferência", "de": "Zwischenablage konnte nicht beschrieben werden", "fr": "Impossible d'écrire dans le presse-papiers", "zh": "无法写入剪贴板"},
    "delete": {"en": "Delete", "ru": "Удалить", "es": "Eliminar", "pt": "Excluir", "de": "Löschen", "fr": "Supprimer", "zh": "删除"},
    "sec_exclude": {"en": "Not in these programs", "ru": "Не срабатывать в программах", "es": "No en estos programas", "pt": "Não nestes programas", "de": "Nicht in diesen Programmen", "fr": "Pas dans ces programmes", "zh": "在这些程序中不触发"},
    "exclude_note": {"en": "While one of them is in front, the hotkey goes to it as usual — for games that use the same keys.", "ru": "Пока такая программа на переднем плане, сочетание достаётся ей, как обычно, — для игр, где эти клавиши заняты.", "es": "Mientras una de ellas está delante, el atajo le llega a ella como siempre: para juegos que usan las mismas teclas.", "pt": "Enquanto uma delas estiver em primeiro plano, o atalho vai para ela como sempre — para jogos que usam as mesmas teclas.", "de": "Solange eines davon im Vordergrund ist, geht der Hotkey wie gewohnt an das Programm — für Spiele, die dieselben Tasten nutzen.", "fr": "Tant que l'un d'eux est au premier plan, le raccourci lui revient comme d'habitude — pour les jeux qui utilisent les mêmes touches.", "zh": "当其中某个程序在前台时，快捷键照常交给它——适用于占用同样按键的游戏。"},
    "exclude_add": {"en": "Add a program…", "ru": "Добавить программу…", "es": "Añadir programa…", "pt": "Adicionar programa…", "de": "Programm hinzufügen…", "fr": "Ajouter un programme…", "zh": "添加程序…"},
    "exclude_file": {"en": "Choose an .exe file…", "ru": "Выбрать файл .exe…", "es": "Elegir un archivo .exe…", "pt": "Escolher um arquivo .exe…", "de": ".exe-Datei wählen…", "fr": "Choisir un fichier .exe…", "zh": "选择 .exe 文件…"},
    "exclude_none": {"en": "No other programs with windows are running", "ru": "Других программ с окнами сейчас нет", "es": "No hay otros programas con ventanas", "pt": "Não há outros programas com janelas", "de": "Keine anderen Programme mit Fenstern", "fr": "Aucun autre programme avec fenêtre", "zh": "没有其他带窗口的程序在运行"},
    "exe_files": {"en": "Programs", "ru": "Программы", "es": "Programas", "pt": "Programas", "de": "Programme", "fr": "Programmes", "zh": "程序"},
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
    "exclude": [],                  # 'game.exe' names: the hotkey is left to them while they are in front
}
# keys allowed as the hotkey WITHOUT a modifier: F1-F24, Pause, ScrollLock, PrtSc.
# A single modifier is refused: it would fire on every Ctrl / Shift press.
PLAIN_HOTKEYS_OK = frozenset(range(0x70, 0x88)) | {0x13, 0x91, 0x2C}
KEEP_CHOICES = (7, 14, 30, 90)
THUMB_W, THUMB_H = 232, 140
MIN_DRAG = 4                        # a shorter drag is a click = whole monitor
UNDO_MAX = 15
PAGE = 60                           # history cards shown per "Show more"
OVERLAY_ALPHA = 1.0
OVERLAY_TIMEOUT = 120               # s: the mouse is grabbed, so the overlay never stays forever
DARK = 45                           # brightness (%) of the frozen screen outside the selection

# key, name, shortcut (a Latin letter, matched by key code)
TOOLS = (("pen", "t_pen", "P"), ("marker", "t_marker", "M"), ("line", "t_line", "L"),
         ("arrow", "t_arrow", "A"), ("rect", "t_rect", "R"), ("ellipse", "t_ellipse", "O"),
         ("text", "t_text", "T"), ("crop", "t_crop", "C"))
TOOL_HINTS = {"line": "h_angle", "arrow": "h_angle", "rect": "h_square", "ellipse": "h_circle",
              "text": "h_text", "crop": "h_crop"}
COLORS = ("#ff3b30", "#ffd60a", "#30d158", "#0a84ff", "#ffffff", "#111111")
WIDTHS = (2, 4, 8)
TEXT_SIZES = {2: 18, 4: 28, 8: 44}  # text height in picture pixels for each width button
TEXT_PAD = 3
HIT_TOL = 6                         # how close to a mark the pointer counts, screen pixels
SS_DRAW = 3                         # supersampling of strokes on the picture
STAGE = "#141416"                   # behind the picture, darker than the bars
HAIRLINE = "#37383c"
HAIRLINE_LIGHT = "#5a5b60"
SELECTED = "#3d3e43"
ICON_IDLE = "#c4c7cc"
ICON_OFF = "#55565a"
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


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hCursor", ctypes.c_void_p),
                ("ptScreenPos", wintypes.POINT)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetClipCursor.argtypes = [ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
user32.GetWindow.restype = ctypes.c_void_p
user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.EnumWindows.argtypes = [vs.WNDENUMPROC, ctypes.c_void_p]
user32.GetAncestor.restype = ctypes.c_void_p
user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, wintypes.DWORD]
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WDA_EXCLUDEFROMCAPTURE = 0x11       # Windows 10 2004+
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONUP = 0x200, 0x201, 0x202, 0x205
LLMHF_INJECTED = 1
SHELL_CLASSES = ("Progman", "WorkerW", "Shell_TrayWnd")


def foreground_fullscreen():
    """Does the foreground window cover its whole monitor (a game, a video)?"""
    fg = user32.GetForegroundWindow()
    if not fg or vs.class_name(fg) in SHELL_CLASSES:
        return False
    r = wintypes.RECT()
    if not user32.GetWindowRect(fg, ctypes.byref(r)):
        return False
    l, t, rt, b = monitor_at((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    return r.left <= l and r.top <= t and r.right >= rt and r.bottom >= b


def foreground_exe():
    return vs.process_exe(vs.window_pid(user32.GetForegroundWindow()))


def running_programs():
    """Sorted exe names of the programs that have a visible titled top-level window."""
    names = set()

    def cb(h, _l):
        if user32.IsWindowVisible(h) and user32.GetWindowTextLengthW(h) > 0 and not user32.GetWindow(h, 4):
            n = vs.process_exe(vs.window_pid(h))
            if n:
                names.add(n)
        return True
    user32.EnumWindows(vs.WNDENUMPROC(cb), None)
    me = os.path.basename(sys.executable).lower()
    return sorted(names - {me, "explorer.exe", "applicationframehost.exe", "textinputhost.exe",
                           "shellexperiencehost.exe", "searchhost.exe", "startmenuexperiencehost.exe"})


def take_foreground(hwnd):
    """Bring our own new window to the front WITH the keyboard. Windows gives the foreground
    only to the process that got the last input, and the hotkey went to a hook and the
    overlay's clicks were swallowed: so borrow the input state of the window in front."""
    fg = user32.GetForegroundWindow()
    if not hwnd or fg == hwnd:
        return
    me = vs.kernel32.GetCurrentThreadId()
    ft = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(ft and ft != me and user32.AttachThreadInput(ft, me, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(ft, me, False)


def game_has_mouse():
    """The foreground app holds the pointer: it confines it, or hides it over a whole
    monitor. Hidden alone proves nothing: Windows hides the pointer while typing."""
    clip = wintypes.RECT()
    if user32.GetClipCursor(ctypes.byref(clip)):
        vx, vy, vw, vh = virtual_screen()
        if clip.left > vx or clip.top > vy or clip.right < vx + vw or clip.bottom < vy + vh:
            return True
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(ci)
    return bool(user32.GetCursorInfo(ctypes.byref(ci))) and not ci.flags & 1 and foreground_fullscreen()


class MouseGrab:
    """Low-level mouse hook for the overlay, which must get no real input: a window that
    is clicked could take the focus. Buttons and the wheel are swallowed. With lock=True
    moves are swallowed as well, so a game does not turn its camera; the position is then
    counted here, because the real pointer stands still. Events go to self.events;
    the callback touches nothing else."""

    def __init__(self, lock, x, y, log):
        self.lock, self.x, self.y, self.log = lock, x, y, log
        self.accept_injected = False       # the game's own SetCursorPos is not the user's mouse
        self.events = queue.Queue()
        self._tid = 0
        self._hook = None
        self._proc = vs._HOOKPROC(self._cb)

    def _cb(self, code, wp, lp):
        try:
            if code == 0:
                ms = MSLLHOOKSTRUCT.from_address(lp)
                if wp != WM_MOUSEMOVE:
                    self.events.put(("btn", wp, self.x, self.y))
                    return 1
                if not self.lock:
                    self.x, self.y = ms.pt.x, ms.pt.y
                    self.events.put(("move", self.x, self.y))
                elif self.accept_injected or not ms.flags & LLMHF_INJECTED:
                    cur = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(cur))
                    vx, vy, vw, vh = virtual_screen()
                    self.x = max(vx, min(vx + vw - 1, self.x + ms.pt.x - cur.x))
                    self.y = max(vy, min(vy + vh - 1, self.y + ms.pt.y - cur.y))
                    self.events.put(("move", self.x, self.y))
                    return 1
        except Exception:
            pass
        return vs.user32.CallNextHookEx(None, code, wp, lp)

    def start(self):
        ready = threading.Event()

        def run():
            self._tid = vs.kernel32.GetCurrentThreadId()
            self._hook = vs.user32.SetWindowsHookExW(14, self._proc, vs.kernel32.GetModuleHandleW(None), 0)
            if not self._hook:
                self.log("screenshot: mouse hook failed: %d" % ctypes.get_last_error())
            ready.set()
            msg = vs._MSG()
            while vs.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
            if self._hook:
                vs.user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
        threading.Thread(target=run, daemon=True, name="hdc-shot-mouse").start()
        ready.wait(2.0)
        return bool(self._hook)

    def stop(self):
        if self._tid:
            vs.user32.PostThreadMessageW(self._tid, vs.WM_QUIT, 0, 0)
            self._tid = 0


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
            "bd": C("COL_BORDER", "#2a2b2c"), "hv": C("COL_HOVER", "#303236"), "ac": C("COL_ACCENT", "#4c8bf5"), "ac2": C("COL_ACCENT_HI", "#629bff"),
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
        self.hook.block = self._excluded_now
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

    def _excluded_now(self):
        """Keyboard-hook thread: is a program from the exclusion list in front? (saved settings only)"""
        ex = self.app.settings.get("screenshot", {}).get("exclude")
        return bool(ex) and foreground_exe() in {x.lower() for x in ex}

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
            self.app._notify(SS("editor_title"), SS("err_capture"), kind="error")

    def _on_region(self, img, quiet=False):
        self.capture = None
        path = self._new_path() if self.get("history") else None
        if path:
            self._io(lambda: self._write(path, img))
        if self.get("editor") and not quiet:
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

        header(SS("sec_exclude"))
        note(SS("exclude_note"))
        for exe in self.get("exclude"):
            row = tk.Frame(parent, bg=th["sf"])
            row.pack(fill="x", padx=pad, pady=1)
            tk.Label(row, text=exe, bg=th["sf2"], fg=th["tx"], font=th["font"], padx=10, pady=3).pack(side="left")
            x = tk.Label(row, text="✕", bg=th["sf"], fg=th["dim"], font=th["font"], padx=8, cursor="hand2")
            x.pack(side="left")
            x.bind("<Button-1>", lambda _e, n=exe: self._exclude_set([v for v in self.get("exclude") if v != n]))
        row = tk.Frame(parent, bg=th["sf"])
        row.pack(fill="x", padx=pad, pady=(6, 2))
        add = a._ghost_btn(row, SS("exclude_add"), lambda: self._exclude_menu(add))
        add.pack(side="left")

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

    def _exclude_set(self, names):
        self.set("exclude", sorted({n.lower() for n in names if n}))
        self._rebuild()

    def _exclude_menu(self, btn):
        cur = set(self.get("exclude"))
        m = tk.Menu(btn, tearoff=0)
        progs = [n for n in running_programs() if n not in cur]
        for n in progs:
            m.add_command(label=n, command=lambda n=n: self._exclude_set(self.get("exclude") + [n]))
        if not progs:
            m.add_command(label=SS("exclude_none"), state="disabled")
        m.add_separator()
        m.add_command(label=SS("exclude_file"), command=self._exclude_browse)
        try:
            m.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())
        finally:
            m.grab_release()

    def _exclude_browse(self):
        tab = self._tab
        p = filedialog.askopenfilename(parent=tab[1] if tab else None, filetypes=[(SS("exe_files"), "*.exe")])
        if p:
            self._exclude_set(self.get("exclude") + [os.path.basename(p)])

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
_Point = namedtuple("_Point", "x y")


class Capture:
    def __init__(self, ctl, img, origin):
        self.ctl, self.img = ctl, img
        self.ox, self.oy = origin
        self.top = self.grab = None
        self.done = False
        try:
            self._build()
        except Exception:
            self.close()
            raise

    def _build(self):
        ctl, img = self.ctl, self.img
        w, h = img.size
        # taken before the overlay exists: it must describe the app under it
        lock = game_has_mouse()
        self.quiet = lock or foreground_fullscreen()
        top = tk.Toplevel(ctl.root)
        self.top = top
        top.overrideredirect(True)
        top.attributes("-alpha", 0.0)      # stays invisible until it is safe from capture
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
        self._crosshair(pt.x - self.ox, pt.y - self.oy)
        top.update()
        hwnd = user32.GetAncestor(top.winfo_id(), 2)                  # GA_ROOT
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE)
        if not user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
            ctl.log("screenshot: overlay stays visible to capture (Windows older than 10 2004?)")
        self.grab = MouseGrab(lock, pt.x, pt.y, ctl.log)
        if not self.grab.start():
            raise OSError("no mouse hook")
        ctl.hook.recording = True          # Esc arrives through the keyboard hook: no focus here
        top.attributes("-alpha", OVERLAY_ALPHA)
        self.deadline = time.time() + OVERLAY_TIMEOUT
        self._pump()

    def _pump(self):
        if self.done:
            return
        if time.time() > self.deadline:
            self.cancel()
            return
        last = None
        while not self.done:
            try:
                ev = self.grab.events.get_nowait()
            except queue.Empty:
                break
            if ev[0] == "move":
                last = ev
                continue
            if last:
                self._moved(last)
                last = None
            self._button(ev)
        if last and not self.done:
            self._moved(last)
        if not self.done:
            self.top.after(8, self._pump)

    def _at(self, ev):
        return _Point(ev[-2] - self.ox, ev[-1] - self.oy)

    def _moved(self, ev):
        p = self._at(ev)
        if self.start is None:
            self._motion(p)
        else:
            self._drag(p)

    def _button(self, ev):
        if ev[1] == WM_LBUTTONDOWN:
            self._press(self._at(ev))
        elif ev[1] == WM_LBUTTONUP:
            self._release(self._at(ev))
        elif ev[1] == WM_RBUTTONUP:        # on release: the up event must not reach the app below
            self.cancel()

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
            self.ctl._on_region(region, self.quiet)
        except Exception:
            self.ctl.capture = None
            self.ctl.log("screenshot region failed:\n" + traceback.format_exc())

    def cancel(self):
        self.close()
        self.ctl._on_cancel()

    def close(self):
        self.done = True
        self.ctl.hook.recording = False
        if self.grab:
            self.grab.stop()
        try:
            if self.top:
                self.top.destroy()
        except Exception:
            pass
        self.sel_photo = self.dark = None


# ---------------------------------------------------------------------------
#  Drawing on the picture. Every stroke is rasterised SS times larger on a mask of
#  its own bounding box and reduced, so edges are smooth at any size
# ---------------------------------------------------------------------------
def _stamp(img, bbox, paint, rgb, alpha=255):
    iw, ih = img.size
    l, t = max(0, int(math.floor(bbox[0]))), max(0, int(math.floor(bbox[1])))
    r, b = min(iw, int(math.ceil(bbox[2]))), min(ih, int(math.ceil(bbox[3])))
    if r <= l or b <= t:
        return
    m = Image.new("L", ((r - l) * SS_DRAW, (b - t) * SS_DRAW), 0)
    paint(ImageDraw.Draw(m), lambda p: ((p[0] - l) * SS_DRAW, (p[1] - t) * SS_DRAW))
    m = m.reduce(SS_DRAW)
    if alpha < 255:
        m = m.point(lambda v: v * alpha // 255)
    img.paste(rgb, (l, t, r, b), m)


def _box(p1, p2):
    return min(p1[0], p2[0]), min(p1[1], p2[1]), max(p1[0], p2[0]), max(p1[1], p2[1])


def draw_stroke(img, pts, rgb, width, alpha=255):
    """Freehand or straight stroke with round ends and joints."""
    seq = [tuple(p) for p in (pts if len(pts) > 1 else list(pts) * 2)]
    pad = width + 2
    bbox = (min(p[0] for p in seq) - pad, min(p[1] for p in seq) - pad,
            max(p[0] for p in seq) + pad, max(p[1] for p in seq) + pad)

    def paint(d, to):
        q = [to(p) for p in seq]
        d.line(q, fill=255, width=max(1, round(width * SS_DRAW)), joint="curve")
        r = width * SS_DRAW / 2
        for x, y in q:
            d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    _stamp(img, bbox, paint, rgb, alpha)


def draw_rect(img, p1, p2, rgb, width):
    x1, y1, x2, y2 = _box(p1, p2)
    rad = min(width * 1.5, (x2 - x1) / 2, (y2 - y1) / 2)

    def paint(d, to):
        a, b = to((x1, y1)), to((x2, y2))
        d.rounded_rectangle([a, (b[0] - 1, b[1] - 1)], radius=rad * SS_DRAW, outline=255,
                            width=max(1, round(width * SS_DRAW)))
    _stamp(img, (x1, y1, x2, y2), paint, rgb)


def draw_ellipse(img, p1, p2, rgb, width):
    x1, y1, x2, y2 = _box(p1, p2)

    def paint(d, to):
        a, b = to((x1, y1)), to((x2, y2))
        d.ellipse([a, (b[0] - 1, b[1] - 1)], outline=255, width=max(1, round(width * SS_DRAW)))
    _stamp(img, (x1, y1, x2, y2), paint, rgb)


def draw_arrow(img, p1, p2, rgb, width, dims):
    """Arrow from p1 to p2 as one rounded shape: the shaft swells from a thin tail to the
    neck, the head is swept back with a notch. dims = (neck, head length, half width)."""
    (x1, y1), (x2, y2) = p1, p2
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1:
        draw_stroke(img, [p2], rgb, width * 2)
        return
    ux, uy = dx / length, dy / length
    fit = min(1.0, length * 0.6 / dims[1])          # a short arrow gets a smaller head
    neck, head, half = dims[0] * fit, dims[1] * fit, dims[2] * fit
    edge = max(0.8, width * 0.3)                    # rounding of every corner
    shaft = width * 0.62 * max(fit, 0.5)
    tail = width * 0.28
    end = length - edge                             # the rounding must not overshoot p2

    def at(u, v):
        return x1 + ux * u - uy * v, y1 + uy * u + ux * v

    shape = [at(edge, tail), at(end - neck, shaft), at(end - head, half), at(end, 0),
             at(end - head, -half), at(end - neck, -shaft), at(edge, -tail)]
    pad = edge + 2
    xs, ys = [p[0] for p in shape], [p[1] for p in shape]

    def paint(d, to):
        q = [to(p) for p in shape]
        d.polygon(q, fill=255)
        d.line(q + q[:1], fill=255, width=max(1, round(edge * 2 * SS_DRAW)))
        r = edge * SS_DRAW
        for x, y in q:
            d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    _stamp(img, (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad), paint, rgb)


_FONTS = {}


def _font(px, bold=True):
    key = (px, bold)
    if key not in _FONTS:
        fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        names = ("seguisb.ttf", "segoeuib.ttf", "arial.ttf") if bold else ("segoeui.ttf", "arial.ttf")
        for n in names:
            try:
                _FONTS[key] = ImageFont.truetype(os.path.join(fonts, n), px)
                break
            except OSError:
                continue
        else:
            _FONTS[key] = ImageFont.load_default(px)
    return _FONTS[key]


def draw_text(img, x, y, text, rgb, px):
    """Text with its top-left corner at (x, y); lines are spaced like the Tk text box."""
    font = _font(px)
    asc, desc = font.getmetrics()
    d = ImageDraw.Draw(img)
    for i, line in enumerate(text.split("\n")):
        d.text((x, y + i * (asc + desc)), line, font=font, fill=rgb, anchor="la")


def constrain(tool, a, b):
    """Shift held: 45-degree steps for lines, equal sides for boxes and circles."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if tool in ("line", "arrow"):
        step = math.pi / 4
        ang = round(math.atan2(dy, dx) / step) * step
        n = math.hypot(dx, dy)
        return a[0] + n * math.cos(ang), a[1] + n * math.sin(ang)
    n = max(abs(dx), abs(dy))
    return a[0] + math.copysign(n, dx), a[1] + math.copysign(n, dy)


# ---------------------------------------------------------------------------
#  Marks are objects over the untouched picture, so they stay movable and deletable.
#  A mark is a dict: k (stroke, arrow, rect, ellipse, text), pts, rgb, w and, by kind,
#  a (alpha), dims (arrow), text, px. Marks are never changed in place: undo keeps lists
# ---------------------------------------------------------------------------
def paint_mark(img, o):
    k, pts, rgb = o["k"], o["pts"], o["rgb"]
    if k == "stroke":
        draw_stroke(img, pts, rgb, o["w"], o.get("a", 255))
    elif k == "arrow":
        draw_arrow(img, pts[0], pts[1], rgb, o["w"], o["dims"])
    elif k == "rect":
        draw_rect(img, pts[0], pts[1], rgb, o["w"])
    elif k == "ellipse":
        draw_ellipse(img, pts[0], pts[1], rgb, o["w"])
    elif k == "text":
        draw_text(img, pts[0][0], pts[0][1], o["text"], rgb, o["px"])


def mark_bbox(o):
    pts = o["pts"]
    if o["k"] == "text":
        font = _font(o["px"])
        asc, desc = font.getmetrics()
        lines = o["text"].split("\n")
        x, y = pts[0]
        return x, y, x + max(font.getlength(t) for t in lines), y + (asc + desc) * len(lines)
    pad = o["dims"][2] if o["k"] == "arrow" else o["w"] / 2 + 1
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def _seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def mark_hit(o, p, tol):
    """Is p on the mark? Frames and circles count by their line only: inside them
    the picture stays free for drawing."""
    l, t, r, b = mark_bbox(o)
    if not (l - tol <= p[0] <= r + tol and t - tol <= p[1] <= b + tol):
        return False
    k, pts, w = o["k"], o["pts"], o["w"]
    if k == "text":
        return True
    if k == "stroke":
        seq = pts if len(pts) > 1 else list(pts) * 2
        return any(_seg_dist(p, seq[i], seq[i + 1]) <= w / 2 + tol for i in range(len(seq) - 1))
    if k == "arrow":
        a, c = pts
        n = math.hypot(c[0] - a[0], c[1] - a[1]) or 1.0
        fit = min(1.0, n * 0.6 / o["dims"][1])
        neck = (c[0] - (c[0] - a[0]) / n * o["dims"][1] * fit, c[1] - (c[1] - a[1]) / n * o["dims"][1] * fit)
        return _seg_dist(p, a, c) <= w / 2 + tol or _seg_dist(p, neck, c) <= o["dims"][2] * fit + tol
    x1, y1, x2, y2 = _box(pts[0], pts[1])
    if k == "rect":
        return not (x1 + w + tol < p[0] < x2 - w - tol and y1 + w + tol < p[1] < y2 - w - tol)
    cx, cy, ra, rb = (x1 + x2) / 2, (y1 + y2) / 2, max((x2 - x1) / 2, 1.0), max((y2 - y1) / 2, 1.0)
    dx, dy = p[0] - cx, p[1] - cy
    rel = math.hypot(dx / ra, dy / rb)
    if rel < 0.001:
        return False
    gap = abs(rel - 1) * math.hypot(dx, dy) / rel                 # along the ray from the centre
    return gap <= w + tol


def mark_moved(o, dx, dy):
    return dict(o, pts=[(x + dx, y + dy) for x, y in o["pts"]])


# ---------------------------------------------------------------------------
#  Toolbar graphics: authored icons on a 24-unit grid, one stroke weight, drawn
#  by Pillow at 4x. Tk canvas shapes have no antialiasing
# ---------------------------------------------------------------------------
def _aa_mask(w, h, paint):
    m = Image.new("L", (w * 4, h * 4), 0)
    paint(ImageDraw.Draw(m), 4)
    return m.reduce(4)


def _pill(w, h, bg, fill, radius):
    base = Image.new("RGB", (w, h), _hex_rgb(bg))
    if fill:
        base.paste(_hex_rgb(fill), (0, 0, w, h), _aa_mask(
            w, h, lambda d, s: d.rounded_rectangle([0, 0, w * s - 1, h * s - 1], radius=radius * s, fill=255)))
    return base


def _icon_mask(name, n):
    if name == "redo":
        return _icon_mask("undo", n).transpose(Image.FLIP_LEFT_RIGHT)

    def paint(d, s):
        u = n * s / 24.0
        sw = 1.7 * u

        def path(*pts, closed=False):
            q = [(x * u, y * u) for x, y in pts]
            if closed:
                q.append(q[0])
            d.line(q, fill=255, width=max(1, round(sw)), joint="curve")
            for x, y in q:
                d.ellipse([x - sw / 2, y - sw / 2, x + sw / 2, y + sw / 2], fill=255)

        def dot(x, y, r):
            d.ellipse([(x - r) * u, (y - r) * u, (x + r) * u, (y + r) * u], fill=255)

        if name == "pen":
            path((4.5, 19.5), (5.5, 14.5), (15.5, 4.5), (19.5, 8.5), (9.5, 18.5), closed=True)
            path((13, 7), (17, 11))
        elif name == "marker":
            path((13.5, 3.5), (20.5, 10.5), (12.5, 18), (6, 11.5), closed=True)
            path((7.5, 13), (4, 17), (7, 20), (11, 16.5))
            path((13.5, 20.5), (20.5, 20.5))
        elif name == "line":
            path((5.5, 18.5), (18.5, 5.5))
            dot(5.5, 18.5, 2.3)
            dot(18.5, 5.5, 2.3)
        elif name == "arrow":
            path((5, 19), (18.5, 5.5))
            path((10.5, 5.5), (18.5, 5.5), (18.5, 13.5))
        elif name == "rect":
            d.rounded_rectangle([4 * u, 6 * u, 20 * u, 18 * u], radius=2 * u, outline=255, width=max(1, round(sw)))
        elif name == "ellipse":
            d.ellipse([4 * u, 4 * u, 20 * u, 20 * u], outline=255, width=max(1, round(sw)))
        elif name == "text":
            path((5.5, 8), (5.5, 5), (18.5, 5), (18.5, 8))
            path((12, 5), (12, 19))
            path((9.5, 19), (14.5, 19))
        elif name == "crop":
            path((7, 3), (7, 17), (21, 17))
            path((3, 7), (17, 7), (17, 21))
        elif name == "undo":
            arc = [(15 + 5 * math.cos(math.radians(a)), 14 + 5 * math.sin(math.radians(a)))
                   for a in range(-90, 91, 10)]
            path((4, 9), *arc, (9, 19))
            path((8, 5), (4, 9), (8, 13))
    return _aa_mask(n, n, paint)


class ImgBtn:
    """A button whose every state is a picture: paint(state) -> PIL image, where state
    is idle, hover, on or off. The pictures are built once and kept for tk."""

    def __init__(self, parent, paint, bg, click=None, hint=None, on_hint=None):
        self.paint, self.click, self.hint, self.on_hint = paint, click, hint, on_hint
        self.on, self.enabled, self.over = False, True, False
        self.cache = {}
        self.w = tk.Label(parent, bg=bg, bd=0, padx=0, pady=0, highlightthickness=0, cursor="hand2")
        self.w.bind("<Button-1>", self._click)
        self.w.bind("<Enter>", lambda _e: self._over(True))
        self.w.bind("<Leave>", lambda _e: self._over(False))
        self.show()

    def _click(self, _e):
        if self.enabled and self.click:
            self.click()

    def _over(self, v):
        self.over = v
        self.show()
        if self.on_hint and self.hint:
            self.on_hint(self.hint() if v else None)

    def state(self):
        if not self.enabled:
            return "off"
        return "on" if self.on else ("hover" if self.over else "idle")

    def show(self):
        st = self.state()
        if st not in self.cache:
            self.cache[st] = ImageTk.PhotoImage(self.paint(st), master=self.w)
        self.w.configure(image=self.cache[st], cursor="hand2" if self.enabled else "arrow")

    def set(self, on=None, enabled=None):
        if on is not None:
            self.on = on
        if enabled is not None:
            self.enabled = enabled
        self.show()

    def repaint(self):
        self.cache = {}
        self.show()


# ---------------------------------------------------------------------------
#  Editor: pen, marker, line, arrow, frame, circle, text, crop → clipboard / file
# ---------------------------------------------------------------------------
class Editor:
    def __init__(self, ctl, img, path):
        self.ctl, self.path = ctl, path
        self.img = img.convert("RGB")
        self.base, self.marks = self.img, []     # self.img = base + marks, always in step
        self.undo, self.redo = [], []
        self.hover = self.mv = self.mv_photo = None
        self.tool, self.color, self.width = "pen", COLORS[0], WIDTHS[1]
        self.scale, self.ox, self.oy = 1.0, 0, 0
        self.photo = None
        self.pts = []
        self.item = self.item_under = None
        self.txt = self.txt_item = self.txt_font = self.txt_pos = None
        self.hover_hint = None
        self._render_job = None
        self._shadow_key = None
        self.shadow = None
        th = self.th = _theme()
        app = ctl.app

        win = self.win = app._dialog(SS("editor_title"), 800, 600)
        k = self.k = max(1.0, win.winfo_fpixels("1i") / 96.0)
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        wl, wt, wr, wb = monitor_at(pt.x, pt.y, work=True)
        iw, ih = self.img.size
        margin = self.margin = round(24 * k)
        w = max(round(760 * k), min(iw + 2 * margin, int((wr - wl) * 0.92)))
        h = max(round(460 * k), min(ih + 2 * margin + round(120 * k), int((wb - wt) * 0.9)))
        win.resizable(True, True)
        win.minsize(round(760 * k), round(380 * k))
        win.attributes("-topmost", False)
        # centre on the monitor where the mouse is
        win.geometry("%dx%d+%d+%d" % (w, h, wl + (wr - wl - w) // 2, wt + (wb - wt - h) // 2))
        _dark_title(win, th["sf"])

        sf = th["sf"]
        top = tk.Frame(win, bg=sf)
        top.pack(side="top", fill="x")
        row = tk.Frame(top, bg=sf)
        row.pack(fill="x", padx=round(10 * k), pady=round(8 * k))
        tk.Frame(top, bg=HAIRLINE, height=1).pack(fill="x")

        def sep():
            tk.Frame(row, bg=HAIRLINE, width=1).pack(side="left", fill="y", padx=round(9 * k), pady=round(6 * k))

        self.tool_btns = {}
        for key, name, letter in TOOLS:
            b = ImgBtn(row, lambda st, key=key: self._icon_img(key, st), sf, lambda key=key: self._set_tool(key),
                       lambda key=key: self._tool_hint(key), self._on_hint)
            b.w.pack(side="left", padx=1)
            self.tool_btns[key] = b
        sep()
        self.color_btns = {}
        for c in COLORS:
            b = ImgBtn(row, lambda st, c=c: self._swatch_img(c, st), sf, lambda c=c: self._set_color(c))
            b.w.pack(side="left")
            self.color_btns[c] = b
        sep()
        self.width_btns = {}
        for wd in WIDTHS:
            b = ImgBtn(row, lambda st, wd=wd: self._width_img(wd, st), sf, lambda wd=wd: self._set_width(wd))
            b.w.pack(side="left", padx=1)
            self.width_btns[wd] = b
        self.redo_btn = ImgBtn(row, lambda st: self._icon_img("redo", st), sf, self._redo,
                               lambda: "%s  ·  Ctrl+Y" % SS("t_redo"), self._on_hint)
        self.redo_btn.w.pack(side="right", padx=1)
        self.undo_btn = ImgBtn(row, lambda st: self._icon_img("undo", st), sf, self._undo,
                               lambda: "%s  ·  Ctrl+Z" % SS("t_undo"), self._on_hint)
        self.undo_btn.w.pack(side="right", padx=1)

        bottom = tk.Frame(win, bg=sf)
        bottom.pack(side="bottom", fill="x")
        tk.Frame(bottom, bg=HAIRLINE, height=1).pack(fill="x")
        brow = tk.Frame(bottom, bg=sf)
        brow.pack(fill="x", padx=round(14 * k), pady=round(9 * k))
        ImgBtn(brow, lambda st: self._label_img(SS("copy"), True, st), sf, self.copy).w.pack(side="right")
        ImgBtn(brow, lambda st: self._label_img(SS("save_as"), False, st), sf,
               self.save_as).w.pack(side="right", padx=(0, round(8 * k)))
        self.size_lbl = tk.Label(brow, text="", bg=sf, fg=th["tx"], font=th["font_sm"])
        self.size_lbl.pack(side="left")
        self.hint_lbl = tk.Label(brow, text="", bg=sf, fg=th["dim"], font=th["font_sm"], anchor="w")
        self.hint_lbl.pack(side="left", fill="x", expand=True, padx=(round(14 * k), round(10 * k)))

        cv = self.cv = tk.Canvas(win, bg=STAGE, highlightthickness=0, bd=0, cursor="crosshair")
        cv.pack(fill="both", expand=True)
        self.shadow_item = cv.create_image(0, 0, anchor="nw")
        self.img_item = cv.create_image(0, 0, anchor="nw")
        self.edge_item = cv.create_rectangle(0, 0, 0, 0, outline=HAIRLINE, width=1)
        self.mv_item = cv.create_image(0, 0, anchor="nw", state="hidden")
        self.hl_item = cv.create_rectangle(0, 0, 0, 0, outline=th["ac"], width=1, dash=(4, 3), state="hidden")
        cv.bind("<Configure>", lambda e: self._schedule_render())
        cv.bind("<ButtonPress-1>", self._press)
        cv.bind("<B1-Motion>", self._drag)
        cv.bind("<ButtonRelease-1>", self._release)
        cv.bind("<Motion>", self._motion)
        cv.bind("<ButtonPress-3>", self._right_click)
        cv.bind("<Leave>", lambda e: self._hover_clear())
        win.bind("<Delete>", lambda e: self._delete(self.hover) if self.hover is not None and not self.txt else None)
        win.bind("<Control-Key>", self._ctrl_key)
        win.bind("<Key>", self._key)
        win.bind("<Return>", lambda e: self.copy())
        win.bind("<Escape>", lambda e: self.close())
        self._set_tool("pen")
        self._set_color(self.color)
        self._set_width(self.width)
        self._history_btns()
        win.update_idletasks()
        take_foreground(user32.GetAncestor(win.winfo_id(), 2))    # else Ctrl+C / Enter go to the app below
        win.focus_force()

    # ---- toolbar pictures ----
    def _icon_img(self, name, st):
        th, k = self.th, self.k
        b = round(34 * k)
        fill = {"on": th["ac"], "hover": th["hv"]}.get(st)
        ink = {"on": "#ffffff", "hover": th["tx"], "off": ICON_OFF}.get(st, ICON_IDLE)
        base = _pill(b, b, th["sf"], fill, round(8 * k))
        n = round(20 * k)
        o = (b - n) // 2
        base.paste(_hex_rgb(ink), (o, o, o + n, o + n), _icon_mask(name, n))
        return base

    def _swatch_img(self, c, st):
        th, k = self.th, self.k
        n = round(28 * k)
        ring = {"on": th["tx"], "hover": th["dim"]}.get(st)
        base = Image.new("RGB", (n, n), _hex_rgb(th["sf"]))
        mid, r = n / 2, 8 * k

        def disc(rad):
            return lambda d, s: d.ellipse([(mid - rad) * s, (mid - rad) * s, (mid + rad) * s, (mid + rad) * s], fill=255)

        def circle(d, s):
            rad = 12 * k
            d.ellipse([(mid - rad) * s, (mid - rad) * s, (mid + rad) * s, (mid + rad) * s], outline=255,
                      width=round(1.7 * k * s))
        if ring:
            base.paste(_hex_rgb(ring), (0, 0, n, n), _aa_mask(n, n, circle))
        base.paste(_hex_rgb(HAIRLINE_LIGHT), (0, 0, n, n), _aa_mask(n, n, disc(r + 0.7)))
        base.paste(_hex_rgb(c), (0, 0, n, n), _aa_mask(n, n, disc(r)))
        return base

    def _width_img(self, wd, st):
        th, k = self.th, self.k
        h, w = round(34 * k), round(36 * k)
        fill = {"on": SELECTED, "hover": th["hv"]}.get(st)
        base = _pill(w, h, th["sf"], fill, round(8 * k))
        ink = _hex_rgb(th["tx"] if st != "idle" else ICON_IDLE)
        if self.tool == "text":
            px = round({WIDTHS[0]: 12, WIDTHS[1]: 16, WIDTHS[2]: 21}[wd] * k)
            ImageDraw.Draw(base).text((w / 2, h / 2), "A", font=_font(px), fill=ink, anchor="mm")
            return base
        t = {WIDTHS[0]: 1.5, WIDTHS[1]: 3.0, WIDTHS[2]: 6.0}[wd] * k

        def bar(d, s):
            x1, x2, y = 10 * k * s, (w - 10 * k) * s, h / 2 * s
            d.line([(x1, y), (x2, y)], fill=255, width=round(t * s))
            for x in (x1, x2):
                d.ellipse([x - t * s / 2, y - t * s / 2, x + t * s / 2, y + t * s / 2], fill=255)
        base.paste(ink, (0, 0, w, h), _aa_mask(w, h, bar))
        return base

    def _label_img(self, text, primary, st):
        th, k = self.th, self.k
        font = _font(round(13 * k))
        h = round(34 * k)
        w = round(font.getlength(text)) + round(32 * k)
        fill = ((th["ac2"] if st == "hover" else th["ac"]) if primary
                else (th["hv"] if st == "hover" else th["sf2"]))
        base = _pill(w, h, th["sf"], fill, round(8 * k))
        ImageDraw.Draw(base).text((w / 2, h / 2), text, font=font, anchor="mm",
                                  fill=_hex_rgb("#ffffff" if primary else th["tx"]))
        return base

    # ---- toolbar state ----
    def _tool_hint(self, key):
        name, letter = next((n, l) for k, n, l in TOOLS if k == key)
        parts = [SS(name), letter]
        extra = TOOL_HINTS.get(key)
        if extra:
            parts.append(SS(extra))
        return "  ·  ".join(parts)

    def _on_hint(self, text):
        self.hover_hint = text
        self._show_hint()

    def _show_hint(self):
        text = self.hover_hint or (SS("h_typing") if self.txt else
                                   SS("h_mark") if self.hover is not None else self._tool_hint(self.tool))
        self.hint_lbl.configure(text=text)

    def _set_tool(self, key):
        if self.txt:
            self._text_commit()
        was_text = self.tool == "text"
        self.tool = key
        for k, b in self.tool_btns.items():
            b.set(on=k == key)
        if was_text != (key == "text"):
            for b in self.width_btns.values():
                b.repaint()
        self._hover_clear()

    def _tool_cursor(self):
        return "xterm" if self.tool == "text" else "crosshair"

    def _set_color(self, c):
        self.color = c
        for k, b in self.color_btns.items():
            b.set(on=k == c)
        if self.txt:
            self._text_style()

    def _set_width(self, w):
        self.width = w
        for k, b in self.width_btns.items():
            b.set(on=k == w)
        if self.txt:
            self._text_style()

    def _history_btns(self):
        self.undo_btn.set(enabled=bool(self.undo))
        self.redo_btn.set(enabled=bool(self.redo))

    def _ctrl_key(self, e):
        if self.txt:
            return None                   # the text box has its own Ctrl keys
        code = e.keycode
        if code == 0x5A:                  # Ctrl+Z
            self._undo()
        elif code == 0x59:                # Ctrl+Y
            self._redo()
        elif code == 0x53:                # Ctrl+S
            self.save_as()
        elif code == 0x43:                # Ctrl+C
            self.copy()
        else:
            return None
        return "break"

    def _key(self, e):
        # by key code, not by letter: the shortcuts must work on a Russian layout too
        if self.txt or e.state & 0x20004:              # typing, Ctrl or Alt
            return
        for key, _name, letter in TOOLS:
            if e.keycode == ord(letter):
                self._set_tool(key)
                return

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
        m = self.margin
        self.scale = max(0.02, min(1.0, (cw - 2 * m) / iw, (ch - 2 * m) / ih))
        dw, dh = max(1, round(iw * self.scale)), max(1, round(ih * self.scale))
        disp = self.img if self.scale >= 1.0 else self.img.resize((dw, dh), Image.BILINEAR)
        self.photo = ImageTk.PhotoImage(disp, master=cv)
        self.ox, self.oy = (cw - dw) // 2, (ch - dh) // 2
        cv.coords(self.img_item, self.ox, self.oy)
        cv.itemconfigure(self.img_item, image=self.photo)
        cv.coords(self.edge_item, self.ox - 1, self.oy - 1, self.ox + dw, self.oy + dh)
        self._render_shadow(dw, dh)
        self._show_size()

    def _render_shadow(self, dw, dh):
        k = self.k
        p, drop = round(30 * k), round(8 * k)
        if self._shadow_key != (dw, dh):
            self._shadow_key = (dw, dh)
            mask = Image.new("L", (dw + 2 * p, dh + 2 * p), 0)
            mask.paste(170, (p, p + drop, p + dw, p + dh + drop))
            mask = mask.filter(ImageFilter.GaussianBlur(14 * k))
            base = Image.new("RGB", mask.size, _hex_rgb(STAGE))
            base.paste((0, 0, 0), (0, 0), mask)
            self.shadow = ImageTk.PhotoImage(base, master=self.cv)
            self.cv.itemconfigure(self.shadow_item, image=self.shadow)
        self.cv.coords(self.shadow_item, self.ox - p, self.oy - p)

    def _show_size(self, box=None):
        iw, ih = self.img.size
        if box:
            text = "%d × %d" % box
        else:
            text = "%d × %d" % (iw, ih) + ("" if self.scale >= 1.0 else "  ·  %d %%" % round(self.scale * 100))
        self.size_lbl.configure(text=text)

    def _to_img(self, x, y):
        iw, ih = self.img.size
        return (max(0.0, min(iw, (x - self.ox) / self.scale)), max(0.0, min(ih, (y - self.oy) / self.scale)))

    def _to_disp(self, x, y):
        return (self.ox + x * self.scale, self.oy + y * self.scale)

    # ---- marks: hover, delete, move ----
    def _hit(self, p):
        if self.tool == "crop":
            return None
        tol = HIT_TOL / self.scale
        return next((o for o in reversed(self.marks) if mark_hit(o, p, tol)), None)

    def _motion(self, e):
        if self.photo is None or self.item is not None or self.mv or self.txt:
            return
        o = self._hit(self._to_img(e.x, e.y))
        if o is None:
            if self.hover is not None:
                self._hover_clear()
        elif o is not self.hover:
            self._hover_set(o)

    def _right_click(self, e):
        if self.photo is None or self.item is not None or self.mv or self.txt:
            return
        o = self._hit(self._to_img(e.x, e.y))
        if o is not None:
            self._delete(o)

    def _hover_set(self, o):
        self.hover = o
        l, t, r, b = mark_bbox(o)
        (l, t), (r, b) = self._to_disp(l, t), self._to_disp(r, b)
        self.cv.coords(self.hl_item, l - 4, t - 4, r + 4, b + 4)
        self.cv.itemconfigure(self.hl_item, state="normal")
        self.cv.configure(cursor="fleur")
        self._show_hint()

    def _hover_clear(self):
        self.hover = None
        self.cv.itemconfigure(self.hl_item, state="hidden")
        self.cv.configure(cursor=self._tool_cursor())
        self._show_hint()

    def _delete(self, o):
        self._push()
        self.marks = [m for m in self.marks if m is not o]
        self._hover_clear()
        self._apply()

    def _move_begin(self, o, p):
        """The picture without the mark goes to the canvas once and the mark rides above
        it as one image: a move only changes its coordinates, so it stays smooth."""
        self._hover_clear()
        iw, ih = self.img.size
        l, t, r, b = mark_bbox(o)
        pad = 3
        box = (max(0, int(l) - pad), max(0, int(t) - pad), min(iw, int(r) + pad + 1), min(ih, int(b) + pad + 1))
        self.mv = {"o": o, "p": p, "d": (0.0, 0.0), "at": self._to_disp(box[0], box[1])}
        if box[2] > box[0] and box[3] > box[1]:
            layer = Image.new("RGBA", self.img.size, o["rgb"] + (0,))
            paint_mark(layer, o)
            layer = layer.crop(box)
            if self.scale < 1.0:
                layer = layer.resize((max(1, round(layer.width * self.scale)), max(1, round(layer.height * self.scale))),
                                     Image.BILINEAR)
            self.mv_photo = ImageTk.PhotoImage(layer, master=self.cv)
            self.cv.itemconfigure(self.mv_item, image=self.mv_photo, state="normal")
            self.cv.coords(self.mv_item, *self.mv["at"])
        self._compose(skip=o)
        self._render()
        self.cv.configure(cursor="fleur")

    def _move_drag(self, e):
        mv = self.mv
        p = self._to_img(e.x, e.y)
        mv["d"] = (p[0] - mv["p"][0], p[1] - mv["p"][1])
        self.cv.coords(self.mv_item, mv["at"][0] + mv["d"][0] * self.scale, mv["at"][1] + mv["d"][1] * self.scale)

    def _move_end(self, e):
        self._move_drag(e)
        mv, self.mv = self.mv, None
        self.cv.itemconfigure(self.mv_item, state="hidden")
        self.mv_photo = None
        if math.hypot(*mv["d"]) >= 1:
            self._push()
            moved = mark_moved(mv["o"], *mv["d"])
            self.marks = [moved if m is mv["o"] else m for m in self.marks]
        self._apply()
        self.cv.configure(cursor=self._tool_cursor())

    # ---- drawing ----
    def _press(self, e):
        if self.photo is None:
            return
        if self.txt:                      # a click outside the box finishes the text
            self._text_commit()
            return
        p = self._to_img(e.x, e.y)
        o = self._hit(p)
        if o is not None:
            self._move_begin(o, p)
            return
        self._hover_clear()
        if self.tool == "text":
            self._text_begin(p)
            return
        self.pts = [p]
        x, y = self._to_disp(*self.pts[0])
        cv = self.cv
        w = max(1.0, self.width * self.scale)
        if self.tool == "pen":
            self.item = cv.create_line(x, y, x, y, fill=self.color, width=w, capstyle="round", joinstyle="round")
        elif self.tool == "marker":
            self.item = cv.create_line(x, y, x, y, fill=self.color, width=w * 3, capstyle="round",
                                       joinstyle="round", stipple="gray50")
        elif self.tool == "line":
            self.item = cv.create_line(x, y, x, y, fill=self.color, width=w, capstyle="round")
        elif self.tool == "rect":
            self.item = cv.create_rectangle(x, y, x, y, outline=self.color, width=w)
        elif self.tool == "ellipse":
            self.item = cv.create_oval(x, y, x, y, outline=self.color, width=w)
        elif self.tool == "arrow":
            a = self._arrow_dims()
            self.item = cv.create_line(x, y, x, y, fill=self.color, width=w, arrow="last", capstyle="round",
                                       arrowshape=tuple(v * self.scale for v in a))
        elif self.tool == "crop":
            self.item_under = cv.create_rectangle(x, y, x, y, outline="#000000", width=1)
            self.item = cv.create_rectangle(x, y, x, y, outline="#ffffff", width=1, dash=(6, 4))

    def _drag(self, e):
        if self.mv:
            self._move_drag(e)
            return
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
            return
        if getattr(e, "state", 0) & 0x1:                     # Shift
            p = constrain(self.tool, self.pts[0], p)
        self.pts = [self.pts[0], p]
        x1, y1 = self._to_disp(*self.pts[0])
        x2, y2 = self._to_disp(*p)
        self.cv.coords(self.item, x1, y1, x2, y2)
        if self.item_under:
            self.cv.coords(self.item_under, x1, y1, x2, y2)
        if self.tool in ("rect", "ellipse", "crop"):
            self._show_size((abs(p[0] - self.pts[0][0]), abs(p[1] - self.pts[0][1])))

    def _release(self, e):
        if self.mv:
            self._move_end(e)
            return
        if self.item is None:
            return
        self._drag(e)
        self.cv.delete(self.item)
        if self.item_under:
            self.cv.delete(self.item_under)
        self.item = self.item_under = None
        try:
            self._commit()
        except Exception:
            self.ctl.log("screenshot edit failed:\n" + traceback.format_exc())
        self.pts = []
        self._show_size()

    def _arrow_dims(self):
        w = self.width
        head = 8 + 5 * w
        return (head * 0.72, head, 4 + 2.4 * w)             # also Tk arrowshape: d1, d2, d3 (image pixels)

    def _compose(self, skip=None):
        img = self.base.copy()
        for o in self.marks:
            if o is not skip:
                paint_mark(img, o)
        self.img = img

    def _apply(self):
        self._compose()
        self._render()

    def _push(self):
        self.undo.append((self.base, self.marks))
        del self.undo[:-UNDO_MAX]
        self.redo = []
        self._history_btns()

    def _add(self, mark):
        self._push()
        self.marks = self.marks + [mark]
        self._apply()

    def _undo(self):
        if not self.undo:
            return
        self.redo.append((self.base, self.marks))
        self.base, self.marks = self.undo.pop()
        self._hover_clear()
        self._history_btns()
        self._apply()

    def _redo(self):
        if not self.redo:
            return
        self.undo.append((self.base, self.marks))
        self.base, self.marks = self.redo.pop()
        self._hover_clear()
        self._history_btns()
        self._apply()

    def _commit(self):
        pts = self.pts
        if not pts:
            return
        rgb = _hex_rgb(self.color)
        w = self.width
        if self.tool == "pen":
            self._add({"k": "stroke", "pts": list(pts), "rgb": rgb, "w": w})
            return
        if self.tool == "marker":
            self._add({"k": "stroke", "pts": list(pts), "rgb": rgb, "w": w * 3, "a": MARKER_ALPHA})
            return
        if len(pts) < 2 or pts[0] == pts[1]:
            return
        if self.tool == "crop":
            iw, ih = self.img.size
            x1, y1, x2, y2 = _box(pts[0], pts[1])
            box = (max(0, int(x1)), max(0, int(y1)), min(iw, int(round(x2))), min(ih, int(round(y2))))
            if box[2] - box[0] < 2 or box[3] - box[1] < 2:
                return
            self._push()
            self.base = self.base.crop(box)
            self.marks = [mark_moved(o, -box[0], -box[1]) for o in self.marks]
            self._apply()
            return
        mark = {"pts": list(pts), "rgb": rgb, "w": w}
        if self.tool == "arrow":
            mark.update(k="arrow", dims=self._arrow_dims())
        else:
            mark["k"] = {"line": "stroke", "rect": "rect", "ellipse": "ellipse"}[self.tool]
        self._add(mark)

    # ---- text: a real tk.Text over the picture (caret, selection, IME, paste),
    #      drawn into the image with the same font when it is finished ----
    def _text_begin(self, p):
        self.txt_pos = p
        self.txt_font = tkfont.Font(family="Segoe UI Semibold", size=-self._text_px())
        ac = self.th["ac"]
        t = self.txt = tk.Text(self.cv, font=self.txt_font, bd=0, padx=TEXT_PAD, pady=TEXT_PAD, wrap="none",
                               undo=True, width=1, height=1, insertwidth=2, highlightthickness=1,
                               highlightbackground=ac, highlightcolor=ac, selectbackground=ac,
                               selectforeground="#ffffff")
        x, y = self._to_disp(*p)
        self.txt_item = self.cv.create_window(x - TEXT_PAD - 1, y - TEXT_PAD - 1, window=t, anchor="nw")
        self._text_style()
        t.bind("<Return>", self._text_enter)
        t.bind("<Shift-Return>", self._text_newline)
        t.bind("<Escape>", self._text_escape)
        t.bind("<Control-Key>", self._text_ctrl)
        t.bind("<<Modified>>", self._text_changed)
        t.focus_set()
        self._show_hint()

    def _text_px(self):
        return max(8, round(TEXT_SIZES[self.width] * self.scale))

    def _text_style(self):
        t = self.txt
        self.txt_font.configure(size=-self._text_px())
        bg = self._text_bg()
        t.configure(fg=self.color, insertbackground=self._text_caret(bg), bg="#%02x%02x%02x" % bg)
        self._text_fit()

    def _text_bg(self):
        """The box takes the colour of the picture under it, so the text looks as it will
        when finished: always the chosen palette colour, never replaced or backed."""
        x, y = self.txt_pos
        px = TEXT_SIZES[self.width]
        iw, ih = self.img.size
        box = (int(min(x, iw - 1)), int(min(y, ih - 1)), int(min(iw, x + px * 6)) or 1, int(min(ih, y + px * 1.4)) or 1)
        if box[2] <= box[0] or box[3] <= box[1]:
            box = (0, 0, 1, 1)
        return self.img.crop(box).resize((1, 1), Image.BOX).getpixel((0, 0))

    def _text_caret(self, bg):
        """Only the caret may differ from the text colour: it must stay visible."""
        if abs(_lum(bg) - _lum(_hex_rgb(self.color))) >= 0.3:
            return self.color
        return "#101012" if _lum(bg) > 0.5 else "#f4f4f5"

    def _text_fit(self):
        lines = self.txt.get("1.0", "end-1c").split("\n")
        f = self.txt_font
        w = max([f.measure(s) for s in lines] + [f.measure("00")]) + f.measure("0") + 2 * TEXT_PAD + 2
        h = f.metrics("linespace") * len(lines) + 2 * TEXT_PAD + 2
        self.cv.itemconfigure(self.txt_item, width=w, height=h)

    def _text_changed(self, _e):
        if self.txt and self.txt.edit_modified():
            self.txt.edit_modified(False)
            self._text_fit()

    def _text_enter(self, _e):
        self._text_commit()
        return "break"

    def _text_newline(self, _e):
        self.txt.insert("insert", "\n")
        return "break"

    def _text_escape(self, _e):
        self._text_close()
        return "break"

    def _text_ctrl(self, e):
        # by key code: Tk's own Ctrl+V / Ctrl+C do not fire on a Russian layout
        t, code = self.txt, e.keycode
        try:
            if code == 0x41:
                t.tag_add("sel", "1.0", "end-1c")
            elif code == 0x43:
                t.event_generate("<<Copy>>")
            elif code == 0x56:
                t.event_generate("<<Paste>>")
            elif code == 0x58:
                t.event_generate("<<Cut>>")
            elif code == 0x5A:
                t.edit_undo()
            elif code == 0x59:
                t.edit_redo()
            elif code == 0x53:
                self.save_as()
            else:
                return None
        except tk.TclError:               # nothing to undo
            pass
        return "break"

    def _text_close(self):
        self.cv.delete(self.txt_item)
        self.txt.destroy()
        self.txt = self.txt_item = None
        self.win.focus_set()
        self._show_hint()

    def _text_commit(self):
        text = self.txt.get("1.0", "end-1c").rstrip()
        self._text_close()
        if text.strip():
            self._add({"k": "text", "pts": [self.txt_pos], "rgb": _hex_rgb(self.color), "w": 0, "text": text,
                       "px": TEXT_SIZES[self.width]})

    # ---- results ----
    def copy(self):
        if self.txt:
            self._text_commit()
        if self.ctl.copy_image(self.img, self.path):
            self.close()

    def save_as(self):
        if self.txt:
            self._text_commit()
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
        self.undo, self.redo = [], []
        self.photo = self.shadow = self.mv_photo = None


def _lum(rgb):
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0


def _dark_title(win, colour):
    """Dark title bar in the colour of the toolbar (the colour needs Windows 11)."""
    try:
        win.update_idletasks()
        hwnd = wintypes.HWND(user32.GetParent(win.winfo_id()) or win.winfo_id())
        dwm = ctypes.WinDLL("dwmapi")
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)      # DWMWA_USE_IMMERSIVE_DARK_MODE
        r, g, b = _hex_rgb(colour)
        rgb = ctypes.c_uint(r | g << 8 | b << 16)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(rgb), 4)     # DWMWA_CAPTION_COLOR
    except Exception:
        pass
