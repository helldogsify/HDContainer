# -*- coding: utf-8 -*-
"""
Оконный контейнер (Window Container) — трей-архитектура, без главного окна.
===========================================================================
Никакого видимого интерфейса, кроме значка в системном трее. По клику —
контекстное меню:

    [Контейнер A] ▸ Активировать / Добавить окно / Сделать текущим /
                     Переименовать / Удалить
    [Контейнер B] ▸ ...
    ───────────────
    ➕ Создать контейнер…
    ➕ Добавить окно в текущий (…)
    ───────────────
    Выход

Каждый АКТИВНЫЙ контейнер — это невидимое полноэкранное окно-владелец
(layered, alpha 0, click-through). Окна-члены делаются "owned" этим хостом
через SetWindowLongPtr(GWLP_HWNDPARENT). Тогда:

  * каждое окно остаётся самостоятельным top-level → СВОЯ очередь ввода →
    клавиатура, буфер и раскладка (Alt+Shift) работают НАТИВНО;
  * owned-окна держатся над (невидимым) хостом, прячутся при его сворачивании
    и возвращаются при разворачивании (в т.ч. Win+D), уходят из таскбара/Alt+Tab;
  * хост — единственная кнопка группы в таскбаре (несёт имя и иконку контейнера);
  * окна можно ставить по экрану КАК УГОДНО — рамка контейнера их не ограничивает,
    их можно даже разворачивать на весь экран (перекрывать нечего — хост невидим).

Несколько контейнеров активны одновременно — несколько таких хостов.
Контейнеры сохраняются (имя + список приложений) и при активации
переподхватывают уже открытые окна, а недостающие приложения запускают заново.

⚠️ Windows УНИЧТОЖАЕТ owned-окно вместе с владельцем → перед уничтожением хоста
со всех его окон снимается владение (иначе убьём VS Code/Chrome). Плюс
восстановление осиротевших окон при старте — как страховка.
"""

import os
import sys
import json
import time
import tempfile
import shutil
import subprocess
import threading
import traceback
import faulthandler
import winreg
import ssl
import webbrowser
import urllib.request
import ctypes
from ctypes import wintypes
import tkinter as tk

try:                                   # Pillow: аватар из любой картинки + чёткие иконки
    from PIL import Image, ImageDraw, ImageTk
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

VERSION = "1.2.4"
GITHUB_REPO = "helldogsify/HDContainer"
GITHUB_URL = "https://github.com/" + GITHUB_REPO
DONATE_ADDR = "TWG8Y5EyaqQf8GsJKJVhcaAMFZxxHoPWzC"
DONATE_NET = "USDT · TRON (TRC20)"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor v2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)


def _exe_dir():
    try:
        d = os.path.dirname(os.path.abspath(sys.argv[0]))
        if os.path.isdir(d):
            return d
    except Exception:
        pass
    return os.getcwd()


def _data_dir():
    # СТАБИЛЬНОЕ место данных (не рядом с exe!), чтобы обновление/переустановка
    # в другую папку не теряли контейнеры
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    d = os.path.join(base, "HDContainer")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return _exe_dir()


APP_NAME = "HDContainer"
IPC_TITLE = "HDContainer::IPC::singleton"

_EXE_DIR = _exe_dir()
_DIR = _data_dir()
_ICON = os.path.join(_EXE_DIR, "HDContainer.ico")          # иконка лежит рядом с exe
_LOG = os.path.join(_DIR, "HDContainer_debug.log")
_RECOVERY = os.path.join(_DIR, "HDContainer_recovery.json")
_STORE = os.path.join(_DIR, "HDContainer_containers.json")
_ICONDIR = os.path.join(_DIR, "icons")   # пользовательские иконки контейнеров


def _migrate_data():
    # перенос данных из прежних мест (рядом с exe / Public\\WC / прошлая установка)
    # в стабильную папку — чтобы апдейт не «терял» контейнеры
    if os.path.exists(_STORE):
        return
    cands = [_EXE_DIR, r"C:\Users\Public\WC",
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "HDContainer")]
    best, best_key = None, (0, -1.0)   # выбираем по (число контейнеров, mtime)
    for d in cands:
        try:
            if not d or os.path.abspath(d) == os.path.abspath(_DIR):
                continue
            p = os.path.join(d, "HDContainer_containers.json")
            if not os.path.exists(p):
                continue
            try:
                n = len(json.load(open(p, "r", encoding="utf-8")))
            except Exception:
                n = 0
            key = (n, os.path.getmtime(p))
            if key > best_key:
                best_key, best = key, d
        except Exception:
            pass
    if not best or best_key[0] == 0:   # нечего восстанавливать
        return
    try:
        for name in ("HDContainer_containers.json", "HDContainer_settings.json"):
            src = os.path.join(best, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(_DIR, name))
        src_icons = os.path.join(best, "icons")
        if os.path.isdir(src_icons):
            os.makedirs(_ICONDIR, exist_ok=True)
            for f in os.listdir(src_icons):
                try:
                    shutil.copy2(os.path.join(src_icons, f), os.path.join(_ICONDIR, f))
                except Exception:
                    pass
    except Exception:
        pass


_migrate_data()


def log(msg):
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
    except Exception:
        pass


