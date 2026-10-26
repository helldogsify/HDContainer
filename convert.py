"""
Конвертер файлов из контекстного меню Проводника.

ПКМ по файлу -> «Конвертировать в» -> список форматов, в которые этот файл
можно превратить. Результат ложится рядом с исходным файлом: то же имя,
другое расширение (если такой уже есть — «имя (2).расш»).

Движки:
  * изображения — Pillow (встроен в приложение);
  * аудио/видео  — FFmpeg (BtbN/FFmpeg-Builds), скачивается при первом
                   использовании (~110 МБ);
  * документы    — Pandoc (jgm/pandoc), скачивается при первом использовании
                   (~40 МБ);
  * csv / tsv / json — своими силами.
Скачанное сверяется с SHA-256 из API релиза GitHub, как и обновления
самого приложения.

Меню — статические ключи реестра текущего пользователя
(HKCU\\Software\\Classes\\SystemFileAssociations\\.<расш>\\shell\\HDContainer.Convert):
прав администратора не нужно; в Windows 11 пункт живёт в «Показать
дополнительные параметры» (Shift+ПКМ открывает это меню сразу).
"""

import csv
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import traceback
import urllib.request
import winreg
import zipfile

CREATE_NO_WINDOW = 0x08000000
LANG_GET = lambda: "en"
LOG = lambda m: None

