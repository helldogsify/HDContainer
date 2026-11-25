# -*- coding: utf-8 -*-
"""LAN link UI: Settings -> Devices, tray rows, the pairing dialogs and the remote file browser.

Every network call runs in a worker thread; results come back through self._ui (a queue
drained by a tk timer). Nothing here touches tk from another thread or from a native
callback (the drop target only stores the paths).
"""
import ctypes
import os
import queue
import socket
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog

import lan
import voice_sys as vs

LSTR = {
    "tab": {"en": "Devices", "ru": "Устройства", "es": "Dispositivos", "pt": "Dispositivos", "de": "Geräte", "fr": "Appareils", "zh": "设备"},
    "enable": {"en": "Link my devices on the local network", "ru": "Связь с моими устройствами в локальной сети", "es": "Conectar mis dispositivos en la red local", "pt": "Conectar meus dispositivos na rede local", "de": "Meine Geräte im lokalen Netz verbinden", "fr": "Relier mes appareils sur le réseau local", "zh": "连接局域网内我的设备"},
    "note": {"en": "Computers with HDContainer on one network find each other. A linked device opens all drives of this computer; everything is encrypted.", "ru": "Компьютеры с HDContainer в одной сети находят друг друга сами. Связанное устройство открывает все диски этого компьютера, всё шифруется.", "es": "Los equipos con HDContainer en la misma red se encuentran solos. Un dispositivo vinculado abre todas las unidades de este equipo; todo va cifrado.", "pt": "Computadores com HDContainer na mesma rede se encontram sozinhos. Um dispositivo vinculado abre todas as unidades deste computador; tudo é criptografado.", "de": "Computer mit HDContainer im selben Netz finden sich selbst. Ein verbundenes Gerät öffnet alle Laufwerke dieses Computers; alles ist verschlüsselt.", "fr": "Les ordinateurs avec HDContainer sur le même réseau se trouvent seuls. Un appareil relié ouvre tous les disques de cet ordinateur ; tout est chiffré.", "zh": "同一网络中装有 HDContainer 的电脑会自动互相发现。已连接的设备可打开本机所有磁盘，全程加密。"},
    "name": {"en": "This computer's name", "ru": "Имя этого компьютера", "es": "Nombre de este equipo", "pt": "Nome deste computador", "de": "Name dieses Computers", "fr": "Nom de cet ordinateur", "zh": "本机名称"},
    "mine": {"en": "My devices", "ru": "Мои устройства", "es": "Mis dispositivos", "pt": "Meus dispositivos", "de": "Meine Geräte", "fr": "Mes appareils", "zh": "我的设备"},
    "nearby": {"en": "Nearby", "ru": "Рядом", "es": "Cerca", "pt": "Por perto", "de": "In der Nähe", "fr": "À proximité", "zh": "附近"},
    "searching": {"en": "Looking for computers with HDContainer on this network…", "ru": "Ищу компьютеры с HDContainer в этой сети…", "es": "Buscando equipos con HDContainer en esta red…", "pt": "Procurando computadores com HDContainer nesta rede…", "de": "Suche Computer mit HDContainer in diesem Netz…", "fr": "Recherche d'ordinateurs avec HDContainer sur ce réseau…", "zh": "正在查找此网络中装有 HDContainer 的电脑…"},
    "online": {"en": "Online", "ru": "В сети", "es": "En línea", "pt": "Online", "de": "Online", "fr": "En ligne", "zh": "在线"},
    "far": {"en": "Via Tailscale", "ru": "Через Tailscale", "es": "Vía Tailscale", "pt": "Via Tailscale", "de": "Über Tailscale", "fr": "Via Tailscale", "zh": "经 Tailscale"},
    "offline": {"en": "Offline", "ru": "Не в сети", "es": "Sin conexión", "pt": "Offline", "de": "Offline", "fr": "Hors ligne", "zh": "离线"},
    "revoked": {"en": "Unlinked on that device", "ru": "Связь отменена на том устройстве", "es": "Desvinculado en ese dispositivo", "pt": "Desvinculado naquele dispositivo", "de": "Auf dem Gerät getrennt", "fr": "Délié sur cet appareil", "zh": "对方已解除连接"},
    "files": {"en": "Files", "ru": "Файлы", "es": "Archivos", "pt": "Arquivos", "de": "Dateien", "fr": "Fichiers", "zh": "文件"},
    "link": {"en": "Link", "ru": "Связать", "es": "Vincular", "pt": "Vincular", "de": "Verbinden", "fr": "Relier", "zh": "连接"},
    "unlink": {"en": "Unlink", "ru": "Отвязать", "es": "Desvincular", "pt": "Desvincular", "de": "Trennen", "fr": "Délier", "zh": "解除"},
    "unlink_t": {"en": "Unlink device", "ru": "Отвязать устройство", "es": "Desvincular dispositivo", "pt": "Desvincular dispositivo", "de": "Gerät trennen", "fr": "Délier l'appareil", "zh": "解除设备"},
    "unlink_q": {"en": "Unlink “%s”? It loses access to this computer, and this computer to it.", "ru": "Отвязать «%s»? Оно потеряет доступ к этому компьютеру, а этот компьютер — к нему.", "es": "¿Desvincular «%s»? Perderá el acceso a este equipo y este equipo a él.", "pt": "Desvincular “%s”? Ele perde o acesso a este computador, e este computador a ele.", "de": "„%s“ trennen? Es verliert den Zugriff auf diesen Computer und dieser auf es.", "fr": "Délier « %s » ? Il perd l'accès à cet ordinateur, et cet ordinateur à lui.", "zh": "解除“%s”？它将无法访问本机，本机也无法访问它。"},
    "pair_t": {"en": "Link with %s", "ru": "Связь с %s", "es": "Vincular con %s", "pt": "Vincular com %s", "de": "Mit %s verbinden", "fr": "Relier à %s", "zh": "连接 %s"},
    "connecting": {"en": "Connecting…", "ru": "Соединяюсь…", "es": "Conectando…", "pt": "Conectando…", "de": "Verbinde…", "fr": "Connexion…", "zh": "正在连接…"},
    "pair_check": {"en": "Check that %s shows the same code and press «Link» there.", "ru": "Проверьте, что на %s тот же код, и нажмите там «Связать».", "es": "Compruebe que %s muestra el mismo código y pulse «Vincular» allí.", "pt": "Confira se %s mostra o mesmo código e toque em «Vincular» lá.", "de": "Prüfen Sie, dass %s denselben Code zeigt, und drücken Sie dort „Verbinden“.", "fr": "Vérifiez que %s affiche le même code et appuyez sur « Relier » là-bas.", "zh": "请确认 %s 上显示相同的代码，并在那里点击“连接”。"},
    "pair_done": {"en": "Linked", "ru": "Связано", "es": "Vinculado", "pt": "Vinculado", "de": "Verbunden", "fr": "Relié", "zh": "已连接"},
    "pair_declined": {"en": "%s declined the request.", "ru": "%s отклонил запрос.", "es": "%s rechazó la solicitud.", "pt": "%s recusou o pedido.", "de": "%s hat abgelehnt.", "fr": "%s a refusé la demande.", "zh": "%s 拒绝了请求。"},
    "pair_busy": {"en": "%s is answering another request. Try again in a minute.", "ru": "%s сейчас отвечает на другой запрос. Попробуйте через минуту.", "es": "%s está atendiendo otra solicitud. Inténtelo en un minuto.", "pt": "%s está respondendo outro pedido. Tente em um minuto.", "de": "%s beantwortet gerade eine andere Anfrage. Gleich noch einmal versuchen.", "fr": "%s répond à une autre demande. Réessayez dans une minute.", "zh": "%s 正在处理另一个请求，请稍后再试。"},
    "pair_timeout": {"en": "No answer from %s.", "ru": "%s не ответил.", "es": "%s no respondió.", "pt": "%s não respondeu.", "de": "Keine Antwort von %s.", "fr": "Pas de réponse de %s.", "zh": "%s 没有回应。"},
    "pair_fail": {"en": "Could not connect to %s.", "ru": "Не удалось соединиться с %s.", "es": "No se pudo conectar con %s.", "pt": "Não foi possível conectar a %s.", "de": "Keine Verbindung zu %s.", "fr": "Connexion à %s impossible.", "zh": "无法连接 %s。"},
    "ask_t": {"en": "Link request", "ru": "Запрос на связь", "es": "Solicitud de vínculo", "pt": "Pedido de vínculo", "de": "Verbindungsanfrage", "fr": "Demande de liaison", "zh": "连接请求"},
    "ask_q": {"en": "%s (%s) wants to link with this computer and open all of its drives.", "ru": "%s (%s) хочет связаться с этим компьютером и открыть все его диски.", "es": "%s (%s) quiere vincularse con este equipo y abrir todas sus unidades.", "pt": "%s (%s) quer se vincular a este computador e abrir todas as suas unidades.", "de": "%s (%s) möchte sich mit diesem Computer verbinden und alle Laufwerke öffnen.", "fr": "%s (%s) veut se relier à cet ordinateur et ouvrir tous ses disques.", "zh": "%s（%s）想连接本机并打开其所有磁盘。"},
    "ask_hint": {"en": "Link only if %s shows the same code.", "ru": "Связывайте, только если на %s тот же код.", "es": "Vincule solo si %s muestra el mismo código.", "pt": "Vincule só se %s mostrar o mesmo código.", "de": "Nur verbinden, wenn %s denselben Code zeigt.", "fr": "Reliez seulement si %s affiche le même code.", "zh": "仅当 %s 显示相同代码时才连接。"},
    "decline": {"en": "Decline", "ru": "Отклонить", "es": "Rechazar", "pt": "Recusar", "de": "Ablehnen", "fr": "Refuser", "zh": "拒绝"},
    "close": {"en": "Close", "ru": "Закрыть", "es": "Cerrar", "pt": "Fechar", "de": "Schließen", "fr": "Fermer", "zh": "关闭"},
    "cancel": {"en": "Cancel", "ru": "Отмена", "es": "Cancelar", "pt": "Cancelar", "de": "Abbrechen", "fr": "Annuler", "zh": "取消"},
    "fw_note": {"en": "Windows Firewall may keep other devices out. Allow HDContainer once.", "ru": "Брандмауэр Windows может не пускать другие устройства. Разрешите HDContainer один раз.", "es": "El firewall de Windows puede bloquear otros dispositivos. Permita HDContainer una vez.", "pt": "O firewall do Windows pode bloquear outros dispositivos. Permita o HDContainer uma vez.", "de": "Die Windows-Firewall kann andere Geräte aussperren. HDContainer einmal zulassen.", "fr": "Le pare-feu Windows peut bloquer les autres appareils. Autorisez HDContainer une fois.", "zh": "Windows 防火墙可能阻止其他设备访问。请允许 HDContainer 一次。"},
    "fw_allow": {"en": "Allow", "ru": "Разрешить", "es": "Permitir", "pt": "Permitir", "de": "Zulassen", "fr": "Autoriser", "zh": "允许"},
    # browser
    "this_pc": {"en": "This PC", "ru": "Этот компьютер", "es": "Este equipo", "pt": "Este computador", "de": "Dieser PC", "fr": "Ce PC", "zh": "此电脑"},
    "desktop": {"en": "Desktop", "ru": "Рабочий стол", "es": "Escritorio", "pt": "Área de trabalho", "de": "Desktop", "fr": "Bureau", "zh": "桌面"},
    "downloads": {"en": "Downloads", "ru": "Загрузки", "es": "Descargas", "pt": "Downloads", "de": "Downloads", "fr": "Téléchargements", "zh": "下载"},
    "documents": {"en": "Documents", "ru": "Документы", "es": "Documentos", "pt": "Documentos", "de": "Dokumente", "fr": "Documents", "zh": "文档"},
    "home": {"en": "User folder", "ru": "Папка пользователя", "es": "Carpeta personal", "pt": "Pasta do usuário", "de": "Benutzerordner", "fr": "Dossier personnel", "zh": "用户文件夹"},
    "drives": {"en": "Drives", "ru": "Диски", "es": "Unidades", "pt": "Unidades", "de": "Laufwerke", "fr": "Disques", "zh": "磁盘"},
    "col_name": {"en": "Name", "ru": "Имя", "es": "Nombre", "pt": "Nome", "de": "Name", "fr": "Nom", "zh": "名称"},
    "col_date": {"en": "Modified", "ru": "Изменён", "es": "Modificado", "pt": "Modificado", "de": "Geändert", "fr": "Modifié", "zh": "修改时间"},
    "col_size": {"en": "Size", "ru": "Размер", "es": "Tamaño", "pt": "Tamanho", "de": "Größe", "fr": "Taille", "zh": "大小"},
    "col_free": {"en": "Free", "ru": "Свободно", "es": "Libre", "pt": "Livre", "de": "Frei", "fr": "Libre", "zh": "可用"},
    "col_total": {"en": "Total", "ru": "Всего", "es": "Total", "pt": "Total", "de": "Gesamt", "fr": "Total", "zh": "总计"},
    "free_of": {"en": "%s free of %s", "ru": "%s свободно из %s", "es": "%s libres de %s", "pt": "%s livres de %s", "de": "%s von %s frei", "fr": "%s libres sur %s", "zh": "可用 %s，共 %s"},
    "loading": {"en": "Loading…", "ru": "Загрузка…", "es": "Cargando…", "pt": "Carregando…", "de": "Laden…", "fr": "Chargement…", "zh": "正在加载…"},
    "empty": {"en": "This folder is empty", "ru": "Папка пуста", "es": "Esta carpeta está vacía", "pt": "Esta pasta está vazia", "de": "Dieser Ordner ist leer", "fr": "Ce dossier est vide", "zh": "此文件夹为空"},
    "items": {"en": "Items: %d", "ru": "Объектов: %d", "es": "Elementos: %d", "pt": "Itens: %d", "de": "Elemente: %d", "fr": "Éléments : %d", "zh": "项目：%d"},
    "selected": {"en": "Selected: %d", "ru": "Выбрано: %d", "es": "Seleccionados: %d", "pt": "Selecionados: %d", "de": "Ausgewählt: %d", "fr": "Sélection : %d", "zh": "已选：%d"},
    "dev_offline": {"en": "%s is offline", "ru": "%s не в сети", "es": "%s está sin conexión", "pt": "%s está offline", "de": "%s ist offline", "fr": "%s est hors ligne", "zh": "%s 离线"},
    "retry": {"en": "Retry", "ru": "Повторить", "es": "Reintentar", "pt": "Tentar de novo", "de": "Erneut versuchen", "fr": "Réessayer", "zh": "重试"},
    "open": {"en": "Open", "ru": "Открыть", "es": "Abrir", "pt": "Abrir", "de": "Öffnen", "fr": "Ouvrir", "zh": "打开"},
    "download": {"en": "Download…", "ru": "Скачать…", "es": "Descargar…", "pt": "Baixar…", "de": "Herunterladen…", "fr": "Télécharger…", "zh": "下载…"},
    "rename": {"en": "Rename", "ru": "Переименовать", "es": "Cambiar nombre", "pt": "Renomear", "de": "Umbenennen", "fr": "Renommer", "zh": "重命名"},
    "delete": {"en": "Delete", "ru": "Удалить", "es": "Eliminar", "pt": "Excluir", "de": "Löschen", "fr": "Supprimer", "zh": "删除"},
    "up_files": {"en": "Upload files…", "ru": "Загрузить файлы…", "es": "Subir archivos…", "pt": "Enviar arquivos…", "de": "Dateien hochladen…", "fr": "Envoyer des fichiers…", "zh": "上传文件…"},
    "up_folder": {"en": "Upload a folder…", "ru": "Загрузить папку…", "es": "Subir una carpeta…", "pt": "Enviar uma pasta…", "de": "Ordner hochladen…", "fr": "Envoyer un dossier…", "zh": "上传文件夹…"},
    "new_folder": {"en": "New folder", "ru": "Новая папка", "es": "Nueva carpeta", "pt": "Nova pasta", "de": "Neuer Ordner", "fr": "Nouveau dossier", "zh": "新建文件夹"},
    "show_hidden": {"en": "Show hidden", "ru": "Показывать скрытые", "es": "Mostrar ocultos", "pt": "Mostrar ocultos", "de": "Versteckte anzeigen", "fr": "Afficher les masqués", "zh": "显示隐藏项"},
    "refresh": {"en": "Refresh", "ru": "Обновить", "es": "Actualizar", "pt": "Atualizar", "de": "Aktualisieren", "fr": "Actualiser", "zh": "刷新"},
    "back": {"en": "Back", "ru": "Назад", "es": "Atrás", "pt": "Voltar", "de": "Zurück", "fr": "Retour", "zh": "后退"},
    "up": {"en": "Up", "ru": "Вверх", "es": "Subir nivel", "pt": "Acima", "de": "Nach oben", "fr": "Dossier parent", "zh": "向上"},
    "upload": {"en": "Upload", "ru": "Загрузить", "es": "Subir", "pt": "Enviar", "de": "Hochladen", "fr": "Envoyer", "zh": "上传"},
    "folder_q": {"en": "Folder name", "ru": "Имя папки", "es": "Nombre de la carpeta", "pt": "Nome da pasta", "de": "Ordnername", "fr": "Nom du dossier", "zh": "文件夹名称"},
    "rename_q": {"en": "New name", "ru": "Новое имя", "es": "Nuevo nombre", "pt": "Novo nome", "de": "Neuer Name", "fr": "Nouveau nom", "zh": "新名称"},
    "delete_t": {"en": "Delete", "ru": "Удаление", "es": "Eliminar", "pt": "Excluir", "de": "Löschen", "fr": "Supprimer", "zh": "删除"},
    "delete_q": {"en": "Delete %(n)d item(s) on %(dev)s? They go to its Recycle Bin.", "ru": "Удалить на %(dev)s объектов: %(n)d? Они попадут в корзину того компьютера.", "es": "¿Eliminar %(n)d elemento(s) en %(dev)s? Irán a su Papelera.", "pt": "Excluir %(n)d item(ns) em %(dev)s? Eles vão para a Lixeira de lá.", "de": "%(n)d Element(e) auf %(dev)s löschen? Sie kommen in dessen Papierkorb.", "fr": "Supprimer %(n)d élément(s) sur %(dev)s ? Ils iront dans sa Corbeille.", "zh": "删除 %(dev)s 上的 %(n)d 项？它们会进入该电脑的回收站。"},
    "xf_down": {"en": "Downloading", "ru": "Скачивание", "es": "Descargando", "pt": "Baixando", "de": "Herunterladen", "fr": "Téléchargement", "zh": "正在下载"},
    "xf_up": {"en": "Uploading", "ru": "Отправка", "es": "Subiendo", "pt": "Enviando", "de": "Hochladen", "fr": "Envoi", "zh": "正在上传"},
    "xf_open": {"en": "Opening", "ru": "Открываю", "es": "Abriendo", "pt": "Abrindo", "de": "Öffne", "fr": "Ouverture", "zh": "正在打开"},
    "xf_downloaded": {"en": "Downloaded · Show", "ru": "Скачано · Показать", "es": "Descargado · Mostrar", "pt": "Baixado · Mostrar", "de": "Heruntergeladen · Zeigen", "fr": "Téléchargé · Afficher", "zh": "已下载 · 显示"},
    "xf_uploaded": {"en": "Uploaded", "ru": "Отправлено", "es": "Subido", "pt": "Enviado", "de": "Hochgeladen", "fr": "Envoyé", "zh": "已上传"},
    "xf_cancelled": {"en": "Cancelled", "ru": "Отменено", "es": "Cancelado", "pt": "Cancelado", "de": "Abgebrochen", "fr": "Annulé", "zh": "已取消"},
    "queue": {"en": "+%d queued", "ru": "ещё %d в очереди", "es": "+%d en cola", "pt": "+%d na fila", "de": "+%d in Warteschlange", "fr": "+%d en attente", "zh": "另有 %d 个排队"},
    "err_denied": {"en": "Access denied", "ru": "Нет доступа", "es": "Acceso denegado", "pt": "Acesso negado", "de": "Zugriff verweigert", "fr": "Accès refusé", "zh": "拒绝访问"},
    "err_missing": {"en": "Not found", "ru": "Не найдено", "es": "No encontrado", "pt": "Não encontrado", "de": "Nicht gefunden", "fr": "Introuvable", "zh": "未找到"},
    "err_exists": {"en": "Already exists", "ru": "Уже существует", "es": "Ya existe", "pt": "Já existe", "de": "Existiert bereits", "fr": "Existe déjà", "zh": "已存在"},
    "err_conn": {"en": "Connection lost", "ru": "Связь прервалась", "es": "Conexión perdida", "pt": "Conexão perdida", "de": "Verbindung verloren", "fr": "Connexion perdue", "zh": "连接中断"},
    "err_many": {"en": "Too many files", "ru": "Слишком много файлов", "es": "Demasiados archivos", "pt": "Arquivos demais", "de": "Zu viele Dateien", "fr": "Trop de fichiers", "zh": "文件过多"},
    "err_auth": {"en": "The link was cancelled on that device", "ru": "Связь отменена на том устройстве", "es": "El vínculo se canceló en ese dispositivo", "pt": "O vínculo foi cancelado naquele dispositivo", "de": "Die Verbindung wurde auf dem Gerät getrennt", "fr": "La liaison a été annulée sur cet appareil", "zh": "对方已解除连接"},
    "b": {"en": "B", "ru": "Б", "es": "B", "pt": "B", "de": "B", "fr": "o", "zh": "B"},
    "kb": {"en": "KB", "ru": "КБ", "es": "KB", "pt": "KB", "de": "KB", "fr": "Ko", "zh": "KB"},
    "mb": {"en": "MB", "ru": "МБ", "es": "MB", "pt": "MB", "de": "MB", "fr": "Mo", "zh": "MB"},
    "gb": {"en": "GB", "ru": "ГБ", "es": "GB", "pt": "GB", "de": "GB", "fr": "Go", "zh": "GB"},
    "tb": {"en": "TB", "ru": "ТБ", "es": "TB", "pt": "TB", "de": "TB", "fr": "To", "zh": "TB"},
    "per_s": {"en": "/s", "ru": "/с", "es": "/s", "pt": "/s", "de": "/s", "fr": "/s", "zh": "/秒"},
}