# нативные крэши (ctypes/Access Violation) -> стек в файл (ДОЗАПИСЬ, чтобы стек
# не стирался при следующем запуске)
try:
    _CRASHF = open(os.path.join(_DIR, "HDContainer_crash.log"), "a")
    _CRASHF.write("\n==== STARTUP %s ====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    _CRASHF.flush()
    faulthandler.enable(_CRASHF)
except Exception:
    pass

log("==== STARTUP %s argv=%r ====" % (time.strftime("%Y-%m-%d %H:%M:%S"), sys.argv[1:]))


def _excepthook(et, ev, tb):
    log("UNCAUGHT:\n" + "".join(traceback.format_exception(et, ev, tb)))


sys.excepthook = _excepthook


# ---------------------------------------------------------------------------
# Палитра (для диалогов)
# ---------------------------------------------------------------------------
COL_BG       = "#1b1b1d"
COL_SURFACE  = "#232427"
COL_SURFACE2 = "#2d2e31"
COL_BORDER   = "#2a2b2c"
COL_HOVER    = "#303236"
COL_ACCENT   = "#4c8bf5"
COL_ACCENT_HI = "#629bff"
COL_TEXT     = "#e6e8ea"
COL_TEXT_DIM = "#9aa0a6"

FONT       = ("Segoe UI", 10)
FONT_SM    = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI Semibold", 10)
FONT_H     = ("Segoe UI Semibold", 14)

# ---------------------------------------------------------------------------
# Настройки + язык (i18n)
# ---------------------------------------------------------------------------
_SETTINGS = os.path.join(_DIR, "HDContainer_settings.json")


def load_settings():
    try:
        with open(_SETTINGS, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    try:
        with open(_SETTINGS, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


LANG = "en"
LANG_NAMES = {"en": "English", "ru": "Русский", "es": "Español",
              "pt": "Português", "de": "Deutsch", "fr": "Français", "zh": "中文"}

STRINGS = {
    "tray_tip": {"en": "HDContainer — %d active / %d total", "ru": "HDContainer — активно %d / всего %d",
                 "es": "HDContainer — %d activos / %d en total", "pt": "HDContainer — %d ativos / %d no total",
                 "de": "HDContainer — %d aktiv / %d gesamt", "fr": "HDContainer — %d actifs / %d au total",
                 "zh": "HDContainer — %d 个活动 / 共 %d 个"},
    "container_n": {"en": "Container %d", "ru": "Контейнер %d", "es": "Contenedor %d", "pt": "Contêiner %d",
                    "de": "Container %d", "fr": "Conteneur %d", "zh": "容器 %d"},
    "activate": {"en": "Activate", "ru": "Активировать", "es": "Activar", "pt": "Ativar", "de": "Aktivieren", "fr": "Activer", "zh": "激活"},
    "deactivate": {"en": "Deactivate", "ru": "Деактивировать", "es": "Desactivar", "pt": "Desativar", "de": "Deaktivieren", "fr": "Désactiver", "zh": "停用"},
    "add_here": {"en": "Add windows here…", "ru": "Добавить окна сюда…", "es": "Añadir ventanas aquí…", "pt": "Adicionar janelas aqui…", "de": "Fenster hier hinzufügen…", "fr": "Ajouter des fenêtres ici…", "zh": "在此添加窗口…"},
    "make_current": {"en": "Make current", "ru": "Сделать текущим", "es": "Marcar como actual", "pt": "Tornar atual", "de": "Als aktuell setzen", "fr": "Définir comme actuel", "zh": "设为当前"},
    "rename": {"en": "Rename…", "ru": "Переименовать…", "es": "Renombrar…", "pt": "Renomear…", "de": "Umbenennen…", "fr": "Renommer…", "zh": "重命名…"},
    "set_icon": {"en": "Set icon…", "ru": "Задать иконку…", "es": "Establecer icono…", "pt": "Definir ícone…", "de": "Symbol festlegen…", "fr": "Définir l’icône…", "zh": "设置图标…"},
    "set_color": {"en": "Color label…", "ru": "Цветная метка…", "es": "Etiqueta de color…", "pt": "Etiqueta de cor…", "de": "Farbmarkierung…", "fr": "Étiquette de couleur…", "zh": "颜色标记…"},
    "clear_color": {"en": "Clear color", "ru": "Убрать цвет", "es": "Quitar color", "pt": "Remover cor", "de": "Farbe entfernen", "fr": "Retirer la couleur", "zh": "清除颜色"},
    "create_shortcut": {"en": "Create desktop shortcut", "ru": "Создать ярлык на рабочем столе", "es": "Crear acceso directo", "pt": "Criar atalho na área de trabalho", "de": "Desktop-Verknüpfung erstellen", "fr": "Créer un raccourci bureau", "zh": "创建桌面快捷方式"},
    "delete": {"en": "Delete", "ru": "Удалить", "es": "Eliminar", "pt": "Excluir", "de": "Löschen", "fr": "Supprimer", "zh": "删除"},
    "create_container": {"en": "Create container", "ru": "Создать контейнер", "es": "Crear contenedor", "pt": "Criar contêiner", "de": "Container erstellen", "fr": "Créer un conteneur", "zh": "创建容器"},
    "add_to_current": {"en": "Add windows to current (%s)", "ru": "Добавить окна в текущий (%s)", "es": "Añadir ventanas al actual (%s)", "pt": "Adicionar janelas ao atual (%s)", "de": "Fenster zum aktuellen hinzufügen (%s)", "fr": "Ajouter au conteneur actuel (%s)", "zh": "添加窗口到当前 (%s)"},
    "current": {"en": "current", "ru": "текущий", "es": "actual", "pt": "atual", "de": "aktuell", "fr": "actuel", "zh": "当前"},
    "settings": {"en": "Settings", "ru": "Настройки", "es": "Ajustes", "pt": "Configurações", "de": "Einstellungen", "fr": "Paramètres", "zh": "设置"},
    "quit": {"en": "Quit", "ru": "Выход", "es": "Salir", "pt": "Sair", "de": "Beenden", "fr": "Quitter", "zh": "退出"},
    "new_container": {"en": "New container", "ru": "Новый контейнер", "es": "Nuevo contenedor", "pt": "Novo contêiner", "de": "Neuer Container", "fr": "Nouveau conteneur", "zh": "新建容器"},
    "name_label": {"en": "Container name:", "ru": "Название контейнера:", "es": "Nombre del contenedor:", "pt": "Nome do contêiner:", "de": "Container-Name:", "fr": "Nom du conteneur :", "zh": "容器名称："},
    "rename_title": {"en": "Rename", "ru": "Переименовать", "es": "Renombrar", "pt": "Renomear", "de": "Umbenennen", "fr": "Renommer", "zh": "重命名"},
    "new_name": {"en": "New name:", "ru": "Новое название:", "es": "Nuevo nombre:", "pt": "Novo nome:", "de": "Neuer Name:", "fr": "Nouveau nom :", "zh": "新名称："},
    "delete_title": {"en": "Delete container", "ru": "Удалить контейнер", "es": "Eliminar contenedor", "pt": "Excluir contêiner", "de": "Container löschen", "fr": "Supprimer le conteneur", "zh": "删除容器"},
    "delete_msg": {"en": "Delete container “%s”?\nIts windows will return to the desktop.", "ru": "Удалить контейнер «%s»?\nОкна вернутся на рабочий стол.", "es": "¿Eliminar el contenedor «%s»?\nSus ventanas volverán al escritorio.", "pt": "Excluir o contêiner “%s”?\nAs janelas voltarão para a área de trabalho.", "de": "Container „%s“ löschen?\nSeine Fenster kehren zum Desktop zurück.", "fr": "Supprimer le conteneur « %s » ?\nSes fenêtres reviendront au bureau.", "zh": "删除容器“%s”？\n其窗口将返回桌面。"},
    "ok": {"en": "OK", "ru": "OK", "es": "Aceptar", "pt": "OK", "de": "OK", "fr": "OK", "zh": "确定"},
    "cancel": {"en": "Cancel", "ru": "Отмена", "es": "Cancelar", "pt": "Cancelar", "de": "Abbrechen", "fr": "Annuler", "zh": "取消"},
    "yes": {"en": "Yes", "ru": "Да", "es": "Sí", "pt": "Sim", "de": "Ja", "fr": "Oui", "zh": "是"},
    "no": {"en": "No", "ru": "Нет", "es": "No", "pt": "Não", "de": "Nein", "fr": "Non", "zh": "否"},
    "pick_title": {"en": "Add windows to the group", "ru": "Добавить окна в группу", "es": "Añadir ventanas al grupo", "pt": "Adicionar janelas ao grupo", "de": "Fenster zur Gruppe hinzufügen", "fr": "Ajouter des fenêtres au groupe", "zh": "将窗口加入分组"},
    "pick_hint": {"en": "Pick windows (several allowed) and click Add", "ru": "Выберите окна (можно несколько) и нажмите «Добавить»", "es": "Elige ventanas (varias posibles) y pulsa Añadir", "pt": "Escolha janelas (várias possíveis) e clique em Adicionar", "de": "Fenster auswählen (mehrere möglich) und Hinzufügen klicken", "fr": "Choisissez des fenêtres (plusieurs possibles) puis Ajouter", "zh": "选择窗口（可多选）并点击添加"},
    "add": {"en": "Add", "ru": "Добавить", "es": "Añadir", "pt": "Adicionar", "de": "Hinzufügen", "fr": "Ajouter", "zh": "添加"},
    "no_windows": {"en": "No suitable windows found.", "ru": "Подходящих окон не найдено.", "es": "No se encontraron ventanas adecuadas.", "pt": "Nenhuma janela adequada encontrada.", "de": "Keine passenden Fenster gefunden.", "fr": "Aucune fenêtre appropriée trouvée.", "zh": "未找到合适的窗口。"},
    "no_preview": {"en": "(no preview)", "ru": "(превью недоступно)", "es": "(sin vista previa)", "pt": "(sem prévia)", "de": "(keine Vorschau)", "fr": "(pas d’aperçu)", "zh": "(无预览)"},
    "icon_title": {"en": "Container “%s” icon (.ico)", "ru": "Иконка контейнера «%s» (.ico)", "es": "Icono del contenedor «%s» (.ico)", "pt": "Ícone do contêiner “%s” (.ico)", "de": "Symbol für Container „%s“ (.ico)", "fr": "Icône du conteneur « %s » (.ico)", "zh": "容器“%s”的图标 (.ico)"},
    "need_ico_title": {"en": "An .ico file is needed", "ru": "Нужен файл .ico", "es": "Se necesita un archivo .ico", "pt": "É necessário um arquivo .ico", "de": "Eine .ico-Datei wird benötigt", "fr": "Un fichier .ico est requis", "zh": "需要 .ico 文件"},
    "need_ico_msg": {"en": "The taskbar icon and shortcut need a .ico file.\nConvert a PNG to ICO and choose it.", "ru": "Для иконки на панели задач и ярлыка нужен .ico файл.\nСконвертируй PNG в ICO и выбери его.", "es": "El icono y el acceso directo necesitan un archivo .ico.\nConvierte un PNG a ICO y selecciónalo.", "pt": "O ícone e o atalho precisam de um arquivo .ico.\nConverta um PNG para ICO e escolha-o.", "de": "Symbol und Verknüpfung benötigen eine .ico-Datei.\nWandle ein PNG in ICO um und wähle es.", "fr": "L’icône et le raccourci nécessitent un fichier .ico.\nConvertissez un PNG en ICO et choisissez-le.", "zh": "任务栏图标和快捷方式需要 .ico 文件。\n请将 PNG 转换为 ICO 并选择。"},
    "color_title": {"en": "Color label “%s”", "ru": "Цветная метка «%s»", "es": "Etiqueta de color «%s»", "pt": "Etiqueta de cor “%s”", "de": "Farbmarkierung „%s“", "fr": "Étiquette de couleur « %s »", "zh": "颜色标记“%s”"},
    "shortcut_title": {"en": "Shortcut", "ru": "Ярлык", "es": "Acceso directo", "pt": "Atalho", "de": "Verknüpfung", "fr": "Raccourci", "zh": "快捷方式"},
    "shortcut_ok": {"en": "Shortcut created on the desktop:\n%s\n\nLaunching it raises the container and opens its apps.", "ru": "Ярлык создан на рабочем столе:\n%s\n\nЗапуск ярлыка поднимет контейнер и откроет его приложения.", "es": "Acceso directo creado en el escritorio:\n%s\n\nAl abrirlo se levanta el contenedor y sus apps.", "pt": "Atalho criado na área de trabalho:\n%s\n\nAbri-lo levanta o contêiner e abre seus apps.", "de": "Verknüpfung auf dem Desktop erstellt:\n%s\n\nIhr Start öffnet den Container und seine Apps.", "fr": "Raccourci créé sur le bureau :\n%s\n\nSon lancement ouvre le conteneur et ses apps.", "zh": "已在桌面创建快捷方式：\n%s\n\n运行它会唤起容器并打开其应用。"},
    "shortcut_fail": {"en": "Could not create the shortcut (see log).", "ru": "Не удалось создать ярлык (см. лог).", "es": "No se pudo crear el acceso directo (ver registro).", "pt": "Não foi possível criar o atalho (ver log).", "de": "Verknüpfung konnte nicht erstellt werden (siehe Log).", "fr": "Impossible de créer le raccourci (voir le journal).", "zh": "无法创建快捷方式（见日志）。"},
    "settings_title": {"en": "Settings", "ru": "Настройки", "es": "Ajustes", "pt": "Configurações", "de": "Einstellungen", "fr": "Paramètres", "zh": "设置"},
    "run_with_windows": {"en": "Start with Windows", "ru": "Запускать с Windows", "es": "Iniciar con Windows", "pt": "Iniciar com o Windows", "de": "Mit Windows starten", "fr": "Démarrer avec Windows", "zh": "随 Windows 启动"},
    "auto_update": {"en": "Automatic updates", "ru": "Автообновление", "es": "Actualizaciones automáticas", "pt": "Atualizações automáticas", "de": "Automatische Updates", "fr": "Mises à jour automatiques", "zh": "自动更新"},
    "language": {"en": "Language", "ru": "Язык", "es": "Idioma", "pt": "Idioma", "de": "Sprache", "fr": "Langue", "zh": "语言"},
    "guide_title": {"en": "How it works", "ru": "Как это работает", "es": "Cómo funciona", "pt": "Como funciona", "de": "So funktioniert es", "fr": "Comment ça marche", "zh": "工作原理"},
    "guide_text": {
        "en": "A task is usually several windows at once — editor, browser, terminal, a video. HDContainer groups them into one container that behaves like a single app, so you flip your whole workspace with one Alt+Tab instead of digging through windows.\n\n• Put the windows for a task into a container — one taskbar button, one Alt+Tab entry.\n• Switch tasks in a single keystroke; the whole set comes forward together.\n• Save a container as a desktop shortcut. Reopening it puts back windows that are still open exactly where they were; apps that were closed get relaunched, but their exact size and position can't always be restored.\n• Windows stay real — keyboard, clipboard and Alt+Shift layout switching keep working.\n• Tick a container in the menu to switch it on or off; name and color-label them to tell them apart. Quit from the tray to release all windows.",
        "ru": "Задача — это обычно несколько окон сразу: редактор, браузер, терминал, видео. HDContainer собирает их в один контейнер, который ведёт себя как одна программа, — и ты переключаешь весь рабочий набор одним Alt+Tab, а не ищешь окна по отдельности.\n\n• Сложи окна задачи в контейнер — одна кнопка в таскбаре, один Alt+Tab.\n• Переключай задачи одним нажатием; весь набор выходит вперёд вместе.\n• Сохрани контейнер ярлыком. При открытии окна, что ещё открыты, встают точно на свои места; закрытые приложения запускаются заново, но их точный размер и позицию воссоздать удаётся не всегда.\n• Окна остаются настоящими — клавиатура, буфер и смена раскладки Alt+Shift работают.\n• Галочкой в меню включаешь/выключаешь контейнер; имя и цветная метка — чтобы различать. Выход — через трей, он вернёт все окна.",
        "es": "Una tarea suele ser varias ventanas a la vez: editor, navegador, terminal, un vídeo. HDContainer las agrupa en un contenedor que se comporta como una sola app, así cambias todo tu espacio de trabajo con un Alt+Tab en vez de buscar ventanas una a una.\n\n• Mete las ventanas de una tarea en un contenedor — un botón en la barra, un Alt+Tab.\n• Cambia de tarea con una tecla; todo el conjunto aparece junto.\n• Guarda el contenedor como acceso directo. Al reabrirlo, las ventanas que siguen abiertas vuelven a su sitio exacto; las apps cerradas se relanzan, pero su tamaño y posición exactos no siempre se pueden restaurar.\n• Las ventanas siguen siendo reales — teclado, portapapeles y cambio de teclado Alt+Shift funcionan.\n• Marca un contenedor en el menú para activarlo o desactivarlo; ponles nombre y color para distinguirlos. Sal desde la bandeja para liberar las ventanas.",
        "pt": "Uma tarefa costuma ser várias janelas ao mesmo tempo: editor, navegador, terminal, um vídeo. O HDContainer as agrupa em um contêiner que se comporta como um único app, então você troca todo o seu espaço de trabalho com um Alt+Tab em vez de caçar janelas uma a uma.\n\n• Coloque as janelas de uma tarefa em um contêiner — um botão na barra, um Alt+Tab.\n• Troque de tarefa com uma tecla; todo o conjunto vem junto.\n• Salve o contêiner como atalho. Ao reabri-lo, as janelas ainda abertas voltam ao lugar exato; apps fechados são reabertos, mas o tamanho e a posição exatos nem sempre podem ser restaurados.\n• As janelas continuam reais — teclado, área de transferência e troca de layout Alt+Shift funcionam.\n• Marque um contêiner no menu para ligá-lo ou desligá-lo; dê nome e cor para distingui-los. Saia pela bandeja para liberar as janelas.",
        "de": "Eine Aufgabe besteht meist aus mehreren Fenstern zugleich — Editor, Browser, Terminal, ein Video. HDContainer fasst sie in einem Container zusammen, der sich wie eine einzige App verhält, sodass du deinen ganzen Arbeitsbereich mit einem Alt+Tab umschaltest, statt Fenster einzeln zu suchen.\n\n• Leg die Fenster einer Aufgabe in einen Container — ein Taskbar-Knopf, ein Alt+Tab.\n• Wechsle Aufgaben mit einem Tastendruck; der ganze Satz kommt zusammen nach vorn.\n• Speichere einen Container als Verknüpfung. Beim erneuten Öffnen kehren noch offene Fenster genau an ihren Platz zurück; geschlossene Apps werden neu gestartet, ihre genaue Größe und Position lassen sich aber nicht immer wiederherstellen.\n• Fenster bleiben echt — Tastatur, Zwischenablage und Alt+Shift-Layoutwechsel funktionieren.\n• Hake einen Container im Menü an, um ihn ein- oder auszuschalten; benenne und färbe sie zur Unterscheidung. Über das Tray beenden, um alle Fenster freizugeben.",
        "fr": "Une tâche, c'est souvent plusieurs fenêtres à la fois : éditeur, navigateur, terminal, une vidéo. HDContainer les regroupe dans un conteneur qui se comporte comme une seule appli, donc tu bascules tout ton espace de travail d'un seul Alt+Tab au lieu de chercher les fenêtres une à une.\n\n• Mets les fenêtres d'une tâche dans un conteneur — un bouton de barre, un Alt+Tab.\n• Change de tâche d'une touche ; tout l'ensemble revient groupé.\n• Enregistre un conteneur en raccourci. À sa réouverture, les fenêtres encore ouvertes reviennent exactement à leur place ; les applis fermées sont relancées, mais leur taille et position exactes ne sont pas toujours restaurables.\n• Les fenêtres restent réelles — clavier, presse-papiers et changement de disposition Alt+Shift fonctionnent.\n• Coche un conteneur dans le menu pour l'activer ou le désactiver ; nomme-les et colore-les pour les distinguer. Quitte via la zone de notification pour libérer les fenêtres.",
        "zh": "一个任务通常同时开着好几个窗口——编辑器、浏览器、终端、一个视频。HDContainer 把它们归为一个容器，像单个应用一样，于是你用一次 Alt+Tab 就切换整个工作区，而不必逐个去找窗口。\n\n• 把一个任务的窗口放进一个容器——一个任务栏按钮，一个 Alt+Tab。\n• 一键切换任务；整组窗口一起回到面前。\n• 把容器保存为桌面快捷方式。再次打开时，仍开着的窗口会精确回到原位；已关闭的应用会被重新启动，但其确切的大小和位置不一定能还原。\n• 窗口仍是真实窗口——键盘、剪贴板和 Alt+Shift 输入法切换都正常。\n• 在菜单里勾选容器即可开启或关闭；给它们命名和颜色以便区分。通过托盘退出可释放所有窗口。",
    },
    "credits": {"en": "Vibe-coded by hdk with Claude Code.\nFree for everyone.", "ru": "Завайбкодил hdk с помощью Claude Code.\nБесплатно для всех.", "es": "Hecho con flow por hdk con Claude Code.\nGratis para todos.", "pt": "Feito no flow por hdk com Claude Code.\nGrátis para todos.", "de": "Vibe-coded von hdk mit Claude Code.\nKostenlos für alle.", "fr": "Vibe-codé par hdk avec Claude Code.\nGratuit pour tous.", "zh": "由 hdk 借助 Claude Code 随性编写。\n对所有人免费。"},
    "donate_label": {"en": "Tip the author — %s:", "ru": "Поблагодарить автора — %s:", "es": "Apoyar al autor — %s:", "pt": "Agradecer ao autor — %s:", "de": "Den Autor unterstützen — %s:", "fr": "Soutenir l’auteur — %s :", "zh": "打赏作者 — %s："},
    "copy": {"en": "Copy", "ru": "Копировать", "es": "Copiar", "pt": "Copiar", "de": "Kopieren", "fr": "Copier", "zh": "复制"},
    "copied": {"en": "Copied", "ru": "Скопировано", "es": "Copiado", "pt": "Copiado", "de": "Kopiert", "fr": "Copié", "zh": "已复制"},
    "version_label": {"en": "Version %s", "ru": "Версия %s", "es": "Versión %s", "pt": "Versão %s", "de": "Version %s", "fr": "Version %s", "zh": "版本 %s"},
    "update_title": {"en": "Update", "ru": "Обновление", "es": "Actualización", "pt": "Atualização", "de": "Update", "fr": "Mise à jour", "zh": "更新"},
    "update_available": {"en": "Version %s is available. Update now?", "ru": "Доступна версия %s. Обновить сейчас?", "es": "La versión %s está disponible. ¿Actualizar ahora?", "pt": "A versão %s está disponível. Atualizar agora?", "de": "Version %s ist verfügbar. Jetzt aktualisieren?", "fr": "La version %s est disponible. Mettre à jour maintenant ?", "zh": "有新版本 %s。现在更新吗？"},
    "update_fail": {"en": "Update failed (see log).", "ru": "Не удалось обновить (см. лог).", "es": "Error al actualizar (ver registro).", "pt": "Falha na atualização (ver log).", "de": "Update fehlgeschlagen (siehe Log).", "fr": "Échec de la mise à jour (voir le journal).", "zh": "更新失败（见日志）。"},
    "update_fail_manual": {"en": "Couldn't update automatically — opening the download page so you can install the latest version manually.", "ru": "Не получилось обновить автоматически — открываю страницу загрузки, поставь последнюю версию вручную.", "es": "No se pudo actualizar automáticamente — abriendo la página de descarga para instalar la última versión manualmente.", "pt": "Não foi possível atualizar automaticamente — abrindo a página de download para instalar a versão mais recente manualmente.", "de": "Automatisches Update fehlgeschlagen — die Download-Seite wird geöffnet, bitte installiere die neueste Version manuell.", "fr": "Mise à jour automatique impossible — ouverture de la page de téléchargement pour installer la dernière version manuellement.", "zh": "无法自动更新——正在打开下载页面，请手动安装最新版本。"},
    "check_update": {"en": "Check for updates", "ru": "Проверить обновления", "es": "Buscar actualizaciones", "pt": "Verificar atualizações", "de": "Nach Updates suchen", "fr": "Rechercher des mises à jour", "zh": "检查更新"},
    "up_to_date": {"en": "You have the latest version.", "ru": "У вас последняя версия.", "es": "Tienes la última versión.", "pt": "Você tem a versão mais recente.", "de": "Du hast die neueste Version.", "fr": "Vous avez la dernière version.", "zh": "已是最新版本。"},
    "manage": {"en": "Manage containers", "ru": "Управление контейнерами", "es": "Gestionar contenedores", "pt": "Gerenciar contêineres", "de": "Container verwalten", "fr": "Gérer les conteneurs", "zh": "管理容器"},
    "reset_look": {"en": "Reset to default look", "ru": "Сбросить оформление", "es": "Restablecer apariencia", "pt": "Redefinir aparência", "de": "Aussehen zurücksetzen", "fr": "Réinitialiser l’apparence", "zh": "恢复默认外观"},
    "active": {"en": "Active", "ru": "Активен", "es": "Activo", "pt": "Ativo", "de": "Aktiv", "fr": "Actif", "zh": "已激活"},
    "edit_windows": {"en": "Edit windows…", "ru": "Редактировать окна…", "es": "Editar ventanas…", "pt": "Editar janelas…", "de": "Fenster bearbeiten…", "fr": "Modifier les fenêtres…", "zh": "编辑窗口…"},
    "edit_hint": {"en": "Tick the windows that belong to this container, then Apply", "ru": "Отметь окна, которые входят в контейнер, и нажми «Применить»", "es": "Marca las ventanas de este contenedor y pulsa Aplicar", "pt": "Marque as janelas deste contêiner e clique em Aplicar", "de": "Markiere die Fenster dieses Containers und klicke auf Übernehmen", "fr": "Coche les fenêtres de ce conteneur puis Appliquer", "zh": "勾选属于该容器的窗口，然后点击应用"},
    "apply": {"en": "Apply", "ru": "Применить", "es": "Aplicar", "pt": "Aplicar", "de": "Übernehmen", "fr": "Appliquer", "zh": "应用"},
    "arrange": {"en": "Arrange:", "ru": "Разложить:", "es": "Organizar:", "pt": "Organizar:", "de": "Anordnen:", "fr": "Disposer :", "zh": "排列："},
    "lay_cols": {"en": "Columns", "ru": "Колонки", "es": "Columnas", "pt": "Colunas", "de": "Spalten", "fr": "Colonnes", "zh": "并排列"},
    "lay_grid": {"en": "Grid", "ru": "Сетка", "es": "Cuadrícula", "pt": "Grade", "de": "Raster", "fr": "Grille", "zh": "网格"},
    "lay_master": {"en": "Master + stack", "ru": "Главное + стек", "es": "Principal + pila", "pt": "Principal + pilha", "de": "Haupt + Stapel", "fr": "Principal + pile", "zh": "主 + 堆叠"},
    "edit_container": {"en": "Edit container", "ru": "Изменить контейнер", "es": "Editar contenedor", "pt": "Editar contêiner", "de": "Container bearbeiten", "fr": "Modifier le conteneur", "zh": "编辑容器"},
    "save": {"en": "Save", "ru": "Сохранить", "es": "Guardar", "pt": "Salvar", "de": "Speichern", "fr": "Enregistrer", "zh": "保存"},
    "lbl_name": {"en": "Name", "ru": "Имя", "es": "Nombre", "pt": "Nome", "de": "Name", "fr": "Nom", "zh": "名称"},
    "lbl_icon": {"en": "Icon", "ru": "Иконка", "es": "Icono", "pt": "Ícone", "de": "Symbol", "fr": "Icône", "zh": "图标"},
    "lbl_color": {"en": "Color", "ru": "Цвет", "es": "Color", "pt": "Cor", "de": "Farbe", "fr": "Couleur", "zh": "颜色"},
    "lbl_windows": {"en": "Windows", "ru": "Окна", "es": "Ventanas", "pt": "Janelas", "de": "Fenster", "fr": "Fenêtres", "zh": "窗口"},
    "choose_ico": {"en": "Choose icon", "ru": "Выбрать иконку", "es": "Elegir icono", "pt": "Escolher ícone", "de": "Symbol wählen", "fr": "Choisir une icône", "zh": "选择图标"},
    "none_color": {"en": "None", "ru": "Без цвета", "es": "Ninguno", "pt": "Nenhuma", "de": "Keine", "fr": "Aucune", "zh": "无"},
    "shortcut_btn": {"en": "Desktop shortcut", "ru": "Ярлык на стол", "es": "Acceso directo", "pt": "Atalho", "de": "Verknüpfung", "fr": "Raccourci", "zh": "桌面快捷方式"},
    "updating": {"en": "Downloading the update…", "ru": "Загружаю обновление…", "es": "Descargando la actualización…", "pt": "Baixando a atualização…", "de": "Update wird heruntergeladen…", "fr": "Téléchargement de la mise à jour…", "zh": "正在下载更新…"},
    "installing": {"en": "Installing… the app will restart.", "ru": "Устанавливаю… приложение перезапустится.", "es": "Instalando… la app se reiniciará.", "pt": "Instalando… o app vai reiniciar.", "de": "Installation… die App startet neu.", "fr": "Installation… l’app va redémarrer.", "zh": "正在安装……应用将重启。"},
}


def T(key, *args):
    s = STRINGS.get(key, {})
    txt = s.get(LANG) or s.get("en") or key
    return (txt % args) if args else txt


def detect_lang():
    try:
        lid = kernel32.GetUserDefaultUILanguage() & 0x3FF
        return {0x09: "en", 0x19: "ru", 0x0A: "es", 0x16: "pt",
                0x07: "de", 0x0C: "fr", 0x04: "zh"}.get(lid, "en")
    except Exception:
        return "en"


def enable_dark_menus():
    """Тёмные системные контекстные меню (immersive dark mode, Win10 1903+/Win11)."""
    try:
        if sys.getwindowsversion().build < 18362:
            return
        ux = ctypes.WinDLL("uxtheme")
        set_mode = ux[135]            # SetPreferredAppMode / AllowDarkModeForApp
        set_mode.restype = ctypes.c_int
        set_mode.argtypes = [ctypes.c_int]
        set_mode(2)                   # ForceDark
        ux[136]()                     # FlushMenuThemes
    except Exception as ex:
        log("dark menus failed: %r" % ex)


def version_tuple(v):
    out = []
    for part in str(v).split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)


def fetch_latest_release():
    """(tag, setup_asset_url) последнего релиза GitHub или (None, None)."""
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO,
            headers={"User-Agent": "HDContainer", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = (data.get("tag_name") or "").lstrip("vV")
        url = None
        for a in data.get("assets", []):
            name = (a.get("name") or "").lower()
            if name.endswith("setup.exe"):
                url = a.get("browser_download_url")
                break
        if url is None:
            for a in data.get("assets", []):
                if (a.get("name") or "").lower().endswith(".exe"):
                    url = a.get("browser_download_url")
                    break
        return tag, url
    except Exception as ex:
        log("update check failed: %r" % ex)
        return None, None

# ---------------------------------------------------------------------------
# Win32 константы
# ---------------------------------------------------------------------------
GWL_STYLE = -16
GWL_EXSTYLE = -20
GWLP_HWNDPARENT = -8
GWLP_WNDPROC = -4
GW_OWNER = 4
GA_ROOT = 2

WS_POPUP        = 0x80000000
WS_CLIPCHILDREN = 0x02000000
WS_CHILD        = 0x40000000
WS_MINIMIZEBOX  = 0x00020000
WS_SYSMENU      = 0x00080000

WS_EX_APPWINDOW  = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED    = 0x00080000
WS_EX_TRANSPARENT = 0x00000020

LWA_ALPHA = 0x02

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_NOOWNERZORDER = 0x0200

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_MINIMIZE = 6
SW_SHOWNA = 8
SW_RESTORE = 9

HWND_TOP = 0
SPI_GETWORKAREA = 0x0030

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_SETICON = 0x0080
WM_COMMAND = 0x0111
WM_COPYDATA = 0x004A

CREATE_NO_WINDOW = 0x08000000
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_APP = 0x8000
WM_TRAY = WM_APP + 1

ICON_SMALL = 0
ICON_BIG = 1
IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
LR_SHARED = 0x8000

# меню
MF_STRING = 0x0000
MF_POPUP = 0x0010
MF_SEPARATOR = 0x0800
MF_GRAYED = 0x0001
MF_CHECKED = 0x0008
WM_SYSCOMMAND = 0x0112
IDM_EDIT = 0x9000          # своя команда меню окна «Редактировать контейнер»
TPM_LEFTALIGN = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

# трей
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

HWND = wintypes.HWND
LONG = wintypes.LONG
DWORD = wintypes.DWORD
UINT = wintypes.UINT
BOOL = wintypes.BOOL
LPCWSTR = wintypes.LPCWSTR
LPWSTR = wintypes.LPWSTR
HICON = wintypes.HICON
HMENU = wintypes.HMENU
HINSTANCE = wintypes.HINSTANCE
HANDLE = wintypes.HANDLE
POINT = wintypes.POINT

WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)


# ---------------------------------------------------------------------------
# Структуры
# ---------------------------------------------------------------------------
class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", UINT), ("style", UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", HINSTANCE),
                ("hIcon", HICON), ("hCursor", HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", LPCWSTR),
                ("lpszClassName", LPCWSTR), ("hIconSm", HICON)]


class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [("dwData", ctypes.c_size_t), ("cbData", DWORD), ("lpData", ctypes.c_void_p)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", DWORD), ("biWidth", LONG), ("biHeight", LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", DWORD), ("biSizeImage", DWORD),
                ("biXPelsPerMeter", LONG), ("biYPelsPerMeter", LONG),
                ("biClrUsed", DWORD), ("biClrImportant", DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", DWORD * 3)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", LONG), ("bmWidth", LONG), ("bmHeight", LONG),
                ("bmWidthBytes", LONG), ("bmPlanes", wintypes.WORD),
                ("bmBitsPixel", wintypes.WORD), ("bmBits", ctypes.c_void_p)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", BOOL), ("xHotspot", DWORD), ("yHotspot", DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, d1, d2, d3, d4):
        super().__init__()
        self.Data1, self.Data2, self.Data3 = d1, d2, d3
        for i, b in enumerate(d4):
            self.Data4[i] = b


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.USHORT),
                ("r2", wintypes.USHORT), ("r3", wintypes.USHORT),
                ("pwszVal", ctypes.c_void_p), ("pad", ctypes.c_byte * 8)]


