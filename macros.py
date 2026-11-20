# -*- coding: utf-8 -*-
"""Key scripts for HDContainer: a trigger key pressed in a chosen program is replaced by a
sequence of key presses with random pauses (e.g. E -> E, 2, E).

Rules of a script (settings["macros"]["scripts"], a list of dicts):
  * fires only while one of its programs (exe names) is in front;
  * the trigger may carry Ctrl/Alt/Shift/Win, which must match exactly;
  * Enter pressed in a script's program pauses scripts until the next Enter (game chat: open /
    send), Esc ends the pause (chat closed); while paused the key goes through untouched;
  * each step = key down, hold, key up; between steps a pause; both random in their ranges
    with a human-like spread (human_ms);
  * switched on and off from the tray menu.

Engine: own WH_KEYBOARD_LL hook on its own thread, installed only while some script is active
(or a key is being captured). The hook never touches tk: it swallows the trigger and hands the
script to a player thread; capture results go to a queue read by a tk timer. Injected keys
(our own SendInput) are ignored, so a step equal to the trigger never loops. Steps are sent as
scan codes: games that read DirectInput / raw input see them like real keys.
"""
import queue
import random
import threading
import time
import traceback
import uuid
import ctypes
import tkinter as tk

import voice_sys as vs

# ---------------------------------------------------------------------------
#  Strings (7 languages, as in the main app)
# ---------------------------------------------------------------------------
MSTR = {
    "tab": {"en": "Key scripts", "ru": "Скрипты клавиш", "es": "Scripts de teclas", "pt": "Scripts de teclas", "de": "Tastenskripte", "fr": "Scripts de touches", "zh": "按键脚本"},
    "new": {"en": "New script", "ru": "Новый скрипт", "es": "Nuevo script", "pt": "Novo script", "de": "Neues Skript", "fr": "Nouveau script", "zh": "新建脚本"},
    "default_name": {"en": "Script %d", "ru": "Скрипт %d", "es": "Script %d", "pt": "Script %d", "de": "Skript %d", "fr": "Script %d", "zh": "脚本 %d"},
    "empty": {"en": "No scripts yet.", "ru": "Скриптов пока нет.", "es": "Aún no hay scripts.", "pt": "Ainda não há scripts.", "de": "Noch keine Skripte.", "fr": "Aucun script pour l'instant.", "zh": "还没有脚本。"},
    "trigger": {"en": "Key", "ru": "Клавиша", "es": "Tecla", "pt": "Tecla", "de": "Taste", "fr": "Touche", "zh": "按键"},
    "change": {"en": "Change…", "ru": "Изменить…", "es": "Cambiar…", "pt": "Alterar…", "de": "Ändern…", "fr": "Modifier…", "zh": "更改…"},
    "not_set": {"en": "not set", "ru": "не задана", "es": "sin asignar", "pt": "não definida", "de": "nicht gesetzt", "fr": "non définie", "zh": "未设置"},
    "steps": {"en": "Presses", "ru": "Нажимает", "es": "Pulsa", "pt": "Pressiona", "de": "Drückt", "fr": "Appuie sur", "zh": "依次按下"},
    "add_key": {"en": "+ Key", "ru": "+ Клавиша", "es": "+ Tecla", "pt": "+ Tecla", "de": "+ Taste", "fr": "+ Touche", "zh": "+ 按键"},
    "delay": {"en": "Pause between keys, ms", "ru": "Пауза между клавишами, мс", "es": "Pausa entre teclas, ms", "pt": "Pausa entre teclas, ms", "de": "Pause zwischen Tasten, ms", "fr": "Pause entre les touches, ms", "zh": "按键间隔，毫秒"},
    "hold": {"en": "Key hold, ms", "ru": "Удержание клавиши, мс", "es": "Mantener tecla, ms", "pt": "Segurar tecla, ms", "de": "Taste halten, ms", "fr": "Maintien de la touche, ms", "zh": "按住时长，毫秒"},
    "programs": {"en": "Only in", "ru": "Только в программах", "es": "Solo en", "pt": "Só em", "de": "Nur in", "fr": "Seulement dans", "zh": "仅在以下程序中"},
    "add_prog": {"en": "+ Program", "ru": "+ Программа", "es": "+ Programa", "pt": "+ Programa", "de": "+ Programm", "fr": "+ Programme", "zh": "+ 程序"},
    "prog_none": {"en": "No programs with windows", "ru": "Нет программ с окнами", "es": "No hay programas con ventanas", "pt": "Nenhum programa com janelas", "de": "Keine Programme mit Fenstern", "fr": "Aucun programme avec fenêtre", "zh": "没有带窗口的程序"},
    "prog_file": {"en": "Choose .exe…", "ru": "Выбрать .exe…", "es": "Elegir .exe…", "pt": "Escolher .exe…", "de": ".exe wählen…", "fr": "Choisir un .exe…", "zh": "选择 .exe…"},
    "exe_files": {"en": "Programs", "ru": "Программы", "es": "Programas", "pt": "Programas", "de": "Programme", "fr": "Programmes", "zh": "程序"},
    "enter": {"en": "Enter pauses the script until the next Enter (chat)", "ru": "Enter выключает скрипт до следующего Enter (чат)", "es": "Enter pausa el script hasta el siguiente Enter (chat)", "pt": "Enter pausa o script até o próximo Enter (chat)", "de": "Enter pausiert das Skript bis zum nächsten Enter (Chat)", "fr": "Entrée met le script en pause jusqu'à la prochaine Entrée (chat)", "zh": "按 Enter 暂停脚本，直到再次按 Enter（聊天）"},
    "sec": {"en": "s", "ru": "с", "es": "s", "pt": "s", "de": "s", "fr": "s", "zh": "秒"},
    "name": {"en": "Name", "ru": "Название", "es": "Nombre", "pt": "Nome", "de": "Name", "fr": "Nom", "zh": "名称"},
    "delete": {"en": "Delete", "ru": "Удалить", "es": "Eliminar", "pt": "Excluir", "de": "Löschen", "fr": "Supprimer", "zh": "删除"},
    "delete_q": {"en": "Delete the script «%s»?", "ru": "Удалить скрипт «%s»?", "es": "¿Eliminar el script «%s»?", "pt": "Excluir o script «%s»?", "de": "Skript «%s» löschen?", "fr": "Supprimer le script « %s » ?", "zh": "删除脚本「%s」？"},
    "press_key": {"en": "Press the key…\nEsc — cancel", "ru": "Нажми клавишу…\nEsc — отмена", "es": "Pulsa la tecla…\nEsc — cancelar", "pt": "Pressione a tecla…\nEsc — cancelar", "de": "Taste drücken…\nEsc — abbrechen", "fr": "Appuie sur la touche…\nEsc — annuler", "zh": "请按下按键…\nEsc — 取消"},
    "trig_bad": {"en": "The script key must be an ordinary key (letter, digit, F-key…), optionally with Ctrl/Alt/Shift/Win.", "ru": "Клавиша скрипта должна быть обычной (буква, цифра, F-клавиша…), можно с Ctrl/Alt/Shift/Win.", "es": "La tecla del script debe ser una tecla normal (letra, número, tecla F…), con Ctrl/Alt/Shift/Win si quieres.", "pt": "A tecla do script deve ser uma tecla comum (letra, número, tecla F…), com Ctrl/Alt/Shift/Win se quiser.", "de": "Die Skripttaste muss eine normale Taste sein (Buchstabe, Ziffer, F-Taste…), optional mit Strg/Alt/Umschalt/Win.", "fr": "La touche du script doit être une touche ordinaire (lettre, chiffre, touche F…), avec Ctrl/Alt/Maj/Win si besoin.", "zh": "脚本按键必须是普通键（字母、数字、F 键…），可加 Ctrl/Alt/Shift/Win。"},
    "warn": {"en": "Won't run: %s", "ru": "Не сработает: %s", "es": "No funcionará: %s", "pt": "Não vai funcionar: %s", "de": "Läuft nicht: %s", "fr": "Ne fonctionnera pas : %s", "zh": "不会运行：%s"},
    "w_trigger": {"en": "no key", "ru": "нет клавиши", "es": "sin tecla", "pt": "sem tecla", "de": "keine Taste", "fr": "pas de touche", "zh": "没有按键"},
    "w_steps": {"en": "nothing to press", "ru": "нечего нажимать", "es": "nada que pulsar", "pt": "nada a pressionar", "de": "nichts zu drücken", "fr": "rien à appuyer", "zh": "没有要按的键"},
    "r_ok": {"en": "Human-like", "ru": "Как у человека", "es": "Como una persona", "pt": "Como uma pessoa", "de": "Wie ein Mensch", "fr": "Comme un humain", "zh": "像真人"},
    "r_swapped": {"en": "«From» is larger than «to»", "ru": "«От» больше, чем «до»", "es": "«Desde» es mayor que «hasta»", "pt": "«De» é maior que «até»", "de": "«Von» ist größer als «bis»", "fr": "« De » est plus grand que « à »", "zh": "下限大于上限"},
    "r_too_fast": {"en": "No human is that fast — start from %d ms", "ru": "Так быстро человек не может — начинай от %d мс", "es": "Nadie es tan rápido: empieza desde %d ms", "pt": "Ninguém é tão rápido — comece de %d ms", "de": "So schnell ist kein Mensch — ab %d ms", "fr": "Aucun humain n'est aussi rapide — commence à %d ms", "zh": "真人不可能这么快——从 %d 毫秒起"},
    "r_fast": {"en": "Very fast for a person — better from %d ms", "ru": "Очень быстро для человека — лучше от %d мс", "es": "Muy rápido para una persona: mejor desde %d ms", "pt": "Muito rápido para uma pessoa — melhor de %d ms", "de": "Sehr schnell für einen Menschen — besser ab %d ms", "fr": "Très rapide pour un humain — mieux à partir de %d ms", "zh": "对真人来说太快——建议从 %d 毫秒起"},
    "r_slow": {"en": "Slow for a person — better up to %d ms", "ru": "Медленно для человека — лучше до %d мс", "es": "Lento para una persona: mejor hasta %d ms", "pt": "Lento para uma pessoa — melhor até %d ms", "de": "Langsam für einen Menschen — besser bis %d ms", "fr": "Lent pour un humain — mieux jusqu'à %d ms", "zh": "对真人来说偏慢——建议不超过 %d 毫秒"},
    "r_too_slow": {"en": "Far too long — keep it up to %d ms", "ru": "Слишком долго — не больше %d мс", "es": "Demasiado largo: como máximo %d ms", "pt": "Longo demais — no máximo %d ms", "de": "Viel zu lang — höchstens %d ms", "fr": "Beaucoup trop long — au plus %d ms", "zh": "太长了——不要超过 %d 毫秒"},
    "r_flat": {"en": "Same time every press — that is a robot. Widen the range", "ru": "Каждый раз одно и то же время — так жмёт робот. Раздвинь рамки", "es": "Mismo tiempo en cada pulsación: eso es un robot. Amplía el rango", "pt": "Mesmo tempo a cada toque — isso é um robô. Amplie o intervalo", "de": "Jedes Mal dieselbe Zeit — so drückt ein Roboter. Bereich erweitern", "fr": "Toujours le même temps — c'est un robot. Élargis la plage", "zh": "每次时间都一样——这是机器人。请拉开范围"},
    "r_narrow": {"en": "People are less even — make the range at least %d ms wide", "ru": "Люди не такие ровные — сделай разброс хотя бы %d мс", "es": "Las personas son menos regulares: deja un rango de al menos %d ms", "pt": "Pessoas são menos regulares — deixe um intervalo de pelo menos %d ms", "de": "Menschen sind ungleichmäßiger — Bereich mindestens %d ms breit", "fr": "Les humains sont moins réguliers — une plage d'au moins %d ms", "zh": "真人没那么均匀——范围至少拉开 %d 毫秒"},
    "w_progs": {"en": "no program", "ru": "не выбрана программа", "es": "sin programa", "pt": "sem programa", "de": "kein Programm", "fr": "pas de programme", "zh": "未选择程序"},
}

