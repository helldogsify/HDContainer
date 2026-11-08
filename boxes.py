# -*- coding: utf-8 -*-
"""Desktop boxes: panels on the desktop that hold files.

Each box is a layered child window of the desktop icon layer (the window that hosts
SHELLDLL_DefView), so it sits above the icons, below every app window and survives
Win+D. A box shows the files of its own folder (Desktop\\HDContainer Boxes\\<name>)
or of any chosen folder. Pixels come from Pillow through UpdateLayeredWindow;
icons and thumbnails come from the shell on a background thread.

Input arrives in the window procedure. It never calls tkinter: dialogs, menus and
drag-and-drop are started from the app's deferred queue (a tk timer).
"""
import os
import re
import json
import math
import time
import queue
import shutil
import ctypes
import hashlib
import threading
import collections
import configparser
from ctypes import wintypes
import tkinter as tk
from tkinter import filedialog

from PIL import Image, ImageDraw, ImageFont, ImageChops

# ---------------------------------------------------------------------------
# strings
# ---------------------------------------------------------------------------
BSTR = {
    "tab": {"en": "Boxes", "ru": "Ящики", "es": "Cajas", "pt": "Caixas", "de": "Boxen", "fr": "Boîtes", "zh": "收纳盒"},
    "new_box": {"en": "New box", "ru": "Новый ящик", "es": "Nueva caja", "pt": "Nova caixa", "de": "Neue Box", "fr": "Nouvelle boîte", "zh": "新建收纳盒"},
    "new_folder_box": {"en": "New box for a folder…", "ru": "Ящик для папки…", "es": "Caja para una carpeta…", "pt": "Caixa para uma pasta…", "de": "Box für einen Ordner…", "fr": "Boîte pour un dossier…", "zh": "为文件夹新建收纳盒…"},
    "box_n": {"en": "Box %d", "ru": "Ящик %d", "es": "Caja %d", "pt": "Caixa %d", "de": "Box %d", "fr": "Boîte %d", "zh": "收纳盒 %d"},
    "rename": {"en": "Rename…", "ru": "Переименовать…", "es": "Renombrar…", "pt": "Renomear…", "de": "Umbenennen…", "fr": "Renommer…", "zh": "重命名…"},
    "name_prompt": {"en": "Box name", "ru": "Название ящика", "es": "Nombre de la caja", "pt": "Nome da caixa", "de": "Name der Box", "fr": "Nom de la boîte", "zh": "收纳盒名称"},
    "file_name": {"en": "New name", "ru": "Новое имя", "es": "Nuevo nombre", "pt": "Novo nome", "de": "Neuer Name", "fr": "Nouveau nom", "zh": "新名称"},
    "appearance_menu": {"en": "Appearance…", "ru": "Оформление…", "es": "Apariencia…", "pt": "Aparência…", "de": "Aussehen…", "fr": "Apparence…", "zh": "外观…"},
    "rollup": {"en": "Roll up into the title", "ru": "Сворачивать в заголовок", "es": "Plegar en el título", "pt": "Recolher no título", "de": "In die Titelleiste einklappen", "fr": "Replier dans le titre", "zh": "折叠为标题栏"},
    "lock": {"en": "Lock position and size", "ru": "Закрепить положение", "es": "Bloquear posición", "pt": "Travar posição", "de": "Position sperren", "fr": "Verrouiller la position", "zh": "锁定位置"},
    "no_quickhide": {"en": "Don't hide on double-click", "ru": "Не прятать двойным кликом", "es": "No ocultar con doble clic", "pt": "Não ocultar com clique duplo", "de": "Bei Doppelklick nicht ausblenden", "fr": "Ne pas masquer au double-clic", "zh": "双击时不隐藏"},
    "refresh": {"en": "Refresh icons", "ru": "Обновить значки", "es": "Actualizar iconos", "pt": "Atualizar ícones", "de": "Symbole aktualisieren", "fr": "Actualiser les icônes", "zh": "刷新图标"},
    "sort": {"en": "Sort", "ru": "Сортировка", "es": "Ordenar", "pt": "Ordenar", "de": "Sortieren", "fr": "Trier", "zh": "排序"},
    "sort_name": {"en": "By name", "ru": "По имени", "es": "Por nombre", "pt": "Por nome", "de": "Nach Name", "fr": "Par nom", "zh": "按名称"},
    "sort_type": {"en": "By type", "ru": "По типу", "es": "Por tipo", "pt": "Por tipo", "de": "Nach Typ", "fr": "Par type", "zh": "按类型"},
    "sort_date": {"en": "By date", "ru": "По дате", "es": "Por fecha", "pt": "Por data", "de": "Nach Datum", "fr": "Par date", "zh": "按日期"},
    "sort_manual": {"en": "Custom order", "ru": "Свой порядок", "es": "Orden propio", "pt": "Ordem própria", "de": "Eigene Reihenfolge", "fr": "Ordre personnalisé", "zh": "自定义顺序"},
    "paste": {"en": "Paste", "ru": "Вставить", "es": "Pegar", "pt": "Colar", "de": "Einfügen", "fr": "Coller", "zh": "粘贴"},
    "open_folder": {"en": "Open box folder", "ru": "Открыть папку ящика", "es": "Abrir la carpeta de la caja", "pt": "Abrir a pasta da caixa", "de": "Box-Ordner öffnen", "fr": "Ouvrir le dossier de la boîte", "zh": "打开收纳盒文件夹"},
    "delete_box": {"en": "Delete box", "ru": "Удалить ящик", "es": "Eliminar caja", "pt": "Excluir caixa", "de": "Box löschen", "fr": "Supprimer la boîte", "zh": "删除收纳盒"},
    "delete_q": {"en": "Delete box “%s”? Its files go back to the desktop.", "ru": "Удалить ящик «%s»? Файлы из него вернутся на рабочий стол.", "es": "¿Eliminar la caja «%s»? Sus archivos vuelven al escritorio.", "pt": "Excluir a caixa “%s”? Os arquivos voltam para a área de trabalho.", "de": "Box „%s“ löschen? Ihre Dateien kommen zurück auf den Desktop.", "fr": "Supprimer la boîte « %s » ? Ses fichiers reviennent sur le bureau.", "zh": "删除收纳盒“%s”？其中的文件会回到桌面。"},
    "delete_q_folder": {"en": "Remove box “%s”? The folder itself stays as it is.", "ru": "Убрать ящик «%s»? Сама папка останется как есть.", "es": "¿Quitar la caja «%s»? La carpeta se queda como está.", "pt": "Remover a caixa “%s”? A pasta continua como está.", "de": "Box „%s“ entfernen? Der Ordner selbst bleibt unverändert.", "fr": "Retirer la boîte « %s » ? Le dossier reste tel quel.", "zh": "移除收纳盒“%s”？文件夹本身保持不变。"},
    "to_desktop": {"en": "Move to the desktop", "ru": "Вынести на рабочий стол", "es": "Mover al escritorio", "pt": "Mover para a área de trabalho", "de": "Auf den Desktop verschieben", "fr": "Déplacer sur le bureau", "zh": "移到桌面"},
    "empty": {"en": "Drop files here", "ru": "Перетащите сюда файлы", "es": "Suelta archivos aquí", "pt": "Solte arquivos aqui", "de": "Dateien hierher ziehen", "fr": "Déposez des fichiers ici", "zh": "将文件拖到这里"},
    "missing": {"en": "Folder not found", "ru": "Папка не найдена", "es": "Carpeta no encontrada", "pt": "Pasta não encontrada", "de": "Ordner nicht gefunden", "fr": "Dossier introuvable", "zh": "找不到文件夹"},
    "appearance": {"en": "Appearance", "ru": "Внешний вид", "es": "Apariencia", "pt": "Aparência", "de": "Aussehen", "fr": "Apparence", "zh": "外观"},
    "for": {"en": "Settings for", "ru": "Настройки для", "es": "Ajustes para", "pt": "Ajustes para", "de": "Einstellungen für", "fr": "Réglages pour", "zh": "设置对象"},
    "all_boxes": {"en": "All boxes", "ru": "Все ящики", "es": "Todas las cajas", "pt": "Todas as caixas", "de": "Alle Boxen", "fr": "Toutes les boîtes", "zh": "所有收纳盒"},
    "bg_color": {"en": "Background color", "ru": "Цвет фона", "es": "Color de fondo", "pt": "Cor de fundo", "de": "Hintergrundfarbe", "fr": "Couleur du fond", "zh": "背景颜色"},
    "bg_transp": {"en": "Background transparency: %d%%", "ru": "Прозрачность фона: %d%%", "es": "Transparencia del fondo: %d%%", "pt": "Transparência do fundo: %d%%", "de": "Hintergrund-Transparenz: %d%%", "fr": "Transparence du fond : %d %%", "zh": "背景透明度：%d%%"},
    "hover_transp": {"en": "Transparency under the cursor: %d%%", "ru": "Прозрачность под курсором: %d%%", "es": "Transparencia bajo el cursor: %d%%", "pt": "Transparência sob o cursor: %d%%", "de": "Transparenz unter dem Mauszeiger: %d%%", "fr": "Transparence sous le curseur : %d %%", "zh": "鼠标悬停时透明度：%d%%"},
    "title_font": {"en": "Title font", "ru": "Шрифт заголовка", "es": "Fuente del título", "pt": "Fonte do título", "de": "Titelschrift", "fr": "Police du titre", "zh": "标题字体"},
    "title_color": {"en": "Title color", "ru": "Цвет заголовка", "es": "Color del título", "pt": "Cor do título", "de": "Titelfarbe", "fr": "Couleur du titre", "zh": "标题颜色"},
    "bold": {"en": "Bold", "ru": "Жирный", "es": "Negrita", "pt": "Negrito", "de": "Fett", "fr": "Gras", "zh": "粗体"},
    "size": {"en": "Size", "ru": "Размер", "es": "Tamaño", "pt": "Tamanho", "de": "Größe", "fr": "Taille", "zh": "大小"},
    "change": {"en": "Change", "ru": "Изменить", "es": "Cambiar", "pt": "Alterar", "de": "Ändern", "fr": "Modifier", "zh": "更改"},
    "reset_box": {"en": "Same as all boxes", "ru": "Как у всех ящиков", "es": "Igual que todas", "pt": "Igual a todas", "de": "Wie alle Boxen", "fr": "Comme toutes les boîtes", "zh": "与所有收纳盒相同"},
    "round": {"en": "Rounded corners", "ru": "Скруглённые углы", "es": "Esquinas redondeadas", "pt": "Cantos arredondados", "de": "Abgerundete Ecken", "fr": "Coins arrondis", "zh": "圆角"},
    "border": {"en": "Border around boxes", "ru": "Рамка вокруг ящиков", "es": "Borde alrededor de las cajas", "pt": "Borda em volta das caixas", "de": "Rahmen um die Boxen", "fr": "Bordure autour des boîtes", "zh": "收纳盒边框"},
    "badge": {"en": "Count new files on the title", "ru": "Счётчик новых файлов на заголовке", "es": "Contar archivos nuevos en el título", "pt": "Contar arquivos novos no título", "de": "Neue Dateien im Titel zählen", "fr": "Compter les nouveaux fichiers dans le titre", "zh": "在标题上显示新文件数"},
    "icon_size": {"en": "Icons", "ru": "Значки", "es": "Iconos", "pt": "Ícones", "de": "Symbole", "fr": "Icônes", "zh": "图标"},
    "icons_s": {"en": "Small", "ru": "Маленькие", "es": "Pequeños", "pt": "Pequenos", "de": "Klein", "fr": "Petites", "zh": "小"},
    "icons_m": {"en": "Medium", "ru": "Средние", "es": "Medianos", "pt": "Médios", "de": "Mittel", "fr": "Moyennes", "zh": "中"},
    "icons_l": {"en": "Large", "ru": "Крупные", "es": "Grandes", "pt": "Grandes", "de": "Groß", "fr": "Grandes", "zh": "大"},
    "behavior": {"en": "Behavior", "ru": "Поведение", "es": "Comportamiento", "pt": "Comportamento", "de": "Verhalten", "fr": "Comportement", "zh": "行为"},
    "snap": {"en": "Line boxes up when moving", "ru": "Выстраивать ящики при перемещении", "es": "Alinear las cajas al moverlas", "pt": "Alinhar as caixas ao mover", "de": "Boxen beim Verschieben ausrichten", "fr": "Aligner les boîtes en les déplaçant", "zh": "移动时排列收纳盒"},
    "snap_note": {"en": "They stick to each other with the same gap and line up by their edges.", "ru": "Прилипают друг к другу с одинаковым отступом и выравниваются по краям.", "es": "Se pegan entre sí con el mismo hueco y se alinean por los bordes.", "pt": "Grudam umas nas outras com o mesmo espaço e se alinham pelas bordas.", "de": "Sie haften mit gleichem Abstand aneinander und richten ihre Kanten aus.", "fr": "Elles se collent avec le même écart et s’alignent par les bords.", "zh": "以相同间距相互吸附，并按边缘对齐。"},
    "quickhide": {"en": "Double-click the empty desktop to hide boxes and icons", "ru": "Двойной клик по рабочему столу прячет ящики и значки", "es": "Doble clic en el escritorio vacío oculta cajas e iconos", "pt": "Clique duplo na área de trabalho vazia oculta caixas e ícones", "de": "Doppelklick auf den leeren Desktop blendet Boxen und Symbole aus", "fr": "Double-clic sur le bureau vide : masquer boîtes et icônes", "zh": "双击桌面空白处隐藏收纳盒和图标"},
    "arrange": {"en": "Arrange", "ru": "Расстановка", "es": "Colocación", "pt": "Disposição", "de": "Anordnung", "fr": "Disposition", "zh": "排列"},
    "box_w": {"en": "Box width", "ru": "Ширина ящика", "es": "Ancho de caja", "pt": "Largura da caixa", "de": "Boxbreite", "fr": "Largeur des boîtes", "zh": "收纳盒宽度"},
    "gap": {"en": "Gap", "ru": "Отступ", "es": "Espacio", "pt": "Espaço", "de": "Abstand", "fr": "Écart", "zh": "间距"},
    "arr_top": {"en": "Along the top", "ru": "Вверху", "es": "Arriba", "pt": "No topo", "de": "Oben", "fr": "En haut", "zh": "顶部"},
    "arr_bottom": {"en": "Along the bottom", "ru": "Внизу", "es": "Abajo", "pt": "Embaixo", "de": "Unten", "fr": "En bas", "zh": "底部"},
    "up_note": {"en": "Boxes in the lower half of the screen open upwards, with the title at the bottom.", "ru": "Ящики в нижней половине экрана открываются вверх, заголовок у них снизу.", "es": "Las cajas de la mitad inferior de la pantalla se abren hacia arriba, con el título abajo.", "pt": "As caixas na metade de baixo da tela abrem para cima, com o título embaixo.", "de": "Boxen in der unteren Bildschirmhälfte öffnen nach oben, mit dem Titel unten.", "fr": "Les boîtes de la moitié basse de l’écran s’ouvrent vers le haut, titre en bas.", "zh": "位于屏幕下半部分的收纳盒向上展开，标题在底部。"},
    "arr_col": {"en": "In a column", "ru": "Столбцом", "es": "En columna", "pt": "Em coluna", "de": "Als Spalte", "fr": "En colonne", "zh": "成列"},
    "left": {"en": "Left", "ru": "Слева", "es": "Izquierda", "pt": "Esquerda", "de": "Links", "fr": "À gauche", "zh": "左"},
    "center": {"en": "Center", "ru": "По центру", "es": "Centro", "pt": "Centro", "de": "Mitte", "fr": "Au centre", "zh": "居中"},
    "right": {"en": "Right", "ru": "Справа", "es": "Derecha", "pt": "Direita", "de": "Rechts", "fr": "À droite", "zh": "右"},
    "arrange_note": {"en": "The width changes on all boxes at once. A click on a place puts all boxes there with the same gap between them and from the screen edge; while they stay there, the gap follows at once too.", "ru": "Ширина сразу меняется у всех ящиков. Клик по месту ставит туда все ящики с одинаковым отступом друг от друга и от края экрана; пока они там стоят, отступ тоже меняется сразу.", "es": "El ancho cambia en todas las cajas al momento. Un clic en un lugar coloca ahí todas las cajas con el mismo espacio entre ellas y hasta el borde; mientras sigan ahí, el espacio también cambia al momento.", "pt": "A largura muda em todas as caixas na hora. Um clique num lugar coloca todas as caixas ali com o mesmo espaço entre elas e até a borda; enquanto ficarem ali, o espaço também muda na hora.", "de": "Die Breite ändert sich sofort bei allen Boxen. Ein Klick auf einen Platz stellt alle Boxen dorthin, mit gleichem Abstand zueinander und zum Bildschirmrand; solange sie dort stehen, folgt auch der Abstand sofort.", "fr": "La largeur change tout de suite sur toutes les boîtes. Un clic sur une place y met toutes les boîtes avec le même écart entre elles et avec le bord ; tant qu’elles y restent, l’écart suit aussi tout de suite.", "zh": "宽度会立即应用到所有收纳盒。单击某个位置即可把所有收纳盒放到该处，彼此及与屏幕边缘的间距相同；只要它们还在那里，间距也会立即跟随调整。"},
    "import": {"en": "Import boxes from iTop Easy Desktop", "ru": "Перенести ящики из iTop Easy Desktop", "es": "Importar cajas de iTop Easy Desktop", "pt": "Importar caixas do iTop Easy Desktop", "de": "Boxen aus iTop Easy Desktop übernehmen", "fr": "Importer les boîtes d’iTop Easy Desktop", "zh": "从 iTop Easy Desktop 导入收纳盒"},
    "imported_n": {"en": "Imported boxes: %d. Drag your files into them.", "ru": "Перенесено ящиков: %d. Перетащите в них свои файлы.", "es": "Cajas importadas: %d. Arrastra tus archivos a ellas.", "pt": "Caixas importadas: %d. Arraste seus arquivos para elas.", "de": "Übernommene Boxen: %d. Zieh deine Dateien hinein.", "fr": "Boîtes importées : %d. Glissez-y vos fichiers.", "zh": "已导入收纳盒：%d。把文件拖进去即可。"},
    "itop_running": {"en": "Quit iTop Easy Desktop first, otherwise both programs will draw boxes.", "ru": "Сначала закройте iTop Easy Desktop, иначе ящики будут рисовать обе программы.", "es": "Cierra primero iTop Easy Desktop; si no, ambos programas dibujarán cajas.", "pt": "Feche o iTop Easy Desktop primeiro, senão os dois programas vão desenhar caixas.", "de": "Beende zuerst iTop Easy Desktop, sonst zeichnen beide Programme Boxen.", "fr": "Quittez d’abord iTop Easy Desktop, sinon les deux programmes dessineront des boîtes.", "zh": "请先退出 iTop Easy Desktop，否则两个程序都会绘制收纳盒。"},
    "where": {"en": "Box files are kept in", "ru": "Файлы ящиков лежат в", "es": "Los archivos de las cajas están en", "pt": "Os arquivos das caixas ficam em", "de": "Die Dateien der Boxen liegen in", "fr": "Les fichiers des boîtes sont dans", "zh": "收纳盒文件保存在"},
    "open": {"en": "Open", "ru": "Открыть", "es": "Abrir", "pt": "Abrir", "de": "Öffnen", "fr": "Ouvrir", "zh": "打开"},
}

LANG_GET = lambda: "en"


def B(key, *args):
    s = BSTR.get(key, {})
    txt = s.get(LANG_GET()) or s.get("en") or key
    return (txt % args) if args else txt


ROOT_NAME = "HDContainer Boxes"
BG_COLORS = [None, "#2b5ea7", "#4c8bf5", "#64d2ff", "#1a9e8f", "#34c759",
             "#ffd60a", "#ff9f0a", "#ff453a", "#ff375f", "#bf5af2", "#8e8e93"]
TITLE_COLORS = ["#f4f6fa", "#c9ced8", "#1d1f24", "#ffd60a", "#64d2ff", "#ff9f0a"]
STYLE_DEFAULT = {"color": None, "transp": 30, "hover_transp": 10, "font": "Segoe UI Semibold",
                 "font_size": 11, "bold": False, "title_color": "#f4f6fa"}
OPTS_DEFAULT = {"round": True, "border": False, "badge": True, "icon_size": 48, "snap": True, "quickhide": True,
                "box_w": 300, "gap": 12,           # box_w / gap: plain screen pixels, as the user sets them
                "layout": None}                    # the last Arrange place while the boxes keep it

