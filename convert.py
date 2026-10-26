"""
Конвертер файлов из контекстного меню Проводника.

ПКМ по файлу -> «Конвертировать в» -> список форматов, в которые этот файл
можно превратить. Результат ложится рядом с исходным файлом: то же имя,
другое расширение (если такой уже есть — «имя (2).расш»).

Движки:
  * изображения        — Pillow (встроен в приложение);
  * svg / html -> pdf, png; документы -> pdf — Microsoft Edge (есть в Windows)
                         в headless-режиме: печать в PDF / снимок страницы;
  * аудио/видео/субтитры — FFmpeg (BtbN/FFmpeg-Builds), ~190 МБ, скачивается
                         при первом использовании;
  * документы          — Pandoc (jgm/pandoc), ~40 МБ, скачивается;
  * чтение PDF         — Poppler (pdftoppm/pdftotext/pdftohtml), ~45 МБ, скачивается;
  * csv / tsv / json / xlsx — своими силами.
Скачанное сверяется с SHA-256 из API релиза GitHub, как и обновления
самого приложения.

Меню — статические ключи реестра текущего пользователя
(HKCU\\Software\\Classes\\SystemFileAssociations\\.<расш>\\shell\\HDContainer.Convert):
прав администратора не нужно; в Windows 11 пункт живёт в «Показать
дополнительные параметры» (Shift+ПКМ открывает это меню сразу).
"""

import csv
import glob
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import traceback
import urllib.request
import winreg
import zipfile
import xml.etree.ElementTree as ET

CREATE_NO_WINDOW = 0x08000000
LANG_GET = lambda: "en"
LOG = lambda m: None
MENU_VERSION = 2            # менять при изменении списка форматов -> меню перепишется

