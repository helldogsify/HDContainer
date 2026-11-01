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
import json
import time
import queue
import shutil
import ctypes
import threading
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
    "color": {"en": "Color", "ru": "Цвет", "es": "Color", "pt": "Cor", "de": "Farbe", "fr": "Couleur", "zh": "颜色"},
    "no_color": {"en": "No color", "ru": "Без цвета", "es": "Sin color", "pt": "Sem cor", "de": "Keine Farbe", "fr": "Sans couleur", "zh": "无颜色"},
    "c_blue": {"en": "Blue", "ru": "Синий", "es": "Azul", "pt": "Azul", "de": "Blau", "fr": "Bleu", "zh": "蓝色"},
    "c_green": {"en": "Green", "ru": "Зелёный", "es": "Verde", "pt": "Verde", "de": "Grün", "fr": "Vert", "zh": "绿色"},
    "c_orange": {"en": "Orange", "ru": "Оранжевый", "es": "Naranja", "pt": "Laranja", "de": "Orange", "fr": "Orange", "zh": "橙色"},
    "c_red": {"en": "Red", "ru": "Красный", "es": "Rojo", "pt": "Vermelho", "de": "Rot", "fr": "Rouge", "zh": "红色"},
    "c_purple": {"en": "Purple", "ru": "Фиолетовый", "es": "Morado", "pt": "Roxo", "de": "Lila", "fr": "Violet", "zh": "紫色"},
    "c_yellow": {"en": "Yellow", "ru": "Жёлтый", "es": "Amarillo", "pt": "Amarelo", "de": "Gelb", "fr": "Jaune", "zh": "黄色"},
    "c_cyan": {"en": "Cyan", "ru": "Голубой", "es": "Celeste", "pt": "Ciano", "de": "Cyan", "fr": "Cyan", "zh": "青色"},
    "c_pink": {"en": "Pink", "ru": "Розовый", "es": "Rosa", "pt": "Rosa", "de": "Rosa", "fr": "Rose", "zh": "粉色"},
    "transparency": {"en": "Transparency", "ru": "Прозрачность", "es": "Transparencia", "pt": "Transparência", "de": "Transparenz", "fr": "Transparence", "zh": "透明度"},
    "opaque": {"en": "Opaque", "ru": "Непрозрачный", "es": "Opaca", "pt": "Opaca", "de": "Deckend", "fr": "Opaque", "zh": "不透明"},
    "rollup": {"en": "Roll up into the title", "ru": "Сворачивать в заголовок", "es": "Plegar en el título", "pt": "Recolher no título", "de": "In die Titelleiste einklappen", "fr": "Replier dans le titre", "zh": "折叠为标题栏"},
    "sort": {"en": "Sort", "ru": "Сортировка", "es": "Ordenar", "pt": "Ordenar", "de": "Sortieren", "fr": "Trier", "zh": "排序"},
    "sort_name": {"en": "By name", "ru": "По имени", "es": "Por nombre", "pt": "Por nome", "de": "Nach Name", "fr": "Par nom", "zh": "按名称"},
    "sort_type": {"en": "By type", "ru": "По типу", "es": "Por tipo", "pt": "Por tipo", "de": "Nach Typ", "fr": "Par type", "zh": "按类型"},
    "sort_date": {"en": "By date", "ru": "По дате", "es": "Por fecha", "pt": "Por data", "de": "Nach Datum", "fr": "Par date", "zh": "按日期"},
    "paste": {"en": "Paste", "ru": "Вставить", "es": "Pegar", "pt": "Colar", "de": "Einfügen", "fr": "Coller", "zh": "粘贴"},
    "open_folder": {"en": "Open box folder", "ru": "Открыть папку ящика", "es": "Abrir la carpeta de la caja", "pt": "Abrir a pasta da caixa", "de": "Box-Ordner öffnen", "fr": "Ouvrir le dossier de la boîte", "zh": "打开收纳盒文件夹"},
    "delete_box": {"en": "Delete box", "ru": "Удалить ящик", "es": "Eliminar caja", "pt": "Excluir caixa", "de": "Box löschen", "fr": "Supprimer la boîte", "zh": "删除收纳盒"},
    "delete_q": {"en": "Delete box “%s”? Its files go back to the desktop.", "ru": "Удалить ящик «%s»? Файлы из него вернутся на рабочий стол.", "es": "¿Eliminar la caja «%s»? Sus archivos vuelven al escritorio.", "pt": "Excluir a caixa “%s”? Os arquivos voltam para a área de trabalho.", "de": "Box „%s“ löschen? Ihre Dateien kommen zurück auf den Desktop.", "fr": "Supprimer la boîte « %s » ? Ses fichiers reviennent sur le bureau.", "zh": "删除收纳盒“%s”？其中的文件会回到桌面。"},
    "delete_q_folder": {"en": "Remove box “%s”? The folder itself stays as it is.", "ru": "Убрать ящик «%s»? Сама папка останется как есть.", "es": "¿Quitar la caja «%s»? La carpeta se queda como está.", "pt": "Remover a caixa “%s”? A pasta continua como está.", "de": "Box „%s“ entfernen? Der Ordner selbst bleibt unverändert.", "fr": "Retirer la boîte « %s » ? Le dossier reste tel quel.", "zh": "移除收纳盒“%s”？文件夹本身保持不变。"},
    "to_desktop": {"en": "Move to the desktop", "ru": "Вынести на рабочий стол", "es": "Mover al escritorio", "pt": "Mover para a área de trabalho", "de": "Auf den Desktop verschieben", "fr": "Déplacer sur le bureau", "zh": "移到桌面"},
    "empty": {"en": "Drop files here", "ru": "Перетащите сюда файлы", "es": "Suelta archivos aquí", "pt": "Solte arquivos aqui", "de": "Dateien hierher ziehen", "fr": "Déposez des fichiers ici", "zh": "将文件拖到这里"},
    "missing": {"en": "Folder not found", "ru": "Папка не найдена", "es": "Carpeta no encontrada", "pt": "Pasta não encontrada", "de": "Ordner nicht gefunden", "fr": "Dossier introuvable", "zh": "找不到文件夹"},
    "quickhide": {"en": "Hide boxes on double-click on the desktop", "ru": "Прятать ящики двойным кликом по рабочему столу", "es": "Ocultar las cajas con doble clic en el escritorio", "pt": "Ocultar as caixas com clique duplo na área de trabalho", "de": "Boxen per Doppelklick auf den Desktop ausblenden", "fr": "Masquer les boîtes d’un double-clic sur le bureau", "zh": "双击桌面隐藏收纳盒"},
    "rules_title": {"en": "Sort new desktop files into boxes", "ru": "Раскладывать новые файлы с рабочего стола", "es": "Ordenar los archivos nuevos del escritorio", "pt": "Organizar os novos arquivos da área de trabalho", "de": "Neue Desktop-Dateien einsortieren", "fr": "Ranger les nouveaux fichiers du bureau", "zh": "将桌面新文件归入收纳盒"},
    "keep": {"en": "Leave on the desktop", "ru": "Не трогать", "es": "Dejar en el escritorio", "pt": "Deixar na área de trabalho", "de": "Auf dem Desktop lassen", "fr": "Laisser sur le bureau", "zh": "留在桌面"},
    "cat_folders": {"en": "Folders", "ru": "Папки", "es": "Carpetas", "pt": "Pastas", "de": "Ordner", "fr": "Dossiers", "zh": "文件夹"},
    "cat_apps": {"en": "Shortcuts and apps", "ru": "Ярлыки и приложения", "es": "Accesos directos y apps", "pt": "Atalhos e apps", "de": "Verknüpfungen und Apps", "fr": "Raccourcis et applis", "zh": "快捷方式和应用"},
    "cat_documents": {"en": "Documents", "ru": "Документы", "es": "Documentos", "pt": "Documentos", "de": "Dokumente", "fr": "Documents", "zh": "文档"},
    "cat_images": {"en": "Images", "ru": "Картинки", "es": "Imágenes", "pt": "Imagens", "de": "Bilder", "fr": "Images", "zh": "图片"},
    "cat_videos": {"en": "Videos", "ru": "Видео", "es": "Vídeos", "pt": "Vídeos", "de": "Videos", "fr": "Vidéos", "zh": "视频"},
    "cat_music": {"en": "Music", "ru": "Музыка", "es": "Música", "pt": "Música", "de": "Musik", "fr": "Musique", "zh": "音乐"},
    "cat_archives": {"en": "Archives", "ru": "Архивы", "es": "Archivos comprimidos", "pt": "Arquivos compactados", "de": "Archive", "fr": "Archives", "zh": "压缩包"},
    "cat_programs": {"en": "Installers and scripts", "ru": "Установщики и скрипты", "es": "Instaladores y scripts", "pt": "Instaladores e scripts", "de": "Installer und Skripte", "fr": "Installateurs et scripts", "zh": "安装程序和脚本"},
    "cat_links": {"en": "Web links", "ru": "Веб-ссылки", "es": "Enlaces web", "pt": "Links da web", "de": "Weblinks", "fr": "Liens web", "zh": "网页链接"},
    "cat_others": {"en": "Other files", "ru": "Остальное", "es": "Otros archivos", "pt": "Outros arquivos", "de": "Andere Dateien", "fr": "Autres fichiers", "zh": "其他文件"},
    "sort_now": {"en": "Sort the desktop now", "ru": "Разложить рабочий стол сейчас", "es": "Ordenar el escritorio ahora", "pt": "Organizar a área de trabalho agora", "de": "Desktop jetzt einsortieren", "fr": "Ranger le bureau maintenant", "zh": "立即整理桌面"},
    "sorted_n": {"en": "Files sorted into boxes: %d", "ru": "Разложено по ящикам: %d", "es": "Archivos ordenados: %d", "pt": "Arquivos organizados: %d", "de": "Einsortierte Dateien: %d", "fr": "Fichiers rangés : %d", "zh": "已归入收纳盒的文件：%d"},
    "import": {"en": "Import boxes from iTop Easy Desktop", "ru": "Перенести ящики из iTop Easy Desktop", "es": "Importar cajas de iTop Easy Desktop", "pt": "Importar caixas do iTop Easy Desktop", "de": "Boxen aus iTop Easy Desktop übernehmen", "fr": "Importer les boîtes d’iTop Easy Desktop", "zh": "从 iTop Easy Desktop 导入收纳盒"},
    "import_done": {"en": "Imported boxes: %d. Sort the files on your desktop into them by the same rules now?", "ru": "Перенесено ящиков: %d. Разложить файлы с рабочего стола по ним по тем же правилам?", "es": "Cajas importadas: %d. ¿Ordenar ahora los archivos del escritorio con las mismas reglas?", "pt": "Caixas importadas: %d. Organizar agora os arquivos da área de trabalho com as mesmas regras?", "de": "Übernommene Boxen: %d. Desktop-Dateien jetzt nach denselben Regeln einsortieren?", "fr": "Boîtes importées : %d. Ranger maintenant les fichiers du bureau selon les mêmes règles ?", "zh": "已导入收纳盒：%d。现在按相同规则整理桌面文件吗？"},
    "itop_running": {"en": "Quit iTop Easy Desktop first, otherwise both programs will draw boxes.", "ru": "Сначала закройте iTop Easy Desktop, иначе ящики будут рисовать обе программы.", "es": "Cierra primero iTop Easy Desktop; si no, ambos programas dibujarán cajas.", "pt": "Feche o iTop Easy Desktop primeiro, senão os dois programas vão desenhar caixas.", "de": "Beende zuerst iTop Easy Desktop, sonst zeichnen beide Programme Boxen.", "fr": "Quittez d’abord iTop Easy Desktop, sinon les deux programmes dessineront des boîtes.", "zh": "请先退出 iTop Easy Desktop，否则两个程序都会绘制收纳盒。"},
    "where": {"en": "Box files are kept in", "ru": "Файлы ящиков лежат в", "es": "Los archivos de las cajas están en", "pt": "Os arquivos das caixas ficam em", "de": "Die Dateien der Boxen liegen in", "fr": "Les fichiers des boîtes sont dans", "zh": "收纳盒文件保存在"},
    "open": {"en": "Open", "ru": "Открыть", "es": "Abrir", "pt": "Abrir", "de": "Öffnen", "fr": "Ouvrir", "zh": "打开"},
}