VT_LPWSTR = 31


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("hWnd", HWND), ("uID", UINT),
                ("uFlags", UINT), ("uCallbackMessage", UINT), ("hIcon", HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", DWORD), ("dwStateMask", DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", HICON)]


# ---------------------------------------------------------------------------
# Объявления функций
# ---------------------------------------------------------------------------
def _decl(fn, restype, argtypes):
    fn.restype = restype
    fn.argtypes = argtypes


_decl(user32.GetAncestor, HWND, [HWND, UINT])
_decl(user32.GetWindow, HWND, [HWND, UINT])
_decl(user32.GetWindowLongW, LONG, [HWND, ctypes.c_int])
_decl(user32.SetWindowLongW, LONG, [HWND, ctypes.c_int, LONG])
_decl(user32.SetWindowPos, BOOL,
      [HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, UINT])
_decl(user32.GetWindowRect, BOOL, [HWND, ctypes.POINTER(wintypes.RECT)])
_decl(user32.IsWindow, BOOL, [HWND])
_decl(user32.IsWindowVisible, BOOL, [HWND])
_decl(user32.ShowWindow, BOOL, [HWND, ctypes.c_int])
_decl(user32.SetForegroundWindow, BOOL, [HWND])
_decl(user32.GetForegroundWindow, HWND, [])
_decl(user32.GetWindowTextLengthW, ctypes.c_int, [HWND])
_decl(user32.GetWindowTextW, ctypes.c_int, [HWND, LPWSTR, ctypes.c_int])
_decl(user32.GetClassNameW, ctypes.c_int, [HWND, LPWSTR, ctypes.c_int])
_decl(user32.GetWindowThreadProcessId, DWORD, [HWND, ctypes.POINTER(DWORD)])
_decl(user32.SystemParametersInfoW, BOOL, [UINT, UINT, ctypes.c_void_p, UINT])
_decl(user32.GetSystemMetrics, ctypes.c_int, [ctypes.c_int])
_decl(user32.SetLayeredWindowAttributes, BOOL, [HWND, DWORD, ctypes.c_ubyte, DWORD])
_decl(user32.SetWindowTextW, BOOL, [HWND, LPCWSTR])
_decl(user32.DestroyWindow, BOOL, [HWND])
_decl(user32.DefWindowProcW, LRESULT, [HWND, UINT, WPARAM, LPARAM])
_decl(user32.CallWindowProcW, LRESULT, [ctypes.c_void_p, HWND, UINT, WPARAM, LPARAM])
_decl(user32.SendMessageW, LRESULT, [HWND, UINT, WPARAM, LPARAM])
_decl(user32.PostMessageW, BOOL, [HWND, UINT, WPARAM, LPARAM])
_decl(user32.RegisterClassExW, wintypes.ATOM, [ctypes.POINTER(WNDCLASSEXW)])
_decl(user32.CreateWindowExW, HWND,
      [DWORD, LPCWSTR, LPCWSTR, DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
       ctypes.c_int, HWND, HMENU, HINSTANCE, ctypes.c_void_p])
_decl(user32.LoadImageW, HANDLE, [HINSTANCE, LPCWSTR, UINT, ctypes.c_int, ctypes.c_int, UINT])
_decl(user32.LoadIconW, HICON, [HINSTANCE, LPCWSTR])
_decl(user32.CreatePopupMenu, HMENU, [])
_decl(user32.DestroyMenu, BOOL, [HMENU])
_decl(user32.AppendMenuW, BOOL, [HMENU, UINT, ctypes.c_size_t, LPCWSTR])
_decl(user32.GetSystemMenu, HMENU, [HWND, BOOL])
_decl(user32.TrackPopupMenu, BOOL,
      [HMENU, UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, HWND, ctypes.c_void_p])
_decl(user32.GetCursorPos, BOOL, [ctypes.POINTER(POINT)])
_decl(user32.EnumWindows, BOOL, [WNDPROC, LPARAM])
_decl(user32.FindWindowW, HWND, [LPCWSTR, LPCWSTR])

_decl(kernel32.GetModuleHandleW, HINSTANCE, [LPCWSTR])
_decl(kernel32.GetUserDefaultUILanguage, wintypes.WORD, [])
_decl(kernel32.OpenProcess, HANDLE, [DWORD, BOOL, DWORD])
_decl(kernel32.QueryFullProcessImageNameW, BOOL,
      [HANDLE, DWORD, LPWSTR, ctypes.POINTER(DWORD)])
_decl(kernel32.CloseHandle, BOOL, [HANDLE])

_decl(shell32.Shell_NotifyIconW, BOOL, [DWORD, ctypes.POINTER(NOTIFYICONDATAW)])

_decl(user32.IsIconic, BOOL, [HWND])
_decl(user32.GetDC, wintypes.HDC, [HWND])
_decl(user32.ReleaseDC, ctypes.c_int, [HWND, wintypes.HDC])
_decl(user32.PrintWindow, BOOL, [HWND, wintypes.HDC, UINT])
_decl(user32.GetIconInfo, BOOL, [HICON, ctypes.POINTER(ICONINFO)])
_decl(user32.CreateIconIndirect, HICON, [ctypes.POINTER(ICONINFO)])
_decl(user32.DrawIconEx, BOOL,
      [wintypes.HDC, ctypes.c_int, ctypes.c_int, HICON, ctypes.c_int, ctypes.c_int,
       UINT, wintypes.HBRUSH, UINT])

_decl(gdi32.CreateCompatibleDC, wintypes.HDC, [wintypes.HDC])
_decl(gdi32.DeleteDC, BOOL, [wintypes.HDC])
_decl(gdi32.CreateCompatibleBitmap, wintypes.HBITMAP, [wintypes.HDC, ctypes.c_int, ctypes.c_int])
_decl(gdi32.CreateDIBSection, wintypes.HBITMAP,
      [wintypes.HDC, ctypes.POINTER(BITMAPINFO), UINT,
       ctypes.POINTER(ctypes.c_void_p), HANDLE, DWORD])
_decl(gdi32.CreateBitmap, wintypes.HBITMAP,
      [ctypes.c_int, ctypes.c_int, UINT, UINT, ctypes.c_void_p])
_decl(gdi32.SelectObject, wintypes.HGDIOBJ, [wintypes.HDC, wintypes.HGDIOBJ])
_decl(gdi32.DeleteObject, BOOL, [wintypes.HGDIOBJ])
_decl(gdi32.CreateRectRgn, wintypes.HRGN, [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int])
_decl(user32.SetWindowRgn, ctypes.c_int, [HWND, wintypes.HRGN, BOOL])
_decl(gdi32.StretchBlt, BOOL,
      [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
       wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, DWORD])
_decl(gdi32.SetStretchBltMode, ctypes.c_int, [wintypes.HDC, ctypes.c_int])
_decl(gdi32.GetDIBits, ctypes.c_int,
      [wintypes.HDC, wintypes.HBITMAP, UINT, UINT, ctypes.c_void_p,
       ctypes.POINTER(BITMAPINFO), UINT])
_decl(gdi32.GetObjectW, ctypes.c_int, [wintypes.HGDIOBJ, ctypes.c_int, ctypes.c_void_p])

_decl(shell32.SHGetPropertyStoreForWindow, ctypes.c_long,
      [HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)])
_decl(ole32.PropVariantClear, ctypes.c_long, [ctypes.POINTER(PROPVARIANT)])
ole32.CoTaskMemAlloc.restype = ctypes.c_void_p
ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]

SRCCOPY = 0x00CC0020
HALFTONE = 4
DIB_RGB_COLORS = 0
BI_RGB = 0
PW_RENDERFULLCONTENT = 2
DI_NORMAL = 3

IID_IPropertyStore = GUID(0x886D8EEB, 0x8CF2, 0x4446,
                          (0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99))
PKEY_AppUserModel_ID = PROPERTYKEY(
    GUID(0x9F4C2855, 0x9F79, 0x4B39,
         (0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3)), 5)

# системные окна оболочки — НИКОГДА не трогаем (особенно рабочий стол Progman:
# сделать его членом контейнера = краш)
SHELL_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "TrayNotifyWnd", "DV2ControlHost", "Windows.UI.Core.CoreWindow",
    "ForegroundStaging", "XamlExplorerHostIslandWindow", "MSCTFIME UI",
    "Default IME", "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland",
}

# окна проводника-папки — для них запоминаем ПУТЬ, чтобы открыть ту же папку
FOLDER_CLASSES = {"CabinetWClass", "ExploreWClass"}

# SetWindowLongPtrW нужен на 64-бит, чтобы не обрезать указатели (HWND/WNDPROC)
_SetWindowLongPtr = (user32.SetWindowLongPtrW
                     if hasattr(user32, "SetWindowLongPtrW") else user32.SetWindowLongW)
_SetWindowLongPtr.restype = ctypes.c_void_p
_SetWindowLongPtr.argtypes = [HWND, ctypes.c_int, ctypes.c_void_p]

ENUMPROC = WNDPROC  # для EnumWindows используем BOOL(HWND,LPARAM)
_EnumProc = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)
user32.EnumWindows.argtypes = [_EnumProc, LPARAM]

try:
    dwmapi = ctypes.WinDLL("dwmapi")
    _decl(dwmapi.DwmGetWindowAttribute, ctypes.c_long,
          [HWND, DWORD, ctypes.c_void_p, DWORD])
    DWMWA_CLOAKED = 14
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
except Exception:
    dwmapi = None


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------
def get_style(h):
    return user32.GetWindowLongW(h, GWL_STYLE) & 0xFFFFFFFF


def set_owner(hwnd, owner_hwnd):
    _SetWindowLongPtr(hwnd, GWLP_HWNDPARENT, ctypes.c_void_p(owner_hwnd or 0))


def get_window_text(h):
    n = user32.GetWindowTextLengthW(h)
    if n <= 0:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, b, n + 1)
    return b.value