LANG_GET = lambda: "en"
DECIMAL_COMMA = ("ru", "es", "pt", "de", "fr")
DEFAULTS = {"enabled": False, "name": ""}

ICON_FONT = "Segoe MDL2 Assets"
G_BACK, G_UP, G_REFRESH, G_UPLOAD, G_NEWFOLDER = chr(0xE72B), chr(0xE74A), chr(0xE72C), chr(0xE898), chr(0xE8F4)
G_CLOSE, G_DESKTOP, G_DOWNLOADS, G_DOCS, G_HOME, G_DRIVE = (chr(0xE711), chr(0xE7F4), chr(0xE896),
                                                              chr(0xE8A5), chr(0xE80F), chr(0xEDA2))
G_PC = chr(0xE7F8)
COL_ONLINE, COL_OFF = "#34c759", "#5f6368"
COL_SEL = "#2b4370"


def L(key, *args, **kw):
    s = LSTR.get(key, {})
    txt = s.get(LANG_GET()) or s.get("en") or key
    if kw:
        return txt % kw
    return (txt % args) if args else txt


def _num(x):
    s = ("%.1f" % x) if x < 100 else ("%d" % round(x))
    if s.endswith(".0"):
        s = s[:-2]
    return s.replace(".", ",") if LANG_GET() in DECIMAL_COMMA else s