LANG_GET = lambda: "en"


def B(key, *args):
    s = BSTR.get(key, {})
    txt = s.get(LANG_GET()) or s.get("en") or key
    return (txt % args) if args else txt


CATS = ["folders", "apps", "documents", "images", "videos", "music", "archives", "programs", "links", "others"]
_EXT = {
    "apps": {".lnk", ".appref-ms", ".msc"},
    "documents": set(".doc .docx .docm .pdf .txt .rtf .odt .ods .odp .xls .xlsx .xlsm .ppt .pptx .csv .md "
                     ".htm .html .xml .xps .mht .mhtml .epub .djvu .fb2 .wps".split()),
    "images": set(".jpg .jpeg .png .webp .svg .bmp .gif .tif .tiff .heic .heif .avif .ico .psd .raw .cr2 "
                  ".nef .arw .dng .jfif .eps .ai".split()),
    "videos": set(".mp4 .mkv .avi .mov .wmv .webm .flv .m4v .mpg .mpeg .3gp .ts .mts .m2ts .vob .ogv".split()),
    "music": set(".mp3 .wav .flac .ogg .aac .m4a .wma .opus .aiff .alac .amr .ape .mid".split()),
    "archives": set(".zip .rar .7z .tar .gz .tgz .bz2 .xz .iso .cab .lzh .arj .img .dmg .vhd .vhdx".split()),
    "programs": set(".exe .msi .msix .bat .cmd .ps1 .com .apk .vbs".split()),
    "links": {".url", ".website"},
}
GAME_URLS = ("steam:", "battlenet:", "origin2:", "uplay:", "roblox:", "com.epicgames.launcher:")
TEMP_EXT = (".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload")

