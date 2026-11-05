# -*- coding: utf-8 -*-
"""Minimal torrent downloader for HDContainer.

A .torrent opened with HDContainer starts downloading at once. Progress, pause
and delete live in the tray menu; Settings holds the save folder and presets.
libtorrent runs its own network threads; we only call it from tk timers, never
from native callbacks.
"""
import os
import json
import time
import ctypes
import threading
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog

import libtorrent as lt

TSTR = {
    "tab": {"en": "Torrents", "ru": "Торренты", "es": "Torrents", "pt": "Torrents", "de": "Torrents", "fr": "Torrents", "zh": "种子下载"},
    "folder": {"en": "Save to", "ru": "Папка сохранения", "es": "Guardar en", "pt": "Salvar em", "de": "Speichern in", "fr": "Enregistrer dans", "zh": "保存到"},
    "change": {"en": "Change…", "ru": "Изменить…", "es": "Cambiar…", "pt": "Alterar…", "de": "Ändern…", "fr": "Modifier…", "zh": "更改…"},
    "presets": {"en": "Presets", "ru": "Предустановки", "es": "Ajustes", "pt": "Predefinições", "de": "Voreinstellungen", "fr": "Préréglages", "zh": "预设"},
    "max_speed": {"en": "Maximum speed, no limits", "ru": "Максимальная скорость без ограничений", "es": "Velocidad máxima, sin límites", "pt": "Velocidade máxima, sem limites", "de": "Maximale Geschwindigkeit, keine Limits", "fr": "Vitesse maximale, sans limite", "zh": "最高速度，不限速"},
    "force_seeds": {"en": "Force-request seeds", "ru": "Принудительно запрашивать сидов", "es": "Forzar la búsqueda de seeds", "pt": "Forçar a busca de seeds", "de": "Seeds aktiv anfordern", "fr": "Forcer la recherche de seeds", "zh": "强制请求做种者"},
    "merge": {"en": "Merge seeds from all sources", "ru": "Склеивать сидов из разных источников", "es": "Unir seeds de todas las fuentes", "pt": "Juntar seeds de todas as fontes", "de": "Seeds aus allen Quellen bündeln", "fr": "Regrouper les seeds de toutes les sources", "zh": "合并所有来源的做种者"},
    "auto_stop": {"en": "Stop seeding when done", "ru": "Уходить с раздачи после загрузки", "es": "Dejar de compartir al terminar", "pt": "Parar de semear ao concluir", "de": "Nach dem Download nicht mehr seeden", "fr": "Arrêter le partage une fois terminé", "zh": "下载完成后停止做种"},
    "auto_clear": {"en": "Remove finished from the list", "ru": "Убирать загруженное из списка", "es": "Quitar los terminados de la lista", "pt": "Remover concluídos da lista", "de": "Fertige aus der Liste entfernen", "fr": "Retirer les terminés de la liste", "zh": "从列表移除已完成项"},
    "paused": {"en": "Paused", "ru": "Пауза", "es": "En pausa", "pt": "Pausado", "de": "Pausiert", "fr": "En pause", "zh": "已暂停"},
    "checking": {"en": "Checking…", "ru": "Проверка…", "es": "Comprobando…", "pt": "Verificando…", "de": "Prüfung…", "fr": "Vérification…", "zh": "正在校验…"},
    "seeking": {"en": "Looking for seeds…", "ru": "Ищу сидов…", "es": "Buscando seeds…", "pt": "Procurando seeds…", "de": "Suche Seeds…", "fr": "Recherche de seeds…", "zh": "正在寻找做种者…"},
    "done": {"en": "Done", "ru": "Готово", "es": "Listo", "pt": "Concluído", "de": "Fertig", "fr": "Terminé", "zh": "已完成"},
    "seeding": {"en": "Done · seeding", "ru": "Готово · раздаётся", "es": "Listo · compartiendo", "pt": "Concluído · semeando", "de": "Fertig · wird geseedet", "fr": "Terminé · en partage", "zh": "已完成 · 做种中"},
    "error": {"en": "Error", "ru": "Ошибка", "es": "Error", "pt": "Erro", "de": "Fehler", "fr": "Erreur", "zh": "错误"},
    "delete_title": {"en": "Delete download", "ru": "Удаление", "es": "Eliminar descarga", "pt": "Excluir download", "de": "Download löschen", "fr": "Supprimer le téléchargement", "zh": "删除下载"},
    "delete_q": {"en": "Delete “%s” and the files downloaded so far?", "ru": "Точно удалить «%s» вместе с уже скачанными файлами?", "es": "¿Eliminar «%s» y los archivos ya descargados?", "pt": "Excluir “%s” e os arquivos já baixados?", "de": "„%s“ und die bereits geladenen Dateien löschen?", "fr": "Supprimer « %s » et les fichiers déjà téléchargés ?", "zh": "删除“%s”及已下载的文件？"},
    "finished": {"en": "Downloaded", "ru": "Скачано", "es": "Descargado", "pt": "Baixado", "de": "Heruntergeladen", "fr": "Téléchargé", "zh": "下载完成"},
    "started": {"en": "Download started", "ru": "Скачивание началось", "es": "Descarga iniciada", "pt": "Download iniciado", "de": "Download gestartet", "fr": "Téléchargement lancé", "zh": "开始下载"},
    "already": {"en": "Already downloading", "ru": "Уже скачивается", "es": "Ya se está descargando", "pt": "Já está baixando", "de": "Wird bereits geladen", "fr": "Déjà en cours", "zh": "已在下载中"},
    "bad_file": {"en": "Can't open the torrent file", "ru": "Не удалось открыть торрент-файл", "es": "No se puede abrir el archivo torrent", "pt": "Não foi possível abrir o arquivo torrent", "de": "Torrent-Datei kann nicht geöffnet werden", "fr": "Impossible d’ouvrir le fichier torrent", "zh": "无法打开种子文件"},
    "mbs": {"en": "MB/s", "ru": "МБ/с", "es": "MB/s", "pt": "MB/s", "de": "MB/s", "fr": "Mo/s", "zh": "MB/s"},
    "kbs": {"en": "KB/s", "ru": "КБ/с", "es": "KB/s", "pt": "KB/s", "de": "KB/s", "fr": "Ko/s", "zh": "KB/s"},
    "mb": {"en": "MB", "ru": "МБ", "es": "MB", "pt": "MB", "de": "MB", "fr": "Mo", "zh": "MB"},
    "gb": {"en": "GB", "ru": "ГБ", "es": "GB", "pt": "GB", "de": "GB", "fr": "Go", "zh": "GB"},
    "hist_menu": {"en": "Download history", "ru": "История загрузок", "es": "Historial de descargas", "pt": "Histórico de downloads", "de": "Download-Verlauf", "fr": "Historique des téléchargements", "zh": "下载历史"},
    "hist_keep": {"en": "Keep download history", "ru": "Сохранять историю загрузок", "es": "Guardar el historial de descargas", "pt": "Manter histórico de downloads", "de": "Download-Verlauf speichern", "fr": "Conserver l'historique des téléchargements", "zh": "保存下载历史"},
    "hist_open": {"en": "Open history", "ru": "Открыть историю", "es": "Abrir historial", "pt": "Abrir histórico", "de": "Verlauf öffnen", "fr": "Ouvrir l'historique", "zh": "打开历史"},
    "hist_clear": {"en": "Clear history", "ru": "Очистить историю", "es": "Borrar historial", "pt": "Limpar histórico", "de": "Verlauf löschen", "fr": "Effacer l'historique", "zh": "清空历史"},
    "hist_clear_q": {"en": "Clear the download history? The downloaded files stay.", "ru": "Очистить историю загрузок? Скачанные файлы останутся.", "es": "¿Borrar el historial de descargas? Los archivos descargados se conservan.", "pt": "Limpar o histórico de downloads? Os arquivos baixados permanecem.", "de": "Download-Verlauf löschen? Die geladenen Dateien bleiben erhalten.", "fr": "Effacer l'historique des téléchargements ? Les fichiers téléchargés sont conservés.", "zh": "清空下载历史？已下载的文件会保留。"},
    "hist_search": {"en": "Search…", "ru": "Поиск…", "es": "Buscar…", "pt": "Pesquisar…", "de": "Suchen…", "fr": "Rechercher…", "zh": "搜索…"},
    "hist_empty": {"en": "No finished downloads yet.", "ru": "Завершённых загрузок пока нет.", "es": "Aún no hay descargas terminadas.", "pt": "Ainda não há downloads concluídos.", "de": "Noch keine fertigen Downloads.", "fr": "Aucun téléchargement terminé pour l'instant.", "zh": "还没有已完成的下载。"},
    "hist_nomatch": {"en": "Nothing matches.", "ru": "Ничего не найдено.", "es": "Sin coincidencias.", "pt": "Nada encontrado.", "de": "Keine Treffer.", "fr": "Aucun résultat.", "zh": "没有匹配项。"},
    "hist_gone": {"en": "Files not found", "ru": "Файлы не найдены", "es": "Archivos no encontrados", "pt": "Arquivos não encontrados", "de": "Dateien nicht gefunden", "fr": "Fichiers introuvables", "zh": "找不到文件"},
}

