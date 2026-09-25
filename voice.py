# -*- coding: utf-8 -*-
"""
Голосовой ввод HDContainer.

Держишь горячую клавишу (по умолчанию Shift + Num +) — над часами появляется
анимированный индикатор, HDContainer слушает микрофон. Отпустил — речь
распознаётся (Whisper) и отдаётся LLM:

  * ничего не выделено  -> диктовка: текст причёсывается (пунктуация, ошибки
    распознавания, слова-паразиты) и вставляется в поле ввода под курсором,
    а если курсор не в поле — кладётся в буфер обмена;
  * выделен текст       -> сказанное — это инструкция к выделенному
    («переведи», «причеши», «проверь на ошибки»); результат заменяет выделение
    (или уходит в буфер). Если это вопрос о тексте — ответ идёт в буфер.

Модули: voice_sys (Win32: микрофон, буфер, клавиши, UI Automation),
voice_engines (Whisper, Claude Code / Anthropic API / OpenAI-совместимые).
"""

import ctypes
import math
import os
import queue
import threading
import time
import traceback
from ctypes import wintypes

import tkinter as tk

import voice_engines as ve
import voice_sys as vs

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

WM_HOTKEY = 0x0312
HOTKEY_ID = 0xB0CE

# ---------------------------------------------------------------------------
#  Строки (7 языков, как в основном приложении)
# ---------------------------------------------------------------------------
VSTR = {
    "menu": {"en": "Voice input", "ru": "Голосовой ввод", "es": "Entrada por voz", "pt": "Entrada por voz", "de": "Spracheingabe", "fr": "Saisie vocale", "zh": "语音输入"},
    "title": {"en": "Voice input", "ru": "Голосовой ввод", "es": "Entrada por voz", "pt": "Entrada por voz", "de": "Spracheingabe", "fr": "Saisie vocale", "zh": "语音输入"},
    "intro": {
        "en": "Hold the hotkey and speak. Nothing selected — your speech becomes clean text: it is typed into the field under the cursor, or copied to the clipboard. Text selected — what you say is an instruction for it: “translate to English”, “tidy up”, “check for mistakes”.",
        "ru": "Зажми горячую клавишу и говори. Ничего не выделено — речь превращается в чистый текст: он вставляется в поле под курсором или копируется в буфер. Выделен текст — сказанное становится инструкцией к нему: «переведи на английский», «причеши», «проверь на ошибки».",
        "es": "Mantén la tecla y habla. Sin selección — tu voz se convierte en texto limpio: se escribe en el campo bajo el cursor o se copia al portapapeles. Con texto seleccionado — lo que dices es una instrucción: «traduce al inglés», «pule el texto», «revisa errores».",
        "pt": "Segure o atalho e fale. Sem seleção — sua fala vira texto limpo: é digitado no campo sob o cursor ou copiado para a área de transferência. Com texto selecionado — o que você diz é uma instrução: “traduza para o inglês”, “arrume o texto”, “verifique erros”.",
        "de": "Halte den Hotkey gedrückt und sprich. Nichts markiert — deine Sprache wird zu sauberem Text: er wird ins Feld unter dem Cursor getippt oder in die Zwischenablage kopiert. Text markiert — das Gesagte ist eine Anweisung dafür: „ins Englische übersetzen“, „glätten“, „auf Fehler prüfen“.",
        "fr": "Maintiens le raccourci et parle. Rien de sélectionné — ta voix devient un texte propre : il est saisi dans le champ sous le curseur ou copié dans le presse-papiers. Texte sélectionné — ce que tu dis est une consigne : « traduis en anglais », « mets au propre », « vérifie les fautes ».",
        "zh": "按住快捷键说话。未选中文本——语音变成整洁的文字：输入到光标所在的输入框，或复制到剪贴板。选中了文本——你说的话就是对它的指令：“翻译成英文”“润色”“检查错误”。"},
    "enable": {"en": "Enable voice input", "ru": "Включить голосовой ввод", "es": "Activar entrada por voz", "pt": "Ativar entrada por voz", "de": "Spracheingabe aktivieren", "fr": "Activer la saisie vocale", "zh": "启用语音输入"},
    "sec_hotkey": {"en": "Hotkey", "ru": "Горячая клавиша", "es": "Atajo", "pt": "Atalho", "de": "Hotkey", "fr": "Raccourci", "zh": "快捷键"},
    "change": {"en": "Change…", "ru": "Изменить…", "es": "Cambiar…", "pt": "Alterar…", "de": "Ändern…", "fr": "Modifier…", "zh": "更改…"},
    "press_combo": {"en": "Press the new key combination,\nor press and release a single key like Right Ctrl…\nEsc — cancel", "ru": "Нажми новое сочетание клавиш\nили нажми и отпусти одну клавишу, например правый Ctrl…\nEsc — отмена", "es": "Pulsa la nueva combinación\no pulsa y suelta una sola tecla, como Ctrl derecho…\nEsc — cancelar", "pt": "Pressione a nova combinação\nou pressione e solte uma tecla, como Ctrl direito…\nEsc — cancelar", "de": "Drücke die neue Tastenkombination\noder drücke und löse eine einzelne Taste wie Strg rechts…\nEsc — Abbrechen", "fr": "Appuie sur la nouvelle combinaison\nou appuie et relâche une seule touche, comme Ctrl droit…\nÉchap — annuler", "zh": "请按下新的组合键，\n或按下并松开单个键（如右 Ctrl）…\nEsc — 取消"},
    "llm_remote": {"en": "Claude on another computer — via HDContainer on the PC that has Claude Code", "ru": "Claude на другом компьютере — через HDContainer на ПК, где есть Claude Code", "es": "Claude en otro equipo — a través de HDContainer en el PC que tiene Claude Code", "pt": "Claude em outro computador — via HDContainer no PC que tem o Claude Code", "de": "Claude auf einem anderen Computer — über HDContainer auf dem PC mit Claude Code", "fr": "Claude sur un autre ordinateur — via HDContainer sur le PC qui a Claude Code", "zh": "另一台电脑上的 Claude — 通过装有 Claude Code 的电脑上的 HDContainer"},
    "remote_hint": {"en": "On the PC with Claude Code open Voice input → “Share Claude Code with other computers”, turn it on, and copy the address and key here.", "ru": "На ПК с Claude Code открой «Голосовой ввод» → «Поделиться Claude Code с другими компьютерами», включи и скопируй сюда адрес и ключ.", "es": "En el PC con Claude Code abre Entrada por voz → «Compartir Claude Code con otros equipos», actívalo y copia aquí la dirección y la clave.", "pt": "No PC com o Claude Code abra Entrada por voz → “Compartilhar o Claude Code com outros computadores”, ative e copie aqui o endereço e a chave.", "de": "Öffne auf dem PC mit Claude Code Spracheingabe → „Claude Code mit anderen Computern teilen“, schalte es ein und kopiere Adresse und Schlüssel hierher.", "fr": "Sur le PC qui a Claude Code, ouvre Saisie vocale → « Partager Claude Code avec d’autres ordinateurs », active-le et copie ici l’adresse et la clé.", "zh": "在装有 Claude Code 的电脑上打开“语音输入 → 与其他电脑共享 Claude Code”，开启后把地址和密钥复制到这里。"},
    "remote_url": {"en": "Address (e.g. 100.112.15.42 or http://100.112.15.42:8765/v1)", "ru": "Адрес (например, 100.112.15.42 или http://100.112.15.42:8765/v1)", "es": "Dirección (p. ej. 100.112.15.42 o http://100.112.15.42:8765/v1)", "pt": "Endereço (ex.: 100.112.15.42 ou http://100.112.15.42:8765/v1)", "de": "Adresse (z. B. 100.112.15.42 oder http://100.112.15.42:8765/v1)", "fr": "Adresse (ex. 100.112.15.42 ou http://100.112.15.42:8765/v1)", "zh": "地址（例如 100.112.15.42 或 http://100.112.15.42:8765/v1）"},
    "pan_local": {"en": "Local Whisper settings", "ru": "Настройки локального Whisper", "es": "Ajustes de Whisper local", "pt": "Configurações do Whisper local", "de": "Einstellungen für lokales Whisper", "fr": "Réglages de Whisper local", "zh": "本地 Whisper 设置"},
    "pan_cloud": {"en": "Cloud recognition settings", "ru": "Настройки облачного распознавания", "es": "Ajustes del reconocimiento en la nube", "pt": "Configurações do reconhecimento na nuvem", "de": "Einstellungen der Cloud-Erkennung", "fr": "Réglages de la reconnaissance cloud", "zh": "云端识别设置"},
    "pan_cc": {"en": "Claude Code on this PC", "ru": "Claude Code на этом ПК", "es": "Claude Code en este PC", "pt": "Claude Code neste PC", "de": "Claude Code auf diesem PC", "fr": "Claude Code sur ce PC", "zh": "本机上的 Claude Code"},
    "pan_remote": {"en": "Connection to the other computer", "ru": "Подключение к другому компьютеру", "es": "Conexión con el otro equipo", "pt": "Conexão com o outro computador", "de": "Verbindung zum anderen Computer", "fr": "Connexion à l’autre ordinateur", "zh": "连接到另一台电脑"},
    "pan_oai": {"en": "OpenAI-compatible service", "ru": "OpenAI-совместимый сервис", "es": "Servicio compatible con OpenAI", "pt": "Serviço compatível com OpenAI", "de": "OpenAI-kompatibler Dienst", "fr": "Service compatible OpenAI", "zh": "兼容 OpenAI 的服务"},
    "share_panel": {"en": "Give these to the other computer", "ru": "Это нужно ввести на другом компьютере", "es": "Introduce esto en el otro equipo", "pt": "Informe isto no outro computador", "de": "Dies auf dem anderen Computer eingeben", "fr": "À saisir sur l’autre ordinateur", "zh": "在另一台电脑上填写以下信息"},
    "share_pending": {"en": "Press Save to start sharing.", "ru": "Нажми «Сохранить», чтобы включить.", "es": "Pulsa Guardar para activarlo.", "pt": "Clique em Salvar para ativar.", "de": "Zum Aktivieren auf Speichern klicken.", "fr": "Clique sur Enregistrer pour l’activer.", "zh": "点击“保存”以启用。"},
    "share_none": {"en": "Claude Code isn’t installed on this PC, so there is nothing to share. Turn this off here and choose “Claude on another computer” above.", "ru": "На этом ПК нет Claude Code — делиться нечем. Выключи это здесь и выбери выше «Claude на другом компьютере».", "es": "Este PC no tiene Claude Code, no hay nada que compartir. Desactívalo aquí y elige arriba «Claude en otro equipo».", "pt": "Este PC não tem o Claude Code, não há o que compartilhar. Desative aqui e escolha acima “Claude em outro computador”.", "de": "Auf diesem PC ist kein Claude Code installiert – nichts zu teilen. Schalte es hier aus und wähle oben „Claude auf einem anderen Computer“.", "fr": "Claude Code n’est pas installé sur ce PC : rien à partager. Désactive-le ici et choisis plus haut « Claude sur un autre ordinateur ».", "zh": "本机未安装 Claude Code，无可共享。请在此关闭，并在上方选择“另一台电脑上的 Claude”。"},
    "o_need_model": {"en": "Speech model isn’t downloaded yet", "ru": "Модель распознавания ещё не скачана", "es": "El modelo de voz aún no está descargado", "pt": "O modelo de fala ainda não foi baixado", "de": "Das Sprachmodell ist noch nicht heruntergeladen", "fr": "Le modèle vocal n’est pas encore téléchargé", "zh": "语音模型尚未下载"},
    "o_need_llm": {"en": "Text processing isn’t set up", "ru": "Не настроена обработка текста", "es": "El procesamiento de texto no está configurado", "pt": "O processamento de texto não está configurado", "de": "Textverarbeitung ist nicht eingerichtet", "fr": "Le traitement du texte n’est pas configuré", "zh": "尚未设置文本处理"},
    "sec_share": {"en": "Share Claude Code with other computers", "ru": "Поделиться Claude Code с другими компьютерами", "es": "Compartir Claude Code con otros equipos", "pt": "Compartilhar o Claude Code com outros computadores", "de": "Claude Code mit anderen Computern teilen", "fr": "Partager Claude Code avec d’autres ordinateurs", "zh": "与其他电脑共享 Claude Code"},
    "share_desc": {"en": "This PC processes voice text for your other computers with its Claude Code, so they need neither Claude Code nor an API key. On the other PC choose “Claude on another computer” and paste the address and key shown here. Works over Tailscale or a home network.", "ru": "Этот ПК обрабатывает текст голосового ввода для других ваших компьютеров своим Claude Code — там не нужны ни Claude Code, ни API-ключ. На другом ПК выбери «Claude на другом компьютере» и вставь адрес и ключ отсюда. Работает через Tailscale или домашнюю сеть.", "es": "Este PC procesa el texto de voz de tus otros equipos con su Claude Code; allí no hace falta Claude Code ni clave API. En el otro PC elige «Claude en otro equipo» y pega la dirección y la clave de aquí. Funciona por Tailscale o red doméstica.", "pt": "Este PC processa o texto de voz dos seus outros computadores com o Claude Code dele; lá não é preciso Claude Code nem chave de API. No outro PC escolha “Claude em outro computador” e cole o endereço e a chave daqui. Funciona via Tailscale ou rede doméstica.", "de": "Dieser PC verarbeitet den Sprachtext deiner anderen Computer mit seinem Claude Code – dort brauchst du weder Claude Code noch API-Schlüssel. Wähle auf dem anderen PC „Claude auf einem anderen Computer“ und füge Adresse und Schlüssel von hier ein. Funktioniert über Tailscale oder das Heimnetz.", "fr": "Ce PC traite le texte vocal de tes autres ordinateurs avec son Claude Code : pas besoin de Claude Code ni de clé API là-bas. Sur l’autre PC, choisis « Claude sur un autre ordinateur » et colle l’adresse et la clé d’ici. Fonctionne via Tailscale ou le réseau domestique.", "zh": "这台电脑用自己的 Claude Code 为你的其他电脑处理语音文本，那些电脑无需 Claude Code 或 API 密钥。在另一台电脑上选择“另一台电脑上的 Claude”，并粘贴这里的地址和密钥。可通过 Tailscale 或家庭网络使用。"},
    "share_enable": {"en": "Accept requests from other computers", "ru": "Принимать запросы с других компьютеров", "es": "Aceptar solicitudes de otros equipos", "pt": "Aceitar pedidos de outros computadores", "de": "Anfragen von anderen Computern annehmen", "fr": "Accepter les requêtes d’autres ordinateurs", "zh": "接受其他电脑的请求"},
    "share_on": {"en": "Listening on port %d", "ru": "Слушаю порт %d", "es": "Escuchando en el puerto %d", "pt": "Escutando na porta %d", "de": "Lausche auf Port %d", "fr": "À l’écoute sur le port %d", "zh": "正在监听端口 %d"},
    "share_err": {"en": "Can’t open port %d: %s", "ru": "Не удалось открыть порт %d: %s", "es": "No se pudo abrir el puerto %d: %s", "pt": "Não foi possível abrir a porta %d: %s", "de": "Port %d kann nicht geöffnet werden: %s", "fr": "Impossible d’ouvrir le port %d : %s", "zh": "无法打开端口 %d：%s"},
    "share_port": {"en": "Port", "ru": "Порт", "es": "Puerto", "pt": "Porta", "de": "Port", "fr": "Port", "zh": "端口"},
    "share_addr": {"en": "Address for the other PC", "ru": "Адрес для другого ПК", "es": "Dirección para el otro PC", "pt": "Endereço para o outro PC", "de": "Adresse für den anderen PC", "fr": "Adresse pour l’autre PC", "zh": "供另一台电脑使用的地址"},
    "share_key": {"en": "Key", "ru": "Ключ", "es": "Clave", "pt": "Chave", "de": "Schlüssel", "fr": "Clé", "zh": "密钥"},
    "share_newkey": {"en": "New key", "ru": "Новый ключ", "es": "Nueva clave", "pt": "Nova chave", "de": "Neuer Schlüssel", "fr": "Nouvelle clé", "zh": "新密钥"},
    "copy": {"en": "Copy", "ru": "Копировать", "es": "Copiar", "pt": "Copiar", "de": "Kopieren", "fr": "Copier", "zh": "复制"},
    "copied": {"en": "Copied", "ru": "Скопировано", "es": "Copiado", "pt": "Copiado", "de": "Kopiert", "fr": "Copié", "zh": "已复制"},
    "share_client": {"en": "Paste the address and key shown in Voice input → “Share Claude Code” on the PC that has Claude Code.", "ru": "Вставь адрес и ключ из раздела «Поделиться Claude Code» в голосовом вводе на том ПК, где есть Claude Code.", "es": "Pega la dirección y la clave de Entrada por voz → «Compartir Claude Code» del PC que tiene Claude Code.", "pt": "Cole o endereço e a chave de Entrada por voz → “Compartilhar o Claude Code” do PC que tem o Claude Code.", "de": "Füge Adresse und Schlüssel aus Spracheingabe → „Claude Code teilen“ des PCs mit Claude Code ein.", "fr": "Colle l’adresse et la clé de Saisie vocale → « Partager Claude Code » du PC qui a Claude Code.", "zh": "粘贴装有 Claude Code 的那台电脑上“语音输入 → 共享 Claude Code”中显示的地址和密钥。"},
    "hk_single_note": {"en": "Hold it and talk. Pressed together with another key it works as usual (e.g. Ctrl+C) and no recording starts.", "ru": "Держишь — идёт запись. Нажатая вместе с другой клавишей работает как обычно (например, Ctrl+C), запись не начинается.", "es": "Mantenla pulsada y habla. Junto con otra tecla funciona como siempre (p. ej. Ctrl+C) y no graba.", "pt": "Segure e fale. Junto com outra tecla funciona normalmente (ex.: Ctrl+C) e não grava.", "de": "Gedrückt halten und sprechen. Zusammen mit einer anderen Taste wirkt sie wie gewohnt (z. B. Strg+C), es wird nicht aufgenommen.", "fr": "Maintiens-la et parle. Avec une autre touche elle fonctionne normalement (ex. Ctrl+C), sans enregistrement.", "zh": "按住即录音。与其他键一起按时照常工作（例如 Ctrl+C），不会开始录音。"},
    "hk_busy": {"en": "This combination is already used by another program", "ru": "Это сочетание уже занято другой программой", "es": "Otra aplicación ya usa esta combinación", "pt": "Outro programa já usa esta combinação", "de": "Diese Kombination wird bereits von einem anderen Programm verwendet", "fr": "Cette combinaison est déjà utilisée par un autre programme", "zh": "该组合键已被其他程序占用"},
    "mode_hold": {"en": "Hold to talk", "ru": "Удерживать и говорить", "es": "Mantener para hablar", "pt": "Segurar para falar", "de": "Halten zum Sprechen", "fr": "Maintenir pour parler", "zh": "按住说话"},
    "mode_toggle": {"en": "Press to start, press again to finish", "ru": "Нажал — говоришь, нажал ещё раз — готово", "es": "Pulsa para empezar, otra vez para terminar", "pt": "Pressione para começar e de novo para terminar", "de": "Drücken zum Starten, erneut zum Beenden", "fr": "Appuie pour commencer, encore pour terminer", "zh": "按一次开始，再按一次结束"},
    "sec_mic": {"en": "Microphone", "ru": "Микрофон", "es": "Micrófono", "pt": "Microfone", "de": "Mikrofon", "fr": "Microphone", "zh": "麦克风"},
    "mic_default": {"en": "System default", "ru": "Системный по умолчанию", "es": "Predeterminado del sistema", "pt": "Padrão do sistema", "de": "Systemstandard", "fr": "Par défaut du système", "zh": "系统默认"},
    "sec_stt": {"en": "Speech recognition", "ru": "Распознавание речи", "es": "Reconocimiento de voz", "pt": "Reconhecimento de fala", "de": "Spracherkennung", "fr": "Reconnaissance vocale", "zh": "语音识别"},
    "stt_local": {"en": "Local Whisper — offline, free, private", "ru": "Локальный Whisper — офлайн, бесплатно, приватно", "es": "Whisper local — sin conexión, gratis, privado", "pt": "Whisper local — offline, grátis, privado", "de": "Lokales Whisper — offline, kostenlos, privat", "fr": "Whisper local — hors ligne, gratuit, privé", "zh": "本地 Whisper — 离线、免费、私密"},
    "stt_cloud": {"en": "Cloud (OpenAI-compatible: Groq, OpenAI…)", "ru": "Облако (OpenAI-совместимое: Groq, OpenAI…)", "es": "Nube (compatible con OpenAI: Groq, OpenAI…)", "pt": "Nuvem (compatível com OpenAI: Groq, OpenAI…)", "de": "Cloud (OpenAI-kompatibel: Groq, OpenAI…)", "fr": "Cloud (compatible OpenAI : Groq, OpenAI…)", "zh": "云端（兼容 OpenAI：Groq、OpenAI…）"},
    "model": {"en": "Model", "ru": "Модель", "es": "Modelo", "pt": "Modelo", "de": "Modell", "fr": "Modèle", "zh": "模型"},
    "m_base": {"en": "base — fastest, rougher", "ru": "base — самая быстрая, грубее", "es": "base — la más rápida, menos precisa", "pt": "base — mais rápido, menos preciso", "de": "base — am schnellsten, ungenauer", "fr": "base — la plus rapide, moins précise", "zh": "base — 最快，精度较低"},
    "m_small": {"en": "small — balanced (recommended)", "ru": "small — баланс (рекомендуется)", "es": "small — equilibrado (recomendado)", "pt": "small — equilibrado (recomendado)", "de": "small — ausgewogen (empfohlen)", "fr": "small — équilibré (recommandé)", "zh": "small — 均衡（推荐）"},
    "m_turbo": {"en": "large-v3-turbo — most accurate, slow on weak CPUs", "ru": "large-v3-turbo — точнее всех, медленно на слабых CPU", "es": "large-v3-turbo — la más precisa, lenta en CPU débiles", "pt": "large-v3-turbo — mais precisa, lenta em CPUs fracas", "de": "large-v3-turbo — am genauesten, langsam auf schwachen CPUs", "fr": "large-v3-turbo — la plus précise, lente sur petit CPU", "zh": "large-v3-turbo — 最准确，弱 CPU 上较慢"},
    "lang": {"en": "Speech language", "ru": "Язык речи", "es": "Idioma del habla", "pt": "Idioma da fala", "de": "Sprache", "fr": "Langue parlée", "zh": "语音语言"},
    "lang_auto": {"en": "Auto-detect", "ru": "Определять автоматически", "es": "Detectar automáticamente", "pt": "Detectar automaticamente", "de": "Automatisch erkennen", "fr": "Détection automatique", "zh": "自动检测"},
    "installed": {"en": "Downloaded and ready", "ru": "Скачана и готова", "es": "Descargado y listo", "pt": "Baixado e pronto", "de": "Heruntergeladen und bereit", "fr": "Téléchargé et prêt", "zh": "已下载，可以使用"},
    "not_installed": {"en": "Not downloaded yet (%d MB)", "ru": "Ещё не скачана (%d МБ)", "es": "Aún no descargado (%d MB)", "pt": "Ainda não baixado (%d MB)", "de": "Noch nicht heruntergeladen (%d MB)", "fr": "Pas encore téléchargé (%d Mo)", "zh": "尚未下载（%d MB）"},
    "download": {"en": "Download", "ru": "Скачать", "es": "Descargar", "pt": "Baixar", "de": "Herunterladen", "fr": "Télécharger", "zh": "下载"},
    "downloading": {"en": "Downloading… %d%%", "ru": "Скачиваю… %d%%", "es": "Descargando… %d%%", "pt": "Baixando… %d%%", "de": "Wird heruntergeladen… %d%%", "fr": "Téléchargement… %d%%", "zh": "正在下载… %d%%"},
    "delete_model": {"en": "Delete", "ru": "Удалить", "es": "Eliminar", "pt": "Excluir", "de": "Löschen", "fr": "Supprimer", "zh": "删除"},
    "preset": {"en": "Service", "ru": "Сервис", "es": "Servicio", "pt": "Serviço", "de": "Dienst", "fr": "Service", "zh": "服务"},
    "url": {"en": "API URL", "ru": "Адрес API", "es": "URL de la API", "pt": "URL da API", "de": "API-URL", "fr": "URL de l’API", "zh": "API 地址"},
    "key": {"en": "API key", "ru": "API-ключ", "es": "Clave API", "pt": "Chave de API", "de": "API-Schlüssel", "fr": "Clé API", "zh": "API 密钥"},
    "vocab": {"en": "Vocabulary hint (names, terms)", "ru": "Подсказка словаря (имена, термины)", "es": "Vocabulario (nombres, términos)", "pt": "Vocabulário (nomes, termos)", "de": "Vokabular-Hinweis (Namen, Begriffe)", "fr": "Vocabulaire (noms, termes)", "zh": "词汇提示（人名、术语）"},
    "sec_llm": {"en": "Text processing (LLM)", "ru": "Обработка текста (LLM)", "es": "Procesamiento de texto (LLM)", "pt": "Processamento de texto (LLM)", "de": "Textverarbeitung (LLM)", "fr": "Traitement du texte (LLM)", "zh": "文本处理（LLM）"},
    "llm_cc": {"en": "Claude Code — your Claude subscription, no API key", "ru": "Claude Code — подписка Claude, без API-ключа", "es": "Claude Code — tu suscripción de Claude, sin clave API", "pt": "Claude Code — sua assinatura Claude, sem chave de API", "de": "Claude Code — dein Claude-Abo, ohne API-Schlüssel", "fr": "Claude Code — ton abonnement Claude, sans clé API", "zh": "Claude Code — 使用 Claude 订阅，无需 API 密钥"},
    "llm_api": {"en": "Anthropic API key", "ru": "Ключ Anthropic API", "es": "Clave de la API de Anthropic", "pt": "Chave da API da Anthropic", "de": "Anthropic-API-Schlüssel", "fr": "Clé API Anthropic", "zh": "Anthropic API 密钥"},
    "llm_oai": {"en": "OpenAI-compatible — OpenAI, OpenRouter, Gemini, Groq, DeepSeek, Ollama, LM Studio…", "ru": "OpenAI-совместимые — OpenAI, OpenRouter, Gemini, Groq, DeepSeek, Ollama, LM Studio…", "es": "Compatible con OpenAI — OpenAI, OpenRouter, Gemini, Groq, DeepSeek, Ollama, LM Studio…", "pt": "Compatível com OpenAI — OpenAI, OpenRouter, Gemini, Groq, DeepSeek, Ollama, LM Studio…", "de": "OpenAI-kompatibel — OpenAI, OpenRouter, Gemini, Groq, DeepSeek, Ollama, LM Studio…", "fr": "Compatible OpenAI — OpenAI, OpenRouter, Gemini, Groq, DeepSeek, Ollama, LM Studio…", "zh": "兼容 OpenAI — OpenAI、OpenRouter、Gemini、Groq、DeepSeek、Ollama、LM Studio…"},
    "cc_path": {"en": "claude.exe (empty — find automatically)", "ru": "claude.exe (пусто — найти автоматически)", "es": "claude.exe (vacío — buscar automáticamente)", "pt": "claude.exe (vazio — localizar automaticamente)", "de": "claude.exe (leer — automatisch suchen)", "fr": "claude.exe (vide — recherche automatique)", "zh": "claude.exe（留空则自动查找）"},
    "cc_found": {"en": "Found: %s", "ru": "Найден: %s", "es": "Encontrado: %s", "pt": "Encontrado: %s", "de": "Gefunden: %s", "fr": "Trouvé : %s", "zh": "已找到：%s"},
    "cc_missing": {"en": "Claude Code not found. Install it (claude.com/claude-code) and run `claude` once to sign in.", "ru": "Claude Code не найден. Установи его (claude.com/claude-code) и один раз запусти `claude`, чтобы войти.", "es": "No se encontró Claude Code. Instálalo (claude.com/claude-code) y ejecuta `claude` una vez para iniciar sesión.", "pt": "Claude Code não encontrado. Instale-o (claude.com/claude-code) e execute `claude` uma vez para entrar.", "de": "Claude Code nicht gefunden. Installiere es (claude.com/claude-code) und starte `claude` einmal zum Anmelden.", "fr": "Claude Code introuvable. Installe-le (claude.com/claude-code) et lance `claude` une fois pour te connecter.", "zh": "未找到 Claude Code。请安装（claude.com/claude-code）并运行一次 `claude` 登录。"},
    "model_empty_default": {"en": "Model (empty — Claude Code default)", "ru": "Модель (пусто — по умолчанию Claude Code)", "es": "Modelo (vacío — el de Claude Code)", "pt": "Modelo (vazio — padrão do Claude Code)", "de": "Modell (leer — Standard von Claude Code)", "fr": "Modèle (vide — celui de Claude Code)", "zh": "模型（留空则用 Claude Code 默认）"},
    "test": {"en": "Test", "ru": "Проверить", "es": "Probar", "pt": "Testar", "de": "Testen", "fr": "Tester", "zh": "测试"},
    "testing": {"en": "Testing…", "ru": "Проверяю…", "es": "Probando…", "pt": "Testando…", "de": "Teste…", "fr": "Test en cours…", "zh": "测试中…"},
    "test_ok": {"en": "✓ Connection works — answered in %.1f s. Example of the clean-up:", "ru": "✓ Связь есть — ответ за %.1f с. Пример обработки:", "es": "✓ La conexión funciona: respuesta en %.1f s. Ejemplo:", "pt": "✓ A conexão funciona — resposta em %.1f s. Exemplo:", "de": "✓ Verbindung steht — Antwort in %.1f s. Beispiel:", "fr": "✓ La connexion fonctionne — réponse en %.1f s. Exemple :", "zh": "✓ 连接正常——%.1f 秒内响应。处理示例："},
    "test_fail": {"en": "Error: %s", "ru": "Ошибка: %s", "es": "Error: %s", "pt": "Erro: %s", "de": "Fehler: %s", "fr": "Erreur : %s", "zh": "错误：%s"},
    "test_phrase": {"en": "so um this is like a quick connection test i guess", "ru": "ну короче это типа проверка связи эээ наверное", "es": "pues eh esto es como una prueba de conexión creo", "pt": "então tipo isso é um teste de conexão eu acho", "de": "also ähm das ist so ein verbindungstest glaube ich", "fr": "alors euh c'est genre un test de connexion je crois", "zh": "嗯那个这个就是一个连接测试吧"},
    "sec_behavior": {"en": "Behavior", "ru": "Поведение", "es": "Comportamiento", "pt": "Comportamento", "de": "Verhalten", "fr": "Comportement", "zh": "行为"},
    "cleanup": {"en": "Clean up dictation with the LLM", "ru": "Причёсывать диктовку через LLM", "es": "Pulir el dictado con el LLM", "pt": "Polir o ditado com o LLM", "de": "Diktat mit dem LLM glätten", "fr": "Mettre au propre la dictée avec le LLM", "zh": "用 LLM 润色听写"},
    "cleanup_note": {"en": "Off — the recognized text is inserted as is (faster). Selected-text commands always use the LLM.", "ru": "Выкл — распознанный текст вставляется как есть (быстрее). Команды к выделенному тексту всегда идут через LLM.", "es": "Desactivado — el texto reconocido se inserta tal cual (más rápido). Las órdenes sobre texto seleccionado siempre usan el LLM.", "pt": "Desligado — o texto reconhecido é inserido como está (mais rápido). Comandos sobre texto selecionado sempre usam o LLM.", "de": "Aus — der erkannte Text wird unverändert eingefügt (schneller). Befehle für markierten Text nutzen immer das LLM.", "fr": "Désactivé — le texte reconnu est inséré tel quel (plus rapide). Les consignes sur un texte sélectionné passent toujours par le LLM.", "zh": "关闭时直接插入识别结果（更快）。针对选中文本的指令始终使用 LLM。"},
    "autopaste": {"en": "Type the result into the text field under the cursor", "ru": "Вставлять результат в поле ввода под курсором", "es": "Escribir el resultado en el campo bajo el cursor", "pt": "Inserir o resultado no campo sob o cursor", "de": "Ergebnis ins Textfeld unter dem Cursor einfügen", "fr": "Insérer le résultat dans le champ sous le curseur", "zh": "将结果输入到光标所在的输入框"},
    "restore": {"en": "Keep my clipboard as it was after inserting", "ru": "После вставки возвращать прежнее содержимое буфера", "es": "Conservar mi portapapeles tras insertar", "pt": "Manter minha área de transferência após inserir", "de": "Zwischenablage nach dem Einfügen wiederherstellen", "fr": "Rétablir mon presse-papiers après insertion", "zh": "插入后恢复原剪贴板内容"},
    "sec_prompts": {"en": "Instructions for the LLM", "ru": "Инструкции для LLM", "es": "Instrucciones para el LLM", "pt": "Instruções para o LLM", "de": "Anweisungen für das LLM", "fr": "Consignes pour le LLM", "zh": "LLM 指令"},
    "p_dict": {"en": "Dictation (nothing selected)", "ru": "Диктовка (ничего не выделено)", "es": "Dictado (sin selección)", "pt": "Ditado (nada selecionado)", "de": "Diktat (nichts markiert)", "fr": "Dictée (rien de sélectionné)", "zh": "听写（未选中文本）"},
    "p_edit": {"en": "Working with selected text", "ru": "Работа с выделенным текстом", "es": "Trabajo con texto seleccionado", "pt": "Trabalho com texto selecionado", "de": "Arbeit mit markiertem Text", "fr": "Travail sur le texte sélectionné", "zh": "处理选中的文本"},
    "reset": {"en": "Reset to default", "ru": "Сбросить", "es": "Restablecer", "pt": "Redefinir", "de": "Zurücksetzen", "fr": "Réinitialiser", "zh": "恢复默认"},
    "close": {"en": "Close", "ru": "Закрыть", "es": "Cerrar", "pt": "Fechar", "de": "Schließen", "fr": "Fermer", "zh": "关闭"},
    # оверлей
    "o_listen": {"en": "Listening", "ru": "Слушаю", "es": "Escuchando", "pt": "Ouvindo", "de": "Höre zu", "fr": "J’écoute", "zh": "正在聆听"},
    "o_listen_sub": {"en": "%s · Esc — cancel", "ru": "%s · Esc — отмена", "es": "%s · Esc — cancelar", "pt": "%s · Esc — cancelar", "de": "%s · Esc — Abbrechen", "fr": "%s · Échap — annuler", "zh": "%s · Esc — 取消"},
    "o_warm": {"en": "Loading speech model…", "ru": "Загружаю модель речи…", "es": "Cargando el modelo de voz…", "pt": "Carregando o modelo de fala…", "de": "Sprachmodell wird geladen…", "fr": "Chargement du modèle vocal…", "zh": "正在加载语音模型…"},
    "o_recog": {"en": "Recognizing", "ru": "Распознаю", "es": "Reconociendo", "pt": "Reconhecendo", "de": "Erkenne", "fr": "Reconnaissance", "zh": "正在识别"},
    "o_think": {"en": "Thinking", "ru": "Обрабатываю", "es": "Procesando", "pt": "Processando", "de": "Verarbeite", "fr": "Traitement", "zh": "正在处理"},
    "o_pasted": {"en": "Inserted", "ru": "Вставлено", "es": "Insertado", "pt": "Inserido", "de": "Eingefügt", "fr": "Inséré", "zh": "已插入"},
    "o_copied": {"en": "Copied to clipboard", "ru": "Скопировано в буфер", "es": "Copiado al portapapeles", "pt": "Copiado para a área de transferência", "de": "In die Zwischenablage kopiert", "fr": "Copié dans le presse-papiers", "zh": "已复制到剪贴板"},
    "o_answer": {"en": "Answer copied to clipboard", "ru": "Ответ скопирован в буфер", "es": "Respuesta copiada al portapapeles", "pt": "Resposta copiada", "de": "Antwort kopiert", "fr": "Réponse copiée", "zh": "回答已复制到剪贴板"},
    "o_nothing": {"en": "Didn’t catch that", "ru": "Ничего не расслышал", "es": "No te he oído", "pt": "Não entendi", "de": "Nichts verstanden", "fr": "Je n’ai rien entendu", "zh": "没有听清"},
    "o_cancel": {"en": "Cancelled", "ru": "Отменено", "es": "Cancelado", "pt": "Cancelado", "de": "Abgebrochen", "fr": "Annulé", "zh": "已取消"},
    "o_err": {"en": "Something went wrong", "ru": "Что-то пошло не так", "es": "Algo salió mal", "pt": "Algo deu errado", "de": "Etwas ist schiefgelaufen", "fr": "Un problème est survenu", "zh": "出错了"},
    "o_err_mic": {"en": "Microphone unavailable", "ru": "Микрофон недоступен", "es": "Micrófono no disponible", "pt": "Microfone indisponível", "de": "Mikrofon nicht verfügbar", "fr": "Microphone indisponible", "zh": "麦克风不可用"},
    "o_err_mic_sub": {"en": "Check Windows › Privacy › Microphone", "ru": "Проверь Параметры › Конфиденциальность › Микрофон", "es": "Revisa Windows › Privacidad › Micrófono", "pt": "Verifique Windows › Privacidade › Microfone", "de": "Prüfe Windows › Datenschutz › Mikrofon", "fr": "Vérifie Windows › Confidentialité › Micro", "zh": "请检查 Windows › 隐私 › 麦克风"},
    "o_setup": {"en": "Voice input needs setting up", "ru": "Голосовой ввод нужно настроить", "es": "Hay que configurar la entrada por voz", "pt": "Configure a entrada por voz", "de": "Spracheingabe muss eingerichtet werden", "fr": "La saisie vocale doit être configurée", "zh": "需要先设置语音输入"},
    "o_click": {"en": "Click here to open settings", "ru": "Нажми, чтобы открыть настройки", "es": "Haz clic para abrir los ajustes", "pt": "Clique para abrir as configurações", "de": "Klicke, um die Einstellungen zu öffnen", "fr": "Clique pour ouvrir les paramètres", "zh": "点击打开设置"},
    "o_raw": {"en": "LLM failed — inserted raw text", "ru": "LLM не ответил — вставлен текст как есть", "es": "El LLM falló — texto sin procesar", "pt": "O LLM falhou — texto bruto inserido", "de": "LLM-Fehler — Rohtext eingefügt", "fr": "Échec du LLM — texte brut inséré", "zh": "LLM 失败——已插入原始文本"},
}