CSTR = {
    "menu": {"en": "Convert to", "ru": "Конвертировать в", "es": "Convertir a", "pt": "Converter para", "de": "Konvertieren in", "fr": "Convertir en", "zh": "转换为"},
    "tab": {"en": "Converter", "ru": "Конвертер", "es": "Conversor", "pt": "Conversor", "de": "Konverter", "fr": "Convertisseur", "zh": "转换器"},
    "setting": {"en": "\"Convert to\" in the Explorer right-click menu", "ru": "«Конвертировать в» в контекстном меню Проводника", "es": "\"Convertir a\" en el menú contextual del Explorador", "pt": "\"Converter para\" no menu de contexto do Explorador", "de": "„Konvertieren in“ im Kontextmenü des Explorers", "fr": "« Convertir en » dans le menu contextuel de l'Explorateur", "zh": "资源管理器右键菜单中的“转换为”"},
    "setting_note": {"en": "Right-click a file → Convert to → format. The result appears next to the original with the same name. On Windows 11 the item is under \"Show more options\" (or Shift + right-click).", "ru": "ПКМ по файлу → Конвертировать в → формат. Результат появляется рядом с исходником с тем же именем. В Windows 11 пункт — в «Показать дополнительные параметры» (или Shift + правый клик).", "es": "Clic derecho en un archivo → Convertir a → formato. El resultado aparece junto al original con el mismo nombre. En Windows 11 está en \"Mostrar más opciones\" (o Shift + clic derecho).", "pt": "Clique direito no arquivo → Converter para → formato. O resultado aparece ao lado do original com o mesmo nome. No Windows 11 fica em \"Mostrar mais opções\" (ou Shift + clique direito).", "de": "Rechtsklick auf eine Datei → Konvertieren in → Format. Das Ergebnis erscheint neben dem Original mit demselben Namen. Unter Windows 11 unter „Weitere Optionen anzeigen“ (oder Umschalt + Rechtsklick).", "fr": "Clic droit sur un fichier → Convertir en → format. Le résultat apparaît à côté de l'original avec le même nom. Sous Windows 11, dans « Afficher plus d'options » (ou Maj + clic droit).", "zh": "右键文件 → 转换为 → 格式。结果出现在原文件旁，同名。Windows 11 中位于“显示更多选项”（或 Shift + 右键）。"},
    "sec_tools": {"en": "Engines", "ru": "Движки", "es": "Motores", "pt": "Motores", "de": "Engines", "fr": "Moteurs", "zh": "引擎"},
    "tools_note": {"en": "Downloaded from GitHub on first use and verified by SHA-256; you can fetch them in advance here.", "ru": "Скачиваются с GitHub при первом использовании и проверяются по SHA-256; здесь их можно скачать заранее.", "es": "Se descargan de GitHub al primer uso y se verifican por SHA-256; aquí puedes descargarlos por adelantado.", "pt": "Baixados do GitHub no primeiro uso e verificados por SHA-256; aqui você pode baixá-los antes.", "de": "Werden bei der ersten Nutzung von GitHub geladen und per SHA-256 geprüft; hier können sie vorab geladen werden.", "fr": "Téléchargés depuis GitHub à la première utilisation et vérifiés par SHA-256 ; vous pouvez les récupérer à l'avance ici.", "zh": "首次使用时从 GitHub 下载并通过 SHA-256 校验；也可在此提前下载。"},
    "tool_ffmpeg": {"en": "audio, video, subtitles", "ru": "аудио, видео, субтитры", "es": "audio, vídeo, subtítulos", "pt": "áudio, vídeo, legendas", "de": "Audio, Video, Untertitel", "fr": "audio, vidéo, sous-titres", "zh": "音频、视频、字幕"},
    "tool_pandoc": {"en": "documents (docx, md, html, epub, fb2…)", "ru": "документы (docx, md, html, epub, fb2…)", "es": "documentos (docx, md, html, epub, fb2…)", "pt": "documentos (docx, md, html, epub, fb2…)", "de": "Dokumente (docx, md, html, epub, fb2…)", "fr": "documents (docx, md, html, epub, fb2…)", "zh": "文档（docx、md、html、epub、fb2…）"},
    "tool_poppler": {"en": "reading PDF (to images, text, docx…)", "ru": "чтение PDF (в картинки, текст, docx…)", "es": "lectura de PDF (a imágenes, texto, docx…)", "pt": "leitura de PDF (para imagens, texto, docx…)", "de": "PDF lesen (zu Bildern, Text, docx…)", "fr": "lecture de PDF (vers images, texte, docx…)", "zh": "读取 PDF（转为图片、文本、docx…）"},
    "installed": {"en": "installed", "ru": "установлен", "es": "instalado", "pt": "instalado", "de": "installiert", "fr": "installé", "zh": "已安装"},
    "not_installed": {"en": "not downloaded", "ru": "не скачан", "es": "no descargado", "pt": "não baixado", "de": "nicht geladen", "fr": "non téléchargé", "zh": "未下载"},
    "download": {"en": "Download", "ru": "Скачать", "es": "Descargar", "pt": "Baixar", "de": "Laden", "fr": "Télécharger", "zh": "下载"},
    "delete": {"en": "Delete", "ru": "Удалить", "es": "Eliminar", "pt": "Excluir", "de": "Löschen", "fr": "Supprimer", "zh": "删除"},
    "sec_pdf": {"en": "PDF output", "ru": "Вывод в PDF", "es": "Salida a PDF", "pt": "Saída em PDF", "de": "PDF-Ausgabe", "fr": "Sortie PDF", "zh": "输出 PDF"},
    "pdf_browser": {"en": "Documents, html and svg are printed to PDF with the browser built into Windows: %s", "ru": "Документы, html и svg печатаются в PDF встроенным в Windows браузером: %s", "es": "Documentos, html y svg se imprimen a PDF con el navegador integrado en Windows: %s", "pt": "Documentos, html e svg são impressos em PDF com o navegador integrado ao Windows: %s", "de": "Dokumente, html und svg werden mit dem in Windows integrierten Browser als PDF gedruckt: %s", "fr": "Les documents, html et svg sont imprimés en PDF avec le navigateur intégré à Windows : %s", "zh": "文档、html 和 svg 通过 Windows 内置浏览器打印为 PDF：%s"},
    "pdf_nobrowser": {"en": "Microsoft Edge or Google Chrome was not found — PDF output is unavailable.", "ru": "Microsoft Edge или Google Chrome не найден — вывод в PDF недоступен.", "es": "No se encontró Microsoft Edge ni Google Chrome — la salida a PDF no está disponible.", "pt": "Microsoft Edge ou Google Chrome não encontrado — a saída em PDF não está disponível.", "de": "Microsoft Edge oder Google Chrome wurde nicht gefunden — PDF-Ausgabe nicht verfügbar.", "fr": "Microsoft Edge ou Google Chrome introuvable — la sortie PDF est indisponible.", "zh": "未找到 Microsoft Edge 或 Google Chrome — 无法输出 PDF。"},
    "sec_formats": {"en": "What converts to what", "ru": "Что во что", "es": "Qué se convierte en qué", "pt": "O que vira o quê", "de": "Was wird zu was", "fr": "Quoi vers quoi", "zh": "可转换的格式"},
    "f_images": {"en": "Images", "ru": "Картинки", "es": "Imágenes", "pt": "Imagens", "de": "Bilder", "fr": "Images", "zh": "图片"},
    "f_audio": {"en": "Audio", "ru": "Аудио", "es": "Audio", "pt": "Áudio", "de": "Audio", "fr": "Audio", "zh": "音频"},
    "f_video": {"en": "Video", "ru": "Видео", "es": "Vídeo", "pt": "Vídeo", "de": "Video", "fr": "Vidéo", "zh": "视频"},
    "f_video_audio": {"en": "(+ audio track)", "ru": "(+ звуковая дорожка)", "es": "(+ pista de audio)", "pt": "(+ faixa de áudio)", "de": "(+ Tonspur)", "fr": "(+ piste audio)", "zh": "（+ 音轨）"},
    "f_docs": {"en": "Documents", "ru": "Документы", "es": "Documentos", "pt": "Documentos", "de": "Dokumente", "fr": "Documents", "zh": "文档"},
    "f_pdf": {"en": "PDF", "ru": "PDF", "es": "PDF", "pt": "PDF", "de": "PDF", "fr": "PDF", "zh": "PDF"},
    "f_pdf_pages": {"en": "(one image per page)", "ru": "(картинка на каждую страницу)", "es": "(una imagen por página)", "pt": "(uma imagem por página)", "de": "(ein Bild pro Seite)", "fr": "(une image par page)", "zh": "（每页一张图片）"},
    "f_tables": {"en": "Tables", "ru": "Таблицы", "es": "Tablas", "pt": "Tabelas", "de": "Tabellen", "fr": "Tableaux", "zh": "表格"},
    "f_tables_note": {"en": "(xlsx: first sheet)", "ru": "(xlsx: первый лист)", "es": "(xlsx: primera hoja)", "pt": "(xlsx: primeira planilha)", "de": "(xlsx: erstes Blatt)", "fr": "(xlsx : première feuille)", "zh": "（xlsx：第一个工作表）"},
    "f_subs": {"en": "Subtitles", "ru": "Субтитры", "es": "Subtítulos", "pt": "Legendas", "de": "Untertitel", "fr": "Sous-titres", "zh": "字幕"},
    "title": {"en": "Convert", "ru": "Конвертация", "es": "Convertir", "pt": "Converter", "de": "Konvertieren", "fr": "Conversion", "zh": "转换"},
    "working": {"en": "Converting %s → %s…", "ru": "Конвертирую %s → %s…", "es": "Convirtiendo %s → %s…", "pt": "Convertendo %s → %s…", "de": "Konvertiere %s → %s…", "fr": "Conversion %s → %s…", "zh": "正在转换 %s → %s…"},
    "done": {"en": "Done: %s", "ru": "Готово: %s", "es": "Listo: %s", "pt": "Pronto: %s", "de": "Fertig: %s", "fr": "Terminé : %s", "zh": "完成：%s"},
    "done_pages": {"en": "Done: %d files (%s…)", "ru": "Готово: %d файлов (%s…)", "es": "Listo: %d archivos (%s…)", "pt": "Pronto: %d arquivos (%s…)", "de": "Fertig: %d Dateien (%s…)", "fr": "Terminé : %d fichiers (%s…)", "zh": "完成：%d 个文件（%s…）"},
    "failed": {"en": "Conversion failed", "ru": "Не удалось сконвертировать", "es": "Error de conversión", "pt": "Falha na conversão", "de": "Konvertierung fehlgeschlagen", "fr": "Échec de la conversion", "zh": "转换失败"},
    "need_tool": {"en": "%s (about %d MB) is needed for this conversion. Download it now from GitHub (%s)?", "ru": "Для этой конвертации нужен %s (около %d МБ). Скачать сейчас с GitHub (%s)?", "es": "Para esta conversión se necesita %s (unos %d MB). ¿Descargarlo ahora desde GitHub (%s)?", "pt": "Esta conversão precisa de %s (cerca de %d MB). Baixar agora do GitHub (%s)?", "de": "Für diese Konvertierung wird %s (etwa %d MB) benötigt. Jetzt von GitHub laden (%s)?", "fr": "Cette conversion nécessite %s (environ %d Mo). Le télécharger maintenant depuis GitHub (%s) ?", "zh": "此转换需要 %s（约 %d MB）。现在从 GitHub（%s）下载吗？"},
    "downloading": {"en": "Downloading %s… %d%%", "ru": "Скачиваю %s… %d%%", "es": "Descargando %s… %d%%", "pt": "Baixando %s… %d%%", "de": "Lade %s… %d%%", "fr": "Téléchargement de %s… %d%%", "zh": "正在下载 %s… %d%%"},
    "cancel": {"en": "Cancel", "ru": "Отмена", "es": "Cancelar", "pt": "Cancelar", "de": "Abbrechen", "fr": "Annuler", "zh": "取消"},
    "close": {"en": "Close", "ru": "Закрыть", "es": "Cerrar", "pt": "Fechar", "de": "Schließen", "fr": "Fermer", "zh": "关闭"},
    "tool_fail": {"en": "Couldn't download %s: %s", "ru": "Не удалось скачать %s: %s", "es": "No se pudo descargar %s: %s", "pt": "Não foi possível baixar %s: %s", "de": "%s konnte nicht geladen werden: %s", "fr": "Impossible de télécharger %s : %s", "zh": "无法下载 %s：%s"},
    "no_pil": {"en": "Image support (Pillow) is not available in this build", "ru": "В этой сборке нет поддержки изображений (Pillow)", "es": "Esta compilación no incluye soporte de imágenes (Pillow)", "pt": "Esta versão não inclui suporte a imagens (Pillow)", "de": "Diese Version enthält keine Bildunterstützung (Pillow)", "fr": "Cette version n'inclut pas la prise en charge des images (Pillow)", "zh": "此版本不包含图片支持（Pillow）"},
}