LANG_GET = lambda: "en"
DECIMAL_COMMA = ("ru", "es", "pt", "de", "fr")


def S(key, *args):
    s = TSTR.get(key, {})
    txt = s.get(LANG_GET()) or s.get("en") or key
    return (txt % args) if args else txt


DEFAULTS = {"dir": "", "max_speed": True, "force_seeds": True, "merge": True,
            "auto_stop": True, "auto_clear": True, "history": True}

LISTEN = "0.0.0.0:6881,[::]:6881"
FORCE_EVERY = 180        # s between forced announces while seeds are scarce
FORCE_MIN_SEEDS = 5      # fewer connected seeds than this -> keep forcing
RESUME_EVERY = 30        # s between resume-data snapshots of active downloads
LOCK = "running.lock"    # exists while the app runs; left behind = the last exit was not clean
MARKER = "format2"       # state written by a version with LOCK; older state may hold a stale snapshot
TICK_MS = 1000
HISTORY_MAX = 200        # finished downloads kept in the history, as many as dictations

# "Maximum speed" lifts libtorrent's conservative connection defaults; rate limits
# are always off (0 = unlimited) since the app never offers to set them.
FAST = {"connections_limit": 1000, "connection_speed": 100,
        "max_peerlist_size": 8000, "max_out_request_queue": 1500}