LANG_GET = lambda: "en"


def M(key, *args):
    s = MSTR.get(key, {})
    txt = s.get(LANG_GET()) or s.get("en") or key
    return (txt % args) if args else txt


def _theme():
    main = __import__("__main__")
    C = lambda n, d: getattr(main, n, d)
    return {"bg": C("COL_BG", "#1b1b1d"), "sf": C("COL_SURFACE", "#232427"), "sf2": C("COL_SURFACE2", "#2d2e31"),
            "bd": C("COL_BORDER", "#2a2b2c"), "hv": C("COL_HOVER", "#303236"), "ac": C("COL_ACCENT", "#4c8bf5"),
            "tx": C("COL_TEXT", "#e6e8ea"), "dim": C("COL_TEXT_DIM", "#9aa0a6"), "err": C("COL_DANGER", "#ff453a"),
            "font": C("FONT", ("Segoe UI", 10)), "font_sm": C("FONT_SM", ("Segoe UI", 9))}


# ---------------------------------------------------------------------------
#  Script data
# ---------------------------------------------------------------------------
MS_MAX = 10000
LABEL_W = 170                       # px: label column of a script card
DEFAULT_SCRIPT = {
    "name": "", "enabled": True,
    "trigger_vk": 0, "trigger_mods": 0,
    "steps": [],                    # [[mods, vk], ...]
    "delay_min": 70, "delay_max": 160,
    "hold_min": 55, "hold_max": 120,
    "programs": [],                 # exe names, lower case
    "enter_block": True, "enter_s": 3.0,
}
VK_RETURN = 0x0D