def C(key, *args):
    s = CSTR.get(key, {})
    txt = s.get(LANG_GET()) or s.get("en") or key
    return (txt % args) if args else txt


# ---------------------------------------------------------------------------
#  Что во что можно
# ---------------------------------------------------------------------------
IMAGE_SRC = {"png", "jpg", "jpeg", "jfif", "webp", "bmp", "gif", "tif", "tiff", "ico", "avif"}
IMAGE_DST = ["png", "jpg", "webp", "avif", "bmp", "gif", "ico", "tiff", "pdf"]
SVG_SRC = {"svg"}
SVG_DST = ["png", "jpg", "pdf"]
AUDIO_SRC = {"mp3", "wav", "ogg", "oga", "flac", "m4a", "aac", "wma", "opus", "aiff", "aif", "amr"}
AUDIO_DST = ["mp3", "wav", "ogg", "flac", "m4a", "aac", "opus"]
VIDEO_SRC = {"mp4", "mkv", "mov", "avi", "webm", "wmv", "m4v", "ts", "flv", "mpg", "mpeg", "3gp", "mts", "vob"}
VIDEO_DST = ["mp4", "mkv", "webm", "mov", "avi", "gif"]
SUB_SRC = {"srt", "vtt", "ass", "ssa"}
SUB_DST = ["srt", "vtt", "ass"]
DOC_SRC = {"md", "markdown", "docx", "html", "htm", "txt", "rtf", "odt", "epub", "fb2", "tex", "rst", "org"}
DOC_DST = ["pdf", "docx", "md", "html", "txt", "rtf", "odt", "epub", "fb2", "tex", "rst"]
PDF_SRC = {"pdf"}
PDF_DST = ["png", "jpg", "txt", "html", "docx", "md", "epub", "odt", "rtf"]
DATA_SRC = {"csv", "tsv", "json", "xlsx"}
DATA_DST = ["csv", "tsv", "json", "xlsx", "html", "pdf"]

PANDOC_FROM = {"md": "markdown", "markdown": "markdown", "txt": "markdown", "html": "html",
               "htm": "html", "docx": "docx", "rtf": "rtf", "odt": "odt", "epub": "epub",
               "fb2": "fb2", "tex": "latex", "rst": "rst", "org": "org"}
PANDOC_TO = {"md": "gfm", "html": "html", "txt": "plain", "docx": "docx", "rtf": "rtf",
             "odt": "odt", "epub": "epub", "fb2": "fb2", "tex": "latex", "rst": "rst"}

_SAME = {("jpeg", "jpg"), ("jfif", "jpg"), ("tif", "tiff"), ("markdown", "md"), ("htm", "html"),
         ("oga", "ogg"), ("aif", "aiff"), ("ssa", "ass"), ("mpeg", "mpg")}


def _not_self(ext, dsts):
    return [t for t in dsts if t != ext and (ext, t) not in _SAME]


def targets_for(ext):
    """Список целевых расширений для исходного (без точки, нижний регистр)."""
    ext = ext.lower()
    out = []
    if ext in IMAGE_SRC:
        out += _not_self(ext, IMAGE_DST)
    if ext in SVG_SRC:
        out += SVG_DST
    if ext in AUDIO_SRC:
        out += _not_self(ext, AUDIO_DST)
    if ext in VIDEO_SRC:
        out += _not_self(ext, VIDEO_DST) + ["mp3", "wav", "m4a"]   # звуковая дорожка
    if ext in SUB_SRC:
        out += _not_self(ext, SUB_DST)
    if ext in DOC_SRC:
        out += _not_self(ext, DOC_DST)
    if ext in PDF_SRC:
        out += PDF_DST
    if ext in DATA_SRC:
        out += _not_self(ext, DATA_DST)
    return out


def all_source_exts():
    return sorted(IMAGE_SRC | SVG_SRC | AUDIO_SRC | VIDEO_SRC | SUB_SRC | DOC_SRC | PDF_SRC | DATA_SRC)


def engine_for(src_ext, fmt):
    """Какой движок нужен: image / browser / ffmpeg / pandoc / poppler / data — или None."""
    if src_ext in IMAGE_SRC and fmt in IMAGE_DST:
        return "image"
    if src_ext in SVG_SRC and fmt in SVG_DST:
        return "browser"
    if src_ext in DATA_SRC and fmt in DATA_DST:
        return "data"
    if src_ext in PDF_SRC and fmt in PDF_DST:
        return "poppler"
    if src_ext in DOC_SRC and fmt in DOC_DST:
        return "pandoc"
    if (src_ext in AUDIO_SRC or src_ext in VIDEO_SRC) and (fmt in AUDIO_DST or fmt in VIDEO_DST):
        return "ffmpeg"
    if src_ext in SUB_SRC and fmt in SUB_DST:
        return "ffmpeg"
    return None