def get_class_name(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def get_pid(h):
    pid = DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return pid.value


def exe_for_hwnd(h):
    pid = get_pid(h)
    if not pid:
        return ""
    hp = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not hp:
        return ""
    try:
        size = DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(hp)
    return ""


def is_cloaked(h):
    if not dwmapi:
        return False
    val = ctypes.c_int(0)
    r = dwmapi.DwmGetWindowAttribute(h, DWMWA_CLOAKED, ctypes.byref(val),
                                     ctypes.sizeof(val))
    return r == 0 and val.value != 0


def get_rect(h):
    r = wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return r


def frame_insets(h):
    """Невидимые поля окна (тень/борта DWM): GetWindowRect больше реально видимой
    рамки. Возвращаем (left,top,right,bottom) — насколько визуальная рамка ужата
    внутрь оконного прямоугольника. Нужно, чтобы класть окна впритык, как Win+→."""
    if not dwmapi:
        return (0, 0, 0, 0)
    fb = wintypes.RECT()
    res = dwmapi.DwmGetWindowAttribute(h, DWMWA_EXTENDED_FRAME_BOUNDS,
                                       ctypes.byref(fb), ctypes.sizeof(fb))
    if res != 0:
        return (0, 0, 0, 0)
    wr = get_rect(h)
    li = fb.left - wr.left
    ti = fb.top - wr.top
    ri = wr.right - fb.right
    bi = wr.bottom - fb.bottom
    # подстраховка от мусора
    if min(li, ti, ri, bi) < 0 or max(li, ti, ri, bi) > 40:
        return (0, 0, 0, 0)
    return (li, ti, ri, bi)


def virtual_screen():
    g = user32.GetSystemMetrics
    return g(SM_XVIRTUALSCREEN), g(SM_YVIRTUALSCREEN), \
        g(SM_CXVIRTUALSCREEN), g(SM_CYVIRTUALSCREEN)


def work_area():
    r = wintypes.RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _layout_rects(kind, n):
    """Раскладка ВИДА `kind` для n окон -> список (x,y,w,h) в долях 0..1.
    Вид фиксирован, поэтому раскладку можно «переразложить» под любое число окон
    (закрыл одно из трёх колонок |_|_|_| -> два встают |__|__|)."""
    if n <= 0:
        return []
    if n == 1:
        return [(0.0, 0.0, 1.0, 1.0)]
    if kind == "rows":
        return [(0.0, i / n, 1.0, 1.0 / n) for i in range(n)]
    if kind == "grid":
        c = int(n ** 0.5)
        if c * c < n:
            c += 1
        rr = (n + c - 1) // c
        return [((i % c) / c, (i // c) / rr, 1.0 / c, 1.0 / rr) for i in range(n)]
    if kind == "master":
        k = n - 1
        return [(0.0, 0.0, 0.6, 1.0)] + [(0.6, i / k, 0.4, 1.0 / k) for i in range(k)]
    # "cols" (по умолчанию): n равных колонок
    return [(i / n, 0.0, 1.0 / n, 1.0) for i in range(n)]


def layouts_for(n):
    """Пресеты раскладки для n окон: список (kind, rects)."""
    if n <= 0:
        return []
    if n == 1:
        return [("cols", _layout_rects("cols", 1))]
    kinds = ["cols", "rows"]
    if n >= 3:
        kinds.append("master")
    if n >= 4:
        kinds.append("grid")
    return [(k, _layout_rects(k, n)) for k in kinds]


def load_icon_file(path, size=256):
    # грузим крупно (256) для чёткости на таскбаре; без LR_SHARED (он кэширует хэндл)
    if path and os.path.exists(path):
        h = user32.LoadImageW(None, path, IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if h:
            return h
    return 0


def hex_rgb(s):
    try:
        s = (s or "").lstrip("#")
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return None


def mix(hexc, other, f):
    """Смешать hexc -> other на долю f (0..1). Для мягкого «свечения» к фону."""
    a = hex_rgb(hexc) or (255, 255, 255)
    b = hex_rgb(other) or (0, 0, 0)
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, int(a[i] + (b[i] - a[i]) * f))) for i in range(3))


def load_icon():
    return load_icon_file(_ICON) or \
        user32.LoadIconW(None, ctypes.cast(ctypes.c_void_p(IDI_APPLICATION), LPCWSTR))


def load_tray_icon():
    # грузим точно в размер значка трея -> чётко и не мельче соседних
    cx = user32.GetSystemMetrics(49) or 16   # SM_CXSMICON
    cy = user32.GetSystemMetrics(50) or 16   # SM_CYSMICON
    if os.path.exists(_ICON):
        h = user32.LoadImageW(None, _ICON, IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
        if h:
            return h
    return load_icon()


def capture_thumb(hwnd, tw=260, th=150):
    """PPM-превью окна (как в Alt+Tab) или None. Чистый GDI, без Pillow."""
    scr = src = dst = hbsrc = hbdst = None
    o1 = o2 = None
    try:
        if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
            return None                       # свёрнутое не снимаем (рискованно/бессмысленно)
        r = get_rect(hwnd)
        w, h = r.right - r.left, r.bottom - r.top
        vx, vy, vw, vh = virtual_screen()
        if w <= 0 or h <= 0 or w > vw + 64 or h > vh + 64:
            return None
        scr = user32.GetDC(0)
        src = gdi32.CreateCompatibleDC(scr)
        dst = gdi32.CreateCompatibleDC(scr)
        hbsrc = gdi32.CreateCompatibleBitmap(scr, w, h)
        hbdst = gdi32.CreateCompatibleBitmap(scr, tw, th)
        if not (scr and src and dst and hbsrc and hbdst):
            return None
        o1 = gdi32.SelectObject(src, hbsrc)
        user32.PrintWindow(hwnd, src, PW_RENDERFULLCONTENT)
        o2 = gdi32.SelectObject(dst, hbdst)
        gdi32.SetStretchBltMode(dst, HALFTONE)
        gdi32.StretchBlt(dst, 0, 0, tw, th, src, 0, 0, w, h, SRCCOPY)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = tw
        bi.bmiHeader.biHeight = -th          # top-down
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = BI_RGB
        buf = (ctypes.c_char * (tw * th * 4))()
        if not gdi32.GetDIBits(dst, hbdst, 0, th, buf, ctypes.byref(bi), DIB_RGB_COLORS):
            return None
        raw = bytes(buf)                      # BGRA
        rgb = bytearray(tw * th * 3)
        rgb[0::3] = raw[2::4]
        rgb[1::3] = raw[1::4]
        rgb[2::3] = raw[0::4]
        return b"P6\n%d %d\n255\n" % (tw, th) + bytes(rgb)
    except Exception as ex:
        log("thumb failed hwnd=%s: %r" % (hwnd, ex))
        return None
    finally:
        try:
            if o1:
                gdi32.SelectObject(src, o1)
            if o2:
                gdi32.SelectObject(dst, o2)
            if hbsrc:
                gdi32.DeleteObject(hbsrc)
            if hbdst:
                gdi32.DeleteObject(hbdst)
            if src:
                gdi32.DeleteDC(src)
            if dst:
                gdi32.DeleteDC(dst)
            if scr:
                user32.ReleaseDC(0, scr)
        except Exception:
            pass


def recolor_icon(base_hicon, rgb):
    """Перекрасить «синюю» левую панель логотипа в заданный цвет (сохраняя тени/градиент).
    Белые панели и тёмная плитка не трогаются. HICON или 0."""
    if not base_hicon or not rgb:
        return 0
    ii = ICONINFO()
    if not user32.GetIconInfo(base_hicon, ctypes.byref(ii)):
        log("recolor: GetIconInfo failed")
        return 0
    if not ii.hbmColor:
        # моно-иконка без цветного битмапа -> GetDIBits по NULL = краш; пропускаем
        log("recolor: no hbmColor (mono icon)")
        try:
            if ii.hbmMask:
                gdi32.DeleteObject(ii.hbmMask)
        except Exception:
            pass
        return 0
    memdc = dib = mask = 0
    try:
        bm = BITMAP()
        gdi32.GetObjectW(ii.hbmColor, ctypes.sizeof(BITMAP), ctypes.byref(bm))
        w = bm.bmWidth or 32
        h = bm.bmHeight or 32
        scr = user32.GetDC(0)
        memdc = gdi32.CreateCompatibleDC(scr)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h            # top-down
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        dib = gdi32.CreateDIBSection(memdc, ctypes.byref(bi), DIB_RGB_COLORS,
                                     ctypes.byref(bits), None, 0)
        user32.ReleaseDC(0, scr)
        if not dib or not bits:
            return 0
        # скопировать пиксели базовой иконки (с альфой) в DIB
        gdi32.GetDIBits(memdc, ii.hbmColor, 0, h, bits, ctypes.byref(bi), DIB_RGB_COLORS)
        ptr = ctypes.cast(bits, ctypes.POINTER(ctypes.c_ubyte))
        tr, tg, tb = rgb
        ref = 132.0          # яркость дефолтной синей панели (#4c8bf5)
        changed = 0
        total = w * h
        for p in range(total):
            i = p * 4
            b = ptr[i]
            g = ptr[i + 1]
            r = ptr[i + 2]
            # «синий» пиксель панели: синева доминирует (белые/тёмные не трогаем)
            if b > r * 1.25 and b > g * 1.05 and b > 80:
                f = (0.299 * r + 0.587 * g + 0.114 * b) / ref   # сохранить тень/градиент
                nb = int(tb * f)
                ng = int(tg * f)
                nr = int(tr * f)
                ptr[i] = nb if nb < 255 else 255
                ptr[i + 1] = ng if ng < 255 else 255
                ptr[i + 2] = nr if nr < 255 else 255
                changed += 1
        mask = gdi32.CreateBitmap(w, h, 1, 1, None)
        ii2 = ICONINFO()
        ii2.fIcon = 1
        ii2.hbmMask = mask
        ii2.hbmColor = dib
        res = user32.CreateIconIndirect(ctypes.byref(ii2)) or 0
        log("recolor: w=%d h=%d changed=%d -> hicon=%s" % (w, h, changed, res))
        return res
    except Exception as ex:
        log("recolor icon failed: %r" % ex)
        return 0
    finally:
        try:
            if ii.hbmColor:
                gdi32.DeleteObject(ii.hbmColor)
            if ii.hbmMask:
                gdi32.DeleteObject(ii.hbmMask)
            if dib:
                gdi32.DeleteObject(dib)
            if mask:
                gdi32.DeleteObject(mask)
            if memdc:
                gdi32.DeleteDC(memdc)
        except Exception:
            pass


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "HDContainer"


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            v, _t = winreg.QueryValueEx(k, _RUN_NAME)
            return bool(v)
    except OSError:
        return False


def set_autostart(on):
    try:
        exe = os.path.abspath(sys.argv[0])
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            if on:
                winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, '"%s"' % exe)
            else:
                try:
                    winreg.DeleteValue(k, _RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as ex:
        log("autostart failed: %r" % ex)
        return False


def folder_path_for_hwnd(hwnd):
    """Путь папки, открытой в окне проводника (через Shell.Application). '' если не вышло."""
    try:
        ps = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
              "$ErrorActionPreference='SilentlyContinue';"
              "$sh=New-Object -ComObject Shell.Application;"
              "foreach($w in $sh.Windows()){"
              "  try{ if([int64]$w.HWND -eq %dL){ $w.Document.Folder.Self.Path; break } }catch{} }"
              % int(hwnd))
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, timeout=12, creationflags=CREATE_NO_WINDOW)
        path = out.stdout.decode("utf-8", "replace").strip()
        return path if path and os.path.isdir(path) else ""
    except Exception as ex:
        log("folder_path failed: %r" % ex)
        return ""


def set_app_id(hwnd, app_id):
    """Уникальный AppUserModelID окну -> Windows НЕ группирует хосты в одну кнопку
    на таскбаре, у каждого контейнера своя кнопка со своей иконкой."""
    try:
        store = ctypes.c_void_p()
        hr = shell32.SHGetPropertyStoreForWindow(
            hwnd, ctypes.byref(IID_IPropertyStore), ctypes.byref(store))
        if hr != 0 or not store:
            return
        try:
            n = (len(app_id) + 1) * 2
            mem = ole32.CoTaskMemAlloc(n)
            if not mem:
                return
            ctypes.memmove(mem, ctypes.create_unicode_buffer(app_id), n)
            pv = PROPVARIANT()
            pv.vt = VT_LPWSTR
            pv.pwszVal = mem
            vtbl = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            set_value = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                ctypes.POINTER(PROPVARIANT))(vtbl[6])
            commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[7])
            set_value(store, ctypes.byref(PKEY_AppUserModel_ID), ctypes.byref(pv))
            commit(store)
            ole32.PropVariantClear(ctypes.byref(pv))     # освободит mem
        finally:
            release = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(
                ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents[2])
            release(store)
    except Exception as ex:
        log("set_app_id failed: %r" % ex)


def send_copydata(hwnd, text):
    """Передать строку другому экземпляру через WM_COPYDATA."""
    buf = ctypes.create_unicode_buffer(text)
    cds = COPYDATASTRUCT()
    cds.dwData = 1
    cds.cbData = (len(text) + 1) * ctypes.sizeof(wintypes.WCHAR)
    cds.lpData = ctypes.cast(buf, ctypes.c_void_p)
    user32.SendMessageW(hwnd, WM_COPYDATA, 0, ctypes.addressof(cds))


# ---------------------------------------------------------------------------
# Модель управляемого (owned) окна
# ---------------------------------------------------------------------------
class Managed:
    __slots__ = ("hwnd", "title", "o_owner", "o_style", "o_exstyle", "o_rect",
                 "sig", "min_detached", "group_hidden")

    def __init__(self, hwnd, title):
        self.hwnd = hwnd
        self.title = title
        self.o_owner = 0
        self.o_style = 0
        self.o_exstyle = 0
        self.o_rect = (0, 0, 0, 0)
        self.sig = None
        self.min_detached = False
        self.group_hidden = False


# ---------------------------------------------------------------------------
# Контейнер
# ---------------------------------------------------------------------------
class Container:
    def __init__(self, name, title=None, apps=None, icon=None, color=None):
        self.name = name
        self.title = title or name
        self.apps = list(apps or [])      # [{"exe","title","cls","rect"}]
        self.icon = icon                  # путь к .ico контейнера (или None)
        self.color = color                # "#rrggbb" цветная метка (или None)
        self.layout = None                # вид раскладки (cols/rows/grid/master)
        self.members = {}                 # hwnd -> Managed
        self.active = False
        self.host_hwnd = 0
        self.hicon = 0                    # сгенерированный HICON (кэш)

    # --- сериализация ---
    def to_dict(self):
        return {"name": self.name, "title": self.title, "apps": self.apps,
                "icon": self.icon, "color": self.color, "layout": self.layout}

    @staticmethod
    def from_dict(d):
        c = Container(d.get("name", "Контейнер"), d.get("title"),
                      d.get("apps"), d.get("icon"), d.get("color"))
        c.layout = d.get("layout")
        return c

    # --- подписи приложений (для переоткрытия + восстановления позиций) ---
    def _find_sig(self, exe, title):
        for s in self.apps:
            if s.get("exe") == exe and s.get("title") == title:
                return s
        return None

    def add_app_sig(self, hwnd):
        exe = exe_for_hwnd(hwnd)
        title = get_window_text(hwnd)
        r = get_rect(hwnd)
        rect = [r.left, r.top, r.right - r.left, r.bottom - r.top]
        sig = self._find_sig(exe, title)
        if sig is None:
            sig = {"exe": exe, "title": title,
                   "cls": get_class_name(hwnd), "rect": rect}
            self.apps.append(sig)
        else:
            sig["rect"] = rect
        return sig

    def sync_app_rects(self):
        # держим в подписях актуальные позиции живых окон ("последнее состояние").
        # подпись привязана к окну (m.sig) -> смена заголовка не плодит дубли
        for m in list(self.members.values()):
            if not user32.IsWindow(m.hwnd):
                continue
            r = get_rect(m.hwnd)
            rect = [r.left, r.top, r.right - r.left, r.bottom - r.top]
            title = get_window_text(m.hwnd)
            if m.sig is not None:
                m.sig["rect"] = rect
                if title:
                    m.sig["title"] = title
            else:
                m.sig = self.add_app_sig(m.hwnd)

    # --- членство ---
    def attach(self, hwnd, host_hwnd, place_rect=None):
        if not user32.IsWindow(hwnd) or hwnd in self.members:
            return False
        user32.ShowWindow(hwnd, SW_RESTORE)
        m = Managed(hwnd, get_window_text(hwnd))
        m.o_owner = user32.GetWindow(hwnd, GW_OWNER) or 0
        m.o_style = get_style(hwnd)
        m.o_exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        r = get_rect(hwnd)
        m.o_rect = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        # сделать owned-окном хоста; toggle видимости форсирует пересчёт таскбара.
        user32.ShowWindow(hwnd, SW_HIDE)
        set_owner(hwnd, host_hwnd)
        if place_rect and len(place_rect) == 4:
            l, t, w, hh = place_rect
            user32.SetWindowPos(hwnd, HWND_TOP, int(l), int(t), int(w), int(hh),
                                SWP_NOACTIVATE | SWP_NOOWNERZORDER)
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.SetForegroundWindow(hwnd)
        self.members[hwnd] = m
        m.sig = self.add_app_sig(hwnd)
        cls = get_class_name(hwnd)
        if cls in FOLDER_CLASSES and not m.sig.get("folder"):
            path = folder_path_for_hwnd(hwnd)   # запомнить путь папки для переоткрытия
            if path:
                m.sig["folder"] = path
        log("ATTACH '%s' <- hwnd=%s cls=%r '%s'" % (
            self.name, hwnd, cls, m.title[:40]))
        return True

    def detach(self, hwnd):
        m = self.members.pop(hwnd, None)
        if m:
            self._restore(m)

    def detach_all(self):
        for m in list(self.members.values()):
            self._restore(m)
        self.members.clear()

    def _restore(self, m):
        h = m.hwnd
        if not user32.IsWindow(h):
            return
        try:
            set_owner(h, m.o_owner)                # вернуть исходного владельца
            # СБРОСИТЬ КЭШ владения: без этого система может ещё считать окно
            # принадлежащим хосту, и DestroyWindow(host) уничтожит его вместе с
            # хостом (окна приложения «пропадают», процесс жив) — это и был баг.
            user32.SetWindowPos(h, None, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER |
                                SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_FRAMECHANGED)
            if m.group_hidden:
                # это окно МЫ прятали при сворачивании группы -> обязательно вернуть
                user32.ShowWindow(h, SW_SHOWNA)
                m.group_hidden = False
            elif user32.IsWindowVisible(h) and not user32.IsIconic(h):
                # окно, спрятанное приложением в трей (Telegram и т.п.), НЕ трогаем —
                # иначе остаётся «призрачная» кнопка таскбара без видимого окна
                user32.ShowWindow(h, SW_HIDE)
                user32.ShowWindow(h, SW_SHOWNA)    # обновить таскбар, без кражи фокуса
        except Exception as ex:
            log("restore failed hwnd=%s: %r" % (h, ex))

    def prune(self):
        gone = [h for h in self.members if not user32.IsWindow(h)]
        for h in gone:
            self.members.pop(h, None)
        return bool(gone)


# ---------------------------------------------------------------------------
# Трей-приложение
# ---------------------------------------------------------------------------
HOST_CLASS = "WCInvisibleHost"