# logical sizes (scaled by the window's DPI)
PAD, RADIUS, EDGE = 8, 10, 6
HOVER_OPEN_MS, LEAVE_CLOSE_MS, ANIM_MS, FRAME_MS = 50, 350, 170, 8
LIFT, GLIDE = 1.1, 18.0        # a dragged item grows by 10 %; others glide aside (rate per second)
ICON_RETRY_S = (5, 30)         # seconds before asking again for a picture the shell did not give
HEADER_EXTRA = 48          # an open box's header is this much denser (0..255), setting it apart
DATA_VERSION = 3
TICK_MS = 50
SCAN_EVERY = 2.0

# ---------------------------------------------------------------------------
# win32
# ---------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)

VP = ctypes.c_void_p
HWND, UINT, DWORD, BOOL, LONG = wintypes.HWND, wintypes.UINT, wintypes.DWORD, wintypes.BOOL, wintypes.LONG
WPARAM, LPARAM, HRESULT = wintypes.WPARAM, wintypes.LPARAM, ctypes.c_long
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)
ENUMPROC = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)
MONENUMPROC = ctypes.WINFUNCTYPE(BOOL, VP, VP, ctypes.POINTER(wintypes.RECT), LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)
POINT, RECT = wintypes.POINT, wintypes.RECT


def _d(fn, res, args):
    fn.restype, fn.argtypes = res, args
    return fn


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", UINT), ("style", UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", VP), ("hIcon", VP), ("hCursor", VP),
                ("hbrBackground", VP), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", VP)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", LONG), ("cy", LONG)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte), ("fmt", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", DWORD), ("biWidth", LONG), ("biHeight", LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", DWORD), ("biSizeImage", DWORD),
                ("biXPelsPerMeter", LONG), ("biYPelsPerMeter", LONG), ("biClrUsed", DWORD), ("biClrImportant", DWORD)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", LONG), ("bmWidth", LONG), ("bmHeight", LONG), ("bmWidthBytes", LONG),
                ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD), ("bmBits", VP)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("dwFlags", DWORD), ("hwndTrack", HWND), ("dwHoverTime", DWORD)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", DWORD)]


class GUID(ctypes.Structure):
    _fields_ = [("a", DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]


class FORMATETC(ctypes.Structure):
    _fields_ = [("cfFormat", wintypes.WORD), ("ptd", VP), ("dwAspect", DWORD), ("lindex", LONG), ("tymed", DWORD)]


class STGMEDIUM(ctypes.Structure):
    _fields_ = [("tymed", DWORD), ("hGlobal", VP), ("pUnkForRelease", VP)]


class CMINVOKECOMMANDINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("fMask", DWORD), ("hwnd", HWND), ("lpVerb", VP), ("lpParameters", VP),
                ("lpDirectory", VP), ("nShow", ctypes.c_int), ("dwHotKey", DWORD), ("hIcon", VP), ("lpTitle", VP),
                ("lpVerbW", VP), ("lpParametersW", VP), ("lpDirectoryW", VP), ("lpTitleW", VP), ("ptInvoke", POINT)]


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", HWND), ("wFunc", UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", BOOL), ("hNameMappings", VP),
                ("lpszProgressTitle", wintypes.LPCWSTR)]


class LVHITTESTINFO(ctypes.Structure):
    _fields_ = [("pt", POINT), ("flags", UINT), ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int),
                ("iGroup", ctypes.c_int)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", DWORD), ("flags", DWORD), ("time", DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class SHDRAGIMAGE(ctypes.Structure):
    _fields_ = [("size", SIZE), ("offset", POINT), ("hbmp", VP), ("crColorKey", DWORD)]


_d(user32.RegisterClassExW, ctypes.c_ushort, [VP])
_d(user32.CreateWindowExW, HWND, [DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, DWORD, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, HWND, VP, VP, VP])
_d(user32.DestroyWindow, BOOL, [HWND])
_d(user32.DefWindowProcW, LRESULT, [HWND, UINT, WPARAM, LPARAM])
_d(user32.SetWindowPos, BOOL, [HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, UINT])
_d(user32.ShowWindow, BOOL, [HWND, ctypes.c_int])
_d(user32.IsWindow, BOOL, [HWND])
_d(user32.IsWindowVisible, BOOL, [HWND])
_d(user32.GetWindowRect, BOOL, [HWND, ctypes.POINTER(RECT)])
_d(user32.SetParent, HWND, [HWND, HWND])
_d(user32.GetParent, HWND, [HWND])
_d(user32.GetWindowLongPtrW, ctypes.c_ssize_t, [HWND, ctypes.c_int])
_d(user32.GetWindow, HWND, [HWND, UINT])
_d(user32.FindWindowExW, HWND, [HWND, HWND, wintypes.LPCWSTR, wintypes.LPCWSTR])
_d(user32.EnumWindows, BOOL, [ENUMPROC, LPARAM])
_d(user32.UpdateLayeredWindow, BOOL, [HWND, VP, VP, VP, VP, VP, DWORD, VP, DWORD])
_d(user32.GetDC, VP, [HWND])
_d(user32.ReleaseDC, ctypes.c_int, [HWND, VP])
_d(user32.SetCapture, HWND, [HWND])
_d(user32.ReleaseCapture, BOOL, [])
_d(user32.GetCapture, HWND, [])
_d(user32.GetCursorPos, BOOL, [ctypes.POINTER(POINT)])
_d(user32.ScreenToClient, BOOL, [HWND, ctypes.POINTER(POINT)])
_d(user32.ClientToScreen, BOOL, [HWND, ctypes.POINTER(POINT)])
_d(user32.WindowFromPoint, HWND, [POINT])
_d(user32.GetAncestor, HWND, [HWND, UINT])
_d(user32.GetClassNameW, ctypes.c_int, [HWND, wintypes.LPWSTR, ctypes.c_int])
_d(user32.TrackMouseEvent, BOOL, [VP])
_d(user32.SetTimer, ctypes.c_size_t, [HWND, ctypes.c_size_t, UINT, VP])
_d(user32.KillTimer, BOOL, [HWND, ctypes.c_size_t])
_d(user32.SetCursor, VP, [VP])
_d(user32.LoadCursorW, VP, [VP, VP])
_d(user32.GetKeyState, ctypes.c_short, [ctypes.c_int])
_d(user32.GetDoubleClickTime, UINT, [])
_d(user32.GetSystemMetrics, ctypes.c_int, [ctypes.c_int])
_d(user32.EnumDisplayMonitors, BOOL, [VP, VP, MONENUMPROC, LPARAM])
_d(user32.GetMonitorInfoW, BOOL, [VP, VP])
_d(user32.MonitorFromPoint, VP, [POINT, DWORD])
_d(user32.CreatePopupMenu, VP, [])
_d(user32.AppendMenuW, BOOL, [VP, UINT, ctypes.c_size_t, VP])
_d(user32.DestroyMenu, BOOL, [VP])
_d(user32.SetMenuItemBitmaps, BOOL, [VP, UINT, UINT, VP, VP])
_d(user32.TrackPopupMenu, ctypes.c_int, [VP, UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, HWND, VP])
_d(user32.SetForegroundWindow, BOOL, [HWND])
_d(user32.PostMessageW, BOOL, [HWND, UINT, WPARAM, LPARAM])
_d(user32.SendMessageW, LRESULT, [HWND, UINT, WPARAM, LPARAM])
_d(user32.GetWindowThreadProcessId, DWORD, [HWND, ctypes.POINTER(DWORD)])
_d(user32.SetWindowsHookExW, VP, [ctypes.c_int, HOOKPROC, VP, DWORD])
_d(user32.CallNextHookEx, LRESULT, [VP, ctypes.c_int, WPARAM, LPARAM])
_d(user32.UnhookWindowsHookEx, BOOL, [VP])
_d(user32.GetMessageW, BOOL, [VP, HWND, UINT, UINT])
_d(user32.PostThreadMessageW, BOOL, [DWORD, UINT, WPARAM, LPARAM])
_d(user32.OpenClipboard, BOOL, [HWND])
_d(user32.CloseClipboard, BOOL, [])
_d(user32.GetClipboardData, VP, [UINT])
_d(user32.IsClipboardFormatAvailable, BOOL, [UINT])
_d(user32.RegisterClipboardFormatW, UINT, [wintypes.LPCWSTR])
_d(gdi32.CreateCompatibleDC, VP, [VP])
_d(gdi32.CreateDIBSection, VP, [VP, VP, UINT, ctypes.POINTER(VP), VP, DWORD])
_d(gdi32.SelectObject, VP, [VP, VP])
_d(gdi32.DeleteObject, BOOL, [VP])
_d(gdi32.DeleteDC, BOOL, [VP])
_d(gdi32.GetObjectW, ctypes.c_int, [VP, ctypes.c_int, VP])
_d(gdi32.GetDIBits, ctypes.c_int, [VP, VP, UINT, UINT, VP, VP, UINT])
_d(kernel32.GetModuleHandleW, VP, [wintypes.LPCWSTR])
_d(kernel32.GetCurrentThreadId, DWORD, [])
_d(kernel32.OpenProcess, VP, [DWORD, BOOL, DWORD])
_d(kernel32.CloseHandle, BOOL, [VP])
_d(kernel32.VirtualAllocEx, VP, [VP, VP, ctypes.c_size_t, DWORD, DWORD])
_d(kernel32.VirtualFreeEx, BOOL, [VP, VP, ctypes.c_size_t, DWORD])
_d(kernel32.WriteProcessMemory, BOOL, [VP, VP, VP, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)])
_d(kernel32.ReadProcessMemory, BOOL, [VP, VP, VP, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)])
_d(kernel32.SetFileAttributesW, BOOL, [wintypes.LPCWSTR, DWORD])
_d(kernel32.GlobalLock, VP, [VP])
_d(kernel32.GlobalUnlock, BOOL, [VP])
_d(shell32.SHGetKnownFolderPath, HRESULT, [VP, DWORD, VP, ctypes.POINTER(ctypes.c_wchar_p)])
_d(shell32.SHParseDisplayName, HRESULT, [wintypes.LPCWSTR, VP, ctypes.POINTER(VP), ctypes.c_ulong, VP])
_d(shell32.SHBindToParent, HRESULT, [VP, VP, ctypes.POINTER(VP), ctypes.POINTER(VP)])
_d(shell32.SHCreateItemFromParsingName, HRESULT, [wintypes.LPCWSTR, VP, VP, ctypes.POINTER(VP)])
_d(shell32.SHCreateItemFromIDList, HRESULT, [VP, VP, ctypes.POINTER(VP)])
_d(shell32.ShellExecuteW, VP, [HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int])
_d(shell32.ILFindLastID, VP, [VP])
_d(shell32.ILFree, None, [VP])
_d(shell32.ILCombine, VP, [VP, VP])
_d(shell32.ILGetSize, UINT, [VP])
_d(shell32.SHGetNameFromIDList, HRESULT, [VP, ctypes.c_uint, ctypes.POINTER(ctypes.c_wchar_p)])
_d(shell32.SHChangeNotify, None, [ctypes.c_long, UINT, VP, VP])
_d(shell32.DragQueryFileW, UINT, [VP, UINT, wintypes.LPWSTR, UINT])
_d(shell32.SHFileOperationW, ctypes.c_int, [VP])
_d(ole32.OleInitialize, HRESULT, [VP])
_d(ole32.DoDragDrop, HRESULT, [VP, VP, DWORD, ctypes.POINTER(DWORD)])
_d(ole32.RegisterDragDrop, HRESULT, [HWND, VP])
_d(ole32.RevokeDragDrop, HRESULT, [HWND])
_d(ole32.ReleaseStgMedium, None, [VP])
_d(ole32.CoInitializeEx, HRESULT, [VP, DWORD])
_d(ole32.CoCreateInstance, HRESULT, [VP, VP, DWORD, VP, ctypes.POINTER(VP)])
_d(ole32.CLSIDFromString, HRESULT, [wintypes.LPCWSTR, VP])
_d(ole32.CoTaskMemFree, None, [VP])
try:
    _d(user32.GetDpiForWindow, UINT, [HWND])
except AttributeError:
    pass

WS_CHILD, WS_CLIPSIBLINGS, WS_POPUP = 0x40000000, 0x04000000, 0x80000000
WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000, 0x80, 0x08000000
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x4, 0x10, 0x40
WM_TIMER, WM_SETCURSOR, WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x113, 0x20, 0x200, 0x201, 0x202
WM_LBUTTONDBLCLK, WM_RBUTTONUP, WM_MOUSEWHEEL, WM_MOUSELEAVE = 0x203, 0x205, 0x20A, 0x2A3
WM_MOUSEACTIVATE, WM_CAPTURECHANGED, WM_NULL, WM_QUIT = 0x21, 0x215, 0x0, 0x12
WM_INITMENUPOPUP, WM_DRAWITEM, WM_MEASUREITEM, WM_MENUCHAR = 0x117, 0x2B, 0x2C, 0x120
MA_NOACTIVATE, TME_LEAVE, CS_DBLCLKS = 3, 0x2, 0x8
MK_LBUTTON, MK_SHIFT, MK_CONTROL = 0x1, 0x4, 0x8
VK_CONTROL, VK_SHIFT = 0x11, 0x10
MF_STRING, MF_SEPARATOR, MF_POPUP, MF_CHECKED, MF_GRAYED, MF_BYCOMMAND = 0x0, 0x800, 0x10, 0x8, 0x1, 0x0
TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x100, 0x2
DROPEFFECT_NONE, DROPEFFECT_COPY, DROPEFFECT_MOVE, DROPEFFECT_LINK = 0, 1, 2, 4
S_OK, E_NOINTERFACE = 0, 0x80004002
DRAGDROP_S_DROP, DRAGDROP_S_CANCEL, DRAGDROP_S_USEDEFAULTCURSORS = 0x40100, 0x40101, 0x40102
FO_MOVE, FO_COPY = 1, 2
FOF_NOCONFIRMMKDIR, FOF_ALLOWUNDO, FOF_RENAMEONCOLLISION = 0x200, 0x40, 0x8
CF_HDROP = 15
IDC_ARROW, IDC_SIZEWE, IDC_SIZENS, IDC_SIZENWSE, IDC_SIZEALL = 32512, 32644, 32645, 32642, 32646
LVM_HITTEST = 0x1012
SHCNE_UPDATEDIR, SHCNF_PATHW = 0x1000, 0x5
SHCNE_ASSOCCHANGED, SHCNF_IDLIST = 0x08000000, 0x0
SIGDN_NORMALDISPLAY, SIGDN_DESKTOPABSOLUTEPARSING, SIGDN_FILESYSPATH = 0x0, 0x80028000, 0x80058000

_HINST = kernel32.GetModuleHandleW(None)


def _guid(s):
    g = GUID()
    ole32.CLSIDFromString(s, ctypes.byref(g))
    return g


IID_IUnknown = _guid("{00000000-0000-0000-C000-000000000046}")
IID_IShellFolder = _guid("{000214E6-0000-0000-C000-000000000046}")
IID_IContextMenu = _guid("{000214E4-0000-0000-C000-000000000046}")
IID_IContextMenu2 = _guid("{000214F4-0000-0000-C000-000000000046}")
IID_IContextMenu3 = _guid("{BCFCE0A0-EC17-11D0-8D10-00A0C90F2719}")
IID_IDataObject = _guid("{0000010E-0000-0000-C000-000000000046}")
IID_IDropSource = _guid("{00000121-0000-0000-C000-000000000046}")
IID_IDropTarget = _guid("{00000122-0000-0000-C000-000000000046}")
IID_IShellItemImageFactory = _guid("{BCC18B79-BA16-442F-80C4-8A59C30C463B}")
CLSID_DragDropHelper = _guid("{4657278A-411B-11D2-839A-00C04FD918D0}")
IID_IDragSourceHelper = _guid("{DE5BF786-477A-11D2-839D-00C04FD918D0}")
CLSID_ShellLink = _guid("{00021401-0000-0000-C000-000000000046}")
IID_IShellLinkW = _guid("{000214F9-0000-0000-C000-000000000046}")
IID_IPersistFile = _guid("{0000010B-0000-0000-C000-000000000046}")
FOLDERID_Desktop = _guid("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}")


def _guid_eq(p, g):
    return bytes(ctypes.string_at(p, 16)) == bytes(g)


def _vcall(obj, index, restype, argtypes, *args):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(VP)))[0]
    fn = ctypes.WINFUNCTYPE(restype, VP, *argtypes)(vtbl[index])
    return fn(obj, *args)


def _release(obj):
    if obj:
        try:
            _vcall(obj, 2, ctypes.c_ulong, [])
        except Exception:
            pass


def _class_of(h):
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(h, buf, 64)
    return buf.value


def desktop_dir():
    p = ctypes.c_wchar_p()
    try:
        if shell32.SHGetKnownFolderPath(ctypes.byref(FOLDERID_Desktop), 0, None, ctypes.byref(p)) == 0 and p.value:
            path = p.value
            ole32.CoTaskMemFree(p)
            return path
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def desktop_host():
    """(host, defview): the top-level window that holds the desktop icons (Progman,
    or a WorkerW after something split the desktop) and its SHELLDLL_DefView."""
    found = []

    def cb(h, _l):
        if _class_of(h) not in ("Progman", "WorkerW"):          # an Open/Save dialog has an icon view too
            return True
        dv = user32.FindWindowExW(h, None, "SHELLDLL_DefView", None)
        if dv:
            found.append((h, dv))
            return False
        return True
    user32.EnumWindows(ENUMPROC(cb), 0)
    return found[0] if found else (None, None)


def box_parent(host, dv):
    """The window boxes live in. Since Windows 11 24H2 Progman has no surface of its own
    (WS_EX_NOREDIRECTIONBITMAP): Explorer composes only the icon view and the wallpaper
    layer, so a child of Progman is never on screen. There boxes go inside SHELLDLL_DefView,
    above the icons; on older systems they stay next to it."""
    if host and dv and user32.GetWindowLongPtrW(host, -20) & WS_EX_NOREDIRECTIONBITMAP:   # GWL_EXSTYLE
        return dv
    return host


_MONS = {"t": 0.0, "v": None}


def monitors():
    """[(monitor rect, work rect)] in screen pixels (cached for a second)."""
    if _MONS["v"] is not None and time.time() - _MONS["t"] < 1.0:
        return _MONS["v"]
    res = []

    def cb(hmon, _hdc, _rc, _l):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            m, w = mi.rcMonitor, mi.rcWork
            res.append(((m.left, m.top, m.right, m.bottom), (w.left, w.top, w.right, w.bottom)))
        return True
    user32.EnumDisplayMonitors(None, None, MONENUMPROC(cb), 0)
    _MONS["v"], _MONS["t"] = res or [((0, 0, 1920, 1080), (0, 0, 1920, 1040))], time.time()
    return _MONS["v"]


def screen_signature():
    return ";".join("%d,%d,%d,%d" % m for m, _w in sorted(monitors()))


def work_area_at(x, y):
    for m, w in monitors():
        if m[0] <= x < m[2] and m[1] <= y < m[3]:
            return w
    return monitors()[0][1]


# ---------------------------------------------------------------------------
# pictures
# ---------------------------------------------------------------------------
_FONTS = {}


def _font(name, px):
    key = (name, px)
    if key not in _FONTS:
        windir = os.environ.get("WINDIR", r"C:\Windows")
        for fn in ((name,) if name else ()) + ("segoeui.ttf", "arial.ttf"):
            try:
                _FONTS[key] = ImageFont.truetype(os.path.join(windir, "Fonts", fn), px)
                break
            except Exception:
                continue
        else:
            _FONTS[key] = ImageFont.load_default()
    return _FONTS[key]


def hbitmap_to_image(hbm):
    bm = BITMAP()
    if not gdi32.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm)):
        return None
    w, h = bm.bmWidth, abs(bm.bmHeight)
    bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    hdc = user32.GetDC(None)
    try:
        if not gdi32.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bi), 0):
            return None
    finally:
        user32.ReleaseDC(None, hdc)
    img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).copy()
    a = img.getchannel("A")
    if a.getextrema()[1] == 0:                      # no alpha at all: an opaque thumbnail
        img.putalpha(255)
        return img
    r, g, b, _ = img.split()
    over = ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(r, g), b), a)
    if over.getbbox() is None:                       # premultiplied -> straight alpha
        img = Image.frombytes("RGBa", img.size, img.tobytes()).convert("RGBA")
    return img


