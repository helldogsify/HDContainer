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
    "bad_file": {"en": "Can't open the torrent file", "ru": "Не удалось открыть торрент-файл", "es": "No se puede abrir el archivo torrent", "pt": "Não foi possível abrir o arquivo torrent", "de": "Torrent-Datei kann nicht geöffnet werden", "fr": "Impossible d’ouvrir le fichier torrent", "zh": "无法打开种子文件"},
    "mbs": {"en": "MB/s", "ru": "МБ/с", "es": "MB/s", "pt": "MB/s", "de": "MB/s", "fr": "Mo/s", "zh": "MB/s"},
    "kbs": {"en": "KB/s", "ru": "КБ/с", "es": "KB/s", "pt": "KB/s", "de": "KB/s", "fr": "Ko/s", "zh": "KB/s"},
}

LANG_GET = lambda: "en"
DECIMAL_COMMA = ("ru", "es", "pt", "de", "fr")


def S(key, *args):
    s = TSTR.get(key, {})
    txt = s.get(LANG_GET()) or s.get("en") or key
    return (txt % args) if args else txt


DEFAULTS = {"dir": "", "max_speed": True, "force_seeds": True, "merge": True,
            "auto_stop": True, "auto_clear": True}

LISTEN = "0.0.0.0:6881,[::]:6881"
FORCE_EVERY = 180        # s between forced announces while seeds are scarce
FORCE_MIN_SEEDS = 5      # fewer connected seeds than this -> keep forcing
RESUME_EVERY = 300       # s between resume-data snapshots of active downloads
TICK_MS = 1000

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
        self._restore()
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
            return
        if it is not None:                 # finished entry kept in the list: start over
            self.items.pop(tid)
            self.order.remove(tid)
        try:
            with open(path, "rb") as src, open(self._p(tid, ".torrent"), "wb") as dst:
                dst.write(src.read())
        except Exception as ex:
            self.log("torrent copy failed: %r" % ex)
        self._add(ti, self.folder())
        self._save_list()
        self._repopup()

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
                try:
                    tid = _tid_of(a.handle.info_hashes())
                    if tid in self.items:
                        with open(self._p(tid, ".resume"), "wb") as f:
                            f.write(lt.write_resume_data_buf(a.params))
                except Exception as ex:
                    self.log("resume write failed: %r" % ex)
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

    def shutdown(self):
        """Quit: snapshot resume data so a restart continues where it stopped."""
        if self.ses is None:
            return
        n = 0
        for it in self.items.values():
            if it["h"] is not None and it["h"].is_valid():
                it["h"].save_resume_data(lt.torrent_handle.flush_disk_cache)
                n += 1
        deadline = time.time() + 5
        while n > 0 and time.time() < deadline:
            if self.ses.wait_for_alert(250) is not None:
                n -= self._alerts()
        self._save_list()
        self.ses.pause()
        self.ses = None

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
        else:
            subprocess.Popen(["explorer.exe", path])
    except Exception:
        pass