LANG_GET = lambda: "en"


def V(key, *args):
    s = VSTR.get(key, {})
    txt = s.get(LANG_GET()) or s.get("en") or key
    return (txt % args) if args else txt


DEFAULTS = {
    "enabled": True,
    "hk_mods": 0, "hk_vk": vs.VK_RCONTROL, "mode": "hold",
    "mic": "",
    "stt": "local", "w_model": "small", "lang": "auto", "stt_prompt": "",
    "stt_preset": "groq", "stt_url": "https://api.groq.com/openai/v1", "stt_key": "",
    "stt_model": "whisper-large-v3-turbo",
    "llm": "claude_code", "cc_path": "", "cc_model": "",
    "an_key": "", "an_model": "claude-opus-5",
    "oa_preset": "openai", "oa_url": "https://api.openai.com/v1", "oa_key": "",
    "oa_model": "gpt-4.1-mini",
    "cleanup": True, "autopaste": True, "restore_clip": True,
    "p_dict": "", "p_edit": "",
    "share_enabled": False, "share_port": 8765, "share_token": "",
    "rm_url": "", "rm_key": "",
}
SECRET_KEYS = {"stt_key", "an_key", "oa_key", "share_token", "rm_key"}

# ---------------------------------------------------------------------------
#  Палитра состояний индикатора
# ---------------------------------------------------------------------------
STATE_COLORS = {
    "listen": ((0, 224, 255), (255, 56, 196)),
    "warm": ((0, 224, 255), (120, 130, 255)),
    "recog": ((96, 140, 255), (0, 224, 255)),
    "think": ((178, 92, 255), (0, 224, 255)),
    "pasted": ((46, 224, 150), (0, 224, 255)),
    "copied": ((46, 224, 150), (0, 224, 255)),
    "answer": ((46, 224, 150), (178, 92, 255)),
    "nothing": ((150, 156, 168), (90, 96, 110)),
    "cancel": ((150, 156, 168), (90, 96, 110)),
    "error": ((255, 84, 72), (255, 160, 60)),
    "setup": ((255, 176, 48), (255, 84, 72)),
}
HIDE_AFTER = {"pasted": 2.2, "copied": 3.0, "answer": 4.0, "nothing": 1.6, "cancel": 1.0,
              "error": 7.0, "setup": 8.0}