def shell_image(path, size):
    """Explorer's picture for a path: a thumbnail for images/documents, else its icon."""
    fac = VP()
    target = link_icon_target(path) if path.lower().endswith(".lnk") else None
    try:
        if target:
            hr = shell32.SHCreateItemFromIDList(target, ctypes.byref(IID_IShellItemImageFactory), ctypes.byref(fac))
        else:
            hr = shell32.SHCreateItemFromParsingName(path, None, ctypes.byref(IID_IShellItemImageFactory),
                                                     ctypes.byref(fac))
    finally:
        if target:
            shell32.ILFree(target)
    if hr != 0 or not fac:
        return None
    hbm = VP()
    try:
        packed = (size & 0xFFFFFFFF) | (size << 32)           # SIZE passed by value
        if _vcall(fac, 3, HRESULT, [ctypes.c_uint64, ctypes.c_int, ctypes.POINTER(VP)],
                  packed, 0, ctypes.byref(hbm)) != 0 or not hbm:
            return None
        img = hbitmap_to_image(hbm)
    finally:
        if hbm:
            gdi32.DeleteObject(hbm)
        _release(fac)
    if img is not None and (img.width > size or img.height > size):
        img.thumbnail((size, size), Image.LANCZOS)
    return img


def image_to_hbitmap(img):
    """Premultiplied 32-bit DIB section (caller owns it)."""
    img = img.convert("RGBA")
    w, h = img.size
    a = img.getchannel("A")
    r, g, b, _ = img.convert("RGBa").split()
    data = Image.merge("RGBA", (b, g, r, a)).tobytes()
    bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = VP()
    hbm = gdi32.CreateDIBSection(None, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
    if hbm and bits:
        ctypes.memmove(bits, data, len(data))
    return hbm


class IconCache:
    """Pictures made on one background STA thread; the UI only reads the cache.
    Every picture the shell gave is also kept as a PNG on disk: after a restart it is shown
    at once (the shell is slow then, Store apps answer only after seconds) and asked from
    the shell again in the background, replaced only if it changed."""

    def __init__(self):
        self.cache, self.pending = {}, set()
        self.lock = threading.Lock()
        self.q = queue.Queue()
        self.recheck = collections.deque()      # keys shown from disk, to ask the shell when idle
        self.dirty = False
        self.gen = 0                    # bumped with every new picture: drawn item layers go stale
        self.dir = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "HDContainer", "box_icons")
        threading.Thread(target=self._run, daemon=True, name="hdc-box-icons").start()

    def _file(self, key):
        h = hashlib.sha1(("%s|%d|%r" % key).encode("utf-8", "surrogatepass")).hexdigest()
        return os.path.join(self.dir, h + ".png")

    def _disk_load(self, key):
        try:
            with Image.open(self._file(key)) as im:
                return im.convert("RGBA")
        except Exception:
            return None

    def _disk_save(self, key, img):
        f = self._file(key)
        try:
            os.makedirs(self.dir, exist_ok=True)
            img.save(f + ".tmp", "PNG")
            os.replace(f + ".tmp", f)
        except Exception:
            pass

    def _prune(self):
        """Keep the disk cache small: pictures not used for 60 days go."""
        try:
            old = time.time() - 60 * 86400
            for e in os.scandir(self.dir):
                if e.stat().st_mtime < old:
                    os.remove(e.path)
        except Exception:
            pass

    def _put(self, key, img):
        with self.lock:
            self.cache[key] = img
            self.pending.discard(key)
            self.gen += 1
        self.dirty = True

    def get(self, path, size, mtime):
        key = (path, size, mtime)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            if key not in self.pending:
                self.pending.add(key)
                self.q.put((key, 0))
        return None

    def _run(self):
        ole32.CoInitializeEx(None, 2)
        t = threading.Timer(120, self._prune)
        t.daemon = True
        t.start()
        while True:
            if self.recheck:
                try:
                    key, tries = self.q.get_nowait()             # new requests first
                except queue.Empty:                             # idle: ask the shell about a disk picture
                    self._recheck(self.recheck.popleft())
                    continue
            else:
                key, tries = self.q.get()
            if tries == 0:
                img = self._disk_load(key)
                if img is not None:
                    self._put(key, img)
                    self.recheck.append(key)
                    continue
            try:
                img = shell_image(key[0], key[1])
            except Exception:
                img = None
            if img is None and tries < len(ICON_RETRY_S):     # right after sign-in Store apps may not answer yet
                t = threading.Timer(ICON_RETRY_S[tries], self.q.put, ((key, tries + 1),))
                t.daemon = True
                t.start()
            if img is None and tries:
                continue                                        # failed again: nothing to redraw
            if img is not None:
                self._disk_save(key, img)
            self._put(key, img or False)                        # False: no picture, draw a placeholder

    def _recheck(self, key):
        try:
            img = shell_image(key[0], key[1])
        except Exception:
            img = None
        if img is None:
            return                                              # keep the disk picture
        with self.lock:
            cur = self.cache.get(key)
        if cur and cur.size == img.size and cur.tobytes() == img.convert("RGBA").tobytes():
            try:
                os.utime(self._file(key))                       # still in use: not pruned
            except OSError:
                pass
            return
        self._disk_save(key, img)
        self._put(key, img)


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------
def unique_path(folder, name):
    dst = os.path.join(folder, name)
    if not os.path.exists(dst):
        return dst
    stem, ext = os.path.splitext(name)
    if os.path.isdir(os.path.join(folder, name)):
        stem, ext = name, ""
    n = 2
    while True:
        dst = os.path.join(folder, "%s (%d)%s" % (stem, n, ext))
        if not os.path.exists(dst):
            return dst
        n += 1


def safe_name(name):
    bad = '<>:"/\\|?*'
    s = "".join("_" if c in bad or ord(c) < 32 else c for c in name).strip().rstrip(".")
    return s or "Box"


def file_op(func, sources, dest_dir, log):
    """Move/copy with Explorer's progress, undo and rename-on-collision (worker thread)."""
    def run():
        ole32.CoInitializeEx(None, 2)
        op = SHFILEOPSTRUCTW()
        op.wFunc = func
        op.pFrom = "\0".join(sources) + "\0\0"
        op.pTo = dest_dir + "\0\0"
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMMKDIR | FOF_RENAMEONCOLLISION
        rc = shell32.SHFileOperationW(ctypes.byref(op))
        if rc:
            log("box file op %d failed: %d" % (func, rc))
    threading.Thread(target=run, daemon=True).start()


def hdrop_files(hdrop):
    n = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    out = []
    for i in range(n):
        ln = shell32.DragQueryFileW(hdrop, i, None, 0)
        buf = ctypes.create_unicode_buffer(ln + 1)
        shell32.DragQueryFileW(hdrop, i, buf, ln + 1)
        out.append(buf.value)
    return out


def dataobject_files(pdo):
    fmt = FORMATETC(CF_HDROP, None, 1, -1, 1)          # DVASPECT_CONTENT, TYMED_HGLOBAL
    stg = STGMEDIUM()
    if _vcall(pdo, 3, HRESULT, [VP, VP], ctypes.byref(fmt), ctypes.byref(stg)) != 0:
        return []
    try:
        return hdrop_files(stg.hGlobal)
    finally:
        ole32.ReleaseStgMedium(ctypes.byref(stg))


def pidl_name(pidl, sigdn):
    p = ctypes.c_wchar_p()
    if shell32.SHGetNameFromIDList(pidl, sigdn, ctypes.byref(p)) != 0 or not p.value:
        return ""
    s = p.value
    ole32.CoTaskMemFree(p)
    return s


def dataobject_items(pdo):
    """Every item of a shell drag (its "Shell IDList Array"): [(parsing name, display name,
    absolute ID list bytes, dragged from the desktop, file system path or "")]."""
    fmt = FORMATETC(user32.RegisterClipboardFormatW("Shell IDList Array"), None, 1, -1, 1)
    stg = STGMEDIUM()
    if _vcall(pdo, 3, HRESULT, [VP, VP], ctypes.byref(fmt), ctypes.byref(stg)) != 0:
        return []
    out = []
    try:
        base = kernel32.GlobalLock(stg.hGlobal)
        if not base:
            return []
        try:
            n = ctypes.c_uint.from_address(base).value                  # CIDA: count, offsets, parent, children
            if n > 400:                                                 # a big folder drag: plain files will do
                return []
            offs = (ctypes.c_uint * (n + 1)).from_address(base + 4)
            parent = base + offs[0]
            from_desktop = ctypes.c_ushort.from_address(parent).value == 0     # empty ID list = the desktop
            for i in range(1, n + 1):
                pidl = shell32.ILCombine(parent, base + offs[i])
                if not pidl:
                    continue
                try:
                    out.append((pidl_name(pidl, SIGDN_DESKTOPABSOLUTEPARSING), pidl_name(pidl, SIGDN_NORMALDISPLAY),
                                ctypes.string_at(pidl, shell32.ILGetSize(pidl)), from_desktop,
                                pidl_name(pidl, SIGDN_FILESYSPATH)))
                finally:
                    shell32.ILFree(pidl)
        finally:
            kernel32.GlobalUnlock(stg.hGlobal)
    finally:
        ole32.ReleaseStgMedium(ctypes.byref(stg))
    return out


def _shell_link():
    """(IShellLinkW, IPersistFile) of a new shell link object, or (None, None)."""
    psl, ppf = VP(), VP()
    if ole32.CoCreateInstance(ctypes.byref(CLSID_ShellLink), None, 1, ctypes.byref(IID_IShellLinkW),
                              ctypes.byref(psl)) != 0:
        return None, None
    if _vcall(psl, 0, HRESULT, [VP, ctypes.POINTER(VP)], ctypes.byref(IID_IPersistFile), ctypes.byref(ppf)) != 0:
        _release(psl)
        return None, None
    return psl, ppf


def make_link(path, pidl_bytes):
    """Save a .lnk pointing at a shell item by its ID list: works for This PC, the Recycle Bin
    and anything else that has no file of its own."""
    psl, ppf = _shell_link()
    if not psl:
        return False
    try:
        buf = ctypes.create_string_buffer(pidl_bytes, len(pidl_bytes) + 2)
        if _vcall(psl, 5, HRESULT, [VP], buf) != 0:                       # IShellLinkW::SetIDList
            return False
        return _vcall(ppf, 6, HRESULT, [wintypes.LPCWSTR, BOOL], path, True) == 0     # IPersistFile::Save
    finally:
        _release(ppf)
        _release(psl)


def link_icon_target(path):
    """The ID list (the caller frees it) of what a .lnk points to when that is no file and the
    link has no icon of its own: a Store app, This PC… Explorer's picture of such a link can be a
    blank page (a WhatsApp link after signing in again) while its target's is right. Else None."""
    psl, ppf = _shell_link()
    if not psl:
        return None
    pidl = VP()
    try:
        if _vcall(ppf, 5, HRESULT, [wintypes.LPCWSTR, DWORD], path, 0) != 0:           # IPersistFile::Load
            return None
        icon, idx = ctypes.create_unicode_buffer(520), ctypes.c_int()
        if _vcall(psl, 16, HRESULT, [wintypes.LPWSTR, ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
                  icon, 520, ctypes.byref(idx)) == 0 and icon.value:                 # GetIconLocation
            return None
        if _vcall(psl, 4, HRESULT, [ctypes.POINTER(VP)], ctypes.byref(pidl)) != 0 or not pidl:   # GetIDList
            return None
        if pidl_name(pidl, SIGDN_FILESYSPATH):
            return None
        out, pidl = pidl.value, VP()
        return out
    finally:
        if pidl:
            shell32.ILFree(pidl)
        _release(ppf)
        _release(psl)


def link_target(path):
    """The parsing name of what a .lnk points to ("::{CLSID}" for This PC, the Recycle Bin…)."""
    psl, ppf = _shell_link()
    if not psl:
        return ""
    pidl = VP()
    try:
        if _vcall(ppf, 5, HRESULT, [wintypes.LPCWSTR, DWORD], path, 0) != 0:           # IPersistFile::Load
            return ""
        if _vcall(psl, 4, HRESULT, [ctypes.POINTER(VP)], ctypes.byref(pidl)) != 0 or not pidl:   # GetIDList
            return ""
        return pidl_name(pidl, SIGDN_DESKTOPABSOLUTEPARSING)
    finally:
        if pidl:
            shell32.ILFree(pidl)
        _release(ppf)
        _release(psl)


# the standard desktop icons (Settings → Personalization → Themes → Desktop icon settings):
# parsing CLSID -> the value that shows / hides it
DESKTOP_ICONS = {
    "{20D04FE0-3AEA-1069-A2D8-08002B30309D}": "{20D04FE0-3AEA-1069-A2D8-08002B30309D}",     # This PC
    "{645FF040-5081-101B-9F08-00AA002F954E}": "{645FF040-5081-101B-9F08-00AA002F954E}",     # Recycle Bin
    "{59031A47-3F72-44A7-89C5-5595FE6B30EE}": "{59031A47-3F72-44A7-89C5-5595FE6B30EE}",     # the user's folder
    "{F02C1A0D-BE21-4350-88B0-7367FC96EF3C}": "{F02C1A0D-BE21-4350-88B0-7367FC96EF3C}",     # Network
    "{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}": "{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}",     # Control Panel
    "{26EE0668-A00A-44D7-9371-BEB064C98683}": "{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}",     # Control Panel (categories)
}
HIDE_ICONS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\HideDesktopIcons\NewStartPanel"


USER_FILES = "{59031A47-3F72-44A7-89C5-5595FE6B30EE}"


def _profile():
    return os.path.normcase(os.environ.get("USERPROFILE") or os.path.expanduser("~")).rstrip("\\/")


def protected_folder(path):
    """Folders a box must never move or copy in, only link to: a drive root, the user's profile
    (the desktop shows it as an icon of its own) and any folder the desktop itself lives in."""
    if not path:
        return False
    p = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
    if len(p) <= 2 or p == _profile():
        return True
    desk = os.path.normcase(desktop_dir()).rstrip("\\/")
    return desk == p or desk.startswith(p + os.sep)


def desktop_icon_clsid(parse, from_desktop=False, fs=""):
    """The CLSID of a standard desktop icon, else None. The user's folder icon is parsed
    as its plain path, so it is recognised by being the profile, dragged from the desktop."""
    c = parse[2:].upper() if parse.startswith("::{") and len(parse) == 40 else None
    if c in DESKTOP_ICONS:
        return c
    if from_desktop and fs and os.path.normcase(fs).rstrip("\\/") == _profile():
        return USER_FILES
    return None


def link_is_icon(path, clsid):
    t = link_target(path)
    return t.upper() == "::" + clsid or (clsid == USER_FILES and os.path.normcase(t).rstrip("\\/") == _profile())


def show_desktop_icon(clsid, show):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, HIDE_ICONS_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, DESKTOP_ICONS[clsid], 0, winreg.REG_DWORD, 0 if show else 1)
    shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)     # the desktop re-reads its icons


# ---------------------------------------------------------------------------
# COM objects implemented here: drop source and drop target
# ---------------------------------------------------------------------------
class _ComObject:
    """A minimal COM object: vtable of ctypes callbacks, ref counting is a no-op
    because Python keeps the object alive as long as the box exists."""

    def __init__(self, iid, methods):
        QI = ctypes.WINFUNCTYPE(HRESULT, VP, VP, ctypes.POINTER(VP))(self._qi)
        AR = ctypes.WINFUNCTYPE(ctypes.c_ulong, VP)(lambda this: 1)
        RL = ctypes.WINFUNCTYPE(ctypes.c_ulong, VP)(lambda this: 1)
        self._iid = iid
        self._fns = [QI, AR, RL] + methods
        self._vtbl = (VP * len(self._fns))(*[ctypes.cast(f, VP) for f in self._fns])
        self._obj = (VP * 1)(ctypes.cast(self._vtbl, VP))
        self.ptr = ctypes.cast(self._obj, VP)

    def _qi(self, this, riid, ppv):
        if _guid_eq(riid, IID_IUnknown) or _guid_eq(riid, self._iid):
            ppv[0] = this
            return S_OK
        ppv[0] = None
        return E_NOINTERFACE


class DropSource(_ComObject):
    def __init__(self):
        qcd = ctypes.WINFUNCTYPE(HRESULT, VP, BOOL, DWORD)(self._query)
        gfb = ctypes.WINFUNCTYPE(HRESULT, VP, DWORD)(lambda this, eff: DRAGDROP_S_USEDEFAULTCURSORS)
        super().__init__(IID_IDropSource, [qcd, gfb])

    @staticmethod
    def _query(this, esc, keys):
        if esc:
            return DRAGDROP_S_CANCEL
        if not keys & MK_LBUTTON:
            return DRAGDROP_S_DROP
        return S_OK


class DropTarget(_ComObject):
    def __init__(self, box):
        self.box = box
        self.files = []                 # real files and folders: moved / copied into the box
        self.links = []                 # shell items without a file of their own: become shortcuts
        enter =ctypes.WINFUNCTYPE(HRESULT, VP, VP, DWORD, ctypes.c_uint64, ctypes.POINTER(DWORD))(self._enter)
        over = ctypes.WINFUNCTYPE(HRESULT, VP, DWORD, ctypes.c_uint64, ctypes.POINTER(DWORD))(self._over)
        leave = ctypes.WINFUNCTYPE(HRESULT, VP)(self._leave)
        drop = ctypes.WINFUNCTYPE(HRESULT, VP, VP, DWORD, ctypes.c_uint64, ctypes.POINTER(DWORD))(self._drop)
        super().__init__(IID_IDropTarget, [enter, over, leave, drop])

    def _effect(self, keys):
        folder = self.box.folder
        if not folder or not os.path.isdir(folder):
            return DROPEFFECT_NONE
        if not self.files:
            return DROPEFFECT_LINK if self.links else DROPEFFECT_NONE
        if all(os.path.normcase(os.path.dirname(f)) == os.path.normcase(folder) for f in self.files):
            return DROPEFFECT_NONE
        if keys & MK_CONTROL:
            return DROPEFFECT_COPY
        if keys & MK_SHIFT:
            return DROPEFFECT_MOVE
        same = all(os.path.splitdrive(f)[0].lower() == os.path.splitdrive(folder)[0].lower() for f in self.files)
        return DROPEFFECT_MOVE if same else DROPEFFECT_COPY

    def _negotiate(self, keys, allowed):
        eff = self._effect(keys)
        if eff == DROPEFFECT_LINK:
            return eff if not allowed or allowed & eff else (allowed & DROPEFFECT_COPY)
        if eff and allowed and not eff & allowed:
            eff = DROPEFFECT_COPY if allowed & DROPEFFECT_COPY else (allowed & DROPEFFECT_MOVE)
        return eff

    def _read(self, pdo):
        try:
            files = dataobject_files(pdo)
            items = dataobject_items(pdo)
        except Exception as ex:
            self.box.mgr.log("box drop read failed: %r" % ex)
            files, items = [], []
        # namespace items (This PC, the Recycle Bin…) and protected folders (the user's own
        # folder, a drive) are never moved or copied: they go in as shortcuts
        links = [it for it in items if it[0].startswith("::") or protected_folder(it[4])]
        skip = {os.path.normcase(it[4]) for it in links if it[4]}
        if files:
            files = [f for f in files if os.path.normcase(f) not in skip]
        else:
            files = [it[4] for it in items if it not in links and it[4]]
        for f in [f for f in files if protected_folder(f)]:          # no ID list to link with: just refuse
            files.remove(f)
        self.files, self.links = files, links

    def _enter(self, this, pdo, keys, pt, peff):
        self._read(pdo)
        peff[0] = self._negotiate(keys, peff[0])
        self.box.drag_hover(True)
        return S_OK

    def _over(self, this, keys, pt, peff):
        peff[0] = self._negotiate(keys, peff[0])
        return S_OK

    def _leave(self, this):
        self.box.drag_hover(False)
        return S_OK

    def _drop(self, this, pdo, keys, pt, peff):
        if not self.files and not self.links:
            self._read(pdo)
        eff = self._negotiate(keys, peff[0])
        files, links = self.files, self.links
        self.box.drag_hover(False)
        if eff and files:
            # we move/copy ourselves and report COPY: a source told MOVE would delete
            # the originals itself, racing with our own operation
            self.box.mgr.receive(self.box, files, FO_MOVE if eff == DROPEFFECT_MOVE else FO_COPY)
        if eff and links:
            box = self.box                                  # after the drop: the desktop is still in its drag loop
            box.mgr.defer(lambda: box.mgr.add_links(box, links))
        peff[0] = (DROPEFFECT_COPY if files else eff) if eff else DROPEFFECT_NONE
        self.files, self.links = [], []
        return S_OK