# Human limits, ms: (red below, yellow below, yellow above, red above, suggested low, suggested high).
# A tap of a key by a person lasts ~50-150 ms (fast players ~40); a deliberate next key in a
# combo comes ~70-300 ms after the previous release; under ~35 ms between different keys is
# beyond human motor timing.
HUMAN = {
    "hold": (25, 45, 250, 600),
    "delay": (35, 70, 400, 1500),
}
RATE_COLORS = {"ok": "#30d158", "warn": "#ffd60a", "bad": None}      # None = theme danger colour


def rate(kind, lo, hi):
    """(level, text) of how human a [lo, hi] ms range is: level ok / warn / bad."""
    red_lo, yel_lo, yel_hi, red_hi = HUMAN[kind]
    if lo > hi:
        return "bad", M("r_swapped")
    if lo < red_lo:
        return "bad", M("r_too_fast", yel_lo)
    if hi > red_hi:
        return "bad", M("r_too_slow", yel_hi)
    if hi - lo < 3:
        return "bad", M("r_flat")
    if lo < yel_lo:
        return "warn", M("r_fast", yel_lo)
    if hi > yel_hi:
        return "warn", M("r_slow", yel_hi)
    need = max(20, int(round(lo * 0.3 / 5.0)) * 5)
    if hi - lo < need:
        return "warn", M("r_narrow", need)
    return "ok", M("r_ok")
MODIFIER_VKS = frozenset({0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C})


def _int(v, lo, hi, d):
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except Exception:
        return d


def _float(v, lo, hi, d):
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return d