def tools_needed(src_ext, fmt):
    """Какие скачиваемые инструменты и нужен ли браузер (для PDF)."""
    eng = engine_for(src_ext, fmt)
    tools, browser = [], False
    if eng == "ffmpeg":
        tools = ["ffmpeg"]
    elif eng == "pandoc":
        if src_ext not in ("html", "htm", "txt") or fmt != "pdf":
            tools = ["pandoc"]
        browser = fmt == "pdf"
    elif eng == "poppler":
        tools = ["poppler"] + (["pandoc"] if fmt in ("docx", "md", "epub", "odt", "rtf") else [])
    elif eng == "browser":
        browser = True
    elif eng == "data":
        browser = fmt == "pdf"
    return tools, browser


# ---------------------------------------------------------------------------
#  Внешние инструменты: скачивание с проверкой SHA-256 из API релиза
# ---------------------------------------------------------------------------
TOOLS = {
    # имя: (репозиторий, тег, файл архива, главный exe, правило распаковки, ~МБ)
    #   правило: ("names", [...]) — только файлы с такими именами;
    #            ("dir", "bin")   — все файлы из папок с таким именем (exe + dll)
    "ffmpeg": ("BtbN/FFmpeg-Builds", "latest", "ffmpeg-master-latest-win64-gpl.zip",
               "ffmpeg.exe", ("names", ["ffmpeg.exe"]), 190),
    "pandoc": ("jgm/pandoc", "3.12.1", "pandoc-3.12.1-windows-x86_64.zip",
               "pandoc.exe", ("names", ["pandoc.exe"]), 40),
    "poppler": ("oschwartz10612/poppler-windows", "v26.09.0-0", "Release-26.09.0-0.zip",
                "pdftoppm.exe", ("dir", "bin"), 45),
}
TOOL_ORDER = ["ffmpeg", "pandoc", "poppler"]


def tools_dir():
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return os.path.join(base, "HDContainer", "tools")


def tool_exe(name, exe=None):
    return os.path.join(tools_dir(), name, exe or TOOLS[name][3])


def tool_installed(name):
    return os.path.isfile(tool_exe(name))


def tool_size_mb(name):
    total = 0
    for root, _d, files in os.walk(os.path.join(tools_dir(), name)):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total // 1048576


def delete_tool(name):
    shutil.rmtree(os.path.join(tools_dir(), name), ignore_errors=True)