PALETTE = [("c_blue", "#4c8bf5"), ("c_green", "#34c759"), ("c_orange", "#ff9f0a"), ("c_red", "#ff453a"),
           ("c_purple", "#bf5af2"), ("c_yellow", "#ffd60a"), ("c_cyan", "#64d2ff"), ("c_pink", "#ff375f")]
ALPHAS = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4]
ROOT_NAME = "HDContainer Boxes"

# logical sizes (scaled by the window's DPI)
TITLE_H, PAD, CELL_W, CELL_H, ICON, RADIUS, EDGE = 34, 8, 88, 88, 48, 10, 6
HOVER_OPEN_MS, LEAVE_CLOSE_MS, ANIM_MS = 220, 450, 130
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
_d(shell32.ShellExecuteW, VP, [HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int])
_d(shell32.ILFindLastID, VP, [VP])
_d(shell32.ILFree, None, [VP])
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
        dv = user32.FindWindowExW(h, None, "SHELLDLL_DefView", None)
        if dv:
            found.append((h, dv))
            return False
        return True
    user32.EnumWindows(ENUMPROC(cb), 0)
    return found[0] if found else (None, None)


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
    if shell32.SHCreateItemFromParsingName(path, None, ctypes.byref(IID_IShellItemImageFactory),
                                           ctypes.byref(fac)) != 0 or not fac:
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
    """Pictures made on one background STA thread; the UI only reads the cache."""

    def __init__(self):
        self.cache, self.pending = {}, set()
        self.lock = threading.Lock()
        self.q = queue.Queue()
        self.dirty = False
        threading.Thread(target=self._run, daemon=True, name="hdc-box-icons").start()

    def get(self, path, size, mtime):
        key = (path, size, mtime)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            if key not in self.pending:
                self.pending.add(key)
                self.q.put(key)
        return None

    def _run(self):
        ole32.CoInitializeEx(None, 2)
        while True:
            key = self.q.get()
            try:
                img = shell_image(key[0], key[1])
            except Exception:
                img = None
            with self.lock:
                self.cache[key] = img or False          # False: no picture, draw a placeholder
                self.pending.discard(key)
            self.dirty = True


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------
def category(path, is_dir):
    if is_dir:
        return "folders"
    ext = os.path.splitext(path)[1].lower()
    if ext == ".url":
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip().lower().startswith("url="):
                        url = line.strip()[4:].lower()
                        return "apps" if url.startswith(GAME_URLS) else "links"
        except OSError:
            pass
        return "links"
    for cat, exts in _EXT.items():
        if ext in exts:
            return cat
    return "others"


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
        self.files = []
        enter = ctypes.WINFUNCTYPE(HRESULT, VP, VP, DWORD, ctypes.c_uint64, ctypes.POINTER(DWORD))(self._enter)
        over = ctypes.WINFUNCTYPE(HRESULT, VP, DWORD, ctypes.c_uint64, ctypes.POINTER(DWORD))(self._over)
        leave = ctypes.WINFUNCTYPE(HRESULT, VP)(self._leave)
        drop = ctypes.WINFUNCTYPE(HRESULT, VP, VP, DWORD, ctypes.c_uint64, ctypes.POINTER(DWORD))(self._drop)
        super().__init__(IID_IDropTarget, [enter, over, leave, drop])

    def _effect(self, keys):
        folder = self.box.folder
        if not self.files or not folder or not os.path.isdir(folder):
            return DROPEFFECT_NONE
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
        if eff and allowed and not eff & allowed:
            eff = DROPEFFECT_COPY if allowed & DROPEFFECT_COPY else (allowed & DROPEFFECT_MOVE)
        return eff

    def _enter(self, this, pdo, keys, pt, peff):
        try:
            self.files = dataobject_files(pdo)
        except Exception:
            self.files = []
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
        eff = self._negotiate(keys, peff[0])
        files = self.files or dataobject_files(pdo)
        self.box.drag_hover(False)
        if eff and files:
            # we move/copy ourselves and report COPY: a source told MOVE would delete
            # the originals itself, racing with our own operation
            self.box.mgr.receive(self.box, files, FO_COPY if eff == DROPEFFECT_COPY else FO_MOVE)
            peff[0] = DROPEFFECT_COPY
        else:
            peff[0] = DROPEFFECT_NONE
        self.files = []
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
def _mix(rgb, other, f):
    return tuple(int(a + (b - a) * f) for a, b in zip(rgb, other))


def _hex(c):
    c = (c or "").lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