def normalize(s):
    """A clean script dict from whatever was stored (missing keys, wrong types)."""
    out = dict(DEFAULT_SCRIPT)
    if isinstance(s, dict):
        out.update({k: s[k] for k in DEFAULT_SCRIPT if k in s})
        out["id"] = str(s.get("id") or uuid.uuid4().hex[:8])
    else:
        out["id"] = uuid.uuid4().hex[:8]
    out["name"] = str(out["name"] or "")
    out["enabled"] = bool(out["enabled"])
    out["trigger_vk"] = _int(out["trigger_vk"], 0, 255, 0)
    out["trigger_mods"] = _int(out["trigger_mods"], 0, 15, 0)
    steps = []
    for st in out["steps"] if isinstance(out["steps"], list) else []:
        try:
            m, v = int(st[0]), int(st[1])
            if 0 < v < 256:
                steps.append([m & 15, v])
        except Exception:
            pass
    out["steps"] = steps
    for k in ("delay_min", "delay_max", "hold_min", "hold_max"):
        out[k] = _int(out[k], 0, MS_MAX, DEFAULT_SCRIPT[k])
    progs = out["programs"] if isinstance(out["programs"], list) else []
    out["programs"] = sorted({str(p).strip().lower() for p in progs if str(p).strip()})
    out["enter_block"] = bool(out["enter_block"])
    out["enter_s"] = _float(out["enter_s"], 0.0, 600.0, 3.0)
    return out


def problems(s):
    """Why a script cannot run (list of short strings), empty when it can."""
    p = []
    if not s.get("trigger_vk"):
        p.append(M("w_trigger"))
    if not s.get("steps"):
        p.append(M("w_steps"))
    if not s.get("programs"):
        p.append(M("w_progs"))
    return p


class _Compiled:
    """Read-only view of an active script for the hook and the player threads."""
    __slots__ = ("id", "name", "vk", "mods", "steps", "dmin", "dmax", "hmin", "hmax", "programs",
                 "enter_block", "enter_s")

    def __init__(self, s):
        self.id, self.name = s["id"], s["name"]
        self.vk, self.mods = s["trigger_vk"], s["trigger_mods"]
        self.steps = tuple((m, v) for m, v in s["steps"])
        self.dmin, self.dmax = sorted((s["delay_min"], s["delay_max"]))
        self.hmin, self.hmax = sorted((s["hold_min"], s["hold_max"]))
        self.programs = frozenset(s["programs"])
        self.enter_block, self.enter_s = s["enter_block"], s["enter_s"]


# ---------------------------------------------------------------------------
#  Key output (scan codes, game-friendly)
# ---------------------------------------------------------------------------
KEYEVENTF_SCANCODE = 0x0008
_MOD_VKS = ((vs.MOD_CONTROL, 0xA2), (vs.MOD_ALT, 0xA4), (vs.MOD_SHIFT, 0xA0), (vs.MOD_WIN, 0x5B))
_SIDED_MODS = (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)
vs._decl(vs.winmm.timeBeginPeriod, vs.UINT, [vs.UINT])
vs._decl(vs.winmm.timeEndPeriod, vs.UINT, [vs.UINT])


def _inp(vk, up):
    i = vs.INPUT()
    i.type = vs.INPUT_KEYBOARD
    i.u.ki.wVk = vk
    sc = vs.user32.MapVirtualKeyW(vk, 0) & 0xFF
    fl = vs.KEYEVENTF_KEYUP if up else 0
    if sc:
        i.u.ki.wScan = sc
        fl |= KEYEVENTF_SCANCODE
    if vk in vs._EXTENDED_VKS:
        fl |= vs.KEYEVENTF_EXTENDEDKEY
    i.u.ki.dwFlags = fl
    return i


def _send(events):
    """events: [(vk, up), ...]. True when Windows accepted all of them (UIPI drops input to
    windows of elevated programs silently, SendInput then returns less)."""
    if not events:
        return True
    arr = (vs.INPUT * len(events))(*[_inp(v, u) for v, u in events])
    return vs.user32.SendInput(len(events), arr, ctypes.sizeof(vs.INPUT)) == len(events)


def human_ms(lo, hi):
    """A human-like duration in [lo, hi] ms, as seconds. People do not spread key timings
    evenly: they cluster a bit below the middle with rare long ones, so a beta(2.2, 3.2)
    shape is used instead of a flat random; fractions of a millisecond are kept."""
    if hi <= lo:
        return lo / 1000.0
    return (lo + (hi - lo) * random.betavariate(2.2, 3.2)) / 1000.0


def _mod_vks(mods):
    return [vk for bit, vk in _MOD_VKS if mods & bit]