def _release_asset(repo, tag, asset):
    """(url, sha256) файла релиза из API GitHub. Релиз «latest» у BtbN — это тег."""
    url = "https://api.github.com/repos/%s/releases/tags/%s" % (repo, tag)
    req = urllib.request.Request(url, headers={"User-Agent": "HDContainer-convert",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read(8 * 1024 * 1024).decode("utf-8"))
    for a in data.get("assets", []):
        if a.get("name") == asset:
            d = str(a.get("digest") or "")
            if not d.startswith("sha256:"):
                raise RuntimeError("release asset has no sha256 digest")
            return a["browser_download_url"], d[7:].lower()
    raise RuntimeError("asset %s not found in %s@%s" % (asset, repo, tag))


def install_tool(name, progress=None, cancelled=lambda: False):
    """Скачать и распаковать инструмент. progress(done, total)."""
    repo, tag, asset, main_exe, rule, _mb = TOOLS[name]
    url, sha = _release_asset(repo, tag, asset)
    dst_dir = os.path.join(tools_dir(), name)
    os.makedirs(dst_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=name + "-", suffix=".zip.part", dir=dst_dir)
    os.close(fd)
    try:
        h = hashlib.sha256()
        req = urllib.request.Request(url, headers={"User-Agent": "HDContainer-convert"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.getheader("Content-Length") or 0)
            got = 0
            while True:
                if cancelled():
                    raise RuntimeError("cancelled")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
        if h.hexdigest() != sha:
            raise RuntimeError("sha256 mismatch")
        kind, what = rule
        with zipfile.ZipFile(tmp) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                parts = info.filename.replace("\\", "/").split("/")
                base = parts[-1]
                # пишем только по ИМЕНИ файла в свою папку: пути из архива не
                # используем (zip-slip), вложенность сплющиваем
                ok = base in what if kind == "names" else (what in parts[:-1])
                if not ok or not base:
                    continue
                with z.open(info) as src, open(os.path.join(dst_dir, base), "wb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
        if not tool_installed(name):
            raise RuntimeError("%s not found in archive" % main_exe)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  Браузер (Edge / Chrome) для печати в PDF и снимков svg
# ---------------------------------------------------------------------------
BROWSER_EXE = None          # можно подменить (тесты)


def find_browser():
    if BROWSER_EXE:
        return BROWSER_EXE
    cands = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    for exe in ("msedge.exe", "chrome.exe"):
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\App Paths\%s" % exe) as k:
                    cands.append(winreg.QueryValueEx(k, "")[0].strip('"'))
            except Exception:
                pass
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ""


def _browser(args, timeout=180):
    exe = find_browser()
    if not exe:
        raise ConvertError(C("pdf_nobrowser"))
    prof = tempfile.mkdtemp(prefix="hdc-browser-")     # свой профиль: не мешать открытому Edge
    try:
        _run([exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
              "--disable-extensions", "--disable-sync", "--user-data-dir=" + prof] + args,
             timeout=timeout)
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def _file_url(path):
    return "file:///" + os.path.abspath(path).replace("\\", "/")


def html_to_pdf(src_html, dst):
    _browser(["--no-pdf-header-footer", "--print-to-pdf=" + dst, _file_url(src_html)])
    if not os.path.isfile(dst):
        raise ConvertError("browser produced no PDF")


def _svg_size(path):
    try:
        root = ET.parse(path).getroot()
        w, h = root.get("width"), root.get("height")
        vb = root.get("viewBox")
        if (not w or not h) and vb:
            _x, _y, w, h = vb.replace(",", " ").split()
        w, h = float(re.sub(r"[^0-9.]", "", w)), float(re.sub(r"[^0-9.]", "", h))
        if w > 0 and h > 0:
            return int(min(w, 4000)), int(min(h, 4000))
    except Exception:
        pass
    return 1600, 1200


def svg_to_png(src, dst):
    w, h = _svg_size(src)
    _browser(["--hide-scrollbars", "--window-size=%d,%d" % (w, h), "--screenshot=" + dst,
              _file_url(src)])
    if not os.path.isfile(dst):
        raise ConvertError("browser produced no image")


_PRINT_CSS = ("<style>body{font-family:Segoe UI,Arial,sans-serif;font-size:12pt;line-height:1.45;"
              "max-width:none;margin:0.6in 0.7in}img{max-width:100%%}pre{white-space:pre-wrap;"
              "font-family:Consolas,monospace;font-size:10.5pt}table{border-collapse:collapse}"
              "td,th{border:1px solid #999;padding:3px 7px}</style>")


def text_to_html(text, title=""):
    return ("<!doctype html><html><head><meta charset='utf-8'><title>%s</title>%s</head>"
            "<body><pre>%s</pre></body></html>" % (html.escape(title), _PRINT_CSS, html.escape(text)))


# ---------------------------------------------------------------------------
#  Собственно конвертация
# ---------------------------------------------------------------------------
class ConvertError(Exception):
    pass


def output_path(src, fmt):
    d, name = os.path.split(src)
    stem = os.path.splitext(name)[0]
    out = os.path.join(d, stem + "." + fmt)
    n = 2
    while os.path.exists(out):
        out = os.path.join(d, "%s (%d).%s" % (stem, n, fmt))
        n += 1
    return out


def _run(args, timeout=3600, cwd=None):
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW, cwd=cwd)
    try:                                       # ребёнок умирает вместе с нами (окно закрыли)
        import voice_sys
        voice_sys.adopt_process(p)
    except Exception:
        pass
    out, err = p.communicate(timeout=timeout)
    if p.returncode != 0:
        lines = (err or out or b"").decode("utf-8", "replace").strip().splitlines()
        raise ConvertError(lines[-1][:300] if lines else "exit code %d" % p.returncode)


def _pil():
    try:
        from PIL import Image
        return Image
    except Exception:
        raise ConvertError(C("no_pil"))


def _save_image(im, dst, fmt):
    Image = _pil()
    if fmt == "gif" and getattr(im, "n_frames", 1) > 1:
        im.save(dst, save_all=True)           # анимацию сохраняем целиком
        return
    if fmt in ("jpg", "pdf", "bmp"):
        if im.mode in ("RGBA", "LA", "P"):    # без альфы: прозрачное -> на белом
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
    elif im.mode == "P" and fmt != "gif":
        im = im.convert("RGBA")
    kw = {}
    if fmt == "jpg":
        kw = {"quality": 92, "subsampling": 0}
    elif fmt == "webp":
        kw = {"quality": 90, "method": 6}
    elif fmt == "avif":
        kw = {"quality": 80}
    elif fmt == "ico":
        im.thumbnail((256, 256))
        kw = {"sizes": [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]}
    elif fmt == "pdf":
        kw = {"resolution": 100.0}
    im.save(dst, format={"jpg": "JPEG", "tiff": "TIFF"}.get(fmt, fmt.upper()), **kw)


def convert_image(src, dst, fmt):
    im = _pil().open(src)
    _save_image(im, dst, fmt)


def convert_svg(src, dst, fmt):
    if fmt == "pdf":
        html_to_pdf(src, dst)
        return
    if fmt == "png":
        svg_to_png(src, dst)
        return
    tmp = dst + ".png"
    svg_to_png(src, tmp)
    try:
        _save_image(_pil().open(tmp), dst, fmt)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def convert_ffmpeg(src, dst, fmt, src_ext):
    exe = tool_exe("ffmpeg")
    base = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", src]
    if src_ext in SUB_SRC:
        _run(base + [dst])
        return
    if fmt == "gif":
        vf = "fps=12,scale='min(640,iw)':-2:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse"
        _run(base + ["-vf", vf, "-loop", "0", dst])
        return
    if fmt in AUDIO_DST and src_ext in VIDEO_SRC:
        base += ["-vn"]                        # только звуковая дорожка
    codec = {"mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
             "ogg": ["-c:a", "libvorbis", "-q:a", "6"],
             "opus": ["-c:a", "libopus", "-b:a", "128k"],
             "m4a": ["-c:a", "aac", "-b:a", "192k"],
             "aac": ["-c:a", "aac", "-b:a", "192k"],
             "flac": ["-c:a", "flac"], "wav": ["-c:a", "pcm_s16le"],
             "webm": ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32", "-c:a", "libopus"],
             "mp4": ["-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
             "mov": ["-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac"],
             "mkv": ["-c:v", "libx264", "-crf", "20", "-c:a", "aac"],
             "avi": ["-c:v", "mpeg4", "-q:v", "4", "-c:a", "libmp3lame", "-q:a", "3"]}.get(fmt, [])
    if fmt in ("mp4", "mkv", "mov") and src_ext in ("mp4", "mkv", "mov", "m4v"):
        try:                                   # та же кодировка в другом контейнере — без перекодирования
            _run(base + ["-c", "copy", dst])
            return
        except ConvertError:
            pass
    _run(base + codec + [dst])


def _pandoc(src, dst, src_fmt, dst_fmt, extra=()):
    args = [tool_exe("pandoc"), src, "-f", src_fmt, "-t", dst_fmt, "-s", "-o", dst] + list(extra)
    _run(args, timeout=600)


def convert_doc(src, dst, fmt, src_ext):
    if fmt != "pdf":
        extra = ["--wrap=none"] if fmt == "txt" else []
        _pandoc(src, dst, PANDOC_FROM[src_ext], PANDOC_TO[fmt], extra)
        return
    # -> PDF: html (свой или через pandoc) печатает браузер
    tmpd = tempfile.mkdtemp(prefix="hdc-pdf-")
    try:
        page = os.path.join(tmpd, "page.html")
        if src_ext in ("html", "htm"):
            page = src
        elif src_ext == "txt":
            with open(src, "r", encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
            with open(page, "w", encoding="utf-8") as f:
                f.write(text_to_html(text, os.path.basename(src)))
        else:
            # картинки и стили внутрь html, чтобы браузер видел их из temp-папки
            _pandoc(src, page, PANDOC_FROM[src_ext], "html",
                    ["--embed-resources", "--resource-path=" + os.path.dirname(src)])
            with open(page, "r", encoding="utf-8", errors="replace") as f:
                h = f.read()
            with open(page, "w", encoding="utf-8") as f:
                f.write(h.replace("</head>", _PRINT_CSS + "</head>", 1) if "</head>" in h
                        else _PRINT_CSS + h)
        html_to_pdf(page, dst)
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def convert_pdf(src, dst, fmt):
    """PDF -> картинки (файл на страницу) / текст / html / документы."""
    if fmt in ("png", "jpg"):
        prefix = os.path.splitext(dst)[0]
        _run([tool_exe("poppler"), "-r", "150", "-png" if fmt == "png" else "-jpeg", src, prefix],
             timeout=1800)
        pages = sorted(glob.glob(glob.escape(prefix) + "-*." + fmt))
        if not pages:
            raise ConvertError("no pages rendered")
        if len(pages) == 1:
            os.replace(pages[0], dst)
            return [dst]
        return pages
    if fmt == "txt":
        _run([tool_exe("poppler", "pdftotext.exe"), "-layout", "-enc", "UTF-8", src, dst])
        return [dst]
    tmpd = tempfile.mkdtemp(prefix="hdc-pdf-")
    try:
        base = os.path.join(tmpd, "doc")
        _run([tool_exe("poppler", "pdftohtml.exe"), "-s", "-i", "-noframes", "-enc", "UTF-8",
              "-q", src, base], timeout=1800)
        page = base + ".html" if os.path.isfile(base + ".html") else base + "-html.html"
        if not os.path.isfile(page):
            raise ConvertError("pdftohtml produced no html")
        if fmt == "html":
            shutil.copyfile(page, dst)
        else:
            _pandoc(page, dst, "html", PANDOC_TO[fmt])
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    return [dst]


# ---- таблицы: csv / tsv / json / xlsx ----
_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _col_index(ref):
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return n - 1


def read_xlsx(path):
    """Первый лист книги -> список строк (списки строк)."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(_XLSX_NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_XLSX_NS + "t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {r.get("Id"): r.get("Target") for r in rels.iter(_REL_NS + "Relationship")}
        sheets = list(wb.iter(_XLSX_NS + "sheet"))
        if not sheets:
            raise ConvertError("workbook has no sheets")
        rid = sheets[0].get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = rid_to_target.get(rid, "worksheets/sheet1.xml").lstrip("/")
        sheet_path = target if target.startswith("xl/") else "xl/" + target
        root = ET.fromstring(z.read(sheet_path))
    rows = []
    for row in root.iter(_XLSX_NS + "row"):
        cells = []
        for c in row.findall(_XLSX_NS + "c"):
            idx = _col_index(c.get("r", ""))
            t = c.get("t")
            v = c.find(_XLSX_NS + "v")
            if t == "s":
                val = shared[int(v.text)] if v is not None and v.text else ""
            elif t == "inlineStr":
                is_ = c.find(_XLSX_NS + "is")
                val = "".join(x.text or "" for x in is_.iter(_XLSX_NS + "t")) if is_ is not None else ""
            elif t == "b":
                val = "TRUE" if (v is not None and v.text == "1") else "FALSE"
            else:
                val = v.text if v is not None and v.text is not None else ""
                if re.fullmatch(r"-?\d+\.0+", val):
                    val = val.split(".")[0]
            while len(cells) < idx:
                cells.append("")
            if idx >= 0:
                cells.append(val)
        rows.append(cells)
    return rows


def write_xlsx(path, rows):
    """Минимальная книга xlsx с одним листом (строки — списки значений)."""
    def cell(ref, val):
        s = "" if val is None else str(val)
        if re.fullmatch(r"-?\d+(\.\d+)?", s) and len(s) < 16 and not (len(s) > 1 and s[0] == "0" and s[1] != "."):
            return '<c r="%s"><v>%s</v></c>' % (ref, s)
        return '<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (
            ref, html.escape(s, quote=False))

    def col_name(i):
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s
    body = []
    for ri, row in enumerate(rows, 1):
        body.append('<row r="%d">%s</row>' % (ri, "".join(cell("%s%d" % (col_name(ci), ri), v)
                                                           for ci, v in enumerate(row))))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>%s</sheetData></worksheet>' % "".join(body))
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)


def _read_table(src, ext):
    if ext == "xlsx":
        rows = read_xlsx(src)
        return (rows[0], rows[1:]) if rows else ([], [])
    with open(src, "r", encoding="utf-8-sig", newline="") as f:
        if ext == "json":
            data = json.load(f)
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                raise ConvertError("JSON is not a list of objects")
            rows = [r if isinstance(r, dict) else {"value": r} for r in data]
            cols = []
            for r in rows:
                for k in r:
                    if k not in cols:
                        cols.append(k)
            return cols, [[("" if r.get(c) is None else
                            (json.dumps(r[c], ensure_ascii=False) if isinstance(r[c], (dict, list))
                             else str(r[c]))) for c in cols] for r in rows]
        dialect = "excel-tab" if ext == "tsv" else "excel"
        rows = list(csv.reader(f, dialect=dialect))
        if not rows:
            return [], []
        return rows[0], rows[1:]


def table_to_html(cols, rows, title=""):
    def tr(cells, tag):
        return "<tr>" + "".join("<%s>%s</%s>" % (tag, html.escape(str(c)), tag) for c in cells) + "</tr>"
    return ("<!doctype html><html><head><meta charset='utf-8'><title>%s</title>%s</head><body>"
            "<table>%s%s</table></body></html>" % (html.escape(title), _PRINT_CSS, tr(cols, "th"),
                                                   "".join(tr(r, "td") for r in rows)))


def convert_data(src, dst, fmt, src_ext):
    cols, rows = _read_table(src, src_ext)
    if fmt == "json":
        out = [dict(zip(cols, r + [""] * (len(cols) - len(r)))) for r in rows]
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    elif fmt == "xlsx":
        write_xlsx(dst, [cols] + rows)
    elif fmt == "html":
        with open(dst, "w", encoding="utf-8") as f:
            f.write(table_to_html(cols, rows, os.path.basename(src)))
    elif fmt == "pdf":
        tmpd = tempfile.mkdtemp(prefix="hdc-pdf-")
        try:
            page = os.path.join(tmpd, "table.html")
            with open(page, "w", encoding="utf-8") as f:
                f.write(table_to_html(cols, rows, os.path.basename(src)))
            html_to_pdf(page, dst)
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)
    else:
        with open(dst, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, dialect="excel-tab" if fmt == "tsv" else "excel")
            w.writerow(cols)
            w.writerows(rows)


def convert(src, fmt):
    """Сконвертировать файл. Возвращает список созданных файлов (обычно один)."""
    src_ext = os.path.splitext(src)[1].lstrip(".").lower()
    fmt = fmt.lower()
    engine = engine_for(src_ext, fmt)
    if not engine:
        raise ConvertError("%s -> %s is not supported" % (src_ext, fmt))
    if not os.path.isfile(src):
        raise ConvertError("file not found")
    dst = output_path(src, fmt)
    try:
        if engine == "image":
            convert_image(src, dst, fmt)
        elif engine == "browser":
            convert_svg(src, dst, fmt)
        elif engine == "ffmpeg":
            convert_ffmpeg(src, dst, fmt, src_ext)
        elif engine == "pandoc":
            convert_doc(src, dst, fmt, src_ext)
        elif engine == "poppler":
            return convert_pdf(src, dst, fmt)
        else:
            convert_data(src, dst, fmt, src_ext)
    except Exception:
        try:
            if os.path.exists(dst):
                os.remove(dst)              # не оставлять обрезок
        except Exception:
            pass
        raise
    return [dst]


# ---------------------------------------------------------------------------
#  Контекстное меню Проводника (реестр HKCU)
# ---------------------------------------------------------------------------
_VERB = "HDContainer.Convert"


def _assoc_key(ext):
    return r"Software\Classes\SystemFileAssociations\.%s\shell\%s" % (ext, _VERB)


def _delete_tree(root, path):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(root, path + "\\" + sub)
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def menu_registered(exe):
    """Зарегистрировано ли меню ИМЕННО для этого exe, на текущем языке и для
    текущего набора форматов (после обновления, смены языка или расширения
    списка форматов ключи надо перезаписать)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _assoc_key("png")) as k:
            verb, _t = winreg.QueryValueEx(k, "MUIVerb")
            ver, _t = winreg.QueryValueEx(k, "HDCVersion")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            _assoc_key("png") + r"\shell\00_jpg\command") as k:
            cmd, _t = winreg.QueryValueEx(k, "")
        return (verb == C("menu") and int(ver) == MENU_VERSION
                and cmd.lower().startswith('"%s"' % exe.lower()))
    except Exception:
        return False


def menu_present():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _assoc_key("png")):
            return True
    except Exception:
        return False


def register_menu(exe):
    hk = winreg.HKEY_CURRENT_USER
    for ext in all_source_exts():
        _delete_tree(hk, _assoc_key(ext))
        with winreg.CreateKey(hk, _assoc_key(ext)) as k:
            winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, C("menu"))
            winreg.SetValueEx(k, "SubCommands", 0, winreg.REG_SZ, "")
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, '"%s",0' % exe)
            winreg.SetValueEx(k, "HDCVersion", 0, winreg.REG_DWORD, MENU_VERSION)
        for i, fmt in enumerate(targets_for(ext)):
            # имя подключа задаёт порядок -> нумеруем
            sub = _assoc_key(ext) + r"\shell\%02d_%s" % (i, fmt)
            with winreg.CreateKey(hk, sub) as k:
                winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, fmt.upper())
            with winreg.CreateKey(hk, sub + r"\command") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ,
                                  '"%s" --convert "%%1" %s' % (exe, fmt))