class TrayApp:
    def __init__(self, launch_name=None):
        self.my_pid = os.getpid()
        self.containers = []          # list[Container]
        self.current = None           # Container | None
        self.pending = []             # отложенный подхват после запуска приложения
        self._reassert = []           # повторная установка позиции только что добавленных окон
        self._menu_actions = {}
        self._pending_update = None   # (tag, url), выставляется фоновым потоком
        self.settings = load_settings()
        global LANG
        LANG = self.settings.get("lang") or detect_lang()
        self.hinst = kernel32.GetModuleHandleW(None)
        self.hicon = load_icon()
        self.tray_hicon = load_tray_icon()
        self._host_class_atom = 0
        enable_dark_menus()           # тёмное контекстное меню в стиле приложения

        # скрытый tk-root: единственный насос сообщений + источник диалогов.
        # Заголовок = IPC-маяк: по нему другой экземпляр нас находит (FindWindow).
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(IPC_TITLE)
        self.root.report_callback_exception = self._tk_exc
        self.root.update_idletasks()
        self.msg_hwnd = user32.GetAncestor(self.root.winfo_id(), GA_ROOT)

        # держим ссылки на колбэки, иначе GC их убьёт -> краш
        self._tray_wndproc = WNDPROC(self._on_message)
        self._host_wndproc = WNDPROC(self._host_proc)
        self._enum_cb = _EnumProc(self._enum_collect)
        self._enum_acc = []

        # подменяем wndproc нашего же окна, чтобы ловить сообщения трея
        self._old_proc = _SetWindowLongPtr(
            self.msg_hwnd, GWLP_WNDPROC,
            ctypes.cast(self._tray_wndproc, ctypes.c_void_p))
        self._old_proc = ctypes.cast(self._old_proc, ctypes.c_void_p)

        self._run_recovery()
        self._load_containers()
        self._add_tray()

        # авто-восстановление контейнеров после краха: контейнеры, чьи окна выжили,
        # активируем заново — _activate пере-адаптирует выжившие окна (по сигнатуре)
        # и перезапустит только то, что не выжило. Никаких ручных действий.
        for _nm in getattr(self, "_recover_pending", []):
            _c = next((x for x in self.containers if x.name == _nm), None)
            if _c and not _c.active:
                self.root.after(1000, lambda c=_c: self._activate(c))

        # внешний WM_CLOSE (напр. при обновлении) -> штатное завершение,
        # которое снимает владение со всех окон (а не убивает их)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.after(700, self._poll)
        self.root.after(300, self._watch)

        # запуск по ярлыку: --launch "<имя>" -> поднять этот контейнер
        if launch_name:
            self.root.after(400, lambda: self._activate_by_name(launch_name))

        # автообновление: первая проверка через 2.5с, затем периодически
        self.root.after(2500, self._update_tick)

    # ===================================================================
    #  Оконные процедуры
    # ===================================================================
    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            low = lparam & 0xFFFF
            if low in (WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
            return 0
        if msg == WM_COPYDATA:
            try:
                cds = COPYDATASTRUCT.from_address(int(lparam))
                name = ctypes.wstring_at(cds.lpData) if cds.lpData else ""
                if name:
                    self.root.after(1, lambda n=name: self._activate_by_name(n))
            except Exception as ex:
                log("copydata failed: %r" % ex)
            return 1
        return user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)

    def _activate_by_name(self, name):
        c = next((x for x in self.containers if x.name == name), None)
        if c:
            self._activate(c)
        else:
            log("activate_by_name: no container %r" % name)

    def _host_proc(self, hwnd, msg, wparam, lparam):
        # пункт «Редактировать контейнер» из меню окна (ПКМ по кнопке в таскбаре)
        if msg == WM_SYSCOMMAND and (wparam & 0xFFF0) == IDM_EDIT:
            for c in self.containers:
                if c.host_hwnd == hwnd:
                    self.root.after(1, lambda c=c: self._edit_container(c))
                    break
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _tk_exc(self, exc, val, tb):
        log("TK EXC:\n" + "".join(traceback.format_exception(exc, val, tb)))

    # ===================================================================
    #  Трей
    # ===================================================================
    def _nid(self, flags):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.msg_hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.tray_hicon
        nid.szTip = self._tip()
        return nid

    def _tip(self):
        act = sum(1 for c in self.containers if c.active)
        return T("tray_tip", act, len(self.containers))

    def _add_tray(self):
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(
            self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)))

    def _update_tray(self):
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(
            self._nid(NIF_TIP)))

    def _del_tray(self):
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(
            self._nid(NIF_MESSAGE)))

    # ===================================================================
    #  Меню
    # ===================================================================
    def _make_check(self, parent, checked, command, accent=None, bg=None):
        """Аккуратный чекбокс в стиле приложения (рисуем на canvas)."""
        accent = accent or COL_ACCENT
        bg = bg or COL_SURFACE
        cv = tk.Canvas(parent, width=20, height=20, bg=bg,
                       highlightthickness=0, bd=0, cursor="hand2")
        st = {"v": bool(checked)}

        def draw():
            cv.delete("all")
            if st["v"]:
                cv.create_rectangle(3, 3, 17, 17, fill=accent, outline=accent)
                cv.create_line(6, 10, 9, 13, fill="white", width=2)
                cv.create_line(9, 13, 14, 6, fill="white", width=2)
            else:
                cv.create_rectangle(3, 3, 17, 17, fill="", outline=COL_TEXT_DIM, width=1)

        def click(_e):
            st["v"] = not st["v"]
            draw()
            if command:
                command(st["v"])
        draw()
        cv.bind("<Button-1>", click)
        return cv, st, draw

    def _show_menu(self):
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        self._popup_at(pt.x, pt.y)

    def _popup_at(self, px, py):
        # кастомное минималистичное меню в стиле приложения: нативное меню не умеет
        # в одной строке и чекбокс (вкл/выкл), и клик по имени (открыть редактор)
        old = getattr(self, "_popup", None)
        if old:
            try:
                old.destroy()
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        self._popup = win
        self._popup_pos = (px, py)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=COL_BORDER)            # тонкая рамка по периметру
        frame = tk.Frame(win, bg=COL_SURFACE)
        frame.pack(padx=1, pady=1, fill="both", expand=True)

        def close():
            try:
                win.destroy()
            except Exception:
                pass
            if getattr(self, "_popup", None) is win:
                self._popup = None

        def act(fn):
            close()
            self.root.after(1, fn)

        W = 268
        for c in self.containers:
            self._popup_container_row(frame, c, W, close)
        if self.containers:
            tk.Frame(frame, bg=COL_BORDER, height=1).pack(fill="x", padx=12, pady=4)
        self._popup_action_row(frame, "＋", T("create_container"), W,
                               lambda: act(self._create_container))
        self._popup_action_row(frame, "⚙", T("settings"), W,
                               lambda: act(self._open_settings))
        self._popup_action_row(frame, "⏻", T("quit"), W,
                               lambda: act(self._quit))

        win.update_idletasks()
        w = frame.winfo_reqwidth() + 2
        h = frame.winfo_reqheight() + 2
        sx, sy, sw, sh = virtual_screen()
        x = max(sx, min(px, sx + sw - w - 4))
        y = py - h if py > sy + sh - h - 48 else py        # выше курсора у нижней грани
        y = max(sy, min(y, sy + sh - h - 4))
        win.geometry("%dx%d+%d+%d" % (w, h, x, y))
        user32.SetForegroundWindow(self.msg_hwnd)
        win.lift()
        win.focus_force()
        # связываем закрытие по потере фокуса с задержкой: у overrideredirect-окна
        # фокус «устаканивается» не сразу, иначе меню схлопнулось бы мгновенно
        win.after(250, lambda: win.winfo_exists() and win.bind("<FocusOut>",
                                                               lambda e: close()))
        win.bind("<Escape>", lambda e: close())

    def _popup_container_row(self, parent, c, width, close):
        row = tk.Frame(parent, bg=COL_SURFACE, height=32, width=width)
        row.pack(fill="x")
        row.pack_propagate(False)
        accent = c.color or COL_ACCENT

        def on_toggle(want, c=c):
            self.root.after(1, lambda: self._popup_toggle(c, want))
        chk, st, draw = self._make_check(row, c.active, on_toggle, accent=accent)
        chk.pack(side="left", padx=(14, 10))
        cnt = ("  (%d)" % len(c.members)) if c.active else ""
        name = tk.Label(row, text=c.name + cnt, bg=COL_SURFACE,
                        fg=(COL_TEXT if c.active else COL_TEXT_DIM),
                        font=FONT, cursor="hand2", anchor="w")
        name.pack(side="left", fill="x", expand=True, padx=(0, 14))

        def open_editor(_e=None, c=c):
            close()
            self.root.after(1, lambda: self._edit_container(c))
        name.bind("<Button-1>", open_editor)

        def enter(_e):
            row.configure(bg=COL_HOVER)
            name.configure(bg=COL_HOVER)
            chk.configure(bg=COL_HOVER)
        def leave(_e):
            row.configure(bg=COL_SURFACE)
            name.configure(bg=COL_SURFACE)
            chk.configure(bg=COL_SURFACE)
        for wdg in (row, name):
            wdg.bind("<Enter>", enter)
            wdg.bind("<Leave>", leave)

    def _popup_action_row(self, parent, glyph, text, width, command):
        row = tk.Frame(parent, bg=COL_SURFACE, height=32, width=width)
        row.pack(fill="x")
        row.pack_propagate(False)
        g = tk.Label(row, text=glyph, bg=COL_SURFACE, fg=COL_TEXT_DIM,
                     font=("Segoe UI Symbol", 12), width=2, cursor="hand2")
        g.pack(side="left", padx=(14, 8))
        t = tk.Label(row, text=text, bg=COL_SURFACE, fg=COL_TEXT, font=FONT,
                     anchor="w", cursor="hand2")
        t.pack(side="left", fill="x", expand=True, padx=(0, 14))
        widgets = (row, g, t)

        def enter(_e):
            for w in widgets:
                w.configure(bg=COL_HOVER)
        def leave(_e):
            for w in widgets:
                w.configure(bg=COL_SURFACE)
        for w in widgets:
            w.bind("<Enter>", enter)
            w.bind("<Leave>", leave)
            w.bind("<Button-1>", lambda e: command())

    def _popup_toggle(self, c, want_active):
        if want_active and not c.active:
            self._activate(c)
        elif not want_active and c.active:
            self._deactivate(c)
        # перерисовать меню на том же месте (обновить счётчик/состояние)
        if getattr(self, "_popup_pos", None):
            self._popup_at(*self._popup_pos)

    # ===================================================================
    #  Невидимый полноэкранный хост
    # ===================================================================
    def _ensure_class(self):
        if self._host_class_atom:
            return
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = self._host_wndproc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = self.hinst
        wc.hIcon = self.hicon
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = HOST_CLASS
        wc.hIconSm = self.hicon
        atom = user32.RegisterClassExW(ctypes.byref(wc))
        self._host_class_atom = atom or 1

    def _container_icon(self, c):
        # свой аватар используем КАК ЕСТЬ (без перекраски). Цвет-метка
        # перекрашивает только ДЕФОЛТНОЕ лого (его синюю панель).
        if not c.hicon:
            if c.icon and os.path.exists(c.icon):
                c.hicon = load_icon_file(c.icon) or 0     # свой аватар — как есть
            else:
                col = hex_rgb(c.color) if c.color else None
                if col:
                    c.hicon = recolor_icon(self.hicon, col) or 0
                # иначе c.hicon=0 -> общий дефолт
        return c.hicon or self.hicon

    def _apply_host_icon(self, c):
        if c.active and c.host_hwnd:
            ic = self._container_icon(c)
            user32.SendMessageW(c.host_hwnd, WM_SETICON, ICON_SMALL, ic)
            user32.SendMessageW(c.host_hwnd, WM_SETICON, ICON_BIG, ic)

    def _create_host(self, title, hicon=0, app_id=""):
        self._ensure_class()
        # КЛЮЧЕВОЕ для Win+D: Show Desktop (фаза 1) сворачивает только окна со стилем
        # WS_MINIMIZEBOX. Без него хост лишь «накрывается» рабочим столом, IsIconic
        # остаётся False, и наш каскад скрытия членов не срабатывает (док. Raymond
        # Chen, актуально и для Win11). Поэтому хост — НАСТОЯЩЕЕ сворачиваемое окно,
        # а невидимость даём слоем с alpha=0 (WS_EX_LAYERED), а не пустым регионом.
        ex = WS_EX_APPWINDOW | WS_EX_LAYERED
        style = WS_POPUP | WS_MINIMIZEBOX | WS_SYSMENU
        hwnd = user32.CreateWindowExW(
            ex, HOST_CLASS, title, style,
            0, 0, 1, 1, None, None, self.hinst, None)
        if not hwnd:
            log("CreateWindowExW host FAILED err=%s" % ctypes.get_last_error())
            return 0
        # alpha=0 -> полностью прозрачно, но это «настоящее» окно с кнопкой в
        # таскбаре и кандидат на сворачивание по Win+D / клику по кнопке
        user32.SetLayeredWindowAttributes(hwnd, 0, 0, LWA_ALPHA)
        # сбросить кэш стилей, чтобы WS_MINIMIZEBOX точно учёлся
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                            | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        if app_id:
            set_app_id(hwnd, app_id)       # отдельная кнопка в таскбаре до показа
        ic = hicon or self.hicon
        if ic:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, ic)
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, ic)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        # пункт «Редактировать контейнер» в меню окна -> доступен по ПКМ
        # (на Win11 — Shift+ПКМ) по кнопке контейнера в таскбаре
        try:
            sm = user32.GetSystemMenu(hwnd, False)
            if sm:
                user32.AppendMenuW(sm, MF_SEPARATOR, 0, None)
                user32.AppendMenuW(sm, MF_STRING, IDM_EDIT, T("edit_container"))
        except Exception as ex:
            log("system menu add failed: %r" % ex)
        log("HOST created hwnd=%s '%s'" % (hwnd, title))
        return hwnd

    # ===================================================================
    #  Перечисление окон-кандидатов
    # ===================================================================
    def _enum_collect(self, hwnd, _l):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if hwnd == self.msg_hwnd or get_pid(hwnd) == self.my_pid:
                return True
            if get_style(hwnd) & WS_CHILD:
                return True
            if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True
            if get_class_name(hwnd) in SHELL_CLASSES:    # рабочий стол/таскбар и пр.
                return True
            title = get_window_text(hwnd)
            if not title.strip() or is_cloaked(hwnd):
                return True
            self._enum_acc.append((hwnd, title))
        except Exception:
            pass
        return True

    def _enum_windows(self):
        self._enum_acc = []
        user32.EnumWindows(self._enum_cb, 0)
        return list(self._enum_acc)

    def _all_member_hwnds(self):
        s = set()
        for c in self.containers:
            s.update(c.members.keys())
        return s

    def _pick_targets(self):
        busy = self._all_member_hwnds()
        res = [(h, t) for h, t in self._enum_windows() if h not in busy]
        res.sort(key=lambda x: x[1].lower())
        return res

    # ===================================================================
    #  Переоткрытие приложений при активации
    # ===================================================================
    def _find_match(self, sig, used):
        exe = (sig.get("exe") or "").lower()
        base = os.path.basename(exe)
        want_title = (sig.get("title") or "").lower()
        busy = self._all_member_hwnds() | set(used)
        # explorer.exe держит много окон (папки/рабочий стол) -> требуем совпадение
        # заголовка, иначе схватим не ту папку
        strict = base in ("explorer.exe",)
        best = None
        for hwnd, title in self._enum_windows():
            if hwnd in busy:
                continue
            ex = os.path.basename(exe_for_hwnd(hwnd).lower())
            if base and ex != base:
                continue
            if want_title and want_title[:24] in title.lower():
                return hwnd
            if best is None and not strict:
                best = hwnd
        return best

    def _launch(self, sig):
        folder = sig.get("folder")
        if folder and os.path.isdir(folder):
            try:
                subprocess.Popen(["explorer.exe", folder], close_fds=True)
                log("LAUNCH folder %s" % folder)
            except Exception as ex:
                log("launch folder failed %r: %r" % (folder, ex))
            return
        exe = sig.get("exe")
        if not exe or not os.path.exists(exe):
            log("launch skip (no exe): %r" % exe)
            return
        try:
            subprocess.Popen([exe], close_fds=True)
            log("LAUNCH %s" % exe)
        except Exception as ex:
            log("launch failed %r: %r" % (exe, ex))

    # ===================================================================
    #  Действия меню
    # ===================================================================
    def _set_current(self, c):
        self.current = c
        self._update_tray()

    def _toggle_active(self, c):
        if c.active:
            self._deactivate(c)
        else:
            self._activate(c)

    def _reset_look(self, c):
        c.icon = None
        c.color = None
        c.hicon = 0
        self._apply_host_icon(c)
        self._save()
        self._update_tray()

    def _arrange(self, c, rects):
        # rects — нормализованные (x,y,w,h) в долях 0..1 из выбранного пресета.
        # Кладём окна ВПРИТЫК (как Win+← / Win+→): без зазора и с поправкой на
        # невидимые борта DWM, иначе между окнами и у краёв остаются щели.
        members = [h for h in c.members if user32.IsWindow(h) and not user32.IsIconic(h)]
        if not members or not rects:
            return
        x, y, w, h = work_area()
        placed = []
        for hwnd, (nx, ny, nw, nh) in zip(members, rects):
            # целевая ВИДИМАЯ рамка
            vx = x + int(round(nx * w))
            vy = y + int(round(ny * h))
            vw = max(160, int(round((nx + nw) * w)) - int(round(nx * w)))
            vh = max(120, int(round((ny + nh) * h)) - int(round(ny * h)))
            user32.ShowWindow(hwnd, SW_RESTORE)
            li, ti, ri, bi = frame_insets(hwnd)   # расширяем на невидимые поля
            user32.SetWindowPos(hwnd, HWND_TOP, vx - li, vy - ti,
                                vw + li + ri, vh + ti + bi,
                                SWP_NOACTIVATE | SWP_NOOWNERZORDER)
            placed.append(hwnd)
        # поднять разложенную группу над только что отвязанными/прочими окнами,
        # иначе визуально «ничего не поменялось» (старое окно перекрывает раскладку)
        for hwnd in placed:
            user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                                | SWP_NOOWNERZORDER)
        if placed:
            user32.SetForegroundWindow(placed[0])

    def _reflow(self, c):
        # переразложить ВИДИМЫЕ окна под запомненный вид раскладки c.layout —
        # вызывается при закрытии окна, чтобы оставшиеся заняли всё место
        if not getattr(c, "layout", None) or not c.active:
            return
        members = [h for h in c.members
                   if user32.IsWindow(h) and not user32.IsIconic(h)]
        if members:
            self._arrange(c, _layout_rects(c.layout, len(members)))

    def _toggle_autostart(self):
        set_autostart(not autostart_enabled())

    # ------- автообновление -------
    def _update_tick(self):
        # периодическая проверка релизов (раз в 3 часа), пока включено в настройках
        if self.settings.get("autoupdate", True):
            self._check_update_bg(False)
        self.root.after(3 * 60 * 60 * 1000, self._update_tick)

    def _check_update_bg(self, verbose):
        def worker():
            tag, url = fetch_latest_release()
            if tag and url and version_tuple(tag) > version_tuple(VERSION):
                self._pending_update = (tag, url)
            elif verbose:
                self._pending_update = ("__uptodate__", None)
        threading.Thread(target=worker, daemon=True).start()

    def _do_update(self, url):
        # окно прогресса + загрузка в фоне (UI не зависает) + ретраи на флоду сети
        win = self._dialog(T("update_title"), 440, 184)
        status = tk.Label(win, text=T("updating"), bg=COL_SURFACE, fg=COL_TEXT,
                          font=FONT, justify="left", wraplength=400)
        status.pack(anchor="w", padx=20, pady=(24, 14))
        track = tk.Canvas(win, height=10, bg=COL_BG, highlightthickness=0, bd=0)
        track.pack(fill="x", padx=20)
        bar = track.create_rectangle(0, 0, 0, 10, fill=COL_ACCENT, outline="")
        pct = tk.Label(win, text="", bg=COL_SURFACE, fg=COL_TEXT_DIM, font=FONT_SM)
        pct.pack(anchor="e", padx=20, pady=(6, 0))
        st = {"done": False}

        def set_bar(frac):
            if not win.winfo_exists():
                return
            w = max(1, track.winfo_width())
            frac = max(0.0, min(1.0, frac))
            track.coords(bar, 0, 0, int(w * frac), 10)
            pct.configure(text="%d%%" % int(frac * 100))

        def marquee(pos):                       # неизвестен размер -> бегущая полоса
            if st["done"] or not win.winfo_exists():
                return
            w = max(1, track.winfo_width())
            seg, x = 0.32, (pos % 1.3) - 0.32
            track.coords(bar, int(w * max(0.0, x)), 0,
                         int(w * min(1.0, x + seg)), 10)
            win.after(40, lambda: marquee(pos + 0.035))

        def fail():
            st["done"] = True
            try:
                win.destroy()
            except Exception:
                pass
            try:                                # запасной путь — страница релизов
                webbrowser.open(GITHUB_URL + "/releases/latest")
            except Exception:
                pass
            self._info(T("update_title"), T("update_fail_manual"))

        def succeed(dst):
            st["done"] = True
            try:
                set_bar(1.0)
                status.configure(text=T("installing"))
            except Exception:
                pass
            if self._launch_updater(dst):
                self.root.after(700, self._quit)
            else:
                try:
                    win.destroy()
                except Exception:
                    pass
                self._update_fallback()

        def worker():
            dst = os.path.join(tempfile.gettempdir(), "HDContainer-Setup.exe")
            err = None
            for attempt in range(3):
                try:
                    ctx = ssl.create_default_context()
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "HDContainer-Updater/%s" % VERSION})
                    with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
                        total = int(r.getheader("Content-Length") or 0)
                        if total <= 0:
                            self.root.after(0, lambda: marquee(0.0))
                        got = 0
                        with open(dst, "wb") as f:
                            while True:
                                chunk = r.read(65536)
                                if not chunk:
                                    break
                                f.write(chunk)
                                got += len(chunk)
                                if total > 0:
                                    self.root.after(0, set_bar, got / total)
                    with open(dst, "rb") as f:
                        head = f.read(2)
                    if os.path.getsize(dst) < 1000000 or head != b"MZ":
                        raise IOError("bad download (size=%d)" % os.path.getsize(dst))
                    self.root.after(0, lambda: succeed(dst))
                    return
                except Exception as ex:
                    err = ex
                    log("do_update attempt %d failed: %r" % (attempt + 1, ex))
                    time.sleep(2)
            log("do_update gave up: %r" % err)
            self.root.after(0, fail)

        threading.Thread(target=worker, daemon=True).start()

    def _update_fallback(self):
        try:
            webbrowser.open(GITHUB_URL + "/releases/latest")
        except Exception:
            pass
        self._info(T("update_title"), T("update_fail_manual"))

    def _launch_updater(self, dst):
        # ПОЧЕМУ так: onefile-сборка держит процесс в Job-объекте с kill-on-close,
        # поэтому дочерний cmd/установщик умирает ВМЕСТЕ с приложением и установка
        # не запускается (а CREATE_BREAKAWAY_FROM_JOB запрещён). Поэтому ставим
        # через Планировщик задач — задача выполняется ВНЕ нашего job и переживает
        # выход приложения. bat ждёт полного выхода приложения, затем ставит молча.
        bat = os.path.join(tempfile.gettempdir(), "hdc_update.cmd")
        task = "HDContainer_SelfUpdate"
        script = (
            "@echo off\r\n"
            "setlocal\r\n"
            "set n=0\r\n"
            ":wait\r\n"
            'tasklist /fi "imagename eq HDContainer.exe" 2>nul | '
            'find /i "HDContainer.exe" >nul || goto run\r\n'
            "ping 127.0.0.1 -n 2 >nul\r\n"
            "set /a n+=1\r\n"
            "if %%n%% lss 30 goto wait\r\n"
            ":run\r\n"
            '"%s" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART\r\n'
            'schtasks /Delete /TN "%s" /F >nul 2>&1\r\n'
            '(goto) 2>nul & del "%%~f0"\r\n'
            % (dst, task)
        )
        try:
            with open(bat, "w", encoding="ascii", newline="") as f:
                f.write(script)
        except Exception as ex:
            log("update bat write failed: %r" % ex)
            return False
        try:
            cf = CREATE_NO_WINDOW
            subprocess.run(["schtasks", "/Create", "/TN", task, "/TR", '"%s"' % bat,
                            "/SC", "ONCE", "/ST", "00:00", "/F"], creationflags=cf,
                           timeout=20)
            r = subprocess.run(["schtasks", "/Run", "/TN", task], creationflags=cf,
                               timeout=20)
            if r.returncode == 0:
                return True
            log("schtasks /Run rc=%s" % r.returncode)
        except Exception as ex:
            log("schtasks updater failed: %r" % ex)
        # запасной путь (если Планировщик отключён): отвязанный процесс напрямую
        try:
            subprocess.Popen(["cmd", "/c", bat],
                             creationflags=CREATE_NO_WINDOW | 0x00000008 | 0x00000200,
                             close_fds=True)
            return True
        except Exception as ex:
            log("detached updater failed: %r" % ex)
        return False

    def _activate(self, c):
        if c.active:
            self._set_current(c)
            return
        app_id = "HDContainer." + "".join(
            ch if ch.isalnum() else "_" for ch in c.name)[:96]
        host = self._create_host(c.title or c.name, self._container_icon(c), app_id)
        if not host:
            return
        c.host_hwnd = host
        c.active = True
        self._set_current(c)
        used = set()
        for sig in list(c.apps):
            hw = self._find_match(sig, used)
            if hw and c.attach(hw, host, sig.get("rect")):
                used.add(hw)
                self._queue_reassert(hw, sig.get("rect"))
            else:
                self._launch(sig)
                # дольше ждём «тяжёлые»/поздно стартующие приложения (~30с)
                self.pending.append({"c": c, "sig": sig, "left": 42})
        self._save()
        self._update_tray()

    def _disown_owned_by(self, host):
        """Снять владение со ВСЕХ окон, ещё принадлежащих host, и вернуть скрытым
        видимость. Страховка перед DestroyWindow: иначе система уничтожит эти
        окна вместе с хостом (окна приложений «исчезают», процесс остаётся жив)."""
        if not host:
            return []
        owned = self._owned_by(host)
        for h in owned:
            try:
                set_owner(h, 0)
                user32.SetWindowPos(h, None, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER |
                                    SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_FRAMECHANGED)
                if not user32.IsWindowVisible(h):
                    user32.ShowWindow(h, SW_SHOWNA)
            except Exception:
                pass
        return owned

    def _owned_by(self, host):
        # ТОЛЬКО чужие (других процессов) окна, ещё принадлежащие host — это и есть
        # окна приложений пользователя, которые нельзя уничтожать. Свои окна
        # (служебное «Default IME» и т.п.) система авто-переназначает на host и
        # держать из-за них хост живым нельзя — иначе в таскбаре висит «призрак».
        owned = []

        def _cb(hwnd, _l):
            try:
                if user32.GetWindow(hwnd, GW_OWNER) == host \
                        and get_pid(hwnd) != self.my_pid:
                    owned.append(hwnd)
            except Exception:
                pass
            return True
        try:
            user32.EnumWindows(_EnumProc(_cb), 0)
        except Exception as ex:
            log("owned-by enum failed: %r" % ex)
        return owned

    def _safe_destroy_host(self, host):
        # уничтожаем хост ТОЛЬКО когда им НИЧЕГО не владеет. Сначала пытаемся снять
        # владение; если что-то осталось — НЕ уничтожаем (прячем хост и «утекаем»
        # невидимым окном), лучше так, чем уничтожить окно пользователя.
        if not host:
            return
        left = self._disown_owned_by(host)
        if left:
            log("safe_destroy: force-disowned %d window(s) from host %s"
                % (len(left), host))
        stuck = self._owned_by(host)
        if stuck:
            # реально не смогли отвязать чужое окно пользователя (напр. UIPI/админ):
            # хост не уничтожаем, но УБИРАЕМ его кнопку из таскбара, чтобы не было
            # «призрака» — снимаем WS_EX_APPWINDOW и ставим WS_EX_TOOLWINDOW.
            log("safe_destroy: host %s still owns %d foreign win %r -> detab+hide"
                % (host, len(stuck), [get_class_name(h) for h in stuck]))
            try:
                ex = user32.GetWindowLongW(host, GWL_EXSTYLE) & 0xFFFFFFFF
                user32.SetWindowLongW(host, GWL_EXSTYLE,
                                      (ex & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW)
                user32.ShowWindow(host, SW_HIDE)
                user32.SetWindowPos(host, None, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER |
                                    SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_FRAMECHANGED)
            except Exception:
                pass
            return
        try:
            # СНАЧАЛА прячем (шелл убирает кнопку таскбара), ПОТОМ уничтожаем
            user32.ShowWindow(host, SW_HIDE)
            user32.DestroyWindow(host)
        except Exception:
            pass

    def _deactivate(self, c):
        c.detach_all()
        self._safe_destroy_host(c.host_hwnd)
        c.host_hwnd = 0
        c.active = False
        self.pending = [p for p in self.pending if p["c"] is not c]
        self._save()
        self._update_tray()

    def _import_icon(self, c, src):
        # «аватар» контейнера: ЛЮБАЯ картинка -> квадратная .ico. Неквадратное
        # обрезаем по центру, прозрачность сохраняем. Готовый .ico -> копируем как есть.
        safe = "".join(ch if ch.isalnum() else "_" for ch in c.name)[:40] or "icon"
        dst = os.path.join(_ICONDIR, safe + ".ico")
        try:
            os.makedirs(_ICONDIR, exist_ok=True)
            if HAVE_PIL:
                im = Image.open(src).convert("RGBA")
                w, h = im.size
                m = min(w, h)                         # центр-кроп до квадрата
                im = im.crop(((w - m) // 2, (h - m) // 2,
                              (w - m) // 2 + m, (h - m) // 2 + m))
                im = im.resize((256, 256), Image.LANCZOS)
                im.save(dst, format="ICO",
                        sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                               (64, 64), (128, 128), (256, 256)])
                c.icon = dst
            elif src.lower().endswith(".ico"):
                with open(src, "rb") as s, open(dst, "wb") as o:
                    o.write(s.read())
                c.icon = dst
            else:
                c.icon = src
        except Exception as ex:
            log("import icon failed: %r" % ex)
            c.icon = src if src.lower().endswith(".ico") else c.icon

    def _avatar_photo(self, src, size):
        # квадратное превью картинки на фоне редактора (для окна-редактора)
        if not (HAVE_PIL and src and os.path.exists(src)):
            return None
        try:
            im = Image.open(src).convert("RGBA")
            w, h = im.size
            m = min(w, h)
            im = im.crop(((w - m) // 2, (h - m) // 2,
                          (w - m) // 2 + m, (h - m) // 2 + m)).resize(
                (size, size), Image.LANCZOS)
            bg = Image.new("RGBA", (size, size), hex_rgb(COL_BG) + (255,))
            bg.alpha_composite(im)
            return ImageTk.PhotoImage(bg)
        except Exception as ex:
            log("avatar preview failed: %r" % ex)
            return None

    def _swatch_photo(self, dot=None, sel=False, none=False, plus=False, size=30):
        # чёткая (сглаженная) иконка-кружок цвета; выделение — тонкое серое кольцо
        if not HAVE_PIL:
            return None
        S = 4
        n = size * S
        dim = hex_rgb(COL_TEXT_DIM)
        im = Image.new("RGBA", (n, n), hex_rgb(COL_SURFACE) + (255,))
        d = ImageDraw.Draw(im)
        c = n / 2.0
        r = n * 0.30
        if none:
            d.ellipse([c - r, c - r, c + r, c + r], outline=dim, width=S)
            o = r * 0.66
            d.line([c - o, c + o, c + o, c - o], fill=dim, width=S)
        elif plus:
            d.ellipse([c - r, c - r, c + r, c + r], outline=dim, width=S)
            a = r * 0.52
            w = int(S * 1.5)
            d.line([c - a, c, c + a, c], fill=dim, width=w)
            d.line([c, c - a, c, c + a], fill=dim, width=w)
        elif dot:
            d.ellipse([c - r, c - r, c + r, c + r], fill=hex_rgb(dot))
        if sel:
            rr = n * 0.42
            d.ellipse([c - rr, c - rr, c + rr, c + rr], outline=dim, width=int(S * 0.9))
        return ImageTk.PhotoImage(im.resize((size, size), Image.LANCZOS))

    def _close_badge_photo(self, size):
        # значок «удалить» (× в тёмном кружке) поверх аватара при наведении
        if not HAVE_PIL:
            return None
        S = 4
        n = size * S
        im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse([0, 0, n - 1, n - 1], fill=(20, 20, 22, 230),
                  outline=(230, 230, 230, 255), width=S)
        a = n * 0.28
        c = n / 2.0
        w = int(S * 1.3)
        d.line([c - a, c - a, c + a, c + a], fill=(235, 235, 235, 255), width=w)
        d.line([c - a, c + a, c + a, c - a], fill=(235, 235, 235, 255), width=w)
        return ImageTk.PhotoImage(im.resize((size, size), Image.LANCZOS))

    def _make_recolored_ico(self, color, dst):
        # дефолтное лого, перекрашенное в цвет метки -> .ico (тот же приём, что и
        # для иконки в таскбаре: красим синюю панель, сохраняя тени/градиент)
        if not (HAVE_PIL and os.path.exists(_ICON)):
            return None
        rgb = hex_rgb(color)
        if not rgb:
            return None
        try:
            tr, tg, tb = rgb
            ref = 132.0
            im = Image.open(_ICON).convert("RGBA").resize((256, 256), Image.LANCZOS)
            px = im.load()
            for y in range(im.height):
                for x in range(im.width):
                    r, g, b, a = px[x, y]
                    if b > r * 1.25 and b > g * 1.05 and b > 80:
                        f = (0.299 * r + 0.587 * g + 0.114 * b) / ref
                        px[x, y] = (min(255, int(tr * f)), min(255, int(tg * f)),
                                    min(255, int(tb * f)), a)
            im.save(dst, format="ICO",
                    sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                           (64, 64), (128, 128), (256, 256)])
            return dst
        except Exception as ex:
            log("recolor ico failed: %r" % ex)
            return None

    def _container_icon_file(self, c):
        # .ico, совпадающая с иконкой контейнера: свой аватар -> как есть;
        # только цвет -> перекрашенное дефолтное лого; иначе -> общее лого
        if c.icon and os.path.exists(c.icon):
            return c.icon
        if c.color:
            try:
                os.makedirs(_ICONDIR, exist_ok=True)
                safe = "".join(ch if ch.isalnum() else "_" for ch in c.name)[:40] or "label"
                dst = os.path.join(_ICONDIR, safe + "_label.ico")
                if self._make_recolored_ico(c.color, dst):
                    return dst
            except Exception:
                pass
        return _ICON

    def _color_popup(self, anchor, initial, on_pick):
        # компактный встроенный выбор цвета (HSV-полосы + hex) в стиле приложения
        import colorsys
        rgb = hex_rgb(initial) or (76, 139, 245)
        h, s, v = colorsys.rgb_to_hsv(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)
        st = {"h": h, "s": s, "v": v}
        W, BH, PAD = 200, 14, 10
        win = tk.Toplevel(anchor.winfo_toplevel())   # дитя редактора -> закроется с ним
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=COL_BORDER)
        fr = tk.Frame(win, bg=COL_SURFACE)
        fr.pack(padx=1, pady=1)

        def cur_hex():
            r, g, b = colorsys.hsv_to_rgb(st["h"], st["s"], st["v"])
            return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))

        preview = tk.Canvas(fr, width=W, height=22, bg=COL_SURFACE,
                            highlightthickness=0, bd=0)
        pv = preview.create_rectangle(0, 0, W, 22, outline="", fill=initial)
        preview.pack(padx=12, pady=(12, 8))
        bars = {}

        def make_bar(key):
            cv = tk.Canvas(fr, width=W + 2 * PAD, height=BH + 8, bg=COL_SURFACE,
                           highlightthickness=0, bd=0, cursor="hand2")
            cv.pack(padx=12, pady=2)

            def onclick(e):
                st[key] = max(0.0, min(1.0, (e.x - PAD) / float(W)))
                redraw()
            cv.bind("<Button-1>", onclick)
            cv.bind("<B1-Motion>", onclick)
            bars[key] = cv

        def redraw():
            for key, cv in bars.items():
                cv.delete("all")
                for i in range(0, W, 2):
                    t = i / float(W)
                    if key == "h":
                        r, g, b = colorsys.hsv_to_rgb(t, 1, 1)
                    elif key == "s":
                        r, g, b = colorsys.hsv_to_rgb(st["h"], t, max(st["v"], 0.15))
                    else:
                        r, g, b = colorsys.hsv_to_rgb(st["h"], st["s"], t)
                    cv.create_line(PAD + i, 4, PAD + i, 4 + BH, width=2,
                                   fill="#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255)))
                x = PAD + st[key] * W
                cv.create_rectangle(x - 2, 1, x + 2, 4 + BH + 2, outline="white", width=2)
            preview.itemconfigure(pv, fill=cur_hex())
            hx.set(cur_hex())

        for k in ("h", "s", "v"):
            make_bar(k)

        row = tk.Frame(fr, bg=COL_SURFACE)
        row.pack(fill="x", padx=12, pady=(6, 10))
        hx = tk.StringVar()
        e = tk.Entry(row, textvariable=hx, width=9, font=FONT_SM, bg=COL_BG,
                     fg=COL_TEXT, insertbackground=COL_TEXT, relief="flat",
                     highlightthickness=1, highlightbackground=COL_BORDER,
                     highlightcolor=COL_ACCENT, justify="center")
        e.pack(side="left", ipady=3)

        def apply_hex(_=None):
            rr = hex_rgb(hx.get().strip())
            if rr:
                st["h"], st["s"], st["v"] = colorsys.rgb_to_hsv(
                    rr[0] / 255.0, rr[1] / 255.0, rr[2] / 255.0)
                redraw()
        e.bind("<Return>", apply_hex)
        e.bind("<FocusOut>", apply_hex)

        def ok():
            on_pick(cur_hex())
            win.destroy()
        self._accent_btn(row, T("ok"), ok).pack(side="right")
        self._ghost_btn(row, T("cancel"), win.destroy).pack(side="right", padx=(0, 8))

        redraw()
        anchor.update_idletasks()
        x = max(0, anchor.winfo_rootx() - 40)
        y = anchor.winfo_rooty() + anchor.winfo_height() + 6
        win.geometry("+%d+%d" % (x, y))
        win.lift()
        win.focus_force()
        win.bind("<Escape>", lambda e: win.destroy())

        def _close_outside(_e=None):
            # закрыть по ЛЮБОМУ клику мимо: фокус ушёл не на наш дочерний виджет
            try:
                f = win.focus_get()
            except Exception:
                f = None
            if f is not None and str(f).startswith(str(win)):
                return                       # клик по полю hex/полосам — не закрываем
            try:
                win.destroy()
            except Exception:
                pass
        win.after(300, lambda: win.winfo_exists()
                  and win.bind("<FocusOut>", _close_outside))

    def _create_container(self):
        c = Container(T("container_n", len(self.containers) + 1))
        self._edit_container(c)            # is_new определяется по отсутствию в списке

    # единое окно: имя, иконка, цвет, окна (добавить/убрать), раскладка, сброс
    PALETTE = [None, "#4c8bf5", "#34c759", "#ff9f0a", "#ff453a",
               "#bf5af2", "#ffd60a", "#64d2ff", "#ff375f"]

    def _edit_container(self, c):
        is_new = c not in self.containers
        win = self._dialog(T("edit_container"), 940, 700)
        draft = {"icon": c.icon, "layout": c.layout}
        color_state = {"v": c.color}
        cur_members = [h for h in c.members if user32.IsWindow(h)] if c.active else []
        selected = set(cur_members)
        tmpdir = tempfile.mkdtemp(prefix="hdc_thumb_")
        tmpfiles, imgs = [], []

        # ---------- имя ----------
        head = tk.Frame(win, bg=COL_SURFACE)
        head.pack(fill="x", padx=20, pady=(14, 2))
        tk.Label(head, text=T("lbl_name"), bg=COL_SURFACE, fg=COL_TEXT_DIM,
                 font=FONT_SM).pack(anchor="w")
        name_var = tk.StringVar(value=c.name)
        ent = tk.Entry(head, textvariable=name_var, font=FONT_H, bg=COL_BG,
                       fg=COL_TEXT, insertbackground=COL_TEXT, relief="flat",
                       highlightthickness=1, highlightbackground=COL_BORDER,
                       highlightcolor=COL_ACCENT)
        ent.pack(fill="x", ipady=5, pady=(3, 0))

        # ---------- иконка (аватар) + цвет ----------
        look = tk.Frame(win, bg=COL_SURFACE)
        look.pack(fill="x", padx=20, pady=(10, 4))

        ico_col = tk.Frame(look, bg=COL_SURFACE)
        ico_col.pack(side="left", anchor="n")
        tk.Label(ico_col, text=T("lbl_icon"), bg=COL_SURFACE, fg=COL_TEXT_DIM,
                 font=FONT_SM).pack(anchor="w")
        ico_row = tk.Frame(ico_col, bg=COL_SURFACE)
        ico_row.pack(anchor="w", pady=(4, 0))
        AV, BD = 46, 18
        prev = tk.Canvas(ico_row, width=AV, height=AV, bg=COL_BG, cursor="hand2",
                         highlightthickness=1, highlightbackground=COL_BORDER)
        prev_id = prev.create_image(AV // 2 + 1, AV // 2 + 1)
        badge_ph = self._close_badge_photo(BD)
        imgs.append(badge_ph)
        badge_id = prev.create_image(AV - BD // 2 - 1, BD // 2 + 1,
                                     image=(badge_ph or ""), state="hidden")
        prev.pack(side="left")
        av_ref = {"img": None, "has": False}

        def show_avatar(path):                 # сразу показать выбранную картинку
            ph = self._avatar_photo(path, AV - 2)
            av_ref["img"] = ph
            av_ref["has"] = bool(ph)
            prev.itemconfigure(prev_id, image=(ph or ""))
            if not ph:
                prev.itemconfigure(badge_id, state="hidden")

        def choose_icon():
            from tkinter import filedialog
            ftypes = [("Images", "*.png *.jpg *.jpeg *.ico *.bmp *.gif *.webp *.tiff *.tif"),
                      ("All files", "*.*")]
            path = filedialog.askopenfilename(parent=win, title=T("lbl_icon"),
                                              filetypes=ftypes)
            if not path:
                return
            if not HAVE_PIL and not path.lower().endswith(".ico"):
                self._info(T("need_ico_title"), T("need_ico_msg"))
                return
            draft["icon"] = path
            show_avatar(path)

        # навёл на превью -> в углу появляется крестик; клик по нему удаляет иконку
        def _on_enter(_e):
            if av_ref["has"] and badge_ph:
                prev.itemconfigure(badge_id, state="normal")

        def _on_leave(_e):
            prev.itemconfigure(badge_id, state="hidden")

        def _on_click(e):
            if av_ref["has"] and badge_ph and e.x >= AV - BD and e.y <= BD:
                draft["icon"] = None           # клик по крестику — убрать иконку
                show_avatar(None)
            else:
                choose_icon()                  # клик по превью — выбрать
        prev.bind("<Enter>", _on_enter)
        prev.bind("<Leave>", _on_leave)
        prev.bind("<Button-1>", _on_click)
        self._ghost_btn(ico_row, T("choose_ico"), choose_icon).pack(
            side="left", padx=(10, 0))
        show_avatar(c.icon)

        col_col = tk.Frame(look, bg=COL_SURFACE)
        col_col.pack(side="left", anchor="n", padx=(40, 0))
        tk.Label(col_col, text=T("lbl_color"), bg=COL_SURFACE, fg=COL_TEXT_DIM,
                 font=FONT_SM).pack(anchor="w")
        sw_row = tk.Frame(col_col, bg=COL_SURFACE)
        sw_row.pack(anchor="w", pady=(6, 0))
        swatches = {}          # key -> (widget, normal_img, selected_img)

        if HAVE_PIL:
            def _img(**kw):
                ph = self._swatch_photo(size=30, **kw)
                imgs.append(ph)            # держим ссылку (чистится в close)
                return ph

            def pick_color(col):
                color_state["v"] = col
                is_custom = col is not None and col not in self.PALETTE
                for key, (lbl, nrm, sel) in swatches.items():
                    lbl.configure(image=(sel if key == col else nrm))
                if col:
                    custom_lbl.configure(image=(_img(dot=col, sel=is_custom)
                                                if is_custom else cplus))
                else:
                    custom_lbl.configure(image=cplus)

            for key in self.PALETTE:
                if key is None:
                    nrm, sel = _img(none=True), _img(none=True, sel=True)
                else:
                    nrm, sel = _img(dot=key), _img(dot=key, sel=True)
                lbl = tk.Label(sw_row, image=nrm, bg=COL_SURFACE, cursor="hand2")
                lbl.bind("<Button-1>", lambda e, k=key: pick_color(k))
                lbl.pack(side="left", padx=(0, 3))
                swatches[key] = (lbl, nrm, sel)

            cplus = _img(plus=True)
            custom_lbl = tk.Label(sw_row, image=cplus, bg=COL_SURFACE, cursor="hand2")
            custom_lbl.bind("<Button-1>", lambda e: self._color_popup(
                custom_lbl, color_state["v"] or "#4c8bf5", pick_color))
            custom_lbl.pack(side="left", padx=(2, 0))
            pick_color(c.color)
        else:
            def pick_color(col):
                color_state["v"] = col
                for key, (cv, ring) in swatches.items():
                    cv.itemconfigure(ring, outline=(COL_TEXT_DIM if key == col else ""))
            for key in list(self.PALETTE) + ["__custom__"]:
                cv = tk.Canvas(sw_row, width=30, height=30, bg=COL_SURFACE,
                               highlightthickness=0, bd=0, cursor="hand2")
                ring = cv.create_oval(3, 3, 27, 27, outline="", width=1)
                if key == "__custom__":
                    cv.create_oval(9, 9, 21, 21, outline=COL_TEXT_DIM, width=1)
                    cv.create_text(15, 15, text="+", fill=COL_TEXT_DIM)
                    cv.bind("<Button-1>", lambda e: self._color_popup(
                        cv, color_state["v"] or "#4c8bf5", pick_color))
                elif key is None:
                    cv.create_oval(9, 9, 21, 21, outline=COL_TEXT_DIM, width=1)
                    cv.create_line(11, 19, 19, 11, fill=COL_TEXT_DIM, width=2)
                    cv.bind("<Button-1>", lambda e, k=key: pick_color(k))
                else:
                    cv.create_oval(8, 8, 22, 22, fill=key, outline="")
                    cv.bind("<Button-1>", lambda e, k=key: pick_color(k))
                cv.pack(side="left", padx=(0, 4))
                if key != "__custom__":
                    swatches[key] = (cv, ring)
            pick_color(c.color)

        # ---------- окна ----------
        tk.Frame(win, bg=COL_BORDER, height=1).pack(fill="x", padx=20, pady=(8, 0))
        tk.Label(win, text=T("lbl_windows"), bg=COL_SURFACE, fg=COL_TEXT_DIM,
                 font=FONT_SM).pack(anchor="w", padx=20, pady=(6, 2))

        # ---------- низ: кнопки, разделитель, второстепенные действия ----------
        foot = tk.Frame(win, bg=COL_SURFACE)
        foot.pack(side="bottom", fill="x", padx=20, pady=(6, 10))
        tk.Frame(win, bg=COL_BORDER, height=1).pack(side="bottom", fill="x")
        links = tk.Frame(win, bg=COL_SURFACE)
        links.pack(side="bottom", fill="x", padx=20, pady=(5, 6))

        body = tk.Frame(win, bg=COL_SURFACE)
        canvas = tk.Canvas(body, bg=COL_SURFACE, highlightthickness=0)
        vsb = self._dark_scrollbar(body, canvas)
        vsb.pack(side="right", fill="y", padx=(2, 0))
        canvas.pack(side="left", fill="both", expand=True)
        grid = tk.Frame(canvas, bg=COL_SURFACE)
        canvas.create_window((0, 0), window=grid, anchor="nw")
        grid.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        wa = work_area()
        aspect = wa[2] / max(wa[3], 1)
        presets_row = tk.Frame(foot, bg=COL_SURFACE)
        presets_row.pack(side="left")

        def rebuild_presets():
            for ch in presets_row.winfo_children():
                ch.destroy()
            pw = 56
            ph = max(34, int(pw / aspect))
            iw, ih = pw - 6, ph - 6
            for kind, rects in layouts_for(len(selected))[:6]:
                issel = (draft["layout"] == kind)
                cv = tk.Canvas(presets_row, width=pw, height=ph, bg=COL_BG,
                               highlightthickness=1, cursor="hand2", bd=0,
                               highlightbackground=(COL_ACCENT if issel else COL_BORDER))
                for (nx, ny, nw, nh) in rects:
                    cv.create_rectangle(
                        3 + nx * iw + 1, 3 + ny * ih + 1,
                        3 + (nx + nw) * iw - 1, 3 + (ny + nh) * ih - 1,
                        fill=(COL_ACCENT if issel else COL_SURFACE2),
                        outline=(COL_ACCENT_HI if issel else COL_TEXT_DIM), width=1)

                def choose(_e=None, K=kind):
                    draft["layout"] = None if draft["layout"] == K else K
                    rebuild_presets()
                cv.bind("<Button-1>", choose)
                cv.pack(side="left", padx=(0, 6))

        TW, TH, COLS = 264, 150, 3

        def make_tile(idx, hwnd, title):
            tile = tk.Frame(grid, bg=COL_BG, highlightthickness=2, cursor="hand2",
                            highlightbackground=(COL_ACCENT if hwnd in selected else COL_BORDER))
            thumb = None
            ppm = capture_thumb(hwnd, TW, TH)
            if ppm:
                try:
                    p = os.path.join(tmpdir, "t%d.ppm" % idx)
                    with open(p, "wb") as f:
                        f.write(ppm)
                    tmpfiles.append(p)
                    img = tk.PhotoImage(file=p)
                    imgs.append(img)
                    thumb = tk.Label(tile, image=img, bg=COL_BG)
                except Exception:
                    thumb = None
            if thumb is None:
                thumb = tk.Label(tile, text=T("no_preview"), bg=COL_BG,
                                 fg=COL_TEXT_DIM, width=34, height=8, font=FONT_SM)
            thumb.pack(padx=6, pady=(6, 2))
            cap = tk.Label(tile, text=(title[:46] or "—"), bg=COL_BG, fg=COL_TEXT,
                           font=FONT_SM, wraplength=TW, justify="left")
            cap.pack(padx=6, pady=(0, 6), anchor="w")

            def toggle(_=None, hwnd=hwnd, tile=tile):
                if hwnd in selected:
                    selected.discard(hwnd)
                    tile.configure(highlightbackground=COL_BORDER)
                else:
                    selected.add(hwnd)
                    tile.configure(highlightbackground=COL_ACCENT)
                rebuild_presets()
            for wdg in (tile, thumb, cap):
                wdg.bind("<Button-1>", toggle)
            tile.grid(row=idx // COLS, column=idx % COLS, padx=8, pady=8, sticky="n")

        def close():
            try:
                canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass
            imgs.clear()
            for p in tmpfiles:
                try:
                    os.remove(p)
                except Exception:
                    pass
            try:
                os.rmdir(tmpdir)
            except Exception:
                pass

        def do_reset():
            draft["icon"] = None
            show_avatar(None)
            pick_color(None)

        def do_delete():
            close()
            self.root.after(1, lambda: self._delete(c))

        def do_save():
            nm = name_var.get().strip() or c.name
            c.name, c.title = nm, nm
            if draft["icon"] != c.icon:
                if draft["icon"]:
                    self._import_icon(c, draft["icon"])
                else:
                    c.icon = None
            c.color = color_state["v"]
            c.hicon = 0
            if c not in self.containers:
                self.containers.append(c)
            want = set(h for h in selected if user32.IsWindow(h))
            if want and not c.active:
                self._activate(c)
            if c.active:
                if c.host_hwnd:
                    user32.SetWindowTextW(c.host_hwnd, nm)
                for h in list(c.members):
                    if h not in want:
                        c.detach(h)
                for h in want:
                    if h not in c.members and user32.IsWindow(h):
                        c.attach(h, c.host_hwnd)
                self._set_current(c)
            c.layout = draft["layout"]          # запомнить ВИД раскладки
            self._apply_host_icon(c)
            self._save()
            self._update_tray()
            kind = c.layout
            close()
            if kind and c.active:
                self.root.after(60, lambda: self._reflow(c))

        # второстепенные действия (тихие ссылки)
        def link(parent, text, cmd, danger=False):
            l = tk.Label(parent, text=text, bg=COL_SURFACE, fg=COL_TEXT_DIM,
                         font=FONT_SM, cursor="hand2")
            l.bind("<Button-1>", lambda e: cmd())
            l.bind("<Enter>", lambda e: l.configure(fg=("#ff453a" if danger else COL_TEXT)))
            l.bind("<Leave>", lambda e: l.configure(fg=COL_TEXT_DIM))
            return l

        def dot(parent):
            tk.Label(parent, text="·", bg=COL_SURFACE, fg=COL_TEXT_DIM).pack(
                side="left", padx=8)
        link(links, T("reset_look"), do_reset).pack(side="left")
        dot(links)
        link(links, T("shortcut_btn"), lambda: self._create_shortcut(c)).pack(side="left")
        if not is_new:
            dot(links)
            link(links, T("delete"), do_delete, danger=True).pack(side="left")

        self._accent_btn(foot, "  " + T("save") + "  ", do_save).pack(side="right")
        self._ghost_btn(foot, T("cancel"), close).pack(side="right", padx=(0, 10))

        targets = [(h, get_window_text(h)) for h in cur_members] + self._pick_targets()
        if not targets:
            tk.Label(grid, text=T("no_windows"), bg=COL_SURFACE, fg=COL_TEXT_DIM,
                     font=FONT).grid(padx=20, pady=20)
        for i, (hwnd, title) in enumerate(targets):
            make_tile(i, hwnd, title)

        body.pack(fill="both", expand=True, padx=14, pady=(0, 4))
        rebuild_presets()
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda e: close())

    def _create_shortcut(self, c):
        exe = os.path.abspath(sys.argv[0])
        # ярлык получает ТУ ЖЕ иконку, что и контейнер: свой аватар, либо
        # перекрашенное в цвет метки дефолтное лого, либо общее лого
        icon = self._container_icon_file(c)
        if not (icon and os.path.exists(icon)):
            icon = exe
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):
            desktop = os.path.join(os.environ.get("USERPROFILE", _DIR), "Desktop")
        safe = "".join(ch for ch in c.name if ch not in '\\/:*?"<>|').strip() or "Container"
        lnk = os.path.join(desktop, safe + ".lnk")
        name_ps = c.name.replace("'", "''")
        ps = ("$w = New-Object -ComObject WScript.Shell\n"
              "$s = $w.CreateShortcut('%s')\n"
              "$s.TargetPath = '%s'\n"
              "$s.Arguments = '--launch \"%s\"'\n"
              "$s.IconLocation = '%s,0'\n"
              "$s.WorkingDirectory = '%s'\n"
              "$s.Description = 'HDContainer: %s'\n"
              "$s.Save()\n" % (lnk.replace("'", "''"), exe.replace("'", "''"),
                               name_ps, icon.replace("'", "''"),
                               os.path.dirname(exe).replace("'", "''"), name_ps))
        try:
            ps1 = os.path.join(_DIR, "_mk_shortcut.ps1")
            with open(ps1, "w", encoding="utf-8-sig") as f:
                f.write(ps)
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", ps1], creationflags=CREATE_NO_WINDOW, timeout=30)
            ok = os.path.exists(lnk)
        except Exception as ex:
            log("shortcut failed: %r" % ex)
            ok = False
        self._info(T("shortcut_title"),
                   T("shortcut_ok", lnk) if ok else T("shortcut_fail"))

    def _delete(self, c):
        if not self._ask_yesno(T("delete_title"), T("delete_msg", c.name)):
            return
        if c.active:
            self._deactivate(c)
        if c in self.containers:
            self.containers.remove(c)
        if self.current is c:
            self.current = next((x for x in self.containers if x.active), None)
        self._save()
        self._update_tray()

    def _quit(self):
        for c in self.containers:
            c.detach_all()
            self._safe_destroy_host(c.host_hwnd)
            c.host_hwnd = 0
            c.active = False
        self._save_recovery()      # пустой список
        self._save_containers()
        self._del_tray()
        # вернуть оригинальный wndproc до разрушения
        try:
            _SetWindowLongPtr(self.msg_hwnd, GWLP_WNDPROC, self._old_proc)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ===================================================================
    #  Быстрый тик: вернуть свёрнутые в одиночку окна + дожать позицию
    # ===================================================================
    def _queue_reassert(self, hwnd, rect):
        if hwnd and rect and len(rect) == 4:
            self._reassert.append({"hwnd": hwnd, "rect": list(rect), "n": 4})

    def _watch(self):
        try:
            for c in self.containers:
                if not c.active or not c.host_hwnd:
                    continue
                if user32.IsIconic(c.host_hwnd):
                    # группа свёрнута (таскбар-кнопка / Win+D): прячем все окна явно —
                    # на Win11 авто-скрытие owned-окон срабатывает не всегда
                    for h in list(c.members):
                        m = c.members.get(h)
                        if m and not m.min_detached and user32.IsWindow(h) \
                                and user32.IsWindowVisible(h):
                            user32.ShowWindow(h, SW_HIDE)
                            m.group_hidden = True
                    continue
                for h in list(c.members):
                    m = c.members.get(h)
                    if not m or not user32.IsWindow(h):
                        continue
                    if m.group_hidden:           # группа развёрнута — вернуть окна
                        user32.ShowWindow(h, SW_SHOWNA)
                        m.group_hidden = False
                    iconic = bool(user32.IsIconic(h))
                    # свёрнутое в одиночку окно временно ОТВЯЗЫВАЕМ -> своя кнопка
                    # в таскбаре, его можно развернуть; развернул — вернулось в контейнер
                    if iconic and not m.min_detached:
                        set_owner(h, m.o_owner or 0)
                        m.min_detached = True
                    elif not iconic and m.min_detached:
                        set_owner(h, c.host_hwnd)
                        m.min_detached = False
            if self._reassert:
                keep = []
                for e in self._reassert:
                    h = e["hwnd"]
                    if user32.IsWindow(h) and not user32.IsIconic(h):
                        l, t, w, hh = e["rect"]
                        user32.SetWindowPos(h, HWND_TOP, int(l), int(t), int(w), int(hh),
                                            SWP_NOACTIVATE | SWP_NOOWNERZORDER)
                    e["n"] -= 1
                    if e["n"] > 0:
                        keep.append(e)
                self._reassert = keep
        except Exception as ex:
            log("watch err: %r" % ex)
        self.root.after(250, self._watch)

    # ===================================================================
    #  Поллинг: чистка закрытых окон + отложенный подхват
    # ===================================================================
    def _poll(self):
        if self._pending_update:
            upd = self._pending_update
            self._pending_update = None
            tag, url = upd
            if tag == "__uptodate__":
                self._info(T("update_title"), T("up_to_date"))
            elif url and self._ask_yesno(T("update_title"), T("update_available", tag)):
                self._do_update(url)

        changed = False
        for c in self.containers:
            if c.active and c.prune():       # окно закрыли -> убрали из контейнера
                changed = True
                self._reflow(c)              # оставшиеся занимают освободившееся место

        if self.pending:
            still = []
            for p in self.pending:
                c = p["c"]
                if not c.active:
                    continue
                used = self._all_member_hwnds()
                hw = self._find_match(p["sig"], used)
                if hw and c.attach(hw, c.host_hwnd, p["sig"].get("rect")):
                    self._queue_reassert(hw, p["sig"].get("rect"))
                    changed = True
                    continue
                p["left"] -= 1
                if p["left"] > 0:
                    still.append(p)
                else:
                    log("pending give up: %r" % p["sig"].get("exe"))
            self.pending = still

        if changed:
            self._save()
            self._update_tray()
        self.root.after(700, self._poll)

    # ===================================================================
    #  Хранилище контейнеров
    # ===================================================================
    def _save(self):
        for c in self.containers:
            if c.active:
                c.sync_app_rects()       # запомнить последние позиции окон
        self._save_containers()
        self._save_recovery()

    def _save_containers(self):
        try:
            with open(_STORE, "w", encoding="utf-8") as f:
                json.dump([c.to_dict() for c in self.containers], f, ensure_ascii=False)
        except Exception as ex:
            log("save_containers failed: %r" % ex)

    def _load_containers(self):
        try:
            if not os.path.exists(_STORE):
                return
            with open(_STORE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for d in data:
                self.containers.append(Container.from_dict(d))
            log("loaded %d containers" % len(self.containers))
        except Exception as ex:
            log("load_containers failed: %r" % ex)

    # ===================================================================
    #  Страховка от потери окон (orphan recovery)
    # ===================================================================
    def _save_recovery(self):
        # снимок активных контейнеров и их окон. Пишется при каждом изменении;
        # при штатном выходе перетирается пустым -> наличие записей = БЫЛ КРАШ.
        try:
            out = []
            for c in self.containers:
                if not (c.active and c.members):
                    continue
                mem = []
                for m in c.members.values():
                    sig = m.sig or {}
                    mem.append({"hwnd": m.hwnd, "owner": m.o_owner,
                                "exe": sig.get("exe", "")})
                out.append({"name": c.name, "members": mem})
            with open(_RECOVERY, "w", encoding="utf-8") as f:
                json.dump({"containers": out}, f)
        except Exception:
            pass

    def _run_recovery(self):
        # после краха/жёсткого убийства хост умирает, а окна-члены ВЫЖИВАЮТ
        # «сиротами» (проверено эмпирически). Возвращаем им видимость+кнопку
        # таскбара и помечаем контейнеры на авто-восстановление (см. __init__).
        self._recover_pending = []
        data = None
        try:
            if os.path.exists(_RECOVERY):
                with open(_RECOVERY, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception:
            data = None
        try:
            os.remove(_RECOVERY)
        except Exception:
            pass
        conts = data.get("containers") if isinstance(data, dict) else None
        if not conts:
            return
        for cd in conts:
            survived = 0
            for m in cd.get("members", []):
                h = m.get("hwnd")
                if not h or not user32.IsWindow(h):
                    continue
                try:                         # защита от переиспользования hwnd: сверяем exe
                    if m.get("exe") and exe_for_hwnd(h).lower() != m["exe"].lower():
                        continue
                except Exception:
                    pass
                cur = user32.GetWindow(h, GW_OWNER)
                if cur and user32.IsWindow(cur):
                    continue                 # окно ещё кому-то принадлежит — не трогаем
                try:
                    set_owner(h, m.get("owner") or 0)
                    user32.SetWindowPos(h, None, 0, 0, 0, 0,
                                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER |
                                        SWP_NOACTIVATE | SWP_FRAMECHANGED)
                    user32.ShowWindow(h, SW_HIDE)
                    user32.ShowWindow(h, SW_SHOW)   # вернуть видимость + кнопку таскбара
                    survived += 1
                    log("RECOVERED orphan hwnd=%s" % h)
                except Exception as ex:
                    log("recover show failed: %r" % ex)
            if survived:                      # есть что вернуть -> восстановим контейнер
                self._recover_pending.append(cd.get("name"))

    # ===================================================================
    #  Диалоги (tk)
    # ===================================================================
    def _dialog(self, title, w=420, h=180):
        win = tk.Toplevel(self.root)
        win.title(title)
        try:                                   # иконка приложения вместо пера Tk
            if os.path.exists(_ICON):
                win.iconbitmap(_ICON)
        except Exception:
            pass
        win.configure(bg=COL_SURFACE)
        win.geometry("%dx%d" % (w, h))
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.lift()
        win.focus_force()
        win.update_idletasks()
        # по центру экрана
        sx, sy, sw, sh = virtual_screen()
        win.geometry("+%d+%d" % (sx + (sw - w) // 2, sy + (sh - h) // 2))
        return win

    def _accent_btn(self, master, text, cmd):
        b = tk.Label(master, text=text, bg=COL_ACCENT, fg="white", font=FONT,
                     cursor="hand2", padx=14, pady=7)
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=COL_ACCENT_HI))
        b.bind("<Leave>", lambda e: b.configure(bg=COL_ACCENT))
        return b

    def _ghost_btn(self, master, text, cmd):
        b = tk.Label(master, text=text, bg=COL_SURFACE2, fg=COL_TEXT, font=FONT,
                     cursor="hand2", padx=14, pady=7)
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=COL_HOVER))
        b.bind("<Leave>", lambda e: b.configure(bg=COL_SURFACE2))
        return b

    def _dark_scrollbar(self, parent, canvas):
        """Тонкий тёмный скроллбар в стиле приложения вместо нативного белого."""
        sb = tk.Canvas(parent, width=12, bg=COL_SURFACE, highlightthickness=0, bd=0)
        thumb = sb.create_rectangle(0, 0, 0, 0, fill=COL_SURFACE2, outline="")
        drag = {"y": None, "f": 0.0}

        def set_view(first, last):       # вызывается canvas как yscrollcommand
            first, last = float(first), float(last)
            ht = max(sb.winfo_height(), 1)
            if first <= 0.0 and last >= 1.0:
                sb.itemconfigure(thumb, state="hidden")
                return
            sb.itemconfigure(thumb, state="normal")
            sb.coords(thumb, 3, first * ht + 1, 10, last * ht - 1)

        def on_press(e):
            ht = max(sb.winfo_height(), 1)
            co = sb.coords(thumb)
            if not co or e.y < co[1] or e.y > co[3]:      # клик по дорожке — прыжок
                first, last = canvas.yview()
                span = last - first
                f = max(0.0, min(1.0 - span, e.y / ht - span / 2))
                canvas.yview_moveto(f)
            drag["y"] = e.y
            drag["f"] = canvas.yview()[0]

        def on_drag(e):
            if drag["y"] is None:
                return
            ht = max(sb.winfo_height(), 1)
            canvas.yview_moveto(max(0.0, drag["f"] + (e.y - drag["y"]) / ht))

        sb.bind("<Button-1>", on_press)
        sb.bind("<B1-Motion>", on_drag)
        sb.bind("<ButtonRelease-1>", lambda e: drag.update(y=None))
        sb.bind("<Enter>", lambda e: sb.itemconfigure(thumb, fill=COL_HOVER))
        sb.bind("<Leave>", lambda e: sb.itemconfigure(thumb, fill=COL_SURFACE2))
        sb.bind("<Configure>", lambda e: set_view(*canvas.yview()))
        canvas.configure(yscrollcommand=set_view)
        return sb

    def _ask_string(self, title, prompt, initial=""):
        res = {"v": None}
        win = self._dialog(title, 420, 170)
        tk.Label(win, text=prompt, bg=COL_SURFACE, fg=COL_TEXT,
                 font=FONT).pack(anchor="w", padx=18, pady=(18, 6))
        var = tk.StringVar(value=initial)
        ent = tk.Entry(win, textvariable=var, font=FONT, bg=COL_BG, fg=COL_TEXT,
                       insertbackground=COL_TEXT, relief="flat", highlightthickness=1,
                       highlightbackground=COL_BORDER, highlightcolor=COL_ACCENT)
        ent.pack(fill="x", padx=18, ipady=5)
        ent.focus_set()
        ent.select_range(0, "end")
        for combo in ("<Control-a>", "<Control-A>"):
            ent.bind(combo, lambda e: (ent.select_range(0, "end"), "break")[-1])

        def ok():
            res["v"] = var.get().strip()
            win.destroy()

        def cancel():
            win.destroy()

        ent.bind("<Return>", lambda e: ok())
        ent.bind("<Escape>", lambda e: cancel())
        row = tk.Frame(win, bg=COL_SURFACE)
        row.pack(fill="x", padx=18, pady=16)
        self._accent_btn(row, T("ok"), ok).pack(side="right")
        self._ghost_btn(row, T("cancel"), cancel).pack(side="right", padx=(0, 8))
        self.root.wait_window(win)
        return res["v"] or None

    def _ask_yesno(self, title, prompt):
        res = {"v": False}
        win = self._dialog(title, 440, 170)
        tk.Label(win, text=prompt, bg=COL_SURFACE, fg=COL_TEXT, font=FONT,
                 justify="left", wraplength=400).pack(anchor="w", padx=18, pady=(20, 10))

        def yes():
            res["v"] = True
            win.destroy()
        row = tk.Frame(win, bg=COL_SURFACE)
        row.pack(fill="x", padx=18, pady=16)
        self._accent_btn(row, T("yes"), yes).pack(side="right")
        self._ghost_btn(row, T("no"), win.destroy).pack(side="right", padx=(0, 8))
        win.bind("<Escape>", lambda e: win.destroy())
        self.root.wait_window(win)
        return res["v"]

    def _set_setting(self, key, v):
        self.settings[key] = v
        save_settings(self.settings)

    def _setting_check(self, parent, text, val, setter, pad):
        row = tk.Frame(parent, bg=COL_SURFACE)
        row.pack(fill="x", padx=pad, pady=6)
        chk, st, draw = self._make_check(row, val, setter)
        chk.pack(side="left")
        lbl = tk.Label(row, text="   " + text, bg=COL_SURFACE, fg=COL_TEXT,
                       font=FONT, cursor="hand2")
        lbl.pack(side="left")

        def toggle(_e=None):
            st["v"] = not st["v"]
            draw()
            setter(st["v"])
        lbl.bind("<Button-1>", toggle)

    def _setting_lang(self, parent, pad):
        row = tk.Frame(parent, bg=COL_SURFACE)
        row.pack(fill="x", padx=pad, pady=6)
        tk.Label(row, text=T("language"), bg=COL_SURFACE, fg=COL_TEXT,
                 font=FONT).pack(side="left")
        val = tk.Label(row, text=LANG_NAMES.get(LANG, "English") + "   ⌄",
                       bg=COL_SURFACE, fg=COL_ACCENT, font=FONT, cursor="hand2")
        val.pack(side="right")
        val.bind("<Button-1>", lambda e: self._lang_dropdown(val))

    def _lang_dropdown(self, anchor):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=COL_BORDER)
        fr = tk.Frame(win, bg=COL_SURFACE)
        fr.pack(padx=1, pady=1)

        def choose(code):
            global LANG
            LANG = code
            self.settings["lang"] = code
            save_settings(self.settings)
            try:
                win.destroy()
            except Exception:
                pass
            self._update_tray()
            self._reopen_settings()
        for code, nm in LANG_NAMES.items():
            r = tk.Label(fr, text=nm, bg=COL_SURFACE,
                         fg=(COL_ACCENT if code == LANG else COL_TEXT),
                         font=FONT, anchor="w", padx=16, pady=7, cursor="hand2",
                         width=14)
            r.pack(fill="x")
            r.bind("<Enter>", lambda e, r=r: r.configure(bg=COL_HOVER))
            r.bind("<Leave>", lambda e, r=r: r.configure(bg=COL_SURFACE))
            r.bind("<Button-1>", lambda e, code=code: choose(code))
        anchor.update_idletasks()
        x = max(0, anchor.winfo_rootx() - 60)
        y = anchor.winfo_rooty() + anchor.winfo_height() + 4
        win.geometry("+%d+%d" % (x, y))
        win.focus_force()
        win.after(250, lambda: win.winfo_exists() and win.bind(
            "<FocusOut>", lambda e: win.destroy()))

    def _reopen_settings(self):
        if getattr(self, "_settings_win", None):
            try:
                self._settings_win.destroy()
            except Exception:
                pass
            self._settings_win = None
        self._open_settings()

    def _open_settings(self):
        if getattr(self, "_settings_win", None):
            try:
                self._settings_win.destroy()
            except Exception:
                pass
        win = self._dialog(T("settings_title"), 470, 500)
        self._settings_win = win
        pad = 24
        tk.Label(win, text=T("settings_title"), bg=COL_SURFACE, fg=COL_TEXT,
                 font=FONT_H).pack(anchor="w", padx=pad, pady=(18, 12))
        self._setting_check(win, T("run_with_windows"), autostart_enabled(),
                            lambda v: set_autostart(v), pad)
        self._setting_check(win, T("auto_update"),
                            self.settings.get("autoupdate", True),
                            lambda v: self._set_setting("autoupdate", v), pad)
        self._setting_lang(win, pad)
        upd = tk.Label(win, text=T("check_update"), bg=COL_SURFACE, fg=COL_ACCENT,
                       font=FONT_SM, cursor="hand2")
        upd.pack(anchor="w", padx=pad, pady=(10, 4))
        upd.bind("<Button-1>", lambda e: self._check_update_bg(True))

        tk.Frame(win, bg=COL_BORDER, height=1).pack(fill="x", padx=pad, pady=14)

        tk.Label(win, text=T("credits"), bg=COL_SURFACE, fg=COL_TEXT, font=FONT,
                 justify="left").pack(anchor="w", padx=pad)
        tk.Label(win, text=T("version_label", VERSION) + "   ·   " + GITHUB_URL,
                 bg=COL_SURFACE, fg=COL_TEXT_DIM, font=FONT_SM).pack(
            anchor="w", padx=pad, pady=(2, 12))

        tk.Label(win, text=T("donate_label", DONATE_NET), bg=COL_SURFACE,
                 fg=COL_TEXT_DIM, font=FONT_SM).pack(anchor="w", padx=pad)
        drow = tk.Frame(win, bg=COL_SURFACE)
        drow.pack(fill="x", padx=pad, pady=(4, 14))
        ent = tk.Entry(drow, font=FONT_SM, bg=COL_BG, fg=COL_TEXT, relief="flat",
                       readonlybackground=COL_BG, highlightthickness=1,
                       highlightbackground=COL_BORDER)
        ent.insert(0, DONATE_ADDR)
        ent.configure(state="readonly")
        ent.pack(side="left", fill="x", expand=True, ipady=5)

        def copy_addr():
            self.root.clipboard_clear()
            self.root.clipboard_append(DONATE_ADDR)
            cbtn.configure(text=T("copied"))
            self.root.after(1200, lambda: cbtn.configure(text=T("copy")))
        cbtn = self._accent_btn(drow, T("copy"), copy_addr)
        cbtn.pack(side="left", padx=(8, 0))

    def _info(self, title, prompt):
        win = self._dialog(title, 460, 200)
        tk.Label(win, text=prompt, bg=COL_SURFACE, fg=COL_TEXT, font=FONT,
                 justify="left", wraplength=420).pack(anchor="w", padx=18, pady=(20, 10))
        row = tk.Frame(win, bg=COL_SURFACE)
        row.pack(fill="x", padx=18, pady=16)
        self._accent_btn(row, T("ok"), win.destroy).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Return>", lambda e: win.destroy())
        self.root.wait_window(win)

    # ===================================================================
    def run(self):
        self.root.mainloop()