CSTR = {
    "menu": {"en": "Convert to", "ru": "Конвертировать в", "es": "Convertir a", "pt": "Converter para", "de": "Konvertieren in", "fr": "Convertir en", "zh": "转换为"},
    "setting": {"en": "\"Convert to\" in the Explorer right-click menu (images, audio, video, documents)", "ru": "«Конвертировать в» в контекстном меню Проводника (картинки, аудио, видео, документы)", "es": "\"Convertir a\" en el menú contextual del Explorador (imágenes, audio, vídeo, documentos)", "pt": "\"Converter para\" no menu de contexto do Explorador (imagens, áudio, vídeo, documentos)", "de": "„Konvertieren in“ im Kontextmenü des Explorers (Bilder, Audio, Video, Dokumente)", "fr": "« Convertir en » dans le menu contextuel de l'Explorateur (images, audio, vidéo, documents)", "zh": "资源管理器右键菜单中的“转换为”（图片、音频、视频、文档）"},
    "setting_note": {"en": "On Windows 11 it is under \"Show more options\" (or Shift + right-click). FFmpeg and Pandoc are downloaded on first use.", "ru": "В Windows 11 пункт — в «Показать дополнительные параметры» (или Shift + правый клик). FFmpeg и Pandoc скачиваются при первом использовании.", "es": "En Windows 11 está en \"Mostrar más opciones\" (o Shift + clic derecho). FFmpeg y Pandoc se descargan al primer uso.", "pt": "No Windows 11 fica em \"Mostrar mais opções\" (ou Shift + clique direito). FFmpeg e Pandoc são baixados no primeiro uso.", "de": "Unter Windows 11 unter „Weitere Optionen anzeigen“ (oder Umschalt + Rechtsklick). FFmpeg und Pandoc werden bei der ersten Nutzung geladen.", "fr": "Sous Windows 11, dans « Afficher plus d'options » (ou Maj + clic droit). FFmpeg et Pandoc sont téléchargés à la première utilisation.", "zh": "在 Windows 11 中位于“显示更多选项”（或 Shift + 右键）。FFmpeg 和 Pandoc 在首次使用时下载。"},
    "title": {"en": "Convert", "ru": "Конвертация", "es": "Convertir", "pt": "Converter", "de": "Konvertieren", "fr": "Conversion", "zh": "转换"},
    "working": {"en": "Converting %s → %s…", "ru": "Конвертирую %s → %s…", "es": "Convirtiendo %s → %s…", "pt": "Convertendo %s → %s…", "de": "Konvertiere %s → %s…", "fr": "Conversion %s → %s…", "zh": "正在转换 %s → %s…"},
    "done": {"en": "Done: %s", "ru": "Готово: %s", "es": "Listo: %s", "pt": "Pronto: %s", "de": "Fertig: %s", "fr": "Terminé : %s", "zh": "完成：%s"},
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
IMAGE_SRC = {"png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff", "ico"}
IMAGE_DST = ["png", "jpg", "webp", "bmp", "gif", "ico", "tiff", "pdf"]
AUDIO_SRC = {"mp3", "wav", "ogg", "oga", "flac", "m4a", "aac", "wma", "opus", "aiff", "aif"}
AUDIO_DST = ["mp3", "wav", "ogg", "flac", "m4a", "opus"]
VIDEO_SRC = {"mp4", "mkv", "mov", "avi", "webm", "wmv", "m4v", "ts", "flv", "mpg", "mpeg", "3gp"}
VIDEO_DST = ["mp4", "mkv", "webm", "mov", "avi", "gif"]
DOC_SRC = {"md", "markdown", "docx", "html", "htm", "txt", "rtf", "odt", "epub", "tex", "rst"}
DOC_DST = ["docx", "md", "html", "txt", "rtf", "odt", "epub"]
DATA_SRC = {"csv", "tsv", "json"}
DATA_DST = ["csv", "tsv", "json"]

PANDOC_FROM = {"md": "markdown", "markdown": "markdown", "txt": "markdown", "html": "html",
               "htm": "html", "docx": "docx", "rtf": "rtf", "odt": "odt", "epub": "epub",
               "tex": "latex", "rst": "rst"}
PANDOC_TO = {"md": "gfm", "html": "html", "txt": "plain", "docx": "docx", "rtf": "rtf",
             "odt": "odt", "epub": "epub"}


def targets_for(ext):
    """Список целевых расширений для исходного (без точки, нижний регистр)."""
    ext = ext.lower()
    out = []
    if ext in IMAGE_SRC:
        out += [t for t in IMAGE_DST if t != ext and not (ext == "jpeg" and t == "jpg")
                and not (ext == "tif" and t == "tiff")]
    if ext in AUDIO_SRC:
        out += [t for t in AUDIO_DST if t != ext]
    if ext in VIDEO_SRC:
        out += [t for t in VIDEO_DST if t != ext] + ["mp3", "wav", "m4a"]   # звуковая дорожка
    if ext in DOC_SRC:
        out += [t for t in DOC_DST if t != ext and not (ext == "markdown" and t == "md")
                and not (ext == "htm" and t == "html")]
    if ext in DATA_SRC:
        out += [t for t in DATA_DST if t != ext]
    return out


def all_source_exts():
    return sorted(IMAGE_SRC | AUDIO_SRC | VIDEO_SRC | DOC_SRC | DATA_SRC)


# ---------------------------------------------------------------------------
#  Внешние инструменты: скачивание с проверкой SHA-256 из API релиза
# ---------------------------------------------------------------------------
TOOLS = {
    # имя: (репозиторий, тег, файл архива, [exe внутри], ~МБ)
    "ffmpeg": ("BtbN/FFmpeg-Builds", "latest", "ffmpeg-master-latest-win64-gpl.zip",
               ["ffmpeg.exe"], 190),
    "pandoc": ("jgm/pandoc", "3.12.1", "pandoc-3.12.1-windows-x86_64.zip", ["pandoc.exe"], 40),
}


def tools_dir():
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return os.path.join(base, "HDContainer", "tools")


def tool_exe(name):
    return os.path.join(tools_dir(), name, TOOLS[name][3][0])


def tool_installed(name):
    return os.path.isfile(tool_exe(name))


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
    repo, tag, asset, exes, _mb = TOOLS[name]
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
        with zipfile.ZipFile(tmp) as z:
            for info in z.infolist():
                base = os.path.basename(info.filename)
                # распаковываем только нужные exe по ИМЕНИ, в свою папку:
                # пути из архива не используем (zip-slip)
                if base in exes and not info.is_dir():
                    with z.open(info) as src, open(os.path.join(dst_dir, base), "wb") as out:
                        while True:
                            b = src.read(1024 * 1024)
                            if not b:
                                break
                            out.write(b)
        if not tool_installed(name):
            raise RuntimeError("%s not found in archive" % exes[0])
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


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


def _run(args, timeout=3600):
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
    try:                                       # ребёнок умирает вместе с нами (окно закрыли)
        import voice_sys
        voice_sys.adopt_process(p)
    except Exception:
        pass
    out, err = p.communicate(timeout=timeout)
    if p.returncode != 0:
        lines = (err or out or b"").decode("utf-8", "replace").strip().splitlines()
        raise ConvertError(lines[-1][:300] if lines else "exit code %d" % p.returncode)


def convert_image(src, dst, fmt):
    try:
        from PIL import Image
    except Exception:
        raise ConvertError(C("no_pil"))
    im = Image.open(src)
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
    elif fmt == "ico":
        im.thumbnail((256, 256))
        kw = {"sizes": [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]}
    elif fmt == "pdf":
        kw = {"resolution": 100.0}
    im.save(dst, format={"jpg": "JPEG", "tiff": "TIFF"}.get(fmt, fmt.upper()), **kw)


def convert_ffmpeg(src, dst, fmt, src_ext):
    exe = tool_exe("ffmpeg")
    base = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", src]
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


def convert_pandoc(src, dst, fmt, src_ext):
    exe = tool_exe("pandoc")
    args = [exe, src, "-f", PANDOC_FROM[src_ext], "-t", PANDOC_TO[fmt], "-s", "-o", dst]
    if fmt == "txt":
        args += ["--wrap=none"]
    _run(args, timeout=600)


def _read_table(src, ext):
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


def convert_data(src, dst, fmt, src_ext):
    cols, rows = _read_table(src, src_ext)
    if fmt == "json":
        out = [dict(zip(cols, r + [""] * (len(cols) - len(r)))) for r in rows]
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return
    with open(dst, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, dialect="excel-tab" if fmt == "tsv" else "excel")
        w.writerow(cols)
        w.writerows(rows)


def engine_for(src_ext, fmt):
    """Какой движок нужен: 'image' / 'ffmpeg' / 'pandoc' / 'data'."""
    if src_ext in IMAGE_SRC and fmt in IMAGE_DST:
        return "image"
    if src_ext in DATA_SRC and fmt in DATA_DST:
        return "data"
    if src_ext in DOC_SRC and fmt in DOC_DST:
        return "pandoc"
    if (src_ext in AUDIO_SRC or src_ext in VIDEO_SRC) and (fmt in AUDIO_DST or fmt in VIDEO_DST):
        return "ffmpeg"
    return None


def convert(src, fmt):
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
        elif engine == "ffmpeg":
            convert_ffmpeg(src, dst, fmt, src_ext)
        elif engine == "pandoc":
            convert_pandoc(src, dst, fmt, src_ext)
        else:
            convert_data(src, dst, fmt, src_ext)
    except Exception:
        try:
            if os.path.exists(dst):
                os.remove(dst)              # не оставлять обрезок
        except Exception:
            pass
        raise
    return dst


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
    """Зарегистрировано ли меню ИМЕННО для этого exe и на текущем языке (после
    обновления в другую папку или смены языка ключи надо перезаписать)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _assoc_key("png")) as k:
            verb, _t = winreg.QueryValueEx(k, "MUIVerb")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            _assoc_key("png") + r"\shell\00_jpg\command") as k:
            cmd, _t = winreg.QueryValueEx(k, "")
        return verb == C("menu") and cmd.lower().startswith('"%s"' % exe.lower())
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

    def need_tool(name):
        repo, _tag, _asset, _exes, mb = TOOLS[name]
        res = {"v": False}
        ev = threading.Event()

        def ask():
            res["v"] = messagebox.askyesno(C("title"), C("need_tool", name, mb, repo), parent=root)
            ev.set()
        root.after(0, ask)
        ev.wait()
        return res["v"]

    def work():
        try:
            src_ext = os.path.splitext(path)[1].lstrip(".").lower()
            engine = engine_for(src_ext, fmt)
            if engine in ("ffmpeg", "pandoc") and not tool_installed(engine):
                if not need_tool(engine):
                    root.after(0, root.destroy)
                    return
                try:
                    install_tool(engine, lambda d, t: set_status(
                        C("downloading", engine, int(d * 100 / t) if t else 0),
                        (d / t) if t else None), lambda: st["cancel"])
                except Exception as ex:
                    if st["cancel"]:
                        return
                    raise ConvertError(C("tool_fail", engine, str(ex)[:200]))
                set_status(C("working", name, fmt.upper()), None)
            out = convert(path, fmt)
            LOG("convert ok: %s -> %s" % (path, out))
            st["done"] = True
            root.after(0, lambda: (status.configure(text=C("done", os.path.basename(out))),
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