# ---------------------------------------------------------------------------
#  Win32 для оверлея
# ---------------------------------------------------------------------------
u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32")
k32 = ctypes.WinDLL("kernel32")
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


def _d(fn, res, args):
    fn.restype, fn.argtypes = res, args


_d(u32.RegisterClassExW, wintypes.ATOM, [ctypes.POINTER(WNDCLASSEXW)])
_d(u32.CreateWindowExW, wintypes.HWND,
   [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p])
_d(u32.DefWindowProcW, LRESULT, [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM])
_d(u32.DestroyWindow, wintypes.BOOL, [wintypes.HWND])
_d(u32.ShowWindow, wintypes.BOOL, [wintypes.HWND, ctypes.c_int])
_d(u32.SetWindowPos, wintypes.BOOL, [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT])
_d(u32.GetWindowLongW, ctypes.c_long, [wintypes.HWND, ctypes.c_int])
_d(u32.SetWindowLongW, ctypes.c_long, [wintypes.HWND, ctypes.c_int, ctypes.c_long])
_d(u32.GetDC, wintypes.HDC, [wintypes.HWND])
_d(u32.ReleaseDC, ctypes.c_int, [wintypes.HWND, wintypes.HDC])
_d(u32.UpdateLayeredWindow, wintypes.BOOL,
   [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(SIZE), wintypes.HDC,
    ctypes.POINTER(wintypes.POINT), wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD])