# ---------------------------------------------------------------------------
#  Hook + player
# ---------------------------------------------------------------------------
class MacroEngine:
    def __init__(self, log):
        self.log = log
        self.events = queue.Queue()           # ("captured", mods, vk) / ("capture_cancel",) / ("fired", id)
        self.scripts = ()                     # tuple of _Compiled, replaced as a whole
        self.capture = False
        self.chat = False                     # Enter pressed in a script's program: paused until the next Enter
        self._down = set()
        self._swallowed = set()               # triggers whose key-up (and auto-repeat) we eat
        self._passing = {}                    # vk -> script: the real press is the first step,
                                              # the rest plays after its release
        self._cap_single = None
        self._busy = False
        self._play_q = queue.Queue()
        self._hook = None
        self._tid = 0
        self._thread = None
        self._proc = vs._HOOKPROC(self._cb)
        self._warned_uipi = False
        threading.Thread(target=self._player, daemon=True, name="hdc-macro-play").start()

    # ---- hook lifecycle (tk thread) ----
    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def set_scripts(self, compiled):
        self.scripts = tuple(compiled)
        self.sync()

    def sync(self):
        want = bool(self.scripts) or self.capture
        if want and not self.running:
            self._start()
        elif not want and self.running:
            self.stop()

    def _start(self):
        ready = threading.Event()

        def run():
            self._tid = vs.kernel32.GetCurrentThreadId()
            self._hook = vs.user32.SetWindowsHookExW(vs.WH_KEYBOARD_LL, self._proc,
                                                     vs.kernel32.GetModuleHandleW(None), 0)
            if not self._hook:
                self.log("macros: keyboard hook failed: %d" % ctypes.get_last_error())
            ready.set()
            msg = vs._MSG()
            while vs.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
            if self._hook:
                vs.user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
        self._down.clear()
        self._swallowed.clear()
        self._passing.clear()
        self.chat = False
        self._thread = threading.Thread(target=run, daemon=True, name="hdc-macro-hook")
        self._thread.start()
        ready.wait(2.0)
        self.log("macros: hook %s, %d active script(s)" % ("on" if self._hook else "FAILED", len(self.scripts)))

    def stop(self):
        t = self._thread
        if t is None:
            return
        if self._tid:
            vs.user32.PostThreadMessageW(self._tid, vs.WM_QUIT, 0, 0)
        t.join(1.0)
        self._thread, self._tid = None, 0
        self.log("macros: hook off")

    # ---- hook callback (hook thread: no tk, answer fast) ----
    def _cb(self, code, wp, lp):
        try:
            if code == 0:
                kb = vs.KBDLLHOOKSTRUCT.from_address(lp)
                if not (kb.flags & vs.LLKHF_INJECTED) and self._handle(
                        kb.vkCode, wp in (vs.WM_KEYUP, vs.WM_SYSKEYUP)):
                    return 1
        except Exception:
            pass
        return vs.user32.CallNextHookEx(None, code, wp, lp)

    def _handle(self, vk, up):
        repeat = (not up) and vk in self._down
        if up:
            self._down.discard(vk)
        else:
            self._down.add(vk)
        if self.capture:
            return self._handle_capture(vk, up, repeat)
        if not up and not repeat and vk in (VK_RETURN, vs.VK_ESCAPE):
            # chat in a game: Enter opens it, Enter sends, Esc closes it. Only keys pressed in a
            # script's program count, so Enter in a messenger does not flip the state
            scripts = self.scripts
            if scripts:
                exe = (vs.foreground_exe() or "").lower()
                if any(exe in sc.programs for sc in scripts):
                    self.chat = (not self.chat) if vk == VK_RETURN else False
            return False
        if vk in self._swallowed:
            if up:
                self._swallowed.discard(vk)
            return True                         # auto-repeat and release of a fired trigger
        if vk in self._passing:
            if up:                              # the real first press is over: play the rest
                s = self._passing.pop(vk)
                if not self._busy:
                    self._busy = True
                    self._play_q.put((s, 1))
            return False                        # the real key (and its auto-repeat) goes through
        if up or repeat or vk in MODIFIER_VKS:
            return False
        scripts = self.scripts
        if not scripts or not any(s.vk == vk for s in scripts):
            return False
        mods = vs.current_mods()
        exe = None
        for s in scripts:
            if s.vk != vk or s.mods != mods:
                continue
            if exe is None:
                exe = (vs.foreground_exe() or "").lower()
            if exe not in s.programs:
                continue
            if s.enter_block and self.chat:
                return False                    # chat typing: the key goes through as it is
            if self._busy:                      # a press during playback is dropped, not queued
                self._swallowed.add(vk)
                return True
            if s.steps[0] == (s.mods, vk) and not s.mods:
                # first step = the trigger itself: let the real press through (real timing, real
                # hold), the other steps follow its release
                self._passing[vk] = s
                return False
            self._swallowed.add(vk)
            self._busy = True
            self._play_q.put((s, 0))
            return True
        return False

    def _handle_capture(self, vk, up, repeat):
        """Capture of one key (+ modifiers) for the settings: every key is swallowed."""
        if not up:
            if repeat:
                return True
            if vk in MODIFIER_VKS:
                others = {v for v in self._down if v != vk}
                self._cap_single = vk if (vk in _SIDED_MODS and not others) else None
                return True
            mods = 0
            for v in self._down:
                if v != vk:
                    mods |= vs._MOD_BIT.get(v, 0)
            self._cap_single = None
            self.capture = False
            self._swallowed.add(vk)
            if vk == vs.VK_ESCAPE and mods == 0:
                self.events.put(("capture_cancel",))
            else:
                self.events.put(("captured", mods, vk))
            return True
        if vk == self._cap_single:              # a modifier pressed and released alone
            self.capture = False
            self._cap_single = None
            self.events.put(("captured", 0, vk))
        return True

    # ---- player thread ----
    def _player(self):
        while True:
            job = self._play_q.get()
            if job is None:
                return
            try:
                self._play(*job)
            except Exception:
                self.log("macros: play failed:\n" + traceback.format_exc())
            finally:
                self._busy = False

    def _in_program(self, s):
        return (vs.foreground_exe() or "").lower() in s.programs

    def _play(self, s, first):
        vs.winmm.timeBeginPeriod(1)             # 1 ms sleep resolution instead of ~15.6 ms
        try:
            if s.mods:
                # the user still holds the trigger's modifiers: release them logically, or
                # every step would arrive as Ctrl+<step>
                held = [v for v in _SIDED_MODS if vs.key_down(v)]
                _send([(v, True) for v in held])
            ok = True
            for i, (mods, vk) in enumerate(s.steps[first:]):
                if i or first:                  # after the real first press: a pause too
                    time.sleep(human_ms(s.dmin, s.dmax))
                if not self._in_program(s):
                    self.log("macros: %s stopped, the program left the front" % s.name)
                    return
                mv = _mod_vks(mods)
                ok &= _send([(m, False) for m in mv] + [(vk, False)])
                time.sleep(human_ms(s.hmin, s.hmax))
                ok &= _send([(vk, True)] + [(m, True) for m in reversed(mv)])
            if not ok and not self._warned_uipi:
                self._warned_uipi = True
                self.log("macros: Windows dropped the keys (program runs as administrator?)")
            self.events.put(("fired", s.id))
        finally:
            vs.winmm.timeEndPeriod(1)

    def shutdown(self):
        self.capture = False
        self.scripts = ()
        self.stop()
        self._play_q.put(None)