def _parse_launch(argv):
    if "--launch" in argv:
        i = argv.index("--launch")
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main():
    args = sys.argv[1:]
    # штатно закрыть запущенный экземпляр (для деинсталлятора): WM_CLOSE -> _quit,
    # который снимает владение со всех окон, и ждём, пока процесс действительно выйдет
    if "--quit" in args:
        h = user32.FindWindowW(None, IPC_TITLE)
        if h:
            user32.PostMessageW(h, WM_CLOSE, 0, 0)
            for _ in range(40):
                if not user32.FindWindowW(None, IPC_TITLE):
                    break
                time.sleep(0.25)
        return

    launch_name = _parse_launch(args)
    # единственный экземпляр: если уже запущен — отдать ему команду и выйти
    existing = user32.FindWindowW(None, IPC_TITLE)
    if existing:
        if launch_name:
            send_copydata(existing, launch_name)
        return
    # именованный мьютекс держим всё время работы: по нему установщик (AppMutex)
    # понимает, что программа запущена, и закрывает её перед обновлением
    try:
        kernel32.CreateMutexW(None, False,
                              ctypes.c_wchar_p("HDContainer_singleton_mutex"))
    except Exception:
        pass
    TrayApp(launch_name=launch_name).run()


if __name__ == "__main__":
    main()