def fmt_size(n):
    n = float(n or 0)
    for unit in ("b", "kb", "mb", "gb"):
        if n < 1024:
            return "%s %s" % (_num(n) if unit != "b" else "%d" % n, L(unit))
        n /= 1024.0
    return "%s %s" % (_num(n), L("tb"))


def fmt_date(t):
    if not t:
        return ""
    lt = time.localtime(t)
    return time.strftime("%Y-%m-%d %H:%M" if LANG_GET() in ("en", "zh") else "%d.%m.%Y %H:%M", lt)


def _C(name, default):
    return getattr(sys.modules.get("__main__"), name, default)


def colors():
    return {"bg": _C("COL_BG", "#1b1b1d"), "sf": _C("COL_SURFACE", "#232427"), "sf2": _C("COL_SURFACE2", "#2d2e31"),
            "border": _C("COL_BORDER", "#2a2b2c"), "hover": _C("COL_HOVER", "#303236"),
            "accent": _C("COL_ACCENT", "#4c8bf5"), "accent_hi": _C("COL_ACCENT_HI", "#629bff"),
            "tx": _C("COL_TEXT", "#e6e8ea"), "dim": _C("COL_TEXT_DIM", "#9aa0a6"),
            "danger": _C("COL_DANGER", "#ff453a"),
            "font": _C("FONT", ("Segoe UI", 10)), "font_sm": _C("FONT_SM", ("Segoe UI", 9)),
            "font_title": _C("FONT_TITLE", ("Segoe UI Semibold", 10))}


def err_text(ex):
    code = getattr(ex, "code", "")
    if code in ("denied",) or isinstance(ex, PermissionError):
        return L("err_denied")
    if code in ("missing",) or isinstance(ex, FileNotFoundError):
        return L("err_missing")
    if code == "exists" or isinstance(ex, FileExistsError):
        return L("err_exists")
    if code == "too_many":
        return L("err_many")
    if code in ("auth", "unpaired"):
        return L("err_auth")
    if code in ("closed", "offline", "proto", "timeout") or isinstance(ex, (OSError, lan.lc.AuthError)):
        return L("err_conn")
    return str(ex) or type(ex).__name__


def _conn_level(ex):
    """True when the channel itself is broken (reconnect), False for an answer of the other side."""
    if isinstance(ex, lan.LinkError):
        return ex.code in ("closed", "proto", "offline", "timeout")
    return isinstance(ex, (OSError, lan.lc.AuthError, ValueError))


def unique_name(exists, name):
    if not exists(name):
        return name
    stem, ext = os.path.splitext(name)
    i = 2
    while exists("%s (%d)%s" % (stem, i, ext)):
        i += 1
    return "%s (%d)%s" % (stem, i, ext)


def _flat_btn(parent, text, cmd, c, accent=False, danger=False, small=True):
    bg = c["accent"] if accent else c["sf2"]
    fg = "white" if accent else c["tx"]
    b = tk.Label(parent, text=text, bg=bg, fg=fg, font=c["font_sm"] if small else c["font"],
                 cursor="hand2", padx=12 if small else 14, pady=4 if small else 7)

    def enter(_e):
        b.configure(bg=c["accent_hi"] if accent else c["hover"], fg=c["danger"] if danger else fg)

    def leave(_e):
        b.configure(bg=bg, fg=fg)
    b.bind("<Enter>", enter)
    b.bind("<Leave>", leave)
    b.bind("<Button-1>", lambda e: cmd())
    return b


def _dot(parent, color, bg, d=8):
    cv = tk.Canvas(parent, width=d + 2, height=d + 2, bg=bg, highlightthickness=0, bd=0)
    cv.create_oval(1, 1, d + 1, d + 1, fill=color, outline="")
    return cv


class Tooltip:
    def __init__(self, widget, text, c):
        self.w, self.text, self.c, self.tip, self.job = widget, text, c, None, None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<Button-1>", self._leave, add="+")

    def _enter(self, _e):
        self.job = self.w.after(550, self._show)

    def _leave(self, _e=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None

    def _show(self):
        self.job = None
        if not self.w.winfo_exists():
            return
        t = tk.Toplevel(self.w)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg=self.c["border"])
        tk.Label(t, text=self.text, bg=self.c["sf2"], fg=self.c["tx"], font=self.c["font_sm"],
                 padx=8, pady=3).pack(padx=1, pady=1)
        x = self.w.winfo_rootx()
        y = self.w.winfo_rooty() + self.w.winfo_height() + 4
        t.geometry("+%d+%d" % (x, y))
        self.tip = t


class PopupMenu:
    """A dark menu in the style of the tray menu: [(text, cmd, opts)] or None for a separator."""
    def __init__(self, root, x, y, items, c):
        w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=c["border"])
        fr = tk.Frame(w, bg=c["sf"])
        fr.pack(padx=1, pady=1)
        self.w = w

        def close(_e=None):
            try:
                w.destroy()
            except Exception:
                pass
        for it in items:
            if it is None:
                tk.Frame(fr, bg=c["border"], height=1).pack(fill="x", padx=10, pady=4)
                continue
            text, cmd = it[0], it[1]
            opts = it[2] if len(it) > 2 else {}
            row = tk.Frame(fr, bg=c["sf"])
            row.pack(fill="x")
            mark = tk.Label(row, text=chr(0xE73E) if opts.get("checked") else "", bg=c["sf"], fg=c["accent"],
                            font=(ICON_FONT, 9), width=2)
            mark.pack(side="left", padx=(8, 0))
            fg = c["danger"] if opts.get("danger") else c["tx"]
            lbl = tk.Label(row, text=text, bg=c["sf"], fg=fg, font=c["font"], anchor="w", padx=6, pady=5)
            lbl.pack(side="left", fill="x", expand=True, ipadx=0)
            tk.Frame(row, bg=c["sf"], width=18).pack(side="right")
            parts = (row, mark, lbl)

            def enter(_e, parts=parts):
                for p in parts:
                    p.configure(bg=c["hover"])

            def leave(_e, parts=parts):
                for p in parts:
                    p.configure(bg=c["sf"])

            def click(_e, cmd=cmd):
                close()
                root.after(1, cmd)
            for p in parts:
                p.bind("<Enter>", enter)
                p.bind("<Leave>", leave)
                p.bind("<ButtonRelease-1>", click)
        w.update_idletasks()
        ww, hh = w.winfo_reqwidth(), w.winfo_reqheight()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        x = min(x, w.winfo_vrootx() + w.winfo_vrootwidth() - ww - 4) if x + ww > sw else x
        y = y - hh if y + hh > sh else y
        w.geometry("+%d+%d" % (x, y))
        w.focus_force()
        w.bind("<Escape>", close)
        w.after(200, lambda: w.winfo_exists() and w.bind("<FocusOut>", close))