class Box:
    def __init__(self, mgr, d):
        self.mgr, self.d = mgr, d
        self.hwnd = None
        self.target = None              # DropTarget (registered with OLE)
        self.items = []                 # [(name, path, is_dir, mtime)]
        self.sig = None                 # listing signature: re-render only on change
        self.scroll = 0
        self.hover = -1
        self.sel = set()
        self.cur_h = None               # current height (animation)
        self.anim = None                # (from_h, to_h, t0)
        self.temp_open = False          # opened by hover, closes when the mouse leaves
        self.drop_hl = False
        self.tracking = False
        self.press = None               # dict while a mouse button is held
        self.hidden = False
        self._dc = None

    # ---- data ----
    @property
    def folder(self):
        return self.d.get("dir")

    @property
    def name(self):
        return self.d.get("name") or "Box"

    def scale(self):
        try:
            dpi = user32.GetDpiForWindow(self.hwnd) if self.hwnd else 96
        except Exception:
            dpi = 96
        return (dpi or 96) / 96.0

    def px(self, v):
        return int(round(v * self.scale()))

    def rect(self):
        """[x, y, w, h] in screen pixels for the current monitor layout."""
        r = self.d.get("rects", {}).get(self.mgr.sig) or self.d.get("last") or [200, 120, 320, 420]
        x, y, w, h = r
        wa = work_area_at(x + 10, y + 10)
        w, h = max(self.px(CELL_W * 2 + PAD * 2), w), max(self.px(TITLE_H) + 10, h)
        x = min(max(x, wa[0] - w + 40), wa[2] - 40)
        y = min(max(y, wa[1]), wa[3] - self.px(TITLE_H))
        return [x, y, w, h]

    def set_rect(self, r):
        self.d.setdefault("rects", {})[self.mgr.sig] = list(r)
        self.d["last"] = list(r)
        self.mgr.save_soon()

    def rolled(self):
        if self.press and self.press.get("mode") == "resize":
            return False
        if self.d.get("rollup"):
            return not (self.temp_open or self.d.get("pinned"))
        return bool(self.d.get("collapsed"))

    def target_h(self):
        return self.px(TITLE_H) if self.rolled() else self.rect()[3]

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
            if sort == "date":
                out.sort(key=lambda t: -t[3])
            elif sort == "type":
                out.sort(key=lambda t: (not t[2], os.path.splitext(t[0])[1].lower(), t[0].lower()))
            else:
                out.sort(key=lambda t: (not t[2], t[0].lower()))
        sig = None if out is None else tuple((n, m) for n, _p, _d, m in out)
        if sig != self.sig:
            self.items = out or []
            self.sig = sig
            self.sel = {i for i in self.sel if i < len(self.items)}
            if out is not None and "seen" not in self.d:
                self.d["seen"] = [n for n, *_ in out]
            return True
        return False

    def badge(self):
        seen = set(self.d.get("seen") or [])
        return sum(1 for n, *_ in self.items if n not in seen)

    def mark_seen(self):
        names = [n for n, *_ in self.items]
        if set(names) != set(self.d.get("seen") or []):
            self.d["seen"] = names
            self.mgr.save_soon()

    def label(self, name, is_dir):
        stem, ext = os.path.splitext(name)
        return stem if (not is_dir and ext.lower() in (".lnk", ".url", ".appref-ms", ".website")) else name

    # ---- window ----
    def create(self, host):
        x, y, w, h = self.rect()
        cx, cy = self.mgr.to_client(x, y)
        self.cur_h = self.px(TITLE_H) if self.rolled() else h
        self.hwnd = user32.CreateWindowExW(WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, self.mgr.CLASS,
                                           self.name, WS_CHILD | WS_CLIPSIBLINGS, cx, cy, w, self.cur_h,
                                           host, None, _HINST, None)
        if not self.hwnd:
            self.mgr.log("box window failed: %d" % ctypes.get_last_error())
            return False
        self.mgr.by_hwnd[self.hwnd] = self
        self.cur_h = self.target_h()
        self.refresh_items()
        self.render()
        user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)  # HWND_TOP
        if not self.mgr.hidden:
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

    def place(self, h=None):
        x, y, w, hh = self.rect()
        cx, cy = self.mgr.to_client(x, y)
        user32.SetWindowPos(self.hwnd, None, cx, cy, w, h if h is not None else self.cur_h,
                            SWP_NOZORDER | SWP_NOACTIVATE)

    # ---- drawing ----
    def colors(self):
        c = _hex(self.d.get("color"))
        base = (20, 23, 28)
        if c:
            return _mix(c, base, 0.72), _mix(c, base, 0.35)
        return (26, 29, 35), (40, 46, 58)

    def layout(self, w):
        """(x0, cols, cell, cell_h, pad): fixed-width cells, the whole grid centered,
        so the gaps between icons equal the margins and columns line up across rows."""
        pad, cell = self.px(PAD), self.px(CELL_W)
        cols = max(1, (w - 2 * pad) // cell)
        return (w - cols * cell) // 2, cols, cell, self.px(CELL_H), pad

    def content_h(self, w):
        _x0, cols, _cell, ch, pad = self.layout(w)
        rows = (len(self.items) + cols - 1) // cols
        return pad * 2 + rows * ch

    def render(self):
        if not self.hwnd:
            return
        x, y, w, full_h = self.rect()
        h = max(1, self.cur_h or full_h)
        t = self.px(TITLE_H)
        body, title = self.colors()
        alpha = max(0.25, min(1.0, float(self.d.get("alpha", 0.9))))
        a = int(255 * alpha)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        r = self.px(RADIUS)
        rolled_now = h <= t + 1
        if rolled_now:
            dr.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=title + (min(255, a + 30),))
        else:
            dr.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=body + (a,))
            dr.rounded_rectangle((0, 0, w - 1, t), radius=r, fill=title + (min(255, a + 30),),
                                 corners=(True, True, False, False))
        # title: name + badge, centered
        f = _font("seguisb.ttf", self.px(14))
        name = self.name
        badge = self.badge() if self.items else 0
        bw = self.px(18) if badge else 0
        maxw = w - self.px(24) - (bw + self.px(6) if badge else 0)
        while name and dr.textlength(name, font=f) > maxw:
            name = name[:-2] + "…" if len(name) > 2 else ""
        tw = dr.textlength(name, font=f)
        x0 = (w - tw - (bw + self.px(6) if badge else 0)) / 2.0
        asc, desc = f.getmetrics()
        ty = (min(h, t) - (asc + desc)) / 2.0
        dr.text((x0, ty), name, font=f, fill=(244, 246, 250, 255))
        if badge:
            bx, by = x0 + tw + self.px(6), (min(h, t) - bw) / 2.0
            txt = str(badge) if badge < 100 else "99+"
            if len(txt) > 1:
                bw2 = self.px(10) + int(dr.textlength(txt, font=_font("seguisb.ttf", self.px(10))))
            else:
                bw2 = bw
            dr.rounded_rectangle((bx, by, bx + bw2, by + bw), radius=bw // 2, fill=(235, 64, 52, 255))
            fb = _font("seguisb.ttf", self.px(10))
            tl = dr.textlength(txt, font=fb)
            fa, fd = fb.getmetrics()
            dr.text((bx + (bw2 - tl) / 2.0, by + (bw - fa - fd) / 2.0), txt, font=fb, fill=(255, 255, 255, 255))
        if not rolled_now:
            self._draw_items(img, w, h, t, a)
        if self.drop_hl:
            dr.rounded_rectangle((1, 1, w - 2, h - 2), radius=r, outline=(76, 139, 245, 255), width=self.px(2))
        self._blit(img)

    def _draw_items(self, img, w, h, t, a):
        view_h = h - t
        grid = Image.new("RGBA", (w, max(1, view_h)), (0, 0, 0, 0))
        dr = ImageDraw.Draw(grid)
        x0, cols, cell, ch, pad = self.layout(w)
        icon = self.px(ICON)
        inset = self.px(4)
        fl = _font(None, self.px(12))
        if not self.items:
            msg = B("missing") if not os.path.isdir(self.folder or "") else B("empty")
            tl = dr.textlength(msg, font=fl)
            dr.text(((w - tl) / 2.0, self.px(18)), msg, font=fl, fill=(200, 205, 214, 200))
        full = self.content_h(w)
        self.scroll = max(0, min(self.scroll, max(0, full - view_h)))
        for i, (name, path, is_dir, mtime) in enumerate(self.items):
            col, row = i % cols, i // cols
            cx = x0 + col * cell
            cy = pad + row * ch - self.scroll
            if cy + ch < 0 or cy > view_h:
                continue
            box = (cx + inset, cy + inset // 2, cx + cell - inset, cy + ch - inset)
            if i in self.sel:
                dr.rounded_rectangle(box, radius=self.px(6), fill=(76, 139, 245, 110))
            elif i == self.hover:
                dr.rounded_rectangle(box, radius=self.px(6), fill=(255, 255, 255, 34))
            pic = self.mgr.icons.get(path, icon, mtime)
            ix, iy = cx + (cell - icon) // 2, cy + self.px(7)
            if pic:                                       # bottom-aligned, like Explorer: labels start on one line
                grid.alpha_composite(pic, (ix + (icon - pic.width) // 2, iy + icon - pic.height))
            else:
                dr.rounded_rectangle((ix + self.px(8), iy + self.px(4), ix + icon - self.px(8), iy + icon - self.px(2)),
                                     radius=self.px(4), outline=(210, 214, 222, 160), width=self.px(2))
            lines = self._wrap(dr, self.label(name, is_dir), fl, cell - 2 * inset - self.px(4))
            ly = iy + icon + self.px(4)
            for ln in lines:
                lw = dr.textlength(ln, font=fl)
                lx = round(cx + (cell - lw) / 2.0)
                dr.text((lx + 1, ly + 1), ln, font=fl, fill=(0, 0, 0, 170))
                dr.text((lx, ly), ln, font=fl, fill=(242, 244, 248, 255))
                ly += self.px(15)
        if full > view_h:                                   # slim scrollbar
            bar_h = max(self.px(24), int(view_h * view_h / float(full)))
            by = int((view_h - bar_h) * self.scroll / float(full - view_h))
            dr.rounded_rectangle((w - self.px(5), by + 2, w - self.px(2), by + bar_h - 2), radius=self.px(2),
                                 fill=(255, 255, 255, 90))
        mask = Image.new("L", grid.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, -self.px(RADIUS), w - 1, view_h - 1), radius=self.px(RADIUS),
                                               fill=255)
        clipped = Image.new("RGBA", grid.size, (0, 0, 0, 0))
        clipped.paste(grid, (0, 0), mask)
        img.alpha_composite(clipped, (0, t))

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
        x, y, _w, _h = self.rect()
        cx, cy = self.mgr.to_client(x, y)
        user32.SetWindowPos(self.hwnd, None, cx, cy, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
        blend = BLENDFUNCTION(0, 0, 255, 1)
        user32.UpdateLayeredWindow(self.hwnd, None, None, ctypes.byref(SIZE(w, h)), mdc,
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
        """('title'|'resize-r'|'resize-b'|'resize-rb'|'item'|'empty', index)"""
        _x, _y, w, _h = self.rect()
        h = self.cur_h or _h
        t = self.px(TITLE_H)
        e = self.px(EDGE)
        if not self.rolled() and h > t:
            right, bottom = x >= w - e, y >= h - e
            if right and bottom:
                return "resize-rb", -1
            if right:
                return "resize-r", -1
            if bottom:
                return "resize-b", -1
        if y < t:
            return "title", -1
        x0, cols, cell, ch, pad = self.layout(w)
        gx, gy = x - x0, y - t - pad + self.scroll
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
        self.anim = (self.cur_h, h, time.time())
        user32.SetTimer(self.hwnd, 3, 15, None)

    def _anim_step(self):
        if not self.anim:
            user32.KillTimer(self.hwnd, 3)
            return
        a, b, t0 = self.anim
        k = min(1.0, (time.time() - t0) * 1000.0 / ANIM_MS)
        k = 1 - (1 - k) ** 3
        self.cur_h = int(a + (b - a) * k)
        if k >= 1.0:
            self.cur_h = b
            self.anim = None
            user32.KillTimer(self.hwnd, 3)
        self.render()

    def update_roll(self):
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
            self.scroll -= int(delta / 120.0 * self.px(CELL_H) * 0.6)
            self.render()
            return 0
        if msg == WM_MOUSELEAVE:
            self.tracking = False
            if self.hover != -1:
                self.hover = -1
                self.render()
            user32.KillTimer(self.hwnd, 1)
            if self.d.get("rollup") and self.temp_open and not self.press:
                user32.SetTimer(self.hwnd, 2, LEAVE_CLOSE_MS, None)
            return 0
        if msg == WM_TIMER:
            if wp == 1:                                       # hover over a rolled title -> open
                user32.KillTimer(self.hwnd, 1)
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
            return 0
        if msg == WM_SETCURSOR:
            p = POINT()
            user32.GetCursorPos(ctypes.byref(p))
            user32.ScreenToClient(self.hwnd, ctypes.byref(p))
            zone = self.hit(p.x, p.y)[0]
            cur = {"resize-r": IDC_SIZEWE, "resize-b": IDC_SIZENS, "resize-rb": IDC_SIZENWSE}.get(zone, IDC_ARROW)
            user32.SetCursor(user32.LoadCursorW(None, VP(cur)))
            return 1
        if msg == WM_CAPTURECHANGED:
            if self.press and self.press.get("mode") in ("move", "resize"):
                self._finish_press()
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
        pr = self.press
        if pr:
            p = POINT()
            user32.GetCursorPos(ctypes.byref(p))
            mode = pr["mode"]
            if mode == "item" and (abs(p.x - pr["sx"]) > 4 or abs(p.y - pr["sy"]) > 4):
                self.press = None
                user32.ReleaseCapture()
                paths = [self.items[i][1] for i in sorted(self.sel) if i < len(self.items)]
                self.mgr.defer(lambda: self.mgr.drag_out(self, paths))
                return 0
            if mode == "title" and (abs(p.x - pr["sx"]) > 3 or abs(p.y - pr["sy"]) > 3):
                pr["mode"] = "move"
            if pr["mode"] == "move":
                nx, ny = p.x - pr["dx"], p.y - pr["dy"]
                nx, ny = self.mgr.snap(self, nx, ny)
                r = self.rect()
                self.d.setdefault("rects", {})[self.mgr.sig] = [nx, ny, r[2], r[3]]
                self.place()
                return 0
            if mode == "resize":
                r = self.rect()
                w = r[2] if pr["zone"] == "resize-b" else max(self.px(CELL_W + PAD * 2), p.x - r[0] + pr["ox"])
                h = r[3] if pr["zone"] == "resize-r" else max(self.px(TITLE_H + CELL_H), p.y - r[1] + pr["oy"])
                self.d.setdefault("rects", {})[self.mgr.sig] = [r[0], r[1], w, h]
                self.cur_h = h
                self.render()
                return 0
            return 0
        zone, i = self.hit(x, y)
        if zone == "title" and self.rolled():
            user32.SetTimer(self.hwnd, 1, HOVER_OPEN_MS, None)
        if i != self.hover:
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
        pr = self.press
        if user32.GetCapture() == self.hwnd:
            user32.ReleaseCapture()
        if pr and pr["mode"] == "title" and self.d.get("rollup"):     # click on the title: pin / unpin
            if self.d.get("pinned"):
                self.d["pinned"] = False
                self.temp_open = False
            else:
                self.d["pinned"] = True
            self.mgr.save_soon()
            self.update_roll()
        self._finish_press()
        return 0

    def _finish_press(self):
        pr, self.press = self.press, None
        if pr and pr["mode"] in ("move", "resize"):
            self.set_rect(self.rect())
            self.cur_h = self.target_h()
            self.render()

    def _dbl(self, x, y):
        zone, i = self.hit(x, y)
        if zone == "item":
            path = self.items[i][1]
            self.mgr.defer(lambda: self.mgr.open_item(path))
        elif zone == "title" and not self.d.get("rollup"):
            self.d["collapsed"] = not self.d.get("collapsed")
            self.mgr.save_soon()
            self.update_roll()
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


# ---------------------------------------------------------------------------
# the manager
# ---------------------------------------------------------------------------
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
        self._save_at = 0.0
        self._scan_at = 0.0
        self._pending = {}                  # desktop name -> (size, first seen)
        self._menu_cm = None                # IContextMenu2/3 while a shell menu is open
        self.dbl = None
        self.ole_ok = ole32.OleInitialize(None) in (0, 1)     # S_OK / S_FALSE
        if not self.ole_ok:
            log("boxes: OLE unavailable on the UI thread, drag-and-drop off")
        self._proc = WNDPROC(self._wndproc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(wc)
        wc.style = CS_DBLCLKS
        wc.lpfnWndProc = self._proc
        wc.hInstance = _HINST
        wc.hCursor = user32.LoadCursorW(None, VP(IDC_ARROW))
        wc.lpszClassName = self.CLASS
        user32.RegisterClassExW(ctypes.byref(wc))
        wc2 = WNDCLASSEXW()
        wc2.cbSize = ctypes.sizeof(wc2)
        wc2.lpfnWndProc = self._proc
        wc2.hInstance = _HINST
        wc2.lpszClassName = self.OWNER_CLASS
        user32.RegisterClassExW(ctypes.byref(wc2))
        self.owner = user32.CreateWindowExW(WS_EX_TOOLWINDOW, self.OWNER_CLASS, "HDContainer boxes", WS_POPUP,
                                            0, 0, 0, 0, None, None, _HINST, None)
        for d in self.data["boxes"]:
            self.boxes.append(Box(self, d))
        self._apply_quickhide()
        self.root.after(600, self._tick)

    # ---- persistence ----
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("boxes"), list):
                data.setdefault("rules", {})
                data.setdefault("quickhide", True)
                return data
        except Exception:
            pass
        return {"boxes": [], "rules": {}, "quickhide": True}

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

    # ---- timer: host, listings, desktop rules, quick hide, saving ----
    def _tick(self):
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
                for b in self.boxes:
                    if b.hwnd and b.refresh_items():
                        if not b.rolled():
                            b.mark_seen()
                        b.render()
                self._scan_desktop()
            if self.icons.dirty:
                self.icons.dirty = False
                for b in self.boxes:
                    if b.hwnd and not b.rolled():
                        b.render()
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
        host, _dv = desktop_host()
        if not host:
            return
        if host != self.host or sig != self.sig:
            moved = host != self.host
            self.host, self.sig = host, sig
            for b in self.boxes:
                if b.hwnd and moved and user32.IsWindow(b.hwnd):
                    user32.SetParent(b.hwnd, host)
                if b.hwnd:
                    b.cur_h = b.target_h()
                    b.render()
        for b in self.boxes:
            if b.hwnd and not user32.IsWindow(b.hwnd):        # Explorer restarted
                self.by_hwnd.pop(b.hwnd, None)
                b.hwnd = None
                b._dc = None
            if not b.hwnd:
                b.create(self.host)

    def on_explorer_restart(self):
        self.host = None
        for b in self.boxes:
            if b.hwnd and not user32.IsWindow(b.hwnd):
                self.by_hwnd.pop(b.hwnd, None)
                b.hwnd = None
                b._dc = None

    # ---- moving boxes ----
    def snap(self, box, x, y):
        _x, _y, w, h = box.rect()
        wa = work_area_at(x + w // 2, y + 10)
        s = box.px(12)
        if abs(x - wa[0]) < s:
            x = wa[0]
        if abs(x + w - wa[2]) < s:
            x = wa[2] - w
        if abs(y - wa[1]) < s:
            y = wa[1]
        for o in self.boxes:
            if o is box or not o.hwnd:
                continue
            ox, oy, ow, oh = o.rect()
            if abs(y - oy) < s:
                y = oy
            if abs(x - (ox + ow)) < s and y < oy + oh and y + h > oy:
                x = ox + ow + box.px(4)
            if abs(x + w - ox) < s and y < oy + oh and y + h > oy:
                x = ox - w - box.px(4)
        return x, y

    # ---- items ----
    def open_item(self, path):
        shell32.ShellExecuteW(None, None, path, None, os.path.dirname(path), 1)

    def receive(self, box, files, func):
        folder = box.folder
        if folder and os.path.isdir(folder):
            file_op(func, files, folder, self.log)
            self._scan_at = time.time() + 0.6

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
                pic = self.icons.get(paths[0], box.px(ICON), os.path.getmtime(paths[0]) if os.path.exists(paths[0]) else 0)
                if pic:
                    sdi = SHDRAGIMAGE(SIZE(pic.width, pic.height), POINT(pic.width // 2, pic.height // 2),
                                      image_to_hbitmap(pic), 0xFFFFFFFF)
                    _vcall(helper, 3, HRESULT, [VP, VP], ctypes.byref(sdi), pdo)
            self.keep_on_desktop(paths)                   # dropped on the desktop: don't sort it back
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
        items = ShellItems(paths)
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
                self.keep_on_desktop(paths)
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

    def box_menu(self, box, x, y):
        menu = user32.CreatePopupMenu()
        subs = []
        try:
            def add(m, cid, text, checked=False, grayed=False):
                user32.AppendMenuW(m, MF_STRING | (MF_CHECKED if checked else 0) | (MF_GRAYED if grayed else 0),
                                   cid, text)

            def submenu(text):
                s = user32.CreatePopupMenu()
                subs.append(s)
                user32.AppendMenuW(menu, MF_POPUP, s, text)
                return s
            if self._clipboard_files()[0]:
                add(menu, 0x9001, B("paste"))
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            add(menu, 0x9002, B("new_box"))
            add(menu, 0x9003, B("new_folder_box"))
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            add(menu, 0x9004, B("rename"))
            col = submenu(B("color"))
            add(col, 0x9100, B("no_color"), checked=not box.d.get("color"))
            for i, (key, hx) in enumerate(PALETTE):
                add(col, 0x9101 + i, B(key), checked=(box.d.get("color") or "").lower() == hx)
            tr = submenu(B("transparency"))
            cur = float(box.d.get("alpha", 0.9))
            for i, al in enumerate(ALPHAS):
                text = B("opaque") if al >= 1.0 else "%d%%" % int(round((1 - al) * 100))
                add(tr, 0x9200 + i, text, checked=abs(cur - al) < 0.05)
            add(menu, 0x9005, B("rollup"), checked=bool(box.d.get("rollup")))
            so = submenu(B("sort"))
            for i, k in enumerate(("name", "type", "date")):
                add(so, 0x9300 + i, B("sort_" + k), checked=box.d.get("sort", "name") == k)
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            add(menu, 0x9006, B("open_folder"))
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
            new = self.app._ask_string(B("rename").rstrip("…"), B("name_prompt"), box.name)
            if new:
                self.rename_box(box, new)
        elif cmd == 0x9100:
            box.d["color"] = None
        elif 0x9101 <= cmd < 0x9101 + len(PALETTE):
            box.d["color"] = PALETTE[cmd - 0x9101][1]
        elif 0x9200 <= cmd < 0x9200 + len(ALPHAS):
            box.d["alpha"] = ALPHAS[cmd - 0x9200]
        elif cmd == 0x9005:
            box.d["rollup"] = not box.d.get("rollup")
            box.d["pinned"] = False
            box.temp_open = False
        elif 0x9300 <= cmd < 0x9303:
            box.d["sort"] = ("name", "type", "date")[cmd - 0x9300]
            box.sig = None
            box.refresh_items()
        elif cmd == 0x9006:
            if box.folder:
                shell32.ShellExecuteW(None, "open", box.folder, None, None, 1)
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
    def _place_new(self, at, w=300, h=400):
        if at:
            x, y = at
        else:
            m = monitors()[0][1]
            x, y = (m[0] + m[2]) // 2 - w // 2, (m[1] + m[3]) // 2 - h // 2
        return [x, y, w, h]

    def add_box(self, name, folder=None, at=None, rect=None, **extra):
        own = folder is None
        if own:
            os.makedirs(self.box_root, exist_ok=True)
            kernel32.SetFileAttributesW(self.box_root, 0x2 | 0x4)              # hidden + system
            folder = unique_path(self.box_root, safe_name(name))
            os.makedirs(folder, exist_ok=True)
        d = {"id": "b%x" % int(time.time() * 1000000), "name": name, "dir": folder, "own": own,
             "rects": {}, "last": rect or self._place_new(at), "rollup": False, "alpha": 0.9,
             "sort": "name", "color": None}
        d.update(extra)
        d["rects"][self.sig] = list(d["last"])
        self.data["boxes"].append(d)
        box = Box(self, d)
        self.boxes.append(box)
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
                    dst = unique_path(self.desktop, name)
                    self.keep_on_desktop([dst])
                    shutil.move(os.path.join(box.folder, name), dst)
                except OSError as ex:
                    self.log("return to desktop failed %s: %r" % (name, ex))
            try:
                os.rmdir(box.folder)
            except OSError:
                pass
        box.destroy()
        self.boxes.remove(box)
        self.data["boxes"].remove(box.d)
        for k, v in list(self.data["rules"].items()):
            if v == box.d["id"]:
                self.data["rules"][k] = None
        self.save()
        self._apply_quickhide()

    # ---- desktop: new files by rules ----
    def _desktop_entries(self):
        out = {}
        try:
            for e in os.scandir(self.desktop):
                if e.name == ROOT_NAME or e.name.lower() == "desktop.ini":
                    continue
                try:
                    st = e.stat()
                    if getattr(st, "st_file_attributes", 0) & 0x6:
                        continue
                    out[e.name] = (e.path, e.is_dir(), st.st_size, st.st_mtime)
                except OSError:
                    continue
        except OSError:
            pass
        return out

    def _scan_desktop(self):
        cur = self._desktop_entries()
        seen = self.data.get("desktop_seen")
        if seen is None:                                  # first run: what is there now is not "new"
            self.data["desktop_seen"] = sorted(cur)
            self.save_soon()
            return
        seen = set(seen)
        rules = self.data.get("rules") or {}
        boxes = {b.d["id"]: b for b in self.boxes}
        now = time.time()
        for name, (path, is_dir, size, mtime) in cur.items():
            if name in seen:
                continue
            if name.lower().endswith(TEMP_EXT) or name.startswith("~$"):
                continue
            prev = self._pending.get(name)
            if prev is None or prev[0] != size:           # still being written: wait for a stable size
                self._pending[name] = (size, now)
                continue
            if now - prev[1] < SCAN_EVERY * 1.5:
                continue
            target = boxes.get(rules.get(category(path, is_dir)))
            if target and target.folder and os.path.isdir(target.folder):
                try:
                    shutil.move(path, unique_path(target.folder, name))
                    self._pending.pop(name, None)
                    continue
                except OSError:
                    continue                               # locked: try again later
            seen.add(name)
            self._pending.pop(name, None)
        seen &= set(cur) | set(self._pending)
        if seen != set(self.data["desktop_seen"]):
            self.data["desktop_seen"] = sorted(seen)
            self.save_soon()

    def keep_on_desktop(self, paths):
        seen = set(self.data.get("desktop_seen") or [])
        seen.update(os.path.basename(p) for p in paths)
        self.data["desktop_seen"] = sorted(seen)
        self.save_soon()

    def sort_desktop_now(self):
        rules = self.data.get("rules") or {}
        boxes = {b.d["id"]: b for b in self.boxes}
        n = 0
        for name, (path, is_dir, _s, _m) in self._desktop_entries().items():
            target = boxes.get(rules.get(category(path, is_dir)))
            if target and target.folder and os.path.isdir(target.folder):
                try:
                    shutil.move(path, unique_path(target.folder, name))
                    n += 1
                except OSError as ex:
                    self.log("sort %s failed: %r" % (name, ex))
        self._scan_at = 0
        return n

    # ---- quick hide ----
    def _apply_quickhide(self):
        want = bool(self.data.get("quickhide", True)) and bool(self.boxes)
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
        if not w or _class_of(w) != "SysListView32" or _class_of(user32.GetParent(w)) != "SHELLDLL_DefView":
            return
        if listview_hit(w, x, y) == -1:
            self.set_hidden(not self.hidden)

    def set_hidden(self, on):
        self.hidden = on
        for b in self.boxes:
            if b.hwnd:
                user32.ShowWindow(b.hwnd, 0 if on else 4)

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
        made = {}
        for sec in boxes.sections():
            if not sec.startswith("Box_"):
                continue
            s = boxes[sec]
            if s.get("IsHide", "0") == "1":
                continue
            name = (s.get("BoxName") or "").strip() or B("box_n", len(self.boxes) + 1)
            try:
                rect = [int(s.get(k, d)) for k, d in (("Left", 200), ("Top", 120), ("Width", 320), ("Height", 420))]
            except ValueError:
                rect = None
            existing = next((b for b in self.boxes if b.name == name), None)
            if existing:
                made[sec] = existing.d["id"]
                continue
            rollup = s.get("RollUpBox", "0") == "1"
            b = self.add_box(name, rect=rect, rollup=rollup)
            made[sec] = b.d["id"]
        cfg_path = os.path.join(src_dir, "Config.ini")
        if os.path.isfile(cfg_path):
            cfg = self._read_ini(cfg_path)
            org = cfg["Organization"] if cfg.has_section("Organization") else {}
            keys = {"folders": "BaseTypeFolderBoxTitle", "apps": "BaseTypeAppsBoxTitle",
                    "documents": "BaseTypeDocumentsBoxTitle", "images": "BaseTypeImagesBoxTitle",
                    "videos": "BaseTypeVideosBoxTitle", "music": "BaseTypeMusicBoxTitle",
                    "archives": "BaseTypeCompressedFilesBoxTitle", "programs": "BaseTypeExecutableFilesBoxTitle",
                    "links": "BaseTypeWebLinksBoxTitle", "others": "BaseTypeOthersBoxTitle"}
            for cat, key in keys.items():
                sec = org.get(key)
                if sec in made:
                    self.data["rules"][cat] = made[sec]
            act = cfg["Actions"] if cfg.has_section("Actions") else {}
            if act.get("Dclicking") in ("0", "1"):
                self.data["quickhide"] = act.get("Dclicking") == "1"
        self.save()
        self._apply_quickhide()
        return len(made)

    # ---- settings tab ----
    def begin_edit(self):
        self.draft = {"rules": dict(self.data.get("rules") or {}), "quickhide": self.data.get("quickhide", True)}

    def discard(self):
        self.draft = None

    def commit(self):
        d, self.draft = self.draft, None
        if d is None:
            return
        self.data["rules"] = d["rules"]
        self.data["quickhide"] = d["quickhide"]
        self.save()
        self._apply_quickhide()

    def build_into(self, parent, win, pad):
        main = __import__("__main__")
        C = lambda n, dflt: getattr(main, n, dflt)
        sf, tx, dim, ac, hv, bd = (C("COL_SURFACE", "#232427"), C("COL_TEXT", "#e6e8ea"),
                                   C("COL_TEXT_DIM", "#9aa0a6"), C("COL_ACCENT", "#4c8bf5"),
                                   C("COL_HOVER", "#303236"), C("COL_BORDER", "#2a2b2c"))
        font, font_sm = C("FONT", ("Segoe UI", 10)), C("FONT_SM", ("Segoe UI", 9))
        if self.draft is None:
            self.begin_edit()
        d = self.draft
        tk.Frame(parent, bg=sf, height=8).pack()
        self.app._setting_check(parent, B("quickhide"), d["quickhide"],
                                lambda v: d.__setitem__("quickhide", bool(v)), pad)
        tk.Label(parent, text=B("rules_title"), bg=sf, fg=dim, font=font_sm).pack(anchor="w", padx=pad, pady=(14, 4))
        names = [(None, B("keep"))] + [(b.d["id"], b.name) for b in self.boxes]
        for cat in CATS:
            row = tk.Frame(parent, bg=sf)
            row.pack(fill="x", padx=pad, pady=1)
            tk.Label(row, text=B("cat_" + cat), bg=sf, fg=tx, font=font).pack(side="left")
            cur = d["rules"].get(cat)
            txt = next((n for i, n in names if i == cur), B("keep"))
            val = tk.Label(row, text=txt + "   ⌄", bg=sf, fg=ac if cur else dim, font=font, cursor="hand2")
            val.pack(side="right")
            val.bind("<Button-1>", lambda e, c=cat, v=val: self._pick(v, names, c, sf, tx, hv, bd, ac, dim, font))
        btns = tk.Frame(parent, bg=sf)
        btns.pack(fill="x", padx=pad, pady=(16, 4))
        self.app._accent_btn(btns, B("new_box"), lambda: self.new_box_dialog()).pack(side="left")
        self.app._ghost_btn(btns, B("sort_now"), self._sort_now_dialog).pack(side="left", padx=(8, 0))
        src = self.itop_dir()
        if src:
            imp = tk.Label(parent, text=B("import"), bg=sf, fg=ac, font=font_sm, cursor="hand2")
            imp.pack(anchor="w", padx=pad, pady=(12, 0))
            imp.bind("<Button-1>", lambda e: self._import_dialog(src))
        where = tk.Frame(parent, bg=sf)
        where.pack(fill="x", padx=pad, pady=(14, 0))
        tk.Label(where, text=B("where") + ":  " + self.box_root, bg=sf, fg=dim, font=font_sm,
                 wraplength=520, justify="left").pack(side="left")
        op = tk.Label(where, text=B("open"), bg=sf, fg=ac, font=font_sm, cursor="hand2")
        op.pack(side="left", padx=(8, 0))
        op.bind("<Button-1>", lambda e: os.path.isdir(self.box_root) and
                shell32.ShellExecuteW(None, "open", self.box_root, None, None, 1))

    def _pick(self, anchor, names, cat, sf, tx, hv, bd, ac, dim, font):
        pop = tk.Toplevel(anchor)
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        pop.configure(bg=bd)
        fr = tk.Frame(pop, bg=sf)
        fr.pack(padx=1, pady=1)

        def pick(bid, text):
            pop.destroy()
            self.draft["rules"][cat] = bid
            anchor.configure(text=text + "   ⌄", fg=ac if bid else dim)
        for bid, text in names:
            r = tk.Label(fr, text=text, bg=sf, fg=tx, font=font, anchor="w", padx=14, pady=5, cursor="hand2")
            r.pack(fill="x")
            r.bind("<Enter>", lambda e, r=r: r.configure(bg=hv))
            r.bind("<Leave>", lambda e, r=r: r.configure(bg=sf))
            r.bind("<Button-1>", lambda e, b=bid, t=text: pick(b, t))
        anchor.update_idletasks()
        pop.update_idletasks()
        x = max(0, anchor.winfo_rootx() + anchor.winfo_width() - pop.winfo_reqwidth())
        pop.geometry("+%d+%d" % (x, anchor.winfo_rooty() + anchor.winfo_height() + 2))
        pop.focus_force()
        pop.after(200, lambda: pop.winfo_exists() and pop.bind("<FocusOut>", lambda e: pop.destroy()))

    def _sort_now_dialog(self):
        if self.draft is not None:                 # apply the rules shown in the open tab
            self.data["rules"] = dict(self.draft["rules"])
            self.save()
        n = self.sort_desktop_now()
        self.app._info(B("tab"), B("sorted_n", n))

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
        self.app._settings_show("boxes")
        if n and self.app._ask_yesno(B("tab"), B("import_done", n)):
            self.app._info(B("tab"), B("sorted_n", self.sort_desktop_now()))

    # ---- shutdown ----
    def shutdown(self):
        if self.dbl is not None:
            self.dbl.stop()
        self.save()
        for b in self.boxes:
            b.destroy()