# ---------------------------------------------------------------------------
# shell objects for a set of files in one folder
# ---------------------------------------------------------------------------
class ShellItems:
    def __init__(self, paths):
        self.pidls, self.children, self.psf = [], [], VP()
        for p in paths:
            pidl = VP()
            if shell32.SHParseDisplayName(p, None, ctypes.byref(pidl), 0, None) == 0 and pidl:
                self.pidls.append(pidl)
        if not self.pidls:
            return
        child = VP()
        if shell32.SHBindToParent(self.pidls[0], ctypes.byref(IID_IShellFolder), ctypes.byref(self.psf),
                                  ctypes.byref(child)) != 0:
            self.psf = VP()
            return
        self.children = [shell32.ILFindLastID(p) for p in self.pidls]

    def ui_object(self, hwnd, iid):
        if not self.psf or not self.children:
            return None
        arr = (VP * len(self.children))(*self.children)
        out = VP()
        hr = _vcall(self.psf, 10, HRESULT, [HWND, UINT, VP, VP, VP, ctypes.POINTER(VP)],
                    hwnd, len(self.children), arr, ctypes.byref(iid), None, ctypes.byref(out))
        return out if hr == 0 and out else None

    def free(self):
        _release(self.psf)
        self.psf = VP()
        for p in self.pidls:
            shell32.ILFree(p)
        self.pidls, self.children = [], []


# ---------------------------------------------------------------------------
# one box
# ---------------------------------------------------------------------------
_MASKS = {}
_CORNERS = {}
_MEASURE = ImageDraw.Draw(Image.new("L", (1, 1)))          # text measuring without a picture


def _corner(r):
    """Coverage of a top-left corner of radius r, drawn 4x larger and averaged down:
    smooth instead of Pillow's jagged arcs."""
    c = _CORNERS.get(r)
    if c is None:
        k = 4
        big = Image.new("L", (r * k, r * k), 0)
        ImageDraw.Draw(big).ellipse((0, 0, 2 * r * k - 1, 2 * r * k - 1), fill=255)
        c = _CORNERS[r] = big.resize((r, r), Image.BOX)
    return c