_d(u32.SystemParametersInfoW, wintypes.BOOL, [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT])
_d(u32.LoadCursorW, wintypes.HANDLE, [wintypes.HINSTANCE, ctypes.c_void_p])
_d(k32.GetModuleHandleW, wintypes.HINSTANCE, [wintypes.LPCWSTR])
_d(g32.CreateCompatibleDC, wintypes.HDC, [wintypes.HDC])
_d(g32.DeleteDC, wintypes.BOOL, [wintypes.HDC])
_d(g32.CreateDIBSection, wintypes.HBITMAP, [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD])
_d(g32.SelectObject, wintypes.HGDIOBJ, [wintypes.HDC, wintypes.HGDIOBJ])
_d(g32.DeleteObject, wintypes.BOOL, [wintypes.HGDIOBJ])

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
GWL_EXSTYLE = -20
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
WM_LBUTTONUP = 0x0202
WM_MOUSEACTIVATE = 0x0021
WM_SETCURSOR = 0x0020
MA_NOACTIVATE = 3
ULW_ALPHA = 2
SPI_GETWORKAREA = 0x0030


def _dpi_scale():
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
    except Exception:
        return 1.0


def _font(names, size):
    if not HAVE_PIL:
        return None
    fdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for n in names:
        try:
            return ImageFont.truetype(os.path.join(fdir, n), size)
        except Exception:
            continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