# ---------------------------------------------------------------------------
class LanController:
    def __init__(self, app, get_lang, log, data_dir, local_dir, version):
        global LANG_GET
        LANG_GET = get_lang
        self.app, self.root, self.log = app, app.root, log
        self.data_dir, self.local_dir, self.version = data_dir, local_dir, version
        self.node = None
        self.draft = None
        self._page = None
        self._sig = None
        self._fw = None                    # None unknown / True rule present / False missing
        self._browsers = {}
        self._asks = []                    # (PairRequest, Toplevel)
        self._ui_q = vs.WakeQueue()
        self._tray_sig = None
        self._ticking = self._draining = False
        app.on_wake(self._drain)           # queued work and node events wake it
        if self.saved("enabled"):
            self._start()

    # ---- settings
    def saved(self, k):
        return self.app.settings.get("lan", {}).get(k, DEFAULTS[k])

    def get(self, k):
        if self.draft is not None and k in self.draft:
            return self.draft[k]
        return self.saved(k)

    def device_name(self):
        return (self.saved("name") or "").strip() or socket.gethostname()

    def begin_edit(self):
        self.draft = {"name": self.saved("name")}

    def discard(self):
        self.draft = None

    def commit(self):
        new, self.draft = self.draft, None
        if new is None:
            return
        s = dict(self.app.settings.get("lan", {}))
        s["name"] = (new.get("name") or "").strip()
        self.app._set_setting("lan", s, user=True)

    def set_enabled(self, on):
        s = dict(self.app.settings.get("lan", {}))
        s["enabled"] = bool(on)
        self.app._set_setting("lan", s, user=True)
        if on:
            self._start()
            self._check_firewall(ask=True)
        else:
            self._stop()
        self._refresh_page()

    def _start(self):
        if self.node is None:
            try:
                self.node = lan.Node(self.data_dir, self.log, self.device_name, self.version)
            except Exception:
                self.log("lan init failed:\n" + traceback.format_exc())
                return
            self.node.events = vs.WakeQueue()
        try:
            self.node.start()
        except OSError as ex:
            self.log("lan start failed: %r" % ex)
        self._check_firewall(ask=False)
        self._tick_on()

    def _stop(self):
        if self.node is not None:
            self.node.stop()
        for b in list(self._browsers.values()):
            b.close()

    def shutdown(self):
        self._stop()

    def _exe(self):
        return sys.executable if not getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0])

    def _check_firewall(self, ask):
        def work():
            ok = lan.firewall_rule_present()
            if not ok and ask:
                ok = lan.firewall_allow(self._exe(), self.log)
            self._ui(lambda: self._fw_result(ok))
        threading.Thread(target=work, daemon=True, name="lan-fw").start()

    def _fw_result(self, ok):
        changed = self._fw != ok
        self._fw = ok
        if changed:
            self._refresh_page()

    def _ui(self, fn):
        self._ui_q.put(fn)

    # ---- timer: only while the link runs (devices go offline by time, without an event)
    def _tick_on(self):
        if not self._ticking:
            self._ticking = True
            self.root.after(500, self._tick)

    def _tick(self):
        self._drain()
        if self._asks:
            self.root.after(500, self._tick)
        elif self.node is not None and self.node.running:
            self.root.after(3000, self._tick)       # a device unheard for 10 s turns offline
        else:
            self._ticking = False

    def _drain(self):
        if self._draining:                 # a dialog opened from here pumps events
            return
        self._draining = True
        try:
            while True:
                try:
                    fn = self._ui_q.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:
                    self.log("lan ui task failed:\n" + traceback.format_exc())
            if self.node is not None:
                while True:
                    try:
                        kind, data = self.node.events.get_nowait()
                    except queue.Empty:
                        break
                    if kind == "pair_ask":
                        self._show_ask(data)
                for req, win in list(self._asks):
                    if req.closed or req.decided.is_set():
                        self._asks.remove((req, win))
                        try:
                            win.destroy()
                        except Exception:
                            pass
                sig = self._snap_sig()
                if sig != self._sig:
                    self._sig = sig
                    self._refresh_page()
                    for b in list(self._browsers.values()):
                        b.on_status()
                    if self._tray_sig is not None and self._tray_sig != self._tray_part(sig):
                        self._tray_sig = None
                        self.app._repopup()
        except Exception:
            self.log("lan tick failed:\n" + traceback.format_exc())
        finally:
            self._draining = False

    def snapshot(self):
        if self.node is None or not self.node.running:
            return []
        return self.node.snapshot()

    def _snap_sig(self):
        return tuple((d["id"], d["name"], d["paired"], d["online"], d["route"], d["revoked"], d["ip"])
                     for d in self.snapshot())

    @staticmethod
    def _tray_part(sig):
        return tuple(x[:6] for x in sig if x[2])

    def peer(self, pid):
        return next((d for d in self.snapshot() if d["id"] == pid), None)

    # ---- settings page
    def build_into(self, parent, win, pad):
        c = colors()
        if self.draft is None:
            self.begin_edit()
        d = self.draft
        self._page = {"parent": parent, "win": win, "pad": pad}
        self.app._setting_check(parent, L("enable"), self.saved("enabled"), self.set_enabled, pad)
        tk.Label(parent, text=L("note"), bg=c["sf"], fg=c["dim"], font=c["font_sm"], justify="left",
                 wraplength=560, anchor="w").pack(fill="x", padx=pad + 2, pady=(0, 6))
        tk.Label(parent, text=L("name"), bg=c["sf"], fg=c["dim"], font=c["font_sm"]).pack(
            anchor="w", padx=pad, pady=(16, 4))
        var = tk.StringVar(value=d.get("name") or socket.gethostname())
        ent = tk.Entry(parent, textvariable=var, font=c["font"], bg=c["bg"], fg=c["tx"], insertbackground=c["tx"],
                       relief="flat", highlightthickness=1, highlightbackground=c["border"],
                       highlightcolor=c["accent"])
        ent.pack(fill="x", padx=pad, ipady=5)

        def on_name(*_a):
            v = var.get().strip()
            d["name"] = "" if v == socket.gethostname() else v[:64]
        var.trace_add("write", on_name)
        box = tk.Frame(parent, bg=c["sf"])
        box.pack(fill="x")
        self._page["box"] = box
        self._fill_page()

    def _refresh_page(self):
        p = self._page
        if not p:
            return
        try:
            if not p["box"].winfo_exists():
                self._page = None
                return
        except Exception:
            self._page = None
            return
        for w in p["box"].winfo_children():
            w.destroy()
        self._fill_page()

    def _fill_page(self):
        c = colors()
        p = self._page
        box, pad = p["box"], p["pad"]
        if not self.saved("enabled"):
            return
        if self._fw is False:
            fw = tk.Frame(box, bg=c["bg"])
            fw.pack(fill="x", padx=pad, pady=(16, 0))
            tk.Label(fw, text=L("fw_note"), bg=c["bg"], fg=c["tx"], font=c["font_sm"], justify="left",
                     wraplength=440, anchor="w").pack(side="left", fill="x", expand=True, padx=12, pady=10)
            _flat_btn(fw, L("fw_allow"), lambda: self._check_firewall(ask=True), c, accent=True).pack(
                side="right", padx=10)
        snap = self.snapshot()
        paired = [x for x in snap if x["paired"]]
        near = [x for x in snap if not x["paired"]]
        if paired:
            self._caption(box, L("mine"), pad, c)
            for x in paired:
                self._row(box, x, pad, c)
        self._caption(box, L("nearby"), pad, c)
        if near:
            for x in near:
                self._row(box, x, pad, c)
        else:
            tk.Label(box, text=L("searching"), bg=c["sf"], fg=c["dim"], font=c["font_sm"], anchor="w").pack(
                fill="x", padx=pad + 2, pady=(2, 0))

    @staticmethod
    def _caption(parent, text, pad, c):
        tk.Label(parent, text=text.upper(), bg=c["sf"], fg=c["dim"], font=("Segoe UI Semibold", 8),
                 anchor="w").pack(fill="x", padx=pad + 2, pady=(20, 6))

    def _row(self, parent, x, pad, c):
        row = tk.Frame(parent, bg=c["bg"])
        row.pack(fill="x", padx=pad, pady=2)
        _dot(row, COL_ONLINE if x["online"] else COL_OFF, c["bg"]).pack(side="left", padx=(12, 10), pady=12)
        txt = tk.Frame(row, bg=c["bg"])
        txt.pack(side="left", fill="x", expand=True, pady=6)
        tk.Label(txt, text=x["name"], bg=c["bg"], fg=c["tx"], font=c["font"], anchor="w").pack(fill="x")
        if x["paired"] and x["revoked"]:
            sub = L("revoked")
        elif not x["online"]:
            sub = L("offline")
        elif x["route"] == "far":
            sub = L("far")
        else:
            sub = "%s · %s" % (L("online"), x["ip"]) if x["paired"] else x["ip"]
        tk.Label(txt, text=sub, bg=c["bg"], fg=c["dim"], font=c["font_sm"], anchor="w").pack(fill="x")
        btns = tk.Frame(row, bg=c["bg"])
        btns.pack(side="right", padx=10)
        pid, name = x["id"], x["name"]
        if x["paired"]:
            _flat_btn(btns, L("unlink"), lambda: self._unlink(pid, name), c, danger=True).pack(side="right")
            if x["revoked"]:
                if x["route"] == "lan" or self._seen_on_lan(pid):
                    _flat_btn(btns, L("link"), lambda: self.pair(pid, name), c, accent=True).pack(
                        side="right", padx=(0, 6))
            elif x["online"]:
                _flat_btn(btns, L("files"), lambda: self.open_browser(pid), c, accent=True).pack(
                    side="right", padx=(0, 6))
        else:
            _flat_btn(btns, L("link"), lambda: self.pair(pid, name), c, accent=True).pack(side="right")

    def _seen_on_lan(self, pid):
        n = self.node
        s = n.seen.get(pid) if n else None
        return bool(s and time.time() - s["t"] < lan.ONLINE_S)

    def _unlink(self, pid, name):
        if not self.app._ask_yesno(L("unlink_t"), L("unlink_q", name)):
            return
        b = self._browsers.get(pid)
        if b:
            b.close()
        node = self.node
        threading.Thread(target=lambda: node.unpair(pid), daemon=True, name="lan-unpair").start()

    # ---- pairing: this side asks
    def pair(self, pid, name):
        if self.node is None:
            return
        PairDialog(self, pid, name)

    # ---- pairing: the other side asks us
    def _show_ask(self, req):
        self._tick_on()
        c = colors()
        app = self.app
        win = app._dialog(L("ask_t"), 440, 262)
        win.protocol("WM_DELETE_WINDOW", lambda: answer(False))
        tk.Label(win, text=L("ask_q", req.name or "?", req.ip), bg=c["sf"], fg=c["tx"], font=c["font"],
                 justify="left", wraplength=400, anchor="w").pack(fill="x", padx=20, pady=(20, 6))
        tk.Label(win, text=_code(req.code), bg=c["sf"], fg=c["tx"], font=("Segoe UI Semibold", 30)).pack(pady=(8, 2))
        tk.Label(win, text=L("ask_hint", req.name or "?"), bg=c["sf"], fg=c["dim"], font=c["font_sm"],
                 wraplength=400).pack(padx=20)
        row = tk.Frame(win, bg=c["sf"])
        row.pack(side="bottom", fill="x", padx=20, pady=16)

        def answer(yes):
            req.answer(yes)
            try:
                win.destroy()
            except Exception:
                pass
        app._accent_btn(row, L("link"), lambda: answer(True)).pack(side="right")
        app._ghost_btn(row, L("decline"), lambda: answer(False)).pack(side="right", padx=(0, 8))
        win.bind("<Escape>", lambda e: answer(False))
        self._asks.append((req, win))
        self.log("lan: link request from %s (%s), code %s" % (req.name, req.ip, req.code))

    def accept_pending(self, code):
        """--lan accept CODE: answer the open request from the command line (same check as the eyes)."""
        for req, _w in list(self._asks):
            if req.code == code.replace(" ", ""):
                req.answer(True)
                return True
        self.log("lan: no pending request with that code")
        return False

    # ---- tray
    def build_popup(self, parent, width, close):
        c = colors()
        paired = [x for x in self.snapshot() if x["paired"]]
        self._tray_sig = self._tray_part(self._snap_sig())
        for x in paired:
            self._tray_row(parent, x, width, close, c)

    def has_tray_rows(self):
        return any(x["paired"] for x in self.snapshot())

    def _tray_row(self, parent, x, width, close, c):
        row = tk.Frame(parent, bg=c["sf"], height=32, width=width)
        row.pack(fill="x")
        row.pack_propagate(False)
        dot = _dot(row, COL_ONLINE if x["online"] else COL_OFF, c["sf"])
        dot.pack(side="left", padx=(20, 12))
        trail = L("far") if x["route"] == "far" else ("" if x["online"] else L("offline"))
        if x["revoked"]:
            trail = L("unlink")
        tr = tk.Label(row, text=trail, bg=c["sf"], fg=c["dim"], font=c["font_sm"])
        tr.pack(side="right", padx=(0, 16))
        t = tk.Label(row, text=x["name"], bg=c["sf"], fg=c["tx"] if x["online"] else c["dim"], font=c["font"],
                     anchor="w", cursor="hand2")
        t.pack(side="left", fill="x", expand=True)
        parts = [row, dot, tr, t]

        def enter(_e):
            for w in parts:
                w.configure(bg=c["hover"])

        def leave(_e):
            for w in parts:
                w.configure(bg=c["sf"])

        def click(_e, pid=x["id"]):
            close()
            self.root.after(1, lambda: self.open_browser(pid))
        for w in parts:
            w.bind("<Enter>", enter)
            w.bind("<Leave>", leave)
            w.bind("<Button-1>", click)

    # ---- browser
    def open_browser(self, pid):
        b = self._browsers.get(pid)
        if b is not None and b.alive():
            b.win.deiconify()
            b.win.lift()
            b.win.focus_force()
            return b
        sw = getattr(self.app, "_settings_win", None)
        try:
            if sw is not None and sw.winfo_exists():
                sw.attributes("-topmost", False)        # the file window opens in front of Settings
        except Exception:
            pass
        x = self.peer(pid)
        b = Browser(self, pid, x["name"] if x else pid[:8])
        self._browsers[pid] = b
        return b

    def forget_browser(self, pid, b):
        if self._browsers.get(pid) is b:
            del self._browsers[pid]

    # ---- command line: HDContainer.exe --lan pair NAME | accept CODE | open NAME
    def command(self, text):
        parts = text.split(None, 1)
        if parts and parts[0].lower() == "settings":
            self.app._open_settings("lan")
            return
        if len(parts) < 2:
            return
        verb, arg = parts[0].lower(), parts[1].strip()
        if verb == "name":
            s = dict(self.app.settings.get("lan", {}))
            s["name"] = arg[:64]
            self.app._set_setting("lan", s, user=True)
            self.log("lan: device name set to %s" % arg[:64])
            self._refresh_page()
            return
        if verb == "enable":
            if not self.saved("enabled"):
                self.set_enabled(True)
            return
        x = next((d for d in self.snapshot() if arg.lower() in (d["name"].lower(), d["id"])), None)
        if verb == "pair":
            if x is None:
                self.log("lan: pair: no device %r nearby" % arg)
            else:
                self.pair(x["id"], x["name"])
        elif verb == "accept":
            self.accept_pending(arg)
        elif verb == "open" and x is not None:
            self.open_browser(x["id"])
        elif verb == "unpair" and x is not None and x["paired"]:
            node = self.node
            threading.Thread(target=lambda: node.unpair(x["id"]), daemon=True, name="lan-unpair").start()