def _rect_mask(w, h, r, corners):
    m = Image.new("L", (w, h), 255)
    r = min(r, w // 2, h // 2)
    if r > 0:
        c = _corner(r)
        tl, tr, br, bl = corners or (True, True, True, True)
        if tl:
            m.paste(c, (0, 0))
        if tr:
            m.paste(c.transpose(Image.FLIP_LEFT_RIGHT), (w - r, 0))
        if br:
            m.paste(c.transpose(Image.ROTATE_180), (w - r, h - r))
        if bl:
            m.paste(c.transpose(Image.FLIP_TOP_BOTTOM), (0, h - r))
    return m


def aa_mask(w, h, r, corners=None, outline=0):
    """Coverage mask of a rounded rectangle with smooth corners. Built from cached corner
    pieces, so a new height costs next to nothing: the roll animation makes one every frame."""
    w, h = max(1, int(w)), max(1, int(h))
    key = (w, h, r, corners, outline)
    m = _MASKS.get(key)
    if m is None:
        m = _rect_mask(w, h, r, corners)
        if outline:
            inner = Image.new("L", (w, h), 0)
            if w > 2 * outline and h > 2 * outline:
                inner.paste(_rect_mask(w - 2 * outline, h - 2 * outline, max(0, r - outline), corners),
                            (outline, outline))
            m = ImageChops.subtract(m, inner)
        if len(_MASKS) > 96:
            _MASKS.clear()
        _MASKS[key] = m
    return m


def aa_fill(img, box, r, rgba, corners=None, outline=0):
    """Composite a smooth rounded rectangle (filled or outlined) onto img."""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    layer = Image.new("RGBA", (w, h), tuple(rgba[:3]) + (0,))
    mask = aa_mask(w, h, min(r, w // 2, h // 2), corners, outline)
    layer.putalpha(mask if rgba[3] >= 255 else mask.point(lambda v, a=rgba[3]: v * a // 255))
    img.alpha_composite(layer, (x0, y0))


def _blend_at(dst, src, x, y):
    """alpha_composite src onto dst at (x, y), clipped to dst (Pillow refuses negative offsets)."""
    sx, sy = max(0, -x), max(0, -y)
    x, y = max(0, x), max(0, y)
    w, h = min(src.width - sx, dst.width - x), min(src.height - sy, dst.height - y)
    if w > 0 and h > 0:
        dst.alpha_composite(src, (x, y), (sx, sy, sx + w, sy + h))


def _mix(rgb, other, f):
    return tuple(int(a + (b - a) * f) for a, b in zip(rgb, other))


def _hex(c):
    c = (c or "").lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# fonts installed in Windows (family -> files), for the title font
# ---------------------------------------------------------------------------
_FONT_FILES = None
_FONT_CACHE = {}


def font_files():
    global _FONT_FILES
    if _FONT_FILES is None:
        import winreg
        res = {}
        fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        key = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                k = winreg.OpenKey(hive, key)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    name, val, _t = winreg.EnumValue(k, i)
                except OSError:
                    break
                i += 1
                if not isinstance(val, str) or not val.lower().endswith((".ttf", ".otf", ".ttc")):
                    continue
                path = val if os.path.isabs(val) else os.path.join(fonts_dir, val)
                fam = re.sub(r"\s*\((TrueType|OpenType|All res)\)\s*$", "", name).split(" & ")[0].strip()
                style = "regular"
                for suffix, st in ((" Bold Italic", None), (" Italic", None), (" Oblique", None), (" Bold", "bold")):
                    if fam.endswith(suffix):
                        fam, style = fam[:-len(suffix)].strip(), st
                        break
                if style and fam:
                    res.setdefault(fam, {}).setdefault(style, path)
        _FONT_FILES = {f: v for f, v in res.items() if "regular" in v}
    return _FONT_FILES


def title_font(family, px, bold):
    """(ImageFont, needs fake bold) for a family name; falls back to Segoe UI."""
    files = font_files().get(family) or font_files().get("Segoe UI") or {}
    path = files.get("bold") if bold and "bold" in files else files.get("regular")
    key = (path, px)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = ImageFont.truetype(path, px) if path else _font("seguisb.ttf", px)
        except Exception:
            _FONT_CACHE[key] = _font("seguisb.ttf", px)
    return _FONT_CACHE[key], bool(bold and "bold" not in files)


def wallpaper_image(w, h):
    """The current wallpaper cropped to w x h (for the settings preview), or None."""
    buf = ctypes.create_unicode_buffer(520)
    try:
        ctypes.windll.user32.SystemParametersInfoW(0x73, 520, buf, 0)      # SPI_GETDESKWALLPAPER
        img = Image.open(buf.value).convert("RGB")
    except Exception:
        return None
    k = max(w / float(img.width), h / float(img.height))
    img = img.resize((max(w, int(img.width * k + 1)), max(h, int(img.height * k + 1))), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h)).convert("RGBA")


# ---------------------------------------------------------------------------
# one box
# ---------------------------------------------------------------------------
class Box:
    def __init__(self, mgr, d):
        self.mgr, self.d = mgr, d
        self.hwnd = None
        self.target = None              # DropTarget (registered with OLE)
        self.items = []                 # [(name, path, is_dir, mtime)]
        self.sig = None                 # listing signature: re-render only on change
        self.scroll = 0
        self.hover = -1
        self.hovering = False           # cursor over the box: its own transparency
        self.sel = set()
        self.cur_h = None               # current height (animation)
        self.anim = None                # (from_h, to_h, t0)
        self.temp_open = False          # opened by hover, closes when the mouse leaves
        self.open_pending = False       # the hover-open timer is running
        self.drop_hl = False
        self.tracking = False
        self.press = None               # dict while a mouse button is held
        self._dc = None
        self._pos = None                # where the window was last put (parent client coordinates)
        self._layer = None              # (key, icons and labels of all items) reused by every frame
        self._wraps = {}                # label -> its lines
        self._tiles = {}                # path -> (key, one item's picture) for drawing it off its cell
        self.reorder = None             # dict while an item is dragged around inside the box
        self.slots = None               # path -> [x, y] where each item is drawn while items glide

    # ---- data ----
    @property
    def id(self):
        return self.d.get("id")

    @property
    def folder(self):
        return self.d.get("dir")

    @property
    def name(self):
        return self.d.get("name") or "Box"

    def scale(self):
        now = time.monotonic()
        c = getattr(self, "_scale", None)
        if c and c[0] == self.hwnd and now - c[1] < 1.0:           # asked ~1000 times per frame: cache it
            return c[2]
        try:
            dpi = user32.GetDpiForWindow(self.hwnd) if self.hwnd else 96
        except Exception:
            dpi = 96
        self._scale = (self.hwnd, now, (dpi or 96) / 96.0)
        return self._scale[2]

    def px(self, v):
        return int(round(v * self.scale()))

    def style(self):
        return self.mgr.style_of(self.id)

    def font_px(self):
        return int(round(self.style()["font_size"] * 96 / 72.0 * self.scale()))

    def title_h(self):
        return max(self.px(26), self.font_px() + self.px(18))

    def icon_px(self):
        return self.px(int(self.mgr.opt("icon_size")))

    def layout(self, w):
        """(x0, cols, cell, row_h, pad): fixed-width cells, the whole grid centered, so the gaps
        between icons equal the margins and columns line up across rows; a row has room
        for a label on two lines."""
        pad, cell = self.px(PAD), self.icon_px() + self.px(40)
        cols = max(1, (w - 2 * pad) // cell)
        return (w - cols * cell) // 2, cols, cell, self.icon_px() + self.px(48), pad

    def _label_font(self):
        return _font(None, self.px(12))

    def _lines(self, w, name, is_dir):
        """An item's label wrapped to its cell (one or two lines), cached."""
        cell = self.layout(w)[2]
        maxw = cell - 3 * self.px(4)
        key = (name, is_dir, maxw, self.px(12))
        ln = self._wraps.get(key)
        if ln is None:
            if len(self._wraps) > 2000:
                self._wraps.clear()
            ln = self._wraps[key] = self._wrap(_MEASURE, self.label(name, is_dir), self._label_font(), maxw)
        return ln

    def _item_h(self, lines):
        """Height an item takes: the icon, then its label. The box ends under the text."""
        return self.px(7) + self.icon_px() + self.px(4) + lines * self.px(15) + self.px(5)

    def up(self):
        """A box in the lower half of its screen opens upwards; its title is then a footer."""
        r = self.d.get("rects", {}).get(self.mgr.sig) or self.d.get("last") or [200, 120, 320, 420]
        wa = work_area_at(r[0] + 10, r[1] + 10)
        return r[1] + self.title_h() / 2.0 > (wa[1] + wa[3]) / 2.0

    def win_top(self, h):
        """Screen y of the window's top edge at height h: (x, y) of rect() is always the title."""
        y = self.rect()[1]
        return y + self.title_h() - h if self.up() else y

    def grid_top(self):
        """Where the items start inside the window: under the title, or at the top over a footer."""
        return 0 if self.up() else self.title_h()

    def rect(self):
        """[x, y, w, h] in screen pixels for the current monitor layout; (x, y) is the title's
        corner. h is the open height: it follows the number of items, up to the screen edge the
        box opens towards (then it scrolls)."""
        r = self.d.get("rects", {}).get(self.mgr.sig) or self.d.get("last") or [200, 120, 320, 420]
        x, y, w, _h = r
        wa = work_area_at(x + 10, y + 10)
        w = max(self.px(PAD) * 2 + self.icon_px() + self.px(40), w)
        x = min(max(x, wa[0] - w + 40), wa[2] - 40)
        t = self.title_h()
        y = min(max(y, wa[1]), wa[3] - t)
        one_row = t + self.px(PAD) * 2 + self.layout(w)[3]
        room = (y + t - wa[1]) if self.up() else (wa[3] - y)
        h = min(t + self.content_h(w), max(one_row, room))
        return [x, y, w, h]

    def visible_rect(self):
        x, y, w, h = self.rect()
        vh = self.cur_h or self.target_h()
        return [x, self.win_top(vh), w, vh]

    def set_rect(self, r):
        self.d.setdefault("rects", {})[self.mgr.sig] = list(r)
        self.d["last"] = list(r)
        self.mgr.save_soon()

    def rolled(self):
        if self.press and self.press.get("mode") == "resize":
            return False
        if self.d.get("rollup"):
            return not self.temp_open
        return bool(self.d.get("collapsed"))

    def target_h(self):
        return self.title_h() if self.rolled() else self.rect()[3]

    def rest_h(self):
        """Height while the cursor is elsewhere: just the title for a box that rolls up."""
        return self.title_h() if (self.d.get("rollup") or self.d.get("collapsed")) else self.rect()[3]

    # ---- listing ----
    def refresh_items(self):
        folder = self.folder
        out = []
        try:
            for e in os.scandir(folder):
                try:
                    st = e.stat()
                    attrs = getattr(st, "st_file_attributes", 0)
                    if attrs & 0x2 or attrs & 0x4 or e.name.lower() == "desktop.ini":     # hidden / system
                        continue
                    out.append((e.name, e.path, e.is_dir(), st.st_mtime))
                except OSError:
                    continue
        except OSError:
            out = None
        sort = self.d.get("sort", "name")
        if out is not None:
            if sort == "manual":                         # dragged into place; new files go to the end
                rank = {n: k for k, n in enumerate(self.d.get("order") or [])}
                out.sort(key=lambda t: (t[0] not in rank, rank.get(t[0], 0), not t[2], t[0].lower()))
            elif sort == "date":
                out.sort(key=lambda t: -t[3])
            elif sort == "type":
                out.sort(key=lambda t: (not t[2], os.path.splitext(t[0])[1].lower(), t[0].lower()))
            else:
                out.sort(key=lambda t: (not t[2], t[0].lower()))
        sig = None if out is None else tuple((n, m) for n, _p, _d, m in out)
        if sig != self.sig:
            self.items = out or []
            self.sig = sig
            self._prefetch_icons()
            self.sel = {i for i in self.sel if i < len(self.items)}
            if out is not None and "seen" not in self.d:
                self.d["seen"] = [n for n, *_ in out]
            return True
        return False

    def _prefetch_icons(self):
        try:
            size = self.icon_px()
            for _n, path, _d, mtime in self.items:
                self.mgr.icons.get(self.mgr.moved_target(path) or path, size, mtime)
        except Exception:
            pass

    def badge(self):
        seen = set(self.d.get("seen") or [])
        return sum(1 for n, *_ in self.items if n not in seen)

    def mark_seen(self):
        names = [n for n, *_ in self.items]
        if set(names) != set(self.d.get("seen") or []):
            self.d["seen"] = names
            self.mgr.save_soon()

    @staticmethod
    def label(name, is_dir):
        stem, ext = os.path.splitext(name)
        return stem if (not is_dir and ext.lower() in (".lnk", ".url", ".appref-ms", ".website")) else name

    # ---- window ----
    def create(self, host):
        x, y, w, h = self.rect()
        cx, cy = self.mgr.to_client(x, y)
        self.cur_h = self.title_h() if self.rolled() else h
        self.hwnd = user32.CreateWindowExW(WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, self.mgr.CLASS,
                                           self.name, WS_CHILD | WS_CLIPSIBLINGS, cx, cy, w, self.cur_h,
                                           host, None, _HINST, None)
        if not self.hwnd:
            self.mgr.log("box window failed: %d" % ctypes.get_last_error())
            return False
        self.mgr.by_hwnd[self.hwnd] = self
        self._pos = (cx, cy)
        self.cur_h = self.target_h()
        self.refresh_items()
        self.render()
        user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)  # HWND_TOP
        if not (self.mgr.hidden and not self.d.get("no_quickhide")):
            user32.ShowWindow(self.hwnd, 4)                                                     # SW_SHOWNOACTIVATE
        if self.mgr.ole_ok:
            self.target = DropTarget(self)
            hr = ole32.RegisterDragDrop(self.hwnd, self.target.ptr)
            if hr != 0:
                self.mgr.log("RegisterDragDrop failed: 0x%08x" % (hr & 0xFFFFFFFF))
        return True

    def destroy(self):
        if self.hwnd:
            if self.target:
                ole32.RevokeDragDrop(self.hwnd)
            self.mgr.by_hwnd.pop(self.hwnd, None)
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        self._free_dc()

    def place(self):
        x, y, w, _h = self.rect()
        cx, cy = self.mgr.to_client(x, self.win_top(self.cur_h or self.title_h()))
        user32.SetWindowPos(self.hwnd, None, cx, cy, w, self.cur_h, SWP_NOZORDER | SWP_NOACTIVATE)
        self._pos = (cx, cy)

    # ---- drawing ----
    def content_h(self, w):
        _x0, cols, _cell, ch, pad = self.layout(w)
        rows = max(1, (len(self.items) + cols - 1) // cols)     # an empty box keeps one row to drop into
        last = self.items[(rows - 1) * cols:]
        lines = max([len(self._lines(w, n, d)) for n, _p, d, _m in last] or [1])
        return pad * 2 + (rows - 1) * ch + self._item_h(lines)

    def render(self):
        if not self.hwnd:
            return
        x, y, w, full_h = self.rect()
        self._blit(self.compose(w, max(1, self.cur_h or full_h)))

    def compose(self, w, h):
        st = self.style()
        t = self.title_h()
        base = _hex(st.get("color"))
        rgb = _mix(base, (18, 20, 25), 0.5) if base else (24, 27, 33)
        tr = st["hover_transp"] if self.hovering else st["transp"]
        a = int(round(255 * (1 - max(0, min(95, int(tr))) / 100.0)))
        r = self.px(RADIUS) if self.mgr.opt("round") else 0
        rolled_now = h <= t + 1
        up = self.up()
        hy = h - t if (up and not rolled_now) else 0       # the title strip: on top, or a footer
        # one translucent shape: rolled up it is just the title; open, the body keeps the
        # same transparency and the header gets a denser fill of the same color
        img = Image.new("RGBA", (w, h), rgb + (0,))
        m = aa_mask(w, h, r)
        img.putalpha(m if a >= 255 else m.point(lambda v, a=a: v * a // 255))
        if not rolled_now:
            head = Image.new("RGBA", (w, t), rgb + (min(255, a + HEADER_EXTRA),))
            img.paste(head, (0, hy), aa_mask(w, t, r, (False, False, True, True) if up else (True, True, False, False)))
        if self.mgr.opt("border"):
            aa_fill(img, (0, 0, w, h), r, (255, 255, 255, 72), outline=max(1, self.px(1)))
        dr = ImageDraw.Draw(img)
        f, fake_bold = title_font(st["font"], self.font_px(), st["bold"])
        tc = _hex(st.get("title_color")) or (244, 246, 250)
        name = self.name
        badge = self.badge() if (self.items and self.mgr.opt("badge")) else 0
        fb = _font("seguisb.ttf", self.px(10))
        txt = (str(badge) if badge < 100 else "99+") if badge else ""
        bh = self.px(18)
        bw = max(bh, self.px(10) + int(dr.textlength(txt, font=fb))) if badge else 0
        gap = self.px(6) if badge else 0
        maxw = w - self.px(24) - bw - gap
        while name and dr.textlength(name, font=f) > maxw:
            name = name[:-2] + "…" if len(name) > 2 else ""
        tw = dr.textlength(name, font=f)
        x0 = (w - tw - bw - gap) / 2.0
        asc, desc = f.getmetrics()
        th = min(h, t)
        ty = hy + (th - (asc + desc)) / 2.0
        sw = 1 if fake_bold else 0
        dark_text = sum(tc) < 300
        dr.text((x0 + 1, ty + 1), name, font=f, fill=(255, 255, 255, 70) if dark_text else (0, 0, 0, 110),
                stroke_width=sw, stroke_fill=(0, 0, 0, 0))
        dr.text((x0, ty), name, font=f, fill=tc + (255,), stroke_width=sw, stroke_fill=tc + (255,))
        if badge:
            bx, by = x0 + tw + gap, hy + (th - bh) / 2.0
            aa_fill(img, (bx, by, bx + bw, by + bh), bh // 2, (235, 64, 52, 255))
            tl = dr.textlength(txt, font=fb)
            fa, fd = fb.getmetrics()
            dr.text((bx + (bw - tl) / 2.0, by + (bh - fa - fd) / 2.0), txt, font=fb, fill=(255, 255, 255, 255))
        if not rolled_now:
            self._draw_items(img, w, h, t, r)
        if self.drop_hl:
            aa_fill(img, (0, 0, w, h), r, (76, 139, 245, 255), outline=self.px(2))
        return img

    def _items_layer(self, w):
        """Icons and labels of all items (scroll 0, the grid only), drawn once and reused by
        every frame: the roll animation and hover highlights only composite it. Rebuilt when
        the items, the column count, the icon size or the icons themselves change."""
        _x0, cols, cell, ch, pad = self.layout(w)
        rows = max(1, (len(self.items) + cols - 1) // cols)
        key = (cols, cell, ch, self.px(12), self.sig, self.mgr.icons.gen, len(self.mgr.data.get("moved_icons") or ()))
        if self._layer and self._layer[0] == key:
            return self._layer[1]
        layer = Image.new("RGBA", (cols * cell, pad + rows * ch), (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        for i, item in enumerate(self.items):
            self._paint_item(layer, dr, (i % cols) * cell, pad + (i // cols) * ch, item, w)
        self._layer = (key, layer)
        return layer

    def _paint_item(self, img, dr, cx, cy, item, w):
        """An item's icon and label in the cell whose top-left corner is (cx, cy)."""
        name, path, is_dir, mtime = item
        cell, icon, fl = self.layout(w)[2], self.icon_px(), self._label_font()
        pic = self.mgr.icons.get(self.mgr.moved_target(path) or path, icon, mtime)
        ix, iy = cx + (cell - icon) // 2, cy + self.px(7)
        if pic:                                           # bottom-aligned, like Explorer: labels start on one line
            img.alpha_composite(pic, (ix + (icon - pic.width) // 2, iy + icon - pic.height))
        elif pic is False:                                # the shell gave none; still coming: an empty spot
            aa_fill(img, (ix + icon // 6, iy + self.px(4), ix + icon - icon // 6, iy + icon - self.px(2)),
                    self.px(4), (210, 214, 222, 160), outline=self.px(2))
        ly = iy + icon + self.px(4)
        for ln in self._lines(w, name, is_dir):
            lw = dr.textlength(ln, font=fl)
            lx = round(cx + (cell - lw) / 2.0)
            dr.text((lx + 1, ly + 1), ln, font=fl, fill=(0, 0, 0, 170))
            dr.text((lx, ly), ln, font=fl, fill=(242, 244, 248, 255))
            ly += self.px(15)

    def _tile(self, i, w):
        """One item on its own picture, to draw it away from its cell (while items glide)."""
        _x0, _cols, cell, ch, _pad = self.layout(w)
        item = self.items[i]
        key = (item, cell, ch, self.px(12), self.mgr.icons.gen)
        c = self._tiles.get(item[1])
        if c and c[0] == key:
            return c[1]
        img = Image.new("RGBA", (cell, ch), (0, 0, 0, 0))
        self._paint_item(img, ImageDraw.Draw(img), 0, 0, item, w)
        if len(self._tiles) > 500:
            self._tiles.clear()
        self._tiles[item[1]] = (key, img)
        return img

    def _slot_xy(self, k, w):
        """Top-left corner of cell k in content coordinates (before scrolling)."""
        x0, cols, cell, ch, pad = self.layout(w)
        return x0 + (k % cols) * cell, pad + (k // cols) * ch

    def _draw_items(self, img, w, h, t, r):
        view_h = h - t
        if view_h <= 0:
            return
        x0, cols, cell, ch, pad = self.layout(w)
        inset = self.px(4)
        full = self.content_h(w)
        self.scroll = max(0, min(self.scroll, max(0, full - view_h)))
        if self.slots is not None:                         # items gliding to new places: each drawn on its own
            grid = Image.new("RGBA", (w, view_h), (0, 0, 0, 0))
            lifted = self.reorder["path"] if self.reorder else None
            for i, it in enumerate(self.items):
                if it[1] != lifted:
                    x, y = self.slots.get(it[1]) or self._slot_xy(i, w)
                    _blend_at(grid, self._tile(i, w), int(round(x)), int(round(y)) - self.scroll)
            marks = []
        else:
            layer = self._items_layer(w)
            grid = layer.crop((-x0, self.scroll, w - x0, self.scroll + view_h))     # shifted into place in one step
            marks = [i for i in sorted(self.sel | {self.hover}) if 0 <= i < len(self.items)]
        for i in marks:                                    # highlights go under the icons; only their own spot is redone
            name, _p, is_dir, _m = self.items[i]
            cx, cy = x0 + (i % cols) * cell, pad + (i // cols) * ch - self.scroll
            box = (cx + inset, max(0, cy + inset // 2), cx + cell - inset,
                   min(view_h, cy + self._item_h(len(self._lines(w, name, is_dir))) - inset // 2))
            if box[3] - box[1] < 2:
                continue
            under = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (0, 0, 0, 0))
            aa_fill(under, (0, 0) + under.size, self.px(6), (76, 139, 245, 110) if i in self.sel else (255, 255, 255, 34))
            grid.paste(Image.alpha_composite(under, grid.crop(box)), box[:2])
        if not self.items:
            dr = ImageDraw.Draw(grid)
            fl = self._label_font()
            msg = B("missing") if not os.path.isdir(self.folder or "") else B("empty")
            tl = dr.textlength(msg, font=fl)
            dr.text(((w - tl) / 2.0, self.px(18)), msg, font=fl, fill=(200, 205, 214, 200))
        if full > view_h:                                   # slim scrollbar
            bar_h = max(self.px(24), int(view_h * view_h / float(full)))
            by = int((view_h - bar_h) * self.scroll / float(full - view_h))
            aa_fill(grid, (w - self.px(6), by + 2, w - self.px(2), by + bar_h - 2), self.px(2), (255, 255, 255, 90))
        up = self.up()
        mask = aa_mask(w, view_h, r, (True, True, False, False) if up else (False, False, True, True))   # outer corners
        grid.putalpha(ImageChops.multiply(grid.getchannel("A"), mask))
        gy0 = 0 if up else t
        img.alpha_composite(grid, (0, gy0))
        if self.reorder and self.reorder["path"] in self.slots:      # the lifted item: a bit larger, over everything
            tile = self._tile(self.reorder["i"], w)
            bw, bh = int(tile.width * LIFT), int(tile.height * LIFT)
            big = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
            aa_fill(big, (0, 0, bw, bh - self.px(6)), self.px(8), (255, 255, 255, 38))
            big.alpha_composite(tile.resize((bw, bh), Image.BILINEAR))
            x, y = self.slots[self.reorder["path"]]
            _blend_at(img, big, int(round(x - (bw - tile.width) / 2.0)),
                      int(round(gy0 + y - self.scroll - (bh - tile.height) / 2.0)))

    @staticmethod
    def _wrap(dr, text, font, maxw):
        if dr.textlength(text, font=font) <= maxw:
            return [text]
        cut = len(text)
        while cut > 1 and dr.textlength(text[:cut], font=font) > maxw:
            cut -= 1
        sp = text.rfind(" ", 0, cut)
        if sp > 0:                                       # break at a space, like Explorer
            cut = sp
        first, rest = text[:cut].rstrip(), text[cut:].lstrip()
        if dr.textlength(rest, font=font) > maxw:
            while rest and dr.textlength(rest + "…", font=font) > maxw:
                rest = rest[:-1]
            rest = rest + "…"
        return [first, rest]

    def _blit(self, img):
        w, h = img.size
        try:
            data = img.convert("RGBa").tobytes("raw", "BGRa")       # premultiplied BGRA in one pass
        except Exception:
            a = img.getchannel("A")
            r, g, b, _ = img.convert("RGBa").split()
            data = Image.merge("RGBA", (b, g, r, a)).tobytes()
        if self._dc is None or self._dc[3] != (w, h):
            self._free_dc()
            bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            mdc = gdi32.CreateCompatibleDC(None)
            bits = VP()
            hbm = gdi32.CreateDIBSection(mdc, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
            old = gdi32.SelectObject(mdc, hbm)
            self._dc = (mdc, hbm, old, (w, h), bits)
        mdc, _hbm, _old, _sz, bits = self._dc
        ctypes.memmove(bits, data, len(data))
        x = self.rect()[0]
        cx, cy = self.mgr.to_client(x, self.win_top(h))
        self._pos = (cx, cy)
        # place, size and picture change in one call (for a child window the place is in the
        # parent's client coordinates): no frame of the old picture at the new size or place,
        # which matters for a box that grows upwards
        blend = BLENDFUNCTION(0, 0, 255, 1)
        user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(POINT(cx, cy)), ctypes.byref(SIZE(w, h)), mdc,
                                   ctypes.byref(POINT(0, 0)), 0, ctypes.byref(blend), 2)

    def _free_dc(self):
        if self._dc:
            mdc, hbm, old, _sz, _bits = self._dc
            gdi32.SelectObject(mdc, old)
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(mdc)
            self._dc = None

    # ---- geometry helpers ----
    def hit(self, x, y):
        """('title'|'resize-r'|'item'|'empty', index); the height follows the items, so only the width resizes"""
        _x, _y, w, _h = self.rect()
        h = self.cur_h or _h
        t = self.title_h()
        if not self.rolled() and h > t and not self.d.get("locked") and x >= w - self.px(EDGE):
            return "resize-r", -1
        if (y >= h - t) if self.up() else (y < t):
            return "title", -1
        x0, cols, cell, ch, pad = self.layout(w)
        gx, gy = x - x0, y - self.grid_top() - pad + self.scroll
        if gx >= 0 and gy >= 0:
            col, row = int(gx // cell), int(gy // ch)
            i = row * cols + col
            if col < cols and 0 <= i < len(self.items):
                return "item", i
        return "empty", -1

    # ---- animation ----
    def animate_to(self, h):
        if self.cur_h is None:
            self.cur_h = h
        if self.cur_h == h:
            self.anim = None
            self.render()
            return
        self.anim = (self.cur_h, h, time.perf_counter())
        user32.SetTimer(self.hwnd, 3, FRAME_MS, None)       # frames come as often as the timer allows

    def _anim_step(self):
        if not self.anim:
            user32.KillTimer(self.hwnd, 3)
            return
        a, b, t0 = self.anim
        k = min(1.0, (time.perf_counter() - t0) * 1000.0 / ANIM_MS)
        k = 1 - (1 - k) ** 3                              # ease-out: quick start, soft landing
        self.cur_h = int(a + (b - a) * k)
        if k >= 1.0:
            self.cur_h = b
            self.anim = None
            user32.KillTimer(self.hwnd, 3)
        self.render()

    def update_roll(self):
        if not self.rolled() and self.hwnd:
            user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        self.animate_to(self.target_h())
        if not self.rolled():
            self.mark_seen()

    def drag_hover(self, on):
        """An OLE drag is over the box: open it so the drop has somewhere to land."""
        self.drop_hl = on
        if on and self.d.get("rollup") and not self.temp_open:
            self.temp_open = True
            self.update_roll()
        elif not on and self.d.get("rollup") and self.temp_open:
            user32.SetTimer(self.hwnd, 2, LEAVE_CLOSE_MS, None)
        self.render()

    # ---- window procedure ----
    def on_msg(self, msg, wp, lp):
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg in (WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP):
            x = ctypes.c_short(lp & 0xFFFF).value
            y = ctypes.c_short((lp >> 16) & 0xFFFF).value
            if msg == WM_MOUSEMOVE:
                return self._move(x, y, wp)
            if msg == WM_LBUTTONDOWN:
                return self._down(x, y, wp)
            if msg == WM_LBUTTONUP:
                return self._up(x, y)
            if msg == WM_LBUTTONDBLCLK:
                return self._dbl(x, y)
            return self._rclick(x, y)
        if msg == WM_MOUSEWHEEL:
            delta = ctypes.c_short((wp >> 16) & 0xFFFF).value
            _x0, _c, _cell, ch, _p = self.layout(self.rect()[2])
            self.scroll -= int(delta / 120.0 * ch * 0.6)
            self.render()
            return 0
        if msg == WM_MOUSELEAVE:
            self.tracking = False
            changed = self.hover != -1 or self.hovering
            self.hover, self.hovering = -1, False
            if changed:
                self.render()
            user32.KillTimer(self.hwnd, 1)
            self.open_pending = False
            if self.d.get("rollup") and self.temp_open and not self.press:
                user32.SetTimer(self.hwnd, 2, LEAVE_CLOSE_MS, None)
            return 0
        if msg == WM_TIMER:
            if wp == 1:                                       # hover over a rolled title -> open
                user32.KillTimer(self.hwnd, 1)
                self.open_pending = False
                if self.rolled():
                    self.temp_open = True
                    self.update_roll()
            elif wp == 2:                                     # mouse left -> roll back up
                user32.KillTimer(self.hwnd, 2)
                if not self._mouse_inside() and not self.press and not self.drop_hl:
                    self.temp_open = False
                    self.update_roll()
            elif wp == 3:
                self._anim_step()
            elif wp == 4:
                self._reorder_step()
            return 0
        if msg == WM_SETCURSOR:
            p = POINT()
            user32.GetCursorPos(ctypes.byref(p))
            user32.ScreenToClient(self.hwnd, ctypes.byref(p))
            zone = self.hit(p.x, p.y)[0]
            cur = IDC_SIZEWE if zone == "resize-r" else IDC_ARROW
            user32.SetCursor(user32.LoadCursorW(None, VP(cur)))
            return 1
        if msg == WM_CAPTURECHANGED:
            if self.press and self.press.get("mode") in ("move", "resize"):
                self._finish_press()
            elif self.press and self.press.get("mode") == "reorder":     # capture taken away: put things back
                self.press = None
                self._end_reorder(commit=False)
            return 0
        return None

    def _mouse_inside(self):
        p = POINT()
        user32.GetCursorPos(ctypes.byref(p))
        return user32.WindowFromPoint(p) == self.hwnd

    def _track(self):
        if not self.tracking:
            tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE, self.hwnd, 0)
            user32.TrackMouseEvent(ctypes.byref(tme))
            self.tracking = True

    def _move(self, x, y, wp):
        self._track()
        user32.KillTimer(self.hwnd, 2)
        redraw = not self.hovering
        self.hovering = True
        pr = self.press
        if pr:
            p = POINT()
            user32.GetCursorPos(ctypes.byref(p))
            mode = pr["mode"]
            if mode == "item" and (abs(p.x - pr["sx"]) > 4 or abs(p.y - pr["sy"]) > 4):
                if len(self.sel) <= 1 and 0 <= pr["i"] < len(self.items):
                    pr["mode"] = "reorder"                   # one item: lift it and move it around the box
                    self._start_reorder(pr["i"], x, y)
                    return 0
                self._drag_out([self.items[i][1] for i in sorted(self.sel) if i < len(self.items)])
                return 0
            if mode == "reorder":
                _x, _y, w, _h = self.rect()
                m = self.px(10)
                if x < -m or y < -m or x > w + m or y > (self.cur_h or _h) + m:    # out of the box: a real drag
                    path = self.reorder["path"] if self.reorder else None
                    self._end_reorder(commit=False)
                    if path:
                        self._drag_out([path])
                    return 0
                self._reorder_move(x, y)
                return 0
            if mode == "title" and not self.d.get("locked") and (abs(p.x - pr["sx"]) > 3 or abs(p.y - pr["sy"]) > 3):
                pr["mode"] = "move"
            if pr["mode"] == "move":
                nx, ny = self.mgr.snap_move(self, p.x - pr["dx"], p.y - pr["dy"])
                r = self.rect()
                self.d.setdefault("rects", {})[self.mgr.sig] = [nx, ny, r[2], r[3]]
                self.place()
                return 0
            if mode == "resize":
                r = self.rect()
                right = self.mgr.snap_resize(self, p.x + pr["ox"])
                min_w = self.px(PAD) * 2 + self.icon_px() + self.px(40)
                self.d.setdefault("rects", {})[self.mgr.sig] = [r[0], r[1], max(min_w, right - r[0]), r[3]]
                self.cur_h = self.rect()[3]                   # fewer columns, more rows: the height follows
                self.render()
                return 0
            return 0
        zone, i = self.hit(x, y)
        if zone == "title" and self.rolled() and not self.open_pending:
            # armed once per visit: re-arming on every move kept a moving cursor waiting
            self.open_pending = True
            user32.SetTimer(self.hwnd, 1, HOVER_OPEN_MS, None)
        if i != self.hover or redraw:
            self.hover = i
            self.render()
        return 0

    def _down(self, x, y, wp):
        zone, i = self.hit(x, y)
        p = POINT()
        user32.GetCursorPos(ctypes.byref(p))
        r = self.rect()
        self.press = {"zone": zone, "mode": zone, "i": i, "sx": p.x, "sy": p.y, "dx": p.x - r[0], "dy": p.y - r[1]}
        if zone.startswith("resize"):
            self.press.update(mode="resize", ox=r[0] + r[2] - p.x, oy=r[1] + r[3] - p.y)
        elif zone == "item":
            if wp & MK_CONTROL:
                self.sel ^= {i}
            elif i not in self.sel:
                self.sel = {i}
            self.render()
        elif zone == "empty":
            if self.sel:
                self.sel = set()
                self.render()
        user32.SetCapture(self.hwnd)
        return 0

    def _up(self, x, y):
        self._finish_press()                    # before ReleaseCapture: WM_CAPTURECHANGED would cancel a reorder
        if user32.GetCapture() == self.hwnd:
            user32.ReleaseCapture()
        return 0

    def _drag_out(self, paths):
        """Hand the items over to a shell drag (to the desktop, another box, a folder, an app)."""
        self.press = None
        user32.ReleaseCapture()
        self.mgr.defer(lambda: self.mgr.drag_out(self, paths))

    # ---- moving items around inside the box (like app icons on a phone) ----
    def _start_reorder(self, i, x, y):
        w = self.rect()[2]
        sx, sy = self._slot_xy(i, w)
        cy = y - self.grid_top() + self.scroll                 # content coordinates
        self.reorder = {"i": i, "path": self.items[i][1], "target": i, "gx": x - sx, "gy": cy - sy,
                        "x": x, "y": y, "t": time.perf_counter()}
        self.slots = {it[1]: list(self._slot_xy(k, w)) for k, it in enumerate(self.items)}
        self.hover = -1
        self._glide_t = time.perf_counter()
        self._reorder_move(x, y)
        user32.SetTimer(self.hwnd, 4, FRAME_MS, None)

    def _reorder_move(self, x, y):
        r = self.reorder
        if not r:
            return
        w = self.rect()[2]
        x0, cols, cell, ch, pad = self.layout(w)
        r["x"], r["y"] = x, y
        lx, ly = x - r["gx"], y - self.grid_top() + self.scroll - r["gy"]    # the lifted item's cell corner
        self.slots[r["path"]] = [lx, ly]
        col = min(cols - 1, max(0, int((lx + cell / 2.0 - x0) // cell)))   # the cell under the item's middle
        row = max(0, int((ly + ch / 2.0 - pad) // ch))
        r["target"] = min(len(self.items) - 1, row * cols + col)

    def _reorder_order(self, r):
        order = [k for k in range(len(self.items)) if k != r["i"]]
        order.insert(r["target"], r["i"])
        return order

    def _reorder_step(self):
        """One frame: the others glide to their places in the order being made; the lifted
        item follows the cursor; near the top or bottom edge the box scrolls."""
        if self.slots is None or not self.hwnd:
            user32.KillTimer(self.hwnd, 4)
            return
        now = time.perf_counter()
        last = getattr(self, "_glide_t", now)
        self._glide_t = now
        k = 1.0 - math.exp(-GLIDE * max(0.0, min(0.1, now - last)))
        w = self.rect()[2]
        r = self.reorder
        if r:
            g0, edge = self.grid_top(), self.px(26)
            g1 = (self.cur_h or 0) - (self.title_h() if self.up() else 0)
            if r["y"] > g1 - edge:
                self.scroll += self.px(8)
            elif r["y"] < g0 + edge and self.scroll > 0:
                self.scroll = max(0, self.scroll - self.px(8))
            self._reorder_move(r["x"], r["y"])
            order = self._reorder_order(r)
        else:
            order = list(range(len(self.items)))
        settled = True
        for slot, j in enumerate(order):
            path = self.items[j][1]
            if r and path == r["path"]:
                continue
            gx, gy = self._slot_xy(slot, w)
            pos = self.slots.setdefault(path, [gx, gy])
            pos[0] += (gx - pos[0]) * k
            pos[1] += (gy - pos[1]) * k
            if abs(gx - pos[0]) > 0.5 or abs(gy - pos[1]) > 0.5:
                settled = False
            else:
                pos[0], pos[1] = gx, gy
        if settled and not r:                                  # all home: back to the one-picture drawing
            self.slots = None
            user32.KillTimer(self.hwnd, 4)
        self.render()

    def _end_reorder(self, commit):
        r, self.reorder = self.reorder, None
        if self.hwnd:
            user32.SetTimer(self.hwnd, 4, FRAME_MS, None)       # glide on to the final places
        if not r:
            return
        self._glide_t = time.perf_counter()
        if commit and r["target"] != r["i"]:
            order = self._reorder_order(r)
            self.items = [self.items[j] for j in order]
            self.d["order"] = [it[0] for it in self.items]
            self.d["sort"] = "manual"
            self.sig = tuple((n, m) for n, _p, _d, m in self.items)
            self.sel = {order.index(r["i"])}
            self.mgr.save_soon()

    def _finish_press(self):
        pr, self.press = self.press, None
        if pr and pr["mode"] == "reorder":
            self._end_reorder(commit=True)
            return
        if pr and pr["mode"] in ("move", "resize"):
            if pr["mode"] == "move":
                self.mgr.forget_layout()
            self.set_rect(self.rect())
            self.cur_h = self.target_h()
            self.render()

    def _dbl(self, x, y):
        zone, i = self.hit(x, y)
        if zone == "item":
            path = self.items[i][1]
            self.mgr.defer(lambda: self.mgr.open_item(path))
        elif zone == "title":                             # like iTop: double-click the title to rename
            self.mgr.defer(lambda: self.mgr.rename_dialog(self))
        return 0

    def _rclick(self, x, y):
        zone, i = self.hit(x, y)
        p = POINT()
        user32.GetCursorPos(ctypes.byref(p))
        if zone == "item":
            if i not in self.sel:
                self.sel = {i}
                self.render()
            paths = [self.items[j][1] for j in sorted(self.sel) if j < len(self.items)]
            self.mgr.defer(lambda: self.mgr.item_menu(self, paths, p.x, p.y))
        else:
            self.mgr.defer(lambda: self.mgr.box_menu(self, p.x, p.y))
        return 0


# ---------------------------------------------------------------------------
# double-click on the empty desktop (low-level mouse hook on its own thread)
# ---------------------------------------------------------------------------
class DesktopDoubleClick:
    def __init__(self, log):
        self.log = log
        self.events = queue.Queue()
        self._tid = 0
        self._last = (0, 0, 0)
        self._proc = HOOKPROC(self._cb)

    def _cb(self, code, wp, lp):
        if code == 0 and wp == WM_LBUTTONDOWN:
            ms = ctypes.cast(lp, ctypes.POINTER(MSLLHOOKSTRUCT))[0]
            t, x, y = ms.time, ms.pt.x, ms.pt.y
            lt, lx, ly = self._last
            if t - lt <= user32.GetDoubleClickTime() and abs(x - lx) <= 4 and abs(y - ly) <= 4:
                self.events.put((x, y))
                self._last = (0, 0, 0)
            else:
                self._last = (t, x, y)
        return user32.CallNextHookEx(None, code, wp, lp)

    def start(self):
        ready = threading.Event()

        def run():
            self._tid = kernel32.GetCurrentThreadId()
            hook = user32.SetWindowsHookExW(14, self._proc, _HINST, 0)      # WH_MOUSE_LL
            if not hook:
                self.log("boxes: mouse hook failed: %d" % ctypes.get_last_error())
            ready.set()
            msg = (ctypes.c_byte * 64)()
            while user32.GetMessageW(msg, None, 0, 0) > 0:
                pass
            if hook:
                user32.UnhookWindowsHookEx(hook)
        threading.Thread(target=run, daemon=True, name="hdc-box-dblclick").start()
        ready.wait(2.0)

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._tid = 0


def listview_hit(lv, x, y):
    """Item index under a screen point in Explorer's desktop list view (-1: empty)."""
    pid = DWORD()
    user32.GetWindowThreadProcessId(lv, ctypes.byref(pid))
    hp = kernel32.OpenProcess(0x8 | 0x10 | 0x20, False, pid.value)      # VM_OPERATION|VM_READ|VM_WRITE
    if not hp:
        return 0
    try:
        size = ctypes.sizeof(LVHITTESTINFO)
        mem = kernel32.VirtualAllocEx(hp, None, size, 0x1000, 0x04)
        if not mem:
            return 0
        try:
            p = POINT(x, y)
            user32.ScreenToClient(lv, ctypes.byref(p))
            info = LVHITTESTINFO(p, 0, -1, 0, 0)
            kernel32.WriteProcessMemory(hp, mem, ctypes.byref(info), size, None)
            return int(user32.SendMessageW(lv, LVM_HITTEST, 0, mem))
        finally:
            kernel32.VirtualFreeEx(hp, mem, 0, 0x8000)
    finally:
        kernel32.CloseHandle(hp)


def _gaps(rects, limit):
    """Gaps between neighbouring boxes (side by side or one above the other)."""
    out = set()
    for a in rects:
        for b in rects:
            if a is b:
                continue
            if a[1] < b[1] + b[3] and b[1] < a[1] + a[3]:
                g = b[0] - (a[0] + a[2])
                if 0 <= g <= limit:
                    out.add(g)
            if a[0] < b[0] + b[2] and b[0] < a[0] + a[2]:
                g = b[1] - (a[1] + a[3])
                if 0 <= g <= limit:
                    out.add(g)
    return out


def _nearest(v, cands, reach):
    best = min(cands, key=lambda c: abs(c - v), default=None)
    return best if best is not None and abs(best - v) <= reach else v


# ---------------------------------------------------------------------------
# the manager
# ---------------------------------------------------------------------------
_ACTIVE = {"mgr": None}


def _class_wndproc(hwnd, msg, wp, lp):
    mgr = _ACTIVE["mgr"]
    if mgr is not None:
        return mgr._wndproc(hwnd, msg, wp, lp)
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


_CLASS_PROC = WNDPROC(_class_wndproc)       # registered once per process; messages go to the live manager
_CLASSES_DONE = []


class BoxManager:
    CLASS = "HDContainerBox"
    OWNER_CLASS = "HDContainerBoxMenu"

    def __init__(self, app, get_lang, log, data_dir):
        global LANG_GET
        LANG_GET = get_lang
        self.app, self.root, self.log = app, app.root, log
        self.path = os.path.join(data_dir, "HDContainer_boxes.json")
        self.desktop = desktop_dir()
        self.box_root = os.path.join(self.desktop, ROOT_NAME)
        self.data = self._load()
        self.boxes = []
        self.by_hwnd = {}
        self.host = None
        self.sig = screen_signature()
        self.hidden = False
        self.icons = IconCache()
        self.draft = None
        self.settings_target = None         # box id the Settings tab opens on (None: all boxes)
        self._ui = None                     # Settings tab widgets while it is shown
        self._arrange_undo = None           # box places before Settings → Arrange, until Save / Cancel
        self._save_at = 0.0
        self._scan_at = 0.0
        self._menu_cm = None                # IContextMenu2/3 while a shell menu is open
        self.dbl = None
        self.ole_ok = ole32.OleInitialize(None) in (0, 1)     # S_OK / S_FALSE
        if not self.ole_ok:
            log("boxes: OLE unavailable on the UI thread, drag-and-drop off")
        _ACTIVE["mgr"] = self
        if not _CLASSES_DONE:
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(wc)
            wc.style = CS_DBLCLKS
            wc.lpfnWndProc = _CLASS_PROC
            wc.hInstance = _HINST
            wc.hCursor = user32.LoadCursorW(None, VP(IDC_ARROW))
            wc.lpszClassName = self.CLASS
            user32.RegisterClassExW(ctypes.byref(wc))
            wc2 = WNDCLASSEXW()
            wc2.cbSize = ctypes.sizeof(wc2)
            wc2.lpfnWndProc = _CLASS_PROC
            wc2.hInstance = _HINST
            wc2.lpszClassName = self.OWNER_CLASS
            user32.RegisterClassExW(ctypes.byref(wc2))
            _CLASSES_DONE.append(True)
        self.owner = user32.CreateWindowExW(WS_EX_TOOLWINDOW, self.OWNER_CLASS, "HDContainer boxes", WS_POPUP,
                                            0, 0, 0, 0, None, None, _HINST, None)
        if self.data.get("icons_hidden"):                    # we quit (or died) with the icons hidden
            self._show_icons(True)
            self.data["icons_hidden"] = False
            self.save()
        for d in self.data["boxes"]:
            self.boxes.append(Box(self, d))
        self._apply_quickhide()
        self.root.after(600, self._tick)

    # ---- persistence ----
    def _load(self):
        data = None
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
        if not (isinstance(data, dict) and isinstance(data.get("boxes"), list)):
            return {"boxes": [], "style": {}, "box_styles": {}, "opts": {}, "v": DATA_VERSION}
        v = data.get("v", 1)
        data.setdefault("style", {})
        data.setdefault("box_styles", {})
        data.setdefault("opts", {})
        if v < 2:
            # 1.9.0 made boxes always open; the point of a box is to stay rolled up and open on hover
            for b in data["boxes"]:
                b["rollup"] = True
                b.pop("pinned", None)
        if v < 3:
            # color/alpha moved into per-box styles; sorting rules are gone
            for b in data["boxes"]:
                st = {}
                if b.get("color"):
                    st["color"] = b["color"]
                al = b.get("alpha")
                if al is not None and abs(float(al) - 0.9) > 0.01 and abs(float(al) - 0.7) > 0.01:
                    st["transp"] = int(round((1 - float(al)) * 100))
                if st:
                    data["box_styles"][b["id"]] = st
                for k in ("color", "alpha", "pinned"):
                    b.pop(k, None)
            if "quickhide" in data:
                data["opts"]["quickhide"] = bool(data.pop("quickhide"))
            for k in ("rules", "desktop_seen"):
                data.pop(k, None)
        data["v"] = DATA_VERSION
        return data

    def save_soon(self):
        self._save_at = time.time() + 1.0

    def save(self):
        self._save_at = 0.0
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as ex:
            self.log("boxes save failed: %r" % ex)

    # ---- style ----
    def _src(self):
        return self.draft if self.draft is not None else self.data

    def style_of(self, box_id):
        src = self._src()
        st = dict(STYLE_DEFAULT)
        st.update(src.get("style") or {})
        if box_id:
            st.update((src.get("box_styles") or {}).get(box_id) or {})
        return st

    def opt(self, key):
        return (self._src().get("opts") or {}).get(key, OPTS_DEFAULT[key])

    def redraw_all(self):
        for b in self.boxes:
            if b.hwnd:
                b.cur_h = b.target_h()
                b.render()

    # ---- app glue ----
    def defer(self, fn):
        self.app._defer(fn)

    def has_boxes(self):
        return bool(self.boxes)

    def to_client(self, x, y):
        if not self.host:
            return x, y
        p = POINT(0, 0)
        user32.ClientToScreen(self.host, ctypes.byref(p))
        return x - p.x, y - p.y

    # ---- window procedure (no tk here) ----
    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg in (WM_INITMENUPOPUP, WM_DRAWITEM, WM_MEASUREITEM, WM_MENUCHAR) and self._menu_cm:
                cm, level = self._menu_cm
                res = LRESULT()
                if level == 3:
                    if _vcall(cm, 7, HRESULT, [UINT, WPARAM, LPARAM, ctypes.POINTER(LRESULT)],
                              msg, wp, lp, ctypes.byref(res)) == 0:
                        return res.value
                elif _vcall(cm, 6, HRESULT, [UINT, WPARAM, LPARAM], msg, wp, lp) == 0:
                    return 0
            box = self.by_hwnd.get(hwnd)
            if box is not None:
                r = box.on_msg(msg, wp, lp)
                if r is not None:
                    return r
        except Exception as ex:
            self.log("box wndproc %x failed: %r" % (msg, ex))
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    # ---- timer: host, listings, icons, double-click, saving ----
    def _tick(self):
        if self.icons.dirty and not self.boxes:
            self.icons.dirty = False
            self._refresh_preview()
        if not self.boxes:                                # nothing on the desktop: stay idle
            if self._save_at and time.time() >= self._save_at:
                self.save()
            self.root.after(1000, self._tick)
            return
        try:
            self._ensure_windows()
            now = time.time()
            if now >= self._scan_at:
                self._scan_at = now + SCAN_EVERY
                changed = False
                for b in self.boxes:
                    if b.hwnd and not b.reorder and b.refresh_items():
                        changed = True
                        if b.rolled():
                            b.render()
                        else:
                            b.mark_seen()
                            b.animate_to(b.target_h())             # items came or went: the height follows
                if changed:
                    self._check_moved_icons()
            if self.icons.dirty:
                self.icons.dirty = False
                for b in self.boxes:
                    if b.hwnd and not b.rolled():
                        b.render()
                self._refresh_preview()
            if self.dbl is not None:
                while not self.dbl.events.empty():
                    self._desktop_dblclick(*self.dbl.events.get())
            if self._save_at and now >= self._save_at:
                self.save()
        except Exception as ex:
            self.log("boxes tick failed: %r" % ex)
        self.root.after(TICK_MS if any(b.anim for b in self.boxes) else 250, self._tick)

    def _ensure_windows(self):
        sig = screen_signature()
        host, dv = desktop_host()
        if not host:
            return
        host = box_parent(host, dv)
        if host != self.host or sig != self.sig:
            moved = host != self.host
            if moved:
                self.log("boxes: desktop window %s" % _class_of(host))
            self.host, self.sig = host, sig
            for b in self.boxes:
                b._pos = None                                   # new parent or screens: place it again
                if b.hwnd and moved and user32.IsWindow(b.hwnd):
                    user32.SetParent(b.hwnd, host)
                if b.hwnd:
                    b.cur_h = b.target_h()
                    b.render()
        self._keep_on_top()
        for b in self.boxes:
            if b.hwnd and not user32.IsWindow(b.hwnd):        # Explorer restarted
                self.by_hwnd.pop(b.hwnd, None)
                b.hwnd = None
                b._dc = None
            if not b.hwnd:
                b.create(self.host)

    def _keep_on_top(self):
        """Other programs drawing in the desktop layer (iTop's full-screen box window,
        a re-created icon view) can end up above our boxes and hide them."""
        ours = {b.hwnd for b in self.boxes if b.hwnd}
        if not ours or not self.host:
            return
        h, n = user32.GetWindow(self.host, 5), 0                # GW_CHILD: topmost child
        while h and h in ours:
            n += 1
            h = user32.GetWindow(h, 2)                            # GW_HWNDNEXT
        if n < len(ours):
            for b in self.boxes:
                if b.hwnd:
                    user32.SetWindowPos(b.hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def on_explorer_restart(self):
        self.host = None
        for b in self.boxes:
            if b.hwnd and not user32.IsWindow(b.hwnd):
                self.by_hwnd.pop(b.hwnd, None)
                b.hwnd = None
                b._dc = None

    # ---- lining boxes up: even gaps, aligned edges ----
    def _others(self, box):
        return [o.visible_rect() for o in self.boxes if o is not box and o.hwnd]

    def snap_move(self, box, x, y):
        if not self.opt("snap"):
            return x, y
        _x, _y, w, h = box.visible_rect()
        reach = box.px(12)
        rects = self._others(box)
        gaps = _gaps(rects, box.px(240)) or {box.px(8)}   # repeat the gaps already there; 8 px if none
        wa = work_area_at(x + w // 2, y + h // 2)
        cx = [wa[0], wa[2] - w]
        for ox, oy, ow, oh in rects:
            cx += [ox, ox + ow - w, ox + (ow - w) // 2]
            if y < oy + oh + reach and oy < y + h + reach:          # same row: sit next to it
                cx += [ox + ow + g for g in gaps] + [ox - w - g for g in gaps]
        nx = _nearest(x, cx, reach)
        cy = [wa[1], wa[3] - h]
        for ox, oy, ow, oh in rects:
            cy += [oy, oy + oh - h]
            if nx < ox + ow + reach and ox < nx + w + reach:        # same column: sit under / above it
                cy += [oy + oh + g for g in gaps] + [oy - h - g for g in gaps]
        return nx, _nearest(y, cy, reach)

    def arrange(self, where):
        """Settings → Arrange: every box on its own monitor, all with the box width, the same gap
        between them and from the screen edge. where: top-left, top-center, top-right (rows,
        wrapping when a row is full) or col-left, col-right (columns, the next one beside)."""
        (self.draft or self.data)["opts"]["layout"] = where   # width / gap changes re-apply it
        if not self.boxes:
            return
        self._remember_places()
        gap = max(0, int(self.opt("gap")))
        groups = {}
        for b in self.boxes:
            x, y, w, _h = b.rect()
            groups.setdefault(tuple(work_area_at(x + w // 2, y + b.title_h() // 2)), []).append(b)
        for wa, group in groups.items():
            bw = max(group[0].px(PAD) * 2 + group[0].icon_px() + group[0].px(40),
                     min(int(self.opt("box_w")), wa[2] - wa[0] - 2 * gap))
            if where.startswith(("top", "bottom")):
                bottom = where.startswith("bottom")              # rows from the bottom edge, opening upwards
                side = where.split("-")[1]
                group.sort(key=lambda b: (b.rect()[0], b.rect()[1]))       # keep their left-to-right order
                per_row = max(1, (wa[2] - wa[0] - 2 * gap + gap) // (bw + gap))
                edge = wa[3] - gap if bottom else wa[1] + gap       # the free edge of the next row
                for i in range(0, len(group), per_row):
                    row = group[i:i + per_row]
                    total = len(row) * bw + (len(row) - 1) * gap
                    x = {"left": wa[0] + gap, "right": wa[2] - gap - total}.get(side, wa[0] + (wa[2] - wa[0] - total) // 2)
                    t = max(b.title_h() for b in row)
                    y = edge - t if bottom else edge                # (x, y) of a box is its title
                    for b in row:
                        b.set_rect([x, y, bw, 0])
                        x += bw + gap
                    rh = max(b.rest_h() for b in row)
                    edge = (y + t - rh - gap) if bottom else (y + rh + gap)
            else:
                group.sort(key=lambda b: (b.rect()[1], b.rect()[0]))       # keep their top-to-bottom order
                step = bw + gap if where == "col-left" else -(bw + gap)
                x = wa[0] + gap if where == "col-left" else wa[2] - gap - bw
                y = wa[1] + gap
                for b in group:
                    if y > wa[1] + gap and y + b.title_h() > wa[3]:          # column full: start the next one
                        x, y = x + step, wa[1] + gap
                    b.set_rect([x, y, bw, 0])
                    y += b.rest_h() + gap
        self.redraw_all()

    def _remember_places(self):
        """Box places before the first change from Settings, for Cancel."""
        if self._arrange_undo is None:
            self._arrange_undo = {b.id: json.loads(json.dumps([b.d.get("rects", {}), b.d.get("last")]))
                                  for b in self.boxes}

    def _geometry_soon(self):
        """Width / gap moved in Settings: apply shortly (a dragged slider sends many values)."""
        if getattr(self, "_geo_job", None):
            self.root.after_cancel(self._geo_job)
        self._geo_job = self.root.after(30, self._apply_geometry)

    def _apply_geometry(self):
        """Live, before Save: the boxes on the desktop take the new width (and gap)."""
        self._geo_job = None
        if self.draft is None or not self.boxes:
            return
        layout = self.opt("layout")
        if layout:
            self.arrange(layout)                                # same place, new width / gap
            return
        self._remember_places()
        bw = int(self.opt("box_w"))
        for b in self.boxes:
            x, y, _w, _h = b.rect()
            b.set_rect([x, y, bw, 0])
        self.redraw_all()

    def forget_layout(self):
        """A box was moved by hand: the boxes no longer keep the Arrange place."""
        for src in (self.data, self.draft):
            if src:
                (src.get("opts") or {}).pop("layout", None)

    def snap_resize(self, box, right):
        """The right edge while the width is dragged (the height follows the items)."""
        if not self.opt("snap"):
            return right
        x, y, _w, h = box.visible_rect()
        reach = box.px(12)
        rects = self._others(box)
        gaps = _gaps(rects, box.px(240)) or {box.px(8)}   # repeat the gaps already there; 8 px if none
        wa = work_area_at(x + 10, y + 10)
        _x0, _cols, cell, _ch, pad = box.layout(_w)
        cands = [wa[2]] + [x + pad * 2 + n * cell for n in range(1, 30)]     # whole columns of icons
        for ox, oy, ow, oh in rects:
            cands += [ox + ow]
            if y < oy + oh and oy < y + h:
                cands += [ox - g for g in gaps]
        return _nearest(right, cands, reach)

    # ---- items ----
    def open_item(self, path):
        shell32.ShellExecuteW(None, None, path, None, os.path.dirname(path), 1)

    def receive(self, box, files, func):
        folder = box.folder
        if folder and os.path.isdir(folder):
            file_op(func, files, folder, self.log)
            self._scan_at = time.time() + 0.6

    def add_links(self, box, links):
        """Shell items with no file of their own go into a box as shortcuts. A standard desktop
        icon (This PC, Recycle Bin…) dragged from the desktop leaves it, like a moved file, and
        comes back when its shortcut leaves the boxes (see _check_moved_icons)."""
        folder = box.folder
        if not folder or not os.path.isdir(folder):
            return
        moved = self.data.setdefault("moved_icons", {})
        for parse, name, raw, from_desktop, fs in links:
            clsid = desktop_icon_clsid(parse, from_desktop, fs)
            if clsid and clsid in moved and os.path.isfile(moved[clsid]):
                continue                                        # already in a box
            path = unique_path(folder, safe_name(name or "Shortcut") + ".lnk")
            if not make_link(path, raw):
                self.log("box shortcut failed: %s" % parse)
                continue
            if clsid and from_desktop:
                moved[clsid] = path
                try:
                    show_desktop_icon(clsid, False)
                except OSError as ex:
                    self.log("hide desktop icon failed: %r" % ex)
        self.save()
        self._scan_at = 0

    def moved_target(self, path):
        """'::{CLSID}' when path is the shortcut of a desktop icon moved into a box."""
        n = os.path.normcase(path)
        for clsid, p in (self.data.get("moved_icons") or {}).items():
            if os.path.normcase(p) == n:
                return "::" + clsid
        return None

    def _check_moved_icons(self):
        """A moved desktop icon whose shortcut has left the boxes (deleted, moved out, its box
        deleted) is shown on the desktop again; its shortcut there gives way to the real icon."""
        moved = self.data.get("moved_icons")
        if not moved:
            return
        folders = {os.path.normcase(b.folder) for b in self.boxes if b.folder}
        changed = False
        for clsid, path in list(moved.items()):
            if os.path.isfile(path) and os.path.normcase(os.path.dirname(path)) in folders:
                continue
            changed = True
            found = None
            for b in self.boxes:                                # moved to another box or renamed
                for name, p, is_dir, _m in b.items:
                    if not is_dir and name.lower().endswith(".lnk") and link_is_icon(p, clsid):
                        found = p
                        break
                if found:
                    break
            if found:
                moved[clsid] = found
                continue
            back = os.path.join(self.desktop, os.path.basename(path))
            if os.path.isfile(back) and link_is_icon(back, clsid):
                try:
                    os.remove(back)
                except OSError:
                    pass
            del moved[clsid]
            try:
                show_desktop_icon(clsid, True)
            except OSError as ex:
                self.log("show desktop icon failed: %r" % ex)
        if changed:
            self.save()

    def drag_out(self, box, paths):
        if not paths or not self.ole_ok:
            return
        items = ShellItems(paths)
        pdo = items.ui_object(None, IID_IDataObject)
        if not pdo:
            items.free()
            return
        helper = VP()
        try:
            if ole32.CoCreateInstance(ctypes.byref(CLSID_DragDropHelper), None, 1, ctypes.byref(IID_IDragSourceHelper),
                                      ctypes.byref(helper)) == 0 and helper:
                pic = self.icons.get(paths[0], box.icon_px(),
                                     os.path.getmtime(paths[0]) if os.path.exists(paths[0]) else 0)
                if pic:
                    sdi = SHDRAGIMAGE(SIZE(pic.width, pic.height), POINT(pic.width // 2, pic.height // 2),
                                      image_to_hbitmap(pic), 0xFFFFFFFF)
                    _vcall(helper, 3, HRESULT, [VP, VP], ctypes.byref(sdi), pdo)
            src = DropSource()
            eff = DWORD(0)
            ole32.DoDragDrop(pdo, src.ptr, DROPEFFECT_COPY | DROPEFFECT_MOVE | DROPEFFECT_LINK, ctypes.byref(eff))
        except Exception as ex:
            self.log("box drag failed: %r" % ex)
        finally:
            _release(helper)
            _release(pdo)
            items.free()
            self._scan_at = time.time() + 0.5

    def _run_menu(self, hmenu, x, y):
        user32.SetForegroundWindow(self.owner)
        cmd = user32.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, self.owner, None)
        user32.PostMessageW(self.owner, WM_NULL, 0, 0)
        return cmd

    def item_menu(self, box, paths, x, y):
        # a moved desktop icon gets its own menu (Empty Recycle Bin, Properties of This PC…)
        target = self.moved_target(paths[0]) if len(paths) == 1 else None
        items = ShellItems([target] if target else paths)
        cm = items.ui_object(self.owner, IID_IContextMenu)
        hmenu = user32.CreatePopupMenu()
        cm3 = VP()
        try:
            if cm:
                for iid, level in ((IID_IContextMenu3, 3), (IID_IContextMenu2, 2)):
                    if _vcall(cm, 0, HRESULT, [VP, ctypes.POINTER(VP)], ctypes.byref(iid), ctypes.byref(cm3)) == 0:
                        self._menu_cm = (cm3, level)
                        break
                _vcall(cm, 3, HRESULT, [VP, UINT, UINT, UINT, UINT], hmenu, 0, 1, 0x7FFF, 0)
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hmenu, MF_STRING, 0x8001, B("to_desktop"))
            cmd = self._run_menu(hmenu, x, y)
            self._menu_cm = None
            if cmd == 0x8001:
                file_op(FO_MOVE, paths, self.desktop, self.log)
            elif cm and 1 <= cmd <= 0x7FFF:
                verb = ctypes.create_unicode_buffer(64)
                _vcall(cm, 5, HRESULT, [ctypes.c_size_t, UINT, VP, VP, UINT], cmd - 1, 4, None, verb, 64)
                if verb.value.lower() == "rename" and len(paths) == 1:
                    self.defer(lambda: self.rename_item(paths[0]))
                else:
                    ci = CMINVOKECOMMANDINFOEX()
                    ci.cbSize = ctypes.sizeof(ci)
                    ci.fMask = 0x4000 | 0x20000000              # CMIC_MASK_UNICODE | CMIC_MASK_PTINVOKE
                    ci.hwnd = self.owner
                    ci.lpVerb = VP(cmd - 1)
                    ci.lpVerbW = VP(cmd - 1)
                    ci.nShow = 1
                    ci.ptInvoke = POINT(x, y)
                    _vcall(cm, 4, HRESULT, [VP], ctypes.byref(ci))
        except Exception as ex:
            self.log("box item menu failed: %r" % ex)
        finally:
            self._menu_cm = None
            _release(cm3)
            _release(cm)
            user32.DestroyMenu(hmenu)
            items.free()
            self._scan_at = time.time() + 0.5

    def rename_item(self, path):
        old = os.path.basename(path)
        new = self.app._ask_string(B("rename").rstrip("…"), B("file_name"), old)
        if new and new != old:
            try:
                os.rename(path, os.path.join(os.path.dirname(path), safe_name(new)))
            except OSError as ex:
                self.log("rename failed: %r" % ex)
            self._scan_at = 0

    def rename_dialog(self, box):
        new = self.app._ask_string(B("rename").rstrip("…"), B("name_prompt"), box.name)
        if new:
            self.rename_box(box, new)

    def box_menu(self, box, x, y):
        menu = user32.CreatePopupMenu()
        subs = []
        try:
            def add(m, cid, text, checked=False):
                user32.AppendMenuW(m, MF_STRING | (MF_CHECKED if checked else 0), cid, text)

            if self._clipboard_files()[0]:
                add(menu, 0x9001, B("paste"))
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            add(menu, 0x9002, B("new_box"))
            add(menu, 0x9003, B("new_folder_box"))
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            add(menu, 0x9004, B("rename"))
            add(menu, 0x9010, B("appearance_menu"))
            add(menu, 0x9005, B("rollup"), checked=bool(box.d.get("rollup")))
            so = user32.CreatePopupMenu()
            subs.append(so)
            user32.AppendMenuW(menu, MF_POPUP, so, B("sort"))
            for i, k in enumerate(("name", "type", "date", "manual")):
                add(so, 0x9300 + i, B("sort_" + k), checked=box.d.get("sort", "name") == k)
            add(menu, 0x9011, B("lock"), checked=bool(box.d.get("locked")))
            add(menu, 0x9012, B("no_quickhide"), checked=bool(box.d.get("no_quickhide")))
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            add(menu, 0x9006, B("open_folder"))
            add(menu, 0x9013, B("refresh"))
            add(menu, 0x9007, B("delete_box"))
            cmd = self._run_menu(menu, x, y)
        finally:
            for s in subs:
                user32.DestroyMenu(s)
            user32.DestroyMenu(menu)
        if cmd == 0x9001:
            self.paste_into(box)
        elif cmd == 0x9002:
            self.new_box_dialog(at=(x, y))
        elif cmd == 0x9003:
            self.new_folder_box(at=(x, y))
        elif cmd == 0x9004:
            self.rename_dialog(box)
        elif cmd == 0x9010:
            self.settings_target = box.id
            self.app._open_settings("boxes")
        elif cmd == 0x9005:
            box.d["rollup"] = not box.d.get("rollup")
            box.temp_open = False
        elif 0x9300 <= cmd < 0x9304:
            box.d["sort"] = ("name", "type", "date", "manual")[cmd - 0x9300]       # the custom order is kept
            box.sig = None
            box.refresh_items()
        elif cmd == 0x9011:
            box.d["locked"] = not box.d.get("locked")
        elif cmd == 0x9012:
            box.d["no_quickhide"] = not box.d.get("no_quickhide")
            if self.hidden and box.hwnd:
                user32.ShowWindow(box.hwnd, 4 if box.d["no_quickhide"] else 0)
        elif cmd == 0x9006:
            if box.folder:
                shell32.ShellExecuteW(None, "open", box.folder, None, None, 1)
        elif cmd == 0x9013:
            targets = {self.moved_target(p) for _n, p, _d, _m in box.items} - {None}    # e.g. a full Recycle Bin
            with self.icons.lock:
                for k in [k for k in self.icons.cache if os.path.dirname(k[0]) == box.folder or k[0] in targets]:
                    self.icons.cache.pop(k, None)
            box._layer = None
            box.sig = None
            box.refresh_items()
        elif cmd == 0x9007:
            self.delete_box_dialog(box)
            return
        if cmd:
            self.save_soon()
            if box.hwnd:
                box.update_roll()

    # ---- clipboard ----
    def _clipboard_files(self):
        if not user32.IsClipboardFormatAvailable(CF_HDROP) or not user32.OpenClipboard(self.owner):
            return [], False
        try:
            h = user32.GetClipboardData(CF_HDROP)
            files = hdrop_files(h) if h else []
            cut = False
            fmt = user32.RegisterClipboardFormatW("Preferred DropEffect")
            hd = user32.GetClipboardData(fmt)
            if hd:
                p = kernel32.GlobalLock(hd)
                if p:
                    cut = bool(ctypes.c_uint32.from_address(p).value & DROPEFFECT_MOVE)
                    kernel32.GlobalUnlock(hd)
            return files, cut
        finally:
            user32.CloseClipboard()

    def paste_into(self, box):
        files, cut = self._clipboard_files()
        if files:
            self.receive(box, files, FO_MOVE if cut else FO_COPY)

    # ---- creating / deleting / renaming ----
    def _place_new(self, at, w=None, h=400):
        w = w or int(self.opt("box_w"))
        if at:
            x, y = at
        else:
            m = monitors()[0][1]
            x, y = (m[0] + m[2]) // 2 - w // 2, (m[1] + m[3]) // 4
        for _ in range(30):                               # cascade past boxes already there
            if any(abs(b.rect()[0] - x) < 24 and abs(b.rect()[1] - y) < 24 for b in self.boxes):
                x, y = x + 36, y + 36
            else:
                break
        return [x, y, w, h]

    def add_box(self, name, folder=None, at=None, rect=None, **extra):
        own = folder is None
        if own:
            os.makedirs(self.box_root, exist_ok=True)
            kernel32.SetFileAttributesW(self.box_root, 0x2 | 0x4)              # hidden + system
            folder = unique_path(self.box_root, safe_name(name))
            os.makedirs(folder, exist_ok=True)
        d = {"id": "b%x" % int(time.time() * 1000000), "name": name, "dir": folder, "own": own,
             "rects": {}, "last": rect or self._place_new(at), "rollup": True, "sort": "name"}
        d.update(extra)
        d["rects"][self.sig] = list(d["last"])
        self.data["boxes"].append(d)
        box = Box(self, d)
        self.boxes.append(box)
        if self.hidden:
            self.set_hidden(False)
        self._ensure_windows()                              # finds the desktop and creates the window now
        self.save()
        self._apply_quickhide()
        return box

    def new_box_dialog(self, at=None):
        name = self.app._ask_string(B("new_box"), B("name_prompt"), B("box_n", len(self.boxes) + 1))
        if name:
            self.add_box(name.strip(), at=at)

    def new_folder_box(self, at=None):
        path = filedialog.askdirectory(parent=self.root, mustexist=True)
        if path:
            path = os.path.normpath(path)
            self.add_box(os.path.basename(path) or path, folder=path, at=at)

    def rename_box(self, box, new):
        new = new.strip()
        if not new or new == box.name:
            return
        box.d["name"] = new
        if box.d.get("own") and box.folder and os.path.isdir(box.folder):
            dst = unique_path(self.box_root, safe_name(new))
            try:
                os.rename(box.folder, dst)
                box.d["dir"] = dst
                box.sig = None
            except OSError as ex:
                self.log("box folder rename failed: %r" % ex)       # files open: keep the old folder name
        box.refresh_items()
        self.save()
        box.render()

    def delete_box_dialog(self, box):
        q = B("delete_q", box.name) if box.d.get("own") else B("delete_q_folder", box.name)
        if not self.app._ask_yesno(B("delete_box"), q):
            return
        if box.d.get("own") and box.folder and os.path.isdir(box.folder):
            for name in os.listdir(box.folder):
                try:
                    shutil.move(os.path.join(box.folder, name), unique_path(self.desktop, name))
                except OSError as ex:
                    self.log("return to desktop failed %s: %r" % (name, ex))
            try:
                os.rmdir(box.folder)
            except OSError:
                pass
        box.destroy()
        self.boxes.remove(box)
        self.data["boxes"].remove(box.d)
        self.data["box_styles"].pop(box.id, None)
        self.save()
        self._check_moved_icons()                           # This PC & co. went back to the desktop with the files
        self._apply_quickhide()

    # ---- double-click on the empty desktop: hide / show boxes and icons ----
    def _apply_quickhide(self):
        want = bool(self.opt("quickhide")) and bool(self.boxes)
        if want and self.dbl is None:
            self.dbl = DesktopDoubleClick(self.log)
            self.dbl.start()
        elif not want and self.dbl is not None:
            self.dbl.stop()
            self.dbl = None
            if self.hidden:
                self.set_hidden(False)

    def _desktop_dblclick(self, x, y):
        w = user32.WindowFromPoint(POINT(x, y))
        if not w or w in self.by_hwnd:
            return
        host, dv = desktop_host()
        if not host or (w != host and user32.GetAncestor(w, 2) != host):     # GA_ROOT: not the desktop
            return
        lv = user32.FindWindowExW(dv, None, "SysListView32", None) if dv else None
        if lv and user32.IsWindowVisible(lv) and listview_hit(lv, x, y) != -1:
            return                                                         # on an icon: Explorer opens it
        self.set_hidden(not self.hidden)

    def _show_icons(self, show):
        _host, dv = desktop_host()
        lv = user32.FindWindowExW(dv, None, "SysListView32", None) if dv else None
        if lv:
            user32.ShowWindow(lv, 5 if show else 0)

    def set_hidden(self, on):
        self.hidden = on
        for b in self.boxes:
            if b.hwnd and not b.d.get("no_quickhide"):
                user32.ShowWindow(b.hwnd, 0 if on else 4)
        self._show_icons(not on)
        self.data["icons_hidden"] = on
        self.save()

    # ---- iTop import ----
    @staticmethod
    def itop_dir():
        d = os.path.join(os.environ.get("LOCALAPPDATA", ""), "iTop Easy Desktop")
        return d if os.path.isfile(os.path.join(d, "BoxConfig.ini")) else None

    @staticmethod
    def _read_ini(path):
        raw = open(path, "rb").read()
        enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
        cp = configparser.ConfigParser(strict=False, interpolation=None)
        cp.optionxform = str
        cp.read_string(raw.decode(enc, "replace"))
        return cp

    def import_itop(self, src_dir):
        boxes = self._read_ini(os.path.join(src_dir, "BoxConfig.ini"))
        n = 0
        for sec in boxes.sections():
            if not sec.startswith("Box_"):
                continue
            s = boxes[sec]
            if s.get("IsHide", "0") == "1":
                continue
            name = (s.get("BoxName") or "").strip() or B("box_n", len(self.boxes) + 1)
            if any(b.name == name for b in self.boxes):
                n += 1
                continue
            try:
                rect = [int(s.get(k, d)) for k, d in (("Left", 200), ("Top", 120), ("Width", 320), ("Height", 420))]
            except ValueError:
                rect = None
            self.add_box(name, rect=rect, rollup=s.get("RollUpBox", "1") == "1")
            n += 1
        cfg_path = os.path.join(src_dir, "Config.ini")
        if os.path.isfile(cfg_path):
            act = self._read_ini(cfg_path)
            if act.has_section("Actions") and act["Actions"].get("Dclicking") in ("0", "1"):
                self.data["opts"]["quickhide"] = act["Actions"].get("Dclicking") == "1"
        self.save()
        self._apply_quickhide()
        return n

    # ---- settings tab ----
    def begin_edit(self):
        self.draft = json.loads(json.dumps({k: self.data.get(k, {}) for k in ("style", "box_styles", "opts")}))
        self._arrange_undo = None

    def discard(self):
        self.draft = None
        self._ui = None
        self.settings_target = None
        undo, self._arrange_undo = self._arrange_undo, None
        if undo:                                             # Cancel puts arranged boxes back too
            for b in self.boxes:
                if b.id in undo:
                    b.d["rects"], b.d["last"] = undo[b.id]
            self.save()
        self.redraw_all()

    def commit(self):
        d, self.draft = self.draft, None
        self._ui = None
        self.settings_target = None
        self._arrange_undo = None
        if d is None:
            return
        for k in ("style", "box_styles", "opts"):
            self.data[k] = d[k]
        self.save()
        self._apply_quickhide()
        self.redraw_all()

    def _set_style(self, key, value):
        tgt = self.settings_target
        if tgt:
            self.draft["box_styles"].setdefault(tgt, {})[key] = value
        else:
            self.draft["style"][key] = value
        self._changed()

    def _set_opt(self, key, value):
        self.draft["opts"][key] = value
        self._changed()

    def _changed(self):
        self.redraw_all()                                    # live: the real boxes show the draft
        self._refresh_preview()

    def build_into(self, parent, win, pad):
        main = __import__("__main__")
        C = lambda n, dflt: getattr(main, n, dflt)
        col = {"sf": C("COL_SURFACE", "#232427"), "tx": C("COL_TEXT", "#e6e8ea"), "dim": C("COL_TEXT_DIM", "#9aa0a6"),
               "ac": C("COL_ACCENT", "#4c8bf5"), "hv": C("COL_HOVER", "#303236"), "bd": C("COL_BORDER", "#2a2b2c"),
               "sf2": C("COL_SURFACE2", "#2d2e31"), "bg": C("COL_BG", "#1b1b1d")}
        fonts = {"f": C("FONT", ("Segoe UI", 10)), "sm": C("FONT_SM", ("Segoe UI", 9)),
                 "h": ("Segoe UI Semibold", 11)}
        if self.draft is None:
            self.begin_edit()
        if self.settings_target and not any(b.id == self.settings_target for b in self.boxes):
            self.settings_target = None
        cv = tk.Canvas(parent, bg=col["sf"], highlightthickness=0, bd=0)
        sb = self.app._dark_scrollbar(parent, cv)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        body = tk.Frame(cv, bg=col["sf"])
        wid = cv.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(wid, width=e.width))
        win.bind("<MouseWheel>", lambda e: cv.yview_scroll(int(-e.delta / 120) * 3, "units"))
        self._ui = {"body": body, "col": col, "fonts": fonts, "pad": pad, "win": win, "preview": None}
        self._build_body()

    def _build_body(self):
        ui = self._ui
        body, col, fonts, pad = ui["body"], ui["col"], ui["fonts"], ui["pad"]
        for w in body.winfo_children():
            w.destroy()
        sf, tx, dim, ac = col["sf"], col["tx"], col["dim"], col["ac"]
        st = self.style_of(self.settings_target)

        def header(text, top=16):
            tk.Label(body, text=text, bg=sf, fg=tx, font=fonts["h"]).pack(anchor="w", padx=pad, pady=(top, 6))

        header(B("appearance"))
        row = tk.Frame(body, bg=sf)
        row.pack(fill="x", padx=pad)
        pv = tk.Label(row, bg=col["bg"], bd=0)
        pv.pack(side="left", anchor="n")
        pv.bind("<Enter>", lambda e: self._refresh_preview(hover=True))
        pv.bind("<Leave>", lambda e: self._refresh_preview(hover=False))
        ui["preview"] = pv
        ctl = tk.Frame(row, bg=sf)
        ctl.pack(side="left", fill="x", expand=True, padx=(16, 0))

        # target: all boxes or one box
        trow = tk.Frame(ctl, bg=sf)
        trow.pack(fill="x")
        tk.Label(trow, text=B("for") + ":", bg=sf, fg=dim, font=fonts["sm"]).pack(side="left")
        names = [(None, B("all_boxes"))] + [(b.id, b.name) for b in self.boxes]
        cur = next((n for i, n in names if i == self.settings_target), B("all_boxes"))
        tl = tk.Label(trow, text=cur + "  ⌄", bg=sf, fg=ac, font=fonts["f"], cursor="hand2")
        tl.pack(side="left", padx=(6, 0))
        tl.bind("<Button-1>", lambda e: self._popup_list(tl, names, self._set_target))
        if self.settings_target and self.draft["box_styles"].get(self.settings_target):
            rs = tk.Label(trow, text=B("reset_box"), bg=sf, fg=dim, font=fonts["sm"], cursor="hand2")
            rs.pack(side="right")
            rs.bind("<Button-1>", lambda e: (self.draft["box_styles"].pop(self.settings_target, None),
                                             self._changed(), self._build_body()))

        tk.Label(ctl, text=B("bg_color"), bg=sf, fg=dim, font=fonts["sm"]).pack(anchor="w", pady=(12, 4))
        self._swatches(ctl, BG_COLORS, st.get("color"), lambda c: (self._set_style("color", c), self._build_body()),
                       none_rgb="#181b21")
        self._slider_row(ctl, "bg_transp", st["transp"], lambda v: self._set_style("transp", v))
        self._slider_row(ctl, "hover_transp", st["hover_transp"], lambda v: self._set_style("hover_transp", v))

        frow = tk.Frame(ctl, bg=sf)
        frow.pack(fill="x", pady=(12, 0))
        tk.Label(frow, text=B("title_font") + ":", bg=sf, fg=dim, font=fonts["sm"]).pack(side="left")
        ftxt = "%s, %d%s" % (st["font"], st["font_size"], (", " + B("bold").lower()) if st["bold"] else "")
        tk.Label(frow, text=ftxt, bg=sf, fg=tx, font=fonts["sm"]).pack(side="left", padx=(6, 0))
        ch = tk.Label(frow, text=B("change"), bg=sf, fg=ac, font=fonts["sm"], cursor="hand2")
        ch.pack(side="right")
        ch.bind("<Button-1>", lambda e: self._font_popup(ch))
        tk.Label(ctl, text=B("title_color"), bg=sf, fg=dim, font=fonts["sm"]).pack(anchor="w", pady=(10, 4))
        self._swatches(ctl, TITLE_COLORS, st.get("title_color"),
                       lambda c: (self._set_style("title_color", c or "#f4f6fa"), self._build_body()))

        # global look
        g = tk.Frame(body, bg=sf)
        g.pack(fill="x", pady=(14, 0))
        for key in ("round", "border", "badge"):
            self.app._setting_check(g, B(key), bool(self.opt(key)), lambda v, k=key: self._set_opt(k, bool(v)), pad)
        irow = tk.Frame(body, bg=sf)
        irow.pack(fill="x", padx=pad, pady=(8, 0))
        tk.Label(irow, text=B("icon_size") + ":", bg=sf, fg=tx, font=fonts["f"]).pack(side="left")
        for size, key in ((32, "icons_s"), (48, "icons_m"), (64, "icons_l")):
            on = int(self.opt("icon_size")) == size
            lb = tk.Label(irow, text=B(key), bg=ac if on else col["sf2"], fg="white" if on else tx,
                          font=fonts["sm"], padx=10, pady=4, cursor="hand2")
            lb.pack(side="left", padx=(8 if size == 32 else 2, 0))
            lb.bind("<Button-1>", lambda e, s=size: (self._set_opt("icon_size", s), self._build_body()))

        header(B("behavior"), top=20)
        b2 = tk.Frame(body, bg=sf)
        b2.pack(fill="x")
        self.app._setting_check(b2, B("snap"), bool(self.opt("snap")), lambda v: self._set_opt("snap", bool(v)), pad)
        tk.Label(b2, text=B("snap_note"), bg=sf, fg=dim, font=fonts["sm"], wraplength=520, justify="left").pack(
            anchor="w", padx=(pad + 32, pad), pady=(0, 4))
        self.app._setting_check(b2, B("quickhide"), bool(self.opt("quickhide")),
                                lambda v: self._set_opt("quickhide", bool(v)), pad)

        header(B("arrange"), top=20)
        grid = tk.Frame(body, bg=sf)
        grid.pack(fill="x", padx=pad)
        nums = tk.Frame(grid, bg=sf)
        nums.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        self._px_row(nums, 0, "box_w", 120, 1600, 900)
        self._px_row(nums, 1, "gap", 0, 300, 80)
        for r, (label, places) in enumerate((("arr_top", (("left", "top-left", 1), ("center", "top-center", 2),
                                                          ("right", "top-right", 3))),
                                             ("arr_bottom", (("left", "bottom-left", 1), ("center", "bottom-center", 2),
                                                             ("right", "bottom-right", 3))),
                                             ("arr_col", (("left", "col-left", 1), ("right", "col-right", 3)))), 1):
            tk.Label(grid, text=B(label) + ":", bg=sf, fg=tx, font=fonts["f"]).grid(
                row=r, column=0, sticky="w", padx=(0, 10), pady=(6, 0))
            for key, where, c in places:
                on = self.opt("layout") == where                 # the place the boxes keep now
                lb = tk.Label(grid, text=B(key), bg=ac if on else col["sf2"], fg="white" if on else tx,
                              font=fonts["sm"], padx=10, pady=4, cursor="hand2")
                lb.grid(row=r, column=c, sticky="we", padx=(0, 2), pady=(6, 0))
                lb.bind("<Button-1>", lambda e, w=where: (self.arrange(w), self._build_body()))
        tk.Label(body, text=B("arrange_note") + " " + B("up_note"), bg=sf, fg=dim, font=fonts["sm"], wraplength=520,
                 justify="left").pack(anchor="w", padx=pad, pady=(8, 0))

        btns = tk.Frame(body, bg=sf)
        btns.pack(fill="x", padx=pad, pady=(14, 4))
        self.app._accent_btn(btns, B("new_box"), lambda: (self.new_box_dialog(), self._build_body())).pack(side="left")
        src = self.itop_dir()
        if src:
            imp = tk.Label(body, text=B("import"), bg=sf, fg=ac, font=fonts["sm"], cursor="hand2")
            imp.pack(anchor="w", padx=pad, pady=(10, 0))
            imp.bind("<Button-1>", lambda e: self._import_dialog(src))
        where = tk.Frame(body, bg=sf)
        where.pack(fill="x", padx=pad, pady=(12, 16))
        tk.Label(where, text=B("where") + ":  " + self.box_root, bg=sf, fg=dim, font=fonts["sm"],
                 wraplength=500, justify="left").pack(side="left")
        op = tk.Label(where, text=B("open"), bg=sf, fg=ac, font=fonts["sm"], cursor="hand2")
        op.pack(side="left", padx=(8, 0))
        op.bind("<Button-1>", lambda e: os.path.isdir(self.box_root) and
                shell32.ShellExecuteW(None, "open", self.box_root, None, None, 1))
        self._refresh_preview()

    def _px_row(self, parent, row, key, lo, hi, smax, width=230):
        """Label, slider and number field for a pixel option, kept in step; the boxes on the
        desktop follow at once. The slider covers lo..smax, the field takes up to hi; the mouse
        wheel steps it (Shift: by 10)."""
        col, fonts = self._ui["col"], self._ui["fonts"]
        sf = col["sf"]
        tk.Label(parent, text=B(key) + ":", bg=sf, fg=col["tx"], font=fonts["f"]).grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=3)
        cv = tk.Canvas(parent, width=width, height=22, bg=sf, highlightthickness=0, bd=0, cursor="hand2")
        cv.grid(row=row, column=1, sticky="w")
        var = tk.StringVar(value=str(int(self.opt(key))))
        e = tk.Entry(parent, textvariable=var, width=5, justify="center", bg=col["sf2"], fg=col["tx"],
                     insertbackground=col["tx"], relief="flat", font=fonts["f"], highlightthickness=1,
                     highlightbackground=col["bd"], highlightcolor=col["ac"])
        e.grid(row=row, column=2, padx=(10, 0))
        tk.Label(parent, text="px", bg=sf, fg=col["dim"], font=fonts["sm"]).grid(row=row, column=3, padx=(4, 0))

        def draw(v):
            cv.delete("all")
            x = 8 + (width - 16) * (min(max(v, lo), smax) - lo) / float(smax - lo)
            cv.create_line(8, 11, width - 8, 11, fill=col["sf2"], width=4, capstyle="round")
            cv.create_line(8, 11, x, 11, fill=col["ac"], width=4, capstyle="round")
            cv.create_oval(x - 7, 4, x + 7, 18, fill="white", outline="")

        def apply(*_):
            try:
                v = int(var.get())
            except ValueError:
                return
            if lo <= v <= hi and self.draft is not None:
                draw(v)
                if self.draft["opts"].get(key) != v:
                    self.draft["opts"][key] = v
                    self._geometry_soon()

        def at(ev):
            var.set(str(int(round(lo + max(0.0, min(1.0, (ev.x - 8) / float(width - 16))) * (smax - lo)))))

        def wheel(ev):
            try:
                v = int(var.get())
            except ValueError:
                v = int(self.opt(key))
            var.set(str(max(lo, min(hi, v + (1 if ev.delta > 0 else -1) * (10 if ev.state & 1 else 1)))))
            return "break"

        var.trace_add("write", apply)
        cv.bind("<Button-1>", at)
        cv.bind("<B1-Motion>", at)
        for wdg in (cv, e):
            wdg.bind("<MouseWheel>", wheel)
        e.bind("<FocusOut>", lambda ev: var.set(str(int(self.opt(key)))))
        draw(int(self.opt(key)))

    def _set_target(self, box_id):
        self.settings_target = box_id
        self._build_body()

    def _slider_row(self, parent, key, value, on_change, width=250):
        col, fonts = self._ui["col"], self._ui["fonts"]
        lab = tk.Label(parent, text=B(key, value), bg=col["sf"], fg=col["dim"], font=fonts["sm"])
        lab.pack(anchor="w", pady=(10, 2))
        cv = tk.Canvas(parent, width=width, height=22, bg=col["sf"], highlightthickness=0, bd=0, cursor="hand2")
        cv.pack(anchor="w")

        def draw(v):
            cv.delete("all")
            x = 8 + (width - 16) * v / 90.0
            cv.create_line(8, 11, width - 8, 11, fill=col["sf2"], width=4, capstyle="round")
            cv.create_line(8, 11, x, 11, fill=col["ac"], width=4, capstyle="round")
            cv.create_oval(x - 7, 4, x + 7, 18, fill="white", outline="")

        def at(e):
            v = int(round(max(0.0, min(90.0, (e.x - 8) * 90.0 / (width - 16))) / 5.0) * 5)
            draw(v)
            lab.configure(text=B(key, v))
            on_change(v)
        cv.bind("<Button-1>", at)
        cv.bind("<B1-Motion>", at)
        draw(value)

    def _swatches(self, parent, colors, current, on_pick, none_rgb=None):
        col = self._ui["col"]
        row = tk.Frame(parent, bg=col["sf"])
        row.pack(anchor="w")
        cur = (current or "").lower() if current else None
        for c in colors + ["__custom__"]:
            cvs = tk.Canvas(row, width=20, height=20, bg=col["sf"], highlightthickness=0, bd=0, cursor="hand2")
            cvs.pack(side="left", padx=(0, 3))
            if c == "__custom__":
                cvs.create_oval(2, 2, 18, 18, outline=col["dim"], width=1)
                cvs.create_text(10, 10, text="+", fill=col["tx"], font=("Segoe UI", 9))
                cvs.bind("<Button-1>", lambda e, w=cvs: self.app._color_popup(w, current or "#4c8bf5", on_pick))
                continue
            fill = c or none_rgb or "#181b21"
            sel = (c or None) == (cur or None) or (c and cur and c.lower() == cur)
            cvs.create_rectangle(3, 3, 17, 17, fill=fill, outline=col["bd"])
            if sel:
                cvs.create_rectangle(1, 1, 19, 19, outline="white", width=2)
            cvs.bind("<Button-1>", lambda e, v=c: on_pick(v))
        if cur and cur not in [(c or "").lower() for c in colors]:      # a custom color in use
            cvs = tk.Canvas(row, width=20, height=20, bg=col["sf"], highlightthickness=0, bd=0)
            cvs.pack(side="left")
            cvs.create_rectangle(3, 3, 17, 17, fill=current, outline=col["bd"])
            cvs.create_rectangle(1, 1, 19, 19, outline="white", width=2)

    def _popup_list(self, anchor, items, pick, height=None):
        col, fonts = self._ui["col"], self._ui["fonts"]
        pop = tk.Toplevel(anchor)
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        pop.configure(bg=col["bd"])
        fr = tk.Frame(pop, bg=col["sf"])
        fr.pack(padx=1, pady=1)
        if height and len(items) > height:
            lb = tk.Listbox(fr, height=height, bg=col["sf"], fg=col["tx"], selectbackground=col["ac"],
                            highlightthickness=0, bd=0, font=fonts["f"], activestyle="none", width=28)
            sb = tk.Scrollbar(fr, command=lb.yview)
            lb.configure(yscrollcommand=sb.set)
            lb.pack(side="left")
            sb.pack(side="left", fill="y")
            for _v, text in items:
                lb.insert("end", text)
            lb.bind("<<ListboxSelect>>", lambda e: lb.curselection() and
                    (pop.destroy(), pick(items[lb.curselection()[0]][0])))
        else:
            for v, text in items:
                r = tk.Label(fr, text=text, bg=col["sf"], fg=col["tx"], font=fonts["f"], anchor="w", padx=14, pady=5,
                             cursor="hand2")
                r.pack(fill="x")
                r.bind("<Enter>", lambda e, r=r: r.configure(bg=col["hv"]))
                r.bind("<Leave>", lambda e, r=r: r.configure(bg=col["sf"]))
                r.bind("<Button-1>", lambda e, v=v: (pop.destroy(), pick(v)))
        anchor.update_idletasks()
        pop.update_idletasks()
        pop.geometry("+%d+%d" % (anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height() + 2))
        pop.focus_force()
        pop.after(200, lambda: pop.winfo_exists() and pop.bind("<FocusOut>", lambda e: pop.destroy()))
        pop.bind("<Escape>", lambda e: pop.destroy())

    def _font_popup(self, anchor):
        col, fonts = self._ui["col"], self._ui["fonts"]
        st = self.style_of(self.settings_target)
        pop = tk.Toplevel(self._ui["win"])
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        pop.configure(bg=col["bd"])
        fr = tk.Frame(pop, bg=col["sf"])
        fr.pack(padx=1, pady=1)
        fams = sorted(font_files(), key=str.lower)
        lb = tk.Listbox(fr, height=12, bg=col["sf"], fg=col["tx"], selectbackground=col["ac"], highlightthickness=0,
                        bd=0, font=fonts["f"], activestyle="none", width=30, exportselection=False)
        sb = tk.Scrollbar(fr, command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        top = tk.Frame(fr, bg=col["sf"])
        top.pack(side="top", fill="x", padx=10, pady=(8, 6))
        lb.pack(side="left", padx=(10, 0), pady=(0, 10))
        sb.pack(side="left", fill="y", pady=(0, 10), padx=(0, 10))
        for f in fams:
            lb.insert("end", f)
        if st["font"] in fams:
            i = fams.index(st["font"])
            lb.selection_set(i)
            lb.see(i)

        def pick_font(_e=None):
            if lb.curselection():
                self._set_style("font", fams[lb.curselection()[0]])
                self._refresh_font_label()
        lb.bind("<<ListboxSelect>>", pick_font)
        tk.Label(top, text=B("size") + ":", bg=col["sf"], fg=col["dim"], font=fonts["sm"]).pack(side="left")
        size_lbl = tk.Label(top, text=str(st["font_size"]), bg=col["sf"], fg=col["tx"], font=fonts["f"], width=3)

        def step(dv):
            v = max(8, min(24, int(self.style_of(self.settings_target)["font_size"]) + dv))
            size_lbl.configure(text=str(v))
            self._set_style("font_size", v)
            self._refresh_font_label()
        for txt, dv in (("−", -1), (None, 0), ("+", 1)):
            if txt is None:
                size_lbl.pack(side="left")
                continue
            bt = tk.Label(top, text=txt, bg=col["sf2"], fg=col["tx"], font=fonts["f"], width=2, cursor="hand2")
            bt.pack(side="left", padx=2)
            bt.bind("<Button-1>", lambda e, d=dv: step(d))
        bold = {"v": bool(st["bold"])}
        bl = tk.Label(top, text=B("bold"), bg=col["ac"] if bold["v"] else col["sf2"], fg="white" if bold["v"] else col["tx"],
                      font=fonts["sm"], padx=10, pady=2, cursor="hand2")
        bl.pack(side="right")

        def toggle_bold(_e=None):
            bold["v"] = not bold["v"]
            bl.configure(bg=col["ac"] if bold["v"] else col["sf2"], fg="white" if bold["v"] else col["tx"])
            self._set_style("bold", bold["v"])
            self._refresh_font_label()
        bl.bind("<Button-1>", toggle_bold)
        anchor.update_idletasks()
        pop.update_idletasks()
        x = max(0, anchor.winfo_rootx() + anchor.winfo_width() - pop.winfo_reqwidth())
        pop.geometry("+%d+%d" % (x, anchor.winfo_rooty() + anchor.winfo_height() + 4))
        pop.focus_force()

        def maybe_close(_e):
            def check():
                if not pop.winfo_exists():
                    return
                w = pop.focus_get()
                if w is None or not str(w).startswith(str(pop)):
                    pop.destroy()
            pop.after(80, check)
        pop.after(250, lambda: pop.winfo_exists() and pop.bind("<FocusOut>", maybe_close))
        pop.bind("<Escape>", lambda e: pop.destroy())

    def _refresh_font_label(self):
        if self._ui:
            self.root.after(10, self._build_body_keep_popups)

    def _build_body_keep_popups(self):
        """Rebuild the tab under an open font popup (the popup belongs to the window, not the body)."""
        if self._ui and self._ui["body"].winfo_exists():
            self._build_body()

    def _preview_box(self):
        box = next((b for b in self.boxes if b.id == self.settings_target), None)
        d = {"id": self.settings_target, "name": box.name if box else B("box_n", 1), "dir": box.folder if box else ""}
        pb = Box(self, d)
        if box and box.items:
            pb.items = box.items[:6]
        else:
            win = os.environ.get("WINDIR", r"C:\Windows")
            samples = [(os.path.basename(win), win, True), ("explorer.exe", os.path.join(win, "explorer.exe"), False),
                       ("notepad.exe", os.path.join(win, "notepad.exe"), False),
                       ("regedit.exe", os.path.join(win, "regedit.exe"), False)]
            pb.items = [(n, p, isdir, 0) for n, p, isdir in samples if os.path.exists(p)]
        return pb

    def _refresh_preview(self, hover=None):
        ui = self._ui
        if not ui or not ui.get("preview") or not ui["preview"].winfo_exists():
            return
        W, H = 240, 230
        bg = ui.get("wall")
        if bg is None:
            bg = wallpaper_image(W, H) or Image.new("RGBA", (W, H), (40, 64, 104, 255))
            ui["wall"] = bg
        if hover is not None:
            ui["hover"] = hover
        pb = self._preview_box()
        pb.hovering = bool(ui.get("hover"))
        bw = min(W - 30, max(pb.px(PAD) * 2 + 2 * (pb.icon_px() + pb.px(40)), 170))
        img = bg.copy()
        roll = pb.compose(bw, pb.title_h())                   # a rolled-up box above an open one
        img.alpha_composite(roll, ((W - bw) // 2, 12))
        open_h = min(H - pb.title_h() - 34, pb.title_h() + pb.content_h(bw))
        img.alpha_composite(pb.compose(bw, open_h), ((W - bw) // 2, 12 + pb.title_h() + 10))
        from PIL import ImageTk
        ph = ImageTk.PhotoImage(img)
        ui["preview"].configure(image=ph)
        ui["preview"].image = ph

    def _import_dialog(self, src):
        try:
            import subprocess
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq iEasyDesk.exe", "/NH"], capture_output=True,
                                 text=True, creationflags=0x08000000, timeout=10).stdout
            if "ieasydesk.exe" in out.lower():
                self.app._info(B("tab"), B("itop_running"))
        except Exception:
            pass
        try:
            n = self.import_itop(src)
        except Exception as ex:
            self.log("itop import failed: %r" % ex)
            n = 0
        self.app._info(B("tab"), B("imported_n", n))
        if self._ui:
            self._build_body()

    # ---- shutdown ----
    def shutdown(self):
        if self.dbl is not None:
            self.dbl.stop()
        if self.hidden:
            self._show_icons(True)
            self.data["icons_hidden"] = False
        self.save()
        for b in self.boxes:
            b.destroy()