def unregister_menu():
    for ext in all_source_exts():
        _delete_tree(winreg.HKEY_CURRENT_USER, _assoc_key(ext))


# ---------------------------------------------------------------------------
#  Вкладка «Конвертер» в окне настроек
# ---------------------------------------------------------------------------
def build_settings(app, parent, win, pad=24):
    import tkinter as tk
    main = __import__("__main__")
    col = lambda n, d: getattr(main, n, d)
    SF, SF2, TX, DIM, AC, BD = (col("COL_SURFACE", "#232427"), col("COL_SURFACE2", "#2d2e31"),
                                col("COL_TEXT", "#e6e8ea"), col("COL_TEXT_DIM", "#9aa0a6"),
                                col("COL_ACCENT", "#4c8bf5"), col("COL_BORDER", "#2a2b2c"))
    cv = tk.Canvas(parent, bg=SF, highlightthickness=0, bd=0)
    sb = app._dark_scrollbar(parent, cv)
    sb.pack(side="right", fill="y")
    cv.pack(side="left", fill="both", expand=True)
    body = tk.Frame(cv, bg=SF)
    wid = cv.create_window((0, 0), window=body, anchor="nw")
    body.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
    cv.bind("<Configure>", lambda e: cv.itemconfigure(wid, width=e.width))
    win.bind("<MouseWheel>", lambda e: cv.yview_scroll(int(-e.delta / 120) * 3, "units"))

    def h(text):
        tk.Label(body, text=text, bg=SF, fg=TX, font=("Segoe UI Semibold", 11)).pack(
            anchor="w", padx=pad, pady=(20, 6))

    def note(text, indent=0, pady=(0, 4)):
        tk.Label(body, text=text, bg=SF, fg=DIM, font=("Segoe UI", 9), wraplength=540,
                 justify="left").pack(anchor="w", padx=pad + indent, pady=pady)

    tk.Frame(body, bg=SF, height=10).pack()
    d = app._gen_draft
    app._setting_check(body, C("setting"), d["convert_menu"],
                       lambda v: d.__setitem__("convert_menu", bool(v)), pad)
    note(C("setting_note"), indent=30, pady=(0, 6))

    # движки
    h(C("sec_tools"))
    note(C("tools_note"))
    rows = {}

    def refresh(name):
        lbl, btn = rows[name]
        if tool_installed(name):
            lbl.configure(text="● %s · %d MB" % (C("installed"), tool_size_mb(name)), fg="#34d399")
            btn.configure(text=C("delete"))
        else:
            lbl.configure(text="○ %s · ~%d MB" % (C("not_installed"), TOOLS[name][5]), fg=DIM)
            btn.configure(text=C("download"))

    def action(name):
        lbl, btn = rows[name]
        if tool_installed(name):
            delete_tool(name)
            refresh(name)
            return
        if getattr(btn, "_busy", False):
            return
        btn._busy = True
        st = {"cancel": False}

        def prog(done, total):
            pct = int(done * 100 / total) if total else 0
            win.after(0, lambda: lbl.winfo_exists() and lbl.configure(
                text=C("downloading", name, pct), fg=AC))

        def work():
            try:
                install_tool(name, prog, lambda: st["cancel"] or not win.winfo_exists())
            except Exception as ex:
                LOG("tool download failed: %s: %r" % (name, ex))
                err = str(ex)[:80]
                win.after(0, lambda: lbl.winfo_exists() and lbl.configure(
                    text=C("tool_fail", name, err), fg=col("COL_DANGER", "#ff453a")))
                btn._busy = False
                return
            btn._busy = False
            win.after(0, lambda: lbl.winfo_exists() and refresh(name))
        threading.Thread(target=work, daemon=True).start()

    for name in TOOL_ORDER:
        card = tk.Frame(body, bg=SF2)
        card.pack(fill="x", padx=pad, pady=(0, 8))
        left = tk.Frame(card, bg=SF2)
        left.pack(side="left", fill="x", expand=True, padx=12, pady=8)
        tk.Label(left, text=name.capitalize() + "  —  " + C("tool_" + name), bg=SF2, fg=TX,
                 font=("Segoe UI", 10)).pack(anchor="w")
        lbl = tk.Label(left, text="", bg=SF2, fg=DIM, font=("Segoe UI", 9))
        lbl.pack(anchor="w")
        btn = app._ghost_btn(card, "", lambda n=name: action(n))
        btn.configure(font=("Segoe UI", 9), padx=10, pady=4)
        btn.pack(side="right", padx=12)
        rows[name] = (lbl, btn)
        refresh(name)

    # PDF
    h(C("sec_pdf"))
    br = find_browser()
    note(C("pdf_browser", br) if br else C("pdf_nobrowser"))

    # форматы
    h(C("sec_formats"))
    fmt_rows = [
        (C("f_images"), ", ".join(sorted(IMAGE_SRC)) + "  →  " + ", ".join(IMAGE_DST)),
        ("SVG", "svg  →  " + ", ".join(SVG_DST)),
        (C("f_audio"), ", ".join(sorted(AUDIO_SRC)) + "  →  " + ", ".join(AUDIO_DST)),
        (C("f_video"), ", ".join(sorted(VIDEO_SRC)) + "  →  " + ", ".join(VIDEO_DST)
         + ", mp3, wav, m4a " + C("f_video_audio")),
        (C("f_subs"), ", ".join(sorted(SUB_SRC)) + "  →  " + ", ".join(SUB_DST)),
        (C("f_docs"), ", ".join(sorted(DOC_SRC)) + "  →  " + ", ".join(DOC_DST)),
        (C("f_pdf"), "pdf  →  " + ", ".join(PDF_DST) + "  " + C("f_pdf_pages")),
        (C("f_tables"), ", ".join(sorted(DATA_SRC)) + "  →  " + ", ".join(DATA_DST)
         + "  " + C("f_tables_note")),
    ]
    for title, text in fmt_rows:
        tk.Label(body, text=title, bg=SF, fg=TX, font=("Segoe UI Semibold", 9)).pack(
            anchor="w", padx=pad, pady=(6, 0))
        tk.Label(body, text=text, bg=SF, fg=DIM, font=("Segoe UI", 9), wraplength=540,
                 justify="left").pack(anchor="w", padx=pad)
    tk.Frame(body, bg=SF, height=16).pack()
    tk.Frame(body, bg=BD, height=0).pack()