# ---------------------------------------------------------------------------
#  Controller: settings, tray rows, settings page
# ---------------------------------------------------------------------------
class MacroController:
    def __init__(self, app, get_lang, log):
        global LANG_GET
        LANG_GET = get_lang
        self.app, self.root, self.log = app, app.root, log
        self.draft = None
        self._tab = None
        self._capture_dlg = None
        self._capture_for = None              # ("trigger", id) / ("step", id)
        self.engine = MacroEngine(log)
        self.apply()
        self.root.after(50, self._loop)

    # ---- data ----
    def saved_scripts(self):
        raw = self.app.settings.get("macros", {})
        lst = raw.get("scripts", []) if isinstance(raw, dict) else []
        return [normalize(s) for s in lst]

    def scripts(self):
        return self.draft if self.draft is not None else self.saved_scripts()

    def _find(self, lst, sid):
        for s in lst:
            if s["id"] == sid:
                return s
        return None

    def apply(self):
        active = [_Compiled(s) for s in self.saved_scripts() if s["enabled"] and not problems(s)]
        self.engine.set_scripts(active)

    def _save(self, lst, user=False):
        self.app._set_setting("macros", {"scripts": [normalize(s) for s in lst]}, user=user)

    # ---- settings draft (see recorder.py) ----
    def begin_edit(self):
        self.draft = self.saved_scripts()

    def discard(self):
        self.draft = None
        self._tab = None
        self._end_capture()

    def commit(self):
        new, self.draft = self.draft, None
        self._tab = None
        self._end_capture()
        if new is None:
            return
        self._save(new)
        self.apply()
        self.log("macros settings saved: %d script(s)" % len(new))

    # ---- tray ----
    def tray_items(self):
        """[(id, name, enabled, runnable)] of the saved scripts, for the tray menu."""
        return [(s["id"], s["name"] or M("default_name", i + 1), s["enabled"], not problems(s))
                for i, s in enumerate(self.saved_scripts())]

    def set_enabled(self, sid, on):
        """Tray switch: saved at once; an open Settings draft follows it."""
        lst = self.saved_scripts()
        s = self._find(lst, sid)
        if s is None:
            return
        s["enabled"] = bool(on)
        self._save(lst, user=True)
        if self.draft is not None:
            d = self._find(self.draft, sid)
            if d is not None:
                d["enabled"] = bool(on)
                self._rebuild()
        self.apply()
        self.log("macros: %s %s" % (s["name"] or sid, "on" if on else "off"))

    # ---- events ----
    def _loop(self):
        try:
            while True:
                try:
                    ev = self.engine.events.get_nowait()
                except queue.Empty:
                    break
                if ev[0] in ("captured", "capture_cancel"):
                    self._capture_done(ev)
        except Exception:
            self.log("macros loop:\n" + traceback.format_exc())
        self.root.after(50, self._loop)

    def shutdown(self):
        try:
            self.engine.shutdown()
        except Exception:
            pass

    # ---- capture ----
    def _start_capture(self, what, sid):
        th = _theme()
        self._end_capture()
        dlg = self.app._dialog(M("tab"), 420, 170)
        tk.Label(dlg, text=M("press_key"), bg=th["sf"], fg=th["tx"], font=("Segoe UI", 11),
                 justify="center").pack(expand=True)
        self._capture_dlg = dlg
        self._capture_for = (what, sid)
        self.engine.capture = True
        self.engine.sync()
        dlg.protocol("WM_DELETE_WINDOW", self._end_capture)
        dlg.focus_force()

    def _end_capture(self):
        self.engine.capture = False
        self.engine.sync()
        self._capture_for = None
        dlg, self._capture_dlg = self._capture_dlg, None
        if dlg:
            try:
                dlg.destroy()
            except Exception:
                pass

    def _capture_done(self, ev):
        target = self._capture_for
        self._end_capture()
        if not target or self.draft is None:
            return
        s = self._find(self.draft, target[1])
        if s is None or ev[0] != "captured":
            return
        mods, vk = ev[1], ev[2]
        if target[0] == "trigger":
            if vk in MODIFIER_VKS:
                self.app._info(M("tab"), M("trig_bad"))
                return
            s["trigger_vk"], s["trigger_mods"] = vk, mods
        else:
            s["steps"].append([mods, vk])
        self._rebuild()

    # ---- settings page ----
    def build_into(self, parent, win, pad):
        th = _theme()
        self._tab = (parent, win, pad)
        if self.draft is None:
            self.begin_edit()
        tk.Frame(parent, bg=th["sf"], height=8).pack()
        top = tk.Frame(parent, bg=th["sf"])
        top.pack(fill="x", padx=pad, pady=(0, 8))
        self.app._accent_btn(top, M("new"), self._new_script).pack(side="left")
        if not self.draft:
            tk.Label(parent, text=M("empty"), bg=th["sf"], fg=th["dim"], font=th["font"]).pack(
                anchor="w", padx=pad, pady=(12, 0))
        for i, s in enumerate(self.draft):
            self._card(parent, win, pad, s, i, th)
        tk.Frame(parent, bg=th["sf"], height=16).pack()

    def _card(self, parent, win, pad, s, idx, th):
        a = self.app
        outer = tk.Frame(parent, bg=th["bd"])
        outer.pack(fill="x", padx=pad, pady=(8, 4))
        card = tk.Frame(outer, bg=th["bg"])
        card.pack(fill="x", padx=1, pady=1)
        cp = 16
        bg = th["bg"]

        # title: switch + name (click = rename) + delete
        row = tk.Frame(card, bg=bg)
        row.pack(fill="x", padx=cp, pady=(12, 6))
        chk, _st, _d = a._make_check(row, s["enabled"], lambda v: (s.__setitem__("enabled", bool(v)),
                                                                     self._rebuild()), bg=bg)
        chk.pack(side="left")
        name = s["name"] or M("default_name", idx + 1)
        nl = tk.Label(row, text=name, bg=bg, fg=th["tx"] if s["enabled"] else th["dim"],
                      font=("Segoe UI Semibold", 11), cursor="hand2")
        nl.pack(side="left", padx=(10, 0))
        nl.bind("<Button-1>", lambda _e: self._rename(s, name))
        dl = tk.Label(row, text=M("delete"), bg=bg, fg=th["dim"], font=th["font_sm"], cursor="hand2")
        dl.pack(side="right")
        dl.bind("<Button-1>", lambda _e: self._delete(s, name))
        dl.bind("<Enter>", lambda _e: dl.configure(fg=th["err"]))
        dl.bind("<Leave>", lambda _e: dl.configure(fg=th["dim"]))

        def line(label):
            r = tk.Frame(card, bg=bg)
            r.pack(fill="x", padx=cp, pady=4)
            col = tk.Frame(r, bg=bg, width=LABEL_W, height=26)
            col.pack(side="left")
            col.pack_propagate(False)
            tk.Label(col, text=label, bg=bg, fg=th["dim"], font=th["font_sm"], anchor="w").pack(side="left")
            return r

        def chip(r, text, on_x=None, mono=True):
            f = tk.Frame(r, bg=th["sf2"])
            f.pack(side="left", padx=(0, 6))
            tk.Label(f, text=text, bg=th["sf2"], fg=th["tx"], font=("Consolas", 10) if mono else th["font_sm"],
                     padx=8, pady=3).pack(side="left")
            if on_x:
                x = tk.Label(f, text="✕", bg=th["sf2"], fg=th["dim"], font=th["font_sm"], padx=4, cursor="hand2")
                x.pack(side="left")
                x.bind("<Button-1>", lambda _e: on_x())
                x.bind("<Enter>", lambda _e: x.configure(fg=th["err"]))
                x.bind("<Leave>", lambda _e: x.configure(fg=th["dim"]))
            return f

        def link(r, text, cmd):
            lb = tk.Label(r, text=text, bg=bg, fg=th["ac"], font=th["font_sm"], cursor="hand2", padx=4)
            lb.pack(side="left")
            lb.bind("<Button-1>", lambda _e: cmd())
            return lb

        # trigger
        r = line(M("trigger"))
        trig = vs.hotkey_label(s["trigger_mods"], s["trigger_vk"]) if s["trigger_vk"] else M("not_set")
        chip(r, trig, mono=bool(s["trigger_vk"]))
        link(r, M("change"), lambda: self._start_capture("trigger", s["id"]))

        # steps
        r = line(M("steps"))
        wrap = tk.Frame(r, bg=bg)
        wrap.pack(side="left", fill="x", expand=True)
        for j, (m, v) in enumerate(s["steps"]):
            if j:
                tk.Label(wrap, text="→", bg=bg, fg=th["dim"], font=th["font_sm"]).pack(side="left", padx=(0, 6))
            chip(wrap, vs.hotkey_label(m, v), lambda j=j: (s["steps"].pop(j), self._rebuild()))
        link(wrap, M("add_key"), lambda: self._start_capture("step", s["id"]))

        # timings
        self._range_row(card, cp, line(M("delay")), s, "delay", th)
        self._range_row(card, cp, line(M("hold")), s, "hold", th)

        # programs
        r = line(M("programs"))
        for p in s["programs"]:
            chip(r, p, lambda p=p: (s["programs"].remove(p), self._rebuild()), mono=False)
        add = link(r, M("add_prog"), lambda: self._prog_menu(s, add, win))

        # Enter rule
        r = tk.Frame(card, bg=bg)
        r.pack(fill="x", padx=cp, pady=(6, 4))
        chk2, _s2, _d2 = a._make_check(r, s["enter_block"], lambda v: s.__setitem__("enter_block", bool(v)), bg=bg)
        chk2.pack(side="left")
        tk.Label(r, text="  " + M("enter"), bg=bg, fg=th["tx"], font=th["font_sm"]).pack(side="left")

        bad = problems(s)
        if bad:
            tk.Label(card, text=M("warn", ", ".join(bad)), bg=bg, fg=th["err"], font=th["font_sm"],
                     anchor="w").pack(fill="x", padx=cp, pady=(4, 0))
        tk.Frame(card, bg=bg, height=10).pack()

    def _num(self, parent, s, key, lo, hi, th, is_float=False, on_change=None):
        var = tk.StringVar(value=(("%g" % s[key]) if is_float else str(s[key])))
        e = tk.Entry(parent, textvariable=var, width=6, font=th["font"], bg=th["sf2"], fg=th["tx"],
                     insertbackground=th["tx"], relief="flat", justify="center", highlightthickness=1,
                     highlightbackground=th["bd"], highlightcolor=th["ac"])

        def store(_e=None):
            txt = var.get().strip().replace(",", ".")
            if is_float:
                s[key] = _float(txt, lo, hi, s[key])
            else:
                s[key] = _int(txt, lo, hi, s[key])
            if on_change:
                on_change()

        def finish(_e=None):
            store()
            var.set(("%g" % s[key]) if is_float else str(s[key]))
        e.bind("<KeyRelease>", store)
        e.bind("<FocusOut>", finish)
        e.bind("<Return>", finish)
        return e

    def _range_row(self, card, cp, r, s, kind, th):
        """[from] – [to] and under it a live rating: green human-like, yellow advice, red not human."""
        kmin, kmax = kind + "_min", kind + "_max"
        bg = th["bg"]
        st = tk.Frame(card, bg=bg)
        st.pack(fill="x", padx=(cp + LABEL_W, cp), pady=(0, 2))
        dot = tk.Label(st, text="●", bg=bg, font=("Segoe UI", 8))
        dot.pack(side="left", anchor="n", pady=(2, 0))
        txt = tk.Label(st, text="", bg=bg, font=th["font_sm"], anchor="w", justify="left", wraplength=340)
        txt.pack(side="left", padx=(5, 0))

        def show():
            level, text = rate(kind, s[kmin], s[kmax])
            col = RATE_COLORS[level] or th["err"]
            dot.configure(fg=col)
            txt.configure(text=text, fg=col)
        self._num(r, s, kmin, 0, MS_MAX, th, on_change=show).pack(side="left")
        tk.Label(r, text="–", bg=bg, fg=th["dim"], font=th["font"]).pack(side="left", padx=6)
        self._num(r, s, kmax, 0, MS_MAX, th, on_change=show).pack(side="left")
        show()

    def _prog_menu(self, s, anchor, win):
        cur = set(s["programs"])
        m = tk.Menu(anchor, tearoff=0)
        progs = [n for n in vs.running_programs() if n not in cur]
        for n in progs:
            m.add_command(label=n, command=lambda n=n: self._add_prog(s, n))
        if not progs:
            m.add_command(label=M("prog_none"), state="disabled")
        m.add_separator()

        def browse():
            from tkinter import filedialog
            import os
            p = filedialog.askopenfilename(parent=win, filetypes=[(M("exe_files"), "*.exe")])
            if p:
                self._add_prog(s, os.path.basename(p))
        m.add_command(label=M("prog_file"), command=browse)
        try:
            m.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height())
        finally:
            m.grab_release()

    def _add_prog(self, s, name):
        name = name.strip().lower()
        if name and name not in s["programs"]:
            s["programs"] = sorted(set(s["programs"]) | {name})
        self._rebuild()

    def _new_script(self):
        if self.draft is None:
            self.begin_edit()
        s = normalize({})
        s["name"] = M("default_name", len(self.draft) + 1)
        self.draft.append(s)
        self._rebuild()

    def _rename(self, s, current):
        v = self.app._ask_string(M("name"), M("name"), current)
        if v:
            s["name"] = v
            self._rebuild()

    def _delete(self, s, name):
        if self.app._ask_yesno(M("delete"), M("delete_q", name)):
            self.draft[:] = [x for x in self.draft if x["id"] != s["id"]]
            self._rebuild()

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
            self.log("macros tab rebuild:\n" + traceback.format_exc())