def _code(code):
    return "%s %s" % (code[:3], code[3:])


class PairDialog:
    def __init__(self, ctl, pid, name):
        self.ctl, self.pid, self.name = ctl, pid, name
        app, c = ctl.app, colors()
        self.cancel = threading.Event()
        win = app._dialog(L("pair_t", name), 420, 250)
        self.win = win
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda e: self.close())
        self.msg = tk.Label(win, text=L("connecting"), bg=c["sf"], fg=c["tx"], font=c["font"], justify="left",
                            wraplength=380, anchor="w")
        self.msg.pack(fill="x", padx=20, pady=(20, 6))
        self.code = tk.Label(win, text="", bg=c["sf"], fg=c["tx"], font=("Segoe UI Semibold", 30))
        self.code.pack(pady=(8, 2))
        row = tk.Frame(win, bg=c["sf"])
        row.pack(side="bottom", fill="x", padx=20, pady=16)
        self.btn = app._ghost_btn(row, L("cancel"), self.close)
        self.btn.pack(side="right")
        threading.Thread(target=self._work, daemon=True, name="lan-pair").start()

    def _work(self):
        ui = self.ctl._ui
        try:
            self.ctl.node.pair(self.pid, lambda code: ui(lambda: self._show_code(code)), self.cancel)
            ui(self._done)
        except lan.LinkError as ex:
            self.ctl.log("lan: pairing with %s failed: %s" % (self.name, ex.code))
            if ex.code != "cancelled":
                ui(lambda code=ex.code: self._fail(code))
        except Exception as ex:
            self.ctl.log("lan: pairing failed: %r" % ex)
            ui(lambda: self._fail("offline"))

    def _alive(self):
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def _show_code(self, code):
        if self._alive():
            self.code.configure(text=_code(code))
            self.msg.configure(text=L("pair_check", self.name))
            self.ctl.log("lan: pairing with %s, code %s" % (self.name, code))

    def _done(self):
        if not self._alive():
            return
        c = colors()
        self.code.configure(text=chr(0xE73E) + "  " + L("pair_done"), font=("Segoe UI Semibold", 18), fg=COL_ONLINE)
        self.msg.configure(text="")
        self.btn.configure(text=L("close"))
        self.win.after(1400, self.close)
        self.ctl._refresh_page()

    def _fail(self, code):
        if not self._alive():
            return
        key = {"declined": "pair_declined", "busy": "pair_busy", "timeout": "pair_timeout"}.get(code, "pair_fail")
        self.code.configure(text="")
        self.msg.configure(text=L(key, self.name))
        self.btn.configure(text=L("close"))

    def close(self):
        self.cancel.set()
        try:
            self.win.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
