# -*- coding: utf-8 -*-
"""Key scripts for HDContainer: a trigger key pressed in a chosen program is replaced by a
sequence of key presses with random pauses (e.g. E -> E, 2, E).

Rules of a script (settings["macros"]["scripts"], a list of dicts):
  * fires only while one of its programs (exe names) is in front; no programs = everywhere (1.17.1);
  * the trigger may carry Ctrl/Alt/Shift/Win, which must match exactly;
  * Enter pressed in a script's program pauses scripts until the next Enter (game chat: open /
    send), Esc ends the pause (chat closed); while paused the key goes through untouched;
  * each step = key down, hold, key up; the next key goes down «interval» after the previous
    one went down (press to press, as other macro tools count it); both random in their
    ranges with a human-like spread (human_ms);
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
    "toggle": {"en": "Hotkeys", "ru": "Горячие клавиши", "es": "Atajos", "pt": "Atalhos", "de": "Tastenkürzel", "fr": "Raccourcis", "zh": "快捷键"},
    "notify": {"en": "Show a notice when a script is turned on or off", "ru": "Показывать уведомление при включении и выключении скрипта", "es": "Mostrar un aviso al activar o desactivar un script", "pt": "Mostrar um aviso ao ligar ou desligar um script", "de": "Hinweis beim Ein- und Ausschalten eines Skripts zeigen", "fr": "Afficher un avis quand un script est activé ou désactivé", "zh": "开关脚本时显示通知"},
    "toggled_on": {"en": "Script on", "ru": "Скрипт включён", "es": "Script activado", "pt": "Script ligado", "de": "Skript an", "fr": "Script activé", "zh": "脚本已开启"},
    "toggled_off": {"en": "Script off", "ru": "Скрипт выключен", "es": "Script desactivado", "pt": "Script desligado", "de": "Skript aus", "fr": "Script désactivé", "zh": "脚本已关闭"},
    "not_set_pl": {"en": "not set", "ru": "не заданы", "es": "sin asignar", "pt": "não definidos", "de": "nicht gesetzt", "fr": "non définis", "zh": "未设置"},
    "toggle_same": {"en": "The hotkeys must differ from the script key.", "ru": "Горячие клавиши должны отличаться от клавиши скрипта.", "es": "Los atajos deben ser distintos de la tecla del script.", "pt": "Os atalhos devem ser diferentes da tecla do script.", "de": "Die Tastenkürzel müssen sich von der Skripttaste unterscheiden.", "fr": "Les raccourcis doivent différer de la touche du script.", "zh": "快捷键必须与脚本按键不同。"},
    "steps": {"en": "Presses", "ru": "Нажимает", "es": "Pulsa", "pt": "Pressiona", "de": "Drückt", "fr": "Appuie sur", "zh": "依次按下"},
    "add_key": {"en": "+ Key", "ru": "+ Клавиша", "es": "+ Tecla", "pt": "+ Tecla", "de": "+ Taste", "fr": "+ Touche", "zh": "+ 按键"},
    "between": {"en": "Between presses", "ru": "Между нажатиями", "es": "Entre pulsaciones", "pt": "Entre toques", "de": "Zwischen Tastendrücken", "fr": "Entre les appuis", "zh": "按键间隔"},
    "m_random": {"en": "Random", "ru": "Случайно", "es": "Aleatorio", "pt": "Aleatório", "de": "Zufällig", "fr": "Aléatoire", "zh": "随机"},
    "m_fixed": {"en": "Constant", "ru": "Постоянно", "es": "Constante", "pt": "Constante", "de": "Konstant", "fr": "Constant", "zh": "固定"},
    "m_auto": {"en": "Auto", "ru": "Авто", "es": "Auto", "pt": "Auto", "de": "Auto", "fr": "Auto", "zh": "自动"},
    "m_manual": {"en": "Manual", "ru": "Вручную", "es": "Manual", "pt": "Manual", "de": "Manuell", "fr": "Manuel", "zh": "手动"},
    "avg": {"en": "≈ %d ms on average", "ru": "≈ %d мс в среднем", "es": "≈ %d ms de media", "pt": "≈ %d ms em média", "de": "≈ %d ms im Schnitt", "fr": "≈ %d ms en moyenne", "zh": "平均 ≈ %d 毫秒"},
    "every": {"en": "every %d ms", "ru": "каждые %d мс", "es": "cada %d ms", "pt": "a cada %d ms", "de": "alle %d ms", "fr": "toutes les %d ms", "zh": "每 %d 毫秒"},
    "more": {"en": "More", "ru": "Подробнее", "es": "Más", "pt": "Mais", "de": "Mehr", "fr": "Plus", "zh": "更多"},
    "hold_auto_note": {"en": "about %d–%d ms, picked from the interval", "ru": "примерно %d–%d мс, подбирается по интервалу", "es": "unos %d–%d ms, según el intervalo", "pt": "cerca de %d–%d ms, conforme o intervalo", "de": "etwa %d–%d ms, passend zum Abstand", "fr": "environ %d–%d ms, selon l'intervalle", "zh": "约 %d–%d 毫秒，按间隔自动选择"},
    "r_fixed": {"en": "The same time every press is easy to spot as a robot", "ru": "Одинаковое время каждый раз легко распознать как робота", "es": "El mismo tiempo cada vez delata a un robot", "pt": "O mesmo tempo sempre denuncia um robô", "de": "Immer dieselbe Zeit verrät einen Roboter", "fr": "Le même temps à chaque fois trahit un robot", "zh": "每次时间都一样，很容易被识别为机器人"},
    "interval": {"en": "From press to press, ms", "ru": "От нажатия до нажатия, мс", "es": "De pulsación a pulsación, ms", "pt": "De toque a toque, ms", "de": "Von Druck zu Druck, ms", "fr": "D'appui à appui, ms", "zh": "两次按下间隔，毫秒"},
    "r_overlap": {"en": "The hold is longer than the interval: make the hold shorter than %d ms", "ru": "Удержание длиннее интервала: сделай удержание короче %d мс", "es": "La pulsación dura más que el intervalo: hazla menor de %d ms", "pt": "O toque dura mais que o intervalo: deixe-o abaixo de %d ms", "de": "Halten dauert länger als der Abstand: unter %d ms halten", "fr": "Le maintien dépasse l'intervalle : garde-le sous %d ms", "zh": "按住时间超过间隔：请短于 %d 毫秒"},
    "hold": {"en": "Key hold, ms", "ru": "Удержание клавиши, мс", "es": "Mantener tecla, ms", "pt": "Segurar tecla, ms", "de": "Taste halten, ms", "fr": "Maintien de la touche, ms", "zh": "按住时长，毫秒"},
    "programs": {"en": "Only in", "ru": "Только в программах", "es": "Solo en", "pt": "Só em", "de": "Nur in", "fr": "Seulement dans", "zh": "仅在以下程序中"},
    "add_prog": {"en": "+ Program", "ru": "+ Программа", "es": "+ Programa", "pt": "+ Programa", "de": "+ Programm", "fr": "+ Programme", "zh": "+ 程序"},
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
    "toggle_vk": 0, "toggle_mods": 0,   # on / off hotkey of the script, in its programs (0 = none)
    "steps": [],                    # [[mods, vk], ...]
    "interval_mode": "random",      # random: [interval_min, interval_max]; fixed: interval_fixed
    "interval_min": 120, "interval_max": 160,   # key down -> next key down
    "interval_fixed": 130,
    "hold_mode": "auto",            # auto: from the interval (auto_hold); manual: [hold_min, hold_max]
    "hold_min": 30, "hold_max": 70,
    "programs": [],                 # exe names, lower case
    "enter_block": True, "enter_s": 3.0,
}
VK_RETURN = 0x0D

# Human limits, ms: (red below, yellow below, yellow above, red above).
# A fast tap in a game combo is held ~30-80 ms (under ~15 ms is a machine); keys of a practised
# combo follow each other every ~80-300 ms from press to press, under ~50 ms is beyond human
# motor timing for different keys.
HUMAN = {
    "hold": (15, 30, 200, 500),
    "interval": (50, 80, 500, 1500),
}
MIN_GAP_MS = 8                      # a released key and the next press are never closer than this
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
    need = max(15, int(round(lo * 0.15 / 5.0)) * 5)      # practised combos vary by ~10-15 %
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
    out["toggle_vk"] = _int(out["toggle_vk"], 0, 255, 0)
    out["toggle_mods"] = _int(out["toggle_mods"], 0, 15, 0)
    steps = []
    for st in out["steps"] if isinstance(out["steps"], list) else []:
        try:
            m, v = int(st[0]), int(st[1])
            if 0 < v < 256:
                steps.append([m & 15, v])
        except Exception:
            pass
    out["steps"] = steps
    if isinstance(s, dict) and "interval_min" not in s and "delay_min" in s:
        # before 1.15.5 the pause ran from release to the next press: keep the same rhythm
        for a in ("min", "max"):
            out["interval_" + a] = _int(s.get("delay_" + a), 0, MS_MAX, 100) + _int(
                s.get("hold_" + a), 0, MS_MAX, 50)
    if isinstance(s, dict) and "hold_mode" not in s and ("hold_min" in s or "delay_min" in s):
        out["hold_mode"] = "manual"     # scripts from before the casual UI keep their own hold
    for k in ("interval_min", "interval_max", "interval_fixed", "hold_min", "hold_max"):
        out[k] = _int(out[k], 0, MS_MAX, DEFAULT_SCRIPT[k])
    out["interval_mode"] = out["interval_mode"] if out["interval_mode"] in ("random", "fixed") else "random"
    out["hold_mode"] = out["hold_mode"] if out["hold_mode"] in ("auto", "manual") else "auto"
    progs = out["programs"] if isinstance(out["programs"], list) else []
    out["programs"] = sorted({str(p).strip().lower() for p in progs if str(p).strip()})
    out["enter_block"] = bool(out["enter_block"])
    out["enter_s"] = _float(out["enter_s"], 0.0, 600.0, 3.0)
    return out


BETA_MEAN = 2.2 / (2.2 + 3.2)        # mean of human_ms as a share of the range


def mean_ms(lo, hi):
    lo, hi = sorted((lo, hi))
    return lo + (hi - lo) * BETA_MEAN


def interval_range(s):
    if s["interval_mode"] == "fixed":
        return s["interval_fixed"], s["interval_fixed"]
    return tuple(sorted((s["interval_min"], s["interval_max"])))


def auto_hold(imin):
    """Hold range picked from the shortest interval: a quick tap, always shorter than the interval."""
    hi = max(25, min(70, imin - MIN_GAP_MS - 20))
    return min(25, hi - 5), hi


def hold_range(s):
    if s["hold_mode"] == "auto":
        return auto_hold(interval_range(s)[0])
    return tuple(sorted((s["hold_min"], s["hold_max"])))


def rate_overlap(s):
    """(level, text) when the hold can outlast the press-to-press interval, else None."""
    lo = interval_range(s)[0]
    if max(s["hold_min"], s["hold_max"]) > lo - MIN_GAP_MS - 10:
        return "bad", M("r_overlap", max(0, lo - MIN_GAP_MS - 10))
    return None


def problems(s):
    """Why a script cannot run (list of short strings), empty when it can."""
    p = []
    if not s.get("trigger_vk"):
        p.append(M("w_trigger"))
    if not s.get("steps"):
        p.append(M("w_steps"))
    return p


class RangeSlider(tk.Canvas):
    """Stretchable window on a ms scale: drag an edge to stretch it, drag the middle to move it,
    the mouse wheel nudges the nearest edge by 1 ms. With k_lo None it is a single handle.
    Values are written into the dict d at once; on_change() is called after every change."""
    W, H, PAD, R = 230, 30, 9, 7

    def __init__(self, parent, th, vmin, vmax, d, k_lo, k_hi, on_change):
        super().__init__(parent, width=self.W, height=self.H, bg=th["bg"], highlightthickness=0, bd=0,
                         cursor="hand2")
        self.th, self.vmin, self.vmax, self.d = th, vmin, vmax, d
        self.k_lo, self.k_hi, self.on_change = k_lo, k_hi, on_change
        self.drag = None
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "drag", None))
        self.bind("<MouseWheel>", self._wheel)
        self._draw()

    def _x(self, v):
        span = self.W - 2 * self.PAD
        return self.PAD + span * (min(max(v, self.vmin), self.vmax) - self.vmin) / float(self.vmax - self.vmin)

    def _v(self, x):
        span = self.W - 2 * self.PAD
        return int(round(self.vmin + (x - self.PAD) * (self.vmax - self.vmin) / float(span)))

    def _vals(self):
        hi = self.d[self.k_hi]
        lo = self.d[self.k_lo] if self.k_lo else hi
        return lo, hi

    def _set(self, lo, hi):
        lo = max(self.vmin, min(self.vmax, lo))
        hi = max(self.vmin, min(self.vmax, hi))
        if self.k_lo:
            self.d[self.k_lo], self.d[self.k_hi] = min(lo, hi), max(lo, hi)
        else:
            self.d[self.k_hi] = hi
        self._draw()
        self.on_change()

    def _draw(self):
        th = self.th
        self.delete("all")
        cy = 19
        self.create_line(self.PAD, cy, self.W - self.PAD, cy, fill=th["sf2"], width=4, capstyle="round")
        lo, hi = self._vals()
        x0, x1 = self._x(lo), self._x(hi)
        self.create_line(x0, cy, x1, cy, fill=th["ac"], width=6, capstyle="round")
        handles = (x0, x1) if self.k_lo else (x1,)
        for x in handles:
            self.create_oval(x - self.R, cy - self.R, x + self.R, cy + self.R, fill="white", outline=th["ac"], width=2)
        f = ("Segoe UI", 8)
        if self.k_lo and x1 - x0 < 34:          # labels would overlap: one combined label
            self.create_text((x0 + x1) / 2, 5, text="%d–%d" % (lo, hi), fill=th["tx"], font=f)
        else:
            for x, v in zip(handles, (lo, hi) if self.k_lo else (hi,)):
                self.create_text(x, 5, text=str(v), fill=th["tx"], font=f)

    def _press(self, e):
        lo, hi = self._vals()
        x0, x1 = self._x(lo), self._x(hi)
        if not self.k_lo:
            self.drag = ("hi",)
            self._set(lo, self._v(e.x))
        elif abs(e.x - x0) <= self.R + 2 and abs(e.x - x0) <= abs(e.x - x1):
            self.drag = ("lo",)
        elif abs(e.x - x1) <= self.R + 2:
            self.drag = ("hi",)
        elif x0 < e.x < x1:
            self.drag = ("both", e.x, lo, hi)
        else:                                   # click on the track: the nearer edge jumps there
            self.drag = ("lo",) if abs(e.x - x0) < abs(e.x - x1) else ("hi",)
            self._move(e)

    def _move(self, e):
        if not self.drag:
            return
        lo, hi = self._vals()
        v = self._v(e.x)
        if self.drag[0] == "lo":
            self._set(min(v, hi), hi)
        elif self.drag[0] == "hi":
            self._set(lo, max(v, lo) if self.k_lo else v)
        else:
            _k, x_start, lo0, hi0 = self.drag
            dv = self._v(e.x) - self._v(x_start)
            dv = max(self.vmin - lo0, min(self.vmax - hi0, dv))
            self._set(lo0 + dv, hi0 + dv)

    def _wheel(self, e):
        step = 1 if e.delta > 0 else -1
        lo, hi = self._vals()
        if not self.k_lo:
            self._set(lo, hi + step)
        elif abs(e.x - self._x(lo)) <= abs(e.x - self._x(hi)):
            self._set(min(lo + step, hi), hi)
        else:
            self._set(lo, max(hi + step, lo))
        return "break"


class _Compiled:
    """Read-only view of an active script for the hook and the player threads."""
    __slots__ = ("id", "name", "vk", "mods", "steps", "imin", "imax", "hmin", "hmax", "programs",
                 "enter_block", "enter_s")

    def __init__(self, s):
        self.id, self.name = s["id"], s["name"]
        self.vk, self.mods = s["trigger_vk"], s["trigger_mods"]
        self.steps = tuple((m, v) for m, v in s["steps"])
        self.imin, self.imax = interval_range(s)
        self.hmin, self.hmax = hold_range(s)
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
        self.events = vs.WakeQueue()          # ("captured", mods, vk) / ("capture_cancel",)
        self.scripts = ()                     # tuple of _Compiled, replaced as a whole
        self.toggles = ()                     # ((vk, mods, programs, id), ...) of every script with an on/off key
        self.capture = False
        self.chat = False                     # Enter pressed in a script's program: paused until the next Enter
        self._down = set()
        self._swallowed = set()               # triggers whose key-up (and auto-repeat) we eat
        self._passing = {}                    # vk -> (script, t_down): the real press is the first step,
                                              # the rest plays after its release
        self._cap_single = None
        self._busy = False
        self._play_q = queue.Queue()
        self._hook = None
        self._on = False
        self._warned_uipi = False
        threading.Thread(target=self._player, daemon=True, name="hdc-macro-play").start()

    # ---- hook lifecycle (tk thread) ----
    @property
    def running(self):
        return self._on

    def set_scripts(self, compiled, toggles=()):
        self.scripts = tuple(compiled)
        self.toggles = tuple(toggles)
        self.sync()

    def sync(self):
        want = bool(self.scripts) or bool(self.toggles) or self.capture
        if want and not self.running:
            self._start()
        elif not want and self.running:
            self.stop()

    # 1.17.0: no hook of its own any more: the engine is the newest handler of the shared
    # keyboard hook (voice_sys.HUB), so it is asked first, as its own later-installed hook
    # was, and the hub ignores injected keys as this hook did.
    def _start(self):
        self._down.clear()
        self._swallowed.clear()
        self._passing.clear()
        self.chat = False
        self._hook = vs.HUB.add(self)
        self._on = True
        self.log("macros: hook %s, %d active script(s)" % ("on" if self._hook else "FAILED", len(self.scripts)))

    def stop(self):
        if not self._on:
            return
        vs.HUB.remove(self)
        self._on, self._hook = False, None
        self.log("macros: hook off")

    # ---- key handler (hook thread: no tk, answer fast) ----

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
                if any(not sc.programs or exe in sc.programs for sc in scripts):
                    self.chat = (not self.chat) if vk == VK_RETURN else False
            return False
        if vk in self._swallowed:
            if up:
                self._swallowed.discard(vk)
            return True                         # auto-repeat and release of a fired trigger
        if vk in self._passing:
            if up:                              # the real first press is over: play the rest
                s, t_down = self._passing.pop(vk)
                if not self._busy:
                    self._busy = True
                    self._play_q.put((s, 1, t_down, time.perf_counter()))
            return False                        # the real key (and its auto-repeat) goes through
        if up or repeat or vk in MODIFIER_VKS:
            return False
        toggles = self.toggles
        if toggles and any(t[0] == vk for t in toggles):
            mods = vs.current_mods()
            exe = None
            for tvk, tmods, progs, sid in toggles:
                if tvk != vk or tmods != mods:
                    continue
                if exe is None:
                    exe = (vs.foreground_exe() or "").lower()
                if progs and exe not in progs:
                    continue
                if self.chat and not tmods:
                    return False                # typing in the game chat: a plain key stays a letter
                self._swallowed.add(vk)
                self.events.put(("toggle", sid))
                return True
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
            if s.programs and exe not in s.programs:      # no programs = everywhere
                continue
            if s.enter_block and self.chat:
                return False                    # chat typing: the key goes through as it is
            if self._busy:                      # a press during playback is dropped, not queued
                self._swallowed.add(vk)
                return True
            if s.steps[0] == (s.mods, vk) and not s.mods:
                # first step = the trigger itself: let the real press through (real timing, real
                # hold), the other steps follow its release
                self._passing[vk] = (s, time.perf_counter())
                return False
            self._swallowed.add(vk)
            self._busy = True
            self._play_q.put((s, 0, None, None))
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
        return not s.programs or (vs.foreground_exe() or "").lower() in s.programs

    @staticmethod
    def _wait_until(t):
        d = t - time.perf_counter()
        if d > 0:
            time.sleep(d)

    def _play(self, s, first, t_down, t_up):
        """t_down / t_up: the real first press when it went through (first == 1)."""
        vs.winmm.timeBeginPeriod(1)             # 1 ms sleep resolution instead of ~15.6 ms
        try:
            if s.mods:
                # the user still holds the trigger's modifiers: release them logically, or
                # every step would arrive as Ctrl+<step>
                held = [v for v in _SIDED_MODS if vs.key_down(v)]
                _send([(v, True) for v in held])
            ok = True
            last_down, last_up = t_down, t_up
            for mods, vk in s.steps[first:]:
                if last_down is not None:
                    # next press: «interval» after the previous press, never before the previous
                    # key is up (+ a few ms, a finger needs that too)
                    gap = (MIN_GAP_MS + random.uniform(0, 6)) / 1000.0
                    self._wait_until(max(last_down + human_ms(s.imin, s.imax), last_up + gap))
                if not self._in_program(s):
                    self.log("macros: %s stopped, the program left the front" % s.name)
                    return
                mv = _mod_vks(mods)
                ok &= _send([(m, False) for m in mv] + [(vk, False)])
                last_down = time.perf_counter()
                time.sleep(human_ms(s.hmin, s.hmax))
                ok &= _send([(vk, True)] + [(m, True) for m in reversed(mv)])
                last_up = time.perf_counter()
            if not ok and not self._warned_uipi:
                self._warned_uipi = True
                self.log("macros: Windows dropped the keys (program runs as administrator?)")
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
        self._more_open = set()               # script ids whose «More» is unfolded
        self._hold_refresh = {}               # script id -> redraw of its hold rating
        self.engine = MacroEngine(log)
        self.apply()
        app.on_wake(self._loop)               # engine events wake it (no polling)

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
        saved = self.saved_scripts()
        active = [_Compiled(s) for s in saved if s["enabled"] and not problems(s)]
        toggles = [(s["toggle_vk"], s["toggle_mods"], frozenset(s["programs"]), s["id"])
                   for s in saved if s["toggle_vk"]]
        self.engine.set_scripts(active, toggles)

    def notify_on(self):
        raw = self.app.settings.get("macros", {})
        return bool(raw.get("notify", True)) if isinstance(raw, dict) else True

    def _save(self, lst, user=False, notify=None):
        d = {"scripts": [normalize(s) for s in lst],
             "notify": self.notify_on() if notify is None else bool(notify)}
        self.app._set_setting("macros", d, user=user)

    # ---- settings draft (see recorder.py) ----
    def begin_edit(self):
        self.draft = self.saved_scripts()
        self.draft_notify = self.notify_on()

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
        self._save(new, notify=getattr(self, "draft_notify", None))
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

    def _toggle(self, sid):
        """The on / off key of a script was pressed in one of its programs (or anywhere)."""
        lst = self.saved_scripts()
        s = self._find(lst, sid)
        if s is None:
            return
        on = not s["enabled"]
        self.set_enabled(sid, on)
        if not self.notify_on():
            return
        name = s["name"] or M("default_name", lst.index(s) + 1)
        try:
            self.app._notify(name, M("toggled_on" if on else "toggled_off"), kind="on" if on else "off")
        except Exception:
            pass

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
                elif ev[0] == "toggle":
                    self._toggle(ev[1])
        except Exception:
            self.log("macros loop:\n" + traceback.format_exc())

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
        if target[0] in ("trigger", "toggle"):
            if vk in MODIFIER_VKS:
                self.app._info(M("tab"), M("trig_bad"))
                return
            other = ("trigger_vk", "trigger_mods") if target[0] == "toggle" else ("toggle_vk", "toggle_mods")
            if s[other[0]] == vk and s[other[1]] == mods:
                self.app._info(M("tab"), M("toggle_same"))
                return
            s[target[0] + "_vk"], s[target[0] + "_mods"] = vk, mods
        else:
            s["steps"].append([mods, vk])
        self._rebuild()

    # ---- settings page ----
    def build_into(self, parent, win, pad):
        th = _theme()
        self._hold_refresh = {}
        self._tab = (parent, win, pad)
        if self.draft is None:
            self.begin_edit()
        tk.Frame(parent, bg=th["sf"], height=8).pack()
        top = tk.Frame(parent, bg=th["sf"])
        top.pack(fill="x", padx=pad, pady=(0, 8))
        self.app._accent_btn(top, M("new"), self._new_script).pack(side="left")
        self.app._setting_check(parent, M("notify"), getattr(self, "draft_notify", self.notify_on()),
                                lambda v: setattr(self, "draft_notify", bool(v)), pad)
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

        # timings: between presses (random window / constant), hold under «More»
        self._timing_block(card, cp, line, s, th)

        # programs
        r = line(M("programs"))
        for p in s["programs"]:
            chip(r, p, lambda p=p: (s["programs"].remove(p), self._rebuild()), mono=False)
        add = link(r, M("add_prog"), lambda: self._prog_menu(s, add, win))

        # hotkeys: turn the script on / off, in its programs (or anywhere without any)
        r = line(M("toggle"))
        if s["toggle_vk"]:
            chip(r, vs.hotkey_label(s["toggle_mods"], s["toggle_vk"]),
                 lambda: (s.__setitem__("toggle_vk", 0), s.__setitem__("toggle_mods", 0), self._rebuild()))
        else:
            chip(r, M("not_set_pl"), mono=False)
        link(r, M("change"), lambda: self._start_capture("toggle", s["id"]))

        # Enter rule
        r = tk.Frame(card, bg=bg)
        r.pack(fill="x", padx=cp, pady=(6, 4))
        chk2, _s2, _d2 = a._make_check(r, s["enter_block"], lambda v: s.__setitem__("enter_block", bool(v)), bg=bg)
        chk2.pack(side="left")
        tk.Label(r, text="  " + M("enter"), bg=bg, fg=th["tx"], font=th["font_sm"]).pack(side="left")

        # «More»: the key hold (auto by default)
        opened = s["id"] in self._more_open
        r = tk.Frame(card, bg=bg)
        r.pack(fill="x", padx=cp, pady=(6, 0))
        more = tk.Label(r, text=M("more") + ("  ⌃" if opened else "  ⌄"), bg=bg, fg=th["dim"], font=th["font_sm"],
                        cursor="hand2")
        more.pack(side="left")
        more.bind("<Button-1>", lambda _e: (self._more_open.symmetric_difference_update({s["id"]}), self._rebuild()))
        if opened:
            self._hold_block(card, cp, line, s, th)

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

    def _pills(self, parent, options, cur, on_pick, th, bg):
        row = tk.Frame(parent, bg=bg)
        row.pack(side="left")
        for val, label in options:
            on = val == cur
            p = tk.Label(row, text=label, bg=th["ac"] if on else th["sf2"], fg="white" if on else th["tx"],
                         font=th["font_sm"], padx=10, pady=3, cursor="hand2")
            p.pack(side="left", padx=(0, 4))
            p.bind("<Button-1>", lambda _e, v=val: on_pick(v))
        return row

    def _status(self, card, cp, th):
        """A rating line under a slider: (dot, text label, frame); hidden while the rating is green."""
        bg = th["bg"]
        st = tk.Frame(card, bg=bg)
        dot = tk.Label(st, text="●", bg=bg, font=("Segoe UI", 8))
        dot.pack(side="left", anchor="n", pady=(2, 0))
        txt = tk.Label(st, text="", bg=bg, font=th["font_sm"], anchor="w", justify="left", wraplength=340)
        txt.pack(side="left", padx=(5, 0))
        anchor = tk.Frame(card, bg=bg, height=0)
        anchor.pack(fill="x")

        def show(level, text):
            col = RATE_COLORS[level] or th["err"]
            dot.configure(fg=col)
            txt.configure(text=text, fg=col)
            if level == "ok":
                st.pack_forget()
            else:
                st.pack(fill="x", padx=(cp + LABEL_W, cp), pady=(0, 2), after=anchor)
        return show

    def _timing_block(self, card, cp, line, s, th):
        bg = th["bg"]
        r = line(M("between"))
        fixed = s["interval_mode"] == "fixed"
        self._pills(r, (("random", M("m_random")), ("fixed", M("m_fixed"))), s["interval_mode"],
                    lambda v: (s.__setitem__("interval_mode", v), self._rebuild()), th, bg)
        r2 = tk.Frame(card, bg=bg)
        r2.pack(fill="x", padx=(cp + LABEL_W, cp), pady=(2, 0))
        avg = tk.Label(r2, text="", bg=bg, font=th["font_sm"], anchor="w")
        status = self._status(card, cp, th)

        def changed():
            lo, hi = interval_range(s)
            if fixed:
                level, text = "warn", M("r_fixed")
                avg.configure(text=M("every", lo), fg=th["tx"])
            else:
                level, text = rate("interval", lo, hi)
                avg.configure(text=M("avg", round(mean_ms(lo, hi))), fg=RATE_COLORS[level] or th["err"])
            if level != "bad" and s["hold_mode"] == "manual":
                level, text = rate_overlap(s) or (level, text)
            status(level, text)
            hook = self._hold_refresh.get(s["id"])
            if hook:
                hook()
        if fixed:
            RangeSlider(r2, th, 60, 300, s, None, "interval_fixed", changed).pack(side="left")
        else:
            RangeSlider(r2, th, 60, 300, s, "interval_min", "interval_max", changed).pack(side="left")
        avg.pack(side="left", padx=(10, 0))
        changed()

    def _hold_block(self, card, cp, line, s, th):
        bg = th["bg"]
        r = line(M("hold"))
        self._pills(r, (("auto", M("m_auto")), ("manual", M("m_manual"))), s["hold_mode"],
                    lambda v: (s.__setitem__("hold_mode", v), self._rebuild()), th, bg)
        r2 = tk.Frame(card, bg=bg)
        r2.pack(fill="x", padx=(cp + LABEL_W, cp), pady=(2, 0))
        note = tk.Label(r2, text="", bg=bg, font=th["font_sm"], anchor="w")
        status = self._status(card, cp, th)

        def refresh():
            lo, hi = hold_range(s)
            if s["hold_mode"] == "auto":
                note.configure(text=M("hold_auto_note", lo, hi), fg=th["dim"])
                status("ok", "")
                return
            level, text = rate("hold", lo, hi)
            if level != "bad":
                level, text = rate_overlap(s) or (level, text)
            note.configure(text=M("avg", round(mean_ms(lo, hi))), fg=RATE_COLORS[level] or th["err"])
            status(level, text)
        self._hold_refresh[s["id"]] = refresh
        if s["hold_mode"] == "manual":
            RangeSlider(r2, th, 10, 200, s, "hold_min", "hold_max", refresh).pack(side="left")
            note.pack(side="left", padx=(10, 0))
        else:
            note.pack(side="left")
        refresh()

    def _prog_menu(self, s, anchor, win):
        """Program picker of the app: running programs first, then installed ones, or an .exe file."""
        exe = self.app._pick_program(s["programs"])
        if exe:
            self._add_prog(s, exe)

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