#  Индикатор над часами: слоёная окно с per-pixel alpha, рисуем через Pillow
# ---------------------------------------------------------------------------
class Overlay:
    W0, H0 = 360, 136         # логический размер окна (до DPI); запас под свечение
    ORB = 30                  # радиус «сферы»

    def __init__(self, root, on_click, log):
        self.root, self.on_click, self.log = root, on_click, log
        self.hwnd = None
        self.state = None
        self.title = ""
        self.sub = ""
        self.rec = None
        self.t_state = 0.0
        self.alpha = 0.0
        self.target_alpha = 0.0
        self.hide_at = 0.0
        self.clickable = False
        self._ticking = False
        self._bars = [0.0] * 5
        self._glow = 0.0
        self._dc = None                 # DC + DIB переиспользуем между кадрами
        self.s = _dpi_scale()
        self.W, self.H = int(self.W0 * self.s), int(self.H0 * self.s)
        self.f_title = _font(["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"], int(13 * self.s))
        self.f_sub = _font(["segoeui.ttf", "arial.ttf"], int(11 * self.s))
        self._proc = WNDPROC(self._wndproc)

    # ---- окно ----
    def _create(self):
        hinst = k32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hCursor = u32.LoadCursorW(None, ctypes.c_void_p(32649))   # IDC_HAND
        wc.lpszClassName = "HDContainerVoiceOverlay"
        u32.RegisterClassExW(ctypes.byref(wc))
        ex = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT
        self.hwnd = u32.CreateWindowExW(ex, "HDContainerVoiceOverlay", "HDContainer voice",
                                        WS_POPUP, 0, 0, self.W, self.H, None, None, hinst, None)

    def _wndproc(self, hwnd, msg, wp, lp):
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_LBUTTONUP and self.clickable:
            self._clicked = True              # tk из нативного колбэка трогать нельзя
            return 0
        return u32.DefWindowProcW(hwnd, msg, wp, lp)

    def _pos(self):
        rc = wintypes.RECT()
        u32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rc), 0)
        # целиком внутри рабочей области: окно поверх панели задач та перерисовывает — мерцание
        return rc.right - self.W, rc.bottom - self.H

    def _set_click(self, on):
        self.clickable = on
        ex = u32.GetWindowLongW(self.hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        ex = (ex & ~WS_EX_TRANSPARENT) if on else (ex | WS_EX_TRANSPARENT)
        u32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ctypes.c_long(ex - (1 << 32) if ex >= 1 << 31 else ex).value)

    # ---- API ----
    def show(self, state, title, sub="", rec=None, clickable=False):
        if not HAVE_PIL:
            return self._show_fallback(state, title, sub, clickable)
        try:
            if not self.hwnd:
                self._create()
            self.state, self.title, self.sub = state, title, sub
            if rec is not None:
                self.rec = rec
            self.t_state = time.time()
            self.target_alpha = 1.0
            self.hide_at = time.time() + HIDE_AFTER[state] if state in HIDE_AFTER else 0
            self._set_click(clickable)
            x, y = self._pos()
            u32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, self.W, self.H,
                             SWP_NOACTIVATE | SWP_SHOWWINDOW)
            if not self._ticking:
                self._ticking = True
                self._tick()
        except Exception as ex:
            self.log("overlay show failed: %r" % ex)

    def update(self, sub=None, title=None):
        if sub is not None:
            self.sub = sub
        if title is not None:
            self.title = title

    def hide(self, delay=0.0):
        self.hide_at = time.time() + delay

    def destroy(self):
        self._free_dc()
        if self.hwnd:
            u32.DestroyWindow(self.hwnd)
            self.hwnd = None
        fb = getattr(self, "_fb", None)
        if fb:
            try:
                fb.destroy()
            except Exception:
                pass

    # ---- анимация ----
    def _tick(self):
        if not self.hwnd:
            self._ticking = False
            return
        if getattr(self, "_clicked", False):
            self._clicked = False
            self.hide(0)
            self.on_click()
        now = time.time()
        if self.hide_at and now >= self.hide_at:
            self.target_alpha = 0.0
        # плавное появление/исчезновение. ВАЖНО: при alpha == target не трогаем —
        # иначе 1.0 -> 0.875 -> 1.0 … каждый кадр, и индикатор мерцает
        if self.alpha < self.target_alpha:
            self.alpha = min(self.target_alpha, self.alpha + 1 / 5.0)
        elif self.alpha > self.target_alpha:
            self.alpha = max(self.target_alpha, self.alpha - 1 / 8.0)
        if self.alpha <= 0.0 and self.target_alpha == 0.0:
            u32.ShowWindow(self.hwnd, SW_HIDE)
            self.rec = None
            self._ticking = False
            return
        try:
            self._blit(self._render(now))
        except Exception as ex:
            self.log("overlay render failed: %r" % ex)
        self.root.after(33, self._tick)

    def _render(self, now):
        s, W, H = self.s, self.W, self.H
        t = now
        c1, c2 = STATE_COLORS.get(self.state, STATE_COLORS["listen"])
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        R = int(self.ORB * s)
        cx, cy = W - int(66 * s), H // 2
        raw = self.rec.level if (self.state == "listen" and self.rec is not None) else 0.0
        # свечение реагирует на голос плавно (шум микрофона не должен его дёргать)
        k = 0.25 if raw > self._glow else 0.08
        self._glow += (raw - self._glow) * k
        lvl = self._glow
        # --- свечение ---
        pulse = 0.5 + 0.5 * math.sin(t * 2.4)
        gr = int(R + (7 + 9 * lvl + 2 * pulse) * s)
        box = 2 * (R + int(34 * s))       # с запасом: размытие не должно упираться в край
        glow = Image.new("RGBA", (box, box), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        ga = int(95 + 70 * lvl + 20 * pulse)
        m = box // 2
        gd.ellipse((m - gr, m - gr, m + gr, m + gr), fill=c1 + (ga,))
        glow = glow.filter(ImageFilter.GaussianBlur(int(9 * s)))
        img.alpha_composite(glow, (cx - m, cy - m))
        # --- сфера (рисуем с 3x суперсэмплингом для гладких краёв) ---
        k = 3
        ob = (R + int(6 * s)) * 2
        orb = Image.new("RGBA", (ob * k, ob * k), (0, 0, 0, 0))
        od = ImageDraw.Draw(orb)
        oc = ob * k // 2
        rr = R * k
        od.ellipse((oc - rr, oc - rr, oc + rr, oc + rr), fill=(9, 12, 22, 238),
                   outline=c1 + (150,), width=int(1.2 * s * k))
        # вращающиеся дуги
        ra = int((R - 5 * s) * k)
        a1 = (t * 210) % 360
        a2 = (-t * 150) % 360
        wa = max(2, int(2.4 * s * k))
        od.arc((oc - ra, oc - ra, oc + ra, oc + ra), a1, a1 + 110, fill=c1 + (235,), width=wa)
        od.arc((oc - ra, oc - ra, oc + ra, oc + ra), a1 + 180, a1 + 225, fill=c1 + (120,), width=wa)
        rb = int((R - 10 * s) * k)
        od.arc((oc - rb, oc - rb, oc + rb, oc + rb), a2, a2 + 70, fill=c2 + (210,), width=max(2, wa - k))
        # центр — по состоянию
        self._draw_core(od, oc, k, s, t, c1, c2)
        orb = orb.resize((ob, ob), Image.LANCZOS)
        img.alpha_composite(orb, (cx - ob // 2, cy - ob // 2))
        # --- плашка с текстом ---
        self._draw_label(img, cx - R - int(12 * s), cy, c1)
        return img

    def _draw_core(self, d, oc, k, s, t, c1, c2):
        st = self.state
        if st == "listen":
            lv = list(self.rec.levels)[-5:] if self.rec else [0] * 5
            order = [lv[1], lv[3], lv[4], lv[2], lv[0]]      # громкое — в центре
            for i in range(5):
                tgt = order[i] if i < len(order) else 0
                self._bars[i] += (tgt - self._bars[i]) * 0.45
            bw, gap = 3.2 * s * k, 2.6 * s * k
            x0 = oc - (5 * bw + 4 * gap) / 2
            for i, v in enumerate(self._bars):
                hh = (4 + 20 * v + 1.5 * math.sin(t * 6 + i)) * s * k
                x = x0 + i * (bw + gap)
                col = tuple(int(a + (b - a) * (i / 4.0)) for a, b in zip(c1, c2)) + (255,)
                d.rounded_rectangle((x, oc - hh / 2, x + bw, oc + hh / 2), radius=bw / 2, fill=col)
        elif st in ("warm", "recog", "think"):
            rad = 9 * s * k
            for i in range(3):
                ang = t * (4.5 if st == "think" else 3.0) + i * 2 * math.pi / 3
                x, y = oc + rad * math.cos(ang), oc + rad * math.sin(ang)
                r = (2.6 + 0.8 * math.sin(t * 5 + i)) * s * k
                col = (c1 if i else c2) + (255,)
                d.ellipse((x - r, y - r, x + r, y + r), fill=col)
            r0 = (3 + 1.2 * math.sin(t * 4)) * s * k
            d.ellipse((oc - r0, oc - r0, oc + r0, oc + r0), fill=(255, 255, 255, 220))
        elif st in ("pasted", "copied", "answer"):
            p = min(1.0, (time.time() - self.t_state) / 0.28)
            pts = [(-8, 0), (-2.5, 6), (9, -7)]
            pts = [(oc + x * s * k, oc + y * s * k) for x, y in pts]
            if p < 0.45:
                q = p / 0.45
                seg = [pts[0], (pts[0][0] + (pts[1][0] - pts[0][0]) * q, pts[0][1] + (pts[1][1] - pts[0][1]) * q)]
            else:
                q = (p - 0.45) / 0.55
                seg = [pts[0], pts[1], (pts[1][0] + (pts[2][0] - pts[1][0]) * q, pts[1][1] + (pts[2][1] - pts[1][1]) * q)]
            d.line(seg, fill=c1 + (255,), width=int(3.4 * s * k), joint="curve")
        elif st in ("nothing", "cancel"):
            w = 8 * s * k
            d.line((oc - w, oc, oc + w, oc), fill=c1 + (255,), width=int(3 * s * k))
        else:                                                  # error / setup
            d.line((oc, oc - 9 * s * k, oc, oc + 2 * s * k), fill=c1 + (255,), width=int(3.4 * s * k))
            r = 2.2 * s * k
            d.ellipse((oc - r, oc + 6 * s * k - r, oc + r, oc + 6 * s * k + r), fill=c1 + (255,))

    def _draw_label(self, img, right, cy, c1):
        s = self.s
        title = self.title or ""
        sub = self.sub or ""
        d = ImageDraw.Draw(img)
        maxw = right - int(10 * s)
        sub = self._fit(d, sub.replace("\n", " "), self.f_sub, maxw - int(28 * s))
        title = self._fit(d, title, self.f_title, maxw - int(28 * s))
        tw = max(d.textlength(title, font=self.f_title), d.textlength(sub, font=self.f_sub) if sub else 0)
        pw = int(tw + 28 * s)
        ph = int((46 if sub else 32) * s)
        x1, y0 = right, cy - ph // 2
        x0 = x1 - pw
        d.rounded_rectangle((x0, y0, x1, y0 + ph), radius=int(12 * s), fill=(9, 12, 22, 225),
                            outline=c1 + (90,), width=max(1, int(s)))
        # тонкий «HUD»-акцент слева
        d.rounded_rectangle((x0 + int(6 * s), y0 + int(9 * s), x0 + int(8.5 * s), y0 + ph - int(9 * s)),
                            radius=int(2 * s), fill=c1 + (230,))
        tx = x0 + int(16 * s)
        if sub:
            d.text((tx, y0 + int(6 * s)), title, font=self.f_title, fill=(236, 240, 247, 255))
            d.text((tx, y0 + int(25 * s)), sub, font=self.f_sub, fill=(150, 160, 178, 255))
        else:
            d.text((tx, y0 + int(7 * s)), title, font=self.f_title, fill=(236, 240, 247, 255))

    @staticmethod
    def _fit(d, text, font, maxw):
        if not text or d.textlength(text, font=font) <= maxw:
            return text
        while text and d.textlength(text + "…", font=font) > maxw:
            text = text[:-1]
        return text + "…"

    def _blit(self, img):
        W, H = img.size
        a = img.split()[3]
        prem = img.convert("RGBa")                              # premultiplied alpha
        r, g, b, _ = prem.split()
        data = Image.merge("RGBA", (b, g, r, a)).tobytes()      # BGRA для DIB
        if self._dc is None or self._dc[3] != (W, H):
            self._free_dc()
            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth, bmi.biHeight = W, -H
            bmi.biPlanes, bmi.biBitCount = 1, 32
            mdc = g32.CreateCompatibleDC(None)
            bits = ctypes.c_void_p()
            hbm = g32.CreateDIBSection(mdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            old = g32.SelectObject(mdc, hbm)
            self._dc = (mdc, hbm, old, (W, H), bits)
        mdc, _hbm, _old, _sz, bits = self._dc
        ctypes.memmove(bits, data, len(data))
        x, y = self._pos()
        blend = BLENDFUNCTION(0, 0, int(round(255 * self.alpha)), 1)
        sdc = u32.GetDC(None)
        try:
            u32.UpdateLayeredWindow(self.hwnd, sdc, ctypes.byref(wintypes.POINT(x, y)),
                                    ctypes.byref(SIZE(W, H)), mdc, ctypes.byref(wintypes.POINT(0, 0)),
                                    0, ctypes.byref(blend), ULW_ALPHA)
        finally:
            u32.ReleaseDC(None, sdc)

    def _free_dc(self):
        if self._dc:
            mdc, hbm, old, _sz, _bits = self._dc
            g32.SelectObject(mdc, old)
            g32.DeleteObject(hbm)
            g32.DeleteDC(mdc)
            self._dc = None

    # ---- запасной вариант без Pillow: простая плашка tk ----
    def _show_fallback(self, state, title, sub, clickable):
        fb = getattr(self, "_fb", None)
        if fb is None:
            fb = tk.Toplevel(self.root)
            fb.overrideredirect(True)
            fb.attributes("-topmost", True)
            fb.attributes("-alpha", 0.93)
            fb.configure(bg="#0b0f1a")
            self._fb_lbl = tk.Label(fb, bg="#0b0f1a", fg="#e8ecf4", font=("Segoe UI Semibold", 10),
                                    justify="left", padx=14, pady=8)
            self._fb_lbl.pack()
            self._fb = fb
        col = "#%02x%02x%02x" % STATE_COLORS.get(state, STATE_COLORS["listen"])[0]
        self._fb_lbl.configure(text="● " + title + ("\n" + sub if sub else ""), fg=col)
        self._fb_lbl.bind("<Button-1>", lambda e: (self.on_click(), fb.withdraw()) if clickable else None)
        fb.update_idletasks()
        x, y = self._pos_fb(fb)
        fb.geometry("+%d+%d" % (x, y))
        fb.deiconify()
        if state in HIDE_AFTER:
            self.root.after(int(HIDE_AFTER[state] * 1000), fb.withdraw)

    def _pos_fb(self, fb):
        rc = wintypes.RECT()
        u32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rc), 0)
        return rc.right - fb.winfo_reqwidth() - 8, rc.bottom - fb.winfo_reqheight() - 8


# ---------------------------------------------------------------------------
#  Сессия (одно нажатие горячей клавиши)
# ---------------------------------------------------------------------------
class Session:
    def __init__(self, fg):
        self.fg = fg
        self.t0 = time.time()
        self.rec = None
        self.probe = None
        self.probe_thr = None
        self.runner = None
        self.cancelled = False


CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "mintty",
                   "PseudoConsoleWindow"}


class VoiceController:
    def __init__(self, app, get_lang, log, local_dir):
        global LANG_GET
        LANG_GET = get_lang
        self.draft = None               # черновик настроек, пока открыто окно (см. begin_edit)
        self.app = app
        self.root = app.root
        self.log = log
        self.dir = local_dir
        self.whisper = ve.WhisperLocal(os.path.join(local_dir, "whisper"), log)
        self.overlay = Overlay(self.root, self.open_settings, log)
        self.state = "idle"
        self.sess = None
        self._q = queue.Queue()
        self._pumping = False
        self._hk_ok = False
        self.win = None
        self.dl = None                  # {"done":..,"total":..,"err":..,"model":..}
        vs.CLIP_HWND = app.msg_hwnd
        self._arm_t = 0.0
        self._capture_dlg = None
        self.hook = vs.KeyHook(log)
        self.hook.start()
        self.register_hotkey()
        self.share = None
        self._p = self._pbg = None
        self._migrate()
        self.apply_share()
        self.root.after(20, self._hook_loop)
        self.root.after(60000, self._idle_tick)

    # ---- настройки ----
    def get(self, k):
        src = self.draft if self.draft is not None else self.app.settings.get("voice", {})
        v = src.get(k, DEFAULTS[k])
        return vs.unprotect(v) if k in SECRET_KEYS else v

    def set(self, k, v, save=True):
        v = vs.protect(v) if (k in SECRET_KEYS and v) else v
        if self.draft is not None:            # окно настроек открыто — только черновик
            self.draft[k] = v
            return
        d = self.app.settings.setdefault("voice", {})
        d[k] = v
        if save:
            self.app._set_setting("voice", d)

    # ---- черновик: правки из окна применяются только по «Сохранить» ----
    def begin_edit(self):
        self.draft = dict(self.app.settings.get("voice", {}))

    def discard(self):
        self.draft = None
        self.win = None
        self.hook.capture = False

    def commit(self):
        new, self.draft = self.draft, None
        self.win = None
        self.hook.capture = False
        if new is None:
            return
        old = dict(self.app.settings.get("voice", {}))
        self.app._set_setting("voice", new)
        self.register_hotkey()
        if any(old.get(k) != new.get(k) for k in
               ("share_enabled", "share_port", "share_token", "cc_path", "cc_model")):
            self.apply_share()
        if any(old.get(k) != new.get(k) for k in ("w_model", "lang")):
            self.whisper.stop()
        self.log("voice settings saved")

    # ---- горячая клавиша (низкоуровневый хук, см. voice_sys.KeyHook) ----
    ARM_DELAY = 0.2         # одиночный модификатор: запись стартует, если держат дольше

    def register_hotkey(self):
        self.hook.configure(self.get("hk_mods"), self.get("hk_vk"), self.get("enabled"))
        self._hk_ok = bool(self.hook._hook)
        self.log("voice hotkey %s (%s) hook=%s" % (
            vs.hotkey_label(self.get("hk_mods"), self.get("hk_vk")), self.get("mode"),
            "ok" if self._hk_ok else "FAILED"))
        return self._hk_ok

    def unregister_hotkey(self):
        self.hook.configure(self.get("hk_mods"), self.get("hk_vk"), False)

    def on_hotkey(self, wparam):
        return False            # RegisterHotKey больше не используется

    def _hook_loop(self):
        # разбираем события хука здесь, в обычном таймере tk (никогда из колбэка)
        try:
            while True:
                try:
                    ev = self.hook.events.get_nowait()
                except queue.Empty:
                    break
                self._on_key_event(ev)
            if self.state == "arming" and time.time() - self._arm_t >= self.ARM_DELAY:
                self.state = "idle"
                self._start()
        except Exception:
            self.log("voice key loop:\n" + traceback.format_exc())
        self.root.after(20, self._hook_loop)

    def _on_key_event(self, ev):
        kind = ev[0]
        if kind in ("captured", "capture_cancel"):
            self._capture_done(ev)
            return
        toggle = self.get("mode") == "toggle"
        single = self.hook.modifier_only
        if kind == "press":
            if toggle and not single:
                if self.state == "idle":
                    self._start()
                elif self.state == "recording":
                    self._stop_recording()
            elif not toggle and self.state == "idle":
                if single:
                    self.state, self._arm_t = "arming", time.time()
                else:
                    self._start()
        elif kind == "release":
            if toggle:
                if single:                    # одиночный модификатор: «тап» = старт/стоп
                    if self.state == "idle":
                        self._start()
                    elif self.state == "recording":
                        self._stop_recording()
            elif self.state == "arming":
                self.state = "idle"           # коротко нажали — это не запись
            elif self.state == "recording":
                self._stop_recording()
        elif kind in ("chord", "chord_release"):
            if self.state == "arming":
                self.state = "idle"
            elif self.state == "recording" and not toggle:
                self._cancel(silent=True)     # это было обычное сочетание (RCtrl+C…)
        elif kind == "esc":
            if self.state == "recording":
                self._cancel()

    # ---- проверка готовности ----
    def _setup_problem(self):
        """Короткое описание того, что не настроено (или None)."""
        if self.get("stt") == "local":
            if not self.whisper.is_installed(self.get("w_model")):
                return V("o_need_model")
        elif not self.get("stt_url"):
            return V("o_setup")
        llm = self.get("llm")
        if llm == "claude_code" and not ve.find_claude(self.get("cc_path")):
            return V("o_need_llm")
        if llm == "remote" and not (self.get("rm_url") and self.get("rm_key")):
            return V("o_need_llm")
        if llm == "anthropic" and not self.get("an_key"):
            return V("o_need_llm")
        if llm == "openai" and not self.get("oa_url"):
            return V("o_need_llm")
        return None

    # ---- запись ----
    def _start(self):
        problem = self._setup_problem()
        if problem:
            self.overlay.show("setup", problem, V("o_click"), clickable=True)
            self.log("voice: setup needed: %s" % problem)
            return
        fg = vs.user32.GetForegroundWindow()
        s = Session(fg)
        s.rec = vs.Recorder(self.get("mic"))
        if not s.rec.start():
            self.log("voice: mic failed %s" % s.rec.error)
            self.overlay.show("error", V("o_err_mic"), V("o_err_mic_sub"))
            return
        self.sess = s
        self.state = "recording"
        self.hook.recording = True
        self.overlay.show("listen", V("o_listen"), V("o_listen_sub", "0:00"), rec=s.rec)
        # пока человек говорит: смотрим, что в фокусе/выделено, и прогреваем движки
        s.probe_thr = threading.Thread(target=self._probe, args=(s,), daemon=True)
        s.probe_thr.start()
        threading.Thread(target=self._prewarm, args=(s,), daemon=True).start()
        self._ensure_pump()
        self.root.after(40, self._hold_poll)

    def _probe(self, s):
        try:
            s.probe = vs.uia_probe(s.fg)
        except Exception as ex:
            s.probe = {"editable": None, "selection": None, "error": repr(ex)}

    def _prewarm(self, s):
        try:
            if self.get("llm") == "claude_code":
                spf = ve.write_prompt_file(os.path.join(ve.scratch_dir(), "system_prompt.txt"),
                                           self._system_prompt())
                s.runner = ve.ClaudeCode(ve.find_claude(self.get("cc_path")), self.get("cc_model"),
                                         spf, os.path.join(ve.scratch_dir(), "cwd"), self.log)
                if not s.cancelled:
                    s.runner.start()
            elif self.get("llm") == "remote":
                ve.remote_warm(ve.remote_base(self.get("rm_url")), self.get("rm_key"),
                               self._system_prompt())
            if self.get("stt") == "local":
                self.whisper.ensure_server(self.get("w_model"), self.get("lang"))
        except Exception as ex:
            self.log("voice prewarm: %r" % ex)

    def _hold_poll(self):
        s = self.sess
        if self.state != "recording" or s is None:
            return
        if self.get("mode") == "hold" and not vs.key_down(self.get("hk_vk")):
            s.lost_key = getattr(s, "lost_key", 0) + 1
            if s.lost_key > 10:               # отпускание не пришло (экран блокировки и т.п.)
                self._stop_recording()
                return
        else:
            s.lost_key = 0
        d = s.rec.duration
        if d > 300:
            self._stop_recording()
            return
        self.overlay.update(sub=V("o_listen_sub", "%d:%02d" % (int(d) // 60, int(d) % 60)))
        self.root.after(40, self._hold_poll)

    def _cancel(self, silent=False):
        s = self.sess
        s.cancelled = True
        s.rec.stop()
        if s.runner:
            s.runner.cancel()
        self.state = "idle"
        self.hook.recording = False
        self.sess = None
        if silent:
            self.overlay.hide(0)
        else:
            self.overlay.show("cancel", V("o_cancel"))

    def _stop_recording(self):
        s = self.sess
        self.hook.recording = False
        s.rec.stop()
        dur, peak = s.rec.duration, s.rec.peak_rms
        self.log("voice: recorded %.2fs peak=%.4f" % (dur, peak))
        if dur < 0.35 or peak < 0.003:
            s.cancelled = True
            if s.runner:
                s.runner.cancel()
            self.state = "idle"
            self.sess = None
            self.overlay.show("nothing", V("o_nothing"))
            return
        self.state = "busy"
        warm = self.get("stt") == "local" and not self.whisper.alive()
        self.overlay.show("warm" if warm else "recog", V("o_warm") if warm else V("o_recog"))
        threading.Thread(target=self._pipeline, args=(s,), daemon=True).start()
        self._ensure_pump()

    # ---- очередь рабочий поток -> tk ----
    def _post(self, kind, **kw):
        self._q.put((kind, kw))

    def _ensure_pump(self):
        if not self._pumping:
            self._pumping = True
            self.root.after(30, self._pump)

    def _pump(self):
        while True:
            try:
                kind, kw = self._q.get_nowait()
            except queue.Empty:
                break
            if kind == "stage":
                self.overlay.show(kw["state"], kw["title"], kw.get("sub", ""))
            elif kind == "final":
                self.state = "idle"
                self.sess = None
                self.overlay.show(kw["state"], kw["title"], kw.get("sub", ""),
                                  clickable=kw.get("clickable", False))
            elif kind == "test":
                cb = kw.get("cb")
                if cb:
                    cb(kw.get("text", ""))
        if self.state != "idle" or not self._q.empty() or getattr(self, "_test_running", False):
            self.root.after(40, self._pump)
        else:
            self._pumping = False

    # ---- конвейер ----
    def _system_prompt(self):
        return ve.system_prompt(self.get("p_dict").strip() or None, self.get("p_edit").strip() or None)

    def _stt(self, wav):
        lang = self.get("lang")
        if self.get("stt") == "local":
            return self.whisper.transcribe(wav, self.get("w_model"), lang, self.get("stt_prompt"))
        return ve.cloud_transcribe(wav, self.get("stt_url"), self.get("stt_key"), self.get("stt_model"),
                                   lang, self.get("stt_prompt"))

    def _llm(self, text, runner=None):
        p = self.get("llm")
        if p == "claude_code":
            if runner is None:
                spf = ve.write_prompt_file(os.path.join(ve.scratch_dir(), "system_prompt.txt"),
                                           self._system_prompt())
                runner = ve.ClaudeCode(ve.find_claude(self.get("cc_path")), self.get("cc_model"),
                                       spf, os.path.join(ve.scratch_dir(), "cwd"), self.log)
            return runner.ask(text)
        if p == "remote":
            return ve.openai_ask(ve.remote_base(self.get("rm_url")), self.get("rm_key"), ve.SHARE_MODEL_ID,
                                 self._system_prompt(), text)
        if p == "anthropic":
            return ve.anthropic_ask(self.get("an_key"), self.get("an_model"), self._system_prompt(), text)
        return ve.openai_ask(self.get("oa_url"), self.get("oa_key"), self.get("oa_model"),
                             self._system_prompt(), text)

    def _clipboard_selection(self, fg):
        """Запасной способ узнать выделение, если UIA не сказал: Ctrl+Insert
        (в отличие от Ctrl+C не шлёт SIGINT в терминалах) + проверка, что буфер
        изменился. Буфер потом возвращаем как был."""
        if vs.class_name(fg) in CONSOLE_CLASSES:
            return ""
        snap = vs.clip_snapshot()
        seq = vs.clip_seq()
        vs.send_combo([vs.VK_CONTROL], vs.VK_INSERT)
        end = time.time() + 0.45
        while time.time() < end and vs.clip_seq() == seq:
            time.sleep(0.015)
        if vs.clip_seq() == seq:
            return ""
        time.sleep(0.03)
        txt = vs.clip_get_text() or ""
        meta = vs.clip_get_format("vscode-editor-data") or b""
        if b'"isFromEmptySelection":true' in meta.replace(b" ", b""):
            txt = ""                               # VS Code копирует строку при пустом выделении
        vs.clip_restore(snap)
        return txt

    def _pipeline(self, s):
        t0 = time.time()
        try:
            wav = s.rec.wav_bytes()
            text = ve.clean_transcript(self._stt(wav))
            t_stt = time.time() - t0
            self.log("voice: stt %.1fs -> %r" % (t_stt, text[:120]))
            if not text:
                if s.runner:
                    s.runner.cancel()
                self._post("final", state="nothing", title=V("o_nothing"))
                return
            if s.probe_thr:
                s.probe_thr.join(1.5)
            probe = s.probe or {}
            vs.wait_modifiers_released(1.5)
            sel = probe.get("selection")
            if sel is None:
                sel = self._clipboard_selection(s.fg)
            sel = sel if sel and sel.strip() else None
            editable = probe.get("editable")
            if not editable:
                _focus, caret = vs.caret_info(s.fg)
                if caret and editable is None:
                    editable = True
            self.log("voice: probe uia=%s ctrl=%s cls=%r editable=%s sel=%d"
                     % (probe.get("uia"), probe.get("ctrl"), probe.get("cls"), editable,
                        len(sel or "")))
            answer, warn = False, ""
            if not sel and not self.get("cleanup"):
                result = text
                if s.runner:
                    s.runner.cancel()
            else:
                self._post("stage", state="think", title=V("o_think"), sub=text)
                t1 = time.time()
                try:
                    raw = self._llm(ve.user_message(text, sel), s.runner)
                    result, answer = ve.split_answer(raw)
                    self.log("voice: llm %.1fs answer=%s" % (time.time() - t1, answer))
                except ve.VoiceError as ex:
                    if sel:
                        raise
                    self.log("voice: llm failed, raw text: %s" % ex)
                    result, warn = text, V("o_raw")
            if not result.strip():
                self._post("final", state="nothing", title=V("o_nothing"))
                return
            how = self._deliver(s, result, bool(editable) and not answer)
            if answer:
                how = "answer"
            title = {"pasted": V("o_pasted"), "copied": V("o_copied"), "answer": V("o_answer")}[how]
            self._post("final", state=how, title=warn or title, sub=result)
            self.log("voice: done %s in %.1fs" % (how, time.time() - s.t0))
        except ve.VoiceError as ex:
            msg = str(ex)
            self.log("voice error: %s" % msg)
            if msg in ("model_missing", "claude_missing"):
                self._post("final", state="setup", title=V("o_setup"), sub=V("o_click"), clickable=True)
            else:
                self._post("final", state="error", title=V("o_err"), sub=msg, clickable=True)
        except Exception as ex:
            self.log("voice pipeline crash:\n" + traceback.format_exc())
            self._post("final", state="error", title=V("o_err"), sub=repr(ex)[:120], clickable=True)
        finally:
            if s.runner:
                s.runner.cancel()

    def _deliver(self, s, text, editable):
        fg_now = vs.user32.GetForegroundWindow()
        if self.get("autopaste") and editable and fg_now == s.fg:
            restore = self.get("restore_clip")
            snap = vs.clip_snapshot() if restore else None
            if not vs.clip_set_text(text, exclude_history=restore):
                raise ve.VoiceError("clipboard busy")
            seq = vs.clip_seq()
            time.sleep(0.04)
            vs.send_combo([vs.VK_CONTROL], vs.VK_V)
            if restore and snap is not None:
                time.sleep(0.8)                    # дать приложению забрать текст
                if vs.clip_seq() == seq:
                    vs.clip_restore(snap)
            return "pasted"
        vs.clip_set_text(text)
        return "copied"

    def _migrate(self):
        v = self.app.settings.get("voice") or {}
        # 1.3.2: «Claude на другом ПК» был пресетом OpenAI-совместимых
        if v.get("llm") == "openai" and v.get("oa_preset") == "hdcontainer":
            self.set("llm", "remote", save=False)
            self.set("rm_url", self.get("oa_url"), save=False)
            self.set("rm_key", self.get("oa_key"), save=False)
            self.set("oa_preset", "openai", save=False)
            self.set("oa_url", DEFAULTS["oa_url"], save=False)
            self.set("oa_model", DEFAULTS["oa_model"], save=False)
        # раздавать Claude Code может только тот ПК, где он есть
        if v.get("share_enabled") and not ve.find_claude(self.get("cc_path")):
            self.set("share_enabled", False, save=False)
            self.log("voice: share disabled — no Claude Code on this PC")
        if v:
            self.app._set_setting("voice", self.app.settings.get("voice", {}))

    # ---- «Поделиться Claude Code» (сервер для других ПК) ----
    def apply_share(self):
        if self.share:
            self.share.stop()
            self.share = None
        if not self.get("share_enabled"):
            return
        if not self.get("share_token"):
            self.set("share_token", ve.new_token())
        pool = ve.WarmPool(lambda: ve.find_claude(self.get("cc_path")), lambda: self.get("cc_model"),
                           self.log)
        try:
            port = int(self.get("share_port"))
        except (TypeError, ValueError):
            port = DEFAULTS["share_port"]
        self.share = ve.ShareServer(pool, self.get("share_token"), port, self.log)
        self.share.start()

    def _idle_tick(self):
        try:
            self.whisper.idle_check()
        except Exception:
            pass
        self.root.after(60000, self._idle_tick)

    def shutdown(self):
        try:
            self.hook.stop()
            if self.share:
                self.share.stop()
            if self.sess and self.sess.runner:
                self.sess.runner.cancel()
            self.whisper.stop()
            self.overlay.destroy()
        except Exception as ex:
            self.log("voice shutdown: %r" % ex)

    # =======================================================================
    #  Окно настроек
    # =======================================================================
    def open_settings(self):
        self.app._open_settings("voice")

    def build_into(self, parent, win):
        """Нарисовать вкладку «Голосовой ввод» внутри общего окна настроек."""
        main = __import__("__main__")
        C = lambda n, d: getattr(main, n, d)
        self.c_bg, self.c_sf, self.c_sf2 = C("COL_BG", "#1b1b1d"), C("COL_SURFACE", "#232427"), C("COL_SURFACE2", "#2d2e31")
        self.c_bd, self.c_hv, self.c_ac = C("COL_BORDER", "#2a2b2c"), C("COL_HOVER", "#303236"), C("COL_ACCENT", "#4c8bf5")
        self.c_tx, self.c_dim, self.c_err = C("COL_TEXT", "#e6e8ea"), C("COL_TEXT_DIM", "#9aa0a6"), C("COL_DANGER", "#ff453a")
        self.win = win
        cv = tk.Canvas(parent, bg=self.c_sf, highlightthickness=0, bd=0)
        sb = self.app._dark_scrollbar(parent, cv)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        body = tk.Frame(cv, bg=self.c_sf)
        wid = cv.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(wid, width=e.width))
        win.bind("<MouseWheel>", lambda e: cv.yview_scroll(int(-e.delta / 120) * 3, "units"))
        self._cv, self._body = cv, body
        self._build()
        self._dl_poll()

    def _rebuild(self):
        y = self._cv.yview()[0]
        for w in self._body.winfo_children():
            w.destroy()
        self._build()
        self._body.update_idletasks()
        self._cv.configure(scrollregion=self._cv.bbox("all"))
        self._cv.yview_moveto(y)

    # ---- мелкие виджеты ----
    PAD = 24

    # Виджеты рисуются в «текущий контейнер» self._p с фоном self._pbg: это либо
    # само тело окна, либо карточка настроек выбранного варианта (см. _panel).
    def _px(self, indent=0):
        base = 14 if self._p is not self._body else self.PAD
        return (base + indent, base)

    def _h(self, text):
        tk.Label(self._body, text=text, bg=self.c_sf, fg=self.c_tx, font=("Segoe UI Semibold", 11)).pack(
            anchor="w", padx=self.PAD, pady=(20, 6))

    def _note(self, text, fg=None, pady=(0, 4), indent=0):
        lb = tk.Label(self._p, text=text, bg=self._pbg, fg=fg or self.c_dim, font=("Segoe UI", 9),
                      justify="left", wraplength=500 - indent, anchor="w")
        lb.pack(anchor="w", fill="x", padx=self._px(indent), pady=pady)
        return lb

    def _check(self, text, key, on_change=None):
        def setter(v):
            self.set(key, bool(v))
            if on_change:
                on_change(v)
        self.app._setting_check(self._body, text, bool(self.get(key)), setter, self.PAD)

    def _radio(self, options, key, panels=None):
        """panels: {value: (заголовок, функция-построитель)} — карточка под выбранным вариантом."""
        cur = self.get(key)
        for val, label in options:
            row = tk.Frame(self._p, bg=self._pbg)
            row.pack(fill="x", padx=self._px(), pady=2)
            on = (val == cur)
            dot = tk.Label(row, text="◉" if on else "○", bg=self._pbg,
                           fg=self.c_ac if on else self.c_dim, font=("Segoe UI Symbol", 12), cursor="hand2")
            dot.pack(side="left", anchor="n")
            # отступ — через padx, а не пробелами: тогда перенесённая строка
            # начинается ровно под первой, а не под кружком
            lb = tk.Label(row, text=label, bg=self._pbg, fg=self.c_tx if on else self.c_dim,
                          font=("Segoe UI", 10), cursor="hand2", justify="left", wraplength=470, anchor="w")
            lb.pack(side="left", fill="x", padx=(8, 0), pady=(2, 0))

            def pick(_e=None, v=val):
                self.set(key, v)
                self._rebuild()
            dot.bind("<Button-1>", pick)
            lb.bind("<Button-1>", pick)
            if on and panels and val in panels:
                title, build = panels[val]
                with self._panel(title):
                    build()

    class _PanelCtx:
        def __init__(self, ctl, title):
            self.ctl, self.title = ctl, title

        def __enter__(self):
            c = self.ctl
            outer = tk.Frame(c._body, bg=c.c_sf2)
            outer.pack(fill="x", padx=(c.PAD + 26, c.PAD), pady=(4, 8))
            tk.Frame(outer, bg=c.c_ac, width=3).pack(side="left", fill="y")
            inner = tk.Frame(outer, bg=c.c_sf2)
            inner.pack(side="left", fill="both", expand=True, pady=(10, 12))
            tk.Label(inner, text=self.title, bg=c.c_sf2, fg=c.c_tx, font=("Segoe UI Semibold", 9)).pack(
                anchor="w", padx=14, pady=(0, 4))
            self.saved = (c._p, c._pbg)
            c._p, c._pbg = inner, c.c_sf2
            return inner

        def __exit__(self, *exc):
            self.ctl._p, self.ctl._pbg = self.saved
            return False

    def _panel(self, title):
        """Карточка с настройками выбранного варианта — видно, к чему они относятся."""
        return self._PanelCtx(self, title)

    def _entry(self, label, key, secret=False, on_change=None):
        row = tk.Frame(self._p, bg=self._pbg)
        row.pack(fill="x", padx=self._px(), pady=3)
        tk.Label(row, text=label, bg=self._pbg, fg=self.c_dim, font=("Segoe UI", 9)).pack(anchor="w")
        var = tk.StringVar(value=self.get(key) or "")
        ent = tk.Entry(row, textvariable=var, font=("Segoe UI", 10), bg=self.c_bg, fg=self.c_tx,
                       insertbackground=self.c_tx, relief="flat", highlightthickness=1,
                       highlightbackground=self.c_bd, highlightcolor=self.c_ac, show="•" if secret else "")
        ent.pack(fill="x", ipady=4)
        for combo in ("<Control-a>", "<Control-A>"):
            ent.bind(combo, lambda e, en=ent: (en.select_range(0, "end"), "break")[-1])

        def changed(*_):
            self.set(key, var.get().strip())
            if on_change:
                on_change()
        var.trace_add("write", changed)
        return ent

    def _dropdown(self, label, items, key, on_pick=None):
        """items: [(value, text)]"""
        row = tk.Frame(self._p, bg=self._pbg)
        row.pack(fill="x", padx=self._px(), pady=3)
        tk.Label(row, text=label, bg=self._pbg, fg=self.c_dim, font=("Segoe UI", 9)).pack(side="left")
        cur = self.get(key)
        cur_txt = next((t for v, t in items if v == cur), str(cur))
        val = tk.Label(row, text=cur_txt + "   ⌄", bg=self._pbg, fg=self.c_ac, font=("Segoe UI", 10),
                       cursor="hand2")
        val.pack(side="right")

        def pick(v):
            self.set(key, v)
            if on_pick:
                on_pick(v)
            self._rebuild()
        val.bind("<Button-1>", lambda e: self._popup_list(val, items, pick))

    def _popup_list(self, anchor, items, pick):
        pop = tk.Toplevel(self.win)
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        pop.configure(bg=self.c_bd)
        fr = tk.Frame(pop, bg=self.c_sf)
        fr.pack(padx=1, pady=1)
        for v, t in items:
            r = tk.Label(fr, text=t, bg=self.c_sf, fg=self.c_tx, font=("Segoe UI", 10), anchor="w",
                         padx=14, pady=5, cursor="hand2")
            r.pack(fill="x")
            r.bind("<Enter>", lambda e, r=r: r.configure(bg=self.c_hv))
            r.bind("<Leave>", lambda e, r=r: r.configure(bg=self.c_sf))
            r.bind("<Button-1>", lambda e, v=v: (pop.destroy(), pick(v)))
        anchor.update_idletasks()
        pop.update_idletasks()
        x = max(0, anchor.winfo_rootx() + anchor.winfo_width() - pop.winfo_reqwidth())
        y = anchor.winfo_rooty() + anchor.winfo_height() + 2
        pop.geometry("+%d+%d" % (x, y))
        pop.focus_force()
        pop.after(200, lambda: pop.winfo_exists() and pop.bind("<FocusOut>", lambda e: pop.destroy()))
        pop.bind("<Escape>", lambda e: pop.destroy())

    def _copy_row(self, label, value, extra=None):
        row = tk.Frame(self._p, bg=self._pbg)
        row.pack(fill="x", padx=self._px(), pady=3)
        tk.Label(row, text=label, bg=self._pbg, fg=self.c_dim, font=("Segoe UI", 9)).pack(anchor="w")
        line = tk.Frame(row, bg=self._pbg)
        line.pack(fill="x")
        ent = tk.Entry(line, font=("Consolas", 10), bg=self.c_bg, fg=self.c_tx, relief="flat",
                       readonlybackground=self.c_bg, highlightthickness=1,
                       highlightbackground=self.c_bd)
        ent.insert(0, value)
        ent.configure(state="readonly")
        ent.pack(side="left", fill="x", expand=True, ipady=4)

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
            btn.configure(text=V("copied"))
            self.root.after(1200, lambda: btn.winfo_exists() and btn.configure(text=V("copy")))
        btn = self.app._accent_btn(line, V("copy"), copy)
        btn.pack(side="left", padx=(8, 0))
        if extra:
            lk = tk.Label(line, text=extra[0], bg=self._pbg, fg=self.c_ac, font=("Segoe UI", 9),
                          cursor="hand2")
            lk.pack(side="left", padx=(10, 0))
            lk.bind("<Button-1>", lambda e: extra[1]())

    def _port_changed(self):
        try:
            port = int(str(self.get("share_port")).strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            port = DEFAULTS["share_port"]
        self.set("share_port", port)
        self._rebuild()

    def _btn_row(self):
        row = tk.Frame(self._p, bg=self._pbg)
        row.pack(fill="x", padx=self._px(), pady=(6, 2))
        return row

    def _textbox(self, label, key, default):
        tk.Label(self._body, text=label, bg=self.c_sf, fg=self.c_dim, font=("Segoe UI", 9)).pack(
            anchor="w", padx=self.PAD, pady=(8, 2))
        tb = tk.Text(self._body, height=5, wrap="word", font=("Segoe UI", 9), bg=self.c_bg, fg=self.c_tx,
                     insertbackground=self.c_tx, relief="flat", highlightthickness=1,
                     highlightbackground=self.c_bd, highlightcolor=self.c_ac, padx=6, pady=4)
        tb.pack(fill="x", padx=self.PAD)
        tb.insert("1.0", self.get(key) or default)

        def save(_e=None):
            v = tb.get("1.0", "end").strip()
            self.set(key, "" if v == default.strip() else v)
        tb.bind("<KeyRelease>", save)
        tb.bind("<FocusOut>", save)
        row = self._btn_row()
        lk = tk.Label(row, text=V("reset"), bg=self.c_sf, fg=self.c_ac, font=("Segoe UI", 9), cursor="hand2")
        lk.pack(side="right")
        lk.bind("<Button-1>", lambda e: (self.set(key, ""), tb.delete("1.0", "end"), tb.insert("1.0", default)))

    def _test_row(self):
        row = self._btn_row()
        res = tk.Label(row, text="", bg=self._pbg, fg=self.c_dim, font=("Segoe UI", 9), justify="left",
                       wraplength=360, anchor="w")
        self.app._accent_btn(row, V("test"), lambda: self._test_llm(res)).pack(side="left", anchor="n")
        res.pack(side="left", padx=10, fill="x")

    # ---- содержимое ----
    def _build(self):
        b, a = self._body, self.app
        self._p, self._pbg = b, self.c_sf
        self._note(V("intro"), pady=(16, 6))
        self._check(V("enable"), "enabled")

        # горячая клавиша
        self._h(V("sec_hotkey"))
        row = self._btn_row()
        tk.Label(row, text=vs.hotkey_label(self.get("hk_mods"), self.get("hk_vk")), bg=self.c_sf2,
                 fg=self.c_tx, font=("Consolas", 11), padx=12, pady=5).pack(side="left")
        a._ghost_btn(row, V("change"), self._capture_hotkey).pack(side="left", padx=10)
        if self.get("hk_mods") == 0 and self.get("hk_vk") in vs.SIDED_MODIFIERS:
            self._note(V("hk_single_note"), pady=(4, 2))
        self._radio([("hold", V("mode_hold")), ("toggle", V("mode_toggle"))], "mode")

        # микрофон
        self._h(V("sec_mic"))
        mics = [("", V("mic_default"))] + [(n, n) for n in vs.list_mics()]
        self._dropdown(V("sec_mic"), mics, "mic")

        # распознавание
        self._h(V("sec_stt"))
        langs = [(c, V("lang_auto") if c == "auto" else c) for c in ve.LANGS]

        def stt_local():
            models = [("base", V("m_base")), ("small", V("m_small")),
                      ("large-v3-turbo-q5_0", V("m_turbo"))]
            self._dropdown(V("model"), models, "w_model")
            self._dropdown(V("lang"), langs, "lang")
            m = self.get("w_model")
            row = self._btn_row()
            if self.dl and self.dl.get("model") == m and not self.dl.get("finished"):
                self._dl_label = tk.Label(row, text=V("downloading", 0), bg=self._pbg, fg=self.c_ac,
                                          font=("Segoe UI", 9))
                self._dl_label.pack(side="left")
            elif self.whisper.is_installed(m):
                tk.Label(row, text="✓ " + V("installed"), bg=self._pbg, fg="#34d399",
                         font=("Segoe UI", 9)).pack(side="left")
                lk = tk.Label(row, text=V("delete_model"), bg=self._pbg, fg=self.c_dim,
                              font=("Segoe UI", 9), cursor="hand2")
                lk.pack(side="right")
                lk.bind("<Button-1>", lambda e: (self.whisper.remove_model(m), self._rebuild()))
            else:
                tk.Label(row, text=V("not_installed", ve.WHISPER_MODELS[m]), bg=self._pbg, fg=self.c_dim,
                         font=("Segoe UI", 9)).pack(side="left")
                a._accent_btn(row, V("download"), lambda: self._download(m)).pack(side="right")
                if self.dl and self.dl.get("err") and self.dl.get("model") == m:
                    self._note(V("test_fail", self.dl["err"]), fg=self.c_err)
            self._entry(V("vocab"), "stt_prompt")

        def stt_cloud():
            presets = [(p[0], p[1]) for p in ve.STT_PRESETS]

            def on_stt_preset(v):
                p = next(x for x in ve.STT_PRESETS if x[0] == v)
                if p[2]:
                    self.set("stt_url", p[2])
                    self.set("stt_model", p[3])
            self._dropdown(V("preset"), presets, "stt_preset", on_pick=on_stt_preset)
            self._entry(V("url"), "stt_url")
            self._entry(V("key"), "stt_key", secret=True)
            self._entry(V("model"), "stt_model")
            self._dropdown(V("lang"), langs, "lang")
            self._entry(V("vocab"), "stt_prompt")

        self._radio([("local", V("stt_local")), ("cloud", V("stt_cloud"))], "stt",
                    {"local": (V("pan_local"), stt_local), "cloud": (V("pan_cloud"), stt_cloud)})

        # LLM
        self._h(V("sec_llm"))
        def llm_cc():
            exe = ve.find_claude(self.get("cc_path"))
            if exe:
                self._note("✓ " + V("cc_found", exe), fg="#34d399")
            else:
                self._note(V("cc_missing"), fg=self.c_err)
            self._entry(V("cc_path"), "cc_path")
            self._entry(V("model_empty_default"), "cc_model")
            self._test_row()

        def llm_remote():
            self._note(V("remote_hint"), pady=(0, 6))
            self._entry(V("remote_url"), "rm_url")
            self._entry(V("share_key"), "rm_key", secret=True)
            self._test_row()

        def llm_api():
            self._entry(V("key"), "an_key", secret=True)
            self._dropdown(V("model"), [(m, m) for m in ve.ANTHROPIC_MODELS], "an_model")
            self._test_row()

        def llm_oai():
            presets = [(p[0], p[1]) for p in ve.LLM_PRESETS]

            def on_llm_preset(v):
                p = next(x for x in ve.LLM_PRESETS if x[0] == v)
                if p[2]:
                    self.set("oa_url", p[2])
                elif self.get("oa_url") in [x[2] for x in ve.LLM_PRESETS if x[2]]:
                    self.set("oa_url", "")        # адрес другого сервиса тут не подходит
                if p[2] or p[3]:
                    self.set("oa_model", p[3])
            self._dropdown(V("preset"), presets, "oa_preset", on_pick=on_llm_preset)
            self._entry(V("url"), "oa_url")
            self._entry(V("key"), "oa_key", secret=True)
            self._entry(V("model"), "oa_model")
            self._test_row()

        self._radio([("claude_code", V("llm_cc")), ("remote", V("llm_remote")),
                     ("anthropic", V("llm_api")), ("openai", V("llm_oai"))], "llm",
                    {"claude_code": (V("pan_cc"), llm_cc), "remote": (V("pan_remote"), llm_remote),
                     "anthropic": (V("llm_api"), llm_api), "openai": (V("pan_oai"), llm_oai)})

        # сервер для других ПК — только там, где есть что раздавать
        if ve.find_claude(self.get("cc_path")) or self.get("share_enabled"):
            self._h(V("sec_share"))
            self._note(V("share_desc"), pady=(0, 6))

            def toggle_share(v):
                self._rebuild()
            self._check(V("share_enable"), "share_enabled", toggle_share)
            if self.get("share_enabled"):
                with self._panel(V("share_panel")):
                    port = self.get("share_port")
                    running = self.share and self.share.httpd and self.share.port == int(port)
                    if running and self.share.token == self.get("share_token"):
                        self._note("● " + V("share_on", int(port)), fg="#34d399")
                    elif ve.find_claude(self.get("cc_path")) and not (self.share and self.share.error):
                        self._note(V("share_pending"), fg=self.c_ac)
                    elif self.share and self.share.error:
                        self._note(V("share_err", int(port), self.share.error), fg=self.c_err)
                    elif not ve.find_claude(self.get("cc_path")):
                        self._note(V("share_none"), fg=self.c_err)
                    for ip in ve.local_addresses()[:3]:
                        self._copy_row(V("share_addr"), "http://%s:%s/v1" % (ip, port))
                    self._copy_row(V("share_key"), self.get("share_token"),
                                   extra=(V("share_newkey"), lambda: (self.set("share_token", ve.new_token()),
                                                                      self._rebuild())))
                    pe = self._entry(V("share_port"), "share_port")
                    pe.bind("<FocusOut>", lambda e: self._port_changed())
                    pe.bind("<Return>", lambda e: self._port_changed())

        # поведение
        self._h(V("sec_behavior"))
        self._check(V("cleanup"), "cleanup")
        self._note(V("cleanup_note"), pady=(0, 6), indent=30)
        self._check(V("autopaste"), "autopaste")
        self._check(V("restore"), "restore_clip")

        # инструкции
        self._h(V("sec_prompts"))
        self._textbox(V("p_dict"), "p_dict", ve.DEFAULT_DICTATION)
        self._textbox(V("p_edit"), "p_edit", ve.DEFAULT_EDIT)
        tk.Frame(b, bg=self.c_sf, height=16).pack()

    # ---- действия окна ----
    def _capture_hotkey(self):
        dlg = self.app._dialog(V("sec_hotkey"), 420, 170)
        tk.Label(dlg, text=V("press_combo"), bg=self.c_sf, fg=self.c_tx, font=("Segoe UI", 11),
                 justify="center").pack(expand=True)
        self._capture_dlg = dlg
        self.hook.capture = True

        def closed():
            self.hook.capture = False
            self._capture_dlg = None
            try:
                dlg.destroy()
            except Exception:
                pass
        dlg.protocol("WM_DELETE_WINDOW", closed)
        dlg.focus_force()

    def _capture_done(self, ev):
        if ev[0] == "captured":
            self.set("hk_mods", ev[1])
            self.set("hk_vk", ev[2])
        dlg, self._capture_dlg = self._capture_dlg, None
        if dlg:
            try:
                dlg.destroy()
            except Exception:
                pass
        if self.win:
            self._rebuild()

    def _download(self, model):
        if self.whisper.installing:
            return
        self.dl = {"model": model, "done": 0, "total": 1, "err": None, "finished": False}

        def prog(done, total):
            self.dl["done"], self.dl["total"] = done, max(total, 1)

        def run():
            try:
                self.whisper.install(model, prog)
            except Exception as ex:
                self.dl["err"] = str(ex)[:160]
                self.log("whisper install failed: %r" % ex)
            self.dl["finished"] = True
        threading.Thread(target=run, daemon=True).start()
        self._rebuild()

    def _dl_poll(self):
        try:
            if not self.win or not self.win.winfo_exists():
                return
        except Exception:
            return
        try:
            if self.dl and not self.dl.get("shown_done"):
                if self.dl.get("finished"):
                    self.dl["shown_done"] = True
                    self._rebuild()
                else:
                    lb = getattr(self, "_dl_label", None)
                    if lb and lb.winfo_exists():
                        lb.configure(text=V("downloading", int(100 * self.dl["done"] / self.dl["total"])))
        except Exception:
            pass
        self.win.after(250, self._dl_poll)

    def _test_llm(self, label):
        label.configure(text=V("testing"), fg=self.c_dim)
        self._test_running = True

        def done_cb(text):
            self._test_running = False
            try:
                ok = not text.startswith("!")
                label.configure(text=text.lstrip("!"), fg="#34d399" if ok else self.c_err)
            except Exception:
                pass

        def run():
            t0 = time.time()
            try:
                out, _ = ve.split_answer(self._llm(ve.user_message(V("test_phrase"))))
                msg = "%s\n«%s» → «%s»" % (V("test_ok", time.time() - t0), V("test_phrase"), out[:160])
            except Exception as ex:
                msg = "!" + V("test_fail", str(ex)[:200])
            self._post("test", cb=done_cb, text=msg)
        threading.Thread(target=run, daemon=True).start()
        self._ensure_pump()