class Browser:
    """Files of a linked device: places + drives on the left, the folder on the right,
    transfers along the bottom. Remote paths are plain Windows paths of that device;
    "" is the device's drive list."""
    def __init__(self, ctl, pid, name):
        self.ctl, self.app, self.pid, self.name = ctl, ctl.app, pid, name
        self.root = ctl.root
        c = self.c = colors()
        self.u = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.path = None
        self.back = []
        self.items = []                       # [name, is_dir, size, mtime, hidden]
        self.drives = []
        self.places = []
        self.sort = ("name", False)
        self.show_hidden = False
        self.seq = 0
        self._ch = None
        self._ch_lock = threading.Lock()
        self._jobs = queue.Queue()
        self._worker = None
        self._job = None                      # dict of the running transfer (read by the timer)
        self._pending = 0
        self._last = None                     # (text, color, on_click) of the finished transfer
        self._drops = []
        self._drop = None
        self._closed = False
        self._icons = {}

        win = tk.Toplevel(self.root)
        self.win = win
        win.title("%s — HDContainer" % name)
        ico = _C("_ICON", "")
        try:
            if ico and os.path.exists(ico):
                win.iconbitmap(ico)
        except Exception:
            pass
        win.configure(bg=c["sf"])
        W, H = int(1000 * self.u), int(640 * self.u)
        sx, sy = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry("%dx%d+%d+%d" % (W, H, max(0, (sx - W) // 2), max(0, (sy - H) // 2)))
        win.minsize(int(640 * self.u), int(380 * self.u))
        win.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._load_icons()
        self.go("", push=False)
        win.after(200, self._timer)
        win.after(300, self._register_drop)

    # ---------------------------------------------------------------- layout
    def _build(self):
        c, u, win = self.c, self.u, self.win
        side = tk.Frame(win, bg=c["bg"], width=int(210 * u))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.side = side
        tk.Frame(win, bg=c["border"], width=1).pack(side="left", fill="y")
        main = tk.Frame(win, bg=c["sf"])
        main.pack(side="left", fill="both", expand=True)

        # top bar: back / up / refresh, path, upload / new folder
        bar = tk.Frame(main, bg=c["sf"])
        bar.pack(fill="x", padx=12, pady=(10, 8))
        self.b_back = self._icon_btn(bar, G_BACK, L("back"), self.go_back)
        self.b_back.pack(side="left")
        self.b_up = self._icon_btn(bar, G_UP, L("up"), self.go_up)
        self.b_up.pack(side="left", padx=(2, 0))
        self._icon_btn(bar, G_REFRESH, L("refresh"), self.refresh).pack(side="left", padx=(2, 10))
        self._icon_btn(bar, G_NEWFOLDER, L("new_folder"), self.new_folder).pack(side="right")
        self.b_upload = self._icon_btn(bar, G_UPLOAD, L("upload"), self._upload_menu)
        self.b_upload.pack(side="right", padx=(10, 2))
        self.path_var = tk.StringVar()
        ent = tk.Entry(bar, textvariable=self.path_var, font=c["font"], bg=c["bg"], fg=c["tx"],
                       insertbackground=c["tx"], relief="flat", highlightthickness=1,
                       highlightbackground=c["border"], highlightcolor=c["accent"])
        ent.pack(side="left", fill="x", expand=True, ipady=5)
        ent.bind("<Return>", lambda e: self._path_entered())
        ent.bind("<Escape>", lambda e: self._show_path())
        self.path_ent = ent

        # bottom: status on the left, the transfer on the right
        bottom = tk.Frame(main, bg=c["sf"])
        bottom.pack(side="bottom", fill="x")
        tk.Frame(bottom, bg=c["border"], height=1).pack(fill="x")
        brow = tk.Frame(bottom, bg=c["sf"], height=int(30 * u))
        brow.pack(fill="x")
        brow.pack_propagate(False)
        self.status = tk.Label(brow, text="", bg=c["sf"], fg=c["dim"], font=c["font_sm"], anchor="w")
        self.status.pack(side="left", padx=14)
        self.x_cancel = tk.Label(brow, text=G_CLOSE, bg=c["sf"], fg=c["dim"], font=(ICON_FONT, 8), cursor="hand2")
        self.x_cancel.bind("<Button-1>", lambda e: self._cancel_job())
        self.x_cancel.bind("<Enter>", lambda e: self.x_cancel.configure(fg=c["danger"]))
        self.x_cancel.bind("<Leave>", lambda e: self.x_cancel.configure(fg=c["dim"]))
        self.x_bar = tk.Canvas(brow, width=int(120 * u), height=4, bg=c["sf2"], highlightthickness=0, bd=0)
        self.x_fill = self.x_bar.create_rectangle(0, 0, 0, 4, fill=c["accent"], outline="")
        self.x_text = tk.Label(brow, text="", bg=c["sf"], fg=c["dim"], font=c["font_sm"], anchor="e")

        # the list
        body = tk.Frame(main, bg=c["sf"])
        body.pack(fill="both", expand=True)
        st = ttk.Style(win)
        try:
            if st.theme_use() != "clam":
                st.theme_use("clam")
        except tk.TclError:
            pass
        rh = int(28 * u)
        st.configure("Lan.Treeview", background=c["sf"], fieldbackground=c["sf"], foreground=c["tx"],
                     borderwidth=0, relief="flat", rowheight=rh, font=c["font"])
        st.map("Lan.Treeview", background=[("selected", COL_SEL)], foreground=[("selected", "#ffffff")])
        st.configure("Lan.Treeview.Heading", background=c["sf"], foreground=c["dim"], borderwidth=0,
                     relief="flat", font=c["font_sm"], padding=(8, 5))
        st.map("Lan.Treeview.Heading", background=[("active", c["hover"])], foreground=[("active", c["tx"])])
        st.layout("Lan.Treeview", [("Lan.Treeview.treearea", {"sticky": "nswe"})])
        tree = ttk.Treeview(body, style="Lan.Treeview", columns=("date", "size"), selectmode="extended")
        tree.heading("#0", text=L("col_name"), anchor="w", command=lambda: self._sort_by("name"))
        tree.heading("date", text=L("col_date"), anchor="w", command=lambda: self._sort_by("date"))
        tree.heading("size", text=L("col_size"), anchor="e", command=lambda: self._sort_by("size"))
        tree.column("#0", width=int(420 * u), minwidth=int(160 * u), stretch=True, anchor="w")
        tree.column("date", width=int(150 * u), minwidth=int(90 * u), stretch=False, anchor="w")
        tree.column("size", width=int(150 * u), minwidth=int(70 * u), stretch=False, anchor="e")
        tree.tag_configure("hidden", foreground=c["dim"])
        sb = self.app._dark_scrollbar(body, tree)
        sb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.tree = tree
        self.overlay = tk.Frame(body, bg=c["sf"])
        self.ov_text = tk.Label(self.overlay, text="", bg=c["sf"], fg=c["dim"], font=c["font"])
        self.ov_text.pack()
        self.ov_btn = _flat_btn(self.overlay, L("retry"), self.refresh, c, small=False)

        tree.bind("<Double-1>", self._on_double)
        tree.bind("<Return>", lambda e: self.open_selected())
        tree.bind("<BackSpace>", lambda e: self.go_up())
        tree.bind("<Alt-Left>", lambda e: self.go_back())
        tree.bind("<Delete>", lambda e: self.delete_selected())
        tree.bind("<F2>", lambda e: self.rename_selected())
        tree.bind("<F5>", lambda e: self.refresh())
        tree.bind("<Control-KeyPress>", self._ctrl_key)
        tree.bind("<Button-3>", self._on_menu)
        tree.bind("<<TreeviewSelect>>", lambda e: self._show_status())
        win.bind("<Alt-Left>", lambda e: self.go_back())
        win.bind("<F5>", lambda e: self.refresh())

    def _ctrl_key(self, e):
        # by key code: on a Russian layout Tk reports Ctrl+A as a Cyrillic keysym
        if e.keycode == 65:                          # A
            self.tree.selection_set(self.tree.get_children())
            return "break"
        return None

    def _icon_btn(self, parent, glyph, tip, cmd):
        c = self.c
        b = tk.Label(parent, text=glyph, bg=c["sf"], fg=c["tx"], font=(ICON_FONT, 11), width=3, pady=4,
                     cursor="hand2")
        b.bind("<Button-1>", lambda e: (b.cget("fg") != COL_OFF) and cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=c["hover"]))
        b.bind("<Leave>", lambda e: b.configure(bg=c["sf"]))
        Tooltip(b, tip, c)
        return b

    def _load_icons(self):
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageTk
            px = int(16 * self.u)
            fdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
            font = None
            for fn in ("segmdl2.ttf", "SegoeIcons.ttf"):
                try:
                    font = ImageFont.truetype(os.path.join(fdir, fn), px)
                    break
                except OSError:
                    continue
            if font is None:
                return

            def make(glyph, color):
                # the canvas follows the glyph's own box: the folder glyph is wider than its em size
                l, t, r, b = font.getbbox(glyph)
                w, h = max(px, r - l) + 4, max(px, b - t) + 2
                im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                ImageDraw.Draw(im).text((2 - l, (h - (b - t)) // 2 - t), glyph, font=font, fill=color)
                return ImageTk.PhotoImage(im, master=self.win)
            self._icons = {"dir": make(chr(0xED25), "#e3b341"), "file": make(chr(0xE7C3), "#9aa0a6"),
                           "drive": make(G_DRIVE, "#9aa0a6")}
        except Exception:
            self._icons = {}

    def _side_fill(self):
        c, u = self.c, self.u
        for w in self.side.winfo_children():
            w.destroy()
        x = self.ctl.peer(self.pid)
        head = tk.Frame(self.side, bg=c["bg"])
        head.pack(fill="x", padx=14, pady=(16, 10))
        _dot(head, COL_ONLINE if (x and x["online"]) else COL_OFF, c["bg"]).pack(side="left", padx=(0, 8))
        tk.Label(head, text=self.name, bg=c["bg"], fg=c["tx"], font=c["font_title"], anchor="w").pack(
            side="left", fill="x")
        self.head_dot = head
        rows = [(G_PC, L("this_pc"), "")]
        glyphs = {"desktop": G_DESKTOP, "downloads": G_DOWNLOADS, "documents": G_DOCS, "home": G_HOME}
        for p in self.places:
            rows.append((glyphs.get(p["key"], G_HOME), L(p["key"]), p["path"]))
        self._side_rows = []
        for g, text, path in rows:
            self._side_row(g, text, path)
        if self.drives:
            tk.Label(self.side, text=L("drives").upper(), bg=c["bg"], fg=c["dim"], font=("Segoe UI Semibold", 8),
                     anchor="w").pack(fill="x", padx=16, pady=(16, 4))
            for d in self.drives:
                label = d.get("label") or ""
                text = "%s (%s)" % (label, d["path"][:2]) if label else d["path"][:2]
                self._side_row(G_DRIVE, text, d["path"], d)
        self._side_mark()

    def _side_row(self, glyph, text, path, drive=None):
        c = self.c
        row = tk.Frame(self.side, bg=c["bg"], cursor="hand2")
        row.pack(fill="x", padx=6)
        top = tk.Frame(row, bg=c["bg"])
        top.pack(fill="x")
        g = tk.Label(top, text=glyph, bg=c["bg"], fg=c["dim"], font=(ICON_FONT, 10), width=2)
        g.pack(side="left", padx=(8, 6), pady=6)
        t = tk.Label(top, text=text, bg=c["bg"], fg=c["tx"], font=c["font"], anchor="w")
        t.pack(side="left", fill="x", expand=True)
        parts = [row, top, g, t]
        if drive and drive.get("total"):
            used = 1.0 - drive["free"] / float(drive["total"])
            bw = int(150 * self.u)
            bar = tk.Canvas(row, width=bw, height=3, bg=c["sf2"], highlightthickness=0, bd=0)
            bar.create_rectangle(0, 0, max(2, int(bw * used)), 3, fill="#ff9f0a" if used > 0.9 else c["accent"],
                                 outline="")
            bar.pack(anchor="w", padx=(38, 0))
            free = tk.Label(row, text=L("free_of", fmt_size(drive["free"]), fmt_size(drive["total"])),
                            bg=c["bg"], fg=c["dim"], font=("Segoe UI", 8), anchor="w")
            free.pack(fill="x", padx=(37, 0), pady=(1, 6))
            parts += [bar, free]
        self._side_rows.append((path, parts))

        def paint(bg):
            for w in parts:
                if isinstance(w, tk.Canvas):
                    continue
                w.configure(bg=bg)

        def enter(_e):
            if self._norm(path) != self._norm(self.path or ""):
                paint(c["hover"])

        def leave(_e):
            self._side_mark()
        for w in parts:
            w.bind("<Enter>", enter)
            w.bind("<Leave>", leave)
            w.bind("<Button-1>", lambda e, p=path: self.go(p))

    def _side_mark(self):
        c = self.c
        cur = self._norm(self.path or "")
        for path, parts in getattr(self, "_side_rows", []):
            bg = c["sf2"] if self._norm(path) == cur else c["bg"]
            for w in parts:
                if not isinstance(w, tk.Canvas):
                    w.configure(bg=bg)

    @staticmethod
    def _norm(p):
        return os.path.normcase(p.rstrip("\\/")) if p else ""

    # ---------------------------------------------------------------- network helpers
    def alive(self):
        try:
            return not self._closed and bool(self.win.winfo_exists())
        except Exception:
            return False

    def _ui(self, fn):
        self.ctl._ui(lambda: self.alive() and fn())

    def _control(self, fn, ok, fail):
        """fn(channel) in a thread on the shared control channel; one reconnect on a broken channel."""
        def work():
            with self._ch_lock:
                for attempt in (0, 1):
                    try:
                        if self._ch is None:
                            self._ch = self.ctl.node.open(self.pid)
                        res = fn(self._ch)
                    except Exception as ex:
                        if _conn_level(ex) or isinstance(ex, lan.LinkError) and ex.code in ("unpaired", "auth"):
                            if self._ch is not None:
                                self._ch.close()
                                self._ch = None
                            if attempt == 0 and _conn_level(ex) and not getattr(ex, "code", "") == "offline":
                                continue
                        self._ui(lambda ex=ex: fail(ex))
                        return
                    self._ui(lambda: ok(res))
                    return
        if self.ctl.node is None:
            fail(lan.LinkError("offline"))
            return
        threading.Thread(target=work, daemon=True, name="lan-ctl").start()

    # ---------------------------------------------------------------- navigation
    def go(self, path, push=True):
        self.seq += 1
        seq = self.seq
        self._status_text(L("loading"))
        prev = self.path

        def fetch(ch):
            if path == "":
                info = ch.call({"op": "info"})
                return ("root", info)
            items, _ = ch.call_paged({"op": "list", "path": path})
            return ("dir", items)

        def ok(res):
            if seq != self.seq:
                return
            if push and prev is not None and self._norm(prev) != self._norm(path):
                self.back.append(prev)
                del self.back[:-50]
            self.path = path
            kind, data = res
            if kind == "root":
                self.drives, self.places = data.get("drives", []), data.get("places", [])
                self._side_fill()
                self.items = None
            else:
                self.items = data
                if not self.drives:
                    self._fetch_side()
            self._show_path()
            self._fill()
            self._side_mark()
            self._nav_state()

        def fail(ex):
            if seq != self.seq:
                return
            if self.path is None or _conn_level(ex) or getattr(ex, "code", "") in ("offline", "auth", "unpaired"):
                self._offline(ex)
            else:
                self._status_text(err_text(ex), err=True)
        self._control(fetch, ok, fail)

    def _fetch_side(self):
        def ok(info):
            self.drives, self.places = info.get("drives", []), info.get("places", [])
            self._side_fill()
        self._control(lambda ch: ch.call({"op": "info"}), ok, lambda ex: None)

    def go_back(self):
        if self.back:
            p = self.back.pop()
            self.go(p, push=False)

    def go_up(self):
        p = self.path
        if not p:
            return
        if len(p.rstrip("\\")) <= 2:
            self.go("")
            return
        parent = os.path.dirname(p.rstrip("\\"))
        if len(parent) == 2 and parent[1] == ":":
            parent += "\\"
        self.go(parent)

    def refresh(self):
        self.go(self.path if self.path is not None else "", push=False)

    def _path_entered(self):
        t = self.path_var.get().strip().strip('"')
        if not t or t == L("this_pc"):
            self.go("")
        elif len(t) == 2 and t[1] == ":":
            self.go(t + "\\")
        else:
            self.go(t)
        self.tree.focus_set()

    def _show_path(self):
        self.path_var.set(self.path or L("this_pc"))

    def _nav_state(self):
        c = self.c
        self.b_back.configure(fg=c["tx"] if self.back else COL_OFF)
        self.b_up.configure(fg=c["tx"] if self.path else COL_OFF)
        can = bool(self.path)
        self.b_upload.configure(fg=c["tx"] if can else COL_OFF)

    def _offline(self, ex):
        self.tree.delete(*self.tree.get_children())
        code = getattr(ex, "code", "")
        txt = L("err_auth") if code in ("auth", "unpaired") else L("dev_offline", self.name)
        self.ov_text.configure(text=txt)
        if code in ("auth", "unpaired"):
            self.ov_btn.pack_forget()
        else:
            self.ov_btn.pack(pady=(12, 0))
        self.overlay.place(relx=0.5, rely=0.42, anchor="center")
        self._status_text("")

    def on_status(self):
        """The device list changed (online / offline): retry an offline view, repaint the dot."""
        x = self.ctl.peer(self.pid)
        if x is None:
            return
        if self.drives or self.places:
            self._side_fill()
        if x["online"] and self.overlay.winfo_ismapped() and self.ov_btn.winfo_ismapped():
            self.refresh()

    # ---------------------------------------------------------------- the list
    def _visible(self):
        if self.items is None:
            return []
        items = self.items if self.show_hidden else [i for i in self.items if not i[4]]
        key, rev = self.sort
        if key == "size":
            k = lambda i: (not i[1], i[2], i[0].lower())
        elif key == "date":
            k = lambda i: (not i[1], i[3], i[0].lower())
        else:
            k = lambda i: (not i[1], i[0].lower())
        items = sorted(items, key=k, reverse=rev)
        if rev:                                       # folders stay on top either way
            items = [i for i in items if i[1]] + [i for i in items if not i[1]]
        return items

    def _fill(self, keep=None):
        tree = self.tree
        sel = set(keep if keep is not None else [tree.item(i, "text").strip() for i in tree.selection()])
        tree.delete(*tree.get_children())
        self.overlay.place_forget()
        ic = self._icons
        root = self.items is None
        tree.heading("date", text=L("col_free") if root else L("col_date"))
        tree.heading("size", text=L("col_total") if root else L("col_size"))
        if root:
            for d in self.drives:
                label = d.get("label") or ""
                text = "%s (%s)" % (label, d["path"][:2]) if label else d["path"][:2]
                vals = (fmt_size(d["free"]), fmt_size(d["total"])) if d.get("total") else ("", "")
                tree.insert("", "end", iid="d:" + d["path"], text="  " + text, values=vals,
                            image=ic.get("drive", ""))
        else:
            for i in self._visible():
                name, isdir, size, mtime, hidden = i
                iid = tree.insert("", "end", text="  " + name,
                                  values=(fmt_date(mtime), "" if isdir else fmt_size(size)),
                                  image=ic.get("dir" if isdir else "file", ""),
                                  tags=("hidden",) if hidden else ())
                if name in sel:
                    tree.selection_add(iid)
            if not tree.get_children():
                self.ov_text.configure(text=L("empty"))
                self.ov_btn.pack_forget()
                self.overlay.place(relx=0.5, rely=0.42, anchor="center")
        kids = tree.get_children()
        if kids and not tree.selection():
            tree.focus(kids[0])
        self._show_status()

    def _sort_by(self, key):
        cur, rev = self.sort
        self.sort = (key, not rev if cur == key else key != "name")
        self._fill()

    def _selected(self):
        """[(name, is_dir, size, mtime, full_path)] of the selected rows."""
        out = []
        if self.items is None:
            for iid in self.tree.selection():
                if iid.startswith("d:"):
                    p = iid[2:]
                    out.append((p, 1, 0, 0, p))
            return out
        by = {i[0]: i for i in self.items}
        for iid in self.tree.selection():
            name = self.tree.item(iid, "text")[2:]
            i = by.get(name)
            if i:
                out.append((i[0], i[1], i[2], i[3], os.path.join(self.path, i[0])))
        return out

    def _show_status(self):
        if self.items is None:
            n = len(self.drives)
            self._status_text(L("items", n) if n else "")
            return
        sel = self._selected()
        if sel:
            size = sum(s[2] for s in sel if not s[1])
            t = L("selected", len(sel))
            if size:
                t += " · " + fmt_size(size)
        else:
            t = L("items", len(self._visible()))
        self._status_text(t)

    def _status_text(self, text, err=False):
        self.status.configure(text=text, fg=self.c["danger"] if err else self.c["dim"])

    def _on_double(self, e):
        if self.tree.identify_region(e.x, e.y) in ("heading", "separator"):
            return
        self.open_selected()

    def open_selected(self):
        sel = self._selected()
        if len(sel) != 1:
            return
        name, isdir, size, mtime, path = sel[0]
        if isdir:
            self.go(path if self.items is not None else name)
        else:
            self._enqueue({"kind": "open", "path": path, "name": name, "size": size, "mtime": mtime})

    def _on_menu(self, e):
        tree = self.tree
        iid = tree.identify_row(e.y)
        if iid and iid not in tree.selection():
            tree.selection_set(iid)
        elif not iid:
            tree.selection_remove(tree.selection())
        sel = self._selected()
        items = []
        if sel:
            one = len(sel) == 1
            if one:
                items.append((L("open"), self.open_selected))
            if self.items is not None:
                items.append((L("download"), self.download_selected))
                if one:
                    items.append((L("rename"), self.rename_selected))
                items.append(None)
                items.append((L("delete"), self.delete_selected, {"danger": True}))
        elif self.path:
            items += [(L("up_files"), self.upload_files), (L("up_folder"), self.upload_folder),
                      (L("new_folder"), self.new_folder), None]
        if not sel:
            items += [(L("show_hidden"), self._toggle_hidden, {"checked": self.show_hidden}),
                      (L("refresh"), self.refresh)]
        if items:
            PopupMenu(self.win, e.x_root, e.y_root, items, self.c)

    def _upload_menu(self):
        if not self.path:
            return
        b = self.b_upload
        PopupMenu(self.win, b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + 2,
                  [(L("up_files"), self.upload_files), (L("up_folder"), self.upload_folder)], self.c)

    def _toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self._fill()

    # ---------------------------------------------------------------- simple operations
    def new_folder(self):
        if not self.path:
            return
        name = self.app._ask_string(L("new_folder"), L("folder_q"), unique_name(self._exists, L("new_folder")))
        if not name:
            return
        path = os.path.join(self.path, name)
        self._control(lambda ch: ch.call({"op": "mkdir", "path": path}),
                      lambda r: self._after_change(name), lambda ex: self._status_text(err_text(ex), err=True))

    def rename_selected(self):
        sel = self._selected()
        if len(sel) != 1 or self.items is None:
            return
        old = sel[0][0]
        name = self.app._ask_string(L("rename"), L("rename_q"), old)
        if not name or name == old:
            return
        src, dst = os.path.join(self.path, old), os.path.join(self.path, name)
        self._control(lambda ch: ch.call({"op": "rename", "src": src, "dst": dst}),
                      lambda r: self._after_change(name), lambda ex: self._status_text(err_text(ex), err=True))

    def delete_selected(self):
        sel = self._selected()
        if not sel or self.items is None:
            return
        if not self.app._ask_yesno(L("delete_t"), L("delete_q", n=len(sel), dev=self.name)):
            return
        paths = [s[4] for s in sel]
        self._control(lambda ch: ch.call({"op": "delete", "paths": paths}),
                      lambda r: self._after_change(None), lambda ex: self._status_text(err_text(ex), err=True))

    def _after_change(self, select_name):
        path, seq = self.path, self.seq

        def ok(items):
            if seq != self.seq or self.path != path:
                return
            self.items = items[0]
            self._fill(keep=[select_name] if select_name else [])
            if select_name:
                for iid in self.tree.get_children():
                    if self.tree.item(iid, "text")[2:] == select_name:
                        self.tree.see(iid)
                        self.tree.focus(iid)
        self._control(lambda ch: ch.call_paged({"op": "list", "path": path}), ok,
                      lambda ex: self._status_text(err_text(ex), err=True))

    def _exists(self, name):
        return any(i[0].lower() == name.lower() for i in (self.items or []))

    # ---------------------------------------------------------------- transfers
    def download_selected(self):
        sel = self._selected()
        if not sel or self.items is None:
            return
        last = getattr(self.ctl, "_last_dl", "")
        dest = filedialog.askdirectory(parent=self.win, initialdir=last or lan.known_folder("downloads"),
                                       mustexist=True)
        if not dest:
            return
        self.ctl._last_dl = dest
        self._enqueue({"kind": "down", "sel": sel, "dest": os.path.normpath(dest)})

    def upload_files(self):
        if not self.path:
            return
        files = filedialog.askopenfilenames(parent=self.win)
        if files:
            self._enqueue({"kind": "up", "paths": [os.path.normpath(f) for f in files], "dest": self.path})

    def upload_folder(self):
        if not self.path:
            return
        d = filedialog.askdirectory(parent=self.win, mustexist=True)
        if d:
            self._enqueue({"kind": "up", "paths": [os.path.normpath(d)], "dest": self.path})

    def _enqueue(self, job):
        job["cancel"] = threading.Event()
        self._pending += 1
        self._jobs.put(job)
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, daemon=True, name="lan-xfer")
            self._worker.start()
        self._last = None

    def _cancel_job(self):
        j = self._job
        if j is not None:
            j["cancel"].set()

    def _xfer_channel(self):
        ch = getattr(self, "_xch", None)
        if ch is None:
            ch = self._xch = self.ctl.node.open(self.pid)
        return ch

    def _drop_xfer_channel(self):
        ch = getattr(self, "_xch", None)
        self._xch = None
        if ch is not None:
            ch.close()

    def _work(self):
        while not self._closed:
            try:
                job = self._jobs.get(timeout=2)
            except queue.Empty:
                if self._jobs.empty():
                    self._drop_xfer_channel()
                    return
                continue
            self._pending -= 1
            job.update({"done": 0, "total": 0, "file": "", "t0": time.time()})
            self._job = job
            try:
                for attempt in (0, 1):
                    try:
                        res = getattr(self, "_job_" + job["kind"])(job)
                        break
                    except Exception as ex:
                        if job["cancel"].is_set():
                            raise lan.LinkError("cancelled")
                        self._drop_xfer_channel()
                        if attempt == 0 and _conn_level(ex) and job["done"] == 0:
                            continue
                        raise
                self._ui(lambda job=job, res=res: self._job_done(job, res))
            except Exception as ex:
                self._drop_xfer_channel()
                cancelled = job["cancel"].is_set() or getattr(ex, "code", "") == "cancelled"
                if not cancelled:
                    self.ctl.log("lan: transfer failed: %r" % ex)
                self._ui(lambda ex=ex, c=cancelled: self._job_failed(ex, c))
            finally:
                self._job = None

    def _progress(self, job):
        def cb(n):
            job["done"] += n
        return cb

    def _job_open(self, job):
        cache = os.path.join(self.ctl.local_dir, "lan", "open", self.pid[:8])
        os.makedirs(cache, exist_ok=True)
        local = os.path.join(cache, job["name"])
        job["file"], job["total"] = job["name"], job["size"]
        try:
            st = os.stat(local)
            if st.st_size == job["size"] and int(st.st_mtime) == int(job["mtime"]):
                return local
        except OSError:
            pass
        lan.download_file(self._xfer_channel(), job["path"], local, self._progress(job), job["cancel"])
        return local

    def _job_down(self, job):
        ch = self._xfer_channel()
        plan = []                       # (remote, local, size, is_dir)
        dest = job["dest"]
        for name, isdir, size, mtime, rpath in job["sel"]:
            top = unique_name(lambda n: os.path.exists(os.path.join(dest, n)), name)
            lroot = os.path.join(dest, top)
            if not isdir:
                plan.append((rpath, lroot, size, 0))
                continue
            plan.append((rpath, lroot, 0, 1))
            items, _ = ch.call_paged({"op": "walk", "path": rpath})
            for rel, d, sz, _mt in items:
                plan.append((os.path.join(rpath, rel), os.path.join(lroot, rel), sz, d))
        job["total"] = sum(p[2] for p in plan)
        for rpath, lpath, size, isdir in plan:
            if job["cancel"].is_set():
                raise lan.LinkError("cancelled")
            if isdir:
                os.makedirs(lpath, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(lpath), exist_ok=True)
            job["file"] = os.path.basename(lpath)
            lan.download_file(ch, rpath, lpath, self._progress(job), job["cancel"])
        return dest if len(job["sel"]) != 1 else os.path.join(dest, os.path.basename(plan[0][1]))

    def _job_up(self, job):
        ch = self._xfer_channel()
        dest = job["dest"]

        def remote_exists(n):
            try:
                ch.call({"op": "stat", "path": os.path.join(dest, n)})
                return True
            except lan.LinkError as ex:
                if ex.code == "missing":
                    return False
                raise
        plan = []
        for p in job["paths"]:
            top = unique_name(remote_exists, os.path.basename(p.rstrip("\\")))
            rroot = os.path.join(dest, top)
            if os.path.isdir(p):
                plan.append((p, rroot, 0, 1))
                for dirpath, dirnames, filenames in os.walk(p):
                    rel = os.path.relpath(dirpath, p)
                    rel = "" if rel == "." else rel
                    for d in dirnames:
                        plan.append((os.path.join(dirpath, d), os.path.join(rroot, rel, d), 0, 1))
                    for f in filenames:
                        lp = os.path.join(dirpath, f)
                        try:
                            plan.append((lp, os.path.join(rroot, rel, f), os.path.getsize(lp), 0))
                        except OSError:
                            pass
            else:
                plan.append((p, rroot, os.path.getsize(p), 0))
        job["total"] = sum(x[2] for x in plan)
        for lp, rp, size, isdir in plan:
            if job["cancel"].is_set():
                raise lan.LinkError("cancelled")
            if isdir:
                ch.call({"op": "mkdir", "path": rp, "ok_exists": True})
                continue
            job["file"] = os.path.basename(lp)
            lan.upload_file(ch, lp, rp, self._progress(job), job["cancel"], mkdirs=True)
        return dest

    def _job_done(self, job, res):
        c = self.c
        if job["kind"] == "open":
            try:
                os.startfile(res)
            except OSError as ex:
                self._last = (err_text(ex), c["danger"], None)
                return
            self._last = None
        elif job["kind"] == "down":
            def show(p=res):
                if os.path.isdir(p) and len(job["sel"]) != 1:
                    os.startfile(p)
                else:
                    import subprocess
                    subprocess.Popen(["explorer", "/select,", p])
            self._last = (L("xf_downloaded"), c["accent"], show)
        else:
            self._last = (L("xf_uploaded"), c["dim"], None)
            if self.path is not None and self._norm(self.path) == self._norm(job["dest"]):
                self._after_change(None)
        self._last_t = time.time()

    def _job_failed(self, ex, cancelled):
        c = self.c
        self._last = (L("xf_cancelled"), c["dim"], None) if cancelled else (err_text(ex), c["danger"], None)
        self._last_t = time.time()

    def _timer(self):
        if not self.alive():
            return
        try:
            self._paint_transfer()
            self._take_drops()
        except Exception:
            self.ctl.log("lan browser timer failed:\n" + traceback.format_exc())
        self.win.after(200, self._timer)

    def _paint_transfer(self):
        c, j = self.c, self._job
        if j is not None:
            kind = {"down": "xf_down", "up": "xf_up", "open": "xf_open"}[j["kind"]]
            dt = max(0.001, time.time() - j["t0"])
            rate = j["done"] / dt
            parts = [L(kind)]
            if j["file"]:
                f = j["file"]
                parts.append(f if len(f) <= 36 else f[:22] + "…" + f[-12:])
            if j["total"]:
                parts.append("%d%%" % min(100, int(100 * j["done"] / j["total"])))
            if dt > 1 and j["done"]:
                parts.append(fmt_size(rate) + L("per_s"))
            if self._pending > 0:
                parts.append(L("queue", self._pending))
            self.x_text.configure(text="  ·  ".join(parts), fg=c["dim"], cursor="")
            self.x_text.unbind("<Button-1>")
            frac = (j["done"] / float(j["total"])) if j["total"] else 0
            w = int(self.x_bar.cget("width"))
            self.x_bar.coords(self.x_fill, 0, 0, int(w * frac), 4)
            if not self.x_cancel.winfo_ismapped():
                self.x_cancel.pack(side="right", padx=(8, 14))
                self.x_bar.pack(side="right", padx=(10, 0))
                self.x_text.pack(side="right")
            return
        self.x_cancel.pack_forget()
        self.x_bar.pack_forget()
        if self._last and time.time() - getattr(self, "_last_t", 0) < 20:
            text, color, click = self._last
            self.x_text.configure(text=text, fg=color, cursor="hand2" if click else "")
            if click:
                self.x_text.bind("<Button-1>", lambda e: click())
            else:
                self.x_text.unbind("<Button-1>")
            if not self.x_text.winfo_ismapped():
                self.x_text.pack(side="right", padx=(0, 14))
        else:
            self._last = None
            self.x_text.pack_forget()

    # ---------------------------------------------------------------- drag and drop from Explorer
    def _register_drop(self):
        boxes = sys.modules.get("boxes")
        mgr = getattr(self.app, "boxes", None)
        if boxes is None or mgr is None or not getattr(mgr, "ole_ok", False) or not self.alive():
            return
        try:
            self._drop = _FileDrop(boxes, self)
            hwnd = self.tree.winfo_id()
            hr = boxes.ole32.RegisterDragDrop(hwnd, self._drop.ptr)
            if hr != 0:
                self.ctl.log("lan: RegisterDragDrop failed 0x%08x" % (hr & 0xFFFFFFFF))
                self._drop = None
            else:
                self._drop_hwnd = hwnd
        except Exception:
            self.ctl.log("lan: drop target failed:\n" + traceback.format_exc())
            self._drop = None

    def _take_drops(self):
        if self._drops and self.path:
            paths, self._drops = self._drops, []
            self._enqueue({"kind": "up", "paths": paths, "dest": self.path})
        else:
            self._drops = []

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._job is not None:
            self._job["cancel"].set()
        if self._drop is not None:
            try:
                sys.modules["boxes"].ole32.RevokeDragDrop(self._drop_hwnd)
            except Exception:
                pass
        ch, self._ch = self._ch, None
        if ch is not None:
            ch.close()
        try:
            self.win.destroy()
        except Exception:
            pass
        self.ctl.forget_browser(self.pid, self)


def _FileDrop(boxes, browser):
    """IDropTarget on the file list: files and folders from Explorer are uploaded into the open folder.
    The COM callbacks only store the paths; the browser's tk timer starts the upload."""
    HRESULT, VP, DWORD = boxes.HRESULT, boxes.VP, boxes.DWORD

    class FileDrop(boxes._ComObject):
        def __init__(self):
            self.files = []
            enter = ctypes.WINFUNCTYPE(HRESULT, VP, VP, DWORD, ctypes.c_uint64, ctypes.POINTER(DWORD))(self._enter)
            over = ctypes.WINFUNCTYPE(HRESULT, VP, DWORD, ctypes.c_uint64, ctypes.POINTER(DWORD))(self._over)
            leave = ctypes.WINFUNCTYPE(HRESULT, VP)(lambda this: 0)
            drop = ctypes.WINFUNCTYPE(HRESULT, VP, VP, DWORD, ctypes.c_uint64, ctypes.POINTER(DWORD))(self._drop)
            super().__init__(boxes.IID_IDropTarget, [enter, over, leave, drop])

        def _eff(self):
            return 1 if (self.files and browser.path) else 0           # DROPEFFECT_COPY / NONE

        def _enter(self, this, pdo, keys, pt, peff):
            try:
                self.files = boxes.dataobject_files(pdo)
            except Exception:
                self.files = []
            peff[0] = self._eff()
            return 0

        def _over(self, this, keys, pt, peff):
            peff[0] = self._eff()
            return 0

        def _drop(self, this, pdo, keys, pt, peff):
            if not self.files:
                try:
                    self.files = boxes.dataobject_files(pdo)
                except Exception:
                    self.files = []
            peff[0] = self._eff()
            if peff[0]:
                browser._drops.extend(self.files)
            self.files = []
            return 0
    return FileDrop()