NORMAL = {"connections_limit": 200, "connection_speed": 30,
          "max_peerlist_size": 3000, "max_out_request_queue": 500}

GLYPH_PAUSE, GLYPH_PLAY, GLYPH_DELETE, GLYPH_DISMISS = "", "", "", ""


def downloads_dir():
    """The user's Downloads folder (respects a relocated Downloads)."""
    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort),
                    ("d", ctypes.c_ubyte * 8)]
    try:
        g = GUID(0x374DE290, 0x123F, 0x4565,
                 (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
            path = p.value
            ctypes.windll.ole32.CoTaskMemFree(p)
            if path:
                return path
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Downloads")


def _tid_of(hashes):
    return str(hashes.get_best())


def _fmt_num(x):
    s = "%.1f" % x
    return s.replace(".", ",") if LANG_GET() in DECIMAL_COMMA else s


def _fmt_rate(bps):
    if bps >= 1024 * 1024:
        return "%s %s" % (_fmt_num(bps / 1048576.0), S("mbs"))
    return "%d %s" % (bps // 1024, S("kbs"))


def _fmt_size(n):
    if n >= 1 << 30:
        return "%s %s" % (_fmt_num(n / float(1 << 30)), S("gb"))
    return "%s %s" % (_fmt_num(n / float(1 << 20)), S("mb"))


def _fmt_eta(sec):
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def _elide(text, font, px):
    f = tkfont.Font(font=font)
    if f.measure(text) <= px:
        return text
    while text and f.measure(text + "…") > px:
        text = text[:-1]
    return text.rstrip() + "…"


def _elide_mid(text, font, px):
    f = tkfont.Font(font=font)
    if f.measure(text) <= px:
        return text
    a, b = len(text) // 2, len(text) // 2
    while a > 0 and f.measure(text[:a] + "…" + text[b:]) > px:
        a -= 1
        b += 1
    return text[:a] + "…" + text[b:]


class TorrentController:
    def __init__(self, app, get_lang, log, local_dir):
        global LANG_GET
        LANG_GET = get_lang
        self.app = app
        self.root = app.root
        self.log = log
        self.dir = os.path.join(local_dir, "torrents")
        os.makedirs(self.dir, exist_ok=True)
        self.ses = None
        self.items = {}        # tid -> {"name","path","h","done","paused","err","t_force","t_resume"}
        self.order = []
        self.draft = None
        self._popup = None     # {"parent", "sig", "rows"} while the tray menu is open
        self.history_file = os.path.join(self.dir, "history.json")
        self.history = self._history_load()     # [{"id","t","name","path","size"}], oldest first
        self.hist_win = None
        self._lock = os.path.join(self.dir, LOCK)
        marker = os.path.join(self.dir, MARKER)
        # versions before 1.8.2 saved snapshots only every 5 min and not at Windows shutdown,
        # so their state is checked against the files once
        self._unclean = os.path.exists(self._lock) or not os.path.exists(marker)
        self._restore()
        try:
            open(marker, "w").close()
        except OSError:
            pass
        self._set_lock(True)
        self.root.after(TICK_MS, self._tick)

    # ---- settings ----
    def get(self, k):
        src = self.draft if self.draft is not None else self.app.settings.get("torrent", {})
        return src.get(k, DEFAULTS[k])

    def folder(self):
        d = self.get("dir")
        return d if d and os.path.isdir(d) else downloads_dir()

    def begin_edit(self):
        self.draft = dict(self.app.settings.get("torrent", {}))

    def discard(self):
        self.draft = None

    def commit(self):
        new, self.draft = self.draft, None
        if new is None:
            return
        self.app._set_setting("torrent", new)
        if self.ses is not None:
            self.ses.apply_settings(self._tuning())

    def _tuning(self):
        p = {"download_rate_limit": 0, "upload_rate_limit": 0,
             "announce_to_all_trackers": bool(self.get("merge")),
             "announce_to_all_tiers": bool(self.get("merge"))}
        p.update(FAST if self.get("max_speed") else NORMAL)
        return p

    # ---- session ----
    def _session(self):
        if self.ses is None:
            p = {"listen_interfaces": LISTEN,
                 "alert_mask": lt.alert_category.error | lt.alert_category.status | lt.alert_category.storage,
                 "enable_dht": True, "enable_lsd": True, "enable_upnp": True, "enable_natpmp": True}
            p.update(self._tuning())
            self.ses = lt.session(p)
            self.log("torrent session started")
        return self.ses

    def _maybe_close_session(self):
        """Close the session once nothing is in it: no open ports or DHT traffic at idle."""
        if self.ses is None or any(it["h"] is not None for it in self.items.values()):
            return
        box, self.ses = [self.ses], None

        def bye():
            s = box.pop()
            s.pause()
            del s                      # the blocking shutdown runs here, off the UI thread
        threading.Thread(target=bye, daemon=True).start()
        self.log("torrent session closed")

    # ---- persistence: list.json + <tid>.torrent + <tid>.resume ----
    def _p(self, tid, ext):
        return os.path.join(self.dir, tid + ext)

    def _save_list(self):
        rows = [{"id": tid, "name": it["name"], "path": it["path"], "done": it["done"],
                 "paused": it["paused"], "active": it["h"] is not None}
                for tid, it in ((t, self.items[t]) for t in self.order)]
        tmp = os.path.join(self.dir, "list.json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False)
            os.replace(tmp, os.path.join(self.dir, "list.json"))
        except Exception as ex:
            self.log("torrent list save failed: %r" % ex)

    def _set_lock(self, on):
        try:
            if on:
                open(self._lock, "w").close()
            else:
                os.remove(self._lock)
        except OSError:
            pass

    def _write_resume(self, a):
        """Store a save_resume_data_alert atomically: a torn file would lose all progress."""
        try:
            tid = _tid_of(a.handle.info_hashes())
            if tid in self.items:
                tmp = self._p(tid, ".resume.tmp")
                with open(tmp, "wb") as f:
                    f.write(lt.write_resume_data_buf(a.params))
                os.replace(tmp, self._p(tid, ".resume"))
        except Exception as ex:
            self.log("resume write failed: %r" % ex)

    def _drop_state(self, tid):
        for ext in (".torrent", ".resume"):
            try:
                os.remove(self._p(tid, ext))
            except OSError:
                pass

    def _restore(self):
        try:
            with open(os.path.join(self.dir, "list.json"), encoding="utf-8") as f:
                rows = json.load(f)
        except Exception:
            return
        for r in rows:
            tid = r.get("id")
            if not tid:
                continue
            if not r.get("active"):
                if r.get("done"):          # finished, kept only as a list entry
                    self.items[tid] = self._new_item(r.get("name", tid), r.get("path", ""), None)
                    self.items[tid]["done"] = True
                    self.order.append(tid)
                continue
            try:
                ti = lt.torrent_info(self._p(tid, ".torrent"))
            except Exception as ex:
                self.log("torrent restore failed %s: %r" % (tid, ex))
                continue
            resume = None
            if self._unclean and not r.get("done"):
                # killed, crashed or power lost: the snapshot can be older than the files
                # (pieces fetched after it would be downloaded again), so without resume
                # data libtorrent checks what is really on disk
                self.log("torrent %s: last exit was not clean, checking files" % r.get("name"))
            else:
                try:
                    with open(self._p(tid, ".resume"), "rb") as f:
                        resume = f.read()
                except OSError:
                    pass
            self._add(ti, r.get("path") or self.folder(), resume=resume,
                      paused=bool(r.get("paused")), done=bool(r.get("done")))
        self._save_list()

    # ---- adding ----
    @staticmethod
    def _new_item(name, path, h):
        return {"name": name, "path": path, "h": h, "done": False, "paused": False, "err": "",
                "t_force": 0.0, "t_resume": time.time()}

    def add_file(self, path):
        """Entry point: a .torrent opened with HDContainer."""
        try:
            ti = lt.torrent_info(path)
        except Exception as ex:
            self.log("bad torrent %r: %r" % (path, ex))
            self.app._notify(S("bad_file"), os.path.basename(path), kind="error")
            return
        tid = _tid_of(ti.info_hashes())
        it = self.items.get(tid)
        if it is not None and it["h"] is not None:
            self._merge(it, ti)
            self._notice_opened(S("finished") if it["done"] else S("already"), it)
            return
        if it is not None:                 # finished entry kept in the list: start over
            self.items.pop(tid)
            self.order.remove(tid)
        try:
            with open(path, "rb") as src, open(self._p(tid, ".torrent"), "wb") as dst:
                dst.write(src.read())
        except Exception as ex:
            self.log("torrent copy failed: %r" % ex)
        it = self._add(ti, self.folder())
        self._save_list()
        self._repopup()
        self._notice_opened(S("started"), it)

    def _notice_opened(self, title, it):
        """A .torrent was opened: the user sees at once that it was taken."""
        self.app._notify(title, it["name"], kind="started",
                         on_click=lambda p=it["path"], n=it["name"]: open_folder(p, n))

    def _merge(self, it, ti):
        """Same torrent from another place: add its trackers and web seeds to the running one."""
        if not self.get("merge"):
            return
        h = it["h"]
        known = {t.get("url") for t in h.trackers()}
        added = 0
        for t in ti.trackers():
            if t.url not in known:
                h.add_tracker({"url": t.url, "tier": t.tier})
                added += 1
        seeds = set(h.url_seeds())
        for ws in ti.web_seeds():
            url = ws.get("url") if isinstance(ws, dict) else getattr(ws, "url", None)
            if url and url not in seeds:
                h.add_url_seed(url)
                added += 1
        self.log("torrent merge %s: +%d sources" % (it["name"], added))
        if added:
            self._force(it)

    def _add(self, ti, save_path, resume=None, paused=False, done=False):
        tid = _tid_of(ti.info_hashes())
        atp = None
        if resume:
            try:
                atp = lt.read_resume_data(resume)
            except Exception as ex:
                self.log("resume data unreadable %s: %r" % (tid, ex))
        if atp is None:
            atp = lt.add_torrent_params()
        atp.ti = ti
        atp.save_path = save_path
        # not auto-managed: no queue, every download runs at once, pause stays a pause
        flags = atp.flags & ~lt.torrent_flags.auto_managed
        flags = (flags | lt.torrent_flags.paused) if paused else (flags & ~lt.torrent_flags.paused)
        atp.flags = flags
        h = self._session().add_torrent(atp)
        it = self._new_item(ti.name(), save_path, h)
        it["paused"], it["done"] = paused, done
        self.items[tid] = it
        self.order.append(tid)
        if not paused and not done:
            self._force(it)
        return it

    # ---- actions ----
    def toggle_pause(self, tid):
        it = self.items.get(tid)
        if not it or it["h"] is None:
            return
        h = it["h"]
        if it["paused"] or it["err"]:
            h.clear_error()
            h.resume()
            it["paused"], it["err"] = False, ""
            self._force(it)
        else:
            h.pause()
            it["paused"] = True
            h.save_resume_data(lt.torrent_handle.flush_disk_cache)
        self._save_list()

    def remove(self, tid, delete_files):
        it = self.items.pop(tid, None)
        if it is None:
            return
        self.order.remove(tid)
        h = it["h"]
        if h is not None and self.ses is not None and h.is_valid():
            self.ses.remove_torrent(h, lt.options_t.delete_files if delete_files else 0)
        self._drop_state(tid)
        self._save_list()
        self._maybe_close_session()

    def _force(self, it):
        """Ask every tracker and the DHT for peers right now, ignoring the tracker's min interval."""
        if not self.get("force_seeds") or it["h"] is None:
            return
        h = it["h"]
        try:
            h.force_reannounce(0, -1, lt.reannounce_flags_t.ignore_min_interval)
        except Exception:
            h.force_reannounce()
        if not ti_private(h):
            h.force_dht_announce()
        it["t_force"] = time.time()

    # ---- timer ----
    def _tick(self):
        try:
            if self.ses is not None:
                self._alerts()
                now = time.time()
                for it in list(self.items.values()):
                    h = it["h"]
                    if h is None or it["done"] or it["paused"]:
                        continue
                    if now - it["t_force"] >= FORCE_EVERY and self.get("force_seeds"):
                        if h.status().num_seeds < FORCE_MIN_SEEDS:
                            self._force(it)
                    if now - it["t_resume"] >= RESUME_EVERY:
                        h.save_resume_data(lt.torrent_handle.only_if_modified)
                        it["t_resume"] = now
        except Exception as ex:
            self.log("torrent tick failed: %r" % ex)
        self.root.after(TICK_MS, self._tick)

    def _alerts(self, quit_wait=None):
        pending = 0
        for a in self.ses.pop_alerts():
            if isinstance(a, lt.save_resume_data_alert):
                self._write_resume(a)
                pending += 1
            elif isinstance(a, lt.save_resume_data_failed_alert):
                pending += 1
            elif isinstance(a, lt.torrent_finished_alert):
                self._on_finished(a.handle)
            elif isinstance(a, (lt.torrent_error_alert, lt.file_error_alert)):
                it = self.items.get(_tid_of(a.handle.info_hashes()))
                if it is not None:
                    it["err"] = a.message()
                    self.log("torrent error %s: %s" % (it["name"], it["err"]))
        return pending

    def _on_finished(self, h):
        tid = _tid_of(h.info_hashes())
        it = self.items.get(tid)
        if it is None or it["done"]:
            return
        it["done"], it["err"] = True, ""
        self.log("torrent finished: %s" % it["name"])
        try:
            size = h.status().total_wanted
        except Exception:
            size = 0
        self._history_add(tid, it, size)
        self.app._notify(S("finished"), it["name"],
                         on_click=lambda p=it["path"], n=it["name"]: open_folder(p, n))
        if self.get("auto_stop"):
            self.ses.remove_torrent(h)          # files stay
            it["h"] = None
            self._drop_state(tid)
            if self.get("auto_clear"):
                self.items.pop(tid)
                self.order.remove(tid)
        else:
            h.save_resume_data(lt.torrent_handle.flush_disk_cache)
        self._save_list()
        self._maybe_close_session()
        self._repopup()

    def _save_all_resume(self, wait):
        """Snapshot every torrent now and wait for the files. Only resume alerts are
        handled here (no notices, no tk), so it is safe at Windows session end."""
        if self.ses is None:
            return
        n = 0
        for it in self.items.values():
            if it["h"] is not None and it["h"].is_valid():
                it["h"].save_resume_data(lt.torrent_handle.flush_disk_cache)
                n += 1
        deadline = time.time() + wait
        while n > 0 and time.time() < deadline:
            if self.ses.wait_for_alert(250) is None:
                continue
            for a in self.ses.pop_alerts():
                if isinstance(a, lt.save_resume_data_alert):
                    self._write_resume(a)
                    n -= 1
                elif isinstance(a, lt.save_resume_data_failed_alert):
                    n -= 1

    def shutdown(self):
        """Quit: snapshot resume data so a restart continues where it stopped."""
        self._save_all_resume(5)
        self._save_list()
        if self.ses is not None:
            self.ses.pause()
            self.ses = None
        self._set_lock(False)

    def end_session(self):
        """Windows is shutting down or restarting and kills us right after this:
        save progress now. Called from the window procedure, so no tk here."""
        self._save_all_resume(3)
        self._save_list()
        self._set_lock(False)
        self.log("torrents saved at session end")

    # ---- tray menu rows ----
    def has_items(self):
        return bool(self.order)

    def _status(self, it):
        """(progress 0..1, text, is_error) for one row."""
        if it["h"] is None:
            return 1.0, S("done"), False
        st = it["h"].status()
        pct = "%d%%" % int(st.progress * 100)
        if it["err"]:
            return st.progress, "%s · %s" % (S("error"), it["err"]), True
        if it["done"] or st.is_seeding:
            return 1.0, S("seeding"), False
        if it["paused"]:
            return st.progress, "%s · %s" % (pct, S("paused")), False
        S_ = lt.torrent_status.states
        if st.state in (S_.checking_files, S_.checking_resume_data):
            return st.progress, "%s %s" % (S("checking"), pct), False
        rate = st.download_payload_rate
        if rate <= 0:
            return st.progress, "%s · %s" % (pct, S("seeking")), False
        left = max(0, st.total_wanted - st.total_wanted_done)
        return st.progress, "%s · %s · %s" % (pct, _fmt_rate(rate), _fmt_eta(left / rate)), False

    def build_popup(self, parent, width, close):
        main = __import__("__main__")
        C = lambda n, d: getattr(main, n, d)
        col = {"sf": C("COL_SURFACE", "#232427"), "hv": C("COL_HOVER", "#303236"),
               "bd": C("COL_BORDER", "#2a2b2c"), "ac": C("COL_ACCENT", "#4c8bf5"),
               "tx": C("COL_TEXT", "#e6e8ea"), "dim": C("COL_TEXT_DIM", "#9aa0a6"),
               "err": C("COL_DANGER", "#ff453a")}
        font, font_sm = C("FONT", ("Segoe UI", 10)), C("FONT_SM", ("Segoe UI", 9))
        icon_font = (C("ICON_FONT", "Segoe MDL2 Assets"), 10)
        rows = {}
        for tid in self.order:
            rows[tid] = self._popup_row(parent, tid, width, close, col, font, font_sm, icon_font)
        self._popup = {"parent": parent, "sig": tuple(self.order), "rows": rows}
        parent.after(TICK_MS, self._popup_tick)

    def _popup_row(self, parent, tid, width, close, col, font, font_sm, icon_font):
        it = self.items[tid]
        # Sizes come from the fonts, not fixed pixels: at 125-150% display scaling a
        # fixed-size row cut the status line. The status line spans the whole row
        # (the buttons sit on the name line), and the row is at least as wide as the
        # longest status: "100% · 999,9 MB/s · 99:59:59".
        sample = "100%% · %s · 99:59:59" % _fmt_rate(999.9 * 1048576)
        inner = max(width - 28, tkfont.Font(font=font_sm).measure(sample) + 6)
        row = tk.Frame(parent, bg=col["sf"])
        row.pack(fill="x")
        tk.Frame(row, bg=col["sf"], width=inner + 28, height=1).pack()      # width keeper
        top = tk.Frame(row, bg=col["sf"])
        top.pack(fill="x", padx=(14, 8), pady=(6, 0))
        btns = tk.Frame(top, bg=col["sf"])
        btns.pack(side="right")
        name = tk.Label(top, text="", bg=col["sf"], fg=col["tx"], font=font, anchor="w", cursor="hand2")
        name.pack(side="left", fill="x", expand=True)
        name.bind("<Button-1>", lambda e: (close(), open_folder(it["path"], it["name"])))
        track = tk.Frame(row, bg=col["bd"], height=3)
        track.pack(fill="x", padx=14, pady=(4, 4))
        fill = tk.Frame(track, bg=col["ac"], height=3)
        line = tk.Label(row, text="", bg=col["sf"], fg=col["dim"], font=font_sm, anchor="w")
        line.pack(fill="x", padx=14, pady=(0, 8))

        def btn(glyph, fg, cmd):
            b = tk.Label(btns, text=glyph, bg=col["sf"], fg=fg, font=icon_font, width=2,
                         cursor="hand2", pady=4)
            b.pack(side="left", padx=1)
            b.bind("<Button-1>", lambda e: cmd())
            b.bind("<Enter>", lambda e: b.configure(bg=col["hv"]))
            b.bind("<Leave>", lambda e: b.configure(bg=col["sf"]))
            return b

        pause = None
        if it["h"] is not None and not it["done"]:
            pause = btn(GLYPH_PAUSE, col["tx"], lambda: (self.toggle_pause(tid), refresh()))
            btn(GLYPH_DELETE, col["err"], lambda: self._ask_delete(tid, close))
        else:
            btn(GLYPH_DISMISS, col["dim"], lambda: (self.remove(tid, delete_files=False), self._repopup()))
        btns.update_idletasks()
        name.configure(text=_elide(it["name"], font, inner + 6 - btns.winfo_reqwidth() - 10))

        def refresh():
            if not row.winfo_exists() or tid not in self.items:
                return
            try:
                p, txt, bad = self._status(self.items[tid])
            except Exception:
                return
            fill.place(x=0, y=0, relheight=1, relwidth=max(0.0, min(1.0, p)))
            line.configure(text=_elide(txt, font_sm, inner), fg=col["err"] if bad else col["dim"])
            if pause is not None:
                cur = self.items[tid]
                pause.configure(text=GLYPH_PLAY if (cur["paused"] or cur["err"]) else GLYPH_PAUSE)
        refresh()
        return refresh

    def _popup_tick(self):
        pp = self._popup
        if not pp or not pp["parent"].winfo_exists():
            self._popup = None
            return
        if tuple(self.order) != pp["sig"]:
            self._repopup()
            return
        for refresh in pp["rows"].values():
            refresh()
        pp["parent"].after(TICK_MS, self._popup_tick)

    def _repopup(self):
        """The list changed while the tray menu is open: redraw it in place."""
        pp = self._popup
        if pp and pp["parent"].winfo_exists():
            self.app._repopup()

    def _ask_delete(self, tid, close):
        close()
        it = self.items.get(tid)
        if it is None:
            return
        if self.app._ask_yesno(S("delete_title"), S("delete_q", it["name"])):
            self.remove(tid, delete_files=True)

    # ---- download history: history.json, oldest first, one entry per torrent ----
    def _history_load(self):
        try:
            with open(self.history_file, encoding="utf-8") as f:
                rows = json.load(f)
            return rows if isinstance(rows, list) else []
        except Exception:
            return []

    def _history_save(self):
        tmp = self.history_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.history, f, ensure_ascii=False)
            os.replace(tmp, self.history_file)
        except Exception as ex:
            self.log("torrent history save failed: %r" % ex)

    def _history_add(self, tid, it, size):
        if not self.get("history"):
            return
        self.history = [e for e in self.history if e.get("id") != tid]     # downloaded again
        self.history.append({"id": tid, "t": time.time(), "name": it["name"],
                             "path": it["path"], "size": size})
        del self.history[:-HISTORY_MAX]
        self._history_save()
        self._history_refresh()

    def _history_delete(self, entry):
        if entry in self.history:
            self.history.remove(entry)
            self._history_save()
        self._history_refresh()

    def _history_clear(self):
        if self.app._ask_yesno(S("hist_menu"), S("hist_clear_q")):
            self.history = []
            self._history_save()
            self._history_refresh()

    def _history_refresh(self):
        if self.hist_win and self.hist_win.winfo_exists():
            self._history_fill()

    def open_history(self):
        if self.hist_win and self.hist_win.winfo_exists():
            self.hist_win.lift()
            self.hist_win.focus_force()
            return
        main = __import__("__main__")
        C = lambda n, d: getattr(main, n, d)
        c = self._hc = {"sf": C("COL_SURFACE", "#232427"), "sf2": C("COL_SURFACE2", "#2d2e31"),
                        "bg": C("COL_BG", "#1b1b1d"), "tx": C("COL_TEXT", "#e6e8ea"),
                        "dim": C("COL_TEXT_DIM", "#9aa0a6"), "bd": C("COL_BORDER", "#2a2b2c"),
                        "ac": C("COL_ACCENT", "#4c8bf5"), "font": C("FONT", ("Segoe UI", 10)),
                        "font_sm": C("FONT_SM", ("Segoe UI", 9))}
        win = self.app._dialog(S("hist_menu"), 600, 560)
        win.resizable(True, True)
        win.minsize(420, 320)
        win.attributes("-topmost", False)
        self.hist_win = win

        top = tk.Frame(win, bg=c["sf"])
        top.pack(fill="x", padx=16, pady=(14, 6))
        self._hist_q = tk.StringVar()
        ent = tk.Entry(top, textvariable=self._hist_q, font=c["font"], bg=c["bg"], fg=c["dim"],
                       insertbackground=c["tx"], relief="flat", highlightthickness=1,
                       highlightbackground=c["bd"], highlightcolor=c["ac"])
        ent.pack(side="left", fill="x", expand=True, ipady=5)
        ent.insert(0, S("hist_search"))

        def focus_in(_e):
            if ent.get() == S("hist_search"):
                ent.delete(0, "end")
                ent.configure(fg=c["tx"])

        def focus_out(_e):
            if not ent.get():
                ent.insert(0, S("hist_search"))
                ent.configure(fg=c["dim"])
        ent.bind("<FocusIn>", focus_in)
        ent.bind("<FocusOut>", focus_out)
        self.app._ghost_btn(top, S("hist_clear"), self._history_clear).pack(side="left", padx=(8, 0))

        box = tk.Frame(win, bg=c["sf"])
        box.pack(fill="both", expand=True, padx=(16, 8), pady=(0, 12))
        cv = tk.Canvas(box, bg=c["sf"], highlightthickness=0, bd=0)
        sb = self.app._dark_scrollbar(box, cv)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        body = tk.Frame(cv, bg=c["sf"])
        wid = cv.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: (cv.itemconfigure(wid, width=e.width), self._history_rewrap(e.width)))
        win.bind("<MouseWheel>", lambda e: cv.yview_scroll(int(-e.delta / 120) * 3, "units"))
        win.bind("<Escape>", lambda e: win.destroy())
        self._hist_cv, self._hist_body, self._hist_labels = cv, body, []
        self._hist_q.trace_add("write", lambda *a: (self._history_fill(), cv.yview_moveto(0)))
        self._history_fill()
        win.focus_force()

    def _history_rewrap(self, width):
        for lb in self._hist_labels:
            lb.configure(wraplength=max(200, width - 60))

    def _history_fill(self):
        body, c = self._hist_body, self._hc
        for w in body.winfo_children():
            w.destroy()
        self._hist_labels = []
        q = self._hist_q.get().strip().lower()
        if q == S("hist_search").lower():
            q = ""
        items = list(reversed(self.history))
        if q:
            items = [e for e in items if q in e.get("name", "").lower()]
        if not items:
            tk.Label(body, text=S("hist_nomatch") if q else S("hist_empty"), bg=c["sf"], fg=c["dim"],
                     font=c["font"]).pack(pady=40)
            return
        wrap = max(200, self._hist_cv.winfo_width() - 60)
        for e in items:
            name, path = e.get("name", ""), e.get("path", "")
            gone = not os.path.exists(os.path.join(path, name))
            card = tk.Frame(body, bg=c["sf2"])
            card.pack(fill="x", pady=(0, 8), padx=(0, 4))
            head = tk.Frame(card, bg=c["sf2"])
            head.pack(fill="x", padx=12, pady=(8, 2))
            x = tk.Label(head, text="✕", bg=c["sf2"], fg=c["dim"], font=c["font"], cursor="hand2")
            x.pack(side="right", anchor="n")
            x.bind("<Button-1>", lambda _ev, e=e: self._history_delete(e))
            lb = tk.Label(head, text=name, bg=c["sf2"], fg=c["dim"] if gone else c["tx"], font=c["font"],
                          justify="left", anchor="w", wraplength=wrap, cursor="hand2")
            lb.pack(side="left", fill="x", expand=True)
            lb.bind("<Button-1>", lambda _ev, p=path, n=name: open_folder(p, n))
            parts = [time.strftime("%d.%m.%Y %H:%M", time.localtime(e.get("t", 0)))]
            if e.get("size"):
                parts.append(_fmt_size(e["size"]))
            parts.append(S("hist_gone") if gone else path)
            info = tk.Label(card, text=" · ".join(parts), bg=c["sf2"], fg=c["dim"], font=c["font_sm"],
                            justify="left", anchor="w", wraplength=wrap)
            info.pack(fill="x", padx=12, pady=(0, 8))
            self._hist_labels += [lb, info]

    # ---- settings tab ----
    def build_into(self, parent, win, pad):
        main = __import__("__main__")
        C = lambda n, d: getattr(main, n, d)
        sf, bg, tx, dim = (C("COL_SURFACE", "#232427"), C("COL_BG", "#1b1b1d"),
                           C("COL_TEXT", "#e6e8ea"), C("COL_TEXT_DIM", "#9aa0a6"))
        font, font_sm = C("FONT", ("Segoe UI", 10)), C("FONT_SM", ("Segoe UI", 9))
        if self.draft is None:
            self.begin_edit()
        d = self.draft

        tk.Label(parent, text=S("folder"), bg=sf, fg=dim, font=font_sm).pack(
            anchor="w", padx=pad, pady=(18, 4))
        row = tk.Frame(parent, bg=sf)
        row.pack(fill="x", padx=pad)
        path = tk.Label(row, text="", bg=bg, fg=tx, font=font, anchor="w", padx=10, pady=6)
        path.pack(side="left", fill="x", expand=True)

        def show():
            path.configure(text=_elide_mid(self.folder(), font, 440))

        def pick():
            p = filedialog.askdirectory(parent=win, initialdir=self.folder(), mustexist=False)
            if p:
                d["dir"] = os.path.normpath(p)
                show()
        self.app._ghost_btn(row, S("change"), pick).pack(side="left", padx=(8, 0))
        show()

        tk.Label(parent, text=S("presets"), bg=sf, fg=dim, font=font_sm).pack(
            anchor="w", padx=pad, pady=(22, 2))
        for key in ("max_speed", "force_seeds", "merge", "auto_stop", "auto_clear"):
            self.app._setting_check(parent, S(key), self.get(key),
                                    lambda v, k=key: d.__setitem__(k, bool(v)), pad)
        self.app._setting_check(parent, S("hist_keep"), self.get("history"),
                                lambda v: d.__setitem__("history", bool(v)), pad)
        self.app._ghost_btn(parent, S("hist_open"), self.open_history).pack(
            anchor="w", padx=pad, pady=(10, 0))


def ti_private(h):
    try:
        tf = h.torrent_file()
        return bool(tf and tf.priv())
    except Exception:
        return False


def open_folder(path, name):
    """Show the download in Explorer (selected, if it already exists)."""
    full = os.path.join(path, name)
    try:
        if os.path.exists(full):
            subprocess.Popen(["explorer.exe", "/select,", full])
        elif os.path.isdir(path):
            subprocess.Popen(["explorer.exe", path])
    except Exception:
        pass