# ---------------------------------------------------------------------------
#  Запуск из меню: --convert <файл> <формат>  (отдельный процесс, своё окно)
# ---------------------------------------------------------------------------
def run_cli(path, fmt):
    import tkinter as tk
    from tkinter import messagebox

    main = __import__("__main__")
    col = lambda n, d: getattr(main, n, d)
    BG, SF, TX, AC = (col("COL_BG", "#1b1b1d"), col("COL_SURFACE", "#232427"),
                      col("COL_TEXT", "#e6e8ea"), col("COL_ACCENT", "#4c8bf5"))
    root = tk.Tk()
    root.title(C("title"))
    root.configure(bg=SF)
    root.resizable(False, False)
    root.attributes("-topmost", True)
    try:
        ico = os.path.join(os.path.dirname(main._app_exe()), "HDContainer.ico")
        if os.path.exists(ico):
            root.iconbitmap(ico)
    except Exception:
        pass
    w, h = 460, 150
    root.geometry("%dx%d+%d+%d" % (w, h, (root.winfo_screenwidth() - w) // 2,
                                   (root.winfo_screenheight() - h) // 2))
    name = os.path.basename(path)
    status = tk.Label(root, text=C("working", name, fmt.upper()), bg=SF, fg=TX,
                      font=("Segoe UI", 10), wraplength=420, justify="left")
    status.pack(anchor="w", padx=20, pady=(22, 10))
    track = tk.Canvas(root, height=8, bg=BG, highlightthickness=0, bd=0)
    track.pack(fill="x", padx=20)
    bar = track.create_rectangle(0, 0, 0, 8, fill=AC, outline="")
    st = {"done": False, "cancel": False, "frac": None}

    def tick(pos=0.0):
        if st["done"]:
            return
        tw = max(1, track.winfo_width())
        if st["frac"] is None:                    # неизвестен прогресс — бегущая полоса
            seg, x = 0.3, (pos % 1.3) - 0.3
            track.coords(bar, int(tw * max(0.0, x)), 0, int(tw * min(1.0, x + seg)), 8)
        else:
            track.coords(bar, 0, 0, int(tw * st["frac"]), 8)
        root.after(40, lambda: tick(pos + 0.03))

    btn = tk.Label(root, text=C("cancel"), bg=col("COL_SURFACE2", "#2d2e31"), fg=TX,
                   font=("Segoe UI", 10), padx=14, pady=6, cursor="hand2")
    btn.pack(side="right", padx=20, pady=12)
    btn.bind("<Button-1>", lambda e: (st.__setitem__("cancel", True), root.destroy()))

    def set_status(text, frac=None):
        st["frac"] = frac
        root.after(0, lambda: status.winfo_exists() and status.configure(text=text))

    def need_tool(tname):
        repo, _tag, _asset, _exe, _rule, mb = TOOLS[tname]
        res = {"v": False}
        ev = threading.Event()

        def ask():
            res["v"] = messagebox.askyesno(C("title"), C("need_tool", tname, mb, repo), parent=root)
            ev.set()
        root.after(0, ask)
        ev.wait()
        return res["v"]

    def work():
        try:
            src_ext = os.path.splitext(path)[1].lstrip(".").lower()
            tools, browser = tools_needed(src_ext, fmt)
            if browser and not find_browser():
                raise ConvertError(C("pdf_nobrowser"))
            for tname in tools:
                if tool_installed(tname):
                    continue
                if not need_tool(tname):
                    root.after(0, root.destroy)
                    return
                try:
                    install_tool(tname, lambda dn, tt, n=tname: set_status(
                        C("downloading", n, int(dn * 100 / tt) if tt else 0),
                        (dn / tt) if tt else None), lambda: st["cancel"])
                except Exception as ex:
                    if st["cancel"]:
                        return
                    raise ConvertError(C("tool_fail", tname, str(ex)[:200]))
                set_status(C("working", name, fmt.upper()), None)
            outs = convert(path, fmt)
            LOG("convert ok: %s -> %s" % (path, outs[0]))
            st["done"] = True
            msg = (C("done", os.path.basename(outs[0])) if len(outs) == 1
                   else C("done_pages", len(outs), os.path.basename(outs[0])))
            root.after(0, lambda: (status.configure(text=msg),
                                   track.coords(bar, 0, 0, max(1, track.winfo_width()), 8),
                                   btn.configure(text=C("close")),
                                   root.after(1500, root.destroy)))
        except Exception as ex:
            LOG("convert failed: %s -> %s: %s\n%s" % (path, fmt, ex, traceback.format_exc()))
            st["done"] = True
            msg = str(ex)[:400] if isinstance(ex, ConvertError) else repr(ex)[:400]
            root.after(0, lambda: (status.configure(text=C("failed") + "\n" + msg, fg=col("COL_DANGER", "#ff453a")),
                                   track.coords(bar, 0, 0, 0, 8), btn.configure(text=C("close"))))

    threading.Thread(target=work, daemon=True).start()
    root.after(50, tick)
    root.mainloop()
